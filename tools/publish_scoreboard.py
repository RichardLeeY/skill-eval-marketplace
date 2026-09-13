#!/usr/bin/env python3
"""Publish the local evaluation to the public scoreboard without CI credentials.

    python tools/publish_scoreboard.py            # publish .eval as the run for HEAD
    python tools/publish_scoreboard.py --dry-run  # build site/, commit nothing

Why this exists. GitHub Pages serves the gh-pages branch, and the scoreboard is
just files on that branch. When the repository cannot hold a cloud credential
(no OIDC role, no API key in secrets), run the evaluation here with your own AWS
profile and push the result yourself:

    uv run --locked skill-eval run --skill <name> --profile core --negative-controls
    uv run --locked python -m evalkit.ci_report
    uv run --locked python tools/publish_scoreboard.py

Requires a clean, pushed HEAD on main so the run is attributed to a commit that
exists on GitHub. The gh-pages branch is checked out as the `site/` worktree
(created as an orphan branch on first use) and pushed with your git credentials.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evalkit import pages  # noqa: E402


def git(*args: str, cwd: Path = ROOT, capture: bool = True) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, text=True, check=True,
                            capture_output=capture)
    return (result.stdout or "").strip()


def repository_slug() -> str:
    url = git("remote", "get-url", "origin")
    path = url.split("github.com")[-1].lstrip(":/")
    return path[:-4] if path.endswith(".git") else path


def ensure_site(site: Path) -> None:
    if (site / ".git").exists():
        git("checkout", "gh-pages", cwd=site)
        try:
            git("pull", "--ff-only", "origin", "gh-pages", cwd=site)
        except subprocess.CalledProcessError:
            print("gh-pages: no remote branch yet or not fast-forward; continuing with local state")
        return
    git("fetch", "origin")
    exists = subprocess.run(["git", "ls-remote", "--exit-code", "--heads", "origin", "gh-pages"],
                            cwd=ROOT, capture_output=True).returncode == 0
    if exists:
        git("worktree", "add", "-B", "gh-pages", str(site), "origin/gh-pages")
    else:
        git("worktree", "add", "--orphan", "-b", "gh-pages", str(site))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--run", default=str(ROOT / ".eval"))
    parser.add_argument("--site", default=str(ROOT / "site"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-dirty", action="store_true",
                        help="publish even if HEAD has uncommitted changes or is not pushed")
    args = parser.parse_args(argv)
    run = Path(args.run)
    if not (run / "summary.json").is_file():
        print("No .eval/summary.json; run `uv run --locked python -m evalkit.ci_report` first",
              file=sys.stderr)
        return 2
    head = git("rev-parse", "HEAD")
    if not args.allow_dirty:
        if git("status", "--porcelain", "--untracked-files=no"):
            print("Working tree has uncommitted changes; commit or use --allow-dirty", file=sys.stderr)
            return 2
        git("fetch", "origin", "main")
        if subprocess.run(["git", "merge-base", "--is-ancestor", head, "origin/main"],
                          cwd=ROOT).returncode != 0:
            print("HEAD is not on origin/main; push first or use --allow-dirty", file=sys.stderr)
            return 2
    summary = json.loads((run / "summary.json").read_text(encoding="utf-8"))
    if not summary.get("commit"):
        summary["commit"] = head
        (run / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    slug = repository_slug()
    site = Path(args.site)
    ensure_site(site)
    env = {**os.environ, "GITHUB_REPOSITORY": slug, "GITHUB_SERVER_URL": "https://github.com"}
    result = pages.build(site, run, ROOT / "eval-baseline.json", ROOT, env)
    print(json.dumps(result))
    git("add", "-A", cwd=site)
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=site).returncode == 0:
        print("Site unchanged")
        return 0
    if args.dry_run:
        print(f"Dry run: changes staged in {site}, nothing committed")
        return 0
    git("commit", "-m", f"scoreboard: {head[:7]} (local publish)", cwd=site)
    if subprocess.run(["git", "push", "origin", "gh-pages"], cwd=site).returncode != 0:
        print(f"Push rejected. The run is committed on gh-pages in {site}; "
              "resolve the rejection above and run `git push origin gh-pages` there.",
              file=sys.stderr)
        return 1
    print(f"Published https://{slug.split('/')[0].lower()}.github.io/{slug.split('/')[1]}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
