import json
import os

import pytest

import claude_threads as ct

SID = "undo-sess"


@pytest.fixture
def moved(mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    src_cwd = "C:\\proj\\src"
    target = str(tmp_path / "target")
    os.makedirs(target)
    t = write_transcript(env, ct.encode(src_cwd, ct.SCHEME_CURRENT), SID, [{"cwd": src_cwd}])
    # sidecar tree (a file under a subdir) so undo's sidecar handling is
    # exercised, not just the bare-transcript path (identical precedent:
    # test_engine.py::planned, test_recover.py::crashed)
    side = ct.sidecar_path(t)
    os.makedirs(os.path.join(side, "sub"))
    open(os.path.join(side, "sub", "agent.jsonl"), "w").write("agent")
    write_row(env, 0, "org", "acct", "local_r1",
              {"sessionId": "local_r1", "cliSessionId": SID, "cwd": src_cwd,
               "originCwd": src_cwd})
    # evidence folder + row so scheme detection resolves unambiguously
    # regardless of what pytest's own tmp_path segment happens to contain
    # (identical precedent: test_engine.py::planned, test_recover.py::crashed)
    os.makedirs(os.path.join(env.projects_root, ct.encode("C:\\proj\\_ev", ct.SCHEME_CURRENT)))
    write_row(env, 0, "org", "acct", "local_ev",
              {"sessionId": "local_ev", "cliSessionId": "other", "cwd": "C:\\proj\\_ev",
               "lastActivityAt": 2})
    m = ct.plan_move(env, SID, target, ct.MoveFlags())
    assert ct.run_move(env, m) == "completed"
    return env, m, t


def test_undo_roundtrip(moved):
    env, m, t = moved
    prior = ct.list_ops(env)[0]
    assert ct.run_undo(env, prior) == "completed"
    assert os.path.isfile(t)                              # transcript back home
    assert not os.path.exists(m["dest_transcript"])
    # sidecar tree round-tripped too
    assert os.path.isfile(os.path.join(ct.sidecar_path(t), "sub", "agent.jsonl"))
    assert not os.path.exists(m["sidecar_dest"])
    row = json.load(open(m["rows"][0]["path"], encoding="utf-8"))
    assert row["cwd"] == "C:\\proj\\src"
    # `prior` is the very Op object run_undo mutated in place (set_status
    # writes through the same reference); list_ops(env)[0] is deliberately
    # NOT re-queried here because the env's clock is frozen in tests, so the
    # move-op and the new undo-op share an identical history[0]["at"], and
    # list_ops's tie-break then falls to comparing random op_id suffixes -
    # not creation order.
    assert prior.manifest["status"] == "undone"
    assert ct.moved_session_ids(env) == set()             # undo entry cancels move


def test_undo_refuses_on_dest_growth(moved):
    env, m, t = moved
    with open(m["dest_transcript"], "a") as fh:
        fh.write('{"resumed": true}\n')
    with pytest.raises(ct.Refusal, match="changed"):
        ct.run_undo(env, ct.list_ops(env)[0])


def test_undo_refuses_on_row_drift(moved):
    env, m, t = moved
    p = m["rows"][0]["path"]
    data = json.load(open(p, encoding="utf-8"))
    data["title"] = "app renamed me"
    open(p, "w").write(json.dumps(data))
    with pytest.raises(ct.Refusal, match="changed"):
        ct.run_undo(env, ct.list_ops(env)[0])


def test_undo_refuses_non_completed(moved):
    env, m, t = moved
    prior = ct.list_ops(env)[0]
    assert ct.run_undo(env, prior) == "completed"
    with pytest.raises(ct.Refusal):
        ct.run_undo(env, prior)                           # now status == "undone"


# --------------------------------------------------- deltas: guards & locking


def test_undo_refuses_while_claude_running(moved):
    """run_undo must check claude_running itself up front - plan_move's guard
    does not run for undo (the engine's process guard otherwise only fires
    at execute_op's last-instant revalidation)."""
    env, m, t = moved
    prior = ct.list_ops(env)[0]
    env.process_lister = lambda: [(999999, "claude")]
    with pytest.raises(ct.Refusal):
        ct.run_undo(env, prior)
    assert os.path.isfile(m["dest_transcript"])   # nothing touched
    assert not os.path.isfile(t)
    assert prior.manifest["status"] == "completed"


def test_undo_refuses_when_lock_held(moved):
    """run_undo takes the single-instance lock (a distinctive 'undo-<op_id>'
    name), same as run_move/recover_op."""
    env, m, t = moved
    prior = ct.list_ops(env)[0]
    ct.acquire_lock(env, "other-op")
    with pytest.raises(ct.Refusal, match="lock"):
        ct.run_undo(env, prior)
    ct.release_lock(env)
    assert os.path.isfile(m["dest_transcript"])   # nothing touched
