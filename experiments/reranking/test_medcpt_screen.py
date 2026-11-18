"""Guard the offline pilot's score coverage and deterministic ranking."""
import math
import pytest

from experiments.reranking.medcpt_screen import ordered
from demo.app.rerank import fuse


def test_scores_reorder_without_changing_candidate_pool():
    candidates = ['a', 'b', 'c']
    ranked = ordered(candidates, {'a': -2., 'b': 1., 'c': 1.})
    assert ranked == ['b', 'c', 'a']
    assert set(fuse(candidates, ranked, .5)) == set(candidates)


@pytest.mark.parametrize('candidates,scores', [
    (['a', 'a'], {'a': 1.}),
    (['a', 'b'], {'a': 1.}),
    (['a'], {'a': 1., 'b': 2.}),
    (['a'], {'a': math.nan}),
    (['a'], {'a': math.inf}),
])
def test_incomplete_or_invalid_model_output_is_rejected(candidates, scores):
    with pytest.raises(ValueError):
        ordered(candidates, scores)
