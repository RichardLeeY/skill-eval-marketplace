"""Select and evaluate the skills affected by a GitLab merge request.

`select_skills` and `run_selected` are platform-neutral; `evalkit.github_ci` reuses
them with GitHub's event model. Only `plan` reads GitLab's predefined variables.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
SHARED_FILES = {
    "pyproject.toml", "uv.lock", "requirements.txt", "eval-baseline.json",
    ".gitlab-ci.yml", ".github/workflows/skill-eval.yml", "AGENTS.md", "CLAUDE.md",
}
SHARED_DIRS = ("evalkit/", "tests/")


def select_skills(root: Path, paths: list[str], *, full: bool = False) -> dict:
    available = {p.name for p in (root / "skills").iterdir()
                 if p.is_dir() and not p.name.startswith(".")}
    shared = sorted(p for p in paths if p in SHARED_FILES or p.startswith(SHARED_DIRS)
                    or (p.startswith("skills/") and len(p.split("/")) == 2))
    touched = {p.split("/")[1] for p in paths
               if p.startswith("skills/") and len(p.split("/")) >= 3}
    selected = sorted(available if full or shared else touched & available)
    missing = [name for name in selected
               if not (root / "skills" / name / "eval" / "dataset.jsonl").is_file()]
    return {
        "mode": "full" if full or shared else "changed",
        "skills": selected,
        "removed_skills": sorted(touched - available),
        "missing_datasets": missing,
        "shared_changes": shared,
        "changed_paths": paths,
    }


def plan(root: Path, env: dict) -> dict:
    source = env.get("CI_PIPELINE_SOURCE")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    base = None
    paths = []
    if source == "merge_request_event":
        base = env.get("CI_MERGE_REQUEST_DIFF_BASE_SHA", "")
        if not re.fullmatch(r"[0-9a-fA-F]{40,64}", base) or set(base) == {"0"}:
            raise ValueError("CI_MERGE_REQUEST_DIFF_BASE_SHA must identify the MR diff base")
        # --no-renames reports both sides of moves, even across skill directories.
        # NUL delimiters preserve filenames containing whitespace or newlines.
        raw = subprocess.check_output(
            ["git", "diff", "--name-only", "--no-renames", "-z", base, head, "--"], cwd=root)
        paths = [os.fsdecode(p) for p in raw.split(b"\0") if p]
    elif source != "web":
        raise ValueError("Expected a merge_request_event or web pipeline")
    result = select_skills(root, paths, full=source == "web")
    result.update(source=source, base_sha=base, head_sha=head,
                  merge_request=env.get("CI_MERGE_REQUEST_IID"))
    return result


def run_selected(root: Path, selection: dict, env: dict, *,
                 credential_vars: tuple[str, ...] = ("AWS_CREDS_TARGET_ROLE",)) -> int:
    """Set up and evaluate the selected skills; `credential_vars` names the environment
    variables of which at least one must be present for the run to be attempted."""
    if selection["missing_datasets"]:
        raise ValueError("Selected skills need eval/dataset.jsonl: "
                         + ", ".join(selection["missing_datasets"]))
    if not selection["skills"]:
        print("No remaining skill requires evaluation.")
        return 0
    # AWS credentials gate Bedrock runs only. An OpenAI-compatible provider carries
    # its own key (evalkit.models validates it at setup), so a pure-openai job may
    # run without any AWS variable.
    agent = env.get("MODEL_PROVIDER") or "bedrock"
    judge = env.get("JUDGE_MODEL_PROVIDER") or agent
    if not (agent == "openai" and judge == "openai"):
        if not any(env.get(name) for name in credential_vars):
            raise ValueError("Required eval cannot run: none of "
                             + ", ".join(credential_vars) + " is available to this job")
    flags = [arg for name in selection["skills"] for arg in ("--skill", name)]
    commands = [[sys.executable, "-m", "evalkit.cli", "setup", "--with-system-deps", *flags]]
    # MP4 rendering and frame extraction need the system binary.
    if "visual-flow-webp" in selection["skills"] and not shutil.which("ffmpeg"):
        commands += [["apt-get", "update", "-qq"], ["apt-get", "install", "-y", "-qq", "ffmpeg"]]
    commands += [[sys.executable, "-m", "evalkit.cli", "run",
                  "--profile", "core", "--negative-controls", *flags]]
    for command in commands:
        print("+ " + " ".join(command), flush=True)
        code = subprocess.run(command, cwd=root).returncode
        if code:
            return code
    return 0


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
        code = 0 if args.plan_only else run_selected(ROOT, selection, os.environ)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        selection["error"] = str(exc)
        print(str(exc), file=sys.stderr)
    selection["exit_code"] = code
    selection["plan_only"] = args.plan_only
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(selection, indent=2) + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
