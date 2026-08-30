"""Tests for `ccs converge`.

Implements docs/specs/2026-08-26-converge-design.md's Testing section. Every
title is from the fake cast (ACME-REVIEW, Northwind, "Quarterly board report
finalization") - never a real one.
"""
import json
import os
import types

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
A4 = "99999999-0000-0000-0000-000000000009"
O4 = "88888888-0000-0000-0000-000000000008"

S1 = "12345678-9abc-def0-1234-56789abcdef0"
S2 = "87654321-9abc-def0-1234-56789abcdef0"
S3 = "abcdef01-9abc-def0-1234-56789abcdef0"
# A deliberately DEAD conversation (no transcript is ever written for it).
# Tests park a row opening it in an account to make that account a
# destination - populated, but contributing no eligible pairs of its own.
PAD = "0dead000-9abc-def0-1234-56789abcdef0"


class SimulatedCrash(Exception):
    pass


def _monotonic_now(env, start=1_800_000_000.0):
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


def t_entries(cwd="C:\\Users\\u\\Projects\\Northwind"):
    """A transcript _transcript_facts can populate a row from: a cwd, usable
    timestamps, and an assistant record carrying the model."""
    return [
        {"cwd": cwd, "timestamp": "2026-08-01T00:00:00.000Z", "type": "user",
         "message": {"role": "user", "content": "hello"}},
        {"timestamp": "2026-08-01T00:10:00.000Z", "type": "assistant",
         "message": {"role": "assistant", "model": "claude-opus-5",
                     "content": [{"type": "text", "text": "hi there"}]}},
    ]


def cv_ns(**kw):
    d = {"json": False, "verbose": False, "anonymize": False, "apply": False,
         "only": "", "live": ""}
    d.update(kw)
    return types.SimpleNamespace(**d)


def undo_ns(**kw):
    d = {"json": False, "verbose": False, "anonymize": False, "apply": False,
         "show": False, "op_id": None}
    d.update(kw)
    return types.SimpleNamespace(**d)


def store_rows(env, acct, org):
    d = os.path.join(env.store_candidates[0], acct, org)
    return sorted(n for n in os.listdir(d)
                  if n.startswith("local_") and n.endswith(".json"))


def mixed_holdings(env, write_transcript, write_row):
    """The spec's first scenario: three accounts, mixed holdings.
    S1 held by A1 only (missing A2, A3); S2 held by A1+A2 (missing A3);
    S3 held by all three (complete). All transcripts exist."""
    for sid in (S1, S2, S3):
        write_transcript(env, "C--p", sid, t_entries())
    write_row(env, 0, A1, O1, "local_s1", row_data(S1, "ACME-REVIEW"))
    write_row(env, 0, A1, O1, "local_s2",
              row_data(S2, "Quarterly board report finalization"))
    write_row(env, 0, A2, O2, "local_s2",
              row_data(S2, "Quarterly board report finalization"))
    for acct, org in ((A1, O1), (A2, O2), (A3, O3)):
        write_row(env, 0, acct, org, "local_s3",
                  row_data(S3, "Northwind intake notes"))


def desktop_config(env, account_uuid):
    """The desktop's config.json, beside the store root - the exact location
    _identity_disagreement reads (os.path.dirname(candidate))."""
    cfg = os.path.join(os.path.dirname(env.store_candidates[0]), "config.json")
    with open(cfg, "w", encoding="utf-8") as fh:
        json.dump({"lastKnownAccountUuid": account_uuid}, fh)


def identity_files(env, oauth_acct, config_acct, email="alice@example.com",
                   oauth_org=""):
    """The spec's identity-file fixture: ~/.claude.json's oauthAccount names
    one account, config.json names the other (or the same, for agreement)."""
    with open(os.path.join(env.home, ".claude.json"), "w",
              encoding="utf-8") as fh:
        json.dump({"oauthAccount": {"accountUuid": oauth_acct,
                                    "organizationUuid": oauth_org,
                                    "emailAddress": email}}, fh)
    desktop_config(env, config_acct)


def agent_mode_email(env, account_uuid, email):
    """A per-account agent-mode sandbox config naming ACCOUNT_UUID's email -
    the source account_email recovers a dormant account's address from."""
    d = os.path.join(os.path.dirname(env.store_candidates[0]),
                     "local-agent-mode-sessions", account_uuid,
                     "dddddddd-0000-0000-0000-000000000004",
                     "agent", "local_ditto_x", ".claude")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, ".claude.json"), "w", encoding="utf-8") as fh:
        json.dump({"oauthAccount": {"accountUuid": account_uuid,
                                    "emailAddress": email}}, fh)


# ------------------------------------------------------------------ planning

def test_plan_enumerates_exactly_the_missing_pairs(mkenv, tmp_path,
                                                   write_transcript,
                                                   write_row):
    env = mkenv(tmp_path)
    mixed_holdings(env, write_transcript, write_row)
    m = ct.plan_converge(env, ct.ConvergeFlags())
    assert m["op_type"] == "converge"
    pairs = sorted((r["session"], r["account"]) for r in m["rows"])
    assert pairs == [(S1, A2), (S1, A3), (S2, A3)]
    assert m["holds"] == []
    # Truthful completeness math: 1 of 3 now, 3 of 3 projected, 0 held.
    assert m["complete"] == {"now": 1, "of": 3, "after": 3, "held": 0,
                             "scoped": False}
    # Planning writes nothing and journals nothing.
    assert store_rows(env, A2, O2) == ["local_s2.json", "local_s3.json"]
    assert ct.list_ops(env) == []


def test_plan_rows_synthesize_from_the_transcript_not_the_holder(
        mkenv, tmp_path, write_transcript, write_row):
    """Converge copies NO per-account fields from the holder: the row is
    new-row's synthesis with the title forced to the plan's choice."""
    env = mkenv(tmp_path)
    write_transcript(env, "C--p", S1, t_entries())
    write_row(env, 0, A1, O1, "local_s1",
              row_data(S1, "ACME-REVIEW", isArchived=True,
                       lastFocusedAt=999, chromeTabGroupId=7))
    write_row(env, 0, A2, O2, "local_other", row_data(S2, "Northwind"))
    write_transcript(env, "C--p", S2, t_entries())
    m = ct.plan_converge(env, ct.ConvergeFlags(only=S1[:8]))
    row = json.loads(ct.unb64(m["rows"][0]["post_b64"]).decode("utf-8"))
    assert row["cliSessionId"] == S1
    assert row["title"] == "ACME-REVIEW"
    assert row["isArchived"] is False           # not the holder's shelving
    assert "chromeTabGroupId" not in row        # nothing cloned
    assert row["model"] == "claude-opus-5"      # read from the transcript
    assert row["cwd"] == "C:\\Users\\u\\Projects\\Northwind"


def test_duplicate_rows_in_one_account_are_one_holding(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    write_transcript(env, "C--p", S1, t_entries())
    write_row(env, 0, A1, O1, "local_a", row_data(S1, "ACME-REVIEW"))
    write_row(env, 0, A1, O1, "local_b", row_data(S1, "ACME-REVIEW"))
    write_row(env, 0, A2, O2, "local_c", row_data(S1, "ACME-REVIEW"))
    write_row(env, 0, A3, O3, "local_d", row_data(S1, "ACME-REVIEW"))
    m = ct.plan_converge(env, ct.ConvergeFlags())
    # A1 reaches it - the extra row is not a missing pair.
    assert m["rows"] == []
    assert m["complete"]["now"] == 1


def test_storekey_populated_org_receives_the_row(mkenv, tmp_path,
                                                 write_transcript, write_row):
    """An account with several org directories receives its row in the
    populated one."""
    env = mkenv(tmp_path)
    write_transcript(env, "C--p", S1, t_entries())
    write_row(env, 0, A2, O2, "local_s1", row_data(S1, "ACME-REVIEW"))
    write_row(env, 0, A1, O1, "local_x", row_data(S2, "Northwind"))
    write_transcript(env, "C--p", S2, t_entries())
    # A1 also owns an EMPTY sibling org directory - scaffolding.
    os.makedirs(os.path.join(env.store_candidates[0], A1, O2))
    m = ct.plan_converge(env, ct.ConvergeFlags(only=S1[:8]))
    dest = [r for r in m["rows"] if r["account"] == A1]
    assert len(dest) == 1
    assert os.path.normcase(dest[0]["store_path"]) == os.path.normcase(
        os.path.join(env.store_candidates[0], A1, O1))


def test_storekey_empty_account_is_not_a_destination_and_the_plan_says_so(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    env = mkenv(tmp_path)
    mixed_holdings(env, write_transcript, write_row)
    # A4 exists on disk with an org directory but holds nothing anywhere.
    os.makedirs(os.path.join(env.store_candidates[0], A4, O4))
    m = ct.plan_converge(env, ct.ConvergeFlags())
    assert not any(r["account"] == A4 for r in m["rows"])
    assert [nd["account"] for nd in m["non_destinations"]] == [A4]
    # And the completeness denominator counts only the three real accounts.
    assert m["complete"] == {"now": 1, "of": 3, "after": 3, "held": 0,
                             "scoped": False}
    rc = ct.cmd_converge(env, cv_ns())
    out = capsys.readouterr().out
    assert rc == 0
    assert "NOT a destination" in out


def test_multi_populated_org_refuses_rather_than_guessing(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    write_transcript(env, "C--p", S1, t_entries())
    write_row(env, 0, A1, O1, "local_a", row_data(S1, "ACME-REVIEW"))
    # A1 holds rows in TWO org directories - the populated-one rule cannot
    # resolve which is real.
    write_row(env, 0, A1, O2, "local_b", row_data(S2, "Northwind"))
    write_transcript(env, "C--p", S2, t_entries())
    with pytest.raises(ct.Refusal, match="more than one org directory"):
        ct.plan_converge(env, ct.ConvergeFlags())


def test_dead_only_conversation_excluded_from_work_and_denominator(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    mixed_holdings(env, write_transcript, write_row)
    # A conversation with rows everywhere and NO transcript: doctor's dead-row
    # report, not convergence.
    for acct, org in ((A1, O1), (A2, O2)):
        write_row(env, 0, acct, org, "local_dead",
                  row_data("deaddead-9abc-def0-1234-56789abcdef0",
                           "Northwind offboarding"))
    m = ct.plan_converge(env, ct.ConvergeFlags())
    assert not any(r["session"].startswith("deaddead") for r in m["rows"])
    assert m["complete"]["of"] == 3          # not 4
    assert m["dead_excluded"] == 1


def test_orphan_transcript_generates_no_pair(mkenv, tmp_path,
                                             write_transcript, write_row):
    """A transcript with no row anywhere stays retired - first rows are
    new-row's job."""
    env = mkenv(tmp_path)
    mixed_holdings(env, write_transcript, write_row)
    write_transcript(env, "C--p", "0abcdef0-9abc-def0-1234-56789abcdef0",
                     t_entries())
    m = ct.plan_converge(env, ct.ConvergeFlags())
    assert not any(r["session"].startswith("0abcdef0") for r in m["rows"])
    assert m["complete"]["of"] == 3


# ---------------------------------------------------------------- title rule

def test_title_choice_greatest_last_activity_wins_and_note_appears(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    write_transcript(env, "C--p", S1, t_entries())
    write_row(env, 0, A1, O1, "local_s1",
              row_data(S1, "ACME-REVIEW", lastActivityAt=200))
    write_row(env, 0, A2, O2, "local_s1",
              row_data(S1, "Quarterly board report finalization",
                       lastActivityAt=100))
    write_row(env, 0, A3, O3, "local_pad", row_data(PAD, "Padding"))
    m = ct.plan_converge(env, ct.ConvergeFlags())
    assert [r["title"] for r in m["rows"]] == ["ACME-REVIEW"]
    assert len(m["notes"]) == 1
    note = m["notes"][0]
    assert note["title"] == "ACME-REVIEW"
    assert note["retitle"] == ('claude-code-sessions retitle --only {0} '
                               '--title "ACME-REVIEW" --apply'.format(S1[:8]))


def test_title_missing_activity_compares_as_zero(mkenv, tmp_path,
                                                 write_transcript, write_row):
    env = mkenv(tmp_path)
    write_transcript(env, "C--p", S1, t_entries())
    d = row_data(S1, "ACME-REVIEW")
    del d["lastActivityAt"]                       # missing -> zero
    write_row(env, 0, A1, O1, "local_s1", d)
    write_row(env, 0, A2, O2, "local_s1",
              row_data(S1, "Northwind kickoff", lastActivityAt=1))
    write_row(env, 0, A3, O3, "local_pad", row_data(PAD, "Padding"))
    m = ct.plan_converge(env, ct.ConvergeFlags())
    assert [r["title"] for r in m["rows"]] == ["Northwind kickoff"]


def test_title_exact_tie_breaks_to_greatest_account_uuid(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    write_transcript(env, "C--p", S1, t_entries())
    write_row(env, 0, A1, O1, "local_s1",
              row_data(S1, "ACME-REVIEW", lastActivityAt=100))
    write_row(env, 0, A2, O2, "local_s1",
              row_data(S1, "Northwind kickoff", lastActivityAt=100))
    write_row(env, 0, A3, O3, "local_pad", row_data(PAD, "Padding"))
    m = ct.plan_converge(env, ct.ConvergeFlags())
    # A2 ("cccc...") > A1 ("aaaa...") lexicographically.
    assert [r["title"] for r in m["rows"]] == ["Northwind kickoff"]


def test_title_source_travels_with_the_chosen_title(mkenv, tmp_path,
                                                    write_transcript,
                                                    write_row):
    env = mkenv(tmp_path)
    write_transcript(env, "C--p", S1, t_entries())
    write_transcript(env, "C--p", S2, t_entries())
    write_row(env, 0, A1, O1, "local_s1",
              row_data(S1, "ACME-REVIEW", titleSource="user"))
    write_row(env, 0, A1, O1, "local_s2",
              row_data(S2, "Northwind kickoff", titleSource="auto"))
    write_row(env, 0, A2, O2, "local_pad", row_data(S3, "Padding"))
    write_transcript(env, "C--p", S3, t_entries())
    m = ct.plan_converge(env, ct.ConvergeFlags())
    by_sid = {}
    for r in m["rows"]:
        by_sid.setdefault(r["session"],
                          json.loads(ct.unb64(r["post_b64"]).decode("utf-8")))
    assert by_sid[S1]["titleSource"] == "user"
    assert by_sid[S2]["titleSource"] == "auto"


# ---------------------------------------------------------------------- --only

def test_only_resolves_ambiguity_with_a_candidate_listing(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    title = "Quarterly board report finalization"
    for sid in (S1, S2):
        write_transcript(env, "C--p", sid, t_entries())
    write_row(env, 0, A1, O1, "local_1", row_data(S1, title))
    write_row(env, 0, A1, O1, "local_2", row_data(S2, title))
    with pytest.raises(ct.Refusal) as exc:
        ct.plan_converge(env, ct.ConvergeFlags(only=title))
    msg = str(exc.value)
    assert S1[:8] in msg and S2[:8] in msg


def test_only_no_match_names_new_row(mkenv, tmp_path, write_transcript,
                                     write_row):
    env = mkenv(tmp_path)
    mixed_holdings(env, write_transcript, write_row)
    with pytest.raises(ct.Refusal, match="new-row"):
        ct.plan_converge(env, ct.ConvergeFlags(only="Northwind onboarding"))


def test_only_dead_conversation_refuses(mkenv, tmp_path, write_row):
    env = mkenv(tmp_path)
    write_row(env, 0, A1, O1, "local_1", row_data(S1, "ACME-REVIEW"))
    with pytest.raises(ct.Refusal, match="no transcript"):
        ct.plan_converge(env, ct.ConvergeFlags(only=S1[:8]))


def test_only_scopes_the_completeness_line_and_says_so(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    env = mkenv(tmp_path)
    mixed_holdings(env, write_transcript, write_row)
    m = ct.plan_converge(env, ct.ConvergeFlags(only=S2[:8]))
    assert [(r["session"], r["account"]) for r in m["rows"]] == [(S2, A3)]
    assert m["complete"] == {"now": 0, "of": 1, "after": 1, "held": 0,
                             "scoped": True}
    rc = ct.cmd_converge(env, cv_ns(only=S2[:8]))
    assert rc == 0
    assert "scoped to --only" in capsys.readouterr().out


# --------------------------------------------------------------------- holds

def held_setup(env, write_transcript, write_row):
    """S1 held by A1 (newest, ACME-REVIEW) and A2 (older, minority title);
    missing from A3, where a DIFFERENT conversation already carries
    ACME-REVIEW. S2 held by A1, missing from A2 and A3, no collision.

    The colliding conversation (S3) and the padding row are deliberately
    DEAD - no transcript - so they make A3 a destination and its sidebar
    carry the colliding title without adding eligible pairs of their own.
    A dead row still collides: the title is on that sidebar either way."""
    for sid in (S1, S2):
        write_transcript(env, "C--p", sid, t_entries())
    write_row(env, 0, A1, O1, "local_s1",
              row_data(S1, "ACME-REVIEW", lastActivityAt=200))
    write_row(env, 0, A2, O2, "local_s1",
              row_data(S1, "Northwind kickoff", lastActivityAt=100))
    write_row(env, 0, A1, O1, "local_s2",
              row_data(S2, "Quarterly board report finalization"))
    write_row(env, 0, A3, O3, "local_coll", row_data(S3, "ACME-REVIEW"))
    write_row(env, 0, A3, O3, "local_pad", row_data(PAD, "Padding"))


def test_held_title_collision_names_the_row_and_everything_else_applies(
        mkenv, tmp_path, write_transcript, write_row):
    env = _monotonic_now(mkenv(tmp_path))
    held_setup(env, write_transcript, write_row)
    m = ct.plan_converge(env, ct.ConvergeFlags())
    held = [h for h in m["holds"] if h["reason"] == "held_title_collision"]
    assert len(held) == 1
    h = held[0]
    assert h["session"] == S1 and h["account"] == A3
    assert "local_coll" in h["detail"]
    assert "retitle" in h["retitle"]
    # A non-colliding minority title is NOT substituted: no planned row for
    # the held pair under any name.
    assert not any(r["session"] == S1 and r["account"] == A3
                   for r in m["rows"])
    assert not any(r["title"] == "Northwind kickoff" for r in m["rows"])
    # The rest of the target set proceeds: S1 -> A2 is not missing (A2 holds
    # it), so only S2's two pairs remain.
    pairs = sorted((r["session"], r["account"]) for r in m["rows"])
    assert pairs == [(S2, A2), (S2, A3)]
    # Held conversations stay short in the projection; the two dead rows are
    # out of the denominator.
    assert m["complete"] == {"now": 0, "of": 2, "after": 1, "held": 1,
                             "scoped": False}
    assert m["dead_excluded"] == 2


def test_collision_hold_apply_exits_3_and_applies_the_rest(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    env = _monotonic_now(mkenv(tmp_path))
    held_setup(env, write_transcript, write_row)
    rc = ct.cmd_converge(env, cv_ns(apply=True))
    out = capsys.readouterr().out
    assert rc == 3
    assert "held_title_collision" in out
    assert "exit 3" in out
    # Everything else applied: S2 landed in A2 and A3; the held pair did not.
    a3_rows = [json.loads(open(os.path.join(
        env.store_candidates[0], A3, O3, n), encoding="utf-8").read())
        for n in store_rows(env, A3, O3)]
    sids = {d["cliSessionId"] for d in a3_rows}
    assert S2 in sids and S1 not in sids
    op = ct.list_ops(env)[-1]
    assert op.manifest["status"] == "completed"
    assert sum(1 for r in op.manifest["rows"] if r["written"]) == 2
    # And the holds travelled into the journalled record.
    assert [h["reason"] for h in op.manifest["holds"]] == \
        ["held_title_collision"]


def test_collision_comparator_is_trimmed_exact_match(
        mkenv, tmp_path, write_transcript, write_row):
    """The hold uses title_key - alignment's own comparator - so a
    whitespace-variant title still collides, and a substring does not."""
    env = mkenv(tmp_path)
    for sid in (S1, S2, S3):
        write_transcript(env, "C--p", sid, t_entries())
    write_row(env, 0, A1, O1, "local_s1", row_data(S1, "ACME-REVIEW"))
    # A2's colliding row differs only in surrounding whitespace: collides.
    write_row(env, 0, A2, O2, "local_ws", row_data(S3, "  ACME-REVIEW  "))
    # A3's row merely CONTAINS the title: not a collision.
    write_row(env, 0, A3, O3, "local_sub", row_data(S2, "ACME-REVIEW extras"))
    m = ct.plan_converge(env, ct.ConvergeFlags(only=S1[:8]))
    assert [h["account"] for h in m["holds"]] == [A2]
    assert [(r["session"], r["account"]) for r in m["rows"]] == [(S1, A3)]


def test_two_planned_conversations_colliding_in_one_destination_hold_later(
        mkenv, tmp_path, write_transcript, write_row):
    """Two eligible conversations converging on one destination under one
    chosen title: the later (sid order) holds, the earlier lands - a
    destination never receives two planned rows under one title_key."""
    env = mkenv(tmp_path)
    for sid in (S1, S2):
        write_transcript(env, "C--p", sid, t_entries())
    write_row(env, 0, A1, O1, "local_1", row_data(S1, "ACME-REVIEW"))
    write_row(env, 0, A2, O2, "local_2", row_data(S2, "ACME-REVIEW"))
    write_row(env, 0, A3, O3, "local_pad", row_data(PAD, "Padding"))
    m = ct.plan_converge(env, ct.ConvergeFlags())
    # Cross-holdings collide as EXISTING rows (S1 -> A2 hits S2's row and
    # vice versa); the neutral destination A3 is where the planned-vs-planned
    # rule decides: S1 (lower sid) lands, S2 holds naming the plan itself.
    planned = sorted((r["session"], r["account"]) for r in m["rows"])
    assert planned == [(S1, A3)]
    held = {(h["session"], h["account"]): h for h in m["holds"]}
    assert set(held) == {(S1, A2), (S2, A1), (S2, A3)}
    assert "already creates" in held[(S2, A3)]["detail"]
    # And distinguishable cannot move: no destination gets two planned rows
    # under one title_key.
    seen = set()
    for r in m["rows"]:
        key = (r["account"], ct.title_key(r["title"]))
        assert key not in seen
        seen.add(key)


def test_unusable_transcript_holds_its_pairs_and_the_rest_proceed(
        mkenv, tmp_path, write_transcript, write_row):
    """A transcript that exists but cannot populate a row (here: no assistant
    reply, so no model) holds that conversation with the reason attached
    rather than refusing the whole bulk run."""
    env = mkenv(tmp_path)
    write_transcript(env, "C--p", S1,
                     [{"cwd": "C:\\p", "timestamp": "2026-08-01T00:00:00.000Z",
                       "type": "user",
                       "message": {"role": "user", "content": "hi"}}])
    write_transcript(env, "C--p", S2, t_entries())
    write_row(env, 0, A1, O1, "local_1", row_data(S1, "ACME-REVIEW"))
    write_row(env, 0, A1, O1, "local_2", row_data(S2, "Northwind kickoff"))
    write_row(env, 0, A2, O2, "local_3", row_data(S3, "Padding"))
    write_transcript(env, "C--p", S3, t_entries())
    m = ct.plan_converge(env, ct.ConvergeFlags())
    held = [h for h in m["holds"]
            if h["reason"] == "held_transcript_unusable"]
    assert {h["session"] for h in held} == {S1}
    assert any("model" in h["detail"] for h in held)
    assert any(r["session"] == S2 for r in m["rows"])


# ------------------------------------------------------------------ applying

def test_apply_levels_the_counts_and_alignment_agrees(
        mkenv, tmp_path, write_transcript, write_row):
    env = _monotonic_now(mkenv(tmp_path))
    mixed_holdings(env, write_transcript, write_row)
    before = ct.gather_alignment(env)
    assert before["complete"]["in_all_accounts"] == 1
    dup_before = before["distinguishable"]["duplicate_titles"]
    m = ct.plan_converge(env, ct.ConvergeFlags())
    assert ct.run_converge(env, m) == "completed"
    after = ct.gather_alignment(env)
    # complete is levelled to the plan's projection...
    assert after["complete"]["in_all_accounts"] == m["complete"]["after"] == 3
    # ...and distinguishable cannot move (shared comparator).
    assert after["distinguishable"]["duplicate_titles"] == dup_before == 0
    op = ct.list_ops(env)[-1]
    assert op.manifest["op_type"] == "converge"
    assert op.manifest["status"] == "completed"
    assert all(r["written"] for r in op.manifest["rows"])
    assert ct.read_lock(env) is None
    assert m["op_id"] == op.manifest["op_id"]


def test_alignment_complete_rises_by_the_applied_count(
        mkenv, tmp_path, write_transcript, write_row):
    """Each short conversation misses exactly one account, so conversations
    levelled == rows applied and the spec's arithmetic is exact."""
    env = _monotonic_now(mkenv(tmp_path))
    for sid in (S1, S2):
        write_transcript(env, "C--p", sid, t_entries())
    for acct, org in ((A1, O1), (A2, O2)):
        write_row(env, 0, acct, org, "local_s1", row_data(S1, "ACME-REVIEW"))
        write_row(env, 0, acct, org, "local_s2",
                  row_data(S2, "Northwind kickoff"))
    write_row(env, 0, A3, O3, "local_s1", row_data(S1, "ACME-REVIEW"))
    before = ct.gather_alignment(env)["complete"]["in_all_accounts"]
    m = ct.plan_converge(env, ct.ConvergeFlags())
    assert ct.run_converge(env, m) == "completed"
    applied = sum(1 for r in m["rows"] if r["written"])
    after = ct.gather_alignment(env)["complete"]["in_all_accounts"]
    assert applied == 1
    assert after == before + applied


def test_apply_time_appearance_becomes_already_present_skip(
        mkenv, tmp_path, write_transcript, write_row):
    env = _monotonic_now(mkenv(tmp_path))
    mixed_holdings(env, write_transcript, write_row)
    m = ct.plan_converge(env, ct.ConvergeFlags())
    # A row for (S1, A3) appears between plan and apply - the app, a sync,
    # another converge.
    write_row(env, 0, A3, O3, "local_raced", row_data(S1, "ACME-REVIEW"))
    assert ct.run_converge(env, m) == "completed"
    raced = [r for r in m["rows"]
             if r["session"] == S1 and r["account"] == A3]
    assert raced[0]["skipped"] == "already_present"
    assert not raced[0]["written"]
    # The other pairs landed; exit is unaffected by an already_present skip.
    assert sum(1 for r in m["rows"] if r["written"]) == 2
    # And the minted filename was never created.
    assert raced[0]["name"] not in store_rows(env, A3, O3)


def test_apply_time_collision_holds_the_pair(mkenv, tmp_path,
                                             write_transcript, write_row,
                                             capsys):
    env = _monotonic_now(mkenv(tmp_path))
    mixed_holdings(env, write_transcript, write_row)
    m = ct.plan_converge(env, ct.ConvergeFlags())
    # A DIFFERENT conversation adopts S1's chosen title in A3 between plan
    # and apply.
    write_row(env, 0, A3, O3, "local_late", row_data(S3.replace("a", "b", 1),
                                                     "ACME-REVIEW"))
    assert ct.run_converge(env, m) == "completed"
    held = [r for r in m["rows"]
            if r["session"] == S1 and r["account"] == A3]
    assert held[0]["skipped"] == "held_title_collision"
    assert not held[0]["written"]
    assert held[0]["name"] not in store_rows(env, A3, O3)


def test_guard_ordering_running_app_refuses_before_any_op_record(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    mixed_holdings(env, write_transcript, write_row)
    m = ct.plan_converge(env, ct.ConvergeFlags())     # plan is allowed
    env.process_lister = lambda: [(99999, "claude.exe")]
    with pytest.raises(ct.Refusal, match="running"):
        ct.run_converge(env, m)
    # No journal residue, nothing for recover.
    assert ct.list_ops(env) == []
    assert ct.read_lock(env) is None
    assert store_rows(env, A3, O3) == ["local_s3.json"]


def test_all_already_present_apply_creates_no_op(mkenv, tmp_path,
                                                 write_transcript, write_row,
                                                 capsys):
    env = mkenv(tmp_path)
    mixed_holdings(env, write_transcript, write_row)
    m = ct.plan_converge(env, ct.ConvergeFlags())
    # Every pair's row appears before the apply.
    write_row(env, 0, A2, O2, "local_r1", row_data(S1, "ACME-REVIEW"))
    write_row(env, 0, A3, O3, "local_r2", row_data(S1, "ACME-REVIEW"))
    write_row(env, 0, A3, O3, "local_r3",
              row_data(S2, "Quarterly board report finalization"))
    assert ct.run_converge(env, m) == "unchanged"
    assert ct.list_ops(env) == []               # no op record at all
    with pytest.raises(ct.Refusal, match="no completed operation"):
        ct.cmd_undo(env, undo_ns(apply=True))   # and no undo entry


def test_all_held_apply_creates_no_op_and_exits_3(mkenv, tmp_path,
                                                  write_transcript, write_row,
                                                  capsys):
    env = mkenv(tmp_path)
    for sid in (S1, S3):
        write_transcript(env, "C--p", sid, t_entries())
    write_row(env, 0, A1, O1, "local_s1", row_data(S1, "ACME-REVIEW"))
    write_row(env, 0, A2, O2, "local_c", row_data(S3, "ACME-REVIEW"))
    write_row(env, 0, A3, O3, "local_c", row_data(S3, "ACME-REVIEW"))
    m = ct.plan_converge(env, ct.ConvergeFlags(only=S1[:8]))
    assert m["rows"] == [] and len(m["holds"]) == 2
    rc = ct.cmd_converge(env, cv_ns(only=S1[:8], apply=True))
    out = capsys.readouterr().out
    assert rc == 3
    assert "unchanged" in out
    assert ct.list_ops(env) == []


def test_complete_store_plans_zero_rows_and_exits_0(mkenv, tmp_path,
                                                    write_transcript,
                                                    write_row, capsys):
    env = mkenv(tmp_path)
    for sid in (S1,):
        write_transcript(env, "C--p", sid, t_entries())
    for acct, org in ((A1, O1), (A2, O2), (A3, O3)):
        write_row(env, 0, acct, org, "local_s1", row_data(S1, "ACME-REVIEW"))
    rc = ct.cmd_converge(env, cv_ns(apply=True))
    out = capsys.readouterr().out
    assert rc == 0
    assert "nothing to do" in out
    assert "complete : 1 of 1  ->  1 of 1" in out
    assert ct.list_ops(env) == []


# ---------------------------------------------------- fault injection, recovery

def _crash_after_first_row(env, write_transcript, write_row,
                           point="converge-mid-write", setup=None):
    (setup or mixed_holdings)(env, write_transcript, write_row)
    m = ct.plan_converge(env, ct.ConvergeFlags())

    def hook(p):
        if p == point:
            raise SimulatedCrash()
    ct._crash_hook = hook
    try:
        with pytest.raises(SimulatedCrash):
            ct.run_converge(env, m)
    finally:
        ct._crash_hook = None
    op = ct.nonterminal_ops(env)[0]
    assert op.manifest["status"] == "writing"
    return m, op


def test_crash_after_row_one_then_back_leaves_no_trace(
        mkenv, tmp_path, write_transcript, write_row):
    env = _monotonic_now(mkenv(tmp_path))
    m, op = _crash_after_first_row(env, write_transcript, write_row)
    written = [r for r in op.manifest["rows"] if r["written"]]
    assert len(written) == 1
    c = ct.classify_op(env, op)
    assert sorted(c["resolutions"]) == ["back", "forward"]
    assert ct.recover_op(env, op, "back") == "rolled_back"
    # No trace: the created row is gone, the untouched stores are untouched.
    assert store_rows(env, A2, O2) == ["local_s2.json", "local_s3.json"]
    assert store_rows(env, A3, O3) == ["local_s3.json"]
    assert op.manifest["status"] == "rolled_back"
    assert "rollback_residue" not in op.manifest
    assert ct.read_lock(env) is None


def test_crash_then_forward_re_evaluates_a_gained_row_as_skip(
        mkenv, tmp_path, write_transcript, write_row):
    env = _monotonic_now(mkenv(tmp_path))
    m, op = _crash_after_first_row(env, write_transcript, write_row)
    pending = [r for r in op.manifest["rows"] if not r["written"]]
    # In the window, one pending pair's destination gains a row for the same
    # conversation.
    gained = pending[0]
    acct_org = {A1: O1, A2: O2, A3: O3}[gained["account"]]
    write_row(env, 0, gained["account"], acct_org, "local_gained",
              row_data(gained["session"], gained["title"]))
    assert ct.recover_op(env, op, "forward") == "completed"
    assert gained["skipped"] == "already_present"
    # The minted filename was never created; every other pair finished.
    assert gained["name"] not in store_rows(env, gained["account"], acct_org)
    others = [r for r in op.manifest["rows"] if r is not gained]
    assert all(r["written"] or r.get("skipped") for r in others)


def test_crash_then_forward_never_writes_a_plan_time_hold_even_if_cleared(
        mkenv, tmp_path, write_transcript, write_row):
    env = _monotonic_now(mkenv(tmp_path))

    def setup(env, wt, wr):
        held_setup(env, wt, wr)
    m, op = _crash_after_first_row(env, write_transcript, write_row,
                                   setup=setup)
    # The plan held (S1 -> A3) on the collision with local_coll. Clear it in
    # the window.
    os.unlink(os.path.join(env.store_candidates[0], A3, O3,
                           "local_coll.json"))
    assert ct.recover_op(env, op, "forward") == "completed"
    # Still not written: clearing the collision changed the sidebar, so the
    # user replans rather than the tool guessing.
    a3 = [json.loads(open(os.path.join(env.store_candidates[0], A3, O3, n),
                          encoding="utf-8").read())
          for n in store_rows(env, A3, O3)]
    assert S1 not in {d["cliSessionId"] for d in a3}
    assert not any(r["session"] == S1 and r["account"] == A3
                   for r in op.manifest["rows"])


def test_hard_kill_window_back_goes_by_disk_evidence(
        mkenv, tmp_path, write_transcript, write_row):
    """A kill between atomic_write and save_manifest leaves the row holding
    this op's bytes with `written` still False. Back consults the disk and
    removes it anyway."""
    env = _monotonic_now(mkenv(tmp_path))
    m, op = _crash_after_first_row(env, write_transcript, write_row,
                                   point="converge-write-before-save")
    assert not any(r["written"] for r in op.manifest["rows"])
    first = op.manifest["rows"][0]
    on_disk = open(first["dest_path"], "rb").read()
    assert on_disk == ct.unb64(first["post_b64"])
    assert ct.recover_op(env, op, "back") == "rolled_back"
    assert not os.path.exists(first["dest_path"])


def test_completion_marker_failure_recovers_journal_only(
        mkenv, tmp_path, write_transcript, write_row):
    env = _monotonic_now(mkenv(tmp_path))
    m, op = _crash_after_first_row(env, write_transcript, write_row,
                                   point="converge-before-complete")
    assert all(r["written"] for r in op.manifest["rows"])
    c = ct.classify_op(env, op)
    assert sorted(c["resolutions"]) == ["back", "forward"]
    assert "0 pending" in c["note"]
    # Forward finishes the bookkeeping without touching a store - even with
    # the app running, because nothing will be written.
    env.process_lister = lambda: [(99999, "claude.exe")]
    assert ct.recover_op(env, op, "forward") == "completed"
    assert op.manifest["status"] == "completed"


# ---------------------------------------------------------------------- undo

def converged(env, write_transcript, write_row):
    mixed_holdings(env, write_transcript, write_row)
    m = ct.plan_converge(env, ct.ConvergeFlags())
    assert ct.run_converge(env, m) == "completed"
    return m, ct.list_ops(env)[-1]


def test_undo_deletes_created_rows_and_consumes_the_op(
        mkenv, tmp_path, write_transcript, write_row):
    env = _monotonic_now(mkenv(tmp_path))
    m, op = converged(env, write_transcript, write_row)
    assert ct.undo_converge(env, op) == "undone"
    assert store_rows(env, A2, O2) == ["local_s2.json", "local_s3.json"]
    assert store_rows(env, A3, O3) == ["local_s3.json"]
    assert op.manifest["status"] == "undone"
    rep = op.manifest["undo_report"]
    assert rep["deleted"] == 3 and rep["already_gone"] == 0
    assert rep["skipped"] == []


def test_undo_never_deletes_the_last_pointer(mkenv, tmp_path,
                                             write_transcript, write_row):
    env = _monotonic_now(mkenv(tmp_path))
    m, op = converged(env, write_transcript, write_row)
    # The holder S1 was copied from is removed after the converge: of the two
    # created S1 rows, undo may delete one - the other is the last pointer.
    os.unlink(os.path.join(env.store_candidates[0], A1, O1, "local_s1.json"))
    assert ct.undo_converge(env, op) == "undone"
    rep = op.manifest["undo_report"]
    kept = [s for s in rep["skipped"] if s["reason"] == "last_pointer"]
    assert len(kept) == 1
    # Exactly one S1 pointer survives somewhere.
    survivors = []
    for acct, org in ((A1, O1), (A2, O2), (A3, O3)):
        for n in store_rows(env, acct, org):
            d = json.loads(open(os.path.join(env.store_candidates[0], acct,
                                             org, n),
                                encoding="utf-8").read())
            if d.get("cliSessionId") == S1:
                survivors.append((acct, n))
    assert len(survivors) == 1


def test_undo_skips_a_repointed_row_and_says_so(mkenv, tmp_path,
                                                write_transcript, write_row):
    env = _monotonic_now(mkenv(tmp_path))
    m, op = converged(env, write_transcript, write_row)
    victim = [r for r in op.manifest["rows"]
              if r["session"] == S1 and r["account"] == A2][0]
    d = json.loads(open(victim["dest_path"], encoding="utf-8").read())
    d["cliSessionId"] = S3
    with open(victim["dest_path"], "w", encoding="utf-8") as fh:
        json.dump(d, fh)
    assert ct.undo_converge(env, op) == "undone"
    rep = op.manifest["undo_report"]
    assert [s["reason"] for s in rep["skipped"]] == ["repointed"]
    assert os.path.exists(victim["dest_path"])
    assert rep["deleted"] == 2


def test_undo_skips_a_retitled_row_and_says_so(mkenv, tmp_path,
                                               write_transcript, write_row):
    env = _monotonic_now(mkenv(tmp_path))
    m, op = converged(env, write_transcript, write_row)
    victim = [r for r in op.manifest["rows"]
              if r["session"] == S1 and r["account"] == A3][0]
    d = json.loads(open(victim["dest_path"], encoding="utf-8").read())
    d["title"] = "Northwind renamed"
    with open(victim["dest_path"], "w", encoding="utf-8") as fh:
        json.dump(d, fh)
    assert ct.undo_converge(env, op) == "undone"
    rep = op.manifest["undo_report"]
    assert [s["reason"] for s in rep["skipped"]] == ["curated"]
    assert os.path.exists(victim["dest_path"])


def test_undo_deletes_despite_a_moved_focus_time(mkenv, tmp_path,
                                                 write_transcript, write_row):
    """A moved lastFocusedAt alone is not curation - the app writes that on
    focus - so the row still deletes."""
    env = _monotonic_now(mkenv(tmp_path))
    m, op = converged(env, write_transcript, write_row)
    victim = [r for r in op.manifest["rows"]
              if r["session"] == S1 and r["account"] == A2][0]
    d = json.loads(open(victim["dest_path"], encoding="utf-8").read())
    d["lastFocusedAt"] = 9_999_999_999_999
    with open(victim["dest_path"], "w", encoding="utf-8") as fh:
        json.dump(d, fh)
    assert ct.undo_converge(env, op) == "undone"
    rep = op.manifest["undo_report"]
    assert rep["deleted"] == 3 and rep["skipped"] == []
    assert not os.path.exists(victim["dest_path"])


def test_undo_counts_already_gone_as_done(mkenv, tmp_path, write_transcript,
                                          write_row):
    env = _monotonic_now(mkenv(tmp_path))
    m, op = converged(env, write_transcript, write_row)
    victim = [r for r in op.manifest["rows"] if r["session"] == S2][0]
    os.unlink(victim["dest_path"])
    assert ct.undo_converge(env, op) == "undone"
    rep = op.manifest["undo_report"]
    assert rep["deleted"] == 2 and rep["already_gone"] == 1


def test_second_undo_reaches_the_previous_op(mkenv, tmp_path,
                                             write_transcript, write_row,
                                             capsys):
    env = _monotonic_now(mkenv(tmp_path))
    mixed_holdings(env, write_transcript, write_row)
    ct.run_retitle(env, ct.plan_retitle(
        env, ct.RetitleFlags(only=S3[:8], title="ACME-ARCHIVE")))
    m = ct.plan_converge(env, ct.ConvergeFlags())
    assert ct.run_converge(env, m) == "completed"
    # First undo reverses the converge (the newest op), tally printed...
    assert ct.cmd_undo(env, undo_ns(apply=True)) == 0
    out = capsys.readouterr().out
    assert "removed 3 row(s)" in out
    # ...second undo reaches the retitle beneath it.
    assert ct.cmd_undo(env, undo_ns(apply=True)) == 0
    ops = {o.manifest["op_type"]: o.manifest["status"]
           for o in ct.list_ops(env)}
    assert ops.get("converge") == "undone"
    assert ops.get("retitle") == "undone"


def test_undo_preview_names_the_converge(mkenv, tmp_path, write_transcript,
                                         write_row, capsys):
    env = _monotonic_now(mkenv(tmp_path))
    converged(env, write_transcript, write_row)
    assert ct.cmd_undo(env, undo_ns(apply=False)) == 0
    out = capsys.readouterr().out
    assert "converge" in out and "3 row(s)" in out


# --------------------------------------------------------------------- doctor

def test_doctor_vanished_row_check_covers_converge_rows(
        mkenv, tmp_path, write_transcript, write_row):
    env = _monotonic_now(mkenv(tmp_path))
    m, op = converged(env, write_transcript, write_row)
    victim = [r for r in op.manifest["rows"] if r["session"] == S2][0]
    os.unlink(victim["dest_path"])
    rep = ct.gather_doctor(env)
    hits = [v for v in rep["vanished_new_rows"] if v["to_session"] == S2]
    assert len(hits) == 1
    assert hits[0]["op_id"] == op.manifest["op_id"]
    assert hits[0]["transcript_count"] == 1
    assert rep["exit_code"] == 1


def test_doctor_vanished_check_asks_the_store_not_the_journal(
        mkenv, tmp_path, write_transcript, write_row):
    """A converge row that vanished but whose conversation is reachable again
    by a fresh row is not reported - same rule as new-row's check."""
    env = _monotonic_now(mkenv(tmp_path))
    m, op = converged(env, write_transcript, write_row)
    victim = [r for r in op.manifest["rows"] if r["session"] == S2][0]
    os.unlink(victim["dest_path"])
    write_row(env, 0, victim["account"], victim["org"], "local_fresh",
              row_data(S2, "Quarterly board report finalization"))
    rep = ct.gather_doctor(env)
    assert not [v for v in rep["vanished_new_rows"]
                if v["to_session"] == S2]


# ----------------------------------------------------------------- the command

def test_cmd_dry_run_groups_by_destination_and_writes_nothing(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    env = mkenv(tmp_path)
    mixed_holdings(env, write_transcript, write_row)
    rc = ct.cmd_converge(env, cv_ns())
    out = capsys.readouterr().out
    assert rc == 0
    assert "dry run" in out
    assert "destinations:" in out
    assert "held by:" in out
    assert "complete : 1 of 3  ->  3 of 3   (0 held)" in out
    assert ct.list_ops(env) == []
    assert store_rows(env, A3, O3) == ["local_s3.json"]


def test_cmd_apply_end_to_end(mkenv, tmp_path, write_transcript, write_row,
                              capsys):
    env = _monotonic_now(mkenv(tmp_path))
    mixed_holdings(env, write_transcript, write_row)
    rc = ct.cmd_converge(env, cv_ns(apply=True))
    out = capsys.readouterr().out
    assert rc == 0
    assert "result  : completed" in out
    assert "created : 3 row(s)" in out
    assert len(store_rows(env, A3, O3)) == 3


def test_cmd_json_strips_row_images(mkenv, tmp_path, write_transcript,
                                    write_row, capsys):
    env = mkenv(tmp_path)
    mixed_holdings(env, write_transcript, write_row)
    rc = ct.cmd_converge(env, cv_ns(json=True))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["rows"]) == 3
    for r in payload["rows"]:
        assert "post_b64" not in r and "pre_b64" not in r
    assert payload["complete"]["after"] == 3


def test_anonymized_plan_labels_titles_and_commands(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    env = mkenv(tmp_path)
    held_setup(env, write_transcript, write_row)
    ct._ANONYMIZE = True
    ct._ANON_CACHE.clear()
    try:
        rc = ct.cmd_converge(env, cv_ns(anonymize=True))
        out = capsys.readouterr().out
        assert rc == 0
        assert "ACME-REVIEW" not in out
        assert "Northwind kickoff" not in out
        assert "board report" not in out
        assert "<session-" in out

        rc = ct.cmd_converge(env, cv_ns(anonymize=True, json=True))
        payload = capsys.readouterr().out
        assert rc == 0
        assert "ACME-REVIEW" not in payload
        assert "Northwind kickoff" not in payload
    finally:
        ct._ANONYMIZE = False
        ct._ANON_CACHE.clear()


def test_converge_help_names_the_promise(capsys):
    with pytest.raises(SystemExit) as exc:
        ct.main(["converge", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "EVERY account" in out
    assert "additive" in out


def test_anonymize_apply_is_refused_globally(capsys):
    rc = ct.main(["converge", "--anonymize", "--apply"])
    assert rc == 2
    assert "--anonymize" in capsys.readouterr().err


# ------------------------------------- 0.13.0: the dry run discloses RULING 5

def one_missing_pair(env, write_transcript, write_row):
    """S1 held by A1 with a transcript; A2 made a destination by a PAD row
    (dead - no transcript for it), so the plan's one write is (S1 -> A2)."""
    write_transcript(env, "C--p", S1, t_entries())
    write_row(env, 0, A1, O1, "local_s1", row_data(S1, "ACME-REVIEW"))
    write_row(env, 0, A2, O2, "local_pad", row_data(PAD, "Northwind"))


def test_dry_run_warns_when_identity_files_disagree(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    """The plan knew everything the apply-time refusal knew and said nothing
    - the one dry-run gap that cost a full close-the-app cycle to discover.
    The warning names each side's remedy as a pastable command line."""
    env = mkenv(tmp_path)
    one_missing_pair(env, write_transcript, write_row)
    identity_files(env, A1, A2, email="alice@example.com")
    rc = ct.cmd_converge(env, cv_ns())
    out = capsys.readouterr().out
    assert rc == 0                       # disclosure, not enforcement
    assert "disagree" in out
    assert "converge --live alice@example.com --apply" in out
    assert "converge --live {0} --apply".format(A2[:8]) in out
    assert "(if the app is on {0})".format(A1[:8]) in out
    assert "(if the app is on {0})".format(A2[:8]) in out
    assert "refusal writes nothing" in out


def test_dry_run_quiet_when_files_agree(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    env = mkenv(tmp_path)
    one_missing_pair(env, write_transcript, write_row)
    identity_files(env, A1, A1, oauth_org=O1)
    rc = ct.cmd_converge(env, cv_ns())
    out = capsys.readouterr().out
    assert rc == 0
    assert "warning" not in out
    assert "disagree" not in out


def test_dry_run_quiet_when_live_asserted(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    """A validated --live IS the arbitration the warning asks for, so
    printing the warning anyway would nag past the answer."""
    env = mkenv(tmp_path)
    one_missing_pair(env, write_transcript, write_row)
    identity_files(env, A1, A2)
    rc = ct.cmd_converge(env, cv_ns(live=A2[:8]))
    out = capsys.readouterr().out
    assert rc == 0
    assert "warning" not in out
    assert "disagree" not in out


def test_json_manifest_carries_identity_disagreement(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    """--json is the machine caller's warning: the field is the signal (the
    dry run still exits 0), present with both ids under a disagreement,
    absent otherwise, and --anonymize must keep its structure - the values
    are machine ids, not content."""
    env = mkenv(tmp_path)
    one_missing_pair(env, write_transcript, write_row)
    identity_files(env, A1, A2)
    rc = ct.cmd_converge(env, cv_ns(json=True))
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["identity_disagreement"] == {"oauth": A1, "config": A2}

    desktop_config(env, A1)              # agreement: the key is absent
    ct.cmd_converge(env, cv_ns(json=True))
    payload = json.loads(capsys.readouterr().out)
    assert "identity_disagreement" not in payload

    desktop_config(env, A2)
    ct._ANONYMIZE = True
    ct._ANON_CACHE.clear()
    try:
        rc = ct.cmd_converge(env, cv_ns(anonymize=True, json=True))
        payload = json.loads(capsys.readouterr().out)
        assert rc == 0
        assert payload["identity_disagreement"] == {"oauth": A1, "config": A2}
    finally:
        ct._ANONYMIZE = False
        ct._ANON_CACHE.clear()


def test_apply_still_refuses_fresh_disagreement(
        mkenv, tmp_path, write_transcript, write_row):
    """Regression for the boundary the warning must not blur: the plan is a
    snapshot and the recheck evaluates the files FRESH under the lock - a
    clean plan does not carry a clean apply."""
    env = mkenv(tmp_path)
    one_missing_pair(env, write_transcript, write_row)
    identity_files(env, A1, A1, oauth_org=O1)
    m = ct.plan_converge(env, ct.ConvergeFlags())
    assert "identity_disagreement" not in m
    desktop_config(env, A2)              # the files fall out of agreement
    with pytest.raises(ct.Refusal, match="RULING 5"):
        ct.run_converge(env, m)
    assert store_rows(env, A2, O2) == ["local_pad.json"]   # nothing written
    assert ct.list_ops(env) == []                          # nothing journaled


def test_warning_then_clean_apply_when_disagreement_clears(
        mkenv, tmp_path, write_transcript, write_row):
    """The reverse transition: a plan that warned applies cleanly once the
    files agree again - the warning was a snapshot, never the gate."""
    env = mkenv(tmp_path)
    one_missing_pair(env, write_transcript, write_row)
    identity_files(env, A1, A2)
    m = ct.plan_converge(env, ct.ConvergeFlags())
    assert m["identity_disagreement"] == {"oauth": A1, "config": A2}
    desktop_config(env, A1)              # the disagreement clears
    assert ct.run_converge(env, m) == "completed"
    assert len(store_rows(env, A2, O2)) == 2


def test_remedy_lines_fall_back_when_emails_collide(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    """Two accounts under one address would print two identical, unusable
    remedies; the 8-char ids are already distinct, so they carry the lines."""
    env = mkenv(tmp_path)
    one_missing_pair(env, write_transcript, write_row)
    identity_files(env, A1, A2, email="alice@example.com")
    agent_mode_email(env, A2, "alice@example.com")
    rc = ct.cmd_converge(env, cv_ns())
    out = capsys.readouterr().out
    assert rc == 0
    assert "converge --live {0} --apply".format(A1[:8]) in out
    assert "converge --live {0} --apply".format(A2[:8]) in out
    remedy = [line for line in out.splitlines() if "--live" in line]
    assert remedy and all("@" not in line for line in remedy)
