"""Regression tests for recording integrity and accepting reviewed scores."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from evalkit.records import (
    IntegrityError, accept_baseline, digest, join_recording, metadata_path,
    requested_cases, select_datasets, skills_root, write_json,
)


class RecordsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.results = self.root / "results.json"
        self.cases = [{"id": "a", "prompt": "first"}, {"id": "b", "prompt": "second"}]

    def test_missing_case_is_an_error(self):
        write_json(self.results, [{"case": "a"}])
        with self.assertRaisesRegex(IntegrityError, "missing case records: b"):
            join_recording(self.cases, str(self.results))

    def test_duplicate_record_is_an_error_even_for_a_subset(self):
        write_json(self.results, [{"case": "a"}, {"case": "a"}])
        with self.assertRaisesRegex(IntegrityError, "duplicate"):
            join_recording(self.cases[:1], str(self.results))

    def test_subset_preserves_requested_order(self):
        write_json(self.results, [{"case": "b"}, {"case": "a"}])
        entries = join_recording(self.cases[:1], str(self.results))
        self.assertEqual([e["recorded"]["case"] for e in entries], ["a"])

    def test_artifact_must_exist_and_match_recording(self):
        artifact = self.root / "output.txt"
        artifact.write_text("original")
        write_json(self.results, [{"case": "a", "files": {
            "output.txt": {"saved_to": str(artifact), "sha256": digest(artifact)}
        }}])
        join_recording(self.cases[:1], str(self.results))
        artifact.write_text("changed")
        with self.assertRaisesRegex(IntegrityError, "changed saved artifact"):
            join_recording(self.cases[:1], str(self.results))
        artifact.unlink()
        with self.assertRaisesRegex(IntegrityError, "missing saved artifact"):
            join_recording(self.cases[:1], str(self.results))

    def test_selection_uses_custom_root_and_rejects_unknown_ids(self):
        dataset = self.root / "custom" / "eval" / "dataset.jsonl"
        write_json(dataset, self.cases)
        with patch.dict(os.environ, {"SKILLS_DIR": str(self.root)}):
            self.assertEqual(skills_root(), self.root.resolve())
            paths = select_datasets(skills_root(), ["custom"])
        self.assertEqual([c["id"] for c in requested_cases(paths, ["b"])], ["b"])
        with self.assertRaisesRegex(IntegrityError, "unknown case"):
            requested_cases(paths, ["typo"])

    def test_duplicate_dataset_ids_rejected_before_filtering(self):
        first, second = self.root / "one.json", self.root / "two.json"
        write_json(first, self.cases)
        write_json(second, self.cases[:1])
        with self.assertRaisesRegex(IntegrityError, "duplicate dataset"):
            requested_cases([str(first), str(second)], ["b"])

    def accepted_report(self):
        report = self.root / "report.json"
        rows = [{"case": "a", "skill": "custom", "overall_score": 0.8,
                 "judge_model": "judge-1", "judge_provider": "openai", "rubric_hash": "abc",
                 "rows": [{"test_pass": True, "gate": True},
                          {"test_pass": False, "gate": False}]}]
        write_json(report, rows)
        meta = {"complete": True, "gates_passed": True, "scoring_profile": "core",
                "requested_cases": ["a"], "report_sha256": digest(report), "regressed": ["a"]}
        write_json(metadata_path(report), meta)
        baseline = self.root / "baseline.json"
        write_json(baseline, {"cases": {"other": {"overall_score": 0.9}}})
        return report, baseline, rows, meta

    def test_accept_merges_reviewed_regression_without_model_calls(self):
        report, baseline, _, _ = self.accepted_report()
        with patch("evalkit.models.build_model", side_effect=AssertionError("no model calls")):
            self.assertEqual(accept_baseline(report, baseline), 1)
        accepted = json.loads(baseline.read_text())["cases"]
        self.assertEqual(accepted["a"]["overall_score"], 0.8)
        self.assertEqual(accepted["a"]["judge_provider"], "openai")
        self.assertEqual(accepted["other"]["overall_score"], 0.9)

    def test_refuse_failed_incomplete_tampered_or_empty_gates_without_changing_baseline(self):
        for defect in ("failed", "incomplete", "tampered", "missing-case", "no-gates",
                       "invalid-score", "pending-controls"):
            with self.subTest(defect=defect):
                report, baseline, rows, meta = self.accepted_report()
                before = baseline.read_bytes()
                if defect == "failed":
                    meta["gates_passed"] = False
                elif defect == "incomplete":
                    meta["complete"] = False
                elif defect == "missing-case":
                    meta["requested_cases"].append("b")
                elif defect == "no-gates":
                    rows[0]["rows"] = [{"test_pass": False, "gate": False}]
                elif defect == "invalid-score":
                    rows[0]["overall_score"] = 2
                elif defect == "pending-controls":
                    meta["negative_controls_requested"] = True
                    meta["controls_complete"] = False
                else:
                    rows[0]["overall_score"] = 1
                write_json(report, rows)
                if defect != "tampered":
                    meta["report_sha256"] = digest(report)
                write_json(metadata_path(report), meta)
                with self.assertRaises(IntegrityError):
                    accept_baseline(report, baseline)
                self.assertEqual(baseline.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
