"""Chunked strong-search contracts exercised without loading model libraries."""

import importlib.metadata
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest

from reposcope.config import RepoScopeError
from reposcope.models import Snapshot, Symbol, digest
from reposcope.retrieval.model_files import verify_model_files
from reposcope.retrieval.search import Search

QUERY = "needle"


@pytest.fixture
def fake_models(monkeypatch, tmp_path):
    observed = SimpleNamespace(embedding_batches=[], reranker_batches=[], tokenizer_calls=[], loaded=[])

    class Tokenizer:
        model_max_length = 256
        name_or_path = "fake-character-tokenizer"

        @staticmethod
        def num_special_tokens_to_add(pair=False):
            return 3 if pair else 2

        def encode(self, text, add_special_tokens=True, truncation=False, **kwargs):
            assert isinstance(text, str)
            assert truncation is False, "Token counts must not truncate before checking the budget"
            observed.tokenizer_calls.append((text, None, truncation))
            size = len(text) + (self.num_special_tokens_to_add() if add_special_tokens else 0)
            return list(range(size))

        def __call__(self, query, text=None, add_special_tokens=True, truncation=False, **kwargs):
            assert isinstance(query, str) and (text is None or isinstance(text, str))
            assert truncation is False, "Pair counting must not silently truncate"
            observed.tokenizer_calls.append((query, text, truncation))
            size = len(query) + (len(text) if text is not None else 0)
            if add_special_tokens:
                size += self.num_special_tokens_to_add(pair=text is not None)
            return {"input_ids": list(range(size)), "attention_mask": [1] * size}

    class FakeEmbedding:
        max_seq_length = 256

        def __init__(self, name, **kwargs):
            assert kwargs.get("local_files_only") is True
            assert kwargs.get("revision")
            self.tokenizer = Tokenizer()
            observed.loaded.append(("embedding", name))

        def encode(self, texts, **kwargs):
            single = isinstance(texts, str)
            batch = [texts] if single else list(texts)
            for text in batch:
                assert len(self.tokenizer.encode(text, truncation=False)) <= self.max_seq_length, (
                    "Embedding received oversized input instead of a valid chunk"
                )
            observed.embedding_batches.append(tuple(batch))
            vectors = np.asarray([[1.0, 0.0] if QUERY in text else [0.0, 1.0] for text in batch], dtype=np.float32)
            vectors = vectors.reshape((-1, 2))
            return vectors[0] if single else vectors

    class FakeReranker:
        max_length = 256

        def __init__(self, name, **kwargs):
            assert kwargs.get("local_files_only") is True
            assert kwargs.get("revision")
            self.tokenizer = Tokenizer()
            observed.loaded.append(("reranker", name))

        def predict(self, pairs, **kwargs):
            pairs = list(pairs)
            for query, text in pairs:
                actual = self.tokenizer(query, text, add_special_tokens=True, truncation=False)
                assert len(actual["input_ids"]) <= self.max_length, "Reranker received an oversized query/document pair"
            observed.reranker_batches.append(tuple(pairs))
            return np.asarray([100.0 if query in text else 0.0 for query, text in pairs], dtype=np.float32)

    module = ModuleType("sentence_transformers")
    module.SentenceTransformer = FakeEmbedding
    module.CrossEncoder = FakeReranker
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)
    original_version = importlib.metadata.version
    monkeypatch.setattr(
        importlib.metadata,
        "version",
        lambda name: (
            "0.0.test" if name in {"sentence-transformers", "transformers", "torch"} else original_version(name)
        ),
    )
    for kind in ("embedding", "reranker"):
        directory = tmp_path / kind
        directory.mkdir()
        (directory / "config.json").write_text("{}")
    models = {
        "embedding": str(tmp_path / "embedding"),
        "reranker": str(tmp_path / "reranker"),
        "embedding_revision": "embedding-v1",
        "reranker_revision": "reranker-v1",
        "query_max_tokens": 8,
        "chunk_overlap_lines": 1,
        "cache_dir": str(tmp_path / "vectors"),
    }
    return models, observed


def make_snapshot(files, *, snapshot_id="base", signatures=None):
    symbols = []
    for index, (path, source) in enumerate(files.items()):
        name = path.removesuffix(".py")
        symbols.append(
            Symbol(
                symbol_id=f"{snapshot_id}-{index}",
                snapshot_id=snapshot_id,
                path=path,
                module=name,
                qualname=name,
                kind="Function",
                start=1,
                end=len(source.splitlines()),
                signature=(signatures or {}).get(path, ""),
                content_hash=digest("\n".join(source.splitlines())),
            )
        )
    return Snapshot(
        snapshot_id=snapshot_id,
        repo_id="test-repo",
        commit_sha="commit-" + snapshot_id,
        tree_hash=digest(files),
        manifest_hash=digest(files),
        files=files,
        symbols=symbols,
        relations=[],
        unresolved=[],
        diagnostics=[],
    )


def long_source():
    return (
        "def process():\n"
        + "\n".join(f"    value_{index:02d} = {index}" for index in range(60))
        + '\n    return "needle"\n'
    )


def encoded_documents(observed):
    return [text for batch in observed.embedding_batches for text in batch if text != QUERY]


def assert_hit_binding(hit, snapshot):
    chunk, parent = hit["chunk"], hit["symbol"]
    assert {
        "chunk_id",
        "parent_symbol_id",
        "snapshot_id",
        "path",
        "start",
        "end",
        "source",
        "content_hash",
    } <= chunk.keys()
    assert chunk["parent_symbol_id"] == parent["symbol_id"]
    assert chunk["snapshot_id"] == parent["snapshot_id"] == snapshot.snapshot_id
    assert chunk["path"] == parent["path"]
    assert parent["start"] <= chunk["start"] <= chunk["end"] <= parent["end"]
    canonical = "\n".join(snapshot.files[chunk["path"]].splitlines()[chunk["start"] - 1 : chunk["end"]])
    assert chunk["source"] == canonical
    assert chunk["content_hash"] == digest(canonical)


def test_late_matching_chunk_is_retrieved_and_parents_are_not_duplicated(fake_models):
    models, observed = fake_models
    snapshot = make_snapshot({"process.py": long_source(), "other.py": "def other():\n    return 0\n"})
    result = Search(snapshot).strong_query(QUERY, models, limit=5)
    assert result["state"] == "ready"
    assert result["corpus"]["symbols_total"] == 2
    assert result["corpus"]["parents_with_ready_chunks"] == 2
    assert result["corpus"]["ready_chunks"] > 2
    assert result["corpus"]["oversized_chunks"] == 0
    assert len({hit["symbol"]["symbol_id"] for hit in result["hits"]}) == len(result["hits"]) == 2
    best = result["hits"][0]
    assert best["symbol"]["path"] == "process.py"
    assert 'return "needle"' in best["chunk"]["source"]
    assert best["chunk"]["start"] > 20, "Search returned a truncated prefix instead of the matching tail"
    for hit in result["hits"]:
        assert_hit_binding(hit, snapshot)
    assert len(encoded_documents(observed)) == result["corpus"]["ready_chunks"]
    assert any('return "needle"' in text for text in encoded_documents(observed))


def test_oversized_line_is_reported_partial_and_never_sent_to_models(fake_models):
    models, observed = fake_models
    giant = '    value = "UNFIT_' + "x" * 900 + '_needle"'
    snapshot = make_snapshot(
        {
            "oversized.py": "def oversized():\n" + giant + "\n    return 1\n",
            "small.py": 'def small():\n    return "needle"\n',
        }
    )
    result = Search(snapshot).strong_query(QUERY, models, limit=5)
    assert result["state"] == "partial"
    assert result["corpus"]["oversized_chunks"] >= 1
    assert result["corpus"]["ready_chunks"] >= 1
    assert result["corpus"]["oversized"], "The skipped source ranges must remain visible"
    assert not any("UNFIT_" in text for text in encoded_documents(observed))
    assert not any("UNFIT_" in text for batch in observed.reranker_batches for _, text in batch)
    assert result["hits"][0]["symbol"]["path"] == "small.py"
    for hit in result["hits"]:
        assert_hit_binding(hit, snapshot)


def test_corpus_with_only_oversized_signature_fails_without_model_inference(fake_models):
    models, observed = fake_models
    signature = ", ".join(f"argument_{index}: int = {index}" for index in range(45))
    source = f"def huge({signature}):\n    return 1\n"
    snapshot = make_snapshot({"huge.py": source}, signatures={"huge.py": signature})
    with pytest.raises(RepoScopeError) as error:
        Search(snapshot).strong_query(QUERY, models)
    assert error.value.code == "retrieval_corpus_unavailable"
    assert not observed.embedding_batches and not observed.reranker_batches


def test_overlong_query_is_rejected_before_inference_or_cache_publication(fake_models, tmp_path):
    models, observed = fake_models
    snapshot = make_snapshot({"small.py": 'def small():\n    return "needle"\n'})
    with pytest.raises(RepoScopeError) as error:
        Search(snapshot).strong_query("long query beyond the declared reserve", models)
    assert error.value.code == "query_budget_exceeded"
    assert not observed.embedding_batches and not observed.reranker_batches
    assert not list(tmp_path.rglob("*.npz"))


def test_chunk_vectors_reload_from_disk_and_remain_snapshot_bound(fake_models):
    models, observed = fake_models
    files = {"process.py": long_source()}
    base = make_snapshot(files)
    first = Search(base).strong_query(QUERY, models)
    first_documents = len(encoded_documents(observed))
    second = Search(base).strong_query(QUERY, models)
    assert first["vector_cache"]["state"] == "built"
    assert second["vector_cache"]["state"] == "disk_hit"
    assert len(encoded_documents(observed)) == first_documents
    assert first["hits"][0]["chunk"]["chunk_id"] == second["hits"][0]["chunk"]["chunk_id"]
    assert first["vector_cache"]["artifact"] == second["vector_cache"]["artifact"]
    head = make_snapshot(files, snapshot_id="head")
    third = Search(head).strong_query(QUERY, models)
    assert third["vector_cache"]["state"] == "built"
    assert third["vector_cache"]["artifact"] != first["vector_cache"]["artifact"]
    assert third["hits"][0]["chunk"]["chunk_id"] != first["hits"][0]["chunk"]["chunk_id"]
    assert_hit_binding(third["hits"][0], head)


def test_wrong_snapshot_or_source_hash_is_rejected_before_embedding(fake_models):
    models, observed = fake_models
    snapshot = make_snapshot({"small.py": "def small():\n    return 1\n"})
    wrong_owner = snapshot.model_copy(
        update={"symbols": [snapshot.symbols[0].model_copy(update={"snapshot_id": "other-snapshot"})]}
    )
    changed_source = snapshot.model_copy(update={"files": {"small.py": "def small():\n    return 2\n"}})
    for invalid in [wrong_owner, changed_source]:
        with pytest.raises(RepoScopeError) as error:
            Search(invalid).strong_query(QUERY, models)
        assert error.value.code == "snapshot_mismatch"
    assert not observed.embedding_batches and not observed.reranker_batches


def test_document_packing_respects_a_smaller_reranker_pair_limit(fake_models, monkeypatch):
    models, observed = fake_models
    monkeypatch.setattr(sys.modules["sentence_transformers"].CrossEncoder, "max_length", 192)
    snapshot = make_snapshot({"process.py": long_source()})
    result = Search(snapshot).strong_query(QUERY, models)
    assert result["state"] == "ready"
    assert result["limits"]["embedding_max_tokens"] == 256
    assert result["limits"]["reranker_max_tokens"] == 192
    assert result["hits"][0]["chunk"]["end"] == snapshot.symbols[0].end
    assert all(len(query) + len(text) + 3 <= 192 for batch in observed.reranker_batches for query, text in batch)
    assert_hit_binding(result["hits"][0], snapshot)


@pytest.mark.parametrize("mutation", ["change", "add", "remove", "symlink"])
def test_pinned_model_file_drift_rejected_on_warm_query(fake_models, mutation):
    models, observed = fake_models
    identity = verify_model_files(models)
    for kind in identity:
        models[kind + "_files"] = identity[kind]["files"]
    search = Search(make_snapshot({"process.py": long_source()}))
    search.strong_query(QUERY, models)
    before = len(observed.embedding_batches)
    root = Path(models["embedding"])
    if mutation == "change":
        (root / "config.json").write_text('{"changed": true}')
    elif mutation == "add":
        (root / "tokenizer.json").write_text("{}")
    elif mutation == "remove":
        (root / "config.json").unlink()
    else:
        (root / "extra.json").symlink_to(root / "config.json")
    with pytest.raises(RepoScopeError) as error:
        search.strong_query(QUERY, models)
    assert error.value.code == "model_unavailable"
    assert len(observed.embedding_batches) == before


def test_changed_model_bytes_rebuild_runtime_and_disk_cache_at_same_revision(fake_models):
    models, observed = fake_models
    search = Search(make_snapshot({"process.py": long_source()}))
    first = search.strong_query(QUERY, models)
    second = search.strong_query(QUERY, models)
    assert second["vector_cache"]["state"] == "memory_hit"
    assert len(observed.loaded) == 2
    (Path(models["embedding"]) / "config.json").write_text('{"changed": true}')
    third = search.strong_query(QUERY, models)
    assert third["model_memory_reused"] is False
    assert third["vector_cache"]["state"] == "built"
    assert third["vector_cache"]["artifact"] != first["vector_cache"]["artifact"]
    assert len(observed.loaded) == 4
    assert Search(search.snapshot).strong_query(QUERY, models)["vector_cache"]["state"] == "disk_hit"


def test_declared_weight_checksum_still_required(fake_models):
    models, observed = fake_models
    models["embedding_weights_sha256"] = "wrong"
    with pytest.raises(RepoScopeError) as error:
        Search(make_snapshot({"process.py": long_source()})).strong_query(QUERY, models)
    assert error.value.code == "model_unavailable"
    assert not observed.loaded


def test_bundle_reload_avoids_rechunking_and_retains_partial_state(fake_models, monkeypatch):
    models, observed = fake_models
    snapshot = make_snapshot({"process.py": long_source(), "huge.py": 'def huge():\n    value = "' + "x" * 900 + '"'})
    first = Search(snapshot).strong_query(QUERY, models)
    documents_before = len(encoded_documents(observed))

    def fail(*args, **kwargs):
        raise AssertionError("Disk bundle must restore corpus without repeating tokenizer chunk construction")

    monkeypatch.setattr("reposcope.retrieval.search.prepare_corpus", fail)
    second = Search(snapshot).strong_query(QUERY, models)
    assert second["vector_cache"]["state"] == "disk_hit"
    assert second["state"] == first["state"] == "partial"
    assert second["corpus"] == first["corpus"]
    assert second["hits"] == first["hits"]
    assert len(encoded_documents(observed)) == documents_before


@pytest.mark.parametrize("repair_checksum", [False, True])
def test_bundle_rejects_corrupt_source_components(fake_models, repair_checksum):
    models, observed = fake_models
    snapshot = make_snapshot({"process.py": long_source()})
    first = Search(snapshot).strong_query(QUERY, models)
    artifact = Path(first["vector_cache"]["artifact"])
    with np.load(artifact, allow_pickle=False) as archive:
        vectors = archive["vectors"]
        manifest = json.loads(str(archive["manifest"].item()))
    manifest["components"]["chunks"][0]["source"] = "corrupt source"
    if repair_checksum:
        manifest["components_sha256"] = digest(manifest["components"])
    np.savez(artifact, vectors=vectors, manifest=json.dumps(manifest))
    before = len(observed.embedding_batches)
    with pytest.raises(RepoScopeError) as error:
        Search(snapshot).strong_query(QUERY, models)
    assert error.value.code == "vector_cache_invalid"
    assert len(observed.embedding_batches) == before


def test_failed_reranker_does_not_publish_bundle_or_replace_previous_snapshot(fake_models, monkeypatch):
    models, _ = fake_models
    snapshot = make_snapshot({"process.py": long_source()})
    first = Search(snapshot).strong_query(QUERY, models)
    original_path = Path(first["vector_cache"]["artifact"])
    original_bytes = original_path.read_bytes()

    def fail(*args, **kwargs):
        raise RuntimeError("simulated reranker failure")

    monkeypatch.setattr(sys.modules["sentence_transformers"].CrossEncoder, "predict", fail)
    head = make_snapshot({"process.py": long_source()}, snapshot_id="head")
    with pytest.raises(RuntimeError, match="simulated reranker"):
        Search(head).strong_query(QUERY, models)
    assert original_path.read_bytes() == original_bytes
    assert list(original_path.parent.glob("*.npz")) == [original_path]
    assert not list(original_path.parent.glob("*.tmp"))


def test_bundle_binds_graph_content_but_not_build_timings(fake_models):
    models, _ = fake_models
    snapshot = make_snapshot({"process.py": long_source()})
    first = Search(snapshot).strong_query(QUERY, models)
    timed = snapshot.model_copy(update={"stats": {"elapsed": 2.5}})
    assert Search(timed).strong_query(QUERY, models)["vector_cache"]["state"] == "disk_hit"
    changed_graph = snapshot.model_copy(update={"unresolved": [{"reason": "new unresolved edge"}]})
    changed = Search(changed_graph).strong_query(QUERY, models)
    assert changed["vector_cache"]["state"] == "built"
    assert changed["vector_cache"]["artifact"] != first["vector_cache"]["artifact"]


def test_cli_strong_search_uses_explicit_manifest_and_does_not_degrade(fake_models, monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from reposcope.cli import app

    models, _ = fake_models
    snapshot = make_snapshot({"process.py": long_source()})
    monkeypatch.setattr("reposcope.cli.store", lambda: SimpleNamespace(snapshot=lambda _: snapshot))
    manifest = tmp_path / "models.json"
    manifest.write_text(json.dumps(models))
    runner = CliRunner()
    strong = runner.invoke(app, ["search", "base", QUERY, "--models", str(manifest), "--limit", "2"])
    assert strong.exit_code == 0, strong.output
    assert json.loads(strong.stdout)["vector_cache"]["state"] == "built"
    weak = runner.invoke(app, ["search", "base", QUERY])
    assert weak.exit_code == 0 and isinstance(json.loads(weak.stdout), list)
    rejected = runner.invoke(app, ["search", "base", "query exceeds budget", "--models", str(manifest)])
    assert rejected.exit_code == 1
    assert "query_budget_exceeded" in rejected.output
    assert '"hits"' not in rejected.output


def test_strong_publication_keeps_previous_version_when_next_corpus_is_partial(fake_models, tmp_path):
    from reposcope.config import Settings
    from reposcope.graph.store import Store
    from reposcope.indexing.publication import publish_index, search_published

    models, _ = fake_models
    store = Store(Settings(home=tmp_path / "state"))
    base = make_snapshot({"small.py": 'def small():\n    return "needle"'})
    published = publish_index(store, base, models=models)
    assert published["activated"]
    head = make_snapshot({"small.py": 'def small():\n    value = "' + "x" * 900 + '"'}, snapshot_id="head")
    partial = publish_index(store, head, models=models)
    assert partial["state"] == "partial" and not partial["activated"]
    found = search_published(store, base.repo_id, QUERY, models=models)
    assert found["publication_id"] == published["publication_id"]
    assert found["snapshot_id"] == base.snapshot_id
    assert found["vector_cache"]["state"] == "disk_hit"


def test_published_query_rejects_changed_model_without_reembedding(fake_models, tmp_path):
    from reposcope.config import Settings
    from reposcope.graph.store import Store
    from reposcope.indexing.publication import publish_index, search_published

    models, observed = fake_models
    store = Store(Settings(home=tmp_path / "state"))
    snapshot = make_snapshot({"small.py": 'def small():\n    return "needle"'})
    publish_index(store, snapshot, models=models)
    before = len(observed.embedding_batches)
    (Path(models["embedding"]) / "config.json").write_text('{"changed":true}')
    with pytest.raises(RepoScopeError) as error:
        search_published(store, snapshot.repo_id, QUERY, models=models)
    assert error.value.code == "index_profile_mismatch"
    assert len(observed.embedding_batches) == before


def test_publication_pointer_compare_and_swap_and_profile_isolation(tmp_path):
    from reposcope.config import Settings
    from reposcope.graph.store import Store
    from reposcope.indexing.publication import publish_index, search_published

    store = Store(Settings(home=tmp_path / "state"))
    base = make_snapshot({"small.py": 'def small():\n    return "needle"'})
    first = publish_index(store, base)
    old = store.active_index(base.repo_id, "default")
    head = make_snapshot(base.files, snapshot_id="head")
    second = publish_index(store, head)
    with pytest.raises(RepoScopeError) as error:
        store.activate_index(old, first["publication_id"])
    assert error.value.code == "index_publication_conflict"
    assert search_published(store, base.repo_id, QUERY)["publication_id"] == second["publication_id"]
    with pytest.raises(RepoScopeError) as error:
        search_published(store, base.repo_id, QUERY, profile="another-profile")
    assert error.value.code == "index_not_published"
    incomplete = head.model_copy(update={"status": "partial"})
    with pytest.raises(RepoScopeError):
        publish_index(store, incomplete)
    assert store.active_index(base.repo_id, "default")["publication_id"] == second["publication_id"]


def test_new_snapshot_reuses_unchanged_content_vectors_and_matches_full_build(fake_models, tmp_path):
    models, _ = fake_models
    files = {"process.py": long_source(), "small.py": 'def small():\n    return "needle"'}
    base = make_snapshot(files)
    Search(base).strong_query(QUERY, models)
    head = make_snapshot({**files, "small.py": 'def small():\n    return "needle changed"'}, snapshot_id="head")
    incremental = Search(head).strong_query(QUERY, models)
    assert incremental["vector_cache"]["reused_embedding_chunks"] > 0
    assert incremental["vector_cache"]["encoded_embedding_chunks"] == 1
    full = Search(head).strong_query(QUERY, {**models, "cache_dir": str(tmp_path / "full")})
    assert full["vector_cache"]["reused_embedding_chunks"] == 0
    assert full["hits"] == incremental["hits"]
    with np.load(incremental["vector_cache"]["artifact"], allow_pickle=False) as a:
        with np.load(full["vector_cache"]["artifact"], allow_pickle=False) as b:
            assert np.array_equal(a["vectors"], b["vectors"])


def test_corrupt_content_embedding_is_not_reused(fake_models):
    import sqlite3

    models, _ = fake_models
    base = make_snapshot({"process.py": long_source()})
    Search(base).strong_query(QUERY, models)
    with sqlite3.connect(Path(models["cache_dir"]) / "embedding-rows.sqlite3") as db:
        db.execute("UPDATE embeddings SET checksum='corrupt'")
    with pytest.raises(RepoScopeError) as error:
        Search(make_snapshot(base.files, snapshot_id="head")).strong_query(QUERY, models)
    assert error.value.code == "vector_cache_invalid"


@pytest.mark.parametrize("change", ["delete", "rename", "signature", "insert"])
def test_content_reuse_matches_full_for_structural_file_changes(fake_models, tmp_path, change):
    models, _ = fake_models
    files = {"alpha.py": 'def alpha():\n    return "needle"', "beta.py": "def beta():\n    return 2"}
    Search(make_snapshot(files)).strong_query(QUERY, models)
    head_files = dict(files)
    if change == "delete":
        del head_files["alpha.py"]
    elif change == "rename":
        head_files["renamed.py"] = head_files.pop("alpha.py")
    elif change == "signature":
        head_files["alpha.py"] = 'def alpha(value=1):\n    return "needle"'
    else:
        head_files["gamma.py"] = 'def gamma():\n    return "needle extra"'
    head = make_snapshot(head_files, snapshot_id="head")
    reused = Search(head).strong_query(QUERY, models)
    full = Search(head).strong_query(QUERY, {**models, "cache_dir": str(tmp_path / "full")})
    assert reused["vector_cache"]["reused_embedding_chunks"] > 0
    assert reused["hits"] == full["hits"]
    assert all(hit["symbol"]["snapshot_id"] == "head" for hit in reused["hits"])
