# Local use and a low-cost portfolio demo

The app can run entirely on a Windows laptop. FastAPI serves the frontend and
search API together; SQLite holds the trial index and the optional ONNX model
runs locally. No Azure resource, paid model endpoint, PostgreSQL service, or GPU
is required. Local use has no hosting/API bill; ordinary hardware, electricity
and internet costs still apply.

## Run locally

From the repository root in PowerShell, with Python 3.12 and the existing
`data/indexes/bm25.sqlite` index:

```powershell
py -3.12 -m venv demo/.venv
& ./demo/.venv/Scripts/python.exe -m pip install -r demo/requirements-demo.txt
$env:TRIAL_INDEX_PATH = (Resolve-Path './data/indexes/bm25.sqlite').Path
Remove-Item Env:RERANK_MODEL_DIR -ErrorAction SilentlyContinue
Set-Location demo
& ./.venv/Scripts/python.exe serve.py
```

Open **http://127.0.0.1:8000**. This is a local address, accessible on this
computer. Choose lexical fusion for balanced-fusion retrieval. BM25 is the
initial comparison view. Reranking is unavailable until its model is installed.
Stop the server with Ctrl+C. The separate demo environment avoids changing the
baseline experiment's environment.

For optional reranking, stop the server, then from `demo/`:

```powershell
& ./.venv/Scripts/python.exe download_model.py --output models
$env:RERANK_MODEL_DIR = (Resolve-Path './models').Path
& ./.venv/Scripts/python.exe serve.py
```

Model download requires internet during setup. Search subsequently runs locally.
See [README](README.md) for Docker Compose commands. Docker build/run has not yet
been verified; the Python API and browser workflow were tested locally.

## Cheapest portfolio presentation

1. Keep the working app and full index locally for development.
2. Put screenshots, a short recorded walkthrough, benchmark results and exact
   local run instructions in the portfolio/README. This has no app-server bill.
3. Add a live backend only when ready to demonstrate it. A static portfolio page
   can show the video, but cannot run the existing Python backend or local index.

## Optional live HTTPS URL

Deploy the tested image to **Azure Container Apps Consumption**, exposing port
8000 through HTTPS ingress. Azure assigns an application address; a custom domain
is optional. Keep frontend and API together in one container.

- Set HTTP scaling to minimum 0, maximum 1. Cold starts are expected; restoring
  the 2.22 GB index and loading the model adds startup time. Confirm the first
  request works; show a walkthrough video alongside the live link.
- Start with the documented 2 vCPU/4 GiB benchmark allocation and reduce only after
  measuring memory and latency. A small allocation that repeatedly fails is not
  a useful saving. Keep reranking optional and limit concurrent searches.
- Keep one versioned index in private storage and one current image. Compare a
  free registry allowance with Azure Container Registry charges before choosing.
- Retain minimal logs, omit patient-note bodies, and use synthetic research notes.
- Use authentication for an invited demo; provide access instructions separately.
  A public demo needs additional abuse/rate controls before sharing widely.
- Set budget alerts and check actual spending. Alerts are notifications, not a
  hard spending cap. Storage, registry and logging may charge while compute sleeps.
- Delete the dedicated PoC resources after the demo period, once local copies
  of the image/model/index are confirmed. No Azure resources have been deployed.

An index startup-download/checksum path is a proposed improvement and is not
implemented. Current code supports a local path/read-only mount. Benchmark any
Azure Files mount before relying on its SQLite search performance.

The live URL demonstrates deployment skills and makes review convenient. It does
not replace measured retrieval results, reproducibility, or clinical validation.

Local Docker is the primary delivery path for this PoC. Azure hosting is optional.
Other users need Docker plus compatible copies of the image, index and model;
the image alone does not contain the mounted trial database. A local localhost
address is not a public portfolio URL. Keep screenshots and a recorded demo on
a stable portfolio page so reviewers can inspect the project without installing it.

References checked 2026-11-16:
[scaling](https://learn.microsoft.com/en-us/azure/container-apps/scale-app),
[pricing/free allowances](https://azure.microsoft.com/en-us/pricing/details/container-apps/),
[budgets](https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/tutorial-acm-create-budgets),
[storage](https://learn.microsoft.com/en-us/azure/container-apps/storage-mounts).
