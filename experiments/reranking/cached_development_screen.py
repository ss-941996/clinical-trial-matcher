"""Sequential, cache-only TREC 2021 development checks; no model inference."""
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
for variable in ['OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS']:
    os.environ[variable] = '1'


def main():
    started, cpu_started = time.perf_counter(), time.process_time()
    low_priority = False
    if os.name == 'nt':
        kernel = ctypes.windll.kernel32
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        kernel.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        low_priority = bool(kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x4000))
        if not low_priority:
            raise RuntimeError('Could not set BelowNormal priority; no screening work started')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--deps', type=Path)
    parser.add_argument('--stage', choices=['audit', 'retrieval', 'rerank'], required=True)
    args = parser.parse_args()
    root = args.project.resolve()
    if args.deps:
        sys.path.insert(0, str(args.deps.resolve()))
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root/'experiments/retrieval_inputs'))
    from experiment import save, digest, aggregate, bootdiff
    from src.clean import load_qrels, load_topics
    from src.evaluate import metrics, check_reference
    from demo.app.retrieval import rank_fusion
    from demo.app.rerank import ordered_candidates, fuse
    read = lambda path: json.loads(Path(path).read_text(encoding='utf-8'))
    output = root/'reports/reranking/cached_development_screen'
    source = root/'experiments/retrieval_inputs/results'
    frozen_path = root/'reports/reranking/balanced_reranker/protocol.json'
    frozen = read(frozen_path)
    dev = read(source/'protocol.json')['development_topics']
    screen = ['1', '2', '3', '4', '5']
    assert len(dev) == 45 and set(screen).issubset(dev)
    protocol = dict(year=2021, development_topics=dev, screening_topics=screen,
        execution='One sequential process, BelowNormal priority on Windows, numeric-library thread limits 1. Cache only; no ONNX session or inference.',
        retrieval_weights=[1.5, 2., 2.5, 3.], rrf_constant=60, original_bm25_weight=1., candidate_depth=200,
        retrieval_selection='Maximum development eligible recall@200 subject to nDCG@10 >= balanced_2 minus 0.005 and eligible recall@10 >= balanced_2; then nDCG@10, then arm name. No deployment promotion from this screen.',
        rerank_weights=[.25, .5, 1., 2.], rerank_pool='Exactly the original balanced_2 top 200; never reuse these scores for a changed candidate pool.',
        rerank_selection='A screening arm is promising only if five-patient nDCG@10 improves by at least 0.01 and eligible recall@10 does not regress; fixed recall@200 required. Five reused development patients are not an independent benchmark.',
        validation='No internal-validation evaluation; TREC 2022 excluded. Repeated development tuning is exploratory.',
        frozen_score_protocol_sha256=digest(frozen_path), code_sha256=digest(Path(__file__)))
    if (output/'protocol.json').exists() and read(output/'protocol.json') != protocol:
        raise ValueError('Changed protocol: use a new versioned output directory')
    save(output/'protocol.json', protocol)
    for name, expected in frozen['input_sha256'].items():
        assert digest(root/name) == expected, f'Input changed: {name}'
    for name, expected in frozen['code_sha256'].items():
        assert digest(root/name) == expected, f'Frozen scoring/retrieval code changed: {name}'
    qrels = load_qrels(root/'data/raw/trec2021/qrels2021.txt')
    pools, original, lexical = {}, {}, {}
    for topic in dev:
        original[topic] = read(source/f'cache/baseline/{topic}.json')
        lexical[topic] = read(source/f'cache/lexical_rrf/{topic}.json')
        ids = [d for d, _ in rank_fusion([original[topic], lexical[topic]], 200, [1., 2.])]
        assert hashlib.sha256('\n'.join(ids).encode()).hexdigest() == frozen['candidate_hashes'][topic]
        pools[topic] = ids

    if args.stage == 'audit':
        topics = load_topics(root/'data/raw/trec2021/topics2021.xml')
        rows, cases = {}, []
        db = sqlite3.connect((root/'data/indexes/bm25.sqlite').as_uri()+'?mode=ro', uri=True)
        for topic in dev:
            eligible = {d for d, grade in qrels[topic].items() if grade == 2}
            assert eligible
            baseline_ids = {d for d, _ in original[topic]}
            lexical_ids = {d for d, _ in lexical[topic]}
            selected = set(pools[topic])
            missed = eligible-selected
            inside = missed.intersection(baseline_ids | lexical_ids)
            outside = missed-(baseline_ids | lexical_ids)
            rows[topic] = dict(eligible_total=len(eligible), retrieved_200=len(eligible & selected), missed_200=len(missed),
                missed_but_in_cached_1000_union=len(inside), missed_outside_cached_1000_union=len(outside),
                eligible_recall_200=len(eligible & selected)/len(eligible), cached_union_coverage=len(eligible & (baseline_ids | lexical_ids))/len(eligible))
            if topic in screen:
                for category, candidates in [('within_cached_union', inside), ('outside_cached_union', outside)]:
                    for trial_id in sorted(candidates)[:2]:
                        document = db.execute('SELECT title,conditions FROM trials WHERE rowid=(SELECT row_id FROM identities WHERE trial_id=?)', (trial_id,)).fetchone()
                        assert document is not None
                        cases.append(dict(topic=topic, patient_note=topics[topic], trial_id=trial_id, title=document[0], conditions=document[1],
                            judgment='eligible', location=category,
                            bm25_rank=next((i for i, (d, _) in enumerate(original[topic], 1) if d == trial_id), None),
                            lexical_rank=next((i for i, (d, _) in enumerate(lexical[topic], 1) if d == trial_id), None)))
        db.close()
        totals = {name: sum(row[name] for row in rows.values()) for name in ['eligible_total','retrieved_200','missed_200','missed_but_in_cached_1000_union','missed_outside_cached_1000_union']}
        result = dict(topics=45, totals=totals, macro_recall_200=sum(row['eligible_recall_200'] for row in rows.values())/45,
            macro_cached_union_coverage=sum(row['cached_union_coverage'] for row in rows.values())/45,
            interpretation='Within-union misses may be recoverable by rank fusion; outside-union misses require different retrieval. Locations alone do not establish a clinical/linguistic cause.', per_topic=rows)
        save(output/'miss_audit.json', result)
        save(output/'miss_examples.json', cases)
        print('MISS AUDIT', json.dumps({k:v for k,v in result.items() if k != 'per_topic'}), flush=True)

    elif args.stage == 'retrieval':
        arms = ['bm25', 'lexical_rrf']+[f'balanced_{w:g}' for w in protocol['retrieval_weights']]
        rows = {arm:{} for arm in arms}
        for topic in dev:
            ranks = {'bm25':[d for d, _ in original[topic][:200]], 'lexical_rrf':[d for d, _ in lexical[topic][:200]]}
            for weight in protocol['retrieval_weights']:
                ranks[f'balanced_{weight:g}'] = [d for d, _ in rank_fusion([original[topic], lexical[topic]], 200, [1., weight])]
            for arm, ids in ranks.items():
                row = metrics(ids, qrels[topic]); check_reference(ids, qrels[topic], row)
                rows[arm][topic] = row
        macros = {arm:aggregate(values) for arm, values in rows.items()}
        baseline = macros['balanced_2']
        allowed = [arm for arm in arms if arm.startswith('balanced_') and macros[arm]['ndcg@10'] >= baseline['ndcg@10']-.005 and macros[arm]['eligible_recall@10'] >= baseline['eligible_recall@10']]
        best = min(allowed, key=lambda arm:(-macros[arm]['eligible_recall@200'], -macros[arm]['ndcg@10'], arm))
        save(output/'retrieval_development_metrics.json', {arm:dict(macro=macros[arm], per_topic=values) for arm, values in rows.items()})
        keys = ['ndcg@10','eligible_recall@10','eligible_recall@200']
        comparisons = {arm:{key:bootdiff(rows['balanced_2'], rows[arm], key) for key in keys} for arm in arms if arm.startswith('balanced_') and arm != 'balanced_2'}
        save(output/'retrieval_selection.json', dict(best_development_arm=best, allowed_arms=allowed, deployed=False, reason='Development-only exploratory screen; no validation or promotion.', comparisons=comparisons))
        for arm, macro in macros.items():
            print('RETRIEVAL', arm, json.dumps({key:macro[key] for key in keys}), flush=True)
        print('DEVELOPMENT SELECTION', best, flush=True)

    else:
        signature = hashlib.sha256(json.dumps(frozen, sort_keys=True).encode()).hexdigest()
        rows, saved_ranks, cache_hashes = {}, {}, {}
        for topic in screen:
            path = root/f'data/cache/balanced_reranker/{topic}.json'
            entry = read(path)
            assert entry['protocol_sha256'] == signature, 'Incompatible score cache'
            ids = pools[topic]
            assert len(ids) == len(set(ids)) == 200 and set(entry['scores']) == set(ids)
            cache_hashes[topic] = digest(path)
            ce = ordered_candidates(ids, entry['scores'])
            rankings = {'balanced_2':ids, 'cross_encoder_only':ce}
            for weight in protocol['rerank_weights']:
                rankings[f'blend_{weight:g}'] = fuse(ids, ce, weight)
            for arm, ranking in rankings.items():
                assert len(ranking) == 200 and set(ranking) == set(ids)
                row = metrics(ranking, qrels[topic]); check_reference(ranking, qrels[topic], row)
                assert row['eligible_recall@200'] == metrics(ids, qrels[topic])['eligible_recall@200']
                rows.setdefault(arm,{})[topic] = row
                saved_ranks.setdefault(arm,{})[topic] = ranking
        macros = {arm:aggregate(values) for arm, values in rows.items()}
        base = macros['balanced_2']
        promising = [arm for arm in rows if arm != 'balanced_2' and macros[arm]['ndcg@10'] >= base['ndcg@10']+.01 and macros[arm]['eligible_recall@10'] >= base['eligible_recall@10']]
        save(output/'reranking_five_patient_metrics.json', {arm:dict(macro=macros[arm],per_topic=values) for arm,values in rows.items()})
        save(output/'reranking_five_patient_rankings.json', saved_ranks)
        save(output/'reranking_screen_decision.json', dict(topics=screen, promising_arms=promising, deployed=False, cache_sha256=cache_hashes,
            new_inference_pairs=0, interpretation='Five previously used development patients, reused model scores. Screening only; requires broader development confirmation.'))
        for arm, macro in macros.items():
            print('FIVE-PATIENT RERANK', arm, json.dumps({key:macro[key] for key in ['ndcg@10','eligible_recall@10','eligible_recall@200']}), flush=True)
        print('PROMISING SCREEN ARMS', promising, flush=True)

    manifest = dict(stage=args.stage, wall_seconds=round(time.perf_counter()-started,3), process_cpu_seconds=round(time.process_time()-cpu_started,3),
        below_normal_priority=low_priority, single_process=True, model_inference_pairs=0, paid_api_calls=0,
        validation_evaluated=False, trec2022_accessed=False)
    save(output/f'{args.stage}_execution.json', manifest)
    print('EXECUTION', json.dumps(manifest), flush=True)


if __name__ == '__main__':
    main()
