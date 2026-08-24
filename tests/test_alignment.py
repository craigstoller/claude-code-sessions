"""Tests for `alignment` - the five-property scoreboard.

A note on the fixture, because it reads backwards: `write_row(env, root, X, Y, ...)`
joins `<root>/X/Y/<local_id>.json`, and the real store is
`<accountUuid>/<organizationUuid>/`. So the THIRD positional argument is the
account, whatever the fixture calls its parameters. Every test here passes the
account there.
"""
import json
import types

import claude_code_sessions as ct

A = "acct-alpha"
B = "acct-beta"
ORG = "org"


def ns(**kw):
    d = {"json": False, "verbose": False}
    d.update(kw)
    return types.SimpleNamespace(**d)


def row(sid, title=None, local=None, activity=9):
    d = {"sessionId": local or ("local_" + sid), "cliSessionId": sid,
         "cwd": "C:\\p", "lastActivityAt": activity}
    if title is not None:
        d["title"] = title
    return d


def test_counts_each_property_independently(mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    for sid in ("s-both", "s-alpha-only", "s-orphan"):
        write_transcript(env, "C--p", sid, [{"cwd": "C:\\p"}])
    # reachable from both accounts
    write_row(env, 0, A, ORG, "local_both", row("s-both", "Shared"))
    write_row(env, 0, B, ORG, "local_both", row("s-both", "Shared"))
    # reachable from one only
    write_row(env, 0, A, ORG, "local_solo", row("s-alpha-only", "Solo"))
    # a row whose transcript is gone, and one with no pointer at all
    write_row(env, 0, A, ORG, "local_dead", row("s-vanished", "Dead"))
    write_row(env, 0, A, ORG, "local_blank", {"sessionId": "local_blank",
                                              "cliSessionId": "", "cwd": "C:\\p"})

    rep = ct.gather_alignment(env)

    assert [a["label"] for a in rep["accounts"]] == [A[:8], B[:8]]
    # s-orphan has a transcript and no row; s-vanished has a row and no transcript
    assert rep["reachable"] == {"transcripts": 3, "reachable": 2, "orphans": 1,
                                "orphan_ids": ["s-orphan"]}
    assert rep["complete"]["conversations"] == 3
    assert rep["complete"]["in_all_accounts"] == 1          # only s-both
    assert rep["complete"]["short"] == 2                    # s-alpha-only, s-vanished
    assert rep["safe"] == {"dead_rows": 1, "blank_rows": 1, "unreadable_rows": 0}
    assert rep["exit_code"] == 0


def test_duplicate_titles_are_counted_per_sidebar_not_machine_wide(
        mkenv, tmp_path, write_transcript, write_row):
    """The regression that matters most.

    One conversation synced to two accounts shows ONE row in each sidebar. An
    earlier analysis grouped titles across the whole machine and called that a
    duplicate - then proposed removing an account's only copy. Nothing here may
    ever count a cross-account pair as a within-sidebar duplicate.
    """
    env = mkenv(tmp_path)
    for sid in ("s-1", "s-2"):
        write_transcript(env, "C--p", sid, [{"cwd": "C:\\p"}])
    write_row(env, 0, A, ORG, "local_1", row("s-1", "Same Name"))
    write_row(env, 0, B, ORG, "local_1", row("s-1", "Same Name"))

    rep = ct.gather_alignment(env)
    assert rep["distinguishable"]["duplicate_titles"] == 0
    assert rep["distinguishable"]["per_account"] == {A[:8]: 0, B[:8]: 0}

    # Now a genuine one: TWO conversations under one title, inside ONE sidebar.
    write_row(env, 0, A, ORG, "local_2", row("s-2", "Same Name"))
    rep = ct.gather_alignment(env)
    assert rep["distinguishable"]["duplicate_titles"] == 1
    assert rep["distinguishable"]["per_account"] == {A[:8]: 1, B[:8]: 0}
    assert rep["distinguishable"]["titles"]["Same Name"] == {A[:8]: ["s-1", "s-2"]}


def test_disagreement_without_a_gap_is_reported_but_not_counted_as_loss(
        mkenv, tmp_path, write_transcript, write_row):
    """A row file opening different conversations per account costs nothing when
    both conversations are reachable from every account by some OTHER row."""
    env = mkenv(tmp_path)
    for sid in ("s-x", "s-y"):
        write_transcript(env, "C--p", sid, [{"cwd": "C:\\p"}])
    # the disagreeing file: alpha's copy opens s-x, beta's opens s-y
    write_row(env, 0, A, ORG, "local_split", row("s-x", "Split"))
    write_row(env, 0, B, ORG, "local_split", row("s-y", "Split"))
    # ...but other rows make both reachable from both accounts
    write_row(env, 0, B, ORG, "local_x2", row("s-x", "X elsewhere"))
    write_row(env, 0, A, ORG, "local_y2", row("s-y", "Y elsewhere"))

    rep = ct.gather_alignment(env)
    assert rep["consistent"]["disagreeing_rows"] == 1
    assert rep["consistent"]["leaving_a_gap"] == 0
    assert rep["consistent"]["rows"][0]["short_of_all_accounts"] == []

    # Remove the compensating row and the same disagreement now loses something.
    import os
    os.remove(os.path.join(env.store_candidates[0], B, ORG, "local_x2.json"))
    rep = ct.gather_alignment(env)
    assert rep["consistent"]["disagreeing_rows"] == 1
    assert rep["consistent"]["leaving_a_gap"] == 1
    assert rep["consistent"]["rows"][0]["short_of_all_accounts"] == ["s-x"]


def test_exit_stays_zero_while_work_remains(mkenv, tmp_path, write_transcript,
                                            write_row, capsys):
    """It is a scoreboard, not a check. Duplicates, gaps and orphans are all
    present here and it still exits 0 - a command that exits 1 for months
    trains you to stop reading it."""
    env = mkenv(tmp_path)
    for sid in ("s-1", "s-2", "s-orphan"):
        write_transcript(env, "C--p", sid, [{"cwd": "C:\\p"}])
    write_row(env, 0, A, ORG, "local_1", row("s-1", "Dup"))
    write_row(env, 0, A, ORG, "local_2", row("s-2", "Dup"))

    rc = ct.cmd_alignment(env, ns())
    out = capsys.readouterr().out
    assert rc == 0
    assert "[observed]" in out and "[hypothesis]" in out
    assert "distinguishable 1 title(s)" in out
    assert "1 orphaned" in out

    rc = ct.cmd_alignment(env, ns(json=True))
    rep = json.loads(capsys.readouterr().out)
    assert rc == 0 and rep["exit_code"] == 0
    assert rep["distinguishable"]["duplicate_titles"] == 1


def test_missing_store_is_the_one_failure(mkenv, tmp_path, capsys):
    env = mkenv(tmp_path)
    env = ct.dataclasses.replace(env, store_candidates=[str(tmp_path / "nope")])
    rc = ct.cmd_alignment(env, ns())
    assert rc == 1
    assert "[observed] store:" in capsys.readouterr().out
