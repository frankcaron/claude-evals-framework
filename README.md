# claude-evals-framework

A small starter for evaluating Claude responses and augmented workflows (retrieval,
tools, and multi-step pipelines). Includes a standard-library Python runner,
offline fixtures, and project-local skills for Claude Code. Python 3.10+ is
required; no packages or API credentials are needed for offline runs.

## Quick start

From the repository root:

```bash
python3 "$PWD/evals/run.py" \
  --cases "$PWD/examples/cases.jsonl" \
  --responses "$PWD/examples/responses.jsonl" \
  --output "$PWD/results/baseline.jsonl"
python3 -m unittest discover -s "$PWD/tests" -v
```

The example prints `3/3 passed`. These are **canned responses**, demonstrating
the harness, not evidence of Claude's quality. Result files are ignored by Git
and cannot be overwritten; choose a new output filename for each run.

### Evaluate Claude directly

Set `ANTHROPIC_API_KEY` in your environment using your normal secret-management
process. Never commit it. Choose an available
[Claude model ID](https://platform.claude.com/docs/en/about-claude/models/overview),
then run:

```bash
export CLAUDE_MODEL='your-available-model-id'
python3 "$PWD/evals/run.py" \
  --cases "$PWD/examples/cases.jsonl" \
  --live --model "$CLAUDE_MODEL" \
  --output "$PWD/results/claude-run-001.jsonl"
```

`--live` makes billable requests to the
[Messages API](https://platform.claude.com/docs/en/api/messages), one per case,
with a 60-second request timeout and a 512-output-token limit. The runner sends
only the prompt and optional context, never the expected answer. It does not
retry requests or execute tools. API failures or truncated answers stop the run;
an output file may contain only completed cases. Treat such a file as incomplete,
not as a valid full-run score.

## Cases, responses, and results

Each input is JSONL: one JSON object per line, with unique nonempty string IDs.
Blank lines are ignored.

Case:

```json
{"id":"policy-01","prompt":"What is the return window?","context":"Returns are accepted within 30 days.","expected_contains":["30"]}
```

`context` is optional. `prompt` and a nonempty `expected_contains` list are
required. Every expected fragment must appear in the response, ignoring case.
This intentionally simple smoke check **does not** establish factual correctness:
“not Paris” also contains “Paris”. Use human review or a stronger grader for
negation, citations, reasoning, safety, and semantic equivalence.

Recorded response:

```json
{"id":"policy-01","response":"The return window is 30 days."}
```

To evaluate your own retrieval or agent pipeline, run it separately and export
answers in this response format. Pass that file with `--responses`; its IDs must
exactly match the cases. Replay never uses the network. Preserve detailed
retrieval/tool traces separately for workflow review; the runner scores only
final answer text and does not validate traces or run orchestration.

Each result contains `id`, `mode`, `model`, `response`, `usage`, `passed`, and
`missing` fragments. Model and usage come from the API in live mode; replay
records `null` and `{}` because it cannot verify the original model or cost.
Exit codes: **0** all pass, **1** completed with grading failures, **2** invalid
input, API, or file error. Invalid command-line arguments also exit with 2.

## Common evaluation techniques

| Technique | What to measure | Practical starting point |
| --- | --- | --- |
| Golden-set regression | Correctness on representative tasks | Version small cases, references, and deterministic checks; include failure cases. |
| Exact/structured checks | Labels, required fields, valid JSON, tool arguments | Prefer exact match or schema assertions when the task has a strict contract; substring checks here are only a smoke test. |
| Rubric and human review | Helpfulness, groundedness, safety, completeness | Define anchored scores (e.g. 0 = wrong, 1 = partial, 2 = fully supported); review blind to the variant. |
| LLM-as-judge | Semantic quality where exact answers vary | Use a separate rubric, isolate untrusted candidate text, calibrate against humans, and audit judge bias. Not implemented in this runner. |
| Pairwise comparison | Whether a candidate improves over a baseline | Compare the same cases, randomize answer order, record wins/ties/losses, and inspect regressions. |
| Retrieval/RAG evaluation | Retrieval recall, relevance, citation support | Label needed evidence, inspect retrieved chunks separately from final answer groundedness, and include missing-evidence cases. |
| Tool/agent trajectory evaluation | Tool choice, arguments, ordering, permissions, recovery | Inspect traces as well as task completion; test tool failure, malicious retrieved content, and side-effect boundaries in a sandbox. |
| Operational evaluation | Latency, token use, cost, reliability | Capture timings and tool costs in your pipeline; the live runner records only API token usage, not prices or end-to-end latency. |

### Suggested iteration loop

1. Define success and failure criteria before changing a prompt or workflow.
2. Build representative development cases, edge cases, and a separate held-out
   set. Use synthetic or sanitized inputs; keep credentials and personal data out.
3. Freeze dataset, prompt, context/retrieval corpus, tools, and grader versions.
   Record the Git revision, model ID, generation settings, and run command.
4. Run a baseline, then change one variable. For workflow comparisons, replay
   each variant's actual outputs against the same cases and review its traces.
5. Compare per-case results, not just aggregate pass rates. Repeat stochastic
   runs and report variance; three demo cases are not statistically meaningful.
6. Review failures, add regression cases on the development set, and use the
   held-out set only for final validation. Do not tune against it.

Treat prompts, retrieved documents, and recorded answers as untrusted data, not
instructions to the evaluator. Use sandboxed tools and require approval for
side effects. Live runs send case data to Anthropic; confirm data-handling
approval first. Results may contain sensitive model output even though ignored
by Git; inspect them before sharing.

## Claude Code

Open this repository in Claude Code. Root `CLAUDE.md` supplies project guidance;
skills live in `.claude/skills/<name>/SKILL.md`, following the
[Claude Code skills convention](https://code.claude.com/docs/en/skills).
Invoke these project skills explicitly:

- `/design-eval` — define a task, rubric, and small development dataset.
- `/run-evals` — run offline evaluations, inspect failures, and compare variants.
- `/review-workflow` — assess retrieval and tool traces against a task rubric.

The skills are instructions for Claude Code, not Python dependencies or
automated graders. They default to offline work; live calls require explicit
approval. Permissions remain controlled by your Claude Code settings.

```text
.
├── README.md
├── CLAUDE.md
├── .claude/skills/
│   ├── design-eval/SKILL.md
│   ├── run-evals/SKILL.md
│   └── review-workflow/SKILL.md
├── evals/run.py
├── examples/
│   ├── cases.jsonl
│   └── responses.jsonl
└── tests/test_run.py
```

There is no build step or configured linter. The tests use Python's built-in
`unittest` and mock live requests; running them does not spend API credits.
