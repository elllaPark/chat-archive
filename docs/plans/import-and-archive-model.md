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

## Observed export formats

This plan is based on five real KakaoTalk app exports inspected read-only. The project will only target app export options 1 and 2:

1. **Send text only**
2. **Save as documents**: messages plus photos/videos/files compressed and saved as KakaoTalk Documents

Talk Cloud Drive exports are out of scope.

### Korean setting, option 1: Send text only

Observed folder shape:

```text
Kakaotalk_Chat_[chat-name]/
  Talk_2026.5.25 15:10-1.txt
  Talk_2026.5.25 15:10-2.txt
  Talk_2026.5.25 15:10-3.txt
  Talk_2026.5.25 15:10-4.txt
```

Observed properties:

- flat folder
- only `.txt` files
- multiple `Talk_...-N.txt` chunks
- Korean-setting header:

```text
Talk_2026.5.25 15:10-1.txt
저장한 날짜 : 2026-05-27 오후 4:13
```

- Korean date divider:

```text
2019년 3월 30일 토요일
```

- Korean message line:

```text
2019-03-30 오전 4:39, sender : content
```

Important finding: text-only exports still contain media placeholders such as `사진`, `동영상`, `음성메시지`, and `이모티콘`, but no attached media files.

### Korean setting, option 2: Save as documents

Observed folder shape:

```text
Kakaotalk_Chat_[chat-name]_20260526_152352/
  Talk_2026.5.25 16:20-1.txt
  Talk_2026.5.25 16:20-2.txt
  20231030_123053.txt
  20251225_230420_8657.jpeg
  20211003_100750_923.mp4
  20231114_042004.docx
  ...
```

Observed properties:

- flat folder
- large media-heavy export
- chat history files are `Talk_...-N.txt`
- extra non-chat `.txt` files can exist as user-shared attachments
- media filenames generally encode timestamps:

```text
YYYYMMDD_HHMMSS_number.ext
YYYYMMDD_HHMMSS.ext
```

- observed extensions include `.jpeg`, `.jpg`, `.png`, `.webp`, `.gif`, `.mp4`, `.m4a`, `.pdf`, `.docx`, `.vcf`, and `.txt`
- chat messages include placeholders such as `사진`, `동영상`, `음성메시지`, and `이모티콘`
- some file attachments appear in chat text with a `파일:` prefix

Important finding: placeholder counts and file counts do not perfectly match. The importer must not assume every placeholder maps to exactly one media file.

### English setting, older option 1: Send text only

Observed folder shape:

```text
KakaoTalk Chats with [chat-name]/
  Talk_2021.3.30 22:16-1.txt
  Talk_2021.3.30 22:16-2.txt
```

Observed properties:

- flat folder
- only `.txt` files
- older English-setting header:

```text
Talk_2021.3.30 22:16-1.txt
Date Saved : Apr 23, 2021 14:55
```

- English date divider:

```text
Thursday, July 16, 2020
```

- English message line:

```text
Jul 16, 2020 22:23, sender : content
```

Important finding: older English text-only exports also contain media placeholders, observed as `Photo` and `Video`, but no attached media files.

### Korean setting, group chat option 1 and option 2

Observed group chat examples came from the same group chat, exported once as text-only and once as documents.

Text-only file:

```text
Talk_2026.5.26 17:39-1.txt
```

Documents folder:

```text
Kakaotalk_Chat_[group-chat-name]_20260527_170010/
  Talk_2026.5.26 17:39-1.txt
  20251006_035714_238.jpg
  20250128_095329_157.mp4
  ...
```

Observed properties:

- same Korean-setting header/date/message format as 1:1 Korean exports
- one chat `.txt` file in both export modes
- same parsed message-level stats across text-only and documents exports
- multiple senders were present, so the archive model must not assume exactly two participants
- text-only group export contains media placeholders but no media files
- documents group export contains the same chat `.txt` plus media files
- documents media filenames follow the same timestamp pattern as 1:1 documents exports

Important finding: for the same group chat, text-only and documents exports can produce equivalent chat timelines but different folder contents. The importer should treat export mode as a source packaging difference, not as a different conversation model.

Another important finding: documents exports can contain more media files than chat placeholders. In the inspected group documents export, media files outnumbered photo/video placeholders. Extra media files should be indexed as `unmatched`, not treated as import errors.

### Import implications

- Export detection should record `exportLocale`: `ko`, `en`, or `unknown`.
- Export detection should record `exportMode`: `text_only`, `documents`, or `unknown`.
- Export detection should record `exportFormatVersion`: `current`, `legacy`, or `unknown`.
- Export detection should record `chatKind`: `direct`, `group`, or `unknown`.
- Chat `.txt` files should be identified by filename pattern plus header sniffing, not extension alone.
- Non-chat `.txt` files in documents exports should be treated as file attachments, not parsed as chat history.
- Text-only placeholder messages should remain in the timeline with no linked media.
- Documents exports should produce a media/file index, but matching must preserve `unmatched` and `ambiguous` states.
- Message parsing and rendering must support any number of senders.

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

`manifest.json` should include at least:

```json
{
  "schemaVersion": 1,
  "archiveId": "...",
  "sourceRootPath": "/selected/folder",
  "label": "Kakaotalk_Chat_...",
  "exportLocale": "ko",
  "exportMode": "documents",
  "exportFormatVersion": "current",
  "chatKind": "group",
  "importedAt": "2026-05-27T00:00:00.000Z",
  "participants": [],
  "chatTextFiles": [],
  "attachmentFiles": [],
  "warnings": []
}
```

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
  "senderId": "participant_...",
  "type": "text",
  "content": "message text",
  "mediaId": null,
  "mediaMatchStatus": null,
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
- `voice`
- `emoticon`
- `file`
- `link`
- `system`
- `unknown_media`

Placeholder mapping should support both Korean and English export text:

```text
사진 -> photo
동영상 -> video
음성메시지 -> voice
이모티콘 -> emoticon
Photo -> photo
Video -> video
```

For text-only exports, placeholder messages should remain in `messages.json` with `mediaId: null` and `mediaMatchStatus: "unavailable_in_text_only_export"`.

Sender handling must support both 1:1 and group chats. The importer should build a participant list from observed sender names, assign stable participant IDs inside the archive, and avoid hardcoded assumptions such as `mine` versus one other person at the data-model layer.

For backwards compatibility, renderer code should temporarily tolerate both `content` and older `text` fields while new parser output standardizes on `content`.

### 3. Discover archive inputs

Create one main-process archive discovery function that accepts a selected path and returns:

```json
{
  "rootPath": "/selected/folder",
  "textFiles": [],
  "chatTextFiles": [],
  "mediaFiles": [],
  "attachmentFiles": [],
  "otherFiles": [],
  "detectedExportLocale": "ko",
  "detectedExportMode": "documents",
  "detectedExportFormatVersion": "current",
  "detectedChatKind": "group",
  "warnings": []
}
```

Discovery rules:

- Recursively scan the selected folder with a bounded depth or clear exclusion rules.
- Prefer flat-folder handling because observed app exports are flat folders.
- Identify KakaoTalk chat `.txt` exports by filename pattern and lightweight header sniffing.
- Treat `Talk_...-N.txt` files with KakaoTalk headers as chat history files.
- Treat other `.txt` files in documents exports as attachment files unless header sniffing proves they are chat history.
- Detect Korean current exports from `저장한 날짜`, Korean date dividers, and `오전`/`오후` message lines.
- Detect older English exports from `Date Saved`, English date dividers, and month-name message lines.
- Infer `chatKind` conservatively from participant count and folder/title hints. More than two observed senders should be treated as `group`; otherwise use `direct` or `unknown`.
- Identify media by extension:
  - images: `.jpg`, `.jpeg`, `.png`, `.gif`, `.heic`, `.webp`
  - videos: `.mp4`, `.mov`, `.avi`, `.m4v`
  - audio/voice: `.m4a`, `.mp3`, `.wav`, `.aac`
  - files: `.pdf`, `.docx`, `.vcf`, `.txt`, and other relevant user-shared files
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

- Detect placeholder messages such as `사진`, `동영상`, `음성메시지`, `Photo`, `Video`, and file-like labels.
- Detect file attachment messages with the Korean `파일:` prefix where possible.
- Parse timestamps from observed documents-export filenames:

```text
YYYYMMDD_HHMMSS_number.ext
YYYYMMDD_HHMMSS.ext
```

- Match by chronological order within media kind when no reliable filename exists in the chat text.
- Mark unmatched or ambiguous assets rather than guessing silently.
- Preserve all discovered media in `media.json` even if not matched.
- Allow text-only placeholder messages to have no media record.
- Treat extra media files in documents exports as expected. They should remain in `media.json` with `matchStatus: "unmatched"` unless confidently linked.

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
- The observed exports confirm that both text-only and documents modes still need placeholder message types in the timeline.
- Group chat examples confirm that the same conversation can be exported in both supported modes with equivalent chat text but different media availability.

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

- Date: 2026-05-27
- Decision: Detect export locale/mode/version during discovery and store it in `manifest.json`.
- Reason: Current Korean exports and older English exports use different headers, date dividers, message formats, and placeholder words.
- Tradeoff: Import code needs a detection layer before parsing, but parser behavior becomes more explicit and testable.

- Date: 2026-05-27
- Decision: Treat non-`Talk_...` `.txt` files in documents exports as attachments by default.
- Reason: Observed documents exports can include user-shared `.txt` files alongside chat history `.txt` chunks.
- Tradeoff: Header sniffing must be reliable enough to avoid skipping valid chat files with unusual names.

- Date: 2026-05-27
- Decision: Add `chatKind` and participant metadata to the archive model.
- Reason: Group chat exports use the same line format as direct chats but can include more than two senders.
- Tradeoff: Renderer code will need to stop assuming a fixed two-person conversation when richer group UI is added.

- Date: 2026-05-27
- Decision: Treat unmatched media files in documents exports as expected archive assets.
- Reason: The inspected group documents export contained more media files than photo/video placeholders even though it represented the same chat as the text-only export.
- Tradeoff: The media index may include assets that do not render inline until matching rules improve.

## Implementation steps

- [ ] Add sample fixtures or small synthetic KakaoTalk exports for parser/import tests without private data.
- [ ] Define archive schema constants and schema version in the Electron main process or a shared module.
- [ ] Implement export discovery for selected folders, including locale/mode/version detection and warnings.
- [ ] Add header sniffing to separate chat history `.txt` files from `.txt` attachments.
- [ ] Update parser output to include stable IDs, source file, source line numbers, locale, `type`, `ts`, and richer metadata.
- [ ] Add placeholder detection for Korean and English exports.
- [ ] Build participant metadata from observed senders and infer `chatKind`.
- [ ] Add conservative media/file index creation, including timestamp parsing from documents-export filenames.
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
- Korean current text-only exports keep placeholder messages with `mediaMatchStatus: "unavailable_in_text_only_export"`.
- Korean current documents exports identify media/file assets and do not parse non-chat `.txt` attachments as chat history.
- Older English text-only exports parse month-name message lines and English placeholders.
- Group chat exports import without assuming only two senders.
- Same-chat text-only and documents exports produce equivalent message timelines where the chat text overlaps.
- Extra documents-export media files are preserved as unmatched media rather than treated as failures.
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
- Should `이모티콘` remain a distinct `emoticon` message type even when no asset file is exported?
- Should voice messages be modeled as `voice` or as an audio subtype of `file`?
- How should participant display names be normalized if a sender changes profile name over time?
- Should same-chat text-only and documents exports update one archive, create variants, or be treated as separate imports?
- Should analysis metadata be generated during import or lazily when the analysis view is opened?
- What is the maximum expected archive size, and when should SQLite replace JSON?
- Should duplicate imports update an existing archive, create a new archive version, or ask the user?
- How should the app handle multiple `.txt` exports from the same chat room with overlapping date ranges?
