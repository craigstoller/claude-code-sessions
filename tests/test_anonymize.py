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
        ct._ANONYMIZE = False
        ct._ANON_CACHE.clear()
