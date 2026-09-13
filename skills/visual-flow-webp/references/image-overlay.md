# Animate a reference architecture image

Use this mode when the user supplies a picture and wants data moving on that
picture. Keep its boxes, icons, typography, layout and arrows. The renderer does
not detect components or infer direction: inspect the image and author the
anchors, paths and step order from what it shows and the user's description.
If an arrow is ambiguous, clarify its direction before animating that route.

## Prepare

1. Open the image with an image-viewing tool. Read the dimensions, orient it using
   EXIF if applicable, and identify components and arrow bends in source pixels.
   If viewing is unavailable, use coordinates the user provided and state that
   limitation. If those are also absent, request coordinates or a usable image;
   do not claim to have inspected it or invent a route.
2. State `Reading:` with actors, direction, parallel branches, overlay mode, MP4
   and concrete timing (usually 1.5 seconds per step and 1.5 seconds of hold).
   Honour an explicit WebP/GIF request.
3. Initialize in the working directory:

   ```bash
   python3 <skill-dir>/scripts/init_overlay.py \
     --image "./architecture.png" --spec "./architecture-flow.spec.json"
   ```

   This reads the native dimensions, makes a byte-for-byte companion copy named
   `architecture-flow.background.png` (or `.jpg` / `.webp`) and writes a spec
   skeleton. It refuses to overwrite existing files. Add the geometry and steps
   to this new spec; an empty skeleton is not a finished animation.

Supported inputs: PNG, JPEG and static WebP. Rasterize PDF/SVG first; keep the
original and describe the conversion. For a transparent image, `background.matte`
defaults to white and can be set explicitly. Coordinate origin is the top left
of the EXIF-oriented image, x rightwards, y downwards. The canvas must match that
image exactly: no stretching, cropping or implicit resizing. If a large source
needs reducing, create a separate resized copy and use its pixel coordinates.

## Describe the transparent layers

The composition is **source image → optional static annotations → animated
particles/trails and node outlines**. With only anchors and hidden paths, the
static PNG is pixel-identical to the decoded source (after EXIF orientation and
alpha matting); neither the original file nor its companion is modified.

```json
{
  "canvas": {"width": 1000, "height": 500, "fps": 20},
  "background": {"image": "architecture-flow.background.png"},
  "nodes": [
    {"id": "client", "type": "anchor", "label": "Client",
     "x": 80, "y": 180, "w": 160, "h": 120},
    {"id": "api", "type": "anchor", "label": "API",
     "x": 640, "y": 180, "w": 180, "h": 120}
  ],
  "edges": [
    {"from": "client", "to": "api",
     "points": [[240, 240], [440, 240], [440, 150], [640, 150], [640, 240]]}
  ],
  "animation": {
    "steps": [{"edges": [0], "pulse": ["api"]}],
    "step_seconds": 1.5, "hold_seconds": 1.5,
    "trail": false, "motion_color": "#007fdb"
  }
}
```

These coordinates only illustrate the fields; trace the user's actual arrows.

- `background.image` is resolved relative to the **spec's directory**, not the
  shell's working directory. Keep it relative so the delivered files can move.
- `type: "anchor"` is an invisible rectangle over an existing component. Its
  label is metadata and is not drawn. On arrival, it takes an outline pulse
  without a filled rectangle covering the original. Set a node's `stroke` to
  change that outline's colour. Set a step's `pulse: []` to omit outlines.
- Every edge needs `from`, `to` and explicit `points` following the visible arrow.
  Do not replace a bent arrow with a straight centre-to-centre connection.
  Corners are exact by default in overlay mode (`radius: 0`). Add more points
  for curves, or use an explicit radius where the source has rounded corners.
- Existing arrow strokes, arrowheads and labels are not redrawn. `draw_path`
  defaults to `false` in overlay mode, `true` in generated mode. Turn it on
  only when the user wants an added connection or annotation.
- `animation.steps` keeps causal order; one step containing several edge indices
  animates parallel branches together. Use `trail: false` for a clear image after
  particles pass. `trail: true` retains a faint line on travelled routes.
- Pick a motion colour that contrasts with the original image. Changing `theme`
  only styles overlays; it does not recolour the source.

## Export and inspect

```bash
python3 <skill-dir>/scripts/lint_spec.py ./architecture-flow.spec.json
python3 <skill-dir>/scripts/render_animated_webp.py \
  --spec ./architecture-flow.spec.json --outdir . --basename architecture-flow \
  --format mp4 --verify --check
```

The renderer composites each frame and streams it to ffmpeg. No browser or screen
recorder is needed; the resulting MP4 can be played, paused and scrubbed. MP4 uses
H.264/yuv420p with silent AAC and faststart. Odd image dimensions add one padding
pixel on the right/bottom for encoding. The video is lossy, so pixel-preservation
checks apply to the static PNG, not to decoded MP4 frames.

`--check` verifies source-file integrity and, for anchor-only overlays without
static paths, exact background pixels. It also checks animation and step timing.
These checks do not prove that traced paths match the source arrows: open the PNG
and extract representative MP4 frames (including a fan-out step, if present),
then inspect alignment, direction, contrast and label visibility. Fix and
re-render as needed.

Deliver the PNG, MP4, spec and companion background together. State the duration,
size and whether frames were visually inspected. To loop an MP4, enable
PowerPoint's **Loop until Stopped** or the `loop` attribute on an HTML `<video
controls loop muted playsinline>` element. The file does not self-loop.
