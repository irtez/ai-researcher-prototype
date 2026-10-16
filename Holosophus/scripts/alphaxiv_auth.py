#!/usr/bin/env python3
"""AlphaXiv OAuth (DCR + PKCE) for headless Claude Agent SDK runs.

AlphaXiv MCP requires OAuth via Clerk (no static API key). Claude Code's interactive
``/mcp alphaxiv`` flow does not produce reusable tokens for SDK subprocesses
(see project memory ``project_alphaxiv_oauth.md``). This script does its own
DCR + authorization-code+PKCE flow, stores tokens locally, and exposes
``get_access_token()`` for ``run_research.py`` to inject ``Authorization: Bearer …``
via ``McpHttpServerConfig.headers``.

Usage:
    uv run python scripts/alphaxiv_auth.py login    # one-time browser auth
    uv run python scripts/alphaxiv_auth.py status   # show token validity
    uv run python scripts/alphaxiv_auth.py refresh  # force refresh
    uv run python scripts/alphaxiv_auth.py probe    # try tools/list with current Bearer

Library use:
    from scripts.alphaxiv_auth import get_access_token
    token = get_access_token()
    if token:
        mcp_servers["alphaxiv"]["headers"] = {"Authorization": f"Bearer {token}"}
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import sys
import time
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any

CLERK = "https://clerk.alphaxiv.org"
RESOURCE = "https://api.alphaxiv.org/"
MCP_URL = "https://api.alphaxiv.org/mcp/v1"
SCOPES = "openid profile email offline_access"
PORT = 8765
REDIRECT = f"http://localhost:{PORT}/callback"
USER_AGENT = "holosophus-alphaxiv-auth/1.0 (curl-compatible)"

CONFIG_DIR = Path.home() / ".config" / "holosophus"
CLIENT_FILE = CONFIG_DIR / "alphaxiv_client.json"
TOKENS_FILE = CONFIG_DIR / "alphaxiv_tokens.json"


def _atomic_write_json(path: Path, data: dict[str, Any], mode: int = 0o600) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.chmod(tmp, mode)
    tmp.replace(path)


def _http_post_json(url: str, body: dict[str, Any]) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())  # type: ignore[no-any-return]
    except urllib.error.HTTPError as e:
        body_text = e.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {e.code} from {url}: {body_text}") from e


def _http_post_form(url: str, fields: dict[str, str]) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(fields).encode(),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())  # type: ignore[no-any-return]
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {e.code} from {url}: {body}") from e


def register_client() -> dict[str, Any]:
    if CLIENT_FILE.exists():
        client: dict[str, Any] = json.loads(CLIENT_FILE.read_text())
        return client

    body = {
        "client_name": "holosophus-headless",
        "redirect_uris": [REDIRECT],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "client_secret_post",
        "scope": SCOPES,
    }
    client = _http_post_json(f"{CLERK}/oauth/register", body)
    _atomic_write_json(CLIENT_FILE, client)
    print(f"Registered client {client['client_id']} → {CLIENT_FILE}", flush=True)
    return client


def _make_pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    return verifier, challenge


def login() -> dict[str, Any]:
    client = register_client()
    verifier, challenge = _make_pkce()
    state = secrets.token_urlsafe(16)

    auth_url = f"{CLERK}/oauth/authorize?" + urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client["client_id"],
            "redirect_uri": REDIRECT,
            "scope": SCOPES,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "prompt": "consent",
            "resource": RESOURCE,
        }
    )

    code_holder: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            code_holder["code"] = qs.get("code", [""])[0]
            code_holder["state"] = qs.get("state", [""])[0]
            err = qs.get("error", [""])[0]
            if err:
                code_holder["error"] = err
                code_holder["error_description"] = qs.get("error_description", [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            msg = "Authentication received. You can close this tab." if not err else f"Error: {err}"
            self.wfile.write(f"<h1>{msg}</h1>".encode())

        def log_message(self, *a: Any, **kw: Any) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Opening browser ...\n  {auth_url}\n", flush=True)
    webbrowser.open(auth_url)
    print(f"Waiting for callback on {REDIRECT} ...", flush=True)
    server.handle_request()
    server.server_close()

    if "error" in code_holder:
        raise RuntimeError(
            f"OAuth error: {code_holder['error']} — {code_holder.get('error_description', '')}"
        )
    if not code_holder.get("code"):
        raise RuntimeError("No authorization code received")
    if code_holder.get("state") != state:
        raise RuntimeError(f"State mismatch: expected {state!r}, got {code_holder.get('state')!r}")

    tokens = _http_post_form(
        f"{CLERK}/oauth/token",
        {
            "grant_type": "authorization_code",
            "code": code_holder["code"],
            "redirect_uri": REDIRECT,
            "code_verifier": verifier,
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
        },
    )
    tokens["expires_at"] = int(time.time()) + int(tokens.get("expires_in", 3600))
    _atomic_write_json(TOKENS_FILE, tokens)
    print(
        f"Saved tokens → {TOKENS_FILE}\n"
        f"  access_token  : {tokens['access_token'][:18]}...\n"
        f"  refresh_token : {'present' if tokens.get('refresh_token') else 'MISSING'}\n"
        f"  expires_in    : {tokens.get('expires_in')}s",
        flush=True,
    )
    return tokens


def refresh_tokens() -> dict[str, Any]:
    if not TOKENS_FILE.exists():
        raise RuntimeError("No tokens — run `login` first")
    tokens = json.loads(TOKENS_FILE.read_text())
    if "refresh_token" not in tokens:
        raise RuntimeError("Stored tokens have no refresh_token — re-run `login`")
    if not CLIENT_FILE.exists():
        raise RuntimeError("No client registration — re-run `login`")

    client = json.loads(CLIENT_FILE.read_text())
    new = _http_post_form(
        f"{CLERK}/oauth/token",
        {
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
        },
    )
    new["expires_at"] = int(time.time()) + int(new.get("expires_in", 3600))
    if "refresh_token" not in new:
        new["refresh_token"] = tokens["refresh_token"]
    _atomic_write_json(TOKENS_FILE, new)
    return new


def get_access_token() -> str | None:
    """Return a valid access token, refreshing if near expiry. None if not logged in."""
    if not TOKENS_FILE.exists():
        return None
    tokens = json.loads(TOKENS_FILE.read_text())
    if int(time.time()) > int(tokens.get("expires_at", 0)) - 60:
        try:
            tokens = refresh_tokens()
        except Exception as e:
            print(f"WARNING: alphaxiv token refresh failed ({e})", flush=True)
            return None
    return tokens.get("access_token")


def status() -> None:
    if not TOKENS_FILE.exists():
        print("Not logged in. Run `login` first.")
        return
    tokens = json.loads(TOKENS_FILE.read_text())
    now = int(time.time())
    expires_at = int(tokens.get("expires_at", 0))
    print(f"Tokens file       : {TOKENS_FILE}")
    print(f"access_token      : {tokens.get('access_token', '')[:18]}...")
    print(f"refresh_token     : {'present' if tokens.get('refresh_token') else 'MISSING'}")
    print(f"expires_at        : {datetime.fromtimestamp(expires_at)} ({expires_at - now}s)")
    print(
        f"client_id (DCR)   : {(json.loads(CLIENT_FILE.read_text()) if CLIENT_FILE.exists() else {}).get('client_id', '?')}"
    )


def probe() -> None:
    token = get_access_token()
    if not token:
        print("No token. Run `login` first.", file=sys.stderr)
        sys.exit(2)

    import asyncio

    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async def _probe() -> None:
        async with streamablehttp_client(
            MCP_URL,
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
            sse_read_timeout=30,
        ) as (r, w, _):
            async with ClientSession(r, w) as session:
                await session.initialize()
                resp = await session.list_tools()
                tools = [t.name for t in resp.tools]
                print(f"alphaxiv MCP tools ({len(tools)}):")
                for t in tools:
                    print(f"  {t}")

    try:
        asyncio.run(_probe())
    except BaseException as e:
        cause = e
        while isinstance(cause, BaseExceptionGroup) and cause.exceptions:
            cause = cause.exceptions[0]
        print(f"PROBE FAILED: {cause.__class__.__name__}: {cause}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "login":
        login()
    elif cmd == "refresh":
        t = refresh_tokens()
        print(f"Refreshed. expires_at = {datetime.fromtimestamp(t['expires_at'])}")
    elif cmd == "status":
        status()
    elif cmd == "probe":
        probe()
    else:
        print(f"Unknown command: {cmd!r}. Use login|status|refresh|probe.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
