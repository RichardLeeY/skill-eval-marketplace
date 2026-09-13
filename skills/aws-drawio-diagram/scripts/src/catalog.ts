/**
 * AWS icon catalog + group styling for draw.io's official `mxgraph.aws4.*`
 * shape library.
 *
 * v0 note: the resource catalog is a curated (not exhaustive) starter set.
 * Unknown `type` values fall back to `mxgraph.aws4.<slug>`, so any real
 * draw.io slug can be used directly. Colors follow AWS architecture-icon
 * category colors and can be tuned.
 */

import type { GroupKind } from "./types";

type Category =
  | "compute"
  | "storage"
  | "database"
  | "networking"
  | "integration"
  | "analytics"
  | "security"
  | "ml"
  | "management"
  | "general";

/** AWS architecture-icon category colors (icon fill). */
const CATEGORY_COLOR: Record<Category, string> = {
  compute: "#ED7100",
  storage: "#7AA116",
  database: "#C925D1",
  networking: "#8C4FFF",
  integration: "#E7157B",
  analytics: "#8C4FFF",
  security: "#DD344C",
  ml: "#01A88D",
  management: "#E7157B",
  general: "#232F3E",
};

interface ResourceDef {
  /** slug appended to `mxgraph.aws4.` for the resIcon. */
  resIcon: string;
  category: Category;
}

/** Curated map of friendly key -> AWS resource icon. */
const CATALOG: Record<string, ResourceDef> = {
  // Compute
  ec2: { resIcon: "ec2", category: "compute" },
  lambda: { resIcon: "lambda", category: "compute" },
  // The resIcon slugs below are draw.io's *abbreviated* names. The spelled-out
  // ones (`elastic_container_service`, `simple_storage_service`, ...) are not in
  // the AWS4 stencil set and render as a blank coloured square -- and nothing
  // catches it: validate.ts counted `mxgraph.aws4.` references without checking
  // they resolve, and a blank 78 px icon is invisible to a vision judge on a
  // wide canvas. See `KNOWN_BAD_RESICONS` in validate.ts.
  elastic_container_service: { resIcon: "ecs", category: "compute" },
  elastic_kubernetes_service: { resIcon: "eks", category: "compute" },
  fargate: { resIcon: "fargate", category: "compute" },

  // Storage
  s3: { resIcon: "s3", category: "storage" },
  efs: { resIcon: "elastic_file_system", category: "storage" },

  // Database
  rds: { resIcon: "rds", category: "database" },
  aurora: { resIcon: "aurora", category: "database" },
  dynamodb: { resIcon: "dynamodb", category: "database" },
  elasticache: { resIcon: "elasticache", category: "database" },
  redshift: { resIcon: "redshift", category: "analytics" },

  // Networking & content delivery
  cloudfront: { resIcon: "cloudfront", category: "networking" },
  route_53: { resIcon: "route_53", category: "networking" },
  elastic_load_balancing: { resIcon: "elastic_load_balancing", category: "networking" },
  api_gateway: { resIcon: "api_gateway", category: "networking" },
  internet_gateway: { resIcon: "internet_gateway", category: "networking" },
  nat_gateway: { resIcon: "nat_gateway", category: "networking" },

  // Application integration
  sqs: { resIcon: "sqs", category: "integration" },
  sns: { resIcon: "sns", category: "integration" },
  eventbridge: { resIcon: "eventbridge", category: "integration" },
  step_functions: { resIcon: "step_functions", category: "integration" },

  // Analytics
  kinesis: { resIcon: "kinesis", category: "analytics" },
  athena: { resIcon: "athena", category: "analytics" },
  glue: { resIcon: "glue", category: "analytics" },

  // Security, identity & compliance
  iam: { resIcon: "identity_and_access_management", category: "security" },
  cognito: { resIcon: "cognito", category: "security" },
  secrets_manager: { resIcon: "secrets_manager", category: "security" },
  kms: { resIcon: "key_management_service", category: "security" },

  // Management & governance
  cloudwatch: { resIcon: "cloudwatch", category: "management" },
  cloudformation: { resIcon: "cloudformation", category: "management" },

  // Machine learning
  sagemaker: { resIcon: "sagemaker", category: "ml" },
  bedrock: { resIcon: "bedrock", category: "ml" },
  bedrock_agentcore: { resIcon: "bedrock_agentcore", category: "ml" },

  // Streaming, file systems and search. Added because the catalog covering 32 of
  // the library's 1038 stencils was the practical problem: everything outside it
  // reached the canvas as a grey box (see `resourceStyle`), so the services a
  // modern data pipeline is made of were exactly the ones that looked wrong.
  msk: { resIcon: "managed_streaming_for_kafka", category: "analytics" },
  managed_flink: { resIcon: "kinesis_data_analytics", category: "analytics" },
  kinesis_data_analytics: { resIcon: "kinesis_data_analytics", category: "analytics" },
  // The library now also ships a properly-named stencil, so both slugs are live.
  // Catalogued rather than left to the raw-slug path because a raw slug carries no
  // category: a model that reasonably reaches for the current official name got a
  // dark-navy icon instead of the analytics colour, and nothing warned it.
  managed_service_for_apache_flink: {
    resIcon: "managed_service_for_apache_flink", category: "analytics" },
  kinesis_firehose: { resIcon: "kinesis_data_firehose", category: "analytics" },
  opensearch: { resIcon: "elasticsearch_service", category: "analytics" },
  emr: { resIcon: "emr", category: "analytics" },
  lake_formation: { resIcon: "lake_formation", category: "analytics" },
  quicksight: { resIcon: "quicksight", category: "analytics" },
  fsx: { resIcon: "fsx", category: "storage" },
  fsx_openzfs: { resIcon: "fsx_for_openzfs", category: "storage" },
  fsx_lustre: { resIcon: "fsx_for_lustre", category: "storage" },
  fsx_windows: { resIcon: "fsx_for_windows_file_server", category: "storage" },
  ebs: { resIcon: "elastic_block_store", category: "storage" },
  backup: { resIcon: "backup", category: "storage" },
  ecr: { resIcon: "ecr", category: "compute" },
  auto_scaling: { resIcon: "auto_scaling", category: "compute" },
  vpc: { resIcon: "virtual_private_cloud", category: "networking" },
  direct_connect: { resIcon: "direct_connect", category: "networking" },
  transit_gateway: { resIcon: "transit_gateway", category: "networking" },
  privatelink: { resIcon: "privatelink", category: "networking" },
  global_accelerator: { resIcon: "global_accelerator", category: "networking" },
  waf: { resIcon: "waf", category: "security" },
  shield: { resIcon: "shield", category: "security" },
  systems_manager: { resIcon: "systems_manager", category: "management" },
};

/**
 * Returns the draw.io style string for an AWS resource icon.
 *
 * `category` is honoured for types outside the catalog. Without it every raw
 * slug fell to `general` (#232F3E), so a diagram using any of the ~1000 stencils
 * the catalog does not name came out as a row of identical dark boxes with
 * correct glyphs — legible, but with the category colour that the AWS house
 * style uses to carry information stripped out. A grey box is a subtler failure
 * than a blank one and survived the same three layers of checking.
 */
export function resourceStyle(type: string, category?: string): string {
  const def = CATALOG[type];
  const resIcon = def
    ? `mxgraph.aws4.${def.resIcon}`
    : type.startsWith("mxgraph.aws4.")
      ? type
      : `mxgraph.aws4.${type}`;
  const declared = category && category in CATEGORY_COLOR
    ? CATEGORY_COLOR[category as Category]
    : undefined;
  const fill = def ? CATEGORY_COLOR[def.category] : declared ?? CATEGORY_COLOR.general;
  return [
    "sketch=0",
    "outlineConnect=0",
    "fontColor=#232F3E",
    "gradientColor=none",
    `fillColor=${fill}`,
    "strokeColor=none",
    "dashed=0",
    "verticalLabelPosition=bottom",
    "verticalAlign=top",
    "align=center",
    "html=1",
    "fontSize=12",
    "fontStyle=0",
    "aspect=fixed",
    "shape=mxgraph.aws4.resourceIcon",
    `resIcon=${resIcon}`,
    "",
  ].join(";");
}

/**
 * Style for a non-AWS actor: a user, a device, an on-premises system.
 *
 * Deliberately unlike `resourceStyle`. No `resourceIcon` wrapper and no category
 * colour, because the coloured square is what marks a box as an AWS service --
 * give one to an on-premises mainframe and the diagram asserts a service that
 * does not exist. Flat grey glyph, matching how official AWS architecture
 * diagrams draw everything outside the cloud boundary.
 */
export function actorStyle(type: string): string {
  const shape = type.startsWith("mxgraph.aws4.") ? type : `mxgraph.aws4.${type}`;
  return [
    "sketch=0",
    "outlineConnect=0",
    "fontColor=#232F3E",
    "gradientColor=none",
    "fillColor=#545B64",
    "strokeColor=none",
    "dashed=0",
    "verticalLabelPosition=bottom",
    "verticalAlign=top",
    "align=center",
    "html=1",
    "fontSize=12",
    "fontStyle=0",
    `shape=${shape}`,
    "",
  ].join(";");
}

/** Text cells for the document frame: title, subtitle, intent, footer. */
export const TEXT_STYLES = {
  title:
    "text;html=1;strokeColor=none;fillColor=none;align=left;verticalAlign=middle;" +
    "whiteSpace=wrap;rounded=0;fontSize=30;fontStyle=1;fontColor=#232F3E;",
  subtitle:
    "text;html=1;strokeColor=none;fillColor=none;align=left;verticalAlign=middle;" +
    "whiteSpace=wrap;rounded=0;fontSize=19;fontStyle=0;fontColor=#232F3E;",
  intent:
    "text;html=1;strokeColor=none;fillColor=none;align=left;verticalAlign=top;" +
    "whiteSpace=wrap;rounded=0;fontSize=13;fontStyle=0;fontColor=#545B64;",
  rule: "line;strokeWidth=1;html=1;strokeColor=#879196;fillColor=none;",
  footerLeft:
    "text;html=1;strokeColor=none;fillColor=none;align=left;verticalAlign=middle;" +
    "whiteSpace=wrap;rounded=0;fontSize=11;fontColor=#545B64;",
  footerCentre:
    "text;html=1;strokeColor=none;fillColor=none;align=center;verticalAlign=middle;" +
    "whiteSpace=wrap;rounded=0;fontSize=13;fontStyle=1;fontColor=#ED7100;",
} as const;

/** The annotation panel: grey field down the right, visually off the canvas. */
export const PANEL_STYLE =
  "rounded=1;arcSize=2;whiteSpace=wrap;html=1;fillColor=#F2F3F3;strokeColor=#D5DBDB;" +
  "verticalAlign=top;align=left;";

/** A numbered callout badge: white numeral on an AWS-blue rounded square. */
export const BADGE_STYLE =
  "rounded=1;arcSize=30;whiteSpace=wrap;html=1;fillColor=#146EB4;strokeColor=#FFFFFF;" +
  "strokeWidth=2;fontColor=#FFFFFF;fontSize=14;fontStyle=1;align=center;verticalAlign=middle;";

/** Prose beside a badge in the panel. */
export const NOTE_STYLE =
  "text;html=1;strokeColor=none;fillColor=none;align=left;verticalAlign=top;" +
  "whiteSpace=wrap;rounded=0;fontSize=12;fontColor=#232F3E;";

/** The repeated Availability Zone label at the bottom of the band. */
export const AZ_FOOT_STYLE =
  "text;html=1;strokeColor=none;fillColor=none;align=center;verticalAlign=middle;" +
  "whiteSpace=wrap;rounded=0;fontSize=12;fontColor=#ED7100;";

/** Returns true if `type` resolves to a known catalog entry. */
export function isKnownType(type: string): boolean {
  return Object.prototype.hasOwnProperty.call(CATALOG, type);
}

/** Returns true if `category` is one of the AWS categories that carries a colour. */
export function isKnownCategory(category: string): boolean {
  return Object.prototype.hasOwnProperty.call(CATEGORY_COLOR, category);
}

/** The category names a spec may use, for error messages. */
export const CATEGORY_NAMES: string[] = Object.keys(CATEGORY_COLOR);

const GROUP_POINTS =
  "points=[[0,0],[0.25,0],[0.5,0],[0.75,0],[1,0],[1,0.25],[1,0.5],[1,0.75]," +
  "[1,1],[0.75,1],[0.5,1],[0.25,1],[0,1],[0,0.75],[0,0.5],[0,0.25]]";

const GROUP_BASE =
  "outlineConnect=0;gradientColor=none;html=1;whiteSpace=wrap;fontSize=12;" +
  "fontStyle=0;container=1;pointerEvents=0;collapsible=0;recursiveResize=0;" +
  "verticalAlign=top;align=left;spacingLeft=30";

/** Returns the draw.io style string for a grouping container. */
export function groupStyle(kind: GroupKind): string {
  switch (kind) {
    case "aws_cloud":
      return `${GROUP_POINTS};${GROUP_BASE};shape=mxgraph.aws4.group;grIcon=mxgraph.aws4.group_aws_cloud_alt;strokeColor=#232F3E;fillColor=none;fontColor=#232F3E;dashed=0;`;
    case "region":
      return `${GROUP_POINTS};${GROUP_BASE};shape=mxgraph.aws4.group;grIcon=mxgraph.aws4.group_region;strokeColor=#00A4A6;fillColor=none;fontColor=#00A4A6;dashed=1;`;
    case "vpc":
      return `${GROUP_POINTS};${GROUP_BASE};shape=mxgraph.aws4.group;grIcon=mxgraph.aws4.group_vpc;strokeColor=#248814;fillColor=none;fontColor=#248814;dashed=0;`;
    case "az":
      // Availability Zone: dashed orange, no grIcon, centred label repeated at
      // the bottom of the band by the generator. Orange rather than the teal a
      // Region uses, so the two dashed containers are told apart at a glance.
      return `${GROUP_POINTS};outlineConnect=0;gradientColor=none;html=1;whiteSpace=wrap;fontSize=12;fontStyle=0;container=1;pointerEvents=0;collapsible=0;recursiveResize=0;verticalAlign=top;align=center;fillColor=none;strokeColor=#ED7100;dashed=1;fontColor=#ED7100;`;
    // Both subnet kinds badge with `group_subnet`. `group_public_subnet` and
    // `group_private_subnet` read like the obvious names and do not exist -- the
    // aws4 set ships exactly one subnet badge -- so every subnet container in every
    // diagram this generator has ever produced carried a blank badge. Nothing caught
    // it: `validate.ts` checked `resIcon=` only, and a group's badge is `grIcon=`.
    // Public and private are told apart by colour, which is how AWS's own style
    // distinguishes them anyway; draw.io's own "Private subnet" palette entry
    // reaches for `group_security_group` for want of anything better.
    case "subnet_public":
      return `${GROUP_POINTS};${GROUP_BASE};shape=mxgraph.aws4.group;grIcon=mxgraph.aws4.group_subnet;strokeColor=#7AA116;fillColor=#F2F6E8;fontColor=#248814;dashed=0;`;
    case "subnet_private":
      return `${GROUP_POINTS};${GROUP_BASE};shape=mxgraph.aws4.group;grIcon=mxgraph.aws4.group_subnet;strokeColor=#00A4A6;fillColor=#E6F2F8;fontColor=#147EBA;dashed=0;`;
    case "security_group":
      return `${GROUP_POINTS};${GROUP_BASE};shape=mxgraph.aws4.group;grIcon=mxgraph.aws4.group_security_group;strokeColor=#DD3522;fillColor=none;fontColor=#DD3522;dashed=0;`;
    case "generic":
    default:
      // A logical tier is not an AWS container, so it must not borrow the
      // border-plus-badge language of one. A dashed grey outline did exactly
      // that: the style judge read "缓存层" and "Stream processing" boxes as
      // AWS containers missing their badge, and marked container language
      // partial on both diagrams whose specs an agent wrote itself. The house
      // style groups logically with a **grey fill and no stroke**, which no
      // bordered container can be confused with.
      return "rounded=0;html=1;whiteSpace=wrap;fontSize=12;fontStyle=0;container=1;collapsible=0;recursiveResize=0;verticalAlign=top;align=left;spacingLeft=8;fillColor=#F2F3F3;strokeColor=none;fontColor=#5A6B86;dashed=0;";
  }
}
