#!/usr/bin/env bash
set -euo pipefail

# Stops docker compose stack and cleans workdir contents,
# preserving workdir/.gitkeep.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
WORKDIR_PATH="${REPO_ROOT}/workdir"

DRY_RUN=0
SKIP_STOP=0
SKIP_CLEAN=0

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --no-stop) SKIP_STOP=1 ;;
    --no-clean) SKIP_CLEAN=1 ;;
    *)
      echo "Unknown argument: $arg"
      echo "Usage: $0 [--dry-run] [--no-stop] [--no-clean]"
      exit 2
      ;;
  esac
done

cd "${REPO_ROOT}"

if [[ "${SKIP_STOP}" -eq 0 ]]; then
  echo "Stopping docker compose stack..."
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    echo "[dry-run] docker compose down"
  else
    docker compose down
  fi
fi

if [[ "${SKIP_CLEAN}" -eq 0 ]]; then
  if [[ ! -d "${WORKDIR_PATH}" ]]; then
    echo "workdir not found at: ${WORKDIR_PATH}"
    exit 1
  fi

  echo "Cleaning workdir (preserving .gitkeep)..."
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    find "${WORKDIR_PATH}" -mindepth 1 ! -name ".gitkeep" -print
    echo "[dry-run] no files were removed"
  else
    find "${WORKDIR_PATH}" -mindepth 1 ! -name ".gitkeep" -exec rm -rf {} +
  fi
fi

echo "Done."
