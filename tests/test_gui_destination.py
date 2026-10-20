"""The window's saved sync destination (docs/internals.md, Amendment 2026-10-20).

A destination the window saved before Claude Desktop 2.42304.2 moved its user
data names a folder under the old root, and every plan refused it with a bare
"Refused". The ruling: no write target changes without a pick; a saved value
proven gone is set aside and the window asks; a saved store whose account is
now signed in is named; the candidate with the saved ids is marked.

These are the pure helpers behind that, importable without tkinter, plus the
core sentences and the candidate-line format the window keys on. Fake ids only.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import claude_code_sessions as ct  # noqa: E402
import claude_code_sessions_gui as gui  # noqa: E402

ACCT = "aaaa1111" + "0" * 24
ORG = "cccc3333" + "0" * 24
OTHER_ORG = "dddd4444" + "0" * 24


# ------------------------------------------------ the candidate line's path

@pytest.mark.parametrize("rows", [0, 1, 286, None])
def test_candidate_path_reads_every_label_form(tmp_path, rows):
    """The label is padded to 17 characters, but "(row count unreadable)" is
    22 and "(286 rows)" holds a space - so the path is read after the label,
    never by column."""
    path = str(tmp_path / "a store root" / ACCT / ORG)
    line = ct._candidate_line(ACCT, ORG, path, rows)
    assert gui._candidate_path(line) == path


def test_candidate_path_of_a_parsed_listing(tmp_path):
    a = tmp_path / "one"
    b = tmp_path / "two words"
    a.mkdir()
    b.mkdir()
    listing = ct._candidate_listing([(ACCT, ORG, str(a)), (ACCT, OTHER_ORG, str(b))],
                                    live_org=ORG)
    cands = gui.SyncApp._candidates(listing)
    assert [gui._candidate_path(line) for _p, line, _n in cands] == [str(a), str(b)]


def test_candidate_path_of_anything_else_is_empty():
    assert gui._candidate_path("") == ""
    assert gui._candidate_path("A store with no listing rows holds no sessions yet") == ""
    assert gui._candidate_path("        ^ shares your signed-in org - the likelier "
                               "scaffolding, see below") == ""


# ------------------------------------------------------ the saved ids

def test_saved_ids_are_the_last_two_folders_lower_cased(tmp_path):
    p = str(tmp_path / "claude-code-sessions" / ACCT.upper() / ORG)
    assert gui._saved_ids(p) == (ACCT, ORG)
    assert gui._saved_ids(p + os.sep) == (ACCT, ORG)
    assert gui._saved_ids(p.replace("\\", "/")) == (ACCT, ORG)


@pytest.mark.parametrize("value", ["", "   ", "aaaa1111/cccc3333", "aaaa1111",
                                   "relative" + os.sep + ACCT + os.sep + ORG])
def test_saved_ids_need_an_absolute_path(value):
    assert gui._saved_ids(value) is None


def test_saved_ids_need_id_shaped_folders(tmp_path):
    assert gui._saved_ids(str(tmp_path / "Documents" / "notes")) is None


# ------------------------------------------------------- proven gone

def test_an_existing_folder_is_not_gone(tmp_path):
    d = tmp_path / "store" / ACCT / ORG
    d.mkdir(parents=True)
    assert gui._destination_gone(str(d), []) is False


def test_a_missing_folder_is_gone(tmp_path):
    assert gui._destination_gone(str(tmp_path / "gone" / ACCT / ORG), []) is True


def test_a_path_under_a_file_is_gone(tmp_path):
    f = tmp_path / "file"
    f.write_text("x")
    assert gui._destination_gone(str(f / ACCT / ORG), []) is True


def test_a_path_under_a_superseded_store_is_gone_even_if_it_is_still_there(tmp_path):
    """Ruling 1: a moved package's old root is never read - a leftover copy
    included - so a saved path under it names no store this version uses."""
    old = tmp_path / "Packages" / "Claude_pkgx" / "LocalCache" / "Roaming" / "Claude" \
        / "claude-code-sessions"
    left = old / ACCT / ORG
    left.mkdir(parents=True)
    assert gui._destination_gone(str(left), [str(old)]) is True
    assert gui._destination_gone(str(old), [str(old)]) is True
    # another casing and the other separator, on either side
    variant = str(left).upper().replace("\\", "/")
    assert gui._destination_gone(variant, [str(old)]) is True
    assert gui._destination_gone(str(left) + os.sep, [str(old).lower()]) is True


def test_a_sibling_of_a_superseded_store_is_not_under_it(tmp_path):
    old = tmp_path / "Claude" / "claude-code-sessions"
    sibling = tmp_path / "Claude" / "claude-code-sessions-bak" / ACCT / ORG
    sibling.mkdir(parents=True)
    assert gui._destination_gone(str(sibling), [str(old)]) is False


@pytest.mark.parametrize("value", ["", "   ", "aaaa1111/cccc3333", "zzzz"])
def test_a_value_that_is_not_an_absolute_path_is_never_gone(value):
    assert gui._destination_gone(value, []) is False


def test_a_path_that_cannot_be_looked_at_is_not_gone(tmp_path):
    """Couldn't look is never "nothing there"."""
    def denied(_p):
        raise PermissionError("denied")
    p = str(tmp_path / "x" / ACCT / ORG)
    assert gui._destination_gone(p, [], stat=denied) is False


# ------------------------------------------------------------- the mark

def test_the_one_candidate_with_the_saved_ids_is_marked(tmp_path):
    paths = [str(tmp_path / "r" / ACCT / OTHER_ORG), str(tmp_path / "r" / ACCT / ORG)]
    assert gui._mark_index(paths, (ACCT, ORG)) == 1


def test_upper_case_ids_on_disk_still_match(tmp_path):
    paths = [str(tmp_path / "r" / ACCT.upper() / ORG.upper())]
    assert gui._mark_index(paths, (ACCT, ORG)) == 0


def test_no_match_or_two_matches_marks_nothing(tmp_path):
    one = str(tmp_path / "moved" / ACCT / ORG)
    two = str(tmp_path / "classic" / ACCT / ORG)
    assert gui._mark_index([one, two], (ACCT, ORG)) is None
    assert gui._mark_index([one], (ACCT, OTHER_ORG)) is None
    assert gui._mark_index([one], None) is None
    assert gui._mark_index(["", one], (ACCT, ORG)) == 1


# ------------------------------------------------- the findings, on a real Env

def test_findings_for_a_dormant_store_that_is_there(two_account_env, tmp_path):
    env, src, dst = two_account_env(tmp_path)
    f = gui._destination_findings(env, dst)
    assert f["gone"] is False and f["signed_in"] is False
    assert f["ids"] == tuple(os.path.normcase(dst).split(os.sep)[-2:])


def test_findings_for_the_signed_in_store(two_account_env, tmp_path):
    env, src, dst = two_account_env(tmp_path)
    f = gui._destination_findings(env, src)
    assert f["gone"] is False and f["signed_in"] is True


def test_findings_for_a_moved_store_whose_account_is_signed_in(two_account_env,
                                                               tmp_path):
    """The move and an account switch together: the saved folder is gone, and
    its account is the signed-in one - sync could never write it anyway."""
    env, src, dst = two_account_env(tmp_path)
    acct, org = os.path.basename(os.path.dirname(src)), os.path.basename(src)
    old = str(tmp_path / "old-root" / "claude-code-sessions" / acct / org)
    f = gui._destination_findings(env, old)
    assert f["gone"] is True and f["signed_in"] is True


def test_findings_for_a_gone_store_of_an_account_not_signed_in(two_account_env,
                                                               tmp_path):
    env, src, dst = two_account_env(tmp_path)
    old = str(tmp_path / "old-root" / "claude-code-sessions" / ACCT / ORG)
    f = gui._destination_findings(env, old)
    assert f["gone"] is True and f["signed_in"] is False


def test_findings_make_no_signed_in_claim_when_the_files_disagree(two_account_env,
                                                                  tmp_path):
    env, src, dst = two_account_env(tmp_path)
    with open(os.path.join(os.path.dirname(env.store_candidates[0]), "config.json"),
              "w", encoding="utf-8") as fh:
        json.dump({"lastKnownAccountUuid": "cccccccc-0000-0000-0000-000000000003"}, fh)
    assert ct._identity_disagreement(env)
    assert gui._destination_findings(env, src)["signed_in"] is False


def test_findings_make_no_signed_in_claim_when_discovery_fails(two_account_env,
                                                               tmp_path):
    env, src, dst = two_account_env(tmp_path)
    env.layout_errors = ["a store folder: Access is denied"]
    with pytest.raises(ct.LayoutError):
        ct._account_dirs(env)
    assert gui._destination_findings(env, src)["signed_in"] is False


def test_findings_of_a_value_that_is_not_a_path(two_account_env, tmp_path):
    env, src, dst = two_account_env(tmp_path)
    f = gui._destination_findings(env, "cccccccc/dddddddd")
    assert f == {"value": "cccccccc/dddddddd", "gone": False, "ids": None,
                 "signed_in": False}


# ------------------------------------- what the window keys on, in the core

def test_the_refusal_sentences_the_window_keys_on(two_account_env, tmp_path):
    """The window routes on these. A rewording must fail here, in pytest,
    not silently restore the bare "Refused"."""
    env, src, dst = two_account_env(tmp_path)
    with pytest.raises(ct.Refusal) as exc:
        ct.resolve_sync_endpoints(env, to=src)       # the signed-in store
    assert str(exc.value) == "--to {0!r} matched no other account store".format(src)
    assert gui.NO_MATCH in str(exc.value)

    os.makedirs(os.path.join(env.store_candidates[0],
                             "cccccccc-0000-0000-0000-000000000003",
                             "eeeeeeee-0000-0000-0000-000000000005"))
    with pytest.raises(ct.Refusal) as exc:
        ct.resolve_sync_endpoints(env)
    assert str(exc.value).startswith(gui.MORE_THAN_ONE)


def test_the_no_other_store_sentence(mkenv, tmp_path):
    env = mkenv(tmp_path)
    acct, org = "aaaaaaaa-0000-0000-0000-000000000001", "bbbbbbbb-0000-0000-0000-000000000002"
    os.makedirs(os.path.join(env.store_candidates[0], acct, org))
    with open(os.path.join(env.home, ".claude.json"), "w", encoding="utf-8") as fh:
        json.dump({"oauthAccount": {"accountUuid": acct, "organizationUuid": org}}, fh)
    with pytest.raises(ct.Refusal) as exc:
        ct.resolve_sync_endpoints(env)
    assert str(exc.value) == "no other account store on this machine - nothing to sync into"
    assert gui.NO_MATCH not in str(exc.value)


def test_the_candidate_line_format_the_picker_parses(tmp_path):
    path = str(tmp_path / "s")
    assert ct._candidate_line(ACCT, ORG, path, 3) == \
        "   aaaa1111/cccc3333   (3 rows)            " + path
    assert ct._candidate_line(ACCT, ORG, path, None) == \
        "   aaaa1111/cccc3333   (row count unreadable)   " + path
    line = ct._candidate_line(ACCT, ORG, path, 0)
    assert gui.SyncApp._candidates(line)[0][0] == "aaaa1111/cccc3333"
    assert gui._candidate_path(line) == path
