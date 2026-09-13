# GitLab merge request evaluation

GitLab calls a pull request a **Merge Request (MR)**. The repository pipeline starts
when an MR is created or receives a new commit, or when someone uses **Run pipeline**
in the GitLab UI. Ordinary push and tag pipelines are excluded. GitHub's separate
workflow is unchanged.

| Change / trigger | Checks | Model evaluation |
| --- | --- | --- |
| `skills/visual-flow-webp/**` | Repo validation, unit tests, security scans | visual-flow-webp only |
| Several skill directories | Same checks | All affected skills |
| `evalkit/**`, `tests/**`, root dependency files, baseline, `.gitlab-ci.yml`, root `AGENTS.md` / `CLAUDE.md`, files directly under `skills/` | Same checks | All skills |
| Root README or `docs/**` only | Same checks | None |
| Manual UI pipeline | Same checks | All skills |

The `lint` stage must succeed before `eval` starts. Both dependency setup and the
core evaluation receive the same repeated `--skill` arguments. Negative controls
run for the selected skills that provide one. MP4 dependencies are installed for
visual-flow. Full per-case vision judging remains a separate full-profile run.

Selection lives in `evalkit/gitlab_ci.py`. It compares
`CI_MERGE_REQUEST_DIFF_BASE_SHA` to the checked-out commit, rather than comparing only
the latest commit. `GIT_DEPTH=0` keeps the diff base available. A missing base is an
error. Renames include old and new paths; deleting a complete skill is recorded
without running unrelated skills. A remaining affected skill without its dataset
is an error, including dataset deletion. New skills are discovered from the
`skills/` directory without editing the CI file.

Default detached MR pipelines evaluate the source checkout. If merged-results
pipelines are enabled in GitLab, the diff also includes target changes present in
the merged checkout, conservatively selecting their affected skills.

## Project configuration

1. Configure `AWS_CREDS_TARGET_ROLE` so it is available to the intended MR jobs.
   The target is a role in your own account, for example `arn:aws:iam::<ACCOUNT_ID>:role/skill-marketplace`.
   Protected-variable settings and MR branch protection can affect availability.
   Required evaluations fail if the variable is missing; credentials are vended by
   the AWS GitLab shared runner before the script starts.
2. Enable **Settings → Merge requests → Merge checks → Pipelines must succeed**.
   This project setting is what prevents a failed pipeline from merging.
3. If all changes must pass through MRs, configure the default branch protection
   to require that workflow. The YAML itself cannot prevent direct pushes.

The role's trust must be scoped to the intended GitLab namespace/project and its
permissions must cover the configured Bedrock models. Fork MRs need an explicit
decision about running their code with the parent project's credentials; do not
broaden trust just to make an unknown fork's eval run.

An MR that modifies the shared harness or CI will run the full suite. Existing
assertion failures will continue to fail it; routing does not relax evaluation
thresholds or automatically accept a new baseline.

## Dashboard and evidence

The eval job's `after_script` reads its artifacts without model calls and writes:

* `.eval/dashboard.html`: case scores, failed gate rows with complete reasons,
  baseline deltas and provenance warnings, negative controls, evidence links.
* `.eval/summary.json`: machine-readable summary for a future notification job.
* `.eval/selection.json`: selected skills, shared changes, removed skills, diff SHAs,
  selection errors and exit code.
* `.eval/runs/`: original execution, scoring, metadata and generated artifacts.

The MR exposes these through **Skill evaluation**. Download and extract the
artifact archive, then open `.eval/dashboard.html` locally. Artifact HTML preview
depends on the GitLab instance configuration. Artifacts expire after two weeks.
The dashboard covers one job; missing or incomplete runs are not shown as passing.
Runner loss or a hard job timeout may prevent `after_script` and artifact upload.

For a historical dashboard, a later aggregation job can retain summaries by
pipeline/MR/commit in S3 and serve an authenticated site or use GitLab Pages where
available. This repository currently produces a dashboard per eval job, without
publishing a historical site.

## Email and agent analysis

For simple pipeline status emails, configure GitLab's
[Pipeline status emails integration](https://docs.gitlab.com/user/project/integrations/pipeline_status_emails/).
Under **Settings → Integrations → Pipeline status emails**, enable the integration,
set recipients and include the MR source branches in the branch filter. A project
Maintainer or Owner is required.
For emails containing case scores and failure evidence, add a notification job
after evaluation that runs even on failure, downloads `.eval/summary.json`, and
sends a summary plus the job/artifact URL via SES or the team's email service.
That requires an approved sender, recipients and narrowly scoped sending
permissions. No email is sent by the current pipeline.

The evaluation already runs an agent and judges through Bedrock. Additional
failure analysis can be another GitLab job that reads the report and execution
evidence, calls an agent, and publishes suggested fixes as an artifact. Keep that
advisory output separate from deterministic gate status. Posting MR comments or
creating a fix branch would additionally require authorized GitLab API access;
neither behavior is enabled here.
