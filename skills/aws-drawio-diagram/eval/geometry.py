"""Layout defects computable from a .drawio file's geometry, without rendering.

The skill's own `validate.ts` reads the XML as text, so it verifies structure
(icon library, grouping, labels, connectivity) and says so in its header. What it
cannot see is where anything ended up, and that is where this project's real
diagram defects live: a node that left its container, two labels written on top
of each other, a container sized for shapes but not for the label text hanging
below them.

None of that needs pixels. mxGraph records `x/y/width/height` per cell, child
coordinates relative to the parent, so containment and overlap are arithmetic.
Rendering is still worth doing -- it catches missing icons and visual weight,
which arithmetic cannot -- but it costs a browser and a vision model, and
everything here is exact and free. Run this first; it also gives the vision judge
something to be checked against.

Coordinates are resolved to absolute page space before comparison, because a
cell's geometry means nothing without its ancestors' offsets.
"""

from __future__ import annotations

# defusedxml, not the stdlib parser: the .drawio files parsed here are produced
# by the skill under evaluation, i.e. semi-trusted input, and defusedxml blocks
# entity-expansion and external-entity (XXE) attacks a raw ElementTree allows.
import defusedxml.ElementTree as ET
from dataclasses import dataclass, field

# A label placed under a shape (`verticalLabelPosition=bottom`) occupies space
# the shape's own height does not account for. These are draw.io's defaults for
# the AWS shape style the skill emits, used to grow a node's effective box so
# label collisions and container overflow are measured against what is drawn
# rather than against the icon alone.
LABEL_LINE_HEIGHT = 14.0
# Per-character width at fontSize=12, matching `generate.ts:labelWidth` exactly so
# the generator's space reservation and this estimate cannot disagree.
LABEL_CHAR_W = 6.6
LABEL_PAD = 8.0
# One line of edge-label text at fontSize=11, for `check_edge_label_overlaps`.
EDGE_LABEL_H = 16.0
# Sub-pixel overlaps are rounding, not defects. A real collision is legible.
OVERLAP_TOLERANCE = 2.0
# Label extents are estimated, not measured (see `Box.label_box`), so a pair a
# few pixels apart is not evidence of clearance -- draw.io's real wrapping can
# push it either way. Reported as a separate, weaker finding rather than folded
# into the overlap threshold: tightening the threshold until a known-bad pair
# fires would be fitting the metric to one diagram, and calling a 2 px gap clean
# would be claiming precision this estimate does not have. This band is the
# honest answer, and it is the case for keeping a rendered check as well.
LABEL_NEAR_MARGIN = 24.0
# Blank space inside a container that is worth reporting. A container is allowed
# slack for its own title and padding; several times that is a sizing bug.
CONTAINER_SLACK_LIMIT = 120.0


@dataclass
class Box:
    id: str
    label: str
    x: float
    y: float
    w: float
    h: float
    parent: str | None
    is_container: bool
    has_bottom_label: bool
    #: Document furniture -- title, rule, footer, annotation panel, callout
    #: badges -- rather than a piece of the architecture.
    is_chrome: bool = False

    @property
    def x2(self) -> float:
        return self.x + self.w

    @property
    def y2(self) -> float:
        return self.y + self.h

    def label_box(self) -> tuple[float, float, float, float]:
        """The drawn extent, including a bottom-positioned label.

        The label is centred on the shape and drawn on **one line**, so it is often
        much wider than the icon and extends one line-height below it. Approximated
        rather than measured -- exact text metrics need a font engine -- but the
        model matters more than the precision, and this one was wrong.

        It previously assumed the label wrapped every 18 characters, capping its
        estimated width at 126 px. `catalog.resourceStyle` sets no `whiteSpace=wrap`,
        so draw.io does not wrap these labels at all: every bottom-labelled cell in
        the generator's output renders on a single line. The consequence was not a
        missed defect but an invented one -- `check_container_slack` reported 123 px
        of dead space to the right of a 372 px subnet holding one NLB, when the
        render shows "Elastic Load Balancing (Network Load Balancer)" filling that
        space on one line and the container sized correctly around it. The vision
        judge, handed the finding, then "confirmed" the dead space and docked two
        runs for it. A false positive is not the harmless direction of error: it
        spends the judge's attention and its authority on something that is not
        there.

        If `whiteSpace=wrap` is ever added to that style, this flips back to a
        wrapping model -- the two have to be kept in step deliberately.
        """
        if not self.has_bottom_label or not self.label:
            return (self.x, self.y, self.x2, self.y2)
        text_w = max(self.w, len(self.label) * LABEL_CHAR_W + LABEL_PAD)
        cx = self.x + self.w / 2
        return (cx - text_w / 2, self.y, cx + text_w / 2, self.y2 + LABEL_LINE_HEIGHT)


@dataclass
class Finding:
    kind: str
    detail: str
    ids: list[str] = field(default_factory=list)


def _f(value: str | None, default: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def parse_boxes(path: str) -> tuple[dict[str, Box], dict[str, str | None]]:
    """Absolute-positioned vertex boxes, plus the declared parent of each cell.

    Edges are skipped: they carry no geometry in this generator's output, and an
    edge's routing is a rendering question rather than an arithmetic one.
    """
    root = ET.parse(path).getroot().find(".//root")
    if root is None:
        raise ValueError(f"{path}: no <root> element; not an mxGraph document")

    raw: dict[str, dict] = {}
    parents: dict[str, str | None] = {}
    for cell in root:
        cid = cell.get("id")
        if cid is None:
            continue
        parents[cid] = cell.get("parent")
        if cell.get("edge") == "1" or cell.get("vertex") != "1":
            continue
        geo = cell.find("mxGeometry")
        if geo is None:
            continue
        style = cell.get("style") or ""
        raw[cid] = {
            "label": (cell.get("value") or "").strip(),
            "x": _f(geo.get("x")),
            "y": _f(geo.get("y")),
            "w": _f(geo.get("width")),
            "h": _f(geo.get("height")),
            "parent": cell.get("parent"),
            "is_container": "container=1" in style,
            "has_bottom_label": "verticalLabelPosition=bottom" in style,
            # A cell is part of the architecture if it draws an AWS shape or holds
            # other cells. Everything else -- the title block, the horizontal
            # rules, the footer, the grey annotation panel and the numbered badges
            # sitting on top of it -- is document furniture.
            #
            # The distinction has to exist because these checks are about whether
            # the *architecture* is drawn legibly. A callout badge is supposed to
            # overlap the panel it sits on and the container it points at; scored
            # as a node collision it reports five defects for a feature working as
            # designed, and burying the real findings is how a check stops being
            # read.
            "is_chrome": "mxgraph.aws4." not in style and "container=1" not in style,
        }

    def absolute(cid: str, seen: frozenset[str] = frozenset()) -> tuple[float, float]:
        """Offset of a cell's parent chain. Cycles yield (0, 0) rather than
        recursing forever -- a malformed document should be reported, not hang."""
        parent = raw.get(cid, {}).get("parent")
        if parent is None or parent not in raw or cid in seen:
            return (0.0, 0.0)
        px, py = absolute(parent, seen | {cid})
        return (px + raw[parent]["x"], py + raw[parent]["y"])

    boxes: dict[str, Box] = {}
    for cid, r in raw.items():
        ox, oy = absolute(cid)
        boxes[cid] = Box(id=cid, label=r["label"], x=r["x"] + ox, y=r["y"] + oy,
                         w=r["w"], h=r["h"], parent=r["parent"],
                         is_container=r["is_container"],
                         has_bottom_label=r["has_bottom_label"],
                         is_chrome=r["is_chrome"])
    return boxes, parents


def check_containment(boxes: dict[str, Box]) -> list[Finding]:
    """Children that stick out of the container they were declared inside."""
    out = []
    for box in boxes.values():
        parent = boxes.get(box.parent or "")
        if parent is None or not parent.is_container:
            continue
        lx, ly, lx2, ly2 = box.label_box()
        over = {
            "left": parent.x - lx, "top": parent.y - ly,
            "right": lx2 - parent.x2, "bottom": ly2 - parent.y2,
        }
        worst = {k: round(v) for k, v in over.items() if v > OVERLAP_TOLERANCE}
        if worst:
            out.append(Finding(
                "node_escapes_container",
                f"{box.id!r} ({box.label[:40]!r}) overflows {parent.id!r} by {worst} px",
                [box.id, parent.id],
            ))
    return out


def check_orphans(boxes: dict[str, Box], expected_group: dict[str, str]) -> list[Finding]:
    """Nodes the spec assigned to a group that were emitted outside it.

    This is the defect the .drawio text passes and the picture fails: the cell is
    well-formed and carries a valid AWS icon, so a structural validator is happy,
    but it was parented to the page instead of to its group and lands wherever
    its absolute coordinates put it.
    """
    out = []
    for node_id, group_id in expected_group.items():
        box = boxes.get(node_id)
        if box is None:
            out.append(Finding("node_missing", f"spec node {node_id!r} is not in the diagram", [node_id]))
            continue
        if box.parent != group_id:
            out.append(Finding(
                "node_lost_its_group",
                f"{node_id!r} should be inside group {group_id!r} but its parent is "
                f"{box.parent!r}",
                [node_id],
            ))
    return out


def check_label_overlaps(boxes: dict[str, Box]) -> list[Finding]:
    """Pairs of sibling nodes whose drawn labels intersect."""
    out = []
    leaves = [b for b in boxes.values() if not b.is_container]
    for i, a in enumerate(leaves):
        for b in leaves[i + 1:]:
            if a.parent != b.parent:
                continue
            ax, ay, ax2, ay2 = a.label_box()
            bx, by, bx2, by2 = b.label_box()
            dx = min(ax2, bx2) - max(ax, bx)
            dy = min(ay2, by2) - max(ay, by)
            if dy <= OVERLAP_TOLERANCE:
                continue  # different rows; horizontal proximity is irrelevant
            if dx > OVERLAP_TOLERANCE:
                out.append(Finding(
                    "labels_overlap",
                    f"{a.id!r} and {b.id!r} labels overlap by {round(dx)}x{round(dy)} px "
                    f"({a.label[:24]!r} / {b.label[:24]!r})",
                    [a.id, b.id],
                ))
            elif dx > -LABEL_NEAR_MARGIN:
                out.append(Finding(
                    "labels_nearly_overlap",
                    f"{a.id!r} and {b.id!r} labels are {round(-dx)} px apart, inside the "
                    f"{LABEL_NEAR_MARGIN:.0f} px margin of this estimate -- verify in the render "
                    f"({a.label[:24]!r} / {b.label[:24]!r})",
                    [a.id, b.id],
                ))
    return out


def parse_edge_labels(path: str) -> list[tuple[str, str, str, str]]:
    """`(id, label, source, target)` for every labelled edge."""
    root = ET.parse(path).getroot().find(".//root")
    if root is None:
        raise ValueError(f"{path}: no <root> element; not an mxGraph document")
    out = []
    for cell in root:
        if cell.get("edge") != "1":
            continue
        label = (cell.get("value") or "").strip()
        src, tgt = cell.get("source"), cell.get("target")
        if label and src and tgt:
            out.append((cell.get("id") or "?", label, src, tgt))
    return out


def check_edge_label_overlaps(path: str, boxes: dict[str, Box]) -> list[Finding]:
    """Edge labels landing on a node's own label.

    `parse_boxes` skips edges because routing is a rendering question, and that is
    still true of the *path*. It is not true of the *label*: draw.io centres an
    unpositioned edge label on the routed midpoint, which for these orthogonal
    two-segment routes is close to the midpoint of the line between the endpoints'
    centres. Close enough to check, and worth checking, because the failure is
    invisible to everything else in the stack.

    Observed: `log-pipeline-streaming` rendered "Amazon MSK (ManagedRead
    logsreaming for Apache Kafka)" -- the edge label "Read logs" sitting in a white
    box punched through the middle of the node's own label. `DiagramLayout` scored
    that run 1.00 and said "no labels collide", and a vision judge called it "minor
    crowding", because at 2234 px a chopped label still looks like a label. A
    deterministic check reporting no defect is worse than the defect: it is what
    stops anyone looking.

    Any edge arriving from below necessarily crosses the target's label band -- the
    label hangs under the icon, between it and everything beneath -- so this is
    structural, not a stray coordinate. Reported rather than fixed in the generator:
    placing labels clear of every label band needs the layout engine to reserve that
    space, which is a larger change than this check.

    The *pairing* is an estimate and the finding says so. On the run above it named
    `flink` and `redshift` while the chopped label confirmed in the render was
    `msk`'s -- all three sit in adjacent rows, and a midpoint computed from endpoint
    centres cannot tell which of a crowded row's label bands the real route passed
    through. What it establishes reliably is that this diagram has edge labels
    landing in label bands, which is the part nothing else could see; treat the named
    node as a starting point for looking, not as the answer.
    """
    out = []
    for eid, label, src, tgt in parse_edge_labels(path):
        a, b = boxes.get(src), boxes.get(tgt)
        if a is None or b is None:
            continue
        cx = (a.x + a.x2) / 2 + (b.x + b.x2) / 2
        cy = (a.y + a.y2) / 2 + (b.y + b.y2) / 2
        cx, cy = cx / 2, cy / 2
        # fontSize=11 on edges, versus 12 on node labels; the same crude per-character
        # width the node estimate uses, scaled down to match.
        half_w = max(1.0, len(label) * 6.0 / 2)
        half_h = EDGE_LABEL_H / 2
        ex, ey, ex2, ey2 = cx - half_w, cy - half_h, cx + half_w, cy + half_h
        for box in boxes.values():
            # The endpoints are deliberately *not* excluded: the target's own label
            # is the one an arriving edge collides with, which is the whole finding.
            if box.is_container:
                continue
            if not box.has_bottom_label or not box.label:
                continue
            lx, ly, lx2, ly2 = box.label_box()
            # Only the label band, not the icon: an edge crossing an icon is a
            # routing question and this check does not claim to answer it.
            ly = max(ly, box.y2)
            dx = min(ex2, lx2) - max(ex, lx)
            dy = min(ey2, ly2) - max(ey, ly)
            if dx > OVERLAP_TOLERANCE and dy > OVERLAP_TOLERANCE:
                out.append(Finding(
                    "edge_label_on_node_label",
                    f"edge label {label[:24]!r} ({eid}) lands in the label band of "
                    f"{box.id!r} ({box.label[:32]!r}), overlapping by ~{round(dx)}x"
                    f"{round(dy)} px -- a node label here renders chopped. The pair is "
                    f"estimated from endpoint centres, so the collision may be with a "
                    f"neighbour in the same row; check the render",
                    [eid, box.id],
                ))
    return out


def check_container_slack(boxes: dict[str, Box]) -> list[Finding]:
    """Containers far larger than the content they hold.

    Reported because it is the difference between a diagram that reads as
    deliberate and one with half an image of blank space, and because it is
    invisible to any check that does not do arithmetic.
    """
    out = []
    for box in boxes.values():
        if not box.is_container:
            continue
        kids = [b for b in boxes.values() if b.parent == box.id]
        if not kids:
            continue
        used_x2 = max(b.label_box()[2] for b in kids)
        used_y2 = max(b.label_box()[3] for b in kids)
        slack_r, slack_b = box.x2 - used_x2, box.y2 - used_y2
        if slack_b > CONTAINER_SLACK_LIMIT or slack_r > CONTAINER_SLACK_LIMIT:
            out.append(Finding(
                "container_oversized",
                f"{box.id!r} ({box.w:.0f}x{box.h:.0f}) leaves {slack_r:.0f} px right and "
                f"{slack_b:.0f} px bottom unused",
                [box.id],
            ))
    return out


def analyze(path: str, expected_group: dict[str, str] | None = None) -> list[Finding]:
    """Every geometry finding for one diagram, worst class first.

    Document furniture is dropped before any check runs -- see `is_chrome`. These
    checks answer "is the architecture drawn legibly", and the title block and
    annotation panel are neither architecture nor laid out by the same code.
    """
    boxes, _ = parse_boxes(path)
    boxes = {k: b for k, b in boxes.items() if not b.is_chrome}
    return [
        *check_orphans(boxes, expected_group or {}),
        *check_containment(boxes),
        *check_label_overlaps(boxes),
        *check_edge_label_overlaps(path, boxes),
        *check_container_slack(boxes),
    ]


def spec_groups(spec_path: str) -> dict[str, str]:
    """node id -> group id, from a diagram spec, for `check_orphans`."""
    import json

    with open(spec_path, encoding="utf-8") as f:
        spec = json.load(f)
    return {n["id"]: n["group"] for n in spec.get("nodes", []) if n.get("group")}
