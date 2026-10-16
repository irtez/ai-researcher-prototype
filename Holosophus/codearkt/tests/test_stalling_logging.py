import asyncio
import logging

import pytest

from codearkt.codeact import RunContext
from codearkt.event_bus import EventType


@pytest.mark.asyncio
async def test_run_llm_logs_stream_timeout(
    dummy_agent,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def fake_astream(*args, **kwargs):
        raise asyncio.TimeoutError
        yield

    run_context = RunContext(session_id="test-session", run_id="test-run")

    dummy_agent.llm.astream = fake_astream  # type: ignore[method-assign]

    with caplog.at_level(logging.ERROR, logger=dummy_agent.logger.name):
        with pytest.raises(asyncio.TimeoutError):
            await dummy_agent._run_llm(
                messages=[],
                run_context=run_context,
                excluded_stop_sequences=[],
                included_stop_sequences=[],
                event_type=EventType.OUTPUT,
            )

    assert "LLM stream timed out after" in caplog.text
    assert "during output" in caplog.text
    assert "agent agent" in caplog.text
