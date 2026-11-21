# CLAUDE.md

A workspace for translating classical philosophy into Vietnamese, and the read-only reader site built from it. [PROCESS.md](PROCESS.md) is the translator's own process; [recaps/](recaps/) holds the setup decisions and the open questions.

## Who does what

- The owner does all the translating. A session never drafts, rewrites or polishes Vietnamese text; a critique lists errors of meaning and inconsistent terms, and leaves the wording to the owner.
- A session's part: the tooling (`tr.py`, `app.html`, the site), preparing a new book (PROCESS.md, "Add a new book"), and critique when asked.

## The owner's work

`books/<slug>/vi/*.json`, `memos/`, `titles.json`, `glossary.json`, `state.json` and `NOTES.md` are the owner's. A tooling change keeps their format and never edits them by hand. `last.json` (the last book opened) is local and ignored.

## What must not move

- A segment id (`003.0012`, or `003.0012.2` for a piece of a long paragraph) is the chapter number, the paragraph number and the piece, counted from `source.md` with the book's `split_over`. Once a book has translations, its `source.md` keeps its paragraph structure and its `split_over`: words can be corrected, then `python3 tr.py check`.
- Each translation keeps the first 40 characters of its source (`src`), which is how `check` sees a source that changed under it.
- Any new server (a cloud one included) takes its segments from this Python parser (`parse_md`, `split_para`, `is_heading`), never from a re-implementation, so ids stay the same.

## Checks

- `python3 tr.py check`: the gate; its exit code is the verdict.
- `python3 tr.py site`: builds `site/` (ignored), the same command CI runs.
- The API without side effects: import `tr`, serve `tr.Handler` on `127.0.0.1` port 0 in a thread, send GETs, shut it down. Never `tr.py start` for a test: it makes a git commit.

## Side effects

- `tr.py start` makes a signed git commit of `books/` when it starts and when it stops.
- `tr.py publish` runs `git add -A`, commits and pushes. The owner runs it; a session never does.
- `GET /api/chapter/<slug>/<n>` rewrites `last.json`.

## Ship

- A push to `main` starts `.github/workflows/pages.yml`, the only workflow: `tr.py site`, then a deploy to GitHub Pages, https://an-reader.github.io/philosophy-translation/.
- A changed workflow: `actionlint` first, then `act` in the owner's terminal (`runs-on` is `ubuntu-latest`), then GitHub's own run is the verdict.

## Public

The repository and every committed file are public, drafts, notes and memos included; its commits are signed as `Reader`. A committed file names no person and holds no absolute path or other detail of the owner's machine. `archive/` and `.local/` are private and ignored.

## Next piece

The workspace online, from any browser, phone included, on Cloudflare Workers, D1 and Access; slice 1 read-only. The plan, its decisions and its order: [plans/cloud-workspace.md](plans/cloud-workspace.md). The Worker's code lives in `cloud/`; never run a `cf` command that reaches the owner's account (sign-in, deploy, a remote database): hand it to the owner.
