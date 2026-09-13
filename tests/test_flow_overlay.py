"""Deterministic reference-image, geometry and real media-export regressions."""
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "visual-flow-webp"


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    obj = importlib.util.module_from_spec(spec)
    sys.modules[name] = obj
    spec.loader.exec_module(obj)
    return obj


renderer = module("flow_renderer_test", SKILL / "scripts/render_animated_webp.py")
initializer = module("flow_initializer_test", SKILL / "scripts/init_overlay.py")
lint = module("flow_lint_test", SKILL / "scripts/lint_spec.py")
geometry = module("flow_geometry_test", SKILL / "eval/specgeom.py")


def overlay_spec(background="flow.background.png"):
    return {
        "canvas": {"width": 960, "height": 540, "fps": 20},
        "background": {"image": background},
        "nodes": [
            {"id": "client", "type": "anchor", "label": "Original client label",
             "x": 60, "y": 225, "w": 160, "h": 90},
            {"id": "api", "type": "anchor", "x": 370, "y": 225, "w": 170, "h": 90},
            {"id": "database", "type": "anchor", "x": 740, "y": 110, "w": 160, "h": 90},
            {"id": "queue", "type": "anchor", "x": 740, "y": 350, "w": 160, "h": 90},
        ],
        "edges": [
            {"from": "client", "to": "api", "points": [[220, 270], [370, 270]]},
            {"from": "api", "to": "database",
             "points": [[540, 270], [630, 270], [630, 155], [740, 155]]},
            {"from": "api", "to": "queue",
             "points": [[540, 270], [630, 270], [630, 395], [740, 395]]},
        ],
        "animation": {"steps": [{"edges": [0]}, {"edges": [1, 2]}],
                      "step_seconds": 1.0, "hold_seconds": 0.5,
                      "trail": False, "motion_color": "#007fdb"},
    }


class FlowOverlayTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = SKILL / "assets/overlay-reference.png"
        result = initializer.init_overlay(self.source, self.root / "flow.spec.json")
        self.spec = overlay_spec(Path(result["background"]).name)
        (self.root / "flow.spec.json").write_text(json.dumps(self.spec))
        self.flow = renderer.FlowRenderer(self.spec, self.root)

    def test_original_pixels_and_bytes_survive_compositing(self):
        before = self.source.read_bytes()
        with Image.open(self.source) as image:
            expected = image.convert("RGB")
        base = self.flow.finish_static(self.flow.draw_scene())
        self.assertIsNone(ImageChops.difference(base, expected).getbbox())
        self.assertEqual(before, (self.root / "flow.background.png").read_bytes())
        mid = self.flow.frame(base, 10, 50)
        self.assertIsNotNone(ImageChops.difference(base, mid).getbbox())
        # Header and blank regions are never changed by theme texture or motion.
        self.assertIsNone(ImageChops.difference(base, mid).crop((0, 0, 960, 90)).getbbox())
        arrival = self.flow.frame(base, 19, 50)
        self.assertIsNone(ImageChops.difference(base, arrival).crop((410, 250, 510, 290)).getbbox())
        self.assertEqual(self.source.read_bytes(), before)

    def test_paths_keep_original_elbows_and_optional_annotations(self):
        self.assertEqual(self.flow.edge_route(self.spec["edges"][1]),
                         [tuple(p) for p in self.spec["edges"][1]["points"]])
        spec = copy.deepcopy(self.spec)
        spec["edges"][0].update(draw_path=True, stroke="#ff0000")
        other = renderer.FlowRenderer(spec, self.root)
        self.assertIsNotNone(ImageChops.difference(other.draw_scene(), self.flow.draw_scene()).getbbox())

    def test_missing_corrupt_and_mismatched_images_fail_without_fallback(self):
        for change in ({"image": "missing.png"}, {"image": ""}):
            with self.subTest(change=change), self.assertRaises((OSError, ValueError)):
                renderer.FlowRenderer({**self.spec, "background": change}, self.root)
        (self.root / "broken.png").write_bytes(b"not an image")
        with self.assertRaises(OSError):
            renderer.FlowRenderer({**self.spec, "background": {"image": "broken.png"}}, self.root)
        for canvas in ({"width": 480, "height": 270}, {}):
            with self.subTest(canvas=canvas), self.assertRaisesRegex(ValueError, "match"):
                renderer.FlowRenderer({**self.spec, "canvas": canvas}, self.root)

    def test_initializer_refuses_overwrites_and_creates_portable_copy(self):
        before = (self.root / "flow.spec.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "overwrite"):
            initializer.init_overlay(self.source, self.root / "flow.spec.json")
        self.assertEqual((self.root / "flow.spec.json").read_bytes(), before)
        moved = self.root / "moved"
        moved.mkdir()
        for name in ("flow.spec.json", "flow.background.png"):
            shutil.copyfile(self.root / name, moved / name)
        self.assertEqual(renderer.FlowRenderer(self.spec, moved).background.size, (960, 540))
        with self.assertRaisesRegex(ValueError, "overwrite"):
            self.flow.write(self.root, "flow.background")

    def test_alpha_matting_and_exif_orientation(self):
        transparent = Image.new("RGBA", (96, 54), (255, 0, 0, 0))
        transparent.putpixel((10, 10), (10, 20, 30, 255))
        transparent.save(self.root / "alpha.png")
        spec = {"canvas": {"width": 96, "height": 54},
                "background": {"image": "alpha.png", "matte": "#112233"}}
        background = renderer.FlowRenderer(spec, self.root).draw_scene()
        self.assertEqual(background.getpixel((0, 0)), (17, 34, 51))
        self.assertEqual(background.getpixel((10, 10)), (10, 20, 30))
        exif = Image.Exif()
        exif[274] = 6
        Image.new("RGB", (96, 54), "white").save(self.root / "rotated.jpg", exif=exif)
        result = initializer.init_overlay(self.root / "rotated.jpg", self.root / "rotated.spec.json")
        self.assertEqual((result["width"], result["height"]), (54, 96))
        spec = json.loads((self.root / "rotated.spec.json").read_text())
        self.assertEqual(renderer.FlowRenderer(spec, self.root).background.size, (54, 96))

    def test_animated_background_is_rejected(self):
        frames = [Image.new("RGB", (96, 54), color) for color in ("white", "black")]
        path = self.root / "animated.webp"
        frames[0].save(path, save_all=True, append_images=frames[1:], duration=100)
        with self.assertRaisesRegex(ValueError, "static WebP"):
            initializer.init_overlay(path, self.root / "animated.spec.json")
        with self.assertRaisesRegex(ValueError, "static WebP"):
            renderer.FlowRenderer({"canvas": {"width": 96, "height": 54},
                                   "background": {"image": path.name}}, self.root)

    def test_lint_checks_source_coordinates_and_ignores_invisible_labels(self):
        self.assertEqual(lint.check_overlay(self.spec, self.root), [])
        self.assertEqual(geometry.analyze(str(self.root / "flow.spec.json")), [])
        spec = copy.deepcopy(self.spec)
        spec["nodes"][0].update(label="This metadata label is deliberately much too long to render",
                                body="→\n✓\n✗\n×\n\uE000", x=370)
        self.assertEqual(lint.check_overlaps(spec), [])
        self.assertEqual(geometry.check_overlaps(spec), [])
        self.assertEqual(geometry.check_text_fits(spec), [])
        self.assertEqual(geometry.check_copy_length(spec), [])
        self.assertEqual(geometry.check_glyph_coverage(spec), [])
        spec["edges"][0]["points"] = [[0, 0], [961, 270]]
        self.assertTrue(lint.check_overlay(spec, self.root))

    def test_webp_export_from_another_working_directory(self):
        proc = subprocess.run(
            [sys.executable, str(SKILL / "scripts/render_animated_webp.py"),
             "--spec", str(self.root / "flow.spec.json"), "--outdir", str(self.root / "out"),
             "--basename", "flow", "--verify", "--check"],
            cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("✓ PASS", proc.stdout)
        result = json.loads(proc.stdout[proc.stdout.index("{"):])
        self.assertEqual(result["source_mode"], "overlay")
        self.assertTrue(all(c["ok"] for c in result["checks"]["checks"]))
        self.assertIn("background_pixels_preserved", proc.stdout)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg/ffprobe required")
    def test_real_mp4_export_and_step_isolation(self):
        result = self.flow.write(self.root, "movie", "mp4")
        checks = renderer.validate_outputs(result, self.spec, self.flow)
        self.assertTrue(checks["ok"], checks)
        probe = renderer.probe_video(result["animation"])
        self.assertEqual((probe["width"], probe["height"], probe["n_frames"]), (960, 540, 50))
        self.assertEqual(probe["codec"], "h264")
        self.assertTrue(probe["has_audio"] and probe["faststart"])

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg/ffprobe required")
    def test_odd_size_mp4_pads_without_resizing(self):
        source = Image.new("RGB", (101, 55), "white")
        source.save(self.root / "odd.png")
        spec = {"canvas": {"width": 101, "height": 55, "fps": 20, "frames": 10},
                "background": {"image": "odd.png"},
                "animation": {"paths": [{"points": [[10, 25], [90, 25]]}]}}
        flow = renderer.FlowRenderer(spec, self.root)
        result = flow.write(self.root, "odd-movie", "mp4")
        with Image.open(result["png"]) as still:
            self.assertEqual(still.size, (101, 55))
        probe = renderer.probe_video(result["animation"])
        self.assertEqual((probe["width"], probe["height"]), (102, 56))

    def test_generated_mode_still_draws_and_animates(self):
        spec = json.loads((SKILL / "assets/default-spec.json").read_text())
        flow = renderer.FlowRenderer(spec)
        self.assertIsNone(flow.background)
        base = flow.finish_static(flow.draw_scene())
        mid = flow.frame(base, 10, flow.canvas["frames"])
        self.assertIsNotNone(ImageChops.difference(base, mid).getbbox())
        blank = Image.new("RGB", base.size, flow.theme["bg"])
        self.assertIsNotNone(ImageChops.difference(base, blank).getbbox())


if __name__ == "__main__":
    unittest.main()
