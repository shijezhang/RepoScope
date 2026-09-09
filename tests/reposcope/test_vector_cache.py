import numpy as np
import pytest

from reposcope.config import RepoScopeError
from reposcope.retrieval.vector_cache import VectorCache


def test_vector_snapshot_and_revision_isolation(tmp_path):
    cache = VectorCache(tmp_path)
    binding = {"snapshot_id": "a", "embedding_revision": "frozen-1", "reranker_revision": "frozen-r"}
    cache.publish(binding, np.array([[1.0, 0.0]], dtype=np.float32))
    vectors, manifest = cache.load(binding, 1)
    assert vectors.tolist() == [[1.0, 0.0]] and manifest["state"] == "ready"
    assert cache.load({**binding, "snapshot_id": "b"}, 1) is None
    assert cache.load({**binding, "embedding_revision": "frozen-2"}, 1) is None
    assert not list(tmp_path.glob("*.tmp"))


def test_corrupt_vector_artifact_is_not_ready(tmp_path):
    cache = VectorCache(tmp_path)
    binding = {"snapshot_id": "a"}
    cache.publish(binding, np.array([[1.0, 0.0]], dtype=np.float32))
    cache.path(binding).write_bytes(b"partial write or corrupt archive")
    with pytest.raises(RepoScopeError, match="Invalid vector artifact"):
        cache.load(binding, 1)


def test_failed_publication_preserves_previous_ready_artifact(tmp_path, monkeypatch):
    cache = VectorCache(tmp_path)
    binding = {"snapshot_id": "a"}
    cache.publish(binding, np.array([[1.0, 0.0]], dtype=np.float32))

    def fail(*args):
        raise OSError("simulated disk publication failure")

    monkeypatch.setattr("reposcope.retrieval.vector_cache.os.replace", fail)
    with pytest.raises(OSError):
        cache.publish(binding, np.array([[0.0, 1.0]], dtype=np.float32))
    vectors, _ = cache.load(binding, 1)
    assert vectors.tolist() == [[1.0, 0.0]]
    assert not list(tmp_path.glob("*.tmp"))
