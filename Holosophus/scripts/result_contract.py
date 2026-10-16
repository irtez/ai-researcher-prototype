"""Shared CLI/UI document contract; no model calls or guessed output filenames."""

from pathlib import Path

import yaml
from cc_activity import ResearchActivity

from holosophos import research_iterations as history

RESULT_PROMPT = yaml.safe_load(
    (
        Path(__file__).resolve().parents[1]
        / "holosophos/holosophos/prompts/system.yaml"
    ).read_text(encoding="utf-8")
)["claude_code"]["result_prompt"]


class ResultContract:
    def __init__(self, workdir: Path, iteration_id: str, *, interrupted=lambda: False):
        import approval_control

        self.workdir = Path(workdir)
        self.iteration_id = iteration_id
        self.item = history.get_iteration(workdir, iteration_id)
        self.approvals = approval_control.normalize_policy(
            self.item["settings"].get("approvals")
        )
        approval_control.begin_iteration(workdir, iteration_id, self.approvals)
        self.item = history.get_iteration(workdir, iteration_id)
        self.output_path = self.item["output_path"]
        self.interrupted = interrupted
        self.failure = None
        self.plans = []
        self.activity = ResearchActivity()
        self.activity_sink = None
        self.begin_turn()

    def begin_turn(self):
        self.sdk_result = None
        self.completed = False
        self.defer_completion = False
        self.assistant_text = None
        self.waiting_at_stop = False

    @property
    def answer(self):
        return getattr(self.sdk_result, "result", None)

    def observe(self, msg) -> bool:
        """Track the current turn; return whether its result text needs rendering."""
        self.activity.observe(msg)
        name = type(msg).__name__
        if name == "AssistantMessage" and not getattr(msg, "parent_tool_use_id", None):
            blocks = getattr(msg, "content", None) or []
            self.assistant_text = (
                [b.text for b in blocks if type(b).__name__ == "TextBlock"]
                if not any(type(b).__name__ == "ToolUseBlock" for b in blocks)
                else None
            )
            if getattr(msg, "error", None):
                self.defer_completion = True
        elif name == "UserMessage" and not getattr(msg, "parent_tool_use_id", None):
            self.assistant_text = None
        elif name == "ResultMessage":
            self.sdk_result = msg
            text = (self.answer or "").strip()
            duplicate = bool(
                text
                and self.assistant_text is not None
                and any(
                    text == separator.join(self.assistant_text).strip()
                    for separator in ("", "\n", "\n\n")
                )
            )
            self.assistant_text = None
            return not duplicate
        return True

    def end_turn(self):
        self.completed = self.sdk_result is not None and not self.sdk_result.is_error

    def close_background_connection(self):
        records = self.activity.close_connection()
        if records and self.activity_sink:
            self.activity_sink(records)

    def query(self, text: str, *, mode: str, approvals=None) -> str:
        import approval_control

        if (
            approvals is not None
            and approval_control.normalize_policy(approvals) != self.approvals
        ):
            raise ValueError("Approval policy is fixed for this iteration")

        previous_id = self.item["previous_result_iteration_id"]
        previous = (
            history.get_iteration(self.workdir, previous_id) if previous_id else None
        )
        document = previous and previous["result"]
        paths = [
            f"OUTPUT_PATH: {self.output_path}",
            f"EXPERIMENT_SUMMARY_DIRECTORY: {self.item['experiment_summary_directory']}",
        ]
        if self.item["kind"] == "feedback" or self.approvals.plan:
            paths.append(f"PLAN_DIRECTORY: {self.item['plan_directory']}")
        if self.approvals.sources:
            paths.append(f"SOURCES_DIRECTORY: {self.item['sources_directory']}")
        if document:
            paths += [
                f"PREVIOUS_RESULT_PATH: {document['snapshot_path']}",
                f"PREVIOUS_RESULT_SOURCE_PATH: {document['source_path']}",
            ]
        if mode == "initial":
            instructions = (
                "This is the initial task. There is no previous result to revise."
            )
        elif mode == "feedback":
            instructions = (
                "Continue this same session. Read PREVIOUS_RESULT_PATH and use it as the baseline. "
                "Save a complete revised document at OUTPUT_PATH, preserving unaffected content "
                "unless requested otherwise. You may copy the previous text and edit that copy. "
                "Do not modify the previous result or replace the document with only a change log. "
                "Resolve old links from PREVIOUS_RESULT_SOURCE_PATH; make new links valid from OUTPUT_PATH."
                if document
                else "Continue this same session. No successful previous result is available. "
                "Use the existing task and working materials to prepare the complete result at OUTPUT_PATH."
            )
        elif mode == "resume":
            instructions = "Continue the current iteration with the same output path. This is a technical continuation, not a new task or feedback iteration."
        else:
            raise ValueError("Invalid iteration mode")
        if self.item["kind"] == "feedback" and mode != "resume":
            instructions += (
                " Before making changes, write a new Markdown action plan in PLAN_DIRECTORY; "
                "a few lines are enough for a small edit. Choose a descriptive filename. "
                "Keep earlier iterations' plans intact."
            )
        elif mode == "resume" and (
            self.item["kind"] == "feedback" or self.approvals.plan
        ):
            instructions += (
                " Reuse this iteration's existing action plan in PLAN_DIRECTORY. "
                "If no plan was saved yet, create it there before making changes. "
                "Do not create a new plan just because execution resumed."
            )
        approval_context = approval_control.approval_prompt(
            self.workdir,
            approvals
            if approvals is not None
            else self.item["settings"].get("approvals"),
        )
        return "\n\n".join(
            part
            for part in [instructions, "\n".join(paths), approval_context, text]
            if part
        )

    def control_pending(self) -> bool:
        import cc_run_control as ctl

        try:
            command = (
                (self.workdir / ctl.CTL_DIRNAME / ctl.COMMAND_FILENAME)
                .read_text()
                .strip()
            )
        except FileNotFoundError:
            command = None
        return (
            self.interrupted()
            or self.defer_completion
            or ctl.is_checkpoint_stop(self.workdir)
            or command in {"pause", "stop", "steer", "inject"}
            or ctl.read_rate_limit(self.workdir) is not None
        )

    async def stop_hook(self, input_data, tool_use_id=None, context=None) -> dict:
        if input_data.get("hook_event_name") != "Stop" or input_data.get(
            "stop_hook_active"
        ):
            return {}
        try:
            if self.control_pending():
                return {}
            if self.activity.active:
                self.waiting_at_stop = True
                return {}
            try:
                self._validate_documents()
            except ValueError as error:
                if history.claim_result_reminder(self.workdir, self.iteration_id):
                    return {
                        "decision": "block",
                        "reason": f"{error}. Complete this requirement before finishing. Use the assigned document paths and preserve earlier plans/results. This is the only automatic completion reminder for this iteration.",
                    }
        except (OSError, ValueError) as error:
            self.failure = str(error)
        return {}

    def validated_result(self) -> bytes:
        if self.failure:
            raise ValueError(self.failure)
        if self.activity.active or self.waiting_at_stop:
            raise ValueError("Background agents are still running")
        if not self.completed:
            detail = "; ".join(getattr(self.sdk_result, "errors", None) or [])
            raise ValueError(
                detail
                or "Research ended without a successful final SDK response in the last turn"
            )
        if self.control_pending():
            raise ValueError(
                "Research is interrupted or awaiting operator control / usage limit"
            )
        return self._validate_documents()

    def _validate_documents(self) -> bytes:
        import approval_control

        approval_control.validate_completion(
            self.workdir, self.iteration_id, self.approvals
        )
        self.plans = history.read_plan_documents(
            self.workdir,
            self.iteration_id,
            required=self.item["kind"] == "feedback" or self.approvals.plan,
        )
        return history.result_document(self.workdir, self.iteration_id)


def prepare_cli_iteration(
    workdir: Path, *, resume: bool, sdk_session_id: str, question: str, settings: dict
) -> ResultContract:
    items = history.list_iterations(workdir)["iterations"]
    if resume and items and items[-1]["kind"] != "legacy":
        item = history.resume_iteration(
            workdir, items[-1]["id"], sdk_session_id, settings=settings
        )
    else:
        item = history.start_iteration(
            workdir,
            kind="feedback" if resume else "initial",
            user_message=question,
            settings=settings,
        )
        history.update_iteration(workdir, item["id"], sdk_session_id=sdk_session_id)
    return ResultContract(workdir, item["id"])
