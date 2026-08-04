import json
import os

import pytest

import claude_code_sessions as ct
from test_engine import SimulatedCrash  # reuse, matches test_recover.py precedent

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


# ------------------------------------------ adversarial review round fixes


def test_undo_refuses_when_undo_target_already_exists(moved):
    """C1: plan_undo must check its OWN destination (the original source
    path) before an op is even journaled. _abort's scratch rule treats a
    journaled/copying-phase destination as tool-owned and deletes it
    unconditionally (no hash check) once the copy's O_EXCL create fails -
    a foreign file the user manually restored there (and maybe resumed)
    would otherwise be silently destroyed."""
    env, m, t = moved
    with open(t, "w") as fh:
        fh.write("user manually restored + resumed here")
    with pytest.raises(ct.Refusal):
        ct.run_undo(env, ct.list_ops(env)[0])
    assert os.path.isfile(t)
    assert open(t, encoding="utf-8").read() == "user manually restored + resumed here"
    assert os.path.isfile(m["dest_transcript"])          # moved copy also untouched
    assert ct.nonterminal_ops(env) == []                  # no op was ever journaled


def test_undo_refuses_when_moved_transcript_missing(moved):
    """I2: sha256_file on a missing dest_transcript must not raise an
    unhandled FileNotFoundError - it must surface as a Refusal."""
    env, m, t = moved
    os.unlink(m["dest_transcript"])
    with pytest.raises(ct.Refusal, match="missing"):
        ct.run_undo(env, ct.list_ops(env)[0])


def test_undo_refuses_on_untracked_dest_sidecar_file(moved):
    """I3: plan_undo's sidecar check only verified journaled-subset-of-
    present; the reverse (present-subset-of-journaled) was missing. A file
    that appeared in the moved sidecar AFTER the move (never journaled) is
    post-move activity and must block undo, naming the file, without
    touching anything."""
    env, m, t = moved
    extra = os.path.join(m["sidecar_dest"], "sub", "untracked.txt")
    with open(extra, "w") as fh:
        fh.write("not journaled")
    with pytest.raises(ct.Refusal) as exc_info:
        ct.run_undo(env, ct.list_ops(env)[0])
    assert "untracked.txt" in str(exc_info.value)
    assert os.path.isfile(extra)                          # nothing mutated
    assert os.path.isfile(m["dest_transcript"])
    assert ct.nonterminal_ops(env) == []


def test_undo_refuses_undo_of_undo(moved):
    """I1 ruling: an undo op is never itself undo-able - 'to redo, run move
    again' keeps moved-log semantics coherent (an undo-of-undo would need a
    THIRD moved-log kind to cancel correctly, which the log format doesn't
    have)."""
    env, m, t = moved
    prior = ct.list_ops(env)[0]
    assert ct.run_undo(env, prior) == "completed"
    undo_op = [o for o in ct.list_ops(env) if o.manifest.get("op_type") == "undo"][0]
    with pytest.raises(ct.Refusal, match="redo"):
        ct.run_undo(env, undo_op)


def _make_same_session_op(env, prior, op_id_suffix, at_offset, status):
    """A bare op for SID, positioned relative to `prior` via an explicit
    op_id (sorts lexicographically after prior's) and an explicit
    history[0]['at'] (may be before OR after prior's) - used to prove
    plan_undo's newer-op guard follows (at, op_id), not op_id alone."""
    other = ct.new_op(env, {"op_type": "move", "session_id": SID,
                            "source_transcript": "x", "dest_transcript": "y",
                            "rows": []})
    other.manifest["op_id"] = prior.manifest["op_id"] + op_id_suffix
    other.manifest["history"][0]["at"] = prior.manifest["history"][0]["at"] + at_offset
    ct.set_status(other, status)
    return other


def test_plan_undo_newer_op_check_uses_at_not_opid_string(moved):
    """I1: the 'newer op touches this session' guard must sort by
    (history[0]['at'], op_id) - not by op_id string alone. Here `other`'s
    op_id sorts AFTER prior's (which the old op_id-only comparison would
    read as "newer"), but its `at` is BEFORE prior's, so it is actually
    older and must not block the undo."""
    env, m, t = moved
    prior = ct.list_ops(env)[0]
    _make_same_session_op(env, prior, "z", -1, "completed")
    assert ct.run_undo(env, prior) == "completed"


def test_plan_undo_refuses_when_newer_op_still_in_effect(moved):
    """Sanity counterpart: a genuinely newer, still-completed op on the same
    session must still block undo (the guard's actual purpose)."""
    env, m, t = moved
    prior = ct.list_ops(env)[0]
    _make_same_session_op(env, prior, "z", 1, "completed")
    with pytest.raises(ct.Refusal, match="newer"):
        ct.run_undo(env, prior)


def test_plan_undo_ignores_undone_and_rolled_back_newer_ops(moved):
    """I1: a newer same-session op whose own effect has already been
    cancelled (undone) or that never took effect (rolled_back) must not
    block undoing an older op."""
    env, m, t = moved
    prior = ct.list_ops(env)[0]
    _make_same_session_op(env, prior, "y", 1, "undone")
    _make_same_session_op(env, prior, "z", 2, "rolled_back")
    assert ct.run_undo(env, prior) == "completed"


def test_undo_crash_recover_marks_prior_undone(moved):
    """C2: recovering a crashed undo must derive the moved-log entry kind
    from the UNDO op's own op_type (not hardcode 'move'), and on completion
    must mark the ORIGINAL move op 'undone' via undo_of - recover_op's
    completion path previously only knew about forward moves, so a crash
    between an undo reaching 'committed' and run_undo's own bookkeeping
    left the original op stuck reading 'completed' forever."""
    env, m, t = moved
    prior = ct.list_ops(env)[0]

    def hook(point):
        if point == "after-committed":
            raise SimulatedCrash()
    ct._crash_hook = hook
    with pytest.raises(SimulatedCrash):
        ct.run_undo(env, prior)
    ct._crash_hook = None

    assert prior.manifest["status"] == "completed"        # crash: never reached "undone"
    undo_op = ct.nonterminal_ops(env)[0]
    assert undo_op.manifest["status"] == "committed"
    assert undo_op.manifest["op_type"] == "undo"

    assert ct.recover_op(env, undo_op, "forward") == "completed"
    assert ct.moved_session_ids(env) == set()
    reloaded_prior = [o for o in ct.list_ops(env)
                      if o.manifest["op_id"] == prior.manifest["op_id"]][0]
    assert reloaded_prior.manifest["status"] == "undone"


@pytest.fixture
def moved_transcript_only(mkenv, tmp_path, write_transcript, write_row):
    """A move done with --transcript-only: no listing rows adopted, even
    though the store itself is populated (with an unrelated row, purely for
    scheme-detection evidence)."""
    env = mkenv(tmp_path)
    src_cwd = "C:\\proj\\src2"
    target = str(tmp_path / "target2")
    os.makedirs(target)
    t = write_transcript(env, ct.encode(src_cwd, ct.SCHEME_CURRENT), "undo-sess-to",
                         [{"cwd": src_cwd}])
    # evidence row so scheme detection resolves unambiguously regardless of
    # what pytest's own tmp_path segment happens to contain (same precedent
    # as `moved` above); this row is unrelated to the session being moved,
    # which is exactly the point - the move itself adopts zero rows.
    os.makedirs(os.path.join(env.projects_root, ct.encode("C:\\proj\\_ev", ct.SCHEME_CURRENT)))
    write_row(env, 0, "org", "acct", "local_ev",
              {"sessionId": "local_ev", "cliSessionId": "other", "cwd": "C:\\proj\\_ev",
               "lastActivityAt": 2})
    m = ct.plan_move(env, "undo-sess-to", target, ct.MoveFlags(transcript_only=True))
    assert ct.run_move(env, m) == "completed"
    return env, m, t


def test_undo_roundtrip_transcript_only_no_rows(moved_transcript_only):
    """Traced from the brief: an undo roundtrip with no rows at all exercises
    plan_undo's row loop over an empty list, and M4's target_cwd fallback to
    '' when there are no rows to read a pre-image cwd from."""
    env, m, t = moved_transcript_only
    assert m["rows"] == []
    prior = ct.list_ops(env)[0]
    assert ct.run_undo(env, prior) == "completed"
    assert os.path.isfile(t)
    assert not os.path.exists(m["dest_transcript"])
    assert prior.manifest["status"] == "undone"


def test_undo_target_cwd_is_original_source_not_move_target(moved):
    """M4: the undo manifest's target_cwd must describe where undo takes the
    session (the ORIGINAL source cwd), not the forward move's target_cwd."""
    env, m, t = moved
    prior = ct.list_ops(env)[0]
    undo_manifest = ct.plan_undo(env, prior)
    assert undo_manifest["target_cwd"] == "C:\\proj\\src"
    assert undo_manifest["target_cwd"] != m["target_cwd"]
