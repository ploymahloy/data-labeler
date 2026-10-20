const KEY = "labeler.returnTo";
const MAX_AGE_MS = 60 * 60 * 1000;

/** Remember where the user was so signing back in lands them there (sessionStorage: per tab). */
export const rememberReturnPath = (path: string = window.location.pathname + window.location.search): void => {
  if (path.startsWith("/login") || path.startsWith("/auth/")) return;
  sessionStorage.setItem(KEY, JSON.stringify({ path, at: Date.now() }));
};

/** The remembered path (or "/") without clearing it. Only same-origin absolute paths are honoured. */
export const peekReturnPath = (): string => {
  const raw = sessionStorage.getItem(KEY);
  if (!raw) return "/";
  try {
    const { path, at } = JSON.parse(raw) as { path: string; at: number };
    const safe = typeof path === "string" && path.startsWith("/") && !path.startsWith("//");
    return safe && Date.now() - at < MAX_AGE_MS ? path : "/";
  } catch {
    return "/";
  }
};

/** Single use. */
export const consumeReturnPath = (): string => {
  const path = peekReturnPath();
  sessionStorage.removeItem(KEY);
  return path;
};
