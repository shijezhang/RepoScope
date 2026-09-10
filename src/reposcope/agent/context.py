"""Budget admission uses complete evidence units, never sliced code strings."""

from copy import deepcopy

from reposcope.config import RepoScopeError
from reposcope.llm.messages import request_upper_bound


def pack_context(report, results, schemas, remaining, allow_tests):
    impacts = [
        {
            "symbol_id": item["symbol"]["symbol_id"],
            "path": item["symbol"]["path"],
            "qualname": item["symbol"]["qualname"],
            "side": item["side"],
            "distance": item["distance"],
            "evidence_id": item["evidence_id"],
        }
        for item in report["impacts"][:6]
    ]
    recent = results[-3:]
    limits = report["limitations"]
    context = {
        "run_id": report["run_id"],
        "question": report["question"],
        "base": report["base"],
        "head": report["head"],
        "test_plan": {k: report["test_plan"][k] for k in ("plan_id", "status")},
        "test_execution_allowed": allow_tests,
        "impact_summary": [],
        "prior_results": [],
        "limitations": [],
        "context_selection": {
            "omitted_impacts": len(impacts),
            "omitted_results": len(recent),
            "omitted_limitations": len(limits),
        },
    }
    if request_upper_bound(context, schemas) > remaining:
        raise RepoScopeError("budget_exceeded", "Essential context and response reserve exceed remaining budget")
    admitted = set()

    def admit(field, value, counter, key):
        candidate = deepcopy(context)
        candidate[field].append(value)
        candidate["context_selection"][counter] -= 1
        if request_upper_bound(candidate, schemas) <= remaining:
            context.clear()
            context.update(candidate)
            admitted.add(key)

    # New execution feedback takes precedence over repeatedly sending the same
    # initial symbol summaries. Whole omitted units remain available in Store.
    if recent:
        admit("prior_results", recent[-1], "omitted_results", len(recent) - 1)
    for limit in limits:
        admit("limitations", limit, "omitted_limitations", ("limit", limit))
    for index in range(len(recent) - 2, -1, -1):
        admit("prior_results", recent[index], "omitted_results", index)
    for index, impact in enumerate(impacts):
        admit("impact_summary", impact, "omitted_impacts", ("impact", index))
    context["prior_results"] = [row for index, row in enumerate(recent) if index in admitted]
    return context, {
        "remaining_tokens": remaining,
        "request_upper_bound": request_upper_bound(context, schemas),
        **context["context_selection"],
    }
