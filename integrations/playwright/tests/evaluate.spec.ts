import fs from 'node:fs';
import { collectResponses, compareWithBaseline, expect, runEvaluation, settings, test } from '../evals';

test('grade recorded workflow answers', async ({}, testInfo) => {
  let responses = settings.responses;
  if (!responses) {
    const collected = collectResponses();
    // Every case needs an answer or a recorded workflow error; a partial run is not a score.
    expect(collected.missing, 'cases with no recorded response').toEqual([]);
    responses = collected.file;
  }

  const evaluation = runEvaluation(responses);
  await testInfo.attach('eval-summary.txt', { body: evaluation.stdout + evaluation.stderr });
  for (const attachment of [evaluation.output, evaluation.manifestFile]) {
    if (fs.existsSync(attachment)) await testInfo.attach(attachment.split('/').pop()!, { path: attachment });
  }
  console.log(evaluation.stdout.trim());
  // Exit 2 means invalid input or an incomplete run.
  expect(evaluation.status, evaluation.stderr).not.toBe(2);

  const { passed, total, workflow_errors: errors } = evaluation.manifest;
  testInfo.annotations.push({ type: 'eval', description: `${passed}/${total} passed; workflow errors: ${errors.length}` });
  expect(passed / total, `pass rate below EVAL_MIN_PASS_RATE=${settings.minPassRate}`)
    .toBeGreaterThanOrEqual(settings.minPassRate);

  const comparison = compareWithBaseline(evaluation.output);
  if (comparison) {
    await testInfo.attach('comparison.txt', { body: comparison.stdout + comparison.stderr });
    console.log(comparison.stdout.trim());
    expect(comparison.status, comparison.stderr).not.toBe(2);
    if (!settings.allowRegressions) {
      expect(comparison.status, 'regressions against EVAL_BASELINE').toBe(0);
    }
  }
});
