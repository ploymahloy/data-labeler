import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installFakeServer, makeToken } from "../test/fakeAuthServer";
import type { TokenEndpoint } from "../test/fakeAuthServer";
import { refreshSession } from "./refresh";
import { getSession, saveSession } from "./sessionStore";

describe("refreshSession", () => {
  let fresh: string;
  let tokenMode: TokenEndpoint;

  beforeEach(() => {
    fresh = makeToken(3600, { jti: "fresh" });
    tokenMode = "ok";
    saveSession({ idToken: makeToken(-10), refreshToken: "refresh-1" });
  });
  afterEach(() => {
    saveSession(null);
    vi.unstubAllGlobals();
  });

  it("stores the new ID token and keeps the refresh token", async () => {
    installFakeServer({ validToken: () => fresh, token: () => tokenMode });
    expect(await refreshSession()).toBe("ok");
    expect(getSession()).toEqual({ idToken: fresh, refreshToken: "refresh-1" });
  });

  it("is single-flight: concurrent callers share one token request", async () => {
    const { calls } = installFakeServer({ validToken: () => fresh, token: () => tokenMode });
    const results = await Promise.all([refreshSession(), refreshSession(), refreshSession()]);
    expect(results).toEqual(["ok", "ok", "ok"]);
    expect(calls.tokenRequests).toBe(1);

    // ...and a later expiry can refresh again.
    await refreshSession();
    expect(calls.tokenRequests).toBe(2);
  });

  it("reports a rejected refresh token without touching the stored session", async () => {
    tokenMode = "rejected";
    installFakeServer({ validToken: () => fresh, token: () => tokenMode });
    const before = getSession();
    expect(await refreshSession()).toBe("rejected");
    expect(getSession()).toEqual(before);
  });

  it("treats an unreachable token endpoint as offline, not as a sign-out", async () => {
    tokenMode = "offline";
    installFakeServer({ validToken: () => fresh, token: () => tokenMode });
    expect(await refreshSession()).toBe("offline");
  });

  it("cannot refresh without a refresh token", async () => {
    saveSession({ idToken: makeToken(-10) });
    const { calls } = installFakeServer({ validToken: () => fresh, token: () => tokenMode });
    expect(await refreshSession()).toBe("rejected");
    expect(calls.tokenRequests).toBe(0);
  });
});
