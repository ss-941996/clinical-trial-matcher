"""Read-only experiment observer with append-only human and JSON event logs.

Does not load a model, encode a trial, execute a test, or change the protocol.
"""
from datetime import datetime, timezone
import json
import time
from experiments.medcpt_hybrid.pilot import OUT
from experiments.medcpt_hybrid.run import low_priority


def main():
    low_priority();OUT.mkdir(parents=True,exist_ok=True)
    seen={}
    def event(kind,message,payload=None):
        stamp=datetime.now(timezone.utc).isoformat(timespec='seconds')
        record=dict(observed_at_utc=stamp,kind=kind,message=message,payload=payload)
        with (OUT/'live.jsonl').open('a',encoding='utf-8') as stream:
            stream.write(json.dumps(record)+'\n')
        line=f'[{stamp}] {kind}: {message}'
        with (OUT/'live.log').open('a',encoding='utf-8') as stream:stream.write(line+'\n')
        print(line,flush=True)
    event('monitor_started','Logging begins from the currently saved checkpoint; earlier console output remains in the chat.')
    event('implementation_checks','Four implementation checks passed before this monitor started; these are not relevance-quality results.',dict(passed=4))
    while True:
        paths=[OUT/name for name in ['encoding-progress.json','execution.json',
            'judgment-coverage.json','retrieval-results.json','rerank-results.json']]
        paths+=sorted((OUT/'rerank-cache').glob('*.json'))
        for path in paths:
            try:
                content=path.read_text(encoding='utf-8')
            except FileNotFoundError:
                continue
            if seen.get(str(path))==content:continue
            try:value=json.loads(content)
            except json.JSONDecodeError:continue
            seen[str(path)]=content
            if path.name=='encoding-progress.json':
                if value.get('reused'):
                    event('vectors_reused',f"{value['reused']:,} verified vectors reused; no trial encoding",value)
                    continue
                event('encoding',f"{value['completed']:,}/{value['total']:,} ({100*value['completed']/value['total']:.2f}%); "
                    f"{value['records_per_second']:.2f} trials/s; ETA {value['estimated_remaining_minutes']:.1f} min; "
                    f"peak working set {value['peak_process_mib']:.1f} MiB",value)
            elif path.name=='execution.json':
                event('stage',value['stage'],value)
            elif path.parent.name=='rerank-cache':
                event('patient_reranked',f"Patient {path.stem}: {len(value['ids'])} candidates scored in {value['seconds']:.2f}s; "
                    f"pool={value['selected_hybrid']}",dict(patient=path.stem,seconds=value['seconds'],pairs=len(value['ids'])))
            elif path.name=='retrieval-results.json':
                for name,arm in value['arms'].items():
                    m=arm['macro']
                    event('retrieval_metrics',f"{name}: nDCG@10={m['ndcg@10']:.4f}, eligible recall@10={m['eligible_recall@10']:.2%}, "
                        f"eligible recall@200={m['eligible_recall@200']:.2%}",dict(arm=name,**arm))
            elif path.name=='rerank-results.json':
                m=value['metrics']['macro']
                event('rerank_metrics',f"{value['selected_hybrid']} + MiniLM-L2: nDCG@10={m['ndcg@10']:.4f}, "
                    f"eligible recall@10={m['eligible_recall@10']:.2%}, eligible recall@200={m['eligible_recall@200']:.2%}",value)
            else:event('judgment_coverage','Judged eligible counts for the sampled collection are available.',value)
        if (OUT/'execution.json').exists():
            state=json.loads((OUT/'execution.json').read_text())
            if state['stage'] in ['complete','failed']:
                event('monitor_finished','Experiment '+state['stage'],state)
                break
        time.sleep(2)


if __name__=='__main__':main()
