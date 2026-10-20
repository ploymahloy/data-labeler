import { vi } from "vitest";

const b64url = (obj: unknown) => Buffer.from(JSON.stringify(obj), "utf8").toString("base64url");

/** An unsigned JWT; the SPA only decodes it (the API verifies signatures). */
export const makeToken = (expiresInSeconds: number, extra: Record<string, unknown> = {}): string =>
  `${b64url({ alg: "none" })}.${b64url({
    sub: "u1",
    email: "u1@example.com",
    "cognito:groups": ["labeler"],
    exp: Math.floor(Date.now() / 1000) + expiresInSeconds,
    ...extra,
  })}.sig`;

export type TokenEndpoint = "ok" | "rejected" | "offline";

/**
 * Stand-in for the network: a Cognito token endpoint and an API that only accepts `validToken`.
 * `calls` records everything so tests can assert on how many refreshes happened.
 */
export const installFakeServer = (opts: {
  validToken: () => string;
  token: () => TokenEndpoint;
  /** Token the endpoint hands out; defaults to the one the API accepts. */
  issuedToken?: () => string;
}) => {
  const calls = { api: [] as { path: string; auth: string | null }[], tokenRequests: 0 };

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/oauth2/token")) {
      calls.tokenRequests += 1;
      await Promise.resolve(); // let concurrent callers pile up behind one in-flight request
      const mode = opts.token();
      if (mode === "offline") throw new TypeError("Failed to fetch");
      if (mode === "rejected") return new Response(JSON.stringify({ error: "invalid_grant" }), { status: 400 });
      return new Response(JSON.stringify({ id_token: (opts.issuedToken ?? opts.validToken)(), expires_in: 3600 }), { status: 200 });
    }
    const auth = new Headers(init?.headers).get("Authorization");
    calls.api.push({ path: url, auth });
    if (auth !== `Bearer ${opts.validToken()}`) {
      return new Response(JSON.stringify({ detail: "Invalid token: Signature has expired" }), { status: 401 });
    }
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, fetchMock };
};
