import copy
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
    assert os.path.isfile(m["dest_transcript"])       # keep-both: corrupt dest survives too


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
    assert not os.path.isfile(m2["dest_transcript"])            # dest copies removed
    assert not os.path.exists(m2["sidecar_dest"])


def test_second_instance_locked_out(planned):
    env, m, t, target = planned
    ct.acquire_lock(env, "other-op")
    with pytest.raises(ct.Refusal, match="lock"):
        ct.run_move(env, m)
    ct.release_lock(env)


# ---------------------------------------------------- review-round fixes


def test_uninventoried_source_sidecar_file_blocks_deletion(planned):
    """C1: a file that showed up in the source sidecar after journaling (so
    it was never copied anywhere) must never be destroyed by the final
    source-deletion step - it is the only copy of its data."""
    env, m, t, target = planned
    extra = os.path.join(m["sidecar_source"], "sub", "untracked.txt")
    def hook(point):
        if point == "after-committed":
            with open(extra, "w") as fh:
                fh.write("not journaled")
    ct._crash_hook = hook
    assert ct.run_move(env, m) == "rolled_back"
    assert os.path.isfile(extra)
    assert os.path.isfile(t)
    assert os.path.isfile(os.path.join(m["sidecar_source"], "sub", "agent.jsonl"))


def test_manifest_tampering_rejected_before_mutation(planned, tmp_path):
    """C2: a manifest whose sidecar_dest escapes projects_root, or whose
    inventory rel path traverses ('..'), must be rejected before any file
    is touched."""
    env, m, t, target = planned
    m2 = copy.deepcopy(m)
    m2["sidecar_dest"] = str(tmp_path / "outside-projects-root")
    m2["sidecar_inventory"] = [{"rel": "../evil.txt", "sha256": "0" * 64, "size": 0}]
    with pytest.raises(ct.LayoutError):
        ct.run_move(env, m2)
    assert os.path.isfile(t)                          # source untouched
    assert not os.path.exists(m2["dest_transcript"])   # no copy ever started


def test_abort_restores_row_by_content_not_flag(planned):
    """I4: a crash between os.replace and save_manifest can leave a row's
    on-disk bytes at the post-image while manifest still says rewritten is
    False. _abort must trust the bytes, not the flag."""
    env, m, t, target = planned
    op = ct.new_op(env, m)
    row = op.manifest["rows"][0]
    ct.atomic_write(row["path"], ct.unb64(row["post_b64"]))
    assert row["rewritten"] is False
    ct._abort(env, op)
    assert open(row["path"], "rb").read() == ct.unb64(row["pre_b64"])
    assert op.manifest["status"] == "rolled_back"
    assert op.manifest["drifted_rows"] == []


def test_copy_failure_mid_copy_rolls_back(planned, monkeypatch):
    """I5: an OSError during the copy phase (e.g. ENOSPC) must roll back
    cleanly instead of propagating uncaught."""
    env, m, t, target = planned
    calls = {"n": 0}
    real_copy = ct._copy_file
    def failing(src, dst):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError(28, "No space left on device")
        return real_copy(src, dst)
    monkeypatch.setattr(ct, "_copy_file", failing)
    assert ct.run_move(env, m) == "rolled_back"
    monkeypatch.undo()
    assert os.path.isfile(t)
    assert not os.path.isfile(m["dest_transcript"])    # partial dest cleaned (scratch rule)


def test_crash_mid_rewriting_two_rows_recoverable(planned, write_row):
    """I9(a): a crash between rewriting row 1 and row 2 of a multi-row op
    must leave a nonterminal op with both rows' pre-images still intact in
    the journal, and the source untouched."""
    env, m, t, target = planned
    write_row(env, 0, "org", "acct2", "local_r2",
              {"sessionId": "local_r2", "cliSessionId": SID, "cwd": "C:\\proj\\src"})
    m2 = ct.plan_move(env, SID, target, ct.MoveFlags())
    assert len(m2["rows"]) == 2
    def hook(point):
        if point == "mid-rewriting":
            raise SimulatedCrash()
    ct._crash_hook = hook
    with pytest.raises(SimulatedCrash):
        ct.run_move(env, m2)
    ct._crash_hook = None
    op = ct.list_ops(env)[0]
    assert op.manifest["status"] == "rewriting"
    assert ct.nonterminal_ops(env)
    for r in op.manifest["rows"]:
        assert r["pre_b64"]
    assert os.path.isfile(t)


def test_sidecar_delete_failure_keeps_source_and_stays_committed(planned, monkeypatch):
    """Fix-round-2: a locked/undeletable sidecar file during the final
    commit step must not be silently swallowed. It must not reach
    'completed' (which would delete the source transcript out from under
    the surviving sidecar file, orphaning it with no journal trail) - it
    must stay nonterminal at 'committed' with the source transcript intact
    for `recover` to finish."""
    env, m, t, target = planned
    sidecar_file = os.path.join(m["sidecar_source"], "sub", "agent.jsonl")
    real_unlink = os.unlink
    def failing_unlink(path, *a, **kw):
        if path == sidecar_file:
            raise PermissionError(13, "Access is denied")
        return real_unlink(path, *a, **kw)
    monkeypatch.setattr(os, "unlink", failing_unlink)
    assert ct.run_move(env, m) == "committed"
    monkeypatch.undo()
    op = ct.list_ops(env)[0]
    assert op.manifest["status"] == "committed"
    assert ct.nonterminal_ops(env)
    assert os.path.isfile(t)               # source transcript NOT deleted
    assert os.path.isfile(sidecar_file)    # sidecar file survives, not orphaned


def test_row_drift_before_rewrite_blocks_overwrite(planned):
    """Task 12 review (ENGINE ruling): execute_op's rewriting phase must
    re-read each row's CURRENT bytes and compare against its journaled
    pre-image immediately before writing it - a row that changed between
    planning and rewriting (some other process touched it) must never be
    blindly overwritten. This reuses _abort's existing, adversarially-
    hardened drifted-row protection (I3/C1 - see test_recover.py's
    aborting-dead-end tests): a row that is neither the journaled pre- nor
    post-image is unsafe to auto-resolve either way, so _abort itself
    refuses rather than guessing, leaving the op nonterminal at 'aborting'
    for `claude-threads recover` - not a silent 'rolled_back'."""
    env, m, t, target = planned
    row = m["rows"][0]

    def hook(point):
        if point == "after-copied":
            open(row["path"], "w").write("some other process changed this row")
    ct._crash_hook = hook
    with pytest.raises(ct.Refusal):
        ct.run_move(env, m)
    ct._crash_hook = None

    assert open(row["path"]).read() == "some other process changed this row"  # untouched
    op = ct.list_ops(env)[0]
    assert op.manifest["status"] == "aborting"        # nonterminal; recover required
    assert row["path"] in op.manifest.get("drifted_rows", [])
    assert os.path.isfile(t)                          # source kept
    assert os.path.isfile(m["dest_transcript"])        # dest also kept (nothing deleted)
    assert ct.read_lock(env) is None                   # lock released despite the raise
