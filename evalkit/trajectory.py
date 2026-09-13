"""Convert a sandbox response into the message list `strands-evals` reads.

`strands_evals` evaluators take `actual_trajectory` as either a `Session` object or
a raw Bedrock-style message list. The message list is the one a harness can build
without importing the agent framework, which is what lets the two phases stay
separate processes -- and staying separate is what makes a recording re-scorable
without paying for the agent again.

Every rule below is a fact about `strands_evals.extractors.skills`, read off its
`_patterns.py` and confirmed against a real sandbox trace:

1. A skill invocation is found from an assistant `toolUse` block whose `name` is
   a reserved skill-tool name. `Skill` with the skill name under the `skill`
   argument is the registered Claude Code / Claude Agent SDK convention, which
   is what the sandbox already emits. (`skills`/`skill_name`,
   `load_skill`/`skill_name`, `activate_skill`/`name` are the other harnesses.)
2. The `SKILL.md` body is recovered from the paired `toolResult`, matched by
   `toolUseId`. Pass the sandbox's full `skill_content`, not `result_head`: the
   truncated line is what the trace log shows a human, and handing it over
   instead makes `SkillInstructionFollowing` judge adherence against a
   one-line summary of the instructions.
3. The catalog comes from `<available_skills><skill><name>...</name>
   <description>...</description></skill></available_skills>`, found either in a
   `{"role": "system", "system_prompt": ...}` entry of the list or in a bare
   prompt string. `sandbox.py` emits exactly this block.
4. A refused load must stay in the trajectory. `extract_selected_skills`
   reports it as `status="failed"`, and that is a *correct selection the harness
   refused* -- dropping it would score the agent as having abstained.
"""

from __future__ import annotations

from typing import Any

# Reserved tool name / argument key for a skill load. Renaming either makes
# every skill-level evaluator silently see an agent that loaded nothing.
SKILL_TOOL_NAME = "Skill"
SKILL_TOOL_ARG = "skill"


def _args_dict(args: Any) -> dict[str, Any]:
    """Tool arguments as a dict.

    `run_eval.py` serialises whatever the tool was called with, and a non-dict
    reaches here as a repr string. Wrapping it keeps the call in the trajectory
    as a tool use that carries no skill name, which is what it was, rather than
    dropping the step and leaving a gap in the trajectory the judges read.
    """
    return args if isinstance(args, dict) else {"_raw": str(args)}


def _tool_result_body(call: dict) -> str:
    """What the tool returned, preferring the untruncated skill body (rule 2)."""
    return call.get("skill_content") or call.get("result_head") or ""


def build_trajectory(prompt: str, resp: dict, system_prompt: str = "") -> list[dict]:
    """The message list for `EvaluationData.actual_trajectory`.

    Args:
        prompt: The user turn.
        resp: A sandbox `run_case` response.
        system_prompt: The system prompt, carrying the `<available_skills>`
            block. Optional only because a replayed run may not have recorded
            it; without it the selection judge is told the catalog is unknown
            instead of being shown the alternatives.

    Returns:
        Alternating assistant `toolUse` / user `toolResult` messages, in call
        order, preceded by the system and user turns. When the sandbox recorded
        what the agent said ahead of a call (`said_before`), that text goes into
        the same assistant turn, before the `toolUse` block, which is where it
        was: a skill that asks for a narrated step before the first Write can
        only be graded on that step if the judge can read it.
    """
    messages: list[dict] = []
    if system_prompt:
        messages.append({"role": "system", "system_prompt": system_prompt})
    messages.append({"role": "user", "content": [{"text": prompt}]})

    for i, call in enumerate(resp.get("trace", {}).get("tool_calls") or []):
        tool_use_id = f"tooluse_{i}"
        content: list[dict] = []
        if call.get("said_before"):
            content.append({"text": call["said_before"]})
        content.append(
            {
                "toolUse": {
                    "name": call.get("name", "tool"),
                    "toolUseId": tool_use_id,
                    "input": _args_dict(call.get("args")),
                }
            }
        )
        messages.append({"role": "assistant", "content": content})
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "toolResult": {
                            "toolUseId": tool_use_id,
                            # `ok=False` is how the sandbox reports a refused
                            # load; the extractor reads this status to tell a
                            # refusal apart from a load whose body went
                            # uncaptured (rule 4).
                            "status": "success" if call.get("ok", True) else "error",
                            "content": [{"text": _tool_result_body(call)}],
                        }
                    }
                ],
            }
        )

    if resp.get("output"):
        messages.append({"role": "assistant", "content": [{"text": resp["output"]}]})
    return messages


def expected_trajectory(expect: dict) -> list[str] | None:
    """The case's expected tool names, for the trajectory evaluators."""
    return expect.get("tools_in_order") or expect.get("tools_any_order")
