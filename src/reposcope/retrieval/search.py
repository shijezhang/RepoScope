"""Snapshot-scoped identifier/BM25 search; explicit optional strong baseline."""

import re
import time
from collections import defaultdict

from rank_bm25 import BM25Okapi

from reposcope.config import RepoScopeError


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
        """No silent fallback: both pinned embedding and reranker are required for B1."""
        if not models.get("embedding_revision") or not models.get("reranker_revision"):
            raise RepoScopeError("model_unavailable", "B1 requires frozen embedding and reranker revisions")
        try:
            from sentence_transformers import CrossEncoder, SentenceTransformer
        except ImportError as exc:
            raise RepoScopeError("model_unavailable", "Install the models extra to run B1") from exc
        started = time.perf_counter()
        embedding = SentenceTransformer(
            models["embedding"], revision=models["embedding_revision"], local_files_only=True
        )
        reranker = CrossEncoder(models["reranker"], revision=models["reranker_revision"], local_files_only=True)
        if not self.symbols:
            return {"hits": [], "seconds": time.perf_counter() - started, "models": models}
        vectors = embedding.encode(self.texts, normalize_embeddings=True)
        q = embedding.encode([query], normalize_embeddings=True)[0]
        dense = vectors @ q
        pool = max(30, limit * 3)
        ids = [self.symbols[i].symbol_id for i in sorted(range(len(dense)), key=lambda i: (-float(dense[i]), i))[:pool]]
        sparse = [h["symbol"]["symbol_id"] for h in self.query(query, pool)]
        fused = rrf({"dense": ids, "bm25_identifier": sparse})[:pool]
        by_id = {s.symbol_id: (s, text) for s, text in zip(self.symbols, self.texts)}
        scores = reranker.predict([(query, by_id[sid][1]) for sid, _, _ in fused])
        hits = [
            {"symbol": by_id[sid][0].model_dump(), "score": float(score), "sources": sources + ["reranker"]}
            for (sid, _, sources), score in zip(fused, scores)
        ]
        return {
            "hits": sorted(hits, key=lambda h: (-h["score"], h["symbol"]["symbol_id"]))[:limit],
            "seconds": time.perf_counter() - started,
            "models": models,
        }
