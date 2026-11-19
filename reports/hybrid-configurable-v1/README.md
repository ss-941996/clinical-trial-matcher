# Step 1: configurable hybrid pipeline — 19 November 2026

Completed locally. No new trial encoding, dataset download or Azure compute.

- Bundle building accepts a JSON spec for source index, vector cache, protocol,
  query model, collection metadata and output directory. Paths follow the spec.
- Record count and embedding dimensions are derived from validated inputs.
  Query length and model identity follow the pinned encoding protocol.
- Source checksum, protocol checksum, vector files and lexical/vector IDs must
  agree. Failed builds do not publish a partial bundle; existing outputs are
  never overwritten. Index tables and registry metadata are preserved.
- Runtime accepts `TRIAL_CONFIG_PATH` or `TRIAL_HYBRID_BUNDLE`. Collection changes
  do not require application code edits. Custom file paths follow their file;
  environment overrides take precedence. Invalid fusion weights are rejected.
- Frontend retains one search action and reads collection information dynamically.
  Selected defaults remain 1000 lexical + 1000 semantic → equal-weight RRF →
  200 candidates → top10, without a cross-encoder.

Verification completed sequentially:

- **25 tests passed in 4.68 seconds**, including synthetic 2/7-trial collections,
  changed-source and wrong-ID rejection, partial-copy failure, configuration
  precedence, and existing API/authentication/request-limit checks.
- The general builder packaged the **existing 5,120 cached trial vectors** at
  `data/indexes/medcpt-pilot-portable-v1`; the earlier serving bundle is unchanged.
- All five patients exactly reproduced the saved hybrid top ten through the API
  using this new bundle. Every query kept 200 candidates and used no reranker.
  Observed times were **1.111–1.504 seconds** under the single-core launcher.
- `git diff --check` passed. Measured details are in verification.json.

The current-data 20,000-trial collection is **not prepared or encoded yet**.
Pilot quality remains a small historical screening result, not a current-data
evaluation. Large data/model assets and local environments remain outside Git.

See [configuration and commands](../../demo/HYBRID.md). Steps 2–6 are documented
in the main README roadmap and remain pending.
