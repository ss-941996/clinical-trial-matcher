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
    def __init__(self,index_path,model_dir=None,expected_records=375580,config=None):
        self.index_path=Path(index_path).resolve();self.model_dir=Path(model_dir) if model_dir else None
        self.expected_records=expected_records;self.encoder=None;self.ready=False;self.config=config or {}
        self.vocab=json.loads((Path(__file__).parent/'assets/condition_vocabulary.json').read_text(encoding='utf-8'))

    def connect(self):
        db=sqlite3.connect(self.index_path.as_uri()+'?mode=ro',uri=True)
        db.execute('PRAGMA cache_size=-65536');return db

    def load(self):
        if not self.index_path.is_file():raise RuntimeError(f'Trial index not found: {self.index_path}')
        with closing(self.connect()) as db:
            self.metadata={k:json.loads(v) for k,v in db.execute('SELECT key,value FROM metadata')}
            if self.metadata.get('records')!=self.expected_records:raise RuntimeError('Trial index record count differs from expected snapshot')
            db.execute('SELECT rowid FROM trials LIMIT 1').fetchone()
        if self.model_dir:self.encoder=CrossEncoder(self.model_dir)
        self.ready=True

    def search(self,note,method='bm25',top_k=10):
        if not self.ready:raise RuntimeError('Index is not ready')
        if method not in {'bm25','fusion','rerank'}:raise ValueError('Unknown method')
        if method=='rerank' and self.encoder is None:raise ValueError('Reranking model is unavailable')
        started=time.perf_counter()
        with closing(self.connect()) as db:
            pool=self.config.get('rerank_pool','baseline') if method=='rerank' else 'lexical_rrf' if method=='fusion' else 'baseline'
            if pool=='lexical_rrf':
                balanced=method=='fusion' and self.config.get('fusion_balance_weight') is not None
                hits=rank_fusion([search_fts(db,q,1000,True) for q in [note,focused(note,self.vocab),opening(note)]],k=1000 if balanced else 200)
                if balanced:hits=rank_fusion([search_fts(db,note,1000),hits],weights=[1.,self.config['fusion_balance_weight']])
            else:hits=search_fts(db,note)
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
                results.append(dict(rank=i,trial_id=trial_id,title=document['title'],conditions=document['conditions'],
                    eligibility_text=document['eligibility'],retrieval_rank=positions[trial_id],retrieval_score=score_map[trial_id],
                    reranker_score=ce_scores.get(trial_id),eligibility_status='not_assessed',
                    registry_url='https://clinicaltrials.gov/study/'+trial_id))
        return dict(schema_version='1.0',method=method,snapshot='2021-04-27',indexed_trials=self.metadata['records'],
            candidate_count=len(original),rerank_scored_pairs=len(ce_scores),seconds=round(time.perf_counter()-started,3),results=results,
            notice='Research retrieval only. Eligibility is not assessed. Historical records may no longer be recruiting.')
