# Findings that shaped the evaluation design

These are historical observations retained from the earlier README. They explain
design decisions, not current benchmark guarantees. Use the README and current run
reports for setup, case counts and supported behavior.

## Preserve the delivered artifact

Early artifact collection only retained text. A 293 KB PNG became 471 KB of
replacement characters, while a smaller binary could disappear entirely. The visual
plugin therefore reconstructed pictures from the JSON spec.

Collection now preserves the bytes it cannot inline. The visual-flow plugin prefers
delivered pictures and re-renders for comparison or fallback. A JSON spec remains
useful evidence, but it is no longer the only output that survives collection.
MP4 output also requires ffmpeg and ffprobe; treating those tools as optional while
running the MP4 case hid an environment failure behind a skill failure.

## Test what an animation says

A flow can render successfully while its pulse sequence changes the meaning. In the
fan-out case, the sequence `build, unit-test, static-scan, image-build, gate` narrated
parallel branches as sequential work. Deterministic pulse-order checking and the
motion judge identified the issue from different evidence.

The skill's `scripts/lint_spec.py` now gives the agent an actionable check before
rendering. Earlier cases also exposed overlapping boxes and a missing arrow glyph in
a bundled font. These became layout and font-coverage checks.

## Distinguish judge drift from skill improvement

Prior runs showed score movement on unchanged artifacts, and score gains when a
rubric's anchors changed. Negative controls help detect a looser scoring standard;
they do not eliminate model variance. A contradictory instruction in `SKILL.md`
also penalized otherwise correct runs, since that document is itself judged as a
contract.

The current kit records judge/provider and source fingerprints, preserves reviewed
reports, and accepts a baseline without re-scoring. Coverage remains incomplete:
only visual-flow currently supplies an executable negative-control script.

The former two-interpreter dependency conflict is no longer a constraint. Both
phases run in one locked environment while remaining separate processes, allowing
judge iteration without repeating agent execution.
