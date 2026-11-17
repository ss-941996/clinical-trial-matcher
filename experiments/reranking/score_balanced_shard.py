"""Optionally precompute independent score-cache shards for the frozen experiment."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
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
    parser.add_argument('--phase', choices=['development', 'validation'], required=True)
    parser.add_argument('--shard', type=int, required=True)
    parser.add_argument('--shards', type=int, default=3)
    args = parser.parse_args()
    root = args.project.resolve()
    if args.deps:
        sys.path.insert(0, str(args.deps.resolve()))
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root/'experiments/retrieval_inputs'))
    from experiment import digest
    from src.clean import load_topics
    from demo.app.retrieval import rank_fusion
    from demo.app.rerank import CrossEncoder, trial_document, ordered_candidates
    report = root/'reports/reranking/balanced_reranker'
    read = lambda p: json.loads(Path(p).read_text(encoding='utf-8'))
    protocol = read(report/'protocol.json')
    if args.phase == 'validation' and not (report/'selection.json').exists():
        raise ValueError('Freeze development selection before scoring validation')
    if not 0 <= args.shard < args.shards:
        raise ValueError('Invalid shard')
    for name, expected in protocol['code_sha256'].items():
        assert digest(root/name) == expected, 'Frozen scoring code changed'
    for name, expected in protocol['model_files'].items():
        assert digest(args.model/name) == expected, 'Model differs'
    signature = hashlib.sha256(json.dumps(protocol, sort_keys=True).encode()).hexdigest()
    topic_ids = protocol[args.phase+'_topics']
    width = (len(topic_ids)+args.shards-1)//args.shards
    topic_ids = topic_ids[args.shard*width:(args.shard+1)*width]
    topics = load_topics(root/'data/raw/trec2021/topics2021.xml')
    encoder = CrossEncoder(args.model, threads=4)
    db = sqlite3.connect((root/'data/indexes/bm25.sqlite').as_uri()+'?mode=ro', uri=True)
    db.execute('PRAGMA cache_size=-65536')
    source = root/'experiments/retrieval_inputs/results/cache'
    cache = root/'data/cache/balanced_reranker'
    cache.mkdir(parents=True, exist_ok=True)
    for topic in topic_ids:
        target = cache/f'{topic}.json'
        if target.exists():
            assert read(target)['protocol_sha256'] == signature
            continue
        original = [d for d, _ in rank_fusion([read(source/f'baseline/{topic}.json'), read(source/f'lexical_rrf/{topic}.json')], 200, [1., 2.])]
        assert hashlib.sha256('\n'.join(original).encode()).hexdigest() == protocol['candidate_hashes'][topic]
        tick = time.perf_counter()
        ids = sorted(original)
        scores = dict(zip(ids, encoder.predict(topics[topic], [trial_document(db, d) for d in ids], batch_size=8), strict=True))
        ordered_candidates(original, scores)
        seconds = time.perf_counter()-tick
        entry = dict(protocol_sha256=signature, scores=scores, seconds=seconds,
                     execution='Optional concurrent shard, four inference threads; latency includes competing desktop work.')
        temporary = target.with_suffix(f'.{os.getpid()}.tmp')
        temporary.write_text(json.dumps(entry, indent=2)+'\n', encoding='utf-8')
        temporary.replace(target)
        print(f'{args.phase} shard {args.shard}, topic {topic}: {seconds:.2f}s', flush=True)
    db.close()


if __name__ == '__main__':
    main()
