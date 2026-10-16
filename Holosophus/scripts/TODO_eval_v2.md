# GAIA Eval Runner v2 — TODO

Issues from first run (2026-03-28):

## Logging
- [ ] `gaia_eval_run.log` is empty — nohup stdout not flushing. Fix: add `flush=True` to prints, or use `logging` with file handler.
- [ ] No visibility into agent actions — can't see if manager delegates to librarian/mle-solver. Fix: capture `AssistantMessage` events from SDK stream and log tool_use blocks with timestamps.

## File isolation
- [ ] MCP `download_pdf_paper` still writes to `workdir/` root (UUID-named PDFs). This is server-side behavior (Docker WORKSPACE_DIR=/workdir). Fix options:
  - Post-run cleanup: `mv workdir/*.pdf eval_dir/`
  - Or add cleanup step in `run_question()` after SDK query completes
  - Long-term: patch academia MCP to accept destination path

## Response format
- [ ] `response` field contains full reasoning chain, not just the answer. Fix: store both `response_full` (for debugging) and `response_answer` (extracted via `extract_final_answer()`).

## Metrics
- [ ] No token counts in results. SDK `ResultMessage` has `model_usage` field — extract and save `input_tokens`, `output_tokens`, `cache_read_input_tokens`.
- [ ] No info on which sub-agents were used. Fix: count Agent tool_use blocks in AssistantMessage stream.

## Trajectory logging
- [ ] Each SDK query should produce a trajectory log (like view_session.py output) showing:
  - Manager reasoning steps
  - Agent delegations (which agent, what prompt)
  - Tool calls per agent (name, args, result preview)
  - Token usage per step
  This is the main gap vs Langfuse in the old CodeArkt setup.
