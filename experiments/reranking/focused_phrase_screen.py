"""Five-patient, single-process focused-query screen; no model inference."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time

sys.dont_write_bytecode = True
for name in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS']:
    os.environ[name] = '1'


def longest_phrase(note, vocab):
    tokens = re.findall(r'[a-z0-9]+', note.lower())
    matches = []
    for start in range(len(tokens)):
        for length in range(min(10, len(tokens)-start), 1, -1):
            phrase = ' '.join(tokens[start:start+length])
            if phrase in vocab:
                matches.append((length, start, phrase))
                break
    return min(matches, key=lambda x:(-x[0], x[1], x[2]))[2] if matches else None


def main():
    started, cpu = time.perf_counter(), time.process_time()
    low = False
    if os.name == 'nt':
        kernel = ctypes.windll.kernel32
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        kernel.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        low = bool(kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x4000))
        if not low:
            raise RuntimeError('Cannot set low priority')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--deps', type=Path)
    args = parser.parse_args()
    root = args.project.resolve()
    if args.deps:
        sys.path.insert(0, str(args.deps.resolve()))
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root/'experiments/retrieval_inputs'))
    from experiment import save, digest, aggregate
    from src.clean import load_topics, load_qrels
    from src.evaluate import metrics, check_reference
    from demo.app.retrieval import search_fts, rank_fusion, focused
    read = lambda path: json.loads(Path(path).read_text(encoding='utf-8'))
    source = root/'experiments/retrieval_inputs/results'
    output = root/'reports/reranking/focused_phrase_screen'
    topics = ['1','2','3','4','5']
    assert set(topics).issubset(read(source/'protocol.json')['development_topics'])
    protocol = dict(year=2021, topics=topics, purpose='Exploratory development screen motivated by inspected development examples; not independent validation.',
        query='Longest multiword phrase in existing registry-condition vocabulary; tie by earliest note occurrence. Fall back to existing focused query. A lexical heuristic, not a clinical diagnosis.',
        combination='RRF k=60; original BM25 top1000 weight1, existing three-query RRF top1000 weight2, new weighted-BM25 phrase top1000 weight0.5; keep200.',
        selection='Report both systems on the five reused patients. No tuning, promotion or wider run from this script.',
        execution='One process; BelowNormal priority on Windows; one SQLite query at a time; 32MiB SQLite cache; 20-second per-query progress-handler guard; no ONNX inference.',
        index_sha256=read(root/'demo/index-image/manifest.json')['sha256'],
        input_sha256={name:digest(root/name) for name in ['data/raw/trec2021/topics2021.xml','data/raw/trec2021/qrels2021.txt','demo/app/assets/condition_vocabulary.json','demo/app/retrieval.py']},
        code_sha256=digest(Path(__file__)))
    if (output/'protocol.json').exists() and read(output/'protocol.json') != protocol:
        raise ValueError('Changed protocol; use a new output directory')
    save(output/'protocol.json', protocol)
    notes = load_topics(root/'data/raw/trec2021/topics2021.xml')
    judgments = load_qrels(root/'data/raw/trec2021/qrels2021.txt')
    vocab = read(root/'demo/app/assets/condition_vocabulary.json')
    frozen = read(root/'reports/reranking/balanced_reranker/protocol.json')
    db = sqlite3.connect((root/'data/indexes/bm25.sqlite').as_uri()+'?mode=ro', uri=True)
    db.execute('PRAGMA cache_size=-32768')
    db.execute('PRAGMA mmap_size=0')
    rows = {'balanced_2':{}, 'balanced_plus_phrase':{}}
    details = {}
    for topic in topics:
        original = read(source/f'cache/baseline/{topic}.json')
        lexical = read(source/f'cache/lexical_rrf/{topic}.json')
        phrase = longest_phrase(notes[topic], vocab) or focused(notes[topic], vocab)
        base = [d for d, _ in rank_fusion([original, lexical], 200, [1.,2.])]
        import hashlib
        assert hashlib.sha256('\n'.join(base).encode()).hexdigest() == frozen['candidate_hashes'][topic]
        cache = root/f'data/cache/focused_phrase_screen/{topic}.json'
        if cache.exists():
            entry = read(cache)
            assert entry['protocol'] == protocol and entry['query'] == phrase
            hits, seconds = entry['hits'], entry['seconds']
        else:
            tick = time.perf_counter()
            db.set_progress_handler(lambda: int(time.perf_counter()-tick > 20), 10000)
            try:
                hits = search_fts(db, phrase, 1000, weighted=True)
            finally:
                db.set_progress_handler(None, 0)
            seconds = time.perf_counter()-tick
            save(cache, dict(protocol=protocol, query=phrase, seconds=seconds, hits=hits))
        combined = [d for d, _ in rank_fusion([original, lexical, hits], 200, [1.,2.,.5])]
        for arm, ids in [('balanced_2', base), ('balanced_plus_phrase', combined)]:
            assert len(ids) == len(set(ids)) == 200
            row = metrics(ids, judgments[topic]); check_reference(ids, judgments[topic], row)
            rows[arm][topic] = row
        details[topic] = dict(query=phrase, query_seconds=seconds, new_candidates_in_200=len(set(combined)-set(base)))
        print(f'Topic {topic}: query={phrase!r}, {seconds:.3f}s', flush=True)
    db.close()
    save(output/'metrics.json', {arm:dict(macro=aggregate(values), per_topic=values) for arm, values in rows.items()})
    save(output/'queries.json', details)
    manifest = dict(completed=True, wall_seconds=round(time.perf_counter()-started,3), process_cpu_seconds=round(time.process_time()-cpu,3),
        below_normal_priority=low, single_process=True, model_inference_pairs=0, validation_evaluated=False, trec2022_accessed=False,
        interpretation='Small reused development screen only; candidate pool changed, so existing cross-encoder scores cannot be reused blindly. No deployment change.')
    save(output/'execution.json', manifest)
    for arm, values in rows.items():
        macro = aggregate(values)
        print(arm, json.dumps({key:macro[key] for key in ['ndcg@10','eligible_recall@10','eligible_recall@200']}), flush=True)
    print('EXECUTION', json.dumps(manifest), flush=True)


if __name__ == '__main__':
    main()
