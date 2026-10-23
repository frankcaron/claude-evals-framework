import { defineConfig, devices } from '@playwright/test';

// Set once in the main process; workers inherit it, so every worker writes to
// the same run directory. A pipeline can set EVAL_RUN_ID itself, e.g. a CI build ID.
process.env.EVAL_RUN_ID ??= new Date().toISOString().replace(/[:.]/g, '-');

export default defineConfig({
  testDir: './tests',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  // The eval scores first attempts; a retry would hide a flaky workflow.
  retries: 0,
  // Keep each run's traces and report beside its eval results, so recorded
  // trace paths stay valid; Playwright empties outputDir at the start of a run.
  outputDir: `../../results/playwright/${process.env.EVAL_RUN_ID}/output`,
  reporter: [['list'], ['html', { open: 'never', outputFolder: `../../results/playwright/${process.env.EVAL_RUN_ID}/report` }]],
  use: {
    trace: 'retain-on-failure',
    // Optional: a preinstalled browser when the bundled one isn't downloaded.
    launchOptions: { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined },
  },
  projects: [
    {
      // Drives the upstream workflow once per case and records its final answers.
      name: 'agent-workflow',
      testMatch: /agent-workflow\.spec\.ts/,
      use: { ...devices['Desktop Chrome'] },
    },
    {
      // Grades the recorded answers; runs only after every workflow test finished.
      name: 'evaluate',
      testMatch: /evaluate\.spec\.ts/,
      dependencies: ['agent-workflow'],
    },
  ],
});
