"""Which ``claude`` binary the SDK spawns — and why it decides which model runs.

Two things ride on this. First, **the CLI resolves model aliases**: the SDK
prefers the copy bundled in its own wheel over the one on PATH, so a stale
bundle silently pins ``model="opus"`` to whichever Opus shipped with that wheel.
Second, **a batch shim destroys multi-line arguments**: on Windows PATH lookup
finds ``claude.CMD``, and CMD reads a newline as a command separator, so a
multi-line system prompt never arrives intact — the SDK then waits out its whole
handshake and reports ``Control request timeout: initialize``, which reads as a
broken agent rather than a broken launcher. Measured 2026-08-20 on one 4210-char
prompt: 70.2s and a timeout through the shim, 3.6s and success through
``claude.exe``.

The rule is implemented twice — ``run_research.resolve_cli_path`` and
``cc_critic_runner.resolve_cli`` — because the critic runner must not import
``run_research`` (that import loads ``.env`` and pops placeholder API keys).
Two copies can drift, so the last test here is the guard that they do not.
"""

import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

import cc_critic_runner as ccr  # noqa: E402
import run_research  # noqa: E402


def _shim_beside_real(tmp_path):
    shim = tmp_path / "claude.CMD"
    shim.write_text("@echo off", encoding="utf-8")
    real = tmp_path / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
    real.parent.mkdir(parents=True)
    real.write_bytes(b"MZ")
    return shim, real


def test_resolve_cli_path_skips_the_batch_shim(tmp_path, monkeypatch):
    shim, real = _shim_beside_real(tmp_path)
    monkeypatch.setattr(run_research.shutil, "which", lambda _: str(shim))
    monkeypatch.delenv("CLAUDE_CLI_PATH", raising=False)
    assert run_research.resolve_cli_path() == str(real)


def test_resolve_cli_path_keeps_the_shim_when_nothing_better_is_there(tmp_path, monkeypatch):
    shim = tmp_path / "claude.CMD"
    shim.write_text("@echo off", encoding="utf-8")
    monkeypatch.setattr(run_research.shutil, "which", lambda _: str(shim))
    monkeypatch.delenv("CLAUDE_CLI_PATH", raising=False)
    assert run_research.resolve_cli_path() == str(shim)


def test_resolve_cli_path_falls_back_to_the_bundle_when_no_cli_is_installed(monkeypatch):
    """None is the SDK's own default, so a host without the CLI keeps working."""
    monkeypatch.setattr(run_research.shutil, "which", lambda _: None)
    monkeypatch.delenv("CLAUDE_CLI_PATH", raising=False)
    assert run_research.resolve_cli_path() is None


def test_the_two_resolvers_agree(tmp_path, monkeypatch):
    shim, real = _shim_beside_real(tmp_path)
    plain = tmp_path / "claude"
    plain.write_bytes(b"#!/bin/sh")
    monkeypatch.delenv("CLAUDE_CLI_PATH", raising=False)

    for found in (str(shim), str(plain), None):
        monkeypatch.setattr(run_research.shutil, "which", lambda _, f=found: f)
        monkeypatch.setattr(ccr.shutil, "which", lambda _, f=found: f)
        assert run_research.resolve_cli_path() == ccr.resolve_cli()


# Model selection is configured through the UI API, not an App.tsx slug.
# Its behavior is covered by test_cc_runner_model.py and launch-config.test.ts.
