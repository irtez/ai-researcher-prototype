# Skill: Paper Skeleton Planner

## Purpose

Use this skill when building a structured paper skeleton before writing any section content. The skeleton is the first mandatory step of the full-paper writing workflow.

The skeleton defines the paper's argument structure, section plan, evidence requirements, and page budget. All subsequent section drafting uses the skeleton as the primary planning context.

---

## When to Use

- At the start of every full-paper writing task, before any section is drafted.
- When the task is to write a complete paper, conference submission, or full-length report.

Do not use this skill for local edits, paragraph rewrites, formatting fixes, or single-section patches.

---

## Skeleton Contents

The skeleton must include all of the following:

### Paper identity

- **Working title**: one or two candidate titles.
- **Target venue or venue family**: record only an explicitly requested venue; otherwise write "unspecified".
- **Target document length**: follow the requested length, or choose a concise evidence-based budget. There is no fixed minimum page count. A PDF page target includes all pages, including references and appendices, unless the user or requested venue explicitly excludes them. Record any such exclusions and whether the target is approximate or strict. Do not turn approximate targets into hard limits. The tool's separate main-content measurement is not the total PDF budget.
- **Paper type**: method / benchmark / system / empirical study / dataset / analysis / mixed.

### Argument core

- **Central claim**: the single main statement the paper defends.
- **Problem**: what real-world or research problem motivates this work.
- **Gap or question**: what the task seeks to establish; a literature gap or novel method is not required for a comparison, reproduction or educational analysis.
- **Main idea**: the proposed approach, method, system, or insight.

### Contributions and evidence

- **Contribution list**: concrete, checkable contributions (not vague "we propose a novel framework").
- **Required evidence per contribution**: what experimental result, analysis, proof, or external reference supports each contribution.

### Section plan

- **Section list**: ordered list of all paper sections.
- **Approximate page budget per section**: allocate the requested total length according to the evidence and section roles; use `paper_standard.md`.
- **Key points per section**: the 2–5 most important points each section must convey.
- **Required evidence per section**: what data, results, citations, or arguments the section needs.

### Gaps and risks

- **Missing information**: any data, results, citations, or details not yet available.
- **Appendix plan**: what supplementary material should go to the appendix.
- **Reviewer-facing risks**: likely reviewer objections, weak spots, or missing comparisons.

---

## Argument Flow

The skeleton must follow this argument flow:

**Question/Problem → Context → Approach → Method → Evidence → Interpretation → Limitations**

Every contribution should answer the stated question and connect forward to evidence. If a contribution has no planned evidence, flag it under missing information.

---

## Skeleton Artifact

Save the skeleton as `paper_skeleton.md` in the same directory as the paper output files. This artifact is for observability and debugging. It does not replace the required final outputs (`.tex`, `references.bib`, `.pdf`).

---

## Common Failure Modes

- **Skeleton is too vague**: contributions say "we propose a method" without specifying what the method does or what evidence supports it. Fix: make every contribution concrete and checkable.
- **No evidence plan**: sections are listed without specifying what results or citations they need. Fix: every section must have a required-evidence field.
- **Missing information is ignored**: the skeleton does not flag unavailable data or results. Fix: always list missing information explicitly.
- **Page budget is unrealistic**: sections do not fit the requested length or evidence. Fix: rebalance the skeleton without padding.
- **Argument flow is broken**: results appear without the question or method needed to interpret them. Fix: connect question, approach, evidence, interpretation and limitations.

---

## Self-Check Before Proceeding

Before moving to section drafting, verify:

1. Every required skeleton field is filled.
2. Every contribution has planned evidence.
3. Missing information is explicitly listed.
4. Page budgets fit the requested length and any explicitly requested venue rules. There is no fixed minimum.
5. The argument flow is complete and coherent.
6. Reviewer-facing risks are identified.
