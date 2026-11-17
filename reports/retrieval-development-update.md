# Sequential retrieval development checks, 2026-11-17

The current balanced-fusion frontend remains unchanged. A focused-phrase query gave a small, uncertain coverage gain; cached cross-encoder blends failed the five-patient screen. No new inference, internal-validation evaluation, TREC 2022 access or deployment was performed in this work.

## Scope and controls

All comparisons use the full 375,580-trial index and TREC 2021 judgments. The fixed split contains 45 development topics and 30 previously reused internal-validation topics. Only development topics were evaluated here. Five already-used development topics (1-5) served as a cheap screen. The other 40 are a broader development confirmation, not an independent holdout. Repeated development tuning remains exploratory.

We reused the original BM25 and three-query lexical RRF top-1,000 lists. Existing balanced fusion combines these with weights 1:2 and RRF constant 60, retaining 200. Candidate hashes were checked against the frozen baseline. Per-topic metrics were independently checked with pytrec_eval.

## Eligible misses

Across 45 topics there are 3,372 judged-eligible patient-trial pairs: 969 are in the current top 200 and 2,403 are missed. Of the misses, 983 appear somewhere in the union of the two cached top-1,000 lists; 1,420 are outside it. These are patient-trial pair counts, not distinct trials. Macro recall@200 is 31.86%; pooled counts use a different weighting.

Twenty missed examples from five topics were inspected. Focused queries can include incidental conditions and generic terms. This motivated a general phrase heuristic; the audit does not establish clinical reasons for every miss. See [audit and examples](reranking/cached_development_screen/miss_audit.json).

## Retrieval reweighting: 45 development patients

| System | nDCG@10 | Eligible recall@10 | Eligible recall@200 |
|---|---:|---:|---:|
| Original BM25 | 0.4457 | 3.87% | 24.52% |
| Three-query lexical RRF | 0.3939 | 4.36% | 30.21% |
| Outer weights 1:1.5 | 0.4547 | 4.55% | 31.73% |
| Current outer weights 1:2 | 0.4495 | 4.40% | 31.86% |
| Outer weights 1:2.5 | 0.4433 | 4.23% | 31.89% |
| Outer weights 1:3 | 0.4348 | 4.11% | 31.63% |

The prespecified coverage selection with top-ten guards retained the current 1:2 setting. The 1:1.5 variant trades a small amount of coverage for better top-ten results. Nothing was promoted. Full results and paired uncertainty are in [the cache-only report directory](reranking/cached_development_screen).

## Cached reranking: five development patients only

Scores were reused only for exactly matching 200-candidate pools, model revision, token budgets and batching. The existing model is `cross-encoder/ms-marco-MiniLM-L6-v2`, using its frozen ONNX INT8 artifact. No model was loaded for inference.

| System | nDCG@10 | Eligible recall@10 | Eligible recall@200 |
|---|---:|---:|---:|
| Current balanced fusion | 0.4951 | 5.11% | 27.91% |
| Pure cross-encoder | 0.2796 | 2.62% | 27.91% |
| Retrieval/CE RRF, CE weight 0.25 | 0.4557 | 4.29% | 27.91% |
| CE weight 0.5 | 0.4640 | 4.44% | 27.91% |
| CE weight 1 | 0.3959 | 2.57% | 27.91% |
| CE weight 2 | 0.3471 | 2.54% | 27.91% |

None passed the screen (nDCG improvement at least 0.01 and no recall@10 regression). Therefore no expensive expansion was started. This small reused screen cannot establish performance on the full development set. Recall@200 stays fixed because only ordering changes.

## New focused-phrase query

Select the longest multiword phrase found in the existing registry-condition vocabulary, breaking ties by earliest occurrence in the note. Fall back to the existing focused query if no phrase matches. This is a lexical heuristic, not diagnosis extraction. Retrieve weighted BM25 top 1,000, then fuse original BM25, existing lexical RRF and the new phrase list with fixed weights 1:2:0.5. Keep 200.

The five-patient screen raised recall@200 from 27.91% to 31.64%, but reduced nDCG@10 from 0.4951 to 0.4843 and recall@10 from 5.11% to 4.48%. We then evaluated the identical fixed variant on all development patients, separately reporting the other 40.

| Population / system | nDCG@10 | Eligible recall@10 | Eligible recall@200 |
|---|---:|---:|---:|
| All 45: current | 0.4495 | 4.40% | 31.86% |
| All 45: plus phrase | 0.4494 | 4.71% | 32.57% |
| Other 40: current | 0.4438 | 4.32% | 32.36% |
| Other 40: plus phrase | 0.4451 | 4.74% | 32.68% |

On all 45, the recall@200 change is +0.70 percentage points (paired bootstrap 95% interval -0.13 to +1.62 points); nDCG change is -0.0001 (interval -0.0182 to +0.0207). Recall@10 rises 0.31 percentage points (interval -0.21 to +0.95). On the other 40, recall@200 rises only 0.32 percentage points (interval -0.50 to +1.28). All intervals include zero. These results support further investigation, not a reliable improvement claim. See [full grouped results](reranking/focused_phrase_development/grouped_results.json).

These development scores must not be compared directly with the historical 30-patient validation values of 39.82% recall@200 and 0.4484 nDCG@10: the patient populations differ.

## Resource use and reproduction

Every stage ran sequentially in one process at Windows BelowNormal priority, with numeric-library thread limits of one. New SQLite queries used a 32 MiB connection cache, disabled mmap and a 20-second query guard. This bounds connection cache, not total machine memory. There were no parallel tests or new model inference pairs.

| Stage | Wall seconds | Process CPU seconds |
|---|---:|---:|
| Miss audit | 1.621 | 1.125 |
| Cached retrieval comparison | 1.551 | 1.406 |
| Cached reranker screen | 1.143 | 0.922 |
| Five new phrase queries | 4.842 | 2.734 |
| 45-topic phrase confirmation (40 new queries) | 42.293 | 18.094 |

These are incremental experiment times, not complete app latency or CPU-utilization measurements. Confirmation paused 0.25 seconds after each new query. Execution JSON files preserve measurements. No index rebuild was needed.

From the repository root, using the existing project environment and local index/caches, run each command only after the preceding command exits:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe experiments/reranking/cached_development_screen.py --stage audit
.\.venv\Scripts\python.exe experiments/reranking/cached_development_screen.py --stage retrieval
.\.venv\Scripts\python.exe experiments/reranking/cached_development_screen.py --stage rerank
.\.venv\Scripts\python.exe experiments/reranking/focused_phrase_screen.py
.\.venv\Scripts\python.exe experiments/reranking/focused_phrase_development.py
```

Dependencies include the project evaluation packages and demo dependencies. `--deps PATH` optionally adds a separate installed dependency directory. Raw index and inference caches are intentionally not committed; the reranker screen requires matching previously computed scores and does not regenerate missing inference. Protocol checks reject incompatible caches/code. Reruns reuse phrase-query caches, so execution timings will differ and their execution JSON will be replaced.

## Next decision

Keep the current single frontend pipeline. Investigate a better general condition-query rule or a clinically suitable reranker through another prespecified small screen; do not spend CPU expanding the unsuccessful generic reranker. Measure end-to-end latency before promoting any extra query. Freeze a convincing development improvement before reused internal validation and a separately authorized holdout evaluation. TREC 2022 remains deferred; its limited earlier exposure is recorded in [the existing status](trec2022-status.md).
