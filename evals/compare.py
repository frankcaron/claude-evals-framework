"""Compare two results files from run.py case by case."""

import argparse
import sys

try:
    from evals.run import read_records
except ImportError:  # Run as a script: evals/ is on sys.path instead.
    from run import read_records


def load_results(path):
    results = read_records(path)
    for case_id, row in results.items():
        if not isinstance(row.get("passed"), bool):
            raise ValueError(f"{path}: {case_id}: passed must be true or false")
    return results


def compare(baseline, candidate):
    if baseline.keys() != candidate.keys():
        raise ValueError("Result IDs must exactly match; compare runs of the same cases")
    changes = {"regressed": [], "improved": [], "still_failing": []}
    for case_id in baseline:
        before, after = baseline[case_id]["passed"], candidate[case_id]["passed"]
        if before and not after:
            changes["regressed"].append(case_id)
        elif after and not before:
            changes["improved"].append(case_id)
        elif not after:
            changes["still_failing"].append(case_id)
    return changes


def describe(results):
    passed = sum(row["passed"] for row in results.values())
    modes = sorted({str(row.get("mode")) for row in results.values()})
    models = sorted({str(row.get("model")) for row in results.values()})
    return f"{passed}/{len(results)} passed (mode: {', '.join(modes)}; model: {', '.join(models)})"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, help="Results JSONL for the baseline")
    parser.add_argument("--candidate", required=True, help="Results JSONL for the candidate")
    args = parser.parse_args(argv)
    try:
        baseline = load_results(args.baseline)
        candidate = load_results(args.candidate)
        changes = compare(baseline, candidate)
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(f"baseline:  {describe(baseline)}")
    print(f"candidate: {describe(candidate)}")
    for label, ids in changes.items():
        print(f"{label}: {len(ids)}{': ' + ', '.join(ids) if ids else ''}")
    return 1 if changes["regressed"] else 0


if __name__ == "__main__":
    sys.exit(main())
