#!/usr/bin/env bash
# Read an exact commit out of recipes/PINNED_REFS.toml.
#
# Sourced by every recipe that builds from a git checkout. There is
# deliberately no branch-name fallback: a recipe that cannot find its pin must
# fail, not quietly build whatever HEAD happens to be today.

set -euo pipefail

PINS_FILE="${PINS_FILE:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/PINNED_REFS.toml}"
PINS_PYTHON="${PINS_PYTHON:-$(command -v python3 || command -v python || true)}"

# pin_get <section> <key>   e.g. pin_get android.pyjnius commit
#
# Errors are checked explicitly rather than left to `set -e`: errexit is
# disabled inside a function called in a `||` list or a command substitution,
# which is exactly how this gets called. Relying on it would let a missing pin
# return empty and succeed — the silent float this file exists to prevent.
pin_get() {
  local section="$1" key="$2"
  if [[ ! -f "$PINS_FILE" ]]; then
    echo "pins: $PINS_FILE not found" >&2
    return 1
  fi
  if [[ -z "$PINS_PYTHON" ]]; then
    echo "pins: no python3 on PATH; cannot read $PINS_FILE" >&2
    return 1
  fi
  local value
  if ! value="$("$PINS_PYTHON" - "$PINS_FILE" "$section" "$key" <<'PY'
import sys, tomllib
path, section, key = sys.argv[1], sys.argv[2], sys.argv[3]
with open(path, "rb") as fh:
    data = tomllib.load(fh)
node = data
for part in section.split("."):
    node = node.get(part)
    if node is None:
        sys.exit(f"pins: no section [{section}] in {path}")
value = node.get(key)
if not value:
    sys.exit(f"pins: [{section}] has no {key!r} in {path}")
print(value)
PY
  )"; then
    echo "pins: could not read [$section].$key from $PINS_FILE" >&2
    return 1
  fi
  if [[ -z "$value" ]]; then
    echo "pins: [$section].$key resolved empty in $PINS_FILE" >&2
    return 1
  fi
  printf '%s' "$value"
}

# clone_pinned <section> <dest>  — clone at the pinned commit, exactly.
clone_pinned() {
  local section="$1" dest="$2"
  local repo commit
  repo="$(pin_get "$section" repo)"
  commit="$(pin_get "$section" commit)"

  if [[ ! -d "$dest/.git" ]]; then
    git clone --filter=blob:none "$repo" "$dest"
  fi
  git -C "$dest" fetch origin "$commit" --depth 1 2>/dev/null || git -C "$dest" fetch origin
  git -C "$dest" checkout --detach "$commit"
  echo "[$section] $repo @ $commit"
}
