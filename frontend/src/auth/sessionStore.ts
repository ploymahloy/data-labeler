import type { Session } from "./jwt";

const SESSION_KEY = "labeler.session";

const read = (): Session | null => {
  try {
    const raw = localStorage.getItem(SESSION_KEY);
    return raw ? (JSON.parse(raw) as Session) : null;
  } catch {
    return null;
  }
};

let current: Session | null = read();
const listeners = new Set<(session: Session | null) => void>();

export const getSession = (): Session | null => current;

/** Token attached to API calls. Kept outside React so requests never race a render. */
export const getIdToken = (): string | null => current?.idToken ?? null;

export const saveSession = (next: Session | null): void => {
  current = next;
  if (next) localStorage.setItem(SESSION_KEY, JSON.stringify(next));
  else localStorage.removeItem(SESSION_KEY);
  listeners.forEach((listener) => listener(next));
};

/** Lets React state follow session changes made outside components (e.g. a 401 refresh). */
export const subscribeSession = (listener: (session: Session | null) => void): (() => void) => {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
};
