"""Score separately reviewed annotations; never treat generated probes as gold."""

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path
from statistics import mean


def score(cases, predictions):
    groups, ids = {}, set()
    for case in cases:
        if case["case_id"] in ids:
            raise ValueError("Duplicate case id")
        ids.add(case["case_id"])
        if case.get("annotation_status") != "reviewed":
            raise ValueError("Formal scoring requires independently reviewed annotations")
        group, split = case["origin_group"], case["split"]
        if group in groups and groups[group] != split:
            raise ValueError("Same-origin changes cross dataset splits")
        groups[group] = split
        if not set(case.get("regression_failures", [])).issubset(case["test_pool"]):
            raise ValueError("Regression failures must belong to frozen test pool")
    by_id = {}
    for prediction in predictions:
        if prediction["case_id"] not in ids or prediction["case_id"] in by_id:
            raise ValueError("Unknown or repeated prediction case id")
        by_id[prediction["case_id"]] = prediction
    results = []
    for case in cases:
        predicted = by_id.get(case["case_id"], {})
        status = predicted.get("status", "missing")
        selected = set(predicted.get("selected_tests", []))
        pool = set(case["test_pool"])
        if not selected.issubset(pool):
            raise ValueError("Prediction contains an uncollected test")
        for key in ("base_sha", "head_sha", "config_hash"):
            if predicted and predicted.get(key) != case.get(key):
                raise ValueError(f"Prediction input mismatch: {key}")
        gold, found = set(case["gold_impacts"]), set(predicted.get("impacts", []))
        precision = len(found & gold) / len(found) if found else 0.0
        recall = len(found & gold) / len(gold) if gold else None
        failures = set(case.get("regression_failures", []))
        results.append(
            {
                "case_id": case["case_id"],
                "origin_group": case["origin_group"],
                "status": status,
                "precision": precision if gold else None,
                "recall": recall,
                "f1": (2 * precision * recall / (precision + recall) if precision + (recall or 0) else 0.0)
                if gold
                else None,
                "no_impact_false_positives": len(found) if not gold else None,
                "regression_detected": bool(selected & failures) if failures else None,
                "failure_test_recall": len(selected & failures) / len(failures) if failures else None,
                "test_reduction": 1 - len(selected) / len(pool) if pool and status == "completed" else None,
                "online_seconds": predicted.get("online_seconds"),
                "execution_kind": predicted.get("execution_kind", "not_run"),
            }
        )
    fields = (
        "precision",
        "recall",
        "f1",
        "regression_detected",
        "failure_test_recall",
        "test_reduction",
        "online_seconds",
    )
    aggregate = {
        field: mean(values) if (values := [r[field] for r in results if r[field] is not None]) else None
        for field in fields
    }
    return {
        "cases": results,
        "macro": aggregate,
        "case_count": len(results),
        "missing_or_failed": sum(r["status"] != "completed" for r in results),
        "regression_denominator": sum(r["regression_detected"] is not None for r in results),
        "quality_scope": "reviewed labeled impact definition; graph reachability is not behavioral truth",
    }


def paired_group_interval(left, right, metric, seed=0, repeats=2000):
    """Paired bootstrap over origin groups, retaining within-group dependence."""
    a, b = {r["case_id"]: r for r in left["cases"]}, {r["case_id"]: r for r in right["cases"]}
    if set(a) != set(b):
        raise ValueError("Paired comparison requires identical cases")
    groups = defaultdict(list)
    for case_id, row in a.items():
        if row[metric] is not None and b[case_id][metric] is not None:
            groups[row["origin_group"]].append(float(b[case_id][metric]) - float(row[metric]))
    if len(groups) < 2:
        return {"delta": None, "interval": None, "reason": "Insufficient independent origin groups"}
    values = [mean(group) for group in groups.values()]
    rng = random.Random(seed)
    samples = sorted(mean(rng.choices(values, k=len(values))) for _ in range(repeats))
    return {
        "delta": mean(values),
        "interval": [samples[int(repeats * 0.025)], samples[int(repeats * 0.975)]],
        "groups": len(values),
        "repeats": repeats,
        "seed": seed,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("annotations", type=Path)
    parser.add_argument("predictions", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = score(json.loads(args.annotations.read_text()), json.loads(args.predictions.read_text()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
