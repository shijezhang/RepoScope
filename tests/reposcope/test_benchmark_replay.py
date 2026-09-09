"""Mutation drift and historical Git retention must be checked before delivery."""

import ast
import importlib.util
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("benchmark_replay", ROOT / "benchmarks/runners/replay.py")
replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)


def test_fixture_mutation_anchors_match_formatted_sources():
    cases = json.loads((ROOT / "benchmarks/cases/development.json").read_text())
    fixtures = [case for case in cases if case["repo_id"] == "fixture"]
    assert len(fixtures) == 6
    for case in fixtures:
        mutation = case["mutation"]
        source = (ROOT / "benchmarks/fixtures" / mutation["path"]).read_text()
        assert source.count(mutation["old"]) == mutation["expected_occurrences"], case["case_id"]
        mutated = source.replace(mutation["old"], mutation["new"])
        assert mutated != source
        ast.parse(mutated)


def test_repeated_replay_preserves_old_git_objects(tmp_path, monkeypatch):
    layout = tmp_path / "project"
    (layout / "benchmarks/cases").mkdir(parents=True)
    (layout / "benchmarks/manifests").mkdir(parents=True)
    shutil.copytree(
        ROOT / "benchmarks/fixtures",
        layout / "benchmarks/fixtures",
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"),
    )
    cases = json.loads((ROOT / "benchmarks/cases/development.json").read_text())
    (layout / "benchmarks/cases/development.json").write_text(
        json.dumps([c for c in cases if c["case_id"] == "fixture-04"])
    )
    (layout / "benchmarks/manifests/repositories.json").write_text('{"repositories": []}')
    legacy = layout / "artifacts/benchmark-replay/fixture-04/repository"
    legacy.mkdir(parents=True)
    replay.git(legacy, "init", "-q")
    (legacy / "original.py").write_text("original = True\n")
    old_sha = replay.commit(legacy, "Historical report source")
    monkeypatch.setattr(replay, "ROOT", layout)
    assert replay.main(["--case", "fixture-04"]) == 0
    first = json.loads((layout / "benchmarks/results/fixture-04-index.json").read_text())
    assert replay.main(["--case", "fixture-04"]) == 0
    second = json.loads((layout / "benchmarks/results/fixture-04-index.json").read_text())
    assert first["run_id"] != second["run_id"]
    assert first["cases"][0]["replay_repository"] != second["cases"][0]["replay_repository"]
    assert replay.git(legacy, "cat-file", "-t", old_sha) == "commit"
    for run in [first, second]:
        row = run["cases"][0]
        path = layout / row["replay_repository"]
        assert replay.git(path, "cat-file", "-t", row["base"]) == "commit"
        assert replay.git(path, "cat-file", "-t", row["head"]) == "commit"
        assert (layout / "artifacts/benchmark-replay" / run["run_id"] / "results.json").exists()
