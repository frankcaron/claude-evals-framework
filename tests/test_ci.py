"""Tests for push-triggered eval CI (evals/ci.py), using throwaway Git repositories.

Each test commits eval data the way an upstream pipeline would, then grades the
push the way .github/workflows/eval-data.yml does. Nothing touches the network.
"""

import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from evals import ci

ROOT = Path(__file__).resolve().parents[1]
CASES = (ROOT / "examples/cases.jsonl").read_text(encoding="utf-8")
GOOD = (ROOT / "examples/responses.jsonl").read_text(encoding="utf-8")
WORSE = (ROOT / "examples/candidate-responses.jsonl").read_text(encoding="utf-8")


class PushTests(unittest.TestCase):
    """Which suites a push grades, how it compares, and when the gate fails."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.repo = Path(self.directory.name) / "repo"
        self.data = self.repo / "eval-data"
        self.data.mkdir(parents=True)
        self.git("init", "-q", "-b", "main")

    def tearDown(self):
        self.directory.cleanup()

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), "-c", "user.name=test",
                               "-c", "user.email=test@example.com", *args],
                              capture_output=True, text=True, check=True).stdout.strip()

    def push(self, files):
        """Commit files (path -> text) and return the new commit, like one pipeline push."""
        for name, text in files.items():
            (self.repo / name).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / name).write_text(text, encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "push")
        return self.git("rev-parse", "HEAD")

    def grade(self, before, *extra):
        out = Path(self.directory.name) / f"results-{len(list(Path(self.directory.name).iterdir()))}"
        summary = Path(self.directory.name) / "summary.md"
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
            code = ci.main(["--data-dir", str(self.data), "--before", before or "",
                            "--output-dir", str(out), "--summary", str(summary), *extra])
        return code, stdout.getvalue()

    def test_regressing_push_fails_the_gate(self):
        """Push: answers that regress against the previous commit fail (exit 1)."""
        before = self.push({"eval-data/agent/cases.jsonl": CASES, "eval-data/agent/responses.jsonl": GOOD})
        self.push({"eval-data/agent/responses.jsonl": WORSE})
        code, output = self.grade(before)
        self.assertEqual(code, 1)
        self.assertIn("regressions against the previous commit", output)
        self.assertIn("2: grounded-policy, injected-context", output)

    def test_gate_can_allow_regressions(self):
        """Gate: gate.json can lower the pass-rate floor and tolerate regressions."""
        before = self.push({"eval-data/agent/cases.jsonl": CASES, "eval-data/agent/responses.jsonl": GOOD,
                            "eval-data/agent/gate.json": '{"min_pass_rate": 0.5, "allow_regressions": true}'})
        self.push({"eval-data/agent/responses.jsonl": WORSE})
        code, output = self.grade(before)
        self.assertEqual(code, 0)
        self.assertIn("| `agent` | PASS | 2/4", output)

    def test_only_changed_suites_are_graded(self):
        """Selection: a push grades the suites it touched and nothing else."""
        before = self.push({"eval-data/a/cases.jsonl": CASES, "eval-data/a/responses.jsonl": GOOD,
                            "eval-data/b/cases.jsonl": CASES, "eval-data/b/responses.jsonl": GOOD})
        self.push({"eval-data/b/responses.jsonl": GOOD.replace("Paris", "Paris, France")})
        code, output = self.grade(before)
        self.assertEqual(code, 0)
        self.assertIn("`b`", output)
        self.assertNotIn("`a`", output)
        # Suite names may contain spaces.
        self.push({"eval-data/c d/cases.jsonl": CASES, "eval-data/c d/responses.jsonl": GOOD})
        self.assertIn("`c d`", self.grade(self.git("rev-parse", "HEAD~1"))[1])
        # --all grades every suite, e.g. for a manual run.
        self.assertIn("`a`", self.grade(before, "--all")[1])

    def test_new_suite_and_first_push(self):
        """Baseline: a new suite or an all-zero 'before' (new branch) has no previous version."""
        first = self.push({"eval-data/old/cases.jsonl": CASES, "eval-data/old/responses.jsonl": GOOD})
        for before in ("0" * 40, ""):
            with self.subTest(before=before):
                code, output = self.grade(before)
                self.assertEqual(code, 0)
                self.assertIn("no previous version", output)
        # A suite added in a later push is graded, but has no baseline to compare with.
        self.push({"eval-data/new/cases.jsonl": CASES, "eval-data/new/responses.jsonl": GOOD})
        code, output = self.grade(first)
        self.assertEqual(code, 0)
        self.assertIn("| `new` | PASS | 4/4 (100%, 95% CI 51%-100%) | 0 | no previous version |", output)
        self.assertNotIn("`old`", output)

    def test_invalid_data_is_not_a_score(self):
        """Validation: mismatched IDs or a malformed gate are invalid (exit 2), not a failure."""
        before = self.push({"eval-data/agent/cases.jsonl": CASES, "eval-data/agent/responses.jsonl": GOOD})
        after = self.push({"eval-data/agent/responses.jsonl": GOOD.splitlines()[0] + "\n"})
        code, output = self.grade(before)
        self.assertEqual(code, 2)
        self.assertIn("INVALID", output)
        self.push({"eval-data/agent/responses.jsonl": GOOD, "eval-data/agent/gate.json": '{"min_pass_rate": 2}'})
        self.assertEqual(self.grade(after)[0], 2)

    def test_summary_is_written_for_the_job_page(self):
        """Report: the Markdown summary is appended to --summary ($GITHUB_STEP_SUMMARY in Actions)."""
        self.push({"eval-data/agent/cases.jsonl": CASES, "eval-data/agent/responses.jsonl": GOOD})
        self.grade("")
        summary = (Path(self.directory.name) / "summary.md").read_text(encoding="utf-8")
        self.assertIn("| `agent` | PASS | 4/4 (100%, 95% CI 51%-100%) | 0 | no previous version |", summary)
        # Summaries show case IDs and fragments, never the pushed answer text.
        self.assertNotIn(json.loads(GOOD.splitlines()[1])["response"], summary)


if __name__ == "__main__":
    unittest.main()
