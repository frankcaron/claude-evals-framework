/**
 * Glue between a Playwright suite that drives an agentic workflow and the
 * Python eval runner in ../../evals.
 *
 * Contract: Playwright test status reports harness health (the page loaded,
 * selectors worked). Agent quality is an eval result: a crashed or timed-out
 * workflow is recorded as a workflow error and graded as a failed case, so the
 * evaluation still runs and counts it.
 */
import { test as base, type TestInfo } from '@playwright/test';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

export const REPO_ROOT = path.resolve(__dirname, '../..');

/** Paths come from the pipeline's environment; defaults use the bundled examples. */
export const settings = {
  cases: path.resolve(REPO_ROOT, process.env.EVAL_CASES ?? 'examples/cases.jsonl'),
  // Set when an upstream pipeline step already wrote responses; browser tests then skip.
  responses: process.env.EVAL_RESPONSES ? path.resolve(REPO_ROOT, process.env.EVAL_RESPONSES) : undefined,
  runDir: path.resolve(REPO_ROOT, 'results', 'playwright', process.env.EVAL_RUN_ID ?? 'local'),
  baseline: process.env.EVAL_BASELINE ? path.resolve(REPO_ROOT, process.env.EVAL_BASELINE) : undefined,
  python: process.env.EVAL_PYTHON ?? 'python3',
  caseTimeoutMs: Number(process.env.EVAL_CASE_TIMEOUT_MS ?? 60_000),
  minPassRate: Number(process.env.EVAL_MIN_PASS_RATE ?? 1),
  allowRegressions: process.env.EVAL_ALLOW_REGRESSIONS === '1',
};

/** What the workflow under test may see. Expected answers are deliberately absent. */
export interface CaseInput {
  id: string;
  prompt: string;
  context?: string;
}

function readJsonl(file: string): Record<string, unknown>[] {
  return fs.readFileSync(file, 'utf-8').split('\n').filter((line) => line.trim())
    .map((line) => JSON.parse(line));
}

/** Load cases for data-driven tests, stripping grading fields so tests cannot leak them. */
export function loadCases(file = settings.cases): CaseInput[] {
  return readJsonl(file).map((row) => ({
    id: String(row.id),
    prompt: String(row.prompt),
    ...(typeof row.context === 'string' ? { context: row.context } : {}),
  }));
}

function responseFile(caseId: string): string {
  return path.join(settings.runDir, 'responses', `${encodeURIComponent(caseId)}.json`);
}

function firstLine(error: unknown): string {
  const text = error instanceof Error ? error.message : String(error);
  return (text.split('\n').find((line) => line.trim()) ?? 'unknown error').slice(0, 500);
}

export class EvalRecorder {
  constructor(private readonly testInfo: TestInfo) {}

  /**
   * Run one case through the workflow and record its final answer. Errors and
   * timeouts are recorded as workflow errors instead of failing the test.
   */
  async run(caseId: string, workflow: () => Promise<string>): Promise<void> {
    if (this.testInfo.retry > 0) {
      throw new Error('Eval cases must not be retried: a retry would hide flaky workflows. Set retries: 0.');
    }
    let timer: NodeJS.Timeout | undefined;
    const timeout = new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new Error(`workflow timed out after ${settings.caseTimeoutMs}ms`)),
        settings.caseTimeoutMs);
    });
    let row: Record<string, string>;
    try {
      const response = await Promise.race([workflow(), timeout]);
      if (typeof response !== 'string') throw new Error('workflow must return the final answer as a string');
      row = { id: caseId, response };
    } catch (error) {
      row = { id: caseId, response: '', error: firstLine(error) };
      this.testInfo.annotations.push({ type: 'workflow_error', description: row.error });
    } finally {
      clearTimeout(timer);
    }
    if (this.testInfo.project.use.trace === 'on') {
      row.trace = path.relative(REPO_ROOT, this.testInfo.outputPath('trace.zip'));
    }
    fs.mkdirSync(path.dirname(responseFile(caseId)), { recursive: true });
    // 'wx' fails if a case is recorded twice, e.g. by two tests sharing an ID.
    fs.writeFileSync(responseFile(caseId), JSON.stringify(row) + '\n', { flag: 'wx' });
  }
}

export const test = base.extend<{ evalRecorder: EvalRecorder }>({
  evalRecorder: async ({}, use, testInfo) => {
    await use(new EvalRecorder(testInfo));
  },
});
export { expect } from '@playwright/test';

/** Merge per-case files written by parallel workers into responses.jsonl, in case order. */
export function collectResponses(cases = loadCases()): { file: string; missing: string[] } {
  const file = path.join(settings.runDir, 'responses.jsonl');
  const missing = cases.filter((c) => !fs.existsSync(responseFile(c.id))).map((c) => c.id);
  if (!missing.length) {
    fs.writeFileSync(file, cases.map((c) => fs.readFileSync(responseFile(c.id), 'utf-8')).join(''),
      { flag: 'wx' });
  }
  return { file, missing };
}

export interface ToolRun {
  status: number | null;
  stdout: string;
  stderr: string;
}

function python(script: string, args: string[]): ToolRun {
  const run = spawnSync(settings.python, [path.join(REPO_ROOT, 'evals', script), ...args],
    { cwd: REPO_ROOT, encoding: 'utf-8' });
  if (run.error) throw new Error(`could not start ${settings.python}: ${run.error.message}`);
  return { status: run.status, stdout: run.stdout, stderr: run.stderr };
}

/** Grade the collected responses offline with evals/run.py (exit 0 pass, 1 failures, 2 invalid). */
export function runEvaluation(responses: string, output = path.join(settings.runDir, 'results.jsonl')) {
  const run = python('run.py', ['--cases', settings.cases, '--responses', responses, '--output', output]);
  const manifestFile = output.replace(/\.jsonl$/, '.manifest.json');
  const manifest = fs.existsSync(manifestFile) ? JSON.parse(fs.readFileSync(manifestFile, 'utf-8')) : undefined;
  return { ...run, output, manifestFile, manifest };
}

/** Compare against an earlier results file with evals/compare.py (exit 1 means regressions). */
export function compareWithBaseline(candidate: string, baseline = settings.baseline): ToolRun | undefined {
  return baseline ? python('compare.py', ['--baseline', baseline, '--candidate', candidate]) : undefined;
}
