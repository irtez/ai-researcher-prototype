"""Text quality metrics and plain-text extraction helpers."""

from __future__ import annotations

import re

from .base import BaseMetric, FailType, MetricResult, PaperPackage
from .defaults import BASE_NOTATION_POLICY
from .profiles import NotationPolicy


def extract_plain_text(tex: str) -> str:
    """Best-effort extraction of readable text from LaTeX source."""

    text = tex
    doc_start = text.find("\\begin{document}")
    if doc_start >= 0:
        text = text[doc_start:]

    text = re.sub(r"(?<!\\)%.*", "", text)
    for env in (
        "equation",
        "equation*",
        "align",
        "align*",
        "lstlisting",
        "verbatim",
        "tikzpicture",
        "CCSXML",
        "tabular",
        "tabular*",
    ):
        text = re.sub(
            rf"\\begin\{{{re.escape(env)}\}}.*?\\end\{{{re.escape(env)}\}}",
            "",
            text,
            flags=re.DOTALL,
        )

    text = re.sub(r"\$[^$]+\$", "", text)
    text = re.sub(r"\\\(.*?\\\)", "", text, flags=re.DOTALL)
    text = re.sub(r"\\\[.*?\\\]", "", text, flags=re.DOTALL)
    text = re.sub(
        r"\\(?:textbf|textit|emph|underline|texttt)\{([^}]*)\}",
        r"\1",
        text,
    )
    text = re.sub(r"\\(?:cite[tp]?|ref|autoref|label|eqref|cref|Cref)\{[^}]*\}", "", text)
    text = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])*(?:\{[^}]*\})*", "", text)
    text = re.sub(r"[{}]", "", text)
    return re.sub(r"\s+", " ", text).strip()


DOMAIN_WHITELIST = {
    "ablation",
    "ablations",
    "acl",
    "api",
    "apis",
    "arxiv",
    "benchmarking",
    "bert",
    "cnns",
    "codeact",
    "codearkt",
    "cpus",
    "cpu",
    "dataset",
    "datasets",
    "decoder",
    "decoders",
    "embeddings",
    "encoders",
    "finetuned",
    "finetuning",
    "github",
    "gpt",
    "gpu",
    "gpus",
    "holosophos",
    "hyperparameter",
    "hyperparameters",
    "json",
    "langfuse",
    "llm",
    "llms",
    "mcp",
    "naacl",
    "numpy",
    "paperbanana",
    "pretrained",
    "pretraining",
    "pytorch",
    "relu",
    "reproducibility",
    "rnns",
    "scalability",
    "softmax",
    "tensorflow",
    "tokenizer",
    "tokenizers",
    "transformers",
    "yaml",
}


class SpellingErrors(BaseMetric):
    """Count likely misspelled words using pyspellchecker when available."""

    def evaluate(self, pkg: PaperPackage) -> list[MetricResult]:
        try:
            from spellchecker import SpellChecker
        except ImportError:
            return [
                MetricResult(
                    name="spelling_errors",
                    value=0,
                    fail_type=FailType.SOFT,
                    details={"message": "pyspellchecker not installed, skipping"},
                )
            ]

        words = re.findall(r"\b[a-zA-Z]{3,}\b", extract_plain_text(pkg.raw_tex))
        spell = SpellChecker()
        spell.word_frequency.load_words(DOMAIN_WHITELIST)
        unknown = spell.unknown(word.lower() for word in words)
        filtered_unknown = {word for word in unknown if not word.isupper() and len(word) > 2}
        return [
            MetricResult(
                name="spelling_errors",
                value=len(filtered_unknown),
                fail_type=FailType.SOFT,
                details={"unknown_words": sorted(filtered_unknown)[:50]},
            )
        ]


class GrammarErrors(BaseMetric):
    """Count grammar issues using language-tool-python when available."""

    def evaluate(self, pkg: PaperPackage) -> list[MetricResult]:
        try:
            import language_tool_python
        except ImportError:
            return [
                MetricResult(
                    name="grammar_errors",
                    value=0,
                    fail_type=FailType.SOFT,
                    details={"message": "language-tool-python not installed, skipping"},
                )
            ]

        text = extract_plain_text(pkg.raw_tex)
        if len(text) > 50_000:
            text = text[:50_000]

        try:
            tool = language_tool_python.LanguageTool("en-US")
        except Exception as exc:  # pragma: no cover - environment-dependent fallback
            return [
                MetricResult(
                    name="grammar_errors",
                    value=0,
                    fail_type=FailType.SOFT,
                    details={"message": f"language tool unavailable: {exc}"},
                )
            ]

        try:
            matches = tool.check(text)
        except Exception as exc:  # pragma: no cover - environment-dependent fallback
            return [
                MetricResult(
                    name="grammar_errors",
                    value=0,
                    fail_type=FailType.SOFT,
                    details={"message": f"language tool check failed: {exc}"},
                )
            ]
        finally:
            tool.close()

        errors = [match for match in matches if match.ruleIssueType != "style"]
        return [
            MetricResult(
                name="grammar_errors",
                value=len(errors),
                fail_type=FailType.SOFT,
                details={
                    "error_count": len(errors),
                    "sample_errors": [
                        {
                            "message": match.message,
                            "context": match.context,
                            "rule": match.ruleId,
                        }
                        for match in errors[:20]
                    ],
                },
            )
        ]


class NotationConsistencyMetric(BaseMetric):
    """Count inconsistent terminology variants configured by a profile."""

    def __init__(self, policy: NotationPolicy) -> None:
        self.policy = policy

    def evaluate(self, pkg: PaperPackage) -> list[MetricResult]:
        text = extract_plain_text(pkg.raw_tex).lower()
        issues: list[str] = []
        for group in self.policy.variant_groups:
            found = [variant for variant in group if variant in text]
            if len(found) > 1:
                counts = {variant: text.count(variant) for variant in found}
                issues.append(f"inconsistent: {counts}")
        return [
            MetricResult(
                name=self.policy.metric_name,
                value=len(issues),
                fail_type=FailType.SOFT,
                details={"issues": issues},
            )
        ]


class NotationConsistency(NotationConsistencyMetric):
    def __init__(self) -> None:
        super().__init__(policy=BASE_NOTATION_POLICY)


__all__ = [
    "DOMAIN_WHITELIST",
    "GrammarErrors",
    "NotationConsistency",
    "NotationConsistencyMetric",
    "SpellingErrors",
    "extract_plain_text",
]

