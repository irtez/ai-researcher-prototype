"""Configuration routing without contacting any model/provider."""

import json
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "holosophos"))
from holosophos import model_catalog as catalog


@pytest.fixture
def configured(tmp_path, monkeypatch):
    path = tmp_path / "models.yaml"
    data = {
        "defaults": {"claude_code": "lab"},
        "models": {
            "lab": {
                "label": "Лабораторная модель",
                "runtime": "claude_code",
                "connection": "api",
                "model_id": "test-model",
                "base_url": "http://127.0.0.1:8000",
                "auth": {"type": "bearer", "token_env": "TEST_MODEL_TOKEN"},
                "generation": {
                    "thinking": {"type": "disabled"},
                    "max_output_tokens": 4096,
                    "context_window": 32768,
                    "effort": {"choices": ["low", "high"], "default": "high"},
                },
            }
        },
    }
    path.write_text(yaml.safe_dump(data))
    monkeypatch.setenv("AI_RESEARCHER_MODELS_CONFIG", str(path))
    monkeypatch.setenv("TEST_MODEL_TOKEN", "test-secret")
    return path, data


def test_public_catalog_has_no_connection_or_secrets(configured):
    public = catalog.public_config()
    assert public["claude_code_default_model"] == "lab"
    entry = public["model_configs"]["claude_code"][0]
    assert entry == {
        "id": "lab",
        "label": "Лабораторная модель",
        "efforts": ["low", "high"],
        "default_effort": "high",
    }
    assert "test-secret" not in json.dumps(public)
    assert "base_url" not in json.dumps(public)


def test_routing_and_effort_are_per_session(configured):
    low = catalog.resolve("claude_code", "lab", "low")
    high = catalog.resolve("claude_code", "lab")
    assert low["effort"] == "low" and high["effort"] == "high"
    options = catalog.claude_options(low)
    assert options["model"] == "test-model" and options["effort"] == "low"
    assert options["env"]["ANTHROPIC_AUTH_TOKEN"] == "test-secret"
    assert options["env"]["ANTHROPIC_API_KEY"] == ""
    assert options["env"]["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "test-model"
    assert options["env"]["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "4096"
    assert "test-secret" not in json.dumps(low)


@pytest.mark.parametrize("model,effort", [("missing", None), ("lab", "max")])
def test_unknown_selection_rejected(configured, model, effort):
    with pytest.raises(ValueError):
        catalog.resolve("claude_code", model, effort)


def test_missing_key_and_unsupported_parameters_rejected(configured, monkeypatch):
    path, data = configured
    monkeypatch.delenv("TEST_MODEL_TOKEN")
    with pytest.raises(ValueError, match="TEST_MODEL_TOKEN"):
        catalog.claude_options(catalog.resolve("claude_code", "lab"))
    data["models"]["lab"]["generation"]["temperature"] = 0.5
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError, match="temperature"):
        catalog.resolve("claude_code", "lab")


def test_snapshot_survives_catalog_change(configured):
    path, data = configured
    snapshot = catalog.resolve("claude_code", "lab", "low")
    data["models"]["lab"]["model_id"] = "different-model"
    path.write_text(yaml.safe_dump(data))
    assert catalog.claude_options(snapshot)["model"] == "test-model"


def test_workspace_snapshot_cannot_redirect_credentials(configured, tmp_path):
    work = tmp_path / "session"
    snapshot = catalog.resolve("claude_code", "lab", "low")
    catalog.save_selection(work, snapshot)
    snapshot["base_url"] = "https://other.example"
    catalog.save_selection(work, snapshot)
    with pytest.raises(ValueError, match="connection differs"):
        catalog.session_selection(work)


def test_subscription_clears_inherited_external_credentials(monkeypatch):
    monkeypatch.delenv("AI_RESEARCHER_MODELS_CONFIG", raising=False)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "unrelated-secret")
    snapshot = catalog.resolve("claude_code", "haiku")
    options = catalog.claude_options(snapshot)
    assert options["env"]["ANTHROPIC_AUTH_TOKEN"] == ""
    assert options["env"]["ANTHROPIC_BASE_URL"] == ""
    assert options["env"]["CLAUDE_CODE_EFFORT_LEVEL"] == ""
    assert "effort" not in options


def test_legacy_full_claude_slug_uses_trusted_subscription(tmp_path, monkeypatch):
    monkeypatch.delenv("AI_RESEARCHER_MODELS_CONFIG", raising=False)
    selected = catalog.session_selection(tmp_path, "claude-opus-4-7")
    assert selected["id"] == "opus"
    options = catalog.claude_options(selected)
    assert options["model"] == "claude-opus-4-7"
    assert options["env"]["ANTHROPIC_BASE_URL"] == ""
    catalog.save_selection(tmp_path, selected)
    assert catalog.session_selection(tmp_path)["model_id"] == "claude-opus-4-7"


def test_codearkt_uses_its_endpoint_and_generation_parameters(configured, monkeypatch):
    path, data = configured
    model = data["models"]["lab"]
    model["runtime"] = "codearkt"
    model["base_url"] = "http://127.0.0.1:8000/v1"
    model["generation"].pop("thinking")
    data["defaults"] = {"codearkt": "lab"}
    path.write_text(yaml.safe_dump(data))
    routing = catalog.resolve_codearkt("lab", "low", strict=True)
    assert routing["model_name"] == "test-model"
    assert routing["base_url"].endswith("/v1")
    assert routing["params"] == {
        "reasoning_effort": "low",
        "max_completion_tokens": 4096,
    }
    model["generation"]["effort"] = {
        "choices": ["minimal", "xhigh"],
        "default": "xhigh",
    }
    path.write_text(yaml.safe_dump(data))
    assert (
        catalog.resolve_codearkt("lab", strict=True)["params"]["reasoning_effort"]
        == "xhigh"
    )


def test_title_uses_saved_external_model_and_effort(configured, tmp_path, monkeypatch):
    import asyncio

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import session_titles
    import claude_agent_sdk
    import run_research
    from holosophos import research_session

    received = []

    async def query(*, prompt, options):
        received.append(options)
        yield claude_agent_sdk.ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="stub",
            result="Название задачи",
        )

    monkeypatch.setattr(claude_agent_sdk, "query", query)
    monkeypatch.setattr(run_research, "resolve_cli_path", lambda: "/fixture/claude")
    work = tmp_path / "session"
    research_session.initialize_display_metadata(work, "Задача")
    catalog.save_selection(work, catalog.resolve("claude_code", "lab", "low"))
    asyncio.run(session_titles.generate_title_once(work, "Задача"))
    assert received[0].model == "test-model"
    assert received[0].effort == "low"
    assert received[0].env["ANTHROPIC_AUTH_TOKEN"] == "test-secret"
    assert research_session.read_marker(work, "title") == "Название задачи"
