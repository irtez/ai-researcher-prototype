"""App-level team auth: an allowed-user list plus HMAC-signed bearer tokens.

This is the "public read, gated run" login layer (see
``docs/plans/2026-07-05-ui-login-and-per-account-queue.md``): anonymous users
may browse the UI, but starting a run requires a login. Unlike the nginx
Basic-auth overlay (``docker-compose.auth.yml``) it does NOT gate the whole
site, and it verifies a signed token in the app instead of trusting a header
nginx rewrote -- so it works identically behind nginx (prod) or the Vite dev
proxy, with no nginx changes.

Pure stdlib on purpose (``hmac``/``hashlib``/``base64``/``time``/``os``): it
must import without triggering ``codearkt.settings`` validation (which requires
``OPENROUTER_API_KEY``) and under the pinned codearkt wheel, and it is imported
by both the CodeArkt server (``server.py``) and the host-side Claude-Code runner
(``scripts/cc_runner.py``).

Config comes from the environment (loaded from ``.env`` by each process):

* ``HOLO_AUTH_USERS``  -- ``"user1:pass1,user2:pass2"`` (plaintext passwords;
  trusted internal contour, ``.env`` is gitignored). Empty/unset disables auth
  entirely, which keeps dev/smoke/CI runs working without a login.
* ``HOLO_AUTH_SECRET`` -- the HMAC key tokens are signed with.
* ``HOLO_AUTH_TOKEN_TTL_DAYS`` -- token lifetime in days (default 30).

Token format: ``b64url(payload) "." b64url(sig)`` where ``payload`` is
``"<username>|<exp_unix_ts>"`` and ``sig = HMAC-SHA256(secret, payload)``.
Any process holding ``HOLO_AUTH_SECRET`` can verify a token with no shared
state, so the two runner processes stay independent.
"""

import base64
import hashlib
import hmac
import os
import time
from typing import Dict, Optional

DEFAULT_TOKEN_TTL_DAYS = 30.0


def load_users() -> Dict[str, str]:
    """Parse ``HOLO_AUTH_USERS`` into a ``{username: password}`` map.

    Malformed entries (no ``:`` separator, blank username) are skipped rather
    than raised, so a typo in one entry can't take down login for everyone.
    """
    raw = os.environ.get("HOLO_AUTH_USERS", "").strip()
    users: Dict[str, str] = {}
    if not raw:
        return users
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        username, _, password = entry.partition(":")
        username = username.strip()
        if username:
            users[username] = password
    return users


def _secret() -> bytes:
    return os.environ.get("HOLO_AUTH_SECRET", "").encode("utf-8")


def auth_enabled() -> bool:
    """True once both a user list and a signing secret are configured.

    When False the callers skip the login gate entirely (backward-compatible:
    dev/smoke/CI runs that never log in keep working, exactly as before this
    feature).
    """
    return bool(load_users()) and bool(_secret())


def check_password(username: str, password: str) -> bool:
    """Constant-time verify ``password`` for ``username`` against the env list."""
    users = load_users()
    expected = users.get(username)
    if expected is None:
        # Compare against a dummy of equal handling so a missing user and a
        # wrong password take a similar path (don't leak user existence via a
        # trivially different code path).
        hmac.compare_digest(password or "", password or "")
        return False
    return hmac.compare_digest(expected, password or "")


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _sign(payload: bytes) -> bytes:
    return hmac.new(_secret(), payload, hashlib.sha256).digest()


def token_ttl_seconds() -> int:
    try:
        days = float(os.environ.get("HOLO_AUTH_TOKEN_TTL_DAYS", str(DEFAULT_TOKEN_TTL_DAYS)))
    except ValueError:
        days = DEFAULT_TOKEN_TTL_DAYS
    return int(days * 86400)


def mint_token(username: str, now: Optional[float] = None) -> str:
    """Issue a signed bearer token for ``username`` (call after a password check).

    ``now`` is injectable for tests; production passes ``None`` (wall clock).
    """
    issued = time.time() if now is None else now
    exp = int(issued) + token_ttl_seconds()
    payload = f"{username}|{exp}".encode("utf-8")
    return f"{_b64e(payload)}.{_b64e(_sign(payload))}"


def verify_token(token: Optional[str], now: Optional[float] = None) -> Optional[str]:
    """Return the username a valid token authenticates, else ``None``.

    Accepts either a bare token or an ``"Authorization: Bearer <token>"`` value.
    Rejects a tampered signature, an expired token, or a user no longer on the
    allowed list. Never raises -- any decode error is an invalid token.
    """
    if not token:
        return None
    token = token.strip()
    if token.lower().startswith("bearer "):
        token = token[len("bearer ") :].strip()
    if "." not in token:
        return None
    payload_b64, _, sig_b64 = token.partition(".")
    try:
        payload = _b64d(payload_b64)
        sig = _b64d(sig_b64)
    except (ValueError, TypeError):
        return None
    if not hmac.compare_digest(sig, _sign(payload)):
        return None
    try:
        username, _, exp_str = payload.decode("utf-8").rpartition("|")
        exp = int(exp_str)
    except (ValueError, UnicodeDecodeError):
        return None
    if not username:
        return None
    checked_at = time.time() if now is None else now
    if checked_at > exp:
        return None
    # A user removed from HOLO_AUTH_USERS is locked out immediately, even if
    # they still hold a not-yet-expired token.
    if username not in load_users():
        return None
    return username
