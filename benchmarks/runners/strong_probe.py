"""Offline, non-gold strong retrieval probe against fixed public snapshots."""

import argparse
import gc
import importlib.metadata
import json
import os
import platform
import resource
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_HOME"] = str(ROOT / "artifacts/models/hf-runtime")
os.environ["TOKENIZERS_PARALLELISM"] = "false"
os.environ["OMP_NUM_THREADS"] = "4"

from reposcope.config import Settings  # noqa: E402
from reposcope.graph.store import Store  # noqa: E402
from reposcope.indexing.parser import build_snapshot  # noqa: E402
from reposcope.retrieval.search import Search  # noqa: E402

QUERIES = {
    "click": [
        "Validate a command line parameter against an integer range with inclusive and exclusive boundaries.",
        "Split an environment variable into multiple command line option values.",
    ],
    "httpx": [
        "Determine whether an HTTP response status code represents a client or server error.",
        "Decode JSON content from an HTTP response.",
    ],
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        default="strong-retrieval-chunks.json",
        help="Result filename; original baseline is never overwritten",
    )
    parser.add_argument("--cache-dir", default="artifacts/model-probe/chunk-vectors")
    args = parser.parse_args()
    if Path(args.output).name != args.output or not args.output.endswith(".json"):
        parser.error("Output must be a JSON filename")
    os.chdir(ROOT)
    result = {
        "annotation_status": "unreviewed",
        "quality_metrics": None,
        "scope": "four English development queries; actual Dense + BM25/identifier + RRF + CrossEncoder",
        "platform": platform.platform(),
        "python": platform.python_version(),
        "device": "cpu",
        "limitations": [
            "Not a held-out evaluation; no relevance labels or quality claims.",
            "General English models, not code-trained models; oversized complete declarations/lines remain explicit retrieval gaps, never silently truncated.",
            "No B2/B3/B4 quality comparison and no API or external inference service.",
        ],
        "queries": [],
        "disk_reload_checks": [],
        "torch_threads": 4,
    }
    output = ROOT / "benchmarks/results" / args.output

    def save():
        output.write_text(json.dumps(result, indent=2) + "\n")

    try:
        import torch

        torch.set_num_threads(4)
        result["packages"] = {
            name: importlib.metadata.version(name)
            for name in ["sentence-transformers", "transformers", "torch", "numpy", "huggingface-hub"]
        }
        models = json.loads((ROOT / "benchmarks/manifests/models.json").read_text())
        models["cache_dir"] = args.cache_dir
        result["models"] = models
        result["acquisition"] = {
            kind: json.loads((ROOT / f"artifacts/models/{kind}-acquisition.json").read_text())
            for kind in ["embedding", "reranker"]
        }
        manifest = json.loads((ROOT / "benchmarks/manifests/repositories.json").read_text())
        store = Store(Settings(home=ROOT / "artifacts/model-probe/index"))
        result["state"] = "running"
        save()
        for repo in manifest["repositories"]:
            snap = build_snapshot(
                store, {**repo, "path": str(ROOT / repo["path"]), "name": repo["repo_id"]}, repo["commit"]
            )
            search = Search(snap)
            for index, query in enumerate(QUERIES[repo["repo_id"]]):
                start = time.perf_counter()
                found = search.strong_query(query, models, limit=5)
                row = {
                    "repo_id": repo["repo_id"],
                    "commit": snap.commit_sha,
                    "snapshot_id": snap.snapshot_id,
                    "symbol_count": len(snap.symbols),
                    "parser_version": snap.parser_version,
                    "query": query,
                    "phase": "first-query-this-snapshot" if index == 0 else "warm-query-this-snapshot",
                    "wall_seconds": time.perf_counter() - start,
                    "process_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                    * (1 if platform.system() == "Darwin" else 1024),
                    "result": found,
                }
                result["queries"].append(row)
                print(
                    json.dumps(
                        {
                            "repo_id": repo["repo_id"],
                            "query_index": index,
                            "seconds": row["wall_seconds"],
                            "hits": len(found["hits"]),
                        }
                    ),
                    flush=True,
                )
                save()
            expected = result["queries"][-2]["result"]["hits"]
            del search
            gc.collect()
            reload_start = time.perf_counter()
            reloaded = Search(snap).strong_query(QUERIES[repo["repo_id"]][0], models, limit=5)
            ranking_equal = [h["symbol"]["symbol_id"] for h in reloaded["hits"]] == [
                h["symbol"]["symbol_id"] for h in expected
            ]
            result["disk_reload_checks"].append(
                {
                    "repo_id": repo["repo_id"],
                    "wall_seconds": time.perf_counter() - reload_start,
                    "cache_state": reloaded["vector_cache"]["state"],
                    "ranking_equal": ranking_equal,
                    "max_score_delta": max(abs(a["score"] - b["score"]) for a, b in zip(expected, reloaded["hits"])),
                }
            )
            assert ranking_equal and reloaded["vector_cache"]["state"] == "disk_hit"
            save()
        result["state"] = "completed"
    except Exception as exc:
        result["state"] = "blocked"
        result["error"] = {"type": type(exc).__name__, "message": str(exc)}
        save()
        raise
    save()


if __name__ == "__main__":
    main()
