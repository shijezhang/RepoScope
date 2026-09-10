"""Offline real-model publication acceptance; not a quality benchmark."""

import json
import os
import subprocess
import time
import uuid
from pathlib import Path

os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", TOKENIZERS_PARALLELISM="false", OMP_NUM_THREADS="4")

from reposcope.config import Settings  # noqa: E402
from reposcope.graph.store import Store  # noqa: E402
from reposcope.indexing.parser import build_snapshot  # noqa: E402
from reposcope.indexing.publication import publish_index, search_published  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def main():
    import torch

    torch.set_num_threads(4)
    run_id = "run-" + uuid.uuid4().hex
    directory = ROOT / "artifacts/publication-validation" / run_id
    repository = directory / "repository"
    repository.mkdir(parents=True)

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(repository), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init", "--quiet")
    git("config", "user.name", "RepoScope fixture")
    git("config", "user.email", "fixture@example.invalid")
    (repository / "calc.py").write_text("def total(value):\n    return value + 1\n")
    (repository / "test_calc.py").write_text("from calc import total\ndef test_total():\n    assert total(1) == 2\n")
    git("add", ".")
    git("commit", "--quiet", "-m", "Complete small source")
    base_sha = git("rev-parse", "HEAD")
    (repository / "calc.py").write_text(
        'def total(value):\n    unused = "' + "word " * 2000 + '"\n    return value + 2\n'
    )
    git("add", ".")
    git("commit", "--quiet", "-m", "Oversized line remains partial")
    head_sha = git("rev-parse", "HEAD")
    store = Store(Settings(home=directory / "state"))
    repo = {"repo_id": run_id, "path": str(repository), "name": "Publication fixture"}
    base = build_snapshot(store, repo, base_sha)
    head = build_snapshot(store, repo, head_sha)
    models = json.loads((ROOT / "benchmarks/manifests/models.json").read_text())
    models["cache_dir"] = str(directory / "vectors")
    start = time.perf_counter()
    first = publish_index(store, base, models=models)
    assert first["activated"]
    partial = publish_index(store, head, models=models)
    assert partial["state"] == "partial" and not partial["activated"]
    found = search_published(store, run_id, "total", models=models)
    assert found["publication_id"] == first["publication_id"]
    assert found["commit_sha"] == base_sha and found["vector_cache"]["state"] == "disk_hit"
    result = {
        "run_id": run_id,
        "directory": str(directory.relative_to(ROOT)),
        "base_sha": base_sha,
        "head_sha": head_sha,
        "first_publication": first,
        "partial_candidate": partial,
        "query_publication_id": found["publication_id"],
        "query_commit_sha": found["commit_sha"],
        "cache": found["vector_cache"],
        "wall_seconds": time.perf_counter() - start,
        "status": "passed",
        "models_local_only": True,
        "target_tests_run": False,
        "quality_claim": False,
    }
    (directory / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    output = ROOT / "benchmarks/results/publication-validation.json"
    if output.exists():
        output = output.with_name("publication-validation-" + run_id + ".json")
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
