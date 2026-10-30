import OAuthProvider, { insufficientScope } from "@cloudflare/workers-oauth-provider";
import { CATALOG_SCOPE, defaultHandler } from "./authorize";
import type { Env } from "./env";
import { handleMcp } from "./mcp";

const apiHandler: ExportedHandler<Env> & Required<Pick<ExportedHandler<Env>, "fetch">> = {
  async fetch(request, env, ctx) {
    const auth = (ctx as unknown as { auth: Parameters<typeof insufficientScope>[0] }).auth;
    if (!auth.scope.includes(CATALOG_SCOPE)) return insufficientScope(auth, [CATALOG_SCOPE]);
    return handleMcp(request, env);
  }
};

const providers = new Map<string, OAuthProvider<Env>>();

function providerFor(origin: string): OAuthProvider<Env> {
  let provider = providers.get(origin);
  if (!provider) {
    provider = new OAuthProvider<Env>({
      apiRoute: "/mcp",
      apiHandler,
      defaultHandler,
      authorizeEndpoint: "/authorize",
      tokenEndpoint: "/oauth/token",
      clientRegistrationEndpoint: "/oauth/register",
      scopesSupported: [CATALOG_SCOPE],
      requiredScopes: [CATALOG_SCOPE],
      resourceMetadata: {
        resource: `${origin}/mcp`,
        authorization_servers: [origin],
        resource_name: "agentlocker catalog"
      },
      clientIdMetadataDocumentEnabled: true,
      accessTokenTTL: 3600
    });
    providers.set(origin, provider);
  }
  return provider;
}

const BARE_RESOURCE_METADATA = "/.well-known/oauth-protected-resource";

function withSuffixedMetadataPath(request: Request): Request {
  const url = new URL(request.url);
  if (url.pathname !== BARE_RESOURCE_METADATA) return request;
  url.pathname = `${BARE_RESOURCE_METADATA}/mcp`;
  return new Request(url, request);
}

export default {
  fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    return providerFor(new URL(env.PUBLIC_ORIGIN).origin).fetch(withSuffixedMetadataPath(request), env, ctx);
  }
} satisfies ExportedHandler<Env>;
