from collections import Counter
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
import time
from .rerank import CrossEncoder,trial_document,ordered_candidates,fuse

STOPWORDS=frozenset('a an and are as at be been being but by for from had has have he her hers him his i if in into is it its of on or our she that the their them there these they this those to was we were what when where which who will with you your'.split())

def query_terms(note):
    return list(dict.fromkeys(t for t in re.findall(r'[a-z0-9]+',note.lower()) if len(t)>=2 and t not in STOPWORDS))[:512]

def focused(note,vocab):
    tokens=re.findall(r'[a-z0-9]+',note.lower());found=[]
    for i in range(len(tokens)):
        for n in range(min(10,len(tokens)-i),0,-1):
            p=' '.join(tokens[i:i+n])
            if p in vocab:found.append((i,i+n,p,vocab[p]));break
    chosen=[];occupied=set()
    for i,j,p,df in sorted(found,key=lambda x:(-(x[1]-x[0]),x[3],x[0])):
        if occupied.intersection(range(i,j)) or any(p==x[2] for x in chosen):continue
        chosen.append((i,j,p,df));occupied.update(range(i,j))
        if len(chosen)==6:break
    return ' '.join(x[2] for x in sorted(chosen)) if chosen else opening(note)

def opening(note):
    return ' '.join(re.split(r'(?<=[.!?])\s+(?=[A-Z])',note,maxsplit=1)[0].split()[:60])

def search_fts(db,note,k=200,weighted=False):
    terms=query_terms(note)
    if not terms:return []
    query=' OR '.join('"'+x+'"' for x in terms);limit=k+1
    while True:
        sql='SELECT trial_id,rank FROM trials WHERE trials MATCH ?';args=[query]
        if weighted:sql+=' AND rank MATCH ?';args.append('bm25(0,3,5,1,0.5)')
        rows=db.execute(sql+' ORDER BY rank LIMIT ?',[*args,limit]).fetchall()
        if len(rows)<=k or len(rows)<limit or rows[-1][1]!=rows[k-1][1]:break
        limit*=2
    rows.sort(key=lambda r:(r[1],r[0]));return [(r[0],-r[1]) for r in rows[:k]]

def rank_fusion(lists,k=200,weights=None):
    scores=Counter()
    weights=weights or [1.]*len(lists)
    for ranking,weight in zip(lists,weights,strict=True):
        for i,(trial,_) in enumerate(ranking,1):scores[trial]+=weight/(60+i)
    return sorted(scores.items(),key=lambda x:(-x[1],x[0]))[:k]

class Engine:
    def __init__(self,index_path,model_dir=None,expected_records=375580,config=None,vector_dir=None):
        self.index_path=Path(index_path).resolve();self.model_dir=Path(model_dir) if model_dir else None
        self.expected_records=expected_records;self.encoder=None;self.ready=False;self.config=config or {}
        self.vector_dir=vector_dir;self.semantic=None
        self.vocab=json.loads((Path(__file__).parent/'assets/condition_vocabulary.json').read_text(encoding='utf-8'))

    def connect(self):
        db=sqlite3.connect(self.index_path.as_uri()+'?mode=ro',uri=True)
        db.execute('PRAGMA cache_size=-65536');return db

    def load(self):
        self.ready=False
        if not self.index_path.is_file():raise RuntimeError(f'Trial index not found: {self.index_path}')
        with closing(self.connect()) as db:
            self.metadata={k:json.loads(v) for k,v in db.execute('SELECT key,value FROM metadata')}
            if self.expected_records is not None and self.metadata.get('records')!=self.expected_records:raise RuntimeError('Trial index record count differs from expected snapshot')
            db.execute('SELECT rowid FROM trials LIMIT 1').fetchone()
            if not isinstance(self.metadata.get('records'),int) or self.metadata['records']<1:raise RuntimeError('Invalid index record count')
            if self.metadata.get('dataset_kind')=='current-recruiting':
                if self.metadata.get('schema_version')!=2 or self.metadata.get('serving_statuses')!=['RECRUITING']:raise RuntimeError('Invalid recruiting index contract')
                counts=dict(db.execute('SELECT status,count(*) FROM trial_metadata GROUP BY status'))
                if counts!={'RECRUITING':self.metadata['records']} or db.execute('SELECT count(*) FROM identities').fetchone()[0]!=self.metadata['records']:raise RuntimeError('Recruiting record count mismatch')
                self.vocab=dict(db.execute('SELECT phrase,df FROM condition_vocabulary'))
        if self.vector_dir:
            from .semantic import SemanticIndex
            self.semantic=SemanticIndex(self.vector_dir,self.index_path)
            with closing(self.connect()) as db:
                if self.semantic.ids!={r[0] for r in db.execute('SELECT trial_id FROM identities')}:
                    raise RuntimeError('Lexical and semantic trial identities differ')
        if self.config.get('default_method')=='hybrid' and self.semantic is None:
            raise RuntimeError('Hybrid search requires a matching MedCPT vector bundle; see demo/HYBRID.md')
        if self.model_dir and self.config.get('default_method')!='hybrid':self.encoder=CrossEncoder(self.model_dir)
        self.ready=True

    def dataset_info(self):
        m=self.metadata;current=m.get('dataset_kind')=='current-recruiting'
        return dict(snapshot=m.get('snapshot','2021-04-27'),indexed_trials=m['records'],
            source=m.get('source','TREC 2021 historical ClinicalTrials.gov snapshot'),
            data_timestamp=m.get('data_timestamp'),serving_statuses=m.get('serving_statuses',[]),
            registry_records=m.get('registry_records',m['records']),historical=not current,
            collection_notice=m.get('collection_notice',''))

    def search(self,note,method='bm25',top_k=10):
        if not self.ready:raise RuntimeError('Index is not ready')
        if method not in {'bm25','fusion','rerank','hybrid'}:raise ValueError('Unknown method')
        if method=='hybrid' and self.semantic is None:raise ValueError('MedCPT vector index is unavailable')
        if method=='rerank' and self.encoder is None:raise ValueError('Reranking model is unavailable')
        started=time.perf_counter()
        with closing(self.connect()) as db:
            pool=self.config.get('rerank_pool','baseline') if method=='rerank' else 'lexical_rrf' if method in {'fusion','hybrid'} else 'baseline'
            if pool=='lexical_rrf':
                balanced=method=='hybrid' or method=='fusion' and self.config.get('fusion_balance_weight') is not None
                hits=rank_fusion([search_fts(db,q,1000,True) for q in [note,focused(note,self.vocab),opening(note)]],k=1000 if balanced else 200)
                if balanced:hits=rank_fusion([search_fts(db,note,1000),hits],k=1000 if method=='hybrid' else 200,weights=[1.,self.config.get('fusion_balance_weight',2.)])
            else:hits=search_fts(db,note)
            lexical_seconds=time.perf_counter()-started;dense_seconds=0.
            if method=='hybrid':
                tick=time.perf_counter();dense=self.semantic.search(note,1000)
                dense_seconds=time.perf_counter()-tick
                hits=rank_fusion([hits,dense],k=200,weights=[1.,self.config.get('semantic_rrf_weight',1.)])
            original=[d for d,_ in hits];score_map=dict(hits);positions={d:i for i,d in enumerate(original,1)}
            ranking=original;ce_scores={}
            if method=='rerank' and ranking:
                # Dynamic quantization depends on batch composition. Reproduce the
                # benchmark's sorted union of both fixed candidate pools exactly.
                other=rank_fusion([search_fts(db,q,1000,True) for q in [note,focused(note,self.vocab),opening(note)]]) if pool=='baseline' else search_fts(db,note)
                scoring_ids=sorted(set(original).union(d for d,_ in other))
                docs=[trial_document(db,d) for d in scoring_ids]
                ce_scores=dict(zip(scoring_ids,self.encoder.predict(note,docs),strict=True))
                reranked=ordered_candidates(original,{d:ce_scores[d] for d in original})
                weight=self.config.get('ce_blend_weight',1.)
                ranking=reranked if weight is None else fuse(original,reranked,float(weight))
            results=[]
            for i,trial_id in enumerate(ranking[:top_k],1):
                document=trial_document(db,trial_id)
                extra=dict(status='UNKNOWN',last_updated=None)
                if self.metadata.get('dataset_kind')=='current-recruiting':
                    row=db.execute('SELECT status,last_updated,minimum_age,maximum_age,sex,countries FROM trial_metadata WHERE trial_id=?',(trial_id,)).fetchone()
                    if row is None:raise RuntimeError('Missing trial metadata')
                    extra=dict(zip(['status','last_updated','minimum_age','maximum_age','sex','countries'],row,strict=True));extra['countries']=json.loads(extra['countries'])
                results.append(dict(rank=i,trial_id=trial_id,title=document['title'],conditions=document['conditions'],
                    eligibility_text=document['eligibility'],retrieval_rank=positions[trial_id],retrieval_score=score_map[trial_id],
                    reranker_score=ce_scores.get(trial_id),eligibility_status='not_assessed',
                    registry_url='https://clinicaltrials.gov/study/'+trial_id,**extra))
        return dict(schema_version='1.1',method=method,**self.dataset_info(),
            candidate_count=len(original),rerank_scored_pairs=len(ce_scores),seconds=round(time.perf_counter()-started,3),results=results,
            pipeline='lexical_medcpt_rrf' if method=='hybrid' else method,
            timing=dict(lexical_seconds=round(lexical_seconds,3),semantic_seconds=round(dense_seconds,3)),
            notice='Research retrieval only. Eligibility is not assessed. Registry status is reported as of the snapshot; verify the current record and contact the study team.')
