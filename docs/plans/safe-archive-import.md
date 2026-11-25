# Safe Archive Import Plan

## Problem

Preserve exported transcripts and attachments in user-selected archive storage with verified copies, persistent progress, cancellation, and restart recovery. Source exports must remain unchanged.

## Current behavior

- `src/archive/importer.py` creates schema version 1 with imports, messages, and assets. It discovers files recursively, extracts sending timestamps and numeric sequence suffixes, and stores normalized messages.
- It references source files without copying or hashing them. All discovered files, including transcripts, currently become assets.
- It rejects existing databases and removes a newly created database after errors. This behavior cannot support resumable imports.
- `tests/test_archive_importer.py` verifies initial database insertion and deterministic asset discovery.
- Electron `indexArchive()` still independently scans direct-child TXT files and writes legacy JSON. It does not call the SQLite importer, support destination selection, or report persistent progress.
- Electron removal currently deletes its cache directory. Managed originals must not be placed under that deletion path without revisiting removal semantics.
- Existing tests: 14 passing on 2026-11-23.

## Proposed approach

### 1. Inventory and destination checks

Accept separate source and archive destination roots. Keep the destination outside the source tree, reject overlapping roots, and use archive-relative paths. Initially skip symlinks and special files with explicit warnings rather than following them outside the source.

Build a deterministic inventory of all regular source files. Classify transcripts using detection signatures and attachments separately, while preserving both. Unsupported transcripts remain preserved and produce warnings. Record relative path, size, and modification time as a source snapshot.

Calculate required copy bytes and check available destination space before copying, with additional headroom for SQLite and temporary data. Space checking is advisory; handle later write failures too. Do not change or delete exports.

### 2. Persistent import journal

Extend SQLite with import jobs and per-file records rather than reusing a function that deletes failed databases. File states: pending, copying, verified, failed. Job states: running, paused, failed, completed.

Store source identity/snapshot, stored relative path, expected size, SHA-256, actual size, error details, and progress checkpoints. Commit file verification independently so failures do not discard completed work. Finalize the manifest only when all required files are verified; parsing warnings remain distinct from copy failures.

Introduce an explicit migration path for schema version 1. Preserve existing source-referencing databases and do not label them portable or verified until their originals have been copied successfully.

### 3. Verified copying

Copy in bounded chunks into an exclusively created temporary file on the destination volume. Compute source SHA-256 while reading, flush/sync copied data, then independently read the destination to compare size and SHA-256. Recheck source metadata for changes during copying; changed files must not be marked verified.

Publish the verified file without overwriting existing originals, then commit its verified state. Define recovery for a crash between filesystem publication and the SQLite commit: reverify the published file before recording completion. Keep temporary files identifiable and never remove unrelated files.

Preserve relative directory structure and original names. For case-insensitive or Unicode filename collisions, allocate a non-conflicting stored name and retain the exact original relative path in the journal. Numeric media suffixes are source ordering evidence and must remain in metadata.

### 4. Resume, cancellation, and duplicates

Resume a stable job ID, rechecking the source snapshot and revalidating previously verified copies before skipping them. Restart incomplete individual files from the beginning in the MVP; byte-offset resume is deferred.

Check cancellation between chunks and files, save a paused state, and leave verified originals intact. Missing external disks and disk-full errors must leave recoverable journal entries. Allow one active writer per archive.

Deduplicate within an archive only after size and content hash verification, retaining every source provenance record and distinct message relationship. Do not deduplicate based on filename or timestamps alone. Preserve same-name files with different bytes. Cross-archive deduplication is deferred.

### 5. Integration boundary

Implement and test the Python copy/journal service first. Keep parser extraction responsibilities unchanged. A later increment will connect destination selection, progress, cancel/resume, and archive registration through narrow Electron IPC methods. Register an archive as ready only after verified import completion. Media matching and thumbnail generation follow this milestone.

## Why this approach

The repository already uses Python SQLite and has an importer test. Extending that boundary avoids introducing another database owner. Per-file verification and commits limit recovery work for large archives. Separate copy and parsing outcomes prevent unsupported transcript formats from losing original files.

## Alternatives considered

1. Extend the synchronous Electron JSON indexing loop: it has no durable import journal and currently blocks on filesystem operations; keep it as the existing UI path until integration is tested.
2. Copy everything in one SQLite transaction and roll back on failure: filesystem writes do not roll back with SQLite, and a failure would discard useful progress.
3. Resume at byte offsets: adds checkpoint and partial-hash complexity; restarting only the interrupted file is sufficient for the first implementation.

## Decision log

- Date: 2026-11-23
- Decision: Build on the existing SQLite importer in a feature branch based on `docs/media-indexing-plan`.
- Reason: That branch contains the initial importer and confirmed storage policies required by this work. This is a dependent branch, not a fresh main-based task.
- Tradeoff: Review against that branch to isolate new work; reconcile its ancestry when merging.

- Date: 2026-11-23
- Decision: Verify copies before marking files complete and resume at file boundaries.
- Reason: This provides recoverable progress with a manageable MVP implementation.
- Tradeoff: An interrupted large video must be copied again.

## Expected files to change

- `src/archive/importer.py`: integrate managed import without discarding interrupted work; preserve existing API where practical.
- `src/archive/storage.py` (new): inventory, destination checks, chunked copying, verification, and collision handling.
- `src/archive/database.py` (new if needed): schema migration and journal operations; keep small helpers in importer if separation adds no value.
- `src/archive/__init__.py`: expose the managed import entry point.
- `tests/test_archive_importer.py`: preserve current behavior coverage and add journal integration tests.
- `tests/test_archive_storage.py` (new): verify copy and failure/recovery behavior using temporary synthetic files.
- `README.md` and this plan: document execution, progress, and limitations.
- Later UI integration: `src/electron/main.js`, `src/electron/preload.js`, and `src/renderer/handleArchive.js`, with a separate pre-edit plan.

## Implementation steps

- [x] Inspect importer, SQLite schema, Electron import flow, and existing tests.
- [x] Create the task branch and write this plan.
- [x] Define migration and persistent journal contracts.
- [x] Add inventory and destination/space checks.
- [x] Add verified copying with no-overwrite publication and existing-file revalidation.
- [ ] Add deterministic collision renaming (current behavior rejects conflicts).
- [ ] Add cancellation, per-file resume, and duplicate handling.
- [ ] Verify failure recovery and source preservation.
- [x] Connect database bootstrap, durable inventory, copying, parsing and finalization.
- [x] Verify source-independent reopen and completed archive relocation.
- [ ] Plan and implement Electron integration separately.

## Verification plan

Use synthetic temporary exports; never mutate private samples. Verify byte/hash equality and that source files remain unchanged. Test nested files, transcripts and attachments, unknown extensions, duplicate bytes, different bytes with the same name, numeric ordering metadata, and platform filename collisions.

Inject failures for insufficient space, destination disconnection/write errors, hash mismatch, source changes, cancellation, and interruption between publication and journal commit. Resume and confirm verified files and provenance are preserved without duplicate records. Test migration from version 1, reject unsupported newer schemas, and run the existing parser/importer suite.

## Open questions

- Choose the exact free-space reserve and chunk size during implementation; keep them configurable and testable.
- Confirm cross-platform no-overwrite publication and external-volume filesystem behavior during implementation.
- Design archive removal and backup semantics before connecting managed originals to Electron's current remove handler.
- Finalize the progress/cancel/resume IPC contract in the UI integration milestone.

## Implementation log

- 2026-11-24: Platform-storage follow-up is implemented on dependent branch `feature/portable-archive-storage`; see `docs/plans/portable-archive-storage.md` for portable publication, Windows locks/names, fallback ownership recovery, CI, and validation limits. Earlier POSIX/hard-link-only limitations below describe historical milestones.

- 2026-11-23 orchestration complete: added managed import/resume/inspect APIs and CLI. New databases are initialized in a temporary file before no-overwrite publication; v3 stores managed identity, immutable inventory/warnings, and transactional index checkpoints. Existing unrelated/source-only databases are rejected without migration. Transcript copies are parsed via the existing CLI; raw text, missing fields, system rows, source locations and file-local IDs are preserved in SQLite. Attachments use archive-relative stored paths and hashes.
- Completion recovery: SQLite indexing commits once, then a deterministic manifest is synced/published, then the job is marked completed. Inspection requires both completed state and a matching manifest. Retry reuses the committed index and reconciles an already-published matching manifest. Copy/parse/write failures retain verified originals and recoverable journal records. One destination lock covers the full operation.
- Validation covers clean CLI imports, namespace collisions across transcripts, unknown/partial formats, parser and index failures, manifest and completion-checkpoint failures, source-independent reopening, relocated archives, and corruption. Full-file hashing is optional during read-only inspection and performed before import completion. Remaining limits include parser memory use per transcript, repeated hashing cost, source requirement for incomplete-copy retries, POSIX/hard-link filesystem support, and Electron/bundled runtime integration. No private samples were changed or imported for this milestone.

- 2026-11-23 orchestration plan: add `src/archive/managed.py` and a small `__main__.py` entry point for bootstrap, durable inventory, locked copying, parsing verified originals via the existing parser CLI, transactional indexing, manifest publication, and completion. Migrate to v3 with singleton managed-archive identity, persisted inventory/warnings, and indexed-import checkpoint. Reuse importer insertion helpers and namespace file-local message IDs; retain raw/missing-field metadata. Expose read-only archive inspection using relative stored paths so completed archives reopen without sources and after relocation.
- Expected changes: `database.py` migration/registration, `copying.py` internal lock-held entry point, package exports, new managed module/CLI and `tests/test_managed_archive.py`, README and this plan. Tests cover clean import, source-independent reopen, relocation, parser failure, rollback, and both manifest/DB interruption orders. Keep Electron integration and Windows/exFAT support out of this increment.
- Chosen approach: use the current Python parser subprocess boundary rather than change every parser import or mutate global sys.path. Keep one destination lock across all stages rather than release it between copying and indexing. Manifest and SQLite cannot commit atomically together, so publish a deterministic manifest first and require both it and completed DB state when opening; retries reconcile a manifest published before the completion commit.

- 2026-11-23: Added `copy_import()` with bounded streaming, SHA-256 comparison, file/directory syncing, POSIX writer locking, no-overwrite hard-link publication, and independent file checkpoints. Rechecks previously verified files and recovers files published before a missing DB checkpoint. Simulated write failures, corrupt copies, changed sources, duplicate destinations, and interrupted jobs are covered with synthetic temporary files. Closed existing importer/test SQLite connections explicitly after resource warnings appeared during the suite.
- Copy milestone limits: currently POSIX only; external filesystems must support hard links and directory fsync or the import fails recoverably. No Windows/exFAT fallback is implemented. The job remains running after successful file verification until orchestration/manifest finalization is implemented. Collision renaming, content deduplication, orphan-temp recovery, cancellation UI, and destination volume identity checks remain pending. Path checks reject observed symlinks but are not a sandbox against hostile concurrent directory replacement. Real private exports were not copied during tests.

- 2026-11-23 copy milestone plan: add `src/archive/copying.py` to stream source files into same-directory temporary files, hash source and destination independently, sync and publish with a no-overwrite hard link, and checkpoint verified files. Recheck source snapshots and paths. Recover published-but-uncommitted files by hashing rather than replacing them. Use a destination writer lock on POSIX; unsupported platforms/filesystems fail explicitly. Add synthetic copy, corruption, source-change, write-error, and restart tests. Update package exports and README. Keep collision renaming, cross-file deduplication, UI progress/cancel, and manifest finalization for later increments.

- 2026-11-23 completed inventory milestone: `build_inventory()` reads bounded text samples, reuses detector signatures, preserves unknown files, excludes symlinks/special files with warnings, rejects overlapping roots and portable filename collisions, and checks copy bytes plus a configurable 64 MiB reserve. `prepare_import()` registers all file snapshots atomically without creating destination files. New tests cover classification, encodings, overlap aliases, capacity errors, source changes, special files, and transaction rollback.
- Limitations: free-space checks are advisory; 64 MiB is an initial configurable reserve, not a guarantee for database size. Classification/warnings are returned in memory while snapshot paths/sizes/mtimes are persisted. The copy milestone must persist reports, handle safe collision renaming, recheck paths/snapshots, and validate existing destination contents. The journal must already exist outside the source tree; bootstrap and UI wiring remain pending.

- 2026-11-23 plan for inventory milestone: add `src/archive/storage.py` for deterministic no-symlink inventory, bounded transcript detection, overlapping-root and free-space checks. Add atomic inventory registration in `src/archive/database.py`, package-compatible imports in the detector, exports in `src/archive/__init__.py`, and synthetic filesystem tests in `tests/test_archive_storage.py`. Reuse detector signatures instead of duplicating KakaoTalk patterns. No copying or UI changes in this increment. Store source snapshots in existing journal rows; classification and warnings are returned by the preparation result for now.

- 2026-11-23: Inspected existing code and confirmed baseline tests pass (14). Added this plan only; implementation remains pending.
- 2026-11-23: Added `src/archive/database.py` with transactional v1-to-v2 migration using PRAGMA user_version, persistent jobs/files, guarded transitions, and size/hash evidence constraints. Existing imports keep their original schema-version provenance. Added journal tests covering reopen/resume, migration rollback, future-version rejection, duplicates, and verification requirements.
- Integration boundary: the existing importer still creates v1 databases; the managed-copy service will explicitly migrate and own a dedicated journal connection. A crashed running job must be marked paused/failed before resuming. Inventory sealing, exclusive job ownership, source/destination checks, actual hashing, copying, and UI integration remain pending; journal records alone do not verify filesystem contents.
