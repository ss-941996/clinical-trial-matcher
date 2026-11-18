import json
from pathlib import Path
import sqlite3
from threading import Event,Thread
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.retrieval import Engine

@pytest.fixture
def engine(tmp_path):
    path=tmp_path/'trials.sqlite'
    with sqlite3.connect(path) as db:
        db.execute("CREATE VIRTUAL TABLE trials USING fts5(trial_id UNINDEXED,title,conditions,summary,eligibility,tokenize='porter unicode61')")
        db.execute('CREATE TABLE identities(trial_id TEXT PRIMARY KEY,row_id INTEGER UNIQUE)')
        db.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT)')
        db.execute('INSERT INTO metadata VALUES (?,?)',('records',json.dumps(2)))
        for trial,title,condition in [('NCT00000001','A diabetes study','diabetes'),('NCT00000002','A stroke study','stroke')]:
            row=db.execute('INSERT INTO trials VALUES (?,?,?,?,?)',(trial,title,condition,'Treatment research','Adults over 18 years'))
            db.execute('INSERT INTO identities VALUES (?,?)',(trial,row.lastrowid))
    return Engine(path,expected_records=2)

def test_search_contract_readonly_index_and_unassessed_eligibility(engine):
    with TestClient(create_app(engine)) as client:
        assert client.get('/health/ready').status_code==200
        assert client.get('/').status_code==200
        assert client.get('/assets/app.js').status_code==200
        response=client.post('/match',json={'note':'A patient with diabetes taking metformin.'})
        assert response.status_code==200
        data=response.json();assert data['candidate_count']==1
        trial=data['results'][0];assert trial['trial_id']=='NCT00000001'
        assert trial['eligibility_status']=='not_assessed' and trial['rank']==1
        assert 'note' not in data and trial['eligibility_text']=='Adults over 18 years'
        assert response.headers['Cache-Control']=='no-store'
        assert client.get('/assets/unknown.js').status_code==404
    assert not engine.ready
    with engine.connect() as db:
        with pytest.raises(sqlite3.OperationalError):db.execute('DELETE FROM identities')

def test_rejects_invalid_inputs_and_unavailable_model(engine):
    with TestClient(create_app(engine)) as client:
        for body in [{'note':'short'},{'note':' '*30},{'note':'1'*30},{'note':'x'*12001},{'note':'A patient with diabetes history.','top_k':11},{'note':'A patient with diabetes history.','method':'unknown'},{'note':'A patient with diabetes history.','other':1}]:
            assert client.post('/match',json=body).status_code==422
        assert client.post('/match',json={'note':'A patient with diabetes history.','method':'rerank'}).status_code==422
        assert client.post('/match',content='x'*70000).status_code==413

def test_auth_protects_ui_api_and_schema_but_not_health(engine,monkeypatch):
    monkeypatch.setenv('DEMO_USERNAME','research');monkeypatch.setenv('DEMO_PASSWORD','test-password')
    monkeypatch.setenv('REQUIRE_AUTH','true')
    with TestClient(create_app(engine)) as client:
        for path in ['/','/config','/openapi.json','/assets/app.js']:assert client.get(path).status_code==401
        assert client.post('/match',json={'note':'A patient with diabetes history.'}).status_code==401
        assert client.get('/config',auth=('research','wrong')).status_code==401
        assert client.get('/config',auth=('research','test-password')).status_code==200
        assert client.get('/health/live').status_code==200
    monkeypatch.delenv('DEMO_PASSWORD')
    with pytest.raises(RuntimeError):create_app(engine)

def test_overlapping_search_gets_retry_response(engine):
    entered=Event();release=Event();original=engine.search;responses=[]
    def slow(*args,**kwargs):
        entered.set();assert release.wait(10);return original(*args,**kwargs)
    engine.search=slow
    with TestClient(create_app(engine)) as client:
        thread=Thread(target=lambda:responses.append(client.post('/match',json={'note':'A patient with diabetes history.'})))
        thread.start()
        try:
            assert entered.wait(5)
            busy=client.post('/match',json={'note':'A patient with diabetes history.'})
            assert busy.status_code==429 and busy.headers['Retry-After']=='5'
        finally:release.set();thread.join(timeout=10)
        assert not thread.is_alive() and responses[0].status_code==200


def test_configured_pipeline_is_used_when_browser_omits_method(engine):
    engine.config.update(default_method='fusion',fusion_balance_weight=2.0)
    with TestClient(create_app(engine)) as client:
        note='A research patient has diabetes and prior stroke.'
        automatic=client.post('/match',json={'note':note}).json()
        explicit=client.post('/match',json={'note':note,'method':'fusion'}).json()
        assert automatic['method']==explicit['method']=='fusion'
        assert [x['trial_id'] for x in automatic['results']]==[x['trial_id'] for x in explicit['results']]
        assert client.get('/config').json()['default_method']=='fusion'
        assert client.post('/match',json={'note':note,'method':'bm25'}).json()['method']=='bm25'
        assert '<select' not in client.get('/').text


def test_global_search_budget_rejects_excess_requests(engine,monkeypatch):
    monkeypatch.setenv('MAX_SEARCHES_PER_MINUTE','1')
    with TestClient(create_app(engine)) as client:
        note={'note':'A patient with diabetes history.'}
        assert client.post('/match',json=note).status_code==200
        response=client.post('/match',json=note)
        assert response.status_code==429 and int(response.headers['Retry-After'])>0
        assert client.get('/health/ready').status_code==200


def test_current_index_uses_its_own_vocabulary_and_reports_freshness(engine):
    with sqlite3.connect(engine.index_path) as db:
        metadata=dict(schema_version=2,dataset_kind='current-recruiting',serving_statuses=['RECRUITING'],snapshot='2026-11-17',data_timestamp='2026-11-17T10:00:05',source='ClinicalTrials.gov API v2')
        db.executemany('INSERT INTO metadata VALUES (?,?)',[(k,json.dumps(v)) for k,v in metadata.items()])
        db.execute('CREATE TABLE trial_metadata(trial_id TEXT PRIMARY KEY,status TEXT,last_updated TEXT,minimum_age TEXT,maximum_age TEXT,sex TEXT,countries TEXT)')
        db.executemany('INSERT INTO trial_metadata VALUES (?,?,?,?,?,?,?)',[(f'NCT{i:08}','RECRUITING','2026-11-01','18 Years','','ALL','["France"]') for i in [1,2]])
        db.execute('CREATE TABLE condition_vocabulary(phrase TEXT PRIMARY KEY,df INTEGER)')
        db.execute("INSERT INTO condition_vocabulary VALUES ('diabetes',1)")
    with TestClient(create_app(engine)) as client:
        assert engine.vocab=={'diabetes':1}
        data=client.get('/config').json()
        assert data['snapshot']=='2026-11-17' and not data['historical']
        result=client.post('/match',json={'note':'A patient with diabetes history.'}).json()
        assert result['results'][0]['status']=='RECRUITING'
        assert result['results'][0]['countries']==['France']
        assert result['results'][0]['last_updated']=='2026-11-01'
    with sqlite3.connect(engine.index_path) as db:db.execute("UPDATE trial_metadata SET status='COMPLETED' WHERE trial_id='NCT00000001'")
    with pytest.raises(RuntimeError,match='Recruiting record count mismatch'):engine.load()
