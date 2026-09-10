"""Verify local model bytes before either model or index reuse."""

import hashlib
from pathlib import Path

from reposcope.config import RepoScopeError


def verify_model_files(models):
    identities = {}
    try:
        for kind in ("embedding", "reranker"):
            root = Path(models[kind]).resolve(strict=True)
            if not root.is_dir():
                raise ValueError(f"{kind} must be a local model directory")
            declared = models.get(kind + "_files")
            actual = {}
            for path in sorted(root.rglob("*")):
                if path.is_symlink() or not path.resolve().is_relative_to(root):
                    raise ValueError(f"{kind} model directory contains a symbolic link")
                if not path.is_file():
                    continue
                with path.open("rb") as source:
                    checksum = hashlib.file_digest(source, "sha256").hexdigest()
                actual[path.relative_to(root).as_posix()] = {"sha256": checksum, "bytes": path.stat().st_size}
            if not actual:
                raise ValueError(f"{kind} model directory is empty")
            if declared is not None:
                if not isinstance(declared, dict) or set(declared) != set(actual):
                    raise ValueError(f"{kind} model file inventory differs from the pinned manifest")
                for name, expected in declared.items():
                    if not isinstance(expected, dict) or expected.get("sha256") != actual[name]["sha256"]:
                        raise ValueError(f"{kind} model file checksum mismatch: {name}")
                    if "bytes" in expected and expected["bytes"] != actual[name]["bytes"]:
                        raise ValueError(f"{kind} model file size mismatch: {name}")
            expected_weights = models.get(kind + "_weights_sha256")
            if expected_weights and actual.get("model.safetensors", {}).get("sha256") != expected_weights:
                raise ValueError(f"{kind} weights do not match the pinned manifest")
            identities[kind] = {
                "verification": "pinned-file-manifest" if declared is not None else "local-content-fingerprint",
                "files": actual,
            }
    except (OSError, ValueError, KeyError) as exc:
        raise RepoScopeError("model_unavailable", f"Local model verification failed: {exc}") from exc
    return identities
