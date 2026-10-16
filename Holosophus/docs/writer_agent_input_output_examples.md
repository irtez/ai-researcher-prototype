# Writer Agent Reference

This document is a source-grounded reference for the `writer` agent in the Holosophos scientific paper generation pipeline.

It focuses on:

- what the Writer receives,
- what it is expected to produce,
- how its prompts constrain behavior,
- which tools it can call,
- what those tools appear to accept and return,
- how session-scoped files are laid out,
- what good upstream inputs look like.

## Scope and Sources

This note is based on the repository code and prompts, especially:

- [`holosophos/holosophos/agents/writer.py`](../holosophos/holosophos/agents/writer.py)
- [`holosophos/holosophos/prompts/writer.yaml`](../holosophos/holosophos/prompts/writer.yaml)
- [`holosophos/holosophos/prompts/system.yaml`](../holosophos/holosophos/prompts/system.yaml)
- [`holosophos/holosophos/settings.py`](../holosophos/holosophos/settings.py)
- [`holosophos/holosophos/main_agent.py`](../holosophos/holosophos/main_agent.py)
- [`holosophos/holosophos/langfuse_openai_patch.py`](../holosophos/holosophos/langfuse_openai_patch.py)
- [`codearkt/codearkt/codeact.py`](../codearkt/codearkt/codeact.py)
- [`codearkt/codearkt/server.py`](../codearkt/codearkt/server.py)
- [`codearkt/container/tools.py`](../codearkt/container/tools.py)
- [`mle_kit_mcp/mle_kit_mcp/tools/bash.py`](../mle_kit_mcp/mle_kit_mcp/tools/bash.py)
- [`mle_kit_mcp/mle_kit_mcp/tools/text_editor.py`](../mle_kit_mcp/mle_kit_mcp/tools/text_editor.py)
- [`README.md`](../README.md)
- [`docs/implementation_plan_feature_paperbanana.md`](./implementation_plan_feature_paperbanana.md)

Important limitation:

- Some Writer-visible tools are referenced in this repo but their concrete implementation is not vendored here, especially the LaTeX-template tools and PaperBanana visualization tools.
- For those tools, this document distinguishes between behavior that is exact from source code and behavior that is inferred from prompts, instrumentation, and surrounding docs.

## Where Writer Fits in the Pipeline

The manager prompt in [`system.yaml`](../holosophos/holosophos/prompts/system.yaml) recommends this order for full paper creation:

Source:

- [`holosophos/holosophos/prompts/system.yaml`](../holosophos/holosophos/prompts/system.yaml)

1. `librarian`
2. `proposer`
3. `librarian`
4. `mle_solver`
5. `writer`
6. `reviewer`

So the Writer is downstream of literature search and experiments. Its role is to transform already-collected evidence into a complete paper package. It is not the place where missing experiments should be invented.

## Writer Identity and Responsibility

The Writer agent is defined in [`writer.py`](../holosophos/holosophos/agents/writer.py) as a technical writer that can create PDF papers and reports. Its description says it is the specialist for:

Source:

- [`holosophos/holosophos/agents/writer.py`](../holosophos/holosophos/agents/writer.py)
- [`README.md`](../README.md)
- [`holosophos/holosophos/settings.py`](../holosophos/holosophos/settings.py)

- writing technical texts,
- including mathematical formulas,
- formatting technical documents,
- creating reports with plots and tables.

The repo [`README.md`](../README.md) also makes a routing decision explicit:

- PaperBanana tools are mounted globally at MCP level,
- but in practice they are routed to the `writer` through [`WRITER_TOOLS`](../holosophos/holosophos/settings.py).

## Writer Prompt Contract

The Writer system prompt in [`writer.yaml`](../holosophos/holosophos/prompts/writer.yaml) is the strongest source of truth for expected behavior.

Source:

- [`holosophos/holosophos/prompts/writer.yaml`](../holosophos/holosophos/prompts/writer.yaml)

### Core identity

The Writer is told to behave like:

- an expert AI scientist,
- a writer of high-quality academic papers for A* conferences and Q1 journals,
- someone who writes clearly, structurally, and with evidence-backed claims.

### Required outputs

The Writer prompt explicitly says it must produce:

- `paper.tex`
- `references.bib`
- `paper.pdf`

This is stronger than a normal chat response. The durable artifact set is part
of the contract, and each session updates these canonical files in place.

### Required process

The Writer is instructed to:

- do everything step by step,
- wait for tool observations before continuing,
- always use an existing LaTeX template,
- read all provided files in full,
- compile with `compile_latex`,
- fix compilation errors and retry if compilation fails.

### Required paper structure

The Writer prompt requires the paper to include all of:

- Abstract
- Introduction
- Related Work
- Method
- Experiments
- Results
- Limitations
- Ethics/Broader Impact
- References

The prompt follows the requested scope and length, with no universal minimum page count.
Acknowledgments, References, Bibliography, and Appendix material are excluded;
a standalone Discussion section counts.

### Escalation rule

The Writer prompt says:

- if some information or experiment results are missing, ask the manager for more information.

That matters operationally: if the manager gives the Writer only a vague request, the Writer is prompt-constrained to depend on more context.

## How Writer Is Invoked

There are three useful levels of interface to understand.

Source:

- [`codearkt/codearkt/tools.py`](../codearkt/codearkt/tools.py)
- [`codearkt/container/tools.py`](../codearkt/container/tools.py)
- [`codearkt/codearkt/server.py`](../codearkt/codearkt/server.py)

### Level 1: Manager-to-Writer call inside the agent loop

The manager sees the Writer as a managed-agent tool named `agent__writer`.

The tool schema that CodeArkt exposes for managed agents is effectively defined through [`codearkt/codearkt/tools.py`](../codearkt/codearkt/tools.py) and the runtime wrapper in [`codearkt/container/tools.py`](../codearkt/container/tools.py):

```json
{
  "type": "object",
  "properties": {
    "query": {
      "type": "string"
    }
  }
}
```

In practice, the manager uses it like this:

```python
agent__writer("""
... very detailed, self-contained task description ...
""")
```

Important implications:

- The Writer receives a single long string from the manager.
- The manager prompt says managed agents are stateless.
- Therefore the string must contain the full task context again, even if the manager already learned it in earlier steps.

### Level 2: HTTP endpoint for the Writer agent

Under the hood, each agent is exposed as an endpoint in [`codearkt/codearkt/server.py`](../codearkt/codearkt/server.py). The request object is:

```json
{
  "messages": [
    {
      "role": "user",
      "content": "Write the paper from the completed experiments."
    }
  ],
  "session_id": "research__topic__model__001",
  "stream": false
}
```

Field meanings:

- `messages`: the chat history passed into the agent.
- `session_id`: the run identifier used for workspace isolation and tracing.
- `stream`: if `true`, the server returns streamed events; if `false`, it waits for the final result.

### Level 3: Final returned value

When `stream=false`, the Writer endpoint returns the final answer from the Writer agent. That final answer is usually just a string, not a rich object.

Example:

```json
"The paper is complete and saved as \"paper.pdf\"."
```

The files the Writer produced are usually more important than that returned string.

## What the Manager Must Put into the Writer Prompt

Because the Writer is stateless, the manager prompt should be treated as a complete handoff package.

Source:

- [`holosophos/holosophos/prompts/system.yaml`](../holosophos/holosophos/prompts/system.yaml)
- [`./manager_to_writer_handoff.md`](./manager_to_writer_handoff.md)

### Minimum useful contents

A good Writer input usually includes:

- the original user request,
- a short summary of what the paper claims,
- the method name,
- the experimental setting,
- the main metrics,
- the key ablations,
- the list of important cited papers and IDs,
- limitations that should be stated honestly,
- output filenames,
- any figure requirements,
- any venue or page-limit constraints.

### Recommended handoff structure

A reliable manager-to-Writer message usually includes these blocks:

1. Original task
2. Scope and hard constraints
3. Evidence from prior agents
4. File locations available in the workdir
5. Specific instructions for paper assembly
6. Requested final filenames

### Bad input

```python
agent__writer("Write the paper.")
```

Why this is bad:

- no evidence,
- no metrics,
- no citations,
- no filenames,
- no figure request,
- no limitations,
- no indication of what upstream phases have already produced.

### Good input

```python
agent__writer("""
The user asked us to prepare a scientific paper on our completed project.
This is the writing phase after literature review and experiments are finished.
You must only use the facts in this prompt and in the provided workdir files.
Do not invent experiment results.

Project summary:
- Method name: Retrieval Consistency Training
- Domain: robust passage retrieval under noisy query reformulations
- Main claim: consistency training across paraphrases improves ranking quality

Experiment setting:
- Dataset: MS MARCO passage benchmark
- Baseline nDCG@10: 0.384
- Proposed method nDCG@10: 0.417
- Baseline Recall@50: 0.812
- Proposed method Recall@50: 0.846
- Ablation without consistency loss nDCG@10: 0.401

Relevant citations:
- Dense Passage Retrieval
- Contriever
- Query augmentation methods
- Include exact titles and IDs where available from upstream notes

Available files in the workdir:
- experiment_summary.md
- results/main_metrics.json
- results/ablation_metrics.json
- refs/citation_notes.md

Paper requirements:
- Use an existing LaTeX template
- Produce paper.tex, references.bib, and paper.pdf
- Include all mandatory sections
- Match the requested length and scope using supported evidence
- Include one methodology diagram and one performance plot if useful
- Discuss limitations honestly
- Include broader impact and ethics

Return a short final message with the produced filenames and exact output paths.
""")
```

## Example of Upstream Input Files

The Writer does not need only prompt text. In a robust pipeline, upstream agents should leave structured files in the session workdir.

Source:

- [`README.md`](../README.md)
- [`holosophos/holosophos/langfuse_openai_patch.py`](../holosophos/holosophos/langfuse_openai_patch.py)
- [`mle_kit_mcp/mle_kit_mcp/files.py`](../mle_kit_mcp/mle_kit_mcp/files.py)

Example layout:

```text
workdir/<session_id>/
  started_prompt.md
  experiment_summary.md
  results/
    main_metrics.json
    ablation_metrics.json
  refs/
    citation_notes.md
  figures/
    retrieval_runtime.csv
```

### Example `experiment_summary.md`

```md
# Experiment Summary

## Method
Retrieval Consistency Training regularizes embeddings so semantically equivalent query variants produce similar retrieval behavior.

## Main result
The method improves nDCG@10 from 0.384 to 0.417 on MS MARCO passage retrieval.

## Additional result
Recall@50 improves from 0.812 to 0.846.

## Ablation
Removing the consistency loss reduces nDCG@10 to 0.401.

## Limitations
Performance depends on paraphrase quality and increases training cost.
```

### Example `results/main_metrics.json`

```json
{
  "dataset": "MS MARCO passage",
  "baseline": {
    "ndcg_at_10": 0.384,
    "recall_at_50": 0.812
  },
  "proposed": {
    "ndcg_at_10": 0.417,
    "recall_at_50": 0.846
  }
}
```

### Example `results/ablation_metrics.json`

```json
{
  "full_model": {
    "ndcg_at_10": 0.417
  },
  "without_consistency_loss": {
    "ndcg_at_10": 0.401
  }
}
```

### Example `refs/citation_notes.md`

```md
# Citation Notes

- Dense Passage Retrieval: include exact paper title and identifier from librarian output.
- Contriever: include exact paper title and identifier from librarian output.
- Query augmentation papers: cite only those explicitly verified upstream.
```

These example files are not enforced by the framework, but they are the kind of inputs that make the Writer much more reliable.

## Session and Workspace Semantics

The Writer does not write into a global flat directory. The runtime isolates work by `session_id`.

Source:

- [`README.md`](../README.md)
- [`holosophos/holosophos/langfuse_openai_patch.py`](../holosophos/holosophos/langfuse_openai_patch.py)
- [`mle_kit_mcp/mle_kit_mcp/files.py`](../mle_kit_mcp/mle_kit_mcp/files.py)
- [`mle_kit_mcp/mle_kit_mcp/tools/text_editor.py`](../mle_kit_mcp/mle_kit_mcp/tools/text_editor.py)

### Session-scoped workdir

All work is expected under:

```text
workdir/<session_id>/
```

The repo [`README.md`](../README.md) explicitly states:

- each run is isolated under `workdir/<session_id>/`,
- the `session_id` matches the client session and Langfuse session,
- the initial user prompt is persisted as `workdir/<session_id>/started_prompt.md`.

### Session normalization

Session IDs are normalized so unsafe characters are replaced with `_`.

This matters when you are checking files on disk or debugging path mismatches.

### Automatic path rewriting

The runtime adds an important convenience feature for some Writer tools:

- relative paths in certain tool calls are automatically rewritten to the current `session_id` subdirectory.

This is visible in [`langfuse_openai_patch.py`](../holosophos/holosophos/langfuse_openai_patch.py).

Observed rewrite coverage:

- `compile_latex`
- `read_pdf`
- `describe_image`
- `evaluate_diagram`
- `remote_download`
- `review_pdf_paper`
- `speech_to_text`

Important consequence:

- when the Writer calls `compile_latex("paper.tex")`, the runtime can rewrite that into the session workspace path.

There is also a fallback behavior for `compile_latex`:

- if no `output_filename` is supplied, the runtime injects `output_filename="paper.pdf"`.

That behavior is inferred from the session-path rewrite patch and is useful when debugging unexpected output names.

## Writer Toolset

The Writer tool allowlist is defined in [`WRITER_TOOLS`](../holosophos/holosophos/settings.py):

Source:

- [`holosophos/holosophos/settings.py`](../holosophos/holosophos/settings.py)
- [`README.md`](../README.md)

- `get_latex_template`
- `get_latex_templates_list`
- `compile_latex`
- `bash`
- `text_editor`
- `read_pdf`
- `describe_image`
- `generate_diagram`
- `generate_plot`
- `evaluate_diagram`

Below, each tool is described with the strongest evidence available.

## Tool Reference

## `get_latex_templates_list`

Status:

- exact name is confirmed in [`WRITER_TOOLS`](../holosophos/holosophos/settings.py)
- concrete implementation is not present in this repo
- usage pattern is shown in [`writer.yaml`](../holosophos/holosophos/prompts/writer.yaml)

Usage source:

- [`writer.yaml`](../holosophos/holosophos/prompts/writer.yaml)

Observed role:

- list available LaTeX templates for paper creation

Observed usage:

```python
templates = get_latex_templates_list()
print(templates)
```

Expected output shape:

- likely a list of template names

Prompt example output:

```python
["agents4science_2025"]
```

When to use:

- first step in document creation
- choosing a conference or house style template

## `get_latex_template`

Status:

- exact name is confirmed in [`WRITER_TOOLS`](../holosophos/holosophos/settings.py)
- concrete implementation is not present in this repo
- usage pattern is shown in [`writer.yaml`](../holosophos/holosophos/prompts/writer.yaml)

Usage source:

- [`writer.yaml`](../holosophos/holosophos/prompts/writer.yaml)

Observed role:

- fetch the contents or metadata of a chosen template

Observed usage:

```python
template = "agents4science_2025"
print(get_latex_template(template))
```

Prompt example output:

```json
{"template": "...", "style": "..."}
```

Reasonable interpretation:

- `template` is likely the main `.tex` scaffold
- `style` is likely auxiliary style information or template assets

When to use:

- after listing templates,
- before writing the final `paper.tex`.

## `compile_latex`

Status:

- exact name is confirmed in [`WRITER_TOOLS`](../holosophos/holosophos/settings.py)
- exact parameter names are partially visible through path-rewrite instrumentation
- concrete implementation is not present in this repo

Usage sources:

- [`writer.yaml`](../holosophos/holosophos/prompts/writer.yaml)
- [`langfuse_openai_patch.py`](../holosophos/holosophos/langfuse_openai_patch.py)

Observed parameter names:

- positional argument 0 or keyword `input_filename`
- positional argument 1 or keyword `output_filename`

Observed usage:

```python
result = compile_latex("paper.tex", output_filename="paper.pdf")
print(result)
```

Observed behavior from prompt and runtime:

- compiles the LaTeX source into a PDF
- should be retried after fixing errors
- if `output_filename` is omitted, runtime injects canonical `paper.pdf`

Likely output:

- a text result or structured result containing compiler output

When to use:

- after writing `paper.tex` and `references.bib`
- again after every substantial LaTeX fix

## `bash`

Status:

- implementation is present in [`bash.py`](../mle_kit_mcp/mle_kit_mcp/tools/bash.py)
- behavior is exact from source

Source and usage:

- implementation: [`bash.py`](../mle_kit_mcp/mle_kit_mcp/tools/bash.py)
- usage tests: [`test_bash.py`](../mle_kit_mcp/tests/test_bash.py)

Signature:

```python
bash(command: str, cwd: Optional[str] = None, timeout: int = 60, session_id: Optional[str] = None) -> str
```

Behavior:

- runs inside a Docker container
- mounts the workspace under `/workdir`
- requires `session_id` for isolated session execution
- maintains persistent container state across calls
- has no internet access through this tool
- enforces a timeout

Useful for Writer:

- listing files,
- checking generated artifacts,
- running helper shell commands around figures or LaTeX assets,
- inspecting compiler logs if they are materialized to files.

Examples:

```python
bash("ls -R", timeout=30)
```

```python
bash("mkdir -p figures && ls", timeout=30)
```

Typical return form:

```text
Command stdout: ...
Command stderr: ...
```

## `text_editor`

Status:

- implementation is present in [`text_editor.py`](../mle_kit_mcp/mle_kit_mcp/tools/text_editor.py)
- behavior is exact from source

Source and usage:

- implementation: [`text_editor.py`](../mle_kit_mcp/mle_kit_mcp/tools/text_editor.py)
- usage tests: [`test_text_editor.py`](../mle_kit_mcp/tests/test_text_editor.py)

Signature:

```python
text_editor(
    command: str,
    path: str,
    new_str: Optional[str] = None,
    file_text: Optional[str] = None,
    old_str: Optional[str] = None,
    insert_line: Optional[int] = None,
    view_start_line: Optional[int] = None,
    view_end_line: Optional[int] = None,
    show_lines: Optional[bool] = False,
    dry_run: Optional[bool] = False,
    session_id: Optional[str] = None,
) -> str
```

Allowed commands:

- `view`
- `write`
- `append`
- `insert`
- `str_replace`
- `undo_edit`

Important constraints:

- paths must be relative, not absolute
- paths are relative to the session workdir
- `str_replace` requires the old string to be unique
- edit history is stored so `undo_edit` can revert the last change

Useful for Writer:

- reading experiment files,
- creating `paper.tex`,
- creating `references.bib`,
- patching LaTeX errors,
- inspecting generated text files.

Examples:

```python
text_editor("view", "experiment_summary.md", show_lines=True)
```

```python
text_editor("write", "paper.tex", file_text=paper_source)
```

```python
text_editor(
    "str_replace",
    "paper.tex",
    old_str="\\section{Method}",
    new_str="\\section{Method}\n\\label{sec:method}",
)
```

## `read_pdf`

Status:

- exact name is confirmed in [`WRITER_TOOLS`](../holosophos/holosophos/settings.py)
- parameter name `pdf_path` is visible through instrumentation
- concrete implementation is not in this repo

Source hints:

- allowlist: [`settings.py`](../holosophos/holosophos/settings.py)
- path rewrite / parameter evidence: [`langfuse_openai_patch.py`](../holosophos/holosophos/langfuse_openai_patch.py)

Observed role:

- read a compiled PDF back into text or a machine-readable representation

Observed parameter form:

- positional argument 0, or keyword `pdf_path`

Likely uses:

- verifying the compiled paper,
- checking whether references and sections appear as expected,
- validating that the generated PDF is readable.

Example:

```python
paper_text = read_pdf("paper.pdf")
print(paper_text)
```

## `describe_image`

Status:

- exact name is confirmed in [`WRITER_TOOLS`](../holosophos/holosophos/settings.py)
- parameter name `path` is visible through instrumentation
- concrete implementation is not in this repo

Source hints:

- allowlist: [`settings.py`](../holosophos/holosophos/settings.py)
- path rewrite / parameter evidence: [`langfuse_openai_patch.py`](../holosophos/holosophos/langfuse_openai_patch.py)

Observed role:

- describe the contents of an image file

Likely uses in Writer:

- checking a generated figure before including it,
- inspecting plots or diagrams from prior steps,
- confirming figure content for captions.

Example:

```python
description = describe_image("figures/method_diagram.png")
print(description)
```

## `generate_diagram`

Status:

- exact name is confirmed in [`WRITER_TOOLS`](../holosophos/holosophos/settings.py)
- tool is routed to Writer through [`WRITER_TOOLS`](../holosophos/holosophos/settings.py)
- referenced in [PaperBanana integration docs](./implementation_plan_feature_paperbanana.md)
- concrete schema is not present in this repo

Source hints:

- allowlist: [`settings.py`](../holosophos/holosophos/settings.py)
- routing / intended usage: [PaperBanana integration docs](./implementation_plan_feature_paperbanana.md)

Observed role from docs:

- generate a methodology or conceptual diagram

PaperBanana integration plan says an end-to-end test should require:

- one methodology diagram via `generate_diagram`
- saved into `workdir/figures` or a subfolder

Practical expectation:

- use it when the paper needs a method overview figure
- save the result into a predictable path under `figures/`

Illustrative example:

```python
diagram_result = generate_diagram(
    prompt="Create a clean methodology diagram showing query paraphrases, shared encoder, consistency loss, and retrieval head.",
    output_path="figures/method_diagram.png",
)
print(diagram_result)
```

The exact schema above is illustrative, not source-confirmed.

## `generate_plot`

Status:

- exact name is confirmed in [`WRITER_TOOLS`](../holosophos/holosophos/settings.py)
- tool is routed to Writer through [`WRITER_TOOLS`](../holosophos/holosophos/settings.py)
- referenced in [PaperBanana integration docs](./implementation_plan_feature_paperbanana.md)
- concrete schema is not present in this repo

Source hints:

- allowlist: [`settings.py`](../holosophos/holosophos/settings.py)
- routing / intended usage: [PaperBanana integration docs](./implementation_plan_feature_paperbanana.md)

Observed role from docs:

- generate a statistical plot

PaperBanana integration plan says an end-to-end test should require:

- one statistical plot via `generate_plot`
- saved into `workdir/figures` or a subfolder

Illustrative example:

```python
plot_result = generate_plot(
    prompt="Plot nDCG@10 for baseline, full model, and ablation.",
    data_path="results/ablation_metrics.json",
    output_path="figures/ablation_plot.png",
)
print(plot_result)
```

The exact schema above is illustrative, not source-confirmed.

## `evaluate_diagram`

Status:

- exact name is confirmed in [`WRITER_TOOLS`](../holosophos/holosophos/settings.py)
- parameter names `generated_path` and `reference_path` are visible through instrumentation
- concrete implementation is not present in this repo

Source hints:

- allowlist: [`settings.py`](../holosophos/holosophos/settings.py)
- parameter evidence: [`langfuse_openai_patch.py`](../holosophos/holosophos/langfuse_openai_patch.py)

Observed role:

- compare or score a generated diagram against a reference image or expected figure

Possible uses:

- refining a figure,
- checking whether a generated diagram matches a reference sketch,
- automated figure QA in the Writer loop.

Example with source-confirmed parameter names:

```python
evaluation = evaluate_diagram(
    generated_path="figures/method_diagram.png",
    reference_path="figures/reference_method_sketch.png",
)
print(evaluation)
```

## Typical Writer Working Pattern

A realistic Writer run usually looks like this:

Source:

- [`holosophos/holosophos/prompts/writer.yaml`](../holosophos/holosophos/prompts/writer.yaml)
- [`holosophos/holosophos/settings.py`](../holosophos/holosophos/settings.py)

1. Read prompt and files in full.
2. Inspect available templates.
3. Choose a template.
4. Read experiment summaries and citation notes.
5. Write `paper.tex`.
6. Write `references.bib`.
7. Generate diagrams or plots if requested.
8. Compile the paper.
9. Read or inspect the PDF.
10. Patch errors and recompile until successful.
11. Return a short final message with artifact names and paths.

## End-to-End Example Tool Sequence

This is a practical example of the kinds of tool calls the Writer would make during one run.

Source:

- [`holosophos/holosophos/prompts/writer.yaml`](../holosophos/holosophos/prompts/writer.yaml)
- [`mle_kit_mcp/mle_kit_mcp/tools/text_editor.py`](../mle_kit_mcp/mle_kit_mcp/tools/text_editor.py)
- [`mle_kit_mcp/mle_kit_mcp/tools/bash.py`](../mle_kit_mcp/mle_kit_mcp/tools/bash.py)
- [`docs/implementation_plan_feature_paperbanana.md`](./implementation_plan_feature_paperbanana.md)

```python
templates = get_latex_templates_list()
print(templates)
```

```python
template_name = templates[0]
template_payload = get_latex_template(template_name)
print(template_payload)
```

```python
summary = text_editor("view", "experiment_summary.md")
metrics = text_editor("view", "results/main_metrics.json")
citations = text_editor("view", "refs/citation_notes.md")
print(summary)
print(metrics)
print(citations)
```

```python
diagram_result = generate_diagram(
    prompt="Create a methodology figure for Retrieval Consistency Training.",
    output_path="figures/method_diagram.png",
)
print(diagram_result)
```

```python
plot_result = generate_plot(
    prompt="Create a bar plot comparing baseline and proposed nDCG@10 and Recall@50.",
    data_path="results/main_metrics.json",
    output_path="figures/main_results.png",
)
print(plot_result)
```

```python
paper_source = r"""
\\documentclass{article}
% template content would normally come from get_latex_template()
\\begin{document}
\\title{Retrieval Consistency Training}
\\maketitle
\\begin{abstract}
...
\\end{abstract}
...
\\bibliography{references}
\\end{document}
"""
text_editor("write", "paper.tex", file_text=paper_source)
```

```python
references = r"""
@article{dpr,
  title={Dense Passage Retrieval for Open-Domain Question Answering},
  author={...},
  year={2020}
}
"""
text_editor("write", "references.bib", file_text=references)
```

```python
compile_result = compile_latex("paper.tex", output_filename="paper.pdf")
print(compile_result)
```

```python
paper_text = read_pdf("paper.pdf")
print(paper_text)
```

```python
<final_answer>
The paper is complete and saved as "paper.tex", "references.bib", and "paper.pdf".
</final_answer>
```

The figure-generation call signatures in this sequence are illustrative. The compile, file-edit, and agent-call parts are source-grounded.

## Example of Good Prompt Plus Good Files

If you want the Writer to behave reliably, combine a strong prompt with structured files.

Source:

- [`holosophos/holosophos/prompts/system.yaml`](../holosophos/holosophos/prompts/system.yaml)
- [`holosophos/holosophos/prompts/writer.yaml`](../holosophos/holosophos/prompts/writer.yaml)
- [`./manager_to_writer_handoff.md`](./manager_to_writer_handoff.md)

### Manager prompt

```text
You are in the final writing phase.
Write a complete paper from the verified experiment outputs in the workdir.
Use only the provided evidence.
Do not invent metrics or citations.
Use an existing LaTeX template and compile the result.
Create paper.tex, references.bib, and paper.pdf.
Read these files fully before drafting:
- experiment_summary.md
- results/main_metrics.json
- results/ablation_metrics.json
- refs/citation_notes.md
Include all mandatory sections.
Include one methodology diagram and one main-results plot.
Save figures under figures/.
Return exact output paths.
```

### Workdir files

```text
workdir/<session_id>/
  experiment_summary.md
  results/main_metrics.json
  results/ablation_metrics.json
  refs/citation_notes.md
```

This combination gives the Writer:

- narrative context,
- quantitative evidence,
- citation guidance,
- deterministic filenames,
- explicit figure expectations.

## Output Expectations

At minimum, a successful Writer run should leave:

Source:

- [`holosophos/holosophos/prompts/writer.yaml`](../holosophos/holosophos/prompts/writer.yaml)
- [`README.md`](../README.md)
- [`mle_kit_mcp/mle_kit_mcp/files.py`](../mle_kit_mcp/mle_kit_mcp/files.py)

```text
workdir/<session_id>/
  started_prompt.md
  paper.tex
  references.bib
  paper.pdf
```

If figures are requested, a better result looks like:

```text
workdir/<session_id>/
  started_prompt.md
  experiment_summary.md
  results/
    main_metrics.json
    ablation_metrics.json
  refs/
    citation_notes.md
  figures/
    method_diagram.png
    main_results.png
  paper.tex
  references.bib
  paper.pdf
```

The Writer's final textual answer is usually brief, for example:

```text
The paper is complete and saved as "paper.tex", "references.bib", and "paper.pdf".
```

## Streaming vs Non-Streaming Behavior

When the Writer endpoint is called with `stream=true`:

Source:

- [`codearkt/codearkt/server.py`](../codearkt/codearkt/server.py)
- [`codearkt/container/tools.py`](../codearkt/container/tools.py)
- [`codearkt/codearkt/client.py`](../codearkt/codearkt/client.py)

- the server returns NDJSON events from the event bus,
- you can observe intermediate model and tool activity.

When called with `stream=false`:

- the server waits for completion,
- then returns the final answer only.

This distinction matters if you are debugging stalls or trying to observe incremental tool use.

## Practical Debugging Notes

If the Writer is failing or producing weak papers, the most common failure modes are likely:

Source:

- [`README.md`](../README.md)
- [`holosophos/holosophos/langfuse_openai_patch.py`](../holosophos/holosophos/langfuse_openai_patch.py)
- [`mle_kit_mcp/mle_kit_mcp/files.py`](../mle_kit_mcp/mle_kit_mcp/files.py)

- incomplete manager prompt,
- missing experiment files,
- missing or low-quality citations,
- figure requests without clear save paths,
- LaTeX compilation failure,
- overreliance on vague prose instead of structured inputs.

Good debugging checks:

- inspect `workdir/<session_id>/started_prompt.md`
- inspect whether the expected input files actually exist
- inspect generated `paper.tex`
- inspect `references.bib`
- confirm whether `paper.pdf` exists
- confirm whether figure files exist under `figures/`
- check whether the prompt omitted critical metrics or exact citations

## What Is Exact vs Inferred

Exact from source:

Source:

- [`holosophos/holosophos/agents/writer.py`](../holosophos/holosophos/agents/writer.py)
- [`holosophos/holosophos/prompts/writer.yaml`](../holosophos/holosophos/prompts/writer.yaml)
- [`holosophos/holosophos/settings.py`](../holosophos/holosophos/settings.py)
- [`codearkt/container/tools.py`](../codearkt/container/tools.py)
- [`codearkt/codearkt/server.py`](../codearkt/codearkt/server.py)
- [`holosophos/holosophos/langfuse_openai_patch.py`](../holosophos/holosophos/langfuse_openai_patch.py)
- [`mle_kit_mcp/mle_kit_mcp/tools/bash.py`](../mle_kit_mcp/mle_kit_mcp/tools/bash.py)
- [`mle_kit_mcp/mle_kit_mcp/tools/text_editor.py`](../mle_kit_mcp/mle_kit_mcp/tools/text_editor.py)

- Writer exists as a managed agent named `writer`
- it is called through `agent__writer`
- it must produce `.tex`, `.bib`, and `.pdf`
- it must use an existing LaTeX template
- it must include the mandatory sections
- it has access to the listed tools in `WRITER_TOOLS`
- `bash` and `text_editor` exact behavior
- session-scoped workdir behavior
- low-level agent endpoint shape
- path rewrite support for some file-based tools
- `evaluate_diagram(generated_path=..., reference_path=...)` parameter names

Inferred from prompts and surrounding docs:

- practical output format of LaTeX helper tools beyond the prompt example
- exact request schema of `generate_diagram`
- exact request schema of `generate_plot`
- exact return payload shape for PaperBanana tools
- exact implementation of `read_pdf` and `describe_image`

## Short Mental Model

The Writer contract is best summarized like this:

- input is one long, self-contained manager handoff plus any files already placed in the session workdir,
- work happens inside `workdir/<session_id>/`,
- Writer reads evidence, drafts LaTeX, writes `references.bib`, generates figures if needed, compiles PDF, and retries on errors,
- output is a paper artifact bundle plus a short final confirmation message.
