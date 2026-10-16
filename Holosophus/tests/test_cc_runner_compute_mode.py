"""SC5 (ui-claude-code-orchestrator): compute_mode agent filter on the CC path.

``cc_research_lib.filter_agents_by_compute_mode`` is the per-run hard ceiling on
which mle-solver variant a Claude-Code run may use — the same meaning
``compute_mode`` has on the CodeArkt path:

- ``Local`` -> drop ``mle-solver-remote``;
- ``GPU``   -> drop ``mle-solver``;
- ``Auto`` (and unset/other) -> keep both;
- when ``ensure_synced()``'s ``MLE_SOLVER_IS_REMOTE`` env filter did not render
  the variant the mode requires, the run fails loud
  (``ComputeModeUnavailableError``) instead of silently downgrading to the other
  solver.

Driven with plain dicts (the filter is generic over the agents mapping), so the
test needs neither the SDK nor a rendered ``.claude/agents/``.
"""

import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

from cc_research_lib import (  # noqa: E402
    MLE_SOLVER_LOCAL_AGENT,
    MLE_SOLVER_REMOTE_AGENT,
    ComputeModeUnavailableError,
    filter_agents_by_compute_mode,
)

# Sentinel agent definitions — the filter only cares about keys, so any values do.
LIBRARIAN = object()
LOCAL_DEF = object()
REMOTE_DEF = object()


def _both() -> dict:
    """Agents dict as ensure_synced() renders it with MLE_SOLVER_IS_REMOTE unset."""
    return {
        "librarian": LIBRARIAN,
        MLE_SOLVER_LOCAL_AGENT: LOCAL_DEF,
        MLE_SOLVER_REMOTE_AGENT: REMOTE_DEF,
    }


def _local_only() -> dict:
    """As rendered with MLE_SOLVER_IS_REMOTE=false (light session)."""
    return {"librarian": LIBRARIAN, MLE_SOLVER_LOCAL_AGENT: LOCAL_DEF}


def _remote_only() -> dict:
    """As rendered with MLE_SOLVER_IS_REMOTE=true (heavy session)."""
    return {"librarian": LIBRARIAN, MLE_SOLVER_REMOTE_AGENT: REMOTE_DEF}


# ── Local drops the remote solver, keeps the local one ──


def test_local_drops_remote_keeps_local():
    filtered = filter_agents_by_compute_mode(_both(), "Local")
    assert MLE_SOLVER_REMOTE_AGENT not in filtered
    assert MLE_SOLVER_LOCAL_AGENT in filtered
    assert "librarian" in filtered  # unrelated agents untouched


# ── GPU drops the local solver, keeps the remote one ──


def test_gpu_drops_local_keeps_remote():
    filtered = filter_agents_by_compute_mode(_both(), "GPU")
    assert MLE_SOLVER_LOCAL_AGENT not in filtered
    assert MLE_SOLVER_REMOTE_AGENT in filtered
    assert "librarian" in filtered


# ── Auto keeps both variants ──


def test_auto_keeps_both():
    filtered = filter_agents_by_compute_mode(_both(), "Auto")
    assert MLE_SOLVER_LOCAL_AGENT in filtered
    assert MLE_SOLVER_REMOTE_AGENT in filtered
    assert "librarian" in filtered


@pytest.mark.parametrize("mode", [None, "", "   ", "unknown"])
def test_unset_or_unknown_keeps_both(mode):
    """Defensive: no explicit/unrecognized mode must not filter (parity with Auto)."""
    filtered = filter_agents_by_compute_mode(_both(), mode)
    assert MLE_SOLVER_LOCAL_AGENT in filtered
    assert MLE_SOLVER_REMOTE_AGENT in filtered


# ── Missing required variant -> explicit error, not a silent downgrade ──


def test_local_missing_local_variant_raises():
    """Local requested but ensure_synced rendered remote-only -> explicit error."""
    with pytest.raises(ComputeModeUnavailableError) as exc:
        filter_agents_by_compute_mode(_remote_only(), "Local")
    # The message names the missing variant and points at the env filter.
    assert MLE_SOLVER_LOCAL_AGENT in str(exc.value)
    assert "MLE_SOLVER_IS_REMOTE" in str(exc.value)


def test_gpu_missing_remote_variant_raises():
    """GPU requested but ensure_synced rendered local-only -> explicit error."""
    with pytest.raises(ComputeModeUnavailableError) as exc:
        filter_agents_by_compute_mode(_local_only(), "GPU")
    assert MLE_SOLVER_REMOTE_AGENT in str(exc.value)


def test_error_message_refuses_silent_substitution():
    """SC5 intent: the failure explicitly refuses to run with a different solver."""
    with pytest.raises(ComputeModeUnavailableError) as exc:
        filter_agents_by_compute_mode(_remote_only(), "Local")
    assert "silently" in str(exc.value).lower()


# ── A mode whose variant IS present succeeds even when the other is absent ──


def test_local_ok_when_only_local_rendered():
    filtered = filter_agents_by_compute_mode(_local_only(), "Local")
    assert MLE_SOLVER_LOCAL_AGENT in filtered
    assert MLE_SOLVER_REMOTE_AGENT not in filtered


def test_gpu_ok_when_only_remote_rendered():
    filtered = filter_agents_by_compute_mode(_remote_only(), "GPU")
    assert MLE_SOLVER_REMOTE_AGENT in filtered
    assert MLE_SOLVER_LOCAL_AGENT not in filtered


# ── Auto tolerates a single rendered variant (env filter is intentional) ──


def test_auto_tolerates_single_rendered_variant():
    filtered = filter_agents_by_compute_mode(_remote_only(), "Auto")
    assert MLE_SOLVER_REMOTE_AGENT in filtered
    assert MLE_SOLVER_LOCAL_AGENT not in filtered  # cannot conjure a missing one


# ── Purity: the input dict is never mutated ──


def test_does_not_mutate_input():
    agents = _both()
    before = dict(agents)
    filter_agents_by_compute_mode(agents, "Local")
    assert agents == before
