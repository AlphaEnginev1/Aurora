from aurora.autonomy.policy import Policy

def test_policy_default_behavior():
    p = Policy(frozenset({"tool:git"}), frozenset({"ubuntu-apt"}))
    assert p.allows_capability("tool:git")
    assert not p.allows_capability("tool:unknown")
    assert not p.allows_source("unknown")
