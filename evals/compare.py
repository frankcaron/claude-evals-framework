"""Compare two results files from run.py case by case."""

import argparse
import json
import math
import sys

try:
    from evals.run import format_rate, manifest_path, read_records
except ImportError:  # Run as a script: evals/ is on sys.path instead.
    from run import format_rate, manifest_path, read_records


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


def mcnemar_p(regressed, improved):
    """Two-sided exact McNemar p-value: could this many changed cases split by chance?"""
    changed = regressed + improved
    if not changed:
        return 1.0
    tail = sum(math.comb(changed, k) for k in range(min(regressed, improved) + 1))
    return min(1.0, 2 * tail / 2 ** changed)


def load_manifest(results_path):
    """Provenance written by run.py after a complete run, or None if absent."""
    path = manifest_path(results_path)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def confounders(baseline, candidate):
    """Differences between two runs' inputs that make a comparison unfair."""
    if baseline is None or candidate is None:
        return ["provenance unknown: a results file has no manifest (incomplete or older run)"]
    notes = []
    if baseline["cases"]["sha256"] != candidate["cases"]["sha256"]:
        notes.append("case files differ; regressions may come from changed cases")
    if baseline["mode"] != candidate["mode"]:
        notes.append(f"modes differ: {baseline['mode']} vs {candidate['mode']}")
    if baseline["models"] != candidate["models"]:
        notes.append(f"models differ: {baseline['models']} vs {candidate['models']}")
    for name, manifest in (("baseline", baseline), ("candidate", candidate)):
        if not manifest.get("git") or manifest["git"]["dirty"]:
            notes.append(f"{name} ran from uncommitted or unknown code")
    return notes


def describe(results):
    passed = sum(row["passed"] for row in results.values())
    modes = sorted({str(row.get("mode")) for row in results.values()})
    models = sorted({str(row.get("model")) for row in results.values()})
    return f"{format_rate(passed, len(results))} passed (mode: {', '.join(modes)}; model: {', '.join(models)})"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, help="Results JSONL for the baseline")
    parser.add_argument("--candidate", required=True, help="Results JSONL for the candidate")
    args = parser.parse_args(argv)
    try:
        baseline = load_results(args.baseline)
        candidate = load_results(args.candidate)
        changes = compare(baseline, candidate)
        notes = confounders(load_manifest(args.baseline), load_manifest(args.candidate))
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(f"baseline:  {describe(baseline)}")
    print(f"candidate: {describe(candidate)}")
    for label, ids in changes.items():
        print(f"{label}: {len(ids)}{': ' + ', '.join(ids) if ids else ''}")
    p_value = mcnemar_p(len(changes["regressed"]), len(changes["improved"]))
    print(f"McNemar exact p={p_value:.3f} (regressed vs improved; small p = unlikely to be chance)")
    for note in notes:
        print(f"WARNING {note}")
    return 1 if changes["regressed"] else 0


if __name__ == "__main__":
    sys.exit(main())
