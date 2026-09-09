import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("metrics", Path(__file__).parents[2] / "benchmarks/runners/metrics.py")
metrics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metrics)


def case(identifier, **overrides):
    return {
        "case_id": identifier,
        "annotation_status": "reviewed",
        "origin_group": identifier,
        "split": "holdout",
        "gold_impacts": ["a"],
        "test_pool": ["test_a", "test_b"],
        "base_sha": "base",
        "head_sha": "head",
        "config_hash": "c",
        **overrides,
    }


def prediction(identifier, **overrides):
    return {
        "case_id": identifier,
        "status": "completed",
        "base_sha": "base",
        "head_sha": "head",
        "config_hash": "c",
        "impacts": ["a"],
        "selected_tests": ["test_a"],
        **overrides,
    }


def test_non_regressions_excluded_missing_tasks_not_hidden():
    result = metrics.score(
        [case("a", regression_failures=["test_a"]), case("b"), case("c", gold_impacts=[])],
        [prediction("a"), prediction("c", impacts=["false"])],
    )
    assert result["regression_denominator"] == 1
    assert result["macro"]["regression_detected"] == 1
    assert result["macro"]["recall"] == 0.5
    assert result["missing_or_failed"] == 1
    assert result["cases"][2]["no_impact_false_positives"] == 1


def test_unreviewed_and_cross_split_groups_rejected():
    with pytest.raises(ValueError, match="reviewed"):
        metrics.score([case("a", annotation_status="unreviewed")], [])
    with pytest.raises(ValueError, match="splits"):
        metrics.score([case("a", origin_group="same"), case("b", origin_group="same", split="development")], [])
    with pytest.raises(ValueError, match="input mismatch"):
        metrics.score([case("a")], [prediction("a", head_sha="future")])
    with pytest.raises(ValueError, match="uncollected"):
        metrics.score([case("a")], [prediction("a", selected_tests=["invented"])])


def test_paired_groups_and_small_sample_refusal():
    one = metrics.score([case("a")], [prediction("a")])
    assert metrics.paired_group_interval(one, one, "recall")["interval"] is None
    two = metrics.score([case("a"), case("b")], [prediction("a"), prediction("b")])
    assert metrics.paired_group_interval(two, two, "recall", repeats=100)["interval"] == [0, 0]
