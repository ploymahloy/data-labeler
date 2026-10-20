import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { makeApiError } from "../api/client";
import { useLeaseHeartbeat } from "./useLeaseHeartbeat";

describe("useLeaseHeartbeat", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("beats on the interval and stops after unmount", async () => {
    const calls: string[] = [];
    const beat = async (id: string) => {
      calls.push(id);
    };
    const { unmount } = renderHook(() => useLeaseHeartbeat("img-1", beat, () => undefined, 1000));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3100);
    });
    expect(calls).toEqual(["img-1", "img-1", "img-1"]);
    unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(calls).toHaveLength(3);
  });

  it("reports a lost lease once and stops beating", async () => {
    let beats = 0;
    let lost = 0;
    const beat = async () => {
      beats += 1;
      throw makeApiError(409, "lease_lost", "gone");
    };
    renderHook(() =>
      useLeaseHeartbeat(
        "img-1",
        beat,
        () => {
          lost += 1;
        },
        1000,
      ),
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(lost).toBe(1);
    expect(beats).toBe(1);
  });

  it("keeps trying through transient failures", async () => {
    let beats = 0;
    let lost = 0;
    const beat = async () => {
      beats += 1;
      throw makeApiError(503, "error", "unavailable");
    };
    renderHook(() =>
      useLeaseHeartbeat(
        "img-1",
        beat,
        () => {
          lost += 1;
        },
        1000,
      ),
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3100);
    });
    expect(beats).toBe(3);
    expect(lost).toBe(0);
  });

  it("does nothing without an image", async () => {
    let beats = 0;
    renderHook(() =>
      useLeaseHeartbeat(
        null,
        async () => {
          beats += 1;
        },
        () => undefined,
        1000,
      ),
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(beats).toBe(0);
  });
});
