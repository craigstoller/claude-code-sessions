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
    assert len(ct.LINEAGE_LEFT_OUT) == 11


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
        ("lineage_chain_dropped", "the chain was left out"),
        ("existing_cycle", "the account already holds a cycle of links"),
        ("lineage_retargeted", "changed since the plan"),
        ("lineage_dropped", "dropped since the plan"),
        ("lineage_gained", "gained since the plan"),
    ]
    assert ct.LINEAGE_LEFT_OUT == frozenset((
        "lineage_parent_disagree", "lineage_self", "lineage_parent_dead",
        "parent_not_in_account", "parent_pair_held", "parent_row_drifted",
        "lineage_variant", "lineage_no_task", "lineage_task_disagree",
        "lineage_cycle", "lineage_source_changed"))
