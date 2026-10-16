"""Shared pytest fixtures for canonical writer/base tests."""

from __future__ import annotations

import pytest

from writer.base.tests.fixtures import (
    ACL_GOLD_BIB,
    ACL_GOLD_TEX,
    add_dangling_refs,
    add_unused_bib_entries,
    break_citations,
    duplicate_first_label,
    empty_limitations,
    inject_inconsistent_notation,
    remove_contributions,
    remove_figure_caption,
    remove_metric_values,
    swap_results_and_limitations,
    write_paper,
)


@pytest.fixture
def gold_paper(tmp_path):
    return write_paper(tmp_path / "gold")


@pytest.fixture
def paper_broken_citations(tmp_path):
    return write_paper(tmp_path / "broken_citations", tex=break_citations(ACL_GOLD_TEX))


@pytest.fixture
def paper_unused_bib_entries(tmp_path):
    return write_paper(
        tmp_path / "unused_bib",
        bib=add_unused_bib_entries(ACL_GOLD_BIB),
    )


@pytest.fixture
def paper_missing_captions(tmp_path):
    return write_paper(tmp_path / "missing_captions", tex=remove_figure_caption(ACL_GOLD_TEX))


@pytest.fixture
def paper_dangling_refs(tmp_path):
    return write_paper(tmp_path / "dangling_refs", tex=add_dangling_refs(ACL_GOLD_TEX))


@pytest.fixture
def paper_duplicate_labels(tmp_path):
    return write_paper(tmp_path / "duplicate_labels", tex=duplicate_first_label(ACL_GOLD_TEX))


@pytest.fixture
def paper_metric_mentions_without_values(tmp_path):
    return write_paper(
        tmp_path / "metric_without_values",
        tex=remove_metric_values(ACL_GOLD_TEX),
    )


@pytest.fixture
def paper_inconsistent_notation(tmp_path):
    return write_paper(
        tmp_path / "inconsistent_notation",
        tex=inject_inconsistent_notation(ACL_GOLD_TEX),
    )


@pytest.fixture
def paper_empty_limitations(tmp_path):
    return write_paper(tmp_path / "empty_limitations", tex=empty_limitations(ACL_GOLD_TEX))


@pytest.fixture
def paper_wrong_section_order(tmp_path):
    return write_paper(
        tmp_path / "wrong_order",
        tex=swap_results_and_limitations(ACL_GOLD_TEX),
    )


@pytest.fixture
def paper_no_contributions(tmp_path):
    return write_paper(tmp_path / "no_contributions", tex=remove_contributions(ACL_GOLD_TEX))

