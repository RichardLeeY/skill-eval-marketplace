"""
Deterministic graders that score a sandbox response.

These run against the response contract that sandbox.run_case() returns, so the
same graders work locally, in CI, and (wrapped) as an AgentCore custom code
evaluator. Anything an LLM judge is *not* needed for belongs here: it is cheaper,
repeatable, and cannot drift.

Each grader returns {name, passed, score, detail}.
"""

from __future__ import annotations

import fnmatch
import re

Grade = dict


def _ok(name: str, passed: bool, detail: str = "") -> Grade:
    return {"name": name, "passed": bool(passed), "score": 1.0 if passed else 0.0, "detail": detail}


def _artifact_text(resp: dict, glob: str | None = None) -> str:
    parts = []
    for rel, meta in (resp.get("files") or {}).items():
        if glob and not fnmatch.fnmatch(rel, glob):
            continue
        parts.append(meta.get("text") or "")
    return "\n".join(parts)


def _bash_outputs(resp: dict) -> str:
    return "\n".join(
        c.get("result_head", "")
        for c in (resp.get("trace", {}).get("tool_calls") or [])
        if c.get("name") == "Bash"
    )


# --------------------------------------------------------------------------- #
# Graders
# --------------------------------------------------------------------------- #
def completed(resp: dict, _e: dict) -> Grade:
    return _ok("completed", resp.get("status") == "ok", resp.get("error") or "")


def skill_selected(resp: dict, expect: dict) -> Grade:
    want = expect.get("skill")
    if not want:
        return _ok("skill_selected", True, "no expectation")
    loaded = resp.get("skills_loaded") or []
    return _ok("skill_selected", want in loaded, f"loaded={loaded} want={want}")


def no_wrong_skill(resp: dict, expect: dict) -> Grade:
    """One skill per case, by design.

    Loading a second skill fails the case even when the first one was correct.
    That is deliberate: multi-skill evaluation is out of scope, so there is no
    `expect.skills` list and layer 2 resolves exactly one plugin from
    `expect.skill`. The sandbox system prompt tells the agent the same thing --
    keep the two in step, or a run gets marked down for doing what it was asked.
    """
    want = expect.get("skill")
    loaded = [s for s in (resp.get("skills_loaded") or []) if s != want]
    return _ok("no_wrong_skill", not loaded, f"extra={loaded}")


def file_produced(resp: dict, expect: dict) -> Grade:
    glob = expect.get("file_glob")
    if not glob:
        return _ok("file_produced", True, "no expectation")
    hits = [r for r in (resp.get("files") or {}) if fnmatch.fnmatch(r, glob)]
    return _ok("file_produced", bool(hits), f"glob={glob} hits={hits}")


def artifact_regex(resp: dict, expect: dict) -> list[Grade]:
    out = []
    text = _artifact_text(resp, expect.get("file_glob"))
    for pat in expect.get("artifact_regex") or []:
        n = len(re.findall(pat, text))
        out.append(_ok(f"regex:{pat[:38]}", n > 0, f"{n} match(es)"))
    for pat in expect.get("artifact_regex_absent") or []:
        n = len(re.findall(pat, text))
        out.append(_ok(f"absent:{pat[:38]}", n == 0, f"{n} match(es)"))
    return out


def tools_used(resp: dict, expect: dict) -> list[Grade]:
    used = resp.get("trace", {}).get("tool_names") or []
    out = []
    for t in expect.get("tools_any_order") or []:
        out.append(_ok(f"tool_used:{t}", t in used, f"trace={used}"))
    order = expect.get("tools_in_order") or []
    if order:
        i, matched = 0, []
        for t in used:
            if i < len(order) and t == order[i]:
                matched.append(t)
                i += 1
        out.append(_ok("tools_in_order", i == len(order), f"want={order} matched={matched}"))
    return out


def validator_passed(resp: dict, expect: dict) -> Grade:
    """The skill ships its own validator. Requiring its PASS line is the single
    highest-signal deterministic check: it proves the skill's prescribed path
    was actually executed, not just described."""
    pat = expect.get("validator_pass_regex")
    if not pat:
        return _ok("validator_passed", True, "no expectation")
    out = _bash_outputs(resp)
    m = re.search(pat, out)
    return _ok("validator_passed", bool(m), (m.group(0) if m else "no PASS line in any bash output"))


def generator_not_bypassed(resp: dict, expect: dict) -> Grade:
    """Catches the most common way a skill is silently violated: the model
    hand-writes the artifact with Write instead of running the skill's
    generator. Scored separately from correctness because the output can look
    fine while the skill was ignored."""
    marker = expect.get("handwrite_marker")
    if not marker:
        return _ok("generator_not_bypassed", True, "no expectation")
    offenders = [
        c["args"].get("path")
        for c in (resp.get("trace", {}).get("tool_calls") or [])
        if c.get("name") == "Write" and re.search(marker, str(c["args"].get("content", "")))
    ]
    return _ok("generator_not_bypassed", not offenders, f"hand-written via Write: {offenders}")


def count_at_least(resp: dict, expect: dict) -> list[Grade]:
    out = []
    text = _artifact_text(resp, expect.get("file_glob"))
    for pat, minimum in (expect.get("min_counts") or {}).items():
        n = len(re.findall(pat, text))
        out.append(_ok(f"count>={minimum}:{pat[:30]}", n >= minimum, f"found {n}"))
    return out


def tool_budget(resp: dict, expect: dict) -> Grade:
    runtime = resp.get("tool_budget") or {}
    if runtime.get("exhausted"):
        return _ok("tool_budget", False,
                   f"execution limit {runtime['limit']} exhausted "
                   f"({runtime['attempted']} attempts, {runtime['completed']} completed calls)")
    cap = expect.get("max_tool_calls")
    if not cap:
        return _ok("tool_budget", True, "no expectation")
    n = resp.get("trace", {}).get("n_tool_calls", 0)
    return _ok("tool_budget", n <= cap, f"{n} calls (cap {cap})")


GRADERS = [completed, skill_selected, no_wrong_skill, file_produced, validator_passed,
           generator_not_bypassed, tool_budget]
MULTI_GRADERS = [artifact_regex, tools_used, count_at_least]


def grade(resp: dict, expect: dict) -> dict:
    grades: list[Grade] = [g(resp, expect) for g in GRADERS]
    for g in MULTI_GRADERS:
        grades.extend(g(resp, expect))
    passed = sum(1 for g in grades if g["passed"])
    return {
        "grades": grades,
        "passed": passed,
        "total": len(grades),
        "score": round(passed / len(grades), 4) if grades else 0.0,
        "all_passed": passed == len(grades),
    }
