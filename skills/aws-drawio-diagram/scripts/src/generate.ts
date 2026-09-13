/**
 * generate.ts — turn a diagram spec (JSON) into AWS-styled Drawio XML.
 *
 * Usage:
 *   npx tsx src/generate.ts <spec.json> [out.drawio]
 *
 * If [out.drawio] is omitted (or "-"), the XML is written to stdout.
 *
 * The output is a *document*, not a bare canvas: title block, numbered callouts
 * pinned to what they annotate, a prose panel down the right, and a footer. That
 * shape is what distinguishes an AWS reference architecture from a topology
 * drawing, and it is why the spec carries `subtitle` / `intent` / `annotations`
 * alongside the nodes.
 */

import { readFileSync, writeFileSync } from "node:fs";
import process from "node:process";
import type { AnnotationSpec, DiagramSpec, EdgeSpec, GroupKind } from "./types";
import {
  AZ_FOOT_STYLE,
  BADGE_STYLE,
  NOTE_STYLE,
  PANEL_STYLE,
  TEXT_STYLES,
  actorStyle,
  groupStyle,
  isKnownType,
  isKnownCategory,
  CATEGORY_NAMES,
  resourceStyle,
} from "./catalog";
import { VALID_STENCILS, loaded as stencilsLoaded, suggest } from "./stencils";

const NODE_W = 78;
const NODE_H = 78;
/** Vertical room under an icon for its label; two lines at 12 pt plus leading. */
const LABEL_H = 34;
const GAP = 46;
const ROW_GAP = 34;
const PAD_X = 30;
const HEADER = 44;
const PAD_BOTTOM = 30;
/** Extra bottom padding for an AZ band, which repeats its label there. */
const AZ_PAD_BOTTOM = 46;
const ROOT_ORIGIN_X = 40;
const RESERVED_IDS = new Set(["0", "1"]);

/**
 * Wrap a container's children onto a new row past this width.
 *
 * Without it, a container laid its children out in one unbounded row: eight
 * nodes became a 1400 px strip, the page grew to match, and every label shrank
 * relative to the canvas until the render was unreadable. Wrapping trades width
 * for height, which is the cheaper direction on a page that is already wider
 * than it is tall.
 */
const MAX_ROW_W = 760;

// --- Document frame -------------------------------------------------------
const MARGIN = 40;
const TITLE_Y = 26;
const TITLE_H = 40;
const SUBTITLE_H = 28;
const INTENT_H = 22;
const RULE_GAP = 14;
const CANVAS_GAP = 26;
const PANEL_W = 330;
const PANEL_PAD = 16;
const BADGE = 26;
const NOTE_LINE_H = 15;
/** Characters that fit one panel line at 12 pt in the width left beside a badge. */
const NOTE_CHARS_PER_LINE = 46;
const FOOTER_GAP = 30;
const FOOTER_H = 30;

interface Item {
  kind: "node" | "group" | "actor";
  id: string;
  label: string;
  type?: string;
  gkind?: GroupKind;
  /** AWS category for icon fill; only used when `type` is a raw stencil slug. */
  category?: string;
  children: Item[];
  x: number;
  y: number;
  w: number;
  h: number;
  /** Horizontal room this item claims from its parent, including label overhang. */
  slotW: number;
  /** Vertical room, including the label under an icon. */
  slotH: number;
  /** Absolute position on the page, filled in after relative layout. */
  absX: number;
  absY: number;
}

function esc(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

function fail(msg: string): never {
  console.error(`generate: ${msg}`);
  process.exit(1);
}

/** Rough drawn width of a label at 12 pt, used only to reserve space. */
function labelWidth(label: string): number {
  return Math.ceil(label.length * 6.6) + 8;
}

function validateSpec(spec: DiagramSpec): void {
  if (!spec || typeof spec !== "object") fail("spec is not an object");
  if (!spec.title) fail("spec.title is required");
  if (!Array.isArray(spec.nodes) || spec.nodes.length === 0) {
    fail("spec.nodes must be a non-empty array");
  }
  const ids = new Set<string>();
  const register = (id: string, what: string) => {
    if (!id) fail(`${what} is missing an id`);
    if (RESERVED_IDS.has(id)) fail(`id "${id}" is reserved (do not use "0" or "1")`);
    if (ids.has(id)) fail(`duplicate id "${id}"`);
    ids.add(id);
  };
  for (const g of spec.groups ?? []) register(g.id, "group");
  for (const n of spec.nodes) {
    register(n.id, "node");
    if (!n.label || !n.label.trim()) fail(`node "${n.id}" has an empty label`);
    if (!n.type) fail(`node "${n.id}" is missing a type`);
  }
  const groupIds = new Set((spec.groups ?? []).map((g) => g.id));
  for (const g of spec.groups ?? []) {
    if (g.parent && !groupIds.has(g.parent)) fail(`group "${g.id}" references unknown parent "${g.parent}"`);
  }
  for (const n of spec.nodes) {
    if (n.group && !groupIds.has(n.group)) fail(`node "${n.id}" references unknown group "${n.group}"`);
    if (n.external && n.group) {
      fail(
        `node "${n.id}" is external but sits in group "${n.group}" -- a non-AWS ` +
          `actor belongs outside every AWS container; drop \`group\``,
      );
    }
  }
  for (const e of spec.edges ?? []) {
    if (!ids.has(e.source)) fail(`edge references unknown source "${e.source}"`);
    if (!ids.has(e.target)) fail(`edge references unknown target "${e.target}"`);
  }
  for (const [i, a] of (spec.annotations ?? []).entries()) {
    if (!a.text || !a.text.trim()) fail(`annotation ${i + 1} has empty text`);
    if (a.target && !ids.has(a.target)) {
      fail(`annotation ${i + 1} targets unknown id "${a.target}"`);
    }
  }
}

/** Build the forest of grouping containers + nodes, preserving spec order. */
function buildForest(spec: DiagramSpec): { actors: Item[]; roots: Item[] } {
  const items = new Map<string, Item>();
  const blank = { children: [] as Item[], x: 0, y: 0, w: 0, h: 0, slotW: 0, slotH: 0, absX: 0, absY: 0 };

  for (const g of spec.groups ?? []) {
    items.set(g.id, { ...blank, children: [], kind: "group", id: g.id, label: g.label, gkind: g.kind });
  }
  for (const n of spec.nodes) {
    items.set(n.id, {
      ...blank,
      children: [],
      kind: n.external ? "actor" : "node",
      id: n.id,
      label: n.label,
      type: n.type,
      category: n.category,
    });
  }

  const actors: Item[] = [];
  const roots: Item[] = [];
  // Groups first (in spec order), then nodes — keeps containers laid out before leaf icons.
  for (const g of spec.groups ?? []) {
    const item = items.get(g.id)!;
    if (g.parent) items.get(g.parent)!.children.push(item);
    else roots.push(item);
  }
  for (const n of spec.nodes) {
    const item = items.get(n.id)!;
    if (n.group) items.get(n.group)!.children.push(item);
    else if (n.external) actors.push(item);
    else roots.push(item);
  }
  return { actors, roots };
}

/**
 * Recursively size each item and place its children relative to it.
 *
 * Each child claims a *slot* at least as wide as its label rather than as wide
 * as its icon. A 78 px icon under "Application Load Balancer" draws ~170 px of
 * text, so packing on icon width alone put every long label through its
 * neighbour's — the single most visible defect in the previous output.
 */
function sizeAndPlace(item: Item): void {
  if (item.kind === "node" || item.kind === "actor") {
    item.w = NODE_W;
    item.h = NODE_H;
    item.slotW = Math.max(NODE_W, labelWidth(item.label));
    item.slotH = NODE_H + LABEL_H;
    return;
  }

  for (const child of item.children) sizeAndPlace(child);

  // Loose icons go in rows above the containers, never beside them.
  //
  // Packing them in one sequence put a Region's CloudFront *below* its VPC: the
  // VPC is wider than MAX_ROW_W on its own, so it filled a row and every
  // following sibling wrapped underneath it. That leaves the edge-facing service
  // at the bottom of the page with its connectors crossing the whole canvas.
  // Splitting the two also matches how the reference draws shared services --
  // a row of them above the compute box, not interleaved with it.
  const leaves = item.children.filter((c) => c.kind !== "group");
  const containers = item.children.filter((c) => c.kind === "group");

  const pack = (items: Item[]): Item[][] => {
    const rows: Item[][] = [];
    let row: Item[] = [];
    let rowW = 0;
    for (const child of items) {
      if (row.length && rowW + GAP + child.slotW > MAX_ROW_W) {
        rows.push(row);
        row = [];
        rowW = 0;
      }
      row.push(child);
      rowW += (row.length > 1 ? GAP : 0) + child.slotW;
    }
    if (row.length) rows.push(row);
    return rows;
  };

  const rows = [...pack(leaves), ...pack(containers)];

  const isAz = item.gkind === "az";
  const padBottom = isAz ? AZ_PAD_BOTTOM : PAD_BOTTOM;

  let y = HEADER;
  let widest = 0;
  for (const r of rows) {
    let x = PAD_X;
    let rowH = 0;
    for (const child of r) {
      // Centre the icon in its slot so the label is centred too; a container
      // fills its slot and the offset is zero.
      child.x = x + (child.slotW - child.w) / 2;
      child.y = y;
      x += child.slotW + GAP;
      rowH = Math.max(rowH, child.slotH);
    }
    widest = Math.max(widest, x - GAP);
    y += rowH + ROW_GAP;
  }

  const contentH = rows.length ? y - ROW_GAP - HEADER : 40;
  item.w = Math.max(widest + PAD_X, labelWidth(item.label) + PAD_X * 2, 200);
  item.h = HEADER + contentH + padBottom;
  item.slotW = item.w;
  item.slotH = item.h;
}

/** Fill in absolute page coordinates, which the badges need. */
function absolutise(item: Item, ox: number, oy: number): void {
  item.absX = ox + item.x;
  item.absY = oy + item.y;
  for (const child of item.children) absolutise(child, item.absX, item.absY);
}

function cellXml(
  id: string,
  value: string,
  style: string,
  x: number,
  y: number,
  w: number,
  h: number,
  parentId: string,
): string {
  // Inline markup in `value` stays XML-escaped. Drawio decodes the attribute and
  // then, because every style here sets `html=1`, renders the result as HTML --
  // so `&lt;b&gt;` reaches the canvas as bold. Emitting a raw `<b>` instead
  // breaks the document at the XML layer.
  return (
    `        <mxCell id="${esc(id)}" value="${esc(value)}" ` +
    `style="${esc(style)}" vertex="1" parent="${esc(parentId)}">\n` +
    `          <mxGeometry x="${Math.round(x)}" y="${Math.round(y)}" ` +
    `width="${Math.round(w)}" height="${Math.round(h)}" as="geometry" />\n` +
    `        </mxCell>`
  );
}

function emit(item: Item, parentId: string, out: string[]): void {
  const style =
    item.kind === "group"
      ? groupStyle(item.gkind!)
      : item.kind === "actor"
        ? actorStyle(item.type!)
        : resourceStyle(item.type!, item.category);
  out.push(cellXml(item.id, item.label, style, item.x, item.y, item.w, item.h, parentId));

  // An AZ repeats its label at the bottom of the band, so a component sitting in
  // the middle of a tall zone can still be read as belonging to it.
  if (item.gkind === "az") {
    out.push(
      cellXml(`${item.id}-foot`, item.label, AZ_FOOT_STYLE, PAD_X, item.h - 34,
              item.w - PAD_X * 2, 22, item.id),
    );
  }

  for (const child of item.children) emit(child, item.id, out);
}

function edgeCell(edge: EdgeSpec, index: number): string {
  const style =
    "edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;endArrow=classic;startArrow=none;" +
    "strokeWidth=1;strokeColor=#545B64;fontColor=#232F3E;fontSize=11;" +
    // Opaque label background: an edge label crossing an icon or a container
    // border was previously unreadable, and the reference leaves most edges
    // unlabelled precisely to avoid that crowding.
    "labelBackgroundColor=#FFFFFF;" +
    `${edge.dashed ? "dashed=1;" : ""}`;
  return (
    `        <mxCell id="edge-${index}" value="${esc(edge.label ?? "")}" style="${esc(style)}" edge="1" parent="1" source="${esc(edge.source)}" target="${esc(edge.target)}">\n` +
    `          <mxGeometry relative="1" as="geometry" />\n` +
    `        </mxCell>`
  );
}

function noteHeight(text: string): number {
  const plain = text.replace(/<[^>]+>/g, "");
  const lines = Math.max(1, Math.ceil(plain.length / NOTE_CHARS_PER_LINE));
  return Math.max(BADGE, lines * NOTE_LINE_H + 6);
}

/** Panel height needed for these notes, so the canvas and panel can be balanced. */
function panelHeight(annotations: AnnotationSpec[]): number {
  let h = PANEL_PAD;
  for (const a of annotations) h += noteHeight(a.text) + PANEL_PAD;
  return h;
}

function toXml(spec: DiagramSpec): string {
  const { actors, roots } = buildForest(spec);
  const annotations = spec.annotations ?? [];

  // --- header block ---
  let y = TITLE_Y;
  const header: string[] = [];
  header.push(cellXml("doc-title", spec.title, TEXT_STYLES.title, MARGIN, y, 1100, TITLE_H, "1"));
  y += TITLE_H;
  if (spec.subtitle) {
    header.push(cellXml("doc-subtitle", spec.subtitle, TEXT_STYLES.subtitle, MARGIN, y, 1100, SUBTITLE_H, "1"));
    y += SUBTITLE_H;
  }
  if (spec.intent) {
    header.push(cellXml("doc-intent", spec.intent, TEXT_STYLES.intent, MARGIN, y + 4, 1000, INTENT_H, "1"));
    y += INTENT_H + 4;
  }
  const ruleY = y + RULE_GAP;
  const canvasY = ruleY + CANVAS_GAP;

  // --- canvas: actors in a column at the far left, then containers in a row ---
  let cx = ROOT_ORIGIN_X;
  let canvasBottom = canvasY;

  if (actors.length) {
    let ay = canvasY;
    let widest = 0;
    for (const actor of actors) {
      sizeAndPlace(actor);
      widest = Math.max(widest, actor.slotW);
    }
    for (const actor of actors) {
      // Centre each actor in the column so a short label under a wide one still
      // lines up, and place the icon rather than the slot.
      actor.x = cx + (widest - actor.w) / 2;
      actor.y = ay;
      ay += actor.slotH + ROW_GAP;
    }
    canvasBottom = Math.max(canvasBottom, ay - ROW_GAP);
    cx += widest + GAP * 2; // wider gutter: this is the cloud boundary crossing
  }

  for (const root of roots) {
    sizeAndPlace(root);
    root.x = cx;
    root.y = canvasY;
    cx += root.slotW + GAP;
    canvasBottom = Math.max(canvasBottom, root.y + root.slotH);
  }
  const canvasRight = cx - GAP;

  for (const item of [...actors, ...roots]) absolutise(item, 0, 0);

  // --- annotation panel, to the right of the canvas ---
  const panelX = canvasRight + GAP;
  const panelH = annotations.length ? Math.max(panelHeight(annotations), canvasBottom - canvasY) : 0;
  const frame: string[] = [];
  const overlay: string[] = [];

  if (annotations.length) {
    frame.push(cellXml("doc-panel", "", PANEL_STYLE, panelX, canvasY, PANEL_W, panelH, "1"));
    let ny = canvasY + PANEL_PAD;
    const byId = new Map<string, Item>();
    const index = (item: Item) => {
      byId.set(item.id, item);
      item.children.forEach(index);
    };
    [...actors, ...roots].forEach(index);

    annotations.forEach((a, i) => {
      const n = String(a.n ?? i + 1);
      const h = noteHeight(a.text);
      frame.push(cellXml(`note-badge-${n}`, n, BADGE_STYLE, panelX + PANEL_PAD, ny, BADGE, BADGE, "1"));
      frame.push(
        cellXml(`note-text-${n}`, a.text, NOTE_STYLE,
                panelX + PANEL_PAD + BADGE + 10, ny, PANEL_W - PANEL_PAD * 2 - BADGE - 10, h, "1"),
      );
      ny += h + PANEL_PAD;

      // The canvas badge is pinned to the target's top-left corner and parented to
      // the root layer, not to the target's container: badges are emitted last so
      // they draw over the container borders they straddle, exactly as a callout
      // on the reference sits on the EKS boundary.
      const target = a.target ? byId.get(a.target) : undefined;
      if (target) {
        overlay.push(
          cellXml(`badge-${n}`, n, BADGE_STYLE,
                  target.absX - BADGE / 2, target.absY - BADGE / 2, BADGE, BADGE, "1"),
        );
      }
    });
  }

  const contentRight = annotations.length ? panelX + PANEL_W : canvasRight;
  const contentBottom = Math.max(canvasBottom, annotations.length ? canvasY + panelH : 0);

  frame.push(cellXml("doc-rule", "", TEXT_STYLES.rule, MARGIN, ruleY, contentRight - MARGIN, 10, "1"));

  // --- footer ---
  const footerY = contentBottom + FOOTER_GAP;
  frame.push(cellXml("doc-footer-rule", "", TEXT_STYLES.rule, MARGIN, footerY, contentRight - MARGIN, 10, "1"));
  const reviewed = spec.reviewed
    ? `Reviewed for technical accuracy ${spec.reviewed}`
    : "Reviewed for technical accuracy";
  frame.push(
    cellXml("doc-footer-left", `${reviewed}<br>\u00a9 ${
      (spec.reviewed ?? "").slice(0, 4) || ""
    } Amazon Web Services, Inc. or its affiliates. All rights reserved.`,
            TEXT_STYLES.footerLeft, MARGIN, footerY + 10, 460, FOOTER_H, "1"),
  );
  frame.push(
    cellXml("doc-footer-centre", "AWS Reference Architecture", TEXT_STYLES.footerCentre,
            MARGIN, footerY + 10, contentRight - MARGIN, FOOTER_H, "1"),
  );

  const cells: string[] = [...header, ...frame];
  for (const item of [...actors, ...roots]) emit(item, "1", cells);
  (spec.edges ?? []).forEach((edge, i) => cells.push(edgeCell(edge, i)));
  // Badges last: later cells draw on top.
  cells.push(...overlay);

  const pageW = Math.round(contentRight + MARGIN);
  const pageH = Math.round(footerY + FOOTER_H + MARGIN);

  return (
    `<mxfile host="app.diagrams.net" type="device">\n` +
    `  <diagram id="aws-diagram" name="${esc(spec.title)}">\n` +
    `    <mxGraphModel dx="900" dy="640" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="${pageW}" pageHeight="${pageH}" math="0" shadow="0">\n` +
    `      <root>\n` +
    `        <mxCell id="0" />\n` +
    `        <mxCell id="1" parent="0" />\n` +
    `${cells.join("\n")}\n` +
    `      </root>\n` +
    `    </mxGraphModel>\n` +
    `  </diagram>\n` +
    `</mxfile>\n`
  );
}

function main(): void {
  const specPath = process.argv[2];
  const outPath = process.argv[3];
  if (!specPath) fail("usage: tsx src/generate.ts <spec.json> [out.drawio]");

  let spec: DiagramSpec;
  try {
    spec = JSON.parse(readFileSync(specPath, "utf8")) as DiagramSpec;
  } catch (err) {
    fail(`could not read/parse spec "${specPath}": ${(err as Error).message}`);
  }

  validateSpec(spec);

  // Warn (non-fatal) about node types the catalog does not cover.
  for (const n of spec.nodes) {
    // Only when the resolved slug is not a real stencil. Catalog membership on its
    // own is not a problem -- a raw slug is a documented escape hatch -- and noting
    // it unconditionally printed `type "users" not in catalog` on every run of two
    // shipped examples, which is how a warning stops being read. The failure worth a
    // line here is the slug that resolves to nothing and renders blank.
    const slug = n.type.startsWith("mxgraph.aws4.")
      ? n.type.slice("mxgraph.aws4.".length)
      : n.type;
    if (!isKnownType(n.type) && stencilsLoaded && !VALID_STENCILS.has(slug)) {
      const hint = suggest(slug);
      console.error(
        `generate: note — type "${n.type}" is neither a catalog key nor an AWS4 ` +
        `stencil; it will render as a blank square` +
        (hint ? ` — did you mean "${hint}"?` : " — check scripts/aws4-stencils.txt"));
    }
    // An unrecognised `category` is worse than an unrecognised `type`, because it
    // fails silently: `resourceStyle` falls back to `general`, the icon renders in
    // #232F3E dark navy, and the diagram loses "colour carries category" for that
    // one service while passing every text-level check. Catalogued types ignore
    // `category` outright -- the catalog's own value wins -- so say that too, or a
    // spec author sets `category` on `s3`, sees green, and concludes the field does
    // nothing.
    if (n.category !== undefined && !isKnownCategory(n.category)) {
      console.error(
        `generate: note — node "${n.id}" has category "${n.category}", which is not ` +
        `an AWS category; its icon will render dark navy (general). Valid: ` +
        CATEGORY_NAMES.join(", "));
    } else if (n.category !== undefined && isKnownType(n.type)) {
      console.error(
        `generate: note — node "${n.id}" sets category "${n.category}" but type ` +
        `"${n.type}" is in the catalog, which supplies its own category; the field ` +
        `is ignored here and only applies to raw mxgraph.aws4.* slugs`);
    }
  }

  const xml = toXml(spec);

  if (!outPath || outPath === "-") {
    process.stdout.write(xml);
  } else {
    writeFileSync(outPath, xml, "utf8");
    console.error(`generate: wrote ${outPath}`);
  }
}

main();
