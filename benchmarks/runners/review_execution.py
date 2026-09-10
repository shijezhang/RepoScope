"""Run frozen review cases in server-owned Docker profiles; preserve failed collection."""

import json
import time
import uuid
from collections import Counter
from pathlib import Path

from reposcope.config import Settings
from reposcope.execution.runner import TestRunner
from reposcope.graph.store import Store
from reposcope.indexing.parser import build_snapshot
from reposcope.jobs.worker import compare_results

ROOT = Path(__file__).resolve().parents[2]


def main():
    run_id = "run-" + uuid.uuid4().hex
    state = ROOT / "artifacts/review-execution" / run_id
    (state / "profiles").mkdir(parents=True)
    profiles = {}
    for name in ("fixture", "click", "httpx"):
        profile = json.loads((ROOT / f"artifacts/state/profiles/{name}.json").read_text())
        if name == "click":
            profile["test_paths"] = ["tests/test_types.py", "tests/test_options.py", "tests/test_arguments.py"]
        profiles[name] = profile
        (state / f"profiles/{name}.json").write_text(json.dumps(profile, indent=2) + "\n")
    store = Store(Settings(home=state))
    runner = TestRunner(store)
    cases = json.loads((ROOT / "benchmarks/review/2026-09-10/annotations.json").read_text())
    result = {
        "run_id": run_id,
        "state": "running",
        "profiles": profiles,
        "scope": "one base/head observation per case; not independently human-reviewed gold",
        "cases": [],
    }
    output = ROOT / "benchmarks/results" / ("review-execution-" + run_id + ".json")

    def save():
        output.write_text(json.dumps(result, indent=2) + "\n")

    save()
    for case in cases:
        print(case["case_id"] + ": frozen Docker collection and execution", flush=True)
        repo = {
            "repo_id": case["repo_id"],
            "profile_id": case["repo_id"],
            "path": str(ROOT / case["replay_repository"]),
        }
        row = {
            "case_id": case["case_id"],
            "base_sha": case["base_sha"],
            "head_sha": case["head_sha"],
            "collections": {},
            "executions": {},
        }
        result["cases"].append(row)
        start = time.perf_counter()
        for side in ("base", "head"):
            snapshot = build_snapshot(store, repo, case[side + "_sha"])
            collection = runner.collect(snapshot, repo, case["case_id"] + "-" + side + "-collect")
            row["collections"][side] = collection
            if collection["status"] == "completed":
                execution = runner.run(snapshot, repo, collection["nodeids"], case["case_id"] + "-" + side)
                execution["counts"] = dict(Counter(test["status"] for test in execution.get("results", [])))
                row["executions"][side] = execution
            save()
        if len(row["executions"]) == 2:
            pool = set(row["collections"]["base"]["nodeids"]) & set(row["collections"]["head"]["nodeids"])
            row["comparison"] = compare_results(row["executions"]["base"], row["executions"]["head"], pool)
        row["wall_seconds"] = time.perf_counter() - start
        save()
    result["state"] = "completed"
    result["all_containers_cleaned"] = all(
        entry.get("cleanup_status") == "completed"
        for row in result["cases"]
        for stage in ("collections", "executions")
        for entry in row[stage].values()
    )
    save()
    print(str(output))


if __name__ == "__main__":
    main()
