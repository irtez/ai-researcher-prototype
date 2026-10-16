# Skill: Experimental Setup Writer

## Purpose

Use this skill when drafting the Experimental Setup (Experiments) section of a full paper.

---

## Role in an Academic Paper

The Experimental Setup section describes how the paper's claims will be tested. It defines datasets, baselines, metrics, and evaluation protocols so that the Results section can be interpreted meaningfully. Follow the section budget in the task-specific skeleton.

---

## Required Content

1. **Research questions or hypotheses**: what specific questions do the experiments answer?
2. **Datasets**: description, statistics, splits, and any preprocessing.
3. **Baselines, when comparison is required**: what methods are compared against and why they were chosen.
4. **Evaluation metrics**: what metrics are used and why they are appropriate.
5. **Implementation details**: software, hardware and settings actually used; include hyperparameters and training procedures only for work involving them.
6. **Reproducibility information**: data provenance and repeatable procedures; seeds, repeated runs and variance when relevant to stochastic methods or claims.

---

## How to Use the Skeleton

- Use the skeleton's required evidence per section to identify which datasets, baselines, and metrics are needed.
- Use the contribution list to ensure every contribution has a corresponding experimental question.
- Check for missing information flagged in the skeleton.

---

## Previous Sections as Context

- Use the Method section to ensure experimental setup evaluates the proposed approach correctly.
- Verify that baselines and datasets are consistent with claims in the Introduction and Related Work.

---

## Common Failure Modes

- **Missing required comparisons**: comparative claims lack comparison methods. Fix: document relevant baselines or weaken the unsupported claim.
- **Vague metrics**: "we use standard metrics" without specifying which ones. Fix: name each metric explicitly.
- **Hallucinated datasets**: refers to datasets not in the provided materials. Fix: use only provided datasets or mark as TODO.
- **Hallucinated hyperparameters**: invents learning rates, batch sizes, or architectures. Fix: use TODOs for unknown values.
- **Missing reproducibility**: the described procedure cannot be repeated. Fix: add the relevant inputs, steps and settings; request seeds or repeats only when needed.
- **Too detailed**: full hyperparameter grids in the main text. Fix: move grids to the appendix and keep key settings here.

---

## What This Section Should NOT Do

- Should not present results (that belongs in Results).
- Should not re-explain the method (that belongs in Method).
- Should not invent any datasets, baselines, metrics, or hyperparameters.

---

## Section-Specific Self-Check

After drafting, verify:
- Does the Experimental Setup fit its task-specific skeleton budget?
- Are research questions or hypotheses explicit?
- Are datasets described with sufficient detail?
- Are baselines listed and justified where required by comparative claims?
- Are metrics named explicitly?
- Are all datasets, baselines, and hyperparameters from provided materials (not invented)?
- Is there at least minimal reproducibility information or a TODO for it?
