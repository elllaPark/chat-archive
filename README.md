# Chat Archive

A local desktop app for archiving exported KakaoTalk chat data.

The goal of this project is to move old KakaoTalk chat history, photos, and videos out of the phone app while keeping the conversation readable in a familiar chatroom-style interface. The current app parses KakaoTalk `.txt` exports into JSON and displays the messages in an Electron UI.

## Current Status

This is an early-stage personal archive app.

Working pieces:

- Electron desktop app shell
- KakaoTalk-style chat UI
- Native folder picker for adding an archive
- Python parser for KakaoTalk `.txt` exports
- Local cache of parsed messages under Electron `userData`
- Basic time-of-day and season metadata on parsed messages

Not finished yet:

- Reliable drag-and-drop folder import
- Photo/video file matching and rendering
- Full search
- Analysis dashboard
- Automated tests

## Project Structure

```text
src/
  electron/
    main.js        # Electron main process, IPC, archive indexing
    preload.js     # Safe APIs exposed to the renderer
  parser/
    chatParse.py   # Compatibility facade for the parser pipeline
    detector.py    # Format detection from file/signature samples
    preprocessor.py
    registry.py
    parsers/       # Format-specific parser implementations
    main.py        # CLI wrapper for the parser
  renderer/
    index.html     # Main app UI
    handleArchive.js
    dropDownSearch.js
    chat.css
    style.css
    img/           # UI icons
local-data/        # Ignored local/private imports and generated files
```

## Requirements

- Node.js
- npm
- Python 3

## Setup

Install dependencies:

```bash
npm install
```

If Electron fails to launch because its app bundle is missing, rebuild Electron:

```bash
npm rebuild electron
```

## Run The App

```bash
npm start
```

## Run The Parser Manually

```bash
python3 src/parser/main.py <input.txt> <output.json>
```

Example:

```bash
python3 src/parser/main.py local-data/imports/chat.txt /private/tmp/chat-output.json
```

To inspect parser metadata while debugging detection or unknown formats:

```bash
python3 src/parser/main.py local-data/imports/chat.txt /private/tmp/chat-output.json --include-metadata
```

## Parser Flow

The parser is organized as a small pipeline:

```text
KakaoTalk export file
  -> preprocessor
  -> detector
  -> parser registry
  -> format-specific parser
  -> normalizer
  -> validator
  -> JSON exporter
```

The default CLI output remains compatible with the renderer: it writes a list of message objects. The richer parser result is available with `--include-metadata` and includes detection details, parser used, parse status, warnings, and sample lines for unknown formats.

To add a new KakaoTalk export parser:

1. Add a format signature to `src/parser/detector.py`.
2. Add a parser class under `src/parser/parsers/`.
3. Register the parser in `src/parser/registry.py`.
4. Add a sanitized fixture and parser tests under `tests/`.

## Parsed Message Format

The parser currently outputs messages like:

```json
{
  "timestamp": "2024-06-09 22:04",
  "sender": "mmm",
  "content": "message text",
  "time_of_day": "night",
  "season": "summer"
}
```

## Privacy Notes

This project is designed for private chat history and media.

Do not commit:

- KakaoTalk `.txt` exports
- Photos or videos
- Generated parser JSON
- Electron archive cache files
- Anything in `local-data/`

The `.gitignore` is set up to keep local/private archive data out of Git.

## Development Notes

Archive import flow:

1. The renderer calls `window.archivesAPI.add()` or `window.archivesAPI.addByPath(path)`.
2. Electron indexes selected `.txt` files.
3. Electron runs the Python parser.
4. Parsed messages are merged, sorted, and cached.
5. The renderer reads the cache and displays chat bubbles.

Known technical issues:

- Drag-and-drop currently relies on renderer-side file path behavior that may not work in modern Electron.
- Archive indexing only scans direct child `.txt` files.
- Media messages such as `Photo` and `Video` are not linked to actual files yet.
- Blank lines inside mobile `.txt` multiline messages are not preserved yet.

## Safe Import Journal (In Progress)

`src/archive/database.py` adds a durable import journal to an existing version 1
archive database. `ImportJournal(connection)` migrates it to database version 3
without rewriting legacy import rows. Use a dedicated SQLite connection because
journal operations commit each checkpoint independently.

Jobs track running/paused/failed/completed states; files track
pending/copying/verified/failed states. Verified file records require equal sizes
and matching SHA-256 evidence. The journal stores checkpoints; `copy_import`
performs file copying and hashing. Electron integration is still pending.

Run parser, importer, and journal tests with `python3 -m unittest discover`.

`archive.build_inventory(source, destination)` performs read-only discovery,
classifies transcript samples, checks overlapping paths and available capacity,
and returns file snapshots and warnings. `archive.prepare_import(journal, job_id,
source, destination)` also registers the full inventory atomically in an existing
journal. Neither API copies files or creates the destination folder. The default
space reserve is 64 MiB and can be overridden with `reserve_bytes`.

`archive.copy_import(journal, job_id)` copies a prepared inventory, verifies
SHA-256 and size, and checkpoints each verified file. Re-running it rechecks
completed files and retries interrupted files. Different existing bytes are
never overwritten. The storage adapter uses native locks and no-replace
publication without requiring hard links. Copy success leaves the job running until the
manifest/finalization step described below. Electron integration is still pending.

## Managed Archive Import

From the repository root, import an export folder into a separate archive folder:

```bash
PYTHONPATH=src python3 -m archive import /path/to/export /path/to/archive
```

This creates `archive.sqlite`, preserves transcript and attachment files under
`originals/`, parses verified transcript copies, and writes `manifest.json` before
marking the import completed. The whole process holds one destination writer lock.
The destination must be new/empty or an archive previously created by this command.
Existing source-only databases are rejected rather than automatically replaced.

Retry an interrupted import using its recorded source folder:

```bash
PYTHONPATH=src python3 -m archive resume /path/to/archive
```

Copies are reverified, and a committed index is reused without inserting duplicate
messages. A recognized transcript that fails parsing blocks completion while
retaining verified originals. Unsupported text is preserved with warnings;
partial parser results retain system messages and missing-field information.

Reopen a completed archive without the original export folder, including after
moving the complete archive directory:

```bash
PYTHONPATH=src python3 -m archive inspect /path/to/archive --verify-files
```

Inspection opens SQLite read-only and requires both a completed job and a matching
manifest. `--verify-files` checks full SHA-256 hashes; without it, inspection checks
stored file existence and sizes. The Python equivalents are
`archive.import_managed_archive(source, destination)` and
`archive.inspect_archive(destination, verify_files=True)`.

SQLite v3 persists managed archive identity, the original inventory and warnings,
and the index checkpoint. Stored message/asset file references are relative to the
archive. Multi-file local message IDs are namespaced while raw text and parser
metadata are retained. Media-to-message matching, thumbnail generation, UI wiring,
and application bundling are still pending.

## Portable Storage

`src/archive/filesystem.py` separates OS-specific locking and file publication:

- Windows uses a nonblocking msvcrt byte-range lock and non-replacing rename.
- macOS/Linux use flock and native exclusive rename when supported.
- Volumes that reject exclusive rename use an O_EXCL-created destination plus a
  retained verified payload and ownership journal. Incomplete files never count
  as verified; restart extends only an owned, matching prefix, then hashes again.
- Unrelated existing files are preserved. A conflict, missing ownership evidence,
  or changed partial file stops recovery instead of guessing what to overwrite.
- Marked copy staging is cleaned on retry; unowned legacy temporaries and unknown
  entries are retained. Recovery control directories live outside `originals/`.

The selected volume is probed before importing. File fsync and hash verification
remain required. If directory syncing is unavailable, the manifest records
`directory_sync_unavailable_power_loss_durability_limited`; this explicitly limits
power-loss durability rather than guaranteeing persistence on every device.

New inventories encode Windows-invalid names (for example `15:10.txt` becomes
`15%3A10.txt`) and disambiguate case/Unicode collisions deterministically. Original
names/paths remain in SQLite; transcript extensions are preserved for detection.
Existing archived paths are not rewritten automatically. Older archives with
incompatible filenames may need reimporting before moving to Windows/exFAT.

PowerShell can run the CLI with:

```powershell
$env:PYTHONPATH = "src"
python -m archive import "C:/Exports/Chat" "D:/ChatArchive"
```

Validation status: the macOS suite passes, including simulated unavailable rename,
copy/process interruption, DB/manifest recovery, and Windows adapter dispatch.
`.github/workflows/archive-tests.yml` adds native Windows/macOS/Linux runs on
Python 3.12/3.13, but those remote jobs have not run as part of this local task.
Actual exFAT disk-image creation was blocked by host permissions, so real exFAT
and native Windows validation remain outstanding. An extremely narrow fallback
crash between exclusive destination creation and ownership checkpointing retains
the conflict for manual resolution. Network shares, volume identity checks,
path-length handling and sudden power-loss testing remain follow-up work.

## License

ISC
