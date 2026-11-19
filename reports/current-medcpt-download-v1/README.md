# Full recruiting-download MedCPT run — 19 November 2026

Source: `C:\Users\S.Sharma\Downloads\ctg-studies.json`, downloaded 19 November.
Streaming validation counted **65,160 records, all RECRUITING, 65,160 unique IDs**.
File size: **910,651,336 bytes (868.46 MiB)**. The export does not provide an
authoritative registry-release timestamp; the download date is not that timestamp.

The user expanded the former 20,000-trial plan to all valid recruiting trials
in this file. No Azure compute is used. The serving app remains on the pilot
until the completed current-data bundle is tested.

## Durable checkpoints and resource limits

- One worker, one logical CPU, BelowNormal priority, one ONNX/BLAS thread.
- Default 50% duty cycle: short waits between batches, targeting half of one
  core (about 3.6% of a 14-logical-CPU machine). Individual active intervals can
  use the one assigned core. No system-load suspension or parallel inference.
- Four trials per inference batch. Every successful batch is committed to
  `vectors.sqlite` with SQLite `synchronous=FULL` and rollback journaling.
- Each vector stores its trial ID, input-text hash, vector hash, represented
  source byte count and encoding timestamp. Model/index/source hashes bind the run.
- Human-readable checkpoint every 32 trials, summary every 256, liveness every
  30 seconds. Source-byte milestones at 10%, 20%, …, 100% are logged. Progress
  shows both source bytes represented and trial count; they need not rise together.
- Ctrl+C or a stop marker requests a graceful stop: complete and commit the
  current batch, then exit. A forced kill can require recomputing the unfinished
  batch (at most four trials); already committed batches remain reusable.
- Resume verifies source, protocol, database integrity, text hashes and cached
  vectors. A process lock prevents a second worker on the same run directory.
- Final export uses 256-vector shards and a checksummed completion manifest.
  The general bundle builder then packages matching lexical/vector indexes.

SQLite atomic-commit guarantees apply to the local filesystem. Keep the
download unchanged and allow OneDrive to sync completed files; cloud sync is
not a substitute for a live database's local recovery journal.

## Run, stop, resume and watch

From the repository root in PowerShell:

```powershell
# Start or resume as a hidden background process; no terminal needs to stay open.
.\scripts\run_current_medcpt.ps1 -Action Start

# Request a safe stop, then check stage=stopped before shutting down.
.\scripts\run_current_medcpt.ps1 -Action Stop
.\scripts\run_current_medcpt.ps1 -Action Status

# Resume with the same Start command. Keep the computer awake while running.
.\scripts\run_current_medcpt.ps1 -Action Start

# Live logs, including 10% milestones:
Get-Content .\data\current\ctgov-download-20261119-medcpt\live.log -Tail 10 -Wait
```

`-Action Run` runs in the foreground and supports Ctrl+C. Use `-Source` if the
original JSON is stored elsewhere; its contents must match. `-DutyCycle 0.5`
can reduce or raise the CPU duty fraction without changing model outputs.
Closing the app, a reboot or OS termination may stop the worker; persistent
checkpoints allow restart. Chat messages require active monitoring; log events
continue without an active chat and do not trigger automatic messages.

Checks before launch: **37 tests passed in 9.95 seconds**, including graceful
stop after an inference batch, resume skipping saved vectors, completed-run
reuse without model inference, corruption rejection, protocol mismatch,
interrupted import recovery, streaming boundaries and duplicate-worker refusal.

## Seven-step status

- [x] 1. Generalise the pipeline and bundle configuration.
- [x] 2. Prepare the full downloaded recruiting collection (65,160 records).
- [ ] 3. Build all trial embeddings and final indexes locally — in progress.
- [ ] 4. Test the completed current-data app.
- [ ] 5. Package and test portable app/data Docker images.
- [ ] 6. Deploy the tested Azure demo.
- [ ] 7. Establish the manual refresh workflow.

The initial import stopped safely at 6,400 saved rows because a throttle-wait
deadline elapsed between clock reads. The wait calculation was corrected,
a regression test added, and the run resumed from those saved rows. No trial
embeddings existed at that point and no committed import records were lost.

## Graceful stop checkpoint — 19 November 2026, 19:13 CET

Stopped at the user's request. Saved 11,288 of 65,160 vectors, covering
162,005,442 source bytes (154.50 / 868.46 MiB; 17.7901%).
SQLite integrity check, all saved vector SHA256 checksums, vector dimensions
and finite-value checks passed. Last saved trial: NCT05460416.
See `stopped-checkpoint.json` for exact hashes and file sizes.

A separate verified local backup includes the complete run directory and the
original downloaded JSON at:
`C:\Users\S.Sharma\Documents\Codex\backups\clinical-trial-matcher\checkpoint-20261119-191352`.
Backup database/protocol hashes and source JSON hash match the originals.
Large checkpoints and source data are intentionally excluded from Git.

Resume from the repository root with:
` .\scripts\run_current_medcpt.ps1 -Action Start `
The worker validates the unchanged source/protocol and reuses saved vectors.
Do not delete the original download, model files or run directory.
