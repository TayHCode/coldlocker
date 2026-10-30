import { AuthorizationError, CimdFetchError } from "@cloudflare/workers-oauth-provider";
import type { ConsentDescription } from "@cloudflare/workers-oauth-provider";
import { verifiedOwner } from "./access";
import type { Env } from "./env";

export const CATALOG_SCOPE = "catalog:read";

const PAGE_HEADERS = { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" };

const escape = (value: string) => value.replace(/[&<>"']/g, (char) => `&#${char.charCodeAt(0)};`);

function page(title: string, body: string, status = 200, headers?: Headers): Response {
  const merged = new Headers(headers);
  for (const [name, value] of Object.entries(PAGE_HEADERS)) merged.set(name, value);
  return new Response(
    `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">` +
      `<title>${escape(title)}</title><body style="font-family:system-ui;max-width:36rem;margin:3rem auto;padding:0 1rem">${body}</body>`,
    { status, headers: merged }
  );
}

function consentPage(details: ConsentDescription, handle: string): string {
  const origin = details.clientDomain
    ? `Published by <strong>${escape(details.clientDomain)}</strong>.`
    : "This app registered itself, so its name is not verified.";
  const loopback = details.redirectIsLoopback
    ? "<p><strong>This sends access to an app on your computer.</strong> Continue only if you just started signing in from it.</p>"
    : "";
  return (
    `<h1>Allow ${escape(details.clientName)} to read your agentlocker catalog?</h1>` +
    `<p>${origin} Access will be sent to <strong>${escape(details.redirectHost)}</strong>.</p>${loopback}` +
    `<p>It will be able to list and read your published skills, rules and routines. It cannot change anything or run anything.</p>` +
    `<form method="post"><input type="hidden" name="handle" value="${escape(handle)}">` +
    `<button name="decision" value="approve">Allow</button> <button name="decision" value="deny">Deny</button></form>`
  );
}

function authorizationFailure(error: unknown): Response {
  if (error instanceof AuthorizationError && error.redirectTo) {
    return new Response(null, { status: 302, headers: { Location: error.redirectTo } });
  }
  if (error instanceof AuthorizationError) {
    return page("Authorization failed", `<p>${escape(error.description)} Start again from your app.</p>`, 400);
  }
  if (error instanceof CimdFetchError) {
    return page("Authorization failed", "<p>This app could not be verified.</p>", 400);
  }
  throw error;
}

async function authorize(request: Request, env: Env): Promise<Response> {
  const owner = await verifiedOwner(request, env);
  if (!owner) {
    return page("Sign-in required", "<p>Sign in through Cloudflare Access as the catalog owner to authorize an app.</p>", 403);
  }
  const oauth = env.OAUTH_PROVIDER;
  try {
    if (request.method === "GET") {
      const authRequest = await oauth.parseAuthRequest(request);
      const details = await oauth.describeConsent(authRequest);
      const consent = await oauth.beginConsent(authRequest);
      return page("Authorize app", consentPage(details, consent.handle), 200, consent.headers);
    }
    if (request.method === "POST") {
      const form = await request.formData();
      const handle = String(form.get("handle") ?? "");
      if (form.get("decision") !== "approve") {
        const denied = await oauth.denyConsent(request, handle);
        denied.headers.set("Location", denied.redirectTo);
        return new Response(null, { status: 302, headers: denied.headers });
      }
      const approved = await oauth.approveConsent(request, handle, { scope: [CATALOG_SCOPE] });
      const { redirectTo } = await oauth.completeAuthorization({
        request: approved.request,
        userId: "owner",
        metadata: {},
        scope: [CATALOG_SCOPE],
        props: {}
      });
      approved.headers.set("Location", redirectTo);
      return new Response(null, { status: 302, headers: approved.headers });
    }
    return new Response("Method not allowed", { status: 405, headers: { Allow: "GET, POST" } });
  } catch (error) {
    return authorizationFailure(error);
  }
}

export const defaultHandler: ExportedHandler<Env> = {
  async fetch(request, env) {
    const { pathname } = new URL(request.url);
    if (pathname === "/authorize") return authorize(request, env);
    if (pathname === "/") {
      return page(
        "agentlocker catalog",
        "<h1>agentlocker catalog</h1><p>A read-only MCP server for one owner's skills, rules and routines. " +
          "Point an MCP client at <code>/mcp</code>; it will walk you through authorization.</p>"
      );
    }
    return new Response("Not found", { status: 404 });
  }
};
