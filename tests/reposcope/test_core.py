import json
import time

import pytest
from fastapi.testclient import TestClient

from reposcope.agent.controller import Controller
from reposcope.analysis.impact import analyze
from reposcope.analysis.selection import select_tests
from reposcope.api.app import create_app
from reposcope.config import RepoScopeError
from reposcope.indexing.parser import build_snapshot, semantic_hash
from reposcope.jobs.worker import Worker
from reposcope.llm.provider import Decision
from reposcope.reports.render import validate_report
from reposcope.repository.git import comparison, resolve

BASE = {
    "pkg/__init__.py": "",
    "pkg/money.py": "def total(amount):\n    return amount if amount >= 0 else 0\n",
    "pkg/orders.py": "from .money import total as calculate\n\ndef checkout(amount):\n    return calculate(amount)\n",
    "tests/test_orders.py": "from pkg.orders import checkout\n\ndef test_zero():\n    assert checkout(0) == 0\n",
}


def test_cross_module_immutable_evidence(store, git_repo):
    repo, commit, _ = git_repo
    b = commit(BASE)
    h = commit({"pkg/money.py": "def total(amount):\n    return amount if amount > 0 else -1\n"})
    base, head = [build_snapshot(store, repo, sha) for sha in (b, h)]
    report = analyze(store, repo, base, head, "run")
    assert {i["symbol"]["qualname"] for i in report["impacts"]} >= {"total", "checkout", "test_zero"}
    assert report["completeness"] == "partial"
    assert validate_report(store, report)
    assert all(c["status"] == "inferred" for c in report["claims"])
    e = store.get("evidence", report["impacts"][0]["evidence_id"])
    e["source"] = "tampered"
    store.put("evidence", report["impacts"][0]["evidence_id"], e)
    with pytest.raises(RepoScopeError, match="hash mismatch"):
        validate_report(store, report)


@pytest.mark.parametrize(
    "change",
    [
        {"pkg/money.py": None},
        {"pkg/money.py": None, "pkg/cash.py": BASE["pkg/money.py"]},
        {"pkg/money.py": "def renamed(amount):\n    return amount\n"},
        {"pkg/new.py": "from .orders import checkout\ndef wrapper(x):\n    return checkout(x)\n"},
        {"pkg/money.py": "from .orders import checkout\ndef total(x):\n    return checkout(x)\n"},
    ],
)
def test_incremental_equivalence_and_no_dangling(store, git_repo, change):
    repo, commit, _ = git_repo
    b = commit(BASE)
    base = build_snapshot(store, repo, b)
    h = commit(change)
    inc = build_snapshot(store, repo, h)
    full = build_snapshot(store, repo, h, incremental=False)
    assert semantic_hash(inc) == semantic_hash(full)
    ids = {s.symbol_id for s in inc.symbols}
    assert all(e.source_id in ids and e.target_id in ids for e in inc.relations)
    report = analyze(store, repo, base, inc, "change")
    assert validate_report(store, report)
    assert (
        any(i["side"] == "base" and i["symbol"]["qualname"] == "total" for i in report["impacts"])
        or "pkg/new.py" in change
    )


def test_dynamic_shadow_and_duplicate_not_resolved(store, git_repo):
    repo, commit, _ = git_repo
    sha = commit(
        {
            "a.py": "def target():\n    return 1\ndef caller(target):\n    return target()\ndef dynamic(obj):\n    return getattr(obj, 'target')()\nclass A:\n    def run(self):\n        return self.work()\n    def work(self):\n        pass\n"
        }
    )
    snap = build_snapshot(store, repo, sha)
    ids = {s.symbol_id: s.qualname for s in snap.symbols}
    assert not any(ids[e.source_id] == "caller" and e.relation_type == "CALLS" for e in snap.relations)
    assert any(u["reason"] == "local binding shadows symbol" for u in snap.unresolved)
    assert any(e.resolution == "candidate" for e in snap.relations)


def test_parse_failure_visible(store, git_repo):
    repo, commit, _ = git_repo
    snap = build_snapshot(store, repo, commit({"bad.py": "def broken(:\n"}))
    assert snap.status == "partial"
    assert snap.diagnostics[0]["code"] == "unsupported_syntax"


def test_git_revision_and_truncation(store, git_repo):
    repo, commit, _ = git_repo
    b = commit(BASE)
    h = commit({"pkg/money.py": "def total(amount):\n    return 42\n"})
    from pathlib import Path

    with pytest.raises(RepoScopeError):
        resolve(Path(repo["path"]), "--help")
    assert comparison(Path(repo["path"]), b, h, "direct") == (b, h)
    store.settings.max_nodes = 1
    report = analyze(store, repo, build_snapshot(store, repo, b), build_snapshot(store, repo, h), "budget")
    assert any("truncated" in s for s in report["limitations"])


def test_api_worker_idempotency_exports_and_cancel(store, git_repo):
    repo, commit, _ = git_repo
    b = commit(BASE)
    h = commit({"pkg/money.py": "def total(amount):\n    return 10\n"})
    client = TestClient(create_app(store.settings))
    rid = client.post("/api/repositories", json={"path": repo["path"]}).json()["repo_id"]
    payload = {"repo_id": rid, "base": b, "head": h}
    created = client.post("/api/analyses", json=payload, headers={"Idempotency-Key": "a"}).json()
    assert client.post("/api/analyses", json=payload, headers={"Idempotency-Key": "a"}).json() == created
    assert (
        client.post("/api/analyses", json={**payload, "head": b}, headers={"Idempotency-Key": "a"}).status_code == 409
    )
    jid = created["run_id"]
    assert Worker(store).once()
    report = client.get(f"/api/analyses/{jid}").json()["report"]
    assert report["head"]["commit_sha"] == h
    assert json.loads(client.get(f"/api/analyses/{jid}/export").text) == report
    assert client.get(f"/api/analyses/{jid}/export?format=html").status_code == 200
    assert "data:" in client.get(f"/api/analyses/{jid}/events").text
    events = store.events(jid)
    assert store.events(jid, events[-1]["event_id"]) == []
    assert client.post(f"/api/analyses/{jid}/test-runs", json={"plan_id": "wrong"}).status_code == 409
    task = client.post(f"/api/analyses/{jid}/test-runs", json={"plan_id": report["test_plan"]["plan_id"]}).json()
    Worker(store).once()
    assert store.job(task["execution_id"])["report"]["status"] == "test_environment_unavailable"
    assert store.job(jid)["report"]["revision"] == 2
    pending = client.post("/api/analyses", json=payload).json()["run_id"]
    client.post(f"/api/analyses/{pending}/cancel")
    assert store.job(pending)["state"] == "cancelled"
    assert (
        client.post(
            "/api/repositories", json={"path": repo["path"]}, headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )


def test_worker_lost_lease_no_test_resubmit(store):
    jid = store.enqueue("test", {"run_id": "r", "plan_id": "p"})
    assert store.claim("dead") == jid
    with store.connect() as db:
        db.execute("UPDATE jobs SET lease=? WHERE id=?", (time.time() - 1, jid))
    assert store.claim("new") is None
    assert store.job(jid)["state"] == "interrupted"


def test_agent_bounded_and_snapshot_scoped(store, git_repo):
    repo, commit, _ = git_repo
    snap = build_snapshot(store, repo, commit(BASE))
    report = analyze(store, repo, snap, snap, "agent", question="find total")

    class Fake:
        model = "fake-test-only"
        usage = {"input_tokens": 1, "output_tokens": 1, "requests": 1}

        def decide(self, context, tools):
            return Decision(
                tool="find_symbol",
                arguments={"snapshot_id": snap.snapshot_id, "query": "total"},
                summary="Locate named symbol",
            )

    controller = Controller(store, report, Fake())
    result = controller.run()
    assert len(result["tool_calls"]) == 1
    assert any("Repeated" in limitation for limitation in result["limitations"])
    with pytest.raises(RepoScopeError):
        controller.call("find_symbol", {"snapshot_id": "another", "query": "total"})


def test_selector_budget_and_fallback():
    assert select_tests(["a", "b"], {"x": 3}, {}, budget=1)["nodeids"] == ["a", "b"]
    selected = select_tests(["a", "b"], {"x": 3, "y": 1}, {"a": ["x"], "b": ["y"]}, budget=1)
    assert selected["nodeids"] == ["a"] and selected["uncovered"] == ["y"]
