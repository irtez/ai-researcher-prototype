"""Reviewed YAML prompt outputs; updated for eager/deferred MCP availability."""

from contextlib import contextmanager
import copy
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import approval_control
from holosophos import research_iterations

WORKDIR = Path("/tmp/approval-yaml-parity/session")
FIXTURE = Path(__file__).parent / "fixtures/approval-prompt-hashes.json"


def render_cases():
    base_state = {
        "iteration_id": "0002",
        "sources_directory": str(WORKDIR / "sources/0002"),
        "plan_directory": str(WORKDIR / "plans/0002"),
        "stages": {},
    }
    result = {"rules": approval_control.APPROVAL_RULES}
    for sources, plan in ((False, False), (True, False), (False, True), (True, True)):
        for status in ("required", "pending", "approved", "auto_approved"):
            state = copy.deepcopy(base_state)
            state["stages"] = {
                name: {"status": status, "document_path": f"{name}/0002/document.md"}
                for name in ("sources", "plan")
            }

            @contextmanager
            def transaction(_workdir):
                yield WORKDIR, state

            with (
                patch.object(approval_control, "_transaction", transaction),
                patch.object(approval_control, "_refresh"),
                patch.object(
                    research_iterations,
                    "get_iteration",
                    return_value={
                        "kind": "feedback",
                        "settings": {"input_files": ["inputs/new.csv"]},
                    },
                ),
            ):
                key = f"sources={sources},plan={plan},status={status}"
                result[key] = approval_control.approval_prompt(
                    WORKDIR, {"sources": sources, "plan": plan}
                )
    return result


def test_rendered_prompts_match_reviewed_outputs():
    expected = json.loads(FIXTURE.read_text())
    actual = {
        name: hashlib.sha256(text.encode()).hexdigest()
        for name, text in render_cases().items()
    }
    assert actual == expected
