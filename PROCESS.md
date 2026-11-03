# How to work

The same process applies to every book. Your job is reading and translating. The workspace handles files, saving, progress and the reader.

## Start working

```bash
cd ~/works/co-work/experiments/philosophy-translation
python3 tr.py start
```

The workspace opens in your browser and returns to where you stopped. Stop it with Ctrl+C. Your work saves as you type.

## The daily loop

1. **Read first.** Read the whole passage before typing. Open **Reference** if you are stuck.
2. **Translate a small piece.** Fifteen minutes and a few paragraphs is a valid session. Press `Cmd+Enter` to move to the next segment.
3. **Record real decisions.** Select a source word and press **+ Glossary** when you settle a term. Use **Note** on a passage you are unsure about.
4. **Read it back.** Switch to **Read** and read only the Vietnamese. If it sounds wrong aloud, fix it now or leave it as a draft.

Do not polish during the first pass. Finish the chapter, then revise it.

## Status means something

| Status | Meaning | How it is set |
|---|---|---|
| `todo` | Nothing written | Automatic |
| `draft` | Meaning captured, wording rough | Automatic when you type |
| `reviewed` | Checked again against the source, reads well | `Cmd+Shift+Enter`, or click the status |
| `final` | Read in the context of the finished chapter or book, you would publish it | Click the status |

The percentage at the top counts drafted text. The colours on the bar show how much is reviewed and final.

## The life of a book

### 1. Choose the source

Decide this before translating, and write it in the book's `NOTES.md`.

- Prefer the original language as the source if you can follow it, and a translation as the reference.
- If you translate from English, say so in `NOTES.md`. It is a valid choice, but you are translating that translator's reading.

### 2. Add the book

See **Add a new book** below.

### 3. Run a pilot (about 20 segments)

Translate 20 segments from an easy passage and 20 from a hard one. Then write in `NOTES.md`:

- **Voice:** the register, how to handle pronouns (such as *tôi*, *ta*, *ngươi*), and how archaic the Vietnamese should be.
- **Terms:** the first 10 to 20 terms that will recur, in the glossary.

Decide these now, because changing them is expensive after 200 pages.

### 4. Draft in order

Work through chapter by chapter with the daily loop.

### 5. Review each finished chapter

Read the whole chapter in **Song ngữ** (side by side). Clear the ⚠ glossary warnings, resolve your notes, and mark segments `reviewed`.

### 6. Compare with others, last

Compare with existing translations only after your own draft and review, so your voice stays your own. Do not copy their wording.

### 7. Finish and share

Run a final pass in **Tiếng Việt** mode and mark segments `final`. Then export:

```bash
python3 tr.py export plato-republic
```

This writes a reader page and its fonts to `dist/`. Keep them together when you share or upload them. Before publishing, check the edition's licence in `NOTES.md`.

## Publish to the web

Translating happens only on your computer. The website is read-only. To update it after a session:

```bash
python3 tr.py publish
```

This commits your work and pushes it to GitHub. GitHub Actions then rebuilds the site, usually within a few minutes.

- **Everything in this repository is public**, including drafts, notes and `NOTES.md`. `archive/` stays on your computer.
- **Using another PC:** run `git pull` before you start, and `python3 tr.py publish` when you finish.
- **Preview the site locally:** run `python3 tr.py site`, then open `site/index.html`.

## Add a new book

Ask Claude:

> Add a new book to this workspace, following PROCESS.md: **[title, author]**. Use the original language as the source and **[translator]** as the reference. Find public-domain or openly licensed texts, split them into matching chapters, record the editions and licences in NOTES.md, and run `python3 tr.py check`. Do not translate anything.

Or do it yourself:

```bash
python3 tr.py new plato-apology
```

Then fill in the new folder:

| File | What it is |
|---|---|
| `book.json` | Title, author, source language, reference labels |
| `source.md` | The text you translate from |
| `ref-en.md` | A reference text with the same chapters in the same order |
| `NOTES.md` | Your editions and decisions |

The format of `source.md` and `ref-en.md`:

```markdown
# Part title (optional)

## Chapter title

A paragraph, on one line.

Another paragraph.
Line breaks inside a paragraph are kept, which suits verse.
```

Run `python3 tr.py check` when you are done.

## Rules that keep your work safe

- **Do not edit `source.md` after you begin translating.** Segment IDs come from its order. If you must correct it, change only words, not the paragraph structure, then run `python3 tr.py check`.
- **Your work is in `books/<book>/vi/`, `titles.json` and `glossary.json`.** These are plain JSON, so you are never locked in.
- **Use Git for history.** Run `git init` once in this folder. After that, the workspace commits your work each time it starts and stops, if Git is set up with your name and email.

## Where Claude fits, only when you ask

You do the translating. Claude is useful for what surrounds it:

- Preparing a new book, as above.
- Critiquing a chapter you finished: *"Review my translation of chapter 3 against the source. List errors of meaning and inconsistent terms. Do not rewrite it."*
- Checking terms across a book: *"Find where I translated the same term differently."*
