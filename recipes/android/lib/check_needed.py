#!/usr/bin/env python3
"""Verify every ``DT_NEEDED`` in an Android wheel can actually be resolved.

This is the static stand-in for the one test CI does not have: running the
wheel on a device. The failure it catches is the ``libc++_shared.so`` class of
bug — an extension links against a library nobody ships, the wheel builds, every
structural check passes, and the app then fails at ``dlopen`` time. Kivy reports
that as a provider being "ignored" and the real ``ImportError`` is visible only
at debug log level, so the symptom on a device is a feature quietly missing.

A dependency resolves if it is either:

* shipped inside the wheel (any ``.so`` in it, including the grafted
  ``.libs/``) — matched on file name, which is what the dynamic linker uses on
  Android; or
* part of the OS, i.e. in ``SYSTEM_LIBS`` below.

Anything else is a hard failure. ``SYSTEM_LIBS`` is deliberately a short
allow-list of NDK stable-ABI libraries rather than "everything in the sysroot":
the sysroot also contains libraries that are *not* guaranteed present on a
device, and a permissive list here would defeat the purpose.

Note that ``libc++_shared.so`` is **not** in the list. It is an NDK-shipped
runtime, not an OS library, so a wheel that needs it must carry it.

Usage:  check_needed.py <wheel> [<wheel> ...]
"""

from __future__ import annotations

import struct
import sys
import zipfile
from pathlib import Path

# NDK stable-ABI libraries guaranteed on any API 24+ device.
# https://developer.android.com/ndk/guides/stable_apis
SYSTEM_LIBS = frozenset(
    {
        "libc.so",
        "libm.so",
        "libdl.so",
        "libz.so",
        "liblog.so",
        "libandroid.so",
        "libjnigraphics.so",
        "libEGL.so",
        "libGLESv1_CM.so",
        "libGLESv2.so",
        "libGLESv3.so",
        "libvulkan.so",
        "libOpenSLES.so",
        "libOpenMAXAL.so",
        "libaaudio.so",
        "libamidi.so",
        "libcamera2ndk.so",
        "libmediandk.so",
        "libnativewindow.so",
        "libneuralnetworks.so",
        "libsync.so",
        # The Python runtime is provided by the app, not the wheel — the same
        # arrangement as libpython on a desktop install.
        "libpython3.14.so",
        "libpython3.15.so",
    }
)

DT_NULL = 0
DT_NEEDED = 1
DT_STRTAB = 5
SHT_DYNAMIC = 6


def dt_needed(data: bytes) -> list[str]:
    """Every ``DT_NEEDED`` name in a 64-bit little-endian ELF image."""
    if data[:4] != b"\x7fELF" or data[4] != 2:
        return []
    end = "<" if data[5] == 1 else ">"
    e_shoff, = struct.unpack_from(end + "Q", data, 0x28)
    e_shentsize, e_shnum = struct.unpack_from(end + "HH", data, 0x3A)
    if not e_shoff or not e_shnum:
        return []

    # Locate .dynamic, then the string table it points at. Reading DT_STRTAB
    # from .dynamic itself (rather than trusting section names) keeps this
    # working on stripped libraries.
    dynamic: tuple[int, int] | None = None
    sections: list[tuple[int, int, int]] = []
    for i in range(e_shnum):
        off = e_shoff + i * e_shentsize
        sh_type, = struct.unpack_from(end + "I", data, off + 0x04)
        sh_addr, sh_offset, sh_size = struct.unpack_from(end + "QQQ", data, off + 0x10)
        sections.append((sh_addr, sh_offset, sh_size))
        if sh_type == SHT_DYNAMIC:
            dynamic = (sh_offset, sh_size)
    if dynamic is None:
        return []

    dyn_off, dyn_size = dynamic
    strtab_addr = 0
    needed_offsets: list[int] = []
    for pos in range(dyn_off, dyn_off + dyn_size, 16):
        d_tag, d_val = struct.unpack_from(end + "qQ", data, pos)
        if d_tag == DT_NULL:
            break
        if d_tag == DT_NEEDED:
            needed_offsets.append(d_val)
        elif d_tag == DT_STRTAB:
            strtab_addr = d_val
    if not needed_offsets or not strtab_addr:
        return []

    # DT_STRTAB is a virtual address; map it back to a file offset.
    strtab_off = None
    for sh_addr, sh_offset, sh_size in sections:
        if sh_addr and sh_addr <= strtab_addr < sh_addr + sh_size:
            strtab_off = sh_offset + (strtab_addr - sh_addr)
            break
    if strtab_off is None:
        return []

    names = []
    for offset in needed_offsets:
        start = strtab_off + offset
        stop = data.index(b"\x00", start)
        names.append(data[start:stop].decode())
    return names


def check(wheel: Path) -> list[str]:
    """Unresolvable dependencies in ``wheel``, as printable lines."""
    with zipfile.ZipFile(wheel) as zf:
        shared_objects = {
            name: zf.read(name) for name in zf.namelist() if name.endswith(".so")
        }

    provided = {Path(name).name for name in shared_objects}
    problems = []
    for name, data in sorted(shared_objects.items()):
        for dep in dt_needed(data):
            if dep in provided or dep in SYSTEM_LIBS:
                continue
            problems.append(f"{name} needs {dep}, which nothing provides")
    return problems


def main() -> int:
    wheels = [Path(a) for a in sys.argv[1:]]
    if not wheels:
        print(__doc__, file=sys.stderr)
        return 2

    failed = False
    for wheel in wheels:
        problems = check(wheel)
        if problems:
            failed = True
            print(f"  FAIL {wheel.name}", file=sys.stderr)
            for line in problems:
                print(f"    {line}", file=sys.stderr)
        else:
            print(f"  ok   {wheel.name}: every DT_NEEDED resolves")

    if failed:
        print(
            "check_needed: a dependency is missing. The wheel will install and "
            "then fail at dlopen on device, usually silently.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
