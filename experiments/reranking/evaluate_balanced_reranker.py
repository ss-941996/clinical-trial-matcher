"""Rerank fixed balanced-fusion candidates; develop on TREC 2021 only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import statistics
import sys
import time

sys.dont_write_bytecode = True
os.environ.setdefault('TOKENIZERS_PARALLELISM', 'false')
os.environ.setdefault('OMP_NUM_THREADS', '4')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--deps', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--cache', type=Path)
    args = parser.parse_args()
    project = args.project.resolve()
    if args.deps:
        sys.path.insert(0, str(args.deps.resolve()))
    sys.path.insert(0, str(project))
    sys.path.insert(0, str(project/'experiments/retrieval_inputs'))
    from experiment import save, digest, aggregate, bootdiff
    from src.clean import load_topics, load_qrels
    from src.evaluate import metrics, check_reference
    from demo.app.retrieval import rank_fusion
    from demo.app.rerank import CrossEncoder, trial_document, ordered_candidates, fuse, MODEL, REVISION, GRAPH

    read = lambda path: json.loads(Path(path).read_text(encoding='utf-8'))
    source = project/'experiments/retrieval_inputs/results'
    output = args.output or project/'reports/reranking/balanced_reranker'
    cache = args.cache or project/'data/cache/balanced_reranker'
    split = read(source/'protocol.json')
    dev, validation = split['development_topics'], split['validation_topics']
    assert len(dev) == 45 and len(validation) == 30 and not set(dev).intersection(validation)
    pools = {}
    bm25 = {}
    for topic in dev + validation:
        baseline = read(source/f'cache/baseline/{topic}.json')
        lexical = read(source/f'cache/lexical_rrf/{topic}.json')
        pools[topic] = [d for d, _ in rank_fusion([baseline, lexical], 200, [1., 2.])]
        bm25[topic] = [d for d, _ in baseline[:200]]
        assert len(pools[topic]) == len(set(pools[topic])) == 200
    previous = read(project/'reports/reranking/balanced_fusion/validation_rankings.json')['balance_2']
    for topic in validation:
        assert pools[topic] == previous[topic], 'Balanced-fusion candidates changed'
    candidate_hashes = {t: hashlib.sha256('\n'.join(ids).encode()).hexdigest() for t, ids in pools.items()}
    protocol = dict(year=2021, development_topics=dev, validation_topics=validation,
        validation_status='Reused internal validation, exploratory; not an untouched holdout.',
        external_holdout='TREC 2022 excluded from this experiment; see reports/trec2022-status.md for prior cancellation history.',
        model=MODEL, revision=REVISION, graph=GRAPH, threads=4, batch_size=8,
        scoring='Exactly 200 balanced-fusion candidates per topic, sorted by trial ID before dynamic-quantized inference.',
        candidate_depth=200, balance_weights=[1., 2.], rrf_constant=60,
        arms=['balanced', 'balanced_ce', 'balanced_blend_0.5', 'balanced_blend_1', 'balanced_blend_2'],
        selection='Maximum development nDCG@10, then eligible recall@10, then arm name; no-change balanced arm included.',
        candidate_hashes=candidate_hashes,
        code_sha256={name: digest(project/name) for name in ['demo/app/rerank.py', 'demo/app/retrieval.py', 'experiments/reranking/evaluate_balanced_reranker.py']},
        input_sha256={name: digest(project/name) for name in ['data/raw/trec2021/topics2021.xml', 'data/raw/trec2021/qrels2021.txt', 'experiments/retrieval_inputs/results/protocol.json']},
        model_files={name: digest(args.model/name) for name in ['tokenizer.json', GRAPH]})
    if (output/'protocol.json').exists() and read(output/'protocol.json') != protocol:
        raise ValueError('Protocol changed: use a fresh output and score cache')
    save(output/'protocol.json', protocol)
    signature = hashlib.sha256(json.dumps(protocol, sort_keys=True).encode()).hexdigest()
    topics = load_topics(project/'data/raw/trec2021/topics2021.xml')
    qrels = load_qrels(project/'data/raw/trec2021/qrels2021.txt')
    db = sqlite3.connect((project/'data/indexes/bm25.sqlite').as_uri()+'?mode=ro', uri=True)
    db.execute('PRAGMA cache_size=-65536')
    encoder = CrossEncoder(args.model)
    timings = {}
    started = time.perf_counter()

    def rankings(topic):
        original = pools[topic]
        ids = sorted(original)
        path = cache/f'{topic}.json'
        if path.exists():
            entry = read(path)
            if entry['protocol_sha256'] != signature or set(entry['scores']) != set(ids):
                raise ValueError('Incompatible cached scores')
        else:
            tick = time.perf_counter()
            docs = [trial_document(db, trial) for trial in ids]
            scores = dict(zip(ids, encoder.predict(topics[topic], docs, batch_size=8), strict=True))
            entry = dict(protocol_sha256=signature, scores=scores, seconds=time.perf_counter()-tick)
            save(path, entry)
        timings[topic] = entry['seconds']
        ce = ordered_candidates(original, entry['scores'])
        result = {'balanced': original, 'balanced_ce': ce}
        for weight in [.5, 1., 2.]:
            result[f'balanced_blend_{weight:g}'] = fuse(original, ce, weight)
        print(f'Topic {topic}: 200 pairs, {entry["seconds"]:.2f}s', flush=True)
        return result

    def measured(topic, ids):
        assert len(ids) == 200 and set(ids) == set(pools[topic])
        row = metrics(ids, qrels[topic])
        check_reference(ids, qrels[topic], row)
        assert row['eligible_recall@200'] == metrics(pools[topic], qrels[topic])['eligible_recall@200']
        return row

    development = {arm: {} for arm in protocol['arms']}
    for topic in dev:
        for arm, ids in rankings(topic).items():
            development[arm][topic] = measured(topic, ids)
        save(output/'development_metrics.json', {arm: dict(macro=aggregate(rows), per_topic=rows) for arm, rows in development.items()})
    key = lambda arm: (-aggregate(development[arm])['ndcg@10'], -aggregate(development[arm])['eligible_recall@10'], arm)
    best = min(development, key=key)
    best_rerank = min([arm for arm in development if arm != 'balanced'], key=key)
    selection = dict(best_overall=best, best_reranking_arm=best_rerank, frozen_before_validation=True)
    save(output/'selection.json', selection)
    print('FROZEN SELECTION', selection, flush=True)
    arms = list(dict.fromkeys(['balanced', best, best_rerank]))
    results = {arm: {} for arm in ['bm25'] + arms}
    saved_rankings = {arm: {} for arm in ['bm25'] + arms}
    for topic in validation:
        ranks = rankings(topic)
        results['bm25'][topic] = metrics(bm25[topic], qrels[topic])
        check_reference(bm25[topic], qrels[topic], results['bm25'][topic])
        saved_rankings['bm25'][topic] = bm25[topic]
        for arm in arms:
            results[arm][topic] = measured(topic, ranks[arm])
            saved_rankings[arm][topic] = ranks[arm]
        save(output/'validation_metrics.json', {arm: dict(macro=aggregate(rows), per_topic=rows) for arm, rows in results.items()})
    save(output/'validation_rankings.json', saved_rankings)
    save(output/'paired_comparisons.json', {reference: {arm: {key: bootdiff(results[reference], results[arm], key) for key in ['ndcg@10', 'eligible_recall@10', 'eligible_recall@200']} for arm in arms if arm != reference} for reference in ['bm25', 'balanced']})
    values = sorted(timings.values())
    save(output/'manifest.json', dict(seconds_this_run=time.perf_counter()-started, pairs=15000,
        latency_scope='Document loading/tokenization/ONNX inference only; cached candidate retrieval excluded. Four CPU threads, batch eight. Not end-to-end app latency.',
        rerank_seconds=dict(mean=statistics.mean(values), median=statistics.median(values), p95=values[min(len(values)-1, int(.95*len(values)))], per_topic=timings),
        paid_api_calls=0, candidate_sets_preserved=True, metrics_verified_with='pytrec_eval for each topic and reported arm'))
    for arm, rows in results.items():
        print('VALIDATION', arm, aggregate(rows), flush=True)
    db.close()
    print('BALANCED RERANKING COMPLETE', flush=True)


if __name__ == '__main__':
    main()
