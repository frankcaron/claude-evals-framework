# Eval data

Each subdirectory is one eval suite. A push that changes files here runs
`.github/workflows/eval-data.yml`, which grades only the changed suites with
`evals/ci.py` and compares each with its version from the commit before the push.

```text
eval-data/<suite>/
├── cases.jsonl       # required: the spec (keep curated and versioned)
├── responses.jsonl   # required: the upstream workflow's answers for this push
├── gate.json         # optional: {"min_pass_rate": 0-1, "allow_regressions": false}
├── rubric.json       # optional, with grades.jsonl: rubric summary (informational)
└── grades.jsonl
```

Without `gate.json`, a suite must pass every case and show no regressions.

Commit only synthetic or approved, sanitized data. Git history keeps every
pushed answer even after a file is deleted. `support-agent/` is a copy of the
bundled synthetic examples.
