"""Split rows: one conversation, whose rows in different accounts open
different copies of its transcript.

Each account's listing row names a project folder through its `cwd`, and the
app opens the copy of the transcript that sits in that folder - even a stale
one. When a conversation's transcript is in two folders and the accounts' rows
name different ones, each sidebar can show a different history. `alignment`
counted such a conversation as complete, because every account had a row for
its id, and `doctor` never looked.

What these pin: `doctor` names each such conversation, says which copy each row
opens, and classifies each pair of copies with the duplicated-transcript
comparison (strict prefix, identical, diverged); `alignment` counts them from
file sizes alone. The documented worktree placement - `cwd` names the main
repository and the transcript sits under one of its worktree folders - is the
app working, not a split. Fake cast only: ACME-REVIEW, Northwind, the A/O/S
ids - never a real id, title or path.
"""
import json
import os
import types

import pytest

import claude_code_sessions as ct
from test_converge import A1, A2, A3, O1, O2, O3, S1, S2, t_entries

MAIN = "C:\\Users\\u\\Projects\\Northwind"
WT = "C:\\Users\\u\\Projects\\Northwind-wt"
F_MAIN = "C--Users-u-Projects-Northwind"
F_WT = "C--Users-u-Projects-Northwind-wt"
F_WT_OF_MAIN = "C--Users-u-Projects-Northwind--claude-worktrees-wt1"
TITLE = "ACME-REVIEW handoff"


@pytest.fixture(autouse=True)
def _reset_anonymize():
    ct._ANONYMIZE = False
    ct._ANON_CACHE.clear()
    yield
    ct._ANONYMIZE = False
    ct._ANON_CACHE.clear()


def later_turns():
    return [
        {"timestamp": "2026-08-01T00:20:00.000Z", "type": "user",
         "message": {"role": "user", "content": "and the board deck?"}},
        {"timestamp": "2026-08-01T00:30:00.000Z", "type": "assistant",
         "message": {"role": "assistant", "model": "claude-opus-5",
                     "content": [{"type": "text", "text": "drafted"}]}},
    ]


def diverging_turn():
    return [{"timestamp": "2026-08-01T00:20:00.000Z", "type": "user",
             "message": {"role": "user", "content": "something else"}}]


def row(sid, cwd, title=TITLE, activity=9, **extra):
    d = {"sessionId": "local-x", "cliSessionId": sid, "title": title,
         "cwd": cwd, "originCwd": cwd, "lastActivityAt": activity,
         "isArchived": False}
    d.update(extra)
    return d


def ns(**kw):
    d = {"json": False, "verbose": False, "anonymize": False}
    d.update(kw)
    return types.SimpleNamespace(**d)


def only_split(rep):
    assert len(rep["split_rows"]) == 1, rep["split_rows"]
    return rep["split_rows"][0]


def copy_of(entry, account):
    """The folder of the copy ACCOUNT's row opens, or None."""
    (r,) = [r for r in entry["rows"] if r["account"] == account]
    return None if r["copy"] is None else entry["copies"][r["copy"]]["folder"]


def strict_prefix_store(env, write_transcript, write_row):
    """S1 in two folders: the main one holds the longer copy, the -wt one a
    strict byte-prefix of it. A1's row names main, A2's names -wt."""
    write_transcript(env, F_MAIN, S1, t_entries(cwd=MAIN) + later_turns())
    write_transcript(env, F_WT, S1, t_entries(cwd=MAIN))
    write_row(env, 0, A1, O1, "local_1", row(S1, MAIN, activity=20))
    write_row(env, 0, A2, O2, "local_1", row(S1, WT, activity=10))


# --------------------------------------------------------------- doctor

def test_rows_opening_a_prefix_copy_are_a_split_and_doctor_exits_1(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    strict_prefix_store(env, write_transcript, write_row)
    rep = ct.gather_doctor(env)
    s = only_split(rep)
    assert s["session_id"] == S1 and s["title"] == TITLE
    assert s["relation"] == "strict_prefix"
    assert copy_of(s, A1) == F_MAIN and copy_of(s, A2) == F_WT
    assert s["copies"][s["live"]]["folder"] == F_MAIN
    assert [p["relation"] for p in s["pairs"]] == ["strict_prefix"]
    assert all(r["via"] == "cwd" for r in s["rows"])
    assert s["counted"] is True
    assert rep["exit_code"] == 1


def test_identical_copies_are_listed_but_do_not_move_the_exit_code(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    write_transcript(env, F_MAIN, S1, t_entries(cwd=MAIN))
    write_transcript(env, F_WT, S1, t_entries(cwd=MAIN))
    write_row(env, 0, A1, O1, "local_1", row(S1, MAIN))
    write_row(env, 0, A2, O2, "local_1", row(S1, WT))
    rep = ct.gather_doctor(env)
    s = only_split(rep)
    assert s["relation"] == "identical" and s["live"] is None
    assert s["counted"] is False
    assert rep["exit_code"] == 0


def test_diverged_copies_are_a_split_with_no_live_copy(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    write_transcript(env, F_MAIN, S1, t_entries(cwd=MAIN) + later_turns())
    write_transcript(env, F_WT, S1, t_entries(cwd=MAIN) + diverging_turn())
    write_row(env, 0, A1, O1, "local_1", row(S1, MAIN))
    write_row(env, 0, A2, O2, "local_1", row(S1, WT))
    rep = ct.gather_doctor(env)
    s = only_split(rep)
    assert s["relation"] == "diverged" and s["live"] is None
    assert s["counted"] is True and rep["exit_code"] == 1


def test_three_accounts_three_folders_classify_every_pair(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    third = "C:\\Users\\u\\Projects\\Northwind-b"
    write_transcript(env, F_MAIN, S1, t_entries(cwd=MAIN) + later_turns())
    write_transcript(env, F_WT, S1, t_entries(cwd=MAIN))
    write_transcript(env, "C--Users-u-Projects-Northwind-b", S1,
                     t_entries(cwd=MAIN) + diverging_turn())
    write_row(env, 0, A1, O1, "local_1", row(S1, MAIN))
    write_row(env, 0, A2, O2, "local_1", row(S1, WT))
    write_row(env, 0, A3, O3, "local_1", row(S1, third))
    s = only_split(ct.gather_doctor(env))
    assert len(s["copies"]) == 3 and len(s["pairs"]) == 3
    rel = sorted(p["relation"] for p in s["pairs"])
    # main vs wt: prefix; b vs wt: prefix (t_entries is a prefix of both
    # longer copies); main vs b: diverged. The worst decides the whole.
    assert rel == ["diverged", "strict_prefix", "strict_prefix"]
    assert s["relation"] == "diverged"


def test_every_row_naming_one_folder_is_not_a_split(
        mkenv, tmp_path, write_transcript, write_row):
    """A stale copy no row names is converge's duplicate-transcript case,
    not a split: every account opens the same copy."""
    env = mkenv(tmp_path)
    strict_prefix_store(env, write_transcript, write_row)
    write_row(env, 0, A2, O2, "local_1", row(S1, MAIN))
    rep = ct.gather_doctor(env)
    assert rep["split_rows"] == [] and rep["exit_code"] == 0


def test_a_single_copy_is_never_a_split(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    write_transcript(env, F_MAIN, S1, t_entries(cwd=MAIN))
    write_row(env, 0, A1, O1, "local_1", row(S1, MAIN))
    write_row(env, 0, A2, O2, "local_1", row(S1, WT))
    assert ct.gather_doctor(env)["split_rows"] == []


def test_the_worktree_placement_is_the_app_working_not_a_split(
        mkenv, tmp_path, write_transcript, write_row):
    """docs/internals.md, 'Worktree transcript placement': a session started
    in a worktree records the main repository as `cwd` while its transcript
    sits under the worktree's own folder. Every row names main, main holds
    no copy, and the one copy under its worktree folders is what opens - so
    an unrelated copy elsewhere does not make a split."""
    env = mkenv(tmp_path)
    write_transcript(env, F_WT_OF_MAIN, S1, t_entries(cwd=MAIN) + later_turns())
    write_transcript(env, "C--Users-u-Projects-Elsewhere", S1, t_entries(cwd=MAIN))
    write_row(env, 0, A1, O1, "local_1", row(S1, MAIN))
    write_row(env, 0, A2, O2, "local_1", row(S1, MAIN))
    assert ct.gather_doctor(env)["split_rows"] == []


def test_a_worktree_placed_row_against_a_row_naming_a_stale_copy_is_a_split(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    write_transcript(env, F_WT_OF_MAIN, S1, t_entries(cwd=MAIN) + later_turns())
    write_transcript(env, F_WT, S1, t_entries(cwd=MAIN))
    write_row(env, 0, A1, O1, "local_1", row(S1, MAIN))
    write_row(env, 0, A2, O2, "local_1", row(S1, WT))
    s = only_split(ct.gather_doctor(env))
    assert s["relation"] == "strict_prefix"
    (r1,) = [r for r in s["rows"] if r["account"] == A1]
    assert r1["via"] == "worktree" and copy_of(s, A1) == F_WT_OF_MAIN
    assert s["copies"][s["live"]]["folder"] == F_WT_OF_MAIN


def test_the_folder_a_cwd_names_wins_over_its_worktree_folders(
        mkenv, tmp_path, write_transcript, write_row):
    """Measured 2026-11 on one conversation: a row whose folder holds a
    stale copy opens that copy, although a longer one sits under a worktree
    of that folder."""
    env = mkenv(tmp_path)
    write_transcript(env, F_MAIN, S1, t_entries(cwd=MAIN))
    write_transcript(env, F_WT_OF_MAIN, S1, t_entries(cwd=MAIN) + later_turns())
    nested = MAIN + "\\.claude\\worktrees\\wt1"
    write_row(env, 0, A1, O1, "local_1", row(S1, MAIN))
    write_row(env, 0, A2, O2, "local_1", row(S1, nested))
    s = only_split(ct.gather_doctor(env))
    assert copy_of(s, A1) == F_MAIN and copy_of(s, A2) == F_WT_OF_MAIN
    assert s["copies"][s["live"]]["folder"] == F_WT_OF_MAIN
    assert all(r["via"] == "cwd" for r in s["rows"])


def test_a_row_whose_folder_holds_no_copy_among_several_is_unplaced(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    write_transcript(env, "C--Users-u-Projects-Elsewhere", S1, t_entries(cwd=MAIN))
    write_transcript(env, "C--Users-u-Projects-Other", S1,
                     t_entries(cwd=MAIN) + later_turns())
    write_row(env, 0, A1, O1, "local_1", row(S1, MAIN))
    rep = ct.gather_doctor(env)
    s = only_split(rep)
    assert s["relation"] == "unplaced"
    (r1,) = s["rows"]
    assert r1["copy"] is None and r1["via"] == "none"
    assert s["counted"] is False and rep["exit_code"] == 0


def test_two_worktree_copies_under_the_named_folder_leave_the_row_unplaced(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    write_transcript(env, F_WT_OF_MAIN, S1, t_entries(cwd=MAIN))
    write_transcript(env, F_MAIN + "--claude-worktrees-wt2", S1,
                     t_entries(cwd=MAIN) + later_turns())
    write_row(env, 0, A1, O1, "local_1", row(S1, MAIN))
    s = only_split(ct.gather_doctor(env))
    (r1,) = s["rows"]
    assert r1["via"] == "none" and r1["worktree_copies"] == 2


def test_a_sibling_folder_sharing_a_prefix_is_not_a_worktree(
        mkenv, tmp_path, write_transcript, write_row):
    """'Northwind-wt' starts with 'Northwind' but is not one of its
    worktrees; only '<folder>--claude-worktrees-<name>' is."""
    env = mkenv(tmp_path)
    write_transcript(env, F_WT, S1, t_entries(cwd=MAIN))
    write_transcript(env, "C--Users-u-Projects-Other", S1,
                     t_entries(cwd=MAIN) + later_turns())
    write_row(env, 0, A1, O1, "local_1", row(S1, MAIN))
    (r1,) = only_split(ct.gather_doctor(env))["rows"]
    assert r1["via"] == "none" and r1["worktree_copies"] == 0


def test_a_legacy_encoded_folder_is_the_one_a_cwd_names(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    cwd = "C:\\Users\\u\\Projects\\_Tools\\Northwind"
    legacy = ct.encode(cwd, ct.SCHEME_LEGACY)
    assert legacy != ct.encode(cwd, ct.SCHEME_CURRENT)
    write_transcript(env, legacy, S1, t_entries(cwd=cwd))
    write_transcript(env, F_WT, S1, t_entries(cwd=cwd) + later_turns())
    write_row(env, 0, A1, O1, "local_1", row(S1, cwd))
    write_row(env, 0, A2, O2, "local_1", row(S1, WT))
    s = only_split(ct.gather_doctor(env))
    assert copy_of(s, A1) == legacy


def test_folder_names_match_without_case_on_windows(
        mkenv, tmp_path, write_transcript, write_row):
    """NTFS resolves a folder name without regard to case, so a cwd spelled
    with a lower-case drive names the same folder."""
    env = mkenv(tmp_path, is_windows=True)
    strict_prefix_store(env, write_transcript, write_row)
    write_row(env, 0, A1, O1, "local_1", row(S1, "c:\\users\\u\\projects\\northwind"))
    s = only_split(ct.gather_doctor(env))
    assert copy_of(s, A1) == F_MAIN


def test_a_split_whose_rows_are_all_archived_does_not_count(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    write_transcript(env, F_MAIN, S1, t_entries(cwd=MAIN) + later_turns())
    write_transcript(env, F_WT, S1, t_entries(cwd=MAIN))
    write_row(env, 0, A1, O1, "local_1", row(S1, MAIN, isArchived=True))
    write_row(env, 0, A2, O2, "local_1", row(S1, WT, isArchived=True))
    rep = ct.gather_doctor(env)
    s = only_split(rep)
    assert s["relation"] == "strict_prefix" and s["counted"] is False
    assert all(r["archived"] for r in s["rows"])
    assert rep["exit_code"] == 0


def test_only_an_archived_row_on_the_stale_copy_does_not_count(
        mkenv, tmp_path, write_transcript, write_row):
    """The unarchived row opens the live copy; the one row behind it is
    archived. Nobody's active sidebar shows the old history."""
    env = mkenv(tmp_path)
    write_transcript(env, F_MAIN, S1, t_entries(cwd=MAIN) + later_turns())
    write_transcript(env, F_WT, S1, t_entries(cwd=MAIN))
    write_row(env, 0, A1, O1, "local_1", row(S1, MAIN))
    write_row(env, 0, A2, O2, "local_1", row(S1, WT, isArchived=True))
    assert only_split(ct.gather_doctor(env))["counted"] is False


def test_the_title_is_the_most_recently_active_rows(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    strict_prefix_store(env, write_transcript, write_row)
    write_row(env, 0, A2, O2, "local_1",
              row(S1, WT, title="Northwind intake notes", activity=5))
    assert only_split(ct.gather_doctor(env))["title"] == TITLE


def test_doctor_prints_which_copy_each_account_opens_and_why_removal_is_not_a_fix(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    env = mkenv(tmp_path)
    strict_prefix_store(env, write_transcript, write_row)
    rc = ct.cmd_doctor(env, ns())
    out = capsys.readouterr().out
    assert rc == 1
    assert "DIFFERENT COPY" in out
    assert TITLE in out
    assert F_MAIN in out and F_WT in out
    assert "strict byte-prefix" in out
    assert "one-time catch-up" in out
    # Redacted by default, like every doctor line.
    assert env.home not in out


def test_doctor_says_diverged_copies_need_a_human(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    env = mkenv(tmp_path)
    write_transcript(env, F_MAIN, S1, t_entries(cwd=MAIN) + later_turns())
    write_transcript(env, F_WT, S1, t_entries(cwd=MAIN) + diverging_turn())
    write_row(env, 0, A1, O1, "local_1", row(S1, MAIN))
    write_row(env, 0, A2, O2, "local_1", row(S1, WT))
    ct.cmd_doctor(env, ns())
    out = capsys.readouterr().out
    assert "DIVERGED" in out


def test_doctor_names_an_unplaced_row(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    env = mkenv(tmp_path)
    write_transcript(env, "C--Users-u-Projects-Elsewhere", S1, t_entries(cwd=MAIN))
    write_transcript(env, "C--Users-u-Projects-Other", S1,
                     t_entries(cwd=MAIN) + later_turns())
    write_row(env, 0, A1, O1, "local_1", row(S1, MAIN))
    ct.cmd_doctor(env, ns())
    out = capsys.readouterr().out
    assert "holds no copy" in out
    assert "In 1, a row's folder holds no copy of its own" in out


def test_doctor_lists_copies_no_row_opens_as_a_count(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    """One real conversation had thirteen copies. The ones a row opens, and
    the longest, are listed; the rest are counted."""
    env = mkenv(tmp_path)
    strict_prefix_store(env, write_transcript, write_row)
    for i in range(4):
        write_transcript(env, "C--Users-u-Projects-Spare%d" % i, S1,
                         t_entries(cwd=MAIN)[:1])
    ct.cmd_doctor(env, ns())
    out = capsys.readouterr().out
    assert "Spare" not in out
    assert "and 4 other copies no row opens" in out


def test_alignment_counts_an_unplaced_row_inside_a_split_once(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    env = mkenv(tmp_path)
    strict_prefix_store(env, write_transcript, write_row)
    write_row(env, 0, A3, O3, "local_1",
              row(S1, "C:\\Users\\u\\Projects\\Nowhere"))
    rep = ct.gather_alignment(env)
    assert rep["consistent"]["split"] == {"conversations": 1,
                                          "different_lengths": 1,
                                          "unplaced": 1}
    ct.cmd_alignment(env, ns())
    out = capsys.readouterr().out
    assert "in 1, a row's folder holds no copy" in out
    assert "more have a row" not in out


def test_doctor_anonymize_hides_the_title_and_the_folders(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    env = mkenv(tmp_path)
    strict_prefix_store(env, write_transcript, write_row)
    ct._ANONYMIZE = True
    ct.cmd_doctor(env, ns())
    out = capsys.readouterr().out
    ct.cmd_doctor(env, ns(json=True))
    js = capsys.readouterr().out
    assert "DIFFERENT COPY" in out and '"split_rows"' in js
    for text in (out, js):
        assert TITLE not in text
        assert "Northwind" not in text
    json.loads(js)


def test_the_split_report_keys_are_all_structural(
        mkenv, tmp_path, write_transcript, write_row):
    from test_anonymize import walk_fixed_keys
    env = mkenv(tmp_path)
    strict_prefix_store(env, write_transcript, write_row)
    write_transcript(env, "C--Users-u-Projects-Elsewhere", S2, t_entries(cwd=MAIN))
    write_transcript(env, "C--Users-u-Projects-Other", S2, t_entries(cwd=MAIN))
    write_row(env, 0, A1, O1, "local_2", row(S2, MAIN, title="Northwind intake"))
    rep = ct.gather_doctor(env)
    assert len(rep["split_rows"]) == 2
    assert walk_fixed_keys(rep) == []
    assert walk_fixed_keys(ct.gather_alignment(env)) == []


def many_splits(env, write_transcript, write_row, n_counted, n_identical):
    """N_COUNTED strict-prefix splits and N_IDENTICAL identical ones, the
    identical ids sorting first so an id-ordered report would show them."""
    titles = {}
    for i in range(n_identical):
        sid = "0000000%d-9abc-def0-1234-56789abcdef0" % i
        write_transcript(env, F_MAIN, sid, t_entries(cwd=MAIN))
        write_transcript(env, F_WT, sid, t_entries(cwd=MAIN))
        t = "Northwind identical %d" % i
        write_row(env, 0, A1, O1, "local_i%d" % i, row(sid, MAIN, title=t))
        write_row(env, 0, A2, O2, "local_i%d" % i, row(sid, WT, title=t))
        titles[sid] = t
    for i in range(n_counted):
        sid = "f000000%d-9abc-def0-1234-56789abcdef0" % i
        write_transcript(env, F_MAIN, sid, t_entries(cwd=MAIN) + later_turns())
        write_transcript(env, F_WT, sid, t_entries(cwd=MAIN))
        t = "ACME-REVIEW counted %d" % i
        write_row(env, 0, A1, O1, "local_c%d" % i, row(sid, MAIN, title=t))
        write_row(env, 0, A2, O2, "local_c%d" % i, row(sid, WT, title=t))
        titles[sid] = t
    return titles


def test_splits_that_count_come_first(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    many_splits(env, write_transcript, write_row, n_counted=1, n_identical=2)
    rels = [(s["counted"], s["relation"]) for s in ct.gather_doctor(env)["split_rows"]]
    assert rels == [(True, "strict_prefix"), (False, "identical"),
                    (False, "identical")]


def test_doctor_prints_every_split_that_counts_and_caps_the_rest(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    env = mkenv(tmp_path)
    titles = many_splits(env, write_transcript, write_row,
                         n_counted=ct.SPLIT_DETAIL_LIMIT + 1, n_identical=7)
    ct.cmd_doctor(env, ns())
    out = capsys.readouterr().out
    for sid, t in titles.items():
        if t.startswith("ACME-REVIEW"):
            assert t in out
    shown = sum(1 for t in titles.values() if t.startswith("Northwind") and t in out)
    assert shown == ct.SPLIT_DETAIL_LIMIT
    assert "... and 2 more" in out
    assert "6 show an account an older or a diverged history" in out
    assert "7 are byte-identical for now" in out


# ------------------------------------------------------------ alignment

def test_alignment_counts_splits_from_sizes_without_reading_a_copy(
        mkenv, tmp_path, write_transcript, write_row, monkeypatch):
    env = mkenv(tmp_path)
    strict_prefix_store(env, write_transcript, write_row)
    # A second conversation split across identical copies: same length.
    write_transcript(env, F_MAIN, S2, t_entries(cwd=MAIN))
    write_transcript(env, F_WT, S2, t_entries(cwd=MAIN))
    write_row(env, 0, A1, O1, "local_2", row(S2, MAIN, title="Northwind intake"))
    write_row(env, 0, A2, O2, "local_2", row(S2, WT, title="Northwind intake"))

    def no_reading(*a, **k):
        raise AssertionError("alignment compared transcript bytes")
    monkeypatch.setattr(ct, "_transcript_copies", no_reading)
    monkeypatch.setattr(ct, "_byte_prefixes", no_reading)
    rep = ct.gather_alignment(env)
    assert rep["consistent"]["split"] == {"conversations": 2,
                                          "different_lengths": 1,
                                          "unplaced": 0}
    # Complete is unchanged: both conversations have a row everywhere.
    assert rep["complete"]["in_all_accounts"] == 2


def test_alignment_prints_the_split_line_and_points_at_doctor(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    env = mkenv(tmp_path)
    strict_prefix_store(env, write_transcript, write_row)
    rc = ct.cmd_alignment(env, ns())
    out = capsys.readouterr().out
    assert rc == 0
    line = [l for l in out.splitlines() if "different copy" in l]
    assert len(line) == 1 and "1 conversation(s)" in line[0]
    assert "doctor" in out


def test_alignment_reports_no_split_line_detail_when_there_are_none(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    env = mkenv(tmp_path)
    write_transcript(env, F_MAIN, S1, t_entries(cwd=MAIN))
    write_row(env, 0, A1, O1, "local_1", row(S1, MAIN))
    rep = ct.gather_alignment(env)
    assert rep["consistent"]["split"] == {"conversations": 0,
                                          "different_lengths": 0,
                                          "unplaced": 0}
