"""Single-core, transactional, resumable MedCPT encoding of a registry download."""
import argparse
from collections import Counter
from contextlib import closing,contextmanager
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import sys
import threading
import time

for name in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS']:os.environ[name]='1'
os.environ['TOKENIZERS_PARALLELISM']='false'
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[1]
FIELDS={'title':48,'conditions':48,'summary':96,'eligibility':61}


def now():return datetime.now(timezone.utc).isoformat(timespec='seconds')


def digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp')
    with temp.open('w',encoding='utf-8') as stream:
        json.dump(value,stream,indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())
    temp.replace(path)


def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))


def database(path):
    db=sqlite3.connect(path);db.execute('PRAGMA journal_mode=DELETE');db.execute('PRAGMA synchronous=FULL')
    db.execute('PRAGMA cache_size=-16384');db.execute('PRAGMA threads=1');return db


@contextmanager
def exclusive(path):
    with open(path,'a+b') as handle:
        if path.stat().st_size==0:handle.write(b'0');handle.flush()
        handle.seek(0)
        if os.name=='nt':
            import msvcrt
            try:msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            except OSError as exc:raise RuntimeError('This run already has a worker; refusing a second encoder') from exc
        else:
            import fcntl
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:yield
        finally:
            handle.seek(0)
            if os.name=='nt':msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(handle,fcntl.LOCK_UN)


class Monitor:
    def __init__(self,directory,duty):
        self.directory=directory;self.duty=duty;self.lock=threading.RLock()
        self.stop=threading.Event();self.finished=threading.Event()
        self.state=dict(pid=os.getpid(),stage='starting',completed=0,total=None,started_utc=now(),duty_cycle=duty,
            logical_cpu_count=os.cpu_count(),approx_cpu_target_percent=100*duty/os.cpu_count(),batch_size=4)
    def publish(self,kind,**updates):
        with self.lock:
            self.state.update(updates,observed_utc=now())
            record=dict(kind=kind,**self.state)
            save(self.directory/'progress.json',self.state)
            line=json.dumps(record)
            with (self.directory/'live.jsonl').open('a',encoding='utf-8') as stream:stream.write(line+'\n')
            with (self.directory/'live.log').open('a',encoding='utf-8') as stream:stream.write(f'[{now()}] {kind}: {line}\n')
            print(f'[{now()}] {kind}: {line}',flush=True)
    def update(self,**updates):
        with self.lock:self.state.update(updates)
    def heartbeat(self):
        cpu=time.process_time();wall=time.monotonic()
        while not self.finished.wait(30):
            current_cpu=time.process_time();current_wall=time.monotonic()
            self.publish('heartbeat',cpu_percent_of_machine=round(100*(current_cpu-cpu)/(current_wall-wall)/os.cpu_count(),2))
            cpu=current_cpu;wall=current_wall
    def stopping(self):return self.stop.is_set() or (self.directory/'stop.request').exists()
    def rest(self,cpu_started):
        delay=max(0.,(time.process_time()-cpu_started)*(1/self.duty-1))
        # Short waits remain responsive to a graceful stop marker.
        until=time.monotonic()+delay
        while not self.stopping():
            remaining=until-time.monotonic()
            if remaining<=0:break
            time.sleep(min(.5,remaining))
    def check(self):
        if self.stopping():raise InterruptedError('Graceful stop requested; committed batches are saved')


def prepare(source,directory,snapshot,monitor):
    from src.registry_json import studies
    from src.current_data import normalize
    monitor.publish('validating_source',stage='validating_source')
    identity=dict(sha256=digest(source),bytes=source.stat().st_size,snapshot_download_date=snapshot,
        schema='ctgov-json-normalization-v1')
    if (directory/'source.json').exists() and read(directory/'source.json')!=identity:
        raise RuntimeError('Downloaded source changed; choose a new run directory')
    save(directory/'source.json',identity)
    with closing(database(directory/'catalog.sqlite')) as db:
        db.execute('CREATE TABLE IF NOT EXISTS studies(trial_id TEXT PRIMARY KEY,title TEXT,conditions TEXT,brief TEXT,detailed TEXT,eligibility TEXT,status TEXT,minimum TEXT,maximum TEXT,sex TEXT,updated TEXT,posted TEXT,countries TEXT,source_bytes INTEGER)')
        db.execute('CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,value TEXT)')
        state=dict(db.execute('SELECT key,value FROM state'))
        if state.get('complete')!='true':
            done=int(state.get('imported','0'));previous=0;pending=[];last_id=None;total=0
            monitor.publish('importing',stage='importing',imported=done)
            tick=time.process_time()
            for total,(study,offset) in enumerate(studies(source,with_offsets=True),1):
                if total>done:
                    row=normalize(study);pending.append((*row,offset-previous));last_id=row[0]
                previous=offset
                if len(pending)>=256:
                    with db:
                        db.executemany('INSERT INTO studies VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',pending)
                        db.execute("INSERT OR REPLACE INTO state VALUES('imported',?)",(str(total),))
                    pending=[];monitor.update(imported=total);monitor.check();monitor.rest(tick);tick=time.process_time()
                    if total%4096==0:monitor.publish('import_checkpoint',imported=total)
            with db:
                if pending:db.executemany('INSERT INTO studies VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',pending)
                # Assign closing array/whitespace bytes to the last record. UTF-8
                # BOM, if present, is included in this remainder as well.
                if total:
                    if last_id is None:last_id=db.execute('SELECT trial_id FROM studies ORDER BY rowid DESC LIMIT 1').fetchone()[0]
                    db.execute('UPDATE studies SET source_bytes=source_bytes+? WHERE trial_id=?',(identity['bytes']-previous,last_id))
                db.executemany('INSERT OR REPLACE INTO state VALUES (?,?)',[('imported',str(total)),('complete','true')])
            if db.execute('SELECT count(*) FROM studies').fetchone()[0]!=total:raise RuntimeError('Import count mismatch')
        counts=dict(db.execute('SELECT status,count(*) FROM studies GROUP BY status'))
        expected=counts.get('RECRUITING',0)
        if expected<1:raise RuntimeError('No recruiting trials in downloaded file')
        if db.execute('SELECT sum(source_bytes) FROM studies').fetchone()[0]!=identity['bytes']:raise RuntimeError('Source byte accounting mismatch')
        profile=dict(records=sum(counts.values()),status_counts=counts,recruiting=expected,source_sha256=identity['sha256'],
            source_bytes=identity['bytes'],snapshot_download_date=snapshot,registry_release_timestamp=None)
        save(directory/'data-profile.json',profile)
        monitor.publish('source_validated',stage='building_index',total=expected,profile=profile)
        index=directory/'bm25.sqlite'
        if not index.exists():
            temporary=directory/'bm25.building.sqlite'
            with closing(database(temporary)) as target:
                target.execute("CREATE VIRTUAL TABLE IF NOT EXISTS trials USING fts5(trial_id UNINDEXED,title,conditions,summary,eligibility,tokenize='porter unicode61')")
                target.execute('CREATE TABLE IF NOT EXISTS identities(trial_id TEXT PRIMARY KEY,row_id INTEGER UNIQUE NOT NULL)')
                target.execute('CREATE TABLE IF NOT EXISTS trial_metadata(trial_id TEXT PRIMARY KEY,status TEXT,last_updated TEXT,minimum_age TEXT,maximum_age TEXT,sex TEXT,countries TEXT)')
                target.execute('CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT)')
                target.execute('CREATE TABLE IF NOT EXISTS build_state(key TEXT PRIMARY KEY,value TEXT)')
                last=target.execute('SELECT max(trial_id) FROM identities').fetchone()[0] or ''
                cursor=db.execute("SELECT * FROM studies WHERE status='RECRUITING' AND trial_id>? ORDER BY trial_id",(last,))
                while rows:=cursor.fetchmany(256):
                    tick=time.process_time()
                    with target:
                        for row in rows:
                            trial,title,conditions,brief,detailed,criteria,status,minimum,maximum,sex,updated,posted,countries,size=row
                            inserted=target.execute('INSERT INTO trials VALUES(?,?,?,?,?)',(trial,title,' '.join(json.loads(conditions)),brief+'\n'+detailed,criteria))
                            target.execute('INSERT INTO identities VALUES (?,?)',(trial,inserted.lastrowid))
                            target.execute('INSERT INTO trial_metadata VALUES(?,?,?,?,?,?,?)',(trial,status,updated,minimum,maximum,sex,countries))
                    monitor.check();monitor.rest(tick)
                vocab=Counter()
                import re
                for text, in db.execute("SELECT conditions FROM studies WHERE status='RECRUITING'"):
                    vocab.update({p for c in json.loads(text) if (p:=' '.join(re.findall(r'[a-z0-9]+',c.lower()))) and len(p.split())<=10})
                with target:
                    target.execute('CREATE TABLE IF NOT EXISTS condition_vocabulary(phrase TEXT PRIMARY KEY,df INTEGER NOT NULL)')
                    target.execute('DELETE FROM condition_vocabulary');target.executemany('INSERT INTO condition_vocabulary VALUES (?,?)',sorted(vocab.items()))
                    meta=dict(schema_version=2,records=expected,registry_records=sum(counts.values()),dataset_kind='current-recruiting',
                        source='ClinicalTrials.gov downloaded JSON',snapshot=snapshot,data_timestamp=None,serving_statuses=['RECRUITING'],
                        source_sha256=identity['sha256'],collection_notice=f'Recruiting records downloaded {snapshot}; official registry release timestamp is not present in this export. Eligibility unchecked.')
                    target.executemany('INSERT OR REPLACE INTO metadata VALUES (?,?)',[(k,json.dumps(v)) for k,v in meta.items()])
                    target.execute("INSERT INTO trials(trials) VALUES('optimize')")
                target.execute("INSERT INTO trials(trials) VALUES('integrity-check')")
                if target.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise RuntimeError('Index integrity check failed')
            temporary.replace(index)
        with closing(database(index)) as target:
            if target.execute('SELECT count(*) FROM identities').fetchone()[0]!=expected:raise RuntimeError('Index count mismatch')
    return index,identity,profile


def protocol_for(directory,index,model_dir,identity):
    p=dict(schema='current-medcpt-v1',source_sha256=identity['sha256'],source_bytes=identity['bytes'],
        index_sha256=digest(index),fields=FIELDS,article_max_tokens=256,query_max_tokens=128,batch_size=4,
        dimensions=768,similarity='dot_product',model_manifests={},onnx_manifests={})
    for kind in ['article','query']:
        folder=model_dir/kind;m=read(folder/'manifest.json');onnx=read(folder/'onnx-manifest.json')
        if digest(folder/'encoder-int8.onnx')!=onnx['onnx_sha256']:raise RuntimeError('ONNX model checksum mismatch')
        for name in ['tokenizer.json','README.md','LICENSE']:
            if digest(folder/name)!=m['files'][name]:raise RuntimeError('Model resource checksum mismatch')
        p['model_manifests'][kind]=m;p['onnx_manifests'][kind]=onnx
    if (directory/'protocol.json').exists() and read(directory/'protocol.json')!=p:raise RuntimeError('Encoding protocol changed; do not mix vectors')
    save(directory/'protocol.json',p);return p


class ArticleEncoder:
    def __init__(self,folder):
        import onnxruntime as ort
        from tokenizers import Tokenizer
        self.tokenizer=Tokenizer.from_file(str(folder/'tokenizer.json'))
        options=ort.SessionOptions();options.intra_op_num_threads=1;options.inter_op_num_threads=1
        self.model=ort.InferenceSession(str(folder/'encoder-int8.onnx'),sess_options=options,providers=['CPUExecutionProvider'])
    def encode(self,rows):
        import numpy as np
        packed=[];tok=self.tokenizer
        for row in rows:
            title=tok.encode(row[1],add_special_tokens=False).ids[:FIELDS['title']];body=[]
            for field,text in zip(['conditions','summary','eligibility'],row[2:5],strict=True):
                body.extend(tok.encode(field+': '+text,add_special_tokens=False).ids[:FIELDS[field]])
            ids=[tok.token_to_id('[CLS]')]+title+[tok.token_to_id('[SEP]')]+body+[tok.token_to_id('[SEP]')]
            packed.append((ids,[0]*(len(title)+2)+[1]*(len(body)+1)))
        length=max(len(ids) for ids,_ in packed)
        inputs={name:np.zeros((len(rows),length),dtype=np.int64) for name in ['input_ids','attention_mask','token_type_ids']}
        for i,(ids,types) in enumerate(packed):
            inputs['input_ids'][i,:len(ids)]=ids;inputs['attention_mask'][i,:len(ids)]=1;inputs['token_type_ids'][i,:len(ids)]=types
        values=self.model.run(None,inputs)[0]
        if values.shape!=(len(rows),768) or not np.isfinite(values).all():raise RuntimeError('Invalid article vectors')
        return values.astype(np.float32,copy=False)


def text_hash(row):return hashlib.sha256(json.dumps(list(row[1:5]),ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def vector_store(path,protocol_hash):
    db=database(path)
    db.execute('CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS vectors(trial_id TEXT PRIMARY KEY,input_sha256 TEXT,vector BLOB,vector_sha256 TEXT,source_bytes INTEGER,encoded_utc TEXT)')
    old=dict(db.execute('SELECT key,value FROM metadata'))
    if not old and db.execute('SELECT count(*) FROM vectors').fetchone()[0]:db.close();raise RuntimeError('Saved vectors have no protocol identity')
    if old and old.get('protocol_sha256')!=protocol_hash:db.close();raise RuntimeError('Saved vectors belong to another protocol')
    with db:db.execute("INSERT OR REPLACE INTO metadata VALUES('protocol_sha256',?)",(protocol_hash,))
    if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':db.close();raise RuntimeError('Saved vector database failed integrity check')
    return db


def encode(directory,index,model_dir,protocol,monitor):
    import numpy as np
    with closing(vector_store(directory/'vectors.sqlite',digest(directory/'protocol.json'))) as cache, closing(database(index)) as db, closing(database(directory/'catalog.sqlite')) as catalog:
        count,covered=cache.execute('SELECT count(*),coalesce(sum(source_bytes),0) FROM vectors').fetchone()
        total=db.execute('SELECT count(*) FROM identities').fetchone()[0]
        monitor.publish('resume_verified',stage='encoding',completed=count,total=total,source_bytes_covered=covered,
            source_bytes_total=protocol['source_bytes'],resumed_vectors=count,source_percent=round(100*covered/protocol['source_bytes'],4))
        cursor=db.execute('SELECT t.trial_id,t.title,t.conditions,t.summary,t.eligibility FROM identities i JOIN trials t ON t.rowid=i.row_id ORDER BY i.trial_id')
        model=None;new=0;started=time.monotonic();milestone=int(100*covered/protocol['source_bytes'])//10*10
        verified=0
        while rows:=cursor.fetchmany(4):
            monitor.check();tick=time.process_time()
            existing={r[0]:r[1:] for r in cache.execute('SELECT trial_id,input_sha256,vector,vector_sha256 FROM vectors WHERE trial_id IN ('+','.join('?' for _ in rows)+')',[r[0] for r in rows])}
            for row in rows:
                if row[0] in existing:
                    text,blob,checksum=existing[row[0]]
                    if text!=text_hash(row) or len(blob)!=768*4 or hashlib.sha256(blob).hexdigest()!=checksum or not np.isfinite(np.frombuffer(blob,dtype=np.float32)).all():
                        raise RuntimeError(f'Invalid saved vector for {row[0]}; refusing silent reuse')
            verified+=len(existing)
            if len(existing)==len(rows):continue
            if model is None:
                monitor.publish('loading_article_model',validated_cached_vectors=verified)
                model=ArticleEncoder(model_dir/'article')
            values=model.encode(rows)
            with cache:
                for row,value in zip(rows,values,strict=True):
                    size=catalog.execute('SELECT source_bytes FROM studies WHERE trial_id=?',(row[0],)).fetchone()[0]
                    blob=value.tobytes()
                    cache.execute('INSERT OR REPLACE INTO vectors VALUES (?,?,?,?,?,?)',(row[0],text_hash(row),blob,hashlib.sha256(blob).hexdigest(),size,now()))
                    if row[0] not in existing:count+=1;new+=1;covered+=size
            percent=100*covered/protocol['source_bytes'];elapsed=time.monotonic()-started
            monitor.update(completed=count,total=total,source_bytes_covered=covered,source_percent=round(percent,4),last_saved_utc=now(),last_trial_id=rows[-1][0],
                new_this_session=new,session_seconds=round(elapsed,1),trials_per_hour=round(3600*new/elapsed,2),
                estimated_remaining_hours=round((total-count)/(new/elapsed)/3600,2) if new>=32 else None)
            if count%32==0 or count==total:monitor.publish('checkpoint_saved')
            if count%256==0:monitor.publish('summary_256')
            reached=min(100,int(percent)//10*10)
            if reached>milestone:
                for level in range(milestone+10,reached+1,10):monitor.publish('milestone',milestone_percent=level)
                milestone=reached
            monitor.check();monitor.rest(tick)
        if count!=total or cache.execute('SELECT count(*) FROM vectors').fetchone()[0]!=total:raise RuntimeError('Final vector count mismatch')
        monitor.publish('encoding_complete',stage='exporting_vectors',completed=count)
        shards={};folder=directory/'vector-shards';folder.mkdir(exist_ok=True)
        cursor=cache.execute('SELECT trial_id,vector FROM vectors ORDER BY trial_id');offset=0
        while rows:=cursor.fetchmany(256):
            monitor.check();dest=folder/f'{offset:06d}.npz';temp=dest.with_suffix('.npz.tmp')
            with temp.open('wb') as stream:
                np.savez(stream,ids=np.asarray([r[0] for r in rows]),vectors=np.stack([np.frombuffer(r[1],dtype=np.float32) for r in rows]))
                stream.flush();os.fsync(stream.fileno())
            temp.replace(dest);shards[dest.name]=digest(dest);offset+=len(rows)
        save(folder/'complete.json',dict(records=total,protocol_sha256=digest(directory/'protocol.json'),index_sha256=digest(index),shards=shards))
    return folder


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True);parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--snapshot-date',required=True,help='Download date, not an asserted registry release timestamp')
    parser.add_argument('--model-dir',type=Path,default=ROOT/'models/medcpt-retrieval')
    parser.add_argument('--duty-cycle',type=float,default=.5)
    parser.add_argument('--clear-stop',action='store_true')
    args=parser.parse_args()
    if not 0<args.duty_cycle<=1:parser.error('duty-cycle must be >0 and <=1')
    from experiments.medcpt_hybrid.pilot import cap_single_cpu
    from experiments.medcpt_hybrid.run import low_priority
    cap_single_cpu();low_priority()
    directory=args.run_dir.resolve();directory.mkdir(parents=True,exist_ok=True)
    with exclusive(directory/'worker.lock'):
        if args.clear_stop:(directory/'stop.request').unlink(missing_ok=True)
        monitor=Monitor(directory,args.duty_cycle)
        signal.signal(signal.SIGINT,lambda *_:monitor.stop.set())
        if hasattr(signal,'SIGTERM'):signal.signal(signal.SIGTERM,lambda *_:monitor.stop.set())
        thread=threading.Thread(target=monitor.heartbeat,daemon=True);thread.start()
        try:
            monitor.publish('started',source_path=str(args.source.resolve()))
            index,identity,profile=prepare(args.source.resolve(),directory,args.snapshot_date,monitor)
            monitor.check();protocol=protocol_for(directory,index,args.model_dir,identity)
            cache=encode(directory,index,args.model_dir,protocol,monitor)
            spec=dict(source_index=str(index),source_index_sha256=digest(index),vector_cache=str(cache),protocol=str(directory/'protocol.json'),
                query_model_dir=str(args.model_dir.resolve()/'query'),output_dir=str(directory/'serving-bundle'))
            save(directory/'bundle-spec.json',spec)
            monitor.check();monitor.publish('packaging',stage='packaging')
            sys.path.insert(0,str(ROOT/'demo'))
            from build_hybrid_bundle import build
            if not (directory/'serving-bundle').exists():build(directory/'bundle-spec.json')
            monitor.publish('complete',stage='complete',finished_utc=now(),bundle_path=str(directory/'serving-bundle'))
        except InterruptedError as exc:monitor.publish('stopped',stage='stopped',reason=str(exc))
        except Exception as exc:
            monitor.publish('failed',stage='failed',error=repr(exc));raise
        finally:monitor.finished.set();thread.join(timeout=2)


if __name__=='__main__':main()
