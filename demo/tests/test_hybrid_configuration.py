"""Collection-independent packaging and runtime path selection."""
import json
from pathlib import Path
import sqlite3
import numpy as np
import pytest
from app.semantic import digest
from app.settings import load_settings
from build_hybrid_bundle import build


def save(path,value):path.write_text(json.dumps(value),encoding='utf-8')


@pytest.fixture
def collection(tmp_path):
    def make(count):
        source=tmp_path/'source.sqlite';cache=tmp_path/'cache';query=tmp_path/'query'
        cache.mkdir();query.mkdir()
        with sqlite3.connect(source) as db:
            db.execute('CREATE TABLE identities(trial_id TEXT PRIMARY KEY,row_id INTEGER UNIQUE)')
            db.execute("CREATE VIRTUAL TABLE trials USING fts5(trial_id UNINDEXED,title,conditions,summary,eligibility)")
            for i in range(count):
                trial=f'NCT{i:08}'
                row=db.execute('INSERT INTO trials VALUES(?,?,?,?,?)',(trial,'Study','diabetes','summary','criteria'))
                db.execute('INSERT INTO identities VALUES(?,?)',(trial,row.lastrowid))
        for name in ['encoder-int8.onnx','tokenizer.json','README.md','LICENSE']:(query/name).write_text('synthetic '+name)
        protocol=dict(query_max_tokens=64,model_manifests={'query':{'model':'synthetic-medcpt-format','files':{n:digest(query/n) for n in ['tokenizer.json','README.md','LICENSE']}}},
            onnx_manifests={'query':{'source':'test-revision','onnx_sha256':digest(query/'encoder-int8.onnx')},'article':{'source':'test-article'}})
        save(tmp_path/'protocol.json',protocol)
        np.savez(cache/'000000.npz',ids=np.asarray([f'NCT{i:08}' for i in range(count)]),vectors=np.ones((count,3),dtype=np.float32))
        save(cache/'complete.json',dict(records=count,protocol_sha256=digest(tmp_path/'protocol.json'),shards={'000000.npz':digest(cache/'000000.npz')}))
        spec=dict(source_index='source.sqlite',source_index_sha256=digest(source),vector_cache='cache',protocol='protocol.json',query_model_dir='query',output_dir='bundle',
            collection=dict(dataset_kind='historical-pilot',snapshot='synthetic-snapshot',source='Synthetic test collection'))
        path=tmp_path/'spec.json';save(path,spec)
        return path,cache
    return make


@pytest.mark.parametrize('count',[2,7])
def test_builder_derives_count_model_settings_and_metadata(collection,count):
    spec,_=collection(count);target=build(spec)
    manifest=json.loads((target/'manifest.json').read_text())
    assert manifest['records']==count and manifest['dimensions']==3 and manifest['query_max_tokens']==64
    assert manifest['query_model']=='synthetic-medcpt-format'
    with sqlite3.connect(target/'bm25.sqlite') as db:
        meta={k:json.loads(v) for k,v in db.execute('SELECT key,value FROM metadata')}
    assert meta['records']==count and meta['snapshot']=='synthetic-snapshot'
    before=digest(target/'manifest.json')
    with pytest.raises(RuntimeError,match='already exists'):build(spec)
    assert digest(target/'manifest.json')==before


def test_builder_refuses_same_count_but_different_trial_ids(collection):
    spec,cache=collection(2)
    np.savez(cache/'000000.npz',ids=np.asarray(['NCT99999998','NCT99999999']),vectors=np.ones((2,3),dtype=np.float32))
    complete=json.loads((cache/'complete.json').read_text());complete['shards']['000000.npz']=digest(cache/'000000.npz');save(cache/'complete.json',complete)
    with pytest.raises(RuntimeError,match='identities differ'):build(spec)
    assert not (spec.parent/'bundle').exists()


def test_builder_refuses_changed_source_index(collection):
    spec,_=collection(2)
    with sqlite3.connect(spec.parent/'source.sqlite') as db:db.execute("UPDATE trials SET title='Changed text'")
    with pytest.raises(RuntimeError,match='Source index checksum'):build(spec)
    assert not (spec.parent/'bundle').exists()


def test_failed_copy_does_not_publish_partial_bundle(collection,monkeypatch):
    spec,_=collection(2)
    def fail(*args):raise OSError('Simulated copy failure')
    monkeypatch.setattr('build_hybrid_bundle.shutil.copyfile',fail)
    with pytest.raises(OSError,match='copy failure'):build(spec)
    assert not (spec.parent/'bundle').exists() and not list(spec.parent.glob('.bundle-*'))


@pytest.fixture
def settings_env(monkeypatch):
    for name in ['TRIAL_CONFIG_PATH','TRIAL_PIPELINE','TRIAL_HYBRID_BUNDLE','TRIAL_INDEX_PATH']:monkeypatch.delenv(name,raising=False)


def test_custom_config_resolves_paths_relative_to_file_and_env_takes_priority(tmp_path,monkeypatch,settings_env):
    path=tmp_path/'settings.json';save(path,dict(hybrid_bundle='data/another-collection',semantic_rrf_weight=.5))
    monkeypatch.setenv('TRIAL_CONFIG_PATH',str(path))
    config,index,bundle=load_settings()
    assert config['semantic_rrf_weight']==.5
    assert Path(bundle)==tmp_path/'data/another-collection' and Path(index)==Path(bundle)/'bm25.sqlite'
    monkeypatch.setenv('TRIAL_HYBRID_BUNDLE',str(tmp_path/'override'))
    assert Path(load_settings()[2])==tmp_path/'override'


def test_weight_only_override_preserves_default_bundle_base(tmp_path,monkeypatch,settings_env):
    original=load_settings()[2]
    path=tmp_path/'weights.json';save(path,dict(semantic_rrf_weight=2.))
    monkeypatch.setenv('TRIAL_CONFIG_PATH',str(path));monkeypatch.chdir(tmp_path)
    assert load_settings()[2]==original


@pytest.mark.parametrize('value',[0,-1,float('nan'),True,None])
def test_settings_reject_invalid_fusion_weights(value,tmp_path,monkeypatch,settings_env):
    path=tmp_path/'settings.json';save(path,dict(semantic_rrf_weight=value))
    monkeypatch.setenv('TRIAL_CONFIG_PATH',str(path))
    with pytest.raises(ValueError,match='finite and positive'):load_settings()
