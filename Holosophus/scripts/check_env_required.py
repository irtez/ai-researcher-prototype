#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


REQUIRED_NON_EMPTY_KEYS = (
    "OPENROUTER_API_KEY",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "OPENAI_API_KEY",
)

SEARCH_PROVIDER_KEYS = (
    "TAVILY_API_KEY",
    "S2_API_KEY",
    "SERPER_API_KEY",
    "BRAVE_SEARCH_API_KEY",
)

OPTIONAL_WARN_KEYS = ("GEMINI_API_KEY",)


def _load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'").strip('"')
    return values


def _resolve_value(key: str, env_file_values: dict[str, str]) -> str:
    runtime_value = os.getenv(key)
    if runtime_value is not None:
        return runtime_value.strip()
    return env_file_values.get(key, "").strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate runtime keys for the legacy CodeArkt/Langfuse v2 compose stack (not source verification or Claude Code).")
    parser.add_argument("--env-file", default=".env", help="Path to env file (default: .env).")
    args = parser.parse_args()

    env_path = Path(args.env_file).resolve()
    env_values = _load_env_file(env_path)
    errors: list[str] = []
    warnings: list[str] = []

    missing_required = [key for key in REQUIRED_NON_EMPTY_KEYS if not _resolve_value(key, env_values)]
    if missing_required:
        errors.append(
            "Missing required non-empty keys: " + ", ".join(missing_required)
        )

    has_search_provider = any(_resolve_value(key, env_values) for key in SEARCH_PROVIDER_KEYS)
    if not has_search_provider:
        errors.append(
            "At least one search provider key must be set: "
            + ", ".join(SEARCH_PROVIDER_KEYS)
        )

    for key in OPTIONAL_WARN_KEYS:
        if not _resolve_value(key, env_values):
            warnings.append(
                f"{key} is empty (this can break Gemini-based flows later)."
            )

    if errors:
        print("ERROR: env preflight check failed.")
        print(f"Checked file: {env_path}")
        for item in errors:
            print(f"- {item}")
        print("Fix .env and run again.")
        return 1

    print("OK: env preflight check passed.")
    if warnings:
        for item in warnings:
            print(f"WARN: {item}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
