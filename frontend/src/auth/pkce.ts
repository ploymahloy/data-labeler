const base64Url = (bytes: Uint8Array): string =>
  btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

export const randomString = (byteLength = 32): string =>
  base64Url(crypto.getRandomValues(new Uint8Array(byteLength)));

/** RFC 7636 S256 code challenge. */
export const codeChallenge = async (verifier: string): Promise<string> => {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return base64Url(new Uint8Array(digest));
};

type CognitoEndpoints = { domain: string; clientId: string; redirectUri: string };

export const buildAuthorizeUrl = (
  { domain, clientId, redirectUri }: CognitoEndpoints,
  state: string,
  challenge: string,
): string => {
  const params = new URLSearchParams({
    response_type: "code",
    client_id: clientId,
    redirect_uri: redirectUri,
    scope: "openid email profile",
    state,
    code_challenge: challenge,
    code_challenge_method: "S256",
  });
  return `https://${domain}/oauth2/authorize?${params.toString()}`;
};

export type TokenResponse = { id_token: string; refresh_token?: string; expires_in: number };

const tokenRequest = async (domain: string, form: Record<string, string>): Promise<TokenResponse> => {
  const res = await fetch(`https://${domain}/oauth2/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams(form).toString(),
  });
  if (!res.ok) throw new Error(`Token request failed (${res.status})`);
  return (await res.json()) as TokenResponse;
};

export const exchangeCode = (
  { domain, clientId, redirectUri }: CognitoEndpoints,
  code: string,
  verifier: string,
): Promise<TokenResponse> =>
  tokenRequest(domain, {
    grant_type: "authorization_code",
    client_id: clientId,
    code,
    redirect_uri: redirectUri,
    code_verifier: verifier,
  });

export const refreshTokens = (
  { domain, clientId }: CognitoEndpoints,
  refreshToken: string,
): Promise<TokenResponse> =>
  tokenRequest(domain, { grant_type: "refresh_token", client_id: clientId, refresh_token: refreshToken });
