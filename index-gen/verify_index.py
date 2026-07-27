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

The platform arguments are derived from each wheel's own filename, so this needs
no list of expected targets and cannot drift from what is actually published.

Usage:
    verify_index.py --base-url https://elliotgarbus.github.io/kivy-mobile-wheels
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import urlopen


class Links(HTMLParser):
    """Every ``href`` on a PEP 503 page, in document order."""

    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        for name, value in attrs:
            if name == "href" and value:
                self.hrefs.append(value)


def fetch(url: str, attempts: int = 6) -> str:
    """GET ``url``, retrying — a fresh Pages deployment takes a moment to serve."""
    delay = 5
    for attempt in range(1, attempts + 1):
        try:
            with urlopen(url, timeout=60) as response:
                return response.read().decode()
        except (HTTPError, URLError) as exc:
            if attempt == attempts:
                raise
            print(f"  {url} not ready ({exc}); retrying in {delay}s")
            import time

            time.sleep(delay)
    raise AssertionError("unreachable")


def links(url: str) -> list[str]:
    parser = Links()
    parser.feed(fetch(url))
    return parser.hrefs


WHEEL = re.compile(
    r"^(?P<name>[^-]+)-(?P<version>[^-]+)"
    r"-(?P<python>[^-]+)-(?P<abi>[^-]+)-(?P<platform>.+)\.whl$"
)


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
    args = parser.parse_args()

    base = args.base_url.rstrip("/") + "/"
    index_url = urljoin(base, "simple/")

    print(f"Verifying {index_url}")
    projects = [href.strip("/") for href in links(index_url)]
    if not projects:
        print("  no projects in the index; nothing to verify", file=sys.stderr)
        return 1
    print(f"  projects: {', '.join(projects)}")

    wheels: list[str] = []
    for project in projects:
        page = urljoin(index_url, f"{project}/")
        for href in links(page):
            filename = href.split("#")[0].rsplit("/", 1)[-1]
            if filename.endswith(".whl"):
                wheels.append(filename)
    if not wheels:
        print("  index lists no wheels; nothing to verify", file=sys.stderr)
        return 1

    failed = []
    with tempfile.TemporaryDirectory() as tmp:
        destination = Path(tmp)
        for wheel in sorted(wheels):
            result = pip_download(wheel, index_url, destination)
            if result.returncode == 0 and (destination / wheel).exists():
                print(f"  ok   {wheel}")
                continue
            failed.append(wheel)
            print(f"  FAIL {wheel}", file=sys.stderr)
            for line in (result.stdout + result.stderr).splitlines():
                if line.strip():
                    print(f"    {line}", file=sys.stderr)

    print(f"\n{len(wheels) - len(failed)} of {len(wheels)} wheels resolved from the index")
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
