"""Layout defects computable from a flow spec, without rendering it.

The skill's own `--check` reads the *output*: files exist, dimensions match the
spec, frame count matches, sampled frames differ. All four are true of a diagram
whose boxes sit on top of each other, whose labels run past their borders, and
whose pulses name nodes that do not exist — because none of that changes the
canvas size or the frame count.

That gap is where this skill's real defects live, and closing it needs no pixels.
The spec states `x/y/w/h` per node, so containment and overlap are arithmetic.
Rendering is still worth doing — it catches fonts, contrast and whether the thing
reads — but it costs a subprocess and a vision model, and everything here is
exact and free.

One thing this module deliberately *does* check that looks like content rather
than geometry: whether the spec is the shipped asset with the labels left alone.
`assets/default-spec.json` is a complete, well-formed diagram of somebody else's
system, so rendering it unedited passes every mechanical check the skill ships.
A "layout" that is 100% correct because it was copied is the loudest defect this
skill can have, and it is only visible by comparison.
"""

from __future__ import annotations

import importlib.util

import json
import os
from dataclasses import dataclass

#: Per-character width as a fraction of font size, for the hand-drawn faces the
#: renderer prefers (Excalifont, then a sans fallback). Measured against the
#: shipped specs: at this ratio every label in `assets/default-spec.json` fits
#: its box at its declared size, which is the calibration that matters — the
#: assets are the only known-good layouts available.
CHAR_W_RATIO = 0.55
#: Line height as a multiple of font size, plus the renderer's line spacing.
LINE_H_RATIO = 1.25
LINE_SPACING = 4.0
#: `RENDER_SCALE` in the renderer: it draws into a canvas twice the declared size
#: and downsamples at the end. Font sizes in a spec are therefore *pixel* sizes in
#: that doubled space, while `x/y/w/h` are in declared space — so a box 250 wide
#: offers 500 px of room to a 20 px font. Getting this factor wrong is not a
#: tolerance question: it reports every second body line in the shipped assets as
#: clipped, which is how it was found.
RENDER_SCALE = 2.0
#: Overlaps below this are rounding in a hand-authored spec, not collisions.
OVERLAP_TOLERANCE = 2.0
#: A font size below the author's declared size by more than this is worth
#: reporting: the renderer shrank the copy to make it fit, and the diagram reads
#: smaller than it was drawn to.
SHRINK_SLACK = 2.0
#: An animation needs frames to animate. Below this it reads as a stutter, not motion.
MIN_FRAMES = 8
#: Pacing floors. Under MIN_FPS the dots jump between positions; a continuous-mode
#: highlight held under MIN_PULSE_SECONDS, or a step travelled in under
#: MIN_STEP_SECONDS, renders and passes --check and cannot be followed by a viewer.
#: Measured on a 7-pulse spec at the old 48-frame default: 0.34 s per node, and the
#: user's first reaction was "slow it down".
MIN_FPS = 12
MIN_PULSE_SECONDS = 0.4
MIN_STEP_SECONDS = 0.5
#: The renderer's own defaults for `animation.steps`, mirrored so the schedule the
#: eval computes is the schedule the renderer wrote.
DEFAULT_STEP_SECONDS = 1.2
DEFAULT_HOLD_SECONDS = 0.8
#: Empty margin around the drawn content, past which the canvas is oversized and
#: the diagram floats in dead space.
DEAD_MARGIN = 200.0

HERE = os.path.dirname(os.path.abspath(__file__))
SHIPPED_SPECS = (
    os.path.join(HERE, "..", "assets", "default-spec.json"),
    os.path.join(HERE, "..", "assets", "dark-spec.json"),
)


@dataclass
class Finding:
    kind: str
    detail: str


@dataclass
class Node:
    id: str
    type: str
    label: str
    body: str
    x: float
    y: float
    w: float
    h: float
    layer: str

    @property
    def x2(self) -> float:
        return self.x + self.w

    @property
    def y2(self) -> float:
        return self.y + self.h


def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def nodes_of(spec: dict) -> list[Node]:
    out = []
    for raw in spec.get("nodes") or []:
        if not isinstance(raw, dict):
            continue
        out.append(Node(
            id=str(raw.get("id") or ""),
            type=str(raw.get("type") or "box"),
            label=str(raw.get("label") or ""),
            body=str(raw.get("body") or ""),
            x=_num(raw.get("x")), y=_num(raw.get("y")),
            w=_num(raw.get("w")), h=_num(raw.get("h")),
            layer=str(raw.get("layer") or "foreground"),
        ))
    return out


def canvas_of(spec: dict) -> dict:
    canvas = spec.get("canvas") or {}
    return {
        "width": _num(canvas.get("width"), 1280.0),
        "height": _num(canvas.get("height"), 800.0),
        "frames": _num(canvas.get("frames"), 48.0),
        "fps": _num(canvas.get("fps"), 20.0),
    }


def _theme_name(spec: dict) -> str:
    theme = spec.get("theme")
    if isinstance(theme, str):
        return theme
    if isinstance(theme, dict):
        return str(theme.get("name") or "")
    return ""


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #
def check_frame(spec: dict) -> list[Finding]:
    """Nothing may be drawn outside the canvas, and nothing sized to zero."""
    canvas = canvas_of(spec)
    out = []
    for n in nodes_of(spec):
        if n.w <= 0 or n.h <= 0:
            out.append(Finding("node_has_no_size",
                               f"node {n.id!r} has w={n.w:g} h={n.h:g}; it renders as nothing"))
            continue
        if n.x < 0 or n.y < 0 or n.x2 > canvas["width"] or n.y2 > canvas["height"]:
            out.append(Finding("node_out_of_frame",
                               f"node {n.id!r} spans ({n.x:g},{n.y:g})-({n.x2:g},{n.y2:g}) "
                               f"outside the {canvas['width']:g}x{canvas['height']:g} canvas"))
    return out


def check_overlaps(spec: dict) -> list[Finding]:
    """No two drawn-on-top elements may occupy the same pixels.

    `layer: "background"` boxes are section containers and are *supposed* to sit
    under their contents, so they are excluded as the left-hand side of a pair.
    Everything else — boxes, diamonds, notes and free `label` text — is drawn at
    full opacity, and two of them in the same place is one covering the other.
    """
    fg = [n for n in nodes_of(spec) if n.layer != "background" and n.type != "anchor" and n.w > 0 and n.h > 0]
    out = []
    for i, a in enumerate(fg):
        for b in fg[i + 1:]:
            dx = min(a.x2, b.x2) - max(a.x, b.x)
            dy = min(a.y2, b.y2) - max(a.y, b.y)
            if dx > OVERLAP_TOLERANCE and dy > OVERLAP_TOLERANCE:
                out.append(Finding("nodes_overlap",
                                   f"{a.id!r} and {b.id!r} overlap by {dx:g}x{dy:g} px; "
                                   f"one is drawn over the other"))
    return out


def _is_wide(ch: str) -> bool:
    """CJK, Hangul and full-width punctuation occupy about one em, not 0.55.

    Worth the four ranges: this repo's datasets are bilingual, and estimating a
    Chinese label at Latin width under-measures it by nearly half — which turns
    the clipping check off exactly where a hand-authored spec is most likely to
    overflow, since the author counted characters rather than pixels.
    """
    code = ord(ch)
    return (0x1100 <= code <= 0x11FF or 0x2E80 <= code <= 0xA4CF
            or 0xAC00 <= code <= 0xD7A3 or 0xF900 <= code <= 0xFAFF
            or 0xFE30 <= code <= 0xFE4F or 0xFF00 <= code <= 0xFF60)


def _text_width(text: str, size: float) -> float:
    ems = sum(1.0 if _is_wide(ch) else CHAR_W_RATIO for ch in text)
    return ems * size


def _wrapped_extent(text: str, avail_w: float, size: float) -> tuple[float, float]:
    """`(width, height)` of `text` wrapped at `avail_w`, at font `size`.

    Models the renderer's own `wrap_text` + `text_box`: break on spaces, keep
    explicit newlines, and let a single over-long word overhang rather than
    hyphenating it — which is what makes one long word a real defect while a long
    sentence is merely several lines.
    """
    lines: list[str] = []
    for para in str(text).split("\n"):
        current = ""
        for word in para.split(" "):
            candidate = f"{current} {word}".strip()
            if current and _text_width(candidate, size) > avail_w:
                lines.append(current)
                current = word
            else:
                current = candidate
        lines.append(current)
    width = max((_text_width(line, size) for line in lines), default=0.0)
    height = len(lines) * size * LINE_H_RATIO + max(0, len(lines) - 1) * LINE_SPACING
    return width, height


def _text_areas(spec: dict, n: Node) -> list[tuple[str, str, float, float, float, float]]:
    """`(what, text, avail_w, avail_h, declared_size, min_size)` per text run.

    The numbers come straight from `render_animated_webp.py:draw_node` — the inset,
    the label band height, the residual body height, the per-type defaults and the
    per-type `min_size` floor. Duplicated deliberately, and the duplication is the
    point: this module claims to predict what that function draws, so it has to use
    the same arithmetic, and a drift between the two is a bug in the prediction
    rather than a matter of taste.
    """
    raw = spec_node(spec, n.id) or {}
    if n.type == "anchor":
        return []
    if n.type == "label":
        return [("label", n.label, n.w, n.h, _num(raw.get("size"), 18.0), 9.0)]
    if n.type == "diamond":
        return [
            ("label", n.label, n.w * 0.64, n.h * 0.28, _num(raw.get("label_size"), 20.0), 8.0),
            ("body", n.body, n.w * 0.56, n.h * 0.25, _num(raw.get("body_size"), 13.0), 9.0),
        ]
    label_h = _num(raw.get("label_h"), min(42.0, n.h * 0.34))
    return [
        ("label", n.label, n.w - 28, label_h, _num(raw.get("label_size"), 22.0), 12.0),
        ("body", n.body, n.w - 28, n.h - label_h - 22, _num(raw.get("body_size"), 14.0), 8.0),
    ]


def check_text_fits(spec: dict) -> list[Finding]:
    """Labels and body copy must fit the box they are written into.

    The renderer does not clip text on the first attempt: it wraps, then walks the
    font size down to a per-area floor looking for something that fits. So there
    are two distinct defects, and calling either one "overflow" would be wrong:

    `text_shrinks`   it fits, but only after the renderer dropped the size the
                     author asked for. Cosmetic — the diagram is legible, just not
                     the diagram that was specified.
    `text_clips`     nothing fits, even at the floor. The renderer draws it anyway
                     and it runs outside the shape.

    Both are estimates from a per-character width model, so both are stated with
    the numbers behind them; `SHRINK_SLACK` keeps a one-point shrink from being
    reported as a defect at all.
    """
    out = []
    for n in nodes_of(spec):
        if n.w <= 0 or n.h <= 0:
            continue
        for what, text, avail_w, avail_h, declared, floor in _text_areas(spec, n):
            if not str(text).strip() or avail_w <= 0 or avail_h <= 0:
                continue
            room_w, room_h = avail_w * RENDER_SCALE, avail_h * RENDER_SCALE
            best = None
            size = declared
            while size >= floor:
                w, h = _wrapped_extent(text, room_w, size)
                if w <= room_w and h <= room_h:
                    best = size
                    break
                size -= 1
            snippet = str(text).replace("\n", " / ")
            if best is None:
                w, h = _wrapped_extent(text, room_w, floor)
                out.append(Finding("text_clips",
                                   f"{what} {snippet!r} on {n.id!r} does not fit "
                                   f"{avail_w:.0f}x{avail_h:.0f} px even at the {floor:g} px "
                                   f"floor (it needs about {w / RENDER_SCALE:.0f}x"
                                   f"{h / RENDER_SCALE:.0f}); it renders outside the shape"))
            elif declared - best > SHRINK_SLACK:
                out.append(Finding("text_shrinks",
                                   f"{what} {snippet!r} on {n.id!r} is declared at "
                                   f"{declared:g} px but only fits at about {best:g} px; the "
                                   f"renderer will shrink it"))
    return out


def spec_node(spec: dict, node_id: str) -> dict | None:
    for raw in spec.get("nodes") or []:
        if isinstance(raw, dict) and str(raw.get("id") or "") == node_id:
            return raw
    return None


def check_wiring(spec: dict) -> list[Finding]:
    """Every edge must land somewhere, and every animation reference must resolve.

    The two failure modes differ and both are worth naming. An edge whose `from`
    or `to` is not a node id makes the renderer raise `KeyError`
    (`render_animated_webp.py:365`), so nothing is produced at all — a loud failure
    that this check turns into a sentence about which edge. A pulse naming an
    unknown node is filtered out instead, so the animation renders, exits 0, passes
    `--check`, and is quietly missing the highlight the spec asked for. That one is
    only visible here.
    """
    ids = {n.id for n in nodes_of(spec)}
    edges = spec.get("edges") or []
    out = []
    for i, edge in enumerate(edges):
        if not isinstance(edge, dict):
            out.append(Finding("edge_malformed", f"edge {i} is not an object"))
            continue
        points = edge.get("points") or []
        if len(points) >= 2:
            continue  # an explicit route needs no endpoints
        for end in ("from", "to"):
            ref = edge.get(end)
            if not ref:
                out.append(Finding("edge_dangling",
                                   f"edge {i} has no {end!r} and no explicit points"))
            elif str(ref) not in ids:
                out.append(Finding("edge_dangling",
                                   f"edge {i} points {end}={ref!r}, which is not a node id"))

    animation = spec.get("animation") or {}
    for pulse in animation.get("pulses") or []:
        if str(pulse) not in ids:
            out.append(Finding("pulse_unknown_node",
                               f"animation.pulses names {pulse!r}, which is not a node id; "
                               f"the highlight is silently dropped"))
    for i, path in enumerate(animation.get("paths") or []):
        if not isinstance(path, dict):
            continue
        if "edge" in path:
            idx = path["edge"]
            if not isinstance(idx, int) or not (0 <= idx < len(edges)):
                out.append(Finding("path_edge_out_of_range",
                                   f"animation.paths[{i}] references edge {idx!r}, but the "
                                   f"spec declares {len(edges)} edges"))
    return out


def animation_mode(spec: dict) -> str:
    steps = (spec.get("animation") or {}).get("steps")
    return "steps" if isinstance(steps, list) and steps else "continuous"


def steps_schedule(spec: dict) -> dict | None:
    """Frame ranges of `animation.steps`, as the renderer derives them. None otherwise.

    A second implementation of the renderer's `build_schedule`, kept in stdlib so
    the eval can name "the midpoint frame of step 3" without importing Pillow. If
    the two ever disagree, the frame the judge is shown is not the frame the
    renderer meant, and `steps_confined` in the renderer's own --check is the
    tie-breaker.
    """
    if animation_mode(spec) != "steps":
        return None
    animation = spec.get("animation") or {}
    edges = spec.get("edges") or []
    fps = max(1, int(canvas_of(spec)["fps"]))
    default_seconds = _num(animation.get("step_seconds"), DEFAULT_STEP_SECONDS)
    steps = []
    cursor = 0
    for index, raw in enumerate(animation["steps"]):
        raw = raw if isinstance(raw, dict) else {"edges": raw}
        seconds = _num(raw.get("seconds"), default_seconds)
        length = max(2, int(round(fps * seconds)))
        edge_ids = [i for i in (raw.get("edges") or []) if isinstance(i, int) and 0 <= i < len(edges)]
        steps.append({"step": index, "edges": edge_ids, "start": cursor, "end": cursor + length,
                      "mid": cursor + length // 2,
                      "routes": [f"{edges[i].get('from')} -> {edges[i].get('to')}" for i in edge_ids]})
        cursor += length
    hold = max(0, int(round(fps * _num(animation.get("hold_seconds"), DEFAULT_HOLD_SECONDS))))
    return {"fps": fps, "steps": steps, "hold_frames": hold, "frames": cursor + hold,
            "seconds": round((cursor + hold) / fps, 2)}


def check_steps_order(spec: dict) -> list[Finding]:
    """`animation.steps` is the flow told one step at a time, so each step must start
    where the previous ones left the viewer.

    Step 0 may begin anywhere -- a cyclic flow has no source. From then on an edge
    may only travel out of a node an earlier step arrived at, or out of a node
    nothing points to (an independent trigger is allowed to fire mid-story). A
    `pulse` names the box lit when the step's dots arrive, so it has to be one the
    step's own arrows reach. And `pulses` means nothing in this mode: a spec that
    sets both has two stories in it, and only one will play.
    """
    if animation_mode(spec) != "steps":
        return []
    animation = spec.get("animation") or {}
    ids = {n.id for n in nodes_of(spec)}
    edges = [e for e in (spec.get("edges") or []) if isinstance(e, dict)]
    incoming = {str(e.get("to")) for e in edges}
    out: list[Finding] = []
    if animation.get("pulses"):
        out.append(Finding("step_pulses_ignored",
                           "animation.pulses is set alongside animation.steps; the renderer "
                           "ignores it in steps mode, so the spec describes an ordering the "
                           "animation does not play"))
    default_seconds = _num(animation.get("step_seconds"), DEFAULT_STEP_SECONDS)
    if default_seconds < MIN_STEP_SECONDS:
        out.append(Finding("pace_step_too_fast",
                           f"animation.step_seconds={default_seconds:g}; below {MIN_STEP_SECONDS} "
                           f"a viewer cannot follow the dot from one box to the next"))
    reached: set[str] = set()
    seen: dict[int, int] = {}
    for i, raw in enumerate(animation["steps"]):
        step = raw if isinstance(raw, dict) else {"edges": raw}
        edge_ids = step.get("edges") if isinstance(step.get("edges"), list) else []
        if not edge_ids:
            out.append(Finding("step_empty", f"animation.steps[{i}] travels no edge"))
        if step.get("seconds") is not None and _num(step["seconds"]) < MIN_STEP_SECONDS:
            out.append(Finding("pace_step_too_fast",
                               f"animation.steps[{i}].seconds={_num(step['seconds']):g}; below "
                               f"{MIN_STEP_SECONDS} the dot teleports"))
        valid: list[int] = []
        for idx in edge_ids:
            if not isinstance(idx, int) or not 0 <= idx < len(edges):
                out.append(Finding("step_edge_out_of_range",
                                   f"animation.steps[{i}] names edge {idx!r}; the spec has "
                                   f"{len(edges)} edges"))
            elif idx in seen:
                out.append(Finding("step_edge_repeated",
                                   f"edge {idx} is travelled in steps[{seen[idx]}] and again in "
                                   f"steps[{i}]"))
            else:
                seen[idx] = i
                valid.append(idx)
        if i > 0:
            for idx in valid:
                start = str(edges[idx].get("from"))
                if start not in reached and start in incoming:
                    out.append(Finding(
                        "step_order_disconnected",
                        f"animation.steps[{i}] travels {start} -> {edges[idx].get('to')}, but "
                        f"no earlier step arrived at {start!r}; the dot appears from nowhere"))
        arrivals = {str(edges[idx].get("to")) for idx in valid}
        pulse = step.get("pulse")
        for node_id in ([pulse] if isinstance(pulse, str) else list(pulse or [])):
            if str(node_id) not in ids:
                out.append(Finding("pulse_unknown_node",
                                   f"animation.steps[{i}].pulse names {node_id!r}, which is not a node id"))
            elif str(node_id) not in arrivals:
                out.append(Finding("step_pulse_off_route",
                                   f"animation.steps[{i}].pulse lights {node_id!r}, but the step's "
                                   f"arrows arrive at {sorted(arrivals)}"))
        reached |= arrivals | {str(edges[idx].get("from")) for idx in valid}
    unlisted = [i for i in range(len(edges)) if i not in seen]
    if unlisted:
        out.append(Finding("step_edges_unlisted",
                           f"edges {unlisted} are in no step and never carry a dot"))
    return out


def _icon_root():
    override = os.environ.get("VISUAL_FLOW_ICON_DIR")
    if override:
        return override if os.path.isdir(override) else None
    found = importlib.util.find_spec("diagrams")
    if found is None or not found.origin:
        return None
    base = os.path.dirname(os.path.realpath(found.origin))
    for candidate in (os.path.join(os.path.dirname(base), "resources"), os.path.join(base, "resources")):
        if os.path.isdir(candidate):
            return candidate
    return None


def check_icons(spec: dict) -> list[Finding]:
    """An `icon` key that does not resolve renders as a gap with nothing to say so.

    Mirrors the renderer's lookup: `<provider>/<category>/<name>.png` under the
    icon set the `diagrams` package installs, or a literal .png path.
    """
    wanted = [(n.id, str(spec_node(spec, n.id).get("icon"))) for n in nodes_of(spec)
              if (spec_node(spec, n.id) or {}).get("icon")]
    if not wanted:
        return []
    root = _icon_root()
    if root is None:
        return [Finding("icon_set_missing",
                        f"{len(wanted)} node(s) carry an icon key but no icon set is installed "
                        f"in this interpreter (pip install diagrams)")]
    out = []
    for node_id, key in wanted:
        path = key if key.lower().endswith(".png") else os.path.join(root, f"{key}.png")
        if not os.path.isfile(path):
            out.append(Finding("icon_unknown",
                               f"node {node_id!r} asks for icon {key!r}, which is not in the icon set; "
                               f"the render shows a gap where the icon was meant"))
    return out


def check_animation(spec: dict) -> list[Finding]:
    """The animation has to move, and the spec is where that is decided."""
    canvas = canvas_of(spec)
    animation = spec.get("animation") or {}
    out = []
    if canvas["fps"] < MIN_FPS:
        out.append(Finding("pace_low_fps",
                           f"canvas.fps={canvas['fps']:g}; below {MIN_FPS} the dots jump between "
                           f"positions instead of travelling"))
    if animation_mode(spec) == "steps":
        # Frames are derived from the schedule; there is nothing else to check here.
        return out
    if canvas["frames"] < MIN_FRAMES:
        out.append(Finding("too_few_frames",
                           f"canvas.frames={canvas['frames']:g}; below {MIN_FRAMES} the animation "
                           f"stutters rather than animates"))
    paths = animation.get("paths")
    pulses = animation.get("pulses") or []
    edges = spec.get("edges") or []
    # `paths: null`/absent defaults to every edge inside the renderer, so only an
    # explicit empty list is a declaration of no motion.
    no_paths = (paths == []) or (paths is None and not edges)
    if no_paths and not pulses:
        out.append(Finding("no_motion_declared",
                           "animation declares neither paths nor pulses, so every frame is "
                           "the static PNG"))
    ids = {n.id for n in nodes_of(spec)}
    known = [p for p in pulses if str(p) in ids]
    if known and canvas["frames"] and canvas["fps"]:
        dwell = canvas["frames"] / len(known) / canvas["fps"]
        if dwell < MIN_PULSE_SECONDS:
            out.append(Finding("pace_pulse_too_fast",
                               f"{len(known)} pulses over {canvas['frames']:g} frames at "
                               f"{canvas['fps']:g} fps hold each node for {dwell:.2f} s; under "
                               f"{MIN_PULSE_SECONDS} s the walk cannot be followed"))
    return out


def check_pulse_order(spec: dict) -> list[Finding]:
    """`animation.pulses` is a sequence in time, so it has to agree with the graph.

    A data flow's meaning is largely its ordering, and the pulse list is the only
    place the animation expresses that: the highlight visits nodes in this order, one after
    another. Nothing checked it. `pulse_unknown_node` asks whether an id exists and
    `no_motion_declared` asks whether anything moves at all -- both are satisfied by
    a sequence that tells the story in the wrong order, or in no order.

    Measured on a passing run: an ETL case pulsed
    `… decision, sns-alert, retry-check, athena, complete` while no edge joins
    `retry-check` to `athena`. On screen the highlight teleports out of the retry
    branch into the happy path across empty canvas. `FlowSpecLayout` scored 1.00 and
    the motion judge 0.82, because "is there motion" and "is the motion the flow" are
    different questions and only the first was being asked.

    Three distinct defects, and the severities differ:

      * a consecutive pair with no forward path at all -- the highlight jumps. The
        viewer is shown a transition the diagram says does not exist.
      * a pair joined only against the arrows -- the flow is narrated backwards,
        which is worse than silence because it is legible and wrong.
      * a pair connected by a longer path whose intermediate stages are skipped, or
        a sequence starting somewhere other than a source. Both are legitimate
        abbreviations often enough that failing them would train people to ignore
        this check, so they are reported without blocking.

    Decorative nodes -- backgrounds, group titles, standalone labels -- carry no
    edges and are not expected to be pulsed; they are simply absent from the walk.
    """
    ids = {n.id for n in nodes_of(spec)}
    pulses = [str(p) for p in ((spec.get("animation") or {}).get("pulses") or [])
              if str(p) in ids]
    if len(pulses) < 2:
        return []

    succ: dict[str, set[str]] = {}
    pred: dict[str, set[str]] = {}
    for edge in spec.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        a, b = edge.get("from"), edge.get("to")
        if a in ids and b in ids:
            succ.setdefault(str(a), set()).add(str(b))
            pred.setdefault(str(b), set()).add(str(a))

    def walk(start: str, end: str, graph: dict[str, set[str]]) -> list[str] | None:
        """Shortest walk as a node list, or None. Cycles are normal here.

        The route is returned, not just its length, because the length alone
        misleads: `retry-check -> athena` is "4 edges apart" only by going the whole
        way around a retry loop, which on screen is still a jump. Naming the route
        lets the reader see that.
        """
        seen, frontier = {start}, [[start]]
        while frontier:
            nxt = []
            for route in frontier:
                for step in graph.get(route[-1], ()):
                    if step == end:
                        return route + [step]
                    if step not in seen:
                        seen.add(step)
                        nxt.append(route + [step])
            frontier = nxt
        return None

    out: list[Finding] = []
    for i in range(len(pulses) - 1):
        a, b = pulses[i], pulses[i + 1]
        if b in succ.get(a, set()):
            continue
        forward = walk(a, b, succ)
        if forward is not None:
            route = " -> ".join(forward)
            if any(node in pulses[:i + 1] for node in forward[1:-1]):
                # Forward-reachable only by going back around through stages the
                # animation has already visited. On screen that is a jump, not a
                # skip: the highlight leaves the branch it was in and reappears on
                # the happy path, having traversed nothing the viewer can see.
                out.append(Finding(
                    "pulse_order_doubles_back",
                    f"animation.pulses goes {a!r} -> {b!r}, but the only route is "
                    f"{route}, back through stages already highlighted; on screen the "
                    f"highlight jumps rather than advances"))
            else:
                out.append(Finding(
                    "pulse_skips_stage",
                    f"animation.pulses goes {a!r} -> {b!r}, but the diagram routes that "
                    f"as {route}, and those stages are never highlighted"))
            continue
        if walk(a, b, pred) is not None:
            out.append(Finding(
                "pulse_order_backwards",
                f"animation.pulses goes {a!r} -> {b!r}, but the edges run the other way; "
                f"the animation narrates the flow in reverse"))
        else:
            out.append(Finding(
                "pulse_order_disconnected",
                f"animation.pulses goes {a!r} -> {b!r} with no path between them, so the "
                f"highlight jumps across the diagram to a stage the edges do not reach"))

    # A source is a node the edges *use* and never point into. Isolated decorative
    # nodes -- backgrounds, group titles, a standalone "loop closed" caption -- have
    # no edges at all and are not the start of anything; counting them made every
    # cyclic flow look like it began mid-stream. And a genuinely cyclic flow has no
    # source at all, which is not a defect: `runbook -> alarm` closing an oncall loop
    # is the point of the diagram, so there is nothing to open at.
    wired = {n for e in (spec.get("edges") or []) if isinstance(e, dict)
             for n in (str(e.get("from")), str(e.get("to"))) if n in ids}
    sources = {n for n in wired if not pred.get(n)}
    if sources and pulses[0] not in sources:
        out.append(Finding(
            "pulse_starts_mid_flow",
            f"animation.pulses starts at {pulses[0]!r}, but the flow starts at "
            f"{sorted(sources)}; the animation opens partway through"))
    return out


def check_copy_length(spec: dict) -> list[Finding]:
    """The copy-length guidance from `references/spec-format.md`, as a check.

    Cosmetic by design: long labels make a worse diagram, not a broken one.
    """
    out = []
    for n in nodes_of(spec):
        if n.type == "anchor":
            continue
        if n.type != "label" and len(n.label.split()) > 4:
            out.append(Finding("label_too_long",
                               f"node {n.id!r} label {n.label!r} is "
                               f"{len(n.label.split())} words; 1-3 keeps it scannable"))
        lines = [line for line in n.body.split("\n") if line.strip()]
        if len(lines) > 4:
            out.append(Finding("body_too_long",
                               f"node {n.id!r} body has {len(lines)} lines; 1-4 fits the box"))
    return out


def check_canvas_use(spec: dict) -> list[Finding]:
    """Content should fill the canvas it declared."""
    if spec.get("background"):
        return []  # The background fills it; anchors need only name animated regions.
    nodes = [n for n in nodes_of(spec) if n.w > 0 and n.h > 0]
    if not nodes:
        return [Finding("no_nodes", "the spec declares no nodes")]
    canvas = canvas_of(spec)
    right = canvas["width"] - max(n.x2 for n in nodes)
    bottom = canvas["height"] - max(n.y2 for n in nodes)
    out = []
    if right > DEAD_MARGIN or bottom > DEAD_MARGIN:
        out.append(Finding("dead_space",
                           f"{right:.0f} px of empty canvas on the right and {bottom:.0f} px "
                           f"at the bottom; the diagram floats in the frame"))
    return out


#: A codepoint in the Private Use Area. No text font ships a glyph for it, so
#: whatever FreeType draws for it *is* this font's "missing glyph" box — which
#: makes it the reference every other character is compared against.
_MISSING_PROBE = ""
#: Characters every spec has in it that are certainly covered. Used to prove the
#: probe works before trusting a negative result: if `A` also matches the missing
#: box, the comparison is broken and the whole check must stay quiet rather than
#: report every character in the diagram as unsupported.
_PRESENT_PROBE = "A"


def _renderer_module():
    """The skill's renderer, imported by path.

    By path rather than as a package because `scripts/` is not one, and lazily
    because it imports Pillow — which the interpreter running this check is not
    guaranteed to have. A missing Pillow must make this check silent, not fatal.
    """
    import importlib.util

    path = os.path.join(HERE, "..", "scripts", "render_animated_webp.py")
    spec = importlib.util.spec_from_file_location("_vfw_renderer", os.path.abspath(path))
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_glyph_coverage(spec: dict) -> list[Finding]:
    """No text may use a character the display font cannot draw.

    This is the defect that costs nothing to make and cannot be seen from a spec:
    the bundled hand-drawn face has no glyph for `→`, `✓`, `✗` or `×`, so a
    subtitle reading "S3 → Glue → Parquet → Athena" renders as "S3 □ Glue □
    Parquet □ Athena". Nothing errors, `--check` passes, and the diagram ships
    with boxes in its title. It was found that way, in a scored run.

    Coverage is probed rather than guessed at from a denylist. FreeType maps every
    missing codepoint to the same `.notdef` glyph, so a character whose rendered
    mask is identical to that of an unassigned Private Use codepoint is a character
    this font does not have — which stays correct when the font changes, and needs
    no font library to ask.
    """
    try:
        renderer = _renderer_module()
        from PIL import Image, ImageDraw
    except Exception:  # noqa: BLE001 - Pillow absent, or the renderer moved
        return []

    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))

    def mask(ch: str, font) -> bytes:
        try:
            return bytes(font.getmask(ch, mode="L"))
        except Exception:  # noqa: BLE001
            return b""

    texts: list[tuple[str, str]] = []
    title = spec.get("title") or {}
    if isinstance(title, dict) and not spec.get("background"):
        for field in ("text", "highlight", "subtitle"):
            if title.get(field):
                texts.append((f"title.{field}", str(title[field])))
    for n in nodes_of(spec):
        if n.type == "anchor":
            continue
        for field, value in (("label", n.label), ("body", n.body)):
            if value:
                texts.append((f"{n.id}.{field}", value))
    for i, edge in enumerate(spec.get("edges") or []):
        if isinstance(edge, dict) and edge.get("label") and edge.get("draw_path", not bool(spec.get("background"))):
            texts.append((f"edge[{i}].label", str(edge["label"])))

    out: list[Finding] = []
    seen: set[str] = set()
    for where, text in texts:
        font = renderer.get_font(20, text, display=True)
        missing_mask = mask(_MISSING_PROBE, font)
        if not missing_mask or mask(_PRESENT_PROBE, font) == missing_mask:
            # The probe cannot distinguish present from absent with this font, so
            # any answer it gives is noise. Say nothing.
            continue
        for ch in text:
            if ch in seen or ch.isspace() or ch.isalnum():
                continue
            if mask(ch, font) == missing_mask:
                seen.add(ch)
                out.append(Finding("unsupported_glyph",
                                   f"{where} uses {ch!r} (U+{ord(ch):04X}), which the display "
                                   f"font has no glyph for; it renders as an empty box. Write "
                                   f"the word instead."))
    # `draw` exists only to keep Pillow's font machinery happy on old versions.
    del draw
    return out


def check_title(spec: dict) -> list[Finding]:
    if spec.get("background"):
        return []  # The source image owns its title and typography.
    title = spec.get("title") or {}
    text = (title.get("text") if isinstance(title, dict) else str(title)) or ""
    if not str(text).strip():
        return [Finding("title_missing", "the spec has no title text")]
    return []


def check_theme(spec: dict, expected: str | None) -> list[Finding]:
    """Light by default, dark only on request — as the case declared it."""
    if not expected:
        return []
    actual = _theme_name(spec) or "light"
    if actual != expected:
        return [Finding("theme_mismatch",
                        f"the case asked for the {expected!r} theme; the spec renders "
                        f"{actual!r}")]
    return []


def _signature(spec: dict) -> tuple:
    nodes = nodes_of(spec)
    return (
        tuple(sorted(n.id for n in nodes)),
        tuple(sorted(n.label for n in nodes if n.label)),
    )


def check_not_shipped_asset(spec: dict) -> list[Finding]:
    """The spec must describe the user's system, not the bundled example's.

    Compared on node ids *and* labels: renaming the title while leaving
    `intake -> triage -> reply / Support Memory / Signal Workbench` in place is
    the same defect wearing a hat.
    """
    ids, labels = _signature(spec)
    for path in SHIPPED_SPECS:
        if not os.path.isfile(path):
            continue
        ship_ids, ship_labels = _signature(load(path))
        name = os.path.basename(path)
        if ids and ids == ship_ids:
            return [Finding("unrendered_template",
                            f"every node id matches the shipped {name}; the example was "
                            f"rendered instead of a spec written for this request")]
        shared = set(labels) & set(ship_labels)
        if labels and len(shared) >= max(3, len(ship_labels) // 2):
            return [Finding("unrendered_template",
                            f"{len(shared)} node labels are verbatim from the shipped {name} "
                            f"({', '.join(sorted(shared)[:4])}...); the example was edited "
                            f"rather than replaced")]
    return []


def analyze(spec_path: str, expected_theme: str | None = None) -> list[Finding]:
    """Every finding for one spec, cheapest check first."""
    spec = load(spec_path)
    findings: list[Finding] = []
    findings += check_not_shipped_asset(spec)
    findings += check_frame(spec)
    findings += check_overlaps(spec)
    findings += check_wiring(spec)
    findings += check_animation(spec)
    findings += check_pulse_order(spec)
    findings += check_steps_order(spec)
    findings += check_icons(spec)
    findings += check_text_fits(spec)
    findings += check_glyph_coverage(spec)
    findings += check_theme(spec, expected_theme)
    findings += check_title(spec)
    findings += check_copy_length(spec)
    findings += check_canvas_use(spec)
    return findings
