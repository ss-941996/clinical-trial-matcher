"""Confirm the fixed focused-phrase coverage variant on 45 development topics."""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

sys.dont_write_bytecode = True
for name in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS']:
    os.environ[name] = '1'


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
    sys.path.insert(0, str(root/'experiments/reranking'))
    from focused_phrase_screen import longest_phrase
    from experiment import save, digest, aggregate, bootdiff
    from src.clean import load_topics, load_qrels
    from src.evaluate import metrics, check_reference
    from demo.app.retrieval import search_fts, rank_fusion, focused
    read = lambda path: json.loads(Path(path).read_text(encoding='utf-8'))
    source = root/'experiments/retrieval_inputs/results'
    output = root/'reports/reranking/focused_phrase_development'
    screen_report = root/'reports/reranking/focused_phrase_screen'
    screen_protocol = read(screen_report/'protocol.json')
    for name, expected in screen_protocol['input_sha256'].items():
        assert digest(root/name) == expected
    topics = read(source/'protocol.json')['development_topics']
    screen_topics = screen_protocol['topics']
    assert len(topics) == 45 and set(screen_topics).issubset(topics)
    protocol = dict(year=2021, topics=topics, new_confirmation_topics=[t for t in topics if t not in screen_topics],
        motivation='Five-patient screen improved coverage while hurting top-ten metrics. Confirm the fixed trade-off on development only; no promotion from this run.',
        query=screen_protocol['query'], combination=screen_protocol['combination'],
        execution=screen_protocol['execution']+' Pause 0.25s after each newly executed query; reuse compatible five-patient query hits.',
        screen_protocol_sha256=digest(screen_report/'protocol.json'),
        code_sha256={name:digest(root/name) for name in ['experiments/reranking/focused_phrase_screen.py','experiments/reranking/focused_phrase_development.py']},
        selection='No weight tuning. Report both arms on all45, and separately on the40 development topics outside the initial screen. Internal validation and2022 excluded.')
    if (output/'protocol.json').exists() and read(output/'protocol.json') != protocol:
        raise ValueError('Changed protocol; use a new output directory')
    save(output/'protocol.json', protocol)
    signature = hashlib.sha256(json.dumps(protocol, sort_keys=True).encode()).hexdigest()
    notes = load_topics(root/'data/raw/trec2021/topics2021.xml')
    qrels = load_qrels(root/'data/raw/trec2021/qrels2021.txt')
    vocab = read(root/'demo/app/assets/condition_vocabulary.json')
    frozen = read(root/'reports/reranking/balanced_reranker/protocol.json')
    db = sqlite3.connect((root/'data/indexes/bm25.sqlite').as_uri()+'?mode=ro', uri=True)
    db.execute('PRAGMA cache_size=-32768'); db.execute('PRAGMA mmap_size=0')
    rows = {'balanced_2':{}, 'balanced_plus_phrase':{}}
    details, queries_run = {}, 0
    for position, topic in enumerate(topics, 1):
        original = read(source/f'cache/baseline/{topic}.json')
        lexical = read(source/f'cache/lexical_rrf/{topic}.json')
        phrase = longest_phrase(notes[topic], vocab) or focused(notes[topic], vocab)
        base = [d for d, _ in rank_fusion([original, lexical], 200, [1.,2.])]
        assert hashlib.sha256('\n'.join(base).encode()).hexdigest() == frozen['candidate_hashes'][topic]
        cache = root/f'data/cache/focused_phrase_development/{topic}.json'
        if topic in screen_topics:
            entry = read(root/f'data/cache/focused_phrase_screen/{topic}.json')
            assert entry['protocol'] == screen_protocol and entry['query'] == phrase
            hits, seconds = entry['hits'], entry['seconds']
            origin = 'five-patient cache'
        elif cache.exists():
            entry = read(cache)
            assert entry['protocol_sha256'] == signature and entry['query'] == phrase
            hits, seconds = entry['hits'], entry['seconds']
            origin = 'development cache'
        else:
            tick = time.perf_counter()
            db.set_progress_handler(lambda: int(time.perf_counter()-tick > 20), 10000)
            try:
                hits = search_fts(db, phrase, 1000, weighted=True)
            finally:
                db.set_progress_handler(None, 0)
            seconds = time.perf_counter()-tick
            queries_run += 1
            save(cache, dict(protocol_sha256=signature, query=phrase, seconds=seconds, hits=hits))
            origin = 'new sequential query'
            time.sleep(.25)
        combined = [d for d, _ in rank_fusion([original, lexical, hits], 200, [1.,2.,.5])]
        for arm, ids in [('balanced_2', base), ('balanced_plus_phrase', combined)]:
            assert len(ids) == len(set(ids)) == 200
            row = metrics(ids, qrels[topic]); check_reference(ids, qrels[topic], row)
            rows[arm][topic] = row
        details[topic] = dict(query=phrase, query_seconds=seconds, origin=origin, new_candidates_in_200=len(set(combined)-set(base)))
        save(output/'checkpoint.json', dict(completed_topics=position, planned_topics=45, complete=False))
        save(output/'metrics.json', {arm:dict(macro=aggregate(values), per_topic=values) for arm, values in rows.items()})
        print(f'{position}/45 topic {topic}: {origin}, {seconds:.3f}s', flush=True)
    db.close()
    groups = {'all45':topics, 'confirmation40':[t for t in topics if t not in screen_topics]}
    grouped = {}
    for name, ids in groups.items():
        subset = {arm:{topic:values[topic] for topic in ids} for arm, values in rows.items()}
        grouped[name] = dict(topics=ids, macro={arm:aggregate(values) for arm, values in subset.items()},
            comparisons={key:bootdiff(subset['balanced_2'], subset['balanced_plus_phrase'], key) for key in ['ndcg@10','eligible_recall@10','eligible_recall@200']})
    save(output/'grouped_results.json', grouped)
    save(output/'queries.json', details)
    save(output/'checkpoint.json', dict(completed_topics=45, planned_topics=45, complete=True))
    execution = dict(completed=True, wall_seconds=round(time.perf_counter()-started,3), process_cpu_seconds=round(time.process_time()-cpu,3),
        below_normal_priority=low, single_process=True, queries_run_this_execution=queries_run, model_inference_pairs=0,
        validation_evaluated=False, trec2022_accessed=False, deployed=False)
    save(output/'execution.json', execution)
    for group, result in grouped.items():
        for arm, macro in result['macro'].items():
            print(group, arm, json.dumps({key:macro[key] for key in ['ndcg@10','eligible_recall@10','eligible_recall@200']}), flush=True)
    print('EXECUTION', json.dumps(execution), flush=True)


if __name__ == '__main__':
    main()
