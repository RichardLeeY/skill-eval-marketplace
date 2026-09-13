# Changelog

One entry per plugin release. Entries are written by `tools/release.py` from the
commit subjects since the previous release; edit before pushing if they need tidying.

## Unreleased

- Add the locked `skill-eval` CLI for selected-skill setup, preflight, execution,
  archived re-scoring and acceptance of reviewed baselines. Both CI workflows use
  it and retain negative-control checks.
- Reject missing/duplicate case records, changed/missing artifacts, empty evaluator
  reports and failed recorded executions. Persist completed cases incrementally,
  retain agent/judge provenance, and refuse baseline acceptance until required
  controls complete. Remove the scoring-time `--update-baseline` switch.
- Separate quick starts from author and architecture documentation; document the
  existing coverage gaps and the `.eval/runs/` archive layout. Add offline framework
  regression tests under `tests/`.
- Default low-level execution and scoring outputs to `.eval/manual/`, keeping
  generated results and reports out of the repository root.
- visual-flow-webp renders a pausable MP4 with `--format mp4`, for a slide or any
  viewer that has to stop on one step. Smaller than the WebP as well (243 KB against
  622 KB on the reference render). Four new `--check` rows read the written file back
  (codec, pixel format, audio track, faststart), because a file that plays here and
  not on the slide is the failure worth catching. New eval case `slide-mp4` scores
  the choice, not the flag: the prompt names a PowerPoint deck and pausing, never a
  format. Needs ffmpeg, now checked by `doctor.py` and installed in CI.
- Fix `steps_confined`, which had been passing for the wrong reason. It sampled one
  point per edge and the dot is not there, so it was reading the trailing edge of the
  glow with a 6-level margin; it now samples a band, and skips points sitting on an
  already-travelled edge's trail. Same verdict in every container.
- Phase 1 records `toolchain` (the sandbox's `python3` and `node`) and plugins get it
  on `PrepareContext.recorded`. Phase 2 runs under `uv run`, so every interpreter it
  could find by name was an ephemeral venv that never ran the skill: the visual-flow
  re-render was losing every icon and the delivered-vs-rendered comparison then
  reported the difference as the agent's fault. The re-render now uses the run's own
  interpreter, and where it cannot, it says so instead of blaming the run.
- Drop accidentally committed .playwright-mcp scratch files and ignore the dir
- Fix the 11 Probe criticals in eval and kit tooling
- Ignore diagrams rendered at the repo root

## 0.7.0 — 2026-09-04

visual-flow-webp places official AWS icons from the installed icon set


## 0.6.0 — 2026-09-04

visual-flow-webp plays the flow step by step, at a pace you set


## 0.5.0 — 2026-09-04

visual-flow-gif is now visual-flow-webp

- visual-flow-webp: animated WebP instead of GIF, and cases built on one target diagram
- evalkit: show the judges what the agent said before each tool call
- evalkit: one dataset reader for both phases, and grade every assertion
- Remove the AgentCore Evaluation API path
- Correct the stale reason for the two-phase split
- visual-flow-gif: check the animation's ordering, and judge the delivered image
- Add visual-flow-gif skill, adapted from AI-Builder-Club/skills
- Remove the cost-estimate-table skill
- Add Claude Code plugin + marketplace manifests
- Add folder-specific-claude-and-agents-md skill, adapted from davidondrej/skills
- evalkit: configurable model provider for both eval phases
- Add cost-estimate-table skill, zh-CN docs, and evalkit improvements
- Skill marketplace: shared eval kit + aws-drawio-diagram
