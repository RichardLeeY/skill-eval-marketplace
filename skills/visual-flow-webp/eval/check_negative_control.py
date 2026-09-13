#!/usr/bin/env python3
"""Score the negative control and assert it stayed under its ceiling.

Run after every edit to `rubrics.py`:

    uv run --with strands-agents-evals python \
      skills/visual-flow-webp/eval/check_negative_control.py

Why this exists. The same person owns the skill and the rubric that scores it, so
a score can be raised by loosening the ruler instead of improving the skill — and
it does not feel like cheating at the time. Measured elsewhere in this repo: one
rubric went from 0.32 to 0.92 and only about half of that was the skill getting
better. The rest was the rubric growing an anchored scoring table.

The control is how the two are told apart. It is a real render of a deliberately
bad spec, with a declared ceiling in `negative-control/expected.json`. It must
stay at or below that ceiling across rubric edits. If it climbs, the ruler moved.

Exits 0 when the control is at or below its ceiling, 1 when it is above, and 2
when it could not be scored at all — a control that silently fails to run is
indistinguishable from a passing one, which is the failure this file is here to
prevent.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import render  # noqa: E402
import rubrics  # noqa: E402

from strands_evals import Case, Experiment  # noqa: E402
from strands_evals.evaluators import MultimodalOutputEvaluator  # noqa: E402
from strands_evals.types import ImageData, MultimodalInput  # noqa: E402

from evalkit import models as model_config  # noqa: E402

CONTROL = os.path.join(HERE, "negative-control")


def main() -> int:
    expected = json.load(open(os.path.join(CONTROL, "expected.json"), encoding="utf-8"))
    ceiling = float(expected["max_score"])
    prompt = expected["prompt"]

    outdir = tempfile.mkdtemp(prefix="vfw-control-")
    pair, why = render.render_if_possible(os.path.join(CONTROL, "spec.json"), outdir,
                                          basename="control")
    if pair is None:
        print(f"could not render the control, so it was not scored: {why}", file=sys.stderr)
        return 2
    png, _anim = pair

    # The findings are handed over exactly as a real case's would be: the judge is
    # meant to see the same evidence here as in production, or the control is
    # measuring a different rubric from the one that scores the skill.
    import specgeom
    findings = [f"{f.kind}: {f.detail}"
                for f in specgeom.analyze(os.path.join(CONTROL, "spec.json"), "light")]

    case = Case(
        name="negative-control",
        input=MultimodalInput(media=[ImageData(source=png, format="png")],
                              instruction=rubrics.visual_instruction(prompt, findings)),
        metadata={},
    )
    evaluator = MultimodalOutputEvaluator(
        rubric=rubrics.FLOW_VISUAL_RUBRIC, model=model_config.build_model("judge"),
        name="FlowVisualQuality")
    exp = Experiment(cases=[case], evaluators=[evaluator])
    report = asyncio.run(exp.run_evaluations_async(
        task=lambda _case: {"output": f"Wrote {os.path.basename(png)} and the animated WebP.",
                            "trajectory": []},
        max_workers=1))

    score = report.scores[0] if report.scores else None
    reason = (report.reasons[0] if report.reasons else "") or ""
    if score is None:
        print("the judge returned no score", file=sys.stderr)
        return 2
    print(f"negative control: {score:.2f} (ceiling {ceiling:.2f})")
    print(f"  render:  {png}")
    print(f"  reason:  {reason[:600]}")
    if score > ceiling:
        print(f"\nFAIL: the control scored {score:.2f}, above its {ceiling:.2f} ceiling.\n"
              f"The artifact did not change, so the rubric did. Re-read the scoring table "
              f"in rubrics.py before believing any score that moved with it.", file=sys.stderr)
        return 1
    print("PASS: the ruler still measures.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
