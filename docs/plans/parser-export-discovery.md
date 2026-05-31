# Parser Export Discovery Plan

## Problem

The parser needs to support multiple KakaoTalk export formats without mixing parser responsibilities with folder import, media indexing, archive IDs, or UI behavior.

The current parser handles a narrow subset of KakaoTalk mobile `.txt` exports and writes a simple JSON list. Real exports vary by language setting, device, export mode, and file extension:

- mobile KakaoTalk exports chat text as `.txt`
- PC KakaoTalk exports chat text as `.csv`
- Korean and English settings use different timestamp and date divider conventions
- documents exports include media/files next to chat transcripts
- text-only exports still include media placeholders but no actual media files

The goal is to define how parser discovery should work before changing parser code.

## Current behavior

Current parser flow:

1. Electron calls `python3 src/parser/main.py <input.txt> <output.json>`.
2. `src/parser/main.py` creates a `ChatParser`.
3. `ChatParser.parse_chat_file(path)` reads one text file.
4. Parsed messages are exported through `exportJson(output_path)`.

Current parser output is a list of messages shaped like:

```json
{
  "timestamp": "YYYY-MM-DD HH:mm",
  "sender": "name",
  "content": "message text",
  "time_of_day": "morning",
  "season": "spring"
}
```

Known limitations:

- only one parser class exists
- format detection is implicit and regex-based inside the parser
- PC `.csv` exports currently produce zero parsed messages
- multiline messages are not modeled explicitly
- system/deleted-message rows are not modeled
- source file and source line/record numbers are not preserved
- message IDs are not stable
- parser output does not include message type, URLs, parse warnings, or raw format metadata
- parser code does not have automated tests

## Observed export formats

All observations below came from read-only inspection of user-provided sample exports. Private chat names and nicknames should not be committed into documentation or fixtures.

### Korean mobile `.txt`, current format

Observed in both 1:1 and group chat exports.

Header pattern:

```text
저장한 날짜 : 2026-05-27 오후 4:13
```

Date divider pattern:

```text
2019년 3월 30일 토요일
```

Message line pattern:

```text
2019-03-30 오전 4:39, sender : content
```

Important behavior:

- uses `오전` / `오후`
- uses Korean weekday names in date dividers
- supports multiple chunked `Talk_...-N.txt` files
- group chat format appears structurally the same as 1:1 chat format
- text-only exports still contain placeholders such as `사진`, `동영상`, `음성메시지`, and `이모티콘`

### English mobile `.txt`, observed format

Observed in a text-only export from a few years ago. Its currentness is unknown until a recent English-setting export is inspected, so this plan should avoid calling it legacy or outdated.

Header pattern:

```text
Date Saved : Apr 23, 2021 14:55
```

Date divider pattern:

```text
Thursday, July 16, 2020
```

Message line pattern:

```text
Jul 16, 2020 22:23, sender : content
```

Important behavior:

- uses English month and weekday names
- observed timestamps use 24-hour time
- text-only exports can contain placeholders such as `Photo` and `Video`
- because only one English-setting sample has been inspected, treat it as an observed English mobile format rather than assuming it represents every current English export

### PC `.csv`

Observed in a PC KakaoTalk export.

File properties:

- UTF-8 with BOM
- comma-delimited CSV
- header row: `Date,User,Message`
- timestamp format: `YYYY-MM-DD HH:mm:ss`
- records can contain quoted multiline message bodies
- the CSV parser should handle quoted newlines rather than parsing physical lines manually

Normal row shape:

```csv
Date,User,Message
2026-05-27 22:52:01,sender,message text
```

System/deleted-message row shape:

```csv
,,The message has been deleted.
```

Important behavior:

- rows with empty `Date` and `User` but message text can be system messages
- observed empty `Date`/`User` rows with `The message has been deleted.` are confirmed system messages, not multiline continuation rows
- multiline user messages should be handled by the CSV reader when quotes are balanced
- PC export placeholders were observed as `Photo` and `Video`

## Proposed approach

Build parser support as a detector plus multiple small parser implementations.

### Parser boundary

The parser should only parse one transcript file at a time.

The parser should be responsible for:

- detecting or receiving a specific transcript format
- parsing text or CSV records into normalized message objects
- assigning file-local message IDs
- preserving source file and source line or CSV record references
- identifying basic message type from text placeholders
- identifying system/deleted-message rows
- returning parse warnings for ambiguous or unsupported records

The parser should not be responsible for:

- selecting folders
- deciding whether an export is text-only or documents mode
- deciding whether a chat is 1:1 or group chat
- assigning archive IDs
- matching media files to placeholders
- copying files into an archive cache
- rendering UI

Those responsibilities belong to higher-level archive discovery/import code.

### Format detection

Add a detector that reads the file extension and the first meaningful lines or records.

Detector inputs:

- file extension
- BOM/encoding clues
- header row or saved-date line
- date divider pattern
- message line pattern
- timestamp language markers such as `오전`, `오후`, English months, and Korean weekdays
- CSV delimiter and field names

Detector output should include:

```json
{
  "formatId": "kakao.ko.mobile_txt.current",
  "confidence": 0.98,
  "reasons": [],
  "warnings": []
}
```

Initial format IDs:

- `kakao.ko.mobile_txt.current`
- `kakao.en.mobile_txt.observed`
- `kakao.pc_csv.current`
- `unknown`

### Parser modules

Use separate parser modules for separate export formats:

```text
src/parser/
  detector.py
  models.py
  parsers/
    base.py
    kakao_ko_mobile_txt.py
    kakao_en_mobile_txt.py
    kakao_pc_csv.py
  chatParse.py
  main.py
```

Keep `chatParse.py` temporarily as a compatibility facade so Electron can keep calling the existing CLI while parser internals are reorganized.

### Normalized parser result

Each parser should return a structured parse result:

```json
{
  "sourceFile": "Talk_....txt",
  "formatId": "kakao.ko.mobile_txt.current",
  "messages": [],
  "participants": [],
  "warnings": [],
  "stats": {
    "messageCount": 0,
    "systemMessageCount": 0,
    "parseWarningCount": 0
  }
}
```

Each message should include at least:

```json
{
  "localId": "msg_000001",
  "sourceFile": "Talk_....txt",
  "sourceLineStart": 123,
  "sourceLineEnd": 123,
  "sourceRecordNumber": null,
  "timestamp": "2026-05-27 22:52:01",
  "sender": "sender name",
  "type": "text",
  "content": "message text",
  "metadata": {
    "hasUrl": false,
    "urls": []
  },
  "warnings": []
}
```

For PC CSV system rows without timestamp or sender:

```json
{
  "localId": "msg_000455",
  "sourceRecordNumber": 455,
  "timestamp": "2023-04-10 23:57:34",
  "sender": null,
  "type": "system",
  "content": "The message has been deleted.",
  "metadata": {
    "systemSubtype": "deleted_message",
    "previousMessageLocalId": "msg_000454",
    "timestampInheritedFromPreviousMessage": true
  },
  "warnings": [
    "missing_sender"
  ]
}
```

PC CSV system rows without their own timestamp should inherit the previous valid message timestamp. Preserve the source record number and `timestampInheritedFromPreviousMessage` flag so the importer and UI can keep the row in the right relative order and still know the timestamp was inferred.

### Message type detection

Initial type mapping:

```text
사진 -> photo
동영상 -> video
음성메시지 -> voice
이모티콘 -> emoticon
Photo -> photo
Video -> video
The message has been deleted. -> system/deleted_message
```

URL detection can mark `type: "link"` only when the whole message is primarily a link. Otherwise, keep `type: "text"` and store URLs in metadata.

### Tests and fixtures

Add tests before or alongside parser implementation.

Use small sanitized fixtures, not real private exports. Fixtures should cover:

- Korean mobile `.txt` current format
- English mobile `.txt` observed format
- PC `.csv` current format
- multiline text messages
- media placeholders
- deleted/system CSV rows
- unknown or unsupported file format detection

Prefer Python standard `unittest` at first to avoid adding project dependencies.

## Why this approach

This approach matches the project shape because the app already has a Python parser boundary and Electron calls it through a CLI. Keeping that boundary stable lets the parser improve without forcing UI and importer changes at the same time.

Separate parser modules fit the evidence from real exports: KakaoTalk does not have one stable transcript format. Language setting, device, and export age can change timestamps, separators, headers, and row structure. A detector-first design keeps those differences explicit and testable.

Keeping folder import, media matching, and archive IDs outside the parser also keeps responsibilities clean. The parser can stay focused on transcript semantics, while the importer can later handle documents exports, media files, archive cache layout, and chat-level metadata.

## Alternatives considered

1. Keep one large parser with many regex branches

   Not chosen because it would make format-specific behavior harder to test and reason about. CSV parsing, Korean mobile text parsing, and English mobile text parsing have different assumptions and failure modes.

2. Make the importer detect every detail and pass pre-classified lines into one generic parser

   Not chosen because transcript formats need parsing logic close to detection logic. A generic parser would still need format-specific timestamp, divider, multiline, and CSV handling.

3. Convert every export into a common intermediate plain-text format before parsing

   Not chosen because CSV has useful record structure that should not be thrown away. Rewriting quoted multiline records into text first would add another place for data loss.

4. Start with media matching before parser refactoring

   Not chosen because media matching depends on reliable transcript message types, timestamps, placeholders, and source references. Parser correctness should come first.

## Decision log

- Date: 2026-05-27
- Decision: Treat PC CSV rows with empty `Date` and `User` plus `The message has been deleted.` as system/deleted-message rows.
- Reason: The CSV parser already handles quoted multiline records, and inspected examples show these rows are independent records, not unclosed quote continuations.
- Tradeoff: These rows may not have their own timestamp, so the parser will inherit the previous valid message timestamp and mark it as inferred.

- Date: 2026-05-27
- Decision: Untimestamped PC CSV system messages inherit the previous valid message timestamp.
- Reason: These system rows belong chronologically next to the previous message in the CSV export and should appear in the timeline at that point.
- Tradeoff: The inherited timestamp is not the system row's own source timestamp, so parser output must preserve source record order and mark the timestamp as inferred.

- Date: 2026-05-27
- Decision: Use multiple parser modules selected by a detector.
- Reason: Observed KakaoTalk exports differ by language, device, and age.
- Tradeoff: More files and a small dispatch layer, but simpler format-specific parsers and tests.

- Date: 2026-05-27
- Decision: Parser assigns file-local IDs only; archive IDs stay outside parser.
- Reason: A single transcript file can be parsed independently, while archive identity belongs to the folder-level import process.
- Tradeoff: Importer must later convert or namespace local IDs when combining multiple files.

## Expected files to change during implementation

Likely parser implementation files:

- `src/parser/main.py`
- `src/parser/chatParse.py`
- `src/parser/detector.py`
- `src/parser/models.py`
- `src/parser/parsers/base.py`
- `src/parser/parsers/kakao_ko_mobile_txt.py`
- `src/parser/parsers/kakao_en_mobile_txt.py`
- `src/parser/parsers/kakao_pc_csv.py`

Likely test files:

- `tests/parser/test_detector.py`
- `tests/parser/test_ko_mobile_txt.py`
- `tests/parser/test_en_mobile_txt.py`
- `tests/parser/test_pc_csv.py`
- `tests/fixtures/parser/*.txt`
- `tests/fixtures/parser/*.csv`

Possible integration files, only if output schema changes need app compatibility:

- `src/electron/main.js`
- `src/renderer/handleArchive.js`
- `src/renderer/chatDisplay.js`

## Implementation steps

- [x] Add sanitized parser fixtures.
- [x] Add parser data models for parse results, messages, metadata, and warnings.
- [x] Add format detector.
- [x] Add Korean mobile `.txt` parser.
- [x] Add English mobile `.txt` parser for the observed format.
- [x] Add PC `.csv` parser.
- [x] Preserve current CLI behavior through `src/parser/main.py`.
- [x] Keep `chatParse.py` as a compatibility facade or migrate callers carefully.
- [x] Add tests for detector and each parser.
- [x] Run parser outputs against fixtures and confirm stable JSON.
- [ ] Decide whether renderer compatibility changes are needed for the new schema.

## Implementation log

- Date: 2026-05-31
- Change: Added an MVP parser pipeline with preprocessor, detector, parser registry, format-specific parsers, normalizer, validator, unknown parser, and JSON exporter.
- Reason: This creates extension points for new KakaoTalk formats while keeping the current Electron parser CLI compatible.
- Tradeoff: The default CLI still exports the older renderer-compatible message list, so richer parser metadata is currently available only with `--include-metadata`.

- Date: 2026-05-31
- Change: Added sanitized parser fixtures and Python `unittest` coverage.
- Reason: The refactor needs tests before deeper schema or importer changes.
- Tradeoff: Fixtures are intentionally small and do not cover every real-world multiline/mobile edge case yet.

- Date: 2026-05-31
- Change: Mobile `.txt` parsers now append non-structural continuation lines to the previous message and extend `source_line_end`.
- Reason: Inspected exports showed many physical lines without timestamps that are part of multiline KakaoTalk messages.
- Tradeoff: Blank lines are still ignored until we confirm whether KakaoTalk exports meaningful blank lines inside message bodies.

- Date: 2026-05-31
- Change: Added shared placeholder classification for multi-photo messages such as `사진 5장` and `5 photos`.
- Reason: KakaoTalk can send multiple photos as a single chat message, and these placeholders should be treated as photo messages rather than normal text.
- Tradeoff: The parser records `attachmentCount`, but matching those placeholders to actual media files remains importer work.

## Verification plan

Implementation should be verified by:

- running Python parser tests
- manually running `python3 src/parser/main.py <fixture> /private/tmp/parser-output.json`
- confirming JSON is valid
- confirming Korean AM/PM conversion works
- confirming English mobile `.txt` timestamps parse correctly
- confirming PC CSV quoted multiline records stay inside one message
- confirming PC CSV deleted-message rows become `type: "system"` with `systemSubtype: "deleted_message"` and inherit the previous valid timestamp
- confirming unsupported files produce a clear detection warning or error
- confirming the Electron app can still call the parser CLI

## Open questions

- What should the final sender/participant model be if two participants share the same display name?
- Should parser output include both raw timestamp text and normalized timestamp?
- Should URL-only messages become `type: "link"` immediately, or should link extraction remain metadata-only until the analysis feature is designed?
- How much backward compatibility should `chatParse.py` preserve after the new parser modules exist?
