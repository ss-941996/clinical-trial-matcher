# Clinical Trial Matcher demo

Paste a synthetic research patient note, search the full historical TREC 2021 collection, and inspect the top ten trial records and source eligibility criteria. FastAPI serves both the API and a responsive frontend; no Node build or external inference service is required.

This package lives in `demo/` inside the project. It uses the existing `data/indexes/bm25.sqlite`; it does not modify the index. The index must contain the existing 375,580-record snapshot and the `identities` and `metadata` tables created by this project's loader.

## Methods

- **BM25**: the original full-note baseline. This remains the default.
- **Lexical fusion**: weighted BM25 searches using the full note, extracted registry-condition phrases, and the opening sentence. The three top-1,000 lists are combined with reciprocal rank fusion (RRF, constant 60). A second RRF blends this top-1,000 list with original BM25, using development-selected weights 2:1 in favor of the three-query fusion; keep 200. Field weights for the three-query component: title 3, conditions 5, summary 1, eligibility 0.5. This improves coverage in the exploratory comparison but does not beat original BM25 on nDCG@10.
- **Cross-encoder**: a pinned MiniLM relevance model compares each note–trial pair and reorders the same 200 candidates. A frozen development-selected configuration is recorded in `app/config.json`. This is a general MS MARCO model, not a clinical eligibility classifier. Query and trial text are truncated to fixed field budgets within 512 model tokens. Scores are raw relevance logits, not eligibility probabilities.

Fusion broadens candidate coverage; reranking changes their order. Neither establishes eligibility. Negated diagnoses and missing clinical information still need dedicated criterion-level reasoning.

For exact experiment reproducibility, the quantized reranker scores the sorted union of the original BM25 and original three-query fusion top-200 pools, in fixed batches of eight, before ranking the selected 200. This can score up to 400 pairs. Dynamic quantization is sensitive to batch composition; changing the candidate order or batch size can change logits and rankings. The API reports `rerank_scored_pairs` separately from the 200 ranked candidates. The balanced fusion follow-up is a separate retrieval option and is not used to redefine the frozen reranking pools.

## Local Python run

From the project root, using Python 3.12:

```powershell
cd demo
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-demo.txt
.\.venv\Scripts\python.exe download_model.py --output models
$env:TRIAL_INDEX_PATH = (Resolve-Path '..\data\indexes\bm25.sqlite').Path
$env:RERANK_MODEL_DIR = (Resolve-Path 'models').Path
.\.venv\Scripts\python.exe serve.py
```

Open http://127.0.0.1:8000. Omit the model download and `RERANK_MODEL_DIR` to use only BM25 and lexical fusion. Stop with Ctrl+C. Model weights download only during setup or image build; matching runs locally.

## Docker run

For a separate versioned trial-index image plus an app image, see
[the two-image Compose setup](INDEX-IMAGE.md). It initializes a persistent volume
and lets recipients run without a separate raw index download. Both images and local container startup/search/restart/index reuse were verified on 2026-11-17; see [verification](VERIFICATION.md). The bind-mount setup below remains available.

Docker Desktop must use Linux containers on an x86-64 CPU. This first image uses the model's AVX2 ONNX export; ARM and older CPUs have not been validated. Allow at least 4 GB RAM plus disk space for the image, model, and approximately 2.22 GB index. This is a starting allocation to benchmark, not a measured minimum.

```powershell
cd demo
Copy-Item .env.example .env
docker compose config
docker compose up --build -d
docker compose ps
Invoke-RestMethod http://127.0.0.1:8000/health/ready
```

Open http://127.0.0.1:8000. Compose mounts the existing index directory read-only, limits the app to one search at a time, and exposes it only on localhost. If your index lives elsewhere, edit `TRIAL_INDEX_DIR` in `.env`; use forward slashes for a Windows absolute path. Keep the index outside the image and Git. A missing mount fails rather than silently creating an empty directory.

```powershell
docker compose logs --tail 50
docker compose down
```

The same image and index can run after Azure access expires. Users still need the index and model image; exporting a Docker image alone does not include the mounted dataset. Review dataset/model terms before distributing them. The model is pinned to a commit; the app base image is pinned to the tested digest. Transitive Python dependencies and the data-image base tag are not fully locked.

## API

`POST /match` accepts `{ "note": "A research patient description...", "method": "bm25", "top_k": 10 }`. Methods: `bm25`, `fusion`, `rerank`. Results contain trial IDs, titles, conditions, full source eligibility text, original retrieval ranks, relevance scores, and `eligibility_status: "not_assessed"`. The response labels the April 27, 2021 snapshot.

`GET /health/live` and `/health/ready` support probes. `GET /config` exposes available methods. `GET /openapi.json` provides the API schema. One active match per process is allowed; overlapping calls return 429 with `Retry-After: 5`. Use one worker to preserve that limit and avoid loading multiple models. Input is bounded to 12,000 characters and output to ten trials.

Optional local demo authentication: set both `DEMO_USERNAME` and `DEMO_PASSWORD`. Set `REQUIRE_AUTH=true` to fail startup if either is missing. This protects the frontend, API, and schema; health probes reveal only status. For Azure, use HTTPS and require authentication. Basic authentication is a small private-PoC control; add identity integration and durable rate limits before wider access.

## Checks

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-test.txt
.\.venv\Scripts\python.exe run_tests.py
```

Tests exercise a real miniature FTS index, API validation, authentication, and concurrent request rejection. `VERIFICATION.md` records full-corpus checks and the limits of verification performed in this session. The two-image Docker build/run was verified locally on Linux/x86-64 Docker Desktop. Azure deployment and other CPU architectures remain unverified.

## Scope and next stages

The service does not save submitted notes or write to the trial index. Access logging is disabled. Responses use `no-store`; notes remain visible in the browser and downloaded JSON if the user exports results. Hosting infrastructure may add logging, so use synthetic/de-identified data for this PoC.

The next quality stage is criterion extraction and evidence-backed eligibility assessment, including missing information, followed by a frozen evaluation on untouched TREC 2022. Refreshing current trial/recruitment data is separate from benchmarking this historical snapshot. See `AZURE.md` for the portable Azure PoC plan and cleanup checklist.

Sources: [TREC 2021](https://www.trec-cds.org/2021.html), [SQLite FTS5](https://www.sqlite.org/fts5.html), [pinned cross-encoder](https://huggingface.co/cross-encoder/ms-marco-MiniLM-L6-v2/tree/233902d25c440f23af6f7d6e94d2946bac0bee0a).
