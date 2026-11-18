# Current recruiting registry release

Source: [ClinicalTrials.gov API v2](https://clinicaltrials.gov/api/v2/studies); release metadata from [version](https://clinicaltrials.gov/api/v2/version).

| Item | Result |
| --- | --- |
| API / source data timestamp | 2.0.5 / 2026-11-17T10:00:05 |
| Full local catalog | 612,293 records |
| Recruiting serving index | 65,080 records |
| Index size | 482,504,704 bytes (460.2 MiB) |
| Data image tag | `clinical-trial-index:ctgov-20261117T100005-recruiting-572058e0a049` |
| Four sequential example search times | 5.364s, 5.028s, 4.086s, 3.475s |
| App limit | 0.5 CPU / 1 GiB, one worker, one active search |
| Tests | 30 passed (full project and demo suite, sequential) |

The raw pages and full SQLite catalog stay local and are ignored by Git. The source download includes every record for these selected fields: NCT ID, brief/official titles, conditions, brief/detailed descriptions, original eligibility, age/sex fields, overall status, posted/updated dates, and countries. This is not an all-field registry export. Only records with `overallStatus=RECRUITING` enter the demo. Registry-reported status can be out of date; verify the linked record and contact the study team.

The page cache and build checkpoint permit sequential resumption. Every page has a checksum; the build rejects duplicate/malformed IDs, verifies counts and SQLite integrity, and checks that the source release did not change during download. Zero duplicate IDs or parse failures were accepted. Full status counts and missing-field counts are in [profile.json](profile.json); precise provenance/index SHA-256 are in [the serving manifest](../../data/manifests/ctgov-current.json).

The old TREC index `data/indexes/bm25.sqlite` is preserved. Completed TREC 2021/2022 benchmark results remain historical-corpus measurements. The new serving corpus has **no judged relevance evaluation**; the four synthetic examples are operational checks only. TREC 2022 has been used and is no longer an untouched holdout.

## Known limitations

The browser check exposed a concrete eligibility mismatch: the adult type-2-diabetes example retrieves `NCT05065372`, a youth type-1-diabetes study, in its top ten. Shared medications and related disease terms can outrank diagnosis/age constraints. Recruiting status is a serving filter, not an eligibility decision. Criterion-level checks and current-corpus relevance assessment remain future work.

## Operational checks

[docker-verification.json](docker-verification.json) records first startup, all four example searches, reported recruiting status on all 40 displayed records, zero neural scoring calls, restart, app-container replacement, unchanged index checksum/inode/timestamp, and seeder reuse. The replacement uses the same image to test the update procedure; a second app software build was not needed. The lean image contains no NumPy, PyTorch, Transformers or ONNX runtime. Images are verified locally on Linux/x86-64 only.

[Browser QA](frontend-qa.json) verifies the example controls, displayed snapshot, one search action, ten results and expanded source criteria. Mobile layout was not tested. [Release validation](release-validation.json) independently checks the official recruiting count and actual Docker CPU/memory limits. Docker reported about 77 MiB of app memory after four sequential searches; this is a point-in-time reading, not a peak measurement or an Azure estimate. First initialization took 53.1s; restart and container replacement took about 4.1s each on this host.

Azure's Bicep template compiles locally. No cloud resources were created, no images were published, and Azure allocation/cold-start/latency/cost have not been verified. It uses Consumption, min 0/max 1 replica, public image references, and an init-container copy to `EmptyDir`. Local Docker preserves a named volume; Azure's ephemeral copy is rebuilt when a new replica starts. Review [the deployment guide](../../demo/AZURE.md) before publishing or provisioning.

## Reproduce

From the repository root:

```powershell
.\.venv\Scripts\python.exe -S .\src\current_data.py --stage fetch
.\.venv\Scripts\python.exe -S .\src\current_data.py --stage build
.\.venv\Scripts\python.exe -S .\scripts\prepare_current_demo.py
docker compose -f .\demo\compose.images.yaml build trial-index
docker compose -f .\demo\compose.images.yaml build matcher
docker compose -f .\demo\compose.images.yaml up -d --no-build --pull never
```

Open http://localhost:8000. To stop while retaining the index: `docker compose -f .\demo\compose.images.yaml down`. To restart: repeat only `up`. See [demo README](../../demo/README.md) for Python setup, app-only updates and tests, and [image distribution](../../demo/INDEX-IMAGE.md) for another computer.
