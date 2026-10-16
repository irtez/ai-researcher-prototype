.PHONY: install black validate validate-legacy verify verify-python verify-prompts verify-ui verify-docs test smoke smoke-e2e smoke-writer-tools test-e2e stop-clean cleanup-debug check-env up recreate-app recreate-writer-tools up-mcp up-mcp-parallel sync-agents research-console cloudru-smoke selectel-smoke cloudru-job-smoke cloudru-jobs-list cloudru-jobs-killall

# Resolve Python via uv (no bare `python` on PATH on some machines).
PYTHON ?= uv run python
.DEFAULT_GOAL := up-claude
EXECUTOR_IMAGE ?= ai-researcher-executor:2026-10-10

.PHONY: executor-image
executor-image:
	docker build -t $(EXECUTOR_IMAGE) docker/executor

.PHONY: up-claude down-claude cc-runner up-chemistry down-chemistry test-chemistry chemistry-tools-list chemistry-prepare-datasets
up-claude:
	docker compose -f docker-compose.claude.yml up --build -d --wait --wait-timeout 300

down-claude:
	docker compose -f docker-compose.claude.yml down

cc-runner:
	uv run --locked --env-file .env python scripts/cc_runner.py

up-chemistry:
	docker compose -f docker-compose.claude.yml --profile chemistry up --build -d --wait --wait-timeout 300 chem chem_conversions

down-chemistry:
	docker compose -f docker-compose.claude.yml --profile chemistry stop chem chem_conversions

test-chemistry:
	docker compose -f docker-compose.claude.yml --profile chemistry run --rm --no-deps chem python -m unittest discover -s tests
	docker compose -f docker-compose.claude.yml --profile chemistry run --rm --no-deps chem_conversions python -m unittest discover -s tests

chemistry-tools-list:
	docker compose -f docker-compose.claude.yml --profile chemistry run --rm --no-deps chem python smoke_http.py --url http://chem:5058/mcp
	docker compose -f docker-compose.claude.yml --profile chemistry run --rm --no-deps chem python smoke_http.py --url http://chem_conversions:5061/mcp

chemistry-prepare-datasets:
	docker compose -f docker-compose.claude.yml --profile chemistry run --rm --no-deps chem python -m prepare_datasets --preload

install:
	# --inexact: root pyproject and holosophos/ share one .venv. Without
	# this flag, `uv sync` would uninstall CodeArkt deps (accelerate,
	# datasets, transformers, ...) that the root manifest doesn't list.
	uv sync --inexact
	$(MAKE) -C holosophos install

# Runtime preflight for the original CodeArkt/Langfuse v2 compose stack.
# Source verification and the Claude Code launcher do not require these keys.
check-env:
	$(PYTHON) scripts/check_env_required.py

up: check-env
	docker compose up --build -d

up-mcp: check-env
	docker compose -f docker-compose.mcp.yml up --build -d

up-mcp-parallel: check-env
	docker compose -f docker-compose.mcp.yml --profile parallel up --build -d mle_kit_2

recreate-app: check-env
	docker compose up -d --build --no-deps --force-recreate app

# cloud.ru: set REMOTE_PROVIDER=cloudru + CLOUDRU_* in .env (or shell), then
# `make up` or `make up-mcp` -- cloud.ru wiring is baked into the standard
# compose files. The SSH key bind falls back to /dev/null if you don't have
# one (Vast users, this is harmless).
cloudru-smoke:
	docker exec holosophus-mle_kit-1 sh -c 'ls -l /root/.ssh/cloudru_key && python -m mle_kit_mcp.scripts.cloudru_smoke'

# Selectel: set REMOTE_PROVIDER=selectel + SELECTEL_* in .env (or shell), then
# `make up` / `make up-mcp` — Selectel wiring is baked into the standard compose
# files. Proves SC7: required plumbing (hostname/user/home + rsync round-trip)
# plus the one-card GPU pin and distinct cards for two sessions on the shared
# multi-GPU host. Real remote run, not lint (AP-05).
selectel-smoke:
	docker exec holosophus-mle_kit-1 sh -c 'ls -l /root/.ssh/selectel_key && python -m mle_kit_mcp.scripts.selectel_smoke'

# Heavy variant — actually submits a binary job to SR006, waits for Running,
# kills it. Proves namespace can submit AND cluster gives us a GPU pod (vs
# `cloudru-smoke` which only checks SSH plumbing). 1-30 min wall-clock; SR006
# queue wait varies wildly with load (priority_class=high does not help). See
# docs/operations/cloudru-mlspace.md "Smoke-checks" for observed timings.
cloudru-job-smoke:
	docker exec holosophus-mle_kit-1 sh -c 'ls -l /root/.ssh/cloudru_key && python -m mle_kit_mcp.scripts.cloudru_smoke --job'

# List ALL jobs in our SR006 namespace, flag any Running/Pending (use to
# verify nothing was left behind after debug iterations).
cloudru-jobs-list:
	docker exec holosophus-mle_kit-1 sh -c 'python -m mle_kit_mcp.scripts.cloudru_jobs --action list'

# Kill ALL Running/Pending jobs in our namespace. Use sparingly.
cloudru-jobs-killall:
	docker exec holosophus-mle_kit-1 sh -c 'python -m mle_kit_mcp.scripts.cloudru_jobs --action killall'
recreate-writer-tools: check-env
	docker compose up -d --build --no-deps --force-recreate writer-tools

black:
	$(MAKE) -C holosophos black

validate-legacy:
	$(MAKE) -C holosophos validate

# Current integration checks: no .env preflight, services, model calls or source edits.
validate: verify-python verify-prompts

verify: validate verify-ui verify-docs

verify-python:
	uv run --locked ruff check --select E9,F63,F7,F82 scripts tests holosophos/holosophos holosophos/tests
	uv run --locked python scripts/cc_config.py
	PYTHONPATH=holosophos uv run --locked python -m holosophos.model_catalog

verify-prompts:
	PYTHONPATH=holosophos:codearkt:scripts ENABLE_PHOENIX=false LANGFUSE_PUBLIC_KEY_V3= LANGFUSE_SECRET_KEY_V3= uv run --locked pytest --noconftest -q tests/test_prompt_boundaries.py holosophos/tests/test_prompt_composition.py tests/test_cc_config.py tests/test_approval_prompt_migration.py

verify-ui:
	npm --prefix ui test
	npm --prefix ui run build

verify-docs:
	bash scripts/check-docs.sh

test:
	$(MAKE) -C holosophos test

smoke:
	$(PYTHON) scripts/smoke_test_pipeline.py

smoke-writer-tools:
	$(PYTHON) scripts/smoke_test_writer_tools_mcp.py --phase all

smoke-e2e:
	docker compose down
	$(MAKE) up
	@attempt=1; \
	max_attempts=6; \
	until [ $$attempt -gt $$max_attempts ]; do \
		echo "Smoke attempt $$attempt/$$max_attempts..."; \
		if $(MAKE) smoke; then \
			exit 0; \
		fi; \
		attempt=$$((attempt + 1)); \
		sleep 5; \
	done; \
	echo "Smoke failed after $$max_attempts attempts"; \
	exit 1

test-e2e:
	$(MAKE) smoke-e2e
	$(MAKE) test

stop-clean:
	bash scripts/stop_and_clean_workdir.sh

cleanup-debug:
	$(PYTHON) scripts/cleanup_debug_sessions.py

# Manual escape hatch: entry points (run_research.py, run_eval_claude_code.py)
# regenerate .claude/agents/*.md from YAML automatically. This target only matters
# if you want to inspect the rendered .md without running the pipeline.
sync-agents:
	$(PYTHON) scripts/sync_claude_agents.py

# Launch interactive Claude Code as the research orchestrator with Langfuse
# tracing (HOLO_CC_TRACING = hook|otel|both|off; default hook). See
# docs/observability.md "Interactive CLI path".
research-console:
	bash scripts/research_console.sh

# Standalone console client; shared research services, no browser frontend.
.PHONY: console-services console-runner prototype-console
console-services:
	docker compose -p ai-researcher-console -f docker-compose.claude.yml -f docker-compose.console.yml --profile chemistry up --build -d --wait --wait-timeout 300 ui-api academia mle_kit paperbanana-mcp writer-tools chem chem_conversions

console-runner:
	CC_RUNNER_BIND=127.0.0.1 uv run --locked --env-file .env python scripts/cc_runner.py

prototype-console:
	uv run --locked python scripts/prototype_console.py $(ARGS)
