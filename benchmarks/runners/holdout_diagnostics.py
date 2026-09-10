"""Post-freeze diagnostic test overlays; original holdout observations remain intact."""

import hashlib
import json
import subprocess
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
    cases = json.loads((ROOT / "benchmarks/cases/holdout-candidates.json").read_text())
    run_id = "run-" + uuid.uuid4().hex
    directory = ROOT / "artifacts/holdout-diagnostics" / run_id
    directory.mkdir(parents=True)
    result = {
        "run_id": run_id,
        "kind": "post-hoc diagnostic reproduction, not original holdout evaluation",
        "state": "running",
        "cases": [],
    }
    output = ROOT / "benchmarks/results" / ("holdout-diagnostics-" + run_id + ".json")
    for case in cases:
        print(case["case_id"] + ": shared diagnostic test overlay", flush=True)
        root = directory / case["repo_id"] / "repository"
        root.parent.mkdir()
        subprocess.run(
            ["git", "clone", "--quiet", "--no-hardlinks", str(ROOT / case["repository"]), str(root)], check=True
        )

        def git(*args):
            return subprocess.check_output(
                ["git", "-C", str(root), *args], stderr=subprocess.DEVNULL, text=True
            ).strip()

        filename = "click-isolation.py" if case["repo_id"] == "click" else "httpx-client-cert.py"
        test_source = (ROOT / "benchmarks/diagnostics" / filename).read_bytes()

        # Freeze head-side tests/configuration/non-Python assets on BOTH sides.
        def assets(sha):
            paths = git("ls-tree", "-r", "--name-only", sha).splitlines()
            return {
                p for p in paths if not p.endswith(".py") or p.startswith("tests/") or Path(p).name == "conftest.py"
            }

        shared_assets = assets(case["head_sha"])
        shas = {}
        for side in ("base", "head"):
            git("checkout", "--detach", case[side + "_sha"])
            removed = assets(case[side + "_sha"]) - shared_assets
            if removed:
                git("rm", "--", *sorted(removed))
            git("checkout", case["head_sha"], "--", *sorted(shared_assets))
            (root / "tests/test_reposcope_diagnostic.py").write_bytes(test_source)
            git("add", "tests/test_reposcope_diagnostic.py")
            git(
                "-c",
                "user.name=RepoScope diagnostic",
                "-c",
                "user.email=fixture@example.invalid",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-qm",
                "Shared diagnostic test asset",
            )
            shas[side] = git("rev-parse", "HEAD")
            changed_paths = git("diff", "--name-only", case[side + "_sha"], shas[side]).splitlines()
            assert all(
                path in shared_assets or path in removed or path == "tests/test_reposcope_diagnostic.py"
                for path in changed_paths
            )
            git("update-ref", "refs/heads/diagnostic-" + side, shas[side])
        store = Store(Settings(home=root.parent / "state"))
        (store.settings.home / "profiles").mkdir()
        profile = json.loads((ROOT / f"artifacts/state/profiles/{case['repo_id']}.json").read_text())
        profile["test_paths"] = ["tests/test_reposcope_diagnostic.py"]
        (store.settings.home / "profiles/diagnostic.json").write_text(json.dumps(profile))
        repo = {"repo_id": case["repo_id"], "path": str(root), "profile_id": "diagnostic"}
        runner = TestRunner(store)
        row = {
            "case_id": case["case_id"],
            "origin_group": case["origin_group"],
            "original_base_sha": case["base_sha"],
            "original_head_sha": case["head_sha"],
            "execution_commits": shas,
            "shared_test_and_non_python_asset_commit": case["head_sha"],
            "production_python_unchanged_from_original_sides": True,
            "test_asset_sha256": hashlib.sha256(test_source).hexdigest(),
            "repository": str(root.relative_to(ROOT)),
            "executions": {},
        }
        for side in ("base", "head"):
            snap = build_snapshot(store, repo, shas[side])
            collection = runner.collect(snap, repo, "diagnostic-collect-" + side)
            assert collection["status"] == "completed", collection
            execution = runner.run(snap, repo, collection["nodeids"], "diagnostic-" + side)
            assert execution["cleanup_status"] == "completed"
            execution["counts"] = dict(Counter(t["status"] for t in execution["results"]))
            row["executions"][side] = execution
        pool = {t["nodeid"] for t in row["executions"]["head"]["results"]}
        row["comparison"] = compare_results(row["executions"]["base"], row["executions"]["head"], pool)
        row["reproduced_then_fixed"] = (
            row["executions"]["base"]["counts"].get("failed") == 1
            and row["executions"]["head"]["counts"].get("passed") == 1
        )
        assert row["executions"]["base"]["comparison_hash"] == row["executions"]["head"]["comparison_hash"]
        result["cases"].append(row)
        output.write_text(json.dumps(result, indent=2) + "\n")
    result["state"] = "passed" if all(r["reproduced_then_fixed"] for r in result["cases"]) else "failed"
    output.write_text(json.dumps(result, indent=2) + "\n")
    assert result["state"] == "passed", result
    print(output)


if __name__ == "__main__":
    main()
