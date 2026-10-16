"""Shared helpers for canonical writer benchmark tests."""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

from writer.base.base import PaperPackage

ACL_GOLD_TEX = textwrap.dedent(
    r"""
    \documentclass[11pt]{article}
    \usepackage[review]{acl}
    \usepackage{times}
    \usepackage{latexsym}
    \usepackage{amsmath}
    \author{Anonymous ACL submission}
    \title{A Unified Benchmark for Writer Metrics}
    \begin{document}
    \maketitle

    \begin{abstract}
    We study the problem of evaluating scientific paper generation for natural language processing.
    We present a deterministic ACL-oriented benchmark that audits structure, citations, formatting,
    and submission compliance for LaTeX paper packages. Results show that the benchmark detects
    common submission defects with 95.3\% precision while remaining fast enough for repeated use in
    automated writer evaluation workflows.
    \end{abstract}

    \section{Introduction}
    Automatic scientific writing systems are increasingly used to prepare drafts for NLP venues.
    This creates a practical need for deterministic validation before submission. Our main
    contributions are: (1) a reusable benchmark architecture with shared and venue-specific modules,
    (2) a suite of ACL-oriented compliance metrics, and (3) synthetic corruption tests that make the
    benchmark easy to extend. The paper focuses on automated quality checks rather than human
    preference judgments and aims to support maintainable evaluation pipelines.

    \section{Related Work}
    Prior work evaluates text generation with fluency metrics or manual assessment only.
    SurveyBench~\cite{surveybench2024} highlights the difficulty of measuring academic writing
    quality. S2ORC~\cite{lo2020s2orc} provides structured scholarly documents, and
    PeerRead~\cite{kang2018peerread} shows the value of paper-review artifacts for analysis.
    However, these resources do not directly enforce ACL submission hygiene for generated LaTeX.

    \section{Method}
    Our benchmark parses LaTeX sources, extracts sections, citations, labels, and floats, and then
    applies deterministic rules. The method supports a shared core for venue-agnostic checks and a
    profile layer for ACL-specific expectations. This lets us preserve modularity while adding
    precise rules for formatting, anonymization, and checklist artifacts.

    \section{Experiments}
    We evaluate the benchmark on a writer output bundle and a corruption suite. The dataset contains
    20 gold papers and 50 corrupted variants. We compare against a manual baseline and report
    accuracy, precision, recall, and F1-score. Implementation details include a batch size of 1, a
    learning rate of 0.001, and an Adam optimizer on a single GPU. The model was trained for
    10 epochs and the processed data is publicly available for reproducibility.

    \section{Results}
    Table~\ref{tab:main_results} summarizes the main metrics. The benchmark reaches 95.3\%
    precision, 93.1\% recall, and 94.2 F1 on defect detection. Figure~\ref{fig:overview}
    illustrates performance across corruption types and shows that structural failures are easier to
    catch than subtle content omissions.

    \begin{table}[t]
    \caption{Detection quality on corrupted ACL-style papers.}
    \label{tab:main_results}
    \begin{tabular}{lccc}
    Metric & Precision & Recall & F1 \\
    Structural & 95.3 & 93.1 & 94.2 \\
    Citation & 98.4 & 97.9 & 98.1 \\
    Content & 88.0 & 85.4 & 86.7 \\
    \end{tabular}
    \end{table}

    \begin{figure}[t]
    \caption{Benchmark behavior across corruption classes.}
    \label{fig:overview}
    Placeholder visualization.
    \end{figure}

    \section{Limitations}
    The benchmark is heuristic and does not guarantee semantic correctness. It can miss highly
    unusual section structures or implicit claims that avoid standard phrases. Nevertheless, the
    rules are intentionally transparent and can be adapted to new conference profiles without
    rewriting the full evaluation stack.

    \section{Ethics Statement}
    This work evaluates automated scientific writing and should not be used to hide misconduct.
    The code is available in an anonymous repository for review, and the data is publicly available
    for reproducibility. We release the benchmark to support responsible NLP research workflows.

    \bibliographystyle{plain}
    \bibliography{references}
    \end{document}
    """
).strip()

ACL_GOLD_BIB = textwrap.dedent(
    r"""
    @inproceedings{surveybench2024,
      title={SurveyBench: A Benchmark for Scientific Survey Writing},
      author={Zhang, Wei and Liu, Jing},
      booktitle={Proceedings of ACL},
      year={2024}
    }

    @article{lo2020s2orc,
      title={S2ORC: The Semantic Scholar Open Research Corpus},
      author={Lo, Kyle and Wang, Lucy Lu and Neumann, Mark and Kinney, Rodney and Weld, Daniel S.},
      journal={Proceedings of ACL},
      year={2020}
    }

    @inproceedings{kang2018peerread,
      title={A Dataset of Peer Reviews},
      author={Kang, Dongyeop and Ammar, Waleed and Dalvi, Bhavana},
      booktitle={Proceedings of NAACL},
      year={2018}
    }
    """
).strip()

CHECKLIST_NAME = "acl_responsible_nlp_checklist.txt"
CHECKLIST_TEXT = "Responsible NLP Research Checklist\n- Data statement: yes\n- Limitations: yes\n"


def write_paper(
    root: Path,
    tex: str = ACL_GOLD_TEX,
    bib: str = ACL_GOLD_BIB,
    *,
    include_checklist: bool = True,
) -> PaperPackage:
    root.mkdir(parents=True, exist_ok=True)
    (root / "main.tex").write_text(tex)
    (root / "references.bib").write_text(bib)
    if include_checklist:
        (root / CHECKLIST_NAME).write_text(CHECKLIST_TEXT)
    return PaperPackage.from_directory(root)


def remove_abstract(tex: str) -> str:
    return re.sub(r"\\begin\{abstract\}.*?\\end\{abstract\}", "", tex, flags=re.DOTALL)


def remove_limitations(tex: str) -> str:
    return re.sub(r"\\section\{Limitations\}.*?(?=\\section\{|\Z)", "", tex, flags=re.DOTALL)


def empty_limitations(tex: str) -> str:
    return re.sub(
        r"(\\section\{Limitations\}\n).*?(?=\\section\{|\Z)",
        r"\1TODO.\n\n",
        tex,
        flags=re.DOTALL,
    )


def swap_results_and_limitations(tex: str) -> str:
    results = re.search(r"(\\section\{Results\}.*?)(?=\\section\{Limitations\})", tex, re.DOTALL)
    limitations = re.search(r"(\\section\{Limitations\}.*?)(?=\\section\{Ethics Statement\})", tex, re.DOTALL)
    if not results or not limitations:
        return tex
    swapped = tex.replace(results.group(0), "@@LIMITATIONS@@")
    swapped = swapped.replace(limitations.group(0), results.group(0))
    return swapped.replace("@@LIMITATIONS@@", limitations.group(0))


def break_citations(tex: str) -> str:
    tex = tex.replace("surveybench2024", "broken_surveybench2024", 1)
    return tex.replace("lo2020s2orc", "broken_lo2020s2orc", 1)


def add_unused_bib_entries(bib: str) -> str:
    return bib + textwrap.dedent(
        r"""

        @article{unused_entry_2025,
          title={Unused Entry},
          author={Ghost, Author},
          journal={Invisible Journal},
          year={2025}
        }
        """
    )


def remove_figure_caption(tex: str) -> str:
    return tex.replace(
        r"""\begin{figure}[t]
\caption{Benchmark behavior across corruption classes.}
\label{fig:overview}
Placeholder visualization.
\end{figure}""",
        r"""\begin{figure}[t]
\label{fig:overview}
Placeholder visualization.
\end{figure}""",
    )


def add_dangling_refs(tex: str) -> str:
    return tex + "\nSee Figure~\\ref{fig:ghost} and Table~\\ref{tab:ghost}.\n"


def duplicate_first_label(tex: str) -> str:
    labels = re.findall(r"\\label\{([^}]+)\}", tex)
    if not labels:
        return tex
    return tex + f"\n\\label{{{labels[0]}}}\n"


def remove_contributions(tex: str) -> str:
    return re.sub(
        r"Our main\s+contributions are:.*?benchmark easy to extend\.",
        "We describe the benchmark design in the following sections.",
        tex,
        flags=re.DOTALL,
    )


def remove_metric_values(tex: str) -> str:
    return re.sub(r"\d+(?:\.\d+)?\s*%?", "many", tex)


def inject_inconsistent_notation(tex: str) -> str:
    return re.sub(
        r"The dataset contains\s+20 gold papers and 50 corrupted variants\.",
        "The dataset contains 20 gold papers and 50 corrupted variants. "
        "A parallel data set is used for ablations.",
        tex,
        count=1,
    )


def make_long_abstract() -> str:
    sentence = "This sentence explains the benchmark in a reproducible and detailed way."
    return "\\begin{abstract}\n" + " ".join([sentence] * 45) + "\n\\end{abstract}"


def replace_abstract(tex: str, abstract_block: str) -> str:
    pattern = re.compile(r"\\begin\{abstract\}.*?\\end\{abstract\}", re.DOTALL)
    return pattern.sub(lambda _: abstract_block, tex)


def inflate_length(tex: str, repeats: int = 10) -> str:
    filler = (" Extensive analysis confirms robust generalization across multilingual benchmarks." * 40)
    page_breaks = "\\newpage\n" * repeats
    return tex.replace(
        "\\section{Results}\n",
        "\\section{Results}\n" + filler + "\n" + page_breaks,
        1,
    )


def remove_acl_template(tex: str) -> str:
    return tex.replace("\\usepackage[review]{acl}\n", "")


def make_final_version(tex: str) -> str:
    return tex.replace("\\usepackage[review]{acl}", "\\usepackage{acl}") + "\n\\aclfinalcopy\n"


def deanonymize(tex: str) -> str:
    tex = tex.replace("\\author{Anonymous ACL submission}", "\\author{Jane Doe \\\\ University of Example \\\\ jane@example.edu}")
    tex = tex.replace(
        "\\section{Ethics Statement}",
        "\\section*{Acknowledgements}\nWe thank our lab for support and the maintainers of https://github.com/example/private-project.\n\n\\section{Ethics Statement}",
        1,
    )
    return tex
