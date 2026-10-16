"""Tests for ACL-specific compliance metrics."""

from writer.acl.formatting import (
    ACLAbstractLengthCompliance,
    ACLAnonymizationCompliance,
    ACLFormatCompliance,
    ACLPageLimitCompliance,
    ACLResponsibleNLPChecklistCompliance,
    ACLReviewVersionCompliance,
)


def test_acl_page_limit_compliance_passes_on_gold(gold_paper):
    result = ACLPageLimitCompliance().evaluate(gold_paper)[0]
    assert result.value == 1.0


def test_acl_page_limit_compliance_prefers_pdf_page_count(gold_paper):
    gold_paper.root.joinpath("paper.pdf").write_bytes(
        b"%PDF-1.4\n"
        b"1 0 obj <</Type /Page>> endobj\n"
        b"2 0 obj <</Type /Page>> endobj\n"
        b"3 0 obj <</Type /Page>> endobj\n"
        b"4 0 obj <</Type /Pages>> endobj\n"
    )

    result = ACLPageLimitCompliance().evaluate(gold_paper)[0]

    assert result.value == 1.0
    assert result.details["page_count_source"] in {"pypdf", "pdfinfo", "regex"}
    assert result.details["measured_pdf_pages"] == 3
    assert result.details["pages_for_limit"] == 3


def test_acl_page_limit_compliance_detects_long_paper(paper_over_page_limit):
    result = ACLPageLimitCompliance().evaluate(paper_over_page_limit)[0]
    assert result.value == 0.0


def test_acl_abstract_length_compliance_detects_long_abstract(paper_long_abstract):
    result = ACLAbstractLengthCompliance().evaluate(paper_long_abstract)[0]
    assert result.value == 0.0
    assert result.details["abstract_word_count"] > result.details["word_limit"]


def test_acl_format_compliance_detects_missing_template(paper_missing_acl_template):
    result = ACLFormatCompliance().evaluate(paper_missing_acl_template)[0]
    assert result.value == 0.0


def test_acl_review_version_compliance_detects_final_copy(paper_non_review_version):
    result = ACLReviewVersionCompliance().evaluate(paper_non_review_version)[0]
    assert result.value == 0.0


def test_acl_anonymization_compliance_detects_identity_leaks(paper_deanonymized):
    result = ACLAnonymizationCompliance().evaluate(paper_deanonymized)[0]
    assert result.value == 0.0
    assert result.details["violations"]


def test_acl_responsible_nlp_checklist_compliance_detects_missing_artifact(paper_missing_checklist):
    result = ACLResponsibleNLPChecklistCompliance().evaluate(paper_missing_checklist)[0]
    assert result.value == 0.0

