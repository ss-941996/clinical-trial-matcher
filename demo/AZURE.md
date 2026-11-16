# Azure PoC deployment plan

The image and API are prepared locally. No Azure resources have been created or changed, and no cloud deployment or Docker build was verified in this session: access to the Docker daemon and Azure account was denied by the session's permission policy.

## Proposed first deployment

Use Azure Container Apps with HTTPS ingress to port 8000, HTTP scaling with minimum zero and maximum one replica, one worker, initially 2 vCPU / 4 GiB RAM. This saves idle compute at the cost of cold starts; validate startup time before sharing. The ONNX runtime requests up to four threads, but a 2-vCPU allocation may run more slowly; benchmark before sharing. Use a private registry or the project's chosen image registry and keep credentials in platform secrets. Choose the region and subscription after checking the available Azure account and budget.

The index stays separate from the image. Container Apps supports classic Azure Files mounts. Upload a closed, verified copy of `bm25.sqlite` to a dedicated file share; attach the share to the Container Apps environment and mount it read-only at `/data`. The app opens SQLite in read-only mode. Do not run an index writer against this file while the app is running. Benchmark cold/warm reads on the share: remote SQLite reads can be materially slower than local disk. If latency is poor, copy a versioned index to local ephemeral storage during startup (with a checksum) before marking the app ready. That startup-copy path is not implemented in this version.

Set these environment variables:

| Variable | Value |
|---|---|
| `TRIAL_INDEX_PATH` | `/data/bm25.sqlite` |
| `RERANK_MODEL_DIR` | `/app/models` |
| `HOST` | `0.0.0.0` |
| `PORT` | `8000` |
| `REQUIRE_AUTH` | `true` |
| `DEMO_USERNAME`, `DEMO_PASSWORD` | Platform secret references |

Set startup/readiness probes to `/health/ready` and liveness to `/health/live`, HTTP port 8000. Allow model loading and index checks before readiness. Enable HTTPS only. Keep maximum replicas at one initially so the process-level single-search gate has the intended scope. The default Container Apps HTTP request timeout is 240 seconds; measure full retrieval plus reranking latency under the actual CPU/storage allocation. If requests approach that limit, switch to asynchronous jobs before extending usage.

## Go/no-go checks before sharing

TREC 2022 evaluation is deferred at the user's request until development on 2021 is complete. See [status and limited prior exposure](../reports/trec2022-status.md). A synthetic-note engineering PoC can proceed while retrieval development continues. Freeze settings before a future independent evaluation; repeated 2022 tests after that are regression checks.

1. Run `docker compose config`, build, health check, synthetic BM25/fusion/rerank requests, and restart locally. Confirm the mounted index is unchanged.
2. Push that tested image and record its digest. Deploy with required authentication, HTTPS, platform secrets, a budget alert and an expiry reminder.
3. Check unauthenticated UI/API requests return 401; authenticated synthetic requests return ten candidates and unassessed eligibility. Confirm the second overlapping request returns 429.
4. Check cold start, warm latency, memory, restart behavior, storage access and probe health on Azure. Confirm notes are absent from infrastructure logs. Do not use live patient data for this PoC.
5. Save the image tag/digest, index checksum, model manifest and local Compose instructions before the Azure period ends.

## End of Azure access

Keep source, the versioned index and model/image available locally. Run the same Compose package on another x86-64 Docker host. Azure identity, registry access and storage mounts are hosting settings; they are not needed for local inference. Before Azure access expires, remove the PoC's resources after checking the resource group contains only this project. Stopping a container does not necessarily stop storage/registry charges. No cleanup command is executed by this package.

References: [Container Apps storage mounts](https://learn.microsoft.com/en-us/azure/container-apps/storage-mounts), [HTTP ingress and timeout](https://learn.microsoft.com/en-us/azure/container-apps/ingress), [health probes](https://learn.microsoft.com/en-us/azure/container-apps/health-probes).

See [local use and low-cost portfolio options](POC-OPTIONS.md). A budget alert does not stop charges. A cloud URL stops serving the app when its resources are stopped or deleted; retain a stable portfolio page and local Docker package.
