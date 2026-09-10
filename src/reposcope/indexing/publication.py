"""Profile-scoped immutable indexes with compare-and-swap publication."""

import hashlib
from pathlib import Path

from reposcope.config import RepoScopeError
from reposcope.models import Snapshot, digest
from reposcope.retrieval.index_bundle import snapshot_content
from reposcope.retrieval.search import Search, tokenize


def checksum(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def publish_index(store, snapshot, profile="default", models=None):
    if not profile or len(profile) > 100:
        raise RepoScopeError("invalid_profile", "Index profile must have 1 to 100 characters")
    previous = store.active_index(snapshot.repo_id, profile)
    previous_id = previous["publication_id"] if previous else None
    if snapshot.status != "ready":
        raise RepoScopeError("snapshot_not_ready", "Cannot publish an incomplete parsed snapshot")
    if models is not None and not snapshot.symbols:
        raise RepoScopeError("retrieval_corpus_unavailable", "An empty snapshot has no strong-index corpus")
    search = Search(snapshot)
    record = {
        "repo_id": snapshot.repo_id,
        "profile": profile,
        "version": "index-publication-v1",
        "snapshot": snapshot_content(snapshot),
        "mode": "strong" if models is not None else "sparse",
        "model_configuration_hash": digest(models) if models is not None else None,
        "sparse_version": "identifier-regex-v1",
        "sparse_terms": [tokenize(text) or ["_"] for text in search.texts],
        "test_symbols": [symbol.symbol_id for symbol in snapshot.symbols if symbol.kind == "TestCase"],
        "test_catalog_kind": "static definitions; runtime pytest collection remains execution-profile-bound",
    }
    if models is not None:
        # This fixed, non-source query verifies the real inference/rerank path.
        built = search.strong_query("index", models, limit=1)
        if built["state"] != "ready":
            return {
                "state": "partial",
                "activated": False,
                "previous_publication_id": previous_id,
                "snapshot_id": snapshot.snapshot_id,
                "corpus": built["corpus"],
            }
        artifact = Path(built["vector_cache"]["artifact"]).resolve()
        record["bundle"] = {"path": str(artifact), "sha256": checksum(artifact)}
    else:
        search.query("index", limit=1)
    record["publication_id"] = digest(record)
    store.activate_index(record, previous_id)
    return {
        "state": "ready",
        "activated": True,
        "publication_id": record["publication_id"],
        "previous_publication_id": previous_id,
        "snapshot_id": snapshot.snapshot_id,
        "profile": profile,
    }


def search_published(store, repo_id, query, profile="default", models=None, limit=20):
    # Read pointer+record in one SQLite statement; retain this record throughout
    # the query even when another build publishes a newer version.
    record = store.active_index(repo_id, profile)
    if record is None:
        raise RepoScopeError("index_not_published", "No complete index is published for this repository/profile")
    if record["publication_id"] != digest({k: v for k, v in record.items() if k != "publication_id"}):
        raise RepoScopeError("index_publication_invalid", "Published index content hash mismatch")
    if record["model_configuration_hash"] != (digest(models) if models is not None else None):
        raise RepoScopeError("index_profile_mismatch", "Query models differ from the published index configuration")
    snapshot = Snapshot.model_validate(record["snapshot"])
    search = Search(snapshot)
    if [tokenize(text) or ["_"] for text in search.texts] != record["sparse_terms"]:
        raise RepoScopeError("index_publication_invalid", "Sparse index differs from its published snapshot")
    if record["mode"] == "strong":
        bundle = record["bundle"]
        try:
            if checksum(bundle["path"]) != bundle["sha256"]:
                raise ValueError("Bundle checksum changed")
        except (OSError, ValueError) as exc:
            raise RepoScopeError("index_publication_invalid", "Published bundle is missing or changed") from exc
        result = search.strong_query(query, models, limit, expected_artifact=bundle["path"])
        if Path(result["vector_cache"]["artifact"]).resolve() != Path(bundle["path"]):
            raise RepoScopeError("index_profile_mismatch", "Model/runtime identity differs from the published bundle")
    else:
        result = {"hits": search.query(query, limit), "state": "ready"}
    return {
        **result,
        "publication_id": record["publication_id"],
        "snapshot_id": snapshot.snapshot_id,
        "commit_sha": snapshot.commit_sha,
        "profile": profile,
    }
