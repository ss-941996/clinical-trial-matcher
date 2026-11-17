# Balanced-fusion reranking: incomplete, deferred

The 2026-11-17 experiment was stopped at the user's request. The release retains the existing app ranking choices; this experiment does not change its defaults.

- Development metric checkpoint: **14 of 45 patients**. Other scoring processes saved additional inference-only caches; there are 26 topic cache files locally.
- No development winner was frozen and no validation comparison was completed. Partial averages must not be presented as the completed benchmark.
- Candidate IDs were fixed to balanced fusion's top 200. All completed metric rows preserved their candidate set and were independently checked with pytrec_eval.
- Only TREC 2021 inputs were used. The existing 2022 cancellation/exposure record remains authoritative.
- Cached model scores live in ignored `data/cache/balanced_reranker/`; they are not included in Git. Protocol and partial metrics are retained as an explicit progress record.

The optional `score_balanced_shard.py` helper was used to precompute independent patients. Concurrent scoring caused substantial resource contention and is not recommended on this laptop. Recorded score timings include that workload and are not representative app latency. Prefer a single scoring process for any later resume.

To resume later from the repository root, after installing the project evaluation and demo dependencies into the same Python environment and downloading the pinned model:

```powershell
python experiments/reranking/evaluate_balanced_reranker.py --model .\demo\models
```

The model path must contain `tokenizer.json` and `onnx/model_quint8_avx2.onnx` at the pinned revision. Use the same frozen protocol and scoring code to reuse existing caches. The runner rejects changed protocols rather than silently mixing experiments. A future completed run should replace this status record only after reporting all prespecified results. No evaluation is started by these instructions.
