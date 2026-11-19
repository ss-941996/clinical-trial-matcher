"""Wait for pilot encoding, then run retrieval and reranking sequentially.

Useful when encoding is already running. This process performs no inference
until the encoder has written its completed-vector manifest.
"""
import time
from experiments.medcpt_hybrid.run import low_priority,save,read,digest
from experiments.medcpt_hybrid.pilot import OUT,CACHE,COUNT,retrieval,rerank,cap_single_cpu


def main():
    low_priority();cap_single_cpu()
    state=OUT/'execution.json'
    save(state,dict(stage='waiting_for_encoding',finished=False))
    print(f'Waiting for the completed {COUNT:,}-vector manifest; no inference running here.',flush=True)
    try:
        deadline=time.monotonic()+8*3600
        while not (CACHE/'complete.json').exists():
            if time.monotonic()>deadline: raise TimeoutError('Encoding did not finish within eight hours; resume manually')
            time.sleep(5)
        complete=read(CACHE/'complete.json')
        if complete['records']!=COUNT or complete['protocol_sha256']!=digest(OUT/'protocol.json'):
            raise RuntimeError('Wrong completed-vector manifest')
        save(state,dict(stage='retrieval',finished=False));retrieval()
        save(state,dict(stage='reranking',finished=False));rerank()
        save(state,dict(stage='complete',finished=True,
            report='reports/medcpt-hybrid-pilot-v1/README.md'))
    except Exception as error:
        save(state,dict(stage='failed',finished=False,error=repr(error)))
        raise


if __name__=='__main__': main()
