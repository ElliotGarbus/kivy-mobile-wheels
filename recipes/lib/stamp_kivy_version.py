#!/usr/bin/env python3
"""Give every Kivy dev build a version that names the commit it came from.

Kivy 3.0 is unreleased and its version is a literal: ``kivy/_version.py`` sets
MAJOR/MINOR/MICRO and appends ``.dev0`` for as long as ``RELEASE`` is False, and
``setup.py`` passes that straight to ``setup(version=...)``. So every commit on
master builds a wheel named exactly ``kivy-3.0.0.dev0-<tags>.whl``, and that one
fact is what makes following a moving upstream painful:

  * The release tag is derived from the wheel filename, so it collides.
  * Publishing then needs ``replace_assets``, which changes the sha256 of an
    asset consumers have already pinned in a lock file.
  * Two builds from different commits can never coexist in the index, so a new
    one cannot be offered without taking the old one away.

This rewrites the version to ``3.0.0.dev<YYYYMMDDHHMM>``, from the *commit's* own
committer date in UTC. Three properties are the point:

  * Deterministic — the same commit always produces the same version, so a
    rebuild is recognisably a rebuild and the append-only guard still means
    something. The build clock is deliberately not used; it would make every run
    a new version and every rebuild a silent fork.
  * Monotonic — later commits sort higher, so ``kivy>=3.0.0.dev0,<4``, which is
    what every kivyforge example already requires, resolves to the newest build.
  * Plain PEP 440, no local version segment. ``+g<sha>`` was the obvious
    alternative and breaks ``kivy.require()``: ``parse_kivy_version`` matches
    ``^([0-9]+)\\.([0-9]+)\\.([0-9]+?)(rc|a|b|\\.dev|\\.post)?([0-9]+)?$`` and
    raises on anything trailing the dev number. A long dev number is safe there —
    ``require()`` compares only the ``[3, 0, 0]`` triple and never reads the
    revision.

The sha and date also fill the ``_kivy_git_hash`` and ``_kivy_build_date``
placeholders, which exist for exactly this and which ``kivy/__init__.py`` prints
at startup. A running app then says ``Kivy: v3.0.0.dev202606221936, git-a933f85,
...``, which is otherwise impossible to recover from a device.

Only Kivy is stamped. pyjnius and pyobjus are pinned to commits too, but they
change rarely and carry notes demanding a human look before any bump, so there is
no version collision to solve and nothing here should touch them.

Usage:
    stamp_kivy_version.py <kivy-src>    patch a checkout in place; print version
    stamp_kivy_version.py --predict     print what the pinned commit would build

``--predict`` reads ``recipes/PINNED_REFS.toml`` and fetches ``_version.py`` at
that commit over HTTPS, so CI can know the release tag before spending a runner
on the build. Both modes share one implementation of the rule: two copies that
disagreed would produce a release tag that does not match the wheel inside it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn
from urllib.request import Request, urlopen

# Both Kivy pins, because one Kivy version has to mean one Kivy tree. The two
# entries exist so the platforms *can* move independently, but a build of each
# has to agree on the version or the index offers two different trees under one
# name.
KIVY_SECTIONS = ("ios.kivy", "android.kivy")

VERSION_FILE = Path("kivy") / "_version.py"
PINS = Path(__file__).resolve().parent.parent / "PINNED_REFS.toml"

DEV0 = "__version__ += '.dev0'"
GIT_HASH = re.compile(r"^_kivy_git_hash\s*=\s*(['\"])\1\s*$", re.M)
BUILD_DATE = re.compile(r"^_kivy_build_date\s*=\s*(['\"])\1\s*$", re.M)


def fail(message: str) -> NoReturn:
    sys.exit(f"stamp_kivy_version: {message}")


def stamp(when: datetime) -> str:
    """``YYYYMMDDHHMM`` in UTC.

    Minute resolution, because two commits in the same minute would collide and
    that is rare enough to notice (the append-only guard refuses the second) but
    fine enough that the number reads as a date.
    """
    return when.astimezone(timezone.utc).strftime("%Y%m%d%H%M")


def parse_version_file(text: str) -> tuple[str, bool]:
    """``("3.0.0", RELEASE)`` out of ``_version.py``'s literals.

    Read rather than imported: the file is exec'd by Kivy's own setup.py and
    importing it here would need the package on sys.path, which for a fresh
    cross-build checkout it is not.
    """
    parts = []
    for key in ("MAJOR", "MINOR", "MICRO"):
        found = re.search(rf"^{key}\s*=\s*(\d+)\s*$", text, re.M)
        if found is None:
            fail(
                f"no `{key} = <int>` in {VERSION_FILE}. Upstream changed how the "
                "version is declared; this script has to be updated to match."
            )
        parts.append(found.group(1))

    released = re.search(r"^RELEASE\s*=\s*(True|False)\s*$", text, re.M)
    if released is None:
        fail(f"no `RELEASE = True|False` in {VERSION_FILE}.")
    return ".".join(parts), released.group(1) == "True"


def git(src: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(src), *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        fail(f"git {' '.join(args)} failed in {src}: {result.stderr.strip()}")
    return result.stdout.strip()


def patch(src: Path) -> str:
    """Stamp ``src``'s ``_version.py`` from its own HEAD. Returns the version."""
    path = src / VERSION_FILE
    if not path.is_file():
        fail(f"{path} does not exist; is {src} a Kivy checkout?")

    # Restore before patching. The Android recipe reuses one checkout across
    # ABIs, so this can run against a file an earlier run already stamped, and
    # patching a patched file would either fail the DEV0 check or compound.
    # Checking the file out first makes the input the committed one every time.
    git(src, "checkout", "--", str(VERSION_FILE))

    text = path.read_text(encoding="utf-8")
    base, released = parse_version_file(text)

    sha = git(src, "rev-parse", "HEAD")
    committed = datetime.fromtimestamp(
        int(git(src, "log", "-1", "--format=%ct", "HEAD")), timezone.utc
    )

    if released:
        # Upstream tagged a real release: the version is already unique and
        # already meaningful, and appending a dev stamp would make it *lower*
        # than the release it was built from. Leave it alone.
        print(f"  RELEASE is True; leaving version {base} untouched")
        return base

    occurrences = text.count(DEV0)
    if occurrences != 1:
        fail(
            f"expected exactly one `{DEV0}` in {VERSION_FILE}, found "
            f"{occurrences}. Upstream changed how the dev suffix is applied; "
            "update this script rather than guessing."
        )

    version = f"{base}.dev{stamp(committed)}"
    text = text.replace(DEV0, f"__version__ += '.dev{stamp(committed)}'", 1)

    for label, pattern, value in (
        ("_kivy_git_hash", GIT_HASH, sha),
        ("_kivy_build_date", BUILD_DATE, committed.isoformat()),
    ):
        text, count = pattern.subn(f"{label} = '{value}'", text, count=1)
        if count != 1:
            fail(
                f"no empty `{label}` assignment in {VERSION_FILE}; upstream "
                "changed the placeholders this script fills."
            )

    path.write_text(text, encoding="utf-8", newline="\n")

    # Exec the result and check it agrees. The whole scheme rests on the wheel's
    # version being this string — the release tag is derived from it — so a
    # silently botched substitution has to fail here, not at upload time.
    namespace: dict[str, object] = {}
    exec(compile(text, str(path), "exec"), namespace)
    if namespace.get("__version__") != version:
        fail(
            f"patched {VERSION_FILE} evaluates to "
            f"{namespace.get('__version__')!r}, expected {version!r}"
        )

    print(f"  version {version} (git-{sha[:7]}, {committed.isoformat()})")
    return version


def get(url: str) -> bytes:
    request = Request(url, headers={"Accept": "application/vnd.github+json"})
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urlopen(request, timeout=60) as response:
        return response.read()


def slug(repo: str) -> str:
    return repo.rstrip("/").removeprefix("https://github.com/").removesuffix(".git")


def pinned_kivy() -> tuple[str, str]:
    """``(repo, commit)`` for Kivy, insisting both platform pins agree."""
    with open(PINS, "rb") as handle:
        data = tomllib.load(handle)

    resolved = {}
    for section in KIVY_SECTIONS:
        platform, project = section.split(".")
        entry = data.get(platform, {}).get(project)
        if not entry:
            fail(f"no [{section}] in {PINS}")
        resolved[section] = (entry["repo"], entry["commit"])

    if len(set(resolved.values())) != 1:
        detail = "; ".join(f"{k} = {v[1][:12]}" for k, v in resolved.items())
        fail(
            f"the Kivy pins disagree ({detail}). One version number cannot "
            "describe two trees — reconcile them, or predict a single section."
        )
    return next(iter(resolved.values()))


def predict(repo: str, commit: str) -> str:
    """The version ``commit`` would build, without checking anything out."""
    text = get(
        f"https://raw.githubusercontent.com/{slug(repo)}/{commit}/{VERSION_FILE.as_posix()}"
    ).decode()
    base, released = parse_version_file(text)
    if released:
        return base

    payload = json.loads(get(f"https://api.github.com/repos/{slug(repo)}/commits/{commit}"))
    committed = datetime.fromisoformat(payload["commit"]["committer"]["date"])
    return f"{base}.dev{stamp(committed)}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "src",
        nargs="?",
        type=Path,
        help="Kivy checkout to stamp in place.",
    )
    parser.add_argument(
        "--predict",
        action="store_true",
        help="Print the version the pinned commit would build; touch nothing.",
    )
    parser.add_argument(
        "--commit",
        help="With --predict, use this commit instead of the pinned one.",
    )
    parser.add_argument(
        "--repo",
        help="With --predict --commit, the repo to read it from.",
    )
    args = parser.parse_args()

    if args.predict:
        repo, commit = pinned_kivy()
        print(predict(args.repo or repo, args.commit or commit))
        return 0

    if args.src is None:
        parser.error("give a Kivy checkout to stamp, or --predict")
    print(f"Stamping {args.src / VERSION_FILE}")
    patch(args.src)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
