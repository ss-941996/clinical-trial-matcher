import json
from src.current_data import normalize
import pytest


def test_missing_optional_fields_are_preserved_as_unknown():
    row=normalize({'protocolSection':{'identificationModule':{'nctId':'NCT00000001','briefTitle':'Example'}}})
    assert row[:3]==('NCT00000001','Example','[]')
    assert row[5:10]==('','UNKNOWN','','','UNKNOWN')
    assert row[-1]=='[]'


def test_source_status_dates_and_full_criteria_survive_normalization():
    row=normalize({'protocolSection':{
        'identificationModule':{'nctId':'NCT00000002','officialTitle':'Fallback title'},
        'statusModule':{'overallStatus':'COMPLETED','lastUpdatePostDateStruct':{'date':'2026-11-01'}},
        'eligibilityModule':{'eligibilityCriteria':'Exclusion: prior cancer','minimumAge':'18 Years','sex':'ALL'},
        'conditionsModule':{'conditions':['Type 2 Diabetes']},
        'contactsLocationsModule':{'locations':[{'country':'France'},{'country':'France'}]}}})
    assert row[1]=='Fallback title' and json.loads(row[2])==['Type 2 Diabetes']
    assert row[5]=='Exclusion: prior cancer' and row[6]=='COMPLETED'
    assert row[10]=='2026-11-01' and json.loads(row[-1])==['France']


def test_missing_identity_is_not_silently_skipped():
    with pytest.raises(ValueError):normalize({'protocolSection':{}})


def test_full_catalog_and_recruiting_index_have_separate_scope(tmp_path):
    import gzip
    import sqlite3
    from src.current_data import build,save,sha
    directory=tmp_path/'raw';directory.mkdir()
    studies=[]
    for n,status in [(1,'RECRUITING'),(2,'COMPLETED')]:
        studies.append({'protocolSection':{
            'identificationModule':{'nctId':f'NCT{n:08}','briefTitle':'Diabetes study'},
            'statusModule':{'overallStatus':status,'lastUpdatePostDateStruct':{'date':'2026-11-01'}},
            'conditionsModule':{'conditions':['Type 2 Diabetes']},
            'descriptionModule':{'briefSummary':'Brief original','detailedDescription':'Detailed original'},
            'eligibilityModule':{'eligibilityCriteria':'Exclude prior cancer'}}})
    page=directory/'page.json.gz';page.write_bytes(gzip.compress(json.dumps({'studies':studies}).encode()))
    state=dict(complete=True,release='fixture',version={'dataTimestamp':'2026-11-17T10:00:05','apiVersion':'2.0.5'},expected_records=2,
        pages=[dict(file=page.name,sha256=sha(page),records=2,next_token=None)])
    save(directory/'download.json',state)
    build(tmp_path,directory,state)
    manifest=json.loads((tmp_path/'data/manifests/ctgov-current.json').read_text())
    assert manifest['records']==1 and manifest['registry_records']==2
    with sqlite3.connect(tmp_path/manifest['catalog']) as db:
        assert db.execute('SELECT count(*) FROM studies').fetchone()[0]==2
        assert db.execute("SELECT status FROM studies WHERE trial_id='NCT00000002'").fetchone()[0]=='COMPLETED'
    with sqlite3.connect(tmp_path/manifest['index']) as db:
        assert db.execute('SELECT trial_id FROM identities').fetchall()==[('NCT00000001',)]
        assert db.execute('SELECT status FROM trial_metadata').fetchone()[0]=='RECRUITING'
        assert db.execute('SELECT summary,eligibility FROM trials').fetchone()==('Brief original\nDetailed original','Exclude prior cancer')
        assert db.execute('SELECT phrase FROM condition_vocabulary').fetchone()[0]=='type 2 diabetes'
    original=sha(tmp_path/manifest['index'])
    build(tmp_path,directory,state)
    assert sha(tmp_path/manifest['index'])==original



def test_paginated_api_need_not_repeat_total_count(tmp_path,monkeypatch):
    from src import current_data as module
    version={'dataTimestamp':'2026-11-17T10:00:05','apiVersion':'2.0.5'}
    calls=[]
    def fake_request(endpoint,params=None):
        calls.append((endpoint,params))
        if endpoint=='version':return version
        if not params.get('pageToken'):return {'totalCount':2,'studies':[{'id':1}],'nextPageToken':'second'}
        assert params['pageToken']=='second'
        return {'studies':[{'id':2}]}
    monkeypatch.setattr(module,'request',fake_request)
    monkeypatch.setattr(module.time,'sleep',lambda _:None)
    directory,state=module.fetch(tmp_path)
    assert state['complete'] and len(state['pages'])==2
    assert (tmp_path/'data/manifests/ctgov-download.json').exists()
    calls.clear()
    module.fetch(tmp_path)
    assert calls==[('version',None)]


def test_changed_source_release_is_not_published(tmp_path,monkeypatch):
    from src import current_data as module
    versions=iter([{'dataTimestamp':'2026-11-17T10:00:05','apiVersion':'2.0.5'},
                   {'dataTimestamp':'2026-11-18T10:00:05','apiVersion':'2.0.5'}])
    monkeypatch.setattr(module,'request',lambda endpoint,params=None:next(versions) if endpoint=='version' else {'totalCount':1,'studies':[{'id':1}]})
    monkeypatch.setattr(module.time,'sleep',lambda _:None)
    with pytest.raises(ValueError,match='Source release changed'):module.fetch(tmp_path)
    assert not (tmp_path/'data/manifests/ctgov-download.json').exists()
