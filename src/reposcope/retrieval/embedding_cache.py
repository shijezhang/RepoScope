"""Checksummed content-addressed embedding rows, independent of snapshot identity."""

import hashlib
import sqlite3
from pathlib import Path

import numpy as np

from reposcope.config import RepoScopeError
from reposcope.models import digest


class EmbeddingCache:
    def __init__(self, root, binding):
        self.path = Path(root) / "embedding-rows.sqlite3"
        self.identity = {
            key: value
            for key, value in binding.items()
            if key
            not in {
                "snapshot_id",
                "manifest_hash",
                "parser_version",
                "snapshot_content_hash",
            }
        }

    def keys(self, texts):
        return [digest({"text": text, "identity": self.identity}) for text in texts]

    def load(self, texts):
        keys = self.keys(texts)
        if not self.path.exists():
            return [None] * len(keys)
        found = {}
        try:
            with sqlite3.connect(self.path) as db:
                for offset in range(0, len(keys), 500):
                    batch = keys[offset : offset + 500]
                    if not batch:
                        continue
                    rows = db.execute(
                        "SELECT id,vector,checksum FROM embeddings WHERE id IN (" + ",".join("?" * len(batch)) + ")",
                        batch,
                    )
                    for key, data, expected in rows:
                        vector = np.frombuffer(data, dtype=np.float32).copy()
                        if (
                            not len(vector)
                            or not np.isfinite(vector).all()
                            or hashlib.sha256(data).hexdigest() != expected
                        ):
                            raise ValueError("Embedding row checksum or values invalid")
                        found[key] = vector
            return [found.get(key) for key in keys]
        except (sqlite3.Error, ValueError) as exc:
            raise RepoScopeError("vector_cache_invalid", "Invalid content embedding cache") from exc

    def publish(self, texts, vectors):
        if len(texts) != len(vectors):
            raise RepoScopeError("vector_cache_invalid", "Embedding row count mismatch")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS embeddings(id TEXT PRIMARY KEY, vector BLOB, checksum TEXT)")
            for key, vector in zip(self.keys(texts), vectors):
                vector = np.asarray(vector, dtype=np.float32)
                if vector.ndim != 1 or not len(vector) or not np.isfinite(vector).all():
                    raise RepoScopeError("vector_cache_invalid", "Cannot cache invalid embedding row")
                data = vector.tobytes()
                db.execute(
                    "INSERT OR IGNORE INTO embeddings VALUES(?,?,?)", (key, data, hashlib.sha256(data).hexdigest())
                )
