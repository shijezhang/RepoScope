"""Real Docker base/head and repeat observations on two fixed public-project pools."""

import argparse
import json
import subprocess
import time
from collections import Counter
from pathlib import Path

from reposcope.config import Settings
from reposcope.coverage import binding, validity
from reposcope.execution.runner import TestRunner
from reposcope.graph.store import Store
from reposcope.indexing.parser import build_snapshot
from reposcope.jobs.worker import compare_results

try:
    from .case_locator import locate_case
except ImportError:  # Direct script invocation.
    from case_locator import locate_case

ROOT = Path(__file__).resolve().parents[2]
CASES = {"click": "click-01", "httpx": "httpx-02"}


def command(*args):
    return subprocess.check_output(list(args), text=True).strip()


def validate(name):
    case_id = CASES[name]
    replay = locate_case(case_id)
    source = ROOT / replay["replay_repository"]
    probe = next(
        row
        for row in json.loads((ROOT / "benchmarks/results/regression-probes.json").read_text())
        if row["case_id"] == case_id
    )
    image_manifest = json.loads((ROOT / f"benchmarks/manifests/{name}-docker-image.json").read_text())
    image = image_manifest["image_id"]
    profile = {
        "image": image,
        "python": "python",
        "timeout": 120,
        "memory": "768m",
        "cpus": 1,
        "pids_limit": 128,
        "max_checkout_file_bytes": 4_000_000 if name == "httpx" else 1_000_000,
        "test_paths": probe["test_files"],
        "pytest_plugins": ["anyio.pytest_plugin"] if name == "httpx" else [],
    }
    state = ROOT / "artifacts/public-docker-validation-state"
    for home in [state, ROOT / "artifacts/state"]:
        (home / "profiles").mkdir(parents=True, exist_ok=True)
        (home / f"profiles/{name}.json").write_text(json.dumps(profile, indent=2) + "\n")
    store = Store(Settings(home=state))
    runner = TestRunner(store)
    repo = {"repo_id": name, "path": str(source), "profile_id": name}
    before = command("git", "-C", str(source), "status", "--porcelain")
    shas = {side: replay[side] for side in ["base", "head"]}
    assert shas["base"] == probe["base_commit"]
    snapshots = {side: build_snapshot(store, repo, sha) for side, sha in shas.items()}
    prefix = f"{case_id}-{time.time_ns()}"
    collections = {side: runner.collect(snap, repo, prefix + "-collect-" + side) for side, snap in snapshots.items()}
    for collection in collections.values():
        assert collection["status"] == "completed", collection
        assert collection["cleanup_status"] == "completed", collection
    pool = sorted(collections["base"]["nodeids"])
    assert pool == sorted(collections["head"]["nodeids"]), "Tests changed across comparison"
    assert len(pool) == {"click": 39, "httpx": 106}[name], collections
    attempts = []
    expected_failures = {"click": 4, "httpx": 1}[name]
    for attempt in [1, 2]:
        executions = {}
        for side, snap in snapshots.items():
            print(f"{case_id} attempt {attempt}: running {side}, {len(pool)} collected tests", flush=True)
            result = runner.run(snap, repo, pool, f"{prefix}-{side}-{attempt}")
            assert result["status"] == "completed" and result["cleanup_status"] == "completed", result
            assert result["coverage_ids"], result
            records = [store.get("coverage", eid) for eid in result["coverage_ids"]]
            expected = binding(snap, result["environment_hash"], result["test_suite_hash"])
            assert all(validity(record, expected)["status"] == "valid" for record in records)
            source_prefix = "src/click/" if name == "click" else "httpx/"
            assert any(record["path"].startswith(source_prefix) and record["phase"] == "call" for record in records), (
                "Tests did not cover frozen source tree"
            )
            result["pytest_phase_seconds"] = sum(row["duration"] for row in result["results"])
            result["counts"] = dict(Counter(row["status"] for row in result["results"]))
            executions[side] = result
        assert executions["base"]["counts"].get("failed", 0) == 0
        assert executions["base"]["counts"].get("error", 0) == 0
        comparison = compare_results(executions["base"], executions["head"], set(pool))
        assert sum(row["finding"] == "suspected_regression" for row in comparison["tests"]) == expected_failures, (
            comparison
        )
        attempts.append(
            {
                "attempt": attempt,
                "reason": "initial execution" if attempt == 1 else "same-nodeid stability observation",
                "executions": executions,
                "comparison": comparison,
            }
        )
    for side in ["base", "head"]:
        first, second = [
            {row["nodeid"]: row["status"] for row in attempt["executions"][side]["results"]} for attempt in attempts
        ]
        assert first == second, "Observed result changed on repeat"
    assert before == command("git", "-C", str(source), "status", "--porcelain")
    known = {result["container"] for result in collections.values()}
    known.update(result["container"] for attempt in attempts for result in attempt["executions"].values())
    leftovers = sorted(
        set(command("docker", "ps", "-a", "--filter", "name=reposcope-", "--format", "{{.Names}}").splitlines()) & known
    )
    assert not leftovers, leftovers
    assert not list((state / "execution-tmp").iterdir())
    environment = command(
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--read-only",
        "--entrypoint",
        "python",
        image,
        "-c",
        "import sys,importlib.metadata as m; print(sys.version); print('\\n'.join(sorted(d.metadata['Name']+'=='+d.version for d in m.distributions())))",
    )
    (ROOT / f"benchmarks/manifests/{name}-docker-environment.txt").write_text(environment + "\n")
    return {
        "case_id": case_id,
        "repository": name,
        "source": str(source.relative_to(ROOT)),
        "replay_manifest": replay["replay_manifest"],
        "commits": shas,
        "parser_versions": {side: snap.parser_version for side, snap in snapshots.items()},
        "kind": "real-product-docker-execution",
        "environment_preparation": {
            "build_wall_seconds": image_manifest.get("build_wall_seconds"),
            "measurement_note": "Initial image build started before wall-clock instrumentation; missing timing is not reconstructed"
            if image_manifest.get("build_wall_seconds") is None
            else "Measured separately from collection and test execution",
        },
        "profile": profile,
        "pool": pool,
        "collections": collections,
        "attempts": attempts,
        "same_statuses_on_repeat": True,
        "working_tree_unchanged": True,
        "remaining_containers": [],
        "temporary_workspaces": [],
        "coverage_source_verified": True,
        "python_environment": environment,
        "limitations": [
            "Fixed existing test-file pool, not the entire upstream suite",
            "Two matching observations do not prove absence of flakiness",
            "Controlled development mutation, not a held-out quality metric",
            "No public-project network-dependent tests executed",
        ],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", choices=list(CASES))
    parser.add_argument(
        "--locate-only", action="store_true", help="Resolve replay inputs without Docker or state writes"
    )
    args = parser.parse_args()
    if args.locate_only:
        print(
            json.dumps(
                [locate_case(CASES[name]) for name in ([args.repository] if args.repository else CASES)], indent=2
            )
        )
        return
    output = ROOT / "benchmarks/results/public-docker-validation.json"
    previous = json.loads(output.read_text()) if output.exists() else {"schema_version": 1, "cases": []}
    for name in [args.repository] if args.repository else CASES:
        result = validate(name)
        previous["cases"] = [row for row in previous["cases"] if row["repository"] != name] + [result]
        previous["updated_at"] = time.time()
        output.write_text(json.dumps(previous, indent=2) + "\n")
        print(
            json.dumps(
                {
                    "repository": name,
                    "counts": {side: result["attempts"][0]["executions"][side]["counts"] for side in ["base", "head"]},
                    "repeat_matches": True,
                    "output": str(output),
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
