# pyright: reportMissingImports=false

import asyncio
import sys
import time
from pathlib import Path
from typing import Any

import pytest

TIME_TO_HANG = 600

CODEARKT_ROOT = Path(__file__).resolve().parents[2] / "codearkt"
if str(CODEARKT_ROOT) not in sys.path:
    sys.path.insert(0, str(CODEARKT_ROOT))

import codearkt.llm as llm_module
from codearkt.llm import ChatMessage, LLM


class HangingStream:
    def __init__(self) -> None:
        self.close_called = False

    def __aiter__(self) -> "HangingStream":
        return self

    async def __anext__(self) -> Any:
        await asyncio.sleep(TIME_TO_HANG)
        raise StopAsyncIteration

    async def close(self) -> None:
        self.close_called = True


class FakeCompletions:
    def __init__(self, stream: HangingStream) -> None:
        self.stream = stream

    async def create(self, **_: Any) -> HangingStream:
        return self.stream


class FakeChat:
    def __init__(self, stream: HangingStream) -> None:
        self.completions = FakeCompletions(stream)


class FakeAsyncOpenAI:
    def __init__(self, *, stream: HangingStream, **_: Any) -> None:
        self.chat = FakeChat(stream)

    async def __aenter__(self) -> "FakeAsyncOpenAI":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


async def _read_first_chunk(generator: Any) -> Any:
    try:
        return await anext(generator)
    finally:
        await generator.aclose()


@pytest.mark.asyncio
async def test_llm_astream_times_out_when_provider_goes_silent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = HangingStream()
    monkeypatch.setattr(
        llm_module,
        "AsyncOpenAI",
        lambda **kwargs: FakeAsyncOpenAI(stream=stream, **kwargs),
    )

    llm = LLM(
        model_name="test-model",
        api_key="test-key",
        base_url="https://example.invalid/v1",
    )
    generator = llm.astream(
        [ChatMessage(role="user", content="Say hello")],
        stream_chunk_timeout_seconds=0.01,
    )

    started_at = time.monotonic()
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(_read_first_chunk(generator), timeout=0.2)
    elapsed = time.monotonic() - started_at

    assert elapsed < 0.1, (
        "LLM.astream should enforce its own stream inactivity timeout. "
        "This failure took too long, which means the caller-side timeout fired instead."
    )
    assert stream.close_called, "LLM.astream should close the provider stream on timeout."
