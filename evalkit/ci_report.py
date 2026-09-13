"""Build an offline HTML dashboard and notification-ready JSON from CI artifacts."""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape
import json
import os
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent


def load(path: Path, default, errors: list[str]):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        errors.append(f"{path.name}: {exc}")
        return default


def baseline_note(case: dict, baseline: dict) -> str:
    previous = baseline.get(case["case"])
    if not previous:
        return "No baseline"
    if previous.get("scoring_profile") and previous["scoring_profile"] != case.get("scoring_profile"):
        return "Incompatible profiles"
    delta = case["overall_score"] - float(previous["overall_score"])
    notes = [f"{delta:+.3f}"]
    if not previous.get("scoring_profile"):
        notes.append("legacy profile unknown")
    for key, label in (("rubric_hash", "rubric"), ("judge_model", "judge"),
                       ("judge_provider", "judge provider")):
        if previous.get(key) and previous[key] != case.get(key):
            notes.append(f"{label} changed")
    return "; ".join(notes)


def summarize(root: Path, env: dict) -> dict:
    errors = []
    directory = root / ".eval"
    selection = load(directory / "selection.json", {}, errors)
    base = load(root / "eval-baseline.json", {}, errors)
    baseline = base.get("cases", base)
    cases, runs, controls = [], [], []
    for run_path in sorted(directory.glob("runs/*/run.json")):
        run = load(run_path, {}, errors)
        score_paths = sorted(run_path.parent.glob("scores/*/report.meta.json"))
        meta_path = score_paths[-1] if score_paths else None
        meta = load(meta_path, {}, errors) if meta_path else {}
        report_path = meta_path.with_name("report.json") if meta_path else None
        reports = load(report_path, [], errors) if report_path else []
        complete = (run.get("status") == "passed" and meta.get("complete") is True
                    and meta.get("gates_passed") is True and not meta.get("regressed")
                    and (not meta.get("negative_controls_requested")
                         or meta.get("controls_complete") is True))
        if set(meta.get("requested_cases", [])) != {c["case"] for c in reports} or not reports:
            complete = False
        runs.append({"run": str(run_path.parent.relative_to(directory)), "passed": complete})
        for control in meta.get("negative_controls", []):
            controls.append(control)
        for case in reports:
            failures = [row for row in case["rows"]
                        if row.get("gate", True) and not row.get("test_pass")]
            regressed = case["case"] in meta.get("regressed", [])
            cases.append({
                "case": case["case"], "skill": case.get("skill", ""),
                "score": case["overall_score"], "failures": failures,
                "status": "FAIL" if failures or regressed else
                          ("PASS" if meta.get("complete") is True else "INCOMPLETE"),
                "baseline": baseline_note(case, baseline),
                "profile": case.get("scoring_profile", ""),
                "agent": case.get("agent_model", ""), "judge": case.get("judge_model", ""),
                "report": str(report_path.relative_to(directory)),
            })
    if selection.get("error"):
        errors.append(selection["error"])
    completed = (selection.get("exit_code") == 0 and not selection.get("plan_only")
                 and not errors and env.get("CI_JOB_STATUS") not in ("failed", "canceled")
                 and not any(c["failures"] or c["status"] != "PASS" for c in cases))
    if completed and runs and all(run["passed"] for run in runs):
        status = "PASS"
    elif completed and selection.get("skills") == [] and not runs:
        status = "SKIPPED"
    else:
        status = "FAIL / INCOMPLETE"
    return {
        "status": status, "generated_at": datetime.now(timezone.utc).isoformat(),
        "pipeline": env.get("CI_PIPELINE_URL", ""), "job": env.get("CI_JOB_URL", ""),
        "commit": env.get("CI_COMMIT_SHA", ""), "selection": selection,
        "failed_rows": sum(len(c["failures"]) for c in cases),
        "cases": cases, "runs": runs, "controls": controls, "errors": errors,
    }


def render(summary: dict) -> str:
    def text(value):
        return escape(str(value))

    def link(path, label):
        return f'<a href="{quote(path, safe="/")}">{text(label)}</a>'

    selection = summary["selection"]
    rows, details = [], []
    for case in summary["cases"]:
        rows.append(
            f'<tr><td>{text(case["skill"])}</td><td>{text(case["case"])}</td>'
            f'<td>{case["score"]:.3f}</td><td>{text(case["status"])}</td>'
            f'<td>{text(case["baseline"])}</td></tr>')
        failures = "".join(
            f'<h4>{text(row["evaluator"])}</h4><pre>{text(row["reason"])}</pre>'
            for row in case["failures"])
        details.append(
            f'<details{" open" if case["failures"] else ""}><summary>{text(case["case"])}'
            f' — {text(case["status"])}</summary>'
            f'<p>Profile: {text(case["profile"])} · Agent: {text(case["agent"])}'
            f' · Judge: {text(case["judge"])}</p>'
            f'<p>{link(case["report"], "Full report JSON")}</p>'
            f'{failures or "<p>No failing gate rows. See baseline and run completion status.</p>"}'
            '</details>')
    run_links = "".join(
        f'<li>{text(run["run"])}: '
        + " · ".join(link(f'{run["run"]}/{name}', name)
                     for name in ("run.json", "results.json", "execute.log"))
        + '</li>' for run in summary["runs"])
    errors = "".join(f'<li>{text(error)}</li>' for error in summary["errors"])
    controls = "".join(
        f'<li>{text(c["skill"])}: {text(c.get("status", "exit code " + str(c.get("exit_code"))))}</li>'
        for c in summary["controls"])
    return f"""<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Skill evaluation — {text(summary["status"])}</title>
<style>
body{{font:16px/1.6 system-ui,sans-serif;margin:2rem auto;padding:0 1rem;max-width:1200px;color:#182b43;background:#f6f8fb}}
h1,h2{{line-height:1.2}}h2{{margin-top:2rem}}a{{color:#075ea8}}
.card,details{{background:white;border:1px solid #d5dde8;border-radius:8px;padding:1rem;margin:1rem 0}}
.scroll{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;background:white}}
td,th{{padding:.6rem;text-align:left;border-bottom:1px solid #d5dde8}}
summary{{cursor:pointer;font-weight:600}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}}
.muted{{color:#52657c;overflow-wrap:anywhere}}
</style>
<h1>Skill evaluation: {text(summary["status"])}</h1>
<p class="muted">MR {text(selection.get("merge_request") or "manual")} · Commit {text(summary["commit"])}</p>
<p class="muted">{text(summary["pipeline"])}<br>{text(summary["generated_at"])}</p>
<div class="card"><strong>{len(selection.get("skills", []))} selected skills ·
{len(summary["cases"])} scored cases · {summary["failed_rows"]} failing gate rows</strong>
<p>Selection: {text(selection.get("mode", "unavailable"))} —
{text(", ".join(selection.get("skills", [])) or "none")}</p>
<p>Shared changes: {text(", ".join(selection.get("shared_changes", [])) or "none")}.
Removed skills: {text(", ".join(selection.get("removed_skills", [])) or "none")}.</p>
{link("selection.json", "Selection JSON")} · {link("summary.json", "Summary JSON")}</div>
<p>Overall scores are descriptive. Passing requires all gating rows, complete execution,
baseline checks and requested negative controls to pass. This report covers this job only.</p>
{"<h2>Errors</h2><ul>" + errors + "</ul>" if errors else ""}
{"<p>No scored report was produced. Inspect the GitLab job log for setup or execution errors.</p>" if not rows and summary["status"] != "SKIPPED" else ""}
<h2>Cases</h2><div class="scroll"><table><thead><tr><th>Skill</th><th>Case</th><th>Overall</th>
<th>Gate rows / baseline</th><th>Baseline delta / provenance</th></tr></thead><tbody>
{"".join(rows)}</tbody></table></div>
<h2>Evidence</h2>{"".join(details)}
<h2>Negative controls</h2><ul>{controls or "<li>No control results recorded.</li>"}</ul>
<h2>Run artifacts</h2><ul>{run_links}</ul>
<p class="muted">Download and extract the full artifact archive to follow local evidence links.
No external scripts or services are needed.</p></html>"""


def main() -> int:
    summary = summarize(ROOT, os.environ)
    directory = ROOT / ".eval"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (directory / "dashboard.html").write_text(render(summary), encoding="utf-8")
    print(f'Evaluation dashboard: .eval/dashboard.html ({summary["status"]})')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
