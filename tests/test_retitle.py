"""Tests for `ccs retitle`.

Ports the August scaffold's lessons and the design review's findings as tests,
per docs/specs/2026-08-26-retitle-design.md's Testing section. Every title is
from the fake cast (ACME-REVIEW, Northwind, "Quarterly board report
finalization") - never a real one.
"""
import json
import os

import pytest

import claude_code_sessions as ct


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
