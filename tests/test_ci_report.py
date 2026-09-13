"""Dashboard status must preserve gates, missing reports and untrusted evidence."""
import json
from pathlib import Path
import tempfile
import unittest

from evalkit import ci_report


class CIReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.selection = {"skills": ["alpha"], "exit_code": 0, "plan_only": False}
        self.run = {"status": "passed"}
        self.meta = {"complete": True, "gates_passed": True, "regressed": [],
                     "requested_cases": ["case-a"], "negative_controls_requested": True,
                     "controls_complete": True}
        self.case = {"case": "case-a", "skill": "alpha", "overall_score": 0.95,
                     "scoring_profile": "core",
                     "rows": [{"evaluator": "Assertions[#1]", "score": 1.0, "gate": True,
                               "test_pass": True, "reason": "OK"}]}

    def write(self, path, value):
        dest = self.root / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(value))

    def report(self, env=None):
        self.write(".eval/selection.json", self.selection)
        self.write(".eval/runs/run-a/run.json", self.run)
        self.write(".eval/runs/run-a/scores/score-a/report.meta.json", self.meta)
        self.write(".eval/runs/run-a/scores/score-a/report.json", [self.case])
        return ci_report.summarize(self.root, env or {})

    def test_pass_requires_completed_run(self):
        self.assertEqual(self.report()["status"], "PASS")
        self.run["status"] = "running"
        self.assertEqual(self.report()["status"], "FAIL / INCOMPLETE")

    def test_missing_and_corrupt_reports_are_not_green(self):
        self.write(".eval/selection.json", self.selection)
        self.assertEqual(ci_report.summarize(self.root, {})["status"], "FAIL / INCOMPLETE")
        self.report()
        report = self.root / ".eval/runs/run-a/scores/score-a/report.json"
        report.write_text("{truncated")
        summary = ci_report.summarize(self.root, {})
        self.assertEqual(summary["status"], "FAIL / INCOMPLETE")
        self.assertTrue(summary["errors"])

    def test_failing_row_is_red_even_with_high_overall_and_escaped_in_html(self):
        self.case["rows"][0].update(test_pass=False, reason="Evidence:\n<script>alert(1)</script>")
        summary = self.report()
        self.assertEqual(summary["status"], "FAIL / INCOMPLETE")
        self.assertEqual(summary["failed_rows"], 1)
        html = ci_report.render(summary)
        self.assertIn("Evidence:\n&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertNotIn("<script>", html)

    def test_tracked_only_failure_does_not_gate(self):
        self.case["rows"][0].update(gate=False, test_pass=False)
        self.assertEqual(self.report()["status"], "PASS")

    def test_baseline_regression_and_control_failure_gate(self):
        self.meta["regressed"] = ["case-a"]
        self.assertEqual(self.report()["status"], "FAIL / INCOMPLETE")
        self.meta["regressed"] = []
        self.meta["controls_complete"] = False
        self.assertEqual(self.report()["status"], "FAIL / INCOMPLETE")
        self.meta["controls_complete"] = True
        self.meta["gates_passed"] = False
        self.meta["negative_controls"] = [{"skill": "alpha", "exit_code": 1}]
        self.assertEqual(self.report()["status"], "FAIL / INCOMPLETE")

    def test_missing_requested_case_and_failed_job_are_not_green(self):
        self.assertEqual(self.report({"CI_JOB_STATUS": "failed"})["status"], "FAIL / INCOMPLETE")
        self.meta["requested_cases"].append("missing")
        self.assertEqual(self.report()["status"], "FAIL / INCOMPLETE")

    def test_empty_selection_is_skipped_but_plan_only_is_not_a_pass(self):
        self.selection["skills"] = []
        self.write(".eval/selection.json", self.selection)
        self.assertEqual(ci_report.summarize(self.root, {})["status"], "SKIPPED")
        self.selection["plan_only"] = True
        self.write(".eval/selection.json", self.selection)
        self.assertEqual(ci_report.summarize(self.root, {})["status"], "FAIL / INCOMPLETE")

    def test_baseline_delta_and_profile_warning(self):
        self.write("eval-baseline.json", {"cases": {"case-a": {
            "overall_score": 1, "rubric_hash": "old"}}})
        note = self.report()["cases"][0]["baseline"]
        self.assertIn("-0.050", note)
        self.assertIn("legacy profile unknown", note)
        self.assertIn("rubric changed", note)
        self.write("eval-baseline.json", {"cases": {"case-a": {
            "overall_score": 1, "scoring_profile": "full"}}})
        self.assertEqual(self.report()["cases"][0]["baseline"], "Incompatible profiles")


if __name__ == "__main__":
    unittest.main()
