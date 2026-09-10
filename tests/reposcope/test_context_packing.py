import copy

import pytest

from reposcope.agent.context import pack_context
from reposcope.config import RepoScopeError
from reposcope.llm.messages import OUTPUT_TOKENS, messages, request_upper_bound


def report():
    return {
        "run_id": "r",
        "question": "Review the change",
        "base": {"snapshot_id": "b", "commit_sha": "base"},
        "head": {"snapshot_id": "h", "commit_sha": "head"},
        "test_plan": {"plan_id": "p", "status": "completed"},
        "limitations": ["Dynamic dispatch remains unknown"],
        "impacts": [],
    }


def test_whole_evidence_admission_preserves_latest_feedback_and_omissions():
    original = report()
    untouched = copy.deepcopy(original)
    old = {"tool": "read_evidence", "result": {"source": "def whole():\n" + "    value += 1\n" * 2000}}
    latest = {"tool": "run_tests", "result": {"status": "completed", "findings": [{"finding": "suspected_regression"}]}}
    context, packing = pack_context(original, [old, latest], {"finish": {}}, 3000, True)
    assert context["prior_results"] == [latest]
    assert packing["omitted_results"] == 1
    assert context["limitations"] == original["limitations"]
    assert packing["request_upper_bound"] <= 3000
    assert original == untouched
    assert old["result"]["source"].endswith("    value += 1\n")


def test_unfit_essential_context_stops_without_slicing_question():
    value = report()
    value["question"] = "important requirement " * 1000
    with pytest.raises(RepoScopeError, match="Essential context"):
        pack_context(value, [], {}, 2000, False)


def test_wire_encoding_and_generated_budget_match_accounting():
    context, packing = pack_context(report(), [], {}, 5000, False)
    assert OUTPUT_TOKENS == 600
    wire = messages(context, {})
    assert "\\u" not in wire[1]["content"]
    assert packing["request_upper_bound"] == request_upper_bound(context, {})
    assert request_upper_bound(context, {}) > sum(len(row["content"].encode()) for row in wire) + OUTPUT_TOKENS


def test_saved_fixture_budget_failure_can_admit_complete_recent_evidence():
    import json
    from pathlib import Path

    from reposcope.agent.controller import TOOLS

    root = Path(__file__).parents[2]
    preview = json.loads((root / "docs/examples/agent-data-preview.json").read_text())
    old = json.loads((root / "benchmarks/results/agent-validation.json").read_text())
    arm = next(row for row in old["arms"] if row["case_id"] == "fixture-01" and row["arm"] == "agent")
    initial = preview["cases"][0]["initial_outbound_context"]
    value = {key: initial[key] for key in ("run_id", "question", "base", "head", "test_plan", "limitations")}
    value["impacts"] = [
        {
            "symbol": {k: hit[k] for k in ("symbol_id", "path", "qualname")},
            **{k: hit[k] for k in ("side", "distance", "evidence_id")},
        }
        for hit in initial["impact_summary"]
    ]
    schemas = {key: model.model_json_schema() for key, model in TOOLS.items()}
    schemas["finish"] = {}
    remaining = 12000 - arm["usage"]["input_tokens"] - arm["usage"]["output_tokens"]
    old_context = {**initial, "prior_results": arm["tool_calls"][-3:]}
    assert len(json.dumps(old_context).encode()) + len(json.dumps(schemas).encode()) + 2000 > remaining
    packed, accounting = pack_context(value, arm["tool_calls"], schemas, remaining, True)
    assert accounting["request_upper_bound"] <= remaining
    assert arm["tool_calls"][-1] in packed["prior_results"]
    assert packed["question"] == initial["question"]
