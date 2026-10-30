import { createRemoteJWKSet, jwtVerify } from "jose";
import type { Env } from "./env";

const keySets = new Map<string, ReturnType<typeof createRemoteJWKSet>>();

function keySet(teamDomain: string) {
  let set = keySets.get(teamDomain);
  if (!set) {
    set = createRemoteJWKSet(new URL(`${teamDomain}/cdn-cgi/access/certs`));
    keySets.set(teamDomain, set);
  }
  return set;
}

export async function verifiedOwner(request: Request, env: Env): Promise<string | null> {
  const assertion = request.headers.get("Cf-Access-Jwt-Assertion");
  if (!assertion) return null;
  try {
    const { payload } = await jwtVerify(assertion, keySet(env.ACCESS_TEAM_DOMAIN), {
      issuer: env.ACCESS_TEAM_DOMAIN,
      audience: env.ACCESS_AUD
    });
    const email = typeof payload.email === "string" ? payload.email.toLowerCase() : "";
    return email !== "" && email === env.OWNER_EMAIL.toLowerCase() ? email : null;
  } catch {
    return null;
  }
}
