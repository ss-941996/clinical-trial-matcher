"""Frozen sequential TREC 2022 evaluation: rank all 50 before opening qrels."""
from __future__ import annotations
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import time
import urllib.request
from datetime import datetime, timezone

TOPICS='https://trec.nist.gov/data/trials/topics2022.xml'
QRELS='https://trec.nist.gov/data/trials/qrels2022.txt'
TOPIC_IDS=[str(i) for i in range(1,51)]
ARMS={'balanced_fusion':'fusion','bm25':'bm25'}


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def signature(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')
    temp.replace(path)


def priority():
    for name in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS']:
        os.environ[name]='1'
    os.environ['TOKENIZERS_PARALLELISM']='false'
    if os.name=='nt':
        kernel=ctypes.windll.kernel32
        kernel.GetCurrentProcess.restype=ctypes.c_void_p
        kernel.SetPriorityClass.argtypes=[ctypes.c_void_p,ctypes.c_uint32]
        if not kernel.SetPriorityClass(kernel.GetCurrentProcess(),0x4000):
            raise RuntimeError('Cannot lower process priority')
    elif hasattr(os,'nice'):
        os.nice(max(0,10-os.nice(0)))


def code_files(root):
    paths=['demo/app/config.json','demo/app/retrieval.py','demo/app/rerank.py',
        'demo/app/assets/condition_vocabulary.json','src/evaluate.py','src/clean.py',
        'src/common.py','src/retrieve.py','experiments/holdout/evaluate_2022.py']
    return {name:digest(root/name) for name in paths}


def download(raw, name, url):
    path=raw/name
    if not path.exists():
        request=urllib.request.Request(url,headers={'User-Agent':'ClinicalTrialMatcher-Research/1.0'})
        with urllib.request.urlopen(request,timeout=60) as response:
            payload=response.read()
        raw.mkdir(parents=True,exist_ok=True)
        temp=path.with_suffix('.tmp');temp.write_bytes(payload);temp.replace(path)
    receipts_path=raw/'provenance.json'
    receipts=json.loads(receipts_path.read_text()) if receipts_path.exists() else {}
    if name in receipts and digest(path)!=receipts[name]['sha256']:
        raise RuntimeError('Downloaded input changed')
    receipts.setdefault(name,dict(url=url,sha256=digest(path),bytes=path.stat().st_size,
        recorded_utc=datetime.now(timezone.utc).isoformat()))
    save(receipts_path,receipts)
    return path


def frozen(root, output, create=False):
    path=output/'freeze.json';index=root/'data/indexes/bm25.sqlite'
    config=json.loads((root/'demo/app/config.json').read_text(encoding='utf-8'))
    if config.get('default_method')!='fusion' or config.get('fusion_balance_weight')!=2.0:
        raise ValueError('Expected existing balanced fusion, weight 2.0')
    if create:
        if path.exists():
            raise FileExistsError('Freeze already exists; reuse it rather than overwrite it')
        git=subprocess.run(['git','-c','safe.directory='+str(root),'-C',str(root),'rev-parse','HEAD'],
            text=True,capture_output=True,check=True).stdout.strip()
        document=dict(created_utc=datetime.now(timezone.utc).isoformat(),git_base=git,
            config=config,code_sha256=code_files(root),index_sha256=digest(index),
            index_bytes=index.stat().st_size,topics=TOPIC_IDS,arms=ARMS,candidate_k=200,
            pipeline='Original BM25 top1000; equal RRF of weighted full-note/condition/opening top1000 each; outer RRF weights 1:2, constant60, keep200. No neural reranking.',
            sources=dict(topics=TOPICS,qrels=QRELS),
            metrics=['ndcg@10','eligible_recall@10','eligible_recall@30','eligible_recall@200','eligible@10'],
            conventions='Linear gains 0/1/2; unjudged gain zero; only grade2 eligible. Macro recall excludes topics with zero eligible judgments.',
            exposure='Prior topics access and 13 candidate rankings in cancelled 2026-11-16 run; no local qrels or computed metrics used for selection. No zero-exposure claim.',
            constraints='Single sequential low-priority process; no neural inference; no 2022-based tuning. Rank all50 before downloading qrels. Later repeats are regression checks.')
        save(path,document);return document
    document=json.loads(path.read_text())
    if code_files(root)!=document['code_sha256'] or index.stat().st_size!=document['index_bytes']:
        raise RuntimeError('Frozen code/config/index identity changed')
    return document


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[2])
    parser.add_argument('--stage',choices=['freeze','rank','evaluate'],required=True)
    args=parser.parse_args();root=args.root.resolve();priority()
    output=root/'reports/trec2022-balanced-fusion-v1';raw=root/'data/raw/trec2022'
    protocol=frozen(root,output,create=args.stage=='freeze');key=signature(protocol)
    if args.stage=='freeze':
        print('Configuration frozen before new 2022 input download',flush=True);return
    from src.clean import load_topics,load_qrels
    from demo.app.retrieval import Engine
    topics_path=download(raw,'topics2022.xml',TOPICS)
    topics=load_topics(topics_path);topics_sha=digest(topics_path)
    if set(topics)!=set(TOPIC_IDS):raise ValueError('All 50 official topics required')
    if args.stage=='rank':
        engine=Engine(root/'data/indexes/bm25.sqlite',config=protocol['config']);engine.load()
        for topic in TOPIC_IDS:
            path=output/f'rankings/{topic}.json'
            if path.exists():
                previous=json.loads(path.read_text())
                if previous['freeze_signature']!=key or previous['topics_sha256']!=topics_sha:
                    raise RuntimeError('Incompatible ranking checkpoint')
                print(f'Topic {topic}: existing checkpoint reused',flush=True);continue
            row=dict(topic=topic,freeze_signature=key,topics_sha256=topics_sha,arms={})
            for arm,method in ARMS.items():
                result=engine.search(topics[topic],method=method,top_k=200)
                ids=[r['trial_id'] for r in result['results']]
                if len(ids)!=200 or len(set(ids))!=200 or result['rerank_scored_pairs']!=0:
                    raise RuntimeError('Expected 200 unique candidates and no neural scores')
                row['arms'][arm]=dict(ranking=ids,scores={r['trial_id']:r['retrieval_score'] for r in result['results']},seconds=result['seconds'])
                print(f'Topic {topic}/50 {arm}: 200 trials, {result["seconds"]:.3f}s',flush=True)
            save(path,row);time.sleep(.25)
        print('All 50 ranking checkpoints complete; qrels not opened by rank stage',flush=True);return
    rows={topic:json.loads((output/f'rankings/{topic}.json').read_text()) for topic in TOPIC_IDS}
    for row in rows.values():
        if row['freeze_signature']!=key or row['topics_sha256']!=topics_sha:
            raise RuntimeError('Incompatible ranking protocol')
    qrels_path=download(raw,'qrels2022.txt',QRELS);judgments=load_qrels(qrels_path)
    if set(judgments)!=set(TOPIC_IDS):raise ValueError('Judgment topics differ')
    from src.evaluate import metrics,check_reference
    measured={};exports={arm:[] for arm in ARMS}
    for topic,row in rows.items():
        measured[topic]={}
        for arm in ARMS:
            ranking=row['arms'][arm]['ranking']
            if len(ranking)!=200 or len(set(ranking))!=200:raise ValueError('Invalid ranking')
            result=metrics(ranking,judgments[topic]);check_reference(ranking,judgments[topic],result)
            measured[topic][arm]=result
            exports[arm].extend(f'{topic} Q0 {trial} {i} {201-i} {arm}' for i,trial in enumerate(ranking,1))
    macro={arm:{} for arm in ARMS}
    for arm in ARMS:
        for metric in protocol['metrics']:
            values=[row[arm][metric] for row in measured.values() if row[arm][metric] is not None]
            macro[arm][metric]=statistics.mean(values) if values else None
    paired={}
    for metric in ['ndcg@10','eligible_recall@10','eligible_recall@200']:
        deltas=[row['balanced_fusion'][metric]-row['bm25'][metric] for row in measured.values() if row['bm25'][metric] is not None]
        paired[metric]=dict(mean_delta=statistics.mean(deltas),improved=sum(d>1e-12 for d in deltas),unchanged=sum(abs(d)<=1e-12 for d in deltas),worsened=sum(d < -1e-12 for d in deltas))
    timings={arm:dict(median_seconds=statistics.median(row['arms'][arm]['seconds'] for row in rows.values()),
        total_seconds=sum(row['arms'][arm]['seconds'] for row in rows.values())) for arm in ARMS}
    report=dict(freeze_signature=key,topics_sha256=topics_sha,qrels_sha256=digest(qrels_path),topics=TOPIC_IDS,
        macro=macro,per_topic=measured,paired_fusion_minus_bm25=paired,timings=timings,
        eligible_recall_topics=sum(row['bm25']['eligible_total']>0 for row in measured.values()),
        verification='All 100 patient/arm metric sets independently checked against pytrec_eval.',
        timing_note='Engine.search includes result hydration; same process/index, fusion then BM25 per patient; warm-cache effects apply. No concurrent experiments.')
    save(output/'metrics.json',report)
    for arm,lines in exports.items():(output/f'{arm}.trec').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({k:report[k] for k in ['macro','paired_fusion_minus_bm25','timings','eligible_recall_topics']},indent=2),flush=True)


if __name__=='__main__':main()
