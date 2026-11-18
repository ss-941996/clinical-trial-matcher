# MedCPT one-patient development pilot — 2026-11-18

**Mixed result; not promoted to the frontend.** This is TREC 2021 development
patient 1 only, not a new validation set or an independent holdout. No TREC 2022
inputs were used. These results cannot be compared directly with the earlier
30-patient development figures.

| Fixed-pool ranking | nDCG@10 | Eligible trials@10 | Eligible recall@10 | Eligible recall@200 |
|---|---:|---:|---:|---:|
| Balanced fusion | 0.4288 | 1 | 2.13% | 29.79% |
| MedCPT FP32 | 0.4165 | 2 | 4.26% | 29.79% |
| RRF: balanced fusion 1 + MedCPT 0.5 | 0.4208 | 0 | 0.00% | 29.79% |

The patient has 47 judged eligible trials; the fixed 200 contain 14 of them.
Pure MedCPT brings one more eligible trial into the top ten, while nDCG falls
by 0.0123. nDCG also credits grade-1 excluded trials and accounts for positions,
so these measures can move in different directions. The tested blend is worse
on both top-ten measures. One patient cannot establish aggregate improvement
or failure of MedCPT.

## Protocol and cost

Pinned `ncbi/MedCPT-Cross-Encoder`, revision
`71caf65d4927987813984f54c284405a13fcca49`, FP32 on CPU. Candidate hashes exactly
match the existing frozen balanced-fusion protocol. Retrieval and index
construction were not rerun. Existing text budgets were retained, with MedCPT's
own tokenizer: note 128, title 64, conditions 48, summary 112, eligibility 157,
plus special tokens, at most 512 tokens total.

Execution used one low-priority process, one PyTorch intra-op thread, one
inter-op thread, batches of two, tokenizer parallelism disabled and a 50 ms
pause per batch. No parallel experiments were run.

- 200 new FP32 pairs: **290.37 seconds** of cumulative batch scoring, including
  pauses, excluding checkpoint writes, retrieval, initialization and metrics.
- Model initialization: **22.30 seconds**. This is not an end-to-end app latency
  measurement. Recorded CPU seconds are in the patient JSON.
- A process sample during scoring showed about **0.8 GiB** working memory and
  Windows `BelowNormal` priority; this is an observation, not a guaranteed cap.
- A repeat run scored **zero new pairs**, produced identical metrics and kept
  the original scoring-time record. It did not load the model for inference.

An initial eight-pair oneDNN dynamic-INT8 comparison changed rank order and
showed maximum absolute logit drift of 3.7713. That run was stopped. Its partial
scores were not reused in this FP32 result. INT8 is not enabled in the runner.

## Verification and decision

Six score-coverage/ranking guard tests passed. Each ranking's metrics passed the
independent `pytrec_eval` checks. All three rankings contain the same 200 IDs.
Model/input/code checksums and runtime versions are recorded in
[the protocol](medcpt/protocol.json); raw scores and all ranked IDs are in
[the patient result](medcpt/topic-1.json), with
[the summary](medcpt/summary.json).

Keep the frontend on balanced fusion. This first screen does not justify
changing the default ranking, and roughly five minutes per patient is not an
interactive search latency. Do not tune several blend weights to this one
patient. A later broader screen should retain the frozen settings and compare
the same 2021 development patients sequentially before any promotion decision.
The reranker cannot improve recall@200 without changing the candidate pool.

Reproduction commands, dependencies, license provenance, checkpoint behavior
and resource limits are in
[the experiment guide](../experiments/reranking/MEDCPT.md). No Docker rebuild,
frontend switch, index rebuild, remote provisioning or new 2022 evaluation was
performed.

## Subsequent screen cancelled

A sequential expansion reused patient 1 and completed patient 2 before the user stopped MedCPT and requested the frozen balanced-fusion 2022 evaluation. Patient 3 was only partially scored; no five-patient aggregate was produced. Patient 2 raw results are in [its result file](medcpt/topic-2.json). The app never switched to MedCPT. The separate 2022 evaluation does not alter the scope of the 2021-only pilot above.
