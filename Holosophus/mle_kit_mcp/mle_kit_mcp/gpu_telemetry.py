"""Live per-card GPU telemetry for the capacity snapshot.

``gpu_registry`` already runs an ``nvidia-smi`` probe to decide which cards are
free, but it reduces every probe to bare indices. The UI needs the numbers
themselves -- *which* card, how much memory, how hard it is working -- so a user
can see a host is full **before** starting a run instead of hitting
``NoFreeGpuError`` after.

Two constraints shape the design:

* ``capacity.write_status()`` runs on every lease change, so it must stay
  cheap. Telemetry is therefore refreshed by a background thread and
  ``snapshot()`` is a pure cache read -- it never performs I/O. That also keeps
  ``write_status -> _gpu_snapshot -> snapshot`` free of re-entrancy, since the
  refresher itself calls ``write_status`` to publish.
* A probe must never *provision* anything. For the static hosts (selectel,
  cloudru) connecting costs nothing, so we may probe before any run. For vast,
  ``get_instance()`` would rent a machine, so we only report a host some run
  already brought up.
"""

from __future__ import annotations

import importlib
import logging
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Richer than gpu_registry's own query (which stays as-is so its parser and
# tests are untouched): the UI reproduces what you would read off nvidia-smi --
# model, memory, utilisation, temperature, power, and the processes resident on
# each card. `uuid` is carried only to join the two queries: compute-apps
# reports gpu_uuid, not index.
GPU_FIELDS = (
    "index,uuid,name,memory.used,memory.total,utilization.gpu,"
    "temperature.gpu,power.draw,power.limit"
)
APPS_FIELDS = "gpu_uuid,pid,process_name,used_memory"
APPS_MARKER = "---HOLO-APPS---"

# Both queries in one round trip -- this runs over SSH every refresh, so two
# separate invocations would double the latency for no benefit.
PROBE_COMMAND = (
    f"nvidia-smi --query-gpu={GPU_FIELDS} --format=csv,noheader,nounits; "
    f"echo {APPS_MARKER}; "
    f"nvidia-smi --query-compute-apps={APPS_FIELDS} --format=csv,noheader,nounits"
)

# How stale telemetry may get. The remote probe is one SSH round-trip and the
# local one is a container exec, so this trades a little load for a display
# that still feels live next to the UI's 4s capacity poll.
REFRESH_SECONDS = 20

# Providers that are already-provisioned static hosts: probing them starts
# nothing and costs nothing.
_STATIC_PROVIDERS = ("selectel", "cloudru")

_cache: Dict[str, Any] = {"remote": None, "local": None}
_cache_lock = threading.Lock()
_refresher: Optional[threading.Thread] = None
_probe_instance: Any = None


def _parse_int(raw: str) -> Optional[int]:
    try:
        return int(raw.strip())
    except (TypeError, ValueError):
        return None


def _parse_float(raw: str) -> Optional[float]:
    try:
        return round(float(raw.strip()), 1)
    except (TypeError, ValueError):
        return None


def _parse_processes(output: str) -> Dict[str, List[Dict[str, Any]]]:
    """Compute-apps rows grouped by the card UUID they run on."""
    by_uuid: Dict[str, List[Dict[str, Any]]] = {}
    for line in output.splitlines():
        line = line.strip()
        if not line or "not supported" in line.lower():
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4:
            continue
        by_uuid.setdefault(parts[0], []).append(
            {
                "pid": _parse_int(parts[1]),
                "name": parts[2],
                "memory_mib": _parse_int(parts[3]),
            }
        )
    return by_uuid


def parse_cards(output: str) -> List[Dict[str, Any]]:
    """Parse the combined probe output into card dicts.

    A row whose index is unreadable is dropped -- a card we cannot name is a
    card we cannot report on. Unreadable metrics become ``None`` and the card is
    reported **not free**, matching ``gpu_registry``'s rule that anything the
    driver won't state is treated as busy.
    """
    from mle_kit_mcp import gpu_registry

    gpu_part, _, apps_part = output.partition(APPS_MARKER)
    processes = _parse_processes(apps_part)

    cards: List[Dict[str, Any]] = []
    for line in gpu_part.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 9:
            continue
        index = _parse_int(parts[0])
        if index is None:
            continue
        uuid = parts[1]
        used = _parse_int(parts[3])
        total = _parse_int(parts[4])
        util = _parse_int(parts[5])
        free = (
            used is not None
            and util is not None
            and used < gpu_registry.FREE_MEMORY_THRESHOLD_MIB
            and util == 0
        )
        cards.append(
            {
                "index": index,
                "name": parts[2],
                "memory_used_mib": used,
                "memory_total_mib": total,
                "utilization_pct": util,
                "temperature_c": _parse_int(parts[6]),
                "power_draw_w": _parse_float(parts[7]),
                "power_limit_w": _parse_float(parts[8]),
                "processes": processes.get(uuid, []),
                "free": free,
            }
        )
    return sorted(cards, key=lambda c: c["index"])


def _annotate(cards: List[Dict[str, Any]], allowed: Optional[set], held: Dict[int, str]) -> None:
    """Mark which cards this deployment may use and which a session holds."""
    for card in cards:
        card["allowed"] = True if allowed is None else card["index"] in allowed
        card["held_by"] = held.get(card["index"])


def _instance_for_probe() -> Any:
    """An InstanceInfo to probe, or None if probing would provision a host."""
    global _probe_instance
    from mle_kit_mcp.settings import settings
    from mle_kit_mcp.tools import remote_gpu

    live = getattr(remote_gpu, "_instance_info", None)
    if live is not None:
        return live
    if _probe_instance is not None:
        return _probe_instance
    provider = str(settings.REMOTE_PROVIDER)
    if provider not in _STATIC_PROVIDERS:
        # vast: get_instance() rents hardware. Never probe speculatively.
        return None
    # reconcile=False: observing must not resize the real GPU_LEASE (see
    # build_selectel_instance's docstring).
    _probe_instance = (
        remote_gpu.build_selectel_instance(reconcile=False)
        if provider == "selectel"
        else remote_gpu.build_cloudru_instance()
    )
    return _probe_instance


def _remote_snapshot() -> Dict[str, Any]:
    from mle_kit_mcp import gpu_registry
    from mle_kit_mcp.settings import settings
    from mle_kit_mcp.tools import remote_gpu

    out: Dict[str, Any] = {
        "provider": str(settings.REMOTE_PROVIDER),
        "host": None,
        "cards": [],
        "error": None,
        "updated_at": int(time.time()),
    }
    try:
        instance = _instance_for_probe()
        if instance is None:
            out["error"] = "not_connected"
            return out
        out["host"] = instance.ip
        result = remote_gpu.run_command(instance, PROBE_COMMAND, timeout=30)
        if getattr(result, "returncode", 1) != 0:
            out["error"] = (getattr(result, "stderr", "") or "probe failed").strip()[:200]
            return out
        cards = parse_cards(getattr(result, "stdout", "") or "")
        _annotate(cards, gpu_registry.effective_reserved_indices(), gpu_registry.held_by_index())
        out["cards"] = cards
    except Exception as e:  # never let telemetry break the status file
        out["error"] = f"{type(e).__name__}: {e}"[:200]
    return out


def _local_snapshot() -> Dict[str, Any]:
    from mle_kit_mcp.settings import settings

    # importlib, not `from mle_kit_mcp.tools import bash`: the package re-exports
    # the `bash` *function* under that name, which would shadow the module.
    bash_tool = importlib.import_module("mle_kit_mcp.tools.bash")

    devices = settings.LOCAL_GPU_DEVICES.strip()
    out: Dict[str, Any] = {
        "devices": devices,
        "cards": [],
        "error": None,
        "updated_at": int(time.time()),
    }
    if not devices:
        out["error"] = "disabled"
        return out
    try:
        # Reuse the live sandbox container when there is one -- an exec is far
        # cheaper than starting a throwaway container every refresh.
        container = getattr(bash_tool, "_container", None)
        if container is not None:
            raw = container.exec_run(["sh", "-c", PROBE_COMMAND]).output.decode("utf-8", "replace")
        else:
            client = bash_tool.get_docker_client()
            raw = client.containers.run(
                bash_tool.BASE_IMAGE,
                ["sh", "-c", PROBE_COMMAND],
                remove=True,
                device_requests=bash_tool._local_gpu_device_requests(),
            ).decode("utf-8", "replace")
        allowed = None if devices == "all" else {int(d) for d in devices.split(",") if d.strip()}
        cards = parse_cards(raw)
        _annotate(cards, allowed, {})
        out["cards"] = cards
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"[:200]
    return out


def _sweep_idle_leases() -> None:
    """Expire stale GPU leases on the same cadence as the probe.

    Piggy-backs on this thread rather than adding another: it is the only
    periodic task in the process, and a lease that outlives its run is exactly
    the sort of drift a monitor should be correcting.
    """
    try:
        from mle_kit_mcp import gpu_registry
        from mle_kit_mcp.settings import settings

        released = gpu_registry.sweep_idle_leases(settings.GPU_LEASE_IDLE_SECONDS)
        if released:
            logger.info("Released idle GPU leases: %s", ", ".join(released))
    except Exception:
        logger.debug("Idle GPU lease sweep failed", exc_info=True)


def refresh() -> Dict[str, Any]:
    """Re-probe both sides and publish. Called by the background refresher."""
    _sweep_idle_leases()
    remote = _remote_snapshot()
    local = _local_snapshot()
    with _cache_lock:
        _cache["remote"] = remote
        _cache["local"] = local
    try:
        from mle_kit_mcp import capacity

        capacity.write_status()
    except Exception:
        logger.debug("Telemetry refresh could not publish capacity status", exc_info=True)
    return {"remote": remote, "local": local}


def snapshot() -> Dict[str, Any]:
    """Last known telemetry. Pure cache read -- never performs I/O."""
    with _cache_lock:
        return {"remote": _cache["remote"], "local": _cache["local"]}


def _loop() -> None:
    while True:
        try:
            refresh()
        except Exception:
            logger.debug("GPU telemetry refresh failed", exc_info=True)
        time.sleep(REFRESH_SECONDS)


def start_background_refresh() -> None:
    """Start the refresher once. Idempotent; safe to call from server startup."""
    global _refresher
    if _refresher is not None and _refresher.is_alive():
        return
    _refresher = threading.Thread(target=_loop, name="gpu-telemetry", daemon=True)
    _refresher.start()
