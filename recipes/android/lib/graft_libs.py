#!/usr/bin/env python3
"""Graft shared libraries into a wheel's flat top-level ``.libs/``.

Replaces what auditwheel would normally do, because auditwheel cannot be used
here: it renames grafted libraries with a hash suffix (``libSDL2-1664d2a2.so``)
and Android's ``System.loadLibrary("SDL2")`` resolves the *exact* filename
``libSDL2.so``. A repaired wheel builds, passes every alignment check, and then
throws ``UnsatisfiedLinkError`` at app start.

So the libraries go in verbatim, sonames untouched, in a **flat** directory —
kivyforge's staging rejects per-ABI subdirectories, and its jniLibs flattening
depends on the names being exactly what ``DT_NEEDED`` asks for.

Also verifies 16 KB page alignment across the whole wheel, since that is the
property most easily lost and least visibly.

``libdir`` may be ``-`` for wheels with nothing to graft (pyjnius bundles no
third-party libraries). The wheel is still unpacked, verified and repacked —
skipping the alignment check for those would leave the one property most easily
lost unverified on half the wheels we ship.

Usage:  graft_libs.py <wheel> <libdir|-> <outdir>
"""

from __future__ import annotations

import hashlib
import shutil
import struct
import sys
import zipfile
from pathlib import Path

WANT_ALIGN = 0x4000  # 16 KB


def p_align_values(data: bytes) -> list[int]:
    """p_align of every PT_LOAD segment in an ELF image."""
    if data[:4] != b"\x7fELF":
        return []
    is64 = data[4] == 2
    little = data[5] == 1
    end = "<" if little else ">"
    if not is64:
        return []
    e_phoff, = struct.unpack_from(end + "Q", data, 0x20)
    e_phentsize, e_phnum = struct.unpack_from(end + "HH", data, 0x36)
    out = []
    for i in range(e_phnum):
        off = e_phoff + i * e_phentsize
        p_type, = struct.unpack_from(end + "I", data, off)
        if p_type == 1:  # PT_LOAD
            p_align, = struct.unpack_from(end + "Q", data, off + 0x30)
            out.append(p_align)
    return out


def main() -> int:
    if len(sys.argv) != 4:
        print(__doc__, file=sys.stderr)
        return 2
    wheel = Path(sys.argv[1]).resolve()
    outdir = Path(sys.argv[3]).resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    if sys.argv[2] == "-":
        libs: list[Path] = []
    else:
        libdir = Path(sys.argv[2]).resolve()
        # Both SDL generations; the Kivy 2.3.1 recipe passes an SDL2 prefix and
        # the Kivy 3 recipe an SDL3 one.
        libs = sorted(libdir.glob("libSDL*.so"))
        if not libs:
            print(f"graft: no libSDL*.so in {libdir}", file=sys.stderr)
            return 1

    # Nothing to graft: verify in place and copy the wheel through untouched.
    # Repacking would change its sha256 for no benefit, and the point is to
    # ship exactly what cibuildwheel produced.
    if not libs:
        bad = []
        count = 0
        with zipfile.ZipFile(wheel) as zf:
            for name in zf.namelist():
                if not name.endswith(".so"):
                    continue
                count += 1
                aligns = p_align_values(zf.read(name))
                if aligns and any(a != WANT_ALIGN for a in aligns):
                    bad.append((name, [hex(a) for a in aligns]))
        if bad:
            for name, aligns in bad:
                print(f"  FAIL {name}: p_align={aligns} (want 0x4000)", file=sys.stderr)
            print("graft: 16 KB alignment check failed.", file=sys.stderr)
            return 1
        print(f"  nothing to graft; {count} .so all at p_align=0x4000")
        out = outdir / wheel.name
        shutil.copy2(wheel, out)
        print(f"  {out.name}")
        print(f"  sha256 {hashlib.sha256(out.read_bytes()).hexdigest()}")
        return 0

    work = outdir / f".work-{wheel.stem}"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    with zipfile.ZipFile(wheel) as zf:
        zf.extractall(work)

    dest = work / ".libs"
    dest.mkdir(exist_ok=True)
    for lib in libs:
        shutil.copy2(lib, dest / lib.name)
        print(f"  grafted {lib.name}")

    # Verify alignment across everything the wheel will ship.
    bad = []
    for so in sorted(work.rglob("*.so")):
        aligns = p_align_values(so.read_bytes())
        if aligns and any(a != WANT_ALIGN for a in aligns):
            bad.append((so.relative_to(work), [hex(a) for a in aligns]))
    total = sum(1 for _ in work.rglob("*.so"))
    if bad:
        for rel, aligns in bad:
            print(f"  FAIL {rel}: p_align={aligns} (want 0x4000)", file=sys.stderr)
        print(
            "graft: 16 KB alignment check failed; Android 15/16 devices with "
            "16 KB pages will refuse to load this wheel.",
            file=sys.stderr,
        )
        return 1
    print(f"  {total} .so all at p_align=0x4000")

    out = outdir / wheel.name
    if out.exists():
        out.unlink()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(work.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(work))
    shutil.rmtree(work)

    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    print(f"  {out.name}")
    print(f"  sha256 {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
