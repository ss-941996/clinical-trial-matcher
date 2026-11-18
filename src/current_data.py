"""Versioned ClinicalTrials.gov backfill and recruiting-only serving migration."""
from __future__ import annotations
import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import ctypes
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request

API='https://clinicaltrials.gov/api/v2/'
FIELDS='NCTId,BriefTitle,OfficialTitle,Condition,BriefSummary,DetailedDescription,EligibilityCriteria,MinimumAge,MaximumAge,Sex,OverallStatus,LastUpdatePostDate,StudyFirstPostDate,LocationCountry'

def now():return datetime.now(timezone.utc).isoformat()

def low_priority():
    for name in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS']:os.environ[name]='1'
    if os.name=='nt':
        kernel=ctypes.windll.kernel32
        kernel.GetCurrentProcess.restype=ctypes.c_void_p
        kernel.SetPriorityClass.argtypes=[ctypes.c_void_p,ctypes.c_uint32]
        if not kernel.SetPriorityClass(kernel.GetCurrentProcess(),0x4000):raise RuntimeError('Cannot lower process priority')
    elif hasattr(os,'nice'):os.nice(max(0,10-os.nice(0)))

def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8');temp.replace(path)

def read(path):return json.loads(path.read_text(encoding='utf-8'))

def request(endpoint,params=None):
    url=API+endpoint+('?' + urllib.parse.urlencode(params) if params else '')
    for attempt in range(6):
        try:
            req=urllib.request.Request(url,headers={'User-Agent':'ClinicalTrialMatcher-Research/1.0','Accept':'application/json','Accept-Encoding':'gzip'})
            with urllib.request.urlopen(req,timeout=120) as response:
                payload=response.read()
                if response.headers.get('Content-Encoding')=='gzip':payload=gzip.decompress(payload)
            return json.loads(payload)
        except urllib.error.HTTPError as exc:
            if exc.code not in {429,500,502,503,504} or attempt==5:raise
            wait=min(30,max(2**attempt,int(exc.headers.get('Retry-After','0')) if exc.headers.get('Retry-After','').isdigit() else 0))
            print(f'HTTP {exc.code}; retry in {wait}s',flush=True);time.sleep(wait)
        except (urllib.error.URLError,TimeoutError):
            if attempt==5:raise
            time.sleep(min(30,2**attempt))

def release_id(version):return 'ctgov-'+re.sub(r'[^0-9T]','',version['dataTimestamp'])

def fetch(root):
    version=request('version');release=release_id(version);directory=root/'data/raw/ctgov'/release
    directory.mkdir(parents=True,exist_ok=True);path=directory/'download.json'
    state=read(path) if path.exists() else dict(release=release,version=version,fields=FIELDS,page_size=1000,
        source=API+'studies',started_utc=now(),pages=[],complete=False)
    if state['fields']!=FIELDS or state['page_size']!=1000 or state['version']!=version:raise ValueError('Incompatible download checkpoint')
    for row in state['pages']:
        if sha(directory/row['file'])!=row['sha256']:raise ValueError('Corrupt cached page')
    if not state['complete']:
        token=state['pages'][-1]['next_token'] if state['pages'] else None
        while not state['pages'] or token:
            params=dict(format='json',pageSize=1000,countTotal='true',fields=FIELDS)
            if token:params['pageToken']=token
            data=request('studies',params)
            if not isinstance(data.get('studies'),list) or not data['studies']:raise ValueError('Empty or malformed registry page')
            total=data.get('totalCount',state.get('expected_records'))
            if total is None:raise ValueError('First registry page did not report a total count')
            if state.get('expected_records',total)!=total:raise ValueError('Registry count changed during pagination; start a fresh release')
            state['expected_records']=total;token=data.get('nextPageToken')
            file=directory/f'page-{len(state["pages"])+1:05}.json.gz'
            payload=gzip.compress(json.dumps(data,separators=(',',':'),ensure_ascii=False).encode(),compresslevel=1,mtime=0)
            temp=file.with_suffix('.tmp');temp.write_bytes(payload);temp.replace(file)
            state['pages'].append(dict(file=file.name,sha256=sha(file),records=len(data['studies']),next_token=token))
            save(path,state)
            count=sum(row['records'] for row in state['pages'])
            print(f'Backfill: {count:,}/{total:,} records; {len(state["pages"])} pages cached',flush=True)
            time.sleep(.5)
        if sum(row['records'] for row in state['pages'])!=state['expected_records']:raise ValueError('Incomplete record count')
        if request('version')!=version:raise ValueError('Source release changed during pagination; do not publish this mixed window')
        state.update(complete=True,completed_utc=now());save(path,state)
    save(root/'data/manifests/ctgov-download.json',dict(release=release,download_manifest=path.relative_to(root).as_posix(),sha256=sha(path),version=version,records=state['expected_records']))
    return directory,state

def normalize(study):
    p=study.get('protocolSection',{});ident=p.get('identificationModule',{});status=p.get('statusModule',{})
    description=p.get('descriptionModule',{});eligible=p.get('eligibilityModule',{})
    trial=ident.get('nctId','')
    if not re.fullmatch(r'NCT\d{8}',trial):raise ValueError('Missing/invalid trial ID')
    conditions=p.get('conditionsModule',{}).get('conditions',[])
    countries=sorted({loc['country'] for loc in p.get('contactsLocationsModule',{}).get('locations',[]) if loc.get('country')})
    return (trial,ident.get('briefTitle') or ident.get('officialTitle') or '',json.dumps(conditions,ensure_ascii=False),
        description.get('briefSummary',''),description.get('detailedDescription',''),eligible.get('eligibilityCriteria',''),
        status.get('overallStatus','UNKNOWN'),eligible.get('minimumAge',''),eligible.get('maximumAge',''),eligible.get('sex','UNKNOWN'),
        status.get('lastUpdatePostDateStruct',{}).get('date',''),status.get('studyFirstPostDateStruct',{}).get('date',''),json.dumps(countries,ensure_ascii=False))

def writer(path):
    db=sqlite3.connect(path);db.execute('PRAGMA journal_mode=DELETE');db.execute('PRAGMA synchronous=NORMAL')
    db.execute('PRAGMA cache_size=-65536');db.execute('PRAGMA threads=1');db.execute('PRAGMA temp_store=FILE');return db

def build(root,directory,state):
    if not state['complete']:raise ValueError('Download is incomplete')
    output=root/'data/current'/state['release'];output.mkdir(parents=True,exist_ok=True)
    catalog=output/'catalog.sqlite';source_hash=sha(directory/'download.json')
    if not catalog.exists():
        temporary=output/'catalog.building.sqlite'
        with closing(writer(temporary)) as db:
            db.execute('CREATE TABLE IF NOT EXISTS studies(trial_id TEXT PRIMARY KEY,title TEXT,conditions TEXT,brief_summary TEXT,detailed_description TEXT,eligibility TEXT,status TEXT,minimum_age TEXT,maximum_age TEXT,sex TEXT,last_updated TEXT,first_posted TEXT,countries TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS build_state(key TEXT PRIMARY KEY,value TEXT)')
            progress=dict(db.execute('SELECT key,value FROM build_state'))
            if progress and progress['source_sha256']!=source_hash:raise ValueError('Different source in partial catalog')
            last=int(progress.get('last_page','0'))
            for number,page in enumerate(state['pages'],1):
                if number<=last:continue
                if sha(directory/page['file'])!=page['sha256']:raise ValueError('Cached page checksum mismatch')
                with gzip.open(directory/page['file'],'rt',encoding='utf-8') as stream:data=json.load(stream)
                with db:
                    db.executemany('INSERT INTO studies VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',[normalize(s) for s in data['studies']])
                    db.executemany('INSERT OR REPLACE INTO build_state VALUES (?,?)',[('source_sha256',source_hash),('last_page',str(number))])
                if number%10==0:print(f'Catalog: {number}/{len(state["pages"])} pages',flush=True)
            if db.execute('SELECT count(*) FROM studies').fetchone()[0]!=state['expected_records']:raise ValueError('Catalog count mismatch')
            with db:db.execute('CREATE INDEX IF NOT EXISTS studies_status ON studies(status)')
            if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Catalog integrity failure')
        temporary.replace(catalog)
    index=output/'bm25.sqlite'
    with closing(sqlite3.connect(catalog.as_uri()+'?mode=ro',uri=True)) as source:
        catalog_state=dict(source.execute('SELECT key,value FROM build_state'))
        if catalog_state.get('source_sha256')!=source_hash:raise ValueError('Catalog source mismatch')
        counts=dict(source.execute('SELECT status,count(*) FROM studies GROUP BY status'))
        if sum(counts.values())!=state['expected_records']:raise ValueError('Catalog count mismatch')
        expected=counts.get('RECRUITING',0)
        if expected<1:raise ValueError('No recruiting studies')
        if not index.exists():
            temporary=output/'bm25.building.sqlite'
            with closing(writer(temporary)) as db:
                db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS trials USING fts5(trial_id UNINDEXED,title,conditions,summary,eligibility,tokenize='porter unicode61')")
                db.execute('CREATE TABLE IF NOT EXISTS identities(trial_id TEXT PRIMARY KEY,row_id INTEGER UNIQUE NOT NULL)')
                db.execute('CREATE TABLE IF NOT EXISTS trial_metadata(trial_id TEXT PRIMARY KEY,status TEXT,last_updated TEXT,minimum_age TEXT,maximum_age TEXT,sex TEXT,countries TEXT)')
                db.execute('CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
                db.execute('CREATE TABLE IF NOT EXISTS build_state(key TEXT PRIMARY KEY,value TEXT)')
                progress=dict(db.execute('SELECT key,value FROM build_state'))
                if progress and progress.get('source_sha256')!=source_hash:raise ValueError('Different partial index source')
                last=progress.get('last_id','')
                cursor=source.execute("SELECT * FROM studies WHERE status='RECRUITING' AND trial_id>? ORDER BY trial_id",(last,))
                while rows:=cursor.fetchmany(1000):
                    with db:
                        for row in rows:
                            trial,title,conditions,brief,detailed,criteria,status,minimum,maximum,sex,updated,posted,countries=row
                            inserted=db.execute('INSERT INTO trials VALUES (?,?,?,?,?)',(trial,title,' '.join(json.loads(conditions)),brief+'\n'+detailed,criteria))
                            db.execute('INSERT INTO identities VALUES (?,?)',(trial,inserted.lastrowid))
                            db.execute('INSERT INTO trial_metadata VALUES (?,?,?,?,?,?,?)',(trial,status,updated,minimum,maximum,sex,countries))
                        db.executemany('INSERT OR REPLACE INTO build_state VALUES (?,?)',[('source_sha256',source_hash),('last_id',rows[-1][0])])
                    print(f'Recruiting index: {db.execute("SELECT count(*) FROM identities").fetchone()[0]:,}/{expected:,}',flush=True);time.sleep(.05)
                vocab=Counter()
                for (text,) in source.execute("SELECT conditions FROM studies WHERE status='RECRUITING'"):
                    phrases={' '.join(re.findall(r'[a-z0-9]+',condition.lower())) for condition in json.loads(text)}
                    vocab.update(phrase for phrase in phrases if phrase and len(phrase.split())<=10)
                with db:
                    db.execute('CREATE TABLE IF NOT EXISTS condition_vocabulary(phrase TEXT PRIMARY KEY,df INTEGER NOT NULL)')
                    db.execute('DELETE FROM condition_vocabulary')
                    db.executemany('INSERT INTO condition_vocabulary VALUES (?,?)',sorted(vocab.items()))
                    metadata=dict(schema_version=2,records=expected,registry_records=state['expected_records'],dataset_kind='current-recruiting',
                        source='ClinicalTrials.gov API v2',snapshot=state['version']['dataTimestamp'][:10],data_timestamp=state['version']['dataTimestamp'],
                        api_version=state['version']['apiVersion'],serving_statuses=['RECRUITING'],source_sha256=source_hash,created_at=now(),
                        tokenizer='porter unicode61',field_weights=[0.,1.,1.,1.,1.],summary_field='brief summary plus detailed description')
                    db.executemany('INSERT OR REPLACE INTO metadata VALUES (?,?)',[(key,json.dumps(value)) for key,value in metadata.items()])
                print('Optimizing recruiting index (one writer)',flush=True)
                with db:db.execute("INSERT INTO trials(trials) VALUES('optimize')")
                if db.execute('SELECT count(*) FROM identities').fetchone()[0]!=expected:raise ValueError('Serving count mismatch')
                db.execute("INSERT INTO trials(trials) VALUES('integrity-check')")
                if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Serving integrity failure')
            temporary.replace(index)
        with closing(sqlite3.connect(index.as_uri()+'?mode=ro',uri=True)) as db:
            metadata={key:json.loads(value) for key,value in db.execute('SELECT key,value FROM metadata')}
            if metadata.get('source_sha256')!=source_hash or metadata.get('records')!=expected:raise ValueError('Serving release mismatch')
        profile=dict(registry_records=state['expected_records'],status_counts=counts,serving_records=expected,
            missing={key:source.execute(f"SELECT count(*) FROM studies WHERE {key}='' ").fetchone()[0] for key in ['title','brief_summary','eligibility','last_updated']},
            duplicate_ids=0,parse_failures=0,scope='Full registry for selected fields kept locally; serving FTS contains RECRUITING only. No eligibility assessment or current-corpus relevance evaluation.')
    manifest=dict(dataset_id=state['release']+'-recruiting',source=API+'studies',version=state['version'],
        index=index.relative_to(root).as_posix(),catalog=catalog.relative_to(root).as_posix(),records=expected,registry_records=state['expected_records'],
        bytes=index.stat().st_size,sha256=sha(index),download_manifest_sha256=source_hash,serving_statuses=['RECRUITING'],created_utc=now())
    save(root/'data/manifests/ctgov-current.json',manifest)
    save(root/'reports/current-data/profile.json',profile)
    print(json.dumps(manifest,indent=2),flush=True)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--stage',choices=['fetch','build'],required=True)
    args=parser.parse_args();root=args.root.resolve();low_priority()
    if args.stage=='fetch':fetch(root)
    else:
        receipt=read(root/'data/manifests/ctgov-download.json');path=root/receipt['download_manifest']
        if sha(path)!=receipt['sha256']:raise ValueError('Download manifest changed')
        build(root,path.parent,read(path))

if __name__=='__main__':main()
