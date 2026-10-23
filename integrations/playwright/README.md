# Playwright integration

Run an upstream agentic workflow from a Playwright suite, record each case's
final answer, and grade the answers with `evals/run.py`. The Python framework
stays dependency-free; this directory is the only part that needs Node.

```text
cases.jsonl ─▶ [agent-workflow project]  one Playwright test per case drives the
                │                         workflow and records its answer or error
                ▼
           results/playwright/<run id>/responses/<case>.json
                │
                ▼
           [evaluate project]  merges answers ─▶ evals/run.py ─▶ gates ─▶ evals/compare.py
```

## Contract

- **Playwright test status means harness health.** If the page fails to load
  or a harness selector breaks outside `evalRecorder.run()`, the test fails,
  the `evaluate` project does not run, and no score is produced. A broken
  harness is not an agent failure.
- **Agent quality is an eval result.** An exception, failed assertion, or
  timeout inside `evalRecorder.run()` is recorded as a workflow error
  (`{"id","response":"","error":"..."}`); the test still passes, and the case
  is graded as failed with `workflow_error=True`. This keeps "the agent crashed"
  separate from "the agent answered wrongly" in the results.
- **Workflow tests never see expected answers.** `loadCases()` returns only
  `id`, `prompt`, and `context`.
- **First attempts only.** Retries are disabled and the recorder refuses to run
  on a retry: a retry that succeeds would hide a flaky workflow from the score.
- **Every case or no score.** The `evaluate` test fails if any case has no
  recorded answer or error, and `run.py` exits 2 on mismatched IDs. A filtered
  run (`--grep`) therefore produces no score; that is deliberate.

## Run it

Python 3.10+ must be available as `python3` (or set `EVAL_PYTHON`; on macOS,
use the `.venv` from the main README, e.g. `EVAL_PYTHON=../../.venv/bin/python3`).

```bash
cd integrations/playwright
npm ci
npx playwright install chromium
npx playwright test
```

This drives the bundled demo agent (`demo-agent/index.html`) through the four
example cases and prints `4/4 (100%, 95% CI 51%-100%) passed`. Each run writes
everything to `results/playwright/<run id>/` at the repository root:
per-case answers, `responses.jsonl`, `results.jsonl`, the provenance manifest,
Playwright traces (`output/`), and the HTML report (`report/`).

To see a regression gate fail, record a baseline and then run a variant that
obeys instructions injected into its context:

```bash
EVAL_RUN_ID=baseline npx playwright test
DEMO_AGENT_VARIANT=naive EVAL_RUN_ID=naive \
  EVAL_BASELINE=results/playwright/baseline/results.jsonl npx playwright test
```

The second run fails the `evaluate` test twice over: `injected-context`
regresses, and the 3/4 pass rate is below the default gate of 1.0.

## Plug in your workflow

1. Point the suite at your inputs and app: set `EVAL_CASES` to your pipeline's
   cases file and `AGENT_URL` to the workflow under test.
2. Edit `tests/agent-workflow.spec.ts`. Put harness setup (navigation, login)
   before `evalRecorder.run()`, and put the workflow steps inside it, returning
   the final answer text.
3. Choose the gates: `EVAL_MIN_PASS_RATE` and, for release candidates,
   `EVAL_BASELINE` pointing at a results file from the version you ship today.

If an upstream pipeline step already produced the answers, set
`EVAL_RESPONSES` to that JSONL file. The browser tests skip and `evaluate`
grades the file directly. Rows may add `"error"` (a workflow failure) and
`"trace"` (a path to the trace for that case); both are copied into results.

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `EVAL_CASES` | `examples/cases.jsonl` | Cases file, relative to the repository root or absolute |
| `EVAL_RESPONSES` | unset | Grade this responses file instead of driving the browser |
| `AGENT_URL` | bundled demo agent | The workflow under test |
| `EVAL_RUN_ID` | UTC timestamp | Run folder name under `results/playwright/`; use a CI build ID |
| `EVAL_MIN_PASS_RATE` | `1` | Fail `evaluate` below this pass rate (0 to 1) |
| `EVAL_BASELINE` | unset | Results file to compare against; regressions fail `evaluate` |
| `EVAL_ALLOW_REGRESSIONS` | unset | Set to `1` to report regressions without failing |
| `EVAL_CASE_TIMEOUT_MS` | `60000` | Per-case workflow timeout, recorded as a workflow error |
| `EVAL_PYTHON` | `python3` | Interpreter for `evals/*.py` |
| `PLAYWRIGHT_CHROMIUM_EXECUTABLE` | unset | Use a preinstalled Chromium instead of a downloaded one |
| `DEMO_AGENT_VARIANT` | unset | `naive` makes the demo agent obey injected instructions |

## Limitations

- **The gate rejects any failure by default.** `EVAL_MIN_PASS_RATE=1` fails the
  suite on any failing case. Lower it deliberately, and remember the interval:
  on 30 cases, a 90% observed pass rate is compatible with roughly 74-96%.
- **Grading is the runner's substring and canary checks.** For groundedness or
  tone, grade the recorded `responses.jsonl` with `/grade-rubric`.
- **Only the final answer is graded.** The runner does not score the agent's
  steps. Use the linked traces with `/review-workflow` for tool choice,
  ordering, and side effects.
- **One sample per case.** A stochastic workflow needs repeated runs (distinct
  `EVAL_RUN_ID`s) before a regression is believable; `compare.py` reports a
  McNemar p-value for that reason.
