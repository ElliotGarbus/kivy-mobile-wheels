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

A wheel whose release also carries ``<wheel>.metadata`` (see
tools/wheel_metadata.py) gets PEP 658/714 attributes, so pip reads its
dependencies from that small file instead of downloading the whole wheel to
resolve. Without one the anchor is plain and pip falls back to the wheel, which
is how releases from before the metadata existed still behave.

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

# Projects that used to be published here and now ship mobile wheels on PyPI.
# Their GitHub Releases stay (old lock files pin those URLs directly), but they
# must not appear in the index or pip will keep preferring this copy.
RETIRED = {"pyjnius"}


def normalize(name: str) -> str:
    """PEP 503 normalized project name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def project_of(filename: str) -> str | None:
    """Project name from a wheel filename, or None if it isn't a wheel."""
    if not filename.endswith(".whl"):
        return None
    return filename.split("-", 1)[0]


def api(path: str) -> list[dict]:
    """Every page of a list endpoint.

    Paginated because the default page size is 30: without this the index would
    silently start dropping the oldest releases once there were more than that,
    and a wheel missing from the index looks identical to one never built.
    """
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    separator = "&" if "?" in path else "?"
    items: list[dict] = []
    page = 1
    while True:
        url = f"{API}{path}{separator}per_page=100&page={page}"
        request = Request(url, headers={"Accept": "application/vnd.github+json"})
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        with urlopen(request, timeout=60) as response:
            batch = json.load(response)
        if not isinstance(batch, list):
            return batch
        items.extend(batch)
        if len(batch) < 100:
            return items
        page += 1


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


# (filename, url#sha256, sha256 of its .metadata or None)
File = tuple[str, str, str | None]


def collect() -> dict[str, list[File]]:
    """{normalized project: [File, ...]} across all releases."""
    projects: dict[str, list[File]] = defaultdict(list)
    for release in api(f"/repos/{REPO}/releases"):
        if release.get("draft"):
            continue
        assets = release.get("assets", [])
        sums = next(
            (sha256sums(a["browser_download_url"]) for a in assets
             if a["name"] == "SHA256SUMS"),
            {},
        )
        by_name = {a["name"]: a for a in assets}

        def sha256(asset: dict) -> str:
            # The API digest is "sha256:<hex>" when present.
            digest = (asset.get("digest") or "").removeprefix("sha256:")
            return digest or sums.get(asset["name"], "")

        for asset in assets:
            name = asset["name"]
            project = project_of(name)
            if project is None:
                continue
            if normalize(project) in RETIRED:
                print(f"  skipping {name}: {project} is on PyPI", file=sys.stderr)
                continue
            digest = sha256(asset)
            if not digest:
                print(f"  skipping {name}: no sha256", file=sys.stderr)
                continue
            url = f"{asset['browser_download_url']}#sha256={digest}"
            metadata = metadata_sha(asset, by_name.get(name + ".metadata"), sha256)
            projects[normalize(project)].append((name, url, metadata))
    return projects


def metadata_sha(wheel: dict, asset: dict | None, sha256) -> str | None:
    """The hash of the wheel's ``.metadata`` asset, if it can be advertised.

    pip is never told the metadata's own URL: it fetches the wheel's URL plus
    ``.metadata``, so this only advertises an asset really served there. And
    pip fails the whole resolve on metadata whose hash does not match, so one
    with no recoverable hash is left out rather than advertised unverified —
    pip then just downloads the wheel, as it did before.
    """
    if asset is None:
        return None
    if asset["browser_download_url"] != wheel["browser_download_url"] + ".metadata":
        print(f"  {asset['name']}: not served at the wheel URL + .metadata", file=sys.stderr)
        return None
    digest = sha256(asset)
    if not digest:
        print(f"  {asset['name']}: no sha256, not advertised", file=sys.stderr)
        return None
    return digest


def anchor(name: str, url: str, metadata: str | None) -> str:
    # Both spellings: data-core-metadata is PEP 714's, and pip before 23.3
    # reads only PEP 658's original data-dist-info-metadata.
    attrs = (
        f' data-core-metadata="sha256={metadata}"'
        f' data-dist-info-metadata="sha256={metadata}"'
        if metadata
        else ""
    )
    return f'    <a href="{html.escape(url)}"{attrs}>{html.escape(name)}</a><br/>'


def write(output: Path, projects: dict[str, list[File]]) -> None:
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
        anchors = "\n".join(anchor(*file) for file in sorted(files))
        (directory / "index.html").write_text(
            f"<!DOCTYPE html>\n<html><body>\n{anchors}\n</body></html>\n",
            encoding="utf-8",
        )
        with_metadata = sum(1 for *_, metadata in files if metadata)
        print(f"  {project}: {len(files)} file(s), {with_metadata} with metadata")


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
