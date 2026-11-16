# Clinical Trial Matcher: patient note → ranked trials

A staged pipeline that takes a free-text patient description, cleans it, extracts structured eligibility facts, narrows a large registry of clinical trials down to a short list, checks eligibility criterion by criterion, and returns an explainable top-10 ranking. A clinician or trial coordinator makes the final decision.

Scored with **nDCG@10** against graded relevance judgments (0 = not relevant, 1 = excluded, 2 = eligible).

> Research and portfolio project. Not a medical device and not for real patient decisions.

---

## 1. Problem

Given:
- a **patient description** (a synthetic admission-note-style free text)
- a **pool of clinical trials** (long registry records: title, summary, conditions, interventions, and inclusion/exclusion criteria written as prose)

Produce, per patient, an ordered list of trial IDs, best first. Only the top 10 count toward the score.

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

## 9. Quickstart

```bash
git clone <your-repo> && cd clinical-trial-matcher
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m src.clean --input data/raw --output data/clean
python -m src.evaluate --run hybrid_v1
docker compose up   # FastAPI on :8000
```

## 10. Roadmap

- [ ] Stage 0 data profile report
- [ ] Trial section splitting (summary / inclusion / exclusion)
- [ ] Patient parser with explicit negations and unknowns
- [ ] Criteria parser
- [ ] BM25 + embeddings + RRF retrieval baseline
- [ ] Evaluation harness + ablation table
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
