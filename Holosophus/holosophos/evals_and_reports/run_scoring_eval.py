#!/usr/bin/env python3
"""Scoring prompt A/B evaluation.

Tests whether an LLM scoring prompt can reliably distinguish weak/medium/strong
research proposals. Measures discrimination, stability, cross-model agreement.

Usage (from Holosophus/holosophos/ or Holosophus/):
    python evals_and_reports/run_scoring_eval.py discrimination --config production
    python evals_and_reports/run_scoring_eval.py retest --config production --runs 3
    python evals_and_reports/run_scoring_eval.py ab --config-a baseline --config-b production
    python evals_and_reports/run_scoring_eval.py cross-model --models production,production_gpt4.1,production_claude
    python evals_and_reports/run_scoring_eval.py full --runs 3

The "production" config imports the live SCORE_PROMPT from academia_mcp/tools/research_proposals.py.
The "baseline" config is a frozen snapshot of the old prompt for A/B comparison.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Template
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

# ── paths ─────────────────────────────────────────────────────────────────
TESTSET_PATH = Path(__file__).parent / "data" / "scoring_testset.json"
OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "workdir" / "scoring_eval"
SCORE_FIELDS = ["novelty", "clarity", "significance", "feasibility", "soundness", "overall"]

# ── import production prompts from academia_mcp ──────────────────────────
# research_proposals.py has heavy deps (datasets, paddlepaddle) that may not
# install on all platforms. We parse the source file directly to extract prompt
# strings and build Pydantic models locally — this guarantees the eval always
# tests the exact same prompt that ships in production.

PROPOSALS_SRC = Path(__file__).resolve().parent.parent.parent / "academia_mcp" / "academia_mcp" / "tools" / "research_proposals.py"

def _extract_prompt(src: str, name: str) -> str:
    """Extract a triple-quoted string assigned to `name` from Python source."""
    pattern = rf'^{name}\s*=\s*"""(.*?)"""'
    m = re.search(pattern, src, re.DOTALL | re.MULTILINE)
    if not m:
        raise ValueError(f"Could not extract {name} from research_proposals.py")
    return m.group(1)

def _load_production_prompts() -> tuple[str, str]:
    src = PROPOSALS_SRC.read_text()
    return _extract_prompt(src, "SCORE_SYSTEM_PROMPT"), _extract_prompt(src, "SCORE_PROMPT")

PROD_SYSTEM, PROD_PROMPT = _load_production_prompts()

# Response model — mirrors ScoredProposal/ScoreResearchProposalsResponse from
# research_proposals.py but defined here to avoid importing the heavy module.
class ScoredProposal(BaseModel):
    proposal_id: int = 0; spark: str = ""
    novelty_reasoning: str = ""; novelty: int = 0
    clarity_reasoning: str = ""; clarity: int = 0
    significance_reasoning: str = ""; significance: int = 0
    feasibility_reasoning: str = ""; feasibility: int = 0
    soundness_reasoning: str = ""; soundness: int = 0
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    improvement_suggestions: list[str] = Field(default_factory=list)
    overall_reasoning: str = ""; overall: int = 0

class ScoreResponse(BaseModel):
    proposals: list[ScoredProposal] = Field(default_factory=list)

# Frozen baseline: the old prompt before V2 changes (for A/B comparison).
# This is intentionally hardcoded — it's a historical snapshot, not production code.
class ScoredBaseline(BaseModel):
    proposal_id: int = 0; spark: str = ""
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    novelty: int = 0; clarity: int = 0; significance: int = 0
    feasibility: int = 0; soundness: int = 0; overall: int = 0

class ResponseBaseline(BaseModel):
    proposals: list[ScoredBaseline] = Field(default_factory=list)

BASELINE_PROMPT = """
You are a highly advanced research assistant.
You are given a list of research proposals.
Your task is to score the proposals.

Proposals:
{% for proposal in proposals %}
----
{{proposal}}
----
{% endfor %}

Here are the criteria:
- "Strengths": A list of strengths of the proposal.
- "Weaknesses": A list of weaknesses of the proposal.
- "Novelty": Is the proposal novel? A rating from 1 to 4 (low, medium, high, very high).
- "Clarity": Is the proposal clear? A rating from 1 to 4 (low, medium, high, very high).
- "Significance": Is the proposal significant? A rating from 1 to 4 (low, medium, high, very high).
- "Feasibility": Is the proposal feasible and easy to implement? A rating from 1 to 4 (low, medium, high, very high).
- "Soundness": Is the proposal sound? A rating from 1 to 4 (poor, fair, good, excellent).
- "Overall": A rating from 1 to 10 (very strong reject to award quality).

Return only scores for all proposals in this exact format (no extra text):
{
    "proposals": [
        {"proposal_id": 0, "spark": "...", "strengths": ["..."], "weaknesses": ["..."],
         "novelty": 2, "clarity": 2, "significance": 2, "feasibility": 2, "soundness": 2, "overall": 5},
        ...
    ]
}
"""

# ── scoring config ────────────────────────────────────────────────────────

@dataclass
class ScoringConfig:
    name: str
    model: str
    system_prompt: str | None
    prompt_template: str
    response_model: type[BaseModel]
    temperature: float = 0.0
    overall_max: int = 10

    def render(self, proposals: list[dict]) -> str:
        fmt = [json.dumps(p, indent=2, ensure_ascii=False) for p in proposals]
        return Template(self.prompt_template).render(proposals=fmt).strip()

    def with_model(self, model: str, name: str | None = None) -> ScoringConfig:
        return ScoringConfig(name=name or f"{self.name}_{model.split('/')[-1]}",
            model=model, system_prompt=self.system_prompt,
            prompt_template=self.prompt_template, response_model=self.response_model,
            temperature=self.temperature, overall_max=self.overall_max)

DS_V3 = "deepseek/deepseek-chat-v3-0324"
CONFIGS: dict[str, ScoringConfig] = {
    # production: imports live prompt from research_proposals.py
    "production": ScoringConfig("production", DS_V3, PROD_SYSTEM, PROD_PROMPT, ScoreResponse, overall_max=5),
    # baseline: frozen old prompt for A/B comparison
    "baseline": ScoringConfig("baseline", DS_V3, None, BASELINE_PROMPT, ResponseBaseline, overall_max=10),
    # cross-model variants of production prompt
    "production_gpt4.1": ScoringConfig("production_gpt4.1", "openai/gpt-4.1", PROD_SYSTEM, PROD_PROMPT, ScoreResponse, overall_max=5),
    "production_claude": ScoringConfig("production_claude", "anthropic/claude-sonnet-4", PROD_SYSTEM, PROD_PROMPT, ScoreResponse, overall_max=5),
}

# ── LLM scorer ────────────────────────────────────────────────────────────

@dataclass
class ScoringRun:
    config_name: str; model: str
    proposals: list[dict]; scores: list[dict]; tiers: list[str]
    duration_s: float = 0.0; errors: list[str] = field(default_factory=list)


def _client() -> AsyncOpenAI:
    key = os.environ.get("OPENROUTER_API_KEY", "")
    assert key, "Set OPENROUTER_API_KEY"
    return AsyncOpenAI(base_url="https://openrouter.ai/api/v1", api_key=key)


async def _call(client: AsyncOpenAI, cfg: ScoringConfig, prompt: str) -> BaseModel:
    msgs: list[dict] = []
    if cfg.system_prompt:
        msgs.append({"role": "system", "content": cfg.system_prompt})
    msgs.append({"role": "user", "content": prompt})
    try:
        r = await client.chat.completions.parse(
            model=cfg.model, messages=msgs,
            response_format=cfg.response_model, temperature=cfg.temperature)
        assert r.choices[0].message.parsed
        return r.choices[0].message.parsed
    except Exception:
        r2 = await client.chat.completions.create(
            model=cfg.model, messages=msgs, temperature=cfg.temperature)
        text = r2.choices[0].message.content or ""
        for pat in [r"```json\s*(.*?)\s*```", r"```\s*(.*?)\s*```", r"(\{.*\})"]:
            for m in re.findall(pat, text, re.DOTALL):
                try: return cfg.response_model.model_validate(json.loads(m.strip()))
                except Exception: continue
        raise ValueError(f"Parse failed:\n{text[:500]}")


async def score(proposals: list[dict], cfg: ScoringConfig) -> list[dict]:
    c = _client()
    prompt = cfg.render(proposals)
    for attempt in range(3):
        try:
            result = await _call(c, cfg, prompt)
            return result.model_dump().get("proposals", [])
        except Exception as e:
            if attempt == 2: raise
            await asyncio.sleep(2 ** attempt)
    return []


async def run_scoring(items: list[dict], cfg: ScoringConfig) -> ScoringRun:
    props = [it["proposal"] for it in items]
    tiers = [it["tier"] for it in items]
    t0 = time.time(); errs: list[str] = []
    try: scores = await score(props, cfg)
    except Exception as e: scores = []; errs.append(str(e))
    return ScoringRun(cfg.name, cfg.model, props, scores, tiers, time.time()-t0, errs)

# ── stats (no scipy) ─────────────────────────────────────────────────────

def _mean(xs: list[float]) -> float: return sum(xs)/len(xs) if xs else 0.0
def _var(xs: list[float], ddof: int = 1) -> float:
    if len(xs) <= ddof: return 0.0
    m = _mean(xs); return sum((x-m)**2 for x in xs) / (len(xs)-ddof)
def _std(xs: list[float], ddof: int = 1) -> float: return math.sqrt(_var(xs, ddof))

def _skewness(xs: list[float]) -> float:
    n = len(xs)
    if n < 3: return 0.0
    m, s = _mean(xs), _std(xs)
    if s == 0: return 0.0
    return (n / ((n-1)*(n-2))) * sum(((x-m)/s)**3 for x in xs)

def _spearman(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 3: return 0.0
    def _rank(v: list[float]) -> list[float]:
        idx = sorted(range(n), key=lambda i: v[i]); r = [0.0]*n; i = 0
        while i < n:
            j = i
            while j < n-1 and v[idx[j]] == v[idx[j+1]]: j += 1
            avg = (i+j)/2+1
            for k in range(i, j+1): r[idx[k]] = avg
            i = j+1
        return r
    rx, ry = _rank(xs), _rank(ys)
    d2 = sum((a-b)**2 for a, b in zip(rx, ry))
    return 1 - 6*d2 / (n*(n*n-1))

def _icc(matrix: list[list[float]]) -> float:
    n = len(matrix)
    if n == 0: return 0.0
    k = len(matrix[0])
    if k < 2: return 1.0
    gm = _mean([x for row in matrix for x in row])
    rm = [_mean(row) for row in matrix]
    msb = k*sum((r-gm)**2 for r in rm)/(n-1) if n > 1 else 0
    ssw = sum(sum((x-r)**2 for x in row) for row, r in zip(matrix, rm))
    msw = ssw/(n*(k-1)) if n*(k-1) > 0 else 0
    d = msb + (k-1)*msw
    return (msb-msw)/d if d else 0

def _cohens_d(a: list[float], b: list[float]) -> float:
    if len(a) < 2 or len(b) < 2: return 0.0
    m1, m2 = _mean(a), _mean(b)
    pv = ((len(a)-1)*_var(a) + (len(b)-1)*_var(b)) / (len(a)+len(b)-2)
    if pv == 0: return float("inf") if m2 > m1 else (float("-inf") if m2 < m1 else 0.0)
    return (m2-m1)/math.sqrt(pv)

# ── metric helpers ────────────────────────────────────────────────────────

def _scores(run: ScoringRun, f: str) -> list[float]:
    return [float(s.get(f, 0)) for s in run.scores]

def _by_tier(run: ScoringRun, f: str) -> dict[str, list[float]]:
    r: dict[str, list[float]] = {}
    for t, s in zip(run.tiers, run.scores): r.setdefault(t, []).append(float(s.get(f, 0)))
    return r

# ── formatters ────────────────────────────────────────────────────────────

def _hdr(t: str) -> str: return f"\n{'='*60}\n  {t}\n{'='*60}"

def fmt_raw(run: ScoringRun) -> str:
    L = [_hdr(f"RAW SCORES — {run.config_name} ({run.model})")]
    L.append(f"{'ID':>3} {'Tier':<8} {'Nov':>4} {'Cla':>4} {'Sig':>4} {'Fea':>4} {'Sou':>4} {'Ovr':>4}  Spark")
    L.append("-"*70)
    for t, s in zip(run.tiers, run.scores):
        L.append(f"{s.get('proposal_id','?'):>3} {t:<8} "
            f"{s.get('novelty',0):>4} {s.get('clarity',0):>4} {s.get('significance',0):>4} "
            f"{s.get('feasibility',0):>4} {s.get('soundness',0):>4} {s.get('overall',0):>4}  "
            f"{s.get('spark','')[:40]}")
    L.append(f"\nDuration: {run.duration_s:.1f}s")
    if run.errors: L.append(f"Errors: {run.errors}")
    return "\n".join(L)

def fmt_disc(run: ScoringRun, name: str) -> str:
    L = [_hdr(f"DISCRIMINATION — {name}")]
    L.append(f"{'Criterion':<15} {'weak':>6} {'med':>6} {'strong':>6}  {'d(w-s)':>7} {'Verdict':>8}")
    L.append("-"*60)
    for f in SCORE_FIELDS:
        bt = _by_tier(run, f)
        w, m, s = _mean(bt.get("weak",[])), _mean(bt.get("medium",[])), _mean(bt.get("strong",[]))
        d = _cohens_d(bt.get("weak",[]), bt.get("strong",[]))
        da = abs(d) if math.isfinite(d) else 999
        v = "INVERTED" if d < 0 else ("GOOD" if da > 0.8 else ("MODERATE" if da > 0.5 else "POOR"))
        ds = f"{d:>7.2f}" if math.isfinite(d) else "    inf"
        L.append(f"{f:<15} {w:>6.2f} {m:>6.2f} {s:>6.2f}  {ds} {v:>8}")
    return "\n".join(L)

def fmt_dist(run: ScoringRun, name: str, omax: int = 10) -> str:
    L = [_hdr(f"DISTRIBUTION — {name}")]
    L.append(f"{'Criterion':<15} {'Mean':>6} {'Std':>6} {'Range':>8} {'Skew':>7} {'Flags'}")
    L.append("-"*65)
    for f in SCORE_FIELDS:
        sc = _scores(run, f)
        if not sc: continue
        mx = omax if f == "overall" else 4
        sd, sk = _std(sc), _skewness(sc)
        flags = []
        if sd < 0.3: flags.append("LOW_VAR")
        if sk > 0.5: flags.append("LENIENCY")
        elif sk < -0.5: flags.append("SEVERITY")
        L.append(f"{f:<15} {_mean(sc):>6.2f} {sd:>6.2f} {min(sc):.0f}-{max(sc):.0f}     {sk:>7.2f} {', '.join(flags) or 'OK'}")
    return "\n".join(L)

def fmt_retest(runs: list[ScoringRun], name: str) -> str:
    n_p = len(runs[0].scores) if runs else 0
    L = [_hdr(f"TEST-RETEST — {name} ({len(runs)} runs)")]
    L.append(f"{'Criterion':<15} {'ICC':>8} {'Mean Std':>10} {'Verdict':>10}")
    L.append("-"*50)
    for f in SCORE_FIELDS:
        mat = [[float(runs[j].scores[i].get(f,0)) for j in range(len(runs))] for i in range(n_p)]
        ic = _icc(mat)
        ms = _mean([_std(row, ddof=0) for row in mat])
        v = "EXCELLENT" if ic > 0.9 else ("GOOD" if ic > 0.75 else ("MODERATE" if ic > 0.5 else "POOR"))
        L.append(f"{f:<15} {ic:>8.3f} {ms:>10.3f} {v:>10}")
    return "\n".join(L)

def fmt_cross(runs: dict[str, ScoringRun]) -> str:
    L = [_hdr("CROSS-MODEL AGREEMENT")]
    L.append(f"{'A':<18} {'B':<18} {'Criterion':<15} {'Spearman':>8} {'Diff':>6} {'V':>6}")
    L.append("-"*75)
    keys = list(runs.keys())
    for i in range(len(keys)):
        for j in range(i+1, len(keys)):
            ra, rb = runs[keys[i]], runs[keys[j]]
            for f in SCORE_FIELDS:
                sa, sb = _scores(ra, f), _scores(rb, f)
                rho = _spearman(sa, sb); diff = _mean(sb) - _mean(sa)
                v = "GOOD" if rho > 0.7 else ("MOD" if rho > 0.4 else "POOR")
                L.append(f"{keys[i]:<18} {keys[j]:<18} {f:<15} {rho:>8.3f} {diff:>+6.2f} {v:>6}")
    return "\n".join(L)

def fmt_ab(ra: ScoringRun, rb: ScoringRun, na: str, nb: str) -> str:
    L = [_hdr(f"A/B — {na} vs {nb}")]
    L.append(f"{'Criterion':<15} {'A avg':>6} {'B avg':>6} {'d(A)':>6} {'d(B)':>6} {'Win':>5}")
    L.append("-"*50)
    aw = bw = tw = 0
    for f in SCORE_FIELDS:
        ta, tb = _by_tier(ra, f), _by_tier(rb, f)
        da = _cohens_d(ta.get("weak",[]), ta.get("strong",[]))
        db = _cohens_d(tb.get("weak",[]), tb.get("strong",[]))
        ai, bi = not math.isfinite(da), not math.isfinite(db)
        if ai and bi:
            ga = _mean(ta.get("strong",[0])) - _mean(ta.get("weak",[0]))
            gb = _mean(tb.get("strong",[0])) - _mean(tb.get("weak",[0]))
            w = "tie" if abs(ga-gb) < 0.5 else ("B" if gb > ga else "A")
        elif ai: w = "A"
        elif bi: w = "B"
        else: w = "tie" if abs(da-db) < 0.2 else ("B" if db > da else "A")
        if w == "A": aw += 1
        elif w == "B": bw += 1
        else: tw += 1
        das = f"{da:>6.1f}" if math.isfinite(da) else "   inf"
        dbs = f"{db:>6.1f}" if math.isfinite(db) else "   inf"
        L.append(f"{f:<15} {_mean(_scores(ra,f)):>6.2f} {_mean(_scores(rb,f)):>6.2f} {das} {dbs} {w:>5}")
    L.append(f"\nA={aw} B={bw} tie={tw}")
    return "\n".join(L)

# ── save ──────────────────────────────────────────────────────────────────

def _save(name: str, data: dict) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    p = OUTPUT_DIR / f"{name}_{datetime.now():%Y%m%d_%H%M%S}.json"
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    return p

# ── commands ──────────────────────────────────────────────────────────────

def _testset() -> list[dict]:
    return json.loads(TESTSET_PATH.read_text())["proposals"]

def _cfg(n: str) -> ScoringConfig:
    if n not in CONFIGS: sys.exit(f"Unknown config '{n}'. Available: {list(CONFIGS.keys())}")
    return CONFIGS[n]

async def cmd_discrimination(cn: str) -> None:
    cfg = _cfg(cn); ts = _testset()
    print(f"Scoring {len(ts)} proposals [{cn}] ({cfg.model})...")
    run = await run_scoring(ts, cfg)
    print(fmt_raw(run)); print(fmt_disc(run, cn)); print(fmt_dist(run, cn, cfg.overall_max))
    p = _save(f"disc_{cn}", {"config": cn, "model": cfg.model, "scores": run.scores, "tiers": run.tiers})
    print(f"\nSaved: {p}")

async def cmd_retest(cn: str, n: int) -> None:
    cfg = _cfg(cn); ts = _testset()
    print(f"Test-retest: {n} runs × {len(ts)} proposals [{cn}]...")
    runs = []
    for i in range(n):
        print(f"  Run {i+1}/{n}..."); r = await run_scoring(ts, cfg); runs.append(r)
        print(f"    {r.duration_s:.1f}s")
    for r in runs: print(fmt_raw(r))
    print(fmt_retest(runs, cn))
    _save(f"retest_{cn}", {"config": cn, "n": n, "runs": [{"scores": r.scores} for r in runs]})

async def cmd_cross_model(names: list[str]) -> None:
    ts = _testset(); runs: dict[str, ScoringRun] = {}
    for cn in names:
        cfg = _cfg(cn); print(f"Scoring [{cn}] ({cfg.model})...")
        r = await run_scoring(ts, cfg); runs[cn] = r; print(fmt_raw(r))
    print(fmt_cross(runs))
    for cn, r in runs.items(): print(fmt_disc(r, cn))
    _save("cross_model", {"configs": names, "runs": {k: {"scores": v.scores, "tiers": v.tiers} for k, v in runs.items()}})

async def cmd_ab(an: str, bn: str) -> None:
    ca, cb = _cfg(an), _cfg(bn); ts = _testset()
    print(f"A/B: {an} vs {bn}\n  A: {ca.model} 1-{ca.overall_max}\n  B: {cb.model} 1-{cb.overall_max}")
    print(f"\nScoring A..."); ra = await run_scoring(ts, ca); print(fmt_raw(ra))
    print(f"\nScoring B..."); rb = await run_scoring(ts, cb); print(fmt_raw(rb))
    print(fmt_ab(ra, rb, an, bn))
    print(fmt_dist(ra, an, ca.overall_max)); print(fmt_dist(rb, bn, cb.overall_max))
    _save(f"ab_{an}_vs_{bn}", {"a": an, "b": bn, "ra": {"scores": ra.scores, "tiers": ra.tiers}, "rb": {"scores": rb.scores, "tiers": rb.tiers}})

async def cmd_full(nr: int) -> None:
    print(_hdr("FULL SUITE"))
    await cmd_discrimination("baseline"); await cmd_discrimination("production")
    await cmd_retest("production", nr); await cmd_ab("baseline", "production")
    print(_hdr("DONE")); print(f"Results: {OUTPUT_DIR}")

# ── CLI ───────────────────────────────────────────────────────────────────

def main() -> None:
    env = Path(__file__).resolve().parent.parent.parent / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

    p = argparse.ArgumentParser(description="Scoring prompt A/B eval")
    s = p.add_subparsers(dest="cmd", required=True)

    d = s.add_parser("discrimination"); d.add_argument("--config", default="production")
    r = s.add_parser("retest"); r.add_argument("--config", default="production"); r.add_argument("--runs", type=int, default=3)
    c = s.add_parser("cross-model"); c.add_argument("--models", default="production,production_gpt4.1,production_claude")
    a = s.add_parser("ab"); a.add_argument("--config-a", default="baseline"); a.add_argument("--config-b", default="production")
    f = s.add_parser("full"); f.add_argument("--runs", type=int, default=3)

    args = p.parse_args()
    if args.cmd == "discrimination": asyncio.run(cmd_discrimination(args.config))
    elif args.cmd == "retest": asyncio.run(cmd_retest(args.config, args.runs))
    elif args.cmd == "cross-model": asyncio.run(cmd_cross_model([m.strip() for m in args.models.split(",")]))
    elif args.cmd == "ab": asyncio.run(cmd_ab(args.config_a, args.config_b))
    elif args.cmd == "full": asyncio.run(cmd_full(args.runs))

if __name__ == "__main__":
    main()
