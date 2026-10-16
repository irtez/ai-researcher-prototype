"""Score a critic verdict against an adjudicated ground-truth fixture.

Design: ``docs/plans/2026-08-18-self-improving-loop-verification.md`` (Approved).

Separate from the comparator on purpose. The comparator decides whether a *run*
passes; this decides whether the *critics* work, and it only ever runs against a
fixture whose answers are known.

Two things the spec's criteria do not say outright, both learned from the
ground truth:

* **Recall alone flatters.** SC2 asks for >=3 of N caught. A critic that reports
  fifty findings satisfies that and is useless. ``f3_defects.json`` also records
  what both auditors verified *correct*, so precision is measurable and is
  reported next to recall.
* **Not every miss is a false positive.** A finding matching neither the defect
  list nor the confirmed-clean list is *unmatched*, not wrong: the audit was
  thorough but is not certified complete. Unmatched findings are counted and
  shown, never silently scored either way.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from cc_critic_lib import _as_number, _EMBEDDED_RE, values_match

# How a ground-truth defect was matched to a finding, strongest first. Reported
# per match so a run held together only by value coincidence is visible as such.
JOIN_STRENGTH = ("claim_id", "value", "declared", "source+type", "none")


@dataclass
class Match:
    defect_id: str
    finding_id: str | None
    join: str
    defect_type: str

    @property
    def caught(self) -> bool:
        return self.finding_id is not None


@dataclass
class Score:
    fixture: str
    matches: list[Match] = field(default_factory=list)
    false_positives: list[dict[str, Any]] = field(default_factory=list)
    unmatched: list[dict[str, Any]] = field(default_factory=list)
    criteria: dict[str, Any] = field(default_factory=dict)

    @property
    def caught(self) -> list[Match]:
        return [m for m in self.matches if m.caught]

    @property
    def recall(self) -> float:
        return len(self.caught) / len(self.matches) if self.matches else 0.0

    @property
    def precision(self) -> float:
        """Against the confirmed-clean list only — unmatched findings are excluded.

        Counting unmatched findings as errors would punish a critic for finding
        something the auditors missed, which is the opposite of the point.
        """
        hits = len(self.caught)
        denom = hits + len(self.false_positives)
        return hits / denom if denom else 1.0

    def by_type(self) -> dict[str, tuple[int, int]]:
        """caught / total per defect_type — the spread the spec asks for."""
        total: Counter[str] = Counter(m.defect_type for m in self.matches)
        hit: Counter[str] = Counter(m.defect_type for m in self.caught)
        return {t: (hit[t], total[t]) for t in sorted(total)}

    def as_dict(self) -> dict[str, Any]:
        return {
            "fixture": self.fixture,
            "recall": round(self.recall, 3),
            "precision": round(self.precision, 3),
            "caught": len(self.caught),
            "defects": len(self.matches),
            "false_positives": len(self.false_positives),
            "unmatched": len(self.unmatched),
            "by_defect_type": {k: f"{c}/{t}" for k, (c, t) in self.by_type().items()},
            "by_join_strength": dict(Counter(m.join for m in self.caught)),
            "criteria": self.criteria,
            "detail": {
                "missed": [m.defect_id for m in self.matches if not m.caught],
                "false_positives": self.false_positives,
                "unmatched": self.unmatched,
            },
        }


# ── matching ──────────────────────────────────────────────────────────────────


def _texts(finding: dict[str, Any]) -> str:
    parts = [finding.get(k) for k in ("claim_id", "claimed", "derived", "fix", "detail")]
    parts += list(finding.get("records") or [])
    return " ".join(str(p) for p in parts if p)


def _squash(text: str) -> str:
    """Strip everything but letters and digits, so an identifier and its prose
    spelling compare equal: a fixture writes ``gpt5mini`` and a critic writes
    ``gpt-5-mini``, and they name the same cell."""
    return re.sub(r"[^a-z0-9]", "", text.casefold())


def _joins(defect: dict[str, Any], finding: dict[str, Any], strength: str) -> bool:
    """Does this finding report this defect, at this join strength?"""
    if strength == "claim_id":
        claim_id = str(defect.get("claim_id") or "")
        return bool(claim_id) and str(finding.get("claim_id") or "") == claim_id
    if strength == "value":
        # The paper critic assigns its own claim_ids (it enumerates the write-up),
        # so an exact id join is not always available. A matching claimed value
        # against the same quantity is the next best evidence.
        claimed = defect.get("claimed")
        return (
            claimed not in (None, "")
            and finding.get("claimed") not in (None, "")
            and values_match(claimed, finding["claimed"])
        )
    if strength == "declared":
        # The fixture names what identifies this defect. Inferring it from a
        # claim_id or a cited path fails whenever the critic reached the same
        # conclusion by another route: the F4 gpt-4o defect cites
        # iter4_gpt4o_indomain.json, and two models found it by reading
        # iter4_corrections.md and the iteration-3 file instead. Matching on the
        # finding's own claim_id, which is its declaration of what it is about.
        wanted = defect.get("identifies_any") or []
        subject = _squash(str(finding.get("claim_id") or ""))
        return bool(subject) and any(_squash(str(w)) in subject for w in wanted)
    if strength == "source+type":
        src = str(defect.get("source") or "")
        return bool(src) and Path(src).name in _texts(finding)
    return False


def match_defects(
    defects: Sequence[dict[str, Any]], findings: Sequence[dict[str, Any]]
) -> tuple[dict[str, tuple[dict[str, Any], str]], list[dict[str, Any]]]:
    """Assign findings to defects, one finding to at most one defect.

    Strongest join first, and each finding is **consumed** once assigned. Without
    that, a single finding citing a widely-referenced source file joins to every
    defect sharing it: the first dry run scored one canned finding as 4 of 13
    defects caught. A scorer that flatters is worse than one that under-reports,
    since nobody goes looking for the bug.

    Returns the assignment and the findings nothing claimed.
    """
    taken: set[int] = set()
    assigned: dict[str, tuple[dict[str, Any], str]] = {}
    for strength in ("claim_id", "value", "declared", "source+type"):
        for defect in defects:
            if defect["id"] in assigned:
                continue
            for finding in findings:
                if id(finding) in taken:
                    continue
                if _joins(defect, finding, strength):
                    assigned[defect["id"]] = (finding, strength)
                    taken.add(id(finding))
                    break
    return assigned, [f for f in findings if id(f) not in taken]


def _is_false_positive(
    finding: dict[str, Any], clean: Sequence[dict[str, Any]]
) -> dict[str, Any] | None:
    """Did this finding accuse something both auditors verified as correct?"""
    fid = str(finding.get("claim_id") or "")
    for entry in clean:
        cid = str(entry.get("claim_id") or "")
        if not cid or cid == "*":
            continue
        if cid.endswith("*"):
            if fid.startswith(cid[:-1]):
                return {
                    "finding": finding.get("id"),
                    "claim_id": fid,
                    "verified_clean": entry["id"],
                }
        elif fid == cid:
            return {"finding": finding.get("id"), "claim_id": fid, "verified_clean": entry["id"]}
    return None


def score_defects(
    verdict: dict[str, Any],
    ground_truth: dict[str, Any],
    *,
    fixture: str,
    defect_key: str = "defects",
) -> Score:
    """Score a verdict's findings against a fixture's adjudicated defect list."""
    findings = [f for f in verdict.get("findings") or [] if f.get("status") != "resolved"]
    defects = ground_truth.get(defect_key) or []
    clean = ground_truth.get("confirmed_clean") or []

    score = Score(fixture=fixture)
    assigned, leftover = match_defects(defects, findings)
    for defect in defects:
        found, join = assigned.get(defect["id"], (None, "none"))
        score.matches.append(
            Match(
                defect_id=defect["id"],
                finding_id=found.get("id") if found else None,
                join=join,
                defect_type=defect.get("defect_type", "unlabelled"),
            )
        )

    for finding in leftover:
        fp = _is_false_positive(finding, clean)
        if fp:
            score.false_positives.append(fp)
        else:
            score.unmatched.append(
                {
                    "finding": finding.get("id"),
                    "claim_id": finding.get("claim_id"),
                    "class": finding.get("class"),
                }
            )
    return score


# ── the criteria the spec names ───────────────────────────────────────────────


def check_sc1(verdict: dict[str, Any], ground_truth: dict[str, Any]) -> dict[str, Any]:
    """The paper critic names the fabrication: claimed, true source value, source file."""
    target = next((d for d in ground_truth["defects"] if d.get("is_sc1_target")), None)
    if target is None:
        return {"pass": False, "why": "fixture declares no SC1 target"}
    leaf = Path(str(target["source"])).name
    for f in verdict.get("findings") or []:
        if f.get("class") != "mismatch":
            continue
        hit_claimed = f.get("claimed") not in (None, "") and values_match(
            target["claimed"], f["claimed"]
        )
        hit_derived = f.get("derived") not in (None, "") and values_match(
            target["true_value"], f["derived"]
        )
        hit_source = leaf in _texts(f)
        if hit_claimed and hit_derived and hit_source:
            return {"pass": True, "finding": f.get("id")}
    return {
        "pass": False,
        "why": f"no mismatch carrying claimed={target['claimed']}, derived={target['true_value']} and {leaf}",
    }


def check_sc2(score: Score, ground_truth: dict[str, Any]) -> dict[str, Any]:
    """>= the agreed floor of the sourced defects, joined on claim_id."""
    cfg = ground_truth.get("scoring") or {}
    floor = int(cfg.get("sc2_floor", 3))
    sourced = {d["id"] for d in ground_truth["defects"] if d.get("sourced")}
    hits = [m for m in score.caught if m.defect_id in sourced]
    return {
        "pass": len(hits) >= floor,
        "caught": len(hits),
        "floor": floor,
        "population": len(sourced),
        "note": "floor is the spec's agreed absolute bar; population is the true count",
    }


def _anchored(derived: Any, texts: Sequence[str]) -> bool:
    """Is this finding's value anchored in the records it cites?

    A single number must be *there*: the critic says it read it, so the record
    has to hold it. A value stated as a computation - "4 arms x 2994 triples =
    11976 rows" - cannot be, because the analysis critic's whole job is deriving
    values that are not sitting in a file; for those, at least one operand must
    be present, which is what distinguishes arithmetic over the records from
    arithmetic over nothing.

    Numbers compare through `values_match`, so a record printing
    0.014705882352941176 satisfies a critic that reported 0.015.
    """
    wanted = [m.group(0) for m in _EMBEDDED_RE.finditer(str(derived))]
    if not wanted:
        return True
    found = 0
    for token in wanted:
        for text in texts:
            if any(values_match(token, m.group(0)) for m in _EMBEDDED_RE.finditer(text)):
                found += 1
                break
    return found == len(wanted) if len(wanted) == 1 else found >= 1


def _record_text(root: Path, cited: str, *, cap: int = 4_000_000) -> str | None:
    """The text of a cited record, or None if it cannot be read.

    Critics cite paths relative to their read scope; a few cite only the leaf.
    Both resolve against the fixture root, and a path that resolves to nothing is
    itself the finding this check exists to make - a value attributed to a file
    that is not there.
    """
    candidate = root / cited
    if not candidate.is_file():
        matches = list(root.rglob(Path(cited).name))
        if len(matches) != 1:
            return None
        candidate = matches[0]
    try:
        if candidate.stat().st_size > cap:
            return None
        return candidate.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None


def check_sc3(
    verdict: dict[str, Any], root: Path, *, allowed_failures: int = 1
) -> dict[str, Any]:
    """Does each finding's reported value actually appear in the records it cites?

    **This is an anchoring check, not a re-derivation.** It reads the cited
    records and asks whether the reported value is grounded in the numbers there
    (see :func:`_anchored`). It catches the failure it exists to catch - a value
    attributed to a source that has nothing to do with it, which is what
    fabrication looks like from the outside - and does not prove the critic
    computed correctly. Independently recomputing the analysis is the stronger
    test and is out of scope at this iteration's cost.

    A finding with no `derived` value (the `unverified` and `uninterpretable`
    classes assert absence, not a number) is not applicable and is not counted.
    """
    checked, failures = 0, []
    for finding in verdict.get("findings") or []:
        derived = finding.get("derived")
        records = [str(r) for r in (finding.get("records") or []) if r]
        if derived in (None, "") or _as_number(derived) is None or not records:
            continue
        checked += 1
        texts = [t for t in (_record_text(root, r) for r in records) if t is not None]
        if not texts:
            failures.append({"finding": finding.get("id"), "why": "no cited record could be read"})
            continue
        if not _anchored(derived, texts):
            failures.append(
                {
                    "finding": finding.get("id"),
                    "why": f"no number in {str(derived)[:70]!r} appears in the cited records",
                }
            )
    return {
        "pass": len(failures) <= allowed_failures,
        "checked": checked,
        "failures": len(failures),
        "allowed": allowed_failures,
        "detail": failures[:10],
        "note": "the reported value is anchored in the cited records; not an independent recompute",
    }


def check_sc5(verdict: dict[str, Any], ground_truth: dict[str, Any]) -> dict[str, Any]:
    """Sufficiency caps the claim and names the under-powered cell."""
    expected = ground_truth.get("expected") or {}
    cap = verdict.get("claim_cap")
    target = next((d for d in ground_truth["sufficiency_defects"] if d.get("is_sc5_target")), None)

    # What counts as naming the cell is the fixture's to state, not this
    # function's to guess. Splitting the claim_id and demanding every part
    # verbatim failed a critic that had named the cell in every finding it
    # returned: the write-up calls it "gpt-5-mini" and describes the quantity
    # rather than spelling out `learned_delta`.
    alternatives = [str(w) for w in (expected.get("must_name_any") or [])]
    if alternatives:
        needles, require_all = [_squash(w) for w in alternatives], False
    elif target:
        needles = [_squash(part) for part in str(target["claim_id"]).split(".")[-2:]]
        require_all = True
    else:
        needles, require_all = [], True

    named = False
    for finding in verdict.get("findings") or []:
        blob = _squash(_texts(finding))
        if needles and (all if require_all else any)(n in blob for n in needles):
            named = True
            break

    allowed = expected.get("claim_cap")
    capped = (
        cap in allowed
        if isinstance(allowed, list) and allowed
        else cap is not None and cap != expected.get("claim_cap_must_not_be", "confident")
    )
    return {"pass": capped and named, "claim_cap": cap, "named_the_cell": named}


def check_sc7(
    plan: Any,
    verdict: dict[str, Any],
    ground_truth: dict[str, Any],
    *,
    upstream_missed: Iterable[str] = (),
) -> dict[str, Any]:
    """The planner covers every required action and traces each to a real finding.

    ``upstream_missed`` are defect ids the critics failed to catch on the previous
    fixture. An action answering one of those was never in the verdict the planner
    read, so it is reported as **unreachable**: the gap is real but it belongs to
    the critic that missed the defect, not to the planner that was never told.
    It still counts against the criterion - SC7 asks for coverage of all three -
    but naming it keeps the fix attributable to the stage that needs it.
    """
    if isinstance(plan, str):
        try:
            plan = json.loads(plan)
        except ValueError:
            return {"pass": False, "why": "plan is not JSON"}
    actions = (plan or {}).get("actions") or []
    # Separator-insensitive, for the same reason as SC5: a recogniser reading
    # "alfworld only" has to match a plan that writes "ALFWorld-only".
    blob = _squash(" ".join(f"{a.get('action', '')} {a.get('rationale', '')}" for a in actions))

    upstream = set(upstream_missed)
    covered, missing, unreachable = [], [], []
    for required in ground_truth["actions"]:
        if any(_squash(r) in blob for r in required.get("recognisers") or []):
            covered.append(required["id"])
            continue
        missing.append(required["id"])
        if required.get("answers") in upstream:
            unreachable.append(required["id"])

    ids = {f.get("id") for f in verdict.get("findings") or []}
    ids |= {f.get("origin_id") for f in verdict.get("findings") or [] if f.get("origin_id")}
    dangling = [a.get("traces_to") for a in actions if a.get("traces_to") not in ids]

    result = {
        "pass": not missing and not dangling,
        "covered": covered,
        "missing": missing,
        "dangling_traces_to": dangling,
        "actions": len(actions),
    }
    if unreachable:
        result["unreachable"] = unreachable
        result["why_unreachable"] = (
            "the verdict carried no finding for these; the critics missed the defect upstream"
        )
    return result


def render(score: Score) -> str:
    """A short human-readable block — this is what a person reads first."""
    d = score.as_dict()
    if not d["defects"]:
        # A criteria-only fixture (the planner has no defect list). Printing
        # "recall 0/0 (0%)" there reads as a failure when nothing was measured.
        lines = [f"fixture {d['fixture']}: no defect list — criteria only"]
        for name, result in (d["criteria"] or {}).items():
            mark = "PASS" if result.get("pass") else "FAIL"
            lines.append(
                f"  {name}: {mark}  {json.dumps({k: v for k, v in result.items() if k != 'pass'})}"
            )
        return "\n".join(lines)

    lines = [
        f"fixture {d['fixture']}: recall {d['caught']}/{d['defects']} ({d['recall']:.0%})"
        f"  precision {d['precision']:.0%}"
        f"  false-positives {d['false_positives']}  unmatched {d['unmatched']}",
        "  by defect_type: " + ", ".join(f"{k} {v}" for k, v in d["by_defect_type"].items()),
        "  joined by: " + (", ".join(f"{k}={v}" for k, v in d["by_join_strength"].items()) or "-"),
    ]
    if d["detail"]["missed"]:
        lines.append("  missed: " + ", ".join(d["detail"]["missed"]))
    for name, result in (d["criteria"] or {}).items():
        mark = "PASS" if result.get("pass") else "FAIL"
        lines.append(
            f"  {name}: {mark}  {json.dumps({k: v for k, v in result.items() if k != 'pass'})}"
        )
    return "\n".join(lines)


def load_ground_truth(path: Path | str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
    return loaded


def iter_findings(verdicts: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [f for v in verdicts for f in v.get("findings") or []]
