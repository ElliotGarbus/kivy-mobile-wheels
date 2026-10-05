#!/usr/bin/env python3
"""Publish each wheel's METADATA beside it, so pip can resolve without the wheel.

Without this, pip has to download every candidate wheel in full just to read its
``Requires-Dist`` — for a Kivy Android lock that is two ~9 MB downloads to learn
a few lines of text. PEP 658 lets an index serve that text on its own, at the
wheel's URL plus ``.metadata``. This index's anchors point at GitHub release
assets, so the ``.metadata`` file is just another asset on the same release:
``<wheel filename>.metadata``, whose download URL is then exactly the wheel's
plus the suffix. generate_index.py advertises it when it exists.

The file must be the wheel's ``*.dist-info/METADATA`` byte for byte. pip checks
it against the hash in the index, and a consumer's resolution is only as right
as this copy is.

Usage:
    wheel_metadata.py extract dist/*.whl     write <wheel>.metadata beside each
    wheel_metadata.py backfill               report releases missing metadata
    wheel_metadata.py backfill --apply       extract and upload it

``backfill`` needs the gh CLI, authenticated. It only ever adds ``.metadata``
assets; wheels are downloaded, never re-uploaded, so no published hash changes.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import NoReturn

SUFFIX = ".metadata"

# Top-level only: a vendored package's dist-info deeper in the tree is not this
# wheel's metadata.
METADATA = re.compile(r"^[^/]+\.dist-info/METADATA$")

# Mirrors generate_index.py: these are on PyPI now and absent from the index, so
# metadata for them would be published for nobody.
RETIRED = {"pyjnius"}


def fail(message: str) -> NoReturn:
    sys.exit(f"wheel_metadata: {message}")


def metadata_of(wheel: Path) -> bytes:
    """The wheel's own ``*.dist-info/METADATA``, exactly as stored."""
    with zipfile.ZipFile(wheel) as archive:
        found = [name for name in archive.namelist() if METADATA.match(name)]
        if len(found) != 1:
            fail(f"{wheel.name}: expected one top-level dist-info METADATA, found {found}")
        return archive.read(found[0])


def extract(wheel: Path) -> Path:
    target = wheel.with_name(wheel.name + SUFFIX)
    target.write_bytes(metadata_of(wheel))
    return target


def gh(*args: str) -> str:
    result = subprocess.run(["gh", *args], capture_output=True, text=True)
    if result.returncode != 0:
        fail(f"gh {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def backfill(repo: str | None, apply: bool) -> int:
    where = ["--repo", repo] if repo else []
    releases = json.loads(
        gh("release", "list", "--limit", "1000", "--json", "tagName,isDraft", *where)
    )
    missing: dict[str, list[str]] = {}
    for release in releases:
        if release["isDraft"]:
            continue
        tag = release["tagName"]
        assets = set(
            gh("release", "view", tag, "--json", "assets", "--jq", ".assets[].name", *where)
            .split()
        )
        wheels = sorted(
            name for name in assets
            if name.endswith(".whl")
            and name.split("-", 1)[0].lower() not in RETIRED
            and name + SUFFIX not in assets
        )
        if wheels:
            missing[tag] = wheels

    if not missing:
        print("Every indexed wheel already has its .metadata asset.")
        return 0
    for tag, wheels in missing.items():
        print(f"{tag}: {len(wheels)} wheel(s) without metadata")
        for wheel in wheels:
            print(f"  {wheel}")
    if not apply:
        print("\nNothing uploaded. Re-run with --apply.")
        return 0

    for tag, wheels in missing.items():
        with tempfile.TemporaryDirectory() as tmp:
            for wheel in wheels:
                gh("release", "download", tag, "--pattern", wheel, "--dir", tmp, *where)
            outputs = [str(extract(Path(tmp) / wheel)) for wheel in wheels]
            # No --clobber: this adds files and must never replace one.
            gh("release", "upload", tag, *outputs, *where)
        print(f"{tag}: uploaded {len(outputs)} .metadata file(s)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    extract_cmd = commands.add_parser("extract", help="Write <wheel>.metadata beside each wheel.")
    extract_cmd.add_argument("wheels", nargs="+", type=Path)

    backfill_cmd = commands.add_parser("backfill", help="Add .metadata to existing releases.")
    backfill_cmd.add_argument("--apply", action="store_true", help="Actually upload.")
    backfill_cmd.add_argument("--repo", help="owner/name, if not the current directory's.")

    args = parser.parse_args()
    if args.command == "extract":
        for wheel in args.wheels:
            print(f"  {extract(wheel).name}")
        return 0
    return backfill(args.repo, args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
