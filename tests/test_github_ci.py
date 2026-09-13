"""Offline GitHub Actions routing checks; no AWS credentials or model calls."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from evalkit import github_ci, gitlab_ci


class GitHubCITests(unittest.TestCase):
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
        return dest

    IDENTITY = ("-c", "user.name=CI test", "-c", "user.email=ci@example.invalid",
                "-c", "commit.gpgsign=false")

    def git(self, *args):
        # Every commit-creating command carries its own identity: CI runners have none.
        return subprocess.check_output(["git", *self.IDENTITY, *args], cwd=self.root,
                                       stderr=subprocess.PIPE, text=True).strip()

    def commit(self):
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        return self.git("rev-parse", "HEAD")

    def pr_env(self, base_sha, head_sha, number=7):
        payload = self.write("event.json", json.dumps({"pull_request": {
            "number": number, "base": {"sha": base_sha}, "head": {"sha": head_sha}}}))
        return {"GITHUB_EVENT_NAME": "pull_request", "GITHUB_EVENT_PATH": str(payload),
                "GITHUB_REPOSITORY": "owner/repo", "GITHUB_RUN_ID": "42"}

    def test_pull_request_diffs_from_merge_base_to_pr_head(self):
        self.git("init", "-q", "-b", "main")
        base = self.commit()
        # The target branch moves on after the PR forks: that change must not select beta.
        self.git("checkout", "-q", "-b", "feature")
        self.write("skills/alpha/references/note.md", "pr change")
        head = self.commit()
        self.git("checkout", "-q", "main")
        self.write("skills/beta/SKILL.md", "# beta moved on\n")
        main_tip = self.commit()
        self.git("merge", "-q", "--no-edit", "--no-ff", "feature")
        result = github_ci.plan(self.root, self.pr_env(main_tip, head))
        self.assertEqual(result["skills"], ["alpha"])
        self.assertEqual(result["base_sha"], base)
        self.assertEqual(result["head_sha"], head)
        self.assertEqual(result["pull_request"], 7)
        self.assertEqual(result["merge_request"], 7)
        self.assertEqual(result["source"], "pull_request")

    def test_pull_request_covers_all_commits_and_both_sides_of_rename(self):
        self.git("init", "-q", "-b", "main")
        base = self.commit()
        self.write("skills/alpha/references/space and\nnewline.md", "first commit")
        self.commit()
        (self.root / "skills/alpha/SKILL.md").rename(self.root / "skills/beta/moved.md")
        head = self.commit()
        result = github_ci.plan(self.root, self.pr_env(base, head))
        self.assertEqual(result["skills"], ["alpha", "beta"])
        self.assertIn("skills/alpha/references/space and\nnewline.md", result["changed_paths"])
        self.assertIn("skills/alpha/SKILL.md", result["changed_paths"])
        self.assertIn("skills/beta/moved.md", result["changed_paths"])

    def test_workflow_dispatch_push_and_schedule_run_all(self):
        for event in ("workflow_dispatch", "push", "schedule"):
            with self.subTest(event=event), patch.object(
                    github_ci.subprocess, "check_output", return_value="a" * 40):
                result = github_ci.plan(self.root, {"GITHUB_EVENT_NAME": event})
            self.assertEqual(result["skills"], ["alpha", "beta"])
            self.assertEqual(result["mode"], "full")
            self.assertEqual(result["source"], event)

    def test_wrong_event_or_missing_payload_fails(self):
        with patch.object(github_ci.subprocess, "check_output", return_value="a" * 40):
            for env in ({"GITHUB_EVENT_NAME": "release"},
                        {},
                        {"GITHUB_EVENT_NAME": "pull_request"},
                        {"GITHUB_EVENT_NAME": "pull_request",
                         "GITHUB_EVENT_PATH": str(self.write("event.json", "{}"))}):
                with self.subTest(env=env), self.assertRaises(ValueError):
                    github_ci.plan(self.root, env)

    def test_unavailable_base_commit_fails_instead_of_evaluating_an_empty_diff(self):
        self.git("init", "-q", "-b", "main")
        head = self.commit()
        with self.assertRaises(subprocess.CalledProcessError):
            github_ci.plan(self.root, self.pr_env("b" * 40, head))

    def test_no_credentials_fails_before_setup(self):
        selection = gitlab_ci.select_skills(self.root, ["skills/alpha/SKILL.md"])
        with patch.object(gitlab_ci.subprocess, "run") as run, self.assertRaisesRegex(
                ValueError, "AWS_ACCESS_KEY_ID"):
            gitlab_ci.run_selected(self.root, selection, {"AWS_CREDS_TARGET_ROLE": "gitlab-only"},
                                   credential_vars=github_ci.CREDENTIAL_VARS)
        run.assert_not_called()

    def test_plan_only_writes_selection_and_job_outputs(self):
        output = self.root / "github_output"
        env = {"GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_OUTPUT": str(output)}
        with patch.object(github_ci, "ROOT", self.root), patch.dict(os.environ, env, clear=True), \
                patch.object(github_ci.subprocess, "check_output", return_value="a" * 40), \
                patch.object(gitlab_ci, "run_selected") as run, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(github_ci.main(["--plan-only"]), 0)
            run.assert_not_called()
        selection = json.loads((self.root / ".eval/selection.json").read_text())
        self.assertEqual(selection["skills"], ["alpha", "beta"])
        self.assertTrue(selection["plan_only"])
        self.assertIn("eval_required=true", output.read_text())
        self.assertIn('skills=["alpha", "beta"]', output.read_text())

    def test_documentation_only_marks_eval_not_required(self):
        self.git("init", "-q", "-b", "main")
        base = self.commit()
        self.write("docs/example.md", "docs only")
        head = self.commit()
        output = self.root / "github_output"
        env = {**self.pr_env(base, head), "GITHUB_OUTPUT": str(output)}
        with patch.object(github_ci, "ROOT", self.root), patch.dict(os.environ, env, clear=True), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(github_ci.main(["--plan-only"]), 0)
        self.assertIn("eval_required=false", output.read_text())

    def test_selection_failure_is_archived_for_dashboard(self):
        env = {"GITHUB_EVENT_NAME": "release"}
        with patch.object(github_ci, "ROOT", self.root), patch.dict(os.environ, env, clear=True), \
                patch.object(github_ci.subprocess, "check_output", return_value="a" * 40), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(github_ci.main([]), 2)
        selection = json.loads((self.root / ".eval/selection.json").read_text())
        self.assertIn("Expected", selection["error"])
        self.assertEqual(selection["exit_code"], 2)


if __name__ == "__main__":
    unittest.main()
