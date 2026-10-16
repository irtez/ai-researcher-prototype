"""Static tests for the Writer prompt contract, skill registry, and skill content.

These tests verify the Writer's prompt-level workflow contract without
invoking any LLM. They check that the prompt YAML, skill registry, and
skill markdown files contain the required sections and guidance.
"""

from pathlib import Path
from typing import Any, cast

import yaml

HOLOSOPHOS_REPO = Path(__file__).resolve().parent.parent
HOLOSOPHOS_PKG = HOLOSOPHOS_REPO / "holosophos"
HOLOSOPHUS_ROOT = HOLOSOPHOS_REPO.parent
WRITER_YAML = HOLOSOPHOS_PKG / "prompts" / "writer.yaml"
SKILLS_DIR = HOLOSOPHOS_PKG / "skills" / "writer"
REGISTRY_FILE = SKILLS_DIR / "skills_registry.yaml"
WRITER_DOCS = [
    HOLOSOPHUS_ROOT / "docs" / "manager_to_writer_handoff.md",
    HOLOSOPHUS_ROOT / "docs" / "run_writer_on_distilled_benchmark.md",
    HOLOSOPHUS_ROOT / "docs" / "tools" / "writer" / "README.md",
    HOLOSOPHUS_ROOT / "docs" / "tools" / "writer" / "content_placement.md",
    HOLOSOPHUS_ROOT / "docs" / "tools" / "writer" / "paper_length.md",
    HOLOSOPHUS_ROOT / "docs" / "tools" / "writer" / "section_order.md",
    HOLOSOPHUS_ROOT / "docs" / "writer-agent-how-it-works.md",
    HOLOSOPHUS_ROOT / "docs" / "writer-skills-workflow.md",
    HOLOSOPHUS_ROOT / "docs" / "writer_agent_input_output_examples.md",
]

GENERAL_SKILL_IDS = [
    "paper_standard",
    "a_star_conference_paper",
    "skeleton_planner",
    "section_self_check",
    "global_writer_check",
]

SECTION_SKILL_IDS = [
    "abstract_writer",
    "introduction_writer",
    "related_work_writer",
    "method_writer",
    "experimental_setup_writer",
    "results_writer",
    "analysis_ablations_writer",
    "limitations_writer",
    "ethics_broader_impact_writer",
    "conclusion_writer",
    "appendix_writer",
]

ALL_SKILL_IDS = GENERAL_SKILL_IDS + SECTION_SKILL_IDS


def _load_writer_yaml() -> dict[str, Any]:
    return cast(dict[str, Any], yaml.safe_load(WRITER_YAML.read_text()))


def _load_registry() -> list[dict[str, Any]]:
    data = yaml.safe_load(REGISTRY_FILE.read_text())
    return cast(list[dict[str, Any]], data["skills"])


def _writer_guidelines() -> str:
    """Return the full guidelines text from writer.yaml."""
    return cast(str, _load_writer_yaml()["canon"]["guidelines"])


# ── Prompt contract: writer.yaml ────────────────────────────────────


class TestWriterPromptContract:
    """Verify writer.yaml contains the required workflow sections."""

    def test_required_outputs_are_canonical(self) -> None:
        guidelines = _writer_guidelines()
        for artifact in ["paper.tex", "references.bib", "paper.pdf"]:
            assert artifact in guidelines, f"Required output {artifact!r} missing"
        assert "<paper_name>.tex" not in guidelines
        assert "<paper_name>.pdf" not in guidelines

        codearkt_examples = _load_writer_yaml()["codearkt"]["examples"]
        assert '"report.tex"' not in codearkt_examples
        assert '"report.pdf"' not in codearkt_examples

    def test_canonical_artifacts_are_updated_in_place(self) -> None:
        guidelines = _writer_guidelines().lower()
        assert "one canonical paper revision" in guidelines
        assert "selected latex and bibliography in place" in guidelines
        assert "regenerate the selected pdf" in guidelines
        assert "register them in start_writer_check" in guidelines

        codearkt_extras = _load_writer_yaml()["codearkt"]["system_extras"].lower()
        assert "do not pass `session_id` or `agent_name`" in codearkt_extras
        assert "only in start_writer_check" in codearkt_extras

    def test_writer_docs_use_canonical_artifact_names(self) -> None:
        forbidden_names = [
            "<paper_name>.tex",
            "<paper_name>.pdf",
            "paper_name.tex",
            "paper_name.pdf",
            "report.tex",
            "report.pdf",
            "rct_paper.tex",
            "rct_paper.pdf",
            "output.pdf",
        ]
        for path in WRITER_DOCS:
            content = path.read_text(encoding="utf-8")
            for forbidden_name in forbidden_names:
                assert (
                    forbidden_name not in content
                ), f"Noncanonical artifact {forbidden_name!r} remains in {path}"

    def test_staged_workflow_present(self) -> None:
        guidelines = _writer_guidelines()
        assert "Full-paper writing workflow" in guidelines

    def test_local_task_exception(self) -> None:
        guidelines = _writer_guidelines()
        assert "LOCAL" in guidelines or "local" in guidelines.lower()
        assert (
            "skip the staged workflow" in guidelines.lower()
            or "direct Writer behavior" in guidelines
        )

    def test_skeleton_requirement(self) -> None:
        guidelines = _writer_guidelines()
        assert "paper_skeleton.md" in guidelines
        assert "central claim" in guidelines.lower()
        assert "contribution" in guidelines.lower()

    def test_section_self_check_requirement(self) -> None:
        guidelines = _writer_guidelines()
        assert "section_self_check.md" in guidelines
        assert "self-check" in guidelines.lower() or "Section self-check" in guidelines

    def test_global_check_requirement(self) -> None:
        guidelines = _writer_guidelines()
        assert "global_writer_check.md" in guidelines
        assert "Global Writer check" in guidelines

    def test_anti_hallucination_invariants(self) -> None:
        guidelines = _writer_guidelines()
        assert "Anti-hallucination" in guidelines or "anti-hallucination" in guidelines
        assert "may NOT invent" in guidelines or "may not invent" in guidelines.lower()
        for item in [
            "experimental results",
            "citations",
            "baselines",
            "datasets",
            "metrics",
        ]:
            assert item in guidelines.lower(), f"Anti-hallucination rule for {item!r} missing"

    def test_previous_sections_read_only(self) -> None:
        guidelines = _writer_guidelines()
        assert "read-only context" in guidelines.lower()
        assert "Do NOT silently rewrite previous sections" in guidelines

    def test_selective_skill_loading(self) -> None:
        guidelines = _writer_guidelines()
        assert (
            "Do NOT load all section-specific skills" in guidelines
            or "Do NOT load all section skills" in guidelines
        )

    def test_section_specific_skill_usage(self) -> None:
        guidelines = _writer_guidelines()
        assert "section-specific" in guidelines.lower()
        assert (
            "ONLY the section-specific" in guidelines
            or "ONLY the section-specific skill" in guidelines
        )

    def test_skill_registry_path_consistent(self) -> None:
        """Both codearkt and claude_code sections should point to the same registry."""
        raw = WRITER_YAML.read_text()
        assert "skills/writer/skills_registry.yaml" in raw
        assert (
            "prompts/skills/writer" not in raw
        ), "Stale prompts/skills/writer path found in writer.yaml"

    def test_server_owned_writer_quality_gate(self) -> None:
        guidelines = _writer_guidelines()
        for value in [
            "start_writer_check",
            "section_order",
            "content_placement",
            "paper_length",
            "finish_writer_check",
            "check_run_id",
            "pass_number",
        ]:
            assert value in guidelines
        assert "all three" in guidelines.lower()
        assert "server" in guidelines.lower()
        assert "does not consume" in guidelines.lower()
        assert "blocked" in guidelines.lower()

    def test_writer_length_contract_matches_server_policy(self) -> None:
        sources = [
            _writer_guidelines(),
            (SKILLS_DIR / "paper_standard.md").read_text(),
            (SKILLS_DIR / "skeleton_planner.md").read_text(),
            (SKILLS_DIR / "global_writer_check.md").read_text(),
        ]
        for source in sources:
            normalized = source.lower().replace("–", "-")
            assert "no fixed minimum" in normalized
            assert "requested length" in normalized
            assert "references" in normalized
            assert "at least 8 pages" not in normalized

    def test_claude_writer_has_writer_tools_mcp(self) -> None:
        claude = _load_writer_yaml()["claude_code"]

        assert "writer_tools" in claude["mcpServers"]
        assert {
            "mcp__writer_tools__start_writer_check",
            "mcp__writer_tools__section_order",
            "mcp__writer_tools__content_placement",
            "mcp__writer_tools__paper_length",
            "mcp__writer_tools__finish_writer_check",
        } <= set(claude["tools"])


# ── Skill registry integrity ────────────────────────────────────────


class TestSkillRegistry:
    """Verify the skill registry is complete and consistent."""

    def test_registry_file_exists(self) -> None:
        assert REGISTRY_FILE.exists()

    def test_all_required_skills_registered(self) -> None:
        registry = _load_registry()
        registered_ids = {s["id"] for s in registry}
        for skill_id in ALL_SKILL_IDS:
            assert skill_id in registered_ids, f"Skill {skill_id!r} not in registry"

    def test_all_registered_paths_exist(self) -> None:
        registry = _load_registry()
        for skill in registry:
            skill_path = HOLOSOPHUS_ROOT / skill["path"]
            assert skill_path.exists(), f"Registered path does not exist: {skill['path']}"

    def test_no_stale_prompts_skills_paths(self) -> None:
        registry = _load_registry()
        for skill in registry:
            assert (
                "prompts/skills/writer" not in skill["path"]
            ), f"Stale prompts/skills/writer path in registry: {skill['path']}"

    def test_each_skill_has_description_and_triggers(self) -> None:
        registry = _load_registry()
        for skill in registry:
            assert skill.get("description"), f"Skill {skill['id']!r} missing description"
            assert skill.get("triggers"), f"Skill {skill['id']!r} missing triggers"
            assert len(skill["triggers"]) >= 1


# ── Skill content coverage ──────────────────────────────────────────

REQUIRED_SECTION_SKILL_HEADINGS = [
    "Purpose",
    "Role in an Academic Paper",
    "Required Content",
    "Common Failure Modes",
    "How to Use the Skeleton",
    "Previous Sections as Context",
    "What This Section Should NOT Do",
    "Section-Specific Self-Check",
]


class TestSectionSkillContent:
    """Verify section-specific skills contain required guidance categories."""

    def test_section_skills_contain_required_headings(self) -> None:
        registry = _load_registry()
        for skill_id in SECTION_SKILL_IDS:
            skill = next(s for s in registry if s["id"] == skill_id)
            content = (HOLOSOPHUS_ROOT / skill["path"]).read_text()
            for heading in REQUIRED_SECTION_SKILL_HEADINGS:
                assert (
                    heading in content
                ), f"Skill {skill_id!r} missing required heading: {heading!r}"

    def test_general_skills_have_purpose(self) -> None:
        registry = _load_registry()
        for skill_id in GENERAL_SKILL_IDS:
            skill = next(s for s in registry if s["id"] == skill_id)
            content = (HOLOSOPHUS_ROOT / skill["path"]).read_text()
            assert "## Purpose" in content, f"General skill {skill_id!r} missing Purpose section"

    def test_skeleton_planner_has_required_fields(self) -> None:
        content = (SKILLS_DIR / "skeleton_planner.md").read_text()
        for field in [
            "Working title",
            "Target venue",
            "Central claim",
            "Problem",
            "Gap",
            "Main idea",
            "Contribution list",
            "Section list",
            "Missing information",
            "Appendix plan",
            "Reviewer-facing risks",
        ]:
            assert (
                field.lower() in content.lower()
            ), f"Skeleton planner missing required field: {field!r}"

    def test_section_self_check_has_action_requirement(self) -> None:
        content = (SKILLS_DIR / "section_self_check.md").read_text()
        assert "Revise immediately" in content
        assert "TODO" in content

    def test_global_writer_check_has_checklist(self) -> None:
        content = (SKILLS_DIR / "global_writer_check.md").read_text()
        assert "central claim consistency" in content.lower()
        assert "contribution" in content.lower()
        assert "TODO" in content

    def test_global_writer_check_has_mcp_quality_gate(self) -> None:
        content = (SKILLS_DIR / "global_writer_check.md").read_text()
        for value in [
            "start_writer_check",
            "section_order",
            "content_placement",
            "paper_length",
            "finish_writer_check",
        ]:
            assert value in content
        for criterion in [
            "no section-order violations",
            "no forbidden or discouraged content placements",
            "main-content",
            "blocked",
        ]:
            assert criterion in content.lower()
