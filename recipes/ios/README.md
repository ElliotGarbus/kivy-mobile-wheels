# iOS recipes

**Status: skeletons.** Unlike Android, working build scripts already exist —
kivyforge's `scripts/build_ios_wheels.sh` and
`scripts/build_pyobjus_ios_wheels.sh`, which produced the Kivy 3.0 wheel that
`examples/mobile/hello-kivy` currently runs on the simulator. Phase 4 ports
them here and into CI.

| Script | Builds | Pinned by |
|---|---|---|
| `kivy-3.0-sdl3.sh` | Kivy 3.0 pre-release, 3 slices | `PINNED_REFS.toml` |
| `pyobjus.sh` | pyobjus, 3 slices | `PINNED_REFS.toml` |

Slices: `arm64_iphoneos`, `arm64_iphonesimulator`, `x86_64_iphonesimulator`.
`cp315-*` only. `IPHONEOS_DEPLOYMENT_TARGET=16.0`.

**Requires macOS + Xcode.** cibuildwheel's iOS target has no Linux path.

## What the port must change

**Drop the branch-name defaults.** Both scripts currently default
`KIVY_REF=master` / `PYOBJUS_REF=master`. Read the commit from
`PINNED_REFS.toml` via `recipes/lib/pins.sh` instead, with no fallback.

**Handle the CPython framework in CI.** Both scripts stop and ask the user to
`sudo installer -pkg` the CPython 3.15 macOS framework, because cibuildwheel
refuses to sudo-install outside CI. A CI runner should install it
non-interactively rather than exiting 1.

**Deployment target must go through `CIBW_ENVIRONMENT_IOS`.** Exporting
`IPHONEOS_DEPLOYMENT_TARGET` in the shell is not enough — iOS builds run in an
isolated cross-venv and cibuildwheel defaults the wheel tag to `13.0` unless
the variable is injected. Getting this wrong silently produces `ios_13_0_*`
tags instead of `ios_16_0_*`.

## Kivy specifics

Kivy's own `tools/build_ios_dependencies.sh` builds the SDL3 + ANGLE + ThorVG
xcframeworks; `tools/add-ios-frameworks.py` then grafts them into the wheel.
Both are upstream Kivy scripts — this recipe drives them, it does not
reimplement them. The native-dependency build is slow, so the existing script
caches `ios-kivy-dependencies/` and only rebuilds when the SDL3 marker is
missing; preserve that in CI if the runner has a usable cache.

## pyobjus specifics

Only native dependency is libffi, built by pyobjus's own
`.ci/build_ios_dependencies.sh` into `ios-deps-install/`.

**pyobjus has no iOS CI upstream**, so this path is not upstream-validated —
the first CI run here is its real test, not a formality. `setup.py` gates the
iOS branch on `sys.platform == "ios"` and picks the libffi slice via
`platform.ios_ver().is_simulator` and `ARCH`; those are the first two things to
check if a build fails.

The python.org cp315 binary targets iOS 13.0, so pyobjus wheels may tag as
`ios_13_0_*` even with the deployment target set. That is fine — a lower
minimum OS is compatible with a 16.0 project.
