# iOS recipes

| Script | Builds | Pinned by | Status |
|---|---|---|---|
| `kivy-3.0-sdl3.sh` | Kivy 3.0 pre-release, 3 slices | `PINNED_REFS.toml` (stamped `3.0.0.devYYYYMMDDHHMM`) | **ported, published from CI** |
| `pyobjus.sh` | pyobjus, 3 slices | `PINNED_REFS.toml` | **ported, published from CI** |

Both were ported from kivyforge's `scripts/build_ios_wheels.sh` and
`scripts/build_pyobjus_ios_wheels.sh`, which produced the wheels
`examples/mobile/hello-kivy` first ran on the simulator.

Slices: `arm64_iphoneos`, `arm64_iphonesimulator`, `x86_64_iphonesimulator`.
`cp315-*` only. `IPHONEOS_DEPLOYMENT_TARGET=16.0`.

**Requires macOS + Xcode.** cibuildwheel's iOS target has no Linux path — which
is also why `kivyforge lock -p ios` cannot run on a Windows host, so re-locking a
consumer after an iOS rebuild has to happen on a Mac.

> **These wheels were rebuilt in CI on 2026-07-27**, replacing the local builds
> the releases originally carried. The simulator validation recorded in
> kivyforge's `ios-validation-findings.md` was performed against the bytes that
> replaced — same commit, same recipe, but a different build — so repeat it on the
> simulator to carry that result forward.

## Traps already paid for — do not rediscover these

**`macos-14`'s system `python3` is Homebrew-managed (PEP 668).** A bare
`pip install cibuildwheel` fails there with `error:
externally-managed-environment` — this doesn't show up on a normal dev Mac,
only on GitHub-hosted runners. `actions/setup-python` before running the recipe
gives an unmanaged host Python. This is the interpreter that *drives*
cibuildwheel; it is unrelated to the cp315 iOS target being cross-built.
(Found by pyobjus's first CI run.)

**The CPython framework cannot be installed interactively in CI.** Both scripts
stop and ask the user to `sudo installer -pkg` the CPython 3.15 macOS framework,
because cibuildwheel refuses to sudo-install outside CI. `build-ios.yml` installs
it non-interactively instead.

**The deployment target must go through `CIBW_ENVIRONMENT_IOS` — and it will
not show up in the wheel tag.** Exporting `IPHONEOS_DEPLOYMENT_TARGET` in the
shell is not enough: iOS builds run in an isolated cross-venv, and cibuildwheel
builds that environment itself (it applies `CIBW_ENVIRONMENT` and then
`setdefault`s `13.0`, so an injected value wins). Inject it and the compiler
gets it.

What it does **not** do is change the platform tag. That comes from the
interpreter, and python.org's CPython iOS framework is built for 13.0 — so
**every wheel here is tagged `ios_13_0_*` even though it is linked for 16.0**,
and that is correct rather than a bug: a lower tag installs into any project
whose own deployment target is 16.0 or higher.

This is worth stating plainly because this file previously claimed the opposite —
that an `ios_13_0_*` tag was the symptom of a failed injection. Measured on the
CI-built wheels, every Mach-O reports `minos 16.0` while the tag reads
`ios_13_0`. The tag is not evidence either way, so
[`lib/check_min_os.py`](lib/check_min_os.py) reads `LC_BUILD_VERSION` from the
binaries instead, and `build-ios.yml` runs it on every wheel before publishing.

## Verification gate

Every wheel, before it is published:

1. Every Mach-O reports `minos` >= `IOS_DEPLOYMENT_TARGET`, across all three
   slices — `lib/check_min_os.py`.
2. The wheel resolves from the deployed index by name, version and platform tag,
   with a matching `sha256` — `index-gen/verify_index.py`, run by
   `publish-index.yml` after each deployment.

## Kivy specifics

Upstream's version is the literal `3.0.0.dev0` on every commit. The recipe runs
[`../lib/stamp_kivy_version.py`](../lib/stamp_kivy_version.py) before the build
so each commit publishes as `3.0.0.devYYYYMMDDHHMM` and can coexist with earlier
builds. `watch-upstream` / `refresh-kivy3` at the repo root are how a new pin
becomes a new pair of releases.

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
its CI run here is its real test, not a formality. `setup.py` gates the
iOS branch on `sys.platform == "ios"` and picks the libffi slice via
`platform.ios_ver().is_simulator` and `ARCH`; those are the first two things to
check if a build fails. It builds all three slices cleanly, and its single
extension links at `minos 16.0`.
