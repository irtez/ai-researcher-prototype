# Structural & Content-Quality Schema for IEEE Access (Computer Science) Papers

A machine-parseable template for an AI paper generator covering four CS paper types: **Empirical/Experimental (E)**, **Systems/Applied (S)**, **Theoretical (T)**, and **Methods/Algorithms (M)**.

---

## [Не уверен, что нужно, это прямо совсем общие правила] 0. IEEE Access House Rules (Apply to All Papers)

These are journal-wide constraints the generator must obey regardless of paper type:

- **Format**: Double-column, single-spaced, official IEEE Access template (Word or LaTeX). Body text ~10 pt; section headings numbered with Roman numerals (I, II, III…); subsections labeled A, B, C…; sub-subsections 1), 2), 3); sub-sub-subsections a), b), c).
- **Recommended total length**: No hard limit, but the journal "strongly recommends" **under 20 pages**; most published CS Access papers run **10–16 typeset two-column pages**. Papers >20 pages require an EIC pre-submission inquiry.
- **First-page footnote (unnumbered)**: receipt/revision/acceptance dates (filled by IEEE), corresponding author, funding statement, conference-version disclosure with DOI (if applicable), human/animal-subjects statement (if applicable). Acknowledgments and funding are **not** placed in the Acknowledgment section; funding goes in this first-page footnote.
- **CC BY 4.0 footer** on first page (template-provided).
- **ORCID** required for submitting author; all authors should have one.
- **Author photographs and biographies** (≈100–150 words each) are **mandatory** for every author and placed *after* the references — this is an Access-specific quirk vs. some other IEEE journals where bios are optional.
- **AI-disclosure**: Any AI-generated text/figures/code must be disclosed in the Acknowledgment section, with the system named and the affected sections identified.
- **Reproducibility**: Strongly encouraged; supports Code Ocean integration and IEEE DataPort. Eligible papers can earn "Code Available" / "Code Reviewed" badges. Including a Data/Code Availability statement (typically a short paragraph at the end of the methodology or as its own labeled item before References) is best practice.
- **Drop cap**: First letter of the Introduction is rendered as a 2-line drop cap with the rest of the first word in small caps (template-handled).
- **Equations**: Numbered consecutively in parentheses, right-aligned; referenced as "(1)" not "Eq. (1)" except at sentence start.
- **Figures/Tables**: Numbered separately and consecutively (Fig. 1, Table 1). Captions below figures, titles above tables. Use SI units. The "Lena image" is banned.
- **References**: IEEE numeric style, square brackets, in citation order. Every reference must be relevant; padding is grounds for review criticism. Typical CS paper has **30–80 references** (empirical/methods often 40–60; theoretical may be lower, 25–45; systems often 30–50).
- **Reviewer evaluation lens** (important for content rubrics below): IEEE Access reviewers explicitly judge **technical soundness, contribution distinctness, comprehensive presentation, and reference adequacy** — *not* primarily novelty/impact. Papers must demonstrate a defensible advance over prior art but do not need to be "breakthrough" level. Reviews are binary (accept/reject); reviewers expect strong baselines, ablations, and reproducibility detail.

---

## 1. Section-by-Section Schema

For each section the schema gives: **(a)** name, **(b)** position, **(c)** length in two-column pages, **(d)** purpose, **(e)** sub-structure, **(f)** quality rubric, **(g)** pitfalls, **(h)** variation across E / S / T / M paper types.

---

### Section 1 — Title

- **(a) Standard name(s)**: Title (no label printed). IEEE Access preference: specific, descriptive, ≤12–15 words; capitalization in title case; avoid "novel," "new," "a study of," "research on"; no math/special symbols; no "(Invited)".
- **(b) Position**: Top of page 1, centered, 24 pt.
- **(c) Length**: 1–3 lines (≈one-third of a column-width line block).
- **(d) Purpose**: Communicate the contribution and scope so a reader can decide relevance in one glance.
- **(e) Sub-structure**: Optional two-part form using a colon ("Specific contribution: domain/dataset/system").
- **(f) Quality rubric**: Strong titles name the technique AND the problem/domain (e.g., "Sparse-Attention Transformer for Real-Time Network Intrusion Detection on Edge Devices"); include a noun phrase identifying the method and an application context.
- **(g) Pitfalls**: Vague generic titles; over-promising ("optimal", "best"); jargon/acronyms not yet established; embedding equations.
- **(h) Variation by paper type**:
  - E: emphasize the empirical claim or comparative study ("Benchmarking… on…").
  - S: name the system and its capability ("X: A Framework for…").
  - T: name the algorithm/result and complexity class ("An O(n log n) Algorithm for…").
  - M: name the method and the problem it solves ("Contrastive Self-Supervised Method for…").

---

### Section 2 — Author Block

Оставим на ручную работу)

---

### Section 3 — Abstract

- **(a) Standard name(s)**: ABSTRACT (rendered in bold small caps in the template). Single unstructured paragraph (no subheadings).
- **(b) Position**: Immediately below author block, page 1, left column.
- **(c) Length**: ≈ **150–250 words** ≈ **0.15–0.25 page** (roughly half of column-1 height on page 1).
- **(d) Purpose**: A self-contained microcosm of the paper: context → gap → contribution → method → key result → implication (Good approach is STAR based abstract).
- **(e) Sub-structure** (recommended sentence pattern, 5–8 sentences): (1) Context/problem, (2) Gap or motivation, (3) Proposed contribution, (4) Method/approach in one sentence, (5–6) Headline quantitative results, (7) Implication/significance.
- **(f) Quality rubric**: Self-contained; no abbreviations (or define on first use); no citations, footnotes, or equations; quantifies the headline result; uses standardized terms found in the IEEE Thesaurus.
- **(g) Pitfalls**: Generic motivation ("AI is important"); vague results ("our method shows good performance"); undefined acronyms; spilling into >250 words; promising findings not actually shown in the paper.
- **(h) Variation**:
  - E: emphasize datasets, baselines compared, headline metric improvement (absolute and relative).
  - S: emphasize what the system *does* and at what scale/throughput/latency it operates.
  - T: state the formal problem, the new bound or guarantee, and conditions; can mention proof technique if novel.
  - M: emphasize the methodological innovation, why it works, and headline empirical validation.

---

### Section 4 — Index Terms (Keywords) - только контекст перегружать

### Section 5 — Introduction

- **(a) Standard name(s)**: I. INTRODUCTION.
- **(b) Position**: First numbered section.
- **(c) Length**: **1–2 pages** (commonly 1.25–1.75 pages).
- **(d) Purpose**: Establish problem importance, expose the research gap, state the contribution, and outline the paper.
- **(e) Sub-structure** (recommended 5-block pattern):
  1. **Hook & context** (1–2 paragraphs): Why the problem matters, application stakes.
  2. **State of the art / gap** (1–2 paragraphs): Brief positioning vs. closest prior work; what is *missing*.
  3. **Proposed approach (overview)** (1 paragraph): One-paragraph high-level summary, optionally referencing a "system overview" figure (Fig. 1).
  4. **Bullet/enumerated list of contributions** (3–5 items): Crisp, claim-by-claim — IEEE Access reviewers explicitly look for this. Each contribution should be falsifiable/verifiable.
  5. **Paper organization** (1 short paragraph): "The remainder of this article is organized as follows. Section II reviews… Section III presents… Section IV…"
- **(f) Quality rubric**: Reviewers want the gap stated *explicitly*, not implied; contributions stated *quantitatively where possible*; introduction terminates with a clear roadmap. Strong intros include 8–25 well-chosen citations to anchor the gap.
- **(g) Pitfalls**: Long history lessons; promising results not delivered later; overuse of "to the best of our knowledge…"; vague contributions ("we improve performance"); skipping the contribution list; writing what is essentially an abstract again.
- **(h) Variation by paper type**:
  - E: Frame as a comparative/measurement question; gap = no fair benchmark / missing condition; contributions list datasets, baselines, metrics, and findings.
  - S: Frame around real-world deployment constraints; gap = no system meets requirements X/Y/Z simultaneously; contributions list architectural innovations and validated deployment.
  - T: Frame around formal open problem; gap = no algorithm achieves bound B under assumption A; contributions list theorem(s), bound improvements, proof technique, and corollaries.
  - M: Frame around a methodological limitation in existing methods; contributions list the new method, its design rationale, theoretical properties (if any), and empirical validation.

---

### Section 6 — Related Work

- **(a) Standard name(s)**: II. RELATED WORK
- **(b) Position**: Section II.
- **(c) Length**: **1–2.5 pages** (commonly 1.5–2 pages; theoretical papers often shorter).
- **(d) Purpose**: Survey relevant prior work, taxonomize approaches, position the paper, and justify the gap.
- **(e) Sub-structure**: 2–4 thematic subsections (A, B, C…) grouped by approach family or sub-problem. End the section with a 1-paragraph synthesis: "In summary, prior work either … or …; none addresses …, which motivates our approach."
- **(f) Quality rubric**: Coverage of the **strongest 2–3 most recent comparators** is mandatory — omitting them is a top reviewer criticism. References should be primarily ≤5 years old (with classic foundations cited where appropriate). Each cited work should be characterized briefly (1–2 sentences) — not just listed. Comparison Table I (techniques × properties) is highly valued by reviewers. At least 25 citations are required for the full paper.
- **(g) Pitfalls**: Reading like an annotated bibliography; missing recent SOTA; excessive self-citation; cherry-picking weaker comparators; treating the section as a tutorial; not connecting back to the gap.
- **(h) Variation**:
  - E: Emphasize prior empirical studies, datasets used, and metrics reported; comparison table of *experimental setups*.
  - S: Emphasize prior systems/architectures with comparison table on capabilities, scale, and deployment context.
  - T: Emphasize prior bounds, models, and proof techniques; often a comparison table of complexity results.
  - M: Emphasize prior method families; comparison table on assumptions, complexity, supervision regime, and reported performance.

---

### Section 7 — Preliminaries / Problem Formulation (optional but common)

- **(a) Standard name(s)**: III. PRELIMINARIES; alternatives: "Problem Formulation", "Background", "Notation and Problem Definition", "System Model".
- **(b) Position**: Section III (after Related Work). Often **omitted in S/E** papers and **mandatory in T/M** papers.
- **(c) Length**: **0.5–2 pages**. Short for E (often 0.5 page or merged into Methodology); 1–2 pages for T/M.
- **(d) Purpose**: Establish notation, definitions, formal problem statement, assumptions, and threat/system model.
- **(e) Sub-structure**: A. Notation and Definitions; B. Problem Statement (formal); C. Assumptions / System Model / Threat Model (where applicable).
- **(f) Quality rubric**: Notation table appreciated when many symbols are introduced. Formal problem definition uses a numbered "Definition" or "Problem" environment. Assumptions are listed and justified.
- **(g) Pitfalls**: Notation inconsistency with later sections; ambiguous problem statement; hidden assumptions revealed only later in proofs; over-formalization in papers that don't need it (E/S).
- **(h) Variation**:
  - E: Often skipped or one paragraph defining the task and metrics.
  - S: Becomes "System Model" / "Requirements" — describes deployment context, constraints, and threat model.
  - T: **Mandatory**, longest version; rigorous notation, formal problem, assumptions, complexity model.
  - M: **Recommended**; formal task definition (e.g., learning objective), input/output spaces, loss formulation.

---

### Section 8 — Proposed Method / System / Approach (Core Contribution)

- **(a) Standard name(s)**: IV. PROPOSED METHOD; alternatives: "Methodology", "Proposed Approach", "System Design", "System Architecture", "The X Framework", "Algorithm Description". Often named after the contribution (e.g., "IV. SparseGNN Architecture").
- **(b) Position**: Section III or IV — the **central section** of the paper.
- **(c) Length**: **2–5 pages** (the longest section; commonly 3–4 pages). Theoretical papers may stretch to 4–6 pages.
- **(d) Purpose**: Describe what the authors built/proposed in enough detail to be reproducible.
- **(e) Sub-structure** (varies; typical templates below):
  - **E template (rare, since E papers usually evaluate existing methods)**: A. Methods Compared; B. Evaluation Pipeline.
  - **S template**: A. Overview/Architecture (with system diagram, Fig. 2); B. Component 1; C. Component 2; …; D. Implementation Details.
  - **T template**: A. High-Level Idea; B. Algorithm (pseudocode block); C. Correctness; D. Complexity Analysis. (Or proofs may be moved to a separate Section V.)
  - **M template**: A. Overview (with model diagram); B. Module/Component descriptions; C. Training/Inference procedure; D. Algorithm pseudocode; E. Complexity / theoretical properties.
- **(f) Quality rubric**: Includes (i) a system/model overview figure; (ii) precise algorithm pseudocode in an `Algorithm` float; (iii) explicit design rationale ("we choose X because…"); (iv) every variable defined; (v) hyperparameters and architectural choices justified, not just stated. Reviewers reward design rationale paragraphs that explain *why* over *what*.
- **(g) Pitfalls**: Pseudocode that doesn't match the prose; missing edge cases; unjustified arbitrary choices ("we use 3 layers" with no rationale); equations introduced without naming variables; black-box prose without diagrams; describing implementation trivia at the expense of conceptual structure.
- **(h) Variation**:
  - E: This section is often very brief or replaced by an "Experimental Methodology" section because the method under study is from prior work.
  - S: Heavy emphasis on architecture diagrams, data flow, deployment considerations, technology stack, and engineering trade-offs.
  - T: Centers on the algorithm + correctness + complexity; pseudocode is mandatory; proofs may be inline or deferred to a later section.
  - M: Centers on the model/method, with diagram, equations of the loss/update, training procedure, and discussion of inductive biases.

---

### Section 9 — Theoretical Analysis (conditional) - это скипаем, так как я думаю пока мы полностью скипаем T тип статьи

- **(a) Standard name(s)**: V. THEORETICAL ANALYSIS; alternatives: "Analysis", "Convergence Analysis", "Complexity Analysis", "Correctness and Soundness", "Privacy/Security Analysis."
- **(b) Position**: Immediately after the proposed method, before experimental evaluation. **Always present in T**, **often present in M**, **sometimes in S** (e.g., security/scalability arguments), **rarely in E**.
- **(c) Length**: **0.5–3 pages**. T papers: 1.5–3+ pages. M: 0.5–1.5 pages. S: 0.5–1 page (if at all).
- **(d) Purpose**: Provide formal guarantees, complexity bounds, convergence proofs, security/privacy analysis, or scalability arguments.
- **(e) Sub-structure**: Numbered Theorem / Lemma / Proposition / Corollary blocks, each with a Proof block (or proof sketch with full proof in Appendix). Subsections by property analyzed (e.g., A. Correctness, B. Complexity, C. Convergence).
- **(f) Quality rubric**: Theorems are stated precisely with all assumptions; proofs are either complete or have a clear sketch + appendix pointer; complexity claims state model (RAM, PAC, expected vs. worst-case); empirical evaluation later validates the predicted scaling.
- **(g) Pitfalls**: Hand-wavy proofs; assumptions buried inside the proof rather than the theorem statement; complexity stated without specifying the cost model; theorems that don't actually inform the contribution.
- **(h) Variation**:
  - E: Usually absent.
  - S: Optional — used for security/privacy guarantees, throughput models, fault tolerance arguments.
  - T: Mandatory and the heart of the paper; expect ≥1 main theorem with full proof.
  - M: Recommended for ML methods (e.g., convergence rate, generalization bound, sample complexity); often shorter and more applied than in T papers.

---

### Section 10 — Experimental Setup

- **(a) Standard name(s)**: V. or VI. EXPERIMENTAL SETUP; alternatives: "Experiments", "Evaluation Methodology", "Datasets and Setup", "Implementation".
- **(b) Position**: Before Results. Sometimes merged with Results into a single "VI. Experiments and Results" section; in IEEE Access CS papers, the *split* version is more common in longer (>12 page) papers.
- **(c) Length**: **0.75–1.5 pages**.
- **(d) Purpose**: Describe datasets, baselines, metrics, hardware, hyperparameters, and protocol with enough detail that another lab could reproduce the study.
- **(e) Sub-structure**: A. Datasets (sources, sizes, splits, preprocessing); B. Baselines / Comparators (with citations and configurations); C. Evaluation Metrics (formally defined); D. Implementation Details (framework, GPUs/CPUs, hyperparameters, training time); E. Research Questions (RQ1, RQ2…) — recommended for E papers.
- **(f) Quality rubric**: Reviewers explicitly check for **competitive recent baselines (≥2–3 SOTA)**, fair hyperparameter tuning for baselines, multiple seeds / cross-validation, and statistical significance reporting. Hardware specs and wall-clock times are appreciated. Cite each dataset and each baseline.
- **(g) Pitfalls**: Cherry-picked weak baselines; mismatched datasets between method and baselines; reporting only on a single seed; missing preprocessing details; opaque hyperparameter selection.
- **(h) Variation**:
  - E: This is the **largest setup section** of any paper type — datasets, study design, participant recruitment (for user studies), and statistical methodology must be exhaustive.
  - S: Includes deployment environment, workload characterization, and measurement methodology (latency, throughput, energy).
  - T: Often minimal or absent; if present, describes synthetic instances and scale parameters used to validate complexity claims.
  - M: Substantial; emphasis on training protocol, ablation conditions, and reproducibility detail.

---

### Section 11 — Results / Evaluation

- **(a) Standard name(s)**: VI. RESULTS or VI. EVALUATION; alternatives: "Experimental Results", "Empirical Evaluation", "Performance Evaluation".
- **(b) Position**: After Experimental Setup.
- **(c) Length**: **2–4 pages** (commonly 2.5–3.5). With many figures/tables, can reach 4+ pages.
- **(d) Purpose**: Report and interpret results, organized by research question.
- **(e) Sub-structure**: One subsection per RQ or per evaluation dimension, e.g., A. Main Comparison; B. Ablation Study; C. Scalability / Efficiency; D. Sensitivity / Hyperparameter Analysis; E. Qualitative Examples / Case Study; F. User Study (E papers); G. Failure Analysis.
- **(f) Quality rubric**: Reviewers expect (i) headline comparison table covering all baselines on all datasets/metrics; (ii) **at least one ablation study** isolating the contribution; (iii) variance/error bars or significance tests; (iv) qualitative analysis (examples, attention maps, traces); (v) discussion *in-place* of unexpected results; (vi) figures/tables referenced and interpreted in the prose, not dumped. Tables should highlight the best result per column.
- **(g) Pitfalls**: Reporting numbers without interpretation; missing ablations (a top reviewer criticism); no statistical testing; cherry-picked metrics; figures without legends/axes labels; tables without highlighting; ignoring cases where the proposed method loses.
- **(h) Variation**:
  - E: The dominant section of the paper (often 4–6 pages); may include human-subjects results with qualitative coding, inter-rater reliability, and statistical tests; threats to validity discussion typically separated into its own section.
  - S: Includes throughput/latency/scalability plots, deployment-scale experiments, real-world workload traces, possibly a deployment case study.
  - T: Optional empirical validation of asymptotic claims; usually 0.5–1.5 pages and clearly framed as "empirical validation of theoretical results".
  - M: Standard ML evaluation; ablation and sensitivity studies are mandatory; benchmark tables central.

---

### Section 12 — Discussion

- **(a) Standard name(s)**: VII. DISCUSSION; sometimes folded into Results or Conclusion. In IEEE Access CS papers, an explicit Discussion section is common but not mandatory.
- **(b) Position**: After Results, before Limitations or Conclusion.
- **(c) Length**: **0.5–1.5 pages** (when present).
- **(d) Purpose**: Step back from individual results to interpret what they mean, why the method works, and how findings generalize.
- **(e) Sub-structure**: A. Interpretation of Findings; B. Implications for Practice / Theory; C. Comparison to Prior Work (revisited in light of results); D. Generalizability.
- **(f) Quality rubric**: Reviewers reward causal/mechanistic explanations of *why* the method outperforms baselines (e.g., via probing, ablation reasoning, or theoretical argument), and honest engagement with cases where it doesn't.
- **(g) Pitfalls**: Recapping results without adding interpretive value; overclaiming generalization; ignoring negative findings; speculation unsupported by data.
- **(h) Variation**:
  - E: Strong emphasis on what the empirical patterns tell us about the underlying phenomenon.
  - S: Discusses deployment lessons, engineering trade-offs, and real-world implications.
  - T: Discusses tightness of bounds, gap to lower bounds, and practical relevance.
  - M: Discusses inductive biases, where the method is expected to fail, and connections to other model families.

---

### Section 13 — Limitations / Threats to Validity

- **(a) Standard name(s)**: VIII. LIMITATIONS; alternatives: "Threats to Validity" (common in empirical/SE papers), "Discussion of Limitations". Often a subsection of Discussion or Conclusion rather than a standalone section.
- **(b) Position**: After Discussion or merged into Conclusion.
- **(c) Length**: **0.25–0.75 page** (typically 1–3 paragraphs or one half-column).
- **(d) Purpose**: Honestly disclose scope, assumptions, and threats; build reviewer trust.
- **(e) Sub-structure** (for E papers): Internal validity, External validity, Construct validity, Conclusion/Statistical validity. For other types, list 3–6 limitations as bullets or paragraphs.
- **(f) Quality rubric**: Reviewers respond well to a frank, specific limitations paragraph; it actively *helps* acceptance because it shows scientific maturity. Each limitation should be paired with a mitigation or a future-work pointer.
- **(g) Pitfalls**: Skipping the section entirely; only listing trivial limitations; framing limitations as strengths.
- **(h) Variation**:
  - E: Mandatory and most elaborate; uses formal validity taxonomy.
  - S: Lists deployment-scope limits (workloads tested, scale tested, hardware classes).
  - T: Limits on assumptions, model of computation, and tightness of bounds.
  - M: Limits on data regimes, supervision requirements, computational cost, and failure modes.

---

### Section 14 — Conclusion (and Future Work)

- **(a) Standard name(s)**: IX. CONCLUSION; alternatives: "Conclusion and Future Work". (IEEE notes a Conclusion section is "not required" but in practice nearly all CS Access papers include one.)
- **(b) Position**: Last numbered section before Acknowledgments.
- **(c) Length**: **0.25–0.5 page** (typically half a column to one column).
- **(d) Purpose**: Summarize contributions, headline results, and outline future directions.
- **(e) Sub-structure**: One paragraph summarizing the paper; one paragraph with key quantitative findings; one paragraph on future work.
- **(f) Quality rubric**: Restates contributions in terms tied to results actually delivered; future-work paragraph is concrete (specific extensions, applications, datasets) rather than vague.
- **(g) Pitfalls**: Repeating the introduction verbatim; introducing new results or claims; vague future work ("we plan to extend in many directions"); inflating findings.
- **(h) Variation**: Largely uniform, but T papers should restate the main theorem and identify open problems; S papers should mention deployment status and intended releases; E papers should reiterate the evidence and study scope.

---

### Section 17 — References

- **(a) Standard name(s)**: REFERENCES (unnumbered heading).
- **(b) Position**: After Acknowledgment.
- **(c) Length**: **1–3 pages** (typical CS Access paper has 30–80 references; 1.5–2 pages is most common).
- **(d) Purpose**: Cite all prior work that supports the paper.
- **(e) Sub-structure**: Single numbered list, in citation order, IEEE format.
- **(f) Quality rubric**: All references are relevant; recent SOTA included; no retracted papers; complete bibliographic data; consistent abbreviation of conference names; DOIs included where available.
- **(g) Pitfalls**: Citation padding; missing recent comparators; uneven formatting; preprint-only versions when peer-reviewed versions exist; broken DOIs/URLs; including the Lena image references.
- **(h) Variation**: Theory papers tend to cite fewer (25–45) and older foundational work; empirical/methods/systems papers tend toward 40–70 with strong recency bias.

---

### Section 18 — Author Biographies (and Photos)

- **(a) Standard name(s)**: Biographies, with photo. **Mandatory in IEEE Access for every author** (unlike many other IEEE journals where they are optional).
- **(b) Position**: **After References** (this is the IEEE Access convention; some other IEEE pubs place them before).
- **(c) Length**: ~**0.25–0.4 page per author** (1×1.25-inch photo + 100–150 words).
- **(d) Purpose**: Author background, IEEE membership grade, research interests.
- **(e) Sub-structure** (per author): photo (300+ dpi, head-and-shoulders); name in bold (with IEEE membership grade); degrees (with year and institution); past and current positions; current affiliation; research interests; awards / professional society activities; email (optional).
- **(f) Quality rubric**: 100–150 words; first-person discouraged; ends with research interests sentence ("His research interests include…").
- **(g) Pitfalls**: Missing for some authors (instant desk-reject risk); excessive length; non-professional photos; missing IEEE grade if applicable.
- **(h) Variation**: None across paper types.

---

## 2. Section Order — Canonical Templates per Paper Type

The numbered Roman ordering depends on which optional sections are present. Below are the canonical orderings; an AI generator should select one based on paper type.

**Empirical / Experimental (E)**:
I. Introduction → II. Related Work → III. Methods Under Study (or Experimental Methodology) → IV. Experimental Setup → V. Results → VI. Discussion → VII. Threats to Validity → VIII. Conclusion → Acknowledgment → References → Biographies. *(Theoretical Analysis section omitted.)*

**Systems / Applied (S)**:
I. Introduction → II. Related Work → III. System Model / Requirements → IV. System Design / Architecture → V. Evaluation → VI. Discussion → VII. Limitations → VIII. Conclusion → Acknowledgment → References → Biographies.

**Theoretical (T)** Скип:
I. Introduction → II. Related Work → III. Preliminaries / Problem Formulation → IV. Proposed Algorithm → V. Theoretical Analysis (Correctness, Complexity, Lower Bounds) → VI. Empirical Validation (optional, brief) → VII. Discussion → VIII. Conclusion → Appendix (full proofs) → Acknowledgment → References → Biographies.

**Methods / Algorithms (M)** (e.g., new ML model):
I. Introduction → II. Related Work → III. Preliminaries / Problem Formulation → IV. Proposed Method → V. Theoretical Properties (optional) → VI. Experimental Setup → VII. Results → VIII. Discussion → IX. Limitations → X. Conclusion → Acknowledgment → References → Biographies.

---

## 3. Summary Table — Section Order, Length, and Applicability

| # | Section | Position | Length (2-col pages) | E | S | T | M |
|---|---|---|---|---|---|---|---|
| 1 | Title | p.1 top | 1–3 lines | ✅ | ✅ | ✅ | ✅ |
| 2 | Authors / Affiliations | p.1 | ~0.1–0.2 | ✅ | ✅ | ✅ | ✅ |
| 3 | Abstract (≤250 words, 1 paragraph) | p.1 | 0.15–0.25 | ✅ | ✅ | ✅ | ✅ |
| 4 | Index Terms (3–10) | p.1 | 1–3 lines | ✅ | ✅ | ✅ | ✅ |
| 5 | Introduction | I | 1–2 | ✅ | ✅ | ✅ | ✅ |
| 6 | Related Work / Background | II | 1–2.5 | ✅ | ✅ | ✅ | ✅ |
| 7 | Preliminaries / Problem Formulation / System Model | III (often) | 0.5–2 | △ (small) | ✅ (as System Model) | ✅ (mandatory) | ✅ |
| 8 | Proposed Method / System / Approach | III–IV | 2–5 | △ (replaced by Methods Under Study) | ✅ (architecture-heavy) | ✅ (algorithm + correctness) | ✅ (model-heavy) |
| 9 | Theoretical Analysis | V (conditional) | 0.5–3 | ✗ | △ (security/scalability) | ✅ (mandatory, 1.5–3+) | △ (0.5–1.5) |
| 10 | Experimental Setup | V–VI | 0.75–1.5 | ✅ (largest) | ✅ | △ (small) | ✅ |
| 11 | Results / Evaluation | VI–VII | 2–4 | ✅ (largest) | ✅ | △ (0.5–1.5) | ✅ |
| 12 | Discussion | VII–VIII | 0.5–1.5 | ✅ | ✅ | ✅ | ✅ |
| 13 | Limitations / Threats to Validity | VIII–IX | 0.25–0.75 | ✅ (formal taxonomy) | ✅ | △ | ✅ |
| 14 | Conclusion (& Future Work) | last numbered | 0.25–0.5 | ✅ | ✅ | ✅ | ✅ |
| 15 | Appendix / Appendices | post-Conclusion | 0–4+ | △ | △ | ✅ (proofs) | △ |
| 16 | Acknowledgment (incl. AI disclosure) | unnumbered | 1–6 lines | ✅ | ✅ | ✅ | ✅ |
| 17 | References (30–80) | unnumbered | 1–3 | ✅ | ✅ | ✅ (often fewer) | ✅ |
| 18 | Author Biographies + Photos | after References | ~0.25–0.4 / author | ✅ | ✅ | ✅ | ✅ |

Legend: ✅ = standard / required, △ = sometimes present / variable, ✗ = typically absent.

**Approximate total page budgets**:
- E paper: ~10–14 pages (heavy on Setup + Results + Threats).
- S paper: ~12–16 pages (heavy on Architecture + Implementation + Evaluation).
- T paper: ~10–14 pages (heavy on Preliminaries + Algorithm + Analysis; large appendix common).
- M paper: ~12–16 pages (balanced across Method + Setup + Results + Discussion).

---

## 4. Cross-Cutting Quality Signals an AI Generator Should Optimize

These are the strongest predictors of acceptance based on IEEE Access reviewer guidelines and editorial commentary:

1. **Explicit gap statement and contribution list** in the Introduction — top reviewer expectation.
2. **Strong, recent baselines (2–3 SOTA from the past 1–3 years)** in Related Work and Results.
3. **At least one ablation study** in Results that isolates the contribution.
4. **Reproducibility package**: hyperparameters table, hardware specs, data/code availability statement, and (ideally) a Code Ocean / GitHub link with DOI.
5. **System/architecture/model overview figure** as Fig. 1 or Fig. 2.
6. **Algorithm pseudocode** in a numbered Algorithm float for any non-trivial procedure.
7. **Quantified abstract** with headline numbers, not adjectives.
8. **Honest Limitations / Threats to Validity** section.
9. **Tight equation and notation hygiene** (numbered equations, defined variables, consistent symbols).
10. **Author photos and biographies for every author** below References (Access-specific gating requirement).
11. **Page count ≤ 16** (target 10–16) unless EIC pre-approval was obtained.

---

## 5. Common Cross-Section Pitfalls to Avoid (Generator Checklist)

- Padded conference paper: a 6-page conference paper with experiments stapled on. The journal version must add ≥30–40% genuinely new content (typically additional datasets, ablations, theoretical analysis, or system extensions) and reference the conference version with DOI in the first-page footnote.
- Benchmark tables without analysis ("results dump").
- Related Work section that is actually a survey, dwarfing the contribution.
- Mismatched datasets between baselines and proposed method.
- Reporting only mean scores without variance, multiple seeds, or significance.
- Acronyms defined in the abstract but not redefined in the body.
- Funding placed in Acknowledgment instead of first-page footnote.
- Missing biographies for one or more authors.
- Overlong paper (>20 pages) without EIC pre-submission inquiry.