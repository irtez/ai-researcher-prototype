#!/usr/bin/env bash
# Launch interactive Claude Code as the research orchestrator, with Langfuse
# observability. The HOLO_CC_TRACING switch (shell env wins, else .env, else
# default "hook") selects the path:
#
#   hook  Path A only — .claude/settings.json hooks export the session JSONL to
#         Langfuse v3 via REST (rich nested trace). No OTel env needed.
#   otel  Path B only — stream Claude Code's beta OTel spans to Langfuse v3.
#   both  Path A + Path B (belt-and-suspenders).
#   off   no tracing.
#
# Path A also works under a bare `claude` (the hooks fire and self-gate on the
# switch). This launcher exists for Path B, whose OTel config must be in Claude
# Code's process env.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

# Read a single KEY from .env safely (no sourcing — .env values may contain
# spaces/quotes). Returns the value after the first '=', minus surrounding
# quotes. Python-dotenv (used by the Path A exporter) parses .env the same way.
read_env_val() {
  local line val
  line="$(grep -E "^[[:space:]]*$1=" .env 2>/dev/null | tail -n1 || true)"
  [ -n "$line" ] || return 0
  val="${line#*=}"
  val="${val%\"}"; val="${val#\"}"
  val="${val%\'}"; val="${val#\'}"
  printf '%s' "$val"
}

# Shell env wins; else .env; else default.
MODE="${HOLO_CC_TRACING:-$(read_env_val HOLO_CC_TRACING)}"
MODE="${MODE:-hook}"
export HOLO_CC_TRACING="$MODE"   # so the hooks subprocess sees the same mode

if [ "$MODE" = "otel" ] || [ "$MODE" = "both" ]; then
  PK="${LANGFUSE_PUBLIC_KEY_V3:-$(read_env_val LANGFUSE_PUBLIC_KEY_V3)}"
  SK="${LANGFUSE_SECRET_KEY_V3:-$(read_env_val LANGFUSE_SECRET_KEY_V3)}"
  LF_HOST="${LANGFUSE_BASE_URL_V3:-$(read_env_val LANGFUSE_BASE_URL_V3)}"
  LF_HOST="${LF_HOST:-http://localhost:3001}"
  [ -n "$PK" ] || { echo "[research_console] set LANGFUSE_PUBLIC_KEY_V3 in .env" >&2; exit 1; }
  [ -n "$SK" ] || { echo "[research_console] set LANGFUSE_SECRET_KEY_V3 in .env" >&2; exit 1; }
  AUTH="$(printf '%s:%s' "$PK" "$SK" | base64 | tr -d '\n')"

  export CLAUDE_CODE_ENABLE_TELEMETRY=1
  export CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1
  # Langfuse OTLP is HTTP-only and ingests TRACES only: send spans on the
  # per-signal traces endpoint, leave metrics/logs exporters off.
  export OTEL_TRACES_EXPORTER=otlp
  export OTEL_METRICS_EXPORTER=none
  export OTEL_LOGS_EXPORTER=none
  export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
  export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT="${LF_HOST%/}/api/public/otel/v1/traces"
  export OTEL_EXPORTER_OTLP_TRACES_HEADERS="Authorization=Basic ${AUTH}"
  # Local self-hosted Langfuse → capture prompt + tool content (redacted by default).
  export OTEL_LOG_USER_PROMPTS=1
  export OTEL_LOG_TOOL_CONTENT=1
  echo "[research_console] Path B native OTel traces -> ${OTEL_EXPORTER_OTLP_TRACES_ENDPOINT}" >&2
fi

echo "[research_console] HOLO_CC_TRACING=${MODE}" >&2
exec claude "$@"
