"""Layer-2 evaluation for `aws-drawio-diagram`. Owned by this skill's author.

Everything in this directory is diagram-specific and deliberately NOT in the
shared kit: mxGraph coordinate arithmetic (`geometry.py`), the draw.io Electron
renderer (`render.py`), the visual and house-style rubrics (`rubrics.py`), and the
layout evaluator built on them. A team evaluating a markdown cost table has no use
for any of it and should not inherit an Electron dependency to get layer 1.

Three scoring layers live here, cheapest first:

  layout   `DiagramLayoutEvaluator` -- exact geometry. No model, no rendering, no
           network. Every node inside its declared group, no colliding labels, no
           oversized container. Deterministic, so it gates the build.
  visual   A vision judge over a rendered PNG, for what geometry cannot see:
           whether icons resolved, whether the picture reads. Handed the geometry
           findings as facts so it is not asked to re-derive arithmetic.
  style    A second pass comparing the render against an official AWS Reference
           Architecture. Declared non-gating (`gate=False`) -- see below.

Why `style` does not gate the build: a single vision call over a full-page render
is the least reproducible layer in the stack. Measured, the same diagram scored
0.82 and 0.97 across runs where the only change was one label. A check that flips
on that much noise gets switched off by the third team to adopt the kit, and it
takes the trustworthy checks down with it. It is printed and recorded so the gap
stays visible; it just does not fail the run on its own.
"""

from __future__ import annotations

import os
import re

# Siblings in this directory. `plugins._load_module` puts it on sys.path first,
# which is what lets author evaluator code be versioned beside the skill.
import geometry
import render
import rubrics
from layout_evaluator import DiagramLayoutEvaluator

from strands_evals.evaluators import MultimodalOutputEvaluator
from strands_evals.types import ImageData

from evalkit.plugins import ExtraExperiment, PrepareContext, Prepared

HERE = os.path.dirname(os.path.abspath(__file__))

#: The house style every diagram is held to: an official AWS Reference
#: Architecture, drawn by AWS. Global to this skill rather than per-case because
#: it defines a *style*, not a subject -- unlike `expect.reference_image`, which
#: is topical and must resemble the case at hand. The contract it encodes is
#: written out in `reference/aws-official-style.md`.
STYLE_REFERENCE = os.path.join(HERE, "reference", "aws-official-style.png")


def applies(expect: dict) -> bool:
    """Whether this case was supposed to yield a diagram at all.

    The distinction is load-bearing. A Mermaid or cost-table case producing no
    `.drawio` is correct and must not be handed a layout checker; a diagram case
    producing none is a real failure the layout checker should report. Without
    this both look identical -- a not-applicable row -- and the suite reports
    failures it does not have.
    """
    return (expect.get("file_glob") or "").endswith(".drawio")


_GENERATOR = re.compile(r"generate\.ts\s+(\S+)\s+(\S+)")
_SKILL_COPY = re.compile(r"^skill/([^/]+)/")
#: Repo root, for resolving a spec path recovered out of the trace.
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))


def _spec_from_trace(recorded: dict) -> str | None:
    """The spec the generator was actually invoked with, recovered from the trace.

    Needed because an agent may point the generator at a spec that ships *with*
    the skill instead of authoring one. Artifact collection correctly treats an
    unmodified registry file as skill source rather than agent output, so no spec
    is collected -- and the orphan check, the one that catches a node drawn
    outside its declared group, then has nothing to compare against and silently
    passes. That is how the most defective diagram in the suite scored a clean
    layout: five nodes declared `group: region` were rendered outside the cloud
    boundary and nothing reported it.

    The command records a workspace path (`skill/<name>/...`) that no longer
    exists; the registry copy does, so the leading `skill/` segment is remapped to
    `skills/`. Returns None rather than guessing when the spec cannot be located,
    so the caller reports an unavailable check instead of a passing one.
    """
    for call in recorded.get("tool_calls") or []:
        if call.get("name") != "Bash":
            continue
        cmd = (call.get("args") or {}).get("command") or ""
        m = _GENERATOR.search(cmd)
        if not m:
            continue
        # `cd <dir> && npx tsx src/generate.ts <spec> <out>` -- the spec path is
        # relative to the cd target, not to the workspace root.
        cd = re.search(r"cd\s+(\S+)\s*&&", cmd)
        rel = os.path.normpath(os.path.join(cd.group(1) if cd else "", m.group(1)))
        rel = _SKILL_COPY.sub(r"skills/\1/", rel)
        cand = os.path.join(_ROOT, rel)
        if os.path.isfile(cand):
            return cand
    return None


def _artifacts(recorded: dict) -> tuple[str | None, str | None]:
    """`(drawio_path, spec_path)` from the run's saved files.

    The spec is the generator's *input*, and the orphan check needs it to know
    which group each node was *meant* to be in -- the .drawio alone can say where
    a node is, not where it was supposed to go.
    """
    artifact = spec = None
    for rel, meta in (recorded.get("files") or {}).items():
        saved = meta.get("saved_to")
        if not saved or not os.path.exists(saved):
            continue
        if rel.endswith(".drawio"):
            artifact = saved
        elif rel.endswith(".json"):
            spec = saved
    return artifact, spec or _spec_from_trace(recorded)


def _reference_image(expect: dict) -> tuple[str | None, str | None]:
    """`(path, problem)` for a topical reference this case's diagram should resemble.

    Relative paths resolve against this `eval/` directory.

    A declared-but-missing file returns a problem instead of `None`, because
    failing open here is invisible: the case simply scores under the plain
    `DiagramVisualQuality` rubric instead of `[vs reference]`, still passes, and
    nothing says the comparison never ran. Measured -- `adversarial-inline-xml`
    declared `evals/reference/…` against a directory named `reference/`, and the
    reference rubric silently sat out every run until the row name was noticed.
    """
    rel = expect.get("reference_image")
    if not rel:
        return None, None
    path = rel if os.path.isabs(rel) else os.path.join(HERE, rel)
    if os.path.isfile(path):
        return path, None
    return None, (f"reference_image {rel!r} does not exist (resolved to {path}); "
                  f"the [vs reference] comparison did not run")


def prepare(ctx: PrepareContext) -> Prepared:
    """Render the diagram and run geometry, then assemble what the judges see."""
    artifact, spec_path = _artifacts(ctx.recorded)
    if not artifact:
        # Asked for a diagram, produced none. Report it as a failure of this
        # skill's layer rather than skipping the row silently: a missing
        # deliverable is the loudest defect there is.
        return Prepared(
            findings=["no .drawio artifact was produced by this run"],
            metadata={"artifact_path": None, "spec_path": None, "render_path": None},
        )

    findings: list[str] = []
    try:
        groups = geometry.spec_groups(spec_path) if spec_path else {}
        findings = [f"{f.kind}: {f.detail}" for f in geometry.analyze(artifact, groups)]
    except (OSError, ValueError) as e:
        findings = [f"geometry check failed: {e}"]

    png: str | None = None
    if ctx.want_visual:
        out_png = os.path.join(ctx.artifacts_dir, "render.png")
        png, why = render.render_if_possible(artifact, out_png)
        if png is None:
            # A missing renderer is a missing *tool*, not a failing diagram. Saying
            # so keeps a laptop without draw.io installed from looking like a
            # skill that cannot lay out a diagram.
            findings.append(f"render unavailable, visual layer skipped: {why}")

    metadata = {"artifact_path": artifact, "spec_path": spec_path, "render_path": png}
    if not png:
        return Prepared(findings=findings, metadata=metadata)

    reference, ref_problem = _reference_image(ctx.expect)
    if ref_problem:
        findings.append(ref_problem)
    # Order is the contract: the prompt composer emits every media block first in
    # list order and appends the text last. Nothing labels the images, so the
    # instruction has to say which is which -- hence `has_reference`.
    media: list = [ImageData(source=png, format="png")]
    if reference:
        media.insert(0, ImageData(source=reference, format="png"))
        metadata["reference_path"] = reference

    extra: list[ExtraExperiment] = []
    if os.path.isfile(STYLE_REFERENCE):
        extra.append(ExtraExperiment(
            suffix="::style",
            media=[ImageData(source=STYLE_REFERENCE, format="png"),
                   ImageData(source=png, format="png")],
            instruction=rubrics.style_instruction(ctx.prompt),
            evaluators=[MultimodalOutputEvaluator(
                rubric=rubrics.STYLE_CONFORMANCE_RUBRIC, model=ctx.judge_model,
                name="DiagramStyleConformance")],
            row_label="DiagramStyleConformance",
            gate=False,  # see the module docstring
        ))

    return Prepared(
        findings=findings,
        media=media,
        instruction=rubrics.visual_instruction(ctx.prompt, findings,
                                               has_reference=bool(reference)),
        metadata=metadata,
        extra=extra,
    )


def evaluators(prepared: Prepared, judge_model) -> list:
    """Layer-2 evaluators for one diagram case."""
    evs: list = [DiagramLayoutEvaluator()]
    if prepared.media:
        rubric = rubrics.DIAGRAM_VISUAL_RUBRIC
        has_reference = bool(prepared.metadata.get("reference_path"))
        if has_reference:
            rubric += rubrics.REFERENCE_COMPARISON
        evs.append(MultimodalOutputEvaluator(
            rubric=rubric, model=judge_model,
            # Named apart so a report cannot be read as comparable across cases
            # with and without a reference: the criteria differ, so the numbers
            # are not on the same scale.
            name="DiagramVisualQuality" + ("[vs reference]" if has_reference else ""),
        ))
    return evs
