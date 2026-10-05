#!/usr/bin/env python3
"""Resolve every wheel in the published index with pip, exactly as a consumer would.

Generating an index and deploying it proves the HTML exists, not that pip can
use it. Everything between those two facts is untested otherwise: a wrong
platform tag, a hash that does not match the asset, a release whose files were
replaced, a project name that does not normalize to what consumers ask for.
Each of those produces a perfectly valid-looking index that fails at
``kivyforge lock`` time, which is the worst place to find out.

So this reads the deployed index, and for every wheel in it asks pip to resolve
and download that exact wheel by name, version and platform tag. pip verifies
the ``#sha256`` fragment on download, so a hash mismatch fails here too.

Where an anchor advertises PEP 658 metadata, the ``.metadata`` file is fetched
and hashed too: pip rejects a mismatch, and that fails the whole resolve rather
than falling back to the wheel. For each wheel pip downloads, its own METADATA
must also be what the ``.metadata`` file says, or consumers would lock against
dependencies the installed wheel does not have.

The platform arguments are derived from each wheel's own filename, so this needs
no list of expected targets and cannot drift from what is actually published.

By default only the newest version of each (project, interpreter, platform) is
resolved. Every Kivy dev build now publishes under its own version — see
recipes/lib/stamp_kivy_version.py — so the index grows without bound, and
downloading all of it on every publish would make this job slower every week
while re-testing bytes that have not changed since they were uploaded. A version
this cannot order is always verified, so the shortcut can never skip something by
accident. ``--all`` resolves everything.

Usage:
    verify_index.py --base-url https://elliotgarbus.github.io/kivy-mobile-wheels
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
import tempfile
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import urlopen


class Links(HTMLParser):
    """Every anchor on a PEP 503 page as {attribute: value}, in document order."""

    def __init__(self) -> None:
        super().__init__()
        self.anchors: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        found = {name: value for name, value in attrs if value is not None}
        if found.get("href"):
            self.anchors.append(found)


def fetch(url: str, attempts: int = 6) -> bytes:
    """GET ``url``, retrying — a fresh Pages deployment takes a moment to serve."""
    delay = 5
    for attempt in range(1, attempts + 1):
        try:
            with urlopen(url, timeout=60) as response:
                return response.read()
        except (HTTPError, URLError) as exc:
            if attempt == attempts:
                raise
            print(f"  {url} not ready ({exc}); retrying in {delay}s")
            import time

            time.sleep(delay)
    raise AssertionError("unreachable")


def links(url: str) -> list[dict[str, str]]:
    parser = Links()
    parser.feed(fetch(url).decode())
    return parser.anchors


def advertised_metadata(anchor: dict[str, str]) -> tuple[str | None, str | None]:
    """(sha256 the index advertises for this wheel's metadata, or None; problem).

    The two attribute spellings are read by different pip versions, so if they
    disagree some consumers get a hash the file cannot match.
    """
    values = {
        anchor[name]
        for name in ("data-core-metadata", "data-dist-info-metadata")
        if name in anchor
    }
    if not values:
        return None, None
    if len(values) > 1:
        return None, f"metadata attributes disagree: {sorted(values)}"
    (value,) = values
    if not value.startswith("sha256="):
        return None, f"metadata attribute carries no sha256: {value!r}"
    return value.removeprefix("sha256="), None


def check_metadata(href: str, expected: str) -> str | None:
    """Fetch ``<wheel URL>.metadata`` as pip would; a problem, or None if sound."""
    url = href.split("#")[0] + ".metadata"
    try:
        body = fetch(url, attempts=3)
    except (HTTPError, URLError) as exc:
        return f"{url} unreachable ({exc})"
    actual = hashlib.sha256(body).hexdigest()
    if actual != expected:
        return f"{url} has sha256 {actual}, index says {expected}"
    return None


def wheel_metadata(wheel: Path) -> bytes | None:
    """The wheel's top-level ``*.dist-info/METADATA``, or None if not exactly one."""
    with zipfile.ZipFile(wheel) as archive:
        found = [
            name for name in archive.namelist()
            if re.fullmatch(r"[^/]+\.dist-info/METADATA", name)
        ]
        return archive.read(found[0]) if len(found) == 1 else None


WHEEL = re.compile(
    r"^(?P<name>[^-]+)-(?P<version>[^-]+)"
    r"-(?P<python>[^-]+)-(?P<abi>[^-]+)-(?P<platform>.+)\.whl$"
)

# Only the shapes this index actually publishes: 2.3.1, 1.7.0, 3.0.0.dev202607271534.
VERSION = re.compile(r"^(?P<release>\d+(?:\.\d+)*)(?:\.dev(?P<dev>\d+))?$")


def order(version: str) -> tuple | None:
    """A sort key for ``version``, or None if this cannot order it confidently.

    Deliberately not ``packaging.version``: this script runs on nothing but the
    standard library, and being wrong about an ordering here would silently skip
    a wheel. Anything unrecognised returns None and gets verified regardless.
    """
    parsed = VERSION.match(version)
    if parsed is None:
        return None
    release = tuple(int(part) for part in parsed["release"].split("."))
    dev = parsed["dev"]
    # A dev release sorts below the release it leads to: 3.0.0.dev1 < 3.0.0.
    return release, 0 if dev else 1, int(dev) if dev else 0


def newest_only(wheels: list[str]) -> tuple[list[str], list[str]]:
    """Split wheels into (to verify, skipped as superseded).

    A wheel supersedes another only within one release line: the group key
    includes the ``X.Y.Z`` release, so 3.0.0 dev builds collapse to the newest
    while Kivy 2.3.1 stays verified in its own right. Both lines are supported
    and consumed, and "there is a newer major" is not a reason to stop checking
    the older one.

    Platform slices group separately too. An iOS build publishes three, and a
    device wheel that resolves says nothing about the simulator ones.
    """
    groups: dict[tuple, list[tuple[tuple, str]]] = {}
    keep: list[str] = []
    for wheel in wheels:
        parts = WHEEL.match(wheel)
        if parts is None:
            keep.append(wheel)
            continue
        key = order(parts["version"])
        if key is None:
            keep.append(wheel)
            continue
        release = key[0]
        groups.setdefault(
            (
                parts["name"].lower(),
                release,
                parts["python"],
                parts["abi"],
                parts["platform"],
            ),
            [],
        ).append((key, wheel))

    skipped: list[str] = []
    for candidates in groups.values():
        candidates.sort()
        keep.append(candidates[-1][1])
        skipped.extend(wheel for _, wheel in candidates[:-1])
    return sorted(keep), sorted(skipped)


def pip_download(
    wheel: str, index_url: str, destination: Path
) -> subprocess.CompletedProcess[str]:
    """Ask pip for one specific wheel, cross-platform, from the index only."""
    parts = WHEEL.match(wheel)
    if parts is None:
        raise ValueError(f"not a wheel filename: {wheel}")

    # --only-binary is mandatory whenever --platform is used, and --no-deps
    # keeps this a test of *this* index rather than of PyPI reachability.
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "download",
            "--no-deps",
            "--no-cache-dir",
            "--pre",
            "--only-binary=:all:",
            "--index-url",
            index_url,
            "--platform",
            parts["platform"],
            "--implementation",
            "cp",
            "--python-version",
            parts["abi"].removeprefix("cp"),
            "--abi",
            parts["abi"],
            "--dest",
            str(destination),
            f"{parts['name']}=={parts['version']}",
        ],
        capture_output=True,
        text=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        required=True,
        help="Root of the deployed site, without /simple.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Resolve every published version, not just the newest of each.",
    )
    args = parser.parse_args()

    base = args.base_url.rstrip("/") + "/"
    index_url = urljoin(base, "simple/")

    print(f"Verifying {index_url}")
    projects = [anchor["href"].strip("/") for anchor in links(index_url)]
    if not projects:
        print("  no projects in the index; nothing to verify", file=sys.stderr)
        return 1
    print(f"  projects: {', '.join(projects)}")

    wheels: list[str] = []
    # {wheel filename: advertised metadata sha256}
    metadata: dict[str, str] = {}
    failed: list[str] = []
    for project in projects:
        page = urljoin(index_url, f"{project}/")
        for anchor in links(page):
            href = anchor["href"]
            filename = href.split("#")[0].rsplit("/", 1)[-1]
            if not filename.endswith(".whl"):
                continue
            wheels.append(filename)
            # Every advertised .metadata is checked, superseded wheels included:
            # it is a few KB each, and a bad one fails every resolve that sees
            # that wheel as a candidate, whether or not it ends up chosen.
            expected, problem = advertised_metadata(anchor)
            if problem is None and expected is not None:
                problem = check_metadata(urljoin(page, href), expected)
            if problem is not None:
                failed.append(f"{filename} metadata")
                print(f"  FAIL {filename}: {problem}", file=sys.stderr)
            elif expected is not None:
                metadata[filename] = expected
    if not wheels:
        print("  index lists no wheels; nothing to verify", file=sys.stderr)
        return 1
    print(f"  {len(metadata)} of {len(wheels)} wheels advertise sound metadata")

    if args.all:
        skipped = []
    else:
        wheels, skipped = newest_only(wheels)
    if skipped:
        print(f"  {len(skipped)} superseded wheel(s) skipped; --all includes them")

    resolved = 0
    with tempfile.TemporaryDirectory() as tmp:
        destination = Path(tmp)
        for wheel in sorted(wheels):
            result = pip_download(wheel, index_url, destination)
            if result.returncode == 0 and (destination / wheel).exists():
                resolved += 1
                # The hash proves the .metadata file is the one the index
                # meant; only the wheel itself can prove it is the right text.
                # pip resolves from the file and installs the wheel, so a
                # mismatch means a lock built on dependencies the wheel lacks.
                if wheel in metadata:
                    inside = wheel_metadata(destination / wheel)
                    if inside is None or hashlib.sha256(inside).hexdigest() != metadata[wheel]:
                        failed.append(f"{wheel} metadata")
                        print(
                            f"  FAIL {wheel}: .metadata differs from the wheel's own METADATA",
                            file=sys.stderr,
                        )
                        continue
                print(f"  ok   {wheel}")
                continue
            failed.append(wheel)
            print(f"  FAIL {wheel}", file=sys.stderr)
            for line in (result.stdout + result.stderr).splitlines():
                if line.strip():
                    print(f"    {line}", file=sys.stderr)

    print(f"\n{resolved} of {len(wheels)} wheels resolved from the index")
    if failed:
        print(
            "verify_index: the index is published but not usable. A consumer "
            "would hit this at lock time.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
