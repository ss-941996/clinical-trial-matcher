# Local Docker or an optional Azure demo

The default app uses balanced fusion with no neural-model runtime. It can run locally for screenshots, a recorded walkthrough, or a recruiter who downloads the images. An Azure subscription is optional.

| Option | What to provide | Ongoing hosting |
| --- | --- | --- |
| Local Docker | App image, recruiting-index image, Compose file | None when stopped |
| Recorded portfolio demo | Screenshots/video plus repository and reproducible commands | None |
| Azure live URL | Public app/index images, Container Apps Consumption, cost review | Usage-based; scale-to-zero, maximum one replica |

Start with [local commands](README.md) and [image export/load](INDEX-IMAGE.md). Data and image binaries are not stored in Git. For a live URL, follow [Azure preparation](AZURE.md). The template avoids paid model APIs, a managed database, Azure Files, Azure Container Registry and Log Analytics. It still needs hosted verification and does not guarantee zero cost.

Local Docker retains a versioned volume for the index. Azure uses replica-local ephemeral storage, so cold starts recopy/check the index. When Azure ends, its URL stops; the same images remain runnable on Docker locally. Keep image versions, screenshots and a short walkthrough.

All results are research candidates. Recruiting status is registry-reported as of the snapshot, and eligibility is unchecked. The latest recruiting corpus has no judged relevance evaluation; historical TREC metrics are documented separately.