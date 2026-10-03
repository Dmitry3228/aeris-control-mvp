import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  outputDir: "./output/playwright",
  reporter: [["list"]],
  use: {
    baseURL: process.env.AERIS_WEB_URL ?? "http://127.0.0.1:3100",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
