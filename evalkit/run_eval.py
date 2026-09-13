#!/usr/bin/env python3
"""
Eval runner for the skill sandbox.

    # local: import the sandbox, run in-process (fastest iteration)
    python run_eval.py --target local

    # deployed: invoke the AgentCore Runtime
    python run_eval.py --target runtime --arn arn:aws:bedrock-agentcore:us-east-1:...:runtime/...

This phase runs the skill and records what happened; the deterministic graders in
`graders.py` score what can be checked by arithmetic. Model judging is phase 2's
job -- see `run_strands_eval.py`.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import uuid

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _HERE)
sys.path.insert(0, ROOT)

from evalkit import graders  # noqa: E402
from evalkit.plugins import skill_datasets  # noqa: E402
from evalkit.records import MANUAL, IntegrityError, requested_cases, skills_root, write_json  # noqa: E402

# Bedrock streaming occasionally drops a response mid-flight
# ("Response ended prematurely", throttling). That is infrastructure noise, and
# scoring it would report a transient network fault as a skill defect, so retry
# before letting the failure through. Genuine agent failures -- tool budget
# exceeded, a skill that never completes -- do not match and are graded as-is.
TRANSIENT = ("ended prematurely", "ThrottlingException", "ServiceUnavailable",
             "ModelStreamErrorException", "timed out", "Read timeout")
MAX_ATTEMPTS = 3


def invoke_with_retry(invoke, payload: dict) -> dict:
    resp: dict = {}
    for attempt in range(1, MAX_ATTEMPTS + 1):
        resp = invoke(payload)
        err = resp.get("error") or ""
        if resp.get("status") == "ok" or not any(t in err for t in TRANSIENT):
            return resp
        print(f"  transient failure (attempt {attempt}/{MAX_ATTEMPTS}): {err[:80]}", flush=True)
        time.sleep(2 * attempt)
    return resp


def save_artifacts(case_id: str, resp: dict, root: str) -> dict[str, str]:
    """Persist what the agent produced, and return `rel -> saved path`.

    The sandbox works in a temp directory that is gone by the time anything
    downstream runs, and `results.json` keeps only each file's size and hash. The
    visual layer needs the bytes: it renders the artifact and shows the picture
    to a judge, which cannot be done from a sha256. Writing them here also makes
    a failed case inspectable by hand afterwards, which the temp workspace never
    allowed.

    The case directory is cleared first. Without that, files from earlier runs
    survive beside the current ones with nothing to tell them apart, and reading
    `artifacts/` gives you a mix of runs presented as one. That is not a
    hypothetical: `artifacts/coinbase-low-latency/coinbase-low-latency.drawio`
    outlived the run that made it and still carried a stencil bug that had
    already been fixed, which is a stale-data trap for anyone auditing by hand.
    The deterministic graders were never fooled — they read `resp["files"]`, not
    the disk — so this is about the copy being an honest record of one run.

    Two sources, and `local_path` wins. The sandbox copies verbatim anything the
    recording could not inline, so a PNG or a GIF lands here as itself. Falling back
    to `text` for those produced a file that was not an image and said otherwise: a
    293 KB PNG became 471 KB of replacement characters, and a truncated GIF kept its
    `GIF89a` magic so `file(1)` vouched for it. `text` remains the right source for
    everything that is genuinely text.
    """
    case_root = os.path.join(root, case_id)
    shutil.rmtree(case_root, ignore_errors=True)

    saved: dict[str, str] = {}
    for rel, meta in (resp.get("files") or {}).items():
        src = meta.get("local_path")
        text = meta.get("text")
        if src is None and text is None:
            continue
        dest = os.path.join(root, case_id, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if src and os.path.isfile(src):
            shutil.copy2(src, dest)
        elif text is None:
            # Bytes the recording dropped and the sandbox could not hand over: a
            # remote target, or the copy failed. Better no file than a wrong one.
            continue
        else:
            with open(dest, "w", encoding="utf-8") as f:
                f.write(text)
        saved[rel] = dest

    # The sandbox's staging directory has served its purpose once the copies are in
    # `artifacts/`. Left alone on the runtime target, where it is not ours.
    media_dir = resp.get("media_dir")
    if media_dir and os.path.isdir(media_dir):
        shutil.rmtree(media_dir, ignore_errors=True)
    return saved


#: How much of an agent's reply is kept in `results.json`. Generous, because one
#: case's whole point is whether the agent pasted a ~13 KB XML file into its reply.
OUTPUT_HEAD_CAP = 60_000


def head(output: str) -> str:
    """The agent's reply, capped, and *saying so* when it was capped.

    The cap used to be 1500 characters with no marker, which is enough for five of
    the six replies and cuts the sixth mid-attribute. `adversarial-inline-xml` asks
    for the XML inline; the agent pasted all 12,637 characters of it; the judge read
    a reply ending `...whiteSpace=wrap;html=1;f` and scored the step partial for
    "truncated/incomplete" output. It had no way to tell harness truncation from
    agent truncation, because nothing in the record distinguished them.

    So: a cap large enough that a pasted diagram fits, and an explicit marker when
    it does not, which is the part that matters. A silently-truncated record does
    not just lose information, it manufactures a defect in whatever it truncates.
    """
    if len(output) <= OUTPUT_HEAD_CAP:
        return output
    return (output[:OUTPUT_HEAD_CAP] +
            f"\n\n[harness: reply truncated here at {OUTPUT_HEAD_CAP} characters; "
            f"the agent's full reply was {len(output)} characters. The cut is this "
            f"harness's, not the agent's.]")


def invoke_local(payload: dict) -> dict:
    from evalkit import sandbox

    return sandbox.run_case(payload)


def invoke_runtime(payload: dict, arn: str, region: str) -> dict:
    import boto3

    c = boto3.client("bedrock-agentcore", region_name=region)
    r = c.invoke_agent_runtime(
        agentRuntimeArn=arn,
        runtimeSessionId=uuid.uuid4().hex,
        payload=json.dumps(payload).encode(),
        contentType="application/json",
        accept="application/json",
    )
    body = r["response"].read()
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"status": "error", "error": f"non-JSON response: {body[:500]!r}", "output": ""}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", choices=["local", "runtime"], default="local")
    ap.add_argument("--arn", help="AgentCore Runtime ARN (required for --target runtime)")
    ap.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    ap.add_argument("--cases", nargs="*", default=None,
                    help="dataset paths; default = every skills/*/eval/dataset.jsonl")
    ap.add_argument("--only", nargs="*", help="case ids to run")
    ap.add_argument("--out", default=str(MANUAL / "results.json"))
    ap.add_argument("--artifacts", default=str(MANUAL / "artifacts"),
                    help="where to persist the files each case produced")
    args = ap.parse_args()

    if args.target == "runtime" and not args.arn:
        ap.error("--target runtime requires --arn")

    # Default: every skill that declares cases. Evaluating "the repo" means
    # evaluating all of them; a skill with no dataset is simply absent from the
    # report, which is a visible gap rather than a silent pass.
    datasets = args.cases or skill_datasets(str(skills_root()))
    if not datasets:
        print(f"no datasets found under {skills_root()}/*/eval/dataset.jsonl", file=sys.stderr)
        return 2
    # Loaded per dataset so a duplicate id can name the files that collide.
    # Checked before any agent runs: the failure mode is silent data loss
    # downstream, and paying for four Bedrock runs first to then discover the
    # suite cannot record them is the worst possible ordering.
    try:
        cases = requested_cases(datasets, args.only, with_seeds=True)
    except IntegrityError as e:
        print(str(e), file=sys.stderr)
        return 2
    if not cases:
        print("no cases matched", file=sys.stderr)
        return 2

    results = []
    write_json(args.out, results)
    for case in cases:
        print(f"\n=== {case['id']} ===", flush=True)
        payload = {"prompt": case["prompt"]}
        if case.get("seed_files"):
            payload["seed_files"] = case["seed_files"]

        started = time.monotonic()
        resp = invoke_with_retry(
            invoke_local
            if args.target == "local"
            else (lambda p: invoke_runtime(p, args.arn, args.region)),
            payload,
        )

        artifacts = save_artifacts(case["id"], resp, args.artifacts)

        report = graders.grade(resp, case.get("expect", {}))
        for g in report["grades"]:
            print(f"  {'PASS' if g['passed'] else 'FAIL'}  {g['name']:<44} {g['detail'][:90]}")
        print(f"  -> deterministic {report['passed']}/{report['total']}  ({report['score']:.0%})")

        results.append(
            {
                "case": case["id"],
                "prompt": case["prompt"],
                "deterministic": report,
                "skills_loaded": resp.get("skills_loaded"),
                "tool_names": resp.get("trace", {}).get("tool_names"),
                "tool_calls": resp.get("trace", {}).get("tool_calls"),
                "stop_reason": resp.get("trace", {}).get("stop_reason"),
                "tool_budget": resp.get("tool_budget"),
                "status": resp.get("status"),
                "error": resp.get("error"),
                "files": {k: {**{kk: vv for kk, vv in v.items() if kk != "text"},
                              "saved_to": artifacts.get(k)}
                          for k, v in (resp.get("files") or {}).items()},
                "system_prompt": resp.get("system_prompt"),
                "output_head": head(resp.get("output") or ""),
                "usage": resp.get("usage"),
                "model_provider": resp.get("model_provider"),
                "model_id": resp.get("model_id"),
                "elapsed_seconds": round(time.monotonic() - started, 3),
                # What `python3` meant during execution, useful when re-scoring
                # later from a different interpreter.
                "toolchain": resp.get("toolchain"),
            }
        )
        # Preserve completed cases if a later invocation fails or is interrupted.
        write_json(args.out, results)

    n_all = sum(1 for r in results if r["deterministic"]["all_passed"])
    micro = sum(r["deterministic"]["passed"] for r in results)
    micro_total = sum(r["deterministic"]["total"] for r in results)
    print("\n" + "=" * 72)
    print(f"cases fully passing: {n_all}/{len(results)}")
    print(f"checks passing:      {micro}/{micro_total} ({micro / micro_total:.1%})")
    for r in results:
        d = r["deterministic"]
        print(f"  {'✓' if d['all_passed'] else '✗'} {r['case']:<24} {d['passed']}/{d['total']}"
              f"   skill={r['skills_loaded']}")

    print(f"\nwrote {args.out}")
    return 0 if n_all == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
