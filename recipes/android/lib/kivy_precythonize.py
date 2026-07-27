#!/usr/bin/env python3
"""Generate config.pxi and pre-cythonize Kivy for an Android cross-build.

Kivy 2.3.1's sdist ships 45 ``.pyx`` against only 9 ``.c``, and its setup.py
sets ``can_use_cython = False`` on Android — so nothing cythonizes them during
the wheel build and the compile fails on missing sources. They have to be
generated first, here.

Three non-obvious things this has to get right, each of which cost a build:

1. ``config.pxi`` must exist before Cython runs — every graphics ``.pyx``
   ``include``s it. Kivy writes it at the top of ``KivyBuildExt
   .build_extensions()``, *before* compiling, so running ``build_ext`` with the
   per-extension compile step no-oped produces it and stops cleanly.
   ``KIVY_FAKE_BUILDEXT=1`` does **not** work: it returns an empty extension
   list, so ``build_extensions()`` never runs and no config is written.

2. Only the extensions Kivy actually builds on Android may be cythonized —
   38 of the 45. Cythonizing all of them fails on desktop-only sources such as
   ``window_x11.pyx``. The list is obtained by importing setup.py with
   ``setuptools.setup`` intercepted, which is the only way to see the real
   ``ext_modules`` for the configured platform.

3. Cythonize **serially**. Cython's parallel mode uses multiprocessing, whose
   workers re-import ``__main__``; run from a heredoc that is ``<stdin>`` and
   cannot be re-imported, and the build dies with ``ConnectionResetError``.

Usage:  kivy_precythonize.py <kivy-source-dir>
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# The configuration the Android cross-build must produce. Checked rather than
# assumed: a wrong value here yields a wheel that builds and then misbehaves at
# runtime, which is far more expensive to diagnose than a failed build.
EXPECTED_CONFIG = {
    "PY3": "1",
    "USE_SDL2": "1",
    "USE_ANDROID": "1",
    "USE_OPENGL_ES2": "1",
    "USE_IOS": "0",
    "USE_EGL": "0",
    "USE_GSTREAMER": "0",
    "USE_X11": "0",
    "PLATFORM": '"android"',
}


def write_config_pxi(src: Path) -> Path:
    """Run build_ext far enough to emit config.pxi, then stop."""
    from setuptools.command import build_ext as build_ext_mod

    # Kivy writes config.h/config.pxi/setupconfig.py at the top of
    # build_extensions(), then compiles. No-op the per-extension compile so the
    # configs land and the command returns without touching a compiler.
    build_ext_mod.build_ext.build_extension = lambda self, ext: None

    sys.argv = ["setup.py", "build_ext", "--inplace"]
    sys.path.insert(0, str(src))
    os.chdir(src)
    runpy_globals = {"__file__": str(src / "setup.py"), "__name__": "__main__"}
    exec(compile((src / "setup.py").read_text(), "setup.py", "exec"), runpy_globals)

    config = src / "kivy" / "include" / "config.pxi"
    if not config.is_file():
        raise SystemExit(f"config.pxi was not generated at {config}")
    return config


def check_config(config: Path) -> None:
    values = {}
    for line in config.read_text().splitlines():
        if line.startswith("DEF "):
            key, _, value = line[4:].partition(" = ")
            values[key.strip()] = value.strip()
    wrong = {
        k: (v, values.get(k)) for k, v in EXPECTED_CONFIG.items()
        if values.get(k) != v
    }
    if wrong:
        for key, (want, got) in sorted(wrong.items()):
            print(f"  {key}: expected {want}, got {got}", file=sys.stderr)
        raise SystemExit(
            "config.pxi is not the Android configuration; the build environment "
            "is wrong (check KIVY_CROSS_PLATFORM, USE_SDL2, KIVY_SDL2_PATH)."
        )
    print(f"config.pxi OK ({len(values)} DEFs, PLATFORM={values.get('PLATFORM')})")


def android_pyx_sources(src: Path) -> list[str]:
    """The .pyx Kivy actually builds on Android, via intercepted setup()."""
    import setuptools

    captured: dict = {}
    original = setuptools.setup

    def intercept(**kwargs):
        captured.update(kwargs)

    setuptools.setup = intercept
    try:
        sys.argv = ["setup.py", "--version"]
        os.chdir(src)
        exec(
            compile((src / "setup.py").read_text(), "setup.py", "exec"),
            {"__file__": str(src / "setup.py"), "__name__": "__main__"},
        )
    finally:
        setuptools.setup = original

    ext_modules = captured.get("ext_modules") or []
    sources = []
    for ext in ext_modules:
        for source in ext.sources:
            if source.endswith(".pyx"):
                sources.append(source)
    if not sources:
        raise SystemExit(
            "no .pyx sources captured from setup.py; the interception failed "
            "or the platform was detected as something other than android."
        )
    return sources


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    src = Path(sys.argv[1]).resolve()

    print("==> generating config.pxi")
    config = write_config_pxi(src)
    check_config(config)

    print("==> collecting Android extension sources")
    sources = android_pyx_sources(src)
    print(f"  {len(sources)} .pyx to cythonize")

    print("==> cythonizing (serial)")
    from Cython.Build import cythonize

    # nthreads left at 0 deliberately — see the module docstring.
    cythonize(sources, include_path=[str(src / "kivy" / "include")], nthreads=0)

    generated = sum(1 for s in sources if Path(s[:-4] + ".c").is_file())
    print(f"  {generated}/{len(sources)} .c present")
    if generated != len(sources):
        raise SystemExit("cythonize did not produce every .c file")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
