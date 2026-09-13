#!/usr/bin/env python3
"""Check selected skills and model configuration before a paid evaluation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from evalkit import models
from evalkit.records import requested_cases, select_datasets, skills_root


def run(cmd: list[str], timeout: int = 30, cwd: Path | None = None) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or p.stderr or "").strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, str(e)


def dist_names(req_path: str | Path) -> list[str]:
    return [re.split(r"[<>=!~\[; ]", line, maxsplit=1)[0]
            for raw in Path(req_path).read_text().splitlines()
            if (line := raw.split("#", 1)[0].strip()) and not line.startswith("-")]


def has_dists(exe: str, names: list[str]) -> dict[str, str | None]:
    if not names:
        return {}
    code = ("import importlib.metadata as m, json, sys\nout = {}\n"
            "for n in sys.argv[1:]:\n"
            "    try: out[n] = m.version(n)\n"
            "    except m.PackageNotFoundError: out[n] = None\n"
            "print(json.dumps(out))")
    rc, text = run([exe, "-c", code, *names])
    try:
        return json.loads(text) if rc == 0 else {n: None for n in names}
    except ValueError:
        return {n: None for n in names}


def command_path(name: str) -> str | None:
    if name == "drawio":
        mac = Path("/Applications/draw.io.app/Contents/MacOS/draw.io")
        if mac.exists():
            return str(mac)
    return shutil.which(name)


def check(root: Path, names: list[str], cases: list[dict], *, profile: str = "core",
          phase: str = "both", credentials: bool = True) -> list[dict]:
    rows = []

    def add(area, item, ok, detail="", required=True):
        rows.append(dict(area=area, item=item, ok=bool(ok), detail=detail, required=required))

    for name, ver in has_dists(sys.executable, [
        "strands-agents", "strands-agents-evals", "boto3", "bedrock-agentcore", "defusedxml",
    ]).items():
        add("python", name, ver, ver or "run uv run skill-eval setup")
    sandbox = shutil.which("python3") or sys.executable
    add("python", "interpreter", True, sys.executable)
    for name in names:
        base = root / name
        req = base / "requirements.txt"
        if req.exists():
            for dist, ver in has_dists(sandbox, dist_names(req)).items():
                add(name, dist, ver, ver or f"run skill-eval setup --skill {name}")
        scripts = base / "scripts"
        pkg = scripts / "package.json"
        if pkg.exists():
            add(name, "node", shutil.which("node"), "Node 20+ required")
            rc, text = run(["npm", "ls", "--depth=0"], cwd=scripts)
            add(name, "npm dependencies", rc == 0, text[:240] if rc else "installed")
            manifest = json.loads(pkg.read_text())
            deps = {**manifest.get("dependencies", {}), **manifest.get("devDependencies", {})}
            if "playwright" in deps:
                rc, text = run(["node", "-e",
                    "require('playwright').chromium.launch({headless:true})"
                    ".then(b=>b.close()).catch(e=>{console.error(e.message);process.exit(1)})"],
                    cwd=scripts)
                add(name, "Chromium launch", rc == 0, text[:240] or "available")
        manifest = base / "eval" / "dependencies.json"
        if manifest.exists():
            deps = json.loads(manifest.read_text())
            commands = set(deps.get("commands", []))
            if profile == "full":
                commands.update(deps.get("visual_commands", []))
            for case in cases:
                commands.update(deps.get("case_commands", {}).get(case["id"], []))
            for command in sorted(commands):
                path = command_path(command)
                add(name, command, path, path or f"install system command: {command}")
    roles = ["judge"] if phase == "score" else ["agent", "judge"]
    providers = {}
    for role in roles:
        config = models.describe(role)
        providers[role] = config["model_provider"]
        try:
            models.validate_config(role)
            if config["model_provider"] == "openai":
                version = has_dists(sys.executable, ["openai"]).get("openai")
                add(role, "OpenAI SDK", version, version or "run skill-eval setup after configuring providers")
            add(role, "model configuration", True, f"{config['model_provider']} / {config['model_id']}")
        except Exception as e:
            add(role, "model configuration", False, f"{type(e).__name__}: {e}")
    if credentials and "bedrock" in providers.values():
        try:
            import boto3
            from botocore.config import Config
            boto3.client("sts", region_name=models.bedrock_region(),
                         config=Config(connect_timeout=5, read_timeout=5,
                                       retries={"max_attempts": 0})).get_caller_identity()
            add("aws", "credentials", True, "STS identity resolved; model access is checked during inference")
        except Exception as e:
            add("aws", "credentials", False, f"{type(e).__name__}: {e}")
    elif not credentials:
        add("models", "remote credentials", True, "not checked (--no-credentials)", required=False)
    return rows


def display(rows: list[dict], as_json: bool = False) -> int:
    if as_json:
        print(json.dumps(rows, indent=2))
    else:
        for r in rows:
            print(f"  {'OK' if r['ok'] else 'MISSING'} {r['area']}: {r['item']} — {r['detail']}")
    missing = sum(not r["ok"] and r["required"] for r in rows)
    print(f"doctor: {len(rows)} checks, {missing} required item(s) missing",
          file=sys.stderr if as_json else sys.stdout)
    return int(missing > 0)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skill", action="append")
    ap.add_argument("--only", action="append")
    ap.add_argument("--profile", choices=["core", "full"], default="core")
    ap.add_argument("--phase", choices=["both", "score"], default="both")
    ap.add_argument("--no-credentials", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    root = skills_root()
    paths = select_datasets(root, args.skill)
    cases = requested_cases(paths, args.only)
    wanted = {c["id"] for c in cases}
    names = sorted({Path(p).parent.parent.name for p in paths
                    if wanted.intersection(c["id"] for c in requested_cases([p]))})
    return display(check(root, names, cases, profile=args.profile, phase=args.phase,
                         credentials=not args.no_credentials), args.json)


if __name__ == "__main__":
    raise SystemExit(main())
