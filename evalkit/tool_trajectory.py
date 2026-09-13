"""Tool-trajectory checks over the message-list format.

Exists because `strands_evals` carries two incompatible meanings for a list
trajectory. `SkillInvoked` and the skill judges read a Bedrock-style *message
list* and dig tool uses out of `content[].toolUse`. The built-in `ToolCalled`,
handed the same list, does `self.tool_name in trajectory` -- it expects a flat
list of tool-name strings, so against a message list it reports every tool as
uncalled. That is how `ToolCalled[Bash]` scored 0 on a run whose trace plainly
contains two Bash calls.

Only one of the two formats can be supplied, and it has to be the message list:
that is the only one the skill evaluators and the LLM judges can read, and they
are the reason this framework exists. So the tool check is reimplemented on top
of the message list rather than the trajectory being downgraded to fit it.

Expectations come from the dataset (`expect.tools_any_order` /
`expect.tools_in_order`) via case metadata, so this evaluator is constructed once
for the whole suite instead of being rebuilt per case with a hardcoded tool name.
"""

from __future__ import annotations

from typing import Any

from strands_evals.evaluators import Evaluator
from strands_evals.types import NOT_APPLICABLE, EvaluationData, EvaluationOutput


def tool_names(trajectory: Any) -> list[str]:
    """Tool names in call order, from a message-list trajectory."""
    names: list[str] = []
    for msg in trajectory or []:
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        for block in msg.get("content") or []:
            if isinstance(block, dict) and "toolUse" in block:
                names.append(block["toolUse"].get("name", ""))
    return names


def is_subsequence(expected: list[str], actual: list[str]) -> bool:
    """Whether `expected` appears in `actual` in order, gaps allowed.

    A subsequence rather than a prefix or an exact match: a skill legitimately
    reads a reference file or lists the workspace between the steps it
    prescribes, and requiring adjacency would fail a run that did everything
    asked plus one reasonable extra step.
    """
    it = iter(actual)
    return all(any(a == e for a in it) for e in expected)


class ToolTrajectoryEvaluator(Evaluator):
    """Scores the tools a run actually called against the case's expectations.

    Reads `metadata["tools_any_order"]` and `metadata["tools_in_order"]`. One row
    per missing tool, plus one row for order, so the report names which tool was
    skipped instead of collapsing to a single number.
    """

    #: Read by `run_strands_eval.collect`. Without it the framework aggregates the
    #: per-tool rows into one average before the report is written, and the claim
    #: above -- the whole reason this emits a row per tool -- is not true of what a
    #: reader sees.
    expand_rows = True

    def __init__(self, name: str | None = None):
        super().__init__(name=name or "ToolTrajectory")
        self.aggregator = self._aggregate_dropping_na

    def evaluate(self, evaluation_case: EvaluationData) -> list[EvaluationOutput]:
        meta = evaluation_case.metadata or {}
        want_any = list(meta.get("tools_any_order") or [])
        want_order = list(meta.get("tools_in_order") or [])
        if not want_any and not want_order:
            return [EvaluationOutput(score=0.0, test_pass=False, label=NOT_APPLICABLE,
                                     reason="the case declares no tool expectations")]

        actual = tool_names(evaluation_case.actual_trajectory)
        if not actual:
            return [EvaluationOutput(
                score=0.0, test_pass=False, label="no_tool_calls",
                reason="the trajectory contains no tool use at all, so the run either "
                       "answered from memory or the trace was not captured")]

        rows: list[EvaluationOutput] = []
        for tool in want_any:
            hit = tool in actual
            rows.append(EvaluationOutput(
                score=1.0 if hit else 0.0, test_pass=hit,
                label=f"called:{tool}",
                reason=f"'{tool}' {'was called' if hit else 'was never called'}; "
                       f"trajectory was {actual}"))

        if want_order:
            ok = is_subsequence(want_order, actual)
            rows.append(EvaluationOutput(
                score=1.0 if ok else 0.0, test_pass=ok,
                label="order",
                reason=f"expected {want_order} in order (extra steps allowed); "
                       f"got {actual}"))
        return rows
