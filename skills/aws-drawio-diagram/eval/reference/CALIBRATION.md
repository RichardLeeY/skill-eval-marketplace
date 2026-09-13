# Reference renders

A case may declare `expect.reference_image` (path relative to `skill-sandbox/`).
The vision judge is then shown **two** images — the reference first, the diagram
under evaluation second — and the rubric gains `rubrics.REFERENCE_COMPARISON`.

```json
"expect": { "reference_image": "evals/reference/s3-lambda-dynamodb.png" }
```

## Why not `expected_output`

Because it is a text field. `compose_multimodal_test_prompt` does

```python
text_parts.append(f"<ExpectedOutput>{evaluation_case.expected_output}</ExpectedOutput>\n")
```

so an `ImageData` placed there reaches the judge as a pydantic repr of a *file
path*. The judge then grades a picture it was never shown, fluently and without
complaint. Only `input.media` becomes an image content block.

Media order is the contract: the composer emits every media block first, in list
order, then appends the text. Nothing captions the images, so
`visual_instruction(..., has_reference=True)` states which is which.

## A reference is a standard of quality, not a target to match

`generate.ts` lays out nodes from the spec, so two correct runs of the same
request differ in coordinates, canvas size and edge routing. Scoring similarity
would punish legitimate variation and would get *worse* every time the
generator's layout improves.

The rubric says this explicitly, and it mostly holds — but not entirely.
Measured on `three-tier-web` with a deliberately mismatched reference (a 3-node
S3/Lambda/DynamoDB render standing in for a multi-AZ web app):

| Reference | `DiagramVisualQuality` |
|---|---|
| none | 0.82 |
| mismatched | 0.72 |

The 0.10 went to *"the canvas is quite wide which slightly reduces
density/compactness compared to the reference"* — a resemblance penalty the
rubric had forbidden in as many words. So:

- **A reference must be topically comparable to the case.** A wrong one does not
  merely add nothing, it distorts the score.
- **Scores are not comparable across cases with and without a reference.** The
  criteria differ, which is why the evaluator reports as
  `DiagramVisualQuality[vs reference]`.

## Curating one

Do not generate a reference with the skill under test and stop there — its
defects become the standard. `s3-lambda-dynamodb.png` was produced by running
the skill's own generator and then hand-correcting what it got wrong:

1. `generate.ts` mapped S3 to `resIcon=mxgraph.aws4.simple_storage_service`, which
   is not a shape in draw.io's AWS4 stencil set, so it rendered as a blank green
   square. Patched to `mxgraph.aws4.s3` in the `.drawio`. Five more catalog
   entries had the same bug; all six are fixed in `catalog.ts` now — see the note
   below.
2. Edge labels are placed at the edge midpoint, which for a single row of 78 px
   icons lands on top of the next icon. `"ObjectCreated event"` was shortened to
   `"event"`.
3. Node labels wider than ~128 px collide with their neighbours' (the known
   single-row spacing limitation). `"S3 Source Bucket"` → `"S3 Bucket"` clears
   it; `geometry.analyze` goes from 2 findings to 0.

Then **look at the PNG**. `validate.ts` reported `✓ PASS` and
`geometry.analyze` reported no findings for a diagram whose S3 icon was a blank
square — neither layer can see an unresolved stencil.

`s3-lambda-dynamodb.spec.json` and `.drawio` are kept beside the PNG so the
reference can be regenerated and re-checked rather than trusted.

## Calibrating the style rubric, and the control that keeps it honest

`STYLE_CONFORMANCE_RUBRIC` enumerated six style groups but never said how
present/absent maps to a number. The result was a judge whose prose and score
disagreed: two cases whose reasons read *"all six style elements are present and
well-executed… no material omissions found"* both returned **0.72**, while a third
with a genuine defect returned 0.82–0.92. Only the number is used downstream, so
the metric was ranking by noise. The rubric now carries an anchored table (all
present → 0.95–1.00; one group partial → 0.75–0.85; any group absent → ≤0.55;
annotations absent → ≤0.35) and an instruction that the score must follow from the
findings named in the reason.

Adding a scale that raises scores is exactly the change that should be distrusted,
so it comes with a control. `negative-control.spec.json` is what the skill emitted
before it could draw a document frame: correct icons, correct containers, correct
edges, and no subtitle, no design intent, no annotations, bare `S3` / `Lambda` /
`DynamoDB` labels. `validate.ts` fails it on three errors. It renders 2234×852 —
a real diagram, not a blank page — and the calibrated judge scores it **0.25**,
citing the hard cap from the absent annotation group.

```bash
uv run --with strands-agents-evals python tools/style_probe.py \
    evals/reference/negative-control.spec.json --allow-invalid --out artifacts/_control
# expect: validate FAIL on 3 errors, then DiagramStyleConformance: ~0.25
```

`--allow-invalid` is not optional here, and the reason is worth keeping. The recipe
in this file used to stop at `validate.ts`, which reports the three errors and scores
nothing — so the 0.25 above was quoted from a run nobody could reproduce with the
instructions given. `style_probe.py` exits on a failed validation by design, and the
control fails validation *by construction*: the two are mutually exclusive without a
flag. A control you cannot run is not a control, and the gap went unnoticed for as
long as nobody re-ran it. Use `--out artifacts/_control`, not the default
`artifacts/_probe`, so a control run does not overwrite whatever probe you were
looking at.

0.25 for a frameless topology drawing against 0.92–0.95 for current output is the
evidence that the scale discriminates. Re-run the control whenever the rubric is
touched; a scale that cannot fail anything is not a scale. Most recently confirmed
after the verdict-placement and small-text rules were added — both raise scores, and
the control held at 0.25.

One failed attempt is worth recording: the first control was made by deleting the
`doc-*`, `note-*` and `badge-*` cells from a passing `.drawio` with a regex. Every
remaining id and parent looked correct, and it rendered as a 32×32 white square,
which the judge scored 0.0 — a number that looks like a working floor and proves
nothing. Build a control from a spec through the real generator, and check the
render's pixel dimensions before believing a low score.

## Broken stencil names in `catalog.ts` — fixed

**Historical, as of 2026-08-26.** All six entries below are corrected in
`catalog.ts`, and `validate.ts` now fails any diagram carrying one, naming the
replacement. Kept here because the table is the denylist's source, and because
the way the bug survived three layers of checking is the reusable part.

Six of 32 entries pointed at names that do not exist, and rendered as blank
coloured squares. `validate.ts` passed them (it counted `aws4Refs`, it did not
check they resolve) and the `artifact_regex` grader matched them (the pattern only
requires `resIcon=mxgraph.aws4.`):

| `catalog.ts` | renders | correct |
|---|---|---|
| `elastic_container_service` | blank | `ecs` |
| `elastic_kubernetes_service` | blank | `eks` |
| `simple_storage_service` | blank | `s3` |
| `simple_queue_service` | blank | `sqs` |
| `simple_notification_service` | blank | `sns` |
| `step_function` | blank | `step_functions` |

`artifacts/coinbase-low-latency/` uses two of them, so two icons in that render
are blank squares — and the vision judge scored it 0.62 while reporting *"No
obviously empty boxes or generic placeholders are visible"* and describing
"purple for EKS". That render is 5919 px wide; downsampled to the judge's input
budget, a 78 px icon is a few pixels and a blank square is indistinguishable
from a glyphed one.

Which is the real lesson about reference images: for this defect class neither a
reference nor a vision judge is the right instrument. Comparing every
`resIcon=mxgraph.aws4.X` against the set of names that resolve is exact and free,
and that is now what `stencils.ts` + `scripts/aws4-stencils.txt` do — an
allowlist of the library's 1038 shapes.

It was a six-name denylist first, and it did miss a seventh:
`managed_streaming_for_apache_kafka` (the real name drops `apache`) was introduced
into the coinbase example *by the change that fixed the other six*, rendered blank
for two evaluation runs, and scored 0.92. A denylist can only ever catch the bugs
already found, which for this failure class is the wrong shape of check.
