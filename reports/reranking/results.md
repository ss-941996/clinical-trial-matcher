# Reranking and demo results

All 375,580 trials are searched. The split is 45 development topics and 30 **reused internal validation** topics from TREC 2021. These validation topics were already inspected during retrieval experiments; this is exploratory evidence, not an untouched final test. TREC 2022 remains untouched.

Lexical fusion combines weighted BM25 rankings for the full note, registry-condition phrases, and the opening sentence using equal reciprocal rank fusion (constant 60). It selects 200 candidates. Reranking reads the note and each of those 200 candidates together and changes their order; it cannot recover trials missing from that candidate pool.

## Fixed comparison

Model: `cross-encoder/ms-marco-MiniLM-L6-v2`, pinned revision `233902d25c440f23af6f7d6e94d2946bac0bee0a`, quantized ONNX CPU export. This is a general passage relevance model, not a biomedical eligibility model. Raw scores are logits, not probabilities. Inputs reserve 128 wordpieces for the note and separate title/condition/summary/eligibility budgets within 512 tokens.

Two candidate pools were compared: original BM25 and lexical fusion. Each was tested unchanged, with pure cross-encoder order, and with retrieval/cross-encoder rank blends at model weights 0.5, 1 and 2 (retrieval weight 1, RRF constant 60). The development winner was frozen before the validation phase.

Scores are computed over the sorted union of both top-200 pools in batches of eight, then reused for each pool. Dynamic quantization depends on batch composition. The API deliberately reproduces this exact scoring context; candidate-pool or batch changes require a new evaluation. This can score up to 400 pairs while the selected ranking still contains 200 trials.

Development best overall: `baseline`. Best reranking arm: `baseline_blend_0.5`.

The original BM25 baseline won development selection. On reused validation, the best model blend produces a small nDCG@10 increase and slightly more eligible trials in the top ten, while macro eligible recall@10 decreases. This mixed result does not establish a consistent reranking improvement or justify promoting this general model. Balanced fusion is the stronger candidate-coverage result below.

## Reused internal validation: 30 patients

| Method | nDCG@10 | Eligible recall@10 | Eligible recall@200 | Eligible trials in top 10, mean |
|---|---:|---:|---:|---:|
| baseline | 0.4701 | 6.26% | 30.27% | 2.77 |
| lexical_rrf | 0.4151 | 6.73% | 37.74% | 2.60 |
| baseline_blend_0.5 | 0.4739 | 6.06% | 30.27% | 2.87 |

Recall is averaged per patient. nDCG uses the existing linear grades 0/1/2 and treats unjudged trials as zero; excluded trials still receive grade 1. Unjudged does not mean clinically irrelevant, and differing judgment coverage can affect these comparisons. This metric does not measure confirmed clinical eligibility.

| Method | Judged eligible patient–trial pairs in top 200 | In top 10 |
|---|---:|---:|
| baseline | 569 / 2198 | 83 / 2198 |
| lexical_rrf | 635 / 2198 | 78 / 2198 |
| baseline_blend_0.5 | 569 / 2198 | 86 / 2198 |

These are patient–trial pairs, not unique trial IDs. Pooled ratios differ from the per-patient mean recall above.

## Paired differences versus original BM25

Bootstrap intervals reflect variation across these same 30 topics, not uncertainty from future datasets or model selection. A positive observed difference alone does not establish a reliable improvement.

```json
{
  "lexical_rrf": {
    "ndcg@10": {
      "mean_difference": -0.05492676767560984,
      "paired_bootstrap_95pct": [
        -0.13024928050160123,
        0.01661462179301877
      ],
      "replicates": 10000,
      "seed": 941996
    },
    "eligible_recall@10": {
      "mean_difference": 0.00469984117056396,
      "paired_bootstrap_95pct": [
        -0.010315795429637604,
        0.02121337364460655
      ],
      "replicates": 10000,
      "seed": 941996
    },
    "eligible_recall@200": {
      "mean_difference": 0.07468394534506739,
      "paired_bootstrap_95pct": [
        0.019868915844487293,
        0.1359216580341235
      ],
      "replicates": 10000,
      "seed": 941996
    }
  },
  "baseline_blend_0.5": {
    "ndcg@10": {
      "mean_difference": 0.0038527277125587345,
      "paired_bootstrap_95pct": [
        -0.04376420371851075,
        0.0497410934917027
      ],
      "replicates": 10000,
      "seed": 941996
    },
    "eligible_recall@10": {
      "mean_difference": -0.0019176945862928957,
      "paired_bootstrap_95pct": [
        -0.011849664302446786,
        0.006810629086428626
      ],
      "replicates": 10000,
      "seed": 941996
    },
    "eligible_recall@200": {
      "mean_difference": 0.0,
      "paired_bootstrap_95pct": [
        0.0,
        0.0
      ],
      "replicates": 10000,
      "seed": 941996
    }
  }
}
```

## Development results: 45 patients

| Arm | nDCG@10 | Eligible recall@10 |
|---|---:|---:|
| baseline | 0.4457 | 3.87% |
| baseline_ce | 0.3019 | 2.83% |
| baseline_blend_0.5 | 0.4321 | 3.56% |
| baseline_blend_1 | 0.3940 | 3.17% |
| baseline_blend_2 | 0.3494 | 2.70% |
| lexical_rrf | 0.3939 | 4.36% |
| lexical_rrf_ce | 0.2031 | 1.51% |
| lexical_rrf_blend_0.5 | 0.3814 | 3.30% |
| lexical_rrf_blend_1 | 0.3289 | 2.76% |
| lexical_rrf_blend_2 | 0.2747 | 2.08% |

## Cost and verification

Scored 23,493 distinct note–trial pairs across 75 patients. Median scoring time for the union of both candidate pools: 37.2 seconds on this machine while other checks ran; this is not an Azure latency benchmark. No paid inference APIs or LLM calls were used.

Every saved per-patient evaluation was independently checked against pytrec_eval. Reranking preserves each candidate set exactly, so recall@200 is unchanged within a pool. Scores and rankings are saved for audit. Model files and trial data are excluded from the package.

The FastAPI/frontend demo supports all three methods, source criteria, registry links, optional demo authentication, input bounds, and one active search per process. Original BM25 remains the default. Docker and Azure readiness still require checks listed in the demo documentation.

## Next quality stage

Use a biomedical reranker as a new development experiment, then add patient facts, atomic criteria, explicit negation/missing values, and evidence-backed met/not_met/unknown judgments. Keep the current retrieval baseline available for comparison. Freeze the final pipeline before evaluating on TREC 2022.


## Balanced lexical fusion follow-up

A separate fixed comparison blended original BM25 with the three-query lexical fusion ranking. Both inputs have depth 1,000; RRF constant is 60. Original BM25 weight stays 1, and fusion weights 0.25, 0.5, 1 and 2 were compared. The development selection rule maximizes recall@200 while requiring development nDCG@10 to stay within 0.005 of original BM25. The selected weight was frozen before scoring the reused internal validation set. This is not cross-encoder reranking.

Selected arm: `balance_2`. The development quality gate does not guarantee the same bound on validation.

| Method | Validation nDCG@10 | Eligible recall@10 | Eligible recall@200 | Eligible in top 10, mean |
|---|---:|---:|---:|---:|
| baseline | 0.4701 | 6.26% | 30.27% | 2.77 |
| lexical_rrf | 0.4151 | 6.73% | 37.74% | 2.60 |
| balance_2 | 0.4484 | 6.97% | 39.82% | 2.83 |

The balanced option improves candidate coverage and recovers some top-ten quality versus the earlier equal three-query fusion. With candidate recall as the priority, this is a useful overall retrieval trade-off and the recommended candidate generator for the next reranking/eligibility experiment. Its observed validation nDCG@10 remains below original BM25, so it is not a win on every metric. The demo retains original BM25 as its initial comparison view and exposes balanced fusion as a research option. Full per-topic results and paired bootstrap differences are in the `balanced_fusion/` report directory.
