# Skill: Evidence-Based Article and Report

## Purpose

Use this standard for computer-science articles, comparisons, reproductions,
educational analyses and technical reports. Match the user's question, requested
format and supported evidence. Apply venue requirements only when that venue was
explicitly requested; do not assume a conference submission.

## Scope and Length

Follow the requested length, including short articles. There is no fixed minimum
page count. When length is unspecified, choose a concise evidence-based budget in
`paper_skeleton.md`. Do not add padding, unnecessary experiments or unsupported
novelty to reach a page target. Section skills guide content; their examples do not
create new obligations outside the task.

A PDF page target includes references and appendices unless explicitly excluded by the user or requested venue. Treat approximate length as a guideline allowing small deviations for content and readability. Only explicit user or venue limits are strict. Preserve the template's readable font size and move secondary detail to supporting files when needed.

The page-count tool is informational. Check explicit user or venue limits against
its measurements. Main-content measurements exclude Acknowledgments/Acknowledgements,
References, Bibliography and Appendix; standalone Discussion counts as main content.

## Evidence and Structure

- State the question and the conclusion supported by the available evidence.
- Explain data, methods, transformations, evaluation and limitations sufficiently
  for the actual work to be understood and reproduced.
- Use a structure suited to the task. Related Work, Experimental Setup,
  Analysis/Ablations and Ethics/Broader Impact are included where relevant.
- A correct comparison or reproduction can be valuable without a novel method.
  State its contribution accurately and keep novelty separate from correctness.
- Include baselines, repeated seeds, training details, ablations or statistical
  tests only when justified by the actual methods and claims. Do not invent them
  or add placeholders for experiments the task does not require.
- Tie claims to observed results, calculations or verified references. If required
  evidence is missing, weaken or remove the unsupported claim and report the gap.

## Completion

Keep the staged skeleton, sequential drafting, section self-check and global
check workflow. Compile `paper.pdf`, verify references and complete all three
deterministic checks on the same canonical revision. Missing or unreadable inputs
remain blockers. A short but complete document can pass the gate.
