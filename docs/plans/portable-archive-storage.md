# Portable Archive Storage Plan

## Problem

Managed archives currently depend on POSIX flock, O_NOFOLLOW/O_DIRECTORY and hard-link publication. Windows cannot execute that path, and exFAT does not support hard links. Preserve the existing copy/hash/checkpoint guarantees while removing these dependencies.

## Current behavior

The `feature/safe-archive-import` implementation has verified chunked copying, SQLite v3 journals, source-independent inspection, and manifest recovery. Tests cover interrupted copies and finalization failures. File inventory rejects case/Unicode collisions. Temporary copy filenames are random and untracked after an abrupt process exit. No Windows runtime or physical exFAT device has been validated.

## Proposed approach

- Add a small `filesystem.py` platform adapter. Use Windows non-replacing rename, macOS `renamex_np(RENAME_EXCL)`, and Linux `renameat2(RENAME_NOREPLACE)`. Never fall back to Unix rename or replace, which can overwrite destinations.
- If a volume rejects exclusive rename, use a persistent publication workspace: retain the full verified payload and owner metadata, create the destination with O_EXCL, then copy and independently verify it. On restart, continue only if the destination identity matches the recorded created file and every existing byte matches the retained payload prefix. Unknown/mismatching files are preserved as conflicts. A crash between exclusive creation and recording identity cannot be proven safe and requires manual conflict resolution; no unrelated file is deleted to guess recovery.
- Keep file fsync mandatory. Try directory fsync on POSIX; record a durability warning when unsupported or on Windows rather than pretend directory persistence is guaranteed. Other I/O errors remain failures.
- Use native flock on POSIX and msvcrt byte-range locks on Windows. Reject symlinks and Windows reparse points in regular-file and directory checks. Report unsupported backends explicitly.
- Probe publication/no-overwrite and directory syncing using only a disposable directory on the destination volume before importing. Persist capabilities and warnings in the existing managed inventory JSON; no schema migration is needed.
- Encode Windows-invalid filename characters and reserved names deterministically, preserving original paths separately. Allocate deterministic suffixes for case/Unicode collisions. Handle file and directory components consistently.
- Give new copy staging workspaces a validated ownership marker containing job/file identity. Recover only those owned workspaces under the destination lock; retain legacy unowned random temporaries and unrelated entries rather than delete them by filename pattern.
- Route copying, database bootstrap, and manifest publication through the same platform adapter.
- Add adapter/failure tests, run the full suite locally, and add a Windows/macOS/Linux CI matrix. A simulated backend test does not count as native Windows/exFAT validation.

## Why this approach

Native exclusive rename publishes an already verified file without another large copy and without requiring hard links. A thin adapter keeps the SQLite, parser and archive schemas stable. Capability probing tests the selected destination instead of assuming all drives attached to an OS have the same behavior.

## Alternatives considered

1. Check existence then ordinary rename: rejected because another process can create the destination between the check and rename, and Unix rename may overwrite it.
2. Copy directly into the final path without a durable retained payload/ownership record: rejected because interruption exposes a partially written final file with no safe recovery evidence. The fallback above adds that evidence; only verified files and completed manifests are reader-visible.
3. Add a third-party cross-platform library: deferred; the limited required primitives are available through the standard library and native C interfaces.

## Decision log

- Date: 2026-11-23
- Decision: Branch `feature/portable-archive-storage` from the clean safe-import feature branch.
- Reason: This work requires the managed importer already built there.
- Tradeoff: This is a dependent branch; review against `feature/safe-archive-import`.

- Date: 2026-11-23
- Decision: File-byte verification remains strict; missing directory-sync support becomes an explicit persisted warning.
- Reason: Windows/exFAT directory persistence APIs differ, while failed writes and mismatched hashes must still block completion.
- Tradeoff: Sudden power-loss durability depends on the selected filesystem/device; no unconditional power-loss guarantee is made.

## Expected files to change

- `src/archive/filesystem.py` (new): native no-overwrite publication, locking, checked opens, syncing, capability probes, owned staging.
- `src/archive/copying.py`: use adapter and owned staging while keeping verification/checkpoints.
- `src/archive/managed.py`: portable bootstrap/manifest publication and persisted capabilities/warnings.
- `src/archive/storage.py`: portable stored names and reparse-point exclusion.
- `tests/test_archive_filesystem.py` (new), copy/storage/managed tests: meaningful failure, restart, and portability coverage.
- `.github/workflows/archive-tests.yml` (new): platform test matrix.
- `README.md` and this plan: usage, supported behavior and remaining validation limits.

## Implementation steps

- [x] Inspect current implementation and create the dependent task branch.
- [x] Implement platform adapter and destination capability probe.
- [x] Integrate portable names and owned staging recovery.
- [x] Integrate copying, bootstrap, and manifest publication.
- [x] Run meaningful adapter and full regression tests.
- [x] Add CI matrix and document actual verification limits.

## Verification plan

Test publication collision without replacing either file, Unicode paths, lock contention across processes, unsupported features, directory-sync warnings versus real I/O errors, Windows dispatch, filename conversion/collisions, stale owned/unowned staging, copied-byte equality, and manifest recovery. Keep existing end-to-end tests enabled on Windows where privileges permit. Use only synthetic temporary files.

If available and authorized by the execution environment, use a disposable exFAT disk image on macOS for real filesystem validation; otherwise explicitly leave real exFAT unverified. Windows execution requires a Windows runner; adding CI alone is not proof that it passed.

## Open questions

- Which destination volumes actually support exclusive rename and directory syncing? Probe each selected volume.
- Native Windows and exFAT validation results must be recorded separately from host/unit-test results.
- Removable-volume identity, network shares, path-length limits, bundled Python, and UI integration remain follow-up work.

## References

- Python os.rename: https://docs.python.org/3/library/os.html#os.rename
- Python msvcrt.locking: https://docs.python.org/3/library/msvcrt.html#msvcrt.locking
- Apple exclusive renaming capability: https://developer.apple.com/documentation/foundation/urlresourcevalues/volumesupportsexclusiverenaming
- Linux renameat2: https://man7.org/linux/man-pages/man2/rename.2.html

## Implementation log

- 2026-11-23: Wrote plan before implementation. Native Windows/exFAT support is not yet claimed as verified.
- 2026-11-24: Added the filesystem adapter, native lock dispatch, checked regular-file opens, reparse-point rejection, no-replace rename, and ownership-backed exclusive-create fallback. Copying, database bootstrap and manifest use the same publication path. Persisted destination capabilities/durability warnings without changing schema v3.
- 2026-11-24: Added reversible stored-name encoding and deterministic collision allocation; source provenance and transcript extensions are preserved. Owned staging recovery requires matching job/file records. Publication recovery is restricted to root-level control workspaces, never interpreted from imported originals.
- Verification: `python3 -m unittest discover` ran 83 tests: 82 passed, 1 skipped because the host filesystem cannot create case-distinct names. Pure allocator tests still cover that collision rule. Tests include a child process exiting via os._exit, cross-process locking, fallback DB/manifest interruptions, changed-prefix conflicts, retained unknown files, full imports with native rename disabled, and read-only reopening without sources.
- Platform limits: Windows dispatch is unit-tested and a native Windows/macOS/Linux CI matrix is added, but remote CI has not run yet. Attempted a disposable exFAT image; the correctly formed hdiutil create command was denied with Operation not permitted even after execution approval. No real exFAT or native Windows success is claimed. No private data was used. Fallback publication can require extra I/O/storage; identity-less crash conflicts remain preserved for manual resolution.
