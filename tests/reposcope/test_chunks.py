from dataclasses import FrozenInstanceError

import pytest

from reposcope.config import RepoScopeError
from reposcope.indexing.chunks import MAX_OVERLAP_LINES, chunk_symbol
from reposcope.models import Symbol, digest


def make_symbol(source, *, start=1, end=None, kind="Module", signature="", snapshot="base", parent="parent"):
    lines = source.splitlines()
    end = end if end is not None else max(1, len(lines))
    return Symbol(
        symbol_id=parent,
        snapshot_id=snapshot,
        path="package/example.py",
        module="package.example",
        qualname="compute" if kind != "Module" else "",
        kind=kind,
        start=start,
        end=end,
        signature=signature,
        content_hash=digest("\n".join(lines[start - 1 : end])),
    )


def assert_complete(chunks, symbol, source, counter, budget):
    expected = source.splitlines() or [""]
    covered = set()
    for chunk in chunks:
        assert chunk.snapshot_id == symbol.snapshot_id
        assert chunk.parent_symbol_id == symbol.symbol_id
        assert chunk.parent_content_hash == symbol.content_hash
        assert chunk.path == symbol.path
        assert symbol.start <= chunk.start <= chunk.end <= symbol.end
        assert chunk.source == "\n".join(expected[chunk.start - 1 : chunk.end])
        assert chunk.content_hash == digest(chunk.source)
        assert chunk.token_count == counter(chunk.text)
        covered.update(range(chunk.start, chunk.end + 1))
        if chunk.status == "ready":
            assert chunk.token_count <= budget
            assert chunk.partial is False
            assert not chunk.limitations
        else:
            assert chunk.token_count > budget
            assert chunk.partial is True
            assert chunk.limitations
    assert covered == set(range(symbol.start, symbol.end + 1))
    assert len({chunk.chunk_id for chunk in chunks}) == len(chunks)
    assert [chunk.index for chunk in chunks] == list(range(len(chunks)))


def test_packs_complete_lines_without_leaking_outside_symbol():
    source = (
        "outside_before\n\ndef compute():\n    first = 1\n\n    second = 2\n    return first + second\noutside_after\n"
    )
    symbol = make_symbol(source, start=3, end=7, kind="Function")
    full = chunk_symbol(symbol, source, len, 10_000)[0]
    budget = len(full.context) + 2 + 32
    chunks = chunk_symbol(symbol, source, len, budget)
    assert len(chunks) > 1
    assert all(chunk.status == "ready" for chunk in chunks)
    assert all("outside_before" not in chunk.text and "outside_after" not in chunk.text for chunk in chunks)
    assert_complete(chunks, symbol, source, len, budget)
    assert all(left.end + 1 == right.start for left, right in zip(chunks, chunks[1:]))


def test_multiline_signature_decorators_and_identifiers_survive_every_chunk():
    source = (
        "@decorate(\n    mode='safe',\n)\n"
        "async def compute(\n    value: tuple[int, str],\n    *, limit: int = 4,\n) -> dict[str, int]:\n"
        + "\n".join(f"    item_{i} = {i}" for i in range(12))
        + "\n    return {'total': limit}\n"
    )
    signature = "value: tuple[int, str], *, limit: int=4"
    symbol = make_symbol(source, kind="Method", signature=signature)
    full = chunk_symbol(symbol, source, len, 100_000)[0]
    declaration = "\n".join(source.splitlines()[:7])
    budget = len(full.context) + 2 + 65
    chunks = chunk_symbol(symbol, source, len, budget, overlap_lines=1)
    assert len(chunks) > 2
    for chunk in chunks:
        assert chunk.signature == signature
        assert chunk.declaration == declaration
        assert declaration in chunk.context
        assert symbol.path in chunk.context and symbol.module in chunk.context and symbol.qualname in chunk.context
    assert_complete(chunks, symbol, source, len, budget)


def test_ids_are_content_bound_and_independent_of_counter_identity():
    source = "\n".join(f"value_{i} = {i}" for i in range(20))
    symbol = make_symbol(source)

    def another_counter(text):
        return len(text)

    first = chunk_symbol(symbol, source, len, 160)
    second = chunk_symbol(symbol, source, another_counter, 160)
    assert [chunk.chunk_id for chunk in first] == [chunk.chunk_id for chunk in second]
    head = symbol.model_copy(update={"snapshot_id": "head"})
    other_snapshot = chunk_symbol(head, source, len, 160)
    assert {chunk.chunk_id for chunk in first}.isdisjoint(chunk.chunk_id for chunk in other_snapshot)
    assert all(chunk.snapshot_id == "base" for chunk in first)
    assert all(chunk.snapshot_id == "head" for chunk in other_snapshot)
    renamed_parent = chunk_symbol(symbol.model_copy(update={"symbol_id": "another-parent"}), source, len, 160)
    assert {chunk.chunk_id for chunk in first}.isdisjoint(chunk.chunk_id for chunk in renamed_parent)
    # The same text/range is the same content identity even with another budget.
    assert (
        chunk_symbol(symbol, source, len, 10_000)[0].chunk_id == chunk_symbol(symbol, source, len, 20_000)[0].chunk_id
    )
    with pytest.raises(FrozenInstanceError):
        first[0].snapshot_id = "wrong"


def test_hash_mismatch_refuses_head_text_under_base_identity_before_counting():
    source = "def compute():\n    return 1\n"
    symbol = make_symbol(source, kind="Function")
    counted = []
    with pytest.raises(RepoScopeError) as failure:
        chunk_symbol(symbol, source.replace("return 1", "return 2"), lambda text: counted.append(text) or 1, 100)
    assert failure.value.code == "snapshot_mismatch"
    assert not counted


@pytest.mark.parametrize("start,end", [(0, 1), (2, 1), (1, 3)])
def test_invalid_ranges_are_not_silently_clipped(start, end):
    source = "first\nsecond"
    symbol = make_symbol(source).model_copy(update={"start": start, "end": end})
    with pytest.raises(RepoScopeError) as failure:
        chunk_symbol(symbol, source, len, 100)
    assert failure.value.code == "invalid_symbol_source"


def test_oversized_line_is_intact_and_following_lines_are_not_lost():
    huge = "data = '" + "x" * 600 + "'"
    source = "before = 1\n" + huge + "\nafter = 2\nlast = 3"
    symbol = make_symbol(source)
    full = chunk_symbol(symbol, source, len, 10_000)[0]
    budget = len(full.context) + 2 + 30
    chunks = chunk_symbol(symbol, source, len, budget, overlap_lines=2)
    oversized = [chunk for chunk in chunks if chunk.status == "oversized"]
    assert len(oversized) == 1
    assert oversized[0].source == huge
    assert oversized[0].start == oversized[0].end == 2
    assert oversized[0].oversized_lines == (2,)
    assert "single_line_exceeds_budget" in oversized[0].limitations
    assert any(chunk.status == "ready" and chunk.end == 4 for chunk in chunks)
    assert_complete(chunks, symbol, source, len, budget)


def test_oversized_signature_yields_one_explicit_partial_without_truncation():
    signature = ", ".join(f"argument_{i}: int = {i}" for i in range(30))
    source = f"def compute({signature}) -> int:\n    return 1\n"
    symbol = make_symbol(source, kind="Function", signature=signature)
    chunks = chunk_symbol(symbol, source, len, 120)
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.status == "oversized" and chunk.partial
    assert "signature_exceeds_budget" in chunk.limitations
    assert chunk.signature == signature
    assert chunk.declaration == source.splitlines()[0]
    assert_complete(chunks, symbol, source, len, 120)


def test_context_cost_can_make_an_otherwise_small_line_oversized():
    source = "a fairly small physical line"
    symbol = make_symbol(source)
    full = chunk_symbol(symbol, source, len, 1000)[0]
    budget = len(full.context) + 5
    chunks = chunk_symbol(symbol, source, len, budget)
    assert len(source) < budget
    assert chunks[0].limitations == ("context_and_line_exceed_budget",)
    assert chunks[0].source == source


def test_actual_payload_is_counted_instead_of_adding_per_line_estimates():
    source = "ALPHA\nBETA\nGAMMA"
    symbol = make_symbol(source)

    def count(text):
        return len(text) + (1000 if "ALPHA\nBETA" in text else 0)

    context = chunk_symbol(symbol, source, len, 1000)[0].context
    budget = len(context) + 2 + 30
    chunks = chunk_symbol(symbol, source, count, budget)
    assert len(chunks) == 2
    assert all(chunk.status == "ready" for chunk in chunks)
    assert_complete(chunks, symbol, source, count, budget)


def test_bounded_overlap_always_progresses_and_can_shrink_to_fit():
    source = "\n".join(f"line_{i:02d}" for i in range(25))
    symbol = make_symbol(source)
    context = chunk_symbol(symbol, source, len, 10_000)[0].context
    budget = len(context) + 2 + len("line_00\nline_01\nline_02")
    chunks = chunk_symbol(symbol, source, len, budget, overlap_lines=MAX_OVERLAP_LINES)
    assert 1 < len(chunks) <= 25
    assert all(right.end > left.end for left, right in zip(chunks, chunks[1:]))
    assert all(right.start <= left.end + 1 for left, right in zip(chunks, chunks[1:]))
    assert_complete(chunks, symbol, source, len, budget)


def test_empty_module_crlf_and_trailing_blank_lines_follow_parser_hash_convention():
    for source in ["", "first\r\n\r\nlast\r\n", "first\n\n"]:
        symbol = make_symbol(source)
        chunks = chunk_symbol(symbol, source, len, 1000)
        assert_complete(chunks, symbol, source, len, 1000)
        assert chunks[0].source == "\n".join(source.splitlines())


@pytest.mark.parametrize("counter", [lambda text: -1, lambda text: 1.5, lambda text: True])
def test_invalid_token_counter_results_fail_closed(counter):
    with pytest.raises(RepoScopeError) as failure:
        chunk_symbol(make_symbol("x"), "x", counter, 1000)
    assert failure.value.code == "invalid_token_counter"


@pytest.mark.parametrize("budget,overlap", [(0, 0), (True, 0), (1.5, 0), (100, -1), (100, MAX_OVERLAP_LINES + 1)])
def test_invalid_budgets_are_rejected(budget, overlap):
    with pytest.raises(RepoScopeError) as failure:
        chunk_symbol(make_symbol("x"), "x", len, budget, overlap_lines=overlap)
    assert failure.value.code == "invalid_chunk_budget"


def test_one_line_suite_is_preserved_without_mislabeling_its_body_as_signature():
    source = 'def compute(): return "' + "x" * 400 + '"'
    symbol = make_symbol(source, kind="Function")
    chunk = chunk_symbol(symbol, source, len, 100)[0]
    assert chunk.source == source and chunk.declaration == source
    assert chunk.oversized_lines == (1,)
    assert "single_line_exceeds_budget" in chunk.limitations
    assert "declaration_exceeds_budget" in chunk.limitations
    assert "signature_exceeds_budget" not in chunk.limitations
