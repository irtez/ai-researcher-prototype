"""Process-global per-session GPU-index registry + dynamic free-card discovery.

Selectel provider
(``docs/plans/2026-07-01-selectel-provider-per-session-gpu.md``): the Selectel
host is a *shared* multi-GPU box -- some cards are held by external
(non-Holosophus) tenants at any moment, so a static index list (e.g. "0..7")
would hand a session a card that is not ours. SC3 makes card selection
dynamic: probe live occupancy via ``nvidia-smi`` and lease only cards that are
actually free right now.

A card is considered **free** when both:
  * ``memory.used`` is below ``FREE_MEMORY_THRESHOLD_MIB`` (~0), and
  * ``utilization.gpu`` is ``0``.

A card busy with an external process (high ``memory.used`` and/or non-zero
utilisation) is never handed out. When no free card is available the allocator
raises an explicit error rather than silently seating a session on a busy card.

Kept dependency-free at import time (mirrors ``codearkt/codearkt/run_config.py``):
the ``nvidia-smi`` transport (``run_command`` over SSH) is imported lazily and
can be injected, so this module can be imported and unit-tested without the
``remote_gpu`` stack or a live host.
"""

from __future__ import annotations

import contextlib
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterator, List, Optional, Set

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids runtime import cycle
    from mle_kit_mcp.tools.remote_gpu import InstanceInfo

# A free card has essentially no resident memory. 512 MiB tolerates driver/ECC
# overhead and small persistent contexts while still rejecting any real
# workload (external tenants on this host hold tens of GB per card).
FREE_MEMORY_THRESHOLD_MIB = 512

# Discovery: ask the driver for occupancy of every card as clean CSV.
NVIDIA_SMI_QUERY = (
    "nvidia-smi --query-gpu=index,memory.used,utilization.gpu " "--format=csv,noheader,nounits"
)

RunCommandFn = Callable[..., Any]


class GpuDiscoveryError(RuntimeError):
    """``nvidia-smi`` occupancy probe failed or returned unparseable output."""


class NoFreeGpuError(RuntimeError):
    """Probe succeeded but every discovered card is busy (no card to lease)."""


@dataclass(frozen=True)
class GpuOccupancy:
    """One parsed row of the ``nvidia-smi`` occupancy query."""

    index: int
    memory_used_mib: int
    utilization_pct: int

    def is_free(self) -> bool:
        return self.memory_used_mib < FREE_MEMORY_THRESHOLD_MIB and self.utilization_pct == 0


# --- process-global registry (session_id -> leased card index) ---------------
# mle_kit tools are synchronous `def`s dispatched on worker threads (see
# capacity.py), so a threading primitive -- not asyncio -- is what serialises
# concurrent allocation.
_gpu_index_by_session: Dict[str, int] = {}
# Last time each session touched its card, for idle expiry. There is no
# end-of-run signal reaching this process (CodeArkt talks to mle_kit only
# through MCP tool calls), so without expiry a lease is held until restart and
# `gpu.busy` only ever grows -- the UI would show cards as ours long after the
# run that took them finished.
_gpu_last_used: Dict[str, float] = {}
_registry_lock = threading.Lock()

# Sessions currently blocked waiting for a free card because the pool is
# exhausted. This mirrors ``gpu.busy``'s *per-session* model (not the per-call
# ``ResourceLease`` semaphore queue), so the capacity snapshot reads
# consistently: ``busy`` = sessions holding a card, ``waiting`` = sessions
# queued for one. The blocking-wait path itself (a caller that retries instead
# of failing fast on ``NoFreeGpuError``) is a deferrable open question (spec
# Risks); until it lands callers fail fast and this stays at rest.
# ``mark_waiting()`` is the seam that future path wraps around its wait.
_gpu_waiting: int = 0


# A field the driver cannot report (e.g. "[N/A]" for utilisation on some
# configs) must be treated as *busy*, never free -- picking a "busy" sentinel
# well above every threshold guarantees the card is skipped.
_BUSY_SENTINEL = 10**9


def _parse_int_field(raw: str) -> int:
    """Parse one CSV field to int; map anything non-numeric to a busy sentinel."""
    token = raw.strip()
    try:
        return int(token)
    except ValueError:
        return _BUSY_SENTINEL


def parse_gpu_occupancy(output: str) -> List[GpuOccupancy]:
    """Parse ``nvidia-smi --query-gpu=index,memory.used,utilization.gpu`` CSV.

    ``--format=csv,noheader,nounits`` yields lines like ``"2, 5, 0"``. Rows
    whose *index* is unreadable are dropped (we cannot lease a card we cannot
    name); memory/utilisation that the driver reports as non-numeric are mapped
    to a busy sentinel so the card is treated as occupied, not free.
    """
    rows: List[GpuOccupancy] = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        try:
            index = int(parts[0])
        except ValueError:
            # A row without a numeric index is not a usable card entry.
            continue
        rows.append(
            GpuOccupancy(
                index=index,
                memory_used_mib=_parse_int_field(parts[1]),
                utilization_pct=_parse_int_field(parts[2]),
            )
        )
    return rows


def free_indices_from_output(output: str, reserved: Optional[Set[int]] = None) -> List[int]:
    """Free card indices parsed from occupancy CSV, optionally narrowed.

    ``reserved`` (from ``SELECTEL_GPU_INDICES``) restricts the allocatable set to
    a Holosophus-owned subset; when ``None`` every free card is allocatable.
    """
    free = [occ.index for occ in parse_gpu_occupancy(output) if occ.is_free()]
    if reserved is not None:
        free = [i for i in free if i in reserved]
    return sorted(free)


def parse_reserved_indices(raw: Optional[str]) -> Optional[Set[int]]:
    """Parse ``SELECTEL_GPU_INDICES`` ("2,3,4") into a set; empty/None -> None."""
    if raw is None:
        return None
    tokens = [t.strip() for t in raw.split(",") if t.strip()]
    if not tokens:
        return None
    return {int(t) for t in tokens}


def _default_run_command() -> RunCommandFn:
    # Lazy import keeps this module free of the remote_gpu stack at import time
    # and avoids an import cycle once remote_gpu wires in the registry.
    from mle_kit_mcp.tools.remote_gpu import run_command

    return run_command


def _reserved_from_settings() -> Optional[Set[int]]:
    # Read defensively via getattr: the SELECTEL_GPU_INDICES setting is added by
    # the provider foundation (SC1); this module tolerates its absence.
    from mle_kit_mcp.settings import settings

    return parse_reserved_indices(getattr(settings, "SELECTEL_GPU_INDICES", None))


def _forced_index() -> Optional[int]:
    """The hand-pinned card index from the UI, or None for automatic selection."""
    try:
        from mle_kit_mcp import capacity

        return capacity.read_gpu_forced()
    except Exception:
        return None


def effective_reserved_indices() -> Optional[Set[int]]:
    """The allocatable card set: the UI's override if set, else the env default.

    Read per allocation rather than cached, which is what lets the card pool be
    changed from the UI without restarting mle_kit. The override is only ever a
    *narrowing* of what is free, exactly like the env setting -- neither can
    hand out a card an external tenant is using.
    """
    try:
        from mle_kit_mcp import capacity

        override = capacity.read_gpu_pool()
    except Exception:
        override = None
    if override:
        return set(override)
    return _reserved_from_settings()


def discover_free_indices(
    instance: "InstanceInfo",
    reserved: Optional[Set[int]] = None,
    run_command_fn: Optional[RunCommandFn] = None,
) -> List[int]:
    """Probe live occupancy on ``instance`` and return the free card indices.

    Runs the ``nvidia-smi`` occupancy query **un-pinned** (all cards visible) so
    discovery sees the whole host, not a single pinned card. Raises
    ``GpuDiscoveryError`` if the probe cannot be run or parsed.
    """
    run = run_command_fn or _default_run_command()
    if reserved is None:
        reserved = effective_reserved_indices()

    result = run(instance, NVIDIA_SMI_QUERY, timeout=30)
    returncode = getattr(result, "returncode", 1)
    stdout = getattr(result, "stdout", "") or ""
    if returncode != 0:
        stderr = getattr(result, "stderr", "") or ""
        raise GpuDiscoveryError(
            f"nvidia-smi occupancy probe failed (returncode={returncode}): " f"{stderr or stdout}"
        )
    return free_indices_from_output(stdout, reserved=reserved)


def is_index_free(
    instance: "InstanceInfo",
    index: int,
    run_command_fn: Optional[RunCommandFn] = None,
) -> bool:
    """Point re-probe of a single card, just before it is handed out.

    Narrows (does not close) the race window in which an external tenant grabs a
    card between the host-wide probe and the lease (see spec Risks).
    """
    run = run_command_fn or _default_run_command()
    result = run(instance, f"{NVIDIA_SMI_QUERY} -i {index}", timeout=30)
    if getattr(result, "returncode", 1) != 0:
        return False
    stdout = getattr(result, "stdout", "") or ""
    rows = parse_gpu_occupancy(stdout)
    return any(occ.index == index and occ.is_free() for occ in rows)


def acquire_gpu_index(
    session_id: str,
    instance: "InstanceInfo",
    reserved: Optional[Set[int]] = None,
    run_command_fn: Optional[RunCommandFn] = None,
) -> int:
    """Lease a free card index, choosing only from cards free at lease time.

    Selection order: skip cards already held by another Holosophus session, then
    among the host-wide free set point-re-probe each candidate (an external
    tenant may have taken it since the host-wide probe) and lease the first that
    is still free. Raises ``NoFreeGpuError`` when no free card remains.

    Leasing under ``_registry_lock`` serialises concurrent acquirers so two
    sessions cannot race onto the same card.
    """
    acquired: Optional[int] = None
    recorded = False
    with _registry_lock:
        existing = _gpu_index_by_session.get(session_id)
        if session_id and existing is not None:
            _gpu_last_used[session_id] = time.monotonic()
            return existing

        # Manual pin short-circuits every automatic rule. On a shared host the
        # freeness check can refuse indefinitely (every card holds someone
        # else's work), so this is the operator's explicit "use that one
        # anyway": no free check, no skip-if-held, and no discovery probe at
        # all. Recorded like any other lease so the UI still shows who sits
        # where. Note the status refresh still happens *after* the lock below --
        # write_status re-reads the registry and this lock is not reentrant.
        forced = _forced_index()
        if forced is not None:
            if session_id:
                _gpu_index_by_session[session_id] = forced
                _gpu_last_used[session_id] = time.monotonic()
                recorded = True
            acquired = forced
        else:
            held = set(_gpu_index_by_session.values())
            free = discover_free_indices(instance, reserved=reserved, run_command_fn=run_command_fn)
            for candidate in free:
                if candidate in held:
                    continue
                if is_index_free(instance, candidate, run_command_fn=run_command_fn):
                    if session_id:
                        _gpu_index_by_session[session_id] = candidate
                        _gpu_last_used[session_id] = time.monotonic()
                        recorded = True
                    acquired = candidate
                    break

            if acquired is None:
                raise NoFreeGpuError(
                    "No free GPU available: every discovered card on the shared host is "
                    "busy (external tenants and/or other Holosophus sessions). Free a "
                    "card or wait for one to be released before retrying. You can also "
                    "pin a card by hand from the UI to share one that is already in use."
                )

    # Refresh gpu.busy outside the lock (write_status re-reads the registry).
    if recorded:
        _write_capacity_status()
    return acquired


def release_gpu_index(session_id: str) -> None:
    """Return a session's leased card index to the free pool, if any."""
    with _registry_lock:
        removed = _gpu_index_by_session.pop(session_id, None) is not None
        _gpu_last_used.pop(session_id, None)
    if removed:
        _write_capacity_status()


def sweep_idle_leases(max_idle_seconds: float) -> List[str]:
    """Release leases whose session hasn't touched its card in a while.

    Nothing tells this process that a run ended, so this is what keeps
    ``gpu.busy`` honest instead of monotonically increasing. It is safe to
    reclaim optimistically: releasing only forgets our *bookkeeping*: a card
    with real work still resident stays out of the free pool anyway, because
    allocation re-checks live occupancy and a busy card is never handed out.
    Returns the released session ids (for logging/tests).
    """
    if max_idle_seconds <= 0:
        return []
    now = time.monotonic()
    with _registry_lock:
        stale = [
            session
            for session in _gpu_index_by_session
            if now - _gpu_last_used.get(session, now) > max_idle_seconds
        ]
        for session in stale:
            _gpu_index_by_session.pop(session, None)
            _gpu_last_used.pop(session, None)
    if stale:
        _write_capacity_status()
    return stale


# --- capacity reporting (SC5): sessions-holding-a-card model -----------------
# The GPU capacity snapshot exports how many *sessions* hold a card, not how
# many *tool calls* are in flight. These accessors are the source of truth for
# ``gpu.busy`` / ``gpu.waiting`` in ``capacity.write_status``; ``gpu.slots`` is
# the (discovery-lifted) ``GPU_LEASE`` ceiling. The JSON shape is unchanged.


def held_session_count() -> int:
    """Number of sessions currently holding a leased card index (``gpu.busy``)."""
    with _registry_lock:
        return len(_gpu_index_by_session)


def held_by_index() -> Dict[int, str]:
    """Leased card index -> the session holding it, for the UI's usage view."""
    with _registry_lock:
        return {index: session for session, index in _gpu_index_by_session.items()}


def waiting_session_count() -> int:
    """Number of sessions blocked waiting for a free card (``gpu.waiting``)."""
    with _registry_lock:
        return _gpu_waiting


@contextlib.contextmanager
def mark_waiting() -> Iterator[None]:
    """Count the caller as waiting for a card for the duration of the block.

    The seam a future blocking-acquire path wraps around its wait so
    ``gpu.waiting`` reflects sessions queued on an exhausted pool (today
    ``acquire_gpu_index`` fails fast, so this is unused in the hot path --
    see the ``_gpu_waiting`` note and spec Risks). Refreshes the capacity
    status file on entry and exit.
    """
    global _gpu_waiting
    with _registry_lock:
        _gpu_waiting += 1
    _write_capacity_status()
    try:
        yield
    finally:
        with _registry_lock:
            _gpu_waiting -= 1
        _write_capacity_status()


def _write_capacity_status() -> None:
    """Best-effort refresh of the mle_kit capacity file after a registry change.

    Lazy import avoids an import cycle (``capacity`` reads this module for the
    gpu snapshot) and keeps ``gpu_registry`` dependency-free at import time.
    Never raises: capacity reporting must not fail a GPU allocation.
    """
    try:
        from mle_kit_mcp import capacity

        capacity.write_status()
    except Exception:
        pass
