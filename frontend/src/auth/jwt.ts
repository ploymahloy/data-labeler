import type { Role } from "../api/types";

export type Session = {
  idToken: string;
  refreshToken?: string;
};

export type Identity = {
  sub: string;
  email: string;
  roles: Role[];
  /** Expiry as epoch seconds. */
  exp: number;
};

const KNOWN_ROLES: Role[] = ["admin", "labeler", "reviewer"];

const base64UrlDecode = (input: string): string => {
  const padded = input.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(input.length / 4) * 4, "=");
  const bytes = Uint8Array.from(atob(padded), (c) => c.charCodeAt(0));
  return new TextDecoder().decode(bytes);
};

/** Decodes (does NOT verify) the JWT payload; the API verifies signatures. */
export const parseIdentity = (token: string): Identity | null => {
  try {
    const payload = JSON.parse(base64UrlDecode(token.split(".")[1] ?? "")) as Record<string, unknown>;
    const groups = Array.isArray(payload["cognito:groups"]) ? (payload["cognito:groups"] as string[]) : [];
    if (typeof payload.sub !== "string" || typeof payload.exp !== "number") return null;
    return {
      sub: payload.sub,
      email: typeof payload.email === "string" ? payload.email : "",
      roles: groups.filter((g): g is Role => (KNOWN_ROLES as string[]).includes(g)),
      exp: payload.exp,
    };
  } catch {
    return null;
  }
};

export const isExpired = (identity: Identity, nowMs: number = Date.now(), skewSeconds = 30): boolean =>
  identity.exp * 1000 - skewSeconds * 1000 <= nowMs;

/** Admins act in every role (mirrors the API). */
export const hasRole = (identity: Identity | null, ...roles: Role[]): boolean =>
  !!identity && (identity.roles.includes("admin") || roles.some((r) => identity.roles.includes(r)));
