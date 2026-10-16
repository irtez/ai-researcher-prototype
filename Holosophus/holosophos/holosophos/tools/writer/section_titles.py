"""Shared Unicode normalization and Russian aliases for writer section checks."""

import re
import unicodedata

RUSSIAN_SECTION_TITLES = {
    "аннотация": "abstract",
    "реферат": "abstract",
    "введение": "introduction",
    "введение и обзор литературы": "introduction",
    "введение и связанные работы": "introduction",
    "введение и предшествующие работы": "introduction",
    "обзор литературы": "related work",
    "обзор связанных работ": "related work",
    "связанные работы": "related work",
    "предшествующие работы": "related work",
    "метод": "method",
    "методы": "method",
    "методика": "method",
    "методология": "method",
    "методы исследования": "method",
    "материалы и методы": "method",
    "данные и методы": "method",
    "экспериментальная часть": "experimental setup",
    "экспериментальная установка": "experimental setup",
    "постановка эксперимента": "experimental setup",
    "условия эксперимента": "experimental setup",
    "эксперименты": "experiments",
    "результаты": "results",
    "основные результаты": "results",
    "результаты и обсуждение": "results",
    "анализ": "analysis",
    "анализ ошибок": "error analysis",
    "дополнительный анализ": "additional analysis",
    "абляции": "ablations",
    "обсуждение": "discussion",
    "обсуждение результатов": "discussion",
    "ограничения": "limitations",
    "ограничения исследования": "limitations",
    "этика": "ethics",
    "этические аспекты": "ethics",
    "воспроизводимость": "reproducibility",
    "заключение": "conclusion",
    "выводы": "conclusion",
    "список литературы": "references",
    "литература": "references",
    "список использованных источников": "references",
    "список использованной литературы": "references",
    "библиография": "bibliography",
    "благодарности": "acknowledgments",
    "благодарность": "acknowledgments",
}

# Combined headings keep the type of their leading section. These normalized
# aliases are shared by the LaTeX checks and the PDF length analyzer.
COMBINED_SECTION_TITLES = {
    "introduction and related work": "introduction",
    "introduction related work": "introduction",
    "introduction and literature review": "introduction",
    "introduction literature review": "introduction",
    "introduction and background": "introduction",
    "introduction background": "introduction",
    "results and discussion": "results",
    "results discussion": "results",
}


def normalize_section_title(title: str) -> str:
    title = unicodedata.normalize("NFKC", title).casefold().replace("ё", "е")
    return re.sub(r"[\W_]+", " ", title).strip()


def translate_section_title(normalized: str) -> str:
    """Map known Russian/combined headings to section labels; retain unknown text."""
    for prefix in ("приложение", "приложения"):
        if normalized == prefix or normalized.startswith(prefix + " "):
            return "appendix" + normalized[len(prefix) :]
    return RUSSIAN_SECTION_TITLES.get(
        normalized, COMBINED_SECTION_TITLES.get(normalized, normalized)
    )
