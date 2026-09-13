#!/usr/bin/env python3
"""Skill evaluation through the Strands Evals SDK.

Run it through `uv`, which keeps the eval SDK out of the interpreter under test:

    uv run --with strands-agents-evals python run_strands_eval.py
    uv run --with strands-agents-evals python run_strands_eval.py --only three-tier-web
    uv run --with strands-agents-evals python run_strands_eval.py --no-visual

Phase 2 reuses the recording without running the agent again. Model judges still
make paid calls and can vary between runs; current plugins and dependencies also
affect scores. Prefer `uv run skill-eval run` and `skill-eval score` for archived runs.

Two layers land in one report, and the split is what makes the kit shareable:

  layer 1    Owned here, zero author configuration: SkillSelectionAccuracy,
             SkillInstructionFollowing (which reads the skill's own SKILL.md as
             its rubric), SkillInvoked, ToolTrajectory, and Assertions (every
             claim in `expect.assertions`, one row each). Skill-agnostic, and the
             only layer comparable across skills.
  layer 2    Owned by the skill author, loaded from `skills/<name>/eval/plugin.py`.
             Geometry, renderers, domain rubrics. See `evalkit/plugins.py`.

Nothing diagram-specific lives in this file. If you find yourself adding a
`.drawio` check here, it belongs in that skill's plugin instead.
"""

from __future__ import annotations

import argparse
import hashlib
import asyncio
import functools
import json
import os
from pathlib import Path
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
#: Repo root: this file lives in `evalkit/`, skills live in `skills/`.
ROOT = os.path.dirname(_HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, _HERE)

from strands_evals import Case, Experiment  # noqa: E402
from strands_evals.evaluators import (  # noqa: E402
    SkillInstructionFollowingEvaluator,
    SkillInvoked,
    SkillSelectionAccuracyEvaluator,
)
from strands_evals.types import MultimodalInput  # noqa: E402

# Package-qualified on purpose. Imported as bare `plugins`, this module would be a
# *second* module object with its own `Prepared` class, and an isinstance check
# against a plugin's `Prepared` would fail with the uniquely confusing
# "must return a Prepared, got Prepared".
from evalkit import models as model_config  # noqa: E402
from evalkit import trajectory  # noqa: E402
from evalkit.assertions import AssertionsEvaluator  # noqa: E402
from evalkit.plugins import (  # noqa: E402
    PrepareContext,
    Prepared,
    load_plugin,
    skill_datasets,
)
from evalkit.tool_trajectory import ToolTrajectoryEvaluator  # noqa: E402
from evalkit.records import (  # noqa: E402
    MANUAL, IntegrityError, digest, join_recording, metadata_path, requested_cases, write_json,
)

SKILLS_ROOT = os.path.realpath(os.environ.get("SKILLS_DIR", os.path.join(ROOT, "skills")))
HERE = ROOT

# On Bedrock (the default), `JUDGE_MODEL_ID` must be a full inference-profile id,
# not the bare family name. `global.anthropic.claude-sonnet-4-5` looks plausible and
# is rejected as an invalid model identifier -- which surfaces as every judge scoring
# 0.00 with a ValidationException, i.e. as a failing skill rather than a bad config.
# Verified ACTIVE via ListInferenceProfiles.
#
# `JUDGE_MODEL_PROVIDER=openai` points the judges at any OpenAI-compatible endpoint
# instead; see `evalkit/models.py`, including why the judges specifically need
# structured-output support.


@functools.lru_cache(maxsize=1)
def judge_model():
    """The judges' model, built once.

    Cached rather than rebuilt per case because `evaluators()` runs once per case
    and each call would otherwise construct another provider client -- one HTTP
    client per case, for a single unchanging configuration. Lazy rather than
    module-level so `--help` and the argument errors do not require working model
    credentials.
    """
    return model_config.build_model("judge")


def load(results_path: str, dataset_path: str, only: list[str] | None) -> list[dict]:
    """Join each recorded run to its case definition.

    Kept as a join rather than trusting `results.json` alone because the
    expectations (`expect`) live in the dataset and are what the reference-based
    evaluators need; the results file records what happened, not what was asked.
    """
    return join_recording(requested_cases([dataset_path], only), results_path)


# Text deliverables the instruction-following judge has to read to score the
# skill's workflow. Diagram formats are excluded from the default on purpose: a
# .drawio is tens of kilobytes of XML that would crowd out the trajectory, and the
# layout, visual and style layers already judge it from geometry and pixels.
#
# A default, not a policy. This list is the one place the kit would otherwise know
# what skills produce: a skill whose deliverable is `.json`, `.yaml`, `.sql` or
# `.html` got no evidence at all and its judge scored the workflow blind -- the
# exact failure this function was added to fix, reintroduced for every format not
# named here. Cases override it with `expect.text_artifacts`.
_TEXT_ARTIFACTS = (".md", ".mmd", ".txt", ".csv")
_ARTIFACT_CAP = 12_000


def text_extensions(expect: dict) -> tuple[str, ...]:
    """Which produced files count as readable evidence for this case.

    `expect.text_artifacts` is a list of extensions, with or without the leading
    dot, and replaces the default outright rather than adding to it -- a skill
    that emits both `.md` and `.json` says so, and one that wants no artifact
    evidence at all passes `[]`.
    """
    declared = expect.get("text_artifacts")
    if declared is None:
        return _TEXT_ARTIFACTS
    return tuple(e if e.startswith(".") else f".{e}" for e in declared)


def artifact_evidence(recorded: dict, extensions: tuple[str, ...] = _TEXT_ARTIFACTS) -> str:
    """The text deliverables this run wrote, read from disk.

    `SkillInstructionFollowing` scores steps like "add a Total as the last row of
    the table", and the trajectory is not enough to check that: `sandbox._record`
    truncates every string tool argument at 400 characters, so a `Write` of a 2.7 KB
    markdown file appears cut off well before its table ends. The judge did the
    honest thing with what it had -- "the Write content is truncated ... there's no
    clear evidence the Total row was included" -- and scored the step violated on a
    file that has `| **总计** | | | | **$438.42** |` as its last table row. The
    deterministic grader, which reads the file, passed it in the same run.

    Raising the 400-char truncation instead would be the wrong fix: it bloats every
    trajectory to serve one judge, and the thing the judge actually needs is the
    *final* state of the file, not the arguments of the call that happened to write
    it. An agent that writes then edits is judged correctly here and would not be by
    a longer argument dump.
    """
    parts: list[str] = []
    for rel, meta in sorted((recorded.get("files") or {}).items()):
        if not extensions or not rel.lower().endswith(extensions):
            continue
        saved = meta.get("saved_to")
        if not saved or not os.path.exists(saved):
            continue
        try:
            with open(saved, encoding="utf-8") as fh:
                body = fh.read(_ARTIFACT_CAP + 1)
        except OSError as e:
            parts.append(f"### {rel}\n(could not be read: {e})")
            continue
        if len(body) > _ARTIFACT_CAP:
            body = body[:_ARTIFACT_CAP] + "\n…[truncated]"
        parts.append(f"### {rel}\n{body}")
    if not parts:
        return ""
    return (
        "The files this run produced, read from disk after it finished. This is the "
        "deliverable; judge the workflow against it rather than against the "
        "arguments of the tool call that wrote it.\n\n" + "\n\n".join(parts)
    )


def build_case(entry: dict, want_visual: bool,
               artifacts_root: str) -> tuple[Case, dict]:
    """One `Case`, plus the per-case metadata the layout/visual layers read."""
    spec, recorded = entry["spec"], entry["recorded"]
    prompt = spec["prompt"]
    expect = spec.get("expect", {})

    resp = {
        "output": recorded.get("output_head") or "",
        "trace": {"tool_calls": recorded.get("tool_calls") or []},
        "files": recorded.get("files") or {},
    }
    traj = trajectory.build_trajectory(prompt, resp, recorded.get("system_prompt") or "")
    # Appended as a final user turn rather than folded into `output`: `output` is
    # what the agent said, and overwriting it with file contents would break the
    # style rubric's rule about not judging the diagram by its `<Output>` field.
    evidence = artifact_evidence(recorded, text_extensions(expect))
    if evidence:
        traj.append({"role": "user", "content": [{"text": evidence}]})
    # Layer 2: hand the recorded run to the skill's own plugin, if it ships one.
    # Everything format-specific -- rendering, geometry, domain rubrics -- happens
    # in there. This file stays ignorant of what the skill produces.
    want_skill = expect.get("skill") or ""
    plugin = load_plugin(SKILLS_ROOT, want_skill)
    applicable = plugin.applies(expect)
    prepared = Prepared(applicable=False)
    if applicable:
        prepared = plugin.prepare(PrepareContext(
            case_id=spec["id"],
            prompt=prompt,
            expect=expect,
            recorded=recorded,
            # Passed in rather than composed from `__file__`: two runs writing to
            # the same derived path would overwrite each other's evidence.
            artifacts_dir=os.path.join(artifacts_root, spec["id"]),
            want_visual=want_visual,
            judge_model=judge_model(),
        ))
        for line in prepared.findings:
            print(f"  {spec['id']}: {line}", file=sys.stderr)

    # Media go in `input`, which is where the multimodal judge looks for them; the
    # instruction carries the user's ask plus whatever facts the plugin already
    # settled deterministically.
    #
    # A reference image belongs in this same media list, never in
    # `expected_output`: the prompt composer interpolates that field into text --
    # `f"<ExpectedOutput>{expected_output}</ExpectedOutput>"` -- so an image there
    # reaches the judge as a pydantic repr of a *file path*, and the judge grades a
    # picture it was never shown, fluently.
    case_input: object = prompt
    if prepared.media:
        case_input = MultimodalInput(media=prepared.media,
                                     instruction=prepared.instruction or prompt)

    # Every assertion, not just the first. `AssertionsEvaluator` reads the list off
    # the metadata and scores each one as its own row; `expected_assertion` carries
    # the same claims joined, for any SDK evaluator that reads that field (its own
    # docstring calls it "assertions", plural). Passing `assertions[0]` alone, as
    # this did, silently discarded three or four load-bearing claims per case.
    assertions = [a for a in (expect.get("assertions") or [])
                  if isinstance(a, str) and a.strip()]

    case = Case(
        name=spec["id"],
        input=case_input,
        expected_assertion="\n".join(f"{i}. {a}" for i, a in enumerate(assertions, 1))
                           or None,
        expected_trajectory=trajectory.expected_trajectory(expect),
        metadata={
            "want_skill": want_skill,
            "assertions": assertions,
            "tools_any_order": expect.get("tools_any_order") or [],
            "tools_in_order": expect.get("tools_in_order") or [],
            # Whether the skill's own layer applies to this case at all. Kept
            # explicit because "produced nothing because nothing was asked for" and
            # "was supposed to produce something and did not" must not collapse
            # into the same not-applicable row.
            "layer2_applies": applicable,
            "request_prompt": prompt,
            **prepared.metadata,
        },
    )
    return case, {"trajectory": traj, "output": resp["output"], "metadata": case.metadata,
                  "prompt": prompt, "prepared": prepared, "plugin": plugin}


def evaluators(want_skill: str, plugin, prepared: Prepared) -> list:
    """Layer 1 (always) plus whatever the skill's plugin adds.

    Layer 1 comes first so the report reads in a fixed order regardless of which
    skill is under test, which is what makes the shared rows scannable across
    skills.
    """
    evs: list = [
        SkillSelectionAccuracyEvaluator(model=judge_model()),
        SkillInstructionFollowingEvaluator(model=judge_model()),
    ]
    if want_skill:
        evs.append(SkillInvoked(skill_name=want_skill, name=f"SkillInvoked[{want_skill}]"))
    evs.append(ToolTrajectoryEvaluator())
    # Registered unconditionally: it reads the assertions off the case metadata and
    # returns a not-applicable row -- with no judge call -- for a case that declares
    # none. Conditioning registration on the dataset instead would make the report's
    # row set depend on the case, which is what makes layer 1 hard to compare.
    evs.append(AssertionsEvaluator(model=judge_model()))
    if prepared.applicable:
        evs.extend(plugin.evaluators(prepared, judge_model()))
    return evs


def expandable(evs: list) -> frozenset[str]:
    """Names of the evaluators whose per-item verdicts belong in the report.

    Opt-in via a class attribute rather than inferred from the row count, so an
    evaluator that *happens* to emit two rows this run is not silently reshaped
    into two report rows the next. Any plugin evaluator can set `expand_rows = True`
    for the same treatment.
    """
    return frozenset(e.get_name() for e in evs if getattr(e, "expand_rows", False))


def rubric_hash(skill: str | None) -> str:
    """Source fingerprint, not a promise of deterministic model scores."""
    # Only what takes part in scoring. run_eval/sandbox shape the *recording*, and
    # check_repo/doctor/release touch nothing a judge reads; hashing them would
    # flag "rubric changed" on every tooling edit and the flag would stop meaning
    # anything.
    scoring = ("graders.py", "assertions.py", "tool_trajectory.py", "trajectory.py",
               "plugins.py", "models.py", "run_strands_eval.py")
    paths = [os.path.join(ROOT, "evalkit", name) for name in scoring
             if os.path.exists(os.path.join(ROOT, "evalkit", name))]
    if skill:
        base = Path(SKILLS_ROOT) / skill
        # Prune dependency trees before traversal; filtering rglob's output still
        # walks every node_modules file for every case.
        for directory, dirs, files in os.walk(base):
            dirs[:] = sorted(d for d in dirs if d not in {
                "node_modules", "__pycache__", ".git", ".venv", ".eval"
            })
            paths += [str(Path(directory) / name) for name in sorted(files)
                      if name != ".DS_Store"]
    paths += [str(p) for p in (Path(ROOT) / "uv.lock",) if p.exists()]
    h = hashlib.sha256()
    for p in paths:
        with open(p, "rb") as fh:
            h.update(os.path.relpath(p, ROOT).encode())
            h.update(fh.read())
    return h.hexdigest()[:12]


def read_baseline(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return data.get("cases", data) if isinstance(data, dict) else {}


def compare_baseline(reports: list[dict], path: str, tolerance: float) -> list[str]:
    """Print the per-case delta; return the cases that regressed past tolerance."""
    base = read_baseline(path)
    if not base:
        print(f"baseline: none at {path} (review a report, then use skill-eval baseline accept)")
        return []
    regressed = []
    print("baseline comparison:")
    for r in reports:
        prev = base.get(r["case"])
        if not prev:
            print(f"  {r['case']:<40} {r['overall_score']:.3f}   (no baseline yet)")
            continue
        old_profile = prev.get("scoring_profile")
        if old_profile and old_profile != r.get("scoring_profile"):
            print(f"  {r['case']}: INCOMPATIBLE profiles "
                  f"{old_profile} / {r.get('scoring_profile')}; choose a matching baseline")
            regressed.append(r["case"])
            continue
        delta = r["overall_score"] - float(prev["overall_score"])
        note = ""
        if not old_profile:
            note += "  legacy baseline: profile unknown"
        if prev.get("judge_provider") and prev["judge_provider"] != r.get("judge_provider"):
            note += "  judge provider changed"
        if prev.get("judge_model") and prev["judge_model"] != r["judge_model"]:
            note += f"  judge changed ({prev['judge_model']} -> {r['judge_model']})"
        if prev.get("rubric_hash") and prev["rubric_hash"] != r["rubric_hash"]:
            note += "  rubric changed"
        mark = "REGRESSED" if delta < -tolerance else ("improved" if delta > tolerance else "steady")
        print(f"  {r['case']:<40} {r['overall_score']:.3f}  {delta:+.3f}  {mark}{note}")
        if delta < -tolerance:
            regressed.append(r["case"])
    if regressed:
        print(f"  {len(regressed)} case(s) dropped more than {tolerance:.2f} below baseline; "
              f"review the saved report before using skill-eval baseline accept", file=sys.stderr)
    return regressed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=str(MANUAL / "results.json"))
    ap.add_argument("--cases", nargs="*", default=None,
                    help="dataset paths; default = every skills/*/eval/dataset.jsonl")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--out", default=str(MANUAL / "eval-report.json"))
    ap.add_argument("--artifacts", default=str(MANUAL / "derived"),
                    help="where plugins may write derived files (renders, diffs)")
    ap.add_argument("--no-visual", action="store_true",
                    help="skip anything a plugin marks visual (rendering, vision judges)")
    ap.add_argument("--max-workers", type=int, default=4)
    ap.add_argument("--no-extra", action="store_true",
                    help="skip plugin-declared extra passes (each is an added judge call)")
    ap.add_argument("--baseline", default=os.path.join(ROOT, "eval-baseline.json"),
                    help="committed per-case scores the run is compared against")
    ap.add_argument("--tolerance", type=float, default=0.05,
                    help="a case may score this much below its baseline before the run fails")
    ap.add_argument("--update-baseline", action="store_true",
                    help="removed: accept an existing report with skill-eval baseline accept")
    ap.add_argument("--no-baseline", action="store_true",
                    help="skip the regression comparison (e.g. re-scoring a partial recording)")
    ap.add_argument("--pending-controls", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.update_baseline:
        ap.error("--update-baseline was removed; review the saved report, then run "
                 "skill-eval baseline accept --report <path>")
    if args.max_workers < 1 or not 0 <= args.tolerance <= 1:
        ap.error("--max-workers must be positive and --tolerance must be between 0 and 1")
    profile = ("core" if args.no_visual else "full") + ("-no-extra" if args.no_extra else "")
    completion = {
        "complete": False, "gates_passed": False, "scoring_profile": profile,
        "judge": model_config.describe("judge"),
        "negative_controls_requested": args.pending_controls,
        "controls_complete": not args.pending_controls,
    }
    # Invalidate old completion metadata even if input validation fails this time.
    write_json(metadata_path(args.out), completion)
    datasets = args.cases or skill_datasets(SKILLS_ROOT)
    if not datasets:
        print(f"no datasets found under {SKILLS_ROOT}/*/eval/dataset.jsonl", file=sys.stderr)
        return 2

    try:
        entries = join_recording(requested_cases(datasets, args.only), args.results)
    except (IntegrityError, OSError, ValueError) as e:
        print(str(e), file=sys.stderr)
        return 2
    completion.update(requested_cases=[e["spec"]["id"] for e in entries],
                      results_sha256=digest(args.results),
                      datasets={str(p): digest(p) for p in datasets})
    write_json(metadata_path(args.out), completion)
    built = [build_case(e, not args.no_visual, args.artifacts) for e in entries]
    replay = {c.name: extra for c, extra in built}

    # Plugin-declared extra passes. Each needs its own `Case` because every
    # evaluator in one Experiment builds its prompt from the same `case.input`:
    # appending a second reference image to the existing media list would leave two
    # judges working out which picture they are scoring, and measured, they do not
    # reliably ignore an image they were told to ignore.
    extra_cases: list[tuple[str, Case, list, str, bool]] = []
    if not args.no_extra:
        for c, extra in built:
            for ex in extra["prepared"].extra:
                ec = Case(
                    name=f"{c.name}{ex.suffix}",
                    input=MultimodalInput(media=ex.media, instruction=ex.instruction),
                    metadata=dict(extra["metadata"]),
                )
                replay[ec.name] = extra
                extra_cases.append((c.name, ec, ex.evaluators, ex.row_label, ex.gate))

    def task(case: Case):
        """Replay the recorded run.

        `Experiment` expects a callable that produces the run under test. The run
        already happened in phase 1, so this hands back what was
        recorded -- including the trajectory, which is what every skill-level
        evaluator actually reads.
        """
        extra = replay[case.name]
        # Keys are `output` / `trajectory`: Experiment._execute_task reads exactly
        # those off a dict return. `actual_output` / `actual_trajectory` are the
        # EvaluationData field names and are silently ignored here, which shows up
        # as every skill evaluator reporting "no trajectory provided".
        return {"output": extra["output"], "trajectory": extra["trajectory"]}

    def collect(rep, case_name: str, label: str | None, gate: bool,
                expand: frozenset[str] = frozenset()) -> list[dict]:
        """Flatten a report into rows.

        The report is parallel lists, not a list of row dicts: `cases[i]` names the
        evaluator while the verdict for that row lives in `scores[i]` /
        `test_passes[i]` / `reasons[i]`. Zipping them is what turns it into rows.

        `expand` names the evaluators whose *individual* verdicts belong in the
        report. The framework calls `evaluator.aggregator(outputs)` and keeps only
        the average, which is the right default for a graded judge and wrong for an
        evaluator that emits one decision per item: "Assertions 0.60" and
        "ToolTrajectory 0.75" tell you something is broken without telling you
        which claim or which tool, and the whole point of listing them separately
        was to be told. The per-item verdicts survive in `detailed_results`, so
        expanding is a matter of reading them rather than of scoring anything
        twice. Aggregate pass is `all(...)` over the same rows, so an expanded
        evaluator gates exactly as it did -- with a name on the failure.
        """
        out = []
        if not rep.cases:
            return [{"case": case_name, "evaluator": label or "ReportIntegrity",
                     "score": 0.0, "test_pass": False, "reason": "evaluator returned no rows",
                     "gate": gate}]
        for i, row_meta in enumerate(rep.cases):
            name = label or (row_meta or {}).get("evaluator", "?")
            details = rep.detailed_results[i] if i < len(rep.detailed_results) else None
            sub = list(details or []) if name in expand else []
            if len(sub) > 1:
                rows = [{
                    "case": case_name,
                    "evaluator": f"{name}[{d.label or n}]",
                    "score": float(d.score),
                    "test_pass": bool(d.test_pass),
                    "reason": d.reason or "",
                    "gate": gate,
                } for n, d in enumerate(sub, 1)]
            else:
                rows = [{
                    "case": case_name,
                    "evaluator": name,
                    "score": rep.scores[i] if i < len(rep.scores) else 0.0,
                    "test_pass": rep.test_passes[i] if i < len(rep.test_passes) else False,
                    "reason": (rep.reasons[i] if i < len(rep.reasons) else "") or "",
                    # Whether this row may fail the run. Plugins declare it; the kit
                    # does not guess from the evaluator's name.
                    "gate": gate,
                }]
            out.extend(rows)
            for row in rows:
                # One line per row: an assertion's reason carries its evidence on a
                # second line, and a raw newline here would break the column the
                # report is read in.
                flat = " · ".join(row["reason"].split("\n"))
                print(f"  {row['score']:5.2f}  {'PASS' if row['test_pass'] else 'FAIL'}"
                      f"{'' if gate else ' (tracked)'}  "
                      f"{row['evaluator']:<34} {flat[:88]}")
        return out

    # One Experiment per case: the evaluator set is per-case, since the expected
    # skill differs and a plugin may decide it has nothing to contribute.
    reports, all_rows = [], []
    for case, extra in built:
        meta = extra["metadata"]
        prepared = extra["prepared"]
        evs = evaluators(meta["want_skill"], extra["plugin"], prepared)
        exp = Experiment(cases=[case], evaluators=evs)
        print(f"\n=== {case.name} === "
              f"(skill={meta['want_skill'] or 'n/a'}, "
              f"layer2={extra['plugin'].name if prepared.applicable else 'n/a'})", flush=True)
        # `run_evaluations_async` is a coroutine, and the sync `run_evaluations`
        # just calls it with max_workers=1. Driving it here keeps the evaluators for
        # one case running concurrently -- judges in sequence is round trips of
        # waiting for nothing.
        report = asyncio.run(exp.run_evaluations_async(task=task, max_workers=args.max_workers))
        rows = collect(report, case.name, None, gate=True, expand=expandable(evs))
        if len(report.cases) < len(evs):
            rows.append({"case": case.name, "evaluator": "ReportIntegrity", "score": 0.0,
                         "test_pass": False, "gate": True,
                         "reason": "one or more requested evaluators returned no result"})
        recorded = next(e["recorded"] for e in entries if e["spec"]["id"] == case.name)
        phase1_ok = (recorded.get("status") == "ok"
                     and recorded.get("deterministic", {}).get("all_passed") is True)
        rows.insert(0, {"case": case.name, "evaluator": "RecordedExecution",
                        "score": float(phase1_ok), "test_pass": phase1_ok, "gate": True,
                        "reason": "execution and deterministic checks passed" if phase1_ok
                        else "recorded execution or deterministic checks failed or are missing"})

        for parent, ec, ex_evs, label, gate in extra_cases:
            if parent != case.name:
                continue
            ex_report = asyncio.run(
                Experiment(cases=[ec], evaluators=ex_evs)
                .run_evaluations_async(task=task, max_workers=1))
            # An extra pass declares one `row_label` for the whole pass, so its rows
            # are not expanded: the label would be reused for every sub-row and the
            # report would carry several identically-named rows.
            rows += collect(ex_report, case.name, label, gate)

        all_rows.extend(rows)
        reports.append({
            "case": case.name,
            "skill": meta["want_skill"],
            "overall_score": report.overall_score,
            # Provenance helps distinguish changed evaluation inputs from changed
            # deliveries; model sampling can still move scores with identical inputs.
            "judge_model": model_config.judge_model_id(),
            "judge_provider": model_config.judge_provider(),
            "agent_model": recorded.get("model_id"),
            "agent_provider": recorded.get("model_provider"),
            "scoring_profile": profile,
            "rubric_hash": rubric_hash(meta["want_skill"]),
            "coverage": {
                "visual_requested": not args.no_visual,
                "main_media_count": len(getattr(prepared, "media", [])),
                "extra_passes": sum(parent == case.name for parent, *_ in extra_cases),
            },
            "findings": getattr(prepared, "findings", []),
            "rows": rows,
            "detailed_results": json.loads(json.dumps(report.detailed_results, default=str)),
        })
        write_json(args.out, reports)

    print("\n" + "=" * 78)
    tracked = [r for r in all_rows if not r["gate"]]
    failed = [r for r in all_rows if r["gate"] and not r["test_pass"]]
    print(f"rows: {len(all_rows)}   failing: {len(failed)}"
          f"   ({len(tracked)} tracked-only row(s) excluded from the exit code)")
    for r in reports:
        print(f"  {r['case']:<24} overall={r['overall_score']:.2f}   skill={r['skill']}")
    for r in tracked:
        print(f"  tracked  {r['case']:<20} {r['evaluator']:<30} {r['score']:.2f}")
    for r in failed:
        print(f"  FAIL  {r['case']:<22} {r['evaluator']}")
        # Assertion reasons put the deciding evidence after the full claim.
        # Keep it visible in CI logs, even when the claim spans several lines.
        for line in r["reason"].splitlines():
            print(f"    {line}")

    # Integrity gate: scoring fewer cases than were requested must not read as
    # success. A phase-1 crash on the hardest case would otherwise *raise* the
    # aggregate and still exit 0 -- a tool that measured nothing and reported green
    # is worse than no tool.
    missing = len(entries) - len(built)
    if missing:
        print(f"  INTEGRITY  {missing} requested case(s) were never scored", file=sys.stderr)

    # Regression gate. Pass/fail rows are binary, so a case can slide from 0.98 to
    # 0.91 and still exit 0 -- a slow leak nobody sees until it crosses a rubric
    # threshold. The baseline is the last *accepted* score per case, committed, so
    # a drop past --tolerance fails the run and names the case and the delta.
    regressed: list[str] = []
    if not args.no_baseline:
        regressed = compare_baseline(reports, args.baseline, args.tolerance)
    write_json(args.out, reports)
    completion.update(complete=not missing, gates_passed=not failed and not missing,
                      report_sha256=digest(args.out), regressed=regressed,
                      baseline=None if args.no_baseline else args.baseline,
                      tolerance=args.tolerance)
    write_json(metadata_path(args.out), completion)
    print(f"\nwrote {args.out}")
    return 0 if not failed and not missing and not regressed else 1


if __name__ == "__main__":
    raise SystemExit(main())
