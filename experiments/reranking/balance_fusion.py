"""Separate cached-ranking experiment: retain BM25 quality while broadening coverage."""
import argparse
import json
from pathlib import Path
import sys
sys.dont_write_bytecode=True

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--project',type=Path,required=True);parser.add_argument('--retrieval-results',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    sys.path.insert(0,str(args.project));sys.path.insert(0,str(args.retrieval_results.parent));sys.path.insert(0,str(Path(__file__).parent.parent/'experiment-deps'))
    from src.clean import load_qrels
    from src.evaluate import metrics,check_reference
    from experiment import save,digest,rrf,aggregate,bootdiff
    read=lambda p:json.loads(p.read_text());split=read(args.retrieval_results/'protocol.json');qrels=load_qrels(args.project/'data/raw/trec2021/qrels2021.txt')
    out=args.output;out.mkdir(parents=True,exist_ok=True)
    protocol={'weights':[.25,.5,1.,2.],'formula':'RRF k=60; original BM25 weight 1; three-query lexical RRF list weight w; each input depth 1000; retain 200',
        'selection':'Maximum development eligible recall@200 among arms with development nDCG@10 >= original BM25 - 0.005; break ties by nDCG@10 then arm name. Original BM25 included.',
        'validation_status':'Same previously inspected internal validation; exploratory, not a final test.',
        'development_topics':split['development_topics'],'validation_topics':split['validation_topics'],
        'code_sha256':digest(Path(__file__)),'source_protocol_sha256':digest(args.retrieval_results/'protocol.json')}
    if (out/'protocol.json').exists() and read(out/'protocol.json')!=protocol:raise ValueError('Changed protocol; use fresh output')
    save(out/'protocol.json',protocol)
    def rankings(topic):
        base=read(args.retrieval_results/f'cache/baseline/{topic}.json');lexical=read(args.retrieval_results/f'cache/lexical_rrf/{topic}.json')
        arms={'baseline':[d for d,_ in base[:200]],'lexical_rrf':[d for d,_ in lexical[:200]]}
        for weight in protocol['weights']:arms[f'balance_{weight:g}']=[d for d,_ in rrf([base,lexical],[1.,weight],k=200)]
        return arms
    dev={}
    for topic in protocol['development_topics']:
        for arm,ids in rankings(topic).items():
            row=metrics(ids,qrels[topic]);check_reference(ids,qrels[topic],row);dev.setdefault(arm,{})[topic]=row
    baseline=aggregate(dev['baseline'])['ndcg@10'];allowed=[n for n,rows in dev.items() if aggregate(rows)['ndcg@10']>=baseline-.005]
    best=sorted(allowed,key=lambda n:(-aggregate(dev[n])['eligible_recall@200'],-aggregate(dev[n])['ndcg@10'],n))[0]
    save(out/'selection.json',{'best_overall':best,'frozen_before_validation':True,'allowed_development_arms':allowed})
    save(out/'development_metrics.json',{n:{'macro':aggregate(rows),'per_topic':rows} for n,rows in dev.items()})
    val={n:{} for n in dict.fromkeys(['baseline','lexical_rrf',best])};saved={n:{} for n in val}
    for topic in protocol['validation_topics']:
        ranks=rankings(topic)
        for arm in val:
            ids=ranks[arm];row=metrics(ids,qrels[topic]);check_reference(ids,qrels[topic],row);val[arm][topic]=row;saved[arm][topic]=ids
    save(out/'validation_metrics.json',{n:{'macro':aggregate(rows),'per_topic':rows} for n,rows in val.items()})
    save(out/'validation_rankings.json',saved)
    save(out/'paired_comparisons.json',{n:{k:bootdiff(val['baseline'],rows,k) for k in ['ndcg@10','eligible_recall@10','eligible_recall@200']} for n,rows in val.items() if n!='baseline'})
    for name,rows in dev.items():print('DEV',name,aggregate(rows))
    print('SELECTION',best)
    for name,rows in val.items():print('VALIDATION',name,aggregate(rows))

if __name__=='__main__':main()
