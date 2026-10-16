"""Drive scripts/run_research.py::_drive_with_pause_control with a fake SDK client.

Exercises the pause-aware control loop end-to-end without a live LLM (SC1/SC2/SC4):
- pause → interrupt the current turn and hold the process (SC1),
- resume → continue in-process with the RESUME_NUDGE (SC2),
- stop  → set the Tier-2 checkpoint flag and exit cleanly (SC4),
- natural completion → one turn, no interrupt.
Design: docs/plans/2026-08-12-pause-restart-cc.md.
"""

import asyncio
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

import cc_run_control as ctl  # noqa: E402
import run_research  # noqa: E402


class FakeMsg:
    def __init__(self, is_result: bool = False) -> None:
        self.is_result = is_result


class FakeClient:
    """Minimal ClaudeSDKClient stand-in. Each 'turn' is a list of messages; a blocking turn
    parks in receive_response() until interrupt() fires (simulating a long tool call)."""

    def __init__(self, turns: list) -> None:
        self._turns = turns
        self._i = 0
        self.queries: list = []
        self.interrupts = 0
        self._evt = None

    async def query(self, text, session_id=None):
        self.queries.append(text)

    async def interrupt(self):
        self.interrupts += 1
        if self._evt is not None:
            self._evt.set()

    async def receive_response(self):
        turn = self._turns[self._i]
        self._i += 1
        if turn.get("block"):
            self._evt = asyncio.Event()
        for m in turn["messages"]:
            yield m
        if turn.get("block"):
            await self._evt.wait()  # held until interrupt()
            self._evt = None


def _render(msg, start, workdir):
    return getattr(msg, "is_result", False)


async def _drive(client, workdir, first="first"):
    await asyncio.wait_for(
        run_research._drive_with_pause_control(
            client, first, "sess", workdir, 0.0, render=_render, poll_interval=0.01
        ),
        timeout=5.0,
    )


def test_natural_completion_one_turn(tmp_path: Path) -> None:
    client = FakeClient([{"messages": [FakeMsg(is_result=True)], "block": False}])
    asyncio.run(_drive(client, tmp_path))
    assert client.queries == ["first"]
    assert client.interrupts == 0


def test_pause_then_resume_continues_in_process(tmp_path: Path) -> None:
    client = FakeClient(
        [
            {"messages": [FakeMsg(), FakeMsg()], "block": True},      # turn 1 blocks (long job)
            {"messages": [FakeMsg(is_result=True)], "block": False},  # turn 2 after resume, completes
        ]
    )

    async def scenario():
        async def operator():
            await asyncio.sleep(0.05)
            ctl.send_command(tmp_path, "pause")
            await asyncio.sleep(0.08)
            ctl.send_command(tmp_path, "resume")

        op = asyncio.create_task(operator())
        await _drive(client, tmp_path)
        await op

    asyncio.run(scenario())
    assert client.interrupts >= 1, "pause must interrupt the blocked turn (SC1)"
    assert client.queries[0] == "first"
    assert client.queries[-1] == run_research.RESUME_NUDGE, "resume re-queries with the nudge (SC2)"
    # Tier-2 flag is cleared again after a soft resume.
    assert ctl.is_checkpoint_stop(tmp_path) is False


def test_stop_sets_checkpoint_flag_and_exits(tmp_path: Path) -> None:
    client = FakeClient([{"messages": [FakeMsg()], "block": True}])

    async def scenario():
        async def operator():
            await asyncio.sleep(0.05)
            ctl.send_command(tmp_path, "stop")

        op = asyncio.create_task(operator())
        await _drive(client, tmp_path)
        await op

    asyncio.run(scenario())
    assert client.interrupts >= 1
    assert client.queries == ["first"], "stop must not re-query (SC4)"
    assert ctl.is_checkpoint_stop(tmp_path) is True, "stop sets the Tier-2 checkpoint flag (SC4)"


def test_pause_freezes_and_resume_unfreezes(tmp_path: Path) -> None:
    client = FakeClient(
        [
            {"messages": [FakeMsg()], "block": True},               # blocked (long job)
            {"messages": [FakeMsg(is_result=True)], "block": False},
        ]
    )
    froze: list = []
    thawed: list = []

    def fake_freeze(wd):
        froze.append(wd)
        return True

    def fake_unfreeze(wd):
        thawed.append(wd)
        return True

    async def scenario():
        async def operator():
            await asyncio.sleep(0.05)
            ctl.send_command(tmp_path, "pause")
            await asyncio.sleep(0.08)
            ctl.send_command(tmp_path, "resume")

        op = asyncio.create_task(operator())
        await asyncio.wait_for(
            run_research._drive_with_pause_control(
                client, "first", "sess", tmp_path, 0.0,
                render=_render, poll_interval=0.01,
                freeze=fake_freeze, unfreeze=fake_unfreeze,
            ),
            timeout=5.0,
        )
        await op

    asyncio.run(scenario())
    assert froze, "pause must freeze the sandbox (Tier-1)"
    assert thawed, "resume must unfreeze the sandbox"


def test_inject_steers_and_continues(tmp_path: Path) -> None:
    """HITL inject (SC3): a mid-turn operator message interrupts, is delivered via query(), and
    the run continues (non-terminal)."""
    client = FakeClient(
        [
            {"messages": [FakeMsg()], "block": True},               # turn 1 blocks (long job)
            {"messages": [FakeMsg(is_result=True)], "block": False},  # turn 2 after inject, completes
        ]
    )

    async def scenario():
        async def operator():
            await asyncio.sleep(0.05)
            ctl.write_inject(tmp_path, "pivot to the linear baseline")  # payload first...
            ctl.send_command(tmp_path, "inject")                        # ...then the verb

        op = asyncio.create_task(operator())
        await _drive(client, tmp_path)
        await op

    asyncio.run(scenario())
    assert client.interrupts >= 1, "inject must interrupt the blocked turn to land promptly (SC3)"
    assert client.queries[0] == "first"
    assert client.queries[-1] == run_research.INJECT_PREFIX + "pivot to the linear baseline"
    assert len(client.queries) == 2, "non-terminal: delivered the steer, then continued to completion"


def test_bare_inject_without_payload_is_ignored(tmp_path: Path) -> None:
    """A `command=inject` with no inject_msg must not abort the turn or re-query (empty steer)."""
    client = FakeClient([{"messages": [FakeMsg(is_result=True)], "block": False}])

    async def scenario():
        async def operator():
            await asyncio.sleep(0.02)
            ctl.send_command(tmp_path, "inject")  # no write_inject → empty payload

        op = asyncio.create_task(operator())
        await _drive(client, tmp_path)
        await op

    asyncio.run(scenario())
    assert client.queries == ["first"], "empty inject must not re-query"


def test_stop_while_paused_exits(tmp_path: Path) -> None:
    client = FakeClient([{"messages": [FakeMsg()], "block": True}])

    async def scenario():
        async def operator():
            await asyncio.sleep(0.05)
            ctl.send_command(tmp_path, "pause")
            await asyncio.sleep(0.08)
            ctl.send_command(tmp_path, "stop")

        op = asyncio.create_task(operator())
        await _drive(client, tmp_path)
        await op

    asyncio.run(scenario())
    assert client.queries == ["first"], "no resume happened"
    assert ctl.is_checkpoint_stop(tmp_path) is True
