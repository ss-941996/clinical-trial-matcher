"""Check actual-model API ordering against the frozen experiment's cached scores."""
import argparse
import json
import os
from pathlib import Path
import sys
sys.dont_write_bytecode=True
if os.environ.get('TRIAL_DEMO_DEPS'):sys.path.insert(0,os.environ['TRIAL_DEMO_DEPS'])

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--project',type=Path,required=True);parser.add_argument('--experiment',type=Path,required=True);parser.add_argument('--retrieval-results',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();sys.path.insert(0,str(args.project))
    from src.clean import load_topics
    from app.main import create_app
    from app.retrieval import Engine
    from app.rerank import ordered_candidates,fuse
    from collections import Counter
    from fastapi.testclient import TestClient
    config=json.loads((Path(__file__).parent/'app/config.json').read_text())
    topic='10';note=load_topics(args.project/'data/raw/trec2021/topics2021.xml')[topic]
    model=args.experiment/'cache/huggingface/models--cross-encoder--ms-marco-MiniLM-L6-v2/snapshots/233902d25c440f23af6f7d6e94d2946bac0bee0a'
    engine=Engine(args.project/'data/indexes/bm25.sqlite',model_dir=model,config=config)
    hits=json.loads((args.retrieval_results/f"cache/{config['rerank_pool']}/{topic}.json").read_text())[:200]
    original=[d for d,_ in hits];cached=json.loads((args.experiment/f'cache/scores/{topic}.json').read_text())['scores']
    expected=ordered_candidates(original,{d:cached[d] for d in original})
    if config['ce_blend_weight'] is not None:expected=fuse(original,expected,config['ce_blend_weight'])
    with TestClient(create_app(engine)) as client:
        assert client.get('/config').json()['reranking_available']
        response=client.post('/match',json={'note':note,'method':'rerank','top_k':10})
        assert response.status_code==200,response.text
        data=response.json();actual=[d['trial_id'] for d in data['results']]
        assert actual==expected[:10],(actual,expected[:10])
        differences=[abs(d['reranker_score']-cached[d['trial_id']]) for d in data['results']]
        # Batch composition can change floating point rounding slightly.
        assert max(differences)<0.001,differences
        assert data['candidate_count']==200 and all(d['eligibility_status']=='not_assessed' for d in data['results'])
        base=json.loads((args.retrieval_results/f'cache/baseline/{topic}.json').read_text())
        lexical=json.loads((args.retrieval_results/f'cache/lexical_rrf/{topic}.json').read_text())
        fusion_scores=Counter()
        for ranking,weight in [(base,1.),(lexical,config['fusion_balance_weight'])]:
            for rank,(trial,_) in enumerate(ranking,1):fusion_scores[trial]+=weight/(60+rank)
        expected_fusion=sorted(fusion_scores,key=lambda d:(-fusion_scores[d],d))[:10]
        fusion_response=client.post('/match',json={'note':note,'method':'fusion','top_k':10})
        assert fusion_response.status_code==200,fusion_response.text
        fusion_data=fusion_response.json();assert [r['trial_id'] for r in fusion_data['results']]==expected_fusion
    summary={'topic':topic,'method':'rerank','configuration':config,'top10_ids_match_cached_ranking':True,'max_logit_difference':max(differences),'candidate_count':200,'seconds':data['seconds'],'result_ids':actual,'balanced_fusion_top10_ids_match_cached_ranking':True,'fusion_seconds':fusion_data['seconds']}
    args.output.write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8');print(json.dumps(summary,indent=2),flush=True)
if __name__=='__main__':main()
