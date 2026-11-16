"""Full-corpus retrieval experiments; writes only to --output, never to --project."""
from __future__ import annotations
import argparse
from collections import Counter
from contextlib import closing
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import random
import re
import sqlite3
import sys
import time

sys.dont_write_bytecode=True
os.environ.setdefault('TOKENIZERS_PARALLELISM','false')
os.environ.setdefault('OPENBLAS_NUM_THREADS','4')
os.environ.setdefault('OMP_NUM_THREADS','4')

MODEL='NeuML/pubmedbert-base-embeddings-8M'
REVISION='b6a0de4964b05d69fea61532cd6ae1241a3ee9fd'
SEED=941996
WEIGHTS='bm25(0.0,3.0,5.0,1.0,0.5)'
POOL=1000

def save(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    temp.replace(path)

def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(4*1024*1024),b''):h.update(b)
    return h.hexdigest()

def words(text):return re.findall(r'[a-z0-9]+',text.lower())

def opening(note):
    # Fixed lexical heuristic, not a clinical diagnosis extractor.
    return ' '.join(re.split(r'(?<=[.!?])\s+(?=[A-Z])',note,maxsplit=1)[0].split()[:60])

def focused(note,vocab):
    tokens=words(note); found=[]
    for i in range(len(tokens)):
        for n in range(min(10,len(tokens)-i),0,-1):
            p=' '.join(tokens[i:i+n])
            if p in vocab:
                found.append((i,i+n,p,vocab[p]));break
    # Longer condition phrases first, then corpus rarity, then note order.
    chosen=[];occupied=set()
    for i,j,p,df in sorted(found,key=lambda x:(-(x[1]-x[0]),x[3],x[0])):
        if occupied.intersection(range(i,j)) or any(p==x[2] for x in chosen):continue
        chosen.append((i,j,p,df));occupied.update(range(i,j))
        if len(chosen)==6:break
    return ' '.join(x[2] for x in sorted(chosen)) if chosen else opening(note)

def fts(db,note,query_terms,weighted=False,k=POOL):
    tokens=query_terms(note)
    if not tokens:return []
    query=' OR '.join('"'+t+'"' for t in tokens)
    limit=k+1
    while True:
        sql='SELECT trial_id,rank FROM trials WHERE trials MATCH ?'
        args=[query]
        if weighted:sql+=' AND rank MATCH ?';args.append(WEIGHTS)
        rows=db.execute(sql+' ORDER BY rank LIMIT ?',[*args,limit]).fetchall()
        if len(rows)<=k or len(rows)<limit or rows[-1][1]!=rows[k-1][1]:break
        limit*=2
    rows.sort(key=lambda x:(x[1],x[0]))
    return [[r[0],-r[1]] for r in rows[:k]]

def rrf(lists,weights=None,k=POOL):
    weights=weights or [1.0]*len(lists)
    scores=Counter()
    for ranking,w in zip(lists,weights,strict=True):
        if len({r[0] for r in ranking})!=len(ranking):raise ValueError('Duplicate candidate')
        for rank,(trial,_) in enumerate(ranking,1):scores[trial]+=w/(60+rank)
    return [[d,s] for d,s in sorted(scores.items(),key=lambda x:(-x[1],x[0]))[:k]]

def exact_top(scores,ids,k=POOL):
    import numpy as np
    if not np.isfinite(scores).all():raise ValueError('Nonfinite cosine scores')
    k=min(k,len(scores))
    cutoff=np.partition(scores,len(scores)-k)[len(scores)-k]
    chosen=np.flatnonzero(scores>=cutoff)
    chosen=sorted(chosen,key=lambda i:(-float(scores[i]),ids[i]))[:k]
    return [[ids[i],float(scores[i])] for i in chosen]

def aggregate(per_topic):
    return {k:sum(x[k] for x in per_topic.values() if x[k] is not None)/sum(x[k] is not None for x in per_topic.values())
            if any(x[k] is not None for x in per_topic.values()) else None for k in next(iter(per_topic.values()))}

def bootdiff(base,new,key):
    import numpy as np
    delta=np.array([new[t][key]-base[t][key] for t in base])
    rng=np.random.default_rng(SEED)
    draws=delta[rng.integers(0,len(delta),size=(10000,len(delta)))].mean(axis=1)
    lo,hi=np.quantile(draws,[.025,.975])
    return dict(mean_difference=float(delta.mean()),paired_bootstrap_95pct=[float(lo),float(hi)],replicates=10000,seed=SEED)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--deps',type=Path)
    parser.add_argument('--prepare-only',action='store_true')
    a=parser.parse_args();project=a.project.resolve();out=a.output.resolve()
    if out==project or project in out.parents:raise ValueError('Output must be outside the read-only project')
    out.mkdir(parents=True,exist_ok=True)
    if a.deps:sys.path.insert(0,str(a.deps.resolve()))
    sys.path.insert(0,str(project))
    from src.clean import load_topics,load_qrels
    from src.evaluate import metrics,check_reference
    from src.retrieve import query_terms
    import numpy as np
    import pyarrow.parquet as pq
    topics=load_topics(project/'data/raw/trec2021/topics2021.xml')
    remaining=sorted((t for t in topics if int(t)>10),key=int)
    random.Random(SEED).shuffle(remaining)
    dev=sorted([str(i) for i in range(1,11)]+remaining[:35],key=int)
    validation=sorted(remaining[35:],key=int)
    assert len(dev)==45 and len(validation)==30 and not set(dev)&set(validation)
    assert set(dev)|set(validation)==set(topics)
    protocol=dict(seed=SEED,development_topics=dev,validation_topics=validation,
        prior_exposure='Topics 1-10 previously inspected; forced into development.',
        collection='All 375,580 TREC 2021 snapshot trials. TREC 2022 not accessed.',
        lexical_arms=['baseline','weighted_full','focused','weighted_focused','weighted_opening','lexical_rrf'],
        lexical_rrf='Equal RRF of weighted_full, weighted_focused and weighted_opening; constant 60.',
        field_weights=[0,3,5,1,.5],pool_depth=POOL,evaluation_depth=200,
        focus='Up to six nonoverlapping registry-condition phrases, longest then rarest, df 3..50000; fallback opening 60 words. No clinical inference or negation resolution.',
        dense=dict(model=MODEL,revision=REVISION,kind='Static biomedical embeddings distilled from PubMedBERT, not contextual transformer inference',
            document='title + conditions + first 256 whitespace words of brief summary',query='opening sentence, capped at 60 whitespace words',
            similarity='L2-normalized float32 vectors; exact full-corpus cosine search; ties trial ID'),
        hybrid_dense_weights=[.5,1.,2.],hybrid='RRF of development-selected lexical arm and dense; constant 60.',
        selection='Maximize development macro eligible recall@200; tie-break nDCG@10 then arm name. Freeze winners before validation metrics.',
        metrics='Linear nDCG gains 0/1/2, eligible recall positives grade 2, unjudged zero gain.',
        caveat='Internal validation, not final held-out performance; no further tuning after validation inspection.',
        source_fingerprints={'experiment.py':digest(__file__),'topics':digest(project/'data/raw/trec2021/topics2021.xml'),'qrels':digest(project/'data/raw/trec2021/qrels2021.txt')})
    protocol_path=out/'protocol.json'
    if protocol_path.exists() and json.loads(protocol_path.read_text())!=protocol:raise ValueError('Protocol changed; choose a new output directory')
    save(protocol_path,protocol)
    print(f'Frozen split: {len(dev)} development / {len(validation)} validation patients',flush=True)
    if a.prepare_only:return
    started=time.perf_counter()
    vocab_path=out/'cache/condition_vocabulary.json'
    if vocab_path.exists():vocab=json.loads(vocab_path.read_text())
    else:
        counts=Counter()
        for batch in pq.ParquetFile(project/'data/clean/trials.parquet').iter_batches(batch_size=4096,columns=['conditions']):
            for row in batch.to_pylist():counts.update({' '.join(words(c)) for c in row['conditions'] if c.strip()})
        vocab={p:df for p,df in counts.items() if 3<=df<=50000 and 3<=len(p) and 1<=len(p.split())<=10}
        save(vocab_path,vocab)
    queries={t:dict(full=topics[t],focus=focused(topics[t],vocab),opening=opening(topics[t])) for t in topics}
    save(out/'queries.json',queries)
    qrels=load_qrels(project/'data/raw/trec2021/qrels2021.txt')
    timings={}
    db=sqlite3.connect((project/'data/indexes/bm25.sqlite').as_uri()+'?mode=ro',uri=True)
    db.execute('PRAGMA cache_size=-131072')
    meta={k:json.loads(v) for k,v in db.execute('SELECT key,value FROM metadata')}
    assert meta['records']==375580
    def lexical(t,arm):
        path=out/f'cache/{arm}/{t}.json'
        if path.exists():return json.loads(path.read_text())
        tick=time.perf_counter()
        if arm=='lexical_rrf':hits=rrf([lexical(t,n) for n in ['weighted_full','weighted_focused','weighted_opening']])
        else:
            mode={'baseline':'full','weighted_full':'full','focused':'focus','weighted_focused':'focus','weighted_opening':'opening'}[arm]
            hits=fts(db,queries[t][mode],query_terms,weighted=arm.startswith('weighted'))
        seconds=time.perf_counter()-tick
        save(path,hits);save(path.with_suffix('.timing.json'),dict(seconds=seconds))
        return hits
    def score(t,hits):
        ids=[r[0] for r in hits[:200]]
        row=metrics(ids,qrels[t]);check_reference(ids,qrels[t],row)
        return row
    dev_metrics={}
    for arm in protocol['lexical_arms']:
        per={}
        for i,t in enumerate(dev,1):
            per[t]=score(t,lexical(t,arm))
            if i%10==0:print(f'{arm}: {i}/{len(dev)} dev topics',flush=True)
        dev_metrics[arm]=per
        save(out/'development_metrics.json',{k:dict(macro=aggregate(v),per_topic=v) for k,v in dev_metrics.items()})
        print(arm,aggregate(per),flush=True)
    def select(arms):return sorted(arms,key=lambda arm:(-aggregate(dev_metrics[arm])['eligible_recall@200'],-aggregate(dev_metrics[arm])['ndcg@10'],arm))[0]
    best_lex=select(protocol['lexical_arms'])
    save(out/'lexical_selection.json',dict(selected=best_lex,development_only=True))
    print('Selected lexical arm on development:',best_lex,flush=True)
    # Model download/cache is confined to output. The model revision is pinned.
    os.environ['HF_HOME']=str(out/'cache/huggingface')
    from model2vec import StaticModel
    from huggingface_hub import snapshot_download
    model_path=snapshot_download(MODEL,revision=REVISION,cache_dir=str(out/'cache/huggingface'),allow_patterns=['config.json','model.safetensors','tokenizer.json'])
    model=StaticModel.from_pretrained(model_path)
    print('Loaded pinned biomedical static model',flush=True)
    vectors_path=out/'cache/trial_vectors.npy';ids_path=out/'cache/trial_ids.json'
    if not vectors_path.exists():
        tick=time.perf_counter();vectors=None;ids=[];offset=0
        temp=vectors_path.with_suffix('.partial.npy')
        for batch in pq.ParquetFile(project/'data/clean/trials.parquet').iter_batches(batch_size=2048,columns=['trial_id','title','conditions','summary']):
            rows=batch.to_pylist()
            texts=[r['title']+'\n'+'; '.join(r['conditions'])+'\n'+' '.join(r['summary'].split()[:256]) for r in rows]
            v=np.asarray(model.encode(texts),dtype=np.float32)
            v/=np.maximum(np.linalg.norm(v,axis=1,keepdims=True),1e-12)
            assert np.isfinite(v).all()
            if vectors is None:vectors=np.lib.format.open_memmap(temp,mode='w+',dtype='float32',shape=(375580,v.shape[1]))
            vectors[offset:offset+len(v)]=v;ids.extend(r['trial_id'] for r in rows);offset+=len(v)
            if offset//25000!=(offset-len(v))//25000:print(f'Embedded {offset:,}/375,580 trials',flush=True)
        assert offset==375580 and len(set(ids))==375580
        vectors.flush();del vectors
        temp.replace(vectors_path);save(ids_path,ids)
        save(out/'embedding_build.json',dict(seconds=time.perf_counter()-tick,trials=offset,dimensions=int(v.shape[1]),model=MODEL,revision=REVISION,
             model_files_sha256={p.name:digest(p) for p in Path(model_path).iterdir() if p.is_file()},numpy=np.__version__,parquet_sha256=meta['parquet_sha256']))
    vectors=np.load(vectors_path,mmap_mode='r');ids=json.loads(ids_path.read_text())
    assert len(ids)==len(vectors)==375580
    def dense(t):
        path=out/f'cache/dense/{t}.json'
        if path.exists():return json.loads(path.read_text())
        tick=time.perf_counter()
        q=np.asarray(model.encode([queries[t]['opening']]),dtype=np.float32)[0]
        q/=max(float(np.linalg.norm(q)),1e-12)
        scores=np.empty(len(ids),dtype=np.float32)
        for start in range(0,len(ids),16384):scores[start:start+16384]=vectors[start:start+16384]@q
        hits=exact_top(scores,ids)
        save(path,hits);save(path.with_suffix('.timing.json'),dict(seconds=time.perf_counter()-tick))
        return hits
    def hybrid(t,w):return rrf([lexical(t,best_lex),dense(t)],[1.,w])
    dev_metrics['dense']={t:score(t,dense(t)) for t in dev}
    for w in protocol['hybrid_dense_weights']:
        arm=f'hybrid_{w:g}'
        dev_metrics[arm]={t:score(t,hybrid(t,w)) for t in dev}
        print(arm,aggregate(dev_metrics[arm]),flush=True)
    best_hybrid=select([f'hybrid_{w:g}' for w in protocol['hybrid_dense_weights']])
    selection=dict(lexical=best_lex,hybrid=best_hybrid,objective=protocol['selection'],frozen_before_validation=True)
    save(out/'selection.json',selection)
    save(out/'development_metrics.json',{k:dict(macro=aggregate(v),per_topic=v) for k,v in dev_metrics.items()})
    print('Frozen development winners:',selection,flush=True)
    val_metrics={};val_rankings={}
    for arm in dict.fromkeys(['baseline',best_lex,'dense',best_hybrid]):
        per={};runs={}
        for i,t in enumerate(validation,1):
            hits=dense(t) if arm=='dense' else hybrid(t,float(arm.split('_')[1])) if arm.startswith('hybrid_') else lexical(t,arm)
            per[t]=score(t,hits);runs[t]=hits[:200]
            if i%10==0:print(f'{arm}: {i}/{len(validation)} validation topics',flush=True)
        val_metrics[arm]=per;val_rankings[arm]=runs
        print('VALIDATION',arm,aggregate(per),flush=True)
    db.close()
    comparison={arm:{key:bootdiff(val_metrics['baseline'],rows,key) for key in ['ndcg@10','eligible_recall@10','eligible_recall@30','eligible_recall@200']} for arm,rows in val_metrics.items() if arm!='baseline'}
    save(out/'validation_metrics.json',{k:dict(macro=aggregate(v),per_topic=v) for k,v in val_metrics.items()})
    save(out/'validation_rankings.json',val_rankings)
    save(out/'paired_comparisons.json',comparison)
    save(out/'run_manifest.json',dict(seconds=time.perf_counter()-started,selection=selection,index_metadata=meta,
        packages={name:importlib.metadata.version(name) for name in ['numpy','model2vec','tokenizers','safetensors','huggingface_hub','pyarrow','pytrec-eval-terrier']},
        code_sha256=digest(__file__),llm_calls=0,paid_api_calls=0,project_writes=False,
        verification='Every per-topic nDCG and eligible recall independently checked with pytrec_eval. Full corpus exact cosine search.'))
    print('EXPERIMENT COMPLETE',flush=True)

if __name__=='__main__':main()
