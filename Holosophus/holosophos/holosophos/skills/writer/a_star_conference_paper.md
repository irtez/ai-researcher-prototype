# Skill: A* Conference Paper Requirements

## Purpose

Use this optional skill only when the user explicitly requests a submission to a top-tier ML/AI/NLP conference, such as ICML, NeurIPS, ICLR, ACL, EMNLP, COLM-style venues, or a similarly selective A*/Q1 venue.

This skill defines the expected **conference-paper standard**: target length, section density, contribution framing, evidence quality, reviewer-facing completeness, and final argumentative closure.

This skill does not define LaTeX compilation, citation verification, tool usage, or mandatory file outputs. Those are handled by the agent definition.

---

## Requested Venue and Length

Use the length and format actually requested for the target venue. There is no universal minimum or maximum; do not infer a page limit from this skill. Main content excludes Acknowledgments/Acknowledgements, References, Bibliography, and Appendix material. A standalone Discussion section counts as main content.

Default page-budget policy:

* Main paper: use the explicit task or venue length, including its maximum if given.
* Acknowledgments/Acknowledgements: excluded from the main-content target.
* References: excluded from the main-content target.
* Bibliography: excluded from the main-content target.
* Appendix: encouraged for extra proofs, ablations, prompts, datasets, implementation details, and reproducibility material.
* Prefer a dense main paper plus a strong appendix.

---

## Approximate Main-Paper Budget

Use the existing required sections from the agent definition, but aim for this approximate density:

| Component                              |         Target |
| -------------------------------------- | -------------: |
| Abstract                               |  150–250 words |
| Introduction                           |    1–1.5 pages |
| Related Work                           |        ~1 page |
| Method                                 |  1.5–2.5 pages |
| Experimental Setup                     |        ~1 page |
| Results                                |    1–1.5 pages |
| Analysis / Ablations                   |        ~1 page |
| Limitations / Ethics / Reproducibility |     0.5–1 page |
| Conclusion                             | 0.25–0.5 pages |

The goal is not to mechanically hit these numbers. The goal is to make the paper feel like a complete conference submission rather than a short technical note.

---

## A* Paper Standard

A strong A* conference paper should make it easy for reviewers to answer:

1. What is the problem?
2. Why does the problem matter?
3. What is the precise gap in prior work or current practice?
4. What is the main contribution?
5. Why should the proposed idea work?
6. What evidence supports the claims?
7. What are the strongest baselines or comparisons?
8. What are the limitations?
9. Can the work be reproduced?
10. What should the reader believe after reading the paper?
11. Why is this paper worth accepting?

The paper should follow this argument flow:

**Problem → Gap → Idea → Method → Evidence → Interpretation → Limitations → Conclusion.**

Avoid writing a paper that is only a cleaned-up project report. The paper must have a clear scientific claim and a reviewer-facing argument.

---

## Contribution Requirements

The contribution list should be concrete, checkable, and not inflated.

Good contributions are specific:

* a new method, benchmark, dataset, evaluation protocol, analysis, system, or empirical finding;
* a clear improvement over relevant baselines;
* a reproducible experimental pipeline;
* a careful analysis that changes how the reader understands the problem.

Avoid vague contributions:

* “We propose a novel framework.”
* “We show good performance.”
* “We use AI to improve the task.”
* “We provide extensive experiments” without saying what they establish.

For A* style, every contribution should correspond to evidence later in the paper.

---

## Density and Completeness Rule

If the paper is too short, expand with scientifically useful material, not filler.

Good ways to expand:

1. sharper problem formulation;
2. clearer novelty statement;
3. deeper related-work positioning;
4. more detailed method explanation;
5. explicit design rationale;
6. stronger experimental protocol description;
7. more careful result interpretation;
8. ablation or sensitivity analysis;
9. error analysis;
10. concrete limitations and reproducibility details;
11. a stronger conclusion that synthesizes the actual scientific takeaway.

Bad ways to expand:

1. generic background;
2. repeated motivation;
3. inflated claims;
4. vague future work;
5. unnecessary adjectives;
6. long paragraphs that do not add evidence;
7. conclusions that merely repeat the abstract.

Writer-tools reports page count without enforcing a universal minimum. Check the explicitly requested venue or task limit separately.

---

## Reviewer-Facing Writing Standard

Write for skeptical expert reviewers.

Every important claim should be connected to one of:

* experimental evidence;
* a formal argument;
* a cited prior result;
* a clearly marked hypothesis;
* a stated limitation.

Use calibrated claim strength:

* weak evidence: “suggests”, “indicates”, “is consistent with”;
* moderate evidence: “shows in this setting”, “improves over the tested baselines”;
* strong evidence: “consistently improves across datasets, seeds, and ablations”.

Do not use strong claims such as “solves”, “proves”, “guarantees”, “state-of-the-art”, or “universally” unless the provided evidence directly supports them.

---

## Conclusion Policy

Include a concise conclusion section unless the target venue or user explicitly requests a format without one.

The conclusion should close the paper's argument. It should briefly synthesize:

* the problem addressed;
* the main idea or contribution;
* the strongest evidence supporting the contribution;
* the most important limitation or boundary condition;
* the broader implication of the result.

A good conclusion is usually short, around 0.25–0.5 pages. It should synthesize the paper rather than repeat the abstract.

Do not introduce new methods, new experiments, new baselines, or unsupported claims in the conclusion.

Avoid generic conclusions such as:

* “In this paper, we proposed a framework and showed promising results.”
* “Future work will explore more models and datasets.”
* “Our method is effective and useful.”

Prefer a concrete closing statement that makes clear what the reader should now believe after reading the paper.

---

## Appendix Policy

Use the appendix to preserve completeness without overloading the main paper.

Move to appendix:

* full hyperparameter grids;
* extra dataset statistics;
* additional ablations;
* implementation details;
* full prompts;
* proofs or derivations;
* extended qualitative examples;
* reproducibility checklists.

Do not move the core idea, main method, main results, key limitations, or conclusion entirely to the appendix.

The main paper must stand alone.

---

## Final A* Readiness Check

Before finalizing the paper, check:

* The main body is close to the expected conference length.
* The paper has a clear central claim.
* The contribution is explicit and checkable.
* The method is detailed enough to understand and mostly reproduce.
* The experiments answer clear research questions.
* Results are interpreted, not merely reported.
* Limitations are concrete and honest.
* The conclusion clearly closes the argument without introducing unsupported new claims.
* The paper avoids unsupported state-of-the-art or superiority claims.
* The appendix supports the main paper but does not replace it.
* The final result feels like a complete conference submission, not a short report.
