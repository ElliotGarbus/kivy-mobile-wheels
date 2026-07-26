#!/usr/bin/env python3
"""Generate a PEP 503 "simple" index from this repo's release assets.

The index is a handful of static HTML files pointing at GitHub Release asset
URLs, so there is no server and no moving parts: list the assets, emit anchors,
deploy to Pages.

Every anchor carries a ``#sha256=`` fragment, which is what makes the index
usable for locking — pip verifies it on download, and kivyforge records it in
the lock file. Hashes come from the GitHub API's asset ``digest`` when present,
otherwise from a ``SHA256SUMS`` asset published alongside the wheels. A wheel
with neither is skipped rather than published unverified: an unpinnable wheel
in a lock file is worse than a missing one.

Usage:
    GH_TOKEN=... python index-gen/generate_index.py --output public/simple
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from urllib.request import Request, urlopen

REPO = os.environ.get("GITHUB_REPOSITORY", "ElliotGarbus/kivy-mobile-wheels")
API = "https://api.github.com"


def normalize(name: str) -> str:
    """PEP 503 normalized project name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def project_of(filename: str) -> str | None:
    """Project name from a wheel filename, or None if it isn't a wheel."""
    if not filename.endswith(".whl"):
        return None
    return filename.split("-", 1)[0]


def api(path: str) -> list[dict]:
    request = Request(f"{API}{path}", headers={"Accept": "application/vnd.github+json"})
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urlopen(request, timeout=60) as response:
        return json.load(response)


def sha256sums(url: str) -> dict[str, str]:
    """Parse a ``SHA256SUMS`` asset into {filename: sha256}."""
    try:
        with urlopen(url, timeout=60) as response:
            text = response.read().decode()
    except OSError as exc:
        print(f"  warning: could not read SHA256SUMS ({exc})", file=sys.stderr)
        return {}
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2:
            digest, name = parts
            out[name.lstrip("*")] = digest
    return out


def collect() -> dict[str, list[tuple[str, str]]]:
    """{normalized project: [(filename, url#sha256), ...]} across all releases."""
    projects: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for release in api(f"/repos/{REPO}/releases"):
        if release.get("draft"):
            continue
        assets = release.get("assets", [])
        sums = next(
            (sha256sums(a["browser_download_url"]) for a in assets
             if a["name"] == "SHA256SUMS"),
            {},
        )
        for asset in assets:
            name = asset["name"]
            project = project_of(name)
            if project is None:
                continue
            # The API digest is "sha256:<hex>" when present.
            digest = (asset.get("digest") or "").removeprefix("sha256:")
            digest = digest or sums.get(name, "")
            if not digest:
                print(f"  skipping {name}: no sha256", file=sys.stderr)
                continue
            url = f"{asset['browser_download_url']}#sha256={digest}"
            projects[normalize(project)].append((name, url))
    return projects


def write(output: Path, projects: dict[str, list[tuple[str, str]]]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    links = "\n".join(
        f'    <a href="{name}/">{name}</a><br/>' for name in sorted(projects)
    )
    (output / "index.html").write_text(
        f"<!DOCTYPE html>\n<html><body>\n{links}\n</body></html>\n",
        encoding="utf-8",
    )
    for project, files in sorted(projects.items()):
        directory = output / project
        directory.mkdir(parents=True, exist_ok=True)
        anchors = "\n".join(
            f'    <a href="{html.escape(url)}">{html.escape(name)}</a><br/>'
            for name, url in sorted(files)
        )
        (directory / "index.html").write_text(
            f"<!DOCTYPE html>\n<html><body>\n{anchors}\n</body></html>\n",
            encoding="utf-8",
        )
        print(f"  {project}: {len(files)} file(s)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("public/simple"))
    args = parser.parse_args()

    print(f"Generating index for {REPO} -> {args.output}")
    projects = collect()
    if not projects:
        # Not an error: before the first release there is genuinely nothing to
        # index, and an empty index is a valid one.
        print("  no wheels found in any release")
    write(args.output, projects)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
