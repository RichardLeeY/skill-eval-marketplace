"""
Generic skill sandbox for AgentCore Runtime.

Unlike a skill-specific agent, this runtime hosts *no* domain tools. It gives the
model only Claude-Code-shaped primitives:

    Skill(skill)       -> returns that skill's SKILL.md
    Read(path)
    Write(path, content)
    LS(path)
    Bash(command)

Skills live in a read-only registry; their SKILL.md is NOT injected into the
system prompt. Only a catalog of (name, description) is, exactly like Claude
Code's available-skills listing. The model must therefore *choose* a skill and
*then* follow it, which is what makes these two evaluators meaningful:

    SkillSelectionAccuracy     did it pick the right skill?
    SkillInstructionFollowing  did it follow what SKILL.md prescribed?

What is being graded here is the skill's own instructions, not Python glue.
"""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
# html.escape covers the same &<>"' that the old xml.sax.saxutils.escape did,
# without importing the xml package (which the SAST scanner flags for XXE even
# though escaping output cannot be an XXE sink).
from html import escape

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent, tool
from strands.hooks import HookProvider, HookRegistry
from strands.hooks.events import BeforeModelCallEvent

_HERE = os.path.dirname(os.path.abspath(__file__))

# Sibling module, imported package-qualified for the same reason `run_strands_eval`
# does it: a bare `import models` would be a second module object, and the provider
# constants would then disagree between the two phases.
if __package__:
    from evalkit import models as model_config
else:  # standalone execution (the module shipped on its own to the runtime)
    import models as model_config  # type: ignore[no-redef]

#: Skills live at the repo root, one directory per skill; this file lives in
#: `evalkit/`. Overridable so a single kit checkout can score another repo's skills.
_ROOT = os.path.dirname(_HERE)
SKILLS_DIR = os.path.realpath(os.environ.get("SKILLS_DIR", os.path.join(_ROOT, "skills")))

# No model constants here: provider, id and region all come from `models.py`
# (`MODEL_PROVIDER`, `MODEL_ID`, `BEDROCK_MODEL_ID`, `AWS_REGION`), read per run so
# a change does not need a restart of a long-lived runtime.

BASH_TIMEOUT = int(os.environ.get("BASH_TIMEOUT", "120"))
MAX_TOOL_CALLS = int(os.environ.get("MAX_TOOL_CALLS", "40"))
MAX_ARTIFACT_BYTES = int(os.environ.get("MAX_ARTIFACT_BYTES", str(256 * 1024)))

SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv"}

#: Never copied into the workspace, and never named to the agent. `eval/` holds
#: the case dataset -- expected values, assertions, negative controls -- and a
#: skill's own directory is otherwise readable, with its file list handed over in
#: the Skill tool's result. Measured before this existed: every run was told
#: `eval/dataset.jsonl, eval/plugin.py, eval/rubrics.py` and, for the diagram
#: skill, `eval/reference/s3-lambda-dynamodb.spec.json` -- the answer to one of its
#: own cases. No recorded run had read any of it, so no score was contaminated, but
#: that was the agent's restraint rather than the harness's doing.
#:
#: Kept out of SKIP_DIRS deliberately: those get symlinked back in below, which is
#: right for a pre-installed dependency tree and exactly wrong here.
PRIVATE_DIRS = {"eval"}


# --------------------------------------------------------------------------- #
# Per-invocation state
# --------------------------------------------------------------------------- #
class Run:
    """Mutable state for one invocation. Reset by invoke()."""

    def __init__(self) -> None:
        self.workspace = ""
        #: Where verbatim copies of artifacts the recording cannot inline are kept.
        #: Outside the workspace, which is deleted as soon as collection returns.
        #: Created on first use, so a run that produced no media makes no directory.
        self.media_dir = ""
        self.calls: list[dict] = []
        # Counted separately from `calls`: a call is charged on entry and logged
        # on exit, so an aborted call must consume budget without appearing as a
        # completed step in the trajectory.
        self.n_started = 0
        self.budget_error: str | None = None
        self.skills_loaded: list[str] = []
        self.skill_roots: dict[str, str] = {}
        # relpath -> sha256 of every file copied in from the registry, so
        # artifact collection can tell "agent produced this" from "this is
        # just the skill's own source".
        self.baseline: dict[str, str] = {}


RUN = Run()


class ToolBudgetExceeded(Exception):
    """Reject this tool and let Strands produce its required error toolResult.

    A BaseException is swallowed by the concurrent tool executor, leaving an
    unmatched toolUse in the conversation. The latched error and ToolBudgetGuard
    stop inference separately, after all tool results for this turn are recorded.
    """


def _begin(name: str) -> None:
    """Reserve a slot in the tool budget, before the tool does anything.

    Charged on entry rather than in `_record`, which runs after the work is done:
    a budget checked afterwards still lets the over-limit call write its file or
    run its command, so the cap would bound the *log* rather than the side
    effects.
    """
    RUN.n_started += 1
    if RUN.n_started > MAX_TOOL_CALLS:
        if RUN.budget_error is None:
            RUN.budget_error = (
                f"exceeded MAX_TOOL_CALLS={MAX_TOOL_CALLS} on call {RUN.n_started} ({name})"
            )
        raise ToolBudgetExceeded(RUN.budget_error)


class ToolBudgetGuard(HookProvider):
    """Stop before another model request once a tool has exhausted the budget."""

    def register_hooks(self, registry: HookRegistry, **kwargs) -> None:
        registry.add_callback(BeforeModelCallEvent, self.before_model)

    def before_model(self, event: BeforeModelCallEvent) -> None:
        if RUN.budget_error is not None:
            event.cancel = RUN.budget_error


def _record(name: str, args: dict, started: float, ok: bool, result, **extra) -> None:
    head = result if isinstance(result, str) else json.dumps(result, default=str)
    RUN.calls.append(
        {
            **extra,
            "name": name,
            "args": {k: (v[:400] + "…" if isinstance(v, str) and len(v) > 400 else v) for k, v in args.items()},
            "ok": ok,
            "duration_ms": round((time.time() - started) * 1000, 1),
            "result_head": head[:600],
        }
    )


def _attach_said_before(messages: list) -> None:
    """Copy the prose the agent wrote ahead of each tool call onto that call's record.

    `RUN.calls` is written by the tool wrappers, so it knows what was called and
    what came back, and nothing about what the agent *said* in between. That text
    is where a skill's narrated steps live -- `visual-flow-webp` asks for a
    `Reading:` block before the first Write, and the instruction-following judge
    was marking every run as having skipped it, because the trajectory it read
    was tool calls and results only. The judge was right about what it saw and
    wrong about what happened.

    Walks the agent's own message list: each assistant turn is text blocks and
    toolUse blocks in order, and the text is attached to the *first* toolUse of
    that turn, in call order, as `said_before`. Matched by position, so a turn
    with two tool calls puts its text on the first one and none on the second,
    which is where it stood in the conversation. Stops at whichever runs out
    first -- a run cut off by the tool budget has more toolUse blocks than
    recorded calls, and guessing at the tail would misfile text.
    """
    idx = 0
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        pending: list[str] = []
        for block in msg.get("content") or []:
            if not isinstance(block, dict):
                continue
            if "text" in block and block["text"]:
                pending.append(str(block["text"]))
            elif "toolUse" in block:
                if idx >= len(RUN.calls):
                    return
                if pending:
                    RUN.calls[idx]["said_before"] = "\n".join(pending).strip()
                    pending = []
                idx += 1


def _resolve(path: str, *, for_write: bool) -> str:
    """Contain paths to the workspace (reads may also reach the skill registry)."""
    base = RUN.workspace
    p = os.path.realpath(path if os.path.isabs(path) else os.path.join(base, path))
    if p == base or p.startswith(base + os.sep):
        return p
    if not for_write and (p == SKILLS_DIR or p.startswith(SKILLS_DIR + os.sep)):
        # The registry copy is reachable by absolute path, so excluding PRIVATE_DIRS
        # from the workspace copy is only half a fence without this.
        if any(part in PRIVATE_DIRS for part in p[len(SKILLS_DIR):].split(os.sep)):
            raise ValueError(f"path is not readable by a skill under test: {path}")
        return p
    raise ValueError(f"path escapes the sandbox: {path}")


# --------------------------------------------------------------------------- #
# Skill registry
# --------------------------------------------------------------------------- #
def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _frontmatter(text: str) -> dict:
    """Parse the leading `---` block. Flat key: value only, which is all a
    SKILL.md frontmatter needs."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    out, key = {}, None
    for line in text[3:end].splitlines():
        if not line.strip():
            continue
        if line[0] not in " \t" and ":" in line:
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip()
            # `>`, `>-`, `|`, `|-` start a block scalar: the value is on the
            # following indented lines, not on this one.
            out[key] = "" if val in (">", ">-", ">+", "|", "|-", "|+") else val
        elif key:  # continuation of a folded/multi-line value
            out[key] = (out[key] + " " + line.strip()).strip()
    return out


def discover_skills() -> dict[str, dict]:
    """name -> {root, description, sha256}"""
    found: dict[str, dict] = {}
    if not os.path.isdir(SKILLS_DIR):
        return found
    for entry in sorted(os.listdir(SKILLS_DIR)):
        root = os.path.join(SKILLS_DIR, entry)
        md = os.path.join(root, "SKILL.md")
        if not os.path.isfile(md):
            continue
        text = _read(md)
        fm = _frontmatter(text)
        name = fm.get("name") or entry
        found[name] = {
            "root": root,
            "description": fm.get("description", ""),
            "sha256": hashlib.sha256(text.encode()).hexdigest()[:16],
        }
    return found


SKILLS = discover_skills()


def _sha(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _materialize_skill(name: str, registry_root: str) -> str:
    """Copy a skill from the read-only registry into the workspace.

    A skill's instructions use paths relative to its own root, so if the skill
    stays outside the workspace its outputs land outside too and never get
    collected. Copying it in keeps everything contained; pre-installed
    dependency trees are symlinked back rather than copied so this stays cheap.
    """
    dest = os.path.join(RUN.workspace, "skill", name)
    if os.path.isdir(dest):
        return dest

    shutil.copytree(registry_root, dest,
                    ignore=shutil.ignore_patterns(*SKIP_DIRS, *PRIVATE_DIRS))

    for dirpath, dirnames, _ in os.walk(registry_root):
        dirnames[:] = [d for d in dirnames if d not in PRIVATE_DIRS]
        for d in list(dirnames):
            if d in SKIP_DIRS:
                dirnames.remove(d)
                rel = os.path.relpath(os.path.join(dirpath, d), registry_root)
                link = os.path.join(dest, rel)
                if not os.path.exists(link):
                    os.makedirs(os.path.dirname(link), exist_ok=True)
                    os.symlink(os.path.join(registry_root, rel), link)

    for dirpath, dirnames, filenames in os.walk(dest):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            if not os.path.islink(full):
                RUN.baseline[os.path.relpath(full, RUN.workspace)] = _sha(full)
    return dest


def _catalog_block() -> str:
    """The advertised skill catalog, as an `<available_skills>` block.

    The tag names are a wire contract, like the `Skill`/`skill` tool naming
    below. Evaluation harnesses recover "what could the agent have chosen from"
    by regex over the system prompt, and `<available_skills><skill><name>` is
    the shape they look for. A plain `- name: description` list parses as prose,
    the catalog comes back empty, and a selection judge then scores the choice
    with no idea what the alternatives were.
    """
    if not SKILLS:
        return "<available_skills>\n</available_skills>"
    entries = "\n".join(
        f"  <skill>\n    <name>{n}</name>\n"
        f"    <description>{escape(m['description'])}</description>\n  </skill>"
        for n, m in SKILLS.items()
    )
    return f"<available_skills>\n{entries}\n</available_skills>"


SYSTEM_PROMPT = f"""You are an agent working inside a sandbox with a writable workspace.

Available skills — a skill is a packaged set of instructions for a particular
kind of task. If one covers the task at hand, call `Skill` with its exact
name FIRST, then follow its instructions instead of your default approach.
Do not guess names not on this list.

{{catalog}}

Rules:
- Call `Skill` before doing any work a skill covers. Load exactly one. If two
  look applicable, pick the better fit and follow it; do not load both.
- Once loaded, follow the skill's instructions literally, including which
  scripts to run and which files to produce. Do not substitute your own method
  for one the skill prescribes.
- If the skill points at reference files, read them with `Read` before
  relying on them.
- Write all output files into the workspace (relative paths). The workspace is
  your current directory for `Bash`.
- When done, state what you produced and whether the skill's own validation
  passed.
""".replace("{catalog}", _catalog_block())


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
@tool
def Skill(skill: str) -> str:  # noqa: N802
    """Load a skill's instructions. Call this before doing work the skill covers.

    Args:
        skill: Exact skill name from the available-skills list.

    Returns:
        The skill's SKILL.md contents, plus the path of its root directory
        (the skill's own relative paths are relative to that root).
    """
    # The capitalised name and the `skill` parameter are part of AgentCore's
    # contract, not a style choice: its skill evaluators recognise a skill
    # invocation only from a tool span named exactly `Skill` whose input JSON
    # carries a `skill` key. Rename either and the skill scores silently vanish.
    _begin("Skill")
    started = time.time()
    name = skill
    meta = SKILLS.get(name)
    if not meta:
        msg = f"unknown skill '{name}'. Available: {', '.join(SKILLS) or '(none)'}"
        _record("Skill", {"skill": name}, started, False, msg)
        return msg

    dest = _materialize_skill(name, meta["root"])
    body = _read(os.path.join(dest, "SKILL.md"))
    rel_root = os.path.relpath(dest, RUN.workspace)
    refs = sorted(
        os.path.relpath(r, rel_root)
        for r in RUN.baseline
        if r.startswith(rel_root + os.sep) and not r.endswith("SKILL.md")
    )

    if name not in RUN.skills_loaded:
        RUN.skills_loaded.append(name)
    RUN.skill_roots[name] = dest

    out = (
        f"Skill '{name}' loaded and copied into your workspace.\n"
        f"Skill root: {rel_root}/  -- every relative path the skill mentions is "
        f"relative to THIS directory.\n"
        f"Its dependencies are already installed; do not run npm install.\n"
        f"Write your own output files to the workspace root (e.g. `diagram.drawio`), "
        f"not inside {rel_root}/.\n"
        f"Files in the skill (read with Read if the instructions say to): "
        f"{', '.join(refs[:40]) or '(none)'}\n\n"
        f"--- SKILL.md ---\n{body}"
    )
    _record("Skill", {"skill": name}, started, True,
            f"loaded {name} ({len(body)} chars)", skill_content=out)
    return out


@tool
def Read(path: str) -> str:
    """Read a UTF-8 text file from the workspace or a loaded skill's directory.

    Args:
        path: Relative to the workspace, or an absolute path inside the skill registry.
    """
    _begin("Read")
    started = time.time()
    try:
        p = _resolve(path, for_write=False)
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            data = f.read()
    except (OSError, ValueError) as e:
        _record("Read", {"path": path}, started, False, str(e))
        return f"ERROR: {e}"
    _record("Read", {"path": path}, started, True, f"{len(data)} chars")
    return data


@tool
def Write(path: str, content: str) -> str:
    """Write a UTF-8 text file into the workspace, creating parent directories.

    Args:
        path: Path relative to the workspace.
        content: Full file contents.
    """
    _begin("Write")
    started = time.time()
    try:
        p = _resolve(path, for_write=True)
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
    except (OSError, ValueError) as e:
        _record("Write", {"path": path}, started, False, str(e))
        return f"ERROR: {e}"
    msg = f"wrote {path} ({len(content)} bytes)"
    _record("Write", {"path": path, "content": content}, started, True, msg)
    return msg


@tool
def LS(path: str = ".") -> str:
    """List files under a directory in the workspace or the skill registry.

    Args:
        path: Directory to list. Defaults to the workspace root.
    """
    _begin("LS")
    started = time.time()
    try:
        base = _resolve(path, for_write=False)
    except ValueError as e:
        _record("LS", {"path": path}, started, False, str(e))
        return f"ERROR: {e}"
    rows = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            rows.append(f"{os.path.relpath(full, base)}\t{os.path.getsize(full)}")
        if len(rows) > 400:
            rows.append("… (truncated)")
            break
    out = "\n".join(rows) or "(empty)"
    _record("LS", {"path": path}, started, True, f"{len(rows)} entries")
    return out


@tool
def Bash(command: str) -> str:
    """Run a shell command with the workspace as the current directory.

    Args:
        command: Shell command. Use this to run a skill's scripts.

    Returns:
        Combined exit code, stdout and stderr.
    """
    _begin("Bash")
    started = time.time()
    try:
        # The skill under test drives its scripts through shell one-liners in its
        # SKILL.md (pipes, redirects, `cd`), so a real shell is required. Invoking
        # bash explicitly with an argv list instead of shell=True keeps that
        # behaviour while dropping the parent shell's settings/vars propagation,
        # which is what the shell=True finding is about. The command is the agent's
        # own input into its own throwaway sandbox, not an external actor's.
        r = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
            ["/bin/bash", "-c", command],
            cwd=RUN.workspace,
            capture_output=True,
            text=True,
            timeout=BASH_TIMEOUT,
        )
        out = f"exit={r.returncode}\n--- stdout ---\n{r.stdout[-8000:]}\n--- stderr ---\n{r.stderr[-4000:]}"
        ok = r.returncode == 0
    except subprocess.TimeoutExpired:
        out, ok = f"ERROR: timed out after {BASH_TIMEOUT}s", False
    _record("Bash", {"command": command}, started, ok, out)
    return out


TOOLS = [Skill, Read, Write, LS, Bash]


# --------------------------------------------------------------------------- #
# Artifact collection
# --------------------------------------------------------------------------- #
def _keep_bytes(rel: str, full: str, entry: dict) -> None:
    """Copy a file whose bytes the recording cannot carry, and record where.

    Into a directory of its own rather than the workspace, because the workspace is
    deleted the moment collection returns. The caller (`run_eval.save_artifacts`)
    moves these into `artifacts/<case>/` and removes the directory, so nothing is
    left behind on a normal run; a crash between the two leaves a `skillbox-media-`
    temp dir, which is the right failure -- the evidence survives.

    Silent on failure by design: a media copy is an improvement to the record, and
    a full disk should not turn a scored run into an error.
    """
    try:
        if not RUN.media_dir:
            RUN.media_dir = tempfile.mkdtemp(prefix="skillbox-media-")
        dest = os.path.join(RUN.media_dir, rel)
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        shutil.copy2(full, dest)
        entry["local_path"] = dest
    except OSError:
        pass


def collect_artifacts(ws: str) -> dict:
    """Everything the agent produced, hashed and (if text and small) inlined.
    This is what deterministic graders and custom evaluators score.

    Files copied in from the skill registry are skipped unless the agent changed
    them, so the result is the agent's output rather than the skill's source.

    `MAX_ARTIFACT_BYTES` bounds `text` because the response is a JSON payload, not
    a directory: on `--target runtime` it comes back over the wire from AgentCore.
    That bound has no business reaching the *file*, though, and it used to. A PNG
    over the cap was inlined as `raw[:cap].decode(errors="replace")` and written
    back out as UTF-8, so the copy on disk was 106,772 replacement characters and
    not an image; one under the cap failed `decode()`, got `binary: True` and no
    `text` at all, so no file appeared. Small pictures vanished silently, large ones
    left a corrupt file that `file(1)` still called a GIF.

    So anything whose bytes do not survive inlining is *copied* verbatim, while it
    still exists, and the entry carries `local_path`. Only the local target gets
    this: over the wire there is no shared disk, and the entry says so by omitting
    the key rather than by pretending.
    """
    files: dict[str, dict] = {}
    for dirpath, dirnames, filenames in os.walk(ws):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            if os.path.islink(full):
                continue
            rel = os.path.relpath(full, ws)
            try:
                raw = open(full, "rb").read()
            except OSError:
                continue
            if RUN.baseline.get(rel) == hashlib.sha256(raw).hexdigest():
                continue  # unchanged skill source
            entry = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()[:16]}
            if len(raw) <= MAX_ARTIFACT_BYTES:
                try:
                    entry["text"] = raw.decode("utf-8")
                except UnicodeDecodeError:
                    entry["binary"] = True
            else:
                entry["truncated"] = True
                entry["text"] = raw[:MAX_ARTIFACT_BYTES].decode("utf-8", errors="replace")
            if "text" not in entry or entry.get("truncated"):
                _keep_bytes(rel, full, entry)
            files[rel] = entry
            if len(files) >= 100:
                return files
    return files


# --------------------------------------------------------------------------- #
# Entrypoint
# --------------------------------------------------------------------------- #
app = BedrockAgentCoreApp()


def run_case(payload: dict) -> dict:
    """Run one prompt in a fresh workspace. Importable for local eval runs."""
    prompt = (payload or {}).get("prompt", "").strip()
    if not prompt:
        return {"status": "error", "error": "missing 'prompt' in payload", "output": ""}

    ws = tempfile.mkdtemp(prefix="skillbox-")
    RUN.__init__()
    RUN.workspace = os.path.realpath(ws)

    seed = (payload or {}).get("seed_files") or {}
    for rel, content in seed.items():
        p = _resolve(rel, for_write=True)
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)

    # Outside the try, unlike everything below it. A provider misconfiguration is
    # not a skill failure, and recording it as one would put an identical
    # `status: "error"` on every case in the suite — the shape of a broken skill,
    # for a wrong environment variable. Let it raise and stop the run instead.
    model = model_config.build_model("agent")

    status, error, output, stop_reason, usage = "ok", None, "", None, {}
    try:
        agent = Agent(
            model=model,
            tools=TOOLS,
            hooks=[ToolBudgetGuard()],
            system_prompt=SYSTEM_PROMPT,
            # Strands otherwise scans ./tools/*.py and hands whatever it finds to
            # the agent, on top of TOOLS. That directory holds the *evaluator's*
            # instruments: `tools/style_probe.py` renders a spec and scores it
            # against evals/reference/aws-official-style.png. An agent under test
            # must not be able to call the thing scoring it. It only ever failed
            # to load — "No module named 'strands_evals'", once per case — because
            # the sandbox interpreter lacks the evals package, which is an
            # accident of the two-interpreter split, not a safeguard.
            load_tools_from_directory=False,
        )
        result = agent(prompt)
        output = str(result)
        stop_reason = getattr(result, "stop_reason", None)
        _attach_said_before(getattr(agent, "messages", None) or [])
        metrics = getattr(result, "metrics", None)
        if metrics is not None:
            usage = json.loads(json.dumps(getattr(metrics, "accumulated_usage", {}), default=str))
    except ToolBudgetExceeded as e:
        status, error = "error", str(e)
    except Exception as e:  # surface the failure as data; an eval run must not crash
        status, error = "error", f"{type(e).__name__}: {e}"

    # Hook cancellation produces a normal AgentResult; it is still an execution
    # failure for the evaluator, with the original budget error as its cause.
    if RUN.budget_error is not None:
        status, error, stop_reason = "error", RUN.budget_error, "tool_budget_exceeded"

    artifacts = collect_artifacts(RUN.workspace)
    shutil.rmtree(RUN.workspace, ignore_errors=True)

    # Returned so the caller can move the verbatim copies somewhere durable and
    # then delete this directory. Absent on the runtime target, where the paths
    # inside it mean nothing to the caller anyway.
    media_dir = RUN.media_dir

    return {
        "media_dir": media_dir or None,
        "status": status,
        "error": error,
        "output": output,
        "skills_loaded": list(RUN.skills_loaded),
        "skills_available": sorted(SKILLS),
        "trace": {
            "tool_names": [c["name"] for c in RUN.calls],
            "tool_calls": RUN.calls,
            "n_tool_calls": len(RUN.calls),
            "stop_reason": stop_reason,
        },
        "tool_budget": {
            "limit": MAX_TOOL_CALLS,
            "attempted": RUN.n_started,
            "completed": len(RUN.calls),
            "exhausted": RUN.budget_error is not None,
        },
        "files": artifacts,
        "usage": usage,
        # Record the original toolchain so a later re-score can locate it even
        # when launched from a different environment. Missing icon dependencies
        # during reconstruction must not be blamed on the original delivery.
        "toolchain": {name: shutil.which(name) for name in ("python3", "node")},
        # `model_provider` alongside the id: once more than one provider can serve
        # a name, the id alone no longer identifies what produced the run, and
        # phase 2 re-scores this recording long after the environment is gone.
        **model_config.describe("agent"),
        # Returned because the skill catalog lives here and nowhere else in the
        # response. A selection judge scores the chosen skill against the set the
        # agent could have chosen from, so without the prompt it is left rating a
        # decision whose alternatives it cannot see.
        "system_prompt": SYSTEM_PROMPT,
    }


@app.entrypoint
def invoke(payload: dict) -> dict:
    return run_case(payload)


if __name__ == "__main__":
    app.run()
