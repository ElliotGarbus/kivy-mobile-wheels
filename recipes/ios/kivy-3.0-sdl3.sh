#!/usr/bin/env bash
# Build Kivy cp315 iOS wheels (3 slices), from the pinned commit.
#
# Ported from kivyforge's scripts/build_ios_wheels.sh, which produced the
# wheels currently validated on the iOS simulator (see kivyforge
# docs/design/dev/ios-validation-findings.md). Mirrors kivy/kivy
# .github/workflows/ios_wheels.yml.
#
# Usage:
#   recipes/ios/kivy-3.0-sdl3.sh [OUTPUT_DIR]
#
# Environment:
#   IOS_DEPLOYMENT_TARGET  Minimum iOS version to link against (default: 16.0,
#                          which Kivy's xcframeworks require). Passed to
#                          cibuildwheel as CIBW_ENVIRONMENT_IOS, because iOS
#                          builds run in an isolated cross-venv and exporting it
#                          in this shell would not reach the compiler.
#
#                          It does NOT change the wheel's platform tag. That
#                          comes from the interpreter — python.org's CPython iOS
#                          framework is built for 13.0 — so these wheels are
#                          tagged ios_13_0_* even at 16.0, and that is correct:
#                          a lower tag installs into any project with a 16.0
#                          target. recipes/ios/lib/check_min_os.py reads the
#                          Mach-O load commands, which are the only place the
#                          real minimum is visible.
#
# The commit built is read from PINNED_REFS.toml's [ios.kivy] — there is no
# branch-name fallback; a missing pin fails the build.
#
# Prerequisites: macOS, Xcode, network. Host Python 3.x with pip.
# Python 3.15 iOS wheels require cibuildwheel's cpython-prerelease enablement.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
source "$ROOT/recipes/lib/pins.sh"

OUTPUT_DIR="${1:-$ROOT/dist/ios}"
BUILD_ROOT="${BUILD_ROOT:-$ROOT/.build/ios-wheels}"
KIVY_SRC="${KIVY_SRC:-$BUILD_ROOT/kivy}"
IOS_DEPLOYMENT_TARGET="${IOS_DEPLOYMENT_TARGET:-16.0}"

mkdir -p "$OUTPUT_DIR" "$BUILD_ROOT"

CPYTHON_PKG_ID="org.python.Python.PythonFramework-3.15"
CPYTHON_FRAMEWORK="/Library/Frameworks/Python.framework/Versions/3.15"
CPYTHON_PKG_URL="https://www.python.org/ftp/python/3.15.0/python-3.15.0b4-macos11.pkg"
CPYTHON_PKG_CACHE="${BUILD_ROOT}/python-3.15.0b4-macos11.pkg"

if ! pkgutil --pkgs 2>/dev/null | grep -qx "$CPYTHON_PKG_ID"; then
  if [[ ! -d "$CPYTHON_FRAMEWORK" ]]; then
    echo "CPython 3.15 macOS framework is required to cross-build iOS wheels."
    echo "cibuildwheel refuses to sudo-install outside CI."
    if [[ ! -f "$CPYTHON_PKG_CACHE" ]]; then
      echo "Downloading installer to $CPYTHON_PKG_CACHE ..."
      curl -fsSL -o "$CPYTHON_PKG_CACHE" "$CPYTHON_PKG_URL"
    fi
    echo ""
    echo "Install once, then re-run this script:"
    echo "  sudo installer -pkg \"$CPYTHON_PKG_CACHE\" -target /"
    echo ""
    exit 1
  fi
fi

if ! xcrun -find metal >/dev/null 2>&1; then
  echo "Metal toolchain missing; downloading via xcodebuild ..."
  xcodebuild -downloadComponent MetalToolchain
fi

clone_pinned ios.kivy "$KIVY_SRC"
echo "Kivy checkout at: $(git -C "$KIVY_SRC" log -1 --oneline)"

# Upstream's version is the literal 3.0.0.dev0 on every commit, which would make
# every build of every commit the same wheel filename. See stamp_kivy_version.py.
echo "Stamping the version from the pinned commit ..."
python3 "$ROOT/recipes/lib/stamp_kivy_version.py" "$KIVY_SRC"

echo "Installing cibuildwheel build deps ..."
python3 -m pip install -q -r "$KIVY_SRC/.ci/cicd-requirements.txt"
# Kivy's pin (~=3.4) predates cp315 iOS; 4.0rc+ is required for Python 3.15 wheels.
python3 -m pip install -q 'cibuildwheel>=4.0.0rc2'
python3 -m pip install -q meson ninja

pushd "$KIVY_SRC" >/dev/null

DEPS_MARKER="ios-kivy-dependencies/dist/Frameworks/SDL3.xcframework/Info.plist"
if [[ ! -f "$DEPS_MARKER" ]]; then
  echo "Building iOS native dependencies (SDL3, ANGLE, ThorVG) — this takes a while ..."
  rm -rf ios-kivy-dependencies
  ./tools/build_ios_dependencies.sh
else
  echo "Reusing cached ios-kivy-dependencies in $KIVY_SRC"
fi

export KIVY_DEPS_ROOT="$(pwd)/ios-kivy-dependencies"
export KIVY_SPLIT_EXAMPLES=1
export CIBW_PLATFORM=ios
export CIBW_ARCHS="arm64_iphoneos arm64_iphonesimulator x86_64_iphonesimulator"
export CIBW_ENABLE=cpython-prerelease
export CIBW_BUILD="cp315-*"
# Kivy's SDL3/ANGLE/ThorVG xcframeworks target iOS 16+, so the extensions have
# to be linked for 16.0 too. cibuildwheel passes IPHONEOS_DEPLOYMENT_TARGET
# through to the compiler, but only from the build environment it constructs —
# iOS builds run in an isolated cross-venv, so exporting it here would be
# dropped. Injecting it via CIBW_ENVIRONMENT_IOS survives (cibuildwheel applies
# CIBW_ENVIRONMENT first and then `setdefault`s 13.0, so an explicit value wins).
# The resulting wheel is still tagged ios_13_0_*; see the header.
export CIBW_ENVIRONMENT_IOS="IPHONEOS_DEPLOYMENT_TARGET=$IOS_DEPLOYMENT_TARGET"

WHEELHOUSE="$(mktemp -d "$BUILD_ROOT/kivy-wheelhouse.XXXXXX")"
echo "Running cibuildwheel (IPHONEOS_DEPLOYMENT_TARGET=$IOS_DEPLOYMENT_TARGET via CIBW_ENVIRONMENT_IOS, output -> $WHEELHOUSE) ..."
python3 -m cibuildwheel --output-dir "$WHEELHOUSE"

echo "Patching wheels with bundled iOS frameworks ..."
python3 ./tools/add-ios-frameworks.py "$WHEELHOUSE"

popd >/dev/null

echo "Copying wheels to $OUTPUT_DIR ..."
cp -v "$WHEELHOUSE"/*.whl "$OUTPUT_DIR/"
rm -rf "$WHEELHOUSE"

echo "Done. Built wheels:"
ls -1 "$OUTPUT_DIR"/*.whl
