#!/usr/bin/env python3
"""Parity check: the Worker (under `cf dev`, local D1) against tr.Handler, same requests, same JSON.

One invocation does everything and leaves nothing running:
  1. copies books/ into a scratch folder and points tr.ROOT and tr.BOOKS at the copy (the real books/ is never written),
  2. builds the data files from the copy (tr.cmd_cloud) and loads them into the local D1 (cf d1 ... --local),
  3. starts `cf dev` (bounded by a deadline and killed in the finally block) and tr.Handler in a thread on port 0,
  4. GETs every endpoint for every book and chapter, runs one fixed sequence of POSTs against both, GETs everything again,
  5. pulls the work back with `python3 tr.py pull` (a copy of tr.py in a scratch git repository) twice, see round_trip_*,
  5b. restarts `cf dev` with the bypass off and a test key (TR_DEV_ACCESS_KEY) and runs the login, the cookie, Bearer and the pull's key paths end to end,
  6. compares status codes and parsed JSON, prints the first differences, exits 1 on any, 2 on a setup failure.

Left out of the comparison, by design: the `updated` and `created` fields and memo ids (clock and random values).
`last` is compared: both sides get the same call sequence.

  python3 /abs/path/to/cloud/parity.py
"""
import json, os, secrets, shutil, signal, socket, subprocess, sys, tempfile, threading, time, http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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


def start_cf_dev(port, env, logfile, persist):
    p = subprocess.Popen([CF, "dev", "--port", str(port), "--host", "127.0.0.1", "--strictPort"], cwd=CLOUD, env=dict(env, CF_PERSIST_TO=str(persist)),
                         stdout=logfile, stderr=subprocess.STDOUT, start_new_session=True)
    return p


def stop(proc):
    if proc and proc.poll() is None:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)


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
    state = scratch / "d1-state"  # not cloud/.cloudflare/state: the owner's local database is left alone
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
        def cfrun(*args):
            r = subprocess.run([CF, *args], cwd=CLOUD, env=env, capture_output=True, text=True, timeout=120)
            if r.returncode:
                die(f"cf {' '.join(args)[:80]} failed: {(r.stderr or r.stdout)[-400:]}")
            return r.stdout
        cfrun("d1", "migrations", "apply", LOCAL_ID, "--local", "--persist-to", str(state))
        for f in sorted(build.glob("texts-*.json")) + sorted(build.glob("work-*.json")):
            out = cfrun("d1", "raw", LOCAL_ID, "--local", "--persist-to", str(state), "--batch", f"@{f}")
            if '"success": false' in out:
                die(f"a statement of {f.name} failed")
        log("loaded the local D1")
        # 3. the two servers
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), tr.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        py = Side("python", httpd.server_address[1])
        port = free_port()
        logfile = open(scratch / "cf-dev.log", "wb")
        proc = start_cf_dev(port, env, logfile, state)
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
        worker_url = f"http://127.0.0.1:{port}"
        round_trip_seed(cmp, scratch, worker_url, env)
        run_checks(cmp, py, wk)
        round_trip_writes(cmp, scratch, worker_url, env)
        stop(proc)  # the bypass is on for everything above; the key login is tested on a second `cf dev` with it off
        proc = None
        proc = key_login(cmp, scratch, env, state)
        log(f"{cmp.n} comparisons; left out: the fields {IGNORED} and memo ids")
        for m in cmp.bad[:8]:
            log("DIFF", m)
        log(f"{len(cmp.bad)} differences")
        return 1 if cmp.bad else 0
    finally:
        stop(proc)
        if httpd:
            httpd.shutdown()
        shutil.rmtree(scratch, ignore_errors=True)


# ---- the round trip: `python3 tr.py pull` against the Worker ----

def work_files(books):
    """Every file of the owner's work under a books/ folder, as {relative path: path}."""
    out = {}
    for book in sorted(p for p in books.iterdir() if (p / "book.json").exists()):
        for pat in ("vi/*.json", "memos/*.json", "titles.json", "glossary.json", "state.json"):
            for f in book.glob(pat):
                out[str(f.relative_to(books))] = f
    return out


def make_root(scratch, name):
    """A scratch repository holding a copy of tr.py and of books/ with its work spoiled: every vi file emptied (kept),
    the memo files removed, titles, glossary and state emptied (kept), plus one stray memo file. Pull must restore it all."""
    root = scratch / name
    shutil.copytree(REPO / "books", root / "books")
    shutil.copyfile(REPO / "tr.py", root / "tr.py")
    for rel, f in work_files(root / "books").items():
        if "/memos/" in "/" + rel:
            f.unlink()
        elif "/vi/" in "/" + rel or rel.endswith("titles.json") or rel.endswith("state.json"):
            f.write_text("{}\n")
        else:
            f.write_text("[]\n")
    stray = root / "books" / "plato-republic" / "memos" / "002.json"
    stray.parent.mkdir(exist_ok=True)
    stray.write_text('[{"id":"stray","seg":"002.0001","offset":0,"created":"x"}]\n')
    git = lambda *a: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", *a],
                                    cwd=root, capture_output=True, text=True, check=True)
    git("init", "-q")
    git("add", "-A")
    git("commit", "-q", "-m", "scratch")
    return root


def run_tr(root, *args, url=None, env=None, stdin=""):
    """stdin is what the pull reads as its key when TR_CLOUD_KEY is not set; empty by default, never the parent's stdin."""
    e = {k: v for k, v in os.environ.items() if not k.startswith("CF_ACCESS") and k not in ("TR_CLOUD_URL", "TR_CLOUD_KEY")}
    e.update(env or {})
    if url:
        e["TR_CLOUD_URL"] = url
    return subprocess.run([sys.executable, "-I", str(root / "tr.py"), *args], cwd=root, env=e, input=stdin, capture_output=True, text=True, timeout=300)


def check(cmp, label, ok, detail=""):
    cmp.n += 1
    if not ok:
        cmp.bad.append(f"{label}: {detail}")


def round_trip_seed(cmp, scratch, url, env):
    """1. seed -> pull into a spoiled copy -> every work file byte for byte as in the real books/. 4. the guard."""
    root = make_root(scratch, "pull-seed")
    r = run_tr(root, "pull", url=url)
    check(cmp, "pull-seed exit", r.returncode == 0, (r.stderr or r.stdout)[-300:])
    real, got = work_files(REPO / "books"), work_files(root / "books")
    check(cmp, "pull-seed file set", set(real) == set(got), f"only real {sorted(set(real) - set(got))[:3]}, only pulled {sorted(set(got) - set(real))[:3]}")
    diff = [k for k in real if k in got and real[k].read_bytes() != got[k].read_bytes()]
    check(cmp, "pull-seed bytes", not diff, f"{len(diff)} files differ, first {diff[:3]}")
    log(f"round trip 1: {len(real)} work files compared byte for byte, {len(diff)} differ")
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", "commit", "-qam", "pulled"],
                   cwd=root, capture_output=True, check=True)
    again = run_tr(root, "pull", url=url)  # the guard needs a committed tree; a second pull then changes nothing
    check(cmp, "pull-seed second pull", again.returncode == 0 and "Nothing changed" in again.stdout, again.stdout[-200:])
    # no key, no header: the Worker under cf dev (bypass on) needed none. With a key set and a wrong URL, the key is not printed.
    bad = run_tr(root, "pull", url="http://127.0.0.1:9", env={"TR_CLOUD_KEY": "secret-hidden"})
    check(cmp, "pull-unreachable", bad.returncode != 0 and "secret-hidden" not in bad.stdout + bad.stderr, bad.stderr[-200:])
    ua_checks(cmp, root)
    # 4. the guard: a dirty books/ is refused and nothing changes
    for kind in ("modified", "untracked"):
        f = root / "books" / "plato-republic" / ("vi/001.json" if kind == "modified" else "vi/099.json")
        before = {k: v.read_bytes() for k, v in work_files(root / "books").items()}
        f.write_text('{"x": 1}\n')
        before[str(f.relative_to(root / "books"))] = f.read_bytes()
        r = run_tr(root, "pull", url=url)
        after = {k: v.read_bytes() for k, v in work_files(root / "books").items()}
        check(cmp, f"guard {kind}", r.returncode != 0 and "not committed" in r.stderr and before == after, f"exit {r.returncode}: {r.stderr[-200:]}")
        subprocess.run(["git", "checkout", "-q", "--", "books"], cwd=root, check=True)
        subprocess.run(["git", "clean", "-fdq", "--", "books"], cwd=root, check=True)
    log("round trip 4: the guard refused a modified and an untracked file, and changed nothing")


def ua_checks(cmp, root):
    """A stub server on 127.0.0.1 records pulls' headers: the User-Agent is descriptive; without a key no Authorization is sent; with TR_CLOUD_KEY
    or a key on stdin it is `Bearer <key>`. And the key is never sent over plain http to a non-loopback host: the pull exits non-zero
    without a connection, and prints no secret."""
    seen = []

    class Stub(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(dict(self.headers))
            self.send_response(404)
            self.end_headers()

        def log_message(self, *a):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        stub = f"http://127.0.0.1:{httpd.server_address[1]}"
        run_tr(root, "pull", url=stub)
        run_tr(root, "pull", url=stub, env={"TR_CLOUD_KEY": "key-from-env"})
        run_tr(root, "pull", url=stub, stdin="key-from-stdin\n")
        run_tr(root, "pull", url=stub, env={"TR_CLOUD_KEY": "key-from-env"}, stdin="ignored\n")
    finally:
        httpd.shutdown()
        httpd.server_close()
        t.join(10)
    check(cmp, "pull user agent", bool(seen) and seen[0].get("User-Agent") == "philosophy-translation-pull/1", str(seen[:1]))
    check(cmp, "pull sends no header without a key", len(seen) == 4 and "Authorization" not in seen[0], str(seen[:1]))
    check(cmp, "pull key from the environment", len(seen) == 4 and seen[1].get("Authorization") == "Bearer key-from-env", str(seen[1:2]))
    check(cmp, "pull key from stdin", len(seen) == 4 and seen[2].get("Authorization") == "Bearer key-from-stdin", str(seen[2:3]))
    check(cmp, "pull key: the environment wins over stdin", len(seen) == 4 and seen[3].get("Authorization") == "Bearer key-from-env", str(seen[3:4]))
    r = run_tr(root, "pull", url="http://example.invalid", env={"TR_CLOUD_KEY": "secret-fake"})
    out = r.stdout + r.stderr
    check(cmp, "pull key over http refused", r.returncode != 0 and "https://" in out and "secret-fake" not in out and "cannot read" not in out, out[-200:])
    log("round trip 5: the pull sends a descriptive User-Agent and its key as Bearer (environment, then stdin); the key over plain http to a remote host is refused before any request")


def key_login(cmp, scratch, env, state):
    """The key login end to end on a second `cf dev` with the bypass off and a test key (TR_DEV_ACCESS_KEY, read only in development mode).
    Returns the new process, for the caller to stop."""
    key = secrets.token_urlsafe(32)
    port = free_port()
    logfile = open(scratch / "cf-dev-key.log", "wb")
    proc = start_cf_dev(port, dict(env, TR_DEV_ACCESS_KEY=key), logfile, state)
    base = f"http://127.0.0.1:{port}"

    def req(method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
        h = dict(headers or {})
        data = json.dumps(body).encode() if body is not None else None
        if data is not None:
            h["Content-Type"] = "application/json"
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        raw = r.read()
        out = (r.status, r.getheader("Set-Cookie"), json.loads(raw) if raw else None)
        c.close()
        return out

    while True:
        if time.time() > DEADLINE:
            die("cf dev (key login) did not answer in time")
        if proc.poll() is not None:
            die("cf dev (key login) exited: " + (scratch / "cf-dev-key.log").read_text(errors="replace")[-600:])
        try:
            if req("GET", "/api/books")[0] in (200, 401):
                break
        except OSError:
            pass
        time.sleep(1)
    log(f"cf dev with the bypass off answers on 127.0.0.1:{port}")
    check(cmp, "key: no credentials", req("GET", "/api/books")[::2] == (401, {"error": "unauthorized"}))
    check(cmp, "key: POST without credentials", req("POST", "/api/seg/plato-republic/1", {"id": "x"})[0] == 401)
    st, ck, _ = req("POST", "/api/login", {"key": "wrong" * 8})
    check(cmp, "key: wrong key at login", st == 401 and ck is None)
    st, ck, body = req("POST", "/api/login", {"key": key})
    attrs = (ck or "").split("; ")
    check(cmp, "key: right key at login", st == 200 and body == {"ok": True} and attrs[0].startswith("session=") and attrs[1:] == ["HttpOnly", "Secure", "SameSite=Strict", "Path=/", "Max-Age=34560000"] and key not in (ck or ""), str(st) + " " + str(attrs[1:]))
    cookie = attrs[0]
    check(cmp, "key: the cookie passes", req("GET", "/api/books", headers={"Cookie": cookie})[0] == 200)
    check(cmp, "key: a forged cookie fails", req("GET", "/api/books", headers={"Cookie": "session=" + cookie[8:][::-1]})[0] == 401)
    check(cmp, "key: Bearer passes", req("GET", "/api/books", headers={"Authorization": "Bearer " + key})[0] == 200)
    check(cmp, "key: a wrong Bearer fails", req("GET", "/api/books", headers={"Authorization": "Bearer " + key[::-1]})[0] == 401)
    check(cmp, "key: a POST with the cookie works", req("POST", "/api/glossary/nope", {"items": []}, {"Cookie": cookie})[0] == 400)
    check(cmp, "key: a cross-origin login is refused", req("POST", "/api/login", {"key": key}, {"Origin": "http://evil.example"})[0] == 403)
    st, ck, _ = req("POST", "/api/logout")
    check(cmp, "key: logout clears the cookie", st == 200 and (ck or "").startswith("session=;") and "Max-Age=0" in (ck or ""))
    # the pull against a Worker that wants the key
    root = make_root(scratch, "pull-key")
    out = lambda r: r.stdout + r.stderr
    r = run_tr(root, "pull", url=base)
    check(cmp, "key: pull without a key is refused", r.returncode != 0 and "401" in out(r), out(r)[-200:])
    r = run_tr(root, "pull", url=base, env={"TR_CLOUD_KEY": key[::-1]})
    check(cmp, "key: pull with a wrong key is refused", r.returncode != 0 and "401" in out(r) and key not in out(r) and key[::-1] not in out(r), out(r)[-200:])
    r = run_tr(root, "pull", url=base, env={"TR_CLOUD_KEY": key})
    check(cmp, "key: pull with TR_CLOUD_KEY", r.returncode == 0 and key not in out(r), out(r)[-200:])
    subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", "commit", "-qm", "pulled"], cwd=root, capture_output=True, check=True)
    r = run_tr(root, "pull", url=base, stdin=key + "\n")
    check(cmp, "key: pull with the key on stdin", r.returncode == 0 and "Nothing changed" in r.stdout and key not in out(r), out(r)[-200:])
    log("key login: 401 without credentials, wrong key refused, cookie attributes exact, cookie and Bearer pass, forged ones fail, logout clears, pull by environment and stdin works")
    return proc


def parsed_work(books):
    """The work of every book as parsed JSON (an absent file is its empty value), clock values and memo ids left out."""
    out = {}
    for rel, f in work_files(books).items():
        out[rel] = norm(json.loads(f.read_text(encoding="utf-8")))
    for book in sorted(p.name for p in books.iterdir() if (p / "book.json").exists()):
        for pat, empty in (("titles.json", {}), ("glossary.json", []), ("state.json", {})):
            out.setdefault(f"{book}/{pat}", empty)
    return {k: v for k, v in out.items() if v not in ({}, [])}


def round_trip_writes(cmp, scratch, url, env):
    """2. after the fixed writes, pull again into a spoiled copy: the same work as tr.py's own scratch copy, as parsed JSON.
    3. `tr.py check` passes on the pulled copy."""
    root = make_root(scratch, "pull-writes")
    r = run_tr(root, "pull", url=url)
    check(cmp, "pull-writes exit", r.returncode == 0, (r.stderr or r.stdout)[-300:])
    want, got = parsed_work(scratch / "books"), parsed_work(root / "books")
    d = first_diff(want, got)
    check(cmp, "pull-writes parsed", not d and set(want) == set(got), d or f"files only in one side: {sorted(set(want) ^ set(got))[:3]}")
    log(f"round trip 2: {len(want)} non-empty work files compared as parsed JSON: {'same' if not d else d}")
    c = run_tr(root, "check")
    check(cmp, "check on the pulled copy", c.returncode == 0, c.stdout[-300:])
    log(f"round trip 3: tr.py check on the pulled copy exits {c.returncode}")
    # the pull left `last` alone: work reads do not move it
    a = cmp.wk.call("GET", "/api/books")[1]["last"]
    cmp.wk.call("GET", "/api/work/plato-republic")
    check(cmp, "work leaves last alone", cmp.wk.call("GET", "/api/books")[1]["last"] == a)
    check(cmp, "work unknown book", cmp.wk.call("GET", "/api/work/nope")[0] == 404)
    check(cmp, "memo delete with a non-text id", cmp.wk.call("POST", "/api/memos/plato-republic/1", {"delete": 5})[0] == 400)


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


if __name__ == "__main__":
    sys.exit(main())
