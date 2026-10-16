"""ui-claude-code-orchestrator: the CC runner honours the per-run model_name.

Regression for the acceptance finding that ``cc_runner`` parsed the request's
``model_name`` and then silently dropped it (the field had zero effect). The
runner now resolves the SDK model from the request: a non-empty ``model_name`` is
honoured verbatim, otherwise the CC path default (``CC_DEFAULT_MODEL``) is used —
so the per-run model selector actually takes effect instead of being discarded.

``_resolve_model`` is a pure helper, so this needs neither the SDK nor a live run.
"""

import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

from cc_runner import CC_DEFAULT_MODEL, _resolve_model  # noqa: E402


def test_empty_or_missing_model_name_uses_cc_default() -> None:
    assert _resolve_model(None) == CC_DEFAULT_MODEL
    assert _resolve_model("") == CC_DEFAULT_MODEL
    assert _resolve_model("   ") == CC_DEFAULT_MODEL


def test_non_empty_model_name_is_honoured_verbatim() -> None:
    # A per-run override reaches the SDK instead of being silently dropped.
    assert _resolve_model("claude-sonnet-4-5") == "claude-sonnet-4-5"
    # Whitespace is trimmed but the value is otherwise passed through as-is.
    assert _resolve_model("  claude-opus-4-7  ") == "claude-opus-4-7"


def test_cc_default_matches_the_model_offered_by_ui_api() -> None:
    from holosophos.ui_api import build_ui_config

    config = build_ui_config()
    assert _resolve_model(None) == config["claude_code_default_model"]
    assert _resolve_model(None) in config["claude_code_model_choices"]
