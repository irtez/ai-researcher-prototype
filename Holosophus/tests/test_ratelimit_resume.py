"""Unit tests for limit-aware auto-resume (docs/plans/2026-08-13-rate-limit-resume-cc.md).

No live LLM: the fallback parser + marker helpers are pure; the driver's structured detector is
exercised with a FakeClient yielding a real RateLimitEvent / raising a real ProcessError; the
across-limit orchestration runs with fake driver/waiter/factory.
"""

import asyncio
import sys
import time
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

import cc_run_control as ctl  # noqa: E402
import run_research as rr  # noqa: E402

from claude_agent_sdk import RateLimitEvent, ProcessError  # noqa: E402
from claude_agent_sdk.types import RateLimitInfo  # noqa: E402

# --- fallback parser (SC1 fallback / SC2 / SC5) -------------------------------


def test_parse_none_for_normal_text() -> None:
    assert ctl.parse_rate_limit("Final answer: done") is None
    assert ctl.parse_rate_limit("some unrelated crash: disk full") is None
    assert ctl.parse_rate_limit("") is None


def test_parse_epoch_and_type() -> None:
    r = ctl.parse_rate_limit("Claude usage limit reached. five_hour resetsAt: 1786570000")
    assert r == {"resets_at": 1786570000.0, "rate_limit_type": "five_hour"}


def test_parse_pipe_epoch() -> None:
    assert ctl.parse_rate_limit("usage limit reached|1786570000")["resets_at"] == 1786570000.0


def test_parse_ms_epoch_normalized() -> None:
    r = ctl.parse_rate_limit("five_hour resetsAt=1786570000000")
    assert abs(r["resets_at"] - 1786570000.0) < 1.0


def test_parse_iso_and_seven_day() -> None:
    r = ctl.parse_rate_limit("usage limit reached, resets at 2026-08-13T18:00:00Z (seven_day)")
    assert r["rate_limit_type"] == "seven_day" and r["resets_at"] is not None


def test_parse_signal_without_time_backs_off() -> None:
    assert ctl.parse_rate_limit("usage limit reached, try again later") == {
        "resets_at": None,
        "rate_limit_type": None,
    }


# --- marker file --------------------------------------------------------------


def test_rate_limit_marker_round_trip(tmp_path: Path) -> None:
    ctl.write_rate_limit(tmp_path, 1786570000.0, "five_hour")
    m = ctl.read_rate_limit(tmp_path)
    assert (
        m["rate_limit_type"] == "five_hour" and m["resets_at"] == 1786570000.0 and m["detected_at"]
    )
    ctl.clear_rate_limit(tmp_path)
    assert ctl.read_rate_limit(tmp_path) is None


def test_rate_limit_marker_corrupt_is_none(tmp_path: Path) -> None:
    (ctl.ctl_dir(tmp_path) / ctl.RATE_LIMIT_FILENAME).write_text("[]", encoding="utf-8")
    assert ctl.read_rate_limit(tmp_path) is None


# --- _rate_hit classifier -----------------------------------------------------


def _info(status, resets_at, t):
    return RateLimitInfo(status=status, resets_at=resets_at, rate_limit_type=t)


def test_rate_hit_structured_rejected_wins() -> None:
    h = rr._rate_hit(_info("rejected", 1786570000, "five_hour"), "", ctl)
    assert h and h.resets_at == 1786570000 and h.rate_limit_type == "five_hour"


def test_rate_hit_allowed_warning_is_not_a_hit() -> None:
    assert rr._rate_hit(_info("allowed_warning", 1, "five_hour"), "", ctl) is None


def test_rate_hit_text_fallback() -> None:
    h = rr._rate_hit(None, "usage limit reached five_hour resetsAt: 1786570000", ctl)
    assert h and h.rate_limit_type == "five_hour"


def test_rate_hit_unrelated_is_none() -> None:
    assert rr._rate_hit(None, "connection reset by peer", ctl) is None


# --- driver-level detection (FakeClient) --------------------------------------


class _FakeMsg:
    def __init__(self, is_result=False, is_error=False, result=""):
        self.is_result = is_result
        self.is_error = is_error
        self.result = result


class _FakeClient:
    """Yields a scripted message list from receive_response, or raises on query."""

    def __init__(self, messages=None, raise_exc=None):
        self._messages = messages or []
        self._raise = raise_exc
        self.queries = []

    async def query(self, text, session_id=None):
        self.queries.append(text)

    async def interrupt(self):
        pass

    async def receive_response(self):
        if self._raise is not None:
            raise self._raise
        for m in self._messages:
            yield m


def _drive(client, tmp_path):
    return asyncio.run(
        asyncio.wait_for(
            rr._drive_with_pause_control(
                client,
                "TASK",
                "sess",
                tmp_path,
                0.0,
                render=lambda *a: None,
                poll_interval=0.01,
                freeze=lambda wd: False,
                unfreeze=lambda wd: False,
            ),
            timeout=5.0,
        )
    )


def test_driver_returns_hit_on_rate_limit_event(tmp_path: Path) -> None:
    ev = RateLimitEvent(
        rate_limit_info=RateLimitInfo(
            status="rejected", resets_at=1786570000, rate_limit_type="five_hour"
        ),
        uuid="u1",
        session_id="s1",
    )
    hit = _drive(_FakeClient(messages=[ev]), tmp_path)
    assert isinstance(hit, rr.RateLimitHit)
    assert hit.resets_at == 1786570000 and hit.rate_limit_type == "five_hour"


def test_driver_returns_hit_on_process_error_with_limit_text(tmp_path: Path) -> None:
    exc = ProcessError("usage limit reached five_hour resetsAt: 1786570000", exit_code=1)
    hit = _drive(_FakeClient(raise_exc=exc), tmp_path)
    assert isinstance(hit, rr.RateLimitHit) and hit.rate_limit_type == "five_hour"


def test_driver_reraises_unrelated_process_error(tmp_path: Path) -> None:
    exc = ProcessError("segmentation fault", exit_code=139)
    with pytest.raises(ProcessError):
        _drive(_FakeClient(raise_exc=exc), tmp_path)


def test_driver_completes_normally_returns_none(tmp_path: Path) -> None:
    msgs = [_FakeMsg(is_result=True, is_error=False, result="done")]
    assert _drive(_FakeClient(messages=msgs), tmp_path) is None


# --- _wait_for_reset (SC4) ----------------------------------------------------


def test_wait_elapses_returns_true(tmp_path: Path) -> None:
    ok = asyncio.run(
        rr._wait_for_reset(
            tmp_path, None, "five_hour", ctl, buffer_s=0.0, backoff_s=0.1, poll_interval=0.05
        )
    )
    assert ok is True


def test_wait_stop_returns_false(tmp_path: Path) -> None:
    ctl.send_command(tmp_path, "stop")
    ok = asyncio.run(
        rr._wait_for_reset(
            tmp_path,
            time.time() + 100,
            "five_hour",
            ctl,
            buffer_s=0,
            backoff_s=100,
            poll_interval=0.05,
        )
    )
    assert ok is False and ctl.is_checkpoint_stop(tmp_path) is True


def test_wait_resume_returns_true(tmp_path: Path) -> None:
    ctl.send_command(tmp_path, "resume")
    ok = asyncio.run(
        rr._wait_for_reset(
            tmp_path,
            time.time() + 100,
            "five_hour",
            ctl,
            buffer_s=0,
            backoff_s=100,
            poll_interval=0.05,
        )
    )
    assert ok is True


# --- _drive_across_limits orchestration (SC3/SC5/SC6) -------------------------


class _Ctx:
    def __init__(self, resume):
        self.resume = resume

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _run_loop(tmp_path, driver, waiter, **kw):
    factories = []

    def factory(resume):
        factories.append(resume)
        return _Ctx(resume)

    asyncio.run(
        rr._drive_across_limits(
            factory,
            tmp_path,
            "sess",
            0.0,
            ctl,
            first_query="TASK",
            driver=driver,
            waiter=waiter,
            **kw
        )
    )
    return factories


def test_loop_detect_wait_resume_complete(tmp_path: Path) -> None:
    queries = []

    async def driver(client, query, *a):
        queries.append(query)
        if len(queries) == 1:
            return rr.RateLimitHit(resets_at=time.time() + 1, rate_limit_type="five_hour")
        return None

    async def waiter(*a, **k):
        return True

    factories = _run_loop(tmp_path, driver, waiter)
    assert queries == ["TASK", rr.RESUME_NUDGE]
    assert factories == [False, True]  # reconnect switched to resume mode
    assert ctl.read_rate_limit(tmp_path) is None  # marker cleared after resume


def test_loop_no_auto_wait_exits(tmp_path: Path) -> None:
    queries = []

    async def driver(client, query, *a):
        queries.append(query)
        return rr.RateLimitHit(resets_at=time.time() + 1, rate_limit_type="five_hour")

    async def waiter(*a, **k):
        raise AssertionError("must not wait under no_auto_wait")

    _run_loop(tmp_path, driver, waiter, no_auto_wait=True)
    assert queries == ["TASK"]


def test_loop_weekly_cap_exits(tmp_path: Path) -> None:
    queries = []

    async def driver(client, query, *a):
        queries.append(query)
        return rr.RateLimitHit(resets_at=time.time() + 1, rate_limit_type="seven_day")

    async def waiter(*a, **k):
        raise AssertionError("must not wait for weekly cap")

    _run_loop(tmp_path, driver, waiter)
    assert queries == ["TASK"]


def test_loop_stop_during_wait_exits(tmp_path: Path) -> None:
    queries = []

    async def driver(client, query, *a):
        queries.append(query)
        return rr.RateLimitHit(resets_at=time.time() + 1, rate_limit_type="five_hour")

    async def waiter(*a, **k):
        return False  # operator stopped during the wait

    _run_loop(tmp_path, driver, waiter)
    assert queries == ["TASK"]  # no resume


def test_loop_with_real_waiter_completes(tmp_path: Path) -> None:
    """Integration: detect → REAL _wait_for_reset (near-future reset) → reconnect(resume) → complete."""
    queries = []

    async def driver(client, query, *a):
        queries.append(query)
        if len(queries) == 1:
            return rr.RateLimitHit(resets_at=time.time() + 0.2, rate_limit_type="five_hour")
        return None

    factories = _run_loop(tmp_path, driver, rr._wait_for_reset, buffer_s=0.0)
    assert queries == ["TASK", rr.RESUME_NUDGE]
    assert factories == [False, True]
    assert ctl.read_rate_limit(tmp_path) is None


def test_loop_caps_repeated_limits(tmp_path: Path) -> None:
    queries = []

    async def driver(client, query, *a):
        queries.append(query)
        return rr.RateLimitHit(
            resets_at=time.time() + 1, rate_limit_type="five_hour"
        )  # always limited

    async def waiter(*a, **k):
        return True

    _run_loop(tmp_path, driver, waiter, max_waits=3)
    # 1 initial + 3 resume attempts, then give up (no 5th).
    assert len(queries) == 4


# --- Layer 0: driver becomes sink/heartbeat-parametric (UI-control spec) -------
# The rate-limit wrapper must forward render/poll_interval/freeze/unfreeze to the
# inner driver and on_wait_tick to the waiter, so a UI run can route messages to
# the browser and keep the stream alive during a multi-hour hold. Defaults keep
# CLI behavior (covered by the tests above, still green).


def test_loop_forwards_render_and_poll_to_driver(tmp_path: Path) -> None:
    """render/poll_interval reach the inner driver positionally (mirrors its signature)."""
    sentinel_render = object()
    captured = {}

    async def driver(client, query, *a):
        captured["a"] = a  # (session_name, workdir, start, render, poll_interval, freeze, unfreeze)
        return None  # complete immediately

    async def waiter(*a, **k):
        return True

    _run_loop(tmp_path, driver, waiter, render=sentinel_render, poll_interval=0.25)
    assert captured["a"][3] is sentinel_render
    assert captured["a"][4] == 0.25


def test_loop_forwards_on_wait_tick_to_waiter(tmp_path: Path) -> None:
    """on_wait_tick reaches the waiter as a keyword (fake waiter captures **k)."""
    cb = lambda rem: None  # noqa: E731
    seen = {}

    async def driver(client, query, *a):
        if "once" not in seen:
            seen["once"] = True
            return rr.RateLimitHit(resets_at=time.time() + 1, rate_limit_type="five_hour")
        return None

    async def waiter(*a, **k):
        seen["kw"] = k
        return True

    _run_loop(tmp_path, driver, waiter, on_wait_tick=cb)
    assert seen["kw"].get("on_wait_tick") is cb


def test_wait_for_reset_fires_heartbeat(tmp_path: Path, monkeypatch) -> None:
    """With the 60 s gate forced to 0, on_wait_tick fires each iteration with remaining>=0."""
    monkeypatch.setattr(rr, "_RATE_LIMIT_HEARTBEAT_S", 0.0)
    ticks = []

    ok = asyncio.run(
        rr._wait_for_reset(
            tmp_path,
            time.time() + 0.15,
            "five_hour",
            ctl,
            0.0,
            0.0,
            poll_interval=0.05,
            on_wait_tick=lambda rem: ticks.append(rem),
        )
    )
    assert ok is True
    assert len(ticks) >= 1
    assert all(t >= 0.0 for t in ticks)


def test_wait_for_reset_heartbeat_error_is_swallowed(tmp_path: Path, monkeypatch) -> None:
    """A raising heartbeat must not break the wait (best-effort)."""
    monkeypatch.setattr(rr, "_RATE_LIMIT_HEARTBEAT_S", 0.0)

    def boom(_rem):
        raise RuntimeError("heartbeat blew up")

    ok = asyncio.run(
        rr._wait_for_reset(
            tmp_path,
            time.time() + 0.15,
            None,
            ctl,
            0.0,
            0.0,
            poll_interval=0.05,
            on_wait_tick=boom,
        )
    )
    assert ok is True
