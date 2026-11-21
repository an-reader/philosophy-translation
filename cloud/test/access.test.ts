import test from "node:test";
import assert from "node:assert/strict";
import { SignJWT, exportJWK, generateKeyPair, createLocalJWKSet } from "jose";
import { accessAllows } from "../src/access.ts";

const TEAM = "https://team.cloudflareaccess.com";
const AUD = "aud-tag";
const env = { TEAM_DOMAIN: TEAM, POLICY_AUD: AUD };

const a = await generateKeyPair("RS256");
const b = await generateKeyPair("RS256");
const jwk = { ...(await exportJWK(a.publicKey)), kid: "k1", alg: "RS256", use: "sig" };
const keys = createLocalJWKSet({ keys: [jwk] });

function sign(key: CryptoKey, o: { iss?: string; aud?: string; exp?: string | number; kid?: string } = {}) {
  return new SignJWT({ email: "x@example.com" })
    .setProtectedHeader({ alg: "RS256", kid: o.kid ?? "k1" })
    .setIssuer(o.iss ?? TEAM).setAudience(o.aud ?? AUD).setIssuedAt().setExpirationTime(o.exp ?? "5m")
    .sign(key);
}
const req = (token?: string, url = "https://x.workers.dev/api/books") =>
  new Request(url, { headers: token ? { "cf-access-jwt-assertion": token } : {} });

test("a good token passes", async () => assert.equal(await accessAllows(req(await sign(a.privateKey)), env, keys), true));
test("no token", async () => assert.equal(await accessAllows(req(), env, keys), false));
test("wrong signature", async () => assert.equal(await accessAllows(req(await sign(b.privateKey)), env, keys), false));
test("wrong audience", async () => assert.equal(await accessAllows(req(await sign(a.privateKey, { aud: "other" })), env, keys), false));
test("wrong issuer", async () => assert.equal(await accessAllows(req(await sign(a.privateKey, { iss: "https://evil.example" })), env, keys), false));
test("expired", async () => assert.equal(await accessAllows(req(await sign(a.privateKey, { exp: Math.floor(Date.now() / 1000) - 60 })), env, keys), false));
test("missing audience setting refuses a good token", async () =>
  assert.equal(await accessAllows(req(await sign(a.privateKey)), { TEAM_DOMAIN: TEAM }, keys), false));
test("missing team setting refuses a good token", async () =>
  assert.equal(await accessAllows(req(await sign(a.privateKey)), { POLICY_AUD: AUD }, keys), false));
test("bypass works on loopback only", async () => {
  assert.equal(await accessAllows(req(undefined, "http://127.0.0.1:8787/api/books"), { ACCESS_DEV_BYPASS: "1" }, keys), true);
  assert.equal(await accessAllows(req(undefined, "https://x.workers.dev/api/books"), { ACCESS_DEV_BYPASS: "1" }, keys), false);
  assert.equal(await accessAllows(req(undefined, "http://127.0.0.1:8787/api/books"), {}, keys), false);
});
