"""Judging `expect.assertions` -- every assertion of every case, one row each.

`expect.assertions` is where a case author states, in prose, what the deliverable
has to be true of: "the 500-record cap and why raising it fails are both
recorded", "the pinned cluster version and its reason appear as a constraint".
They are the checks that matter most and the ones no regex can express.

They were also, until this module existed, decoration. The dataset carried four
or five per case; `run_strands_eval` put `assertions[0]` on `Case.expected_assertion`
and dropped the rest; and no evaluator in the registered set reads that field --
`GoalSuccessRate` and `Correctness` do, and neither is loaded. So a case could
declare five load-bearing claims, satisfy none of them, and pass. Worse than an
unchecked check: an unchecked check that *looks* checked, sitting in the dataset
next to ones that are.

Design, and why:

**One row per assertion, not one score for all of them.** A single averaged
number says "0.6" and leaves you to guess which claim failed. The whole value of
prose assertions is that they name a specific thing, and a report that collapses
them throws that away. `expand_rows` (read by `run_strands_eval.collect`) is what
gets the per-assertion rows into the report instead of the framework's aggregate.

**One judge call for all of them, not one per assertion.** The evidence is the
same trajectory and the same deliverable every time; sending it N times pays N
times for one reading. The verdicts come back as a list and are matched to the
assertions **by index**, never by position, because a judge that returns three
verdicts for five assertions must fail the two it skipped rather than shift the
remaining verdicts onto the wrong claims.

**Binary, with no "cannot tell".** An assertion either holds against the recorded
evidence or it does not. Offering the judge an unjudgeable verdict offers it an
exit from every hard call, and the row it produces is dropped from the mean --
i.e. the gate quietly disappears on exactly the assertions worth gating. The
consequence is a real constraint on authors: **write assertions about text the
kit can see** -- the trajectory, and the text deliverables appended to it. An
assertion about pixels or binary structure belongs in `eval/plugin.py`, which
gets the file itself.
"""

from __future__ import annotations

import json
from typing import Any, Literal, cast

from pydantic import BaseModel, Field
from strands import Agent
from strands_evals.evaluators import Evaluator
from strands_evals.types import NOT_APPLICABLE, EvaluationData, EvaluationOutput

try:
    from strands_evals.evaluators.prompt_templates.trajectory_prompt_template import (
        serialize_trajectory,
    )
except ImportError:  # pragma: no cover - SDK layout changed
    # A judge prompt with no trajectory in it scores every assertion "no evidence",
    # which reads as a broken skill rather than a broken import. So carry a
    # fallback: less careful about where it truncates, honest about what it is.
    def serialize_trajectory(trajectory: Any, max_chars: int = 600_000) -> str:  # type: ignore[misc]
        if trajectory is None:
            return "(no trajectory)"
        text = json.dumps(trajectory, indent=2, default=str)
        return text if len(text) <= max_chars else text[:max_chars] + "\n… [truncated]"


SYSTEM_PROMPT = """\
You verify factual claims about what an AI agent produced.

You are given the user's request, the agent's full trajectory (its tool calls,
including the text deliverables it wrote, read back from disk after the run), the
agent's final reply, and a numbered list of assertions written by the person who
designed this test case.

For each assertion, decide whether it holds. Rules:

- **Quote your evidence.** Before the verdict, name the part of the deliverable or
  trajectory that decides it. A verdict with no quotable evidence is a guess.
- **Judge the assertion as written, including its "rather than" clauses.** "X is
  recorded as a constraint rather than as generic advice" is not satisfied by X
  merely appearing.
- **A compound assertion holds only if every part holds.** If it lists four facts
  and three are present, it fails, and your evidence says which one is missing.
- **Absence claims need a search, not an impression.** For "the file contains no
  directory tree", say what you looked for.
- **Do not reward effort, length, or polish.** They are not what the assertion
  says.
- **Do not soften a failure because the rest of the work is good.** Another
  evaluator scores overall quality; you score these specific claims.
- Return exactly one verdict per assertion, carrying that assertion's `index`.
"""


class AssertionVerdict(BaseModel):
    """One judged assertion."""

    index: int = Field(description="The 1-based number of the assertion being judged")
    assertion: str = Field(
        description="The assertion you are judging, copied verbatim from the list")
    verdict: Literal["holds", "fails"]
    evidence: str = Field(
        description="The quoted text or trajectory step that decides the verdict, "
                    "stated before the conclusion it supports")


class AssertionReport(BaseModel):
    """The judge's verdicts for one case."""

    verdicts: list[AssertionVerdict] = Field(
        description="Exactly one entry per assertion, in the order they were given")


def _aligned(text: str, echoed: str) -> bool:
    """Whether the judge's echoed assertion is recognisably the one asked about.

    Compared loosely -- a normalised prefix -- because a judge legitimately
    normalises whitespace or trims a trailing clause, and failing a run over
    that would be a harness defect masquerading as a skill defect. What this
    catches is the case worth catching: verdicts that slid onto the wrong claims.
    """
    norm = " ".join(text.split()).lower()
    ech = " ".join(echoed.split()).lower()
    if not ech:
        return False
    return norm.startswith(ech[:40]) or ech.startswith(norm[:40])


class AssertionsEvaluator(Evaluator):
    """Scores `expect.assertions` from case metadata, one row per assertion.

    Reads `metadata["assertions"]`. Emits `NOT_APPLICABLE` when the case declares
    none -- a case with no assertions is not a case that failed them, and averaging
    a zero in would punish authors for the checks they did write.
    """

    #: Read by `run_strands_eval.collect`: put each assertion in the report as its
    #: own row instead of collapsing them into this evaluator's average.
    expand_rows = True

    def __init__(self, model: Any = None, name: str | None = None,
                 system_prompt: str | None = None):
        super().__init__(name=name or "Assertions")
        self.model = model
        self.system_prompt = system_prompt or SYSTEM_PROMPT
        # NA rows are placeholders, not verdicts; averaging them in would report a
        # case with one satisfied assertion and one that could not be judged as
        # half right.
        self.aggregator = self._aggregate_dropping_na

    def _prompt(self, assertions: list[str], case: EvaluationData) -> str:
        numbered = "\n".join(f"{i}. {a}" for i, a in enumerate(assertions, 1))
        request = (case.metadata or {}).get("request_prompt") or ""
        return (
            f"## The user's request\n{request}\n\n"
            f"## Agent trajectory\n{serialize_trajectory(case.actual_trajectory)}\n\n"
            f"## Agent's final response\n{case.actual_output}\n\n"
            f"## Assertions to verify\n{numbered}\n"
        )

    def _rows(self, assertions: list[str],
              report: AssertionReport) -> list[EvaluationOutput]:
        by_index = {v.index: v for v in report.verdicts}
        rows: list[EvaluationOutput] = []
        for n, text in enumerate(assertions, 1):
            verdict = by_index.get(n)
            if verdict is None:
                # Silence is not a pass. A judge that returned fewer verdicts than
                # there were assertions left these unchecked, and an unchecked
                # assertion reported as satisfied is the failure this module exists
                # to remove.
                rows.append(EvaluationOutput(
                    score=0.0, test_pass=False, label=f"#{n}",
                    reason=f"#{n} NO VERDICT — the judge returned no verdict for "
                           f"this assertion, so it was never checked: {text}"))
                continue
            holds = verdict.verdict == "holds"
            drift = "" if _aligned(text, verdict.assertion) else (
                f" [warning: the judge restated this assertion as "
                f"{verdict.assertion!r}, which may not be the same claim]")
            rows.append(EvaluationOutput(
                score=1.0 if holds else 0.0,
                test_pass=holds,
                label=f"#{n}",
                reason=f"#{n} {'HOLDS' if holds else 'FAILS'}: {text}\n"
                       f"   evidence: {verdict.evidence}{drift}"))
        extra = sorted(set(by_index) - set(range(1, len(assertions) + 1)))
        if extra:
            rows.append(EvaluationOutput(
                score=0.0, test_pass=False, label="alignment",
                reason=f"the judge returned verdicts for assertion indexes {extra}, "
                       f"which this case does not declare ({len(assertions)} "
                       f"assertions); the verdicts above may be misaligned"))
        return rows

    def evaluate(self, evaluation_case: EvaluationData) -> list[EvaluationOutput]:
        """Score every assertion in one judge call.

        Only the sync path is implemented: the base class runs it through
        `asyncio.to_thread`, so the call still overlaps the other evaluators for
        this case.
        """
        assertions = [a for a in ((evaluation_case.metadata or {}).get("assertions") or [])
                      if isinstance(a, str) and a.strip()]
        if not assertions:
            return [EvaluationOutput(score=0.0, test_pass=True, label=NOT_APPLICABLE,
                                     reason="the case declares no assertions")]
        if evaluation_case.actual_trajectory is None:
            return [EvaluationOutput(
                score=0.0, test_pass=False, label="no_trajectory",
                reason=f"{len(assertions)} assertion(s) could not be checked: the "
                       f"recording carries no trajectory")]

        agent = Agent(model=self.model, system_prompt=self.system_prompt,
                      callback_handler=None)
        result = agent(self._prompt(assertions, evaluation_case),
                       structured_output_model=AssertionReport)
        return self._rows(assertions, cast(AssertionReport, result.structured_output))
