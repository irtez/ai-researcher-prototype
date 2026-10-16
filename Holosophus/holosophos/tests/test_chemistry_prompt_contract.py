"""Check the rendered chemistry profile and its runtime skill references."""

from pathlib import Path
import sys

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from research_profiles import load_chemistry_agents, load_chemistry_manager_prompt


def test_rendered_chemistry_roles_preserve_local_tools_and_neutral_writing():
    agents = load_chemistry_agents()
    for name, agent in agents.items():
        stem = name.replace("-", "_")
        base = yaml.safe_load((ROOT / "holosophos/holosophos/prompts" / f"{stem}.yaml").read_text())
        assert set(base["claude_code"]["tools"]) <= set(agent["tools"])
        assert all("remote" not in tool for tool in agent["tools"])
        assert not any(term in agent["prompt"] for term in ("A*", "at least 8", "NeurIPS", "ICML"))
    assert "mcp__mle_kit__llm_proxy_local" in agents["mle-solver"]["tools"]
    assert "mcp__chem__read_data_file" in agents["librarian"]["tools"]
    assert "mle-solver-remote" not in load_chemistry_manager_prompt()
    registry = ROOT / "holosophos/holosophos/skills/writer/chemistry/skills_registry.yaml"
    assert str(registry) in agents["writer"]["prompt"]
    for skill in yaml.safe_load(registry.read_text())["skills"]:
        text = (registry.parent / skill["path"]).read_text()
        assert not any(term in text for term in ("A*", "at least 8", "NeurIPS", "ICML"))
