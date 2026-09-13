"""Rubrics for the vision layer of `visual-flow-webp`.

Two questions, two rubrics, because a single number mixing them is actionable for
neither:

`FLOW_VISUAL_RUBRIC`   Does the still read as a diagram of the system asked for?
`MOTION_RUBRIC`        Does the animation add signal, or does it cover the labels?

Both are scoped to what pixels show and `specgeom.py` cannot. Overlap, frame
containment, wiring and text fit are arithmetic; asking a vision model to
re-derive them buys a less reliable answer to a settled question. The findings are
handed over as facts so the judge confirms or contradicts them — a contradiction
means one of the two layers is wrong, which is the interesting outcome.
"""

FLOW_VISUAL_RUBRIC = """\
You are shown a rendered flow diagram. **The image is the deliverable under
evaluation** — not an input that the response describes. Judge the image.

The `<Output>` field holds whatever the agent said when it finished, often just
the paths of the files it wrote. A terse summary is the normal case and is not a
defect; do not score the diagram absent because its text summary is short.

Score these groups, and in your reason state what you see in each before you name
a verdict for it:

1. **Legibility.** Is every label and body line fully inside its shape, at
   readable size, in adequate contrast against its fill? A word running past a
   border, text sitting on a border, or body copy so small it is a grey smudge
   are all defects here.
2. **The system is the one asked for.** The request is quoted below. Are the
   stages, stores, actors and decision points from the request present, and named
   in the words the request or the source material used? A well-drawn diagram of
   a different system scores 0 on this group, however handsome.
3. **Flow reads.** Do the arrows follow the described order, with visible
   direction, and does a reader see where to start and where the loop closes?
   Arrows that cross unrelated content, or that stop short of the shape they
   belong to, count against this group.
4. **Roles separated.** Does colour and grouping carry meaning — sections that
   hold their contents, related steps together, risk or friction visually
   distinct from routine work — rather than being applied at random?
5. **Finish.** Does it look like something a person would publish: aligned edges,
   consistent spacing, no shape colliding with another, no large region of dead
   canvas making the layout look accidental?

You are given the findings of an exact geometric check of the spec that produced
this image. Treat them as established facts about coordinates. Say what the
render shows *beyond* them, and flag any finding the picture contradicts.

## Scoring

Judge each group **present** (holds up), **partial** (attempted, with a deviation
you can name) or **absent**. Group 1 and group 2 count double: an unreadable
diagram and a diagram of the wrong system are the two failures nothing else
compensates for.

**Write the evidence first and the verdict last** for each group. A verdict placed
as a heading is a guess made before looking, and the score then follows the guess
rather than what you found.

| what you found | score |
|---|---|
| every group present | 0.90-1.00 |
| all present, one or two nits a reader would not notice | 0.80-0.90 |
| one group partial | 0.65-0.80 |
| two or more groups partial | 0.45-0.65 |
| any group absent | at most 0.40 |
| group 1 or group 2 absent | at most 0.25 |

**Your score must follow from your own findings.** If your reason names no
material defect, the score is above 0.9, not 0.7. Equally, do not award 0.9 while
describing labels that run outside their boxes — a defect you can name is a cap,
not a deduction to be balanced against strengths elsewhere.

Judging notes:

- **Reference-image overlays preserve the user's layout.** When the request says
  to animate on the original, unchanged source typography, icons and spacing are
  intentional. Judge new obscuration or unwanted redraws, not inherited styling.
  An unchanged static PNG alone proves nothing about the animation; the motion
  rubric checks the moving overlays separately.
- **Do not score the choice of palette, font, or hand-drawn styling.** They are
  the skill's house style, not decisions the agent made.
- **A diagram is allowed to be simpler than the source material.** Score group 2
  on whether what the request *emphasised* is there, not on completeness of every
  aside.
- **Do not claim small text is missing.** The render is downsampled before you
  see it. If you cannot read a region, say the resolution prevents you from
  judging it and score the group on what you can read — unreadable is not absent.
"""


REFERENCE_COMPARISON = """\

## The reference render

You are shown two images. **Image 1 is a reference** — a hand-authored spec of the
same request, rendered by this same renderer and checked by a person. **Image 2 is
the diagram under evaluation.** Every judgment above applies to image 2; image 1
only calibrates what "good" means for this request.

Both images come out of one renderer, so the palette, the font and the hand-drawn
styling are **identical by construction** and carry no information. Do not score
them, and do not read a difference in them as a defect — there is none to find.
What differs between the two images is what the *spec author* decided, and that is
the whole comparison:

- **Composition.** Does image 2 make the same structural distinctions legible —
  what runs in parallel versus in sequence, which stages are grouped, where the
  flow starts and ends?
- **Coverage.** Does image 2 carry the stages, decisions and stores the request
  asked for, at comparable depth? Noticeably thinner or padded with stages nobody
  asked for both count.
- **Density.** At the same canvas size, is image 2 more cramped or more sprawling
  than the reference — labels tighter, whitespace dead, boxes colliding?

**Do not reward resemblance.** Node positions, canvas size, colour assignment,
ordering and edge routing may all differ legitimately: the layout is authored, and
two good answers to one request do not match. A diagram composed differently from
the reference and drawn well scores high. A diagram that mimics the reference's
arrangement while its labels overflow or its parallel branches read as sequential
scores low. If the reference itself violates the rubric, say so — it is a
calibration aid, not an authority.
"""


MOTION_RUBRIC = """\
You are shown two frames of the same animated WebP: **image 1 is frame 0, image 2
is a frame roughly a third of the way through.** Both are frames of the
deliverable under evaluation.

The animation is an overlay on a static diagram, so the two frames are supposed to
be *mostly* identical. Score only the overlay:

1. **Motion is visible.** Can you point to something that differs between the two
   frames — a travelling dot on a connector, a highlight on a different node? Two
   frames you cannot tell apart mean the animation does not play for a viewer, even
   when a pixel diff says it does.
2. **Motion is legible.** Is the moving element clearly distinguishable from the
   static diagram — enough contrast against the background to be seen at a glance?
3. **Motion does not damage the diagram.** Does the glow or highlight cover a
   label, blur text, or make a shape harder to read in image 2 than in image 1?
   Emphasis that costs legibility is worse than no emphasis.
4. **Motion follows the flow.** Does the highlighted node or the travelling dot
   sit on a path the diagram actually draws, rather than floating over empty
   canvas?
5. **Motion carries the ordering.** A data flow means its sequence, and the
   animation is the only thing here that can express it. Between the two frames,
   has the emphasis advanced *along* the arrows — from a stage to one the diagram
   connects it to — rather than moving to an unrelated part of the canvas or
   against the arrow direction? A viewer who watched the loop should be able to say
   what happens first. Where the instruction states an ordering defect as an
   established fact, do not contradict it from two frames; say whether it is
   visible in them.

   When the instruction says the frames are **step midpoints**, the animation
   claims to reveal the flow one step at a time. Then image 1 should show dots
   only on the first step's edges and nothing else moving; image 2 should show
   dots on a later step's edges — all of them at once if the step has several —
   with the edges already travelled marked by a faint trail and the edges still
   to come untouched. Dots on every edge in both frames mean the reveal is not
   happening; a dot on an edge the instruction lists as "later" means the
   schedule is not what the spec says.

## Scoring

| what you found | score |
|---|---|
| all five hold | 0.90-1.00 |
| motion visible and legible, a nit on 3, 4 or 5 | 0.75-0.90 |
| motion visible but hard to see, or it obscures a label | 0.50-0.75 |
| the emphasis moves but contradicts the flow's direction or connections | at most 0.40 |
| the two frames are indistinguishable to you | at most 0.20 |

Do not score the diagram's layout, wording, or subject; another evaluator does
that. Two frames differing only in the position of a small glow is exactly the
expected result, not a sign that something is missing.
"""


def visual_instruction(prompt: str, findings: list[str],
                       has_reference: bool = False) -> str:
    """The instruction shown beside the still render.

    Carries the user's request — "is this a good diagram" is unanswerable without
    knowing what was asked — and the geometric findings, so the judge confirms a
    known list instead of guessing at one.

    `has_reference` restates the image order. The prompt composer emits media
    blocks uncaptioned and appends the text *after* them, so "image 1 is the
    reference" is the only thing distinguishing two pictures drawn in the same
    house style — and it has to be said here.
    """
    facts = "\n".join(f"- {f}" for f in findings) or "- none (the spec check found no defects)"
    header = (
        "You are shown two images: image 1 is the reference, image 2 is the "
        "diagram under evaluation. Score image 2.\n\n"
        if has_reference else ""
    )
    return (
        f"{header}"
        f"The user asked for:\n{prompt}\n\n"
        f"An exact check of the spec that produced this image reported:\n{facts}\n\n"
        f"Evaluate the rendered image against the rubric."
    )


def motion_instruction(prompt: str, order_findings: list[str] | None = None,
                       frames_note: str | None = None) -> str:
    """The instruction shown beside the frame pair.

    States the image order, which nothing else does: the prompt composer emits
    media blocks uncaptioned and appends the text after them.

    `order_findings` are the pulse-sequence defects `specgeom.check_pulse_order`
    already settled. Handed over rather than left for the judge because two frames
    cannot show a sequence: a highlight that jumps from a retry branch to the happy
    path looks, in a still pair, exactly like one that advanced along an edge.
    """
    established = "\n".join(f"  - {f}" for f in (order_findings or [])) or "  - (none)"
    which = frames_note or ("image 1 is frame 0, image 2 is a frame about a third of the "
                            "way through the animation")
    return (
        f"You are shown two frames of one animated WebP: {which}.\n\n"
        f"The animation was produced in response to:\n{prompt}\n\n"
        "Already established by exact checks over the spec that produced this animation — "
        "treat as fact, and say whether each is visible in these two frames rather "
        "than re-deriving it:\n"
        f"{established}\n\n"
        "Score the animation overlay only, against the rubric."
    )
