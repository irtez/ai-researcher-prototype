# Skill: Results Writer

## Purpose

Use this skill when drafting the Results section of a full paper.

---

## Role in an Academic Paper

The Results section presents the experimental findings. It reports what happened when the experiments were run, with tables, figures, and quantitative comparisons. Follow the section budget in the task-specific skeleton.

---

## Required Content

1. **Main results**: present the primary experimental outcomes, typically in a table or figure.
2. **Comparison to baselines, where relevant**: support comparative claims with the corresponding baseline results.
3. **Key observations**: highlight the most important findings (do not just dump numbers).
4. **Statistical significance or variance**: report confidence intervals, standard deviations, or number of runs where available.

---

## How to Use the Skeleton

- Use the skeleton's required evidence per section to identify which results must be reported.
- Verify that every contribution has corresponding results here.
- Check for missing information: if results are not yet available, use explicit TODO placeholders.

---

## Previous Sections as Context

- Use the Method section to ensure results correspond to the described approach.
- Use the Experimental Setup to verify that reported metrics, datasets, and baselines match the setup.
- Ensure terminology is consistent with both.

---

## Common Failure Modes

- **Inventing results**: reports specific numbers, improvements, or statistical tests not in the provided evidence. This is a HARD VIOLATION. Fix: use TODO placeholders for unavailable results.
- **Numbers without interpretation**: presents tables of numbers without explaining what they mean. Fix: add at least one sentence of interpretation per major result.
- **Missing required comparisons**: comparative claims lack baseline evidence. Fix: include supported comparison results or weaken the claim.
- **Cherry-picking**: only reports favorable results. Fix: include all results from the planned experiments.
- **Overclaiming**: "our method significantly outperforms all baselines" when improvements are marginal or within noise. Fix: use calibrated language.

---

## What This Section Should NOT Do

- Should not re-describe the method or experimental setup.
- Should not provide deep analysis or explanations of why results look the way they do (that belongs in Analysis/Ablations).
- Should not invent any numbers, metrics, comparisons, or statistical tests.

---

## Section-Specific Self-Check

After drafting, verify:
- Does the Results section fit its task-specific skeleton budget?
- Are all reported numbers from provided evidence (not invented)?
- Are comparisons to baselines included where the claims require them?
- Are results interpreted, not just reported as raw numbers?
- Does every contribution have corresponding results or an explicit TODO?
- Is claim strength calibrated to the evidence?
