"""Graded nDCG and eligible-only recall with independent trec_eval cross-checks."""
from __future__ import annotations
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import subprocess
import time
import pytrec_eval
from src.clean import load_qrels,load_topics
from src.common import ROOT,now,sha256,write_json
from src.retrieve import connect,search,metadata,lookup,query_terms

def metrics(ranking,judgments):
    if len(set(ranking))!=len(ranking): raise ValueError('Duplicate trial in ranking')
    if any(x not in {0,1,2} for x in judgments.values()): raise ValueError('Invalid relevance label')
    def dcg(grades): return sum(g/math.log2(i+2) for i,g in enumerate(grades))
    ideal=dcg(sorted(judgments.values(),reverse=True)[:10])
    top=ranking[:10]
    eligible={d for d,g in judgments.items() if g==2}
    result={'ndcg@10':dcg([judgments.get(d,0) for d in top])/ideal if ideal else 0.0,
            'eligible_total':len(eligible),'judged@10':sum(d in judgments for d in top)/len(top) if top else 0.0,
            'eligible@10':sum(judgments.get(d)==2 for d in top),'excluded@10':sum(judgments.get(d)==1 for d in top),
            'unjudged@10':sum(d not in judgments for d in top)}
    for k in [10,30,200]: result[f'eligible_recall@{k}']=len(set(ranking[:k])&eligible)/len(eligible) if eligible else None
    pairs=[(i,j) for i,d in enumerate(top) for j,e in enumerate(top) if judgments.get(d)==1 and judgments.get(e)==2]
    result['excluded_eligible_pairs@10']=len(pairs)
    result['inversions@10']=sum(i<j for i,j in pairs)
    result['inversion_rate@10']=result['inversions@10']/len(pairs) if pairs else None
    return result

def check_reference(ranking,judgments,result):
    run={'q':{d:float(len(ranking)-i) for i,d in enumerate(ranking)}}
    qrels={'q':judgments}
    graded=pytrec_eval.RelevanceEvaluator(qrels,{'ndcg_cut_10'}).evaluate(run)['q']
    binary=pytrec_eval.RelevanceEvaluator({'q':{d:int(g==2) for d,g in judgments.items()}}, {'recall_10','recall_30','recall_200'}).evaluate(run)['q']
    if not math.isclose(result['ndcg@10'],graded['ndcg_cut_10'],abs_tol=1e-10): raise AssertionError('nDCG differs from trec_eval')
    for k in [10,30,200]:
        actual=result[f'eligible_recall@{k}']
        if actual is not None and not math.isclose(actual,binary[f'recall_{k}'],abs_tol=1e-10): raise AssertionError(f'Recall@{k} differs from trec_eval')

def evaluate(config_path,index,raw,output):
    config_path,index,raw,output=map(Path,[config_path,index,raw,output])
    output.mkdir(parents=True,exist_ok=True)
    if config_path.resolve()==output.resolve(): raise ValueError('Invalid output')
    config=json.loads(config_path.read_text(encoding='utf-8-sig'))
    if config['year']!=2021 or config['snapshot']!='2021-04-27': raise ValueError('This baseline evaluator supports only the fixed TREC 2021 development protocol')
    if config['candidate_k']<200: raise ValueError('Recall@200 requires at least 200 candidates')
    topics=load_topics(raw/'topics2021.xml'); judgments=load_qrels(raw/'qrels2021.txt')
    topic_ids=config['topic_ids']
    if len(set(topic_ids))!=len(topic_ids) or any(t not in topics or t not in judgments for t in topic_ids): raise ValueError('Invalid topic selection')
    results={}; run_lines=[]; detail={}; cases=[]; elapsed={}
    started=time.perf_counter()
    with connect(index) as db:
        index_metadata=metadata(db)
        if index_metadata['records']!=375580: raise ValueError('Full corpus index required')
        if config['tokenizer']!=index_metadata['tokenizer'] or [config['field_weights'][f] for f in ['title','conditions','summary','eligibility']]!=index_metadata['field_weights'][1:]:
            raise ValueError('Configuration and built index differ')
        for t in topic_ids:
            tick=time.perf_counter()
            hits=search(db,topics[t],config['candidate_k'])
            ranking=[h['trial_id'] for h in hits]
            row=metrics(ranking,judgments[t]); check_reference(ranking,judgments[t],row)
            elapsed[t]=time.perf_counter()-tick
            results[f'2021:{t}']=row; detail[f'2021:{t}']=hits
            for i,h in enumerate(hits,1): run_lines.append(f"{t} Q0 {h['trial_id']} {i} {len(hits)-i+1} {config['run_name']}")
            missing=sorted(d for d,g in judgments[t].items() if g==2 and d not in set(ranking))
            cases.append(dict(topic_id=f'2021:{t}',patient_note=topics[t],query_tokens=query_terms(topics[t]),
                top10=[dict(**h,judgment=judgments[t].get(h['trial_id'],'unjudged')) for h in hits[:10]],
                eligible_missed_at_200=len(missing),missed_examples=[dict(trial_id=d,**lookup(db,d)) for d in missing[:3]]))
            print(f"Topic {t}: nDCG@10={row['ndcg@10']:.4f}; eligible recall@200={row['eligible_recall@200']}; {elapsed[t]:.2f}s",flush=True)
    means={}
    for key in next(iter(results.values())):
        values=[r[key] for r in results.values() if r[key] is not None]
        means[key]=sum(values)/len(values) if values else None
    version=subprocess.run(['git','-c','safe.directory='+ROOT.as_posix(),'rev-parse','HEAD'],capture_output=True,text=True,cwd=ROOT)
    document=dict(created_at=now(),run=config['run_name'],scope='FULL trial corpus; first 10 TREC 2021 development topics only',
        config=config,config_sha256=sha256(config_path),index=index_metadata,
        topics_sha256=sha256(raw/'topics2021.xml'),qrels_sha256=sha256(raw/'qrels2021.txt'),
        git_base_commit=version.stdout.strip(),code_sha256={p.name:sha256(p) for p in sorted((ROOT/'src').glob('*.py'))},
        macro=means,per_topic=results,seconds=time.perf_counter()-started,query_seconds=elapsed,
        verification='Every per-topic nDCG@10 and eligible recall cross-checked against pytrec_eval (trec_eval).',
        eligible_recall_topics=sum(r['eligible_total']>0 for r in results.values()),
        convention='Linear gains 0/1/2; unjudged gain zero; recall positives grade 2 only. Zero-eligible recall is null and excluded from macro.',
        limitations=['Development result, not held-out performance.','No eligibility filtering or LLM reasoning.',
                     'Judgments are pooled and incomplete; unjudged does not prove ineligibility.','Historical recruitment status.'],
        trec_export_scores='Descending rank integers preserve deterministic BM25/tie order; native BM25 scores retained in rankings.json',
        search_cache_kib=131072,llm_calls=0,embedding_calls=0,model_api_cost_usd=0)
    write_json(output/'metrics.json',document); write_json(output/'rankings.json',detail); write_json(output/'error_cases.json',cases)
    (output/'run.trec').write_text('\n'.join(run_lines)+'\n',encoding='utf-8')
    lines=['# BM25 development evaluation','',document['scope']+'.', '',f"Indexed trials: **{index_metadata['records']:,}**. Topics: **{len(topic_ids)}**.",
           '', 'This is the first untuned lexical baseline. It does not check eligibility. TREC 2022 was not used.',
           '', '| Run | nDCG@10 | Eligible recall@10 | Eligible recall@30 | Eligible recall@200 |','|---|---:|---:|---:|---:|',
           f"| {config['run_name']} | {means['ndcg@10']:.4f} | {means['eligible_recall@10']:.4f} | {means['eligible_recall@30']:.4f} | {means['eligible_recall@200']:.4f} |",
           '', '## Conventions','',document['convention'],document['verification'],
           f"Eligible recall macro denominator: {document['eligible_recall_topics']} topics. Inversion rate is averaged only over topics with co-retrieved judged grade-1/grade-2 pairs.",
           'Inversion = excluded trial above eligible trial within the retrieved top 10. No eligible/excluded pair means N/A.',
           '', '## Per-topic results','', '| Topic | nDCG@10 | Eligible recall@200 | Judged fraction@10 |','|---|---:|---:|---:|']
    for t,row in results.items():
        recall=row['eligible_recall@200']
        lines.append(f"| {t} | {row['ndcg@10']:.4f} | {recall if recall is not None else 'N/A'} | {row['judged@10']:.2f} |")
    lines+=['', '## Error inspection','',
            'The machine-readable error_cases.json contains each note, the actual query tokens, judged labels for the top 10, and up to three eligible misses at rank 200.',
            'The misses are examples in sorted ID order, not an estimate of which errors matter most. Inspect criteria before attributing the cause.',
            '','## Runtime and reproducibility','',f"Evaluation/search time: {document['seconds']:.2f}s. Zero LLM/embedding calls; model API cost $0.",
            'Run configuration, code fingerprints, index provenance and per-topic timing are in metrics.json. Full rankings are in run.trec and rankings.json.',
            'Only this fixed baseline has been attempted. Hybrid search, filters and LLM ablations remain pending.',
            '', '## Limits','']+[f'- {x}' for x in document['limitations']]
    (output/'eval.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(means,indent=2))

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',type=Path,default=ROOT/'configs/bm25_v1.json')
    p.add_argument('--index',type=Path,default=ROOT/'data/indexes/bm25.sqlite')
    p.add_argument('--raw',type=Path,default=ROOT/'data/raw/trec2021')
    p.add_argument('--output',type=Path,default=ROOT/'reports/bm25_v1')
    a=p.parse_args(); evaluate(a.config,a.index,a.raw,a.output)
if __name__=='__main__': main()
