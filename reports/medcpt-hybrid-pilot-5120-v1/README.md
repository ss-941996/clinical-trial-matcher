# MedCPT hybrid pilot results

Exploratory: five reused 2021 development patients, 5,120-trial sorted prefix of the original 20,000-trial random sample.
This user-capped subset favours earlier trial IDs; it is not representative. Only eight judged eligible trials remain, across three patients.
These scores cannot be compared directly with the earlier full-corpus benchmark. Eligibility is not assessed.
All recall denominators use judged eligible trials present in the subset. No 2022 data accessed.

| Configuration | nDCG@10 | Eligible recall@10 | Eligible recall@200 | Eligible precision@10 |
|---|---:|---:|---:|---:|
| balanced_fusion | 0.2093 | 19.44% | 55.56% | 4.00% |
| medcpt_dense | 0.0991 | 19.44% | 88.89% | 4.00% |
| hybrid_0.25 | 0.2896 | 19.44% | 55.56% | 4.00% |
| hybrid_0.5 | 0.2995 | 27.78% | 88.89% | 6.00% |
| hybrid_1 | 0.3030 | 27.78% | 88.89% | 6.00% |
| hybrid_2 | 0.2803 | 19.44% | 88.89% | 4.00% |
| hybrid_1 + MiniLM-L2 | 0.0241 | 0.00% | 88.89% | 0.00% |

Selected hybrid: hybrid_1. Prespecified expansion gate passed: False.
Weight selection and reporting use the same five patients; this is a screening result, not an independent estimate.
See protocol.json, judgment-coverage.json, encoding-progress.json and per-patient result JSON files for provenance, coverage, timing and memory.
