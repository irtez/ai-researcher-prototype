"""
McNemar exact paired-significance gate for librarian tool-recall experiments.

Pairs two runs by (benchmark, query) on the binary `found` outcome and runs the
two-sided exact McNemar test on the discordant pairs. An experiment is "locked"
(kept) only when the change is positive AND significant (p < alpha).

Usage:
    uv run python evals_and_reports/stat_gate.py \
        --a results/iter14_rebaseline_raw.jsonl \
        --b results/iter15_openalex_ablation_raw.jsonl
"""

import json
from math import comb
from pathlib import Path
from typing import Any, Dict, Tuple

import fire  # type: ignore


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value for discordant counts b and c.

    b = #items hit by A but not B; c = #items hit by B but not A.
    Under H0 each discordant item is 50/50; test statistic ~ Binomial(n=b+c, 0.5).
    """
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(comb(n, i) for i in range(0, k + 1)) * (0.5 ** n)
    return min(1.0, 2.0 * tail)


def _load(path: str) -> Dict[Tuple[str, str], int]:
    """Load a tool-recall raw jsonl into {(benchmark, query): found(0/1)}."""
    out: Dict[Tuple[str, str], int] = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            key = (r.get("benchmark", ""), r.get("query", ""))
            out[key] = int(bool(r.get("found", False)))
    return out


def compare(a_hits: Dict[Any, int], b_hits: Dict[Any, int]) -> Dict[str, Any]:
    """Paired McNemar over the shared keys of two hit maps."""
    shared = sorted(set(a_hits) & set(b_hits))
    n = len(shared)
    a_found = sum(a_hits[k] for k in shared)
    b_found = sum(b_hits[k] for k in shared)
    # discordant pairs
    b = sum(1 for k in shared if a_hits[k] == 1 and b_hits[k] == 0)
    c = sum(1 for k in shared if a_hits[k] == 0 and b_hits[k] == 1)
    p = mcnemar_exact(b, c)
    delta_pp = (b_found - a_found) / n * 100 if n else 0.0
    return {
        "n": n,
        "a_found": a_found,
        "b_found": b_found,
        "a_recall": round(a_found / n * 100, 1) if n else 0.0,
        "b_recall": round(b_found / n * 100, 1) if n else 0.0,
        "delta_pp": round(delta_pp, 1),
        "b_only_a": b,
        "c_only_b": c,
        "p_value": round(p, 4),
        "significant": p < 0.05,
        "locked": (c > b) and (p < 0.05),  # positive AND significant
    }


def compare_runs(a: str, b: str, alpha: float = 0.05) -> Dict[str, Any]:
    """Compare two raw jsonl runs overall and per-benchmark. Prints a table."""
    a_map, b_map = _load(a), _load(b)
    benches = sorted({k[0] for k in (set(a_map) & set(b_map))})
    report: Dict[str, Any] = {"overall": compare(a_map, b_map), "by_benchmark": {}}
    for bench in benches:
        a_sub = {k: v for k, v in a_map.items() if k[0] == bench}
        b_sub = {k: v for k, v in b_map.items() if k[0] == bench}
        report["by_benchmark"][bench] = compare(a_sub, b_sub)

    def _row(name: str, r: Dict[str, Any]) -> str:
        flag = "LOCK ++" if r["locked"] else ("(+ n.s.)" if r["delta_pp"] > 0 else "(no gain)")
        return (f"  {name:<14} {r['a_recall']:>5}% -> {r['b_recall']:>5}% "
                f"(Δ{r['delta_pp']:+.1f}pp, b={r['b_only_a']} c={r['c_only_b']}, "
                f"p={r['p_value']}) {flag}")

    print(f"\n{'='*72}\nMcNemar gate: A={Path(a).name}  B={Path(b).name}  alpha={alpha}\n{'='*72}")
    for bench, r in report["by_benchmark"].items():
        print(_row(bench, r))
    print(_row("OVERALL", report["overall"]))
    print(f"{'='*72}\n")
    return report


if __name__ == "__main__":
    fire.Fire(compare_runs)
