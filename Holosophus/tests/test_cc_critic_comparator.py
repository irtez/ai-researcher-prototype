"""Comparator invariants and the V3 status replay (SC6 + the Tests-table error row).

Design: docs/plans/2026-08-18-self-improving-loop-verification.md.

Pure unit tests - the comparator is non-agent code, so none of this needs a model
call, a run tree or a network. The V3 fixture lives in
holosophos/evals_and_reports/data/critics/ and is replayed against its own
constructed successor state, which is the only input exercising all three
reconciliation branches.
"""

import json
import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

import cc_critic_lib as ccl  # noqa: E402

FIXTURES = PROJECT_DIR / "holosophos" / "evals_and_reports" / "data" / "critics"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _clean_report(critic: str = "analysis", **over) -> dict:
    """A structurally valid report with complete coverage and no defects."""
    report = {
        "critic": critic,
        "iteration": 4,
        "claim_cap": None,
        "coverage": {"claims_in": 10, "derived": 10, "unverified": 0},
        "findings": [],
        "rederivations": [],
    }
    report.update(over)
    return report


# ── SC6: V3 replayed against its successor state ─────────────────────────────


@pytest.fixture(scope="module")
def v3_replay() -> dict:
    previous = _load("v3_verdict.json")
    successor = _load("v3_successor.json")
    return ccl.compare(
        successor["reports"],
        iteration=successor["iteration"],
        previous=previous,
        expected_critics=successor["expected_critics"],
    )


def _by_lineage(verdict: dict) -> dict:
    """Index findings by the V3 id they continue.

    A recurring finding arrives under the id *this* iteration's critic gave it,
    so tracking a defect across iterations goes through ``origin_id``.
    """
    index = {}
    for finding in verdict["findings"]:
        index[finding.get("origin_id") or finding["id"]] = finding
    return index


def test_sc6_reproduces_every_ground_truth_outcome(v3_replay):
    outcomes = _load("v3_outcomes.json")
    by_id = _by_lineage(v3_replay)
    for row in outcomes["outcomes"]:
        finding = by_id.get(row["finding_id"])
        assert finding is not None, f"{row['finding_id']} vanished from the verdict"
        assert finding["status"] == row["expect_status"], (
            f"{row['finding_id']}: outcome {row['outcome']!r} should map to "
            f"{row['expect_status']!r}, got {finding['status']!r} ({row['evidence']})"
        )


def test_sc6_exercises_all_three_branches(v3_replay):
    """The point of the fixture: a replay hitting only two branches proves less."""
    v3_ids = {f["id"] for f in _load("v3_verdict.json")["findings"]}
    carried = {f["status"] for k, f in _by_lineage(v3_replay).items() if k in v3_ids}
    assert carried == {"resolved", "recurring", "unconfirmed-resolved"}


def test_sc6_new_findings_and_verdict(v3_replay):
    outcomes = _load("v3_outcomes.json")
    new_ids = [f["id"] for f in v3_replay["findings"] if f["status"] == "new"]
    assert new_ids == outcomes["expect_new"]
    assert v3_replay["verdict"] == outcomes["expect_verdict"]
    assert v3_replay["claim_cap"] == outcomes["expect_claim_cap"]
    assert not v3_replay["contract_errors"]


def test_sc6_resolved_carries_its_evidence(v3_replay):
    """`resolved` is an evidence-bearing status; the record must survive into the verdict."""
    for finding in v3_replay["findings"]:
        if finding["status"] == "resolved":
            assert finding.get("rederivation"), f"{finding['id']} resolved without a record"


def test_non_confirming_rederivation_is_not_resolved(v3_replay):
    """A re-derivation that ran and disagreed is not positive evidence of a fix."""
    denominator = next(f for f in v3_replay["findings"] if f["id"] == "f3-fidelity-denominator")
    assert denominator["status"] == "unconfirmed-resolved"
    assert "rederivation" not in denominator


def test_v3_findings_are_real_f3_defects():
    """V3's successor state is synthesised; its findings must not be.

    An earlier draft named two defects ('stale model load', 'sign inversion')
    that the recorded audit does not contain — the audit lists the RQ1 sign as
    confirmed *correct*. Fixture ids that look real but aren't are worse than
    obviously fake ones, so V3 is pinned to f3_defects.json.
    """
    real = {d["id"] for d in _load("f3_defects.json")["defects"]}
    v3 = {f["id"] for f in _load("v3_verdict.json")["findings"]}
    assert v3 <= real, f"V3 invents defects that are not in the audit: {sorted(v3 - real)}"


# ── the Tests-table error row: the three block legs, and the pass ─────────────


def test_clean_report_with_complete_coverage_passes():
    out = ccl.compare([_clean_report()], iteration=4, expected_critics=["analysis"])
    assert out["verdict"] == "pass", out["reasons"]
    assert out["findings"] == []


def test_any_open_mismatch_blocks():
    report = _clean_report(
        findings=[
            {
                "id": "F1",
                "class": "mismatch",
                "owner": "writer",
                "claim_id": "abstract.acc",
                "claimed": "0.559",
                "derived": "0.0147",
                "records": ["mle/results/arm_summary.json"],
                "fix": "cite the source",
            }
        ]
    )
    out = ccl.compare([report], iteration=4, expected_critics=["analysis"])
    assert out["verdict"] == "block"
    assert any("open mismatch F1" in r for r in out["reasons"])


def test_incomplete_coverage_blocks():
    """derived + unverified != claims_in - the silent, short or crashed critic."""
    report = _clean_report(coverage={"claims_in": 10, "derived": 6, "unverified": 1})
    out = ccl.compare([report], iteration=4, expected_critics=["analysis"])
    assert out["verdict"] == "block"
    assert any("incomplete coverage" in r for r in out["reasons"])


def test_derived_below_floor_blocks():
    report = _clean_report(coverage={"claims_in": 10, "derived": 5, "unverified": 5})
    out = ccl.compare([report], iteration=4, expected_critics=["analysis"], derived_floor=0.8)
    assert out["verdict"] == "block"
    assert any("below floor" in r for r in out["reasons"])


def test_one_critic_cannot_mask_another_via_summed_coverage():
    """Coverage is checked per critic; summing would let an overshoot hide a shortfall."""
    good = _clean_report("analysis", coverage={"claims_in": 10, "derived": 10, "unverified": 0})
    short = _clean_report("paper", coverage={"claims_in": 10, "derived": 4, "unverified": 6})
    out = ccl.compare([good, short], iteration=4, expected_critics=["analysis", "paper"])
    assert out["verdict"] == "block"
    assert out["per_critic"]["analysis"]["reasons"] == []
    assert out["per_critic"]["paper"]["reasons"]


# ── the blind-derivation join ────────────────────────────────────────────────

CLAIMS = {
    "abstract.best_arm_acc": {"claimed": "0.559", "owner": "writer"},
    "results.table1.gpt4o_acc": {"claimed": "0.548", "owner": "mle-solver"},
}


def test_blind_derivation_that_disagrees_becomes_a_mismatch():
    """The analysis critic never sees the claim, so the comparator makes the call."""
    report = _clean_report(
        coverage={"claims_in": 2, "derived": 2, "unverified": 0},
        derivations=[
            {
                "claim_id": "abstract.best_arm_acc",
                "derived": "0.0147",
                "records": ["mle/results/arm_summary.json"],
            },
            {
                "claim_id": "results.table1.gpt4o_acc",
                "derived": "0.5481",
                "records": ["mle/results/eval.jsonl"],
            },
        ],
    )
    out = ccl.compare([report], iteration=4, claims=CLAIMS, expected_critics=["analysis"])
    mismatches = [f for f in out["findings"] if f["class"] == "mismatch"]
    assert len(mismatches) == 1, "only the disagreeing derivation should raise"
    found = mismatches[0]
    assert found["claim_id"] == "abstract.best_arm_acc"
    assert (found["claimed"], found["derived"]) == ("0.559", "0.0147")
    assert found["owner"] == "writer"
    assert found["raised_by"] == "comparator"
    assert out["verdict"] == "block"


def test_agreeing_derivation_raises_nothing():
    report = _clean_report(
        coverage={"claims_in": 1, "derived": 1, "unverified": 0},
        derivations=[
            {"claim_id": "results.table1.gpt4o_acc", "derived": "0.5481", "records": ["r.jsonl"]}
        ],
    )
    out = ccl.compare([report], iteration=4, claims=CLAIMS, expected_critics=["analysis"])
    assert out["verdict"] == "pass", out["reasons"]


def test_manufactured_finding_ids_are_stable_across_iterations():
    """A deterministic id is what lets `reconcile` follow the defect's lineage."""
    report = _clean_report(
        coverage={"claims_in": 1, "derived": 1, "unverified": 0},
        derivations=[
            {"claim_id": "abstract.best_arm_acc", "derived": "0.0147", "records": ["r.json"]}
        ],
    )
    first = ccl.compare([report], iteration=4, claims=CLAIMS, expected_critics=["analysis"])
    second = ccl.compare(
        [report], iteration=5, claims=CLAIMS, previous=first, expected_critics=["analysis"]
    )
    assert (
        first["findings"][0]["id"]
        == second["findings"][0]["id"]
        == "analysis:abstract.best_arm_acc"
    )
    assert first["findings"][0]["status"] == "new"
    assert second["findings"][0]["status"] == "recurring"
    assert second["findings"][0]["origin_id"] == "analysis:abstract.best_arm_acc"


def test_derivation_outside_the_brief_is_a_contract_error():
    report = _clean_report(
        coverage={"claims_in": 1, "derived": 1, "unverified": 0},
        derivations=[{"claim_id": "invented.claim", "derived": "1.0", "records": ["r.json"]}],
    )
    out = ccl.compare([report], iteration=4, claims=CLAIMS, expected_critics=["analysis"])
    assert out["verdict"] == "block"
    assert any("not in the brief" in e["detail"] for e in out["contract_errors"])


def test_derivation_without_a_claim_id_is_a_contract_error():
    report = _clean_report(
        coverage={"claims_in": 1, "derived": 1, "unverified": 0},
        derivations=[{"derived": "1.0", "records": ["r.json"]}],
    )
    out = ccl.compare([report], iteration=4, claims=CLAIMS, expected_critics=["analysis"])
    assert out["verdict"] == "block"
    assert any("missing 'claim_id'" in e["detail"] for e in out["contract_errors"])


# ── panels ───────────────────────────────────────────────────────────────────


def _panel_member(critic="paper", **over):
    report = {
        "critic": critic,
        "iteration": 3,
        "coverage": {"claims_in": 10, "derived": 10, "unverified": 0},
        "findings": [],
        "derivations": [],
    }
    report.update(over)
    return report


def _mismatch(fid, claim_id, claimed="1", derived="2"):
    return {
        "id": fid,
        "class": "mismatch",
        "owner": "writer",
        "claim_id": claim_id,
        "claimed": claimed,
        "derived": derived,
        "records": ["r.json"],
        "fix": "x",
    }


def test_a_panel_unions_rather_than_intersects():
    """The two defects the real panel exists for were single-model catches."""
    opus = _panel_member(findings=[_mismatch("o1", "shared.claim"), _mismatch("o2", "opus.only")])
    fable = _panel_member(findings=[_mismatch("f1", "shared.claim"), _mismatch("f2", "fable.only")])
    merged = ccl.merge_reports([opus, fable], models=["opus", "fable"])

    ids = {f["claim_id"] for f in merged["findings"]}
    assert ids == {"shared.claim", "opus.only", "fable.only"}
    assert merged["panel"]["agreed"] == 1
    assert merged["panel"]["single_model"] == 2


def test_agreement_is_recorded_per_finding():
    opus = _panel_member(findings=[_mismatch("o1", "shared.claim")])
    fable = _panel_member(findings=[_mismatch("f1", "shared.claim")])
    merged = ccl.merge_reports([opus, fable], models=["opus", "fable"])
    assert len(merged["findings"]) == 1
    assert merged["findings"][0]["found_by"] == ["opus", "fable"]


def test_panel_coverage_comes_from_the_best_member_not_the_sum():
    """Summing would double-count: both members answered the same brief."""
    thorough = _panel_member(coverage={"claims_in": 10, "derived": 10, "unverified": 0})
    lazy = _panel_member(coverage={"claims_in": 10, "derived": 3, "unverified": 7})
    merged = ccl.merge_reports([lazy, thorough], models=["lazy", "thorough"])
    assert merged["coverage"] == {"claims_in": 10, "derived": 10, "unverified": 0}
    assert merged["panel"]["coverage_from"] == "thorough"


def test_a_merged_panel_still_passes_the_comparator_unchanged():
    """Merging before the comparator is what keeps the comparator a pure diff."""
    clean_a = _panel_member("analysis")
    clean_b = _panel_member("analysis")
    merged = ccl.merge_reports([clean_a, clean_b], models=["opus", "fable"])
    out = ccl.compare([merged], iteration=3, expected_critics=["analysis"])
    assert out["verdict"] == "pass", out["reasons"]


def test_panel_weakest_claim_cap_wins():
    a = _panel_member(claim_cap="confident")
    b = _panel_member(claim_cap="inconclusive")
    assert ccl.merge_reports([a, b])["claim_cap"] == "inconclusive"


def test_panel_unions_derivations_by_claim():
    a = _panel_member(derivations=[{"claim_id": "c1", "derived": "1"}])
    b = _panel_member(
        derivations=[{"claim_id": "c1", "derived": "1"}, {"claim_id": "c2", "derived": "2"}]
    )
    merged = ccl.merge_reports([a, b])
    assert {d["claim_id"] for d in merged["derivations"]} == {"c1", "c2"}


def test_a_panel_of_one_is_just_that_report():
    solo = _panel_member(findings=[_mismatch("s1", "a.claim")])
    merged = ccl.merge_reports([solo], models=["opus"])
    assert merged["panel"]["agreed"] == 0
    assert merged["panel"]["single_model"] == 1
    assert merged["coverage"] == solo["coverage"]


def test_an_all_unparseable_panel_returns_nothing_rather_than_raising():
    assert ccl.merge_reports(["not json", 42]) == {}


def test_one_critics_findings_are_never_merged_into_each_other():
    """Two findings from the SAME model are two findings, whatever they are keyed
    on. The sufficiency critic ids every finding `n/a`, so keying on
    (claim_id, class) collapsed three findings into two and listed one model
    twice in `found_by` -- a panel silently losing what it was added to gain."""
    a = {
        "critic": "sufficiency",
        "iteration": 4,
        "claim_cap": "qualified",
        "coverage": {"claims_in": 0, "derived": 0, "unverified": 0},
        "findings": [
            {"id": "one", "class": "unverified", "owner": "manager", "claim_id": "n/a"},
            {"id": "two", "class": "unverified", "owner": "manager", "claim_id": "n/a"},
        ],
    }
    b = {**a, "findings": [
        {"id": "three", "class": "unverified", "owner": "manager", "claim_id": "n/a"},
    ]}

    merged = ccl.merge_reports([a, b], models=["sonnet", "fable"])
    assert [f["id"] for f in merged["findings"]] == ["one", "two", "three"]
    for finding in merged["findings"]:
        assert len(finding["found_by"]) == len(set(finding["found_by"]))


def test_the_merge_carries_the_enumeration_through():
    """Dropping `units` would hide the one thing it exists to show: which units
    were examined at all. The first enumerated panel run reported coverage over
    12 units and a merged report listing none of them."""
    a = {
        "critic": "sufficiency",
        "iteration": 4,
        "claim_cap": "qualified",
        "coverage": {"claims_in": 2, "derived": 2, "unverified": 0},
        "units": [
            {"unit_id": "grad.a", "description": "a", "judged": "sufficient"},
            {"unit_id": "grad.b", "description": "b", "judged": "insufficient"},
        ],
        "findings": [],
    }
    b = {**a, "units": [
        {"unit_id": "grad.b", "description": "b again", "judged": "insufficient"},
        {"unit_id": "grad.c", "description": "c", "judged": "not-assessable"},
    ]}

    merged = ccl.merge_reports([a, b], models=["sonnet", "fable"])
    assert [u["unit_id"] for u in merged["units"]] == ["grad.a", "grad.b", "grad.c"]


def test_two_models_naming_the_same_claim_are_recorded_as_agreeing():
    a = {
        "critic": "sufficiency",
        "iteration": 4,
        "claim_cap": "qualified",
        "coverage": {"claims_in": 0, "derived": 0, "unverified": 0},
        "findings": [
            {"id": "s1", "class": "unverified", "owner": "manager", "claim_id": "grad.mini.delta"},
        ],
    }
    b = {**a, "findings": [
        {"id": "f1", "class": "unverified", "owner": "manager", "claim_id": "grad.mini.delta"},
        {"id": "f2", "class": "unverified", "owner": "manager", "claim_id": "grad.gpt4o.delta"},
    ]}

    merged = ccl.merge_reports([a, b], models=["sonnet", "fable"])
    assert len(merged["findings"]) == 2, "the shared claim merges, the new one is kept"
    assert merged["findings"][0]["found_by"] == ["sonnet", "fable"]
    assert merged["panel"]["agreed"] == 1
    assert merged["panel"]["single_model"] == 1


# ── contract violations block rather than raise ──────────────────────────────


def test_missing_critic_blocks():
    out = ccl.compare(
        [_clean_report("analysis")], iteration=4, expected_critics=["analysis", "paper"]
    )
    assert out["verdict"] == "block"
    assert any("paper" in r and "did not run" in r for r in out["reasons"])


@pytest.mark.parametrize(
    "raw",
    [
        "not json at all",
        '{"critic": "analysis", "iteration": 4}',  # no coverage
        {
            "critic": "nope",
            "iteration": 4,
            "coverage": {"claims_in": 1, "derived": 1, "unverified": 0},
        },
        {
            "critic": "analysis",
            "iteration": "four",
            "coverage": {"claims_in": 1, "derived": 1, "unverified": 0},
        },
        42,
    ],
)
def test_malformed_report_blocks_without_raising(raw):
    out = ccl.compare([raw], iteration=4)
    assert out["verdict"] == "block"
    assert out["contract_errors"]


def test_mismatch_without_both_values_is_a_contract_error():
    report = _clean_report(
        findings=[
            {
                "id": "F1",
                "class": "mismatch",
                "owner": "writer",
                "claim_id": "abstract.acc",
                "claimed": "0.559",
                "records": ["r.json"],
                "fix": "x",
            }
        ]
    )
    out = ccl.compare([report], iteration=4, expected_critics=["analysis"])
    assert out["verdict"] == "block"
    assert any("missing 'derived'" in e["detail"] for e in out["contract_errors"])


def test_critic_supplied_verdict_is_flagged_and_overridden():
    """Critics do not render verdicts. One that tries must not be able to pass itself."""
    report = _clean_report(
        verdict="pass",
        findings=[
            {
                "id": "F1",
                "class": "mismatch",
                "owner": "writer",
                "claim_id": "abstract.acc",
                "claimed": "1",
                "derived": "2",
                "records": ["r.json"],
                "fix": "x",
            }
        ],
    )
    out = ccl.compare([report], iteration=4, expected_critics=["analysis"])
    assert out["verdict"] == "block"
    assert any("do not render one" in e["detail"] for e in out["contract_errors"])


def test_iteration_drift_blocks():
    out = ccl.compare([_clean_report(iteration=3)], iteration=4, expected_critics=["analysis"])
    assert out["verdict"] == "block"
    assert any("!= run iteration" in e["detail"] for e in out["contract_errors"])


# ── value comparison ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "claimed,derived,expected",
    [
        ("0.0147", "0.014703", True),  # published precision governs
        ("0.0147", "0.559", False),
        ("-0.42", "-0.4197", True),
        ("12", "12", True),
        ("12", "13", False),
        ("1.0e-3", "0.001", True),  # no usable precision -> relative epsilon
        ("N/A", "n/a", True),
        ("alfworld", "scienceworld", False),
        # How critics actually write numbers. Every row below was a spurious
        # "mismatch" in the first live F3 replay - 65 of 90 were this.
        ("2,994", "2994", True),  # thousands separator
        ("98.8%", "0.9878", True),  # percent against fraction
        ("0.9878", "98.8%", True),  # and the other way round
        ("100.0%", "1.000", True),
        ("500 tasks per env", "500 baseline tasks", True),  # value buried in prose
        ("11,920 replay rows", "11920 (5920 alf)", True),
        ("0.0147", "0.014705882352941176 (≈0.015)", True),  # the SC1 case
        ("50%", "50%", True),  # both percent: no rescaling needed either way
        ("98.8%", "0.9900", False),  # a percentage still has to be the right one
        ("98.8%", "0.9871", False),  # ...to the precision the percentage states
        # Ground truth arrives from JSON, where a value is a number and not a
        # string. A float claim states a precision too, or the ground-truth side
        # compares at 1e-9 and a correct derivation reads as a mismatch.
        (0.0147, "0.014705882352941176 (≈0.015)", True),
        (0.0147, 0.0152, False),
        (0.559, 0.0147058823, False),
    ],
)
def test_values_match(claimed, derived, expected):
    assert ccl.values_match(claimed, derived) is expected


def test_loosening_does_not_mask_any_real_f3_defect():
    """The prose/percent handling must not turn a genuine defect into agreement.

    This is the guard on the fix: every audited F3 defect that carries both a
    claimed and a true value must still compare as a mismatch, including the
    pairs that differ only in the last digits (1,494 vs 1,480).
    """
    import json

    gt = json.loads((FIXTURES / "f3_defects.json").read_text(encoding="utf-8"))
    checked = 0
    for defect in gt["defects"]:
        claimed, true_value = defect.get("claimed"), defect.get("true_value")
        if claimed in (None, "") or true_value in (None, ""):
            continue
        a, b = ccl._as_number(claimed), ccl._as_number(true_value)
        # Select on the numbers differing, not on formatting. Where they agree
        # the defect is not a disagreement about a value at all:
        # f3-mislabeled-baseline prints the *right* 0.145 against the *wrong*
        # population, so no value comparison can ever catch it — a critic has to
        # notice the unit of analysis and say so.
        if a is None or b is None or a == b:
            continue
        checked += 1
        assert not ccl.values_match(claimed, true_value), (
            f"{defect['id']}: {claimed!r} vs {true_value!r} now reads as agreement — "
            "the loosening has swallowed a real defect"
        )
    assert checked >= 4, f"only {checked} numeric defect pairs exercised; guard is too weak"


def test_thousands_separated_denominators_still_differ():
    """The real FIX-2 defect: 1,494 against 1,480 is a mismatch, not formatting."""
    assert not ccl.values_match("1,494", "1480")
    assert not ccl.values_match("2,994 checkpoints", "2980 replayed")


# ── composite values ────────────────────────────────────────────────────────
#
# A critic often answers with several quantities at once, and the two sides do
# not order them the same way. Comparing first-number-to-first-number measured a
# correlation against a sample size and reported exact agreements as mismatches:
# 37 of the 56 findings in the first scored F3 replay were this.


def test_a_composite_answer_compares_like_with_like():
    assert ccl.values_match(
        "N=616, rho=-0.133, CI[-0.21,-0.05]",
        "rho=-0.132862, N=616, p_boot=0.001",
    )


def test_a_composite_answer_still_reports_a_real_disagreement():
    """Shared labels are compared, so a differing one is still caught."""
    assert not ccl.values_match(
        "N=201, B7=0.095, delta=+0.02",
        "N=350, B7=0.090, delta=-0.01",
    )


def test_the_first_mention_of_a_label_wins():
    """A row reading "N=201 ... bf N=182" is about 201; its footnote is not the
    subject. Letting the last write through made the table-12 provenance defect
    compare on the wrong N and drop out of the score entirely."""
    assert ccl._labelled("N=201, B7 0.095, bf N=182")["n"] == "201"


def test_scientific_notation_states_its_precision_in_significant_figures():
    """1.2e-4 is a claim about two significant figures. Reading it as stating no
    precision let the other side dictate six places and called 0.000122 a
    disagreement."""
    assert ccl.values_match("1.2e-4", "0.000122")
    assert not ccl.values_match("1.2e-4", "0.00015")
    assert ccl.values_match(
        "N=557, rho=-0.162, CI[-0.239,-0.103], p_raw=1.2e-4",
        "rho=-0.162096, N=557, p_raw=0.000122",
    )


def test_a_label_may_carry_an_exponent_and_an_approximate_equals():
    """`rho^2 ~= 1.8%` names a quantity. Without this the leading `2` of the
    exponent was read as the claim's value and compared against a correlation."""
    assert ccl._labelled("rho^2 ~= 1.8%") == {"rho^2": "1.8%"}
    assert ccl.values_match(
        "rho^2 ~= 1.8%",
        "rho=-0.132862 -> rho^2=0.017652 (~1.77% variance explained)",
    )


def test_an_unlabelled_claim_is_checked_against_the_stated_quantities():
    """The paper writes "succ 0.095"; the records say "N=201, success=0.094527".
    Only one side names anything, so ask whether the claimed number is among the
    quantities the other side states -- not whether it equals the first one."""
    assert ccl.values_match(
        "succ 0.095, Delta +0.000[+0.000,+0.000]",
        "pooled test: N=201, success=0.094527, delta=0.0",
    )
    assert not ccl.values_match(
        "succ 0.140, Delta +0.000[+0.000,+0.000]",
        "pooled test: N=201, success=0.094527, delta=0.0",
    )


# ── persistence ──────────────────────────────────────────────────────────────


def test_verdicts_persist_in_sequence(tmp_path):
    ccl.write_verdict(tmp_path, {"iteration": 3, "verdict": "block", "findings": []})
    ccl.write_verdict(tmp_path, {"iteration": 4, "verdict": "pass", "findings": []})
    assert ccl.load_previous_verdict(tmp_path, 5)["iteration"] == 4
    assert ccl.load_previous_verdict(tmp_path, 4)["iteration"] == 3
    assert ccl.load_previous_verdict(tmp_path, 3) is None
    assert ccl.load_previous_verdict(tmp_path / "nope", 4) is None
