#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"
PROMPT_FILE="${ROOT_DIR}/scripts/prompts/gemini_linear_attention_research.txt"
URL="http://127.0.0.1:5055"
MODEL_NAME="google/gemini-3-flash-preview"
DELAY_SECONDS="0"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prompt-file)
      PROMPT_FILE="$2"
      shift 2
      ;;
    --url)
      URL="$2"
      shift 2
      ;;
    --model-name)
      MODEL_NAME="$2"
      shift 2
      ;;
    --delay-seconds)
      DELAY_SECONDS="$2"
      shift 2
      ;;
    *)
      echo "Unknown arg: $1" >&2
      exit 1
      ;;
  esac
done

if [[ ! -f "${PROMPT_FILE}" ]]; then
  echo "Prompt file not found: ${PROMPT_FILE}" >&2
  exit 1
fi

if [[ ! -f "${ENV_FILE}" ]]; then
  cp "${ROOT_DIR}/.env_example" "${ENV_FILE}"
fi

ENV_FILE="${ENV_FILE}" MODEL_NAME="${MODEL_NAME}" python - <<'PY'
from pathlib import Path
import os
env_file = Path(os.environ["ENV_FILE"])
model_name = os.environ["MODEL_NAME"]
lines = env_file.read_text(encoding="utf-8").splitlines()
updated = False
for idx, line in enumerate(lines):
    if line.startswith("MODEL_NAME="):
        lines[idx] = f"MODEL_NAME={model_name}"
        updated = True
        break
if not updated:
    lines.append(f"MODEL_NAME={model_name}")
env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY

echo "MODEL_NAME set to ${MODEL_NAME}"
cd "${ROOT_DIR}"
docker compose up -d

SESSION_ID="research-$(date +%Y%m%d%H%M%S)"
echo "Using session_id=${SESSION_ID}"

cd "${ROOT_DIR}/codearkt"
uv run -m codearkt.terminal \
  --url "${URL}" \
  --session-id "${SESSION_ID}" \
  --message-file "${PROMPT_FILE}" \
  --delay-seconds "${DELAY_SECONDS}"
