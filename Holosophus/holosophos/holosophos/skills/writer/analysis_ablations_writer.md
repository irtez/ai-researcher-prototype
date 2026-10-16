# Skill: Analysis and Ablations Writer

## Purpose

Use this skill when the task or claims require an Analysis / Ablations section. Analysis need not include ablations; include only checks justified by the method and available evidence.

---

## Role in an Academic Paper

The Analysis section explains why the results look the way they do. Ablations isolate the contribution of individual components. Together they demonstrate that the authors understand their method and that improvements are not accidental. Follow the section budget in the task-specific skeleton.

---

## Required Content

1. **Ablation studies, when needed**: use available component comparisons to support claims about individual contributions.
2. **Sensitivity analysis**: how do key hyperparameters or design choices affect performance?
3. **Error analysis**: what does the method get wrong and why?
4. **Qualitative examples**: representative success and failure cases where appropriate.
5. **Insights**: what do the analysis results tell us about the method or the problem?

---

## How to Use the Skeleton

- Use the skeleton's contribution list to identify which components need ablation.
- Check the evidence plan for planned ablation experiments.
- If ablations are needed for a stated claim but unavailable, record the evidence gap or weaken the claim. Do not add ablation TODOs for tasks that do not need them.

---

## Previous Sections as Context

- Use the Results section to identify which findings need deeper explanation.
- Use the Method section to identify which components can be ablated.
- Ensure analysis is consistent with the claims made in Results.

---

## Common Failure Modes

- **Unsupported component claim**: attributes an effect to a component without evidence. Fix: provide relevant ablation evidence, record the gap or weaken that claim.
- **Inventing ablation results**: reports specific ablation numbers not in the provided evidence. Fix: use TODOs.
- **Ablation without insight**: presents ablation tables but does not explain what they show. Fix: add interpretation.
- **Superficial error analysis**: "the method sometimes fails on hard examples" without specifics. Fix: identify concrete failure patterns.
- **Missing from skeleton**: skeleton planned ablations that are not addressed here. Fix: include them or add a TODO.

---

## What This Section Should NOT Do

- Should not re-present the main results from the Results section.
- Should not introduce new methods or approaches.
- Should not invent ablation results or analysis.

---

## Section-Specific Self-Check

After drafting, verify:
- Does the Analysis/Ablations section fit its task-specific skeleton budget?
- Are ablations tied to specific method components?
- Are all reported ablation numbers from provided evidence?
- Is there interpretation of the ablation results?
- Are error patterns identified concretely?
- Are any skeleton-planned ablations missing (and marked as TODO)?
