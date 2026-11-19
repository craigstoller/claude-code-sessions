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
        assert set(res) == {"link", "chain", "lineage", "edges"}
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
