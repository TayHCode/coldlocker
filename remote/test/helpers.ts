import { createExecutionContext, waitOnExecutionContext } from "cloudflare:test";
import { SignJWT, exportJWK, generateKeyPair } from "jose";
import type { CryptoKey } from "jose";
import { vi } from "vitest";
import worker from "../src/index";
import type { Index } from "../src/catalog";
import type { Env } from "../src/env";
import golden from "./golden/parity.json";

export const ORIGIN = "https://catalog.test";
export const REDIRECT = "https://client.test/callback";
const KEY_ID = "test-key";

const encoder = new TextEncoder();

export async function sha256Hex(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", encoder.encode(text));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function base64Url(bytes: ArrayBuffer | Uint8Array): string {
  return btoa(String.fromCharCode(...new Uint8Array(bytes)))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

export interface GoldenCase {
  name: string;
  request: unknown;
  expected: unknown;
}

export interface GoldenFixture {
  name: string;
  revision: string;
  index: Index;
  blobs: Record<string, unknown>;
  cases: GoldenCase[];
}

export const goldenFixtures: GoldenFixture[] = golden.fixtures as unknown as GoldenFixture[];

export const fixtureByName = (name: string): GoldenFixture => {
  const found = goldenFixtures.find((fixture) => fixture.name === name);
  if (!found) throw new Error(`Unknown golden fixture: ${name}`);
  return found;
};

export function expandRepeats(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(expandRepeats);
  if (typeof value === "object" && value !== null) {
    const record = value as Record<string, unknown>;
    if (typeof record.$repeat === "string" && typeof record.count === "number") return record.$repeat.repeat(record.count);
    return Object.fromEntries(Object.entries(record).map(([key, item]) => [key, expandRepeats(item)]));
  }
  return value;
}

export async function seedFixture(env: Env, fixture: GoldenFixture): Promise<void> {
  for (const [digest, text] of Object.entries(fixture.blobs)) await env.CATALOG_KV.put(`blob:${digest}`, expandRepeats(text) as string);
  await env.CATALOG_KV.put(`rev:${fixture.revision}`, JSON.stringify(fixture.index));
  await env.CATALOG_KV.put("current", JSON.stringify({ revision: fixture.revision }));
}

export const seedCatalog = (env: Env) => seedFixture(env, fixtureByName("main"));

let signingKey: CryptoKey;

export async function installAccessCerts(env: Env): Promise<void> {
  const { publicKey, privateKey } = await generateKeyPair("RS256");
  signingKey = privateKey;
  const jwk = { ...(await exportJWK(publicKey)), kid: KEY_ID, alg: "RS256", use: "sig" };
  const real = globalThis.fetch;
  vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const url = input instanceof Request ? input.url : String(input);
    if (url === `${env.ACCESS_TEAM_DOMAIN}/cdn-cgi/access/certs`) return Promise.resolve(Response.json({ keys: [jwk] }));
    return real(input, init);
  });
}

export async function accessJwt(env: Env, overrides: { email?: string; aud?: string; iss?: string } = {}): Promise<string> {
  return new SignJWT({ email: overrides.email ?? env.OWNER_EMAIL })
    .setProtectedHeader({ alg: "RS256", kid: KEY_ID })
    .setIssuer(overrides.iss ?? env.ACCESS_TEAM_DOMAIN)
    .setAudience(overrides.aud ?? env.ACCESS_AUD)
    .setIssuedAt()
    .setExpirationTime("5m")
    .sign(signingKey);
}

export async function send(env: Env, request: Request): Promise<Response> {
  const ctx = createExecutionContext();
  const response = await worker.fetch(request, env, ctx);
  await waitOnExecutionContext(ctx);
  return response;
}

export const get = (env: Env, path: string, init?: RequestInit) => send(env, new Request(`${ORIGIN}${path}`, init));

export async function register(env: Env): Promise<{ client_id: string }> {
  const response = await get(env, "/oauth/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      client_name: "Test <b>Client</b>",
      redirect_uris: [REDIRECT],
      token_endpoint_auth_method: "none",
      grant_types: ["authorization_code", "refresh_token"],
      response_types: ["code"]
    })
  });
  return response.json();
}

export async function pkcePair(): Promise<{ verifier: string; challenge: string }> {
  const verifier = base64Url(crypto.getRandomValues(new Uint8Array(32)));
  const challenge = base64Url(await crypto.subtle.digest("SHA-256", encoder.encode(verifier)));
  return { verifier, challenge };
}

export function authorizeUrl(clientId: string, challenge: string): string {
  const params = new URLSearchParams({
    response_type: "code",
    client_id: clientId,
    redirect_uri: REDIRECT,
    scope: "catalog:read",
    state: "state-123",
    code_challenge: challenge,
    code_challenge_method: "S256",
    resource: `${ORIGIN}/mcp`
  });
  return `/authorize?${params}`;
}

export function cookiesFrom(response: Response): string {
  return response.headers
    .getSetCookie()
    .map((cookie) => cookie.split(";")[0])
    .join("; ");
}

export async function completeFlow(env: Env): Promise<{ accessToken: string; refreshToken: string; clientId: string }> {
  const { client_id: clientId } = await register(env);
  const { verifier, challenge } = await pkcePair();
  const jwt = await accessJwt(env);
  const page = await get(env, authorizeUrl(clientId, challenge), { headers: { "Cf-Access-Jwt-Assertion": jwt } });
  const handle = /name="handle" value="([^"]+)"/.exec(await page.text())?.[1] ?? "";
  const approve = await get(env, authorizeUrl(clientId, challenge), {
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
      redirect_uri: REDIRECT,
      client_id: clientId,
      code_verifier: verifier,
      resource: `${ORIGIN}/mcp`
    })
  });
  const body = (await token.json()) as { access_token: string; refresh_token: string };
  return { accessToken: body.access_token, refreshToken: body.refresh_token, clientId };
}

export async function rpc(env: Env, accessToken: string, method: string, params: unknown = {}, id = 1): Promise<any> {
  const response = await mcpPost(env, accessToken, { jsonrpc: "2.0", id, method, params });
  return response.json();
}

export const mcpPost = (env: Env, accessToken: string, message: unknown, extra: Record<string, string> = {}) =>
  get(env, "/mcp", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
      Accept: "application/json, text/event-stream",
      ...extra
    },
    body: JSON.stringify(message)
  });
