import type { Page, Route } from "@playwright/test";
import { expect, test } from "@playwright/test";
import { createHmac } from "node:crypto";
import { devToken, drawBox, seedBatch } from "./helpers";

/**
 * Session-expiry behaviour against the real API. These run against a second Vite server (port
 * 5174, started by playwright.config.ts) built in *Cognito* mode, because refresh-token handling
 * only exists there. Cognito itself is stubbed in the browser (`page.route`): the Hosted UI
 * redirect and the token endpoint. The API still validates tokens for real (dev HS256 tokens).
 */
const ORIGIN = "http://localhost:5174";
const COGNITO = "http://cognito.test";
const cors = { "access-control-allow-origin": "*", "access-control-allow-headers": "*", "access-control-allow-methods": "*" };

test.use({ baseURL: ORIGIN });

/** Looks valid to the SPA (parses, expires in an hour) but the API rejects the signature. */
const forgedToken = (email: string): string => {
  const enc = (o: unknown) => Buffer.from(JSON.stringify(o)).toString("base64url");
  const body = `${enc({ alg: "HS256", typ: "JWT" })}.${enc({
    sub: `dev-${email}`,
    email,
    "cognito:groups": ["labeler"],
    exp: Math.floor(Date.now() / 1000) + 3600,
  })}`;
  return `${body}.${createHmac("sha256", "not-the-server-secret").update(body).digest("base64url")}`;
};

const installCognitoStub = async (page: Page, issueToken: () => string) => {
  const state = { tokenRequests: 0, rejectRefresh: false };
  await page.route(`${COGNITO}/oauth2/token`, (route: Route) => {
    state.tokenRequests += 1;
    const isRefresh = (route.request().postData() ?? "").includes("grant_type=refresh_token");
    if (isRefresh && state.rejectRefresh) {
      return route.fulfill({ status: 400, headers: cors, contentType: "application/json", body: '{"error":"invalid_grant"}' });
    }
    return route.fulfill({
      status: 200,
      headers: cors,
      contentType: "application/json",
      body: JSON.stringify({ id_token: issueToken(), refresh_token: "stub-refresh", expires_in: 3600 }),
    });
  });
  // The Hosted UI: instantly "log in" and bounce back with an authorization code.
  await page.route(`${COGNITO}/oauth2/authorize*`, (route: Route) => {
    const state = new URL(route.request().url()).searchParams.get("state") ?? "";
    // A scripted redirect rather than a 302: WebKit cannot fulfill a route with a redirect status.
    const target = `${ORIGIN}/auth/callback?code=stub-code&state=${encodeURIComponent(state)}`;
    return route.fulfill({
      status: 200,
      contentType: "text/html",
      body: `<!doctype html><script>location.replace(${JSON.stringify(target)})</script>`,
    });
  });
  return state;
};

/** Seed the session once per tab (sessionStorage survives the login redirects; localStorage is cleared on sign-out). */
const seedSession = (page: Page, session: { idToken: string; refreshToken: string }) =>
  page.addInitScript((value: string) => {
    if (!sessionStorage.getItem("e2e.seeded")) {
      sessionStorage.setItem("e2e.seeded", "1");
      localStorage.setItem("labeler.session", value);
    }
  }, JSON.stringify(session));

test("an expired ID token is refreshed and the failed request is retried transparently", async ({
  page,
  request,
}) => {
  const email = `refresh-${Date.now()}@example.com`;
  const real = await devToken(request, email, ["labeler"]);
  const stub = await installCognitoStub(page, () => real);
  await seedSession(page, { idToken: forgedToken(email), refreshToken: "stub-refresh" });

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Batches" })).toBeVisible();
  // One refresh, shared by every request that hit the expired token, and not a sign-out. The
  // heading can render before the refresh request is issued, so wait for it, then for quiet.
  await expect.poll(() => stub.tokenRequests).toBe(1);
  await page.waitForLoadState("networkidle");
  expect(stub.tokenRequests).toBe(1);
  await expect(page).toHaveURL(`${ORIGIN}/`);
});

test("unsaved boxes and the current page survive a forced sign-out and re-login", async ({ page, request }) => {
  const email = `draft-${Date.now()}@example.com`;
  const token = await devToken(request, email, ["labeler"]);
  const batchId = await seedBatch(request, token, 1);

  const stub = await installCognitoStub(page, () => token);
  await seedSession(page, { idToken: token, refreshToken: "stub-refresh" });

  // Saving drafts "fails": the server says the token expired. Meanwhile the refresh token is rejected.
  stub.rejectRefresh = true;
  await page.route("**/images/*/annotations", (route: Route) =>
    route.request().method() === "OPTIONS"
      ? route.fulfill({ status: 204, headers: cors })
      : route.fulfill({
          status: 401,
          headers: cors,
          contentType: "application/json",
          body: JSON.stringify({ detail: "Invalid token: Signature has expired" }),
        }),
  );

  await page.goto(`/batches/${batchId}/label`);
  await drawBox(page, [0.2, 0.2], [0.5, 0.5], "car");

  // The autosave 401s, the refresh is rejected, and the user lands on the login page.
  await expect(page).toHaveURL(/\/login/);

  // Sign back in through the (stubbed) Hosted UI; the server session is healthy again.
  stub.rejectRefresh = false;
  await page.unroute("**/images/*/annotations");
  await page.getByRole("button", { name: "Sign in" }).click();

  await expect(page).toHaveURL(new RegExp(`/batches/${batchId}/label`));
  await expect(page.getByText("Restored your unsaved changes")).toBeVisible();
  await expect(page.getByLabel("car box").first()).toBeVisible();

  // ...and the restored box is persisted by the normal autosave.
  await expect(page.getByText("draft saved")).toBeVisible();
});
