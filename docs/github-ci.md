# GitHub pull request evaluation

`.github/workflows/skill-eval.yml` is the GitHub Actions counterpart of
`.gitlab-ci.yml`. It starts when a pull request is opened or receives a new commit,
or when someone uses **Run workflow** on the Actions tab. Ordinary pushes and tags
do not start it. A new push to the same PR cancels the run in flight.

| Change / trigger | Checks | Model evaluation |
| --- | --- | --- |
| `skills/visual-flow-webp/**` | Repo validation, unit tests, security scans | visual-flow-webp only |
| Several skill directories | Same checks | All affected skills |
| `evalkit/**`, `tests/**`, root dependency files, baseline, either CI file, root `AGENTS.md` / `CLAUDE.md`, files directly under `skills/` | Same checks | All skills |
| Root README or `docs/**` only | Same checks | None |
| Manual **Run workflow** | Same checks | All skills |
| PR from a fork | Same checks | None (no credentials for forks) |

## Jobs

Two `lint` jobs run in parallel and both must pass before `eval` starts, so a
broken dataset or a scanner finding never buys an agent run.

* `repo-check`: `skill-eval check`, the selection plan, then the unit tests with
  the `visual` extra and `ffmpeg`. Its outputs (`eval_required`, `skills`, `mode`)
  gate the eval job.
* `security-scan`: bandit on the kit and every skill's `eval/` and `scripts/`,
  semgrep with the Python and JavaScript rule packs at `ERROR` severity.
* `eval`: assumes the Bedrock role, runs `skill-eval setup` and
  `skill-eval run --profile core --negative-controls` with the same repeated
  `--skill` arguments, then always builds the dashboard and uploads `.eval/` as
  the **skill-evaluation** artifact (14-day retention).

## Selection

Selection lives in `evalkit/github_ci.py`, which reuses `select_skills` and
`run_selected` from `evalkit/gitlab_ci.py`. For a pull request it reads the base
and head commits from the event payload, takes their `git merge-base`, and diffs
from that base to the PR head. This covers every commit in the PR while ignoring
commits the target branch gained after the PR forked, which GitHub's merge checkout
would otherwise include. `fetch-depth: 0` keeps the merge base available; a missing
base is an error, not an empty diff. Renames include old and new paths; deleting a
complete skill is recorded without running unrelated skills. A remaining affected
skill without its dataset is an error. New skills are discovered from `skills/`
without editing the workflow.

The plan runs twice, once in `repo-check` to decide whether `eval` is needed and
once in `eval` itself. Both read the same event payload, so they agree.

## Repository configuration

1. Create an IAM role for the evaluation with `bedrock:InvokeModel` and
   `bedrock:InvokeModelWithResponseStream` on the configured models. Trust GitHub's
   OIDC provider `token.actions.githubusercontent.com` with a condition on
   `sub` such as `repo:<owner>/<repo>:pull_request` and
   `repo:<owner>/<repo>:ref:refs/heads/main`. Do not trust `repo:<owner>/*`.
2. Store the role ARN as the repository secret `EVAL_ROLE_ARN`. The eval job has
   `id-token: write`; nothing else in the workflow does.
3. Under **Settings → Branches**, protect `main` and require the `repo-check`,
   `security-scan` and `eval` status checks. The YAML itself cannot prevent direct
   pushes.

If the secret is missing, `configure-aws-credentials` fails and the eval job fails
with it. `run_selected` additionally refuses to start when no AWS credential
variable is present, so a required evaluation cannot silently pass.

Fork pull requests cannot read secrets or mint OIDC tokens. The workflow skips
`eval` for them explicitly; run the evaluation on a branch in this repository
instead of broadening the role's trust.

## Dashboard and evidence

The `always()` step after evaluation runs `evalkit.ci_report` without model calls
and writes:

* `.eval/dashboard.html`: case scores, failed gate rows with complete reasons,
  baseline deltas and provenance warnings, negative controls, evidence links.
* `.eval/summary.json`: machine-readable summary for a notification job.
* `.eval/summary.md`: the same summary as Markdown. It is also appended to the
  job's step summary, so the Actions run page shows scores and failing rows
  without downloading anything.
* `.eval/selection.json`: selected skills, shared changes, removed skills, diff
  SHAs, selection errors and exit code.
* `.eval/runs/`: original execution, scoring, metadata and generated artifacts.

Download and extract the **skill-evaluation** artifact, then open
`.eval/dashboard.html` locally to follow the evidence links. The report passes
`JOB_STATUS` from `${{ job.status }}` so a failed or cancelled job is never shown
as passing. A runner loss or hard timeout may prevent the report and upload.

## Differences from GitLab

* Credentials: OIDC role assumption through `aws-actions/configure-aws-credentials`
  instead of the GitLab runner's credential vendor and `AWS_CREDS_TARGET_ROLE`.
* Change filtering: decided by the plan step and job outputs rather than
  `rules: changes`, so the eval job is listed as skipped instead of absent.
* Artifacts: an uploaded archive plus the step summary, instead of GitLab's
  exposed **Skill evaluation** link.
* Root: the runner is not root, so `ffmpeg` is installed with `sudo` in the
  workflow before `run_selected` checks for it.
