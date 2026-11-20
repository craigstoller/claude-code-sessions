"""`relink`: give a row the link and chain its conversation already has elsewhere.

Implements docs/specs/2026-11-19-relink-design.md. This file starts with the
value helpers every later step builds on: the app-written shape check, the
key-by-key apply and the state comparison; then the resolution core, which
decides row by row what to write and what to leave out; then plan_relink,
the strict scan, the refusals and the narrowing around it. Fake cast only:
Northwind, ACME-REVIEW, the A/LP/LC ids and local_p/local_c/local_r rows of
the converge tests. Then the report: the per-account listing, the totals,
and the public projection --json and --anonymize print; the journal's shape
check and saver. Then the apply: the locked re-plan, the row writes verified
by value, the CLI, and the one RULING 5 text the three apply rechecks share.
Then the claims: which rows a later live operation owns (D13), and the other
operations' ownership checks, which neither ignore a relink nor wait on one.
Then undo: key by key, leaving and naming what the app changed since. Last,
recovery: forward finishes an interrupted relink by what each key holds now,
and back takes out what landed, naming what it left.
"""
import argparse
import hashlib
import io
import json
import os
import re
import shutil
import sys
import types

import pytest

import claude_code_sessions as ct
from test_converge import (A1, A2, A3, A4, LC, LD, LP, LX, LY, NUM, O1, O2, O3, O4, ORG, S1,
                           TASK, SimulatedCrash, _monotonic_now, identity_files, lrow,
                           side_link, t_entries, undo_ns)


@pytest.fixture(autouse=True)
def _reset():
    ct._ANONYMIZE = False
    ct._ANON_CACHE.clear()
    ct._crash_hook = None
    yield
    ct._ANONYMIZE = False
    ct._ANON_CACHE.clear()
    ct._crash_hook = None


def test_app_written_is_the_two_app_shapes_only():
    assert ct._relink_app_written(side_link("local_p1"))
    assert ct._relink_app_written({"sessionId": "local_p1", "taskId": TASK, "title": "Northwind"})
    for link in ({"sessionId": "local_p1", "taskId": TASK},                    # this tool's own
                 {"sessionId": "local_p1", "taskId": TASK, "title": ""},
                 {"sessionId": "local_p1", "taskId": TASK, "title": 7},
                 {"sessionId": "local_p1", "taskId": TASK, "title": "N", "pendingPop": True},
                 {"sessionId": "local_p1", "sideSession": "true"}, None, "local_p1", [], {}):
        assert not ct._relink_app_written(link)


def test_apply_new_sets_in_place_or_appends_and_copies():
    d = {"a": 1, "spawnedFrom": None, "z": 2}
    out = ct._relink_apply_new(d, {"spawnedFrom": {"sessionId": "local_p2", "taskId": TASK},
                                   "startChain": {"firstSessionId": "local_r2", "depth": 2}})
    assert list(out) == ["a", "spawnedFrom", "z", "startChain"]
    assert d == {"a": 1, "spawnedFrom": None, "z": 2}


def test_apply_old_pops_an_absent_key_and_restores_a_non_object_exactly():
    cur = {"a": 1, "spawnedFrom": {"sessionId": "local_p2", "taskId": TASK}, "z": 2}
    assert ct._relink_apply_old(cur, {"spawnedFrom": {"present": False, "value": None}}) \
        == {"a": 1, "z": 2}
    out = ct._relink_apply_old(cur, {"spawnedFrom": {"present": True, "value": False}})
    assert list(out) == ["a", "spawnedFrom", "z"] and out["spawnedFrom"] is False
    assert ct._relink_apply_old(cur, {"spawnedFrom": {"present": False, "value": None}},
                                keys=()) == cur


def test_key_state_compares_by_value_and_type():
    new = {"firstSessionId": "local_r2", "depth": 1}
    absent = {"present": False, "value": None}
    assert ct._relink_key_state({"startChain": {"depth": 1, "firstSessionId": "local_r2"}},
                                "startChain", absent, new) == "new"       # member order ignored
    assert ct._relink_key_state({"startChain": {"firstSessionId": "local_r2", "depth": True}},
                                "startChain", absent, new) == "other"
    assert ct._relink_key_state({}, "startChain", absent, new) == "old"
    assert ct._relink_key_state({"startChain": None}, "startChain",
                                {"present": True, "value": None}, new) == "old"


def test_row_bytes_are_compact_json():
    assert ct._relink_row_bytes({"a": "é", "b": [1]}) == b'{"a":"\\u00e9","b":[1]}'


# ------------------------------------------------------- the resolution core
# Task 2: _relink_plan_rows and the judges it consults (spec "What a run
# plans", steps 1 to 6). Every store below is the fake cast's.

LQ = "0abc0000-7777-4000-8000-000000000011"   # another parent; never a real id
LR = "0bbb0000-6666-4000-8000-00000000000b"   # a chain root
CHAIN = {"firstSessionId": "local_r1", "depth": 2}
APP_KEYS = {"cwd": "C:/work/northwind", "model": "model-x", "permissionMode": "default",
            "lastFocusedAt": 1700000000000, "folderPlace": None, "isArchived": False}
            # the app's other row keys, fake values; Tasks 2 and 6 pass them as per-row keys

ROW_FIELDS = ["account", "label", "name", "dest_path", "store_path", "session_id", "title",
              "parent_title", "pre_sha256", "post_sha256", "old", "new", "kept", "lineage",
              "written"]
LEFT_FIELDS = ["account", "label", "name", "session_id", "title", "notes"]


def tool_link(row_id): return {"sessionId": row_id, "taskId": TASK}


def put_row(env, write_row, acct, stem, sid, base_title, **extra):
    """One row of conversation SID in ACCT, its sessionId its stem; EXTRA keys
    are set last, so they may override any of the base ones, its title included."""
    d = lrow(sid, stem, base_title)
    d.update(extra)
    return write_row(env, 0, acct, ORG[acct], stem, d)


def relink_store(env, write_transcript, write_row, link=None, chain=None, roots=False, **per_row):
    """P (LP, 'Northwind kickoff') and C (LC, 'ACME-REVIEW side task') each hold a row in A1, A2
    and A3: local_p1..3, local_c1..3, every sessionId its stem. local_c1 carries LINK (default
    side_link('local_p1')) and CHAIN when given; local_c2 and local_c3 carry no link. ROOTS adds
    LR rows local_r1..3 ('Northwind board'). PER_ROW maps a stem to extra keys for that row.
    Transcripts exist for LP, LC (and LR). Returns {stem: path}."""
    for sid in (LP, LC) + ((LR,) if roots else ()):
        write_transcript(env, "C--p", sid, t_entries())
    paths = {}
    for acct in (A1, A2, A3):
        n = NUM[acct]
        cast = [("local_p" + n, LP, "Northwind kickoff"),
                ("local_c" + n, LC, "ACME-REVIEW side task")]
        if roots:
            cast.append(("local_r" + n, LR, "Northwind board"))
        for stem, sid, title in cast:
            extra = {}
            if stem == "local_c1":
                extra["spawnedFrom"] = side_link("local_p1") if link is None else link
                if chain is not None:
                    extra["startChain"] = chain
            extra.update(per_row.get(stem, {}))
            paths[stem] = put_row(env, write_row, acct, stem, sid, title, **extra)
    assert set(per_row) <= set(paths), "PER_ROW names a stem the store does not hold"
    return paths


def lq_rows(env, write_transcript, write_row, **per_row):
    """Q (LQ, 'Northwind follow-up') holds local_q1..3 in A1..A3, with its
    transcript. PER_ROW as relink_store's. Returns {stem: path}."""
    write_transcript(env, "C--p", LQ, t_entries())
    paths = {}
    for acct in (A1, A2, A3):
        stem = "local_q" + NUM[acct]
        paths[stem] = put_row(env, write_row, acct, stem, LQ, "Northwind follow-up",
                              **per_row.get(stem, {}))
    assert set(per_row) <= set(paths)
    return paths


def plan_rows(env, scope=None):
    # _retitle_scan(env, live=_LIVE_UNRESOLVED, store_path="", why=...): every argument after env
    # has a default (live resolves through live_account), so env alone scans every account
    return ct._relink_plan_rows(ct._retitle_scan(env), ct._relink_tids(env),
                                scope or (lambda rec: True))


def by_name(entries):
    out = {e["name"]: e for e in entries}
    assert len(out) == len(entries), "two entries share a name; key these by account"
    return out


def note_codes(entry):
    return [n["code"] for n in entry["notes"]]


def row_codes(row):
    return [n["code"] for n in row["lineage"]["notes"]]


def raw_of(path):
    with open(path, "rb") as fh:
        return fh.read()


def data_of(path):
    return json.loads(raw_of(path).decode("utf-8"))


def set_keys(path, **changes):
    """Rewrite one row with CHANGES set (in place when present)."""
    d = data_of(path)
    d.update(changes)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(d, fh)


def test_a_missing_link_is_resolved_in_each_account_that_lacks_it(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    rows, left, cycles = plan_rows(env)
    assert [r["name"] for r in rows] == ["local_c2.json", "local_c3.json"] and left == []
    assert cycles == 0
    c2 = rows[0]
    assert list(c2) == ROW_FIELDS
    assert c2["account"] == A2 and c2["session_id"] == LC and c2["label"]
    assert c2["dest_path"] == paths["local_c2"]
    assert c2["store_path"] == os.path.dirname(paths["local_c2"])
    assert c2["title"] == "ACME-REVIEW side task"
    assert c2["new"] == {"spawnedFrom": {"sessionId": "local_p2", "taskId": "task_7f"}}
    assert c2["old"] == {"spawnedFrom": {"present": False, "value": None}} and c2["kept"] == {}
    assert c2["lineage"]["parent_row"] == "local_p2" and c2["lineage"]["parent_session"] == LP
    assert c2["lineage"]["carried_keys"] == ["sessionId", "taskId"]
    assert c2["lineage"]["dropped_keys"] == ["sideSession", "title"]
    assert c2["lineage"]["root_row"] == "" == c2["lineage"]["root_session"]
    assert c2["lineage"]["parent_planned"] is False is c2["lineage"]["root_planned"]
    assert c2["lineage"]["notes"] == []
    assert c2["parent_title"] == "Northwind kickoff" and c2["written"] is False
    assert c2["pre_sha256"] == hashlib.sha256(raw_of(paths["local_c2"])).hexdigest()
    post = ct._relink_apply_new(data_of(paths["local_c2"]), c2["new"])
    assert c2["post_sha256"] == hashlib.sha256(ct._relink_row_bytes(post)).hexdigest()
    assert rows[1]["new"] == {"spawnedFrom": {"sessionId": "local_p3", "taskId": TASK}}


def test_the_chain_comes_with_the_link(mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    relink_store(env, write_transcript, write_row, chain=CHAIN, roots=True)
    rows, left, _ = plan_rows(env)
    assert [r["name"] for r in rows] == ["local_c2.json", "local_c3.json"] and left == []
    assert rows[0]["new"] == {"spawnedFrom": {"sessionId": "local_p2", "taskId": TASK},
                              "startChain": {"firstSessionId": "local_r2", "depth": 2}}
    assert rows[0]["old"]["startChain"] == {"present": False, "value": None}
    assert rows[0]["lineage"]["root_row"] == "local_r2"
    assert rows[0]["lineage"]["root_session"] == LR


@pytest.mark.parametrize("v", [None, False, "local_p2", []])
def test_a_non_object_link_is_missing_and_replaced_where_it_stands(
        v, mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    relink_store(env, write_transcript, write_row)
    data = {"sessionId": "local_c2", "spawnedFrom": v, "cliSessionId": LC,
            "title": "ACME-REVIEW side task", "lastActivityAt": 1_755_000_000_000}
    write_row(env, 0, A2, ORG[A2], "local_c2", data)
    rows, left, _ = plan_rows(env)
    c2 = by_name(rows)["local_c2.json"]
    assert c2["old"] == {"spawnedFrom": {"present": True, "value": v}}
    assert c2["new"] == {"spawnedFrom": {"sessionId": "local_p2", "taskId": TASK}}
    assert list(ct._relink_apply_new(data, c2["new"])) == list(data)
    assert "local_c3.json" in by_name(rows) and left == []


def test_an_existing_link_object_is_never_replaced(mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    relink_store(env, write_transcript, write_row,
                 local_c3={"spawnedFrom": side_link("local_p3")})
    rows, left, _ = plan_rows(env)
    assert [r["name"] for r in rows] == ["local_c2.json"]
    assert all(e["name"] != "local_c3.json" for e in left)


def test_a_kept_agreeing_link_beside_a_chain_gap_gains_the_chain(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    relink_store(env, write_transcript, write_row, chain=CHAIN, roots=True,
                 local_c3={"spawnedFrom": side_link("local_p3")})
    rows, left, _ = plan_rows(env)
    c3 = by_name(rows)["local_c3.json"]
    assert c3["new"] == {"startChain": {"firstSessionId": "local_r3", "depth": 2}}
    assert c3["old"] == {"startChain": {"present": False, "value": None}}
    assert c3["kept"] == {"spawnedFrom": side_link("local_p3")}
    lin = c3["lineage"]
    assert lin["parent_row"] == "local_p3" and lin["parent_session"] == LP
    assert lin["carried_keys"] == ["sessionId", "taskId", "title", "sideSession"]
    assert lin["dropped_keys"] == []
    assert lin["root_row"] == "local_r3" and lin["root_session"] == LR
    assert c3["parent_title"] == "Northwind kickoff" and left == []


def test_a_kept_link_record_names_the_kept_target_not_the_pick(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    relink_store(env, write_transcript, write_row, chain=CHAIN, roots=True,
                 local_c3={"spawnedFrom": side_link("local_p3")})
    put_row(env, write_row, A3, "local_p3b", LP, "Northwind kickoff, later copy",
            lastActivityAt=1_756_000_000_000)
    rows, left, _ = plan_rows(env)
    c3 = by_name(rows)["local_c3.json"]
    assert c3["new"] == {"startChain": {"firstSessionId": "local_r3", "depth": 2}}
    lin = c3["lineage"]
    assert lin["parent_row"] == "local_p3" and lin["parent_session"] == LP
    assert lin["carried_keys"] == ["sessionId", "taskId", "title", "sideSession"]
    assert lin["dropped_keys"] == []
    assert c3["parent_title"] == "Northwind kickoff"
    # the store is one where the pick and the kept target differ
    a3_p = [ct.LineageRow(r.account, r.name, r.data) for r in ct._retitle_scan(env)
            if r.account == A3 and r.data.get("cliSessionId") == LP]
    assert ct._lineage_pick(a3_p).name == "local_p3b.json"


def test_a_chain_only_repair_counts_the_rows_own_app_written_link(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    relink_store(env, write_transcript, write_row, link=tool_link("local_p1"), chain=CHAIN,
                 roots=True, local_c3={"spawnedFrom": side_link("local_p3")})
    rows, left, _ = plan_rows(env)
    got = by_name(rows)
    assert got["local_c3.json"]["new"] == {
        "startChain": {"firstSessionId": "local_r3", "depth": 2}}
    assert got["local_c2.json"]["new"] == {
        "spawnedFrom": {"sessionId": "local_p2", "taskId": TASK},
        "startChain": {"firstSessionId": "local_r2", "depth": 2}}
    assert left == []


def test_a_kept_link_with_another_parent_is_the_conversations_disagreement(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    relink_store(env, write_transcript, write_row,
                 local_c3={"spawnedFrom": side_link("local_q3")})
    lq_rows(env, write_transcript, write_row)
    rows, left, _ = plan_rows(env)
    assert rows == []
    assert [e["name"] for e in left] == ["local_c1.json", "local_c2.json", "local_c3.json"]
    for e in left:
        assert list(e) == LEFT_FIELDS
        assert note_codes(e) == ["lineage_parent_disagree"]
        assert e["notes"][0]["detail"] == "holders name 2 different parents"
    # the judgement reads every account whatever the scope narrows to, so the
    # selected row is listed even when its own link is the only one it sees
    for acct, name in ((A2, "local_c2.json"), (A3, "local_c3.json")):
        rows, left, _ = plan_rows(env, scope=lambda rec, acct=acct: rec.account == acct)
        assert rows == []
        assert [(e["name"], note_codes(e)) for e in left] == [
            (name, ["lineage_parent_disagree"])]


def test_a_kept_link_with_another_task_id_is_the_conversations_disagreement(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    relink_store(env, write_transcript, write_row,
                 local_c3={"spawnedFrom": side_link("local_p3", taskId="task_9z")})
    rows, left, _ = plan_rows(env)
    assert rows == []
    assert [e["name"] for e in left] == ["local_c1.json", "local_c2.json", "local_c3.json"]
    for e in left:
        assert note_codes(e) == ["lineage_task_disagree"]
        assert e["notes"][0]["detail"] == "holders give 2 different taskIds for parent 9aaa0000"


@pytest.mark.parametrize("kept", [{}, tool_link("local_p1")])
def test_a_kept_empty_object_or_another_accounts_row_is_own_link_differs(
        kept, mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    relink_store(env, write_transcript, write_row, local_c3={"spawnedFrom": kept})
    rows, left, _ = plan_rows(env)
    assert [e["name"] for e in left] == ["local_c3.json"]
    assert note_codes(left[0]) == ["own_link_differs"]
    assert left[0]["notes"][0]["detail"] == "the row's own link names no row in its account"
    assert [r["name"] for r in rows] == ["local_c2.json"]


@pytest.mark.parametrize("kept", [{"sessionId": "local_p3", "sideSession": True,
                                   "title": "Northwind"},
                                  {"sessionId": "local_p3", "taskId": ""}])
def test_a_kept_link_without_a_usable_task_id_is_own_link_differs(
        kept, mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    relink_store(env, write_transcript, write_row, local_c3={"spawnedFrom": kept})
    rows, left, _ = plan_rows(env)
    assert [e["name"] for e in left] == ["local_c3.json"]
    assert note_codes(left[0]) == ["own_link_differs"]
    assert "carries no usable taskId" in left[0]["notes"][0]["detail"]
    assert [r["name"] for r in rows] == ["local_c2.json"]


def test_a_disagreement_or_own_link_differs_counts_the_chain_left_out(
        mkenv, tmp_path, write_transcript, write_row):
    # (a) the conversation's links disagree
    env = mkenv(tmp_path / "a")
    relink_store(env, write_transcript, write_row, chain=CHAIN, roots=True,
                 local_c3={"spawnedFrom": side_link("local_q3")})
    lq_rows(env, write_transcript, write_row)
    rows, left, _ = plan_rows(env)
    assert rows == []
    got = {e["name"]: note_codes(e) for e in left}
    assert got == {"local_c1.json": ["lineage_parent_disagree"],     # it holds the chain itself
                   "local_c2.json": ["lineage_parent_disagree", "lineage_chain_dropped"],
                   "local_c3.json": ["lineage_parent_disagree", "lineage_chain_dropped"]}
    assert by_name(left)["local_c2.json"]["notes"][1]["detail"] == "the parent link was left out"
    # (b) the row's own link names no usable row
    env = mkenv(tmp_path / "b")
    relink_store(env, write_transcript, write_row, chain=CHAIN, roots=True,
                 local_c3={"spawnedFrom": {}})
    rows, left, _ = plan_rows(env)
    assert note_codes(by_name(left)["local_c3.json"]) == ["own_link_differs",
                                                          "lineage_chain_dropped"]
    assert [r["name"] for r in rows] == ["local_c2.json"]


def test_a_row_keeping_its_own_chain_never_counts_a_chain_left_out(
        mkenv, tmp_path, write_transcript, write_row):
    own = {"startChain": {"firstSessionId": "local_r3", "depth": 2}}
    # the row gains its link only, and the record names the judged kept chain
    env = mkenv(tmp_path / "base")
    relink_store(env, write_transcript, write_row, chain=CHAIN, roots=True, local_c3=own)
    rows, _, _ = plan_rows(env)
    c3 = by_name(rows)["local_c3.json"]
    assert c3["new"] == {"spawnedFrom": {"sessionId": "local_p3", "taskId": TASK}}
    assert c3["kept"] == own
    assert c3["lineage"]["root_row"] == "local_r3" and c3["lineage"]["root_session"] == LR
    assert c3["lineage"]["dropped_keys"] == ["sideSession", "title"]
    assert c3["lineage"]["notes"] == []
    # (a) its parent has no row in its account
    env = mkenv(tmp_path / "a")
    paths = relink_store(env, write_transcript, write_row, chain=CHAIN, roots=True,
                         local_c3=own)
    os.unlink(paths["local_p3"])
    _, left, _ = plan_rows(env)
    assert note_codes(by_name(left)["local_c3.json"]) == ["parent_not_in_account"]
    # (b) only tool copies back its link
    env = mkenv(tmp_path / "b")
    relink_store(env, write_transcript, write_row, link=tool_link("local_p1"), chain=CHAIN,
                 roots=True, local_c3=own)
    _, left, _ = plan_rows(env)
    c3 = by_name(left)["local_c3.json"]
    assert note_codes(c3) == ["lineage_no_app_link"]
    assert not any(c in ct.RELINK_CHAIN_CODES for c in note_codes(c3))   # not on the chains line


def test_a_candidate_whose_holders_name_no_usable_row_is_in_neither_list(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    relink_store(env, write_transcript, write_row, link=side_link("local_zz"))
    rows, left, _ = plan_rows(env)
    assert rows == []
    assert [e["name"] for e in left] == ["local_c1.json"]
    assert note_codes(left[0]) == ["own_link_differs"]
    assert all(e["notes"] for e in left)


def test_a_kept_chain_with_no_holder_chain_stands_outside_the_record(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    kept = {"firstSessionId": "local_r2", "depth": 5}
    relink_store(env, write_transcript, write_row, roots=True, local_c2={"startChain": kept})
    rows, left, _ = plan_rows(env)
    c2 = by_name(rows)["local_c2.json"]
    assert c2["new"] == {"spawnedFrom": {"sessionId": "local_p2", "taskId": TASK}}
    assert c2["kept"] == {"startChain": kept}
    assert c2["lineage"]["root_row"] == "" == c2["lineage"]["root_session"]
    assert c2["lineage"]["dropped_keys"] == ["sideSession", "title"]
    assert c2["lineage"]["notes"] == []
    # local_c1 keeps its link and now has a holder chain, local_c2's; its
    # holders yield no link, so the resolver leaves the chain out (its own
    # lineage_chain_dropped alone)
    c1 = by_name(left)["local_c1.json"]
    assert note_codes(c1) == ["lineage_chain_dropped"]
    assert c1["notes"][0]["detail"] == "the parent link was left out"


@pytest.mark.parametrize("kept", [{"firstSessionId": "local_q2", "depth": 2},
                                  {"firstSessionId": "local_r2", "depth": 3}])
def test_a_kept_chain_with_the_wrong_root_or_depth_is_own_chain_differs(
        kept, mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    relink_store(env, write_transcript, write_row, chain=CHAIN, roots=True,
                 local_c2={"startChain": kept})
    lq_rows(env, write_transcript, write_row)
    rows, left, _ = plan_rows(env)
    assert note_codes(by_name(left)["local_c2.json"]) == ["own_chain_differs"]
    assert "local_c2.json" not in by_name(rows)
    # local_c3 gains its link. local_c2's kept chain is one of local_c3's chain
    # sources, and the unchanged resolver drops a chain whose sources disagree,
    # so local_c3's chain is left out with the resolver's own reason.
    c3 = by_name(rows)["local_c3.json"]
    assert c3["new"] == {"spawnedFrom": {"sessionId": "local_p3", "taskId": TASK}}
    assert row_codes(c3) == ["lineage_chain_dropped"]
    assert "local_c3.json" not in by_name(left)
    # a depth of True never agrees with 1, as a holder's or as the row's own
    idx = {A2: ct._lineage_index([ct.LineageRow(A2, "local_r2.json", {
        "sessionId": "local_r2", "cliSessionId": LR})])}

    def kept_at(depth):
        return ct.LineageRow(A2, "local_c2.json",
                             {"startChain": {"firstSessionId": "local_r2", "depth": depth}})
    assert ct._relink_chain_agrees(kept_at(1), idx, (LR, 1))
    assert not ct._relink_chain_agrees(kept_at(True), idx, (LR, 1))
    assert ct._relink_agreed_chain([kept_at(1)], idx) == (LR, 1)
    assert ct._relink_agreed_chain([kept_at(True)], idx) is None


def test_a_link_left_out_drops_its_chain(mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row, chain=CHAIN, roots=True)
    os.unlink(paths["local_p3"])
    rows, left, _ = plan_rows(env)
    assert note_codes(by_name(left)["local_c3.json"]) == ["parent_not_in_account",
                                                          "lineage_chain_dropped"]
    assert [r["name"] for r in rows] == ["local_c2.json"]


def test_two_rows_of_one_conversation_in_one_account_are_each_repaired(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    relink_store(env, write_transcript, write_row)
    put_row(env, write_row, A2, "local_c2b", LC, "ACME-REVIEW side task")
    rows, left, _ = plan_rows(env)
    assert [r["name"] for r in rows] == ["local_c2.json", "local_c2b.json", "local_c3.json"]
    for r in rows[:2]:
        assert r["new"] == {"spawnedFrom": {"sessionId": "local_p2", "taskId": TASK}}
    assert left == []


def test_archived_rows_and_an_archived_parent_row_are_included(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    relink_store(env, write_transcript, write_row, local_c2={"isArchived": True},
                 local_p3={"isArchived": True})
    rows, left, _ = plan_rows(env)
    assert [r["name"] for r in rows] == ["local_c2.json", "local_c3.json"] and left == []
    assert rows[1]["new"] == {"spawnedFrom": {"sessionId": "local_p3", "taskId": TASK}}


def _reason_parent_disagree(s):
    lq_rows(s.env, s.write_transcript, s.write_row)
    set_keys(s.paths["local_c3"], spawnedFrom=side_link("local_q3"))


def _reason_self(s):
    put_row(s.env, s.write_row, A1, "local_c1b", LC, "ACME-REVIEW side task")
    set_keys(s.paths["local_c1"], spawnedFrom=side_link("local_c1b"))


def _reason_parent_dead(s):
    os.unlink(os.path.join(s.env.projects_root, "C--p", LP + ".jsonl"))


def _reason_not_in_account(s):
    os.unlink(s.paths["local_p3"])


def _reason_drifted(s):
    os.unlink(s.paths["local_p3"])
    put_row(s.env, s.write_row, A3, "local_p1", LQ, "Northwind follow-up")


def _reason_variant(s):
    set_keys(s.paths["local_c1"],
             spawnedFrom={"sessionId": "local_p1", "taskId": TASK, "pendingPop": True})
    put_row(s.env, s.write_row, A2, "local_c2b", LC, "ACME-REVIEW side task",
            spawnedFrom=tool_link("local_p2"))


def _reason_no_task(s):
    set_keys(s.paths["local_c1"],
             spawnedFrom={k: v for k, v in side_link("local_p1").items() if k != "taskId"})


def _reason_task_disagree(s):
    set_keys(s.paths["local_c3"], spawnedFrom=side_link("local_p3", taskId="task_9z"))


def _reason_cycle(s):
    set_keys(s.paths["local_p2"], spawnedFrom=side_link("local_c2"))


REASONS = [
    ("lineage_parent_disagree", "local_c2", _reason_parent_disagree),
    ("lineage_self", "local_c2", _reason_self),
    ("lineage_parent_dead", "local_c2", _reason_parent_dead),
    ("parent_not_in_account", "local_c3", _reason_not_in_account),
    ("parent_row_drifted", "local_c3", _reason_drifted),
    ("lineage_variant", "local_c2", _reason_variant),
    ("lineage_no_task", "local_c2", _reason_no_task),
    ("lineage_task_disagree", "local_c2", _reason_task_disagree),
    ("lineage_cycle", "local_c2", _reason_cycle),
]


@pytest.mark.parametrize("code, row, setup", REASONS, ids=[c for c, _, _ in REASONS])
def test_every_reachable_0_18_reason_reaches_the_plan(
        code, row, setup, mkenv, tmp_path, write_transcript, write_row):
    # parent_pair_held and lineage_source_changed cannot arise (relink holds nothing; it is
    # sync's); own_link_differs and lineage_no_app_link are relink's own, tested above
    assert {c for c, _, _ in REASONS} == ct.LINEAGE_LEFT_OUT - {
        "parent_pair_held", "lineage_source_changed", "own_link_differs", "lineage_no_app_link"}
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    rows, _, _ = plan_rows(env)
    assert row + ".json" in by_name(rows)          # the base store plans ROW
    setup(types.SimpleNamespace(env=env, paths=paths, write_row=write_row,
                                write_transcript=write_transcript))
    rows, left, _ = plan_rows(env)
    assert row + ".json" not in by_name(rows)
    assert note_codes(by_name(left)[row + ".json"])[0] == code


def test_cycles_are_dropped_against_existing_edges_a_kept_link_counting(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    relink_store(env, write_transcript, write_row,
                 local_p2={"spawnedFrom": side_link("local_c2")})
    rows, left, cycles = plan_rows(env)
    c2 = by_name(left)["local_c2.json"]
    assert note_codes(c2) == ["lineage_cycle"]
    assert "would close a cycle" in c2["notes"][0]["detail"]
    assert "local_c3.json" in by_name(rows)
    assert cycles == 0
    # a chain root that would close a cycle: the link is written without it
    env = mkenv(tmp_path / "chain")
    relink_store(env, write_transcript, write_row, chain=CHAIN, roots=True,
                 local_r2={"spawnedFrom": side_link("local_c2")})
    rows, _, _ = plan_rows(env)
    c2 = by_name(rows)["local_c2.json"]
    assert c2["new"] == {"spawnedFrom": {"sessionId": "local_p2", "taskId": TASK}}
    assert row_codes(c2) == ["lineage_chain_dropped"]
    assert c2["lineage"]["root_row"] == ""
    # a chain-only proposal whose chain would close a cycle is left out
    env = mkenv(tmp_path / "chain_only")
    relink_store(env, write_transcript, write_row, chain=CHAIN, roots=True,
                 local_c3={"spawnedFrom": side_link("local_p3")},
                 local_r3={"spawnedFrom": side_link("local_c3")})
    rows, left, _ = plan_rows(env)
    assert "local_c3.json" not in by_name(rows)
    c3 = by_name(left)["local_c3.json"]
    assert note_codes(c3) == ["lineage_chain_dropped"]
    assert "would close a cycle" in c3["notes"][0]["detail"]


def test_a_left_out_entrys_link_code_comes_first(mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row, chain=CHAIN, roots=True,
                         local_p2={"spawnedFrom": side_link("local_c2")})
    os.unlink(paths["local_r2"])
    _, left, _ = plan_rows(env)
    c2 = by_name(left)["local_c2.json"]
    assert note_codes(c2) == ["lineage_cycle", "lineage_chain_dropped"]
    # the chain note is the resolver's, which came before the cycle pass's
    assert c2["notes"][1]["detail"].startswith("no row for chain root")


def test_a_left_out_rows_link_still_counts_in_the_cycle_graph(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    relink_store(env, write_transcript, write_row,
                 local_p3={"spawnedFrom": side_link("local_q3")})
    lq_rows(env, write_transcript, write_row,
            local_q1={"spawnedFrom": side_link("local_p1")},
            local_q3={"spawnedFrom": side_link("local_c3")})
    rows, left, _ = plan_rows(env)
    got = by_name(left)
    for name in ("local_q1.json", "local_q2.json", "local_q3.json"):
        assert note_codes(got[name]) == ["lineage_parent_disagree"]
    assert note_codes(got["local_c3.json"]) == ["lineage_cycle"]
    assert "local_c2.json" in by_name(rows)


def test_an_existing_cycle_is_counted_once(mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    relink_store(env, write_transcript, write_row)
    put_row(env, write_row, A3, "local_x3", LX, "Northwind loop",
            spawnedFrom=side_link("local_y3"))
    put_row(env, write_row, A3, "local_y3", LY, "Northwind loop partner",
            spawnedFrom=side_link("local_x3"))
    rows, left, cycles = plan_rows(env)
    assert cycles == 1
    assert [r["name"] for r in rows] == ["local_c2.json", "local_c3.json"] and left == []
    # every account's graph is counted, whatever the scope
    assert plan_rows(env, scope=lambda rec: False) == ([], [], 1)


def test_a_link_backed_only_by_tool_copies_is_left_out_with_its_chain(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    relink_store(env, write_transcript, write_row, link=tool_link("local_p1"), chain=CHAIN,
                 roots=True)
    rows, left, _ = plan_rows(env)
    assert rows == []
    assert [e["name"] for e in left] == ["local_c2.json", "local_c3.json"]
    for e in left:
        assert note_codes(e) == ["lineage_no_app_link", "lineage_chain_dropped"]
        assert "no app-written link" in e["notes"][0]["detail"]
        assert e["notes"][1]["detail"] == "the parent link was left out"
    summary = ct._lineage_summary([{"lineage": {"notes": e["notes"]}} for e in left])
    assert ct._lineage_links_line(summary, carried="to add") == \
        "links   : 2 left out (no app-written link backs it 2)"
    # a row keeping a tool copy gains no chain from tool copies alone (D11
    # for the chain counts the row's own link, here not app-written either)
    env = mkenv(tmp_path / "kept")
    relink_store(env, write_transcript, write_row, link=tool_link("local_p1"), chain=CHAIN,
                 roots=True, local_c3={"spawnedFrom": tool_link("local_p3")})
    rows, left, _ = plan_rows(env)
    assert rows == []
    c3 = by_name(left)["local_c3.json"]
    assert note_codes(c3) == ["lineage_chain_dropped"]
    assert c3["notes"][0]["detail"] == "no app-written link backs the chain"
    # an app-written link on another row of the same account is not a holder,
    # so it backs the other accounts' rows but not its own account's
    env = mkenv(tmp_path / "sibling")
    relink_store(env, write_transcript, write_row, link=tool_link("local_p1"))
    put_row(env, write_row, A2, "local_c2b", LC, "ACME-REVIEW side task",
            spawnedFrom=side_link("local_p2"))
    rows, left, _ = plan_rows(env)
    assert [r["name"] for r in rows] == ["local_c3.json"]
    assert [(e["name"], note_codes(e)) for e in left] == [
        ("local_c2.json", ["lineage_no_app_link"])]


def test_one_app_written_yielding_holder_is_enough(mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    relink_store(env, write_transcript, write_row, link=tool_link("local_p1"),
                 local_c3={"spawnedFrom": side_link("local_p3")})
    rows, left, _ = plan_rows(env)
    assert [r["name"] for r in rows] == ["local_c2.json"] and left == []
    assert rows[0]["new"] == {"spawnedFrom": {"sessionId": "local_p2", "taskId": TASK}}


@pytest.mark.parametrize("cleared_in, code", [("A3", "lineage_no_app_link"),
                                              ("A2", "lineage_variant")])
def test_a_titled_link_with_an_empty_title_is_not_app_written(
        cleared_in, code, mkenv, tmp_path, write_transcript, write_row):
    empty = {"sessionId": "local_p1", "taskId": TASK, "title": ""}
    assert not ct._relink_app_written(empty) and not ct._lineage_variant_ok(empty)
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row, link=empty)
    # without a cleared copy the conversation is not planned at all
    assert plan_rows(env) == ([], [], 0)
    if cleared_in == "A3":
        set_keys(paths["local_c3"], spawnedFrom=tool_link("local_p3"))
    else:
        put_row(env, write_row, A2, "local_c2b", LC, "ACME-REVIEW side task",
                spawnedFrom=tool_link("local_p2"))
    rows, left, _ = plan_rows(env)
    assert "local_c2.json" not in by_name(rows)
    assert note_codes(by_name(left)["local_c2.json"]) == [code]


def test_the_new_value_is_the_old_with_only_the_written_keys_set(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row, local_c2=APP_KEYS)
    rows, _, _ = plan_rows(env)
    c2 = by_name(rows)["local_c2.json"]
    data = data_of(paths["local_c2"])
    assert set(APP_KEYS) <= set(data)
    post = ct._relink_apply_new(data, c2["new"])
    assert {k: post[k] for k in data} == data
    assert list(post) == list(data) + ["spawnedFrom"]
    assert c2["post_sha256"] == hashlib.sha256(ct._relink_row_bytes(post)).hexdigest()


def test_a_key_not_written_keeps_its_null(mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row, local_c2={"startChain": None})
    rows, _, _ = plan_rows(env)
    c2 = by_name(rows)["local_c2.json"]
    assert "startChain" not in c2["new"] and list(c2["old"]) == ["spawnedFrom"]
    post = ct._relink_apply_new(data_of(paths["local_c2"]), c2["new"])
    assert "startChain" in post and post["startChain"] is None


def test_a_non_string_title_is_journaled_as_empty(mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    relink_store(env, write_transcript, write_row, local_c2={"title": 5},
                 local_p2={"title": None})
    rows, _, _ = plan_rows(env)
    c2 = by_name(rows)["local_c2.json"]
    assert c2["title"] == "" and c2["parent_title"] == ""


def test_a_stem_mismatched_row_is_repaired_and_never_a_target(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    # local_c2's sessionId is not its stem; P's only A3 row's sessionId is not
    # its stem; local_c3's spawnedFrom is a string naming Q's A3 row, which
    # would read as a second parent if a string link yielded anything
    relink_store(env, write_transcript, write_row, local_c2={"sessionId": "local-x"},
                 local_p3={"sessionId": "local-zz"}, local_c3={"spawnedFrom": "local_q3"})
    lq_rows(env, write_transcript, write_row)
    rows, left, _ = plan_rows(env)
    assert [r["name"] for r in rows] == ["local_c2.json"]
    assert rows[0]["new"] == {"spawnedFrom": {"sessionId": "local_p2", "taskId": TASK}}
    assert rows[0]["old"] == {"spawnedFrom": {"present": False, "value": None}}
    assert [(e["name"], note_codes(e)) for e in left] == [
        ("local_c3.json", ["parent_not_in_account"])]


# ------------------------------------------------------------------ the plan
# Task 3: plan_relink - the strict scan (D16), one store per account (D17),
# no unfinished operation (D18), the identity disclosure and the narrowing.

OX = "77777777-0000-0000-0000-000000000007"   # an empty org folder; never a real id
OY = "66666666-0000-0000-0000-000000000066"   # another empty one; never a real id
OPEN_ID = "20990101T000000Z-aaaaaa"           # a hand-made operation's id
D18_OPEN = ("relink waits while another operation is unfinished ({0}): an unfinished "
            "operation of any kind may yet roll back or finish over rows a relink reads or "
            "changes - a converge or sync rolled back after a relink, for one, could delete a "
            "parent row a new link names. One unfinished operation holds every relink, its dry "
            "run included, until it is recovered. Run 'claude-code-sessions recover' and finish "
            "it first. Nothing was changed.")


def names(entries):
    return [e["name"] for e in entries]


def plan(env, **flags):
    return ct.plan_relink(env, ct.RelinkFlags(**flags))


def refusal(env, **flags):
    with pytest.raises(ct.Refusal) as exc:
        plan(env, **flags)
    return str(exc.value)


def put_op(env, folder, body):
    """A hand-made journal record: BODY is the manifest (a dict) or its raw text."""
    d = os.path.join(env.ops_dir, folder)
    os.makedirs(d)
    with open(os.path.join(d, "manifest.json"), "w", encoding="utf-8") as fh:
        fh.write(body if isinstance(body, str) else json.dumps(body))


def open_op(op_id=OPEN_ID, status="writing", **extra):
    m = {"op_id": op_id, "op_type": "converge", "status": status,
         "history": [{"status": status, "at": 1_800_000_000.0}], "rows": []}
    m.update(extra)
    return m


def store_dir(env, acct, org):
    return os.path.join(env.store_candidates[0], acct, org)


def test_plan_relink_returns_the_journal_shape(mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    relink_store(env, write_transcript, write_row)
    m = plan(env)
    assert set(m) == {"op_type", "lineage_format", "only", "account_filter", "live_asserted",
                      "lineage_cycles", "rows", "left_out"}
    assert m["op_type"] == "relink" and m["lineage_format"] == 1
    assert m["only"] == "" == m["account_filter"] and m["live_asserted"] == ""
    assert names(m["rows"]) == ["local_c2.json", "local_c3.json"] and m["left_out"] == []
    assert all(r["written"] is False for r in m["rows"])
    assert (m["rows"], m["left_out"], m["lineage_cycles"]) == plan_rows(env)
    # the flags are journaled as typed: the apply's locked replan passes them back
    m = plan(env, only="local_c2", account=A2[:8])
    assert m["only"] == "local_c2" and m["account_filter"] == A2[:8]


def test_only_by_row_id_narrows_to_that_row(mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    relink_store(env, write_transcript, write_row)
    assert names(plan(env, only="local_c2")["rows"]) == ["local_c2.json"]
    assert names(plan(env, only="LOCAL_C3")["rows"]) == ["local_c3.json"]   # any case
    # a prefix every account's row shares names one row within --account's store
    assert names(plan(env, only="local_c", account=A3[:8])["rows"]) == ["local_c3.json"]


def test_only_by_conversation_with_two_rows_to_change_refuses(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    head, *lines = refusal(env, only="ACME-REVIEW").split("\n")
    assert head == ("--only 'ACME-REVIEW' leaves 2 rows to change; name one by its row id "
                    "(with --account when two accounts hold that id), so a proof touches one "
                    "row:")
    assert lines == ["   local_c2   cccccccc (cccccccc/dddddddd)",
                     "   local_c3   eeeeeeee (eeeeeeee/ffffffff)"]
    assert names(plan(env, only=LC[:8], account=A2[:8])["rows"]) == ["local_c2.json"]
    # a row that would be left out does not count: one row to change is not refused
    os.unlink(paths["local_p3"])
    m = plan(env, only="ACME-REVIEW")
    assert names(m["rows"]) == ["local_c2.json"]
    assert [(e["name"], note_codes(e)) for e in m["left_out"]] == [
        ("local_c3.json", ["parent_not_in_account"])]


def test_only_refuses_no_match_and_an_ambiguous_one(mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    assert refusal(env, only="Nothing-matches") == (
        "no row in any account matches --only 'Nothing-matches' (a title substring, or a "
        "cliSessionId prefix).")
    assert refusal(env, only="local_zz") == (
        "no row matches --only 'local_zz' (a row id is local_ and a prefix of its uuid).")
    assert refusal(env, only="local_c1", account=A2[:8]).startswith(
        "no row matches --only 'local_c1'")
    head, *lines = refusal(env, only="local_c").split("\n")
    assert head == ("--only 'local_c' matches 3 rows; give more of the id, or add --account "
                    "to name its account:")                 # the rows span three accounts
    assert [ln.split()[0] for ln in lines] == ["local_c1", "local_c2", "local_c3"]
    assert lines[0] == "   local_c1   aaaaaaaa (aaaaaaaa/bbbbbbbb)   ACME-REVIEW side task"
    # rows of one account are told apart by more of the id alone
    extra = put_row(env, write_row, A2, "local_c2b", LC, "ACME-REVIEW side task")
    head, *lines = refusal(env, only="local_c2", account=A2[:8]).split("\n")
    assert head == "--only 'local_c2' matches 2 rows; give more of the id:"
    assert [ln.split()[0] for ln in lines] == ["local_c2", "local_c2b"]
    os.unlink(extra)
    # a second conversation whose title holds the first one's
    write_transcript(env, "C--p", LD, t_entries())
    put_row(env, write_row, A1, "local_d1", LD, "ACME-REVIEW side task, round two")
    head, *lines = refusal(env, only="ACME-REVIEW").split("\n")
    assert head == ("--only 'ACME-REVIEW' matches 2 conversations; give the cliSessionId of "
                    "the one you mean (a prefix is enough):")
    assert [ln.split()[0] for ln in lines] == [LC[:8], LD[:8]]
    # the conversation is matched over every account, whatever --account narrows: LD has
    # rows in A1 only, and still makes the title ambiguous within A2
    assert refusal(env, only="ACME-REVIEW", account=A2[:8]).split("\n")[0] == head
    # a newest row whose title is not a string lists as untitled, without a traceback
    set_keys(paths["local_c3"], title=5, lastActivityAt=1_756_000_000_000)
    head, *lines = refusal(env, only="0").split("\n")        # LC and LD both start with 0
    assert head.startswith("--only '0' matches 2 conversations")
    assert lines[0].split()[:2] == [LC[:8], "(untitled)"]


def test_only_with_no_candidate_plans_nothing(mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    relink_store(env, write_transcript, write_row)
    # P's rows carry no link to resolve; local_c1 carries its own
    for only in ("Northwind kickoff", "local_c1"):
        m = plan(env, only=only)
        assert m["rows"] == [] and m["left_out"] == [], only


def test_account_narrows_to_one_store_the_signed_in_one_included(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    relink_store(env, write_transcript, write_row)
    identity_files(env, A2, A2, email="bob@example.com")
    for value in ("bob@", "BOB@EXAMPLE", A2[:8]):
        assert names(plan(env, account=value)["rows"]) == ["local_c2.json"], value
    assert names(plan(env, account=O3[:8])["rows"]) == ["local_c3.json"]
    # A1's store path, in either separator and any case: its row carries the link already
    for value in (os.path.join("claude-code-sessions", A1, O1),
                  "claude-code-sessions/" + A1 + "/" + O1,
                  ("claude-code-sessions\\" + A1 + "\\" + O1).upper()):
        m = plan(env, account=value)
        assert m["rows"] == [] and m["left_out"] == [], value


def test_account_takes_the_printed_pair_and_a_trailing_separator(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    relink_store(env, write_transcript, write_row)
    # the listing's own label pasted back: its 8 characters, shorter prefixes, the full ids
    for value in (A2[:8] + "/" + O2[:8], A2[:4] + "/" + O2[:2], (A2 + "/" + O2).upper()):
        assert names(plan(env, account=value)["rows"]) == ["local_c2.json"], value
    # a pair is parsed, never searched: A1's folded store path holds this mid-uuid fragment
    fragment = A1[-4:] + "/" + O1[:4]
    assert fragment in (A1 + "/" + O1)
    assert refusal(env, account=fragment).startswith("no account store matches")
    # a Tab-completed store path keeps its trailing separator, in either spelling
    store = store_dir(env, A2, O2)
    other = "/" if os.sep == "\\" else "\\"
    for value in (store + os.sep, store.replace(os.sep, other) + other):
        assert names(plan(env, account=value)["rows"]) == ["local_c2.json"], value


def test_a_row_id_two_accounts_hold_refuses_toward_account(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    relink_store(env, write_transcript, write_row)
    # the same stem in A2 and A3, as two stores may legitimately hold it
    put_row(env, write_row, A3, "local_c2", LC, "ACME-REVIEW side task")
    head, *lines = refusal(env, only="local_c2").split("\n")
    assert head == ("--only 'local_c2' matches 2 rows; give more of the id, or add --account "
                    "to name its account:")
    assert lines == ["   local_c2   cccccccc (cccccccc/dddddddd)   ACME-REVIEW side task",
                     "   local_c2   eeeeeeee (eeeeeeee/ffffffff)   ACME-REVIEW side task"]
    m = plan(env, only="local_c2", account=A3[:8])
    assert [(r["account"], r["name"]) for r in m["rows"]] == [(A3, "local_c2.json")]
    # the conversation's three rows to change name the way out too
    head, *lines = refusal(env, only="ACME-REVIEW").split("\n")
    assert head == ("--only 'ACME-REVIEW' leaves 3 rows to change; name one by its row id "
                    "(with --account when two accounts hold that id), so a proof touches one "
                    "row:")
    assert lines == ["   local_c2   cccccccc (cccccccc/dddddddd)",
                     "   local_c2   eeeeeeee (eeeeeeee/ffffffff)",
                     "   local_c3   eeeeeeee (eeeeeeee/ffffffff)"]


def test_account_matches_empty_org_folders_without_ambiguity(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    relink_store(env, write_transcript, write_row)
    for org in (OX, OY):
        os.makedirs(store_dir(env, A2, org))
    assert names(plan(env, account=A2[:8])["rows"]) == ["local_c2.json"]
    m = plan(env, account=OX[:8])
    assert m["rows"] == [] and m["left_out"] == []
    # a signed-in account whose only org folder is empty
    env = mkenv(tmp_path / "a4")
    relink_store(env, write_transcript, write_row)
    os.makedirs(store_dir(env, A4, O4))
    identity_files(env, A4, A4, email="bob@example.com", oauth_org=O4)
    for value in (A4[:8], "bob@"):
        m = plan(env, account=value)
        assert m["rows"] == [] and m["left_out"] == [], value


def test_account_refuses_no_match_or_several_accounts(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    relink_store(env, write_transcript, write_row)
    identity_files(env, A2, A2, email="bob@example.com")
    head, *lines = refusal(env, account="zzzz").split("\n")
    assert head == ("no account store matches --account 'zzzz' (an account id, org id, store "
                    "path or email).")
    shown = (A1[:8], "bob@example.com", A3[:8])
    assert len(lines) == 3
    for line, acct, label in zip(lines, (A1, A2, A3), shown):
        assert line.startswith("   {0} ({1}/{2})   ".format(label, acct[:8], ORG[acct][:8]))
        assert line.endswith(os.path.join(acct, ORG[acct]))
    assert "   bob@example.com (cccccccc/dddddddd)   " in lines[1]
    head, *again = refusal(env, account="-0000-0000-").split("\n")
    assert head == "--account '-0000-0000-' matches 3 accounts; give more of it:"
    assert again == lines
    # under --anonymize the typed value and the email are scrubbed
    ct._ANONYMIZE = True
    msg = refusal(env, account="alice@example.com")       # no account here carries it
    assert msg.startswith("no account store matches --account '<account-filter>' (")
    assert "bob@" not in msg
    assert ct._relink_account_filter_display("") == ""
    ct._ANONYMIZE = False
    assert ct._relink_account_filter_display("bob@") == "bob@"


def test_a_selected_row_of_a_disagreeing_conversation_is_listed(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    relink_store(env, write_transcript, write_row,
                 local_c3={"spawnedFrom": side_link("local_q3")})
    lq_rows(env, write_transcript, write_row)
    for flags in ({"account": A2[:8]}, {"only": "local_c2"}):
        m = plan(env, **flags)
        assert m["rows"] == []
        assert [(e["name"], note_codes(e)) for e in m["left_out"]] == [
            ("local_c2.json", ["lineage_parent_disagree"])], flags


def test_an_unreadable_record_refuses_with_its_own_line(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    relink_store(env, write_transcript, write_row)
    bad = ["20990101T000000Z-cccccc", "20990101T000000Z-bbbbbb"]
    for folder in bad:
        put_op(env, folder, "{not json")
    # neither the lock file nor a folder without a manifest.json is an operation
    with open(os.path.join(env.ops_dir, "lock"), "w", encoding="utf-8") as fh:
        fh.write("4242 20990101T000000Z-dddddd")
    os.makedirs(os.path.join(env.ops_dir, "a stray folder"))
    unreadable = ("relink waits: the record(s) in 20990101T000000Z-bbbbbb, "
                  "20990101T000000Z-cccccc could not be read, so whether they are unfinished "
                  "cannot be told. Nothing was changed.")
    msg = refusal(env)
    assert msg == unreadable
    assert "(" + "the journal" not in msg
    assert ct._relink_open_others(env) == ([], sorted(bad))
    # beside an open operation the one refusal carries both lines, the open one's first
    put_op(env, OPEN_ID, open_op())
    assert refusal(env).split("\n") == [D18_OPEN.format(OPEN_ID), unreadable]
    assert ct._relink_open_others(env) == ([OPEN_ID], sorted(bad))
    # an operation's own undo or recovery passes its id and never waits on itself
    assert ct._relink_open_others(env, mine=OPEN_ID) == ([], sorted(bad))
    with pytest.raises(ct.Refusal) as exc:
        ct._relink_refuse_open(env, "relink's undo", mine=OPEN_ID)
    assert str(exc.value) == unreadable.replace("relink waits", "relink's undo waits")


def test_each_record_is_read_on_its_own_and_rerooted(mkenv, tmp_path):
    env = mkenv(tmp_path)
    old = os.path.join(str(tmp_path), "an old store")
    env.store_moves = [(old, env.store_candidates[0])]
    put_op(env, "r1", "[1]")                                       # not an object
    put_op(env, "r2", {"status": "writing"})                       # no op_id
    put_op(env, "r3", {"op_id": 7, "status": "writing"})           # an op_id not a string
    put_op(env, "r4", {"op_id": "", "status": "writing"})          # an empty op_id names nothing
    put_op(env, OPEN_ID, open_op(rows=[{"dest_path": os.path.join(old, A2, "local_c2.json")}]))
    done = "20990101T000000Z-eeeeee"
    put_op(env, done, open_op(done, status="completed"))
    got = ct._relink_load_ops(env)
    assert [name for name, _ in got] == sorted([OPEN_ID, done, "r1", "r2", "r3", "r4"])
    loaded = dict(got)
    assert [loaded[r] for r in ("r1", "r2", "r3", "r4")] == [None] * 4
    assert loaded[done]["status"] == "completed"
    assert loaded[OPEN_ID]["rows"][0]["dest_path"] == os.path.join(
        os.path.realpath(env.store_candidates[0]), A2, "local_c2.json")
    assert ct._relink_open_others(env) == ([OPEN_ID], ["r1", "r2", "r3", "r4"])


@pytest.mark.parametrize("bad", ["local_c2", "local_c1", "local_p2", "local_p3"])
def test_an_unreadable_candidate_holder_target_or_kept_target_refuses(
        bad, mkenv, tmp_path, write_transcript, write_row):
    # a candidate, a holder, a link's target, and the target local_c3's kept link names
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row,
                         local_c3={"spawnedFrom": side_link("local_p3")})
    with open(paths[bad], "w", encoding="utf-8") as fh:
        fh.write("{not json")
    for flags in ({}, {"account": A2[:8]}, {"only": "local_c2"}):   # the scan is never narrowed
        msg = refusal(env, **flags)
        assert "the row {0!r}".format(bad + ".json") in msg and "could not be read" in msg
        assert ct._RELINK_SCAN_WHY in msg


def test_an_account_in_two_org_folders_refuses(mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    relink_store(env, write_transcript, write_row)
    write_row(env, 0, A2, O4, "local_c2b", lrow(LC, "local_c2b", "ACME-REVIEW side task"))
    assert "account {0} holds rows in more than one org directory".format(A2[:8]) in \
        refusal(env)


def test_a_missing_journal_folder_means_no_operations(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    relink_store(env, write_transcript, write_row)
    shutil.rmtree(env.ops_dir)      # mkenv always creates it; in use only acquire_lock does
    assert names(plan(env)["rows"]) == ["local_c2.json", "local_c3.json"]
    assert ct._relink_open_others(env) == ([], [])
    assert ct._relink_load_ops(env) == []
    # a journal path that exists and cannot be listed refuses, on a line of its own
    with open(env.ops_dir, "w", encoding="utf-8") as fh:
        fh.write("not a folder")
    msg = refusal(env)
    assert msg.startswith("relink waits: the journal folder could not be listed (")
    assert msg.endswith("). Nothing was changed.") and "\n" not in msg
    with pytest.raises(OSError):
        ct._relink_load_ops(env)
    ids, unreadable = ct._relink_open_others(env)
    assert ids == [] and isinstance(unreadable, str) and unreadable


def test_an_unfinished_operation_refuses_the_plan(mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    relink_store(env, write_transcript, write_row)
    done = "20990101T000000Z-eeeeee"
    put_op(env, done, open_op(done, status="completed"))
    assert names(plan(env)["rows"]) == ["local_c2.json", "local_c3.json"]   # terminal: no wait
    put_op(env, OPEN_ID, open_op())
    msg = refusal(env)
    assert "relink waits while another operation is unfinished ({0})".format(OPEN_ID) in msg
    assert msg == D18_OPEN.format(OPEN_ID)
    assert refusal(env, only="local_c2") == msg              # the narrowed dry run waits too
    later = "20990101T000000Z-ffffff"
    put_op(env, later, open_op(later, status="journaled"))
    assert refusal(env) == D18_OPEN.format(OPEN_ID + ", " + later)


def test_the_plan_records_an_identity_disagreement(mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    relink_store(env, write_transcript, write_row)
    identity_files(env, A1, A2)
    m = plan(env)
    assert m["identity_disagreement"] == {"oauth": A1, "config": A2}
    assert m["live_asserted"] == "" and names(m["rows"]) == ["local_c2.json", "local_c3.json"]
    identity_files(env, A1, A1)
    assert "identity_disagreement" not in plan(env)


def test_live_is_recorded_only_when_it_arbitrates(mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    relink_store(env, write_transcript, write_row)
    # A1 has two org folders, so its email resolves only at account scope
    os.makedirs(store_dir(env, A1, OX))
    identity_files(env, A1, A2)
    for live in (A1[:8], "alice@example.com"):
        assert plan(env, live=live)["live_asserted"] == A1, live
    assert plan(env)["live_asserted"] == ""
    # a --live naming the account the files agree on arbitrates nothing
    identity_files(env, A1, A1, oauth_org=O1)
    assert plan(env, live="alice@example.com")["live_asserted"] == ""


# ---------------------------------------------------------------- the report
# Task 4: the per-account listing, the totals counted from the plan, and the
# public projection --json and --anonymize print (spec "What it prints").

PUB_ROW_FIELDS = ["account", "label", "name", "session_id", "title", "parent_title",
                  "store_path", "dest_path", "adds", "keeps", "lineage", "written"]
C_LINE = "   {0}  'ACME-REVIEW side task'   "          # a row line up to its last column
WRONG_DEPTH = {"startChain": {"firstSessionId": "local_r2", "depth": 3}}
SESSION_LABEL = r"<session-[0-9a-f]{4}>"


def public(env, m):
    return ct._public_relink_manifest(env, m)


def report(env, m, verbose=False):
    lines = []
    ct._print_relink_report(lines.append, m, public(env, m), verbose=verbose)
    return lines


def test_dry_run_lists_rows_per_account_then_the_totals(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    os.unlink(paths["local_p3"])
    m = plan(env)
    assert [(r["account"], r["name"]) for r in m["rows"]] == [(A2, "local_c2.json")]
    label = m["rows"][0]["label"]
    assert label
    assert report(env, m) == [
        "-> " + label,
        "   local_c2  'ACME-REVIEW side task'   link under 'Northwind kickoff'",
        "",
        "links   : 1 to add, 1 left out (no row for the parent in that account 1)"]


def test_both_keys_print_link_and_chain_and_a_chain_only_row_prints_chain(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    relink_store(env, write_transcript, write_row, chain=CHAIN, roots=True,
                 local_c3={"spawnedFrom": side_link("local_p3")})
    m = plan(env)
    assert [(r["name"], list(r["new"])) for r in m["rows"]] == [
        ("local_c2.json", ["spawnedFrom", "startChain"]), ("local_c3.json", ["startChain"])]
    label2, label3 = (r["label"] for r in m["rows"])
    assert report(env, m) == [
        "-> " + label2,
        C_LINE.format("local_c2") + "link and chain",
        "",
        "-> " + label3,
        C_LINE.format("local_c3") + "chain",
        "",
        "links   : 1 to add",
        "chains  : 2 to add"]


def test_counts_come_from_written_keys_and_the_left_out_list(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    relink_store(env, write_transcript, write_row, chain=CHAIN, roots=True,
                 local_c3={"spawnedFrom": side_link("local_p3")})
    m = plan(env)
    # the chain-only row's record names its kept link, so the records alone count two links
    assert ct._lineage_summary(m["rows"])["links"]["carried"] == 2
    assert ct._relink_summary(m) == {
        "links": {"carried": 1, "left_out": {}}, "chains": {"carried": 2, "left_out": 0},
        "changes": {"changed": 0, "dropped": 0, "gained": 0}}
    assert ct._relink_plan_lines(m) == ["links   : 1 to add", "chains  : 2 to add"]
    # a left-out entry counts on the links line by its first link code, and on the chains
    # line by any chain code; a written row counts there when its chain was left out
    def note(code):
        return {"code": code, "detail": "d"}
    hand = {"rows": [{"new": {"spawnedFrom": {}}, "lineage": {"notes": [
                         note("lineage_chain_dropped"), note("lineage_chain_dropped")]}}],
            "left_out": [{"notes": [note("own_link_differs"), note("lineage_chain_dropped")]},
                         {"notes": [note("lineage_no_app_link")]},
                         {"notes": [note("own_link_differs"), note("lineage_cycle")]},
                         {"notes": [note("own_chain_differs")]}],
            "lineage_cycles": 2}
    s = ct._relink_summary(hand)
    assert s["links"] == {"carried": 1,
                          "left_out": {"own_link_differs": 2, "lineage_no_app_link": 1}}
    assert s["chains"] == {"carried": 0, "left_out": 3}
    assert ct._relink_plan_lines(hand) == [
        "links   : 1 to add, 3 left out (the row's own link names no usable row 2, "
        "no app-written link backs it 1)",
        "chains  : 3 left out",
        "cycles  : 2 already present, left as they are"]
    # the codes of a row or an entry, in order, each once; the tag names the first of a kind
    assert ct._relink_codes(hand["rows"][0]) == ["lineage_chain_dropped"]
    assert ct._relink_codes(hand["left_out"][2]) == ["own_link_differs", "lineage_cycle"]
    assert ct._relink_tag(["lineage_chain_dropped", "own_link_differs"]) == \
        "[link: the row's own link names no usable row]"
    assert ct._relink_tag(["own_chain_differs", "lineage_chain_dropped"]) == \
        "[chain: the row's own chain disagrees with its holders']"
    assert ct._relink_tag([]) == "" == ct._relink_tag(["lineage_retargeted"])


def test_the_apply_lines_say_added_and_since_the_plan():
    def row(*keys):
        return {"new": {k: {} for k in keys}, "lineage": {"notes": []}}
    m = {"rows": [row("spawnedFrom", "startChain"), row("spawnedFrom")],
         "left_out": [{"notes": [{"code": "parent_not_in_account", "detail": "d"}]}],
         "lineage_cycles": 1}
    assert ct._relink_apply_lines(m) == [
        "links   : 2 added, 1 left out (no row for the parent in that account 1)",
        "chains  : 1 added"]
    m["since_plan"] = {"changed": 1, "dropped": 0, "gained": 2}
    assert ct._relink_summary(m)["changes"] == {"changed": 1, "dropped": 0, "gained": 2}
    assert ct._relink_apply_lines(m)[0] == (
        "links   : 2 added, 1 left out (no row for the parent in that account 1); "
        "since the plan: 1 changed, 2 gained")
    # with no other part the links line is the since-the-plan part alone
    quiet = {"rows": [], "left_out": [], "since_plan": {"changed": 0, "dropped": 3, "gained": 0}}
    assert ct._relink_apply_lines(quiet) == ["links   : since the plan: 3 dropped"]
    assert ct._relink_apply_lines({"rows": [], "left_out": []}) == []


def test_a_chain_code_is_counted_on_the_chains_line_only(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    relink_store(env, write_transcript, write_row, chain=CHAIN, roots=True, local_c2=WRONG_DEPTH)
    m = plan(env)
    # Task 2, as built: local_c2's kept chain disagrees with local_c1's, and local_c1's with
    # local_c2's, so a wrong kept chain leaves its partner row out too; local_c3 gains its
    # link, and its chain is left out because its two sources disagree
    assert [(e["name"], note_codes(e)) for e in m["left_out"]] == [
        ("local_c1.json", ["own_chain_differs"]), ("local_c2.json", ["own_chain_differs"])]
    assert [(r["name"], row_codes(r)) for r in m["rows"]] == [
        ("local_c3.json", ["lineage_chain_dropped"])]
    # all three on the chains line; none of them on the links line
    assert ct._relink_plan_lines(m) == ["links   : 1 to add", "chains  : 3 left out"]
    assert ct._relink_summary(m)["links"]["left_out"] == {}


def test_a_written_row_whose_chain_was_left_out_carries_a_chain_tag(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row, chain=CHAIN, roots=True)
    os.unlink(paths["local_r3"])
    m = plan(env)
    assert [(r["name"], list(r["new"]), row_codes(r)) for r in m["rows"]] == [
        ("local_c2.json", ["spawnedFrom", "startChain"], []),
        ("local_c3.json", ["spawnedFrom"], ["lineage_chain_dropped"])]
    lines = report(env, m)
    assert lines[1] == C_LINE.format("local_c2") + "link and chain"
    assert lines[4] == (C_LINE.format("local_c3") + "link under 'Northwind kickoff'"
                        "   [chain: the chain was left out]")
    assert lines[-2:] == ["links   : 2 to add", "chains  : 1 to add, 1 left out"]


def test_the_listing_is_capped_at_15_and_verbose_lists_all_with_left_out_tags(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    os.unlink(paths["local_p3"])
    title, longest = "ACME-REVIEW side task", "ACME-REVIEW side task, the longest copy"
    extra = "abcdefghijklmnop"                       # 16 more rows of C in A2, 17 in all
    for ch in extra:
        put_row(env, write_row, A2, "local_c2" + ch, LC, longest if ch == "p" else title)
    m = plan(env)
    stems = [r["name"][:-len(".json")] for r in m["rows"]]
    assert stems == ["local_c2"] + ["local_c2" + ch for ch in extra]
    assert ct.RELINK_LIST_CAP == 15
    label2, label3 = m["rows"][0]["label"], m["left_out"][0]["label"]
    totals = "links   : 17 to add, 1 left out (no row for the parent in that account 1)"

    def line(stem, width):
        shown = repr(longest if stem == "local_c2p" else title)
        return "   {0}  {1:<{w}}   link under 'Northwind kickoff'".format(stem, shown, w=width)
    # the longest title is on the 17th row, which is not printed: nothing is padded to it
    assert report(env, m) == (["-> " + label2]
                              + [line(s, len(repr(title))) for s in stems[:15]]
                              + ["   ... and 2 more", "", totals])
    assert report(env, m, verbose=True) == (
        ["-> " + label2]
        + [line(s, len(repr(longest))) for s in stems]
        + ["", "-> " + label3,
           "   local_c3  'ACME-REVIEW side task'   [link: no row for the parent in that account]",
           "", totals])


def test_nothing_to_add(mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    relink_store(env, write_transcript, write_row, local_c2={"spawnedFrom": side_link("local_p2")},
                 local_c3={"spawnedFrom": side_link("local_p3")})
    m = plan(env)
    assert m["rows"] == [] and m["left_out"] == []
    assert report(env, m) == ["nothing to add"]
    assert report(env, m, verbose=True) == ["nothing to add"]


def test_no_rows_with_left_out_rows_prints_totals_then_nothing_to_add(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    for stem in ("local_p2", "local_p3"):
        os.unlink(paths[stem])
    m = plan(env)
    lines = report(env, m)
    assert lines == ["links   : 2 left out (no row for the parent in that account 2)",
                     "nothing to add"]
    assert lines.count("nothing to add") == 1
    assert not any(ln.startswith("->") for ln in lines)
    pub = public(env, m)
    assert pub["rows"] == [] and len(pub["left_out"]) == 2


def test_the_three_new_phrases_print(mkenv, tmp_path, write_transcript, write_row):
    # own_link_differs
    env = mkenv(tmp_path / "own_link")
    relink_store(env, write_transcript, write_row, local_c3={"spawnedFrom": {}})
    lines = report(env, plan(env), verbose=True)
    assert "links   : 1 to add, 1 left out (the row's own link names no usable row 1)" in lines
    assert C_LINE.format("local_c3") + "[link: the row's own link names no usable row]" in lines
    # lineage_no_app_link
    env = mkenv(tmp_path / "no_app_link")
    relink_store(env, write_transcript, write_row, link=tool_link("local_p1"))
    lines = report(env, plan(env), verbose=True)
    assert "links   : 2 left out (no app-written link backs it 2)" in lines
    assert C_LINE.format("local_c2") + "[link: no app-written link backs it]" in lines
    # own_chain_differs, on the chains line as the planner counts it (three rows, as in
    # test_a_chain_code_is_counted_on_the_chains_line_only) and as a left-out row's tag
    env = mkenv(tmp_path / "own_chain")
    relink_store(env, write_transcript, write_row, chain=CHAIN, roots=True, local_c2=WRONG_DEPTH)
    lines = report(env, plan(env), verbose=True)
    assert "chains  : 3 left out" in lines
    for stem in ("local_c1", "local_c2"):
        assert C_LINE.format(stem) + "[chain: the row's own chain disagrees with its holders']" \
            in lines


def test_the_identity_warning_names_relink(mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    relink_store(env, write_transcript, write_row)
    identity_files(env, A1, A2)
    m = plan(env)
    lines = ct._identity_warning_lines(env, m, command="relink")
    remedies = [ln for ln in lines if "(if the app is on" in ln]
    assert len(remedies) == 2
    assert all("claude-code-sessions relink --live" in ln for ln in remedies)
    # converge keeps its own command, and nothing but the command word differs
    assert ct._identity_warning_lines(env, m) == [
        ln.replace(" relink ", " converge ") for ln in lines]


def test_the_public_projection_omits_old_new_kept_and_hashes(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    os.unlink(paths["local_p3"])
    m = plan(env)
    pub = public(env, m)
    assert list(pub) == ["op_type", "only", "account_filter", "live_asserted", "lineage_format",
                         "lineage_cycles", "rows", "left_out"]
    for r in pub["rows"]:
        assert not {"old", "new", "kept", "pre_sha256", "post_sha256"} & set(r)
    r = pub["rows"][0]
    assert list(r) == PUB_ROW_FIELDS
    assert r["adds"] == ["spawnedFrom"] and r["keeps"] == []
    src = m["rows"][0]
    assert [r[k] for k in ("account", "label", "name", "session_id", "title", "parent_title",
                           "store_path", "dest_path", "written")] == [
        src[k] for k in ("account", "label", "name", "session_id", "title", "parent_title",
                         "store_path", "dest_path", "written")]
    assert r["lineage"] == src["lineage"] and r["lineage"] is not src["lineage"]
    r["lineage"]["notes"].append({"code": "lineage_cycle", "detail": "d"})
    assert src["lineage"]["notes"] == []                    # a copy, not the plan's own
    assert pub["left_out"] == m["left_out"] and list(pub["left_out"][0]) == LEFT_FIELDS
    pub["left_out"][0]["notes"][0]["detail"] = "changed"
    assert m["left_out"][0]["notes"][0]["detail"] != "changed"
    # both keys in RELINK_KEYS order, and a kept link named under keeps
    env = mkenv(tmp_path / "chain")
    relink_store(env, write_transcript, write_row, chain=CHAIN, roots=True,
                 local_c3={"spawnedFrom": side_link("local_p3")})
    m = plan(env)
    assert [(r["adds"], r["keeps"]) for r in public(env, m)["rows"]] == [
        (["spawnedFrom", "startChain"], []), (["startChain"], ["spawnedFrom"])]
    # the fields an apply, an undo and a recovery set later, carried when present
    later = {"op_id": OPEN_ID, "identity_disagreement": {"oauth": A1, "config": A2},
             "since_plan": {"changed": 1, "dropped": 0, "gained": 0},
             "undo_left": [{"name": "local_c2.json", "label": "bob@example.com",
                            "what": "spawnedFrom", "why": "the app changed it since"}]}
    m.update(later)
    m["rows"][0].update(skipped="gone", skip_detail="gone from the store", restored=True)
    pub = public(env, m)
    assert {k: pub[k] for k in later} == later
    assert list(pub)[-2:] == ["rows", "left_out"]
    assert list(pub["rows"][0])[-3:] == ["skipped", "skip_detail", "restored"]
    assert (pub["rows"][0]["skipped"], pub["rows"][0]["restored"]) == ("gone", True)
    assert "skipped" not in pub["rows"][1]


def test_the_anonymized_projection_hides_titles_paths_and_both_filters(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    ct._ANONYMIZE = True
    m = plan(env, only="local_c2", account=A2[:8])
    out = public(env, m)
    assert out["only"] == "<only-filter>" and out["account_filter"] == "<account-filter>"
    r = out["rows"][0]
    assert r["title"].startswith("<session-") and r["parent_title"].startswith("<session-")
    assert "store_path" not in r and "dest_path" not in r
    assert r["account"] == A2[:8] + "…"
    text = json.dumps(out)
    assert "Northwind" not in text and "ACME" not in text
    assert m["only"] == "local_c2" and m["rows"][0]["title"] == "ACME-REVIEW side task"
    # every note detail and a skip detail are scrubbed, which nothing else would do
    os.unlink(paths["local_p3"])
    m = plan(env)
    m["rows"][0]["lineage"]["notes"].append(
        {"code": "lineage_chain_dropped", "detail": "under 'Northwind kickoff'"})
    m["rows"][0].update(skipped="gone", skip_detail="'ACME-REVIEW side task' is gone")
    m["left_out"][0]["notes"][0]["detail"] += ", under 'Northwind kickoff'"
    m["undo_left"] = [{"name": "local_c2.json", "label": "bob@example.com",
                       "what": "spawnedFrom", "why": "the app changed it since"}]
    out = public(env, m)
    text = json.dumps(out)
    assert "Northwind" not in text and "ACME" not in text and "bob@" not in text
    assert re.fullmatch("under '" + SESSION_LABEL + "'",
                        out["rows"][0]["lineage"]["notes"][0]["detail"])
    assert re.fullmatch("'" + SESSION_LABEL + "' is gone", out["rows"][0]["skip_detail"])
    assert re.search(", under '" + SESSION_LABEL + "'$", out["left_out"][0]["notes"][0]["detail"])
    assert out["rows"][0]["skipped"] == "gone"
    assert out["undo_left"][0]["what"] == "spawnedFrom"


def test_odd_titles_survive_the_report_and_the_anonymized_json(
        mkenv, tmp_path, write_transcript, write_row, monkeypatch):
    env = mkenv(tmp_path)
    odd = "Café ☕ Northwind"
    titled = {stem: {"title": odd} for stem in ("local_p1", "local_p2", "local_p3")}
    titled.update({stem: {"title": "left_out"} for stem in ("local_c1", "local_c2", "local_c3")})
    paths = relink_store(env, write_transcript, write_row, **titled)
    os.unlink(paths["local_p3"])
    m = plan(env)
    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", stream)

    def say(line):
        # cmd_realign's console fallback; cmd_relink's own is Task 6's
        try:
            print(line)
        except UnicodeEncodeError:
            enc = getattr(sys.stdout, "encoding", None) or "utf-8"
            print(line.encode(enc, "replace").decode(enc, "replace"))
    ct._print_relink_report(say, m, public(env, m), verbose=True)
    stream.flush()
    printed = stream.buffer.getvalue().decode("cp1252").splitlines()
    assert "   local_c2  'left_out'   link under 'Café ? Northwind'" in printed
    assert "   local_c3  'left_out'   [link: no row for the parent in that account]" in printed
    # --json --anonymize: a title that is a report key leaves the key a key
    ct._ANONYMIZE = True
    out = public(env, m)
    assert isinstance(out["left_out"], list) and isinstance(out["rows"], list)
    assert [e["name"] for e in out["left_out"]] == ["local_c3.json"]
    for entry in out["rows"] + out["left_out"]:
        assert re.fullmatch(SESSION_LABEL, entry["title"])
    assert re.fullmatch(SESSION_LABEL, out["rows"][0]["parent_title"])
    assert "Caf" not in json.dumps(out, ensure_ascii=False)
    ct._print_relink_report(say, m, out, verbose=True)        # raises nothing either


def test_a_title_equal_to_a_schema_value_corrupts_neither_report(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    titled = {stem: {"title": "parent_not_in_account"} for stem in ("local_p1", "local_p2")}
    titled.update({stem: {"title": "spawnedFrom"} for stem in ("local_c1", "local_c2", "local_c3")})
    paths = relink_store(env, write_transcript, write_row, **titled)
    os.unlink(paths["local_p3"])
    ct._ANONYMIZE = True
    m = plan(env)
    pub = public(env, m)
    assert [r["adds"] for r in pub["rows"]] == [["spawnedFrom"]]
    assert [note_codes(e) for e in pub["left_out"]] == [["parent_not_in_account"]]
    assert re.fullmatch(SESSION_LABEL, pub["rows"][0]["title"])
    lines = []
    ct._print_relink_report(lines.append, m, pub)
    assert "links   : 1 to add, 1 left out (no row for the parent in that account 1)" in lines
    assert any(re.fullmatch("   local_c2  '{0}'   link under '{0}'".format(SESSION_LABEL), ln)
               for ln in lines)


# --------------------------------------------------------------- the journal
# Task 5: the shape check undo and recovery trust before they write anything
# back, and the saver that journals rows on a byte budget (D15).

THEN = "The op is left at 'writing' - run 'claude-code-sessions recover' to finish it."


def chain_plan(env, write_transcript, write_row):
    """The plan of the chain store: local_c2 gains its link and chain (A2), local_c3, which
    keeps side_link('local_p3'), its chain only (A3)."""
    relink_store(env, write_transcript, write_row, chain=CHAIN, roots=True,
                 local_c3={"spawnedFrom": side_link("local_p3")})
    m = plan(env)
    assert [(r["name"], list(r["new"]), list(r["kept"])) for r in m["rows"]] == [
        ("local_c2.json", ["spawnedFrom", "startChain"], []),
        ("local_c3.json", ["startChain"], ["spawnedFrom"])]
    return m


def outside_plan(env, write_transcript, write_row):
    """The plan of Task 2's "outside the record" store: local_c2 gains its link and keeps a
    chain no holder judged. Returns (plan, local_c2's row)."""
    kept = {"firstSessionId": "local_r2", "depth": 5}
    relink_store(env, write_transcript, write_row, roots=True, local_c2={"startChain": kept})
    m = plan(env)
    c2 = by_name(m["rows"])["local_c2.json"]
    assert c2["kept"] == {"startChain": kept} and list(c2["new"]) == ["spawnedFrom"]
    assert c2["lineage"]["root_row"] == "" == c2["lineage"]["root_session"]
    return m, c2


def test_a_planned_manifest_passes_the_shape_check(mkenv, tmp_path, write_transcript, write_row):
    m = chain_plan(mkenv(tmp_path / "chain"), write_transcript, write_row)
    assert ct._relink_shape_error(m) is None
    # Task 2's non-string-title store
    env = mkenv(tmp_path / "titles")
    relink_store(env, write_transcript, write_row, local_c2={"title": 5},
                 local_p2={"title": None})
    m = plan(env)
    assert by_name(m["rows"])["local_c2.json"]["title"] == ""
    assert ct._relink_shape_error(m) is None
    # Task 2's two-parent-rows store: the kept link's record names the kept target
    env = mkenv(tmp_path / "two")
    relink_store(env, write_transcript, write_row, chain=CHAIN, roots=True,
                 local_c3={"spawnedFrom": side_link("local_p3")})
    put_row(env, write_row, A3, "local_p3b", LP, "Northwind kickoff, later copy",
            lastActivityAt=1_756_000_000_000)
    m = plan(env)
    assert by_name(m["rows"])["local_c3.json"]["lineage"]["parent_row"] == "local_p3"
    assert ct._relink_shape_error(m) is None
    # a non-object old value, here a list, is journaled as it stood
    env = mkenv(tmp_path / "list")
    relink_store(env, write_transcript, write_row, local_c2={"spawnedFrom": []})
    m = plan(env)
    assert by_name(m["rows"])["local_c2.json"]["old"] == {
        "spawnedFrom": {"present": True, "value": []}}
    assert ct._relink_shape_error(m) is None


def test_a_copy_named_row_the_scan_reads_passes_the_shape_check(
        mkenv, tmp_path, write_transcript, write_row):
    # the scan reads every local_*.json, an Explorer copy's name among them, and the plan
    # repairs a stem-mismatched row as a candidate; its own record must pass its own check
    env = mkenv(tmp_path)
    relink_store(env, write_transcript, write_row)
    put_row(env, write_row, A2, "local_c2 - Copy", LC, "ACME-REVIEW side task",
            sessionId="local_c2")
    m = plan(env)
    assert names(m["rows"]) == ["local_c2 - Copy.json", "local_c2.json", "local_c3.json"]
    assert ct._relink_shape_error(m) is None


def test_a_link_only_row_that_keeps_an_unjudged_chain_passes(
        mkenv, tmp_path, write_transcript, write_row):
    m, _ = outside_plan(mkenv(tmp_path), write_transcript, write_row)
    assert ct._relink_shape_error(m) is None


def test_a_kept_chain_record_may_name_it_or_not(mkenv, tmp_path, write_transcript, write_row):
    m, c2 = outside_plan(mkenv(tmp_path), write_transcript, write_row)
    assert ct._relink_shape_error(m) is None                       # root_row ""
    c2["lineage"]["root_row"] = "local_r2"                         # the kept chain's own
    c2["lineage"]["root_session"] = LR
    assert ct._relink_shape_error(m) is None
    c2["lineage"]["root_row"] = "local_r9"
    bad = ct._relink_shape_error(m)
    assert isinstance(bad, str) and "lineage" in bad


@pytest.mark.parametrize("damage, named", [
    (lambda m: m["rows"][0].__setitem__("old", []), "'old'"),
    (lambda m: m["rows"][0]["old"]["spawnedFrom"].__setitem__("value", {}), "'old'"),
    (lambda m: m["rows"][0]["new"].__setitem__("cwd", "C:\\x"), "'new'"),
    (lambda m: m["rows"][0]["new"]["spawnedFrom"].__setitem__("title", "N"), "'new'"),
    (lambda m: m["rows"][0]["kept"].__setitem__("spawnedFrom", {}), "'kept'"),
    (lambda m: m["rows"][0]["lineage"].__setitem__("parent_row", "local_p9"), "lineage"),
    (lambda m: m["rows"][0].__setitem__("pre_sha256", "xyz"), "'pre_sha256'"),
    (lambda m: m["rows"][0].__setitem__("title", 5), "'title'"),
    (lambda m: m["rows"][0].__setitem__("written", "yes"), "'written'"),
    (lambda m: m["rows"][0].__setitem__("restored", 1), "'restored'"),
    (lambda m: m["rows"][0].__setitem__("skipped", "because"), "'skipped'"),
    (lambda m: m["rows"][0].__setitem__("name", "..\\x.json"), "'name'"),
    (lambda m: m.__setitem__("lineage_format", 2), "lineage_format"),
    (lambda m: m.__setitem__("rows", ["x"]), "row 0"),
    (lambda m: m["rows"][0]["lineage"].__setitem__("parent_session", ""), "lineage"),
    (lambda m: m["rows"][0]["lineage"]["notes"].append({"code": "made_up", "detail": "x"}),
     "lineage"),
    (lambda m: m["rows"][0].__setitem__("store_path", m["rows"][1]["store_path"]),
     "'store_path'"),
    (lambda m: m["rows"][0].__setitem__("account", m["rows"][1]["account"]), "'account'"),
    (lambda m: m.__setitem__("since_plan", {"changed": True, "dropped": 0, "gained": 0}),
     "since_plan"),
    (lambda m: m.__setitem__("rolling_back", "yes"), "rolling_back"),
    (lambda m: m.__setitem__("back_restored", True), "back_restored"),
    (lambda m: m.__setitem__("back_left", [{"name": "local_c2.json"}]), "back_left"),
])
def test_damaged_fields_are_named(damage, named, mkenv, tmp_path, write_transcript, write_row):
    m = chain_plan(mkenv(tmp_path), write_transcript, write_row)
    damage(m)
    bad = ct._relink_shape_error(m)
    assert isinstance(bad, str) and named in bad, bad


def _left(**changes):
    e = {"account": A1, "label": "x", "name": "local_c9.json", "session_id": LC, "title": "",
         "notes": [{"code": "own_link_differs", "detail": "x"}]}
    e.update(changes)
    return e


# The checks the list above does not reach, each by a damage of its own.
@pytest.mark.parametrize("damage, named", [
    (lambda m: m.__setitem__("lineage_cycles", -1), "lineage_cycles"),
    (lambda m: m.__setitem__("lineage_cycles", True), "lineage_cycles"),
    (lambda m: m.__setitem__("lineage_format", True), "lineage_format"),
    (lambda m: m.__setitem__("rows", []), "'rows'"),
    (lambda m: m.__setitem__("left_out", {}), "left_out"),
    (lambda m: m.__setitem__("left_out", ["x"]), "left_out"),
    (lambda m: m.__setitem__("left_out", [_left(session_id=None)]), "left_out"),
    (lambda m: m.__setitem__("left_out", [_left(notes=[])]), "left_out"),
    (lambda m: m.__setitem__("left_out", [_left(notes=[{"code": "own_link_differs"}])]),
     "left_out"),
    (lambda m: m.__setitem__("left_out", [_left(notes=[{"code": "made_up", "detail": "x"}])]),
     "left_out"),
    (lambda m: m.__setitem__("since_plan", {"changed": 0, "dropped": 0}), "since_plan"),
    (lambda m: m.__setitem__("since_plan", {"changed": -1, "dropped": 0, "gained": 0}),
     "since_plan"),
    (lambda m: m.__setitem__("back_restored", -1), "back_restored"),
    (lambda m: m.__setitem__("undo_left", [{"name": "local_c2.json", "label": "x",
                                            "what": "spawnedFrom"}]), "undo_left"),
    (lambda m: m.__setitem__("undo_left", {}), "undo_left"),
    (lambda m: m["rows"][0].__setitem__("session_id", ""), "'session_id'"),
    (lambda m: m["rows"][0].__setitem__("dest_path", ""), "'dest_path'"),
    (lambda m: m["rows"][0].__setitem__("label", None), "'label'"),
    (lambda m: m["rows"][0].__setitem__("parent_title", None), "'parent_title'"),
    (lambda m: m["rows"][0].__setitem__("name", "local_c9.json"), "'name'"),
    (lambda m: m["rows"][0].__setitem__("name", "local_c2.txt"), "'name'"),
    # a separator in the name never equals its dest_path's file, even with the path to match
    (lambda m: m["rows"][0].__setitem__("name", os.path.join("local_c1", "..", "local_c2.json")),
     "'name'"),
    (lambda m: m["rows"][0].update(
        name=os.path.join("local_c1", "..", "local_c2.json"),
        dest_path=os.path.join(m["rows"][0]["store_path"], "local_c1", "..", "local_c2.json")),
     "'name'"),
    # a file that is not a row's, though it is its dest_path's
    (lambda m: m["rows"][0].update(
        name="c2.json", dest_path=os.path.join(m["rows"][0]["store_path"], "c2.json")),
     "'name'"),
    (lambda m: m["rows"][0].__setitem__("post_sha256",
                                        m["rows"][0]["post_sha256"].upper()), "'post_sha256'"),
    (lambda m: m["rows"][0].__setitem__("new", {}), "'new'"),
    (lambda m: m["rows"][0]["new"]["spawnedFrom"].__setitem__("taskId", ""), "'new'"),
    (lambda m: m["rows"][0]["new"]["startChain"].__setitem__("depth", True), "'new'"),
    (lambda m: m["rows"][0]["new"]["startChain"].__setitem__("depth", 0), "'new'"),
    (lambda m: m["rows"][0]["new"]["startChain"].__setitem__("title", "N"), "'new'"),
    (lambda m: m["rows"][0]["old"]["spawnedFrom"].__setitem__("present", 1), "'old'"),
    (lambda m: m["rows"][0]["old"]["spawnedFrom"].__setitem__("value", "x"), "'old'"),
    (lambda m: m["rows"][0]["old"]["spawnedFrom"].__setitem__("was", None), "'old'"),
    (lambda m: m["rows"][0]["old"].__delitem__("startChain"), "'old'"),
    (lambda m: m["rows"][0]["old"].__setitem__("spawnedFrom", None), "'old'"),
    (lambda m: m["rows"][0]["kept"].__setitem__("cwd", {}), "'kept'"),
    (lambda m: m["rows"][1]["kept"].__setitem__("spawnedFrom", "local_p3"), "'kept'"),
    (lambda m: m["rows"][1].__setitem__("kept", {}), "'kept'"),
    (lambda m: m["rows"][0].__setitem__("lineage", "x"), "lineage"),
    (lambda m: m["rows"][0]["lineage"].__setitem__("root_row", ""), "lineage"),
    (lambda m: m["rows"][0]["lineage"].__setitem__("root_session", ""), "lineage"),
    (lambda m: m["rows"][0]["lineage"].__setitem__("root_planned", True), "lineage"),
    (lambda m: m["rows"][1]["lineage"].__setitem__("parent_row", "local_p9"), "lineage"),
    (lambda m: m["rows"][1]["lineage"].__setitem__("root_row", "local_r9"), "lineage"),
    (lambda m: m["rows"][0].__delitem__("written"), "'written'"),
    (lambda m: m["rows"][0].__setitem__("skipped", "gone"), "'skip_detail'"),
    (lambda m: m["rows"][0].__setitem__("skipped", ["gone"]), "'skipped'"),
    (lambda m: m["rows"][0].__setitem__("back_keys", ["cwd"]), "'back_keys'"),
    (lambda m: m["rows"][0].__setitem__("back_keys", [["spawnedFrom"]]), "'back_keys'"),
    (lambda m: m["rows"][1].__setitem__("back_keys", ["spawnedFrom"]), "'back_keys'"),
    # two rows of one record naming one file: undo builds each row's write from one read
    # taken before any write, so the second would put the first's keys back at new
    (lambda m: m["rows"].__setitem__(1, json.loads(json.dumps(m["rows"][0]))), "'dest_path'"),
    (lambda m: m["rows"].__setitem__(1, dict(
        json.loads(json.dumps(m["rows"][0])),
        dest_path=os.path.join(m["rows"][0]["store_path"], ".", m["rows"][0]["name"]))),
     "'dest_path'"),
])
def test_every_other_checked_field_is_named(
        damage, named, mkenv, tmp_path, write_transcript, write_row):
    m = chain_plan(mkenv(tmp_path), write_transcript, write_row)
    damage(m)
    bad = ct._relink_shape_error(m)
    assert isinstance(bad, str) and named in bad, bad
    assert bad != "its record could not be read", "a damage the checks name, not the fallback"


def test_the_later_fields_pass_when_well_formed(mkenv, tmp_path, write_transcript, write_row):
    m = chain_plan(mkenv(tmp_path), write_transcript, write_row)
    entry = {"name": "local_c2.json", "label": "x", "what": "spawnedFrom",
             "why": "the app changed it since"}
    m.update(since_plan={"changed": 0, "dropped": 1, "gained": 0}, rolling_back=True,
             back_restored=0, undo_left=[entry], back_left=[], op_id=OPEN_ID,
             status="writing", history=[])
    m["rows"][0].update(written=True, restored=False, skipped="gone",
                        skip_detail="gone from the store", back_keys=["startChain"])
    m["rows"][0]["old"]["spawnedFrom"] = {"present": True, "value": False}
    m["left_out"] = [_left()]
    assert ct._relink_shape_error(m) is None


def test_the_shape_check_never_raises(mkenv, tmp_path, write_transcript, write_row):
    class Boom(dict):
        def get(self, *a):
            raise RuntimeError("boom")

        def __getitem__(self, k):
            raise RuntimeError("boom")

    for m in ({}, None, [], "x", {"lineage_format": 1, "rows": None},
              {"lineage_format": 1, "rows": [{}]}):
        assert isinstance(ct._relink_shape_error(m), str)
    m = chain_plan(mkenv(tmp_path), write_transcript, write_row)
    m["rows"][0]["lineage"] = "x"
    assert isinstance(ct._relink_shape_error(m), str)
    m["rows"] = None
    assert isinstance(ct._relink_shape_error(m), str)
    assert ct._relink_shape_error(Boom(lineage_format=1)) == "its record could not be read"
    m = chain_plan(mkenv(tmp_path / "again"), write_transcript, write_row)
    m["rows"][0] = Boom(m["rows"][0])
    assert ct._relink_shape_error(m) == "its record could not be read"


def _no_b64_keys(x):
    if isinstance(x, dict):
        return all(not str(k).endswith("_b64") and _no_b64_keys(v) for k, v in x.items())
    if isinstance(x, list):
        return all(_no_b64_keys(v) for v in x)
    return True


def test_the_journal_holds_no_row_images_and_a_row_costs_a_bounded_size(
        mkenv, tmp_path, write_transcript, write_row):
    # Why 60 and not the design's 931: the bound is per row, so a row of 135 KB costing under
    # 3 KB is what scales to 931 rows (under 3 MB, against about 126 MB of row bytes), and 60
    # rows prove it without 15 MB of fixtures in every suite run.
    env = mkenv(tmp_path)
    write_transcript(env, "C--p", LP, t_entries())
    put_row(env, write_row, A1, "local_p1", LP, "Northwind kickoff")
    put_row(env, write_row, A2, "local_p2", LP, "Northwind kickoff")
    filler = "FILLER" * (16 * 1024 // 6)
    for n in range(1, 61):
        kid = "0ccc{0:04x}-2222-4000-8000-00000000000c".format(n)       # LC-like; fake
        put_row(env, write_row, A1, "local_1aaa{0:04x}-0000-4000-8000-000000000001".format(n),
                kid, "ACME-REVIEW side task", spawnedFrom=side_link("local_p1"))
        put_row(env, write_row, A2, "local_2bbb{0:04x}-0000-4000-8000-000000000002".format(n),
                kid, "ACME-REVIEW side task", filler=filler)
    m = plan(env)
    assert len(m["rows"]) == 60 and {r["account"] for r in m["rows"]} == {A2}
    assert ct._relink_shape_error(m) is None
    for r in m["rows"]:
        assert len(json.dumps(r, indent=1).encode()) < 3072
    dump = json.dumps(m, indent=1)
    assert len(dump.encode()) < 60 * 3072
    assert "FILLER" not in dump
    assert _no_b64_keys(m["rows"])


def test_the_saver_saves_per_row_while_the_budget_lasts(
        monkeypatch, mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    relink_store(env, write_transcript, write_row)
    op = ct.new_op(env, plan(env))
    saves = []
    monkeypatch.setattr(ct, "save_manifest", saves.append)
    per_save = len(json.dumps(op.manifest, indent=1)) + 4096
    saver = ct._RelinkSaver(op, budget=2 * per_save, then=THEN)
    assert (saver.per_save, saver.left) == (per_save, 2 * per_save)
    for _ in range(5):
        saver.tick()
    assert saves == [op, op] and saver.left == 0
    # with no budget given, the module's figure is read when the saver is made
    monkeypatch.setattr(ct, "RELINK_JOURNAL_BYTE_BUDGET", 3 * per_save)
    assert ct._RelinkSaver(op).left == 3 * per_save

    def refuse(o):
        raise OSError(13, "Permission denied")
    monkeypatch.setattr(ct, "save_manifest", refuse)
    with pytest.raises(ct.Refusal) as exc:
        ct._RelinkSaver(op, then=THEN).tick()
    msg = str(exc.value)
    assert msg.startswith("could not save the operation record in {0} (".format(op.op_dir))
    assert "Permission denied" in msg and msg.endswith(". " + THEN)


# ----------------------------------------------------------------- the apply
# Task 6: run_relink re-plans under the lock, journals, writes each row with
# only its two keys changed and verifies it by value; cmd_relink and the
# parser; the RULING 5 text realign, converge and relink share.

REOPEN = ("Reopen the app and check that the relinked rows show under their parents. 'undo' "
          "takes the links out again, key by key.")
APP_RUNNING = [(4242, "C:\\Program Files\\Claude\\claude.exe")]


def ns(**kw):
    d = {"json": False, "verbose": False, "anonymize": False, "apply": False,
         "only": "", "account": "", "live": ""}
    d.update(kw)
    return types.SimpleNamespace(**d)


def recover_hint(op_id):
    return "'recover --resolve {0} --back --apply'".format(op_id)


def the_op(env):
    (op,) = ct.list_ops(env)
    return op


def same_file(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def mid_rows(edit):
    """A crash hook that runs EDIT at 'relink-mid-rows', before the second row of the
    base store, and lets the apply go on."""
    def hook(point):
        if point == "relink-mid-rows":
            edit()
    return hook


def raws(paths, stems=("local_c2", "local_c3")):
    return {stem: raw_of(paths[stem]) for stem in stems}


def test_apply_writes_each_row_and_journals_it(mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    before = {stem: data_of(p) for stem, p in paths.items()}
    untouched = raws(paths, [s for s in paths if s not in ("local_c2", "local_c3")])
    m = plan(env)
    assert ct.run_relink(env, m) == "completed"
    assert data_of(paths["local_c2"]) == {
        **before["local_c2"], "spawnedFrom": {"sessionId": "local_p2", "taskId": "task_7f"}}
    assert data_of(paths["local_c3"]) == {
        **before["local_c3"], "spawnedFrom": {"sessionId": "local_p3", "taskId": TASK}}
    assert raws(paths, untouched) == untouched
    om = the_op(env).manifest
    assert om["op_id"] == m["op_id"]
    assert om["op_type"] == "relink" and om["status"] == "completed"
    assert [h["status"] for h in om["history"]] == ["journaled", "writing", "completed"]
    assert om["since_plan"] == {"changed": 0, "dropped": 0, "gained": 0}
    assert names(om["rows"]) == ["local_c2.json", "local_c3.json"]
    for r in om["rows"]:
        assert r["written"] is True and "restored" not in r
        assert r["post_sha256"] == hashlib.sha256(raw_of(r["dest_path"])).hexdigest()
    assert _no_b64_keys(om["rows"])
    assert ct._relink_shape_error(om) is None
    assert ct.read_lock(env) is None and ct.nonterminal_ops(env) == []


def test_a_row_with_many_app_keys_keeps_all_of_them_in_order(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row, local_c2=APP_KEYS)
    before = data_of(paths["local_c2"])
    assert set(APP_KEYS) <= set(before)
    assert ct.run_relink(env, plan(env)) == "completed"
    after = data_of(paths["local_c2"])
    assert list(after) == list(before) + ["spawnedFrom"]
    assert json.dumps({k: after[k] for k in before}) == json.dumps(before)   # order and type
    assert raw_of(paths["local_c2"]) == ct._relink_row_bytes(after)


def test_an_unfinished_operation_refuses_the_apply_and_journals_nothing(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    m = plan(env)
    put_op(env, OPEN_ID, open_op())
    before = raws(paths)
    with pytest.raises(ct.Refusal) as exc:
        ct.run_relink(env, m)
    assert str(exc.value) == D18_OPEN.format(OPEN_ID)
    assert [o.manifest["op_id"] for o in ct.list_ops(env)] == [OPEN_ID]
    assert raws(paths) == before and ct.read_lock(env) is None


def test_the_apply_refuses_while_the_app_runs(mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    m = plan(env)
    before = raws(paths)
    env.process_lister = lambda: APP_RUNNING
    with pytest.raises(ct.Refusal) as exc:
        ct.run_relink(env, m)
    assert "refusing to add links to rows in the session store while it is" in str(exc.value)
    assert ct.RELINK_GUARD_WHY in str(exc.value)
    assert ct.list_ops(env) == [] and ct.read_lock(env) is None and raws(paths) == before


def test_a_row_changed_after_the_locked_plan_refuses_and_leaves_writing(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    m = plan(env)
    ct._crash_hook = mid_rows(
        lambda: set_keys(paths["local_c3"], lastFocusedAt=1_800_000_000_000))
    with pytest.raises(ct.Refusal) as exc:
        ct.run_relink(env, m)
    (op,) = ct.nonterminal_ops(env)
    om = op.manifest
    assert om["status"] == "writing"
    assert str(exc.value) == (
        "row local_c3.json ({0}) changed since this relink was planned - writing would discard "
        "whatever changed it, most likely the app. The op is left at 'writing' - {1} takes out "
        "what landed, then re-run to replan.".format(om["rows"][1]["label"],
                                                     recover_hint(om["op_id"])))
    assert [r["written"] for r in om["rows"]] == [True, False]
    assert "spawnedFrom" in data_of(paths["local_c2"])
    assert "spawnedFrom" not in data_of(paths["local_c3"])
    assert ct.read_lock(env) is None


def test_a_store_path_that_is_not_the_rows_folder_is_unsafe(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    m = plan(env)
    a1_store = os.path.dirname(paths["local_c1"])
    before = raw_of(paths["local_c3"])
    ct._crash_hook = mid_rows(lambda: m["rows"][1].__setitem__("store_path", a1_store))
    with pytest.raises(ct.LayoutError) as exc:
        ct.run_relink(env, m)
    assert "not a direct child" in str(exc.value)
    assert raw_of(paths["local_c3"]) == before
    assert the_op(env).manifest["status"] == "writing"


def test_a_row_repointed_mid_run_refuses(mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    m = plan(env)
    ct._crash_hook = mid_rows(lambda: set_keys(paths["local_c3"], cliSessionId=LD))
    with pytest.raises(ct.Refusal) as exc:
        ct.run_relink(env, m)
    om = the_op(env).manifest
    msg = str(exc.value)
    assert msg.startswith("row local_c3.json ({0}) now opens another conversation".format(
        om["rows"][1]["label"]))
    assert msg.endswith("The op is left at 'writing' - {0} takes out what landed.".format(
        recover_hint(om["op_id"])))
    assert "spawnedFrom" not in data_of(paths["local_c3"])
    assert om["status"] == "writing" and [r["written"] for r in om["rows"]] == [True, False]


@pytest.mark.parametrize("edit, said", [("delete", "is gone from the store"),
                                        ("corrupt", "could not be read")])
def test_a_row_gone_or_unreadable_mid_run_refuses(
        edit, said, mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    m = plan(env)

    def change():
        if edit == "delete":
            os.unlink(paths["local_c3"])
        else:
            with open(paths["local_c3"], "w", encoding="utf-8") as fh:
                fh.write("{not json")
    ct._crash_hook = mid_rows(change)
    with pytest.raises(ct.Refusal) as exc:
        ct.run_relink(env, m)
    om = the_op(env).manifest
    msg = str(exc.value)
    assert msg.startswith("row local_c3.json ({0}) {1}".format(om["rows"][1]["label"], said))
    assert recover_hint(om["op_id"]) in msg
    assert om["status"] == "writing" and [r["written"] for r in om["rows"]] == [True, False]


def test_the_verify_is_by_value(monkeypatch, mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    m = plan(env)
    real = ct.atomic_write

    def wrong_for_c2(path, data):
        # every other path, the manifest's among them, goes to the real writer
        if same_file(path, paths["local_c2"]):
            return real(path, json.dumps({"cliSessionId": LC}).encode("utf-8"))
        return real(path, data)
    monkeypatch.setattr(ct, "atomic_write", wrong_for_c2)
    saves = record_saves(monkeypatch)
    with pytest.raises(ct.LayoutError) as exc:
        ct.run_relink(env, m)
    om = the_op(env).manifest                        # the manifest on disk
    assert str(exc.value) == (
        "verify failed on local_c2.json: the row on disk is not the found value with exactly "
        "relink's keys set. {0} takes out what it can check and names the rest.".format(
            recover_hint(om["op_id"])))
    assert om["rows"][0]["written"] is True and om["rows"][1]["written"] is False
    assert om["status"] == "writing"
    assert "spawnedFrom" not in data_of(paths["local_c3"])
    # marked and saved before the raise, by the write loop itself; then the way-out save
    assert [(who, marks) for who, _, marks in saves][-2:] == [
        ("_relink_write_rows", [True, False]), ("execute_relink_op", [True, False])]


def record_saves(monkeypatch, fail_in=()):
    """Wrap save_manifest: each call is recorded as (the calling function, the status saved,
    each row's written mark), and a call from a function named in FAIL_IN raises OSError
    instead of saving."""
    real = ct.save_manifest
    saves = []

    def save(op):
        who = sys._getframe(1).f_code.co_name
        saves.append((who, op.manifest.get("status"),
                      [r.get("written") for r in op.manifest.get("rows") or []]))
        if who in fail_in:
            raise OSError(28, "No space left on device")
        return real(op)
    monkeypatch.setattr(ct, "save_manifest", save)
    return saves


@pytest.mark.parametrize("fail_in", [("_relink_write_rows",),
                                     ("_relink_write_rows", "execute_relink_op")],
                         ids=["inner", "inner-and-way-out"])
def test_a_failed_save_never_replaces_the_verify_error(
        fail_in, monkeypatch, mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    m = plan(env)
    real = ct.atomic_write

    def wrong_for_c2(path, data):
        if same_file(path, paths["local_c2"]):
            return real(path, json.dumps({"cliSessionId": LC}).encode("utf-8"))
        return real(path, data)
    monkeypatch.setattr(ct, "atomic_write", wrong_for_c2)
    saves = record_saves(monkeypatch, fail_in=fail_in)
    with pytest.raises(ct.LayoutError) as exc:       # never the OSError, never a traceback
        ct.run_relink(env, m)
    assert str(exc.value).startswith("verify failed on local_c2.json: ")
    assert str(exc.value).endswith(
        "{0} takes out what it can check and names the rest.".format(recover_hint(m["op_id"])))
    assert [who for who, _, _ in saves][-2:] == ["_relink_write_rows", "execute_relink_op"]
    om = the_op(env).manifest
    assert om["status"] == "writing"
    # the way-out save tries again: the mark reaches the disk unless that save fails too
    assert om["rows"][0]["written"] is (len(fail_in) == 1)
    assert ct.read_lock(env) is None


def test_a_failed_completed_save_refuses_with_the_runnable_hint(
        monkeypatch, mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    m = plan(env)
    real = ct.save_manifest

    def full_at_completed(op):
        # the real set_status sets the status in memory, then saves it
        if op.manifest.get("status") == "completed":
            raise OSError(28, "No space left on device")
        return real(op)
    monkeypatch.setattr(ct, "save_manifest", full_at_completed)
    with pytest.raises(ct.Refusal) as exc:
        ct.run_relink(env, m)
    op = the_op(env)
    assert str(exc.value) == (
        "could not save the operation record in {0} ([Errno 28] No space left on device). The "
        "op is left at 'writing' - {1} takes out what landed.".format(
            op.op_dir, recover_hint(op.manifest["op_id"])))
    assert op.manifest["status"] == "writing"
    assert [r["written"] for r in op.manifest["rows"]] == [True, True]
    assert "spawnedFrom" in data_of(paths["local_c2"]) and "spawnedFrom" in data_of(paths["local_c3"])
    assert ct.read_lock(env) is None


def test_a_row_that_landed_before_its_mark_is_marked_again_without_a_write(
        monkeypatch, mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    m = plan(env)

    def crash(point):
        if point == "relink-before-mark":
            raise SimulatedCrash()
    ct._crash_hook = crash
    with pytest.raises(SimulatedCrash):
        ct.run_relink(env, m)
    ct._crash_hook = None
    op = the_op(env)                                 # the record on disk
    rows = op.manifest["rows"]
    assert op.manifest["status"] == "writing"
    assert [r["written"] for r in rows] == [False, False]
    landed = raw_of(paths["local_c2"])
    assert hashlib.sha256(landed).hexdigest() == rows[0]["post_sha256"]
    assert "spawnedFrom" not in data_of(paths["local_c3"]) and ct.read_lock(env) is None
    # the write loop again, as forward recovery will run it: the landed row is marked from
    # its bytes, never written a second time; the pending one is written
    real = ct.atomic_write
    written = []

    def record(path, data):
        written.append(os.path.basename(path))
        return real(path, data)
    monkeypatch.setattr(ct, "atomic_write", record)
    ct._relink_write_rows(op, op.manifest, ct._retitle_roots(env),
                          ct._RelinkSaver(op, then=ct._relink_apply_then(op.manifest)))
    assert "local_c2.json" not in written and written.count("local_c3.json") == 1
    assert raw_of(paths["local_c2"]) == landed
    assert [r["written"] for r in the_op(env).manifest["rows"]] == [True, True]
    assert data_of(paths["local_c3"])["spawnedFrom"] == tool_link("local_p3")


def test_a_crash_after_the_rows_leaves_every_mark_on_disk(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    m = plan(env)

    def crash(point):
        if point == "relink-after-rows":
            raise SimulatedCrash()
    ct._crash_hook = crash
    with pytest.raises(SimulatedCrash):
        ct.run_relink(env, m)
    om = the_op(env).manifest
    assert om["status"] == "writing" and [r["written"] for r in om["rows"]] == [True, True]
    for r in om["rows"]:
        assert hashlib.sha256(raw_of(r["dest_path"])).hexdigest() == r["post_sha256"]
    assert ct.read_lock(env) is None


def test_a_record_that_does_not_match_its_row_is_not_written_from(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    m = plan(env)
    before = raw_of(paths["local_c3"])
    ct._crash_hook = mid_rows(lambda: m["rows"][1]["old"].__setitem__(
        "spawnedFrom", {"present": True, "value": "x"}))
    with pytest.raises(ct.LayoutError) as exc:
        ct.run_relink(env, m)
    om = the_op(env).manifest
    assert str(exc.value) == ("the record for {0} does not match row local_c3.json; refusing "
                              "rather than write from it".format(om["op_id"]))
    assert raw_of(paths["local_c3"]) == before and om["status"] == "writing"


def test_a_write_that_fails_names_the_recover_command(
        monkeypatch, mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    m = plan(env)
    real = ct.atomic_write

    def refuse_c3(path, data):
        if same_file(path, paths["local_c3"]):
            raise OSError(13, "Permission denied")
        return real(path, data)
    monkeypatch.setattr(ct, "atomic_write", refuse_c3)
    with pytest.raises(ct.Refusal) as exc:
        ct.run_relink(env, m)
    om = the_op(env).manifest
    msg = str(exc.value)
    assert "recover --resolve " + om["op_id"] + " --back --apply" in msg   # never a preview
    assert msg == ("could not write row local_c3.json ({0}): [Errno 13] Permission denied. The "
                   "op is left at 'writing' - {1} takes out what landed.".format(
                       om["rows"][1]["label"], recover_hint(om["op_id"])))
    assert om["status"] == "writing" and [r["written"] for r in om["rows"]] == [True, False]


def test_a_row_outside_every_discovered_root_is_refused(
        tmp_path, mkenv, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    m = plan(env)
    elsewhere = tmp_path / "elsewhere" / A3 / O3
    copy = str(elsewhere / "local_c3.json")

    def move_out():
        elsewhere.mkdir(parents=True)
        shutil.copyfile(paths["local_c3"], copy)
        m["rows"][1].update(dest_path=copy, store_path=str(elsewhere))
    ct._crash_hook = mid_rows(move_out)
    with pytest.raises(ct.LayoutError) as exc:
        ct.run_relink(env, m)
    assert "outside every recognized root" in str(exc.value)
    # the roots came from discovery, not from the journal that named the copy
    assert raw_of(copy) == raw_of(paths["local_c3"])
    assert "spawnedFrom" not in data_of(copy)


@pytest.mark.parametrize("fails", ["new_op", "set_status"])
def test_a_journal_that_cannot_be_written_refuses_before_any_row(
        fails, monkeypatch, capsys, mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    before = raws(paths)
    if fails == "new_op":
        def no_space(env_, manifest):
            raise OSError(28, "No space left on device")
        monkeypatch.setattr(ct, "new_op", no_space)
    else:
        real = ct.set_status

        def no_space(op, status):
            if status == "writing":
                raise OSError(28, "No space left on device")
            return real(op, status)
        monkeypatch.setattr(ct, "set_status", no_space)
    monkeypatch.setattr(ct, "default_env", lambda: env)
    assert ct.main(["relink", "--apply", "--verbose"]) == 1
    err = capsys.readouterr().err
    if fails == "new_op":
        assert err == ("refused: could not write the operation record ([Errno 28] No space "
                       "left on device). The journal is written before any row is touched, so "
                       "nothing landed - fix the space or permissions under {0} and re-run.\n"
                       .format(env.ops_dir))
        assert ct.list_ops(env) == []
    else:
        op = the_op(env)
        assert op.manifest["status"] == "journaled"
        assert err == ("refused: could not save the operation record in {0} ([Errno 28] No "
                       "space left on device). Nothing landed; the op is left at 'journaled' - "
                       "{1} closes it.\n".format(op.op_dir, recover_hint(op.manifest["op_id"])))
    assert raws(paths) == before and ct.read_lock(env) is None


def test_the_apply_replans_under_the_lock_and_says_what_changed(
        capsys, monkeypatch, mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    real = ct.acquire_lock

    def change_then_lock(env_, op_id):
        # cmd_relink plans and applies in one call: the store changes between the two
        os.unlink(paths["local_p3"])
        write_transcript(env, "C--p", LD, t_entries())
        put_row(env, write_row, A1, "local_d1", LD, "Northwind notes",
                spawnedFrom=side_link("local_p1"))
        paths["local_d2"] = put_row(env, write_row, A2, "local_d2", LD, "Northwind notes")
        return real(env_, op_id)
    monkeypatch.setattr(ct, "acquire_lock", change_then_lock)
    assert ct.cmd_relink(env, ns(apply=True)) == 0
    out = capsys.readouterr().out.splitlines()
    assert any(ln.startswith("   local_c3  ") for ln in out)       # the plan printed it
    om = the_op(env).manifest
    assert om["since_plan"] == {"changed": 0, "dropped": 1, "gained": 1}
    assert names(om["rows"]) == ["local_c2.json", "local_d2.json"]
    assert [(e["name"], note_codes(e)) for e in om["left_out"]] == [
        ("local_c3.json", ["parent_not_in_account"])]
    i = out.index("result  : completed")
    assert out[i + 1:] == [
        "links   : 2 added, 1 left out (no row for the parent in that account 1); since the "
        "plan: 1 dropped, 1 gained", REOPEN]
    assert data_of(paths["local_d2"])["spawnedFrom"] == tool_link("local_p2")
    assert "spawnedFrom" not in data_of(paths["local_c3"])


def test_nothing_to_add_journals_nothing(capsys, mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    relink_store(env, write_transcript, write_row, local_c2={"spawnedFrom": side_link("local_p2")},
                 local_c3={"spawnedFrom": side_link("local_p3")})
    assert ct.cmd_relink(env, ns(apply=True)) == 0
    out = capsys.readouterr().out.splitlines()
    assert out.count("nothing to add") == 1 and out[-1] == "result  : unchanged"
    assert ct.list_ops(env) == [] and ct.read_lock(env) is None


def test_a_plan_with_only_left_out_rows_prints_once_and_never_the_apply_hint(
        capsys, mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    for stem in ("local_p2", "local_p3"):
        os.unlink(paths[stem])
    totals = "links   : 2 left out (no row for the parent in that account 2)"
    assert ct.cmd_relink(env, ns()) == 0
    out = capsys.readouterr().out.splitlines()
    assert out.count(totals) == 1 and out.count("nothing to add") == 1
    assert not any("dry run" in ln for ln in out)
    assert ct.cmd_relink(env, ns(apply=True)) == 0
    out = capsys.readouterr().out.splitlines()
    assert out.count(totals) == 1 and out.count("nothing to add") == 1
    assert out[-1] == "result  : unchanged"
    assert out.index("nothing to add") < out.index("result  : unchanged")
    assert not any("dry run" in ln for ln in out)
    assert ct.cmd_relink(env, ns(apply=True, json=True)) == 0
    got = json.loads(capsys.readouterr().out)
    assert got["rows"] == [] and len(got["left_out"]) == 2 and got["result"] == "unchanged"
    assert ct.list_ops(env) == []


def test_a_locked_replan_that_leaves_nothing_says_so_after_the_result(
        capsys, monkeypatch, mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    real = ct.acquire_lock

    def remove_parents_then_lock(env_, op_id):
        for stem in ("local_p2", "local_p3"):
            os.unlink(paths[stem])
        return real(env_, op_id)
    monkeypatch.setattr(ct, "acquire_lock", remove_parents_then_lock)
    assert ct.cmd_relink(env, ns(apply=True)) == 0
    out = capsys.readouterr().out.splitlines()
    i = out.index("result  : unchanged")
    assert out[i + 1:] == [
        "links   : 2 left out (no row for the parent in that account 2); since the plan: "
        "2 dropped",
        "nothing to add"]
    assert out.count("nothing to add") == 1
    assert ct.list_ops(env) == [] and ct.read_lock(env) is None


def test_an_unreadable_row_at_apply_refuses_and_journals_nothing(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    m = plan(env)
    with open(paths["local_p2"], "w", encoding="utf-8") as fh:
        fh.write("{not json")
    before = raws(paths)
    with pytest.raises(ct.Refusal) as exc:
        ct.run_relink(env, m)
    assert "the row 'local_p2.json'" in str(exc.value) and "could not be read" in str(exc.value)
    assert ct.list_ops(env) == [] and ct.read_lock(env) is None and raws(paths) == before


def test_rows_left_out_still_exit_0_and_json_carries_the_result(
        capsys, mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    os.unlink(paths["local_p3"])
    assert ct.cmd_relink(env, ns(apply=True, json=True)) == 0
    got = json.loads(capsys.readouterr().out)
    assert got["result"] == "completed" and got["op_id"] == the_op(env).manifest["op_id"]
    assert [(r["name"], r["written"]) for r in got["rows"]] == [("local_c2.json", True)]
    assert [e["name"] for e in got["left_out"]] == ["local_c3.json"]
    assert got["since_plan"] == {"changed": 0, "dropped": 0, "gained": 0}
    # the text report: "N added" is the rows the journal holds written
    env = mkenv(tmp_path / "text")
    paths = relink_store(env, write_transcript, write_row)
    os.unlink(paths["local_p3"])
    assert ct.cmd_relink(env, ns(apply=True)) == 0
    out = capsys.readouterr().out.splitlines()
    written = sum(1 for r in the_op(env).manifest["rows"] if r["written"] is True)
    assert written == 1
    assert out[out.index("result  : completed") + 1:] == [
        "links   : 1 added, 1 left out (no row for the parent in that account 1)", REOPEN]


def test_the_help_texts():
    p = ct.build_parser()
    sub = next(a for a in p._actions if isinstance(a, argparse._SubParsersAction))
    rl = sub.choices["relink"]
    assert {c.dest: c.help for c in sub._choices_actions}["relink"] == (
        "give existing rows of a side session the parent link another account's row carries")
    assert rl.description == (
        "Give existing rows of a side session the parent link (spawnedFrom), and its chain "
        "(startChain), that another account's row carries, translated to that account's own "
        "rows as converge writes them on a new row. Only those two keys change, and only "
        "where a row has no link object; rows whose accounts disagree are left out with the "
        "reason. Undoable per key.")
    helps = {a.dest: a.help for a in rl._actions}
    assert helps["only"] == (
        "narrow to one conversation (a title substring, or a cliSessionId prefix) or to one "
        "row (local_ and a prefix of its id) - must resolve to exactly one; a conversation "
        "that leaves more than one row to change refuses and lists their ids")
    assert helps["account"] == (
        "narrow to one account's store: an account id, org id, store path or email substring "
        "that resolves to exactly one account, the signed-in account's included")
    assert helps["live"] == ("assert which account the desktop app is signed into (RULING 5), "
                             "when the identity files disagree")
    assert helps["apply"] == (
        "actually add the links. Needs the desktop app fully closed and no unfinished "
        "operation. Exits 0 when rows were left out - each is counted by its reason")
    assert helps["json"] == "print the plan as JSON"
    got = p.parse_args(["relink"])
    assert (got.only, got.account, got.live, got.apply, got.json, got.verbose,
            got.anonymize) == ("", "", "", False, False, False, False)
    got = p.parse_args(["relink", "--only", "local_c2", "--account", "bob@", "--live",
                        "alice@", "--apply", "--json", "--verbose"])
    assert (got.only, got.account, got.live, got.apply, got.json, got.verbose) == (
        "local_c2", "bob@", "alice@", True, True, True)


def test_odd_titles_print_through_cmd_relink_on_a_cp1252_console(
        monkeypatch, mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    odd = "Café ☕ Northwind"
    titled = {stem: {"title": odd} for stem in ("local_p1", "local_p2", "local_p3")}
    titled.update({stem: {"title": "left_out"} for stem in ("local_c1", "local_c2", "local_c3")})
    paths = relink_store(env, write_transcript, write_row, **titled)
    os.unlink(paths["local_p3"])
    for verbose in (False, True):
        stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
        monkeypatch.setattr(sys, "stdout", stream)
        assert ct.cmd_relink(env, ns(verbose=verbose)) == 0
        stream.flush()
        printed = stream.buffer.getvalue().decode("cp1252").splitlines()
        assert "   local_c2  'left_out'   link under 'Café ? Northwind'" in printed
        assert "dry run - pass --apply to add them" in printed
        assert any("Caf" in ln for ln in printed) and not any("☕" in ln for ln in printed)


RULING5_REALIGN = (
    "~/.claude.json (aaaaaaaa) and config.json (cccccccc) disagree about the signed-in account "
    "(RULING 5). The designed remedy is to assert which account the desktop app is on and "
    "re-plan:\n"
    "   claude-code-sessions {0} --live <email, or acct/org as reports print it> --apply\n"
    "If it is the CLI's record that is stale, re-authenticating the CLI as the desktop's "
    "account (run 'claude', then /login) also clears this. Nothing was written.")
RULING5_CONVERGE = (
    "~/.claude.json (aaaaaaaa) and config.json (cccccccc) disagree about the signed-in account "
    "(RULING 5). The designed remedy is to assert which account the desktop app is on and "
    "re-plan:\n"
    "   claude-code-sessions converge --live <email, or acct/org as reports print it> --apply\n"
    "If it is the CLI's record that is stale, re-authenticating the CLI as the desktop's "
    "account (run 'claude', then /login) also clears this. Switching the desktop app may not "
    "- config.json has been measured both tracking a switch (2026-08-02) and keeping the "
    "previous account across one (2026-08-29) - and re-authenticating never writes it. --live "
    "exists for exactly that case. Nothing was written.")


@pytest.mark.parametrize("command", ["realign", "converge", "relink"])
def test_the_ruling5_refusal_is_one_text_per_command(command, mkenv, tmp_path):
    # realign's and converge's pass before _ruling5_recheck exists: they guard the move
    env = mkenv(tmp_path)
    identity_files(env, A1, A2)
    recheck = getattr(ct, "_{0}_recheck".format(command))
    with pytest.raises(ct.Refusal) as exc:
        recheck(env, {"live_asserted": ""})
    want = RULING5_CONVERGE if command == "converge" else RULING5_REALIGN.format(command)
    assert str(exc.value) == want


def test_a_rerun_adds_only_what_the_first_run_made_resolvable(
        capsys, mkenv, tmp_path, write_transcript, write_row):
    # (a) every chain resolved the first time: a re-run adds nothing and journals nothing
    env = mkenv(tmp_path / "base")
    relink_store(env, write_transcript, write_row)
    assert ct.cmd_relink(env, ns(apply=True)) == 0
    capsys.readouterr()
    assert ct.cmd_relink(env, ns(apply=True)) == 0
    out = capsys.readouterr().out.splitlines()
    assert out.count("nothing to add") == 1 and out[-1] == "result  : unchanged"
    assert len(ct.list_ops(env)) == 1
    # (b) Codex's two-account store: A1's row has an app-written link and no chain, A2's a
    # usable chain and no link; the first run gives A2 its link, and only the second can
    # give A1 the chain
    env = mkenv(tmp_path / "two")
    for sid in (LP, LC, LR):
        write_transcript(env, "C--p", sid, t_entries())
    for acct in (A1, A2):
        put_row(env, write_row, acct, "local_p" + NUM[acct], LP, "Northwind kickoff")
        put_row(env, write_row, acct, "local_r" + NUM[acct], LR, "Northwind board")
    c1 = put_row(env, write_row, A1, "local_c1", LC, "ACME-REVIEW side task",
                 spawnedFrom=side_link("local_p1"))
    kept = {"firstSessionId": "local_r2", "depth": 2}
    c2 = put_row(env, write_row, A2, "local_c2", LC, "ACME-REVIEW side task", startChain=kept)
    first = plan(env)
    assert [(r["name"], r["new"], r["kept"]) for r in first["rows"]] == [
        ("local_c2.json", {"spawnedFrom": tool_link("local_p2")}, {"startChain": kept})]
    assert first["rows"][0]["lineage"]["root_row"] == ""             # its kept chain unjudged
    assert [(e["name"], note_codes(e)) for e in first["left_out"]] == [
        ("local_c1.json", ["lineage_chain_dropped"])]
    c1_before, c2_before = data_of(c1), data_of(c2)
    assert ct.cmd_relink(env, ns(apply=True)) == 0
    assert data_of(c2) == {**c2_before, "spawnedFrom": tool_link("local_p2")}
    assert data_of(c1) == c1_before
    second = plan(env)
    assert [(r["name"], r["new"]) for r in second["rows"]] == [
        ("local_c1.json", {"startChain": {"firstSessionId": "local_r1", "depth": 2}})]
    assert second["left_out"] == []
    assert ct.cmd_relink(env, ns(apply=True)) == 0
    assert data_of(c1) == {**c1_before,
                           "startChain": {"firstSessionId": "local_r1", "depth": 2}}
    capsys.readouterr()
    assert ct.cmd_relink(env, ns(apply=True)) == 0
    out = capsys.readouterr().out.splitlines()
    assert out.count("nothing to add") == 1 and out[-1] == "result  : unchanged"
    assert len(ct.list_ops(env)) == 2


# The contracts earlier reviews set for the apply, each pinned on its own.

@pytest.mark.parametrize("key", ["live_asserted", "only", "account_filter"])
@pytest.mark.parametrize("value", [7, None])
def test_a_scope_the_plan_does_not_hold_as_a_string_refuses(
        key, value, mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    m = plan(env)
    m[key] = value
    before = raws(paths)
    with pytest.raises(ct.Refusal) as exc:
        ct.run_relink(env, m)
    assert str(exc.value) == (
        "this plan's {0!r} is not a string, so the apply cannot repeat its scope under the "
        "lock. Nothing was written - re-run to replan.".format(key))
    assert ct.list_ops(env) == [] and raws(paths) == before and ct.read_lock(env) is None


def test_the_apply_shape_checks_its_record_before_journaling_it(
        monkeypatch, mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    m = plan(env)
    before = raws(paths)
    seen = []

    def damaged(record):
        seen.append(record)
        return "row 0 has no usable 'new'"
    monkeypatch.setattr(ct, "_relink_shape_error", damaged)
    with pytest.raises(ct.Refusal) as exc:
        ct.run_relink(env, m)
    assert str(exc.value) == ("this relink's own record is damaged (row 0 has no usable 'new'); "
                              "refusing rather than journal it or write from it. Nothing was "
                              "written.")
    assert len(seen) == 1 and seen[0] is m and names(m["rows"]) == ["local_c2.json",
                                                                    "local_c3.json"]
    assert ct.list_ops(env) == [] and raws(paths) == before and ct.read_lock(env) is None


def test_the_row_reader_and_writer(mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    r = dict(plan(env)["rows"][0])
    roots = ct._retitle_roots(env)
    assert ct._relink_read(r, roots) == ("present", data_of(paths["local_c2"]),
                                         raw_of(paths["local_c2"]))
    value = ct._relink_apply_new(data_of(paths["local_c2"]), r["new"])
    assert ct._relink_put(r, value, roots, THEN) is True
    assert raw_of(paths["local_c2"]) == ct._relink_row_bytes(value)
    # a parse failure, a duplicate key and a non-object are unreadable, with their bytes
    for body in (b"{not json", b'{"title": "A", "title": "B"}', b"[1]"):
        with open(r["dest_path"], "wb") as fh:
            fh.write(body)
        assert ct._relink_read(r, roots) == ("unreadable", None, body)
    os.unlink(r["dest_path"])
    assert ct._relink_read(r, roots) == ("absent", None, None)
    os.makedirs(r["dest_path"])                                  # an OSError on open
    assert ct._relink_read(r, roots) == ("unreadable", None, None)
    os.rmdir(r["dest_path"])
    # containment first, for the writer as for the reader: nothing lands outside the roots
    out = os.path.join(str(tmp_path), "x", A2, O2)
    away = dict(r, dest_path=os.path.join(out, "local_c2.json"), store_path=out)
    for call in (lambda: ct._relink_read(away, roots),
                 lambda: ct._relink_put(away, value, roots, THEN)):
        with pytest.raises(ct.LayoutError):
            call()
    assert not os.path.exists(out)


def test_the_record_agrees_with_its_row_or_not(mkenv, tmp_path, write_transcript, write_row):
    m = chain_plan(mkenv(tmp_path / "chain"), write_transcript, write_row)
    r = m["rows"][0]                                  # local_c2 gains its link and chain
    data = data_of(r["dest_path"])
    assert ct._relink_row_agrees(r, data) is True
    for other in (dict(data, startChain={"firstSessionId": "local_r2", "depth": 2}),  # an object
                  dict(data, spawnedFrom=None),                    # the record says absent
                  dict(data, cwd="C:/work/elsewhere")):            # the post hash differs
        assert ct._relink_row_agrees(r, other) is False
    assert ct._relink_row_agrees(dict(r, post_sha256="0" * 64), data) is False
    # a non-object old value agrees only as the same value: False is not 0, though the post
    # bytes are the same either way
    env = mkenv(tmp_path / "false")
    relink_store(env, write_transcript, write_row, local_c2={"spawnedFrom": False})
    r = by_name(plan(env)["rows"])["local_c2.json"]
    data = data_of(r["dest_path"])
    assert r["old"] == {"spawnedFrom": {"present": True, "value": False}}
    assert ct._relink_row_agrees(r, data) is True
    assert ct._relink_row_agrees(r, dict(data, spawnedFrom=0)) is False
    assert ct._relink_row_agrees(dict(r, old={"spawnedFrom": {"present": 1, "value": False}}),
                                 data) is False
    for bad in ({}, None, dict(r, old=[]), dict(r, new="x")):
        assert ct._relink_row_agrees(bad, data) is False
    assert ct._relink_row_agrees(r, None) is False


# ---------------------------------------------------------------- the claims
# Task 7: _relink_claims (spec D13), which tells relink's undo and recovery when
# a later live operation owns a row they would put back, and the other
# operations' ownership checks, which must neither ignore a relink's claim nor
# wait forever behind one. A later operation is hand-made in its type's real
# row shape and journaled with new_op after the relink, on a clock that ticks
# per call; a move and its undo come from a real run.

UNREADABLE_CLAIM = "its record cannot be read"
A2_LABEL = "{0} ({0}/{1})".format(A2[:8], O2[:8])


def relinked(mkenv, tmp_path, write_transcript, write_row, before=None):
    """A completed relink of local_c2 and local_c3 on a monotonic clock, so every
    operation journaled after it sorts later. BEFORE(env, paths) runs first, for an
    operation journaled before the relink. Returns (env, {stem: path}, the relink's
    journaled manifest, {stem: its row})."""
    env = _monotonic_now(mkenv(tmp_path))
    paths = relink_store(env, write_transcript, write_row)
    if before is not None:
        before(env, paths)
    assert ct.run_relink(env, plan(env)) == "completed"
    (op,) = [o for o in ct.list_ops(env) if o.manifest["op_type"] == "relink"]
    return env, paths, op.manifest, {r["name"][:-len(".json")]: r for r in op.manifest["rows"]}


def journal_op(env, op_type, rows, status="completed", **top):
    """A hand-made operation journaled now, then set to STATUS."""
    op = ct.new_op(env, dict(top, op_type=op_type, rows=rows))
    if status != "journaled":
        ct.set_status(op, status)
    return op


def whole_op(env, op_type, path, written=True, status="completed", post=None):
    """A whole-row writer of OP_TYPE naming PATH, in its type's real shape: the row's
    pre-image and POST (default: other bytes), `written` as its apply marks it, and the
    store where its type keeps it."""
    raw = raw_of(path)
    store, name = os.path.split(path)
    row = {"name": name, "dest_path": path, "title": "ACME-REVIEW side task",
           "pre_b64": ct.b64(raw), "post_b64": ct.b64(raw + b" " if post is None else post),
           "is_update": True, "written": written}
    top = {"sync": {"dest_path": store},
           "retitle": {"cli_session_id": LC, "new_title": "Northwind", "store_path": store},
           "repoint": {"store_path": store, "name": name, "to_session": LQ},
           "new-row": {"store_path": store}, "converge": {}}[op_type]
    if op_type == "sync":
        row["session_id"] = LC
    if op_type in ("new-row", "converge"):
        row.update(pre_b64=None, is_update=False)
    if op_type == "converge":
        row.update(store_path=store, account=A2, label=A2_LABEL)
    return journal_op(env, op_type, [row], status=status, **top)


def realign_op(env, path, status="completed"):
    """A realign that wrote PATH's cwd, in realign's real row shape."""
    raw = raw_of(path)
    store, name = os.path.split(path)
    row = {"session_id": LC, "name": name, "dest_path": path, "store_path": store,
           "label": A2_LABEL, "account": A2, "archived": False,
           "old": {"cwd": {"present": False, "value": None}}, "new": {"cwd": "C:/work/northwind"},
           "old_folder": "", "pre_b64": ct.b64(raw),
           "post_b64": ct.b64(ct._relink_row_bytes(dict(data_of(path), cwd="C:/work/northwind"))),
           "is_update": True, "written": True}
    return journal_op(env, "realign", [row], status=status, only="", include_identical=False,
                      live_asserted="", conversations=[{"session_id": LC}], held=[], copies=[])


def like(row, **changes):
    """A copy of relink row ROW with CHANGES: a later relink's row for the same file."""
    out = json.loads(json.dumps(row))
    out.update(changes)
    return out


def set_row(op, **changes):
    """Change OP's first row and save its record; a None value takes the key out."""
    r = op.manifest["rows"][0]
    for k, v in changes.items():
        if v is None:
            r.pop(k, None)
        else:
            r[k] = v
    ct.save_manifest(op)


def claimed(claims):
    return [(c["op_id"], c["row"]["name"], c["keys"]) for c in claims]


def wrote_it(op_id):
    return "operation {0} wrote it since".format(op_id)


@pytest.mark.parametrize("op_type", ["sync", "retitle", "repoint", "new-row", "converge"])
def test_a_later_live_operation_that_wrote_the_row_claims_every_key(
        op_type, mkenv, tmp_path, write_transcript, write_row):
    env, paths, m, rows = relinked(mkenv, tmp_path, write_transcript, write_row)
    c2, c3 = rows["local_c2"], rows["local_c3"]
    later = whole_op(env, op_type, paths["local_c2"])
    whole_op(env, op_type, paths["local_c3"], written=False)    # finished, never wrote it
    oid = later.manifest["op_id"]
    assert ct._relink_claims(env, m, [c2, c3]) == [
        {"row": c2, "op_id": oid, "keys": set(c2["new"]), "why": wrote_it(oid)}]
    assert set(c2["new"]) == {"spawnedFrom"}


def moved_c(env, tmp_path, write_row, paths):
    """A real move of LC's transcript to another project folder, which rewrites
    local_c1..3; returns the completed move op."""
    # the scheme evidence plan_move reads, as tests/test_journal_reroot.py's `moved` gives it
    ev = "C:\\proj\\_ev"
    os.makedirs(os.path.join(env.projects_root, ct.encode(ev, ct.SCHEME_CURRENT)))
    write_row(env, 0, A1, O1, "local_ev", {"sessionId": "local_ev", "cliSessionId": "other",
                                           "cwd": ev, "lastActivityAt": 2})
    target = str(tmp_path / "target")
    os.makedirs(target)
    assert ct.run_move(env, ct.plan_move(env, LC, target, ct.MoveFlags())) == "completed"
    (mv,) = [o for o in ct.list_ops(env) if o.manifest["op_type"] == "move"]
    assert sorted(os.path.basename(r["path"]) for r in mv.manifest["rows"]) == [
        "local_c1.json", "local_c2.json", "local_c3.json"]
    assert not any("written" in r for r in mv.manifest["rows"])
    assert data_of(paths["local_c2"])["cwd"] == os.path.normpath(target)
    return mv


def test_a_later_move_claims_the_row_without_a_written_flag_and_its_finished_undo_does_not(
        mkenv, tmp_path, write_transcript, write_row):
    env, paths, m, rows = relinked(mkenv, tmp_path, write_transcript, write_row)
    c2 = rows["local_c2"]
    mv = moved_c(env, tmp_path, write_row, paths)
    assert claimed(ct._relink_claims(env, m, [c2])) == [
        (mv.manifest["op_id"], "local_c2.json", {"spawnedFrom"})]
    assert ct.run_undo(env, mv) == "completed"
    by_type = {o.manifest["op_type"]: o.manifest for o in ct.list_ops(env)}
    assert by_type["move"]["status"] == "undone"
    assert not any("written" in r for r in by_type["undo"]["rows"])
    # no command undoes an undo, so a finished one's claim could never clear; the row it
    # left is the move's pre-image, which holds this relink's keys at new
    assert by_type["undo"]["status"] == "completed"
    assert ct._relink_claims(env, m, [c2]) == []


def test_an_unfinished_undo_of_a_later_move_still_claims_the_row(
        mkenv, tmp_path, write_transcript, write_row):
    env, paths, m, rows = relinked(mkenv, tmp_path, write_transcript, write_row)
    c2 = rows["local_c2"]
    mv = moved_c(env, tmp_path, write_row, paths)

    def hook(point):
        if point == "after-rewriting":
            raise SimulatedCrash()
    ct._crash_hook = hook
    with pytest.raises(SimulatedCrash):
        ct.run_undo(env, mv)
    ct._crash_hook = None
    by_type = {o.manifest["op_type"]: o.manifest for o in ct.list_ops(env)}
    assert by_type["undo"]["status"] in ct.NONTERMINAL
    assert by_type["move"]["status"] == "completed"
    assert claimed(ct._relink_claims(env, m, [c2])) == [
        (mv.manifest["op_id"], "local_c2.json", {"spawnedFrom"}),
        (by_type["undo"]["op_id"], "local_c2.json", {"spawnedFrom"})]


def test_a_later_relink_claims_only_the_keys_it_wrote_or_kept(
        mkenv, tmp_path, write_transcript, write_row):
    env, paths, m, rows = relinked(mkenv, tmp_path, write_transcript, write_row)
    c2 = rows["local_c2"]
    chain = {"firstSessionId": "local_r2", "depth": 1}
    # a chain written on the link this relink wrote: the link is relied on, so it is claimed
    one = journal_op(env, "relink", [like(c2, new={"startChain": chain},
                                          kept={"spawnedFrom": c2["new"]["spawnedFrom"]})])
    assert claimed(ct._relink_claims(env, m, [c2])) == [
        (one.manifest["op_id"], "local_c2.json", {"spawnedFrom"})]
    shutil.rmtree(one.op_dir)
    # a chain written beside no kept link touches none of this relink's keys
    journal_op(env, "relink", [like(c2, new={"startChain": chain}, kept={})])
    assert ct._relink_claims(env, m, [c2]) == []


def test_a_later_realign_claims_nothing(mkenv, tmp_path, write_transcript, write_row):
    env, paths, m, rows = relinked(mkenv, tmp_path, write_transcript, write_row)
    realign_op(env, paths["local_c2"])
    realign_op(env, paths["local_c2"], status="writing")
    assert ct._relink_claims(env, m, [rows["local_c2"]]) == []


def test_a_non_terminal_later_op_naming_the_row_claims_it_written_or_not(
        mkenv, tmp_path, write_transcript, write_row):
    env, paths, m, rows = relinked(mkenv, tmp_path, write_transcript, write_row)
    c2 = rows["local_c2"]
    sync = whole_op(env, "sync", paths["local_c2"], written=False, status="writing")
    assert claimed(ct._relink_claims(env, m, [c2])) == [
        (sync.manifest["op_id"], "local_c2.json", {"spawnedFrom"})]
    shutil.rmtree(sync.op_dir)
    later = journal_op(env, "relink", [like(c2, written=False)], status="writing")
    assert claimed(ct._relink_claims(env, m, [c2])) == [
        (later.manifest["op_id"], "local_c2.json", {"spawnedFrom"})]
    ct.set_status(later, "completed")              # finished, and it never wrote the row
    assert ct._relink_claims(env, m, [c2]) == []


def test_an_earlier_or_withdrawn_op_and_a_restored_row_claim_nothing(
        mkenv, tmp_path, write_transcript, write_row):
    env, paths, m, rows = relinked(
        mkenv, tmp_path, write_transcript, write_row,
        before=lambda env, paths: whole_op(env, "sync", paths["local_c2"]))
    c2 = rows["local_c2"]
    whole_op(env, "sync", paths["local_c2"], status="undone")
    journal_op(env, "relink", [like(c2)], status="rolled_back")
    journal_op(env, "relink", [like(c2, restored=True)])
    assert [o.manifest["op_type"] for o in ct.list_ops(env)] == [
        "sync", "relink", "sync", "relink", "relink"]
    assert ct._relink_claims(env, m, [c2]) == []


@pytest.mark.parametrize("status", ["undone", "rolled_back"])
def test_an_undone_or_rolled_back_relink_claims_nothing_whatever_its_rows(
        status, mkenv, tmp_path, write_transcript, write_row):
    # spec D13: an undone or rolled-back operation has withdrawn its claim, a relink
    # included, whatever its rows' `restored`
    env, paths, m, rows = relinked(mkenv, tmp_path, write_transcript, write_row)
    c2 = rows["local_c2"]
    later = journal_op(env, "relink", [like(c2)], status=status)
    r = later.manifest["rows"][0]
    assert r["written"] is True and "restored" not in r
    assert data_of(paths["local_c2"])["spawnedFrom"] == r["new"]["spawnedFrom"]   # left at new
    assert ct._relink_claims(env, m, [c2]) == []
    ct.set_status(later, "completed")
    assert claimed(ct._relink_claims(env, m, [c2])) == [
        (later.manifest["op_id"], "local_c2.json", {"spawnedFrom"})]


@pytest.mark.parametrize("bad", ["{not json", '{"op_id": "x", "rows": "nope"}',
                                 '{"op_id": "x", "status": "completed", "rows": [5]}'])
def test_an_unreadable_or_damaged_manifest_claims_every_row_without_crashing(
        bad, mkenv, tmp_path, write_transcript, write_row):
    env, paths, m, rows = relinked(mkenv, tmp_path, write_transcript, write_row)
    c2, c3 = rows["local_c2"], rows["local_c3"]
    sync = whole_op(env, "sync", paths["local_c3"])     # sorts before OPEN_ID's folder
    put_op(env, OPEN_ID, bad)
    sid = sync.manifest["op_id"]
    # named by its folder, whatever op_id its record holds; a record with no history
    # counts as later, so a damaged one is never passed over as the earliest of all
    assert ct._relink_claims(env, m, [c2, c3]) == [
        {"row": c3, "op_id": sid, "keys": {"spawnedFrom"}, "why": wrote_it(sid)},
        {"row": c2, "op_id": OPEN_ID, "keys": {"spawnedFrom"}, "why": UNREADABLE_CLAIM},
        {"row": c3, "op_id": OPEN_ID, "keys": {"spawnedFrom"}, "why": UNREADABLE_CLAIM}]


@pytest.mark.parametrize("history", ["missing", [], {"at": 1_900_000_000.0},
                                     [{"status": "completed"}], [{"at": "later"}]],
                         ids=["missing", "empty", "not a list", "no time", "not a number"])
def test_a_damaged_record_with_no_usable_history_still_claims_under_its_folder(
        history, mkenv, tmp_path, write_transcript, write_row):
    env, paths, m, rows = relinked(mkenv, tmp_path, write_transcript, write_row)
    c2 = rows["local_c2"]
    body = {"op_id": "x", "op_type": "sync", "status": "completed", "rows": "nope"}
    if history != "missing":
        body["history"] = history
    put_op(env, OPEN_ID, body)
    assert ct._relink_claims(env, m, [c2]) == [
        {"row": c2, "op_id": OPEN_ID, "keys": {"spawnedFrom"}, "why": UNREADABLE_CLAIM}]


@pytest.mark.parametrize("op_type, status, when, claims", [
    ("sync", "undone", "later", False),
    ("relink", "rolled_back", "later", False),
    ("relink", "undone", "later", False),
    ("undo", "completed", "later", False),         # no command undoes an undo
    ("sync", "completed", "earlier", False),
    ("sync", "completed", "later", True),          # live: the damage is a claim
    ("undo", "rewriting", "later", True)])
def test_an_earlier_or_withdrawn_damaged_record_claims_nothing(
        op_type, status, when, claims, mkenv, tmp_path, write_transcript, write_row):
    # liveness before damage: verdicts that read no row pass a damaged record over as
    # they pass a sound one, so nothing waits on a record no command can undo
    env, paths, m, rows = relinked(mkenv, tmp_path, write_transcript, write_row)
    c2 = rows["local_c2"]
    at = m["history"][0]["at"] + (100 if when == "later" else -100)
    put_op(env, OPEN_ID, {"op_id": OPEN_ID, "op_type": op_type, "status": status,
                          "history": [{"status": status, "at": at}], "rows": "nope"})
    assert claimed(ct._relink_claims(env, m, [c2])) == (
        [(OPEN_ID, "local_c2.json", {"spawnedFrom"})] if claims else [])


def test_a_sort_key_that_cannot_be_computed_counts_as_later(
        mkenv, tmp_path, write_transcript, write_row):
    env, paths, m, rows = relinked(mkenv, tmp_path, write_transcript, write_row)
    sync = whole_op(env, "sync", paths["local_c2"])
    sync.manifest["history"] = []                  # _op_sort_key cannot index it
    ct.save_manifest(sync)
    with pytest.raises(IndexError):
        ct._op_sort_key(sync.manifest)
    assert claimed(ct._relink_claims(env, m, [rows["local_c2"]])) == [
        (sync.manifest["op_id"], "local_c2.json", {"spawnedFrom"})]


def test_the_lock_file_and_stray_entries_are_not_operations(
        mkenv, tmp_path, write_transcript, write_row):
    env, paths, m, rows = relinked(mkenv, tmp_path, write_transcript, write_row)
    ct.acquire_lock(env, "undo-" + m["op_id"])     # held through every undo and recovery
    try:
        os.makedirs(os.path.join(env.ops_dir, "stray"))
        with open(os.path.join(env.ops_dir, "notes.txt"), "w", encoding="utf-8") as fh:
            fh.write("not an operation")
        assert sorted(os.listdir(env.ops_dir)) == sorted(
            [m["op_id"], "lock", "notes.txt", "stray"])
        assert ct._relink_claims(env, m, [rows["local_c2"]]) == []
    finally:
        ct.release_lock(env)


def test_a_missing_journal_claims_nothing_and_an_unlistable_one_claims_every_row(
        mkenv, tmp_path, write_transcript, write_row):
    env, paths, m, rows = relinked(mkenv, tmp_path, write_transcript, write_row)
    c2 = rows["local_c2"]
    shutil.rmtree(env.ops_dir)
    assert ct._relink_claims(env, m, [c2]) == []
    with open(env.ops_dir, "w", encoding="utf-8") as fh:   # there, and not a folder
        fh.write("x")
    (c,) = ct._relink_claims(env, m, [c2])
    assert (c["row"], c["op_id"], c["keys"]) == (c2, "", {"spawnedFrom"})
    assert c["why"].startswith("the journal folder could not be listed (")


def test_retitle_claim_passes_an_earlier_relink_and_a_restored_row(
        mkenv, tmp_path, write_transcript, write_row):
    env, paths, m, rows = relinked(mkenv, tmp_path, write_transcript, write_row)
    c2 = rows["local_c2"]
    raw = raw_of(paths["local_c2"])
    assert hashlib.sha256(raw).hexdigest() == c2["post_sha256"]
    rt = whole_op(env, "retitle", paths["local_c2"], post=raw)     # the same bytes
    mine, r = rt.manifest, rt.manifest["rows"][0]
    assert ct._retitle_claimed_elsewhere(env, mine, r) is None     # the relink is earlier
    later = journal_op(env, "relink", [like(c2)])
    lid = later.manifest["op_id"]
    assert ct._retitle_claimed_elsewhere(env, mine, r) == lid
    set_row(later, restored=True)
    assert ct._retitle_claimed_elsewhere(env, mine, r) is None
    set_row(later, restored=None, post_sha256="0" * 64)    # its bytes are not these
    assert ct._retitle_claimed_elsewhere(env, mine, r) is None
    set_row(later, post_sha256=7)                         # it cannot be told they are not
    assert ct._retitle_claimed_elsewhere(env, mine, r) == lid
    set_row(later, post_sha256=c2["post_sha256"])
    for status in ("undone", "rolled_back"):             # spec D13: the claim is withdrawn
        ct.set_status(later, status)
        assert "restored" not in later.manifest["rows"][0]
        assert ct._retitle_claimed_elsewhere(env, mine, r) is None


def test_repoint_claim_counts_only_a_later_unrestored_relink_not_undone(
        mkenv, tmp_path, write_transcript, write_row):
    env, paths, m, rows = relinked(mkenv, tmp_path, write_transcript, write_row)
    c2 = rows["local_c2"]
    rp = whole_op(env, "repoint", paths["local_c2"])
    mine, r = rp.manifest, rp.manifest["rows"][0]
    assert ct._repoint_claimed_later(env, mine, r) is None         # the relink is earlier
    later = journal_op(env, "relink", [like(c2)])
    lid = later.manifest["op_id"]
    assert ct._repoint_claimed_later(env, mine, r) == lid
    set_row(later, restored=True)
    assert ct._repoint_claimed_later(env, mine, r) is None
    for status in ("undone", "rolled_back"):             # spec D13: the claim is withdrawn
        ct.set_status(later, status)
        for restored in (True, None):
            set_row(later, restored=restored)
            assert ct._repoint_claimed_later(env, mine, r) is None
    # an unwritten row counts only while the later relink is unfinished
    ct.set_status(later, "completed")
    set_row(later, written=False)
    assert ct._repoint_claimed_later(env, mine, r) is None
    ct.set_status(later, "writing")
    assert ct._repoint_claimed_later(env, mine, r) == lid


def test_realign_claim_passes_a_later_relink(mkenv, tmp_path, write_transcript, write_row):
    env, paths, m, rows = relinked(
        mkenv, tmp_path, write_transcript, write_row,
        before=lambda env, paths: realign_op(env, paths["local_c2"]))
    (ra,) = [o for o in ct.list_ops(env) if o.manifest["op_type"] == "realign"]
    mine, r = ra.manifest, ra.manifest["rows"][0]
    assert ct._op_sort_key(m) > ct._op_sort_key(mine) and rows["local_c2"]["written"] is True
    assert ct._realign_claimed_later(env, mine, r) is None
    later = realign_op(env, paths["local_c2"])           # the check still looks past it
    assert ct._realign_claimed_later(env, mine, r) == later.manifest["op_id"]


_RELINK_DAMAGE = {
    '"rows": "nope"': lambda m: m.update(rows="nope"),
    '"rows": [5]': lambda m: m.update(rows=[5]),
    "a row whose dest_path is 7": lambda m: m["rows"][0].update(dest_path=7),
    "a row whose session_id is a list": lambda m: m["rows"][0].update(session_id=[LC]),
}


@pytest.mark.parametrize("damage", ['"rows": "nope"', '"rows": [5]',
                                    "a row whose dest_path is 7"])
def test_a_malformed_later_relink_is_a_claim_in_the_retitle_and_repoint_checks(
        damage, mkenv, tmp_path, write_transcript, write_row):
    env, paths, m, rows = relinked(mkenv, tmp_path, write_transcript, write_row)
    # unwritten, so neither of the two is a claimant in the other's check
    rt = whole_op(env, "retitle", paths["local_c2"], written=False,
                  post=raw_of(paths["local_c2"]))
    rp = whole_op(env, "repoint", paths["local_c2"], written=False)
    bad = journal_op(env, "relink", [like(rows["local_c2"])])     # names local_c2
    _RELINK_DAMAGE[damage](bad.manifest)
    ct.save_manifest(bad)
    oid = bad.manifest["op_id"]
    assert ct._retitle_claimed_elsewhere(env, rt.manifest, rt.manifest["rows"][0]) == oid
    assert ct._repoint_claimed_later(env, rp.manifest, rp.manifest["rows"][0]) == oid


@pytest.mark.parametrize("which, claims", [
    ("an earlier relink, its rows damaged", False),
    ("an undone relink, its rows damaged", False),
    ("a rolled-back relink, its rows damaged", False),
    ("a later relink with no history", True)])      # its sort key cannot be computed
def test_an_earlier_or_withdrawn_damaged_relink_is_passed_by_the_retitle_and_repoint_checks(
        which, claims, mkenv, tmp_path, write_transcript, write_row):
    # liveness before damage: an earlier or withdrawn relink is passed over before its
    # rows are read, so neither check waits on a relink no command can undo
    env, paths, m, rows = relinked(mkenv, tmp_path, write_transcript, write_row)
    (real,) = [o for o in ct.list_ops(env) if o.manifest["op_type"] == "relink"]
    rt = whole_op(env, "retitle", paths["local_c2"], written=False,
                  post=raw_of(paths["local_c2"]))
    rp = whole_op(env, "repoint", paths["local_c2"], written=False)
    if which.startswith("an earlier"):
        op = real
    else:
        op = journal_op(env, "relink", [like(rows["local_c3"])])   # names local_c3 only
        if which.startswith("an undone"):
            ct.set_status(op, "undone")
        elif which.startswith("a rolled-back"):
            ct.set_status(op, "rolled_back")
    if which.endswith("no history"):
        del op.manifest["history"]
    else:
        op.manifest["rows"] = "nope"
    ct.save_manifest(op)
    want = op.manifest["op_id"] if claims else None
    assert ct._retitle_claimed_elsewhere(env, rt.manifest, rt.manifest["rows"][0]) == want
    assert ct._repoint_claimed_later(env, rp.manifest, rp.manifest["rows"][0]) == want


@pytest.mark.parametrize("damage", ['"rows": "nope"', "a row whose session_id is a list"])
def test_a_malformed_relink_does_not_hide_a_later_realigns_claim(
        damage, mkenv, tmp_path, write_transcript, write_row):
    env, paths, m, rows = relinked(
        mkenv, tmp_path, write_transcript, write_row,
        before=lambda env, paths: realign_op(env, paths["local_c2"]))
    ops = {o.manifest["op_type"]: o for o in ct.list_ops(env)}
    _RELINK_DAMAGE[damage](ops["relink"].manifest)
    ct.save_manifest(ops["relink"])
    later = realign_op(env, paths["local_c2"])
    mine = ops["realign"].manifest
    assert ct._realign_claimed_later(env, mine, mine["rows"][0]) == later.manifest["op_id"]


def test_rotation_keeps_a_terminal_relink_a_live_claimant_names(
        mkenv, tmp_path, write_transcript, write_row):
    env, paths, m, rows = relinked(mkenv, tmp_path, write_transcript, write_row)
    for i in range(10):
        journal_op(env, "retitle", [], cli_session_id="x%d" % i, new_title="Northwind")
    live = whole_op(env, "retitle", paths["local_c2"], written=False, status="writing")
    folder = os.path.join(env.ops_dir, m["op_id"])
    assert len([o for o in ct.list_ops(env) if o.manifest["status"] == "completed"]) == 11
    assert ct.rotate_ops(env) == [] and os.path.isdir(folder)
    ct.set_status(live, "completed")                 # nothing unfinished names the row now
    assert m["op_id"] in ct.rotate_ops(env) and not os.path.exists(folder)


def test_an_earlier_relink_does_not_block_a_repoint_rollback(
        monkeypatch, mkenv, tmp_path, write_transcript, write_row):
    env, paths, m, rows = relinked(mkenv, tmp_path, write_transcript, write_row)
    write_transcript(env, "C--p", LQ, t_entries())
    pre = raw_of(paths["local_c2"])
    rpm = ct.plan_repoint(env, ct.RepointFlags(only="ACME-REVIEW", to_session=LQ,
                                               store=A2[:8]))
    assert same_file(rpm["rows"][0]["dest_path"], paths["local_c2"])
    real = ct.set_status

    def stall(op, status):                             # the write lands; the run dies
        if op.manifest.get("op_type") == "repoint" and status == "completed":
            raise SimulatedCrash()
        real(op, status)
    monkeypatch.setattr(ct, "set_status", stall)
    with pytest.raises(SimulatedCrash):
        ct.run_repoint(env, rpm)
    monkeypatch.setattr(ct, "set_status", real)
    (pending,) = ct.nonterminal_ops(env)
    assert pending.manifest["rows"][0]["written"] is True
    assert data_of(paths["local_c2"])["cliSessionId"] == LQ
    assert ct.recover_op(env, pending, "back") == "rolled_back"
    assert raw_of(paths["local_c2"]) == pre
    (done,) = [o for o in ct.list_ops(env) if o.manifest["op_type"] == "repoint"]
    assert done.manifest["status"] == "rolled_back"
    assert "rollback_declined" not in done.manifest


# ---------------------------------------------------------------------- undo
# Task 8: undo_relink puts back, row by row and key by key, each key the relink
# wrote that still holds its new value (D8). A key the app changed since is left
# and named, as is a row that is gone or opens another conversation now (D19). A
# later live operation's claim refuses the whole undo before anything is written
# (D13), and an unfinished one holds it (D18). cmd_undo previews and dispatches
# it, and the window parks a relink as it parks a realign.

APP_CHANGED = "the app changed it since"
GONE = "gone from the store"
REPOINTED = "now opens another conversation"
EARLIER = {"op_id": "20000101T000000Z-eeeeee",               # an earlier relink's record
           "history": [{"status": "journaled", "at": 1_000_000_000.0}]}


def undo_then(op_id):
    # it names the op: a plain 'undo --apply' takes the latest completed one
    return "The relink stays 'completed' - run 'undo --id {0} --apply' again to resume " \
           "it.".format(op_id)


def op_of(env, op_id):
    """Operation OP_ID as list_ops reads it from disk now."""
    (op,) = [o for o in ct.list_ops(env) if o.manifest["op_id"] == op_id]
    return op


def relink_now(env):
    """A completed relink of what the store lacks now; returns its op as list_ops reads it."""
    m = plan(env)
    assert ct.run_relink(env, m) == "completed"
    return op_of(env, m["op_id"])


def applied(env, write_transcript, write_row, **store):
    """relink_store(**STORE) on a clock that ticks per call, then a completed relink of
    local_c2 and local_c3. Returns ({stem: path}, the relink op)."""
    _monotonic_now(env)
    paths = relink_store(env, write_transcript, write_row, **store)
    op = relink_now(env)
    assert names(op.manifest["rows"]) == ["local_c2.json", "local_c3.json"]
    return paths, op


def row_of(op, stem):
    (r,) = [r for r in op.manifest["rows"] if r["name"] == stem + ".json"]
    return r


def undo(env, op):
    """undo_relink on OP as the journal holds it now: (its result, the record after)."""
    oid = op.manifest["op_id"]
    final = ct.undo_relink(env, op_of(env, oid))
    return final, op_of(env, oid).manifest


def undo_refusal(env, op):
    with pytest.raises(ct.Refusal) as exc:
        ct.undo_relink(env, op_of(env, op.manifest["op_id"]))
    return str(exc.value)


def record_of(op):
    """OP's record as its manifest file holds it, read without list_ops."""
    return data_of(os.path.join(op.op_dir, "manifest.json"))


def entry(row, what, why):
    return {"name": row["name"], "label": row["label"], "what": what, "why": why}


def crash_at(point):
    def hook(p):
        if p == point:
            raise SimulatedCrash()
    return hook


def test_undo_takes_out_exactly_what_relink_added(mkenv, tmp_path, write_transcript, write_row):
    env = _monotonic_now(mkenv(tmp_path))
    paths = relink_store(env, write_transcript, write_row, local_c2=APP_KEYS)
    before = {stem: data_of(p) for stem, p in paths.items()}
    others = raws(paths, [s for s in paths if s not in ("local_c2", "local_c3")])
    op = relink_now(env)
    assert all("spawnedFrom" in data_of(paths[s]) for s in ("local_c2", "local_c3"))
    final, om = undo(env, op)
    assert final == "undone"
    for stem in ("local_c2", "local_c3"):
        after = data_of(paths[stem])
        assert after == before[stem] and list(after) == list(before[stem])
    assert raws(paths, others) == others
    assert om["status"] == "undone" and om["history"][-1]["status"] == "undone"
    assert [r["restored"] for r in om["rows"]] == [True, True]
    assert om["undo_left"] == [] and ct._relink_shape_error(om) is None
    assert ct.read_lock(env) is None


def test_undo_keeps_the_keys_the_app_added_since(mkenv, tmp_path, write_transcript, write_row):
    env = _monotonic_now(mkenv(tmp_path))
    paths = relink_store(env, write_transcript, write_row)
    before = data_of(paths["local_c2"])
    op = relink_now(env)
    set_keys(paths["local_c2"], lastFocusedAt=1_800_000_000_000)        # the app, on focus
    final, om = undo(env, op)
    assert final == "undone" and om["undo_left"] == []
    after = data_of(paths["local_c2"])
    assert after == dict(before, lastFocusedAt=1_800_000_000_000)
    assert list(after) == list(before) + ["lastFocusedAt"]


def test_undo_leaves_and_names_a_key_the_app_changed(
        capsys, mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths, op = applied(env, write_transcript, write_row)
    c2 = row_of(op, "local_c2")
    assert c2["label"] == A2_LABEL
    changed = dict(c2["new"]["spawnedFrom"], taskId="task_app")
    set_keys(paths["local_c2"], spawnedFrom=changed)
    assert ct.cmd_undo(env, undo_ns(apply=True)) == 0
    line = "left    : local_c2 -> {0}: spawnedFrom (the app changed it since)".format(A2_LABEL)
    assert capsys.readouterr().out.splitlines() == ["result: undone", line]
    om = op_of(env, op.manifest["op_id"]).manifest
    assert om["undo_left"] == [{"name": "local_c2.json", "label": A2_LABEL,
                                "what": "spawnedFrom", "why": APP_CHANGED}]
    assert ct._relink_left_line(om["undo_left"][0]) == line
    assert data_of(paths["local_c2"])["spawnedFrom"] == changed
    assert "spawnedFrom" not in data_of(paths["local_c3"])
    # restored: no key the relink wrote is left at its new value on the row
    assert [r["restored"] for r in om["rows"]] == [True, True]


def test_undo_puts_back_one_key_and_leaves_the_other_the_app_changed(
        mkenv, tmp_path, write_transcript, write_row):
    # the one write that keeps part of what the relink wrote: the link still at new goes
    # back, and the chain the app changed is neither put back over nor reported wrongly
    env = _monotonic_now(mkenv(tmp_path))
    paths = relink_store(env, write_transcript, write_row, chain=CHAIN, roots=True)
    before = data_of(paths["local_c2"])
    op = relink_now(env)
    c2 = row_of(op, "local_c2")
    assert list(c2["new"]) == ["spawnedFrom", "startChain"]
    changed = dict(c2["new"]["startChain"], depth=9)
    set_keys(paths["local_c2"], startChain=changed)
    final, om = undo(env, op)
    assert final == "undone"
    after = data_of(paths["local_c2"])
    assert after == dict(before, startChain=changed)
    assert list(after) == list(before) + ["startChain"]
    assert om["undo_left"] == [entry(c2, "startChain", APP_CHANGED)]
    assert row_of(types.SimpleNamespace(manifest=om), "local_c2")["restored"] is True


@pytest.mark.parametrize("change", ["gone", "repointed"])
def test_undo_skips_and_names_a_missing_or_repointed_row(
        change, mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths, op = applied(env, write_transcript, write_row)
    c2 = row_of(op, "local_c2")
    asked = [like(c2)]                       # an earlier relink's row for the same file
    assert claimed(ct._relink_claims(env, EARLIER, asked)) == [
        (op.manifest["op_id"], "local_c2.json", {"spawnedFrom"})]
    if change == "gone":
        os.unlink(paths["local_c2"])
    else:
        set_keys(paths["local_c2"], cliSessionId=LQ)
    final, om = undo(env, op)
    assert final == "undone"
    assert om["undo_left"] == [entry(c2, "the row", GONE if change == "gone" else REPOINTED)]
    r2, r3 = om["rows"]
    assert r3["restored"] is True and "spawnedFrom" not in data_of(paths["local_c3"])
    if change == "gone":
        assert r2["restored"] is True and not os.path.exists(paths["local_c2"])
    else:
        # D19: the link stays on the row, so the record does not call it restored
        assert "restored" not in r2
        assert data_of(paths["local_c2"])["spawnedFrom"] == c2["new"]["spawnedFrom"]
    # undone, the relink claims nothing (spec D13), whatever its rows' restored
    assert ct._relink_claims(env, EARLIER, asked) == []


def test_an_undone_relink_with_a_key_left_at_new_does_not_block_an_earlier_undo(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths, r1 = applied(env, write_transcript, write_row, roots=True)
    set_keys(paths["local_c1"], startChain=CHAIN)
    r2 = relink_now(env)                     # the chains, on the links r1 wrote
    assert names(r2.manifest["rows"]) == ["local_c2.json", "local_c3.json"]
    for r in r2.manifest["rows"]:
        assert set(r["new"]) == {"startChain"} and set(r["kept"]) == {"spawnedFrom"}
    chain2 = row_of(r2, "local_c2")["new"]["startChain"]
    set_keys(paths["local_c2"], cliSessionId=LQ)
    final, m2 = undo(env, r2)
    assert final == "undone"
    assert m2["undo_left"] == [entry(row_of(r2, "local_c2"), "the row", REPOINTED)]
    assert "restored" not in m2["rows"][0] and m2["rows"][1]["restored"] is True
    assert data_of(paths["local_c2"])["startChain"] == chain2          # left at new
    assert "startChain" not in data_of(paths["local_c3"])
    # r2 is undone, so it claims nothing: r1's undo neither refuses naming it, which no
    # command could undo again, nor leaves local_c3's link in
    final, m1 = undo(env, r1)
    assert final == "undone"
    assert m1["undo_left"] == [entry(row_of(r1, "local_c2"), "the row", REPOINTED)]
    assert "spawnedFrom" not in data_of(paths["local_c3"])
    assert data_of(paths["local_c2"])["spawnedFrom"] == row_of(r1, "local_c2")["new"]["spawnedFrom"]


def test_undo_restores_a_non_object_exactly(mkenv, tmp_path, write_transcript, write_row):
    env = _monotonic_now(mkenv(tmp_path))
    paths = relink_store(env, write_transcript, write_row,
                         local_c2={"spawnedFrom": False, "model": "model-x"})
    before = data_of(paths["local_c2"])
    assert list(before)[-2:] == ["spawnedFrom", "model"]
    op = relink_now(env)
    assert row_of(op, "local_c2")["old"] == {"spawnedFrom": {"present": True, "value": False}}
    assert list(data_of(paths["local_c2"])) == list(before)        # replaced where it stood
    assert undo(env, op)[0] == "undone"
    after = data_of(paths["local_c2"])
    assert after == before and list(after) == list(before) and after["spawnedFrom"] is False


@pytest.mark.parametrize("changed", [False, True], ids=["put back", "changed by the app"])
def test_a_resumed_undo_after_a_crash_before_the_mark_names_nothing_false(
        changed, mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths, op = applied(env, write_transcript, write_row)
    c2 = row_of(op, "local_c2")
    if changed:
        set_keys(paths["local_c2"], spawnedFrom=dict(c2["new"]["spawnedFrom"], taskId="task_app"))
    ct._crash_hook = crash_at("relink-undo-before-mark")
    with pytest.raises(SimulatedCrash):
        ct.undo_relink(env, op_of(env, op.manifest["op_id"]))
    ct._crash_hook = None
    ct.release_lock(env)
    om = op_of(env, op.manifest["op_id"]).manifest            # the record on disk
    want = [entry(c2, "spawnedFrom", APP_CHANGED)] if changed else []
    assert om["status"] == "completed" and not any(r.get("restored") for r in om["rows"])
    assert om["undo_left"] == want                 # saved on the way out, before the mark
    assert ("spawnedFrom" in data_of(paths["local_c2"])) is changed    # put back, unless changed
    assert "spawnedFrom" in data_of(paths["local_c3"])
    # the resumed undo reads local_c2 again: a key already at old counts as restored and
    # is not named, and an entry already recorded is not recorded twice
    final, om = undo(env, op)
    assert final == "undone" and om["undo_left"] == want
    assert [r["restored"] for r in om["rows"]] == [True, True]
    assert "spawnedFrom" not in data_of(paths["local_c3"])


def test_an_unreadable_row_refuses_the_undo_before_any_write(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths, op = applied(env, write_transcript, write_row)
    c3 = row_of(op, "local_c3")
    landed = raw_of(paths["local_c3"])
    with open(paths["local_c3"], "w", encoding="utf-8") as fh:
        fh.write("{not json")
    record = raw_of(os.path.join(op.op_dir, "manifest.json"))
    before = raw_of(paths["local_c2"])
    assert undo_refusal(env, op) == (
        "could not read row local_c3.json ({0}), so nothing was changed; the relink stays "
        "'completed' and 'undo --id {1} --apply' resumes it once the row can be read.".format(
            c3["label"], op.manifest["op_id"]))
    assert raw_of(paths["local_c2"]) == before and "spawnedFrom" in data_of(paths["local_c2"])
    assert raw_of(os.path.join(op.op_dir, "manifest.json")) == record
    om = op_of(env, op.manifest["op_id"]).manifest
    assert om["status"] == "completed" and not any("restored" in r for r in om["rows"])
    assert ct.read_lock(env) is None
    with open(paths["local_c3"], "wb") as fh:               # put right by hand
        fh.write(landed)
    assert undo(env, op)[0] == "undone"
    assert "spawnedFrom" not in data_of(paths["local_c2"])
    assert "spawnedFrom" not in data_of(paths["local_c3"])


def test_an_interrupted_undo_keeps_what_it_left_and_reports_it_once(
        monkeypatch, mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths, op = applied(env, write_transcript, write_row)
    c2, c3 = row_of(op, "local_c2"), row_of(op, "local_c3")
    set_keys(paths["local_c2"], spawnedFrom=dict(c2["new"]["spawnedFrom"], taskId="task_app"))
    real = ct.atomic_write

    def full_for_c3(path, data):
        # every other path, the manifest's among them, goes to the real writer
        if same_file(path, paths["local_c3"]):
            raise OSError(28, "No space left on device")
        return real(path, data)
    monkeypatch.setattr(ct, "atomic_write", full_for_c3)
    msg = undo_refusal(env, op)
    assert msg == "could not write row local_c3.json ({0}): [Errno 28] No space left on " \
                  "device. {1}".format(c3["label"], undo_then(op.manifest["op_id"]))
    assert "run 'undo --id {0} --apply' again".format(op.manifest["op_id"]) in msg
    monkeypatch.setattr(ct, "atomic_write", real)
    om = op_of(env, op.manifest["op_id"]).manifest
    assert om["status"] == "completed"
    assert [r.get("restored") for r in om["rows"]] == [True, None]
    assert om["undo_left"] == [entry(c2, "spawnedFrom", APP_CHANGED)]
    assert "spawnedFrom" in data_of(paths["local_c3"]) and ct.read_lock(env) is None
    final, om = undo(env, op)
    assert final == "undone"
    assert om["undo_left"] == [entry(c2, "spawnedFrom", APP_CHANGED)]
    assert "spawnedFrom" not in data_of(paths["local_c3"])


@pytest.mark.parametrize("fail_in", [(), ("undo_relink",)], ids=["saved", "way-out-save-fails"])
def test_a_failed_verify_leaves_the_row_unmarked_and_undo_again_reads_the_disk(
        fail_in, monkeypatch, mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths, op = applied(env, write_transcript, write_row)
    real = ct.atomic_write

    def wrong_for_c3(path, data):
        if same_file(path, paths["local_c3"]):
            return real(path, json.dumps({"cliSessionId": LC}).encode("utf-8"))
        return real(path, data)
    monkeypatch.setattr(ct, "atomic_write", wrong_for_c3)
    saves = record_saves(monkeypatch, fail_in=fail_in)
    with pytest.raises(ct.LayoutError) as exc:         # never the OSError, never a traceback
        ct.undo_relink(env, op_of(env, op.manifest["op_id"]))
    assert str(exc.value) == (
        "verify failed restoring row local_c3.json: the row on disk is not the row read "
        "with relink's keys put back. 'undo --id {0} --apply' again re-reads it and goes by "
        "what each key holds.".format(op.manifest["op_id"]))
    assert [who for who, _, _ in saves] == ["tick", "undo_relink"]
    monkeypatch.undo()
    om = op_of(env, op.manifest["op_id"]).manifest
    assert om["status"] == "completed"
    assert [r.get("restored") for r in om["rows"]] == [True, None]   # the row is judged afresh
    assert ct.read_lock(env) is None
    final, om = undo(env, op)
    assert final == "undone" and om["undo_left"] == []


def test_a_failed_save_of_undone_refuses_and_undo_again_finishes(
        monkeypatch, mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths, op = applied(env, write_transcript, write_row)
    target = op_of(env, op.manifest["op_id"])
    real = ct.save_manifest

    def full_at_undone(o):
        # the real set_status sets the status in memory, then saves it
        if o.manifest.get("status") == "undone":
            raise OSError(28, "No space left on device")
        return real(o)
    monkeypatch.setattr(ct, "save_manifest", full_at_undone)
    with pytest.raises(ct.Refusal) as exc:
        ct.undo_relink(env, target)
    assert str(exc.value) == (
        "could not save the operation record in {0} ([Errno 28] No space left on device). "
        "{1}".format(target.op_dir, undo_then(op.manifest["op_id"])))
    monkeypatch.setattr(ct, "save_manifest", real)
    om = op_of(env, op.manifest["op_id"]).manifest
    assert om["status"] == "completed" and [r["restored"] for r in om["rows"]] == [True, True]
    assert undo(env, op)[0] == "undone"


def test_undo_waits_while_another_operation_is_unfinished(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths, op = applied(env, write_transcript, write_row)
    target = op_of(env, op.manifest["op_id"])
    before = raws(paths)
    waits = D18_OPEN.replace("relink waits", "relink's undo waits", 1).format(OPEN_ID)
    put_op(env, OPEN_ID, open_op())
    with pytest.raises(ct.Refusal) as exc:
        ct.undo_relink(env, target)
    assert str(exc.value) == waits
    bad = "20990101T000001Z-bbbbbb"
    put_op(env, bad, "{not json")
    with pytest.raises(ct.Refusal) as exc:
        ct.undo_relink(env, target)
    assert str(exc.value).split("\n") == [
        waits, "relink's undo waits: the record(s) in {0} could not be read, so whether they "
               "are unfinished cannot be told. Nothing was changed.".format(bad)]
    assert raws(paths) == before and record_of(target)["status"] == "completed"
    assert ct.read_lock(env) is None


def test_undo_refuses_while_the_app_runs(mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths, op = applied(env, write_transcript, write_row)
    before = raws(paths)
    env.process_lister = lambda: APP_RUNNING
    msg = undo_refusal(env, op)
    assert "refusing to take links out of rows in the session store while it is" in msg
    assert ct.RELINK_GUARD_WHY in msg
    assert raws(paths) == before and record_of(op)["status"] == "completed"
    assert ct.read_lock(env) is None


def test_undo_refuses_another_type_or_a_relink_not_completed(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths, op = applied(env, write_transcript, write_row)
    sync = whole_op(env, "sync", paths["local_p2"])
    with pytest.raises(ct.Refusal) as exc:
        ct.undo_relink(env, sync)
    assert str(exc.value) == "not a relink op: " + sync.manifest["op_id"]
    target = op_of(env, op.manifest["op_id"])
    target.manifest["status"] = "writing"           # in memory only
    with pytest.raises(ct.Refusal) as exc:
        ct.undo_relink(env, target)
    assert str(exc.value) == "op {0} is 'writing', not 'completed'".format(op.manifest["op_id"])
    assert ct.read_lock(env) is None


@pytest.mark.parametrize("damage", ["old", "two rows, one file", "history missing",
                                    "history empty", "history not a list", "no time",
                                    "time not a number"])
def test_a_damaged_record_or_one_with_no_usable_history_refuses_the_undo(
        damage, mkenv, tmp_path, write_transcript, write_row):
    # a record with no usable history cannot say which operations came after it, so its
    # undo refuses as damaged, named by its folder, rather than judge claims it cannot order
    env = mkenv(tmp_path)
    paths, op = applied(env, write_transcript, write_row)
    target = op_of(env, op.manifest["op_id"])
    m = target.manifest
    shape = {"old": "row 0 has no usable 'old'",
             "two rows, one file": "row 1's 'dest_path' names the same file as row 0's"}
    if damage == "old":
        m["rows"][0]["old"] = {"spawnedFrom": 5}
    elif damage == "two rows, one file":
        m["rows"][1] = json.loads(json.dumps(m["rows"][0]))
    elif damage == "history missing":
        del m["history"]
    else:
        m["history"] = {"history empty": [], "history not a list": {"at": 1_900_000_000.0},
                        "no time": [{"status": "journaled"}],
                        "time not a number": [{"status": "journaled", "at": "later"}]}[damage]
    ct.save_manifest(target)
    record = raw_of(os.path.join(target.op_dir, "manifest.json"))
    before = raws(paths)
    with pytest.raises(ct.Refusal) as exc:
        ct.undo_relink(env, target)
    if damage in shape:
        assert str(exc.value) == (
            "the record for {0} is damaged ({1}); refusing rather than guess. Nothing was "
            "changed.".format(m["op_id"], shape[damage]))
    else:
        assert str(exc.value) == (
            "the record in {0} is damaged (its 'history' gives no time it was made, so which "
            "operations came after it cannot be told); refusing rather than guess. Nothing "
            "was changed.".format(os.path.basename(target.op_dir)))
    assert raws(paths) == before
    assert raw_of(os.path.join(target.op_dir, "manifest.json")) == record
    assert ct.read_lock(env) is None


def test_a_later_live_op_refuses_the_undo_and_names_it(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths, op = applied(env, write_transcript, write_row)
    c2, c3 = row_of(op, "local_c2"), row_of(op, "local_c3")
    pm = ct.plan_retitle(env, ct.RetitleFlags(only=LC, title="ACME-REVIEW side task, round two"))
    assert ct.run_retitle(env, pm) == "completed"
    (rt,) = [o for o in ct.list_ops(env) if o.manifest["op_type"] == "retitle"]
    before = raws(paths)
    assert undo_refusal(env, op) == (
        "cannot undo {0}: operation(s) {1} wrote row(s) local_c2.json ({2}), local_c3.json "
        "({3}) after it - undo those first. Nothing was changed.".format(
            op.manifest["op_id"], rt.manifest["op_id"], c2["label"], c3["label"]))
    assert op_of(env, op.manifest["op_id"]).manifest["status"] == "completed"
    assert raws(paths) == before
    assert all("spawnedFrom" in data_of(paths[s]) for s in ("local_c2", "local_c3"))


def test_a_later_relink_of_the_same_key_refuses_and_of_the_other_key_only_does_not(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths, op = applied(env, write_transcript, write_row)
    c2 = row_of(op, "local_c2")
    same = journal_op(env, "relink", [like(c2)])
    assert undo_refusal(env, op) == (
        "cannot undo {0}: operation(s) {1} wrote row(s) local_c2.json ({2}) after it - undo "
        "those first. Nothing was changed.".format(op.manifest["op_id"], same.manifest["op_id"],
                                                    c2["label"]))
    assert "spawnedFrom" in data_of(paths["local_c2"])
    shutil.rmtree(same.op_dir)
    # a chain written beside no kept link touches none of this relink's keys
    journal_op(env, "relink", [like(c2, new={"startChain": {"firstSessionId": "local_r2",
                                                            "depth": 1}}, kept={})])
    assert undo(env, op)[0] == "undone"
    assert "spawnedFrom" not in data_of(paths["local_c2"])


def test_a_later_realign_does_not_refuse(mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths, op = applied(env, write_transcript, write_row)
    realign_op(env, paths["local_c2"])               # it writes other keys (D13)
    assert undo(env, op)[0] == "undone"
    assert "spawnedFrom" not in data_of(paths["local_c2"])


def test_a_damaged_later_record_refuses_the_undo_by_its_folder(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths, op = applied(env, write_transcript, write_row)
    c2, c3 = row_of(op, "local_c2"), row_of(op, "local_c3")
    # readable, and finished, so D18 does not hold it; its rows cannot be read
    put_op(env, OPEN_ID, '{"op_id": "x", "status": "completed", "rows": [5]}')
    msg = undo_refusal(env, op)
    assert msg == (
        "cannot undo {0}: the record(s) in {1} cannot be read, so whether they wrote row(s) "
        "local_c2.json ({2}), local_c3.json ({3}) after it cannot be told. No command "
        "repairs a record: with the app closed, repair each one, or move its folder out of "
        "the journal. Nothing was changed.".format(op.manifest["op_id"], OPEN_ID, c2["label"],
                                                    c3["label"]))
    assert "undo those first" not in msg
    assert all("spawnedFrom" in data_of(paths[s]) for s in ("local_c2", "local_c3"))


def test_a_journal_that_cannot_be_listed_for_the_claims_names_no_op_id(
        monkeypatch, mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths, op = applied(env, write_transcript, write_row)
    target = op_of(env, op.manifest["op_id"])
    real, calls = ct._relink_load_ops, []

    def load(env):
        # D18 lists the journal first; by the claims' turn it cannot be listed
        calls.append(1)
        if len(calls) > 1:
            raise PermissionError(13, "Access is denied")
        return real(env)
    monkeypatch.setattr(ct, "_relink_load_ops", load)
    with pytest.raises(ct.Refusal) as exc:
        ct.undo_relink(env, target)
    assert str(exc.value) == (
        "cannot undo {0}: the journal folder could not be listed ([Errno 13] Access is "
        "denied), so whether a later operation wrote its rows cannot be told. Nothing was "
        "changed.".format(op.manifest["op_id"]))
    assert len(calls) == 2
    assert all("spawnedFrom" in data_of(paths[s]) for s in ("local_c2", "local_c3"))


def test_cmd_undo_previews_the_relink(capsys, mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths, op = applied(env, write_transcript, write_row)
    before = raws(paths)
    preview = ("would undo {0} (relink: takes out the links and chains it added to {1} row(s), "
               "key by key; a key changed since is left and named); pass --apply to execute\n")
    assert ct.cmd_undo(env, undo_ns()) == 0
    assert capsys.readouterr().out == preview.format(op.manifest["op_id"], 2)
    target = op_of(env, op.manifest["op_id"])          # n is the rows written and not restored
    target.manifest["rows"][0]["restored"] = True
    ct.save_manifest(target)
    assert ct.cmd_undo(env, undo_ns()) == 0
    assert capsys.readouterr().out == preview.format(op.manifest["op_id"], 1)
    assert raws(paths) == before
    assert op_of(env, op.manifest["op_id"]).manifest["status"] == "completed"


def test_the_window_offers_no_undo_while_a_relink_is_latest(
        mkenv, tmp_path, write_transcript, write_row):
    import claude_code_sessions_gui as gui
    env = _monotonic_now(mkenv(tmp_path))
    app = types.SimpleNamespace(env=env)
    paths = relink_store(env, write_transcript, write_row)
    sync = whole_op(env, "sync", paths["local_p2"])      # an earlier op the window reverses
    t = gui.SyncApp._find_undoable_op(app)
    assert (t["type"], t["op_id"]) == ("sync", sync.manifest["op_id"])
    relink_now(env)
    # CLI-only, so parked: reaching past it would undo something other than the last op
    assert gui.SyncApp._find_undoable_op(app) is None


def test_undo_takes_out_a_reordered_link_and_leaves_an_extended_one(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths, op = applied(env, write_transcript, write_row)
    c3 = row_of(op, "local_c3")
    set_keys(paths["local_c2"], spawnedFrom={"taskId": TASK, "sessionId": "local_p2"})
    assert b'"spawnedFrom": {"taskId": ' in raw_of(paths["local_c2"])   # the app's order
    extended = dict(c3["new"]["spawnedFrom"], title="Northwind")
    set_keys(paths["local_c3"], spawnedFrom=extended)
    final, om = undo(env, op)
    assert final == "undone"
    assert "spawnedFrom" not in data_of(paths["local_c2"])
    assert data_of(paths["local_c3"])["spawnedFrom"] == extended
    assert om["undo_left"] == [entry(c3, "spawnedFrom", APP_CHANGED)]
    assert [r["restored"] for r in om["rows"]] == [True, True]


def test_an_earlier_relink_and_a_later_retitle_leaving_the_same_bytes_are_each_undoable(
        mkenv, tmp_path, write_transcript, write_row):
    env = _monotonic_now(mkenv(tmp_path))
    paths = relink_store(env, write_transcript, write_row, local_c2={"titleSource": "user"})
    before = data_of(paths["local_c2"])
    op = relink_now(env)
    landed = raw_of(paths["local_c2"])
    pm = ct.plan_retitle(env, ct.RetitleFlags(only=LC, title="ACME-REVIEW side task",
                                              store=A2[:8]))
    assert names(pm["rows"]) == ["local_c2.json"]
    assert ct.unb64(pm["rows"][0]["post_b64"]) == landed            # the same bytes
    assert ct.run_retitle(env, pm) == "completed"
    assert raw_of(paths["local_c2"]) == landed
    (rt,) = [o for o in ct.list_ops(env) if o.manifest["op_type"] == "retitle"]
    assert ct.undo_retitle(env, rt) == "undone"
    assert undo(env, op)[0] == "undone"
    assert data_of(paths["local_c2"]) == before


def test_a_later_relink_blocks_a_repoint_undo_and_a_restored_one_does_not(
        mkenv, tmp_path, write_transcript, write_row):
    env = _monotonic_now(mkenv(tmp_path))
    paths = relink_store(env, write_transcript, write_row, local_c2={"cliSessionId": LQ})
    write_transcript(env, "C--p", LQ, t_entries())
    pre = raw_of(paths["local_c2"])
    rpm = ct.plan_repoint(env, ct.RepointFlags(only="ACME-REVIEW", to_session=LC,
                                               store=A2[:8]))
    assert same_file(rpm["rows"][0]["dest_path"], paths["local_c2"])
    assert ct.run_repoint(env, rpm) == "completed"
    repointed = raw_of(paths["local_c2"])
    (rp,) = [o for o in ct.list_ops(env) if o.manifest["op_type"] == "repoint"]
    op = relink_now(env)
    assert names(op.manifest["rows"]) == ["local_c2.json", "local_c3.json"]
    with pytest.raises(ct.Refusal) as exc:
        ct.undo_repoint(env, op_of(env, rp.manifest["op_id"]))
    assert "no longer holds" in str(exc.value)
    assert undo(env, op)[0] == "undone"
    assert raw_of(paths["local_c2"]) == repointed
    assert ct.undo_repoint(env, op_of(env, rp.manifest["op_id"])) == "undone"
    assert raw_of(paths["local_c2"]) == pre


def test_a_realign_undo_after_a_relink_of_the_same_row_succeeds(
        mkenv, tmp_path, write_transcript, write_row):
    from test_realign import World, behind_store, plan as realign_plan
    w = World(_monotonic_now(mkenv(tmp_path)), tmp_path, write_transcript, write_row)
    env = w.env
    s = behind_store(w)                  # S1: A1's row opens the live copy, A2's a copy behind
    kickoff, _ = w.place("Northwind-kickoff")
    w.copy(kickoff, LP, t_entries(cwd=kickoff))
    for acct, org in ((A1, O1), (A2, O2)):
        w.row(acct, org, "local_p", LP, kickoff, title="Northwind kickoff")
    w.row(A1, O1, "local_1", S1, s.live_cwd, activity=20, spawnedFrom=side_link("local_p"))
    rm = realign_plan(w)
    assert [r["dest_path"] for r in rm["rows"]] == [s.r2]
    ct.run_realign(env, rm)
    (ra,) = [o for o in ct.list_ops(env) if o.manifest["op_type"] == "realign"]
    assert ra.manifest["status"] == "completed"
    op = relink_now(env)
    (r,) = op.manifest["rows"]
    assert same_file(r["dest_path"], s.r2)
    link = r["new"]["spawnedFrom"]
    assert w.read(s.r2)["cwd"] == s.live_cwd and w.read(s.r2)["spawnedFrom"] == link
    assert ct.undo_realign(env, op_of(env, ra.manifest["op_id"])) == "undone"
    after = w.read(s.r2)
    assert after["cwd"] == s.old_cwd and after["folderPlace"]["cwd"] == s.old_cwd
    assert after["spawnedFrom"] == link


def converged(env):
    """A completed converge of the store as it is; returns its op and its one written row."""
    assert ct.run_converge(env, ct.plan_converge(env, ct.ConvergeFlags())) == "completed"
    (cv,) = [o for o in ct.list_ops(env) if o.manifest["op_type"] == "converge"]
    (made,) = [r for r in cv.manifest["rows"] if r.get("written")]
    return cv, made


def test_a_converge_undo_deletes_a_relinked_row_and_relink_undo_names_it_gone(
        mkenv, tmp_path, write_transcript, write_row):
    env = _monotonic_now(mkenv(tmp_path))
    paths = relink_store(env, write_transcript, write_row)
    os.unlink(paths["local_c3"])                    # A3 lacks C: converge creates its row
    cv, made = converged(env)
    assert (made["account"], made["session"]) == (A3, LC)
    d = data_of(made["dest_path"])                  # the app drops the link converge carried
    d.pop("spawnedFrom", None)
    with open(made["dest_path"], "w", encoding="utf-8") as fh:
        json.dump(d, fh)
    op = relink_now(env)
    assert names(op.manifest["rows"]) == ["local_c2.json", made["name"]]
    mine = row_of(op, made["name"][:-len(".json")])
    assert ct.cmd_undo(env, undo_ns(apply=True, op_id=cv.manifest["op_id"])) == 0
    assert not os.path.exists(made["dest_path"])
    final, om = undo(env, op)
    assert final == "undone"
    assert om["undo_left"] == [entry(mine, "the row", GONE)]
    assert "spawnedFrom" not in data_of(paths["local_c2"])


def test_undoing_a_converge_whose_created_parent_a_relink_linked_to_reports_it_dangling(
        capsys, mkenv, tmp_path, write_transcript, write_row):
    env = _monotonic_now(mkenv(tmp_path))
    paths = relink_store(env, write_transcript, write_row)
    os.unlink(paths["local_p3"])                    # A3 lacks P: converge creates its row
    cv, made = converged(env)
    assert (made["account"], made["session"]) == (A3, LP)
    made_id = made["name"][:-len(".json")]
    op = relink_now(env)
    assert row_of(op, "local_c3")["new"]["spawnedFrom"]["sessionId"] == made_id
    capsys.readouterr()
    assert ct.cmd_undo(env, undo_ns(apply=True, op_id=cv.manifest["op_id"])) == 0
    assert not os.path.exists(made["dest_path"])
    rep = op_of(env, cv.manifest["op_id"]).manifest["link_report"]
    assert rep["dangling"] == [{"row": "local_c3", "target": made_id, "kind": "parent"}]
    assert "link left dangling: local_c3 (its parent's row was removed)" in \
        capsys.readouterr().out.splitlines()


# ------------------------------------------------------------------ recovery
# Task 9: recover's two ways out of an interrupted relink. Forward finishes the
# rows still to write by what each key holds now: a row with a key at new
# landed and is marked, a row still at its pre-image is re-checked against its
# targets and the account's links and written, and anything else is skipped and
# named. Back takes out every key that landed, key by key, and names in
# abort_reason what it left; once it has begun, only back is offered. The
# recovery matrix at the end pins every line of the table in both directions.

THE_ROW_LEFT = "now opens another conversation; its link was left"
FORWARD_NOTE = ("relink: {0} row(s) written, {1} pending; forward finishes from the journal by "
                "what each key holds now, skipping and naming any row it cannot attribute; back "
                "takes out what landed, key by key")
ROLLING_NOTE = ("relink: a back recovery began and stopped part-way; only back is offered - "
                "'recover --resolve {0} --back --apply' goes on from what each row holds now")
NO_HISTORY = ("its 'history' gives no time it was made, so which operations came after it "
              "cannot be told")


def crash_on(point, nth=1):
    """A crash hook raising SimulatedCrash at the NTH hit of POINT."""
    hits = []

    def hook(p):
        if p == point:
            hits.append(p)
            if len(hits) == nth:
                raise SimulatedCrash()
    return hook


def crashed(env, point, nth=1):
    """The apply of a fresh plan, cut off at the NTH hit of POINT, the lock released. Returns
    the unfinished op as the journal holds it."""
    m = plan(env)
    ct._crash_hook = crash_on(point, nth)
    with pytest.raises(SimulatedCrash):
        ct.run_relink(env, m)
    ct._crash_hook = None
    ct.release_lock(env)
    op = op_of(env, m["op_id"])
    assert op.manifest["status"] == "writing"
    return op


def recover(env, op, direction, hook=None):
    """recover_op on OP as the journal holds it now, with HOOK as the crash hook: (its result,
    the record after)."""
    oid = op.manifest["op_id"]
    ct._crash_hook = hook
    try:
        final = ct.recover_op(env, op_of(env, oid), direction)
    finally:
        ct._crash_hook = None
    return final, op_of(env, oid).manifest


def recover_refusal(env, op, direction, exc_type=ct.Refusal):
    with pytest.raises(exc_type) as exc:
        ct.recover_op(env, op_of(env, op.manifest["op_id"]), direction)
    return str(exc.value)


def recover_ns(op_id, direction):
    return types.SimpleNamespace(op_id=op_id, forward=direction == "forward",
                                 back=direction == "back", apply=True, verbose=False)


def chain_store(env, write_transcript, write_row):
    """chain_plan's store: local_c2 gains its link and chain, local_c3, which keeps
    side_link('local_p3'), its chain only. Returns {stem: path}."""
    return relink_store(env, write_transcript, write_row, chain=CHAIN, roots=True,
                        local_c3={"spawnedFrom": side_link("local_p3")})


def write_raw(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def drop_key(path, key):
    """Rewrite one row without KEY, as the app may."""
    d = data_of(path)
    d.pop(key)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(d, fh)


def manifest_raw(op):
    return raw_of(os.path.join(op.op_dir, "manifest.json"))


def test_recovery_waits_while_another_operation_is_unfinished(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    op = crashed(env, "relink-mid-rows")
    oid = op.manifest["op_id"]
    before = raws(paths)
    put_op(env, OPEN_ID, open_op())
    waits = ("relink: recovery waits while another operation is unfinished ({0}); recover that "
             "first".format(OPEN_ID))
    c = ct.classify_op(env, op_of(env, oid))
    assert (c["resolutions"], c["note"]) == ([], waits)
    for direction in ("forward", "back"):
        assert waits in recover_refusal(env, op, direction)
    target = op_of(env, oid)            # held: list_ops cannot read the record put next
    with pytest.raises(ct.Refusal) as exc:
        ct._recover_relink(env, target, "back", [])
    assert str(exc.value) == D18_OPEN.replace("relink waits", "relink's recovery waits",
                                              1).format(OPEN_ID)
    bad = "20990101T000001Z-bbbbbb"
    put_op(env, bad, "{not json")
    unreadable = ("recovery waits: the record(s) in {0} could not be read, so whether they are "
                  "unfinished cannot be told. Nothing was changed.".format(bad))
    c = ct.classify_op(env, target)
    assert (c["resolutions"], c["note"]) == ([], waits + "; " + unreadable)
    shutil.rmtree(os.path.join(env.ops_dir, OPEN_ID))
    c = ct.classify_op(env, target)
    assert (c["resolutions"], c["note"]) == ([], "relink: " + unreadable)
    with pytest.raises(ct.Refusal) as exc:
        ct.recover_op(env, target, "back")
    assert unreadable in str(exc.value)
    with pytest.raises(ct.Refusal) as exc:
        ct._recover_relink(env, target, "back", [])
    assert str(exc.value) == "relink's " + unreadable
    assert raws(paths) == before and record_of(target)["status"] == "writing"
    assert ct.read_lock(env) is None


def test_a_relink_alone_recovers_without_waiting_on_itself(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    op = crashed(env, "relink-mid-rows")
    c = ct.classify_op(env, op)
    assert (c["resolutions"], c["note"]) == (["forward", "back"], FORWARD_NOTE.format(1, 1))
    final, om = recover(env, op, "forward")
    assert final == "completed" and om["status"] == "completed"
    assert [r["written"] for r in om["rows"]] == [True, True]
    assert not any("skipped" in r for r in om["rows"])
    assert data_of(paths["local_c3"])["spawnedFrom"] == tool_link("local_p3")
    assert ct.read_lock(env) is None and ct.nonterminal_ops(env) == []


def test_classify_puts_d18_before_the_unresolved_row_gate(
        mkenv, tmp_path, write_transcript, write_row):
    from test_journal_reroot import UNREAD, packaged, strand
    env = mkenv(tmp_path)
    packaged(env, tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    op = crashed(env, "relink-mid-rows")
    oid = op.manifest["op_id"]
    strand(env, tmp_path)
    files = {p: raw_of(p) for p in paths.values()}
    c = ct.classify_op(env, op_of(env, oid))
    assert c["resolutions"] == ["back"] and UNREAD in c["note"]   # the gate, nothing else open
    put_op(env, OPEN_ID, open_op())
    c = ct.classify_op(env, op_of(env, oid))
    assert c["resolutions"] == []                                 # never ["back"]
    assert c["note"] == ("relink: recovery waits while another operation is unfinished ({0}); "
                         "recover that first".format(OPEN_ID))
    with pytest.raises(ct.Refusal):
        ct.recover_op(env, op_of(env, oid), "back")
    shutil.rmtree(os.path.join(env.ops_dir, OPEN_ID))
    # back alone, with the rows it cannot read left alone and recorded for doctor
    assert recover_refusal(env, op, "forward").startswith("'forward' is not a safe resolution")
    final, om = recover(env, op, "back")
    assert final == "rolled_back" and UNREAD in om["rollback_residue"]
    assert {p: raw_of(p) for p in files} == files


def test_forward_marks_a_row_whose_keys_all_hold_new_as_landed(
        monkeypatch, mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    op = crashed(env, "relink-before-mark")
    assert [r["written"] for r in op.manifest["rows"]] == [False, False]
    landed = raw_of(paths["local_c2"])
    real, written = ct.atomic_write, []

    def record(path, data):
        written.append(os.path.basename(path))
        return real(path, data)
    monkeypatch.setattr(ct, "atomic_write", record)
    final, om = recover(env, op, "forward")
    assert final == "completed"
    assert [r["written"] for r in om["rows"]] == [True, True]
    assert "local_c2.json" not in written and raw_of(paths["local_c2"]) == landed
    assert data_of(paths["local_c3"])["spawnedFrom"] == tool_link("local_p3")


def test_forward_marks_landed_a_row_with_one_key_new_and_one_changed(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = chain_store(env, write_transcript, write_row)
    op = crashed(env, "relink-before-mark")
    c2, c3 = row_of(op, "local_c2"), row_of(op, "local_c3")
    set_keys(paths["local_c2"], startChain=dict(c2["new"]["startChain"], depth=9))
    landed = raw_of(paths["local_c2"])
    final, om = recover(env, op, "forward")
    assert final == "completed"
    assert [r["written"] for r in om["rows"]] == [True, True]
    assert raw_of(paths["local_c2"]) == landed
    assert data_of(paths["local_c3"])["startChain"] == c3["new"]["startChain"]


def test_forward_marks_landed_a_row_with_one_key_new_and_one_back_at_old(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = chain_store(env, write_transcript, write_row)
    pre = data_of(paths["local_c2"])
    op = crashed(env, "relink-before-mark")
    drop_key(paths["local_c2"], "startChain")             # the app took the chain out
    final, om = recover(env, op, "forward")
    assert final == "completed" and om["rows"][0]["written"] is True
    # undo puts back the key still at new and counts the one at old as back
    final, um = undo(env, op)
    assert final == "undone" and um["undo_left"] == []
    assert data_of(paths["local_c2"]) == pre


@pytest.mark.parametrize("edit", ["old", "post_sha256"])
def test_forward_refuses_a_record_that_does_not_match_its_pending_row(
        edit, mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    op = crashed(env, "relink-mid-rows")
    oid = op.manifest["op_id"]
    target = op_of(env, oid)
    r3 = target.manifest["rows"][1]
    if edit == "old":
        r3["old"]["spawnedFrom"] = {"present": True, "value": "x"}
    else:
        r3["post_sha256"] = "0" * 64
    ct.save_manifest(target)
    assert ct._relink_shape_error(target.manifest) is None
    before, record = raw_of(paths["local_c3"]), manifest_raw(target)
    assert recover_refusal(env, op, "forward", ct.LayoutError) == (
        "the record for {0} does not match row local_c3.json; refusing rather than write from "
        "it. Nothing was changed.".format(oid))
    assert raw_of(paths["local_c3"]) == before and manifest_raw(target) == record
    assert op_of(env, oid).manifest["status"] == "writing" and ct.read_lock(env) is None


def test_forward_skips_a_pending_chain_only_row_whose_kept_parent_row_is_gone(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = chain_store(env, write_transcript, write_row)
    op = crashed(env, "relink-mid-rows")
    os.unlink(paths["local_p3"])
    before = raw_of(paths["local_c3"])
    final, om = recover(env, op, "forward")
    assert final == "completed"
    r3 = om["rows"][1]
    assert (r3["skipped"], r3["skip_detail"]) == (
        "parent_changed",
        "its parent {0}'s row is gone since this operation was journaled".format(LP[:8]))
    assert r3["written"] is False and raw_of(paths["local_c3"]) == before
    assert "startChain" not in data_of(paths["local_c3"])


def test_forward_does_not_skip_a_chain_only_row_for_a_cycle_through_its_kept_link(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = chain_store(env, write_transcript, write_row)
    op = crashed(env, "relink-mid-rows")
    set_keys(paths["local_p3"], spawnedFrom=side_link("local_c3"))     # C <-> P on disk
    final, om = recover(env, op, "forward")
    assert final == "completed"
    r3 = om["rows"][1]
    assert r3["written"] is True and "skipped" not in r3
    assert data_of(paths["local_c3"])["startChain"] == r3["new"]["startChain"]


def test_forward_does_not_recheck_a_chain_the_row_keeps(
        mkenv, tmp_path, write_transcript, write_row):
    # local_c3 keeps its own agreeing chain and gains its link only: its record names the
    # kept chain's root, which is not this relink's to re-check
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row, chain=CHAIN, roots=True,
                         local_c3={"startChain": {"firstSessionId": "local_r3", "depth": 2}})
    op = crashed(env, "relink-mid-rows")
    c3 = row_of(op, "local_c3")
    assert list(c3["new"]) == ["spawnedFrom"] and c3["lineage"]["root_row"] == "local_r3"
    os.unlink(paths["local_r3"])
    final, om = recover(env, op, "forward")
    assert final == "completed"
    r3 = om["rows"][1]
    assert r3["written"] is True and "skipped" not in r3
    assert data_of(paths["local_c3"])["spawnedFrom"] == tool_link("local_p3")


@pytest.mark.parametrize("direction", ["forward", "back"])
def test_recovery_takes_the_guard_only_when_a_row_will_be_written(
        direction, mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    op = crashed(env, "relink-mid-rows")
    oid = op.manifest["op_id"]
    before, record = raws(paths), manifest_raw(op)
    env.process_lister = lambda: APP_RUNNING
    msg = recover_refusal(env, op, direction)
    said = "add links to rows in" if direction == "forward" else "take links out of rows in"
    assert "refusing to {0} the session store while it is".format(said) in msg
    assert ct.RELINK_GUARD_WHY in msg
    # refused before anything changed: rolling_back is not set, so forward is still offered
    assert raws(paths) == before and manifest_raw(op) == record
    assert ct.read_lock(env) is None
    # a recovery that writes no row closes with the app running: the marks are the journal's
    if direction == "forward":
        env2 = mkenv(tmp_path / "landed")
        relink_store(env2, write_transcript, write_row)
        done = crashed(env2, "relink-after-rows")
    else:
        env2 = mkenv(tmp_path / "gone")
        paths2 = relink_store(env2, write_transcript, write_row)
        done = crashed(env2, "relink-mid-rows")
        os.unlink(paths2["local_c2"])
    env2.process_lister = lambda: APP_RUNNING
    final, _om = recover(env2, done, direction)
    assert final == ("completed" if direction == "forward" else "rolled_back")


def test_forward_writes_a_pending_row_and_skips_one_whose_parent_row_is_gone(
        monkeypatch, mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    m = plan(env)
    real = ct.atomic_write

    def cut(path, data):
        # the apply cut off at its first row's write, before a byte of it: both rows pending
        if same_file(path, paths["local_c2"]):
            raise SimulatedCrash()
        return real(path, data)
    monkeypatch.setattr(ct, "atomic_write", cut)
    with pytest.raises(SimulatedCrash):
        ct.run_relink(env, m)
    monkeypatch.setattr(ct, "atomic_write", real)
    op = op_of(env, m["op_id"])
    assert op.manifest["status"] == "writing"
    assert [r["written"] for r in op.manifest["rows"]] == [False, False]
    os.unlink(paths["local_p3"])
    before = raw_of(paths["local_c3"])
    final, om = recover(env, op, "forward")
    assert final == "completed"
    r2, r3 = om["rows"]
    assert r2["written"] is True and "skipped" not in r2
    assert data_of(paths["local_c2"])["spawnedFrom"] == tool_link("local_p2")
    assert r3["skipped"] == "parent_changed" and LP[:8] in r3["skip_detail"]
    assert r3["written"] is False and raw_of(paths["local_c3"]) == before


def test_forward_names_a_landed_row_whose_parent_row_is_gone_since(
        capsys, mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    op = crashed(env, "relink-mid-rows")
    oid = op.manifest["op_id"]
    os.unlink(paths["local_p2"])
    capsys.readouterr()
    assert ct.cmd_recover(env, recover_ns(oid, "forward")) == 0
    om = op_of(env, oid).manifest
    assert om["status"] == "completed"
    assert om["link_report"]["dangling"] == [
        {"row": "local_c2", "target": "local_p2", "kind": "parent"}]
    assert capsys.readouterr().out.splitlines() == [
        "result: completed", "link left dangling: local_c2 (its parent's row was removed)"]


def test_forward_skips_a_pending_edge_that_now_closes_a_cycle(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    op = crashed(env, "relink-mid-rows")
    set_keys(paths["local_p3"], spawnedFrom=side_link("local_c3"))
    before = raw_of(paths["local_c3"])
    final, om = recover(env, op, "forward")
    assert final == "completed"
    r3 = om["rows"][1]
    assert (r3["skipped"], r3["skip_detail"]) == ("lineage_cycle", "it would close a cycle now")
    assert r3["written"] is False and raw_of(paths["local_c3"]) == before


def test_forward_skips_a_row_whose_accounts_links_cannot_all_be_read(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    op = crashed(env, "relink-mid-rows")
    write_raw(os.path.join(os.path.dirname(paths["local_c3"]), "local_zz.json"), "{not json")
    before = raws(paths)
    final, om = recover(env, op, "forward")
    assert final == "completed"
    r3 = om["rows"][1]
    assert (r3["skipped"], r3["skip_detail"]) == (
        "edges_unreadable",
        "the account's links could not all be read, so a cycle cannot be ruled out")
    assert r3["written"] is False and raws(paths) == before


def test_forward_skips_a_row_changed_after_the_plan(mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    op = crashed(env, "relink-mid-rows")
    set_keys(paths["local_c3"], lastFocusedAt=1_800_000_000_000)
    before = raw_of(paths["local_c3"])
    final, om = recover(env, op, "forward")
    assert final == "completed"
    r3 = om["rows"][1]
    assert (r3["skipped"], r3["skip_detail"]) == ("changed_since_plan", "changed since the plan")
    assert r3["written"] is False and raw_of(paths["local_c3"]) == before


@pytest.mark.parametrize("landed", [True, False])
def test_forward_skips_a_repointed_row_landed_or_not(
        landed, mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    op = crashed(env, "relink-before-mark" if landed else "relink-mid-rows")
    stem, i = ("local_c2", 0) if landed else ("local_c3", 1)
    set_keys(paths[stem], cliSessionId=LQ)
    before = raw_of(paths[stem])
    final, om = recover(env, op, "forward")
    assert final == "completed"
    r = om["rows"][i]
    # a landed link stays on the row, which is left unwritten, so neither undo nor a claim
    # reaches it: the detail says so
    assert (r["skipped"], r["skip_detail"]) == (
        "repointed", THE_ROW_LEFT if landed else "now opens another conversation")
    assert r["written"] is False and raw_of(paths[stem]) == before
    assert ("spawnedFrom" in data_of(paths[stem])) is landed     # a landed link stays on it
    other = om["rows"][1 - i]
    assert other["written"] is True and "skipped" not in other


def test_cmd_recover_prints_each_skipped_row(capsys, mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    op = crashed(env, "relink-mid-rows")
    oid = op.manifest["op_id"]
    os.unlink(paths["local_p3"])
    capsys.readouterr()
    assert ct.cmd_recover(env, recover_ns(oid, "forward")) == 0
    r3 = op_of(env, oid).manifest["rows"][1]
    detail = "its parent {0}'s row is gone since this operation was journaled".format(LP[:8])
    line = "skipped : local_c3 -> {0}: parent_changed - {1}".format(r3["label"], detail)
    assert r3["label"] == "{0} ({0}/{1})".format(A3[:8], O3[:8])
    assert ct._relink_skip_line(r3) == line
    assert capsys.readouterr().out.splitlines() == ["result: completed", line]


def test_back_takes_out_what_landed_and_terminates(mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    before = data_of(paths["local_c2"])
    op = crashed(env, "relink-mid-rows")
    pending = raw_of(paths["local_c3"])
    final, om = recover(env, op, "back")
    assert final == "rolled_back" and om["status"] == "rolled_back"
    after = data_of(paths["local_c2"])
    assert after == before and list(after) == list(before)
    assert raw_of(paths["local_c3"]) == pending
    r2, r3 = om["rows"]
    assert (r2["written"], r2["restored"], r2["back_keys"]) == (True, True, ["spawnedFrom"])
    assert r3["written"] is False and "restored" not in r3 and "back_keys" not in r3
    assert om["rolling_back"] is True and om["back_left"] == [] and om["back_restored"] == 1
    assert om["abort_reason"] == "back restored 1 row(s)"
    assert "rollback_residue" not in om and ct._relink_shape_error(om) is None
    assert ct.read_lock(env) is None and ct.nonterminal_ops(env) == []


def test_back_takes_out_a_landed_key_beside_one_the_app_reverted(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = chain_store(env, write_transcript, write_row)
    pre = data_of(paths["local_c2"])
    op = crashed(env, "relink-before-mark")
    drop_key(paths["local_c2"], "startChain")
    final, om = recover(env, op, "back")
    assert final == "rolled_back"
    assert data_of(paths["local_c2"]) == pre
    r2 = om["rows"][0]
    assert (r2["written"], r2["restored"], r2["back_keys"]) == (True, True, ["spawnedFrom"])
    assert om["back_left"] == [] and om["abort_reason"] == "back restored 1 row(s)"


def test_an_interrupted_back_offers_only_back_and_a_second_back_finishes(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    op = crashed(env, "relink-after-rows")
    oid = op.manifest["op_id"]
    assert [r["written"] for r in op.manifest["rows"]] == [True, True]
    with pytest.raises(SimulatedCrash):
        recover(env, op, "back", hook=crash_on("relink-back-mid-rows"))
    ct.release_lock(env)
    om = op_of(env, oid).manifest                       # the record on disk
    assert om["status"] == "writing" and om["rolling_back"] is True
    r2, r3 = om["rows"]
    # back reaches local_c3 first, in reverse journal order, and stops before local_c2's write
    assert r3["restored"] is True and "spawnedFrom" not in data_of(paths["local_c3"])
    assert "restored" not in r2 and "spawnedFrom" in data_of(paths["local_c2"])
    c = ct.classify_op(env, op_of(env, oid))
    assert (c["resolutions"], c["note"]) == (["back"], ROLLING_NOTE.format(oid))
    msg = recover_refusal(env, op, "forward")
    assert "began and stopped part-way" in msg and ROLLING_NOTE.format(oid) in msg
    with pytest.raises(ct.Refusal) as exc:      # the arm's own check, should it be reached
        ct._recover_relink(env, op_of(env, oid), "forward", [])
    assert str(exc.value) == ROLLING_NOTE.format(oid) + ". Nothing was changed."
    final, om = recover(env, op, "back")
    assert final == "rolled_back"
    assert not any("spawnedFrom" in data_of(paths[s]) for s in ("local_c2", "local_c3"))
    assert [r["restored"] for r in om["rows"]] == [True, True]
    assert om["back_restored"] == 2 and om["abort_reason"] == "back restored 2 row(s)"
    # rolled back, it claims nothing (spec D13): nothing it wrote is left outside undo's reach
    asked = [like(row_of(op, s)) for s in ("local_c2", "local_c3")]
    assert ct._relink_claims(env, EARLIER, asked) == []


def test_back_names_a_landed_unmarked_row_repointed_since(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    op = crashed(env, "relink-before-mark")
    oid = op.manifest["op_id"]
    c2 = row_of(op, "local_c2")
    set_keys(paths["local_c2"], cliSessionId=LQ)        # its link kept
    before = raw_of(paths["local_c2"])
    asked = [like(c2)]
    # unfinished, it claims every row it names (spec D13)
    assert claimed(ct._relink_claims(env, EARLIER, asked)) == [
        (oid, "local_c2.json", {"spawnedFrom"})]
    final, om = recover(env, op, "back")
    assert final == "rolled_back"
    assert raw_of(paths["local_c2"]) == before
    assert data_of(paths["local_c2"])["spawnedFrom"] == c2["new"]["spawnedFrom"]
    assert om["back_left"] == [entry(c2, "the row", THE_ROW_LEFT)]
    assert om["abort_reason"] == (
        "back restored 0 row(s); left 1: local_c2 the row ({0})".format(THE_ROW_LEFT))
    r2 = om["rows"][0]
    assert r2["written"] is True and "restored" not in r2
    assert ct._relink_claims(env, EARLIER, asked) == []      # rolled back, it claims nothing


@pytest.mark.parametrize("saves_fail", [False, True], ids=["saved", "saves-fail"])
def test_a_failed_verify_in_back_leaves_the_row_for_a_second_back(
        saves_fail, monkeypatch, mkenv, tmp_path, write_transcript, write_row):
    # back reaches local_c3 first, in reverse journal order, so local_c2's write is the one
    # whose verify fails, with local_c3 already restored
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    op = crashed(env, "relink-after-rows")
    oid = op.manifest["op_id"]
    real_write, real_save, broken = ct.atomic_write, ct.save_manifest, []

    def wrong_for_c2(path, data):
        if same_file(path, paths["local_c2"]):
            broken.append(path)
            return real_write(path, json.dumps({"cliSessionId": LC}).encode("utf-8"))
        return real_write(path, data)

    def save(o):
        # with SAVES_FAIL, every save after the wrong write fails: the verify's error stands
        if saves_fail and broken:
            raise OSError(28, "No space left on device")
        return real_save(o)
    monkeypatch.setattr(ct, "atomic_write", wrong_for_c2)
    monkeypatch.setattr(ct, "save_manifest", save)
    msg = recover_refusal(env, op, "back", ct.LayoutError)    # never the OSError
    assert msg == (
        "verify failed restoring row local_c2.json: the row on disk is not the row read with "
        "relink's keys put back - run 'recover --resolve {0} --back --apply' again; it re-reads "
        "the row and goes by what each key holds.".format(oid))
    monkeypatch.undo()
    om = op_of(env, oid).manifest
    assert om["status"] == "writing" and om["rolling_back"] is True
    r2, r3 = om["rows"]
    assert "restored" not in r2 and r2["back_keys"] == ["spawnedFrom"]
    assert r3["restored"] is True
    assert ct.read_lock(env) is None
    final, om = recover(env, op, "back")
    assert final == "rolled_back" and om["back_restored"] == 2
    assert [r["restored"] for r in om["rows"]] == [True, True]


def test_back_marks_a_landed_row_written_before_putting_its_keys_back(
        monkeypatch, mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = chain_store(env, write_transcript, write_row)
    pre = data_of(paths["local_c2"])
    op = crashed(env, "relink-before-mark")
    oid = op.manifest["op_id"]
    c2 = row_of(op, "local_c2")
    changed = dict(c2["new"]["startChain"], depth=9)
    set_keys(paths["local_c2"], startChain=changed)
    real_write, real_save, killed = ct.atomic_write, ct.save_manifest, []

    def write_then_die(path, data):
        # a hard kill the moment local_c2's write lands: nothing more reaches the disk, the
        # save on the way out included, so the record holds only what was saved before it
        real_write(path, data)
        if same_file(path, paths["local_c2"]):
            killed.append(path)
            raise SimulatedCrash()
    monkeypatch.setattr(ct, "atomic_write", write_then_die)
    monkeypatch.setattr(ct, "save_manifest", lambda o: None if killed else real_save(o))
    with pytest.raises(SimulatedCrash):
        recover(env, op, "back")
    monkeypatch.undo()
    om = op_of(env, oid).manifest
    r2 = om["rows"][0]
    assert r2["written"] is True and "restored" not in r2 and r2["back_keys"] == ["spawnedFrom"]
    assert "spawnedFrom" not in data_of(paths["local_c2"])
    assert data_of(paths["local_c2"])["startChain"] == changed
    final, om = recover(env, op, "back")
    assert final == "rolled_back"
    assert om["back_left"] == [entry(c2, "startChain", APP_CHANGED)]
    assert om["abort_reason"] == (
        "back restored 1 row(s); left 1: local_c2 startChain (the app changed it since)")
    assert om["rows"][0]["restored"] is True and om["back_restored"] == 1
    assert data_of(paths["local_c2"]) == dict(pre, startChain=changed)


def test_a_resumed_back_reports_the_rows_its_first_pass_handled(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    op = crashed(env, "relink-after-rows")
    oid = op.manifest["op_id"]
    c3 = row_of(op, "local_c3")
    set_keys(paths["local_c3"], spawnedFrom=dict(c3["new"]["spawnedFrom"], taskId="task_app"))
    left = [entry(c3, "spawnedFrom", APP_CHANGED)]
    with pytest.raises(SimulatedCrash):                  # at local_c2, after its write
        recover(env, op, "back", hook=crash_on("relink-back-before-mark", 2))
    om = op_of(env, oid).manifest
    r2, r3 = om["rows"]
    assert om["back_left"] == left
    assert r3["restored"] is True and "back_keys" not in r3
    assert r2["back_keys"] == ["spawnedFrom"] and "restored" not in r2
    assert "spawnedFrom" not in data_of(paths["local_c2"])
    final, om = recover(env, op, "back")
    assert final == "rolled_back"
    # built from the record, so the first pass's rows count, though the second wrote nothing
    assert om["abort_reason"] == (
        "back restored 1 row(s); left 1: local_c3 spawnedFrom (the app changed it since)")
    assert om["back_restored"] == 1 and om["back_left"] == left
    assert [r["restored"] for r in om["rows"]] == [True, True]


@pytest.mark.parametrize("change", ["repointed", "gone", "unreadable"])
def test_back_never_reports_an_untouched_pending_row(
        change, mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    before = data_of(paths["local_c2"])
    op = crashed(env, "relink-mid-rows")
    if change == "repointed":
        set_keys(paths["local_c3"], cliSessionId=LQ)
    elif change == "gone":
        os.unlink(paths["local_c3"])
    else:
        write_raw(paths["local_c3"], "{not json")
    final, om = recover(env, op, "back")
    assert final == "rolled_back"
    assert om["abort_reason"] == "back restored 1 row(s)" and om["back_left"] == []
    r2, r3 = om["rows"]
    assert r2["restored"] is True and data_of(paths["local_c2"]) == before
    assert r3["written"] is False and "restored" not in r3


def test_back_leaves_a_claimed_key_and_names_it_in_abort_reason(
        capsys, mkenv, tmp_path, write_transcript, write_row):
    env = _monotonic_now(mkenv(tmp_path))
    paths = relink_store(env, write_transcript, write_row)
    op = crashed(env, "relink-after-rows")
    oid = op.manifest["op_id"]
    c2 = row_of(op, "local_c2")
    sync = whole_op(env, "sync", paths["local_c2"])     # later, completed, and wrote the row
    sid = sync.manifest["op_id"]
    capsys.readouterr()
    assert ct.cmd_recover(env, recover_ns(oid, "back")) == 0
    om = op_of(env, oid).manifest
    reason = ("back restored 1 row(s); left 1: local_c2 spawnedFrom (operation {0} wrote it "
              "since)".format(sid))
    assert om["abort_reason"] == reason
    assert capsys.readouterr().out.splitlines() == ["result: rolled_back", "reason: " + reason]
    r2, r3 = om["rows"]
    assert "restored" not in r2
    assert data_of(paths["local_c2"])["spawnedFrom"] == c2["new"]["spawnedFrom"]
    assert r3["restored"] is True and "spawnedFrom" not in data_of(paths["local_c3"])
    # rolled back, the relink claims nothing (spec D13); the sync still does
    assert [c["op_id"] for c in ct._relink_claims(env, EARLIER, [like(c2)])] == [sid]


def test_back_leaves_no_rollback_residue_for_an_existing_row(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    op = crashed(env, "relink-after-rows")
    os.unlink(paths["local_c2"])
    write_raw(paths["local_c3"], "{not json")
    final, om = recover(env, op, "back")
    assert final == "rolled_back"
    assert "rollback_residue" not in om and "rollback_residue_paths" not in om
    assert om["abort_reason"] == ("back restored 0 row(s); left 2: local_c3 the row (could not "
                                  "be read), local_c2 the row (gone from the store)")
    r2, r3 = om["rows"]
    assert r2["restored"] is True and "restored" not in r3
    assert ct.gather_doctor(env)["rollback_residue"] == []


@pytest.mark.parametrize("direction", ["forward", "back"])
def test_a_failed_final_save_refuses_with_the_runnable_hint_and_recovery_again_closes(
        direction, monkeypatch, mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    op = crashed(env, "relink-mid-rows")
    oid = op.manifest["op_id"]
    final_status = "completed" if direction == "forward" else "rolled_back"
    real = ct.save_manifest

    def full_at_final(o):
        # the real set_status sets the status in memory, then saves it
        if o.manifest.get("status") == final_status:
            raise OSError(28, "No space left on device")
        return real(o)
    monkeypatch.setattr(ct, "save_manifest", full_at_final)
    target = op_of(env, oid)
    with pytest.raises(ct.Refusal) as exc:
        ct.recover_op(env, target, direction)
    monkeypatch.setattr(ct, "save_manifest", real)
    then = ("The op is left at 'writing' - {0} takes out what landed.".format(recover_hint(oid))
            if direction == "forward" else
            "The op is left at 'writing' - run {0} again; it goes on from what each row holds "
            "now.".format(recover_hint(oid)))
    assert str(exc.value) == ("could not save the operation record in {0} ([Errno 28] No space "
                              "left on device). {1}".format(target.op_dir, then))
    om = op_of(env, oid).manifest
    assert om["status"] == "writing" and ct.read_lock(env) is None
    final, om = recover(env, op, direction)
    assert final == final_status
    if direction == "forward":
        assert [r["written"] for r in om["rows"]] == [True, True]
    else:
        assert om["back_restored"] == 1 and om["rows"][0]["restored"] is True
        assert "spawnedFrom" not in data_of(paths["local_c2"])


def test_a_relink_whose_own_history_gives_no_time_is_refused_as_damaged_by_its_folder(
        mkenv, tmp_path, write_transcript, write_row):
    # back orders every other operation against this one (D13), so a record that cannot say
    # when it was made is refused as undo refuses it, named by its folder
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    op = crashed(env, "relink-mid-rows")
    oid = op.manifest["op_id"]
    target = op_of(env, oid)
    for h in target.manifest["history"]:
        h.pop("at")
    ct.save_manifest(target)
    folder = os.path.basename(target.op_dir)
    before, record = raws(paths), manifest_raw(target)
    c = ct.classify_op(env, op_of(env, oid))
    assert (c["resolutions"], c["note"]) == ([], (
        "relink: the record in {0} is damaged ({1}), so neither direction can be run from "
        "it".format(folder, NO_HISTORY)))
    for direction in ("forward", "back"):
        assert "is damaged" in recover_refusal(env, op, direction)
    with pytest.raises(ct.Refusal) as exc:
        ct._recover_relink(env, op_of(env, oid), "back", [])
    assert str(exc.value) == ("the record in {0} is damaged ({1}), so neither direction can be "
                              "run from it. Nothing was changed.".format(folder, NO_HISTORY))
    assert raws(paths) == before and manifest_raw(target) == record


_RECOVERY_DAMAGE = {                                    # three of Task 5's damages
    "old": lambda m: m["rows"][0].__setitem__("old", []),
    "lineage": lambda m: m["rows"][0]["lineage"].__setitem__("parent_row", "local_p9"),
    "rolling_back": lambda m: m.__setitem__("rolling_back", "yes"),
}


@pytest.mark.parametrize("damage", sorted(_RECOVERY_DAMAGE))
def test_a_damaged_record_refuses_through_classify_undo_and_recover(
        damage, mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    op = crashed(env, "relink-mid-rows")
    oid = op.manifest["op_id"]
    target = op_of(env, oid)
    _RECOVERY_DAMAGE[damage](target.manifest)
    ct.save_manifest(target)
    bad = ct._relink_shape_error(target.manifest)
    assert isinstance(bad, str)
    before, record = raws(paths), manifest_raw(target)
    c = ct.classify_op(env, op_of(env, oid))
    assert (c["resolutions"], c["note"]) == ([], (
        "relink: this operation's record is damaged ({0}), so neither direction can be run "
        "from it".format(bad)))
    for direction in ("forward", "back"):
        assert "is damaged" in recover_refusal(env, op, direction)
    with pytest.raises(ct.Refusal) as exc:
        ct._recover_relink(env, op_of(env, oid), "back", [])
    assert str(exc.value) == ("the record for {0} is damaged ({1}), so neither direction can be "
                              "run from it. Nothing was changed.".format(oid, bad))
    assert raws(paths) == before and manifest_raw(target) == record
    # a completed relink with the same damage: its undo refuses too
    env2 = mkenv(tmp_path / "completed")
    relink_store(env2, write_transcript, write_row)
    done = relink_now(env2)
    _RECOVERY_DAMAGE[damage](done.manifest)
    ct.save_manifest(done)
    with pytest.raises(ct.Refusal) as exc:
        ct.undo_relink(env2, op_of(env2, done.manifest["op_id"]))
    assert "is damaged" in str(exc.value)


def test_every_route_passes_discovered_roots(
        monkeypatch, mkenv, tmp_path, write_transcript, write_row):
    seen = []
    real_read, real_put = ct._relink_read, ct._relink_put

    def read(r, roots):
        seen.append(list(roots))
        return real_read(r, roots)

    def put(r, value, roots, then):
        seen.append(list(roots))
        return real_put(r, value, roots, then)
    monkeypatch.setattr(ct, "_relink_read", read)
    monkeypatch.setattr(ct, "_relink_put", put)
    for route in ("apply", "undo", "forward", "back"):
        env = mkenv(tmp_path / route)
        relink_store(env, write_transcript, write_row)
        if route == "apply":
            seen.clear()
            assert ct.run_relink(env, plan(env)) == "completed"
        elif route == "undo":
            op = relink_now(env)
            seen.clear()
            assert undo(env, op)[0] == "undone"
        else:
            op = crashed(env, "relink-mid-rows")
            seen.clear()
            assert recover(env, op, route)[0] == (
                "completed" if route == "forward" else "rolled_back")
        want = ct.discover_stores(env).roots
        journaled = {r[k] for o in ct.list_ops(env) for r in o.manifest["rows"]
                     for k in ("store_path", "dest_path")}
        assert seen, route
        assert all(s == want for s in seen), route
        assert not journaled & {p for s in seen for p in s}, route
    # a discovery error refuses undo and recovery before any row is read
    env_u, env_r = mkenv(tmp_path / "error-undo"), mkenv(tmp_path / "error-recover")
    relink_store(env_u, write_transcript, write_row)
    relink_store(env_r, write_transcript, write_row)
    done = op_of(env_u, relink_now(env_u).manifest["op_id"])
    stuck = op_of(env_r, crashed(env_r, "relink-mid-rows").manifest["op_id"])
    monkeypatch.setattr(ct, "discover_stores", lambda env: ct.StoreDiscovery("error", [], "boom"))
    seen.clear()
    with pytest.raises(ct.LayoutError) as exc:
        ct.undo_relink(env_u, done)
    assert "store discovery failed: boom" in str(exc.value)
    for direction in ("forward", "back"):
        with pytest.raises(ct.LayoutError) as exc:
            ct.recover_op(env_r, stuck, direction)
        assert "store discovery failed: boom" in str(exc.value)
    assert seen == []


@pytest.mark.parametrize("direction", ["forward", "back"])
def test_recovery_refuses_a_row_the_app_wrote_after_it_was_read(
        direction, monkeypatch, mkenv, tmp_path, write_transcript, write_row):
    # recovery reads before the guard, which runs only when a row will be written, so the
    # app may have been running at the read and written the row as it closed
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    op = crashed(env, "relink-mid-rows" if direction == "forward" else "relink-after-rows")
    oid = op.manifest["op_id"]
    c2, record = raw_of(paths["local_c2"]), manifest_raw(op)
    real, flushed = ct._guard_mutation, []

    def flush_then_guard(*a, **k):
        if not flushed:                        # the app's last write, as it exits
            set_keys(paths["local_c3"], lastFocusedAt=1_800_000_000_000)
            flushed.append(raw_of(paths["local_c3"]))
        return real(*a, **k)
    monkeypatch.setattr(ct, "_guard_mutation", flush_then_guard)
    assert recover_refusal(env, op, direction) == (
        "row local_c3.json ({0}) changed while recovery read it, most likely the app writing "
        "it as it closed. Nothing was changed. The op is left at 'writing' - run 'recover "
        "--resolve {1} --{2} --apply' again; it goes on from what each row holds now.".format(
            row_of(op, "local_c3")["label"], oid, direction))
    monkeypatch.undo()
    assert flushed and raw_of(paths["local_c3"]) == flushed[0]      # the app's bytes stand
    assert raw_of(paths["local_c2"]) == c2 and manifest_raw(op) == record
    assert ct.read_lock(env) is None
    # run again, recovery reads the row afresh
    final, om = recover(env, op, direction)
    r3 = om["rows"][1]
    if direction == "forward":
        assert final == "completed" and r3["skipped"] == "changed_since_plan"
        assert raw_of(paths["local_c3"]) == flushed[0]
    else:
        assert final == "rolled_back" and r3["restored"] is True
        after = data_of(paths["local_c3"])
        assert "spawnedFrom" not in after and after["lastFocusedAt"] == 1_800_000_000_000


def test_back_refuses_while_a_record_that_cannot_be_read_claims_its_rows(
        mkenv, tmp_path, write_transcript, write_row):
    # such a record claims every key; a back that left them all and closed would strand
    # them, since undo takes only a completed relink and back cannot run again
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    op = crashed(env, "relink-after-rows")
    oid = op.manifest["op_id"]
    c2, c3 = row_of(op, "local_c2"), row_of(op, "local_c3")
    # readable and finished, so D18 does not hold it; its rows cannot be read
    put_op(env, OPEN_ID, '{"op_id": "x", "status": "completed", "rows": [5]}')
    before, record = raws(paths), manifest_raw(op)
    assert recover_refusal(env, op, "back") == (
        "cannot roll back {0}: the record(s) in {1} cannot be read, so whether they wrote "
        "row(s) local_c3.json ({2}), local_c2.json ({3}) after it cannot be told. No command "
        "repairs a record: with the app closed, repair each one, or move its folder out of "
        "the journal. Nothing was changed. The op is left at 'writing' - run 'recover "
        "--resolve {0} --back --apply' again; it goes on from what each row holds now."
        .format(oid, OPEN_ID, c3["label"], c2["label"]))
    assert raws(paths) == before and manifest_raw(op) == record     # rolling_back not set
    assert ct.read_lock(env) is None
    shutil.rmtree(os.path.join(env.ops_dir, OPEN_ID))              # moved out by hand
    final, om = recover(env, op, "back")
    assert final == "rolled_back" and [r["restored"] for r in om["rows"]] == [True, True]


def test_back_refuses_while_the_journal_cannot_be_listed_for_the_claims(
        monkeypatch, mkenv, tmp_path, write_transcript, write_row):
    # the same stranding as a record that cannot be read: every key is claimed
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    op = crashed(env, "relink-after-rows")
    oid = op.manifest["op_id"]
    target = op_of(env, oid)
    real, calls = ct._relink_load_ops, []

    def load(env):
        # D18 lists the journal first; by the claims' turn it cannot be listed
        calls.append(1)
        if len(calls) > 1:
            raise PermissionError(13, "Access is denied")
        return real(env)
    monkeypatch.setattr(ct, "_relink_load_ops", load)
    before, record = raws(paths), manifest_raw(op)
    with pytest.raises(ct.Refusal) as exc:
        ct._recover_relink(env, target, "back", [])
    assert str(exc.value) == (
        "cannot roll back {0}: the journal folder could not be listed ([Errno 13] Access is "
        "denied), so whether a later operation wrote its rows cannot be told. Nothing was "
        "changed. The op is left at 'writing' - run 'recover --resolve {0} --back --apply' "
        "again; it goes on from what each row holds now.".format(oid))
    assert len(calls) == 2
    assert raws(paths) == before and manifest_raw(op) == record


def test_a_later_back_drops_what_an_earlier_pass_left_on_a_row_it_restores(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    op = crashed(env, "relink-after-rows")
    oid = op.manifest["op_id"]
    c3 = row_of(op, "local_c3")
    landed = raw_of(paths["local_c3"])
    write_raw(paths["local_c3"], "{not json")
    with pytest.raises(SimulatedCrash):                  # at local_c2, after its write
        recover(env, op, "back", hook=crash_on("relink-back-before-mark", 2))
    om = op_of(env, oid).manifest
    assert om["back_left"] == [entry(c3, "the row", "could not be read")]
    assert "restored" not in om["rows"][1]
    with open(paths["local_c3"], "wb") as fh:              # readable again
        fh.write(landed)
    final, om = recover(env, op, "back")
    assert final == "rolled_back"
    assert om["back_left"] == [] and om["abort_reason"] == "back restored 2 row(s)"
    assert [r["restored"] for r in om["rows"]] == [True, True]
    assert "spawnedFrom" not in data_of(paths["local_c3"])


def test_a_journaled_relink_closes_with_back(
        monkeypatch, mkenv, tmp_path, write_transcript, write_row):
    # the apply's refusal when 'writing' cannot be saved promises that back closes it
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    before = raws(paths, list(paths))
    m = plan(env)
    real = ct.set_status

    def no_space(op, status):
        if status == "writing":
            raise OSError(28, "No space left on device")
        return real(op, status)
    monkeypatch.setattr(ct, "set_status", no_space)
    with pytest.raises(ct.Refusal) as exc:
        ct.run_relink(env, m)
    assert "{0} closes it".format(recover_hint(m["op_id"])) in str(exc.value)
    monkeypatch.undo()
    op = op_of(env, m["op_id"])
    assert op.manifest["status"] == "journaled"
    env.process_lister = lambda: APP_RUNNING        # it writes no row, so needs no guard
    assert ct.classify_op(env, op)["resolutions"] == ["forward", "back"]
    final, om = recover(env, op, "back")
    assert final == "rolled_back" and om["abort_reason"] == "back restored 0 row(s)"
    assert "rolling_back" not in om and not any(r["written"] for r in om["rows"])
    assert raws(paths, list(paths)) == before and ct.read_lock(env) is None


# The recovery matrix: one journal row, local_c3, by its `written` flag and what a
# read finds; each line is built by a crash point and one edit, and each direction's
# cell is asserted exactly (the skip reason, written and restored as the record on
# disk holds them, the row's bytes, and whether abort_reason names it). local_c2 is
# written before every crash point used, and back always takes it out.

_CRASHES = {"pending": ("relink-mid-rows", 1),     # local_c3 never written: its pre-image
            "landed": ("relink-before-mark", 2),   # local_c3 landed, its mark never saved
            "written": ("relink-after-rows", 1)}   # local_c3 written and marked


def _matrix_edit(edit, env, paths, op):
    c3 = paths["local_c3"]
    if edit == "delete":
        os.unlink(c3)
    elif edit == "corrupt":
        write_raw(c3, "{not json")
    elif edit == "repoint":
        set_keys(c3, cliSessionId=LQ)
    elif edit == "repoint, unlinked":
        drop_key(c3, "spawnedFrom")
        set_keys(c3, cliSessionId=LQ)
    elif edit == "record":
        target = op_of(env, op.manifest["op_id"])
        target.manifest["rows"][1]["old"]["spawnedFrom"] = {"present": True, "value": "x"}
        ct.save_manifest(target)
    elif edit == "parent gone":
        os.unlink(paths["local_p3"])
    elif edit == "cycle":
        set_keys(paths["local_p3"], spawnedFrom=side_link("local_c3"))
    elif edit == "unreadable store":
        write_raw(os.path.join(os.path.dirname(c3), "local_zz.json"), "{not json")
    elif edit == "touched":
        set_keys(c3, lastFocusedAt=1_800_000_000_000)
    elif edit == "app changed":
        set_keys(c3, spawnedFrom=dict(tool_link("local_p3"), taskId="task_app"))
    else:
        assert edit == "none"


def _cell(skip=None, written=False, restored=False, row="same", named=None, refused=False):
    """A cell: ROW is "same" (as before recovery, there or not), "linked" (the pre-image with
    the link) or "unlinked" (the pre-image); NAMED is what abort_reason says of local_c3."""
    return {"skip": skip, "written": written, "restored": restored, "row": row,
            "named": named, "refused": refused}


_MATRIX = [
    # line                    crash      edit                 forward              back
    ("written", "written", "repoint", _cell(written=True), None),
    ("absent", "pending", "delete", _cell(skip="gone"), _cell()),
    ("unreadable", "pending", "corrupt", _cell(skip="unreadable"), _cell()),
    ("other, new", "landed", "repoint", _cell(skip="repointed"),
     _cell(written=True, named="the row ({0})".format(THE_ROW_LEFT))),
    ("other, none", "pending", "repoint", _cell(skip="repointed"), _cell()),
    ("same, new", "landed", "none", _cell(written=True),
     _cell(written=True, restored=True, row="unlinked")),
    ("record disagrees", "pending", "record", _cell(refused=True), _cell()),
    ("target gone", "pending", "parent gone", _cell(skip="parent_changed"), _cell()),
    ("cycle", "pending", "cycle", _cell(skip="lineage_cycle"), _cell()),
    ("links unreadable", "pending", "unreadable store", _cell(skip="edges_unreadable"),
     _cell()),
    ("checks pass", "pending", "none", _cell(written=True, row="linked"), _cell()),
    ("bytes differ", "pending", "touched", _cell(skip="changed_since_plan"), _cell()),
    ("written, absent", "written", "delete", None,
     _cell(written=True, restored=True, named="the row (gone from the store)")),
    ("written, unreadable", "written", "corrupt", None,
     _cell(written=True, named="the row (could not be read)")),
    ("written, other, new", "written", "repoint", None,
     _cell(written=True, named="the row ({0})".format(THE_ROW_LEFT))),
    ("written, other, none", "written", "repoint, unlinked", None,
     _cell(written=True, restored=True, named="the row (now opens another conversation)")),
    ("written, same, new", "written", "none", None,
     _cell(written=True, restored=True, row="unlinked")),
    ("written, same, none", "written", "app changed", None,
     _cell(written=True, restored=True, named="spawnedFrom (the app changed it since)")),
]
_MATRIX_CASES = [(direction, line) for line, _c, _e, fwd, back in _MATRIX
                 for direction, cell in (("forward", fwd), ("back", back)) if cell is not None]


@pytest.mark.parametrize("direction, line", _MATRIX_CASES,
                         ids=["{0}: {1}".format(d, ln) for d, ln in _MATRIX_CASES])
def test_recovery_matrix(direction, line, mkenv, tmp_path, write_transcript, write_row):
    _l, crash, edit, fwd, back = next(e for e in _MATRIX if e[0] == line)
    want = fwd if direction == "forward" else back
    env = mkenv(tmp_path)
    paths = relink_store(env, write_transcript, write_row)
    c3 = paths["local_c3"]
    pre = data_of(c3)
    op = crashed(env, *_CRASHES[crash])
    oid = op.manifest["op_id"]
    _matrix_edit(edit, env, paths, op)
    before = raw_of(c3) if os.path.exists(c3) else None
    if want["refused"]:
        assert "does not match row local_c3.json" in recover_refusal(env, op, direction,
                                                                     ct.LayoutError)
        status = "writing"
    else:
        status = "completed" if direction == "forward" else "rolled_back"
        assert recover(env, op, direction)[0] == status
    om = op_of(env, oid).manifest
    assert om["status"] == status
    r3 = om["rows"][1]
    assert r3.get("skipped") == want["skip"]
    assert r3["written"] is want["written"]
    assert r3.get("restored", False) is want["restored"]
    if want["row"] == "same":
        assert (raw_of(c3) if os.path.exists(c3) else None) == before
    else:
        assert data_of(c3) == (dict(pre, spawnedFrom=tool_link("local_p3"))
                               if want["row"] == "linked" else pre)
    if direction == "back":
        if want["named"]:
            assert "local_c3 " + want["named"] in om["abort_reason"]
        else:
            assert "local_c3" not in om["abort_reason"]
        assert om["rows"][0]["restored"] is True
        assert "spawnedFrom" not in data_of(paths["local_c2"])
