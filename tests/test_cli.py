import itertools
import os

import pytest

import claude_threads as ct

SID = "cli-sess"


@pytest.fixture
def ready(mkenv, tmp_path, write_transcript, write_row, monkeypatch):
    env = mkenv(tmp_path)
    src = "C:\\proj\\src"
    target = str(tmp_path / "target")
    os.makedirs(target)
    t = write_transcript(env, ct.encode(src, ct.SCHEME_CURRENT), SID, [{"cwd": src}])
    write_row(env, 0, "o", "a", "local_1",
              {"sessionId": "local_1", "cliSessionId": SID, "cwd": src,
               "originCwd": src, "title": "CLI test"})
    # evidence folder + row so scheme detection resolves unambiguously
    # regardless of what pytest's own tmp_path segment happens to contain
    # (identical precedent: test_engine.py::planned, test_undo.py::moved,
    # test_recover.py::crashed) - tmp_path embeds the test's own name, which
    # contains underscores, so `target`'s current- vs legacy-scheme encoding
    # genuinely differs and the tie-break in choose_scheme would otherwise
    # raise LayoutError for this fixture.
    # a real transcript (not just a bare folder) backs the evidence row, so
    # `doctor` sees it as a normal listed session rather than a dead row -
    # `doctor` must come back clean (exit 0) for this fixture per
    # test_list_and_doctor_run.
    write_transcript(env, ct.encode("C:\\proj\\_ev", ct.SCHEME_CURRENT), "other",
                     [{"cwd": "C:\\proj\\_ev"}])
    write_row(env, 0, "o", "a", "local_ev",
              {"sessionId": "local_ev", "cliSessionId": "other", "cwd": "C:\\proj\\_ev",
               "lastActivityAt": 2})
    monkeypatch.setattr(ct, "default_env", lambda: env)
    return env, t, target


def test_dry_run_move_mutates_nothing(ready, capsys):
    env, t, target = ready
    assert ct.main(["move", SID, target]) == 0
    assert os.path.isfile(t)
    assert ct.list_ops(env) == []
    assert "dry run" in capsys.readouterr().out.lower()


def test_apply_move_and_undo(ready, capsys):
    env, t, target = ready
    assert ct.main(["move", SID, target, "--apply"]) == 0
    assert not os.path.exists(t)
    assert ct.main(["undo", "--apply"]) == 0
    assert os.path.isfile(t)


def test_refusal_exit_codes(ready, capsys):
    env, t, target = ready
    assert ct.main(["move", "missing-sid", target, "--apply"]) == 1
    assert "refused" in capsys.readouterr().err.lower()


def test_list_and_doctor_run(ready, capsys):
    env, t, target = ready
    assert ct.main(["list"]) == 0
    assert "CLI test" in capsys.readouterr().out
    assert ct.main(["doctor"]) == 0


def test_recover_lists_nonterminal(ready, capsys):
    env, t, target = ready
    from test_engine import SimulatedCrash
    m = ct.plan_move(env, SID, target, ct.MoveFlags())
    def hook(point):
        if point == "after-copied":
            raise SimulatedCrash()
    ct._crash_hook = hook
    with pytest.raises(SimulatedCrash):
        ct.run_move(env, m)
    ct._crash_hook = None
    assert ct.main(["recover"]) == 1
    out = capsys.readouterr().out
    assert "copied" in out
    op_id = ct.nonterminal_ops(env)[0].manifest["op_id"]
    assert ct.main(["recover", "--resolve", op_id, "--back", "--apply"]) == 0
    assert ct.main(["recover"]) == 0


def test_move_phase6_source_drift_prints_reason(ready, capsys):
    """I3: a phase-6 rollback (the source changed at the last instant) must
    not complete silently - `move --apply` prints only "result: rolled_back"
    otherwise, giving no hint that two copies now exist on disk. cmd_move
    must additionally print a reason naming the source and stating that
    both copies were kept."""
    env, t, target = ready
    def hook(point):
        if point == "after-committed":
            with open(t, "a") as fh:        # app writes to source mid-operation
                fh.write("\nlate write")
    ct._crash_hook = hook
    rc = ct.main(["move", SID, target, "--apply"])
    ct._crash_hook = None
    assert rc == 1
    out = capsys.readouterr().out
    assert "result: rolled_back" in out
    assert "reason:" in out
    assert "source" in out.lower()
    assert "both copies" in out.lower()
    assert os.path.isfile(t)                          # both copies really are kept
    assert any(o.manifest.get("abort_keep_dest") for o in ct.list_ops(env))


def test_recover_forward_back_mutually_exclusive(ready):
    """M2: --forward and --back on `recover` must be mutually exclusive at
    the argparse level, not silently resolved by if/else priority."""
    with pytest.raises(SystemExit):
        ct.build_parser().parse_args(["recover", "--resolve", "x", "--forward", "--back"])


# --------------------------------------------------------- deltas: cmd_undo


def test_undo_without_id_prefers_older_completed_move_over_newer_undo(
        ready, tmp_path, write_transcript, write_row, capsys):
    """delta 2: cmd_undo's candidate filter must be completed ops whose
    op_type is 'move' or 'sync' (or missing) - not just any 'completed' op.

    Sequence: move session B, then move session A (SID), then undo A. A's
    own move op is now 'undone' (terminal, not 'completed'); the newest
    'completed' op overall is A's *undo* op. A bare `undo --apply` must
    still reach B's older, still-undoable completed move - not attempt an
    undo-of-undo on A's undo op, which plan_undo always refuses ('to redo,
    run move again')."""
    env, t, target = ready
    # mkenv freezes env.now() to a single constant, so consecutive ops get
    # the same history[0]["at"] and list_ops's "newest" ordering collapses
    # to comparing op_id's random suffix (see test_undo.py's identical
    # caveat) - not useful here, where the test needs a real, deterministic
    # creation order across three ops. A small monotonic clock fixes that.
    clock = itertools.count(1_800_000_000)
    env.now = lambda: next(clock)
    sid_b = "cli-sess-b"
    src_b = "C:\\proj\\srcb"
    target_b = str(tmp_path / "targetb")
    os.makedirs(target_b)
    t_b = write_transcript(env, ct.encode(src_b, ct.SCHEME_CURRENT), sid_b, [{"cwd": src_b}])
    write_row(env, 0, "o", "a", "local_b",
              {"sessionId": "local_b", "cliSessionId": sid_b, "cwd": src_b,
               "originCwd": src_b, "title": "session B"})

    assert ct.main(["move", sid_b, target_b, "--apply"]) == 0   # oldest completed move
    assert ct.main(["move", SID, target, "--apply"]) == 0       # newest completed move
    assert ct.main(["undo", "--apply"]) == 0                    # undoes A (SID): newest
    assert os.path.isfile(t)                                    # A's transcript is back home

    move_status = {o.manifest["session_id"]: o.manifest["status"]
                   for o in ct.list_ops(env) if o.manifest.get("op_type") == "move"}
    assert move_status[SID] == "undone"
    assert move_status[sid_b] == "completed"

    # newest COMPLETED op overall is now A's undo op; the filter must skip
    # it and land on B's still-completed move instead.
    assert ct.main(["undo", "--apply"]) == 0
    assert os.path.isfile(t_b)


def test_undo_list_shows_journal(ready, capsys):
    env, t, target = ready
    assert ct.main(["move", SID, target, "--apply"]) == 0
    assert ct.main(["undo", "--list"]) == 0
    out = capsys.readouterr().out
    assert "completed" in out
