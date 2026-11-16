"""Inspect fixed development examples without changing the model or selection."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
sys.dont_write_bytecode=True

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--project',type=Path,required=True);parser.add_argument('--retrieval-results',type=Path,required=True);parser.add_argument('--results',type=Path,required=True);args=parser.parse_args()
    sys.path.insert(0,str(args.project));sys.path.insert(0,str(Path(__file__).parent.parent/'experiment-deps'))
    from src.clean import load_qrels
    from rerank import ordered_candidates,fuse
    qrels=load_qrels(args.project/'data/raw/trec2021/qrels2021.txt');read=lambda p:json.loads(p.read_text())
    selected=read(args.results/'selection.json')['best_reranking_arm'];pool='lexical_rrf' if selected.startswith('lexical_rrf') else 'baseline'
    db=sqlite3.connect((args.project/'data/indexes/bm25.sqlite').as_uri()+'?mode=ro',uri=True)
    lines=['# Fixed development examples','',f'Patients 1 and 10 were already in the original development set. Compare selected reranking arm `{selected}` with its own `{pool}` candidate order. Grades are the benchmark judgments: 0 irrelevant, 1 excluded, 2 eligible, U unjudged. These are not new clinical assessments.','']
    for topic in ['1','10']:
        original=[d for d,_ in read(args.retrieval_results/f'cache/{pool}/{topic}.json')[:200]]
        scores=read(args.results/f'cache/scores/{topic}.json')['scores'];ranked=ordered_candidates(original,{d:scores[d] for d in original})
        if not selected.endswith('_ce'):ranked=fuse(original,ranked,float(selected.rsplit('_',1)[1]))
        positions={d:i for i,d in enumerate(original,1)}
        lines += [f'## Patient {topic}','','| New rank | Candidate-pool rank | Grade | Trial | Title |','|---:|---:|---|---|---|']
        for i,d in enumerate(ranked[:10],1):
            title=db.execute('SELECT title FROM trials WHERE rowid=(SELECT row_id FROM identities WHERE trial_id=?)',(d,)).fetchone()[0].replace('|','/').replace('\n',' ')
            lines.append(f'| {i} | {positions[d]} | {qrels[topic].get(d,"U")} | {d} | {title} |')
        before=[d for d in original[:10] if qrels[topic].get(d)==2];after=[d for d in ranked[:10] if qrels[topic].get(d)==2]
        lines += ['',f'Judged eligible in the displayed ten: {len(before)} before, {len(after)} after. The 200-trial candidate set is unchanged.','']
    db.close();(args.results/'development_examples.md').write_text('\n'.join(lines),encoding='utf-8')
    print('Saved development_examples.md')
if __name__=='__main__':main()
