/**
 * validate.ts — check a .drawio file against the AWS-style rules.
 *
 * Usage:
 *   npx tsx src/validate.ts <file.drawio>
 *
 * Exit code 0 = pass, 1 = fail. These are the same rules the eval suite uses,
 * so a skill run and an eval run agree on what "AWS style" means.
 *
 * This reads the XML text, not the rendered image, so it verifies structure
 * (icon library, grouping, labels, connectivity, the document frame) — not
 * visual layout. Two things it deliberately *can* catch that a reader of the
 * render cannot: an icon name that does not resolve, and a missing annotation
 * layer. Both were previously invisible to every check in the stack.
 */

import { readFileSync } from "node:fs";
import process from "node:process";

import { VALID_STENCILS, loaded as stencilsLoaded, suggest } from "./stencils";

/**
 * The aws4 library's structural shapes. These are mxgraph shape names rather than
 * icon names, so they do not appear in `aws4-stencils.txt` and must not be
 * reported as unresolvable icons.
 */
const AWS4_PRIMITIVES = new Set([
  "resourceIcon",
  "productIcon",
  "group",
  "groupCenter",
]);

/**
 * draw.io's label metrics for the styles this generator emits, as used to estimate
 * drawn extents below. Kept numerically identical to `evals/geometry.py` so the
 * skill's own validator and the eval harness cannot disagree about whether two
 * labels collide.
 */
const LABEL_LINE_HEIGHT = 14;
// A node label carries no `whiteSpace=wrap` (see `catalog.resourceStyle`), so it
// renders on one line however long it is. These are `labelWidth`'s own numbers.
const NODE_CHAR_W = 6.6; // fontSize=12 under an icon
const NODE_LABEL_PAD = 8;
const EDGE_CHAR_W = 6; // fontSize=11 on an edge
const EDGE_LABEL_H = 16;
const OVERLAP_TOLERANCE = 2;

interface Vertex {
  id: string;
  label: string;
  x: number;
  y: number;
  w: number;
  h: number;
  isContainer: boolean;
  bottomLabel: boolean;
}

/** Rough plain-text length of an HTML-escaped mxCell `value`, for width estimates. */
function textLen(value: string): number {
  return value
    .replace(/<[^>]*>/g, "")
    .replace(/&lt;|&gt;|&amp;|&quot;|&#\d+;/g, "x")
    .trim().length;
}

/**
 * Absolute-positioned vertices, keyed by id. Child coordinates in mxGraph are
 * relative to the parent, so the parent chain has to be summed before any two
 * cells can be compared.
 */
function parseVertices(xml: string): Map<string, Vertex> {
  const raw = new Map<string, { v: Vertex; parent: string | undefined }>();
  const cellRe = /<mxCell\b([^>]*)>([\s\S]*?)<\/mxCell>/g;
  for (const m of xml.matchAll(cellRe)) {
    const attrs = m[1];
    const body = m[2];
    if (/\bedge="1"/.test(attrs) || !/\bvertex="1"/.test(attrs)) continue;
    const id = /id="([^"]*)"/.exec(attrs)?.[1];
    const geo = /<mxGeometry\b([^>]*)/.exec(body)?.[1];
    if (!id || !geo) continue;
    const num = (name: string) =>
      Number.parseFloat(new RegExp(`${name}="([^"]*)"`).exec(geo)?.[1] ?? "0") || 0;
    const style = /style="([^"]*)"/.exec(attrs)?.[1] ?? "";
    raw.set(id, {
      parent: /parent="([^"]*)"/.exec(attrs)?.[1],
      v: {
        id,
        label: /value="([^"]*)"/.exec(attrs)?.[1] ?? "",
        x: num("x"), y: num("y"), w: num("width"), h: num("height"),
        isContainer: /container=1/.test(style),
        bottomLabel: /verticalLabelPosition=bottom/.test(style),
      },
    });
  }

  // Cycles yield (0, 0) rather than recursing forever: a malformed document should
  // be reported by the checks below, not hang the validator.
  const offset = (id: string, seen: Set<string>): [number, number] => {
    const parent = raw.get(id)?.parent;
    if (!parent || !raw.has(parent) || seen.has(id)) return [0, 0];
    const [px, py] = offset(parent, new Set(seen).add(id));
    return [px + raw.get(parent)!.v.x, py + raw.get(parent)!.v.y];
  };

  const out = new Map<string, Vertex>();
  for (const [id, { v }] of raw) {
    const [ox, oy] = offset(id, new Set());
    out.set(id, { ...v, x: v.x + ox, y: v.y + oy });
  }
  return out;
}

/**
 * Edge labels that land in a node's own label band.
 *
 * The SKILL.md section this backs is headed "enforced by validate.ts", and this
 * rule was not: the validator read the XML as text and never resolved a
 * coordinate, so a run that labelled four of five edges printed `✓ PASS` and
 * rendered `Amazon MSK (ManagedRead logsreaming for Apache Kafka)` — the edge
 * label's white background punched straight through the node label underneath it.
 * A rule advertised as tool-enforced and not actually checked is worse than an
 * unchecked rule, because it tells the reader not to look.
 *
 * The predicate is the geometric one rather than "is the edge vertical". Steepness
 * alone flags edges that render fine — `three-tier-web`'s 530 px vertical
 * "synchronous replication" label has empty canvas at its midpoint and reads
 * cleanly — so what is checked is whether the label's estimated box actually
 * intersects some node's label band. draw.io centres an unpositioned edge label on
 * the routed midpoint, which for these orthogonal two-segment routes is close to
 * the midpoint between the endpoints' centres.
 *
 * Reported as an error, not a warning, because the remedy is always available and
 * always in keeping with the house style: drop the label and put the verb in a
 * numbered annotation. Warnings under a `✓ PASS` line are what the previous
 * iteration's runs read straight past.
 */
function checkEdgeLabels(xml: string, verts: Map<string, Vertex>): string[] {
  const out: string[] = [];
  const bands: { id: string; label: string; x: number; y: number; x2: number; y2: number }[] = [];
  for (const v of verts.values()) {
    if (v.isContainer || !v.bottomLabel) continue;
    const n = textLen(v.label);
    if (!n) continue;
    const textW = Math.max(v.w, n * NODE_CHAR_W + NODE_LABEL_PAD);
    const cx = v.x + v.w / 2;
    // The band only, not the icon: an edge crossing an icon is a routing question
    // and this check does not claim to answer it.
    bands.push({
      id: v.id, label: v.label,
      x: cx - textW / 2, y: v.y + v.h,
      x2: cx + textW / 2, y2: v.y + v.h + LABEL_LINE_HEIGHT,
    });
  }

  const edgeRe = /<mxCell\b([^>]*\bedge="1"[^>]*)>/g;
  for (const m of xml.matchAll(edgeRe)) {
    const attrs = m[1];
    const label = /value="([^"]*)"/.exec(attrs)?.[1] ?? "";
    if (!textLen(label)) continue;
    const eid = /id="([^"]*)"/.exec(attrs)?.[1] ?? "?";
    const a = verts.get(/source="([^"]*)"/.exec(attrs)?.[1] ?? "");
    const b = verts.get(/target="([^"]*)"/.exec(attrs)?.[1] ?? "");
    if (!a || !b) continue;
    const cx = ((a.x + a.w / 2) + (b.x + b.w / 2)) / 2;
    const cy = ((a.y + a.h / 2) + (b.y + b.h / 2)) / 2;
    const halfW = Math.max(1, (textLen(label) * EDGE_CHAR_W) / 2);
    const ex = cx - halfW, ex2 = cx + halfW;
    const ey = cy - EDGE_LABEL_H / 2, ey2 = cy + EDGE_LABEL_H / 2;
    for (const band of bands) {
      const dx = Math.min(ex2, band.x2) - Math.max(ex, band.x);
      const dy = Math.min(ey2, band.y2) - Math.max(ey, band.y);
      if (dx > OVERLAP_TOLERANCE && dy > OVERLAP_TOLERANCE) {
        out.push(
          `${eid}: edge label "${label}" lands on the label of "${band.label}" ` +
          `(${band.id}) and will render chopped — remove the edge label and move it ` +
          `into a numbered annotation, which is where this style wants the verb. ` +
          `(The pair is estimated from endpoint centres, so the node it collides ` +
          `with may be a neighbour in the same row; the collision itself is real.)`,
        );
        break; // one report per edge; the remedy is the same whichever band it hit
      }
    }
  }
  return out;
}

interface Report {
  errors: string[];
  warnings: string[];
  stats: {
    resources: number;
    groups: number;
    edges: number;
    aws4Refs: number;
    annotations: number;
  };
}

function validate(xml: string): Report {
  const errors: string[] = [];
  const warnings: string[] = [];

  const aws4Refs = (xml.match(/mxgraph\.aws4\./g) ?? []).length;

  // Inspect each opening <mxCell ...> tag.
  const openTags = xml.match(/<mxCell\b[^>]*>/g) ?? [];
  let resources = 0;
  let groups = 0;
  let edges = 0;
  let labelledEdges = 0;
  const emptyLabelIds: string[] = [];
  const badIcons: string[] = [];
  const containerIds: string[] = [];
  const childParents = new Set<string>();

  for (const tag of openTags) {
    const id = /id="([^"]*)"/.exec(tag)?.[1] ?? "?";
    const value = /value="([^"]*)"/.exec(tag)?.[1];
    const isResource = /shape=mxgraph\.aws4\.resourceIcon/.test(tag);
    const isContainer = /container=1/.test(tag);
    const isEdge = /\bedge="1"/.test(tag);

    const parent = /parent="([^"]*)"/.exec(tag)?.[1];
    if (parent && parent !== "0" && parent !== "1") childParents.add(parent);

    if (isEdge) {
      edges++;
      if (value !== undefined && textLen(value) > 0) labelledEdges++;
    }
    if (isContainer) {
      groups++;
      containerIds.push(id);
    }
    if (isResource) {
      resources++;
      if (value === undefined || value.trim() === "") emptyLabelIds.push(id);
    }

    // Checked against the shipped stencil list rather than a handful of known-bad
    // names: an allowlist catches the invented slug nobody has seen yet, which is
    // the whole population of this bug. Skipped entirely if the list is missing,
    // and `stencilsLoaded` makes the stats line say so.
    // Every `mxgraph.aws4.*` reference in the tag, not just `resIcon=`. The check
    // used to read `resIcon=` alone, which covers service icons and nothing else:
    // an external actor is emitted as `shape=mxgraph.aws4.users` with no `resIcon`
    // at all, so `mxgraph.aws4.corporate_users_bogus` on an `external: true` node
    // rendered blank and validated **clean** — the identical blank-square bug this
    // check exists to catch, on the one path it did not look at. Group containers
    // reference their badge the same way, via `grIcon=`.
    for (const m of tag.matchAll(/mxgraph\.aws4\.([A-Za-z0-9_]+)/g)) {
      const name = m[1];
      // The aws4 library's structural shapes are mxgraph shape names, not icon
      // names, so they are legitimately absent from the stencil list. Exempting
      // them by name is deliberate: the alternative -- only checking attributes we
      // recognise -- is what let the actor path through in the first place.
      if (AWS4_PRIMITIVES.has(name)) continue;
      if (!stencilsLoaded || VALID_STENCILS.has(name)) continue;
      const hint = suggest(name);
      badIcons.push(
        `${id}: icon "${name}" is not in draw.io's AWS4 stencil set and ` +
        `renders as a blank coloured square` +
        (hint ? ` — use "${hint}"` : " — check the name against scripts/aws4-stencils.txt"),
      );
    }
  }

  // The document frame and the annotation layer, by the cell ids the generator
  // emits for them. Checking ids rather than prose also proves the generator ran:
  // hand-written XML will not carry them.
  const has = (id: string) => xml.includes(`id="${id}"`);
  const annotations = (xml.match(/id="note-badge-[^"]*"/g) ?? []).length;
  const canvasBadges = (xml.match(/id="badge-[^"]*"/g) ?? []).length;

  // --- Errors (must fix) ---
  if (aws4Refs === 0) {
    errors.push("no AWS shape library used (expected mxgraph.aws4.* shapes, not plain rectangles)");
  }
  if (resources === 0) {
    errors.push("no AWS resource icons found (expected shape=mxgraph.aws4.resourceIcon)");
  }
  if (emptyLabelIds.length > 0) {
    errors.push(`${emptyLabelIds.length} resource(s) have empty labels: ${emptyLabelIds.join(", ")}`);
  }
  for (const bad of badIcons) errors.push(bad);
  for (const clash of checkEdgeLabels(xml, parseVertices(xml))) errors.push(clash);
  if (!has("doc-title")) {
    errors.push("no title block — set spec.title (a diagram is a document, not a bare canvas)");
  }
  if (!has("doc-subtitle")) {
    errors.push('no subtitle — set spec.subtitle to name the view, e.g. "VPC & Networking"');
  }
  if (!has("doc-intent")) {
    errors.push("no design-intent sentence — set spec.intent to one sentence on why the " +
                "architecture is shaped this way (not what it contains)");
  }
  if (annotations === 0) {
    errors.push("no numbered annotations — add spec.annotations. This is what separates a " +
                "reference architecture from a topology drawing; 3-6 notes carrying design " +
                "rationale is the target");
  }

  // --- Warnings (should fix) ---
  if (groups === 0) {
    warnings.push("no grouping containers (consider AWS Cloud / Region / VPC / AZ / subnet grouping)");
  }
  if (resources > 1 && edges === 0) {
    warnings.push("multiple resources but no connections — add labeled edges to show flow");
  }
  if (!has("doc-footer-centre")) {
    warnings.push("no footer — set spec.reviewed to date the diagram");
  }
  if (annotations > 0 && canvasBadges === 0) {
    warnings.push("annotations have no canvas badges — give each one a `target` so the reader " +
                  "can see what it refers to");
  }
  // "Leave most edges unlabelled" is a house-style rule with a number in it, so it
  // is checkable: the numbered notes carry the semantics and a label on every edge
  // produces exactly the crowding the style exists to avoid. A warning rather than
  // an error — unlike a label collision this is a judgement about density, and a
  // three-edge diagram where two labels genuinely earn their place is defensible.
  if (edges >= 3 && labelledEdges * 2 > edges) {
    warnings.push(
      `${labelledEdges} of ${edges} edges are labelled — the house style wants most ` +
      `edges bare, with the semantics in the numbered notes. Keep the one or two ` +
      `labels that say something an annotation cannot.`,
    );
  }
  const empty = containerIds.filter((id) => !childParents.has(id));
  if (empty.length) {
    warnings.push(`container(s) with nothing in them, which render as dead space: ${empty.join(", ")}`);
  }

  return { errors, warnings, stats: { resources, groups, edges, aws4Refs, annotations } };
}

function main(): void {
  const path = process.argv[2];
  if (!path) {
    console.error("usage: tsx src/validate.ts <file.drawio>");
    process.exit(1);
  }

  let xml: string;
  try {
    xml = readFileSync(path, "utf8");
  } catch (err) {
    console.error(`validate: cannot read "${path}": ${(err as Error).message}`);
    process.exit(1);
  }

  const { errors, warnings, stats } = validate(xml);

  console.log(`AWS Draw.io validation: ${path}`);
  console.log(
    `  resources=${stats.resources}  groups=${stats.groups}  edges=${stats.edges}` +
      `  aws4Refs=${stats.aws4Refs}  annotations=${stats.annotations}` +
      `  stencils=${stencilsLoaded ? `${VALID_STENCILS.size} known` : "UNCHECKED (list missing)"}`,
  );
  for (const w of warnings) console.log(`  ⚠ ${w}`);
  for (const e of errors) console.log(`  ✗ ${e}`);

  if (errors.length === 0) {
    console.log(warnings.length ? "  ✓ PASS (with warnings)" : "  ✓ PASS");
    process.exit(0);
  }
  console.log("  ✗ FAIL");
  process.exit(1);
}

main();
