"""App-level team auth on the Claude-Code runner: the run gate.

Mirrors the CodeArkt-side gate (tests/../codearkt/tests/test_server_auth.py):
with a login list configured, POST /agents/manager needs a valid signed bearer
token, and a forged X-Holo-Client cannot bypass it. The happy path (a real run)
is covered by the CodeArkt integration test + the live e2e check, since spawning
the SDK here would require the Claude CLI.
"""

import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterator

import pytest
from httpx import ASGITransport, AsyncClient

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "codearkt"))
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

from codearkt import auth  # noqa: E402

import cc_runner  # noqa: E402


@pytest.fixture()
def auth_env() -> Iterator[None]:
    saved = {k: os.environ.get(k) for k in ("HOLO_AUTH_USERS", "HOLO_AUTH_SECRET")}
    os.environ["HOLO_AUTH_USERS"] = "a.stepin:pw-one"
    os.environ["HOLO_AUTH_SECRET"] = "cc-test-secret"
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


async def _post_manager(headers: Dict[str, str]) -> Any:
    app = cc_runner.create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(
            "/agents/manager",
            headers=headers,
            json={
                "messages": [{"role": "user", "content": "hi"}],
                "session_id": "cc-auth-sess",
                "stream": True,
                "orchestrator": "claude_code",
            },
        )


def test_manager_requires_token_when_auth_enabled(auth_env: None) -> None:
    resp = asyncio.run(_post_manager({}))
    assert resp.status_code == 401


def test_manager_rejects_forged_client_header(auth_env: None) -> None:
    resp = asyncio.run(_post_manager({"X-Holo-Client": "a.stepin"}))
    assert resp.status_code == 401


def test_manager_rejects_invalid_bearer(auth_env: None) -> None:
    resp = asyncio.run(_post_manager({"Authorization": "Bearer not.a.token"}))
    assert resp.status_code == 401


def test_verify_accepts_token_minted_for_runner(auth_env: None) -> None:
    # Cross-check: a token minted with the shared secret verifies here (the CC
    # runner and the CodeArkt login share codearkt.auth + HOLO_AUTH_SECRET).
    token = auth.mint_token("a.stepin")
    assert auth.verify_token(f"Bearer {token}") == "a.stepin"


def test_get_dispatcher_switches_to_shared_ledger_when_auth_on(auth_env: None) -> None:
    # Auth on -> the run pool is the cross-process ledger (unified 6/3 across
    # both engines), not the plain process-local CC pool. Resolved lazily so the
    # import-time DISPATCHER (SC6 disjoint default) is unaffected.
    dispatcher = cc_runner.get_dispatcher()
    assert dispatcher is not cc_runner.DISPATCHER
    assert dispatcher._ledger is not None
    assert dispatcher._per_owner_limit == 3


def test_get_dispatcher_is_plain_pool_when_auth_off() -> None:
    saved = {k: os.environ.get(k) for k in ("HOLO_AUTH_USERS", "HOLO_AUTH_SECRET")}
    os.environ.pop("HOLO_AUTH_USERS", None)
    os.environ.pop("HOLO_AUTH_SECRET", None)
    try:
        assert cc_runner.get_dispatcher() is cc_runner.DISPATCHER
    finally:
        for key, value in saved.items():
            if value is not None:
                os.environ[key] = value
