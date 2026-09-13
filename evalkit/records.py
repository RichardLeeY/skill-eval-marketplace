"""Selection, atomic JSON records, and accepting an already reviewed report.

No model SDK imports: accepting a baseline must never execute an agent or judge.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from evalkit.dataset import load_cases
from evalkit.plugins import duplicate_ids, skill_datasets

ROOT = Path(__file__).resolve().parent.parent
MANUAL = ROOT / ".eval" / "manual"


class IntegrityError(ValueError):
    pass


def skills_root() -> Path:
    return Path(os.environ.get("SKILLS_DIR", ROOT / "skills")).resolve()


def select_datasets(root: Path, skills: list[str] | None = None) -> list[str]:
    available = {Path(p).parent.parent.name: p for p in skill_datasets(str(root))}
    names = skills or sorted(available)
    unknown = set(names) - available.keys()
    if unknown:
        raise IntegrityError(f"skills with no dataset: {', '.join(sorted(unknown))}")
    if not names:
        raise IntegrityError(f"no datasets under {root}")
    return [available[n] for n in dict.fromkeys(names)]


def requested_cases(paths: list[str], only: list[str] | None = None,
                    *, with_seeds: bool = False) -> list[dict]:
    all_cases = [(p, c) for p in paths for c in load_cases(p, with_seeds=with_seeds)]
    dupes = duplicate_ids([(p, c["id"]) for p, c in all_cases])
    if dupes:
        raise IntegrityError(f"duplicate dataset case ids: {', '.join(sorted(dupes))}")
    known = {c["id"] for _, c in all_cases}
    if only and (unknown := set(only) - known):
        raise IntegrityError(f"unknown case ids: {', '.join(sorted(unknown))}")
    cases = [c for _, c in all_cases if not only or c["id"] in only]
    if not cases:
        raise IntegrityError("no cases matched")
    for case in cases:
        cid = case["id"]
        if not isinstance(cid, str) or cid in (".", "..") or "/" in cid or "\\" in cid:
            raise IntegrityError(f"case id must be a single directory name: {cid!r}")
    return cases


def join_recording(cases: list[dict], path: str) -> list[dict]:
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(rows, list) or any(not isinstance(r, dict) or not r.get("case") for r in rows):
        raise IntegrityError(f"{path}: expected a list of case records")
    ids = [r["case"] for r in rows]
    if any(not isinstance(cid, str) for cid in ids):
        raise IntegrityError(f"{path}: case IDs must be strings")
    if len(ids) != len(set(ids)):
        raise IntegrityError(f"{path}: duplicate case records")
    records = {r["case"]: r for r in rows}
    missing = {c["id"] for c in cases} - records.keys()
    if missing:
        raise IntegrityError(f"{path}: missing case records: {', '.join(sorted(missing))}")
    for case in cases:
        row = records[case["id"]]
        for name, meta in (row.get("files") or {}).items():
            saved = meta.get("saved_to")
            if not saved or not Path(saved).is_file():
                raise IntegrityError(f"{case['id']}: missing saved artifact {name}")
            expected = meta.get("sha256")
            # The sandbox historically records a 16-character SHA256 prefix;
            # archive metadata uses full hashes. Accept both known formats.
            if expected and (len(expected) not in (16, 64)
                             or not digest(saved).startswith(expected)):
                raise IntegrityError(f"{case['id']}: changed saved artifact {name}")
    return [{"spec": c, "recorded": records[c["id"]]} for c in cases]


def digest(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path: str | Path, value: object) -> None:
    """Replace atomically, so interruption cannot leave half a baseline."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", delete=False) as f:
            temp = f.name
            json.dump(value, f, indent=2, ensure_ascii=False, allow_nan=False)
            f.write("\n")
        os.replace(temp, path)
    finally:
        if temp and os.path.exists(temp):
            os.unlink(temp)


def metadata_path(report: str | Path) -> Path:
    return Path(report).with_suffix(".meta.json")


def accept_baseline(report: str | Path, baseline: str | Path) -> int:
    """Accept exactly the scored bytes the caller reviewed; refuse failed gates."""
    report, baseline = Path(report), Path(baseline)
    meta = json.loads(metadata_path(report).read_text(encoding="utf-8"))
    rows = json.loads(report.read_text(encoding="utf-8"))
    # A regression can be intentionally accepted, but failed correctness gates cannot.
    if meta.get("gates_passed") is not True or meta.get("complete") is not True:
        raise IntegrityError("report is incomplete or has failed gates; baseline was not changed")
    if meta.get("negative_controls_requested") and meta.get("controls_complete") is not True:
        raise IntegrityError("requested negative controls have not completed")
    if meta.get("report_sha256") != digest(report):
        raise IntegrityError("report differs from its completion metadata")
    if not isinstance(rows, list) or not rows:
        raise IntegrityError("report has no scored cases")
    ids = [r["case"] for r in rows]
    if len(ids) != len(set(ids)) or set(ids) != set(meta.get("requested_cases", [])):
        raise IntegrityError("report case set does not match the requested cases")
    for r in rows:
        gates = [x for x in r.get("rows", []) if x.get("gate", True)]
        if not gates or any(x.get("test_pass") is not True for x in gates):
            raise IntegrityError(f"{r['case']}: absent or failed gating results")
        score = r.get("overall_score")
        if not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 1:
            raise IntegrityError(f"{r['case']}: invalid score")
    data = json.loads(baseline.read_text(encoding="utf-8")) if baseline.exists() else {}
    existing = data.get("cases", data)
    now = datetime.now(timezone.utc).isoformat()
    for row in rows:
        existing[row["case"]] = {
            "overall_score": row["overall_score"], "skill": row["skill"],
            "judge_model": row["judge_model"], "judge_provider": row.get("judge_provider"),
            "rubric_hash": row["rubric_hash"], "scoring_profile": meta["scoring_profile"],
            "recorded": now, "report_sha256": meta["report_sha256"],
        }
    write_json(baseline, {
        "_comment": "Last explicitly accepted scores. Updated from a completed report without re-scoring.",
        "cases": dict(sorted(existing.items())),
    })
    return len(rows)
