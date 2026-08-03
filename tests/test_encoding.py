import os

import pytest

import claude_threads as ct


def test_encode_both_schemes():
    p = "C:\\Users\\u\\Projects\\_Tools\\x"
    assert ct.encode(p, ct.SCHEME_CURRENT) == "C--Users-u-Projects--Tools-x"
    assert ct.encode(p, ct.SCHEME_LEGACY) == "C--Users-u-Projects-_Tools-x"


def _mk(projects_root, name):
    os.makedirs(os.path.join(projects_root, name), exist_ok=True)


def test_evidence_counts_only_disagreements(mkenv, tmp_path):
    env = mkenv(tmp_path)
    _mk(env.projects_root, "C--u-proj--Tools-a")            # current form of _Tools\a
    cwds = ["C:\\u\\proj\\_Tools\\a", "C:\\u\\proj\\plain"]  # 'plain' agrees under both
    cur, leg = ct.scheme_evidence(cwds, env.projects_root)
    assert (cur, leg) == (1, 0)


def test_choose_scheme_clear_winner(mkenv, tmp_path):
    env = mkenv(tmp_path)
    assert ct.choose_scheme((3, 0), "C:\\u\\proj\\_x") == ct.SCHEME_CURRENT
    assert ct.choose_scheme((0, 2), "C:\\u\\proj\\_x") == ct.SCHEME_LEGACY


def test_choose_scheme_ambiguous_invariant_target_ok():
    # tie evidence, but target has no underscore: schemes agree -> proceed
    assert ct.choose_scheme((0, 0), "C:\\u\\proj\\plain") == ct.SCHEME_CURRENT


def test_choose_scheme_ambiguous_variant_target_refuses():
    with pytest.raises(ct.LayoutError):
        ct.choose_scheme((1, 1), "C:\\u\\proj\\_Tools\\x")
    with pytest.raises(ct.LayoutError):
        ct.choose_scheme((0, 0), "C:\\u\\proj\\_Tools\\x")
