#!/usr/bin/env bash
# Build the pyjnius Android wheel.
#
# Simpler than the Kivy recipe: pyjnius has no native dependency to build
# first, ships no Java, and needs no pre-cythonize step — its setup.py
# cythonizes normally on Android.
#
# Source is a git checkout pinned to an exact commit in
# recipes/PINNED_REFS.toml. The Android wheel work is not upstream yet (see the
# note on [android.pyjnius] there), so this builds from a fork.
#
# Usage:
#   recipes/android/pyjnius.sh <abi> [outdir]
#
# Requires: ANDROID_NDK_HOME (r27), a host python with pip.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
source "$HERE/../lib/pins.sh"

ABI="${1:?usage: pyjnius.sh <abi> [outdir]   (arm64-v8a | x86_64)}"
BUILD_ROOT="${2:-$REPO_ROOT/.build/android}"

case "$ABI" in
  arm64-v8a) CIBW_ABI="arm64_v8a" ;;
  x86_64)    CIBW_ABI="x86_64" ;;
  *) echo "pyjnius: unsupported ABI '$ABI'" >&2; exit 1 ;;
esac

: "${ANDROID_NDK_HOME:?set ANDROID_NDK_HOME to the r27 NDK}"

SRC="$BUILD_ROOT/src/pyjnius"
WHEELHOUSE="$BUILD_ROOT/wheelhouse"
mkdir -p "$(dirname "$SRC")" "$WHEELHOUSE"

# Exact commit, no branch fallback — see recipes/lib/pins.sh.
echo "==> checking out pinned pyjnius"
clone_pinned android.pyjnius "$SRC"

# Same trap as the Kivy build: cibuildwheel's cross-build interpreter is
# Android CPython, so anything shelling out during the build looks for
# Android's shell path on this Linux host.
[[ -e /system/bin/sh ]] || {
  echo "pyjnius: /system/bin/sh is missing. Create it once:" >&2
  echo "  sudo mkdir -p /system/bin && sudo ln -s /bin/sh /system/bin/sh" >&2
  exit 1
}

export CIBW_PLATFORM=android
export CIBW_BUILD_FRONTEND=build
export CIBW_BUILD="cp314-android_${CIBW_ABI}"
export CIBW_ENVIRONMENT_ANDROID="ANDROID_API_LEVEL=24"

# pyjnius bundles no third-party shared libraries, so there is nothing for
# auditwheel to graft — but leaving repair enabled would still let it rename
# and rewrite the extension. Disabled for the same reason as the Kivy recipe:
# Android resolves libraries by exact filename.
export CIBW_REPAIR_WHEEL_COMMAND_ANDROID=""

echo "==> cibuildwheel ($CIBW_BUILD)"
python3 -m pip install -q cibuildwheel
RAW="$BUILD_ROOT/raw-pyjnius-$ABI"
rm -rf "$RAW"; mkdir -p "$RAW"
python3 -m cibuildwheel --output-dir "$RAW" "$SRC"

wheel="$(ls "$RAW"/*.whl | head -1)"
[[ -n "$wheel" ]] || { echo "pyjnius: cibuildwheel produced no wheel" >&2; exit 1; }

# Nothing to graft, but the wheel still gets the 16 KB alignment check — "-"
# means grafting is skipped, verification is not.
echo "==> verifying $(basename "$wheel")"
python3 "$HERE/lib/graft_libs.py" "$wheel" - "$WHEELHOUSE"

echo
echo "Wheel: $(ls "$WHEELHOUSE"/pyjnius*"$CIBW_ABI"*.whl)"
