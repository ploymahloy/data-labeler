import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests run against the real stack: API + Postgres + S3-compatible storage
 * (`docker compose up --build`, or `scripts/dev-stack.sh` with moto/LocalStack). Playwright starts
 * only the Vite dev server (in dev-auth mode).
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:5173",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "npm run dev -- --host localhost --port 5173",
    url: "http://localhost:5173",
    reuseExistingServer: true,
    timeout: 60_000,
  },
});
