#!/usr/bin/env bash
set -euo pipefail

# Clone on first run; fast-forward update on subsequent runs.
# Usage:
#   bash sync.sh           # clone or update all
#   bash sync.sh --status  # show brief status for all subrepos

SCRIPT_DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
cd "${SCRIPT_DIR}"

declare -A REPOS=(
  [academia_mcp]=https://github.com/IlyaGusev/academia_mcp
  [mle_kit_mcp]=https://github.com/IlyaGusev/mle_kit_mcp
  [codearkt]=https://github.com/IlyaGusev/codearkt
  [holosophos]=https://github.com/IlyaGusev/holosophos
)

show_status=false
if [[ "${1:-}" == "--status" ]]; then
  show_status=true
fi

update_repo() {
  local dir="$1" url="$2"
  if [[ ! -d "$dir/.git" ]]; then
    echo "[clone] $dir ← $url"
    git clone "$url" "$dir"
    return
  fi

  echo "[update] $dir"
  (
    cd "$dir"
    # Ensure origin points to the expected URL
    git remote set-url origin "$url" || true
    git fetch --prune origin

    # Determine branch: prefer current; fallback to origin/HEAD; fallback main
    branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '')"
    if [[ "$branch" == "HEAD" || -z "$branch" ]]; then
      branch="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null | sed 's|^origin/||' || echo main)"
      git checkout -q "$branch" || true
    fi

    # If remote branch missing, fallback to origin/HEAD
    if ! git rev-parse --verify --quiet "origin/$branch" >/dev/null; then
      branch="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null | sed 's|^origin/||' || echo main)"
      git checkout -q "$branch" || true
    fi

    # Fast-forward update
    git pull --ff-only origin "$branch" || {
      echo "Fast-forward failed for $dir:$branch. Resolve manually (rebase/merge) and rerun."
      exit 1
    }

    if $show_status; then
      git status -sb
    fi
  )
}

for name in "${!REPOS[@]}"; do
  update_repo "$name" "${REPOS[$name]}"
done

echo "Done. Repos are synced in: $SCRIPT_DIR"

