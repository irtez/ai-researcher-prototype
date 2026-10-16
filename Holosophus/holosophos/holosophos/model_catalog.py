"""Server-owned model configurations; public metadata never contains credentials."""

from __future__ import annotations

import copy
import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import yaml

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config/models.yaml"
RUNTIMES = {"claude_code", "codearkt"}
EFFORTS = {"low", "medium", "high", "max"}


def _validate(item: dict) -> dict:
    if not isinstance(item, dict):
        raise ValueError("Model configuration must be a mapping")
    allowed = {
        "id",
        "label",
        "runtime",
        "connection",
        "model_id",
        "model_id_env",
        "base_url",
        "base_url_env",
        "auth",
        "generation",
        "enabled",
        "effort",
    }
    unknown = set(item) - allowed
    if unknown:
        raise ValueError(f"Unknown model settings: {', '.join(sorted(unknown))}")
    for key in ("id", "label", "model_id"):
        if not isinstance(item.get(key), str) or not item[key].strip():
            raise ValueError(f"Model configuration requires {key}")
    if item.get("runtime") not in RUNTIMES:
        raise ValueError("Unknown model runtime")
    if item.get("connection") not in {"subscription", "api"}:
        raise ValueError("Model connection must be subscription or api")
    if item["connection"] == "subscription" and item["runtime"] != "claude_code":
        raise ValueError("Subscription connection requires Claude Code")
    auth = item.get("auth", {})
    if not isinstance(auth, dict) or set(auth) - {"type", "token_env"}:
        raise ValueError("Model auth accepts only type and token_env")
    if item["connection"] == "api":
        url = urlsplit(item.get("base_url", ""))
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
        ):
            raise ValueError(
                "Model API requires an HTTP(S) base_url without credentials"
            )
        if auth.get("type") not in {"none", "bearer", "api_key"}:
            raise ValueError("Unsupported model authentication")
        if auth["type"] != "none" and not re.fullmatch(
            r"[A-Za-z_][A-Za-z_0-9]*", auth.get("token_env", "")
        ):
            raise ValueError("Model auth requires token_env")
    generation = item.get("generation", {})
    if not isinstance(generation, dict):
        raise ValueError("Model generation settings must be a mapping")
    unknown = set(generation) - {
        "thinking",
        "effort",
        "max_output_tokens",
        "context_window",
    }
    if unknown:
        raise ValueError(
            f"Unsupported generation settings: {', '.join(sorted(unknown))}"
        )
    for key in ("max_output_tokens", "context_window"):
        value = generation.get(key)
        if value is not None and (type(value) is not int or value <= 0):
            raise ValueError(f"{key} must be a positive integer")
    if generation.get("max_output_tokens", 0) >= generation.get(
        "context_window", float("inf")
    ):
        raise ValueError("Output limit must be smaller than context window")
    thinking = generation.get("thinking")
    if thinking is not None:
        if not isinstance(thinking, dict) or thinking.get("type") not in {
            "adaptive",
            "enabled",
            "disabled",
        }:
            raise ValueError("Invalid thinking configuration")
        if set(thinking) - {"type", "budget_tokens"}:
            raise ValueError("Unknown thinking setting")
        if thinking["type"] == "enabled":
            budget = thinking.get("budget_tokens")
            if (
                type(budget) is not int
                or budget < 1024
                or budget >= generation.get("max_output_tokens", float("inf"))
            ):
                raise ValueError(
                    "Thinking budget must be >= 1024 and smaller than output limit"
                )
        elif "budget_tokens" in thinking:
            raise ValueError("Only enabled thinking accepts budget_tokens")
        if item["runtime"] != "claude_code":
            raise ValueError(
                "Use effort for CodeArkt reasoning; thinking is a Claude Code setting"
            )
    effort = generation.get("effort", {})
    if not isinstance(effort, dict) or set(effort) - {"choices", "default"}:
        raise ValueError("Invalid effort configuration")
    choices = effort.get("choices", [])
    if (
        not isinstance(choices, list)
        or any(
            not isinstance(e, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", e)
            for e in choices
        )
        or (item["runtime"] == "claude_code" and any(e not in EFFORTS for e in choices))
        or len(set(choices)) != len(choices)
    ):
        raise ValueError("Invalid effort choices")
    if choices and effort.get("default") not in choices:
        raise ValueError("Default effort must be in choices")
    if not choices and effort.get("default") is not None:
        raise ValueError("Effort default requires choices")
    if item.get("effort") is not None and item["effort"] not in choices:
        raise ValueError("Selected effort is not allowed by this configuration")
    return item


def load_catalog() -> dict:
    path = Path(os.environ.get("AI_RESEARCHER_MODELS_CONFIG") or CONFIG_PATH)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("models"), dict):
        raise ValueError("Model catalog requires models mapping")
    models = {}
    for key, value in data["models"].items():
        if not isinstance(value, dict):
            raise ValueError("Model entry must be a mapping")
        if value.get("enabled", True) is False:
            continue
        item = {**copy.deepcopy(value), "id": key}
        for field in ("model_id", "base_url"):
            variable = item.pop(f"{field}_env", None)
            if variable:
                item[field] = os.environ.get(variable) or item.get(field)
        models[key] = _validate(item)
    defaults = data.get("defaults", {})
    for runtime, key in defaults.items():
        if key not in models or models[key]["runtime"] != runtime:
            raise ValueError(f"Invalid default model for {runtime}")
    return {"models": models, "defaults": defaults}


def public_config() -> dict:
    data = load_catalog()
    public = {runtime: [] for runtime in RUNTIMES}
    for item in data["models"].values():
        effort = item.get("generation", {}).get("effort", {})
        public[item["runtime"]].append(
            {
                "id": item["id"],
                "label": item["label"],
                "efforts": effort.get("choices", []),
                "default_effort": effort.get("default"),
            }
        )
    return {
        "model_configs": public,
        "claude_code_default_model": data["defaults"].get("claude_code"),
        "claude_code_model_choices": [i["id"] for i in public["claude_code"]],
        "model_slug": data["defaults"].get("codearkt"),
        "model_choices": [i["id"] for i in public["codearkt"]],
    }


def resolve(runtime: str, name: str | None = None, effort: str | None = None) -> dict:
    data = load_catalog()
    key = (name or "").strip() or data["defaults"].get(runtime)
    item = copy.deepcopy(data["models"].get(key))
    if item is None or item["runtime"] != runtime:
        raise ValueError(f"Unknown {runtime} model configuration: {key}")
    policy = item.get("generation", {}).get("effort", {})
    selected = effort or policy.get("default")
    if selected is not None and selected not in policy.get("choices", []):
        raise ValueError(f"Unsupported effort for {item['label']}: {selected}")
    item["effort"] = selected
    return item


def _token(item: dict) -> str:
    auth = item.get("auth", {})
    if auth.get("type") == "none":
        return (
            "unused"  # SDK requires a credential even for an unauthenticated local API.
        )
    variable = auth.get("token_env", "")
    value = os.environ.get(variable, "").strip()
    if not value or value == "...":
        raise ValueError(f"Model credential is missing: {variable}")
    return value


def claude_options(item: dict) -> dict:
    _validate(item)
    _authorize_connection(item)
    # Per-child env: never mutate global state or mix concurrent connections.
    env = {
        key: ""
        for key in (
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_BASE_URL",
            "CLAUDE_CODE_OAUTH_TOKEN",
            "ANTHROPIC_CUSTOM_HEADERS",
            "ANTHROPIC_MODEL",
            "CLAUDE_CODE_USE_BEDROCK",
            "CLAUDE_CODE_USE_VERTEX",
            "CLAUDE_CODE_USE_FOUNDRY",
            "CLAUDE_CODE_EFFORT_LEVEL",
            "MAX_THINKING_TOKENS",
            "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
            "CLAUDE_CODE_MAX_CONTEXT_TOKENS",
            "CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING",
            "CLAUDE_CODE_DISABLE_THINKING",
        )
    }
    for alias in ("OPUS", "SONNET", "HAIKU"):
        # These variables require full model IDs, not recursive alias mappings.
        env[f"ANTHROPIC_DEFAULT_{alias}_MODEL"] = (
            ""
            if item["connection"] == "subscription"
            and item["model_id"] in {"opus", "sonnet", "haiku"}
            else item["model_id"]
        )
    env["CLAUDE_CODE_SUBAGENT_MODEL"] = item["model_id"]
    if item["connection"] == "api":
        env["ANTHROPIC_BASE_URL"] = item["base_url"]
        key = (
            "ANTHROPIC_API_KEY"
            if item["auth"]["type"] == "api_key"
            else "ANTHROPIC_AUTH_TOKEN"
        )
        env[key] = _token(item)
        # Custom endpoints may not understand Anthropic's deferred tool references.
        env["ENABLE_TOOL_SEARCH"] = "false"
    else:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
    generation = item.get("generation", {})
    for field, variable in (
        ("max_output_tokens", "CLAUDE_CODE_MAX_OUTPUT_TOKENS"),
        ("context_window", "CLAUDE_CODE_MAX_CONTEXT_TOKENS"),
    ):
        if field in generation:
            env[variable] = str(generation[field])
    options = {"model": item["model_id"], "env": env}
    if generation.get("thinking") is not None:
        options["thinking"] = copy.deepcopy(generation["thinking"])
    if item.get("effort"):
        options["effort"] = item["effort"]
    return options


def save_selection(workdir: Path, item: dict) -> None:
    from .research_session import _marker

    target = _marker(workdir, "model_config.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=target.parent, delete=False
    ) as stream:
        json.dump(item, stream, ensure_ascii=False)
        temporary = Path(stream.name)
    try:
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def session_selection(workdir: Path, name: str | None = None) -> dict:
    from .research_session import _marker, read_session_model

    path = _marker(workdir, "model_config.json")
    if path.exists():
        saved = _validate(json.loads(path.read_text(encoding="utf-8")))
        # Workspace files are editable by research tools. They cannot choose a
        # destination for server credentials. Pin generation/model versions, but
        # authorize the connection against the deployment-owned catalog again.
        _authorize_connection(saved)
        return saved
    name = name or read_session_model(workdir)
    try:
        return resolve("claude_code", name)
    except ValueError:
        # Old sessions/CLI callers can carry a full Claude slug. New UI requests
        # are resolved strictly through the catalog before this path is reached.
        if name and name.startswith("claude-"):
            candidates = [
                item
                for item in load_catalog()["models"].values()
                if item["runtime"] == "claude_code"
                and item["connection"] == "subscription"
            ]
            family = next(
                (part for part in ("haiku", "sonnet", "opus") if part in name), None
            )
            trusted = next(
                (item for item in candidates if item["id"] == family),
                candidates[0] if candidates else None,
            )
            if trusted:
                item = resolve("claude_code", trusted["id"])
                item["model_id"] = name
                item["label"] = name
                return item
        raise


def _authorize_connection(item: dict) -> None:
    current = load_catalog()["models"].get(item["id"])
    if current is None or any(
        item.get(key) != current.get(key)
        for key in ("runtime", "connection", "base_url", "auth")
    ):
        raise ValueError(
            "Saved model connection differs from the configured connection; restore its configuration or start a new session"
        )


def codearkt_options(item: dict) -> dict:
    _validate(item)
    generation = item.get("generation", {})
    params = {}
    if item.get("effort"):
        params["reasoning_effort"] = item["effort"]
    if generation.get("max_output_tokens"):
        params["max_completion_tokens"] = generation["max_output_tokens"]
    return {
        "model_name": item["model_id"],
        "base_url": item["base_url"],
        "config_id": item["id"],
        "label": item["label"],
        "effort": item.get("effort"),
        "api_key": _token(item),
        "params": params,
        "context_window": generation.get("context_window"),
    }


def resolve_codearkt(
    name: str | None, effort: str | None = None, *, strict: bool = False
) -> dict | None:
    data = load_catalog()
    key = name
    if name not in data["models"]:
        key = next(
            (
                i["id"]
                for i in data["models"].values()
                if i["runtime"] == "codearkt" and i["model_id"] == name
            ),
            None,
        )
    if not key and name and not strict:
        return None  # Retain standalone eval/CLI overrides outside the UI catalog.
    return codearkt_options(resolve("codearkt", key or name, effort))


if __name__ == "__main__":
    catalog = load_catalog()
    print(f"Model catalog valid: {len(catalog['models'])} enabled configurations")
