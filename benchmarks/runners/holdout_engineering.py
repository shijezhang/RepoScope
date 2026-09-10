"""Frozen upstream-origin local ablations; no gold metrics or external model calls."""

import hashlib
import json
import os
import time
import uuid
from collections import Counter
from pathlib import Path

os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", TOKENIZERS_PARALLELISM="false", OMP_NUM_THREADS="4")

from reposcope.analysis.impact import analyze  # noqa: E402
from reposcope.config import Settings  # noqa: E402
from reposcope.execution.runner import TestRunner  # noqa: E402
from reposcope.graph.store import Store  # noqa: E402
from reposcope.indexing.parser import build_snapshot, semantic_hash  # noqa: E402
from reposcope.jobs.worker import Worker, compare_results  # noqa: E402
from reposcope.retrieval.search import Search  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def main():
    import torch

    torch.set_num_threads(4)
    source = ROOT / "benchmarks/cases/holdout-candidates.json"
    seal = json.loads(source.with_name("holdout-candidates-seal.json").read_text())
    assert hashlib.sha256(source.read_bytes()).hexdigest() == seal["sha256"]
    run_id = "run-" + uuid.uuid4().hex
    directory = ROOT / "artifacts/holdout-engineering" / run_id
    store = Store(Settings(home=directory / "state"))
    (store.settings.home / "profiles").mkdir()
    output = ROOT / "benchmarks/results" / ("holdout-engineering-" + run_id + ".json")
    result = {
        "run_id": run_id,
        "state": "running",
        "input_sha256": seal["sha256"],
        "quality_metrics": None,
        "limitations": [
            "Two convenience-sampled upstream fixes, not a representative formal test set",
            "No relevance gold; method hit counts are not quality improvements",
            "B4 not run: these sources are outside the approved external-model scope",
        ],
        "cases": [],
    }

    def save():
        output.write_text(json.dumps(result, indent=2) + "\n")

    save()
    for case in json.loads(source.read_text()):
        print(case["case_id"] + ": build and local retrieval ablations", flush=True)
        repo = {"repo_id": case["repo_id"], "path": str(ROOT / case["repository"]), "profile_id": case["repo_id"]}
        profile = json.loads((ROOT / f"artifacts/state/profiles/{case['repo_id']}.json").read_text())
        profile["test_paths"] = case["test_paths"]
        (store.settings.home / f"profiles/{repo['profile_id']}.json").write_text(json.dumps(profile))
        base = build_snapshot(store, repo, case["base_sha"], incremental=False)
        head = build_snapshot(store, repo, case["head_sha"])
        full = build_snapshot(store, repo, case["head_sha"], incremental=False)
        row = {
            "case_id": case["case_id"],
            "base_sha": case["base_sha"],
            "head_sha": case["head_sha"],
            "origin_group": case["origin_group"],
            "split": case["split"],
            "annotation_status": "unreviewed",
            "parser_version": head.parser_version,
            "parser_equal": semantic_hash(head) == semantic_hash(full),
            "incremental_stats": head.stats,
            "methods": {},
            "collections": {},
            "executions": {},
        }
        result["cases"].append(row)
        assert row["parser_equal"]
        engine = Search(head)
        start = time.perf_counter()
        sparse = engine.query(case["query"], 10)
        row["methods"]["B0_sparse"] = {"seconds": time.perf_counter() - start, "hits": sparse}
        models = json.loads((ROOT / "benchmarks/manifests/models.json").read_text())
        models["cache_dir"] = "artifacts/model-probe/chunk-vectors"
        start = time.perf_counter()
        strong = engine.strong_query(case["query"], models, 10)
        row["methods"]["B1_strong"] = {
            "seconds": time.perf_counter() - start,
            "state": strong["state"],
            "hits": strong["hits"],
            "cache": strong["vector_cache"],
        }
        start = time.perf_counter()
        report = analyze(store, repo, base, head, case["case_id"])
        row["methods"]["B2_graph"] = {
            "seconds": time.perf_counter() - start,
            "impact_count": len(report["impacts"]),
            "limitations": report["limitations"],
            "comparable_to_search_precision": False,
        }
        save()
        runner = TestRunner(store)
        for side, snap in (("base", base), ("head", head)):
            print(case["case_id"] + ": Docker " + side, flush=True)
            collection = runner.collect(snap, repo, case["case_id"] + "-collect-" + side)
            row["collections"][side] = collection
            if collection["status"] == "completed":
                execution = runner.run(snap, repo, collection["nodeids"], case["case_id"] + "-" + side)
                raw_path = directory / (case["case_id"] + "-" + side + ".json")
                raw_path.write_text(json.dumps(execution, indent=2) + "\n")
                row["executions"][side] = execution
            save()
        if all(c["status"] == "completed" for c in row["collections"].values()):
            selection = Worker(store).select_plan(
                report, base, head, row["collections"]["head"], row["collections"]["base"]
            )
            row["methods"]["B3_coverage"] = {
                "selection": selection,
                "head_failure_set_used": False,
                "alternative_policy_executed": False,
            }
            if len(row["executions"]) == 2:
                pool = set(row["collections"]["base"]["nodeids"]) & set(row["collections"]["head"]["nodeids"])
                row["comparison"] = compare_results(row["executions"]["base"], row["executions"]["head"], pool)
        for side, execution in row["executions"].items():
            row["executions"][side] = {
                "status": execution["status"],
                "counts": dict(Counter(t["status"] for t in execution.get("results", []))),
                "results": execution.get("results", []),
                "cleanup_status": execution.get("cleanup_status"),
                "raw_artifact": str((directory / (case["case_id"] + "-" + side + ".json")).relative_to(ROOT)),
            }
        save()
    result["state"] = "completed"
    save()
    print(output)


if __name__ == "__main__":
    main()
