# `paper_length`

## Runtime availability

The analyzer is exposed by the `writer_tools` MCP service. Holosophus
CodeArkt calls it by its unprefixed tool name; direct clients use their MCP
server namespace. The service never writes paper files.

The Writer calls `paper_length` as one part of its three-tool quality pass. A
direct call supplies `check_run_id`, `pass_number`, `session_id`, and
`agent_name`. CodeArkt supplies the identity fields automatically. The service
uses the session-relative `pdf_path` registered in `start_writer_check`
(default `paper.pdf`); analyzer calls do not supply paths.

The response includes the analyzer report plus server-owned `check_state`.
One quality pass is complete only after `section_order`, `content_placement`,
and `paper_length` all succeed for that pass. Failed tool calls use the
separate per-tool retry allowance and do not consume a quality rerun.

## Purpose and output

`paper_length` reports physical page count and lengths for major
scientific-paper sections. It combines PDF bookmarks with visible section
headings, preferring rendered coordinates when available. Each recognized
section reports its page bounds, inclusive `page_span`, decimal
`fractional_pages`, and `measurement_source`.

`main_content_pages` measures the document up to Acknowledgments,
Acknowledgements, References, Bibliography, or Appendix. A standalone
Discussion section remains part of main content. If no terminal heading is
found, measurement falls back to the full document.
`main_content_measurement_source` states whether the value came from a rendered
heading, outline-page fallback, or document end.

The MCP service has no universal page minimum. Compatibility fields remain:

- `minimum_main_content_pages`: null;
- `meets_main_content_minimum`: whether a positive main-content measurement was obtained;
- `main_content_page_deficit`: null.

A missing recognized main-body heading prevents a clean check. The writer
compares the measured length with any explicit user or venue requirement.
The service persists the verdict and verifies that inputs have not changed
when `finish_writer_check(outcome="clean")` is called.

Missing or unreadable PDFs, missing headings, lower-precision outline
fallbacks, and outline/rendered disagreements appear in `diagnostics`.

## Verification

Run from `Holosophus/holosophos`:

```bash
UV_CACHE_DIR=/tmp/holosophos-uv-cache \
  uv run --no-sync pytest -q \
  tests/tools/writer/test_writer_paper_length_tool.py \
  tests/tools/writer/test_writer_mcp_server.py
```

For a factual check against the bundled papers:

```bash
UV_CACHE_DIR=/tmp/holosophos-uv-cache \
  uv run --no-sync python tests/tools/writer/check_paper_length_examples.py
```
