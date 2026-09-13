# The reference render, and what it is for

Two pictures live here, and they play different roles.

`git-webhook-target.png` is **the target** -- frame 0 of an animated AWS-style
architecture diagram the maintainer supplied (`demo.webp`, 950x399, 319 frames):
Git users push to a third-party repository, a webhook fires AWS Lambda inside an
"AWS Cloud" boundary, Lambda fans out to an S3 SSH-key bucket, a KMS key and an S3
output bucket, and a return edge goes back to the repository. Its animation is one
glow that walks the flow in time order -- push, webhook, then all three branches at
once, then the return edge. `git-webhook.spec.json` reproduces exactly that
schedule with `animation.steps` (four steps, the three stores sharing one), which
is why it seeds the `extend` case: a pacing edit must leave that order intact.
Every case in `../dataset.jsonl` is a way of asking for that picture. It is here for people, not for the judge: it was drawn by a different
tool, with icons this renderer cannot produce, so showing it beside a run's output
would score house style, which `rubrics.REFERENCE_COMPARISON` tells the judge to
ignore.

`git-webhook.png` is **the reference** -- the target re-authored in this skill's own
renderer and the `reference_image` of `flow-webp-git-webhook-dark`. It was produced
the same way the runs under test are: a spec was written by hand
(`git-webhook.spec.json`), linted with the skill's own `lint_spec.py`, rendered with
the skill's own `render_animated_webp.py`, and looked at. Regenerate it with

```bash
python3 skills/visual-flow-webp/scripts/lint_spec.py \
    skills/visual-flow-webp/eval/reference/git-webhook.spec.json
python3 skills/visual-flow-webp/scripts/render_animated_webp.py \
    --spec skills/visual-flow-webp/eval/reference/git-webhook.spec.json \
    --outdir skills/visual-flow-webp/eval/reference --basename git-webhook --check
```

and delete the `.webp` it writes beside the PNG: the reference is a still, and a
megabyte-plus animation in git buys nothing the PNG does not already show.

The same spec is also the seed file of `flow-webp-git-webhook-extend`, which hands
it to the agent as "the spec from last time" and asks for one more node. Editing
this spec therefore changes two cases at once; re-render after any edit and re-read
that case's assertions, which name the node ids.

## What it calibrates

**Only what the spec author decides.** Both images in the comparison come out of
one renderer, so palette, typeface and the hand-drawn styling are identical by
construction -- there is no style signal in this comparison, and
`rubrics.REFERENCE_COMPARISON` tells the judge so explicitly. What is left is
composition, coverage and density, and the target fixes what "good" means for each:

- the AWS Cloud boundary is a `layer: "background"` container holding Lambda and
  the three stores and nothing else, so the account edge is legible without
  reading a label;
- one fan-out from Lambda to three stores stacked at one x, rather than three
  chains, so "these happen together" is visible;
- the spine runs left to right on one axis, and the return edge drops below it and
  comes back to the repository, so the loop reads as a loop;
- the two hand-offs are edge labels in words -- `Git push`, `Git webhook` -- because
  the target labels them and the display font has no arrow glyph.

Those are what the dark case's assertions check, and they are all authored, not
rendered.

## What it is not

**Not a target to match.** `REFERENCE_COMPARISON` states it, and it is worth
repeating: a run that composes the system differently and draws it well scores
high. Node coordinates, canvas size, colour assignment and edge routing are all
legitimately different between two good answers.

**Not an authority.** If a judge finds the reference itself violating the rubric,
the rubric asks it to say so. That is a finding about this file, not about the run.

**Not a style reference.** `aws-drawio-diagram` ships one of those
(`reference/aws-official-style.png`, an AWS-drawn architecture diagram) because
that skill emits `.drawio` for a renderer it does not control, so house style is
something a run can get wrong. Here the renderer *is* the house style, and a style
reference would compare the renderer to itself. `git-webhook-target.png` is exactly
such a picture, which is why it is documentation and not a `reference_image`.

## The other three cases

They declare no `reference_image`, and the plugin handles that: no reference, plain
`FlowVisualQuality` rubric, row not renamed. Two of them ask for the light theme
(or do not ask, which is the same thing), and pairing this dark reference with a
light render invites exactly the palette scoring the rubric tells the judge to
ignore. `flow-webp-git-webhook-extend` is dark, but it starts from this very spec,
so comparing its render to this picture would score the seed, not the edit. The
icons request now rides along in `flow-webp-git-webhook-dark`; what it grades is
the reply's honesty about icons, which no picture can show.
