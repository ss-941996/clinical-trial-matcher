# Hybrid frontend/backend verification — 19 November 2026

The source default now uses balanced lexical top1000 + MedCPT top1000 →
equal-weight RRF (constant60) → 200 unique trials → display top10 directly.
No cross-encoder is requested or loaded in this serving pipeline.

The local bundle contains the existing 5,120 historical trial vectors, copied
without new inference. Query inference uses the evaluated quantized MedCPT
model. Startup checks index/model/vector hashes and identical trial identities;
missing or incompatible bundles fail startup. The frontend has one search
button and reports the active collection, historical subset limitation and
unchecked eligibility. Request serialization, rate limits and authentication
remain in place.

Verification:

- 13 API tests passed in 4.86s, including semantic-only candidates, no reranking,
  mismatched identities, corrupt bundle files and unavailable vector indexes.
- Five sequential API searches exactly reproduced the saved hybrid top10 for
  patients 1–5. All retained 200 candidates and scored zero cross-encoder pairs.
- Observed first-pass query times: 1.014–1.446s with one logical CPU and one
  inference thread. These are five measurements, not a production latency estimate.
- A real local HTTP server passed readiness, HTML/JS delivery and a synthetic
  search: 200 candidates, ten displayed, no cross-encoder. It was then stopped.
- `git diff --check` passed. See verification.json for measured results.

The pilot evidence remains nDCG@10 0.3030, eligible recall@10 27.78%, recall@200
88.89%. Recall averages three patients with eight judged eligible trials total;
this biased, reused subset does not establish full-corpus or current-data quality.

Run from the repository root:

```powershell
& .\.venv-hybrid\Scripts\python.exe .\demo\serve_hybrid.py
```

Open <http://127.0.0.1:8000>. The local ignored environment reuses dependency
files already available on this machine; no model or package download was
performed. For a clean portable installation, use demo/HYBRID.md.

Existing Docker/Azure configuration explicitly retains the legacy lexical
pipeline and current recruiting index. Hybrid image packaging and vectors for
current recruiting data remain outstanding. No Azure resources were used.
