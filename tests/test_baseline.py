import math
import sqlite3
import pytest
from src.clean import parse_trial,sections,normalize,load_qrels,load_topics
from src.evaluate import metrics,check_reference
from src.retrieve import query_terms,search,build_index,connect,metadata
import pyarrow as pa
import pyarrow.parquet as pq


def xml(criteria='Inclusion Criteria:&#10;Age &gt; 18&#10;Exclusion Criteria:&#10;Prior stroke'):
    return ('<clinical_study><id_info><nct_id>NCT00000001</nct_id></id_info>'
            '<brief_title>Brief title</brief_title><official_title>Official title</official_title>'
            '<condition>Diabetes</condition><condition>Hypertension</condition><eligibility>'
            f'<criteria><textblock>{criteria}</textblock></criteria><minimum_age>18 Years</minimum_age>'
            '<maximum_age>N/A</maximum_age><gender>All</gender></eligibility></clinical_study>').encode()

def test_trial_sections_provenance_and_numeric_comparators():
    record=parse_trial(xml(),'part.zip','record.xml')
    assert record['title']=='Official title'
    assert record['conditions']==['Diabetes','Hypertension']
    assert record['inclusion']=='Age > 18'
    assert record['exclusion']=='Prior stroke'
    assert record['minimum_age']=='18 Years'
    assert record['maximum_age']=='N/A'
    assert record['source_member']=='record.xml'
    assert record['raw_xml']==xml().decode()
    assert normalize('HbA1c < 7 and eGFR > 30')=='HbA1c < 7 and eGFR > 30'

def test_unrecognized_criteria_remain_available():
    record=parse_trial(xml('No prior stroke; eGFR &gt; 30'))
    assert record['inclusion']==record['exclusion']==''
    assert record['eligibility']=='No prior stroke; eGFR > 30'

def test_repeated_headings_are_preserved():
    assert sections('Inclusion Criteria:\nA\nExclusion Criteria:\nB\nInclusion Criteria:\nC')=={'inclusion':'A\nC','exclusion':'B'}

def test_missing_fields_and_invalid_id():
    record=parse_trial(b'<clinical_study><id_info><nct_id>NCT00000002</nct_id></id_info></clinical_study>')
    assert record['sex']=='' and record['conditions']==[]
    with pytest.raises(ValueError): parse_trial(b'<clinical_study/>')

def test_hand_worked_graded_ndcg_and_eligible_recall():
    judgments={'eligible':2,'excluded':1,'irrelevant':0,'missed':2}
    row=metrics(['excluded','unjudged','eligible'],judgments)
    expected=(1+2/math.log2(4))/(2+2/math.log2(3)+1/math.log2(4))
    assert row['ndcg@10']==pytest.approx(expected)
    assert row['eligible_recall@10']==0.5
    assert row['inversion_rate@10']==1
    assert row['judged@10']==pytest.approx(2/3)
    check_reference(['excluded','unjudged','eligible'],judgments,row)

def test_metric_denominators_empty_runs_and_duplicates():
    row=metrics([],{'x':0})
    assert row['ndcg@10']==0 and row['eligible_recall@200'] is None
    assert row['inversion_rate@10'] is None
    assert metrics([],{'x':2})['eligible_recall@10']==0
    with pytest.raises(ValueError): metrics(['x','x'],{'x':2})

def test_metrics_cutoffs_do_not_count_rank11_as_top10():
    ranking=[f'd{i}' for i in range(11)]
    row=metrics(ranking,{'d10':2,'d0':1})
    assert row['eligible_recall@10']==0
    assert row['eligible_recall@30']==1
    check_reference(ranking,{'d10':2,'d0':1},row)

def test_query_syntax_is_not_executed():
    assert query_terms('the AND OR diabetes; DROP TABLE x')==['diabetes','drop','table']
    with sqlite3.connect(':memory:') as db:
        db.execute("CREATE VIRTUAL TABLE trials USING fts5(trial_id UNINDEXED,title,conditions,summary,eligibility,tokenize='porter unicode61')")
        db.execute('INSERT INTO trials VALUES (?,?,?,?,?)',('NCT00000001','Diabetes','diabetes','',''))
        db.execute('INSERT INTO trials VALUES (?,?,?,?,?)',('NCT00000002','Stroke','stroke','',''))
        assert search(db,'Diabetes " OR 1=1 --')[0]['trial_id']=='NCT00000001'
        assert search(db,'and the')==[]

def test_real_index_roundtrip(tmp_path):
    rows=[dict(trial_id='NCT00000001',title='Diabetes treatment',conditions=['diabetes'],summary='metformin',detailed_description='',eligibility='age above 18'),
          dict(trial_id='NCT00000002',title='Stroke treatment',conditions=['stroke'],summary='',detailed_description='',eligibility='adult')]
    parquet=tmp_path/'trials.parquet'; index=tmp_path/'index.sqlite'
    pq.write_table(pa.Table.from_pylist(rows),parquet)
    build_index(parquet,index)
    with connect(index) as db:
        assert metadata(db)['records']==2
        assert search(db,'diabetes')[0]['trial_id']=='NCT00000001'

def test_duplicate_topics_and_judgments_rejected(tmp_path):
    p=tmp_path/'qrels.txt'; p.write_text('1 0 NCT00000001 2\n1 0 NCT00000001 1\n')
    with pytest.raises(ValueError): load_qrels(p)
    p=tmp_path/'topics.xml'; p.write_text('<topics><topic number="1">A</topic><topic number="1">B</topic></topics>')
    with pytest.raises(ValueError): load_topics(p)


def test_downloader_accepts_decompressed_response_and_reuses_verified_cache(tmp_path,monkeypatch):
    from src import download as dl
    body=b'1 0 NCT00000001 2\n'
    class Response:
        headers={'Content-Length':'5','Content-Encoding':'gzip'}
        url='https://example.test/qrels.txt'
        def __enter__(self): return self
        def __exit__(self,*args): return False
        def raise_for_status(self): pass
        def iter_content(self,*args): yield body
    monkeypatch.setattr(dl,'RAW',tmp_path)
    monkeypatch.setattr(dl,'ROOT',tmp_path)
    monkeypatch.setattr(dl.requests,'get',lambda *a,**k:Response())
    first=dl.download(('qrels.txt','https://example.test/qrels.txt'))
    assert (tmp_path/'qrels.txt').read_bytes()==body
    assert first['bytes']==len(body)
    monkeypatch.setattr(dl.requests,'get',lambda *a,**k:pytest.fail('Cache should avoid network'))
    assert dl.download(('qrels.txt','https://example.test/qrels.txt'))['sha256']==first['sha256']
    (tmp_path/'qrels.txt').write_bytes(b'corrupted')
    with pytest.raises(ValueError,match='Hash mismatch'): dl.download(('qrels.txt','https://example.test/qrels.txt'))

def test_invalid_download_is_not_published(tmp_path,monkeypatch):
    from src import download as dl
    class Response:
        headers={}
        url='https://example.test/qrels.txt'
        def __enter__(self): return self
        def __exit__(self,*args): return False
        def raise_for_status(self): pass
        def iter_content(self,*args): yield b'<html>error</html>'
    monkeypatch.setattr(dl,'RAW',tmp_path)
    monkeypatch.setattr(dl,'ROOT',tmp_path)
    monkeypatch.setattr(dl.requests,'get',lambda *a,**k:Response())
    monkeypatch.setattr(dl.time,'sleep',lambda *a:None)
    with pytest.raises(ValueError): dl.download(('qrels.txt','https://example.test/qrels.txt'))
    assert not (tmp_path/'qrels.txt').exists()


def test_unicode_dotted_i_heading_preserves_content():
    assert sections('\u0130nclusion Criteria:\nA\nExclusion Criteria:\nB')=={'inclusion':'A','exclusion':'B'}


def test_native_rank_topk_matches_exhaustive_bm25_including_boundary_ties():
    import random
    rows=[]
    for i in range(50):
        # Multiple equal-length/equal-score records deliberately cross the cutoff.
        rows.append((f'NCT{i:08d}','Trial title','diabetes' if i%2 else 'stroke',('diabetes stroke '*(1+i%3)).strip(),'age above 18'))
    random.Random(17).shuffle(rows)
    with sqlite3.connect(':memory:') as db:
        db.execute("CREATE VIRTUAL TABLE trials USING fts5(trial_id UNINDEXED,title,conditions,summary,eligibility,tokenize='porter unicode61')")
        db.executemany('INSERT INTO trials VALUES (?,?,?,?,?)',rows)
        for note in ['diabetes','diabetes stroke','age 18']:
            query=' OR '.join('"'+t+'"' for t in query_terms(note))
            for k in [1,3,10,49,200]:
                expected=db.execute('SELECT trial_id,title,bm25(trials) AS score FROM trials WHERE trials MATCH ? ORDER BY score,trial_id LIMIT ?',(query,k)).fetchall()
                actual=search(db,note,k)
                assert [(r['trial_id'],r['title'],r['score']) for r in actual]==[(r[0],r[1],-r[2]) for r in expected]
