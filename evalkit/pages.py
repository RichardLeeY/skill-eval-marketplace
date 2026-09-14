"""Build the public marketplace scoreboard published on GitHub Pages.

The site directory is a checkout of the ``gh-pages`` branch, which GitHub Pages
serves directly. Output is deterministic for a given history, so rebuilding without
a new run produces no diff. Each publish appends
the current ``.eval`` run (as summarised by ``evalkit.ci_report``) to a small
append-only history and rebuilds the static pages from it:

    index.html               skill scoreboard, run history, no scripts
    data/index.json          one entry per published run, newest first
    data/runs/<sha>.json     the run's summary.json
    runs/<sha>/...           the per-run dashboard and the evidence it links to
    badge/<skill>.json       shields.io endpoint badges, plus badge/marketplace.json

Only an allowlist of evidence is copied: the report JSON files the dashboard links
to, run metadata and logs, and image previews under a size cap. Video and other
binaries stay in the CI artifact. Anything on Pages is public.

Accepted scores from ``eval-baseline.json`` are always shown next to the latest run,
so the site still carries a "what the reviewers accepted" column when the model
evaluation was skipped or failed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from html import escape
import json
import os
from pathlib import Path
import shutil
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_THRESHOLD = 0.90
IMAGE_SUFFIXES = {".png", ".webp", ".gif", ".jpg", ".jpeg", ".svg"}
IMAGE_LIMIT = 2 * 1024 * 1024
ALWAYS_COPY = {"dashboard.html", "summary.json", "summary.md", "selection.json"}
RUN_FILES = {"run.json", "cases.json", "results.json", "execute.log"}
SCORE_FILES = {"report.json", "report.meta.json"}


def _load_json(path: Path, default):
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return default


def allowed(relative: Path) -> bool:
    """Decide whether one file from ``.eval`` may be published."""
    parts = relative.parts
    if len(parts) == 1:
        return parts[0] in ALWAYS_COPY
    if parts[0] != "runs" or len(parts) < 3:
        return False
    if len(parts) == 3:
        return parts[2] in RUN_FILES
    if parts[2] == "scores" and len(parts) == 5:
        return parts[4] in SCORE_FILES
    if parts[2] == "artifacts" or (parts[2] == "scores" and len(parts) > 4 and parts[4] == "derived"):
        return relative.suffix.lower() in IMAGE_SUFFIXES
    return False


def copy_evidence(source: Path, destination: Path) -> list[str]:
    """Copy allowlisted evidence from ``source`` (.eval) into ``destination``."""
    copied = []
    for path in sorted(p for p in source.rglob("*") if p.is_file()):
        relative = path.relative_to(source)
        if not allowed(relative):
            continue
        if relative.suffix.lower() in IMAGE_SUFFIXES and path.stat().st_size > IMAGE_LIMIT:
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        copied.append(relative.as_posix())
    return copied


def run_entry(summary: dict, env: dict, threshold: float) -> dict:
    """Compress a ci_report summary into the record kept in data/index.json."""
    sha = summary.get("commit") or env.get("GITHUB_SHA") or "local"
    skills: dict[str, dict] = {}
    cases = []
    for case in summary.get("cases", []):
        skill = case.get("skill") or "unknown"
        record = skills.setdefault(skill, {"cases": 0, "passed": 0, "scores": []})
        record["cases"] += 1
        record["scores"].append(float(case["score"]))
        passed = case.get("status") == "PASS" and float(case["score"]) >= threshold
        record["passed"] += int(passed)
        cases.append({"case": case["case"], "skill": skill,
                      "score": round(float(case["score"]), 4), "status": case.get("status", "")})
    for record in skills.values():
        scores = record.pop("scores")
        record["mean"] = round(sum(scores) / len(scores), 4)
        record["min"] = round(min(scores), 4)
        record["status"] = "PASS" if record["passed"] == record["cases"] else "FAIL"
    server = env.get("GITHUB_SERVER_URL", "https://github.com")
    repo = env.get("GITHUB_REPOSITORY", "")
    return {
        "sha": sha, "short": sha[:7],
        "date": summary.get("generated_at") or datetime.now(timezone.utc).isoformat(),
        "status": summary.get("status", "FAIL / INCOMPLETE"),
        "pipeline": summary.get("pipeline", ""),
        "commit_url": f"{server}/{repo}/commit/{sha}" if repo and sha != "local" else "",
        "selection": (summary.get("selection") or {}).get("skills", []),
        "skills": dict(sorted(skills.items())), "cases": cases,
        "errors": len(summary.get("errors", [])),
    }


def baseline_by_skill(baseline: dict) -> dict:
    cases = baseline.get("cases", baseline)
    skills: dict[str, dict] = {}
    for name, case in cases.items():
        if not isinstance(case, dict) or "overall_score" not in case:
            continue
        record = skills.setdefault(case.get("skill", "unknown"), {"cases": 0, "scores": [], "latest": ""})
        record["cases"] += 1
        record["scores"].append(float(case["overall_score"]))
        record["latest"] = max(record["latest"], str(case.get("recorded", "")))
    for record in skills.values():
        scores = record.pop("scores")
        record["mean"] = round(sum(scores) / len(scores), 4)
        record["min"] = round(min(scores), 4)
    return skills


def available_skills(root: Path) -> list[str]:
    """Skills that take part in evaluation: a SKILL.md *and* an eval dataset.

    A skill with no `eval/dataset.jsonl` never produces a score, so a row for it
    would read "NO RUN" forever. Withdrawing the dataset (or renaming the directory
    to `eval.disabled/`) is how a skill leaves the scoreboard.
    """
    skills = root / "skills"
    if not skills.is_dir():
        return []
    return sorted(p.name for p in skills.iterdir()
                  if p.is_dir() and not p.name.startswith(".") and (p / "SKILL.md").is_file()
                  and (p / "eval" / "dataset.jsonl").is_file())


def shown_skills(index: list[dict], baseline: dict, skills: list[str]) -> list[str]:
    """Rows the scoreboard carries: evaluated now, accepted before, or in the latest run.

    Not every skill that ever appeared in the history. A skill whose evaluation
    was withdrawn would otherwise keep a row, and a stale badge, from a run
    nobody can reproduce against the current tree.
    """
    latest = index[0] if index else {}
    return sorted(set(skills) | set(baseline) | set(latest.get("skills", {})))


def badge(label: str, score: float | None, status: str | None, threshold: float) -> dict:
    if score is None:
        return {"schemaVersion": 1, "label": label, "message": "no run", "color": "lightgrey"}
    if status == "PASS" and score >= threshold:
        color = "brightgreen"
    elif score >= threshold:
        color = "yellow"
    else:
        color = "red"
    return {"schemaVersion": 1, "label": label, "message": f"{score:.3f}", "color": color}


def update_index(index: list[dict], entry: dict | None, keep: int) -> tuple[list[dict], list[str]]:
    """Insert ``entry`` newest-first, drop duplicates by sha, return pruned shas."""
    entries = [e for e in index if not entry or e.get("sha") != entry["sha"]]
    if entry:
        entries.insert(0, entry)
    entries.sort(key=lambda e: e.get("date", ""), reverse=True)
    pruned = [e["sha"] for e in entries[keep:]]
    return entries[:keep], pruned


def sparkline(values: list[float], threshold: float, width: int = 120, height: int = 28) -> str:
    """Inline SVG trend, oldest to newest, with the threshold as a dashed line."""
    if not values:
        return ""
    low, high = min(values + [threshold]), max(values + [threshold])
    span = (high - low) or 0.01

    def y(value):
        return round(height - 3 - (value - low) / span * (height - 6), 1)

    step = width / max(len(values) - 1, 1)
    points = " ".join(f"{round(i * step, 1)},{y(v)}" for i, v in enumerate(values))
    if len(values) == 1:
        points = f"0,{y(values[0])} {width},{y(values[0])}"
    return (f'<svg class="spark" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
            f'role="img" aria-label="trend"><line x1="0" x2="{width}" y1="{y(threshold)}" '
            f'y2="{y(threshold)}" stroke="#b6c2d1" stroke-dasharray="3 3"/>'
            f'<polyline fill="none" stroke="#075ea8" stroke-width="2" points="{points}"/></svg>')


def render_index(index: list[dict], baseline: dict, skills: list[str], threshold: float,
                 repo: str, server: str) -> str:
    def text(value):
        return escape(str(value))

    def href(path):
        return quote(path, safe="/:#?=&")

    latest = index[0] if index else None
    repo_url = f"{server}/{repo}" if repo else ""
    shown = shown_skills(index, baseline, skills)
    rows = []
    for skill in shown:
        history = [e["skills"][skill]["mean"] for e in reversed(index) if skill in e.get("skills", {})]
        current = latest["skills"].get(skill) if latest else None
        accepted = baseline.get(skill)
        if current:
            status = current["status"] if current["mean"] >= threshold else "FAIL"
            latest_cell = (f'{current["mean"]:.3f} <span class="muted">(min {current["min"]:.3f}, '
                           f'{current["passed"]}/{current["cases"]} cases)</span>')
        else:
            status, latest_cell = "NO RUN", '<span class="muted">not in latest run</span>'
        accepted_cell = (f'{accepted["mean"]:.3f} <span class="muted">(min {accepted["min"]:.3f}, '
                         f'{accepted["cases"]} cases, {text(accepted["latest"])})</span>'
                         if accepted else '<span class="muted">none</span>')
        name = (f'<a href="{href(repo_url + "/blob/main/skills/" + skill + "/SKILL.md")}">{text(skill)}</a>'
                if repo_url else text(skill))
        rows.append(f'<tr><td>{name}</td><td class="{text(status).split()[0].lower()}">{text(status)}</td>'
                    f'<td>{latest_cell}</td><td>{accepted_cell}</td><td>{sparkline(history, threshold)}</td>'
                    f'<td><a href="{href("badge/" + skill + ".json")}">badge</a></td></tr>')
    runs = []
    for entry in index:
        per_skill = ", ".join(f'{text(s)} {r["mean"]:.3f}' for s, r in entry.get("skills", {}).items()
                              if s in shown) or "no scored cases"
        commit = (f'<a href="{href(entry["commit_url"])}">{text(entry["short"])}</a>'
                  if entry.get("commit_url") else text(entry["short"]))
        pipeline = f' · <a href="{href(entry["pipeline"])}">CI run</a>' if entry.get("pipeline") else ""
        runs.append(f'<tr><td>{text(entry["date"][:19].replace("T", " "))}</td><td>{commit}</td>'
                    f'<td class="{text(entry["status"]).split()[0].lower()}">{text(entry["status"])}</td>'
                    f'<td>{per_skill}</td><td><a href="{href("runs/" + entry["sha"] + "/dashboard.html")}">'
                    f'dashboard</a>{pipeline}</td></tr>')
    headline = (f'Latest run {text(latest["status"])} · {text(latest["date"][:10])} · commit {text(latest["short"])}'
                if latest else "No published run yet")
    return f"""<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Skill marketplace scoreboard</title>
<style>
body{{font:16px/1.6 system-ui,sans-serif;margin:2rem auto;padding:0 1rem;max-width:1200px;color:#182b43;background:#f6f8fb}}
h1,h2{{line-height:1.2}}h2{{margin-top:2rem}}a{{color:#075ea8}}
.card{{background:white;border:1px solid #d5dde8;border-radius:8px;padding:1rem;margin:1rem 0}}
.scroll{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;background:white}}
td,th{{padding:.6rem;text-align:left;border-bottom:1px solid #d5dde8;vertical-align:top}}
.muted{{color:#52657c}}.pass{{color:#1d7a3e;font-weight:600}}.fail{{color:#b3261e;font-weight:600}}
.no,.skipped{{color:#52657c;font-weight:600}}.spark{{display:block}}
</style>
<h1>Skill marketplace scoreboard</h1>
<p class="muted">{headline}. Threshold {threshold:.2f}.</p>
<div class="card">Every skill in the marketplace ships its own evaluation cases. A pull request cannot
merge until its skill scores at or above the threshold; this page publishes the result of every
evaluation of <code>main</code>. <strong>Latest</strong> is the most recent full run.
<strong>Accepted</strong> is the reviewed baseline the gate compares against.
{f'<a href="{href(repo_url)}">Repository</a> · ' if repo_url else ''}<a href="data/index.json">History JSON</a></div>
<h2>Skills</h2><div class="scroll"><table><thead><tr><th>Skill</th><th>Status</th><th>Latest</th>
<th>Accepted</th><th>Trend</th><th>Badge</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>
<h2>Runs</h2><div class="scroll"><table><thead><tr><th>Date</th><th>Commit</th><th>Status</th>
<th>Scores</th><th>Evidence</th></tr></thead><tbody>{"".join(runs) or '<tr><td colspan="5">None yet.</td></tr>'}
</tbody></table></div>
<p class="muted">Overall scores are descriptive. A run passes only when every gating assertion holds,
execution completed, the baseline check passed and requested negative controls ran. Video evidence
stays in the CI artifact; image previews and report JSON are published here.</p></html>"""


def build(site: Path, run: Path | None, baseline_path: Path, root: Path, env: dict, *,
          keep: int = 30, threshold: float = DEFAULT_THRESHOLD) -> dict:
    site.mkdir(parents=True, exist_ok=True)
    (site / "data" / "runs").mkdir(parents=True, exist_ok=True)
    (site / "badge").mkdir(exist_ok=True)
    index = _load_json(site / "data" / "index.json", [])
    if not isinstance(index, list):
        index = []
    entry = None
    summary = _load_json(run / "summary.json", None) if run else None
    if isinstance(summary, dict):
        entry = run_entry(summary, env, threshold)
        target = site / "runs" / entry["sha"]
        if target.exists():
            shutil.rmtree(target)
        entry["files"] = len(copy_evidence(run, target))
        (site / "data" / "runs" / f'{entry["sha"]}.json').write_text(
            json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    index, pruned = update_index(index, entry, keep)
    for sha in pruned:
        shutil.rmtree(site / "runs" / sha, ignore_errors=True)
        (site / "data" / "runs" / f"{sha}.json").unlink(missing_ok=True)
    baseline = baseline_by_skill(_load_json(baseline_path, {}))
    skills = available_skills(root)
    latest = index[0] if index else None
    shown = shown_skills(index, baseline, skills)
    for stale in (site / "badge").glob("*.json"):
        if stale.stem != "marketplace" and stale.stem not in shown:
            stale.unlink()
    # Badge files are named from the tree and the reviewed baseline only; a skill
    # name that exists nowhere but in a run summary is not trusted as a filename.
    for skill in sorted(set(skills) | set(baseline)):
        current = (latest or {}).get("skills", {}).get(skill)
        data = badge(f"{skill} eval", current["mean"] if current else None,
                     current["status"] if current else None, threshold)
        (site / "badge" / f"{skill}.json").write_text(json.dumps(data) + "\n", encoding="utf-8")
    if latest and latest["skills"]:
        means = [r["mean"] for r in latest["skills"].values()]
        overall = badge("skill eval", sum(means) / len(means),
                        "PASS" if latest["status"] == "PASS" else "FAIL", threshold)
    else:
        overall = badge("skill eval", None, None, threshold)
    (site / "badge" / "marketplace.json").write_text(json.dumps(overall) + "\n", encoding="utf-8")
    (site / "data" / "index.json").write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    (site / "index.html").write_text(render_index(
        index, baseline, skills, threshold, env.get("GITHUB_REPOSITORY", ""),
        env.get("GITHUB_SERVER_URL", "https://github.com")), encoding="utf-8")
    (site / ".nojekyll").write_text("", encoding="utf-8")
    return {"runs": len(index), "published": entry["sha"] if entry else None, "pruned": pruned}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--site", required=True, help="checkout of the gh-pages branch")
    parser.add_argument("--run", default=str(ROOT / ".eval"),
                        help=".eval directory with summary.json from evalkit.ci_report; missing is allowed")
    parser.add_argument("--baseline", default=str(ROOT / "eval-baseline.json"))
    parser.add_argument("--keep", type=int, default=30, help="runs to retain in the history")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    args = parser.parse_args(argv)
    run = Path(args.run)
    result = build(Path(args.site), run if run.is_dir() else None, Path(args.baseline), ROOT,
                   os.environ, keep=args.keep, threshold=args.threshold)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
