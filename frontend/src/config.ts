export type AuthMode = "dev" | "cognito";

export const config = {
  apiUrl: (import.meta.env.VITE_API_URL as string | undefined) ?? "http://localhost:8000",
  authMode: ((import.meta.env.VITE_AUTH_MODE as string | undefined) ?? "cognito") as AuthMode,
  cognitoDomain: (import.meta.env.VITE_COGNITO_DOMAIN as string | undefined) ?? "",
  cognitoClientId: (import.meta.env.VITE_COGNITO_CLIENT_ID as string | undefined) ?? "",
  redirectUri:
    (import.meta.env.VITE_COGNITO_REDIRECT_URI as string | undefined) ??
    `${window.location.origin}/auth/callback`,
};

/** Lease heartbeat cadence; the server lease is 15 minutes. */
export const HEARTBEAT_INTERVAL_MS = 60_000;
/** Debounce for draft autosave while labeling. */
export const AUTOSAVE_DELAY_MS = 1_500;
