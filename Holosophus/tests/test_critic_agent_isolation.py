"""SC9: the critics and the planner never reach the manager's agent set.

Design: docs/plans/2026-08-18-self-improving-loop-verification.md (G6).

The property under test is the one the design cannot lose: the audited party must
not be able to invoke the critics that audit it, or write their briefs. That is
enforced by keeping the four prompts out of `.claude/agents/` entirely, so they
never enter `options.agents`.

Since `.claude/` is gitignored, the orphan scan in sync_claude_agents.py is a
local-dev sensor. These tests are the durable guard.
"""

import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

import run_research  # noqa: E402
import sync_claude_agents as sync  # noqa: E402

RUNNER_OWNED = ("analysis_critic", "paper_critic", "sufficiency", "planner")


def test_runner_owned_prompts_exist():
    """Guards the inverse failure: a SKIP entry naming a prompt that isn't there."""
    for stem in RUNNER_OWNED:
        assert (sync.PROMPTS_DIR / f"{stem}.yaml").is_file(), f"{stem}.yaml is missing"


def test_runner_owned_prompts_are_skipped():
    for stem in RUNNER_OWNED:
        assert f"{stem}.yaml" in sync.SKIP


def test_sync_never_emits_a_runner_owned_prompt():
    emitted = {p.stem for p in sync.iter_prompt_yamls()}
    assert emitted.isdisjoint(RUNNER_OWNED)
    assert {p.stem for p in sync.all_prompt_yamls()}.isdisjoint(RUNNER_OWNED)


def test_load_all_agents_excludes_critics_and_planner(tmp_path, monkeypatch):
    """The manager's actual agent set, along the real sync -> load path.

    Rendered into a tmp dir rather than `.claude/agents/`: importing run_research
    runs load_dotenv, and this repo's .env pins MLE_SOLVER_IS_REMOTE, so calling
    ensure_synced() here would delete a solver variant out of the developer's
    live harness as a side effect of running the tests.
    """
    monkeypatch.setattr(sync, "AGENTS_DIR", tmp_path)
    monkeypatch.setattr(run_research, "AGENTS_DIR", tmp_path)
    sync.ensure_synced()

    agents = run_research.load_all_agents()
    assert agents, "nothing was rendered - the assertion below would be vacuous"
    for stem in RUNNER_OWNED:
        assert stem not in agents
        assert stem.replace("_", "-") not in agents
    assert not list(tmp_path.glob("*critic*")) and not list(tmp_path.glob("planner*"))


def test_no_orphan_md_in_the_real_agents_dir():
    assert sync.check_orphans() == []


# ── the scan itself ──────────────────────────────────────────────────────────


@pytest.fixture
def fake_agents_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(sync, "AGENTS_DIR", tmp_path)
    return tmp_path


def test_orphan_scan_catches_a_hand_placed_critic(fake_agents_dir):
    """The leak this exists for: someone drops a critic .md in by hand.

    `--check` alone cannot see it — find_drift() filters SKIP before comparing —
    and load_all_agents() would then hand it to the manager.
    """
    (fake_agents_dir / "analysis_critic.md").write_text(
        "---\nname: analysis_critic\n---\n", encoding="utf-8"
    )
    orphans = sync.check_orphans()
    assert [p.name for p, _ in orphans] == ["analysis_critic.md"]
    assert "runner-owned" in orphans[0][1]


def test_orphan_scan_catches_an_md_with_no_yaml_at_all(fake_agents_dir):
    (fake_agents_dir / "invented.md").write_text("---\nname: invented\n---\n", encoding="utf-8")
    orphans = sync.check_orphans()
    assert [p.name for p, _ in orphans] == ["invented.md"]
    assert "no prompt YAML" in orphans[0][1]


def test_orphan_scan_ignores_dev_harness_agents(fake_agents_dir):
    """dev-* is committed hand-written harness, and iter_research_agent_paths drops it."""
    (fake_agents_dir / "dev-spec-reviewer.md").write_text(
        "---\nname: dev-spec-reviewer\n---\n", encoding="utf-8"
    )
    assert sync.check_orphans() == []


def test_orphan_scan_accepts_generated_agents(fake_agents_dir):
    for stem in ("librarian", "mle-solver", "writer"):
        (fake_agents_dir / f"{stem}.md").write_text(f"---\nname: {stem}\n---\n", encoding="utf-8")
    assert sync.check_orphans() == []


def test_scan_filter_matches_the_runner_filter(fake_agents_dir, monkeypatch):
    """The scan duplicates iter_research_agent_paths()' rule; assert they agree.

    check_orphans() cannot import run_research (that would run load_dotenv and
    mutate the environment from a hygiene check), so the two filters are separate
    code. This is what stops them drifting apart.
    """
    monkeypatch.setattr(run_research, "AGENTS_DIR", fake_agents_dir)
    planted = ["librarian", "dev-spec-reviewer", "analysis_critic", "invented", "mle-solver"]
    for stem in planted:
        (fake_agents_dir / f"{stem}.md").write_text(f"---\nname: {stem}\n---\n", encoding="utf-8")

    manager_sees = {p.stem for p in run_research.iter_research_agent_paths()}
    scanned = {p.stem for p in fake_agents_dir.glob("*.md") if not p.stem.startswith("dev-")}
    assert manager_sees == scanned

    # Everything the manager sees is either generated from a YAML or an orphan;
    # nothing may fall through the gap between the two.
    generated = {sync.NAME_OVERRIDES.get(p.stem, p.stem) for p in sync.all_prompt_yamls()}
    flagged = {p.stem for p, _ in sync.check_orphans()}
    assert manager_sees <= generated | flagged
    assert flagged == {"analysis_critic", "invented"}
