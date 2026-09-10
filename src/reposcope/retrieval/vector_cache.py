"""Atomic snapshot/model-bound vector artifacts; no partially ready manifest."""

import hashlib
import json
import os
import tempfile
import zipfile
from pathlib import Path

import numpy as np

from reposcope.config import RepoScopeError
from reposcope.models import digest


class VectorCache:
    """Manifest and vectors share one NPZ publication boundary."""

    def __init__(self, root):
        self.root = Path(root)

    def path(self, binding):
        return self.root / f"{digest(binding)}.npz"

    def load(self, binding, rows=None):
        path = self.path(binding)
        if not path.exists():
            return None
        try:
            with np.load(path, allow_pickle=False) as artifact:
                manifest = json.loads(str(artifact["manifest"].item()))
                vectors = artifact["vectors"]
            valid = (
                manifest["binding"] == binding
                and manifest["state"] == "ready"
                and vectors.ndim == 2
                and (rows is None or vectors.shape[0] == rows)
                and list(vectors.shape) == manifest["shape"]
                and vectors.dtype == np.float32
                and np.isfinite(vectors).all()
                and hashlib.sha256(vectors.tobytes()).hexdigest() == manifest["vectors_sha256"]
                and (
                    "components" not in manifest or digest(manifest["components"]) == manifest.get("components_sha256")
                )
            )
            if not valid:
                raise ValueError("Binding, shape, state or checksum mismatch")
            return vectors, manifest
        except (OSError, ValueError, KeyError, EOFError, zipfile.BadZipFile) as exc:
            raise RepoScopeError("vector_cache_invalid", f"Invalid vector artifact {path.name}: {exc}") from exc

    def publish(self, binding, vectors, components=None, *, immutable=False):
        vectors = np.asarray(vectors, dtype=np.float32)
        if vectors.ndim != 2 or not np.isfinite(vectors).all():
            raise RepoScopeError("vector_cache_invalid", "Cannot publish invalid vectors")
        manifest = {
            "state": "ready",
            "binding": binding,
            "shape": list(vectors.shape),
            "vectors_sha256": hashlib.sha256(vectors.tobytes()).hexdigest(),
            "update_mode": "full-embedding-per-snapshot",
            "publication": "manifest and vectors in one atomic NPZ",
        }
        if components is not None:
            manifest.update(
                components=components,
                components_sha256=digest(components),
                publication="snapshot, corpus, sparse terms and vectors in one atomic NPZ",
            )
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.root, prefix=".vectors-", suffix=".tmp", delete=False) as output:
                temporary = Path(output.name)
                np.savez(output, vectors=vectors, manifest=json.dumps(manifest, sort_keys=True))
                output.flush()
                os.fsync(output.fileno())
            if immutable:
                try:
                    os.link(temporary, self.path(binding))
                except FileExistsError:
                    _, existing = self.load(binding, vectors.shape[0])
                    if existing["vectors_sha256"] != manifest["vectors_sha256"] or existing.get(
                        "components_sha256"
                    ) != manifest.get("components_sha256"):
                        raise RepoScopeError("vector_cache_invalid", "A different artifact already owns this binding")
                    return existing
            else:
                os.replace(temporary, self.path(binding))
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return manifest
