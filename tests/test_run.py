import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from evals.run import ask_claude, grade, main, read_records, validate_cases


ROOT = Path(__file__).resolve().parents[1]


class EvaluationTests(unittest.TestCase):
    def test_grading_requires_all_fragments(self):
        self.assertTrue(grade("PARIS, France", ["Paris", "France"])["passed"])
        self.assertEqual(grade("Paris", ["Paris", "France"])["missing"], ["France"])

    def test_invalid_cases(self):
        for expected in ([], [""], [" "], "Paris", [1]):
            with self.subTest(expected=expected), self.assertRaises(ValueError):
                validate_cases({"case": {"prompt": "Question", "expected_contains": expected}})

    def test_duplicate_ids_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.jsonl"
            path.write_text('{"id":"same"}\n{"id":"same"}\n', encoding="utf-8")
            with self.assertRaises(ValueError):
                read_records(path)

    def test_replay_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "results.jsonl"
            args = [
                "--cases", str(ROOT / "examples/cases.jsonl"),
                "--responses", str(ROOT / "examples/responses.jsonl"),
                "--output", str(output),
            ]
            with patch("urllib.request.urlopen") as network:
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(main(args), 0)
                network.assert_not_called()
            rows = [json.loads(line) for line in output.read_text().splitlines()]
            self.assertEqual(len(rows), 3)
            self.assertTrue(all(row["passed"] for row in rows))
            original = output.read_bytes()
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(args), 2)
            self.assertEqual(output.read_bytes(), original)

    def test_failure_and_mismatched_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "responses.jsonl"
            source.write_text('{"id":"case","response":"wrong"}\n', encoding="utf-8")
            cases = Path(directory) / "cases.jsonl"
            cases.write_text(
                '{"id":"case","prompt":"Question","expected_contains":["right"]}\n',
                encoding="utf-8",
            )
            args = ["--cases", str(cases), "--responses", str(source), "--output",
                    str(Path(directory) / "results.jsonl")]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(args), 1)
            source.write_text('{"id":"other","response":"right"}\n', encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(args), 2)

    def test_live_request_and_truncation(self):
        result = {
            "model": "test-model", "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "Paris"}],
            "usage": {"input_tokens": 10, "output_tokens": 1},
        }
        with patch("urllib.request.urlopen") as network:
            network.return_value.__enter__.side_effect = lambda: io.StringIO(json.dumps(result))
            text, model, usage = ask_claude(
                {"prompt": "Question", "context": "Evidence"}, "test-model", "placeholder"
            )
            self.assertEqual((text, model, usage), ("Paris", "test-model", result["usage"]))
            request = network.call_args.args[0]
            payload = json.loads(request.data)
            self.assertIn("Evidence", payload["messages"][0]["content"])
            self.assertNotIn("expected_contains", payload)
            result["stop_reason"] = "max_tokens"
            with self.assertRaises(ValueError):
                ask_claude({"prompt": "Question"}, "test-model", "placeholder")

    def test_http_error_does_not_disclose_response_body(self):
        with tempfile.TemporaryDirectory() as directory:
            error = urllib.error.HTTPError(
                "https://api.anthropic.com/v1/messages", 401, "Unauthorized", {},
                io.BytesIO(b"sensitive upstream body"),
            )
            args = ["--cases", str(ROOT / "examples/cases.jsonl"), "--live",
                    "--model", "test-model", "--output", str(Path(directory) / "result.jsonl")]
            stderr = io.StringIO()
            with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "placeholder"}):
                with patch("urllib.request.urlopen", side_effect=error):
                    with contextlib.redirect_stderr(stderr):
                        self.assertEqual(main(args), 2)
            self.assertIn("HTTP 401", stderr.getvalue())
            self.assertNotIn("sensitive", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
