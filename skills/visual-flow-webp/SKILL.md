---
name: visual-flow-webp
description: >-
  Create animated flow diagrams from articles, workflow notes, architecture
  sketches, or process descriptions using a JSON specification and a local
  Python/Pillow renderer. Use when the user wants to turn source material into a
  static PNG + animated WebP diagram, asks for an animated diagram, a flow GIF or
  WebP, a moving/pulsing process diagram, an animated version of an AWS
  architecture picture with animated data-flow overlays that preserve the
  original image, or wants a written workflow visualised
  as a loop with motion. Also use when the animation has to be pausable or go on
  a slide -- PowerPoint, Keynote, Google Slides, a deck, or an HTML page with
  playback controls -- which is the MP4 output.
---

# Visual Flow WebP

Use this skill when a user wants to turn source material into a clear animated
flow diagram.

Deliver three files, all in the user's working directory:

- A static PNG diagram.
- An animation with visible motion, in one of three containers. Which one is a
  decision about where it is going, made in the `Reading:` block — see
  [Choosing the container](#choosing-the-container). WebP is the default.
- The JSON spec that produced them. It is not a by-product: it is the editable
  source, and the only way a later "move that box" request costs a line of JSON
  instead of a re-draw. Write it even when the user did not ask for it.

For a reference-image animation, also keep the companion background image beside
the spec. The spec needs that image to reproduce the animation.

**"Don't paste JSON at me" is about the reply, not the file.** Report the spec's
path; do not put its contents in your answer. Someone asking for a readable reply
is not asking to be handed 7 KB of JSON in a chat window.

**If the user asks you not to leave the spec behind, delete it — and say what that
costs.** One line: the diagram can now only be changed by writing the spec again
from scratch, and you will write it back on request. Their directory is theirs.
What is not acceptable is deleting it silently, deleting it as a tidying step
nobody asked for, or reporting a spec path that no longer exists.

Throughout this file `<skill-dir>` is the directory holding this SKILL.md — the
`Skill` tool result names it, and it differs between an installed plugin and a
copied one. Every `scripts/`, `references/` and `assets/` path below is relative
to it.

## Workflow

**Choose the source mode first.** When the user supplies an architecture image to
animate, preserve it as the background and follow
[Reference-image overlays](references/image-overlay.md). Do not rebuild its
nodes, labels or layout. A picture explicitly supplied only as a style reference,
or an explicit request to redraw, uses the generated mode instead. With no
architecture image, generate the diagram from the source description using the
workflow below. If an explicitly supplied image cannot be read, resolve that
problem rather than silently generating a replacement.

1. **Read the source material and name the system out loud — in your reply, before
   the first `Write`.** Put a short block in the conversation that starts with the
   word `Reading:` and lists, in a few lines, the actors, the steps in order, the
   shared stores, any decision point, and where the loop closes. It is prose the
   user can see and correct, not a comment in the JSON and not something to say
   afterwards in the report. A reading nobody can see is a reading nobody can
   correct, and this is the step where a misunderstanding is cheap to fix.

   Every run that skipped this step wrote a spec of what it assumed rather than
   what was asked; the eval checks for the `Reading:` block explicitly, and a spec
   written without one is graded as unread source material.

   - Inputs, outputs, actors, modules, tools, stores, and feedback loops.
   - Main steps, decision points, shared artifacts, and arrow direction.
   - Which edges leave one node together (parallel) versus one after another.
   - Any visual constraints from a reference image.
   - Source mode (`overlay` or `generated`), container and exact pacing values.

   **Editing an existing spec is a different step 1, not a skipped one.** When the
   user points at a spec they already have, the source material *is* that spec:
   `Read` it, and the `Reading:` block says what you will add or change and what
   stays as it is. It comes between the `Read` and the first `Write`, even when
   the change is two lines of pacing — scored runs that went straight from `Read`
   to `Write` lost the instruction-following row on exactly this. Do
   not read the bundled assets in step 2 — the existing spec is the format
   reference, and its node ids, coordinates, theme and title are the user's
   decisions to keep unless they asked you to move them. Grow the canvas or a
   background container if the new node needs room; do not shuffle the nodes
   that were already there.

2. **Write a JSON spec** to the working directory, e.g. `signal-loop.spec.json`.
   Not inside `<skill-dir>` — that directory is the skill's source, not your
   workspace.
   - **Generated mode only:** read `<skill-dir>/assets/default-spec.json` for the light theme's shape, or
     `<skill-dir>/assets/dark-spec.json` when the user asks for a dark version.
   - **They are format references, not answers.** Both ship a complete "Support
     Ops / Signal Loop" diagram. Rendering one of them unedited produces a
     beautiful animation of somebody else's system, passes every check, and answers
     nothing the user asked. Copy the structure; replace every node, edge, label
     and title with the system in front of you.
   - **Overlay mode:** initialize with `scripts/init_overlay.py`, then add
     invisible `anchor` nodes, edge `points` following the original arrows and
     `animation.steps`. Use source-image pixel coordinates; the overlay guide
     replaces the layout and styling instructions in this step.
   - Short English labels for newly drawn text unless the user asks for another language.
   - Explicit `x`, `y`, `w`, `h` on every node — layout is yours, not the
     renderer's. **Nothing checks this for you.** Two boxes given the same
     coordinates are drawn on top of each other and both the render and `--check`
     report success, so lay out on a grid and leave a visible gap — 20 px or more —
     between every pair of shapes. The exception is a `layer: "background"`
     container, which is *supposed* to sit under the nodes inside it.
   - A diamond's corners reach the full width and height you give it, so its
     left and right tips are the parts that collide with a neighbouring box.
     Measured from its bounding box, not from how it looks.
   - **Words, not symbols, in anything the renderer draws** — titles, node labels,
     node bodies, edge labels. The display font has no glyph for `→`, `←`, `✓`,
     `✗` or `×`; each renders as an empty box, so a subtitle written
     `S3 → Glue → Parquet` comes out as `S3 □ Glue □ Parquet`. Write `to`, `then`,
     `ok`, `fail`, and let the arrows in `edges` carry direction. Your reply to the
     user is prose, not a rendered label — arrows there are fine.
   - Custom multi-point paths for routed arrows. Corners round automatically.
   - **The animation is the only part of the deliverable that says *when* things
     happen, and `animation.steps` is how you say it.** Each step lists the edge
     indices whose dots travel together and, optionally, the node lit when they
     arrive; the renderer plays the steps in order, keeps a faint trail on edges
     already travelled, holds the finished picture for a moment, and loops. Use it
     whenever the source has an order — numbered stages, "first … then …", a
     request that goes in and a response that comes back. It is what the bundled
     assets use, and it is what "the dots should appear step by step" means.
     - **Fan-out goes in one step.** Three stages that all start from `build` are
       parallel: `{"edges": [3, 4, 5]}` moves three dots at once, which is what
       concurrency looks like. Splitting them across three steps plays them as a
       sequence, and a viewer comes away believing the scan waits for the tests.
     - **Mutually exclusive branches: narrate one, leave the other out.** `success`
       and `rollback` both leave `smoke-test`; a step travelling both says both
       happen. Put the branch you are telling in a step and leave the other edge
       unlisted — the lint prints a note naming it, so the omission is visible and
       deliberate.
     - **Each step must start where the story is.** A step's edges leave nodes an
       earlier step arrived at (or a node nothing points to). A loop edge closing
       back to the start (`runbook -> alarm`) is a legitimate last step — it is an
       edge.
     - `canvas.frames` is derived from the schedule in this mode — do not set it.
       Pace with `step_seconds` (default 1.2) and `hold_seconds` (default 0.8); see
       [Pacing](#pacing).
   - **`animation.pulses` is the older, continuous mode**: every edge carries a dot
     for the whole loop and the highlight walks the pulse list. Keep it for a diagram
     with no ordering to tell — a cycle where everything runs all the time. The same
     rule applies: **every consecutive pair in `pulses` must be joined by an edge,
     pointing the way the list goes**, and parallel or exclusive branches are never
     listed in a row. The renderer ignores `pulses` when `steps` is present; a spec
     with both fails the lint.
   - Nothing about ordering is checked by `--check` alone, and a still frame
     cannot show it: a highlight that teleports from a retry branch to the happy
     path looks exactly like one that advanced a step. Step 3's lint is what
     catches it, and in steps mode `--check` then proves it on the written file.
   - `<skill-dir>/references/spec-format.md` is background, not a step: consult it
     when a field's meaning or its allowed values are unclear. The bullets above
     plus one asset are enough to write a spec, and reading the reference every
     time buys nothing.

3. **Lint the spec before rendering it.**

   ```bash
   python3 <skill-dir>/scripts/lint_spec.py ./signal-loop.spec.json
   ```

   Pure stdlib, no Pillow, instant. It catches the defects that render
   *successfully* and are invisible afterwards: overlapping nodes, a step or pulse
   sequence that contradicts the edges, and pacing a viewer cannot follow. It also
   prints the loop length it expects, so you can check that against what the user
   asked for before spending a render. Re-run until `✓ LINT PASS`. Every problem it
   prints names the fix; lines marked `·` are notes, not failures.

   Fixing a spec here costs a line of JSON. Fixing it after the render costs a
   re-render, and finding it after delivery costs the reader's trust in the diagram.

4. **Render.**

   Needs Python 3.10+, Pillow 10+ and, for any spec that uses `icon`, the
   `diagrams` package for its icon set. Check before installing: if
   `python3 -c "import PIL, diagrams"` succeeds, both are there and this step is a
   precondition you have already met, not a step to perform. Only on a fresh
   machine:

   ```bash
   python3 -m pip install -r <skill-dir>/requirements.txt
   ```

   ```bash
   python3 <skill-dir>/scripts/render_animated_webp.py \
     --spec ./signal-loop.spec.json \
     --outdir . \
     --basename signal-loop \
     --verify --check
   ```

   This writes `signal-loop.png` and `signal-loop.webp`. Add `--format mp4` or
   `--format gif` for the other containers — see
   [Choosing the container](#choosing-the-container). The `✓ PASS` line states the
   frame count, the loop length in seconds and, in steps mode, the number of
   steps — that is the number to quote when the user asked for a particular pace.
   For `--format mp4` it also states the file size in KB.

   The renderer prints one verdict line — `✓ PASS …` or `✗ FAIL …` — and then a
   JSON report. Never skip `--verify --check`: see [Quality bar](#quality-bar).

5. **Fix what the report names, and re-render.** A `✗ FAIL` line names the failed
   checks; the JSON says what was expected and what was produced. Wrong canvas
   size, a frame count that does not match the spec, and an animation with no
   motion are all spec bugs, not renderer bugs.

6. **Report, and claim only what you checked.** Give all three paths — PNG,
   animation, spec — and say what each is for. Say how long one loop runs and, in
   steps mode, which steps play in which order; both are read straight off the
   `✓ PASS` line and the spec, so they can be stated as fact. **After an MP4, say
   that it does not loop on its own** and name the one setting that makes it:
   `loop` on an HTML `<video>` tag, or PowerPoint's *Playback → Loop until
   Stopped*. A WebP loops by itself, so a reader coming from one will assume this
   one does too and file the difference as a broken file.

   If you can view images, open the PNG and confirm the things only a look
   catches: text readable and not clipped, labels and arrows clear of each other,
   arrow directions matching the source, pulse highlights visible against the
   background, the whole system inside the frame.

   If you cannot view images, do not describe the drawing as though you had. The
   `--check` report proves dimensions, frame count and motion; it cannot see a
   collision, an unreadable label or an arrow pointing the wrong way. Report what
   it proves and leave the rest to the reader's own look — an unverified "text is
   readable" is worth less than nothing, because it stops them looking.

   **End the report with one sentence saying which of the two you did.** Either
   "I opened the PNG and checked: …" followed by what you actually saw, or "I did
   not view the image; `--check` proves the size, the frame count and that the
   frames differ, and nothing else." Words like *clear*, *clean*, *readable*,
   *well laid out* about the picture belong only after the first sentence. A
   description of the *spec* — which nodes, which edges, which order the pulses
   walk — is fine either way, because you wrote it and can read it back.

   Reading the file's size or mode with Pillow, or running `file` on it, is not
   viewing it. "Opened" means an image-viewing tool showed you the pixels. If
   the only tools you have are a shell and a file reader, the honest sentence is
   the second one.

   **Do not delete the spec as a tidying step nobody asked for.** If the user did
   ask, remove it, say you removed it, and say the diagram is no longer editable
   without re-authoring the spec.

## Choosing the container

Three containers, one decision, made from where the animation is going. State the
choice and the reason in the `Reading:` block, not after the render.

| Ask | Flag | Why |
|---|---|---|
| Anything not covered below | *(none)* | WebP: a quarter of a GIF at 24-bit colour, plays in browsers, GitHub, VS Code and chat clients, and loops by itself |
| Animate a supplied architecture image, with no container specified | `--format mp4` | Preserve the image under animated overlays and export a playable video |
| **Pause, stop, scrub, step through, study one stage** | `--format mp4` | An animated WebP has no playback controls anywhere. It loops from the moment it is on screen and there is no way to hold it on step 3 |
| **A slide: PowerPoint, Keynote, Google Slides, a deck, 演示文稿** | `--format mp4` | H.264 MP4 is the only one of the three a slide tool plays |
| **An HTML page where the reader controls playback** | `--format mp4` | `<video controls loop muted playsinline>` gives pause and a progress bar |
| The user names GIF, or their target shows neither of the others | `--format gif` | Some e-mail clients |

**MP4 is smaller, not a trade.** On the bundled reference render: 243 KB against
the WebP's 622 KB and the GIF's 9.7 MB, because H.264's inter-prediction beats
frame differencing on a picture that is mostly static. So "the file has to be
small" is not a reason to refuse MP4 — it is a reason to prefer it.

Two costs, both worth saying out loud rather than discovering:

- **It needs `ffmpeg` on PATH.** `brew install ffmpeg`, or `apt install ffmpeg`.
  Without it the renderer prints `✗ FAIL video encoder unavailable: …` and exits 2
  — that is a missing tool, not a broken spec, so do not start editing the spec.
  Either install it, or deliver WebP and say which you delivered and why.
- **It does not loop by itself.** WebP and GIF carry a loop flag; MP4 does not.
  The player decides, so the report has to name the setting (step 6).

Everything else is unchanged: same spec, same lint, same `--verify --check`. The
container is a delivery decision, so it is a flag and never a field in the spec —
one spec can be rendered to all three without editing a line.

## Pacing

The renderer draws whatever schedule the spec gives it, and the first thing users
said about the default was "slower". So pacing is a decision to make on purpose,
in the `Reading:` block, before the first render — and stated as the numbers you
will put in the spec (`step_seconds 1.5, hold_seconds 2`), not as a range. A range
is not something the user can confirm or correct; a scored run lost exactly that
assertion for writing "1 to 1.5 s" and then using 1.5.

- **Steps mode** (`animation.steps`): one step is one beat of the story. Default
  `step_seconds` is 1.2; use 1.0 for a diagram of a dozen short hops, 1.5 to 2.0
  when boxes are far apart or the user wants to read a label as the dot passes.
  `hold_seconds` (default 0.8) is the pause on the finished picture before the loop
  restarts; raise it to 1.5 to 2.0 for a diagram people will study. A 7-step
  flow at 1.2 s is about 9 s per loop, and the lint prints the number.
- **Continuous mode** (`animation.pulses`): the loop is `frames / fps` seconds and
  the highlight dwells `frames / len(pulses) / fps` on each node. Under 0.4 s per
  node it cannot be followed, and the lint fails it with the frame count that
  fixes it. To slow a continuous animation, raise `canvas.frames`.
- **Never slow an animation by lowering `fps`.** Below 12 the dots jump between
  positions instead of travelling; the lint fails it. Longer steps or more frames
  are the knobs.
- **"Slower" on an existing spec is a pacing edit, not a redraw.** Change
  `step_seconds` / `hold_seconds` (or `canvas.frames`), re-lint, re-render, and
  leave every node where it was.
- A longer loop is a bigger file, roughly linearly: the WebP encoder stores each
  frame as its difference from the last, so a 9 s render of a busy 1500 px
  diagram is about 1.6 MB. Say the size in the report if the user has to send it
  somewhere. If the size is the problem, `--format mp4` is the answer before
  shortening the animation is — it is roughly a third of the WebP at the same
  pace, so there is no need to cut a beat out of the story to save bytes.

## Style guide

This style guide applies to generated diagrams. In overlay mode, the original
image owns its appearance; choose overlay colours with visible contrast and keep
source labels unobscured.

- A clean technical diagram with a lightweight editorial feel.
- Light theme by default; dark only on request.
- Excalifont when available; the renderer falls back on its own.
- Node labels bold enough to scan in one pass.
- Rounded routed arrows over hard right-angle turns.
- Colour carries role, not decoration:
  - Blue for core process areas.
  - Green for active loops, tools, memory, or operational panels.
  - Purple for shared layers, archives, or internal systems.
  - Red for friction, risk, warnings, or signals.
- Keep node labels short and concrete: 1–3 words for a label, 1–4 short lines of
  body. Long copy is what makes these diagrams unreadable, and the renderer will
  not save you from it: it wraps and then shrinks the type until it fits, so a
  sentence in a label comes out as a grey smudge rather than as an obvious
  overflow you would have caught.
- **Do not invent structure, metrics, claims, or arrow direction that is not in
  the source.** An arrow the source material does not support is a wrong
  statement about the system, not a harmless bit of layout.
- **Every node is something the source names.** The recurring way this goes wrong
  is a helper box added to make the layout feel complete — a `Process`, `Sync`,
  `Status Update` or `Webhook POST` node standing between two things the source
  connects directly. "Lambda reports status to the repo" is one edge labelled
  `status`, not a node; "the webhook calls Lambda" is one edge labelled
  `webhook`. If you cannot point at the words in the source that name a box,
  do not draw it.
- **Icons come from the installed icon set, never from the source picture.** A
  node's `icon` is a key such as `aws/network/direct-connect`, looked up in the
  official architecture icons the `diagrams` package installs (AWS, GCP, Azure,
  k8s, on-prem, generic). Find keys with
  `python3 <skill-dir>/scripts/list_icons.py direct connect`; the lint rejects a
  key that does not exist and names the closest ones. Use `type: "icon"` (icon
  with a caption, no box) for instances, gateways, users and devices, the way
  the AWS reference diagrams draw them; put `icon` on a box or a background
  container to get the small corner icon a region, VPC or subnet carries, and
  move the container's title label right to make room. Do not crop icons out of
  the user's image, do not fetch them from the web, and leave `icon` off anything
  that is not a service or a standard actor. If the icon set is not installed,
  `pip install diagrams` is the fix; do not ship a render with gaps where icons
  were meant — `--check` fails it as `icons_resolved`.

## Quality bar

Always lint with `lint_spec.py`, and always render with `--verify --check`. They
answer different questions and neither substitutes for the other.

`lint_spec.py` reads the spec and catches what renders successfully anyway:

- Two foreground nodes occupying the same pixels — one is drawn over the other, and
  the render, `--check` and the animation all look fine.
- A step schedule or pulse sequence that is not a walk along the edges: parallel
  branches split into consecutive steps or listed in a row, a step leaving a node
  no earlier step reached, a `pulse` on a box the step's arrows do not arrive at,
  a pair joined the other way round. The animation is the only place the flow's
  ordering is expressed, so a wrong sequence is a wrong statement about the
  system, told confidently.
- Pacing nobody can follow: a step under 0.5 s, a highlight held under 0.4 s, a
  frame rate under 12.
- A frame count too low to animate, a pulse naming a node that does not exist
  (silently dropped by the renderer), and `pulses` set alongside `steps` (ignored
  by the renderer).

`--verify` samples frames and reports how many pixels changed between them, which
is how "the animation plays" stops being an assumption.

`--check` validates that:

- PNG and animation (WebP, or GIF / MP4 with `--format`) files exist.
- Every `icon` key the spec names was found and drawn (`icons_resolved`).
- Output dimensions match the spec. For MP4, rounded up to even — yuv420p has no
  valid odd-sized encoding, so an odd canvas is padded by a pixel.
- Animation frame count matches the spec (in steps mode, the derived schedule;
  the encoder may merge identical hold frames, and the check allows exactly that).
- Sampled frames differ, i.e. there is visible motion. In steps mode the samples
  are the step midpoints.
- In steps mode, `steps_confined`: at each step's midpoint frame of the written
  file, every edge of that step carries a dot and no edge of a later step has
  been touched. This is the check that turns "the dots appear step by step" from
  a claim about the spec into a fact about the file.
- For `--format mp4`, four more rows read back off the file: `video_codec`,
  `video_pix_fmt`, `video_has_audio_track` and `video_faststart`. They are the
  difference between a file that plays on a slide and one that plays only on the
  machine that made it, and none of them is visible by looking at the animation.

A non-zero exit means the deliverable is broken. Do not report a diagram whose
last render printed `✗ FAIL`.

---

Adapted for this repo from `skills/visual-flow-gif` in
[AI-Builder-Club/skills](https://github.com/AI-Builder-Club/skills) (MIT, ©
Jason Zhou / AI Builder Club — see `LICENSE-upstream`). Changes: plugin-root
paths replaced with the `<skill-dir>` convention this repo's sandbox resolves;
the dependency install turned into a precondition so runs stop reinstalling
Pillow; the spec JSON made a required deliverable rather than an optional one,
since it is what the layer-2 evaluator reads and what a follow-up edit starts
from; the shipped specs marked as format references after the trap they set was
made explicit; visual inspection made conditional on being able to view images,
so a run without vision reports honestly instead of claiming a look it never
took; and three changes to the renderer, now `scripts/render_animated_webp.py` —
a one-line `✓ PASS` / `✗ FAIL` verdict printed ahead of its JSON report; clamped
frame sampling in `motion_report`, which used to raise `EOFError` on an animation
with no motion, i.e. crashed on exactly the defect `--verify` exists to detect; and
animated WebP as the default container, GIF kept behind `--format gif`.

`--format mp4` was added later, for the one thing neither image format does: stop.
An animated WebP has no playback controls anywhere, and no slide tool plays one. It
turned out to be the smaller file as well — 243 KB against the WebP's 622 KB on the
reference render — so it is not a size-for-control trade.

Adding it found a defect in `steps_confined` that WebP had been hiding. The check
sampled one point, the arc midpoint of each edge, and at the step's midpoint frame
the dot is actually at t=0.43 — so it had been detecting the trailing edge of the
glow, 19 pixels at 70 against a threshold of 64. A 6-level margin. H.264's
smoothing pushed those pixels just under it, and a check that had been passing for
the wrong reason reported a missing dot. It now samples a band of the route, where
the same dot is 171 pixels at 233 in both containers. The margin, not the format,
was the bug.

Two rules here were not adapted from anywhere — they were written from defects the
evaluation caught in scored runs: the no-overlap layout rule (a spec whose boxes
sat 15 px into each other, reported by the geometry check and confirmed
independently by the vision judge) and "words, not symbols" (a subtitle that
rendered `S3 □ Glue □ Parquet`, because the display font has no `→`). `eval/` now
checks both.
