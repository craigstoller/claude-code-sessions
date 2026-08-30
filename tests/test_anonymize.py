"""Tests for `--anonymize`.

Three of these pin bugs the first implementation actually had. All three were
the same shape - output that LOOKED anonymised while still carrying the private
string - which is worse than no feature at all, because it invites the paste.
"""
import json
import types

import pytest

import claude_code_sessions as ct


@pytest.fixture(autouse=True)
def _reset_anonymize():
    ct._ANONYMIZE = False
    ct._ANON_CACHE.clear()
    yield
    ct._ANONYMIZE = False
    ct._ANON_CACHE.clear()


def ns(**kw):
    d = {"json": False, "verbose": False, "anonymize": False,
         "query": "", "project": None, "full": False}
    d.update(kw)
    return types.SimpleNamespace(**d)


def seed(env, write_transcript, write_row, title="Board complaint finalisation"):
    write_transcript(env, "C--clients-Northwind", "s-1", [{"cwd": "C:\\clients\\Northwind"}])
    write_row(env, 0, "acct", "org", "local_1",
              {"sessionId": "local_1", "cliSessionId": "s-1", "cwd": "C:\\clients\\Northwind",
               "title": title, "lastActivityAt": 9})
    return title


def test_the_flag_gates_it_and_labels_are_stable(mkenv, tmp_path, write_transcript, write_row):
    """`anonymize()` always substitutes when called directly; the FLAG decides
    whether `redact()` reaches for it. Test the contract, not the helper."""
    env = mkenv(tmp_path)
    title = seed(env, write_transcript, write_row)

    ct._ANONYMIZE = False
    assert title in ct.redact(env, "row: " + title), "off by default"

    ct._ANONYMIZE = True
    ct._ANON_CACHE.clear()
    first = ct.redact(env, "row: " + title)
    assert title not in first and "<session-" in first

    ct._ANON_CACHE.clear()
    assert ct.redact(env, "row: " + title) == first, "same title, same label, across runs"


def test_long_titles_are_replaced_before_formatting_truncates_them(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    """The first bug.

    `list` truncates the title to 40 characters. Anonymising the finished LINE
    therefore never matched anything longer than that, so the output looked
    processed and most titles were untouched. The replacement has to happen on
    the field, before formatting.
    """
    env = mkenv(tmp_path)
    long_title = "A board complaint finalisation that is quite a lot longer than forty characters"
    seed(env, write_transcript, write_row, title=long_title)
    ct._ANONYMIZE = True

    ct.cmd_list(env, ns(anonymize=True))
    out = capsys.readouterr().out
    assert "board complaint" not in out
    assert "<session-" in out
    # And no fragment of it survived the truncation either.
    assert "A board" not in out


def test_an_email_is_not_chewed_up_by_path_matching(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    """The second bug.

    Directory BASENAMES were in the substitution table, so short generic ones
    matched inside unrelated text and rewrote `craig@foundryside.co` into
    `<project-b83d>@<project-7aa2>`. Over-matching corrupts the report while
    still looking like it worked.
    """
    env = mkenv(tmp_path)
    write_transcript(env, "C--Users-craig-work", "s-1", [{"cwd": "C:\\Users\\craig\\work"}])
    write_row(env, 0, "acct", "org", "local_1",
              {"sessionId": "local_1", "cliSessionId": "s-1", "cwd": "C:\\Users\\craig\\work",
               "title": "Something", "lastActivityAt": 9})
    ct._ANONYMIZE = True
    assert ct.anonymize(env, "craig@foundryside.co") == "craig@foundryside.co"
    assert ct.anonymize(env, "the work directory") == "the work directory"


def test_dict_keys_are_anonymised_not_just_values(mkenv, tmp_path):
    """The third bug. Reports are keyed BY the private thing - per-account
    tallies by email, duplicate-title groups by the title - so anonymising only
    values left it in the key, one line below where it had been replaced."""
    env = mkenv(tmp_path)
    ct._ANONYMIZE = True
    rep = {"per_account": {"craig@foundryside.co": 3},
           "titles": {"Board complaint finalisation": {"craig@foundryside.co": ["s-1"]}}}
    out = ct.anonymize_report(env, rep)
    assert "craig@foundryside.co" not in json.dumps(out)
    assert list(out["per_account"])[0].startswith("<account-")


def test_json_is_covered(mkenv, tmp_path, write_transcript, write_row, capsys):
    env = mkenv(tmp_path)
    title = seed(env, write_transcript, write_row)
    ct._ANONYMIZE = True
    ct.cmd_list(env, ns(anonymize=True, json=True))
    payload = capsys.readouterr().out
    assert title not in payload
    assert "Northwind" not in payload
    assert "<session-" in payload


def test_anonymize_with_verbose_is_refused(mkenv, tmp_path, capsys):
    """--verbose prints the raw line and never reaches redact(), where
    anonymising happens. Accepting both would print real titles under a flag
    whose whole purpose is to hide them."""
    rc = ct.main(["list", "--anonymize", "--verbose"])
    assert rc == 2
    assert "contradict" in capsys.readouterr().err


# ------------------- 0.14.0: measured hold remedies (hold-remedies design)

def test_anonymized_output_is_never_runnable(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    """Spec test 25e: anonymized reports are display artifacts. The rendered
    command necessarily carries an opaque label where the title was - a
    command that would literally rename a conversation to that label - so
    `command_runnable` is forced false under --anonymize."""
    from test_converge import cv_ns, measured_pair
    env = mkenv(tmp_path)
    measured_pair(env, write_transcript, write_row)
    ct._ANONYMIZE = True
    rc = ct.cmd_converge(env, cv_ns(json=True, anonymize=True))
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    holds = [h for h in payload["holds"]
             if h["reason"] == "held_title_collision"]
    assert len(holds) == 2
    for h in holds:
        assert h["measured"]["command_runnable"] is False
        assert "ACME-REVIEW" not in h["retitle"]
        assert "<session-" in h["retitle"]
        assert "earlier leg" in h["retitle"]


def test_anonymize_covers_the_rendered_command(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    """Spec test 27: under --anonymize the generated title survives in
    neither the measured line, the suggested_title field, nor the rendered
    retitle string - text or JSON - INCLUDING the suffix-replacement path,
    where the generated title's base is a stored title's stripped base
    rather than any stored title verbatim."""
    from test_converge import cv_ns, measured_pair
    for sub, title in (("plain", "ACME-REVIEW session"),
                       ("worn", "ACME-REVIEW session - earlier leg "
                                "(Aug 1-3)")):
        env = mkenv(tmp_path / sub)
        measured_pair(env, write_transcript, write_row, title=title)
        ct._ANONYMIZE = True
        ct._ANON_CACHE.clear()
        rc = ct.cmd_converge(env, cv_ns(anonymize=True))
        out = capsys.readouterr().out
        assert rc == 0
        assert "ACME-REVIEW" not in out
        assert "<session-" in out
        rc = ct.cmd_converge(env, cv_ns(json=True, anonymize=True))
        payload = capsys.readouterr().out
        assert rc == 0
        assert "ACME-REVIEW" not in payload
        holds = [h for h in json.loads(payload)["holds"]
                 if h["reason"] == "held_title_collision"]
        for h in holds:
            assert "ACME-REVIEW" not in (h["measured"]["suggested_title"]
                                         or "")
            assert "ACME-REVIEW" not in h["retitle"]


# ---------------- plan-chosen titles (the transcript-derived fallback)
#
# _anon_pairs is built from STORED row titles, but converge names a
# conversation whose rows carry no usable title at plan time, from the
# transcript (_new_row_title's customTitle fallback). That string exists in
# no row, so the substitution table had never seen it and it rode through
# --anonymize verbatim in every field and sentence that carries it.


def test_a_transcript_derived_row_title_is_anonymized(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    """A conversation with untitled rows gets its planned title from the
    transcript's customTitle; under --anonymize that title must come out as
    a label in the row listing, text and JSON both."""
    from test_converge import cv_ns, t_entries, row_data, S1, PAD, A1, O1, A2, O2
    env = mkenv(tmp_path)
    private = "ACME-REVIEW offboarding escalation"
    write_transcript(env, "C--p", S1, t_entries() + [{"customTitle": private}])
    write_row(env, 0, A1, O1, "local_s1", row_data(S1, ""))
    write_row(env, 0, A2, O2, "local_pad", row_data(PAD, "Padding"))
    ct._ANONYMIZE = True
    rc = ct.cmd_converge(env, cv_ns(anonymize=True))
    out = capsys.readouterr().out
    assert rc == 0
    assert "ACME-REVIEW" not in out
    assert "<session-" in out
    rc = ct.cmd_converge(env, cv_ns(json=True, anonymize=True))
    payload = capsys.readouterr().out
    assert rc == 0
    assert "ACME-REVIEW" not in payload
    assert "<session-" in payload


def test_a_placeholder_title_does_not_leak_the_cwd_leaf(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    """The fallback's other arm: no customTitle either, so the plan names the
    conversation '(untitled - <day>, <n> turns, <leaf>)' - and the leaf can
    name a client. Whole cwd paths are in the table; a leaf inside a
    generated title is not, unless the chosen title is registered whole."""
    from test_converge import cv_ns, t_entries, row_data, S1, PAD, A1, O1, A2, O2
    env = mkenv(tmp_path)
    write_transcript(env, "C--clients-Northwind", S1,
                     t_entries(cwd="C:\\clients\\Northwind"))
    write_row(env, 0, A1, O1, "local_s1", row_data(S1, ""))
    write_row(env, 0, A2, O2, "local_pad", row_data(PAD, "Padding"))
    ct._ANONYMIZE = True
    rc = ct.cmd_converge(env, cv_ns(anonymize=True))
    out = capsys.readouterr().out
    assert rc == 0
    assert "Northwind" not in out
    assert "<session-" in out
    rc = ct.cmd_converge(env, cv_ns(json=True, anonymize=True))
    payload = capsys.readouterr().out
    assert rc == 0
    assert "Northwind" not in payload


def test_registration_keeps_the_table_longest_first(
        mkenv, tmp_path, write_transcript, write_row):
    """anonymize() replaces in table order, so a registered title LONGER
    than a stored one it contains must sort ahead of it - iteration hitting
    the short stored pair first would partial-replace inside the long title
    and leave the tail exposed, the exact failure longest-first exists
    for."""
    env = mkenv(tmp_path)
    seed(env, write_transcript, write_row, title="ACME")
    long_title = "ACME offboarding escalation"
    ct._anon_register_title(env, long_title)
    out = ct.anonymize(env, "row: " + long_title)
    assert "offboarding" not in out
    assert out == "row: " + ct._anon_label("session", long_title)


def test_holder_titles_in_a_disagreement_note_are_covered(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    """The minority copies' titles in a disagreement note are STORED row
    titles, covered by _anon_pairs rather than by converge's registration
    loop - pinned here so that division of coverage stays observable if
    either side of it ever changes."""
    from test_converge import cv_ns, t_entries, row_data, S1, PAD, A1, O1, A2, O2, A3, O3
    env = mkenv(tmp_path)
    write_transcript(env, "C--p", S1, t_entries())
    write_row(env, 0, A1, O1, "local_s1",
              row_data(S1, "ACME-REVIEW plan draft", lastActivityAt=5))
    write_row(env, 0, A2, O2, "local_s1",
              row_data(S1, "ACME-REVIEW plan final", lastActivityAt=9))
    write_row(env, 0, A3, O3, "local_pad", row_data(PAD, "Padding"))
    ct._ANONYMIZE = True
    rc = ct.cmd_converge(env, cv_ns(anonymize=True))
    out = capsys.readouterr().out
    assert rc == 0
    assert "ACME-REVIEW" not in out
    rc = ct.cmd_converge(env, cv_ns(json=True, anonymize=True))
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert "ACME-REVIEW" not in json.dumps(payload)
    entries = payload["notes"][0]["holder_titles"]
    assert len(entries) == 2
    for e in entries:
        assert e["title"].startswith("<session-")


def test_a_title_matching_a_structural_key_cannot_break_the_report(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    """anonymize_report rewrites dict KEYS too (the third bug's fix), so a
    registered title that IS a structural field name used to rename that key:
    'rows' crashed _public_converge_manifest at out['rows'], and 'title'
    dodged the field-value scrub because membership was tested on the renamed
    key. Structural keys are schema, not data - they must never be renamed,
    while the value under them still is."""
    from test_converge import cv_ns, t_entries, row_data, S1, PAD, A1, O1, A2, O2
    for colliding in ("rows", "title"):
        env = mkenv(tmp_path / colliding)
        write_transcript(env, "C--p", S1,
                         t_entries() + [{"customTitle": colliding}])
        write_row(env, 0, A1, O1, "local_s1", row_data(S1, ""))
        write_row(env, 0, A2, O2, "local_pad", row_data(PAD, "Padding"))
        ct._ANONYMIZE = True
        ct._ANON_CACHE.clear()
        rc = ct.cmd_converge(env, cv_ns(json=True, anonymize=True))
        payload = json.loads(capsys.readouterr().out)
        assert rc == 0
        assert "rows" in payload
        assert payload["rows"][0]["title"].startswith("<session-")


def test_a_transcript_derived_title_survives_nowhere_in_a_hold(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    """The full carrier set at once: two untitled conversations whose
    transcripts chose the SAME title converge on a third destination, so the
    later one holds (shape (b)) and the pair measures as a supersession. The
    chosen title is the hold's `title`, is embedded in the `detail`
    sentence, and is the base of `suggested_title` and the rendered retitle
    command - all four must carry the label, none the real string."""
    from test_converge import (cv_ns, prose_transcript, turn_labels, row_data,
                               ms_local, S1, S2, PAD, A1, O1, A2, O2, A3, O3)
    env = mkenv(tmp_path)
    private = "ACME-REVIEW session"
    shared = turn_labels("s", 48)
    write_transcript(env, "C--p", S1,
                     prose_transcript(shared + turn_labels("a", 2))
                     + [{"customTitle": private}])
    write_transcript(env, "C--p", S2,
                     prose_transcript(shared + turn_labels("b", 13))
                     + [{"customTitle": private}])
    write_row(env, 0, A1, O1, "local_s1",
              row_data(S1, "", createdAt=ms_local(2026, 8, 24),
                       lastActivityAt=ms_local(2026, 8, 28)))
    write_row(env, 0, A2, O2, "local_s2",
              row_data(S2, "", createdAt=ms_local(2026, 8, 24),
                       lastActivityAt=ms_local(2026, 8, 29)))
    write_row(env, 0, A3, O3, "local_pad", row_data(PAD, "Padding"))
    ct._ANONYMIZE = True
    rc = ct.cmd_converge(env, cv_ns(anonymize=True))
    out = capsys.readouterr().out
    assert rc == 0
    assert "ACME-REVIEW" not in out
    rc = ct.cmd_converge(env, cv_ns(json=True, anonymize=True))
    payload = capsys.readouterr().out
    assert rc == 0
    assert "ACME-REVIEW" not in payload
    holds = [h for h in json.loads(payload)["holds"]
             if h["reason"] == "held_title_collision"]
    assert len(holds) == 1
    h = holds[0]
    label = h["title"]
    assert label.startswith("<session-")
    # One string, one stable label - the detail sentence and the generated
    # suggestion carry the SAME label the title field got.
    assert label in h["detail"]
    assert h["measured"]["suggested_title"].startswith(label)
    assert "earlier leg" in h["measured"]["suggested_title"]
    assert label in h["retitle"]
    assert h["measured"]["command_runnable"] is False
