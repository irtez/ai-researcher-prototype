# `section_order`

## Runtime availability

The analyzer is exposed by the `writer_tools` MCP service. Holosophus
CodeArkt calls it by its unprefixed tool name; direct clients use their MCP
server namespace. The service never writes paper files.

The Writer calls `section_order` as one part of its three-tool quality pass. A
direct call supplies `check_run_id`, `pass_number`, `session_id`, and
`agent_name`. CodeArkt supplies the identity fields automatically. The service
uses the session-relative `tex_path` registered in `start_writer_check`
(default `paper.tex`); analyzer calls do not supply paths.

The response includes the analyzer report plus server-owned `check_state`.
`check_state` identifies completed passes, quality reruns used, successful
tools for the current pass, and per-tool failures. The quality-rerun counter
advances only after all three analyzers succeed for the pass.

## Purpose and output

`section_order` checks recognized top-level LaTeX sections against the
expected A*/Q1 ML/AI/NLP order:

1. Abstract
2. Introduction
3. Related Work
4. Method
5. Experiments
6. Results
7. Analysis / Ablations
8. Limitations / Ethics
9. Conclusion
10. References
11. Appendix

It reports ordering inversions, not missing sections. The structured report
contains detected canonical sections, `violations`, unrecognized
`other_headings`, source locations, and diagnostics. The current analyzer
reads the requested `.tex` file directly; unlike `content_placement`, it does
not expand `\input` or `\include` files.

The shared heading aliases support Russian titles and common combined headings
such as “Introduction and Related Work” and “Results and Discussion”. These
are treated as a single section at the position of their leading component.
Separate headings are not required just to satisfy the analyzer. Unknown titles
remain in `other_headings` and do not create ordering violations by themselves.

## Verification

Run from `Holosophus/holosophos`:

```bash
UV_CACHE_DIR=/tmp/holosophos-uv-cache \
  uv run --no-sync pytest -q \
  tests/tools/writer/test_writer_section_order_tool.py \
  tests/tools/writer/test_writer_mcp_server.py
```
