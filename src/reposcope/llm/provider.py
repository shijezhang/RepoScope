"""OpenAI-compatible structured decision adapter. Credentials are never persisted."""

import os

import httpx
from pydantic import BaseModel, Field, ValidationError

from reposcope.config import RepoScopeError
from reposcope.llm.config import provider_settings
from reposcope.llm.messages import OUTPUT_TOKENS, messages, request_upper_bound


class Decision(BaseModel):
    tool: str
    arguments: dict = Field(default_factory=dict)
    summary: str = Field(max_length=500)


class Provider:
    def __init__(self):
        config = provider_settings()
        expected_url = os.getenv("REPOSCOPE_LLM_EXPECTED_BASE_URL")
        expected_model = os.getenv("REPOSCOPE_LLM_EXPECTED_MODEL")
        if (expected_url and config["base_url"] != expected_url.rstrip("/")) or (
            expected_model and config["model"] != expected_model
        ):
            raise RepoScopeError("model_scope_changed", "Provider no longer matches the reviewed destination/model")
        self.base_url, self.model, self.key = config["base_url"], config["model"], config["api_key"]
        self.diagnostics = []
        self.usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "requests": 0,
            "failed_requests": 0,
            "unknown_token_usage": 0,
        }

    def decide(self, context, tools):
        if not self.key or not self.model or not self.base_url:
            raise RepoScopeError(
                "model_unavailable", "Set REPOSCOPE_LLM_MODEL and REPOSCOPE_LLM_API_KEY for optional Agent"
            )
        self.usage["requests"] += 1
        diagnostic = {
            "request_number": self.usage["requests"],
            "request_upper_bound": request_upper_bound(context, tools),
        }
        self.diagnostics.append(diagnostic)
        try:
            with httpx.Client(timeout=25) as client:
                response = client.post(
                    self.base_url + "/chat/completions",
                    headers={"Authorization": "Bearer " + self.key},
                    json={
                        "model": self.model,
                        "messages": messages(context, tools),
                        "response_format": {"type": "json_object"},
                        "max_tokens": OUTPUT_TOKENS,
                        "temperature": 0,
                    },
                )
                response.raise_for_status()
                body = response.json()
            usage = body.get("usage") or {}
            self.usage["input_tokens"] += usage.get("prompt_tokens", 0)
            self.usage["output_tokens"] += usage.get("completion_tokens", 0)
            if not usage:
                self.usage["unknown_token_usage"] += 1
            choice = body["choices"][0]
            content = choice["message"]["content"]
            diagnostic.update(
                finish_reason=choice.get("finish_reason"),
                content_characters=len(content) if isinstance(content, str) else None,
            )
            return Decision.model_validate_json(content)
        except ValidationError as exc:
            self.usage["failed_requests"] += 1
            diagnostic["validation_errors"] = [
                {"type": error["type"], "location": list(error["loc"])}
                for error in exc.errors(include_input=False, include_context=False, include_url=False)[:10]
            ]
            raise RepoScopeError(
                "model_output_invalid", "Model output failed structured validation; deterministic report retained"
            ) from exc
        except httpx.HTTPStatusError as exc:
            self.usage["failed_requests"] += 1
            self.usage["unknown_token_usage"] += 1
            status = exc.response.status_code
            diagnostic["http_status"] = status
            raise RepoScopeError(
                "model_error",
                f"Provider returned HTTP {status}; deterministic report retained",
                retryable=status == 429 or status >= 500,
            ) from exc
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            diagnostic["error_type"] = type(exc).__name__
            self.usage["failed_requests"] += 1
            raise RepoScopeError(
                "model_error", f"Model decision failed ({type(exc).__name__}); deterministic report retained"
            ) from exc
