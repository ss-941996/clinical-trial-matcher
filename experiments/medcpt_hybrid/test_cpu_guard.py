from experiments.medcpt_hybrid.guard_cpu import Thresholds


def test_transient_cpu_spike_does_not_pause_but_sustained_load_does():
    guard=Thresholds()
    assert guard.update(90,False) is None
    assert guard.update(90,False) is None
    assert guard.update(74,False) is None
    assert guard.update(75,False) is None
    assert guard.update(75,False) is None
    assert guard.update(75,False)=='pause'


def test_resume_requires_two_consecutive_low_samples():
    guard=Thresholds()
    assert guard.update(59,True) is None
    assert guard.update(61,True) is None
    assert guard.update(60,True) is None
    assert guard.update(60,True)=='resume'
