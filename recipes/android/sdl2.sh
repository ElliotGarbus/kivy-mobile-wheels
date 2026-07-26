#!/usr/bin/env bash
# Build the SDL2 family for Android, and capture the matching Java glue.
#
# SDL2 publishes no Android binary package (only SDL3 does), so all four
# libraries are built from libsdl.org release source tarballs, verified by
# SHA-256.
#
# Two outputs, and both matter:
#   1. prefix/<abi>/ — libSDL2*.so + headers + pkgconfig, consumed by the Kivy
#      recipe and grafted into the Kivy wheel's flat .libs/.
#   2. sdl-glue/<version>/ — org/libsdl/app/*.java extracted from the SAME
#      tarball. kivyforge vendors this into its bootstrap, and SDLActivity
#      compares its compiled-in version against nativeGetVersion() at runtime.
#      On mismatch it aborts onCreate before creating the surface and logs
#      NOTHING: black screen, no SDL_main, no Python. Building both from one
#      verified tarball is what makes that impossible.
#
# Usage:
#   recipes/android/sdl2.sh <abi>          # arm64-v8a | x86_64
#   recipes/android/sdl2.sh <abi> <outdir>
#
# Requires: ANDROID_NDK_HOME (r27.3.13750724), cmake, ninja.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"

ABI="${1:?usage: sdl2.sh <abi> [outdir]   (arm64-v8a | x86_64)}"
BUILD_ROOT="${2:-$REPO_ROOT/.build/android}"

case "$ABI" in
  arm64-v8a|x86_64) ;;
  *) echo "sdl2: unsupported ABI '$ABI' (64-bit only: arm64-v8a, x86_64)" >&2
     exit 1 ;;
esac

: "${ANDROID_NDK_HOME:?set ANDROID_NDK_HOME to the r27 NDK}"
TOOLCHAIN="$ANDROID_NDK_HOME/build/cmake/android.toolchain.cmake"
[[ -f "$TOOLCHAIN" ]] || { echo "sdl2: no toolchain at $TOOLCHAIN" >&2; exit 1; }

ANDROID_PLATFORM="android-24"
# NDK r27 does NOT default to 16 KB alignment. Android 15/16 devices with
# 16 KB pages refuse to load a 4 KB-aligned .so, so this is passed explicitly
# on every linker flag variable. cibuildwheel passes it for extensions; a
# hand-built SDL2 gets it only from here.
PAGE_FLAGS="-Wl,-z,max-page-size=16384"

SDL2_VERSION="2.32.10"
SDL2_IMAGE_VERSION="2.8.12"
SDL2_MIXER_VERSION="2.8.2"
SDL2_TTF_VERSION="2.24.0"

SDL2_SHA256="5f5993c530f084535c65a6879e9b26ad441169b3e25d789d83287040a9ca5165"
SDL2_IMAGE_SHA256="393f5efb50536ec13ca4f4affb69cc9966d3c3f969e6c5e701faddf9f9785381"
SDL2_MIXER_SHA256="938dff531d00ace2296557a6599abe6f34599e2f34f0a4a08a397e2ccac8b8f7"
SDL2_TTF_SHA256="0b2bf1e7b6568adbdbc9bb924643f79d9dedafe061fa1ed687d1d9ac4e453bfd"

SRC="$BUILD_ROOT/src"
BUILD="$BUILD_ROOT/build"
PREFIX="$BUILD_ROOT/prefix/$ABI"
SYSROOT="$ANDROID_NDK_HOME/toolchains/llvm/prebuilt/linux-x86_64/sysroot"
mkdir -p "$SRC" "$BUILD" "$PREFIX"

fetch() {  # fetch <url> <file> <sha256>
  local url="$1" file="$2" want="$3"
  if [[ ! -f "$SRC/$file" ]]; then
    echo "==> fetching $file"
    curl -fsSL -o "$SRC/$file" "$url"
  fi
  local got
  got="$(sha256sum "$SRC/$file" | cut -d' ' -f1)"
  if [[ "$got" != "$want" ]]; then
    echo "sdl2: SHA-256 mismatch for $file" >&2
    echo "  expected $want" >&2
    echo "  got      $got" >&2
    exit 1
  fi
  local dir="${file%.tar.gz}"
  [[ -d "$SRC/$dir" ]] || tar -xzf "$SRC/$file" -C "$SRC"
}

fetch "https://github.com/libsdl-org/SDL/releases/download/release-$SDL2_VERSION/SDL2-$SDL2_VERSION.tar.gz" \
      "SDL2-$SDL2_VERSION.tar.gz" "$SDL2_SHA256"
fetch "https://github.com/libsdl-org/SDL_image/releases/download/release-$SDL2_IMAGE_VERSION/SDL2_image-$SDL2_IMAGE_VERSION.tar.gz" \
      "SDL2_image-$SDL2_IMAGE_VERSION.tar.gz" "$SDL2_IMAGE_SHA256"
fetch "https://github.com/libsdl-org/SDL_mixer/releases/download/release-$SDL2_MIXER_VERSION/SDL2_mixer-$SDL2_MIXER_VERSION.tar.gz" \
      "SDL2_mixer-$SDL2_MIXER_VERSION.tar.gz" "$SDL2_MIXER_SHA256"
fetch "https://github.com/libsdl-org/SDL_ttf/releases/download/release-$SDL2_TTF_VERSION/SDL2_ttf-$SDL2_TTF_VERSION.tar.gz" \
      "SDL2_ttf-$SDL2_TTF_VERSION.tar.gz" "$SDL2_TTF_SHA256"

# Common cmake arguments. The satellites need CMAKE_FIND_ROOT_PATH plus
# *_MODE_*=BOTH: the NDK toolchain confines find_package() to its sysroot, so
# without this they cannot see the SDL2 we just installed outside it.
common_args=(
  -G Ninja
  -DCMAKE_TOOLCHAIN_FILE="$TOOLCHAIN"
  -DANDROID_ABI="$ABI"
  -DANDROID_PLATFORM="$ANDROID_PLATFORM"
  -DCMAKE_BUILD_TYPE=Release
  -DCMAKE_INSTALL_PREFIX="$PREFIX"
  -DBUILD_SHARED_LIBS=ON
  -DCMAKE_SHARED_LINKER_FLAGS="$PAGE_FLAGS"
  -DCMAKE_MODULE_LINKER_FLAGS="$PAGE_FLAGS"
  -DCMAKE_EXE_LINKER_FLAGS="$PAGE_FLAGS"
)

satellite_args=(
  -DCMAKE_FIND_ROOT_PATH="$PREFIX;$SYSROOT"
  -DCMAKE_FIND_ROOT_PATH_MODE_INCLUDE=BOTH
  -DCMAKE_FIND_ROOT_PATH_MODE_LIBRARY=BOTH
  -DCMAKE_FIND_ROOT_PATH_MODE_PACKAGE=BOTH
  -DSDL2_LIBRARY="$PREFIX/lib/libSDL2.so"
  -DSDL2_INCLUDE_DIR="$PREFIX/include/SDL2"
)

build_one() {  # build_one <name> <srcdir> [extra cmake args...]
  local name="$1" srcdir="$2"; shift 2
  local bdir="$BUILD/$name-$ABI"
  echo "==> configuring $name ($ABI)"
  cmake -S "$SRC/$srcdir" -B "$bdir" "${common_args[@]}" "$@"
  echo "==> building $name ($ABI)"
  cmake --build "$bdir" --parallel
  cmake --install "$bdir"
}

build_one SDL2 "SDL2-$SDL2_VERSION" -DSDL_SHARED=ON -DSDL_STATIC=OFF

# The release tarballs ship an EMPTY external/ for image and mixer — their
# vendored deps are git submodules that the tarball does not carry. So
# VENDORED=OFF and the built-in decoders are used instead; anything needing an
# external library is disabled. SDL2_ttf does bundle freetype, hence
# VENDORED=ON there.
build_one SDL2_image "SDL2_image-$SDL2_IMAGE_VERSION" "${satellite_args[@]}" \
  -DSDL2IMAGE_VENDORED=OFF -DSDL2IMAGE_BACKEND_STB=ON \
  -DSDL2IMAGE_DEPS_SHARED=OFF -DSDL2IMAGE_SAMPLES=OFF -DSDL2IMAGE_TESTS=OFF \
  -DSDL2IMAGE_AVIF=OFF -DSDL2IMAGE_JXL=OFF -DSDL2IMAGE_TIF=OFF \
  -DSDL2IMAGE_WEBP=OFF

build_one SDL2_mixer "SDL2_mixer-$SDL2_MIXER_VERSION" "${satellite_args[@]}" \
  -DSDL2MIXER_VENDORED=OFF -DSDL2MIXER_DEPS_SHARED=OFF \
  -DSDL2MIXER_SAMPLES=OFF \
  -DSDL2MIXER_MP3=ON -DSDL2MIXER_MP3_MINIMP3=ON -DSDL2MIXER_MP3_MPG123=OFF \
  -DSDL2MIXER_FLAC=ON -DSDL2MIXER_FLAC_DRFLAC=ON -DSDL2MIXER_FLAC_LIBFLAC=OFF \
  -DSDL2MIXER_WAVE=ON -DSDL2MIXER_CMD=ON \
  -DSDL2MIXER_MIDI=OFF -DSDL2MIXER_MOD=OFF -DSDL2MIXER_OPUS=OFF \
  -DSDL2MIXER_GME=OFF -DSDL2MIXER_WAVPACK=OFF

build_one SDL2_ttf "SDL2_ttf-$SDL2_TTF_VERSION" "${satellite_args[@]}" \
  -DSDL2TTF_VENDORED=ON -DSDL2TTF_HARFBUZZ=OFF -DSDL2TTF_SAMPLES=OFF

# --- verification: 16 KB alignment on every shipped library ------------------
echo "==> verifying 16 KB alignment ($ABI)"
fail=0
for so in "$PREFIX"/lib/libSDL2*.so; do
  [[ -f "$so" ]] || continue
  align="$(readelf -lW "$so" | awk '/LOAD/ {print $NF; exit}')"
  if [[ "$align" != "0x4000" ]]; then
    echo "  FAIL $(basename "$so"): p_align=$align (want 0x4000)" >&2
    fail=1
  else
    echo "  ok   $(basename "$so"): p_align=$align"
  fi
done
[[ "$fail" -eq 0 ]] || { echo "sdl2: alignment check failed" >&2; exit 1; }

# --- the Java glue, from the same verified tarball ---------------------------
GLUE_DEST="$HERE/sdl-glue/$SDL2_VERSION"
echo "==> capturing Java glue -> ${GLUE_DEST#"$REPO_ROOT/"}"
mkdir -p "$GLUE_DEST/org/libsdl/app"
cp "$SRC/SDL2-$SDL2_VERSION"/android-project/app/src/main/java/org/libsdl/app/*.java \
   "$GLUE_DEST/org/libsdl/app/"
cp "$SRC/SDL2-$SDL2_VERSION/LICENSE.txt" "$GLUE_DEST/LICENSE-SDL.txt"
grep -E '#define SDL_(MAJOR_VERSION|MINOR_VERSION|PATCHLEVEL)' \
  "$SRC/SDL2-$SDL2_VERSION/include/SDL_version.h" > "$GLUE_DEST/SDL_REVISION.txt"

echo
echo "SDL2 $SDL2_VERSION for $ABI installed to $PREFIX"
echo "Glue captured at ${GLUE_DEST#"$REPO_ROOT/"} (must match the shipped libSDL2.so)"
