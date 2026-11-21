import test from "node:test";
import assert from "node:assert/strict";
import { authorized, gate, sessionToken } from "../src/auth.ts";

const KEY = "k".repeat(16) + "K".repeat(16); // 32 characters
const OTHER = "z".repeat(32);
const HOST = "https://x.workers.dev";
const env = { ACCESS_KEY: KEY };

const login = (key: unknown, e = env, headers: Record<string, string> = {}) =>
  gate(new Request(`${HOST}/api/login`, { method: "POST", headers, body: JSON.stringify({ key }) }), e);
const get = (headers: Record<string, string> = {}, url = `${HOST}/api/books`) => new Request(url, { headers });

test("a Worker without a key refuses everything, login included", async () => {
  for (const e of [{}, { ACCESS_KEY: "" }]) {
    assert.equal((await gate(get(), e))?.status, 401);
    assert.equal((await login(KEY, e))?.status, 401);
    assert.equal(await authorized(get({ authorization: "Bearer " }), e), false);
    assert.equal(await authorized(get({ authorization: "Bearer undefined" }), e), false);
  }
});

test("a key shorter than 32 characters refuses everything", async () => {
  const short = { ACCESS_KEY: "k".repeat(31) };
  assert.equal((await login("k".repeat(31), short))?.status, 401);
  assert.equal(await authorized(get({ authorization: `Bearer ${short.ACCESS_KEY}` }), short), false);
  const token = await sessionToken(short.ACCESS_KEY);
  assert.equal(await authorized(get({ cookie: `session=${token}` }), short), false);
});

test("the wrong or a missing key gets 401 at login, with no cookie", async () => {
  for (const key of [OTHER, "", undefined, 5, null, [KEY]]) {
    const r = (await login(key))!;
    assert.equal(r.status, 401);
    assert.deepEqual(await r.json(), { error: "unauthorized" });
    assert.equal(r.headers.get("set-cookie"), null);
  }
  const bad = await gate(new Request(`${HOST}/api/login`, { method: "POST", body: "{nope" }), env);
  assert.equal(bad?.status, 401);
  assert.equal((await gate(new Request(`${HOST}/api/login`, { method: "POST" }), env))?.status, 401);
});

test("the right key sets the session cookie with exactly the agreed attributes", async () => {
  const r = (await login(KEY))!;
  assert.equal(r.status, 200);
  assert.deepEqual(await r.json(), { ok: true });
  const token = await sessionToken(KEY);
  assert.match(token, /^[A-Za-z0-9_-]+$/);
  assert.notEqual(token, KEY);
  assert.equal(r.headers.get("set-cookie"), `session=${token}; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=34560000`);
  assert.equal(34560000, 400 * 24 * 3600);
});

test("login keeps the same-origin check and takes POST only", async () => {
  assert.equal((await login(KEY, env, { origin: "https://evil.example", host: "x.workers.dev" }))?.status, 403);
  assert.equal((await gate(new Request(`${HOST}/api/login`), env))?.status, 405);
});

test("a valid cookie passes and a forged one fails", async () => {
  const token = await sessionToken(KEY);
  assert.equal(await gate(get({ cookie: `a=b; session=${token}; c=d` }), env), null);
  assert.equal((await gate(get({ cookie: `session=${await sessionToken(OTHER)}` }), env))?.status, 401);
  assert.equal((await gate(get({ cookie: `session=${KEY}` }), env))?.status, 401); // the key itself is not the cookie
  assert.equal((await gate(get({ cookie: "session=" }), env))?.status, 401);
  assert.equal((await gate(get({ cookie: `xsession=${token}` }), env))?.status, 401);
  assert.equal((await gate(get(), env))?.status, 401);
  // a new key ends every session
  assert.equal((await gate(get({ cookie: `session=${token}` }), { ACCESS_KEY: OTHER }))?.status, 401);
});

test("Bearer passes with the key and fails with a wrong one", async () => {
  assert.equal(await gate(get({ authorization: `Bearer ${KEY}` }), env), null);
  assert.equal((await gate(get({ authorization: `Bearer ${OTHER}` }), env))?.status, 401);
  assert.equal((await gate(get({ authorization: KEY }), env))?.status, 401);
  assert.equal((await gate(get({ authorization: `Basic ${KEY}` }), env))?.status, 401);
});

test("every API path is gated, POSTs too", async () => {
  for (const [m, p] of [["GET", "/api/books"], ["POST", "/api/seg/a/1"], ["GET", "/api/work/a"], ["GET", "/other"], ["GET", "/api/loginx"]]) {
    assert.equal((await gate(new Request(HOST + p, { method: m }), env))?.status, 401, `${m} ${p}`);
  }
});

test("logout clears the cookie", async () => {
  const r = (await gate(new Request(`${HOST}/api/logout`, { method: "POST" }), env))!;
  assert.equal(r.status, 200);
  assert.equal(r.headers.get("set-cookie"), "session=; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=0");
});

test("the bypass works on loopback only, and a deployed request is never bypassed", async () => {
  const on = { ACCESS_DEV_BYPASS: "1" };
  for (const u of ["http://127.0.0.1:8787/api/books", "http://localhost:8787/api/books", "http://[::1]:8787/api/books"]) {
    assert.equal(await gate(get({}, u), on), null, u);
  }
  assert.equal((await gate(get({}, `${HOST}/api/books`), on))?.status, 401);
  assert.equal((await gate(get({ host: "127.0.0.1" }, `${HOST}/api/books`), on))?.status, 401);
  assert.equal((await gate(get({}, "http://127.0.0.1:8787/api/books"), {}))?.status, 401);
  assert.equal((await gate(get({}, "http://127.0.0.1:8787/api/books"), { ACCESS_DEV_BYPASS: "" }))?.status, 401);
  assert.equal((await gate(get({}, `${HOST}/api/books`), { ...on, ACCESS_KEY: KEY }))?.status, 401);
});
