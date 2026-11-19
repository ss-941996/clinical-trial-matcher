import json
from pathlib import Path
import sqlite3
import sys
import numpy as np
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from src.registry_json import studies
from src.current_medcpt import Monitor,prepare,encode,save,digest,vector_store,exclusive


def make_source(path,count):
    rows=[dict(protocolSection=dict(identificationModule=dict(nctId=f'NCT{i:08}',briefTitle='Étude diabetes'),
        conditionsModule=dict(conditions=['Type 2 Diabetes']),descriptionModule=dict(briefSummary='Treatment research'),
        eligibilityModule=dict(eligibilityCriteria='Adults over 18'),statusModule=dict(overallStatus='RECRUITING'))) for i in range(count)]
    path.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8-sig');return rows


def test_streaming_json_boundaries_unicode_and_offsets(tmp_path):
    source=tmp_path/'studies.json';expected=make_source(source,3)
    actual=list(studies(source,chunk_size=7,with_offsets=True))
    assert [row for row,_ in actual]==expected
    assert [offset for _,offset in actual]==sorted(offset for _,offset in actual)
    assert actual[-1][1]<source.stat().st_size


@pytest.mark.parametrize('text',['[{"x":1},]','[{"x":1} {"x":2}]','[{"x":1}] garbage','[{"x":1}','{}'])
def test_bad_downloads_are_rejected(tmp_path,text):
    path=tmp_path/'bad.json';path.write_text(text)
    with pytest.raises(ValueError):list(studies(path,chunk_size=5))


def setup_run(tmp_path,count=6):
    source=tmp_path/'input.json';make_source(source,count)
    run=tmp_path/'run';run.mkdir()
    monitor=Monitor(run,1.)
    index,identity,profile=prepare(source,run,'synthetic-date',monitor)
    save(run/'protocol.json',{'source_bytes':source.stat().st_size})
    return source,run,index,monitor


def test_graceful_stop_saves_current_batch_and_resume_skips_saved_vectors(tmp_path,monkeypatch):
    source,run,index,monitor=setup_run(tmp_path)
    calls=[]
    class Fake:
        def __init__(self,*args):pass
        def encode(self,rows):
            calls.append([r[0] for r in rows])
            if len(calls)==1:(run/'stop.request').touch()
            return np.ones((len(rows),768),dtype=np.float32)
    monkeypatch.setattr('src.current_medcpt.ArticleEncoder',Fake)
    protocol=json.loads((run/'protocol.json').read_text())
    with pytest.raises(InterruptedError):encode(run,index,tmp_path,protocol,monitor)
    with sqlite3.connect(run/'vectors.sqlite') as db:assert db.execute('SELECT count(*) FROM vectors').fetchone()[0]==4
    (run/'stop.request').unlink()
    encode(run,index,tmp_path,protocol,Monitor(run,1.))
    assert [len(batch) for batch in calls]==[4,2]
    complete=json.loads((run/'vector-shards/complete.json').read_text())
    assert complete['records']==6
    with sqlite3.connect(run/'vectors.sqlite') as db:
        assert db.execute('SELECT sum(source_bytes) FROM vectors').fetchone()[0]==source.stat().st_size
    # A complete resume must require no encoder at all.
    encode(run,index,tmp_path,protocol,Monitor(run,1.))
    assert len(calls)==2


def test_resume_refuses_corrupt_cached_vector(tmp_path,monkeypatch):
    _,run,index,monitor=setup_run(tmp_path,2)
    class Fake:
        def __init__(self,*args):pass
        def encode(self,rows):return np.ones((len(rows),768),dtype=np.float32)
    monkeypatch.setattr('src.current_medcpt.ArticleEncoder',Fake)
    protocol=json.loads((run/'protocol.json').read_text())
    encode(run,index,tmp_path,protocol,monitor)
    with sqlite3.connect(run/'vectors.sqlite') as db:db.execute("UPDATE vectors SET vector=x'0000' WHERE trial_id='NCT00000000'")
    with pytest.raises(RuntimeError,match='Invalid saved vector'):encode(run,index,tmp_path,protocol,Monitor(run,1.))


def test_resume_refuses_different_protocol(tmp_path):
    path=tmp_path/'vectors.sqlite'
    vector_store(path,'original').close()
    with pytest.raises(RuntimeError,match='another protocol'):vector_store(path,'changed')


def test_import_resume_preserves_exact_record_and_byte_counts(tmp_path):
    source=tmp_path/'source.json';make_source(source,260)
    run=tmp_path/'run';run.mkdir();monitor=Monitor(run,1.);monitor.stop.set()
    with pytest.raises(InterruptedError):prepare(source,run,'synthetic',monitor)
    _,_,profile=prepare(source,run,'synthetic',Monitor(run,1.))
    assert profile['records']==260 and profile['recruiting']==260
    with sqlite3.connect(run/'catalog.sqlite') as db:assert db.execute('SELECT sum(source_bytes) FROM studies').fetchone()[0]==source.stat().st_size


def test_second_worker_cannot_lock_same_run(tmp_path):
    with exclusive(tmp_path/'worker.lock'):
        with pytest.raises((RuntimeError,BlockingIOError)):
            with exclusive(tmp_path/'worker.lock'):pass


def test_throttle_does_not_sleep_negative_when_deadline_passes(tmp_path,monkeypatch):
    monitor=Monitor(tmp_path,.5);clock=iter([0.,.9,1.1]);sleeps=[]
    monkeypatch.setattr('src.current_medcpt.time.process_time',lambda:1.)
    monkeypatch.setattr('src.current_medcpt.time.monotonic',lambda:next(clock))
    def sleep(delay):
        assert delay>=0;sleeps.append(delay)
    monkeypatch.setattr('src.current_medcpt.time.sleep',sleep)
    monitor.rest(0.)
    assert len(sleeps)==1
