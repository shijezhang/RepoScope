"""Snapshot-scoped identifier/BM25 search; explicit optional strong baseline."""

import importlib.metadata
import os
import re
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi

from reposcope.config import RepoScopeError
from reposcope.models import digest
from reposcope.retrieval.corpus import chunk_evidence, limits, prepare_corpus, token_count
from reposcope.retrieval.model_files import verify_model_files
from reposcope.retrieval.vector_cache import VectorCache


def tokenize(text):
    identifiers = re.findall(r"[A-Za-z_][A-Za-z_0-9]*|[\u4e00-\u9fff]+", text)
    words = []
    for word in identifiers:
        words.append(word.lower())
        words.extend(re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", word).lower().replace("_", " ").split())
    return words


def rrf(rankings, k=60):
    scores, sources = defaultdict(float), defaultdict(list)
    for source, ids in rankings.items():
        for rank, sid in enumerate(dict.fromkeys(ids), 1):
            scores[sid] += 1 / (k + rank)
            sources[sid].append(source)
    return [(sid, scores[sid], sources[sid]) for sid in sorted(scores, key=lambda s: (-scores[s], s))]


class Search:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self._strong_runtime = None
        self.symbols = snapshot.symbols
        self.texts = [
            f"{s.path} {s.module} {s.qualname} {s.signature}\n"
            + "\n".join(snapshot.files[s.path].splitlines()[s.start - 1 : s.end])
            for s in self.symbols
        ]
        self.bm25 = BM25Okapi([tokenize(t) or ["_"] for t in self.texts]) if self.symbols else None

    def query(self, query, limit=20):
        if not self.symbols:
            return []
        scores = self.bm25.get_scores(tokenize(query))
        exact = [
            s.symbol_id
            for s in self.symbols
            if query.lower() in {s.qualname.lower(), s.path.lower(), f"{s.module}.{s.qualname}".lower()}
        ]
        order = sorted(range(len(scores)), key=lambda i: (-scores[i], self.symbols[i].symbol_id))
        sparse = [self.symbols[i].symbol_id for i in order if scores[i] > 0][: max(limit * 3, 20)]
        mapping = {s.symbol_id: s for s in self.symbols}
        return [
            {"symbol": mapping[sid].model_dump(), "score": score, "sources": sources}
            for sid, score, sources in rrf({"identifier": exact, "bm25": sparse})[:limit]
        ]

    def strong_query(self, query, models, limit=20):
        """Pinned local Dense + sparse + RRF + reranker; never silently degrades."""
        started = time.perf_counter()
        if self.snapshot.status != "ready":
            raise RepoScopeError("snapshot_not_ready", "Strong retrieval requires a complete source snapshot")
        if not models.get("embedding_revision") or not models.get("reranker_revision"):
            raise RepoScopeError("model_unavailable", "B1 requires frozen embedding and reranker revisions")
        if limit < 1:
            raise RepoScopeError("invalid_limit", "Search limit must be positive")
        try:
            from sentence_transformers import CrossEncoder, SentenceTransformer
        except ImportError as exc:
            raise RepoScopeError("model_unavailable", "Install the models extra to run B1") from exc
        # Recheck bytes even on a warm query: a revision string cannot detect
        # an in-place tokenizer/config replacement in the local directory.
        verification_started = time.perf_counter()
        model_files = verify_model_files(models)
        verification_seconds = time.perf_counter() - verification_started
        model_key = digest({"models": models, "local_files": model_files})
        model_reused = self._strong_runtime is not None and self._strong_runtime["key"] == model_key
        load_started = time.perf_counter()
        if not model_reused:
            try:
                embedding = SentenceTransformer(
                    models["embedding"],
                    revision=models["embedding_revision"],
                    local_files_only=True,
                    device=models.get("device", "cpu"),
                )
                reranker = CrossEncoder(
                    models["reranker"],
                    revision=models["reranker_revision"],
                    local_files_only=True,
                    device=models.get("device", "cpu"),
                )
            except (OSError, ValueError, RuntimeError) as exc:
                raise RepoScopeError("model_unavailable", f"Pinned local models could not load: {exc}") from exc
            self._strong_runtime = {"key": model_key, "embedding": embedding, "reranker": reranker}
        runtime = self._strong_runtime
        embedding, reranker = runtime["embedding"], runtime["reranker"]
        model_load_seconds = time.perf_counter() - load_started
        if not self.symbols:
            return {"hits": [], "seconds": time.perf_counter() - started, "models": models, "state": "ready"}
        budget = limits(embedding, reranker, models)
        if (
            token_count(embedding.tokenizer, query) > budget["embedding_max_tokens"]
            or token_count(reranker.tokenizer, query, special=False) > budget["query_max_tokens"]
        ):
            raise RepoScopeError(
                "query_budget_exceeded", "Query exceeds the declared model/token reserve; it was not truncated"
            )
        corpus_started = time.perf_counter()
        if "corpus" not in runtime:
            runtime["corpus"] = prepare_corpus(
                self.snapshot, embedding, reranker, budget, tokenize, models.get("chunk_overlap_lines", 2)
            )
        corpus = runtime["corpus"]
        corpus_seconds = time.perf_counter() - corpus_started
        chunks, texts, groups = corpus["chunks"], corpus["texts"], corpus["groups"]
        if not chunks:
            raise RepoScopeError(
                "retrieval_corpus_unavailable", "No complete source chunk fits the pinned model budgets"
            )
        binding = {
            "snapshot_id": self.snapshot.snapshot_id,
            "manifest_hash": self.snapshot.manifest_hash,
            "parser_version": self.snapshot.parser_version,
            "chunk_text_hash": digest(texts),
            "chunk_ids": [chunk.chunk_id for chunk in chunks],
            "parent_symbol_ids": [chunk.parent_symbol_id for chunk in chunks],
            "corpus_state": corpus["stats"]["state"],
            "oversized_hash": digest(corpus["stats"]["oversized"]),
            "chunk_policy": {**budget, "overlap_lines": models.get("chunk_overlap_lines", 2)},
            "embedding": models.get("embedding_repository", models["embedding"]),
            "embedding_revision": models["embedding_revision"],
            "reranker": models.get("reranker_repository", models["reranker"]),
            "reranker_revision": models["reranker_revision"],
            "embedding_weights_sha256": models.get("embedding_weights_sha256"),
            "reranker_weights_sha256": models.get("reranker_weights_sha256"),
            "model_files": model_files,
            "sentence_transformers": importlib.metadata.version("sentence-transformers"),
            "transformers": importlib.metadata.version("transformers"),
            "torch": importlib.metadata.version("torch"),
            "device": models.get("device", "cpu"),
            "vector_format": "normalized-float32-chunks-v2",
        }
        cache = VectorCache(models.get("cache_dir", Path(os.getenv("REPOSCOPE_HOME", "artifacts/state")) / "vectors"))
        index_started = time.perf_counter()
        if "vectors" in runtime:
            vectors, cache_state = runtime["vectors"], "memory_hit"
        else:
            cached = cache.load(binding, len(chunks))
            if cached is None:
                vectors = embedding.encode(
                    texts,
                    normalize_embeddings=True,
                    batch_size=models.get("batch_size", 16),
                    show_progress_bar=False,
                )
                cache_state = "built"
            else:
                vectors, _ = cached
                cache_state = "disk_hit"
        vectors = np.asarray(vectors, dtype=np.float32)
        if vectors.ndim != 2 or vectors.shape[0] != len(chunks) or not np.isfinite(vectors).all():
            raise RepoScopeError("vector_cache_invalid", "Encoded chunk vectors have invalid shape or values")
        index_seconds = time.perf_counter() - index_started
        query_started = time.perf_counter()
        q = embedding.encode([query], normalize_embeddings=True, show_progress_bar=False)[0]
        dense = vectors @ q
        pool = max(30, limit * 3)
        dense_best = {
            parent: max(indices, key=lambda i: (float(dense[i]), chunks[i].chunk_id))
            for parent, indices in groups.items()
        }
        ids = sorted(dense_best, key=lambda parent: (-float(dense[dense_best[parent]]), parent))[:pool]
        sparse_hits = self.query(query, pool)
        skipped_candidates = [
            hit["symbol"]["symbol_id"] for hit in sparse_hits if hit["symbol"]["symbol_id"] not in groups
        ]
        sparse = [hit["symbol"]["symbol_id"] for hit in sparse_hits if hit["symbol"]["symbol_id"] in groups]
        fused = rrf({"dense": ids, "bm25_identifier": sparse})[:pool]
        chunk_sparse = corpus["bm25"].get_scores(tokenize(query))
        pairs, pair_meta = [], []
        for parent, _, sources in fused:
            lexical = max(groups[parent], key=lambda i: (float(chunk_sparse[i]), chunks[i].chunk_id))
            selected = [dense_best[parent]]
            if lexical not in selected and chunk_sparse[lexical] > 0:
                selected.append(lexical)
            for index in selected:
                pair_tokens = len(
                    reranker.tokenizer(query, texts[index], add_special_tokens=True, truncation=False)["input_ids"]
                )
                if pair_tokens > budget["reranker_max_tokens"]:
                    raise RepoScopeError(
                        "retrieval_budget_mismatch",
                        "Query/chunk pair exceeds reranker budget; refusing silent truncation",
                    )
                pairs.append((query, texts[index]))
                pair_meta.append((parent, index, sources, pair_tokens))
        scores = reranker.predict(pairs, batch_size=models.get("reranker_batch_size", 8), show_progress_bar=False)
        if len(scores) != len(pair_meta) or not np.isfinite(scores).all():
            raise RepoScopeError("reranker_invalid", "Reranker did not return one finite score per complete pair")
        symbols = {symbol.symbol_id: symbol for symbol in self.symbols}
        best = {}
        for (parent, index, sources, pair_tokens), score in zip(pair_meta, scores):
            hit = {
                "symbol": symbols[parent].model_dump(),
                "score": float(score),
                "sources": sources + ["reranker"],
                "chunk": chunk_evidence(chunks[index]),
                "reranker_pair_tokens": pair_tokens,
            }
            if parent not in best or (hit["score"], hit["chunk"]["chunk_id"]) > (
                best[parent]["score"],
                best[parent]["chunk"]["chunk_id"],
            ):
                best[parent] = hit
        hits = list(best.values())
        query_seconds = time.perf_counter() - query_started
        # Publish only after both embedding inference and reranking actually
        # succeed. Manifest and vectors become visible in one atomic replace.
        if cache_state == "built":
            cache.publish(binding, vectors)
        runtime["vectors"] = vectors
        return {
            "hits": sorted(hits, key=lambda h: (-h["score"], h["symbol"]["symbol_id"]))[:limit],
            "seconds": time.perf_counter() - started,
            "models": models,
            "state": corpus["stats"]["state"],
            "corpus": corpus["stats"],
            "skipped_unrankable_candidates": skipped_candidates,
            "timings": {
                "model_verification_seconds": verification_seconds,
                "model_load_seconds": model_load_seconds,
                "index_seconds": index_seconds,
                "corpus_seconds": corpus_seconds,
                "query_seconds": query_seconds,
            },
            "model_memory_reused": model_reused,
            "vector_cache": {
                "state": cache_state,
                "artifact": str(cache.path(binding)),
                "snapshot_id": self.snapshot.snapshot_id,
                "update_mode": "full-embedding-per-snapshot",
            },
            "limits": {
                **budget,
                "reranker_pairs": len(pairs),
                "candidate_pool": pool,
                "score_kind": "reranker output; not calibrated confidence",
            },
        }
