"""Get a picture of the run in front of a vision judge.

**The delivered image is preferred.** It is what the user actually received, and a
re-render is a reconstruction standing in for it. `usable_image()` decides whether
the collected file is a real image; `sandbox.collect_artifacts` now copies bytes it
cannot inline verbatim, so for a local run it usually is.

Re-rendering from the spec is the fallback, and it is still needed:

  * on `--target runtime` there is no shared disk, so no bytes come back
  * before that copy existed, a PNG over `MAX_ARTIFACT_BYTES` was recorded as its
    first 256 KB decoded to replacement characters and written back as UTF-8 --
    not an image, and worse than nothing because it looked like a file on disk --
    while one under the cap produced no file at all. Old recordings still say that.
  * a run may deliver no image while still delivering the spec

The fallback has a property the preferred path does not: it proves the spec is
renderable, independent of what the agent reported. So when both exist they are
compared — an agent whose delivered picture is not what its own spec renders to is
a finding, and without the comparison the judge would score a picture nobody was
sent.

Uses a subprocess rather than importing the renderer, because the interpreter
scoring the run and the interpreter that can `import PIL` are not reliably the
same one — phase 2 runs under `uv run --with strands-agents-evals`, which knows
nothing about a skill's own dependencies.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys

# Imported at module level on purpose. The plugin loader puts this directory on
# sys.path only while the plugin module is being imported and restores it after,
# so an `import specgeom` inside a function runs at call time, fails, and -- if
# caught -- silently degrades to the continuous-mode frame picks. That is exactly
# how the first steps-mode scoring run showed the judge the webhook frame instead
# of the fan-out frame.
import specgeom

HERE = os.path.dirname(os.path.abspath(__file__))
RENDERER = os.path.join(HERE, "..", "scripts", "render_animated_webp.py")
#: The animation container the renderer emits by default; keep in step with its FORMATS.
ANIM_EXT = ".webp"
#: `--format` value per extension. The re-render has to match the container the run
#: delivered, or the delivered-vs-rendered byte comparison compares an MP4 against a
#: WebP and reports a difference on every such case -- a finding that is always true
#: and never informative, which is how readers learn to skip findings. Both encoders
#: are byte-reproducible for a fixed spec (verified), so with the formats matched an
#: inequality still means what it claims to mean.
FORMAT_BY_EXT = {".webp": "webp", ".gif": "gif", ".mp4": "mp4"}


class RenderUnavailable(RuntimeError):
    """No interpreter here can run the renderer.

    A missing Pillow is a missing tool, not a badly laid out diagram. Saying so
    keeps a machine without the skill's dependencies from looking like a skill
    that cannot draw.
    """


def _interpreters(hint: str | None = None) -> list[str]:
    """Interpreters to try, best first.

    `hint` is the `python3` the *sandbox* resolved during phase 1 -- the one that
    actually ran this skill's scripts -- recorded in the results as
    `toolchain.python3`. It goes first and it is the only entry that can be right by
    construction: phase 2 runs under `uv run`, which puts an ephemeral venv at
    `sys.executable` and at the front of PATH, so `which("python3")` here resolves to
    a venv that never ran the skill and need not carry its dependencies. Measured on
    a real run: every name-resolved candidate had Pillow (a transitive dependency of
    something, not a declared one) and none had the icon set, while the interpreter
    the sandbox used had both and was unreachable.
    """
    out = []
    for exe in (hint, sys.executable, shutil.which("python3"), shutil.which("python")):
        if exe and exe not in out and os.path.exists(exe):
            out.append(exe)
    return out


def _can_import(exe: str, names: str) -> bool:
    return subprocess.run([exe, "-c", f"import {names}"],  # nosemgrep: dangerous-subprocess-use-audit
                          capture_output=True, text=True).returncode == 0


def find_interpreter(hint: str | None = None) -> tuple[str, bool]:
    """`(interpreter, has_icon_set)`, reproducing the run where that is possible.

    **The hint wins over a more capable interpreter, on purpose.** The re-render's
    first job is to be the picture the run produced, so that a byte difference means
    "the agent delivered something its spec does not render to". Rendering with a
    *better* interpreter than the run had breaks that in the opposite direction: the
    icons appear, the delivered picture has none, and the difference gets reported as
    the agent's fault again. Faithfulness first, capability second.

    `has_icon_set` is returned rather than kept private because a re-render without
    the icons is not the same picture, and every caller that concludes something from
    a difference needs to know that first.
    """
    candidates = [exe for exe in _interpreters(hint) if _can_import(exe, "PIL")]
    if hint and candidates and candidates[0] == hint:
        return hint, _can_import(hint, "PIL, diagrams")
    # No usable hint (an old recording, or the interpreter is gone). Now capability
    # is the best available proxy, since there is nothing to be faithful to.
    for exe in candidates:
        if _can_import(exe, "PIL, diagrams"):
            return exe, True
    for exe in candidates:
        return exe, False
    raise RenderUnavailable(
        "no interpreter on this machine can `import PIL`; install the skill's "
        "requirements.txt to enable the visual layer")


#: Set by the last `render()` call: whether the interpreter it used could draw the
#: spec's icons. A module-level fact rather than a return value because `render`'s
#: `(png, animation)` shape is what the plugin and its tests are written against.
last_render_had_icon_set: bool = True


def render(spec_path: str, outdir: str, basename: str = "render",
           timeout: int = 300, ext: str = ANIM_EXT,
           interpreter_hint: str | None = None) -> tuple[str, str]:
    """Render `spec_path`, returning `(png_path, animation_path)`.

    `ext` picks the container, so the re-render can match what the run delivered.
    `interpreter_hint` is the sandbox's `python3` from the recording; see
    `_interpreters`.
    """
    global last_render_had_icon_set
    exe, has_icons = find_interpreter(interpreter_hint)
    last_render_had_icon_set = has_icons
    ext = ext if ext in FORMAT_BY_EXT else ANIM_EXT
    os.makedirs(outdir, exist_ok=True)
    cmd = [exe, os.path.abspath(RENDERER), "--spec", os.path.abspath(spec_path),
           "--outdir", os.path.abspath(outdir), "--basename", basename,
           "--format", FORMAT_BY_EXT[ext], "--verify", "--check"]
    # List form, no shell: exe and paths are argv elements, not shell-parsed.
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # nosemgrep: dangerous-subprocess-use-audit
    png = os.path.join(outdir, f"{basename}.png")
    anim = os.path.join(outdir, f"{basename}{ext}")
    if not (os.path.exists(png) and os.path.exists(anim)):
        # Note the asymmetry: a non-zero exit with both files present is *not* an
        # error here. `--check` fails on a frame-count or motion defect, and that
        # is a finding about the spec the judge should see the picture of, not a
        # reason to withhold the picture.
        raise RenderUnavailable(
            f"the renderer produced no output (exit={proc.returncode}): "
            f"{(proc.stderr or proc.stdout or '').strip()[:300]}")
    return png, anim


def render_if_possible(spec_path: str, outdir: str, basename: str = "render",
                       ext: str = ANIM_EXT, interpreter_hint: str | None = None
                       ) -> tuple[tuple[str, str] | None, str | None]:
    """`((png, animation), None)` on success, `(None, reason)` when it could not run."""
    try:
        return render(spec_path, outdir, basename, ext=ext,
                      interpreter_hint=interpreter_hint), None
    except (RenderUnavailable, subprocess.TimeoutExpired, OSError) as e:
        return None, f"{type(e).__name__}: {e}"


def sandbox_python(recorded: dict) -> str | None:
    """The `python3` the sandbox used, from a phase-1 recording.

    Absent from recordings made before phase 1 started writing it, so this returns
    None rather than raising -- an old recording re-scores exactly as it used to.
    """
    return ((recorded or {}).get("toolchain") or {}).get("python3")


#: Enough to reject the two failure modes that actually occur: a file that is not an
#: image at all, and a truncated one whose magic bytes survived (`RIFF` and `GIF89a`
#: are ASCII, so an animation mangled into replacement characters still passes a
#: signature check and `file(1)` still vouches for it). WebP is `RIFF<size>WEBP`, so
#: bytes 4-8 are the length and only the two four-byte tags are checked.
_MAGIC = {".png": b"\x89PNG\r\n\x1a\n", ".gif": (b"GIF87a", b"GIF89a"), ".webp": b"RIFF"}
_WEBP_TAG = b"WEBP"
#: MP4 is an ISO base media file: a `ftyp` box at offset 4. Pillow cannot open one at
#: all, so a delivered MP4 would fail `usable_image` and the visual layer would score
#: a re-render while reporting nothing about it -- the same silent degradation this
#: module's docstring warns about, arriving through a new door. ffmpeg answers instead.
_MP4_TAG = b"ftyp"


def _ffmpeg(name: str = "ffmpeg") -> str | None:
    return shutil.which(name)


def usable_video(path: str | None) -> bool:
    """Whether `path` is an MP4 with a decodable video stream.

    ffprobe rather than Pillow, and a real decode of one frame rather than a header
    read, because the failure mode that occurs here is a truncated copy whose `ftyp`
    box survived.
    """
    probe = _ffmpeg("ffprobe")
    if not probe:
        return False
    proc = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
        [probe, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_name,width,height", "-of", "csv=p=0", path],
        capture_output=True, text=True)
    if proc.returncode != 0 or not proc.stdout.strip():
        return False
    ffmpeg = _ffmpeg()
    if not ffmpeg:
        return False
    decode = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
        [ffmpeg, "-v", "error", "-i", path, "-frames:v", "1", "-f", "null", "-"],
        capture_output=True, text=True)
    return decode.returncode == 0


def usable_image(path: str | None) -> bool:
    """Whether `path` is an image or video a judge can be shown frames of.

    Signature first because it is free, then a decode, because the signature is what
    a mangled animation keeps. Pillow missing here means unknown rather than bad, and the
    caller treats that as "cannot use it" so the run falls back to a render it can
    verify instead of passing a file forward on faith.
    """
    if not path or not os.path.isfile(path) or os.path.getsize(path) == 0:
        return False
    ext = os.path.splitext(path)[1].lower()
    if ext == ".mp4":
        with open(path, "rb") as fh:
            head = fh.read(12)
        return head[4:8] == _MP4_TAG and usable_video(path)
    want = _MAGIC.get(ext)
    if want:
        with open(path, "rb") as fh:
            head = fh.read(12)
        sigs = want if isinstance(want, tuple) else (want,)
        if not any(head.startswith(s) for s in sigs):
            return False
        if ext == ".webp" and head[8:12] != _WEBP_TAG:
            return False
    try:
        from PIL import Image
    except ImportError:
        return False
    try:
        with Image.open(path) as im:
            im.load()          # not verify(): that reads the header and stops
    except Exception:           # noqa: BLE001 -- any decode failure is the answer
        return False
    return True


def same_bytes(a: str, b: str) -> bool:
    """Whether two files are byte-identical. Cheap: sizes differ far more often."""
    try:
        if os.path.getsize(a) != os.path.getsize(b):
            return False
        with open(a, "rb") as fa, open(b, "rb") as fb:
            return hashlib.sha256(fa.read()).digest() == hashlib.sha256(fb.read()).digest()
    except OSError:
        return False


def frame_picks(spec_path: str | None, n_frames: int) -> tuple[list[int], str]:
    """Which two frames to show the judge, and a sentence saying what they are.

    Continuous mode: frame 0 and one a third of the way in, as before. Steps mode:
    the midpoint of the first step and the midpoint of the busiest step (the one
    with the most edges travelling together, else the last), because those two
    frames are where "one step at a time" and "parallel edges move together" are
    visible -- frame 0 of a steps render shows a dot barely off its start, and a
    frame a third of the way in lands wherever the arithmetic says.
    """
    default = ([0, min(n_frames - 1, max(1, n_frames // 3))],
               "image 1 is frame 0, image 2 is a frame about a third of the way through the animation")
    if not spec_path:
        return default
    try:
        spec = specgeom.load(spec_path)
        schedule = specgeom.steps_schedule(spec)
    except (OSError, ValueError):
        # A broken spec is reported by the layout layer; here it just means default frames.
        return default
    if not schedule or len(schedule["steps"]) < 2:
        return default
    steps = schedule["steps"]
    first = steps[0]
    busiest = max(steps[1:], key=lambda s: (len(s["edges"]), s["step"]))
    picks = [min(n_frames - 1, first["mid"]), min(n_frames - 1, busiest["mid"])]
    earlier = ("earlier edges show a faint trail" if (spec.get("animation") or {}).get("trail", True)
               else "earlier edges have no retained trail")
    note = (f"the animation is in steps mode ({len(steps)} steps, about {schedule['seconds']} s). "
            f"image 1 is the midpoint of step 1, when only {', '.join(first['routes']) or 'its edges'} "
            f"should carry a dot; image 2 is the midpoint of step {busiest['step'] + 1}, when "
            f"{', '.join(busiest['routes']) or 'its edges'} should carry dots at the same time, "
            f"{earlier}, and later edges show nothing yet")
    return picks, note


def frame_pair(anim_path: str, outdir: str,
               spec_path: str | None = None) -> tuple[str, str, str] | None:
    """Two frames of the animation as PNGs, plus a sentence saying which they are.

    Extracted here rather than by the judge because a vision model cannot open an
    animated WebP's (or GIF's) later frames — it sees frame 0 and grades the
    animation on a still. `spec_path` lets steps-mode renders pick the frames that
    show the schedule (see `frame_picks`).
    Returns None when Pillow is unavailable in *this* interpreter, which is a
    separate question from whether the renderer could run.
    """
    if anim_path.lower().endswith(".mp4"):
        return _video_frame_pair(anim_path, outdir, spec_path)
    try:
        from PIL import Image
    except ImportError:
        return None
    out = []
    with Image.open(anim_path) as anim:
        # A spec with no motion collapses to a single frame (both encoders drop
        # identical frames), and seeking past it raises EOFError. Show the judge
        # frame 0 twice instead: "indistinguishable" is the right verdict for a
        # file that does not animate, and it is a score, not a crash.
        picks, note = frame_picks(spec_path, anim.n_frames)
        for i, index in enumerate(picks):
            anim.seek(index)
            dest = os.path.join(outdir, f"frame{i}.png")
            anim.convert("RGB").save(dest, "PNG")
            out.append(dest)
    return (out[0], out[1], note) if len(out) == 2 else None


def video_frame_count(path: str) -> int:
    """How many frames the MP4 has, 0 when it cannot be read."""
    probe = _ffmpeg("ffprobe")
    if not probe:
        return 0
    for args in (["-show_entries", "stream=nb_frames"],
                 ["-count_frames", "-show_entries", "stream=nb_read_frames"]):
        proc = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
            [probe, "-v", "error", "-select_streams", "v:0", *args, "-of", "csv=p=0", path],
            capture_output=True, text=True)
        value = (proc.stdout or "").strip()
        if value.isdigit() and int(value) > 0:
            return int(value)
    return 0


def _video_frame_pair(anim_path: str, outdir: str,
                      spec_path: str | None) -> tuple[str, str, str] | None:
    """The same two frames, out of an MP4, with ffmpeg instead of Pillow.

    Pure ffmpeg on purpose: phase 2 runs under an interpreter that need not have
    Pillow, and writing the PNGs with `-vcodec png` keeps this path working there.
    Without it a run that delivered an MP4 would silently be judged on a re-render.
    """
    ffmpeg = _ffmpeg()
    n_frames = video_frame_count(anim_path)
    if not ffmpeg or n_frames <= 0:
        return None
    picks, note = frame_picks(spec_path, n_frames)
    out = []
    for i, index in enumerate(picks):
        dest = os.path.join(outdir, f"frame{i}.png")
        proc = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
            [ffmpeg, "-y", "-v", "error", "-i", anim_path,
             "-vf", f"select=eq(n\\,{int(min(index, n_frames - 1))})",
             "-fps_mode", "passthrough", "-frames:v", "1", dest],
            capture_output=True, text=True)
        if proc.returncode != 0 or not os.path.isfile(dest):
            return None
        out.append(dest)
    return (out[0], out[1], note) if len(out) == 2 else None
