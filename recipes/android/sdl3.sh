#!/usr/bin/env bash
# Assemble the SDL3 family for Android.
#
# Unlike SDL2, SDL3 publishes prebuilt Android binaries — but only three of the
# four are usable. Measured p_align of the published .aar contents:
#
#   SDL3 3.4.12        0x4000   ok, used as published
#   SDL3_image 3.4.4   0x4000   ok, used as published
#   SDL3_mixer 3.2.4   0x4000   ok, used as published
#   SDL3_ttf 3.2.2     0x1000   NOT 16 KB -> built from source here
#
# SDL3_ttf is still on the 3.2.x line, which predates the 16 KB page-size
# requirement, and 3.2.2 is its newest release. Shipping it as published gives
# a wheel that builds, passes every other check, and fails to load on any
# Android 15/16 device with 16 KB pages. See SDL3-FINDINGS.md.
#
# THIS WORKAROUND IS EXPECTED TO EXPIRE — upstream already fixed it.
#
# It is a known bug, libsdl-org/SDL_ttf#621, filed and closed on 2026-04-09.
# A maintainer confirmed "the 3.2.2 release binaries (Mar 31, 2025) do not have
# this" and landed the fix on BOTH lines the same day:
#
#   3.4.0   cb303d4eb7838122b04b2d6f41c45faff0598374   (2025-11-06)
#   3.2.x   d030c50a0a8cc5792ab8d18d86d48b21b4e5c170   (2025-11-06)
#
# So the fault is in how that one .aar was linked, not in SDL3_ttf — building
# the same source with -Wl,-z,max-page-size=16384 gives a correctly aligned
# library, which is what happens below. Upstream's own suggested interim is
# `build-scripts/build-release.py --actions android`; this recipe does the
# equivalent with cmake directly, to fit the prefix layout the rest of the
# family already uses.
#
# The trigger to remove this is therefore ANY SDL_ttf release newer than
# 3.2.2 — the fix is on the 3.2 maintenance branch as well as the development
# one, so a 3.2.4 would carry it just as a 3.4.0 would. When one appears,
# re-measure before anything else:
#
#   python3 recipes/android/lib/unwrap_sdl3_aar.py <zip> <sha256> /tmp/probe x86_64
#
# If it reports 0x4000, delete the from-source build below and let ttf be
# extracted like its three siblings. Do not carry this step longer than the
# problem it exists for.
#
# Usage:
#   recipes/android/sdl3.sh <abi> [outdir]      # arm64-v8a | x86_64
#
# Requires: ANDROID_NDK_HOME (r27), cmake, ninja.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"

ABI="${1:?usage: sdl3.sh <abi> [outdir]   (arm64-v8a | x86_64)}"
BUILD_ROOT="${2:-$REPO_ROOT/.build/android}"

case "$ABI" in
  arm64-v8a|x86_64) ;;
  *) echo "sdl3: unsupported ABI '$ABI' (64-bit only)" >&2; exit 1 ;;
esac

: "${ANDROID_NDK_HOME:?set ANDROID_NDK_HOME to the r27 NDK}"
TOOLCHAIN="$ANDROID_NDK_HOME/build/cmake/android.toolchain.cmake"
[[ -f "$TOOLCHAIN" ]] || { echo "sdl3: no toolchain at $TOOLCHAIN" >&2; exit 1; }

SDL3_VERSION="3.4.12"
SDL3_IMAGE_VERSION="3.4.4"
SDL3_MIXER_VERSION="3.2.4"
SDL3_TTF_VERSION="3.2.2"

SDL3_SHA256="21e60b78542c05afb81df015d0f8f8cb30e57fcafebaf2dbc38cb62ba92d6198"
SDL3_IMAGE_SHA256="a0d1e27d4529c2bbc3e4ac72d2e6c0dd474b32cfe2586d22c4228a34224d1f57"
SDL3_MIXER_SHA256="b7f9f1ee5162a0492e71941f2cbdc50ec256ef15c0e995ca72c7e519eebd810d"
# Downloaded for its Java glue and headers only; its libraries are rejected.
SDL3_TTF_SHA256="48e534daf88eec5c7f0c95fa6ca0e8a5f38f5fc7276824573131cebc403ee14c"

SRC="$BUILD_ROOT/src"
BUILD="$BUILD_ROOT/build"
PREFIX="$BUILD_ROOT/sdl3/$ABI"
SYSROOT="$ANDROID_NDK_HOME/toolchains/llvm/prebuilt/linux-x86_64/sysroot"
PAGE_FLAGS="-Wl,-z,max-page-size=16384"
mkdir -p "$SRC" "$BUILD" "$PREFIX"

fetch() {  # fetch <url> <file>
  [[ -f "$SRC/$2" ]] || { echo "==> fetching $2"; curl -fsSL -o "$SRC/$2" "$1"; }
}

BASE="https://github.com/libsdl-org"
fetch "$BASE/SDL/releases/download/release-$SDL3_VERSION/SDL3-devel-$SDL3_VERSION-android.zip" \
      "SDL3-android.zip"
fetch "$BASE/SDL_image/releases/download/release-$SDL3_IMAGE_VERSION/SDL3_image-devel-$SDL3_IMAGE_VERSION-android.zip" \
      "SDL3_image-android.zip"
fetch "$BASE/SDL_mixer/releases/download/release-$SDL3_MIXER_VERSION/SDL3_mixer-devel-$SDL3_MIXER_VERSION-android.zip" \
      "SDL3_mixer-android.zip"
fetch "$BASE/SDL_ttf/releases/download/release-$SDL3_TTF_VERSION/SDL3_ttf-devel-$SDL3_TTF_VERSION-android.zip" \
      "SDL3_ttf-android.zip"

echo "==> unwrapping published .aar packages ($ABI)"
python3 "$HERE/lib/unwrap_sdl3_aar.py" "$SRC/SDL3-android.zip"       "$SDL3_SHA256"       "$PREFIX" "$ABI"
python3 "$HERE/lib/unwrap_sdl3_aar.py" "$SRC/SDL3_image-android.zip" "$SDL3_IMAGE_SHA256" "$PREFIX" "$ABI"
python3 "$HERE/lib/unwrap_sdl3_aar.py" "$SRC/SDL3_mixer-android.zip" "$SDL3_MIXER_SHA256" "$PREFIX" "$ABI"

# ttf: take its headers and Java glue from the release, then discard and
# rebuild the library. Extracting into a scratch prefix keeps the rejected
# 4 KB binary from ever reaching $PREFIX.
TTF_STAGE="$BUILD/sdl3_ttf-published-$ABI"
rm -rf "$TTF_STAGE"
python3 "$HERE/lib/unwrap_sdl3_aar.py" "$SRC/SDL3_ttf-android.zip" "$SDL3_TTF_SHA256" "$TTF_STAGE" "$ABI"
cp -r "$TTF_STAGE/include/." "$PREFIX/include/" 2>/dev/null || true
[[ -d "$TTF_STAGE/java" ]] && cp -r "$TTF_STAGE/java/." "$PREFIX/java/" || true

# --- SDL3_ttf from source, 16 KB-aligned -------------------------------------
TTF_TARBALL="SDL3_ttf-$SDL3_TTF_VERSION.tar.gz"
fetch "$BASE/SDL_ttf/releases/download/release-$SDL3_TTF_VERSION/$TTF_TARBALL" "$TTF_TARBALL"
[[ -d "$SRC/SDL3_ttf-$SDL3_TTF_VERSION" ]] || tar -xzf "$SRC/$TTF_TARBALL" -C "$SRC"

# SDL3_ttf's release tarball ships an EMPTY external/ — its vendored freetype
# is a git submodule the tarball does not carry, so a VENDORED=ON build fails
# with "No freetype sources found". (SDL2_ttf did bundle freetype; SDL3_ttf
# does not.) The tree ships a fetch script for exactly this case.
TTF_SRC="$SRC/SDL3_ttf-$SDL3_TTF_VERSION"
if [[ ! -f "$TTF_SRC/external/freetype/CMakeLists.txt" ]]; then
  echo "==> fetching SDL3_ttf vendored dependencies"
  if [[ -x "$TTF_SRC/external/download.sh" ]]; then
    (cd "$TTF_SRC/external" && ./download.sh)
  else
    echo "sdl3: external/download.sh missing; cannot obtain freetype" >&2
    ls "$TTF_SRC/external" >&2 || true
    exit 1
  fi
fi

echo "==> building SDL3_ttf $SDL3_TTF_VERSION from source ($ABI)"
TTF_BUILD="$BUILD/SDL3_ttf-$ABI"
rm -rf "$TTF_BUILD"
cmake -S "$TTF_SRC" -B "$TTF_BUILD" -G Ninja \
  -DCMAKE_TOOLCHAIN_FILE="$TOOLCHAIN" \
  -DANDROID_ABI="$ABI" \
  -DANDROID_PLATFORM=android-24 \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="$PREFIX" \
  -DBUILD_SHARED_LIBS=ON \
  -DCMAKE_SHARED_LINKER_FLAGS="$PAGE_FLAGS" \
  -DCMAKE_MODULE_LINKER_FLAGS="$PAGE_FLAGS" \
  -DCMAKE_EXE_LINKER_FLAGS="$PAGE_FLAGS" \
  -DCMAKE_FIND_ROOT_PATH="$PREFIX;$SYSROOT" \
  -DCMAKE_FIND_ROOT_PATH_MODE_INCLUDE=BOTH \
  -DCMAKE_FIND_ROOT_PATH_MODE_LIBRARY=BOTH \
  -DCMAKE_FIND_ROOT_PATH_MODE_PACKAGE=BOTH \
  -DSDL3_DIR="$PREFIX/lib/cmake/SDL3" \
  -DSDLTTF_VENDORED=ON \
  -DSDLTTF_SAMPLES=OFF \
  -DSDLTTF_HARFBUZZ=OFF
cmake --build "$TTF_BUILD" --parallel
cmake --install "$TTF_BUILD"

# The install lands in lib/; mirror it into the per-ABI tree the rest of the
# family uses so the assembly step below sees one consistent layout.
mv -f "$PREFIX/lib/libSDL3_ttf.so" "$PREFIX/lib/$ABI/libSDL3_ttf.so"

# --- verify every library that will actually ship ----------------------------
echo "==> verifying 16 KB alignment ($ABI)"
fail=0
for so in "$PREFIX/lib/$ABI"/libSDL3*.so; do
  [[ -f "$so" ]] || continue
  align="$(readelf -lW "$so" | awk '/LOAD/ {print $NF; exit}')"
  if [[ "$align" != "0x4000" ]]; then
    echo "  FAIL $(basename "$so"): p_align=$align" >&2
    fail=1
  else
    echo "  ok   $(basename "$so"): p_align=$align"
  fi
done
[[ "$fail" -eq 0 ]] || { echo "sdl3: alignment check failed" >&2; exit 1; }

count="$(ls "$PREFIX/lib/$ABI"/libSDL3*.so | wc -l)"
[[ "$count" -eq 4 ]] || {
  echo "sdl3: expected 4 libraries, found $count" >&2
  ls "$PREFIX/lib/$ABI" >&2
  exit 1
}

echo
echo "SDL3 family for $ABI assembled at $PREFIX"
