#!/usr/bin/env python3
"""Notice when kivy/kivy master has moved past the pinned commit.

Kivy 3.0 is developed on master and released from nowhere, so the only way to
track it is to look. Nothing here talks *to* kivy/kivy: subscribing to another
repository's pushes needs webhook admin on that repository, and
``repository_dispatch`` would need someone upstream to send it. So this polls —
``git ls-remote``, which needs no token and no API quota — and everything it
writes, it writes here.

What it does with a difference is deliberately conservative. PINNED_REFS.toml
opens by saying that bumping a pin is "a deliberate, reviewed commit to this
file", because a recipe that silently follows a branch cannot rebuild the same
wheel twice. A bot that rewrote pins on its own would break that promise, so this
only prepares the change: it edits the pins, and the workflow puts that on a
branch as a pull request for a human to merge. The tedious half — finding the
sha, editing two entries, working out what the new version will be and what
changed upstream — is what gets automated.

Only the two Kivy entries are watched. pyjnius is pinned to an unmerged fork and
pyobjus to a commit that deliberately avoids an upstream PR; both notes say to
re-check before bumping, and neither is something a schedule should touch.

Usage:
    watch_upstream.py                     report drift; change nothing
    watch_upstream.py --write             also rewrite the pins in place
    watch_upstream.py --body-file PATH    write a pull request body as markdown

In CI it also writes ``drifted``, ``sha``, ``short``, ``version`` and ``title``
to ``$GITHUB_OUTPUT``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
from datetime import datetime
from pathlib import Path
from typing import NoReturn
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
PINS = ROOT / "recipes" / "PINNED_REFS.toml"
STAMPER = ROOT / "recipes" / "lib" / "stamp_kivy_version.py"

# Kept in step on purpose: one Kivy version has to describe one tree, or the
# index offers two different trees under the same name.
WATCHED = ("ios.kivy", "android.kivy")

SECTION = re.compile(r"^\[(?P<name>[^\]]+)\]\s*$")
COMMIT = re.compile(r"^(?P<prefix>commit\s*=\s*)\"(?P<sha>[0-9a-f]{40})\"\s*$")

# The compare endpoint returns at most 250 commits and a PR body has a size
# limit, so the list is truncated with a count of what was left out.
MAX_LISTED = 25


def fail(message: str) -> NoReturn:
    sys.exit(f"watch_upstream: {message}")


def api(path: str) -> dict:
    request = Request(
        f"https://api.github.com{path}",
        headers={"Accept": "application/vnd.github+json"},
    )
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urlopen(request, timeout=60) as response:
        return json.load(response)


def slug(repo: str) -> str:
    return repo.rstrip("/").removeprefix("https://github.com/").removesuffix(".git")


def watched_pins() -> tuple[str, str, str]:
    """``(repo, ref, commit)`` shared by both Kivy entries."""
    with open(PINS, "rb") as handle:
        data = tomllib.load(handle)

    resolved = {}
    for section in WATCHED:
        platform, project = section.split(".")
        entry = data.get(platform, {}).get(project)
        if not entry:
            fail(f"no [{section}] in {PINS}")
        resolved[section] = (entry["repo"], entry["ref"], entry["commit"])

    if len(set(resolved.values())) != 1:
        detail = "; ".join(f"[{k}] {v[2][:12]}" for k, v in resolved.items())
        fail(
            f"the Kivy pins disagree ({detail}). Bumping them together would "
            "hide whatever split them — reconcile them by hand first."
        )
    return next(iter(resolved.values()))


def remote_head(repo: str, ref: str) -> str:
    """The sha ``refs/heads/<ref>`` points at, straight from the remote."""
    result = subprocess.run(
        ["git", "ls-remote", repo, f"refs/heads/{ref}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        fail(f"git ls-remote {repo} {ref} failed: {result.stderr.strip()}")
    if not result.stdout.strip():
        fail(f"{repo} has no branch {ref!r}; is the pin's `ref` still right?")
    return result.stdout.split()[0]


def predicted_version(repo: str, commit: str) -> str:
    """What the recipes would build from ``commit``, per stamp_kivy_version.py."""
    result = subprocess.run(
        [sys.executable, str(STAMPER), "--predict", "--repo", repo, "--commit", commit],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        fail(f"could not predict the version: {result.stderr.strip()}")
    return result.stdout.strip()


def rewrite_pins(new_sha: str) -> None:
    """Point every watched section's ``commit`` at ``new_sha``, touching nothing else.

    Line-oriented rather than a TOML round-trip: tomllib cannot write, and every
    other writer reflows the file. The notes in here are the most valuable part
    of it, and a diff that also rewrapped them would be unreviewable.
    """
    lines = PINS.read_text(encoding="utf-8").splitlines(keepends=True)
    current = None
    changed = 0

    for index, line in enumerate(lines):
        header = SECTION.match(line)
        if header:
            current = header.group("name")
            continue
        if current not in WATCHED:
            continue
        commit = COMMIT.match(line)
        if commit:
            lines[index] = f'{commit.group("prefix")}"{new_sha}"\n'
            changed += 1

    if changed != len(WATCHED):
        fail(
            f"rewrote {changed} commit lines, expected {len(WATCHED)}. "
            f"{PINS.name}'s layout changed; check it by hand."
        )
    # newline="\n" because .gitattributes declares this file LF: without it a
    # run on Windows would rewrite every line ending and bury the two-line diff.
    PINS.write_text("".join(lines), encoding="utf-8", newline="\n")


def dated(commit: dict | None) -> str:
    """`` (2026-06-22)`` for a commit payload, or nothing if it is unavailable."""
    if not commit:
        return ""
    iso = commit["commit"]["committer"]["date"]
    return f" ({datetime.fromisoformat(iso).strftime('%Y-%m-%d')})"


def body(
    repo: str,
    ref: str,
    old: str,
    new: str,
    version: str,
    comparison: dict,
) -> str:
    commits = comparison.get("commits", [])
    listed = commits[-MAX_LISTED:]
    hidden = len(commits) - len(listed)

    lines = [
        f"`{slug(repo)}@{ref}` has moved past the pinned commit.",
        "",
        "|  |  |",
        "| --- | --- |",
        f"| pinned | [`{old[:7]}`](https://github.com/{slug(repo)}/commit/{old})"
        f"{dated(comparison.get('base_commit'))} |",
        f"| {ref} | [`{new[:7]}`](https://github.com/{slug(repo)}/commit/{new})"
        f"{dated(commits[-1] if commits else None)} |",
        f"| distance | {comparison.get('ahead_by', '?')} commits |",
        f"| version this would build | `{version}` |",
        "",
        "Merging this triggers `refresh-kivy3`, which builds Android and iOS from",
        "the new commit, publishes a release for each, and republishes the index.",
        "",
        "Nothing already published changes. The new build gets its own version, so",
        "every lock file that pinned the old wheels keeps resolving to exactly the",
        "bytes it pinned.",
        "",
        "Worth a human eye before merging:",
        "",
        "- The `note` fields for `[ios.kivy]` and `[android.kivy]` describe *why*",
        "  the old commit was chosen. Only the `commit` lines were changed here.",
        "- A new build has not run on a device or the simulator. The static gates",
        "  (`check_needed.py`, `check_min_os.py`, `verify_index.py`) all still run,",
        "  but they are not a substitute for launching the thing.",
        "",
        f"### Commits ({len(commits)})",
        "",
    ]
    if hidden > 0:
        lines.append(f"_Showing the newest {len(listed)}; {hidden} older omitted._")
        lines.append("")
    for commit in reversed(listed):
        subject = commit["commit"]["message"].splitlines()[0]
        lines.append(f"- `{commit['sha'][:7]}` {subject}")
    return "\n".join(lines) + "\n"


def emit(**outputs: str) -> None:
    """Publish step outputs when running in Actions; harmless elsewhere."""
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in outputs.items():
            handle.write(f"{key}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Rewrite the watched pins to the remote head.",
    )
    parser.add_argument(
        "--body-file",
        type=Path,
        help="Write a pull request body, as markdown, to this path.",
    )
    args = parser.parse_args()

    repo, ref, pinned = watched_pins()
    head = remote_head(repo, ref)
    print(f"{slug(repo)}@{ref}")
    print(f"  pinned {pinned}")
    print(f"  remote {head}")

    if head == pinned:
        print("  up to date")
        emit(drifted="false")
        return 0

    version = predicted_version(repo, head)
    print(f"  drifted; {head[:7]} would build {version}")

    try:
        comparison = api(f"/repos/{slug(repo)}/compare/{pinned}...{head}")
    except HTTPError as error:
        # Losing the commit list is not worth failing over — the shas and the
        # version are the parts that matter, and they are already known.
        print(f"  could not compare ({error}); continuing without the commit list")
        comparison = {}

    title = f"Bump the Kivy 3.0 pin to {head[:7]} ({version})"
    emit(drifted="true", sha=head, short=head[:7], version=version, title=title)

    if args.body_file:
        args.body_file.write_text(
            body(repo, ref, pinned, head, version, comparison), encoding="utf-8"
        )
        print(f"  wrote {args.body_file}")

    if args.write:
        rewrite_pins(head)
        print(f"  rewrote {len(WATCHED)} pins in {PINS.name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
