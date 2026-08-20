import { defineConfig } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontendDirectory = fileURLToPath(new URL(".", import.meta.url));
const repositoryDirectory = path.resolve(frontendDirectory, "..");
const reuseExistingServer = process.env.PLAYWRIGHT_REUSE_SERVER === "1";

function port(name: string, fallback: string) {
  const value = process.env[name]?.trim() || fallback;
  if (!/^\d{2,5}$/.test(value) || Number(value) > 65_535) {
    throw new Error(`${name} must be a valid TCP port`);
  }
  return value;
}

function shellQuote(value: string) {
  return `'${value.replaceAll("'", `'"'"'`)}'`;
}

const backendPort = port("E2E_BACKEND_PORT", "38100");
const frontendPort = port("E2E_FRONTEND_PORT", "33100");
const backendUrl = `http://127.0.0.1:${backendPort}`;
const frontendUrl = `http://127.0.0.1:${frontendPort}`;
const artifactDirectory =
  process.env.E2E_ARTIFACT_DIRECTORY?.trim() ||
  path.join(
    frontendDirectory,
    "node_modules",
    ".cache",
    `run-artifacts-${backendPort}`,
  );

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
      command: [
        "make serve-backend-fixture",
        `BACKEND_PORT=${shellQuote(backendPort)}`,
        `FRONTEND_PORT=${shellQuote(frontendPort)}`,
        `ARTIFACT_DIRECTORY=${shellQuote(artifactDirectory)}`,
      ].join(" "),
      cwd: repositoryDirectory,
      url: `${backendUrl}/health`,
      reuseExistingServer,
      timeout: 120_000,
      stdout: "pipe",
      stderr: "pipe",
    },
    {
      command: [
        "make serve-frontend",
        `BACKEND_PORT=${shellQuote(backendPort)}`,
        `FRONTEND_PORT=${shellQuote(frontendPort)}`,
      ].join(" "),
      cwd: repositoryDirectory,
      url: frontendUrl,
      reuseExistingServer,
      timeout: 120_000,
      stdout: "pipe",
      stderr: "pipe",
    },
  ],
});
