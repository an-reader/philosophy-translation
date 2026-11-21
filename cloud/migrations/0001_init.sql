-- One row per segment, per reference paragraph, per translation and per memo, so a statement stays far
-- under D1's 100 KB statement limit.
--
-- Two groups of tables, loaded by two files from `python3 tr.py cloud`:
--   texts.sql  books, chapters, segs, refs, ref_paras: the source texts. Deleted and reinserted; safe any time.
--   work.sql   book_work, vi, memos, app: the owner's work. The one-time seed; loading it replaces all of it.

-- texts ------------------------------------------------------------------------------------------

CREATE TABLE books (
  slug TEXT PRIMARY KEY,
  pos  INTEGER NOT NULL,       -- order of tr.all_slugs()
  meta TEXT NOT NULL           -- book.json, as JSON
);

CREATE TABLE chapters (
  slug  TEXT NOT NULL,
  n     INTEGER NOT NULL,
  title TEXT NOT NULL,
  part  TEXT NOT NULL,
  PRIMARY KEY (slug, n)
);

CREATE TABLE segs (
  slug    TEXT NOT NULL,
  n       INTEGER NOT NULL,
  pos     INTEGER NOT NULL,
  id      TEXT NOT NULL,       -- 003.0012 or 003.0012.2, from tr.py's parser
  para    INTEGER NOT NULL,
  text    TEXT NOT NULL,
  heading INTEGER NOT NULL,
  PRIMARY KEY (slug, n, pos)
);
CREATE INDEX segs_by_id ON segs (slug, n, id);

CREATE TABLE refs (
  slug  TEXT NOT NULL,
  ref   INTEGER NOT NULL,
  label TEXT NOT NULL,
  PRIMARY KEY (slug, ref)
);

CREATE TABLE ref_paras (
  slug TEXT NOT NULL,
  ref  INTEGER NOT NULL,
  n    INTEGER NOT NULL,       -- chapter number in the reference text
  pos  INTEGER NOT NULL,
  text TEXT NOT NULL,
  PRIMARY KEY (slug, ref, n, pos)
);

-- work -------------------------------------------------------------------------------------------

CREATE TABLE book_work (
  slug     TEXT PRIMARY KEY,
  state    TEXT NOT NULL,      -- state.json (the reading position), as JSON
  titles   TEXT NOT NULL,      -- titles.json, as JSON
  glossary TEXT NOT NULL       -- glossary.json, as a JSON list
);

CREATE TABLE vi (
  slug TEXT NOT NULL,
  n    INTEGER NOT NULL,
  id   TEXT NOT NULL,
  data TEXT NOT NULL,          -- the entry of vi/<n>.json, as JSON
  PRIMARY KEY (slug, n, id)
);

CREATE TABLE memos (
  slug    TEXT NOT NULL,
  n       INTEGER NOT NULL,
  id      TEXT NOT NULL,
  seq     INTEGER NOT NULL,    -- file order: breaks ties of (seg, off, created) as tr.py's stable sort does
  seg     TEXT NOT NULL,
  off     INTEGER NOT NULL,
  created TEXT NOT NULL,
  data    TEXT NOT NULL,       -- one memo, as JSON
  PRIMARY KEY (slug, n, id)
);
CREATE INDEX memos_order ON memos (slug, n, seg, off, created, seq);

-- Values the Worker keeps itself: 'last', the last book opened.
CREATE TABLE app (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
