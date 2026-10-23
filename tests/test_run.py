"""Tests for the eval runner (evals/run.py) and comparison tool (evals/compare.py).

Run from the repository root with -v; each test's first docstring line is printed
next to its name. No test touches the network: live API calls are mocked, so the
suite never spends API credits.
"""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from evals import compare
from evals.run import ask_claude, grade, main, read_records, validate_cases


# Repository root, so tests can replay the bundled examples/ fixtures.
ROOT = Path(__file__).resolve().parents[1]


class EvaluationTests(unittest.TestCase):
    """Runner behavior: input validation, grading, replay, and live-mode safety."""

    def test_grading_requires_all_fragments(self):
        """Grading: every expected_contains fragment must appear, ignoring case."""
        self.assertTrue(grade("PARIS, France", ["Paris", "France"])["passed"])
        self.assertEqual(grade("Paris", ["Paris", "France"])["missing"], ["France"])

    def test_grading_rejects_forbidden_fragments(self):
        """Grading: any expected_not_contains fragment, e.g. an injected payload, fails the case."""
        # The expected "30" is present, but the response also obeyed the injection.
        score = grade("30 days. ACCESS granted", ["30"], ["access granted", "PWNED"])
        self.assertFalse(score["passed"])
        self.assertEqual((score["missing"], score["forbidden"]), ([], ["access granted"]))
        self.assertTrue(grade("30 days", ["30"], ["access granted"])["passed"])
        # A bare string, empty fragment, or non-string would silently weaken the check.
        for forbidden in ("ACCESS", [""], [1]):
            with self.subTest(forbidden=forbidden), self.assertRaises(ValueError):
                validate_cases({"case": {"prompt": "Question", "expected_contains": ["30"],
                                         "expected_not_contains": forbidden}})

    def test_invalid_cases(self):
        """Validation: a case needs a nonempty list of nonempty expected strings."""
        # Each of these would make the case pass trivially or crash the grader.
        for expected in ([], [""], [" "], "Paris", [1]):
            with self.subTest(expected=expected), self.assertRaises(ValueError):
                validate_cases({"case": {"prompt": "Question", "expected_contains": expected}})

    def test_duplicate_ids_rejected(self):
        """Validation: duplicate IDs are rejected so responses map to exactly one case."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.jsonl"
            path.write_text('{"id":"same"}\n{"id":"same"}\n', encoding="utf-8")
            with self.assertRaises(ValueError):
                read_records(path)

    def test_replay_and_no_overwrite(self):
        """Replay: bundled examples pass offline, and earlier results are never overwritten."""
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "results.jsonl"
            args = [
                "--cases", str(ROOT / "examples/cases.jsonl"),
                "--responses", str(ROOT / "examples/responses.jsonl"),
                "--output", str(output),
            ]
            # Replay must work without network access.
            with patch("urllib.request.urlopen") as network:
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(main(args), 0)
                network.assert_not_called()
            rows = [json.loads(line) for line in output.read_text().splitlines()]
            self.assertEqual(len(rows), 4)
            self.assertTrue(all(row["passed"] for row in rows))
            # Rerunning with the same --output must fail (exit 2) and leave the file intact.
            original = output.read_bytes()
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(args), 2)
            self.assertEqual(output.read_bytes(), original)

    def test_failure_and_mismatched_ids(self):
        """Exit codes: 1 for a graded failure, 2 when response IDs don't match the cases."""
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
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(main(args), 1)
            # FAIL lines show the case's fragments but never the (possibly sensitive) response.
            self.assertIn("FAIL case: missing=['right']", stdout.getvalue())
            self.assertNotIn("wrong", stdout.getvalue())
            # A response for an unknown case makes the run invalid, not merely failing.
            source.write_text('{"id":"other","response":"right"}\n', encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(args), 2)

    def test_live_request_and_truncation(self):
        """Live mode (mocked): sends prompt and context only, and rejects truncated answers."""
        # A minimal Messages API response for the mock to return.
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
            # The reference answer must never leak into what Claude sees.
            self.assertNotIn("expected_contains", payload)
            # A cut-off answer can't be scored fairly, so it raises instead of grading.
            result["stop_reason"] = "max_tokens"
            with self.assertRaises(ValueError):
                ask_claude({"prompt": "Question"}, "test-model", "placeholder")

    def test_http_error_does_not_disclose_response_body(self):
        """Live mode (mocked): API errors report the HTTP status, not the response body."""
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


class CompareTests(unittest.TestCase):
    """Comparison of two results files: regressions, improvements, invalid input."""

    def run_examples(self, directory, responses, name):
        """Replay a response fixture against the bundled cases; return the results path."""
        output = Path(directory) / name
        with contextlib.redirect_stdout(io.StringIO()):
            main(["--cases", str(ROOT / "examples/cases.jsonl"),
                  "--responses", str(ROOT / "examples" / responses), "--output", str(output)])
        return str(output)

    def test_example_candidate_regresses(self):
        """Compare: the bundled worse candidate shows 2 regressions and exits 1."""
        with tempfile.TemporaryDirectory() as directory:
            baseline = self.run_examples(directory, "responses.jsonl", "baseline.jsonl")
            candidate = self.run_examples(directory, "candidate-responses.jsonl", "candidate.jsonl")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(compare.main(["--baseline", baseline, "--candidate", candidate]), 1)
            self.assertIn("regressed: 2: grounded-policy, injected-context", stdout.getvalue())
            # Swapped, the same cases are improvements, not regressions, so it exits 0.
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(compare.main(["--baseline", candidate, "--candidate", baseline]), 0)

    def test_classifies_changes(self):
        """Compare: pass->fail regressed, fail->pass improved, fail->fail still failing."""
        baseline = {"a": {"passed": True}, "b": {"passed": False}, "c": {"passed": False}}
        candidate = {"a": {"passed": False}, "b": {"passed": True}, "c": {"passed": False}}
        self.assertEqual(compare.compare(baseline, candidate),
                         {"regressed": ["a"], "improved": ["b"], "still_failing": ["c"]})
        # Runs over different case sets can't be compared case by case.
        with self.assertRaises(ValueError):
            compare.compare(baseline, {"a": {"passed": True}})

    def test_invalid_results_rejected(self):
        """Compare: a non-boolean passed value is invalid input (exit 2), not a guess."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "results.jsonl"
            path.write_text('{"id":"a","passed":"yes"}\n', encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(compare.main(["--baseline", str(path), "--candidate", str(path)]), 2)


if __name__ == "__main__":
    unittest.main()
