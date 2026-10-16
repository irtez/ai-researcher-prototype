# Skill: Section Self-Check

## Purpose

Use this skill after drafting each paper section. The section self-check is an internal Writer quality gate — it is NOT the Reviewer Agent. Its purpose is to catch issues early before they propagate to later sections or to the final paper.

---

## When to Use

- Immediately after completing a section draft during the full-paper writing workflow.
- Before moving on to the next section.

---

## Self-Check Criteria

After drafting a section, verify each of the following:

### Skeleton alignment

- Does the section follow the skeleton plan (key points, page budget, required evidence)?
- Does the section fulfill its planned role in the argument flow?

### Central claim support

- Does the section contribute to the paper's central claim?
- Is the section's relationship to the central claim clear?

### Evidence and claims

- Are all claims in the section supported by available evidence (data, citations, formal arguments, or stated hypotheses)?
- Is any claim too strong for the available evidence?
- Is any citation, result, baseline, metric, dataset, or hyperparameter invented? If so, this is a hard violation — it must be fixed immediately.

### Density and role

- Does the section fit its role and page budget in the task-specific skeleton? Apply venue-specific requirements only if explicitly requested.
- Does the section avoid being a padded summary or a telegraphic outline?

### Obligations and dependencies

- Does the section create obligations for later sections (e.g., promises an ablation, introduces a baseline that must appear in results)?
- Are these obligations recorded in the paper state?

### Repetition

- Does the section repeat too much from previous sections?
- Does the section introduce new content appropriate to its role?

### Completeness

- Is anything required by the skeleton missing from the section?
- Are there gaps that need TODOs or placeholders?

### Consistency

- Does the section use consistent terminology with previous sections?
- Does the section contradict anything stated earlier?

---

## Required Actions After Self-Check

The self-check must NOT be ceremonial. It must result in one of two actions for each identified issue:

### 1. Revise immediately

If the issue can be fixed with available information, revise the section now. Examples:
- A claim is too strong but can be weakened with calibrated language.
- A paragraph repeats content from the Introduction and can be trimmed.
- A term is inconsistent with the Method section and can be renamed.

### 2. Record an explicit TODO or placeholder

If the issue cannot be fixed because information is missing, record it explicitly. Examples:
- `TODO: Add quantitative comparison against baseline methods once experimental results are available.`
- `TODO: Insert Table 1 with results for datasets X, Y, Z.`
- `TODO: Verify citation [Author, Year] exists and is correctly attributed.`

Do NOT silently drop the issue. Do NOT hallucinate missing information.

---

## Consistency Notes

If the self-check reveals that a **previous** section needs changes, do NOT edit the previous section now. Instead, record a consistency note for the global Writer check. Examples:
- "Introduction promises an ablation that is not supported by available evidence."
- "Related Work uses a different term for the proposed method than the Method section."
- "The Results section must verify Contribution 2, but no result is currently available."

---

## Anti-Hallucination Check

This is the most critical check. Verify that the section does NOT invent:
- experimental results
- citations or references
- baselines
- ablations
- datasets
- metrics or quantitative improvements
- statistical tests
- implementation details
- hyperparameters

If any invented content is found, it must be replaced with a TODO, placeholder, weakened claim, or removed entirely.
