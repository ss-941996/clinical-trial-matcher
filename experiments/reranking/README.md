# Reproduce reranking

Install the existing root project dependencies, then `requirements-experiment.txt`. Run from this directory:

```powershell
..\..\.venv\Scripts\python.exe evaluate_reranker.py --project ../.. --retrieval-results ../retrieval_inputs/results --output ../../../trial-reranking-reproduction --deps ../../.venv/Lib/site-packages
```

The supplied original baseline/fusion candidate caches and frozen split reproduce candidate sets. The script downloads the pinned model and scores note–trial pairs on CPU. It writes only to the chosen output, a sibling folder outside the repository because this runner treats `--project` as read-only. Original experiment protocol code hashes are recorded in reports.
