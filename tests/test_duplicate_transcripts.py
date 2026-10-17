"""The duplicated-transcript hold's diagnosis.

One session id's transcript in two or more project folders is refused - by
converge as `held_transcript_unusable`, by new-row as a refusal - and stays
refused: nothing is written and no copy is chosen. What these pin is the
diagnosis the refusal now carries when the bytes settle the question: every
copy but the longest a strict byte-prefix of it means the shorter copies are
stale and the longest is live, and byte-identical copies mean either can go.
Anything else keeps the older wording. Fake cast only - ACME-REVIEW,
Northwind, the S1/S2 ids - never a real id, title or path.
"""
import json
import os
import types

import pytest

import claude_code_sessions as ct
from test_converge import A1, A2, O1, O2, S1, S2, cv_ns, row_data, t_entries


@pytest.fixture(autouse=True)
def _reset_anonymize():
    ct._ANONYMIZE = False
    ct._ANON_CACHE.clear()
    yield
    ct._ANONYMIZE = False
    ct._ANON_CACHE.clear()


def later_turns():
    """Two more records, so a copy holding only t_entries() is a strict
    byte-prefix of one holding these as well - write_transcript writes one
    json.dumps line per record, in order."""
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


def held_store(env, write_transcript, write_row, copies):
    """S1 written once per (folder, entries) in COPIES, held by A1; A2 is a
    destination through an unrelated healthy conversation, so converge wants
    an S1 row in A2 and has to read S1's transcript to build it. Returns
    the copies' paths in the order given."""
    paths = [write_transcript(env, folder, S1, entries)
             for folder, entries in copies]
    write_row(env, 0, A1, O1, "local_1", row_data(S1, "ACME-REVIEW handoff"))
    write_transcript(env, "C--p", S2, t_entries(cwd="C:\\p"))
    write_row(env, 0, A2, O2, "local_2", row_data(S2, "Padding", cwd="C:\\p"))
    return paths


def unusable_holds(m):
    return [h for h in m["holds"] if h["reason"] == "held_transcript_unusable"]


def remove(path):
    return "Remove-Item -LiteralPath '{0}'".format(path)


# ------------------------------------------------------------- strict prefix

def test_strict_prefix_names_the_stale_copy_and_the_live_one(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    live, stale = held_store(env, write_transcript, write_row, [
        ("C--Users-u-Projects-Northwind", t_entries() + later_turns()),
        ("C--Users-u-Projects-Northwind-wt", t_entries()),
    ])
    m = ct.plan_converge(env, ct.ConvergeFlags())
    # Still refused: no row is planned for S1 anywhere.
    assert not [r for r in m["rows"] if r["session"] == S1]
    held = unusable_holds(m)
    assert [h["session"] for h in held] == [S1]
    h = held[0]
    tc = h["transcript_copies"]
    assert tc["relation"] == "strict_prefix"
    assert tc["live"] == live
    assert tc["stale"] == [stale]
    assert sorted(c["path"] for c in tc["copies"]) == sorted([live, stale])
    sizes = {c["path"]: c["bytes"] for c in tc["copies"]}
    assert sizes == {live: os.path.getsize(live),
                     stale: os.path.getsize(stale)}
    d = h["detail"]
    assert "Nothing was written" in d
    assert "stale" in d and "live" in d
    assert remove(stale) in d
    assert remove(live) not in d
    # The older "remove whichever you do not want" wording is for copies
    # the bytes do not settle; it must not contradict the diagnosis.
    assert "Remove or rename the copies you do not want" not in d


def test_strict_prefix_fix_survives_the_plain_text_redaction(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    """The hold text goes through redact() like every hold: the home folds
    to ~ and the session id shortens. --verbose prints the removal command
    with the real path, ready to paste."""
    env = mkenv(tmp_path)
    live, stale = held_store(env, write_transcript, write_row, [
        ("C--Users-u-Projects-Northwind", t_entries() + later_turns()),
        ("C--Users-u-Projects-Northwind-wt", t_entries()),
    ])
    rc = ct.cmd_converge(env, cv_ns())
    out = capsys.readouterr().out
    assert rc == 0
    assert "held_transcript_unusable" in out
    assert "stale" in out and "live" in out
    assert "Remove-Item -LiteralPath '~" in out
    assert env.home not in out
    assert S1 not in out

    rc = ct.cmd_converge(env, cv_ns(verbose=True))
    out = capsys.readouterr().out
    assert rc == 0
    assert remove(stale) in out
    assert remove(live) not in out


def test_strict_prefix_is_in_the_json(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    env = mkenv(tmp_path)
    live, stale = held_store(env, write_transcript, write_row, [
        ("C--Users-u-Projects-Northwind", t_entries() + later_turns()),
        ("C--Users-u-Projects-Northwind-wt", t_entries()),
    ])
    rc = ct.cmd_converge(env, cv_ns(json=True))
    pub = json.loads(capsys.readouterr().out)
    assert rc == 0
    (h,) = unusable_holds(pub)
    assert h["transcript_copies"]["relation"] == "strict_prefix"
    # Plain --json is unredacted by contract: full paths.
    assert h["transcript_copies"]["live"] == live
    assert h["transcript_copies"]["stale"] == [stale]
    assert remove(stale) in h["detail"]


def test_three_copies_two_stale(mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    shortest, live, middle = held_store(env, write_transcript, write_row, [
        ("C--Users-u-Projects-Northwind-a", t_entries()[:1]),
        ("C--Users-u-Projects-Northwind-b", t_entries() + later_turns()),
        ("C--Users-u-Projects-Northwind-c", t_entries()),
    ])
    (h,) = unusable_holds(ct.plan_converge(env, ct.ConvergeFlags()))
    tc = h["transcript_copies"]
    assert tc["relation"] == "strict_prefix"
    assert tc["live"] == live
    assert sorted(tc["stale"]) == sorted([shortest, middle])
    assert remove(shortest) in h["detail"]
    assert remove(middle) in h["detail"]
    assert remove(live) not in h["detail"]


def test_three_copies_one_diverged_keeps_the_older_wording(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    held_store(env, write_transcript, write_row, [
        ("C--Users-u-Projects-Northwind-a", t_entries()),
        ("C--Users-u-Projects-Northwind-b", t_entries() + later_turns()),
        ("C--Users-u-Projects-Northwind-c", t_entries() + diverging_turn()),
    ])
    (h,) = unusable_holds(ct.plan_converge(env, ct.ConvergeFlags()))
    assert h["transcript_copies"]["relation"] == "diverged"
    assert "Remove or rename the copies you do not want" in h["detail"]
    assert "Remove-Item" not in h["detail"]


# ------------------------------------------------------------------ diverged

def test_diverged_copies_keep_the_older_wording(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    a, b = held_store(env, write_transcript, write_row, [
        ("C--Users-u-Projects-Northwind", t_entries() + later_turns()),
        ("C--Users-u-Projects-Northwind-wt", t_entries() + diverging_turn()),
    ])
    m = ct.plan_converge(env, ct.ConvergeFlags())
    assert not [r for r in m["rows"] if r["session"] == S1]
    (h,) = unusable_holds(m)
    tc = h["transcript_copies"]
    assert tc["relation"] == "diverged"
    assert tc["live"] == "" and tc["stale"] == []
    d = h["detail"]
    assert d == (
        "{0} exists in more than one project folder, so a row built for it "
        "would name a file this tool cannot identify; refusing to guess "
        "which. Nothing was written. Remove or rename the copies you do not "
        "want - whichever one is left is what the row will open:\n"
        "   {1}\n   {2}".format(S1, a, b))


# ----------------------------------------------------------------- identical

def test_identical_copies_are_said_identical_and_either_can_go(
        mkenv, tmp_path, write_transcript, write_row):
    """Identical is not a strict prefix: neither copy is stale, so the tool
    names no copy for removal - it says either can go."""
    env = mkenv(tmp_path)
    a, b = held_store(env, write_transcript, write_row, [
        ("C--Users-u-Projects-Northwind", t_entries()),
        ("C--Users-u-Projects-Northwind-wt", t_entries()),
    ])
    m = ct.plan_converge(env, ct.ConvergeFlags())
    assert not [r for r in m["rows"] if r["session"] == S1]
    (h,) = unusable_holds(m)
    tc = h["transcript_copies"]
    assert tc["relation"] == "identical"
    assert tc["live"] == "" and tc["stale"] == []
    d = h["detail"]
    assert "byte-identical" in d
    assert "either can go" in d
    assert "stale" not in d
    assert "Remove-Item" not in d
    assert a in d and b in d


def test_three_identical_copies_any_can_go(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    held_store(env, write_transcript, write_row, [
        ("C--Users-u-Projects-Northwind-a", t_entries()),
        ("C--Users-u-Projects-Northwind-b", t_entries()),
        ("C--Users-u-Projects-Northwind-c", t_entries()),
    ])
    (h,) = unusable_holds(ct.plan_converge(env, ct.ConvergeFlags()))
    assert h["transcript_copies"]["relation"] == "identical"
    assert "byte-identical" in h["detail"]
    assert "Remove-Item" not in h["detail"]


# ----------------------------------------------------------- the comparison

def _write(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def test_copies_tied_for_longest_are_inconclusive(tmp_path):
    """Two equal longest copies plus a shorter prefix: not every copy but
    one is a strict prefix of the longest, so no single live copy exists to
    name - the older wording, with the reason recorded."""
    a = _write(tmp_path, "a", b"one\ntwo\n")
    b = _write(tmp_path, "b", b"one\ntwo\n")
    c = _write(tmp_path, "c", b"one\n")
    tc = ct._transcript_copies([a, b, c])
    assert tc["relation"] == "inconclusive"
    assert tc["reason"]
    assert tc["live"] == "" and tc["stale"] == []


def test_comparison_streams_across_chunk_boundaries(tmp_path, monkeypatch):
    """The copies are compared a chunk at a time, never read whole - pinned
    with a tiny chunk so prefixes and divergences land past the first one."""
    monkeypatch.setattr(ct, "_COPIES_CHUNK", 5)
    long_ = _write(tmp_path, "long", b"0123456789abcdefghij")
    prefix = _write(tmp_path, "prefix", b"0123456789abc")
    late = _write(tmp_path, "late", b"0123456789abX")
    tc = ct._transcript_copies([prefix, long_])
    assert tc["relation"] == "strict_prefix"
    assert tc["live"] == long_ and tc["stale"] == [prefix]
    assert ct._transcript_copies([long_, late])["relation"] == "diverged"
    assert ct._transcript_copies(
        [long_, _write(tmp_path, "same", b"0123456789abcdefghij")]
    )["relation"] == "identical"


def test_empty_copy_is_a_prefix_of_anything(tmp_path):
    long_ = _write(tmp_path, "long", b"one\n")
    empty = _write(tmp_path, "empty", b"")
    tc = ct._transcript_copies([empty, long_])
    assert tc["relation"] == "strict_prefix"
    assert tc["live"] == long_ and tc["stale"] == [empty]


def test_an_unreadable_copy_is_inconclusive(tmp_path):
    a = _write(tmp_path, "a", b"one\n")
    tc = ct._transcript_copies([a, str(tmp_path / "gone")])
    assert tc["relation"] == "inconclusive"
    assert tc["reason"]


def test_removal_command_quotes_for_the_platform():
    """The one printed command that deletes. Single quotes, -LiteralPath so
    brackets are not wildcards, and an apostrophe in the path escaped the
    way each shell reads it."""
    win = types.SimpleNamespace(is_windows=True)
    posix = types.SimpleNamespace(is_windows=False)
    assert ct._remove_command(win, "C:\\Users\\o'brien\\[x].jsonl") == \
        "Remove-Item -LiteralPath 'C:\\Users\\o''brien\\[x].jsonl'"
    assert ct._remove_command(win, "C:\\Users\\o\u2019brien\\x.jsonl") == \
        "Remove-Item -LiteralPath 'C:\\Users\\o\u2019\u2019brien\\x.jsonl'"
    assert ct._remove_command(posix, "/home/o'brien/x.jsonl") == \
        "rm -- '/home/o'\\''brien/x.jsonl'"


# ---------------------------------------------------------------- anonymize

def test_anonymize_covers_the_diagnosis(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    """The diagnosis adds path-carrying fields beside the detail; under
    --anonymize they must not name the client-named project folder, in text
    or in --json, and the new keys are schema the anonymizer never renames."""
    from test_anonymize import walk_fixed_keys
    env = mkenv(tmp_path)
    held_store(env, write_transcript, write_row, [
        ("C--clients-Northwind", t_entries(cwd="C:\\clients\\Northwind")
         + later_turns()),
        ("C--Users-u-scratch", t_entries(cwd="C:\\clients\\Northwind")),
    ])
    raw = ct.plan_converge(env, ct.ConvergeFlags())
    (h,) = unusable_holds(raw)
    assert "C--clients-Northwind" in h["transcript_copies"]["live"]

    ct._ANONYMIZE = True
    rc = ct.cmd_converge(env, cv_ns(anonymize=True))
    out = capsys.readouterr().out
    assert rc == 0
    assert "held_transcript_unusable" in out and "stale" in out
    assert "Northwind" not in out

    rc = ct.cmd_converge(env, cv_ns(json=True, anonymize=True))
    payload = capsys.readouterr().out
    assert rc == 0
    assert "Northwind" not in payload
    pub = json.loads(payload)
    (h,) = unusable_holds(pub)
    assert h["transcript_copies"]["relation"] == "strict_prefix"
    assert "<project-" in h["transcript_copies"]["live"]
    assert not walk_fixed_keys(pub)


# ------------------------------------------------------------------ new-row

def _signed_in(env):
    with open(os.path.join(env.home, ".claude.json"), "w",
              encoding="utf-8") as fh:
        json.dump({"oauthAccount": {"accountUuid": A1,
                                    "organizationUuid": O1,
                                    "emailAddress": "alice@example.com"}}, fh)


def _new_row_env(mkenv, tmp_path, write_row):
    env = mkenv(tmp_path)
    write_row(env, 0, A1, O1, "local_p",
              {"sessionId": "local_p", "cliSessionId": "other-conversation",
               "title": "Padding", "lastActivityAt": 9})
    _signed_in(env)
    return env


def test_new_row_refusal_carries_the_diagnosis(
        mkenv, tmp_path, write_transcript, write_row):
    env = _new_row_env(mkenv, tmp_path, write_row)
    write_transcript(env, "C--Users-u-Projects-Northwind", S1,
                     t_entries() + later_turns())
    stale = write_transcript(env, "C--Users-u-Projects-Northwind-wt", S1,
                             t_entries())
    with pytest.raises(ct.Refusal) as exc:
        ct.plan_new_row(env, ct.NewRowFlags(to_session=S1))
    msg = str(exc.value)
    assert "Nothing was written" in msg
    assert "stale" in msg and remove(stale) in msg


def test_new_row_apply_recheck_carries_the_diagnosis(
        mkenv, tmp_path, write_transcript, write_row):
    """A prefix copy appearing between plan and apply: the apply-time
    re-check refuses as before, and says which copy is the stale one."""
    env = _new_row_env(mkenv, tmp_path, write_row)
    write_transcript(env, "C--Users-u-Projects-Northwind", S1,
                     t_entries() + later_turns())
    m = ct.plan_new_row(env, ct.NewRowFlags(to_session=S1))
    stale = write_transcript(env, "C--Users-u-Projects-Northwind-wt", S1,
                             t_entries())
    with pytest.raises(ct.Refusal) as exc:
        ct.run_new_row(env, m)
    msg = str(exc.value)
    assert "now exists in more than one project folder" in msg
    assert "Nothing was written" in msg
    assert "stale" in msg and remove(stale) in msg
    assert not os.path.exists(m["rows"][0]["dest_path"])
