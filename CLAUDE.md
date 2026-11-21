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
- The Worker, in `cloud/`: `pnpm run typecheck`, `pnpm test` (the key login), and `python3 cloud/parity.py`, which compares every GET and a fixed sequence of writes between `tr.py` (on a scratch copy of `books/`) and the Worker under `cf dev`, then the `tr.py pull` round trips, then the key login end to end on a second `cf dev` (`TR_DEV_ACCESS_KEY`, read in development mode only). Parity keeps its local D1 in its own scratch folder (`CF_PERSIST_TO`).
- `cf dev` in the sandbox: its dev registry under `~/Library/Preferences/cloudflare` is denied, and its file watcher runs out of file handles. A run sets `CLOUDFLARE_DEV_REGISTRY_PATH` and `XDG_CONFIG_HOME` to a scratch folder and `CHOKIDAR_USEPOLLING=1`, as `parity.py` does.

## Side effects

- `tr.py start` makes a signed git commit of `books/` when it starts and when it stops.
- The committed file `cloud/ONLINE` means the online database is the record: `tr.py start` then opens read-only and its server refuses every POST. Deleting the file and committing that is the only way back.
- `tr.py publish` runs `git add -A`, commits and pushes. The owner runs it; a session never does.
- `GET /api/chapter/<slug>/<n>` rewrites `last.json` (the Worker: its `last` row).
- `tr.py pull` rewrites the owner's work files in `books/` from the online database; it refuses while `books/` has uncommitted changes.
- `tr.py pull` takes the Worker's key from `TR_CLOUD_KEY`, a pipe (`pbpaste | python3 tr.py pull`) or a hidden prompt.
- `bash cloud/load-remote.sh <preview|real> work` replaces all online work of that target; it asks the owner to type the target's name.

## Ship

- A push to `main` starts `.github/workflows/pages.yml`, the only workflow: `tr.py site`, then a deploy to GitHub Pages, https://an-reader.github.io/philosophy-translation/.
- Cloudflare, run by the owner from `cloud/` after `python3 tr.py cloud`: `pnpm exec cf deploy --mode preview` (the preview Worker, its own database) and `pnpm exec cf deploy` (the real one). The account's values live in `cloud/deploy.json`, ignored by git; `deploy.example.json` shows its shape. `cf previews deploy` is refused by the config: it would reuse the real database.
- Each Worker's login is its secret `ACCESS_KEY` (32 characters or more; without it the API refuses everything). The owner sets it in the dashboard (the Worker's Settings, Variables and Secrets), never on a command line: `cf workers secrets update` takes the value only as an argument.
- On `workers.dev`, Cloudflare's Browser Integrity Check refuses Python's default `urllib` user agent (error 1010) before the Worker runs: a script calling the Worker sends a descriptive `User-Agent`.
- A changed workflow: `actionlint` first, then `act` in the owner's terminal (`runs-on` is `ubuntu-latest`), then GitHub's own run is the verdict.

## Public

The repository and every committed file are public, drafts, notes and memos included; its commits are signed as `Reader`. A committed file names no person and holds no absolute path or other detail of the owner's machine. `archive/` and `.local/` are private and ignored.

## The online workspace

Live since 2026-11-22: the online database is the record of the owner's work (`cloud/ONLINE`). The workspace online on Cloudflare Workers and D1, behind an access key the Worker checks itself (no Cloudflare Access: see the plan): reads and writes on a computer, read-only on a phone. The plan, its decisions and its order: [plans/cloud-workspace.md](plans/cloud-workspace.md). The Worker's code lives in `cloud/`; never run a `cf` command that reaches the owner's account (sign-in, deploy, a remote database): hand it to the owner.
