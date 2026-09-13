"""Rubrics for the vision layer.

`MultimodalOutputEvaluator` puts whatever media the case carries into the
judge's content blocks and scores `actual_output` against the rubric. Its
built-in siblings ship image-to-text rubrics -- an image is the *input* and the
judge grades the text answer. Here the image is the agent's *deliverable*, so
the rubric has to say so explicitly; without that the judge grades the prose and
scores a broken diagram highly because the text describing it was accurate.

Deliberately scoped to what a picture shows and arithmetic does not. Containment
and overlap are `geometry.py`'s job, and asking a vision model to re-derive them
buys a less reliable answer to a question already answered exactly. The findings
are passed in as context so the judge confirms or contradicts them rather than
starting from scratch -- a contradiction is the interesting outcome, since it
means one of the two layers is wrong and it is worth knowing which.
"""

DIAGRAM_VISUAL_RUBRIC = """\
You are shown a rendered architecture diagram. **The image is the deliverable
under evaluation** -- not an input the response describes. Judge the image.

Score only what rendering reveals and coordinates cannot:

1. **Icons rendered.** Does every shape display a real service icon, or are
   there empty boxes, generic placeholders, or missing glyphs? A shape whose
   icon failed to resolve is a defect even when it is correctly labelled.
2. **Legibility.** Is every label readable at this resolution -- not clipped by
   its shape, not truncated with an ellipsis, not overrun by a neighbour, not
   low-contrast against its fill?
3. **Edge routing.** Do connectors reach the shapes they belong to without
   passing through unrelated nodes, and are edge labels attached to a single
   readable edge rather than piled on top of each other?
4. **Reads as one diagram.** Does the grouping communicate the architecture --
   containers that visually hold their contents, a sensible flow direction, no
   large regions of dead space that make the layout look accidental?

You are given the findings of an exact geometric check. Treat them as
established facts about coordinates. Your job is to say what the render shows
*beyond* them, and to flag any finding the picture contradicts -- a
contradiction means one of the two checks is wrong, which is worth reporting.

Do not deduct for content decisions (which services, how many, what the labels
say). Another evaluator scores those.
"""


# Appended when the case ships a reference render. Kept separate from the rubric
# above so the no-reference path is byte-identical to what it was -- adding a
# reference to one case must not silently reword the criteria for the others.
#
# The framing is deliberate and is most of the value here. A reference image is
# useful as a *standard of quality*, not as a target to match: `generate.ts`
# auto-layout places nodes from the spec, so two correct runs of the same case
# produce different coordinates, different canvas sizes, and different edge
# routes. Score pixel similarity and the metric punishes legitimate variation
# while ignoring the defects that matter -- and it degrades every time the
# generator's layout improves.
REFERENCE_COMPARISON = """\

## The reference render

You are shown two images. **Image 1 is a reference** -- a hand-checked diagram of
the standard this skill is expected to reach. **Image 2 is the diagram under
evaluation.** Every judgment above applies to image 2; image 1 only calibrates
what "good" means here.

Compare them on craft, not on coordinates:

- Conventions the reference demonstrates -- icon style, how nesting is shown,
  label placement, edge style, use of colour. Does image 2 follow the same
  visual language?
- Density and legibility at comparable scale. Is image 2 noticeably more
  cramped, more sprawling, or harder to read than the reference?
- Finish. Does image 2 look like something a person would ship, next to one that
  was?

**Do not reward resemblance.** Node positions, canvas size, ordering, edge
routing and service selection may all differ legitimately -- the layout is
generated from a spec, so two correct runs of the same request do not match. A
diagram that differs from the reference and is well drawn scores high. A diagram
that mimics the reference's arrangement but has unresolved icons or unreadable
labels scores low. If the reference itself violates the rubric, say so; it is a
calibration aid, not an authority.
"""


# Scored by its own evaluator against `reference/aws-official-style.png`, an AWS
# Reference Architecture. Deliberately not folded into DIAGRAM_VISUAL_RUBRIC: that
# one asks "is this diagram well drawn", this one asks "does it meet the house
# style", and a single number mixing the two is actionable for neither.
#
# The checklist is spelled out instead of left to the judge to infer from the
# reference. Inferred style is unstable between runs and unfalsifiable in review;
# an enumerated contract gives the same answer twice and can be argued with. The
# prose version, with the reasoning behind each clause, is
# `reference/aws-official-style.md`.
#
# The scoring table is there because enumerating criteria is only half a rubric.
# Without it, two cases whose reasons read "all six elements present and
# well-executed, no material omissions found" both scored 0.72, while a third with
# a genuine defect scored 0.82-0.92 -- the prose and the number disagreed, and only
# the number is used downstream. An anchored scale is not grade inflation: it also
# caps a diagram whose annotation panel is missing at 0.35, which the unanchored
# version had no way to express.
STYLE_CONFORMANCE_RUBRIC = """\
You are shown two images. **Image 1 is an official AWS Reference Architecture**
and defines the house style. **Image 2 is the diagram under evaluation.** Score
image 2's conformance to the style of image 1.

**The image is the deliverable. The `<Output>` field is not.** `<Output>` holds
whatever the agent said when it finished -- often just the path of the file it
wrote -- and carries no information about style. Judge the pixels of image 2. If
`<Output>` looks like a bare filename, that is the normal case and not a defect;
do not score the diagram absent because its text summary is terse.

Score these groups, and in your reason state plainly which elements are present
and which are absent:

1. **Document frame.** A title naming the workload; a subtitle naming which view
   this is; a one-sentence statement of design intent; a footer. Image 1 is a
   document, not a bare canvas.
2. **Numbered annotations.** Numbered badges placed on the canvas beside what
   they annotate, and a side panel repeating each number with prose. The prose
   must carry design rationale -- why a component is there, what alternatives
   exist, what the drawing deliberately omits -- and must not restate what a
   label already says. This is the element that makes image 1 a reference
   architecture rather than a topology drawing; weight it accordingly.
3. **Container language.** Nesting shown by border style plus a badge: solid dark
   for the AWS Cloud with an `aws` badge, solid green for a VPC, dashed orange
   for an Availability Zone, a distinct border for a service boundary such as
   EKS. Not fill colour alone.
4. **Icon language.** Filled squares with a white glyph in official AWS category
   colours, colour carrying category. Non-AWS actors drawn as flat line icons
   without a coloured square, placed outside the cloud boundary.
5. **Labels.** Full official service names with vendor prefix -- `Amazon
   CloudFront`, `Amazon Simple Storage Service (S3)` -- not bare `CloudFront` or
   `S3`. Application components named by function rather than by product.

   A component that is not an AWS service is *supposed* to be named by what it
   does, next to the service it runs on: `Vector log shipper on Amazon EC2`,
   `User Experience Platform`, `API layer`. That is the convention, not a
   deviation from it -- do not mark labels partial for naming third-party or
   in-house software functionally. Mark them partial when an **AWS service**
   appears without its `Amazon`/`AWS` prefix, `NAT Gateway` and `NAT Gateway A`
   being the ones that slip through most often.
6. **Edge discipline.** Thin orthogonal connectors with small arrowheads. Image 1
   leaves most edges unlabelled and lets the numbered comments carry the
   semantics; labelling every edge instead is an acceptable alternative, but
   crowded or colliding edge labels are not.

## Scoring

Score each group above as **present** (the convention is followed), **partial**
(attempted, with a real deviation you can name) or **absent**. Group 2 counts
double; the rest count once.

**State each group's verdict after its evidence, never before.** Write what you
see in that group, then the single word `present`, `partial` or `absent` as the
last thing you say about it. A verdict written as a heading is a guess made before
looking, and it will not match what you then find. This is the observed failure
mode, not a hypothetical: a run scored 0.72 with all six groups headed `PARTIAL`
while every one of the six bodies ended *"Scoring as present"*. The number
followed the headings.

**Then count the verdicts you actually wrote, not the headings.** A group whose
evidence ends in "present" is present, even if you began the paragraph expecting
otherwise. A nameable nit inside an otherwise-followed convention is a **nit, not
a partial** — partial requires a deviation a reader of the diagram would notice.

Then:

| what you found | score |
|---|---|
| all groups present | 0.95-1.00 |
| all present, one or two nameable nits that a reader would not notice | 0.85-0.95 |
| one group partial | 0.75-0.85 |
| two or more groups partial | 0.60-0.75 |
| any group absent | at most 0.55 |
| group 2 (annotations) absent | at most 0.35 |

**Your score must follow from your own findings.** If your reason says every
group is present and names no material omission, the score is above 0.9 — not
0.7. A summary reading "faithful, no material omissions found" attached to a 0.72
is a contradiction, and the number is the part that gets used. Equally, do not
award 0.9 while describing a missing annotation panel: an absent element is a
hard cap, not a deduction to be balanced off against strengths elsewhere.

Judging notes:

- **Do not score topology, service choice, or subject matter.** Image 1 is a
  wealth-management platform; image 2 will be something else entirely. That
  difference is expected and costs nothing.
- **A container the architecture does not have is not a missing container.** A
  serverless design of S3, Lambda and DynamoDB has no VPC, and drawing one would
  be wrong. Score group 3 on whether the containers that *are* present use the
  right border-plus-badge language, never on how many levels of nesting there
  are. A three-icon diagram is not required to look as busy as image 1.
- **Do not reward resemblance of arrangement.** Node positions, canvas
  proportions and flow direction may differ freely.
- **Do not claim small text is missing.** These renders are ~3000 px wide and are
  downsampled before you see them; 13 px footer text survives that badly. One run
  deducted for a footer lacking the "AWS Reference Architecture" label on a
  diagram that carries it in bold orange, confirmed by cropping the render. If you
  cannot read a region, say the resolution prevents you from judging it and score
  the group on what you can read — an unreadable element is not an absent one.
- Image 1 nests AWS Cloud directly inside a VPC with **no Region container**.
  Image 2 including a Region is not a defect.
- An absent element scores as absent -- say so. Do not credit a diagram for
  something a reader cannot see, and do not soften the score because the omission
  looks like a tooling limitation rather than a choice. Naming the gap precisely
  is the output being asked for here.
"""


def style_instruction(prompt: str) -> str:
    """The instruction shown beside the style-reference pair.

    Carries the request so the judge can tell a deliberate stylistic choice from
    an omission, and states the image order, which nothing else does: the prompt
    composer emits media blocks uncaptioned and appends the text after them.
    """
    return (
        "You are shown two images: image 1 is the official AWS Reference "
        "Architecture that defines the house style, image 2 is the diagram under "
        "evaluation. Score image 2's conformance to image 1's style.\n\n"
        f"The diagram in image 2 was produced in response to:\n{prompt}\n\n"
        "Judge style only. The two diagrams describe unrelated systems and are "
        "not supposed to depict the same thing."
    )


def visual_instruction(prompt: str, findings: list[str], has_reference: bool = False) -> str:
    """The instruction shown beside the image.

    Carries the user's original request, because "is this a good diagram" is not
    answerable without knowing what was asked for, and the geometry findings, so
    the judge is confirming a known list rather than guessing at one.

    `has_reference` restates the image order. The prompt composer emits media
    blocks with no captions, so "the first image is the reference" is the only
    thing distinguishing them, and it has to be said in the text -- which the
    composer appends *after* all the images.
    """
    facts = "\n".join(f"- {f}" for f in findings) or "- none (geometry check found no defects)"
    header = (
        "You are shown two images: image 1 is the reference, image 2 is the "
        "diagram under evaluation. Score image 2.\n\n"
        if has_reference else ""
    )
    return (
        f"{header}"
        f"The user asked for:\n{prompt}\n\n"
        f"An exact geometric check of the diagram under evaluation reported:\n{facts}\n\n"
        f"Evaluate the rendered image against the rubric."
    )
