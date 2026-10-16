import ast
import contextvars
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator, Iterable, cast

from holosophos.observability_context import (
    get_langfuse_session_id_override,
    get_langfuse_trace_metadata,
    get_langfuse_trace_tags,
)

_PATCHED = False
_CTX_SESSION_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "langfuse_session_id", default=""
)
_CTX_WORKSPACE_SESSION_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "workspace_session_id", default=""
)
_CTX_AGENT_NAME: contextvars.ContextVar[str] = contextvars.ContextVar(
    "langfuse_agent_name", default=""
)
_CTX_EVENT_TYPE: contextvars.ContextVar[str] = contextvars.ContextVar(
    "langfuse_event_type", default="output"
)
_CTX_STEP_NUMBER: contextvars.ContextVar[str] = contextvars.ContextVar(
    "langfuse_step_number", default=""
)
_CTX_TRACE_ID: contextvars.ContextVar[str] = contextvars.ContextVar("langfuse_trace_id", default="")
_CTX_STEP_SPAN_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "langfuse_step_span_id", default=""
)
_CTX_TRACE_INPUT: contextvars.ContextVar[list[dict[str, Any]]] = contextvars.ContextVar(
    "langfuse_trace_input", default=[]
)
_CTX_TRACE_HISTORY: contextvars.ContextVar[list[dict[str, Any]]] = contextvars.ContextVar(
    "langfuse_trace_history", default=[]
)
_CTX_TRACE_TOOL_ERROR_COUNT: contextvars.ContextVar[int] = contextvars.ContextVar(
    "langfuse_trace_tool_error_count", default=0
)
_CTX_TRACE_LAST_TOOL_ERROR: contextvars.ContextVar[str] = contextvars.ContextVar(
    "langfuse_trace_last_tool_error", default=""
)
_CTX_STEP_TOOL_ERROR_COUNT: contextvars.ContextVar[int] = contextvars.ContextVar(
    "langfuse_step_tool_error_count", default=0
)
_CTX_STEP_ERROR_MESSAGE: contextvars.ContextVar[str] = contextvars.ContextVar(
    "langfuse_step_error_message", default=""
)
_BENCHMARK_SESSION_SUFFIX_RE = re.compile(r"^(benchmark__.+)__q\d+$")

_PATH_REWRITE_TOOLS: dict[str, dict[str, list[int] | list[str]]] = {
    "compile_latex": {
        "positionals": [0, 1],
        "keywords": ["input_filename", "output_filename"],
    },
    "read_pdf": {"positionals": [0], "keywords": ["pdf_path"]},
    "show_image": {"positionals": [0], "keywords": ["path"]},
    "describe_image": {"positionals": [0], "keywords": ["path"]},
    "speech_to_text": {"positionals": [0], "keywords": ["audio_path"]},
    "review_pdf_paper": {"positionals": [0], "keywords": ["pdf_filename"]},
    "remote_download": {"positionals": [0], "keywords": ["file_path"]},
    "evaluate_diagram": {
        "positionals": [0, 1],
        "keywords": ["generated_path", "reference_path"],
    },
}


def _detect_called_tools(code: str, available_tools: Iterable[str]) -> list[str]:
    tool_names = set(available_tools)
    if not code or not tool_names:
        return []
    found: set[str] = set()
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name: str | None = None
        if isinstance(fn, ast.Name):
            name = fn.id
        elif isinstance(fn, ast.Attribute):
            name = fn.attr
        if name and name in tool_names:
            found.add(name)
    return sorted(found)


def _rewrite_code_for_session_workspace(code: str, session_id: str) -> tuple[str, bool]:
    if not code or not session_id:
        return code, False

    helper_name = "__holosophos_session_path"
    if helper_name in code:
        return code, False

    class _SessionPathTransformer(ast.NodeTransformer):
        def __init__(self) -> None:
            self.changed = False

        def _wrap_path(self, node: ast.expr) -> ast.expr:
            self.changed = True
            return ast.copy_location(
                ast.Call(
                    func=ast.Name(id=helper_name, ctx=ast.Load()),
                    args=[node],
                    keywords=[],
                ),
                node,
            )

        def visit_Call(self, node: ast.Call) -> ast.AST:
            node = cast(ast.Call, self.generic_visit(node))
            if not isinstance(node.func, ast.Name):
                return node
            func_name = node.func.id

            rewrite_config = _PATH_REWRITE_TOOLS.get(func_name)
            if not rewrite_config:
                return node

            positionals = cast(list[int], rewrite_config["positionals"])
            keywords = cast(list[str], rewrite_config["keywords"])
            for index in positionals:
                if len(node.args) > index:
                    node.args[index] = self._wrap_path(node.args[index])
            for kw in node.keywords:
                if kw.arg in keywords:
                    kw.value = self._wrap_path(kw.value)

            if func_name == "compile_latex":
                has_output_filename = (
                    any(kw.arg == "output_filename" for kw in node.keywords) or len(node.args) > 1
                )
                if not has_output_filename:
                    self.changed = True
                    node.keywords.append(
                        ast.keyword(
                            arg="output_filename",
                            value=self._wrap_path(ast.Constant(value="paper.pdf")),
                        )
                    )
            return node

    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code, False

    transformer = _SessionPathTransformer()
    tree = cast(ast.Module, transformer.visit(tree))
    if not transformer.changed:
        return code, False

    ast.fix_missing_locations(tree)
    rewritten_code = ast.unparse(tree)
    helpers = (
        f"def {helper_name}(_path):\n"
        "    if not isinstance(_path, str):\n"
        "        return _path\n"
        "    _value = _path.strip()\n"
        "    if not _value:\n"
        "        return _path\n"
        "    if _value.startswith('/'):\n"
        "        return _path\n"
        f"    _session_id = {session_id!r}\n"
        "    if _value == '.':\n"
        "        return _session_id\n"
        "    _normalized = _value[2:] if _value.startswith('./') else _value\n"
        "    if _normalized == _session_id or _normalized.startswith(_session_id + '/'):\n"
        "        return _normalized\n"
        "    return _session_id + '/' + _normalized\n"
    )
    return helpers + "\n\n" + rewritten_code, True


def _ensure_session_workspace(session_id: str) -> None:
    if not session_id:
        return
    from holosophos.settings import settings as holosophos_settings

    workspace_path = Path(holosophos_settings.WORKSPACE_DIR) / session_id
    workspace_path.mkdir(parents=True, exist_ok=True)


def _extract_started_prompt(messages: Any) -> str:
    if not isinstance(messages, list):
        return ""
    for message in messages:
        role = getattr(message, "role", None)
        if role is None and isinstance(message, dict):
            role = message.get("role")
        if role != "user":
            continue

        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content")

        if isinstance(content, str):
            stripped = content.strip()
            if stripped:
                return stripped
            continue

        if isinstance(content, list):
            text_chunks: list[str] = []
            for part in content:
                if isinstance(part, dict):
                    if isinstance(part.get("text"), str):
                        text_chunks.append(part["text"])
                    elif isinstance(part.get("content"), str):
                        text_chunks.append(part["content"])
            combined = "\n".join(chunk.strip() for chunk in text_chunks if chunk.strip())
            if combined:
                return combined
    return ""


def _truncate_text(value: str, max_chars: int = 12000) -> str:
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 16] + "\n...[truncated]"


def _normalize_message_content(content: Any) -> Any:
    if isinstance(content, str):
        return _truncate_text(content)
    if isinstance(content, list):
        normalized_items: list[dict[str, Any]] = []
        for item in content:
            if isinstance(item, dict):
                normalized_item = dict(item)
                if normalized_item.get("type") == "text" and isinstance(
                    normalized_item.get("text"), str
                ):
                    normalized_item["text"] = _truncate_text(normalized_item["text"])
                image_url = normalized_item.get("image_url")
                if isinstance(image_url, dict) and isinstance(image_url.get("url"), str):
                    url = image_url["url"]
                    if url.startswith("data:"):
                        normalized_item["image_url"] = {"url": "[inline image omitted]"}
                normalized_items.append(normalized_item)
            else:
                normalized_items.append({"type": "text", "text": _truncate_text(str(item))})
        return normalized_items
    return _truncate_text(str(content))


def _serialize_messages(messages: Any) -> list[dict[str, Any]]:
    if not isinstance(messages, list):
        return []

    serialized_messages: list[dict[str, Any]] = []
    for message in messages:
        role = getattr(message, "role", None)
        if role is None and isinstance(message, dict):
            role = message.get("role")

        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content")

        serialized: dict[str, Any] = {
            "role": str(role or "unknown"),
            "content": _normalize_message_content(content),
        }

        name = getattr(message, "name", None)
        if name is None and isinstance(message, dict):
            name = message.get("name")
        if name:
            serialized["name"] = str(name)

        tool_call_id = getattr(message, "tool_call_id", None)
        if tool_call_id is None and isinstance(message, dict):
            tool_call_id = message.get("tool_call_id")
        if tool_call_id:
            serialized["tool_call_id"] = str(tool_call_id)

        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls is None and isinstance(message, dict):
            tool_calls = message.get("tool_calls")
        if tool_calls:
            normalized_tool_calls = []
            for tool_call in tool_calls:
                if hasattr(tool_call, "model_dump"):
                    normalized_tool_calls.append(tool_call.model_dump(exclude_none=True))
                elif isinstance(tool_call, dict):
                    normalized_tool_calls.append(tool_call)
                else:
                    normalized_tool_calls.append(str(tool_call))
            serialized["tool_calls"] = normalized_tool_calls

        serialized_messages.append(serialized)
    return serialized_messages


def _extract_tool_failure_message(output: dict[str, Any]) -> str:
    explicit_error = str(output.get("error") or "").strip()
    if explicit_error:
        return explicit_error

    stdout = str(output.get("stdout") or "")
    match = re.search(r"Error executing tool [^:]+: (?P<message>.+)", stdout)
    if match:
        return match.group("message").strip()

    if "Traceback (most recent call last):" in stdout:
        return "Python executor traceback"

    return ""


def _extract_messages_from_ainvoke(
    args: tuple[Any, ...], kwargs: dict[str, Any]
) -> list[dict[str, Any]]:
    messages = kwargs.get("messages")
    if messages is None and args:
        messages = args[0]
    return _serialize_messages(messages)


def _persist_started_prompt(session_id: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
    if not session_id:
        return
    from holosophos.settings import settings as holosophos_settings

    messages = kwargs.get("messages")
    if messages is None and args:
        messages = args[0]

    prompt_text = _extract_started_prompt(messages)
    if not prompt_text:
        return

    prompt_path = Path(holosophos_settings.WORKSPACE_DIR) / session_id / "started_prompt.md"
    if prompt_path.exists():
        return

    payload = f"{prompt_text}\n"
    prompt_path.write_text(payload, encoding="utf-8")


def _move_path_to_session(src: Path, dst_dir: Path) -> None:
    if not src.exists():
        return
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / src.name

    if src.is_file():
        if dst.exists():
            dst.unlink()
        shutil.move(str(src), str(dst))
        return

    if src.is_dir():
        if not dst.exists():
            shutil.move(str(src), str(dst))
            return
        for child in src.iterdir():
            _move_path_to_session(child, dst)
        src.rmdir()


def _relocate_llm_proxy_artifacts(session_id: str, called_tools: list[str]) -> None:
    if not session_id or "llm_proxy_local" not in called_tools:
        return
    from holosophos.settings import settings as holosophos_settings

    workspace_root = Path(holosophos_settings.WORKSPACE_DIR)
    session_dir = workspace_root / session_id
    artifact_names = ("llm_proxy.py", "llm_proxy.log", "llm_proxy.pid", ".cache")
    for name in artifact_names:
        try:
            _move_path_to_session(workspace_root / name, session_dir)
        except Exception:
            continue


def _extract_session_id_from_ainvoke(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    session_id = kwargs.get("session_id")
    if isinstance(session_id, str):
        return session_id
    if len(args) >= 2 and isinstance(args[1], str):
        return args[1]
    return ""


def _resolve_langfuse_session_id(workspace_session_id: str) -> str:
    override = get_langfuse_session_id_override().strip()
    if override:
        return override
    match = _BENCHMARK_SESSION_SUFFIX_RE.match(workspace_session_id)
    if match:
        return match.group(1)
    return workspace_session_id


def _extract_step_context(args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[str, str]:
    session_id = ""
    step_number = ""
    if "session_id" in kwargs and isinstance(kwargs["session_id"], str):
        session_id = kwargs["session_id"]
    if "step_number" in kwargs and kwargs["step_number"] is not None:
        step_number = str(kwargs["step_number"])

    # codearkt<=1.8.x signature:
    # _step(self, messages, python_executor, session_id, run_id, ..., step_number=None)
    if not session_id and len(args) >= 3 and isinstance(args[2], str):
        session_id = args[2]
    if not step_number and len(args) >= 7 and args[6] is not None:
        step_number = str(args[6])

    # newer signature:
    # _step(self, messages, python_executor, run_context, step_number)
    if len(args) >= 3:
        run_context = args[2]
        run_context_session_id = getattr(run_context, "session_id", None)
        if not session_id and isinstance(run_context_session_id, str):
            session_id = run_context_session_id
    if not step_number and len(args) >= 4 and args[3] is not None:
        step_number = str(args[3])

    return session_id, step_number


def _extract_event_type(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    event_type = kwargs.get("event_type")
    if event_type is None:
        if len(args) >= 7:
            # codearkt<=1.8.20 positional event_type
            event_type = args[6]
        elif len(args) >= 5:
            # newer codearkt positional event_type
            event_type = args[4]
    return str(getattr(event_type, "value", event_type or "output"))


def _trace_user_id(workspace_session_id: str) -> str | None:
    """The authenticated login for this run, to stamp as the Langfuse trace user.

    Read lazily from codearkt's per-session owner registry so this module keeps
    its light top-level import (codearkt is imported lazily elsewhere here too,
    and the pinned wheel may lag the vendored source that adds the registry).
    Returns None when unauthenticated or when the registry is unavailable, which
    is exactly the pre-auth behaviour.
    """
    try:
        from codearkt.run_config import get_owner
    except Exception:
        return None
    return get_owner(workspace_session_id) or None


def _update_trace_snapshot(
    langfuse_client: Any,
    trace_id: str,
    *,
    agent_name: str,
    langfuse_session_id: str,
    workspace_session_id: str,
    input_messages: list[dict[str, Any]] | None = None,
    output_messages: list[dict[str, Any]] | None = None,
    error_message: str = "",
) -> None:
    if not trace_id:
        return

    tool_error_count = _CTX_TRACE_TOOL_ERROR_COUNT.get()
    last_tool_error = _CTX_TRACE_LAST_TOOL_ERROR.get()
    trace_metadata = {
        "agent_name": agent_name,
        "event_type": "agent_run",
        "workspace_session_id": workspace_session_id,
        "langfuse_session_id": langfuse_session_id,
        "tool_error_count": tool_error_count,
        **get_langfuse_trace_metadata(),
    }
    if last_tool_error:
        trace_metadata["last_tool_error"] = last_tool_error
    if error_message:
        trace_metadata["error"] = error_message

    trace_tags = [agent_name, "agent_run", *get_langfuse_trace_tags()]
    if tool_error_count > 0 and "tool_error" not in trace_tags:
        trace_tags.append("tool_error")
    if error_message and "error" not in trace_tags:
        trace_tags.append("error")

    update_kwargs: dict[str, Any] = {
        "session_id": langfuse_session_id or None,
        # Attribute the trace to the authenticated login (auth'd deploy) so runs
        # are filterable per team member in Langfuse. None when unauthenticated.
        "user_id": _trace_user_id(workspace_session_id),
        "metadata": trace_metadata,
        "tags": trace_tags,
    }
    if input_messages:
        update_kwargs["input"] = input_messages
    if output_messages:
        update_kwargs["output"] = (
            {"messages": output_messages, "error": error_message}
            if error_message
            else output_messages
        )

    langfuse_client.trace(id=trace_id, name=f"{agent_name}:run").update(**update_kwargs)


def patch_codearkt_openai_for_langfuse() -> None:
    global _PATCHED
    if _PATCHED:
        return

    host = os.getenv("LANGFUSE_HOST", "").strip()
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
    secret_key = os.getenv("LANGFUSE_SECRET_KEY", "").strip()
    if not host or not public_key or not secret_key:
        raise RuntimeError(
            "Missing Langfuse credentials: LANGFUSE_HOST, "
            "LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY."
        )

    from langfuse.openai import AsyncOpenAI as LangfuseAsyncOpenAI
    import codearkt.codeact as codearkt_codeact
    import codearkt.llm as codearkt_llm
    import codearkt.python_executor as codearkt_python_executor

    codearkt_llm.AsyncOpenAI = cast(type, LangfuseAsyncOpenAI)
    try:
        from langfuse import get_client

        langfuse_client = get_client()
    except Exception:
        from langfuse import Langfuse

        langfuse_client = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            host=host,
        )

    span_supported = all(hasattr(langfuse_client, method_name) for method_name in ("trace", "span"))

    original_ainvoke = codearkt_codeact.CodeActAgent.ainvoke
    original_run_llm = codearkt_codeact.CodeActAgent._run_llm
    original_step = codearkt_codeact.CodeActAgent._step
    original_astream = codearkt_llm.LLM.astream
    original_tool_ainvoke = codearkt_python_executor.PythonExecutor.ainvoke

    async def _ainvoke_with_langfuse_trace(  # type: ignore[no-untyped-def]
        self: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        workspace_session_id = _extract_session_id_from_ainvoke(args, kwargs)
        langfuse_session_id = _resolve_langfuse_session_id(workspace_session_id)
        agent_name = getattr(self, "name", "agent")
        input_messages = _extract_messages_from_ainvoke(args, kwargs)
        _ensure_session_workspace(workspace_session_id)
        _persist_started_prompt(workspace_session_id, args, kwargs)

        trace_id = ""
        trace_client: Any = None
        if span_supported:
            trace_id = f"{workspace_session_id or langfuse_session_id or 'session'}-{agent_name}-{uuid.uuid4().hex[:8]}"
            try:
                trace_client = langfuse_client.trace(
                    id=trace_id,
                    name=f"{agent_name}:run",
                    session_id=langfuse_session_id or None,
                    user_id=_trace_user_id(workspace_session_id),
                    input=input_messages or None,
                    metadata={
                        "agent_name": agent_name,
                        "event_type": "agent_run",
                        "workspace_session_id": workspace_session_id,
                        "langfuse_session_id": langfuse_session_id,
                        **get_langfuse_trace_metadata(),
                    },
                    tags=[agent_name, "agent_run", *get_langfuse_trace_tags()],
                )
            except Exception:
                trace_id = ""
                trace_client = None

        session_token = _CTX_SESSION_ID.set(langfuse_session_id or "")
        workspace_token = _CTX_WORKSPACE_SESSION_ID.set(workspace_session_id or "")
        agent_token = _CTX_AGENT_NAME.set(agent_name)
        trace_token = _CTX_TRACE_ID.set(trace_id)
        step_token = _CTX_STEP_NUMBER.set("")
        event_token = _CTX_EVENT_TYPE.set("output")
        step_span_token = _CTX_STEP_SPAN_ID.set("")
        trace_input_token = _CTX_TRACE_INPUT.set(input_messages)
        trace_history_token = _CTX_TRACE_HISTORY.set(list(input_messages))
        trace_tool_error_count_token = _CTX_TRACE_TOOL_ERROR_COUNT.set(0)
        trace_last_tool_error_token = _CTX_TRACE_LAST_TOOL_ERROR.set("")
        try:
            bound_original_ainvoke = original_ainvoke.__get__(self, type(self))
            result = await bound_original_ainvoke(*args, **kwargs)
            if trace_client is not None:
                final_history = _CTX_TRACE_HISTORY.get() or input_messages
                _update_trace_snapshot(
                    langfuse_client,
                    trace_id,
                    agent_name=agent_name,
                    langfuse_session_id=langfuse_session_id,
                    workspace_session_id=workspace_session_id,
                    input_messages=input_messages,
                    output_messages=final_history
                    or [{"role": "assistant", "content": str(result)}],
                )
            return result
        except Exception as exc:
            if trace_client is not None:
                final_history = _CTX_TRACE_HISTORY.get() or input_messages
                _update_trace_snapshot(
                    langfuse_client,
                    trace_id,
                    agent_name=agent_name,
                    langfuse_session_id=langfuse_session_id,
                    workspace_session_id=workspace_session_id,
                    input_messages=input_messages,
                    output_messages=final_history,
                    error_message=repr(exc),
                )
            raise
        finally:
            try:
                langfuse_client.flush()
            except Exception:
                pass
            _CTX_TRACE_LAST_TOOL_ERROR.reset(trace_last_tool_error_token)
            _CTX_TRACE_TOOL_ERROR_COUNT.reset(trace_tool_error_count_token)
            _CTX_TRACE_HISTORY.reset(trace_history_token)
            _CTX_TRACE_INPUT.reset(trace_input_token)
            _CTX_STEP_SPAN_ID.reset(step_span_token)
            _CTX_EVENT_TYPE.reset(event_token)
            _CTX_STEP_NUMBER.reset(step_token)
            _CTX_TRACE_ID.reset(trace_token)
            _CTX_AGENT_NAME.reset(agent_token)
            _CTX_WORKSPACE_SESSION_ID.reset(workspace_token)
            _CTX_SESSION_ID.reset(session_token)

    async def _run_llm_with_langfuse_context(  # type: ignore[no-untyped-def]
        self: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        event_type_value = _extract_event_type(args, kwargs)

        langfuse_session_id = _CTX_SESSION_ID.get()
        if not langfuse_session_id:
            if "session_id" in kwargs and isinstance(kwargs["session_id"], str):
                langfuse_session_id = kwargs["session_id"]
            elif len(args) >= 2 and isinstance(args[1], str):
                langfuse_session_id = args[1]

        session_token = _CTX_SESSION_ID.set(langfuse_session_id or "")
        agent_token = _CTX_AGENT_NAME.set(getattr(self, "name", "agent"))
        event_token = _CTX_EVENT_TYPE.set(event_type_value)
        try:
            bound_original_run_llm = original_run_llm.__get__(self, type(self))
            return await bound_original_run_llm(*args, **kwargs)
        finally:
            _CTX_EVENT_TYPE.reset(event_token)
            _CTX_AGENT_NAME.reset(agent_token)
            _CTX_SESSION_ID.reset(session_token)

    async def _step_with_langfuse_context(  # type: ignore[no-untyped-def]
        self: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        workspace_session_id, step_number = _extract_step_context(args, kwargs)
        workspace_session_id = workspace_session_id or _CTX_WORKSPACE_SESSION_ID.get()
        langfuse_session_id = _CTX_SESSION_ID.get()
        agent_name = _CTX_AGENT_NAME.get() or getattr(self, "name", "agent")
        trace_id = _CTX_TRACE_ID.get()
        step_messages = kwargs.get("messages")
        if step_messages is None and args:
            step_messages = args[0]
        serialized_step_messages = _serialize_messages(step_messages)

        step_span = None
        if span_supported and trace_id:
            try:
                step_span = langfuse_client.span(
                    trace_id=trace_id,
                    name=f"{agent_name}:step:{step_number or 'unknown'}",
                    input=serialized_step_messages or None,
                    metadata={
                        "agent_name": agent_name,
                        "event_type": "step",
                        "session_id": langfuse_session_id,
                        "workspace_session_id": workspace_session_id,
                        "step_number": step_number,
                    },
                )
            except Exception:
                step_span = None

        session_token = _CTX_SESSION_ID.set(langfuse_session_id or "")
        workspace_token = _CTX_WORKSPACE_SESSION_ID.set(workspace_session_id or "")
        agent_token = _CTX_AGENT_NAME.set(agent_name)
        step_token = _CTX_STEP_NUMBER.set(step_number)
        step_span_token = _CTX_STEP_SPAN_ID.set(getattr(step_span, "id", "") if step_span else "")
        step_tool_error_count_token = _CTX_STEP_TOOL_ERROR_COUNT.set(0)
        step_error_message_token = _CTX_STEP_ERROR_MESSAGE.set("")
        try:
            bound_original_step = original_step.__get__(self, type(self))
            result = await bound_original_step(*args, **kwargs)
            serialized_step_output = _serialize_messages(result)
            updated_history = serialized_step_messages + serialized_step_output
            _CTX_TRACE_HISTORY.set(updated_history)
            if trace_id:
                step_error = _CTX_STEP_ERROR_MESSAGE.get()
                _update_trace_snapshot(
                    langfuse_client,
                    trace_id,
                    agent_name=agent_name,
                    langfuse_session_id=langfuse_session_id,
                    workspace_session_id=workspace_session_id,
                    input_messages=_CTX_TRACE_INPUT.get() or serialized_step_messages,
                    output_messages=updated_history,
                    error_message=step_error,
                )
            if step_span is not None:
                step_tool_error_count = _CTX_STEP_TOOL_ERROR_COUNT.get()
                step_error = _CTX_STEP_ERROR_MESSAGE.get()
                output_payload: dict[str, Any] = {
                    "messages": serialized_step_output,
                    "tool_error_count": step_tool_error_count,
                }
                if step_error:
                    output_payload["error"] = step_error
                    step_span.end(
                        output=output_payload,
                        level="ERROR",
                        status_message=step_error,
                    )
                else:
                    step_span.end(output=output_payload)
            return result
        except Exception as exc:
            _update_trace_snapshot(
                langfuse_client,
                trace_id,
                agent_name=agent_name,
                langfuse_session_id=langfuse_session_id,
                workspace_session_id=workspace_session_id,
                input_messages=_CTX_TRACE_INPUT.get() or serialized_step_messages,
                output_messages=serialized_step_messages,
                error_message=repr(exc),
            )
            if step_span is not None:
                step_span.end(
                    output={"messages": serialized_step_messages, "error": repr(exc)},
                    level="ERROR",
                    status_message=str(exc),
                )
            raise
        finally:
            _CTX_STEP_ERROR_MESSAGE.reset(step_error_message_token)
            _CTX_STEP_TOOL_ERROR_COUNT.reset(step_tool_error_count_token)
            _CTX_STEP_SPAN_ID.reset(step_span_token)
            _CTX_STEP_NUMBER.reset(step_token)
            _CTX_AGENT_NAME.reset(agent_token)
            _CTX_WORKSPACE_SESSION_ID.reset(workspace_token)
            _CTX_SESSION_ID.reset(session_token)

    async def _astream_with_langfuse_context(  # type: ignore[no-untyped-def]
        self: Any,
        messages: Any,
        **kwargs: Any,
    ) -> AsyncGenerator[Any, None]:
        langfuse_session_id = _CTX_SESSION_ID.get()
        workspace_session_id = _CTX_WORKSPACE_SESSION_ID.get()
        agent_name = _CTX_AGENT_NAME.get()
        event_type = _CTX_EVENT_TYPE.get()
        trace_id = _CTX_TRACE_ID.get()
        step_span_id = _CTX_STEP_SPAN_ID.get()

        if langfuse_session_id:
            kwargs["session_id"] = langfuse_session_id
        owner = _trace_user_id(workspace_session_id)
        if owner:
            kwargs["user_id"] = owner
        if trace_id and "trace_id" not in kwargs:
            kwargs["trace_id"] = trace_id
        if step_span_id and "parent_observation_id" not in kwargs:
            kwargs["parent_observation_id"] = step_span_id

        if agent_name:
            kwargs.setdefault("name", f"{agent_name}:{event_type}")
            metadata = kwargs.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
            metadata.setdefault("agent_name", agent_name)
            metadata.setdefault("event_type", event_type)
            if workspace_session_id:
                metadata.setdefault("workspace_session_id", workspace_session_id)
            step_number = _CTX_STEP_NUMBER.get()
            if step_number:
                metadata.setdefault("step_number", step_number)
            kwargs["metadata"] = metadata

            tags = kwargs.get("tags")
            if not isinstance(tags, list):
                tags = []
            if agent_name not in tags:
                tags.append(agent_name)
            if event_type and event_type not in tags:
                tags.append(event_type)
            kwargs["tags"] = tags

        async for event in original_astream(self, messages, **kwargs):
            yield event

    async def _tool_ainvoke_with_langfuse_span(  # type: ignore[no-untyped-def]
        self: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        if not span_supported:
            bound_original_tool_ainvoke = original_tool_ainvoke.__get__(self, type(self))
            return await bound_original_tool_ainvoke(*args, **kwargs)

        trace_id = _CTX_TRACE_ID.get()
        if not trace_id:
            bound_original_tool_ainvoke = original_tool_ainvoke.__get__(self, type(self))
            return await bound_original_tool_ainvoke(*args, **kwargs)

        code = kwargs.get("code")
        if code is None and args:
            code = args[0]
        code_text = str(code or "")

        workspace_session_id = str(
            getattr(self, "session_id", "") or _CTX_WORKSPACE_SESSION_ID.get()
        )
        langfuse_session_id = _CTX_SESSION_ID.get()
        agent_name = _CTX_AGENT_NAME.get() or "agent"
        step_number = _CTX_STEP_NUMBER.get()
        available_tools = list(getattr(self, "tool_names", tuple()))
        called_tools = _detect_called_tools(code_text, available_tools)
        tool_label = (
            called_tools[0] if len(called_tools) == 1 else ("multi" if called_tools else "none")
        )
        rewritten_code, workspace_rewrite_applied = _rewrite_code_for_session_workspace(
            code_text, workspace_session_id
        )

        span_kwargs: dict[str, Any] = {
            "trace_id": trace_id,
            "name": f"{agent_name}:tool_exec:{tool_label}",
            "input": code_text,
            "metadata": {
                "agent_name": agent_name,
                "session_id": langfuse_session_id,
                "workspace_session_id": workspace_session_id,
                "step_number": step_number,
                "event_type": "tool_exec",
                "called_tools": called_tools,
                "tool_count": len(called_tools),
                "tool_tags": [f"tool:{tool_name}" for tool_name in called_tools],
                "workspace_subdir": workspace_session_id,
                "workspace_rewrite_applied": workspace_rewrite_applied,
            },
        }
        step_span_id = _CTX_STEP_SPAN_ID.get()
        if step_span_id:
            span_kwargs["parent_observation_id"] = step_span_id

        span = None
        try:
            span = langfuse_client.span(**span_kwargs)
        except Exception:
            span = None

        try:
            bound_original_tool_ainvoke = original_tool_ainvoke.__get__(self, type(self))
            rewritten_args = list(args)
            rewritten_kwargs = dict(kwargs)
            if "code" in rewritten_kwargs:
                rewritten_kwargs["code"] = rewritten_code
            elif rewritten_args:
                rewritten_args[0] = rewritten_code
            result = await bound_original_tool_ainvoke(*tuple(rewritten_args), **rewritten_kwargs)
            _relocate_llm_proxy_artifacts(workspace_session_id, called_tools)
            output = {
                "stdout": getattr(result, "stdout", None),
                "error": getattr(result, "error", None),
                "result": getattr(result, "result", None),
            }
            failure_message = _extract_tool_failure_message(output)
            if failure_message:
                _CTX_TRACE_TOOL_ERROR_COUNT.set(_CTX_TRACE_TOOL_ERROR_COUNT.get() + 1)
                _CTX_TRACE_LAST_TOOL_ERROR.set(failure_message)
                _CTX_STEP_TOOL_ERROR_COUNT.set(_CTX_STEP_TOOL_ERROR_COUNT.get() + 1)
                if not _CTX_STEP_ERROR_MESSAGE.get():
                    _CTX_STEP_ERROR_MESSAGE.set(failure_message)
            if span is not None:
                if failure_message:
                    span.end(
                        output=output,
                        level="ERROR",
                        status_message=failure_message,
                    )
                else:
                    span.end(output=output)
            return result
        except Exception as exc:
            _CTX_TRACE_TOOL_ERROR_COUNT.set(_CTX_TRACE_TOOL_ERROR_COUNT.get() + 1)
            _CTX_TRACE_LAST_TOOL_ERROR.set(str(exc))
            _CTX_STEP_TOOL_ERROR_COUNT.set(_CTX_STEP_TOOL_ERROR_COUNT.get() + 1)
            if not _CTX_STEP_ERROR_MESSAGE.get():
                _CTX_STEP_ERROR_MESSAGE.set(str(exc))
            if span is not None:
                span.end(
                    output={"error": repr(exc)},
                    level="ERROR",
                    status_message=str(exc),
                )
            raise

    codearkt_codeact.CodeActAgent.ainvoke = _ainvoke_with_langfuse_trace
    codearkt_codeact.CodeActAgent._run_llm = _run_llm_with_langfuse_context
    codearkt_codeact.CodeActAgent._step = _step_with_langfuse_context
    codearkt_llm.LLM.astream = _astream_with_langfuse_context
    codearkt_python_executor.PythonExecutor.ainvoke = _tool_ainvoke_with_langfuse_span
    _PATCHED = True
