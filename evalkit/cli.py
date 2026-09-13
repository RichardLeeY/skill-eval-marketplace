"""One entry point for setup, execution, recorded scoring and baseline acceptance."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

from evalkit import doctor, models
from evalkit.records import (
    ROOT, IntegrityError, accept_baseline, digest, join_recording, metadata_path,
    requested_cases, select_datasets, skills_root, write_json,
)

RUNS = ROOT / ".eval" / "runs"


def unique_dir(parent: Path) -> Path:
    name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    path = parent / name
    path.mkdir(parents=True, exist_ok=False)
    return path


def selection(args):
    root = skills_root()
    paths = select_datasets(root, args.skill)
    cases = requested_cases(paths, args.only, with_seeds=True)
    wanted = {c["id"] for c in cases}
    names = sorted({Path(p).parent.parent.name for p in paths
                    if wanted.intersection(c["id"] for c in requested_cases([p]))})
    return root, paths, cases, names


def execute(command: list[str], *, cwd: Path = ROOT, log: Path | None = None,
            quiet: bool = False) -> int:
    print("+ " + " ".join(command), flush=True)
    if log is None:
        return subprocess.run(command, cwd=cwd,
                              stdout=subprocess.DEVNULL if quiet else None).returncode
    with log.open("w", encoding="utf-8") as out:
        with subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True) as proc:
            for line in proc.stdout:
                print(line, end="", flush=True)
                out.write(line)
                out.flush()
            return proc.wait()


def setup(args) -> int:
    root, _, _, names = selection(args)
    requirements = [root / n / "requirements.txt" for n in names
                    if (root / n / "requirements.txt").exists()]
    use_openai = "openai" in (models.agent_provider(), models.judge_provider())
    if requirements or use_openai:
        with tempfile.TemporaryDirectory(prefix="skill-eval-") as temp:
            constraints = Path(temp) / "constraints.txt"
            rc = execute(["uv", "export", "--locked", "--all-extras", "--no-dev",
                          "--no-emit-project", "--no-hashes", "--output-file", str(constraints)],
                         quiet=True)
            if rc:
                return rc
            cmd = ["uv", "pip", "install", "--python", sys.executable,
                   "--constraint", str(constraints)]
            for req in requirements:
                cmd += ["-r", str(req)]
            if use_openai:
                cmd += ["strands-agents[openai]"]
            if rc := execute(cmd):
                return rc
    for name in names:
        scripts = root / name / "scripts"
        pkg = scripts / "package.json"
        if not pkg.exists():
            continue
        if rc := execute(["npm", "ci"], cwd=scripts):
            return rc
        data = json.loads(pkg.read_text())
        deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
        if "playwright" in deps:
            cmd = ["npx", "--no-install", "playwright", "install"]
            if args.with_system_deps:
                cmd += ["--with-deps"]
            if rc := execute(cmd + ["chromium"], cwd=scripts):
                return rc
    print(f"Setup complete for: {', '.join(names)}")
    print("Run skill-eval doctor with the same selection to check system tools and credentials.")
    return 0


def locate_run(value: str) -> Path:
    path = Path(value)
    path = path if path.exists() else RUNS / value
    if not (path / "run.json").is_file():
        raise IntegrityError(f"not an archived run: {value}")
    return path.resolve()


def score_recording(run_dir: Path, args, *, preflight: bool = True) -> int:
    config = json.loads((run_dir / "run.json").read_text())
    root = Path(config["skills_root"])
    if root != skills_root():
        raise IntegrityError(f"recording used {root}; set SKILLS_DIR to that directory before scoring")
    dataset, results = run_dir / "cases.json", run_dir / "results.json"
    if digest(dataset) != config["cases_sha256"]:
        raise IntegrityError("archived dataset changed since execution")
    if config.get("results_sha256") and digest(results) != config["results_sha256"]:
        raise IntegrityError("recording changed since execution")
    cases = requested_cases([str(dataset)])
    join_recording(cases, str(results))
    profile = args.profile or config["profile"]
    if preflight and doctor.display(doctor.check(
            root, config["skills"], cases, profile=profile, phase="score")):
        return 2
    score_dir = unique_dir(run_dir / "scores")
    report = score_dir / "report.json"
    cmd = [sys.executable, "-u", "-m", "evalkit.run_strands_eval",
           "--results", str(results), "--cases", str(dataset), "--out", str(report),
           "--artifacts", str(score_dir / "derived"), "--baseline", str(Path(args.baseline).resolve()),
           "--tolerance", str(args.tolerance)]
    if profile == "core":
        cmd += ["--no-visual"]
    if args.no_baseline:
        cmd += ["--no-baseline"]
    check_controls = profile == "full" or args.negative_controls
    if check_controls:
        cmd += ["--pending-controls"]
    rc = execute(cmd, log=score_dir / "score.log")
    controls = []
    meta_path = metadata_path(report)
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
        scoring_gates = meta.get("gates_passed") is True
        meta["negative_controls"] = controls
        meta["negative_controls_requested"] = check_controls
        if check_controls:
            meta["gates_passed"] = False
            write_json(meta_path, meta)
        if check_controls and meta.get("complete"):
            for name in config["skills"]:
                script = root / name / "eval" / "check_negative_control.py"
                if script.exists():
                    result = execute([sys.executable, str(script)],
                                     log=score_dir / f"control-{name}.log")
                    controls.append({"skill": name, "exit_code": result})
                else:
                    controls.append({"skill": name, "status": "not provided"})
            meta["gates_passed"] = scoring_gates and all(c.get("exit_code", 0) == 0 for c in controls)
            meta["controls_complete"] = True
            if not meta["gates_passed"]:
                rc = rc or 1
        write_json(meta_path, meta)
    print(f"\nReport: {report}")
    print(f"Metadata: {meta_path}")
    return rc


def run_evaluation(args) -> int:
    root, paths, cases, names = selection(args)
    profile = args.profile or "core"
    if doctor.display(doctor.check(root, names, cases, profile=profile)):
        return 2
    run_dir = unique_dir(RUNS)
    dataset = run_dir / "cases.json"
    write_json(dataset, cases)
    config = {
        "created_at": datetime.now(timezone.utc).isoformat(), "status": "running",
        "skills_root": str(root), "skills": names, "profile": profile,
        "agent": models.describe("agent"), "judge": models.describe("judge"),
        "datasets": {str(p): digest(p) for p in paths}, "cases_sha256": digest(dataset),
        "lock_sha256": digest(ROOT / "uv.lock"),
    }
    if "bedrock" in (models.agent_provider(), models.judge_provider()):
        config["aws_region"] = models.bedrock_region()
    write_json(run_dir / "run.json", config)
    print(f"\nRun: {run_dir}", flush=True)
    results = run_dir / "results.json"
    code = execute([sys.executable, "-u", "-m", "evalkit.run_eval", "--target", "local",
                    "--cases", str(dataset), "--out", str(results),
                    "--artifacts", str(run_dir / "artifacts")], log=run_dir / "execute.log")
    config["execution_exit_code"] = code
    config["status"] = "recorded" if code in (0, 1) else "execution_failed"
    if results.exists():
        config["results_sha256"] = digest(results)
    write_json(run_dir / "run.json", config)
    try:
        join_recording(cases, str(results))
    except (OSError, ValueError):
        config["status"] = "execution_failed"
        write_json(run_dir / "run.json", config)
        print(f"Incomplete recording; inspect {run_dir / 'execute.log'}", file=sys.stderr)
        return code or 2
    scoring = score_recording(run_dir, args, preflight=False)
    config.update(status="passed" if code == 0 and scoring == 0 else "failed",
                  scoring_exit_code=scoring)
    write_json(run_dir / "run.json", config)
    return code or scoring


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="skill-eval", description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("setup", "doctor", "run"):
        p = sub.add_parser(name)
        p.add_argument("--skill", action="append", help="repeat to select skills; default all")
        p.add_argument("--only", action="append", help="repeat to select case IDs")
        p.add_argument("--profile", choices=["core", "full"], default=None if name == "run" else "core")
        if name == "setup":
            p.add_argument("--with-system-deps", action="store_true",
                           help="let Playwright install Linux browser system packages")
        if name == "doctor":
            p.add_argument("--json", action="store_true")
            p.add_argument("--no-credentials", action="store_true")
    p = sub.add_parser("score")
    p.add_argument("--run", required=True, help="archived run ID or path")
    p.add_argument("--profile", choices=["core", "full"],
                   help="default to the original run profile")
    for name in ("run", "score"):
        p = sub.choices[name]
        p.add_argument("--baseline", default=str(ROOT / "eval-baseline.json"))
        p.add_argument("--no-baseline", action="store_true")
        p.add_argument("--tolerance", type=float, default=0.05)
        p.add_argument("--negative-controls", action="store_true",
                       help="run available calibration controls even with the core profile")
    p = sub.add_parser("check", help="offline repository validation")
    p.add_argument("--strict", action="store_true")
    p = sub.add_parser("baseline")
    b = p.add_subparsers(dest="baseline_command", required=True)
    p = b.add_parser("accept", help="accept a reviewed report without model calls")
    p.add_argument("--report", required=True)
    p.add_argument("--baseline", default=str(ROOT / "eval-baseline.json"))
    return ap


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if hasattr(args, "tolerance") and not 0 <= args.tolerance <= 1:
            raise IntegrityError("--tolerance must be between 0 and 1")
        if args.command == "setup":
            return setup(args)
        if args.command == "doctor":
            root, _, cases, names = selection(args)
            return doctor.display(doctor.check(root, names, cases, profile=args.profile,
                                                credentials=not args.no_credentials), args.json)
        if args.command == "check":
            return execute([sys.executable, "-m", "evalkit.check_repo",
                            "--skills-root", str(skills_root()), *(["--strict"] if args.strict else [])])
        if args.command == "run":
            return run_evaluation(args)
        if args.command == "score":
            return score_recording(locate_run(args.run), args)
        count = accept_baseline(args.report, args.baseline)
        print(f"Accepted {count} case(s) from {args.report}; baseline: {args.baseline}")
        return 0
    except (IntegrityError, OSError, ValueError) as e:
        print(f"skill-eval: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
