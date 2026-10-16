"""SC3 (Selectel provider spec): only free cards are leased.

Dynamic occupancy discovery via
``nvidia-smi --query-gpu=index,memory.used,utilization.gpu`` -- free means
``memory.used < 512 MiB`` and ``utilization.gpu == 0``. A card busy with an
external process is never handed out; with no free card the allocator raises an
explicit error.

The SSH transport (``run_command``) is injected as a fake returning canned
``nvidia-smi`` CSV, so these run without a live host.
"""

from types import SimpleNamespace
from typing import Any

import pytest

from mle_kit_mcp import gpu_registry


def _result(returncode: int, stdout: str, stderr: str = "") -> SimpleNamespace:
    """Stand-in for subprocess.CompletedProcess as run_command returns it."""
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch: pytest.MonkeyPatch) -> Any:
    gpu_registry._gpu_index_by_session.clear()
    gpu_registry._gpu_last_used.clear()
    # These tests exercise the *selection algorithm*, so every ambient config
    # source must be neutralised. Without this they read the deployment's real
    # pool file from the shared workspace and quietly filter the fake cards away
    # -- which is exactly how a live pool of [3..7] turned a green suite red.
    # Stub the *sources*, not `effective_reserved_indices` itself, so the
    # resolution logic stays under test rather than being mocked out.
    monkeypatch.setattr(gpu_registry, "_reserved_from_settings", lambda: None)
    monkeypatch.setattr("mle_kit_mcp.capacity.read_gpu_pool", lambda: None)
    monkeypatch.setattr(gpu_registry, "_forced_index", lambda: None)
    yield
    gpu_registry._gpu_index_by_session.clear()
    gpu_registry._gpu_last_used.clear()


# --- ambient config resolution (explicitly, since the fixture stubs it) -------


def test_idle_lease_is_swept_but_a_fresh_one_is_kept() -> None:
    """A lease outliving its run must expire; an active one must not."""
    gpu_registry._gpu_index_by_session.update({"stale": 1, "fresh": 2})
    now = gpu_registry.time.monotonic()
    gpu_registry._gpu_last_used.update({"stale": now - 5000, "fresh": now})

    released = gpu_registry.sweep_idle_leases(3600)

    assert released == ["stale"]
    assert gpu_registry._gpu_index_by_session == {"fresh": 2}
    assert "stale" not in gpu_registry._gpu_last_used


def test_idle_sweep_disabled_by_zero() -> None:
    gpu_registry._gpu_index_by_session["old"] = 3
    gpu_registry._gpu_last_used["old"] = gpu_registry.time.monotonic() - 10**6
    assert gpu_registry.sweep_idle_leases(0) == []
    assert gpu_registry._gpu_index_by_session == {"old": 3}


def test_pool_file_override_wins_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gpu_registry, "_reserved_from_settings", lambda: {0, 1})
    monkeypatch.setattr("mle_kit_mcp.capacity.read_gpu_pool", lambda: [4, 5])
    assert gpu_registry.effective_reserved_indices() == {4, 5}


def test_env_default_applies_without_pool_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gpu_registry, "_reserved_from_settings", lambda: {0, 1})
    monkeypatch.setattr("mle_kit_mcp.capacity.read_gpu_pool", lambda: None)
    assert gpu_registry.effective_reserved_indices() == {0, 1}


def test_forced_pin_bypasses_freeness_and_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    """The manual override must seat a session on a card nothing would offer."""
    probed = []

    def never_free(instance: Any, command: str, timeout: int = 60) -> SimpleNamespace:
        # Every card busy: automatic selection would raise NoFreeGpuError.
        probed.append(command)
        return _result(0, "0, 71000, 100\n1, 71000, 100\n")

    monkeypatch.setattr(gpu_registry, "_forced_index", lambda: 1)
    assert gpu_registry.acquire_gpu_index("sF", object(), run_command_fn=never_free) == 1
    assert gpu_registry._gpu_index_by_session == {"sF": 1}
    # A pin short-circuits before discovery, so no nvidia-smi probe is issued.
    assert probed == []


# --- parsing / free-selection (SC3 happy) ------------------------------------


def test_free_indices_selects_only_free_cards() -> None:
    # 0: busy mem + util; 1: free; 2: free; 3: busy mem (util 0 is not enough);
    # 4: busy util (low mem is not enough).
    output = "0, 71000, 100\n1, 4, 0\n2, 200, 0\n3, 71000, 0\n4, 10, 30\n"
    assert gpu_registry.free_indices_from_output(output) == [1, 2]


def test_free_threshold_is_512_mib() -> None:
    # 511 MiB free, 512 MiB busy (strictly below the threshold).
    output = "0, 511, 0\n1, 512, 0\n"
    assert gpu_registry.free_indices_from_output(output) == [0]


def test_non_numeric_utilisation_is_treated_as_busy() -> None:
    # A driver reporting "[N/A]" utilisation must not be leased as free.
    output = "0, 5, [N/A]\n1, 5, 0\n"
    assert gpu_registry.free_indices_from_output(output) == [1]


def test_reserved_subset_narrows_free_set() -> None:
    output = "2, 5, 0\n3, 5, 0\n4, 5, 0\n"
    assert gpu_registry.free_indices_from_output(output, reserved={3, 4}) == [3, 4]


def test_parse_reserved_indices() -> None:
    assert gpu_registry.parse_reserved_indices("2, 3,4") == {2, 3, 4}
    assert gpu_registry.parse_reserved_indices(None) is None
    assert gpu_registry.parse_reserved_indices("") is None


def test_discover_free_indices_runs_query_unpinned() -> None:
    seen = {}

    def fake_rc(instance: Any, command: str, timeout: int = 60) -> SimpleNamespace:
        seen["command"] = command
        return _result(0, "0, 71000, 100\n1, 5, 0\n2, 5, 0\n")

    free = gpu_registry.discover_free_indices(object(), run_command_fn=fake_rc)
    assert free == [1, 2]
    # Discovery must query *all* cards (no `-i <index>` pin) so it sees the host.
    assert "--query-gpu=index,memory.used,utilization.gpu" in seen["command"]
    assert " -i " not in seen["command"]


# --- allocation skips busy cards (SC3 edge) ----------------------------------


def test_acquire_skips_externally_busy_cards() -> None:
    # Cards 0 and 1 held by external processes (~70 GB each); 2 and 3 free.
    def fake_rc(instance: Any, command: str, timeout: int = 60) -> SimpleNamespace:
        if " -i " in command:
            idx = int(command.rsplit(" -i ", 1)[1].strip())
            mem = 5 if idx in (2, 3) else 71000
            util = 0 if idx in (2, 3) else 100
            return _result(0, f"{idx}, {mem}, {util}")
        return _result(0, "0, 71000, 100\n1, 71000, 100\n2, 5, 0\n3, 5, 0\n")

    idx = gpu_registry.acquire_gpu_index("s1", object(), run_command_fn=fake_rc)
    assert idx in (2, 3)


def test_point_reprobe_skips_card_taken_since_host_probe() -> None:
    # Host-wide probe says 2 and 3 free, but an external tenant grabbed card 2
    # in between -- the point re-probe must catch it and fall through to 3.
    def fake_rc(instance: Any, command: str, timeout: int = 60) -> SimpleNamespace:
        if " -i 2" in command:
            return _result(0, "2, 71000, 100")  # taken since host probe
        if " -i 3" in command:
            return _result(0, "3, 5, 0")
        return _result(0, "0, 71000, 100\n1, 71000, 100\n2, 5, 0\n3, 5, 0\n")

    idx = gpu_registry.acquire_gpu_index("s1", object(), run_command_fn=fake_rc)
    assert idx == 3


# --- no free card -> explicit error (SC3 edge) -------------------------------


def test_no_free_card_raises_explicit_error() -> None:
    def fake_rc(instance: Any, command: str, timeout: int = 60) -> SimpleNamespace:
        return _result(0, "0, 71000, 100\n1, 71000, 100\n")

    with pytest.raises(gpu_registry.NoFreeGpuError):
        gpu_registry.acquire_gpu_index("s2", object(), run_command_fn=fake_rc)


def test_all_host_free_but_all_reprobe_busy_raises() -> None:
    # Host probe optimistic, every point re-probe shows the card got taken.
    def fake_rc(instance: Any, command: str, timeout: int = 60) -> SimpleNamespace:
        if " -i " in command:
            idx = int(command.rsplit(" -i ", 1)[1].strip())
            return _result(0, f"{idx}, 71000, 100")
        return _result(0, "2, 5, 0\n3, 5, 0\n")

    with pytest.raises(gpu_registry.NoFreeGpuError):
        gpu_registry.acquire_gpu_index("s3", object(), run_command_fn=fake_rc)


def test_discovery_ssh_failure_raises() -> None:
    def fake_rc(instance: Any, command: str, timeout: int = 60) -> SimpleNamespace:
        return _result(255, "", "ssh: connect to host ... : Connection timed out")

    with pytest.raises(gpu_registry.GpuDiscoveryError):
        gpu_registry.discover_free_indices(object(), run_command_fn=fake_rc)


# --- SC4: distinct indices per session + idempotent reuse --------------------
# The `_gpu_index_by_session` registry (keyed by `session_id`, an analog of
# codearkt/codearkt/run_config.py) is the source of truth. Two concurrent
# sessions must never seat on the same card; a single session must reuse the
# same card across tool calls without re-running discovery.


def _fake_host(free: Any, taken: Any = None) -> Any:
    """A fake ``run_command`` for a shared 8-card host.

    ``free`` are the indices reported free by the host-wide occupancy probe; a
    card in ``taken`` is reported busy by the point re-probe (external tenant
    grabbed it after the host-wide probe). Cards outside ``free`` read busy.
    """
    free_set = set(free)
    taken_set = set(taken or [])

    def fake_rc(instance: Any, command: str, timeout: int = 60) -> SimpleNamespace:
        if " -i " in command:
            idx = int(command.rsplit(" -i ", 1)[1].strip())
            is_free = idx in free_set and idx not in taken_set
            return _result(0, f"{idx}, 5, 0" if is_free else f"{idx}, 71000, 100")
        rows = []
        for i in range(8):
            is_free = i in free_set and i not in taken_set
            rows.append(f"{i}, 5, 0" if is_free else f"{i}, 71000, 100")
        return _result(0, "\n".join(rows))

    return fake_rc


def test_two_sessions_get_distinct_indices() -> None:
    # Cards 2, 3, 4 free on the shared host; two concurrent sessions must land
    # on different cards, tracked distinctly in the registry.
    fake = _fake_host(free={2, 3, 4})
    i1 = gpu_registry.acquire_gpu_index("sA", object(), run_command_fn=fake)
    i2 = gpu_registry.acquire_gpu_index("sB", object(), run_command_fn=fake)
    assert i1 in {2, 3, 4}
    assert i2 in {2, 3, 4}
    assert i1 != i2
    assert gpu_registry._gpu_index_by_session == {"sA": i1, "sB": i2}


def test_same_session_reuses_index_idempotently() -> None:
    fake = _fake_host(free={2, 3, 4})
    first = gpu_registry.acquire_gpu_index("sA", object(), run_command_fn=fake)

    # A repeat acquire for the same session must return the identical index and
    # must NOT re-run discovery (the card is reused across tool calls, so no
    # further nvidia-smi probe should be issued).
    calls = {"n": 0}

    def counting_fake(instance: Any, command: str, timeout: int = 60) -> SimpleNamespace:
        calls["n"] += 1
        return fake(instance, command, timeout)

    again = gpu_registry.acquire_gpu_index("sA", object(), run_command_fn=counting_fake)
    assert again == first
    assert calls["n"] == 0


def test_second_session_blocks_on_single_free_card_then_reuses_after_release() -> None:
    # Only card 2 is free on the whole host. sA takes it; a *distinct* second
    # session cannot be seated on the same card, so it gets an explicit error.
    fake = _fake_host(free={2})
    i1 = gpu_registry.acquire_gpu_index("sA", object(), run_command_fn=fake)
    assert i1 == 2
    with pytest.raises(gpu_registry.NoFreeGpuError):
        gpu_registry.acquire_gpu_index("sB", object(), run_command_fn=fake)

    # Once sA releases, the freed card returns to the pool and sB can take it.
    gpu_registry.release_gpu_index("sA")
    assert "sA" not in gpu_registry._gpu_index_by_session
    i2 = gpu_registry.acquire_gpu_index("sB", object(), run_command_fn=fake)
    assert i2 == 2
    assert gpu_registry._gpu_index_by_session == {"sB": 2}


def test_blank_session_id_is_not_tracked() -> None:
    # SC2 fallback: a blank session id still gets a free card pinned, but must
    # NOT occupy a registry slot -- otherwise an untracked lease would subtract
    # a card forever and could not be released by session id.
    fake = _fake_host(free={2, 3})
    idx = gpu_registry.acquire_gpu_index("", object(), run_command_fn=fake)
    assert idx in {2, 3}
    assert gpu_registry._gpu_index_by_session == {}
