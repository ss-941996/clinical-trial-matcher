"""Disk-backed BM25 baseline, independent of relevance judgments."""
from __future__ import annotations
import argparse
from contextlib import closing, contextmanager
import json
from pathlib import Path
import re
import sqlite3
import time
import pyarrow.parquet as pq
from src.common import ROOT, now, sha256, write_json

STOPWORDS = frozenset('a an and are as at be been being but by for from had has have he her hers him his i if in into is it its of on or our she that the their them there these they this those to was we were what when where which who will with you your'.split())

def query_terms(note):
    return list(dict.fromkeys(t for t in re.findall(r'[a-z0-9]+', note.lower()) if len(t)>=2 and t not in STOPWORDS))[:512]

@contextmanager
def connect(index):
    with closing(sqlite3.connect(Path(index).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        # Bounded local read cache; the multi-GB index remains on disk.
        db.execute('PRAGMA cache_size=-131072')
        yield db

def build_index(parquet, index):
    parquet,index=Path(parquet),Path(index)
    index.parent.mkdir(parents=True,exist_ok=True)
    temp=index.with_suffix('.building.sqlite')
    if temp.exists(): raise ValueError(f'Incomplete build exists at {temp}; inspect before retrying')
    started=time.perf_counter()
    with closing(sqlite3.connect(temp)) as db:
        db.execute('PRAGMA journal_mode=DELETE')
        db.execute('PRAGMA synchronous=NORMAL')
        db.execute('PRAGMA cache_size=-65536')
        db.execute("CREATE VIRTUAL TABLE trials USING fts5(trial_id UNINDEXED,title,conditions,summary,eligibility,tokenize='porter unicode61')")
        db.execute('CREATE TABLE identities(trial_id TEXT PRIMARY KEY, row_id INTEGER UNIQUE NOT NULL)')
        db.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        count=0
        for batch in pq.ParquetFile(parquet).iter_batches(batch_size=1000,columns=['trial_id','title','conditions','summary','detailed_description','eligibility']):
            for r in batch.to_pylist():
                cursor=db.execute('INSERT INTO trials(trial_id,title,conditions,summary,eligibility) VALUES (?,?,?,?,?)',
                    (r['trial_id'],r['title'],' '.join(r['conditions']),r['summary']+'\n'+r['detailed_description'],r['eligibility']))
                db.execute('INSERT INTO identities VALUES (?,?)',(r['trial_id'],cursor.lastrowid))
                count+=1
            db.commit()
            if count%25000==0: print(f'Indexed {count:,} trials',flush=True)
        db.execute("INSERT INTO trials(trials) VALUES('optimize')")
        metadata=dict(records=count,parquet_sha256=sha256(parquet),created_at=now(),
            tokenizer='porter unicode61',field_weights=[0.0,1.0,1.0,1.0,1.0],
            summary_field='brief summary plus detailed description',seconds=time.perf_counter()-started,
            sqlite_version=sqlite3.sqlite_version)
        db.executemany('INSERT INTO metadata VALUES (?,?)',[(k,json.dumps(v)) for k,v in metadata.items()])
        db.commit()
        db.execute("INSERT INTO trials(trials) VALUES('integrity-check')")
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise ValueError('SQLite integrity check failed')
    temp.replace(index)
    write_json(index.with_suffix('.json'),metadata)
    print(f'Built BM25 index: {count:,} trials in {metadata["seconds"]:.1f}s',flush=True)

def search(db,note,k=200):
    if not 1<=k<=1000: raise ValueError('k must be between 1 and 1000')
    tokens=query_terms(note)
    if not tokens: return []
    query=' OR '.join('"'+t+'"' for t in tokens)
    # SQLite BM25 returns lower (more negative) scores for better matches.
    # Native rank ordering lets FTS5 optimize top-k selection. Expand boundary
    # ties before sorting IDs, so the result exactly matches an exhaustive sort.
    limit=k+1
    while True:
        rows=db.execute('SELECT trial_id,title,rank FROM trials WHERE trials MATCH ? ORDER BY rank LIMIT ?', (query,limit)).fetchall()
        if len(rows)<=k or len(rows)<limit or rows[-1][2]!=rows[k-1][2]:
            break
        limit*=2
    rows.sort(key=lambda r:(r[2],r[0]))
    return [dict(trial_id=r[0],title=r[1],score=-r[2]) for r in rows[:k]]

def metadata(db):
    return {k:json.loads(v) for k,v in db.execute('SELECT key,value FROM metadata')}

def lookup(db,trial_id):
    row=db.execute('SELECT title,conditions,eligibility FROM trials WHERE rowid=(SELECT row_id FROM identities WHERE trial_id=?)',(trial_id,)).fetchone()
    return dict(title=row[0],conditions=row[1],eligibility=row[2]) if row else None

def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='action',required=True)
    build=sub.add_parser('build'); build.add_argument('--input',type=Path,default=ROOT/'data/clean/trials.parquet'); build.add_argument('--index',type=Path,default=ROOT/'data/indexes/bm25.sqlite')
    run=sub.add_parser('search'); run.add_argument('--index',type=Path,default=ROOT/'data/indexes/bm25.sqlite')
    note=run.add_mutually_exclusive_group(required=True); note.add_argument('--note'); note.add_argument('--note-file',type=Path)
    run.add_argument('--top-k',type=int,default=10); run.add_argument('--output',type=Path)
    a=p.parse_args()
    if a.action=='build': build_index(a.input,a.index)
    else:
        text=a.note if a.note is not None else a.note_file.read_text(encoding='utf-8-sig')
        with connect(a.index) as db: results=search(db,text,a.top_k)
        result=dict(method='BM25 lexical relevance only',notice='Research baseline. Eligibility has not been checked. Historical records may not be recruiting.',results=results)
        if a.output: write_json(a.output,result)
        else: print(json.dumps(result,indent=2,ensure_ascii=False))
if __name__=='__main__': main()
