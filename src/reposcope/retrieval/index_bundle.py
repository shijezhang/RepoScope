"""An immutable strong-index bundle; publication never switches the graph store."""

from collections import defaultdict
from dataclasses import asdict

from rank_bm25 import BM25Okapi

from reposcope.config import RepoScopeError
from reposcope.indexing.chunks import CHUNK_VERSION, SymbolChunk, chunk_symbol
from reposcope.models import digest
from reposcope.retrieval.vector_cache import VectorCache

BUNDLE_VERSION = "snapshot-corpus-vectors-v1"


def snapshot_content(snapshot):
    # Build timings/cache hit statistics are not semantic snapshot contents.
    return snapshot.model_dump(exclude={"stats"})


class IndexBundle(VectorCache):
    def load_corpus(self, binding, snapshot, budget, tokenize):
        cached = self.load(binding)
        if cached is None:
            return None
        vectors, manifest = cached
        try:
            components = manifest["components"]
            if components["snapshot"] != snapshot_content(snapshot):
                raise ValueError("Stored graph/source snapshot differs from the query snapshot")
            chunks = []
            groups = defaultdict(list)
            symbols = {symbol.symbol_id: symbol for symbol in snapshot.symbols}
            contexts = {}
            for raw in components["chunks"]:
                chunk = SymbolChunk(**raw)
                parent = symbols[chunk.parent_symbol_id]
                if parent.symbol_id not in contexts:
                    # Reconstruct exact declaration/context without rerunning the
                    # model tokenizers or line-packing loop.
                    contexts[parent.symbol_id] = chunk_symbol(parent, snapshot.files[parent.path], lambda _: 0, 1)[0]
                expected = contexts[parent.symbol_id]
                source = "\n".join(snapshot.files[chunk.path].splitlines()[chunk.start - 1 : chunk.end])
                identity = {
                    "version": CHUNK_VERSION,
                    "snapshot_id": chunk.snapshot_id,
                    "parent_symbol_id": chunk.parent_symbol_id,
                    "parent_content_hash": chunk.parent_content_hash,
                    "path": chunk.path,
                    "start": chunk.start,
                    "end": chunk.end,
                    "content_hash": chunk.content_hash,
                    "context_hash": digest(chunk.context),
                }
                if not (
                    chunk.snapshot_id == parent.snapshot_id == snapshot.snapshot_id
                    and chunk.path == parent.path
                    and parent.start <= chunk.start <= chunk.end <= parent.end
                    and chunk.parent_content_hash == parent.content_hash
                    and chunk.source == source
                    and chunk.content_hash == digest(source)
                    and chunk.chunk_id == digest(identity)
                    and chunk.context == expected.context
                    and chunk.signature == expected.signature
                    and chunk.declaration == expected.declaration
                    and chunk.status == "ready"
                    and 0 < chunk.token_count <= chunk.max_tokens == budget["document_max_tokens"]
                ):
                    raise ValueError("Chunk evidence or token budget differs from its bound snapshot")
                groups[chunk.parent_symbol_id].append(len(chunks))
                chunks.append(chunk)
            if len({chunk.chunk_id for chunk in chunks}) != len(chunks) or len(chunks) != vectors.shape[0]:
                raise ValueError("Chunk identity/count does not match vector rows")
            texts = [chunk.text for chunk in chunks]
            terms = [tokenize(text) or ["_"] for text in texts]
            if terms != components["sparse_terms"]:
                raise ValueError("Sparse terms differ from the bound chunk texts")
            stats = components["corpus_stats"]
            if stats["ready_chunks"] != len(chunks) or stats["symbols_total"] != len(symbols):
                raise ValueError("Corpus counts do not match the snapshot")
            corpus = {
                "chunks": chunks,
                "texts": texts,
                "groups": groups,
                "bm25": BM25Okapi(terms) if chunks else None,
                "stats": stats,
            }
            return corpus, vectors
        except (KeyError, TypeError, ValueError) as exc:
            raise RepoScopeError("vector_cache_invalid", f"Invalid strong-index bundle: {exc}") from exc

    def publish_corpus(self, binding, snapshot, corpus, vectors, tokenize):
        components = {
            "snapshot": snapshot_content(snapshot),
            "chunks": [asdict(chunk) for chunk in corpus["chunks"]],
            "sparse_terms": [tokenize(text) or ["_"] for text in corpus["texts"]],
            "corpus_stats": corpus["stats"],
        }
        if vectors.shape[0] != len(components["chunks"]):
            raise RepoScopeError("vector_cache_invalid", "Cannot publish mismatched corpus/vector rows")
        return self.publish(binding, vectors, components, immutable=True)
