/** Shared spec types for the aws-drawio-diagram generator. */

export type GroupKind =
  | "aws_cloud"
  | "region"
  | "vpc"
  | "az"
  | "subnet_public"
  | "subnet_private"
  | "security_group"
  | "generic";

export interface NodeSpec {
  /** Unique id. Must not be "0" or "1" (reserved by the Drawio model). */
  id: string;
  /** Human-readable label. Required and must be non-empty. */
  label: string;
  /** Catalog key (e.g. "ec2") or a raw mxgraph.aws4.* slug. */
  type: string;
  /** Optional group id this node is placed inside. */
  group?: string;
  /**
   * Not an AWS service: a user, a device, an on-premises system.
   *
   * Drawn as a flat grey glyph with no coloured category square, and laid out
   * in a column to the left of every container. The visual language is what
   * distinguishes "thing AWS runs" from "thing that talks to it", so an actor
   * given a resource icon reads as a service that does not exist.
   */
  external?: boolean;
  /**
   * AWS category, which sets the icon's fill colour: `compute`, `storage`,
   * `database`, `networking`, `integration`, `analytics`, `security`, `ml`,
   * `management`.
   *
   * Only needed when `type` is a raw stencil slug rather than a catalog key —
   * catalog entries already carry their category. Without it a raw slug renders
   * dark grey, which reads as "uncategorised" in a style where colour is how the
   * reader groups services.
   */
  category?: string;
}

export interface GroupSpec {
  /** Unique id. Must not be "0" or "1". */
  id: string;
  label: string;
  kind: GroupKind;
  /** Optional parent group id, for nesting (Cloud > Region > VPC > AZ > Subnet). */
  parent?: string;
}

export interface EdgeSpec {
  source: string;
  target: string;
  label?: string;
  dashed?: boolean;
}

/**
 * A numbered callout: a badge pinned on the canvas plus prose in the side panel.
 *
 * This is the element that separates a reference architecture from a topology
 * drawing. The prose must carry design rationale -- why a component is here,
 * what could go there instead, what the drawing deliberately omits -- because a
 * note a reader could have derived from the icons earns nothing.
 */
export interface AnnotationSpec {
  /** 1-based badge number. Defaults to position in the array. */
  n?: number;
  /** Node or group id the badge is pinned beside. Omit to leave it panel-only. */
  target?: string;
  /** The rationale. Inline `<b>` is honoured -- bold the AWS service names. */
  text: string;
}

export interface DiagramSpec {
  title: string;
  /**
   * Which view of the system this is, e.g. "VPC & Networking".
   * A diagram is one view among several and should say which.
   */
  subtitle?: string;
  /** One sentence of design intent -- why it is built this way, not what is in it. */
  intent?: string;
  /** Date for the footer's "Reviewed for technical accuracy". ISO, e.g. 2026-08-26. */
  reviewed?: string;
  nodes: NodeSpec[];
  groups?: GroupSpec[];
  edges?: EdgeSpec[];
  annotations?: AnnotationSpec[];
}
