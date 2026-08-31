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


def test_a_title_colliding_with_an_alignment_entry_key_leaves_it_alone(
        mkenv, tmp_path, write_row, capsys):
    """Round-2 catch: the structural set missed alignment's disagree-entry
    keys (row, opens, short_of_all_accounts), so a stored title 'opens'
    renamed that key and silently changed the --json schema."""
    from test_converge import row_data, A1, O1, A2, O2
    env = mkenv(tmp_path)
    write_row(env, 0, A1, O1, "local_1",
              {"sessionId": "local_1", "cliSessionId": "s-1",
               "cwd": "C:\\p", "title": "opens", "lastActivityAt": 9})
    write_row(env, 0, A2, O2, "local_1",
              {"sessionId": "local_1", "cliSessionId": "s-2",
               "cwd": "C:\\p", "title": "Something else", "lastActivityAt": 9})
    ct._ANONYMIZE = True
    rc = ct.cmd_alignment(env, ns(anonymize=True, json=True))
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    entry = payload["consistent"]["rows"][0]
    assert set(entry) == {"row", "opens", "short_of_all_accounts"}


def test_the_only_filter_is_not_echoed_into_anonymized_output(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    """Round-2 catch: --only is a user-typed TITLE SUBSTRING and the
    manifest echoed it back verbatim - a substring matches no whole
    registered title, so no table can cover it. It becomes a fixed marker,
    the same move retitle makes with <proposed-title>; scoped-ness stays
    readable in complete.scoped."""
    from test_converge import cv_ns, t_entries, row_data, S1, PAD, A1, O1, A2, O2
    env = mkenv(tmp_path)
    write_transcript(env, "C--p", S1, t_entries())
    write_row(env, 0, A1, O1, "local_s1",
              row_data(S1, "ACME-REVIEW offboarding escalation"))
    write_row(env, 0, A2, O2, "local_pad", row_data(PAD, "Padding"))
    ct._ANONYMIZE = True
    rc = ct.cmd_converge(env, cv_ns(json=True, anonymize=True,
                                    only="offboarding"))
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert "offboarding" not in json.dumps(payload)
    assert payload["only"] == "<only-filter>"
    assert payload["complete"]["scoped"] is True


# Both >52 characters, so the refusal listings' truncation bites: a
# whole-title pair cannot match a title cut at 52, the first bug's exact
# mechanism resurfacing on the stderr path.
LONG_A = ("ACME-REVIEW quarterly offboarding retrospective for the "
          "northern division")
LONG_B = ("ACME-REVIEW quarterly offboarding retrospective for the "
          "southern division")


def test_a_refusal_does_not_echo_the_only_filter(
        mkenv, tmp_path, write_transcript, write_row):
    """Round-3 catch: --anonymize never reached REFUSAL output. The
    zero-match refusals of converge and retitle embedded the user-typed
    --only substring verbatim - a substring no whole-title pair can cover -
    so it becomes the manifest's own <only-filter> marker at composition."""
    from test_converge import t_entries, row_data, S1, A1, O1
    env = mkenv(tmp_path)
    write_transcript(env, "C--p", S1, t_entries())
    write_row(env, 0, A1, O1, "local_s1", row_data(S1, "ACME-REVIEW plan"))
    ct._ANONYMIZE = True
    with pytest.raises(ct.Refusal) as exc:
        ct.plan_converge(env, ct.ConvergeFlags(only="zz-off-record"))
    assert "zz-off-record" not in str(exc.value)
    assert "<only-filter>" in str(exc.value)
    with pytest.raises(ct.Refusal) as exc:
        ct.plan_retitle(env, ct.RetitleFlags(only="zz-off-record",
                                             title="New name"))
    assert "zz-off-record" not in str(exc.value)
    assert "<only-filter>" in str(exc.value)


def test_a_refusal_listing_shows_labels_not_truncated_titles(
        mkenv, tmp_path, write_row):
    """The ambiguous-match refusal lists candidates with titles cut to 52
    characters BEFORE any redaction ran, so a longer stored title leaked
    its prefix - anonymize-then-truncate, the structured pass's own
    lesson, applied to the listing."""
    from test_converge import row_data, S1, S2, A1, O1
    env = mkenv(tmp_path)
    write_row(env, 0, A1, O1, "local_s1", row_data(S1, LONG_A))
    write_row(env, 0, A1, O1, "local_s2", row_data(S2, LONG_B))
    ct._ANONYMIZE = True
    with pytest.raises(ct.Refusal) as exc:
        ct.plan_converge(env, ct.ConvergeFlags(only="offboarding"))
    text = str(exc.value)
    assert "ACME-REVIEW" not in text
    assert "<session-" in text
    assert "<only-filter>" in text


def test_a_repoint_refusal_listing_is_covered_too(
        two_account_env, tmp_path, write_row):
    """repoint composes its own inline candidate listing with the same
    52-character cut - the same treatment applies."""
    from test_converge import row_data, S1, S2, S3, A1, O1
    env, _src, _dst = two_account_env(tmp_path)
    write_row(env, 0, A1, O1, "local_a", row_data(S1, LONG_A))
    write_row(env, 0, A1, O1, "local_b", row_data(S2, LONG_B))
    ct._ANONYMIZE = True
    with pytest.raises(ct.Refusal) as exc:
        ct.plan_repoint(env, ct.RepointFlags(only="offboarding",
                                             to_session=S3))
    text = str(exc.value)
    assert "ACME-REVIEW" not in text
    assert "<only-filter>" in text


def test_listing_labels_scrub_the_account_email():
    """Holder labels in refusal listings carry the account email; under
    --anonymize they get the same account label the manifest fields do."""
    ct._ANONYMIZE = True
    assert ct._listing_label(
        "alice@example.com (aaaaaaaa/bbbbbbbb)").startswith("<account-")
    ct._ANONYMIZE = False
    assert ct._listing_label("alice@example.com (x)") == "alice@example.com (x)"


# The dict keys under these names are DATA (titles, account labels or uuids,
# stringified counts), not schema - the walker below skips membership checks
# for their whole subtree, mirroring anonymize_report's own key handling.
DATA_KEYED_PARENTS = {"titles", "per_account", "opens", "accounts",
                      "by_account_count"}


def walk_fixed_keys(obj, in_data=False, parent=None, bad=None):
    if bad is None:
        bad = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if not in_data and isinstance(k, str) \
                    and k not in ct._ANON_STRUCTURAL_KEYS:
                bad.append((parent, k))
            # Stickiness applies to dict-valued children only: alignment's
            # `accounts` is a LIST of structural entries while doctor
            # reuses the same name for a dict keyed by account uuid, and
            # blanket stickiness left the list's fixed keys unpinned.
            walk_fixed_keys(v, in_data or (isinstance(v, dict)
                                           and k in DATA_KEYED_PARENTS),
                            k, bad)
    elif isinstance(obj, list):
        for v in obj:
            walk_fixed_keys(v, in_data, parent, bad)
    return bad


def test_every_emitted_report_key_is_in_the_structural_set(
        mkenv, two_account_env, tmp_path, write_transcript, write_row):
    """The mechanical pin two round-2 reviewers asked for: the structural
    set's comment claims the fixed field vocabulary of every report
    anonymize_report traverses, and until now that claim was editorial. This
    walks the reports the fixtures below actually emit - list, alignment,
    doctor, and the public converge, retitle, new-row, repoint and sync
    manifests, the full set the structural-keys comment names - and fails
    on any fixed key the set does not know. Coverage is exactly the shapes
    these fixtures produce; a field only richer state emits still needs
    adding by hand."""
    from test_converge import measured_pair, row_data, t_entries, A1, O1, A2, O2
    env = mkenv(tmp_path / "reports")
    # Reachable + duplicate-title pair (doctor's group shapes need real
    # transcripts), a dead row, a blank row, and an unlisted transcript.
    write_transcript(env, "C--p", "s-1", t_entries())
    write_transcript(env, "C--p", "s-2", t_entries())
    write_transcript(env, "C--p", "s-orphan", t_entries())
    for n, sid in (("local_1", "s-1"), ("local_2", "s-2")):
        write_row(env, 0, A1, O1, n,
                  {"sessionId": n, "cliSessionId": sid, "cwd": "C:\\p",
                   "title": "Quarterly board report", "lastActivityAt": 9})
    write_row(env, 0, A2, O2, "local_1",
              {"sessionId": "local_1", "cliSessionId": "s-2",
               "cwd": "C:\\p", "title": "Another name", "lastActivityAt": 9})
    write_row(env, 0, A1, O1, "local_dead",
              {"sessionId": "local_dead", "cliSessionId": "s-gone",
               "cwd": "C:\\p", "title": "Dead", "lastActivityAt": 9})
    write_row(env, 0, A1, O1, "local_blank",
              {"sessionId": "local_blank", "cwd": "C:\\p",
               "title": "Blank", "lastActivityAt": 9})
    bad = walk_fixed_keys(ct.gather_list(env))
    bad = walk_fixed_keys(ct.gather_alignment(env), bad=bad)
    bad = walk_fixed_keys(ct.gather_doctor(env), bad=bad)
    mr = ct.plan_retitle(env, ct.RetitleFlags(only="s-1",
                                              title="Renamed by the pin"))
    bad = walk_fixed_keys(ct._public_retitle_manifest(env, mr), bad=bad)
    env2 = mkenv(tmp_path / "converge")
    measured_pair(env2, write_transcript, write_row, third_dest=True)
    m = ct._public_converge_manifest(env2, ct.plan_converge(env2, ct.ConvergeFlags()))
    bad = walk_fixed_keys(m, bad=bad)
    env3 = mkenv(tmp_path / "manifests")
    sid = "abcdef01-9abc-def0-1234-56789abcdef0"
    write_transcript(env3, "C--p", sid, t_entries(cwd="C:\\p"))
    write_row(env3, 0, A1, O1, "local_p",
              {"sessionId": "local_p", "cliSessionId": "other-conversation",
               "title": "Padding", "lastActivityAt": 9})
    _signed_in(env3)
    mn = ct.plan_new_row(env3, ct.NewRowFlags(to_session=sid))
    bad = walk_fixed_keys(ct._public_new_row_manifest(env3, mn), bad=bad)
    mp = ct.plan_repoint(env3, ct.RepointFlags(only="Padding",
                                               to_session=sid))
    bad = walk_fixed_keys(ct._public_repoint_manifest(env3, mp), bad=bad)
    env4, _src, _dst = two_account_env(tmp_path / "sync")
    write_transcript(env4, "C--p", sid, t_entries(cwd="C:\\p"))
    write_row(env4, 0, A1, O1, "local_1",
              {"sessionId": "local_1", "cliSessionId": sid,
               "title": "Quarterly board report", "lastActivityAt": 9})
    write_row(env4, 0, A2, O2, "local_2",
              {"sessionId": "local_2", "cliSessionId": sid,
               "title": "Parked", "lastActivityAt": 9})
    ms = ct.plan_sync(env4, ct.SyncFlags())
    bad = walk_fixed_keys(ct._public_manifest(env4, ms), bad=bad)
    assert bad == []


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


def test_anonymized_json_truncates_uuid_shaped_ids(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    """Anonymized JSON borrows redact's OTHER machine-layer rule too:
    uuid-shaped ids - session, account and org, standalone or inside store
    paths - truncate to their 8-character prefix, exactly as every anonymized
    plain-text line always has. Anonymized output exists to be pasted, and a
    full account uuid outlives the paste. Plain --json without the flag stays
    deliberately unredacted."""
    from test_converge import A1, S1, cv_ns, measured_pair
    env = mkenv(tmp_path)
    measured_pair(env, write_transcript, write_row)
    # Sanity, so the truncation assertions cannot pass vacuously: the
    # unanonymized JSON really does carry the full ids.
    rc = ct.cmd_converge(env, cv_ns(json=True))
    payload = capsys.readouterr().out
    assert rc == 0
    assert S1 in payload and A1 in payload

    ct._ANONYMIZE = True
    rc = ct.cmd_converge(env, cv_ns(json=True, anonymize=True))
    payload = capsys.readouterr().out
    assert rc == 0
    assert S1 not in payload and A1 not in payload
    assert ct._UUID_RE.search(payload) is None
    assert S1[:8] in payload and A1[:8] in payload

    # And `list`, the other everyday paste surface.
    seed(env, write_transcript, write_row)
    ct._ANON_CACHE.clear()
    ct.cmd_list(env, ns(anonymize=True, json=True))
    payload = capsys.readouterr().out
    assert S1 not in payload
    assert ct._UUID_RE.search(payload) is None
