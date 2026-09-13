"""A deterministic spec-layout evaluator, as a first-class `strands_evals.Evaluator`.

Written as an `Evaluator` subclass rather than as a side check so it lands in the
same `EvaluationReport` as the judges, aggregates the same way, and can be mixed
into one `Experiment` with them. The deterministic and the model layers
disagreeing is signal, and it is only visible when both are in one report.

Imports `strands_evals`, so this module runs only in the eval environment, never
inside the sandbox.
"""

from __future__ import annotations

from strands_evals.evaluators import Evaluator
from strands_evals.types import NOT_APPLICABLE, EvaluationData, EvaluationOutput

import specgeom

# Findings that make a diagram wrong rather than imperfect. A spec copied from the
# shipped asset answers a question nobody asked; a node outside the canvas is not
# drawn; two boxes in the same place hide each other; a dangling edge or an unknown
# pulse is silently dropped, so the diagram is missing something it claims to have.
# Long labels and dead canvas are ugly, and folding them in here would mean nothing
# ever passes — at which point the check stops being read.
BLOCKING = {
    "unrendered_template",
    "no_nodes",
    "node_has_no_size",
    "node_out_of_frame",
    "nodes_overlap",
    "edge_dangling",
    "edge_malformed",
    "pulse_unknown_node",
    "path_edge_out_of_range",
    "no_motion_declared",
    # An animation whose highlight jumps to a stage the edges do not reach, or walks
    # the flow backwards, states an ordering the diagram contradicts. Skipped stages
    # and a mid-flow start are abbreviations, not contradictions, so they stay out.
    "pulse_order_disconnected",
    "pulse_order_backwards",
    "pulse_order_doubles_back",
    # Steps mode states the ordering even more literally -- the dots appear one step
    # at a time -- so a step leaving a node no step has reached, a pulse on a box the
    # step does not arrive at, or a `pulses` list the renderer will ignore, are the
    # same contradiction. Pacing is blocking too: it is the one thing a user asked
    # for by name ("slower"), and it is cheap to get right from the lint's message.
    "step_order_disconnected",
    "step_pulse_off_route",
    "step_pulses_ignored",
    "step_edge_out_of_range",
    "step_edge_repeated",
    "pace_low_fps",
    "pace_pulse_too_fast",
    "pace_step_too_fast",
    # An icon key that resolves to nothing is a gap in the picture the spec
    # explicitly asked to fill. A missing icon *set* is an environment fact
    # about the scoring machine, not the run, so it stays out.
    "icon_unknown",
    "text_clips",
    "unsupported_glyph",
    "theme_mismatch",
}


class FlowSpecLayoutEvaluator(Evaluator):
    """Scores a flow diagram's geometry from its spec, without rendering it.

    One `EvaluationOutput` row per finding, so the report names each defect rather
    than reducing a diagram to a number nobody can act on. A clean spec yields one
    passing row; a run that produced no spec yields a failing row, because a
    missing deliverable is not a passing layout.

    Reads the spec path out of `metadata`:

        metadata = {"spec_path": ".../flow.spec.json", "expected_theme": "dark"}
    """

    def __init__(self, name: str | None = None):
        super().__init__(name=name or "FlowSpecLayout")
        # Cosmetic rows should not drag a sound diagram to a fraction that reads
        # as "half broken", so only blocking rows are averaged.
        self.aggregator = self._aggregate_dropping_na

    def evaluate(self, evaluation_case: EvaluationData) -> list[EvaluationOutput]:
        meta = evaluation_case.metadata or {}
        spec = meta.get("spec_path")
        if not spec:
            return [EvaluationOutput(
                score=0.0, test_pass=False, label="no_spec",
                reason="no JSON spec was collected from this run, so there is no layout to "
                       "check; the skill requires the spec as a deliverable",
            )]

        try:
            findings = specgeom.analyze(spec, meta.get("expected_theme"))
        except (OSError, ValueError) as e:
            # A spec the renderer would reject is the skill's defect, not the
            # harness's, so it is scored rather than swallowed.
            return [EvaluationOutput(
                score=0.0, test_pass=False, label="unparseable",
                reason=f"could not read the spec at {spec}: {e}",
            )]

        rows = [
            EvaluationOutput(
                score=0.0 if f.kind in BLOCKING else 1.0,
                test_pass=f.kind not in BLOCKING,
                label=f.kind,
                reason=f.detail,
            )
            for f in findings
        ]
        if not rows:
            rows.append(EvaluationOutput(
                score=1.0, test_pass=True, label="clean",
                reason="every node is inside the canvas, nothing overlaps, every edge and "
                       "pulse resolves, the copy fits its boxes, and the spec is not the "
                       "shipped example",
            ))
        if meta.get("render_unavailable"):
            rows.append(EvaluationOutput(
                score=0.0, test_pass=True, label=NOT_APPLICABLE,
                reason=f"the visual layer did not run: {meta['render_unavailable']}",
            ))
        return rows
