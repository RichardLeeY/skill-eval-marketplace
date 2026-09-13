#!/usr/bin/env python3
import argparse
import hashlib
import io
import itertools
import json
import math
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps


DEFAULT_CANVAS = {"width": 1280, "height": 800, "fps": 20, "frames": 48}
RENDER_SCALE = 2

# Pacing. Two animation modes share one renderer:
#
#   continuous  (default) every path carries a moving dot for the whole loop and the
#               node highlight walks `animation.pulses`; `canvas.frames` / `fps` set
#               the loop length. Each pulse dwells for frames / len(pulses), never
#               less than MIN_PULSE_FRAMES, so raising `frames` really slows the
#               whole animation down instead of only the dots.
#   steps       `animation.steps` is an ordered list; each step names the edges that
#               travel together and the node(s) lit when they arrive. Dots appear
#               one step at a time, travelled edges keep a faint trail, and the last
#               frame holds before the loop restarts. The frame count is derived
#               from the schedule -- `canvas.frames` is ignored -- so the animation
#               is as long as the story needs.
DEFAULT_STEP_SECONDS = 1.2
DEFAULT_HOLD_SECONDS = 0.8
MIN_PULSE_FRAMES = 7

# Icons. A node may carry `icon: "aws/network/direct-connect"`. Keys are paths
# inside the icon set that the `diagrams` package (pip install diagrams) installs
# beside itself as `resources/<provider>/<category>/<name>.png` -- the official
# AWS Architecture Icons, plus GCP, Azure, k8s, on-prem and generic sets. The icons
# are a dependency, not a repo asset: nothing is vendored here, and the same key
# renders the same picture on any machine with the package installed.
# VISUAL_FLOW_ICON_DIR overrides the root; a key ending in .png is read as a path.
DEFAULT_ICON_SIZE = 36        # spec units, for an icon in a box's corner
DEFAULT_ICON_NODE_SIZE = 56   # spec units, for a `type: "icon"` node

# Animated WebP by default. On the bundled 48-frame spec it is a quarter of the GIF's
# size (1.6 MB against 6.7 MB) and encodes faster, with 24-bit colour instead of a
# 256-entry palette. GIF stays available with --format gif for the odd viewer that
# still cannot play WebP.
#
# `mp4` exists for the one thing neither image format can do: **stop.** An animated
# WebP has no playback controls anywhere -- it loops from the moment it is on screen
# and a viewer who wants to study step 3 cannot hold it there. An MP4 in `<video
# controls>` or on a PowerPoint slide pauses, scrubs and steps. It is also smaller:
# measured on a 150-frame 1520x800 steps render, 347 KB against the WebP's 538 KB,
# because H.264 inter-prediction beats libwebp's frame differencing on a picture
# that is mostly static. See VIDEO_* below for why the encoder settings are not
# negotiable.
FORMATS = {
    # minimize_size lets libwebp encode each frame as the difference from the last
    # one. On a 188-frame steps-mode render it is 1.6 MB against 8.3 MB without,
    # and encodes faster, because most of every frame is the unchanged diagram.
    "webp": {"suffix": ".webp", "pillow": "WEBP", "save": {"quality": 90, "method": 4, "minimize_size": True}},
    "gif": {"suffix": ".gif", "pillow": "GIF", "save": {"optimize": False}},
    "mp4": {"suffix": ".mp4", "video": True, "crf": 23},
}
DEFAULT_FORMAT = "webp"

# H.264 settings, each one load-bearing rather than a default someone liked:
#
#   yuv420p        PowerPoint and hardware decoders reject 4:2:0's alternatives.
#                  yuv444p keeps small text very slightly sharper -- SSIM 0.9994
#                  against 0.9995 on the reference render, i.e. not measurably --
#                  and buys a file that will not play on the target that motivated
#                  the format. Verified as a --check row, because a silently
#                  yuv444p file plays fine here and fails on the slide.
#   crf 23         Text is what these diagrams are made of. At crf 26 the same
#                  render is 280 KB and 2x-zoom crops of `10.0.1.0/24` are still
#                  clean, so 23 is one step of headroom, not a guess.
#   keyframe / 2s  Pausing and scrubbing is the entire point of the format. Seeks
#                  land on the previous keyframe, so a GOP as long as the clip
#                  makes the progress bar useless while the file gets no smaller
#                  in any way that matters here.
#   silent audio   PowerPoint refuses, or plays black, on some video-only MP4s.
#                  A 32 kbps silent AAC track is a rounding error against the
#                  video and removes a failure the author cannot reproduce.
#   +faststart     Moves the index to the front so a browser can start playing
#                  before the whole file arrives.
VIDEO_CRF = 23
VIDEO_KEYFRAME_SECONDS = 2
VIDEO_PIX_FMT = "yuv420p"
VIDEO_AUDIO_BITRATE = "32k"


class VideoToolMissing(RuntimeError):
    """`--format mp4` was asked for and there is no ffmpeg to honour it.

    Raised rather than falling back to WebP. A silent fallback hands back a file
    that cannot be paused, which is the one property the caller asked for, under a
    name that does not say so.
    """


def _tool(name):
    found = shutil.which(name)
    if not found:
        raise VideoToolMissing(
            f"--format mp4 needs {name} on PATH (brew install ffmpeg / apt install ffmpeg). "
            f"Use --format webp for an animation that needs no encoder.")
    return found


def even(value):
    """The next even number at or above `value`.

    yuv420p stores one chroma sample per 2x2 block of luma, so an odd width or
    height has no valid encoding. A spec is free to declare 1401x721; padding it by
    a pixel is invisible, and refusing it would be a renderer limitation dressed up
    as a spec error.
    """
    return int(value) + (int(value) % 2)


def encode_video(frames, path, fps, crf=VIDEO_CRF, bg="#000000"):
    """Encode `frames` (PIL RGB images) to H.264 MP4, returning what was written.

    Frames go in as raw RGB over a pipe rather than as PNGs in a temp directory:
    a 150-frame render is 49 MB of intermediate PNG that never needs to exist.
    """
    ffmpeg = _tool("ffmpeg")
    frames = iter(frames)
    first = next(frames)
    width, height = first.size
    out_w, out_h = even(width), even(height)
    gop = max(1, int(round(fps * VIDEO_KEYFRAME_SECONDS)))
    cmd = [ffmpeg, "-y", "-v", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}",
           "-framerate", str(fps), "-i", "-",
           # Silent stereo track: see VIDEO_* above. anullsrc is infinite, so
           # -shortest is what ends the file with the video.
           "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    if (out_w, out_h) != (width, height):
        cmd += ["-vf", f"pad={out_w}:{out_h}:0:0:color={bg}"]
    cmd += ["-c:v", "libx264", "-preset", "slow", "-crf", str(crf),
            # Let x264 choose a conforming level for the source dimensions/fps;
            # a large reference image can exceed level 4.0's 1080p limits.
            "-pix_fmt", VIDEO_PIX_FMT, "-profile:v", "high",
            "-g", str(gop), "-keyint_min", str(gop),
            "-c:a", "aac", "-b:a", VIDEO_AUDIO_BITRATE, "-shortest",
            "-movflags", "+faststart", str(path)]
    # List form, no shell: every path is an argv element, not shell-parsed.
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,  # nosemgrep: dangerous-subprocess-use-audit
                            stderr=subprocess.PIPE)
    try:
        for frame in itertools.chain((first,), frames):
            proc.stdin.write(frame.convert("RGB").tobytes())
        proc.stdin.close()
    except BrokenPipeError:
        pass
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    proc.stdin = None
    _, err = proc.communicate()
    if proc.returncode != 0:
        raise VideoToolMissing(f"ffmpeg failed (exit {proc.returncode}): "
                               f"{(err or b'').decode('utf-8', 'replace').strip()[:400]}")
    return {"width": out_w, "height": out_h, "padded": (out_w, out_h) != (width, height),
            "keyframe_interval": gop, "crf": crf, "pix_fmt": VIDEO_PIX_FMT}


def probe_video(path):
    """What the written MP4 actually is, read back with ffprobe.

    Read from the file rather than from what `encode_video` intended, for the same
    reason the frame checks are: the deliverable is the file, and an encoder that
    quietly ignored a flag is exactly the defect worth catching.
    """
    ffprobe = _tool("ffprobe")
    proc = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
        [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True)
    if proc.returncode != 0:
        raise VideoToolMissing(f"ffprobe could not read {path}: {proc.stderr.strip()[:300]}")
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    frames = video.get("nb_frames")
    if frames in (None, "N/A"):
        counted = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
            [ffprobe, "-v", "error", "-count_frames", "-select_streams", "v:0",
             "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True)
        frames = counted.stdout.strip() or "0"
    return {
        "codec": video.get("codec_name"),
        "profile": video.get("profile"),
        "pix_fmt": video.get("pix_fmt"),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "n_frames": int(frames or 0),
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
        "faststart": _faststart(path),
    }


def _faststart(path):
    """Whether the `moov` index precedes the media data.

    Read from the box order in the first kilobytes rather than by asking ffprobe,
    which reports the same stream information either way. Without it a browser
    buffers the whole file before the first frame appears.
    """
    with open(path, "rb") as fh:
        head = fh.read(65536)
    moov, mdat = head.find(b"moov"), head.find(b"mdat")
    return moov != -1 and (mdat == -1 or moov < mdat)


def video_frame(path, index):
    """One decoded frame of an MP4 as a PIL RGB image.

    Per-frame rather than a bulk extraction: the callers ask for a handful of
    frames -- four sample points, or one per step -- and decoding the whole clip to
    disk to answer that costs 300 MB on a long render.
    """
    ffmpeg = _tool("ffmpeg")
    proc = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
        [ffmpeg, "-v", "error", "-i", str(path), "-vf", f"select=eq(n\\,{int(index)})",
         "-fps_mode", "passthrough", "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
        capture_output=True)
    if proc.returncode != 0 or not proc.stdout:
        raise VideoToolMissing(
            f"could not decode frame {index} of {path}: "
            f"{(proc.stderr or b'').decode('utf-8', 'replace').strip()[:200]}")
    return Image.open(io.BytesIO(proc.stdout)).convert("RGB")


def is_video(path):
    return str(path).lower().endswith(".mp4")


class Frames:
    """Uniform frame access over an animated image or an MP4.

    `motion_report` and `steps_report` both need "how many frames, and give me
    frame N as RGB", and both must read the *written* file -- the encoder merges
    identical hold frames, so the file and the intent legitimately differ. Pillow
    answers that for WebP and GIF and cannot open an MP4 at all, so the ffmpeg path
    answers the same two questions rather than the callers growing a branch each.
    """

    def __init__(self, path, size=None):
        #: `size` crops decoded frames back to the canvas. An odd-sized canvas is
        #: padded to even for yuv420p, so without this the MP4's frames are a pixel
        #: wider than the PNG they are diffed against and ImageChops raises.
        self.path = str(path)
        self.video = is_video(path)
        self.size = tuple(size) if size else None
        self._im = None
        if self.video:
            self.n_frames = probe_video(self.path)["n_frames"]
        else:
            self._im = Image.open(self.path)
            self.n_frames = self._im.n_frames

    def rgb(self, index):
        index = min(max(0, int(index)), max(0, self.n_frames - 1))
        if self.video:
            frame = video_frame(self.path, index)
        else:
            self._im.seek(index)
            frame = self._im.convert("RGB")
        if self.size and frame.size != self.size:
            frame = frame.crop((0, 0, self.size[0], self.size[1]))
        return frame

    def close(self):
        if self._im is not None:
            self._im.close()
            self._im = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

THEMES = {
    "light": {
        "bg": "#f6f2df",
        "text": "#15120f",
        "muted": "#665d50",
        "frame": "#cfc7b6",
        "panel": "#fffaf0",
        "blue": "#2f6fb6",
        "blue_fill": "#edf5ff",
        "green": "#3b946d",
        "green_fill": "#eef8ef",
        "purple": "#8d4ba7",
        "purple_fill": "#f7ecfb",
        "red": "#b85f56",
        "red_fill": "#fff0ec",
        "dark": "#5b6468",
        "dark_fill": "#f5f2e9",
        "highlight": "#dff0cf",
        "motion": "#0077ff",
        "motion_core": "#ffffff",
        "grain_min": 1,
        "grain_max": 5,
        "vignette": 20,
    },
    "dark": {
        "bg": "#050607",
        "text": "#f2f1ec",
        "muted": "#c8cbc5",
        "frame": "#596268",
        "panel": "#070a08",
        "blue": "#41a6ff",
        "blue_fill": "#041923",
        "green": "#2bd87d",
        "green_fill": "#03180c",
        "purple": "#c56bff",
        "purple_fill": "#14081a",
        "red": "#d07367",
        "red_fill": "#5a2924",
        "dark": "#596268",
        "dark_fill": "#070a08",
        "highlight": "#173f35",
        "motion": "#2cff8f",
        "motion_core": "#f2f1ec",
        "grain_min": 3,
        "grain_max": 12,
        "vignette": 110,
    },
}


def animation_mode(spec):
    steps = (spec.get("animation") or {}).get("steps")
    return "steps" if isinstance(steps, list) and steps else "continuous"


def step_pulse_ids(step, edges):
    """The nodes a step lights up: `pulse` if given, else where its arrows arrive."""
    raw = step.get("pulse")
    if raw is None:
        out = []
        for idx in step.get("edges") or []:
            if isinstance(idx, int) and 0 <= idx < len(edges):
                target = edges[idx].get("to")
                if target and target not in out:
                    out.append(target)
        return out
    return [raw] if isinstance(raw, str) else [str(item) for item in raw]


def build_schedule(spec, canvas):
    """Frame ranges for `steps` mode. None in continuous mode.

    Every step gets round(fps * seconds) frames, then the whole diagram holds for
    `hold_seconds`. The total is what `canvas.frames` becomes; --check compares
    the written file against it.
    """
    if animation_mode(spec) != "steps":
        return None
    animation = spec.get("animation") or {}
    edges = spec.get("edges", [])
    fps = max(1, int(canvas["fps"]))
    default_seconds = float(animation.get("step_seconds", DEFAULT_STEP_SECONDS))
    hold_seconds = float(animation.get("hold_seconds", DEFAULT_HOLD_SECONDS))
    steps = []
    cursor = 0
    for index, raw in enumerate(animation["steps"]):
        raw = raw if isinstance(raw, dict) else {"edges": raw}
        seconds = float(raw.get("seconds", default_seconds))
        length = max(2, int(round(fps * seconds)))
        edge_ids = [idx for idx in (raw.get("edges") or []) if isinstance(idx, int) and 0 <= idx < len(edges)]
        steps.append({
            "step": index,
            "label": raw.get("label", ""),
            "edges": edge_ids,
            "pulse": step_pulse_ids(raw, edges),
            "seconds": seconds,
            "start": cursor,
            "end": cursor + length,
        })
        cursor += length
    hold = max(0, int(round(fps * hold_seconds)))
    return {
        "mode": "steps",
        "fps": fps,
        "steps": steps,
        "hold": {"start": cursor, "end": cursor + hold, "seconds": hold_seconds},
        "frames": cursor + hold,
        "seconds": round((cursor + hold) / fps, 2),
        "trail": bool(animation.get("trail", True)),
    }


def resolve_canvas(spec):
    """Canvas with `frames` settled: the spec's own in continuous mode, derived in steps mode."""
    canvas = {**DEFAULT_CANVAS, **spec.get("canvas", {})}
    schedule = build_schedule(spec, canvas)
    if schedule:
        canvas["frames"] = schedule["frames"]
    return canvas, schedule


def smoothstep(value):
    value = min(1.0, max(0.0, value))
    return value * value * (3 - 2 * value)


def icon_root():
    """Directory holding `<provider>/<category>/<name>.png`, or None when absent."""
    override = os.environ.get("VISUAL_FLOW_ICON_DIR")
    if override:
        return Path(override) if Path(override).is_dir() else None
    try:
        import diagrams  # noqa: F401 - only its location is needed
    except ImportError:
        return None
    base = Path(diagrams.__file__).resolve().parent
    for candidate in (base.parent / "resources", base / "resources"):
        if candidate.is_dir():
            return candidate
    return None


def icon_path(key):
    """`(path, problem)`: the PNG for an icon key, or why it could not be found."""
    if not key:
        return None, None
    key = str(key)
    if key.lower().endswith(".png"):
        path = Path(key)
        return (path, None) if path.is_file() else (None, f"icon file {key!r} does not exist")
    root = icon_root()
    if root is None:
        return None, (f"icon {key!r} needs the diagrams package for its icon set: "
                      f"python3 -m pip install diagrams (or set VISUAL_FLOW_ICON_DIR)")
    path = root / f"{key}.png"
    if path.is_file():
        return path, None
    return None, f"icon {key!r} is not in the icon set at {root} (try scripts/list_icons.py <words>)"


def px(value):
    return int(round(float(value) * RENDER_SCALE))


def rgba(hex_color, alpha=255):
    raw = hex_color.strip().lstrip("#")
    return tuple(int(raw[i : i + 2], 16) for i in (0, 2, 4)) + (int(alpha),)


def contains_cjk(text):
    return any("\u3400" <= char <= "\u9fff" for char in str(text))


def font_paths(cjk=False, display=False, bold=False):
    regular = [
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "C:\\Windows\\Fonts\\arialbd.ttf" if bold else "C:\\Windows\\Fonts\\arial.ttf",
    ]
    if cjk:
        return [
            "/System/Library/Fonts/STHeiti Medium.ttc" if bold else "/System/Library/Fonts/STHeiti Light.ttc",
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "C:\\Windows\\Fonts\\msyhbd.ttc" if bold else "C:\\Windows\\Fonts\\msyh.ttc",
        ] + regular
    if display:
        return [
            str(Path.home() / "Library/Fonts/Excalifont-Regular.woff2"),
            "/Library/Fonts/Excalifont-Regular.woff2",
            "/System/Library/Fonts/Supplemental/Excalifont-Regular.woff2",
            str(Path.home() / "Library/Fonts/Excalifont-Regular.ttf"),
            "/Library/Fonts/Excalifont-Regular.ttf",
            "/System/Library/Fonts/Supplemental/Chalkduster.ttf",
            "/System/Library/Fonts/MarkerFelt.ttc",
            "/System/Library/Fonts/Supplemental/Bradley Hand Bold.ttf",
            "/System/Library/Fonts/Supplemental/ChalkboardSE.ttc",
            "/System/Library/Fonts/Noteworthy.ttc",
            "/usr/share/fonts/truetype/comic-neue/ComicNeue-Bold.ttf" if bold else "/usr/share/fonts/truetype/comic-neue/ComicNeue-Regular.ttf",
        ] + regular
    return regular


def get_font(size, text="", display=False, bold=False):
    use_cjk = contains_cjk(text)
    for candidate in font_paths(cjk=use_cjk, display=display and not use_cjk, bold=bold):
        try:
            return ImageFont.truetype(candidate, px(size))
        except OSError:
            pass
    return ImageFont.load_default()


def text_box(draw, text, font, spacing=4):
    if not text:
        return 0, 0
    box = draw.multiline_textbbox((0, 0), str(text), font=font, spacing=px(spacing))
    return box[2] - box[0], box[3] - box[1]


def wrap_words(draw, line, font, max_width):
    text = str(line)
    tokens = list(text) if contains_cjk(text) else text.split()
    if not tokens:
        return [""]
    separator = "" if contains_cjk(text) else " "
    rows = []
    active = ""
    for token in tokens:
        proposal = token if not active else f"{active}{separator}{token}"
        if text_box(draw, proposal, font)[0] <= max_width:
            active = proposal
            continue
        if active:
            rows.append(active)
            active = ""
        chunk = ""
        for char in token:
            test = chunk + char
            if chunk and text_box(draw, test, font)[0] > max_width:
                rows.append(chunk)
                chunk = char
            else:
                chunk = test
        active = chunk
    if active:
        rows.append(active)
    return rows


def wrap_text(draw, text, font, max_width):
    rows = []
    for raw_line in str(text).splitlines():
        rows.extend(wrap_words(draw, raw_line, font, max_width))
    return "\n".join(rows) if rows else ""


def fitted_text(draw, text, width, height, start_size, min_size=8, display=True, bold=False, spacing=4, wrap=True):
    source = str(text or "")
    for size in range(int(start_size), int(min_size) - 1, -1):
        font = get_font(size, source, display=display, bold=bold)
        candidates = [wrap_text(draw, source, font, px(width))] if wrap else [source]
        if wrap and candidates[0] != source:
            candidates.append(source)
        for candidate in candidates:
            tw, th = text_box(draw, candidate, font, spacing=spacing)
            if tw <= px(width) and th <= px(height):
                return candidate, font
    font = get_font(min_size, source, display=display, bold=bold)
    return wrap_text(draw, source, font, px(width)) if wrap else source, font


def draw_fitted(draw, text, x, y, width, height, size, fill, align="center", bold=False, min_size=8, spacing=4, wrap=True):
    fitted, font = fitted_text(draw, text, width, height, size, min_size=min_size, bold=bold, spacing=spacing, wrap=wrap)
    tw, th = text_box(draw, fitted, font, spacing=spacing)
    if align == "left":
        tx = px(x)
    elif align == "right":
        tx = px(x + width) - tw
    else:
        tx = px(x) + (px(width) - tw) / 2
    ty = px(y) + (px(height) - th) / 2
    offsets = [(0, 0)]
    if bold:
        offsets = [(0, 0), (1, 0), (0, 1), (1, 1)]
    for ox, oy in offsets:
        draw.multiline_text((tx + ox, ty + oy), fitted, font=font, fill=rgba(fill), spacing=px(spacing), align=align)


def segment_length(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


def line_length(points):
    return sum(segment_length(left, right) for left, right in zip(points, points[1:]))


def point_on_line(points, distance):
    if not points:
        return (0, 0)
    remaining = distance
    for left, right in zip(points, points[1:]):
        length = segment_length(left, right)
        if length <= 0:
            continue
        if remaining <= length:
            t = remaining / length
            return (left[0] + (right[0] - left[0]) * t, left[1] + (right[1] - left[1]) * t)
        remaining -= length
    return points[-1]


def point_at_progress(points, progress):
    total = line_length(points)
    return point_on_line(points, (progress % 1.0) * total) if total else points[0]


def rounded_route(points, radius=30, steps=10):
    if len(points) < 3 or radius <= 0:
        return points
    route = [points[0]]
    for before, corner, after in zip(points, points[1:], points[2:]):
        incoming = segment_length(before, corner)
        outgoing = segment_length(corner, after)
        if incoming <= 0 or outgoing <= 0:
            route.append(corner)
            continue
        in_unit = ((before[0] - corner[0]) / incoming, (before[1] - corner[1]) / incoming)
        out_unit = ((after[0] - corner[0]) / outgoing, (after[1] - corner[1]) / outgoing)
        cross = abs(in_unit[0] * out_unit[1] - in_unit[1] * out_unit[0])
        if cross < 0.01:
            route.append(corner)
            continue
        distance = min(radius, incoming * 0.42, outgoing * 0.42)
        start = (corner[0] + in_unit[0] * distance, corner[1] + in_unit[1] * distance)
        end = (corner[0] + out_unit[0] * distance, corner[1] + out_unit[1] * distance)
        route.append(start)
        for index in range(1, steps):
            t = index / steps
            x = (1 - t) * (1 - t) * start[0] + 2 * (1 - t) * t * corner[0] + t * t * end[0]
            y = (1 - t) * (1 - t) * start[1] + 2 * (1 - t) * t * corner[1] + t * t * end[1]
            route.append((x, y))
        route.append(end)
    route.append(points[-1])
    return route


class FlowRenderer:
    def __init__(self, spec, spec_dir=None):
        self.spec = spec
        self.canvas, self.schedule = resolve_canvas(spec)
        self.icon_cache = {}
        self.icon_problems = []   # [{"node": id, "icon": key, "problem": text}], filled while drawing
        self.icons_drawn = 0
        self._scene = None        # the RGBA scene image while nodes are being drawn
        self.theme = self.load_theme(spec.get("theme", {"name": "light"}))
        self.nodes = {node["id"]: node for node in spec.get("nodes", [])}
        self.background = None
        self.background_path = None
        self.background_sha256 = None
        if "background" in spec:
            raw = spec["background"]
            if not isinstance(raw, dict) or not isinstance(raw.get("image"), str) or not raw["image"]:
                raise ValueError("background.image must name a local PNG, JPEG or static WebP file")
            self.background_path = (Path(spec_dir or ".") / raw["image"]).resolve()
            self.background_sha256 = hashlib.sha256(self.background_path.read_bytes()).hexdigest()
            with Image.open(self.background_path) as image:
                if image.format not in {"PNG", "JPEG", "WEBP"} or getattr(image, "n_frames", 1) != 1:
                    raise ValueError("background.image must be PNG, JPEG or static WebP; rasterize other inputs first")
                oriented = ImageOps.exif_transpose(image).convert("RGBA")
            size = (self.canvas["width"], self.canvas["height"])
            if not all(key in spec.get("canvas", {}) for key in ("width", "height")) or size != oriented.size:
                raise ValueError(f"canvas.width/height must match the oriented background size {oriented.width}x{oriented.height}; no stretching or cropping")
            matte = Image.new("RGBA", size, raw.get("matte", "#ffffff"))
            matte.alpha_composite(oriented)
            self.background = matte.convert("RGB")

    def load_theme(self, raw_theme):
        if isinstance(raw_theme, str):
            preset = raw_theme
            overrides = {}
        else:
            raw_theme = raw_theme or {}
            preset = raw_theme.get("name") or raw_theme.get("preset") or "light"
            overrides = {key: value for key, value in raw_theme.items() if key not in {"name", "preset", "mode"}}
        if preset not in THEMES:
            raise ValueError(f"unknown theme preset: {preset}")
        return {**THEMES[preset], **overrides}

    def node_colors(self, node):
        if node.get("stroke") or node.get("fill"):
            return node.get("stroke", self.theme["frame"]), node.get("fill", self.theme["panel"])
        key = node.get("color", "green")
        return self.theme.get(key, self.theme["green"]), self.theme.get(f"{key}_fill", self.theme["panel"])

    def blank(self):
        return Image.new("RGBA", (px(self.canvas["width"]), px(self.canvas["height"])), rgba(self.theme["bg"]))

    def draw_header(self, draw):
        title = self.spec.get("title", {})
        draw.rounded_rectangle((px(28), px(25), px(38), px(78)), radius=px(4), fill=rgba(self.theme["purple"]))
        draw_fitted(draw, title.get("text", ""), 54, 20, 390, 54, 35, self.theme["text"], align="left", bold=True, min_size=22, wrap=False)
        highlight = title.get("highlight", "")
        if highlight:
            draw.rounded_rectangle((px(470), px(22), px(830), px(80)), radius=px(18), fill=rgba(self.theme["highlight"]), outline=rgba(self.theme["green"]), width=px(1))
            draw_fitted(draw, highlight, 492, 18, 318, 64, 30, self.theme["green"], bold=True, min_size=18, wrap=False)
        draw_fitted(draw, title.get("subtitle", ""), 54, 88, 760, 26, 15, self.theme["muted"], align="left", min_size=10)
        draw.rounded_rectangle(
            (px(20), px(120), px(self.canvas["width"] - 20), px(self.canvas["height"] - 25)),
            radius=px(28),
            outline=rgba(self.theme["frame"]),
            width=px(2),
        )

    def shape_rect(self, draw, node, stroke, fill):
        x, y, w, h = node["x"], node["y"], node["w"], node["h"]
        radius = node.get("radius", 14 if node.get("type") != "note" else 18)
        draw.rounded_rectangle((px(x), px(y), px(x + w), px(y + h)), radius=px(radius), fill=rgba(fill), outline=rgba(stroke), width=px(node.get("width", 2)))

    def shape_diamond(self, draw, node, stroke, fill):
        x, y, w, h = node["x"], node["y"], node["w"], node["h"]
        points = [(x + w / 2, y), (x + w, y + h / 2), (x + w / 2, y + h), (x, y + h / 2)]
        scaled = [(px(a), px(b)) for a, b in points]
        draw.polygon(scaled, fill=rgba(fill), outline=rgba(stroke))
        draw.line(scaled + [scaled[0]], fill=rgba(stroke), width=px(2))

    def draw_node(self, draw, node):
        node_type = node.get("type", "box")
        if node_type == "anchor":
            return  # Geometry on the source image; labels are metadata, never redrawn.
        x, y, w, h = node["x"], node["y"], node["w"], node["h"]
        if node_type == "label":
            draw_fitted(
                draw,
                node.get("label", ""),
                x,
                y,
                w,
                h,
                node.get("size", 18),
                node.get("fill", self.theme["muted"]),
                align=node.get("align", "left"),
                bold=node.get("bold", True),
                min_size=9,
            )
            return
        if node_type == "icon":
            # An icon with a caption under it and no box -- how the AWS reference
            # diagrams draw an instance, a gateway or a user. The bounding box is
            # still what edges anchor to and what the pulse halo surrounds.
            icon = self.load_icon(node)
            size = float(node.get("icon_size", DEFAULT_ICON_NODE_SIZE))
            if icon is not None:
                size = icon.width / RENDER_SCALE
                self.paste_icon(icon, x + (w - size) / 2, y + 4)
            else:
                stroke, fill = self.node_colors(node)
                draw.rounded_rectangle((px(x + (w - size) / 2), px(y + 4), px(x + (w + size) / 2), px(y + 4 + size)),
                                       radius=px(8), fill=rgba(fill), outline=rgba(stroke), width=px(2))
            caption_y = y + size + 10
            label_h = node.get("label_h", 24)
            draw_fitted(draw, node.get("label", ""), x, caption_y, w, label_h, node.get("label_size", 15), self.theme["text"], bold=node.get("label_bold", True), min_size=10)
            draw_fitted(draw, node.get("body", ""), x, caption_y + label_h + 2, w, max(0, h - size - label_h - 16), node.get("body_size", 12), self.theme["muted"], min_size=8)
            return
        stroke, fill = self.node_colors(node)
        if node_type == "diamond":
            self.shape_diamond(draw, node, stroke, fill)
            draw_fitted(draw, node.get("label", ""), x + w * 0.18, y + h * 0.20, w * 0.64, h * 0.28, node.get("label_size", 20), self.theme["text"], bold=node.get("label_bold", True))
            draw_fitted(draw, node.get("body", ""), x + w * 0.22, y + h * 0.48, w * 0.56, h * 0.25, node.get("body_size", 13), self.theme["muted"], min_size=9)
            return
        self.shape_rect(draw, node, stroke, fill)
        label_h = node.get("label_h", min(42, h * 0.34))
        text_x = x + 14
        text_w = w - 28
        icon = self.load_icon(node)
        if icon is not None:
            # Top-left corner, the way AWS group boxes carry the region cloud or
            # the subnet lock; the label moves right to make room, the body does not.
            size = icon.width / RENDER_SCALE
            self.paste_icon(icon, x + 10, y + 8)
            text_x = x + 10 + size + 8
            text_w = w - (text_x - x) - 14
            label_h = max(label_h, size - 4)
        if node.get("layer") == "background":
            # Containers carry their title as a separate label node; only the icon is drawn.
            return
        draw_fitted(draw, node.get("label", ""), text_x, y + 10, text_w, label_h, node.get("label_size", 22), self.theme["text"], align="left", bold=node.get("label_bold", True), min_size=12)
        draw_fitted(draw, node.get("body", ""), x + 14, y + label_h + 12, w - 28, h - label_h - 22, node.get("body_size", 14), self.theme["text"], align="left", min_size=8)

    def node_center(self, node_id):
        node = self.nodes[node_id]
        return (node["x"] + node["w"] / 2, node["y"] + node["h"] / 2)

    def node_boundary_point(self, node, toward):
        cx = node["x"] + node["w"] / 2
        cy = node["y"] + node["h"] / 2
        dx = toward[0] - cx
        dy = toward[1] - cy
        if dx == 0 and dy == 0:
            return (cx, cy)
        half_w = node["w"] / 2
        half_h = node["h"] / 2
        if node.get("type") == "diamond":
            scale = 1 / ((abs(dx) / half_w) + (abs(dy) / half_h))
        else:
            scale_x = half_w / abs(dx) if dx else float("inf")
            scale_y = half_h / abs(dy) if dy else float("inf")
            scale = min(scale_x, scale_y)
        return (cx + dx * scale, cy + dy * scale)

    def edge_points(self, edge):
        if "points" in edge:
            return [tuple(point) for point in edge["points"]]
        start_node = self.nodes[edge["from"]]
        end_node = self.nodes[edge["to"]]
        start_center = self.node_center(edge["from"])
        end_center = self.node_center(edge["to"])
        return [
            self.node_boundary_point(start_node, end_center),
            self.node_boundary_point(end_node, start_center),
        ]

    def edge_route(self, edge):
        # Follow existing elbows exactly on a reference image. New diagrams keep
        # their rounded routes; either mode can override the corner radius.
        return rounded_route(self.edge_points(edge), radius=edge.get("radius", 0 if self.background is not None else 30))

    def draw_arrow(self, draw, route, color, width=2, arrow=True, dashed=False):
        scaled = [(px(x), px(y)) for x, y in route]
        if dashed:
            total = line_length(route)
            cursor = 0
            while cursor < total:
                a = point_on_line(route, cursor)
                b = point_on_line(route, min(total, cursor + 10))
                draw.line([(px(a[0]), px(a[1])), (px(b[0]), px(b[1]))], fill=rgba(color), width=px(width))
                cursor += 20
        else:
            draw.line(scaled, fill=rgba(color), width=px(width), joint="curve")
        if arrow and len(route) >= 2:
            a, b = route[-2], route[-1]
            angle = math.atan2(b[1] - a[1], b[0] - a[0])
            length = 15 + width
            spread = 0.48
            left = (b[0] - length * math.cos(angle - spread), b[1] - length * math.sin(angle - spread))
            right = (b[0] - length * math.cos(angle + spread), b[1] - length * math.sin(angle + spread))
            draw.line([(px(left[0]), px(left[1])), (px(b[0]), px(b[1])), (px(right[0]), px(right[1]))], fill=rgba(color), width=px(width))

    def draw_edges(self, draw):
        for edge in self.spec.get("edges", []):
            if not edge.get("draw_path", self.background is None):
                continue
            points = self.edge_route(edge)
            self.draw_arrow(draw, points, edge.get("stroke", self.theme["dark"]), edge.get("width", 2), edge.get("arrow", True), edge.get("style") == "dashed")
            if edge.get("label"):
                mx, my = point_at_progress(points, 0.5)
                draw_fitted(draw, edge["label"], mx - 54, my - 24, 108, 22, 12, self.theme["muted"], min_size=8)

    def load_icon(self, node):
        """The node's icon as an RGBA image at render scale, or None (problem recorded)."""
        key = node.get("icon")
        if not key:
            return None
        size = float(node.get("icon_size", DEFAULT_ICON_NODE_SIZE if node.get("type") == "icon" else DEFAULT_ICON_SIZE))
        cache_key = (str(key), size)
        if cache_key in self.icon_cache:
            return self.icon_cache[cache_key]
        path, problem = icon_path(key)
        if problem:
            self.icon_problems.append({"node": node.get("id"), "icon": key, "problem": problem})
            self.icon_cache[cache_key] = None
            return None
        with Image.open(path) as raw:
            icon = raw.convert("RGBA")
            icon.thumbnail((px(size), px(size)), Image.Resampling.LANCZOS)
        self.icon_cache[cache_key] = icon
        return icon

    def paste_icon(self, icon, x, y):
        if self._scene is not None and icon is not None:
            self._scene.alpha_composite(icon, dest=(int(px(x)), int(px(y))))
            self.icons_drawn += 1

    def draw_scene(self):
        # Downsample only the transparent annotation layer, never the original.
        image = (Image.new("RGBA", (px(self.canvas["width"]), px(self.canvas["height"])))
                 if self.background is not None else self.blank())
        self._scene = image
        draw = ImageDraw.Draw(image)
        if self.background is None:
            self.draw_header(draw)
        nodes = self.spec.get("nodes", [])
        for node in [item for item in nodes if item.get("layer") == "background"]:
            self.draw_node(draw, node)
        self.draw_edges(draw)
        for node in [item for item in nodes if item.get("layer") != "background"]:
            self.draw_node(draw, node)
        self._scene = None
        layer = image.resize((self.canvas["width"], self.canvas["height"]), Image.Resampling.LANCZOS)
        if self.background is not None:
            return Image.alpha_composite(self.background.convert("RGBA"), layer).convert("RGB")
        return layer.convert("RGB")

    def finish_static(self, image):
        if self.background is not None:
            return image  # No theme texture, vignette or recolouring of the source.
        rng = random.Random(903177)
        final = image.convert("RGBA")
        width, height = final.size
        grain = Image.new("RGBA", final.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(grain)
        alpha_min = int(self.theme["grain_min"])
        alpha_max = int(self.theme["grain_max"])
        for _ in range(max(700, int(width * height * 0.001))):
            tone = rng.randrange(130, 220)
            draw.point((rng.randrange(width), rng.randrange(height)), fill=(tone, tone, tone, rng.randrange(alpha_min, max(alpha_min + 1, alpha_max))))
        final.alpha_composite(grain)
        vignette_alpha = int(self.theme["vignette"])
        if vignette_alpha:
            mask = Image.new("L", final.size, vignette_alpha)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.ellipse((-width * 0.12, -height * 0.35, width * 1.12, height * 1.18), fill=0)
            overlay = Image.new("RGBA", final.size, (0, 0, 0, 0))
            overlay.putalpha(mask.filter(ImageFilter.GaussianBlur(28)))
            final.alpha_composite(overlay)
        return final.convert("RGB")

    def animation_line(self, item):
        if "points" in item:
            return [tuple(point) for point in item["points"]]
        if "edge" in item:
            edges = self.spec.get("edges", [])
            return self.edge_points(edges[item["edge"]]) if 0 <= item["edge"] < len(edges) else []
        if "from" in item and "to" in item:
            return self.edge_points(item)
        return []

    def draw_particle(self, draw, x, y, color, strength):
        # Sized to be seen at a glance on a 1400-px-wide diagram viewed at half
        # size: the motion judge called the previous 18/11/5 px dot "small and
        # low-contrast against the beige background" on every light-theme case.
        for radius, alpha in [(24, 48), (15, 110), (7, 240)]:
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=rgba(color, alpha * strength))
        core = self.theme.get("motion_core", self.theme["text"])
        draw.ellipse((x - 7, y - 7, x + 7, y + 7), outline=rgba(color, 240 * strength), width=2)
        draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=rgba(core, 250 * strength))

    def pulse_node(self, draw, node, progress, strength=1.0):
        if strength <= 0.02:
            return
        stroke, _ = self.node_colors(node)
        x, y, w, h = node["x"], node["y"], node["w"], node["h"]
        alpha = (105 + 95 * (0.5 + 0.5 * math.sin(progress * math.tau * 2))) * strength
        fill_alpha = (18 + 18 * (0.5 + 0.5 * math.sin(progress * math.tau * 2))) * strength
        if node.get("type") == "diamond":
            center = (x + w / 2, y + h / 2)
            base_points = [(center[0], y), (x + w, center[1]), (center[0], y + h), (x, center[1])]
            for growth, line_width, scale_alpha in [(0, 4, 1.0), (8, 4, 0.78), (17, 3, 0.48), (28, 2, 0.25)]:
                points = []
                for px0, py0 in base_points:
                    dx = px0 - center[0]
                    dy = py0 - center[1]
                    length = math.hypot(dx, dy) or 1
                    points.append((px0 + growth * dx / length, py0 + growth * dy / length))
                if growth == 0:
                    draw.polygon(points, fill=rgba(stroke, fill_alpha))
                draw.line(points + [points[0]], fill=rgba(stroke, alpha * scale_alpha), width=line_width)
            return
        if node.get("type") != "anchor":
            draw.rounded_rectangle((x, y, x + w, y + h), radius=18, fill=rgba(stroke, fill_alpha))
        for growth, line_width, scale_alpha in [(0, 4, 1.0), (7, 4, 0.78), (16, 3, 0.46), (28, 2, 0.22)]:
            draw.rounded_rectangle(
                (x - growth, y - growth, x + w + growth, y + h + growth),
                radius=18 + growth,
                outline=rgba(stroke, max(18 * strength, alpha * scale_alpha)),
                width=line_width,
            )

    def motion_color(self):
        animation = self.spec.get("animation", {})
        return animation.get("motion_color") or animation.get("motion") or self.theme["motion"]

    def draw_trail(self, draw, route, color, until=None):
        """The part of a route already travelled, as a soft line under the dots."""
        if until is not None:
            total = line_length(route)
            distance = max(0.0, min(total, until * total))
            trimmed = []
            walked = 0.0
            for a, b in zip(route, route[1:]):
                seg = segment_length(a, b)
                if walked + seg >= distance:
                    ratio = (distance - walked) / seg if seg else 0
                    trimmed.append(a)
                    trimmed.append((a[0] + (b[0] - a[0]) * ratio, a[1] + (b[1] - a[1]) * ratio))
                    break
                trimmed.append(a)
                walked += seg
            else:
                trimmed = route
            route = trimmed
        if len(route) >= 2:
            draw.line(route, fill=rgba(color, 90), width=6, joint="curve")

    def frame_steps(self, base, index):
        """One frame of `steps` mode: dots only on the current step's edges."""
        frame = base.convert("RGBA")
        overlay = Image.new("RGBA", frame.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        schedule = self.schedule
        edges = self.spec.get("edges", [])
        motion = self.motion_color()
        current = None
        previous = None
        for step in schedule["steps"]:
            if step["start"] <= index < step["end"]:
                current = step
                break
            previous = step
        done = [step for step in schedule["steps"] if step["end"] <= index]
        if schedule["trail"]:
            for step in done:
                for idx in step["edges"]:
                    self.draw_trail(draw, self.edge_route(edges[idx]), motion)
        if current is None:
            # Hold: the whole story is on screen, the last stage keeps glowing.
            local = (index - schedule["hold"]["start"]) / max(1, schedule["hold"]["end"] - schedule["hold"]["start"])
            if previous:
                for node_id in previous["pulse"]:
                    if node_id in self.nodes:
                        self.pulse_node(draw, self.nodes[node_id], local * 0.5, 0.85)
            frame.alpha_composite(overlay)
            return frame.convert("RGB")
        local = (index - current["start"] + 0.5) / (current["end"] - current["start"])
        travel = smoothstep(local)
        for idx in current["edges"]:
            points = self.edge_route(edges[idx])
            if len(points) < 2:
                continue
            if schedule["trail"]:
                self.draw_trail(draw, points, motion, until=travel)
            for tail, strength in [(-0.10, 0.36), (-0.05, 0.66), (0, 1.0)]:
                at = max(0.0, travel + tail)
                x, y = point_on_line(points, at * line_length(points))
                self.draw_particle(draw, x, y, motion, strength)
        # The stage just left fades out while the dot is still near it; the stage
        # being reached lights up as the dot arrives.
        if previous:
            for node_id in previous["pulse"]:
                if node_id in self.nodes:
                    self.pulse_node(draw, self.nodes[node_id], local, 1.0 - smoothstep(local / 0.5))
        for node_id in current["pulse"]:
            if node_id in self.nodes:
                self.pulse_node(draw, self.nodes[node_id], local, smoothstep((local - 0.55) / 0.45))
        frame.alpha_composite(overlay)
        return frame.convert("RGB")

    def frame(self, base, index, total):
        if self.schedule:
            return self.frame_steps(base, index)
        frame = base.convert("RGBA")
        overlay = Image.new("RGBA", frame.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        animation = self.spec.get("animation", {})
        motion = self.motion_color()
        paths = animation.get("paths")
        if paths is None:
            paths = [{"edge": idx} for idx, _ in enumerate(self.spec.get("edges", []))]
        progress = index / max(1, total)
        for offset, item in enumerate(paths):
            points = self.animation_line(item)
            if len(points) < 2:
                continue
            if "edge" in item and "points" not in item:
                points = self.edge_route(self.spec["edges"][item["edge"]])
            else:
                points = rounded_route(points, radius=item.get("radius", 0 if self.background is not None else 30))
            for tail, strength in [(-0.08, 0.36), (-0.04, 0.66), (0, 1.0)]:
                x, y = point_at_progress(points, progress + offset * 0.13 + tail)
                self.draw_particle(draw, x, y, motion, strength)
        pulse_ids = [node_id for node_id in animation.get("pulses", []) if node_id in self.nodes]
        if pulse_ids:
            # One full walk of the pulse list per loop, so a longer loop is a slower
            # walk. The floor keeps a short loop with many pulses from flickering.
            dwell = max(MIN_PULSE_FRAMES, total // len(pulse_ids))
            active = pulse_ids[(index // dwell) % len(pulse_ids)]
            self.pulse_node(draw, self.nodes[active], progress)
        frame.alpha_composite(overlay)
        return frame.convert("RGB")

    def write(self, outdir, basename, fmt=DEFAULT_FORMAT):
        outdir.mkdir(parents=True, exist_ok=True)
        options = FORMATS[fmt]
        static = self.finish_static(self.draw_scene())
        png_path = outdir / f"{basename}.png"
        anim_path = outdir / f"{basename}{options['suffix']}"
        if self.background_path in (png_path.resolve(), anim_path.resolve()):
            raise ValueError("output would overwrite background.image; choose another basename or outdir")
        static.save(png_path, "PNG")
        count = int(self.canvas["frames"])
        fps = int(self.canvas["fps"])
        frames = (self.frame(static, idx, count) for idx in range(count))
        video = None
        if options.get("video"):
            video = encode_video(frames, anim_path, fps, options.get("crf", VIDEO_CRF),
                                 bg=self.theme["bg"])
        else:
            frames = list(frames)
            frames[0].save(
                anim_path,
                options["pillow"],
                save_all=True,
                append_images=frames[1:],
                duration=int(1000 / fps),
                loop=0,
                **options["save"],
            )
        result = {"png": str(png_path), "animation": str(anim_path), "format": fmt,
                  "source_mode": "overlay" if self.background is not None else "generated",
                  "mode": "steps" if self.schedule else "continuous",
                  "frames": count, "fps": fps, "seconds": round(count / fps, 2),
                  "icons": {"drawn": self.icons_drawn, "problems": self.icon_problems}}
        if self.background is not None:
            result["background"] = {"image": self.spec["background"]["image"],
                                    "sha256": self.background_sha256,
                                    "width": self.background.width, "height": self.background.height}
        if video:
            # MP4 loops only when the player is told to; the file carries no loop
            # flag the way WebP and GIF do. Said here so the report can say it.
            result["video"] = {**video, "loops_by_itself": False}
        if self.schedule:
            result["schedule"] = {
                "step_seconds_default": float(self.spec.get("animation", {}).get("step_seconds", DEFAULT_STEP_SECONDS)),
                "hold": self.schedule["hold"],
                "steps": [{k: step[k] for k in ("step", "label", "edges", "pulse", "seconds", "start", "end")}
                          for step in self.schedule["steps"]],
            }
        return result


def motion_report(anim_path, schedule=None):
    with Frames(anim_path) as anim:
        # Clamped to the frames the file actually has, and deduplicated. A spec
        # that declares no motion at all writes 48 identical frames, both the WebP
        # and the GIF encoder collapse them to one, and the unclamped sample points
        # then seek past the end -- so --verify raised EOFError on exactly the
        # defect it exists to detect, taking --check's verdict down with it. With
        # one frame there are no pairs to compare, `diffs` is empty, and
        # `animation_has_motion` reports False, which is the answer.
        wanted = (0, anim.n_frames // 3, anim.n_frames * 2 // 3, anim.n_frames - 1)
        if schedule:
            # In steps mode the informative frames are the step midpoints: each
            # should show a dot somewhere the previous one did not.
            wanted = tuple((s["start"] + s["end"]) // 2 for s in schedule["steps"]) + (anim.n_frames - 1,)
        picks = sorted({min(max(0, index), anim.n_frames - 1) for index in wanted})
        frames = [anim.rgb(index) for index in picks]
        frame_count = anim.n_frames
    diffs = []
    for first, second, a, b in zip(frames, frames[1:], picks, picks[1:]):
        delta = ImageChops.difference(first, second)
        box = delta.getbbox()
        changed = 0
        if box:
            crop = delta.crop(box)
            pixels = crop.get_flattened_data() if hasattr(crop, "get_flattened_data") else crop.getdata()
            for pixel in pixels:
                if pixel != (0, 0, 0):
                    changed += 1
        diffs.append({"from": a, "to": b, "changed_pixels": changed})
    return {"frames": frame_count, "diffs": diffs}


# Telling a dot from lossy-WebP noise. Measured on the dark reference render: a dot
# at a route midpoint changes 50-70 pixels of a 16x16 window by more than 64
# levels; a window on an untouched edge that happens to sit 5 px from a bright
# container border shows one pixel at 52 and nothing above 64. So "touched" means
# several pixels well above the noise, not any pixel a little above it.
CHANGE_THRESHOLD = 64   # per-channel
MIN_CHANGED_PIXELS = 6
SAMPLE_RADIUS = 8


#: Where along a route to look for the step's dot. The dot's exact position at a
#: given frame is the renderer's business -- it eases, and the encoder is free to
#: drop a frame -- so pinning the check to one point couples it to both. Measured on
#: the reference render: at the step's midpoint frame the dot sits at t=0.43, and
#: sampling t=0.50 found the trailing edge of its glow, 19 pixels at 70 against a
#: threshold of 64. That margin is 6 levels wide. It survived WebP and vanished
#: under H.264, which is a fragile check reporting an encoder change as a missing
#: dot -- at t=0.43 the same dot is 171 pixels at 233 in both. So the band is what
#: is checked, and a dot anywhere in it counts.
DOT_BAND = (0.30, 0.70)
DOT_BAND_SAMPLES = 9
#: How close to an already-travelled edge a sample may sit before it is unanswerable.
#: A trail is a thin line, not a dot with a halo, so this is much tighter than the
#: 30 px used for an active route. On the spec that exposed the need, the two
#: readings either side of it were 9 px (strength 1) and 14 px (strength 0).
TRAIL_CLEARANCE = 12


def _band_points(route, lo=DOT_BAND[0], hi=DOT_BAND[1], samples=DOT_BAND_SAMPLES):
    step = (hi - lo) / max(1, samples - 1)
    return [point_at_progress(route, lo + step * i) for i in range(samples)]


def _change_strength(diff, x, y, radius=SAMPLE_RADIUS):
    """How many pixels in a window around (x, y) differ by more than noise."""
    w, h = diff.size
    box = (int(max(0, x - radius)), int(max(0, y - radius)), int(min(w, x + radius)), int(min(h, y + radius)))
    if box[2] <= box[0] or box[3] <= box[1]:
        return 0
    crop = diff.crop(box)
    pixels = crop.get_flattened_data() if hasattr(crop, "get_flattened_data") else crop.getdata()
    return sum(1 for pixel in pixels if max(pixel) > CHANGE_THRESHOLD)


def _changed_near(diff, x, y, radius=SAMPLE_RADIUS):
    """Whether a window around (x, y) differs from the static by more than noise."""
    return _change_strength(diff, x, y, radius) >= MIN_CHANGED_PIXELS


def steps_report(anim_path, png_path, renderer):
    """Prove, from the written file, that dots appear step by step.

    At each step's midpoint frame the dot on every edge of that step sits at the
    route's midpoint, so a window there must differ from the static PNG; and no
    edge belonging to a *later* step has been touched yet, so its midpoint must
    not. Compared against the PNG rather than a neighbouring frame because "the
    frames differ" is already `animation_has_motion`; this asks *where* they
    differ. Future edges whose midpoint lies inside a pulsing node's halo or on
    top of an active route are skipped rather than guessed at.
    """
    schedule = renderer.schedule
    edges = renderer.spec.get("edges", [])
    static = Image.open(png_path).convert("RGB")
    report = []
    ok = True
    with Frames(anim_path, size=static.size) as anim:
        for position, step in enumerate(schedule["steps"]):
            mid = min(anim.n_frames - 1, (step["start"] + step["end"]) // 2)
            diff = ImageChops.difference(anim.rgb(mid), static)
            active_routes = [renderer.edge_route(edges[idx]) for idx in step["edges"]]
            active_routes = [r for r in active_routes if len(r) >= 2]
            hits = [idx for idx, route in zip(step["edges"], active_routes)
                    if any(_changed_near(diff, x, y) for x, y in _band_points(route))]
            halo_nodes = [renderer.nodes[n] for s in schedule["steps"][max(0, position - 1):position + 1]
                          for n in s["pulse"] if n in renderer.nodes]
            future = [idx for later in schedule["steps"][position + 1:] for idx in later["edges"]
                      if idx not in step["edges"] and not any(idx in s["edges"] for s in schedule["steps"][:position + 1])]
            clean, dirty, skipped = [], [], []
            active_track = [point_at_progress(route_a, k / 20)
                            for route_a in active_routes for k in range(21)]
            # Edges an earlier step already travelled keep a faint trail, and a
            # future edge's path can run straight over one. Measured on a scored
            # run's spec: the return edge passes 0.8 px from the webhook edge, so a
            # sample there reads the webhook's trail and reports the return edge as
            # having moved. Points on a trail cannot answer the question, the same
            # way points under a pulsing node's halo cannot.
            trail_track = [point_at_progress(renderer.edge_route(edges[idx]), k / 40)
                           for earlier in schedule["steps"][:position]
                           for idx in earlier["edges"]
                           if len(renderer.edge_route(edges[idx])) >= 2
                           for k in range(41)]
            for idx in future:
                route = renderer.edge_route(edges[idx])
                if len(route) < 2:
                    continue
                # Same band as the active edges, for the same reason: a dot on an
                # edge that should not have moved yet is a violation wherever on
                # that edge it sits, and one sample point can miss it.
                verdicts = []
                for x, y in _band_points(route):
                    near_halo = any(n["x"] - 40 <= x <= n["x"] + n["w"] + 40 and n["y"] - 40 <= y <= n["y"] + n["h"] + 40
                                    for n in halo_nodes)
                    near_active = any(math.hypot(x - ax, y - ay) < 30 for ax, ay in active_track)
                    near_trail = any(math.hypot(x - tx, y - ty) < TRAIL_CLEARANCE
                                     for tx, ty in trail_track)
                    if near_halo or near_active or near_trail:
                        verdicts.append("skip")
                    else:
                        verdicts.append("dirty" if _changed_near(diff, x, y, radius=6) else "clean")
                if "dirty" in verdicts:
                    dirty.append(idx)
                elif "clean" in verdicts:
                    clean.append(idx)
                else:
                    # Every sample sat under a pulsing node's halo or on top of an
                    # active route, so this edge is unanswerable at this frame
                    # rather than proven either way. Counted as neither.
                    skipped.append(idx)
            step_ok = len(hits) == len(active_routes) and not dirty
            ok = ok and step_ok
            report.append({"step": step["step"], "frame": mid, "ok": step_ok,
                           "active_edges_with_dot": f"{len(hits)}/{len(active_routes)}",
                           "future_edges_untouched": f"{len(clean)}/{len(clean) + len(dirty)}",
                           **({"future_edges_touched": dirty} if dirty else {}),
                           **({"skipped": skipped} if skipped else {})})
    return {"ok": ok, "steps": report}


def frame_count_check(actual, expected, schedule):
    """The file has the frames the spec declares.

    Both encoders merge runs of identical frames into one longer frame. In steps
    mode the closing hold is a run of near-identical frames by design, so a count
    short by up to the hold's length is the encoder doing its job, not a missing
    part of the animation. Anything shorter than that, or longer, is a defect.
    """
    check = {"name": "animation_frames", "expected": expected, "actual": actual}
    if schedule:
        hold = schedule["hold"]["end"] - schedule["hold"]["start"]
        check["ok"] = expected - hold <= actual <= expected
        if check["ok"] and actual < expected:
            check["note"] = f"{expected - actual} identical hold frame(s) merged by the encoder"
    else:
        check["ok"] = actual == expected
    return check


def validate_outputs(result, spec, renderer=None):
    canvas, schedule = resolve_canvas(spec)
    png_path = Path(result["png"])
    anim_path = Path(result["animation"])
    checks = [{"name": "png_exists", "ok": png_path.is_file()}, {"name": "animation_exists", "ok": anim_path.is_file()}]
    if renderer is not None and renderer.background is not None:
        checks.append({"name": "background_file_unchanged",
                       "ok": hashlib.sha256(renderer.background_path.read_bytes()).hexdigest() == renderer.background_sha256})
        only_anchors = all(n.get("type") == "anchor" for n in spec.get("nodes", []))
        if only_anchors and not any(e.get("draw_path", False) for e in spec.get("edges", [])) and png_path.is_file():
            with Image.open(png_path) as png:
                checks.append({"name": "background_pixels_preserved",
                               "ok": png.size == renderer.background.size and ImageChops.difference(png.convert("RGB"), renderer.background).getbbox() is None})
    if png_path.is_file():
        with Image.open(png_path) as png:
            checks.extend(
                [
                    {"name": "png_width", "ok": png.width == canvas["width"], "expected": canvas["width"], "actual": png.width},
                    {"name": "png_height", "ok": png.height == canvas["height"], "expected": canvas["height"], "actual": png.height},
                ]
            )
    if anim_path.is_file():
        if is_video(anim_path):
            probe = probe_video(anim_path)
            want_w, want_h = even(canvas["width"]), even(canvas["height"])
            checks.extend(
                [
                    {"name": "animation_width", "ok": probe["width"] == want_w, "expected": want_w, "actual": probe["width"]},
                    {"name": "animation_height", "ok": probe["height"] == want_h, "expected": want_h, "actual": probe["height"]},
                    frame_count_check(probe["n_frames"], canvas["frames"], schedule),
                    # The four rows below are the difference between a file that
                    # plays on the slide and one that plays only here. Each is read
                    # back off the written file, because "the flag was on the
                    # command line" and "the file has it" are different claims.
                    {"name": "video_codec", "ok": probe["codec"] == "h264", "expected": "h264", "actual": probe["codec"]},
                    {"name": "video_pix_fmt", "ok": probe["pix_fmt"] == VIDEO_PIX_FMT,
                     "expected": VIDEO_PIX_FMT, "actual": probe["pix_fmt"],
                     "why": "PowerPoint and hardware decoders reject anything else"},
                    {"name": "video_has_audio_track", "ok": probe["has_audio"],
                     "why": "some PowerPoint builds refuse or blank a video-only MP4"},
                    {"name": "video_faststart", "ok": probe["faststart"],
                     "why": "moov before mdat lets a browser play before the file finishes arriving"},
                ]
            )
        else:
            with Image.open(anim_path) as anim:
                checks.extend(
                    [
                        {"name": "animation_width", "ok": anim.width == canvas["width"], "expected": canvas["width"], "actual": anim.width},
                        {"name": "animation_height", "ok": anim.height == canvas["height"], "expected": canvas["height"], "actual": anim.height},
                        frame_count_check(anim.n_frames, canvas["frames"], schedule),
                    ]
                )
        motion = motion_report(anim_path, schedule)
        checks.append({"name": "animation_has_motion", "ok": any(item["changed_pixels"] > 0 for item in motion["diffs"]), "diffs": motion["diffs"]})
        if schedule and png_path.is_file():
            confined = steps_report(anim_path, png_path, renderer or FlowRenderer(spec))
            checks.append({"name": "steps_confined", "ok": confined["ok"], "steps": confined["steps"]})
    if renderer is not None and any(n.get("icon") for n in spec.get("nodes", [])):
        # An icon the spec asked for and the render could not place is a gap in
        # the picture with nothing to say so; the report says so instead.
        checks.append({"name": "icons_resolved", "ok": not renderer.icon_problems,
                       "drawn": renderer.icons_drawn, "problems": renderer.icon_problems})
    return {"ok": all(item["ok"] for item in checks), "checks": checks}


def summary_line(result):
    """One line saying whether the render passed, printed before the JSON.

    The JSON report is the machine-readable answer, but it is also long: with
    --verify the sampled frame diffs alone push the `checks` block past the point
    where a caller that keeps only the head of stdout can still see it. So the
    verdict is printed first, on one line, ahead of everything it summarises.
    """
    png = Path(result["png"]).name
    anim = Path(result["animation"]).name
    checks = result.get("checks")
    verification = result.get("verification") or {}
    frames = verification.get("frames")
    if frames is None:
        for item in (checks or {}).get("checks", []):
            if item["name"] == "animation_frames":
                frames = item.get("actual")
    size = ""
    for item in (checks or {}).get("checks", []):
        if item["name"] == "png_width":
            size = f" {item.get('actual')}x"
        elif item["name"] == "png_height":
            size += str(item.get("actual"))
    moving = [d for d in verification.get("diffs", []) if d["changed_pixels"] > 0]
    motion = f" motion={len(moving)}/{len(verification['diffs'])} sampled pairs" if verification.get("diffs") else ""
    if checks is None:
        return f"rendered {png} + {anim}{size}{motion} (no --check requested)"
    pacing = f" {result['seconds']}s" if result.get("seconds") is not None else ""
    if result.get("schedule"):
        pacing += f" ({len(result['schedule']['steps'])} steps)"
    if (result.get("icons") or {}).get("drawn"):
        pacing += f" icons={result['icons']['drawn']}"
    if result.get("video"):
        # Size belongs on the verdict line for a video, because the format exists to
        # be sent somewhere -- a slide deck, a page -- and SKILL.md asks the report
        # to state it. `du` on a file the agent has to find again is a worse answer.
        try:
            pacing += f" {Path(result['animation']).stat().st_size / 1024:.0f}KB"
        except OSError:
            pass
        pacing += " (no self-loop: set the player to repeat)"
    if checks["ok"]:
        return f"✓ PASS {png} + {anim}{size} frames={frames}{pacing}{motion}"
    broken = ", ".join(item["name"] for item in checks["checks"] if not item["ok"])
    return f"✗ FAIL {png} + {anim} failed: {broken}"


def main():
    parser = argparse.ArgumentParser(description="Render a diagram or animate over a reference image as PNG + WebP/GIF/MP4.")
    parser.add_argument("--spec", required=True, help="Path to a JSON diagram spec.")
    parser.add_argument("--outdir", required=True, help="Directory for the generated PNG and animation.")
    parser.add_argument("--basename", default="flow-diagram", help="Output filename prefix.")
    parser.add_argument("--format", choices=sorted(FORMATS), default=DEFAULT_FORMAT,
                        help="Animation container. Default: webp. mp4 for a file the viewer "
                             "can pause, scrub and put on a PowerPoint slide (needs ffmpeg).")
    parser.add_argument("--verify", action="store_true", help="Print sampled animation frame differences.")
    parser.add_argument("--check", action="store_true", help="Validate output files and fail on broken output.")
    args = parser.parse_args()

    try:
        spec_path = Path(args.spec).resolve()
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        renderer = FlowRenderer(spec, spec_dir=spec_path.parent)
        result = renderer.write(Path(args.outdir), args.basename, args.format)
        if args.verify:
            result["verification"] = motion_report(result["animation"], renderer.schedule)
        if args.check:
            result["checks"] = validate_outputs(result, spec, renderer)
    except VideoToolMissing as e:
        # A missing encoder is a missing tool, not a broken spec. Say which, on the
        # same verdict line the caller already parses, so a run does not read this
        # as "the diagram is wrong" and start editing the spec.
        print(f"✗ FAIL video encoder unavailable: {e}")
        sys.exit(2)
    except (OSError, ValueError) as e:
        print(f"✗ FAIL {e}")
        sys.exit(1)
    print(summary_line(result))
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if args.check and not result["checks"]["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
