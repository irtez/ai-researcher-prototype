"""Cross-process admission ledger + ledger-mode SessionDispatcher.

Covers the team-login capacity model (see
docs/plans/2026-07-05-ui-login-and-per-account-queue.md): one shared run-slot
pool + a per-account cap, counted across processes. "Two processes" is
simulated by two SessionDispatcher / AdmissionLedger instances pointing at the
same ledger file.
"""

import asyncio
from pathlib import Path
from typing import Callable, List, Tuple

import pytest

from codearkt.admission import AdmissionLedger
from codearkt.dispatch import SessionDispatcher


def _ledger(tmp_path: Path) -> AdmissionLedger:
    return AdmissionLedger(path=str(tmp_path / ".holosophus_capacity" / "admission.json"))


def _dispatcher(
    ledger: AdmissionLedger, limit: int, per_owner_limit: int, engine: str = "codearkt"
) -> SessionDispatcher:
    return SessionDispatcher(
        limit,
        ledger=ledger,
        per_owner_limit=per_owner_limit,
        engine=engine,
        poll_interval=0.01,
    )


# --- raw ledger ---------------------------------------------------------------


def test_ledger_admits_up_to_global_limit(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    assert ledger.try_admit("a", "u1", "codearkt", limit=2, owner_limit=3) == (True, 0, 0)
    assert ledger.try_admit("b", "u2", "codearkt", limit=2, owner_limit=3) == (True, 0, 0)
    # Third exceeds the global cap -> queued at position 1.
    admitted, position, depth = ledger.try_admit("c", "u3", "codearkt", limit=2, owner_limit=3)
    assert admitted is False
    assert (position, depth) == (1, 1)
    assert ledger.snapshot(limit=2) == {"active": 2, "limit": 2, "queued": 1}


def test_ledger_per_owner_cap_blocks_even_with_global_room(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    # Global room is ample (6) but per-owner cap is 1.
    assert ledger.try_admit("a", "u1", "codearkt", limit=6, owner_limit=1) == (True, 0, 0)
    admitted, _, _ = ledger.try_admit("b", "u1", "codearkt", limit=6, owner_limit=1)
    assert admitted is False  # u1 already at their personal cap
    # A different owner still gets in.
    assert ledger.try_admit("c", "u2", "codearkt", limit=6, owner_limit=1) == (True, 0, 0)

    snap = ledger.snapshot(owner="u1", limit=6, owner_limit=1)
    assert snap["active"] == 2
    assert snap["owner_active"] == 1
    assert snap["per_owner_limit"] == 1


def test_ledger_release_frees_slot(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.try_admit("a", "u1", "codearkt", limit=1, owner_limit=3)
    assert ledger.try_admit("b", "u2", "codearkt", limit=1, owner_limit=3)[0] is False
    ledger.release("a")
    assert ledger.try_admit("b", "u2", "codearkt", limit=1, owner_limit=3) == (True, 0, 0)


def test_ledger_try_admit_is_idempotent(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    assert ledger.try_admit("a", "u1", "codearkt", limit=1, owner_limit=3) == (True, 0, 0)
    # Re-admitting an already-active session doesn't double-count.
    assert ledger.try_admit("a", "u1", "codearkt", limit=1, owner_limit=3) == (True, 0, 0)
    assert ledger.snapshot(limit=1)["active"] == 1


def test_ledger_prunes_stale_active_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOLO_ADMISSION_STALE_SEC", "100")
    ledger = _ledger(tmp_path)
    # Admit "old" far in the past; a fresh admit should see it as crash-leaked.
    ledger.try_admit("old", "u1", "codearkt", limit=1, owner_limit=3, now=1000.0)
    admitted, _, _ = ledger.try_admit("new", "u2", "codearkt", limit=1, owner_limit=3, now=2000.0)
    assert admitted is True  # "old" pruned (age 1000 > stale 100), slot freed


def test_ledger_shared_file_across_two_instances(tmp_path: Path) -> None:
    """Two ledgers on the same file share the count (the cross-process case)."""
    path = str(tmp_path / ".holosophus_capacity" / "admission.json")
    proc_a = AdmissionLedger(path=path)
    proc_b = AdmissionLedger(path=path)
    assert proc_a.try_admit("a", "u1", "codearkt", limit=1, owner_limit=3) == (True, 0, 0)
    # The OTHER instance sees the slot as taken.
    assert proc_b.try_admit("b", "u2", "claude_code", limit=1, owner_limit=3)[0] is False
    proc_a.release("a")
    assert proc_b.try_admit("b", "u2", "claude_code", limit=1, owner_limit=3) == (True, 0, 0)


# --- ledger-mode dispatcher ---------------------------------------------------


async def test_dispatcher_ledger_mode_queues_then_admits_on_release(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    dispatcher = _dispatcher(ledger, limit=2, per_owner_limit=3)

    queued: List[Tuple[str, int, int]] = []
    running: List[str] = []

    async def on_queued(sid: str, position: int, depth: int) -> None:
        queued.append((sid, position, depth))

    async def on_running(sid: str) -> None:
        running.append(sid)

    await dispatcher.acquire("a", owner="u1", on_running=on_running)
    await dispatcher.acquire("b", owner="u2", on_running=on_running)
    assert running == ["a", "b"]
    assert dispatcher.snapshot() == {"active": 2, "limit": 2, "queued": 0}

    c_task = asyncio.create_task(
        dispatcher.acquire("c", owner="u3", on_queued=on_queued, on_running=on_running)
    )
    # Let it run its first (blocked) admission attempt and queue.
    await asyncio.wait_for(_until(lambda: queued), timeout=2)
    assert queued == [("c", 1, 1)]

    await dispatcher.release("a")
    await asyncio.wait_for(c_task, timeout=2)
    assert "c" in running
    assert dispatcher.snapshot() == {"active": 2, "limit": 2, "queued": 0}

    await dispatcher.release("b")
    await dispatcher.release("c")


async def test_dispatcher_ledger_mode_per_owner_queue(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    # Global 6 (ample), per-owner 1: a 2nd run for the same owner must queue.
    dispatcher = _dispatcher(ledger, limit=6, per_owner_limit=1)

    running: List[str] = []
    queued: List[Tuple[str, int, int]] = []

    async def on_running(sid: str) -> None:
        running.append(sid)

    async def on_queued(sid: str, position: int, depth: int) -> None:
        queued.append((sid, position, depth))

    await dispatcher.acquire("s1", owner="u1", on_running=on_running)
    s2_task = asyncio.create_task(
        dispatcher.acquire("s2", owner="u1", on_queued=on_queued, on_running=on_running)
    )
    await asyncio.wait_for(_until(lambda: queued), timeout=2)
    assert queued and queued[0][0] == "s2"  # u1 at personal cap despite free global slots

    # A different owner is admitted immediately.
    await dispatcher.acquire("s3", owner="u2", on_running=on_running)
    assert "s3" in running

    await dispatcher.release("s1")
    await asyncio.wait_for(s2_task, timeout=2)
    assert "s2" in running

    for sid in ("s2", "s3"):
        await dispatcher.release(sid)


async def test_dispatcher_ledger_mode_cancel_while_queued(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    dispatcher = _dispatcher(ledger, limit=1, per_owner_limit=3)

    await dispatcher.acquire("a", owner="u1")
    b_task = asyncio.create_task(dispatcher.acquire("b", owner="u2"))
    await asyncio.wait_for(_until(lambda: dispatcher.snapshot()["queued"] == 1), timeout=2)

    b_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await b_task

    # Cancelling a queued waiter must not leak its ledger queue entry.
    await asyncio.wait_for(_until(lambda: dispatcher.snapshot()["queued"] == 0), timeout=2)
    await dispatcher.release("a")


async def _until(predicate: Callable[[], bool]) -> None:
    while not predicate():
        await asyncio.sleep(0.01)
