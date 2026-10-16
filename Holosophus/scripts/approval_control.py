"""Session-local document approvals and ordinary operator questions.

The manager's delegation hook enforces enabled stages. Decisions are tied to an
immutable Markdown snapshot and checked again against the current file. The
control directory is durable bookkeeping, not a security boundary against a
model with arbitrary filesystem access.
"""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import cc_run_control as ctl
import yaml
from pydantic import BaseModel, ConfigDict, Field

_APPROVAL_TEXT = yaml.safe_load(
    (Path(__file__).resolve().parents[1] / "holosophos/holosophos/prompts/shared/approvals.yaml")
    .read_text(encoding="utf-8")
)


class ApprovalPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sources: bool = False
    plan: bool = False
    auto_approve_after_seconds: Annotated[int, Field(strict=True, gt=0)] | None = None

    @property
    def enabled(self) -> bool:
        return self.sources or self.plan


def normalize_policy(value=None, *, hitl: bool = False) -> ApprovalPolicy:
    if value is None:
        return ApprovalPolicy(sources=hitl, plan=hitl)
    return ApprovalPolicy.model_validate(value)


_STATE_FILE = "approvals.json"
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()
_APPROVED = {"approved", "auto_approved"}


@contextmanager
def _transaction(workdir: Path):
    """No awaits in a transaction; serialize UI, tool and timer across processes."""
    root = Path(workdir).resolve()
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(str(root), threading.RLock())
    with lock:
        directory = ctl.ctl_dir(root)
        with (directory / "approvals.lock").open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                path = directory / _STATE_FILE
                try:
                    state = json.loads(path.read_text(encoding="utf-8"))
                    if not isinstance(state, dict):
                        state = {}
                except (OSError, ValueError):
                    state = {}
                state.setdefault("stages", {})
                state.setdefault("decisions", {})
                before = json.dumps(state, sort_keys=True)
                try:
                    yield root, state
                finally:
                    if json.dumps(state, sort_keys=True) != before:
                        ctl._atomic_write(
                            path, json.dumps(state, ensure_ascii=False, indent=2)
                        )
                        _sync_history(root, state)
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)


def _sync_history(workdir: Path, state: dict) -> None:
    if state.get("iteration_id"):
        from holosophos import research_iterations as history

        history.record_approvals(
            workdir, state["iteration_id"], state["stages"], state["decisions"]
        )


def _check_scope(state: dict, iteration_id: str | None, policy=None) -> None:
    current = state.get("iteration_id")
    if iteration_id is not None and iteration_id != current:
        raise ValueError("Stale iteration: this tool belongs to another iteration")
    if current and not state.get("iteration_open"):
        raise ValueError("This iteration has finished; start a new feedback iteration")
    if (
        current
        and policy is not None
        and normalize_policy(policy).model_dump() != state["policy"]
    ):
        raise ValueError("Approval policy is fixed for the current iteration")


def begin_iteration(workdir: Path, iteration_id: str, policy) -> None:
    from holosophos import research_iterations as history

    item = history.ensure_preparation_paths(workdir, iteration_id)
    policy = normalize_policy(policy)
    with _transaction(workdir) as (root, state):
        if state.get("iteration_id") == iteration_id:
            if state["policy"] != policy.model_dump():
                raise ValueError("Approval policy is fixed for the current iteration")
            state["iteration_open"] = True
            _refresh(root, state)
            return
        question = ctl.read_question(root)
        if question:
            _finish(root, state, question, "cancelled")
        _refresh(root, state)
        _sync_history(root, state)
        state["stages"].pop("plan", None)
        if item["kind"] == "feedback" and item.get("settings", {}).get("input_files"):
            # New inputs invalidate a carried decision even when sources approval
            # is disabled now; re-enabling it later must not revive old consent.
            state["stages"].pop("sources", None)
        state.update(
            iteration_id=iteration_id,
            iteration_open=True,
            plan_directory=item["plan_directory"],
            sources_directory=item["sources_directory"],
            policy=policy.model_dump(),
        )


def close_iteration(workdir: Path, iteration_id: str) -> None:
    with _transaction(workdir) as (root, state):
        if state.get("iteration_id") != iteration_id:
            return
        question = ctl.read_question(root)
        if question:
            _finish(root, state, question, "cancelled")
        _refresh(root, state)
        state["iteration_open"] = False


def _approved(state: dict, stage: str) -> bool:
    item = state["stages"].get(stage, {})
    return item.get("status") in _APPROVED and (
        stage != "plan"
        or not state.get("iteration_id")
        or item.get("iteration_id") == state["iteration_id"]
    )


def validate_completion(workdir: Path, iteration_id: str, policy) -> None:
    policy = normalize_policy(policy)
    if not policy.enabled:
        return
    with _transaction(workdir) as (root, state):
        _check_scope(state, iteration_id, policy)
        _refresh(root, state)
        for stage in ("sources", "plan"):
            if getattr(policy, stage) and not _approved(state, stage):
                raise ValueError(
                    f"Missing {stage} approval for this iteration; call request_approval before finishing"
                )


def _document(workdir: Path, document_path: str) -> dict:
    if not isinstance(document_path, str) or not document_path.strip():
        raise ValueError(
            "document_path must name a nonempty Markdown file inside the session"
        )
    candidate = Path(document_path)
    path = (candidate if candidate.is_absolute() else workdir / candidate).resolve()
    try:
        relative = path.relative_to(workdir.resolve())
    except ValueError as exc:
        raise ValueError("document_path must stay inside the session workdir") from exc
    if path.suffix.lower() not in {".md", ".markdown"} or not path.is_file():
        raise ValueError(
            "document_path must be an existing regular .md or .markdown file"
        )
    try:
        content = path.read_bytes()
        text = content.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError("document must be readable UTF-8 Markdown") from exc
    if not text.strip() or any(
        ord(char) < 32 and char not in "\n\r\t" for char in text
    ):
        raise ValueError(
            "document must contain nonempty Markdown text, not binary data"
        )
    return {
        "document_path": relative.as_posix(),
        "document_digest": hashlib.sha256(content).hexdigest(),
        "document_text": text,
    }


def _unchanged(workdir: Path, item: dict) -> bool:
    try:
        current = _document(workdir, item.get("document_path"))
    except (ValueError, OSError, RuntimeError):
        return False
    return current["document_digest"] == item.get("document_digest")


def _invalidate(state: dict, stage: str) -> None:
    stages = ("sources", "plan") if stage == "sources" else (stage,)
    for name in stages:
        if name in state["stages"]:
            state["stages"][name]["status"] = "invalidated"


def _finish(
    workdir: Path,
    state: dict,
    question: dict,
    status: str,
    *,
    resolution: str = "system",
    text: str = "",
) -> dict:
    qid = question["id"]
    result = {
        "qid": qid,
        "stage": question.get("stage"),
        "status": status,
        "resolution": resolution,
        "text": text,
        "document_path": question.get("document_path"),
        "document_digest": question.get("document_digest"),
        "resolved_at": time.time(),
        "iteration_id": question.get("iteration_id"),
        "document_text": question.get("document_text"),
    }
    stage = question.get("stage")
    if stage and question.get("iteration_id") == state.get("iteration_id"):
        if status in _APPROVED:
            state["stages"][stage] = {
                **result,
                "document_text": question["document_text"],
            }
        else:
            _invalidate(state, stage)
            state["stages"][stage] = result.copy()
    state["decisions"][qid] = result
    # Keep enough completed calls for concurrent waiters without unbounded history.
    while len(state["decisions"]) > 128:
        del state["decisions"][next(iter(state["decisions"]))]
    state["last_decision"] = result
    ctl.clear_question(workdir)
    ctl.clear_answer(workdir)
    return result


def _refresh(workdir: Path, state: dict) -> None:
    sources_invalidated = False
    for stage in ("sources", "plan"):
        item = state["stages"].get(stage, {})
        if item.get("status") in _APPROVED and not _unchanged(workdir, item):
            _invalidate(state, stage)
            sources_invalidated |= stage == "sources"
    question = ctl.read_question(workdir)
    if question and question.get("iteration_id") != state.get("iteration_id"):
        _finish(workdir, state, question, "cancelled")
        return
    if (
        question
        and question.get("stage")
        and (
            not _unchanged(workdir, question)
            or (sources_invalidated and question["stage"] == "plan")
        )
    ):
        _finish(
            workdir,
            state,
            question,
            "invalidated",
            text=(
                "The document or its approved sources changed. Update the Markdown "
                "document and call request_approval again."
            ),
        )


def read_pending_question(workdir: Path) -> dict | None:
    with _transaction(workdir) as (root, state):
        _refresh(root, state)
        question = ctl.read_question(root)
        if not question:
            return None
        return {
            **question,
            "qid": question["id"],
            "context": question.get("context", ""),
            "stage": question.get("stage"),
            "document_path": question.get("document_path"),
            "document_digest": question.get("document_digest"),
            "asked_at": question.get("asked_at"),
            "auto_approve_at": question.get("auto_approve_at"),
        }


def read_last_decision(workdir: Path) -> dict | None:
    with _transaction(workdir) as (root, state):
        _refresh(root, state)
        return state.get("last_decision")


def submit_answer(
    workdir: Path, qid: str, action: str = "answer", text: str = ""
) -> dict:
    if not isinstance(qid, str) or not qid.strip():
        raise ValueError("qid is required")
    if not isinstance(action, str) or action not in {"answer", "approve", "revise"}:
        raise ValueError("action must be answer, approve or revise")
    if not isinstance(text, str) or (
        action in {"answer", "revise"} and not text.strip()
    ):
        raise ValueError("answer and revision text must be nonempty")
    with _transaction(workdir) as (root, state):
        _refresh(root, state)
        question = ctl.read_question(root)
        if not question or question["id"] != qid:
            raise ValueError("stale or already resolved question")
        approval = bool(question.get("stage"))
        if (approval and action == "answer") or (not approval and action != "answer"):
            raise ValueError("action does not match the pending question type")
        status = {
            "approve": "approved",
            "revise": "changes_requested",
            "answer": "answered",
        }[action]
        result = _finish(
            root, state, question, status, resolution="manual", text=text.strip()
        )
        if not approval:
            # Compatibility for old consumers using ctl.take_answer directly.
            ctl.write_answer(root, text.strip(), qid=qid)
        return result


def _cancel_question(workdir: Path, qid: str | None = None) -> None:
    with _transaction(workdir) as (root, state):
        question = ctl.read_question(root)
        if question and (qid is None or question["id"] == qid):
            _finish(
                root,
                state,
                question,
                "cancelled",
                text="Operator question cancelled; no approval granted.",
            )
        elif qid is None:
            ctl.clear_answer(root)


def cancel_pending(workdir: Path) -> None:
    _cancel_question(workdir)


def _begin(
    workdir: Path,
    policy: ApprovalPolicy,
    *,
    stage: str | None = None,
    document_path: str | None = None,
    question: str = "",
    context: str = "",
    ordinary_timeout: float | None = None,
    iteration_id: str | None = None,
) -> tuple[dict, bool]:
    if stage is not None and stage not in {"sources", "plan"}:
        raise ValueError("stage must be sources or plan")
    with _transaction(workdir) as (root, state):
        _check_scope(state, iteration_id, policy)
        if stage and not getattr(policy, stage):
            return {"stage": stage, "status": "not_required"}, False
        # Validate before publishing state or starting any clock.
        document = _document(root, document_path) if stage else {}
        if stage == "plan" and state.get("iteration_id"):
            if not Path(document["document_path"]).is_relative_to(
                state["plan_directory"]
            ):
                raise ValueError(
                    f"Use a new plan in the current plan directory: {state['plan_directory']}"
                )
        _refresh(root, state)
        if ctl.read_question(root):
            raise ValueError(
                "another operator question is already pending for this session"
            )
        if stage:
            approved = state["stages"].get(stage, {})
            if (
                _approved(state, stage)
                and approved.get("document_digest") == document["document_digest"]
            ):
                # An identical document may have been moved to another file;
                # subsequent checks must follow the file actually submitted.
                approved.update(document)
                return {**approved, "reused": True}, False
            if stage == "plan" and policy.sources and not _approved(state, "sources"):
                raise ValueError(
                    "sources approval is required before requesting plan approval"
                )
            _invalidate(state, stage)
        qid = ctl.new_session_id()
        now = time.time()
        timer = policy.auto_approve_after_seconds if stage else None
        record = {
            "id": qid,
            "qid": qid,
            "stage": stage,
            "iteration_id": state.get("iteration_id"),
            "question": ("Согласовать источники?" if stage == "sources" else "Согласовать план?") if stage else question,
            "context": document.get("document_text", context),
            **document,
            "asked_at": now,
            "auto_approve_at": now + timer if timer is not None else None,
            "ordinary_timeout_at": now + ordinary_timeout
            if ordinary_timeout is not None and not stage
            else None,
        }
        ctl.clear_answer(root)
        ctl._atomic_write(
            ctl.ctl_dir(root) / ctl.QUESTION_FILENAME,
            json.dumps(record, ensure_ascii=False, indent=2),
        )
        if stage:
            state["stages"][stage] = {
                **document,
                "status": "pending",
                "qid": qid,
                "iteration_id": state.get("iteration_id"),
            }
        return record, True


def _poll_result(workdir: Path, qid: str) -> dict | None:
    with _transaction(workdir) as (root, state):
        _refresh(root, state)
        if qid in state["decisions"]:
            return state["decisions"][qid]
        question = ctl.read_question(root)
        if not question or question["id"] != qid:
            return {
                "qid": qid,
                "status": "cancelled",
                "resolution": "system",
                "text": "Question no longer pending.",
            }
        if not question.get("stage"):
            answer = ctl.take_answer(root, qid)
            if answer is not None:
                return _finish(
                    root, state, question, "answered", resolution="manual", text=answer
                )
            deadline = question.get("ordinary_timeout_at")
            if deadline is not None and time.time() >= deadline:
                return _finish(
                    root,
                    state,
                    question,
                    "timeout",
                    text=(
                        f"{ctl.NO_HUMAN_ANSWER_SENTINEL}: no human answer arrived within the "
                        "question timeout. Proceed using your best judgment and state the assumption you made."
                    ),
                )
        else:
            deadline = question.get("auto_approve_at")
            if deadline is not None and time.time() >= deadline:
                # Exactly the same atomic resolution path as the manual answer.
                return _finish(
                    root, state, question, "auto_approved", resolution="auto"
                )
        return None


async def _wait(workdir: Path, qid: str) -> dict:
    try:
        while True:
            result = _poll_result(workdir, qid)
            if result is not None:
                return result
            await asyncio.sleep(0.1)
    finally:
        # Cancellation / MCP transport timeout never turns into approval and an
        # old waiter can never clear a newer question.
        _cancel_question(workdir, qid)


_READ_TOOLS = {
    "Read",
    "Glob",
    "Grep",
    "ToolSearch",
    "ListMcpResourcesTool",
    "ReadMcpResourceTool",
    "TodoWrite",
    "TaskCreate",
    "TaskGet",
    "TaskList",
    "TaskUpdate",
    "TaskOutput",
    "TaskStop",
    "mcp__mle_kit__use_glob",
    "mcp__mle_kit__use_grep",
    "mcp__chem__read_data_file",  # Inspect uploaded inputs like native Read.
    "mcp__chem__list_datasets",  # Catalog/description never download tables.
    "mcp__chem__describe_dataset",
}
_PREPARATION_TOOLS = {
    "WebSearch",
    "WebFetch",
    # Retrieval of existing data can inform a plan after sources approval.
    # Do not allow arbitrary chemistry calls: calculations remain gated.
    *(
        "mcp__chem__" + name
        for name in (
            "pubchem_search_by_formula",
            "pubchem_search_by_smiles",
            "pubchem_search_by_exact_mass",
            "pubchem_search_by_mass_range",
            "pubchem_get_by_cids",
            "pubchem_convert_identifier",
            "pubchem_search_by_informal_name",
            "pubchem_get_computed_properties",
            "query_dataset_sql",
            # Old conversations may still call these hidden compatibility names.
            "search_chemx_cytotox",
            "search_chemx_seltox",
            "search_chemx_synergy",
            "search_chemx_nanozymes",
            "search_chemx_benzimidazoles",
            "search_chemx_oxazolidinones",
            "search_chemx_complexes",
            "search_chemx_eyedrops",
            "search_chemx_co_crystals",
            "search_chemx_nanomag",
            "search_connected_datasets",
        )
    ),
    *(
        "mcp__alphaxiv__" + name
        for name in (
            "paper_search",
            "get_paper_content",
            "answer_pdf_queries",
            "read_files_from_github_repository",
        )
    ),
    *(
        "mcp__academia__" + name
        for name in (
            "arxiv_search",
            "arxiv_download",
            "anthology_search",
            "s2_search",
            "s2_get_info",
            "s2_get_citations",
            "s2_get_references",
            "hf_datasets_search",
            "document_qa",
            "web_search",
            "visit_webpage",
            "describe_image",
            "yt_transcript",
            "read_pdf",
            "download_pdf_paper",
            "extract_bitflip_info",
            "generate_research_proposals",
            "score_research_proposals",
        )
    ),
}


def _preparation_write(
    workdir: Path, state: dict, args: dict, *, specialist: bool
) -> bool:
    value = args.get("file_path")
    if not isinstance(value, str) or not value:
        return False
    path = Path(value)
    if ".." in path.parts:
        return False
    if path.is_absolute():
        try:
            path = path.relative_to(workdir)
        except ValueError:
            return False
    current = workdir
    for part in path.parts:
        current /= part
        if current.is_symlink():
            return False
    for key in ("plan_directory", "sources_directory"):
        if state.get(key) and path.is_relative_to(state[key]):
            return path.suffix.lower() in {".md", ".markdown"}
    # Preparatory agents retain their normal literature/proposal artifact paths.
    # They cannot write the result, inputs, runtime metadata or an older plan.
    return (
        specialist
        and bool(path.parts)
        and not path.parts[0].startswith(".")
        and (
            path.parts[0] not in {"plans", "sources", "results", "inputs"}
            and path.name != "result.md"
            and path.suffix.lower() in {".md", ".markdown", ".json", ".bib", ".txt"}
        )
    )


class ApprovalGate:
    def __init__(self, workdir: Path, policy, *, iteration_id: str | None = None, activity=None):
        self.workdir = Path(workdir).resolve()
        self.policy = normalize_policy(policy)
        self.iteration_id = iteration_id
        self.activity = activity

    def _deny(self, stage: str, state: dict) -> dict:
        directory = state.get(
            "plan_directory" if stage == "plan" else "sources_directory",
            str(self.workdir),
        )
        return self._error(
            f"Missing {stage} approval for this iteration. Write a nonempty Markdown document "
            f"in {directory}, then call request_approval(stage='{stage}', document_path='<relative Markdown path>') "
            "and wait for approval before this operation. The directory already exists: use Write/Edit to prepare "
            "the document, Read for known files, or mcp__mle_kit__use_glob/use_grep for search (load with ToolSearch only if deferred and ToolSearch is available)."
        )

    @staticmethod
    def _error(message: str) -> dict:
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": message,
            }
        }

    async def __call__(self, input_data, tool_use_id=None, context=None) -> dict:
        if not self.policy.enabled:
            return {}
        name = input_data.get("tool_name", "")
        args = input_data.get("tool_input") or {}
        with _transaction(self.workdir) as (root, state):
            try:
                _check_scope(state, self.iteration_id, self.policy)
            except ValueError as error:
                return self._error(str(error))
            _refresh(root, state)
            missing = [
                stage
                for stage in ("sources", "plan")
                if getattr(self.policy, stage) and not _approved(state, stage)
            ]
            if not missing:
                return {}
            if name in {"Agent", "Task", "SendMessage"}:
                target_role = (
                    self.activity.target_role(args.get("to") or args.get("recipient"))
                    if name == "SendMessage" and self.activity is not None else None
                )
                role = re.sub(
                    r"[_\s]+", "-", str(
                        target_role if name == "SendMessage" else args.get("subagent_type", "")
                    ).strip().lower()
                )
                required = (
                    ("sources",)
                    if role in {"librarian", "proposer"}
                    else ("sources", "plan")
                )
                stage = next((stage for stage in required if stage in missing), None)
                return self._deny(stage, state) if stage else {}
            if name in _READ_TOOLS:
                return {}
            if name == "mcp__mle_kit__text_editor" and args.get("command") == "view":
                return {}
            if name in {
                "mcp__hitl__request_approval",
                "mcp__hitl__ask_human",
            } and not input_data.get("agent_id"):
                return {}
            specialist = bool(input_data.get("agent_id")) and input_data.get(
                "agent_type"
            ) in {"librarian", "proposer"}
            if name in {"Write", "Edit"} and _preparation_write(
                root, state, args, specialist=specialist and "sources" not in missing
            ):
                return {}
            if "sources" not in missing and name in _PREPARATION_TOOLS:
                return {}
            return self._deny(missing[0], state)


APPROVAL_RULES = _APPROVAL_TEXT["rules"]


def approval_prompt(workdir: Path, policy) -> str:
    policy = normalize_policy(policy)
    if not policy.enabled:
        return ""
    with _transaction(workdir) as (root, state):
        _refresh(root, state)
        stages = ", ".join(
            f"{name}: {state['stages'].get(name, {}).get('status', 'required')}"
            for name in ("sources", "plan")
            if getattr(policy, name)
        )
    lines = [_APPROVAL_TEXT["header"].format(stages=stages)]
    lines.append(_APPROVAL_TEXT["preparation"].format(session_name=workdir.name))
    if policy.sources:
        from holosophos import research_iterations as history

        current_id = state.get("iteration_id")
        current = history.get_iteration(workdir, current_id) if current_id else {}
        if current.get("kind") == "feedback" and current.get("settings", {}).get("input_files"):
            lines.append(_APPROVAL_TEXT["new_inputs"])
        lines.append(_APPROVAL_TEXT["sources_enabled"])
    else:
        lines.append(_APPROVAL_TEXT["sources_disabled"])
    if policy.plan:
        lines.append(_APPROVAL_TEXT["plan_enabled"])
    lines.append(_APPROVAL_TEXT["all_approved"])
    for stage in ("sources", "plan"):
        if not getattr(policy, stage):
            continue
        record = state["stages"].get(stage, {})
        status = record.get("status", "required")
        document_path = record.get("document_path")
        if status in _APPROVED:
            lines.append(
                _APPROVAL_TEXT["reuse_document"].format(document_path=document_path, stage=stage)
            )
        elif status == "pending":
            lines.append(
                _APPROVAL_TEXT["pending_document"].format(document_path=document_path, stage=stage)
            )
        else:
            directory = state.get(f"{stage}_directory", str(workdir))
            lines.append(
                _APPROVAL_TEXT["required_document"].format(directory=directory, stage=stage)
            )
    lines.append(_APPROVAL_TEXT["decision"])
    if policy.sources and policy.plan:
        lines.append(_APPROVAL_TEXT["sources_before_plan"])
    return "\n".join(lines)


def _tool_result(result, *, error: bool = False) -> dict:
    response = {
        "content": [
            {
                "type": "text",
                "text": result
                if isinstance(result, str)
                else json.dumps(result, ensure_ascii=False),
            }
        ]
    }
    if error:
        response["isError"] = True
    return response


def _approval_reply(record: dict) -> str:
    """Model-facing decision; the durable record keeps the full snapshot for the UI."""
    status = record.get("status")
    message = {
        "approved": "Согласовано.",
        "auto_approved": "Согласовано автоматически.",
        "changes_requested": "Нужно доработать.",
        "invalidated": "Согласование утратило силу.",
        "cancelled": "Согласование отменено.",
        "not_required": "Согласование этого этапа не требуется.",
    }.get(status, "Согласование не получено.")
    comment = str(record.get("text") or "").strip()
    return f"{message}\n{comment}" if comment else message


def build_hitl_server(
    workdir: Path, approvals=None, *, iteration_id: str | None = None
):
    """Create the shared manager-only SDK MCP server; no LLM or network calls here."""
    from claude_agent_sdk import create_sdk_mcp_server, tool

    policy = normalize_policy(approvals)
    workdir = Path(workdir)
    ask_timeout = float(os.environ.get("ASK_HUMAN_TIMEOUT", "1800"))

    @tool(
        "ask_human",
        "Ask the operator an ordinary clarification question and wait for an answer. "
        "Provide question and brief context. Returns answer text, or a proceed-anyway sentinel "
        "on the ordinary question timeout. Does not approve a research stage.",
        {"question": str, "context": str},
    )
    async def ask_human(args_in):
        try:
            record, _ = _begin(
                workdir,
                policy,
                question=str(args_in.get("question") or "").strip(),
                context=str(args_in.get("context") or ""),
                ordinary_timeout=ask_timeout,
                iteration_id=iteration_id,
            )
            result = await _wait(workdir, record["id"])
            return _tool_result(
                result.get("text", "")
                if result["status"] in {"answered", "timeout"}
                else result
            )
        except ValueError as exc:
            return _tool_result({"status": "error", "message": str(exc)}, error=True)

    registered = [ask_human]
    if policy.enabled:

        @tool(
            "request_approval",
            "Submit a saved Markdown document for operator approval of a stage enabled for the current iteration. "
            "Provide stage and document_path; a plan must be inside this iteration's PLAN_DIRECTORY. "
            "Sources may reuse an unchanged approved document. The tool snapshots the file and waits for a decision. "
            "Returns a short decision and any operator correction; the document is not repeated in the response.",
            {
                "type": "object",
                "properties": {
                    "stage": {"type": "string", "enum": ["sources", "plan"]},
                    "document_path": {"type": "string"},
                },
                "required": ["stage", "document_path"],
                "additionalProperties": False,
            },
        )
        async def request_approval(args_in):
            try:
                stage = args_in.get("stage")
                if stage not in {"sources", "plan"}:
                    raise ValueError("stage must be sources or plan")
                record, waiting = _begin(
                    workdir,
                    policy,
                    stage=stage,
                    document_path=args_in.get("document_path"),
                    iteration_id=iteration_id,
                )
                decision = await _wait(workdir, record["id"]) if waiting else record
                return _tool_result(_approval_reply(decision))
            except (ValueError, OSError, RuntimeError) as exc:
                return _tool_result(
                    f"Ошибка согласования: {exc}", error=True
                )

        registered.append(request_approval)
    return create_sdk_mcp_server(name="hitl", tools=registered), ask_timeout
