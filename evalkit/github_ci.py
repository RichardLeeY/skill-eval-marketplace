"""Select and evaluate the skills affected by a GitHub pull request.

GitHub's counterpart to `evalkit.gitlab_ci`. Selection and execution are shared;
this module only translates GitHub's event model:

* `pull_request`: the diff base is `git merge-base` of the PR's base and head
  commits, read from the event payload at `GITHUB_EVENT_PATH`. The diff runs to
  the PR head, so target-branch commits present in the merge checkout do not
  select extra skills. `actions/checkout` needs `fetch-depth: 0` for the merge
  base to exist locally; a missing base is an error, not an empty selection.
* `workflow_dispatch`, `push` and `schedule`: evaluate every skill, like GitLab's
  **Run pipeline**. `push` and `schedule` are how the published scoreboard
  (`evalkit.pages`) evaluates `main` after a merge.

Credentials come from `aws-actions/configure-aws-credentials`, which exports
static keys into the job environment; the run refuses to start without them so a
required eval never silently disappears from a green check.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from evalkit import gitlab_ci
from evalkit.gitlab_ci import ROOT, select_skills

CREDENTIAL_VARS = ("AWS_ACCESS_KEY_ID", "AWS_WEB_IDENTITY_TOKEN_FILE",
                   "AWS_CONTAINER_CREDENTIALS_FULL_URI")
SHA = re.compile(r"[0-9a-fA-F]{40,64}")
FULL_EVENTS = ("workflow_dispatch", "push", "schedule")


def _sha(value: str | None, label: str) -> str:
    if not value or not SHA.fullmatch(value) or set(value) == {"0"}:
        raise ValueError(f"{label} must identify a commit")
    return value


def _event_payload(env: dict) -> dict:
    path = env.get("GITHUB_EVENT_PATH")
    if not path or not Path(path).is_file():
        raise ValueError("GITHUB_EVENT_PATH must point at the pull_request event payload")
    return json.loads(Path(path).read_text(encoding="utf-8"))


def plan(root: Path, env: dict) -> dict:
    event = env.get("GITHUB_EVENT_NAME")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    base = None
    paths = []
    number = None
    if event == "pull_request":
        pr = _event_payload(env).get("pull_request") or {}
        number = pr.get("number")
        base_sha = _sha((pr.get("base") or {}).get("sha"), "pull_request.base.sha")
        head_sha = _sha((pr.get("head") or {}).get("sha"), "pull_request.head.sha")
        base = subprocess.check_output(
            ["git", "merge-base", base_sha, head_sha], cwd=root, text=True).strip()
        # --no-renames reports both sides of moves, even across skill directories.
        # NUL delimiters preserve filenames containing whitespace or newlines.
        raw = subprocess.check_output(
            ["git", "diff", "--name-only", "--no-renames", "-z", base, head_sha, "--"], cwd=root)
        paths = [os.fsdecode(p) for p in raw.split(b"\0") if p]
        head = head_sha
    elif event not in FULL_EVENTS:
        raise ValueError("Expected a pull_request, workflow_dispatch, push or schedule event")
    result = select_skills(root, paths, full=event in FULL_EVENTS)
    result.update(source=event, base_sha=base, head_sha=head,
                  merge_request=number, pull_request=number,
                  repository=env.get("GITHUB_REPOSITORY"), run_id=env.get("GITHUB_RUN_ID"))
    return result


def write_outputs(selection: dict, env: dict) -> None:
    """Expose the selection to later jobs through $GITHUB_OUTPUT, when present."""
    path = env.get("GITHUB_OUTPUT")
    if not path:
        return
    skills = selection.get("skills") or []
    lines = [f"eval_required={'true' if skills else 'false'}",
             f"skills={json.dumps(skills)}",
             f"mode={selection.get('mode', '')}"]
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-only", action="store_true", help="validate selection without model calls")
    args = parser.parse_args(argv)
    destination = ROOT / ".eval" / "selection.json"
    selection = {}
    code = 2
    try:
        selection = plan(ROOT, os.environ)
        print(json.dumps(selection, indent=2), flush=True)
        if selection["missing_datasets"]:
            raise ValueError("Selected skills need eval/dataset.jsonl: "
                             + ", ".join(selection["missing_datasets"]))
        code = 0 if args.plan_only else gitlab_ci.run_selected(
            ROOT, selection, os.environ, credential_vars=CREDENTIAL_VARS)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        selection["error"] = str(exc)
        print(str(exc), file=sys.stderr)
    selection["exit_code"] = code
    selection["plan_only"] = args.plan_only
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(selection, indent=2) + "\n")
    write_outputs(selection, os.environ)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
