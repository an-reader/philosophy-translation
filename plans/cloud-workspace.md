# The workspace online

Purpose, from the owner: the translation workspace reachable from any browser, phone included. Slice 1 is read-only; translating stays on `tr.py start` until slice 2.

## Decisions

| Decision | Reason |
|---|---|
| Cloudflare Workers, Workers Static Assets for `app.html`, D1 for the data, Cloudflare Access for the login | Owner's choice of platform. D1 keeps one row a segment, so autosaves cannot overwrite each other; Access is a login without code; the whole thing runs locally before a deploy |
| The `cf` CLI, a dev dependency in `cloud/` | Cloudflare's guidance for a new project; its develop and deploy commands are stable |
| The Worker in TypeScript | The mainstream Workers path; the Python parser stays where it is (next line) |
| Segments come from `tr.py`'s parser: `python3 tr.py cloud` writes the D1 data and copies `app.html` | Segment ids never depend on a second parser |
| The Worker answers the same GET API as `tr.py`, with the same JSON | `app.html` runs unchanged, and a parity check compares the two |
| Slice 1: the repository is the record and D1 is reloaded from it in full | Read-only, so nothing in D1 can be lost |
| Slice 2: D1 is the record for the owner's work, and a sync writes it back into the same `vi/*.json` files | Git history and the Pages site keep working |
| Hosted on the Worker's `workers.dev` address, Access turned on for it | No domain needed |

## Slice 1: read-only, in order

1. `python3 tr.py cloud`: writes the D1 schema and a full-reload data file into `cloud/.build/`, and copies `app.html` as the Worker's page. About 5 minutes.
2. The Worker: the GET API from D1, `app.html` at `/`, every POST answered 405 read-only. About 10 minutes.
3. Parity, locally: every GET endpoint of every book, `tr.py` against the Worker under `cf dev`, equal JSON. About 5 minutes.
4. `app.html` in read-only mode: editing switched off when the API says read-only, checked at phone width. About 10 minutes.
5. The owner deploys: sign in, create the database, load it, deploy a preview, turn on Access. Commands handed one by one.
6. The phone look on the preview address, then the deploy.

## The four stages of slice 1

- **MVP end to end:** steps 1 and 2, `app.html` served by `cf dev` from a local D1.
- **Verified locally:** `tr.py check`, the TypeScript check and the parity check, each by its exit code.
- **The look:** on the owner's phone, at the preview address, one yes or no each:
  1. After the Access login, does the library open with all three books?
  2. Does a chapter open with its source, its reference and your Vietnamese, in a layout you can read on the phone?
  3. Do your memos, notes and glossary show?
  4. Is it clear that nothing can be edited there?
  Gap: no screenshot walk (the project has no browser tests); the owner's phone is the walk.
- **Pre-production:** the uploaded version's preview address, checked before the deploy. Gap: the preview shares the one D1 database, which is safe only while slice 1 writes nothing; slice 2 gets a database of its own for previews.

## Slice 2, to plan after slice 1

Writes from the Worker, the Worker checking Access's token on every write, a preview database, the sync back into the repository, and what happens to an edit made locally and online on the same day.
