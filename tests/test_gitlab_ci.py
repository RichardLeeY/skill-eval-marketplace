"""Offline CI routing checks; no AWS credentials or model calls."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from evalkit import gitlab_ci


class GitLabCITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for skill in ("alpha", "beta"):
            self.write(f"skills/{skill}/eval/dataset.jsonl", "[]")
            self.write(f"skills/{skill}/SKILL.md", f"# {skill}\n")

    def write(self, path, content):
        dest = self.root / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content)

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.root,
                                       stderr=subprocess.PIPE, text=True).strip()

    def commit(self):
        self.git("add", ".")
        self.git("-c", "user.name=CI test", "-c", "user.email=ci@example.invalid",
                 "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
        return self.git("rev-parse", "HEAD")

    def test_one_skill_and_multiple_skills(self):
        self.assertEqual(gitlab_ci.select_skills(
            self.root, ["skills/alpha/SKILL.md", "README.md"])["skills"], ["alpha"])
        self.assertEqual(gitlab_ci.select_skills(
            self.root, ["skills/alpha/scripts/a.py", "skills/beta/eval/reference/file"])["skills"],
                         ["alpha", "beta"])

    def test_shared_changes_force_all(self):
        for path in [*gitlab_ci.SHARED_FILES, "evalkit/new_module.py", "tests/test_new.py",
                     "skills/README.md"]:
            with self.subTest(path=path):
                result = gitlab_ci.select_skills(self.root, [path])
                self.assertEqual(result["skills"], ["alpha", "beta"])
                self.assertEqual(result["mode"], "full")

    def test_documentation_only_selects_none(self):
        result = gitlab_ci.select_skills(self.root, ["docs/example.md", "README.zh-CN.md"])
        self.assertEqual(result["skills"], [])

    def test_new_skill_is_discovered_without_ci_registration(self):
        self.write("skills/gamma/eval/dataset.jsonl", "[]")
        self.assertEqual(gitlab_ci.select_skills(
            self.root, ["skills/gamma/eval/dataset.jsonl"])["skills"], ["gamma"])

    def test_missing_or_removed_dataset_is_not_a_silent_skip(self):
        (self.root / "skills/alpha/eval/dataset.jsonl").unlink()
        result = gitlab_ci.select_skills(self.root, ["skills/alpha/eval/dataset.jsonl"])
        self.assertEqual(result["missing_datasets"], ["alpha"])
        with self.assertRaisesRegex(ValueError, "alpha"):
            gitlab_ci.run_selected(self.root, result, {})

    def test_removed_skill_does_not_run_unrelated_skills(self):
        result = gitlab_ci.select_skills(self.root, ["skills/removed/SKILL.md"])
        self.assertEqual(result["removed_skills"], ["removed"])
        self.assertEqual(result["skills"], [])
        with patch.object(gitlab_ci.subprocess, "run") as run, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(gitlab_ci.run_selected(self.root, result, {}), 0)
            run.assert_not_called()

    def test_mr_diff_covers_all_commits_and_both_sides_of_rename(self):
        self.git("init", "-q")
        base = self.commit()
        self.write("skills/alpha/references/space and\nnewline.md", "first commit")
        self.commit()
        (self.root / "skills/alpha/SKILL.md").rename(self.root / "skills/beta/moved.md")
        self.commit()
        result = gitlab_ci.plan(self.root, {
            "CI_PIPELINE_SOURCE": "merge_request_event", "CI_MERGE_REQUEST_DIFF_BASE_SHA": base})
        self.assertEqual(result["skills"], ["alpha", "beta"])
        self.assertIn("skills/alpha/references/space and\nnewline.md", result["changed_paths"])
        self.assertIn("skills/alpha/SKILL.md", result["changed_paths"])
        self.assertIn("skills/beta/moved.md", result["changed_paths"])

    def test_missing_diff_base_or_wrong_pipeline_source_fails(self):
        with patch.object(gitlab_ci.subprocess, "check_output", return_value="a" * 40):
            for env in ({"CI_PIPELINE_SOURCE": "merge_request_event"},
                        {"CI_PIPELINE_SOURCE": "push"}):
                with self.subTest(env=env), self.assertRaises(ValueError):
                    gitlab_ci.plan(self.root, env)

    def test_unavailable_base_commit_fails_instead_of_evaluating_an_empty_diff(self):
        self.git("init", "-q")
        self.commit()
        with self.assertRaises(subprocess.CalledProcessError):
            gitlab_ci.plan(self.root, {"CI_PIPELINE_SOURCE": "merge_request_event",
                                      "CI_MERGE_REQUEST_DIFF_BASE_SHA": "b" * 40})

    def test_manual_pipeline_runs_all(self):
        with patch.object(gitlab_ci.subprocess, "check_output", return_value="a" * 40):
            result = gitlab_ci.plan(self.root, {"CI_PIPELINE_SOURCE": "web"})
        self.assertEqual(result["skills"], ["alpha", "beta"])
        self.assertEqual(result["mode"], "full")

    def test_no_credentials_fails_before_setup(self):
        result = gitlab_ci.select_skills(self.root, ["skills/alpha/SKILL.md"])
        with patch.object(gitlab_ci.subprocess, "run") as run, self.assertRaisesRegex(
                ValueError, "AWS_CREDS_TARGET_ROLE"):
            gitlab_ci.run_selected(self.root, result, {})
        run.assert_not_called()

    def test_setup_and_run_share_selection_and_propagate_failures(self):
        result = gitlab_ci.select_skills(self.root, ["skills/beta/SKILL.md"])
        for codes in ([2], [0, 1], [0, 0]):
            with self.subTest(codes=codes), contextlib.redirect_stdout(io.StringIO()), \
                    patch.object(gitlab_ci.subprocess, "run",
                                 side_effect=[SimpleNamespace(returncode=c) for c in codes]) as run:
                rc = gitlab_ci.run_selected(self.root, result, {"AWS_CREDS_TARGET_ROLE": "role"})
                self.assertEqual(rc, codes[-1])
                for call in run.call_args_list:
                    self.assertEqual(call.args[0][-2:], ["--skill", "beta"])
                self.assertEqual(run.call_count, len(codes))

    def test_selection_failure_is_archived_for_dashboard(self):
        env = {"CI_PIPELINE_SOURCE": "push"}
        with patch.object(gitlab_ci, "ROOT", self.root), patch.dict(os.environ, env), \
                patch.object(gitlab_ci.subprocess, "check_output", return_value="a" * 40), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(gitlab_ci.main([]), 2)
        selection = json.loads((self.root / ".eval/selection.json").read_text())
        self.assertIn("Expected", selection["error"])
        self.assertEqual(selection["exit_code"], 2)

    def test_plan_only_needs_no_credentials_or_setup(self):
        with patch.object(gitlab_ci, "ROOT", self.root), \
                patch.dict(os.environ, {"CI_PIPELINE_SOURCE": "web"}, clear=True), \
                patch.object(gitlab_ci.subprocess, "check_output", return_value="a" * 40), \
                patch.object(gitlab_ci, "run_selected") as run, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(gitlab_ci.main(["--plan-only"]), 0)
            run.assert_not_called()
        selection = json.loads((self.root / ".eval/selection.json").read_text())
        self.assertEqual(selection["skills"], ["alpha", "beta"])
        self.assertTrue(selection["plan_only"])

    def test_visual_selection_installs_missing_ffmpeg_before_run(self):
        self.write("skills/visual-flow-webp/eval/dataset.jsonl", "[]")
        selection = gitlab_ci.select_skills(self.root, ["skills/visual-flow-webp/SKILL.md"])
        with patch.object(gitlab_ci.shutil, "which", return_value=None), \
                patch.object(gitlab_ci.subprocess, "run",
                             return_value=SimpleNamespace(returncode=0)) as run, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(gitlab_ci.run_selected(
                self.root, selection, {"AWS_CREDS_TARGET_ROLE": "role"}), 0)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[1:3], [["apt-get", "update", "-qq"],
                                       ["apt-get", "install", "-y", "-qq", "ffmpeg"]])
        self.assertEqual(commands[-1][-2:], ["--skill", "visual-flow-webp"])


if __name__ == "__main__":
    unittest.main()
