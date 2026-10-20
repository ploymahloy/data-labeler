import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { ImageDetail } from "../api/types";

export type Phase = "loading" | "ready" | "empty" | "lost" | "error";

/**
 * Claims images one at a time from a server-side queue (annotation or review).
 *
 * The server hands out each image to exactly one user; asking again while holding a lease
 * returns the same image, so `claim` is safe to call repeatedly (e.g. after a page refresh).
 * When the page unmounts the current lease is released so others can pick the image up
 * immediately instead of waiting for it to expire.
 */
export const useWorkQueue = (
  fetchNext: () => Promise<ImageDetail | null>,
  onLoaded?: (image: ImageDetail) => void,
) => {
  const [image, setImage] = useState<ImageDetail | null>(null);
  const [phase, setPhase] = useState<Phase>("loading");
  const [error, setError] = useState<string | null>(null);
  const activeId = useRef<string | null>(null);
  const onLoadedRef = useRef(onLoaded);
  useEffect(() => {
    onLoadedRef.current = onLoaded;
  });

  const claim = useCallback(async () => {
    setPhase("loading");
    setError(null);
    try {
      const next = await fetchNext();
      activeId.current = next?.id ?? null;
      setImage(next);
      if (!next) {
        setPhase("empty");
        return;
      }
      onLoadedRef.current?.(next);
      setPhase("ready");
    } catch (e) {
      activeId.current = null;
      setImage(null);
      setError(e instanceof Error ? e.message : "Could not load the next image");
      setPhase("error");
    }
  }, [fetchNext]);

  useEffect(() => {
    void claim();
  }, [claim]);

  useEffect(
    () => () => {
      if (activeId.current) void api.release(activeId.current).catch(() => undefined);
    },
    [],
  );

  const markLost = useCallback(() => {
    activeId.current = null; // nothing left to release
    setPhase("lost");
  }, []);

  /** Call after the current image has been handed off (submitted / reviewed / skipped). */
  const handedOff = useCallback(() => {
    activeId.current = null;
  }, []);

  return { image, phase, error, claim, markLost, handedOff };
};
