"""Deterministic, line-preserving symbol chunks with caller-supplied token counts.

``source`` is the complete file from the symbol's snapshot, not a snippet. Line
separators are normalized exactly as in the parser's content hash; indentation,
line contents and complete declaration lines are never character-truncated.
The callback must count the entire provided string without tokenizer truncation,
including any model special-token overhead. Budget guarantees apply to ``text``.
"""

import io
import tokenize
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from reposcope.config import RepoScopeError
from reposcope.models import Symbol, digest

CHUNK_VERSION = "symbol-lines-v1"
MAX_OVERLAP_LINES = 8
DEFINITION_KINDS = frozenset({"Function", "Method", "TestCase", "Class"})


@dataclass(frozen=True)
class SymbolChunk:
    chunk_id: str
    snapshot_id: str
    parent_symbol_id: str
    parent_content_hash: str
    path: str
    start: int
    end: int
    content_hash: str
    source: str
    context: str
    signature: str
    declaration: str
    token_count: int
    max_tokens: int
    status: Literal["ready", "oversized"]
    partial: bool
    limitations: tuple[str, ...]
    oversized_lines: tuple[int, ...]
    index: int

    @property
    def text(self) -> str:
        """The exact text measured by the injected counter; feed this to retrieval."""
        return self.context + "\n\n" + self.source


def _declaration(symbol: Symbol, lines: list[str]) -> str:
    if symbol.kind not in DEFINITION_KINDS:
        return ""
    # Stop at the declaration colon, retaining its WHOLE physical line. Decorators
    # and multiline annotations/defaults remain present in every chunk's context.
    found, depth = False, 0
    try:
        for token in tokenize.generate_tokens(io.StringIO("\n".join(lines)).readline):
            if not found:
                if token.type == tokenize.NAME and token.string in {"def", "class"}:
                    found = True
                continue
            if token.type != tokenize.OP:
                continue
            if token.string in "([{":
                depth += 1
            elif token.string in ")]}":
                depth -= 1
            elif token.string == ":" and depth == 0:
                return "\n".join(lines[: token.end[0]])
    except (tokenize.TokenError, IndentationError, SyntaxError) as exc:
        raise RepoScopeError("invalid_symbol_source", "Cannot preserve the complete symbol declaration") from exc
    raise RepoScopeError("invalid_symbol_source", "Definition symbol has no complete declaration")


def chunk_symbol(
    symbol: Symbol,
    source: str,
    count_tokens: Callable[[str], int],
    max_tokens: int,
    *,
    overlap_lines: int = 0,
) -> list[SymbolChunk]:
    """Split one hash-verified symbol, retaining all of its original source lines.

    Ready chunks fit ``count_tokens(chunk.text) <= max_tokens``. An oversized
    physical line is emitted intact and explicitly marked. If the immutable
    context itself exceeds the budget, a single oversized chunk retains the
    complete symbol instead of repeating an unrepresentable signature per line.
    ``partial`` means the retrieval budget could not be satisfied, never that
    source characters were dropped. Overlap is optional, at most eight lines,
    and is reduced whenever needed to fit at least one new source line.

    IDs bind snapshot, parent symbol, source range and exact content/context.
    They deliberately exclude callback identity, token counts and budget policy.
    """
    if type(max_tokens) is not int or max_tokens < 1:
        raise RepoScopeError("invalid_chunk_budget", "max_tokens must be a positive integer")
    if type(overlap_lines) is not int or not 0 <= overlap_lines <= MAX_OVERLAP_LINES:
        raise RepoScopeError("invalid_chunk_budget", f"overlap_lines must be between 0 and {MAX_OVERLAP_LINES}")
    if not callable(count_tokens):
        raise RepoScopeError("invalid_token_counter", "A complete-text token counter is required")
    if not isinstance(source, str):
        raise RepoScopeError("invalid_symbol_source", "source must contain the complete file text")
    if not symbol.snapshot_id or not symbol.symbol_id or not symbol.path:
        raise RepoScopeError("invalid_symbol_source", "Symbol is missing its snapshot, parent identity or path")
    file_lines = source.splitlines()
    # The current parser represents an empty module using the logical range 1:1.
    if not file_lines and symbol.kind == "Module" and symbol.start == symbol.end == 1:
        file_lines = [""]
    if symbol.start < 1 or symbol.end < symbol.start or symbol.end > len(file_lines):
        raise RepoScopeError("invalid_symbol_source", "Symbol range is outside the supplied source file")
    lines = file_lines[symbol.start - 1 : symbol.end]
    canonical = "\n".join(lines)
    if digest(canonical) != symbol.content_hash:
        raise RepoScopeError("snapshot_mismatch", "Symbol content hash does not match the supplied source range")

    declaration = _declaration(symbol, lines)
    context = (
        f"path: {symbol.path}\nmodule: {symbol.module}\nsymbol: {symbol.qualname or '<module>'}\n"
        f"kind: {symbol.kind}\nsignature:\n{symbol.signature}\ndeclaration:\n{declaration}"
    )

    def count(text):
        value = count_tokens(text)
        if type(value) is not int or value < 0:
            raise RepoScopeError("invalid_token_counter", "Token counter must return a non-negative integer")
        return value

    def measure(start, end):
        return count(context + "\n\n" + "\n".join(lines[start:end]))

    chunks = []

    def emit(start, end, tokens, limitations=(), oversized_lines=()):
        body = "\n".join(lines[start:end])
        absolute_start, absolute_end = symbol.start + start, symbol.start + end - 1
        content_hash = digest(body)
        identity = {
            "version": CHUNK_VERSION,
            "snapshot_id": symbol.snapshot_id,
            "parent_symbol_id": symbol.symbol_id,
            "parent_content_hash": symbol.content_hash,
            "path": symbol.path,
            "start": absolute_start,
            "end": absolute_end,
            "content_hash": content_hash,
            "context_hash": digest(context),
        }
        oversized = tokens > max_tokens
        chunks.append(
            SymbolChunk(
                chunk_id=digest(identity),
                snapshot_id=symbol.snapshot_id,
                parent_symbol_id=symbol.symbol_id,
                parent_content_hash=symbol.content_hash,
                path=symbol.path,
                start=absolute_start,
                end=absolute_end,
                content_hash=content_hash,
                source=body,
                context=context,
                signature=symbol.signature,
                declaration=declaration,
                token_count=tokens,
                max_tokens=max_tokens,
                status="oversized" if oversized else "ready",
                partial=oversized,
                limitations=tuple(limitations),
                oversized_lines=tuple(oversized_lines),
                index=len(chunks),
            )
        )

    total_tokens = measure(0, len(lines))
    if total_tokens <= max_tokens:
        emit(0, len(lines), total_tokens)
        return chunks
    if count(context + "\n\n") > max_tokens:
        reasons = ["context_exceeds_budget"]
        if count(symbol.signature) > max_tokens:
            reasons.append("signature_exceeds_budget")
        if count(declaration) > max_tokens:
            reasons.append("declaration_exceeds_budget")
        oversized_lines = tuple(symbol.start + index for index, line in enumerate(lines) if count(line) > max_tokens)
        if oversized_lines:
            reasons.append("single_line_exceeds_budget")
        emit(0, len(lines), total_tokens, reasons, oversized_lines)
        return chunks

    next_line = 0
    while next_line < len(lines):
        start = max(0, next_line - overlap_lines)
        end = next_line + 1
        tokens = measure(start, end)
        while start < next_line and tokens > max_tokens:
            start += 1
            tokens = measure(start, end)
        if tokens > max_tokens:
            reason = (
                "single_line_exceeds_budget"
                if count(lines[next_line]) > max_tokens
                else "context_and_line_exceed_budget"
            )
            emit(start, end, tokens, (reason,), (symbol.start + next_line,))
        else:
            while end < len(lines):
                candidate_tokens = measure(start, end + 1)
                if candidate_tokens > max_tokens:
                    break
                end += 1
                tokens = candidate_tokens
            emit(start, end, tokens)
        next_line = end
    return chunks
