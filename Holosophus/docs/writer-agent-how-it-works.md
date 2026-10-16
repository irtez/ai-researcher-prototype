# How the Writer Agent Works in Holosophus

This document provides a detailed description of the architecture, operational principles, contracts, and tools of the `writer` agent. Each mention of functionality is accompanied by a link to the corresponding file and lines of code in the repository.

---

## 1. Definition and Agent Initialization

The `writer` agent is a specialized agent based on the CodeAct loop (CodeArkt), designed to write scientific papers in LaTeX format and compile them into PDF.

- **Agent Initialization Code**: [`holosophos/holosophos/agents/writer.py`](../holosophos/holosophos/agents/writer.py) (lines 25-45).
- **Agent Name (`NAME`)**: `"writer"` (line 11).
- **Description (`DESCRIPTION`)**: lines 12-22. Describes the agent's specialization in composing technical texts, mathematical formulas, tables, and plots.
- **Prompts and Rules**: Loaded from [`holosophos/holosophos/prompts/writer.yaml`](../holosophos/holosophos/prompts/writer.yaml) and merged with the base CodeArkt framework (lines 32-35).

---

## 2. System Prompt and Behavioral Contract

The agent's behavior is strictly regulated by its system prompt in [`holosophos/holosophos/prompts/writer.yaml`](../holosophos/holosophos/prompts/writer.yaml):

- **Role and Expertise**: lines 5-19. The agent positions itself as an expert AI scientist who writes high-quality academic papers for top-tier conferences (A*) and Q1 journals.
- **Required Output Files**: lines 23-34. The agent is required to produce three files:
  1. `paper.tex` (paper source code)
  2. `references.bib` (bibliography database)
  3. `paper.pdf` (compiled document)
- **Paper structure and length**: follow the assignment and supported evidence. Use the relevant scientific sections, merging or omitting optional sections when appropriate. There is no universal eight-page minimum. Explicit venue requirements apply when requested. Acknowledgments, references, bibliography and appendix material are excluded from the reported main-content length, while standalone Discussion counts.
- **Language Policy**: lines 45-49. The paper is written strictly in natural academic English by default, even if the source data is provided in another language.
- **LaTeX Pipeline Instructions**: lines 231-240. Describe the step-by-step process: choose a template, create files, compile to PDF, fix errors, and verify citations.

---

## 3. How the Manager Invokes the Writer (Handoff)

Since the agents in the system are **stateless**, the manager is responsible for passing an exhaustive context to the writer as a single long string.

- **Manager Context Collection Rules**: [`holosophos/holosophos/prompts/system.yaml`](../holosophos/holosophos/prompts/system.yaml) (lines 127-141). The manager is instructed to collect all facts, constraints, a detailed summary of previous steps, and detailed instructions (the handoff description should have more than 20 sentences).
- **Delegation Tool**: The manager invokes the writer using the `agent__writer` tool, whose wrapper is implemented in [`codearkt/container/tools.py`](../codearkt/container/tools.py) (lines 131-141). This tool makes an HTTP POST request to the agent server.
- **Session Isolation**: Requests are isolated by `session_id` (line 135), ensuring that each research session operates in its own subdirectory `workdir/<session_id>/`.

---

## 4. Toolbox and Execution Environments

The list of tools available to the writer (`WRITER_TOOLS`) is specified in the configuration file:
- **Tool Allowlist**: [`holosophos/holosophos/settings.py`](../holosophos/holosophos/settings.py) (lines 83-94).

### Two Execution Environments (Critical!):
The system prompt [`holosophos/holosophos/prompts/writer.yaml`](../holosophos/holosophos/prompts/writer.yaml) (lines 67-76) describes a crucial environment constraint:
1. **`<execute>` Sandbox (CodeAct)**: A lightweight environment. Only the Python standard library and `pandas` are available. Scientific/ML packages (`numpy`, `matplotlib`, `scipy`, `PIL/Pillow`, etc.) **are NOT installed** in this sandbox. Attempting to import them directly will result in a `ModuleNotFoundError`.
2. **`bash` Tool**: A full container where all scientific and ML packages are installed. For complex data processing or plotting, the writer must write a script using `text_editor` and run it via `bash("python3 script.py")`.

### Key Tools:
- **`bash`**: Implemented in [`mle_kit_mcp/mle_kit_mcp/tools/bash.py`](../mle_kit_mcp/mle_kit_mcp/tools/bash.py). Allows executing commands in an isolated Docker container.
- **`text_editor`**: Implemented in [`mle_kit_mcp/mle_kit_mcp/tools/text_editor.py`](../mle_kit_mcp/mle_kit_mcp/tools/text_editor.py). Supports commands like `view`, `write`, `append`, `insert`, `str_replace`, and `undo_edit` to manipulate project files.
- **`get_latex_templates_list` and `get_latex_template`**: Used to list, select, and fetch LaTeX templates.
- **`compile_latex`**: Compiles LaTeX source files into a PDF document.
- **`generate_diagram` and `generate_plot`**: PaperBanana tools used to generate methodology diagrams and statistical plots.

---

## 5. Automatic Path Rewriting

For the agent's convenience, the runtime automatically intercepts calls to certain file-based tools and rewrites relative paths into isolated paths under the current session's workspace (`workdir/<session_id>/`).

- **Path Rewrite Tools Registry**: [`holosophos/holosophos/langfuse_openai_patch.py`](../holosophos/holosophos/langfuse_openai_patch.py) (lines 58-73). Rewriting is applied to `compile_latex`, `read_pdf`, `describe_image`, `evaluate_diagram`, etc.
- **Argument Rewriting Logic**: [`holosophos/holosophos/langfuse_openai_patch.py`](../holosophos/holosophos/langfuse_openai_patch.py) (lines 130-141).
- **Default PDF Name Fallback**: If the agent calls `compile_latex` without specifying an output filename, the runtime automatically injects the canonical `output_filename="paper.pdf"` (lines 142-154).

---

## 6. Running Benchmarks and Testing the Writer

To evaluate the quality of the writer's work, the system includes a built-in testing pipeline based on distilled data packages (distilled packs) containing actual experiment results.

- **Single Test Runner Script**: [`scripts/run_writer_pack.py`](../scripts/run_writer_pack.py). Copies the distilled pack to `workdir/<session_id>/`, sends the generated `writer_handoff.md` to the writer agent, verifies the creation of `.tex`, `.bib`, and `.pdf` files, and saves the run metadata.
- **Batch Test Runner Script**: [`scripts/run_writer_packs.py`](../scripts/run_writer_packs.py). Sequentially runs the writer on multiple distilled packs.
- **Evaluation Metrics (Evaluators)**: Compilation quality and paper structure metrics are verified by evaluators in `benchmark/writer/base/evaluator.py` and `benchmark/writer/acl/evaluator.py`.
