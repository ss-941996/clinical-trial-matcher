import numpy as np
import pytest
from experiments.medcpt_hybrid.run import exact_top, evaluate_rankings
from demo.app.retrieval import rank_fusion
from experiments.medcpt_hybrid.pilot import sample_ids


def test_sample_is_reproducible_and_independent_of_input_order():
    ids=[f'NCT{i:08d}' for i in range(100)]
    selected=sample_ids(ids,20)
    assert selected==sample_ids(list(reversed(ids)),20)
    assert len(selected)==len(set(selected))==20


def test_sharded_exact_search_matches_global_search_with_ties():
    ids = ['z','b','a','x','c','d']
    scores = np.array([0.,3.,3.,1.,3.,2.],dtype=np.float32)
    global_hits = exact_top(scores,ids,3)
    partial = exact_top(scores[:3],ids[:3],3)+exact_top(scores[3:],ids[3:],3)
    merged = sorted(partial,key=lambda x:(-x[1],x[0]))[:3]
    assert merged == global_hits == [('a',3.),('b',3.),('c',3.)]
    with pytest.raises(ValueError):
        exact_top(np.array([float('nan')]),['a'])


def test_vector_only_candidate_can_enter_hybrid_pool():
    lexical = [('lexical',4.),('shared',1.)]
    dense = [('semantic',99.),('shared',98.)]
    merged = rank_fusion([lexical,dense],3,[1.,1.])
    assert [d for d,_ in merged] == ['shared','lexical','semantic']
    # RRF depends on positions, independent of native score scales.
    assert merged == rank_fusion([lexical,[(d,s*1000) for d,s in dense]],3,[1.,1.])


def test_reranking_changes_top_ten_but_preserves_recall_200():
    qrels = {'p': {'eligible':2,'excluded':1}}
    candidates = [(f'x{i}',100-i) for i in range(11)]+[('eligible',0)]
    reordered = [('eligible',100)]+candidates[:-1]
    before = evaluate_rankings({'p':candidates},qrels)['macro']
    after = evaluate_rankings({'p':reordered},qrels)['macro']
    assert before['eligible_recall@200'] == after['eligible_recall@200'] == 1.
    assert after['eligible_recall@10'] > before['eligible_recall@10']
    assert after['ndcg@10'] > before['ndcg@10']
