#!/usr/bin/env python3
"""Create an editable overlay spec and a byte-for-byte copy of its background."""
import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageOps


def init_overlay(image_path, spec_path):
    image_path, spec_path = Path(image_path).resolve(), Path(spec_path).resolve()
    with Image.open(image_path) as image:
        suffix = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}.get(image.format)
        if not suffix or getattr(image, "n_frames", 1) != 1:
            raise ValueError("use a PNG, JPEG or static WebP; rasterize other inputs first")
        width, height = ImageOps.exif_transpose(image).size
    stem = spec_path.stem.removesuffix(".spec")
    background_path = spec_path.with_name(stem + ".background" + suffix)
    for dest in (spec_path, background_path):
        if dest.exists() or dest == image_path:
            raise ValueError(f"refusing to overwrite {dest}; choose a new --spec name")
    spec = {
        "canvas": {"width": width, "height": height, "fps": 20},
        "background": {"image": background_path.name},
        "nodes": [],
        "edges": [],
        "animation": {"steps": [], "step_seconds": 1.5, "hold_seconds": 1.5,
                      "trail": False, "motion_color": "#007fdb"},
    }
    data = image_path.read_bytes()
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    with background_path.open("xb") as fh:
        fh.write(data)
    with spec_path.open("x", encoding="utf-8") as fh:
        json.dump(spec, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return {"spec": str(spec_path), "background": str(background_path),
            "width": width, "height": height, "sha256": hashlib.sha256(data).hexdigest(),
            "next": "Inspect the image, add anchor nodes, traced edge points and ordered animation.steps, then lint and render."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="Local reference image; never modified.")
    parser.add_argument("--spec", required=True, help="New spec path; a companion background is copied beside it.")
    args = parser.parse_args()
    try:
        print(json.dumps(init_overlay(args.image, args.spec), indent=2, ensure_ascii=False))
    except (OSError, ValueError) as exc:
        parser.exit(1, f"✗ Cannot initialize overlay: {exc}\n")


if __name__ == "__main__":
    main()
