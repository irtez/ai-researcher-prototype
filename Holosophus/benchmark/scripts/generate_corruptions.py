#!/usr/bin/env python3
"""Generate synthetically corrupted versions of gold papers for metric validation.

Usage:
    python generate_corruptions.py data/gold/arxiv/sample_001/ data/corrupted/sample_001/
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from writer.base.base import find_bib, find_main_tex

CORRUPTIONS = [
    "remove_abstract",
    "remove_method_section",
    "empty_results_section",
    "swap_experiments_results",
    "break_citation_keys",
    "add_unused_bib_entries",
    "remove_captions",
    "add_dangling_refs",
    "remove_contributions",
    "duplicate_labels",
]


def _copy_base(src: Path, dst: Path) -> tuple[Path, Path | None]:
    """Copy the paper package and return (tex_path, bib_path)."""
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    return find_main_tex(dst), find_bib(dst)


def remove_abstract(tex: str) -> str:
    return re.sub(r"\\begin\{abstract\}.*?\\end\{abstract\}", "", tex, flags=re.DOTALL)


def remove_method_section(tex: str) -> str:
    return re.sub(r"\\section\{[Mm]ethod(?:s|ology)?\}.*?(?=\\section\{)", "", tex, flags=re.DOTALL)


def empty_results_section(tex: str) -> str:
    return re.sub(
        r"(\\section\{[Rr]esults?\}[^\n]*\n).*?(?=\\section\{)",
        r"\1TODO: fill results\n\n",
        tex,
        flags=re.DOTALL,
    )


def swap_experiments_results(tex: str) -> str:
    exp = re.search(r"(\\section\{Experiments?\}.*?)(?=\\section\{Results?\})", tex, re.DOTALL)
    res = re.search(r"(\\section\{Results?\}.*?)(?=\\section\{)", tex, re.DOTALL)
    if exp and res:
        tex = tex.replace(exp.group(0), "@@RESULTS@@")
        tex = tex.replace(res.group(0), exp.group(0))
        tex = tex.replace("@@RESULTS@@", res.group(0))
    return tex


def break_citation_keys(tex: str) -> str:
    keys = re.findall(r"\\cite\{([^}]+)\}", tex)
    for key_group in keys[:3]:
        for key in key_group.split(","):
            key = key.strip()
            if key:
                tex = tex.replace(key, f"BROKEN_{key}", 1)
    return tex


def add_unused_bib_entries(bib: str) -> str:
    return bib + "\n\n@article{unused_phantom_2099,\n  title={Phantom Paper},\n  author={Ghost},\n  year={2099}\n}\n"


def remove_captions(tex: str) -> str:
    return re.sub(r"\\caption\s*(?:\[[^\]]*\])?\s*\{[^}]*\}", "", tex)


def add_dangling_refs(tex: str) -> str:
    return tex + "\nSee Figure~\\ref{fig:phantom_999} and Table~\\ref{tab:phantom_999}.\n"


def remove_contributions(tex: str) -> str:
    return re.sub(
        r"(?:Our|The)\s+(?:main\s+)?contributions?\s+(?:are|include).*?\\end\{itemize\}",
        "We describe our approach below.",
        tex,
        flags=re.DOTALL | re.IGNORECASE,
    )


def duplicate_labels(tex: str) -> str:
    labels = re.findall(r"\\label\{([^}]+)\}", tex)
    if labels:
        first = labels[0]
        tex = tex + f"\n\\label{{{first}}}\n"
    return tex


CORRUPTION_FUNCS = {
    "remove_abstract": (lambda t, b: (remove_abstract(t), b)),
    "remove_method_section": (lambda t, b: (remove_method_section(t), b)),
    "empty_results_section": (lambda t, b: (empty_results_section(t), b)),
    "swap_experiments_results": (lambda t, b: (swap_experiments_results(t), b)),
    "break_citation_keys": (lambda t, b: (break_citation_keys(t), b)),
    "add_unused_bib_entries": (lambda t, b: (t, add_unused_bib_entries(b))),
    "remove_captions": (lambda t, b: (remove_captions(t), b)),
    "add_dangling_refs": (lambda t, b: (add_dangling_refs(t), b)),
    "remove_contributions": (lambda t, b: (remove_contributions(t), b)),
    "duplicate_labels": (lambda t, b: (duplicate_labels(t), b)),
}


def main():
    parser = argparse.ArgumentParser(description="Generate corrupted paper variants")
    parser.add_argument("source", help="Gold paper directory")
    parser.add_argument("output", help="Output directory for corrupted variants")
    parser.add_argument(
        "--corruptions", nargs="*", default=CORRUPTIONS,
        choices=CORRUPTIONS, help="Which corruptions to apply",
    )
    args = parser.parse_args()

    src = Path(args.source)
    out_base = Path(args.output)
    out_base.mkdir(parents=True, exist_ok=True)

    tex_path = find_main_tex(src)
    bib_path = find_bib(src)
    original_tex = tex_path.read_text(errors="replace")
    original_bib = bib_path.read_text(errors="replace") if bib_path else ""

    for corruption_name in args.corruptions:
        func = CORRUPTION_FUNCS[corruption_name]
        corrupted_tex, corrupted_bib = func(original_tex, original_bib)

        out_dir = out_base / corruption_name
        tex_dst, bib_dst = _copy_base(src, out_dir)
        tex_dst.write_text(corrupted_tex)
        if bib_dst:
            bib_dst.write_text(corrupted_bib)

        print(f"  Created: {out_dir}")

    print(f"\nGenerated {len(args.corruptions)} corrupted variants in {out_base}")


if __name__ == "__main__":
    main()
