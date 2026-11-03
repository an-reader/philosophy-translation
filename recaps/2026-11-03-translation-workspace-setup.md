# Project recap

Last updated: 2026-11-03. Read this first to pick up the context, then see [PROCESS.md](../PROCESS.md) for the day-to-day process.

## What this is

A hobby project: translating classical philosophy into Vietnamese. The translator, who owns the project, does all the translating. Claude helps with tooling, preparing books, and critique on request. Claude does not draft translations.

## How it works

- **Translate locally:** `python3 tr.py start` opens the workspace at `http://127.0.0.1:8765`.
- **Publish:** `python3 tr.py publish` commits and pushes. GitHub Actions then rebuilds the read-only site.
- **Repository:** `an-reader/philosophy-translation` (public).
- **Website:** https://an-reader.github.io/philosophy-translation/

## Decisions

| Decision | Reason |
|---|---|
| One workspace and process for every book | The project covers many works, not only Kant |
| Original language as source, English as reference | Avoids translating a translator's reading. A book can change this if the translator prefers |
| The translator compares with existing Vietnamese translations only after their own draft | Keeps the translator's voice their own |
| Plain files and Python's standard library, no database | Easy to inspect, back up and move |
| One font: Noto Serif, from a Google Fonts link | The translator wants one free font and no font files stored locally. It handles Vietnamese and polytonic Greek |
| Free GitHub Pages from one public repository | Hobby project, open to everyone |
| Site publishes all translated text, including drafts | The translator's choice. Opens in Song ngữ, because most text is still untranslated |
| Notes, memos, glossary and the English reference stay off the site | The site is for reading. Notes, memos and the glossary can still be read in the public repository |
| `archive/` and `.local/` are excluded from Git | Private files |

## Books

| Folder | Source | Reference | Reference matched by |
|---|---|---|---|
| `nietzsche-zarathustra` | German, Project Gutenberg #7205 | Common, #1998 | Chapter |
| `kant-kritik` | German, 1787 edition, #6343 | Meiklejohn, #4280 | Section (approximate) |
| `plato-republic` | Greek, Burnet, Perseus | Jowett, #1497 | Book |

Each book's `NOTES.md` holds its editions and voice decisions.

## Where things are

- `tr.py`: local server and commands (`start`, `new`, `check`, `status`, `export`, `site`, `publish`)
- `app.html`: the translation workspace
- `reader-template.html`: the single-page export
- `.github/workflows/pages.yml`: site deployment
- `books/<book>/source.md`, `ref-en.md`: texts
- `books/<book>/vi/`, `memos/`, `titles.json`, `glossary.json`, `NOTES.md`: the translator's work

## Rules to keep in mind

- **Don't change the paragraph structure of `source.md` after translating starts.** Translations link to each paragraph's position. Run `python3 tr.py check` after any correction.
- **Kant's text has no A/B page numbers,** so segments are numbered by chapter and paragraph. Many Kant chapter names are short labels added during setup, not Kant's wording.
- **Claude's remote tools can't write inside `.github/`,** and Claude can't sign the translator's commits because they use the translator's GPG key. The translator runs those steps in Terminal.
- **Starting or stopping the workspace makes a local Git commit** of `books/`, including memos. Nothing goes online until a push or `publish`.

## Open questions

- **Memos:** any memo becomes public on the next push, because the repository is public. To keep them private, exclude the `memos` folders from Git before writing any, but they then won't sync to another PC.
- **Licence:** the Greek text of *Republic* is CC BY-SA 4.0, which may also apply to its translation. The translator hasn't chosen a licence for the translations.
- **Older *Zarathustra* work:** The translator's earlier translation and reader haven't been imported.
- **Source language:** confirm that German and Greek suit the translator, especially for *Republic*, before much is translated.
