# Media Import And Indexing Plan

## Problem

The app needs to preserve and browse non-text KakaoTalk export data such as photos, videos, audio, contacts, documents, and other shared files.

KakaoTalk documents exports place chat transcript files and attachment files in the same folder. The parser can now identify message placeholders such as `사진`, `Photo`, `사진 5장`, `5 photos`, `동영상`, `Video`, `음성메시지`, and `이모티콘`, but it does not match those messages to files. The next design step is to define how media and file assets should be indexed, stored, linked to messages, and preserved without making the app heavy or risking data loss.

## Current behavior

Current app behavior:

- imports selected folders by scanning direct child `.txt` files
- runs the Python parser on transcript files
- writes parsed message JSON under Electron `userData`
- renders text messages in the chat UI

Current parser behavior:

- parses one transcript file at a time
- classifies some media placeholders as `photo`, `video`, `voice`, or `emoticon`
- records `attachmentCount` for multi-photo placeholders such as `사진 5장` and `5 photos`
- does not inspect or match actual media files

Current limitations:

- no media/file index exists yet
- media files are not copied into a stable archive folder
- placeholder messages are not linked to actual files
- extra attachment files are not represented
- non-chat `.txt` attachments can be confused with transcript files unless discovery sniffs headers
- large exports need a lightweight storage strategy before full import is implemented

## Proposed approach

Use a local archive model where original files remain ordinary files on disk, while SQLite stores searchable indexes and metadata.

High-level shape:

```text
archive/
  archive.sqlite
  originals/
    Talk_2026.5.25 15:10-1.txt
    20260525_221503_123.jpg
    20260525_221503_124.mp4
    ...
  thumbnails/
    media_000001.jpg
  previews/
    media_000002.jpg
```

Guiding principle:

```text
Original files = source of truth
SQLite = searchable index and relationships
Thumbnails/previews = disposable cache
```

This keeps the app standalone and local. It does not require a server or a database service.

## Why this approach

KakaoTalk archives can be large; one inspected documents export was around 20GB. Storing videos and photos directly inside JSON or SQLite would make the app heavy and make recovery harder. Keeping originals as files preserves the export faithfully and allows normal filesystem backup.

SQLite is still useful because messages, timestamps, sender names, media metadata, match status, and future analysis fields need fast lookup. SQLite gives local querying without PostgreSQL or any server process.

## Archive layout

Each imported chat archive should have one stable local directory:

```text
<app-archive-root>/<archiveId>/
  manifest.json
  archive.sqlite
  originals/
  thumbnails/
  previews/
```

### `manifest.json`

Stores archive-level metadata that is useful even before opening SQLite:

```json
{
  "schemaVersion": 1,
  "archiveId": "archive_...",
  "createdAt": "2026-06-01T00:00:00Z",
  "sourceRootPath": "/original/export/folder",
  "sourceCopied": true,
  "exportMode": "documents",
  "formatIds": [
    "kakaotalk_mobile_txt_ko_v1"
  ],
  "warnings": []
}
```

### `originals/`

Stores copied source files unchanged.

Rules:

- preserve original file bytes
- preserve original filename when possible
- do not overwrite existing files
- if duplicate filenames exist, add a stable disambiguating suffix and store the original relative path in SQLite
- calculate file size and checksum after copy

## SQLite role

SQLite should store indexes and relationships, not large media blobs.

Initial tables can be simple:

```text
archives
messages
assets
message_assets
import_warnings
```

### `messages`

Stores normalized parser messages:

```text
id
archive_id
local_id
timestamp
sender
type
content
source_file_id
source_line_start
source_line_end
source_record_number
metadata_json
```

### `assets`

Stores one row per discovered attachment file:

```text
id
archive_id
original_filename
original_relative_path
stored_relative_path
asset_type
mime_type
extension
size_bytes
sha256
filename_timestamp
width
height
duration_ms
match_status
metadata_json
```

Asset types:

```text
photo
video
audio
document
contact
text_file
other
unknown
```

### `message_assets`

Links messages to assets:

```text
message_id
asset_id
position
confidence
match_reason
```

`position` matters for multi-photo messages, where `사진 5장` should link to five ordered photo assets.

## Asset discovery

Discovery should scan the selected export folder before parsing/matching.

Rules:

- Identify transcript files by filename plus parser detector/header sniffing.
- Treat non-transcript `.txt` files as attachments.
- Treat known image/video/audio/document/contact extensions as assets.
- Preserve unknown extensions as `asset_type: "unknown"` rather than dropping them.

Initial extension mapping:

```text
photo: .jpg, .jpeg, .png, .gif, .webp, .heic
video: .mp4, .mov, .m4v
audio: .m4a, .mp3, .wav, .aac
document: .pdf, .doc, .docx, .xls, .xlsx, .ppt, .pptx, .hwp
contact: .vcf
text_file: .txt, .csv
```

## Filename metadata

KakaoTalk document export files often encode timestamps in filenames:

```text
YYYYMMDD_HHMMSS_number.ext
YYYYMMDD_HHMMSS.ext
```

Extract:

```json
{
  "filenameTimestamp": "2026-05-25T22:15:03",
  "filenameSequence": 123,
  "timestampSource": "filename"
}
```

This timestamp is a matching clue, not proof. Some files may be extra, duplicated, edited, forwarded, or not represented by placeholders exactly.

## Matching strategy

Match parser messages to indexed assets after both timelines exist.

Initial matching flow:

1. Sort messages by timestamp and source order.
2. Sort assets by filename timestamp, extension type, filename sequence, and filename.
3. For each placeholder message, infer expected asset type and count:
   - `사진` / `Photo`: one photo
   - `사진 N장` / `N photos`: N photos
   - `동영상` / `Video`: one video
   - `음성메시지`: one audio file
   - `파일:` or future file markers: one document/other file
4. Find candidate assets with matching type near the message timestamp.
5. Prefer assets not already matched.
6. If exactly enough candidates are found with high confidence, link them.
7. If too few, too many, or unclear candidates are found, mark the message/assets as ambiguous instead of guessing silently.

Suggested initial timestamp window:

```text
same second first
then +/- 2 minutes
then same day fallback only if needed
```

The exact windows should be tested against real exports before locking them in.

## Match statuses

Use explicit statuses so import never hides uncertainty:

```text
matched
unmatched
ambiguous
missing_file
unavailable_in_text_only_export
unsupported_type
not_applicable
```

Message-level examples:

```json
{
  "type": "photo",
  "content": "사진 5장",
  "metadata": {
    "attachmentCount": 5
  },
  "mediaMatchStatus": "matched",
  "mediaIds": [
    "media_000001",
    "media_000002",
    "media_000003",
    "media_000004",
    "media_000005"
  ]
}
```

For text-only exports:

```json
{
  "type": "photo",
  "content": "사진",
  "mediaMatchStatus": "unavailable_in_text_only_export",
  "mediaIds": []
}
```

## Thumbnail and preview policy

Thumbnails should be generated only after originals are safely copied and indexed.

Rules:

- thumbnails are cache, not source of truth
- missing thumbnails can be regenerated
- generate image thumbnails for photos
- generate poster-frame previews for videos later
- do not block import if thumbnail generation fails
- record thumbnail generation warnings separately

## Privacy and safety

Non-text data can be more sensitive than messages.

Rules:

- do not upload files
- do not commit originals, generated archives, thumbnails, previews, or SQLite databases
- do not log private filenames unless needed for debugging
- prefer counts and metadata in summaries
- never delete source exports after import unless the user explicitly asks
- verify copied file size and checksum before marking copy as complete

## Alternatives considered

1. Store media binaries inside SQLite

   Not chosen because large videos/photos would bloat the database and make backup, recovery, and inspection harder.

2. Store only paths to the original export folder

   Not chosen because the archive would break if the user moves or deletes the original export. The app exists to preserve data separately from the original chat app/export location.

3. Rename every media file to internal IDs only

   Not chosen because original filenames carry timestamp clues and provenance. Internal IDs can exist in SQLite while stored files preserve original names where possible.

4. Match every placeholder aggressively

   Not chosen because inspected exports showed placeholder counts and file counts do not always match. Ambiguous and unmatched states are safer than confident but wrong links.

## Decision log

- Date: 2026-08-25
- Decision: Implement the first milestone directly in SQLite with `messages`, `assets`, and `imports`, without copying files or matching assets to messages.
- Reason: The normalized parser output is already relational, Python includes SQLite support, and this establishes the archive boundary without changing parser behavior.
- Tradeoff: Asset rows reference source-relative paths for now; the archive is not yet portable until the later originals-copy milestone.

- Date: 2026-08-25
- Decision: Keep original media/files as normal files and store indexes/relationships in SQLite.
- Reason: This preserves original data while keeping the app local, lightweight, and searchable.
- Tradeoff: Backup must include both `archive.sqlite` and `originals/`, not just one database file.

- Date: 2026-08-25
- Decision: Treat ambiguous media matching as expected, not as an import failure.
- Reason: Real KakaoTalk documents exports can contain unmatched or extra files.
- Tradeoff: UI will need to communicate uncertainty or provide a later repair/relink flow.

- Date: 2026-08-25
- Decision: Generate thumbnails/previews as disposable cache.
- Reason: Originals must remain the source of truth, while thumbnails make browsing faster.
- Tradeoff: Import and browse flows need cache invalidation or regeneration logic later.

## Implementation steps

- [x] Define the initial archive SQLite schema (`schema_version = 1`).
- [x] Add deterministic asset discovery for a supplied media/files folder.
- [ ] Add transcript-vs-attachment detection for `.txt`/`.csv` files.
- [ ] Copy original files into `originals/` without modifying bytes.
- [ ] Store file size, extension, MIME guess, checksum, original path, and stored path.
- [x] Extract filename timestamps and sequence numbers from KakaoTalk media filenames.
- [x] Import normalized parser messages into SQLite.
- [ ] Add message-to-asset matching for single photo/video/audio placeholders.
- [ ] Add multi-photo matching using parser `attachmentCount`.
- [ ] Preserve unmatched and ambiguous files/messages.
- [ ] Generate photo thumbnails as non-blocking cache.
- [x] Add an importer test with small sanitized fake assets.
- [ ] Update renderer to load media links from the archive index.

## Verification plan

We will know this works when:

- documents exports import without modifying the source folder
- transcript files are parsed and non-transcript `.txt` files are indexed as assets
- copied originals match source file sizes and checksums
- media assets are indexed with stable IDs
- text-only exports keep placeholder messages with `unavailable_in_text_only_export`
- documents exports link obvious single photo/video placeholders to assets
- multi-photo placeholders link to the expected number of assets or become `ambiguous`
- extra media files are preserved as `unmatched`
- import warnings are visible in metadata
- deleting thumbnails does not lose original data

## Open questions

- How should schema migrations be represented after the initial `schema_version = 1` database?
- What timestamp matching window is accurate for large real KakaoTalk documents exports?
- Should originals be copied into archive storage by default, or should the user choose between copy and reference-only modes?
- How should the UI show ambiguous or unmatched media messages?
- Which media metadata should be extracted first: dimensions, duration, EXIF dates, or only basic file metadata?
- Should checksum calculation happen during import or as a background verification task for very large files?
