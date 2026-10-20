import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests run against the real stack: API + Postgres + S3-compatible storage
 * (`docker compose up --build`, or `scripts/dev-stack.sh` with moto/LocalStack). Playwright starts
 * two Vite dev servers: port 5173 in dev-auth mode (most tests) and port 5174 in Cognito mode with
 * a fake Hosted UI domain, used by the session-expiry tests (Cognito itself is stubbed per test).
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
  projects: [
    // Desktop engines run every spec except the touch-only ones.
    { name: "chromium", use: { ...devices["Desktop Chrome"] }, testIgnore: /touch\.spec\.ts/ },
    { name: "firefox", use: { ...devices["Desktop Firefox"] }, testIgnore: /touch\.spec\.ts/ },
    { name: "webkit", use: { ...devices["Desktop Safari"] }, testIgnore: /touch\.spec\.ts/ },
    // Phones: a touchscreen, a small viewport, coarse pointer. Only the touch spec runs here.
    { name: "mobile-chrome", use: { ...devices["Pixel 7"] }, testMatch: /touch\.spec\.ts/ },
    { name: "mobile-safari", use: { ...devices["iPhone 14"] }, testMatch: /touch\.spec\.ts/ },
  ],
  webServer: [
    {
      command: "npm run dev -- --host localhost --port 5173",
      url: "http://localhost:5173",
      reuseExistingServer: true,
      timeout: 60_000,
    },
    {
      command: "npm run dev -- --host localhost --port 5174 --strictPort",
      url: "http://localhost:5174",
      reuseExistingServer: true,
      timeout: 60_000,
      env: {
        VITE_AUTH_MODE: "cognito",
        VITE_COGNITO_DOMAIN: "http://cognito.test",
        VITE_COGNITO_CLIENT_ID: "e2e-client",
        VITE_COGNITO_REDIRECT_URI: "http://localhost:5174/auth/callback",
      },
    },
  ],
});
