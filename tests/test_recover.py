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
        # sidecar tree (a file under a subdir) so every sidecar branch -
        # dest copy, source cleanup, the untracked-file guard - is actually
        # exercised, not just the bare-transcript path.
        side = ct.sidecar_path(t)
        os.makedirs(os.path.join(side, "sub"))
        open(os.path.join(side, "sub", "agent.jsonl"), "w").write("agent")
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
    assert os.path.isfile(os.path.join(m["sidecar_dest"], "sub", "agent.jsonl"))
    assert not os.path.exists(m["sidecar_source"])


def test_rollback_after_rewriting_crash(crashed):
    env, m, t = crashed("rewriting")
    op = ct.nonterminal_ops(env)[0]
    assert ct.recover_op(env, op, "back") == "rolled_back"
    assert os.path.isfile(t)
    assert open(m["rows"][0]["path"], "rb").read() == ct.unb64(m["rows"][0]["pre_b64"])
    assert not os.path.exists(m["dest_transcript"])
    assert not os.path.exists(m["sidecar_dest"])
    assert os.path.isfile(os.path.join(m["sidecar_source"], "sub", "agent.jsonl"))


def test_committed_crash_forward_allows_growth(crashed):
    env, m, t = crashed("committed")
    with open(m["dest_transcript"], "a") as fh:      # user resumed the moved thread
        fh.write('{"resumed": true}\n')
    op = ct.nonterminal_ops(env)[0]
    c = ct.classify_op(env, op)
    assert c["dest"] == "grown" and c["resolutions"] == ["forward"]
    assert ct.recover_op(env, op, "forward") == "completed"
    assert not os.path.exists(t)
    assert not os.path.exists(m["sidecar_source"])


def test_committed_crash_truncated_dest_blocks(crashed):
    env, m, t = crashed("committed")
    open(m["dest_transcript"], "w").write("gone")
    op = ct.nonterminal_ops(env)[0]
    c = ct.classify_op(env, op)
    assert c["dest"] == "drifted" and c["resolutions"] == []
    with pytest.raises(ct.Refusal):
        ct.recover_op(env, op, "forward")
    assert os.path.isfile(t)                          # source untouched
    assert os.path.isfile(os.path.join(m["sidecar_source"], "sub", "agent.jsonl"))


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
    assert not os.path.exists(m["sidecar_source"])


# ------------------------------------------ adversarial review round fixes


def test_forward_refuses_drifted_row_writes_nothing(crashed):
    """C1: recover_op's copied/rewriting forward path must classify every
    row's CURRENT bytes before writing any of them. A row that is neither
    the journaled pre- nor post-image (some other process wrote to it) must
    block the WHOLE batch - no row gets written, not even the ones that
    would have been safe."""
    env, m, t = crashed("rewriting")
    op = ct.nonterminal_ops(env)[0]
    row = op.manifest["rows"][0]
    with open(row["path"], "wb") as fh:
        fh.write(b"neither pre nor post")
    c = ct.classify_op(env, op)
    assert row["path"] in c["drifted_rows"]
    with pytest.raises(ct.Refusal):
        ct.recover_op(env, op, "forward")
    assert open(row["path"], "rb").read() == b"neither pre nor post"   # untouched
    assert ct.list_ops(env)[0].manifest["status"] == "rewriting"       # never advanced


def test_forward_gate_blocks_before_any_row_write(crashed):
    """C2: the finish gate (source pre, dest intact/grown) must be checked
    BEFORE any row is written. A destination that has genuinely drifted (not
    just grown) at 'rewriting' can never pass the finish gate, so forward
    must refuse before touching the row - never flip it to point at a bad
    copy first and then discover the problem."""
    env, m, t = crashed("rewriting")
    open(m["dest_transcript"], "w").write("truncated, not a valid prefix")
    op = ct.nonterminal_ops(env)[0]
    c = ct.classify_op(env, op)
    assert c["dest"] == "drifted"
    assert c["resolutions"] == []
    with pytest.raises(ct.Refusal):
        ct.recover_op(env, op, "forward")
    row = op.manifest["rows"][0]
    assert open(row["path"], "rb").read() == ct.unb64(row["pre_b64"])   # never written
    assert ct.list_ops(env)[0].manifest["status"] == "rewriting"        # never advanced


def test_aborting_dead_end_from_drifted_row_reports_no_resolution(crashed):
    """I3: if a row drifts during an abort attempt (so _abort itself keeps
    refusing), classify_op must stop offering 'back' forever - that would be
    a dead end where recover always raises the same Refusal. It should
    report resolutions=[] with an explanatory note instead."""
    env, m, t = crashed("rewriting")
    op = ct.nonterminal_ops(env)[0]
    row = op.manifest["rows"][0]
    with open(row["path"], "wb") as fh:
        fh.write(b"drifted mid-abort")
    with pytest.raises(ct.Refusal):
        ct.recover_op(env, op, "back")
    op2 = ct.nonterminal_ops(env)[0]
    assert op2.manifest["status"] == "aborting"
    c = ct.classify_op(env, op2)
    assert c["resolutions"] == []
    assert row["path"] in c["drifted_rows"]


def test_abort_resume_from_copying_still_scratch_deletes(crashed):
    """I3: if a crash interrupts _abort itself while it is rolling back an
    op that started aborting from the 'copying' phase, a SECOND _abort call
    (as recover's 'back' path performs) must still recognize the original
    phase was scratch (via history), not the now-current 'aborting' status -
    otherwise a genuinely partial (hash-mismatched) dest file can never be
    deleted by automatic rollback (hash-gated deletion would refuse it
    forever, since a partial copy never matches the journaled hash)."""
    env, m, t = crashed("copying")
    op = ct.nonterminal_ops(env)[0]
    assert op.manifest["status"] == "copying"

    # simulate what a real mid-copy interruption would leave: a dest file
    # that exists but does NOT match the journaled hash - a true partial.
    os.makedirs(os.path.dirname(m["dest_transcript"]), exist_ok=True)
    with open(m["dest_transcript"], "wb") as fh:
        fh.write(b"partial, does not match the journaled hash")

    def hook(point):
        if point == "after-aborting":
            raise SimulatedCrash()
    ct._crash_hook = hook
    with pytest.raises(SimulatedCrash):
        ct._abort(env, op)
    ct._crash_hook = None

    op2 = ct.nonterminal_ops(env)[0]
    assert op2.manifest["status"] == "aborting"
    assert os.path.isfile(m["dest_transcript"])   # first abort crashed before deleting it

    assert ct.recover_op(env, op2, "back") == "rolled_back"
    assert os.path.isfile(t)
    assert not os.path.exists(m["dest_transcript"])


def test_recover_forward_validates_containment_before_mutating(crashed, tmp_path):
    """I4: recover must validate every manifest path is contained under a
    recognized root BEFORE mutating anything - not just on execute_op's
    fresh runs. A manifest whose source_transcript now points outside
    projects_root (tampered, or corrupted) must be rejected up front."""
    env, m, t = crashed("committed")
    op = ct.nonterminal_ops(env)[0]
    outside = str(tmp_path / "escaped.jsonl")
    with open(outside, "wb") as fh:
        fh.write(open(t, "rb").read())
    op.manifest["source_transcript"] = outside
    with pytest.raises(ct.LayoutError):
        ct.recover_op(env, op, "forward")
    assert os.path.isfile(outside)          # escaped file survives untouched
    assert os.path.isfile(t)                # real source also untouched


def test_recover_op_refuses_when_lock_held(crashed):
    """I5: recover_op mutates state exactly like run_move does, so it must
    take the same single-instance lock."""
    env, m, t = crashed("copying")
    op = ct.nonterminal_ops(env)[0]
    ct.acquire_lock(env, "other-op")
    with pytest.raises(ct.Refusal, match="lock"):
        ct.recover_op(env, op, "forward")
    ct.release_lock(env)


def test_finish_committed_refuses_while_claude_running(crashed):
    """I6(a): _finish_committed must re-check the process guard - deleting
    the source while Claude might be actively writing to it is exactly the
    hazard the pre-move guard exists to prevent."""
    env, m, t = crashed("committed")
    op = ct.nonterminal_ops(env)[0]
    env.process_lister = lambda: [(999999, "claude")]
    with pytest.raises(ct.Refusal):
        ct.recover_op(env, op, "forward")
    assert os.path.isfile(t)


def test_finish_committed_refuses_on_untracked_sidecar_file(crashed):
    """I6(b): the same C1 guard execute_op's own commit step uses - a file
    that appeared in the source sidecar after commit was never journaled and
    must never be destroyed. Forward must refuse and keep both copies."""
    env, m, t = crashed("committed")
    op = ct.nonterminal_ops(env)[0]
    extra = os.path.join(m["sidecar_source"], "sub", "untracked.txt")
    with open(extra, "w") as fh:
        fh.write("not journaled")
    with pytest.raises(ct.Refusal):
        ct.recover_op(env, op, "forward")
    assert os.path.isfile(extra)
    assert os.path.isfile(t)
    assert os.path.isfile(os.path.join(m["sidecar_source"], "sub", "agent.jsonl"))


def test_journaled_scratch_blocked_when_source_missing(crashed):
    """I7: the journaled/copying scratch-deletion rule assumes the source is
    still pristine. If the source has vanished by the time recover runs, the
    partial destination might be the only remaining copy of the data -
    never delete it, and never resume the copy automatically either."""
    env, m, t = crashed("copying")
    os.unlink(t)
    op = ct.nonterminal_ops(env)[0]
    c = ct.classify_op(env, op)
    assert c["source"] == "missing"
    assert c["resolutions"] == []
    with pytest.raises(ct.Refusal):
        ct.recover_op(env, op, "forward")
    with pytest.raises(ct.Refusal):
        ct.recover_op(env, op, "back")


def test_journaled_scratch_blocked_when_source_drifted(crashed):
    """I7, drifted variant: same rule, but the source is still present and
    merely changed (not vanished) - still not the pristine pre-image the
    scratch rule assumes, so it must block just like the missing case."""
    env, m, t = crashed("copying")
    with open(t, "a") as fh:
        fh.write("app wrote here")
    op = ct.nonterminal_ops(env)[0]
    c = ct.classify_op(env, op)
    assert c["source"] == "drifted"
    assert c["resolutions"] == []


# --------------------------------------------- C1: crash-interrupted keep-both abort


def test_hash_gated_abort_refuses_when_source_unverifiable_without_prior_flag(crashed):
    """C1(a): a hash-gated (non-scratch) abort must verify the SOURCE
    against its journaled pre-state before deleting any hash-matching
    destination file - not just check the destination's own hash. Here
    "back" is resumed directly from 'rewriting' (no prior abort attempt, so
    no abort_keep_dest has ever been set) with the source deleted out from
    under it while the destination copy is still perfectly intact: the old
    hash-gate only looked at the DEST and would have deleted the last
    remaining copy. It must refuse instead, and persist the keep-both
    decision so a retry does not need to re-derive it."""
    env, m, t = crashed("rewriting")
    os.unlink(t)
    op = ct.nonterminal_ops(env)[0]
    with pytest.raises(ct.Refusal, match="source"):
        ct.recover_op(env, op, "back")
    assert os.path.isfile(m["dest_transcript"])
    assert os.path.isfile(os.path.join(m["sidecar_dest"], "sub", "agent.jsonl"))
    op2 = ct.nonterminal_ops(env)[0]
    assert op2.manifest.get("abort_keep_dest") is True
    assert op2.manifest.get("abort_reason")


def test_crash_during_keep_both_abort_recover_back_preserves_dest(crashed):
    """C1(d): regression for the reviewer's repro. execute_op's phase-6
    failure path persists the keep-both decision (abort_keep_dest +
    abort_reason) to the manifest BEFORE it ever calls _abort (C1b) - so
    even if THAT abort is itself crash-interrupted right after entering
    'aborting' (before it reaches the terminal rolled_back state), a later
    `recover --back` must still honor the keep-both decision and never
    delete the destination - regardless of the fact that recover's "back"
    always calls _abort with its own default delete_dest=True.

    Before this fix the persisted decision was invisible to the resumed
    call: prior_status resolved (via history) to the ORIGINAL non-scratch
    phase, the dest file still hash-matched its journaled hash, and the
    old hash-gate said nothing about the source - so a resumed "back" would
    delete the last remaining copy after the source had already been lost.
    """
    env, m, t = crashed("committed")
    os.unlink(t)   # source vanishes before the (simulated) phase-6 abort

    # Simulate exactly what execute_op's phase-6 failure path does (C1b):
    # persist the keep-both decision BEFORE _abort is even called, then
    # crash partway through that very abort (right after it enters
    # "aborting", before row-restore or the terminal status transition).
    op = ct.nonterminal_ops(env)[0]
    op.manifest["abort_keep_dest"] = True
    op.manifest["abort_reason"] = "source changed at last instant"
    ct.save_manifest(op)

    def hook(point):
        if point == "after-aborting":
            raise SimulatedCrash()
    ct._crash_hook = hook
    with pytest.raises(SimulatedCrash):
        ct._abort(env, op, delete_dest=False)
    ct._crash_hook = None

    op2 = ct.nonterminal_ops(env)[0]
    assert op2.manifest["status"] == "aborting"
    assert os.path.isfile(m["dest_transcript"])   # nothing deleted by the crashed attempt

    c = ct.classify_op(env, op2)
    assert "back" in c["resolutions"]   # abort_keep_dest shortcut still offers it (C1c)

    try:
        result = ct.recover_op(env, op2, "back")
        assert result in ("rolled_back", "completed")
    except ct.Refusal:
        pass   # "refuse-or-complete" - either is acceptable, but nothing may be lost

    assert os.path.isfile(m["dest_transcript"])
    assert os.path.isfile(os.path.join(m["sidecar_dest"], "sub", "agent.jsonl"))
