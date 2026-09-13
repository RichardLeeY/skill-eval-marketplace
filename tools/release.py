#!/usr/bin/env python3
"""Cut a plugin release: bump both manifests, write the CHANGELOG entry, commit, tag.

    python tools/release.py patch|minor|major|X.Y.Z  "one-line summary"
    python tools/release.py minor "..." --dry-run     # show, change nothing
    python tools/release.py minor "..." --no-tag      # commit without tagging

Why this exists. The version lives in two files, `.claude-plugin/plugin.json` and
`.claude-plugin/marketplace.json`, and Claude Code caches a plugin per version
string. Bumping one file and not the other, or forgetting both, is how a session
kept running the 0.5.0 skill after 0.7.0 was pushed. There was also no tag and
no CHANGELOG, so "what changed between 0.5.0 and 0.7.0" meant reading commits.

The CHANGELOG entry is assembled from commit subjects since the previous tag (or
since the previous version-bump commit when no tag exists yet), under the summary
you pass. Edit CHANGELOG.md before pushing if the subjects need tidying; the
commit this script makes is local until you push it.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFESTS = (os.path.join(ROOT, ".claude-plugin", "plugin.json"),
             os.path.join(ROOT, ".claude-plugin", "marketplace.json"))
CHANGELOG = os.path.join(ROOT, "CHANGELOG.md")
PLUGIN_NAME = "aws-sa-skills"


def git(*args: str) -> str:
    # Fixed argv, no shell.
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()  # nosemgrep: dangerous-subprocess-use-audit


def current_version() -> str:
    with open(MANIFESTS[0], encoding="utf-8") as fh:
        return json.load(fh)["version"]


def bump(version: str, part: str) -> str:
    if re.fullmatch(r"\d+\.\d+\.\d+", part):
        return part
    major, minor, patch = (int(x) for x in version.split("."))
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    if part == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise SystemExit(f"unknown bump {part!r}: use patch, minor, major or X.Y.Z")


def set_version(path: str, new: str) -> None:
    """Rewrite the version fields textually so the rest of the file keeps its formatting."""
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    updated, n = re.subn(r'("version":\s*")\d+\.\d+\.\d+(")', rf"\g<1>{new}\g<2>", text)
    if n == 0:
        raise SystemExit(f"{path}: no \"version\" field found")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(updated)


def since_ref(prev_version: str) -> str | None:
    """The tag for the previous version, or the commit that bumped to it."""
    tags = git("tag", "--list", f"v{prev_version}").splitlines()
    if tags:
        return tags[0]
    log = git("log", "--format=%H %s", "--fixed-strings", f"--grep={PLUGIN_NAME} {prev_version}:")
    return log.split()[0] if log else None


def commit_subjects(since: str | None) -> list[str]:
    rng = f"{since}..HEAD" if since else "HEAD"
    out = git("log", "--format=%s", "--no-merges", rng)
    subjects = [s for s in out.splitlines() if s.strip()]
    # The bump commit for the previous version is not part of this release.
    return [s for s in subjects if not re.match(rf"^{PLUGIN_NAME} \d+\.\d+\.\d+:", s)]


def changelog_entry(version: str, summary: str, subjects: list[str]) -> str:
    today = dt.date.today().isoformat()
    lines = [f"## {version} — {today}", "", summary, ""]
    lines += [f"- {s}" for s in subjects] or ["- (no commits since the previous release)"]
    return "\n".join(lines) + "\n"


def prepend_changelog(entry: str) -> None:
    header = "# Changelog\n\nOne entry per plugin release. Entries are written by `tools/release.py` from the\ncommit subjects since the previous release; edit before pushing if they need tidying.\n\n"
    body = ""
    if os.path.exists(CHANGELOG):
        with open(CHANGELOG, encoding="utf-8") as fh:
            body = fh.read()
        if body.startswith("# Changelog"):
            body = body.split("\n\n", 2)[-1] if body.count("\n\n") >= 2 else ""
    with open(CHANGELOG, "w", encoding="utf-8") as fh:
        fh.write(header + entry + ("\n" + body if body.strip() else ""))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("part", help="patch | minor | major | X.Y.Z")
    ap.add_argument("summary", help="one line for the CHANGELOG and the commit subject")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-tag", action="store_true")
    args = ap.parse_args()

    if git("status", "--porcelain") and not args.dry_run:
        raise SystemExit("working tree is not clean; commit or stash first so the release commit is only the release")
    prev = current_version()
    new = bump(prev, args.part)
    since = since_ref(prev)
    subjects = commit_subjects(since)
    entry = changelog_entry(new, args.summary, subjects)

    print(f"{prev} -> {new}   (commits since {since or 'the beginning'}: {len(subjects)})")
    print(entry)
    if args.dry_run:
        return 0

    for path in MANIFESTS:
        set_version(path, new)
    prepend_changelog(entry)
    git("add", *MANIFESTS, CHANGELOG)
    git("commit", "-q", "-m", f"{PLUGIN_NAME} {new}: {args.summary}")
    if not args.no_tag:
        git("tag", "-a", f"v{new}", "-m", f"{PLUGIN_NAME} {new}: {args.summary}")
    print(f"committed{'' if args.no_tag else f' and tagged v{new}'}; push with: git push origin main"
          f"{'' if args.no_tag else f' v{new}'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
