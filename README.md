# Clinical Trial Matcher: patient note → ranked trials

**Status:** The local BM25 research baseline is implemented. The full staged matcher below is the target architecture.

A staged pipeline that takes a free-text patient description, cleans it, extracts structured eligibility facts, narrows a large registry of clinical trials down to a short list, checks eligibility criterion by criterion, and returns an explainable top-10 ranking. A clinician or trial coordinator makes the final decision.

Scored with **nDCG@10** against graded relevance judgments (0 = not relevant, 1 = excluded, 2 = eligible).

> Research and portfolio project. Not a medical device and not for real patient decisions.

---

## 1. Problem

Given:
- a **patient description** (a synthetic admission-note-style free text)
- a **pool of clinical trials** (long registry records: title, summary, conditions, interventions, and inclusion/exclusion criteria written as prose)

Produce, per patient, an ordered list of trial IDs, best first. The primary metric scores the top 10; recall also measures candidate coverage at 30 and 200.

Why it is hard:
- Trial documents are long, and eligibility rules are buried in free text.
- Medical abbreviations, negation ("no history of…"), and numeric thresholds ("eGFR > 30").
- Patient notes omit most of what a trial asks about, so missing ≠ ineligible.
- "Relevant but excluded" must rank below "eligible".

## 2. Design principle

**Understand the data first, spend LLM calls last.**
Cheap stages shrink the pool; the expensive LLM stage only sees the survivors. Every LLM and embedding call is cached by content hash.

## 3. Pipeline

| # | Stage | Method | Output |
|---|-------|--------|--------|
| 0 | **Profile the data** | length distributions, null/empty fields, abbreviation frequency, criteria-section detection rate | `reports/data_profile.md` |
| 1 | **Clean and normalize** | whitespace/HTML cleanup, split each trial into sections (summary, conditions, inclusion, exclusion), normalize units | `data/clean/trials.parquet` |
| 2 | **Parse the patient** | LLM → structured JSON (schema below), cached | `data/cache/patients/` |
| 3 | **Parse trial criteria** | split inclusion/exclusion into atomic criteria, tag each (condition, age, lab, medication, procedure, other), cached | `data/cache/criteria/` |
| 4 | **Retrieve** | hybrid: BM25 over trial text + biomedical/multilingual-agnostic embedding of patient vs. trial; fuse with reciprocal rank fusion; keep top-200 | candidate trials |
| 5 | **Hard filter** | rule-based on age, sex, condition mismatch; **unknown ≠ fail** | filtered pool |
| 6 | **Criterion-level matching** | LLM judges each (patient, criterion) as `met / not_met / unknown` with a one-line reason, only on top-30 | per-criterion verdicts |
| 7 | **Aggregate and rank** | score = weighted met inclusion minus violated exclusion, `unknown` penalized lightly; ties broken by retrieval score | top-10 |
| 8 | **Explain** | per-trial summary: which criteria matched, which are unknown, which exclude | shortlist JSON |
| 9 | **Evaluate** | nDCG@10, recall@10, and an eligible-vs-excluded confusion check | `reports/eval.md` |

### Patient schema (stage 2)

```json
{
  "patient_id": "P-001",
  "age": 54,
  "sex": "female",
  "conditions": ["type 2 diabetes", "hypertension"],
  "medications": ["metformin", "lisinopril"],
  "labs": [{"name": "HbA1c", "value": 8.1, "unit": "%"}],
  "history": ["no prior stroke"],
  "negations": ["no known drug allergies"],
  "unknowns": ["smoking status", "renal function"]
}
```

Store negations and unknowns explicitly. They drive stages 5 and 6.

## 4. Orchestration

Wire stages 2–8 as a **LangGraph** workflow (one node per stage; state = patient, candidate trials, verdicts, scores) so a run can resume from any node. Build and validate each stage as a plain function first; add the graph last.

## 5. Evaluation

- **Metric:** nDCG@10 (primary), recall@10, plus a check that "excluded" trials do not outrank "eligible" ones.
- **Labels:** graded judgments 0 / 1 / 2 from the TREC Clinical Trials tracks. Some evaluations keep only the 1 and 2 pairs (eligible vs. excluded); decide your convention up front and keep it fixed.
- **Splits:** tune on one year, report on another (for example tune on 2021, report on 2022). Never tune on the set you report.
- **Ablation ladder, in order:** (1) BM25 only, (2) embeddings only, (3) hybrid, (4) hybrid + hard filter, (5) + criterion-level LLM matching, (6) + aggregation tuning. Log every run so the results table writes itself.

## 6. Data

| Source | What it gives you |
|--------|-------------------|
| **TREC Clinical Trials 2021** | 75 patient topics, about 350K trial descriptions, graded judgments |
| **TREC Clinical Trials 2022** | same collection, 50 new topics; 2021 judgments usable for tuning |
| **TREC Clinical Trials 2023** | 40 topics, 450K+ trials (2023 ClinicalTrials.gov snapshot) |
| **SIGIR 2016 patient-trial cohort** | smaller test collection, useful for fast iteration |
| **TrialGPT repo** (`github.com/ncbi-nlp/TrialGPT`) | parsed trial data download, links to the TREC corpora, reference code for retrieval / matching / ranking |

Notes:
- Patient topics are **synthetic** cases written by people with medical training, so there is no real patient data.
- Check the access steps and terms for each TREC collection yourself before relying on them; I have not verified them.
- English only.
- Develop on a slice (for example 10 patients and the trials judged for them) before touching the full corpus.

## 7. Suggested repo layout

```
clinical-trial-matcher/
├── README.md
├── data/
│   ├── raw/            # untouched downloads (gitignored)
│   ├── clean/          # parquet after stages 0–1
│   └── cache/          # LLM + embedding caches keyed by content hash
├── notebooks/
│   └── 00_profile_data.ipynb
├── src/
│   ├── clean.py
│   ├── parse_patient.py
│   ├── parse_criteria.py
│   ├── retrieve.py         # BM25 + embeddings + RRF
│   ├── filters.py
│   ├── match_criteria.py   # LLM met / not_met / unknown
│   ├── aggregate.py
│   ├── explain.py
│   ├── evaluate.py         # nDCG@10, recall@10
│   └── graph.py            # LangGraph wiring
├── api/                    # FastAPI: POST /match
├── reports/
└── docker-compose.yml
```

## 8. Cost and safety rules

- Cache every LLM and embedding call by hash of (model, prompt, input).
- Print a call and token estimate before any batch run; require a flag to exceed a set budget.
- The criterion-level LLM stage runs only on the top-30 per patient, never on the full pool.
- Every ranked trial carries its per-criterion reasons so a human can verify it.
- Treat all outputs as decision support; a human decides.

## 9. Local baseline quickstart

The implemented milestone is data ingestion, profiling, **BM25 retrieval**, and
benchmark evaluation. Dense retrieval, eligibility matching, the API, and Docker
are later milestones. BM25 results indicate lexical relevance, not eligibility.

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and run from
the project root in PowerShell. The setup uses project-local Python and caches:

```powershell
$env:UV_PYTHON_INSTALL_DIR = Join-Path (Get-Location) '.python'
$env:UV_CACHE_DIR = Join-Path (Get-Location) '.uv-cache'
uv sync --frozen
.\.venv\Scripts\python.exe -m src.diagnostics
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -u -m src.download
.\.venv\Scripts\python.exe -u -m src.clean
.\.venv\Scripts\python.exe -u -m src.retrieve build
.\.venv\Scripts\python.exe -u -m src.evaluate
```

Alternatively, `./scripts/run_baseline.ps1` runs the sequence and stops on failure.
Do not launch concurrent builds into the same output paths. A full rebuild can
take time; once the clean data and index exist, search/evaluation reuse them.

If the archive host returns HTTP 406, manually download the five ZIPs linked on
[the official track page](https://www.trec-cds.org/2021.html) into
`data/raw/trec2021/`. Keep their original filenames and leave them zipped. Run
`src.download` again: it registers the local archives after checking the reference
MD5 checksums, records SHA-256 fingerprints, and fetches the small topics/qrels.
No extraction is needed. CRC verification happens while reading each XML member.
If cleaning is interrupted, keep the completed temporary Parquet batches, rename
`trials.parquet.partial` to `trials.checkpoint.parquet`, then resume with:

```powershell
.\.venv\Scripts\python.exe -u -m src.clean --resume-from data/clean/trials.checkpoint.parquet
```

The interrupted process must have exited first. Recovery validates the cached
raw XML against each official archive CRC before reusing rows. Keep the same
parser configuration and archives when resuming; restart cleaning after any
normalization change. An incomplete file without a Parquet footer cannot resume.
Downloaded datasets, runtime files, caches, and indexes are excluded from Git.
Data-use terms and citations are recorded in `data/manifests/trec2021.json`.

Search with an example note (retrieval only):

```powershell
.\.venv\Scripts\python.exe -m src.retrieve search --note "54-year-old woman with type 2 diabetes taking metformin" --top-k 10
```

Use `--note-file path/to/note.txt` for a longer note and `--output results.json`
to save the response. Research benchmark notes only; no clinical decisions.

Outputs:

- `data/clean/trials.parquet`: normalized fields plus original XML and provenance.
- `reports/data_profile.md` and `.json`: field quality, lengths, section detection,
  abbreviation candidates, counts, and parsing checks.
- `reports/topic_review.txt`: engineering review of development topics 1-10.
- `data/indexes/bm25.sqlite`: local, disk-backed SQLite FTS5 BM25 index.
- `reports/bm25_v1/`: evaluation report, standard TREC run, full candidate rankings,
  per-topic metrics, timing, fingerprints, and examples of missed eligible trials.
- `notebooks/00_profile_data.ipynb`: optional inspection of generated statistics.

The fixed baseline searches title, conditions, summary plus detailed description,
and eligibility, with equal field weights. It uses SQLite's Porter/unicode61
index tokenizer and OR-combines unique note tokens after a fixed English stopword
list. Higher JSON scores are better (SQLite's native negative BM25 sign is reversed).
Score ties are ordered by trial ID. The TREC export uses descending rank integers
to preserve this exact order in evaluators with different score-tie conventions;
native BM25 scores remain in `rankings.json`. Native FTS5 top-k retrieval expands
boundary ties before sorting by ID; regression tests compare its exact ordering
and scores with exhaustive BM25 sorting. No clinical facts are inferred.
See [SQLite FTS5 scoring](https://www.sqlite.org/fts5.html#the_bm25_function).

Evaluation uses topics 1-10 of TREC 2021 against the **full snapshot**. Primary:
nDCG@10 with linear gains 0/1/2, matching trec_eval. Secondary: eligible-only
recall at 10/30/200 (grade 2 positives). Unjudged results have gain zero but remain
explicitly marked unjudged in diagnostics. Recall is N/A for a topic with no
eligible judgments. Every topic's nDCG and recall are independently checked with
`pytrec_eval`. These are development results; TREC 2022 remains held out.

## 10. Roadmap

- [x] Stage 0 data profile report
- [x] Trial section splitting (summary / inclusion / exclusion; original text retained)
- [ ] Patient parser with explicit negations and unknowns
- [ ] Criteria parser
- [x] Full-corpus BM25 retrieval baseline
- [ ] Embeddings + RRF hybrid retrieval
- [x] Evaluation harness and first BM25 results table
- [ ] Remaining retrieval/filter/LLM ablations
- [ ] Hard filter with "unknown ≠ fail"
- [ ] Criterion-level matching + aggregation
- [ ] Explanations
- [ ] LangGraph workflow + FastAPI endpoint

## 11. Frontend, containers, and deployment (after evaluation)

Once the matcher works and has been evaluated, the delivery roadmap is:

1. Build a frontend where a user pastes a patient note and sees ranked trials,
   matched criteria, exclusions, and missing information.
2. Connect the frontend to FastAPI through the planned `POST /match` endpoint.
3. Package the application with Docker, keeping trial data, indexes, and caches
   in persistent volumes.
4. Deploy to a cloud host with authentication, HTTPS, secrets management, and
   request/cost limits.

Start with a synthetic-data research demo. Real patient information requires
additional privacy and security work. These are planned delivery milestones;
implementation follows the evaluated retrieval and matching pipeline.

## 12. First measured development result

Full corpus: **375,580 trials**. Fixed first **10 TREC 2021 topics**.

| Method | nDCG@10 | Eligible recall@10 | Eligible recall@30 | Eligible recall@200 |
|---|---:|---:|---:|---:|
| BM25 v1 | 0.4112 | 0.0193 | 0.0492 | 0.1835 |

This is an untuned development baseline, not held-out performance or a clinical
validation. See `reports/bm25_v1/eval.md`, `error_analysis.txt` and
`reports/validation.json`. All 14 tests pass. TREC 2022 remains held out.
