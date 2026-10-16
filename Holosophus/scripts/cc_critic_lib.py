"""Deterministic comparator for the result-critic loop.

Design: ``docs/plans/2026-08-18-self-improving-loop-verification.md`` (Approved).

Critics state findings; **only this module states a verdict**. It is plain
non-agent code on purpose: an agent that can render judgment can be argued into a
favourable one, and the status reconciliation below is a diff, not a judgment.

Responsibilities, in order:

1. **Validate** each critic report against the contract. A silent, short or
   crashed critic must surface as ``block``, never as an exception - so every
   structural problem becomes a recorded ``contract_error`` plus a block reason.
2. **Reconcile** this iteration's findings against the previous verdict, so each
   one carries ``new | recurring | resolved | unconfirmed-resolved``.
   ``resolved`` requires positive evidence (a re-derivation showing the value now
   matches); mere absence yields ``unconfirmed-resolved``, which the planner
   treats as still open.
3. **Decide** the verdict from the invariants in the spec's Data/Contracts
   section, per critic (summing coverage across critics would let one critic's
   overshoot mask another's shortfall).

MVP is advisory: the verdict is a signal a human reads, nothing is blocked.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

CRITICS = ("analysis", "paper", "sufficiency")
FINDING_CLASSES = ("mismatch", "unverified", "uninterpretable")
STATUSES = ("new", "recurring", "resolved", "unconfirmed-resolved")
OWNERS = ("mle-solver", "writer", "manager")

# Weakest cap wins when critics disagree; ``None`` means "not asserted".
CLAIM_CAP_ORDER = ("confident", "qualified", "inconclusive")

# Provisional. The spec lists the floor value as a deferred question - it is a
# knob here rather than a constant so tuning it never means editing the diff
# logic. Expressed as a fraction of ``coverage.claims_in``.
DEFAULT_DERIVED_FLOOR = 0.8

# Relative tolerance used only when the claimed literal carries no decimal
# precision to compare at. Published numbers are rounded, so the primary rule is
# "compare at the precision the claim was stated to" - see ``values_match``.
DEFAULT_FLOAT_EPSILON = 1e-9

VERDICT_FILENAME = "verdict_{iteration:03d}.json"


@dataclass(frozen=True)
class ContractError:
    """A structural problem in a critic report. Always accompanied by a block."""

    critic: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"critic": self.critic, "detail": self.detail}


@dataclass
class CriticReport:
    """One critic's output, already parsed. ``verdict`` is deliberately absent.

    Two critics fill this differently, because they are shown different things:

    * The **paper** critic sees claim and cited source together - matching them is
      its task - so it emits ``mismatch`` findings itself.
    * The **analysis** critic is blind to the claimed values by construction, so it
      cannot know a value is wrong. It emits ``derivations`` instead, and the
      comparator joins them against the runner's claim list. Findings it *can*
      raise unaided are ``unverified`` and ``uninterpretable``.
    """

    critic: str
    iteration: int
    claim_cap: str | None = None
    # Values are ints once validation passes; typed loosely so a malformed report
    # can still be carried into the verdict as a contract error rather than raise.
    coverage: dict[str, Any] = field(default_factory=dict)
    findings: list[dict[str, Any]] = field(default_factory=list)
    derivations: list[dict[str, Any]] = field(default_factory=list)
    rederivations: list[dict[str, Any]] = field(default_factory=list)
    # The sufficiency critic enumerates what it judged before judging it, so a
    # unit it never examined is visible rather than absent.
    units: list[dict[str, Any]] = field(default_factory=list)


# ── value comparison ──────────────────────────────────────────────────────────

_NUM_RE = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")


# A number as a critic actually writes one: thousands separators, a trailing
# percent sign, and usually some prose around it.
_EMBEDDED_RE = re.compile(r"[-+]?\d[\d,]*\.?\d*(?:[eE][-+]?\d+)?%?")


def _as_number(value: Any) -> float | None:
    """The numeric value of a critic's answer, however it chose to write it.

    Real reports do not hand back bare literals. Measured on the first live F3
    replay: 65 of 90 manufactured "mismatches" were the same number twice -
    ``2,994`` against ``2994``, ``98.8%`` against ``0.9878``, ``500 tasks per
    env`` against ``500 baseline tasks``. Comparing those as strings turns a
    working critic into 90 findings nobody can read.

    Takes the FIRST number in the text, because the claim_id already fixes which
    quantity is under discussion - what follows is commentary
    (``0.0147058... (~0.015)``), not a competing value.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if _NUM_RE.match(text):
        return float(text)
    match = _EMBEDDED_RE.search(text)
    if not match:
        return None
    token = match.group(0).replace(",", "")
    try:
        return float(token.rstrip("%")) / 100.0 if token.endswith("%") else float(token)
    except ValueError:
        return None


def _places_from_significant_figures(token: str) -> int | None:
    """Decimal places implied by a literal that states its precision in sig figs.

    ``1.2e-4`` is a claim about two significant figures, which is five decimal
    places at that magnitude. Reading scientific notation as stating no
    precision let the other side dictate it, so a claim of 1.2e-4 was measured
    against a record of 0.000122 at six places and called a disagreement.
    """
    mantissa = token.lower().split("e", 1)[0].lstrip("+-")
    digits = mantissa.replace(".", "").lstrip("0")
    value = _as_number(token)
    if not digits or value is None or value == 0:
        return None
    return len(digits) - 1 - math.floor(math.log10(abs(value)))


def _stated_places(value: Any) -> int | None:
    """Decimal places a value is stated to, or None if it states no rounding.

    Only a *fractional* literal states a precision. ``0.559`` says "somewhere in
    [0.5585, 0.5595)"; ``1`` says one, and reading it as "somewhere in [0.5, 1.5)"
    would make a claim of 1 agree with a derivation of 0.559. Counts are exact.

    Otherwise this covers the three ways a value arrives. A **float** counts via
    its repr: ground truth comes from JSON, where ``0.0147`` is a number and not
    the string ``"0.0147"``, yet it states four places either way. A
    **percentage** counts as the fraction it denotes, and always states a
    precision because writing one is itself a rounding: ``98.8%`` asserts 0.988,
    three places and not one. **Prose** counts by its number, so
    ``0.015 (best_arm_accuracy = 0.0147058...)`` is stated to three.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return None
    if isinstance(value, float):
        text = repr(value)
        if "e" in text or "." not in text:
            return None
        frac = text.split(".", 1)[1]
        return len(frac) if frac != "0" else None
    if not isinstance(value, str):
        return None
    match = _EMBEDDED_RE.search(value.strip())
    if not match:
        return None
    token = match.group(0).replace(",", "")
    if "e" in token.lower():
        return _places_from_significant_figures(token)
    percent = token.endswith("%")
    token = token.rstrip("%")
    digits = len(token.split(".", 1)[1]) if "." in token else 0
    return digits + 2 if percent else (digits or None)


_LABELLED_RE = re.compile(
    r"([A-Za-z_][A-Za-z0-9_.^\-]*)\s*[~]?[=:]\s*([-+]?\d[\d,]*\.?\d*(?:[eE][-+]?\d+)?%?)"
)


def _labelled(value: Any) -> dict[str, str]:
    """``label=value`` pairs in a text, lowercased by label."""
    if not isinstance(value, str):
        return {}
    # First mention wins. A composite answer qualifies its repeats - a table row
    # reading "N=201, ... bf N=182" is about 201, and letting the last write
    # through made the row compare on its footnote instead of on itself.
    # A trailing comma is the sentence's, not the number's; a thousands
    # separator sits between digits and survives.
    out: dict[str, str] = {}
    for key, number in _LABELLED_RE.findall(value):
        out.setdefault(key.casefold(), number.rstrip(","))
    return out


def _shared_labels(claimed: Any, derived: Any) -> list[tuple[str, str]]:
    """Value pairs the two sides both put a name to.

    A composite answer states several quantities at once, and the two sides do
    not order them the same way: a claim of ``N=616, rho=-0.133, CI[...]`` was
    compared against a derivation of ``rho=-0.132862, N=616, p_boot=0.001`` by
    taking the first number of each, so 616 was measured against -0.132862 and
    an exact agreement was reported as a mismatch. Where both sides name their
    quantities, compare like with like.

    Only shared labels are compared, so a defect living in a quantity one side
    never mentions is not caught here - a critic has to raise that as a finding
    of its own.
    """
    a, b = _labelled(claimed), _labelled(derived)
    return [(a[k], b[k]) for k in a.keys() & b.keys()]


def values_match(claimed: Any, derived: Any, epsilon: float = DEFAULT_FLOAT_EPSILON) -> bool:
    """Does a re-derived value agree with the claim?

    Numbers compare **at the coarser of the two stated precisions**, because
    either side may be the rounded one. A paper printing ``0.0147`` is satisfied
    by a derivation of ``0.014703`` and is not satisfied by ``0.559``; equally, a
    critic answering ``0.015`` has agreed with a record holding 0.0147058, and
    reading only the claim's precision called that a mismatch - which is how SC1
    failed on a critic that had found the fabrication exactly. ``epsilon`` is the
    relative fallback when neither side carries usable precision (scientific
    notation). Non-numeric values compare as whitespace-normalised, case-folded
    strings.
    """
    shared = _shared_labels(claimed, derived)
    if shared:
        return all(values_match(a, b, epsilon) for a, b in shared)

    # One side names its quantities and the other does not: a claim of
    # "succ 0.095, Delta +0.000[...]" against a derivation of "pooled test:
    # N=201, success=0.094527, ...". First-number-against-first-number measures
    # a success rate against a sample size, so ask instead whether the claimed
    # number is among the quantities the other side actually states.
    lone, named = (
        (claimed, _labelled(derived)) if _labelled(derived) else (derived, _labelled(claimed))
    )
    if named and not _labelled(lone) and _as_number(lone) is not None:
        # Only ever *adds* agreement. A parenthetical "(n=170)" beside a rate is
        # an annotation, not the quantity being reported, and returning False
        # here would have made "95.9% (ALF)" disagree with 0.95882 - the same
        # cross-quantity comparison this branch exists to prevent.
        if any(values_match(lone, stated, epsilon) for stated in named.values()):
            return True

    c, d = _as_number(claimed), _as_number(derived)
    if c is None or d is None:
        return (
            " ".join(str(claimed).split()).casefold() == " ".join(str(derived).split()).casefold()
        )

    stated = [p for p in (_stated_places(claimed), _stated_places(derived)) if p is not None]
    if stated:
        places = min(stated)
        return round(d, places) == round(c, places)
    return abs(d - c) <= epsilon * max(1.0, abs(c))


# ── validation ────────────────────────────────────────────────────────────────


def _finding_errors(critic: str, finding: dict[str, Any], index: int) -> list[ContractError]:
    where = f"findings[{index}]"
    errors: list[ContractError] = []
    if not finding.get("id"):
        errors.append(ContractError(critic, f"{where}: missing 'id'"))
    cls = finding.get("class")
    if cls not in FINDING_CLASSES:
        errors.append(ContractError(critic, f"{where}: class {cls!r} not in {FINDING_CLASSES}"))
    owner = finding.get("owner")
    if owner is not None and owner not in OWNERS:
        errors.append(ContractError(critic, f"{where}: owner {owner!r} not in {OWNERS}"))
    if cls == "mismatch":
        # A mismatch is an assertion that two values differ; without both, there
        # is nothing to check and nothing for the fix-list to act on.
        for key in ("claimed", "derived"):
            if finding.get(key) in (None, ""):
                errors.append(ContractError(critic, f"{where}: mismatch missing {key!r}"))
    return errors


def _as_count(value: Any) -> int | None:
    """A coverage counter, or None if it is not a usable non-negative int."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return int(value)


def _coverage_errors(critic: str, coverage: dict[str, Any]) -> list[ContractError]:
    errors: list[ContractError] = []
    for key in ("claims_in", "derived", "unverified"):
        if _as_count(coverage.get(key)) is None:
            errors.append(
                ContractError(
                    critic, f"coverage.{key} is {coverage.get(key)!r}, want a non-negative int"
                )
            )
    return errors


def parse_report(
    raw: Any, *, critic: str | None = None
) -> tuple[CriticReport | None, list[ContractError]]:
    """Coerce one critic's raw output into a ``CriticReport``.

    Never raises: a report that cannot be parsed comes back ``None`` with the
    reason, and the caller turns that into a block.
    """
    name = critic or "?"
    if isinstance(raw, (str, bytes)):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError) as exc:
            return None, [ContractError(name, f"report is not valid JSON: {exc}")]
    if not isinstance(raw, dict):
        return None, [ContractError(name, f"report is {type(raw).__name__}, want an object")]

    name = raw.get("critic") or name
    errors: list[ContractError] = []
    if name not in CRITICS:
        errors.append(ContractError(str(name), f"critic {name!r} not in {CRITICS}"))
    if critic is not None and raw.get("critic") not in (None, critic):
        errors.append(
            ContractError(
                str(name), f"report says critic={raw.get('critic')!r}, invoked as {critic!r}"
            )
        )
    if "verdict" in raw:
        # Not fatal - the comparator overwrites it - but worth surfacing, since a
        # critic emitting one has misread its role.
        errors.append(
            ContractError(str(name), "report carries a 'verdict'; critics do not render one")
        )

    cap = raw.get("claim_cap")
    if cap is not None and cap not in CLAIM_CAP_ORDER:
        errors.append(
            ContractError(str(name), f"claim_cap {cap!r} not in {CLAIM_CAP_ORDER} or null")
        )
        cap = None

    coverage = raw.get("coverage")
    if not isinstance(coverage, dict):
        errors.append(
            ContractError(str(name), f"coverage is {type(coverage).__name__}, want an object")
        )
        coverage = {}
    else:
        errors.extend(_coverage_errors(str(name), coverage))

    findings = raw.get("findings")
    if findings is None:
        findings = []
    elif not isinstance(findings, list):
        errors.append(
            ContractError(str(name), f"findings is {type(findings).__name__}, want a list")
        )
        findings = []
    for i, finding in enumerate(findings):
        if not isinstance(finding, dict):
            errors.append(
                ContractError(
                    str(name), f"findings[{i}] is {type(finding).__name__}, want an object"
                )
            )
            continue
        errors.extend(_finding_errors(str(name), finding, i))

    lists: dict[str, list[dict[str, Any]]] = {}
    for key in ("derivations", "rederivations"):
        value = raw.get(key) or []
        if not isinstance(value, list):
            errors.append(ContractError(str(name), f"{key} is {type(value).__name__}, want a list"))
            value = []
        for i, entry in enumerate(value):
            if isinstance(entry, dict) and not entry.get("claim_id"):
                errors.append(ContractError(str(name), f"{key}[{i}]: missing 'claim_id'"))
        lists[key] = [e for e in value if isinstance(e, dict)]

    iteration = raw.get("iteration")
    if not isinstance(iteration, int) or isinstance(iteration, bool):
        errors.append(ContractError(str(name), f"iteration is {iteration!r}, want an int"))
        iteration = -1

    report = CriticReport(
        critic=str(name),
        iteration=iteration,
        claim_cap=cap,
        coverage={k: coverage.get(k) for k in ("claims_in", "derived", "unverified")},
        findings=[f for f in findings if isinstance(f, dict)],
        derivations=lists["derivations"],
        rederivations=lists["rederivations"],
        units=[u for u in (raw.get("units") or []) if isinstance(u, dict)],
    )
    return report, errors


# ── panels ────────────────────────────────────────────────────────────────────


# Values critics use to mean "this finding is not about a specific claim". They
# identify nothing, so they cannot establish that two models found the same thing.
_UNIQUE = "<this-finding-only>"
"""Sentinel key for a finding that must never merge with any other."""

_PLACEHOLDER_CLAIM_IDS = {"", "n/a", "na", "none", "null", "-", "--", "unknown", "various"}


def _agreement_key(finding: dict[str, Any]) -> tuple[Any, ...] | None:
    """What two models must share for a finding to count as the same finding.

    ``None`` when the finding names no claim: without an identifying claim_id
    there is no way to tell agreement from coincidence, and a panel that guesses
    wrong loses a finding. Failing to notice agreement costs only a statistic.
    """
    claim_id = str(finding.get("claim_id") or "").strip().casefold()
    if claim_id in _PLACEHOLDER_CLAIM_IDS:
        return None
    return (claim_id, str(finding.get("class") or ""))


def merge_reports(reports: Sequence[Any], *, models: Sequence[str] | None = None) -> dict[str, Any]:
    """Union the findings of several models that ran the SAME critique.

    The recorded 2026-08-17 audit was a panel: Opus and Fable ran the same
    critique independently and converged on 11 of 13 defects, with two caught by
    Fable alone. Diversity is what bought those two, so a panel unions rather
    than intersects — an intersection would have thrown them away.

    Merging happens here, before the comparator, so the comparator keeps its
    one-report-per-critic assumption and stays a pure diff.

    Coverage is taken from the best single member rather than summed or averaged:
    it answers "did the critique report on every claim in the brief", both members
    got the same brief, and summing would double every count. Complete coverage
    with the most derived wins.
    """
    parsed: list[CriticReport] = []
    tags: list[str] = []
    for i, raw in enumerate(reports):
        report, _ = parse_report(raw)
        if report is not None:
            parsed.append(report)
            tags.append(models[i] if models and i < len(models) else f"model{i}")
    if not parsed:
        return {}

    def completeness(r: CriticReport) -> tuple[int, int]:
        counts = [_as_count(r.coverage.get(k)) for k in ("claims_in", "derived", "unverified")]
        if any(c is None for c in counts):
            return (0, 0)
        claims_in, derived, unverified = counts[0] or 0, counts[1] or 0, counts[2] or 0
        return (1 if derived + unverified == claims_in else 0, derived)

    best = max(parsed, key=completeness)

    findings: list[dict[str, Any]] = []
    seen: dict[tuple[Any, ...], dict[str, Any]] = {}
    for report, tag in zip(parsed, tags):
        local: set[tuple[Any, ...]] = set()
        for index, finding in enumerate(report.findings):
            key = _agreement_key(finding)
            # Two findings from the SAME critic are two findings, whatever they
            # are keyed on. Only a second *model* saying the same thing is
            # agreement. Merging within a report cost real findings: the
            # sufficiency critic ids every finding "n/a", so its three collapsed
            # to two and one model appeared twice in `found_by`.
            if key is None or key in local:
                key = (_UNIQUE, tag, index)
            local.add(key)
            if key in seen:
                # Same defect, independently found. That agreement is signal:
                # the two defects the panel exists for were single-model catches.
                found_by = seen[key].setdefault("found_by", [])
                if tag not in found_by:
                    found_by.append(tag)
                continue
            merged = {**finding, "found_by": [tag]}
            seen[key] = merged
            findings.append(merged)

    def union_units() -> list[dict[str, Any]]:
        """The enumeration, unioned by unit_id. Dropping it would hide the very
        thing it exists to show: which units were examined at all."""
        out: dict[str, dict[str, Any]] = {}
        for report in parsed:
            for entry in report.units:
                unit_id = str(entry.get("unit_id") or "")
                if unit_id and unit_id not in out:
                    out[unit_id] = entry
        return list(out.values())

    def union_by_claim(attr: str) -> list[dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for report in parsed:
            for entry in getattr(report, attr):
                cid = str(entry.get("claim_id") or "")
                if cid and cid not in out:
                    out[cid] = entry
        return list(out.values())

    return {
        "critic": best.critic,
        "iteration": best.iteration,
        "claim_cap": _weakest_cap(r.claim_cap for r in parsed),
        "coverage": best.coverage,
        "findings": findings,
        "units": union_units(),
        "derivations": union_by_claim("derivations"),
        "rederivations": union_by_claim("rederivations"),
        "panel": {
            "models": tags,
            "per_model_findings": {t: len(r.findings) for r, t in zip(parsed, tags)},
            "agreed": sum(1 for f in findings if len(f.get("found_by") or []) > 1),
            "single_model": sum(1 for f in findings if len(f.get("found_by") or []) == 1),
            "coverage_from": tags[parsed.index(best)],
        },
    }


# ── the blind-derivation join ─────────────────────────────────────────────────


def derive_mismatches(
    report: CriticReport,
    claims: dict[str, dict[str, Any]],
    *,
    epsilon: float = DEFAULT_FLOAT_EPSILON,
) -> tuple[list[dict[str, Any]], list[ContractError]]:
    """Turn a blind critic's derivations into ``mismatch`` findings.

    The analysis critic is never shown the claimed values, so it cannot report a
    mismatch - it reports what the records actually say. Diffing that against the
    claim list the runner assembled is this function's job, and doing it here
    rather than in a prompt is what makes the comparison non-negotiable.

    Ids are deterministic (``<critic>:<claim_id>``) so the same defect keeps the
    same identity across iterations and ``reconcile`` can follow its lineage.
    """
    findings: list[dict[str, Any]] = []
    errors: list[ContractError] = []
    for entry in report.derivations:
        claim_id = str(entry.get("claim_id"))
        claim = claims.get(claim_id)
        if claim is None:
            errors.append(
                ContractError(report.critic, f"derived {claim_id!r}, which was not in the brief")
            )
            continue
        claimed, derived = claim.get("claimed"), entry.get("derived")
        if derived in (None, ""):
            errors.append(
                ContractError(report.critic, f"derivation for {claim_id!r} carries no value")
            )
            continue
        if values_match(claimed, derived, epsilon):
            continue
        findings.append(
            {
                "id": f"{report.critic}:{claim_id}",
                "class": "mismatch",
                "owner": claim.get("owner"),
                "claim_id": claim_id,
                "claimed": claimed,
                "derived": derived,
                "records": entry.get("records") or [],
                "fix": entry.get("fix")
                or f"Reconcile the published value with what {', '.join(entry.get('records') or ['the records'])} contains.",
                "raised_by": "comparator",
            }
        )
    return findings, errors


# ── status reconciliation ─────────────────────────────────────────────────────


def _primary_key(finding: dict[str, Any]) -> tuple[str, str]:
    return (str(finding.get("claim_id") or ""), str(finding.get("class") or ""))


def _fallback_keys(finding: dict[str, Any]) -> list[tuple[str, str]]:
    """``class`` + source file, for when a restructure moves the ``claim_id``."""
    records = finding.get("records") or []
    if not isinstance(records, list):
        return []
    return [(str(finding.get("class") or ""), str(r)) for r in records if r]


def _rederivation_index(reports: Sequence[CriticReport]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for report in reports:
        for entry in report.rederivations:
            claim_id = entry.get("claim_id")
            if claim_id:
                index[str(claim_id)] = entry
    return index


def _is_confirmed_fixed(entry: dict[str, Any] | None, epsilon: float) -> bool:
    """Does a re-derivation record positively show the value now matches?

    The comparator recomputes the match rather than trusting the critic's own
    ``matches`` flag - the flag is honoured only when the values are absent or
    non-numeric, where there is nothing to recompute from.
    """
    if not entry:
        return False
    claimed, derived = entry.get("claimed"), entry.get("derived")
    if claimed in (None, "") or derived in (None, ""):
        return bool(entry.get("matches"))
    return values_match(claimed, derived, epsilon)


def reconcile(
    findings: list[dict[str, Any]],
    previous_findings: Sequence[dict[str, Any]],
    rederivations: dict[str, dict[str, Any]],
    *,
    epsilon: float = DEFAULT_FLOAT_EPSILON,
) -> list[dict[str, Any]]:
    """Assign ``status`` to this iteration's findings and carry closed ones over.

    Returns current findings (``new`` / ``recurring``) followed by carried-over
    entries for previous findings nobody reported this time (``resolved`` when a
    re-derivation proves the fix, else ``unconfirmed-resolved``).

    A recurring finding keeps the id its critic gave it and gains ``origin_id``,
    the id it carried the first time it was seen. Without that the status says a
    defect recurred but nothing says *which* defect, and a lineage cannot be
    followed across more than one iteration.
    """
    by_primary: dict[tuple[str, str], int] = {}
    by_fallback: dict[tuple[str, str], int] = {}
    for i, prev in enumerate(previous_findings):
        by_primary.setdefault(_primary_key(prev), i)
        for key in _fallback_keys(prev):
            by_fallback.setdefault(key, i)

    matched: set[int] = set()
    out: list[dict[str, Any]] = []
    for finding in findings:
        hit = by_primary.get(_primary_key(finding))
        if hit is None:
            for key in _fallback_keys(finding):
                hit = by_fallback.get(key)
                if hit is not None:
                    break
        if hit is None:
            out.append({**finding, "status": "new"})
            continue
        matched.add(hit)
        prev = previous_findings[hit]
        out.append(
            {
                **finding,
                "status": "recurring",
                "origin_id": prev.get("origin_id") or prev.get("id"),
            }
        )

    for i, prev in enumerate(previous_findings):
        if i in matched or prev.get("status") == "resolved":
            continue
        claim_id = str(prev.get("claim_id") or "")
        entry = rederivations.get(claim_id)
        confirmed = _is_confirmed_fixed(entry, epsilon)
        carried = {**prev, "status": "resolved" if confirmed else "unconfirmed-resolved"}
        if confirmed and entry is not None:
            carried["rederivation"] = entry
        out.append(carried)
    return out


# ── verdict ───────────────────────────────────────────────────────────────────


def _weakest_cap(caps: Iterable[str | None]) -> str | None:
    present = [c for c in caps if c in CLAIM_CAP_ORDER]
    return max(present, key=CLAIM_CAP_ORDER.index) if present else None


def compare(
    reports: Sequence[Any],
    *,
    iteration: int,
    claims: dict[str, dict[str, Any]] | None = None,
    previous: dict[str, Any] | None = None,
    expected_critics: Sequence[str] | None = None,
    derived_floor: float = DEFAULT_DERIVED_FLOOR,
    epsilon: float = DEFAULT_FLOAT_EPSILON,
) -> dict[str, Any]:
    """Diff critic reports into a verdict. Pure: no I/O, no model calls.

    ``claims`` is the claim list the runner assembled - ``claim_id`` mapped to at
    least ``claimed`` and ``owner``. It is what a blind critic's derivations are
    joined against; omit it and only critic-raised findings are considered.

    ``verdict`` is ``block`` when, for any critic, (a) a finding of class
    ``mismatch`` is open (anything but ``resolved``), (b) coverage does not
    account for every claim the runner put in the brief, or (c) the derived
    share falls below ``derived_floor``. A structurally broken or missing report
    also blocks - it is indistinguishable from a critic that crashed.

    An empty ``findings`` with complete coverage is a **pass**.
    """
    parsed: list[CriticReport] = []
    errors: list[ContractError] = []
    for raw in reports:
        report, report_errors = parse_report(raw)
        errors.extend(report_errors)
        if report is not None:
            parsed.append(report)

    seen = {r.critic for r in parsed}
    for name in expected_critics or ():
        if name not in seen:
            errors.append(ContractError(name, "no report - critic did not run or produced nothing"))

    for report in parsed:
        if report.iteration not in (-1, iteration):
            errors.append(
                ContractError(
                    report.critic,
                    f"report iteration {report.iteration} != run iteration {iteration}",
                )
            )

    raised: list[dict[str, Any]] = []
    for report in parsed:
        raised.extend(report.findings)
        joined, join_errors = derive_mismatches(report, claims or {}, epsilon=epsilon)
        raised.extend(joined)
        errors.extend(join_errors)

    per_critic: dict[str, dict[str, Any]] = {}
    reasons: list[str] = []
    for report in parsed:
        cov = report.coverage
        counts = [_as_count(cov.get(k)) for k in ("claims_in", "derived", "unverified")]
        critic_reasons: list[str] = []
        if any(c is None for c in counts):
            critic_reasons.append("coverage unusable")
        else:
            claims_in, derived, unverified = counts[0] or 0, counts[1] or 0, counts[2] or 0
            if derived + unverified != claims_in:
                critic_reasons.append(
                    f"incomplete coverage: derived {derived} + unverified {unverified} != claims_in {claims_in}"
                )
            elif claims_in and derived < derived_floor * claims_in:
                critic_reasons.append(
                    f"derived {derived}/{claims_in} below floor {derived_floor:.0%}"
                )
        per_critic[report.critic] = {
            "coverage": cov,
            "claim_cap": report.claim_cap,
            "findings": len(report.findings),
            "derivations": len(report.derivations),
            "reasons": critic_reasons,
        }
        reasons.extend(f"{report.critic}: {r}" for r in critic_reasons)

    findings = reconcile(
        raised,
        (previous or {}).get("findings") or [],
        _rederivation_index(parsed),
        epsilon=epsilon,
    )

    open_mismatches = [
        f for f in findings if f.get("class") == "mismatch" and f.get("status") != "resolved"
    ]
    for finding in open_mismatches:
        reasons.append(
            f"open mismatch {finding.get('id')} ({finding.get('status')}) on {finding.get('claim_id')}: "
            f"claimed {finding.get('claimed')!r}, derived {finding.get('derived')!r}"
        )
    reasons.extend(f"contract: {e.critic}: {e.detail}" for e in errors)

    total = {"claims_in": 0, "derived": 0, "unverified": 0}
    for report in parsed:
        for key in total:
            total[key] += _as_count(report.coverage.get(key)) or 0

    return {
        "iteration": iteration,
        "verdict": "block" if reasons else "pass",
        "reasons": reasons,
        "claim_cap": _weakest_cap(r.claim_cap for r in parsed),
        "coverage": total,
        "per_critic": per_critic,
        "findings": findings,
        "contract_errors": [e.as_dict() for e in errors],
    }


# ── persistence ───────────────────────────────────────────────────────────────


def verdict_path(verdict_dir: Path, iteration: int) -> Path:
    return Path(verdict_dir) / VERDICT_FILENAME.format(iteration=iteration)


def write_verdict(verdict_dir: Path, verdict: dict[str, Any]) -> Path:
    """Persist a verdict. Verdicts accumulate in sequence; nothing is overwritten."""
    path = verdict_path(verdict_dir, int(verdict["iteration"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(verdict, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def load_previous_verdict(verdict_dir: Path, iteration: int) -> dict[str, Any] | None:
    """The most recent verdict strictly before ``iteration``, or None."""
    directory = Path(verdict_dir)
    if not directory.is_dir():
        return None
    candidates: list[tuple[int, Path]] = []
    for path in directory.glob("verdict_*.json"):
        match = re.fullmatch(r"verdict_(\d+)", path.stem)
        if match and int(match.group(1)) < iteration:
            candidates.append((int(match.group(1)), path))
    if not candidates:
        return None
    _, newest = max(candidates)
    try:
        loaded = json.loads(newest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None
