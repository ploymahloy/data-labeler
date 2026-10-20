import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getSession, saveSession } from "../auth/sessionStore";
import { installFakeServer, makeToken } from "../test/fakeAuthServer";
import type { TokenEndpoint } from "../test/fakeAuthServer";
import { isApiError, request, setUnauthorizedHandler } from "./client";

describe("request: expired token handling", () => {
  let fresh: string;
  let stale: string;
  let tokenMode: TokenEndpoint;
  const onUnauthorized = vi.fn();

  beforeEach(() => {
    stale = makeToken(-60, { jti: "stale" });
    fresh = makeToken(3600, { jti: "fresh" });
    tokenMode = "ok";
    onUnauthorized.mockReset();
    setUnauthorizedHandler(onUnauthorized);
    saveSession({ idToken: stale, refreshToken: "refresh-1" });
  });
  afterEach(() => {
    saveSession(null);
    vi.unstubAllGlobals();
  });

  it("refreshes once on a 401 and transparently retries the request", async () => {
    const { calls } = installFakeServer({ validToken: () => fresh, token: () => tokenMode });
    await expect(request<{ ok: boolean }>("/batches")).resolves.toEqual({ ok: true });
    expect(calls.tokenRequests).toBe(1);
    expect(calls.api.map((c) => c.auth)).toEqual([`Bearer ${stale}`, `Bearer ${fresh}`]);
    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(getSession()?.idToken).toBe(fresh);
  });

  it("shares one refresh between parallel requests that all hit the expiry", async () => {
    const { calls } = installFakeServer({ validToken: () => fresh, token: () => tokenMode });
    const results = await Promise.all([request("/a"), request("/b"), request("/c")]);
    expect(results).toHaveLength(3);
    expect(calls.tokenRequests).toBe(1);
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("does not refresh again when another request already replaced the token", async () => {
    const { calls } = installFakeServer({ validToken: () => fresh, token: () => tokenMode });
    // A request sent with the stale token is still in flight when a refresh completes elsewhere.
    const slow = request("/slow");
    saveSession({ idToken: fresh, refreshToken: "refresh-1" });
    await expect(slow).resolves.toEqual({ ok: true });
    expect(calls.tokenRequests).toBe(0);
  });

  it("retries only once: a second 401 signs the user out", async () => {
    // The server rejects even the refreshed token (e.g. the user was disabled).
    installFakeServer({
      validToken: () => "something-else",
      issuedToken: () => fresh,
      token: () => tokenMode,
    });
    const error = await request("/batches").catch((e: unknown) => e);
    expect(isApiError(error) && error.status).toBe(401);
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it("signs the user out when the refresh token is rejected", async () => {
    tokenMode = "rejected";
    installFakeServer({ validToken: () => fresh, token: () => tokenMode });
    const error = await request("/batches").catch((e: unknown) => e);
    expect(isApiError(error) && error.status).toBe(401);
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it("keeps the session when the token endpoint is unreachable", async () => {
    tokenMode = "offline";
    installFakeServer({ validToken: () => fresh, token: () => tokenMode });
    const error = await request("/batches").catch((e: unknown) => e);
    expect(isApiError(error) && error.status).toBe(401);
    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(getSession()?.idToken).toBe(stale);
  });

  it("signs out immediately when there is no refresh token (dev sessions)", async () => {
    saveSession({ idToken: stale });
    installFakeServer({ validToken: () => fresh, token: () => tokenMode });
    await request("/batches").catch(() => undefined);
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });
});
