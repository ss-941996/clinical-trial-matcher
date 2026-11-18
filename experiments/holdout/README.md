# Frozen TREC 2022 evaluation

The deployed/local frontend remains on balanced fusion. This runner evaluates
that exact retrieval implementation with its existing outer RRF weight 2.0,
and the original BM25 control, on all 50 TREC 2022 patient topics against the
375,580-trial 2021-04-27 snapshot. No neural reranker runs.

Official inputs: [track description](https://www.trec-cds.org/2022.html) and
[NIST downloads](https://trec.nist.gov/data/trials2022.html). The same trial
corpus is used in 2021 and 2022, so no new index build is needed. Downloaded
topics/judgments and URL/checksum receipts are ignored under `data/raw/trec2022`.

From the repository root, with the root project dependencies installed:

```powershell
# Only when creating a new freeze; refuses to overwrite an existing freeze.
& .\.venv\Scripts\python.exe -m experiments.holdout.evaluate_2022 --stage freeze
# Generates all rankings without opening qrels. Reuses compatible checkpoints.
& .\.venv\Scripts\python.exe -m experiments.holdout.evaluate_2022 --stage rank
# Requires all 50 ranking files before downloading/loading judgments.
& .\.venv\Scripts\python.exe -m experiments.holdout.evaluate_2022 --stage evaluate
```

The freeze records code, vocabulary, configuration and full index SHA256 values.
The rank/evaluate stages reject changed source/config files or incompatible
checkpoints. Both arms use 200 candidates and exactly the same 50 patients.
Balanced fusion's ranking is its final displayed order. BM25 has its own pool;
no candidate eligibility filtering, recruitment-status filtering or neural
ranking is introduced.

One low-priority process runs searches one at a time, fusion then BM25 per
patient. A checkpoint is saved after each complete patient, with a 250 ms pause
before the next. Library thread environment variables are set to one. Timing
includes the API engine's result hydration; BM25 runs after fusion and may
benefit from warmed caches. This is local runtime, not a cold-start or cloud
load test.

Outputs are in `reports/trec2022-balanced-fusion-v1`: freeze, full ranked IDs and
native scores, per-patient metrics, macro metrics and TREC run exports. Linear
nDCG gains are 0/1/2; unjudged trials receive zero gain. Recall positives are
grade 2 only, with zero-positive topics excluded from macro recall. Every
patient/arm's nDCG and recall is independently checked against `pytrec_eval`.

Prior exposure is documented in `reports/trec2022-status.md`: topics and 13
candidate rankings were accessed during the earlier cancelled run. No local
2022 qrels or computed metrics were used to select this version. This is not a
claim that no 2022 material was ever seen. After this evaluation, rerunning
2022 is a regression check; later versions need a new independent final test.

The prespecified paired-bootstrap analysis can be reproduced after evaluation:

```powershell
$env:OMP_NUM_THREADS='1'
$env:OPENBLAS_NUM_THREADS='1'
$env:MKL_NUM_THREADS='1'
& .\.venv\Scripts\python.exe .\experiments\holdout\summarize_2022.py .
```

Its seed, number of resamples and source checksum are recorded in
`reports/trec2022-balanced-fusion-v1/uncertainty-plan.json`, written before
judgments were downloaded. Runtime versions and latency percentiles are recorded
alongside the metrics. The track-site topic URL returned HTTP 406, so the final
freeze uses NIST's official topic download instead; this occurred before any
new rankings or judgments were generated.
