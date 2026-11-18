"""CPU cross-encoder relevance reranking; scores are not eligibility probabilities."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import math

MODEL='cross-encoder/ms-marco-MiniLM-L6-v2'
REVISION='233902d25c440f23af6f7d6e94d2946bac0bee0a'
GRAPH='onnx/model_quint8_avx2.onnx'
FIELD_TOKENS={'title':64,'conditions':48,'summary':112,'eligibility':157}
QUERY_TOKENS=128

def trial_document(db,trial_id):
    row=db.execute('SELECT title,conditions,summary,eligibility FROM trials WHERE rowid=(SELECT row_id FROM identities WHERE trial_id=?)',(trial_id,)).fetchone()
    if row is None:raise ValueError(f'Trial missing: {trial_id}')
    return dict(zip(FIELD_TOKENS,row,strict=True))

def pack_pair(tokenizer,note,document):
    query=tokenizer.encode(note,add_special_tokens=False).ids[:QUERY_TOKENS]
    passage=[]
    for field,limit in FIELD_TOKENS.items():
        passage.extend(tokenizer.encode(field+': '+document.get(field,''),add_special_tokens=False).ids[:limit])
    ids=[tokenizer.token_to_id('[CLS]')]+query+[tokenizer.token_to_id('[SEP]')]+passage+[tokenizer.token_to_id('[SEP]')]
    types=[0]*(len(query)+2)+[1]*(len(passage)+1)
    if len(ids)>512 or any(i is None for i in ids):raise ValueError('Invalid packed model input')
    return ids,types

def ordered_candidates(candidates,scores):
    if len(candidates)!=len(set(candidates)):raise ValueError('Duplicate candidate')
    if set(candidates)!=set(scores) or not all(math.isfinite(scores[d]) for d in candidates):raise ValueError('Invalid candidate score mapping')
    return sorted(candidates,key=lambda d:(-scores[d],d))

def fuse(original,reranked,ce_weight):
    if set(original)!=set(reranked):raise ValueError('Reranking must preserve candidates')
    scores={d:1/(60+i) for i,d in enumerate(original,1)}
    for i,d in enumerate(reranked,1):scores[d]+=ce_weight/(60+i)
    return sorted(original,key=lambda d:(-scores[d],d))

class CrossEncoder:
    def __init__(self,model_dir,threads=4):
        import onnxruntime as ort
        from tokenizers import Tokenizer
        model_dir=Path(model_dir)
        self.tokenizer=Tokenizer.from_file(str(model_dir/'tokenizer.json'))
        options=ort.SessionOptions();options.intra_op_num_threads=threads;options.inter_op_num_threads=1
        options.graph_optimization_level=ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session=ort.InferenceSession(str(model_dir/GRAPH),sess_options=options,providers=['CPUExecutionProvider'])
        self.input_names={x.name for x in self.session.get_inputs()}

    def predict(self,note,documents,batch_size=8):
        import numpy as np
        results=[]
        for start in range(0,len(documents),batch_size):
            pairs=[pack_pair(self.tokenizer,note,d) for d in documents[start:start+batch_size]]
            length=max(len(ids) for ids,_ in pairs);shape=(len(pairs),length)
            ids=np.full(shape,self.tokenizer.token_to_id('[PAD]'),dtype=np.int64)
            mask=np.zeros(shape,dtype=np.int64);types=np.zeros(shape,dtype=np.int64)
            for i,(tokens,segments) in enumerate(pairs):
                ids[i,:len(tokens)]=tokens;mask[i,:len(tokens)]=1;types[i,:len(tokens)]=segments
            inputs={'input_ids':ids,'attention_mask':mask,'token_type_ids':types}
            output=self.session.run(None,{k:v for k,v in inputs.items() if k in self.input_names})[0]
            if output.size!=len(pairs):raise ValueError('Expected one relevance logit per pair')
            values=np.asarray(output).reshape(-1)
            if not np.isfinite(values).all():raise ValueError('Nonfinite model output')
            results.extend(float(v) for v in values)
        return results
