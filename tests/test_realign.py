"""`realign`: point every account's row at the folder holding the live copy.

Implements docs/specs/2026-11-16-realign-rows-design.md as the maintainer
ruled it (D1-D9, 2026-11-17): a row that opens a copy behind the live one, or
opens no copy, gets `cwd` (and `folderPlace.cwd` / `.realPath` where the row
has a `folderPlace`) rewritten to the live copy's folder; a row with a
worktree link is held; the stale copy moves into the operation's journal;
undo is per conversation and key-scoped. The probe behind it found that
`cwd` alone chooses the copy, that the app keeps `folderPlace` in step with
`cwd`, and that a row whose folder holds no copy has one copied in.

Every folder here is a real directory under tmp_path, because realign holds
a conversation whose target directory does not exist. Fake cast only:
ACME-REVIEW, Northwind, the A/O/S ids.
"""
import json
import os
import shutil
import types

import pytest

import claude_code_sessions as ct
from test_converge import A1, A2, A3, O1, O2, O3, S1, S2, SimulatedCrash, t_entries
from test_split_rows import cost_state, diverging_turn, later_turns

TITLE = "ACME-REVIEW handoff"


@pytest.fixture(autouse=True)
def _reset():
    ct._ANONYMIZE = False
    ct._ANON_CACHE.clear()
    ct._crash_hook = None
    yield
    ct._ANONYMIZE = False
    ct._ANON_CACHE.clear()
    ct._crash_hook = None


class World:
    """A store with real project directories under tmp_path."""

    def __init__(self, env, tmp_path, write_transcript, write_row):
        self.env, self.tmp = env, tmp_path
        self._wt, self._wr = write_transcript, write_row

    def place(self, name):
        path = self.tmp / "work" / name
        path.mkdir(parents=True, exist_ok=True)
        cwd = str(path)
        return cwd, ct.encode(cwd, ct.SCHEME_CURRENT)

    def copy(self, cwd, sid, entries):
        return self._wt(self.env, ct.encode(cwd, ct.SCHEME_CURRENT), sid, entries)

    def row(self, acct, org, local, sid, cwd, title=TITLE, activity=9, **extra):
        d = {"sessionId": local, "cliSessionId": sid, "title": title,
             "cwd": cwd, "originCwd": cwd, "lastActivityAt": activity,
             "isArchived": False}
        d.update(extra)
        return self._wr(self.env, 0, acct, org, local, d)

    def read(self, path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)


@pytest.fixture
def world(mkenv, tmp_path, write_transcript, write_row):
    return World(mkenv(tmp_path), tmp_path, write_transcript, write_row)


def behind_store(w):
    """S1: live copy in Northwind (A1's row), a strict-prefix copy in
    Northwind-wt (A2's row, with a folderPlace). Returns the pieces."""
    live_cwd, live_f = w.place("Northwind")
    old_cwd, old_f = w.place("Northwind-wt")
    live_p = w.copy(live_cwd, S1, t_entries(cwd=live_cwd) + later_turns())
    old_p = w.copy(old_cwd, S1, t_entries(cwd=live_cwd))
    r1 = w.row(A1, O1, "local_1", S1, live_cwd, activity=20)
    r2 = w.row(A2, O2, "local_1", S1, old_cwd, activity=10,
               folderPlace={"cwd": old_cwd, "realPath": old_cwd,
                            "origin": {"cwd": old_cwd}},
               lastFocusedAt=5)
    return types.SimpleNamespace(live_cwd=live_cwd, live_f=live_f, old_cwd=old_cwd,
                                 old_f=old_f, live_p=live_p, old_p=old_p,
                                 r1=r1, r2=r2)


def plan(w, **kw):
    return ct.plan_realign(w.env, ct.RealignFlags(**kw))


def ns(**kw):
    d = {"json": False, "verbose": False, "anonymize": False, "apply": False,
         "only": "", "include_identical": False, "live": ""}
    d.update(kw)
    return types.SimpleNamespace(**d)


def undo_ns(**kw):
    d = {"json": False, "verbose": False, "anonymize": False, "apply": True,
         "show": False, "op_id": None}
    d.update(kw)
    return types.SimpleNamespace(**d)


def held_reasons(m):
    return {h["session_id"]: h["reason"] for h in m["held"]}


# ------------------------------------------------------------------ planning

def test_a_behind_row_is_planned_onto_the_live_folder(world):
    s = behind_store(world)
    m = plan(world)
    (c,) = m["conversations"]
    assert c["session_id"] == S1 and c["folder"] == s.live_f
    assert c["cwd"] == s.live_cwd
    (r,) = m["rows"]
    assert r["dest_path"] == s.r2
    post = json.loads(ct.unb64(r["post_b64"]).decode("utf-8"))
    assert post["cwd"] == s.live_cwd
    assert post["folderPlace"]["cwd"] == s.live_cwd
    assert post["folderPlace"]["realPath"] == s.live_cwd
    # Every other key value-identical, folderPlace's other members kept.
    pre = world.read(s.r2)
    for k in pre:
        if k not in ("cwd", "folderPlace"):
            assert post[k] == pre[k], k
    assert post["folderPlace"]["origin"] == pre["folderPlace"]["origin"]
    assert post["originCwd"] == s.old_cwd
    (cp,) = m["copies"]
    assert cp["folder"] == s.old_f
    assert m["held"] == []


def test_folderplace_is_never_added(world):
    s = behind_store(world)
    world.row(A2, O2, "local_1", S1, s.old_cwd, activity=10)    # no folderPlace
    (r,) = plan(world)["rows"]
    post = json.loads(ct.unb64(r["post_b64"]).decode("utf-8"))
    assert "folderPlace" not in post and post["cwd"] == s.live_cwd


def test_a_row_already_opening_the_live_copy_is_left_alone(world):
    s = behind_store(world)
    m = plan(world)
    assert [r["dest_path"] for r in m["rows"]] == [s.r2]


def test_diverged_copies_are_held(world):
    a, _ = world.place("Northwind")
    b, _ = world.place("Northwind-wt")
    world.copy(a, S1, t_entries(cwd=a) + later_turns())
    world.copy(b, S1, t_entries(cwd=a) + diverging_turn())
    world.row(A1, O1, "local_1", S1, a)
    world.row(A2, O2, "local_1", S1, b)
    m = plan(world)
    assert m["conversations"] == [] and "diverged" in held_reasons(m)[S1]


def test_identical_copies_are_held_without_the_flag_and_target_the_newest_row_with_it(world):
    a, fa = world.place("Northwind")
    b, fb = world.place("Northwind-wt")
    world.copy(a, S1, t_entries(cwd=a))
    world.copy(b, S1, t_entries(cwd=a))
    world.row(A1, O1, "local_1", S1, a, activity=5)
    world.row(A2, O2, "local_1", S1, b, activity=50)
    assert "--include-identical" in held_reasons(plan(world))[S1]
    m = plan(world, include_identical=True)
    (c,) = m["conversations"]
    assert c["folder"] == fb and c["cwd"] == b


def test_a_recency_tie_between_folders_is_held(world):
    a, _ = world.place("Northwind")
    b, _ = world.place("Northwind-wt")
    world.copy(a, S1, t_entries(cwd=a))
    world.copy(b, S1, t_entries(cwd=a))
    world.row(A1, O1, "local_1", S1, a, activity=7)
    world.row(A2, O2, "local_1", S1, b, activity=7)
    assert "tie" in held_reasons(plan(world, include_identical=True))[S1]


def test_a_row_with_no_copy_is_planned_even_among_identical_copies(world):
    a, fa = world.place("Northwind")
    b, _ = world.place("Northwind-wt")
    none_cwd, _ = world.place("Elsewhere")
    world.copy(a, S1, t_entries(cwd=a))
    world.copy(b, S1, t_entries(cwd=a))
    world.row(A1, O1, "local_1", S1, a, activity=50)
    world.row(A2, O2, "local_1", S1, b, activity=5)
    r3 = world.row(A3, O3, "local_1", S1, none_cwd, activity=1)
    m = plan(world)
    (c,) = m["conversations"]
    assert c["folder"] == fa
    assert [r["dest_path"] for r in m["rows"]] == [r3]
    assert m["copies"] == []           # B is still opened by A2's row


def test_d9_a_single_copy_conversation_with_a_row_that_reaches_no_copy(world):
    a, fa = world.place("Northwind")
    none_cwd, _ = world.place("Elsewhere")
    world.copy(a, S1, t_entries(cwd=a))
    world.row(A1, O1, "local_1", S1, a)
    r2 = world.row(A2, O2, "local_1", S1, none_cwd)
    m = plan(world)
    (c,) = m["conversations"]
    assert c["folder"] == fa and [r["dest_path"] for r in m["rows"]] == [r2]


def test_d9_with_no_row_reaching_the_copy_takes_the_path_from_the_transcript(world):
    a, fa = world.place("Northwind")
    none_cwd, _ = world.place("Elsewhere")
    world.copy(a, S1, t_entries(cwd=a))
    world.row(A1, O1, "local_1", S1, none_cwd)
    (c,) = plan(world)["conversations"]
    assert c["cwd"] == a


def test_a_copy_no_row_opens_that_is_ahead_holds_the_conversation(world):
    s = behind_store(world)
    far, _ = world.place("Far")
    world.copy(far, S1, t_entries(cwd=s.live_cwd) + later_turns() + [
        {"type": "user", "message": {"content": "even later"}}])
    assert "no row opens" in held_reasons(plan(world))[S1]


def test_a_live_copy_reached_only_through_the_worktree_placement_is_held(world):
    main, fmain = world.place("Repo")
    wt = os.path.join(main, ".claude", "worktrees", "wt1")
    os.makedirs(wt)
    old, _ = world.place("Old")
    world.copy(wt, S1, t_entries(cwd=main) + later_turns())
    world.copy(old, S1, t_entries(cwd=main))
    world.row(A1, O1, "local_1", S1, main)          # reaches the wt copy by placement
    world.row(A2, O2, "local_1", S1, old)
    assert "worktree placement" in held_reasons(plan(world))[S1]


def test_a_row_with_a_worktree_link_is_held(world):
    s = behind_store(world)
    world.row(A2, O2, "local_1", S1, s.old_cwd, worktreePath=s.old_cwd,
              worktreeName="wt", branch="claude/wt")
    assert "worktree link" in held_reasons(plan(world))[S1]


def test_disagreeing_path_evidence_is_held(world):
    """Two real directories whose names encode to one folder: 'North wind'
    and 'North-wind'. Rows reaching the live copy through either spelling
    may mean different directories, so realign does not pick one."""
    spaced, f1 = world.place("North wind")
    hyphen, f2 = world.place("North-wind")
    assert f1 == f2
    old, _ = world.place("Old")
    world.copy(spaced, S1, t_entries(cwd=spaced) + later_turns())
    world.copy(old, S1, t_entries(cwd=spaced))
    world.row(A1, O1, "local_1", S1, spaced)
    world.row(A3, O3, "local_1", S1, hyphen)
    world.row(A2, O2, "local_1", S1, old)
    assert "disagree" in held_reasons(plan(world))[S1]


def test_a_target_directory_that_no_longer_exists_is_held(world):
    s = behind_store(world)
    shutil.rmtree(s.live_cwd)
    assert "no longer exists" in held_reasons(plan(world))[S1]


def test_only_resolves_one_conversation(world):
    behind_store(world)
    a, _ = world.place("Other")
    b, _ = world.place("Other-wt")
    world.copy(a, S2, t_entries(cwd=a) + later_turns())
    world.copy(b, S2, t_entries(cwd=a))
    world.row(A1, O1, "local_2", S2, a, title="Northwind intake")
    world.row(A2, O2, "local_2", S2, b, title="Northwind intake")
    m = plan(world, only="intake")
    assert [c["session_id"] for c in m["conversations"]] == [S2]
    assert plan(world, only=S1[:8])["conversations"][0]["session_id"] == S1
    with pytest.raises(ct.Refusal):
        plan(world, only="nothing-matches-this")
    with pytest.raises(ct.Refusal) as exc:
        plan(world, only="a")            # both titles hold an 'a'
    assert "2 conversations" in str(exc.value)


def test_nothing_split_plans_nothing(world):
    a, _ = world.place("Northwind")
    world.copy(a, S1, t_entries(cwd=a))
    world.row(A1, O1, "local_1", S1, a)
    m = plan(world)
    assert m["conversations"] == [] and m["held"] == [] and m["rows"] == []


# ------------------------------------------------------------------- apply

def test_apply_rewrites_the_row_and_parks_the_stale_copy_in_the_journal(world):
    s = behind_store(world)
    m = plan(world)
    assert ct.run_realign(world.env, m) == "completed"
    assert world.read(s.r2)["cwd"] == s.live_cwd
    assert not os.path.exists(s.old_p)
    op = [o for o in ct.list_ops(world.env) if o.manifest["op_type"] == "realign"][0]
    (cp,) = op.manifest["copies"]
    assert cp["moved"] and os.path.isfile(cp["aside"])
    assert cp["aside"].startswith(op.op_dir)
    rep = ct.gather_doctor(world.env)
    assert rep["split_rows"] == []
    assert rep["realign_held_copies"] and rep["exit_code"] == 0


def test_cmd_realign_dry_run_and_apply_exit_codes(world, capsys):
    behind_store(world)
    assert ct.cmd_realign(world.env, ns()) == 0
    out = capsys.readouterr().out
    assert "dry run" in out and TITLE in out
    assert ct.cmd_realign(world.env, ns(apply=True)) == 0


def test_apply_exits_3_when_a_conversation_is_held(world, capsys):
    behind_store(world)
    a, _ = world.place("Other")
    b, _ = world.place("Other-wt")
    world.copy(a, S2, t_entries(cwd=a) + later_turns())
    world.copy(b, S2, t_entries(cwd=a) + diverging_turn())
    world.row(A1, O1, "local_2", S2, a, title="Northwind intake")
    world.row(A2, O2, "local_2", S2, b, title="Northwind intake")
    assert ct.cmd_realign(world.env, ns()) == 0
    assert ct.cmd_realign(world.env, ns(apply=True)) == 3


def test_apply_refuses_while_the_app_runs(world):
    s = behind_store(world)
    world.env.process_lister = lambda: [(4242, "C:\\Program Files\\Claude\\claude.exe")]
    m = plan(world)
    with pytest.raises(ct.Refusal):
        ct.run_realign(world.env, m)
    assert world.read(s.r2)["cwd"] == s.old_cwd and os.path.exists(s.old_p)


def test_a_copy_that_grew_since_the_plan_holds_the_conversation(world):
    s = behind_store(world)
    m = plan(world)
    with open(s.live_p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "user", "message": {"content": "new"}}) + "\n")
    ct.run_realign(world.env, m)
    assert "changed since" in held_reasons(m)[S1]
    assert world.read(s.r2)["cwd"] == s.old_cwd and os.path.exists(s.old_p)


def test_apply_refuses_while_the_identity_files_disagree_without_live(world):
    behind_store(world)
    with open(os.path.join(world.env.home, ".claude.json"), "w") as fh:
        json.dump({"oauthAccount": {"accountUuid": A1}}, fh)
    cfg = os.path.join(os.path.dirname(world.env.store_candidates[0]), "config.json")
    with open(cfg, "w") as fh:
        json.dump({"lastKnownAccountUuid": A2}, fh)
    with pytest.raises(ct.Refusal) as exc:
        ct.run_realign(world.env, plan(world))
    assert "RULING 5" in str(exc.value)


def test_an_open_operation_on_the_rows_holds_the_conversation(world):
    s = behind_store(world)
    op = ct.new_op(world.env, {"op_type": "retitle", "cli_session_id": S1,
                               "new_title": "x",
                               "rows": [{"name": "local_1.json", "dest_path": s.r2,
                                         "store_path": os.path.dirname(s.r2),
                                         "pre_b64": "e30=", "post_b64": "e30="}]})
    assert op.manifest["status"] == "journaled"
    assert "unfinished operation" in held_reasons(plan(world))[S1]


# ---------------------------------------------------------------- recovery

def test_an_interrupted_apply_recovers_back(world):
    s = behind_store(world)
    m = plan(world)

    def hook(point):
        if point == "realign-after-rows":
            raise SimulatedCrash()
    ct._crash_hook = hook
    with pytest.raises(SimulatedCrash):
        ct.run_realign(world.env, m)
    ct._crash_hook = None
    ct.release_lock(world.env)
    (op,) = ct.nonterminal_ops(world.env)
    c = ct.classify_op(world.env, op)
    assert "back" in c["resolutions"] and "forward" in c["resolutions"]
    assert ct.recover_op(world.env, op, "back") == "rolled_back"
    assert world.read(s.r2)["cwd"] == s.old_cwd and os.path.exists(s.old_p)


def test_an_interrupted_apply_recovers_forward(world):
    s = behind_store(world)
    m = plan(world)

    def hook(point):
        if point == "realign-after-rows":
            raise SimulatedCrash()
    ct._crash_hook = hook
    with pytest.raises(SimulatedCrash):
        ct.run_realign(world.env, m)
    ct._crash_hook = None
    ct.release_lock(world.env)
    (op,) = ct.nonterminal_ops(world.env)
    assert ct.recover_op(world.env, op, "forward") == "completed"
    assert world.read(s.r2)["cwd"] == s.live_cwd and not os.path.exists(s.old_p)


# -------------------------------------------------------------------- undo

def applied(world):
    s = behind_store(world)
    ct.run_realign(world.env, plan(world))
    (op,) = [o for o in ct.list_ops(world.env) if o.manifest["op_type"] == "realign"]
    return s, op


def test_undo_straight_after_puts_rows_and_copy_back(world):
    s, op = applied(world)
    assert ct.undo_realign(world.env, op) == "undone"
    r = world.read(s.r2)
    assert r["cwd"] == s.old_cwd and r["folderPlace"]["cwd"] == s.old_cwd
    assert os.path.isfile(s.old_p)
    assert op.manifest["status"] == "undone"


def test_undo_keeps_the_apps_later_edits_to_other_keys(world):
    s, op = applied(world)
    d = world.read(s.r2)
    d["lastFocusedAt"] = 999
    with open(s.r2, "w") as fh:
        json.dump(d, fh)
    assert ct.undo_realign(world.env, op) == "undone"
    r = world.read(s.r2)
    assert r["lastFocusedAt"] == 999 and r["cwd"] == s.old_cwd


def test_undo_tolerates_bookkeeping_appended_to_the_live_copy(world):
    s, op = applied(world)
    with open(s.live_p, "a", encoding="utf-8") as fh:
        for rec in cost_state(2):
            fh.write(json.dumps(rec) + "\n")
    assert ct.undo_realign(world.env, op) == "undone"


def test_undo_refuses_when_the_conversation_gained_turns(world):
    s, op = applied(world)
    with open(s.live_p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "user", "message": {"content": "more"}}) + "\n")
    assert ct.undo_realign(world.env, op) == "partial"
    assert "gained" in op.manifest["undo_status"][S1]
    assert world.read(s.r2)["cwd"] == s.live_cwd
    assert op.manifest["status"] == "completed"


def test_undo_refuses_a_row_that_now_opens_another_conversation(world):
    s, op = applied(world)
    d = world.read(s.r2)
    d["cliSessionId"] = S2
    with open(s.r2, "w") as fh:
        json.dump(d, fh)
    assert ct.undo_realign(world.env, op) == "partial"
    assert "another conversation" in op.manifest["undo_status"][S1]


def test_undo_refuses_a_key_that_moved_since(world):
    s, op = applied(world)
    d = world.read(s.r2)
    d["cwd"] = s.old_cwd + "x"
    with open(s.r2, "w") as fh:
        json.dump(d, fh)
    assert ct.undo_realign(world.env, op) == "partial"
    assert "no longer holds" in op.manifest["undo_status"][S1]


def test_undo_is_per_conversation_and_retries_the_rest(world):
    s = behind_store(world)
    a, _ = world.place("Other")
    b, _ = world.place("Other-wt")
    pa = world.copy(a, S2, t_entries(cwd=a) + later_turns())
    world.copy(b, S2, t_entries(cwd=a))
    world.row(A1, O1, "local_2", S2, a, title="Northwind intake")
    r22 = world.row(A2, O2, "local_2", S2, b, title="Northwind intake")
    ct.run_realign(world.env, plan(world))
    (op,) = [o for o in ct.list_ops(world.env) if o.manifest["op_type"] == "realign"]
    with open(pa, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "user", "message": {"content": "more"}}) + "\n")
    assert ct.undo_realign(world.env, op) == "partial"
    assert op.manifest["undo_status"][S1] == "undone"
    assert world.read(s.r2)["cwd"] == s.old_cwd
    assert world.read(r22)["cwd"] == a
    # A retry skips the conversation already undone.
    assert ct.undo_realign(world.env, op) == "partial"
    assert op.manifest["undo_status"][S1] == "undone"


def test_a_later_operation_on_the_row_blocks_undo(world):
    s, op = applied(world)
    t0 = world.env.now()
    world.env.now = lambda: t0 + 5
    later = ct.new_op(world.env, {"op_type": "retitle", "cli_session_id": S1,
                                  "new_title": "x",
                                  "rows": [{"name": "local_1.json", "dest_path": s.r2,
                                            "store_path": os.path.dirname(s.r2),
                                            "pre_b64": "e30=", "post_b64": "e30=",
                                            "written": True}]})
    ct.set_status(later, "completed")
    assert ct.undo_realign(world.env, op) == "partial"
    assert later.manifest["op_id"] in op.manifest["undo_status"][S1]


def test_cmd_undo_picks_a_realign(world, capsys):
    s, op = applied(world)
    assert ct.cmd_undo(world.env, undo_ns(apply=False)) == 0
    assert "realign" in capsys.readouterr().out
    assert ct.cmd_undo(world.env, undo_ns()) == 0
    assert world.read(s.r2)["cwd"] == s.old_cwd


# ------------------------------------------------- journal, doctor, reports

def test_rotation_keeps_a_realign_op_that_holds_a_copy(world):
    s, op = applied(world)
    for i in range(12):
        o = ct.new_op(world.env, {"op_type": "retitle", "cli_session_id": "x%d" % i,
                                  "new_title": "t", "rows": []})
        ct.set_status(o, "completed")
    ct.rotate_ops(world.env)
    assert os.path.isdir(op.op_dir)


def test_doctor_lists_single_copy_conversations_a_row_cannot_reach(world, capsys):
    a, _ = world.place("Northwind")
    none_cwd, _ = world.place("Elsewhere")
    world.copy(a, S1, t_entries(cwd=a))
    world.row(A1, O1, "local_1", S1, a)
    world.row(A2, O2, "local_1", S1, none_cwd)
    rep = ct.gather_doctor(world.env)
    assert [w["session_id"] for w in rep["waiting_rows"]] == [S1]
    assert rep["split_rows"] == [] and rep["exit_code"] == 0
    ct.cmd_doctor(world.env, ns())
    assert "copies the transcript into its folder" in capsys.readouterr().out


def test_report_keys_are_structural(world):
    from test_anonymize import walk_fixed_keys
    s = behind_store(world)
    none_cwd, _ = world.place("Elsewhere")
    a, _ = world.place("Lonely")
    world.copy(a, S2, t_entries(cwd=a))
    world.row(A1, O1, "local_2", S2, none_cwd, title="Northwind intake")
    m = plan(world)
    assert walk_fixed_keys(ct._public_realign_manifest(world.env, m)) == []
    ct.run_realign(world.env, m)
    assert walk_fixed_keys(ct.gather_doctor(world.env)) == []


def test_anonymized_plan_hides_titles_and_folders(world, capsys):
    behind_store(world)
    ct._ANONYMIZE = True
    ct.cmd_realign(world.env, ns(json=True))
    js = capsys.readouterr().out
    ct.cmd_realign(world.env, ns())
    out = capsys.readouterr().out
    for text in (js, out):
        assert TITLE not in text and "Northwind" not in text
    json.loads(js)


def test_anonymized_holds_hide_a_path_only_a_transcript_carries(world, capsys):
    """D9 with no row reaching the copy: the path comes from the transcript,
    which no row carries, and the hold quotes it."""
    a, _ = world.place("Northwind")
    none_cwd, _ = world.place("Elsewhere")
    world.copy(a, S1, t_entries(cwd=a))
    world.row(A1, O1, "local_1", S1, none_cwd)
    shutil.rmtree(a)
    assert "no longer exists" in held_reasons(plan(world))[S1]
    ct._ANONYMIZE = True
    ct.cmd_realign(world.env, ns())
    assert "Northwind" not in capsys.readouterr().out


# ------------------------------------------- the design's further cases

def with_sidecar(path, files):
    """A sidecar folder beside transcript PATH holding FILES {rel: text}."""
    side = path[:-len(".jsonl")]
    for rel, text in files.items():
        p = os.path.join(side, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
    return side


def test_the_stale_copys_sidecar_moves_with_it_and_comes_back(world):
    s = behind_store(world)
    with_sidecar(s.live_p, {"subagents/a.jsonl": "a"})
    side = with_sidecar(s.old_p, {"subagents/a.jsonl": "a", "tool-results/t.txt": "t"})
    m = plan(world)
    (cp,) = m["copies"]
    assert cp["sidecar_only"] == ["tool-results/t.txt"]
    pub = ct._public_realign_manifest(world.env, m)
    assert pub["copies"][0]["sidecar_only"] == ["tool-results/t.txt"]
    assert ct.run_realign(world.env, m) == "completed"
    assert not os.path.exists(side)
    (op,) = [o for o in ct.list_ops(world.env) if o.manifest["op_type"] == "realign"]
    assert os.path.isfile(os.path.join(op.manifest["copies"][0]["aside"][:-6],
                                       "tool-results", "t.txt"))
    assert ct.undo_realign(world.env, op) == "undone"
    assert os.path.isfile(os.path.join(side, "tool-results", "t.txt"))


def test_a_sidecar_that_changed_since_the_plan_holds_the_conversation(world):
    s = behind_store(world)
    side = with_sidecar(s.old_p, {"subagents/a.jsonl": "a"})
    m = plan(world)
    with_sidecar(s.old_p, {"subagents/b.jsonl": "b"})
    assert ct.run_realign(world.env, m) == "nothing to write"
    assert "changed since" in held_reasons(m)[S1]
    assert os.path.isdir(side) and world.read(s.r2)["cwd"] == s.old_cwd


def crash_at(world, point, m):
    def hook(p):
        if p == point:
            raise SimulatedCrash()
    ct._crash_hook = hook
    with pytest.raises(SimulatedCrash):
        ct.run_realign(world.env, m)
    ct._crash_hook = None
    ct.release_lock(world.env)
    (op,) = ct.nonterminal_ops(world.env)
    return op


@pytest.mark.parametrize("direction", ["forward", "back"])
def test_an_interruption_mid_move_recovers_both_ways(world, direction):
    s = behind_store(world)
    side = with_sidecar(s.old_p, {"subagents/a.jsonl": "a"})
    op = crash_at(world, "realign-mid-move", plan(world))
    # The transcript is in the journal, its sidecar is not yet.
    assert not os.path.exists(s.old_p) and os.path.isdir(side)
    c = ct.classify_op(world.env, op)
    assert set(c["resolutions"]) == {"forward", "back"}
    final = ct.recover_op(world.env, op, direction)
    if direction == "forward":
        assert final == "completed"
        assert not os.path.exists(s.old_p) and not os.path.exists(side)
        assert world.read(s.r2)["cwd"] == s.live_cwd
        assert ct.list_ops(world.env)[0].manifest["copies"][0]["moved"]
    else:
        assert final == "rolled_back"
        assert os.path.isfile(s.old_p) and os.path.isdir(side)
        assert world.read(s.r2)["cwd"] == s.old_cwd


def test_back_after_the_copy_moved_puts_it_back_before_the_rows(world):
    s = behind_store(world)
    m = plan(world)
    assert ct.run_realign(world.env, m) == "completed"
    (op,) = [o for o in ct.list_ops(world.env) if o.manifest["op_type"] == "realign"]
    # Pretend the completion marker was lost.
    ct.set_status(op, "writing")
    assert ct.recover_op(world.env, op, "back") == "rolled_back"
    assert os.path.isfile(s.old_p) and world.read(s.r2)["cwd"] == s.old_cwd


def test_converge_acts_on_the_conversation_after_realign(world):
    """D6: with the stale copy in the journal the conversation has one copy,
    so converge no longer holds it as a duplicated transcript."""
    s = behind_store(world)
    other, _ = world.place("Other")
    world.copy(other, S2, t_entries(cwd=other))
    world.row(A3, O3, "local_2", S2, other, title="Northwind intake")

    def held_s1():
        m = ct.plan_converge(world.env, ct.ConvergeFlags())
        return ([h["reason"] for h in m["holds"] if h.get("session") == S1],
                [r for r in m["rows"] if r["session"] == S1])
    reasons, rows = held_s1()
    assert reasons == ["held_transcript_unusable"] and rows == []
    ct.run_realign(world.env, plan(world))
    reasons, rows = held_s1()
    assert reasons == [] and len(rows) == 1


def test_alignment_drops_the_conversation_after_realign(world):
    behind_store(world)
    assert ct.gather_alignment(world.env)["consistent"]["split"]["conversations"] == 1
    ct.run_realign(world.env, plan(world))
    assert ct.gather_alignment(world.env)["consistent"]["split"]["conversations"] == 0


def test_a_row_that_changed_since_the_plan_holds_the_conversation(world):
    s = behind_store(world)
    m = plan(world)
    d = world.read(s.r2)
    d["lastFocusedAt"] = 77
    with open(s.r2, "w") as fh:
        json.dump(d, fh)
    assert ct.run_realign(world.env, m) == "nothing to write"
    assert "changed since" in held_reasons(m)[S1]


def test_a_new_copy_since_the_plan_holds_the_conversation(world):
    s = behind_store(world)
    m = plan(world)
    far, _ = world.place("Far")
    world.copy(far, S1, t_entries(cwd=s.live_cwd))
    assert ct.run_realign(world.env, m) == "nothing to write"
    assert "changed since" in held_reasons(m)[S1]


def test_mixed_copies_end_with_every_row_on_the_live_copy(world):
    """One copy behind and another holding the same conversation: every row
    ends on the live copy, both other copies go into the journal."""
    s = behind_store(world)
    twin, _ = world.place("Twin")
    world.copy(twin, S1, t_entries(cwd=s.live_cwd) + later_turns())
    r3 = world.row(A3, O3, "local_1", S1, twin, activity=5)
    m = plan(world)
    (c,) = m["conversations"]
    assert c["folder"] == s.live_f and c["decided_by"]["local_id"] == "local_1"
    assert sorted(r["dest_path"] for r in m["rows"]) == sorted([s.r2, r3])
    assert len(m["copies"]) == 2
    ct.run_realign(world.env, m)
    assert ct.gather_doctor(world.env)["split_rows"] == []


def test_a_copy_no_row_ever_opened_stays_where_it_is(world):
    """D6 sets aside the copy realign leaves unopened. A copy no row opened
    even before - converge's duplicated-transcript case - is not realign's
    to move, behind or not."""
    s = behind_store(world)
    far, far_f = world.place("Far")
    far_p = world.copy(far, S1, t_entries(cwd=s.live_cwd))
    m = plan(world)
    assert [c["folder"] for c in m["copies"]] == [s.old_f]
    ct.run_realign(world.env, m)
    assert os.path.isfile(far_p) and not os.path.exists(s.old_p)
    assert ct.gather_doctor(world.env)["split_rows"] == []


def test_d9_leaves_an_unopened_second_copy_alone(world):
    a, _ = world.place("Northwind")
    b, _ = world.place("Northwind-wt")
    none_cwd, _ = world.place("Elsewhere")
    world.copy(a, S1, t_entries(cwd=a) + later_turns())
    pb = world.copy(b, S1, t_entries(cwd=a))
    world.row(A1, O1, "local_1", S1, a)
    r2 = world.row(A2, O2, "local_1", S1, none_cwd)
    m = plan(world)
    assert [r["dest_path"] for r in m["rows"]] == [r2] and m["copies"] == []
    ct.run_realign(world.env, m)
    assert os.path.isfile(pb)


def test_undo_refuses_when_the_old_folder_is_gone(world):
    s, op = applied(world)
    shutil.rmtree(s.old_cwd)
    assert ct.undo_realign(world.env, op) == "partial"
    assert "no longer exists" in op.manifest["undo_status"][S1]


def test_undo_refuses_when_a_copy_is_back_in_the_old_folder(world):
    s, op = applied(world)
    world.copy(s.old_cwd, S1, t_entries(cwd=s.live_cwd))
    assert ct.undo_realign(world.env, op) == "partial"
    assert "back in" in op.manifest["undo_status"][S1]


def test_identical_copies_with_the_flag_move_every_other_row_and_copy(world):
    """With --include-identical every row not on the live copy moves, so no
    row is left opening the other copy and it goes into the journal; undo
    puts both rows and the copy back."""
    a, _ = world.place("Northwind")
    b, _ = world.place("Northwind-wt")
    world.copy(a, S1, t_entries(cwd=a))
    pb = world.copy(b, S1, t_entries(cwd=a))
    world.row(A1, O1, "local_1", S1, a, activity=50)
    rb = world.row(A2, O2, "local_1", S1, b, activity=5)
    rc = world.row(A3, O3, "local_1", S1, b, activity=4)
    m = plan(world, include_identical=True)
    assert sorted(r["dest_path"] for r in m["rows"]) == sorted([rb, rc])
    assert [c["folder"] for c in m["copies"]] == [ct.encode(b, ct.SCHEME_CURRENT)]
    ct.run_realign(world.env, m)
    (op,) = [o for o in ct.list_ops(world.env) if o.manifest["op_type"] == "realign"]
    assert world.read(rb)["cwd"] == a and not os.path.exists(pb)
    assert ct.undo_realign(world.env, op) == "undone"
    assert world.read(rc)["cwd"] == b and os.path.isfile(pb)


def test_undo_refuses_a_no_copy_rows_folder_that_now_holds_one(world):
    a, _ = world.place("Northwind")
    none_cwd, _ = world.place("Elsewhere")
    world.copy(a, S1, t_entries(cwd=a))
    world.row(A1, O1, "local_1", S1, a)
    world.row(A2, O2, "local_1", S1, none_cwd)
    ct.run_realign(world.env, plan(world))
    (op,) = [o for o in ct.list_ops(world.env) if o.manifest["op_type"] == "realign"]
    world.copy(none_cwd, S1, t_entries(cwd=a))
    assert ct.undo_realign(world.env, op) == "partial"
    assert "now holds a copy" in op.manifest["undo_status"][S1]


def test_an_interrupted_undo_resumes(world, monkeypatch):
    s = behind_store(world)
    ct.run_realign(world.env, plan(world))
    (op,) = [o for o in ct.list_ops(world.env) if o.manifest["op_type"] == "realign"]
    real = ct._realign_apply_old       # the row step, after the copy is back
    monkeypatch.setattr(ct, "_realign_apply_old",
                        lambda *a, **k: (_ for _ in ()).throw(SimulatedCrash()))
    with pytest.raises(SimulatedCrash):
        ct.undo_realign(world.env, op)
    ct.release_lock(world.env)
    monkeypatch.setattr(ct, "_realign_apply_old", real)
    # The copy came back before the crash; the row did not.
    assert os.path.isfile(s.old_p) and world.read(s.r2)["cwd"] == s.live_cwd
    assert op.manifest["undo_status"][S1] == "undoing"
    assert ct.undo_realign(world.env, op) == "undone"
    assert world.read(s.r2)["cwd"] == s.old_cwd


def test_a_damaged_record_is_refused_by_undo_and_recover(world):
    s, op = applied(world)
    op.manifest["copies"][0]["sidecar_inventory"] = [{"rel": "../x", "sha256": "0"}]
    ct.save_manifest(op)
    with pytest.raises(ct.Refusal) as exc:
        ct.undo_realign(world.env, op)
    assert "unsafe path" in str(exc.value)


# Every function that branches on an operation's type, and whether it names
# realign or why it need not. The design asked for this: the row-reader
# test does not catch a missing op-type branch. Fails until a new one is
# listed.
_OP_TYPE_BRANCHES = {
    "rotate_ops": "names it",
    "classify_op": "names it",
    "recover_op": "names it",
    "gather_doctor": "names it",
    "cmd_undo": "names it",
    "undo_realign": "names it",
    "_collides": "names it",
    "plan_realign": "names it",
    "_journal_rows": "realign rows take the last branch, a per-row store_path",
    "_unresolved_rows": "tells moves apart; realign rows carry `written`",
    "_realign_claimed_later": "names it",
    "plan_undo": "undo of a move only",
    "undo_new_row": "its own type",
    "undo_sync": "its own type",
    "undo_repoint": "its own type",
    "undo_retitle": "its own type",
    "undo_converge": "its own type",
    "_sync_paths_claimed_elsewhere": "sync claimants by bytes; a realign "
                                     "write is drift to sync's undo",
    "_live_override_note": "only sync records a live override note",
}


def _op_type_branches(path):
    import ast
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())

    def is_op_type(n):
        return ((isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "get" and n.args
                 and isinstance(n.args[0], ast.Constant)
                 and n.args[0].value == "op_type")
                or (isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant)
                    and n.slice.value == "op_type"))
    out = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        bound = {t.id for n in ast.walk(fn) if isinstance(n, ast.Assign)
                 and is_op_type(n.value) for t in n.targets if isinstance(t, ast.Name)}
        hit = any(isinstance(n, ast.Compare) and any(
            is_op_type(s) or (isinstance(s, ast.Name) and s.id in bound)
            for s in [n.left] + n.comparators) for n in ast.walk(fn))
        if hit:
            out[fn.name] = any(isinstance(n, ast.Constant) and n.value == "realign"
                               for n in ast.walk(fn))
    return out


def test_every_op_type_branch_names_realign_or_says_why_not():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    found = _op_type_branches(os.path.join(root, "claude_code_sessions.py"))
    assert set(found) == set(_OP_TYPE_BRANCHES)
    for name, names_it in found.items():
        assert names_it == (_OP_TYPE_BRANCHES[name] == "names it"), name
    gui = _op_type_branches(os.path.join(root, "claude_code_sessions_gui.py"))
    assert gui == {"_find_undoable_op": True}


# ------------------------------------------- the code review's findings
# Codex, DeepSeek and Kimi reviewed the diff 1541937..1acf6bd on 2026-11-17;
# each test below is a finding verified against the code, failing first.

def crashed_after_rows(world, s=None):
    s = s or behind_store(world)
    op = crash_at(world, "realign-after-rows", plan(world))
    return s, op


def test_forward_is_withheld_when_a_pending_copy_changed(world):
    s, op = crashed_after_rows(world)
    with open(s.old_p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "user", "message": {"content": "x"}}) + "\n")
    assert ct.classify_op(world.env, op)["resolutions"] == ["back"]


def test_forward_is_withheld_when_a_pending_sidecar_changed(world):
    s = behind_store(world)
    with_sidecar(s.old_p, {"subagents/a.jsonl": "a"})
    s, op = crashed_after_rows(world, s)
    with_sidecar(s.old_p, {"subagents/b.jsonl": "b"})
    assert ct.classify_op(world.env, op)["resolutions"] == ["back"]


def test_forward_is_withheld_when_the_live_copy_is_gone(world):
    s, op = crashed_after_rows(world)
    os.remove(s.live_p)
    assert ct.classify_op(world.env, op)["resolutions"] == ["back"]


def test_forward_is_withheld_when_a_written_row_moved_on(world):
    s, op = crashed_after_rows(world)
    d = world.read(s.r2)
    d["cwd"] = s.old_cwd
    with open(s.r2, "w") as fh:
        json.dump(d, fh)
    assert ct.classify_op(world.env, op)["resolutions"] == ["back"]


@pytest.mark.parametrize("direction", ["forward", "back"])
def test_an_interruption_between_rows_recovers_both_ways(world, direction):
    s = behind_store(world)
    r3 = world.row(A3, O3, "local_1", S1, s.old_cwd, activity=4)
    op = crash_at(world, "realign-mid-rows", plan(world))
    written = [r for r in op.manifest["rows"] if r.get("written")]
    assert len(written) == 1
    final = ct.recover_op(world.env, op, direction)
    if direction == "forward":
        assert final == "completed"
        assert world.read(s.r2)["cwd"] == s.live_cwd == world.read(r3)["cwd"]
    else:
        assert final == "rolled_back"
        assert world.read(s.r2)["cwd"] == s.old_cwd == world.read(r3)["cwd"]
        assert os.path.isfile(s.old_p)


def applied_with(world, s):
    ct.run_realign(world.env, plan(world))
    (op,) = [o for o in ct.list_ops(world.env) if o.manifest["op_type"] == "realign"]
    return s, op


def test_back_brings_a_sidecar_back_after_a_crash_mid_restore(world):
    s = behind_store(world)
    side = with_sidecar(s.old_p, {"subagents/a.jsonl": "a"})
    s, op = applied_with(world, s)
    ct.set_status(op, "writing")
    (cp,) = op.manifest["copies"]
    shutil.move(cp["aside"], cp["src"])          # the transcript came back,
    assert not os.path.exists(side)              # the sidecar did not
    assert ct.recover_op(world.env, op, "back") == "rolled_back"
    assert os.path.isfile(os.path.join(side, "subagents", "a.jsonl"))
    assert world.read(s.r2)["cwd"] == s.old_cwd


def test_back_keeps_a_conversation_whose_old_folder_is_taken_again(world):
    """The copy's place holds another copy now: putting the rows back would
    point them at a transcript nobody checked, so the conversation keeps
    realign's placement and its copy stays in the journal, listed."""
    s, op = applied(world)
    ct.set_status(op, "writing")
    world.copy(s.old_cwd, S1, t_entries(cwd=s.live_cwd) + diverging_turn())
    assert ct.recover_op(world.env, op, "back") == "rolled_back"
    assert world.read(s.r2)["cwd"] == s.live_cwd
    (cp,) = op.manifest["copies"]
    assert cp["moved"] and os.path.isfile(cp["aside"])
    assert "taken" in op.manifest["abort_reason"]
    rep = ct.gather_doctor(world.env)
    assert rep["realign_held_copies"] and not rep["rollback_residue"]


def test_a_restore_refuses_a_journal_path_outside_its_operation(world):
    s, op = applied(world)
    (cp,) = op.manifest["copies"]
    outside = str(world.tmp / "elsewhere.jsonl")
    shutil.copyfile(cp["aside"], outside)
    cp["aside"] = outside
    ct.save_manifest(op)
    with pytest.raises((ct.Refusal, ct.LayoutError)):
        ct.undo_realign(world.env, op)
    assert not os.path.exists(s.old_p) and os.path.isfile(outside)


def test_a_restore_refuses_a_source_outside_the_projects_root(world):
    s, op = applied(world)
    (cp,) = op.manifest["copies"]
    cp["src"] = str(world.tmp / "outside" / (S1 + ".jsonl"))
    os.makedirs(os.path.dirname(cp["src"]))
    ct.save_manifest(op)
    with pytest.raises((ct.Refusal, ct.LayoutError)):
        ct.undo_realign(world.env, op)
    assert not os.path.exists(cp["src"])


def test_the_recheck_refuses_with_its_own_reason(world):
    behind_store(world)
    a, _ = world.place("Other")
    b, _ = world.place("Other-wt")
    world.copy(a, S2, t_entries(cwd=a) + later_turns())
    world.copy(b, S2, t_entries(cwd=a))
    world.row(A1, O1, "local_2", S2, a, title="Northwind intake")
    world.row(A2, O2, "local_2", S2, b, title="Northwind intake")
    m = plan(world, only="ACME")
    world.row(A3, O3, "local_9", S2, b, title="ACME intake",   # now 2 match
              activity=99)
    with pytest.raises(ct.Refusal) as exc:
        ct.run_realign(world.env, m)
    assert "2 conversations" in str(exc.value)


def test_holds_found_under_the_lock_are_printed(world, capsys, monkeypatch):
    s = behind_store(world)
    real = ct._realign_recheck

    def grow_then_recheck(env, m):
        with open(s.live_p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "user", "message": {"content": "n"}}) + "\n")
        return real(env, m)
    monkeypatch.setattr(ct, "_realign_recheck", grow_then_recheck)
    assert ct.cmd_realign(world.env, ns(apply=True)) == 3
    out = capsys.readouterr().out
    assert "result" in out
    assert "changed since" in out[out.index("result"):]


def test_a_sidecar_file_that_differs_by_content_is_named(world):
    s = behind_store(world)
    with_sidecar(s.live_p, {"tool-results/t.txt": "new"})
    with_sidecar(s.old_p, {"tool-results/t.txt": "old"})
    (cp,) = plan(world)["copies"]
    assert cp["sidecar_only"] == ["tool-results/t.txt"]


def test_a_live_sidecar_change_since_the_plan_holds_the_conversation(world):
    s = behind_store(world)
    with_sidecar(s.live_p, {"subagents/a.jsonl": "a"})
    m = plan(world)
    with_sidecar(s.live_p, {"subagents/b.jsonl": "b"})
    assert ct.run_realign(world.env, m) == "nothing to write"
    assert "changed since" in held_reasons(m)[S1]


def _boom(*a, **k):
    raise PermissionError(13, "Access is denied")


def test_an_os_error_moving_a_copy_is_a_refusal(world, monkeypatch):
    s = behind_store(world)
    m = plan(world)
    monkeypatch.setattr(ct, "_shutil", types.SimpleNamespace(move=_boom))
    with pytest.raises(ct.Refusal):
        ct.run_realign(world.env, m)
    monkeypatch.undo()
    ct.release_lock(world.env)
    (op,) = ct.nonterminal_ops(world.env)
    assert ct.recover_op(world.env, op, "back") == "rolled_back"
    assert world.read(s.r2)["cwd"] == s.old_cwd and os.path.isfile(s.old_p)


def test_an_os_error_during_undo_is_a_refusal_and_resumes(world, monkeypatch):
    s, op = applied(world)
    monkeypatch.setattr(ct, "_shutil", types.SimpleNamespace(move=_boom))
    with pytest.raises(ct.Refusal):
        ct.undo_realign(world.env, op)
    monkeypatch.undo()
    assert op.manifest["undo_status"][S1] == "undoing"
    assert ct.undo_realign(world.env, op) == "undone"
    assert world.read(s.r2)["cwd"] == s.old_cwd


def test_an_open_converge_op_for_the_conversation_holds_it(world):
    s = behind_store(world)
    store = os.path.dirname(s.r1)
    ct.new_op(world.env, {"op_type": "converge", "only": "", "only_session": "",
                          "rows": [{"name": "local_new.json", "session": S1,
                                    "dest_path": os.path.join(store, "local_new.json"),
                                    "store_path": store, "pre_b64": None,
                                    "post_b64": "e30=", "written": False}]})
    assert "unfinished operation" in held_reasons(plan(world))[S1]


def test_an_open_sync_op_naming_the_conversation_in_its_images_holds_it(world):
    s = behind_store(world)
    store = os.path.dirname(s.r1)
    post = ct.b64(json.dumps({"cliSessionId": S1}).encode("utf-8"))
    ct.new_op(world.env, {"op_type": "sync", "dest_path": store,
                          "rows": [{"name": "local_x.json",
                                    "dest_path": os.path.join(store, "local_x.json"),
                                    "pre_b64": None, "post_b64": post,
                                    "written": False}]})
    assert "unfinished operation" in held_reasons(plan(world))[S1]


def test_folderplace_members_are_not_added(world):
    s = behind_store(world)
    world.row(A2, O2, "local_1", S1, s.old_cwd, activity=10,
              folderPlace={"cwd": s.old_cwd, "lost": True})
    (r,) = plan(world)["rows"]
    post = json.loads(ct.unb64(r["post_b64"]).decode("utf-8"))
    assert post["folderPlace"] == {"cwd": s.live_cwd, "lost": True}
    assert set(r["new"]) == {"cwd", "folderPlace.cwd"}


def test_a_damaged_undo_status_is_refused(world):
    s, op = applied(world)
    op.manifest["undo_status"] = ["not", "a", "dict"]
    ct.save_manifest(op)
    with pytest.raises(ct.Refusal) as exc:
        ct.undo_realign(world.env, op)
    assert "undo_status" in str(exc.value)


def test_doctor_survives_a_damaged_held_copy_record(world):
    s, op = applied(world)
    op.manifest["copies"][0]["basis"] = 5
    ct.save_manifest(op)
    rep = ct.gather_doctor(world.env)
    assert rep["realign_held_copies"][0]["bytes"] is None


def test_anonymized_json_hides_the_only_filter(world, capsys):
    behind_store(world)
    ct._ANONYMIZE = True
    ct.cmd_realign(world.env, ns(json=True, only="ACME"))
    js = json.loads(capsys.readouterr().out)
    assert js["only"] == "<only-filter>"


def test_two_operations_started_together_do_not_lock_each_other_out(world):
    """Ownership goes by the journal's total order (time, then op id), so of
    two operations journaled in the same instant exactly one is later."""
    s = behind_store(world)
    rows = [{"name": "local_1.json", "dest_path": s.r2,
             "store_path": os.path.dirname(s.r2), "written": True}]
    for _ in range(2):
        o = ct.new_op(world.env, {"op_type": "realign", "rows": rows})
        ct.set_status(o, "completed")
    ma, mb = [o.manifest for o in ct.list_ops(world.env)]
    claims = [ct._realign_claimed_later(world.env, ma, ma["rows"][0]),
              ct._realign_claimed_later(world.env, mb, mb["rows"][0])]
    assert sum(1 for c in claims if c) == 1


def test_the_plan_marks_an_archived_row(world, capsys):
    s = behind_store(world)
    world.row(A2, O2, "local_1", S1, s.old_cwd, activity=10, isArchived=True)
    ct.cmd_realign(world.env, ns())
    assert "(archived)" in capsys.readouterr().out


def test_the_window_reports_held_copies_and_waiting_rows(world):
    import claude_code_sessions_gui as gui
    s, op = applied(world)
    none_cwd, _ = world.place("Elsewhere")
    lonely, _ = world.place("Lonely")
    world.copy(lonely, S2, t_entries(cwd=lonely))
    world.row(A1, O1, "local_2", S2, none_cwd, title="Northwind intake")
    text = "\n".join(gui.SyncApp.doctor_lines(ct.gather_doctor(world.env)))
    assert "journal" in text and "copies the transcript" in text
