# Visual Flow Spec Format

Use this when writing a JSON spec for `scripts/render_animated_webp.py`.

## Top-Level Fields

```json
{
  "canvas": {},
  "background": {"image": "./reference.png"},
  "theme": {},
  "title": {},
  "nodes": [],
  "edges": [],
  "animation": {}
}
```

`background` is optional. Omit it to generate a diagram; supply a local image to
animate over the original. See [Reference-image overlays](image-overlay.md) for
initialization, coordinates and preservation rules. In that mode, explicit canvas
width/height must match the oriented image, the header and theme textures are
omitted, and `background.matte` (default white) flattens transparency.

## Canvas

```json
{
  "canvas": {
    "width": 1360,
    "height": 900,
    "fps": 20,
    "frames": 41
  }
}
```

`frames` is only read in continuous mode; with `animation.steps` it is derived
from the step schedule (see Animation).

## Theme

The default example uses the `light` theme. Use the dark preset only when a
user asks for a dark version.

```json
{
  "theme": {
    "name": "light"
  }
}
```

The shorthand string form also works:

```json
{
  "theme": "light"
}
```

Supported presets:

- `dark`
- `light`

You can still override individual colors. Preset selection and overrides can be
combined:

```json
{
  "theme": {
    "name": "light",
    "blue": "#2f6eb5",
    "motion_color": "#00b864"
  }
}
```

## Title

```json
{
  "title": {
    "text": "Support Ops",
    "highlight": "Signal Loop",
    "subtitle": "Turn repeated tickets into product action without losing context"
  }
}
```

## Nodes

Each node needs an `id`, position, size, and label.

```json
{
  "id": "triage",
  "type": "box",
  "label": "Triage",
  "body": "Group repeats\nCheck history\nPick owner",
  "x": 60,
  "y": 150,
  "w": 300,
  "h": 150,
  "color": "green"
}
```

Supported node types:

- `anchor` — invisible geometry over a component in a background image; labels
  are metadata and arrival pulses draw outlines without filling the rectangle.
- `box`
- `diamond`
- `note`
- `label`
- `icon` — an icon with a caption under it and no box, for instances, gateways,
  users and devices. `label` is the caption, `body` a second line. The bounding
  box still anchors edges and takes the pulse halo, so give it room for icon plus
  text (about 100 px tall for a 56 px icon with a caption and one body line).

### Icons

```json
{ "id": "vgw", "type": "icon", "icon": "aws/network/vpn-gateway", "icon_size": 56,
  "label": "virtual private gateway", "body": "vgw-id", "x": 580, "y": 400, "w": 160, "h": 110 }

{ "id": "vpc", "type": "box", "layer": "background", "icon": "aws/network/vpc", "icon_size": 30, ... }
```

- `icon`: a key `<provider>/<category>/<name>` inside the icon set the `diagrams`
  package installs (`pip install diagrams`) — the official AWS Architecture Icons
  plus GCP, Azure, k8s, on-prem and generic sets. Find keys with
  `scripts/list_icons.py <words>`. A key ending in `.png` is read as a file path.
  `VISUAL_FLOW_ICON_DIR` overrides where the set is looked up.
- `icon_size`: in spec units. Default 56 on an `icon` node, 36 on a box.
- On a `box` (foreground or `background`) the icon sits in the top-left corner
  and the label moves right; on a background container only the icon is drawn,
  so shift the container's title label right by the icon size.
- An unknown key fails the lint (with the closest existing keys) and the
  renderer's `--check` (`icons_resolved`).

Supported color names:

- `blue`
- `green`
- `purple`
- `red`
- `dark`

You can also pass hex colors in `stroke` and `fill`.

Optional typography fields:

- `label_size`: title font size inside the node
- `body_size`: body font size inside the node
- `label_h`: reserved title area height before body text starts
- `label_bold`: whether the node label should receive the default faux-bold pass

For `type: "label"` nodes, these fields are also supported:

- `size`: label font size
- `bold`: whether to render with the faux-bold pass
- `fill`: text color override
- `align`: `left`, `center`, or `right`

Use these for dense small cards so titles and body copy do not overlap.

Use `layer: "background"` for large section containers that should render
behind arrows and foreground nodes.

```json
{
  "id": "core-bg",
  "type": "box",
  "layer": "background",
  "label": "Support Loop",
  "body": "(respond, log, act)",
  "x": 50,
  "y": 260,
  "w": 1180,
  "h": 310,
  "color": "blue"
}
```

## Edges

`draw_path` controls static strokes, labels and arrowheads without disabling the
animation: default `false` with a background image, `true` otherwise. `radius`
controls corner rounding: default `0` with a background, `30` otherwise. Overlay
edges require explicit `points` traced along the image, plus `from` and `to`.

Edges can connect nodes by id:

```json
{
  "from": "triage",
  "to": "memory",
  "label": "log"
}
```

Or use a custom path:

```json
{
  "points": [[360, 225], [520, 280], [580, 340]],
  "label": "writes evidence"
}
```

Use custom paths when arrow direction or routing must match a reference image.
Custom paths with three or more points use rounded corners by default.

Optional edge fields:

- `label`: short edge label
- `stroke`: line color override
- `width`: line width
- `style`: use `"dashed"` for dashed lines
- `arrow`: set to `false` to hide the arrowhead

## Animation

The animation is an overlay on top of the static diagram. There are two modes.

### Steps mode (`animation.steps`) — use this when the flow has an order

```json
{
  "animation": {
    "motion_color": "#0077ff",
    "step_seconds": 1.2,
    "hold_seconds": 0.8,
    "steps": [
      { "edges": [0], "label": "git push" },
      { "edges": [1], "label": "webhook" },
      { "edges": [2, 3, 4], "label": "three stores at once" },
      { "edges": [5], "pulse": "repo", "label": "status back" }
    ]
  }
}
```

- `steps` is an ordered list. Each step's `edges` are indices into the top-level
  `edges` array; their dots start together and arrive together. Several indices in
  one step is how parallel branches are drawn as parallel.
- `pulse` (a node id or a list of them) names the box lit when the step's dots
  arrive. If omitted, every `to` node of the step's edges lights up. It must be a
  node the step's arrows actually reach.
- `seconds` on a step overrides `step_seconds` for that step alone.
- `step_seconds` (default 1.2) is how long one step's dots take to travel;
  `hold_seconds` (default 0.8) is the pause on the finished picture before the loop
  restarts. Keep steps at 0.5 s or more.
- `trail` (default `true`) keeps a faint line on edges already travelled, so a
  viewer sees how far the story has got.
- `label` is a note to the next editor; the renderer does not draw it.
- `canvas.frames` is **derived** in this mode: `fps × (sum of step seconds +
  hold_seconds)`. Do not set it. The `✓ PASS` line and the `--check` JSON's
  `schedule` report the frame ranges the renderer used.
- `pulses` is ignored when `steps` is present, and the lint fails a spec that has
  both.

Ordering rules the lint enforces: after step 0, every edge in a step must leave a
node an earlier step arrived at (or a node with no incoming edges); no edge appears
in two steps; a `pulse` must be reached by its step. Edges in no step never carry a
dot — the lint prints them as a note, since leaving out a branch you are not
narrating is legitimate.

### Continuous mode (`animation.paths` + `animation.pulses`) — for cycles with no order to tell

```json
{
  "animation": {
    "motion_color": "#0077ff",
    "paths": [
      { "points": [[360, 225], [520, 280], [580, 340]] },
      { "edge": 0 }
    ],
    "pulses": ["inbox", "triage", "memory"]
  }
}
```

`paths` controls moving glow dots; every listed path animates for the whole loop
(unset means every edge). `pulses` controls which nodes get active highlights, in
that order, each held for `frames / len(pulses)` frames. Pulse highlights are
strong by default. Boxes receive rounded rectangular halos, and diamonds receive
diamond-shaped halos. The loop is `canvas.frames / canvas.fps` seconds; to slow it
down, raise `frames`. Each highlighted node needs 0.4 s or more, and the lint says
how many frames that takes.

Both modes: keep `fps` at 12 or above — a lower rate makes the dots jump rather
than travel. For light output, use a higher-contrast motion color such as
`#0077ff`. For dark output, green motion colors such as `#2cff8f` work well.

## Copy Length Guidance

- Main title: 2 to 5 words
- Highlight phrase: 1 to 3 words
- Subtitle: 1 short sentence
- Node label: 1 to 3 words
- Node body: 1 to 4 short lines
- Edge label: 1 to 3 words

Short labels make better animations.
