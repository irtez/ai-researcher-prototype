"""Exercise the CodeArkt client path with a fake HTTP client, never an LLM."""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "codearkt"))
from codearkt import llm
from codearkt.settings import Settings


def test_openrouter_key_is_optional_until_a_connection_is_selected():
    settings = Settings(_env_file=None, OPENROUTER_API_KEY="")
    assert settings.OPENROUTER_API_KEY == ""
    llm.LLM("configured-model", api_key="", config_resolver=lambda *_: {})
    with pytest.raises(AssertionError, match="LLM API key is required"):
        llm.LLM("legacy-model", api_key="")


def test_routing_pins_connection_and_reserves_output_budget(monkeypatch):
    seen = {}

    class Stream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

        async def close(self):
            pass

    class Client:
        def __init__(self, **kwargs):
            seen["connection"] = kwargs
            self.chat = self.completions = self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def create(self, **kwargs):
            seen["request"] = kwargs
            return Stream()

    monkeypatch.setattr(llm, "AsyncOpenAI", Client)
    route = {
        "model_name": "configured-model",
        "base_url": "http://model.invalid/v1",
        "api_key": "fixture-key",
        "params": {"reasoning_effort": "low", "max_completion_tokens": 8192},
        "context_window": 32768,
    }
    model = llm.LLM("old-model", api_key="old-key", max_completion_tokens=16000)

    def trim(messages, context_window=None):
        seen["input_budget"] = context_window
        return messages

    monkeypatch.setattr(model, "_trim_messages", trim)

    async def scenario():
        async for _ in model.astream(
            [llm.ChatMessage(role="user", content="Fixture")], model_configuration=route
        ):
            pass

    asyncio.run(scenario())
    assert seen["connection"]["base_url"] == "http://model.invalid/v1"
    assert seen["connection"]["api_key"] == "fixture-key"
    assert seen["request"]["model"] == "configured-model"
    assert seen["request"]["reasoning_effort"] == "low"
    assert seen["input_budget"] == 24576
    assert "model_configuration" not in seen["request"]
    assert model._base_url != route["base_url"]  # No shared-instance mutation.
