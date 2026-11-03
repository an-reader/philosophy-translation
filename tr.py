#!/usr/bin/env python3
"""Translation workspace. Needs only Python 3. See PROCESS.md.

  python3 tr.py start [--port 8765] [--no-open]   open the workspace in your browser
  python3 tr.py new <slug>                        create an empty book folder
  python3 tr.py check [slug]                      validate books and translations
  python3 tr.py status                            show progress
  python3 tr.py export <slug>                     write dist/<slug>.html, a reader you can share
  python3 tr.py site                              build the read-only website into site/
  python3 tr.py publish                           commit your work and push it to GitHub
"""
import argparse, html, json, os, re, shutil, subprocess, sys, tempfile, threading, time, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parent
BOOKS = ROOT / "books"
LOCK = threading.Lock()
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")
STATUSES = ("todo", "draft", "reviewed", "final")


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def parse_md(text):
    """'# part', '## chapter', and paragraphs separated by blank lines."""
    chapters, part = [], ""
    for block in re.split(r"\n\s*\n", text.replace("\r", "")):
        block = block.strip("\n")
        if not block.strip():
            continue
        if re.match(r"^# ", block) and "\n" not in block:
            part = block[2:].strip()
        elif re.match(r"^## ", block) and "\n" not in block:
            chapters.append({"part": part, "title": block[3:].strip(), "paras": []})
        else:
            if not chapters:
                chapters.append({"part": part, "title": "(no title)", "paras": []})
            chapters[-1]["paras"].append(re.sub(r"^\\(#)", r"\1", block))
    return chapters


def split_para(text, limit):
    """Split a very long paragraph at sentence ends. Deterministic, so ids stay stable."""
    if len(text) <= limit or "\n" in text:
        return [text]
    sentences = re.split(r"(?<=[.;:?!·])\s+(?=[\"'„»«(\[]?\w)", text)
    out, cur = [], ""
    for s in sentences:
        if cur and len(cur) + len(s) + 1 > limit and len(cur) > limit * 0.35:
            out.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}" if cur else s
    if cur:
        out.append(cur)
    return out


def is_heading(text):
    return ("\n" not in text and len(text) < 90 and
            (re.match(r"^\d+\.$", text) is not None or not re.search(r"[.,;:?!·\"«»-]$", text) or
             re.match(r"^[A-Z]\. [^.]+\.?$", text) is not None))


_cache = {}


def load_book(slug):
    if not SLUG.match(slug or ""):
        raise KeyError(slug)
    d = BOOKS / slug
    meta = read_json(d / "book.json", None)
    if meta is None:
        raise KeyError(slug)
    src = d / meta.get("source_file", "source.md")
    stamp = src.stat().st_mtime_ns
    hit = _cache.get(slug)
    if hit and hit["stamp"] == stamp:
        return hit
    limit = int(meta.get("split_over", 1000))
    chapters = parse_md(src.read_text(encoding="utf-8"))
    for n, ch in enumerate(chapters, 1):
        ch["n"] = n
        ch["segs"] = []
        for p, para in enumerate(ch.pop("paras"), 1):
            parts = split_para(para, limit)
            for i, piece in enumerate(parts, 1):
                sid = f"{n:03d}.{p:04d}" + (f".{i}" if len(parts) > 1 else "")
                ch["segs"].append({"id": sid, "para": p, "text": piece,
                                   "heading": meta.get("detect_headings", True) and len(parts) == 1 and is_heading(piece)})
    refs = []
    for r in meta.get("references", []):
        f = d / r["file"]
        if f.exists():
            refs.append({"label": r["label"], "chapters": parse_md(f.read_text(encoding="utf-8"))})
    book = {"stamp": stamp, "meta": meta, "chapters": chapters, "refs": refs, "dir": d, "slug": slug}
    _cache[slug] = book
    return book


def vi_path(book, n):
    return book["dir"] / "vi" / f"{n:03d}.json"


def load_vi(book, n):
    return read_json(vi_path(book, n), {})


def chapter_progress(book, ch, vi=None):
    vi = load_vi(book, ch["n"]) if vi is None else vi
    out = {"total": 0, "todo": 0, "draft": 0, "reviewed": 0, "final": 0}
    for s in ch["segs"]:
        w = len(s["text"])
        st = (vi.get(s["id"]) or {}).get("status", "todo")
        out["total"] += w
        out[st if st in STATUSES else "todo"] += w
    return out


def book_summary(book):
    chs, tot = [], {"total": 0, "todo": 0, "draft": 0, "reviewed": 0, "final": 0}
    for ch in book["chapters"]:
        pr = chapter_progress(book, ch)
        for k in tot:
            tot[k] += pr[k]
        chs.append({"n": ch["n"], "title": ch["title"], "part": ch["part"], "segs": len(ch["segs"]), "progress": pr})
    return {"slug": book["slug"], "meta": book["meta"], "chapters": chs, "progress": tot,
            "state": read_json(book["dir"] / "state.json", {}), "titles": load_titles(book),
            "refs": [r["label"] for r in book["refs"]]}


def all_slugs():
    return sorted(p.name for p in BOOKS.iterdir() if (p / "book.json").exists()) if BOOKS.exists() else []


def save_seg(book, n, sid, vi_text, status, note):
    ch = next((c for c in book["chapters"] if c["n"] == n), None)
    seg = next((s for s in ch["segs"] if s["id"] == sid), None) if ch else None
    if seg is None:
        raise KeyError(sid)
    with LOCK:
        vi = load_vi(book, n)
        vi_text = vi_text.rstrip()
        if not vi_text and not note.strip():
            vi.pop(sid, None)
            status = "todo"
        else:
            if status not in STATUSES:
                status = "draft"
            if vi_text and status == "todo":
                status = "draft"
            if not vi_text:
                status = "todo"
            vi[sid] = {"vi": vi_text, "status": status, "note": note.rstrip(),
                       "src": seg["text"][:40], "updated": time.strftime("%Y-%m-%dT%H:%M:%S")}
        write_json(vi_path(book, n), vi)
        write_json(book["dir"] / "state.json", {"chapter": n, "seg": sid})
    return {"status": status, "progress": chapter_progress(book, ch, vi)}


def load_titles(book):
    return read_json(book["dir"] / "titles.json", {})


def save_title(book, kind, key, vi_text):
    """Vietnamese titles live in titles.json: book, parts (by original name), chapters (by number)."""
    vi_text = vi_text.strip()
    with LOCK:
        titles = load_titles(book)
        if kind == "book":
            if vi_text:
                titles["book"] = vi_text
            else:
                titles.pop("book", None)
        else:
            if kind == "part":
                valid = any(c["part"] == key for c in book["chapters"])
            elif kind == "chapter":
                valid = key.isdigit() and 1 <= int(key) <= len(book["chapters"])
            else:
                valid = False
            if not valid:
                raise KeyError(key)
            group = titles.setdefault(kind + "s", {})
            if vi_text:
                group[key] = vi_text
            else:
                group.pop(key, None)
        write_json(book["dir"] / "titles.json", titles)
    return titles


def clean_glossary(items):
    out, seen = [], set()
    for g in items:
        term = str(g.get("term", "")).strip()
        if term and term.lower() not in seen:
            seen.add(term.lower())
            out.append({"term": term, "vi": str(g.get("vi", "")).strip(), "note": str(g.get("note", "")).strip()})
    return sorted(out, key=lambda g: g["term"].lower())


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_body(self, body, ctype="application/json; charset=utf-8", code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, data, code=200):
        self.send_body(json.dumps(data, ensure_ascii=False).encode("utf-8"), code=code)

    def do_GET(self):
        parts = [unquote(x) for x in urlparse(self.path).path.split("/") if x]
        try:
            if not parts:
                return self.send_body((ROOT / "app.html").read_bytes(), "text/html; charset=utf-8")
            if len(parts) == 2 and parts[0] == "fonts" and re.match(r"^[a-z0-9-]+\.(woff2|css)$", parts[1]):
                ctype = "font/woff2" if parts[1].endswith("woff2") else "text/css; charset=utf-8"
                return self.send_body((ROOT / "fonts" / parts[1]).read_bytes(), ctype)
            if parts == ["api", "books"]:
                out = []
                for s in all_slugs():
                    b = load_book(s)
                    su = book_summary(b)
                    out.append({"slug": s, "meta": b["meta"], "progress": su["progress"]})
                return self.send_json({"books": out, "last": read_json(ROOT / "last.json", {}).get("book")})
            if len(parts) == 3 and parts[:2] == ["api", "book"]:
                return self.send_json(book_summary(load_book(parts[2])))
            if len(parts) == 4 and parts[:2] == ["api", "chapter"]:
                b = load_book(parts[2])
                n = int(parts[3])
                ch = b["chapters"][n - 1]
                refs = [{"label": r["label"], "paras": (r["chapters"][n - 1]["paras"] if n <= len(r["chapters"]) else [])}
                        for r in b["refs"]]
                write_json(ROOT / "last.json", {"book": parts[2]})
                return self.send_json({"n": n, "title": ch["title"], "part": ch["part"], "segs": ch["segs"],
                                       "vi": load_vi(b, n), "refs": refs})
            if len(parts) == 3 and parts[:2] == ["api", "glossary"]:
                return self.send_json(read_json(load_book(parts[2])["dir"] / "glossary.json", []))
        except (KeyError, IndexError, ValueError, FileNotFoundError):
            pass
        self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc != self.headers.get("Host"):
            return self.send_json({"error": "forbidden"}, 403)
        parts = [unquote(x) for x in urlparse(self.path).path.split("/") if x]
        try:
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if len(parts) == 4 and parts[:2] == ["api", "seg"]:
                r = save_seg(load_book(parts[2]), int(parts[3]), str(data["id"]), str(data.get("vi", "")),
                             str(data.get("status", "draft")), str(data.get("note", "")))
                return self.send_json(r)
            if len(parts) == 3 and parts[:2] == ["api", "titles"]:
                return self.send_json(save_title(load_book(parts[2]), str(data["kind"]), str(data.get("key", "")), str(data.get("vi", ""))))
            if len(parts) == 3 and parts[:2] == ["api", "glossary"]:
                b = load_book(parts[2])
                items = clean_glossary(data["items"])
                with LOCK:
                    write_json(b["dir"] / "glossary.json", items)
                return self.send_json(items)
        except (KeyError, IndexError, ValueError, TypeError):
            pass
        self.send_json({"error": "bad request"}, 400)


def checkpoint():
    """If this folder is a git repository, commit your work so far."""
    if not (ROOT / ".git").exists():
        return
    try:
        subprocess.run(["git", "add", "-A", "books"], cwd=ROOT, check=True, capture_output=True)
        changed = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode
        if changed:
            subprocess.run(["git", "commit", "-q", "-m", "checkpoint " + time.strftime("%Y-%m-%d %H:%M")],
                           cwd=ROOT, check=True, capture_output=True)
            print("Saved a git checkpoint.")
    except (OSError, subprocess.CalledProcessError) as e:
        print("Git checkpoint skipped:", e)


def cmd_start(a):
    checkpoint()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    url = f"http://127.0.0.1:{a.port}/"
    print(f"Workspace: {url}\nPress Ctrl+C to stop.")
    if not a.no_open:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        checkpoint()
        print("\nStopped. Everything is saved.")


def cmd_new(a):
    if not SLUG.match(a.slug):
        sys.exit("Use lowercase letters, digits and hyphens, for example: plato-apology")
    d = BOOKS / a.slug
    if d.exists():
        sys.exit(f"{d} already exists")
    d.mkdir(parents=True)
    write_json(d / "book.json", {"title": a.slug, "author": "", "source_lang": "", "source_label": "Source",
                                 "references": [{"file": "ref-en.md", "label": "English"}],
                                 "split_over": 1000, "provenance": ""})
    (d / "source.md").write_text("# Part title (optional)\n\n## Chapter title\n\nFirst paragraph.\n\nSecond paragraph.\n", encoding="utf-8")
    (d / "ref-en.md").write_text("## Chapter title\n\nReference text for the same chapter.\n", encoding="utf-8")
    (d / "NOTES.md").write_text("# Decisions\n\n- Source edition:\n- Reference:\n- Voice / register:\n- Decisions to remember:\n", encoding="utf-8")
    print(f"Created {d}. See 'Add a new book' in PROCESS.md.")


def cmd_check(a):
    problems = 0
    for s in ([a.slug] if a.slug else all_slugs()):
        b = load_book(s)
        ids = {}
        for ch in b["chapters"]:
            for seg in ch["segs"]:
                ids[seg["id"]] = (ch["n"], seg)
        for r in b["refs"]:
            if len(r["chapters"]) != len(b["chapters"]):
                problems += 1
                print(f"{s}: reference '{r['label']}' has {len(r['chapters'])} chapters, source has {len(b['chapters'])}")
        for f in sorted((b["dir"] / "vi").glob("*.json")) if (b["dir"] / "vi").exists() else []:
            for sid, e in json.loads(f.read_text(encoding="utf-8")).items():
                if sid not in ids:
                    problems += 1
                    print(f"{s}: {sid} is translated but no longer exists in the source")
                elif e.get("src") and not ids[sid][1]["text"].startswith(e["src"]):
                    problems += 1
                    print(f"{s}: source text changed under translated segment {sid}")
        print(f"{s}: {len(b['chapters'])} chapters, {len(ids)} segments")
    print("No problems." if not problems else f"{problems} problem(s) found.")
    if problems:
        sys.exit(1)


def cmd_status(a):
    for s in all_slugs():
        p = book_summary(load_book(s))["progress"]
        t = p["total"] or 1
        print(f"{s:28} draft {p['draft']/t:6.2%}  reviewed {p['reviewed']/t:6.2%}  final {p['final']/t:6.2%}")


def inline(text):
    t = html.escape(text, quote=False)
    t = re.sub(r"(?<![\w_])_([^_\n]+)_(?![\w_])", r"<em>\1</em>", t)
    return t.replace("\n", "<br>")


def cmd_export(a):
    b = load_book(a.slug)
    m = b["meta"]
    titles = load_titles(b)
    def heading(vi, orig):
        if not vi:
            return html.escape(orig)
        return f'<span class="vi">{html.escape(vi)}</span><small class="src">{html.escape(orig)}</small><span class="src only">{html.escape(orig)}</span>'
    nav, body, part = [], [], None
    for ch in b["chapters"]:
        vi = load_vi(b, ch["n"])
        if ch["part"] != part and ch["part"]:
            part = ch["part"]
            part_vi = titles.get("parts", {}).get(part, "")
            nav.append(f"<h3>{html.escape(part_vi or part)}</h3>")
            body.append(f'<h2 class="part">{heading(part_vi, part)}</h2>')
        ch_vi = titles.get("chapters", {}).get(str(ch["n"]), "")
        nav.append(f'<a href="#c{ch["n"]}">{html.escape(ch_vi or ch["title"])}</a>')
        body.append(f'<section id="c{ch["n"]}"><h2>{heading(ch_vi, ch["title"])}</h2>')
        para, src, tr = None, [], []
        def flush():
            if src:
                body.append(f'<div class="row"><p class="src">{" ".join(src)}</p>'
                            f'<p class="vi{"" if tr else " empty"}">{" ".join(tr) if tr else "[Chưa dịch]"}</p></div>')
        for s in ch["segs"]:
            if s["para"] != para:
                flush()
                para, src, tr = s["para"], [], []
            src.append(inline(s["text"]))
            v = (vi.get(s["id"]) or {}).get("vi", "")
            if v:
                tr.append(inline(v))
        flush()
        body.append("</section>")
    page = (ROOT / "reader-template.html").read_text(encoding="utf-8")
    page = (page.replace("{{TITLE}}", html.escape(titles.get("book") or m.get("title_vi") or m["title"]))
            .replace("{{AUTHOR}}", html.escape(m.get("author", "")))
            .replace("{{SOURCE_LABEL}}", html.escape(m.get("source_label", "Source")))
            .replace("{{PROVENANCE}}", html.escape(m.get("provenance", "")))
            .replace("{{NAV}}", "\n".join(nav)).replace("{{BODY}}", "\n".join(body)))
    out = ROOT / "dist" / f"{a.slug}.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(page, encoding="utf-8")
    shutil.copytree(ROOT / "fonts", out.parent / "fonts", dirs_exist_ok=True)
    print(f"Wrote {out} (share it together with dist/fonts)")


SITE_CSS = """:root{--bg:#faf7f0;--ink:#26221c;--mut:#766d5f;--line:#e0d8c8;--acc:#8a3b1e;--bar:#e0d8c8;--on:#faf7f0}
@media (prefers-color-scheme:dark){:root{--bg:#1b1916;--ink:#ebe5d9;--mut:#a39a8c;--line:#3a352c;--acc:#e29a74;--bar:#3a352c;--on:#1b1916}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:18px/1.75 "Literata Variable","Noto Serif Variable",Georgia,serif}
a{color:var(--acc)}.top{display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:10px 16px;border-bottom:1px solid var(--line);font:14px/1.4 system-ui,-apple-system,sans-serif;position:sticky;top:0;background:var(--bg)}.top .crumb{margin-right:auto;min-width:0}
button{font:inherit;color:inherit;background:none;border:1px solid var(--line);border-radius:6px;padding:5px 10px;cursor:pointer}button.on{background:var(--acc);border-color:var(--acc);color:var(--on)}
main{max-width:1180px;margin:auto;padding:24px 16px 64px}.narrow{max-width:720px}h1,h2{line-height:1.3}h1{font-size:30px;margin:.4em 0 .2em}h1 small,h2 small{display:block;font-size:.6em;font-weight:400;color:var(--mut)}.by{color:var(--mut);margin:0 0 24px}
.row{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:36px}.row p{margin:0 0 1em;overflow-wrap:anywhere}.src{color:var(--mut)}.empty{font-style:italic;color:var(--mut)}
body.vi .src,body.source .vi{display:none}body.vi .row,body.source .row{grid-template-columns:minmax(0,1fr);max-width:720px;margin:auto}.source .src{color:inherit}.head-source{display:none}body.source .head-source{display:inline}body.source .head-vi{display:none}
.list{list-style:none;margin:0;padding:0}.list li{display:flex;justify-content:space-between;gap:16px;padding:8px 0;border-bottom:1px solid var(--line)}.list a{text-decoration:none}.list a:hover{text-decoration:underline}.pct{color:var(--mut);font:13px/1.8 system-ui,sans-serif;white-space:nowrap}.partname{font:600 13px/1.4 system-ui,sans-serif;color:var(--mut);margin:28px 0 4px}
.pager{display:flex;justify-content:space-between;gap:16px;margin-top:40px;font:15px/1.4 system-ui,sans-serif}footer{color:var(--mut);font-size:13px;line-height:1.5;margin-top:48px;border-top:1px solid var(--line);padding-top:12px}
@media (max-width:760px){.row{grid-template-columns:minmax(0,1fr);gap:0}body{font-size:17px}}
"""

SITE_JS = """<script>(function(){var m='both';try{m=localStorage.getItem('reader-mode')||'both'}catch(e){}
var bs=document.querySelectorAll('button[data-m]');function set(x){document.body.className=x==='both'?'':x;bs.forEach(function(b){b.classList.toggle('on',b.dataset.m===x)});try{localStorage.setItem('reader-mode',x)}catch(e){}}
bs.forEach(function(b){b.onclick=function(){set(b.dataset.m)}});set(m)})()</script>"""


def site_page(title, prefix, crumb, content, controls=""):
    return (f'<!doctype html><html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{html.escape(title)}</title><link rel="stylesheet" href="{prefix}fonts/fonts.css"><link rel="stylesheet" href="{prefix}site.css"></head>'
            f'<body><div class="top"><span class="crumb">{crumb}</span>{controls}</div>{content}{SITE_JS if controls else ""}</body></html>')


def percent(p):
    return f"{(p['total'] - p['todo']) / p['total'] * 100:.0f}%" if p["total"] else "0%"


def cmd_site(a):
    out = ROOT / "site"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir()
    shutil.copytree(ROOT / "fonts", out / "fonts")
    (out / "site.css").write_text(SITE_CSS, encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    home = []
    for slug in all_slugs():
        b = load_book(slug)
        m, titles = b["meta"], load_titles(b)
        book_title = titles.get("book") or m.get("title_vi") or m["title"]
        (out / slug).mkdir()
        chapters, toc, part, total = b["chapters"], [], None, book_summary(b)["progress"]
        label = html.escape(m.get("source_label") or "Source")
        controls = (f'<button data-m="vi">Tiếng Việt</button><button data-m="both">Song ngữ</button>'
                    f'<button data-m="source">{label}</button>')
        for i, ch in enumerate(chapters):
            n, vi = ch["n"], load_vi(b, ch["n"])
            if ch["part"] and ch["part"] != part:
                part = ch["part"]
                part_vi = titles.get("parts", {}).get(part, "")
                toc.append(f'<li class="partname">{html.escape(part_vi or part)}</li>')
            ch_vi = titles.get("chapters", {}).get(str(n), "")
            toc.append(f'<li><a href="{n}.html">{html.escape(ch_vi or ch["title"])}</a><span class="pct">{percent(chapter_progress(b, ch, vi))}</span></li>')
            rows, para, src, tr = [], None, [], []
            def flush():
                if src:
                    rows.append(f'<div class="row"><p class="src" lang="{html.escape(m.get("source_lang", ""))}">{" ".join(src)}</p>'
                                f'<p class="vi{"" if tr else " empty"}">{" ".join(tr) if tr else "[Chưa dịch]"}</p></div>')
            for s in ch["segs"]:
                if s["para"] != para:
                    flush()
                    para, src, tr = s["para"], [], []
                src.append(inline(s["text"]))
                v = (vi.get(s["id"]) or {}).get("vi", "")
                if v:
                    tr.append(inline(v))
            flush()
            orig = html.escape(ch["title"])
            head = (f'<span class="head-vi">{html.escape(ch_vi)}<small class="src">{orig}</small></span><span class="head-source">{orig}</span>' if ch_vi else orig)
            prev = f'<a href="{n - 1}.html">← {html.escape(titles.get("chapters", {}).get(str(n - 1)) or chapters[i - 1]["title"])}</a>' if i else "<span></span>"
            nxt = f'<a href="{n + 1}.html">{html.escape(titles.get("chapters", {}).get(str(n + 1)) or chapters[i + 1]["title"])} →</a>' if i + 1 < len(chapters) else "<span></span>"
            crumb = f'<a href="../index.html">Thư viện</a> / <a href="index.html">{html.escape(book_title)}</a>'
            (out / slug / f"{n}.html").write_text(site_page(
                f"{ch_vi or ch['title']} · {book_title}", "../", crumb,
                f'<main><h1>{head}</h1>{"".join(rows)}<nav class="pager">{prev}{nxt}</nav></main>', controls), encoding="utf-8")
        credit = f'<footer>{html.escape(m.get("provenance", ""))}</footer>'
        (out / slug / "index.html").write_text(site_page(
            book_title, "../", '<a href="../index.html">Thư viện</a>',
            f'<main class="narrow"><h1>{html.escape(book_title)}</h1><p class="by">{html.escape(m.get("author", ""))} · đã dịch {percent(total)}</p>'
            f'<ul class="list">{"".join(toc)}</ul>{credit}</main>'), encoding="utf-8")
        home.append(f'<li><a href="{slug}/index.html">{html.escape(book_title)}</a><span class="pct">{html.escape(m.get("author", ""))} · {percent(total)}</span></li>')
    (out / "index.html").write_text(site_page(
        "Dịch triết học cổ điển", "", "Thư viện",
        f'<main class="narrow"><h1>Dịch triết học cổ điển</h1><p class="by">Bản dịch tiếng Việt đang thực hiện.</p><ul class="list">{"".join(home)}</ul></main>'), encoding="utf-8")
    print(f"Built {out}")


def cmd_publish(a):
    def git(*args, check=True):
        return subprocess.run(["git", *args], cwd=ROOT, check=check)
    if not (ROOT / ".git").exists():
        sys.exit("This folder is not connected to GitHub yet. See 'Publish to the web' in PROCESS.md.")
    try:
        git("add", "-A")
        if git("diff", "--cached", "--quiet", check=False).returncode:
            git("commit", "-q", "-m", "Update translation " + time.strftime("%Y-%m-%d %H:%M"))
        git("push")
    except subprocess.CalledProcessError:
        sys.exit("Publishing failed. Your work is still saved here. Read the Git message above.")
    print("Pushed. The website updates in a few minutes.")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd")
    s = sub.add_parser("start"); s.add_argument("--port", type=int, default=8765); s.add_argument("--no-open", action="store_true"); s.set_defaults(fn=cmd_start)
    s = sub.add_parser("new"); s.add_argument("slug"); s.set_defaults(fn=cmd_new)
    s = sub.add_parser("check"); s.add_argument("slug", nargs="?"); s.set_defaults(fn=cmd_check)
    s = sub.add_parser("status"); s.set_defaults(fn=cmd_status)
    s = sub.add_parser("export"); s.add_argument("slug"); s.set_defaults(fn=cmd_export)
    s = sub.add_parser("site"); s.set_defaults(fn=cmd_site)
    s = sub.add_parser("publish"); s.set_defaults(fn=cmd_publish)
    a = p.parse_args()
    if not a.cmd:
        a = p.parse_args(["start"])
    try:
        a.fn(a)
    except KeyError as e:
        sys.exit(f"Unknown book: {e.args[0]}")


if __name__ == "__main__":
    main()
