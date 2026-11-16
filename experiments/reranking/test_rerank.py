import pytest
from rerank import ordered_candidates,fuse,pack_pair

def test_reranking_preserves_candidates_and_breaks_ties_by_id():
    assert ordered_candidates(['C','B','A'],{'A':2.,'B':2.,'C':1.})==['A','B','C']
    with pytest.raises(ValueError):ordered_candidates(['A','A'],{'A':1.})
    with pytest.raises(ValueError):ordered_candidates(['A'],{'A':float('nan')})

def test_rank_blend_matches_hand_calculation():
    assert fuse(['B','A'],['A','B'],1)==['A','B']
    assert fuse(['B','A'],['A','B'],.5)==['B','A']
    with pytest.raises(ValueError):fuse(['A'],['B'],1.)

def test_field_budgets_keep_query_and_eligibility_within_512():
    class Encoding:
        def __init__(self,ids):self.ids=ids
    class Tokenizer:
        def encode(self,text,**kwargs):return Encoding([5]*1000)
        def token_to_id(self,s):return {'[CLS]':101,'[SEP]':102}[s]
    ids,types=pack_pair(Tokenizer(),'long note',dict(title='a',conditions='b',summary='c',eligibility='d'))
    assert len(ids)==len(types)==512
    assert ids[0]==101 and ids[129]==102 and ids[-1]==102
    assert types[:130]==[0]*130 and types[130:]==[1]*382
