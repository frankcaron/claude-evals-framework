"""Small JSONL evaluation runner using only the Python standard library."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys
import urllib.error
import urllib.request

if sys.version_info < (3, 10):
    print("error: Python 3.10+ is required; see README.md for setup", file=sys.stderr)
    sys.exit(2)


def read_records(path):
    records = {}
    with Path(path).open(encoding="utf-8") as source:
        for number, line in enumerate(source, 1):
            if not line.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError(f"{path}:{number}: expected an object")
            case_id = record.get("id")
            if not isinstance(case_id, str) or not case_id or case_id in records:
                raise ValueError(f"{path}:{number}: id must be a unique nonempty string")
            records[case_id] = record
    if not records:
        raise ValueError(f"{path}: no records")
    return records


def validate_cases(cases):
    for case_id, case in cases.items():
        if not isinstance(case.get("prompt"), str) or not case["prompt"].strip():
            raise ValueError(f"{case_id}: prompt must be a nonempty string")
        if not isinstance(case.get("context", ""), str):
            raise ValueError(f"{case_id}: context must be a string")
        expected = case.get("expected_contains")
        if (not isinstance(expected, list) or not expected
                or any(not isinstance(text, str) or not text.strip() for text in expected)):
            raise ValueError(f"{case_id}: expected_contains must contain nonempty strings")
        forbidden = case.get("expected_not_contains", [])
        if (not isinstance(forbidden, list)
                or any(not isinstance(text, str) or not text.strip() for text in forbidden)):
            raise ValueError(f"{case_id}: expected_not_contains must contain nonempty strings")


def grade(response, expected, forbidden=()):
    folded = response.casefold()
    missing = [text for text in expected if text.casefold() not in folded]
    found = [text for text in forbidden if text.casefold() in folded]
    return {"passed": not missing and not found, "missing": missing, "forbidden": found}


def wilson_interval(passed, total, z=1.96):
    """95% Wilson score interval for a pass rate; stays inside [0, 1] for small n."""
    if not total:
        return 0.0, 0.0
    rate = passed / total
    center = (rate + z * z / (2 * total)) / (1 + z * z / total)
    margin = z * math.sqrt(rate * (1 - rate) / total + z * z / (4 * total * total))
    margin /= 1 + z * z / total
    return max(0.0, center - margin), min(1.0, center + margin)


def format_rate(passed, total):
    low, high = wilson_interval(passed, total)
    return f"{passed}/{total} ({passed / total:.0%}, 95% CI {low:.0%}-{high:.0%})"


def ask_claude(case, model, api_key):
    prompt = case["prompt"]
    if case.get("context"):
        prompt = f"Context:\n{case['context']}\n\nQuestion:\n{prompt}"
    return send_message(prompt, model, api_key)


def send_message(prompt, model, api_key, max_tokens=512):
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    request = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        result = json.load(response)
    if result.get("stop_reason") != "end_turn":
        raise ValueError("Claude did not complete its response; no score recorded")
    text = "\n".join(
        block["text"] for block in result["content"] if block["type"] == "text"
    )
    if not text.strip():
        raise ValueError("Claude returned no text")
    return text, result["model"], result.get("usage", {})


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest() if path else None


def git_revision():
    """Commit and dirty flag of this checkout, or None outside a Git repository."""
    root = Path(__file__).resolve().parents[1]
    try:
        commit = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                                capture_output=True, text=True, check=True).stdout.strip()
        status = subprocess.run(["git", "-C", str(root), "status", "--porcelain"],
                                capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return {"commit": commit, "dirty": bool(status.strip())}


def manifest_path(output):
    return Path(output).with_suffix(".manifest.json")


def validate_responses(responses, cases):
    if responses.keys() != cases.keys():
        missing = sorted(cases.keys() - responses.keys())
        extra = sorted(responses.keys() - cases.keys())
        raise ValueError(f"Response IDs must exactly match case IDs (missing: {missing}; extra: {extra})")
    for case_id, row in responses.items():
        if not isinstance(row.get("response"), str):
            raise ValueError(f"{case_id}: each recorded response must be a string")
        for field in ("error", "trace"):
            value = row.get(field)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{case_id}: {field} must be a nonempty string when present")


def write_manifest(output, args, models, total, failed, errors):
    """Record provenance beside a completed results file; never includes secrets."""
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "live" if args.live else "replay",
        "requested_model": args.model,
        "models": sorted(models),
        "cases": {"path": args.cases, "sha256": sha256(args.cases)},
        "responses": {"path": args.responses, "sha256": sha256(args.responses)},
        "git": git_revision(),
        "python": platform.python_version(),
        "total": total,
        "passed": total - len(failed),
        "ci95": list(wilson_interval(total - len(failed), total)),
        "failed": failed,
        "workflow_errors": errors,
    }
    with manifest_path(output).open("x", encoding="utf-8") as destination:
        json.dump(manifest, destination, indent=2)
        destination.write("\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", required=True, help="JSONL evaluation cases")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--responses", help="JSONL recorded responses; no network access")
    mode.add_argument("--live", action="store_true", help="Send cases to Claude (billable)")
    parser.add_argument("--model", help="Explicit Claude model ID, required with --live")
    parser.add_argument("--output", required=True, help="New JSONL results file")
    args = parser.parse_args(argv)
    if args.live and not args.model:
        parser.error("--live requires --model")
    try:
        cases = read_records(args.cases)
        validate_cases(cases)
        responses = read_records(args.responses) if args.responses else None
        if responses is not None:
            validate_responses(responses, cases)
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if args.live and not api_key:
            raise ValueError("Set ANTHROPIC_API_KEY to use --live")
        output = Path(args.output)
        if output.suffix != ".jsonl":
            raise ValueError("--output must end in .jsonl")
        if manifest_path(output).exists():
            raise ValueError(f"{manifest_path(output)} already exists; choose a new --output")
        output.parent.mkdir(parents=True, exist_ok=True)
        passed, failures, models = 0, [], set()
        # Refuse to overwrite baselines, datasets, or earlier results.
        with output.open("x", encoding="utf-8") as destination:
            for case_id, case in cases.items():
                recorded = responses[case_id] if responses is not None else {}
                if responses is not None:
                    text = recorded["response"]
                    model, usage = None, {}
                else:
                    text, model, usage = ask_claude(case, args.model, api_key)
                    models.add(model)
                score = grade(text, case["expected_contains"],
                              case.get("expected_not_contains", []))
                # A workflow that crashed or timed out failed the task, whatever its text.
                score["error"] = recorded.get("error")
                score["passed"] = score["passed"] and score["error"] is None
                passed += score["passed"]
                if not score["passed"]:
                    failures.append((case_id, score))
                row = {
                    "id": case_id,
                    "mode": "replay" if responses is not None else "live",
                    "model": model,
                    "response": text,
                    "usage": usage,
                    **score,
                }
                if recorded.get("trace"):
                    row["trace"] = recorded["trace"]
                destination.write(json.dumps(row, ensure_ascii=False) + "\n")
        # Print fragments from the case file only; responses may be sensitive.
        for case_id, score in failures:
            print(f"FAIL {case_id}: missing={score['missing']} forbidden={score['forbidden']} "
                  f"workflow_error={score['error'] is not None}")
        # Written only after every case completes, so its presence marks a full run.
        write_manifest(output, args, models, len(cases), [case_id for case_id, _ in failures],
                       [case_id for case_id, score in failures if score["error"] is not None])
        print(f"{format_rate(passed, len(cases))} passed; results: {output}")
        return 0 if passed == len(cases) else 1
    except urllib.error.HTTPError as error:
        print(f"error: Claude API HTTP {error.code}; run incomplete", file=sys.stderr)
    except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError) as error:
        print(f"error: {error}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
