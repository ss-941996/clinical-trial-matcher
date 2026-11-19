"""Package a configured lexical index, cached trial vectors and query encoder."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
from app.semantic import digest


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def build(spec_path, output=None):
    import numpy as np

    spec_path=Path(spec_path).resolve();spec=read(spec_path)
    def resolve(key):return (spec_path.parent/spec[key]).resolve()
    source=resolve('source_index');cache=resolve('vector_cache')
    protocol_path=resolve('protocol');query=resolve('query_model_dir')
    target=Path(output).resolve() if output else resolve('output_dir')
    if target.exists():raise RuntimeError('Output already exists; choose a new versioned output directory')
    protocol=read(protocol_path);complete=read(cache/'complete.json')
    if complete['protocol_sha256']!=digest(protocol_path):raise RuntimeError('Wrong vector cache protocol')
    if digest(source)!=spec['source_index_sha256']:raise RuntimeError('Source index checksum mismatch')
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as db:
        selected={r[0] for r in db.execute('SELECT trial_id FROM identities')}
        if db.execute('SELECT count(*) FROM identities').fetchone()[0]!=len(selected):
            raise RuntimeError('Duplicate lexical identities')
        if any(a!=b for a,b in db.execute('SELECT i.trial_id,t.trial_id FROM identities i LEFT JOIN trials t ON t.rowid=i.row_id')):
            raise RuntimeError('Lexical identities do not match trial documents')
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        metadata={k:json.loads(v) for k,v in db.execute('SELECT key,value FROM metadata')} if 'metadata' in tables else {}
    if not selected or complete['records']!=len(selected):raise RuntimeError('Cache/index record count mismatch')
    metadata.update(spec.get('collection',{}));metadata['records']=len(selected)
    if not all(metadata.get(k) for k in ['dataset_kind','snapshot','source']):
        raise RuntimeError('Collection requires dataset_kind, snapshot and source metadata')
    dimensions=None;seen=set();shards=[]
    for name,checksum in sorted(complete['shards'].items()):
        if Path(name).name!=name or digest(cache/name)!=checksum:raise RuntimeError('Corrupt vector shard')
        with np.load(cache/name,allow_pickle=False) as shard:
            ids=list(shard['ids']);vectors=shard['vectors']
            if vectors.ndim!=2 or vectors.shape[0]!=len(ids) or not np.isfinite(vectors).all():
                raise RuntimeError('Invalid trial vectors')
            if dimensions is None:dimensions=vectors.shape[1]
            if dimensions<1 or vectors.shape[1]!=dimensions or len(set(ids))!=len(ids) or seen.intersection(ids):
                raise RuntimeError('Inconsistent vectors or duplicate vector identities')
            seen.update(ids)
        shards.append(name)
    if seen!=selected:raise RuntimeError('Lexical and semantic trial identities differ')
    tokens=protocol.get('query_max_tokens',128)
    if type(tokens) is not int or not 2<=tokens<=512:raise RuntimeError('Invalid query token limit')
    query_files=['encoder-int8.onnx','tokenizer.json','README.md','LICENSE']
    for name in query_files:
        expected=(protocol['onnx_manifests']['query']['onnx_sha256'] if name.endswith('.onnx')
                  else protocol['model_manifests']['query']['files'][name])
        if isinstance(expected,dict):expected=expected['sha256']
        if digest(query/name)!=expected:raise RuntimeError('Query model differs from vector protocol')
    # Publish only after full validation and copying. Never overwrite a live bundle.
    target.parent.mkdir(parents=True,exist_ok=True)
    staging=Path(tempfile.mkdtemp(prefix='.'+target.name+'-',dir=target.parent)).resolve()
    try:
        index=staging/'bm25.sqlite'
        with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as src, closing(sqlite3.connect(index)) as db:
            src.backup(db)
            db.execute('CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT)')
            db.executemany('INSERT OR REPLACE INTO metadata VALUES (?,?)',[(k,json.dumps(v)) for k,v in metadata.items()]);db.commit()
        files={}
        for name in shards:
            dest=staging/'vectors'/name;dest.parent.mkdir(exist_ok=True)
            shutil.copyfile(cache/name,dest);files['vectors/'+name]=digest(dest)
            if files['vectors/'+name]!=complete['shards'][name]:raise RuntimeError('Vector copy checksum mismatch')
        for name in query_files:
            dest=staging/'query'/name;dest.parent.mkdir(exist_ok=True)
            shutil.copyfile(query/name,dest);files['query/'+name]=digest(dest)
            if digest(dest)!=digest(query/name):raise RuntimeError('Query copy checksum mismatch')
        manifest=dict(schema_version=1,records=len(selected),dimensions=dimensions,similarity='dot_product',query_max_tokens=tokens,
            query_model=protocol['model_manifests']['query']['model'],query_revision=protocol['onnx_manifests']['query']['source'],
            article_provenance=protocol['onnx_manifests']['article'],collection=metadata,
            protocol_sha256=complete['protocol_sha256'],source_index_sha256=spec['source_index_sha256'],
            build_spec_sha256=digest(spec_path),index_sha256=digest(index),files=files,shards=['vectors/'+name for name in shards])
        (staging/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
        if staging.parent!=target.parent.resolve():raise RuntimeError('Unsafe staging location')
        staging.rename(target)
    finally:
        if staging.exists():
            if staging.parent!=target.parent.resolve() or not staging.name.startswith('.'+target.name+'-'):
                raise RuntimeError('Unsafe staging cleanup location')
            shutil.rmtree(staging)
    print(f'Packaged {len(selected):,} existing trial vectors at {target}; no encoding performed',flush=True)
    return target


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec',type=Path,default=Path(__file__).parent/'bundles/pilot.json')
    parser.add_argument('--output',type=Path,help='Optional new output directory, relative to the working directory')
    args=parser.parse_args();build(args.spec,args.output)
