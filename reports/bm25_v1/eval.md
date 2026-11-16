# BM25 development evaluation

FULL trial corpus; first 10 TREC 2021 development topics only.

Indexed trials: **375,580**. Topics: **10**.

This is the first untuned lexical baseline. It does not check eligibility. TREC 2022 was not used.

| Run | nDCG@10 | Eligible recall@10 | Eligible recall@30 | Eligible recall@200 |
|---|---:|---:|---:|---:|
| bm25v1 | 0.4112 | 0.0193 | 0.0492 | 0.1835 |

## Conventions

Linear gains 0/1/2; unjudged gain zero; recall positives grade 2 only. Zero-eligible recall is null and excluded from macro.
Every per-topic nDCG@10 and eligible recall cross-checked against pytrec_eval (trec_eval).
Eligible recall macro denominator: 10 topics. Inversion rate is averaged only over topics with co-retrieved judged grade-1/grade-2 pairs.
Inversion = excluded trial above eligible trial within the retrieved top 10. No eligible/excluded pair means N/A.

## Per-topic results

| Topic | nDCG@10 | Eligible recall@200 | Judged fraction@10 |
|---|---:|---:|---:|
| 2021:1 | 0.3771 | 0.10638297872340426 | 1.00 |
| 2021:2 | 0.3586 | 0.15217391304347827 | 1.00 |
| 2021:3 | 0.5096 | 0.25 | 0.90 |
| 2021:4 | 0.2054 | 0.20454545454545456 | 0.90 |
| 2021:5 | 0.5551 | 0.19327731092436976 | 1.00 |
| 2021:6 | 0.7717 | 0.21367521367521367 | 0.90 |
| 2021:7 | 0.4249 | 0.144 | 1.00 |
| 2021:8 | 0.3533 | 0.32608695652173914 | 1.00 |
| 2021:9 | 0.3896 | 0.15384615384615385 | 1.00 |
| 2021:10 | 0.1670 | 0.09090909090909091 | 0.90 |

## Error inspection

The machine-readable error_cases.json contains each note, the actual query tokens, judged labels for the top 10, and up to three eligible misses at rank 200.
The misses are examples in sorted ID order, not an estimate of which errors matter most. Inspect criteria before attributing the cause.

## Runtime and reproducibility

Evaluation/search time: 40.94s. Zero LLM/embedding calls; model API cost $0.
Run configuration, code fingerprints, index provenance and per-topic timing are in metrics.json. Full rankings are in run.trec and rankings.json.
Only this fixed baseline has been attempted. Hybrid search, filters and LLM ablations remain pending.

## Limits

- Development result, not held-out performance.
- No eligibility filtering or LLM reasoning.
- Judgments are pooled and incomplete; unjudged does not prove ineligibility.
- Historical recruitment status.
