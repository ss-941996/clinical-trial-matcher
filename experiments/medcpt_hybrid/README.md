> **Selected serving pipeline, 19 November 2026:** balanced lexical + MedCPT,
> equal-weight RRF, top 200, display top ten directly. No cross-encoder.
> See [serving code and commands](../../demo/HYBRID.md). The procedure below
> records the completed experiment, including the rejected reranking arm;
> `--stage cached` reproduces that historical experiment, not the serving flow.
> The recorded protocol hashes the pre-serving source files. After source
> changes, the runner deliberately refuses to overwrite that frozen run;
> a new experiment needs a new output directory and protocol. Serving uses
> `demo/serve_hybrid.py` and does not call this experiment runner.

# Cached MedCPT hybrid pilot: 5,120 trials, five patients

The user stopped the original 20,000-trial encoding job after 5,376 vectors
had been saved and requested an immediate test using exactly the first 5,120.
The extra 256 vectors are excluded. No further trial encoding is performed.
The earlier proposals for 10,000 trials and 45 patients were superseded.

This sorted-ID prefix of the original random sample favours earlier NCT IDs
and is not representative. There are only eight judged eligible trials in the
subset, for patients 1/4/5 respectively 4/1/3. Patients 2 and 3 have no judged
eligible trials in the subset: their recall is N/A and excluded from recall
means. nDCG still covers all five. This is a functional screening experiment,
not a full-corpus benchmark or independent holdout test.

## Pipeline

Patient note → balanced lexical fusion top 1,000 and MedCPT dense top 1,000 →
weighted RRF → 200 unique trials → quantized MiniLM-L2 scores all 200 → top ten.
All arms search the exact same 5,120 trials. Lexical FTS statistics are rebuilt
on this subset. Eligibility is not assessed; unjudged does not mean ineligible.
The serving frontend remains on its existing balanced-fusion configuration.

The four dense RRF weights are .25, .5, 1 and 2; lexical weight is 1 and RRF
constant 60. Select a hybrid by macro eligible recall@200, then nDCG@10 and name.
Rerank only the selected hybrid, on five patients. Candidate membership and
recall@200 must remain unchanged after reranking. No expansion is automatic.
Every per-patient nDCG and eligible recall is checked against pytrec_eval;
nDCG uses linear gains 0/1/2, unjudged zero; recall positives are grade 2.

## Models, representation and cache

NCBI MedCPT query/article encoders are public domain and pinned in run.py.
MiniLM-L2 is Apache-2.0, pinned in models/paper/manifest.json. Model cards,
revisions and file hashes are recorded in the protocol. ONNX encoders use
per-channel dynamic-int8 weights; outputs and exact dot products are float32.
Trial CLS vectors use title/conditions/summary/eligibility token budgets
48/48/96/61 plus three special tokens (256 total); patient queries use 128.
Truncation and PubMed-to-trial domain transfer are limitations. Model conversion
sanity checks are not retrieval accuracy tests.

Before reuse, the runner checks the parent model manifests, field packing,
batching and source-index checksum; each shard must match the selected IDs,
shape, finite values and source checksum. Existing batch composition is retained.
Data, model files, subset indexes and vector shards remain outside Git.

## Local reproduction using the existing cached vectors

Use Python 3.12 in a separate environment with requirements.txt. The historical
SQLite index, 2021 topics/judgments, model files and original first twenty cached
256-vector shards are prerequisites. The source cache is
`data/cache/medcpt-hybrid-pilot-v1/`; its scope and provenance remain recorded
under `reports/medcpt-hybrid-pilot-v1/`.

```powershell
& .\.venv-medcpt\Scripts\python.exe -m pytest .\experiments\medcpt_hybrid\test_run.py .\experiments\medcpt_hybrid\test_cpu_guard.py -q
& .\.venv-medcpt\Scripts\python.exe -m experiments.medcpt_hybrid.pilot --stage cached
```

Run tests before the experiment, never in parallel. The cached command verifies
and reuses vectors, retrieves for all five patient queries, then reranks all 200
candidates per patient. A complete historical run can be developed separately;
run.py's full-corpus encode command is not invoked by this pilot.

## Resources and transparent logging

The pilot sets BelowNormal priority, one inference/BLAS/tokenizer thread and
single-logical-CPU affinity before doing work. Current policy is continuous
execution: automatic pausing is disabled. The affinity remains across all
stages. On this 14-logical-CPU machine the computation uses at most one logical
CPU, approximately 7.1% of overall capacity; measured usage is logged.

An optional observer emits checkpoint/stage/metric events without loading a model:

```powershell
& .\.venv-medcpt\Scripts\python.exe -m experiments.medcpt_hybrid.watch_pilot
```

The Windows CPU guard accepts explicitly verified worker PIDs with `--pids`.
It verifies the repository/module command line, samples liveness, CPU time and
measured experiment CPU every five seconds, and emits a heartbeat every thirty.
Its optional `--auto-pause` behaviour is not used. Other applications are not
changed. The two threshold unit checks cover that optional behaviour.

Current output: `reports/medcpt-hybrid-pilot-5120-v1/`.

```powershell
Get-Content .\reports\medcpt-hybrid-pilot-5120-v1\live.log -Tail 20 -Wait
```

`live.log` contains human-readable stage, metric and heartbeat messages;
`live.jsonl` contains structured observer events. `guardrail-samples.jsonl`
records sampled CPU/liveness and `guardrail-state.json` the latest observation.
Timestamps are explicit UTC. `protocol.json`, `sample-ids.json`, rankings,
judgment coverage, per-patient metrics and query timings make results auditable.
The final README table is generated after all five reranking jobs complete.

Sources: [MedCPT](https://github.com/ncbi/MedCPT),
[article encoder](https://huggingface.co/ncbi/MedCPT-Article-Encoder),
[MiniLM-L2](https://huggingface.co/cross-encoder/ms-marco-MiniLM-L2-v2).
