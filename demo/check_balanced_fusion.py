"""Compare the selected API engine's complete candidate pool with its benchmark."""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys
sys.dont_write_bytecode=True
if os.environ.get('TRIAL_DEMO_DEPS'):sys.path.insert(0,os.environ['TRIAL_DEMO_DEPS'])

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--project',type=Path,required=True);parser.add_argument('--retrieval-results',type=Path,required=True);parser.add_argument('--fusion-results',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    sys.path.insert(0,str(args.project))
    from src.clean import load_topics
    from app.retrieval import Engine
    read=lambda p:json.loads(p.read_text())
    selection=read(args.fusion_results/'selection.json')['best_overall'];topic=read(args.fusion_results/'protocol.json')['validation_topics'][0]
    config=read(Path(__file__).parent/'app/config.json');engine=Engine(args.project/'data/indexes/bm25.sqlite',config=config);engine.load()
    result=engine.search(load_topics(args.project/'data/raw/trec2021/topics2021.xml')[topic],'fusion',200)
    expected=read(args.fusion_results/'validation_rankings.json')[selection][topic]
    assert [r['trial_id'] for r in result['results']]==expected
    scores=Counter()
    for arm,weight in [('baseline',1.),('lexical_rrf',config['fusion_balance_weight'])]:
        for rank,(trial,_) in enumerate(read(args.retrieval_results/f'cache/{arm}/{topic}.json'),1):scores[trial]+=weight/(60+rank)
    assert all(r['retrieval_score']==scores[r['trial_id']] for r in result['results'])
    summary={'topic':topic,'arm':selection,'count':len(expected),'all_200_ids_and_scores_exact':True,'seconds':result['seconds']}
    args.output.write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8');print(summary)
if __name__=='__main__':main()
