"""A deterministic layout evaluator, as a first-class `strands_evals.Evaluator`.

Written as an `Evaluator` subclass rather than as a side check so it appears in
the same `EvaluationReport` as the judges, aggregates the same way, and can be
mixed into an `Experiment` alongside them. That is the whole point of the
framework's extension point: the deterministic and the LLM layers disagreeing is
signal, and it is only visible when both land in one report.

Imports `strands_evals`, so this module runs only in the eval environment (see
the header of `run_strands_eval.py`), never inside the sandbox.
"""

from __future__ import annotations

from strands_evals.evaluators import Evaluator
from strands_evals.types import NOT_APPLICABLE, EvaluationData, EvaluationOutput

import geometry

# Findings that make a diagram wrong, as opposed to imperfect. A node parented
# outside its declared group is drawn in the wrong place, which is a defect at
# any tolerance; a container with slack is ugly. Splitting them keeps the pass
# signal meaningful -- if cosmetic slack failed the case, nothing would ever
# pass and the check would stop being read.
BLOCKING = {"node_lost_its_group", "node_missing", "node_escapes_container", "labels_overlap"}


class DiagramLayoutEvaluator(Evaluator):
    """Scores a rendered-artifact's geometry without rendering it.

    One `EvaluationOutput` row per finding, so the report names each defect
    rather than reducing a diagram to a single number nobody can act on. A clean
    diagram yields one passing row; a diagram with no artifact to check yields a
    not-applicable row, because absent data is not a passing layout.

    Reads the artifact path and the spec path out of `metadata`, since the
    trajectory carries the agent's words and the file lives on disk:

        metadata = {"artifact_path": "...drawio", "spec_path": "...spec.json"}
    """

    def __init__(self, name: str | None = None):
        super().__init__(name=name or "DiagramLayout")
        # Cosmetic findings should not drag the score down to a fraction that
        # reads as "half broken", so only blocking rows are averaged.
        self.aggregator = self._aggregate_dropping_na

    def evaluate(self, evaluation_case: EvaluationData) -> list[EvaluationOutput]:
        meta = evaluation_case.metadata or {}
        artifact = meta.get("artifact_path")
        if not artifact:
            return [EvaluationOutput(
                score=0.0, test_pass=False, label=NOT_APPLICABLE,
                reason="no diagram artifact was produced, so there is no layout to check",
            )]

        spec = meta.get("spec_path")
        try:
            expected = geometry.spec_groups(spec) if spec else {}
            findings = geometry.analyze(artifact, expected)
        except (OSError, ValueError) as e:
            # A malformed document is the skill's defect, not the harness's, so
            # it is scored rather than swallowed.
            return [EvaluationOutput(
                score=0.0, test_pass=False, label="unparseable",
                reason=f"could not read geometry from {artifact}: {e}",
            )]

        rows: list[EvaluationOutput] = []
        if not spec:
            # Without the spec, `check_orphans` cannot run: the .drawio records
            # where each node *is*, never where it was meant to be. Reported
            # rather than skipped, because a silently disabled check reads as a
            # clean diagram -- which is exactly how five nodes drawn outside their
            # declared group once scored a perfect layout.
            rows.append(EvaluationOutput(
                score=0.0, test_pass=True, label=NOT_APPLICABLE,
                reason="no generator spec was located, so 'declared group vs actual parent' "
                       "could not be checked; the remaining geometry checks did run",
            ))

        for f in findings:
            blocking = f.kind in BLOCKING
            rows.append(EvaluationOutput(
                score=0.0 if blocking else 1.0,
                test_pass=not blocking,
                label=f.kind,
                reason=f.detail,
            ))

        if not findings:
            rows.append(EvaluationOutput(
                score=1.0, test_pass=True, label="clean",
                reason="every node sits inside its declared group, no labels collide, "
                       "no container is oversized"
                       + ("" if spec else " (group membership unchecked -- see above)"),
            ))
        return rows
