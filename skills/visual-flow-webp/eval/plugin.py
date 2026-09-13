"""Layer-2 evaluation for `visual-flow-webp`. Owned by this skill's author.

Everything here is flow-diagram-specific and deliberately not in the shared kit:
spec arithmetic (`specgeom.py`), the Pillow renderer call (`render.py`), the
visual and motion rubrics (`rubrics.py`), and the evaluator built on them.

Three layers, cheapest first:

  layout   `FlowSpecLayoutEvaluator` — exact spec arithmetic. No model, no
           rendering, no network. Deterministic, so it gates the build.
  visual   A vision judge over the re-rendered still, for what arithmetic cannot
           see: whether the picture reads, and whether it is a diagram of the
           system that was asked for. Handed the layout findings as facts. A case
           may declare `expect.reference_image`, and then the judge is shown a
           hand-authored render of the same request first and scores against it —
           the row is named `FlowVisualQuality[vs reference]`, because the criteria
           differ and the two numbers do not belong on one scale.
  motion   A second pass over two frames of the animated WebP, asking whether
           the animation adds
           signal or covers the labels. Declared non-gating — see below.

Why `motion` does not gate: it is the least reproducible thing in the stack. The
overlay is a small travelling glow, its position depends on which frame the
sampler happened to pick, and a judge's read of "clearly distinguishable" moves
between runs on an unchanged file. A gate that flips on that noise gets switched
off, and it takes the trustworthy checks down with it. It is printed and recorded
so the gap stays visible; it does not fail the run on its own.

Why the pictures are re-rendered rather than read from the run: see the header of
`render.py`. Briefly — the harness collects text, so the spec survives collection
and the PNG and WebP do not.
"""

from __future__ import annotations

import json
import os

# Siblings in this directory. `plugins._load_module` puts it on sys.path first,
# which is what lets author evaluator code be versioned beside the skill.
import render
import rubrics
import specgeom
from spec_layout_evaluator import FlowSpecLayoutEvaluator

from strands_evals.evaluators import MultimodalOutputEvaluator
from strands_evals.types import ImageData

from evalkit.plugins import ExtraExperiment, PrepareContext, Prepared

HERE = os.path.dirname(os.path.abspath(__file__))


def applies(expect: dict) -> bool:
    """Whether this case was supposed to yield a flow animation at all.

    Keyed on the declared skill rather than on `file_glob`, because this skill's
    deliverable glob is `*.webp` and a future skill could produce one for entirely
    different reasons; being handed a flow-spec checker would give it a row it can
    only fail.
    """
    return expect.get("skill") == "visual-flow-webp"


def _spec_path(recorded: dict) -> tuple[str | None, list[str]]:
    """The spec the run wrote, from the files saved to disk, plus any problems.

    Several JSON files can survive a run — a scratch copy, a second theme. The
    one that parses as a flow spec (an object with a `nodes` list) wins, and the
    largest of those, since a run that wrote a draft and then a final spec wrote
    the final one last but not necessarily smallest.
    """
    problems: list[str] = []
    candidates: list[tuple[int, str]] = []
    for rel, meta in (recorded.get("files") or {}).items():
        saved = meta.get("saved_to")
        if not saved or not rel.endswith(".json") or not os.path.exists(saved):
            continue
        if meta.get("truncated"):
            # A spec big enough to be truncated is not parseable, and guessing at
            # the missing tail would fabricate the thing under evaluation.
            problems.append(f"{rel} was truncated by artifact collection and cannot be parsed")
            continue
        try:
            data = json.loads(open(saved, encoding="utf-8").read())
        except (OSError, ValueError) as e:
            problems.append(f"{rel} is not valid JSON: {e}")
            continue
        if isinstance(data, dict) and isinstance(data.get("nodes"), list):
            candidates.append((meta.get("bytes") or 0, saved))
    if not candidates:
        return None, problems
    candidates.sort()
    return candidates[-1][1], problems


def _delivered(recorded: dict) -> list[str]:
    """What the run actually produced, as facts for the judge."""
    out = []
    for rel, meta in (recorded.get("files") or {}).items():
        if rel.lower().endswith((".webp", ".gif", ".png", ".mp4")):
            out.append(f"the run produced {rel} ({(meta.get('bytes') or 0) / 1024:.0f} KB)")
    return out or ["the run produced no PNG or animation file"]


def _reference_image(expect: dict) -> tuple[str | None, str | None]:
    """`(path, problem)` for a reference this case's diagram is calibrated against.

    `expect.reference_image` is a path relative to this `eval/` directory. It is
    this repo's plugin-level convention, not a field the evals SDK knows about:
    what the SDK provides is `MultimodalInput(media=[ImageData(...)])`, and the
    reference is simply the first image in that list.

    A declared-but-missing file returns a problem rather than `None`, because
    failing open here is invisible: the case would score under the plain
    `FlowVisualQuality` rubric, still pass, and nothing would say the comparison
    never ran. That exact silence cost the drawio skill several runs, where a
    reference declared as `evals/reference/…` against a directory named
    `reference/` sat out every run until someone noticed the row name.
    """
    rel = expect.get("reference_image")
    if not rel:
        return None, None
    path = rel if os.path.isabs(rel) else os.path.join(HERE, rel)
    if os.path.isfile(path):
        return path, None
    return None, (f"reference_image {rel!r} does not exist (resolved to {path}); "
                  f"the [vs reference] comparison did not run")


def _delivered_images(recorded: dict) -> tuple[str | None, str | None, str | None]:
    """`(png, animation, animation_ext)` the run delivered, if they are readable.

    `.gif` and `.mp4` are accepted alongside `.webp`, since the renderer offers all
    three. **The case's own `file_glob` is what decides which one is right**, and
    that is layer 1's job -- an MP4 for a case that asked for a pausable file is
    correct, and the same MP4 for a case that asked for a WebP already fails
    `file_produced`. Judging it twice would report one defect as two.

    So the preference order here is only a tie-breaker for a run that delivered
    several: MP4 first, because a run that produced one did so deliberately (it is
    never the default and needs ffmpeg), then WebP, then GIF.

    Preferred over a re-render because they are what the user received. Largest of
    each kind wins, matching `_spec_path`: a run that wrote a draft and then a final
    picture wrote the final one last but not necessarily smallest.
    """
    best: dict[str, tuple[int, str]] = {}
    for rel, meta in (recorded.get("files") or {}).items():
        ext = os.path.splitext(rel)[1].lower()
        if ext not in (".png", ".webp", ".gif", ".mp4"):
            continue
        saved = meta.get("saved_to")
        if not render.usable_image(saved):
            continue
        size = meta.get("bytes") or 0
        if size >= best.get(ext, (-1, ""))[0]:
            best[ext] = (size, saved)
    for ext in (".mp4", ".webp", ".gif"):
        if ext in best:
            # A companion background is also a PNG, sometimes larger than the
            # rendered still. Prefer the still paired with the chosen animation.
            paired = os.path.splitext(best[ext][1])[0] + ".png"
            saved_pngs = {meta.get("saved_to") for rel, meta in (recorded.get("files") or {}).items()
                          if rel.lower().endswith(".png")}
            if paired in saved_pngs and render.usable_image(paired):
                return (paired, best[ext][1], ext)
            return (best.get(".png", (0, None))[1], best[ext][1], ext)
    return (best.get(".png", (0, None))[1], None, None)


def prepare(ctx: PrepareContext) -> Prepared:
    """Check the spec, re-render it, and assemble what the judges see."""
    spec_path, problems = _spec_path(ctx.recorded)
    findings: list[str] = list(problems) + _delivered(ctx.recorded)
    metadata: dict = {
        "spec_path": spec_path,
        "expected_theme": ctx.expect.get("expected_theme"),
        "png_path": None,
        "animation_path": None,
    }

    if not spec_path:
        # Asked for a diagram, collected no spec. Reported as a failure of this
        # layer rather than skipped: a missing deliverable is the loudest defect
        # there is, and the skill requires the spec explicitly.
        findings.insert(0, "no JSON spec was collected, so the layout could not be checked")
        return Prepared(findings=findings, metadata=metadata)

    order_findings: list[str] = []
    try:
        analysed = specgeom.analyze(spec_path, metadata["expected_theme"])
        findings = [f"{f.kind}: {f.detail}" for f in analysed] + findings
        # Split out for the motion judge specifically: a pulse-sequence defect is the
        # one thing it is being asked about that two still frames cannot reveal.
        order_findings = [f"{f.kind}: {f.detail}" for f in analysed
                          if f.kind.startswith(("pulse_", "step_", "pace_"))]
    except (OSError, ValueError) as e:
        findings.insert(0, f"spec check failed: {e}")

    if not ctx.want_visual:
        metadata["render_unavailable"] = "visual layer disabled for this run (--no-visual)"
        return Prepared(findings=findings, metadata=metadata)

    outdir = os.path.join(ctx.artifacts_dir, "render")
    # The delivered pictures first: they are what the user received, and judging a
    # re-render instead means scoring a reconstruction.
    d_png, d_anim, d_ext = _delivered_images(ctx.recorded)
    pair, why = render.render_if_possible(spec_path, outdir, basename="render",
                                          ext=d_ext or render.ANIM_EXT,
                                          interpreter_hint=render.sandbox_python(ctx.recorded))

    if pair is None and not (d_png and d_anim):
        # No render and no readable delivery. A missing renderer is a missing tool,
        # not a failing diagram, so say which it was.
        findings.append(f"no usable picture: render unavailable ({why}) and the run "
                        f"delivered no readable PNG + animation pair")
        metadata["render_unavailable"] = why
        return Prepared(findings=findings, metadata=metadata)

    # Whether a missing icon set would change the picture at all. A spec with no
    # `icon` node renders identically either way, so the comparison stays honest and
    # must not be skipped.
    try:
        spec_wants_icons = any(n.get("icon") for n in specgeom.load(spec_path).get("nodes", []))
    except (OSError, ValueError):
        spec_wants_icons = False
    if pair is not None and d_png and d_anim:
        # Both exist, so the assumption the fallback silently makes -- that the
        # delivered picture is what the spec renders to -- becomes checkable. An
        # inequality is not automatically a defect (a renderer need not be
        # byte-reproducible), which is why it is a finding rather than a gate.
        #
        # Unless the re-render was not the same picture to begin with. On an
        # interpreter without the icon set every `icon` node renders empty, so the
        # bytes differ for a reason that has nothing to do with the run -- and the
        # finding, which reads as "the agent delivered a picture that is not from its
        # own spec", would be describing this machine. Say which it is instead. Both
        # sides of this were observed on the same recording: identical text, one
        # honest comparison and one about the environment.
        degraded = not render.last_render_had_icon_set and spec_wants_icons
        if degraded:
            findings.append(
                "skipped the delivered-vs-rendered comparison: the re-render ran on an "
                "interpreter without the icon set, so it is not the same picture and a "
                "byte difference would say nothing about the run "
                "(pip install -r skills/visual-flow-webp/requirements.txt into the "
                "interpreter phase 1 used)")
        else:
            for kind, delivered, rendered in (("PNG", d_png, pair[0]), ("animation", d_anim, pair[1])):
                if not render.same_bytes(delivered, rendered):
                    findings.append(
                        f"the delivered {kind} is not what this spec renders to; the judge "
                        f"is looking at what was delivered")
    elif pair is None:
        findings.append(f"scored the delivered pictures without a spec render for "
                        f"comparison: {why}")

    png = d_png or pair[0]
    anim = d_anim or pair[1]
    metadata["png_path"], metadata["animation_path"] = png, anim
    metadata["image_source"] = "delivered" if (d_png and d_anim) else "re-rendered"

    extra: list[ExtraExperiment] = []
    # `outdir` is the renderer's doing, so it may not exist when the pictures came
    # from the run instead; the frames still have to land somewhere.
    os.makedirs(outdir, exist_ok=True)
    frames = render.frame_pair(anim, outdir, spec_path)
    if frames:
        metadata["motion_frames"] = frames[2]
        extra.append(ExtraExperiment(
            suffix="::motion",
            media=[ImageData(source=frames[0], format="png"),
                   ImageData(source=frames[1], format="png")],
            instruction=rubrics.motion_instruction(ctx.prompt, order_findings, frames[2]),
            evaluators=[MultimodalOutputEvaluator(
                rubric=rubrics.MOTION_RUBRIC, model=ctx.judge_model,
                name="FlowMotionQuality")],
            row_label="FlowMotionQuality",
            gate=False,  # see the module docstring
        ))
    else:
        findings.append("could not extract animation frames, so the motion layer did not run")

    reference, ref_problem = _reference_image(ctx.expect)
    if ref_problem:
        findings.append(ref_problem)
    # Order is the contract: the prompt composer emits every media block first, in
    # list order, and appends the text last. Nothing captions them, so the
    # reference goes first and `has_reference` says so in the instruction.
    media: list = [ImageData(source=png, format="png")]
    if reference:
        media.insert(0, ImageData(source=reference, format="png"))
        metadata["reference_path"] = reference

    return Prepared(
        findings=findings,
        media=media,
        instruction=rubrics.visual_instruction(ctx.prompt, findings,
                                               has_reference=bool(reference)),
        metadata=metadata,
        extra=extra,
    )


def evaluators(prepared: Prepared, judge_model) -> list:
    """Layer-2 evaluators for one flow-diagram case."""
    evs: list = [FlowSpecLayoutEvaluator()]
    if prepared.media:
        has_reference = bool(prepared.metadata.get("reference_path"))
        rubric = rubrics.FLOW_VISUAL_RUBRIC
        if has_reference:
            rubric += rubrics.REFERENCE_COMPARISON
        evs.append(MultimodalOutputEvaluator(
            rubric=rubric, model=judge_model,
            # Named apart so the report cannot be read as comparable across cases
            # with and without a reference: the criteria differ, so the two numbers
            # are not on one scale.
            name="FlowVisualQuality" + ("[vs reference]" if has_reference else "")))
    return evs
