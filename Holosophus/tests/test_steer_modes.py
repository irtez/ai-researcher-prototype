"""Unit tests for configurable steering (docs/plans/2026-08-13-steer-feedback-cc.md).

- cc_run_control: steer.json write/read/send round-trip, mode default/validation, read-once (SC6).
- driver: steer --now interrupts + delivers; steer --next delivers WITHOUT interrupt at the boundary;
  pause → steer → resume applies the feedback; inject alias still steers-now; empty payload ignored.
"""

import asyncio
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

import cc_run_control as ctl  # noqa: E402
import run_research  # noqa: E402


# --- control helpers (SC6) ----------------------------------------------------

def test_steer_in_valid_commands() -> None:
    assert ctl.CMD_STEER == "steer" and "steer" in ctl.VALID_COMMANDS


def test_steer_round_trip_default_mode(tmp_path: Path) -> None:
    ctl.write_steer(tmp_path, "use dataset B")
    s = ctl.read_steer(tmp_path)
    assert s == {"text": "use dataset B", "mode": "now"}
    assert ctl.read_steer(tmp_path) is None  # read-once


def test_steer_next_mode(tmp_path: Path) -> None:
    ctl.write_steer(tmp_path, "add an ablation", mode="next")
    assert ctl.read_steer(tmp_path) == {"text": "add an ablation", "mode": "next"}


def test_steer_invalid_mode_normalizes_to_now(tmp_path: Path) -> None:
    ctl.write_steer(tmp_path, "x", mode="whenever")
    assert ctl.read_steer(tmp_path)["mode"] == "now"


def test_steer_empty_text_is_none(tmp_path: Path) -> None:
    ctl.write_steer(tmp_path, "   ")
    assert ctl.read_steer(tmp_path) is None


def test_send_steer_writes_payload_and_command(tmp_path: Path) -> None:
    ctl.send_steer(tmp_path, "steer me", mode="next")
    assert ctl.poll_command(tmp_path) == "steer"
    assert ctl.read_steer(tmp_path) == {"text": "steer me", "mode": "next"}


def test_steer_session_isolated(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    ctl.write_steer(a, "for A")
    assert ctl.read_steer(b) is None


# --- driver FakeClient --------------------------------------------------------

class FakeMsg:
    def __init__(self, is_result: bool = False) -> None:
        self.is_result = is_result


class FakeClient:
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

    def release(self):  # end a blocked turn WITHOUT an interrupt (simulates a job finishing)
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
            await self._evt.wait()
            self._evt = None


def _render(msg, start, workdir):
    return getattr(msg, "is_result", False)


async def _drive(client, workdir):
    await asyncio.wait_for(
        run_research._drive_with_pause_control(
            client, "first", "sess", workdir, 0.0,
            render=_render, poll_interval=0.01, freeze=lambda wd: False, unfreeze=lambda wd: False,
        ),
        timeout=5.0,
    )


def test_steer_now_interrupts_and_delivers(tmp_path: Path) -> None:
    client = FakeClient([
        {"messages": [FakeMsg()], "block": True},               # blocked (long job)
        {"messages": [FakeMsg(is_result=True)], "block": False},  # after steer, completes
    ])

    async def scenario():
        async def operator():
            await asyncio.sleep(0.05)
            ctl.send_steer(tmp_path, "switch to dataset B", mode="now")

        op = asyncio.create_task(operator())
        await _drive(client, tmp_path)
        await op

    asyncio.run(scenario())
    assert client.interrupts >= 1, "now-mode must interrupt the blocked step (SC1)"
    assert client.queries[-1] == run_research.STEER_PREFIX + "switch to dataset B"
    assert len(client.queries) == 2


def test_steer_next_waits_for_boundary_no_interrupt(tmp_path: Path) -> None:
    client = FakeClient([
        {"messages": [FakeMsg()], "block": True},               # long job in flight
        {"messages": [FakeMsg(is_result=True)], "block": False},  # after boundary, completes
    ])

    async def scenario():
        async def operator():
            await asyncio.sleep(0.05)
            ctl.send_steer(tmp_path, "add an ablation when done", mode="next")
            await asyncio.sleep(0.1)  # give the poller time; it must NOT interrupt
            assert client.interrupts == 0, "next-mode must NOT interrupt the running step (SC2)"
            client.release()  # the job finishes on its own -> natural turn boundary

        op = asyncio.create_task(operator())
        await _drive(client, tmp_path)
        await op

    asyncio.run(scenario())
    assert client.interrupts == 0
    assert client.queries[-1] == run_research.STEER_PREFIX + "add an ablation when done"
    assert len(client.queries) == 2


def test_pause_then_steer_then_resume_applies_feedback(tmp_path: Path) -> None:
    client = FakeClient([
        {"messages": [FakeMsg()], "block": True},               # paused here
        {"messages": [FakeMsg(is_result=True)], "block": False},  # resumes with feedback, completes
    ])

    async def scenario():
        async def operator():
            await asyncio.sleep(0.05)
            ctl.send_command(tmp_path, "pause")   # interrupt → turn 1 ends → hold
            await asyncio.sleep(0.08)
            ctl.send_steer(tmp_path, "here is my full review: fix sections 2 and 4", mode="now")
            await asyncio.sleep(0.08)
            ctl.send_command(tmp_path, "resume")

        op = asyncio.create_task(operator())
        await _drive(client, tmp_path)
        await op

    asyncio.run(scenario())
    assert client.queries[-1] == run_research.STEER_PREFIX + "here is my full review: fix sections 2 and 4"


def test_inject_alias_still_steers_now(tmp_path: Path) -> None:
    client = FakeClient([
        {"messages": [FakeMsg()], "block": True},
        {"messages": [FakeMsg(is_result=True)], "block": False},
    ])

    async def scenario():
        async def operator():
            await asyncio.sleep(0.05)
            ctl.write_inject(tmp_path, "legacy inject path")
            ctl.send_command(tmp_path, "inject")

        op = asyncio.create_task(operator())
        await _drive(client, tmp_path)
        await op

    asyncio.run(scenario())
    assert client.interrupts >= 1
    assert client.queries[-1] == run_research.STEER_PREFIX + "legacy inject path"


def test_bare_steer_without_payload_ignored(tmp_path: Path) -> None:
    client = FakeClient([{"messages": [FakeMsg(is_result=True)], "block": False}])

    async def scenario():
        async def operator():
            await asyncio.sleep(0.02)
            ctl.send_command(tmp_path, "steer")  # no steer.json → empty

        op = asyncio.create_task(operator())
        await _drive(client, tmp_path)
        await op

    asyncio.run(scenario())
    assert client.queries == ["first"]
