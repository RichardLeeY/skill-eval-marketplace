"""Tool-budget regressions through the real Strands Agent, without model APIs."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from strands.models import Model

from evalkit import graders, run_eval, sandbox


class WritingModel(Model):
    """Emit a finite sequence of real Write tool requests, then a final reply."""

    def __init__(self, batches):
        self.batches = batches
        self.requests = 0

    def get_config(self):
        return {"model_id": "offline-budget-test"}

    def update_config(self, **kwargs):
        pass

    async def structured_output(self, *args, **kwargs):
        raise AssertionError("Unexpected structured_output")
        yield

    async def stream(self, messages, *args, **kwargs):
        self.requests += 1
        yield {"messageStart": {"role": "assistant"}}
        if self.requests <= len(self.batches):
            for number in self.batches[self.requests - 1]:
                yield {"contentBlockStart": {"start": {"toolUse": {
                    "toolUseId": f"write-{number}", "name": "Write"}}}}
                yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps({
                    "path": f"part-{number}.txt", "content": f"completed part {number}",
                })}}}}
                yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
        else:
            yield {"contentBlockStart": {"start": {}}}
            yield {"contentBlockDelta": {"delta": {"text": "Finished"}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "end_turn"}}
        yield {"metadata": {
            "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
            "metrics": {"latencyMs": 1},
        }}


class SandboxBudgetTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {
            "AWS_EC2_METADATA_DISABLED": "true", "OTEL_SDK_DISABLED": "true",
            "AWS_REGION": "us-east-1", "AGENT_OBSERVABILITY_ENABLED": "",
        }))
        self.stack.enter_context(patch(
            "socket.socket.connect", side_effect=AssertionError("Network forbidden")))
        self.stack.enter_context(patch(
            "socket.create_connection", side_effect=AssertionError("Network forbidden")))
        self.stack.enter_context(patch.object(sandbox, "RUN", sandbox.Run()))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.agents = []
        real_agent = sandbox.Agent

        def agent(**kwargs):
            obj = real_agent(**kwargs, callback_handler=None)
            self.agents.append(obj)
            return obj

        self.stack.enter_context(patch.object(sandbox, "Agent", side_effect=agent))

    def run_model(self, batches, budget=2):
        model = WritingModel(batches)
        with patch.object(sandbox, "MAX_TOOL_CALLS", budget), \
                patch.object(sandbox.model_config, "build_model", return_value=model):
            response = sandbox.run_case({"prompt": "Write the requested text files."})
        self.assertFalse(Path(sandbox.RUN.workspace).exists())
        return model, response

    def assert_tool_results_complete(self):
        messages = self.agents[-1].messages
        for index, message in enumerate(messages):
            ids = {block["toolUse"]["toolUseId"] for block in message["content"]
                   if "toolUse" in block}
            if ids:
                results = {block["toolResult"]["toolUseId"]
                           for block in messages[index + 1]["content"]
                           if "toolResult" in block}
                self.assertEqual(results, ids)

    def test_over_budget_stops_model_and_preserves_completed_artifacts(self):
        model, response = self.run_model([[1], [2], [3], [4], [5]])
        self.assertEqual(response["status"], "error")
        self.assertEqual(response["error"], "exceeded MAX_TOOL_CALLS=2 on call 3 (Write)")
        self.assertEqual(model.requests, 3)  # No request after the rejected tool.
        self.assertEqual(set(response["files"]), {"part-1.txt", "part-2.txt"})
        self.assertEqual(response["files"]["part-2.txt"]["text"], "completed part 2")
        self.assertEqual(response["trace"]["n_tool_calls"], 2)
        self.assertEqual(response["trace"]["stop_reason"], "tool_budget_exceeded")
        self.assertEqual(response["tool_budget"], {
            "limit": 2, "attempted": 3, "completed": 2, "exhausted": True,
        })
        self.assertFalse(graders.tool_budget(response, {"max_tool_calls": 25})["passed"])
        self.assert_tool_results_complete()

    def test_batch_over_budget_returns_every_tool_result_without_more_inference(self):
        model, response = self.run_model([[1, 2, 3, 4, 5]])
        self.assertEqual(response["status"], "error")
        self.assertEqual(response["error"], "exceeded MAX_TOOL_CALLS=2 on call 3 (Write)")
        self.assertEqual(model.requests, 1)
        self.assertEqual(len(response["files"]), 2)
        self.assertEqual(response["trace"]["n_tool_calls"], 2)
        self.assertEqual(response["tool_budget"]["attempted"], 5)
        self.assert_tool_results_complete()

    def test_exact_budget_allows_final_answer_and_next_run_resets_exhaustion(self):
        self.run_model([[1], [2], [3]])
        model, response = self.run_model([[1], [2]])
        self.assertEqual(response["status"], "ok")
        self.assertIsNone(response["error"])
        self.assertEqual(model.requests, 3)
        self.assertIn("Finished", response["output"])
        self.assertEqual(response["tool_budget"], {
            "limit": 2, "attempted": 2, "completed": 2, "exhausted": False,
        })

    def test_zero_budget_does_not_execute_first_tool(self):
        model, response = self.run_model([[1], [2]], budget=0)
        self.assertEqual(response["status"], "error")
        self.assertEqual(response["error"], "exceeded MAX_TOOL_CALLS=0 on call 1 (Write)")
        self.assertEqual(model.requests, 1)
        self.assertEqual(response["files"], {})
        self.assertEqual(response["trace"]["n_tool_calls"], 0)
        self.assert_tool_results_complete()

    def test_runner_persists_budget_failure_and_partial_artifacts(self):
        dataset = self.root / "cases.json"
        dataset.write_text(json.dumps([{
            "id": "budget", "prompt": "Write five text files.",
            "expect": {"file_glob": "*.txt", "max_tool_calls": 25},
        }]))
        results = self.root / "results.json"
        model = WritingModel([[1], [2], [3], [4], [5]])
        with patch.object(sandbox, "MAX_TOOL_CALLS", 2), \
                patch.object(sandbox.model_config, "build_model", return_value=model), \
                patch("sys.argv", ["run_eval", "--cases", str(dataset),
                                   "--out", str(results),
                                   "--artifacts", str(self.root / "artifacts")]):
            self.assertEqual(run_eval.main(), 1)
        self.assertEqual(model.requests, 3)
        row = json.loads(results.read_text())[0]
        self.assertEqual(row["status"], "error")
        self.assertEqual(row["tool_budget"]["attempted"], 3)
        self.assertTrue(row["tool_budget"]["exhausted"])
        budget_grade = next(g for g in row["deterministic"]["grades"] if g["name"] == "tool_budget")
        self.assertFalse(budget_grade["passed"])
        self.assertEqual(set(row["files"]), {"part-1.txt", "part-2.txt"})
        for meta in row["files"].values():
            self.assertTrue(Path(meta["saved_to"]).is_file())
