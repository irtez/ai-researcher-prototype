"""Process-global per-session compute_mode registry.

SC3 (multi-user concurrent UI, `docs/plans/2026-06-30-multiuser-concurrent-ui.md`):
`compute_mode` (Auto/Local/GPU) hard-gates which solver tools a run may use.
The CodeArkt server holds one shared `CodeActAgent` instance per agent, reused
across concurrent requests carrying different `session_id`s, so the mode is
threaded through a per-session registry (keyed by `session_id`) rather than
mutated on the shared agent instance itself (which would race across
concurrent runs). Kept dependency-free so `codeact.py` can import it without
pulling in the rest of the server/holosophos stack.
"""

from typing import Dict, Optional, Tuple

VALID_COMPUTE_MODES: Tuple[str, str, str] = ("Auto", "Local", "GPU")
DEFAULT_COMPUTE_MODE: str = "Auto"

_compute_mode_by_session: Dict[str, str] = {}


def normalize_compute_mode(value: Optional[str]) -> str:
    """Case-insensitively map `value` to one of VALID_COMPUTE_MODES.

    Anything unknown or `None` falls back to DEFAULT_COMPUTE_MODE.
    """
    if value is None:
        return DEFAULT_COMPUTE_MODE
    lowered = value.strip().lower()
    for mode in VALID_COMPUTE_MODES:
        if mode.lower() == lowered:
            return mode
    return DEFAULT_COMPUTE_MODE


def set_compute_mode(session_id: str, value: Optional[str]) -> None:
    """Store the normalized compute mode for `session_id`."""
    _compute_mode_by_session[session_id] = normalize_compute_mode(value)


def get_compute_mode(session_id: str) -> str:
    """Return the stored compute mode for `session_id`, or the default."""
    return _compute_mode_by_session.get(session_id, DEFAULT_COMPUTE_MODE)


def clear_compute_mode(session_id: str) -> None:
    """Remove the stored compute mode for `session_id`, if any."""
    _compute_mode_by_session.pop(session_id, None)


# --- Per-session model_name registry --------------------------------------------
# Same rationale as compute_mode: the CodeArkt server holds one shared LLM
# object per agent, reused across concurrent requests carrying different
# session_ids, so a per-session model override is threaded through this
# registry rather than mutated on the shared LLM instance itself. Unlike
# compute_mode there is no fixed valid set to normalize against here (the
# curated choices live in the UI config) and no default constant: `None`
# means "no override, use the LLM's own configured model_name".

_model_name_by_session: Dict[str, str] = {}
_model_effort_by_session: Dict[str, str] = {}
_model_config_by_session: Dict[str, dict] = {}


def set_model_config(session_id: str, value: dict) -> None:
    _model_config_by_session[session_id] = value


def get_model_config(session_id: str) -> Optional[dict]:
    return _model_config_by_session.get(session_id)


def set_model_effort(session_id: str, value: str) -> None:
    _model_effort_by_session[session_id] = value


def get_model_effort(session_id: str) -> Optional[str]:
    return _model_effort_by_session.get(session_id)


def set_model_name(session_id: str, value: str) -> None:
    """Store the model_name override for `session_id`."""
    _model_name_by_session[session_id] = value


def get_model_name(session_id: str) -> Optional[str]:
    """Return the stored model_name override for `session_id`, or None if unset."""
    return _model_name_by_session.get(session_id)


def clear_model_name(session_id: str) -> None:
    """Remove the stored model_name override for `session_id`, if any."""
    _model_name_by_session.pop(session_id, None)
    _model_effort_by_session.pop(session_id, None)
    _model_config_by_session.pop(session_id, None)


# --- Per-session owner registry (auth / team deploy) ---------------------------
# The authenticated login (delivered as the `X-Holo-Client` header, which the
# auth'd nginx overwrites with `$remote_user`) is registered per session so the
# Langfuse tracer can stamp it as the trace `user`. Same rationale as the
# compute_mode registry: one shared agent instance serves concurrent sessions,
# so the value is keyed by `session_id`, never stored on the agent.

_owner_by_session: Dict[str, str] = {}


def set_owner(session_id: str, owner: Optional[str]) -> None:
    """Register the authenticated owner (login) for `session_id`.

    A blank/`None` owner is ignored: nested delegate calls (manager -> solver,
    same `session_id`) carry no header, and must NOT wipe an already-registered
    owner -- mirrors `set_compute_mode`'s "explicit value only" contract.
    """
    if owner:
        _owner_by_session[session_id] = owner


def get_owner(session_id: str) -> Optional[str]:
    """Return the registered owner (login) for `session_id`, or None if unset."""
    return _owner_by_session.get(session_id)


def clear_owner(session_id: str) -> None:
    """Remove the registered owner for `session_id`, if any."""
    _owner_by_session.pop(session_id, None)
