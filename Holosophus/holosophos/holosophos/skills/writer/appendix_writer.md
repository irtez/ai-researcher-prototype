# Skill: Appendix Writer

## Purpose

Use this skill when drafting the Appendix of a full paper.

---

## Role in an Academic Paper

The Appendix provides supplementary material that supports the main paper without overloading it. Reviewers check the appendix for reproducibility details, additional experiments, and proofs. The main paper must stand alone; the appendix adds depth.

---

## Required Content (select as appropriate)

1. **Full hyperparameter grids**: all hyperparameters used in experiments.
2. **Additional dataset statistics**: detailed descriptions beyond what fits in the main text.
3. **Extended ablation studies**: ablations that supplement the main analysis.
4. **Proofs or derivations**: formal proofs referenced in the Method section.
5. **Implementation details**: software versions, hardware specifications, training procedures.
6. **Full prompts**: if the method uses LLM prompts, include them here.
7. **Extended qualitative examples**: additional success/failure cases.
8. **Reproducibility checklist**: if required by the venue.
9. **Additional figures or tables**: supporting visualizations.

---

## How to Use the Skeleton

- Use the skeleton's appendix plan to determine what supplementary material to include.
- Cross-reference the main paper's sections to identify what was deferred to the appendix.
- Check for TODOs from section self-checks that pointed to appendix material.

---

## Previous Sections as Context

- Use the full paper draft to identify what was referenced as "see Appendix" or "details in the supplementary material."
- Ensure all such forward references are fulfilled.

---

## Common Failure Modes

- **Empty appendix**: skeleton planned appendix material but it was never written. Fix: fulfill the appendix plan or remove forward references from the main paper.
- **Core content in appendix**: the main method or key results are only in the appendix. Fix: move essential content to the main paper.
- **Unfulfilled references**: the main paper says "see Appendix A" but Appendix A does not exist. Fix: create the referenced appendix sections.
- **Hallucinated details**: invents hyperparameters or implementation details not in the provided materials. Fix: use TODOs for unknown values.

---

## What This Section Should NOT Do

- Should not contain the core method, main results, or key limitations (those belong in the main paper).
- Should not be a dumping ground for unfinished sections.
- Should not invent any experimental details or results.

---

## Section-Specific Self-Check

After drafting, verify:
- Does the appendix fulfill all forward references from the main paper?
- Is core content in the main paper, not hidden in the appendix?
- Are all reported details from provided materials (not invented)?
- Does the main paper stand alone without requiring the appendix?
