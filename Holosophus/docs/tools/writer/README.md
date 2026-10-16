# Writer Tools MCP

`writer-tools` is a small MCP service that runs deterministic final checks on a
Writer paper. It reads `.tex` and `.pdf` files from `Holosophus/workdir`, returns
structured findings, and stores check progress in SQLite. It does not edit the
paper.

The service is currently enabled for the Writer agent. Other agents can opt in
later by adding the tool names to their allowlists.

## Tools

| Tool | Purpose |
| --- | --- |
| `start_writer_check` | Registers optional session-relative `tex_path`/`pdf_path` and returns check identity, paths, pass and limits. |
| `section_order` | Finds inversions in the top-level LaTeX section order. |
| `content_placement` | Checks whether figures, tables, equations, algorithms, proofs, and similar content appear in appropriate sections. |
| `paper_length` | Measures main content without a universal page minimum. |
| `finish_writer_check` | Closes a check as `clean`, `blocked`, or `abandoned`. |

For analyzer behavior and report fields, see
[`section_order.md`](section_order.md),
[`content_placement.md`](content_placement.md), and
[`paper_length.md`](paper_length.md).

## How the Agent Sees the Tools

At startup, Holosophus mounts the `writer_tools` MCP server in CodeArkt. CodeArkt
discovers the MCP schemas and gives each agent only the tools in that agent's
allowlist. The Writer allowlist contains all five tools; the manager, reviewer,
librarian, and proposer do not receive them.

Inside the CodeArkt Writer's `<execute>` block, the tools are ordinary,
unprefixed Python callables:

```python
check = start_writer_check()
order = section_order(
    check_run_id=check["check_run_id"],
    pass_number=check["current_pass"],
)
```

CodeArkt binds the active `session_id` and `agent_name` to each call
automatically, and generated code cannot replace those values. Writer-tools
uses the article paths registered in `start_writer_check`. Defaults are
`paper.tex` and `paper.pdf` relative to the session. Optional `tex_path` and
`pdf_path` belong only to the start call; analyzers reuse those stored paths.

A direct client, such as the generated Claude Code Writer, sees namespaced
tools such as `mcp__writer_tools__section_order`. It must send `session_id` and
`agent_name` explicitly, reuse the same values throughout one check, register
the actual article paths in that session, and omit analyzer paths. The integrated
Claude runner binds `session_id` to the active session as well.
The generated Claude Writer prompt already includes these tools and
instructions. For an external client with no Holosophus session, use a stable
value such as `session_id="external"` and `agent_name="writer"`.

## Check Lifecycle

1. Compile the current `.tex` revision to `.pdf`.
2. Call `start_writer_check` once and retain `check_run_id`, `current_pass`, and
   the returned limits. Set optional session-relative `tex_path` and `pdf_path`
   when the actual article uses other names or directories.
3. For that pass, call `section_order` and `content_placement` against the
   registered TeX, and `paper_length` against its regenerated PDF. Send `current_pass` as `pass_number`.
4. If all reports are clean and `meets_main_content_minimum` is true, call
   `finish_writer_check(..., outcome="clean")`.
5. Otherwise, repair and recompile the paper, then repeat the three analyzers
   using the next `current_pass`. If the problem cannot be repaired or the limit
   is exhausted, finish with `outcome="blocked"`.

One pass completes only after all three analyzers succeed. The default
`WRITER_CHECK_MAX_RERUNS=4` therefore permits the initial pass plus four repair
passes. A failed tool call does not consume a quality rerun; it increments only
that analyzer's failure count. The default
`WRITER_CHECK_TOOL_MAX_RETRIES=10` blocks the check after ten failures of one
analyzer in the current pass.

The service owns this state in `/state/checks.sqlite3`, persisted by the
`writer_tools_state` Docker volume. A check belongs to its
`(check_run_id, session_id, agent_name)` tuple, so separate agents and sessions
cannot accidentally resume each other's runs. Paper files are mounted
read-only.

A clean finish also records the fingerprints of the checked TeX and PDF in
the shared article registry. The UI compares them with the current files and
can display a notice that the paper changed after its check. This read-only
notice does not reopen a completed check, remove the previous review, require
manager action, or affect research-run completion. A subsequent ordinary
writer check records the new revision. Older registrations without fingerprints
remain usable, but their freshness cannot be determined.

For each successful `paper_length` call, `pass_tools` stores
`main_content_pages`, `minimum_main_content_pages`, and
`meets_main_content_minimum`. These compact fields make clean finalization
authoritative and directly inspectable without storing the full analyzer
report.

## Run the Service

From `Holosophus/`, start the complete stack:

```bash
docker compose up --build -d
```

The Writer MCP endpoint is available on loopback at
`http://127.0.0.1:5059/mcp`. It is also registered in `.mcp.json` as
`writer_tools` for direct MCP clients.

To rebuild only this service after changing Writer tool code:

```bash
make recreate-writer-tools
```

Useful operational commands:

```bash
docker compose ps writer-tools
docker compose logs -f writer-tools
docker compose restart writer-tools
docker compose stop writer-tools
```

Configuration lives in `.env` and defaults are documented in `.env_example`:

```dotenv
WRITER_TOOLS_MCP_URL=http://localhost:5059/mcp
WRITER_CHECK_MAX_RERUNS=4
WRITER_CHECK_TOOL_MAX_RETRIES=10
WRITER_CHECK_STATE_RETENTION_HOURS=168
```

The Compose service uses internal state path `/state/checks.sqlite3`. Stale
active checks become `abandoned`, and old finished checks are removed, after
the retention period when the next check starts.

## Test Writer on One Distilled Paper

Use the single-pack runner to test the Writer agent on one specific distilled
paper. This sends only the selected distilled pack to Writer and lets Writer use
its normal tools, including the five `writer_tools` MCP tools. It does not give
Writer the original paper.

Start the stack, then run from `Holosophus/`:

```bash
docker compose up --build -d

PACK="benchmark/data/benchmark_distilled/paper_1/acl__2024__quantized_side_tuning_fast_and_memory_efficient_tuning_of_quantized_large_language_models__oracle"
SESSION_ID="writer_tools_test__paper_1__oracle__001"

NO_PROXY=127.0.0.1,localhost \
no_proxy=127.0.0.1,localhost \
python3 scripts/run_writer_pack.py "$PACK" \
  --session-id "$SESSION_ID"
```

Use `run_writer_pack.py`, not the multi-pack runner, when selecting one exact
paper. Do not pass `--evaluator`: the evaluator measures the finished package
but is not part of testing whether Writer can write the paper with its tools.

The runner copies the distilled pack to `workdir/$SESSION_ID`, sends
`writer_handoff.md` directly to `/agents/writer`, streams the run, and checks for:

```text
workdir/<session_id>/paper.tex
workdir/<session_id>/references.bib
workdir/<session_id>/paper.pdf
workdir/<session_id>/writer_events.jsonl
```

Use a new `SESSION_ID` for each clean test. This avoids overwriting an earlier
session while preserving its outputs and trace.

If you intentionally reuse a session, add `--overwrite`. Some older runs may
contain cache directories written by a container as `nobody:nogroup`; in that
case host-side overwrite fails with `PermissionError`. Either choose a new
session ID, which is safest, or first restore ownership of that one session:

```bash
WORKDIR="workdir/<old-session-id>"
sudo chown -R "$(id -u):$(id -g)" "$WORKDIR"
```

Then rerun the single-pack command with `--overwrite`. Never apply this ownership
command to the whole `workdir` tree.

For all runner options and benchmark metadata behavior, see
[`../../run_writer_pack_scripts.md`](../../run_writer_pack_scripts.md).

## Test the MCP Service

With `writer-tools` running, execute the end-to-end MCP smoke test from
`Holosophus/`:

```bash
make smoke-writer-tools
```

The smoke test connects through Streamable HTTP, verifies the exact public tool
list, creates sample files under `workdir/smoke__writer_tools/`, completes all
three analyzers, and finishes the check.

To prove that SQLite state survives a service restart, run the smoke test in two
phases:

```bash
uv run python scripts/smoke_test_writer_tools_mcp.py \
  --phase start --session-id smoke__writer_restart
docker compose restart writer-tools
uv run python scripts/smoke_test_writer_tools_mcp.py \
  --phase resume --session-id smoke__writer_restart
```

Run the focused unit and MCP adapter tests from `Holosophus/holosophos/`:

```bash
UV_CACHE_DIR=/tmp/holosophos-uv-cache \
  uv run --no-sync pytest -q tests/tools/writer tests/test_writer_tools_config.py
```

Run the Writer prompt/Claude exposure contract separately when tool visibility
or Writer instructions change:

```bash
UV_CACHE_DIR=/tmp/holosophos-uv-cache \
  uv run --no-sync pytest -q tests/test_writer_prompt_contract.py
```

## Troubleshooting

- **`Writer HTTP error 502` with no matching request in app logs:** a configured
  `HTTP_PROXY`/`HTTPS_PROXY` is intercepting the request to `127.0.0.1`. Run the
  command with both `NO_PROXY=127.0.0.1,localhost` and
  `no_proxy=127.0.0.1,localhost`, as shown above.
- **Connection refused on port 5059:** check `docker compose ps writer-tools`
  and `docker compose logs writer-tools`.
- **`check run not found`:** reuse the original `check_run_id`, `session_id`,
  and `agent_name` exactly.
- **`pass number does not match current pass`:** use `current_pass` from the
  latest returned `check_state`.
- **Path validation error:** put the file under `Holosophus/workdir` and pass a
  workspace-relative `.tex` or `.pdf` path.
- **Check becomes `blocked`:** inspect `tool_failures`; one analyzer reached
  `WRITER_CHECK_TOOL_MAX_RETRIES`, or the quality-rerun limit was exhausted.

## Article registration and display

`paper_registry_state` stores document metadata separately from research files.
Writer-tools and Academia mount it read/write at `/workdir/.paper_registry`;
the UI mounts it read-only. `PAPER_REGISTRY_DIR` overrides the directory for
non-Compose deployments. All three services must use the same registry.

`start_writer_check` validates paths within the active session and records one
article. `finish_writer_check` updates only that document status and verifies
that checked inputs still match. A missing or invalid document is a local tool
error; this metadata never controls agent stopping, continuation or run completion.

Academia automatically saves `review.json` and `review.md` for that registered PDF,
checking identity and content hash after the model responds. Reviews of unrelated
PDFs do not overwrite them. A prior review remains available after a revision of
the same paper. Registering a different PDF removes its association with the old
review without deleting files. The UI follows registration; without metadata it
retains legacy root and `paper/` layout recognition. It does not scan for an arbitrary PDF.
