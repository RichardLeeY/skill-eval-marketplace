# AWS Architecture Diagram Style Guide

Conventions the generator applies and the validator enforces. Keep the skill,
the generator, and any eval rubric aligned to this one document.

## 1. Use the official AWS shape library

Every resource is an AWS resource icon:

```
shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.<service>;
```

Never represent an AWS service as a plain rectangle. `validate.ts` fails if no
`mxgraph.aws4.*` shapes are present or if there are zero resource icons.

## 2. Group by topology

Reflect the real containment hierarchy with grouping containers, outermost to
innermost:

```
AWS Cloud  →  Region  →  VPC  →  Availability Zone  →  Subnet (public/private)
```

Group kinds and their intent:

| kind             | use for                                  | border            |
|------------------|------------------------------------------|-------------------|
| `aws_cloud`      | everything inside AWS                     | solid dark        |
| `region`         | a specific AWS region                     | dashed teal       |
| `vpc`            | a VPC                                      | solid green       |
| `az`             | an Availability Zone                      | dashed teal       |
| `subnet_public`  | a public subnet                           | solid green       |
| `subnet_private` | a private subnet                          | solid blue        |
| `security_group` | a security-group boundary                 | solid red         |
| `generic`        | any other logical grouping                | dashed gray       |

Not every diagram needs all levels — include the ones that carry meaning.

## 3. Label everything

- Every node must have a clear, human-readable label (service + role, e.g.
  "RDS (PostgreSQL)", "Application Load Balancer").
- Groups are labeled with their identity (e.g. "VPC 10.0.0.0/16",
  "Availability Zone A").
- Edges carry a label for the protocol/flow when it adds meaning ("HTTPS",
  "SQL", "gRPC").

`validate.ts` fails on any resource icon with an empty label.

## 4. Show connections and direction

Connect related resources with arrows indicating flow direction. A diagram
with multiple resources and no edges triggers a validator warning.

## 5. Category colors (icon fill)

Icons are colored by AWS category automatically:

| category    | color     |
|-------------|-----------|
| compute     | `#ED7100` |
| storage     | `#7AA116` |
| database    | `#C925D1` |
| networking  | `#8C4FFF` |
| integration | `#E7157B` |
| analytics   | `#8C4FFF` |
| security    | `#DD344C` |
| ml          | `#01A88D` |
| management  | `#E7157B` |
| general     | `#232F3E` |

## Extending the icon catalog

`scripts/src/catalog.ts` holds a curated starter set. To add a service:

1. Find its draw.io slug (the `resIcon=mxgraph.aws4.<slug>` value from a
   real draw.io export of that shape).
2. Add an entry to `CATALOG`: `key: { resIcon: "<slug>", category: "<cat>" }`.

Or skip the catalog entirely and pass the raw slug as the node `type`
(`"mxgraph.aws4.<slug>"` or `"<slug>"`) — the generator falls back to
`mxgraph.aws4.<slug>`.

## Known v0 limitations

- **Layout**: children are laid out in a single horizontal row per container;
  no edge-routing optimization. Fine for small/medium diagrams; large ones may
  need manual tidying in draw.io.
- **Icon slugs**: the catalog is not exhaustive and some slugs may differ
  across draw.io shape-library versions; verify against a real export if an
  icon renders as a fallback.
- **Validation is structural**: it reads XML, not the rendered picture, so it
  cannot judge visual clarity/aesthetics. Pair with an image-based review (or
  an LLM/multimodal judge on a rendered PNG) when visual quality matters.
