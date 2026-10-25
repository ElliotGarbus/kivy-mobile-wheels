#!/usr/bin/env bash
# Build pyobjus cp315 iOS wheels (3 slices), from the pinned commit.
#
# Ported from kivyforge's scripts/build_pyobjus_ios_wheels.sh.
#
# pyobjus is an optional Objective-C bridge for app code that calls iOS system
# frameworks (kivyforge's own platform shim uses ctypes and does not depend on
# it). It is a Cython extension that links libffi + the ObjC runtime.
#
# Mirrors kivy-3.0-sdl3.sh: cibuildwheel cross-builds the three iOS slices,
# against a libffi that pyobjus's own .ci/build_ios_dependencies.sh produces
# in ios-deps-install/. Unlike Kivy there is no bundled-framework step —
# pyobjus ships no xcframeworks.
#
# Usage:
#   recipes/ios/pyobjus.sh [OUTPUT_DIR]
#
# Environment:
#   IOS_DEPLOYMENT_TARGET  Minimum iOS version to link against (default: 16.0).
#                          The python.org cp315 framework targets iOS 13.0 and
#                          the wheel tag comes from the interpreter, so these
#                          wheels tag as ios_13_0_* regardless — that is fine, a
#                          lower tag is compatible with a 16.0 project. What was
#                          actually linked is checked by
#                          recipes/ios/lib/check_min_os.py.
#
# The commit built is read from PINNED_REFS.toml's [ios.pyobjus] — there is
# no branch-name fallback; a missing pin fails the build.
#
# NOTE: pyobjus has no iOS CI upstream, so this exact cibuildwheel-iOS path is
# not upstream-validated. setup.py gates the iOS branch on
# sys.platform == "ios" and picks the libffi slice by
# platform.ios_ver().is_simulator + ARCH; if a build fails, those are the
# first two things to check.
#
# Prerequisites (identical to kivy-3.0-sdl3.sh): macOS + Xcode + network, a
# host Python 3.x with pip, and the CPython 3.15.0 macOS framework installed —
# cibuildwheel drives the iOS cross-build with it.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
source "$ROOT/recipes/lib/pins.sh"

OUTPUT_DIR="${1:-$ROOT/dist/ios}"
BUILD_ROOT="${BUILD_ROOT:-$ROOT/.build/ios-wheels}"
PYOBJUS_SRC="${PYOBJUS_SRC:-$BUILD_ROOT/pyobjus}"
IOS_DEPLOYMENT_TARGET="${IOS_DEPLOYMENT_TARGET:-16.0}"

mkdir -p "$OUTPUT_DIR" "$BUILD_ROOT"

# --- CPython 3.15.0 macOS framework (cibuildwheel cross-builds iOS with it) ---
CPYTHON_VERSION="3.15.0"
CPYTHON_FRAMEWORK="/Library/Frameworks/Python.framework/Versions/3.15"
CPYTHON_PKG_URL="https://www.python.org/ftp/python/$CPYTHON_VERSION/python-$CPYTHON_VERSION-macos11.pkg"
CPYTHON_PKG_CACHE="${BUILD_ROOT}/python-$CPYTHON_VERSION-macos11.pkg"

# A pre-release framework left over from an earlier build satisfies "3.15 is
# installed" while not being the release these wheels claim to be built on.
installed="$("$CPYTHON_FRAMEWORK/bin/python3.15" -c 'import platform; print(platform.python_version())' 2>/dev/null || true)"
if [[ "$installed" != "$CPYTHON_VERSION" ]]; then
  echo "CPython $CPYTHON_VERSION macOS framework is required to cross-build iOS wheels"
  echo "(found: ${installed:-none}). cibuildwheel refuses to sudo-install outside CI."
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

# --- pyobjus checkout, at the pinned commit -----------------------------------
clone_pinned ios.pyobjus "$PYOBJUS_SRC"
echo "pyobjus checkout at: $(git -C "$PYOBJUS_SRC" log -1 --oneline)"

# Upstream keeps the last tag's version on master. See stamp_pyobjus_version.py.
echo "Stamping the version from the pinned commit ..."
python3 "$ROOT/recipes/lib/stamp_pyobjus_version.py" "$PYOBJUS_SRC"

echo "Installing cibuildwheel ..."
# This exact version is what selects the CPython iOS framework the wheels link
# against (4.3.1+ bundles 3.15.0 final).
python3 -m pip install -q 'cibuildwheel==4.3.4'

pushd "$PYOBJUS_SRC" >/dev/null

# pyobjus's only native dependency is libffi; its .ci script cross-builds the
# three slices (with the armv7 xcodeproj patch) into ios-deps-install/, which
# setup.py links per slice.
DEPS_MARKER="ios-deps-install/iphoneos/arm64/lib/libffi.a"
if [[ ! -f "$DEPS_MARKER" ]]; then
  echo "Building libffi for iOS (pyobjus .ci/build_ios_dependencies.sh) ..."
  bash .ci/build_ios_dependencies.sh
else
  echo "Reusing cached ios-deps-install in $PYOBJUS_SRC"
fi

export CIBW_PLATFORM=ios
export CIBW_ARCHS="arm64_iphoneos arm64_iphonesimulator x86_64_iphonesimulator"
export CIBW_BUILD="cp315-*"
# iOS builds run in an isolated cross-venv, so exporting IPHONEOS_DEPLOYMENT_TARGET
# here would never reach the compiler; injecting it via CIBW_ENVIRONMENT_IOS does.
# This sets what the extension links against, not the wheel's tag — see the header.
export CIBW_ENVIRONMENT_IOS="IPHONEOS_DEPLOYMENT_TARGET=$IOS_DEPLOYMENT_TARGET"

WHEELHOUSE="$(mktemp -d "$BUILD_ROOT/pyobjus-wheelhouse.XXXXXX")"
echo "Running cibuildwheel (IPHONEOS_DEPLOYMENT_TARGET=$IOS_DEPLOYMENT_TARGET, output -> $WHEELHOUSE) ..."
python3 -m cibuildwheel --output-dir "$WHEELHOUSE"

popd >/dev/null

echo "Copying wheels to $OUTPUT_DIR ..."
cp -v "$WHEELHOUSE"/*.whl "$OUTPUT_DIR/"
rm -rf "$WHEELHOUSE"

echo "Done. Built wheels:"
ls -1 "$OUTPUT_DIR"/pyobjus-*.whl
