# kivy-mobile-wheels

Reproducible **Android** and **iOS** wheels for the packages that don't publish
mobile wheels of their own — Kivy, pyjnius, pyobjus — built in CI, hosted on
GitHub Releases, and served as a [PEP 503](https://peps.python.org/pep-0503/)
index.

Built for [kivyforge](https://github.com/ElliotGarbus/kivyforge) development.

## This is a bridge, and it is meant to be deleted

Mobile wheels belong on PyPI, published by each project's own CI. Nothing here
is a long-term home for anything.

**Retirement condition:** when Kivy, pyjnius and pyobjus publish `android_*` /
`ios_*` wheels to PyPI themselves, this repository is archived and consumers
drop one line of configuration. Nothing else about their setup changes —
which is the whole reason for serving a real index instead of passing files
around.

Until then the alternative is loose `.whl` files on whichever laptop built
them: no backup, no provenance, no way for a second machine to reproduce a
build. That is the problem this solves, and the only one.

## Using it

Add the index to a kivyforge project:

```toml
[tool.kivy.android]   # or [tool.kivy.ios]
extra_index_urls = ["https://elliotgarbus.github.io/kivy-mobile-wheels/simple/"]
```

`kivyforge lock` then resolves these wheels exactly like any PyPI package,
recording `url` + `sha256` in the lock file. No `find_links`, no vendored
binaries in your project.

> The index carries the real package names (`kivy`, `pyjnius`, `pyobjus`). pip
> resolves across all configured indexes and takes the highest version, which
> is the intended behaviour here. At install time kivyforge is unaffected
> either way — the lock pins exact URLs and hashes.

## Scope

| Platform | Kivy | Graphics | Bridge |
|---|---|---|---|
| Android | 2.3.1 | SDL2 (2.32.10, built from source) | pyjnius |
| Android | 3.0 (pre-release) | SDL3 | pyjnius |
| iOS | 3.0 (pre-release) | SDL3 | pyobjus |

**Kivy 2.3.1 on iOS is deliberately out of scope.** kivyforge supports Kivy 3+
only on iOS, and [kivy-ios](https://github.com/kivy/kivy-ios) already serves
2.x there — so there is no gap to fill. Kivy 2.3.1's iOS build also targets
`OpenGLES.framework`, deprecated by Apple in 2018; Kivy 3.0 routes GLES through
ANGLE over Metal instead.

## Reproducibility

Every input is pinned, and nothing resolves a branch at build time:

- **Source tarballs** (SDL2 and satellites, Kivy 2.3.1) — versioned release or
  PyPI sdist, verified by recorded SHA-256.
- **Git checkouts** (pyjnius, Kivy 3.0 pre-release, pyobjus) — exact commits in
  [`recipes/PINNED_REFS.toml`](recipes/PINNED_REFS.toml). Recipes read the
  commit from there and have no branch-name fallback; a missing pin fails the
  build rather than quietly floating.

Each release records the pins it was built from.

Wheel builds are **not** bit-reproducible — rebuilding the same inputs yields a
different `sha256`. Consumers must re-lock against a new release rather than
assume hashes carry over.

## Layout

```
recipes/
  PINNED_REFS.toml     exact upstream commits (see above)
  android/             SDL2 + Kivy + pyjnius build scripts
    sdl-glue/          org/libsdl/app/*.java extracted from the SDL tarball
  ios/                 Kivy + pyobjus build scripts
.github/workflows/     build matrices + index publication
index-gen/             PEP 503 static index generator
```

### `recipes/android/sdl-glue/`

Android needs SDL's Java glue (`org/libsdl/app/*.java`) on the app side, and it
**must** come from the same SDL release as the `libSDL2.so` in the wheel.
`SDLActivity` compares its compiled-in version against `nativeGetVersion()` and,
on mismatch, aborts `onCreate` *before creating the surface* — logging nothing.
The app shows a black screen, `SDL_main` is never called, and no Python runs.

So the glue is extracted from the same verified tarball that builds the
library, committed alongside it, and checked in CI. It is not published as a
release asset: nobody `pip install`s a `.java` file, and a second copy on the
distribution path is one more thing that can drift.

## Status

Scaffolding. No wheels published yet — see
`.cursor/plans/mobile-wheels-repo.plan.md` in the kivyforge repo for the phase
plan.

## License

MIT, matching Kivy. The build recipes are original work; the artifacts they
produce carry their own upstream licenses (Kivy: MIT, SDL: zlib).
