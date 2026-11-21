import { env as workerEnv } from "cloudflare:workers";
import { accessAllows, type AccessEnv } from "./access.ts";

const env = workerEnv as typeof workerEnv & AccessEnv & { TZ_OFFSET_HOURS?: string };

// The API of tr.py's Handler (GET and POST, same JSON), answered from D1. Segments come from tr.py's own parser
// (loaded into D1 by `python3 tr.py cloud`); this file only reads them back.

type Row = Record<string, unknown>;
type Progress = { total: number; todo: number; draft: number; reviewed: number; final: number };

const NOT_FOUND = { error: "not found" };

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" },
  });
}

class NotFound extends Error {}
class Bad extends Error {} // tr.py answers 400 {"error":"bad request"}

type Obj = Record<string, unknown>;

// Python's str() on a JSON value.
function pyStr(v: unknown): string {
  if (typeof v === "string") return v;
  if (v === null) return "None";
  if (typeof v === "boolean") return v ? "True" : "False";
  if (typeof v === "number") return String(v);
  throw new Bad();
}

// Python's int() on a JSON value.
function pyInt(v: unknown): number {
  if (typeof v === "number" && Number.isFinite(v)) return Math.trunc(v);
  if (typeof v === "boolean") return v ? 1 : 0;
  if (typeof v === "string" && /^\s*[+-]?\d+\s*$/.test(v)) return Number(v);
  throw new Bad();
}

const truthy = (v: unknown): boolean =>
  !(v === undefined || v === null || v === false || v === 0 || v === "" ||
    (Array.isArray(v) && !v.length) || (typeof v === "object" && v !== null && !Array.isArray(v) && !Object.keys(v).length));

// dict.get(key, default)
const pyGet = (d: Obj, key: string, dflt: unknown): unknown => (Object.hasOwn(d, key) ? d[key] : dflt);
const pyReq = (d: Obj, key: string): unknown => {
  if (!Object.hasOwn(d, key)) throw new Bad();
  return d[key];
};

// Python compares strings by code point; JavaScript's < compares UTF-16 units.
function cmp(a: string, b: string): number {
  const x = Array.from(a), y = Array.from(b);
  for (let i = 0; i < Math.min(x.length, y.length); i++) {
    const d = x[i].codePointAt(0)! - y[i].codePointAt(0)!;
    if (d) return d;
  }
  return x.length - y.length;
}

// time.strftime("%Y-%m-%dT%H:%M:%S") in the repository's local time, set by TZ_OFFSET_HOURS.
function now(): string {
  const hours = Number(env.TZ_OFFSET_HOURS ?? 0) || 0;
  return new Date(Date.now() + hours * 3600_000).toISOString().slice(0, 19);
}

// Python's int() on a path piece: optional sign and digits.
function toInt(s: string): number {
  if (!/^[+-]?\d+$/.test(s.trim())) throw new NotFound();
  return Number(s.trim());
}

async function all(sql: string, ...params: unknown[]): Promise<Row[]> {
  const r = await env.DB.prepare(sql).bind(...params).all<Row>();
  return r.results;
}

// Per-chapter progress in code points: SQLite's length() counts characters, as Python's len() does.
const PROGRESS_SQL = `
  SELECT c.slug AS slug, c.n AS n, c.title AS title, c.part AS part, count(s.id) AS segs,
    coalesce(sum(length(s.text)), 0) AS total,
    coalesce(sum(CASE WHEN v.st = 'draft' THEN length(s.text) END), 0) AS draft,
    coalesce(sum(CASE WHEN v.st = 'reviewed' THEN length(s.text) END), 0) AS reviewed,
    coalesce(sum(CASE WHEN v.st = 'final' THEN length(s.text) END), 0) AS final
  FROM chapters c
  LEFT JOIN segs s ON s.slug = c.slug AND s.n = c.n
  LEFT JOIN (SELECT slug, n, id, json_extract(data, '$.status') AS st FROM vi) v
    ON v.slug = s.slug AND v.n = s.n AND v.id = s.id
  `;

function progressOf(r: Row): Progress {
  const total = Number(r.total), draft = Number(r.draft), reviewed = Number(r.reviewed), final = Number(r.final);
  return { total, todo: total - draft - reviewed - final, draft, reviewed, final };
}

function addTo(t: Progress, p: Progress): void {
  t.total += p.total; t.todo += p.todo; t.draft += p.draft; t.reviewed += p.reviewed; t.final += p.final;
}

const zero = (): Progress => ({ total: 0, todo: 0, draft: 0, reviewed: 0, final: 0 });

async function book(slug: string): Promise<Row> {
  const rows = await all(
    `SELECT b.slug, b.meta, coalesce(w.state, '{}') AS state, coalesce(w.titles, '{}') AS titles, coalesce(w.glossary, '[]') AS glossary
     FROM books b LEFT JOIN book_work w ON w.slug = b.slug WHERE b.slug = ?`, slug);
  if (!rows.length) throw new NotFound();
  return rows[0];
}

async function books(): Promise<unknown> {
  const [bs, ps, last] = await env.DB.batch<Row>([
    env.DB.prepare("SELECT slug, meta FROM books ORDER BY pos"),
    env.DB.prepare(PROGRESS_SQL + " GROUP BY c.slug, c.n"),
    env.DB.prepare("SELECT value FROM app WHERE key = 'last'"),
  ]);
  const sums = new Map<string, Progress>();
  for (const r of ps.results) {
    const t = sums.get(r.slug as string) ?? zero();
    addTo(t, progressOf(r));
    sums.set(r.slug as string, t);
  }
  return {
    books: bs.results.map((b) => ({ slug: b.slug, meta: JSON.parse(b.meta as string), progress: sums.get(b.slug as string) ?? zero() })),
    last: last.results.length ? last.results[0].value : null,
  };
}

async function bookSummary(slug: string): Promise<unknown> {
  const [bs, ps, refs] = await env.DB.batch<Row>([
    env.DB.prepare(`SELECT b.meta, coalesce(w.state, '{}') AS state, coalesce(w.titles, '{}') AS titles
      FROM books b LEFT JOIN book_work w ON w.slug = b.slug WHERE b.slug = ?`).bind(slug),
    env.DB.prepare(PROGRESS_SQL + " WHERE c.slug = ? GROUP BY c.n ORDER BY c.n").bind(slug),
    env.DB.prepare("SELECT label FROM refs WHERE slug = ? ORDER BY ref").bind(slug),
  ]);
  if (!bs.results.length) throw new NotFound();
  const b = bs.results[0];
  const total = zero();
  const chapters = ps.results.map((r) => {
    const progress = progressOf(r);
    addTo(total, progress);
    return { n: r.n, title: r.title, part: r.part, segs: r.segs, progress };
  });
  return {
    slug, meta: JSON.parse(b.meta as string), chapters, progress: total,
    state: JSON.parse(b.state as string), titles: JSON.parse(b.titles as string),
    refs: refs.results.map((r) => r.label),
  };
}

async function chapter(slug: string, n: number): Promise<unknown> {
  await book(slug);
  const [chs, segs, vis, refs, paras] = await env.DB.batch<Row>([
    env.DB.prepare("SELECT title, part FROM chapters WHERE slug = ? AND n = ?").bind(slug, n),
    env.DB.prepare("SELECT id, para, text, heading FROM segs WHERE slug = ? AND n = ? ORDER BY pos").bind(slug, n),
    env.DB.prepare("SELECT id, data FROM vi WHERE slug = ? AND n = ?").bind(slug, n),
    env.DB.prepare("SELECT ref, label FROM refs WHERE slug = ? ORDER BY ref").bind(slug),
    env.DB.prepare("SELECT ref, text FROM ref_paras WHERE slug = ? AND n = ? ORDER BY ref, pos").bind(slug, n),
  ]);
  if (n < 1 || !chs.results.length) throw new NotFound();
  const vi: Record<string, unknown> = {};
  for (const r of vis.results) vi[r.id as string] = JSON.parse(r.data as string);
  const text = new Map<number, string[]>();
  for (const r of paras.results) text.set(r.ref as number, [...(text.get(r.ref as number) ?? []), r.text as string]);
  // tr.py records the last book opened when a chapter is read.
  await env.DB.prepare("INSERT INTO app (key, value) VALUES ('last', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value").bind(slug).run();
  return {
    n, title: chs.results[0].title, part: chs.results[0].part,
    segs: segs.results.map((s) => ({ id: s.id, para: s.para, text: s.text, heading: s.heading === 1 })),
    vi,
    refs: refs.results.map((r) => ({ label: r.label, paras: text.get(r.ref as number) ?? [] })),
  };
}

async function memos(slug: string, n: number): Promise<unknown> {
  await book(slug);
  return (await all("SELECT data FROM memos WHERE slug = ? AND n = ? ORDER BY seg, off, created, seq", slug, n)).map((r) => JSON.parse(r.data as string));
}

async function allMemos(slug: string): Promise<unknown> {
  await book(slug);
  const [chs, ms] = await env.DB.batch<Row>([
    env.DB.prepare("SELECT n, title FROM chapters WHERE slug = ? AND n IN (SELECT n FROM memos WHERE slug = ?) ORDER BY n").bind(slug, slug),
    env.DB.prepare("SELECT n, data FROM memos WHERE slug = ? ORDER BY n, seg, off, created, seq").bind(slug),
  ]);
  const byN = new Map<number, unknown[]>();
  for (const r of ms.results) byN.set(r.n as number, [...(byN.get(r.n as number) ?? []), JSON.parse(r.data as string)]);
  return chs.results.map((c) => ({ n: c.n, title: c.title, memos: byN.get(c.n as number) ?? [] }));
}

async function glossary(slug: string): Promise<unknown> {
  return JSON.parse((await book(slug)).glossary as string);
}

async function route(parts: string[]): Promise<unknown> {
  const [api, kind, slug, n] = parts;
  if (api !== "api") throw new NotFound();
  if (parts.length === 2 && kind === "books") return books();
  if (parts.length === 3 && kind === "book") return bookSummary(slug);
  if (parts.length === 4 && kind === "chapter") return chapter(slug, toInt(n));
  if (parts.length === 4 && kind === "memos") return memos(slug, toInt(n));
  if (parts.length === 3 && kind === "memos") return allMemos(slug);
  if (parts.length === 3 && kind === "glossary") return glossary(slug);
  throw new NotFound();
}

// ---- writes: tr.py's save_seg, save_memo, save_title and clean_glossary ----

const STATUSES = ["todo", "draft", "reviewed", "final"];
const ensureWork = (slug: string) =>
  env.DB.prepare("INSERT OR IGNORE INTO book_work (slug, state, titles, glossary) VALUES (?, '{}', '{}', '[]')").bind(slug);

async function saveSeg(slug: string, n: number, data: Obj): Promise<unknown> {
  const sid = pyStr(pyReq(data, "id"));
  let viText = pyStr(pyGet(data, "vi", ""));
  let status = pyStr(pyGet(data, "status", "draft"));
  const note = pyStr(pyGet(data, "note", ""));
  await book(slug);
  const seg = await all("SELECT text FROM segs WHERE slug = ? AND n = ? AND id = ?", slug, n, sid);
  if (!seg.length) throw new Bad();
  viText = viText.trimEnd();
  const del = !viText && !note.trim();
  if (del) {
    status = "todo";
  } else {
    if (!STATUSES.includes(status)) status = "draft";
    if (viText && status === "todo") status = "draft";
    if (!viText) status = "todo";
  }
  const entry = {
    vi: viText, status, note: note.trimEnd(),
    src: Array.from(seg[0].text as string).slice(0, 40).join(""), updated: now(),
  };
  const write = del
    ? env.DB.prepare("DELETE FROM vi WHERE slug = ? AND n = ? AND id = ?").bind(slug, n, sid)
    : env.DB.prepare("INSERT OR REPLACE INTO vi (slug, n, id, data) VALUES (?, ?, ?, ?)").bind(slug, n, sid, JSON.stringify(entry));
  const res = await env.DB.batch<Row>([
    ensureWork(slug),
    write,
    env.DB.prepare("UPDATE book_work SET state = ? WHERE slug = ?").bind(JSON.stringify({ chapter: n, seg: sid }), slug),
    env.DB.prepare(PROGRESS_SQL + " WHERE c.slug = ? AND c.n = ? GROUP BY c.n").bind(slug, n),
  ]);
  return { status, progress: progressOf(res[3].results[0]) };
}

async function saveMemo(slug: string, n: number, data: Obj): Promise<unknown> {
  await book(slug);
  const segRows = await all("SELECT id FROM segs WHERE slug = ? AND n = ?", slug, n);
  if (n < 1 || !(await all("SELECT 1 FROM chapters WHERE slug = ? AND n = ?", slug, n)).length) throw new Bad();
  const ids = new Set(segRows.map((r) => r.id as string));
  const stmts: D1PreparedStatement[] = [];
  const sel = env.DB.prepare("SELECT data FROM memos WHERE slug = ? AND n = ? ORDER BY seg, off, created, seq").bind(slug, n);
  if (truthy(pyGet(data, "delete", undefined))) {
    stmts.push(env.DB.prepare("DELETE FROM memos WHERE slug = ? AND n = ? AND id = ?").bind(slug, n, data.delete));
  } else {
    const body = pyStr(pyReq(data, "body")).trim();
    if (!body) throw new Bad();
    const at = now();
    const oldId = pyGet(data, "id", undefined);
    const old = (await all("SELECT data FROM memos WHERE slug = ? AND n = ?", slug, n))
      .map((r) => JSON.parse(r.data as string) as Obj).find((m) => m.id === oldId);
    if (old) {
      old.body = body;
      old.updated = at;
      stmts.push(env.DB.prepare("UPDATE memos SET data = ? WHERE slug = ? AND n = ? AND id = ?").bind(JSON.stringify(old), slug, n, old.id));
    } else {
      const seg = pyStr(pyReq(data, "seg"));
      const endRaw = pyGet(data, "endSeg", undefined);
      const end = truthy(endRaw) ? pyStr(endRaw) : seg;
      if (!ids.has(seg) || !ids.has(end)) throw new Bad();
      const memo = {
        id: crypto.randomUUID().replaceAll("-", "").slice(0, 12), side: data.side === "vi" ? "vi" : "source",
        seg, endSeg: end, offset: pyInt(pyGet(data, "offset", 0)), endOffset: pyInt(pyGet(data, "endOffset", 0)),
        quote: pyStr(pyGet(data, "quote", "")), body, created: at, updated: at,
      };
      stmts.push(env.DB.prepare(
        "INSERT INTO memos (slug, n, id, seq, seg, off, created, data) VALUES (?, ?, ?, " +
        "(SELECT coalesce(max(seq), 0) + 1 FROM memos WHERE slug = ? AND n = ?), ?, ?, ?, ?)",
      ).bind(slug, n, memo.id, slug, n, memo.seg, memo.offset, memo.created, JSON.stringify(memo)));
    }
  }
  const res = await env.DB.batch<Row>([...stmts, sel]);
  return res[res.length - 1].results.map((r) => JSON.parse(r.data as string));
}

async function saveTitle(slug: string, data: Obj): Promise<unknown> {
  const kind = pyStr(pyReq(data, "kind"));
  const key = pyStr(pyGet(data, "key", ""));
  const vi = pyStr(pyGet(data, "vi", "")).trim();
  const b = await book(slug);
  const titles = JSON.parse(b.titles as string) as Obj;
  if (kind === "book") {
    if (vi) titles.book = vi; else delete titles.book;
  } else {
    let valid = false;
    if (kind === "part") {
      valid = (await all("SELECT 1 FROM chapters WHERE slug = ? AND part = ?", slug, key)).length > 0;
    } else if (kind === "chapter") {
      const count = Number((await all("SELECT count(*) AS c FROM chapters WHERE slug = ?", slug))[0].c);
      valid = /^\d+$/.test(key) && Number(key) >= 1 && Number(key) <= count;
    }
    if (!valid) throw new Bad();
    const group = ((titles[kind + "s"] ??= {}) as Obj); // setdefault: the group exists even when emptied
    if (vi) group[key] = vi; else delete group[key];
  }
  await env.DB.batch([
    ensureWork(slug),
    env.DB.prepare("UPDATE book_work SET titles = ? WHERE slug = ?").bind(JSON.stringify(titles), slug),
  ]);
  return titles;
}

async function saveGlossary(slug: string, data: Obj): Promise<unknown> {
  const items = pyReq(data, "items");
  if (!Array.isArray(items)) throw new Bad();
  await book(slug);
  const seen = new Set<string>();
  const out: { term: string; vi: string; note: string }[] = [];
  for (const g of items) {
    if (typeof g !== "object" || g === null || Array.isArray(g)) throw new Bad();
    const term = pyStr(pyGet(g as Obj, "term", "")).trim();
    if (term && !seen.has(term.toLowerCase())) {
      seen.add(term.toLowerCase());
      out.push({ term, vi: pyStr(pyGet(g as Obj, "vi", "")).trim(), note: pyStr(pyGet(g as Obj, "note", "")).trim() });
    }
  }
  out.sort((x, y) => cmp(x.term.toLowerCase(), y.term.toLowerCase()));
  await env.DB.batch([
    ensureWork(slug),
    env.DB.prepare("UPDATE book_work SET glossary = ? WHERE slug = ?").bind(JSON.stringify(out), slug),
  ]);
  return out;
}

async function post(parts: string[], request: Request): Promise<unknown> {
  const raw = await request.text();
  let data: unknown;
  try {
    data = JSON.parse(raw.trim() ? raw : "{}");
  } catch {
    throw new Bad();
  }
  if (typeof data !== "object" || data === null || Array.isArray(data)) throw new Bad();
  const d = data as Obj;
  const [api, kind, slug, n] = parts;
  if (api === "api" && parts.length === 4 && kind === "seg") return saveSeg(slug, pyInt(n), d);
  if (api === "api" && parts.length === 4 && kind === "memos") return saveMemo(slug, pyInt(n), d);
  if (api === "api" && parts.length === 3 && kind === "titles") return saveTitle(slug, d);
  if (api === "api" && parts.length === 3 && kind === "glossary") return saveGlossary(slug, d);
  throw new Bad();
}

function sameOrigin(request: Request): boolean {
  const origin = request.headers.get("origin");
  if (!origin) return true;
  let netloc = "";
  try {
    netloc = new URL(origin).host;
  } catch { /* an unparseable Origin never matches */ }
  return netloc === request.headers.get("host");
}

export default {
  async fetch(request: Request): Promise<Response> {
    if (!(await accessAllows(request, env))) return json({ error: "unauthorized" }, 401);
    if (request.method !== "GET" && request.method !== "POST") return json({ error: "method not allowed" }, 405);
    try {
      if (request.method === "POST") {
        if (!sameOrigin(request)) return json({ error: "forbidden" }, 403);
        const parts = new URL(request.url).pathname.split("/").filter(Boolean).map(decodeURIComponent);
        return json(await post(parts, request));
      }
      const parts = new URL(request.url).pathname.split("/").filter(Boolean).map(decodeURIComponent);
      return json(await route(parts));
    } catch (e) {
      if (e instanceof NotFound) return request.method === "POST" ? json({ error: "bad request" }, 400) : json(NOT_FOUND, 404);
      if (e instanceof Bad || e instanceof URIError) return json({ error: "bad request" }, request.method === "POST" ? 400 : 404);
      throw e;
    }
  },
} satisfies ExportedHandler;
