#!/usr/bin/env python3
"""Unwrap an SDL3 Android release zip into a normal install prefix.

SDL3 distributes ``SDL3<_x>-devel-<ver>-android.zip`` containing a single
``.aar``, which is itself a zip in Android **prefab** layout:

    prefab/modules/<Name>-shared/libs/android.<abi>/lib<Name>.so
    prefab/modules/<Name>-Headers/include/<Name>/*.h
    cmake/<Name>Config.cmake

Nothing downstream understands that shape — Kivy wants
``dist/libs/<abi>/lib*.so`` and ``dist/include/<Name>/``, and a cmake build of
SDL3_ttf wants a normal ``prefix/{lib,include,lib/cmake}``. So this flattens
the prefab tree into both.

Verifies the zip's SHA-256 before touching it, and reports the ``p_align`` of
every extracted library: SDL3_ttf 3.2.2's published binaries are 4 KB-aligned
while the rest of the family is 16 KB, and that difference is invisible unless
measured (see SDL3-FINDINGS.md).

Usage:
    unwrap_sdl3_aar.py <zip> <sha256> <prefix> <abi> [<abi> ...]
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from graft_libs import WANT_ALIGN, p_align_values  # noqa: E402


def main() -> int:
    if len(sys.argv) < 5:
        print(__doc__, file=sys.stderr)
        return 2
    zip_path = Path(sys.argv[1]).resolve()
    want_sha = sys.argv[2]
    prefix = Path(sys.argv[3]).resolve()
    abis = sys.argv[4:]

    got = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    if got != want_sha:
        print(f"unwrap: SHA-256 mismatch for {zip_path.name}", file=sys.stderr)
        print(f"  expected {want_sha}", file=sys.stderr)
        print(f"  got      {got}", file=sys.stderr)
        return 1

    outer = zipfile.ZipFile(zip_path)
    aar_names = [n for n in outer.namelist() if n.endswith(".aar")]
    if len(aar_names) != 1:
        print(f"unwrap: expected one .aar, found {aar_names}", file=sys.stderr)
        return 1
    aar = zipfile.ZipFile(io.BytesIO(outer.read(aar_names[0])))

    meta = json.loads(aar.read("prefab/prefab.json"))
    name, version = meta["name"], meta["version"]
    print(f"  {name} {version} ({aar_names[0]})")

    (prefix / "lib").mkdir(parents=True, exist_ok=True)
    (prefix / "include").mkdir(parents=True, exist_ok=True)

    misaligned = []
    for entry in aar.namelist():
        # Shared libraries, per ABI. Skip the *_test static modules.
        if entry.endswith(".so") and "-shared/libs/android." in entry:
            abi = entry.split("android.")[1].split("/")[0]
            if abi not in abis:
                continue
            data = aar.read(entry)
            lib = entry.rsplit("/", 1)[1]
            # lib/<abi>/, which is where SDL3Config.cmake looks — it resolves
            # ${prefix}/lib/<abi>/libSDL3.so and hard-errors if that is
            # missing, so this layout is SDL's choice, not ours.
            dest = prefix / "lib" / abi
            dest.mkdir(parents=True, exist_ok=True)
            (dest / lib).write_bytes(data)
            aligns = p_align_values(data)
            flag = "" if all(a == WANT_ALIGN for a in aligns) else "  <-- NOT 16 KB"
            if flag:
                misaligned.append((abi, lib))
            print(f"    {abi:12s} {lib:22s} "
                  f"p_align={[hex(a) for a in aligns]}{flag}")
        # Headers.
        elif "-Headers/include/" in entry and not entry.endswith("/"):
            rel = entry.split("-Headers/include/", 1)[1]
            dest = prefix / "include" / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(aar.read(entry))
        # cmake package config, so SDL3_ttf can find_package(SDL3).
        elif entry.startswith("cmake/") and entry.endswith(".cmake"):
            dest = prefix / "lib" / "cmake" / name / entry.split("/", 1)[1]
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(aar.read(entry))

    # Java glue, where the .aar ships it. Same matched-pair rule as SDL2: the
    # glue that goes into an app must come from the release that built the .so.
    sources = [n for n in aar.namelist() if n.endswith("classes-sources.jar")]
    if sources:
        jar = zipfile.ZipFile(io.BytesIO(aar.read(sources[0])))
        java = [n for n in jar.namelist() if n.endswith(".java")]
        if java:
            glue = prefix / "java"
            for n in java:
                dest = glue / n
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(jar.read(n))
            print(f"    {len(java)} java sources -> {glue.name}/")

    if misaligned:
        print(
            f"  NOTE: {len(misaligned)} library/libraries are not 16 KB-aligned "
            "and must not be shipped as published.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
