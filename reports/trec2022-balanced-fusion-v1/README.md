# Frozen TREC 2022 evaluation — 2026-11-18

**All 50 patients, against the full 375,580-trial snapshot.** The app stays on the existing balanced-fusion configuration. No model or fusion settings were tuned using this evaluation.

The two arms are original BM25 and balanced fusion with outer RRF weights 1:2 and constant 60. Each produces its own 200 candidates, with the first ten displayed. No neural reranker or eligibility/status filter runs.

| Pipeline | nDCG@10 | Eligible recall@10 | Eligible recall@30 | Eligible recall@200 | Eligible trials@10 |
|---|---:|---:|---:|---:|---:|
| BM25 control | 0.3847 | 4.80% | 11.43% | 29.50% | 2.62 |
| Balanced fusion | 0.3592 | 4.84% | 10.82% | 32.41% | 2.42 |

Balanced fusion improves candidate coverage at 200 in this run, but does not establish an overall win. BM25 has the higher point estimate for nDCG@10 and more judged eligible trials per displayed ten; macro recall@10 is essentially tied. The nDCG difference interval includes zero. The recall@200 interval is positive, with no multiple-metric adjustment. Keep the preselected frontend unchanged; no new weights or model settings were selected from these labels.

These are macro patient averages. nDCG uses linear grades 0/1/2; excluded trials receive partial relevance credit. Eligible recall counts only grade-2 trials. Unjudged trials receive zero gain, which does not prove ineligibility.
Recall denominator: 50 patients with at least one eligible judgment.

## Paired changes

Balanced fusion minus BM25, computed on the same patients. Intervals use 10,000 paired patient bootstrap samples, seed 941996, specified before downloading the qrels. These intervals are not adjusted for multiple metrics.

| Metric | Mean difference | 95% percentile interval | Improved / tied / worsened patients |
|---|---:|---|---|
| ndcg@10 | -0.0256 | [-0.0748, +0.0217] | 18 / 5 / 27 |
| eligible_recall@10 | +0.0004 | [-0.0097, +0.0114] | 12 / 19 / 19 |
| eligible_recall@200 | +0.0291 | [+0.0016, +0.0591] | 25 / 4 / 21 |

## Runtime and verification

- balanced_fusion: median **8.871 s**; p95 **15.694 s**; maximum **35.837 s**; total **490.726 s**.
- bm25: median **2.898 s**; p95 **5.841 s**; maximum **8.763 s**; total **164.374 s**.

Searches ran one at a time in one low-priority process; there was no neural inference or parallel experiment. Timings include result hydration; fusion ran before BM25 for each patient and warmed-cache effects apply. These are not cold-start or cloud load measurements.

All 100 patient/arm metric sets independently checked against pytrec_eval.

The original app API/retrieval tests passed (5 tests) before freezing. The freeze records source/config/vocabulary checksums and the full index SHA256. All 50 rankings were generated before this run downloaded judgments. No previous partial 2022 rankings were reused.

## Holdout exposure and limits

This is the first completed frozen label-based evaluation for this project. Before the earlier cancellation, 2022 topics and candidate rankings for 13 patients had already been accessed, and the judgments landing page briefly opened. No local 2022 qrels or computed metrics were used to select this configuration. We therefore disclose limited prior exposure rather than claim zero access.

Now that 2022 results have been examined, subsequent 2022 runs are regression checks. Continue experimentation on 2021; use a new independent set for a later final assessment. Do not tune to this report or interpret different-year scores as a paired improvement over the earlier 2021 measurements.

Clinical eligibility is unassessed. Judgments are pooled/incomplete, and the historical snapshot is not a live recruiting-trial database. These retrieval scores do not establish clinical readiness.

## Artifacts and reproduction

See [the frozen protocol](freeze.json), [full metrics](metrics.json), [uncertainty plan](uncertainty-plan.json), [uncertainty results](uncertainty.json), patient files under `rankings/`, and the two `.trec` exports. Official input URLs and checksums are recorded under ignored `data/raw/trec2022/provenance.json`.

Run commands and resource settings: [experiment guide](../../experiments/holdout/README.md). No frontend/Docker/index rebuild or Azure deployment was needed.

## Patient-level comparison

| Patient | BM25 nDCG@10 | Fusion nDCG@10 | BM25 recall@200 | Fusion recall@200 |
|---|---:|---:|---:|---:|
| 1 | 0.7066 | 0.6456 | 27.63% | 21.05% |
| 2 | 0.3695 | 0.3474 | 11.86% | 23.73% |
| 3 | 0.7636 | 0.3144 | 56.00% | 56.00% |
| 4 | 0.9306 | 0.8562 | 39.55% | 32.46% |
| 5 | 0.4010 | 0.3908 | 22.03% | 33.90% |
| 6 | 0.5657 | 0.5861 | 38.98% | 45.76% |
| 7 | 0.4033 | 0.4836 | 53.33% | 60.00% |
| 8 | 0.0318 | 0.0000 | 14.29% | 7.14% |
| 9 | 0.4983 | 0.3312 | 14.29% | 14.29% |
| 10 | 0.2609 | 0.5639 | 7.96% | 9.73% |
| 11 | 0.8121 | 0.5928 | 23.40% | 13.19% |
| 12 | 0.4627 | 0.4525 | 21.57% | 37.25% |
| 13 | 0.4473 | 0.2900 | 42.86% | 57.14% |
| 14 | 0.3179 | 0.3898 | 39.39% | 42.42% |
| 15 | 0.6793 | 0.6225 | 56.56% | 53.28% |
| 16 | 0.4403 | 0.3203 | 22.22% | 16.67% |
| 17 | 0.0000 | 0.0000 | 12.50% | 25.00% |
| 18 | 0.0000 | 0.0000 | 5.56% | 2.78% |
| 19 | 0.0000 | 0.0000 | 0.00% | 10.00% |
| 20 | 0.7639 | 0.7314 | 57.23% | 45.66% |
| 21 | 0.0663 | 0.1100 | 18.75% | 10.00% |
| 22 | 0.6943 | 0.8935 | 19.48% | 37.66% |
| 23 | 0.6622 | 0.3195 | 28.93% | 24.79% |
| 24 | 0.4839 | 0.7075 | 61.54% | 69.23% |
| 25 | 0.0678 | 0.2760 | 10.47% | 11.63% |
| 26 | 0.3704 | 0.4803 | 53.33% | 60.00% |
| 27 | 0.2330 | 0.4394 | 64.00% | 80.00% |
| 28 | 0.0392 | 0.0347 | 8.16% | 20.41% |
| 29 | 0.2751 | 0.3414 | 28.57% | 28.57% |
| 30 | 0.4576 | 0.3511 | 24.84% | 18.95% |
| 31 | 0.0000 | 0.0000 | 20.00% | 16.00% |
| 32 | 0.4078 | 0.2103 | 7.14% | 7.14% |
| 33 | 0.0000 | 0.0000 | 13.33% | 33.33% |
| 34 | 0.4315 | 0.3182 | 50.00% | 66.67% |
| 35 | 0.2743 | 0.1392 | 10.79% | 12.23% |
| 36 | 0.1100 | 0.2201 | 24.62% | 23.08% |
| 37 | 0.5228 | 0.6402 | 48.72% | 58.97% |
| 38 | 0.7682 | 0.9149 | 24.46% | 63.31% |
| 39 | 0.3006 | 0.4528 | 29.86% | 20.83% |
| 40 | 0.5706 | 0.0784 | 34.91% | 27.36% |
| 41 | 0.6732 | 0.1528 | 38.89% | 29.63% |
| 42 | 0.3766 | 0.4947 | 26.67% | 40.00% |
| 43 | 0.0474 | 0.0347 | 46.15% | 38.46% |
| 44 | 0.5140 | 0.7113 | 44.44% | 54.63% |
| 45 | 0.1100 | 0.0000 | 9.09% | 27.27% |
| 46 | 0.5590 | 0.4597 | 34.04% | 24.82% |
| 47 | 0.4161 | 0.3711 | 48.89% | 41.11% |
| 48 | 0.2141 | 0.2886 | 12.50% | 14.77% |
| 49 | 0.1834 | 0.0694 | 31.25% | 25.00% |
| 50 | 0.5532 | 0.5307 | 33.88% | 27.27% |
