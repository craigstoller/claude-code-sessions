import json
import os

import pytest

import claude_threads as ct

SID = "eng-sess"


class SimulatedCrash(Exception):
    pass


@pytest.fixture
def planned(mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    src_cwd = "C:\\proj\\src"
    target = str(tmp_path / "target")
    os.makedirs(target)
    t = write_transcript(env, ct.encode(src_cwd, ct.SCHEME_CURRENT), SID,
                         [{"cwd": src_cwd}, {"msg": 1}])
    side = ct.sidecar_path(t)
    os.makedirs(os.path.join(side, "sub"))
    open(os.path.join(side, "sub", "agent.jsonl"), "w").write("agent")
    write_row(env, 0, "org", "acct", "local_r1",
              {"sessionId": "local_r1", "cliSessionId": SID, "cwd": src_cwd,
               "originCwd": src_cwd, "title": "T"})
    # evidence folder + row so scheme detection resolves unambiguously
    # regardless of what pytest's own tmp_path segment happens to contain
    # (identical precedent: test_plan_move.py::setup)
    os.makedirs(os.path.join(env.projects_root, ct.encode("C:\\proj\\_ev", ct.SCHEME_CURRENT)))
    write_row(env, 0, "org", "acct", "local_ev",
              {"sessionId": "local_ev", "cliSessionId": "other", "cwd": "C:\\proj\\_ev",
               "lastActivityAt": 2})
    m = ct.plan_move(env, SID, target, ct.MoveFlags())
    yield env, m, t, target
    ct._crash_hook = None


def test_happy_move_completes(planned):
    env, m, t, target = planned
    assert ct.run_move(env, m) == "completed"
    assert not os.path.exists(t) and not os.path.exists(ct.sidecar_path(t))
    assert os.path.isfile(m["dest_transcript"])
    assert os.path.isfile(os.path.join(m["sidecar_dest"], "sub", "agent.jsonl"))
    row = json.load(open(m["rows"][0]["path"], encoding="utf-8"))
    assert row["cwd"] == m["target_cwd"]
    assert ct.moved_session_ids(env) == {SID}
    assert ct.list_ops(env)[0].manifest["status"] == "completed"


@pytest.mark.parametrize("crash_after", ["journaled", "copying", "copied", "rewriting", "committed"])
def test_crash_after_each_phase_never_loses_data(planned, crash_after):
    env, m, t, target = planned
    def hook(point):
        if point == "after-" + crash_after:
            raise SimulatedCrash()
    ct._crash_hook = hook
    with pytest.raises(SimulatedCrash):
        ct.run_move(env, m)
    ct._crash_hook = None
    # no-loss invariant: source still present (delete is last), rows classifiable
    assert os.path.isfile(t)
    op = ct.list_ops(env)[0]
    assert op.manifest["status"] == crash_after
    assert ct.nonterminal_ops(env)          # recover has something to resolve
    # lock must have been released by the finally
    assert ct.read_lock(env) is None


def test_source_drift_at_last_instant_aborts_to_rolled_back(planned):
    env, m, t, target = planned
    def hook(point):
        if point == "after-committed":
            with open(t, "a") as fh:        # app writes to source mid-operation
                fh.write("\nlate write")
    ct._crash_hook = hook
    assert ct.run_move(env, m) == "rolled_back"
    assert os.path.isfile(t)                          # source kept
    row = json.load(open(m["rows"][0]["path"], encoding="utf-8"))
    assert row["cwd"] != m["target_cwd"]              # rows restored to pre-state


def test_dest_corruption_at_last_instant_aborts(planned):
    env, m, t, target = planned
    def hook(point):
        if point == "after-committed":
            with open(m["dest_transcript"], "w") as fh:
                fh.write("truncated!")
    ct._crash_hook = hook
    assert ct.run_move(env, m) == "rolled_back"
    assert os.path.isfile(t)


def test_row_write_failure_mid_set_rolls_back(planned, mkenv, tmp_path, write_row,
                                              write_transcript, monkeypatch):
    env, m, t, target = planned
    write_row(env, 0, "org", "acct2", "local_r2",
              {"sessionId": "local_r2", "cliSessionId": SID, "cwd": "C:\\proj\\src"})
    m2 = ct.plan_move(env, SID, target, ct.MoveFlags())
    assert len(m2["rows"]) == 2
    calls = {"n": 0}
    real = ct.atomic_write
    def failing(path, data):
        if path.endswith(".json") and "local_" in os.path.basename(path):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("disk gone")
        return real(path, data)
    monkeypatch.setattr(ct, "atomic_write", failing)
    assert ct.run_move(env, m2) == "rolled_back"
    monkeypatch.undo()
    for r in m2["rows"]:                     # both rows back to pre-state bytes
        assert open(r["path"], "rb").read() == ct.unb64(r["pre_b64"])
    assert os.path.isfile(t)


def test_second_instance_locked_out(planned):
    env, m, t, target = planned
    ct.acquire_lock(env, "other-op")
    with pytest.raises(ct.Refusal, match="lock"):
        ct.run_move(env, m)
    ct.release_lock(env)
