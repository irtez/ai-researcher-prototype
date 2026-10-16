import contextlib
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from mle_kit_mcp.files import get_workspace_dir
from mle_kit_mcp.settings import settings

logger = logging.getLogger(__name__)

STATUS_DIR_NAME = ".holosophus_capacity"
STATUS_FILE_NAME = "mle_kit.json"
POOL_FILE_NAME = "gpu_pool.json"

_status_write_lock = threading.Lock()
_pool_write_lock = threading.Lock()


def _pool_path() -> Path:
    return get_workspace_dir() / STATUS_DIR_NAME / POOL_FILE_NAME


def read_gpu_pool() -> Optional[List[int]]:
    """Card indices the UI has reserved for Holosophus, or None if unset.

    This file is the channel between the UI backend and mle_kit. They share the
    workspace volume but nothing else -- mle_kit speaks MCP and has no HTTP
    surface -- so a small JSON file is the whole protocol. Read on every
    allocation rather than cached, which is what makes the setting take effect
    without restarting mle_kit.

    Anything unreadable, malformed, or empty means *no override*, so the
    ``SELECTEL_GPU_INDICES`` env default still applies: a bad write can never
    strand the allocator with an empty pool.
    """
    raw = _read_pool_file()
    indices = raw.get("indices")
    if not isinstance(indices, list):
        return None
    parsed = [int(i) for i in indices if isinstance(i, (int, str)) and str(i).strip().isdigit()]
    return parsed or None


def _read_pool_file() -> Dict[str, Any]:
    try:
        raw = json.loads(_pool_path().read_text())
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def read_gpu_forced() -> Optional[int]:
    """A card index the operator pinned by hand, bypassing the freeness check.

    The automatic allocator only hands out cards that are *idle*, which on a
    shared host can mean "never". This is the manual escape hatch: an explicit
    decision to sit on a card that already has work on it. It deliberately
    overrides both the freeness rule and the pool, because the whole point is to
    do something the automatic rules refuse.
    """
    forced = _read_pool_file().get("forced")
    if isinstance(forced, bool) or not isinstance(forced, int):
        return None
    return forced if forced >= 0 else None


def write_gpu_pool(indices: Optional[List[int]], forced: Optional[int] = None) -> None:
    """Persist the allocatable card set and the manual pin (``None`` clears)."""
    with _pool_write_lock:
        payload: Dict[str, Any] = {
            "indices": sorted({int(i) for i in indices}) if indices else [],
            "forced": int(forced) if forced is not None and int(forced) >= 0 else None,
        }
        path = _pool_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f".{POOL_FILE_NAME}.tmp")
        tmp_path.write_text(json.dumps(payload))
        os.replace(tmp_path, path)


class ResourceLease:
    """A pool-ready in-process resource lease.

    Backed by `threading.Semaphore`, not `asyncio.Lock`: mle_kit tools are
    synchronous `def`s registered via FastMCP `server.add_tool(...)`, so
    concurrent calls can land on different worker threads rather than a
    single event loop -- only a threading primitive actually serializes
    them. `slots` is how many callers may hold the lease at once; today it
    is 1 for the single GPU box, but extending to a small pool later is
    just a bigger `slots`.
    """

    def __init__(self, name: str, slots: int) -> None:
        self.name = name
        self.slots = slots
        self._semaphore = threading.Semaphore(slots)
        self._state_lock = threading.Lock()
        self._busy = 0
        self._waiting = 0

    @contextlib.contextmanager
    def hold(self) -> Iterator[None]:
        """Acquire one slot for the duration of the `with` block.

        A caller that has to wait is visible via `snapshot()["waiting"]`;
        once admitted it moves to `snapshot()["busy"]`.
        """
        with self._state_lock:
            self._waiting += 1
        write_status()
        self._semaphore.acquire()
        with self._state_lock:
            self._waiting -= 1
            self._busy += 1
        write_status()
        try:
            yield
        finally:
            self._semaphore.release()
            with self._state_lock:
                self._busy -= 1
            write_status()

    def snapshot(self) -> Dict[str, int]:
        with self._state_lock:
            return {"busy": self._busy, "waiting": self._waiting, "slots": self.slots}

    def resize(self, slots: int) -> None:
        """Adjust how many callers may hold this lease at once.

        A shared multi-GPU host's free-card count is only known after a live
        probe, so ``GPU_LEASE`` is built at import with the static single-box
        default and lifted to the discovered ceiling here (see
        ``set_gpu_slots``). Growing releases the extra permits; shrinking is
        best-effort -- it reclaims only permits not currently held, so an active
        caller is never yanked off its card mid-call, and ``slots`` is set to
        what was actually reclaimed.
        """
        target = max(0, int(slots))
        with self._state_lock:
            delta = target - self.slots
            if delta > 0:
                for _ in range(delta):
                    self._semaphore.release()
                self.slots = target
            elif delta < 0:
                reclaimed = 0
                for _ in range(-delta):
                    if not self._semaphore.acquire(blocking=False):
                        break
                    reclaimed += 1
                self.slots -= reclaimed


GPU_LEASE = ResourceLease("gpu", settings.GPU_SLOTS)
LOCAL_EXEC_LEASE = ResourceLease("local_exec", settings.LOCAL_EXEC_SLOTS)


def set_gpu_slots(slots: int) -> None:
    """Reconcile the GPU capacity ceiling once free-card discovery has run.

    A shared multi-GPU host's allocatable-card count is not known at import
    time (it needs a live occupancy probe), so ``GPU_LEASE`` is built with the
    static single-box default and lifted here to the dynamic free-card ceiling.
    ``gpu.slots`` in the snapshot then reports the real ceiling instead of a
    hardcoded 1; the JSON shape is unchanged. Idempotent, safe to call after
    each discovery. Refreshes the status file.
    """
    GPU_LEASE.resize(slots)
    write_status()


def _gpu_snapshot() -> Dict[str, Any]:
    """Registry-sourced GPU capacity snapshot in the {busy,waiting,slots} shape.

    The GPU class exports *sessions holding a card* (per-session GPU pinning),
    not per-call semaphore occupancy: ``busy`` is the number of sessions
    holding a leased card index and ``waiting`` the number blocked on an
    exhausted pool -- both from ``gpu_registry`` -- while ``slots`` is
    ``GPU_LEASE``'s (possibly discovery-lifted) ceiling. ``GPU_LEASE`` stays the
    in-process serialization primitive for ``run_command`` but no longer defines
    ``gpu.busy``. The shape matches ``ResourceLease.snapshot()`` so the
    ``mle_kit.json`` / ``/capacity`` contract is unchanged. Falls back to
    ``GPU_LEASE``'s own snapshot if the registry is unavailable, so the file is
    never worse than before.
    """
    try:
        from mle_kit_mcp import gpu_registry

        return {
            "busy": gpu_registry.held_session_count(),
            "waiting": gpu_registry.waiting_session_count(),
            "slots": GPU_LEASE.slots,
        }
    except Exception:
        return GPU_LEASE.snapshot()


def _telemetry_snapshot() -> Dict[str, Any]:
    """Last known per-card occupancy, for the UI's usage view.

    Additive and always present in the file, so a consumer never has to branch
    on the key existing; ``None`` sides simply mean "not probed yet". Reads the
    telemetry cache only -- never probes -- because this runs inside
    ``write_status`` on every lease change.
    """
    try:
        from mle_kit_mcp import gpu_telemetry

        return gpu_telemetry.snapshot()
    except Exception:
        return {"remote": None, "local": None}


def write_status() -> None:
    """Write both leases' snapshots to `<workspace>/.holosophus_capacity/mle_kit.json`.

    Best-effort and atomic (write to a temp file in the same directory, then
    `os.replace`). Never raises: a status-write hiccup must not break a tool
    call, so any failure (workspace not configured yet, directory not
    writable, etc.) is swallowed here.
    """
    with _status_write_lock:
        try:
            payload = {
                "gpu": _gpu_snapshot(),
                "local_exec": LOCAL_EXEC_LEASE.snapshot(),
                "gpu_telemetry": _telemetry_snapshot(),
            }
            status_dir = get_workspace_dir() / STATUS_DIR_NAME
            status_dir.mkdir(parents=True, exist_ok=True)
            status_path = status_dir / STATUS_FILE_NAME
            tmp_path = status_dir / f".{STATUS_FILE_NAME}.tmp"
            tmp_path.write_text(json.dumps(payload))
            os.replace(tmp_path, status_path)
        except Exception:
            logger.debug("Failed to write mle_kit capacity status file", exc_info=True)


# Write once at import/startup so the file exists at rest with zeros.
write_status()
