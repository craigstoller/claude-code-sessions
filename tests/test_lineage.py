"""Tests for the lineage resolver (docs/specs/2026-11-17-carry-lineage-design.md,
"The lineage pass", steps 1 to 6). The resolver is pure: it reads LineageRows
and returns the link, chain and record a row should carry. Fake cast only:
ids like local_pb and task_7f, accounts acct_a and acct_b.
"""
import pytest

import claude_code_sessions as ct



def LR(*a):
    return ct.LineageRow(*a)


SIDE = {"sessionId": "local_pa", "taskId": "task_7f", "title": "Northwind",
        "sideSession": True}


def row(account, sid, cli, planned=False, name=None, **extra):
    d = {"sessionId": sid, "cliSessionId": cli}
    d.update(extra)
    return LR(account, name or sid + ".json", d, planned)


def holder_row(link=SIDE, account="acct_a", **extra):
    if link is not None:
        extra["spawnedFrom"] = link
    return row(account, "local_c", "cli_c", **extra)


def store_a(*extra_rows, parent_cli="cli_pa"):
    return {"acct_a": ct._lineage_index(
        [row("acct_a", "local_pa", parent_cli)] + list(extra_rows))}


def dest_with(*rows):
    return ct._lineage_index(list(rows))


def resolve(holders, stores, dest, held=None, alive=("cli_pa", "cli_ra"),
            **kw):
    return ct._lineage_resolve(
        "cli_c", "local_c.json", holders, stores, dest,
        held=held or {}, transcript_exists=set(alive).__contains__, **kw)


def codes(res):
    return [n["code"] for n in res["lineage"]["notes"]]




@pytest.fixture(autouse=True)
def _dest_parent():
    global DEST_PARENT
    DEST_PARENT = row("acct_b", "local_pb", "cli_pa")


def test_side_session_link_translates_to_the_parent_row_in_b():
    h = holder_row()
    res = resolve([h], store_a(h), dest_with(DEST_PARENT))
    assert res["link"] == {"sessionId": "local_pb", "taskId": "task_7f"}
    assert res["chain"] is None
    assert res["lineage"]["carried_keys"] == ["sessionId", "taskId"]
    assert res["lineage"]["dropped_keys"] == ["sideSession", "title"]
    assert res["lineage"]["parent_row"] == "local_pb"
    assert res["lineage"]["parent_session"] == "cli_pa"
    assert res["lineage"]["parent_planned"] is False
    assert res["edges"] == {"parent": "cli_pa", "root": None}
    assert codes(res) == []


def test_older_three_key_link_is_cleared():
    link = {"sessionId": "local_pa", "taskId": "task_7f", "title": "Northwind"}
    assert ct._lineage_variant_ok(link)
    h = holder_row(link)
    res = resolve([h], store_a(h), dest_with(DEST_PARENT))
    assert res["link"] == {"sessionId": "local_pb", "taskId": "task_7f"}
    assert res["lineage"]["dropped_keys"] == ["title"]
    assert not ct._lineage_variant_ok(dict(link, title=""))
    assert not ct._lineage_variant_ok(dict(link, title=None))


def test_tools_own_two_key_link_is_cleared():
    link = {"sessionId": "local_pa", "taskId": "task_7f"}
    assert ct._lineage_variant_ok(link)
    h = holder_row(link)
    res = resolve([h], store_a(h), dest_with(DEST_PARENT))
    assert res["link"] == {"sessionId": "local_pb", "taskId": "task_7f"}
    assert res["lineage"]["dropped_keys"] == []


def test_variant_and_task_predicates_are_separate():
    pending = {"sessionId": "local_pa", "taskId": "task_7f", "pendingPop": True}
    assert not ct._lineage_variant_ok(pending)
    assert ct._lineage_task_ok(pending)
    h = holder_row(pending)
    res = resolve([h], store_a(h), dest_with(DEST_PARENT))
    assert res["link"] is None
    assert codes(res) == ["lineage_variant"]

    notask = {"sessionId": "local_pa", "sideSession": True}
    assert ct._lineage_variant_ok(notask)
    assert not ct._lineage_task_ok(notask)
    h = holder_row(notask)
    res = resolve([h], store_a(h), dest_with(DEST_PARENT))
    assert res["link"] is None
    assert codes(res) == ["lineage_no_task"]
    assert not ct._lineage_variant_ok("x")
    assert not ct._lineage_task_ok(None)


def test_empty_or_non_string_task_id_counts_as_absent_not_disagreeing():
    h1 = holder_row(SIDE, account="acct_a")
    h2 = row("acct_c", "local_c", "cli_c", spawnedFrom=dict(SIDE, taskId=""))
    h3 = row("acct_d", "local_c", "cli_c", spawnedFrom=dict(SIDE, taskId=7))
    stores = {a: ct._lineage_index([row(a, "local_pa", "cli_pa")])
              for a in ("acct_a", "acct_c", "acct_d")}
    res = resolve([h1, h2, h3], stores, dest_with(DEST_PARENT))
    assert res["link"] == {"sessionId": "local_pb", "taskId": "task_7f"}
    assert codes(res) == []


def test_holders_disagree_on_parent_any_variant_counts():
    h1 = holder_row(SIDE, account="acct_a")
    other = {"sessionId": "local_px", "pendingPop": True}
    h2 = row("acct_c", "local_c", "cli_c", spawnedFrom=other)
    stores = {
        "acct_a": ct._lineage_index([row("acct_a", "local_pa", "cli_pa")]),
        "acct_c": ct._lineage_index([row("acct_c", "local_px", "cli_px")]),
    }
    res = resolve([h1, h2], stores, dest_with(DEST_PARENT),
                  alive=("cli_pa", "cli_px"))
    assert res["link"] is None
    assert codes(res) == ["lineage_parent_disagree"]


def test_holders_disagree_on_task_id():
    h1 = holder_row(SIDE, account="acct_a")
    h2 = row("acct_c", "local_c", "cli_c", spawnedFrom=dict(SIDE, taskId="task_9"))
    stores = {a: ct._lineage_index([row(a, "local_pa", "cli_pa")])
              for a in ("acct_a", "acct_c")}
    res = resolve([h1, h2], stores, dest_with(DEST_PARENT))
    assert res["link"] is None
    assert codes(res) == ["lineage_task_disagree"]


def test_self_link_and_dead_parent():
    h = holder_row()
    res = resolve([h], store_a(h, parent_cli="cli_c"), dest_with(DEST_PARENT),
                  alive=("cli_c",))
    assert res["link"] is None and codes(res) == ["lineage_self"]
    res = resolve([h], store_a(h), dest_with(DEST_PARENT), alive=())
    assert res["link"] is None and codes(res) == ["lineage_parent_dead"]


def test_malformed_holder_links_yield_nothing_and_never_raise():
    stores = store_a(row("acct_a", "local_odd", "cli_odd", name="local_zz.json"),
                     row("acct_a", "local_nocli", ""))
    for bad in ("not a dict", {"sessionId": 5}, {"sessionId": "local_nope"},
                {"sessionId": "local_zz"}, {"sessionId": "local_nocli"}, {},
                None, [1]):
        h = row("acct_a", "local_c", "cli_c", spawnedFrom=bad)
        res = resolve([h], stores, dest_with(DEST_PARENT))
        assert res["link"] is None and res["chain"] is None
        assert codes(res) == []
    # a malformed holder beside a good one is not a disagreement
    good = holder_row(account="acct_a")
    bad = row("acct_c", "local_c", "cli_c", spawnedFrom="x")
    stores["acct_c"] = ct._lineage_index([])
    res = resolve([good, bad], stores, dest_with(DEST_PARENT))
    assert res["link"]["sessionId"] == "local_pb"
    # unknown holder store, non-dict data, bad index
    h = row("acct_x", "local_c", "cli_c", spawnedFrom=SIDE)
    assert resolve([h], {}, dest_with())["link"] is None
    assert resolve([LR("acct_a", "x.json", "oops")], {}, dest_with())["link"] \
        is None
    assert ct._lineage_index([LR("a", "x.json", None)]).by_id == {}


def test_parent_not_in_account_held_and_drifted_are_told_apart():
    h = holder_row()
    res = resolve([h], store_a(h), dest_with())
    assert codes(res) == ["parent_not_in_account"]
    res = resolve([h], store_a(h), dest_with(), held={"cli_pa": "title_conflict"})
    assert codes(res) == ["parent_pair_held"]
    assert "title_conflict" in res["lineage"]["notes"][0]["detail"]
    drifted = row("acct_b", "local_pa", "cli_other")
    res = resolve([h], store_a(h), dest_with(drifted))
    assert res["link"] is None and codes(res) == ["parent_row_drifted"]
    # a stem-mismatched row is not a candidate either
    mism = row("acct_b", "local_zz", "cli_pa", name="local_pb.json")
    res = resolve([h], store_a(h), dest_with(mism))
    assert res["link"] is None and codes(res) == ["parent_not_in_account"]


def test_parent_pick_order():
    p = ct._lineage_pick
    assert p([]) is None
    a = row("acct_b", "local_p1", "cli_pa", lastActivityAt=100)
    b = row("acct_b", "local_p2", "cli_pa", lastActivityAt=200)
    assert p([a, b]) is b
    assert p([a, b], prefer_name="local_p1.json") is a
    arch = row("acct_b", "local_p3", "cli_pa", lastActivityAt=900,
               isArchived=True)
    assert p([a, b, arch]) is b
    assert p([arch]) is arch
    planned = row("acct_b", "local_p4", "cli_pa", planned=True,
                  lastActivityAt=900)
    assert p([b, planned]) is b
    assert p([planned, arch]) is planned
    # activity: missing, bool and string count as 0
    junk = row("acct_b", "local_p5", "cli_pa", lastActivityAt=True)
    assert p([junk, a]) is a
    # name, then account
    n1 = row("acct_b", "local_p6", "cli_pa")
    n2 = row("acct_b", "local_p7", "cli_pa")
    assert p([n1, n2]) is n2
    x = row("acct_a", "local_p6", "cli_pa")
    y = row("acct_c", "local_p6", "cli_pa")
    assert p([x, y]) is y
    # the pick drives the target
    h = holder_row()
    res = resolve([h], store_a(h), dest_with(a, b))
    assert res["link"]["sessionId"] == "local_p2"
    res = resolve([h], store_a(h), dest_with(a, b), prefer_parent_name="local_p1.json")
    assert res["link"]["sessionId"] == "local_p1"
    pl = row("acct_b", "local_p8", "cli_pa", planned=True)
    res = resolve([h], store_a(h), dest_with(pl))
    assert res["lineage"]["parent_planned"] is True


def test_newest_holder_without_task_id_falls_back_to_an_older_cleared_one():
    old = holder_row(SIDE, account="acct_a", lastActivityAt=1)
    new = row("acct_c", "local_c", "cli_c", lastActivityAt=9,
              spawnedFrom={"sessionId": "local_pa", "sideSession": True})
    stores = {a: ct._lineage_index([row(a, "local_pa", "cli_pa")])
              for a in ("acct_a", "acct_c")}
    res = resolve([new, old], stores, dest_with(DEST_PARENT))
    assert res["link"] == {"sessionId": "local_pb", "taskId": "task_7f"}
    assert codes(res) == []


def chain_store(h, **extra):
    return store_a(h, row("acct_a", "local_ra", "cli_ra"), **extra)


def test_chain_translated_exactly_two_keys_extra_keys_recorded():
    sc = {"firstSessionId": "local_ra", "depth": 2, "extra": 1}
    h = holder_row(startChain=sc)
    dest = dest_with(DEST_PARENT, row("acct_b", "local_rb", "cli_ra",
                                      planned=True))
    res = resolve([h], chain_store(h), dest)
    assert res["chain"] == {"firstSessionId": "local_rb", "depth": 2}
    assert res["lineage"]["root_row"] == "local_rb"
    assert res["lineage"]["root_session"] == "cli_ra"
    assert res["lineage"]["root_planned"] is True
    assert res["lineage"]["dropped_keys"] == ["sideSession", "title",
                                              "startChain.extra"]
    assert res["edges"] == {"parent": "cli_pa", "root": "cli_ra"}
    assert codes(res) == []
    res = resolve([h], chain_store(h), dest,
                  prefer_root_name="local_rb.json")
    assert res["chain"]["firstSessionId"] == "local_rb"


def test_chain_dropped_on_bool_depth_dead_root_disagreement_or_self_root():
    rb = row("acct_b", "local_rb", "cli_ra")
    dest = dest_with(DEST_PARENT, rb)

    def go(sc, **kw):
        h = holder_row(startChain=sc)
        res = resolve([h], chain_store(h), dest, **kw)
        assert res["link"] is not None
        assert res["chain"] is None
        assert codes(res) == ["lineage_chain_dropped"]
        assert res["lineage"]["root_row"] == ""
        assert res["edges"]["root"] is None
        return res

    go({"firstSessionId": "local_ra", "depth": True})
    go({"firstSessionId": "local_ra", "depth": 0})
    go({"firstSessionId": "local_ra", "depth": 1.5})
    go({"firstSessionId": "local_ra", "depth": "2"})
    go({"firstSessionId": "local_ra"})
    go({"firstSessionId": "local_ra", "depth": 2}, alive=("cli_pa",))
    # root is the child itself
    h = holder_row(startChain={"firstSessionId": "local_c", "depth": 2})
    res = resolve([h], chain_store(h), dest)
    assert res["chain"] is None and codes(res) == ["lineage_chain_dropped"]
    # root has no row in the account
    h = holder_row(startChain={"firstSessionId": "local_ra", "depth": 2})
    res = resolve([h], chain_store(h), dest_with(DEST_PARENT))
    assert res["chain"] is None and codes(res) == ["lineage_chain_dropped"]
    # the root's row is the row being written
    same = row("acct_b", "local_c", "cli_ra")
    res = resolve([h], chain_store(h), dest_with(DEST_PARENT, same))
    assert res["chain"] is None and codes(res) == ["lineage_chain_dropped"]
    # two holders disagreeing on root or depth
    h2 = row("acct_c", "local_c", "cli_c", spawnedFrom=SIDE,
             startChain={"firstSessionId": "local_ra", "depth": 3})
    stores = chain_store(h)
    stores["acct_c"] = ct._lineage_index([row("acct_c", "local_pa", "cli_pa"),
                                          row("acct_c", "local_ra", "cli_ra")])
    res = resolve([h, h2], stores, dest)
    assert res["chain"] is None and codes(res) == ["lineage_chain_dropped"]
    stores["acct_c"] = ct._lineage_index([row("acct_c", "local_pa", "cli_pa"),
                                          row("acct_c", "local_ra", "cli_rz")])
    h2 = row("acct_c", "local_c", "cli_c", spawnedFrom=SIDE,
             startChain={"firstSessionId": "local_ra", "depth": 2})
    res = resolve([h, h2], stores, dest, alive=("cli_pa", "cli_ra", "cli_rz"))
    assert res["chain"] is None and codes(res) == ["lineage_chain_dropped"]
    # a dropped link drops the chain, with the note
    h = holder_row(startChain={"firstSessionId": "local_ra", "depth": 2})
    res = resolve([h], chain_store(h), dest_with(rb))
    assert res["link"] is None
    assert res["chain"] is None
    assert codes(res) == ["parent_not_in_account", "lineage_chain_dropped"]


def test_an_unresolvable_chain_source_drops_the_chain_with_its_note():
    dest = dest_with(DEST_PARENT, row("acct_b", "local_rb", "cli_ra"))
    good = {"firstSessionId": "local_ra", "depth": 2}
    for bad in ({"firstSessionId": "local_gone", "depth": 2},
                {"firstSessionId": 7, "depth": 2}, {"depth": 2}):
        # a lone (sync) source
        h = holder_row(startChain=bad)
        res = resolve([h], chain_store(h), dest)
        assert res["link"] is not None and res["chain"] is None
        assert codes(res) == ["lineage_chain_dropped"]
        # one good and one unresolvable source
        h1 = holder_row(startChain=good, account="acct_a")
        h2 = row("acct_c", "local_c", "cli_c", spawnedFrom=SIDE,
                 startChain=bad)
        stores = chain_store(h1)
        stores["acct_c"] = ct._lineage_index(
            [row("acct_c", "local_pa", "cli_pa")])
        res = resolve([h1, h2], stores, dest)
        assert res["chain"] is None
        assert codes(res) == ["lineage_chain_dropped"]


def test_no_startchain_anywhere_means_no_chain_and_no_note():
    h = holder_row(startChain="not a dict")
    res = resolve([h], store_a(h), dest_with(DEST_PARENT))
    assert res["chain"] is None
    assert codes(res) == []
    res = resolve([], {}, dest_with())
    assert res["link"] is None and res["chain"] is None and codes(res) == []


def test_lineage_record_has_every_field_with_its_type_and_no_repeated_code():
    assert ct.LINEAGE_FORMAT == 1
    shape = {"parent_session": str, "parent_row": str, "parent_planned": bool,
             "root_session": str, "root_row": str, "root_planned": bool,
             "carried_keys": list, "dropped_keys": list, "notes": list}
    h = holder_row(startChain={"firstSessionId": "local_ra", "depth": 2})
    cases = [
        resolve([h], chain_store(h), dest_with()),
        resolve([h], chain_store(h), dest_with(DEST_PARENT)),
        resolve([], {}, dest_with()),
        resolve([h], chain_store(h), dest_with(DEST_PARENT,
                row("acct_b", "local_rb", "cli_ra"))),
    ]
    for res in cases:
        assert set(res) == {"child", "link", "chain", "lineage", "edges"}
        assert set(res["edges"]) == {"parent", "root"}
        rec = res["lineage"]
        assert set(rec) == set(shape)
        for k, t in shape.items():
            assert type(rec[k]) is t, k
        cs = [n["code"] for n in rec["notes"]]
        assert len(cs) == len(set(cs))
        for n in rec["notes"]:
            assert set(n) == {"code", "detail"}
            assert n["code"] in ct.LINEAGE_PHRASES
            assert isinstance(n["detail"], str)
    assert set(ct.LINEAGE_LEFT_OUT) <= set(ct.LINEAGE_PHRASES)
    assert "lineage_chain_dropped" not in ct.LINEAGE_LEFT_OUT
    assert "existing_cycle" not in ct.LINEAGE_LEFT_OUT
    assert len(ct.LINEAGE_LEFT_OUT) == 13


def test_note_details_name_ids_by_8_chars_and_no_titles():
    long_pa = "cli_pa_0123456789"
    h = row("acct_a", "local_c", "cli_c",
            spawnedFrom=dict(SIDE, title="ACME-REVIEW"))
    stores = {"acct_a": ct._lineage_index([row("acct_a", "local_pa", long_pa)])}
    res = resolve([h], stores, dest_with(), alive=(long_pa,))
    (n,) = res["lineage"]["notes"]
    assert n["code"] == "parent_not_in_account"
    assert "cli_pa_0" in n["detail"] and "cli_pa_01" not in n["detail"]
    assert "ACME-REVIEW" not in n["detail"]
    h = row("acct_a", "local_c", "cli_c",
            spawnedFrom={"sessionId": "local_pa", "title": "ACME-REVIEW"})
    res = resolve([h], stores, dest_with(), alive=(long_pa,))
    for n in res["lineage"]["notes"]:
        assert "ACME-REVIEW" not in n["detail"]


def test_shortened_ids_keep_a_row_ids_prefix_and_8_more():
    """R18: a row id keeps its `local_` prefix and the first 8 characters
    after it, so a minted id no longer prints as the prefix and 2
    characters; any other value, a conversation id among them, keeps its
    first 8. Short values are kept whole, and nothing raises."""
    minted = "local_3f2a9c1d-7e4b-4c2a-9d1e-0123456789ab"
    assert ct._lineage_short(minted) == "local_3f2a9c1d"
    assert ct._lineage_short("local_p") == "local_p"
    assert ct._lineage_short("local_") == "local_"
    assert ct._lineage_short("9aaa0000-1111-4000-8000-00000000000a") == \
        "9aaa0000"
    assert ct._lineage_short("cli_pa_0123456789") == "cli_pa_0"
    assert ct._lineage_short("LOCAL_3f2a9c1d-7e4b") == "LOCAL_3f"
    assert ct._lineage_short(None) == "None"
    assert ct._lineage_short(12345678901) == "12345678"


# ---------------------------------------------------------------- Task 2
# Cycles, write order and the row rewrite.

import base64
import json


def link_row(account, sid, cli, link=None, chain=None, planned=False):
    extra = {}
    if link is not None:
        extra["spawnedFrom"] = {"sessionId": link, "taskId": "task_7f"}
    if chain is not None:
        extra["startChain"] = {"firstSessionId": chain, "depth": 1}
    return row(account, sid, cli, planned=planned, **extra)


def prop(child, parent=None, root=None, parent_row="", root_row="",
         dropped=None):
    """A hand-built _lineage_resolve result: child conversation `child`
    proposing an edge to conversation `parent` and `root`."""
    link = {"sessionId": parent_row or "r_" + str(parent),
            "taskId": "task_7f"} if parent else None
    chain = {"firstSessionId": root_row or "r_" + str(root),
             "depth": 1} if root else None
    return {
        "child": child, "link": link, "chain": chain,
        "edges": {"parent": parent, "root": root},
        "lineage": {
            "parent_session": parent or "",
            "parent_row": link["sessionId"] if link else "",
            "parent_planned": False,
            "root_session": root or "",
            "root_row": chain["firstSessionId"] if chain else "",
            "root_planned": False,
            "carried_keys": ["sessionId", "taskId"] if link else [],
            "dropped_keys": list(dropped or []),
            "notes": [],
        }}


def note_codes(res):
    return [n["code"] for n in res["lineage"]["notes"]]


def existing_of(*rows):
    return ct._lineage_existing_edges(ct._lineage_index(list(rows)))


def test_two_account_cycle_drops_the_later_parent_edge():
    # Account B: Q already links to P. Proposed: P -> Q (closes). The
    # proposed edge is the one dropped.
    ex = existing_of(link_row("acct_b", "local_q", "cli_q", link="local_p"),
                     row("acct_b", "local_p", "cli_p"))
    assert ex == {"cli_q": {"cli_p"}}
    res = {("acct_b", "local_p.json"): prop("cli_p", parent="cli_q")}
    n = ct._lineage_drop_cycles(res, ex)
    r = res[("acct_b", "local_p.json")]
    assert n == 0
    assert r["link"] is None and r["edges"]["parent"] is None
    assert note_codes(r) == ["lineage_cycle"]


def test_three_account_cycle():
    # Three conversations; two proposed edges are fine, the third closes the
    # loop. Edges go in order of the child's conversation id.
    res = {
        ("acct_b", "z.json"): prop("cli_c", parent="cli_a"),
        ("acct_b", "y.json"): prop("cli_b", parent="cli_c"),
        ("acct_b", "x.json"): prop("cli_a", parent="cli_b"),
    }
    ct._lineage_drop_cycles(res, {})
    dropped = [k for k, v in res.items() if v["link"] is None]
    # cli_a first: a->b kept; cli_b: b->c kept; cli_c: c->a closes.
    assert dropped == [("acct_b", "z.json")]
    assert note_codes(res[("acct_b", "z.json")]) == ["lineage_cycle"]


def test_root_edge_never_evicts_a_parent_edge():
    res = {
        ("acct_b", "c.json"): prop("cli_c", parent="cli_x", root="cli_r"),
        ("acct_b", "r.json"): prop("cli_r", parent="cli_c"),
    }
    ct._lineage_drop_cycles(res, {})
    c = res[("acct_b", "c.json")]
    r = res[("acct_b", "r.json")]
    assert c["link"] is not None and r["link"] is not None
    assert c["chain"] is None and c["edges"]["root"] is None
    assert note_codes(c) == ["lineage_chain_dropped"]
    assert c["lineage"]["root_row"] == "" and c["lineage"]["root_session"] == ""


def test_a_proposed_edge_joining_an_existing_cycle_is_dropped():
    ex = existing_of(
        link_row("acct_b", "local_p", "cli_p", link="local_q"),
        link_row("acct_b", "local_q", "cli_q", link="local_p"))
    res = {("acct_b", "n.json"): prop("cli_n", parent="cli_p"),
           ("acct_b", "m.json"): prop("cli_p", parent="cli_n")}
    n = ct._lineage_drop_cycles(res, ex)
    assert n == 1
    # n -> p is fine (p does not reach n); p -> n then closes via n -> p.
    assert res[("acct_b", "n.json")]["link"] is not None
    assert res[("acct_b", "m.json")]["link"] is None
    assert note_codes(res[("acct_b", "m.json")]) == ["lineage_cycle"]


def test_existing_cycle_counted_once_even_when_no_proposed_edge_touches_it():
    ex = existing_of(
        link_row("acct_b", "local_p", "cli_p", link="local_q"),
        link_row("acct_b", "local_q", "cli_q", chain="local_p"),
        link_row("acct_b", "local_s", "cli_s", link="local_s"),
        link_row("acct_b", "local_t", "cli_t", link="local_p"))
    assert ct._lineage_drop_cycles({}, ex) == 2
    assert ct._lineage_drop_cycles(
        {("acct_b", "a.json"): prop("cli_a", parent="cli_t")}, ex) == 2


def test_planned_rows_links_never_enter_the_existing_graph():
    ex = existing_of(
        link_row("acct_b", "local_p", "cli_p", link="local_q", planned=True),
        row("acct_b", "local_q", "cli_q"))
    assert ex == {}
    # A planned row's old link, which would close q -> p -> q, is absent.
    ex = existing_of(
        link_row("acct_b", "local_p", "cli_p", link="local_q", planned=True),
        link_row("acct_b", "local_q", "cli_q", link="local_p"))
    assert ex == {"cli_q": {"cli_p"}}


def test_existing_edges_union_every_row_of_a_conversation():
    ex = existing_of(
        row("acct_b", "local_c1", "cli_c"),
        link_row("acct_b", "local_p1", "cli_p", link="local_c1"),
        link_row("acct_b", "local_p2", "cli_p", link="local_q1"),
        row("acct_b", "local_q1", "cli_q"))
    assert ex == {"cli_p": {"cli_c", "cli_q"}}
    res = {("acct_b", "local_c1.json"): prop("cli_c", parent="cli_p")}
    # cli_c -> cli_p: p reaches c through the first row, so it closes.
    ct._lineage_drop_cycles(res, ex)
    assert note_codes(res[("acct_b", "local_c1.json")]) == ["lineage_cycle"]
    # p reaches q only through the second row's edge.
    res = {("acct_b", "q.json"): prop("cli_q", parent="cli_p")}
    ct._lineage_drop_cycles(res, ex)
    assert note_codes(res[("acct_b", "q.json")]) == ["lineage_cycle"]


def test_dropping_an_edge_clears_link_and_lineage_together():
    ex = {"cli_p": {"cli_c"}}
    r = prop("cli_c", parent="cli_p", root="cli_r",
             dropped=["title", "startChain.extra"])
    r["lineage"]["parent_planned"] = True
    r["lineage"]["root_planned"] = True
    res = {("acct_b", "c.json"): r}
    ct._lineage_drop_cycles(res, ex)
    lin = r["lineage"]
    assert r["link"] is None and r["chain"] is None
    assert r["edges"] == {"parent": None, "root": None}
    assert lin["parent_session"] == lin["parent_row"] == ""
    assert lin["root_session"] == lin["root_row"] == ""
    assert lin["parent_planned"] is False and lin["root_planned"] is False
    assert lin["carried_keys"] == [] and lin["dropped_keys"] == []
    assert note_codes(r) == ["lineage_cycle", "lineage_chain_dropped"]


def test_dropping_a_root_edge_keeps_the_link_and_its_dropped_keys():
    ex = {"cli_r": {"cli_c"}}
    r = prop("cli_c", parent="cli_p", root="cli_r",
             dropped=["title", "startChain.extra"])
    res = {("acct_b", "c.json"): r}
    ct._lineage_drop_cycles(res, ex)
    assert r["link"] is not None and r["chain"] is None
    assert r["lineage"]["dropped_keys"] == ["title"]
    assert r["lineage"]["parent_row"] != ""
    assert note_codes(r) == ["lineage_chain_dropped"]


def test_one_conversation_in_two_accounts_gets_two_independent_results():
    ex = {"cli_p": {"cli_c"}}
    rb = {("acct_b", "c.json"): prop("cli_c", parent="cli_p")}
    ra = {("acct_a", "c.json"): prop("cli_c", parent="cli_p")}
    ct._lineage_drop_cycles(rb, ex)
    ct._lineage_drop_cycles(ra, {})
    assert rb[("acct_b", "c.json")]["link"] is None
    assert ra[("acct_a", "c.json")]["link"] is not None


def test_drop_cycles_does_not_mutate_the_existing_graph():
    ex = {"cli_p": {"cli_c"}}
    ct._lineage_drop_cycles(
        {("acct_b", "n.json"): prop("cli_n", parent="cli_p")}, ex)
    assert ex == {"cli_p": {"cli_c"}}


def test_deep_chain_does_not_recurse():
    ex = {"c%d" % i: {"c%d" % (i + 1)} for i in range(3000)}
    res = {("a", "x.json"): prop("c3000", parent="c0")}
    assert ct._lineage_drop_cycles(res, ex) == 0
    assert res[("a", "x.json")]["link"] is None
    ex["c3000"] = {"c0"}
    assert ct._lineage_drop_cycles({}, ex) == 1


def test_resolve_result_names_the_child_conversation():
    h = holder_row()
    res = resolve([h], store_a(h), dest_with(DEST_PARENT))
    assert res["child"] == "cli_c"


def wrow(name, parent=None, root=None, store="/s", parent_planned=True,
         root_planned=True):
    r = {"store_path": store, "name": name + ".json"}
    if parent or root:
        r["lineage"] = {"parent_row": parent or "",
                        "parent_planned": bool(parent and parent_planned),
                        "root_row": root or "",
                        "root_planned": bool(root and root_planned)}
    return r


WKEY = lambda r: (r["store_path"], r["name"])


def names(rows):
    return [r["name"][:-5] for r in rows]


def test_write_order_parents_and_roots_first_stable_ties():
    rows = [wrow("a", parent="b"), wrow("b", parent="c", root="d"),
            wrow("c"), wrow("e"), wrow("d")]
    out = ct._lineage_write_order(rows, WKEY)
    assert names(out) == ["c", "e", "d", "b", "a"]
    assert ct._lineage_write_order([wrow("x"), wrow("y")], WKEY) == \
        [wrow("x"), wrow("y")]


def test_write_order_ignores_unplanned_other_store_and_missing_targets():
    rows = [wrow("a", parent="b", parent_planned=False), wrow("b"),
            wrow("c", parent="b", store="/other"), wrow("d", parent="zz")]
    assert names(ct._lineage_write_order(rows, WKEY)) == ["a", "b", "c", "d"]


def test_write_order_terminates_on_a_forced_cycle():
    rows = [wrow("a", parent="b"), wrow("b", parent="a"), wrow("c"),
            wrow("d", parent="a")]
    out = ct._lineage_write_order(rows, WKEY)
    assert names(out) == ["c", "a", "b", "d"]
    assert len(out) == 4


def test_write_order_survives_malformed_lineage():
    rows = [{"store_path": "/s", "name": "a.json", "lineage": "x"},
            {"store_path": "/s", "name": "b.json", "lineage": None}]
    assert ct._lineage_write_order(rows, WKEY) == rows


def b64j(d):
    return base64.b64encode(
        json.dumps(d, separators=(",", ":")).encode()).decode("ascii")


def raw(s):
    return base64.b64decode(s).decode()


def test_rewrite_post_keeps_key_position_and_touches_nothing_else():
    d = {"a": 1, "spawnedFrom": {"sessionId": "x", "taskId": "t",
                                 "title": "Northwind"},
         "m": [1, {"k": "v"}], "startChain": {"firstSessionId": "r",
                                              "depth": 2, "z": 1},
         "z": "end"}
    out = ct._lineage_rewrite_post(
        b64j(d), {"sessionId": "local_pb", "taskId": "t"},
        {"firstSessionId": "local_rb", "depth": 2})
    assert raw(out) == (
        '{"a":1,"spawnedFrom":{"sessionId":"local_pb","taskId":"t"},'
        '"m":[1,{"k":"v"}],'
        '"startChain":{"firstSessionId":"local_rb","depth":2},"z":"end"}')


def test_rewrite_post_appends_a_new_key_and_pops_a_removed_one():
    d = {"a": 1, "spawnedFrom": {"sessionId": "x", "taskId": "t"}, "b": 2}
    out = ct._lineage_rewrite_post(
        b64j(d), None, {"firstSessionId": "r", "depth": 1})
    assert raw(out) == ('{"a":1,"b":2,'
                        '"startChain":{"firstSessionId":"r","depth":1}}')
    out = ct._lineage_rewrite_post(b64j({"a": 1}), None, None)
    assert raw(out) == '{"a":1}'


def test_rewrite_post_is_idempotent_and_keeps_non_ascii_as_today():
    d = {"title": "café", "spawnedFrom": {"sessionId": "x"}}
    link = {"sessionId": "y", "taskId": "t"}
    once = ct._lineage_rewrite_post(b64j(d), link, None)
    assert ct._lineage_rewrite_post(once, link, None) == once
    assert json.loads(base64.b64decode(once))["title"] == "café"
    assert "\\u00e9" in raw(once)


def test_note_codes_phrases_and_left_out_set_match_the_table_exactly():
    """The plan's note-code table, verbatim: every code to the phrase the
    reports print, in the table's order (the `links :` line breaks ties by
    it), and the codes that count as "link left out"."""
    assert list(ct.LINEAGE_PHRASES.items()) == [
        ("lineage_parent_disagree", "holders disagree on the parent"),
        ("lineage_self", "the link names the conversation itself"),
        ("lineage_parent_dead", "the parent's transcript is gone"),
        ("parent_not_in_account", "no row for the parent in that account"),
        ("parent_pair_held", "the parent's own row is held in this plan"),
        ("parent_row_drifted",
         "that account's row for the parent opens another conversation"),
        ("lineage_variant", "not a cleared link variant"),
        ("lineage_no_task", "no usable taskId"),
        ("lineage_task_disagree", "holders disagree on taskId"),
        ("lineage_cycle", "it would close a cycle"),
        ("lineage_source_changed", "the source row changed since the plan"),
        ("own_link_differs", "the row's own link names no usable row"),
        ("lineage_no_app_link", "no app-written link backs it"),
        ("lineage_chain_dropped", "the chain was left out"),
        ("own_chain_differs",
         "the row's own chain disagrees with its holders'"),
        ("existing_cycle", "the account already holds a cycle of links"),
        ("lineage_retargeted", "changed since the plan"),
        ("lineage_dropped", "dropped since the plan"),
        ("lineage_gained", "gained since the plan"),
    ]
    assert ct.LINEAGE_LEFT_OUT == frozenset((
        "lineage_parent_disagree", "lineage_self", "lineage_parent_dead",
        "parent_not_in_account", "parent_pair_held", "parent_row_drifted",
        "lineage_variant", "lineage_no_task", "lineage_task_disagree",
        "lineage_cycle", "lineage_source_changed", "own_link_differs",
        "lineage_no_app_link"))


# ------------------------------------------------ shape checks (Task 5)

def b64post(**extra):
    d = {"sessionId": "local_c", "cliSessionId": "cli_c"}
    d.update(extra)
    return ct.b64(json.dumps(d, separators=(",", ":")).encode())


def lin(**kw):
    d = ct._lineage_empty()
    d.update(kw)
    return d


LINK = {"sessionId": "local_p", "taskId": "task_7f"}
CHAIN = {"firstSessionId": "local_r", "depth": 1}


def jrow(name, post=None, lineage=None, planned=None, store="/s"):
    r = {"name": name, "dest_path": store + "/" + name, "store_path": store,
         "post_b64": post if post is not None else b64post()}
    if lineage is not None:
        r["lineage"] = lineage
    if planned is not None:
        r["lineage_planned"] = planned
    return r


def jman(rows, op_type="converge", **kw):
    d = {"op_type": op_type, "lineage_format": 1, "rows": rows}
    d.update(kw)
    return d


def parent_row():
    return jrow("local_p.json")


def root_row():
    return jrow("local_r.json")


def good_child():
    return jrow("local_c.json",
                b64post(spawnedFrom=LINK, startChain=CHAIN),
                lin(parent_session="cli_p", parent_row="local_p",
                    parent_planned=True, root_session="cli_r",
                    root_row="local_r", root_planned=True,
                    carried_keys=["sessionId", "taskId"]))


def good_op():
    return jman([parent_row(), root_row(), good_child()])


def test_a_well_formed_new_format_manifest_has_no_shape_error():
    assert ct._lineage_shape_error(good_op()) is None
    assert ct._lineage_shape_error(jman([parent_row()])) is None


def test_no_lineage_format_means_no_lineage_check_at_all():
    m = good_op()
    del m["lineage_format"]
    m["rows"][2]["lineage"] = "junk"
    m["lineage_cycles"] = -4
    assert ct._lineage_shape_error(m) is None


@pytest.mark.parametrize("fmt", [2, 0, "1", True, None, 1.0, [1]])
def test_unknown_lineage_format_is_a_shape_error(fmt):
    m = good_op()
    m["lineage_format"] = fmt
    err = ct._lineage_shape_error(m)
    assert err == "its 'lineage_format' is not one this version reads"


def _set(key, value):
    def mut(r):
        r["lineage"][key] = value
    return mut


def _drop(key):
    def mut(r):
        del r["lineage"][key]
    return mut


def _planned_of(change):
    def mut(r):
        r["lineage_planned"] = lin(parent_row="local_p", parent_planned=True)
        change(r["lineage_planned"])
    return mut


DAMAGE = [
    lambda r: r.__setitem__("lineage", "x"),
    lambda r: r.__setitem__("lineage", None),
    lambda r: r.__setitem__("lineage", []),
    _drop("notes"), _drop("parent_row"), _drop("carried_keys"),
    _set("parent_planned", "yes"), _set("root_planned", 1),
    _set("parent_session", 5), _set("root_row", None),
    _set("carried_keys", "sessionId"), _set("dropped_keys", [1]),
    _set("carried_keys", {"a": 1}),
    _set("notes", "n"), _set("notes", ["x"]),
    _set("notes", [{"code": 1, "detail": "d"}]),
    _set("notes", [{"code": "lineage_self"}]),
    _set("notes", [{"code": "lineage_self", "detail": "d"},
                   {"code": "lineage_self", "detail": "e"}]),
    lambda r: r.__setitem__("lineage_planned", []),
    lambda r: r.__setitem__("lineage_planned", "x"),
    _planned_of(lambda p: p.pop("notes")),
    _planned_of(lambda p: p.__setitem__("parent_planned", None)),
]


@pytest.mark.parametrize("mut", DAMAGE)
def test_damaged_lineage_is_a_shape_error_never_an_exception(mut):
    c = good_child()
    mut(c)
    err = ct._lineage_shape_error(jman([parent_row(), root_row(), c]))
    assert isinstance(err, str) and err


@pytest.mark.parametrize("cycles", [-1, True, False, "1", 1.5, None, [1]])
def test_damaged_lineage_cycles_is_a_shape_error(cycles):
    m = good_op()
    m["lineage_cycles"] = cycles
    assert ct._lineage_shape_error(m)


@pytest.mark.parametrize("cycles", [0, 1, 40])
def test_good_lineage_cycles_passes(cycles):
    m = good_op()
    m["lineage_cycles"] = cycles
    assert ct._lineage_shape_error(m) is None


@pytest.mark.parametrize("rows", [5, "x", None, {}, [5], [None], [[]],
                                  [{"post_b64": 3, "lineage": {}}]])
def test_a_wrecked_rows_field_never_raises(rows):
    err = ct._lineage_shape_error(jman(rows))
    assert err is None or isinstance(err, str)
    if not isinstance(rows, list):
        assert isinstance(err, str)


def _with(mut):
    c = good_child()
    mut(c)
    return jman([parent_row(), root_row(), c])


DISAGREE = {
    "empty parent_row, link in the bytes":
        lambda c: c["lineage"].__setitem__("parent_row", ""),
    "another row id":
        lambda c: c["lineage"].__setitem__("parent_row", "local_z"),
    "parent_row but no link in the bytes":
        lambda c: c.__setitem__("post_b64", b64post(startChain=CHAIN)),
    "empty root_row, chain in the bytes":
        lambda c: c["lineage"].__setitem__("root_row", ""),
    "another root id":
        lambda c: c["lineage"].__setitem__("root_row", "local_z"),
    "root_row but no chain in the bytes":
        lambda c: c.__setitem__("post_b64", b64post(spawnedFrom=LINK)),
    "a link that is not an object, with a parent_row":
        lambda c: c.__setitem__("post_b64", b64post(
            spawnedFrom="local_p", startChain=CHAIN)),
    "a chain the lineage cannot read":
        lambda c: c.__setitem__("post_b64", b64post(
            spawnedFrom=LINK, startChain={"depth": 1})),
}


@pytest.mark.parametrize("why", sorted(DISAGREE))
def test_lineage_that_disagrees_with_the_bytes_is_a_shape_error(why):
    err = ct._lineage_shape_error(_with(DISAGREE[why]))
    assert isinstance(err, str) and err, why


def test_a_planned_target_not_in_the_op_is_a_shape_error():
    c = good_child()
    assert ct._lineage_shape_error(jman([root_row(), c]))        # no parent
    assert ct._lineage_shape_error(jman([parent_row(), c]))      # no root
    other = parent_row()
    other["store_path"] = "/t"
    other["dest_path"] = "/t/local_p.json"
    assert ct._lineage_shape_error(jman([other, root_row(), c]))  # other store


NOT_AN_OBJECT = {
    "a null link and chain": dict(spawnedFrom=None, startChain=None),
    "a null link": dict(spawnedFrom=None),
    "a link that is a string": dict(spawnedFrom="local_p"),
    "a chain that is a list": dict(startChain=[1]),
}


@pytest.mark.parametrize("why", sorted(NOT_AN_OBJECT))
def test_a_link_key_that_is_not_an_object_names_no_row_to_the_shape_check(why):
    """R28 with m26: a spawnedFrom or startChain that is not an object names
    no row, to the shape check as to the resolver and the writer. A row
    whose bytes hold one passes with no lineage, and with a lineage that
    names no row; a lineage that names a row those bytes do not carry is
    still a shape error."""
    post = b64post(**NOT_AN_OBJECT[why])
    assert ct._lineage_shape_error(jman([jrow("local_c.json", post)])) is None
    assert ct._lineage_shape_error(
        jman([jrow("local_c.json", post, lin())])) is None
    c = good_child()
    c["post_b64"] = post
    err = ct._lineage_shape_error(jman([parent_row(), root_row(), c]))
    assert isinstance(err, str) and "its bytes do not carry" in err


@pytest.mark.parametrize("chain", [None, [1], "local_r"])
def test_a_chain_that_is_not_an_object_is_no_chain_against_a_root_row(chain):
    """R28, from its review: the chain side on its own. Beside a real link, a
    startChain that is not an object carries no chain, so a lineage naming
    a root row is refused for that, not for the link."""
    c = good_child()
    c["post_b64"] = b64post(spawnedFrom=LINK, startChain=chain)
    err = ct._lineage_shape_error(jman([parent_row(), root_row(), c]))
    assert err == "row 2's lineage names a root_row its bytes do not carry"


def test_a_link_object_without_its_row_id_is_still_a_shape_error():
    """The R28 change leaves an object that lacks its row id a link the
    lineage cannot read."""
    c = good_child()
    c["post_b64"] = b64post(spawnedFrom={"taskId": "task_7f"},
                            startChain=CHAIN)
    err = ct._lineage_shape_error(jman([parent_row(), root_row(), c]))
    assert err == "row 2 carries a spawnedFrom its lineage cannot read"
    plain = jrow("local_c.json", b64post(spawnedFrom={"taskId": "task_7f"}))
    assert ct._lineage_shape_error(jman([plain])) \
        == "row 0 carries a link and has no lineage"


def test_planned_false_is_not_held_to_the_rows():
    c = good_child()
    c["lineage"]["parent_planned"] = False
    c["lineage"]["root_planned"] = False
    assert ct._lineage_shape_error(jman([c])) is None


def test_sync_rows_find_a_planned_target_by_the_directory_of_dest_path():
    def srow(name, store, **kw):
        r = jrow(name, store=store, **kw)
        del r["store_path"]
        return r
    c = srow("local_c.json", "/d", post=b64post(spawnedFrom=LINK),
             lineage=lin(parent_session="cli_p", parent_row="local_p",
                         parent_planned=True))
    ok = jman([srow("local_p.json", "/d"), c], op_type="sync")
    assert ct._lineage_shape_error(ok) is None
    elsewhere = jman([srow("local_p.json", "/e"), c], op_type="sync")
    assert ct._lineage_shape_error(elsewhere)


def test_a_retargeted_gained_or_dropped_row_has_a_plan_that_disagrees_by_design():
    # lineage agrees with the bytes; lineage_planned is the plan's record and
    # says something else entirely. Shape alone is checked for it.
    planned_link = lin(parent_session="cli_z", parent_row="local_z",
                       parent_planned=True, carried_keys=["sessionId"],
                       notes=[{"code": "lineage_retargeted", "detail": "d"}])
    retargeted = good_child()
    retargeted["lineage_planned"] = planned_link
    gained = good_child()
    gained["lineage_planned"] = lin()
    dropped = jrow("local_d.json", b64post(), lin(),
                   planned=lin(parent_session="cli_p", parent_row="local_p",
                               parent_planned=True,
                               carried_keys=["sessionId", "taskId"]))
    m = jman([parent_row(), root_row(), retargeted, dropped])
    assert ct._lineage_shape_error(m) is None
    m = jman([parent_row(), root_row(), gained])
    assert ct._lineage_shape_error(m) is None


def test_a_new_format_linked_row_without_lineage_is_a_shape_error():
    c = good_child()
    del c["lineage"]
    assert ct._lineage_shape_error(jman([parent_row(), root_row(), c]))
    chained = jrow("local_c.json", b64post(startChain=CHAIN))
    assert ct._lineage_shape_error(jman([chained]))


def test_an_unlinked_row_needs_no_lineage_and_a_verbatim_one_never_does():
    assert ct._lineage_shape_error(jman([jrow("local_c.json")])) is None
    c = good_child()
    del c["lineage"]
    assert ct._lineage_shape_error(
        jman([c], op_type="sync", verbatim=True)) is None
    assert ct._lineage_shape_error(jman([c], op_type="sync"))


def test_converge_shape_error_reads_a_damaged_lineage_as_a_damaged_record():
    def full(r):
        r.update({"account": "acct_b", "session": "cli_c",
                  "title": "Northwind"})
        return r
    rows = [full(parent_row()), full(root_row()), full(good_child())]
    m = jman(rows)
    assert ct._converge_shape_error(m) is None
    rows[2]["lineage"] = "junk"
    assert "lineage" in ct._converge_shape_error(m)


def test_sync_shape_error_checks_rows_and_their_strings():
    def srow(**kw):
        r = {"name": "local_a.json", "dest_path": "/d/local_a.json",
             "post_b64": b64post()}
        r.update(kw)
        return r
    assert ct._sync_shape_error({"rows": [srow()]}) is None
    assert ct._sync_shape_error({"rows": []}) is None
    for bad in ({"rows": 3}, {}, {"rows": [4]}, {"rows": [srow(name=1)]},
                {"rows": [srow(dest_path=None)]},
                ):
        assert isinstance(ct._sync_shape_error(bad), str), bad
    # post_b64 is never required, on an old journal or a new one: a stuck op
    # keeps its 'back'.
    for fmt in ({}, {"lineage_format": 1}):
        for pb in (7, None, "!!!"):
            m = dict(fmt, rows=[srow(post_b64=pb)])
            assert ct._sync_shape_error(m) is None, (fmt, pb)
        r = srow()
        del r["post_b64"]
        assert ct._sync_shape_error(dict(fmt, rows=[r])) is None
    m = {"rows": [srow(lineage="junk")], "lineage_format": 1}
    assert "lineage" in ct._sync_shape_error(m)


def test_unreadable_bytes_are_not_a_shape_error_but_a_readable_disagreement_is():
    for pb in ("!!!", 7, None):
        c = good_child()
        c["post_b64"] = pb
        assert ct._lineage_shape_error(jman([parent_row(), root_row(), c]))             is None
    bare = jrow("local_c.json", "!!!")
    assert ct._lineage_shape_error(jman([bare])) is None
    # the lineage record's own shape is still checked on such a row
    c = good_child()
    c["post_b64"] = "!!!"
    c["lineage"]["notes"] = "n"
    assert ct._lineage_shape_error(jman([parent_row(), root_row(), c]))
