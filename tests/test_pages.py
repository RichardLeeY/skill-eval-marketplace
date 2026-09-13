"""The published scoreboard must record every run honestly and leak no extra evidence."""
import json
from pathlib import Path
import tempfile
import unittest

from evalkit import pages


class PagesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.site = self.root / "site"
        self.run = self.root / ".eval"
        (self.root / "skills" / "alpha").mkdir(parents=True)
        (self.root / "skills" / "alpha" / "SKILL.md").write_text("# alpha")
        (self.root / "skills" / "beta").mkdir()
        (self.root / "skills" / "beta" / "SKILL.md").write_text("# beta")
        self.baseline = self.root / "eval-baseline.json"
        self.baseline.write_text(json.dumps({"cases": {
            "case-a": {"overall_score": 0.95, "skill": "alpha", "recorded": "2026-09-07"},
            "case-b": {"overall_score": 0.91, "skill": "beta", "recorded": "2026-09-08"}}}))
        self.env = {"GITHUB_REPOSITORY": "o/r", "GITHUB_SERVER_URL": "https://github.com"}

    def write(self, relative, content):
        path = self.run / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content if isinstance(content, str) else json.dumps(content))
        return path

    def summary(self, sha="a" * 40, status="PASS", cases=None, date="2026-09-13T10:00:00+00:00"):
        cases = cases if cases is not None else [
            {"case": "case-a", "skill": "alpha", "score": 0.96, "status": "PASS"},
            {"case": "case-b", "skill": "beta", "score": 0.88, "status": "FAIL"}]
        return {"status": status, "generated_at": date, "commit": sha,
                "pipeline": "https://github.com/o/r/actions/runs/1",
                "selection": {"skills": ["alpha", "beta"]}, "cases": cases, "errors": []}

    def build(self, **kwargs):
        return pages.build(self.site, self.run, self.baseline, self.root, self.env, **kwargs)

    def test_run_is_indexed_with_per_skill_scores_and_badges(self):
        self.write("summary.json", self.summary())
        result = self.build()
        self.assertEqual(result["published"], "a" * 40)
        index = json.loads((self.site / "data" / "index.json").read_text())
        self.assertEqual(len(index), 1)
        self.assertEqual(index[0]["skills"]["alpha"], {"cases": 1, "passed": 1, "mean": 0.96,
                                                       "min": 0.96, "status": "PASS"})
        self.assertEqual(index[0]["skills"]["beta"]["status"], "FAIL")
        alpha = json.loads((self.site / "badge" / "alpha.json").read_text())
        beta = json.loads((self.site / "badge" / "beta.json").read_text())
        self.assertEqual((alpha["message"], alpha["color"]), ("0.960", "brightgreen"))
        self.assertEqual((beta["message"], beta["color"]), ("0.880", "red"))
        html = (self.site / "index.html").read_text()
        self.assertIn("runs/" + "a" * 40 + "/dashboard.html", html)
        self.assertIn("https://github.com/o/r/blob/main/skills/alpha/SKILL.md", html)
        self.assertTrue((self.site / ".nojekyll").exists())

    def test_high_score_with_failed_gate_is_yellow_not_green(self):
        cases = [{"case": "case-a", "skill": "alpha", "score": 0.97, "status": "FAIL"}]
        self.write("summary.json", self.summary(status="FAIL / INCOMPLETE", cases=cases))
        self.build()
        alpha = json.loads((self.site / "badge" / "alpha.json").read_text())
        self.assertEqual(alpha["color"], "yellow")
        html = (self.site / "index.html").read_text()
        self.assertIn('class="fail">FAIL / INCOMPLETE', html)

    def test_only_allowlisted_evidence_is_copied(self):
        self.write("summary.json", self.summary())
        self.write("dashboard.html", "<html>")
        self.write("selection.json", {})
        self.write("runs/r1/run.json", {"status": "passed"})
        self.write("runs/r1/execute.log", "log")
        self.write("runs/r1/scores/s1/report.json", [])
        self.write("runs/r1/scores/s1/report.meta.json", {})
        self.write("runs/r1/scores/s1/derived/frames/f1.png", b"png")
        self.write("runs/r1/artifacts/case-a/diagram.webp", b"webp")
        self.write("runs/r1/artifacts/case-a/video.mp4", b"mp4")
        self.write("runs/r1/artifacts/case-a/secret.env", "KEY=1")
        self.write("runs/r1/artifacts/case-a/huge.png", b"0" * (pages.IMAGE_LIMIT + 1))
        self.write("diagnostics/notes.md", "private")
        self.build()
        published = self.site / "runs" / ("a" * 40)
        copied = sorted(p.relative_to(published).as_posix() for p in published.rglob("*") if p.is_file())
        self.assertEqual(copied, [
            "dashboard.html", "runs/r1/artifacts/case-a/diagram.webp", "runs/r1/execute.log",
            "runs/r1/run.json", "runs/r1/scores/s1/derived/frames/f1.png",
            "runs/r1/scores/s1/report.json", "runs/r1/scores/s1/report.meta.json",
            "selection.json", "summary.json"])

    def test_history_dedupes_by_sha_prunes_and_keeps_newest_first(self):
        for i, day in enumerate(("11", "12", "13")):
            sha = str(i) * 40
            self.write("summary.json", self.summary(sha=sha, date=f"2026-09-{day}T00:00:00+00:00"))
            self.build(keep=2)
        self.write("summary.json", self.summary(sha="2" * 40, date="2026-09-13T01:00:00+00:00"))
        result = self.build(keep=2)
        index = json.loads((self.site / "data" / "index.json").read_text())
        self.assertEqual([e["sha"][0] for e in index], ["2", "1"])
        self.assertEqual(result["pruned"], [])
        self.assertFalse((self.site / "runs" / ("0" * 40)).exists())
        self.assertFalse((self.site / "data" / "runs" / ("0" * 40 + ".json")).exists())
        self.assertTrue((self.site / "runs" / ("1" * 40)).exists())

    def test_missing_run_still_publishes_baseline_column(self):
        result = pages.build(self.site, None, self.baseline, self.root, self.env)
        self.assertIsNone(result["published"])
        html = (self.site / "index.html").read_text()
        self.assertIn("0.950", html)
        self.assertIn("No published run yet", html)
        overall = json.loads((self.site / "badge" / "marketplace.json").read_text())
        self.assertEqual(overall["message"], "no run")

    def test_html_escapes_untrusted_names(self):
        cases = [{"case": "<img src=x onerror=alert(1)>", "skill": "<b>alpha</b>", "score": 0.5,
                  "status": "FAIL"}]
        self.write("summary.json", self.summary(status="FAIL / INCOMPLETE", cases=cases))
        self.build()
        html = (self.site / "index.html").read_text()
        self.assertNotIn("<b>alpha</b>", html)
        self.assertIn("&lt;b&gt;alpha&lt;/b&gt;", html)


if __name__ == "__main__":
    unittest.main()
