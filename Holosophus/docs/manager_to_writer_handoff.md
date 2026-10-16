# Manager to Writer Handoff

This note explains where the manager collects context for the `writer` agent, how that context is passed, and what is actually stored on disk.

Related reference:

- [Writer Agent Reference](./writer_agent_input_output_examples.md)

Primary source files:

- [`system.yaml`](../holosophos/holosophos/prompts/system.yaml)
- [`codeact.py`](../codearkt/codearkt/codeact.py)
- [`tools.py`](../codearkt/container/tools.py)
- [`langfuse_openai_patch.py`](../holosophos/holosophos/langfuse_openai_patch.py)
- [`files.py`](../mle_kit_mcp/mle_kit_mcp/files.py)
- [`text_editor.py`](../mle_kit_mcp/mle_kit_mcp/tools/text_editor.py)

## Short answer

There is no dedicated Python data structure like `WriterContext` that the manager fills and stores inside the Writer agent.

Instead, the flow is:

1. The manager accumulates context in its own conversation history.
2. The manager prompt explicitly instructs it to include prior facts and agent outputs when delegating.
3. The manager sends that context to the Writer as one long string via `agent__writer(...)`.
4. Persistent artifacts are stored in the shared session workspace under `workdir/<session_id>/`.

## Where the manager is instructed to collect and forward context

This behavior is defined in the manager prompt in [`system.yaml`](../holosophos/holosophos/prompts/system.yaml), not in a special handoff class.

Source:

- [`holosophos/holosophos/prompts/system.yaml`](../holosophos/holosophos/prompts/system.yaml)

The manager prompt says team members are stateless and must be given full context:

```34:45:Holosophus/holosophos/holosophos/prompts/system.yaml
## Manager task-specific guidelines

Rules:
- You do not have GPUs for heavy computations, and you can not write code properly.
- Rely only on your team members to do any tasks related to search, writing, and coding.
- Do not write files with Python code yourself, delegate it to team members.
- Team members do not have access to your memory or their memory from previous tasks, so provide them with a fully detailed context as an input.
- Copy the original task details and all the relevant responses from other team members to the input.
- Always provide specific paper IDs (e.g. ArXiv ID) and titles in your final response.
- Do everything step by step. Wait for observations from team members before moving to the next step.
- When you have the final answer, write 'Final answer:' and then the answer.
- The final answer should always start with 'Final answer:'.
```

The same prompt also defines what the manager should include in the handoff:

```127:141:Holosophus/holosophos/holosophos/prompts/system.yaml
You can also give tasks to team members the same way you call tools.
The only positional argument you provide is an extremely detailed task description.
Team members do not have access to your memory or their memory from previous tasks.
They are stateless, so they forget everything after completing the task.
Therefore, the task description should contain:
- A detailed description of a task posed by a user.
- All the found facts and constraints.
- A detailed summary of all previous steps.
- Detailed instructions about what the team member should do now.
The whole task description should have more than 20 sentences.
The task description should contain as much information as possible.
Always rely on your team members to do any tasks related to search and coding.
Provide them with a full detailed context of the task.
```

## Where the manager actually keeps the collected information

The manager keeps context in its own `messages` list while it runs. That is the main in-memory place where previous results accumulate inside [`CodeActAgent`](../codearkt/codearkt/codeact.py).

Source:

- [`codearkt/codearkt/codeact.py`](../codearkt/codearkt/codeact.py)

During execution, planning and step outputs are appended to `messages`:

```107:124:Holosophus/codearkt/codearkt/codeact.py
tools = await self._get_tools(server_host=server_host, server_port=server_port)
self._log(run_context, f"Fetched tools: {[tool.name for tool in tools]}")

current_date = datetime.now().strftime("%Y-%m-%d")
system_prompt = self.prompts.system.render(tools=tools, current_date=current_date)

if messages and messages[0].role not in ("system", "developer"):
    messages = [ChatMessage(role="system", content=system_prompt)] + messages

for step_number in range(1, self.max_iterations + 1):
    if self.planning_interval and (step_number - 1) % self.planning_interval == 0:
        self._log(run_context, f"Planning step {step_number} started")
        new_messages = await self._run_planning_step(
            messages=messages,
            tools=tools,
            run_context=run_context,
        )
        messages.extend(new_messages)
```

And after each step:

```127:135:Holosophus/codearkt/codearkt/codeact.py
self._log(run_context, f"Step {step_number} started")
new_messages = await self._step(
    messages=messages,
    python_executor=python_executor,
    run_context=run_context,
    step_number=step_number,
)
messages.extend(new_messages)
self._log(run_context, f"Step {step_number} completed")
```

So the manager "collects" information mostly by retaining the running conversation state.

## Where the handoff to Writer happens

The Writer is exposed as a managed-agent tool named `agent__writer`. The managed-agent call wrapper lives in [`codearkt/container/tools.py`](../codearkt/container/tools.py).

Source:

- [`codearkt/container/tools.py`](../codearkt/container/tools.py)
- [`codearkt/codearkt/server.py`](../codearkt/codearkt/server.py)

The actual tool wrapper in [`create_call_agent()`](../codearkt/container/tools.py) creates an HTTP request where the manager's handoff string becomes a fresh user message for the Writer:

```166:175:Holosophus/codearkt/container/tools.py
def create_call_agent(url: str) -> Callable[..., Any]:
    def _call_agent(query: str, session_id: str) -> Any:
        payload = {
            "messages": [{"role": "user", "content": query}],
            "session_id": session_id,
            "stream": False,
        }
        resp = httpx.post(url, json=payload, timeout=AGENT_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
```

This is the key point:

- the manager does not pass a structured Python object,
- it passes one long `query` string,
- that string is the full Writer handoff.

## What is persisted on disk

If you mean durable storage, the system persists data in the session workdir, not in the Writer object.

Source:

- [`holosophos/holosophos/langfuse_openai_patch.py`](../holosophos/holosophos/langfuse_openai_patch.py)
- [`mle_kit_mcp/mle_kit_mcp/files.py`](../mle_kit_mcp/mle_kit_mcp/files.py)
- [`mle_kit_mcp/mle_kit_mcp/tools/text_editor.py`](../mle_kit_mcp/mle_kit_mcp/tools/text_editor.py)

The initial prompt for a session is saved as `started_prompt.md` by [`_persist_started_prompt()`](../holosophos/holosophos/langfuse_openai_patch.py):

```202:220:Holosophus/holosophos/holosophos/langfuse_openai_patch.py
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
```

The workspace itself is session-scoped through [`get_workspace_dir()`](../mle_kit_mcp/mle_kit_mcp/files.py):

```13:20:Holosophus/mle_kit_mcp/mle_kit_mcp/files.py
def get_workspace_dir(session_id: Optional[str] = None) -> Path:
    assert settings.WORKSPACE_DIR is not None, "Please set the WORKSPACE_DIR environment variable"
    directory = Path(settings.WORKSPACE_DIR)
    if session_id:
        directory = directory / normalize_session_id(session_id)
    if not directory.exists():
        directory.mkdir(parents=True, exist_ok=True)
    return directory
```

And file operations in [`text_editor.py`](../mle_kit_mcp/mle_kit_mcp/tools/text_editor.py) resolve relative paths under that session workspace:

```180:189:Holosophus/mle_kit_mcp/mle_kit_mcp/tools/text_editor.py
assert not path.startswith(
    "/"
), "Absolute path is not supported, only relative to the work directory"
valid_commands = ("view", "write", "str_replace", "insert", "undo_edit", "append")

path_obj = get_workspace_dir(session_id=session_id) / path

if command == "view":
    show_lines = show_lines if show_lines is not None else False
    return _view(path_obj, view_start_line, view_end_line, show_lines)
```

## Practical interpretation

If you are reading the code with the question "where does manager store Writer input?", the honest answer is:

Source:

- [`./writer_agent_input_output_examples.md`](./writer_agent_input_output_examples.md)
- [`holosophos/holosophos/prompts/system.yaml`](../holosophos/holosophos/prompts/system.yaml)
- [`codearkt/codearkt/codeact.py`](../codearkt/codearkt/codeact.py)
- [`codearkt/container/tools.py`](../codearkt/container/tools.py)

- in memory: inside the manager's running `messages`,
- at handoff time: inside the long `query` string sent to `agent__writer(...)`,
- on disk: inside files under `workdir/<session_id>/`, including `started_prompt.md` and any artifacts written by agents.

There is no separate manager-owned Writer handoff registry or Writer-specific storage object in the code shown here.

## What "artifacts" usually means here

In this repository, "artifacts" usually means files written under the session workspace, not Langfuse database records.

Source:

- [`README.md`](../README.md)
- [`holosophos/holosophos/prompts/writer.yaml`](../holosophos/holosophos/prompts/writer.yaml)
- [`./writer_agent_input_output_examples.md`](./writer_agent_input_output_examples.md)
- [`debugging/stalling/README.md`](../debugging/stalling/README.md)

Typical filesystem artifacts for a paper-writing run can include:

- `workdir/<session_id>/started_prompt.md`
- `workdir/<session_id>/paper.tex`
- `workdir/<session_id>/references.bib`
- `workdir/<session_id>/paper.pdf`
- `workdir/<session_id>/figures/...` for generated diagrams and plots
- upstream files left by other agents, such as experiment summaries, metrics JSON files, or citation notes

So if you want to inspect the actual paper-related outputs, the first place to check is the matching `workdir/<session_id>/` directory.

## What Langfuse contains instead

Langfuse stores traces and observations about the run, not the primary paper files themselves.

Source:

- [`README.md`](../README.md)
- [`holosophos/holosophos/langfuse_openai_patch.py`](../holosophos/holosophos/langfuse_openai_patch.py)

Useful things you can inspect in Langfuse for the same `session_id`:

- top-level traces for the run
- observations for manager and specialist agents
- model calls, tokens, and errors
- tool execution spans
- tool metadata such as `agent_name`, `session_id`, `step_number`, `called_tools`, and `workspace_subdir`

That means Langfuse is good for answering questions like:

- which agent was active last,
- which tool the Writer called,
- whether a tool errored,
- whether the run stalled during model streaming or tool execution

But Langfuse is not the main place to open the generated `.tex`, `.bib`, `.pdf`, or image files. Those should be in `workdir/<session_id>/`.

## Where to check

Use both places together:

1. Check `workdir/<session_id>/` for real output files.
2. Check Langfuse with the same `session_id` for trace history and tool-call metadata.

The repo README includes direct Langfuse investigation pointers and SQL examples for filtering by `session_id`.
