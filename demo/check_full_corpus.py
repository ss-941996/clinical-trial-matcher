"""Verify the API retrieval implementation against saved experiment rankings."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sys
os.environ['PYTHONDONTWRITEBYTECODE']='1'
sys.dont_write_bytecode=True
if os.environ.get('TRIAL_DEMO_DEPS'):sys.path.insert(0,os.environ['TRIAL_DEMO_DEPS'])
from app.retrieval import Engine,search_fts,rank_fusion,focused,opening

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--project',type=Path,required=True);parser.add_argument('--retrieval-results',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();sys.path.insert(0,str(args.project))
    from src.clean import load_topics
    topics=load_topics(args.project/'data/raw/trec2021/topics2021.xml')
    engine=Engine(args.project/'data/indexes/bm25.sqlite');engine.load();checks=[]
    for topic in ['1','10']:
        note=topics[topic]
        with closing(engine.connect()) as db:
            rankings={'baseline':search_fts(db,note),'lexical_rrf':rank_fusion([search_fts(db,q,1000,True) for q in [note,focused(note,engine.vocab),opening(note)]])}
        for arm,actual in rankings.items():
            expected=json.loads((args.retrieval_results/f'cache/{arm}/{topic}.json').read_text())[:200]
            assert actual==[tuple(x) for x in expected],(topic,arm,'ranking differs')
            checks.append({'topic':topic,'arm':arm,'count':len(actual),'ids_and_scores_exact':True})
            print('Matched',topic,arm,len(actual),flush=True)
    args.output.write_text(json.dumps(checks,indent=2)+'\n',encoding='utf-8')
if __name__=='__main__':main()
