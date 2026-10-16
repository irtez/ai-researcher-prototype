"""Control channel + durable-session bookkeeping for the Claude Code pause/restart feature.

Implements the file-based, cross-platform control channel under
``<workdir>/.holosophus_ctl/`` that the headless orchestrator (``scripts/run_research.py``)
polls to pause / resume / stop a run, plus the small JSON side-files that make a run
durably resumable. Design: ``docs/plans/2026-08-12-pause-restart-cc.md``.

Everything here is pure filesystem + JSON (no Claude SDK, no asyncio), so it is
unit-testable in isolation. Reads/writes are UTF-8 (Windows defaults to cp1252 — an
explicit encoding is mandatory here) and writes are atomic (tmp + ``os.replace``),
mirroring the workspace-file protocol in ``mle_kit_mcp/mle_kit_mcp/capacity.py``.

Everything is keyed by the session's ``workdir`` (one control dir per
``workdir/<session>``), so pause is addressed to a session, never to "the container" —
the seam the future UI / multi-tenant rollout extends.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import uuid
from pathlib import Path
from typing import Callable, Optional

# Control directory + files (session-scoped: one set per workdir/<session>).
CTL_DIRNAME = ".holosophus_ctl"
COMMAND_FILENAME = "command"                   # operator writes: pause|resume|stop|inject
SESSION_FILENAME = "session.json"              # {session_id, workdir, question, created}
CHECKPOINT_STOP_FILENAME = "checkpoint_stop"   # Tier-2 flag; the job checks it between chunks
SANDBOX_FILENAME = "sandbox.json"              # mle_kit-published freeze descriptor (Tier-1)
# HITL (human-in-the-loop) files — docs/plans/2026-08-12-hitl-cc.md.
QUESTION_FILENAME = "question.json"            # orchestrator (ask_human) → operator/UI
ANSWER_FILENAME = "answer"                     # operator/UI → orchestrator (raw text)
ANSWER_JSON_FILENAME = "answer.json"           # operator/UI → orchestrator ({id, text})
INJECT_FILENAME = "inject_msg"                 # operator/UI → poller (steering text; with command=inject)
STEER_FILENAME = "steer.json"                  # operator/UI → poller ({text, mode: now|next}; with command=steer)
RATE_LIMIT_FILENAME = "rate_limit.json"        # orchestrator → operator/UI ("paused until reset"; auto-resume)

# Sentinel returned by ask_human when no human answered in time (agent proceeds autonomously).
NO_HUMAN_ANSWER_SENTINEL = "NO_HUMAN_ANSWER_PROCEED"

# Recognized control commands.
CMD_PAUSE = "pause"
CMD_RESUME = "resume"
CMD_STOP = "stop"
CMD_INJECT = "inject"                           # non-terminal: deliver operator text now (alias: steer now)
CMD_STEER = "steer"                             # non-terminal: deliver operator text with a break mode
VALID_COMMANDS = frozenset({CMD_PAUSE, CMD_RESUME, CMD_STOP, CMD_INJECT, CMD_STEER})

# Steer break modes (how a steer breaks into the run).
STEER_NOW = "now"     # interrupt the current step and apply immediately
STEER_NEXT = "next"   # apply at the next turn boundary (after the current step/job finishes)
STEER_MODES = frozenset({STEER_NOW, STEER_NEXT})


def new_session_id() -> str:
    """A fresh SDK/CLI session id — a valid UUID (the ``--session-id`` contract)."""
    return str(uuid.uuid4())


def ctl_dir(workdir: Path) -> Path:
    """Return ``<workdir>/.holosophus_ctl``, creating it if needed."""
    d = Path(workdir) / CTL_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _atomic_write(path: Path, text: str) -> None:
    """Write UTF-8 ``text`` atomically (temp file in the same dir + ``os.replace``)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# --- Durable session bookkeeping (enables --resume) ------------------------------

def write_session_info(
    workdir: Path,
    session_id: str,
    question: str,
    created: Optional[str] = None,
) -> Path:
    """Persist the run's SDK ``session_id`` + workdir + question so ``--resume`` can find it.

    Overwrite-idempotent. ``created`` may be supplied (an ISO string) for deterministic
    tests; otherwise it is stamped now.
    """
    if created is None:
        from datetime import datetime

        created = datetime.now().isoformat(timespec="seconds")
    payload = {
        "session_id": session_id,
        "workdir": str(workdir),
        "question": question,
        "created": created,
    }
    path = ctl_dir(workdir) / SESSION_FILENAME
    _atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2))
    return path


def read_session_info(workdir: Path) -> Optional[dict]:
    """Return the persisted session info, or ``None`` if missing/corrupt/without a
    ``session_id`` — the caller then falls back to the cold ``--workdir`` re-run."""
    path = Path(workdir) / CTL_DIRNAME / SESSION_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("session_id"):
        return None
    return data


# --- Control command channel -----------------------------------------------------

def send_command(workdir: Path, command: str) -> None:
    """Write a control command (operator / test side). Raises on an unknown command.

    In practice an operator just runs ``echo pause > <workdir>/.holosophus_ctl/command``;
    this helper is the typed equivalent.
    """
    if command not in VALID_COMMANDS:
        raise ValueError(
            f"unknown control command: {command!r} (want one of {sorted(VALID_COMMANDS)})"
        )
    _atomic_write(ctl_dir(workdir) / COMMAND_FILENAME, command)


def poll_command(workdir: Path) -> Optional[str]:
    """Read and *consume* the pending control command.

    Tolerates empty / partial / garbage content (returns ``None``); a missing file is
    ``None``. On any read the file is cleared (read-then-clear), so a stray/garbage write
    can never wedge the channel. Returns the command only if it is recognized.
    """
    path = Path(workdir) / CTL_DIRNAME / COMMAND_FILENAME
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    command = raw.strip().lower()
    try:  # consume regardless of validity
        path.unlink()
    except OSError:
        pass
    return command if command in VALID_COMMANDS else None


# --- Tier-2 cooperative checkpoint flag ------------------------------------------

def set_checkpoint_stop(workdir: Path) -> None:
    """Signal a chunked job to checkpoint at its next boundary and exit (Tier-2 durable pause)."""
    _atomic_write(ctl_dir(workdir) / CHECKPOINT_STOP_FILENAME, "1")


def is_checkpoint_stop(workdir: Path) -> bool:
    """True iff the Tier-2 checkpoint-stop flag is set (the job polls this between chunks)."""
    return (Path(workdir) / CTL_DIRNAME / CHECKPOINT_STOP_FILENAME).exists()


def clear_checkpoint_stop(workdir: Path) -> None:
    """Clear the Tier-2 flag (best-effort; used on resume)."""
    try:
        (Path(workdir) / CTL_DIRNAME / CHECKPOINT_STOP_FILENAME).unlink()
    except OSError:
        pass


# --- Tier-1 sandbox freeze descriptor (published by mle_kit; read by the controller) --

def write_sandbox_descriptor(workdir: Path, payload: dict) -> Path:
    """mle_kit publishes how to freeze *this session's* sandbox.

    The shape is intentionally open (forward-compat): today ``{"container": "<id>"}`` for
    the dedicated singleton container; later a per-session container id or ``{"pgid": ...}``
    for a process-group freeze. The pause controller resolves whatever is present without
    knowing the isolation strategy — so multi-tenant is a descriptor change, not a rewrite.
    """
    path = ctl_dir(workdir) / SANDBOX_FILENAME
    _atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2))
    return path


def read_sandbox_descriptor(workdir: Path) -> Optional[dict]:
    """Return the sandbox freeze descriptor, or ``None`` if absent/corrupt (Tier-1
    unavailable → the run can still pause via Tier-2)."""
    path = Path(workdir) / CTL_DIRNAME / SANDBOX_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# --- Tier-1 instant freeze (docker pause/unpause the session's sandbox) ----------

def _default_docker_runner(args: list) -> "subprocess.CompletedProcess":
    return subprocess.run(args, capture_output=True, text=True, timeout=30)


def _pause_or_unpause(workdir: Path, action: str, runner: Optional[Callable]) -> bool:
    """Resolve the session's sandbox container from ``sandbox.json`` and run
    ``docker <action> <container>``. Best-effort: no descriptor / docker error → False."""
    desc = read_sandbox_descriptor(workdir)
    container = (desc or {}).get("container")
    if not container:
        return False
    runner = runner or _default_docker_runner
    try:
        result = runner(["docker", action, str(container)])
    except Exception:  # noqa: BLE001 — docker missing/timeout must never crash the run
        return False
    return getattr(result, "returncode", 1) == 0


def freeze_sandbox(workdir: Path, runner: Optional[Callable] = None) -> bool:
    """Tier-1 instant freeze: ``docker pause`` the session's sandbox container (from
    ``sandbox.json``, published by mle_kit). Returns True on success.

    Bounded hold: a freeze longer than the running job's remaining ``timeout`` budget kills it
    on thaw (exit 124, confirmed by spike) — the idempotent-chunk discipline then re-runs the
    in-flight chunk. For reboot-durable pauses use the Tier-2 ``checkpoint_stop`` path instead.
    """
    return _pause_or_unpause(workdir, "pause", runner)


def unfreeze_sandbox(workdir: Path, runner: Optional[Callable] = None) -> bool:
    """Tier-1 thaw: ``docker unpause`` the session's sandbox container. Returns True on success."""
    return _pause_or_unpause(workdir, "unpause", runner)


# --- HITL: ask (run → human) — question / answer files ---------------------------

def write_question(
    workdir: Path,
    qid: str,
    question: str,
    context: Optional[str] = None,
    asked_at: Optional[str] = None,
) -> Path:
    """The ``ask_human`` tool publishes a pending question for the operator/UI to answer.

    ``qid`` is a fresh uuid; a later answer must carry the same id (or be untagged) to satisfy
    *this* question — the stale-answer guard.
    """
    if asked_at is None:
        from datetime import datetime

        asked_at = datetime.now().isoformat(timespec="seconds")
    payload = {"id": qid, "question": question, "context": context or "", "asked_at": asked_at}
    path = ctl_dir(workdir) / QUESTION_FILENAME
    _atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2))
    return path


def read_question(workdir: Path) -> Optional[dict]:
    """Return the pending question dict, or ``None`` if none/corrupt/without an ``id``."""
    path = Path(workdir) / CTL_DIRNAME / QUESTION_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("id") else None


def clear_question(workdir: Path) -> None:
    """Remove the pending-question file (best-effort)."""
    try:
        (Path(workdir) / CTL_DIRNAME / QUESTION_FILENAME).unlink()
    except OSError:
        pass


def write_answer(workdir: Path, text: str, qid: Optional[str] = None) -> None:
    """Operator/UI/test side: answer the pending question.

    With ``qid`` → write ``answer.json`` = ``{id, text}`` (id-matched). Without → write the raw
    ``answer`` file (untagged; matches whatever question is pending — the simple ``echo > answer`` path).
    """
    if qid is not None:
        _atomic_write(
            ctl_dir(workdir) / ANSWER_JSON_FILENAME,
            json.dumps({"id": qid, "text": text}, ensure_ascii=False),
        )
    else:
        _atomic_write(ctl_dir(workdir) / ANSWER_FILENAME, text)


def read_answer(workdir: Path) -> Optional[dict]:
    """Return ``{"id": <id or None>, "text": str}`` for a pending answer, or ``None``.

    Prefers the id-tagged ``answer.json``; falls back to the raw ``answer`` file (id ``None``).
    """
    d = Path(workdir) / CTL_DIRNAME
    try:
        data = json.loads((d / ANSWER_JSON_FILENAME).read_text(encoding="utf-8"))
        if isinstance(data, dict) and "text" in data:
            return {"id": data.get("id"), "text": str(data["text"])}
    except (OSError, ValueError):
        pass
    try:
        raw = (d / ANSWER_FILENAME).read_text(encoding="utf-8")
    except OSError:
        return None
    return {"id": None, "text": raw.strip()}


def clear_answer(workdir: Path) -> None:
    """Remove any answer files (best-effort)."""
    d = Path(workdir) / CTL_DIRNAME
    for fn in (ANSWER_FILENAME, ANSWER_JSON_FILENAME):
        try:
            (d / fn).unlink()
        except OSError:
            pass


def take_answer(workdir: Path, qid: str) -> Optional[str]:
    """Consume+return the answer text iff it matches ``qid`` (or is untagged); else ``None``.

    A tagged answer for a *different* question is ignored and left in place (the stale-answer guard),
    so a leftover answer can never silently satisfy a new question.
    """
    ans = read_answer(workdir)
    if ans is None:
        return None
    if ans["id"] is not None and ans["id"] != qid:
        return None
    clear_answer(workdir)
    return ans["text"]


# --- HITL: inject (human → run) — steering payload -------------------------------

def write_inject(workdir: Path, text: str) -> None:
    """Operator/UI side: stage steering text; then write ``command=inject`` (ordering matters —
    payload first, so the poller sees the text when it consumes the command)."""
    _atomic_write(ctl_dir(workdir) / INJECT_FILENAME, text)


def read_inject(workdir: Path) -> Optional[str]:
    """Read and *consume* the inject payload (read-then-clear). ``None`` if absent/empty."""
    path = Path(workdir) / CTL_DIRNAME / INJECT_FILENAME
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        path.unlink()
    except OSError:
        pass
    raw = raw.strip()
    return raw or None


# --- HITL: steer (human → run) with a break mode ---------------------------------
# steer.json = {text, mode}. mode "now" interrupts the current step; "next" applies at the next
# turn boundary (after the current step/job finishes). docs/plans/2026-08-13-steer-feedback-cc.md.

def write_steer(workdir: Path, text: str, mode: str = STEER_NOW) -> Path:
    """Stage a steering message + break mode; caller then sends ``command=steer`` (payload first)."""
    if mode not in STEER_MODES:
        mode = STEER_NOW
    path = ctl_dir(workdir) / STEER_FILENAME
    _atomic_write(path, json.dumps({"text": text, "mode": mode}, ensure_ascii=False))
    return path


def read_steer(workdir: Path) -> Optional[dict]:
    """Read and *consume* the steer payload (read-then-clear). Returns ``{"text", "mode"}`` or ``None``
    (absent / empty text / corrupt). An unknown/missing mode normalizes to ``now``."""
    path = Path(workdir) / CTL_DIRNAME / STEER_FILENAME
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        path.unlink()
    except OSError:
        pass
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    text = str(data.get("text", "")).strip()
    if not text:
        return None
    mode = data.get("mode")
    return {"text": text, "mode": mode if mode in STEER_MODES else STEER_NOW}


def send_steer(workdir: Path, text: str, mode: str = STEER_NOW) -> None:
    """Operator convenience: stage the steer payload, then write the ``steer`` command (correct order)."""
    write_steer(workdir, text, mode)
    send_command(workdir, CMD_STEER)


# --- Rate limit: fallback text parser + marker file ------------------------------
# The PRIMARY detector is the SDK's typed RateLimitEvent/RateLimitInfo (read in the driver);
# parse_rate_limit is the FALLBACK for the dead-subprocess path (ProcessError / error text)
# and older CLIs. docs/plans/2026-08-13-rate-limit-resume-cc.md.

_RL_SIGNAL_RE = re.compile(
    r"usage limit|limit will reset|limit resets?|resets?[ _]at|five_hour|seven_day|rate[ _]?limit",
    re.IGNORECASE,
)
_RL_EPOCH_RE = re.compile(r"resets?[ _]?at['\"]?\s*[:=]\s*['\"]?(\d{10,13})", re.IGNORECASE)
_RL_PIPE_EPOCH_RE = re.compile(r"\|(\d{10,13})\b")
_RL_ISO_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)"
)
_RL_TYPE_RE = re.compile(
    r"(seven_day_opus|seven_day_sonnet|seven_day|five_hour|overage)", re.IGNORECASE
)


def _rl_normalize_epoch(digits: str) -> float:
    """13+ digits → milliseconds; 10-12 → seconds."""
    return int(digits) / 1000.0 if len(digits) >= 13 else float(int(digits))


def _rl_iso_to_epoch(s: str) -> Optional[float]:
    from datetime import datetime, timezone

    s = s.strip().replace(" ", "T")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def parse_rate_limit(text: Optional[str]) -> Optional[dict]:
    """FALLBACK usage-limit classifier for error text (dead-subprocess / older-CLI path).

    Returns ``{"resets_at": <epoch|None>, "rate_limit_type": <str|None>}`` if ``text`` carries a
    usage-limit signal, else ``None`` (caller then re-raises the error rather than swallowing it).
    ``resets_at`` is a Unix epoch (seconds); ``None`` means "no time parseable → back off".
    """
    if not text or not _RL_SIGNAL_RE.search(text):
        return None
    resets_at: Optional[float] = None
    m = _RL_EPOCH_RE.search(text) or _RL_PIPE_EPOCH_RE.search(text)
    if m:
        resets_at = _rl_normalize_epoch(m.group(1))
    else:
        iso = _RL_ISO_RE.search(text)
        if iso:
            resets_at = _rl_iso_to_epoch(iso.group(1))
    t = _RL_TYPE_RE.search(text)
    return {"resets_at": resets_at, "rate_limit_type": t.group(1).lower() if t else None}


def write_rate_limit(
    workdir: Path,
    resets_at: Optional[float],
    rate_limit_type: Optional[str],
    detected_at: Optional[str] = None,
) -> Path:
    """Publish the durable "paused until reset" marker (observable by UI/supervisor; auto-resume seam)."""
    if detected_at is None:
        from datetime import datetime

        detected_at = datetime.now().isoformat(timespec="seconds")
    payload = {
        "rate_limit_type": rate_limit_type,
        "resets_at": resets_at,
        "detected_at": detected_at,
    }
    path = ctl_dir(workdir) / RATE_LIMIT_FILENAME
    _atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2))
    return path


def read_rate_limit(workdir: Path) -> Optional[dict]:
    """Return the rate-limit marker dict, or ``None`` if absent/corrupt."""
    path = Path(workdir) / CTL_DIRNAME / RATE_LIMIT_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def clear_rate_limit(workdir: Path) -> None:
    """Remove the rate-limit marker (best-effort; on resume)."""
    try:
        (Path(workdir) / CTL_DIRNAME / RATE_LIMIT_FILENAME).unlink()
    except OSError:
        pass
