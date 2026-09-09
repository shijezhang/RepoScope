import time
from unittest.mock import patch

from reposcope.config import Settings
from reposcope.coverage import binding, import_coverage
from reposcope.execution.runner import TestRunner
from reposcope.graph.store import Store
from reposcope.jobs.worker import Worker, compare_results
from reposcope.models import Snapshot, Symbol


def test_compare_failures_are_not_claimed_confirmed():
    meta = {"comparison_hash": "test-content", "environment_hash": "image"}
    base = {
        **meta,
        "results": [{"nodeid": "regression", "status": "passed"}, {"nodeid": "existing", "status": "failed"}],
    }
    head = {**meta, "results": [{"nodeid": node, "status": "failed"} for node in ["regression", "existing", "new"]]}
    comparison = compare_results(base, head, {"regression", "existing"})
    assert [r["finding"] for r in comparison["tests"]] == [
        "suspected_regression",
        "existing_failure",
        "new_test_failure",
    ]
    assert comparison["status"] == "suspected_regression"
    assert not compare_results(base, {**head, "comparison_hash": "changed-fixture"}, {"regression", "existing"})[
        "comparable"
    ]
    assert compare_results({}, head, None)["tests"][-1]["finding"] == "head_validation_failure"


def test_expired_test_reconciles_known_executions_without_resubmitting(tmp_path):
    store = Store(Settings(home=tmp_path))
    jid = store.enqueue("test", {"run_id": "a", "plan_id": "p"})
    assert store.claim("dead-worker") == jid
    with store.connect() as db:
        db.execute("UPDATE jobs SET lease=? WHERE id=?", (time.time() - 5, jid))
    with (
        patch.object(TestRunner, "recover", return_value={"status": "interrupted"}) as recover,
        patch.object(TestRunner, "run") as run,
    ):
        assert Worker(store).once() is False
        assert [call.args[0] for call in recover.call_args_list] == [
            jid + suffix for suffix in ["-collect-head", "-collect-base", "-head", "-base"]
        ]
        run.assert_not_called()
        assert store.job(jid)["state"] == "interrupted"
        Worker(store).once()
        assert recover.call_count == 4
    store.update(jid, "completed", owner="dead-worker")
    assert store.job(jid)["state"] == "interrupted"


def test_cancelled_expired_analysis_is_not_left_queued(tmp_path):
    store = Store(Settings(home=tmp_path))
    jid = store.enqueue("analysis", {})
    store.claim("dead")
    store.cancel(jid)
    with store.connect() as db:
        db.execute("UPDATE jobs SET lease=? WHERE id=?", (time.time() - 1, jid))
    assert store.claim("new") is None
    assert store.job(jid)["state"] == "cancelled"


def test_selection_uses_only_exact_base_coverage_and_collected_tests(tmp_path):
    store = Store(Settings(home=tmp_path))
    symbol = Symbol(
        symbol_id="base-f",
        snapshot_id="base",
        path="a.py",
        module="a",
        qualname="f",
        kind="Function",
        start=1,
        end=3,
        content_hash="content",
    )
    base = Snapshot(
        snapshot_id="base",
        repo_id="repo",
        commit_sha="base",
        tree_hash="tree",
        manifest_hash="manifest",
        files={},
        symbols=[symbol],
        relations=[],
        unresolved=[],
        diagnostics=[],
    )
    head = base.model_copy(update={"snapshot_id": "head"})
    report = {
        "test_plan": {"reasons": ["Coverage unavailable: initial"]},
        "impacts": [{"symbol": symbol.model_dump(), "side": "base", "distance": 0, "change_type": "modified"}],
        "changes": [],
        "limitations": [],
        "symbol_mappings": [],
    }
    collection = {
        "status": "completed",
        "nodeids": ["test_a", "test_b"],
        "environment_hash": "env",
        "test_suite_hash": "suite",
    }
    expected = binding(base, "env", "suite")
    import_coverage(
        store,
        {"binding": expected, "contexts": [{"path": "a.py", "phase": "call", "nodeid": "test_a", "lines": [2]}]},
        expected,
        "baseline",
    )
    selected = Worker(store).select_plan(report, base, head, collection, collection)
    assert selected["nodeids"] == ["test_a"]
    assert selected["strategy"] == "weighted-coverage"
    assert selected["coverage_source_snapshot"] == "base"
    stale = {**collection, "test_suite_hash": "stale"}
    assert Worker(store).select_plan(report, base, head, collection, stale)["nodeids"] == ["test_a", "test_b"]
    empty = {**collection, "nodeids": ["test_b"]}
    assert Worker(store).select_plan(report, base, head, empty, collection)["nodeids"] == ["test_b"]


def test_worker_budget_exhaustion_is_failed_and_user_cancel_is_cancelled(tmp_path):
    import json

    for user_cancel in (False, True):
        store = Store(Settings(home=tmp_path / str(user_cancel), job_seconds=-1 if not user_cancel else 180))
        jid = store.enqueue("test", {})

        def fake_tests(*args):
            if user_cancel:
                store.cancel(jid)
            return {"status": "partial"}

        with patch.object(Worker, "tests", side_effect=fake_tests):
            assert Worker(store).once()
        job = store.job(jid)
        assert job["state"] == ("cancelled" if user_cancel else "failed")
        assert json.loads(job["error"])["code"] == ("cancelled" if user_cancel else "budget_exceeded")
        assert job["report"]["completeness"] == "partial"


def test_recovery_unconfirmed_requires_attention_without_busy_retry(tmp_path):
    store = Store(Settings(home=tmp_path))
    with store.connect() as db:
        db.execute("INSERT INTO recovery VALUES(?,?,?)", ("job", "pending", "{}"))
    with patch.object(
        TestRunner, "recover", return_value={"status": "interrupted", "cleanup_status": "unconfirmed"}
    ) as recover:
        worker = Worker(store)
        worker.recover_interrupted()
        worker.recover_interrupted()
        assert recover.call_count == 4
    with store.connect() as db:
        assert db.execute("SELECT state FROM recovery").fetchone()[0] == "needs_attention"


def test_completed_execution_replaces_obsolete_limitations(tmp_path):
    store = Store(Settings(home=tmp_path))
    snap = Snapshot(
        snapshot_id="snap",
        repo_id="repo",
        commit_sha="sha",
        tree_hash="tree",
        manifest_hash="manifest",
        files={},
        symbols=[],
        relations=[],
        unresolved=[],
        diagnostics=[],
    )
    store.put("snapshots", "snap", snap)
    store.put("repositories", "repo", {"repo_id": "repo", "path": "/unused"})
    analysis = store.enqueue("analysis", {})
    report = {
        "repo_id": "repo",
        "revision": 1,
        "base": {"snapshot_id": "snap"},
        "head": {"snapshot_id": "snap"},
        "test_plan": {"plan_id": "plan"},
        "executions": [],
        "impacts": [],
        "changes": [],
        "limitations": ["Tests have not been collected or executed for this plan", "Dynamic behavior remains unknown"],
    }
    store.update(analysis, "completed", result=report)
    jid = store.enqueue("test", {"run_id": analysis, "plan_id": "plan"})
    collection = {"status": "completed", "nodeids": ["test_a"], "environment_hash": "env", "test_suite_hash": "suite"}
    outcome = {"status": "completed", "results": [{"nodeid": "test_a", "status": "passed"}]}
    with (
        patch.object(TestRunner, "collect", return_value=collection),
        patch.object(TestRunner, "run", return_value=outcome),
        patch("reposcope.jobs.worker.validate_report"),
    ):
        Worker(store).once()
    updated = store.job(analysis)["report"]
    assert "Tests have not been collected or executed for this plan" not in updated["limitations"]
    assert "Tests collected; selected tests have not completed execution" not in updated["limitations"]
    assert "Dynamic behavior remains unknown" in updated["limitations"]
    assert store.job(jid)["state"] == "completed"
