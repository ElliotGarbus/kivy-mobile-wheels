# Android SDL3 / Kivy 3.0 — investigation findings

Phase 3 of the wheels plan. Recorded before writing the recipe, because two of
these change what the recipe has to do.

## 1. Kivy 3 already has an Android SDL3 branch, with a fixed layout

`setup.py` (kivy/kivy@a933f859) resolves SDL3 for Android like this:

```python
if platform == 'android':
    root = os.getcwd()
    android_abis = ['arm64-v8a', 'x86_64']
    ...
    lib_path = join(root, 'dist', 'libs', abi)
    'SDL3': {'path': join(lib_path, 'libSDL3.so'),
             'headers': join(root, 'dist', 'include', 'SDL3')}
```

Three consequences:

- **It uses `os.getcwd()`, not `KIVY_DEPS_ROOT`.** Unlike the iOS path, there
  is no environment variable to point elsewhere — the libraries must be laid
  out under the Kivy source tree the build runs in:

  ```
  <kivy-src>/dist/libs/<abi>/libSDL3{,_image,_mixer,_ttf}.so
  <kivy-src>/dist/include/{SDL3,SDL3_image,SDL3_mixer,SDL3_ttf}/
  ```

- **The ABI list is hardcoded** to `arm64-v8a` and `x86_64` — exactly the two
  kivyforge targets, so nothing to reconcile.

- `platform == 'android'` sets `use_sdl3 = True` unconditionally, so there is
  no equivalent of SDL2's `USE_SDL2=1` opt-in and no pkg-config probe to fight.
  `KIVY_SDL3_PATH` exists but only as a bypass for when `use_sdl3` is unset —
  irrelevant on Android, where it is already forced on.

The comment in that block reads *"Assuming SDL3 libraries are in
dist/libs/{ABI}/ structure"*, which is **not** the layout SDL3 ships (below),
so the recipe has to build that tree.

## 2. SDL3 ships `.aar`s, not loose libraries

`SDL3-devel-<ver>-android.zip` contains a single `SDL3-<ver>.aar` (plus
INSTALL/LICENSE/README/.git-hash). The `.aar` is itself a zip using the
**prefab** layout:

```
prefab/modules/SDL3-shared/libs/android.<abi>/libSDL3.so
prefab/modules/SDL3-Headers/include/SDL3/*.h
classes.jar  classes-sources.jar  classes-doc.jar
```

So the recipe unwraps zip → aar → prefab, and rearranges into the
`dist/libs/<abi>` + `dist/include` tree Kivy expects. Both steps are pure
extraction; nothing is compiled.

## 3. **SDL3_ttf's official prebuilt is 4 KB-aligned — it cannot be shipped as-is**

Measured `p_align` of every `PT_LOAD` segment in the published `.aar`s:

| Library | Version | arm64-v8a | x86_64 | |
|---|---|---|---|---|
| SDL3 | 3.4.12 | `0x4000` | `0x4000` | ok |
| SDL3_image | 3.4.4 | `0x4000` | `0x4000` | ok |
| SDL3_mixer | 3.2.4 | `0x4000` | `0x4000` | ok |
| **SDL3_ttf** | **3.2.2** | **`0x1000`** | **`0x1000`** | **fails** |

An app linking the published library would build cleanly, pass every other
check, and then fail to load on any Android 15/16 device with 16 KB pages.
**So SDL3_ttf is built from source** with `-Wl,-z,max-page-size=16384`, the
same treatment the whole SDL2 line needed; the other three are used as
published.

Worth stating plainly because the plan assumed the opposite: *"SDL3 publishes
an official prebuilt … which should be cheaper"*. It is cheaper — three of
four — but not free, and the exception is invisible unless measured.

### Why ttf is on 3.2.x while SDL3 and SDL3_image are on 3.4.x

3.2.2 (2025-03-31) is the newest SDL3_ttf release — confirmed against all
releases including prereleases, the `releases/latest` endpoint, and the tag
list. There is nothing newer to move to.

**This is not a coordinated release ttf was omitted from.** SDL's satellites
each release on their own train, and still cut SDL2-line releases in parallel:

| | line | latest |
|---|---|---|
| SDL_image | 3.2.6 -> 3.4.0 -> 3.4.2 -> 3.4.4 | 2026-05-01 |
| SDL_mixer | 3.2.0 -> 3.2.2 -> 3.2.4 | 2026-06-03 |
| SDL_ttf | 3.2.0 -> 3.2.2 | 2025-03-31 |

SDL_ttf has simply published nothing in 16 months, though its repository is
active (`main` pushed 2026-07-05) and its in-development version is **3.3.0**.
SDL treats odd minors as development and even as stable — SDL_image went
`prerelease-3.3.2`, `prerelease-3.3.4`, then `release-3.4.0` — so ttf's 3.3.x
line is what becomes 3.4.0.

The defect is in how the published `.aar` was linked, not in SDL3_ttf itself:
the same 3.2.2 source built with `-Wl,-z,max-page-size=16384` produces a
correctly aligned library, verified in CI on both ABIs. So when 3.4.0 lands,
re-measure its `.aar` first — if it is 16 KB-aligned, the from-source step
should be deleted rather than carried forward.

## 4. The `.aar` carries the Java glue as source

`classes-sources.jar` inside the `.aar` holds 12 files under
`org/libsdl/app/` — the SDL2 set plus `SDLDummyEdit`, `SDLInputConnection`,
`SDLSensorManager`.

That matters beyond this repo: kivyforge needs SDL3 Java glue for its
`templates/sdl3/` (today `render_bootstrap(sdl=3)` raises), and `SDLActivity`
enforces the same version-matching contract as SDL2 — mismatch aborts
`onCreate` silently, black screen, nothing in logcat. Taking the glue from
the same verified `.aar` that provides `libSDL3.so` makes that mismatch
impossible by construction, exactly as `sdl-glue/` does for SDL2.

## Consequences for the recipe

1. Fetch four zips, verify by SHA-256, unwrap zip → aar → prefab.
2. Build **SDL3_ttf from source** for both ABIs with the page-size flag.
3. Assemble `<kivy-src>/dist/{libs/<abi>,include}` before invoking cibuildwheel.
4. Verify 16 KB alignment across all four **after** assembly — the check is
   what caught this, and it has to run on whatever is actually shipped.
5. Capture the glue into `sdl-glue-sdl3/<version>/` for kivyforge to vendor.
6. Kivy 3 is unreleased, so it builds from a pinned commit like the iOS side.
   **No pre-cythonize step is needed**: `can_use_cython` — the switch that
   disabled Cython on Android in 2.3.1 — does not exist in Kivy 3, which
   imports Cython unconditionally. It only has to be present in cibuildwheel's
   cross-venv, which runs with `--no-isolation`.
