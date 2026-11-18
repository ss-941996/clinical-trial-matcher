"""Sequential current-corpus Docker smoke tests; no relevance-score claims."""
from datetime import datetime,timezone
import json
from pathlib import Path
import re
import subprocess
import time
from urllib.request import Request,urlopen

ROOT=Path(__file__).resolve().parents[1]
COMPOSE=['docker','compose','-f',str(ROOT/'demo/compose.images.yaml')]

def command(*args):return subprocess.check_output(args,cwd=ROOT,text=True,timeout=600).strip()
def compose(*args):return command(*COMPOSE,*args)
def get(path):
    with urlopen('http://127.0.0.1:8000'+path,timeout=15) as response:return json.load(response)
def ready():
    start=time.perf_counter()
    while time.perf_counter()-start<300:
        try:
            if get('/health/ready')['status']=='ready':return round(time.perf_counter()-start,3)
        except OSError:pass
        time.sleep(1)
    raise RuntimeError('Readiness timed out')
def identity():
    container=compose('ps','-q','matcher');info=json.loads(command('docker','inspect',container))[0]
    script="import hashlib,json,pathlib; p=pathlib.Path('/data/bm25.sqlite'); s=p.stat(); print(json.dumps(dict(sha256=hashlib.file_digest(p.open('rb'),'sha256').hexdigest(),bytes=s.st_size,inode=s.st_ino,mtime_ns=s.st_mtime_ns)))"
    index=json.loads(compose('exec','-T','matcher','python','-c',script))
    assert index['sha256']==json.loads((ROOT/'data/manifests/ctgov-current.json').read_text())['sha256']
    return dict(container=container,image=info['Image'],index=index,volume=next(m['Name'] for m in info['Mounts'] if m['Destination']=='/data'))
def search(note):
    payload=json.dumps(dict(note=note,top_k=10)).encode()
    with urlopen(Request('http://127.0.0.1:8000/match',data=payload,headers={'Content-Type':'application/json'}),timeout=240) as response:data=json.load(response)
    assert data['method']=='fusion' and data['candidate_count']==200 and len(data['results'])==10
    assert data['rerank_scored_pairs']==0 and not data['historical']
    assert all(t['status']=='RECRUITING' and t['eligibility_status']=='not_assessed' for t in data['results'])
    return dict(seconds=data['seconds'],ids=[t['trial_id'] for t in data['results']],statuses=sorted({t['status'] for t in data['results']}))
def main():
    report=dict(utc=datetime.now(timezone.utc).isoformat());start=time.perf_counter()
    compose('up','-d','--no-build','--pull','never');ready()
    report['first_start_seconds']=round(time.perf_counter()-start,3)
    report['seed_log']=compose('logs','--no-color','trial-index');report['config']=get('/config')
    assert report['config']['serving_statuses']==['RECRUITING'] and not report['config']['reranking_available']
    examples=json.loads(re.search(r'const examples=(\[.*?\]);',(ROOT/'demo/app/static/app.js').read_text(encoding='utf-8')).group(1))
    report['examples']=[search(note) for note in examples]
    report['before']=identity()
    report['container_stats']=command('docker','stats','--no-stream','--format','{{json .}}',report['before']['container'])
    absent=compose('exec','-T','matcher','python','-c',"import importlib.util; assert all(importlib.util.find_spec(n) is None for n in ['numpy','torch','onnxruntime','transformers']); print('No model runtime installed')")
    report['lean_runtime']=absent
    compose('restart','matcher');report['restart_seconds']=ready();after=identity()
    assert after['container']==report['before']['container'] and after['index']==report['before']['index']
    assert search(examples[0])['ids']==report['examples'][0]['ids']
    compose('up','-d','--no-build','--pull','never','--no-deps','--force-recreate','matcher');report['replacement_seconds']=ready();after=identity()
    assert after['container']!=report['before']['container'] and after['index']==report['before']['index'] and after['volume']==report['before']['volume']
    assert search(examples[0])['ids']==report['examples'][0]['ids']
    report['after_replacement']=after
    compose('up','-d','--no-build','--pull','never');ready()
    report['reuse_log']=compose('logs','--no-color','trial-index')
    assert 'Reused verified index; no copy needed.' in report['reuse_log']
    assert identity()['index']==report['before']['index']
    report['replacement_scope']='Same app image, new app container; proves update procedure and persistent index reuse, not a second software build.'
    report['passed']=True
    path=ROOT/'reports/current-data/docker-verification.json';path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2),flush=True)
if __name__=='__main__':main()
