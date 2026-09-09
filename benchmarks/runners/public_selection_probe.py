"""Engineering observation: real selector pool size, not a quality benchmark."""

import json
from pathlib import Path

from reposcope.analysis.impact import analyze
from reposcope.config import Settings
from reposcope.graph.store import Store
from reposcope.jobs.worker import Worker

ROOT = Path(__file__).resolve().parents[2]


def main():
    cases = json.loads((ROOT / "benchmarks/results/public-docker-validation.json").read_text())["cases"]
    store = Store(Settings(home=ROOT / "artifacts/public-docker-validation-state"))
    observations = []
    for case in cases:
        # Deliberately do not feed execution outcomes or the head failure set into selection.
        collections = case["collections"]
        base, head = [store.snapshot(collections[side]["snapshot_id"]) for side in ["base", "head"]]
        repo = {"repo_id": case["repository"], "path": str(ROOT / case["source"]), "profile_id": case["repository"]}
        report = analyze(store, repo, base, head, "engineering-" + case["case_id"])
        selection = Worker(store).select_plan(report, base, head, collections["head"], collections["base"])
        total = len(collections["head"]["nodeids"])
        count = len(selection["nodeids"])
        observations.append(
            {
                "case_id": case["case_id"],
                "parser_versions": {"base": base.parser_version, "head": head.parser_version},
                "pool_count": total,
                "selected_count": count,
                "test_count_reduction": 1 - count / total if total else None,
                "strategy": selection["strategy"],
                "reasons": selection["reasons"],
                "coverage_source_snapshot": selection["coverage_source_snapshot"],
                "coverage_evidence_count": len(selection["coverage_evidence_ids"]),
                "selection_inputs": [
                    "immutable base/head source snapshots",
                    "successfully collected nodeids",
                    "exactly base-bound call-phase coverage",
                ],
                "head_failure_set_used": False,
                "executed_alternative_policy": False,
            }
        )
    output = {
        "kind": "post-run-engineering-selection-replay",
        "quality_benchmark": False,
        "limitations": [
            "No held-out labels or quality metric",
            "Conservative full fallback may yield zero reduction",
            "No runtime saving is inferred from selected counts",
            "This probe does not execute additional tests",
        ],
        "cases": observations,
    }
    path = ROOT / "benchmarks/results/public-selection-engineering.json"
    path.write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps(output), flush=True)


if __name__ == "__main__":
    main()
