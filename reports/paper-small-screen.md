# Rejected small-model pipeline screen

2026-11-18. Five reused TREC 2021 development patients (1–5), all 375,580 trials.
No 2022 evaluation. These numbers are not comparable to the older 30-topic table.

| Pipeline | nDCG@10 | Eligible recall@10 | Eligible recall@200 |
|---|---:|---:|---:|
| Original balanced fusion | 0.4951 | 5.11% | 27.91% |
| T5-small generated-query retrieval | 0.4183 | 4.31% | 26.98% |
| New retrieval plus MiniLM-L2 passage ranking | 0.2843 | 3.10% | 26.98% |

The attempted adaptation used four generated queries, bounded RM3-style feedback,
RRF anchored to balanced fusion, and capped six-sentence passage scoring.
All metrics were independently checked against pytrec_eval. Median uncached
search was 136.4 seconds with one inference thread. A 20-second query timeout
was increased to 60 seconds for the final screen; all five then completed.

The result regressed. The user requested reverting to the original balanced-fusion
frontend and removing failed-experiment backups/code. The experimental Docker
build was cancelled before deployment. No experimental image was installed.
The original image/index were retained; the Compose matcher CPU quota is now one.

The restored original code passes 22 tests. MedCPT is a proposed future experiment,
not implemented or evaluated. Keep its candidate set fixed to isolate reranking;
that can change top-ten metrics but cannot change eligible recall@200.

Restored Docker smoke test: default method `fusion`, 200 candidates, ten results,
zero neural pairs scored, 11.135 seconds. Runtime CPU quota is one core; the
existing `clinical-trial-index-trec2021-b70ca9cdb12e` volume was reused.
