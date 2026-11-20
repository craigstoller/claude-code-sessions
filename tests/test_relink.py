"""`relink`: give a row the link and chain its conversation already has elsewhere.

Implements docs/specs/2026-11-19-relink-design.md. This file starts with the
value helpers every later step builds on: the app-written shape check, the
key-by-key apply and the state comparison; then the resolution core, which
decides row by row what to write and what to leave out; then plan_relink,
the strict scan, the refusals and the narrowing around it. Fake cast only:
Northwind, ACME-REVIEW, the A/LP/LC ids and local_p/local_c/local_r rows of
the converge tests. Last, the report: the per-account listing, the totals,
and the public projection --json and --anonymize print.
"""
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
from test_converge import (A1, A2, A3, A4, LC, LD, LP, LX, LY, NUM, O1, O2, O3, O4, ORG, TASK,
                           identity_files, lrow, side_link, t_entries)


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
