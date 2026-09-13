#!/usr/bin/env python3
"""Lint the repo's eval datasets before anything is paid for.

    python evalkit/check_repo.py            # errors fail, warnings print
    python evalkit/check_repo.py --strict   # warnings fail too

Why this exists. `expect` is a free-form object and every key in it is read with
`.get()`, so a misspelled key is not an error -- it is a check that silently never
runs. `expected_theme` written as `expect_theme` grades every theme as correct;
`reference_image` pointing at a directory that does not exist scores the case
under the plain rubric and renames nothing, which is exactly how the drawio
skill's reference sat out several runs (see its eval/reference/CALIBRATION.md).
The dataset reader catches malformed JSON; nothing caught a well-formed lie.

Errors (exit 1): a key the kit and plugins do not read; a case id used twice
anywhere in the repo; a seed `{"from": …}` or `reference_image` path that does
not exist; a regex that does not compile; `expect.skill` naming a skill that is
not in `skills/`; `tools_in_order` naming a tool not in `tools_any_order`.

Warnings (exit 0, or 1 with --strict): a skill with fewer than MIN_CASES cases; a
skill whose eval/plugin.py exists without a negative-control/expected.json; a
case with no `assertions`; an `assertions` list of one item.

Pure stdlib, no model, no network. Runs first in CI so a broken dataset stops the
pipeline before a single agent run is bought.
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from evalkit.dataset import load_cases  # noqa: E402

SKILLS_ROOT = os.path.join(ROOT, "skills")
MIN_CASES = 3

#: Top-level keys of a case object.
CASE_KEYS = {"id", "prompt", "expect", "seed_files"}
#: Every `expect.*` key something in the kit or a plugin actually reads. Grep
#: `expect.get("` / `expect["` across evalkit/ and skills/*/eval/plugin.py when
#: adding one, and add it here in the same commit -- this list is the schema.
EXPECT_KEYS = {
    # evalkit/graders.py
    "skill", "file_glob", "artifact_regex", "artifact_regex_absent",
    "tools_any_order", "tools_in_order", "validator_pass_regex",
    "handwrite_marker", "min_counts", "max_tool_calls", "text_artifacts",
    # evalkit/assertions.py
    "assertions",
    # plugin-level conventions (skills/*/eval/plugin.py)
    "expected_theme", "reference_image",
}
REGEX_KEYS = ("artifact_regex", "artifact_regex_absent", "validator_pass_regex")


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, where: str, msg: str) -> None:
        self.errors.append(f"{where}: {msg}")

    def warn(self, where: str, msg: str) -> None:
        self.warnings.append(f"{where}: {msg}")


def _regexes(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str)]
    if isinstance(value, dict):
        return [v for v in value.values() if isinstance(v, str)]
    return []


def check_dataset(path: str, skills: set[str], seen_ids: dict[str, str], rep: Report) -> int:
    """Lint one dataset; returns the number of cases it holds."""
    skill_dir = os.path.dirname(os.path.dirname(path))
    eval_dir = os.path.dirname(path)
    skill_name = os.path.basename(skill_dir)
    rel = os.path.relpath(path, ROOT)
    if rel.startswith(".."):
        rel = path  # outside the repo (tests, --skills-root); a ../../ chain says nothing
    try:
        cases = load_cases(path, with_seeds=False)
    except SystemExit as e:
        rep.error(rel, str(e))
        return 0

    for case in cases:
        cid = str(case.get("id") or "<no id>")
        where = f"{rel} [{cid}]"
        if "id" not in case:
            rep.error(where, "case has no `id`")
        elif cid in seen_ids:
            rep.error(where, f"id also declared in {seen_ids[cid]}; ids must be unique repo-wide "
                             f"(artifacts/<id>/ and the phase-2 recording are keyed on them)")
        else:
            seen_ids[cid] = rel
        if not isinstance(case.get("prompt"), str) or not case["prompt"].strip():
            rep.error(where, "`prompt` is missing or empty")
        for key in set(case) - CASE_KEYS:
            rep.error(where, f"unknown top-level key {key!r}; a case has {sorted(CASE_KEYS)}")

        expect = case.get("expect")
        if not isinstance(expect, dict):
            rep.error(where, "`expect` is missing or not an object")
            continue
        for key in sorted(set(expect) - EXPECT_KEYS):
            close = [k for k in EXPECT_KEYS if k.replace("_", "") == key.replace("_", "")
                     or key in k or k in key]
            hint = f" (did you mean {close[0]!r}?)" if close else ""
            rep.error(where, f"expect.{key} is read by nothing in the kit or any plugin, so the "
                             f"check it names never runs{hint}")

        want = expect.get("skill")
        if want is not None and want not in skills:
            rep.error(where, f"expect.skill={want!r} is not a directory under skills/")
        if want is not None and want != skill_name:
            rep.warn(where, f"expect.skill={want!r} but the dataset lives under skills/{skill_name}/")

        for key in REGEX_KEYS:
            for pattern in _regexes(expect.get(key)):
                try:
                    re.compile(pattern)
                except re.error as e:
                    rep.error(where, f"expect.{key} {pattern!r} does not compile: {e}")

        ordered = expect.get("tools_in_order") or []
        anyorder = set(expect.get("tools_any_order") or [])
        if anyorder:
            for tool in ordered:
                if tool not in anyorder:
                    rep.error(where, f"tools_in_order names {tool!r} but tools_any_order does not; "
                                     f"the ordered check can only pass if the tool is also expected")

        ref = expect.get("reference_image")
        if ref:
            if not os.path.isfile(os.path.join(eval_dir, ref)):
                rep.error(where, f"reference_image {ref!r} does not exist relative to {os.path.dirname(rel)}/; "
                                 f"the judge would silently fall back to the plain rubric")

        seeds = case.get("seed_files")
        if seeds is not None:
            if not isinstance(seeds, dict):
                rep.error(where, "`seed_files` must be an object of {relative path: content | {from: path}}")
            else:
                for rel_seed, val in seeds.items():
                    if isinstance(val, dict):
                        src = val.get("from")
                        if not src or set(val) != {"from"}:
                            rep.error(where, f"seed_files[{rel_seed!r}] must be a string or {{\"from\": path}}")
                        elif not os.path.isfile(os.path.join(eval_dir, src)):
                            rep.error(where, f"seed_files[{rel_seed!r}] reads {src!r}, which does not exist "
                                             f"relative to {os.path.dirname(rel)}/")
                    elif not isinstance(val, str):
                        rep.error(where, f"seed_files[{rel_seed!r}] must be a string or {{\"from\": path}}")

        assertions = expect.get("assertions")
        if assertions is None:
            rep.warn(where, "no `assertions`; only the deterministic layer grades this case")
        elif not isinstance(assertions, list) or not all(isinstance(a, str) for a in assertions):
            rep.error(where, "`assertions` must be a list of strings")
        elif len(assertions) < 2:
            rep.warn(where, "one assertion; a case usually states several distinct claims")

        budget = expect.get("max_tool_calls")
        if budget is not None and (not isinstance(budget, int) or budget <= 0):
            rep.error(where, f"max_tool_calls={budget!r} must be a positive integer")
    return len(cases)


def check_skill(skill_dir: str, n_cases: int, rep: Report) -> None:
    name = os.path.basename(skill_dir)
    eval_dir = os.path.join(skill_dir, "eval")
    if not os.path.isfile(os.path.join(skill_dir, "SKILL.md")):
        rep.error(f"skills/{name}", "no SKILL.md")
    if n_cases < MIN_CASES:
        rep.warn(f"skills/{name}", f"{n_cases} case(s); {MIN_CASES} is the floor a skill's "
                                   f"score means anything at")
    if os.path.isfile(os.path.join(eval_dir, "plugin.py")) and \
            not os.path.isfile(os.path.join(eval_dir, "negative-control", "expected.json")):
        rep.warn(f"skills/{name}", "eval/plugin.py without eval/negative-control/expected.json; "
                                   "a rubric with no negative control can loosen unnoticed "
                                   "(see CONTRIBUTING.md, 'The negative control')")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--strict", action="store_true", help="warnings fail too")
    ap.add_argument("--skills-root", default=SKILLS_ROOT)
    args = ap.parse_args()

    skills = {d for d in os.listdir(args.skills_root)
              if os.path.isdir(os.path.join(args.skills_root, d)) and not d.startswith(".")}
    rep = Report()
    seen: dict[str, str] = {}
    total = 0
    for skill in sorted(skills):
        skill_dir = os.path.join(args.skills_root, skill)
        ds = os.path.join(skill_dir, "eval", "dataset.jsonl")
        n = check_dataset(ds, skills, seen, rep) if os.path.isfile(ds) else 0
        if not os.path.isfile(ds):
            rep.warn(f"skills/{skill}", "no eval/dataset.jsonl; the skill is absent from every report")
        check_skill(skill_dir, n, rep)
        total += n

    for line in rep.errors:
        print(f"  ✗ {line}")
    for line in rep.warnings:
        print(f"  · {line}")
    print(f"check_repo: {len(skills)} skills, {total} cases, "
          f"{len(rep.errors)} error(s), {len(rep.warnings)} warning(s)")
    if rep.errors or (args.strict and rep.warnings):
        print("✗ REPO CHECK FAIL")
        return 1
    print("✓ REPO CHECK PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
