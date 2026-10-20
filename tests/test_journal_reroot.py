"""Journaled paths after the Desktop data-root move.

Desktop 2.42304.2 moved the app's user data from
%LOCALAPPDATA%\\Packages\\Claude_<pkg>\\LocalCache\\Roaming\\Claude\\ to
%LOCALAPPDATA%\\Claude-Data\\claude_<pkg>\\. Every operation journaled before
the move records its rows by absolute path under the old root. The maintainer's
rulings of 2026-10-19 (docs/internals.md, "Amendment 2026-10-19 (journal)"):

1. re-root at read time - list_ops maps a recorded path under an old store to
   the same relative path under that package's moved store, the pairs coming
   from the Claude-Data listing alone;
2. the mapping lives in memory - save_manifest writes the recorded strings
   back, and a fresh load maps again;
3. a journaled row is acted on, or read as absent, only when it lies inside a
   discovered root - otherwise it is unresolved: undo refuses, recover --back
   closes with residue, doctor lists it without counting it, and a discovery
   error refuses undo and recover outright;
5. undo of a move checks containment and reads every row before anything is
   journaled, and says which of "missing" and "unreadable" it met;
7. sync's undo says what it reversed, and the window follows it.

Each test builds the packaged layout under tmp_path, runs a real operation
through the module, moves the data folder the way the app did (leaving its
0-byte marker), rebuilds the Env, and pins what the journal consumer does.
"""
import ast
import hashlib
import json
import os
import shutil

import pytest

import claude_code_sessions as ct
import claude_code_sessions_gui as gui
from test_converge import (A1, O1, A2, O2, A3, O3, S1, S2, S3,  # noqa: F401
                           SimulatedCrash, _crash_after_first_row,
                           _monotonic_now, mixed_holdings, t_entries, undo_ns)
from test_sync import _prepared, _row

PKG = "Claude_pkgx"
SRC_ACCT = "aaaaaaaa-0000-0000-0000-000000000001"
SRC_ORG = "bbbbbbbb-0000-0000-0000-000000000002"
DST_ACCT = "cccccccc-0000-0000-0000-000000000003"
DST_ORG = "dddddddd-0000-0000-0000-000000000004"
UNREAD = "not one this version reads"


# ------------------------------------------------------------------ layout

def packaged(env, tmp_path):
    """The store where every pre-2.42304.2 Windows install kept it."""
    la = tmp_path / "LA"
    claude = la / "Packages" / PKG / "LocalCache" / "Roaming" / "Claude"
    (claude / "claude-code-sessions").mkdir(parents=True)
    env.store_candidates = [str(claude / "claude-code-sessions")]
    return str(la), str(claude)


def rebuild(env, la):
    env.store_candidates, env.superseded_stores, env.layout_errors = \
        ct._windows_store_layout(la, "")
    env.store_moves = ct._windows_store_moves(la)


def relocate(env, la, claude, copy=False, drop_old=False):
    """The observed move: the whole data folder goes to Claude-Data\\claude_<pkg>,
    the old folder keeps only the app's marker. copy=True leaves the old store
    in place as well; drop_old=True deletes the vacated folder too."""
    new = os.path.join(la, "Claude-Data", PKG.lower())
    os.makedirs(os.path.dirname(new), exist_ok=True)
    if copy:
        shutil.copytree(claude, new)
    else:
        shutil.move(claude, new)
        if not drop_old:
            os.makedirs(claude)
            open(os.path.join(claude, ".user-data-left." + PKG.lower()), "wb").close()
    rebuild(env, la)
    assert ct.discover_stores(env).status == "found"
    return os.path.join(new, "claude-code-sessions")


def strand(env, tmp_path):
    """A store this version no longer reads, with no pair to the one it does:
    LOCALAPPDATA now points somewhere else that holds an empty moved store.
    The journaled rows stay where they were - unresolved, and still on disk."""
    other = tmp_path / "LA2"
    (other / "Claude-Data" / "claude_other" / "claude-code-sessions").mkdir(parents=True)
    rebuild(env, str(other))
    assert ct.discover_stores(env).status == "found"


def at(path, old_store, new_store):
    return os.path.join(new_store, os.path.relpath(path, old_store))


def signed_in(env, acct=A1, org=O1):
    with open(os.path.join(env.home, ".claude.json"), "w", encoding="utf-8") as fh:
        json.dump({"oauthAccount": {"accountUuid": acct, "organizationUuid": org,
                                    "emailAddress": "alice@example.com"}}, fh)


def journal_hashes(env):
    out = {}
    for d, _, files in os.walk(env.ops_dir):
        for f in files:
            p = os.path.join(d, f)
            with open(p, "rb") as fh:
                out[p] = hashlib.sha256(fh.read()).hexdigest()
    return out


def on_disk(op):
    with open(ct.manifest_path(op), encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------- operations

def converged(mkenv, tmp_path, write_transcript, write_row):
    env = _monotonic_now(mkenv(tmp_path))
    la, claude = packaged(env, tmp_path)
    mixed_holdings(env, write_transcript, write_row)
    assert ct.run_converge(env, ct.plan_converge(env, ct.ConvergeFlags())) == "completed"
    return env, la, claude, env.store_candidates[0], ct.list_ops(env)[-1]


def new_rowed(mkenv, tmp_path, write_transcript, write_row):
    env = _monotonic_now(mkenv(tmp_path))
    la, claude = packaged(env, tmp_path)
    write_transcript(env, "C--p", S3, t_entries(cwd="C:\\p"))
    write_row(env, 0, A1, O1, "local_p",
              {"sessionId": "local_p", "cliSessionId": "other-conversation",
               "title": "Padding", "lastActivityAt": 9})
    signed_in(env)
    assert ct.run_new_row(env, ct.plan_new_row(env, ct.NewRowFlags(to_session=S3))) \
        == "completed"
    return env, la, claude, env.store_candidates[0], ct.list_ops(env)[-1]


def repointed(mkenv, tmp_path, write_transcript, write_row):
    env = _monotonic_now(mkenv(tmp_path))
    la, claude = packaged(env, tmp_path)
    write_transcript(env, "C--p", S3, t_entries(cwd="C:\\p"))
    write_row(env, 0, A1, O1, "local_p",
              {"sessionId": "local_p", "cliSessionId": "other-conversation",
               "title": "Padding", "lastActivityAt": 9})
    signed_in(env)
    m = ct.plan_repoint(env, ct.RepointFlags(only="Padding", to_session=S3))
    assert ct.run_repoint(env, m) == "completed"
    return env, la, claude, env.store_candidates[0], ct.list_ops(env)[-1]


def retitled(mkenv, tmp_path, write_transcript, write_row, store=""):
    env = _monotonic_now(mkenv(tmp_path))
    la, claude = packaged(env, tmp_path)
    for acct, org in ((A1, O1), (A2, O2)):
        write_row(env, 0, acct, org, "local_1",
                  {"sessionId": "local_1", "cliSessionId": "s-1", "cwd": "C:\\p",
                   "title": "Quarterly board report", "lastActivityAt": 9})
    signed_in(env)
    m = ct.plan_retitle(env, ct.RetitleFlags(only="s-1", title="Renamed", store=store))
    assert ct.run_retitle(env, m) == "completed"
    return env, la, claude, env.store_candidates[0], ct.list_ops(env)[-1]


def synced(mkenv, tmp_path, update=False):
    env = _monotonic_now(mkenv(tmp_path))
    la, claude = packaged(env, tmp_path)
    root = env.store_candidates[0]
    src = os.path.join(root, SRC_ACCT, SRC_ORG)
    dst = os.path.join(root, DST_ACCT, DST_ORG)
    os.makedirs(src)
    os.makedirs(dst)
    signed_in(env, SRC_ACCT, SRC_ORG)
    _prepared(env, src, dst, n=2)
    if update:
        _row(dst, "local_0.json", "sid-0", "Session 0", {"lastActivityAt": 0})
    assert ct.run_sync(env, ct.plan_sync(env, ct.SyncFlags(update=update))) == "completed"
    return env, la, claude, root, ct.list_ops(env)[-1]


SID = "undo-sess"
SRC_CWD = "C:\\proj\\src"


def moved(mkenv, tmp_path, write_transcript, write_row, crash_at=None):
    env = mkenv(tmp_path)
    la, claude = packaged(env, tmp_path)
    target = str(tmp_path / "target")
    os.makedirs(target)
    t = write_transcript(env, ct.encode(SRC_CWD, ct.SCHEME_CURRENT), SID, [{"cwd": SRC_CWD}])
    write_row(env, 0, "org", "acct", "local_r1",
              {"sessionId": "local_r1", "cliSessionId": SID, "cwd": SRC_CWD,
               "originCwd": SRC_CWD})
    os.makedirs(os.path.join(env.projects_root, ct.encode("C:\\proj\\_ev", ct.SCHEME_CURRENT)))
    write_row(env, 0, "org", "acct", "local_ev",
              {"sessionId": "local_ev", "cliSessionId": "other", "cwd": "C:\\proj\\_ev",
               "lastActivityAt": 2})
    m = ct.plan_move(env, SID, target, ct.MoveFlags())
    if crash_at:
        def hook(p):
            if p == crash_at:
                raise SimulatedCrash()
        ct._crash_hook = hook
        try:
            with pytest.raises(SimulatedCrash):
                ct.run_move(env, m)
        finally:
            ct._crash_hook = None
        ct.release_lock(env)
        return env, la, claude, env.store_candidates[0], ct.nonterminal_ops(env)[0], t, m
    assert ct.run_move(env, m) == "completed"
    return env, la, claude, env.store_candidates[0], ct.list_ops(env)[0], t, m


# ---------------------------------------------------------- 1. the pairs

def test_pairs_come_from_the_moved_roots_alone(tmp_path):
    la = tmp_path / "LA"
    (la / "Claude-Data" / "claude_pkgx").mkdir(parents=True)
    (la / "Claude-Data" / "claude_file").write_text("not a folder")
    (la / "Claude-Data" / "other").mkdir()
    assert not (la / "Packages").exists()
    assert ct._windows_store_moves(str(la)) == [(
        os.path.join(str(la), "Packages", "Claude_pkgx", "LocalCache", "Roaming",
                     "Claude", "claude-code-sessions"),
        os.path.join(str(la), "Claude-Data", "claude_pkgx", "claude-code-sessions"))]
    assert ct._windows_store_moves("relative") == []
    assert ct._windows_store_moves("") == []


def test_default_env_carries_the_pairs_and_none_on_a_layout_error(
        tmp_path, monkeypatch):
    la = tmp_path / "LA"
    (la / "Claude-Data" / "claude_pkgx" / "claude-code-sessions").mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(la))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setattr(ct.sys, "platform", "win32")
    assert ct.default_env().store_moves == ct._windows_store_moves(str(la))
    real = os.listdir

    def denied(p):
        if os.path.basename(str(p)) == "Claude-Data":
            raise PermissionError(13, "denied")
        return real(p)
    monkeypatch.setattr(ct.os, "listdir", denied)
    env = ct.default_env()
    assert env.layout_errors and env.store_moves == []


def test_default_env_off_windows_has_no_pairs(monkeypatch):
    monkeypatch.setattr(ct.sys, "platform", "linux")
    assert ct.default_env().store_moves == []


# ------------------------------------------------- 1. what list_ops maps

def test_converge_rows_map_and_its_destinations_do_not(
        mkenv, tmp_path, write_transcript, write_row):
    env, la, claude, old, op = converged(mkenv, tmp_path, write_transcript, write_row)
    dests = [d.get("path") for d in op.manifest.get("destinations") or []]
    new = relocate(env, la, claude)
    m = ct.list_ops(env)[-1].manifest
    for r, before in zip(m["rows"], op.manifest["rows"]):
        assert r["dest_path"] == at(before["dest_path"], old, new)
        assert r["store_path"] == at(before["store_path"], old, new)
    assert [d.get("path") for d in m.get("destinations") or []] == dests


def test_new_row_and_repoint_keys_map_and_row_path_does_not(
        mkenv, tmp_path, write_transcript, write_row):
    for build in (new_rowed, repointed):
        env, la, claude, old, op = build(mkenv, tmp_path / build.__name__,
                                         write_transcript, write_row)
        before = json.loads(json.dumps(op.manifest))
        new = relocate(env, la, claude)
        m = ct.list_ops(env)[-1].manifest
        assert m["store_path"] == at(before["store_path"], old, new)
        assert m["rows"][0]["dest_path"] == at(before["rows"][0]["dest_path"], old, new)
        assert m["row_path"] == before["row_path"]


def test_sync_keys_map_and_its_source_and_audit_paths_do_not(mkenv, tmp_path):
    env, la, claude, old, op = synced(mkenv, tmp_path)
    # A --live sync records the config.json it arbitrated against, beside the
    # store rather than in it: audit data nothing validates, left as recorded.
    op.manifest["live_override"] = {"config_path": os.path.join(claude, "config.json")}
    ct.save_manifest(op)
    before = json.loads(json.dumps(op.manifest))
    new = relocate(env, la, claude)
    m = ct.list_ops(env)[-1].manifest
    assert m["dest_path"] == at(before["dest_path"], old, new)
    assert [r["dest_path"] for r in m["rows"]] == \
        [at(r["dest_path"], old, new) for r in before["rows"]]
    assert m["source_path"] == before["source_path"]
    assert m["live_override"]["config_path"] == before["live_override"]["config_path"]


def test_a_retitle_scoped_with_store_maps_its_manifest_store_path(
        mkenv, tmp_path, write_transcript, write_row):
    env, la, claude, old, op = retitled(mkenv, tmp_path, write_transcript, write_row,
                                        store="aaaaaaaa")
    assert op.manifest["store_path"]
    before = json.loads(json.dumps(op.manifest))
    new = relocate(env, la, claude)
    m = ct.list_ops(env)[-1].manifest
    assert m["store_path"] == at(before["store_path"], old, new)
    for r, b in zip(m["rows"], before["rows"]):
        assert (r["dest_path"], r["store_path"]) == \
            (at(b["dest_path"], old, new), at(b["store_path"], old, new))


def test_a_move_rows_path_maps(mkenv, tmp_path, write_transcript, write_row):
    env, la, claude, old, op, t, m = moved(mkenv, tmp_path, write_transcript, write_row)
    before = [r["path"] for r in op.manifest["rows"]]
    new = relocate(env, la, claude)
    assert [r["path"] for r in ct.list_ops(env)[0].manifest["rows"]] == \
        [at(p, old, new) for p in before]


def test_a_sibling_folder_of_the_old_store_is_not_mapped(
        mkenv, tmp_path, write_transcript, write_row):
    env, la, claude, old, op = new_rowed(mkenv, tmp_path, write_transcript, write_row)
    sibling = old + "-bak"
    op.manifest["store_path"] = os.path.join(sibling, A1, O1)
    ct.save_manifest(op)
    relocate(env, la, claude)
    assert ct.list_ops(env)[-1].manifest["store_path"] == os.path.join(sibling, A1, O1)


# --------------------------------------------- 2. nothing written back

def test_a_save_writes_the_recorded_paths_back_and_keeps_the_mapped_ones(
        mkenv, tmp_path, write_transcript, write_row):
    env, la, claude, old, op = converged(mkenv, tmp_path, write_transcript, write_row)
    recorded = [r["dest_path"] for r in op.manifest["rows"]]
    new = relocate(env, la, claude)
    loaded = ct.list_ops(env)[-1]
    assert loaded.rerooted
    ct.set_status(loaded, "completed")
    assert [r["dest_path"] for r in on_disk(loaded)["rows"]] == recorded
    assert [r["dest_path"] for r in loaded.manifest["rows"]] == \
        [at(p, old, new) for p in recorded]


def test_doctor_leaves_the_journal_byte_identical(
        mkenv, tmp_path, write_transcript, write_row):
    env, la, claude, old, op = converged(mkenv, tmp_path, write_transcript, write_row)
    relocate(env, la, claude)
    before = journal_hashes(env)
    ct.gather_doctor(env)
    assert journal_hashes(env) == before


# ------------------------------------------- doctor on a moved journal

def test_doctor_reports_no_vanished_row_and_counts_the_operation(
        mkenv, tmp_path, write_transcript, write_row):
    env, la, claude, old, op = converged(mkenv, tmp_path, write_transcript, write_row)
    relocate(env, la, claude)
    rep = ct.gather_doctor(env)
    assert rep["vanished_new_rows"] == []
    assert rep["unresolved_rows"] == []
    assert rep["rerooted_ops"] == 1
    assert rep["exit_code"] == 0


def test_doctor_still_reports_a_row_absent_at_its_moved_path(
        mkenv, tmp_path, write_transcript, write_row):
    env, la, claude, old, op = converged(mkenv, tmp_path, write_transcript, write_row)
    new = relocate(env, la, claude)
    victim = [r for r in op.manifest["rows"] if r["session"] == S2][0]
    os.unlink(at(victim["dest_path"], old, new))
    rep = ct.gather_doctor(env)
    assert [v["to_session"] for v in rep["vanished_new_rows"]] == [S2]
    assert rep["exit_code"] == 1


def test_the_pairs_hold_after_the_vacated_folder_is_deleted(
        mkenv, tmp_path, write_transcript, write_row):
    env, la, claude, old, op = converged(mkenv, tmp_path, write_transcript, write_row)
    relocate(env, la, claude, drop_old=True)
    assert env.superseded_stores == []          # the layout no longer names it
    assert ct.gather_doctor(env)["vanished_new_rows"] == []


def test_doctor_prints_the_advisory_line(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    env, la, claude, old, op = converged(mkenv, tmp_path, write_transcript, write_row)
    relocate(env, la, claude)
    ns = undo_ns()
    ns.json = False
    ct.cmd_doctor(env, ns)
    assert "1 operation(s) recorded before the data moved" in capsys.readouterr().out


# ------------------------------------------- undo of a pre-move operation

def test_converge_undo_removes_the_rows_from_the_moved_store(
        mkenv, tmp_path, write_transcript, write_row):
    env, la, claude, old, op = converged(mkenv, tmp_path, write_transcript, write_row)
    new = relocate(env, la, claude)
    written = [at(r["dest_path"], old, new) for r in op.manifest["rows"] if r["written"]]
    assert all(os.path.exists(p) for p in written)
    assert ct.undo_converge(env, ct.list_ops(env)[-1]) == "undone"
    assert not any(os.path.exists(p) for p in written)


def test_new_row_undo_removes_the_row_from_the_moved_store(
        mkenv, tmp_path, write_transcript, write_row):
    env, la, claude, old, op = new_rowed(mkenv, tmp_path, write_transcript, write_row)
    new = relocate(env, la, claude)
    row = at(op.manifest["rows"][0]["dest_path"], old, new)
    assert ct.undo_new_row(env, ct.list_ops(env)[-1]) == "undone"
    assert not os.path.exists(row)


def test_new_row_undo_still_refuses_a_row_the_app_rewrote(
        mkenv, tmp_path, write_transcript, write_row):
    env, la, claude, old, op = new_rowed(mkenv, tmp_path, write_transcript, write_row)
    new = relocate(env, la, claude)
    row = at(op.manifest["rows"][0]["dest_path"], old, new)
    with open(row, "w", encoding="utf-8") as fh:
        fh.write('{"cliSessionId": "%s", "title": "rewritten by the app"}' % S3)
    with pytest.raises(ct.Refusal, match="changed it since"):
        ct.undo_new_row(env, ct.list_ops(env)[-1])
    assert os.path.exists(row)


def test_move_undo_restores_the_row_and_journals_the_moved_paths(
        mkenv, tmp_path, write_transcript, write_row):
    env, la, claude, old, op, t, m = moved(mkenv, tmp_path, write_transcript, write_row)
    new = relocate(env, la, claude)
    prior = ct.list_ops(env)[0]
    assert ct.run_undo(env, prior) == "completed"
    assert os.path.isfile(t)
    row = at(op.manifest["rows"][0]["path"], old, new)
    with open(row, encoding="utf-8") as fh:
        assert json.load(fh)["cwd"] == SRC_CWD
    undo_op = [o for o in ct.list_ops(env) if o.manifest.get("op_type") == "undo"][0]
    assert on_disk(undo_op)["rows"][0]["path"] == row     # where it acted
    assert on_disk(prior)["rows"][0]["path"] == op.manifest["rows"][0]["path"]


def test_sync_undo_removes_the_rows_and_says_so(mkenv, tmp_path):
    env, la, claude, old, op = synced(mkenv, tmp_path)
    new = relocate(env, la, claude)
    rows = [at(r["dest_path"], old, new) for r in op.manifest["rows"]]
    loaded = ct.list_ops(env)[-1]
    assert ct.undo_sync(env, loaded) == "undone"
    assert not any(os.path.exists(p) for p in rows)
    assert loaded.manifest["undo_report"] == {
        "removed": 2, "restored": 0, "already_gone": 0, "not_restored": 0}


def test_sync_update_undo_puts_the_pre_image_back_at_the_moved_path(mkenv, tmp_path):
    env, la, claude, old, op = synced(mkenv, tmp_path, update=True)
    refreshed = [r for r in op.manifest["rows"] if r.get("is_update")][0]
    new = relocate(env, la, claude)
    row = at(refreshed["dest_path"], old, new)
    assert ct.undo_sync(env, ct.list_ops(env)[-1]) == "undone"
    with open(row, "rb") as fh:
        assert fh.read() == ct._sync_pre_image(refreshed)


def test_sync_undo_refuses_once_the_destination_is_the_live_account(mkenv, tmp_path):
    env, la, claude, old, op = synced(mkenv, tmp_path)
    relocate(env, la, claude)
    signed_in(env, DST_ACCT, DST_ORG)
    with pytest.raises(ct.Refusal, match="LIVE"):
        ct.undo_sync(env, ct.list_ops(env)[-1])


def test_repoint_undo_restores_the_pointer(mkenv, tmp_path, write_transcript, write_row):
    env, la, claude, old, op = repointed(mkenv, tmp_path, write_transcript, write_row)
    new = relocate(env, la, claude)
    row = at(op.manifest["rows"][0]["dest_path"], old, new)
    assert ct.undo_repoint(env, ct.list_ops(env)[-1]) == "undone"
    with open(row, encoding="utf-8") as fh:
        assert json.load(fh)["cliSessionId"] == "other-conversation"


def test_retitle_undo_restores_the_title(mkenv, tmp_path, write_transcript, write_row):
    env, la, claude, old, op = retitled(mkenv, tmp_path, write_transcript, write_row)
    new = relocate(env, la, claude)
    assert ct.undo_retitle(env, ct.list_ops(env)[-1]) == "undone"
    for r in op.manifest["rows"]:
        with open(at(r["dest_path"], old, new), encoding="utf-8") as fh:
            assert json.load(fh)["title"] == "Quarterly board report"


# ----------------------------------------- recover of a pre-move operation

def test_unfinished_converge_back_removes_the_row_it_wrote(
        mkenv, tmp_path, write_transcript, write_row):
    env = _monotonic_now(mkenv(tmp_path))
    la, claude = packaged(env, tmp_path)
    old = env.store_candidates[0]
    m, op = _crash_after_first_row(env, write_transcript, write_row)
    landed = [r for r in op.manifest["rows"] if r["written"]][0]
    new = relocate(env, la, claude)
    assert ct.recover_op(env, ct.nonterminal_ops(env)[0], "back") == "rolled_back"
    assert not os.path.exists(at(landed["dest_path"], old, new))


def test_unfinished_converge_forward_finishes_in_the_moved_store(
        mkenv, tmp_path, write_transcript, write_row):
    env = _monotonic_now(mkenv(tmp_path))
    la, claude = packaged(env, tmp_path)
    old = env.store_candidates[0]
    m, op = _crash_after_first_row(env, write_transcript, write_row)
    new = relocate(env, la, claude)
    assert ct.recover_op(env, ct.nonterminal_ops(env)[0], "forward") == "completed"
    for r in op.manifest["rows"]:
        assert os.path.exists(at(r["dest_path"], old, new))


def test_unfinished_new_row_back_removes_the_row(
        mkenv, tmp_path, write_transcript, write_row):
    env, la, claude, old, op = new_rowed(mkenv, tmp_path, write_transcript, write_row)
    ct.set_status(op, "writing")
    new = relocate(env, la, claude)
    assert ct.recover_op(env, ct.nonterminal_ops(env)[0], "back") == "rolled_back"
    assert not os.path.exists(at(op.manifest["rows"][0]["dest_path"], old, new))


def test_unfinished_sync_back_removes_the_rows(mkenv, tmp_path):
    env, la, claude, old, op = synced(mkenv, tmp_path)
    ct.set_status(op, "writing")
    new = relocate(env, la, claude)
    assert ct.recover_op(env, ct.nonterminal_ops(env)[0], "back") == "rolled_back"
    assert not any(os.path.exists(at(r["dest_path"], old, new))
                   for r in op.manifest["rows"])


def test_unfinished_repoint_back_restores_the_pointer_without_residue(
        mkenv, tmp_path, write_transcript, write_row):
    env, la, claude, old, op = repointed(mkenv, tmp_path, write_transcript, write_row)
    ct.set_status(op, "writing")
    new = relocate(env, la, claude)
    pending = ct.nonterminal_ops(env)[0]
    assert ct.recover_op(env, pending, "back") == "rolled_back"
    with open(at(op.manifest["rows"][0]["dest_path"], old, new), encoding="utf-8") as fh:
        assert json.load(fh)["cliSessionId"] == "other-conversation"
    assert "rollback_residue" not in pending.manifest
    assert ct.gather_doctor(env)["rollback_residue"] == []


# ------------------------------------------------- a store left behind

def test_a_leftover_old_store_is_never_touched(
        mkenv, tmp_path, write_transcript, write_row):
    env, la, claude, old, op = new_rowed(mkenv, tmp_path, write_transcript, write_row)
    new = relocate(env, la, claude, copy=True)
    recorded = op.manifest["rows"][0]["dest_path"]
    assert ct.undo_new_row(env, ct.list_ops(env)[-1]) == "undone"
    assert not os.path.exists(at(recorded, old, new))
    assert os.path.exists(recorded)              # the unread copy stays as it was


def test_a_leftover_old_store_strands_no_move_undo(
        mkenv, tmp_path, write_transcript, write_row):
    env, la, claude, old, op, t, m = moved(mkenv, tmp_path, write_transcript, write_row)
    relocate(env, la, claude, copy=True)
    assert ct.run_undo(env, ct.list_ops(env)[0]) == "completed"
    assert ct.nonterminal_ops(env) == []


# ---------------------------------------------- 3. the resolution gate

def test_doctor_lists_unresolved_rows_apart_and_does_not_count_them(
        mkenv, tmp_path, write_transcript, write_row):
    env, la, claude, old, op = converged(mkenv, tmp_path, write_transcript, write_row)
    strand(env, tmp_path)
    rep = ct.gather_doctor(env)
    written = [r for r in op.manifest["rows"] if r["written"]]
    assert rep["vanished_new_rows"] == []
    assert len(rep["unresolved_rows"]) == len(written)
    assert rep["exit_code"] == 0


def _stranded_undo_cases():
    def conv(mkenv, tmp_path, wt, wr):
        env, la, claude, old, op = converged(mkenv, tmp_path, wt, wr)
        return env, lambda o: ct.undo_converge(env, o), \
            [r["dest_path"] for r in op.manifest["rows"]]

    def nr(mkenv, tmp_path, wt, wr):
        env, la, claude, old, op = new_rowed(mkenv, tmp_path, wt, wr)
        return env, lambda o: ct.undo_new_row(env, o), [op.manifest["rows"][0]["dest_path"]]

    def rp(mkenv, tmp_path, wt, wr):
        env, la, claude, old, op = repointed(mkenv, tmp_path, wt, wr)
        return env, lambda o: ct.undo_repoint(env, o), [op.manifest["rows"][0]["dest_path"]]

    def rt(mkenv, tmp_path, wt, wr):
        env, la, claude, old, op = retitled(mkenv, tmp_path, wt, wr)
        return env, lambda o: ct.undo_retitle(env, o), \
            [r["dest_path"] for r in op.manifest["rows"]]

    def sy(mkenv, tmp_path, wt, wr):
        env, la, claude, old, op = synced(mkenv, tmp_path)
        return env, lambda o: ct.undo_sync(env, o), \
            [r["dest_path"] for r in op.manifest["rows"]]

    def mv(mkenv, tmp_path, wt, wr):
        env, la, claude, old, op, t, m = moved(mkenv, tmp_path, wt, wr)
        return env, lambda o: ct.run_undo(env, o), [op.manifest["rows"][0]["path"]]
    return {"converge": conv, "new-row": nr, "repoint": rp, "retitle": rt,
            "sync": sy, "move": mv}


@pytest.mark.parametrize("kind", sorted(_stranded_undo_cases()))
def test_undo_refuses_an_unresolved_row_and_changes_nothing(
        kind, mkenv, tmp_path, write_transcript, write_row):
    env, undo, paths = _stranded_undo_cases()[kind](mkenv, tmp_path,
                                                     write_transcript, write_row)
    before = {p: open(p, "rb").read() for p in paths if os.path.exists(p)}
    ops_before = len(ct.list_ops(env))
    strand(env, tmp_path)
    target = [o for o in ct.list_ops(env) if o.manifest.get("status") == "completed"][-1]
    with pytest.raises(ct.Refusal, match=UNREAD):
        undo(target)
    assert {p: open(p, "rb").read() for p in before} == before
    assert target.manifest["status"] == "completed"
    assert len(ct.list_ops(env)) == ops_before   # nothing journaled
    assert ct.read_lock(env) is None


def _stranded_back_cases():
    def nr(mkenv, tmp_path, wt, wr):
        env, la, claude, old, op = new_rowed(mkenv, tmp_path, wt, wr)
        return env, op
    def rp(mkenv, tmp_path, wt, wr):
        env, la, claude, old, op = repointed(mkenv, tmp_path, wt, wr)
        return env, op
    def rt(mkenv, tmp_path, wt, wr):
        env, la, claude, old, op = retitled(mkenv, tmp_path, wt, wr)
        return env, op
    def sy(mkenv, tmp_path, wt, wr):
        env, la, claude, old, op = synced(mkenv, tmp_path)
        return env, op
    def conv(mkenv, tmp_path, wt, wr):
        env, la, claude, old, op = converged(mkenv, tmp_path, wt, wr)
        return env, op
    return {"new-row": nr, "repoint": rp, "retitle": rt, "sync": sy, "converge": conv}


@pytest.mark.parametrize("kind", sorted(_stranded_back_cases()))
def test_back_closes_an_unresolved_operation_with_residue(
        kind, mkenv, tmp_path, write_transcript, write_row):
    env, op = _stranded_back_cases()[kind](mkenv, tmp_path, write_transcript, write_row)
    key = "path" if kind == "move" else "dest_path"
    files = {r[key]: open(r[key], "rb").read() for r in op.manifest["rows"]
             if os.path.exists(r[key])}
    ct.set_status(op, "writing")
    strand(env, tmp_path)
    pending = ct.nonterminal_ops(env)[0]
    c = ct.classify_op(env, pending)
    assert c["resolutions"] == ["back"]
    assert UNREAD in c["note"]
    assert ct.recover_op(env, pending, "back") == "rolled_back"
    assert UNREAD in pending.manifest["rollback_residue"]
    assert {p: open(p, "rb").read() for p in files} == files      # touched nothing
    rep = ct.gather_doctor(env)
    assert rep["rollback_residue"] == []                          # not counted
    assert any(u["op_id"] == pending.manifest["op_id"] for u in rep["unresolved_rows"])
    assert rep["exit_code"] == 0


def test_forward_refuses_an_unresolved_row_and_writes_nothing(
        mkenv, tmp_path, write_transcript, write_row):
    env = _monotonic_now(mkenv(tmp_path))
    packaged(env, tmp_path)
    m, op = _crash_after_first_row(env, write_transcript, write_row)
    pending_paths = [r["dest_path"] for r in op.manifest["rows"] if not r["written"]]
    strand(env, tmp_path)
    with pytest.raises(ct.Refusal, match=UNREAD):
        ct.recover_op(env, ct.nonterminal_ops(env)[0], "forward")
    assert not any(os.path.exists(p) for p in pending_paths)


def test_a_move_back_with_an_unresolved_row_keeps_both_copies_and_closes(
        mkenv, tmp_path, write_transcript, write_row):
    env, la, claude, old, op, t, m = moved(mkenv, tmp_path, write_transcript, write_row,
                                           crash_at="after-rewriting")
    row = op.manifest["rows"][0]["path"]
    with open(row, "rb") as fh:
        row_bytes = fh.read()
    strand(env, tmp_path)
    pending = ct.nonterminal_ops(env)[0]
    assert ct.classify_op(env, pending)["resolutions"] == ["back"]
    assert ct.recover_op(env, pending, "back") == "rolled_back"
    assert os.path.isfile(t) and os.path.isfile(m["dest_transcript"])
    with open(row, "rb") as fh:
        assert fh.read() == row_bytes                            # left alone
    assert UNREAD in pending.manifest["rollback_residue"]
    assert ct.nonterminal_ops(env) == []


def test_a_committed_move_with_an_unresolved_row_still_finishes(
        mkenv, tmp_path, write_transcript, write_row):
    env, la, claude, old, op, t, m = moved(mkenv, tmp_path, write_transcript, write_row,
                                           crash_at="after-committed")
    strand(env, tmp_path)
    pending = ct.nonterminal_ops(env)[0]
    assert ct.classify_op(env, pending)["resolutions"] == ["forward"]
    assert ct.recover_op(env, pending, "forward") == "completed"
    assert not os.path.exists(t) and os.path.isfile(m["dest_transcript"])


def _half_stranded(mkenv, tmp_path, write_transcript, write_row, idx):
    """A converge whose row IDX now sits in a store this version does not read
    (moved there by hand, its journal entry edited to match) while the others
    are where discovery finds them."""
    env, la, claude, old, op = converged(mkenv, tmp_path, write_transcript, write_row)
    rows = [r for r in op.manifest["rows"] if r["written"]]
    r = rows[idx]
    elsewhere = tmp_path / "elsewhere" / "claude-code-sessions"
    store = elsewhere / os.path.basename(os.path.dirname(r["store_path"])) / \
        os.path.basename(r["store_path"])
    store.mkdir(parents=True)
    dest = str(store / os.path.basename(r["dest_path"]))
    shutil.move(r["dest_path"], dest)
    r["dest_path"], r["store_path"] = dest, str(store)
    ct.save_manifest(op)
    return env, op, rows, dest


@pytest.mark.parametrize("idx", [0, -1])
def test_a_half_unresolved_undo_refuses_before_deleting_anything(
        idx, mkenv, tmp_path, write_transcript, write_row):
    env, op, rows, dest = _half_stranded(mkenv, tmp_path, write_transcript, write_row, idx)
    with pytest.raises(ct.Refusal, match=UNREAD):
        ct.undo_converge(env, ct.list_ops(env)[-1])
    assert all(os.path.exists(r["dest_path"]) for r in rows)


@pytest.mark.parametrize("idx", [0, -1])
def test_a_half_unresolved_back_reverses_the_rest_and_reports_the_one(
        idx, mkenv, tmp_path, write_transcript, write_row):
    env, op, rows, dest = _half_stranded(mkenv, tmp_path, write_transcript, write_row, idx)
    ct.set_status(op, "writing")
    assert ct.recover_op(env, ct.nonterminal_ops(env)[0], "back") == "rolled_back"
    assert [os.path.exists(r["dest_path"]) for r in rows] == \
        [r["dest_path"] == dest for r in rows]
    rep = ct.gather_doctor(env)                                    # a fresh load
    assert [u["path"] for u in rep["unresolved_rows"]] == [dest]


def test_a_discovery_error_refuses_undo_and_recover_and_changes_nothing(
        mkenv, tmp_path, write_transcript, write_row):
    env, la, claude, old, op = new_rowed(mkenv, tmp_path, write_transcript, write_row)
    row = op.manifest["rows"][0]["dest_path"]
    env.layout_errors = ["Claude-Data: denied - cannot tell which packages' data moved"]
    with pytest.raises(ct.LayoutError, match="denied"):
        ct.undo_new_row(env, ct.list_ops(env)[-1])
    ct.set_status(op, "writing")
    for direction in ("back", "forward"):
        with pytest.raises(ct.LayoutError, match="denied"):
            ct.recover_op(env, ct.nonterminal_ops(env)[0], direction)
    assert os.path.exists(row)
    assert ct.nonterminal_ops(env)[0].manifest["status"] == "writing"
    c = ct.classify_op(env, ct.nonterminal_ops(env)[0])
    assert c["resolutions"] == [] and "denied" in c["note"]


# ------------------------------------------- doctor's residue reader

def test_a_residue_is_reported_while_a_later_named_row_remains(
        mkenv, tmp_path, write_transcript, write_row):
    env = _monotonic_now(mkenv(tmp_path))
    mixed_holdings(env, write_transcript, write_row)
    assert ct.run_converge(env, ct.plan_converge(env, ct.ConvergeFlags())) == "completed"
    op = ct.list_ops(env)[-1]
    rows = op.manifest["rows"]
    assert len(rows) == 3
    with open(rows[1]["dest_path"], "w", encoding="utf-8") as fh:
        fh.write('{"cliSessionId": "changed by the app"}')
    ct.set_status(op, "writing")
    assert ct.recover_op(env, op, "back") == "rolled_back"
    assert not os.path.exists(rows[0]["dest_path"])               # reversed
    assert os.path.exists(rows[1]["dest_path"])                   # left behind
    rep = ct.gather_doctor(env)
    assert [r["op_id"] for r in rep["rollback_residue"]] == [op.manifest["op_id"]]


def test_a_move_residue_is_read_by_its_path(
        mkenv, tmp_path, write_transcript, write_row):
    env, la, claude, old, op, t, m = moved(mkenv, tmp_path, write_transcript, write_row)
    op.manifest["rollback_residue"] = "left a row in the store"
    ct.save_manifest(op)
    assert len(ct.gather_doctor(env)["rollback_residue"]) == 1
    os.unlink(op.manifest["rows"][0]["path"])
    assert ct.gather_doctor(env)["rollback_residue"] == []


# ----------------------------------------- 5. undo of a move, cleanly

def test_move_undo_refuses_a_missing_row_without_journaling(
        mkenv, tmp_path, write_transcript, write_row):
    env, la, claude, old, op, t, m = moved(mkenv, tmp_path, write_transcript, write_row)
    os.unlink(op.manifest["rows"][0]["path"])
    with pytest.raises(ct.Refusal, match="missing"):
        ct.run_undo(env, op)
    assert len(ct.list_ops(env)) == 1 and ct.read_lock(env) is None


def test_move_undo_names_a_read_error_rather_than_calling_it_missing(
        mkenv, tmp_path, write_transcript, write_row, monkeypatch):
    env, la, claude, old, op, t, m = moved(mkenv, tmp_path, write_transcript, write_row)
    row = op.manifest["rows"][0]["path"]
    real = open

    def guarded(p, *a, **kw):
        if os.path.normcase(str(p)) == os.path.normcase(row):
            raise PermissionError(13, "Access is denied")
        return real(p, *a, **kw)
    monkeypatch.setattr(ct, "open", guarded, raising=False)
    with pytest.raises(ct.Refusal) as ei:
        ct.run_undo(env, op)
    assert "Access is denied" in str(ei.value) and "missing" not in str(ei.value)
    assert len(ct.list_ops(env)) == 1


# --------------------------------------------- 7. sync undo's tally

def test_sync_undo_counts_rows_already_gone_and_refreshes_not_restored(mkenv, tmp_path):
    env, la, claude, old, op = synced(mkenv, tmp_path, update=True)
    for r in op.manifest["rows"]:
        os.unlink(r["dest_path"])                        # deleted in the app since
    assert ct.undo_sync(env, op) == "undone"
    adds = sum(1 for r in op.manifest["rows"] if not r.get("is_update"))
    assert op.manifest["undo_report"] == {
        "removed": 0, "restored": 0, "already_gone": adds,
        "not_restored": len(op.manifest["rows"]) - adds}


def test_cmd_undo_prints_the_sync_tally(mkenv, tmp_path, capsys):
    env, la, claude, old, op = synced(mkenv, tmp_path)
    assert ct.cmd_undo(env, undo_ns(apply=True)) == 0
    assert "removed 2 row(s)" in capsys.readouterr().out


def test_the_window_line_follows_the_tally():
    status, note = gui._sync_undo_lines(
        {"removed": 2, "restored": 1, "already_gone": 0, "not_restored": 0})
    assert "removed 2" in note and "restored 1" in note
    status, note = gui._sync_undo_lines(
        {"removed": 0, "restored": 0, "already_gone": 2, "not_restored": 1})
    assert "the copied rows were removed" not in status + note
    assert "nothing" in (status + note).lower()


# ------------------------- every row reader goes through the gate

# Every function that reads a journaled row's file, or checks its
# containment, with how the gate reaches it. A new reader fails this test
# until someone decides which it is - the list is checked against the code,
# not against itself.
_GATED = "calls _row_resolved"
_ROW_READERS = {
    "_abort": _GATED,
    "_validate_manifest_paths": _GATED,
    "classify_op": _GATED,
    "recover_op": _GATED,
    "gather_doctor": _GATED,
    "undo_new_row": _GATED,
    "undo_repoint": _GATED,
    "undo_retitle": _GATED,
    "undo_converge": _GATED,
    "_sync_delete_targets": _GATED,
    "execute_repoint_op": _GATED,
    "plan_undo": _GATED,
    "_forward_rewrite_and_commit": "recover_op refuses forward on an unresolved row first",
    "execute_new_row_op": "reached only after _new_row_preflight or recover's forward gate",
    "_sync_write_rows": "execute_sync_op gates the destination store first",
    "_sync_drift_titles": "classify_op gates before dispatching to the sync classifier",
    "_retitle_contained": "itself a check against the discovered roots",
    "_retitle_write_rows": "checks each row with _retitle_contained first",
    "_converge_write_rows": "checks the discovered roots, after _converge_recheck",
    "execute_op": "runs _validate_manifest_paths before touching a row",
    "_row_state": "the classifier itself; every caller above is gated or listed",
    "_sync_row_drift": "the classifier itself; every caller above is gated or listed",
}


def test_every_row_reader_goes_through_the_gate():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "claude_code_sessions.py")
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    readers, gated = set(), set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        names = {n.func.id for n in ast.walk(node)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        opens_a_row = any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == "open" and n.args
            and isinstance(n.args[0], ast.Subscript)
            and isinstance(n.args[0].slice, ast.Constant)
            and n.args[0].slice.value in ("path", "dest_path")
            for n in ast.walk(node))
        if names & {"_sync_row_drift", "_row_state", "ensure_contained"} or opens_a_row:
            readers.add(node.name)
        if names & {"_row_resolved", "_unresolved_rows"}:
            gated.add(node.name)
    assert readers == set(_ROW_READERS)
    assert {f for f, how in _ROW_READERS.items() if how == _GATED} <= gated
