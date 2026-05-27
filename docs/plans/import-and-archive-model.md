# Import And Archive Data Model Plan

## Problem

The app needs a reliable import pipeline and archive data model before adding media rendering, search, date navigation, and analysis features.

Right now the app can parse KakaoTalk `.txt` exports into JSON text messages, but it does not yet reliably import dropped folders, identify full KakaoTalk export folder contents, connect media placeholders to photo/video/file assets, or preserve enough structured metadata for future analysis.

The goal is to define the next implementation path for:

- selecting or dropping a KakaoTalk export folder
- discovering chat text, photos, videos, and files
- building one chronological timeline across text and media messages
- storing a stable local archive cache
- preserving metadata useful for future search and analysis

## Current behavior

Current import flow:

1. The renderer calls `window.archivesAPI.add()` for native picker import or `window.archivesAPI.addByPath(path)` for drag/drop import.
2. `src/electron/main.js` receives the folder path.
3. `indexArchive(folder)` scans only direct child files ending in `.txt`.
4. Each `.txt` file is parsed by spawning `python3 src/parser/main.py input.txt output.json`.
5. Parsed messages are merged, sorted by timestamp, and written to:

```text
<userData>/archives/<archiveId>/index.json
```

6. The renderer reads that JSON cache and renders text bubbles.

Known current limitations:

- Drag/drop relies on renderer-side `file.path`, which is unreliable in modern Electron.
- `preload.js` exposes broad filesystem reading and references `getUserDataPath`, but the main process does not define the matching sync IPC handler.
- `archives:addByPath` checks `archiveID` while archives are stored as `archiveId`.
- Folder scanning is shallow and only sees direct child `.txt` files.
- Media files are not indexed or linked to `Photo`, `Video`, or file messages.
- Parser output has no stable message ID or explicit message type.
- Multiline messages may be dropped or split incorrectly.
- Current cache is a single message array, which limits future archive metadata, media indexes, and schema versioning.

## Proposed approach

Implement the archive pipeline in layers, starting with the data model and importer contract before changing UI behavior.

### 1. Define cache layout

Use a versioned archive cache directory under Electron `userData`:

```text
<userData>/archives/<archiveId>/
  manifest.json
  messages.json
  media.json
```

Initial responsibilities:

- `manifest.json`: archive-level metadata and schema version
- `messages.json`: normalized chronological message timeline
- `media.json`: discovered media/file assets and matching status

Keep JSON for the first implementation because the current app already reads and writes JSON. Revisit SQLite after import, media rendering, search, and performance needs are clearer.

### 2. Normalize message records

Create a stable message model:

```json
{
  "id": "msg_...",
  "archiveId": "...",
  "sourceTextFile": "Talk_....txt",
  "sourceLineStart": 123,
  "sourceLineEnd": 123,
  "timestamp": "YYYY-MM-DD HH:mm",
  "ts": 1717985040000,
  "sender": "name",
  "type": "text",
  "content": "message text",
  "mediaId": null,
  "metadata": {
    "date": "YYYY-MM-DD",
    "year": 2024,
    "month": 6,
    "day": 9,
    "hour": 22,
    "weekday": 0,
    "timeOfDay": "night",
    "season": "summer",
    "textLength": 12,
    "hasUrl": false,
    "urls": []
  }
}
```

Allowed initial message types:

- `text`
- `photo`
- `video`
- `file`
- `link`
- `system`
- `unknown_media`

For backwards compatibility, renderer code should temporarily tolerate both `content` and older `text` fields while new parser output standardizes on `content`.

### 3. Discover archive inputs

Create one main-process archive discovery function that accepts a selected path and returns:

```json
{
  "rootPath": "/selected/folder",
  "textFiles": [],
  "mediaFiles": [],
  "otherFiles": [],
  "warnings": []
}
```

Discovery rules:

- Recursively scan the selected folder with a bounded depth or clear exclusion rules.
- Identify KakaoTalk `.txt` exports by extension and, later, lightweight header sniffing.
- Identify media by extension:
  - images: `.jpg`, `.jpeg`, `.png`, `.gif`, `.heic`, `.webp`
  - videos: `.mp4`, `.mov`, `.avi`, `.m4v`
  - files: anything else relevant but not app/system metadata
- Ignore `.DS_Store`, hidden system files, and generated cache files.
- If a parent folder is selected, find likely nested KakaoTalk export folders instead of requiring an exact folder selection.

### 4. Build media index

Create media records:

```json
{
  "id": "media_...",
  "archiveId": "...",
  "kind": "photo",
  "absolutePath": "/source/export/photo.jpg",
  "relativePath": "KakaoTalk_Photo_...jpg",
  "filename": "KakaoTalk_Photo_...jpg",
  "extension": ".jpg",
  "sizeBytes": 123456,
  "createdAt": null,
  "modifiedAt": "2025-07-10T00:37:00.000Z",
  "matchedMessageId": null,
  "matchStatus": "unmatched"
}
```

Initial media matching should be conservative:

- Detect placeholder messages such as `Photo`, `Video`, and file-like labels.
- Match by chronological order within media kind when no reliable filename exists in the chat text.
- Mark unmatched or ambiguous assets rather than guessing silently.
- Preserve all discovered media in `media.json` even if not matched.

### 5. Unify chronological timeline

Messages should be sorted by:

1. timestamp milliseconds (`ts`)
2. source text file order
3. source line number
4. original parse order

Media-linked messages should stay in the message timeline with `type` and `mediaId` instead of being rendered separately. This allows search, date navigation, and analysis to use one chronological sequence.

### 6. Fix import entry points

Use one backend path for native picker and drag/drop:

- Renderer asks preload for safe dropped-file paths.
- Preload exposes narrow helpers, not broad filesystem access.
- Main process owns validation, scanning, indexing, and cache writes.
- Both native picker and drag/drop call the same `archives:importPath` style IPC.

### 7. Prepare renderer for future UI work

Renderer should consume `messages.json` and eventually `media.json` through narrow preload APIs. Initial UI can still render text bubbles, but the data model should make these later features natural:

- media thumbnails/previews
- search by text, sender, type, and URL
- jump to date
- previous/next search result
- timeline/date index
- media gallery

## Why this approach

This approach fits the current codebase because it builds on the existing Electron + Python + JSON architecture without forcing a large storage migration immediately.

Reasons:

- The current app already uses Electron main-process filesystem access and Python parsing.
- JSON cache files are easy to inspect while the model is still evolving.
- Separating `manifest.json`, `messages.json`, and `media.json` gives the app room to add media and metadata without overloading a single `index.json` array.
- A versioned schema allows future migration to SQLite or richer indexes without losing old archives.
- Keeping import and filesystem work in the main process respects Electron security boundaries better than renderer-side path/file handling.
- A unified message timeline supports the KakaoTalk-like reading experience and future analysis features.

## Alternatives considered

### Keep the existing single `index.json` array

Not chosen because media indexing, archive metadata, warnings, schema versioning, and analysis indexes would all become awkward inside one flat array.

### Move directly to SQLite

Not chosen for the first pass. SQLite may be valuable for large archives, search, and analysis, but using it now would add migration and query design work before the data model is proven.

### Store only references to original files without building a media index

Not chosen because the app needs to render and search media messages chronologically. A media index also makes unmatched or ambiguous media visible instead of hidden.

### Copy all media into Electron `userData` during import

Not chosen for the first pass because chat exports can be several gigabytes. Referencing source files is safer for disk space. A later explicit archive/copy mode can be designed separately.

### Implement UI search and date navigation first

Not chosen because those features depend on stable timestamps, message IDs, message types, and metadata. Building them before the archive model would likely cause rework.

## Decision log

- Date: 2026-05-27
- Decision: Start with a versioned JSON archive cache using `manifest.json`, `messages.json`, and `media.json`.
- Reason: This keeps the implementation close to the current app while supporting media, metadata, and future schema changes.
- Tradeoff: JSON may not be fast enough for very large search and analysis workloads later; SQLite may be needed after the model stabilizes.

- Date: 2026-05-27
- Decision: Keep one unified chronological message timeline and link media through `mediaId`.
- Reason: Reading, searching, date navigation, and analysis all need one ordered conversation stream.
- Tradeoff: Media matching must be careful and may produce unmatched or ambiguous states.

- Date: 2026-05-27
- Decision: Keep filesystem scanning and archive indexing in Electron main process APIs.
- Reason: The main process is the right place for privileged filesystem work; renderer code should stay narrow and UI-focused.
- Tradeoff: More IPC design is needed before drag/drop and picker flows share one clean import path.

## Implementation steps

- [ ] Add sample fixtures or small synthetic KakaoTalk exports for parser/import tests without private data.
- [ ] Define archive schema constants and schema version in the Electron main process or a shared module.
- [ ] Implement archive discovery for selected folders, including recursive text/media detection and warnings.
- [ ] Update parser output to include stable IDs, source file, source line numbers, `type`, `ts`, and richer metadata.
- [ ] Add conservative media placeholder detection and initial media index creation.
- [ ] Write `manifest.json`, `messages.json`, and `media.json` into the archive cache directory.
- [ ] Replace separate picker/drop flows with one main-process import IPC path.
- [ ] Update preload APIs to expose narrow import/cache helpers and avoid broad renderer filesystem access.
- [ ] Update renderer loading to read the new cache layout while preserving temporary compatibility with old `index.json`.
- [ ] Add manual verification steps and lightweight automated parser/import checks.

## Verification plan

We will know this worked when:

- Native folder picker can import a folder containing at least one KakaoTalk `.txt` export.
- Drag/drop import calls the same backend path as the native picker.
- The cache directory contains valid `manifest.json`, `messages.json`, and `media.json`.
- `messages.json` is chronologically sorted and includes text and media-placeholder messages with stable IDs.
- `media.json` lists discovered photo/video/file assets and marks match status.
- Renderer can still display text messages from the new cache format.
- Private chat contents are not logged during import.
- Ignored private data remains untracked by Git.

Suggested commands/checks:

```bash
python3 src/parser/main.py <sample.txt> /private/tmp/parser-output.json
npm start
```

Manual checks:

- import through native picker
- import through drag/drop
- inspect generated cache files under Electron `userData`
- open latest archive after app restart
- test with a small synthetic export before testing private real exports

## Open questions

- Should the app reference original media file paths, copy media into archive storage, or support both modes?
- How reliably do KakaoTalk exports order media files relative to `Photo` and `Video` placeholders?
- Which file extensions should count as user-shared files versus system/export artifacts?
- Should analysis metadata be generated during import or lazily when the analysis view is opened?
- What is the maximum expected archive size, and when should SQLite replace JSON?
- Should duplicate imports update an existing archive, create a new archive version, or ask the user?
- How should the app handle multiple `.txt` exports from the same chat room with overlapping date ranges?
