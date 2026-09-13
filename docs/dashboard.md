# Public scoreboard on GitHub Pages

`.github/workflows/publish-dashboard.yml` publishes every evaluation of `main` to
<https://richardleey.github.io/skill-eval-marketplace/>. The page is the
marketplace's public record: which skills are in it, what each one scored on its
own cases, what the reviewers accepted as the baseline, and how the scores moved.

| Trigger | What runs |
| --- | --- |
| Push to `main` | Full evaluation of every skill, then publish |
| Weekly, Monday 03:17 UTC | Same; catches judge drift on unchanged skills |
| Manual **Run workflow** | Same |

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

1. The `eval` job is the PR workflow's eval job with full selection: `github_ci.py`
   treats `push` and `schedule` like `workflow_dispatch`. It always runs
   `evalkit.ci_report` and uploads `.eval/`, including on failure.
2. The `publish` job runs even when `eval` failed, so a red run appears on the site
   instead of vanishing. It checks out the `gh-pages` branch as a worktree (creating
   an orphan branch on first use), downloads the artifact, and runs:

   ```bash
   python -m evalkit.pages --site site --run .eval
   ```

   which appends the run, prunes the history to the last 30 runs, rebuilds
   `index.html` and the badges, and writes `.nojekyll`.
3. The job commits and pushes `gh-pages`, then deploys the same directory with
   `actions/upload-pages-artifact` and `actions/deploy-pages`. Publishes are
   serialized by a concurrency group so two merges cannot race on the branch.

The accepted column comes from `eval-baseline.json` on `main` and needs no model
call, so the site still shows reviewed scores when an evaluation fails or the
credentials are missing.

## Repository configuration

1. **Settings → Pages → Source: GitHub Actions.** The workflow's `configure-pages`
   step attempts this itself; do it by hand if the first deploy fails with a
   Pages-not-enabled error.
2. **OIDC trust.** The Bedrock role behind `EVAL_ROLE_ARN` must accept pushes to
   `main`, not only pull requests. Its trust policy `sub` condition needs
   `repo:<owner>/<repo>:ref:refs/heads/main` in addition to
   `repo:<owner>/<repo>:pull_request`. Without it the eval job fails and the site
   records a failed run.
3. **Branch `gh-pages`** is created by the first publish. Do not protect it with a
   required review; the workflow pushes to it with the default `GITHUB_TOKEN` and
   `contents: write` on the publish job only.

## Cost

Each publish runs the full core profile over every skill: one agent run and one
judge pass per case, twelve cases today, plus negative controls. If that is too
much per merge, drop the `push` trigger and keep `schedule`; the baseline column
still updates on every push because the site is rebuilt from `main`'s
`eval-baseline.json` whenever the workflow runs.

## Local preview

```bash
uv run --locked skill-eval run --skill visual-flow-webp --profile core
uv run --locked python -m evalkit.ci_report
GITHUB_REPOSITORY=<owner>/<repo> uv run --locked python -m evalkit.pages --site site --run .eval
open site/index.html
```

`site/` is ignored by git. Commit history lives only on `gh-pages`.
