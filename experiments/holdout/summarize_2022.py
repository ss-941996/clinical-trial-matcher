"""Paired uncertainty analysis of the frozen 2022 comparison."""
import json
from pathlib import Path
import sys
import numpy as np
from evaluate_2022 import priority, save

priority()
root=Path(sys.argv[1]).resolve()
out=root/'reports/trec2022-balanced-fusion-v1'
plan=json.loads((out/'uncertainty-plan.json').read_text())
result=json.loads((out/'metrics.json').read_text())
rng=np.random.default_rng(plan['seed'])
intervals={}
for metric in plan['metrics']:
    values=[row['balanced_fusion'][metric]-row['bm25'][metric]
            for row in result['per_topic'].values() if row['bm25'][metric] is not None]
    values=np.asarray(values)
    estimates=values[rng.integers(0,len(values),size=(plan['resamples'],len(values)))].mean(axis=1)
    intervals[metric]=dict(paired_topics=len(values),mean_delta=float(values.mean()),
        percentile_95_ci=np.quantile(estimates,[.025,.975]).tolist())
save(out/'uncertainty.json',dict(plan=plan,paired_fusion_minus_bm25=intervals,
    note='Patient-level paired bootstrap percentile intervals; no adjustment for multiple metrics; not a clinical-readiness test.'))
print(json.dumps(intervals,indent=2))
