import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { takeDraft } from "../lib/draftStash";
import { useCanvasStore } from "../store/canvasStore";
import { installFakeServer, makeToken } from "../test/fakeAuthServer";
import type { TokenEndpoint } from "../test/fakeAuthServer";
import { AuthProvider, useAuth } from "./AuthContext";
import { consumeReturnPath } from "./returnTo";
import { getSession, saveSession } from "./sessionStore";

const Probe = () => {
  const { identity, ready } = useAuth();
  return <p data-testid="who">{ready ? (identity?.email ?? "signed-out") : "loading"}</p>;
};

const setVisibility = (state: "visible" | "hidden") =>
  Object.defineProperty(document, "visibilityState", { value: state, configurable: true });

describe("AuthProvider token lifecycle", () => {
  let fresh: string;
  let tokenMode: TokenEndpoint;

  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    fresh = makeToken(3600, { jti: "fresh" });
    tokenMode = "ok";
    sessionStorage.clear();
    window.history.pushState({}, "", "/batches/b1/label");
    setVisibility("visible");
  });
  afterEach(() => {
    saveSession(null);
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  const mount = async () => {
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
  };

  it("refreshes shortly before expiry", async () => {
    saveSession({ idToken: makeToken(90, { jti: "old" }), refreshToken: "r" });
    const { calls } = installFakeServer({ validToken: () => fresh, token: () => tokenMode });
    await mount();
    expect(calls.tokenRequests).toBe(0);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(31_000); // 90s token, refresh 60s early
    });
    expect(calls.tokenRequests).toBe(1);
    expect(getSession()?.idToken).toBe(fresh);
  });

  it("refreshes when a background tab wakes up after its timer was throttled", async () => {
    saveSession({ idToken: makeToken(600, { jti: "old" }), refreshToken: "r" });
    const { calls } = installFakeServer({ validToken: () => fresh, token: () => tokenMode });
    await mount();

    // The clock moves on but the (throttled) timer never fired.
    vi.setSystemTime(Date.now() + 580_000);
    expect(calls.tokenRequests).toBe(0);
    setVisibility("hidden");
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(calls.tokenRequests).toBe(0); // hidden: nothing to do yet

    setVisibility("visible");
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(calls.tokenRequests).toBe(1);
    expect(getSession()?.idToken).toBe(fresh);
  });

  it("does not refresh on visibility change while the token is still comfortably valid", async () => {
    saveSession({ idToken: makeToken(3000), refreshToken: "r" });
    const { calls } = installFakeServer({ validToken: () => fresh, token: () => tokenMode });
    await mount();
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(calls.tokenRequests).toBe(0);
  });

  it("signs in transparently when the stored token already expired", async () => {
    saveSession({ idToken: makeToken(-30, { jti: "old" }), refreshToken: "r" });
    installFakeServer({ validToken: () => fresh, token: () => tokenMode });
    await mount();
    expect(screen.getByTestId("who").textContent).toBe("u1@example.com");
  });

  it("keeps unsaved boxes and the current page when the refresh token is rejected", async () => {
    tokenMode = "rejected";
    useCanvasStore.getState().load([], "img-1");
    useCanvasStore.getState().add({ id: "b1", label: "car", x: 0.1, y: 0.1, w: 0.2, h: 0.2 });
    saveSession({ idToken: makeToken(-30), refreshToken: "r" });
    installFakeServer({ validToken: () => fresh, token: () => tokenMode });
    await mount();

    expect(screen.getByTestId("who").textContent).toBe("signed-out");
    expect(getSession()).toBeNull();
    expect(consumeReturnPath()).toBe("/batches/b1/label");
    expect(takeDraft("other-image")).toBeNull();
    expect(takeDraft("img-1")).toEqual([{ id: "b1", label: "car", x: 0.1, y: 0.1, w: 0.2, h: 0.2 }]);
  });

  it("stays signed in and retries when the token endpoint is unreachable", async () => {
    tokenMode = "offline";
    saveSession({ idToken: makeToken(-30), refreshToken: "r" });
    const { calls } = installFakeServer({ validToken: () => fresh, token: () => tokenMode });
    await mount();
    expect(getSession()).not.toBeNull();
    expect(calls.tokenRequests).toBe(1);

    tokenMode = "ok";
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_500);
    });
    expect(calls.tokenRequests).toBe(2);
    expect(getSession()?.idToken).toBe(fresh);
  });
});
