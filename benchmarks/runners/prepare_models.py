"""Download pinned public model files without credentials or global cache writes."""

import hashlib
import json
import shutil
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEIGHTS_SHA = "821d1aa69520101d6e0737f78a042ae25b19e5cb9160701909d10434f4aeb0ae"
WEIGHTS_BYTES = 90870598
FILES = [
    "README.md",
    "config.json",
    "model.safetensors",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.txt",
]


def fetch(url, destination, expected_bytes=None):
    errors = []
    for attempt in range(4):
        offset = destination.stat().st_size if destination.exists() and expected_bytes else 0
        if expected_bytes and offset == expected_bytes:
            return errors
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=90) as response:
                append = (
                    offset
                    and response.status == 206
                    and response.headers.get("Content-Range", "").startswith(f"bytes {offset}-")
                )
                with destination.open("ab" if append else "wb") as output:
                    shutil.copyfileobj(response, output, length=1024 * 1024)
            if expected_bytes and destination.stat().st_size != expected_bytes:
                raise OSError(f"Incomplete file: {destination.stat().st_size}/{expected_bytes} bytes")
            return errors
        except OSError as exc:
            errors.append({"attempt": attempt + 1, "type": type(exc).__name__, "message": str(exc)})
            print(json.dumps(errors[-1]), flush=True)
    raise OSError(f"Download did not complete: {errors}")


def fetch_weights(url, destination, expected_bytes=WEIGHTS_BYTES, expected_sha=WEIGHTS_SHA):
    """Resume a preserved prefix with eight bounded, hash-checked range downloads."""
    offset = destination.stat().st_size if destination.exists() else 0
    if offset == expected_bytes:
        return []
    if offset > expected_bytes:
        raise OSError("Model weight exceeds expected size")
    parts = destination.parent / ".download-parts"
    parts.mkdir(exist_ok=True)
    ranges = [
        (start, min(start + 4 * 1024 * 1024, expected_bytes) - 1)
        for start in range(offset, expected_bytes, 4 * 1024 * 1024)
    ]

    def download(bounds):
        start, end = bounds
        part = parts / f"{start}-{end}"
        failures = []
        for attempt in range(4):
            have = part.stat().st_size if part.exists() else 0
            if have == end - start + 1:
                return part, failures
            try:
                request = urllib.request.Request(url, headers={"Range": f"bytes={start + have}-{end}"})
                with urllib.request.urlopen(request, timeout=90) as response:
                    if response.status != 206 or not response.headers.get("Content-Range", "").startswith(
                        f"bytes {start + have}-{end}/"
                    ):
                        raise OSError("Server did not honor bounded range")
                    with part.open("ab") as output:
                        shutil.copyfileobj(response, output, length=256 * 1024)
                if part.stat().st_size != end - start + 1:
                    raise OSError("Incomplete range")
                print(json.dumps({"completed_range": [start, end]}), flush=True)
                return part, failures
            except OSError as exc:
                failures.append({"range": [start, end], "attempt": attempt + 1, "message": str(exc)})
        raise OSError(f"Range download failed: {failures}")

    with ThreadPoolExecutor(max_workers=8) as executor:
        completed = list(executor.map(download, ranges))
    assembled = destination.with_suffix(".assembled")
    with assembled.open("wb") as output:
        if offset:
            with destination.open("rb") as prefix:
                shutil.copyfileobj(prefix, output)
        for part, _ in completed:
            with part.open("rb") as source:
                shutil.copyfileobj(source, output)
    if hashlib.sha256(assembled.read_bytes()).hexdigest() != expected_sha:
        assembled.unlink()
        raise OSError("Reassembled official weight SHA256 mismatch")
    assembled.replace(destination)
    shutil.rmtree(parts)
    return [failure for _, failures in completed for failure in failures]


def main():
    models = json.loads((ROOT / "benchmarks/manifests/models.json").read_text())
    embedding_root = ROOT / models["embedding"]
    embedding_started = time.perf_counter()
    downloaded = []
    for name, expected in models["embedding_files"].items():
        destination = embedding_root / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and hashlib.sha256(destination.read_bytes()).hexdigest() == expected["sha256"]:
            continue
        url = f"https://huggingface.co/{models['embedding_repository']}/resolve/{models['embedding_revision']}/{name}"
        if name == "model.safetensors":
            fetch_weights(url, destination, expected["bytes"], expected["sha256"])
        else:
            fetch(url, destination)
        if hashlib.sha256(destination.read_bytes()).hexdigest() != expected["sha256"]:
            raise RuntimeError(f"Embedding file hash mismatch: {name}")
        downloaded.append(name)
    acquisition = ROOT / "artifacts/models/embedding-acquisition.json"
    if downloaded or not acquisition.exists():
        acquisition.write_text(
            json.dumps(
                {
                    "repository": models["embedding_repository"],
                    "revision": models["embedding_revision"],
                    "license": "apache-2.0",
                    "acquisition": "pinned files downloaded" if downloaded else "existing verified local files",
                    "download_seconds": time.perf_counter() - embedding_started if downloaded else None,
                    "downloaded_files": downloaded,
                    "bytes": sum(f["bytes"] for f in models["embedding_files"].values()),
                },
                indent=2,
            )
            + "\n"
        )
    directory = ROOT / models["reranker"]
    directory.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    files = {}
    for name in FILES:
        path = directory / name
        before = path.stat().st_size if path.exists() else 0
        tick = time.perf_counter()
        url = f"https://huggingface.co/{models['reranker_repository']}/resolve/{models['reranker_revision']}/{name}"
        errors = fetch_weights(url, path) if name == "model.safetensors" else fetch(url, path)
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        if name == "model.safetensors" and checksum != WEIGHTS_SHA:
            raise RuntimeError("Official model weight SHA256 mismatch")
        files[name] = {
            "bytes": path.stat().st_size,
            "sha256": checksum,
            "existing_bytes": before,
            "seconds": time.perf_counter() - tick,
            "retried_errors": errors,
        }
        print(json.dumps({name: files[name]}), flush=True)
    record = {
        "repository": models["reranker_repository"],
        "revision": models["reranker_revision"],
        "license": "apache-2.0",
        "acquisition": "pinned model preparation; existing bytes recorded per file",
        "download_seconds_this_invocation": time.perf_counter() - started,
        "prior_failed_attempts": json.loads((ROOT / "artifacts/models/reranker-download-failures.json").read_text())
        if (ROOT / "artifacts/models/reranker-download-failures.json").exists()
        else [],
        "files": files,
        "bytes": sum(row["bytes"] for row in files.values()),
    }
    (ROOT / "artifacts/models/reranker-acquisition.json").write_text(json.dumps(record, indent=2) + "\n")


if __name__ == "__main__":
    main()
