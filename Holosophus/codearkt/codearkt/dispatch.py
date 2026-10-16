"""In-process admission control + host capacity metrics for concurrent runs.

SC4/SC5/SC6 (multi-user concurrent UI, `[B]` in
`docs/plans/2026-06-30-multiuser-concurrent-ui.md`): gates ROOT agent runs
behind a bounded `asyncio.Semaphore` so a burst of concurrent sessions queues
with a live position instead of piling straight onto the host, and exposes
the active/queued bookkeeping + host load for `GET /capacity`.

Only ROOT runs are ever admitted here -- see `server.py`'s root-vs-nested
detection in `agent_tool` (`session_id not in event_bus.root_agent_name`). A
nested delegate call (e.g. `agent__mle_solver`) reuses the *same*
`session_id` as its root run; if it also waited on this semaphore, a manager
holding a run-slot could deadlock waiting on a solver slot held by other
sessions' managers.

The two resource-class queues (`gpu`, `local_exec`) and the mle_kit GPU lease
from the design doc are a *separate*, later change -- this module only
implements the `session` class (the app-wide `MAX_ACTIVE_SESSIONS` run-slot
dispatcher).
"""

import asyncio
import json
import os
from typing import Any, Awaitable, Callable, Dict, List, Optional

from codearkt.admission import (
    AdmissionLedger,
    poll_interval_sec,
    release_async,
    try_admit_async,
)

# session_id, position (1-based), queue depth
QueuedCallback = Callable[[str, int, int], Awaitable[None]]
# session_id
RunningCallback = Callable[[str], Awaitable[None]]

# Module-level so tests can monkeypatch it to a fixture file (or a path
# guaranteed not to exist, to exercise the no-/proc/meminfo fallback).
MEMINFO_PATH = "/proc/meminfo"


class SessionDispatcher:
    """Tracks active/queued root runs and gates them behind a semaphore.

    Single asyncio event loop only, matching the rest of codearkt's
    concurrency model (e.g. `server.run_batch`'s semaphore): the
    `asyncio.Lock` makes the check-then-act around `_active`/`_queued`
    atomic across the `await` points inside `acquire`/`release`, not across
    OS threads.
    """

    def __init__(
        self,
        limit: int,
        *,
        ledger: Optional[AdmissionLedger] = None,
        per_owner_limit: Optional[int] = None,
        engine: Optional[str] = None,
        poll_interval: Optional[float] = None,
    ) -> None:
        self.limit = limit
        # Ledger mode (team login, cross-process): when `ledger` is set the
        # in-process semaphore below is unused and admission is a shared
        # file-backed pool enforcing `limit` (global) + `per_owner_limit` (per
        # account) across BOTH runner processes. When `ledger` is None the
        # dispatcher is the original single-process asyncio.Semaphore gate
        # (still used as-is by the GPU dispatcher and every existing caller).
        self._ledger = ledger
        self._per_owner_limit = per_owner_limit
        self._engine = engine
        self._poll_interval = poll_interval if poll_interval is not None else poll_interval_sec()
        self._semaphore = asyncio.Semaphore(limit)
        self._lock = asyncio.Lock()
        self._active: set[str] = set()
        self._queued: List[str] = []

    def snapshot(self, owner: Optional[str] = None) -> Dict[str, Any]:
        """Current capacity, in the shape `GET /capacity` reports verbatim.

        In ledger mode the counts come from the shared cross-process file; when
        `owner` is given the readout also carries `owner_active`/
        `per_owner_limit` for that account (used by `/capacity`'s `yours`).
        """
        if self._ledger is not None:
            return self._ledger.snapshot(
                owner=owner, limit=self.limit, owner_limit=self._per_owner_limit
            )
        return {
            "active": len(self._active),
            "limit": self.limit,
            "queued": len(self._queued),
        }

    async def acquire(
        self,
        session_id: str,
        owner: Optional[str] = None,
        on_queued: Optional[QueuedCallback] = None,
        on_running: Optional[RunningCallback] = None,
        on_position: Optional[QueuedCallback] = None,
    ) -> None:
        """Admit `session_id`, blocking (queued) if no run-slot is free.

        `on_queued` fires once, synchronously with the queue-position
        bookkeeping, if `session_id` had to wait. `on_running` fires once a
        slot is actually held, whether or not it had to wait. `owner` /
        `on_position` are only used in ledger mode (the per-account cap and the
        self-published position updates while polling); plain mode ignores
        them (position updates come from `release`'s `on_position`).
        """
        if self._ledger is not None:
            await self._acquire_ledger(session_id, owner, on_queued, on_running, on_position)
            return
        async with self._lock:
            has_free_slot = len(self._active) < self.limit
            if not has_free_slot:
                self._queued.append(session_id)
                position = len(self._queued)
                depth = len(self._queued)

        if not has_free_slot and on_queued is not None:
            await on_queued(session_id, position, depth)

        try:
            await self._semaphore.acquire()
        except asyncio.CancelledError:
            # A queued run can be cancelled (e.g. explicit /agents/cancel)
            # before ever reaching the front of the queue -- drop its
            # bookkeeping entry so snapshot() doesn't leak a phantom wait.
            async with self._lock:
                if session_id in self._queued:
                    self._queued.remove(session_id)
            raise

        async with self._lock:
            if session_id in self._queued:
                self._queued.remove(session_id)
            self._active.add(session_id)

        if on_running is not None:
            await on_running(session_id)

    async def _acquire_ledger(
        self,
        session_id: str,
        owner: Optional[str],
        on_queued: Optional[QueuedCallback],
        on_running: Optional[RunningCallback],
        on_position: Optional[QueuedCallback],
    ) -> None:
        """Ledger-mode admission: poll the shared file until a slot frees.

        Unlike the semaphore path there is no cross-process wakeup, so a queued
        run re-checks every `poll_interval` and re-publishes its OWN position
        (its stream lives in this process) rather than relying on the releaser
        -- the releaser may be the other runner process entirely.
        """
        admitted, position, depth = await try_admit_async(
            self._ledger,  # type: ignore[arg-type]
            session_id,
            owner,
            self._engine,
            self.limit,
            self._per_owner_limit,
        )
        if admitted:
            if on_running is not None:
                await on_running(session_id)
            return

        if on_queued is not None:
            await on_queued(session_id, position, depth)
        last_position = position
        try:
            while True:
                await asyncio.sleep(self._poll_interval)
                admitted, position, depth = await try_admit_async(
                    self._ledger,  # type: ignore[arg-type]
                    session_id,
                    owner,
                    self._engine,
                    self.limit,
                    self._per_owner_limit,
                )
                if admitted:
                    if on_running is not None:
                        await on_running(session_id)
                    return
                if position != last_position and on_position is not None:
                    await on_position(session_id, position, depth)
                last_position = position
        except asyncio.CancelledError:
            # Queued run cancelled (e.g. /agents/cancel) before it got a slot --
            # drop its ledger queue entry so it doesn't leak a phantom wait.
            await release_async(self._ledger, session_id)  # type: ignore[arg-type]
            raise

    async def release(
        self,
        session_id: str,
        on_position: Optional[QueuedCallback] = None,
    ) -> None:
        """Free `session_id`'s run-slot and wake the next queued waiter.

        `on_position` (optional) is re-published for every session still in
        the queue after this release, so their reported position/depth stays
        current -- best-effort UX polish, not required for correctness (the
        semaphore itself guarantees FIFO admission).
        """
        if self._ledger is not None:
            # Cross-process: queued waiters re-publish their own position from
            # their poll loop (`_acquire_ledger`), so there is nothing to
            # republish here -- just free the shared slot.
            await release_async(self._ledger, session_id)
            return

        async with self._lock:
            self._active.discard(session_id)
            still_queued = list(self._queued)

        self._semaphore.release()

        if on_position is not None:
            for index, queued_session_id in enumerate(still_queued, start=1):
                await on_position(queued_session_id, index, len(still_queued))


def host_metrics() -> Dict[str, Any]:
    """Best-effort host load snapshot for `GET /capacity`. Never raises."""
    try:
        load1, load5, load15 = os.getloadavg()
    except OSError:
        load1 = load5 = load15 = 0.0
    return {
        "load1": load1,
        "load5": load5,
        "load15": load15,
        "cpu_count": os.cpu_count() or 0,
        "mem_used_pct": mem_used_pct(),
    }


def mem_used_pct() -> Optional[float]:
    """Percentage of RAM in use, read from `/proc/meminfo` (Linux only).

    Stdlib-only (no `psutil`). Returns `None` when the file is absent (e.g.
    a macOS dev host outside the app's Linux container) or malformed.
    """
    try:
        totals: Dict[str, int] = {}
        with open(MEMINFO_PATH, "r", encoding="utf-8") as meminfo_file:
            for line in meminfo_file:
                key, _, rest = line.partition(":")
                if key in ("MemTotal", "MemAvailable"):
                    totals[key] = int(rest.strip().split()[0])
        mem_total = totals.get("MemTotal", 0)
        mem_available = totals.get("MemAvailable")
        if mem_total <= 0 or mem_available is None:
            return None
        return round(100.0 * (mem_total - mem_available) / mem_total, 1)
    except (OSError, ValueError, IndexError):
        return None


# mle_kit writes its GPU / local_exec lease snapshot here (relative to the shared
# WORKSPACE_DIR mount); the app reads it into /capacity so the UI can show both
# resource-class queues without a cross-process RPC.
MLE_KIT_STATUS_RELPATH = ".holosophus_capacity/mle_kit.json"


def _zero_resource_class() -> Dict[str, int]:
    return {"busy": 0, "waiting": 0, "slots": 0}


def mle_kit_gpu_telemetry() -> Optional[Dict[str, Any]]:
    """Read mle_kit's per-card GPU occupancy from the same shared status file.

    Deliberately separate from ``mle_kit_resources``: that one is the fixed
    three-int lease contract the UI has always consumed, while this is an
    optional observability block that may be absent entirely (an older mle_kit)
    or half-null (a side not probed yet). Returns ``None`` when unavailable so
    ``/capacity`` can simply omit the key rather than invent zeros -- "unknown"
    and "nothing free" must not look alike to the UI.
    """
    try:
        workspace = os.environ.get("WORKSPACE_DIR", "workdir")
        path = os.path.join(workspace, *MLE_KIT_STATUS_RELPATH.split("/"))
        with open(path, "r", encoding="utf-8") as status_file:
            data = json.load(status_file)
        telemetry = data.get("gpu_telemetry")
        return telemetry if isinstance(telemetry, dict) else None
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def mle_kit_resources() -> Dict[str, Dict[str, int]]:
    """Read mle_kit's GPU / local_exec lease snapshot from the shared workdir.

    Best-effort cross-process read (mle_kit is a separate container writing
    ``<workspace>/.holosophus_capacity/mle_kit.json`` atomically). A missing or
    malformed file — mle_kit not up yet, or mid-write — yields zeroed classes
    rather than raising, so /capacity never 500s on it.
    """
    default = {"gpu": _zero_resource_class(), "local_exec": _zero_resource_class()}
    try:
        workspace = os.environ.get("WORKSPACE_DIR", "workdir")
        path = os.path.join(workspace, *MLE_KIT_STATUS_RELPATH.split("/"))
        with open(path, "r", encoding="utf-8") as status_file:
            data = json.load(status_file)
        result: Dict[str, Dict[str, int]] = {}
        for resource_class in ("gpu", "local_exec"):
            raw = data.get(resource_class) or {}
            result[resource_class] = {
                "busy": int(raw.get("busy", 0)),
                "waiting": int(raw.get("waiting", 0)),
                "slots": int(raw.get("slots", 0)),
            }
        return result
    except (OSError, ValueError, TypeError, AttributeError):
        return default
