import copy
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from reposcope.agent.controller import TOOLS
from reposcope.config import RepoScopeError
from reposcope.llm.provider import Provider
from reposcope.models import digest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "benchmarks.runners.agent_validation", ROOT / "benchmarks/runners/agent_validation.py"
)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def scope(tmp_path):
    files = {"api.py": "pass\n", "calc.py": "pass\n", "test_calc.py": "pass\n"}
    cases = [
        {
            "case_id": name,
            "base": "a" * 40,
            "head": "b" * 40,
            "replay_repository": f"artifacts/benchmark-replay/run-test/{name}",
        }
        for name in ["fixture-01", "fixture-04"]
    ]
    frozen = {
        "question": "review",
        "budget": probe.BUDGET,
        "cases": cases,
        "profile": {"fixed": True},
        "profile_hash": digest({"fixed": True}),
    }
    preview = {
        "question": "review",
        "budget": probe.BUDGET,
        "destination": "https://api.deepseek.com",
        "model": "deepseek-v4-pro",
        "tool_schemas": {**{name: model.model_json_schema() for name, model in TOOLS.items()}, "finish": {}},
        "cases": [
            {
                "case_id": case["case_id"],
                "snapshots": [{"side": side, "commit_sha": case[side], "files": files} for side in ["base", "head"]],
            }
            for case in cases
        ],
    }
    return frozen, preview, {"preview_sha256": digest(preview)}, files


def test_scope_verification_is_read_only_and_matches_exact_destination(tmp_path, monkeypatch):
    frozen, preview, seal, files = scope(tmp_path)
    monkeypatch.setattr(probe, "read_tree", lambda *args: files)
    provider = SimpleNamespace(base_url=preview["destination"], model=preview["model"])
    assert probe.validate_resume_scope(frozen, preview, seal, tmp_path, provider)["source_hashes_verified"]
    for changed in [
        SimpleNamespace(base_url="https://other.invalid", model=provider.model),
        SimpleNamespace(base_url=provider.base_url, model="another-model"),
    ]:
        with pytest.raises(RepoScopeError, match="scope changed"):
            probe.validate_resume_scope(frozen, preview, seal, tmp_path, changed)


@pytest.mark.parametrize("change", ["source", "commit", "preview"])
def test_scope_drift_stops_before_model_access(tmp_path, monkeypatch, change):
    frozen, preview, seal, files = scope(tmp_path)
    frozen, preview = copy.deepcopy(frozen), copy.deepcopy(preview)
    if change == "source":
        files = {**files, "api.py": "changed = True\n"}
    elif change == "commit":
        frozen["cases"][0]["head"] = "c" * 40
    else:
        preview["question"] = "Changed while awaiting confirmation"
    monkeypatch.setattr(probe, "read_tree", lambda *args: files)
    with pytest.raises(RepoScopeError, match="scope changed"):
        probe.validate_resume_scope(frozen, preview, seal, tmp_path, SimpleNamespace())


def test_provider_rechecks_expected_scope_at_construction(monkeypatch):
    monkeypatch.setenv("REPOSCOPE_LLM_EXPECTED_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("REPOSCOPE_LLM_EXPECTED_MODEL", "deepseek-v4-pro")
    monkeypatch.setattr(
        "reposcope.llm.provider.provider_settings",
        lambda: {"base_url": "https://changed.invalid", "model": "deepseek-v4-pro", "api_key": "synthetic-test-value"},
    )
    with pytest.raises(RepoScopeError) as caught:
        Provider()
    assert caught.value.code == "model_scope_changed"
    assert "synthetic-test-value" not in str(caught.value)
