# Local verification — 2026-11-17

## App and tests

- The Python server started against the full 375,580-record April 2021 index. Readiness returned 200.
- In-browser synthetic-note balanced-fusion search displayed ten of 200 candidates. Source eligibility criteria expanded correctly, and registry links pointed to the displayed trial IDs.
- The optional original-BM25-pool reranker completed in the browser and displayed retrieval-to-output rank changes. Eligibility remained explicitly unchecked.
- All 21 API, index-image initializer and baseline tests passed. Tests cover validation, authentication, real miniature FTS search, read-only access, overlapping-request rejection, corrupt-index rejection and safe index reuse. The first test attempts needed the demo import path and an accessible test temporary directory; these were corrected before the successful run.
- The temporary Python server was stopped. Docker serves the demo at http://127.0.0.1:8000.

## Two Docker images

Both `clinical-trial-matcher:local` and `clinical-trial-index:trec2021-b70ca9cdb12e` built on Linux/x86-64 Docker Desktop. The initial index-image build transferred the 2.22 GB SQLite file and verified its full SHA-256. First container startup initialized an empty named volume; the initializer exited 0 before the app started.

[Recorded integration checks](../reports/docker-verification.json) confirm:

- BM25, balanced fusion and the existing cross-encoder each returned ten results from 200 candidates, with source criteria and unchecked eligibility labels.
- The synthetic-note server times were 1.751 s, 4.667 s and 35.314 s respectively. The existing reranker scored 293 pairs because its frozen protocol scores the union of two original pools. These are single observations, not typical latency or an Azure benchmark.
- Restart preserved the app container and returned the same BM25 top ten. App-container recreation changed the container ID and preserved balanced-fusion results.
- The named volume, 2,220,654,592-byte file size, inode, modification timestamp and full SHA-256 stayed identical across restart and replacement. Re-running the initializer logged `Reused verified index; no copy needed.`
- Initial seeding succeeded, but its duration was not measured. The JSON's Compose-up timing refers to a later start using the initialized volume.

The app Dockerfile pins the tested Python base-image digest and copies the model helper before downloading weights. Subsequent API/frontend changes can reuse the dependency and model layers. The updated app image built successfully, but the dedicated old-image/new-image comparison was interrupted before producing a report. A subsequent user-run Compose start reported the app started and the initializer exited; this is not a substitute for the interrupted comparison. Same-image container replacement and index reuse are covered by the completed integration report.

Run commands from the repository root:

```powershell
cd demo
docker compose -f compose.images.yaml build
docker compose -f compose.images.yaml up -d --no-build --pull never
Invoke-RestMethod http://127.0.0.1:8000/health/ready
```

After installing Python test requirements, the repeatable Docker integration check is:

```powershell
python verify_docker.py --output ../reports/docker-verification.json
```

It uses the local unauthenticated demo on port 8000 and recreates/restarts only this Compose project's app. To test a genuinely new app image against the current running image:

```powershell
docker compose -f compose.images.yaml build matcher
python verify_app_update.py --output ../reports/docker-app-update.json
```

Ordinary `docker compose -f compose.images.yaml down` stops the project and preserves its index. Avoid `down -v` when retaining data.

## Historical retrieval checks

Earlier checks remain in `full_corpus_checks.json`, `rerank_api_checks.json` and `balanced_fusion_checks.json`: cached BM25/fusion rankings matched the API, and fixed-batch reranker logits matched the original experiment. Earlier 84.6 s reranking and 33.6 s fusion timings were measured under a different concurrent workload and are not comparable speedup evidence.

## Limits

This is research retrieval, not criterion-level eligibility assessment. ARM/older CPUs, Azure deployment, registry publication and recipient image archives have not been verified. The reranker uses the AVX2 ONNX export. TREC 2022 was excluded from today's work; prior limited exposure is recorded in [the status report](../reports/trec2022-status.md).

## Deferred model work

The full balanced-fusion reranking experiment was stopped before development selection and validation. [Its status record](../reports/reranking/balanced_reranker/README.md) identifies partial checkpoints. No new ranking-quality improvement is claimed. Concurrent native scoring overloaded this laptop; resume with one process if this work is revisited.

## Single frontend pipeline follow-up — 2026-11-17

The frontend method selector was removed. Browser requests omit `method`; the server uses `app/config.json` (`default_method: fusion`). Explicit research API overrides remain available. This selects the completed balanced-fusion coverage/eligible-recall/CPU trade-off, without claiming a winner on all metrics or promoting the unfinished cross-encoder combination.

Eight focused API/index-image tests passed, including the configured-default/explicit-override contract. JavaScript syntax and Git whitespace checks passed. Only the app image was rebuilt; dependency and model layers were cached, and the existing index volume was reused without rebuilding its image. Docker readiness and `/config` confirmed the selected `fusion` default.

An actual browser search using the built-in synthetic example displayed ten results from 200 candidates, with no method selector. The server-reported time was 14.7 seconds for that single request. No cross-encoder inference or benchmark evaluation was run. The earlier verification sections describe the previous multi-option frontend.
