"""User-capped cached 5,120-trial pilot; all arms search the same subset."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import random
import os
import ctypes
import shutil
import sqlite3
import time

from experiments.medcpt_hybrid.run import (
    ROOT, MODEL_ROOT, FIELDS, Encoder, low_priority, peak_mib, save, read, digest,
    exact_top, evaluate_rankings)

OUT = ROOT/'reports/medcpt-hybrid-pilot-5120-v1'
CACHE = ROOT/'data/cache/medcpt-hybrid-pilot-5120-v1'
PARENT_OUT = ROOT/'reports/medcpt-hybrid-pilot-v1'
PARENT_CACHE = ROOT/'data/cache/medcpt-hybrid-pilot-v1'
INDEX = CACHE/'pilot.sqlite'
TOPICS = ['1','2','3','4','5']
SEED = 941996
COUNT = 5120


def sample_ids(ids, count=COUNT):
    """No relevance labels, patient text, or retrieval scores influence selection."""
    return sorted(random.Random(SEED).sample(sorted(ids),count))


def prepare():
    OUT.mkdir(parents=True,exist_ok=True); CACHE.mkdir(parents=True,exist_ok=True)
    split=read(ROOT/'experiments/retrieval_inputs/results/protocol.json')
    assert set(TOPICS).issubset(split['development_topics'])
    with closing(sqlite3.connect((ROOT/'data/indexes/bm25.sqlite').as_uri()+'?mode=ro',uri=True)) as source:
        source.execute('PRAGMA cache_size=-16384')
        all_ids=dict(source.execute('SELECT trial_id,row_id FROM identities'))
        if len(all_ids)!=375580: raise RuntimeError('Wrong historical corpus')
        selected=read(PARENT_OUT/'sample-ids.json')[:COUNT]
        p=dict(scope='Exploratory subset pilot, not full-corpus performance',year=2021,
            sample_count=COUNT,topics=TOPICS,seed=SEED,
            sampling='User-capped sorted-ID prefix of original uniform random 20,000 sample. Independent of labels, but biased toward earlier trial IDs; not representative.',
            parent_protocol_sha256=digest(PARENT_OUT/'protocol.json'),
            selected_ids_sha256=__import__('hashlib').sha256('\n'.join(selected).encode()).hexdigest(),
            eligibility_denominator='Grade-2 judgments intersected with selected trial IDs; zero-positive recall excluded from macro',
            selection='Among hybrid vector weights .25,.5,1,2 choose macro eligible recall@200, then nDCG@10, then name. Five reused patients; exploratory.',
            lexical='Recompute BM25 and weighted three-query searches against subset FTS; inner RRF k60; balanced outer weights1,2; top1000',
            vector='MedCPT Article/Query CLS embeddings, dynamic-int8 per-channel ONNX, unnormalized float32 dot product; top1000 exact',
            fields=FIELDS,query_max_tokens=128,article_max_tokens=256,batch_size=4,
            reranker='Apache-2.0 cross-encoder/ms-marco-MiniLM-L2-v2 quantized ONNX; pure score order of fixed200',
            reranker_manifest=read(ROOT/'models/paper/manifest.json')['reranker'],
            promotion='No automatic expansion. Five-query functional screen; report hybrid recall and reranker differences for review.',
            execution='One process, BelowNormal priority, numeric and inference threads1, no GPU, no paid API',
            model_manifests={kind:read(MODEL_ROOT/kind/'manifest.json') for kind in ['article','query']},
            onnx_manifests={kind:read(MODEL_ROOT/kind/'onnx-manifest.json') for kind in ['article','query']},
            code_sha256={name:digest(ROOT/'experiments/medcpt_hybrid'/name) for name in ['pilot.py','run.py','optimize.py']},
            historical_index_sha256=digest(ROOT/'data/indexes/bm25.sqlite'),
            input_sha256={str(path.relative_to(ROOT)):digest(path) for path in [
                ROOT/'data/raw/trec2021/topics2021.xml',ROOT/'data/raw/trec2021/qrels2021.txt',
                ROOT/'demo/app/retrieval.py',ROOT/'demo/app/rerank.py']})
        if (OUT/'protocol.json').exists() and read(OUT/'protocol.json')!=p:
            raise RuntimeError('Changed pilot protocol; use a new output directory')
        save(OUT/'protocol.json',p);save(OUT/'sample-ids.json',selected)
        if not INDEX.exists():
            temp=INDEX.with_suffix('.tmp')
            # A failed build is recreated in this exact experiment-owned file.
            if temp.exists(): temp.unlink()
            with closing(sqlite3.connect(temp)) as db:
                db.execute('PRAGMA cache_size=-16384')
                db.execute("CREATE VIRTUAL TABLE trials USING fts5(trial_id UNINDEXED,title,conditions,summary,eligibility,tokenize='porter unicode61')")
                db.execute('CREATE TABLE identities(trial_id TEXT PRIMARY KEY,row_id INTEGER UNIQUE NOT NULL)')
                for offset in range(0,len(selected),256):
                    part=selected[offset:offset+256]
                    rows=source.execute('SELECT trial_id,title,conditions,summary,eligibility FROM trials WHERE rowid IN ('+
                        ','.join('?' for _ in part)+')',[all_ids[d] for d in part]).fetchall()
                    for row in sorted(rows):
                        cur=db.execute('INSERT INTO trials VALUES(?,?,?,?,?)',row)
                        db.execute('INSERT INTO identities VALUES(?,?)',(row[0],cur.lastrowid))
                db.commit();db.execute("INSERT INTO trials(trials) VALUES('optimize')");db.commit()
                if db.execute('SELECT count(*) FROM identities').fetchone()[0]!=COUNT:
                    raise RuntimeError('Subset build count mismatch')
            temp.replace(INDEX)
    with closing(sqlite3.connect(INDEX)) as db:
        actual=sorted(d for d, in db.execute('SELECT trial_id FROM identities'))
    if actual!=selected: raise RuntimeError('Subset identity mismatch')
    print(f'Prepared fixed user-capped {COUNT:,}-trial subset',flush=True)
    return p


def cap_single_cpu():
    if os.name=='nt':
        kernel=ctypes.windll.kernel32
        kernel.GetCurrentProcess.restype=ctypes.c_void_p
        kernel.GetProcessAffinityMask.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_size_t),ctypes.POINTER(ctypes.c_size_t)]
        kernel.SetProcessAffinityMask.argtypes=[ctypes.c_void_p,ctypes.c_size_t]
        process=kernel.GetCurrentProcess();mask=ctypes.c_size_t();system=ctypes.c_size_t()
        if not kernel.GetProcessAffinityMask(process,ctypes.byref(mask),ctypes.byref(system)):raise ctypes.WinError()
        chosen=1<<(mask.value.bit_length()-1)
        if not kernel.SetProcessAffinityMask(process,chosen):raise ctypes.WinError()
        print(f'Continuous single-CPU cap: PID={os.getpid()}, affinity={chosen}; automatic pausing disabled',flush=True)
    elif hasattr(os,'sched_getaffinity'):
        os.sched_setaffinity(0,{max(os.sched_getaffinity(0))})


def reuse_existing():
    import numpy as np
    p=prepare();parent=read(PARENT_OUT/'protocol.json')
    for key in ['fields','batch_size','model_manifests','onnx_manifests','historical_index_sha256']:
        if parent[key]!=p[key]:raise RuntimeError(f'Existing vectors incompatible: {key}')
    selected=read(OUT/'sample-ids.json')
    for offset in range(0,COUNT,256):
        name=f'{offset:06d}.npz';source=PARENT_CACHE/name
        with np.load(source,allow_pickle=False) as shard:
            if list(shard['ids'])!=selected[offset:offset+256] or shard['vectors'].shape!=(256,768) or not np.isfinite(shard['vectors']).all():
                raise RuntimeError('Existing vector shard mismatch')
        dest=CACHE/name
        if not dest.exists():shutil.copyfile(source,dest)
        if digest(dest)!=digest(source):raise RuntimeError('Vector copy checksum mismatch')
    save(CACHE/'complete.json',dict(records=COUNT,protocol_sha256=digest(OUT/'protocol.json'),
        shards={path.name:digest(path) for path in sorted(CACHE.glob('*.npz'))}))
    save(OUT/'encoding-progress.json',dict(completed=COUNT,total=COUNT,new_this_run=0,reused=COUNT,
        precision='unchanged cached dynamic-int8 MedCPT outputs',excluded_parent_records=256))
    print(f'Reused {COUNT:,} verified vectors; no trial encoding performed',flush=True)


def encode():
    import numpy as np
    prepare(); model=Encoder('Article'); done=0; new=0; started=time.perf_counter()
    with closing(sqlite3.connect(INDEX)) as db:
        rows=db.execute('SELECT trial_id,title,conditions,summary,eligibility FROM trials ORDER BY trial_id')
        while batch:=rows.fetchmany(256):
            dest=CACHE/f'{done:06d}.npz';ids=[r[0] for r in batch]
            if dest.exists():
                with np.load(dest,allow_pickle=False) as previous:
                    if list(previous['ids'])!=ids or previous['vectors'].shape!=(len(ids),768):
                        raise RuntimeError('Bad checkpoint')
            else:
                tick=time.perf_counter()
                vectors=np.concatenate([model.articles([r[1:] for r in batch[i:i+4]])
                                        for i in range(0,len(batch),4)])
                with dest.with_suffix('.tmp').open('wb') as stream: np.savez(stream,ids=np.asarray(ids),vectors=vectors)
                dest.with_suffix('.tmp').replace(dest);new+=len(batch)
                elapsed=time.perf_counter()-started
                progress=dict(completed=done+len(batch),total=COUNT,new_this_run=new,
                    seconds_this_run=elapsed,records_per_second=new/elapsed,
                    estimated_remaining_minutes=(COUNT-done-len(batch))/(new/elapsed)/60,
                    last_shard_seconds=time.perf_counter()-tick,peak_process_mib=peak_mib())
                save(OUT/'encoding-progress.json',progress);print(json.dumps(progress),flush=True)
            done+=len(batch)
    if done!=COUNT: raise RuntimeError('Incomplete embeddings')
    save(CACHE/'complete.json',dict(records=done,protocol_sha256=digest(OUT/'protocol.json'),
        shards={path.name:digest(path) for path in sorted(CACHE.glob('*.npz'))}))


def retrieval():
    import numpy as np
    from src.clean import load_topics,load_qrels
    from demo.app.retrieval import search_fts,rank_fusion,focused,opening
    prepare(); complete=read(CACHE/'complete.json')
    if complete['records']!=COUNT or complete['protocol_sha256']!=digest(OUT/'protocol.json'):
        raise RuntimeError('Wrong embedding protocol')
    for name,sha in complete['shards'].items():
        if digest(CACHE/name)!=sha: raise RuntimeError('Corrupt embedding shard')
    topics=load_topics(ROOT/'data/raw/trec2021/topics2021.xml')
    full_qrels=load_qrels(ROOT/'data/raw/trec2021/qrels2021.txt')
    selected=set(read(OUT/'sample-ids.json'))
    qrels={t:{d:g for d,g in full_qrels[t].items() if d in selected} for t in TOPICS}
    save(OUT/'judgment-coverage.json',{t:dict(subset_eligible=sum(g==2 for g in qrels[t].values()),
        full_eligible=sum(g==2 for g in full_qrels[t].values()),subset_judged=len(qrels[t])) for t in TOPICS})
    model=Encoder('Query'); lexical={};dense={};timing={}
    vocab=read(ROOT/'demo/app/assets/condition_vocabulary.json')
    with closing(sqlite3.connect(INDEX)) as db:
        db.execute('PRAGMA cache_size=-16384')
        for t in TOPICS:
            tick=time.perf_counter(); note=topics[t]
            inner=rank_fusion([search_fts(db,q,1000,True) for q in [note,focused(note,vocab),opening(note)]],1000)
            lexical[t]=rank_fusion([search_fts(db,note,1000),inner],1000,[1.,2.])
            lexical_seconds=time.perf_counter()-tick
            tick=time.perf_counter(); query=model.query(note);best=[]
            for name in sorted(complete['shards']):
                with np.load(CACHE/name,allow_pickle=False) as shard:
                    hits=exact_top(shard['vectors']@query,list(shard['ids']))
                best=sorted(best+hits,key=lambda x:(-x[1],x[0]))[:1000]
            dense[t]=best;timing[t]=dict(lexical_seconds=lexical_seconds,dense_seconds=time.perf_counter()-tick)
            print(f'Topic {t} lexical + dense retrieval complete',flush=True)
    arms={'balanced_fusion':lexical,'medcpt_dense':dense}
    for weight in [.25,.5,1.,2.]:
        arms[f'hybrid_{weight:g}']={t:rank_fusion([lexical[t],dense[t]],1000,[1.,weight]) for t in TOPICS}
    results={name:evaluate_rankings(ranks,qrels) for name,ranks in arms.items()}
    winner=sorted([name for name in arms if name.startswith('hybrid_')],
        key=lambda name:(-results[name]['macro']['eligible_recall@200'],-results[name]['macro']['ndcg@10'],name))[0]
    for name,ranks in arms.items(): save(OUT/f'rankings/{name}.json',ranks)
    save(OUT/'retrieval-results.json',dict(scope='5,120-trial sorted-prefix subset; five reused development patients',
        selected_hybrid=winner,arms=results,timing=timing,peak_process_mib=peak_mib(),
        zero_eligible_topics=[t for t in TOPICS if not any(g==2 for g in qrels[t].values())],
        timing_scope='First-run sequential queries, model loading excluded; dense includes vector shard disk reads; not a p95 estimate'))


def rerank():
    from src.clean import load_topics,load_qrels
    from demo.app.rerank import CrossEncoder,trial_document,ordered_candidates
    prepare(); result=read(OUT/'retrieval-results.json');winner=result['selected_hybrid']
    ranks=read(OUT/f'rankings/{winner}.json')
    topics=load_topics(ROOT/'data/raw/trec2021/topics2021.xml')
    selected=set(read(OUT/'sample-ids.json'));full=load_qrels(ROOT/'data/raw/trec2021/qrels2021.txt')
    qrels={t:{d:g for d,g in full[t].items() if d in selected} for t in TOPICS}
    manifest=read(ROOT/'models/paper/manifest.json')['reranker']
    for name,sha in manifest['files'].items():
        if digest(ROOT/'models/paper/reranker'/name)!=sha: raise RuntimeError('Reranker checksum mismatch')
    encoder=CrossEncoder(ROOT/'models/paper/reranker',threads=1);after={};timing={}
    with closing(sqlite3.connect(INDEX)) as db:
        for t in TOPICS:
            ids=sorted(d for d,_ in ranks[t][:200]);dest=OUT/f'rerank-cache/{t}.json'
            if dest.exists():
                cached=read(dest)
                if cached['ids']!=ids or cached['selected_hybrid']!=winner: raise RuntimeError('Changed scoring pool')
            else:
                tick=time.perf_counter()
                scores=encoder.predict(topics[t],[trial_document(db,d) for d in ids],batch_size=8)
                cached=dict(ids=ids,selected_hybrid=winner,scores=dict(zip(ids,scores,strict=True)),seconds=time.perf_counter()-tick)
                save(dest,cached)
            order=ordered_candidates(ids,cached['scores']);after[t]=[(d,cached['scores'][d]) for d in order]
            timing[t]=cached['seconds']; print(f'Reranked all 200 candidates for patient {t}: {timing[t]:.2f}s',flush=True)
    evaluated=evaluate_rankings(after,qrels);before=result['arms'][winner];baseline=result['arms']['balanced_fusion']
    for t in TOPICS:
        assert evaluated['per_topic'][t]['eligible_recall@200']==before['per_topic'][t]['eligible_recall@200']
    promising=before['macro']['eligible_recall@200']>baseline['macro']['eligible_recall@200'] and \
        evaluated['macro']['ndcg@10']>=before['macro']['ndcg@10']+.01 and \
        evaluated['macro']['eligible_recall@10']>=before['macro']['eligible_recall@10']
    save(OUT/'rerank-results.json',dict(selected_hybrid=winner,metrics=evaluated,seconds=timing,
        peak_process_mib=peak_mib(),promising_for_expansion=promising))
    save(OUT/'rankings/reranked.json',after)
    rows=['# MedCPT hybrid pilot results','','Exploratory: five reused 2021 development patients, 5,120-trial sorted prefix of the original 20,000-trial random sample.',
        'This user-capped subset favours earlier trial IDs; it is not representative. Only eight judged eligible trials remain, across three patients.',
        'These scores cannot be compared directly with the earlier full-corpus benchmark. Eligibility is not assessed.',
        'All recall denominators use judged eligible trials present in the subset. No 2022 data accessed.','',
        '| Configuration | nDCG@10 | Eligible recall@10 | Eligible recall@200 | Eligible precision@10 |',
        '|---|---:|---:|---:|---:|']
    combined={name:value['macro'] for name,value in result['arms'].items()}
    combined[winner+' + MiniLM-L2']=evaluated['macro']
    for name,m in combined.items():
        rows.append(f"| {name} | {m['ndcg@10']:.4f} | {m['eligible_recall@10']:.2%} | {m['eligible_recall@200']:.2%} | {m['eligible@10']/10:.2%} |")
    rows+=['',f'Selected hybrid: {winner}. Prespecified expansion gate passed: {promising}.',
        'Weight selection and reporting use the same five patients; this is a screening result, not an independent estimate.',
        'See protocol.json, judgment-coverage.json, encoding-progress.json and per-patient result JSON files for provenance, coverage, timing and memory.']
    (OUT/'README.md').write_text('\n'.join(rows)+'\n',encoding='utf-8')
    print('\n'.join(rows),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage',choices=['prepare','reuse_existing','retrieval','rerank','cached'],required=True)
    args=parser.parse_args();low_priority();cap_single_cpu()
    OUT.mkdir(parents=True,exist_ok=True)
    def log(message):
        from datetime import datetime,timezone
        line=f'[{datetime.now(timezone.utc).isoformat(timespec="seconds")}] {message}'
        with (OUT/'live.log').open('a',encoding='utf-8') as stream:stream.write(line+'\n')
        print(line,flush=True)
    stages=['reuse_existing','retrieval','rerank'] if args.stage=='cached' else [args.stage]
    try:
        for stage in stages:
            save(OUT/'execution.json',dict(stage=stage,finished=False,pid=os.getpid()))
            log('Starting '+stage);globals()[stage]();log('Finished '+stage)
        save(OUT/'execution.json',dict(stage='complete',finished=True,pid=os.getpid()))
        log('All requested stages complete')
    except Exception as error:
        save(OUT/'execution.json',dict(stage='failed',finished=False,error=repr(error),pid=os.getpid()))
        log('FAILED: '+repr(error));raise


if __name__=='__main__': main()
