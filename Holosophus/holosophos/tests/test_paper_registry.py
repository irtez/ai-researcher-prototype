import hashlib
import json

import pytest

from research_paper_registry import PaperRegistry, file_sha256


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.delenv("PAPER_REGISTRY_DIR", raising=False)
    (tmp_path / "research_a").mkdir()
    (tmp_path / "research_b").mkdir()
    return PaperRegistry(tmp_path)


def test_arbitrary_paths_and_missing_files_survive_restart(registry):
    record = registry.register(
        "research_a", "first", "results/article.tex", "results/article.pdf"
    )
    assert record["pdf_path"] == "results/article.pdf"
    assert PaperRegistry(registry.workspace).read("research_a") == record
    assert (
        registry.update_status("research_a", "first", "blocked", "PDF missing")["error"]
        == "PDF missing"
    )


@pytest.mark.parametrize(
    "path",
    [
        "../research_b/a.pdf",
        "/tmp/a.pdf",
        "C:\\a.pdf",
        "a/../a.pdf",
        ".paper_registry/a.pdf",
        ".holosophus_ui/a.pdf",
        ".holosophus_ctl/a.pdf",
        "a%20b.pdf",
        ".cache/a.pdf",
        ".ENV/a.pdf",
        " a.pdf",
        "a.txt",
    ],
)
def test_unsafe_pdf_rejected_without_registration(registry, path):
    with pytest.raises(ValueError):
        registry.register("research_a", "first", pdf_path=path)
    assert registry.read("research_a") is None


def test_session_and_symlink_boundaries(registry):
    target = registry.workspace / "research_b" / "article.pdf"
    target.write_bytes(b"external")
    (registry.workspace / "research_a" / "paper.pdf").symlink_to(target)
    with pytest.raises(ValueError):
        registry.register("research_a", "first")
    with pytest.raises(ValueError):
        registry.read("../research_a")
    (registry.workspace / "alias").symlink_to(
        registry.workspace / "research_a", target_is_directory=True
    )
    with pytest.raises(ValueError):
        registry.read("alias")


def test_internal_alias_is_canonicalized(registry):
    root = registry.workspace / "research_a"
    (root / "results").mkdir()
    (root / "results/article.pdf").write_bytes(b"pdf")
    (root / "paper.pdf").symlink_to("results/article.pdf")
    record = registry.register("research_a", "first")
    assert record["pdf_path"] == "results/article.pdf"
    (root / "paper.pdf").unlink()
    assert registry.read("research_a")["pdf_path"] == "results/article.pdf"


def test_review_identity_hash_and_stale_checks(registry):
    root = registry.workspace / "research_a"
    pdf = root / "paper.pdf"
    pdf.write_bytes(b"first")
    registry.register("research_a", "first")
    calls = []

    def save():
        calls.append(True)
        (root / "review.json").write_text("{}")
        (root / "review.md").write_text("review")

    sha = file_sha256(pdf)
    record = registry.record_review(
        "research_a",
        "paper.pdf",
        "review.json",
        "review.md",
        expected_check_run_id="first",
        pdf_sha256=sha,
        save=save,
    )
    assert record["review"]["pdf_sha256"] == sha
    assert len(calls) == 1
    # The same paper may be revised while retaining its previous review.
    registry.register("research_a", "second")
    assert registry.read("research_a")["review"] == record["review"]
    assert registry.update_status("research_a", "first", "ready") is None
    with pytest.raises(ValueError):
        registry.record_review(
            "research_a",
            "paper.pdf",
            "review.json",
            "review.md",
            expected_check_run_id="first",
            pdf_sha256=sha,
            save=save,
        )
    pdf.write_bytes(b"new revision")
    with pytest.raises(ValueError):
        registry.record_review(
            "research_a",
            "paper.pdf",
            "review.json",
            "review.md",
            expected_check_run_id="second",
            pdf_sha256=sha,
            save=save,
        )
    assert len(calls) == 1
    registry.register("research_a", "third", "other.tex", "other.pdf")
    assert registry.read("research_a")["review"] is None
    assert (root / "review.md").read_text() == "review"


def test_deleted_review_files_do_not_invalidate_record(registry):
    root = registry.workspace / "research_a"
    (root / "paper.pdf").write_bytes(b"pdf")
    (root / "review.json").write_text("{}")
    (root / "review.md").write_text("review")
    registry.register("research_a", "first")
    registry.record_review(
        "research_a",
        "paper.pdf",
        "review.json",
        "review.md",
        expected_check_run_id="first",
        pdf_sha256=file_sha256(root / "paper.pdf"),
    )
    (root / "review.md").unlink()
    (root / "paper.pdf").unlink()
    assert registry.read("research_a")["review"]["markdown_path"] == "review.md"


def test_corrupt_or_symlink_metadata_rejected(registry):
    registry.register("research_a", "first")
    metadata = registry.directory / (
        hashlib.sha256(b"research_a").hexdigest() + ".json"
    )
    record = json.loads(metadata.read_text())
    record["session_id"] = "research_b"
    metadata.write_text(json.dumps(record))
    with pytest.raises(ValueError):
        registry.read("research_a")
    metadata.unlink()
    outside = registry.workspace / "outside.json"
    outside.write_text("{}")
    metadata.symlink_to(outside)
    with pytest.raises(ValueError):
        registry.register("research_a", "next")
    assert outside.read_text() == "{}"


def test_unsafe_review_destination_cannot_call_save(registry):
    root = registry.workspace / "research_a"
    (root / "paper.pdf").write_bytes(b"pdf")
    (root / "review.md").symlink_to(registry.workspace / "research_b/review.md")
    registry.register("research_a", "first")
    called = []
    with pytest.raises(ValueError):
        registry.record_review(
            "research_a",
            "paper.pdf",
            "review.json",
            "review.md",
            expected_check_run_id="first",
            pdf_sha256=file_sha256(root / "paper.pdf"),
            save=lambda: called.append(True),
        )
    assert not called


def test_alias_to_service_metadata_is_rejected(registry):
    root = registry.workspace / "research_a"
    (root / ".holosophus_ui").mkdir()
    (root / ".holosophus_ui/private.pdf").write_bytes(b"private")
    (root / "paper.pdf").symlink_to(".holosophus_ui/private.pdf")
    with pytest.raises(ValueError):
        registry.register("research_a", "first")


@pytest.mark.parametrize("changed_file", ["paper.tex", "paper.pdf"])
def test_revision_notice_does_not_reopen_check_or_remove_review(registry, changed_file):
    root = registry.workspace / "research_a"
    for name in ("paper.tex", "paper.pdf"):
        (root / name).write_bytes(b"checked revision")
    (root / "review.json").write_text("{}")
    (root / "review.md").write_text("Review of the checked revision")
    registry.register("research_a", "first")
    registry.record_review(
        "research_a", "paper.pdf", "review.json", "review.md",
        expected_check_run_id="first", pdf_sha256=file_sha256(root / "paper.pdf"),
    )
    fingerprints = {name: file_sha256(root / f"paper.{name}") for name in ("tex", "pdf")}
    ready = registry.update_status("research_a", "first", "ready", checked_fingerprints=fingerprints)
    assert registry.changed_since_check(ready) is False
    (root / changed_file).write_bytes(b"revised after the completed check")
    reopened = PaperRegistry(registry.workspace)
    current = reopened.read("research_a")
    assert current == ready
    assert reopened.changed_since_check(current) is True
    assert reopened.read("research_a") == ready
    (root / changed_file).unlink()
    assert reopened.changed_since_check(current) is True


def test_legacy_ready_does_not_claim_known_freshness(registry):
    registry.register("research_a", "legacy")
    ready = registry.update_status("research_a", "legacy", "ready")
    assert registry.changed_since_check(ready) is None


@pytest.mark.parametrize("fingerprints", [None, [], {}, {"tex": "bad", "pdf": "bad"}])
def test_unavailable_fingerprints_only_disable_the_notice(registry, fingerprints):
    registry.register("research_a", "check")
    ready = registry.update_status("research_a", "check", "ready")
    ready["checked_fingerprints"] = fingerprints
    assert registry.changed_since_check(ready) is None
    assert registry.read("research_a")["status"] == "ready"
