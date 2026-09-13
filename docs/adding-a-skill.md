# How a skill lives in this repo

*English | [简体中文](adding-a-skill.zh-CN.md)*

[CONTRIBUTING.md](../CONTRIBUTING.md) is the checklist: what files a skill must
ship. This document is the *why* — what the machinery actually does with those
files, so that when a check fails you know which stage produced the number.

Read it once before adding your first skill. After that, CONTRIBUTING is enough.

## Design principle: deterministic work belongs in tools

**Consider cost while designing the skill. Let the model understand the request
and make semantic decisions; put repeatable work with explicit rules in
deterministic scripts or tools.** This reduces model calls, token use and retries,
while making the result easier to reproduce and verify.

Before writing `SKILL.md`, map each workflow step to its executor and evidence:

| Work | Executor | Evidence |
|---|---|---|
| Interpret requirements, choose components and relationships | Model | Compact structured specification |
| Parse input, validate schemas, resolve known identifiers | Script or tool | Validated data or specific field errors |
| Calculate layout, counts, scores, frames or duration | Script or tool | Values with explicit checks |
| Generate XML/HTML, convert formats, render files | Generator or renderer | Output paths and a validation report |
| Check file integrity, dimensions, required fields or unchanged inputs | Validator | Measured values, named checks and hashes where applicable |
| Assess meaning, clarity or visual quality beyond structural checks | Model, when needed | Reasoning grounded in the artifact and measured findings |

For a flow diagram, the model can decide the nodes, edges and animation steps in
a JSON spec. The renderer computes frames and duration; the validator checks
geometry and background preservation. Pass these findings to any model reviewer
instead of asking it to count frames or infer file integrity from a screenshot.
Structural validation still does not establish visual quality.

Write the workflow so it:

- **Reuses work when inputs are unchanged.** Read only relevant reference
  sections, check dependencies before installing, and reuse valid intermediate
  files. Invalidate reused results when inputs, configuration or tool versions
  change.
- **Makes correction specific.** Validate before expensive generation. Return
  field names and failed checks so the agent can fix the relevant input.
  Bound retries and stop with an explicit error when the limit is reached.
- **Preserves concise evidence.** Save a machine-readable report and return a
  short summary with named checks, measured values and file paths. Ensure the
  evaluator can read the full report; important evidence must not depend on the
  tail of a long console log.
- **Measures the cost of representative cases.** Review tool calls, model input
  and output tokens, retries and elapsed time. Set runtime limits appropriate to
  the task and use `expect.max_tool_calls` to detect workflow regressions. Tool
  count alone cannot guarantee a dollar limit; token volume and model pricing
  also matter.

Apply the same principle to evaluation: start with fixture-based script checks,
then run the affected cases. Phase 1 still invokes the agent model, even though
its graders are deterministic. Add model judging for questions that need it;
when only the judge changes, re-score a compatible recording instead of
executing the agent again. Re-scoring is still paid inference. Keep the existing
correctness gates and investigate failures rather than retrying until a run
happens to pass.

---

## 1. Anatomy

A skill is a directory under `skills/`. Nothing registers it; the loader finds it
by walking the directory.

```
skills/my-skill/
  SKILL.md                     required. Frontmatter `name` + `description`
  scripts/                     optional. Whatever the skill tells the agent to run
  references/                  optional. Background the skill may tell the agent to read
  examples/                    optional. Format samples
  eval/
    dataset.jsonl              required. The cases
    plugin.py                  optional. Your own evaluators (layer 2)
    negative-control/          recommended for model judges; --strict checks coverage
```

Two constraints follow from how discovery works
(`evalkit/sandbox.py:165`, `evalkit/plugins.py:200`):

- **`SKILL.md` must exist**, or the directory is invisible to the sandbox.
- **The frontmatter `name` is the skill's identity**, not the directory name. It
  is what the agent must type into the `Skill` tool and what `expect.skill` in
  your dataset must match. Keeping the two the same avoids a class of confusion
  nobody enjoys debugging.

`eval/` sits beside the skill deliberately: the skill's git sha then identifies
its scoring standard as well as its behaviour, so changes to either can be reviewed together; model sampling can still change scores.

---

## 2. What the agent sees

This is the part that surprises people, and it determines how you write
`SKILL.md`.

**`SKILL.md` is not in the system prompt.** The sandbox injects only a catalog of
`(name, description)` pairs, exactly like Claude Code's available-skills listing:

```xml
<available_skills>
  <skill>
    <name>my-skill</name>
    <description>…your frontmatter description…</description>
  </skill>
</available_skills>
```

So the agent must *choose* your skill from a description alone, then load it.
That is what makes `SkillSelectionAccuracy` a real measurement rather than a
formality — and it means **the `description` is load-bearing**. It is the only
thing standing between your skill and being passed over. Write it as trigger
conditions ("Use when the user asks to …"), not as a summary.

**The agent's toolbox is deliberately small** (`sandbox.py:427`):

| Tool | Notes |
|---|---|
| `Skill(skill)` | Returns your `SKILL.md`. The name and the `skill` argument are a wire contract — see §6 |
| `Read(path)` | Workspace, or read-only into the skill registry |
| `Write(path, content)` | Workspace only |
| `LS(path)` | |
| `Bash(command)` | Runs with the workspace as cwd. This is how your `scripts/` get executed |

There is **no way to ask the user a question.** A `SKILL.md` that says "you MUST
confirm with the user before finalizing" scores 0.50 on every run for skipping a
step the harness cannot perform — and on runs with no metadata supplied, it
stalls asking and never writes the deliverable at all. Make interaction
conditional: ask when a user is present, proceed with best-guess drafts and
`_(to confirm)_` markers when one is not.

**When `Skill` is called, the whole skill directory is copied into the
workspace** (`sandbox.py:194`). Directories in `SKIP_DIRS` — `node_modules`,
`.git`, `__pycache__`, `.venv` — are symlinked back to the registry instead of
copied, so a big dependency tree stays cheap. Two consequences:

- Every relative path in your `SKILL.md` resolves against the copy, whose
  location the tool result tells the agent. Say `<skill-dir>/scripts/…` and
  define `<skill-dir>` once, rather than hardcoding a path.
- Every copied file is **hashed as a baseline**. Artifact collection later skips
  any file whose hash is unchanged, so your skill's own source is never mistaken
  for the agent's output. A file the agent *modifies* does show up.

---

## 3. The two phases

```bash
# phase 1 — execute, record
python evalkit/run_eval.py --target local

# phase 2 — score the recording
uv run --with strands-agents-evals python evalkit/run_strands_eval.py
```

The phases remain separate so a recording can be scored without executing the agent
again. Judges still make paid calls and can vary with model sampling, rubric changes,
reference files and dependencies. Both phases now share the project's uv environment.

The commands above are low-level entry points. Prefer `uv run --locked skill-eval run
--skill <name>` for setup-aware execution and `skill-eval score --run <id>` for an
archived recording. See [directory and archive layout](architecture.md).

### Phase 1, step by step

1. Load every `skills/*/eval/dataset.jsonl` (or `--cases`, or `--only <id>`).
2. For each case: fresh temp workspace, write `seed_files`, run the agent.
3. **Retry transient Bedrock failures** — throttling, dropped streams — up to 3×
   (`run_eval.py:72`). A network fault must not be reported as a skill defect.
4. **Tool budget**, charged on entry, not on completion (`sandbox.py:90`), so an
   over-limit call cannot still write its file. Raised as a `BaseException`
   because Strands' `@tool` wrapper swallows `Exception` and hands it back to the
   model as a retryable error — measured, a budget of 2 once let a run make 51
   calls and still report `status: ok`.
5. **Collect artifacts**: everything in the workspace whose hash differs from the
   baseline. Text files ≤ 256 KB are inlined; other collected files retain their bytes on disk.
6. **Grade deterministically** against `expect.*` (§4).
7. Write `results.json`, and save each artifact under `artifacts/<case-id>/`.
   That directory is **cleared first** — otherwise files from earlier runs
   survive beside the current ones and reading `artifacts/` gives you a mix of
   runs presented as one.

Exit code is 0 only if every case passed every deterministic check.

### Phase 2, step by step

1. Join `results.json` to the dataset — the recording says what happened, the
   dataset says what was expected.
2. Rebuild the trajectory as a Bedrock-style message list (`trajectory.py`).
3. **Append the text deliverables read from disk** as a final user turn
   (`run_strands_eval.py:113`). Necessary because the sandbox truncates every
   string tool argument at 400 chars, so a `Write` of a 2.7 KB markdown file looks
   cut off — and a judge scoring "is there a Total row?" would honestly report it
   cannot tell, on a file that has one. Defaults to `.md`, `.mmd`, `.txt`, `.csv`;
   override with `expect.text_artifacts` for other text formats.
4. Call your `plugin.applies(expect)`; if true, `plugin.prepare(ctx)` (§5).
5. Run layer 1 + your layer 2 in one `Experiment`, concurrently.
6. Write `eval-report.json`. Exit non-zero if any **gating** row failed, or if
   fewer cases were scored than requested — a phase-1 crash on the hardest case
   would otherwise *raise* the aggregate and still exit 0.

---

## 4. `dataset.jsonl` — the contract

As many cases as the skill needs, written either one object per line or
pretty-printed across several lines — `evalkit/dataset.py` decodes the file as a
stream of JSON objects, so both work, and a top-level array works too. Prefer
pretty-printed once a case carries prose assertions or multi-kilobyte
`seed_files`: a case that must fit on one line is a case nobody re-reads before
editing it. `//` comment lines are skipped (replaced by blank lines, so a parse
error still reports the line number your editor shows).

```jsonc
{
  "id": "three-tier-web",
  "prompt": "Draw a standard three-tier web application on AWS …",
  "seed_files": {"input.txt": "…"},
  "expect": { /* see below */ }
}
```

### Top-level keys

| Key | Meaning |
|---|---|
| `id` | Case id. Names the `artifacts/<id>/` directory; must be unique across **all** skills |
| `prompt` | The user turn |
| `seed_files` | `{relpath: content}`, staged into the workspace before the agent runs. A value may be `{"from": "<path relative to the dataset>"}` to read a fixture from disk instead of inlining it |

An adversarial case deliberately pits the user's instruction against the skill's —
"just give me the XML, don't run any scripts" when the skill says always run the
generator. Nothing in phase 2 scores fidelity to the *user*, so such a case needs no
special handling: `SkillInstructionFollowing` reads the skill's own `SKILL.md` as its
rubric, and holding the rule under pressure is what it rewards.

### `expect.*` — the deterministic graders

Every key is optional; an absent key is recorded as "no expectation" and passes.

| Key | Grader | Fails when |
|---|---|---|
| `skill` | `skill_selected` | the named skill was never loaded |
| — | `no_wrong_skill` | **any other** skill was loaded. Always runs; no key to set |
| — | `completed` | the run errored or blew the tool budget. Always runs |
| `file_glob` | `file_produced` | no produced file matches the glob |
| `validator_pass_regex` | `validator_passed` | the pattern appears in no `Bash` output |
| `handwrite_marker` | `generator_not_bypassed` | a `Write` call's content matches the pattern |
| `max_tool_calls` | `tool_budget` | more calls than the cap |
| `tools_any_order` | `tools_used` | one row per tool; fails per tool never called |
| `tools_in_order` | `tools_used` | the sequence is not a **subsequence** of the trace |
| `artifact_regex` | `artifact_regex` | a pattern has 0 matches in the artifact text |
| `artifact_regex_absent` | `artifact_regex` | a pattern has ≥ 1 match |
| `min_counts` | `count_at_least` | `{pattern: n}`; fewer than `n` matches |
| `text_artifacts` | — | extensions counted as readable evidence for the judge. Defaults to `.md .mmd .txt .csv`; **set it if your deliverable is `.json`, `.yaml`, `.html`, `.sql`** or the judge scores your workflow blind. `[]` disables |
| `assertions` | `Assertions` (layer 1, judged) | **every** assertion is verified, one report row each (`Assertions[#1]`, `Assertions[#2]`, …), and any one of them failing fails the run. See below for how to write one |
| *(your own keys)* | your plugin | e.g. `reference_image`, read by the `aws-drawio-diagram` and `visual-flow-webp` plugins. **A plugin convention, not an SDK field** — see below |

Behaviours worth knowing before you write your first case:

- **One skill per case.** Multi-skill evaluation is out of scope: there is no
  `expect.skills` list, layer 2 resolves exactly one plugin from `expect.skill`,
  and `no_wrong_skill` fails a run that loaded a second skill even if the first
  was correct. If a task genuinely needs two skills, split it into two cases.
- **`tools_in_order` is a subsequence match, not adjacency**
  (`tool_trajectory.py:41`). A skill legitimately reads a reference file between
  the steps it prescribes, and requiring adjacency would fail a run that did
  everything asked plus one reasonable extra step.
- **`tools_in_order` falls back to `tools_any_order`** when building the expected
  trajectory (`trajectory.py:119`). Declaring only `any_order` still
  feeds the order-aware evaluators.
- **`handwrite_marker` catches the most common silent violation**: the model
  writing the artifact by hand with `Write` instead of running your generator.
  The output can look fine while the skill was ignored entirely. Pick a string
  that only your generator's output contains — `mxGraphModel` for drawio XML.
- **`artifact_regex` runs over the file's *text***. A binary artifact has no
  text, so content regexes are meaningless there; check structure in a plugin.
- **`validator_pass_regex` is the highest-signal single check** if your skill
  ships its own validator: it proves the prescribed path was *executed*, not
  merely described.
- **A case whose point is that your skill should NOT be chosen belongs in the
  other skill's dataset**, with `expect.skill` naming that other skill.

### Writing `assertions`

An assertion is a prose claim about the deliverable that a regex cannot express:
"the 500-record cap **and** the reason raising it fails are both recorded", "the
pinned version appears as a constraint rather than as generic advice". One judge
call per case verifies all of them and returns a verdict per assertion, matched
**by index**; a missing verdict fails that assertion rather than shifting the
others onto the wrong claims (`evalkit/assertions.py`).

Four rules follow from how they are judged:

- **Write about text the kit can see** — the trajectory, and the text deliverables
  appended to it (see `expect.text_artifacts`). An assertion about pixels or
  binary structure cannot be verified here and belongs in `eval/plugin.py`, which
  gets the file itself.
- **Verdicts are binary.** There is deliberately no "cannot tell": an escape
  hatch is taken on exactly the assertions worth gating, and the resulting row is
  dropped from the mean.
- **A compound assertion holds only if every part holds**, and the judge is told
  to name the missing part. That is usually what you want — but split it if you
  would rather see which half failed as its own row.
- **Say "rather than"** when the distinction matters. "X is recorded as a
  constraint" is satisfied by X appearing anywhere; "recorded as a constraint
  rather than as generic advice" is not.

An ambiguous assertion is judge noise waiting to happen. Measured: "the file
contains no file listing for its own sake" flipped between runs on an unchanged
artifact — the file had an annotated list of four files, which is arguably each
way. If a row moves without a cause, sharpen the sentence before you distrust the
judge.

### `reference_image` — a convention, not an SDK field

Nothing in `strands_evals` knows the name `reference_image`. What the SDK gives you
is `MultimodalInput(media=[ImageData(source=…, format="png"), …])`, and a reference
is simply **the first image in that list**. Everything else is a per-skill
agreement between a dataset key and the plugin that reads it —
`aws-drawio-diagram` and `visual-flow-webp` both implement it, in ~20 lines each:

1. `_reference_image(expect)` resolves `expect.reference_image` against the skill's
   `eval/` directory. **A declared-but-missing file returns a problem, not `None`** —
   failing open is invisible: the case scores under the plain rubric, still passes,
   and nothing says the comparison never ran. That silence sat undetected in the
   drawio skill until a row name was noticed.
2. `prepare` inserts the reference at `media[0]` and records
   `metadata["reference_path"]`.
3. The instruction says *"image 1 is the reference, image 2 is under evaluation"* —
   the prompt composer emits media blocks uncaptioned and appends the text after
   them, so nothing else distinguishes the two pictures.
4. `evaluators` appends a `REFERENCE_COMPARISON` block to the rubric and renames the
   row `…[vs reference]`, because a reference-scored row and a plain one are answers
   to different questions and must not be averaged together.

Never put an image in `expected_output`: that field is interpolated into text
(`f"<ExpectedOutput>{…}</ExpectedOutput>"`), so the judge receives a pydantic repr
of a *file path* and grades a picture it was never shown, fluently.

Two things a reference is for, and one it is not. **Topical** (per case, "is this a
good answer to *this* request") goes in the main visual judge's media. **Style**
(global, "does it meet the house style") belongs in its own `ExtraExperiment` pass —
drawio does this with an AWS-drawn architecture diagram, non-gating. And a style
reference is meaningless when your skill *owns* the renderer: both images then come
out of one code path, so palette and typeface are identical by construction and
carry no signal. `visual-flow-webp`'s `REFERENCE_COMPARISON` tells the judge that
outright and scopes it to composition, coverage and density — what the spec author
actually decided.

---

## 5. `eval/plugin.py` — layer 2

Layer 1 (skill selection, instruction following, tool trajectory, the case's
`assertions`, the `expect.*` graders) is owned by the kit and needs no
configuration from you. It
knows nothing about what your skill produces, which is why the same judges run
unchanged across a diagram generator and a meeting-minutes skill.

Layer 2 is everything the kit cannot know: whether a diagram's nodes sit in the
right container, whether a cost table's arithmetic holds, whether minutes lead
with the decision. Three optional functions:

```python
from evalkit.plugins import ExtraExperiment, PrepareContext, Prepared

def applies(expect: dict) -> bool:
    """Does your layer apply to this case at all?"""
    return (expect.get("file_glob") or "").endswith(".drawio")

def prepare(ctx: PrepareContext) -> Prepared:
    """Render, measure, assemble. Write derived files under ctx.artifacts_dir."""
    return Prepared(findings=[...], media=[...], instruction="...", metadata={...})

def evaluators(prepared: Prepared, judge_model) -> list:
    return [MyEvaluator()]
```

Import siblings in your `eval/` directory directly (`import geometry`); the
loader puts that directory on `sys.path` first.

**If your evaluator emits one `EvaluationOutput` per item** — per node, per table
row, per rule — set `expand_rows = True` on it. The framework otherwise averages
those outputs into a single report row before anything is written, so a reader
sees `MyEvaluator 0.71` and not which node was wrong. With the flag, each output
becomes its own row, named `MyEvaluator[<label>]` from the output's `label`. The
gate is unchanged either way: an aggregate passes only when all its outputs do.

**Why `applies` matters.** Return `False` for a case that was never meant to
produce your artifact. Without it, "produced nothing because nothing was asked
for" and "was supposed to produce something and did not" collapse into the same
not-applicable row, and the suite reports failures it does not have.

**Why `prepare` is separate from `evaluators`.** Rendering is your problem, not
the kit's. draw.io needs an Electron binary, Mermaid needs mermaid-cli, a
markdown table needs nothing. Baking a renderer into the kit would make every
team carry a dependency for a format they never emit.

### Order your layers cheapest-first

`aws-drawio-diagram` is the worked example:

| Layer | Cost | Gates? |
|---|---|---|
| `geometry.py` → `DiagramLayoutEvaluator` | exact arithmetic, no model, no network | **yes** |
| `render.py` + vision judge | one Electron export + one multimodal call | yes |
| style comparison vs an official AWS reference | one more multimodal call | **no** (`gate=False`) |

**Hand your deterministic findings to the model judge** via `Prepared.findings`.
A vision model asked to re-derive something arithmetic already settled will
sometimes get it wrong and always cost you a round trip.

**Renderer coverage must be read explicitly.** The CLI preflight checks declared
system commands before a full-profile run. Low-level plugin behavior still degrades
when a render cannot be produced; report findings and coverage expose this.

 `render.py`
returns `(None, reason)` rather than raising, and the plugin degrades to
geometry-only with the reason in `findings`. A laptop without draw.io installed
must not look like a skill that cannot lay out a diagram.

### Two things that will bite you

**Images belong in `media`, never in `expected_output`.** The prompt composer
interpolates that field into text, so an image there reaches the judge as a repr
of a *file path* — and the judge fluently grades a picture it was never shown.

**One `Experiment` means one `case.input`.** Every evaluator in it sees the same
input. If you need a second image pair — a comparison against a style reference —
return an `ExtraExperiment` rather than appending to the existing media list.
Judges do not reliably ignore an image they were told to ignore; that leak was
measured, not assumed.

### Gating vs tracked

Deterministic checks gate the build: they are arithmetic, and across six
iterations of the drawio skill 89 of 89 never once moved without a real cause.

Model-judged rows are noisier. Measured on the same artifact with no code change:
0.72 vs 0.85 for one defect class; 0.82 → 0.97 from editing a single label. A
gate that trips on that much noise gets switched off by the third team to adopt
the kit — and it takes the trustworthy checks down with it. Put such optional evaluators in an
`ExtraExperiment(gate=False)`: the entire extra pass is reported but excluded from
the exit code. Main Experiment rows always gate; setting an arbitrary evaluator
attribute named `gate` has no effect.

### The negative control

If your plugin includes a model judge, ship a deliberately bad artifact with a
declared score ceiling in `negative-control/expected.json`.

**Why it is recommended.** You own both the skill and the standard that scores it, so
you can raise your score by loosening the ruler — and it will not feel like
cheating at the time. Measured: one rubric here went from 0.32 to 0.92, and only
about half of that was the skill improving. The rest was the rubric growing an
anchored scoring table. The control is how you tell the two apart. If it climbs
above its ceiling across a rubric edit, the ruler moved.

**Generate the bad artifact with your real generator from a bad input.** A control
built by hand-corrupting a file gets rejected by your own validator before it
reaches the judge, which makes it untestable rather than bad.

---

## 6. Wire contracts you must not rename

These look like style choices and are not. Each was established empirically; each
fails *silently* when violated, which is the expensive kind.

| Contract | Where | What breaks |
|---|---|---|
| Tool named exactly `Skill`, argument named exactly `skill` | `sandbox.py:275`, `trajectory.py:37` | every skill-level evaluator sees an agent that loaded nothing. `load_skill`, `SkillTool`, or a `{"name": …}` payload are all invisible |
| `<available_skills><skill><name>…` block in the system prompt | `sandbox.py:227` | harnesses recover the catalog by regex. A plain `- name: description` list parses as prose, the catalog comes back empty, and the selection judge rates a choice whose alternatives it cannot see |
| The full `SKILL.md` body in the tool result, not the truncated `result_head` | `trajectory.py:54` | `SkillInstructionFollowing` judges adherence against a one-line summary of the instructions |
| `output` / `trajectory` as the replay task's return keys | `run_strands_eval.py:313` | `actual_output` / `actual_trajectory` are silently ignored, and every skill evaluator reports "no trajectory provided" |
| Judge model id is a full inference profile | `run_strands_eval.py:76` | `global.anthropic.claude-sonnet-4-5` looks plausible and is rejected, surfacing as every judge scoring 0.00 with a `ValidationException` — i.e. as a failing skill rather than a bad config |

---

## 7. `SKILL.md` is also a scoring rubric

`SkillInstructionFollowing` reads your `SKILL.md` as the contract and checks
whether the agent did what it says. You do not write a scoring standard, because
you already wrote one.

The side effect is worth internalising: **a self-contradiction costs points.** A
run that correctly follows rule A gets marked down for violating rule B when B
contradicts A. Measured: adding one unconditional rule that contradicted a
conditional one three sections earlier dropped three *correct* runs from 1.00 to
0.75. The agent was right; the document was wrong.

Practical consequences for how you write it:

- **Make conditional rules visibly conditional.** Not "paste the XML into your
  reply" but "**if the user asked for the XML**, paste it — otherwise report the
  path". `skills/aws-drawio-diagram/SKILL.md:44` carries the scar from getting
  this wrong.
- **Do not require what the harness cannot do** (§2).
- **Say when a step is a precondition rather than a step.** "If `node_modules/`
  exists, skip this" — otherwise runs reinstall packages that were already there.
- **Warn against the traps your own directory sets.** An `examples/` file whose
  name matches the request is the classic one: passing
  `examples/coinbase-low-latency.json` to the generator because the user said
  "Coinbase low latency" produces a diagram of *the example's* architecture, and
  every check downstream passes because the example is well-formed.

---

## 8. Adding a skill, in order

1. `mkdir -p skills/my-skill/eval` and write `SKILL.md` with frontmatter
   `name` + `description`. Write the description as trigger conditions (§2).
2. Add whatever the skill needs — `scripts/`, `references/`, `examples/`. If it
   has npm/pip dependencies, they must install from within the directory, and
   install them with `uv run --locked skill-eval setup --skill my-skill`;
   declare system commands in `eval/dependencies.json` (see CONTRIBUTING).
3. Write `eval/dataset.jsonl`: at least one case per distinct behaviour. Start
   with deterministic `expect.*` only and no domain plugin. The full CLI still runs shared judges; phase 1 alone has no judge. This alone catches
   skill-not-selected, artifact-not-produced, generator-bypassed.
4. Run `uv run --locked skill-eval run --skill my-skill --only <your-case>`.
   Inspect failures, correct their cause, and rerun when a relevant change
   warrants it. Acceptance requires the execution and scoring gates to pass.
5. Use `uv run --locked skill-eval score --run <run-id>` when iterating on judges.
   Read the recorded `SkillInstructionFollowing` reasoning to see which step it
   thinks you skipped. Re-scoring uses model calls but avoids repeating agent
   execution.
6. *Only if layer 1 cannot express what matters*, add `eval/plugin.py`. Start
   with a deterministic evaluator; add a model judge last.
7. If you added a model judge, ship `negative-control/` with a ceiling, and
   re-check it after every rubric edit.
8. Add a row to the README's Skills table.

### Checklist

- [ ] Deterministic steps use scripts or tools with explicit inputs and verifiable outputs
- [ ] Reuse and retry conditions are documented; representative cost metrics reviewed
- [ ] `SKILL.md` frontmatter has `name` + `description`
- [ ] No rule contradicts another, and none requires asking the user
- [ ] `eval/dataset.jsonl` has one case per distinct behaviour, unique `id`s
- [ ] `run_eval.py --only <case>` passes every deterministic check
- [ ] `run_strands_eval.py --only <case>` exits 0
- [ ] Negative control present and below its ceiling, if you ship a model judge
- [ ] Optional noisy checks are in an `ExtraExperiment(gate=False)`
- [ ] README Skills table updated

---

## 9. Where to look when something is wrong

| Symptom | Likely cause |
|---|---|
| `skill_selected` fails | Your `description` does not read as a trigger. The agent never saw `SKILL.md` |
| `no_wrong_skill` fails | Two descriptions overlap. Narrow one, and add a selection case to the *other* skill's dataset |
| `validator_passed` fails but the artifact looks fine | The agent described your script instead of running it. Check the trace for a `Bash` call |
| `generator_not_bypassed` fails | The agent hand-wrote the artifact with `Write`. Usually a `SKILL.md` that explains the format before insisting on the generator |
| Every judge scores 0.00 with a `ValidationException` | Bad `JUDGE_MODEL_ID` — a bare family name instead of a full inference profile (§6) |
| Every judge scores 0.00 on an OpenAI-compatible endpoint | The endpoint or model does not do tool calling. Judges request their verdict as a tool, not as JSON mode — see `evalkit/models.py` |
| A layer-2 row is `NOT_APPLICABLE` unexpectedly | `applies()` returned `False`, or `prepare()` could not find the artifact. Its `findings` say which |
| A judge grades something it was never shown | An image in `expected_output` instead of `media` (§5) |
| Layer-2 score jumped and you did not change the skill | You changed the rubric. Check the negative control before believing the number |
