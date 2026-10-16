#!/usr/bin/env bash
# Generate credentials for the app-level team login
# (docs/plans/2026-07-05-ui-login-and-per-account-queue.md). Prints env lines you
# paste into a gitignored .env; nothing is written for you.
#
#   scripts/gen_auth_secret.sh                     # a HOLO_AUTH_SECRET only
#   scripts/gen_auth_secret.sh a.stepin m.mozikov  # + a HOLO_AUTH_USERS line
#                                                    with a strong password each
#
# Passwords use a base62 charset (no ':' or ',' so they never collide with the
# HOLO_AUTH_USERS "user:pass,user:pass" delimiters).
set -euo pipefail

gen_pw() {
  python3 -c "import secrets,string; print(''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(24)))"
}

echo "HOLO_AUTH_SECRET=$(python3 -c 'import secrets; print(secrets.token_hex(32))')"

if [ "$#" -gt 0 ]; then
  entries=()
  for user in "$@"; do
    entries+=("${user}:$(gen_pw)")
  done
  IFS=,
  echo "HOLO_AUTH_USERS=${entries[*]}"
fi
