# The workspace online

Purpose, from the owner: the translation workspace reachable from any browser. On a computer it reads and writes; on a phone it is read-only.

## Decisions

| Decision | Reason |
|---|---|
| Cloudflare Workers, Workers Static Assets for `app.html`, D1 for the data, Cloudflare Access for the login | Owner's choice of platform. D1 keeps one row a segment, so autosaves cannot overwrite each other; Access is a login without code; the whole thing runs locally before a deploy |
| The `cf` CLI, a dev dependency in `cloud/` | Cloudflare's guidance for a new project; its develop and deploy commands are stable |
| The Worker in TypeScript | The mainstream Workers path; the Python parser stays where it is (next line) |
| Segments come from `tr.py`'s parser. `python3 tr.py cloud` writes `texts.sql` (the books, safe to reload any time), `work.sql` (the owner's work, a one-time seed) and the Worker's page | Segment ids never depend on a second parser, and reloading a book's text never wipes online work |
| The Worker answers the same API as `tr.py`, reads and writes, with the same JSON | `app.html` runs unchanged, and a parity check compares the two |
| Every API request carries a valid Access token, or is refused; only local development skips the check | A deployed Worker without its Access settings fails closed |
| The phone is read-only by a mode in `app.html` at phone width, not by the API | The API cannot tell a phone reliably, and the phone needs a reading layout, not a security boundary |
| From the cutover, D1 is the record of the owner's work. `python3 tr.py pull` writes it back into the same files, then `python3 tr.py publish` as today | Git history and the Pages site keep working |
| `tr.py pull` refuses while `books/` has uncommitted changes | A local edit is never overwritten without a word |
| `tr.py pull` passes through Access with a service token that only the owner holds | The Worker stays closed to everyone else |
| Timestamps written by the Worker are UTC+7, in `tr.py`'s format | The repository's existing timestamps are local times at that offset |
| Two Workers on `workers.dev`, a preview and the real one, each with its own D1 database, Access on both | Test edits on the preview never touch the real work |
| Database ids and the Access values live in `cloud/deploy.json`, ignored by git | The repository is public, and an Access team domain can name its owner |

## In order

1. `python3 tr.py cloud`: `texts.sql`, `work.sql` and the page, into `cloud/.build/`. About 5 minutes.
2. The Worker's reads: the GET API from D1, `app.html` at `/`. About 10 minutes.
3. The Worker's writes: segments, memos, titles, glossary. About 10 minutes.
4. The Access token check, failing closed. About 5 minutes.
5. Parity, locally: every GET and one fixed sequence of writes, `tr.py` on a scratch copy against the Worker under `cf dev`. About 5 minutes.
6. `python3 tr.py pull`, checked by a round trip: seed a local D1, pull into a scratch copy, compare with `books/`, byte for byte. About 10 minutes.
7. `app.html`'s phone mode: read-only at phone width, checked at that width. About 10 minutes.
8. The owner sets up the preview: sign in, create its database, load it, deploy, turn on Access. Commands handed one by one.
9. The look on the preview, on a computer and on the phone.
10. The cutover: publish the local work, set up the real Worker the same way with the seed loaded once, then translate online. PROCESS.md changes to say so.

Done, with their agent time (the size for the next run of the same kind):

- Steps 1 to 5: about 15 minutes.
- The review's fixes and step 6: about 7 minutes. Fixed: a second Worker and database for the preview, account values in the ignored `cloud/deploy.json`, a build that refuses while a value is empty, `cloud/load-remote.sh` for the remote load, a memo delete answering 400, load files cut by bytes, and parity keeping its own local database.
- Step 7: about 2 minutes. The owner's look at it on a computer is open.

- `tr.py pull` sends a descriptive `User-Agent`, since `workers.dev` refuses Python's default one (error 1010), and it never sends the service token over plain `http` except to this computer: about 1 minute.

Next: step 8, the owner's preview setup.

Access comes from the Worker's **Access** tab in the dashboard, which exists only after a first deploy. So the first deploy of each Worker carries a placeholder `aud`, which refuses everyone, and a second deploy carries the real one.

## The four stages

- **MVP end to end:** steps 1 to 3, `app.html` under `cf dev`, reading and writing a local D1.
- **Verified locally:** `tr.py check`, the TypeScript check, the parity check and the pull round trip, each by its exit code.
- **The look**, one yes or no each:
  1. On a computer, at the preview address: after the Access login, does the library open with all three books?
  2. Type in a segment and reload the page: are your text and its status still there?
  3. On the phone: does a chapter read well, with source, reference and your Vietnamese, and no way to edit?
  4. After the cutover, your first `tr.py pull` and `tr.py publish`: does the public site show what you typed online?

  Gap: no screenshot walk (the project has no browser tests); the owner's computer and phone are the walk.
- **Pre-production:** the preview Worker and its own database.

## Later

- `pull` and `publish` on a schedule, instead of by hand.
