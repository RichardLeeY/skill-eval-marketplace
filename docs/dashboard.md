# Public scoreboard on GitHub Pages

`.github/workflows/publish-dashboard.yml` publishes every evaluation of `main` to
<https://richardleey.github.io/skill-eval-marketplace/>. The page is the
marketplace's public record: which skills are in it, what each one scored on its
own cases, what the reviewers accepted as the baseline, and how the scores moved.

GitHub Pages serves the `gh-pages` branch directly, so the scoreboard is just
files on that branch. Two things write to it:

| Writer | When | Needs |
| --- | --- | --- |
| `publish-dashboard.yml` | Push to `main`, weekly Monday 03:17 UTC, manual **Run workflow** | A model credential in the repository (see below), otherwise it only rebuilds the site from history and the baseline |
| `tools/publish_scoreboard.py` | Whenever you run it after a local evaluation | Your own AWS profile on the laptop; no repository secret |

## Publishing without any repository secret

If the repository cannot hold an IAM role or API key, run the evaluation locally
and push the result. The workflow then keeps the site consistent but never calls a
model.

```bash
uv run --locked skill-eval setup --skill visual-flow-webp        # once per skill
uv run --locked skill-eval run --skill visual-flow-webp --profile core --negative-controls
uv run --locked python -m evalkit.ci_report                        # writes .eval/summary.json
uv run --locked python tools/publish_scoreboard.py                # appends to gh-pages, pushes
```

Repeat the `run` line with more `--skill` flags to publish several skills in one
entry. The publish tool refuses to run when HEAD has uncommitted changes or is not
on `origin/main`, so every published run points at a commit visitors can open;
`--allow-dirty` overrides that. `--dry-run` builds `site/` without committing.

The credentials stay on your machine. What leaves it is the allowlisted evidence
described below, attributed to your git identity on the `gh-pages` branch.

## Credentials the workflow can use

`preflight` picks a provider from repository secrets, in this order:

| Secret | Provider | Notes |
| --- | --- | --- |
| `EVAL_ROLE_ARN` | Bedrock through OIDC | Same role as the PR workflow. Its trust policy must also accept `sub: repo:<owner>/<repo>:ref:refs/heads/main` |
| `EVAL_OPENAI_API_KEY` (+ optional `EVAL_OPENAI_BASE_URL` secret, `EVAL_MODEL_ID` / `EVAL_JUDGE_MODEL_ID` variables) | Any OpenAI-compatible endpoint | Installs the `openai` extra. Models need tool calling; the judge needs image input for visual cases. The baseline records the judge, so a different judge shows as a provenance change, not a regression |
| neither | none | `eval` is skipped, `publish` rebuilds from history |

## What is on the site

| Path | Content |
| --- | --- |
| `index.html` | Skill table (status, latest mean and minimum, accepted baseline, trend, badge), then one row per published run with a link to its dashboard and CI run |
| `runs/<sha>/dashboard.html` | The per-run dashboard from `evalkit.ci_report`, unchanged, with its evidence links working |
| `runs/<sha>/runs/…` | Allowlisted evidence: `run.json`, `cases.json`, `results.json`, `execute.log`, `report.json`, `report.meta.json`, and PNG/WebP/GIF/JPEG/SVG previews up to 2 MB |
| `data/index.json` | History, newest first, one entry per run: commit, date, status, per-skill and per-case scores |
| `data/runs/<sha>.json` | That run's `summary.json` |
| `badge/<skill>.json`, `badge/marketplace.json` | [shields.io endpoint](https://shields.io/badges/endpoint) badges: green when the skill passed and scored at or above the threshold, yellow when the score is high but a gate failed, red below the threshold, grey without a run |

The site carries no scripts. The trend column is inline SVG. Video and any other
binary stay in the 14-day **skill-evaluation** artifact; `evalkit.pages.allowed`
is the single place that decides what is public.

## How it works

1. `preflight` decides the provider. `eval` is the PR workflow's eval job with full
   selection: `github_ci.py` treats `push` and `schedule` like `workflow_dispatch`.
   It always runs `evalkit.ci_report` and uploads `.eval/`, including on failure.
2. `publish` runs whether `eval` passed, failed or was skipped, so a red run appears
   on the site instead of vanishing. It checks out `main` for the baseline and skill
   list, checks out `gh-pages` as a worktree (creating an orphan branch on first
   use), downloads the artifact if there is one, and runs:

   ```bash
   python -m evalkit.pages --site site --run .eval
   ```

   which appends the run, prunes the history to the last 30 runs, rebuilds
   `index.html` and the badges, and writes `.nojekyll`. Output is deterministic, so
   a rebuild with no new run produces no commit.
3. The job commits and pushes `gh-pages`; GitHub Pages deploys the branch. Publishes
   are serialized by a concurrency group so two merges cannot race on the branch.

The accepted column comes from `eval-baseline.json` on `main` and needs no model
call, so the site still shows reviewed scores when an evaluation fails or the
credentials are missing.

## Repository configuration

1. **Settings → Pages → Build and deployment → Deploy from a branch: `gh-pages`,
   `/ (root)`.** The repository must be public or on a plan that includes Pages.
   The first publish creates the branch; enable Pages after that, or with
   `gh api -X POST repos/<owner>/<repo>/pages -f build_type=legacy -f 'source[branch]=gh-pages' -f 'source[path]=/'`.
2. **Optional credential**, see the table above. None is required.
3. **Branch `gh-pages`** is written by the workflow with the default `GITHUB_TOKEN`
   (`contents: write` on the publish job only) and by `tools/publish_scoreboard.py`
   with your git identity. Do not require reviews on it.

## Cost

With a credential configured, each publish runs the full core profile over every
skill: one agent run and one judge pass per case, plus negative controls. If that
is too much per merge, drop the `push` trigger and keep `schedule`; the baseline
column still updates on every push because the site is rebuilt from `main`'s
`eval-baseline.json` whenever the workflow runs. Without a credential the workflow
costs nothing beyond a few seconds of runner time.

## Local preview

`python tools/publish_scoreboard.py --dry-run` builds `site/` from the current
`.eval` without committing; open `site/index.html`. `site/` is a `gh-pages`
worktree and is ignored by git on `main`.
