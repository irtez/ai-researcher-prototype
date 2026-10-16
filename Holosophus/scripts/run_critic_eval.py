#!/usr/bin/env python3
"""Replay the result critics against a frozen fixture and score them.

Design: ``docs/plans/2026-08-18-self-improving-loop-verification.md`` (Approved).
This is the spec's Validation step 4 and its ADR-0004 substitute evidence.

**This does not re-run any research.** The runs finished in August; the fixtures
under ``holosophos/evals_and_reports/data/critics/`` are their frozen artifacts.
The critics are pointed at those artifacts and their output is scored against
ground truth transcribed from two independent audits. Old runs are the only ones
where the answers are known, so they are the only ones on which recall can be
measured rather than assumed.

What each fixture proves::

    F3  paper + analysis critics   SC1, SC2, SC3   wrong numbers are caught
    F4  sufficiency critic         SC5             clean-but-insufficient is caught
    F5  planner (on F4's verdict)  SC7             the next iteration is drafted
    FG  cross-domain holdout       -               not overfitted to one study

Start with ``--dry-run``: it builds every brief and runs the comparator and
scorer against canned reports, spending nothing. Every plumbing bug shows up
there rather than after an hour of Opus calls.

Usage::

    python scripts/run_critic_eval.py --fixture f3 --dry-run
    python scripts/run_critic_eval.py --fixture f3 --model sonnet     # shake-out
    python scripts/run_critic_eval.py --fixture f3 --model opus       # recorded
    python scripts/run_critic_eval.py --fixture f3 --model opus --panel-model fable
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

import cc_critic_lib as ccl  # noqa: E402
import cc_critic_runner as ccr  # noqa: E402
import cc_critic_score as ccs  # noqa: E402

FIXTURES = PROJECT_DIR / "holosophos" / "evals_and_reports" / "data" / "critics"
DEFAULT_OUT = PROJECT_DIR / "workdir" / "critic_eval"


@dataclass
class Fixture:
    name: str
    root: Path
    ground_truth: Path
    task: Path | None = None
    write_up: Path | None = None
    records: Path | None = None
    critics: tuple[str, ...] = ()
    notes: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def load_fixture(name: str) -> Fixture:
    root = FIXTURES / name
    if name == "f3":
        return Fixture(
            name="f3",
            root=root,
            ground_truth=FIXTURES / "f3_defects.json",
            write_up=root / "paper" / "main.tex",
            records=root / "mle" / "results",
            critics=("paper", "analysis"),
            notes="iteration 3: a fabricated headline number plus 12 further audited defects",
        )
    if name == "f4":
        return Fixture(
            name="f4",
            root=root,
            ground_truth=FIXTURES / "f4_defects.json",
            task=FIXTURES / "f5" / "task.md",
            write_up=root / "paper" / "main.pdf",
            records=root / "mle" / "results",
            critics=("sufficiency",),
            notes="iteration 4: numerically self-audit-clean and still under-powered",
        )
    if name == "f5":
        return Fixture(
            name="f5",
            root=root,
            ground_truth=FIXTURES / "f5_actions.json",
            task=root / "task.md",
            records=root / "mle" / "results",
            critics=(),
            notes="planner: drafts iteration 5 from the verdict on iteration 4",
            extra={"answer_key": root / "brief.md"},
        )
    if name == "fg":
        return Fixture(
            name="fg",
            root=root,
            ground_truth=FIXTURES / "f3_defects.json",  # unused; FG has no adjudication yet
            task=root / "task.md",
            write_up=root / "writeup" / "research_protocol.md",
            records=root / "mle" / "results",
            critics=("paper", "analysis", "sufficiency"),
            notes="cross-domain holdout: legal retrieval, markdown write-up, different record shapes",
        )
    raise SystemExit(f"unknown fixture {name!r}; expected one of f3, f4, f5, fg")


# ── canned reports for --dry-run ──────────────────────────────────────────────


def canned(critic: str, fixture: Fixture, claims: list[ccr.Claim]) -> dict[str, Any]:
    """A plausible report, so --dry-run exercises the whole chain for free.

    Deliberately imperfect: it reports the SC1 fabrication and nothing else, so a
    dry run also demonstrates the scorer distinguishing a partial result from a
    good one instead of printing a reassuring 100%.
    """
    gt = ccs.load_ground_truth(fixture.ground_truth)
    if critic == "paper":
        target = next((d for d in gt.get("defects", []) if d.get("is_sc1_target")), None)
        enumerated = [
            {
                "claim_id": d["claim_id"],
                "claimed": d.get("claimed") or "1",
                "cited_sources": [d["source"]] if d.get("source") else ["mle/results/"],
                "description": f"quantity for {d['claim_id']}",
            }
            for d in gt.get("defects", [])
            if d.get("source")
        ]
        findings = []
        if target:
            findings.append(
                {
                    "id": "dry-sc1",
                    "class": "mismatch",
                    "owner": "writer",
                    "claim_id": target["claim_id"],
                    "claimed": target["claimed"],
                    "derived": target["true_value"],
                    "records": [target["source"]],
                    "fix": "report what the cited source records",
                }
            )
        n = len(enumerated)
        return {
            "critic": "paper",
            "iteration": 3,
            "claim_cap": "qualified",
            "coverage": {"claims_in": n, "derived": n, "unverified": 0},
            "claims": enumerated,
            "findings": findings,
        }
    if critic == "analysis":
        n = len(claims)
        return {
            "critic": "analysis",
            "iteration": 3,
            "claim_cap": None,
            "coverage": {"claims_in": n, "derived": n, "unverified": 0},
            "derivations": [
                {"claim_id": c.claim_id, "derived": c.claimed or "0", "records": ["mle/results/"]}
                for c in claims
            ],
            "findings": [],
        }
    defects = gt.get("sufficiency_defects", [])
    target = next((d for d in defects if d.get("is_sc5_target")), None)
    return {
        "critic": "sufficiency",
        "iteration": 4,
        "claim_cap": "inconclusive",
        "coverage": {"claims_in": 3, "derived": 3, "unverified": 0},
        "findings": (
            [
                {
                    "id": "dry-sc5",
                    "class": "unverified",
                    "owner": "manager",
                    "claim_id": target["claim_id"],
                    "records": [target.get("source", "mle/results/")],
                    "fix": target.get("fix", "raise n"),
                }
            ]
            if target
            else []
        ),
    }


# ── invocation ────────────────────────────────────────────────────────────────


async def invoke(
    brief: ccr.CriticBrief,
    models: list[str],
    *,
    dry: bool,
    canned_report: dict[str, Any],
    max_usd: float | None = None,
) -> Any:
    """Run one critique, once per model, and merge if there is more than one."""
    if dry:
        return canned_report
    reports = []
    for model in models:
        print(f"  {brief.critic} critic on {model} ...", flush=True)
        report = await ccr.run_critic(brief, model=model, max_usd=max_usd)
        n = len(report.get("findings") or []) if isinstance(report, dict) else "unparsed"
        print(f"    returned {n} findings", flush=True)
        reports.append(report)
    if len(reports) == 1:
        return reports[0]
    return ccl.merge_reports(reports, models=models)


async def replay_f3(
    fixture: Fixture, models: list[str], dry: bool, max_usd: float | None = None
) -> dict[str, Any]:
    """Paper critic enumerates and matches; the analysis critic then derives blind."""
    session = str(fixture.root)

    paper_brief = ccr.build_paper_brief(
        [], iteration=3, session_dir=session, paper_path=str(fixture.write_up)
    )
    paper_report = await invoke(
        paper_brief, models, dry=dry, canned_report=canned("paper", fixture, []), max_usd=max_usd
    )

    # The seam: the paper critic's enumeration becomes the blind critic's task,
    # stripped of every value it just read.
    claims = ccr.claims_from_paper_report(paper_report)
    if not claims:
        # Carry the report out with the error. A run that fails here and saves
        # nothing is undiagnosable: the whole question is what the critic said.
        return {
            "error": "the paper critic enumerated no claims; the analysis critic has nothing to derive",
            "reports": [paper_report],
        }

    analysis_brief = ccr.build_analysis_brief(claims, iteration=3, session_dir=session)
    analysis_report = await invoke(
        analysis_brief,
        models,
        dry=dry,
        canned_report=canned("analysis", fixture, claims),
        max_usd=max_usd,
    )

    verdict = ccl.compare(
        [paper_report, analysis_report],
        iteration=3,
        claims={c.claim_id: {"claimed": c.claimed, "owner": c.owner} for c in claims},
        expected_critics=["paper", "analysis"],
    )
    return {"verdict": verdict, "reports": [paper_report, analysis_report], "claims": len(claims)}


async def replay_f4(
    fixture: Fixture, models: list[str], dry: bool, max_usd: float | None = None
) -> dict[str, Any]:
    task = (
        fixture.task.read_text(encoding="utf-8") if fixture.task and fixture.task.is_file() else ""
    )
    # One member enumerates the units; the rest judge that same list. Two models
    # enumerating independently invent different ids for the same thing, which is
    # why the first panel run recorded zero agreement across ten overlapping
    # findings. Enumerating once makes agreement measurable.
    brief = ccr.build_sufficiency_brief([], iteration=4, session_dir=str(fixture.root), task=task)
    first = await invoke(
        brief, models[:1], dry=dry, canned_report=canned("sufficiency", fixture, []),
        max_usd=max_usd,
    )
    reports = [first]
    units = ccr.units_from_sufficiency_report(first)
    for model in models[1:]:
        pinned = ccr.build_sufficiency_brief(
            units, iteration=4, session_dir=str(fixture.root), task=task
        )
        reports.append(
            await invoke(
                pinned, [model], dry=dry, canned_report=canned("sufficiency", fixture, []),
                max_usd=max_usd,
            )
        )
    report = reports[0] if len(reports) == 1 else ccl.merge_reports(reports, models=models)
    verdict = ccl.compare([report], iteration=4, expected_critics=["sufficiency"])
    return {"verdict": verdict, "reports": [report], "claims": len(units)}


async def replay_f5(
    fixture: Fixture, models: list[str], dry: bool, verdict: dict[str, Any]
) -> dict[str, Any]:
    task = (
        fixture.task.read_text(encoding="utf-8") if fixture.task and fixture.task.is_file() else ""
    )
    brief = ccr.build_planner_brief(verdict, task=task, session_dir=str(fixture.root))
    if dry:
        gt = ccs.load_ground_truth(fixture.ground_truth)
        ids = [f.get("id") for f in verdict.get("findings") or []] or ["F1"]
        plan = {
            "iteration": 5,
            "summary": "dry run",
            "actions": [
                {"action": a["action"], "rationale": a["intent"], "traces_to": ids[i % len(ids)]}
                for i, a in enumerate(gt["actions"])
            ],
        }
    else:
        plan = await ccr.run_critic(brief, model=models[0])
    return {"plan": plan, "verdict": verdict}


# ── re-scoring saved critic output ────────────────────────────────────────────


def upstream_missed_beside(verdict_path: Path) -> list[str]:
    """Defect ids the critics missed on the run that produced this verdict.

    Read from the score written beside it. An action answering one of these was
    never in the verdict the planner read, so the gap belongs to the critic that
    missed the defect and not to the planner.
    """
    sibling = verdict_path.parent / "score.json"
    if not sibling.is_file():
        return []
    prior = json.loads(sibling.read_text(encoding="utf-8"))
    return list((prior.get("detail") or {}).get("missed") or [])


def replay_from_reports(fixture: Fixture, path: Path) -> dict[str, Any]:
    """Re-run the comparator and scorer over reports a live run already paid for.

    The model calls are the expensive part and their output is the evidence; the
    comparator and the scorer are free and change often. Every time one of them
    was fixed after a live run, the only way to see the effect was to buy the
    same critic output again -- and buy a *different* sample of it, since the
    critics are not deterministic, which confounds the very comparison being
    made. Reading the saved reports back keeps the evidence fixed while the
    instrument moves.
    """
    reports = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(reports, list):
        reports = [reports]

    if fixture.name in ("f3", "fg"):
        paper = next((r for r in reports if r.get("critic") == "paper"), None)
        if paper is None:
            return {"error": f"{path} holds no paper report", "reports": reports}
        claims = ccr.claims_from_paper_report(paper)
        verdict = ccl.compare(
            reports,
            iteration=3,
            claims={c.claim_id: {"claimed": c.claimed, "owner": c.owner} for c in claims},
            expected_critics=["paper", "analysis"],
        )
        return {"verdict": verdict, "reports": reports, "claims": len(claims)}

    verdict = ccl.compare(reports, iteration=4, expected_critics=["sufficiency"])
    return {"verdict": verdict, "reports": reports, "claims": 0}


# ── scoring + reporting ───────────────────────────────────────────────────────


def score(fixture: Fixture, outcome: dict[str, Any]) -> ccs.Score:
    gt = ccs.load_ground_truth(fixture.ground_truth)
    verdict = outcome.get("verdict") or {}

    if fixture.name == "f3":
        result = ccs.score_defects(verdict, gt, fixture="F3")
        allowed = int((gt.get("scoring") or {}).get("sc3_allowed_failures", 1))
        result.criteria = {
            "SC1": ccs.check_sc1(verdict, gt),
            "SC2": ccs.check_sc2(result, gt),
            "SC3": ccs.check_sc3(verdict, fixture.root, allowed_failures=allowed),
        }
        return result
    if fixture.name == "f4":
        result = ccs.score_defects(verdict, gt, fixture="F4", defect_key="sufficiency_defects")
        result.criteria = {"SC5": ccs.check_sc5(verdict, gt)}
        return result
    if fixture.name == "f5":
        result = ccs.Score(fixture="F5")
        result.criteria = {
            "SC7": ccs.check_sc7(
                outcome.get("plan"),
                outcome["verdict"],
                gt,
                upstream_missed=outcome.get("upstream_missed") or (),
            )
        }
        return result
    result = ccs.Score(fixture=fixture.name.upper())
    result.criteria = {
        "note": {"pass": True, "why": "holdout has no adjudication yet; inspect by hand"}
    }
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    ap.add_argument("--fixture", required=True, choices=["f3", "f4", "f5", "fg"])
    ap.add_argument("--model", default="opus", help="model for every critic (default: opus)")
    ap.add_argument(
        "--panel-model",
        help="second model family running the SAME critique; findings are unioned, not intersected",
    )
    ap.add_argument(
        "--dry-run", action="store_true", help="build briefs, use canned reports, spend nothing"
    )
    ap.add_argument("--verdict", type=Path, help="f5 only: the iteration-4 verdict to plan from")
    ap.add_argument(
        "--from-reports",
        type=Path,
        metavar="REPORTS.JSON",
        help="re-score saved critic output instead of calling any model (free, and holds "
        "the evidence fixed while the comparator changes)",
    )
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument(
        "--max-usd",
        type=float,
        default=8.0,
        help="hard per-critic spend cap passed to the SDK (default: 8.0)",
    )
    args = ap.parse_args()

    fixture = load_fixture(args.fixture)
    models = [args.model] + ([args.panel_model] if args.panel_model else [])
    # Canned dry-run output must never overwrite live evidence: a dry run costs
    # nothing and is repeated constantly, a live run costs money and is the record.
    out_dir = args.out / (f"{fixture.name}-dry" if args.dry_run else fixture.name)

    for label, path in (("root", fixture.root), ("records", fixture.records)):
        if path and not path.exists():
            print(f"fixture {fixture.name} has no {label} at {path}", file=sys.stderr)
            print(
                "build it: python scripts/build_critic_fixtures.py --session <pulled-session>",
                file=sys.stderr,
            )
            return 1

    print(f"fixture   {fixture.name.upper()}  — {fixture.notes}")
    print(
        f"models    {', '.join(models)}{'  (PANEL: findings unioned)' if len(models) > 1 else ''}"
    )
    if args.from_reports:
        mode = f"RE-SCORE — saved critic output from {args.from_reports}, no model calls"
    elif args.dry_run:
        mode = "DRY RUN — no model calls, nothing spent"
    else:
        mode = "LIVE"
    print(f"mode      {mode}")
    print()

    cap = None if args.dry_run else args.max_usd
    if args.from_reports:
        if not args.from_reports.is_file():
            print(f"no saved output at {args.from_reports}", file=sys.stderr)
            return 1
        if fixture.name == "f5":
            # f5 saves a plan rather than critic reports, and scoring one needs
            # the verdict it was drafted from.
            if not (args.verdict and args.verdict.is_file()):
                print("re-scoring f5 needs --verdict as well as the saved plan", file=sys.stderr)
                return 1
            outcome = {
                "plan": json.loads(args.from_reports.read_text(encoding="utf-8")),
                "verdict": json.loads(args.verdict.read_text(encoding="utf-8")),
                "upstream_missed": upstream_missed_beside(args.verdict),
            }
        else:
            outcome = replay_from_reports(fixture, args.from_reports)
    elif fixture.name == "f3":
        outcome = asyncio.run(replay_f3(fixture, models, args.dry_run, cap))
    elif fixture.name == "f4":
        outcome = asyncio.run(replay_f4(fixture, models, args.dry_run, cap))
    elif fixture.name == "f5":
        upstream_missed: list[str] = []
        if args.verdict and args.verdict.is_file():
            prior = json.loads(args.verdict.read_text(encoding="utf-8"))
            upstream_missed = upstream_missed_beside(args.verdict)
        else:
            print("f5 plans from an iteration-4 verdict; running f4 first to produce one\n")
            f4 = load_fixture("f4")
            prior_outcome = asyncio.run(replay_f4(f4, models, args.dry_run, cap))
            prior = prior_outcome["verdict"]
            upstream_missed = [
                m.defect_id for m in score(f4, prior_outcome).matches if not m.caught
            ]
        outcome = asyncio.run(replay_f5(fixture, models, args.dry_run, prior))
        outcome["upstream_missed"] = upstream_missed
    else:
        outcome = asyncio.run(replay_f3(fixture, models, args.dry_run, cap))

    if "error" in outcome:
        print(f"FAILED: {outcome['error']}", file=sys.stderr)
        if outcome.get("reports"):
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / "reports.json"
            path.write_text(
                json.dumps(outcome["reports"], indent=2, ensure_ascii=False, default=str) + "\n",
                encoding="utf-8",
            )
            print(f"what the critic actually returned: {path}", file=sys.stderr)
        return 1

    result = score(fixture, outcome)
    print(ccs.render(result))

    out = out_dir
    out.mkdir(parents=True, exist_ok=True)
    (out / "score.json").write_text(json.dumps(result.as_dict(), indent=2) + "\n", encoding="utf-8")
    if outcome.get("verdict"):
        ccl.write_verdict(out, outcome["verdict"])
    for key in ("reports", "plan"):
        if outcome.get(key) is not None:
            (out / f"{key}.json").write_text(
                json.dumps(outcome[key], indent=2, ensure_ascii=False, default=str) + "\n",
                encoding="utf-8",
            )
    try:
        shown: Path | str = out.relative_to(PROJECT_DIR)
    except ValueError:  # --out pointed somewhere outside the repo
        shown = out
    print(f"\nwritten to {shown}/")

    failed = [k for k, v in (result.criteria or {}).items() if not v.get("pass")]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
