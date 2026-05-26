# AGENTS.md

## Project Overview

This is a local Electron desktop app for archiving exported KakaoTalk chat data. It parses KakaoTalk `.txt` exports with Python, caches parsed messages locally, and displays them in a KakaoTalk-like chat UI.

Treat this directory as the project root:

```text
/Users/sinhee/my-electron-app
```

Do not touch unrelated files outside this project. The parent `/Users/sinhee` directory may also be a Git repository, but this app has its own nested Git repo.

## Project Structure

```text
src/
  electron/
    main.js        # Electron main process, IPC, archive indexing
    preload.js     # Context bridge APIs exposed to renderer
  parser/
    chatParse.py   # KakaoTalk text parser
    main.py        # CLI wrapper around parser
  renderer/
    index.html     # Main app UI
    handleArchive.js
    dropDownSearch.js
    chat.css
    style.css
    img/           # UI icons
local-data/        # Ignored local/private imports and generated data
```

## Commands

Install dependencies:

```bash
npm install
```

Restore Electron binary if `npm start` fails with missing Electron app bundle:

```bash
npm rebuild electron
```

Start the desktop app:

```bash
npm start
```

Run the parser manually:

```bash
python3 src/parser/main.py <input.txt> <output.json>
```

There is currently no real automated test suite. `npm test` is only a placeholder.

## App Flow

Archive import flow:

1. Renderer calls `window.archivesAPI.add()` or `window.archivesAPI.addByPath(path)`.
2. `src/electron/main.js` receives the request through IPC.
3. Electron indexes selected `.txt` files.
4. Electron runs the Python parser:

```bash
python3 src/parser/main.py input.txt output.json
```

5. Parsed messages are merged and sorted.
6. Cached output is written under Electron `userData`:

```text
<userData>/archives/<archiveId>/index.json
```

7. Renderer reads the cached JSON and displays messages as chat bubbles.

## Data Model

Current parsed message shape:

```json
{
  "timestamp": "YYYY-MM-DD HH:mm",
  "sender": "name",
  "content": "message text",
  "time_of_day": "morning|afternoon|evening|night",
  "season": "spring|summer|fall|winter"
}
```

Use `content` for message text in new code. Some older generated files may use `text`; compatibility code may read both, but new parser output should stay consistent.

## Privacy And Data Safety

This app handles private chat history, photos, and videos.

Agents must:

- Do not commit private KakaoTalk exports, photos, videos, generated parser output, or Electron cache files.
- Keep private/local data in `local-data/` or outside the repo.
- Avoid logging message contents unless explicitly needed for debugging.
- Prefer counts, filenames, and metadata over raw chat content in summaries.
- Do not delete, move, or upload user chat/media data without explicit permission.

## Git And Ignored Files

Expected source files are under `src/`, plus `package.json`, `package-lock.json`, `.gitignore`, and docs such as this file.

Ignored/generated items include:

- `node_modules/`
- `.DS_Store`
- `__pycache__/`
- `Talk_*.txt`
- `ex*.json`
- `*.tmp.json`
- `local-data/`
- `archives/`
- `config.json`
- `dist/`
- `build/`

## Coding Guidelines

### Electron

- Keep privileged filesystem and OS access in `src/electron/main.js` or `src/electron/preload.js`.
- Do not expose broad arbitrary filesystem access to the renderer.
- Prefer narrow IPC methods with specific purposes.
- Use `path.join` for filesystem paths.
- Keep archive cache paths based on Electron `app.getPath('userData')`.

### Preload

- Expose only the APIs the renderer needs through `contextBridge`.
- Keep API names stable under `window.archivesAPI` and `window.electronAPI` unless updating all call sites.
- For drag/drop file paths in modern Electron, prefer safe preload helpers rather than relying on renderer-side `file.path`.

### Renderer

- Keep DOM rendering and UI state in `src/renderer/*.js`.
- Preserve the KakaoTalk-like chatroom feel.
- Ensure long messages wrap and do not create horizontal scrolling.
- Avoid large visual redesigns unless requested.

### Parser

- Preserve UTF-8 support.
- Preserve Korean and English KakaoTalk export support.
- Keep parser output deterministic.
- When changing output schema, update renderer code at the same time.
- Be careful with large exports; avoid unnecessary full-data duplication when practical.

## Known Issues

- Drag-and-drop folder import may fail because modern Electron does not reliably expose dropped file paths as `file.path` in renderer code.
- `src/electron/main.js` should use `archiveId` consistently; avoid `archiveID`.
- `src/electron/main.js` currently scans only direct child `.txt` files in a selected folder.
- Media files are not yet linked to `"Photo"` or `"Video"` messages.
- Multiline chat messages may not be fully preserved.
- Search UI exists visually but is not a complete search feature yet.
- There is no test suite yet.

## Verification Checklist

After changing import/indexing behavior:

1. Run `npm start`.
2. Add a folder through the native picker.
3. If touched, test drag-and-drop import.
4. Confirm archive metadata is saved in Electron `userData/config.json`.
5. Confirm cache exists at `<userData>/archives/<archiveId>/index.json`.
6. Confirm messages render in the app.
7. Restart the app and confirm the latest archive can reopen.

After changing parser behavior:

1. Run:

```bash
python3 src/parser/main.py <sample.txt> /private/tmp/parser-output.json
```

2. Confirm JSON is valid.
3. Confirm message count is plausible.
4. Confirm timestamps sort correctly.
5. Confirm Korean AM/PM conversion works.
6. Confirm renderer still displays parsed messages.

After changing UI/CSS:

1. Run `npm start`.
2. Check the main chat view.
3. Check long messages.
4. Check narrow window sizes.
5. Confirm icons still load from `src/renderer/img/`.

## Do Not Touch Unless Asked

- `node_modules/`
- `local-data/`
- Electron `userData` archive cache
- Large/private KakaoTalk exports
- Parent-folder files outside this project
