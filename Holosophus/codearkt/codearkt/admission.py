"""Cross-process admission ledger: one shared run-slot pool + a per-owner cap
counted across BOTH runner processes.

The two orchestrators run as separate OS processes with disjoint in-process
semaphores -- the CodeArkt server (``app:5055``) and the host-side Claude-Code
runner (``scripts/cc_runner.py``, ``:5061``). The team-login feature wants a
single "6 total, 3 per account" ceiling that does **not** distinguish the two
engines (user decision, see
``docs/plans/2026-07-05-ui-login-and-per-account-queue.md``). An in-process
semaphore can't express that, so admission state lives in a small JSON file in
the shared workdir, mutated under an ``fcntl`` lock -- the same shared-workdir
pattern ``dispatch.mle_kit_resources`` already uses for cross-process capacity
(``WORKSPACE_DIR/.holosophus_capacity/``).

``SessionDispatcher`` (``dispatch.py``) drives this: in ledger mode its
``acquire`` polls :meth:`AdmissionLedger.try_admit` until a slot frees. Both
processes construct a ledger pointing at the same file and read the same
``MAX_ACTIVE_SESSIONS`` / ``PER_OWNER_MAX_ACTIVE_SESSIONS`` from env, so the
count is genuinely unified.

Everything here is best-effort and **fails open**: on a filesystem/lock error
it admits rather than deadlocks the whole team (availability beats a strict cap
-- matching the "never 500 on /capacity" posture elsewhere). A run that crashes
without releasing is swept by a staleness prune so a killed process can't
permanently hold a slot.

Note: on macOS Docker Desktop, ``flock`` coherence across a host<->container
bind mount is best-effort; on the Linux production hosts it is coherent. Dev
typically runs a single engine at a time, so this is an accepted dev-only
limitation.
"""

import asyncio
import fcntl
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

# Sibling of mle_kit's cross-process capacity file under the same shared dir.
LEDGER_RELPATH = ".holosophus_capacity/admission.json"

DEFAULT_GLOBAL_LIMIT = 6
DEFAULT_PER_OWNER_LIMIT = 3

# A run cannot outlive EXEC_TIMEOUT (24h). An active entry older than this must
# be a crash-leak (a process died without releasing), so prune it -- otherwise a
# SIGKILL'd run would hold a slot forever. Generous margin above 24h.
DEFAULT_STALE_SEC = 26 * 60 * 60


def _workspace_dir() -> Path:
    return Path(os.environ.get("WORKSPACE_DIR", "workdir"))


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def global_limit() -> int:
    """Shared run-slot ceiling across both engines (``MAX_ACTIVE_SESSIONS``)."""
    return _env_int("MAX_ACTIVE_SESSIONS", DEFAULT_GLOBAL_LIMIT)


def per_owner_limit() -> int:
    """Per-account concurrent-run cap (``PER_OWNER_MAX_ACTIVE_SESSIONS``)."""
    return _env_int("PER_OWNER_MAX_ACTIVE_SESSIONS", DEFAULT_PER_OWNER_LIMIT)


def poll_interval_sec() -> float:
    try:
        return float(os.environ.get("HOLO_ADMISSION_POLL_SEC", "1.0"))
    except (TypeError, ValueError):
        return 1.0


def _stale_sec() -> int:
    return _env_int("HOLO_ADMISSION_STALE_SEC", DEFAULT_STALE_SEC)


class AdmissionLedger:
    """A file-backed, cross-process active/queued run ledger.

    ``path`` is resolved lazily from ``WORKSPACE_DIR`` when not given, so tests
    can set the env after construction (mirrors ``mle_kit_resources``). All
    mutation goes through :meth:`_locked` (an ``fcntl.LOCK_EX`` critical
    section on a sidecar ``.lock`` file).
    """

    def __init__(self, path: Optional[str] = None) -> None:
        self._override = Path(path) if path else None

    # --- paths -----------------------------------------------------------------
    def _ledger_path(self) -> Path:
        if self._override is not None:
            return self._override
        return _workspace_dir() / LEDGER_RELPATH

    def _lock_path(self) -> Path:
        ledger = self._ledger_path()
        return ledger.parent / (ledger.name + ".lock")

    @contextmanager
    def _locked(self) -> Iterator[None]:
        lock_path = self._lock_path()
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    # --- persistence -----------------------------------------------------------
    def _load(self) -> Dict[str, Any]:
        try:
            with open(self._ledger_path(), "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            data = {}
        active = data.get("active") if isinstance(data, dict) else None
        queued = data.get("queued") if isinstance(data, dict) else None
        return {
            "active": active if isinstance(active, dict) else {},
            "queued": queued if isinstance(queued, list) else [],
        }

    def _store(self, data: Dict[str, Any]) -> None:
        path = self._ledger_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.parent / (path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        os.replace(tmp, path)

    @staticmethod
    def _prune(data: Dict[str, Any], now: float) -> None:
        stale = _stale_sec()
        active: Dict[str, Any] = data["active"]
        for session_id in list(active.keys()):
            entry = active[session_id]
            ts = entry.get("ts", now) if isinstance(entry, dict) else now
            if now - ts > stale:
                active.pop(session_id, None)
        queued: List[Dict[str, Any]] = data["queued"]
        data["queued"] = [
            entry
            for entry in queued
            if isinstance(entry, dict) and now - entry.get("ts", now) <= stale
        ]

    @staticmethod
    def _owner_active(active: Dict[str, Any], owner: Optional[str]) -> int:
        return sum(1 for entry in active.values() if entry.get("owner") == owner)

    # --- public API ------------------------------------------------------------
    def try_admit(
        self,
        session_id: str,
        owner: Optional[str],
        engine: Optional[str],
        limit: int,
        owner_limit: Optional[int],
        now: Optional[float] = None,
    ) -> Tuple[bool, int, int]:
        """Attempt to claim a run-slot for ``session_id``.

        Returns ``(admitted, position, depth)``. When not admitted the session
        is recorded in the shared queue and ``position`` (1-based) / ``depth``
        describe where it sits. Idempotent: re-calling for an already-active
        session re-admits it. Fails **open** (admits) on any I/O error.
        """
        now = time.time() if now is None else now
        try:
            with self._locked():
                data = self._load()
                self._prune(data, now)
                active: Dict[str, Any] = data["active"]
                queued: List[Dict[str, Any]] = data["queued"]

                if session_id in active:
                    self._store(data)
                    return (True, 0, 0)

                room_global = len(active) < limit
                room_owner = (
                    owner_limit is None
                    or owner_limit <= 0
                    or self._owner_active(active, owner) < owner_limit
                )
                if room_global and room_owner:
                    active[session_id] = {"owner": owner, "engine": engine, "ts": now}
                    data["queued"] = [
                        entry for entry in queued if entry.get("session_id") != session_id
                    ]
                    self._store(data)
                    return (True, 0, 0)

                # No slot: ensure a queue entry, refreshing its ts so a live
                # waiter is never mistaken for a stale (dead) one on prune.
                existing = next((e for e in queued if e.get("session_id") == session_id), None)
                if existing is not None:
                    existing["ts"] = now
                else:
                    queued.append(
                        {"session_id": session_id, "owner": owner, "engine": engine, "ts": now}
                    )
                position = next(
                    (i for i, e in enumerate(queued, 1) if e.get("session_id") == session_id),
                    len(queued),
                )
                depth = len(queued)
                self._store(data)
                return (False, position, depth)
        except Exception:
            # Fail open: a broken ledger must not block every run for everyone.
            return (True, 0, 0)

    def release(self, session_id: str, now: Optional[float] = None) -> None:
        """Free ``session_id``'s slot (and drop any queue entry). Never raises."""
        now = time.time() if now is None else now
        try:
            with self._locked():
                data = self._load()
                self._prune(data, now)
                data["active"].pop(session_id, None)
                data["queued"] = [
                    entry for entry in data["queued"] if entry.get("session_id") != session_id
                ]
                self._store(data)
        except Exception:
            return

    def snapshot(
        self,
        owner: Optional[str] = None,
        limit: Optional[int] = None,
        owner_limit: Optional[int] = None,
        now: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Capacity readout in the ``{active, limit, queued}`` shape ``/capacity``
        reports, plus ``owner_active``/``per_owner_limit`` when ``owner`` is
        given. Read-only; stale entries are pruned in-memory for accurate
        counts but not rewritten here.
        """
        now = time.time() if now is None else now
        try:
            with self._locked():
                data = self._load()
                self._prune(data, now)
        except Exception:
            data = {"active": {}, "queued": []}
        active = data["active"]
        result: Dict[str, Any] = {
            "active": len(active),
            "limit": limit,
            "queued": len(data["queued"]),
        }
        if owner is not None:
            result["owner_active"] = self._owner_active(active, owner)
            result["per_owner_limit"] = owner_limit
        return result


async def try_admit_async(
    ledger: AdmissionLedger,
    session_id: str,
    owner: Optional[str],
    engine: Optional[str],
    limit: int,
    owner_limit: Optional[int],
) -> Tuple[bool, int, int]:
    """Run the (blocking, ``flock``-guarded) admission check off the event loop."""
    return await asyncio.to_thread(ledger.try_admit, session_id, owner, engine, limit, owner_limit)


async def release_async(ledger: AdmissionLedger, session_id: str) -> None:
    await asyncio.to_thread(ledger.release, session_id)
