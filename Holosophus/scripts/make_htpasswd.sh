#!/usr/bin/env bash
# Generate/append entries to the gitignored docker/.htpasswd used by the
# authenticated deploy overlay (docker-compose.auth.yml). This IS the predefined
# team login/password list that nginx Basic auth checks.
#
#   scripts/make_htpasswd.sh alice            # prompt for password (hidden input)
#   scripts/make_htpasswd.sh alice s3cret     # non-interactive
#
# First user creates the file; subsequent users append. Uses bcrypt (-B) when
# available. Never commit the resulting file (it is gitignored).
set -euo pipefail

FILE="${HOLO_HTPASSWD_PATH:-docker/.htpasswd}"
user="${1:?usage: scripts/make_htpasswd.sh <user> [password]}"
pass="${2:-}"

mkdir -p "$(dirname "$FILE")"
create_flag=""
[ -f "$FILE" ] || create_flag="-c"

if command -v htpasswd >/dev/null 2>&1; then
  if [ -n "$pass" ]; then
    htpasswd -B $create_flag -b "$FILE" "$user" "$pass"
  else
    htpasswd -B $create_flag "$FILE" "$user"
  fi
else
  # No host htpasswd -> generate the hash inside the httpd image.
  if [ -z "$pass" ]; then
    read -r -s -p "Password for $user: " pass
    echo
  fi
  line="$(docker run --rm httpd:2.4-alpine htpasswd -nbB "$user" "$pass")"
  [ -n "$create_flag" ] && : > "$FILE"
  printf '%s\n' "$line" >> "$FILE"
fi

echo "Wrote entry for '$user' -> $FILE"
