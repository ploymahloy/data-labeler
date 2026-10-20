import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { api, setUnauthorizedHandler } from "../api/client";
import type { Role } from "../api/types";
import { config } from "../config";
import { parseIdentity } from "./jwt";
import type { Identity, Session } from "./jwt";
import { buildAuthorizeUrl, codeChallenge, exchangeCode, randomString, refreshTokens } from "./pkce";
import { getSession, saveSession } from "./sessionStore";

const PKCE_KEY = "labeler.pkce";

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

export const AuthProvider = ({ children }: { children: ReactNode }) => {
  const [session, setSession] = useState<Session | null>(() => getSession());
  const [ready, setReady] = useState(false);

  const persist = useCallback((next: Session | null) => {
    saveSession(next);
    setSession(next);
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
    setUnauthorizedHandler(() => persist(null));
  }, [persist]);

  // Refresh the ID token shortly before it expires (Cognito mode); otherwise sign out on expiry.
  useEffect(() => {
    const parsed = session ? parseIdentity(session.idToken) : null;
    if (!session || !parsed) {
      if (session) persist(null);
      setReady(true);
      return undefined;
    }
    const refresh = async () => {
      if (!session.refreshToken || config.authMode !== "cognito") {
        persist(null);
        return;
      }
      try {
        const t = await refreshTokens(cognito(), session.refreshToken);
        persist({ idToken: t.id_token, refreshToken: session.refreshToken });
      } catch {
        persist(null);
      }
    };
    const msLeft = parsed.exp * 1000 - Date.now();
    if (msLeft <= 0) {
      void refresh().finally(() => setReady(true));
      return undefined;
    }
    setReady(true);
    const timer = window.setTimeout(() => void refresh(), Math.max(msLeft - 60_000, 1_000));
    return () => window.clearTimeout(timer);
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
