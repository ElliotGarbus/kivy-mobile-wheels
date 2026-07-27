#!/usr/bin/env python3
"""Verify the minimum iOS version recorded in an iOS wheel's Mach-O binaries.

**Why this exists, and why the wheel tag cannot be used instead.**

An iOS wheel's platform tag comes from the *interpreter's* build configuration,
not from the build environment: python.org's CPython iOS framework is built with
a 13.0 deployment target, so every wheel cross-built against it is tagged
``ios_13_0_*`` no matter what ``IPHONEOS_DEPLOYMENT_TARGET`` is set to. The
variable still does its real job — it is passed to the compiler and linker, and
lands in each Mach-O's ``LC_BUILD_VERSION.minos``.

So the tag tells you nothing about the deployment target, and this check reads
the only place that does. A lower tag than the linked minimum is not a problem:
a wheel tagged 13.0 whose binaries require 16.0 installs into any project with a
deployment target of 16.0 or higher, which is what the consumer sets.

Usage:  check_min_os.py <expected-version> <wheel> [<wheel> ...]

        check_min_os.py 16.0 dist/ios/*.whl
"""

from __future__ import annotations

import struct
import sys
import zipfile
from pathlib import Path

MH_MAGIC_64 = 0xFEEDFACF
FAT_MAGIC = 0xCAFEBABE
FAT_MAGIC_64 = 0xCAFEBABF
LC_VERSION_MIN_IPHONEOS = 0x25
LC_BUILD_VERSION = 0x32
# Mach-O platform ids that mean "some flavour of iOS".
IOS_PLATFORMS = {2, 7}  # PLATFORM_IOS, PLATFORM_IOSSIMULATOR

MACH_O_SUFFIXES = (".so", ".dylib")


def decode_version(value: int) -> tuple[int, int, int]:
    """Mach-O packed version ``X.Y.Z`` -> tuple."""
    return (value >> 16, (value >> 8) & 0xFF, value & 0xFF)


def format_version(value: tuple[int, int, int]) -> str:
    major, minor, patch = value
    return f"{major}.{minor}" if patch == 0 else f"{major}.{minor}.{patch}"


def slice_offsets(data: bytes) -> list[int]:
    """Offsets of each Mach-O image, unwrapping a fat binary if present."""
    if len(data) < 8:
        return []
    magic, = struct.unpack_from(">I", data, 0)
    if magic in (FAT_MAGIC, FAT_MAGIC_64):
        count, = struct.unpack_from(">I", data, 4)
        wide = magic == FAT_MAGIC_64
        entry = 32 if wide else 20
        offsets = []
        for i in range(count):
            base = 8 + i * entry
            if wide:
                offset, = struct.unpack_from(">Q", data, base + 8)
            else:
                offset, = struct.unpack_from(">I", data, base + 8)
            offsets.append(offset)
        return offsets
    return [0]


def min_os_versions(data: bytes) -> list[tuple[int, int, int]]:
    """``minos`` of every iOS Mach-O slice in ``data``."""
    found = []
    for base in slice_offsets(data):
        if len(data) < base + 32:
            continue
        magic, = struct.unpack_from("<I", data, base)
        if magic != MH_MAGIC_64:
            continue
        ncmds, = struct.unpack_from("<I", data, base + 16)
        pos = base + 32
        for _ in range(ncmds):
            if len(data) < pos + 8:
                break
            cmd, cmdsize = struct.unpack_from("<II", data, pos)
            if cmdsize == 0:
                break
            if cmd == LC_BUILD_VERSION:
                platform, minos = struct.unpack_from("<II", data, pos + 8)
                if platform in IOS_PLATFORMS:
                    found.append(decode_version(minos))
            elif cmd == LC_VERSION_MIN_IPHONEOS:
                version, = struct.unpack_from("<I", data, pos + 8)
                found.append(decode_version(version))
            pos += cmdsize
    return found


def check(wheel: Path, expected: tuple[int, int, int]) -> tuple[int, list[str]]:
    """(binaries checked, complaints) for ``wheel``."""
    problems = []
    checked = 0
    with zipfile.ZipFile(wheel) as zf:
        for name in sorted(zf.namelist()):
            if not name.endswith(MACH_O_SUFFIXES):
                continue
            versions = min_os_versions(zf.read(name))
            if not versions:
                continue
            checked += 1
            for version in sorted(set(versions)):
                if version < expected:
                    problems.append(
                        f"{name}: minos {format_version(version)} is below "
                        f"{format_version(expected)}"
                    )
    return checked, problems


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2

    parts = sys.argv[1].split(".")
    expected = (
        int(parts[0]),
        int(parts[1]) if len(parts) > 1 else 0,
        int(parts[2]) if len(parts) > 2 else 0,
    )

    failed = False
    for wheel in (Path(a) for a in sys.argv[2:]):
        checked, problems = check(wheel, expected)
        if not checked:
            print(f"  FAIL {wheel.name}: no iOS Mach-O binaries found", file=sys.stderr)
            failed = True
            continue
        if problems:
            failed = True
            print(f"  FAIL {wheel.name}", file=sys.stderr)
            for line in problems:
                print(f"    {line}", file=sys.stderr)
        else:
            print(
                f"  ok   {wheel.name}: {checked} binaries at minos "
                f"{format_version(expected)} or higher"
            )

    if failed:
        print(
            "check_min_os: IPHONEOS_DEPLOYMENT_TARGET did not reach the "
            "compiler. Note that the wheel's ios_*_* tag comes from the "
            "interpreter and will not show this either way.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
