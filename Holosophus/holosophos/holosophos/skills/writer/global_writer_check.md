# Skill: Global Writer Check

## Purpose

Use this skill after all sections have been drafted and assembled into a full paper. The global Writer check is an internal full-paper coherence and completeness pass. It is NOT the Reviewer Agent.

---

## When to Use

- After all sections are written, section self-checks are complete, and the paper is assembled as a single LaTeX document.
- Before final compilation and output production.

---

## Global Check Criteria

### Central claim consistency

- Is the central claim stated clearly in the Abstract and Introduction?
- Do all sections consistently support the same central claim?
- Does the Conclusion accurately summarize what was actually shown?

### Contribution and evidence alignment

- Does every listed contribution have corresponding evidence later in the paper?
- Are contributions stated in the Introduction consistent with what the Results and Analysis sections actually deliver?
- Are there contributions with no supporting evidence? If so, they must be removed, weakened, or marked as TODO.

### Research questions and experiments

- Do the experiments answer the research questions or hypotheses stated in the Introduction or Method?
- Where comparisons are part of the work, are they fair and are the baselines appropriate?

### Interpretation quality

- Are results interpreted, not merely reported?
- Does the Analysis section explain why results look the way they do?
- Are unexpected or negative results discussed honestly?

### Limitations

- Are limitations concrete and honest?
- Do limitations cover methodology, data, evaluation, and generalization scope?
- Are limitations not buried or trivialized?

### Claim calibration

- Does the paper avoid unsupported SOTA or superiority claims?
- Is claim strength calibrated to the available evidence?
- Are forbidden phrases ("state-of-the-art", "significantly outperforms", "proves", "guarantees", "solves", "universally improves") used only when evidence directly supports them?

### Section transitions

- Do sections flow naturally into each other?
- Are there abrupt topic shifts or missing connective text?

### Repeated content

- Is any substantial content duplicated across sections?
- Are there paragraphs that belong in one section but appear in another?

### Missing sections

- Are the sections required by the task and skeleton present? Optional experimental, ablation and ethics sections are included only when relevant.
- Is the Appendix present if the skeleton planned supplementary material?

### Density and length

- Does the paper fit the requested length and any explicitly requested venue rules? There is no fixed minimum page count.
- Does total PDF length respect the requested scope, including references and appendices unless explicitly excluded? Allow small deviations from approximate targets for necessary content and readability, including after review. Enforce only explicit user or venue limits, and avoid repeated layout-only revisions for approximate counts. Do not substitute the tool's separate main-content measurement for this total.
- Is any section significantly over or under its page budget?
- Page count is informational. Do not add padding or irrelevant work; check any explicit length limit against the measured result.

### Appendix separation

- Is core content (main method, main results, key limitations) in the main body?
- Are supplementary details (hyperparameters, extra ablations, proofs, prompts, dataset statistics) in the appendix?
- Does the main paper stand alone without requiring the appendix?

### Remaining TODOs

- Are there unresolved TODOs or placeholders?
- If yes, are they clearly marked and not disguised as real content?

### Compilation

- Does the LaTeX compile without errors?
- Are all `\ref` and `\cite` keys resolved?

### Deterministic MCP quality gate

For every full-paper finalization, compile the current revision, call
`start_writer_check` once, and keep the returned `check_run_id`, `current_pass`,
and server limits. Send `current_pass` as `pass_number` to every analyzer call.
For each pass, call all three analyzer tools against the same revision:

- `section_order`
- `content_placement`
- `paper_length`

A failed analyzer call does not complete the pass and does not consume a quality
rerun. Retry only the failed tool while the server permits it. After all three
tools succeed, the pass is clean only if:

- there are no section-order violations;
- there are no forbidden or discouraged content placements;
- the PDF has a valid main-content measurement (`meets_main_content_minimum` remains the compatibility field for this verdict);
- required sections are present;
- no missing input, unreadable input, or parser-failure diagnostic remains.

If substantive findings or an explicit task requirement need repair and the server
allows another pass, repair from supported evidence, recompile, and use the next
`current_pass` from `check_state`. Never invent results, citations, baselines,
datasets, metrics, or evidence to satisfy length or pass the gate. Call
`finish_writer_check` with outcome `clean` only after a clean pass. If the
quality-rerun or tool-failure limit is exhausted, call
`finish_writer_check` with outcome `blocked`, preserve the draft, and report the
unresolved findings.

---

## Required Actions

### Fixable issues

If the global check finds issues that can be repaired with available information, fix them now:
- Strengthen weak transitions.
- Remove duplicated paragraphs.
- Calibrate overclaimed language.
- Move supplementary details to the appendix.
- Add missing section connectives.

### Unfixable issues

If issues cannot be fixed because information is missing:
- Keep explicit TODOs/placeholders.
- Do NOT hallucinate missing results, citations, or evidence.
- Report remaining gaps in the final answer.

---

## Final Verification Checklist

Before producing the final output, confirm:

1. The complete PDF fits the requested length and supported evidence without a fixed minimum, with any explicit venue or user exclusions applied consistently.
2. The paper has a clear, consistently stated central claim.
3. Every contribution is explicit, checkable, and supported by evidence.
4. The method is detailed enough to understand and mostly reproduce.
5. Methods and any experiments answer the stated question.
6. Results are interpreted, not merely reported.
7. Limitations are concrete and honest.
8. The paper avoids unsupported state-of-the-art or superiority claims.
9. The appendix supports the main paper but does not replace it.
10. All TODOs are explicit, not hidden.
11. The paper compiles as valid LaTeX.
12. All `\cite` keys are verified against `references.bib`.
