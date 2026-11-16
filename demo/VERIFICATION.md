# Verification performed in this session

- Four FastAPI tests passed, using a real miniature SQLite FTS5 index: search response and read-only behavior; input rejection; authentication; genuine overlapping-request rejection with 429.
- Frontend JavaScript syntax check passed with Node. In-browser synthetic-note searches returned ten results for BM25 and lexical fusion against the full 375,580-trial index. Expanded eligibility text and registry links were inspected. The form locks the note and method during a search and clears the old results for a new request.
- Narrow and desktop layouts were visually inspected. The saved preview records the desktop view. The temporary preview server was stopped after browser verification.
- All 200 IDs **and scores** for both BM25 and lexical fusion exactly matched the prior experiment's caches for patients 1 and 10 (four full rankings). See `full_corpus_checks.json`.
- Reranker unit checks passed for candidate preservation, deterministic ties, reciprocal rank arithmetic, and the 512-token field budgets. The separate experiment independently checks every evaluation result with pytrec_eval.

- The actual-model `/match` reranking check passed for development patient 10: all ten IDs match the fixed experiment and maximum logit difference is exactly 0. The balanced-fusion API top ten also match the cached-ranking calculation. See `rerank_api_checks.json`. This check caught batch-dependent dynamic quantization; the API now uses the exact sorted union and batches from the experiment. The selected ranking still contains 200 candidates.
- The selected balanced-fusion implementation also exactly matches every ID and score in the full 200-candidate pool for validation patient 11. See `balanced_fusion_checks.json`.
- In that concurrent-workload check, full retrieval plus reranking took 84.6 seconds and balanced fusion took 33.6 seconds. These are individual observed requests on this machine, not typical latency or an Azure benchmark. Performance needs work before wider use.

## Not verified

Docker Compose configuration/build, container startup, Linux runtime behavior, restart/health checks inside Docker, and Azure deployment were not executed. Default Docker/Azure account checks failed on protected configuration/daemon access. Automatic approval review rejected the elevated retry because this session forbids escalated permissions. Docker and Azure commands in the documentation are prepared next checks, not claims of successful execution.

The Desktop repository was read-only. This package has not been applied or committed there. No push, image publication, or Azure resource creation took place.
