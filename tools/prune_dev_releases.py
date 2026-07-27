#!/usr/bin/env python3
"""Delete superseded dev releases, keeping the newest few of each release line.

Every Kivy dev build now publishes under its own version (see
recipes/lib/stamp_kivy_version.py), which is what lets a new build appear without
disturbing the old one. The cost is that releases accumulate for as long as Kivy
3.0 stays unreleased, and each one is two or three wheels of tens of megabytes.

**This deletes published artifacts, and a lock file that pinned one of them will
stop resolving.** That is the trade being made: dev builds are not promised to
last forever, and anything that needs to keep working should be re-locked onto a
newer build or have its wheels vendored. Releases outside a dev line — Kivy 2.3.1,
pyjnius, pyobjus — are never touched, because those *are* promised.

Guard rails, in order of how much they matter:

  * Only versions carrying a ``.dev`` segment are candidates at all.
  * Grouping is per project, per platform, per ``X.Y.Z`` line, and the newest
    ``--keep`` of each group survive.
  * Nothing is deleted without ``--apply``; the default is a report.
  * A run that wants to delete more than ``--max-delete`` stops instead. If the
    grouping is ever wrong, the blast radius is a refused run and not the index.

Usage:
    prune_dev_releases.py                 report what would go
    prune_dev_releases.py --apply         delete it, tags included
    prune_dev_releases.py --keep 3        keep fewer per line

Needs the gh CLI, authenticated. Reads and writes releases only.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from typing import NoReturn

# kivy-android-3.0.0.dev202607271534, pyobjus-ios-1.2.4.dev0, kivy-android-2.3.1
TAG = re.compile(r"^(?P<project>.+)-(?P<platform>android|ios)-(?P<version>.+)$")

# Only dev versions are candidates, and within one release line the dev number is
# a UTC timestamp, so plain integer order is chronological order. No general
# version comparison is needed or wanted here.
DEV = re.compile(r"^(?P<release>\d+(?:\.\d+)*)\.dev(?P<dev>\d+)$")


def fail(message: str) -> NoReturn:
    sys.exit(f"prune_dev_releases: {message}")


def gh(*args: str) -> str:
    result = subprocess.run(["gh", *args], capture_output=True, text=True)
    if result.returncode != 0:
        fail(f"gh {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep",
        type=int,
        default=5,
        help="Dev releases to keep per project/platform/release line (default 5).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete. Without this, nothing is touched.",
    )
    parser.add_argument(
        "--max-delete",
        type=int,
        default=20,
        help="Refuse to delete more than this in one run (default 20).",
    )
    parser.add_argument("--repo", help="owner/name, if not the current directory's.")
    args = parser.parse_args()
    if args.keep < 1:
        fail("--keep must be at least 1; this is not a tool for emptying the index")

    where = ["--repo", args.repo] if args.repo else []
    releases = json.loads(
        gh("release", "list", "--limit", "1000", "--json", "tagName,isDraft", *where)
    )

    groups: dict[tuple, list[tuple[int, str]]] = {}
    for release in releases:
        if release["isDraft"]:
            continue
        tag = TAG.match(release["tagName"])
        if tag is None:
            continue
        dev = DEV.match(tag["version"])
        if dev is None:
            continue
        groups.setdefault(
            (tag["project"], tag["platform"], dev["release"]), []
        ).append((int(dev["dev"]), release["tagName"]))

    doomed: list[str] = []
    for (project, platform, release), candidates in sorted(groups.items()):
        candidates.sort(reverse=True)
        keeping, dropping = candidates[: args.keep], candidates[args.keep :]
        print(f"{project} {platform} {release}: {len(candidates)} dev release(s)")
        for _, tag in keeping:
            print(f"  keep  {tag}")
        for _, tag in dropping:
            print(f"  DROP  {tag}")
        doomed.extend(tag for _, tag in dropping)

    if not doomed:
        print("\nNothing is superseded; nothing to do.")
        return 0

    if len(doomed) > args.max_delete:
        fail(
            f"{len(doomed)} releases would be deleted, over the --max-delete "
            f"limit of {args.max_delete}. Check the grouping above is right, then "
            "raise the limit deliberately."
        )

    if not args.apply:
        print(f"\n{len(doomed)} release(s) would be deleted. Re-run with --apply.")
        return 0

    for tag in doomed:
        # --cleanup-tag: a tag with no release behind it is litter that still
        # looks like a version someone could check out.
        gh("release", "delete", tag, "--cleanup-tag", "--yes", *where)
        print(f"deleted {tag}")
    print(f"\n{len(doomed)} release(s) deleted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
