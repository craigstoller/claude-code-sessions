"""Tests for `--anonymize`.

Three of these pin bugs the first implementation actually had. All three were
the same shape - output that LOOKED anonymised while still carrying the private
string - which is worse than no feature at all, because it invites the paste.
"""
import json
import os
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


def test_a_rowless_transcripts_cwd_does_not_leak_from_list(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    """The substitution table learned cwds from store ROWS, but `list` also
    reports transcripts no row points at, reading their cwd from the
    transcript itself - so a client-naming cwd survived --anonymize, in text
    and in --json, exactly when no sidebar had registered it."""
    env = mkenv(tmp_path)
    seed(env, write_transcript, write_row)   # one registered, row-backed pair
    write_transcript(env, "C--clients-GhostClient", "0abc1234-9abc-def0-1234-56789abcdef0",
                     [{"cwd": "C:\\clients\\GhostClient"}])

    # Sanity: without the flag the cwd really is in the report.
    ct.cmd_list(env, ns())
    assert "GhostClient" in capsys.readouterr().out

    ct._ANONYMIZE = True
    ct._ANON_CACHE.clear()
    ct.cmd_list(env, ns(anonymize=True))
    out = capsys.readouterr().out
    assert "GhostClient" not in out
    assert "<project-" in out
    ct.cmd_list(env, ns(anonymize=True, json=True))
    payload = capsys.readouterr().out
    assert "GhostClient" not in payload
    assert "<project-" in payload


def test_a_duplicated_transcript_hold_does_not_leak_the_project_folder(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    """The transcript-unusable hold quotes _transcript_facts' refusal, and the
    duplicate-transcript refusal quotes full transcript paths. A path names its
    project folder, and the folder is the cwd wearing the app's encoding
    ("C--clients-Northwind") - a form the substitution table's whole-path and
    title pairs never match - so the client's name survived both the text
    scrub and the --json one while everything around it looked anonymised."""
    from test_converge import A1, A2, O1, O2, S1, S2, cv_ns, row_data, t_entries
    env = mkenv(tmp_path)
    # The same conversation in TWO project folders: _transcript_facts refuses,
    # quoting both paths. The client-naming folder is what must not survive.
    write_transcript(env, "C--clients-Northwind", S1,
                     t_entries(cwd="C:\\clients\\Northwind"))
    write_transcript(env, "C--Users-u-scratch", S1,
                     t_entries(cwd="C:\\clients\\Northwind"))
    write_row(env, 0, A1, O1, "local_1",
              row_data(S1, "ACME-REVIEW handoff", cwd="C:\\clients\\Northwind"))
    # A2 is a destination via an unrelated, healthy conversation.
    write_transcript(env, "C--p", S2, t_entries(cwd="C:\\p"))
    write_row(env, 0, A2, O2, "local_2", row_data(S2, "Padding", cwd="C:\\p"))

    # Sanity, so the assertions below cannot pass vacuously: the raw plan
    # really does carry the folder name inside a hold detail.
    m = ct.plan_converge(env, ct.ConvergeFlags())
    held = [h for h in m["holds"] if h["reason"] == "held_transcript_unusable"]
    assert held and all("C--clients-Northwind" in h["detail"] for h in held)

    ct._ANONYMIZE = True
    rc = ct.cmd_converge(env, cv_ns(anonymize=True))
    out = capsys.readouterr().out
    assert rc == 0
    assert "held_transcript_unusable" in out
    assert "Northwind" not in out

    rc = ct.cmd_converge(env, cv_ns(json=True, anonymize=True))
    payload = capsys.readouterr().out
    assert rc == 0
    assert any(h["reason"] == "held_transcript_unusable"
               for h in json.loads(payload)["holds"])
    assert "Northwind" not in payload


A1 = "aaaaaaaa-0000-0000-0000-000000000001"
O1 = "bbbbbbbb-0000-0000-0000-000000000002"


def _signed_in(env):
    with open(os.path.join(env.home, ".claude.json"), "w",
              encoding="utf-8") as fh:
        json.dump({"oauthAccount": {"accountUuid": A1,
                                    "organizationUuid": O1,
                                    "emailAddress": "alice@example.com"}}, fh)


def test_new_row_and_repoint_accept_anonymize():
    """README: --anonymize is available on every command. These two defined
    their own --verbose instead of taking the common flags, so --anonymize
    was an argparse error on exactly the commands whose plans quote
    transcript-derived content."""
    p = ct.build_parser()
    assert p.parse_args(["new-row", "--to", "x", "--anonymize"]).anonymize
    assert p.parse_args(["repoint", "--only", "t", "--to", "x",
                         "--anonymize"]).anonymize


def test_new_row_anonymize_covers_transcript_derived_content(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    """new-row's whole job is conversations no row registers, so its planned
    title (the transcript's customTitle) and cwd exist in no substitution
    pair - and its JSON manifest never met the anonymizer at all."""
    sid = "abcdef01-9abc-def0-1234-56789abcdef0"
    env = mkenv(tmp_path)
    write_transcript(env, "C--clients-GhostClient", sid, [
        {"cwd": "C:\\clients\\GhostClient",
         "timestamp": "2026-08-01T00:00:00.000Z", "type": "user",
         "message": {"role": "user", "content": "hello"}},
        {"timestamp": "2026-08-01T00:10:00.000Z", "type": "assistant",
         "message": {"role": "assistant", "model": "claude-opus-5",
                     "content": [{"type": "text", "text": "hi"}]}},
        {"customTitle": "GHOST-TITLE ghost intake",
         "timestamp": "2026-08-01T00:11:00.000Z"},
    ])
    write_row(env, 0, A1, O1, "local_p",
              {"sessionId": "local_p", "cliSessionId": "other-conversation",
               "title": "Padding", "lastActivityAt": 9})
    _signed_in(env)

    def nr(**kw):
        return ns(to_session=sid, store="", title="", live="", apply=False,
                  **kw)

    # Sanity: the raw plan really carries both.
    ct.cmd_new_row(env, nr(json=True))
    raw = capsys.readouterr().out
    assert "GHOST-TITLE" in raw and "GhostClient" in raw

    ct._ANONYMIZE = True
    ct._ANON_CACHE.clear()
    rc = ct.cmd_new_row(env, nr())
    out = capsys.readouterr().out
    assert rc == 0
    assert "GHOST-TITLE" not in out and "GhostClient" not in out
    assert "<session-" in out and "<project-" in out
    rc = ct.cmd_new_row(env, nr(json=True))
    payload = capsys.readouterr().out
    assert rc == 0
    assert "GHOST-TITLE" not in payload and "GhostClient" not in payload
    assert "<session-" in payload


def test_repoint_anonymize_covers_the_row_title(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    target = "87654321-9abc-def0-1234-56789abcdef0"
    env = mkenv(tmp_path)
    write_transcript(env, "C--p", target, [{"cwd": "C:\\p"}])
    write_row(env, 0, A1, O1, "local_1",
              {"sessionId": "local_1", "cliSessionId": "current-conversation",
               "title": "ACME-REVIEW pointer", "lastActivityAt": 9})
    _signed_in(env)

    def rp(**kw):
        return ns(only="ACME-REVIEW pointer", to_session=target, store="",
                  live="", apply=False, **kw)

    ct.cmd_repoint(env, rp(json=True))
    assert "ACME-REVIEW" in capsys.readouterr().out

    ct._ANONYMIZE = True
    ct._ANON_CACHE.clear()
    rc = ct.cmd_repoint(env, rp())
    out = capsys.readouterr().out
    assert rc == 0
    assert "ACME-REVIEW" not in out and "<session-" in out
    rc = ct.cmd_repoint(env, rp(json=True))
    payload = capsys.readouterr().out
    assert rc == 0
    assert "ACME-REVIEW" not in payload and "<session-" in payload


def _strings(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)
    elif isinstance(obj, str):
        yield obj


def test_anonymize_json_folds_the_home_directory(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    """Plain --json is deliberately unredacted (README) and must stay so. But
    --anonymize output is a display artifact for pasting, and a payload that
    hides every title while printing C:\\Users\\<name>\\... is a partial
    safety that reads as whole. Under the flag - and only under it - every
    string in the structured output folds the home directory to ~."""
    from test_converge import A1, A2, O1, O2, S1, S2, cv_ns, row_data, t_entries
    env = mkenv(tmp_path)
    # The duplicated-transcript hold: its detail quotes home-prefixed paths.
    write_transcript(env, "C--clients-Northwind", S1, t_entries())
    write_transcript(env, "C--scratch-x", S1, t_entries())
    write_row(env, 0, A1, O1, "local_1", row_data(S1, "ACME-REVIEW handoff"))
    write_transcript(env, "C--p", S2, t_entries(cwd="C:\\p"))
    write_row(env, 0, A2, O2, "local_2", row_data(S2, "Padding", cwd="C:\\p"))

    # The documented contract, unchanged: plain --json carries full paths.
    ct.cmd_converge(env, cv_ns(json=True))
    raw = json.loads(capsys.readouterr().out)
    assert any(env.home in s for s in _strings(raw))

    ct._ANONYMIZE = True
    ct._ANON_CACHE.clear()
    rc = ct.cmd_converge(env, cv_ns(json=True, anonymize=True))
    pub = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert not any(env.home in s for s in _strings(pub))
    # Folded, not deleted: the hold detail still shows the ~-relative path.
    held = [h for h in pub["holds"]
            if h["reason"] == "held_transcript_unusable"]
    assert held and "~" in held[0]["detail"]

    # And the other manifest that quotes a transcript path: new-row's.
    _signed_in(env)
    sid = "abcdef01-9abc-def0-1234-56789abcdef0"
    write_transcript(env, "C--p", sid, t_entries(cwd="C:\\p"))
    rc = ct.cmd_new_row(env, ns(to_session=sid, store="", title="", live="",
                                apply=False, json=True))
    pub = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert not any(env.home in s for s in _strings(pub))
    assert pub["transcript"].startswith("~")


def test_sync_anonymize_covers_addresses_titles_and_images(
        two_account_env, tmp_path, write_transcript, capsys):
    """sync was the one --json emitter with no anonymize pass at all: the two
    account addresses printed in the clear (text mode too - an address is
    never a substitution pair), row titles sat in rows[].title AND in the
    tally's title lists, and the row images (post_b64, pre_b64 on refreshes)
    are base64 blobs embedding every title they carry - unreadable in a
    paste, so they are dropped outright under the flag."""
    env, src, dst = two_account_env(tmp_path)
    sid = "abcdef01-9abc-def0-1234-56789abcdef0"
    write_transcript(env, "C--p", sid, [{"cwd": "C:\\p"}])
    with open(os.path.join(src, "local_1.json"), "w", encoding="utf-8") as fh:
        json.dump({"sessionId": "local_1", "cliSessionId": sid,
                   "title": "ACME-REVIEW matter", "lastActivityAt": 9}, fh)
    # A row already in the destination: its title lands in tally["present"].
    for d in (src, dst):
        with open(os.path.join(d, "local_2.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"sessionId": "local_2", "cliSessionId": sid,
                       "title": "ACME-REVIEW parked", "lastActivityAt": 9}, fh)

    def sy(**kw):
        return ns(to="", only="", update=False, newer_only=False,
                  allow_orphan=False, include_deleted=(), verbatim=False,
                  live="", apply=False, **kw)

    # Sanity: the raw JSON carries all three shapes, per its documented
    # unredacted contract.
    ct.cmd_sync(env, sy(json=True))
    raw = json.loads(capsys.readouterr().out)
    assert raw["source_email"] == "me@example.com"
    assert any("ACME-REVIEW" in s for s in _strings(raw))
    assert any("post_b64" in r for r in raw["rows"])

    ct._ANONYMIZE = True
    ct._ANON_CACHE.clear()
    rc = ct.cmd_sync(env, sy())
    out = capsys.readouterr().out
    assert rc == 0
    assert "me@example.com" not in out and "<account-" in out
    assert "ACME-REVIEW" not in out
    rc = ct.cmd_sync(env, sy(json=True))
    pub = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert not any("me@example.com" in s for s in _strings(pub))
    assert not any("ACME-REVIEW" in s for s in _strings(pub))
    assert not any("pre_b64" in r or "post_b64" in r for r in pub["rows"])
    assert "<session-" in json.dumps(pub) and pub["source_email"].startswith(
        "<account-")


def test_undo_preview_anonymizes_manifest_titles_and_addresses(
        mkenv, tmp_path, capsys):
    """undo previews print op-manifest values through the line pass only, and
    an op manifest can carry content the substitution table cannot know: a
    retitle's new_title after a later rename, a sync destination's address.
    Direct labels, byte-identical to the table's when both know the value."""
    env = mkenv(tmp_path)
    op1 = ct.new_op(env, {"op_type": "retitle", "new_title": "SECRET rename",
                          "rows": [{"written": True}]})
    ct.set_status(op1, "completed")
    op2 = ct.new_op(env, {"op_type": "sync",
                          "dest_email": "alice@example.com",
                          "dest_account": "cccccccc-0000-0000-0000-000000000003",
                          "rows": []})
    ct.set_status(op2, "completed")

    def undo_ns(op):
        return ns(apply=False, show=False, op_id=op.manifest["op_id"])

    ct.cmd_undo(env, undo_ns(op1))
    assert "SECRET rename" in capsys.readouterr().out

    ct._ANONYMIZE = True
    ct._ANON_CACHE.clear()
    rc = ct.cmd_undo(env, undo_ns(op1))
    out = capsys.readouterr().out
    assert rc == 0
    assert "SECRET rename" not in out and "<session-" in out
    rc = ct.cmd_undo(env, undo_ns(op2))
    out = capsys.readouterr().out
    assert rc == 0
    assert "alice@example.com" not in out and "<account-" in out


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
