"""Canonical CC settings; no imports with model, auth or service side effects."""

from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
from typing import Mapping

import yaml

PROJECT_DIR = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_DIR / "config/claude-code.yaml"
COMPAT_PATH = PROJECT_DIR / ".mcp.json"


def _mapping(value, label: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"Claude Code configuration: {label} must be a mapping.")
    return value


def _read(path: Path | None = None) -> dict:
    return _mapping(
        yaml.safe_load((path or CONFIG_PATH).read_text(encoding="utf-8")), "root"
    )


def load_mcp_servers(
    profile: str = "general",
    *,
    include_common: bool = True,
    environ: Mapping[str, str] | None = None,
    config_path: Path | None = None,
) -> dict:
    data = _read(config_path)
    profiles = _mapping(data.get("profiles", {"general": {}}), "profiles")
    if profile not in profiles:
        raise ValueError(f"Unknown MCP profile: {profile}")
    specific = _mapping(profiles[profile], f"profile {profile}")
    common = _mapping(data.get("mcpServers", {}), "mcpServers")
    overrides = _mapping(
        specific.get("mcpServers", {}), f"profile {profile} mcpServers"
    )
    servers = copy.deepcopy({**(common if include_common else {}), **overrides})
    environment = os.environ if environ is None else environ
    for name, entry in servers.items():
        server = _mapping(entry, f"MCP server {name}")
        url_env = server.pop("url_env", None)
        if url_env is not None:
            if not isinstance(url_env, str):
                raise ValueError(
                    f"MCP server {name}: url_env must name an environment variable."
                )
            server["url"] = environment.get(url_env, server.get("url"))
        if server.get("type") in {"http", "sse"} and not isinstance(
            server.get("url"), str
        ):
            raise ValueError(f"MCP server {name}: a URL is required.")
    return servers


def cli_environment(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    configured = _mapping(_read().get("cli_env", {}), "cli_env")
    if any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in configured.items()
    ):
        raise ValueError("Claude Code cli_env keys and values must be strings.")
    # The SDK inherits proxy/CA/auth settings itself. Only explicit application
    # settings override inheritance, including TLS verification even if an
    # earlier process or caller supplied NODE_TLS_REJECT_UNAUTHORIZED=0.
    return {**(extra or {}), **configured}


def compatibility_config() -> dict:
    # This committed CLI-compatible file contains defaults only. Never serialize
    # expanded runtime env values or the OAuth headers added by the runner.
    return {"mcpServers": load_mcp_servers(environ={})}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write-compat",
        action="store_true",
        help="Regenerate .mcp.json from YAML defaults",
    )
    args = parser.parse_args()
    expected = compatibility_config()
    if args.write_compat:
        COMPAT_PATH.write_text(json.dumps(expected, indent=2) + "\n", encoding="utf-8")
        print("Updated .mcp.json from config/claude-code.yaml")
        return
    if json.loads(COMPAT_PATH.read_text(encoding="utf-8")) != expected:
        raise SystemExit(
            ".mcp.json differs from YAML; run: uv run python scripts/cc_config.py --write-compat"
        )
    print("MCP compatibility config matches YAML defaults")


if __name__ == "__main__":
    main()
