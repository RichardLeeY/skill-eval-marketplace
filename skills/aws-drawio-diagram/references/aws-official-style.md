# The house style: an AWS Reference Architecture

The conventions below are read off an official AWS Reference Architecture
(*Temenos Wealth Front Office on AWS*, © 2022 Amazon Web Services). They are what
this skill's output is held to. Written out rather than left to be inferred,
because prose is reviewable and gives the same answer twice.

## 1. The frame

The diagram is a *document*, not a bare canvas.

- **Title** top-left, large and bold — the workload, ending in "on AWS".
- **Subtitle** below it, naming the view: `VPC & Networking`. A diagram is one
  view of a system, and it says which.
- **One sentence of design intent**, e.g. *"This architecture makes use of AWS
  services to provide security and elasticity with low maintenance overhead."*
  The intent, not the contents.
- **Horizontal rule** between header and canvas.
- **Footer**: `Reviewed for technical accuracy <date>`, copyright, and
  `AWS Reference Architecture` centred in orange.
- **Annotation panel** down the right on a light grey field.

Set `title`, `subtitle`, `intent` and `reviewed` in the spec; the generator draws
all of it.

## 2. The comments

The element that distinguishes a reference architecture from a topology drawing.

- **Numbered badges** — white numeral on a blue rounded square — on the canvas
  beside the thing they annotate.
- **The panel repeats each badge** and follows it with prose.
- **Numbering follows the architecture's reading order**: edge security, private
  ingress, ingress restriction, compute, AZ topology, service access, data
  durability. Not arbitrary, not alphabetical.
- **AWS service names bold** inside the prose.
- **The prose carries design rationale and never restates the picture.** Each
  note answers *why is this here*, *what else could go here*, or *what is this
  drawing not showing*.

## 3. Containers

Nesting is shown by border style and a badge, never by fill colour alone. The
generator handles this from `kind`:

| Container | Border | Badge / label |
|---|---|---|
| AWS Cloud | thin solid dark | `aws` logo badge top-left |
| VPC | solid green | green cloud-in-box badge |
| Availability Zone | dashed orange | orange label, repeated top and bottom |
| Public / private subnet | solid, tinted fill | subnet badge |
| Security group | solid red | security-group badge |
| Logical grouping | dashed grey (`generic`) | label top-left |

A `Region` container is optional — the reference nests AWS Cloud → VPC directly.

## 4. Icons

- Resource icons are **filled squares with a white glyph** in official AWS
  category colours: purple networking, red security, orange compute/containers,
  magenta management and database, green storage, teal ML.
- Colour carries category information. Do not recolour icons for aesthetics.
- **Non-AWS actors are drawn differently on purpose**: flat grey glyphs, no
  coloured square, placed outside the AWS Cloud boundary. Use
  `"external": true`.

## 5. Labels

- **Full official service names with vendor prefix**: `Amazon CloudFront`,
  `AWS WAF`, `Amazon Simple Storage Service (S3)`. Not `CloudFront`, not `S3`.
- Actor labels lower-case: `internet users`, `branch users`, `API consumers`.
- Application components get functional names, not product names: `User
  Experience Platform (UXP)`, `API layer/business components`.

## 6. Edges

- Thin grey orthogonal lines, small solid arrowheads.
- **Mostly unlabelled** — the numbered comments carry the semantics instead.
  Labelling every edge is the *other* valid style; mixing them produces
  crowding. Label an edge only where the relationship is not obvious from the
  topology (`sync replication`, `egress`).
- One coloured or dashed edge where a relationship differs in kind from data
  flow.

## 7. Layout

- Left-to-right: actors → edge security → gateway → VPC → compute → data.
- External actors stacked vertically at the far left, outside the cloud.
- Shared services (Secrets Manager, ECR, CloudWatch) on their own row above the
  compute containers.
- Generous whitespace. No label collides with another or with an icon.
