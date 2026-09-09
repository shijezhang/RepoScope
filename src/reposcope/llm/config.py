"""Explicit local provider configuration; never execute configuration as code."""

import json
import os
import shlex
from pathlib import Path
from urllib.parse import urlsplit

from reposcope.config import RepoScopeError


def provider_settings():
    values = {}
    source = os.getenv("REPOSCOPE_LLM_CONFIG")
    if source:
        path = Path(source).expanduser()
        try:
            if path.stat().st_size > 65536:
                raise RepoScopeError("model_configuration_error", "Provider configuration exceeds 64 KB")
            content = path.read_text()
            if path.suffix == ".json":
                values = json.loads(content)
            elif path.suffix == ".toml":
                import tomllib

                values = tomllib.loads(content)
            else:
                for line in content.splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith("export "):
                        line = line[7:]
                    key, separator, value = line.partition("=")
                    if not separator or not key.strip().isidentifier():
                        raise ValueError("Expected key=value configuration")
                    tokens = shlex.split(value, comments=True)
                    if len(tokens) > 1:
                        raise ValueError("Quote configuration values containing spaces")
                    values[key.strip()] = tokens[0] if tokens else ""
            if not isinstance(values, dict):
                raise ValueError("Expected object")
            if "env" in values:
                if not isinstance(values["env"], dict):
                    raise ValueError("Expected env object")
                values = values["env"]
        except (OSError, ValueError) as exc:
            raise RepoScopeError(
                "model_configuration_error", f"Cannot load local provider configuration ({type(exc).__name__})"
            ) from exc

    def setting(name, fallback=""):
        key = "REPOSCOPE_LLM_" + name.upper()
        aliases = {"base_url": "BASE_URL", "model": "MODEL", "api_key": "AUTH_TOKEN"}
        return os.getenv(key) or values.get(name, values.get(key, values.get(aliases.get(name), fallback)))

    base_url = setting("base_url", "" if source else "https://api.openai.com/v1")
    model = setting("model")
    api_key = setting("api_key")
    if not api_key and values.get("api_key_env"):
        api_key = os.getenv(values["api_key_env"], "")
    if not all(isinstance(value, str) for value in (base_url, model, api_key)):
        raise RepoScopeError("model_configuration_error", "Provider fields must be strings")
    if base_url:
        url = urlsplit(base_url)
        if (
            url.scheme not in {"https", "http"}
            or not url.netloc
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise RepoScopeError(
                "model_configuration_error", "Use an HTTP(S) base URL without embedded credentials or query parameters"
            )
    return {"base_url": base_url.rstrip("/"), "model": model, "api_key": api_key}
