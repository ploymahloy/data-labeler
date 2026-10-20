import { config } from "../config";
import { refreshTokens } from "./pkce";
import { getSession, saveSession } from "./sessionStore";

/**
 * ok: a new ID token is stored.
 * rejected: the refresh token is invalid/expired (or there is none), the user must sign in again.
 * offline: the token endpoint could not be reached; the session may still be fine, so keep it.
 */
export type RefreshOutcome = "ok" | "rejected" | "offline";

let inflight: Promise<RefreshOutcome> | null = null;

const doRefresh = async (): Promise<RefreshOutcome> => {
  const session = getSession();
  if (config.authMode !== "cognito" || !session?.refreshToken) return "rejected";
  try {
    const t = await refreshTokens(
      { domain: config.cognitoDomain, clientId: config.cognitoClientId, redirectUri: config.redirectUri },
      session.refreshToken,
    );
    // Cognito does not rotate refresh tokens, so keep ours.
    saveSession({ idToken: t.id_token, refreshToken: session.refreshToken });
    return "ok";
  } catch (e) {
    const status = (e as { status?: number }).status;
    return typeof status === "number" && status >= 400 && status < 500 ? "rejected" : "offline";
  }
};

/**
 * Single-flight: any number of concurrent callers (parallel requests that all got a 401, the
 * expiry timer, a tab becoming visible) share one token request.
 */
export const refreshSession = (): Promise<RefreshOutcome> => {
  inflight ??= doRefresh().finally(() => {
    inflight = null;
  });
  return inflight;
};
