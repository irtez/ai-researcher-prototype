# Smoke reliability fixes, October 2026

## Agent continuation

Claude Code task notifications distinguish a completed assignment from a turn-budget
stop in their text, although both can carry the native `completed` status. The runner
maps the latter to `limited`. A confirmed `SendMessage` resume reopens the same
invocation and UI tab. Its previous summary becomes a historical note. The result
contract waits while the continued agent runs, including when hook callbacks arrive
before the corresponding SDK stream messages. Duplicate terminal messages do not
close a new continuation.

The approval gate resolves a message recipient through recorded agent identities.
Continuing librarian/proposer has the same preparation permissions as launching them.
Unknown recipients do not bypass outstanding approvals. Main specialist budgets are
200 Claude Code turns, reviewer 100. These are model turns, not tool-call counts.

## Papers and reviews

Default session files are `paper.tex`, `references.bib`, `paper.pdf`, `review.json`
and `review.md`. Writer registers the actual article paths in `start_writer_check`.
Tools and preview share that registration, including other filenames/directories.
Unregistered older runs retain root and `paper/` recognition. Other PDFs remain
available as artifacts. A missing registered PDF shows a local document message. A visible PDF is the latest built revision, not proof that review
or the complete research assignment has finished.

The article tab requires registration or recognized legacy article files. Unrelated images,
PDF attachments and bibliography alone do not create it, including through a stale
`/paper` bookmark. Images and PDFs under `mle/` belong to Experiments, with an image
gallery and the usual file preview. Markdown image syntax renders a bounded, clickable
image in saved results and file previews, resolving relative paths from the source
document. A missing image falls back to its link. Saved session files are not rewritten.

`get_latex_template(template_name="research_ru")` provides the Russian template.
The existing English template remains available. The Academia image contains the
Cyrillic language support and embedded CM Super fonts needed by the Russian template.
`compile_latex` resolves figures, local styles and bibliography beside the input TeX,
uses isolated auxiliary output, and atomically publishes the completed PDF. A failed
compilation or publication preserves the previous PDF. Compilation remains inside the
service container. This does not introduce a per-session filesystem sandbox for TeX.

Writer structure and length follow the assignment. The default skill is
`paper_standard.md`; `a_star_conference_paper.md` applies only to an explicitly requested
conference submission. There is no universal eight-page minimum. All three writer
checks are still required, and missing/unreadable inputs cannot count as a clean pass.

`review_pdf_paper` accepts the existing `pdf_filename` and `research_profile`, plus:

- `response_language`: `ru` by default, `en` on explicit request.
- `task_context`: optional concise assignment and scope.
- `target_venue`: optional explicitly requested venue.

Computer-science review assesses correctness, evidence, reproducibility and the stated
scope. Low novelty alone does not reject a valid educational/comparative analysis.
Chemistry review retains its domain criteria. Both receive PDF text and page images.
The review model is configured separately through Academia's `REVIEW_MODEL_NAME`.

Reviewing the registered article (legacy default `WORKSPACE_DIR/<session>/paper.pdf`) saves the
unchanged structured response to `review.json` and a readable version to `review.md`.
Arbitrary external/example PDFs do not overwrite these files. The UI reads native
nested scores and aspect lists, while retaining compatibility with older flat reviews.
Questions, limitations and formatting findings have separate labels.

## External searches

Hugging Face dataset search uses the supported descending sort without the removed
`direction` argument. Explicit ascending order is rejected rather than simulated by
reversing a truncated top-N result. The result limit is capped at ten.

Semantic Scholar search caps retries at two, uses short connection/read timeouts,
and does not let `Retry-After` prolong a rate-limited search. A sustained 429 reports
the problem and suggests another available search source. Other tools retain their
existing retry defaults.

## Verification without a research run

The offline checks exercise generated prompts, approval permissions, the driver and
UI lifecycle, raw review persistence, both PDF languages, file-path handling, atomic
publication failures and external-search request handling. They do not measure the
quality of a real model-written article or review.

- `make verify`: configuration, prompt contracts, UI tests/build and documentation.
- `tests/agent_resume_cli_probe.py`: installed Claude CLI and SDK against a scripted
  localhost Messages API, including limit → resume → finish. No real model/account.
- `ui/tests/smoke-reliability-browser.py`: built UI and real history API with scripted
  events, live continuation, reload/replay, canonical PDF and nested review fields.
- `ui/tests/experiment-images-browser.py`: real API and built UI, plot classification,
  article-tab conditions, relative Markdown images, file preview and image fallback.
- `academia_mcp/tests/test_latex_workspace.py`: real English/Russian TeX compilation,
  source-relative dependencies and preservation of an existing PDF on failure.
- `academia_mcp/tests/test_review_contract.py`: language, context, score schema and
  canonical artifact persistence at a substituted model boundary.
- `academia_mcp/tests/test_external_search_offline.py`: real client/request code with
  local HTTP responses and no production API calls.

Tests that explicitly invoke live LLMs remain opt-in and are not part of this pass.
