#!/usr/bin/env bash
# Build the Kivy 2.3.1 Android wheel against the SDL2 line from sdl2.sh.
#
# Run recipes/android/sdl2.sh for the same ABI first — this consumes its
# prefix, and grafts its four libraries into the wheel.
#
# Usage:
#   recipes/android/kivy-2.3.1-sdl2.sh <abi> [outdir]
#
# Requires: ANDROID_NDK_HOME (r27), a host python with pip, and
# /system/bin/sh (see the note below).

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"

ABI="${1:?usage: kivy-2.3.1-sdl2.sh <abi> [outdir]   (arm64-v8a | x86_64)}"
BUILD_ROOT="${2:-$REPO_ROOT/.build/android}"

case "$ABI" in
  arm64-v8a) CIBW_ABI="arm64_v8a" ;;
  x86_64)    CIBW_ABI="x86_64" ;;
  *) echo "kivy: unsupported ABI '$ABI'" >&2; exit 1 ;;
esac

: "${ANDROID_NDK_HOME:?set ANDROID_NDK_HOME to the r27 NDK}"

KIVY_VERSION="2.3.1"
KIVY_SDIST_SHA256="0833949e3502cdb4abcf9c1da4384674045ad7d85644313aa1ee7573f3b4f9d9"

SRC="$BUILD_ROOT/src"
PREFIX="$BUILD_ROOT/prefix/$ABI"
WHEELHOUSE="$BUILD_ROOT/wheelhouse"
KIVY_SRC="$SRC/Kivy-$KIVY_VERSION"
mkdir -p "$SRC" "$WHEELHOUSE"

[[ -f "$PREFIX/lib/libSDL2.so" ]] || {
  echo "kivy: no SDL2 at $PREFIX — run recipes/android/sdl2.sh $ABI first" >&2
  exit 1
}

# CPython selects the shell for subprocess(..., shell=True) via
# hasattr(sys, "getandroidapilevel"), and cibuildwheel's cross-build
# interpreter IS Android CPython — so Kivy's pkgconfig() helper, which shells
# out at the first build hook, looks for Android's shell path on this Linux
# host. Affects any package that shells out during an Android wheel build.
[[ -e /system/bin/sh ]] || {
  echo "kivy: /system/bin/sh is missing. Create it once:" >&2
  echo "  sudo mkdir -p /system/bin && sudo ln -s /bin/sh /system/bin/sh" >&2
  exit 1
}

# --- source, from PyPI, hash-verified ---------------------------------------
TARBALL="$SRC/Kivy-$KIVY_VERSION.tar.gz"
if [[ ! -f "$TARBALL" ]]; then
  echo "==> fetching Kivy $KIVY_VERSION sdist"
  url="$(python3 -c "
import json,urllib.request
with urllib.request.urlopen('https://pypi.org/pypi/Kivy/$KIVY_VERSION/json') as r:
    data = json.load(r)
print(next(u['url'] for u in data['urls'] if u['packagetype'] == 'sdist'))
")"
  curl -fsSL -o "$TARBALL" "$url"
fi
got="$(sha256sum "$TARBALL" | cut -d' ' -f1)"
if [[ "$got" != "$KIVY_SDIST_SHA256" ]]; then
  echo "kivy: sdist SHA-256 mismatch" >&2
  echo "  expected $KIVY_SDIST_SHA256" >&2
  echo "  got      $got" >&2
  exit 1
fi
# Always start from a clean tree: cythonized .c and config.pxi from a previous
# ABI would otherwise be reused, silently baking the wrong configuration in.
rm -rf "$KIVY_SRC"
tar -xzf "$TARBALL" -C "$SRC"

# --- build environment -------------------------------------------------------
# KIVY_SDL2_PATH, not pkg-config: cibuildwheel overrides PKG_CONFIG_LIBDIR with
# its own Android-Python prefix (and routes through a relocating pkgconf-pypi
# wrapper), so Kivy's pkg-config probe can never see an externally built SDL2 —
# it reports use_sdl2 = 0 and then fails on 'SDL.h' file not found. Kivy's
# documented manual path takes a pathsep list used for BOTH include_dirs and
# library_dirs.
export KIVY_CROSS_PLATFORM=android
export USE_SDL2=1
export KIVY_SPLIT_EXAMPLES=1
export ANDROID_API_LEVEL=24
export KIVY_SDL2_PATH="$PREFIX/include/SDL2:$PREFIX/lib"
export NDKPLATFORM="$ANDROID_NDK_HOME/toolchains/llvm/prebuilt/linux-x86_64/sysroot"

echo "==> pre-cythonizing (Kivy sets can_use_cython=False on Android)"
python3 -m pip install -q "cython>=0.29.1,<=3.0.11" setuptools wheel cibuildwheel
python3 "$HERE/lib/kivy_precythonize.py" "$KIVY_SRC"

# --- cibuildwheel -------------------------------------------------------------
# Android does not support the pip build frontend.
export CIBW_PLATFORM=android
export CIBW_BUILD_FRONTEND=build
export CIBW_ENABLE=cpython-prerelease

# Ask cibuildwheel what it calls this ABI rather than guessing. A hardcoded
# "cp314-android_arm64_v8a" selected nothing while the x86_64 spelling worked,
# and cibuildwheel's response to an unmatched pattern is to build zero wheels
# and exit — a failure that reads like a configuration error anywhere but here.
echo "==> resolving the cibuildwheel build identifier"

# Keep stderr: if cibuildwheel cannot enumerate, its reason is the diagnosis.
ALL_IDS="$(python3 -m cibuildwheel --platform android \
             --print-build-identifiers "$KIVY_SRC" || true)"
echo "  cibuildwheel offers: ${ALL_IDS:-<none>}"
# `|| true` is load-bearing: grep exits 1 on no match, and under `set -e` with
# pipefail that status kills the script at the assignment — before the
# diagnostic below can report what went wrong.
BUILD_ID="$(printf '%s\n' "$ALL_IDS" | grep -E "^cp314-.*${CIBW_ABI}\$" | head -1 || true)"
if [[ -z "$BUILD_ID" ]]; then
  echo "kivy: no cp314 build identifier matching '$CIBW_ABI'." >&2
  echo "  cibuildwheel offers:" >&2
  printf '%s\n' "$ALL_IDS" | sed 's/^/    /' >&2
  exit 1
fi
echo "  $BUILD_ID"
export CIBW_BUILD="$BUILD_ID"
export CIBW_ENVIRONMENT_ANDROID="KIVY_CROSS_PLATFORM=android USE_SDL2=1 \
KIVY_SPLIT_EXAMPLES=1 ANDROID_API_LEVEL=24 \
KIVY_SDL2_PATH=$PREFIX/include/SDL2:$PREFIX/lib \
NDKPLATFORM=$NDKPLATFORM"

# auditwheel grafts dependencies into <pkg>.libs/ and RENAMES them with a hash
# suffix (libSDL2-1664d2a2.so). Android's System.loadLibrary("SDL2") resolves
# the exact filename libSDL2.so, so a repaired wheel builds, passes every
# alignment check, and throws UnsatisfiedLinkError at app start. The SDL family
# is grafted below instead, with sonames untouched.
export CIBW_REPAIR_WHEEL_COMMAND_ANDROID=""

echo "==> cibuildwheel ($CIBW_BUILD)"

RAW="$BUILD_ROOT/raw-$ABI"
rm -rf "$RAW"; mkdir -p "$RAW"
python3 -m cibuildwheel --output-dir "$RAW" "$KIVY_SRC"

# --- graft the SDL family into a flat .libs/ ---------------------------------
wheel="$(ls "$RAW"/*.whl | head -1)"
[[ -n "$wheel" ]] || { echo "kivy: cibuildwheel produced no wheel" >&2; exit 1; }
echo "==> grafting SDL2 into $(basename "$wheel")"
python3 "$HERE/lib/graft_libs.py" "$wheel" "$PREFIX/lib" "$WHEELHOUSE"

echo
echo "Wheel: $(ls "$WHEELHOUSE"/Kivy-"$KIVY_VERSION"*"$CIBW_ABI"*.whl)"
