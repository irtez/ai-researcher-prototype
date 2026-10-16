"""
iter19 — Reranker + recall@k on LitSearch (Tier-1).

Idea: the federated tools already retrieve the target (ceiling), but the agent only
reads the top few. Does an LLM listwise reranker (RankGPT-style) surface the target
into the top-k vs a support-count heuristic? Metric: recall@5/@10/@20, paired McNemar
on recall@10.

Reuses eval_tool_recall's federated search; adds candidate extraction + reranking.
LitSearch only — title↔corpus-id matching makes recall@k well-defined there.

Usage:
    uv run python evals_and_reports/eval_rerank.py --benchmark litsearch --max_workers 8
    uv run python evals_and_reports/eval_rerank.py --benchmark litsearch_full --rerank True
"""

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

import fire  # type: ignore
from dotenv import load_dotenv

from eval_tool_recall import (
    BENCHMARK_FILES,
    DATA_DIR,
    RESULTS_DIR,
    _get_litsearch_title_map,
    _normalize_title,
    _warn_llm_dead,
    search_for_paper,
)


def _title_of(item: Any) -> str:
    """Extract a title string from a heterogeneous source result item."""
    if not isinstance(item, dict):
        return ""
    t = item.get("title") or item.get("display_name") or item.get("name") or ""
    if isinstance(t, list):  # crossref titles are lists
        t = t[0] if t else ""
    return t if isinstance(t, str) else ""


def extract_candidates(results: Dict[str, str]) -> List[Dict[str, Any]]:
    """Merge all source results into a deduped candidate list with a support count
    (how many sources returned that title). Ordered by support desc (no-rerank default)."""
    by_norm: Dict[str, Dict[str, Any]] = {}
    for _tool, payload in results.items():
        try:
            items = json.loads(payload)
        except Exception:
            continue
        if not isinstance(items, list):
            continue
        for it in items:
            title = _title_of(it)
            norm = _normalize_title(title)
            if not norm or len(norm) < 8:
                continue
            if norm not in by_norm:
                by_norm[norm] = {"title": title, "norm": norm, "support": 0}
            by_norm[norm]["support"] += 1
    cands = list(by_norm.values())
    cands.sort(key=lambda c: c["support"], reverse=True)
    return cands


def _target_titles(record: Dict[str, Any], rev_map: Dict[int, str]) -> List[str]:
    out = []
    for cid in record.get("target_corpus_ids", []):
        t = rev_map.get(int(cid))
        if t:
            out.append(_normalize_title(t))
    return out


def _title_matches(cand_norm: str, target_norm: str) -> bool:
    """Fuzzy: target's leading 4-word chunk appears in candidate (or vice-versa)."""
    tw = target_norm.split()
    if len(tw) <= 3:
        return all(w in cand_norm for w in tw)
    chunk = " ".join(tw[:4])
    return chunk in cand_norm or cand_norm[:60] in target_norm


def recall_at_k(cands: List[Dict[str, Any]], target_norms: List[str], ks=(5, 10, 20)) -> Dict[int, int]:
    hits = {k: 0 for k in ks}
    for k in ks:
        topk = cands[:k]
        found = any(
            _title_matches(c["norm"], tn) for c in topk for tn in target_norms
        )
        hits[k] = int(found)
    return hits


def llm_rerank(query: str, cands: List[Dict[str, Any]], cap: int = 40) -> List[Dict[str, Any]]:
    """RankGPT-style listwise rerank of the top-`cap` candidates by title."""
    pool = cands[:cap]
    if len(pool) <= 1:
        return cands
    try:
        import os
        from openai import OpenAI

        api_key = os.getenv("OPENROUTER_API_KEY", "")
        if not api_key:
            from dotenv import dotenv_values

            env = dotenv_values(Path(__file__).parent.parent.parent / ".env")
            api_key = env.get("OPENROUTER_API_KEY", "")
        if not api_key:
            return cands
        client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key, timeout=30.0)
        listing = "\n".join(f"[{i}] {c['title'][:140]}" for i, c in enumerate(pool))
        resp = client.chat.completions.create(
            model="deepseek/deepseek-chat-v3-0324",
            messages=[{
                "role": "user",
                "content": (
                    "Rank the candidate papers by relevance to the query for finding the "
                    "SPECIFIC paper(s) the user wants. Output ONLY the indices, most relevant "
                    "first, comma-separated (e.g. 3,0,7,1).\n\n"
                    f"Query: {query}\n\nCandidates:\n{listing}"
                ),
            }],
            max_tokens=300,
            temperature=0.0,
        )
        content = resp.choices[0].message.content or ""
        order: List[int] = []
        for tok in content.replace("\n", ",").split(","):
            tok = tok.strip().lstrip("[").rstrip("]")
            if tok.isdigit():
                idx = int(tok)
                if 0 <= idx < len(pool) and idx not in order:
                    order.append(idx)
        if not order:
            return cands
        reranked = [pool[i] for i in order]
        # append any pool items the model omitted, then the tail beyond cap
        seen = set(order)
        reranked += [pool[i] for i in range(len(pool)) if i not in seen]
        reranked += cands[cap:]
        return reranked
    except Exception as e:
        _warn_llm_dead("llm_rerank", str(e))
        return cands


def run_eval(
    benchmark: str = "litsearch",
    rerank: bool = True,
    max_workers: int = 8,
    nrows: Optional[int] = None,
    experiment_name: str = "iter19_rerank",
) -> None:
    load_dotenv()
    title_map = _get_litsearch_title_map()
    rev_map = {int(v): k for k, v in title_map.items()}  # corpusid -> normalized title

    fpath = DATA_DIR / BENCHMARK_FILES[benchmark]
    records = [json.loads(l) for l in open(fpath)]
    if nrows:
        records = records[:nrows]
    total = len(records)
    print(f"iter19 rerank eval: {benchmark} n={total} rerank={rerank}")

    def _process(record: Dict[str, Any]) -> Dict[str, Any]:
        q = record.get("query", "")
        results = search_for_paper(q, query_strategy="rule")
        cands = extract_candidates(results)
        tnorms = _target_titles(record, rev_map)
        raw = recall_at_k(cands, tnorms)
        rer = recall_at_k(llm_rerank(q, cands), tnorms) if rerank else raw
        return {"query": q, "n_cands": len(cands), "raw": raw, "reranked": rer,
                "has_target_in_pool": int(any(_title_matches(c["norm"], tn) for c in cands for tn in tnorms))}

    rows: List[Dict[str, Any]] = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = [pool.submit(_process, r) for r in records]
        for i, f in enumerate(as_completed(futs)):
            rows.append(f.result())
            if (i + 1) % 10 == 0:
                print(f"  {i+1}/{total}", flush=True)

    def agg(key: str, k: int) -> float:
        return round(sum(r[key][k] for r in rows) / total * 100, 1)

    print(f"\n{'='*60}\niter19 recall@k ({benchmark}, n={total}, {time.time()-t0:.0f}s)")
    print(f"  ceiling (target in pool): {round(sum(r['has_target_in_pool'] for r in rows)/total*100,1)}%")
    for k in (5, 10, 20):
        line = f"  recall@{k:<2}: raw {agg('raw', k):>5}%"
        if rerank:
            line += f"  ->  reranked {agg('reranked', k):>5}%"
        print(line)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = RESULTS_DIR / f"{experiment_name}_raw.jsonl"
    with open(raw_path, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    log = {"experiment": experiment_name, "type": "rerank_recall_at_k",
           "date": time.strftime("%Y-%m-%dT%H:%M:%S"), "benchmark": benchmark,
           "n": total, "rerank": rerank,
           "ceiling": round(sum(r['has_target_in_pool'] for r in rows)/total*100, 1)}
    for k in (5, 10, 20):
        log[f"raw_recall@{k}"] = agg("raw", k)
        if rerank:
            log[f"reranked_recall@{k}"] = agg("reranked", k)
    with open(RESULTS_DIR / "experiment_log.jsonl", "a") as fh:
        fh.write(json.dumps(log) + "\n")
    print(f"  saved {raw_path}")


if __name__ == "__main__":
    fire.Fire(run_eval)
