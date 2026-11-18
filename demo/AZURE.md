# Low-cost Azure recruiter demo

The prepared deployment uses **Azure Container Apps Consumption**, one model-free app, and a separate versioned recruiting-index image. No Azure resources have been created by this preparation. The template compiles locally; successful Azure provisioning, cold start, and hosted search still need testing.

## Cost choices

- App allocation: 0.5 vCPU / 1 GiB, one worker; minimum 0 and maximum 1 replica. The small init container copies and checks the index before app startup.
- A public GHCR image avoids Azure Container Registry's fixed service cost. Publish only the app and recruiting index, not full local registry catalogs or patient notes.
- The init container uses replica-local `EmptyDir` storage. No Azure Files, managed database, GPU, private network, custom domain, or Log Analytics workspace is provisioned.
- Scale-to-zero loses this ephemeral index copy. Each new replica may pull images and recopy/check the index. Expect a cold start; keeping one replica warm costs more. Local Docker instead retains its named volume.
- One active search and a global 10/minute process limit reduce load. They do not prevent all unwanted traffic or enforce a spending cap. Optional Basic authentication is available for a private demo.

Container Apps' monthly subscription-level grants currently cover 180,000 vCPU-seconds, 360,000 GiB-seconds and 2 million requests. Usage above these grants, other resources, and data transfer can cost money. A quiet demo may fit the grants, but there is no guaranteed zero-cost estimate. Configure a small resource-group budget alert (for example EUR 5), check Cost Analysis after launch, and review after a week. A budget sends alerts; it does **not** automatically stop spending. See [official billing](https://learn.microsoft.com/en-us/azure/container-apps/billing), [storage limits](https://learn.microsoft.com/en-us/azure/container-apps/storage-mounts), and [log destinations](https://learn.microsoft.com/en-us/azure/container-apps/log-options).

## Publish images when ready

From the project root after local Docker verification. Replace `YOUR_GITHUB_USERNAME` with the account that owns your packages; publishing changes a remote service and is a manual next step.

```powershell
$owner = 'YOUR_GITHUB_USERNAME'
$m = Get-Content .\data\manifests\ctgov-current.json -Raw | ConvertFrom-Json
$tag = "$($m.dataset_id)-$($m.sha256.Substring(0,12))"
docker login ghcr.io -u $owner
docker tag clinical-trial-matcher:local "ghcr.io/$owner/clinical-trial-matcher:recruiter-v1"
docker tag "clinical-trial-index:$tag" "ghcr.io/$owner/clinical-trial-index:$tag"
docker push "ghcr.io/$owner/clinical-trial-matcher:recruiter-v1"
docker push "ghcr.io/$owner/clinical-trial-index:$tag"
```

Make both GHCR packages public. Record the registry's pushed `sha256` image digests; the index file checksum in the manifest is a different value from the image digest. The template accepts digest-pinned references such as `ghcr.io/account/clinical-trial-matcher@sha256:...`. Test unauthenticated image pulls before deploying. Azure Container Apps needs `linux/amd64` images; ARM machines must build for that platform.

## Review, then deploy

The template `azure/main.bicep` creates only a Container Apps environment and app, with HTTPS, health probes, zero-to-one scaling, and replica-local index storage. It uses publicly accessible images and creates no image registry. `reviewOn` is a reminder tag, not scheduled deletion. Pick a supported region with Consumption quota.

```powershell
az login
$group = 'rg-clinical-trial-demo'
az group create --name $group --location westeurope
$appImage = 'ghcr.io/YOUR_GITHUB_USERNAME/clinical-trial-matcher@sha256:APP_IMAGE_DIGEST'
$indexImage = 'ghcr.io/YOUR_GITHUB_USERNAME/clinical-trial-index@sha256:INDEX_IMAGE_DIGEST'
$m = Get-Content .\data\manifests\ctgov-current.json -Raw | ConvertFrom-Json
$review = (Get-Date).AddDays(30).ToString('yyyy-MM-dd')
az deployment group what-if --resource-group $group --template-file .\demo\azure\main.bicep --parameters appImage=$appImage indexImage=$indexImage indexSha256=$($m.sha256) reviewOn=$review
az deployment group create --resource-group $group --template-file .\demo\azure\main.bicep --parameters appImage=$appImage indexImage=$indexImage indexSha256=$($m.sha256) reviewOn=$review
```

For a private demo, supply `demoUsername` and `demoPassword` through a secure parameters file or approved secret injection, rather than typing the password into shell history. Leave both empty for a public recruiter demo. Authentication settings must be paired or the app refuses startup.

Create the budget alert in Azure Cost Management for this resource group. The commands above do not create one. Before sharing the URL, check readiness, all four examples, reported recruiting status/date, mobile layout, response time, scale-to-zero/cold start, and actual costs. Container images are still downloaded when replicas move or start; separate data layers avoid rebuilding the index on an app update, not all future network transfer.

For an app-only release, publish a new app digest and redeploy using that digest with the **same** index image/checksum. A new replica still needs its ephemeral index copy. For a data refresh, rebuild locally, publish a new versioned data image and deploy its new digest/checksum. Never relabel historical benchmark metrics as current-corpus results.

## Finish the demo

Delete the dedicated resource group after verifying its contents contain only this demo:

```powershell
az resource list --resource-group rg-clinical-trial-demo --output table
az group delete --name rg-clinical-trial-demo
```

The hosted URL then stops working. The same app/index images can still run locally with Docker. Retain screenshots, a short demo video and exact image versions for the portfolio.
