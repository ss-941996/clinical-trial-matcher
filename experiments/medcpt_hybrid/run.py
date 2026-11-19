"""Sequential, checkpointed MedCPT retrieval on the complete TREC 2021 corpus.

Run with --stage download, encode, retrieval, or rerank. No serving configuration
is modified. All model inference and exact vector scans use one numeric thread.
"""
from __future__ import annotations
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

for name in ['OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'NUMEXPR_NUM_THREADS']:
    os.environ[name] = '1'
os.environ['TOKENIZERS_PARALLELISM'] = 'false'
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
REVISIONS = {'Article': 'd05a736da4bb84ee4057b7f7999485be6ed85465',
             'Query': 'd83a36cc6b8e3a5c5e9d9d6ba156808c1643dcbc'}
MODEL_ROOT = ROOT/'models/medcpt-retrieval'
OUT = ROOT/'reports/medcpt-hybrid-v1'
CACHE = ROOT/'data/cache/medcpt-hybrid-v1'
FIELDS = {'title': 48, 'conditions': 48, 'summary': 96, 'eligibility': 61}


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value, indent=2)+'\n', encoding='utf-8')
    temp.replace(path)


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def low_priority():
    if os.name == 'nt':
        kernel = ctypes.windll.kernel32
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        kernel.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        if not kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x4000):
            raise RuntimeError('Cannot set BelowNormal process priority')
    else:
        os.nice(10)


def peak_mib():
    if os.name == 'nt':
        class Counters(ctypes.Structure):
            _fields_ = [('cb', ctypes.c_uint32), ('faults', ctypes.c_uint32)]+[
                (x, ctypes.c_size_t) for x in ['peak', 'working', 'paged_peak', 'paged',
                'nonpaged_peak', 'nonpaged', 'pagefile', 'pagefile_peak']]
        value = Counters(); value.cb = ctypes.sizeof(value)
        kernel = ctypes.windll.kernel32
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        fn = ctypes.windll.psapi.GetProcessMemoryInfo
        fn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32]
        if not fn(kernel.GetCurrentProcess(), ctypes.byref(value), value.cb):
            raise RuntimeError('Memory measurement failed')
        return value.peak/1048576
    import resource
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024


def download():
    from huggingface_hub import hf_hub_download
    for kind, revision in REVISIONS.items():
        directory = MODEL_ROOT/kind.lower()
        files = ['README.md', 'LICENSE', 'config.json', 'model.safetensors',
                 'tokenizer.json', 'tokenizer_config.json', 'special_tokens_map.json', 'vocab.txt']
        hashes = {}
        for filename in files:
            print(f'Download {kind}: {filename}', flush=True)
            path = Path(hf_hub_download(f'ncbi/MedCPT-{kind}-Encoder', filename,
                        revision=revision, local_dir=str(directory)))
            hashes[filename] = digest(path)
        if 'license_name: public-domain' not in (directory/'README.md').read_text():
            raise RuntimeError('Review model license')
        save(directory/'manifest.json', dict(model=f'ncbi/MedCPT-{kind}-Encoder',
             revision=revision, license='public-domain', files=hashes))


def protocol():
    split = read(ROOT/'experiments/retrieval_inputs/results/protocol.json')
    p = dict(dataset='TREC 2021: full 375580 historical trial collection',
        development_topics=split['development_topics'], screening_topics=['1','2','3','4','5'],
        validation='Reused 30-topic internal validation deferred; 2022 already exposed; no holdout claims.',
        models=REVISIONS, representation='CLS hidden state, float32, unnormalized dot product',
        article_fields_token_limits=FIELDS, article_max_tokens=256, query_max_tokens=128,
        article_layout='[CLS] title [SEP] labeled conditions, summary, eligibility [SEP]; fixed field budgets',
        precision='Dynamic-int8 per-channel reduce-range ONNX; float32 output vectors and dot products',
        execution='Sequential; one ONNX/BLAS/tokenizer thread; BelowNormal priority; batch 4',
        embedding_order='SQLite identities.row_id ascending; vectors checkpointed in 256-record shards',
        retrieval='Exact full-corpus dot product; tie trial ID; top 1000 each branch; top 200 after weighted RRF',
        lexical='Existing RRF(original BM25 weight 1, three-query lexical fusion weight 2), depth 1000',
        hybrid_vector_weights=[0.25, 0.5, 1.0, 2.0], lexical_weight=1.0, rrf_constant=60,
        selection='Max development macro eligible recall@200, then nDCG@10, then name; exploratory tuning',
        metrics='Linear nDCG gain 0/1/2; unjudged zero; eligible recall grade 2 only; pytrec_eval crosscheck',
        reranker='cross-encoder/ms-marco-MiniLM-L2-v2, quantized ONNX; one thread; batch 8; all 200 scored',
        reranker_manifest=read(ROOT/'models/paper/manifest.json')['reranker'],
        rerank_selection='Screen five development patients; expand only if nDCG improves >=0.01 and recall@10 does not regress',
        inputs={name:digest(ROOT/name) for name in ['data/raw/trec2021/topics2021.xml',
            'data/raw/trec2021/qrels2021.txt','demo/app/rerank.py','demo/app/retrieval.py']},
        index_metadata=read(ROOT/'data/indexes/bm25.json'))
    for kind in REVISIONS:
        manifest = MODEL_ROOT/kind.lower()/'manifest.json'
        if not manifest.exists():
            raise RuntimeError('Run --stage download first')
        p.setdefault('model_manifests', {})[kind] = read(manifest)
        p.setdefault('onnx_manifests', {})[kind] = read(MODEL_ROOT/kind.lower()/'onnx-manifest.json')
    path = OUT/'protocol.json'
    if path.exists() and read(path) != p:
        raise RuntimeError('Protocol changed: use a new versioned experiment')
    save(path, p)
    return p


class Encoder:
    def __init__(self, kind):
        import onnxruntime as ort
        from tokenizers import Tokenizer
        directory = MODEL_ROOT/kind.lower()
        self.tokenizer = Tokenizer.from_file(str(directory/'tokenizer.json'))
        options=ort.SessionOptions();options.intra_op_num_threads=1;options.inter_op_num_threads=1
        self.model=ort.InferenceSession(str(directory/'encoder-int8.onnx'),sess_options=options,
                                       providers=['CPUExecutionProvider'])

    def articles(self, rows):
        tok = self.tokenizer; packed = []
        for row in rows:
            values = dict(zip(FIELDS, row, strict=True))
            title = tok.encode(values['title'], add_special_tokens=False).ids[:FIELDS['title']]
            body = []
            for field in ['conditions','summary','eligibility']:
                body.extend(tok.encode(field+': '+values[field], add_special_tokens=False).ids[:FIELDS[field]])
            ids = [tok.token_to_id('[CLS]')]+title+[tok.token_to_id('[SEP]')]+body+[tok.token_to_id('[SEP]')]
            types = [0]*(len(title)+2)+[1]*(len(body)+1)
            packed.append(dict(input_ids=ids, attention_mask=[1]*len(ids), token_type_ids=types))
        return self.forward(packed)

    def query(self, note):
        ids=[self.tokenizer.token_to_id('[CLS]')]+self.tokenizer.encode(note,add_special_tokens=False).ids[:126]+[self.tokenizer.token_to_id('[SEP]')]
        return self.forward([dict(input_ids=ids,attention_mask=[1]*len(ids),token_type_ids=[0]*len(ids))])[0]

    def forward(self, encoded):
        import numpy as np
        length=max(len(row['input_ids']) for row in encoded)
        inputs={name:np.zeros((len(encoded),length),dtype=np.int64) for name in encoded[0]}
        for i,row in enumerate(encoded):
            for name,values in row.items():inputs[name][i,:len(values)]=values
        array=self.model.run(None,inputs)[0]
        if not np.isfinite(array).all():
            raise RuntimeError('Nonfinite embedding')
        return array


def encode(limit):
    import numpy as np
    p = protocol()
    model = Encoder('Article')
    db = sqlite3.connect((ROOT/'data/indexes/bm25.sqlite').as_uri()+'?mode=ro', uri=True)
    db.execute('PRAGMA cache_size=-16384')
    CACHE.mkdir(parents=True, exist_ok=True)
    rows = db.execute('SELECT i.row_id,i.trial_id,t.title,t.conditions,t.summary,t.eligibility '
        'FROM identities i JOIN trials t ON t.rowid=i.row_id ORDER BY i.row_id')
    done = total_new = 0; started = time.perf_counter()
    while batch := rows.fetchmany(256):
        path = CACHE/f'{done:06d}.npz'
        ids = [r[1] for r in batch]
        if path.exists():
            with np.load(path, allow_pickle=False) as old:
                if list(old['ids']) != ids or old['vectors'].shape != (len(ids),768):
                    raise RuntimeError('Invalid embedding checkpoint')
        else:
            if limit and total_new >= limit:
                break
            tick = time.perf_counter()
            vectors = np.concatenate([model.articles([r[2:] for r in batch[i:i+4]])
                                     for i in range(0,len(batch),4)])
            with path.with_suffix('.tmp').open('wb') as stream:
                np.savez(stream, ids=np.asarray(ids), vectors=vectors)
            path.with_suffix('.tmp').replace(path)
            total_new += len(batch)
            elapsed = time.perf_counter()-started
            progress = dict(completed=done+len(batch), total=375580,
                new_this_run=total_new, seconds_this_run=elapsed,
                records_per_second=total_new/elapsed,
                estimated_remaining_hours=(375580-done-len(batch))/(total_new/elapsed)/3600,
                peak_process_mib=peak_mib(), last_shard_seconds=time.perf_counter()-tick,
                protocol_sha256=digest(OUT/'protocol.json'))
            save(OUT/'encoding-progress.json', progress)
            print(json.dumps(progress), flush=True)
        done += len(batch)
    db.close()
    if done == 375580:
        save(CACHE/'complete.json', dict(records=done, protocol_sha256=digest(OUT/'protocol.json'),
            shards={path.name:digest(path) for path in sorted(CACHE.glob('*.npz'))}))


def exact_top(scores, ids, count=1000):
    import numpy as np
    if len(ids) != len(scores) or not np.isfinite(scores).all():
        raise ValueError('Invalid scores')
    count = min(count,len(ids))
    cutoff = np.partition(scores,len(scores)-count)[len(scores)-count]
    positions = np.flatnonzero(scores >= cutoff)
    positions = sorted(positions, key=lambda i:(-float(scores[i]), ids[i]))[:count]
    return [(ids[i],float(scores[i])) for i in positions]


def evaluate_rankings(rankings, qrels):
    from src.evaluate import metrics, check_reference
    per = {}
    for topic, ranking in rankings.items():
        per[topic] = metrics([d for d,_ in ranking][:200],qrels[topic])
        check_reference([d for d,_ in ranking][:200],qrels[topic],per[topic])
    macro = {key:sum(v[key] for v in per.values() if v[key] is not None)/
             sum(v[key] is not None for v in per.values())
             if any(v[key] is not None for v in per.values()) else None for key in next(iter(per.values()))}
    return dict(macro=macro, per_topic=per)


def retrieval():
    import numpy as np
    from src.clean import load_topics,load_qrels
    from demo.app.retrieval import rank_fusion
    p = protocol(); topics = load_topics(ROOT/'data/raw/trec2021/topics2021.xml')
    complete = read(CACHE/'complete.json')
    if complete['records'] != 375580 or complete['protocol_sha256'] != digest(OUT/'protocol.json'):
        raise RuntimeError('Full corpus embedding required')
    for name, sha in complete['shards'].items():
        if digest(CACHE/name) != sha:
            raise RuntimeError('Corrupt vector shard')
    model = Encoder('Query'); timings = {}; lexical = {}; dense = {}
    for topic in p['development_topics']:
        source = ROOT/'experiments/retrieval_inputs/results/cache'
        lexical[topic] = rank_fusion([read(source/f'baseline/{topic}.json'),
                          read(source/f'lexical_rrf/{topic}.json')],1000,[1.,2.])
        dest = OUT/f'rankings/dense/{topic}.json'
        if dest.exists():
            cached = read(dest); dense[topic] = cached['hits']; timings[topic] = cached['seconds']; continue
        tick = time.perf_counter(); query = model.query(topics[topic]); best = []
        for name in sorted(complete['shards']):
            with np.load(CACHE/name, allow_pickle=False) as shard:
                hits = exact_top(shard['vectors'] @ query, list(shard['ids']))
            best = sorted(best+hits,key=lambda x:(-x[1],x[0]))[:1000]
        timings[topic] = time.perf_counter()-tick; dense[topic] = best
        save(dest,dict(hits=best,seconds=timings[topic]))
        print(f'Retrieved full corpus: topic {topic}, {timings[topic]:.2f}s',flush=True)
    arms = {'balanced_fusion':lexical,'medcpt_dense':dense}
    for weight in p['hybrid_vector_weights']:
        arms[f'hybrid_{weight:g}'] = {t:rank_fusion([lexical[t],dense[t]],1000,[1.,weight]) for t in lexical}
    qrels = load_qrels(ROOT/'data/raw/trec2021/qrels2021.txt')
    results = {name:evaluate_rankings(ranks,qrels) for name,ranks in arms.items()}
    winner = sorted(arms,key=lambda name:(-results[name]['macro']['eligible_recall@200'],
                    -results[name]['macro']['ndcg@10'],name))[0]
    for name,ranks in arms.items(): save(OUT/f'rankings/{name}.json',ranks)
    save(OUT/'retrieval-results.json',dict(scope='45 reused development patients; complete 375580 trial corpus',
        arms=results,selected=winner,dense_query_seconds=timings,peak_process_mib=peak_mib(),
        timing_note='Dense query timings include loading vector shards; lexical uses saved rankings, no fresh lexical latency claim.'))
    print(json.dumps({name:v['macro'] for name,v in results.items()},indent=2),flush=True)


def rerank():
    from src.clean import load_topics,load_qrels
    from demo.app.rerank import CrossEncoder,trial_document,ordered_candidates
    p = protocol(); result = read(OUT/'retrieval-results.json')
    selected = result['selected']; rankings = read(OUT/f'rankings/{selected}.json')
    topics = load_topics(ROOT/'data/raw/trec2021/topics2021.xml')
    qrels = load_qrels(ROOT/'data/raw/trec2021/qrels2021.txt')
    encoder = CrossEncoder(ROOT/'models/paper/reranker', threads=1)
    db = sqlite3.connect((ROOT/'data/indexes/bm25.sqlite').as_uri()+'?mode=ro', uri=True)
    def score(topic):
        ids = sorted(d for d,_ in rankings[topic][:200]); dest = OUT/f'rerank-cache/{topic}.json'
        if dest.exists():
            saved = read(dest)
            if saved['ids'] != ids or saved['selected'] != selected: raise RuntimeError('Changed candidates')
        else:
            tick = time.perf_counter()
            values = encoder.predict(topics[topic],[trial_document(db,d) for d in ids],batch_size=8)
            saved = dict(ids=ids, selected=selected,scores=dict(zip(ids,values,strict=True)),
                         seconds=time.perf_counter()-tick)
            save(dest,saved)
        order = ordered_candidates(ids,saved['scores'])
        print(f'Reranked topic {topic}: {saved["seconds"]:.2f}s',flush=True)
        return [(d,saved['scores'][d]) for d in order]
    screen = p['screening_topics']; scored = {t:score(t) for t in screen}
    before = evaluate_rankings({t:rankings[t] for t in screen},qrels)
    after = evaluate_rankings(scored,qrels)
    promising = after['macro']['ndcg@10'] >= before['macro']['ndcg@10']+.01 and \
                after['macro']['eligible_recall@10'] >= before['macro']['eligible_recall@10']
    save(OUT/'rerank-screen.json',dict(selected_pool=selected,before=before,after=after,
        expanded=promising,peak_process_mib=peak_mib()))
    if promising:
        for t in p['development_topics']:
            if t not in scored: scored[t] = score(t)
        save(OUT/'rerank-results.json',evaluate_rankings(scored,qrels))
        save(OUT/'rankings/reranked.json',scored)
    db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage',choices=['download','encode','retrieval','rerank'],required=True)
    parser.add_argument('--limit-new',type=int,default=0,help='Encoding pilot limit, rounded up to 256 records; zero means full corpus')
    args = parser.parse_args(); low_priority()
    if args.stage == 'download': download()
    elif args.stage == 'encode': encode(args.limit_new)
    elif args.stage == 'retrieval': retrieval()
    else: rerank()


if __name__ == '__main__':
    main()
