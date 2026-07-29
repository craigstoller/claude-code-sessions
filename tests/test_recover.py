import os

import pytest

import claude_threads as ct
from test_engine import SimulatedCrash  # reuse

SID = "rec-sess"


@pytest.fixture
def crashed(mkenv, tmp_path, write_transcript, write_row):
    def make(crash_after):
        base = tmp_path / crash_after
        base.mkdir()
        env = mkenv(base)
        src_cwd = "C:\\proj\\src"
        target = str(base / "target")
        os.makedirs(target)
        t = write_transcript(env, ct.encode(src_cwd, ct.SCHEME_CURRENT), SID,
                             [{"cwd": src_cwd}])
        write_row(env, 0, "org", "acct", "local_r1",
                  {"sessionId": "local_r1", "cliSessionId": SID, "cwd": src_cwd})
        # evidence folder + row so scheme detection resolves unambiguously
        # regardless of what pytest's own tmp_path segment happens to contain
        # (identical precedent: test_engine.py::planned, test_plan_move.py)
        os.makedirs(os.path.join(env.projects_root, ct.encode("C:\\proj\\_ev", ct.SCHEME_CURRENT)))
        write_row(env, 0, "org", "acct", "local_ev",
                  {"sessionId": "local_ev", "cliSessionId": "other", "cwd": "C:\\proj\\_ev",
                   "lastActivityAt": 2})
        m = ct.plan_move(env, SID, target, ct.MoveFlags())
        def hook(point):
            if point == "after-" + crash_after:
                raise SimulatedCrash()
        ct._crash_hook = hook
        with pytest.raises(SimulatedCrash):
            ct.run_move(env, m)
        ct._crash_hook = None
        return env, m, t
    yield make
    ct._crash_hook = None


def test_scratch_rule_copying_crash(crashed):
    env, m, t = crashed("copying")
    op = ct.nonterminal_ops(env)[0]
    c = ct.classify_op(env, op)
    assert set(c["resolutions"]) == {"back", "forward"}
    assert ct.recover_op(env, op, "forward") == "completed"
    assert os.path.isfile(m["dest_transcript"]) and not os.path.exists(t)


def test_rollback_after_rewriting_crash(crashed):
    env, m, t = crashed("rewriting")
    op = ct.nonterminal_ops(env)[0]
    assert ct.recover_op(env, op, "back") == "rolled_back"
    assert os.path.isfile(t)
    assert open(m["rows"][0]["path"], "rb").read() == ct.unb64(m["rows"][0]["pre_b64"])
    assert not os.path.exists(m["dest_transcript"])


def test_committed_crash_forward_allows_growth(crashed):
    env, m, t = crashed("committed")
    with open(m["dest_transcript"], "a") as fh:      # user resumed the moved thread
        fh.write('{"resumed": true}\n')
    op = ct.nonterminal_ops(env)[0]
    c = ct.classify_op(env, op)
    assert c["dest"] == "grown" and c["resolutions"] == ["forward"]
    assert ct.recover_op(env, op, "forward") == "completed"
    assert not os.path.exists(t)


def test_committed_crash_truncated_dest_blocks(crashed):
    env, m, t = crashed("committed")
    open(m["dest_transcript"], "w").write("gone")
    op = ct.nonterminal_ops(env)[0]
    c = ct.classify_op(env, op)
    assert c["dest"] == "drifted" and c["resolutions"] == []
    with pytest.raises(ct.Refusal):
        ct.recover_op(env, op, "forward")
    assert os.path.isfile(t)                          # source untouched


def test_committed_source_drift_refuses(crashed):
    env, m, t = crashed("committed")
    with open(t, "a") as fh:
        fh.write("app wrote here")
    op = ct.nonterminal_ops(env)[0]
    assert ct.classify_op(env, op)["resolutions"] == []


def test_dest_drift_rewriting_never_deleted(crashed):
    env, m, t = crashed("rewriting")
    with open(m["dest_transcript"], "a") as fh:
        fh.write("user activity")
    op = ct.nonterminal_ops(env)[0]
    c = ct.classify_op(env, op)
    assert c["resolutions"] == ["forward"]
    with pytest.raises(ct.Refusal):
        ct.recover_op(env, op, "back")
    assert os.path.isfile(m["dest_transcript"])       # still there


def test_stale_lock_cleared_only_by_recover(crashed):
    env, m, t = crashed("copying")
    with open(os.path.join(env.ops_dir, "lock"), "w") as fh:
        fh.write("999999999 dead-op")
    assert ct.clear_stale_lock(env) is True
    assert ct.read_lock(env) is None


# ------------------------------------------------- I8 idempotency (Task 10)


def test_committed_crash_missing_source_recovers_forward(crashed):
    """A crash right after 'committed' leaves source and dest both intact and
    matching. If the source transcript is then deleted by hand (e.g. a prior
    partial recover attempt, or manual cleanup) before recover runs, forward
    recovery must still tolerate it and complete instead of refusing - the
    missing file is evidence that deletion already progressed, not drift."""
    env, m, t = crashed("committed")
    os.unlink(t)
    op = ct.nonterminal_ops(env)[0]
    c = ct.classify_op(env, op)
    assert c["source"] == "pre"
    assert c["resolutions"] == ["forward"]
    assert ct.recover_op(env, op, "forward") == "completed"
    assert not os.path.exists(t)
