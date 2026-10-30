import type { OAuthHelpers } from "@cloudflare/workers-oauth-provider";

interface AppEnv {
  OAUTH_KV: KVNamespace;
  CATALOG_KV: KVNamespace;
  OAUTH_PROVIDER: OAuthHelpers;
  PUBLIC_ORIGIN: string;
  ACCESS_TEAM_DOMAIN: string;
  ACCESS_AUD: string;
  OWNER_EMAIL: string;
}

export type Env = AppEnv;

declare global {
  namespace Cloudflare {
    interface Env extends AppEnv {}
  }
}
