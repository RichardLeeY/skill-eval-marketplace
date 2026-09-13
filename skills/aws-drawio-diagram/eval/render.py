"""Render a diagram artifact to PNG so a vision judge can look at it.

Only needed for the layer geometry cannot reach: a missing icon renders as an
empty box, and visual weight or legibility is not a coordinate. `geometry.py`
should run first -- it is exact and free, and its findings give the vision
judge something checkable rather than an open-ended "does this look right".

draw.io's exporter is the desktop app in headless mode; it is an Electron
binary, so on Linux it needs a virtual framebuffer (`xvfb-run`) while on macOS
the bundled binary exports directly. That difference is the whole argument for
putting this step somewhere with a real OS if it ever moves off a laptop.
"""

from __future__ import annotations

import os
import shutil
import subprocess

# Checked in order. The macOS bundle path is not on PATH, so looking only for a
# `drawio` command finds nothing on the machine most likely to have the app.
_CANDIDATES = (
    "/Applications/draw.io.app/Contents/MacOS/draw.io",
    "/Applications/drawio.app/Contents/MacOS/drawio",
    shutil.which("drawio") or "",
    shutil.which("draw.io") or "",
)

RENDERABLE = {".drawio", ".xml", ".drawio.xml"}


class RenderUnavailable(RuntimeError):
    """No exporter on this machine.

    Raised rather than returning None so a missing renderer is reported as an
    absent tool. Scoring it as a layout failure would blame the skill for the
    harness's own missing dependency.
    """


def find_exporter() -> str:
    for path in _CANDIDATES:
        if path and os.path.exists(path):
            return path
    raise RenderUnavailable(
        "no draw.io exporter found; install the desktop app or put `drawio` on PATH"
    )


def render(src: str, out_png: str, scale: int = 2, border: int = 10,
           timeout: int = 120) -> str:
    """Export `src` to `out_png`, returning the output path.

    Args:
        scale: Passed to the exporter. 2 keeps 12 pt labels legible to a vision
            model after downsampling; at 1 the text a layout judgment depends on
            is the first thing lost.
        border: Padding, so shapes at the edge are not flush against the crop.
    """
    exporter = find_exporter()
    os.makedirs(os.path.dirname(os.path.abspath(out_png)), exist_ok=True)
    cmd = [exporter, "--export", "--format", "png",
           "--scale", str(scale), "--border", str(border),
           "--output", out_png, src]
    if os.name != "nt" and not exporter.endswith(".app/Contents/MacOS/draw.io"):
        # Electron off macOS needs a display. Only wrap when xvfb-run exists, so
        # a machine with a real display is not forced through it.
        if shutil.which("xvfb-run"):
            cmd = ["xvfb-run", "-a", *cmd]
    # List form, no shell: a hostile path becomes one argv element, not a command.
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # nosemgrep: dangerous-subprocess-use-audit
    if not os.path.exists(out_png):
        raise RenderUnavailable(
            f"export produced no file (exit={proc.returncode}): "
            f"{(proc.stderr or proc.stdout or '').strip()[:300]}"
        )
    return out_png


def render_if_possible(src: str, out_png: str) -> tuple[str | None, str | None]:
    """`(png_path, None)` on success, `(None, reason)` when rendering is not
    available -- so a caller can degrade to geometry-only instead of failing."""
    try:
        return (render(src, out_png), None)
    except (RenderUnavailable, subprocess.TimeoutExpired, OSError) as e:
        return (None, f"{type(e).__name__}: {e}")
