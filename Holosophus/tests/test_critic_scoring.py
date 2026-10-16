"""The scorer that decides whether the critics work.

Design: docs/plans/2026-08-18-self-improving-loop-verification.md.

Scored against synthetic verdicts, not live critic output: the point is to prove
the *measuring instrument* is sound before spending anything on model calls. A
scorer that flatters a bad critic is worse than no scorer.
"""

import json
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

import cc_critic_score as ccs  # noqa: E402

FIXTURES = PROJECT_DIR / "holosophos" / "evals_and_reports" / "data" / "critics"
F3 = ccs.load_ground_truth(FIXTURES / "f3_defects.json")
F4 = ccs.load_ground_truth(FIXTURES / "f4_defects.json")
F5 = ccs.load_ground_truth(FIXTURES / "f5_actions.json")


def finding(**kw):
    base = {"id": "x", "class": "mismatch", "status": "new", "records": []}
    base.update(kw)
    return base


def verdict(*findings, **kw):
    return {"iteration": 3, "verdict": "block", "findings": list(findings), **kw}


def perfect_verdict():
    """A finding for every F3 defect, joined on claim_id."""
    return verdict(
        *[
            finding(
                id=f"got-{i}",
                claim_id=d["claim_id"],
                claimed=d.get("claimed"),
                derived=d.get("true_value"),
                records=[d["source"]] if d.get("source") else [],
            )
            for i, d in enumerate(F3["defects"])
        ]
    )


# ── recall ───────────────────────────────────────────────────────────────────


def test_a_perfect_critic_scores_full_recall():
    score = ccs.score_defects(perfect_verdict(), F3, fixture="F3")
    assert score.recall == 1.0
    assert not score.false_positives
    assert all(m.join == "claim_id" for m in score.caught)


def test_an_empty_verdict_scores_zero_recall_not_a_crash():
    score = ccs.score_defects(verdict(), F3, fixture="F3")
    assert score.recall == 0.0
    assert len(score.matches) == len(F3["defects"])
    assert score.precision == 1.0, "no findings means nothing wrong was said"


def test_resolved_findings_do_not_count_as_catches():
    """A carried-over resolved finding is closed, not a fresh detection."""
    v = perfect_verdict()
    for f in v["findings"]:
        f["status"] = "resolved"
    assert ccs.score_defects(v, F3, fixture="F3").recall == 0.0


# ── precision: the check the spec's SC2 does not make ────────────────────────


def test_accusing_a_confirmed_clean_claim_is_a_false_positive():
    clean = F3["confirmed_clean"][0]
    v = verdict(finding(id="bogus", claim_id=clean["claim_id"], claimed="1", derived="2"))
    score = ccs.score_defects(v, F3, fixture="F3")
    assert len(score.false_positives) == 1
    assert score.false_positives[0]["verified_clean"] == clean["id"]
    assert score.precision == 0.0


def test_a_flag_everything_critic_passes_recall_but_fails_precision():
    """The failure mode recall alone cannot see."""
    noise = [
        finding(id=f"noise-{i}", claim_id=c["claim_id"], claimed="1", derived="2")
        for i, c in enumerate(F3["confirmed_clean"])
        if not c["claim_id"].endswith("*")
    ]
    v = verdict(*(perfect_verdict()["findings"] + noise))
    score = ccs.score_defects(v, F3, fixture="F3")
    assert score.recall == 1.0
    assert score.precision < 1.0
    assert len(score.false_positives) == len(noise)


def test_an_unmatched_finding_is_neither_caught_nor_a_false_positive():
    """The audit was thorough but is not certified complete."""
    v = verdict(
        finding(id="novel", claim_id="sec:something.nobody.checked", claimed="1", derived="2")
    )
    score = ccs.score_defects(v, F3, fixture="F3")
    assert not score.false_positives
    assert len(score.unmatched) == 1
    assert score.precision == 1.0


def test_one_finding_cannot_be_credited_for_several_defects():
    """The flaw the first dry run exposed: one canned finding scored 4 of 13.

    Several F3 defects cite the same source file, so the weak source+type join
    matched them all to a single finding. A scorer that flatters is worse than
    one that under-reports, because nobody goes looking for the bug.
    """
    target = next(d for d in F3["defects"] if d.get("is_sc1_target"))
    sharing = [d for d in F3["defects"] if d.get("source") == target["source"]]
    assert len(sharing) > 1, "fixture no longer has defects sharing a source; test is vacuous"

    v = verdict(
        finding(
            id="only-one",
            claim_id=target["claim_id"],
            claimed=target["claimed"],
            derived=target["true_value"],
            records=[target["source"]],
        )
    )
    score = ccs.score_defects(v, F3, fixture="F3")
    assert len(score.caught) == 1, [m.defect_id for m in score.caught]
    assert score.caught[0].defect_id == target["id"]


def test_stronger_joins_win_the_finding():
    """A finding that could join two defects goes to the one it matches best."""
    by_id, by_source = F3["defects"][0], F3["defects"][1]
    v = verdict(
        finding(
            id="f",
            claim_id=by_id["claim_id"],
            claimed="1",
            derived="2",
            records=[by_source["source"]],
        )
    )
    assigned, leftover = ccs.match_defects(F3["defects"], v["findings"])
    assert by_id["id"] in assigned and assigned[by_id["id"]][1] == "claim_id"
    assert by_source["id"] not in assigned
    assert leftover == []


# ── join strength is reported, not hidden ────────────────────────────────────


def test_a_value_only_join_is_recorded_as_weaker():
    """The paper critic invents its own claim_ids, so value joins are expected."""
    target = next(d for d in F3["defects"] if d.get("is_sc1_target"))
    v = verdict(
        finding(
            id="byvalue",
            claim_id="its.own.id",
            claimed=target["claimed"],
            derived=target["true_value"],
        )
    )
    score = ccs.score_defects(v, F3, fixture="F3")
    hit = next(m for m in score.caught if m.defect_id == target["id"])
    assert hit.join == "value"


def test_per_defect_type_spread_is_reported():
    """A critic that only catches fabrications must not read as a general result."""
    target = next(d for d in F3["defects"] if d.get("is_sc1_target"))
    v = verdict(
        finding(
            id="only-one",
            claim_id=target["claim_id"],
            claimed=target["claimed"],
            derived=target["true_value"],
        )
    )
    by_type = ccs.score_defects(v, F3, fixture="F3").by_type()
    assert by_type["fabricated-value"] == (1, 1)
    assert by_type["denominator-mismatch"] == (0, 1)


# ── the named criteria ───────────────────────────────────────────────────────


def test_sc1_needs_all_three_parts():
    target = next(d for d in F3["defects"] if d.get("is_sc1_target"))
    full = verdict(
        finding(
            id="ok",
            claim_id="x",
            claimed=target["claimed"],
            derived=target["true_value"],
            records=[target["source"]],
        )
    )
    assert ccs.check_sc1(full, F3)["pass"]

    for drop in ("claimed", "derived", "records"):
        partial = json.loads(json.dumps(full))
        partial["findings"][0][drop] = "" if drop != "records" else []
        assert not ccs.check_sc1(partial, F3)["pass"], f"SC1 passed without {drop}"


def test_sc1_accepts_the_source_precision_not_just_the_rounded_literal():
    """The source records 0.014705882...; the paper prints 0.0147. Both are right."""
    target = next(d for d in F3["defects"] if d.get("is_sc1_target"))
    v = verdict(
        finding(
            id="ok",
            claim_id="x",
            claimed="0.559",
            derived="0.014705882352941176",
            records=[target["source"]],
        )
    )
    assert ccs.check_sc1(v, F3)["pass"]


def test_sc2_counts_only_sourced_defects_against_the_agreed_floor():
    score = ccs.score_defects(perfect_verdict(), F3, fixture="F3")
    result = ccs.check_sc2(score, F3)
    assert result["pass"]
    assert result["floor"] == 3
    assert result["population"] == sum(1 for d in F3["defects"] if d.get("sourced"))
    assert result["caught"] == result["population"]


def test_sc2_fails_below_the_floor():
    sourced = [d for d in F3["defects"] if d.get("sourced")][:2]
    v = verdict(
        *[
            finding(
                id=f"s{i}",
                claim_id=d["claim_id"],
                claimed=d.get("claimed"),
                derived=d.get("true_value"),
            )
            for i, d in enumerate(sourced)
        ]
    )
    assert not ccs.check_sc2(ccs.score_defects(v, F3, fixture="F3"), F3)["pass"]


def test_a_fixture_can_declare_what_identifies_its_defect():
    """A critic that reaches the same conclusion by reading different files is
    still right. Both models found the F4 gpt-4o defect through
    iter4_corrections.md and the iteration-3 file, never opening the
    iter4_gpt4o_indomain.json the defect cites, so the filename join scored a
    correct catch as a miss."""
    defect = next(d for d in F4["sufficiency_defects"] if d["id"] == "f4-underpowered-gpt4o")
    assert defect.get("identifies_any"), "fixture no longer declares this; test is vacuous"

    hit = finding(id="x", **{"class": "unverified"}, claim_id="rq2.gpt4o_indomain",
                  records=["mle/results/iter4_corrections.md"])
    assert ccs._joins(defect, hit, "declared")
    assert not ccs._joins(defect, hit, "source+type")

    other = finding(id="y", **{"class": "unverified"}, claim_id="rq2.gpt5mini_indomain")
    assert not ccs._joins(defect, other, "declared")


def test_a_declared_join_needs_the_finding_to_say_what_it_is_about():
    """It matches the finding's claim_id, not its prose. A finding that merely
    mentions the cell in passing is what the panel run was full of."""
    defect = next(d for d in F4["sufficiency_defects"] if d["id"] == "f4-underpowered-gpt4o")
    passing_mention = finding(
        id="z", **{"class": "unverified"}, claim_id="rq2.gpt5mini",
        fix="gpt-4o has real pooled Oracle headroom (+0.037), unlike gpt-5-mini.",
    )
    assert not ccs._joins(defect, passing_mention, "declared")


def test_sc5_needs_both_a_cap_and_the_named_cell():
    target = next(d for d in F4["sufficiency_defects"] if d.get("is_sc5_target"))
    good = verdict(
        finding(id="p", **{"class": "unverified"}, claim_id=target["claim_id"], fix="raise n"),
        claim_cap="inconclusive",
    )
    assert ccs.check_sc5(good, F4)["pass"]

    assert not ccs.check_sc5(verdict(claim_cap="inconclusive"), F4)[
        "pass"
    ], "cap without naming the cell"
    named_only = verdict(
        finding(id="p", **{"class": "unverified"}, claim_id=target["claim_id"]),
        claim_cap="confident",
    )
    assert not ccs.check_sc5(named_only, F4)["pass"], "named the cell but called it confident"


def test_the_cell_may_be_named_the_way_a_write_up_spells_it():
    """The live F4 critic named the cell in every finding it returned and SC5
    still failed: the fixture writes `gpt5mini` inside a claim_id and the paper
    writes `gpt-5-mini`. Which spellings count is the fixture's to declare."""
    v = verdict(
        finding(
            id="p",
            **{"class": "uninterpretable"},
            claim_id="n/a",
            fix="The gpt-5-mini backbone evaluates only N_test=40 tasks (94 triples).",
        ),
        claim_cap="qualified",
    )
    result = ccs.check_sc5(v, F4)
    assert result["named_the_cell"]
    assert result["pass"], "qualified is an accepted cap for this fixture"


def test_naming_some_other_cell_is_not_naming_this_one():
    v = verdict(
        finding(
            id="p",
            **{"class": "uninterpretable"},
            claim_id="n/a",
            fix="The gpt-4o backbone is fine; nothing to report.",
        ),
        claim_cap="qualified",
    )
    assert not ccs.check_sc5(v, F4)["named_the_cell"]


# ---- SC3: is a reported value anchored in the records it cites? -------------


def _records(tmp_path, **files):
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    return tmp_path


def test_a_single_reported_value_must_be_in_the_cited_record(tmp_path):
    root = _records(tmp_path, a="best_arm_accuracy = 0.014705882352941176")
    v = verdict(finding(id="ok", derived="0.015", records=["a"]))
    assert ccs.check_sc3(v, root)["failures"] == 0, "0.015 is 0.0147058 at its own precision"

    v = verdict(finding(id="bad", derived="0.559", records=["a"]))
    assert ccs.check_sc3(v, root)["failures"] == 1


def test_a_computed_value_needs_an_operand_not_the_result(tmp_path):
    """The analysis critic derives; its results are not sitting in a file. What
    separates arithmetic over the records from arithmetic over nothing is whether
    the inputs are there."""
    root = _records(tmp_path, corpus="checkpoint_triples,2994")
    v = verdict(finding(id="ok", derived="4 arms x 2994 triples = 11976 rows", records=["corpus"]))
    assert ccs.check_sc3(v, root)["failures"] == 0

    other = tmp_path / "other"
    other.mkdir()
    _records(other, summary="mean,0.5 sd,0.1")
    v = verdict(finding(id="bad", derived="4 arms x 2994 triples = 11976 rows",
                        records=["summary"]))
    assert ccs.check_sc3(v, other)["failures"] == 1, (
        "none of 4, 2994 or 11976 is in that record — the derivation is unanchored"
    )


def test_a_cited_record_that_does_not_exist_is_a_failure(tmp_path):
    v = verdict(finding(id="ghost", derived="0.5", records=["nowhere.json"]))
    result = ccs.check_sc3(v, tmp_path)
    assert result["failures"] == 1
    assert "no cited record could be read" in result["detail"][0]["why"]


def test_findings_asserting_absence_are_not_counted(tmp_path):
    """`unverified` and `uninterpretable` report that a value is missing, not what
    it is. There is nothing to anchor, so they neither pass nor fail this."""
    root = _records(tmp_path, a="anything")
    v = verdict(
        finding(id="u", **{"class": "unverified"}, derived=None, records=["a"]),
        finding(id="n", derived="not a number", records=["a"]),
    )
    result = ccs.check_sc3(v, root)
    assert result["checked"] == 0 and result["failures"] == 0


def test_the_allowance_is_what_decides_the_pass(tmp_path):
    root = _records(tmp_path, a="0.1")
    v = verdict(
        finding(id="b1", derived="0.9", records=["a"]),
        finding(id="b2", derived="0.8", records=["a"]),
    )
    assert not ccs.check_sc3(v, root, allowed_failures=1)["pass"]
    assert ccs.check_sc3(v, root, allowed_failures=2)["pass"]


def test_sc7_needs_coverage_and_resolvable_traces():
    v = verdict(finding(id="F-a"), finding(id="F-b"), finding(id="F-c"))
    plan = {
        "actions": [
            {
                "action": "Scope the iteration to ALFWorld only, dropping ScienceWorld",
                "traces_to": "F-a",
            },
            {"action": "Extend the gpt-5-mini corpus to power the cell", "traces_to": "F-b"},
            {"action": "Drop gpt-4o and add a stronger backbone", "traces_to": "F-c"},
        ]
    }
    assert ccs.check_sc7(plan, v, F5)["pass"]


def test_a_recogniser_matches_however_the_plan_hyphenates_it():
    """The live planner wrote "scope every claim to ALFWorld-only"; the recogniser
    reads "alfworld only". Same words, and the substring test said no."""
    v = verdict(finding(id="F-a"), finding(id="F-b"))
    plan = {
        "actions": [
            {
                "action": "Scope every gpt-5-mini claim, table and abstract sentence "
                "to ALFWorld-only, and say ScienceWorld was dropped",
                "traces_to": "F-a",
            },
            {"action": "Extend the corpus so N_test reaches 150-200", "traces_to": "F-b"},
        ]
    }
    covered = ccs.check_sc7(plan, v, F5)["covered"]
    assert "a1-single-environment" in covered
    assert "a2-power-the-middle-rung" in covered


def test_an_action_the_verdict_never_carried_is_named_as_unreachable():
    """The planner cannot act on a defect the critics did not report. It still
    counts against SC7 -- the criterion asks for all three -- but the result says
    where the gap actually is, so the fix lands on the critic and not the planner."""
    v = verdict(finding(id="F-a"))
    plan = {"actions": [{"action": "alfworld only", "traces_to": "F-a"}]}
    answered_by_a3 = next(a for a in F5["actions"] if a["id"] == "a3-replace-the-weak-rung")

    result = ccs.check_sc7(plan, v, F5, upstream_missed=[answered_by_a3["answers"]])
    assert result["unreachable"] == ["a3-replace-the-weak-rung"]
    assert "a3-replace-the-weak-rung" in result["missing"]
    assert not result["pass"]

    # Without the upstream context the same gap is reported, just not attributed.
    assert "unreachable" not in ccs.check_sc7(plan, v, F5)


def test_sc7_fails_on_a_dangling_trace():
    v = verdict(finding(id="F-a"))
    plan = {
        "actions": [
            {"action": "alfworld only; power; drop gpt-4o", "traces_to": "the original task"}
        ]
    }
    result = ccs.check_sc7(plan, v, F5)
    assert not result["pass"]
    assert result["dangling_traces_to"] == ["the original task"]


def test_sc7_fails_when_an_action_is_missing():
    v = verdict(finding(id="F-a"))
    plan = {"actions": [{"action": "Scope to ALFWorld only", "traces_to": "F-a"}]}
    result = ccs.check_sc7(plan, v, F5)
    assert not result["pass"]
    assert set(result["missing"]) == {"a2-power-the-middle-rung", "a3-replace-the-weak-rung"}


def test_sc7_resolves_a_trace_through_origin_id():
    """A recurring finding arrives under a new id; its lineage key must still resolve."""
    v = verdict(finding(id="F-new", origin_id="F-old"))
    plan = {
        "actions": [
            {"action": "alfworld only, power the middle rung, drop gpt-4o", "traces_to": "F-old"}
        ]
    }
    assert not ccs.check_sc7(plan, v, F5)["dangling_traces_to"]


# ── rendering ────────────────────────────────────────────────────────────────


def test_render_is_readable_and_names_what_was_missed():
    score = ccs.score_defects(verdict(), F3, fixture="F3")
    score.criteria = {"SC2": ccs.check_sc2(score, F3)}
    out = ccs.render(score)
    assert "recall 0/" in out
    assert "missed:" in out
    assert "SC2: FAIL" in out
