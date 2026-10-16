"""App-level team auth: user list, password check, HMAC token mint/verify."""

import pytest

from codearkt import auth


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOLO_AUTH_USERS", "a.stepin:pw-one,m.mozikov:pw-two")
    monkeypatch.setenv("HOLO_AUTH_SECRET", "0123456789abcdef")
    monkeypatch.setenv("HOLO_AUTH_TOKEN_TTL_DAYS", "30")


def test_load_users_parses_pairs() -> None:
    assert auth.load_users() == {"a.stepin": "pw-one", "m.mozikov": "pw-two"}


def test_load_users_skips_malformed_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOLO_AUTH_USERS", "good:pw, ,noseparator,:blankuser,other:pw2")
    assert auth.load_users() == {"good": "pw", "other": "pw2"}


def test_auth_enabled_requires_users_and_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    assert auth.auth_enabled() is True
    monkeypatch.setenv("HOLO_AUTH_USERS", "")
    assert auth.auth_enabled() is False
    monkeypatch.setenv("HOLO_AUTH_USERS", "a.stepin:pw-one")
    monkeypatch.setenv("HOLO_AUTH_SECRET", "")
    assert auth.auth_enabled() is False


def test_check_password() -> None:
    assert auth.check_password("a.stepin", "pw-one") is True
    assert auth.check_password("a.stepin", "wrong") is False
    assert auth.check_password("nobody", "pw-one") is False
    assert auth.check_password("a.stepin", "") is False


def test_mint_then_verify_round_trip() -> None:
    token = auth.mint_token("a.stepin")
    assert auth.verify_token(token) == "a.stepin"
    # Also accepts the "Bearer <token>" Authorization form.
    assert auth.verify_token(f"Bearer {token}") == "a.stepin"


def test_verify_rejects_tampered_signature() -> None:
    token = auth.mint_token("a.stepin")
    payload, _, sig = token.partition(".")
    tampered = f"{payload}.{sig[:-2]}xy"
    assert auth.verify_token(tampered) is None


def test_verify_rejects_forged_payload() -> None:
    # Re-sign nothing: swap the payload to another user but keep the old sig.
    token = auth.mint_token("a.stepin")
    _, _, sig = token.partition(".")
    forged_payload = auth._b64e(b"m.mozikov|9999999999")
    assert auth.verify_token(f"{forged_payload}.{sig}") is None


def test_verify_rejects_expired_token() -> None:
    token = auth.mint_token("a.stepin", now=1000.0)
    # TTL is 30 days; far-future check time is past expiry.
    assert auth.verify_token(token, now=1000.0 + 31 * 86400) is None
    assert auth.verify_token(token, now=1000.0 + 29 * 86400) == "a.stepin"


def test_verify_rejects_user_removed_from_list(monkeypatch: pytest.MonkeyPatch) -> None:
    token = auth.mint_token("a.stepin")
    monkeypatch.setenv("HOLO_AUTH_USERS", "m.mozikov:pw-two")  # a.stepin removed
    assert auth.verify_token(token) is None


def test_verify_none_and_garbage() -> None:
    assert auth.verify_token(None) is None
    assert auth.verify_token("") is None
    assert auth.verify_token("not-a-token") is None
    assert auth.verify_token("a.b.c") is None
