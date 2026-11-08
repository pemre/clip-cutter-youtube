# AGENTS.md

Guidance for coding agents (and humans) working on **Clip Cutter YouTube**.

## What this repo is

A two-file application:

| File | Role |
|---|---|
| `index.html` | The whole UI: markup, CSS, vanilla JS, TR/EN strings. No build step, no dependencies, no framework. |
| `server.py` | Python 3.9+ `http.server` app: serves the UI, prepares a cached preview copy, cuts the clip with `yt-dlp`, streams media with HTTP Range support. |

Supporting files: `docs/hero.png` (README screenshot), `cache/` (preview copies, git-ignored), `out/` (rendered clips, git-ignored).

Run: `python3 server.py` → http://localhost:8917 (needs `yt-dlp` and `ffmpeg` on PATH).

## Hard rules

1. **Code in English.** Identifiers, comments, log lines, commit messages. Only the `TEXT` dictionary in `index.html` holds Turkish, and only as a translation of the English UI.
2. **No dependencies, no build step.** If a feature seems to need a framework or a bundler, the feature is wrong. `server.py` uses the standard library only.
3. **Every user-visible string goes through `TEXT`** (`tr` *and* `en`). A missing key silently falls back to the raw key.
4. **Never write into a focused time input.** `paint()` skips the container holding `document.activeElement`; breaking this makes the text caret jump to the end on every keypress.
5. **Start/end state lives in `IN` / `OUT` (milliseconds).** All mutations go through `setSide(side, ms, seek, hard)`; all rendering goes through `paint()`. No parallel state in DOM values.
6. **Media routes must keep Range support.** `serve_media()` answers `206 Partial Content`; without it `<video>` seeking stalls and the UI feels broken.
7. **Server work stays behind `/api/*`.** `index.html` must load fine with no backend — the GitHub Pages demo depends on it.
8. **Turkish UI copy is fine; Turkish code is a bug.**

## Demo mode

`index.html` sets `DEMO = true` on `github.io` or with `?demo`. In demo mode it must:

- show the banner explaining why a server is needed,
- load the public sample video so the timeline is usable,
- answer `/api/preview` and `/api/cut` attempts with a readable message instead of failing silently.

Local development never takes this path (`localhost`).

## How to verify a change

```bash
python3 server.py                     # http://localhost:8917
curl -s -X POST localhost:8917/api/cut -H 'Content-Type: application/json' \
  -d '{"url":"https://www.youtube.com/watch?v=<id>","start":0,"end":10,"name":"check"}'
curl -s "localhost:8917/api/job?id=<id>"                                        # state=done, progress=100
ffprobe -v error -show_entries format=duration -of default=nw=1 out/check.mp4    # 10.0
```

Browser checks worth repeating after UI edits:

- drag both handles → `IN`/`OUT`, the selection band and the command line update, the video seeks;
- hover the bar → ghost line + time tooltip;
- type in a time box → caret stays put, `↑`/`↓` change one unit;
- "Start/End = now" buttons are disabled when the field already equals the playhead;
- `Cut clip` → progress bar moves (≈5 % → 100 %) → download link appears;
- TR/EN toggle flips every label, including `document.title`.

## Style

- Dark, calm, one accent colour (`--acc`), two semantic colours for the handles (`--in`, `--out`).
- Feedback on every action: toast for button presses, status line for loading/errors, progress bar for the cut.
- Keep it boring: no animation libraries, no CSS frameworks, no icon fonts.
