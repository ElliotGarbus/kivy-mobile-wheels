#!/usr/bin/env python3
"""Give a pyobjus build from an untagged commit a version that is not a release's.

pyobjus only bumps ``__version__`` when it tags a release, so master after
``v1.2.4`` still declares ``1.2.4``. Building it as-is would publish different
code under a version already on the index — the release tag collides, and the
append-only guard refuses it, or ``replace_assets`` silently swaps the bytes
every ``pyobjus==1.2.4`` lock file pinned.

So a commit that is not the tag ``v<__version__>`` builds as
``<major>.<minor>.<micro+1>.dev<YYYYMMDDHHMM>``, from the commit's own committer
date in UTC — the same rule, and for the same reasons, as
``stamp_kivy_version.py``: deterministic per commit, monotonic along master,
and sorting above the release it follows but below the next one. A tagged commit
is left alone.

Being a dev version, pip only selects it when the requirement opts in to
pre-releases, e.g. ``pyobjus>=1.2.5.dev0``. A plain ``pyobjus`` keeps resolving
to the last tagged release.

Usage:
    stamp_pyobjus_version.py <pyobjus-src>    patch a checkout in place; print version
"""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn

VERSION_FILE = Path("pyobjus") / "__init__.py"
# setup.py takes the first line starting with __version__ and splits on "'".
VERSION_LINE = re.compile(r"^__version__ = '(?P<version>\d+\.\d+\.\d+)'$", re.M)


def fail(message: str) -> NoReturn:
    sys.exit(f"stamp_pyobjus_version: {message}")


def git(src: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(src), *args], capture_output=True, text=True
    )
    if result.returncode != 0:
        fail(f"git {' '.join(args)} failed in {src}: {result.stderr.strip()}")
    return result.stdout.strip()


def patch(src: Path) -> str:
    path = src / VERSION_FILE
    if not path.is_file():
        fail(f"{path} does not exist; is {src} a pyobjus checkout?")

    # The checkout is reused between runs; stamp the committed file, not a
    # previously stamped one.
    git(src, "checkout", "--", str(VERSION_FILE))
    text = path.read_text(encoding="utf-8")

    found = VERSION_LINE.findall(text)
    if len(found) != 1:
        fail(
            f"expected exactly one `__version__ = 'X.Y.Z'` line in {VERSION_FILE}, "
            f"found {len(found)}. Upstream changed how the version is declared; "
            "update this script rather than guessing."
        )
    base = found[0]

    if f"v{base}" in git(src, "tag", "--points-at", "HEAD").split():
        print(f"  HEAD is tag v{base}; leaving version {base} untouched")
        return base

    committed = datetime.fromtimestamp(
        int(git(src, "log", "-1", "--format=%ct", "HEAD")), timezone.utc
    )
    major, minor, micro = (int(part) for part in base.split("."))
    version = f"{major}.{minor}.{micro + 1}.dev{committed.strftime('%Y%m%d%H%M')}"

    text = VERSION_LINE.sub(f"__version__ = '{version}'", text, count=1)
    path.write_text(text, encoding="utf-8", newline="\n")

    sha = git(src, "rev-parse", "HEAD")
    print(f"  version {version} (git-{sha[:7]}, {committed.isoformat()})")
    return version


def main() -> int:
    if len(sys.argv) != 2:
        fail("usage: stamp_pyobjus_version.py <pyobjus-src>")
    src = Path(sys.argv[1])
    print(f"Stamping {src / VERSION_FILE}")
    patch(src)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
