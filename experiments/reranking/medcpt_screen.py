"""Sequential 2021-only MedCPT pilot on frozen balanced-fusion candidates."""
from __future__ import annotations
import argparse
import ctypes
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import time
import urllib.request

MODEL='ncbi/MedCPT-Cross-Encoder'
REVISION='71caf65d4927987813984f54c284405a13fcca49'
SETTINGS=dict(batch_size=2,threads=1,note_tokens=128,
    field_tokens=dict(title=64,conditions=48,summary=112,eligibility=157),
    max_length=512,pause_seconds=.05,precision='float32',blend_weight=.5)


def digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def fingerprint(value):return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8');temp.replace(path)


def low_priority():
    for name in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS']:
        os.environ[name]='1'
    os.environ['TOKENIZERS_PARALLELISM']='false'
    if os.name=='nt':
        kernel=ctypes.windll.kernel32
        kernel.GetCurrentProcess.restype=ctypes.c_void_p
        kernel.SetPriorityClass.argtypes=[ctypes.c_void_p,ctypes.c_uint32]
        if not kernel.SetPriorityClass(kernel.GetCurrentProcess(),0x4000):raise RuntimeError('Cannot lower priority')
    elif hasattr(os,'nice'):os.nice(max(0,10-os.nice(0)))


def download(directory):
    from huggingface_hub import hf_hub_download
    files=['README.md','LICENSE','config.json','pytorch_model.bin','tokenizer.json',
        'tokenizer_config.json','special_tokens_map.json','vocab.txt','added_tokens.json']
    hashes={}
    for name in files:
        path=hf_hub_download(MODEL,name,revision=REVISION,local_dir=str(directory))
        hashes[name]=digest(path)
    card=(directory/'README.md').read_text(encoding='utf-8')
    if 'license_name: public-domain' not in card:raise RuntimeError('Review model license')
    url='https://raw.githubusercontent.com/ncbi/MedCPT/main/LICENSE'
    with urllib.request.urlopen(url,timeout=60) as response:notice=response.read()
    if b'PUBLIC DOMAIN NOTICE' not in notice:raise RuntimeError('Review NCBI notice')
    (directory/'NCBI-PUBLIC-DOMAIN.txt').write_bytes(notice)
    hashes['NCBI-PUBLIC-DOMAIN.txt']=digest(directory/'NCBI-PUBLIC-DOMAIN.txt')
    save(directory/'manifest.json',dict(model=MODEL,revision=REVISION,license='public-domain',
        license_metadata='HF license=other; license_name=public-domain; original LICENSE may be empty.',notice_source=url,files=hashes))
    print('Pinned model downloaded; no inference run',flush=True)


def ordered(ids,scores):
    import math
    if len(ids)!=len(set(ids)) or set(ids)!=set(scores) or not all(math.isfinite(v) for v in scores.values()):
        raise ValueError('Invalid complete candidate scores')
    return sorted(ids,key=lambda d:(-scores[d],d))


class Scorer:
    def __init__(self,directory):
        import torch
        from transformers import AutoModelForSequenceClassification
        from tokenizers import Tokenizer
        torch.set_num_threads(1);torch.set_num_interop_threads(1)
        self.torch=torch;self.tokenizer=Tokenizer.from_file(str(directory/'tokenizer.json'))
        self.model=AutoModelForSequenceClassification.from_pretrained(str(directory),
            local_files_only=True,use_safetensors=False,attn_implementation='eager').eval()

    def predict(self,note,documents):
        from demo.app.rerank import pack_pair
        torch=self.torch;output=[]
        with torch.inference_mode():
            for start in range(0,len(documents),SETTINGS['batch_size']):
                pairs=[pack_pair(self.tokenizer,note,d) for d in documents[start:start+SETTINGS['batch_size']]]
                length=max(len(ids) for ids,_ in pairs)
                ids=torch.full((len(pairs),length),self.tokenizer.token_to_id('[PAD]'),dtype=torch.long)
                mask=torch.zeros_like(ids);types=torch.zeros_like(ids)
                for i,(tokens,segments) in enumerate(pairs):
                    ids[i,:len(tokens)]=torch.tensor(tokens);mask[i,:len(tokens)]=1;types[i,:len(tokens)]=torch.tensor(segments)
                logits=self.model(input_ids=ids,attention_mask=mask,token_type_ids=types).logits.reshape(-1)
                if logits.numel()!=len(pairs) or not torch.isfinite(logits).all():raise RuntimeError('Invalid model scores')
                output.extend(logits.tolist());time.sleep(SETTINGS['pause_seconds'])
        return output

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[2])
    parser.add_argument('--download',action='store_true')
    parser.add_argument('--topics',nargs='+',default=['1'])
    args=parser.parse_args();root=args.root.resolve();directory=root/'models/medcpt'
    low_priority()
    if args.download:
        directory.mkdir(parents=True,exist_ok=True);download(directory);return
    os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
    from src.clean import load_topics,load_qrels
    from src.evaluate import metrics,check_reference
    from demo.app.retrieval import rank_fusion,Engine
    from demo.app.rerank import trial_document,fuse,FIELD_TOKENS,QUERY_TOKENS
    assert FIELD_TOKENS==SETTINGS['field_tokens'] and QUERY_TOKENS==SETTINGS['note_tokens']
    source=root/'experiments/retrieval_inputs/results'
    split=json.loads((source/'protocol.json').read_text())
    if len(args.topics)!=len(set(args.topics)) or any(t not in split['development_topics'] for t in args.topics):
        raise ValueError('Unique 2021 development topics only')
    manifest=json.loads((directory/'manifest.json').read_text())
    assert manifest['model']==MODEL and manifest['revision']==REVISION
    for name,value in manifest['files'].items():
        if digest(directory/name)!=value:raise RuntimeError('Model checksum mismatch: '+name)
    raw=root/'data/raw/trec2021';notes=load_topics(raw/'topics2021.xml');qrels=load_qrels(raw/'qrels2021.txt')
    protocol=dict(model=MODEL,revision=REVISION,settings=SETTINGS,model_files=manifest['files'],
        code_sha256={p.name:digest(p) for p in [Path(__file__),root/'demo/app/rerank.py',root/'src/evaluate.py']},
        inputs={p.name:digest(p) for p in [raw/'topics2021.xml',raw/'qrels2021.txt']},
        packages={name:importlib.metadata.version(name) for name in ['torch','transformers','tokenizers','numpy','pytrec-eval-terrier']},
        scope='Reused 2021 development screen only; fixed balanced-fusion 200; no 2022; no frontend change.')
    signature=fingerprint(protocol);output=root/'reports/medcpt';cache=root/'data/cache/medcpt'
    save(output/'protocol.json',protocol)
    frozen=json.loads((root/'reports/reranking/balanced_reranker/protocol.json').read_text())
    engine=Engine(root/'data/indexes/bm25.sqlite');engine.load()
    scorer=None;rows={}
    for topic in args.topics:
        baseline=json.loads((source/f'cache/baseline/{topic}.json').read_text())
        lexical=json.loads((source/f'cache/lexical_rrf/{topic}.json').read_text())
        ids=[d for d,_ in rank_fusion([baseline,lexical],200,[1.,2.])]
        assert len(ids)==200 and hashlib.sha256('\n'.join(ids).encode()).hexdigest()==frozen['candidate_hashes'][topic]
        with engine.connect() as db:documents={d:trial_document(db,d) for d in ids}
        key=fingerprint([signature,notes[topic],ids,documents]);path=cache/f'{topic}-{key}.json'
        checkpoint=json.loads(path.read_text()) if path.exists() else dict(scores={},scoring_seconds=0.,cpu_seconds=0.,initialization_seconds=0.)
        scores=checkpoint['scores'];todo=[d for d in sorted(ids) if d not in scores]
        if todo and scorer is None:
            tick=time.perf_counter();print('Loading MedCPT on CPU; one inference thread',flush=True)
            scorer=Scorer(directory)
            checkpoint['initialization_seconds']+=time.perf_counter()-tick
        for start in range(0,len(todo),SETTINGS['batch_size']):
            batch=todo[start:start+SETTINGS['batch_size']]
            tick=time.perf_counter();cpu=time.process_time()
            scores.update(zip(batch,scorer.predict(notes[topic],[documents[d] for d in batch]),strict=True))
            checkpoint['scoring_seconds']+=time.perf_counter()-tick
            checkpoint['cpu_seconds']+=time.process_time()-cpu
            save(path,checkpoint)
            if len(scores)%20==0:print(f'Topic {topic}: {len(scores)}/200 cached scores',flush=True)
        reranked=ordered(ids,scores);blended=fuse(ids,reranked,SETTINGS['blend_weight'])
        rankings=dict(balanced_fusion=ids,medcpt=reranked,medcpt_blend_05=blended);measured={}
        for name,ranking in rankings.items():
            assert set(ranking)==set(ids)
            measured[name]=metrics(ranking,qrels[topic]);check_reference(ranking,qrels[topic],measured[name])
        row=dict(topic=topic,signature=signature,metrics=measured,rankings=rankings,scores=scores,
            newly_scored_pairs=len(todo),scoring_seconds=checkpoint['scoring_seconds'],cpu_seconds=checkpoint['cpu_seconds'],initialization_seconds=checkpoint['initialization_seconds'],
            note='Cumulative batch scoring time includes pauses; excludes retrieval, initialization, checkpoint writes and metrics. Cached reruns preserve original scoring time.')
        save(output/f'topic-{topic}.json',row);rows[topic]=row
        print(json.dumps({k:row[k] for k in ['topic','metrics','newly_scored_pairs','scoring_seconds']}),flush=True)
    macro={name:{metric:sum(row['metrics'][name][metric] for row in rows.values())/len(rows)
        for metric in ['ndcg@10','eligible_recall@10','eligible_recall@200','eligible@10']} for name in rankings}
    save(output/'summary.json',dict(signature=signature,topics=args.topics,macro=macro,per_topic=rows,
        verification='Every metric independently checked against pytrec_eval. All three arms preserve the same candidate sets.'))
    print(json.dumps(macro,indent=2),flush=True)


if __name__=='__main__':main()
