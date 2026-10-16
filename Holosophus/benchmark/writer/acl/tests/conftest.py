"""ACL-specific fixtures layered on top of canonical writer test helpers."""

from __future__ import annotations

import pytest

from writer.base.tests.fixtures import (
    ACL_GOLD_TEX,
    deanonymize,
    empty_limitations,
    inflate_length,
    make_final_version,
    make_long_abstract,
    remove_abstract,
    remove_acl_template,
    remove_contributions,
    remove_limitations,
    replace_abstract,
    swap_results_and_limitations,
    write_paper,
)


@pytest.fixture
def gold_paper(tmp_path):
    return write_paper(tmp_path / "gold")


@pytest.fixture
def paper_missing_abstract(tmp_path):
    return write_paper(tmp_path / "missing_abstract", tex=remove_abstract(ACL_GOLD_TEX))


@pytest.fixture
def paper_missing_limitations(tmp_path):
    return write_paper(tmp_path / "missing_limitations", tex=remove_limitations(ACL_GOLD_TEX))


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


@pytest.fixture
def paper_long_abstract(tmp_path):
    return write_paper(
        tmp_path / "long_abstract",
        tex=replace_abstract(ACL_GOLD_TEX, make_long_abstract()),
    )


@pytest.fixture
def paper_over_page_limit(tmp_path):
    return write_paper(tmp_path / "over_page_limit", tex=inflate_length(ACL_GOLD_TEX))


@pytest.fixture
def paper_missing_acl_template(tmp_path):
    return write_paper(
        tmp_path / "missing_acl_template",
        tex=remove_acl_template(ACL_GOLD_TEX),
    )


@pytest.fixture
def paper_non_review_version(tmp_path):
    return write_paper(
        tmp_path / "non_review_version",
        tex=make_final_version(ACL_GOLD_TEX),
    )


@pytest.fixture
def paper_deanonymized(tmp_path):
    return write_paper(tmp_path / "deanonymized", tex=deanonymize(ACL_GOLD_TEX))


@pytest.fixture
def paper_missing_checklist(tmp_path):
    return write_paper(
        tmp_path / "missing_checklist",
        include_checklist=False,
    )

