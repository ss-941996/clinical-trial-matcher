# TREC 2022 evaluation deferred

On 2026-11-16 the user chose to defer 2022 evaluation until development on 2021
is complete. No 2022 metrics were computed or used to choose model settings.

Before cancellation, the official 2022 patient topics were downloaded and fixed
BM25/balanced-fusion candidate rankings were generated for 13 of 50 topics. The
official judgments webpage was briefly opened while locating its download URL.
The local qrels file was not downloaded; no label-access/evaluation artifacts
were produced. This exposure is recorded rather than claiming zero access.

The stopped run and its partial rankings remain outside the repository and are
not part of this commit. They must not be inspected for development or used to
tune the system. No model changes were made from2022 labels or results.

Continue selection and development using 2021. Freeze the full configuration
before a future 2022 evaluation, report all prespecified metrics regardless of
outcome, and document this limited prior exposure. After evaluating 2022, treat
repeats as regression checks and use a new independent set for later final tests.
