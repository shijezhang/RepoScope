"""Actual Docker acceptance checks; never falls back to host pytest execution."""

import argparse
import json
import subprocess
import threading
import time
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


def docker(*args):
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=True, timeout=30).stdout.strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", help="Prepared immutable sha256 image ID or repository digest")
    parser.add_argument(
        "--locate-only", action="store_true", help="Read replay manifests and verify frozen Git objects without Docker"
    )
    args = parser.parse_args()
    case = locate_case("fixture-01")
    if args.locate_only:
        print(json.dumps(case, indent=2))
        return
    if not args.image:
        parser.error("--image is required unless --locate-only is used")
    image = json.loads(docker("image", "inspect", args.image))[0]
    info = json.loads(docker("info", "--format", "{{json .}}"))
    environment = docker(
        "run",
        "--rm",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--entrypoint",
        "python",
        image["Id"],
        "-c",
        "import sys,importlib.metadata as m; print(sys.version); print('\\n'.join(sorted(d.metadata['Name']+'=='+d.version for d in m.distributions())))",
    )
    (ROOT / "benchmarks/manifests/docker-fixture-environment.txt").write_text(environment + "\n")
    state = ROOT / "artifacts/docker-validation-state"
    store = Store(Settings(home=state))
    profile = {
        "image": image["Id"],
        "python": "python",
        "timeout": 120,
        "memory": "512m",
        "cpus": 1,
        "pids_limit": 128,
        "test_paths": ["test_calc.py"],
    }
    for home in [state, ROOT / "artifacts/state"]:
        (home / "profiles").mkdir(parents=True, exist_ok=True)
        (home / "profiles/fixture.json").write_text(json.dumps(profile, indent=2) + "\n")
    repo = {"repo_id": "docker-fixture", "path": str(ROOT / case["replay_repository"]), "profile_id": "fixture"}
    before_status = subprocess.run(
        ["git", "-C", repo["path"], "status", "--porcelain"], capture_output=True, text=True, check=True
    ).stdout
    base, head = [build_snapshot(store, repo, case[side]) for side in ["base", "head"]]
    runner = TestRunner(store)
    prefix = "acceptance-" + str(time.time_ns())
    results = {}
    for side, snap in [("base", base), ("head", head)]:
        collection = runner.collect(snap, repo, prefix + "-collect-" + side)
        assert collection["status"] == "completed", collection
        assert len(collection["nodeids"]) == 4, collection
        execution = runner.run(snap, repo, collection["nodeids"], prefix + "-" + side)
        assert execution["status"] == "completed", execution
        assert execution["cleanup_status"] == "completed", execution
        assert execution["coverage_ids"], execution
        assert runner.run(snap, repo, collection["nodeids"], prefix + "-" + side) == execution
        results[side] = {"collection": collection, "execution": execution}
    base_run, head_run = (results[side]["execution"] for side in ["base", "head"])
    comparison = compare_results(base_run, head_run, set(results["base"]["collection"]["nodeids"]))
    assert comparison["status"] == "suspected_regression", comparison
    assert all(row["status"] == "passed" for row in base_run["results"])
    evidence = store.get("coverage", base_run["coverage_ids"][0])
    expected = binding(base, base_run["environment_hash"], base_run["test_suite_hash"])
    assert validity(evidence, expected)["status"] == "valid"
    assert (
        validity(evidence, binding(head, head_run["environment_hash"], head_run["test_suite_hash"]))["status"]
        == "stale"
    )
    # A separate, committed slow fixture verifies actual live-container cancellation.
    slow = ROOT / "artifacts/docker-validation-slow"
    slow.mkdir(exist_ok=True)
    (slow / "test_slow.py").write_text("import time\ndef test_slow():\n    time.sleep(30)\n    assert True\n")

    def git(*commands):
        return subprocess.run(
            ["git", "-C", str(slow), *commands], capture_output=True, text=True, check=True
        ).stdout.strip()

    if not (slow / ".git").exists():
        git("init")
        git("add", "test_slow.py")
        git(
            "-c",
            "user.name=RepoScope Validation",
            "-c",
            "user.email=validation@example.invalid",
            "commit",
            "-m",
            "Frozen cancellation acceptance fixture",
        )
    slow_profile = {**profile, "test_paths": ["test_slow.py"]}
    (state / "profiles/slow.json").write_text(json.dumps(slow_profile))
    slow_repo = {"repo_id": "docker-slow", "path": str(slow), "profile_id": "slow"}
    slow_snap = build_snapshot(store, slow_repo, git("rev-parse", "HEAD"))
    collected = runner.collect(slow_snap, slow_repo, prefix + "-collect-slow")
    assert collected["status"] == "completed", collected
    execution_id = prefix + "-cancel"
    cancel = threading.Event()
    seen_running = []
    runtime_controls = {}

    def monitor():
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                current = runner.get(execution_id)
                running = docker("inspect", "--format", "{{.State.Running}}", current["container"])
                if running == "true":
                    inspected = json.loads(docker("inspect", current["container"]))[0]
                    config = inspected["HostConfig"]
                    runtime_controls.update(
                        {
                            key: config.get(key)
                            for key in [
                                "NetworkMode",
                                "ReadonlyRootfs",
                                "CapDrop",
                                "SecurityOpt",
                                "Memory",
                                "NanoCpus",
                                "PidsLimit",
                                "Tmpfs",
                            ]
                        }
                    )
                    runtime_controls["mount_destinations"] = [mount["Destination"] for mount in inspected["Mounts"]]
                    seen_running.append(True)
                    time.sleep(1)
                    cancel.set()
                    return
            except Exception:
                pass
            time.sleep(0.1)
        cancel.set()

    watcher = threading.Thread(target=monitor, daemon=True)
    watcher.start()
    cancellation = runner.run(slow_snap, slow_repo, collected["nodeids"], execution_id, cancel.is_set)
    watcher.join(timeout=2)
    assert seen_running and cancellation["status"] == "cancelled", cancellation
    assert runtime_controls["NetworkMode"] == "none" and runtime_controls["ReadonlyRootfs"] is True
    assert "ALL" in runtime_controls["CapDrop"]
    assert any("no-new-privileges" in opt for opt in runtime_controls["SecurityOpt"])
    assert runtime_controls["Memory"] == 512 * 1024 * 1024
    assert {"/work", "/control", "/output"}.issubset(runtime_controls["mount_destinations"])
    assert set(runtime_controls["mount_destinations"]).issubset({"/work", "/control", "/output", "/tmp"})
    assert "/tmp" in runtime_controls["Tmpfs"]
    assert cancellation["cleanup_status"] == "completed", cancellation
    # Profile-bound collection must be repeated for the distinct timeout environment.
    (state / "profiles/timeout.json").write_text(json.dumps({**slow_profile, "timeout": 3}))
    timeout_repo = {**slow_repo, "profile_id": "timeout"}
    timeout_collection = runner.collect(slow_snap, timeout_repo, prefix + "-collect-timeout")
    assert timeout_collection["status"] == "completed", timeout_collection
    timeout = runner.run(slow_snap, timeout_repo, timeout_collection["nodeids"], prefix + "-timeout")
    assert timeout["status"] == "timeout" and timeout["cleanup_status"] == "completed", timeout
    known = {item["container"] for pair in results.values() for item in pair.values()}
    known.update(item["container"] for item in [collected, cancellation, timeout_collection, timeout])
    remaining = sorted(
        set(docker("ps", "-a", "--filter", "name=reposcope-", "--format", "{{.Names}}").splitlines()) & known
    )
    assert not remaining, remaining
    assert not list((state / "execution-tmp").iterdir())
    after_status = subprocess.run(
        ["git", "-C", repo["path"], "status", "--porcelain"], capture_output=True, text=True, check=True
    ).stdout
    assert before_status == after_status
    result = {
        "kind": "real-docker-acceptance",
        "timestamp": time.time(),
        "image_id": image["Id"],
        "image_repo_digests": image.get("RepoDigests", []),
        "python_environment": environment,
        "architecture": image["Architecture"],
        "docker": {
            key: info.get(key)
            for key in ["ServerVersion", "OperatingSystem", "OSType", "Architecture", "NCPU", "MemTotal", "Driver"]
        },
        "case": "fixture-01",
        "replay_manifest": case["replay_manifest"],
        "replay_repository": case["replay_repository"],
        "base_sha": base.commit_sha,
        "head_sha": head.commit_sha,
        "results": results,
        "comparison": comparison,
        "coverage_exact_binding": "passed",
        "cancellation": cancellation,
        "cancellation_observed_live_container": bool(seen_running),
        "timeout": timeout,
        "remaining_containers": [],
        "temporary_workspaces": [],
        "runtime_controls": runtime_controls,
        "working_tree_unchanged": True,
        "scope": "One self-owned fixture; proves this local isolation path, not arbitrary-project compatibility or benchmark quality",
    }
    output = ROOT / "benchmarks/results/docker-validation.json"
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                "output": str(output),
                "image_id": image["Id"],
                "comparison": comparison["status"],
                "base_tests": len(base_run["results"]),
                "head_tests": len(head_run["results"]),
                "coverage_rows": len(store.list("coverage")),
                "cancel": cancellation["status"],
                "timeout": timeout["status"],
            }
        )
    )


if __name__ == "__main__":
    main()
