# Testing Rules

Rules for choosing and running validation in Holosophus.

Core principle: do not stop at "some tests passed". Choose the cheapest reliable test surface for the invariant being changed.

The repo uses a mix of:
- canonical tests inside the vendored app/runtime repos
- integration smoke checks from the `Holosophus/` layer
- benchmark/eval runners under `Holosophus/holosophos/reports/`

## Current source verification

Run `make verify` from `Holosophus/` for the maintained integration:

- Python syntax/runtime-error checks across `scripts`, root `tests`, the app and
  its tests (Ruff E9/F63/F7/F82);
- actual Claude Code and CodeArkt prompt composition tests, without model calls;
- UI unit tests and production build, including the TypeScript check;
- documentation checks.

This command does not start services, require runtime credentials, format source
files or run research scenarios. Install root dev dependencies with
`uv sync --locked --inexact` and UI dependencies with `pnpm install --frozen-lockfile`
from `ui/`. `make validate` runs just the Python checks and prompt contracts.
Run additional targeted tests when the change requires them; `verify` is not the
full integration suite and does not claim strict Python typing coverage.

The old app's Black/flake8/mypy command remains as `make validate-legacy`, with
Black in check-only mode. Its existing formatting/type issues and separate
runtime dependencies have not been fixed by changing the root entry point.
`make check-env` still validates the original CodeArkt/Langfuse v2 stack before
its launch; Claude Code and source verification do not use that preflight.

## Test Level Decision

| Change Type | Minimum Test Path | Additional If Needed |
| --- | --- | --- |
| Prompt-only tweak | targeted test or benchmark subset | Langfuse inspection |
| Agent composition | `make test` | smoke run |
| Tool bridge / executor | codearkt tests + `make smoke` | Langfuse tool span check |
| Docker / compose / .env | `make smoke` | `make smoke-e2e` |
| Observability / Langfuse | benchmark subset + Langfuse inspection | SQL query verification |
| Settings / env | `make check-env` + smoke | — |
| New agent or tool | new test + `make smoke` | benchmark subset |

## Main Validation Paths

### 1. Holosophus app tests

Canonical app tests live in:
- `Holosophus/holosophos/tests/`

Run:

```bash
cd Holosophus/holosophos
uv run pytest -s
```

Use this when changing:
- agent prompts
- app composition
- Holosophos package logic

## 2. CodeArkt runtime tests

Canonical runtime tests live in:
- `Holosophus/codearkt/tests/`

Run:

```bash
cd Holosophus/codearkt
uv run pytest -s
```

Use this when changing:
- executor behavior
- tool bridges
- client/server runtime logic
- Langfuse/runtime integration at the CodeArkt layer

## 3. Validation bundle

Run the vendored app validation bundle when touching broad app/runtime behavior:

```bash
cd Holosophus/holosophos
make validate
```

This is the higher-signal path for:
- lint
- formatting
- type and validation checks defined by the vendored repo

## 4. Integration smoke

Run from the integration layer:

```bash
cd Holosophus
python scripts/smoke_test_pipeline.py
```

Use this when changing:
- Docker wiring
- `.env`-driven behavior
- app-to-tool integration
- Langfuse ingestion from the main stack

## 5. Benchmark and eval runners

Canonical runners live in:
- `Holosophus/holosophos/reports/`

Key entrypoints:
- `run_gaia.py`
- `run_librarian_eval.py`
- `run_mle_solver_eval.py`
- `run_proposer_eval.py`
- `run_writer_eval.py`
- `run_reviewer_eval.py`

Examples:

```bash
cd Holosophus/holosophos
uv run python reports/run_gaia.py --nrows=10 --enable_langfuse=true
uv run python reports/run_librarian_eval.py reports/data/librarian_test.jsonl
```

Use these when changing:
- benchmark instrumentation
- prompt behavior under realistic tasks
- tool usage patterns during longer runs
- benchmark grouping and Langfuse observability

## 6. Observability checks

For trace-level verification, use:
- `Holosophus/docs/observability.md`

Typical checks:
- does a run appear in Langfuse
- are traces grouped under the intended `session_id`
- do steps and tool spans carry useful input/output
- are errors surfaced as `ERROR` instead of silent text outputs

## Selection Guide

Use the smallest useful validation path:
- prompt-only tweak: targeted test or small benchmark subset
- executor/tool bridge change: `codearkt` tests plus a smoke run
- app/runtime integration change: `make validate` plus integration smoke
- benchmark/observability change: benchmark subset plus Langfuse inspection

## Exit Criteria by Change Type

### Small local change (prompt tweak, config edit)

- targeted test passes
- `make validate` for the affected area
- contract check if an API surface is involved

### Integration-heavy change (new tool, new MCP service, executor change)

- targeted tests pass
- integration smoke passes (`make smoke`)
- Langfuse trace verification (tool spans appear, no silent errors)

### Large feature (new agent, multi-file refactor)

- `make test-e2e` passes (full rebuild + smoke + tests)
- benchmark subset does not regress
- Langfuse trace structure matches expectations
- for tool routing changes: verify tool discovery in Langfuse tool spans
