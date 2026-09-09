"""Actual fixed-flow worker validation with explicit test authorization."""

import json
import shutil
import time
from pathlib import Path

from reposcope.config import Settings
from reposcope.graph.store import Store
from reposcope.jobs.worker import Worker

ROOT = Path(__file__).resolve().parents[2]


def main():
    case = next(
        row
        for row in json.loads((ROOT / "benchmarks/results/index-consistency.json").read_text())["cases"]
        if row["case_id"] == "fixture-01"
    )
    store = Store(Settings(home=ROOT / "artifacts/workflow-validation-state"))
    (store.settings.home / "profiles").mkdir(exist_ok=True)
    shutil.copyfile(ROOT / "artifacts/state/profiles/fixture.json", store.settings.home / "profiles/fixture.json")
    repo = {"repo_id": "workflow-fixture", "path": str(ROOT / case["replay_repository"]), "profile_id": "fixture"}
    store.put("repositories", repo["repo_id"], repo)
    jid = store.enqueue(
        "analysis",
        {"repo_id": repo["repo_id"], "base": case["base"], "head": case["head"], "allow_tests": True, "agent": False},
    )
    started = time.perf_counter()
    Worker(store).once()
    job = store.job(jid)
    assert job["state"] == "completed", job["error"]
    report = job["report"]
    comparison = next(row for row in reversed(report["executions"]) if row["kind"] == "comparison")
    assert comparison["comparison"]["status"] == "suspected_regression"
    assert report["revision"] == 2
    assert sum(row["head_status"] == "failed" for row in comparison["comparison"]["tests"]) == 3
    result = {
        "kind": "actual Docker fixed worker flow",
        "run_id": jid,
        "parser_version": report["metadata"]["parser"],
        "status": job["state"],
        "completeness": report["completeness"],
        "revision": report["revision"],
        "model": None,
        "explicit_allow_tests": True,
        "wall_seconds": time.perf_counter() - started,
        "comparison": comparison["comparison"],
        "events": store.events(jid),
    }
    (ROOT / "benchmarks/results/workflow-validation.json").write_text(json.dumps(result, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k not in {"events", "comparison"}}))


if __name__ == "__main__":
    main()
