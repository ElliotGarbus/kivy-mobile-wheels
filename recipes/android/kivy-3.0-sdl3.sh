#!/usr/bin/env bash
# Build the Kivy 3.0 Android wheel against the SDL3 family from sdl3.sh.
#
# Run recipes/android/sdl3.sh for the same ABI first.
#
# Differs from the 2.3.1 recipe in three ways worth knowing:
#
#   * No pre-cythonize step. Kivy 3 imports Cython unconditionally and
#     cythonizes during the build; can_use_cython — the switch that disabled
#     it on Android in 2.3.1 — no longer exists. Cython just has to be present
#     in cibuildwheel's cross-venv, which CIBW_BEFORE_BUILD handles.
#   * No USE_SDL2/KIVY_SDL2_PATH dance. `platform == 'android'` forces
#     use_sdl3 = True, and there is no pkg-config probe to defeat.
#   * The libraries go where Kivy looks rather than where we say. Its Android
#     branch resolves them from os.getcwd() — `<cwd>/dist/libs/<abi>` and
#     `<cwd>/dist/include` — with no KIVY_DEPS_ROOT escape hatch, so the tree
#     is assembled inside the Kivy source before the build.
#
# Usage:
#   recipes/android/kivy-3.0-sdl3.sh <abi> [outdir]

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
source "$HERE/../lib/pins.sh"

ABI="${1:?usage: kivy-3.0-sdl3.sh <abi> [outdir]   (arm64-v8a | x86_64)}"
BUILD_ROOT="${2:-$REPO_ROOT/.build/android}"

case "$ABI" in
  arm64-v8a) CIBW_ABI="arm64_v8a" ;;
  x86_64)    CIBW_ABI="x86_64" ;;
  *) echo "kivy3: unsupported ABI '$ABI'" >&2; exit 1 ;;
esac

: "${ANDROID_NDK_HOME:?set ANDROID_NDK_HOME to the r27 NDK}"

SRC="$BUILD_ROOT/src"
SDL3_PREFIX="$BUILD_ROOT/sdl3/$ABI"
WHEELHOUSE="$BUILD_ROOT/wheelhouse"
KIVY_SRC="$SRC/kivy3"
mkdir -p "$SRC" "$WHEELHOUSE"

[[ -f "$SDL3_PREFIX/libs/$ABI/libSDL3.so" ]] || {
  echo "kivy3: no SDL3 at $SDL3_PREFIX — run recipes/android/sdl3.sh $ABI first" >&2
  exit 1
}

# Same trap as every other Android wheel build: cibuildwheel's cross-build
# interpreter is Android CPython, so anything shelling out during the build
# looks for Android's shell path on this Linux host.
[[ -e /system/bin/sh ]] || {
  echo "kivy3: /system/bin/sh is missing. Create it once:" >&2
  echo "  sudo mkdir -p /system/bin && sudo ln -s /bin/sh /system/bin/sh" >&2
  exit 1
}

echo "==> checking out pinned Kivy 3"
clone_pinned android.kivy "$KIVY_SRC"
# A checkout reused across ABIs would carry the previous ABI's dist/ tree and
# its cythonized output, silently linking the wrong libraries.
git -C "$KIVY_SRC" clean -xfd >/dev/null

# --- assemble the tree Kivy's setup.py expects -------------------------------
echo "==> staging SDL3 into <kivy-src>/dist"
rm -rf "$KIVY_SRC/dist"
mkdir -p "$KIVY_SRC/dist/libs/$ABI" "$KIVY_SRC/dist/include"
cp "$SDL3_PREFIX/libs/$ABI"/libSDL3*.so "$KIVY_SRC/dist/libs/$ABI/"
cp -r "$SDL3_PREFIX/include/." "$KIVY_SRC/dist/include/"
ls "$KIVY_SRC/dist/libs/$ABI"

# --- cibuildwheel -------------------------------------------------------------
export CIBW_PLATFORM=android
export CIBW_BUILD_FRONTEND=build
# cp314 is a prerelease to cibuildwheel; without this the selector matches
# nothing and it exits having built zero wheels.
export CIBW_ENABLE=cpython-prerelease
export CIBW_ARCHS="$CIBW_ABI"
export CIBW_BUILD="cp314-android_${CIBW_ABI}"

# cibuildwheel invokes the build frontend with --no-isolation
# --skip-dependency-check, so [build-system].requires is never installed.
# Kivy 3 imports Cython at setup.py top level, so without this the build dies
# immediately — the same gap that broke pyjnius.
export CIBW_BEFORE_BUILD_ANDROID="pip install 'cython>=0.29.1,<=3.2.0' \
'setuptools~=82.0.0' 'wheel~=0.47.0' 'packaging~=26.0'"

export CIBW_ENVIRONMENT_ANDROID="KIVY_CROSS_PLATFORM=android \
KIVY_SPLIT_EXAMPLES=1 ANDROID_API_LEVEL=24"

# auditwheel renames grafted libraries with a hash suffix, and Android's
# System.loadLibrary resolves the exact filename. The SDL3 family is grafted
# below instead, sonames untouched.
export CIBW_REPAIR_WHEEL_COMMAND_ANDROID=""

echo "==> cibuildwheel ($CIBW_BUILD)"
python3 -m pip install -q cibuildwheel
RAW="$BUILD_ROOT/raw-kivy3-$ABI"
rm -rf "$RAW"; mkdir -p "$RAW"
python3 -m cibuildwheel --output-dir "$RAW" "$KIVY_SRC"

wheel="$(ls "$RAW"/*.whl | head -1)"
[[ -n "$wheel" ]] || { echo "kivy3: cibuildwheel produced no wheel" >&2; exit 1; }

echo "==> grafting SDL3 into $(basename "$wheel")"
python3 "$HERE/lib/graft_libs.py" "$wheel" "$SDL3_PREFIX/libs/$ABI" "$WHEELHOUSE"

echo
echo "Wheel: $(ls "$WHEELHOUSE"/[Kk]ivy*"$CIBW_ABI"*.whl)"
