import json

import pytest

from reposcope.config import RepoScopeError
from reposcope.llm.config import provider_settings


@pytest.fixture(autouse=True)
def clean_provider_environment(monkeypatch):
    for field in ("CONFIG", "BASE_URL", "MODEL", "API_KEY"):
        monkeypatch.delenv("REPOSCOPE_LLM_" + field, raising=False)


def test_explicit_local_json_and_key_environment(tmp_path, monkeypatch):
    path = tmp_path / "provider.json"
    path.write_text(
        json.dumps({"base_url": "http://127.0.0.1:1234/v1", "model": "test-model", "api_key_env": "TEST_PROVIDER_KEY"})
    )
    monkeypatch.setenv("REPOSCOPE_LLM_CONFIG", str(path))
    monkeypatch.setenv("TEST_PROVIDER_KEY", "fixture-only-key")
    assert provider_settings() == {
        "base_url": "http://127.0.0.1:1234/v1",
        "model": "test-model",
        "api_key": "fixture-only-key",
    }


def test_env_config_is_data_not_executable_and_errors_do_not_echo(tmp_path, monkeypatch):
    path = tmp_path / "provider.env"
    path.write_text(
        'REPOSCOPE_LLM_BASE_URL="https://provider.example/v1"\nREPOSCOPE_LLM_MODEL=model\nREPOSCOPE_LLM_API_KEY="$(touch must-not-exist)"\n'
    )
    monkeypatch.setenv("REPOSCOPE_LLM_CONFIG", str(path))
    assert provider_settings()["api_key"] == "$(touch must-not-exist)"
    assert not (tmp_path / "must-not-exist").exists()
    path.write_text("not a secret command")
    with pytest.raises(RepoScopeError) as error:
        provider_settings()
    assert "secret" not in str(error.value)


def test_provider_http_error_is_actionable_without_secret_echo(monkeypatch):
    import httpx

    from reposcope.llm.provider import Provider

    monkeypatch.delenv("REPOSCOPE_LLM_CONFIG", raising=False)
    monkeypatch.setenv("REPOSCOPE_LLM_BASE_URL", "https://provider.example/v1")
    monkeypatch.setenv("REPOSCOPE_LLM_MODEL", "fixture-model")
    monkeypatch.setenv("REPOSCOPE_LLM_API_KEY", "fixture-secret")
    client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: client(
            transport=httpx.MockTransport(lambda request: httpx.Response(401, json={"error": "bad fixture-secret"})),
            **kwargs,
        ),
    )
    provider = Provider()
    with pytest.raises(RepoScopeError) as failure:
        provider.decide({}, {})
    assert "401" in str(failure.value)
    assert "fixture-secret" not in str(failure.value)
    assert provider.usage["failed_requests"] == 1 and provider.usage["requests"] == 1


def test_provider_missing_usage_is_explicit(monkeypatch):
    import httpx

    from reposcope.llm.provider import Provider

    monkeypatch.delenv("REPOSCOPE_LLM_CONFIG", raising=False)
    monkeypatch.setenv("REPOSCOPE_LLM_BASE_URL", "https://provider.example/v1")
    monkeypatch.setenv("REPOSCOPE_LLM_MODEL", "fixture-model")
    monkeypatch.setenv("REPOSCOPE_LLM_API_KEY", "fixture-secret")
    client = httpx.Client
    response = {"choices": [{"message": {"content": '{"tool":"finish","summary":"Evidence complete"}'}}]}
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response)), **kwargs
        ),
    )
    provider = Provider()
    assert provider.decide({}, {}).tool == "finish"
    assert provider.usage["unknown_token_usage"] == 1


def test_existing_env_wrapper_uses_explicit_service_fields(tmp_path, monkeypatch):
    path = tmp_path / "env.json"
    path.write_text(
        json.dumps(
            {"env": {"BASE_URL": "https://provider.example", "MODEL": "chosen-model", "AUTH_TOKEN": "fixture-key"}}
        )
    )
    monkeypatch.setenv("REPOSCOPE_LLM_CONFIG", str(path))
    config = provider_settings()
    assert config["base_url"] == "https://provider.example"
    assert config["model"] == "chosen-model" and config["api_key"] == "fixture-key"


def test_truncated_output_diagnostic_excludes_content_and_reasoning(monkeypatch):
    import httpx

    from reposcope.llm.provider import Provider

    monkeypatch.setenv("REPOSCOPE_LLM_BASE_URL", "https://provider.example/v1")
    monkeypatch.setenv("REPOSCOPE_LLM_MODEL", "fixture-model")
    monkeypatch.setenv("REPOSCOPE_LLM_API_KEY", "fixture-secret")
    client = httpx.Client
    response = {
        "choices": [
            {
                "finish_reason": "length",
                "message": {"content": '{"tool":', "reasoning_content": "must not be persisted"},
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 600},
    }
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response)), **kwargs
        ),
    )
    provider = Provider()
    with pytest.raises(RepoScopeError) as error:
        provider.decide({}, {})
    assert error.value.code == "model_output_invalid"
    diagnostic = provider.diagnostics[0]
    assert diagnostic["finish_reason"] == "length"
    assert diagnostic["validation_errors"][0]["type"] == "json_invalid"
    assert diagnostic["content_characters"] == 8
    assert "must not be persisted" not in json.dumps(diagnostic)
    assert "fixture-secret" not in json.dumps(diagnostic)
