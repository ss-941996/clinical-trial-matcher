"""Sequential API parity check against the completed five-patient hybrid pilot."""
import json
import argparse
from pathlib import Path
import sys
import time

import serve_hybrid  # Apply the same one-core/one-thread serving limits.
from fastapi.testclient import TestClient
from app.main import create_app

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from src.clean import load_topics


def verify(report_path=None):
    saved=json.loads((ROOT/'reports/medcpt-hybrid-pilot-5120-v1/rankings/hybrid_1.json').read_text())
    topics=load_topics(ROOT/'data/raw/trec2021/topics2021.xml')
    report=dict(pipeline='lexical_medcpt_rrf',records=5120,cross_encoder=False,patients={})
    started=time.perf_counter()
    with TestClient(create_app()) as client:
        config=client.get('/config').json()
        assert config['default_method']=='hybrid' and config['historical'] and config['indexed_trials']==5120
        assert not config['reranking_available'] and config['semantic_available']
        home=client.get('/').text
        assert 'pipeline-description' in home and '<select' not in home
        for topic in ['1','2','3','4','5']:
            response=client.post('/match',json=dict(note=topics[topic]))
            assert response.status_code==200,response.text
            result=response.json()
            assert result['method']=='hybrid' and result['candidate_count']==200
            assert result['rerank_scored_pairs']==0
            expected=[trial for trial,_ in saved[topic][:10]]
            actual=[row['trial_id'] for row in result['results']]
            assert actual==expected,(topic,actual,expected)
            assert all(row['reranker_score'] is None and row['eligibility_status']=='not_assessed' for row in result['results'])
            report['patients'][topic]=dict(top10_exact_match=True,candidates=200,seconds=result['seconds'],timing=result['timing'])
            print(f'Patient {topic}: top10 exactly matches saved hybrid; 200 candidates; no reranking; {result["seconds"]:.3f}s',flush=True)
        report['index_sha256']=client.app.state.engine.semantic.manifest['index_sha256']
    report['total_seconds_including_startup']=round(time.perf_counter()-started,3)
    report['passed']=True
    dest=Path(report_path) if report_path else ROOT/'reports/hybrid-serving-v1/verification.json'
    dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(json.dumps(report,indent=2)+'\n')
    print('All five API comparisons passed. No trial encoding performed.',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',type=Path,help='Write results separately from the original verification')
    verify(parser.parse_args().report)
