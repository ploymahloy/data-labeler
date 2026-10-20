import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { api, setUnauthorizedHandler } from "../api/client";
import type { Role } from "../api/types";
import { config } from "../config";
import { stashCanvasDraft } from "../lib/draftStash";
import { parseIdentity } from "./jwt";
import type { Identity, Session } from "./jwt";
import { buildAuthorizeUrl, codeChallenge, exchangeCode, randomString } from "./pkce";
import { refreshSession } from "./refresh";
import { rememberReturnPath } from "./returnTo";
import { getSession, saveSession, subscribeSession } from "./sessionStore";

const PKCE_KEY = "labeler.pkce";
/** Refresh this long before the ID token expires. */
const REFRESH_LEAD_MS = 60_000;
/** Retry delay when the token endpoint is unreachable (offline, flaky link). */
const OFFLINE_RETRY_MS = 15_000;

type AuthState = {
  identity: Identity | null;
  ready: boolean;
  /** Dev mode: log in with an email and roles. */
  loginDev: (email: string, roles: Role[]) => Promise<void>;
  /** Cognito mode: redirect to the Hosted UI (authorization code + PKCE). */
  loginCognito: () => Promise<void>;
  completeCognitoLogin: (code: string, state: string) => Promise<void>;
  logout: () => void;
};

const AuthContext = createContext<AuthState | null>(null);

const cognito = () => ({
  domain: config.cognitoDomain,
  clientId: config.cognitoClientId,
  redirectUri: config.redirectUri,
});

/** Forced sign-out (expired session): keep unsaved edits and the current page for re-login. */
const expireSession = (): void => {
  stashCanvasDraft();
  rememberReturnPath();
  saveSession(null);
};

export const AuthProvider = ({ children }: { children: ReactNode }) => {
  const [session, setSession] = useState<Session | null>(() => getSession());
  const [ready, setReady] = useState(false);

  // The API layer refreshes tokens on its own after a 401; follow whatever it stored.
  useEffect(() => subscribeSession(setSession), []);

  const persist = useCallback((next: Session | null) => {
    saveSession(next);
  }, []);

  const identity = useMemo(() => (session ? parseIdentity(session.idToken) : null), [session]);

  const logout = useCallback(() => {
    persist(null);
    if (config.authMode === "cognito" && config.cognitoDomain) {
      const params = new URLSearchParams({
        client_id: config.cognitoClientId,
        logout_uri: `${window.location.origin}/login`,
      });
      window.location.assign(`https://${config.cognitoDomain}/logout?${params.toString()}`);
    }
  }, [persist]);

  useEffect(() => {
    setUnauthorizedHandler(expireSession);
  }, []);

  // Keep the ID token fresh: refresh shortly before expiry, and again when a hidden tab wakes up
  // (browsers throttle timers in background tabs, so the timer alone can fire too late).
  // Without a refresh token (dev mode) an expired session simply ends.
  useEffect(() => {
    const parsed = session ? parseIdentity(session.idToken) : null;
    if (!session || !parsed) {
      if (session) persist(null);
      setReady(true);
      return undefined;
    }

    let timer: number | undefined;
    const msLeft = () => parsed.exp * 1000 - Date.now();

    const refresh = async (): Promise<void> => {
      const outcome = await refreshSession();
      if (outcome === "rejected") expireSession();
      else if (outcome === "offline") timer = window.setTimeout(() => void refresh(), OFFLINE_RETRY_MS);
      // "ok": the stored session changed, which re-runs this effect with the new expiry.
    };

    const onVisible = () => {
      if (document.visibilityState === "visible" && msLeft() <= REFRESH_LEAD_MS) {
        window.clearTimeout(timer);
        void refresh();
      }
    };

    if (msLeft() <= 0) {
      void refresh().finally(() => setReady(true));
    } else {
      setReady(true);
      timer = window.setTimeout(() => void refresh(), Math.max(msLeft() - REFRESH_LEAD_MS, 1_000));
    }
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [session, persist]);

  const loginDev = useCallback(
    async (email: string, roles: Role[]) => {
      const { token } = await api.devLogin(email, roles);
      persist({ idToken: token });
    },
    [persist],
  );

  const loginCognito = useCallback(async () => {
    const verifier = randomString(48);
    const state = randomString(16);
    sessionStorage.setItem(PKCE_KEY, JSON.stringify({ verifier, state }));
    const challenge = await codeChallenge(verifier);
    window.location.assign(buildAuthorizeUrl(cognito(), state, challenge));
  }, []);

  const completeCognitoLogin = useCallback(
    async (code: string, state: string) => {
      const stored = JSON.parse(sessionStorage.getItem(PKCE_KEY) ?? "null") as {
        verifier: string;
        state: string;
      } | null;
      sessionStorage.removeItem(PKCE_KEY);
      if (!stored || stored.state !== state) throw new Error("Login state mismatch; please try again");
      const t = await exchangeCode(cognito(), code, stored.verifier);
      persist({ idToken: t.id_token, refreshToken: t.refresh_token });
    },
    [persist],
  );

  const value = useMemo<AuthState>(
    () => ({ identity, ready, loginDev, loginCognito, completeCognitoLogin, logout }),
    [identity, ready, loginDev, loginCognito, completeCognitoLogin, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

export const useAuth = (): AuthState => {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
};
