"""Grade eval data that a push changed, compare it with the previous commit, and gate.

Each subdirectory of --data-dir is a suite holding cases.jsonl and
responses.jsonl, usually committed by an upstream pipeline. Optional files:
gate.json ({"min_pass_rate": 0-1, "allow_regressions": bool}) and
rubric.json plus grades.jsonl for a rubric summary.

For each changed suite this runs run.py on the pushed files, replays the same
suite from --before with the same grader code, and runs compare.py on the
two. Exit codes: 0 all gates pass, 1 a gate failed, 2 invalid data or Git state.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

try:
    from evals.run import format_rate
except ImportError:  # Run as a script: evals/ is on sys.path instead.
    from run import format_rate

EVALS = Path(__file__).resolve().parent
INPUTS = ("cases.jsonl", "responses.jsonl")


def git(directory, *args):
    return subprocess.run(["git", "-C", str(directory), *args],
                          capture_output=True, text=True, check=True).stdout


def usable_commit(directory, rev):
    """False for a missing, all-zero (new branch), or unfetched --before commit."""
    if not rev or set(rev) == {"0"}:
        return False
    try:
        git(directory, "cat-file", "-e", f"{rev}^{{commit}}")
    except subprocess.CalledProcessError:
        return False
    return True


def find_suites(data_dir):
    return sorted(path.name for path in data_dir.iterdir()
                  if path.is_dir() and all((path / name).is_file() for name in INPUTS))


def changed_suites(data_dir, before, after):
    """Suites whose files changed between the commits, or None to mean all suites."""
    if not usable_commit(data_dir, before):
        return None
    top = Path(git(data_dir, "rev-parse", "--show-toplevel").strip())
    prefix = data_dir.resolve().relative_to(top)
    # Run from the top level: pathspecs are relative to git's working directory.
    names = git(top, "diff", "--name-only", before, after, "--", str(prefix)).splitlines()
    return sorted({Path(name).relative_to(prefix).parts[0] for name in names
                   if len(Path(name).relative_to(prefix).parts) > 1})


def show_file(data_dir, rev, path):
    """A file's content at an earlier commit, or None if it did not exist."""
    top = Path(git(data_dir, "rev-parse", "--show-toplevel").strip())
    try:
        return git(data_dir, "show", f"{rev}:{path.resolve().relative_to(top).as_posix()}")
    except subprocess.CalledProcessError:
        return None


def tool(script, *args):
    run = subprocess.run([sys.executable, str(EVALS / script), *map(str, args)],
                         capture_output=True, text=True)
    return run.returncode, (run.stdout + run.stderr).strip()


def load_gate(suite_dir):
    gate = {"min_pass_rate": 1.0, "allow_regressions": False}
    path = suite_dir / "gate.json"
    if path.is_file():
        gate.update(json.loads(path.read_text(encoding="utf-8")))
    rate = gate["min_pass_rate"]
    if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not 0 <= rate <= 1:
        raise ValueError(f"{path}: min_pass_rate must be a number from 0 to 1")
    if not isinstance(gate["allow_regressions"], bool):
        raise ValueError(f"{path}: allow_regressions must be true or false")
    return gate


def evaluate_suite(data_dir, suite, before, output_dir):
    suite_dir, out = data_dir / suite, output_dir / suite
    report = {"suite": suite, "logs": [], "status": "pass", "reasons": []}
    try:
        gate = load_gate(suite_dir)
    except (OSError, ValueError) as error:
        return {**report, "status": "invalid", "reasons": [str(error)]}
    current = out / "current.jsonl"
    code, log = tool("run.py", "--cases", suite_dir / "cases.jsonl",
                     "--responses", suite_dir / "responses.jsonl", "--output", current)
    report["logs"].append(("grade", log))
    if code == 2:
        return {**report, "status": "invalid", "reasons": ["run.py rejected the pushed files"]}
    manifest = json.loads(current.with_suffix(".manifest.json").read_text(encoding="utf-8"))
    report.update(passed=manifest["passed"], total=manifest["total"],
                  workflow_errors=len(manifest["workflow_errors"]))
    if manifest["passed"] / manifest["total"] < gate["min_pass_rate"]:
        report["reasons"].append(f"pass rate below min_pass_rate={gate['min_pass_rate']}")

    old = {name: show_file(data_dir, before, suite_dir / name) for name in INPUTS} \
        if usable_commit(data_dir, before) else {}
    if old and all(content is not None for content in old.values()):
        (out / "previous").mkdir(parents=True, exist_ok=True)
        for name, content in old.items():
            (out / "previous" / name).write_text(content, encoding="utf-8")
        baseline = out / "previous.jsonl"
        code, log = tool("run.py", "--cases", out / "previous/cases.jsonl",
                         "--responses", out / "previous/responses.jsonl", "--output", baseline)
        report["logs"].append(("grade previous commit", log))
        if code != 2:
            code, log = tool("compare.py", "--baseline", baseline, "--candidate", current)
            report["logs"].append(("compare with previous commit", log))
            regressed = next((line for line in log.splitlines() if line.startswith("regressed:")), "")
            report["regressions"] = regressed.removeprefix("regressed: ") or "n/a"
            if code == 1 and not gate["allow_regressions"]:
                report["reasons"].append("regressions against the previous commit")
            elif code == 2:
                report["regressions"] = "not comparable"
        else:
            report["regressions"] = "previous data invalid"
    else:
        report["regressions"] = "no previous version"

    if (suite_dir / "rubric.json").is_file() and (suite_dir / "grades.jsonl").is_file():
        code, log = tool("rubric.py", "summarize", "--rubric", suite_dir / "rubric.json",
                         "--grades", suite_dir / "grades.jsonl", "--cases", suite_dir / "cases.jsonl")
        report["logs"].append(("rubric (informational)", log))
        if code == 2:
            report["reasons"].append("rubric grades are invalid")
    if report["reasons"]:
        report["status"] = "fail"
    return report


def markdown(reports, note):
    lines = ["## Eval data results", "", note, "",
             "| Suite | Status | Pass rate | Workflow errors | Regressions vs previous commit |",
             "| --- | --- | --- | --- | --- |"]
    for report in reports:
        rate = format_rate(report["passed"], report["total"]) if "total" in report else "n/a"
        status = report["status"].upper() + (f": {'; '.join(report['reasons'])}" if report["reasons"] else "")
        lines.append(f"| `{report['suite']}` | {status} | {rate} | "
                     f"{report.get('workflow_errors', 'n/a')} | {report.get('regressions', 'n/a')} |")
    for report in reports:
        for title, log in report["logs"]:
            lines += ["", f"<details><summary>{report['suite']}: {title}</summary>", "",
                      "```text", log, "```", "", "</details>"]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", required=True, help="Directory of suite folders")
    parser.add_argument("--before", help="Commit before the push; omit to grade every suite")
    parser.add_argument("--after", default="HEAD", help="Commit being evaluated")
    parser.add_argument("--all", action="store_true", help="Grade every suite, changed or not")
    parser.add_argument("--output-dir", required=True, help="New directory for results")
    parser.add_argument("--summary", default=os.environ.get("GITHUB_STEP_SUMMARY"),
                        help="Append a Markdown summary here (defaults to the GitHub job summary)")
    args = parser.parse_args(argv)
    data_dir, output_dir = Path(args.data_dir), Path(args.output_dir)
    try:
        if output_dir.exists():
            raise ValueError(f"{output_dir} already exists; choose a new --output-dir")
        suites = find_suites(data_dir)
        changed = None if args.all else changed_suites(data_dir, args.before, args.after)
        selected = suites if changed is None else [suite for suite in suites if suite in changed]
        reports = [evaluate_suite(data_dir, suite, args.before, output_dir) for suite in selected]
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    if changed is None:
        note = "Graded every suite (no usable previous commit, or --all)."
    else:
        note = f"Graded suites changed since `{args.before[:12]}`: {', '.join(selected) or 'none'}."
    summary = markdown(reports, note)
    print(summary)
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as destination:
            destination.write(summary)
    for report in reports:
        if report["status"] != "pass" and os.environ.get("GITHUB_ACTIONS") == "true":
            print(f"::error title=eval suite {report['suite']}::{'; '.join(report['reasons'])}")
    statuses = {report["status"] for report in reports}
    return 2 if "invalid" in statuses else 1 if "fail" in statuses else 0


if __name__ == "__main__":
    sys.exit(main())
