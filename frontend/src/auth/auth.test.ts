import { describe, expect, it } from "vitest";
import { hasRole, isExpired, parseIdentity } from "./jwt";
import { buildAuthorizeUrl, codeChallenge, randomString } from "./pkce";

// JWT payloads are UTF-8 JSON, base64url encoded.
const b64url = (obj: unknown) =>
  Buffer.from(JSON.stringify(obj), "utf8").toString("base64url");

const token = (payload: Record<string, unknown>) => `${b64url({ alg: "none" })}.${b64url(payload)}.sig`;

describe("parseIdentity", () => {
  it("reads sub, email, exp and only known roles from cognito:groups", () => {
    const identity = parseIdentity(
      token({ sub: "u1", email: "a@b.c", exp: 2_000_000_000, "cognito:groups": ["labeler", "bogus", "reviewer"] }),
    );
    expect(identity).toEqual({ sub: "u1", email: "a@b.c", roles: ["labeler", "reviewer"], exp: 2_000_000_000 });
  });

  it("rejects malformed or incomplete tokens", () => {
    expect(parseIdentity("nope")).toBeNull();
    expect(parseIdentity(token({ email: "x" }))).toBeNull();
  });

  it("handles non-ASCII payloads", () => {
    const identity = parseIdentity(token({ sub: "u", email: "zoë@example.com", exp: 1 }));
    expect(identity?.email).toBe("zoë@example.com");
  });
});

describe("roles and expiry", () => {
  const base = { sub: "u", email: "", exp: 1000 };

  it("admins act in every role", () => {
    expect(hasRole({ ...base, roles: ["admin"] }, "reviewer")).toBe(true);
    expect(hasRole({ ...base, roles: ["labeler"] }, "reviewer")).toBe(false);
    expect(hasRole({ ...base, roles: ["labeler"] }, "labeler", "reviewer")).toBe(true);
    expect(hasRole(null, "labeler")).toBe(false);
  });

  it("treats tokens within the skew window as expired", () => {
    const id = { ...base, roles: [], exp: 100 };
    expect(isExpired(id, 69_000, 30)).toBe(false);
    expect(isExpired(id, 71_000, 30)).toBe(true);
  });
});

describe("PKCE", () => {
  it("computes the RFC 7636 appendix B S256 challenge", async () => {
    expect(await codeChallenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")).toBe(
      "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
    );
  });

  it("generates URL-safe random strings", () => {
    const a = randomString(32);
    expect(a).toMatch(/^[A-Za-z0-9_-]+$/);
    expect(a).not.toBe(randomString(32));
  });

  it("builds the Hosted UI authorize URL with PKCE parameters", () => {
    const url = new URL(
      buildAuthorizeUrl(
        { domain: "auth.example.com", clientId: "cid", redirectUri: "https://app/auth/callback" },
        "state-1",
        "challenge-1",
      ),
    );
    expect(url.origin + url.pathname).toBe("https://auth.example.com/oauth2/authorize");
    expect(url.searchParams.get("response_type")).toBe("code");
    expect(url.searchParams.get("code_challenge_method")).toBe("S256");
    expect(url.searchParams.get("state")).toBe("state-1");
    expect(url.searchParams.get("redirect_uri")).toBe("https://app/auth/callback");
  });
});
