"""Exercise profile prompt selection at the mocked LLM boundary; no model calls."""

import pytest

from academia_mcp.tools import research_proposals as proposals
from academia_mcp.tools import review


@pytest.mark.parametrize(
    "session_id", [None, "chem-session", "research__молекулы__opus__001"]
)
def test_download_returns_existing_workspace_path(monkeypatch, tmp_path, session_id):
    monkeypatch.setattr(review, "get_workspace_dir", lambda: tmp_path)
    monkeypatch.setattr(
        review, "download_pdf", lambda url, path: path.write_bytes(b"%PDF-fixture")
    )
    kwargs = {} if session_id is None else {"session_id": session_id}
    filename = review.download_pdf_paper("https://arxiv.org/pdf/2409.06820", **kwargs)
    assert (tmp_path / filename).read_bytes() == b"%PDF-fixture"
    assert filename == (
        "2409.06820.pdf" if session_id is None else f"{session_id}/2409.06820.pdf"
    )


def test_download_rejects_session_escape_before_network(monkeypatch, tmp_path):
    monkeypatch.setattr(review, "get_workspace_dir", lambda: tmp_path)
    monkeypatch.setattr(
        review, "download_pdf", lambda *args: pytest.fail("Network must not be reached")
    )
    with pytest.raises(ValueError):
        review.download_pdf_paper(
            "https://example.invalid/paper", session_id="../other"
        )


@pytest.mark.parametrize("profile", ["general", "chemistry"])
async def test_generation_selects_profile_and_skips_ml_examples(monkeypatch, profile):
    captured = {}

    async def llm(**kwargs):
        captured.update(kwargs)
        return proposals.GenerateResearchProposalResponse(proposals=[])

    def dataset():
        if profile == "chemistry":
            pytest.fail("chemistry must not load the ML example dataset")
        return [
            {"bit": "example-bit", "flip": "example-flip", "spark": "example-spark"}
        ]

    monkeypatch.setattr(proposals, "llm_acall_structured", llm)
    monkeypatch.setattr(proposals.ProposalDataset, "get_dataset", dataset)
    await proposals.generate_research_proposals(
        "compare supplied molecules",
        additional_context="source-table.csv",
        **({"research_profile": profile} if profile == "chemistry" else {}),
    )
    system, user = [m.content for m in captured["messages"]]
    assert "compare supplied molecules" in user
    assert "source-table.csv" in user
    assert captured["response_format"] is proposals.GenerateResearchProposalResponse
    if profile == "general":
        assert system == proposals.SYSTEM_IMPROVEMENT_PROMPT
        assert "example-bit" in user
    else:
        assert "chemistry" in system.lower()
        assert "example-bit" not in user
        assert "top-tier" not in system
        assert "at least three" not in user


@pytest.mark.parametrize("profile", ["general", "chemistry"])
async def test_scoring_selects_profile_with_same_schema(monkeypatch, profile):
    captured = {}

    async def llm(**kwargs):
        captured.update(kwargs)
        return proposals.ScoreResearchProposalsResponse(proposals=[])

    monkeypatch.setattr(proposals, "llm_acall_structured", llm)
    await proposals.score_research_proposals(
        [{"spark": "Molecule comparison"}],
        **({"research_profile": profile} if profile == "chemistry" else {}),
    )
    system, user = [m.content for m in captured["messages"]]
    assert "Molecule comparison" in user
    assert captured["response_format"] is proposals.ScoreResearchProposalsResponse
    if profile == "general":
        assert system == proposals.SCORE_SYSTEM_PROMPT
    else:
        assert "chemistry" in system.lower()
        assert "NeurIPS" not in system + user
        assert "1-4" in user and "1-5" in user


@pytest.mark.parametrize("profile", ["general", "chemistry"])
async def test_review_selects_profile_with_same_scores(monkeypatch, profile, tmp_path):
    captured = {}

    async def llm(**kwargs):
        captured.update(kwargs)
        return review.ReviewResponse.model_construct(summary="Local fixture")

    monkeypatch.setattr(review, "llm_acall_structured", llm)
    monkeypatch.setattr(review, "parse_pdf_file_to_images", lambda path: [])
    monkeypatch.setattr(
        review,
        "parse_pdf_file",
        lambda path: ["Measured solubility differs from predicted solubility."],
    )
    pdf = tmp_path / "paper.pdf"
    pdf.touch()
    await review.review_pdf_paper(
        str(pdf), **({"research_profile": profile} if profile == "chemistry" else {})
    )
    content = captured["messages"][0].content
    instructions = content[-1]["text"]
    assert "Measured solubility" in content[0]["text"]
    assert captured["response_format"] is review.ReviewResponse
    if profile == "general":
        assert instructions.endswith(review.PROMPT)
    else:
        assert "chemistry" in instructions.lower()
        assert "NeurIPS" not in instructions
        assert "1-4" in instructions and "1-6" in instructions and "1-5" in instructions
