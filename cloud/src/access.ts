import { createRemoteJWKSet, jwtVerify, type JWTVerifyGetKey } from "jose";

// Cloudflare Access login check. Fails closed: a missing setting, a missing or bad token all refuse.
// The only way past is the local-development bypass: ACCESS_DEV_BYPASS, which cloudflare.config.ts sets to "1"
// only when evaluated in mode "development" (`cf dev`) and to "" in builds and deploys, and which counts only
// when the request itself arrives on a loopback address, which no deployed address is.

export type AccessEnv = {
  TEAM_DOMAIN?: string; // https://<team>.cloudflareaccess.com
  POLICY_AUD?: string; // the Access application's Application Audience (AUD) tag
  ACCESS_DEV_BYPASS?: string;
};

const LOOPBACK = new Set(["localhost", "127.0.0.1", "[::1]"]);
const remote = new Map<string, JWTVerifyGetKey>();

function keysFor(teamDomain: string): JWTVerifyGetKey {
  let keys = remote.get(teamDomain);
  if (!keys) {
    // jose caches the fetched keys and refetches when a token names a key it does not hold.
    keys = createRemoteJWKSet(new URL(`${teamDomain}/cdn-cgi/access/certs`));
    remote.set(teamDomain, keys);
  }
  return keys;
}

/** Returns true when the request may use the API. `keys` is for tests. */
export async function accessAllows(request: Request, env: AccessEnv, keys?: JWTVerifyGetKey): Promise<boolean> {
  if (env.ACCESS_DEV_BYPASS === "1" && LOOPBACK.has(new URL(request.url).hostname)) return true;
  const team = (env.TEAM_DOMAIN ?? "").replace(/\/+$/, "");
  const aud = env.POLICY_AUD ?? "";
  if (!team || !aud) return false;
  const token = request.headers.get("cf-access-jwt-assertion");
  if (!token) return false;
  try {
    // Signature, issuer, audience, expiry and not-before are all checked here.
    await jwtVerify(token, keys ?? keysFor(team), { issuer: team, audience: aud, algorithms: ["RS256"] });
    return true;
  } catch {
    return false;
  }
}
