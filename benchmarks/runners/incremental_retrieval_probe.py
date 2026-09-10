"""Real local-model full versus content-reuse consistency on one frozen change."""

import json
import os
import time
import uuid
from pathlib import Path

os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", TOKENIZERS_PARALLELISM="false", OMP_NUM_THREADS="4")

import numpy as np  # noqa: E402

from reposcope.config import Settings  # noqa: E402
from reposcope.graph.store import Store  # noqa: E402
from reposcope.indexing.parser import build_snapshot  # noqa: E402
from reposcope.retrieval.search import Search  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def main():
    import torch

    torch.set_num_threads(4)
    case = next(
        c
        for c in json.loads((ROOT / "benchmarks/review/2026-09-10/annotations.json").read_text())
        if c["case_id"] == "click-02"
    )
    run_id = "run-" + uuid.uuid4().hex
    directory = ROOT / "artifacts/incremental-retrieval" / run_id
    store = Store(Settings(home=directory / "state"))
    repo = {"repo_id": "click", "name": "click", "path": str(ROOT / case["replay_repository"])}
    base = build_snapshot(store, repo, case["base_sha"])
    head = build_snapshot(store, repo, case["head_sha"])
    models = json.loads((ROOT / "benchmarks/manifests/models.json").read_text())
    models["cache_dir"] = "artifacts/model-probe/chunk-vectors"
    query = "Split an environment variable into multiple command line option values."
    result = {
        "run_id": run_id,
        "case_id": case["case_id"],
        "base_sha": case["base_sha"],
        "head_sha": case["head_sha"],
        "query": query,
        "state": "running",
        "stages": {},
        "quality_claim": False,
    }
    output = ROOT / "benchmarks/results" / ("incremental-retrieval-" + run_id + ".json")

    def save():
        output.write_text(json.dumps(result, indent=2) + "\n")

    save()
    for label, snapshot, configuration in [
        ("base_seed", base, models),
        ("incremental", head, models),
        ("full", head, {**models, "cache_dir": str(directory / "full-vectors")}),
    ]:
        start = time.perf_counter()
        found = Search(snapshot).strong_query(query, configuration, limit=5)
        result["stages"][label] = {
            "seconds": time.perf_counter() - start,
            "cache": found["vector_cache"],
            "state": found["state"],
            "hits": found["hits"],
            "timings": found["timings"],
        }
        save()
        print(label, result["stages"][label]["seconds"], found["vector_cache"], flush=True)
    a, b = result["stages"]["incremental"], result["stages"]["full"]
    with (
        np.load(a["cache"]["artifact"], allow_pickle=False) as left,
        np.load(b["cache"]["artifact"], allow_pickle=False) as right,
    ):
        result["vector_max_abs_delta"] = float(np.max(np.abs(left["vectors"] - right["vectors"])))
        result["vectors_equal_within_1e_5"] = bool(np.allclose(left["vectors"], right["vectors"], rtol=0, atol=1e-5))
    result["ranking_equal"] = [h["symbol"]["symbol_id"] for h in a["hits"]] == [
        h["symbol"]["symbol_id"] for h in b["hits"]
    ]
    result["speedup"] = b["seconds"] / a["seconds"]
    result["state"] = "passed" if result["ranking_equal"] and result["vectors_equal_within_1e_5"] else "failed"
    save()
    assert result["state"] == "passed", result
    print(output)


if __name__ == "__main__":
    main()
