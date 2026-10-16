"""Bounded Semantic Scholar retries; unrelated HTTP clients keep their policy."""

from contextlib import contextmanager
from contextvars import ContextVar
from email.utils import parsedate_to_datetime
import math
import secrets
import threading
from time import monotonic, sleep, time
from typing import Iterator

import requests
from urllib3.util import Timeout

from academia_mcp.settings import settings
from academia_mcp.utils import USER_AGENT

REQUEST_BUDGET_SECONDS = 30.0
REFERENCE_BUDGET_SECONDS = 60.0
_deadline: ContextVar[float | None] = ContextVar("s2_deadline", default=None)
_rate_lock = threading.Lock()
_next_key_request = 0.0
_cooldown_until = 0.0


@contextmanager
def request_budget(seconds: float) -> Iterator[None]:
    deadline = monotonic() + seconds
    parent = _deadline.get()
    token = _deadline.set(min(parent, deadline) if parent is not None else deadline)
    try:
        yield
    finally:
        _deadline.reset(token)


def budget_remaining() -> float:
    deadline = _deadline.get()
    return math.inf if deadline is None else max(0.0, deadline - monotonic())


def _wait(delay: float, deadline: float, response=None) -> None:
    if monotonic() + max(0.0, delay) >= deadline:
        raise requests.Timeout(
            "Semantic Scholar time budget exhausted", response=response
        )
    if delay > 0:
        sleep(delay)


def _request_slot(deadline: float, authenticated: bool) -> None:
    # API-key quota is shared by this process's S2 tools, including bibliography checks.
    global _next_key_request
    while True:
        with _rate_lock:
            now = monotonic()
            ready = max(_cooldown_until, _next_key_request if authenticated else 0.0)
            if now >= ready:
                if now >= deadline:
                    raise requests.Timeout("Semantic Scholar time budget exhausted")
                if authenticated:
                    _next_key_request = now + 1.0
                return
        _wait(ready - now, deadline)


def _retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = parsedate_to_datetime(value).timestamp() - time()
        except (ValueError, TypeError, OverflowError):
            return None
    return max(0.0, seconds) if math.isfinite(seconds) else None


def get(url: str, *, params: dict, proxies_list: list[str]) -> requests.Response:
    global _cooldown_until
    deadline = monotonic() + min(REQUEST_BUDGET_SECONDS, budget_remaining())
    retries = max(0, min(settings.S2_MAX_RETRIES, 3))
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    key = (settings.S2_API_KEY or "").strip()
    if key:
        headers["x-api-key"] = key
    proxies = None
    if proxies_list:
        # Retain configured routing for the entire call; never rotate on rate limits.
        proxy = secrets.choice(proxies_list)
        proxies = {"http": proxy, "https": proxy}
    with requests.Session() as session:
        for attempt in range(retries + 1):
            _request_slot(deadline, bool(key))
            remaining = deadline - monotonic()
            timeout = Timeout(
                total=remaining, connect=min(3.0, remaining), read=min(5.0, remaining)
            )
            try:
                response = session.get(
                    url,
                    params=params,
                    headers=headers,
                    proxies=proxies,
                    timeout=timeout,
                    allow_redirects=False,
                )
            except (requests.Timeout, requests.ConnectionError):
                if attempt == retries:
                    raise
                _wait(2.0 * 2**attempt, deadline)
                continue
            if response.status_code in (429, 500, 502, 503, 504):
                delay = _retry_after(response.headers.get("Retry-After"))
                if delay is not None:
                    with _rate_lock:
                        _cooldown_until = max(_cooldown_until, monotonic() + delay)
                else:
                    delay = 2.0 * 2**attempt
                if attempt == retries:
                    response.raise_for_status()
                _wait(delay, deadline, response)
                continue
            response.raise_for_status()
            if 300 <= response.status_code < 400:
                # Do not forward the API key to an unexpected redirect destination.
                raise requests.HTTPError(
                    "Unexpected Semantic Scholar redirect", response=response
                )
            return response
    raise AssertionError("Unreachable retry state")
