"""Evaluate a fixed cross-encoder and rank blends without touching TREC 2022."""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
sys.dont_write_bytecode=True
os.environ.setdefault('TOKENIZERS_PARALLELISM','false')
os.environ.setdefault('OMP_NUM_THREADS','4')

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--project',type=Path,required=True);ap.add_argument('--retrieval-results',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);ap.add_argument('--deps',type=Path,required=True)
    a=ap.parse_args();project=a.project.resolve();source=a.retrieval_results.resolve();out=a.output.resolve()
    if project==out or project in out.parents:raise ValueError('Project is read only')
    out.mkdir(parents=True,exist_ok=True);sys.path.insert(0,str(a.deps.resolve()));sys.path.insert(0,str(project));sys.path.insert(0,str(source.parent))
    from src.clean import load_topics,load_qrels
    from src.evaluate import metrics,check_reference
    from experiment import save,digest,aggregate,bootdiff
    from rerank import CrossEncoder,MODEL,REVISION,GRAPH,FIELD_TOKENS,QUERY_TOKENS,trial_document,ordered_candidates,fuse
    read=lambda p:json.loads(Path(p).read_text(encoding='utf-8'))
    split=read(source/'protocol.json');dev=split['development_topics'];val=split['validation_topics']
    protocol=dict(model=MODEL,revision=REVISION,graph=GRAPH,field_token_limits=FIELD_TOKENS,query_token_limit=QUERY_TOKENS,
        max_pair_tokens=512,pools=['baseline','lexical_rrf'],candidate_depth=200,
        rank_blend_weights=[.5,1.,2.],blend='RRF constant 60; retrieval weight 1, cross-encoder weight as listed',
        selection='Maximum development nDCG@10, then eligible recall@10, then arm name; baseline included as no-change option.',
        development_topics=dev,validation_topics=val,
        validation_status='Previously inspected TREC 2021 internal validation, reused for exploratory comparison. Not an untouched test.',
        held_out='TREC 2022 remains untouched',model_scope='General MS MARCO relevance cross-encoder, not a clinical eligibility classifier.',
        code_sha256={n:digest(Path(__file__).parent/n) for n in ['rerank.py','evaluate_reranker.py']},
        retrieval_protocol_sha256=digest(source/'protocol.json'),topics_sha256=digest(project/'data/raw/trec2021/topics2021.xml'),qrels_sha256=digest(project/'data/raw/trec2021/qrels2021.txt'))
    if (out/'protocol.json').exists() and read(out/'protocol.json')!=protocol:raise ValueError('Changed protocol; use fresh output')
    save(out/'protocol.json',protocol)
    os.environ['HF_HOME']=str(out/'cache/huggingface')
    from huggingface_hub import snapshot_download
    model_path=snapshot_download(MODEL,revision=REVISION,cache_dir=str(out/'cache/huggingface'),allow_patterns=['tokenizer.json',GRAPH,'config.json'])
    encoder=CrossEncoder(model_path)
    save(out/'model_manifest.json',dict(model=MODEL,revision=REVISION,files={str(p.relative_to(model_path)):digest(p) for p in Path(model_path).rglob('*') if p.is_file()},
        packages={n:importlib.metadata.version(n) for n in ['onnxruntime','tokenizers','numpy']}))
    topics=load_topics(project/'data/raw/trec2021/topics2021.xml');qrels=load_qrels(project/'data/raw/trec2021/qrels2021.txt')
    db=sqlite3.connect((project/'data/indexes/bm25.sqlite').as_uri()+'?mode=ro',uri=True);db.execute('PRAGMA cache_size=-65536')
    started=time.perf_counter();inferences=0
    def topic_rankings(t):
        nonlocal inferences
        pools={name:[d for d,_ in read(source/f'cache/{name}/{t}.json')[:200]] for name in protocol['pools']}
        all_ids=sorted(set().union(*(set(x) for x in pools.values())))
        path=out/f'cache/scores/{t}.json'
        if path.exists():scores=read(path)['scores']
        else:
            tick=time.perf_counter();docs=[trial_document(db,d) for d in all_ids]
            scores=dict(zip(all_ids,encoder.predict(topics[t],docs),strict=True));inferences+=len(scores)
            save(path,dict(scores=scores,seconds=time.perf_counter()-tick,documents=len(all_ids)))
            print(f'Topic {t}: scored {len(scores)} pairs in {time.perf_counter()-tick:.1f}s',flush=True)
        assert set(scores)==set(all_ids)
        result={}
        for pool,ids in pools.items():
            result[pool]=ids;reranked=ordered_candidates(ids,{d:scores[d] for d in ids})
            result[pool+'_ce']=reranked
            for weight in protocol['rank_blend_weights']:result[f'{pool}_blend_{weight:g}']=fuse(ids,reranked,weight)
        return result
    dev_metrics={};dev_rankings={}
    for t in dev:
        ranks=topic_rankings(t)
        for arm,ids in ranks.items():
            row=metrics(ids,qrels[t]);check_reference(ids,qrels[t],row)
            pool='lexical_rrf' if arm.startswith('lexical_rrf') else 'baseline'
            assert set(ids)==set(ranks[pool])
            assert row['eligible_recall@200']==metrics(ranks[pool],qrels[t])['eligible_recall@200']
            dev_metrics.setdefault(arm,{})[t]=row
        dev_rankings[t]=ranks
        save(out/'development_metrics.json',{name:dict(macro=aggregate(rows),per_topic=rows) for name,rows in dev_metrics.items()})
    best=sorted(dev_metrics,key=lambda name:(-aggregate(dev_metrics[name])['ndcg@10'],-aggregate(dev_metrics[name])['eligible_recall@10'],name))[0]
    rerank_arms=[n for n in dev_metrics if n not in protocol['pools']]
    best_rerank=sorted(rerank_arms,key=lambda name:(-aggregate(dev_metrics[name])['ndcg@10'],-aggregate(dev_metrics[name])['eligible_recall@10'],name))[0]
    save(out/'selection.json',dict(best_overall=best,best_reranking_arm=best_rerank,frozen_before_validation=True))
    for arm,rows in dev_metrics.items():print('DEV',arm,aggregate(rows),flush=True)
    print('Frozen choices:',best,best_rerank,flush=True)
    arms=list(dict.fromkeys(['baseline','lexical_rrf',best,best_rerank]));val_metrics={arm:{} for arm in arms};val_rankings={arm:{} for arm in arms}
    for t in val:
        ranks=topic_rankings(t)
        for arm in arms:
            ids=ranks[arm];row=metrics(ids,qrels[t]);check_reference(ids,qrels[t],row)
            val_metrics[arm][t]=row;val_rankings[arm][t]=ids
    for arm,rows in val_metrics.items():print('VALIDATION',arm,aggregate(rows),flush=True)
    save(out/'validation_metrics.json',{arm:dict(macro=aggregate(rows),per_topic=rows) for arm,rows in val_metrics.items()})
    save(out/'validation_rankings.json',val_rankings)
    save(out/'paired_comparisons.json',{arm:{k:bootdiff(val_metrics['baseline'],rows,k) for k in ['ndcg@10','eligible_recall@10','eligible_recall@200']} for arm,rows in val_metrics.items() if arm!='baseline'})
    save(out/'manifest.json',dict(seconds=time.perf_counter()-started,new_pairs_scored=inferences,llm_calls=0,paid_api_calls=0,
        verification='Every per-topic metric cross-checked with pytrec_eval. Candidate sets preserved exactly by reranking.',
        interpretation='Reused internal validation; do not claim untouched held-out performance.'))
    db.close();print('RERANKING EXPERIMENT COMPLETE',flush=True)

if __name__=='__main__':main()
