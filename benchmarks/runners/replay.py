"""Replay controlled development mutations and compare full/parse-incremental indexes.

Only clones under artifacts/benchmark-replay are mutated. Does not execute target code.
"""

import argparse
import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from reposcope.config import Settings
from reposcope.graph.store import Store
from reposcope.indexing.parser import build_snapshot, semantic_hash

ROOT = Path(__file__).resolve().parents[2]


def git(root, *args):
    env = {**os.environ, "GIT_AUTHOR_DATE": "2026-09-09T00:00:00Z", "GIT_COMMITTER_DATE": "2026-09-09T00:00:00Z"}
    return subprocess.check_output(
        ["git", "-C", str(root), *args], text=True, stderr=subprocess.DEVNULL, env=env
    ).strip()


def commit(root, message):
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=RepoScope benchmark",
        "-c",
        "user.email=benchmark@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-qm",
        message,
    )
    return git(root, "rev-parse", "HEAD")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", help="Replay a single case_id")
    args = parser.parse_args(argv)
    manifests = {
        r["repo_id"]: r
        for r in json.loads((ROOT / "benchmarks/manifests/repositories.json").read_text())["repositories"]
    }
    cases = json.loads((ROOT / "benchmarks/cases/development.json").read_text())
    if args.case:
        cases = [case for case in cases if case["case_id"] == args.case]
        if not cases:
            parser.error("Unknown case")
    run_id = "run-" + uuid.uuid4().hex
    run_directory = ROOT / "artifacts/benchmark-replay" / run_id
    run_directory.mkdir(parents=True, exist_ok=False)
    results = []
    for case in cases:
        # Published reports can retain Git object references indefinitely.
        # Every invocation owns a new directory; prior runs are never removed.
        directory = run_directory / case["case_id"]
        root = directory / "repository"
        root.parent.mkdir(parents=True, exist_ok=True)
        if case["repo_id"] == "fixture":
            shutil.copytree(
                ROOT / "benchmarks/fixtures", root, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache")
            )
            git(root, "init", "-q")
            base = commit(root, "Benchmark fixture base")
        else:
            manifest = manifests[case["repo_id"]]
            subprocess.run(
                ["git", "clone", "--quiet", "--no-hardlinks", str(ROOT / manifest["path"]), str(root)], check=True
            )
            git(root, "checkout", "--detach", manifest["commit"])
            base = manifest["commit"]
        mutation = case["mutation"]
        path = root / mutation["path"]
        source = path.read_text()
        expected = mutation.get("expected_occurrences", 1)
        actual = source.count(mutation["old"])
        if not mutation["old"] or actual != expected:
            raise RuntimeError(f"Mutation anchor mismatch: {case['case_id']} expected {expected}, found {actual}")
        path.write_text(source.replace(mutation["old"], mutation["new"]))
        head = commit(root, case["case_id"])
        repo = {"repo_id": case["repo_id"], "path": str(root), "name": case["repo_id"]}
        store = Store(Settings(home=directory / "incremental-state"))
        before = build_snapshot(store, repo, base, incremental=False)
        started = time.perf_counter()
        incremental = build_snapshot(store, repo, head, incremental=True)
        incremental_seconds = time.perf_counter() - started
        fresh = Store(Settings(home=directory / "full-state"))
        started = time.perf_counter()
        full = build_snapshot(fresh, repo, head, incremental=False)
        full_seconds = time.perf_counter() - started
        item = {
            "case_id": case["case_id"],
            "repo_id": case["repo_id"],
            "base": base,
            "head": head,
            "annotation_status": case["annotation_status"],
            "semantic_equal": semantic_hash(full) == semantic_hash(incremental),
            "semantic_hash": semantic_hash(full),
            "parser_version": full.parser_version,
            "base_seconds": before.stats["seconds"],
            "full_seconds": full_seconds,
            "incremental_seconds": incremental_seconds,
            "incremental_stats": incremental.stats,
            "full_status": full.status,
            "symbol_count": len(full.symbols),
            "relation_count": len(full.relations),
            "unresolved_count": len(full.unresolved),
            "diagnostics_count": len(full.diagnostics),
            "quality_metrics": None,
            "quality_metrics_reason": "unreviewed development probes; no baseline quality evaluation",
            "replay_repository": str(root.relative_to(ROOT)),
        }
        results.append(item)
        print(json.dumps(item), flush=True)
    output = ROOT / "benchmarks/results" / (f"{args.case}-index.json" if args.case else "index-consistency.json")
    document = {
        "scope": "single-run parse-cache consistency; all references re-resolved",
        "run_id": run_id,
        "cases": results,
    }
    content = json.dumps(document, indent=2) + "\n"
    (run_directory / "results.json").write_text(content)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{run_id}.tmp")
    try:
        temporary.write_text(content)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return int(any(not item["semantic_equal"] or item["full_status"] != "ready" for item in results))


if __name__ == "__main__":
    raise SystemExit(main())
