#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Check a spec before rendering it. Pure stdlib, no Pillow, no rendering.

    python3 scripts/lint_spec.py <spec.json>

`render_animated_webp.py --check` validates the *output*: the files exist, the
animation has enough frames, some pixels change between them. Two defects slip
straight through that and are invisible in the finished animation unless you study it:

  **Overlapping nodes.** Two boxes in the same place render one over the other. The
  picture looks fine at a glance and a label is gone.

  **A pulse sequence that contradicts the diagram.** `animation.pulses` is an
  ordered list: the highlight visits those nodes one after another, and that is the
  only thing in the deliverable expressing *when* things happen. Nothing stops it
  from listing three parallel branches in a row, which narrates concurrency as
  sequence, or from jumping to a stage no edge reaches, which looks like the
  highlight teleporting. A single frame cannot show either; a still pair cannot
  either.

  **A step schedule that contradicts the diagram.** In `steps` mode the dots
  appear one step at a time, so a step whose edge starts somewhere no earlier
  step has reached shows a dot materialising out of nowhere, and a `pulse` that
  names a node the step's arrows do not arrive at lights the wrong box.

  **Pacing nobody can follow.** A highlight that dwells a third of a second, a
  step shorter than half a second, or a frame rate under 12 all render and pass
  `--check`; they are just unwatchable.

Exits non-zero and prints `✗ LINT FAIL` with one line per problem, or `✓ LINT PASS`.
Notes prefixed `·` are printed but do not fail the lint: they name choices that are
legitimate often enough that failing them would teach people to ignore the output.

Deliberately a second implementation of checks the eval also runs. Agreement between
two independent readings of "what does this spec say happens" is evidence; a shared
module would only prove the spec is self-consistent with one opinion of it.
"""
from __future__ import annotations

import difflib
import importlib.util
import json
import math
import os
import sys
from pathlib import Path

OVERLAP_TOLERANCE = 2.0     # px; less than this is a shared border, not a collision
MIN_FRAMES = 8
MIN_FPS = 12                # below this the dots visibly jump between positions
MIN_PULSE_SECONDS = 0.4     # continuous mode: how long each highlighted node holds
MIN_STEP_SECONDS = 0.5      # steps mode: how long one step's dots take to travel
DEFAULT_STEP_SECONDS = 1.2  # the renderer's default when the spec does not say
DEFAULT_HOLD_SECONDS = 0.8


def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _boxes(spec: dict) -> list[dict]:
    """Foreground nodes with a size. Background layers are meant to sit under."""
    out = []
    for raw in spec.get("nodes") or []:
        if not isinstance(raw, dict) or str(raw.get("layer") or "") == "background" or raw.get("type") == "anchor":
            continue
        w, h = _num(raw.get("w")), _num(raw.get("h"))
        if w > 0 and h > 0:
            out.append({"id": str(raw.get("id") or ""), "x": _num(raw.get("x")),
                        "y": _num(raw.get("y")), "w": w, "h": h})
    return out


def check_overlay(spec: dict, spec_dir=".") -> list[str]:
    if "background" not in spec:
        return ["anchor nodes need background.image"] if any(n.get("type") == "anchor" for n in spec.get("nodes", [])) else []
    raw = spec["background"]
    if not isinstance(raw, dict) or not isinstance(raw.get("image"), str) or not raw["image"]:
        return ["background.image must name a local PNG, JPEG or static WebP"]
    out = []
    if not (Path(spec_dir) / raw["image"]).is_file():
        out.append(f"background.image {raw['image']!r} is missing relative to the spec directory")
    canvas = spec.get("canvas") or {}
    width, height = canvas.get("width"), canvas.get("height")
    if any(type(n) is not int or n <= 0 for n in (width, height)):
        return out + ["overlay canvas.width and height must be positive integers matching the oriented image"]

    def finite(n):
        return type(n) in (int, float) and math.isfinite(n)

    ids = set()
    for node in spec.get("nodes", []):
        node_id = node.get("id")
        if not node_id or node_id in ids:
            out.append("overlay nodes need unique, nonempty ids")
        ids.add(node_id)
        box = [node.get(k) for k in ("x", "y", "w", "h")]
        if not all(finite(n) for n in box):
            out.append(f"{node_id!r} needs finite x/y/w/h coordinates")
        else:
            x, y, w, h = box
            if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > width or y + h > height:
                out.append(f"{node_id!r} is outside the source image or has no size")
    for i, edge in enumerate(spec.get("edges", [])):
        if edge.get("from") not in ids or edge.get("to") not in ids:
            out.append(f"overlay edge[{i}] must connect known from/to nodes")
        points = edge.get("points")
        if not isinstance(points, list) or len(points) < 2:
            out.append(f"overlay edge[{i}] needs at least two points traced along the original arrow")
        elif any(not isinstance(p, (list, tuple)) or len(p) != 2 or
                 not all(finite(n) for n in p) or not (0 <= p[0] < width and 0 <= p[1] < height)
                 for p in points):
            out.append(f"overlay edge[{i}] has invalid points or points outside the source image")
        elif len(set(map(tuple, points))) < 2:
            out.append(f"overlay edge[{i}] has a zero-length path")
        if "draw_path" in edge and type(edge["draw_path"]) is not bool:
            out.append(f"overlay edge[{i}].draw_path must be a boolean")
        if "radius" in edge and (not finite(edge["radius"]) or edge["radius"] < 0):
            out.append(f"overlay edge[{i}].radius must be a nonnegative number")
    return out


def check_overlaps(spec: dict) -> list[str]:
    out = []
    boxes = _boxes(spec)
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            dx = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
            dy = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
            if dx > OVERLAP_TOLERANCE and dy > OVERLAP_TOLERANCE:
                out.append(f"{a['id']!r} and {b['id']!r} overlap by {dx:g}x{dy:g} px — "
                           f"one will be drawn over the other; move one or shrink both")
    return out


def check_pulse_order(spec: dict) -> list[str]:
    """Consecutive pulses must be joined by an edge, in the edge's direction."""
    ids = {str(n.get("id")) for n in (spec.get("nodes") or []) if isinstance(n, dict)}
    pulses = [str(p) for p in ((spec.get("animation") or {}).get("pulses") or [])
              if str(p) in ids]
    succ: dict[str, set[str]] = {}
    pred: dict[str, set[str]] = {}
    for edge in spec.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        a, b = str(edge.get("from")), str(edge.get("to"))
        if a in ids and b in ids:
            succ.setdefault(a, set()).add(b)
            pred.setdefault(b, set()).add(a)

    out = []
    for i in range(len(pulses) - 1):
        a, b = pulses[i], pulses[i + 1]
        if b in succ.get(a, set()):
            continue
        shared = sorted(pred.get(a, set()) & pred.get(b, set()))
        if shared:
            # The classic fan-out mistake, and worth naming as such: the two stages
            # are siblings, so listing them in a row says one follows the other.
            out.append(
                f"animation.pulses lists {a!r} then {b!r}, but both start from "
                f"{shared[0]!r} — they are parallel branches, and pulsing them in a "
                f"row tells the viewer they happen one after the other")
        elif a in succ.get(b, set()):
            out.append(f"animation.pulses lists {a!r} then {b!r}, but the edge runs "
                       f"{b!r} -> {a!r}; the animation plays the flow backwards")
        else:
            out.append(f"animation.pulses lists {a!r} then {b!r}, and no edge joins "
                       f"them; the highlight will jump across the diagram")
    return out


def _mode(spec: dict) -> str:
    steps = (spec.get("animation") or {}).get("steps")
    return "steps" if isinstance(steps, list) and steps else "continuous"


def check_animation(spec: dict) -> list[str]:
    animation = spec.get("animation") or {}
    canvas = spec.get("canvas") or {}
    frames = _num(canvas.get("frames"), 0)
    fps = _num(canvas.get("fps"), 20)
    out = []
    if fps and fps < MIN_FPS:
        out.append(f"canvas.fps={fps:g}; below {MIN_FPS} the dots jump instead of travel — "
                   f"slow the animation down with more frames or longer steps, not a lower fps")
    if _mode(spec) == "steps":
        return out + check_steps(spec)
    if frames and frames < MIN_FRAMES:
        out.append(f"canvas.frames={frames:g}; below {MIN_FRAMES} the animation stutters")
    if (animation.get("paths") == [] or (animation.get("paths") is None
                                        and not spec.get("edges"))) \
            and not animation.get("pulses"):
        out.append("animation declares neither paths nor pulses, so every frame is "
                   "the static PNG")
    ids = {str(n.get("id")) for n in (spec.get("nodes") or []) if isinstance(n, dict)}
    pulses = [p for p in animation.get("pulses") or [] if str(p) in ids]
    for pulse in animation.get("pulses") or []:
        if str(pulse) not in ids:
            out.append(f"animation.pulses names {pulse!r}, which is not a node id; the "
                       f"highlight is silently dropped")
    if pulses and frames and fps:
        dwell = frames / len(pulses) / fps
        if dwell < MIN_PULSE_SECONDS:
            need = int(-(-MIN_PULSE_SECONDS * len(pulses) * fps // 1))
            out.append(f"{len(pulses)} pulses in {frames:g} frames at {fps:g} fps: each node "
                       f"highlights for {dwell:.2f} s, too fast to follow — raise canvas.frames "
                       f"to at least {need}, or switch to animation.steps")
    return out


def check_steps(spec: dict) -> list[str]:
    """`animation.steps` is the story in order; each step must start where the story is.

    Step 0 may start anywhere. Every later step's edges must leave a node some
    earlier step arrived at, or a node nothing points to (an independent trigger);
    a `pulse` must be a node the step's own arrows reach. `pulses` is ignored in
    this mode, so a spec that carries both is saying two things at once.
    """
    animation = spec.get("animation") or {}
    canvas = spec.get("canvas") or {}
    fps = _num(canvas.get("fps"), 20)
    ids = {str(n.get("id")) for n in (spec.get("nodes") or []) if isinstance(n, dict)}
    edges = [e for e in (spec.get("edges") or []) if isinstance(e, dict)]
    incoming = {str(e.get("to")) for e in edges}
    out = []
    if animation.get("pulses"):
        out.append("animation.pulses is ignored when animation.steps is present — delete it, "
                   "or move each node into the step that reaches it as steps[].pulse")
    default_seconds = _num(animation.get("step_seconds"), DEFAULT_STEP_SECONDS)
    if default_seconds < MIN_STEP_SECONDS:
        out.append(f"animation.step_seconds={default_seconds:g}; below {MIN_STEP_SECONDS} a "
                   f"viewer cannot follow the dot from one box to the next")
    reached: set[str] = set()
    seen: dict[int, int] = {}
    for i, raw in enumerate(animation.get("steps") or []):
        step = raw if isinstance(raw, dict) else {"edges": raw}
        edge_ids = step.get("edges")
        if not isinstance(edge_ids, list) or not edge_ids:
            out.append(f"animation.steps[{i}] has no edges; a step with nothing travelling is a pause "
                       f"— use hold_seconds for that")
            edge_ids = []
        if step.get("seconds") is not None and _num(step["seconds"]) < MIN_STEP_SECONDS:
            out.append(f"animation.steps[{i}].seconds={_num(step['seconds']):g}; below "
                       f"{MIN_STEP_SECONDS} the dot teleports")
        valid = []
        for idx in edge_ids:
            if not isinstance(idx, int) or not 0 <= idx < len(edges):
                out.append(f"animation.steps[{i}] names edge {idx!r}, but the spec has "
                           f"{len(edges)} edges (indices 0..{len(edges) - 1})")
                continue
            if idx in seen:
                out.append(f"edge {idx} is in animation.steps[{seen[idx]}] and again in steps[{i}]; "
                           f"a dot would travel it twice")
                continue
            seen[idx] = i
            valid.append(idx)
        if i > 0:
            for idx in valid:
                start = str(edges[idx].get("from"))
                if start not in reached and start in incoming:
                    out.append(f"animation.steps[{i}] travels edge {idx} ({start} -> "
                               f"{edges[idx].get('to')}), but no earlier step has arrived at "
                               f"{start!r}; the dot appears from nowhere — move this step later, "
                               f"or add the step that reaches {start!r} first")
        arrivals = {str(edges[idx].get("to")) for idx in valid}
        pulse = step.get("pulse")
        pulse_ids = [pulse] if isinstance(pulse, str) else list(pulse or [])
        for node_id in pulse_ids:
            if str(node_id) not in ids:
                out.append(f"animation.steps[{i}].pulse names {node_id!r}, which is not a node id")
            elif str(node_id) not in arrivals:
                out.append(f"animation.steps[{i}].pulse lights {node_id!r}, but the step's arrows "
                           f"arrive at {sorted(arrivals)}; the wrong box lights up")
        reached |= arrivals
        reached |= {str(edges[idx].get("from")) for idx in valid}
    return out


def _icon_root() -> Path | None:
    override = os.environ.get("VISUAL_FLOW_ICON_DIR")
    if override:
        return Path(override) if Path(override).is_dir() else None
    found = importlib.util.find_spec("diagrams")
    if found is None or not found.origin:
        return None
    base = Path(found.origin).resolve().parent
    for candidate in (base.parent / "resources", base / "resources"):
        if candidate.is_dir():
            return candidate
    return None


def check_icons(spec: dict) -> list[str]:
    """Every `icon` key must resolve, or the render shows a gap where an icon was meant.

    Keys are `<provider>/<category>/<name>` inside the icon set the `diagrams`
    package installs. An unknown key gets the closest existing keys as the fix.
    """
    wanted = [(str(n.get("id")), str(n.get("icon"))) for n in (spec.get("nodes") or [])
              if isinstance(n, dict) and n.get("icon")]
    if not wanted:
        return []
    root = _icon_root()
    out = []
    if root is None:
        out.append(f"{len(wanted)} node(s) ask for an icon but no icon set is installed — "
                   f"python3 -m pip install diagrams (or set VISUAL_FLOW_ICON_DIR)")
        return out
    keys = None
    for node_id, key in wanted:
        if key.lower().endswith(".png"):
            if not Path(key).is_file():
                out.append(f"node {node_id!r}: icon file {key!r} does not exist")
            continue
        if (root / f"{key}.png").is_file():
            continue
        if keys is None:
            keys = sorted(p.relative_to(root).with_suffix("").as_posix() for p in root.glob("*/*/*.png"))
        stem = key.rsplit("/", 1)[-1]
        close = difflib.get_close_matches(key, keys, n=3, cutoff=0.5) or \
            [k for k in keys if stem and stem in k][:3]
        hint = f"; did you mean {', '.join(close)}" if close else "; run scripts/list_icons.py <words>"
        out.append(f"node {node_id!r}: icon {key!r} is not in the icon set{hint}")
    return out


def notes(spec: dict) -> list[str]:
    """Things worth saying that are not defects."""
    out = []
    animation = spec.get("animation") or {}
    canvas = spec.get("canvas") or {}
    fps = _num(canvas.get("fps"), 20)
    if _mode(spec) == "steps":
        edges = spec.get("edges") or []
        listed = {idx for raw in animation["steps"]
                  for idx in ((raw.get("edges") if isinstance(raw, dict) else raw) or [])
                  if isinstance(idx, int)}
        missing = [i for i in range(len(edges)) if i not in listed]
        if missing:
            out.append(f"edges {missing} are in no step, so they never carry a dot — fine for a "
                       f"branch you chose not to narrate, wrong if it is part of the story")
        default_seconds = _num(animation.get("step_seconds"), DEFAULT_STEP_SECONDS)
        total = sum(_num((r.get("seconds") if isinstance(r, dict) else None), default_seconds)
                    for r in animation["steps"]) + _num(animation.get("hold_seconds"), DEFAULT_HOLD_SECONDS)
        out.append(f"steps mode: {len(animation['steps'])} steps, about {total:.1f} s per loop "
                   f"at {fps:g} fps (canvas.frames is derived)")
    else:
        frames = _num(canvas.get("frames"), 48)
        if fps:
            out.append(f"continuous mode: {frames:g} frames at {fps:g} fps = {frames / fps:.1f} s per loop")
    return out


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python3 lint_spec.py <spec.json>", file=sys.stderr)
        return 2
    try:
        with open(sys.argv[1], encoding="utf-8") as fh:
            spec = json.load(fh)
    except (OSError, ValueError) as e:
        print(f"✗ LINT FAIL\n  cannot read the spec: {e}")
        return 1

    problems = (check_overlay(spec, Path(sys.argv[1]).resolve().parent) + check_overlaps(spec)
                + check_pulse_order(spec) + check_animation(spec) + check_icons(spec))
    animation = spec.get("animation") or {}
    timing = (f"{len(animation.get('steps') or [])} steps" if _mode(spec) == "steps"
              else f"{len(animation.get('pulses') or [])} pulses")
    print(f"spec lint: {len(spec.get('nodes') or [])} nodes, "
          f"{len(spec.get('edges') or [])} edges, {timing}")
    for note in notes(spec):
        print(f"  · {note}")
    if problems:
        for p in problems:
            print(f"  ✗ {p}")
        print(f"✗ LINT FAIL ({len(problems)} problem(s))")
        return 1
    print("✓ LINT PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
