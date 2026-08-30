"""Tests for `ccs retitle`.

Ports the August scaffold's lessons and the design review's findings as tests,
per docs/specs/2026-08-26-retitle-design.md's Testing section. Every title is
from the fake cast (ACME-REVIEW, Northwind, "Quarterly board report
finalization") - never a real one.
"""
import json
import os
import types

import pytest

import claude_code_sessions as ct


def undo_ns(**kw):
    d = {"json": False, "verbose": False, "anonymize": False, "apply": False,
         "show": False, "op_id": None}
    d.update(kw)
    return types.SimpleNamespace(**d)


# Three accounts, mirroring the real <accountUuid>/<organizationUuid> layout.
# NOTE write_row's positionals are (env, root_idx, X, Y, ...) where X is the
# FIRST path component - the account - despite the fixture naming them
# (org, account).
A1 = "aaaaaaaa-0000-0000-0000-000000000001"
O1 = "bbbbbbbb-0000-0000-0000-000000000002"
A2 = "cccccccc-0000-0000-0000-000000000003"
O2 = "dddddddd-0000-0000-0000-000000000004"
A3 = "eeeeeeee-0000-0000-0000-000000000005"
O3 = "ffffffff-0000-0000-0000-000000000006"

SID = "12345678-9abc-def0-1234-56789abcdef0"
SID_B = "87654321-9abc-def0-1234-56789abcdef0"


class SimulatedCrash(Exception):
    pass


def _monotonic_now(env, start=1_800_000_000.0):
    """Distinct creation times per op - conftest's constant now() makes every
    op sort-key tie, and tie-breaking on the op id's random hex is exactly the
    second-resolution ambiguity the spec warns against relying on."""
    state = {"t": start}

    def now():
        state["t"] += 1.0
        return state["t"]
    env.now = now
    return env


def row_data(sid, title, **extra):
    d = {"sessionId": "local-x", "cliSessionId": sid, "title": title,
         "lastActivityAt": 1_755_000_000_000}
    d.update(extra)
    return d


def three_accounts(env, write_row, title="Quarterly board report finalization"):
    """One conversation held by three accounts; returns the three row paths.
    The rows deliberately differ from each other, and one has NO titleSource -
    the field the scaffold could not reconstruct."""
    p1 = write_row(env, 0, A1, O1, "local_1",
                   row_data(SID, title, titleSource="auto", lastActivityAt=1))
    p2 = write_row(env, 0, A2, O2, "local_1",
                   row_data(SID, title, titleSource="user", lastActivityAt=2))
    p3 = write_row(env, 0, A3, O3, "local_1",
                   row_data(SID, title, lastActivityAt=3))   # no titleSource
    return [p1, p2, p3]


def read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


# ------------------------------------------------------------------ planning

def test_plan_names_every_account_and_journals_every_preimage(
        mkenv, tmp_path, write_row):
    env = mkenv(tmp_path)
    paths = three_accounts(env, write_row)
    originals = {p: read_bytes(p) for p in paths}
    m = ct.plan_retitle(env, ct.RetitleFlags(only=SID[:8], title="ACME-REVIEW"))
    assert m["op_type"] == "retitle"
    assert m["cli_session_id"] == SID
    assert m["new_title"] == "ACME-REVIEW"
    # Count asserted, the way the scaffold learned to: three rows, three
    # byte-exact preimages.
    assert len(m["rows"]) == 3
    assert len([r["pre_b64"] for r in m["rows"]]) == 3
    by_path = {os.path.normcase(r["dest_path"]): r for r in m["rows"]}
    assert len(by_path) == 3
    for p, blob in originals.items():
        assert ct.unb64(by_path[os.path.normcase(p)]["pre_b64"]) == blob
    # Planning writes nothing.
    for p, blob in originals.items():
        assert read_bytes(p) == blob
    assert ct.list_ops(env) == []


def test_plan_resolves_only_by_title_substring(mkenv, tmp_path, write_row):
    env = mkenv(tmp_path)
    three_accounts(env, write_row)
    m = ct.plan_retitle(env, ct.RetitleFlags(only="board report",
                                             title="ACME-REVIEW"))
    assert m["cli_session_id"] == SID


def test_ambiguous_only_lists_candidates_with_ids(mkenv, tmp_path, write_row):
    """Resolving a collision by its title is EXPECTED to refuse the first
    time; the refusal is the workflow - it must hand over the ids."""
    env = mkenv(tmp_path)
    title = "Quarterly board report finalization"
    write_row(env, 0, A1, O1, "local_1", row_data(SID, title))
    write_row(env, 0, A1, O1, "local_2", row_data(SID_B, title))
    with pytest.raises(ct.Refusal) as exc:
        ct.plan_retitle(env, ct.RetitleFlags(only=title, title="ACME-REVIEW"))
    msg = str(exc.value)
    assert SID[:8] in msg
    assert SID_B[:8] in msg
    assert "2 conversations" in msg


def test_no_match_names_new_row_as_the_remedy(mkenv, tmp_path, write_row):
    env = mkenv(tmp_path)
    three_accounts(env, write_row)
    with pytest.raises(ct.Refusal, match="new-row"):
        ct.plan_retitle(env, ct.RetitleFlags(only="Northwind onboarding",
                                             title="ACME-REVIEW"))


def test_title_validation(mkenv, tmp_path, write_row):
    env = mkenv(tmp_path)
    three_accounts(env, write_row)
    for bad in ("", "   ", "\n", " \t "):
        with pytest.raises(ct.Refusal):
            ct.plan_retitle(env, ct.RetitleFlags(only=SID[:8], title=bad))
    # Interior newlines and C0 controls are refused, not stripped.
    for bad in ("ACME\nREVIEW", "ACME\tREVIEW", "ACME\x07REVIEW"):
        with pytest.raises(ct.Refusal, match="control"):
            ct.plan_retitle(env, ct.RetitleFlags(only=SID[:8], title=bad))


def test_stored_title_is_the_trimmed_input(mkenv, tmp_path, write_row):
    env = mkenv(tmp_path)
    three_accounts(env, write_row)
    m = ct.plan_retitle(env, ct.RetitleFlags(only=SID[:8],
                                             title="  ACME-REVIEW  "))
    assert m["new_title"] == "ACME-REVIEW"
    for r in m["rows"]:
        post = json.loads(ct.unb64(r["post_b64"]).decode("utf-8"))
        assert post["title"] == "ACME-REVIEW"
        assert post["titleSource"] == "user"


def test_collision_with_another_conversation_refuses_and_names_it(
        mkenv, tmp_path, write_row):
    env = mkenv(tmp_path)
    three_accounts(env, write_row)
    write_row(env, 0, A2, O2, "local_9",
              row_data(SID_B, "Northwind intake notes"))
    with pytest.raises(ct.Refusal) as exc:
        ct.plan_retitle(env, ct.RetitleFlags(only=SID[:8],
                                             title="Northwind intake notes"))
    assert "local_9" in str(exc.value)


def test_collision_check_is_trimmed_exact_match(mkenv, tmp_path, write_row):
    env = mkenv(tmp_path)
    three_accounts(env, write_row)
    write_row(env, 0, A2, O2, "local_9",
              row_data(SID_B, "  Northwind intake notes  "))
    with pytest.raises(ct.Refusal):
        ct.plan_retitle(env, ct.RetitleFlags(only=SID[:8],
                                             title="Northwind intake notes"))
    # A substring is NOT a collision.
    m = ct.plan_retitle(env, ct.RetitleFlags(only=SID[:8],
                                             title="Northwind intake"))
    assert m["new_title"] == "Northwind intake"


def test_retitling_to_the_targets_own_title_proceeds(mkenv, tmp_path, write_row):
    """The exclusion of the target's own rows, both ways: a same-title plan is
    allowed (it still pins titleSource), and a case-only fix must not collide
    with itself."""
    env = mkenv(tmp_path)
    title = "Quarterly board report finalization"
    three_accounts(env, write_row, title=title)
    m = ct.plan_retitle(env, ct.RetitleFlags(only=SID[:8], title=title))
    assert m["new_title"] == title
    m2 = ct.plan_retitle(env, ct.RetitleFlags(
        only=SID[:8], title="Quarterly Board Report Finalization"))
    assert len(m2["rows"]) == 3


def test_collision_in_a_non_target_sidebar_does_not_block(
        mkenv, tmp_path, write_row):
    """The check is per TARGET sidebar. An account that does not hold the
    conversation can already use the name freely."""
    env = mkenv(tmp_path)
    write_row(env, 0, A1, O1, "local_1", row_data(SID, "Old name"))
    write_row(env, 0, A2, O2, "local_9",
              row_data(SID_B, "ACME-REVIEW"))          # A2 has no row for SID
    m = ct.plan_retitle(env, ct.RetitleFlags(only=SID[:8], title="ACME-REVIEW"))
    assert len(m["rows"]) == 1


def test_duplicate_json_keys_are_unreadable_at_plan_time(
        mkenv, tmp_path, write_row):
    """json.loads silently keeps the LAST duplicate key; rewriting such a row
    would drop data the parser hid. Treated as unreadable, which blocks."""
    env = mkenv(tmp_path)
    three_accounts(env, write_row)
    store = os.path.dirname(
        write_row(env, 0, A2, O2, "local_dup", row_data(SID_B, "x")))
    with open(os.path.join(store, "local_dup.json"), "w", encoding="utf-8") as fh:
        fh.write('{"cliSessionId": "%s", "title": "A", "title": "B"}' % SID_B)
    with pytest.raises(ct.Refusal, match="could not be read"):
        ct.plan_retitle(env, ct.RetitleFlags(only=SID[:8], title="ACME-REVIEW"))


def test_unparseable_row_anywhere_blocks_the_plan(mkenv, tmp_path, write_row):
    env = mkenv(tmp_path)
    three_accounts(env, write_row)
    store = os.path.dirname(
        write_row(env, 0, A3, O3, "local_bad", row_data(SID_B, "x")))
    with open(os.path.join(store, "local_bad.json"), "w", encoding="utf-8") as fh:
        fh.write("{not json")
    with pytest.raises(ct.Refusal, match="could not be read"):
        ct.plan_retitle(env, ct.RetitleFlags(only=SID[:8], title="ACME-REVIEW"))


# -------------------------------------------------------------------- --store

def test_store_narrows_to_one_account(mkenv, tmp_path, write_row):
    env = mkenv(tmp_path)
    three_accounts(env, write_row)
    m = ct.plan_retitle(env, ct.RetitleFlags(only=SID[:8], title="ACME-REVIEW",
                                             store=A2[:8]))
    assert len(m["rows"]) == 1
    assert A2 in m["rows"][0]["dest_path"]
    assert m["store_is_a_guess"] is False


def test_store_without_the_conversation_names_new_row(mkenv, tmp_path, write_row):
    env = mkenv(tmp_path)
    write_row(env, 0, A1, O1, "local_1", row_data(SID, "Old name"))
    write_row(env, 0, A2, O2, "local_2", row_data(SID_B, "Other"))
    with pytest.raises(ct.Refusal, match="new-row"):
        ct.plan_retitle(env, ct.RetitleFlags(only=SID[:8], title="ACME-REVIEW",
                                             store=A2[:8]))


def test_store_guess_is_flagged_in_the_plan(mkenv, tmp_path, write_row):
    """An account id matching two org directories where only one holds rows is
    a row-count GUESS, not an identification - carried in the manifest so the
    apply can refuse it."""
    env = mkenv(tmp_path)
    write_row(env, 0, A1, O1, "local_1", row_data(SID, "Old name"))
    os.makedirs(os.path.join(env.store_candidates[0], A1, O2))   # empty sibling org
    m = ct.plan_retitle(env, ct.RetitleFlags(only=SID[:8], title="ACME-REVIEW",
                                             store=A1[:8]))
    assert m["store_is_a_guess"] is True


def test_pair_form_resolves_store(mkenv, tmp_path, write_row):
    """_repoint_store accepts a pair-shaped query - the `acct/org` form every
    report prints - as two anchored prefixes filtering the same dirs the
    substring branches filter; the let-the-row-settle-it behavior continues
    on whatever survives."""
    env = mkenv(tmp_path)
    three_accounts(env, write_row)
    m = ct.plan_retitle(env, ct.RetitleFlags(only=SID[:8], title="ACME-REVIEW",
                                             store="aaaaaaaa/bbbbbbbb"))
    assert len(m["rows"]) == 1
    assert A1 in m["rows"][0]["dest_path"]
    assert m["store_is_a_guess"] is False
    # A pair whose left half sits MID-uuid matches no store and refuses with
    # the listing - it must never fall back to the path-substring branch,
    # where '0001/bbbbbbbb' IS a fragment of A1's normalized store path.
    with pytest.raises(ct.Refusal, match="matched no store"):
        ct.plan_retitle(env, ct.RetitleFlags(only=SID[:8], title="ACME-REVIEW",
                                             store="0001/bbbbbbbb"))


def test_store_sibling_divergence_warning_iff_siblings_would_differ(
        mkenv, tmp_path, write_row):
    env = mkenv(tmp_path)
    write_row(env, 0, A1, O1, "local_1", row_data(SID, "Old name"))
    write_row(env, 0, A2, O2, "local_1", row_data(SID, "ACME-REVIEW"))
    write_row(env, 0, A3, O3, "local_1", row_data(SID, "ACME-REVIEW"))
    # Renaming A1's copy to what the siblings already say: no divergence.
    m = ct.plan_retitle(env, ct.RetitleFlags(only=SID[:8], title="ACME-REVIEW",
                                             store=A1[:8]))
    assert m["sibling_divergence"] is False
    assert len(m["siblings"]) == 2
    # Renaming it to something the siblings do NOT say: divergence, named.
    m2 = ct.plan_retitle(env, ct.RetitleFlags(only=SID[:8], title="Northwind kickoff",
                                              store=A1[:8]))
    assert m2["sibling_divergence"] is True
    assert sorted(e["title"] for e in m2["siblings"]) == ["ACME-REVIEW", "ACME-REVIEW"]


def test_default_scope_has_no_sibling_report(mkenv, tmp_path, write_row):
    env = mkenv(tmp_path)
    three_accounts(env, write_row)
    m = ct.plan_retitle(env, ct.RetitleFlags(only=SID[:8], title="ACME-REVIEW"))
    assert m["siblings"] == []
    assert m["sibling_divergence"] is False


# ------------------------------------------------------------------- writing

def test_apply_renames_all_three_and_changes_nothing_else(
        mkenv, tmp_path, write_row):
    env = _monotonic_now(mkenv(tmp_path))
    paths = three_accounts(env, write_row)
    originals = {p: json.loads(read_bytes(p).decode("utf-8")) for p in paths}
    m = ct.plan_retitle(env, ct.RetitleFlags(only=SID[:8], title="ACME-REVIEW"))
    assert ct.run_retitle(env, m) == "completed"
    for p in paths:
        after = json.loads(read_bytes(p).decode("utf-8"))
        assert after["title"] == "ACME-REVIEW"
        assert after["titleSource"] == "user"
        rest_before = {k: v for k, v in originals[p].items()
                       if k not in ("title", "titleSource")}
        rest_after = {k: v for k, v in after.items()
                      if k not in ("title", "titleSource")}
        assert rest_after == rest_before
    op = ct.list_ops(env)[-1]
    assert op.manifest["op_type"] == "retitle"
    assert op.manifest["status"] == "completed"
    assert all(r["written"] for r in op.manifest["rows"])
    assert len(op.manifest["rows"]) == 3
    assert ct.read_lock(env) is None
    assert m["op_id"] == op.manifest["op_id"]


def test_same_title_apply_pins_titlesource(mkenv, tmp_path, write_row):
    """Retitling to the target's own current title is a useful write: it pins
    titleSource to 'user' so the app stops resummarising."""
    env = _monotonic_now(mkenv(tmp_path))
    title = "Quarterly board report finalization"
    paths = three_accounts(env, write_row, title=title)
    m = ct.plan_retitle(env, ct.RetitleFlags(only=SID[:8], title=title))
    assert ct.run_retitle(env, m) == "completed"
    for p in paths:
        after = json.loads(read_bytes(p).decode("utf-8"))
        assert after["title"] == title
        assert after["titleSource"] == "user"


def test_apply_time_drift_refuses_and_journals_nothing(
        mkenv, tmp_path, write_row):
    env = _monotonic_now(mkenv(tmp_path))
    paths = three_accounts(env, write_row)
    m = ct.plan_retitle(env, ct.RetitleFlags(only=SID[:8], title="ACME-REVIEW"))
    # The app moves underneath, between plan and apply.
    drifted = json.loads(read_bytes(paths[1]).decode("utf-8"))
    drifted["title"] = "Renamed by the app meanwhile"
    with open(paths[1], "w", encoding="utf-8") as fh:
        json.dump(drifted, fh)
    before = {p: read_bytes(p) for p in paths}
    with pytest.raises(ct.Refusal, match="changed since"):
        ct.run_retitle(env, m)
    assert {p: read_bytes(p) for p in paths} == before
    # The re-check runs BEFORE the journal entry: a refused run leaves no op.
    assert ct.list_ops(env) == []
    assert ct.read_lock(env) is None


def test_apply_time_target_set_drift_refuses_and_journals_nothing(
        mkenv, tmp_path, write_row):
    env = _monotonic_now(mkenv(tmp_path))
    paths = three_accounts(env, write_row)
    m = ct.plan_retitle(env, ct.RetitleFlags(only=SID[:8], title="ACME-REVIEW"))
    # sync lands the conversation in a FOURTH account between plan and apply.
    p4 = write_row(env, 0, "99999999-0000-0000-0000-000000000009",
                   "88888888-0000-0000-0000-000000000008", "local_1",
                   row_data(SID, "Quarterly board report finalization"))
    before = {p: read_bytes(p) for p in paths + [p4]}
    with pytest.raises(ct.Refusal, match="every account"):
        ct.run_retitle(env, m)
    assert {p: read_bytes(p) for p in paths + [p4]} == before
    assert ct.list_ops(env) == []


def test_apply_time_collision_refuses_and_journals_nothing(
        mkenv, tmp_path, write_row):
    env = _monotonic_now(mkenv(tmp_path))
    three_accounts(env, write_row)
    m = ct.plan_retitle(env, ct.RetitleFlags(only=SID[:8], title="ACME-REVIEW"))
    write_row(env, 0, A1, O1, "local_late", row_data(SID_B, "ACME-REVIEW"))
    with pytest.raises(ct.Refusal, match="now names a different conversation"):
        ct.run_retitle(env, m)
    assert ct.list_ops(env) == []


def test_journal_write_failure_touches_nothing(mkenv, tmp_path, write_row,
                                               monkeypatch):
    """The op record is written and fsynced BEFORE any row is touched, so a
    failure writing it is the one clean failure: nothing landed, nothing to
    recover."""
    env = _monotonic_now(mkenv(tmp_path))
    paths = three_accounts(env, write_row)
    before = {p: read_bytes(p) for p in paths}
    m = ct.plan_retitle(env, ct.RetitleFlags(only=SID[:8], title="ACME-REVIEW"))

    def boom(env_, manifest):
        raise OSError("disk full")
    monkeypatch.setattr(ct, "new_op", boom)
    with pytest.raises(ct.Refusal, match="nothing to recover"):
        ct.run_retitle(env, m)
    assert {p: read_bytes(p) for p in paths} == before
    assert ct.read_lock(env) is None


# ---------------------------------------------------- fault injection, recovery

def _crash_after_first_row(env, write_row, point="retitle-mid-write"):
    paths = three_accounts(env, write_row)
    originals = {p: read_bytes(p) for p in paths}
    m = ct.plan_retitle(env, ct.RetitleFlags(only=SID[:8], title="ACME-REVIEW"))

    def hook(p):
        if p == point:
            raise SimulatedCrash()
    ct._crash_hook = hook
    try:
        with pytest.raises(SimulatedCrash):
            ct.run_retitle(env, m)
    finally:
        ct._crash_hook = None
    op = ct.nonterminal_ops(env)[0]
    assert op.manifest["status"] == "writing"
    return paths, originals, op


def test_crash_after_row_one_then_back_restores_agreement(
        mkenv, tmp_path, write_row):
    """The spec's fault-injection case, direction one: kill after row 1 of 3,
    recover --back, and every preimage is restored - the operation never
    happened and the three sidebars agree."""
    env = _monotonic_now(mkenv(tmp_path))
    paths, originals, op = _crash_after_first_row(env, write_row)
    assert [r["written"] for r in op.manifest["rows"]] == [True, False, False]
    c = ct.classify_op(env, op)
    assert sorted(c["resolutions"]) == ["back", "forward"]
    assert ct.recover_op(env, op, "back") == "rolled_back"
    assert {p: read_bytes(p) for p in paths} == originals
    assert op.manifest["status"] == "rolled_back"
    assert ct.read_lock(env) is None


def test_crash_after_row_one_then_forward_finishes_the_rename(
        mkenv, tmp_path, write_row):
    """Direction two: recover --forward completes the remaining writes from
    the journaled plan (re-running the apply-time checks first) and the three
    sidebars agree on the NEW title."""
    env = _monotonic_now(mkenv(tmp_path))
    paths, _originals, op = _crash_after_first_row(env, write_row)
    assert ct.recover_op(env, op, "forward") == "completed"
    for p in paths:
        after = json.loads(read_bytes(p).decode("utf-8"))
        assert after["title"] == "ACME-REVIEW"
        assert after["titleSource"] == "user"
    assert all(r["written"] for r in op.manifest["rows"])


def test_hard_kill_window_is_reversed_by_disk_evidence(
        mkenv, tmp_path, write_row):
    """A kill between atomic_write and save_manifest leaves the row holding
    this op's bytes with `written` still False. Back consults the disk, not
    the flag, and restores it anyway."""
    env = _monotonic_now(mkenv(tmp_path))
    paths, originals, op = _crash_after_first_row(
        env, write_row, point="retitle-write-before-save")
    assert [r["written"] for r in op.manifest["rows"]] == [False, False, False]
    on_disk = read_bytes(op.manifest["rows"][0]["dest_path"])
    assert on_disk == ct.unb64(op.manifest["rows"][0]["post_b64"])
    assert ct.recover_op(env, op, "back") == "rolled_back"
    assert {p: read_bytes(p) for p in paths} == originals


def test_pending_row_drift_withdraws_forward(mkenv, tmp_path, write_row):
    """A pending row the app rewrote can never be rolled forward
    (execute refuses it on every re-entry), so classify offers back alone -
    the same shape as sync's."""
    env = _monotonic_now(mkenv(tmp_path))
    paths, originals, op = _crash_after_first_row(env, write_row)
    pending = op.manifest["rows"][1]
    changed = json.loads(read_bytes(pending["dest_path"]).decode("utf-8"))
    changed["lastActivityAt"] = 999
    with open(pending["dest_path"], "w", encoding="utf-8") as fh:
        json.dump(changed, fh)
    c = ct.classify_op(env, op)
    assert c["resolutions"] == ["back"]
    with pytest.raises(ct.Refusal, match="not a safe resolution"):
        ct.recover_op(env, op, "forward")
    assert ct.recover_op(env, op, "back") == "rolled_back"
    # The written row went back to its original; the drifted pending row keeps
    # whatever changed it (back never overwrites what it cannot verify).
    assert read_bytes(paths[0]) == originals[paths[0]]
    assert json.loads(read_bytes(paths[1]).decode("utf-8"))["lastActivityAt"] == 999


# ---------------------------------------------------------------------- undo

def test_undo_restores_byte_exact_including_absent_titlesource(
        mkenv, tmp_path, write_row):
    """Bytes make undo exact by construction - including titleSource on the
    row where it was ABSENT, the field the scaffold recorded too little to
    restore."""
    env = _monotonic_now(mkenv(tmp_path))
    paths = three_accounts(env, write_row)
    originals = {p: read_bytes(p) for p in paths}
    assert b"titleSource" not in originals[paths[2]]
    m = ct.plan_retitle(env, ct.RetitleFlags(only=SID[:8], title="ACME-REVIEW"))
    assert ct.run_retitle(env, m) == "completed"
    op = ct.list_ops(env)[-1]
    assert ct.undo_retitle(env, op) == "undone"
    assert {p: read_bytes(p) for p in paths} == originals
    assert op.manifest["status"] == "undone"


def test_stacked_undo_reverses_newest_first_and_consumes_each(
        mkenv, tmp_path, write_row, capsys):
    """The scaffold's second bug, pinned: retitle A, retitle B, undo, undo -
    B's preimages restored first, then A's, both byte-exact, each op
    consumed so the second undo reaches the first op."""
    env = _monotonic_now(mkenv(tmp_path))
    pa1 = write_row(env, 0, A1, O1, "local_a",
                    row_data(SID, "Quarterly board report finalization"))
    pa2 = write_row(env, 0, A2, O2, "local_a",
                    row_data(SID, "Quarterly board report finalization"))
    pb = write_row(env, 0, A1, O1, "local_b",
                   row_data(SID_B, "Northwind intake notes"))
    originals = {p: read_bytes(p) for p in (pa1, pa2, pb)}
    ct.run_retitle(env, ct.plan_retitle(
        env, ct.RetitleFlags(only=SID[:8], title="ACME-REVIEW")))
    ct.run_retitle(env, ct.plan_retitle(
        env, ct.RetitleFlags(only=SID_B[:8], title="Northwind kickoff")))
    op_a, op_b = ct.list_ops(env)[-2:]
    assert op_a.manifest["cli_session_id"] == SID
    assert op_b.manifest["cli_session_id"] == SID_B

    assert ct.cmd_undo(env, undo_ns(apply=True)) == 0
    assert read_bytes(pb) == originals[pb]               # B restored first
    assert json.loads(read_bytes(pa1).decode("utf-8"))["title"] == "ACME-REVIEW"

    assert ct.cmd_undo(env, undo_ns(apply=True)) == 0
    assert {p: read_bytes(p) for p in (pa1, pa2, pb)} == originals
    statuses = {o.manifest["op_id"]: o.manifest["status"]
                for o in ct.list_ops(env)}
    assert statuses[op_a.manifest["op_id"]] == "undone"
    assert statuses[op_b.manifest["op_id"]] == "undone"


def test_chained_retitles_unwind_in_order(mkenv, tmp_path, write_row):
    """Retitle X -> T1, then T1 -> T2, then undo twice. Undoing the newest
    restores bytes identical to the older op's post-image, and the older op
    must then still be undoable - the withdrawn-claim rule in
    _retitle_claimed_elsewhere is what makes the chain unwind."""
    env = _monotonic_now(mkenv(tmp_path))
    p = write_row(env, 0, A1, O1, "local_1",
                  row_data(SID, "Quarterly board report finalization"))
    original = read_bytes(p)
    ct.run_retitle(env, ct.plan_retitle(
        env, ct.RetitleFlags(only=SID[:8], title="ACME-REVIEW")))
    ct.run_retitle(env, ct.plan_retitle(
        env, ct.RetitleFlags(only=SID[:8], title="Northwind kickoff")))
    assert ct.cmd_undo(env, undo_ns(apply=True)) == 0
    assert json.loads(read_bytes(p).decode("utf-8"))["title"] == "ACME-REVIEW"
    assert ct.cmd_undo(env, undo_ns(apply=True)) == 0
    assert read_bytes(p) == original


def test_undo_is_all_or_nothing_on_drift(mkenv, tmp_path, write_row):
    env = _monotonic_now(mkenv(tmp_path))
    paths = three_accounts(env, write_row)
    m = ct.plan_retitle(env, ct.RetitleFlags(only=SID[:8], title="ACME-REVIEW"))
    ct.run_retitle(env, m)
    op = ct.list_ops(env)[-1]
    # One account opens the session; the app rewrites its row.
    changed = json.loads(read_bytes(paths[1]).decode("utf-8"))
    changed["lastActivityAt"] = 999
    with open(paths[1], "w", encoding="utf-8") as fh:
        json.dump(changed, fh)
    with pytest.raises(ct.Refusal) as exc:
        ct.undo_retitle(env, op)
    msg = str(exc.value)
    assert "local_1" in msg and "disagreeing" in msg
    # NOTHING was restored - not even the two clean rows.
    for p in (paths[0], paths[2]):
        assert json.loads(read_bytes(p).decode("utf-8"))["title"] == "ACME-REVIEW"
    assert op.manifest["status"] == "completed"


def test_undo_refuses_when_a_renamed_row_was_deleted(mkenv, tmp_path, write_row):
    env = _monotonic_now(mkenv(tmp_path))
    paths = three_accounts(env, write_row)
    ct.run_retitle(env, ct.plan_retitle(
        env, ct.RetitleFlags(only=SID[:8], title="ACME-REVIEW")))
    op = ct.list_ops(env)[-1]
    os.unlink(paths[2])
    with pytest.raises(ct.Refusal, match="resurrect"):
        ct.undo_retitle(env, op)
    assert json.loads(read_bytes(paths[0]).decode("utf-8"))["title"] == "ACME-REVIEW"


def test_undo_restores_even_when_history_collides_and_says_so(
        mkenv, tmp_path, write_row, capsys):
    """Exact restoration outranks the distinguishability invariant - the
    restore proceeds and the report says a collision now exists."""
    env = _monotonic_now(mkenv(tmp_path))
    old = "Quarterly board report finalization"
    p = write_row(env, 0, A1, O1, "local_1", row_data(SID, old))
    original = read_bytes(p)
    ct.run_retitle(env, ct.plan_retitle(
        env, ct.RetitleFlags(only=SID[:8], title="ACME-REVIEW")))
    # Another conversation adopts the old title while the retitle stands.
    write_row(env, 0, A1, O1, "local_2", row_data(SID_B, old))
    assert ct.cmd_undo(env, undo_ns(apply=True)) == 0
    out = capsys.readouterr().out
    assert read_bytes(p) == original
    assert "note:" in out and "collide" in out
    op = [o for o in ct.list_ops(env)
          if o.manifest.get("op_type") == "retitle"][-1]
    assert op.manifest["status"] == "undone"
    assert "collide" in op.manifest["undo_collision_note"]


def test_undo_preview_names_the_retitle(mkenv, tmp_path, write_row, capsys):
    env = _monotonic_now(mkenv(tmp_path))
    three_accounts(env, write_row)
    ct.run_retitle(env, ct.plan_retitle(
        env, ct.RetitleFlags(only=SID[:8], title="ACME-REVIEW")))
    assert ct.cmd_undo(env, undo_ns(apply=False)) == 0
    out = capsys.readouterr().out
    assert "retitle: 3 row(s)" in out
    assert "previous titles" in out


# ----------------------------------------------------------------- the command

def rt_ns(**kw):
    d = {"json": False, "verbose": False, "anonymize": False, "apply": False,
         "only": "", "title": "", "store": "", "live": ""}
    d.update(kw)
    return types.SimpleNamespace(**d)


def test_cmd_dry_run_names_every_account_and_writes_nothing(
        mkenv, tmp_path, write_row, capsys):
    env = mkenv(tmp_path)
    paths = three_accounts(env, write_row)
    before = {p: read_bytes(p) for p in paths}
    rc = ct.cmd_retitle(env, rt_ns(only=SID[:8], title="ACME-REVIEW"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "dry run" in out
    assert out.count("ACME-REVIEW") >= 3            # per-account old -> new lines
    assert "Quarterly board report finalization" in out
    assert "SIDEBAR" in out
    assert {p: read_bytes(p) for p in paths} == before
    assert ct.list_ops(env) == []


def test_cmd_apply_end_to_end(mkenv, tmp_path, write_row, capsys):
    env = _monotonic_now(mkenv(tmp_path))
    paths = three_accounts(env, write_row)
    rc = ct.cmd_retitle(env, rt_ns(only=SID[:8], title="ACME-REVIEW",
                                   apply=True))
    out = capsys.readouterr().out
    assert rc == 0
    assert "result  : completed" in out
    for p in paths:
        assert json.loads(read_bytes(p).decode("utf-8"))["title"] == "ACME-REVIEW"


def test_cmd_refuses_to_apply_a_guessed_store(mkenv, tmp_path, write_row,
                                              capsys):
    """The spec's --store test: an email/id matching several org directories
    is a row-count guess - shown in the dry run, refused at apply."""
    env = _monotonic_now(mkenv(tmp_path))
    p = write_row(env, 0, A1, O1, "local_1", row_data(SID, "Old name"))
    os.makedirs(os.path.join(env.store_candidates[0], A1, O2))
    rc = ct.cmd_retitle(env, rt_ns(only=SID[:8], title="ACME-REVIEW",
                                   store=A1[:8]))
    assert rc == 0
    assert "GUESS" in capsys.readouterr().out
    before = read_bytes(p)
    with pytest.raises(ct.Refusal, match="counting rows"):
        ct.cmd_retitle(env, rt_ns(only=SID[:8], title="ACME-REVIEW",
                                  store=A1[:8], apply=True))
    assert read_bytes(p) == before
    assert ct.list_ops(env) == []


def test_cmd_store_prints_the_sibling_warning_iff_diverging(
        mkenv, tmp_path, write_row, capsys):
    env = mkenv(tmp_path)
    write_row(env, 0, A1, O1, "local_1", row_data(SID, "Old name"))
    write_row(env, 0, A2, O2, "local_1", row_data(SID, "ACME-REVIEW"))
    ct.cmd_retitle(env, rt_ns(only=SID[:8], title="Northwind kickoff",
                              store=A1[:8]))
    assert "WARNING" in capsys.readouterr().out
    ct.cmd_retitle(env, rt_ns(only=SID[:8], title="ACME-REVIEW",
                              store=A1[:8]))
    out = capsys.readouterr().out
    assert "WARNING" not in out
    assert "read the same" in out


def test_cmd_json_strips_row_images(mkenv, tmp_path, write_row, capsys):
    env = mkenv(tmp_path)
    three_accounts(env, write_row)
    rc = ct.cmd_retitle(env, rt_ns(only=SID[:8], title="ACME-REVIEW",
                                   json=True))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["new_title"] == "ACME-REVIEW"
    assert len(payload["rows"]) == 3
    for r in payload["rows"]:
        assert "pre_b64" not in r and "post_b64" not in r


def test_anonymized_plan_labels_both_titles(mkenv, tmp_path, write_transcript,
                                            write_row, capsys):
    """--anonymize is view-only here by construction. The current title goes
    through the standard substitution map; the proposed one exists nowhere
    yet, so it becomes the fixed label <proposed-title>."""
    env = mkenv(tmp_path)
    old = "Quarterly board report finalization"
    three_accounts(env, write_row, title=old)
    ct._ANONYMIZE = True
    ct._ANON_CACHE.clear()
    try:
        rc = ct.cmd_retitle(env, rt_ns(only=SID[:8], title="Northwind kickoff",
                                       anonymize=True))
        out = capsys.readouterr().out
        assert rc == 0
        assert old not in out
        assert "board report" not in out
        assert "<session-" in out
        assert "Northwind kickoff" not in out
        assert "<proposed-title>" in out

        rc = ct.cmd_retitle(env, rt_ns(only=SID[:8], title="Northwind kickoff",
                                       anonymize=True, json=True))
        payload = capsys.readouterr().out
        assert rc == 0
        assert old not in payload and "Northwind kickoff" not in payload
        assert json.loads(payload)["new_title"] == "<proposed-title>"
    finally:
        ct._ANONYMIZE = False
        ct._ANON_CACHE.clear()


def test_anonymized_ambiguity_listing_is_labeled(mkenv, tmp_path, write_row,
                                                 capsys):
    """The candidate listing from an ambiguous --only is anonymized the same
    way (it flows through the refusal, which main() redacts)."""
    env = mkenv(tmp_path)
    title = "Quarterly board report finalization"
    write_row(env, 0, A1, O1, "local_1", row_data(SID, title))
    write_row(env, 0, A1, O1, "local_2", row_data(SID_B, title))
    ct._ANONYMIZE = True
    ct._ANON_CACHE.clear()
    try:
        with pytest.raises(ct.Refusal) as exc:
            ct.plan_retitle(env, ct.RetitleFlags(only=title, title="ACME-X"))
        assert title not in ct.redact(env, str(exc.value))
    finally:
        ct._ANONYMIZE = False
        ct._ANON_CACHE.clear()


def test_retitle_help_says_sidebar(capsys):
    with pytest.raises(SystemExit) as exc:
        ct.main(["retitle", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "SIDEBAR" in out
    assert "customTitle" in out


def test_anonymize_apply_is_refused_globally(capsys):
    rc = ct.main(["retitle", "--only", "x", "--title", "y",
                  "--anonymize", "--apply"])
    assert rc == 2
    assert "--anonymize" in capsys.readouterr().err
