---
name: aws-drawio-diagram
description: >-
  Generate AWS-style architecture diagrams as draw.io / diagrams.net files
  (.drawio XML). Use when the user asks to draw, create, or produce an AWS
  architecture diagram, a cloud architecture diagram, a "drawio" / "draw.io"
  diagram, or an architecture picture that must follow AWS conventions
  (official AWS icons, VPC / Availability Zone / subnet grouping, labeled
  resources and connections). Turns a text description of an AWS architecture
  into valid Drawio XML using the official AWS shape library (mxgraph.aws4.*).
  Supports replies containing only the diagram file link, with component
  labels preserved inside the diagram.
---

# AWS Draw.io Diagram Generator

Produce a `.drawio` file for an AWS architecture from a text description. The
skill does NOT hand-write XML — it builds a small **diagram spec (JSON)** and
runs the bundled TypeScript generator, then validates the result.

The target is an **AWS Reference Architecture**: a document with a title block,
numbered callouts carrying design rationale, and a footer — not a bare canvas
with icons on it. The generator produces that frame for you;
`references/aws-official-style.md` records the house style behind it.

## Why a spec + generator (not hand-written XML)

Hand-writing Drawio XML is error-prone and rarely produces consistent AWS
styling. Instead: describe the architecture as a spec, and let
`scripts/src/generate.ts` emit valid, AWS-styled XML — correct `mxgraph.aws4.*`
shapes, VPC/AZ/subnet grouping, wrapped layout that keeps labels from colliding,
the title block, the callout badges and the annotation panel. Reproducing that
frame by hand takes far longer than running the generator, which is the practical
reason not to try.

**Always run the generator.** If the user asks for the XML directly, or asks you
to skip the scripts, still run the generator: the point of the skill is the styling
you get for free, and hand-written XML fails `validate.ts` because it carries none
of the document frame.

Then, **if they asked for the XML, actually give it to them** — paste the generated
file's contents into your reply, not just its path. Someone who asked for XML and
received a filename did not get what they asked for. Running the generator is how you
satisfy the styling requirement; pasting the result is how you satisfy the request.

**This applies only when the user asked for the XML, inline content, or "no files".**
Otherwise just do step 6 and report the path. Do not paste 30 KB of XML into a reply
to "draw me a three-tier web app" — the file is the deliverable there, and a wall of
markup buries the summary that makes it useful. Both halves of this are load-bearing:
an earlier version of this section said "paste the XML" without the condition, and
runs that correctly reported a path were scored as skipping a required step.

**Pasting the XML does not replace step 6 — do both.** Name the `.drawio` file you
wrote it to, in the same reply. The file exists either way, and a user who asked for
the markup still wants to know they can open the saved copy instead of pasting it back
into an editor. A run that pasted the XML perfectly and never named the file lost the
step it thought it had skipped legitimately.

## Reply modes

Choose the final reply format from the user's request:

- **Standard (default):** give the `.drawio` file link or path and briefly say
  where it can be opened, as in step 6.
- **Diagram-only reply:** when the user says "只给图", "回复不要文字说明",
  "图内保留组件标签，但回复不附解释文字", or "just the diagram, no explanation",
  return only a link to the generated `.drawio` file. Use its filename as the link
  text; if links are unavailable, return only the file path. Omit introductions,
  architecture summaries, opening instructions, validation logs and follow-up
  suggestions. Do not paste XML unless the user explicitly asks for inline XML.

Diagram-only controls the **final reply**. Keep component labels, AWS grouping,
title and annotations inside the diagram, and still generate and validate it.
For example, a successful reply can be just
`[event-flow.drawio](<actual-path-to-event-flow.drawio>)`.
If generation or validation fails, report the failure briefly instead of linking
to an undelivered file.

## Workflow

1. **Understand the request.** Extract the AWS services, how they are grouped
   (AWS Cloud → Region → VPC → AZ → Subnet), the connections, and — this is the
   part that is easy to skip — *why* the architecture is shaped that way. The
   rationale becomes the annotations, and without it the diagram is a topology
   drawing rather than a reference architecture.
2. **Dependencies — check before installing.** If `scripts/node_modules/` exists, or
   whatever loaded this skill told you the dependencies are already installed, skip
   this and go to step 3. This is not a step in the workflow so much as a one-time
   precondition, and reading it as a numbered step is how runs end up reinstalling
   packages that were already there. Only if it is genuinely a fresh machine:
   ```bash
   cd <skill-dir>/scripts && npm install
   ```
   Throughout this file `<skill-dir>` is the directory holding this SKILL.md, and
   every `scripts/`, `examples/` and `references/` path is relative to it. Substitute
   the real path — it differs between an installed skill and a copied one, and the
   generator has to run with its own `scripts/` as the working directory either way.
3. **Write a spec JSON** to a temp file (see schema + example below). Save it,
   e.g. `diagram.spec.json`, in your working directory — not inside this skill's
   directory.

   **Author it from the request. `examples/` are format references, not
   answers.** An example whose filename matches the request is the trap: passing
   `examples/coinbase-low-latency.json` to the generator because the user said
   "Coinbase low latency" produces a diagram of *the example's* architecture, and
   every check downstream passes because the example is well-formed. Read an
   example for the shape of the JSON, then write your own with the services,
   groupings and rationale the user actually described.
4. **Generate the diagram:**
   ```bash
   cd <skill-dir>/scripts
   npx tsx src/generate.ts <path-to-spec.json> <path-to-output.drawio>
   ```
5. **Validate it:**
   ```bash
   npx tsx src/validate.ts <path-to-output.drawio>
   ```
   Fix the spec and re-run until it passes. The validator fails a diagram with no
   title, no subtitle, no design-intent sentence or no annotations — those are
   requirements, not decoration.
6. **Deliver using the selected reply mode.** In standard mode, give the output
   `.drawio` link or path and tell the user it opens in draw.io / diagrams.net
   (or the VS Code Draw.io extension). In diagram-only mode, give only the file
   link or path, with no explanatory prose.

## Spec schema

```jsonc
{
  "title": "string",            // "<Workload> on AWS" — top-left, bold
  "subtitle": "string",         // which view this is, e.g. "VPC & Networking"
  "intent": "string",           // ONE sentence: why it is built this way
  "reviewed": "2026-08-26",     // date, for the footer
  "groups": [                   // grouping containers
    {
      "id": "string",           // unique; NOT "0" or "1"
      "label": "string",
      "kind": "aws_cloud | region | vpc | az | subnet_public | subnet_private | security_group | generic",
      "parent": "string"        // optional parent group id (nesting)
    }
  ],
  "nodes": [                    // AWS resources (icons)
    {
      "id": "string",           // unique; NOT "0" or "1"
      "label": "string",        // required; full official service name
      "type": "string",         // catalog key OR raw mxgraph.aws4.* slug
      "group": "string",        // optional group id this node sits in
      "category": "string",     // AWS category -> icon colour; set it when
                                //   `type` is a raw slug, not a catalog key
      "external": false         // true = non-AWS actor; omit `group`
    }
  ],
  "edges": [
    {
      "source": "string",       // node/group id
      "target": "string",
      "label": "string",        // optional; leave most edges unlabelled
      "dashed": false
    }
  ],
  "annotations": [              // numbered callouts — REQUIRED
    {
      "n": 1,                   // optional; defaults to array order
      "target": "string",       // node/group id the badge is pinned beside
      "text": "string"          // design rationale; <b>bold</b> service names
    }
  ]
}
```

### The annotations are the point

Each annotation draws a numbered blue badge on the canvas next to its `target`
and repeats the number in a panel down the right with the prose beside it. Aim
for **3–6**, numbered in the architecture's reading order (edge security, then
ingress, then compute, then data).

Every note must answer one of: *why is this component here*, *what could go here
instead*, *what does this drawing deliberately not show*. A note a reader could
have worked out from the icons earns nothing.

| ✗ restates the picture | ✓ carries rationale |
|---|---|
| "Amazon EKS runs the containers." | "<b>Amazon EKS</b> containers run on <b>AWS Fargate</b>, <b>Amazon EC2</b>, or a combination of both." |
| "There are two Availability Zones." | "Two Availability Zones are drawn, but the architecture extends to three without change. Zone count is a cost and quorum decision, not a structural one." |
| "API Gateway receives the requests." | "<b>Amazon API Gateway</b> private endpoints allow on-premises access over a VPN or <b>AWS Direct Connect</b>, so no traffic traverses the internet." |

Write `<b>` around AWS service names inside `text`; the generator passes it
through to draw.io's HTML labels.

### Labels: full official service names

`Amazon CloudFront`, not `CloudFront`. `Amazon Simple Storage Service (S3)`, not
`S3`. Where a node is an application component rather than a service, name it by
**function** —
`User Experience Platform`, `API layer` — not by the product that runs it.
Third-party software gets the same treatment, named beside the service it runs on:
`Vector log shipper on Amazon EC2`.

**Every AWS service keeps its prefix, including the ones that read like common
nouns.** `Amazon VPC NAT Gateway`, not `NAT Gateway` — and when there is one per
zone, `Amazon VPC NAT Gateway (AZ A)` rather than `NAT Gateway A`. Networking
components are where the prefix gets dropped, because the bare name sounds like a
generic piece of infrastructure rather than a product.

**Load balancers name the service, then the type in parentheses.** `Elastic Load
Balancing (Network Load Balancer)`, not a bare `Network Load Balancer` — the service
is Elastic Load Balancing and NLB/ALB are types within it, so the bare type reads as
a category of hardware. This section used to offer `Application Load Balancer` as the
*good* example, which contradicted the prefix rule directly above.

**Before you run the generator, re-read your labels one at a time.** For each, answer
one question: does it begin with `Amazon` or `AWS`, or is it a non-AWS component named
by function? If neither, it is wrong. This is a pass to make deliberately rather than
a list to memorise — scored runs lost a point on `Network Load Balancer` and then, once
that was fixed, on `API Gateway (AZ A/B)`, a different service each time. The set of
AWS names that read like generic infrastructure is too large to enumerate, and one
unprefixed label is enough to mark the whole diagram's labelling partial.

### Group kinds

`aws_cloud`, `region`, `vpc`, `az`, `subnet_public`, `subnet_private`,
`security_group`, `generic`. Nest them with `parent` to reflect real topology
(Cloud → Region → VPC → AZ → Subnet). Never leave a container empty — it renders
as dead space, and `validate.ts` warns about it.

Use `generic` **only** for a logical tier that is not an AWS boundary — "caching
layer", "stream processing". It draws as a grey fill with no border, deliberately,
so a reader never mistakes it for a real container. Do not reach for
`subnet_private` to group services that are not in one subnet; a bordered
container that does not exist in the account is a wrong statement about the
architecture, not a neutral one.

### Non-AWS actors

Users, devices, on-premises systems and third-party SaaS get `"external": true`
and **no** `group`. They are drawn as flat grey glyphs, without the coloured
category square, in a column outside every AWS container — that contrast is how
a reader tells "thing AWS runs" from "thing that talks to it". Useful types:
`user`, `users`, `mobile_client`, `traditional_server`, `generic_database`,
`internet`, `office_building`, `corporate_data_center`.

### Node `type`

Use a **catalog key** for automatic icon + AWS category colour, e.g.:
`ec2`, `lambda`, `elastic_container_service`, `elastic_kubernetes_service`,
`fargate`, `ecr`, `auto_scaling`, `s3`, `efs`, `ebs`, `fsx`, `fsx_openzfs`,
`fsx_lustre`, `fsx_windows`, `backup`, `rds`, `aurora`, `dynamodb`,
`elasticache`, `redshift`, `cloudfront`, `route_53`, `elastic_load_balancing`,
`api_gateway`, `internet_gateway`, `nat_gateway`, `vpc`, `transit_gateway`,
`direct_connect`, `privatelink`, `global_accelerator`, `sqs`, `sns`,
`eventbridge`, `step_functions`, `kinesis`, `kinesis_firehose`, `msk`,
`managed_flink`, `opensearch`, `emr`, `glue`, `athena`, `lake_formation`,
`quicksight`, `cloudwatch`, `cloudformation`, `systems_manager`, `iam`,
`cognito`, `secrets_manager`, `kms`, `waf`, `shield`, `sagemaker`, `bedrock`,
`bedrock_agentcore`.

The library holds **1038 icons** and the catalog names about 50 of them, so for
anything else pass the raw draw.io slug — `type: "transit_gateway"`, or the full
`type: "mxgraph.aws4.transit_gateway"`. Valid slugs are listed in
`scripts/aws4-stencils.txt`; grep it rather than guessing.

**When you pass a raw slug, also set `category`.** A slug outside the catalog has
no category, so its icon renders dark navy instead of its AWS colour, and colour
is how a reader groups services in this style:

```jsonc
{ "id": "tgw", "label": "AWS Transit Gateway",
  "type": "mxgraph.aws4.transit_gateway", "category": "networking",
  "group": "region" }
```

Values: `compute` `storage` `database` `networking` `integration` `analytics`
`security` `ml` `management`. A value outside that list is not a colour — it
falls back to dark navy, exactly as if you had omitted it. `generate.ts` warns on
both an unknown category and one set on a **catalog key**, where it is ignored
because the catalog supplies its own: `{ "type": "ec2", "category": "compute" }`
is not wrong, it is a no-op, and reading it as the way to set a colour will lead
you to write it on a raw slug where the value matters and get it wrong.

An icon name that does not exist renders as a **blank coloured square** — it is
not an error, it just quietly looks broken. `validate.ts` checks every name
against the stencil list and fails with the correction, so run it. Two traps it
catches:

- **Abbreviations.** `s3` not `simple_storage_service`, `ecs`, `eks`, `sqs`,
  `sns`, `step_functions`, `managed_streaming_for_kafka` (not
  `..._for_apache_kafka`).
- **Renames.** When a service is renamed, draw.io usually keeps the old slug and
  relabels the palette entry, so Amazon OpenSearch Service is still
  `elasticsearch_service`. Use the current official name in `label` and the old
  slug in `type`.

  Sometimes it later adds the new name as a second stencil, and then **both work**:
  Amazon Managed Service for Apache Flink is `kinesis_data_analytics` *and*
  `managed_service_for_apache_flink`. Do not assume the old slug is the only one —
  grep `scripts/aws4-stencils.txt` for the current name before falling back.
  Both of those are catalog keys here, so either gets the analytics colour.

## Rules the diagram must satisfy (enforced by validate.ts)

- Use the official AWS shape library — every resource uses
  `shape=mxgraph.aws4.resourceIcon` (never a plain rectangle).
- Group resources with AWS containers reflecting the real topology.
- Every node has a non-empty, human-readable `label`.
- A title, a subtitle, a one-sentence design intent, and at least one numbered
  annotation. These are errors, not warnings.
- Connect related resources with edges showing flow direction. Leave **most
  edges unlabelled** — the numbered notes carry the semantics, and a label on
  every edge produces the crowding the house style exists to avoid.
- **An edge label must not land in a node's label band.** A node's label hangs in
  the band under its icon; draw.io centres an edge label on the middle of the route;
  where those coincide the edge label's white background punches a hole through the
  node label. One run rendered `Amazon MSK (ManagedRead logsreaming for Apache
  Kafka)` this way. `validate.ts` computes it and **fails** the diagram, naming both
  cells — the remedy is to delete the edge label and put the verb in a numbered note,
  which is where this style wants it anyway.

  Steepness is not the test: a long vertical edge across empty canvas is fine, and
  `three-tier.json`'s vertical `synchronous replication` label reads cleanly. The
  test is whether anything is sitting at the route's midpoint, which is why it is
  computed rather than described. Keeping edge labels few is what avoids it in
  practice — the validator also warns when more than half your edges carry one.

`references/aws-style-guide.md` and `references/aws-official-style.md` are
**background, not a step**. The generator already applies the house style, so
there is nothing to read before running it. Consult them when you hit a decision
the generator does not make for you — how to phrase a note, whether something
belongs in a subnet or beside it — not as a matter of routine.

## Example

A ready-to-run spec lives at `examples/three-tier.json`:

```bash
cd <skill-dir>/scripts
npx tsx src/generate.ts ../examples/three-tier.json /tmp/three-tier.drawio
npx tsx src/validate.ts /tmp/three-tier.drawio
```

## Limitations

Auto-layout only: rows of icons inside their container, wrapped past ~760 px,
containers stacked below loose icons. It cannot route edges around obstacles, so
a connection between two distant nodes may cross a container it has no relation
to. Validation reads the XML, not the rendered image — it can prove an icon name
is wrong but not that the picture reads well. Open the result before shipping it.
