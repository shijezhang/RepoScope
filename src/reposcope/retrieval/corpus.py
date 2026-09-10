"""Model-budgeted corpus derived from immutable symbols; no implicit truncation."""

from collections import defaultdict
from dataclasses import asdict

from rank_bm25 import BM25Okapi

from reposcope.config import RepoScopeError
from reposcope.indexing.chunks import CHUNK_VERSION, chunk_symbol


def token_count(tokenizer, text, *, special=True):
    return len(tokenizer.encode(text, add_special_tokens=special, truncation=False))


def limits(embedding, reranker, models):
    embedding_limit = int(embedding.max_seq_length)
    reranker_limit = int(reranker.max_length or reranker.tokenizer.model_max_length)
    query_limit = models.get("query_max_tokens", 64)
    if type(query_limit) is not int or not 1 <= query_limit <= 4096:
        raise RepoScopeError("invalid_limit", "query_max_tokens must be a positive bounded integer")
    if not 1 <= embedding_limit <= 32768 or not 1 <= reranker_limit <= 32768:
        raise RepoScopeError("invalid_limit", "Models must expose finite supported context limits")
    specials = reranker.tokenizer.num_special_tokens_to_add(pair=True)
    document_limit = min(embedding_limit, reranker_limit - query_limit - specials)
    if document_limit < 1:
        raise RepoScopeError("invalid_limit", "Query reserve leaves no room for document chunks")
    return {
        "embedding_max_tokens": embedding_limit,
        "reranker_max_tokens": reranker_limit,
        "query_max_tokens": query_limit,
        "document_max_tokens": document_limit,
        "reranker_pair_special_tokens": specials,
        "chunk_version": CHUNK_VERSION,
    }


def prepare_corpus(snapshot, embedding, reranker, budget, tokenize, overlap_lines=2):
    def count(text):
        return max(token_count(embedding.tokenizer, text), token_count(reranker.tokenizer, text, special=False))

    chunks, skipped, groups = [], [], defaultdict(list)
    for symbol in snapshot.symbols:
        if symbol.snapshot_id != snapshot.snapshot_id:
            raise RepoScopeError("snapshot_mismatch", "Symbol belongs to another snapshot")
        for chunk in chunk_symbol(
            symbol, snapshot.files[symbol.path], count, budget["document_max_tokens"], overlap_lines=overlap_lines
        ):
            if chunk.status == "ready":
                groups[symbol.symbol_id].append(len(chunks))
                chunks.append(chunk)
            else:
                skipped.append(
                    {
                        "chunk_id": chunk.chunk_id,
                        "parent_symbol_id": symbol.symbol_id,
                        "path": chunk.path,
                        "start": chunk.start,
                        "end": chunk.end,
                        "token_count": chunk.token_count,
                        "limitations": list(chunk.limitations),
                    }
                )
    texts = [chunk.text for chunk in chunks]
    sparse = BM25Okapi([tokenize(text) or ["_"] for text in texts]) if chunks else None
    affected = {row["parent_symbol_id"] for row in skipped}
    stats = {
        "state": "partial" if skipped else "ready",
        "symbols_total": len(snapshot.symbols),
        "parents_with_ready_chunks": len(groups),
        "fully_indexed_parents": len(groups.keys() - affected),
        "ready_chunks": len(chunks),
        "oversized_chunks": len(skipped),
        "oversized": skipped,
        "chunk_version": CHUNK_VERSION,
        "overlap_lines": overlap_lines,
    }
    return {"chunks": chunks, "texts": texts, "groups": groups, "bm25": sparse, "stats": stats}


def chunk_evidence(chunk):
    data = asdict(chunk)
    return {
        key: data[key]
        for key in (
            "chunk_id",
            "parent_symbol_id",
            "snapshot_id",
            "path",
            "start",
            "end",
            "content_hash",
            "parent_content_hash",
            "source",
            "signature",
            "declaration",
            "token_count",
        )
    }
