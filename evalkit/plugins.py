"""The author-owned evaluation layer, and how the kit finds it.

Two layers score a skill, and the split is the reason this kit can be shared:

**Layer 1 — the kit owns it, the author configures nothing.** Did the agent pick
this skill when it should have (`SkillSelectionAccuracy`)? Did it follow the
skill's own instructions (`SkillInstructionFollowing`, which reads `SKILL.md`
itself as the rubric)? Did it call the tools the case expects
(`ToolTrajectory`, `SkillInvoked`)? Did the artifact satisfy the declared
`expect.*` checks (`graders`), and does every claim in `expect.assertions` hold
of it (`AssertionsEvaluator`)? None of that knows what the skill produces, which
is why the same four judges ran unchanged across a diagram generator, a Mermaid
flowchart skill, a cost-table skill and a meeting-minutes skill.

**Layer 2 — the author owns it.** Whether a diagram's boxes are nested in the
right container, whether a cost table's arithmetic adds up, whether minutes lead
with the decision: the kit cannot know, and must not pretend to. A layout
evaluator handed a markdown table is noise. So the author ships an `eval/plugin.py`
next to their `SKILL.md`, and this module loads it.

The corollary is worth stating plainly: **layer-2 scores are not comparable across
skills.** A diagram scoring 0.94 and a cost table scoring 1.00 are answers to
different questions. Compare a skill to its own history, not to its neighbours.
Only layer 1 is cross-skill comparable.

A plugin is a plain Python module exposing three functions. All are optional; a
skill with no `eval/plugin.py` still gets layer 1:

    applies(expect: dict) -> bool
        Whether this plugin's evaluators apply to a given case. A diagram plugin
        returns False for a case that was never meant to produce a diagram, so
        the case is not handed a layout checker with nothing to check. Without
        this, "produced no diagram because none was asked for" and "was supposed
        to produce a diagram and did not" look identical in the report.

    prepare(ctx: PrepareContext) -> Prepared
        Author-owned preprocessing: render the artifact, run deterministic
        geometry, assemble images for a vision judge. Returns `Prepared`.

    evaluators(prepared: Prepared, judge_model) -> list[Evaluator]
        The author's evaluators for this case.

Why `prepare` is separate from `evaluators`: rendering is the author's problem
(draw.io needs an Electron binary and a virtual framebuffer, Mermaid needs
mermaid-cli, a cost table needs nothing), so the kit must not own a renderer.
Baking one in would make every team installing this kit carry a dependency for a
file format they do not produce.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PrepareContext:
    """Everything a plugin needs to preprocess one recorded case."""

    case_id: str
    prompt: str
    expect: dict
    #: `results.json` entry for this case: `files` (with `saved_to` disk paths),
    #: `tool_calls`, `status`, `output_head`, and `toolchain` -- the `python3` and
    #: `node` the *sandbox* resolved. Use `toolchain` for anything that re-runs a
    #: skill's own scripts, especially when re-scoring later from a different
    #: environment. Absent on recordings made before this field existed.
    recorded: dict
    #: Where the plugin may write derived files (a render, a diff image). Owned by
    #: the run, not by the plugin: passed in rather than composed from `__file__`
    #: so two runs cannot overwrite each other's output.
    artifacts_dir: str
    #: False when the caller asked to skip anything expensive or visual.
    want_visual: bool
    #: Pass straight through to an evaluator's `model=`, never inspected. A strands
    #: `Model` instance since the kit gained non-Bedrock providers; typed loosely
    #: rather than imported, because annotating it would make `strands` an import
    #: of the plugin contract, and a plugin that merely declares `judge_model` has
    #: no reason to need the SDK installed.
    judge_model: Any


@dataclass
class ExtraExperiment:
    """A second scoring pass with a different input, requested by a plugin.

    Needed because every evaluator in one `Experiment` sees the same
    `case.input`. Handing a topical judge a second, uncaptioned reference image
    and hoping it scores against the right one does not work -- judges do not
    reliably ignore an image they were told to ignore. So a comparison against a
    style reference has to be its own pass.
    """

    #: Appended to the case name in the report, e.g. "::style".
    suffix: str
    media: list[Any]
    instruction: str
    evaluators: list[Any]
    #: What the report calls this row.
    row_label: str
    #: False = reported and printed but kept out of the exit code. For a judge
    #: measuring an aspiration rather than a regression: a check that is always
    #: red is a check nobody reads, and it drowns the signal from checks that
    #: mean something. The plugin declares this; the kit does not guess.
    gate: bool = True


@dataclass
class Prepared:
    """What a plugin produced while preprocessing, and what it wants judged."""

    #: Deterministic findings to hand the judge as facts, so a vision model is
    #: not asked to eyeball something arithmetic already settled.
    findings: list[str] = field(default_factory=list)
    #: Images for the main case's input. Empty = no vision judge for this case.
    media: list[Any] = field(default_factory=list)
    #: Instruction shown beside the media. Required when `media` is non-empty:
    #: nothing labels the images, so the instruction has to say which is which.
    instruction: str | None = None
    #: Merged into the case metadata, so author evaluators can read it later.
    metadata: dict = field(default_factory=dict)
    extra: list[ExtraExperiment] = field(default_factory=list)
    #: Set when the plugin decided this case needs no layer-2 scoring at all.
    applicable: bool = True


class NullPlugin:
    """Layer 1 only. What a skill gets when it ships no `eval/plugin.py`."""

    name = "(none)"

    def applies(self, expect: dict) -> bool:
        return False

    def prepare(self, ctx: PrepareContext) -> Prepared:
        return Prepared(applicable=False)

    def evaluators(self, prepared: Prepared, judge_model: Any) -> list[Any]:
        return []


class LoadedPlugin:
    """A plugin module, with the missing hooks defaulted.

    Wrapping rather than requiring all three keeps the contract cheap to adopt: a
    skill that only wants one extra evaluator writes one function.
    """

    def __init__(self, name: str, module: Any, root: str):
        self.name = name
        self.module = module
        self.root = root

    def applies(self, expect: dict) -> bool:
        fn = getattr(self.module, "applies", None)
        return bool(fn(expect)) if fn else True

    def prepare(self, ctx: PrepareContext) -> Prepared:
        fn = getattr(self.module, "prepare", None)
        if not fn:
            return Prepared()
        out = fn(ctx)
        if not isinstance(out, Prepared):
            raise TypeError(
                f"{self.name}/eval/plugin.py: prepare() must return a Prepared, "
                f"got {type(out).__name__}")
        return out

    def evaluators(self, prepared: Prepared, judge_model: Any) -> list[Any]:
        fn = getattr(self.module, "evaluators", None)
        return list(fn(prepared, judge_model)) if fn else []


def _load_module(name: str, path: str) -> Any:
    """Import `path` as a module named `name`, isolating its sibling imports.

    The plugin's directory goes on `sys.path` first so a plugin can `import
    geometry` for a sibling file in its own `eval/` directory. That is the point
    of co-locating them: the author's evaluator code lives beside the author's
    skill and is versioned with it, so a skill's git sha identifies its scoring
    standard as well as its behaviour.

    The sibling imports then have to be cleaned up, and that is not tidiness.
    `import geometry` registers `sys.modules["geometry"]` under the *bare* name,
    so the second skill to ship an `eval/geometry.py` never gets its own: the
    import finds the first skill's module already cached and returns it. Measured
    on two throwaway plugins each importing a sibling `rubrics.py`, skill B was
    handed skill A's rubric and scored against it, with nothing in the report
    saying so. `rubrics.py`, `render.py` and `geometry.py` are exactly the names
    a second diagram-ish skill would pick, so this fails on the likely case.

    So after the plugin is loaded, every module that came out of this plugin's
    directory is re-keyed to `<plugin>.<sibling>` and dropped from the bare
    namespace. The plugin already holds direct references to those module
    objects, so re-keying costs it nothing, and the next plugin's `import
    geometry` finds no cache and loads its own file.

    One constraint follows: **a plugin must import its siblings at module top
    level.** `sys.path` is restored once loading finishes, so an `import
    geometry` inside a function body would run with the directory gone.
    """
    plugin_dir = os.path.dirname(os.path.abspath(path))
    inserted = plugin_dir not in sys.path
    if inserted:
        sys.path.insert(0, plugin_dir)
    before = set(sys.modules)
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load plugin from {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    finally:
        if inserted:
            try:
                sys.path.remove(plugin_dir)
            except ValueError:
                pass
        _rekey_siblings(name, plugin_dir, before)
    return module


def _rekey_siblings(plugin_name: str, plugin_dir: str, before: set[str]) -> None:
    """Move modules loaded from `plugin_dir` out of the bare `sys.modules` namespace.

    Scoped by file location rather than by "newly imported", so a third-party
    package the plugin happens to pull in first (`strands_evals`, `PIL`) stays
    shared and cached where it belongs. Only the author's own co-located files
    move.
    """
    prefix = plugin_dir + os.sep
    for mod_name in set(sys.modules) - before:
        if mod_name == plugin_name:
            continue
        mod = sys.modules.get(mod_name)
        origin = getattr(mod, "__file__", None) or ""
        if origin and os.path.abspath(origin).startswith(prefix):
            sys.modules[f"{plugin_name}.{mod_name}"] = mod
            del sys.modules[mod_name]


def load_plugin(skills_root: str, skill_name: str) -> LoadedPlugin | NullPlugin:
    """The layer-2 plugin for one skill, or `NullPlugin` if it ships none."""
    if not skill_name:
        return NullPlugin()
    path = os.path.join(skills_root, skill_name, "eval", "plugin.py")
    if not os.path.isfile(path):
        return NullPlugin()
    return LoadedPlugin(skill_name, _load_module(f"skilleval_{skill_name.replace('-', '_')}",
                                                 path), os.path.dirname(path))


def duplicate_ids(pairs: list[tuple[str, str]]) -> dict[str, list[str]]:
    """`{case_id: [dataset, …]}` for every id declared by more than one dataset.

    Case ids are the primary key of the whole pipeline and nothing enforced it:
    `artifacts/<id>/` is keyed on the id alone, and phase 2 loads the recording
    into `{r["case"]: r}`. Two skills each shipping a case called `basic` would
    therefore overwrite each other's artifacts and silently drop one run from
    scoring -- while the suite reported a full pass, because the case that
    vanished was never counted as missing.

    Returned rather than raised so each caller can name its own remedy; both
    runners treat a non-empty result as fatal before any agent is invoked.
    """
    seen: dict[str, list[str]] = {}
    for dataset, case_id in pairs:
        seen.setdefault(case_id, []).append(dataset)
    return {cid: ds for cid, ds in seen.items() if len(ds) > 1}


def skill_datasets(skills_root: str) -> list[str]:
    """Every `skills/*/eval/dataset.jsonl`, sorted.

    The marketplace default: evaluating "the repo" means evaluating every skill
    that declares cases. A skill with no dataset is simply not evaluated, which is
    a visible gap in the report rather than a silent pass.
    """
    out = []
    if not os.path.isdir(skills_root):
        return out
    for entry in sorted(os.listdir(skills_root)):
        p = os.path.join(skills_root, entry, "eval", "dataset.jsonl")
        if os.path.isfile(p):
            out.append(p)
    return out
