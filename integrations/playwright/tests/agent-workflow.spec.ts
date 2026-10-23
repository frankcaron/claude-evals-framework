import { pathToFileURL } from 'node:url';
import path from 'node:path';
import { expect, loadCases, settings, test } from '../evals';

// Replace with your deployed workflow, e.g. process.env.AGENT_URL.
const AGENT_URL = process.env.AGENT_URL
  ?? pathToFileURL(path.join(__dirname, '../demo-agent/index.html')).href
     + (process.env.DEMO_AGENT_VARIANT ? `?variant=${process.env.DEMO_AGENT_VARIANT}` : '');

// One Playwright test per eval case. Tests see only id, prompt, and context.
for (const evalCase of loadCases()) {
  test(`case ${evalCase.id}`, async ({ page, evalRecorder }) => {
    test.skip(!!settings.responses, 'EVAL_RESPONSES supplied by the pipeline; grading those instead');
    // Harness steps outside run(): if these fail, the test fails and no score is produced.
    await page.goto(AGENT_URL);
    await expect(page.getByRole('button', { name: 'Ask' })).toBeVisible();

    // Workflow steps inside run(): failures here are recorded as workflow errors.
    await evalRecorder.run(evalCase.id, async () => {
      await page.locator('#context').fill(evalCase.context ?? '');
      await page.locator('#question').fill(evalCase.prompt);
      await page.getByRole('button', { name: 'Ask' }).click();
      await expect(page.getByRole('status')).toHaveText('Done');
      return page.getByTestId('answer').innerText();
    });
  });
}
