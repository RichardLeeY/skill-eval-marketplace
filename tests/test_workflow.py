"""Offline checks of the real runners, with model execution replaced by fixtures."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from evalkit import cli, doctor, run_eval
from evalkit.records import IntegrityError, accept_baseline, digest, join_recording, metadata_path, write_json


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dataset = self.root / "demo" / "eval" / "dataset.jsonl"
        self.cases = [{"id": "a", "prompt": "write a document", "expect": {"skill": "demo"}},
                      {"id": "b", "prompt": "write another", "expect": {"skill": "demo"}}]
        write_json(self.dataset, self.cases)
        self.results = self.root / "results.json"
        self.report = self.root / "report.json"
        self.addCleanup(patch.stopall)
        patch("sys.stdout", new=io.StringIO()).start()
        patch("sys.stderr", new=io.StringIO()).start()

    def test_phase1_records_provider_and_completed_cases_on_later_crash(self):
        response = {"status": "ok", "output": "done", "files": {}, "trace": {},
                    "model_provider": "openai", "model_id": "agent-x"}
        with patch.dict(os.environ, {"SKILLS_DIR": str(self.root)}), \
                patch.object(run_eval, "invoke_local", side_effect=[response, RuntimeError("interrupted")]), \
                patch("sys.argv", ["run_eval", "--out", str(self.results),
                                   "--artifacts", str(self.root / "artifacts")]):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                run_eval.main()
        rows = json.loads(self.results.read_text())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["model_provider"], "openai")
        self.assertEqual(rows[0]["model_id"], "agent-x")
        self.assertGreaterEqual(rows[0]["elapsed_seconds"], 0)

    def test_low_level_defaults_keep_results_out_of_repo_root(self):
        manual = self.root / ".eval" / "manual"
        response = {"status": "ok", "output": "done", "files": {}, "trace": {}}
        with patch.dict(os.environ, {"SKILLS_DIR": str(self.root)}), \
                patch.object(run_eval, "MANUAL", manual), \
                patch.object(run_eval, "invoke_local", return_value=response), \
                patch("sys.argv", ["run_eval"]):
            run_eval.main()
        self.assertEqual(len(json.loads((manual / "results.json").read_text())), 2)
        self.assertFalse(self.results.exists())

    def test_binary_delivery_survives_collection_and_legacy_hash_validation(self):
        original = self.root / "original.bin"
        content = b"\x89PNG\r\n\x1a\n\x00\xff" * 30000
        original.write_bytes(content)
        files = {"delivery.png": {"local_path": str(original), "sha256": digest(original)[:16]}}
        saved = run_eval.save_artifacts("a", {"files": files}, str(self.root / "artifacts"))
        self.assertEqual(Path(saved["delivery.png"]).read_bytes(), content)
        write_json(self.results, [{"case": "a", "files": {
            "delivery.png": {**files["delivery.png"], "saved_to": saved["delivery.png"]}
        }}])
        self.assertEqual(len(join_recording(self.cases[:1], str(self.results))), 1)
        Path(saved["delivery.png"]).write_bytes(b"changed")
        with self.assertRaisesRegex(IntegrityError, "changed saved artifact"):
            join_recording(self.cases[:1], str(self.results))

    def test_doctor_only_checks_selected_provider_and_case_dependencies(self):
        write_json(self.root / "demo" / "eval" / "dependencies.json",
                   {"case_commands": {"b": ["ffmpeg"]}, "visual_commands": ["drawio"]})
        env = {"MODEL_PROVIDER": "openai", "MODEL_ID": "agent-x",
               "JUDGE_MODEL_ID": "judge-x", "OPENAI_BASE_URL": "http://localhost:8000/v1"}
        with patch.dict(os.environ, env, clear=True), \
                patch.object(doctor, "has_dists", side_effect=lambda _, names: dict.fromkeys(names, "1")), \
                patch.object(doctor, "command_path", return_value=None), \
                patch("boto3.client", side_effect=AssertionError("OpenAI must not call STS")):
            rows = doctor.check(self.root, ["demo"], self.cases[:1])
            self.assertFalse(any(r["item"] in ("ffmpeg", "drawio", "credentials") for r in rows))
            rows = doctor.check(self.root, ["demo"], self.cases, profile="full")
            self.assertEqual({r["item"] for r in rows if not r["ok"]}, {"ffmpeg", "drawio"})

    def test_score_preflight_does_not_require_agent_configuration(self):
        with patch.dict(os.environ, {"MODEL_PROVIDER": "invalid", "JUDGE_MODEL_PROVIDER": "openai",
                                     "JUDGE_MODEL_ID": "judge", "OPENAI_BASE_URL": "http://localhost/v1"},
                        clear=True), \
                patch.object(doctor, "has_dists", side_effect=lambda _, names: dict.fromkeys(names, "1")):
            rows = doctor.check(self.root, ["demo"], self.cases, phase="score")
        self.assertTrue(all(r["ok"] for r in rows))
        self.assertFalse(any(r["area"] == "agent" for r in rows))

    def test_only_narrows_dependency_owners(self):
        other = self.root / "other" / "eval" / "dataset.jsonl"
        write_json(other, [{"id": "c", "prompt": "other"}])
        with patch.dict(os.environ, {"SKILLS_DIR": str(self.root)}):
            _, _, cases, names = cli.selection(SimpleNamespace(skill=None, only=["c"]))
        self.assertEqual(names, ["other"])
        self.assertEqual([c["id"] for c in cases], ["c"])

    def test_setup_no_deps_has_no_subprocess_or_credential_checks(self):
        with patch.dict(os.environ, {"SKILLS_DIR": str(self.root), "MODEL_PROVIDER": "bedrock"},
                        clear=True), patch.object(cli, "execute") as execute:
            self.assertEqual(cli.main(["setup", "--skill", "demo"]), 0)
            execute.assert_not_called()

    def test_failed_preflight_does_not_execute_or_create_a_run(self):
        runs = self.root / "runs"
        with patch.dict(os.environ, {"SKILLS_DIR": str(self.root)}), \
                patch.object(cli, "RUNS", runs), \
                patch.object(doctor, "check", return_value=[
                    {"area": "tool", "item": "ffmpeg", "ok": False, "required": True, "detail": ""}]), \
                patch.object(cli, "execute") as execute:
            self.assertEqual(cli.main(["run", "--skill", "demo"]), 2)
        execute.assert_not_called()
        self.assertFalse(runs.exists())

    def archive(self):
        directory = self.root / "archived"
        write_json(directory / "cases.json", self.cases)
        write_json(directory / "results.json", [{"case": c["id"]} for c in self.cases])
        write_json(directory / "run.json", {
            "skills_root": str(self.root.resolve()), "skills": ["demo"], "profile": "core",
            "cases_sha256": digest(directory / "cases.json"),
            "results_sha256": digest(directory / "results.json"),
        })
        return directory

    def fake_scoring(self, command, **kwargs):
        out = Path(command[command.index("--out") + 1])
        write_json(out, [{
            "case": c["id"], "skill": "demo", "overall_score": 1, "judge_model": "fixture",
            "judge_provider": "openai", "rubric_hash": "abc",
            "rows": [{"gate": True, "test_pass": True}],
        } for c in self.cases])
        write_json(metadata_path(out), {
            "complete": True, "gates_passed": True, "requested_cases": ["a", "b"],
            "report_sha256": digest(out), "scoring_profile": "core",
            "negative_controls_requested": "--pending-controls" in command,
            "controls_complete": "--pending-controls" not in command,
        })
        return 0

    def test_rescore_creates_new_reports_and_refuses_changed_archives_before_preflight(self):
        directory = self.archive()
        before = (directory / "results.json").read_bytes()
        with patch.dict(os.environ, {"SKILLS_DIR": str(self.root)}), \
                patch.object(doctor, "check", return_value=[]) as check, \
                patch.object(cli, "execute", side_effect=self.fake_scoring) as execute:
            for _ in range(2):
                self.assertEqual(cli.main(["score", "--run", str(directory)]), 0)
            self.assertEqual(len(list((directory / "scores").glob("*/report.json"))), 2)
            self.assertEqual((directory / "results.json").read_bytes(), before)
            for call in execute.call_args_list:
                self.assertIn("evalkit.run_strands_eval", call.args[0])
                self.assertNotIn("evalkit.run_eval", call.args[0])
            check.reset_mock()
            execute.reset_mock()
            write_json(directory / "cases.json", self.cases[:1])
            self.assertEqual(cli.main(["score", "--run", str(directory)]), 2)
            check.assert_not_called()
            execute.assert_not_called()

    def test_control_failure_cannot_be_accepted_as_baseline(self):
        directory = self.archive()
        script = self.root / "demo" / "eval" / "check_negative_control.py"
        script.write_text("# control fixture")

        def execute(command, **kwargs):
            if "evalkit.run_strands_eval" in command:
                self.assertIn("--pending-controls", command)
                return self.fake_scoring(command, **kwargs)
            return 1

        with patch.dict(os.environ, {"SKILLS_DIR": str(self.root)}), \
                patch.object(doctor, "check", return_value=[]), \
                patch.object(cli, "execute", side_effect=execute):
            self.assertEqual(cli.main(["score", "--run", str(directory), "--negative-controls"]), 1)
        report = next((directory / "scores").glob("*/report.json"))
        meta = json.loads(metadata_path(report).read_text())
        self.assertTrue(meta["controls_complete"])
        self.assertFalse(meta["gates_passed"])
        self.assertEqual(meta["negative_controls"], [{"skill": "demo", "exit_code": 1}])
        with self.assertRaises(IntegrityError):
            accept_baseline(report, self.root / "baseline.json")

    def test_run_archives_inputs_and_scores_a_completed_deterministic_failure(self):
        runs = self.root / "runs"
        invocations = []

        def execute(command, **kwargs):
            invocations.append(command)
            if "evalkit.run_eval" in command:
                write_json(Path(command[command.index("--out") + 1]),
                           [{"case": c["id"], "deterministic": {"all_passed": False}}
                            for c in self.cases])
                return 1
            self.fake_scoring(command, **kwargs)
            return 1

        with patch.dict(os.environ, {"SKILLS_DIR": str(self.root)}), \
                patch.object(cli, "RUNS", runs), \
                patch.object(doctor, "check", return_value=[]), \
                patch.object(cli, "execute", side_effect=execute):
            self.assertEqual(cli.main(["run", "--skill", "demo"]), 1)
        archive = next(runs.iterdir())
        config = json.loads((archive / "run.json").read_text())
        self.assertEqual(config["status"], "failed")
        self.assertEqual(config["cases_sha256"], digest(archive / "cases.json"))
        self.assertEqual(config["results_sha256"], digest(archive / "results.json"))
        self.assertEqual(config["skills"], ["demo"])
        self.assertEqual(len(invocations), 2)
        self.assertIn("evalkit.run_eval", invocations[0])
        self.assertIn("evalkit.run_strands_eval", invocations[1])

    def score(self, rows, sdk_rows=True):
        from evalkit import run_strands_eval as scorer
        write_json(self.results, rows)
        rep = SimpleNamespace(
            cases=[{"evaluator": "fixture"}] if sdk_rows else [],
            detailed_results=[[]] if sdk_rows else [], scores=[0.9] if sdk_rows else [],
            test_passes=[True] if sdk_rows else [], reasons=["ok"] if sdk_rows else [],
            overall_score=0.9)

        class FakeExperiment:
            def __init__(self, **kwargs):
                pass

            async def run_evaluations_async(self, **kwargs):
                return rep

        def build(entry, *_):
            return SimpleNamespace(name=entry["spec"]["id"]), {
                "metadata": {"want_skill": "demo"}, "prepared": SimpleNamespace(applicable=False, extra=[]),
                "plugin": SimpleNamespace(name="fixture"), "trajectory": [], "output": "done",
            }

        with patch.object(scorer, "Experiment", FakeExperiment), \
                patch.object(scorer, "build_case", side_effect=build) as builder, \
                patch.object(scorer, "evaluators", return_value=[]), \
                patch.object(scorer, "rubric_hash", return_value="abc"), \
                patch("sys.argv", ["score", "--results", str(self.results),
                                   "--cases", str(self.dataset), "--out", str(self.report),
                                   "--no-visual", "--no-baseline"]):
            return scorer.main(), builder

    def test_scorer_rejects_missing_and_duplicate_records_before_judging(self):
        for rows in ([{"case": "a"}], [{"case": "a"}, {"case": "a"}, {"case": "b"}]):
            with self.subTest(rows=rows):
                rc, builder = self.score(rows)
                self.assertEqual(rc, 2)
                builder.assert_not_called()
                self.assertFalse(json.loads(metadata_path(self.report).read_text())["complete"])

    def test_scorer_writes_completion_and_model_provenance(self):
        rows = [{"case": c["id"], "status": "ok", "deterministic": {"all_passed": True},
                 "model_provider": "openai", "model_id": "agent-x"} for c in self.cases]
        rc, _ = self.score(rows)
        self.assertEqual(rc, 0)
        meta = json.loads(metadata_path(self.report).read_text())
        self.assertTrue(meta["complete"])
        self.assertTrue(meta["gates_passed"])
        self.assertEqual(meta["requested_cases"], ["a", "b"])
        self.assertEqual(meta["report_sha256"], digest(self.report))
        report = json.loads(self.report.read_text())
        self.assertEqual(report[0]["agent_provider"], "openai")
        self.assertEqual(report[0]["agent_model"], "agent-x")

    def test_scorer_cannot_hide_phase1_failure_or_empty_sdk_results(self):
        rows = [{"case": c["id"], "status": "ok", "deterministic": {"all_passed": True}}
                for c in self.cases]
        rows[1]["deterministic"]["all_passed"] = False
        rc, _ = self.score(rows)
        self.assertEqual(rc, 1)
        self.assertFalse(json.loads(metadata_path(self.report).read_text())["gates_passed"])
        rows[1]["deterministic"]["all_passed"] = True
        rc, _ = self.score(rows, sdk_rows=False)
        self.assertEqual(rc, 1)

    def test_removed_baseline_switch_fails_before_building_models(self):
        from evalkit import run_strands_eval as scorer
        with patch("sys.argv", ["score", "--update-baseline"]), \
                patch.object(scorer, "judge_model", side_effect=AssertionError("must not call")):
            with self.assertRaises(SystemExit) as exc:
                scorer.main()
        self.assertEqual(exc.exception.code, 2)

    def test_profiles_cannot_be_compared_silently(self):
        from evalkit.run_strands_eval import compare_baseline
        base = self.root / "baseline.json"
        write_json(base, {"cases": {"a": {"overall_score": 0.9, "scoring_profile": "full"}}})
        self.assertEqual(compare_baseline([{"case": "a", "overall_score": 1,
                                            "scoring_profile": "core"}], str(base), 0.05), ["a"])


if __name__ == "__main__":
    unittest.main()
