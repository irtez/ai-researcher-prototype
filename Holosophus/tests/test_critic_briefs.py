"""SC4: every critic brief is runner-authored, blind where it must be, and scoped.

Design: docs/plans/2026-08-18-self-improving-loop-verification.md.

Three ways the design fails quietly, and one test each:
  1. the audited party writes the brief,
  2. the brief hands the blind critic the answer it was asked to derive,
  3. the read scope still contains the file that answer lives in.

No SDK and no model call: the guards are all in the builders.
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

import cc_critic_runner as ccr  # noqa: E402

TASK = "Does intervening on a stalled LLM agent beat letting it continue?"


@pytest.fixture
def session(tmp_path):
    (tmp_path / "mle" / "results").mkdir(parents=True)
    (tmp_path / "mle" / "results" / "arm_summary.json").write_text("{}", encoding="utf-8")
    (tmp_path / "paper.tex").write_text("best arm accuracy 0.559", encoding="utf-8")
    return tmp_path


@pytest.fixture
def claims(session):
    return [
        ccr.Claim(
            claim_id="abstract.best_arm_acc",
            claimed="0.559",
            owner="writer",
            sources=(str(session / "mle" / "results" / "arm_summary.json"),),
            description="best-arm accuracy reported in the abstract",
        )
    ]


# ── 1. the audited party must not author a brief ─────────────────────────────


def test_run_critic_rejects_anything_but_a_runner_built_brief():
    for impostor in ["derive the numbers please", {"critic": "analysis", "text": "..."}, None]:
        with pytest.raises(ccr.BriefError, match="never from the run being audited"):
            asyncio.run(ccr.run_critic(impostor))


def test_a_brief_cannot_claim_another_author():
    with pytest.raises(ccr.BriefError, match="not the runner"):
        ccr.CriticBrief(
            critic="analysis", iteration=4, text="x", read_scope="/tmp", authored_by="manager"
        )


def test_builders_stamp_runner_authorship(session, claims):
    brief = ccr.build_analysis_brief(claims, iteration=4, session_dir=str(session))
    assert brief.authored_by == "runner"
    assert brief.claim_ids == ("abstract.best_arm_acc",)


# ── 2. the blind critic must not be shown the value ──────────────────────────


def test_analysis_brief_withholds_the_claimed_value(session, claims):
    brief = ccr.build_analysis_brief(claims, iteration=4, session_dir=str(session))
    assert "0.559" not in brief.text
    assert "abstract.best_arm_acc" in brief.text
    assert "best-arm accuracy reported in the abstract" in brief.text


def test_a_value_leaking_through_the_description_is_caught(session):
    """The realistic leak: an extractor that pastes the sentence, value and all."""
    leaky = [
        ccr.Claim(
            claim_id="abstract.best_arm_acc",
            claimed="0.559",
            sources=(str(session / "mle" / "results" / "arm_summary.json"),),
            description="the abstract reports best-arm accuracy of 0.559",
        )
    ]
    with pytest.raises(ccr.BriefError, match="leaked into the analysis brief"):
        ccr.build_analysis_brief(leaky, iteration=4, session_dir=str(session))


def test_the_guard_does_not_fire_on_a_coincidental_substring(session):
    """0.55 inside 0.559 is not the claimed value; a guard that cries wolf gets disabled."""
    ok = [
        ccr.Claim(
            claim_id="abstract.best_arm_acc",
            claimed="0.55",
            sources=(str(session / "mle" / "results" / "arm_summary.json"),),
            description="accuracy, distinct from the 0.559 figure elsewhere",
        )
    ]
    brief = ccr.build_analysis_brief(ok, iteration=4, session_dir=str(session))
    assert "0.559" in brief.text


def test_redaction_also_drops_the_source_path():
    """Naming the file a value lives in is a strong hint toward the value."""
    claim = ccr.Claim(
        claim_id="c1", claimed="1.0", sources=("mle/results/x.json",), description="d"
    )
    assert claim.redacted() == {"claim_id": "c1", "description": "d"}


# ── 3. the read scope must exclude the sources ───────────────────────────────


def test_analysis_brief_excludes_every_claim_source(session, claims):
    brief = ccr.build_analysis_brief(claims, iteration=4, session_dir=str(session))
    assert brief.excluded == (str(session / "mle" / "results" / "arm_summary.json"),)
    assert "Out of scope" in brief.text


def test_a_source_left_in_scope_is_caught(session, claims):
    with pytest.raises(ccr.BriefError, match="still readable"):
        ccr._assert_sources_excluded(claims, str(session), [])


def test_a_valued_claim_with_no_source_is_caught(session):
    """An extractor that found a number but not its citation excludes nothing."""
    sourceless = [ccr.Claim(claim_id="abstract.acc", claimed="0.559", description="accuracy")]
    with pytest.raises(ccr.BriefError, match="no recorded source"):
        ccr.build_analysis_brief(sourceless, iteration=4, session_dir=str(session))


def test_a_claim_with_no_value_needs_no_source(session):
    """Nothing to withhold means nothing to exclude — the guard must not overreach."""
    unvalued = [ccr.Claim(claim_id="abstract.acc", description="accuracy, value not yet extracted")]
    brief = ccr.build_analysis_brief(unvalued, iteration=4, session_dir=str(session))
    assert brief.excluded == ()


def test_an_exclusion_outside_the_read_scope_is_caught(session, claims):
    with pytest.raises(ccr.BriefError, match="not inside the read scope"):
        ccr._assert_sources_excluded(
            claims, str(session), [str(Path(session).parent / "else.json")]
        )


def test_relative_sources_resolve_against_the_read_scope_not_the_cwd(session):
    """A critic reports paths as it saw them: relative to its own scope.

    Resolving those against the process cwd puts every one of them outside the
    session, so the exclusion guard fires on correct input. Caught by the first
    dry run of the F3 replay.
    """
    relative = [
        ccr.Claim(
            claim_id="abstract.acc",
            claimed="0.559",
            sources=("mle/results/arm_summary.json",),
            description="accuracy in the abstract",
        )
    ]
    brief = ccr.build_analysis_brief(relative, iteration=3, session_dir=str(session))
    assert brief.excluded == (str(session / "mle" / "results" / "arm_summary.json"),)
    assert "0.559" not in brief.text


def test_a_relative_source_escaping_the_scope_is_still_caught(session):
    """Resolving against the scope must not become a way out of it."""
    escaping = [
        ccr.Claim(
            claim_id="abstract.acc",
            claimed="0.559",
            sources=("../outside.json",),
            description="accuracy",
        )
    ]
    with pytest.raises(ccr.BriefError, match="not inside the read scope"):
        ccr.build_analysis_brief(escaping, iteration=3, session_dir=str(session))


# ── reusing the paper critic's enumeration for the blind critic ──────────────


PAPER_REPORT = {
    "critic": "paper",
    "iteration": 3,
    "coverage": {"claims_in": 2, "derived": 2, "unverified": 0},
    "claims": [
        {
            "claim_id": "abstract.best_arm_acc",
            "claimed": "0.559",
            "cited_sources": ["mle/results/iter3_gpt4o_indomain.json"],
            "description": "best-arm accuracy for the in-domain gpt-4o refit",
        },
        {
            "claim_id": "sec:corpus.triples",
            "claimed": "2994",
            "cited_sources": ["mle/results/causal_summary.md"],
            "description": "number of with-arms triples in the corpus",
        },
    ],
    "findings": [],
}


def test_enumeration_carries_over_with_values_intact():
    claims = ccr.claims_from_paper_report(PAPER_REPORT)
    assert [c.claim_id for c in claims] == ["abstract.best_arm_acc", "sec:corpus.triples"]
    assert claims[0].claimed == "0.559"
    assert claims[0].sources == ("mle/results/iter3_gpt4o_indomain.json",)


def test_a_leaked_value_in_the_description_is_scrubbed():
    """The paper critic is told not to put values in descriptions. Don't trust it."""
    leaky = {
        **PAPER_REPORT,
        "claims": [
            {
                "claim_id": "abstract.best_arm_acc",
                "claimed": "0.559",
                "cited_sources": ["r.json"],
                "description": "best-arm accuracy, reported as 0.559 in the abstract",
            }
        ],
    }
    claims = ccr.claims_from_paper_report(leaky)
    assert "0.559" not in claims[0].description
    assert (
        claims[0].claimed == "0.559"
    ), "the runner still needs the value; only the critic is blinded"


def test_scrubbed_enumeration_survives_the_brief_guard(session):
    """End to end: enumeration -> redaction -> a brief the blind critic may see."""
    report = {
        **PAPER_REPORT,
        "claims": [
            {
                "claim_id": "abstract.best_arm_acc",
                "claimed": "0.559",
                "cited_sources": [str(session / "mle" / "results" / "arm_summary.json")],
                "description": "best-arm accuracy of 0.559 for the refit",
            }
        ],
    }
    claims = ccr.claims_from_paper_report(report)
    brief = ccr.build_analysis_brief(claims, iteration=3, session_dir=str(session))
    assert "0.559" not in brief.text
    assert "abstract.best_arm_acc" in brief.text


def test_a_claim_without_an_id_is_dropped():
    bad = {**PAPER_REPORT, "claims": [{"claimed": "1.0", "description": "x"}]}
    assert ccr.claims_from_paper_report(bad) == []


def test_enumeration_survives_a_json_string_report():
    assert len(ccr.claims_from_paper_report(json.dumps(PAPER_REPORT))) == 2


def test_a_report_with_no_enumeration_yields_nothing():
    assert ccr.claims_from_paper_report({"critic": "paper", "iteration": 3}) == []


# ── the sighted critics get what they need ───────────────────────────────────


def test_an_empty_claim_list_asks_the_critic_to_enumerate(session):
    """Empty must mean "build the list", not "there is nothing to check".

    The first live F3 replay handed the matching form an empty list and got back
    a perfectly well-formed report with claims_in=0 and no findings. The critic
    answered the question it was actually asked.
    """
    brief = ccr.build_paper_brief(
        [], iteration=3, session_dir=str(session), paper_path=str(session / "paper.tex")
    )
    assert "Enumerate every quantity" in brief.text
    assert "No claim list is supplied" in brief.text
    assert "Claims:\n[]" not in brief.text


def test_enumeration_mode_warns_against_stating_values_in_descriptions(session):
    """The enumeration feeds a blind critic, so it must not leak values."""
    brief = ccr.build_paper_brief(
        [], iteration=3, session_dir=str(session), paper_path=str(session / "paper.tex")
    )
    assert "without being shown your values" in brief.text
    assert "without stating" in brief.text


def test_paper_brief_shows_claim_and_source_together(session, claims):
    """Withholding applies to derivation, not matching — comparing is this critic's task."""
    brief = ccr.build_paper_brief(
        claims, iteration=4, session_dir=str(session), paper_path=str(session / "paper.tex")
    )
    assert "0.559" in brief.text
    assert "arm_summary.json" in brief.text
    assert brief.excluded == ()


def test_sufficiency_brief_carries_the_task_verbatim(session, claims):
    brief = ccr.build_sufficiency_brief(claims, iteration=4, session_dir=str(session), task=TASK)
    assert TASK in brief.text


def test_planner_brief_enumerates_the_only_valid_trace_targets(session):
    verdict = {
        "iteration": 4,
        "verdict": "block",
        "findings": [{"id": "F1", "class": "mismatch"}, {"id": "F2", "class": "unverified"}],
    }
    brief = ccr.build_planner_brief(
        verdict, task=TASK, session_dir=str(session), caps={"gpu_hours": 8}
    )
    assert brief.iteration == 5
    assert "F1, F2" in brief.text
    assert "gpu_hours" in brief.text


def test_the_sufficiency_brief_asks_for_an_enumeration_when_it_supplies_none(session):
    """Coverage by construction. Two models given the free-form brief both audited
    the run's headline condition and neither turned to the other one, where the
    defect was. The critic now has to list what it is judging before judging it,
    so a unit it never looked at is visible rather than absent."""
    brief = ccr.build_sufficiency_brief([], iteration=4, session_dir=str(session), task=TASK)
    assert "units" in brief.text
    assert "coverage.claims_in" in brief.text, "the enumeration has to be accounted for"
    assert "n/a" in brief.text, "the placeholder id has to be ruled out explicitly"
    assert "Judge exactly these units" not in brief.text


def test_the_sufficiency_brief_pins_the_units_when_it_has_them(session):
    units = [ccr.Claim(claim_id="cell.a.delta", description="the delta on cell a")]
    brief = ccr.build_sufficiency_brief(units, iteration=4, session_dir=str(session), task=TASK)
    assert "Judge exactly these units" in brief.text
    assert "cell.a.delta" in brief.text
    assert "No unit list is supplied" not in brief.text


def test_a_supplied_unit_list_carries_no_values(session):
    """A unit is a thing to judge, not a number to check -- this critic does not do
    arithmetic. Passing the value through would leak it for no purpose."""
    units = [ccr.Claim(claim_id="cell.a.delta", claimed="0.559", description="the delta")]
    brief = ccr.build_sufficiency_brief(units, iteration=4, session_dir=str(session), task=TASK)
    assert "0.559" not in brief.text


def test_the_enumeration_can_be_reused_as_the_next_critics_unit_list():
    report = {
        "critic": "sufficiency",
        "units": [
            {"unit_id": "grad.a.delta", "description": "condition a", "judged": "sufficient"},
            {"unit_id": "grad.b.delta", "description": "condition b", "judged": "insufficient"},
            {"unit_id": "grad.a.delta", "description": "a duplicate", "judged": "sufficient"},
            {"description": "no id at all"},
        ],
    }
    units = ccr.units_from_sufficiency_report(report)
    assert [u.claim_id for u in units] == ["grad.a.delta", "grad.b.delta"]
    assert units[1].description == "condition b"


def test_reusing_an_enumeration_survives_a_report_that_has_none():
    assert ccr.units_from_sufficiency_report({"critic": "sufficiency"}) == []
    assert ccr.units_from_sufficiency_report("not json at all") == []


# ── CLI resolution ───────────────────────────────────────────────────────────


def test_resolve_cli_prefers_the_real_binary_over_a_batch_shim(tmp_path, monkeypatch):
    """A .CMD shim mangles multi-line arguments, and every critic prompt is multi-line.

    Through the shim the system prompt never arrives intact and the SDK waits out
    its whole 60s handshake before reporting a timeout — which reads as a broken
    critic rather than a broken launcher. Measured: 70.2s fail via claude.CMD,
    3.6s pass via claude.exe, same prompt.
    """
    shim = tmp_path / "claude.CMD"
    shim.write_text("@echo off\n", encoding="utf-8")
    real = tmp_path / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
    real.parent.mkdir(parents=True)
    real.write_bytes(b"MZ")

    monkeypatch.setattr(ccr.shutil, "which", lambda _: str(shim))
    monkeypatch.delenv("CLAUDE_CLI_PATH", raising=False)
    assert ccr.resolve_cli() == str(real)


def test_resolve_cli_keeps_the_shim_when_no_real_binary_is_beside_it(tmp_path, monkeypatch):
    shim = tmp_path / "claude.CMD"
    shim.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setattr(ccr.shutil, "which", lambda _: str(shim))
    monkeypatch.delenv("CLAUDE_CLI_PATH", raising=False)
    assert ccr.resolve_cli() == str(shim)


def test_resolve_cli_leaves_a_plain_executable_alone(tmp_path, monkeypatch):
    """On Linux `which` returns the real thing already; do not go hunting."""
    binary = tmp_path / "claude"
    binary.write_bytes(b"#!/bin/sh\n")
    monkeypatch.setattr(ccr.shutil, "which", lambda _: str(binary))
    monkeypatch.delenv("CLAUDE_CLI_PATH", raising=False)
    assert ccr.resolve_cli() == str(binary)


def test_explicit_env_override_wins(monkeypatch):
    monkeypatch.setenv("CLAUDE_CLI_PATH", "/somewhere/else/claude")
    assert ccr.resolve_cli() == "/somewhere/else/claude"


# ── prompt loading ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("stem", ccr.RUNNER_OWNED)
def test_every_runner_owned_prompt_composes(stem):
    spec = ccr.load_runner_prompt(stem)
    assert spec["prompt"].strip()
    assert spec["tools"], f"{stem} has no tool allowlist"
    assert (
        "Write" not in spec["tools"] and "Edit" not in spec["tools"]
    ), "a critic must not modify what it audits"


def test_only_runner_owned_prompts_load():
    with pytest.raises(ccr.BriefError, match="not a runner-owned prompt"):
        ccr.load_runner_prompt("writer")


def test_critic_names_map_to_prompt_stems():
    import cc_critic_lib as ccl

    assert set(ccr.CRITIC_PROMPTS) == set(ccl.CRITICS)
    assert set(ccr.CRITIC_PROMPTS.values()) <= set(ccr.RUNNER_OWNED)


# ── report extraction ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw",
    [
        '{"critic": "analysis", "iteration": 4}',
        '```json\n{"critic": "analysis", "iteration": 4}\n```',
        'Here is the report:\n{"critic": "analysis", "iteration": 4}',
    ],
)
def test_extract_json_survives_the_usual_slips(raw):
    assert ccr.extract_json(raw) == {"critic": "analysis", "iteration": 4}


def test_unparseable_output_is_passed_through_for_the_comparator_to_reject():
    """Not this layer's call: a garbled report is a contract error, and blocks."""
    import cc_critic_lib as ccl

    out = ccl.compare([ccr.extract_json("the model apologised instead")], iteration=4)
    assert out["verdict"] == "block"
    assert out["contract_errors"]


def test_a_brief_round_trips_as_json(session, claims):
    """The brief is what a human reviews when a verdict looks wrong."""
    brief = ccr.build_analysis_brief(claims, iteration=4, session_dir=str(session))
    block = brief.text.split("Quantities to derive:\n", 1)[1]
    assert json.loads(block) == [
        {
            "claim_id": "abstract.best_arm_acc",
            "description": "best-arm accuracy reported in the abstract",
        }
    ]
