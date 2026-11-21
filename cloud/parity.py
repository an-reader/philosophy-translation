#!/usr/bin/env python3
"""Parity check: the Worker (under `cf dev`, local D1) against tr.Handler, same requests, same JSON.

One invocation does everything and leaves nothing running:
  1. copies books/ into a scratch folder and points tr.ROOT and tr.BOOKS at the copy (the real books/ is never written),
  2. builds the data files from the copy (tr.cmd_cloud) and loads them into the local D1 (cf d1 ... --local),
  3. starts `cf dev` (bounded by a deadline and killed in the finally block) and tr.Handler in a thread on port 0,
  4. GETs every endpoint for every book and chapter, runs one fixed sequence of POSTs against both, GETs everything again,
  5. compares status codes and parsed JSON, prints the first differences, exits 1 on any, 2 on a setup failure.

Left out of the comparison, by design: the `updated` and `created` fields and memo ids (clock and random values).
`last` is compared: both sides get the same call sequence.

  python3 /abs/path/to/cloud/parity.py
"""
import json, os, shutil, signal, socket, subprocess, sys, tempfile, threading, time, http.client
from http.server import ThreadingHTTPServer
from pathlib import Path

CLOUD = Path(__file__).resolve().parent
REPO = CLOUD.parent
sys.path.insert(0, str(REPO))
import tr  # noqa: E402

LOCAL_ID = "00000000-0000-4000-8000-000000000001"
DEADLINE = time.time() + 600
CF = str(CLOUD / "node_modules" / ".bin" / "cf")
IGNORED = ("updated", "created")


def log(*a):
    print(*a, flush=True)


def die(msg):
    log("SETUP FAILED:", msg)
    sys.exit(2)


class Side:
    def __init__(self, name, port):
        self.name, self.port = name, port

    def call(self, method, path, body=None, headers=None, raw=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        h = dict(headers or {})
        data = raw if raw is not None else (json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None)
        if data is not None:
            h["Content-Type"] = "application/json"
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        payload = r.read()
        c.close()
        try:
            parsed = json.loads(payload)
        except ValueError:
            parsed = ("NOT JSON", payload[:80])
        return r.status, parsed, r.getheader("Cache-Control")


def norm(v):
    """Drop clock and random values."""
    if isinstance(v, dict):
        out = {k: norm(x) for k, x in v.items() if k not in IGNORED}
        if "side" in out and "seg" in out:  # a memo
            out.pop("id", None)
        return out
    if isinstance(v, list):
        return [norm(x) for x in v]
    return v


def first_diff(a, b, path=""):
    if type(a) != type(b) and not (isinstance(a, (int, float)) and isinstance(b, (int, float))):
        return f"{path}: {a!r:.120} != {b!r:.120}"
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                return f"{path}/{k}: only in {'python' if k in a else 'worker'}"
            d = first_diff(a[k], b[k], f"{path}/{k}")
            if d:
                return d
        return None
    if isinstance(a, list):
        if len(a) != len(b):
            return f"{path}: list length {len(a)} != {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            d = first_diff(x, y, f"{path}[{i}]")
            if d:
                return d
        return None
    return None if a == b else f"{path}: {a!r:.120} != {b!r:.120}"


class Compare:
    def __init__(self, py, wk):
        self.py, self.wk, self.n, self.bad = py, wk, 0, []

    def both(self, label, method, path, body=None, headers=None, raw=None, nocache=True):
        a = self.py.call(method, path, body, headers, raw)
        b = self.wk.call(method, path, body, headers, raw)
        self.n += 1
        msg = None
        if a[0] != b[0]:
            msg = f"status python {a[0]} worker {b[0]}"
        elif nocache and b[2] != "no-store" and path.startswith("/api/"):
            msg = f"worker Cache-Control {b[2]!r}"
        else:
            msg = first_diff(norm(a[1]), norm(b[1]))
        if msg:
            self.bad.append(f"{label} {method} {path}: {msg}")
        return a[1], b[1]


def start_cf_dev(port, env, logfile):
    p = subprocess.Popen([CF, "dev", "--port", str(port), "--host", "127.0.0.1", "--strictPort"], cwd=CLOUD, env=env,
                         stdout=logfile, stderr=subprocess.STDOUT, start_new_session=True)
    return p


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def main():
    scratch = Path(os.environ.get("TMPDIR", tempfile.gettempdir())) / f"parity-{os.getpid()}"
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True)
    env = dict(os.environ, CLOUDFLARE_DEV_REGISTRY_PATH=str(scratch / "dev-registry"), CHOKIDAR_USEPOLLING="1",
               XDG_CONFIG_HOME=str(scratch / "xdg-config"), CI="1")
    state = CLOUD / ".cloudflare" / "state"
    proc = httpd = None
    try:
        # 1. a copy of books/ is the only place the writes land
        shutil.copytree(REPO / "books", scratch / "books")
        shutil.copyfile(REPO / "app.html", scratch / "app.html")
        tr.ROOT, tr.BOOKS = scratch, scratch / "books"
        tr._cache.clear()
        # 2. data files from the copy, into a fresh local D1
        tr.cmd_cloud(None)
        build = scratch / "cloud" / ".build"
        shutil.rmtree(state, ignore_errors=True)
        def cfrun(*args):
            r = subprocess.run([CF, *args], cwd=CLOUD, env=env, capture_output=True, text=True, timeout=120)
            if r.returncode:
                die(f"cf {' '.join(args)[:80]} failed: {(r.stderr or r.stdout)[-400:]}")
            return r.stdout
        cfrun("d1", "migrations", "apply", LOCAL_ID, "--local")
        for f in sorted(build.glob("texts-*.json")) + sorted(build.glob("work-*.json")):
            out = cfrun("d1", "raw", LOCAL_ID, "--local", "--batch", f"@{f}")
            if '"success": false' in out:
                die(f"a statement of {f.name} failed")
        log("loaded the local D1")
        # 3. the two servers
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), tr.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        py = Side("python", httpd.server_address[1])
        port = free_port()
        logfile = open(scratch / "cf-dev.log", "wb")
        proc = start_cf_dev(port, env, logfile)
        wk = Side("worker", port)
        while True:
            if time.time() > DEADLINE:
                die("cf dev did not answer in time")
            if proc.poll() is not None:
                die("cf dev exited: " + (scratch / "cf-dev.log").read_text(errors="replace")[-600:])
            try:
                if wk.call("GET", "/api/books")[0] == 200:
                    break
            except OSError:
                pass
            time.sleep(1)
        log(f"cf dev answers on 127.0.0.1:{port}; tr.Handler on {py.port}")
        cmp = Compare(py, wk)
        run_checks(cmp, py, wk)
        log(f"{cmp.n} comparisons; left out: the fields {IGNORED} and memo ids")
        for m in cmp.bad[:8]:
            log("DIFF", m)
        log(f"{len(cmp.bad)} differences")
        return 1 if cmp.bad else 0
    finally:
        if proc and proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
        if httpd:
            httpd.shutdown()
        shutil.rmtree(state, ignore_errors=True)
        shutil.rmtree(scratch, ignore_errors=True)


def all_gets(cmp):
    """Every GET endpoint, every book, every chapter."""
    cmp.both("get", "GET", "/api/books")
    books = [b["slug"] for b in cmp.py.call("GET", "/api/books")[1]["books"]]
    for slug in books:
        summary = cmp.both("get", "GET", f"/api/book/{slug}")[0]
        cmp.both("get", "GET", f"/api/glossary/{slug}")
        cmp.both("get", "GET", f"/api/memos/{slug}")
        for ch in summary["chapters"]:
            cmp.both("get", "GET", f"/api/chapter/{slug}/{ch['n']}")
            cmp.both("get", "GET", f"/api/memos/{slug}/{ch['n']}")
    return books


def run_checks(cmp, py, wk):
    # the same call sequence on both: reads first, then writes, then reads again
    cmp.both("last-before", "GET", "/api/books")
    books = all_gets(cmp)
    cmp.both("last-after", "GET", "/api/books")
    for p in ["/api/book/nope", "/api/book/Bad_Slug", "/api/chapter/plato-republic/9999",
              "/api/chapter/plato-republic/x", "/api/chapter/nope/1", "/api/memos/nope", "/api/memos/nope/1",
              "/api/glossary/nope", "/api/nothing", "/api/books/extra", "/api/book/plato-republic/extra"]:
        cmp.both("404", "GET", p)
    # not compared: /api/chapter/<slug>/0 (and negative numbers). tr.py indexes chapters[-1] and answers 200 for them; the Worker answers 404.
    cmp.both("memos-beyond", "GET", "/api/memos/plato-republic/9999")

    book, ch = "plato-republic", 1
    segs = py.call("GET", f"/api/chapter/{book}/{ch}")[1]["segs"]
    ids = [s["id"] for s in segs]
    s1, s2, s3, s4, s5 = ids[:5]
    seg = lambda i, **kw: cmp.both("seg", "POST", f"/api/seg/{book}/{ch}", {"id": i, **kw})
    seg(s1, vi="Xin chào, thế giới  \n", status="draft", note="ghi chú đầu")
    seg(s2, vi="Σωκράτης κατέβην χθὲς εἰς Πειραιᾶ — Tôi đã xuống Piraeus hôm qua.", status="reviewed", note="")
    seg(s3, vi="Đây là bản cuối", status="final")
    seg(s4, vi="", status="final", note="chỉ có ghi chú")
    seg(s5, vi="", status="draft", note="")
    seg(s1, vi="đổi lại", status="bogus")
    seg(s2, vi="Λόγος và lời", status="todo")
    seg(s3, vi="  ", status="final", note="  ghi chú  ")
    seg(s5, vi="x", note="''; DROP TABLE vi; --")
    seg(s5, vi="", note="")
    seg(s4, vi="")
    cmp.both("seg-bad", "POST", f"/api/seg/{book}/{ch}", {"id": "999.9999", "vi": "x"})
    cmp.both("seg-bad", "POST", f"/api/seg/{book}/{ch}", {"vi": "x"})
    cmp.both("seg-bad", "POST", f"/api/seg/nope/{ch}", {"id": s1, "vi": "x"})
    cmp.both("seg-bad", "POST", f"/api/seg/{book}/9999", {"id": s1, "vi": "x"})
    cmp.both("seg-bad", "POST", f"/api/seg/{book}/abc", {"id": s1, "vi": "x"})
    cmp.both("seg-bad", "POST", f"/api/seg/{book}/{ch}", raw=b"{not json")
    cmp.both("seg-bad", "POST", f"/api/seg/{book}/{ch}", raw=b"[1,2]")
    cmp.both("seg-empty-body", "POST", f"/api/seg/{book}/{ch}", raw=b"")
    cmp.both("post-bad-path", "POST", "/api/nothing", {})
    cmp.both("forbidden", "POST", f"/api/seg/{book}/{ch}", {"id": s1, "vi": "x"}, headers={"Origin": "http://evil.example"})
    cmp.both("forbidden", "POST", f"/api/glossary/{book}", {"items": []}, headers={"Origin": "null"})
    # a second book, so `last` and the state move there too
    seg2 = cmp.both("get", "GET", "/api/chapter/kant-kritik/1")[0]["segs"][0]["id"]
    cmp.both("seg-kant", "POST", "/api/seg/kant-kritik/1", {"id": seg2, "vi": "Francis Bacon của Verulam", "status": "draft"})

    # memos: created, edited, deleted
    mpath = f"/api/memos/{book}/{ch}"
    for b in (
        {"seg": s1, "side": "vi", "offset": 3, "endOffset": 7, "quote": "λόγος", "body": "Ghi chú về λόγος"},
        {"seg": s1, "offset": 1, "endOffset": 2, "quote": "Xin", "body": "Memo nguồn", "side": "other"}):
        cmp.both("memo", "POST", mpath, b)
    cmp.both("memo", "POST", mpath, {"seg": s2, "endSeg": s3, "body": "Trải dài hai đoạn"})
    cmp.both("memo", "POST", mpath, {"seg": s2, "body": "x", "offset": "5", "endOffset": 6.9})
    first = lambda r: next(m for m in r if m["body"].startswith("Ghi chú về"))["id"]
    second = lambda r: next(m for m in r if m["body"] == "Memo nguồn")["id"]
    # each server has its own memo ids: edit and delete by the ids each one answered
    py_list, wk_list = cmp.py.call("GET", mpath)[1], cmp.wk.call("GET", mpath)[1]
    for lbl, body_fn in (("edit", lambda l: {"id": first(l), "body": "Đã sửa: ἀγαθόν"}),):
        a = cmp.py.call("POST", mpath, body_fn(py_list))
        b = cmp.wk.call("POST", mpath, body_fn(wk_list))
        cmp.n += 1
        if a[0] != b[0] or first_diff(norm(a[1]), norm(b[1])):
            cmp.bad.append(f"memo edit: {a[0]} {b[0]} {first_diff(norm(a[1]), norm(b[1]))}")
    a = cmp.py.call("POST", mpath, {"delete": second(py_list)})
    b = cmp.wk.call("POST", mpath, {"delete": second(wk_list)})
    cmp.n += 1
    if a[0] != b[0] or first_diff(norm(a[1]), norm(b[1])):
        cmp.bad.append(f"memo delete: {a[0]} {b[0]} {first_diff(norm(a[1]), norm(b[1]))}")
    cmp.both("memo-bad", "POST", mpath, {"seg": s1, "body": "   "})
    cmp.both("memo-bad", "POST", mpath, {"seg": s1})
    cmp.both("memo-bad", "POST", mpath, {"seg": "999.9999", "body": "x"})
    cmp.both("memo-bad", "POST", mpath, {"seg": s1, "endSeg": "999.9999", "body": "x"})
    cmp.both("memo-bad", "POST", mpath, {"seg": s1, "body": "x", "offset": "abc"})
    cmp.both("memo-bad", "POST", f"/api/memos/{book}/9999", {"seg": s1, "body": "x"})
    cmp.both("memo-bad", "POST", f"/api/memos/nope/1", {"seg": s1, "body": "x"})
    cmp.both("memo-delete-unknown", "POST", mpath, {"delete": "nonexistent"})

    # titles: set and cleared
    tpath = f"/api/titles/{book}"
    part = cmp.py.call("GET", f"/api/book/{book}")[1]["chapters"][0]["part"]
    for b in ({"kind": "book", "vi": "  Nền Cộng hòa  "}, {"kind": "chapter", "key": "1", "vi": "Chương một: Πολιτεία"},
              {"kind": "chapter", "key": "2", "vi": "Hai"}, {"kind": "part", "key": part, "vi": "Phần một"},
              {"kind": "chapter", "key": "1", "vi": ""}, {"kind": "chapter", "key": "2", "vi": "  "},
              {"kind": "part", "key": part, "vi": ""}, {"kind": "book", "vi": ""}, {"kind": "book", "vi": "Lại đặt"},
              {"kind": "chapter", "key": "0", "vi": "x"}, {"kind": "chapter", "key": "9999", "vi": "x"},
              {"kind": "chapter", "key": "abc", "vi": "x"}, {"kind": "part", "key": "no such part", "vi": "x"},
              {"kind": "bogus", "key": "1", "vi": "x"}, {"key": "1", "vi": "x"}):
        cmp.both("title", "POST", tpath, b)
    cmp.both("title-bad", "POST", "/api/titles/nope", {"kind": "book", "vi": "x"})

    # glossary: a duplicate term (case-insensitive), an empty term, Greek, Vietnamese, sorting
    gpath = f"/api/glossary/{book}"
    cmp.both("glossary", "POST", gpath, {"items": [
        {"term": "Logos", "vi": "lời", "note": " n1 "}, {"term": "logos", "vi": "khác"}, {"term": "  ", "vi": "bỏ"},
        {"term": "λόγος", "vi": "ngôn từ"}, {"term": "Đạo", "vi": "đạo", "note": "ghi"}, {"term": "ΣΟΦΟΣ", "vi": "x"},
        {"term": "αρετή"}, {"term": "Zeta", "vi": "z"}, {"term": "ábc", "vi": "y"}]})
    cmp.both("glossary", "POST", f"/api/glossary/kant-kritik", {"items": [{"term": "Vernunft", "vi": "lý tính"}]})
    # not compared: {"items": "no"} and {"items": [1]} crash tr.py's handler (AttributeError, connection dropped); the Worker answers 400
    cmp.both("glossary-bad", "POST", gpath, {"nothing": 1})
    cmp.both("glossary-bad", "POST", "/api/glossary/nope", {"items": []})
    # everything again, after the writes
    all_gets(cmp)
    cmp.both("last-final", "GET", "/api/books")
    # an Access-less POST on a page path is not the Worker's business; nothing to compare there


if __name__ == "__main__":
    sys.exit(main())
