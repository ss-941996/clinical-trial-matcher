# MedCPT: sequential offline pilot

This experiment keeps the 200 balanced-fusion candidates fixed and compares
their existing order with NCBI MedCPT cross-encoder scores, plus an RRF blend
(balanced-fusion weight 1, MedCPT weight 0.5, RRF constant 60). It does not change
the frontend or default Docker image. Only the frozen TREC 2021 development
topics are accepted; no 2022 inputs are opened.

The [official model](https://huggingface.co/ncbi/MedCPT-Cross-Encoder) is pinned
to revision `71caf65d4927987813984f54c284405a13fcca49`. Its card declares public
domain. The downloader records file checksums and saves the
[NCBI public-domain notice](https://github.com/ncbi/MedCPT/blob/main/LICENSE);
the checkpoint's own LICENSE file is empty. This biomedical relevance model
was trained on PubMed query/article pairs, not patient/trial eligibility labels.

## Run from the repository root

Use an isolated Python 3.12 environment; the experimental PyTorch dependencies
are separate from the app. The CPU wheel source below targets supported CPU
platforms and does not configure a GPU.

```powershell
py -3.12 -m venv .venv-medcpt
& .\.venv-medcpt\Scripts\python.exe -m pip install -r .\experiments\reranking\requirements-medcpt.txt
& .\.venv-medcpt\Scripts\python.exe -m experiments.reranking.medcpt_screen --download
& .\.venv-medcpt\Scripts\python.exe -m pytest .\experiments\reranking\test_medcpt_screen.py -q
& .\.venv-medcpt\Scripts\python.exe -m experiments.reranking.medcpt_screen --topics 1
```

Prerequisites: the existing full `data/indexes/bm25.sqlite`, 2021 topics and
judgments, frozen retrieval caches under `experiments/retrieval_inputs/results`,
and `reports/reranking/balanced_reranker/protocol.json`. No index rebuild is
needed. Inference loads local model files only after the explicit download.

The runner lowers process priority, sets PyTorch intra/inter-op threads to one,
disables tokenizer parallelism, scores batches of two in FP32, and pauses 50 ms
between batches. These settings reduce competition for CPU; they do not promise
zero CPU use. It writes an atomic checkpoint after each batch, with cache keys
covering model files, runtime versions, code, input text and candidate IDs.
Rerun the same command to resume. A completed cache rerun preserves recorded
scoring time rather than reporting cache lookup as inference latency.

Protocol, patient-level rankings, raw scores and metric results are in
`reports/medcpt/`. Model weights and score checkpoints stay in ignored
`models/medcpt/` and `data/cache/medcpt/`. Every metric is checked against
`pytrec_eval`; all arms must contain exactly the same 200 candidate IDs.

The note is limited to 128 WordPiece tokens. Per-field budgets are title 64,
conditions 48, summary 112 and eligibility 157, plus three special tokens, for
at most 512 tokens. These match the earlier cross-encoder's field budgets,
although MedCPT uses its own vocabulary. This screen does not implement passage
selection or change retrieval. Because the pool is fixed, recall@200 cannot
improve through this reranker.

An initial eight-pair dynamic-INT8 probe using this Windows build's oneDNN
backend changed pair order and had a maximum absolute logit difference of
3.7713. The INT8 run was stopped. Reported quality uses FP32 only; INT8 is not
assumed equivalent or enabled in the runner.

Docker packaging alone would still use the host's CPU. The development laptop
reported Intel Graphics, with no NVIDIA GPU detected. A remote execution host
would be needed to move this experiment's CPU work off the laptop. No remote
resources were provisioned.
