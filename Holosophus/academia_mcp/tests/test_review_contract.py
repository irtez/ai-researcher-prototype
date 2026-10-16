"""Reviewer payload and saved artifacts, without network or model calls."""

import asyncio
import hashlib
import json
import stat

import pytest
from academia_mcp.tools import review
from PIL import Image
from pydantic import ValidationError


@pytest.fixture
def review_result():
    return {
        "summary": "Корректное учебное сравнение.",
        "quality": {
            "strengths": ["Метод проверен"],
            "weaknesses": [],
            "reasoning": "Данные подтверждены",
            "score": 4,
        },
        "clarity": {
            "strengths": [],
            "weaknesses": ["Нужны единицы"],
            "reasoning": "Уточнить подписи",
            "score": 3,
        },
        "significance": {
            "strengths": [],
            "weaknesses": [],
            "reasoning": "Полезно для задания",
            "score": 2,
        },
        "originality": {
            "strengths": [],
            "weaknesses": [],
            "reasoning": "Известный метод",
            "score": 1,
        },
        "questions": ["Какие единицы использованы?"],
        "limitations": ["Одна выборка"],
        "overall": {"reasoning": "Задача выполнена", "score": 5},
        "confidence": {"reasoning": "Проверено по таблице", "score": 4},
        "format_issues": ["Подпись таблицы"],
    }


@pytest.fixture
def local_review(monkeypatch, tmp_path, review_result):
    captured = {}

    monkeypatch.delenv("PAPER_REGISTRY_DIR", raising=False)

    async def llm(**kwargs):
        captured.update(kwargs)
        return review.ReviewResponse.model_validate(review_result)

    monkeypatch.setattr(review, "get_workspace_dir", lambda: tmp_path)
    monkeypatch.setattr(review.settings, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(review, "llm_acall_structured", llm)
    monkeypatch.setattr(review, "parse_pdf_file", lambda path: ["English article text"])
    monkeypatch.setattr(
        review, "parse_pdf_file_to_images", lambda path: [Image.new("RGB", (2, 2))]
    )
    pdf = tmp_path / "research-session" / "paper.pdf"
    pdf.parent.mkdir()
    pdf.write_bytes(b"%PDF-fixture")
    return pdf, captured


@pytest.fixture
def registered_review(local_review, tmp_path):
    from research_paper_registry import PaperRegistry

    root_pdf, captured = local_review
    paper = root_pdf.parent / "results" / "article-v2.pdf"
    paper.parent.mkdir()
    paper.write_bytes(b"%PDF-registered-article")
    paper.with_suffix(".tex").write_text("Article source")
    registry = PaperRegistry(tmp_path)
    registry.register(
        root_pdf.parent.name,
        "check-1",
        tex_path="results/article-v2.tex",
        pdf_path="results/article-v2.pdf",
    )
    return paper, root_pdf.parent, registry, captured


def test_registered_nested_article_saves_root_review_and_association(
    registered_review, review_result
):
    pdf, session, registry, _ = registered_review
    result = asyncio.run(review.review_pdf_paper(str(pdf)))
    assert result.model_dump() == review_result
    assert json.loads((session / "review.json").read_text()) == review_result
    markdown = (session / "review.md").read_text()
    assert "Итоговая оценка: 5/6" in markdown
    assert "Оригинальность: 1/4" in markdown
    assert not (pdf.parent / "review.json").exists()
    assert pdf.read_bytes() == b"%PDF-registered-article"
    assert registry.read(session.name)["review"] == {
        "pdf_path": "results/article-v2.pdf",
        "pdf_sha256": hashlib.sha256(b"%PDF-registered-article").hexdigest(),
        "json_path": "review.json",
        "markdown_path": "review.md",
    }


@pytest.mark.parametrize("from_session_cwd", [False, True])
def test_registered_article_accepts_workspace_or_session_relative_path(
    registered_review, monkeypatch, from_session_cwd
):
    _, session, registry, _ = registered_review
    filename = "research-session/results/article-v2.pdf"
    if from_session_cwd:
        monkeypatch.chdir(session)
        filename = "results/article-v2.pdf"
    asyncio.run(review.review_pdf_paper(filename))
    assert (session / "review.json").exists()
    assert registry.read(session.name)["review"]["pdf_path"] == "results/article-v2.pdf"


@pytest.mark.parametrize("source_path", ["paper.pdf", "sources/reference.pdf"])
def test_source_pdf_never_replaces_registered_article_review(
    registered_review, source_path, monkeypatch, review_result
):
    pdf, session, registry, _ = registered_review
    asyncio.run(review.review_pdf_paper(str(pdf)))
    previous_record = registry.read(session.name)
    previous_json = (session / "review.json").read_bytes()
    previous_markdown = (session / "review.md").read_bytes()
    source = session / source_path
    source.parent.mkdir(exist_ok=True)
    source.write_bytes(b"%PDF-source")

    async def source_model(**kwargs):
        return review.ReviewResponse.model_validate(
            dict(review_result, summary="An unrelated source review")
        )

    monkeypatch.setattr(review, "llm_acall_structured", source_model)
    asyncio.run(review.review_pdf_paper(str(source)))
    assert (session / "review.json").read_bytes() == previous_json
    assert (session / "review.md").read_bytes() == previous_markdown
    assert registry.read(session.name) == previous_record


@pytest.mark.parametrize("change", ["pdf", "missing", "registration", "path"])
def test_changed_registered_paper_preserves_previous_review(
    registered_review, monkeypatch, review_result, change
):
    pdf, session, registry, _ = registered_review
    asyncio.run(review.review_pdf_paper(str(pdf)))
    previous_json = (session / "review.json").read_bytes()
    previous_markdown = (session / "review.md").read_bytes()
    previous_review = registry.read(session.name)["review"]

    async def changing_model(**kwargs):
        if change == "pdf":
            pdf.write_bytes(b"%PDF-new-revision")
        elif change == "missing":
            pdf.unlink()
        else:
            registry.register(
                session.name,
                "check-2" if change == "registration" else "check-1",
                tex_path="results/article-v2.tex",
                pdf_path="paper.pdf" if change == "path" else "results/article-v2.pdf",
            )
        changed_result = dict(review_result, summary="A delayed review")
        return review.ReviewResponse.model_validate(changed_result)

    monkeypatch.setattr(review, "llm_acall_structured", changing_model)
    with pytest.raises(ValueError, match="changed|registration"):
        asyncio.run(review.review_pdf_paper(str(pdf)))
    assert (session / "review.json").read_bytes() == previous_json
    assert (session / "review.md").read_bytes() == previous_markdown
    record = registry.read(session.name)
    assert record["review"] == (None if change == "path" else previous_review)


def test_failed_registered_review_preserves_outputs_and_association(
    registered_review, monkeypatch
):
    pdf, session, registry, _ = registered_review
    asyncio.run(review.review_pdf_paper(str(pdf)))
    previous_record = registry.read(session.name)
    previous_json = (session / "review.json").read_bytes()
    previous_markdown = (session / "review.md").read_bytes()

    async def failing_model(**kwargs):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(review, "llm_acall_structured", failing_model)
    with pytest.raises(RuntimeError, match="model unavailable"):
        asyncio.run(review.review_pdf_paper(str(pdf)))
    assert (session / "review.json").read_bytes() == previous_json
    assert (session / "review.md").read_bytes() == previous_markdown
    assert registry.read(session.name) == previous_record


def test_registered_default_paper_saves_review_association(local_review, tmp_path):
    from research_paper_registry import PaperRegistry

    pdf, _ = local_review
    registry = PaperRegistry(tmp_path)
    registry.register(pdf.parent.name, "default-check")
    asyncio.run(review.review_pdf_paper(str(pdf)))
    record = registry.read(pdf.parent.name)
    assert record["review"]["pdf_path"] == "paper.pdf"
    assert record["review"]["json_path"] == "review.json"


@pytest.mark.parametrize("session_id", ["research__молекулы__opus__001", "research.v2"])
@pytest.mark.parametrize("registered", [False, True])
def test_existing_session_names_retain_review_saving(
    local_review, tmp_path, session_id, registered
):
    from research_paper_registry import PaperRegistry

    root_pdf, _ = local_review
    session = root_pdf.parent.with_name(session_id)
    root_pdf.parent.rename(session)
    if registered:
        PaperRegistry(tmp_path).register(session_id, "default-check")
    asyncio.run(review.review_pdf_paper(str(session / "paper.pdf")))
    assert (session / "review.json").is_file()


def test_review_uses_configured_shared_registry_directory(
    local_review, tmp_path, monkeypatch
):
    from research_paper_registry import PaperRegistry

    root_pdf, _ = local_review
    monkeypatch.setenv("PAPER_REGISTRY_DIR", str(tmp_path / "shared-metadata"))
    registry = PaperRegistry(tmp_path)
    paper = root_pdf.with_name("article.pdf")
    paper.write_bytes(b"%PDF-article")
    registry.register(root_pdf.parent.name, "check", pdf_path="article.pdf")
    asyncio.run(review.review_pdf_paper(str(paper)))
    assert registry.read(root_pdf.parent.name)["review"]["pdf_path"] == "article.pdf"
    assert (root_pdf.parent / "review.json").is_file()
    assert not (tmp_path / ".paper_registry").exists()


def test_new_registration_during_legacy_review_preserves_previous_outputs(
    local_review, tmp_path, monkeypatch, review_result
):
    from research_paper_registry import PaperRegistry

    pdf, _ = local_review
    for name in ("review.json", "review.md"):
        (pdf.parent / name).write_text("previous review")

    async def registering_model(**kwargs):
        PaperRegistry(tmp_path).register(
            pdf.parent.name, "new-check", pdf_path="results/article.pdf"
        )
        return review.ReviewResponse.model_validate(review_result)

    monkeypatch.setattr(review, "llm_acall_structured", registering_model)
    with pytest.raises(ValueError, match="changed"):
        asyncio.run(review.review_pdf_paper(str(pdf)))
    for name in ("review.json", "review.md"):
        assert (pdf.parent / name).read_text() == "previous review"


@pytest.mark.parametrize("artifact", ["review.json", "review.md"])
def test_registered_output_symlink_rejected_before_model(
    registered_review, tmp_path, artifact
):
    pdf, session, registry, captured = registered_review
    outside = tmp_path / "preserved"
    outside.write_text("preserve")
    (session / artifact).symlink_to(outside)
    with pytest.raises(ValueError, match="review"):
        asyncio.run(review.review_pdf_paper(str(pdf)))
    assert captured == {}
    assert outside.read_text() == "preserve"
    assert registry.read(session.name)["review"] is None


@pytest.mark.parametrize("link_kind", ["pdf", "session"])
def test_other_session_symlink_cannot_claim_registered_paper_review(
    registered_review, tmp_path, link_kind
):
    pdf, session, registry, _ = registered_review
    if link_kind == "pdf":
        linked_pdf = tmp_path / "other-session" / "article.pdf"
        linked_pdf.parent.mkdir()
        linked_pdf.symlink_to(pdf)
    else:
        alias = tmp_path / "other-session"
        alias.symlink_to(session, target_is_directory=True)
        linked_pdf = alias / "results" / "article-v2.pdf"
    asyncio.run(review.review_pdf_paper(str(linked_pdf)))
    assert not (session / "review.json").exists()
    assert registry.read(session.name)["review"] is None


def test_in_session_pdf_alias_matches_registered_article(registered_review):
    pdf, session, registry, _ = registered_review
    alias = session / "article-alias.pdf"
    alias.symlink_to(pdf)
    asyncio.run(review.review_pdf_paper(str(alias)))
    assert (session / "review.json").exists()
    assert registry.read(session.name)["review"]["pdf_path"] == "results/article-v2.pdf"


@pytest.mark.parametrize("corruption", ["invalid-json", "unsafe-path"])
def test_invalid_registry_cannot_fall_back_to_legacy_review(
    registered_review, tmp_path, corruption
):
    _, session, _, captured = registered_review
    (session / "review.json").write_text("preserve")
    record_path = next((tmp_path / ".paper_registry").glob("*.json"))
    if corruption == "invalid-json":
        record_path.write_text("invalid json")
    else:
        record = json.loads(record_path.read_text())
        record["pdf_path"] = "../external.pdf"
        record_path.write_text(json.dumps(record))
    with pytest.raises(ValueError):
        asyncio.run(review.review_pdf_paper(str(session / "paper.pdf")))
    assert captured == {}
    assert (session / "review.json").read_text() == "preserve"


def test_registered_output_path_changed_during_review_preserves_external_file(
    registered_review, tmp_path, monkeypatch, review_result
):
    pdf, session, registry, _ = registered_review
    outside = tmp_path / "outside-review.json"
    outside.write_text("preserve")

    async def replacing_model(**kwargs):
        (session / "review.json").symlink_to(outside)
        return review.ReviewResponse.model_validate(review_result)

    monkeypatch.setattr(review, "llm_acall_structured", replacing_model)
    with pytest.raises(ValueError, match="review"):
        asyncio.run(review.review_pdf_paper(str(pdf)))
    assert outside.read_text() == "preserve"
    assert not (session / "review.md").exists()
    assert registry.read(session.name)["review"] is None


def test_registration_changed_just_before_commit_preserves_existing_outputs(
    registered_review, monkeypatch, review_result
):
    from research_paper_registry import PaperRegistry

    pdf, session, registry, _ = registered_review
    asyncio.run(review.review_pdf_paper(str(pdf)))
    previous_json = (session / "review.json").read_bytes()
    previous_markdown = (session / "review.md").read_bytes()
    record_review = PaperRegistry.record_review

    def race_at_commit(self, *args, **kwargs):
        registry.register(
            session.name,
            "new-check",
            tex_path="results/article-v2.tex",
            pdf_path="results/article-v2.pdf",
        )
        return record_review(self, *args, **kwargs)

    async def delayed_model(**kwargs):
        return review.ReviewResponse.model_validate(
            dict(review_result, summary="Stale review must not be saved")
        )

    monkeypatch.setattr(PaperRegistry, "record_review", race_at_commit)
    monkeypatch.setattr(review, "llm_acall_structured", delayed_model)
    with pytest.raises(ValueError, match="changed|registration"):
        asyncio.run(review.review_pdf_paper(str(pdf)))
    assert (session / "review.json").read_bytes() == previous_json
    assert (session / "review.md").read_bytes() == previous_markdown
    assert registry.read(session.name)["check_run_id"] == "new-check"


def test_changed_legacy_pdf_preserves_previous_review(
    local_review, monkeypatch, review_result
):
    pdf, _ = local_review
    for name in ("review.json", "review.md"):
        (pdf.parent / name).write_text("previous review")

    async def changing_model(**kwargs):
        pdf.write_bytes(b"%PDF-new-revision")
        return review.ReviewResponse.model_validate(review_result)

    monkeypatch.setattr(review, "llm_acall_structured", changing_model)
    with pytest.raises(ValueError, match="changed"):
        asyncio.run(review.review_pdf_paper(str(pdf)))
    for name in ("review.json", "review.md"):
        assert (pdf.parent / name).read_text() == "previous review"


@pytest.mark.parametrize("profile", ["general", "chemistry"])
def test_review_payload_defaults_to_russian_even_for_english_pdf(local_review, profile):
    pdf, captured = local_review
    asyncio.run(review.review_pdf_paper(str(pdf), research_profile=profile))
    parts = captured["messages"][0].content
    instructions = parts[-1]["text"]
    assert "Russian" in instructions
    assert "English article text" in parts[0]["text"]
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert captured["model_name"] == review.settings.REVIEW_MODEL_NAME
    assert captured["response_format"] is review.ReviewResponse
    assert "stated question" in instructions
    if profile == "general":
        assert "requested scope" in instructions
        assert "Low originality alone" in instructions
        assert "known results" not in instructions.lower()
    else:
        assert "measured values" in instructions
        assert "heuristic estimates" in instructions


def test_explicit_review_language_and_task_context_reach_model(local_review):
    pdf, captured = local_review
    asyncio.run(
        review.review_pdf_paper(
            str(pdf),
            response_language="en",
            task_context="Reproduce the Iris comparison",
            target_venue="Requested workshop",
        )
    )
    instructions = captured["messages"][0].content[-1]["text"]
    assert "Write all review text values in English" in instructions
    assert "Reproduce the Iris comparison" in instructions
    assert "Requested workshop" in instructions
    assert "Review" in (pdf.parent / "review.md").read_text()


def test_canonical_review_saves_original_nested_scores(local_review, review_result):
    pdf, _ = local_review
    result = asyncio.run(review.review_pdf_paper("research-session/paper.pdf"))
    assert json.loads((pdf.parent / "review.json").read_text()) == review_result
    assert result.model_dump() == review_result
    markdown = (pdf.parent / "review.md").read_text()
    assert "Корректное учебное сравнение." in markdown
    assert "4/4" in markdown and "3/4" in markdown and "2/4" in markdown
    assert "1/4" in markdown and "5/6" in markdown and "4/5" in markdown
    for value in (
        "Какие единицы использованы?",
        "Одна выборка",
        "Подпись таблицы",
        "Данные подтверждены",
    ):
        assert value in markdown
    assert (pdf.parent / "paper.pdf").read_bytes() == b"%PDF-fixture"


def test_review_from_session_cwd_saves_canonical_artifacts(local_review, monkeypatch):
    pdf, _ = local_review
    monkeypatch.chdir(pdf.parent)
    asyncio.run(review.review_pdf_paper("paper.pdf"))
    assert (pdf.parent / "review.json").exists()
    assert (pdf.parent / "review.md").exists()


@pytest.mark.parametrize(
    "relative_pdf",
    [
        "paper.pdf",
        "examples/paper.pdf",
        "session/examples/paper.pdf",
        "session/source.pdf",
    ],
)
def test_noncanonical_pdf_never_overwrites_adjacent_reviews(
    local_review, tmp_path, relative_pdf
):
    pdf = tmp_path / relative_pdf
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.touch()
    sentinel = pdf.parent / "review.json"
    sentinel.write_text("unrelated review")
    asyncio.run(review.review_pdf_paper(str(pdf)))
    assert sentinel.read_text() == "unrelated review"
    assert not (pdf.parent / "review.md").exists()


@pytest.mark.parametrize("link_kind", ["pdf", "session"])
def test_symlink_input_does_not_overwrite_other_session_reviews(
    local_review, tmp_path, link_kind
):
    pdf, _ = local_review
    if link_kind == "pdf":
        linked_pdf = tmp_path / "another-session" / "paper.pdf"
        linked_pdf.parent.mkdir()
        linked_pdf.symlink_to(pdf)
    else:
        (tmp_path / "another-session").symlink_to(pdf.parent, target_is_directory=True)
        linked_pdf = tmp_path / "another-session" / "paper.pdf"
    sentinel = pdf.parent / "review.json"
    sentinel.write_text("unrelated review")
    asyncio.run(review.review_pdf_paper(str(linked_pdf)))
    assert sentinel.read_text() == "unrelated review"
    assert not (pdf.parent / "review.md").exists()


def test_output_symlink_is_rejected_before_model(local_review, tmp_path):
    pdf, captured = local_review
    outside = tmp_path / "unrelated.json"
    outside.write_text("preserve")
    (pdf.parent / "review.json").symlink_to(outside)
    with pytest.raises(ValueError, match="review"):
        asyncio.run(review.review_pdf_paper(str(pdf)))
    assert captured == {}
    assert outside.read_text() == "preserve"


def test_external_pdf_review_still_works_without_workspace_setting(
    local_review, monkeypatch, review_result
):
    pdf, _ = local_review
    monkeypatch.setattr(review.settings, "WORKSPACE_DIR", None)
    from academia_mcp.files import get_workspace_dir

    monkeypatch.setattr(review, "get_workspace_dir", get_workspace_dir)
    result = asyncio.run(review.review_pdf_paper(str(pdf)))
    assert result.model_dump() == review_result
    assert not (pdf.parent / "review.json").exists()


def test_failed_model_call_preserves_previous_review(local_review, monkeypatch):
    pdf, _ = local_review
    for name in ("review.json", "review.md"):
        (pdf.parent / name).write_text("previous review")

    async def failing_model(**kwargs):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(review, "llm_acall_structured", failing_model)
    with pytest.raises(RuntimeError, match="model unavailable"):
        asyncio.run(review.review_pdf_paper(str(pdf)))
    for name in ("review.json", "review.md"):
        assert (pdf.parent / name).read_text() == "previous review"


def test_saved_reviews_are_readable_by_runner_and_ui(local_review):
    pdf, _ = local_review
    asyncio.run(review.review_pdf_paper(str(pdf)))
    for name in ("review.json", "review.md"):
        assert stat.S_IMODE((pdf.parent / name).stat().st_mode) == 0o644


@pytest.mark.parametrize(
    "field,score",
    [("quality", 0), ("originality", 5), ("overall", 7), ("confidence", 6)],
)
def test_review_rejects_out_of_scale_scores(review_result, field, score):
    review_result[field]["score"] = score
    with pytest.raises(ValidationError):
        review.ReviewResponse.model_validate(review_result)
