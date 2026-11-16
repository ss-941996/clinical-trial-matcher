# TREC 2021 data profile

Records: 375,580; patient topics: 75; judgments: 35,832.

Archive counts: `{'ClinicalTrials.2021-04-27.part1.zip': 79749, 'ClinicalTrials.2021-04-27.part2.zip': 76241, 'ClinicalTrials.2021-04-27.part3.zip': 76603, 'ClinicalTrials.2021-04-27.part4.zip': 76754, 'ClinicalTrials.2021-04-27.part5.zip': 66233}`.

No duplicate IDs, parse failures, or unmapped judged trial IDs. ZIP CRCs checked during member reads.

## Missing or empty fields

| Field | Count |
|---|---:|
| conditions | 860 |
| detailed_description | 126,028 |
| eligibility | 932 |
| exclusion | 24,521 |
| inclusion | 20,918 |
| interventions | 39,254 |
| maximum_age | 863 |
| minimum_age | 863 |
| sex | 863 |
| summary | 842 |

## Text lengths (characters)

| Field | Median | P90 | P99 | Maximum |
|---|---:|---:|---:|---:|
| title | 125 | 218 | 318 | 598 |
| summary | 403 | 1407 | 2675 | 4986 |
| detailed_description | 673 | 3125 | 10046 | 31861 |
| eligibility | 787 | 3260 | 7779 | 19336 |

## Eligibility sections

Detected inclusion headings: 354,662; exclusion headings: 351,059; both: 346,970.
Heading detection is heuristic; undetected prose remains in the complete eligibility field. It is not dropped.

## Uppercase token frequency

{'HIV': 98682, 'ULN': 79664, 'IV': 58926, 'II': 53148, 'MRI': 52110, 'BMI': 41870, 'III': 39379, 'CT': 39194, 'ALT': 39140, 'AST': 36548, 'ECOG': 33807, 'OR': 27407, 'HCV': 25461, 'PD': 25132, 'ECG': 24806, 'CNS': 22925, 'COPD': 19072, 'DSM': 18050, 'COVID': 17024, 'PET': 15527, 'QT': 14913, 'HBV': 14447, 'NYHA': 14410, 'ASA': 13213, 'ANC': 13116, 'CRITERIA': 12514, 'RECIST': 12234, 'BP': 12107, 'GI': 11935, 'TB': 11457}

These are abbreviation candidates, not clinically validated expansions.

## Normalization and limitations

Whitespace, HTML entities and common formatting tags are normalized. Raw XML, archive name and member path remain in Parquet.
Age strings and lab units are preserved. No clinical unit conversion or inferred patient facts are performed.
Status is profiled but not filtered: historical benchmark relevance is not a statement of current recruitment.

Sources: [TREC track](https://www.trec-cds.org/2021.html), [NIST labels](https://trec.nist.gov/data/trials2021.html).

Processing time: 263.0 seconds. Full machine-readable statistics and fingerprints: data_profile.json.
