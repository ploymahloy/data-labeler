import { useEffect, useRef } from "react";
import { isLeaseLost } from "../api/client";
import { HEARTBEAT_INTERVAL_MS } from "../config";

/**
 * Keeps the server-side lease on an image alive while the user works on it.
 *
 * - Beats every `intervalMs` and immediately when the tab becomes visible again (laptop wake,
 *   background-tab throttling).
 * - A "lease lost" answer (409) stops the heartbeat and calls `onLost` exactly once.
 * - Transient failures (offline, 5xx) are ignored; the next beat retries.
 */
export const useLeaseHeartbeat = (
  imageId: string | null,
  beat: (imageId: string) => Promise<unknown>,
  onLost: () => void,
  intervalMs: number = HEARTBEAT_INTERVAL_MS,
): void => {
  const beatRef = useRef(beat);
  const onLostRef = useRef(onLost);
  useEffect(() => {
    beatRef.current = beat;
    onLostRef.current = onLost;
  });

  useEffect(() => {
    if (!imageId) return undefined;
    let stopped = false;

    const tick = async () => {
      if (stopped) return;
      try {
        await beatRef.current(imageId);
      } catch (e) {
        if (isLeaseLost(e) && !stopped) {
          stopped = true;
          onLostRef.current();
        }
      }
    };

    const timer = window.setInterval(() => void tick(), intervalMs);
    const onVisible = () => {
      if (document.visibilityState === "visible") void tick();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      stopped = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [imageId, intervalMs]);
};
