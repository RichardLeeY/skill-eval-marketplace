# Contributing a skill

A skill is a directory under `skills/`. To be accepted it needs the skill itself
plus an `eval/` directory that lets the repo re-check it when its merge request changes.
GitLab discovers changed skills automatically; no per-skill CI registration is needed.
An affected skill without `eval/dataset.jsonl` fails CI selection.

This file is the checklist. For *why* — what the machinery does with these files,
the full `expect.*` reference, and the wire contracts that fail silently when
renamed — see [docs/adding-a-skill.md](docs/adding-a-skill.md)
([简体中文](docs/adding-a-skill.zh-CN.md)).

```
skills/my-skill/
  SKILL.md                     required — frontmatter `name` + `description`
  scripts/ references/ ...     whatever the skill needs
  eval/
    dataset.jsonl              required — the cases
    plugin.py                  optional — your own evaluators (layer 2)
    negative-control/          recommended for model judges; coverage checked by --strict
      expected.json            {"max_score": 0.30, "why": "..."}
```

Everything in `eval/` sits beside the skill on purpose: the skill's git sha then
identifies its scoring standard as well as its behaviour, so changes to either can be reviewed together. Model sampling can still change scores.

## Design for cost when authoring the skill

**Cost control is a design requirement. Use deterministic scripts or tools for
work whose rules are known; reserve model calls for understanding intent and
making decisions that require judgment.** Make this division before writing the
workflow, rather than trying to shorten prompts after an expensive run.

Put parsing, schema validation, calculations, format conversion, generation,
rendering and structural checks in reusable tools where their rules can be
specified. Have the model supply a compact structured input and interpret the
result. Tools should return concise evidence and actionable errors, with full
reports saved to files.

In `SKILL.md`, describe when to read references, reuse an existing result, run a
validator, or retry after a specific failure. Bound retries and tool use; avoid
repeated reads, dependency installation and rendering without changed inputs.
Review tool calls, tokens and elapsed time on representative cases. A tool-call
cap alone is not a monetary budget.

Use deterministic fixture checks first, then affected eval cases. Re-score an
existing recording when only the judge changes; scoring still costs model calls.
Keep correctness gates intact. See the authoring guide's
[cost design examples](docs/adding-a-skill.md#design-principle-deterministic-work-belongs-in-tools)
([简体中文](docs/adding-a-skill.zh-CN.md#设计原则确定性工作交给脚本和工具)).

## `SKILL.md` is also a scoring rubric

`SkillInstructionFollowing` reads it as the contract and checks whether the agent
did what it says. Two consequences:

- **A self-contradiction costs points.** A run that correctly follows rule A gets
  marked down for violating rule B when B contradicts A. Measured: adding one
  unconditional rule that contradicted a conditional one three sections earlier
  dropped three *correct* runs from 1.00 to 0.75.
- **Do not require what the harness cannot provide.** The sandbox gives the agent
  `Skill`, `Read`, `Write`, `LS`, `Bash` — there is no way to ask the user a
  question. A skill whose workflow said "you MUST ask the user before finalizing"
  scored 0.50 on every run for skipping a step it could not perform, and on runs
  with no metadata supplied it stalled asking and never wrote the deliverable at
  all. Make interaction conditional: ask when a user is present, proceed with
  best-guess drafts and `_(to confirm)_` markers when one is not.

## `dataset.jsonl`

One JSON object per case — on a single line, or pretty-printed across several;
the reader accepts both, and prose assertions read better pretty-printed.
`expect.*` drives the deterministic graders, which need no code from you:

```jsonc
{
  "id": "three-tier-web",
  "prompt": "Draw a standard three-tier web application on AWS ...",
  "seed_files": {"input.txt": "..."},      // staged into the workspace first
  "expect": {
    "skill": "my-skill",                   // must be selected; anything else fails
    "file_glob": "*.drawio",               // a matching file must be produced
    "tools_any_order": ["Skill", "Bash"],
    "tools_in_order":  ["Skill", "Bash"],
    "validator_pass_regex": "✓ PASS",      // must appear in some Bash output
    "handwrite_marker": "mxGraphModel",     // fails if Write bypassed the generator
    "artifact_regex":        ["shape=mxgraph\\.aws4\\."],
    "artifact_regex_absent": ["<mxCell[^>]*value=\"\""],
    "min_counts": {"resourceIcon": 8},
    "max_tool_calls": 25,
    "text_artifacts": [".md"],                // deliverable formats the judge reads
    "assertions": [                           // each judged, each its own gating row
      "The diagram nests a VPC inside a Region.",
      "Every subnet sits inside an Availability Zone rather than beside one."
    ]
  }
}
```

Notes that save debugging time:

- `handwrite_marker` catches the most common silent violation: the model writing
  the artifact by hand with `Write` instead of running your generator. The output
  can look fine while the skill was ignored entirely.
- `artifact_regex` runs over the produced file's **text**. A binary artifact has no
  text, so content regexes are meaningless there — check structure in a plugin.
- **Every** assertion is judged, as its own gating row (`Assertions[#3]` names the
  one that failed). Write them about text the kit can see — the trajectory and the
  text deliverables — since a claim about pixels or binary structure cannot be
  verified there; that belongs in a plugin. Verdicts are binary, so an ambiguous
  sentence buys you a row that flips between runs.
- A case whose point is that your skill should **not** be chosen belongs in the
  other skill's dataset, with `expect.skill` naming that other skill.

## `eval/plugin.py` — layer 2

Three optional functions. Import siblings in your `eval/` directory directly
(`import geometry`); the loader puts that directory on `sys.path`.

```python
from evalkit.plugins import ExtraExperiment, PrepareContext, Prepared

def applies(expect: dict) -> bool:
    """Does your layer apply to this case at all?

    Return False for a case that was never meant to produce your artifact, so it
    is not handed a checker with nothing to check. Without this, "produced nothing
    because nothing was asked for" and "was supposed to produce something and did
    not" collapse into the same not-applicable row.
    """
    return (expect.get("file_glob") or "").endswith(".drawio")

def prepare(ctx: PrepareContext) -> Prepared:
    """Render, measure, assemble. Write derived files under ctx.artifacts_dir."""
    return Prepared(findings=[...], media=[...], instruction="...", metadata={...})

def evaluators(prepared: Prepared, judge_model) -> list:
    """Your evaluators for this case."""
    return [MyEvaluator()]
```

`prepare` is separate from `evaluators` because rendering is your problem, not the
kit's — that is what keeps an Electron dependency out of a repo full of skills
that emit markdown.

Hand your deterministic findings to the model judge via `Prepared.findings`. A
vision model asked to re-derive something arithmetic already settled will
sometimes get it wrong and always cost you a round trip.

### Two things that will bite you

**Images belong in `media`, never in `expected_output`.** The prompt composer
interpolates that field into text, so an image there reaches the judge as a repr
of a *file path* and the judge fluently grades a picture it was never shown.

**One `Experiment` means one `case.input`.** Every evaluator in it sees the same
input. If you need a second image pair — a comparison against a style reference —
return an `ExtraExperiment` rather than appending to the existing media list.
Judges do not reliably ignore an image they were told to ignore; that leak was
measured, not assumed.

## The negative control

If your plugin includes a model judge, ship a deliberately bad artifact with a
declared score ceiling.

**Why this is recommended.** You own both the skill and the standard that scores it,
so you can raise your score by loosening the ruler — and it will not feel like
cheating at the time. This is measured, not hypothetical: one rubric here went
from 0.32 to 0.92, and only about half of that was the skill improving. The rest
was the rubric growing an anchored scoring table.

The control is how you tell the two apart. It must stay at or below its ceiling
across rubric edits. If it climbs, the ruler moved.

```python
# eval/check_negative_control.py — run it after every rubric change
score = MyQualityEvaluator(model=JUDGE).evaluate(case)[0].score
assert score <= json.load(open("negative-control/expected.json"))["max_score"]
```

One lesson from building the first one: **generate the bad artifact with your real
generator from a bad input.** A control built by hand-corrupting a file gets
rejected by your own validator before it ever reaches the judge, which makes it
untestable rather than bad.

## Checklist

- [ ] Deterministic work uses reusable scripts or tools; model steps have a
      stated need for judgment
- [ ] The workflow defines reuse, validation and bounded retry conditions;
      representative runs have been reviewed for tool calls, tokens and elapsed time
- [ ] Validators save complete evidence and return a concise result the agent
      and evaluator can inspect
- [ ] `SKILL.md` has frontmatter `name` + `description`, and contains no rule that
      contradicts another or that the harness cannot satisfy
- [ ] `eval/dataset.jsonl` has at least one case per distinct behaviour, and
      `uv run --locked skill-eval check` passes -- an `expect.*` key it does not know is
      a check that never runs, and this is the only thing that will tell you
- [ ] `uv run --locked skill-eval doctor --skill <name>` is clean on the machine you run the evals from
- [ ] `uv run --locked skill-eval setup --skill <name>` installs the skill dependencies
- [ ] `uv run --locked skill-eval run --skill <name>` exits 0; review its per-case rows
- [ ] Negative control present and below its ceiling, if you ship a model judge
- [ ] Noisy optional checks use `ExtraExperiment(gate=False)`; main Experiment rows always gate
- [ ] Review a completed report, then use `skill-eval baseline accept --report <path>`
- [ ] Run the framework tests before release; `tools/release.py` does not enforce evaluation gates

## Dependency declarations

`skill-eval setup --skill <name>` reads `requirements.txt` and `scripts/package.json`
from that skill. Add new Python distributions to a suitable optional dependency group
in the root `pyproject.toml` and run `uv lock` so installation is constrained by the
shared lock. Keep npm's `package-lock.json` beside its `package.json`.

Declare required system tools in `eval/dependencies.json`, for example:

```json
{
  "commands": ["required-for-all-cases"],
  "visual_commands": ["required-for-full-profile"],
  "case_commands": {"case-needing-video": ["ffmpeg", "ffprobe"]}
}
```

Doctor checks these commands for the selected cases before inference. Install them
through the host package manager. Negative controls are a coverage recommendation:
ordinary `check` warns when a plugin lacks `negative-control/expected.json`; strict
mode fails that warning. To run a control automatically, provide
`eval/check_negative_control.py`. Full profile runs available controls;
`core --negative-controls` runs them without per-case vision judges.
