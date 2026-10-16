#!/usr/bin/env bash
#
# Mechanical docs gate for Holosophus.
#
# This is the cheap, deterministic floor that complements the human/agent
# `docs-review` audit. It checks three things only:
#   1. No leaked template placeholders ([TBD], [verify command], [FIXME], XXX)
#      in docs/ or CLAUDE.md.
#   2. The canonical docs all exist (the doc map in docs/index.md and CLAUDE.md
#      points at them; a dangling pointer is a bug).
#   3. No trailing whitespace in docs/*.md.
#
# Run from anywhere — paths resolve relative to the repo root. Exit non-zero
# with a clear message on any failure; print "OK — docs checks passed" otherwise.
#
# Note: docs/templates/* are exempt from the placeholder scan; they hold
# fill-in slots by design.

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

fail=0

# --- 1. Leaked-placeholder scan -------------------------------------------
# Unfilled template slots must never reach a shipped doc. Templates are exempt.
placeholder_pattern='\[TBD\]|\[verify command\]|\[FIXME\]|XXX'
if matches="$(grep -rnE "$placeholder_pattern" \
      --include='*.md' --exclude-dir='templates' docs CLAUDE.md 2>/dev/null)"; then
  echo "FAIL: leaked template placeholder(s) — replace with the real value:" >&2
  echo "$matches" >&2
  fail=1
fi

# --- 2. Canonical-doc existence guard -------------------------------------
# These are the docs the map (docs/index.md, CLAUDE.md) treats as load-bearing.
# Templates (docs/templates/*) are intentionally NOT listed.
canonical_docs=(
  "CLAUDE.md"
  "docs/index.md"
  "docs/README.md"
  "docs/architecture.md"
  "docs/data-topology.md"
  "docs/observability.md"
  "docs/agent_rules/anti-patterns.md"
  "docs/agent_rules/backend.md"
  "docs/agent_rules/testing.md"
)
for doc in "${canonical_docs[@]}"; do
  if [[ ! -f "$doc" ]]; then
    echo "FAIL: missing canonical doc: $doc" >&2
    fail=1
  fi
done

# --- 3. Trailing-whitespace scan in docs/*.md -----------------------------
if ws="$(grep -rnE ' +$' --include='*.md' docs 2>/dev/null)"; then
  echo "FAIL: trailing whitespace in docs/*.md — strip it:" >&2
  echo "$ws" >&2
  fail=1
fi

if [[ "$fail" -ne 0 ]]; then
  echo "check-docs: FAILED (see messages above)." >&2
  exit 1
fi

echo "OK — docs checks passed"
