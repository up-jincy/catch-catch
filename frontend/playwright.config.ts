import { defineConfig } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontendDirectory = fileURLToPath(new URL(".", import.meta.url));
const repositoryDirectory = path.resolve(frontendDirectory, "..");
const reuseExistingServer = process.env.PLAYWRIGHT_REUSE_SERVER === "1";
const backendPort = process.env.E2E_BACKEND_PORT ?? "38100";
const frontendPort = process.env.E2E_FRONTEND_PORT ?? "33100";
const backendUrl = `http://127.0.0.1:${backendPort}`;
const frontendUrl = `http://127.0.0.1:${frontendPort}`;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  timeout: 45_000,
  expect: { timeout: 12_000 },
  reporter: process.env.CI ? "github" : "list",
  outputDir: "./node_modules/.cache/playwright-test-results",
  use: {
    baseURL: frontendUrl,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [
    {
      name: "desktop-chromium",
      use: { browserName: "chromium", viewport: { width: 1280, height: 800 } },
    },
    {
      name: "mobile-chromium",
      use: {
        browserName: "chromium",
        viewport: { width: 375, height: 812 },
        deviceScaleFactor: 2,
        hasTouch: true,
        isMobile: true,
      },
    },
  ],
  webServer: [
    {
      command: `make serve-backend-fixture BACKEND_PORT=${backendPort} FRONTEND_PORT=${frontendPort}`,
      cwd: repositoryDirectory,
      url: `${backendUrl}/health`,
      reuseExistingServer,
      timeout: 120_000,
      stdout: "pipe",
      stderr: "pipe",
    },
    {
      command: `make serve-frontend BACKEND_PORT=${backendPort} FRONTEND_PORT=${frontendPort}`,
      cwd: repositoryDirectory,
      url: frontendUrl,
      reuseExistingServer,
      timeout: 120_000,
      stdout: "pipe",
      stderr: "pipe",
    },
  ],
});
