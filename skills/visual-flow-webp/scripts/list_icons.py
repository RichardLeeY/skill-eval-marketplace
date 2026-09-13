#!/usr/bin/env python3
"""Find icon keys for a spec's `icon` field.

    python3 scripts/list_icons.py direct connect
    python3 scripts/list_icons.py --provider aws gateway

Prints keys like `aws/network/direct-connect`, one per line, AWS first. Every
word must appear in the key. The icon set is the one the `diagrams` package
installs (pip install diagrams); VISUAL_FLOW_ICON_DIR overrides its location.
Pure stdlib.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from pathlib import Path


def icon_root() -> Path | None:
    override = os.environ.get("VISUAL_FLOW_ICON_DIR")
    if override:
        return Path(override) if Path(override).is_dir() else None
    spec = importlib.util.find_spec("diagrams")
    if spec is None or not spec.origin:
        return None
    base = Path(spec.origin).resolve().parent
    for candidate in (base.parent / "resources", base / "resources"):
        if candidate.is_dir():
            return candidate
    return None


def all_keys(root: Path) -> list[str]:
    keys = [p.relative_to(root).with_suffix("").as_posix() for p in root.glob("*/*/*.png")]
    keys += [p.relative_to(root).with_suffix("").as_posix() for p in root.glob("*/*.png")]
    return sorted(set(keys), key=lambda k: (not k.startswith("aws/"), k))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("words", nargs="*", help="every word must appear in the key")
    ap.add_argument("--provider", help="aws, gcp, azure, k8s, onprem, generic, ...")
    ap.add_argument("--limit", type=int, default=40)
    args = ap.parse_args()
    root = icon_root()
    if root is None:
        print("no icon set found: python3 -m pip install diagrams", file=sys.stderr)
        return 2
    words = [w.lower().replace(" ", "-") for w in args.words]
    hits = [k for k in all_keys(root)
            if (not args.provider or k.split("/")[0] == args.provider)
            and all(w in k.lower() for w in words)]
    for key in hits[:args.limit]:
        print(key)
    if len(hits) > args.limit:
        print(f"... {len(hits) - args.limit} more; add words or --limit", file=sys.stderr)
    if not hits:
        print("no match; try fewer or different words", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
