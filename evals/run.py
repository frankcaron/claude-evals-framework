"""Small JSONL evaluation runner using only the Python standard library."""

import argparse
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request


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


def grade(response, expected):
    missing = [text for text in expected if text.casefold() not in response.casefold()]
    return {"passed": not missing, "missing": missing}


def ask_claude(case, model, api_key):
    prompt = case["prompt"]
    if case.get("context"):
        prompt = f"Context:\n{case['context']}\n\nQuestion:\n{prompt}"
    payload = {
        "model": model,
        "max_tokens": 512,
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
            if responses.keys() != cases.keys():
                raise ValueError("Response IDs must exactly match case IDs")
            if any(not isinstance(row.get("response"), str) for row in responses.values()):
                raise ValueError("Each recorded response must be a string")
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if args.live and not api_key:
            raise ValueError("Set ANTHROPIC_API_KEY to use --live")
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        passed = 0
        # Refuse to overwrite baselines, datasets, or earlier results.
        with output.open("x", encoding="utf-8") as destination:
            for case_id, case in cases.items():
                if responses is not None:
                    text = responses[case_id]["response"]
                    model, usage = None, {}
                else:
                    text, model, usage = ask_claude(case, args.model, api_key)
                score = grade(text, case["expected_contains"])
                passed += score["passed"]
                row = {
                    "id": case_id,
                    "mode": "replay" if responses is not None else "live",
                    "model": model,
                    "response": text,
                    "usage": usage,
                    **score,
                }
                destination.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"{passed}/{len(cases)} passed; results: {output}")
        return 0 if passed == len(cases) else 1
    except urllib.error.HTTPError as error:
        print(f"error: Claude API HTTP {error.code}; run incomplete", file=sys.stderr)
    except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError) as error:
        print(f"error: {error}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
