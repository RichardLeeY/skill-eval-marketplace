/**
 * stencils.ts — the set of `mxgraph.aws4.*` icon names that actually exist.
 *
 * An icon name that does not resolve renders as a blank coloured square. It
 * passes every text-level check (the XML says `mxgraph.aws4.`, so counting
 * references and matching regexes both succeed), and at the resolution a vision
 * judge sees a 5000 px canvas, a blank 78 px square is a few pixels — one was
 * described by a judge as a correctly rendered icon. Nothing in the stack could
 * see it, so the name has to be checked against a list.
 *
 * `aws4-stencils.txt` is that list, taken from the `<shapes name="mxgraph.aws4">`
 * stencil block in the shipped draw.io app bundle — 1038 shapes, stored in both
 * their raw form and lower-cased-with-underscores, which is how a style string
 * spells them. Regenerate after a draw.io upgrade:
 *
 *   python3 - <<'PY'
 *   import re
 *   d = open('/Applications/draw.io.app/Contents/Resources/app.asar','rb').read().decode('utf8','replace')
 *   s = d.find('<shapes name="mxgraph.aws4"'); e = d.find('</shapes>', s)
 *   names = set()
 *   for n in re.findall(r'<shape[^>]*name="([^"]+)"', d[s:e]):
 *       names.add(n.strip()); names.add(n.strip().lower().replace(' ','_').replace('-','_'))
 *   names |= set(re.findall(r'resIcon="\+\w+\+"\.([a-z0-9_]+);', d))
 *   names.discard('mxgraph.aws4')
 *   open('aws4-stencils.txt','w').write("\n".join(sorted(names))+"\n")
 *   PY
 *
 * Do not extract by grepping for the literal string `mxgraph.aws4.<name>`. The
 * palette code builds most names dynamically (`resIcon="+d+".bedrock;`), so a
 * literal grep returns 454 of 1038 — an allowlist that rejects `bedrock`,
 * `fsx_for_openzfs` and most of the library. The stencil block is the source of
 * truth; the palette regex above only backfills aliases.
 *
 * Note `shape=mxgraph.aws4.resourceIcon` is *not* in this list: the wrapper is a
 * programmatic shape from mxAWS4.js, not a stencil. Check `resIcon=` values only.
 */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const LIST = join(dirname(dirname(fileURLToPath(import.meta.url))), "aws4-stencils.txt");

/**
 * Empty if the list cannot be read, which disables the check rather than
 * failing every diagram. A silently skipped check is bad; a check that blocks
 * all work because a data file moved is worse, and `loaded` lets callers say out
 * loud which case they are in.
 */
export const VALID_STENCILS: ReadonlySet<string> = (() => {
  try {
    return new Set(
      readFileSync(LIST, "utf8").split("\n").map((s) => s.trim()).filter(Boolean),
    );
  } catch {
    return new Set<string>();
  }
})();

export const loaded = VALID_STENCILS.size > 0;

/**
 * Corrections for names that look right and do not exist. The allowlist already
 * rejects all of these; this turns "does not exist" into "use X instead", which
 * is the difference between a message an agent can act on and one it retries
 * against.
 *
 * Two kinds. Abbreviations: the library uses `ecs`, not
 * `elastic_container_service`. And renames: draw.io keeps the pre-rename slug and
 * relabels the palette entry, so Amazon OpenSearch Service is still
 * `elasticsearch_service` and Amazon Managed Service for Apache Flink is still
 * `kinesis_data_analytics`. Label the node with the service's current official
 * name and point `type` at the old stencil — the label and the icon are separate
 * things, and only one of them is draw.io's to fix.
 */
const HINTS: Record<string, string> = {
  elastic_container_service: "ecs",
  elastic_kubernetes_service: "eks",
  simple_storage_service: "s3",
  simple_queue_service: "sqs",
  simple_notification_service: "sns",
  step_function: "step_functions",
  // Shipped in this skill's own coinbase example and rendered blank for two
  // evaluation runs, scoring 0.92 while a judge described the icon as present.
  managed_streaming_for_apache_kafka: "managed_streaming_for_kafka",
  msk: "managed_streaming_for_kafka",
  opensearch: "elasticsearch_service",
  opensearch_service: "elasticsearch_service",
  managed_apache_flink: "kinesis_data_analytics",
  managed_service_for_apache_flink: "kinesis_data_analytics",
  flink: "kinesis_data_analytics",
  fsx_for_open_zfs: "fsx_for_openzfs",
  openzfs: "fsx_for_openzfs",
  ec2_auto_scaling: "auto_scaling",
  virtual_private_cloud_vpc: "virtual_private_cloud",
};

/** `undefined` when there is no better name to suggest. */
export function suggest(name: string): string | undefined {
  const hint = HINTS[name];
  if (hint && VALID_STENCILS.has(hint)) return hint;

  // Fall back to dropping trailing words: `rds_aurora_cluster` -> `rds_aurora`
  // -> `rds`. Only useful when the guessed name is a real name with something
  // appended, which is the common shape of an invented slug.
  const parts = name.split("_");
  while (parts.length > 1) {
    parts.pop();
    const shorter = parts.join("_");
    if (VALID_STENCILS.has(shorter)) return shorter;
  }
  return undefined;
}
