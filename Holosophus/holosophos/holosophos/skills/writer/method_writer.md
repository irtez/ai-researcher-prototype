# Skill: Method Writer

## Purpose

Use this skill when drafting the Method section of a full paper.

---

## Role in an Academic Paper

The Method section is the technical core. It describes what the paper proposes in enough detail for an expert reader to understand and mostly reproduce the approach. Follow the section budget in the task-specific skeleton.

---

## Required Content

1. **Problem formulation**: formal or semi-formal definition of the problem being solved (inputs, outputs, constraints).
2. **Proposed approach**: step-by-step description of the method, system, or analysis pipeline.
3. **Design rationale**: why this design was chosen over alternatives (connect to the gap from the Introduction).
4. **Key components**: describe each non-trivial component, module, or step.
5. **Notation and definitions**: introduce notation consistently and define terms before using them.
6. **Algorithms or formal descriptions**: include pseudocode, equations, or system diagrams where they aid understanding.

---

## How to Use the Skeleton

- Use the skeleton's main idea and contribution list to determine what the Method must cover.
- Check the required evidence per section to ensure the method description supports later experimental evaluation.
- Verify that every contribution related to the method is substantiated here.

---

## Previous Sections as Context

- Use the Introduction for problem framing and contribution promises.
- Use a summary of Related Work to avoid repeating background and to position the method against prior approaches.
- Use the same terminology established in the Introduction.

---

## Common Failure Modes

- **Too vague**: describes the method at a high level without enough detail to understand or reproduce it. Fix: add step-by-step details, equations, or pseudocode.
- **Too implementation-heavy**: reads like a code walkthrough instead of a method description. Fix: focus on the method's logic, not implementation details (move those to appendix).
- **Missing rationale**: describes what the method does but not why. Fix: add design rationale connecting to the gap.
- **Notation soup**: introduces too many symbols without clear definitions. Fix: define notation before use and keep it minimal.
- **Overclaiming**: states the method "solves" the problem without evidence. Fix: describe what the method does; defer performance claims to Results.
- **Hallucinated details**: invents architecture sizes, training procedures, or hyperparameters not in the provided materials. Fix: use TODOs for unknown specifics.

---

## What This Section Should NOT Do

- Should not present experimental results.
- Should not duplicate the Related Work's literature coverage.
- Should not make performance claims without pointing to later evidence.
- Should not invent implementation details not in the provided materials.

---

## Section-Specific Self-Check

After drafting, verify:
- Does the Method fit its task-specific skeleton budget?
- Is the problem formally or semi-formally defined?
- Is the proposed approach described step by step?
- Is design rationale provided?
- Are all symbols and terms defined before use?
- Does the method description support the contributions listed in the Introduction?
- Are there any hallucinated implementation details?
