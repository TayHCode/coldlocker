import { env } from "cloudflare:test";
import { beforeAll, describe, expect, it } from "vitest";
import {
  ORIGIN,
  accessJwt,
  authorizeUrl,
  completeFlow,
  cookiesFrom,
  get,
  installAccessCerts,
  pkcePair,
  register,
  seedCatalog
} from "./helpers";

beforeAll(async () => {
  await seedCatalog(env);
  await installAccessCerts(env);
});

describe("discovery and challenge", () => {
  it("rejects an unauthenticated /mcp request with a resource metadata challenge", async () => {
    const response = await get(env, "/mcp", { method: "POST", body: "{}" });
    expect(response.status).toBe(401);
    const challenge = response.headers.get("WWW-Authenticate") ?? "";
    expect(challenge).toMatch(/^Bearer/);
    expect(challenge).toContain(`resource_metadata="${ORIGIN}/.well-known/oauth-protected-resource/mcp"`);
  });

  it("rejects a garbage bearer token", async () => {
    const response = await get(env, "/mcp", { method: "POST", headers: { Authorization: "Bearer nope" }, body: "{}" });
    expect(response.status).toBe(401);
  });

  it("publishes protected resource metadata for the MCP endpoint", async () => {
    const response = await get(env, "/.well-known/oauth-protected-resource/mcp");
    expect(response.status).toBe(200);
    const metadata = (await response.json()) as Record<string, unknown>;
    expect(metadata.resource).toBe(`${ORIGIN}/mcp`);
    expect(metadata.authorization_servers).toEqual([ORIGIN]);
    expect(metadata.scopes_supported).toEqual(["catalog:read"]);
  });

  it("serves the same protected resource metadata at the bare well-known path", async () => {
    const bare = await get(env, "/.well-known/oauth-protected-resource");
    expect(bare.status).toBe(200);
    expect(bare.headers.get("content-type")).toContain("application/json");
    const suffixed = await get(env, "/.well-known/oauth-protected-resource/mcp");
    expect(await bare.json()).toEqual(await suffixed.json());
  });

  it("publishes authorization server metadata with PKCE S256 and registration", async () => {
    const response = await get(env, "/.well-known/oauth-authorization-server");
    expect(response.status).toBe(200);
    const metadata = (await response.json()) as Record<string, any>;
    expect(metadata.issuer).toBe(ORIGIN);
    expect(metadata.authorization_endpoint).toBe(`${ORIGIN}/authorize`);
    expect(metadata.token_endpoint).toBe(`${ORIGIN}/oauth/token`);
    expect(metadata.registration_endpoint).toBe(`${ORIGIN}/oauth/register`);
    expect(metadata.code_challenge_methods_supported).toEqual(["S256"]);
    expect(metadata.client_id_metadata_document_supported).toBe(true);
  });
});

describe("authorize", () => {
  it("refuses a request with no Access assertion", async () => {
    const { client_id } = await register(env);
    const { challenge } = await pkcePair();
    const response = await get(env, authorizeUrl(client_id, challenge));
    expect(response.status).toBe(403);
  });

  it("refuses an assertion for another email, audience or issuer", async () => {
    const { client_id } = await register(env);
    const { challenge } = await pkcePair();
    for (const overrides of [{ email: "intruder@example.com" }, { aud: "other-aud" }, { iss: "https://other.test" }]) {
      const jwt = await accessJwt(env, overrides);
      const response = await get(env, authorizeUrl(client_id, challenge), { headers: { "Cf-Access-Jwt-Assertion": jwt } });
      expect(response.status).toBe(403);
    }
  });

  it("refuses a malformed assertion", async () => {
    const { client_id } = await register(env);
    const { challenge } = await pkcePair();
    const response = await get(env, authorizeUrl(client_id, challenge), { headers: { "Cf-Access-Jwt-Assertion": "not.a.jwt" } });
    expect(response.status).toBe(403);
  });

  it("shows an escaped consent page with the redirect host", async () => {
    const { client_id } = await register(env);
    const { challenge } = await pkcePair();
    const jwt = await accessJwt(env);
    const response = await get(env, authorizeUrl(client_id, challenge), { headers: { "Cf-Access-Jwt-Assertion": jwt } });
    expect(response.status).toBe(200);
    const html = await response.text();
    expect(html).toContain("client.test");
    expect(html).not.toContain("<b>Client</b>");
    expect(html).toContain("&#60;b&#62;Client&#60;/b&#62;");
    expect(cookiesFrom(response)).not.toBe("");
  });

  it("redirects a denial back to the client with access_denied", async () => {
    const { client_id } = await register(env);
    const { challenge } = await pkcePair();
    const jwt = await accessJwt(env);
    const page = await get(env, authorizeUrl(client_id, challenge), { headers: { "Cf-Access-Jwt-Assertion": jwt } });
    const handle = /name="handle" value="([^"]+)"/.exec(await page.text())?.[1] ?? "";
    const denied = await get(env, authorizeUrl(client_id, challenge), {
      method: "POST",
      headers: { "Cf-Access-Jwt-Assertion": jwt, Cookie: cookiesFrom(page), "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ handle, decision: "deny" })
    });
    expect(denied.status).toBe(302);
    const location = new URL(denied.headers.get("Location") ?? "");
    expect(location.searchParams.get("error")).toBe("access_denied");
    expect(location.searchParams.get("state")).toBe("state-123");
  });

  it("does not issue a code when the approving POST lacks the Access assertion", async () => {
    const { client_id } = await register(env);
    const { challenge } = await pkcePair();
    const jwt = await accessJwt(env);
    const page = await get(env, authorizeUrl(client_id, challenge), { headers: { "Cf-Access-Jwt-Assertion": jwt } });
    const handle = /name="handle" value="([^"]+)"/.exec(await page.text())?.[1] ?? "";
    const response = await get(env, authorizeUrl(client_id, challenge), {
      method: "POST",
      headers: { Cookie: cookiesFrom(page), "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ handle, decision: "approve" })
    });
    expect(response.status).toBe(403);
    expect(response.headers.get("Location")).toBeNull();
  });
});

describe("token endpoint", () => {
  it("completes registration, PKCE authorization, token exchange and refresh", async () => {
    const { accessToken, refreshToken, clientId } = await completeFlow(env);
    expect(accessToken).toBeTruthy();
    expect(refreshToken).toBeTruthy();
    const refreshed = await get(env, "/oauth/token", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ grant_type: "refresh_token", refresh_token: refreshToken, client_id: clientId })
    });
    expect(refreshed.status).toBe(200);
    expect(((await refreshed.json()) as { access_token: string }).access_token).toBeTruthy();
  });

  it("rejects a wrong PKCE verifier", async () => {
    const { client_id } = await register(env);
    const { challenge } = await pkcePair();
    const jwt = await accessJwt(env);
    const page = await get(env, authorizeUrl(client_id, challenge), { headers: { "Cf-Access-Jwt-Assertion": jwt } });
    const handle = /name="handle" value="([^"]+)"/.exec(await page.text())?.[1] ?? "";
    const approve = await get(env, authorizeUrl(client_id, challenge), {
      method: "POST",
      headers: { "Cf-Access-Jwt-Assertion": jwt, Cookie: cookiesFrom(page), "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ handle, decision: "approve" })
    });
    const code = new URL(approve.headers.get("Location") ?? "").searchParams.get("code") ?? "";
    const token = await get(env, "/oauth/token", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        grant_type: "authorization_code",
        code,
        redirect_uri: "https://client.test/callback",
        client_id,
        code_verifier: "x".repeat(60),
        resource: `${ORIGIN}/mcp`
      })
    });
    expect(token.status).toBe(400);
  });
});
