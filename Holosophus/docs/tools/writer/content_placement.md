# `content_placement`

## Runtime availability

The analyzer is exposed by the `writer_tools` MCP service. Holosophus
CodeArkt calls it by its unprefixed tool name; direct clients use their MCP
server namespace. The service never writes paper files.

The Writer calls `content_placement` as one part of its three-tool quality
pass. A direct call supplies `check_run_id`, `pass_number`, `session_id`, and
`agent_name`. CodeArkt supplies the identity fields automatically. The service
uses the session-relative `tex_path` registered in `start_writer_check`
(default `paper.tex`); analyzer calls do not supply paths.

The response includes the analyzer report plus server-owned `check_state`.
One quality pass is complete only after all three analyzers succeed. A failed
call increments only this tool's failure count; it does not consume one of the
four default quality reruns. The default per-tool failure limit is 10.

## Purpose and output

`content_placement` deterministically checks whether LaTeX figures, tables,
algorithms, displayed equations, theorem-like blocks, proofs, and code
listings appear in appropriate scientific-paper sections. Its bundled policy
is an A*/Q1 ML/AI/NLP writing convention, not an official venue rule.

The structured report contains policy metadata, workspace-relative
`source_files`, a classification summary, findings, and recoverable
diagnostics. Each finding includes artifact type, classification, severity,
section, source location, evidence, and a repair-oriented message.

Common combined headings in Russian and English are recognized, including
“Введение и обзор литературы” and “Introduction and Related Work”. An unknown
top-level section has no inferred type and produces `unknown_section`, not a
placement error. Unknown subsections inherit their actual enclosing section,
never the type of a preceding sibling. The abstract environment ends at
`\end{abstract}`.

Static local `\input` and `\include` files are expanded. Included paths are
resolved against the mounted workspace; an include that escapes it is skipped
and reported as `include_outside_workspace`. Missing includes and include
cycles are also diagnostics. Comments, macro definitions, and listing bodies
are ignored where appropriate.

## Verification

Run from `Holosophus/holosophos`:

```bash
UV_CACHE_DIR=/tmp/holosophos-uv-cache \
  uv run --no-sync pytest -q \
  tests/tools/writer/test_writer_content_placement_tool.py \
  tests/tools/writer/test_writer_mcp_paths.py \
  tests/tools/writer/test_writer_mcp_server.py
```
