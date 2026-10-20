"""The window's saved sync destination (docs/internals.md, Amendment 2026-10-20).

A destination the window saved before Claude Desktop 2.42304.2 moved its user
data names a folder under the old root, and every plan refused it with a bare
"Refused". The ruling: no write target changes without a pick; a saved value
proven gone is set aside and the window asks; a saved store whose account is
now signed in is named; the candidate with the saved ids is marked.

The re-root ruling of the same day (docs/internals.md, Amendment 2026-10-20
(re-root)): before a saved value proven gone is set aside, it is re-rooted
through the journal's package pairs, and planned into when discovery lists
that store; the preference file changes on a pick, or after a successful
Apply into a re-rooted saved destination, to that same store's new path.

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
from test_journal_reroot import (DST_ACCT, DST_ORG, SRC_ACCT, SRC_ORG,  # noqa: E402
                                 packaged, relocate)

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
                 "signed_in": False, "rerooted": ""}


# ------------------------------------- re-rooting through the package pairs

OTHER_DST_ORG = "eeeeeeee-0000-0000-0000-000000000005"


def _identity(env, oauth=SRC_ACCT, config=None, store=None):
    """~/.claude.json naming OAUTH, and the moved root's config.json naming
    CONFIG when given - two different accounts make the files disagree."""
    with open(os.path.join(env.home, ".claude.json"), "w", encoding="utf-8") as fh:
        json.dump({"oauthAccount": {"accountUuid": oauth,
                                    "organizationUuid": SRC_ORG,
                                    "emailAddress": "me@example.com"}}, fh)
    if config:
        with open(os.path.join(os.path.dirname(store), "config.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"lastKnownAccountUuid": config}, fh)


@pytest.fixture
def moved(mkenv):
    """The packaged layout with the signed-in account's store and two of
    another account, then the app's move. (env, old store, moved store)."""
    def make(tmp_path, copy=False, drop_old=False, stores=None):
        env = mkenv(tmp_path)
        la, claude = packaged(env, tmp_path)
        old = os.path.join(claude, "claude-code-sessions")
        for a, o in stores or [(SRC_ACCT, SRC_ORG), (DST_ACCT, DST_ORG),
                               (DST_ACCT, OTHER_DST_ORG)]:
            os.makedirs(os.path.join(old, a, o))
        new = relocate(env, la, claude, copy=copy, drop_old=drop_old)
        _identity(env)
        return env, old, new
    return make


def _key(p):
    return gui._dest_key(p) if p else ""


def test_a_pre_move_saved_store_re_roots_onto_the_listed_moved_store(moved,
                                                                      tmp_path):
    env, old, new = moved(tmp_path)
    got = gui._reroot_destination(env, os.path.join(old, DST_ACCT, DST_ORG))
    assert _key(got) == _key(os.path.join(new, DST_ACCT, DST_ORG))
    listed = [p for _a, _o, p in ct._account_dirs(env)]
    assert got in listed                    # the listed store's own spelling


def test_the_re_root_holds_after_the_vacated_folder_is_deleted(moved, tmp_path):
    env, old, new = moved(tmp_path, drop_old=True)
    assert env.superseded_stores == []
    got = gui._reroot_destination(env, os.path.join(old, DST_ACCT, DST_ORG))
    assert _key(got) == _key(os.path.join(new, DST_ACCT, DST_ORG))


def test_a_leftover_old_copy_changes_nothing_the_moved_root_wins(moved, tmp_path):
    env, old, new = moved(tmp_path, copy=True)
    assert os.path.isdir(os.path.join(old, DST_ACCT, DST_ORG))
    got = gui._reroot_destination(env, os.path.join(old, DST_ACCT, DST_ORG))
    assert _key(got) == _key(os.path.join(new, DST_ACCT, DST_ORG))


def test_a_store_that_did_not_move_is_not_re_rooted(moved, tmp_path):
    env, old, new = moved(tmp_path)
    missing = os.path.join(old, DST_ACCT, "ffffffff-0000-0000-0000-000000000006")
    assert gui._reroot_destination(env, missing) == ""


def test_a_classic_root_path_is_not_re_rooted(moved, tmp_path):
    env, old, new = moved(tmp_path)
    classic = str(tmp_path / "Roaming" / "Claude" / "claude-code-sessions"
                  / DST_ACCT / DST_ORG)
    assert gui._reroot_destination(env, classic) == ""


def test_the_moved_store_itself_is_not_re_rooted(moved, tmp_path):
    env, old, new = moved(tmp_path)
    assert gui._reroot_destination(env, os.path.join(new, DST_ACCT, DST_ORG)) == ""


def test_no_pairs_no_re_root(moved, tmp_path):
    env, old, new = moved(tmp_path)
    env.store_moves = []
    assert gui._reroot_destination(env, os.path.join(old, DST_ACCT, DST_ORG)) == ""


def test_a_listing_that_cannot_be_read_does_not_re_root(moved, tmp_path):
    """A layout with errors makes discovery an error, every time."""
    env, old, new = moved(tmp_path)
    env.layout_errors = ["a store folder: Access is denied"]
    assert gui._reroot_destination(env, os.path.join(old, DST_ACCT, DST_ORG)) == ""


def test_upper_case_ids_re_root_to_the_listed_spelling(moved, tmp_path):
    env, old, new = moved(tmp_path)
    got = gui._reroot_destination(
        env, os.path.join(old, DST_ACCT.upper(), DST_ORG.upper()))
    assert got in [p for _a, _o, p in ct._account_dirs(env)]
    assert _key(got) == _key(os.path.join(new, DST_ACCT, DST_ORG))


@pytest.mark.skipif(sys.platform != "win32", reason="drive letters are Windows'")
def test_a_lower_case_drive_with_forward_slashes_re_roots(moved, tmp_path):
    env, old, new = moved(tmp_path)
    saved = os.path.join(old, DST_ACCT, DST_ORG)
    saved = (saved[0].lower() + saved[1:]).replace("\\", "/")
    got = gui._reroot_destination(env, saved)
    assert _key(got) == _key(os.path.join(new, DST_ACCT, DST_ORG))


@pytest.mark.parametrize("value", ["", "   ", "cccccccc/dddddddd", "zzzz"])
def test_a_value_that_is_not_an_absolute_path_is_never_re_rooted(moved, tmp_path,
                                                                 value):
    env, old, new = moved(tmp_path)
    assert gui._reroot_destination(env, value) == ""


def test_findings_carry_the_re_rooted_store(moved, tmp_path):
    env, old, new = moved(tmp_path)
    f = gui._destination_findings(env, os.path.join(old, DST_ACCT, DST_ORG))
    assert f["gone"] is True and f["signed_in"] is False
    assert _key(f["rerooted"]) == _key(os.path.join(new, DST_ACCT, DST_ORG))


def test_findings_carry_it_for_a_signed_in_store_too(moved, tmp_path):
    """For the sentences: the window names it at its new place, and never
    plans into it."""
    env, old, new = moved(tmp_path)
    f = gui._destination_findings(env, os.path.join(old, SRC_ACCT, SRC_ORG))
    assert f["gone"] is True and f["signed_in"] is True
    assert _key(f["rerooted"]) == _key(os.path.join(new, SRC_ACCT, SRC_ORG))


def test_findings_look_for_no_re_root_when_the_store_is_there(moved, tmp_path):
    env, old, new = moved(tmp_path, copy=False)
    f = gui._destination_findings(env, os.path.join(new, DST_ACCT, DST_ORG))
    assert f["gone"] is False and f["rerooted"] == ""


def test_a_held_answer_naming_the_saved_account_counts_as_signed_in(moved,
                                                                    tmp_path):
    """The files disagree, so live_account makes no claim; the user's answer
    names the saved account, and plan_sync would refuse that store."""
    env, old, new = moved(tmp_path)
    _identity(env, oauth=SRC_ACCT, config=DST_ACCT, store=new)
    assert ct._identity_disagreement(env)
    saved = os.path.join(old, DST_ACCT, DST_ORG)
    assert gui._destination_findings(env, saved)["signed_in"] is False
    held = os.path.join(new, DST_ACCT, DST_ORG)
    assert gui._destination_findings(env, saved, live=held)["signed_in"] is True
    assert gui._destination_findings(
        env, saved, live=os.path.join(new, SRC_ACCT, SRC_ORG))["signed_in"] is False


def test_a_held_answer_that_cannot_be_resolved_makes_no_claim(moved, tmp_path):
    env, old, new = moved(tmp_path)
    _identity(env, oauth=SRC_ACCT, config=DST_ACCT, store=new)
    saved = os.path.join(old, DST_ACCT, DST_ORG)
    f = gui._destination_findings(env, saved, live="no-such-store-anywhere")
    assert f["signed_in"] is False


# ----------------------------------------------- the save after an Apply

def test_save_pref_reports_whether_it_wrote(tmp_path, monkeypatch):
    monkeypatch.setattr(gui, "PREF_PATH", str(tmp_path / "pref.json"))
    assert gui.save_pref("x") is True
    assert gui.load_pref() == "x"
    monkeypatch.setattr(gui, "PREF_PATH", str(tmp_path / "missing" / "pref.json"))
    assert gui.save_pref("y") is False


def _pref(monkeypatch, tmp_path, value):
    path = tmp_path / "pref.json"
    monkeypatch.setattr(gui, "PREF_PATH", str(path))
    if value is not None:
        path.write_text(json.dumps({"to": value}), encoding="utf-8")
    return path


def test_the_save_writes_the_new_path_over_the_value_it_re_rooted(tmp_path,
                                                                  monkeypatch):
    path = _pref(monkeypatch, tmp_path, "OLD")
    assert gui._save_after_reroot("OLD", "NEW") == ("saved", "NEW")
    assert json.loads(path.read_text(encoding="utf-8")) == {"to": "NEW"}


def test_a_pick_made_meanwhile_elsewhere_is_left_and_adopted(tmp_path, monkeypatch):
    path = _pref(monkeypatch, tmp_path, "PICKED")
    assert gui._save_after_reroot("OLD", "NEW") == ("elsewhere", "PICKED")
    assert json.loads(path.read_text(encoding="utf-8")) == {"to": "PICKED"}


def test_another_window_that_saved_the_same_new_path_counts_as_saved(tmp_path,
                                                                     monkeypatch):
    new = str(tmp_path / "Claude-Data" / "claude_pkgx" / "s" / DST_ACCT / DST_ORG)
    _pref(monkeypatch, tmp_path, new.upper().replace("\\", "/"))
    assert gui._save_after_reroot("OLD", new)[0] == "saved"


def test_a_preference_file_that_cannot_be_read_is_a_failed_save(tmp_path,
                                                                monkeypatch):
    path = _pref(monkeypatch, tmp_path, None)           # gone meanwhile
    assert gui._save_after_reroot("OLD", "NEW") == ("failed", None)
    assert not path.exists()
    path.write_text("{not json", encoding="utf-8")
    assert gui._save_after_reroot("OLD", "NEW") == ("failed", None)


def test_a_write_that_does_not_take_is_a_failed_save(tmp_path, monkeypatch):
    _pref(monkeypatch, tmp_path, "OLD")
    monkeypatch.setattr(gui, "save_pref", lambda _v: False)
    assert gui._save_after_reroot("OLD", "NEW") == ("failed", None)


# ------------------------------------------------------- the sentences

def test_the_location_sentence_names_both_paths_and_claims_no_plan():
    s = gui.DEST_MOVED.format("OLDPATH", "NEWPATH")
    assert "OLDPATH" in s and "NEWPATH" in s
    assert "plan" not in s.lower() and "apply" not in s.lower()


def test_the_no_rows_sentence_offers_only_what_the_user_can_do_there():
    s = gui.DEST_SAVE_BY_PICK
    assert "Change destination" in s
    assert "Applying saves" not in s


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
