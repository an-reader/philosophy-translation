// The Worker's own login: one secret key, ACCESS_KEY, set per Worker by the owner (never in the repository, deploy.json,
// a log line or a response). Fails closed: a missing or short key refuses every API request.
//
// No rate limit: a 32-character random key cannot be brute-forced, so limiting guesses would add code and nothing else.
//
// The only way past without the key is the local-development bypass: ACCESS_DEV_BYPASS, which cloudflare.config.ts sets
// to "1" only when evaluated in mode "development" (`cf dev`) and to "" in builds and deploys, and which counts only
// when the request itself arrives on a loopback address, which no deployed address is.

export type AuthEnv = {
  ACCESS_KEY?: string;
  ACCESS_DEV_BYPASS?: string;
};

export const MIN_KEY_LENGTH = 32;
const SESSION_TEXT = "philosophy-translation session v1";
const SESSION_MAX_AGE = 34_560_000; // 400 days
const LOOPBACK = new Set(["localhost", "127.0.0.1", "[::1]"]);
const enc = new TextEncoder();

const UNAUTHORIZED = JSON.stringify({ error: "unauthorized" });

function reply(body: unknown, status: number, extra: Record<string, string> = {}): Response {
  return new Response(typeof body === "string" ? body : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", ...extra },
  });
}

const refuse = (): Response => reply(UNAUTHORIZED, 401);

const keyOf = (env: AuthEnv): string | null =>
  typeof env.ACCESS_KEY === "string" && env.ACCESS_KEY.length >= MIN_KEY_LENGTH ? env.ACCESS_KEY : null;

function base64url(bytes: ArrayBuffer): string {
  let s = "";
  for (const b of new Uint8Array(bytes)) s += String.fromCharCode(b);
  return btoa(s).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
}

/** The session cookie's value: HMAC-SHA256 of a fixed text under the key, so it is not the key and a new key ends every session. */
export async function sessionToken(key: string): Promise<string> {
  const k = await crypto.subtle.importKey("raw", enc.encode(key), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return base64url(await crypto.subtle.sign("HMAC", k, enc.encode(SESSION_TEXT)));
}

// Constant time: digest both sides to 32 bytes, then compare the digests. crypto.subtle.timingSafeEqual is the Workers runtime's;
// Node (the unit tests) lacks it, so there a plain XOR loop over the same 32 bytes stands in.
async function same(a: string, b: string): Promise<boolean> {
  const [x, y] = await Promise.all([crypto.subtle.digest("SHA-256", enc.encode(a)), crypto.subtle.digest("SHA-256", enc.encode(b))]);
  const native = (crypto.subtle as unknown as { timingSafeEqual?: (a: ArrayBuffer, b: ArrayBuffer) => boolean }).timingSafeEqual;
  if (native) return native.call(crypto.subtle, x, y);
  const u = new Uint8Array(x), v = new Uint8Array(y);
  let d = 0;
  for (let i = 0; i < u.length; i++) d |= u[i] ^ v[i];
  return d === 0;
}

function cookieValue(request: Request, name: string): string | null {
  for (const part of (request.headers.get("cookie") ?? "").split(";")) {
    const i = part.indexOf("=");
    if (i > 0 && part.slice(0, i).trim() === name) return part.slice(i + 1).trim();
  }
  return null;
}

/** True when the request may use the API: the local bypass, a valid session cookie, or `Authorization: Bearer <key>`. */
export async function authorized(request: Request, env: AuthEnv): Promise<boolean> {
  if (env.ACCESS_DEV_BYPASS === "1" && LOOPBACK.has(new URL(request.url).hostname)) return true;
  const key = keyOf(env);
  if (!key) return false;
  const bearer = /^Bearer (.+)$/.exec(request.headers.get("authorization") ?? "");
  if (bearer && (await same(bearer[1], key))) return true;
  const cookie = cookieValue(request, "session");
  return cookie !== null && (await same(cookie, await sessionToken(key)));
}

export function sameOrigin(request: Request): boolean {
  const origin = request.headers.get("origin");
  if (!origin) return true;
  let netloc = "";
  try {
    netloc = new URL(origin).host;
  } catch { /* an unparseable Origin never matches */ }
  return netloc === request.headers.get("host");
}

const COOKIE_ATTRS = "HttpOnly; Secure; SameSite=Strict; Path=/";

/**
 * The login gate in front of everything the Worker answers. Returns the response for /api/login, /api/logout and for a
 * refused request, or null when the request may go on to the API.
 */
export async function gate(request: Request, env: AuthEnv): Promise<Response | null> {
  const path = new URL(request.url).pathname.replace(/\/+$/, "");
  if (path === "/api/login" || path === "/api/logout") {
    if (request.method !== "POST") return reply({ error: "method not allowed" }, 405);
    if (!sameOrigin(request)) return reply({ error: "forbidden" }, 403);
    if (path === "/api/logout") {
      return reply({ ok: true }, 200, { "Set-Cookie": `session=; ${COOKIE_ATTRS}; Max-Age=0` });
    }
    const key = keyOf(env);
    let given: unknown;
    try {
      given = (JSON.parse((await request.text()).slice(0, 4096)) as { key?: unknown } | null)?.key;
    } catch { /* no usable body: no key */ }
    if (!key || typeof given !== "string" || !(await same(given, key))) return refuse();
    return reply({ ok: true }, 200, { "Set-Cookie": `session=${await sessionToken(key)}; ${COOKIE_ATTRS}; Max-Age=${SESSION_MAX_AGE}` });
  }
  return (await authorized(request, env)) ? null : refuse();
}
