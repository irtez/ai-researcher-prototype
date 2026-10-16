import json
import threading
import time
from types import SimpleNamespace
from typing import Any, Callable, Iterator, List

import pytest

from mle_kit_mcp import capacity, gpu_registry
from mle_kit_mcp.capacity import ResourceLease


def test_single_slot_serializes_concurrent_holders() -> None:
    lease = ResourceLease("t", 1)
    intervals: list[tuple[float, float]] = []
    lock = threading.Lock()

    def worker() -> None:
        with lease.hold():
            start = time.monotonic()
            time.sleep(0.15)
            end = time.monotonic()
        with lock:
            intervals.append((start, end))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(intervals) == 2
    (start1, end1), (start2, end2) = intervals
    # Serialized: one interval must fully finish before the other starts.
    overlap = start1 < end2 and start2 < end1
    assert not overlap


def test_two_slots_admit_concurrent_holders() -> None:
    lease = ResourceLease("t", 2)
    intervals: list[tuple[float, float]] = []
    lock = threading.Lock()

    def worker() -> None:
        with lease.hold():
            start = time.monotonic()
            time.sleep(0.15)
            end = time.monotonic()
        with lock:
            intervals.append((start, end))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(intervals) == 2
    (start1, end1), (start2, end2) = intervals
    overlap = start1 < end2 and start2 < end1
    assert overlap


def test_snapshot_reports_busy_and_waiting() -> None:
    lease = ResourceLease("t", 1)
    holder_in = threading.Event()
    release_holder = threading.Event()
    waiter_started = threading.Event()
    waiter_done = threading.Event()

    def holder() -> None:
        with lease.hold():
            holder_in.set()
            release_holder.wait(timeout=5)

    def waiter() -> None:
        waiter_started.set()
        with lease.hold():
            pass
        waiter_done.set()

    holder_thread = threading.Thread(target=holder)
    holder_thread.start()
    assert holder_in.wait(timeout=5)

    waiter_thread = threading.Thread(target=waiter)
    waiter_thread.start()
    assert waiter_started.wait(timeout=5)
    # Give the waiter thread a moment to actually block on the semaphore.
    time.sleep(0.05)

    snap = lease.snapshot()
    assert snap == {"busy": 1, "waiting": 1, "slots": 1}

    release_holder.set()
    holder_thread.join(timeout=5)
    waiter_thread.join(timeout=5)
    assert waiter_done.is_set()

    snap = lease.snapshot()
    assert snap == {"busy": 0, "waiting": 0, "slots": 1}


def test_write_status_creates_expected_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(capacity, "get_workspace_dir", lambda: tmp_path)

    capacity.write_status()

    status_path = tmp_path / capacity.STATUS_DIR_NAME / capacity.STATUS_FILE_NAME
    assert status_path.exists()
    data = json.loads(status_path.read_text())
    assert set(data.keys()) == {"gpu", "local_exec", "gpu_telemetry"}
    # The lease contract is what /capacity has always consumed -- adding
    # telemetry must not perturb its shape or types.
    for key in ("gpu", "local_exec"):
        assert set(data[key].keys()) == {"busy", "waiting", "slots"}
        for field in ("busy", "waiting", "slots"):
            assert isinstance(data[key][field], int)
    # Telemetry is always present but each side may be null until first probed.
    assert set(data["gpu_telemetry"].keys()) == {"remote", "local"}


def test_gpu_pool_round_trip(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(capacity, "get_workspace_dir", lambda: tmp_path)

    # Unset means "no override" so the env default still applies.
    assert capacity.read_gpu_pool() is None

    capacity.write_gpu_pool([7, 3, 5, 3])
    # Normalized: de-duplicated and sorted.
    assert capacity.read_gpu_pool() == [3, 5, 7]

    # Clearing restores the env default rather than an empty (unusable) pool.
    capacity.write_gpu_pool([])
    assert capacity.read_gpu_pool() is None


def test_gpu_pool_malformed_file_is_no_override(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(capacity, "get_workspace_dir", lambda: tmp_path)
    pool_path = tmp_path / capacity.STATUS_DIR_NAME / capacity.POOL_FILE_NAME
    pool_path.parent.mkdir(parents=True, exist_ok=True)

    # A truncated or hand-edited file must never strand the allocator with an
    # empty pool -- it falls back to the env default instead.
    for junk in ("{not json", "[]", '{"indices": "3,4"}', '{"indices": []}'):
        pool_path.write_text(junk)
        assert capacity.read_gpu_pool() is None


def test_write_status_never_raises_when_workspace_unwritable(tmp_path, monkeypatch) -> None:
    unwritable = tmp_path / "does" / "not" / "exist"

    def _boom() -> "object":
        raise AssertionError("WORKSPACE_DIR is not set")

    monkeypatch.setattr(capacity, "get_workspace_dir", _boom)

    # Must not raise.
    capacity.write_status()

    assert not unwritable.exists()


@pytest.mark.parametrize("slots", [1, 4])
def test_snapshot_reflects_slots(slots: int) -> None:
    lease = ResourceLease("t", slots)
    assert lease.snapshot() == {"busy": 0, "waiting": 0, "slots": slots}


# --- SC5: GPU capacity snapshot sourced from the per-session GPU registry -----
# The GPU class exports "sessions holding a card" (dynamic free-card ceiling +
# registry busy), decoupled from the per-call ResourceLease semaphore. The JSON
# shape {busy,waiting,slots} is unchanged; only the origin of the numbers is.


@pytest.fixture
def _clean_gpu() -> Iterator[None]:
    """Isolate the process-global GPU registry + GPU_LEASE size per test."""
    gpu_registry._gpu_index_by_session.clear()
    gpu_registry._gpu_waiting = 0
    original_slots = capacity.GPU_LEASE.slots
    try:
        yield
    finally:
        gpu_registry._gpu_index_by_session.clear()
        gpu_registry._gpu_waiting = 0
        capacity.GPU_LEASE.resize(original_slots)


def _free_host_run_command(free_indices: List[int]) -> Callable[..., Any]:
    """Fake ``run_command``: every card in ``free_indices`` reports free.

    Handles both the whole-host discovery query and the point re-probe
    (``... -i K``) that ``acquire_gpu_index`` issues before handing a card out.
    """

    def run(instance: Any, command: str, timeout: int = 30) -> SimpleNamespace:
        if " -i " in command:
            idx = int(command.rsplit(" -i ", 1)[1].strip())
            rows = f"{idx}, 0, 0\n" if idx in free_indices else ""
        else:
            rows = "".join(f"{i}, 0, 0\n" for i in free_indices)
        return SimpleNamespace(returncode=0, stdout=rows, stderr="")

    return run


def test_gpu_snapshot_slots_reflect_dynamic_ceiling(_clean_gpu: None) -> None:
    # At rest gpu.slots is the static single-box default (not yet lifted).
    assert capacity._gpu_snapshot()["slots"] == capacity.GPU_LEASE.slots
    # After free-card discovery lifts the ceiling, slots is the dynamic count.
    capacity.set_gpu_slots(6)
    assert capacity.GPU_LEASE.slots == 6
    assert capacity._gpu_snapshot()["slots"] == 6  # dynamic ceiling, not hardcoded 1


def test_gpu_snapshot_busy_tracks_sessions_from_registry(_clean_gpu: None) -> None:
    instance = SimpleNamespace()
    run = _free_host_run_command([2, 3, 4])
    assert capacity._gpu_snapshot()["busy"] == 0

    gpu_registry.acquire_gpu_index("sess-A", instance, run_command_fn=run)
    assert capacity._gpu_snapshot()["busy"] == 1  # +1 on acquire

    gpu_registry.acquire_gpu_index("sess-B", instance, run_command_fn=run)
    assert capacity._gpu_snapshot()["busy"] == 2

    gpu_registry.release_gpu_index("sess-A")
    assert capacity._gpu_snapshot()["busy"] == 1  # -1 on release


def test_gpu_busy_decoupled_from_per_call_lease(_clean_gpu: None) -> None:
    # A tool-call holding GPU_LEASE must NOT inflate gpu.busy: busy counts
    # sessions with a card (registry), not in-flight calls (the semaphore).
    with capacity.GPU_LEASE.hold():
        assert capacity.GPU_LEASE.snapshot()["busy"] == 1  # per-call semaphore
        assert capacity._gpu_snapshot()["busy"] == 0  # no session holds a card


def test_gpu_snapshot_waiting_counts_blocked_sessions(_clean_gpu: None) -> None:
    assert capacity._gpu_snapshot()["waiting"] == 0
    with gpu_registry.mark_waiting():
        assert capacity._gpu_snapshot()["waiting"] == 1
    assert capacity._gpu_snapshot()["waiting"] == 0


def test_write_status_gpu_sourced_from_registry(
    tmp_path: Any, monkeypatch: Any, _clean_gpu: None
) -> None:
    monkeypatch.setattr(capacity, "get_workspace_dir", lambda: tmp_path)
    capacity.set_gpu_slots(4)
    instance = SimpleNamespace()
    run = _free_host_run_command([0, 1, 2, 3])
    gpu_registry.acquire_gpu_index("sess-X", instance, run_command_fn=run)

    capacity.write_status()

    status_path = tmp_path / capacity.STATUS_DIR_NAME / capacity.STATUS_FILE_NAME
    data = json.loads(status_path.read_text())
    # Shape unchanged: same {busy,waiting,slots} keys the readers already parse.
    assert set(data["gpu"].keys()) == {"busy", "waiting", "slots"}
    assert set(data["local_exec"].keys()) == {"busy", "waiting", "slots"}
    # Registry-sourced numbers: dynamic ceiling, session-count busy.
    assert data["gpu"]["slots"] == 4  # not the hardcoded 1
    assert data["gpu"]["busy"] == 1  # one session holds a card
    assert data["gpu"]["waiting"] == 0
