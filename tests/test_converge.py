"""Tests for `ccs converge`.

Implements docs/specs/2026-08-26-converge-design.md's Testing section, the
converge-side tests of docs/specs/2026-08-29-identity-ux-design.md (0.13.0:
the RULING 5 disclosure, the pair form, account-scope --live, corroboration),
and docs/specs/2026-08-30-hold-remedies-design.md's Tests section (0.14.0:
measured hold remedies; its two anonymize tests live in test_anonymize.py).
Every title is from the fake cast (ACME-REVIEW, Northwind, "Quarterly board
report finalization") - never a real one.
"""
import datetime
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
    # conversation. Which pending row is incidental: any of them would do, so
    # the journal's write order (parents first, else the plan's) is not
    # what this pins.
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


def _app_touches(path, **changes):
    """The desktop app rewriting a row in place - per-account state only, the
    row's own sessionId and cliSessionId kept - as it does on focus."""
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    d.update(changes)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(d, fh)


def test_hard_kill_then_forward_keeps_the_landed_row_within_undo(
        mkenv, tmp_path, write_transcript, write_row):
    """The mixed recovery the 0.17.0 forward arm got wrong: one row landed
    with its flag unset, the others never written. The landed row must be
    recorded as written - not re-judged 'already_present' against its own
    file - so a later undo still removes it."""
    env = _monotonic_now(mkenv(tmp_path))
    m, op = _crash_after_first_row(env, write_transcript, write_row,
                                   point="converge-write-before-save")
    first = op.manifest["rows"][0]
    assert not first["written"] and os.path.exists(first["dest_path"])
    assert ct.recover_op(env, op, "forward") == "completed"
    assert first["written"] is True
    assert "skipped" not in first
    assert all(r["written"] for r in op.manifest["rows"])
    assert ct.undo_converge(env, op) == "undone"
    assert not os.path.exists(first["dest_path"])
    assert store_rows(env, A2, O2) == ["local_s2.json", "local_s3.json"]
    assert store_rows(env, A3, O3) == ["local_s3.json"]


def test_hard_kill_then_app_rewrite_then_forward_knows_the_row_by_its_stem(
        mkenv, tmp_path, write_transcript, write_row):
    """Same window, and the app then rewrote the landed row (focus time), so
    its bytes no longer match the journal. The minted filename stem it still
    carries as sessionId proves it is this op's row."""
    env = _monotonic_now(mkenv(tmp_path))
    m, op = _crash_after_first_row(env, write_transcript, write_row,
                                   point="converge-write-before-save")
    first = op.manifest["rows"][0]
    _app_touches(first["dest_path"], lastFocusedAt=1_900_000_000_000)
    assert ct._sync_row_drift(first) == "drifted"
    assert ct.recover_op(env, op, "forward") == "completed"
    assert first["written"] is True
    assert "skipped" not in first
    assert ct.undo_converge(env, op) == "undone"
    assert not os.path.exists(first["dest_path"])


def test_all_landed_one_app_rewritten_forward_is_journal_only(
        mkenv, tmp_path, write_transcript, write_row):
    """Every row landed; the last flag never saved and the app rewrote that
    row. Nothing is left to write, so forward is journal-only: no mutation
    guard, no recheck, even with the app running."""
    env = _monotonic_now(mkenv(tmp_path))
    mixed_holdings(env, write_transcript, write_row)
    m = ct.plan_converge(env, ct.ConvergeFlags())
    seen = {"n": 0}

    def hook(p):
        if p == "converge-write-before-save":
            seen["n"] += 1
            if seen["n"] == len(m["rows"]):
                raise SimulatedCrash()
    ct._crash_hook = hook
    try:
        with pytest.raises(SimulatedCrash):
            ct.run_converge(env, m)
    finally:
        ct._crash_hook = None
    op = ct.nonterminal_ops(env)[0]
    last = op.manifest["rows"][-1]
    assert not last["written"]
    _app_touches(last["dest_path"], lastFocusedAt=1_900_000_000_000)
    env.process_lister = lambda: [(99999, "claude.exe")]
    assert ct.recover_op(env, op, "forward") == "completed"
    assert last["written"] is True and "skipped" not in last


def test_hard_kill_then_app_rewrite_then_back_records_residue(
        mkenv, tmp_path, write_transcript, write_row):
    """Back after the same window. The app-rewritten row is this op's (its
    stem says so), so like any written row that no longer holds our bytes it
    is left in place and recorded where doctor looks - not dropped silently
    as somebody else's file."""
    env = _monotonic_now(mkenv(tmp_path))
    m, op = _crash_after_first_row(env, write_transcript, write_row,
                                   point="converge-write-before-save")
    first = op.manifest["rows"][0]
    _app_touches(first["dest_path"], lastFocusedAt=1_900_000_000_000)
    assert ct.recover_op(env, op, "back") == "rolled_back"
    assert os.path.exists(first["dest_path"])
    assert first["name"] in op.manifest["rollback_residue"]


def test_a_foreign_file_at_the_minted_path_is_never_taken_as_landed(
        mkenv, tmp_path, write_transcript, write_row):
    """A file at the minted path whose sessionId is not the minted stem was not
    written by this op. Recovery must not mark it written, so undo can never
    delete it."""
    env = _monotonic_now(mkenv(tmp_path))
    m, op = _crash_after_first_row(env, write_transcript, write_row,
                                   point="converge-write-before-save")
    first = op.manifest["rows"][0]
    _app_touches(first["dest_path"], sessionId="local_someone_else")
    assert not ct._row_is_ours(first)
    try:
        ct.recover_op(env, op, "forward")
    except ct.Refusal:
        pass                        # refusing is fine; marking it ours is not
    assert not first.get("written")
    assert os.path.exists(first["dest_path"])


def test_a_landed_row_missed_by_the_marking_pass_is_still_not_judged_against_itself(
        mkenv, tmp_path, write_transcript, write_row, monkeypatch):
    """If the landed row could not be read for a moment when recovery marked
    rows, the recheck that follows must still not count the row's own file as
    'a row for it appeared': the write loop then finds this op's bytes there
    and records it."""
    env = _monotonic_now(mkenv(tmp_path))
    m, op = _crash_after_first_row(env, write_transcript, write_row,
                                   point="converge-write-before-save")
    first = op.manifest["rows"][0]
    monkeypatch.setattr(ct, "_converge_mark_landed",
                        lambda op_, m_, skip_ids=frozenset(): 0)
    assert ct.recover_op(env, op, "forward") == "completed"
    assert first["written"] is True and "skipped" not in first
    assert ct.undo_converge(env, op) == "undone"
    assert not os.path.exists(first["dest_path"])


def test_a_row_repointed_after_the_crash_is_kept_by_undo(
        mkenv, tmp_path, write_transcript, write_row):
    """Marking a landed row that someone repointed since is safe: undo's own
    rule keeps a repointed row, as after any converge."""
    env = _monotonic_now(mkenv(tmp_path))
    m, op = _crash_after_first_row(env, write_transcript, write_row,
                                   point="converge-write-before-save")
    first = op.manifest["rows"][0]
    _app_touches(first["dest_path"], cliSessionId=S3)
    assert ct.recover_op(env, op, "forward") == "completed"
    assert first["written"] is True
    assert ct.undo_converge(env, op) == "undone"
    assert os.path.exists(first["dest_path"])
    reasons = {s["name"]: s["reason"]
               for s in op.manifest["undo_report"]["skipped"]}
    assert reasons[first["name"]] == "repointed"


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


def test_live_help_mentions_the_printed_form(capsys):
    """The pair form is only discoverable if the flag's own help names it -
    the reports print it, so the help must close the loop."""
    for cmd in ("converge", "sync"):
        with pytest.raises(SystemExit) as exc:
            ct.main([cmd, "--help"])
        assert exc.value.code == 0
        assert "aaaa1111/cccc3333" in capsys.readouterr().out


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
    absent otherwise. --anonymize keeps its structure but truncates the ids
    to their 8-character prefixes - the same form the text warning has
    always printed, still distinct, and anonymized output is a paste
    artifact, not a machine surface (machine callers use plain --json)."""
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
        assert payload["identity_disagreement"] == \
            {"oauth": A1[:8] + "…", "config": A2[:8] + "…"}
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


# ------------------------------------ 0.13.0: --live resolves at account scope

# The spec's fake pair, shaped so the anchored-prefix rules are visible:
# account ids aaaa1111.../bbbb2222..., org ids cccc3333.../dddd4444.... LA
# deliberately ENDS in its own first group, so a mid-uuid fragment like
# '1111/cccc' sits inside its normalized store path as a real SUBSTRING -
# which is how test 9 can prove pair-shaped queries never fall back to
# substring matching.
LA = "aaaa1111-0000-4000-8000-0000aaaa1111"
LOA = "cccc3333-0000-4000-8000-000000000003"
LB = "bbbb2222-0000-4000-8000-000000000002"
LOB = "dddd4444-0000-4000-8000-000000000004"


def live_pair_env(mkenv, tmp_path, write_transcript, write_row):
    """Two accounts holding one conversation each, the identity files
    disagreeing about which is signed in (oauth: LA, config: LB)."""
    env = mkenv(tmp_path)
    write_transcript(env, "C--p", S1, t_entries())
    write_transcript(env, "C--p", S2, t_entries())
    write_row(env, 0, LA, LOA, "local_s1", row_data(S1, "ACME-REVIEW"))
    write_row(env, 0, LB, LOB, "local_s2", row_data(S2, "Northwind"))
    identity_files(env, LA, LB)
    return env


def test_printed_pair_form_resolves_live(mkenv, tmp_path, write_transcript,
                                         write_row):
    """`--live "aaaa1111/cccc3333"` - the exact form every report prints -
    resolves under a disagreement."""
    env = live_pair_env(mkenv, tmp_path, write_transcript, write_row)
    acc = ct._resolve_live_assertion(env, "aaaa1111/cccc3333",
                                     ct._account_dirs(env),
                                     account_scope=True)
    assert acc.account_uuid == LA
    assert acc.resolved_from == "user"


def test_full_uuid_pair_form_resolves_live(mkenv, tmp_path, write_transcript,
                                           write_row):
    env = live_pair_env(mkenv, tmp_path, write_transcript, write_row)
    acc = ct._resolve_live_assertion(env, LA + "/" + LOA,
                                     ct._account_dirs(env),
                                     account_scope=True)
    assert acc.account_uuid == LA


def test_arbitrary_prefix_pair_resolves(mkenv, tmp_path, write_transcript,
                                        write_row):
    """Anchored prefixes are what a human shortening an id actually types."""
    env = live_pair_env(mkenv, tmp_path, write_transcript, write_row)
    acc = ct._resolve_live_assertion(env, "aaaa/cccc", ct._account_dirs(env),
                                     account_scope=True)
    assert acc.account_uuid == LA


def test_mid_uuid_fragment_pair_is_refused(mkenv, tmp_path, write_transcript,
                                           write_row):
    """'1111/cccc' IS a substring of LA's normalized store path (the uuid
    ends in aaa1111, the org starts cccc3333), so acceptance here would
    prove a silent fall back into substring semantics. It must instead get
    the ordinary no-match refusal, with the candidate listing."""
    env = live_pair_env(mkenv, tmp_path, write_transcript, write_row)
    with pytest.raises(ct.Refusal) as exc:
        ct._resolve_live_assertion(env, "1111/cccc", ct._account_dirs(env),
                                   account_scope=True)
    msg = str(exc.value)
    assert "matched neither account" in msg
    assert LA[:8] in msg and LB[:8] in msg


def test_non_hex_slash_query_stays_substring(mkenv, tmp_path,
                                             write_transcript, write_row):
    """A one-slash query with a non-hex half is NOT pair-shaped; it keeps
    substring semantics, paths included - 'sessions/aaaa1111' names LA's
    store the way a pasted path fragment always has. (Were it misread as a
    pair, 'sessions' prefixes no account uuid and it would refuse.)"""
    env = live_pair_env(mkenv, tmp_path, write_transcript, write_row)
    acc = ct._resolve_live_assertion(env, "sessions/aaaa1111",
                                     ct._account_dirs(env),
                                     account_scope=True)
    assert acc.account_uuid == LA


def test_live_store_path_with_a_trailing_separator_resolves(
        mkenv, tmp_path, write_transcript, write_row):
    """The 2026-10-20 separator ruling: a trailing separator may match the end
    of a store path, so a Tab-completed path names its account here too, in
    either spelling."""
    env = live_pair_env(mkenv, tmp_path, write_transcript, write_row)
    store = os.path.join(env.store_candidates[0], LA, LOA)
    other = "/" if os.sep == "\\" else "\\"
    for value in (store + os.sep, store.replace(os.sep, other) + other):
        acc = ct._resolve_live_assertion(env, value, ct._account_dirs(env),
                                         account_scope=True)
        assert acc.account_uuid == LA, value


def test_mid_uuid_pair_with_a_trailing_separator_is_still_refused(
        mkenv, tmp_path, write_transcript, write_row):
    """'1111/<LOA>' is the printed account form with a mid-uuid left half, and
    LA's folded store path ends in exactly that; with a trailing separator it
    must still be refused, never matched at the path's end."""
    env = live_pair_env(mkenv, tmp_path, write_transcript, write_row)
    for value in ("1111/" + LOA + "/", "1111/" + LOA + "\\"):
        with pytest.raises(ct.Refusal, match="matched neither account"):
            ct._resolve_live_assertion(env, value, ct._account_dirs(env),
                                       account_scope=True)


def test_email_live_accepted_at_account_scope(mkenv, tmp_path,
                                              write_transcript, write_row):
    """The measured refusal this change deletes: an email - the natural way
    a person names an account, unambiguous as one - was refused for
    matching the account's three org dirs. At account scope it resolves,
    and the returned Account carries EMPTY org and path: the assertion is
    account-level, so the label reads aaaa1111/- rather than a concrete
    pair a user might trust or re-paste."""
    env = live_pair_env(mkenv, tmp_path, write_transcript, write_row)
    for org in ("eeee5555-0000-4000-8000-000000000005",
                "ffff6666-0000-4000-8000-000000000006"):
        os.makedirs(os.path.join(env.store_candidates[0], LA, org))
    acc = ct._resolve_live_assertion(env, "alice@example.com",
                                     ct._account_dirs(env),
                                     account_scope=True)
    assert acc.account_uuid == LA
    assert acc.org_uuid == "" and acc.path == ""
    assert "{0}/{1}".format(acc.account_uuid[:8],
                            acc.org_uuid[:8] or "-") == "aaaa1111/-"
    m = ct.plan_converge(env, ct.ConvergeFlags(live="alice@example.com"))
    assert m["live_asserted"] == LA


def test_account_scope_works_with_no_store_dirs(mkenv, tmp_path,
                                                write_transcript, write_row):
    """An account whose store dirs are missing entirely is still assertable
    by uuid or email: the PRINCIPAL exists even when no directory does -
    the edge a store-backed resolver could never cover."""
    env = mkenv(tmp_path)
    write_transcript(env, "C--p", S1, t_entries())
    write_row(env, 0, LA, LOA, "local_s1", row_data(S1, "ACME-REVIEW"))
    identity_files(env, LA, LB)          # LB owns no directory anywhere
    acc = ct._resolve_live_assertion(env, "bbbb2222", ct._account_dirs(env),
                                     account_scope=True)
    assert acc.account_uuid == LB
    assert acc.org_uuid == "" and acc.path == ""
    m = ct.plan_converge(env, ct.ConvergeFlags(live="bbbb2222"))
    assert m["live_asserted"] == LB


def test_account_scope_refuses_across_accounts(mkenv, tmp_path,
                                               write_transcript, write_row):
    """A string matching BOTH principals refuses even at account scope -
    widening what one account's principal absorbs never widens what an
    ambiguous string selects."""
    env = live_pair_env(mkenv, tmp_path, write_transcript, write_row)
    with pytest.raises(ct.Refusal, match="matches both accounts"):
        ct._resolve_live_assertion(env, "-0000-", ct._account_dirs(env),
                                   account_scope=True)


def test_apply_refusal_leads_with_live(
        mkenv, tmp_path, write_transcript, write_row):
    """Change 4: the remedies ordered by what can actually work. --live
    first (it works in every disagreement); re-authentication scoped to
    the CLI-stale case (/login rewrites only ~/.claude.json); the desktop
    switch caveated "may not" - config.json has been measured both
    tracking a switch and keeping the previous account across one."""
    env = mkenv(tmp_path)
    one_missing_pair(env, write_transcript, write_row)
    identity_files(env, A1, A2)
    m = ct.plan_converge(env, ct.ConvergeFlags())
    with pytest.raises(ct.Refusal) as exc:
        ct.run_converge(env, m)
    msg = str(exc.value)
    assert msg.index("--live") < msg.index("/login")
    assert "If it is the CLI's record that is stale" in msg
    assert "Switching the desktop app may not" in msg
    assert "Nothing was written" in msg


# --------------------------------- 0.13.0: corroboration is a note, not an error

def test_corroborated_live_proceeds_with_note(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    """The measured ping-pong: plain apply refused (disagreement) -> --live
    added -> the app rewrote its file meanwhile -> --live refused
    (agreement) -> flag stripped again. A --live naming the very account
    the files agree on, and no other, is corroboration: the run proceeds
    with a note, and records NO assertion - nothing was arbitrated."""
    env = mkenv(tmp_path)
    one_missing_pair(env, write_transcript, write_row)
    identity_files(env, A1, A1, oauth_org=O1)
    rc = ct.cmd_converge(env, cv_ns(live="alice@example.com"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "no arbitration was needed" in out
    m = ct.plan_converge(env, ct.ConvergeFlags(live="alice@example.com"))
    assert m["live_asserted"] == ""


def test_corroborated_live_ambiguous_is_refused(
        mkenv, tmp_path, write_transcript, write_row):
    """The uniqueness check runs over EVERY account on the machine - with
    no disagreement there is no two-candidate frame to bound it. A string
    that also matches some other account's principal is refused with the
    listing: a safe annoyance, never a mis-selection."""
    env = mkenv(tmp_path)
    one_missing_pair(env, write_transcript, write_row)
    identity_files(env, A1, A1, oauth_org=O1)
    os.makedirs(os.path.join(env.store_candidates[0],
                             "aaaa9999-0000-0000-0000-000000000009",
                             "bbbb8888-0000-0000-0000-000000000008"))
    with pytest.raises(ct.Refusal) as exc:
        ct.plan_converge(env, ct.ConvergeFlags(live="aaaa"))
    msg = str(exc.value)
    assert "also matches" in msg
    assert "aaaa9999" in msg


def test_live_naming_other_account_while_agreeing_refused(
        mkenv, tmp_path, write_transcript, write_row):
    """A --live naming anything OTHER than the agreed account while the
    files agree keeps today's refusal - that case really is evidence of
    confusion, and refusing it is the feature."""
    env = mkenv(tmp_path)
    one_missing_pair(env, write_transcript, write_row)
    identity_files(env, A1, A1, oauth_org=O1)
    with pytest.raises(ct.Refusal) as exc:
        ct.plan_converge(env, ct.ConvergeFlags(live=A2[:8]))
    msg = str(exc.value)
    assert "do not currently disagree" in msg
    assert "Re-run without --live" in msg
    assert A1[:8] in msg                 # names the account that resolves


def test_json_stdout_stays_pure(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    """Both changed output paths - warning present, corroborated note
    present - emit parseable JSON with no stray prose: the warning is
    non-JSON only (the manifest field is the machine signal), and the note
    rides the manifest's notes array."""
    env = mkenv(tmp_path)
    one_missing_pair(env, write_transcript, write_row)
    identity_files(env, A1, A2)
    rc = ct.cmd_converge(env, cv_ns(json=True))
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["identity_disagreement"] == {"oauth": A1, "config": A2}

    desktop_config(env, A1)              # agreement; --live corroborates
    rc = ct.cmd_converge(env, cv_ns(json=True, live="alice@example.com"))
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["live_asserted"] == ""
    assert any(isinstance(n, str) and "no arbitration was needed" in n
               for n in payload["notes"])


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


# ------------------- 0.14.0: measured hold remedies (hold-remedies design)

def ms_local(y, mo, d, h=12, mi=0):
    """Epoch ms for a LOCAL-time instant. The generated title's range renders
    in local time, so fixtures built this way assert exact strings on any
    runner timezone; noon keeps DST edges out of the arithmetic."""
    return int(datetime.datetime(y, mo, d, h, mi).timestamp() * 1000)


def leg_rows(created, last):
    return [{"createdAt": created, "lastActivityAt": last}]


def test_generated_title_shape():
    """Spec test 9: the four range grammars, min(createdAt) to
    max(lastActivityAt) across the leg's rows."""
    def gen(rows):
        title, why = ct._superseded_leg_title("ACME-REVIEW session", rows)
        assert why is None
        return title
    base = "ACME-REVIEW session - earlier leg ({0})"
    assert gen(leg_rows(ms_local(2026, 8, 28), ms_local(2026, 8, 28))) == \
        base.format("Aug 28")
    assert gen(leg_rows(ms_local(2026, 8, 24), ms_local(2026, 8, 28))) == \
        base.format("Aug 24-28")
    assert gen(leg_rows(ms_local(2026, 8, 24), ms_local(2026, 9, 2))) == \
        base.format("Aug 24 - Sep 2")
    assert gen(leg_rows(ms_local(2026, 12, 30), ms_local(2027, 1, 2))) == \
        base.format("2026-12-30 - 2027-01-02")
    # The range spans EVERY row of the leg, not whichever came first.
    assert gen(leg_rows(ms_local(2026, 8, 25), ms_local(2026, 8, 26))
               + leg_rows(ms_local(2026, 8, 24), ms_local(2026, 8, 28))) == \
        base.format("Aug 24-28")


def test_exact_suffix_is_replaced_lookalike_degrades():
    """Spec test 10: a byte-exact generated suffix is tool provenance - its
    range is refreshed; a tail that merely resembles one is a human's prose
    and degrades rather than being rewritten."""
    rows = leg_rows(ms_local(2026, 8, 24), ms_local(2026, 8, 28))
    fresh = "ACME-REVIEW session - earlier leg (Aug 24-28)"
    assert ct._superseded_leg_title(
        "ACME-REVIEW session - earlier leg (Aug 1-3)", rows) == (fresh, None)
    assert ct._superseded_leg_title(
        "ACME-REVIEW session - earlier leg (2026-12-30 - 2027-01-02)",
        rows) == (fresh, None)
    title, why = ct._superseded_leg_title(
        "ACME-REVIEW session - earlier leg (probably)", rows)
    assert title is None
    assert why == "title already carries a leg suffix"


T_COLL = "ACME-REVIEW session"
SUGGESTED = "ACME-REVIEW session - earlier leg (Aug 24-28)"
S4 = "fedcba98-9abc-def0-1234-56789abcdef0"


def prose_transcript(labels, cwd="C:\\Users\\u\\Projects\\Northwind"):
    """A transcript whose prose turns are exactly LABELS (each one distinct
    prose), and which _transcript_facts can populate a row from - a cwd,
    usable timestamps, an assistant record carrying the model."""
    entries = [
        {"cwd": cwd, "timestamp": "2026-08-01T00:00:00.000Z", "type": "user",
         "message": {"role": "user", "content": "prose turn " + labels[0]}},
        {"timestamp": "2026-08-01T00:10:00.000Z", "type": "assistant",
         "message": {"role": "assistant", "model": "claude-opus-5",
                     "content": [{"type": "text",
                                  "text": "prose turn " + labels[1]}]}},
    ]
    for lab in labels[2:]:
        entries.append({"type": "user",
                        "message": {"role": "user",
                                    "content": "prose turn " + lab}})
    return entries


def turn_labels(prefix, n):
    return ["{0}{1}".format(prefix, i) for i in range(n)]


def measured_pair(env, write_transcript, write_row, title=T_COLL,
                  early=None, late=None, third_dest=False,
                  early_last=None, late_last=None):
    """The 2026-08-29 shape, fake cast: S1 the earlier leg, S2 the current
    one, one title between them. S1 held by A1 only and S2 by A2 only, so
    each collides with the other's row in the opposite sidebar (shape (a),
    both directions); THIRD_DEST parks a dead PAD row in A3 so the
    planned-vs-planned rule adds a shape (b) hold there too."""
    shared = turn_labels("s", 48)
    write_transcript(env, "C--p", S1,
                     prose_transcript(shared + (early or turn_labels("a", 2))))
    write_transcript(env, "C--p", S2,
                     prose_transcript(shared + (late or turn_labels("b", 13))))
    write_row(env, 0, A1, O1, "local_s1",
              row_data(S1, title, createdAt=ms_local(2026, 8, 24),
                       lastActivityAt=early_last or ms_local(2026, 8, 28)))
    write_row(env, 0, A2, O2, "local_s2",
              row_data(S2, title, createdAt=ms_local(2026, 8, 24),
                       lastActivityAt=late_last or ms_local(2026, 8, 29)))
    if third_dest:
        write_row(env, 0, A3, O3, "local_pad", row_data(PAD, "Padding"))


def collision_holds(m):
    return [h for h in m["holds"] if h["reason"] == "held_title_collision"]


def test_supersession_hold_names_the_superseded_leg(
        mkenv, tmp_path, write_transcript, write_row):
    """Spec test 1: asymmetric overlap (48/50 vs 48/61), the contained side
    also older - the remedy names THAT sid in both directions of the hold,
    with the generated title, complete."""
    env = mkenv(tmp_path)
    measured_pair(env, write_transcript, write_row)
    m = ct.plan_converge(env, ct.ConvergeFlags())
    held = collision_holds(m)
    assert len(held) == 2
    want = ('claude-code-sessions retitle --only {0} --title "{1}" '
            '--apply'.format(S1[:8], SUGGESTED))
    for h in held:
        mm = h["measured"]
        assert mm["classification"] == "supersession"
        assert mm["superseded"] == S1 and mm["current"] == S2
        assert mm["shared"] == 48
        assert {mm["a"], mm["b"]} == {S1, S2}
        if mm["a"] == S1:
            assert (mm["a_total"], mm["b_total"]) == (50, 61)
        else:
            assert (mm["a_total"], mm["b_total"]) == (61, 50)
        assert mm["suggested_title"] == SUGGESTED
        assert mm["degrade_reason"] is None
        assert mm["command_runnable"] is True
    pub = ct._public_converge_manifest(env, m)
    assert [h["retitle"] for h in collision_holds(pub)] == [want, want]


def test_both_directions_of_a_group_print_one_identical_remedy(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    """Spec test 2: holds both ways plus the shape (b) third destination -
    every hold of the group carries the SAME command (the repeated
    (sid, title) suggestion is one suggestion, not a self-collision)."""
    env = mkenv(tmp_path)
    measured_pair(env, write_transcript, write_row, third_dest=True)
    m = ct.plan_converge(env, ct.ConvergeFlags())
    held = collision_holds(m)
    assert len(held) == 3
    for h in held:
        assert h["measured"]["command_runnable"] is True
        assert h["measured"]["degrade_reason"] is None
        assert h["measured"]["suggested_title"] == SUGGESTED
    rc = ct.cmd_converge(env, cv_ns())
    out = capsys.readouterr().out
    assert rc == 0
    want = ('claude-code-sessions retitle --only {0} --title "{1}" '
            '--apply'.format(S1[:8], SUGGESTED))
    assert out.count(want) == 3


def test_replan_after_the_paste_clears_the_group(
        mkenv, tmp_path, write_transcript, write_row):
    """Spec test 3: apply the suggested title to the superseded leg's rows -
    a fresh plan holds NOTHING for the pair, shapes (a) and (b) alike,
    because renaming either leg of a two-leg group changes its key."""
    env = mkenv(tmp_path)
    measured_pair(env, write_transcript, write_row, third_dest=True)
    m = ct.plan_converge(env, ct.ConvergeFlags())
    suggested = collision_holds(m)[0]["measured"]["suggested_title"]
    assert suggested == SUGGESTED
    # The paste, simulated: retitle rewrites every row of the superseded leg.
    p = os.path.join(env.store_candidates[0], A1, O1, "local_s1.json")
    with open(p, encoding="utf-8") as fh:
        d = json.load(fh)
    d["title"] = suggested
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(d, fh)
    m2 = ct.plan_converge(env, ct.ConvergeFlags())
    assert m2["holds"] == []
    pairs = sorted((r["session"], r["account"]) for r in m2["rows"])
    assert pairs == [(S1, A2), (S1, A3), (S2, A1), (S2, A3)]


def test_overlap_picks_the_side_recency_confirms(
        mkenv, tmp_path, write_transcript, write_row):
    """Spec test 4: symmetric ratios (the mutual-fork shape all three
    2026-08-29 pairs had) - recency picks the superseded leg, past the
    margin."""
    env = mkenv(tmp_path)
    measured_pair(env, write_transcript, write_row,
                  early=turn_labels("a", 2), late=turn_labels("b", 2))
    m = ct.plan_converge(env, ct.ConvergeFlags())
    held = collision_holds(m)
    assert len(held) == 2
    for h in held:
        mm = h["measured"]
        assert mm["classification"] == "supersession"
        assert mm["superseded"] == S1 and mm["current"] == S2
        assert mm["command_runnable"] is True


def test_planned_collision_shape_is_measured_too(
        mkenv, tmp_path, write_transcript, write_row):
    """Spec test 28: the shape (b) hold - whose text otherwise never names
    the counterpart - redirects its remedy and its measured line carries
    both sids."""
    env = mkenv(tmp_path)
    measured_pair(env, write_transcript, write_row, third_dest=True)
    m = ct.plan_converge(env, ct.ConvergeFlags())
    shape_b = [h for h in collision_holds(m)
               if "already creates" in h["detail"]]
    assert len(shape_b) == 1
    h = shape_b[0]
    assert h["session"] == S2 and h["account"] == A3
    assert h["measured"]["classification"] == "supersession"
    assert h["measured"]["superseded"] == S1
    assert S1[:8] in h["measured_line"] and S2[:8] in h["measured_line"]
    pub = ct._public_converge_manifest(env, m)
    b = [x for x in collision_holds(pub) if "already creates" in x["detail"]]
    assert "--only {0}".format(S1[:8]) in b[0]["retitle"]
    assert SUGGESTED in b[0]["retitle"]


def test_current_leg_wearing_suffix_gets_the_note(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    """Spec test 25d: a fork of an already-renamed leg - the colliding title
    itself wears the exact generated grammar. The remedy still renames only
    the superseded side (range refreshed); the fixed note line says the
    current leg likely wants a fresh name."""
    env = mkenv(tmp_path)
    worn = "ACME-REVIEW session - earlier leg (Aug 1-3)"
    measured_pair(env, write_transcript, write_row, title=worn)
    m = ct.plan_converge(env, ct.ConvergeFlags())
    held = collision_holds(m)
    assert len(held) == 2
    for h in held:
        assert h["measured"]["suggested_title"] == SUGGESTED
        assert h["measured"]["command_runnable"] is True
        assert h.get("leg_suffix_note") is True
    rc = ct.cmd_converge(env, cv_ns())
    out = capsys.readouterr().out
    assert rc == 0
    assert ("note: the current leg also carries a leg suffix and likely "
            "wants a fresh name") in out


def assert_unmeasured(m, reason, count=2):
    held = collision_holds(m)
    assert len(held) == count
    for h in held:
        mm = h["measured"]
        assert mm["classification"] == "unmeasured"
        assert mm["reason"] == reason
        assert mm["suggested_title"] is None
        assert mm["command_runnable"] is False
        assert "<new title>" in h["retitle"]
    return held


def test_overlap_and_recency_disagreeing_is_unmeasured(
        mkenv, tmp_path, write_transcript, write_row):
    """Spec test 5: the contained leg is the NEWER one - the trunk touched
    after forking. When the two signals point at different legs, the honest
    output is neither."""
    env = mkenv(tmp_path)
    measured_pair(env, write_transcript, write_row,
                  early_last=ms_local(2026, 8, 29),
                  late_last=ms_local(2026, 8, 28))
    m = ct.plan_converge(env, ct.ConvergeFlags())
    assert_unmeasured(m, "overlap and recency disagree")


def test_inconclusive_band_is_unmeasured_with_ratios(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    """Spec test 6: overlap 0.5 sits between the bands - hedged output with
    both ratios, never a confident wrong sentence."""
    env = mkenv(tmp_path)
    shared = turn_labels("s", 8)
    write_transcript(env, "C--p", S1,
                     prose_transcript(shared + turn_labels("a", 8)))
    write_transcript(env, "C--p", S2,
                     prose_transcript(shared + turn_labels("b", 8)))
    write_row(env, 0, A1, O1, "local_s1",
              row_data(S1, T_COLL, createdAt=ms_local(2026, 8, 24),
                       lastActivityAt=ms_local(2026, 8, 28)))
    write_row(env, 0, A2, O2, "local_s2",
              row_data(S2, T_COLL, createdAt=ms_local(2026, 8, 24),
                       lastActivityAt=ms_local(2026, 8, 29)))
    m = ct.plan_converge(env, ct.ConvergeFlags())
    held = assert_unmeasured(m, "inconclusive overlap")
    for h in held:
        assert h["measured"]["shared"] == 8
        assert h["measured"]["a_total"] == 16
        assert h["measured"]["b_total"] == 16
        assert h["measured_line"].count("0.50") == 2
    rc = ct.cmd_converge(env, cv_ns())
    out = capsys.readouterr().out
    assert rc == 0
    assert "not measured: inconclusive overlap" in out
    assert "0.50" in out


def test_band_edges(mkenv, tmp_path, write_transcript, write_row):
    """Spec test 7: 0.8 exactly is supersession-shaped; 0.2 exactly is
    distinct."""
    env = mkenv(tmp_path / "hi")
    shared = turn_labels("s", 8)
    # |A| = 10, |B| = 40: a_in_b = 0.8 exactly, and recency corroborates.
    write_transcript(env, "C--p", S1,
                     prose_transcript(shared + turn_labels("a", 2)))
    write_transcript(env, "C--p", S2,
                     prose_transcript(shared + turn_labels("b", 32)))
    write_row(env, 0, A1, O1, "local_s1",
              row_data(S1, T_COLL, createdAt=ms_local(2026, 8, 24),
                       lastActivityAt=ms_local(2026, 8, 28)))
    write_row(env, 0, A2, O2, "local_s2",
              row_data(S2, T_COLL, createdAt=ms_local(2026, 8, 24),
                       lastActivityAt=ms_local(2026, 8, 29)))
    m = ct.plan_converge(env, ct.ConvergeFlags())
    held = collision_holds(m)
    assert len(held) == 2
    for h in held:
        assert h["measured"]["classification"] == "supersession"
        assert h["measured"]["superseded"] == S1

    env = mkenv(tmp_path / "lo")
    # |A| = |B| = 40, shared 8: both ratios 0.2 exactly.
    write_transcript(env, "C--p", S1,
                     prose_transcript(shared + turn_labels("a", 32)))
    write_transcript(env, "C--p", S2,
                     prose_transcript(shared + turn_labels("b", 32)))
    write_row(env, 0, A1, O1, "local_s1",
              row_data(S1, T_COLL, createdAt=ms_local(2026, 8, 24),
                       lastActivityAt=ms_local(2026, 8, 28)))
    write_row(env, 0, A2, O2, "local_s2",
              row_data(S2, T_COLL, createdAt=ms_local(2026, 8, 24),
                       lastActivityAt=ms_local(2026, 8, 29)))
    m = ct.plan_converge(env, ct.ConvergeFlags())
    held = collision_holds(m)
    assert len(held) == 2
    for h in held:
        assert h["measured"]["classification"] == "distinct"


def test_distinct_wording_claims_no_ancestry(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    """Spec test 8: near-zero overlap says 'largely distinct', never
    'branch' - a shared title does not prove shared ancestry, and the
    output must not claim it."""
    env = mkenv(tmp_path)
    shared = turn_labels("s", 2)
    write_transcript(env, "C--p", S1,
                     prose_transcript(shared + turn_labels("a", 39)))
    write_transcript(env, "C--p", S2,
                     prose_transcript(shared + turn_labels("b", 36)))
    write_row(env, 0, A1, O1, "local_s1",
              row_data(S1, T_COLL, createdAt=ms_local(2026, 8, 24),
                       lastActivityAt=ms_local(2026, 8, 28)))
    write_row(env, 0, A2, O2, "local_s2",
              row_data(S2, T_COLL, createdAt=ms_local(2026, 8, 24),
                       lastActivityAt=ms_local(2026, 8, 29)))
    m = ct.plan_converge(env, ct.ConvergeFlags())
    held = collision_holds(m)
    assert len(held) == 2
    for h in held:
        mm = h["measured"]
        assert mm["classification"] == "distinct"
        assert mm["reason"] is None
        assert mm["shared"] == 2
        assert {mm["a_total"], mm["b_total"]} == {41, 38}
        assert "largely distinct" in h["measured_line"]
        assert "branch" not in h["measured_line"]
        assert "both need human names" in h["measured_line"]
        assert mm["suggested_title"] is None
        assert "<new title>" in h["retitle"]
    rc = ct.cmd_converge(env, cv_ns())
    out = capsys.readouterr().out
    assert rc == 0
    assert "measured: largely distinct conversations" in out


def test_missing_row_dates_degrade(mkenv, tmp_path, write_transcript,
                                   write_row):
    """Spec test 16: a null lastActivityAt on one row, and an inverted
    createdAt > lastActivityAt - both 'row dates unusable'."""
    env = mkenv(tmp_path / "null")
    measured_pair(env, write_transcript, write_row)
    p = os.path.join(env.store_candidates[0], A1, O1, "local_s1.json")
    with open(p, encoding="utf-8") as fh:
        d = json.load(fh)
    d["lastActivityAt"] = None
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(d, fh)
    m = ct.plan_converge(env, ct.ConvergeFlags())
    assert_unmeasured(m, "row dates unusable")

    env = mkenv(tmp_path / "inv")
    measured_pair(env, write_transcript, write_row,
                  early_last=ms_local(2026, 8, 20))  # before its createdAt
    m = ct.plan_converge(env, ct.ConvergeFlags())
    assert_unmeasured(m, "row dates unusable")


def test_recency_tie_is_unmeasured(mkenv, tmp_path, write_transcript,
                                   write_row):
    """Spec test 17: equal max(lastActivityAt) in BOTH the symmetric and the
    asymmetric branch - checked before anything else, so neither branch has
    an undefined tie state."""
    tie = ms_local(2026, 8, 28)
    env = mkenv(tmp_path / "sym")
    measured_pair(env, write_transcript, write_row,
                  early=turn_labels("a", 2), late=turn_labels("b", 2),
                  early_last=tie, late_last=tie)
    m = ct.plan_converge(env, ct.ConvergeFlags())
    assert_unmeasured(m, "legs cannot be ordered")

    env = mkenv(tmp_path / "asym")
    measured_pair(env, write_transcript, write_row,
                  early_last=tie, late_last=tie)
    m = ct.plan_converge(env, ct.ConvergeFlags())
    assert_unmeasured(m, "legs cannot be ordered")


def test_symmetric_recency_below_margin_is_unmeasured(
        mkenv, tmp_path, write_transcript, write_row):
    """Spec test 25c: symmetric ratios with an activity gap under
    RECENCY_MARGIN_MS - timestamp jitter and touch-updates cannot decide."""
    env = mkenv(tmp_path)
    measured_pair(env, write_transcript, write_row,
                  early=turn_labels("a", 2), late=turn_labels("b", 2),
                  early_last=ms_local(2026, 8, 28, 12, 0),
                  late_last=ms_local(2026, 8, 28, 12, 4))
    m = ct.plan_converge(env, ct.ConvergeFlags())
    assert_unmeasured(m, "legs cannot be ordered")


@pytest.mark.parametrize("ch", ['"', "$", "`", "\\", "!", "%"])
def test_shell_unsafe_title_degrades_but_suggests(
        mkenv, tmp_path, write_transcript, write_row, ch, capsys):
    """Spec test 11: each metacharacter that stays live inside double quotes
    in at least one target shell degrades the COMMAND only - the title
    itself is valid, so the suggestion survives as prose for the GUI."""
    env = mkenv(tmp_path)
    unsafe = "ACME {0} review".format(ch)
    measured_pair(env, write_transcript, write_row, title=unsafe)
    m = ct.plan_converge(env, ct.ConvergeFlags())
    held = collision_holds(m)
    assert len(held) == 2
    want = unsafe + " - earlier leg (Aug 24-28)"
    for h in held:
        mm = h["measured"]
        assert mm["classification"] == "supersession"
        assert mm["degrade_reason"] == "title not shell-safe"
        assert mm["command_runnable"] is False
        assert mm["suggested_title"] == want
        assert "<new title>" in h["retitle"]
        assert "--only {0}".format(S1[:8]) in h["retitle"]
    rc = ct.cmd_converge(env, cv_ns())
    out = capsys.readouterr().out
    assert rc == 0
    assert "suggested name: {0}".format(want) in out
    assert not any(want in ln for ln in out.splitlines()
                   if "retitle --only" in ln)


def test_unprintable_title_drops_the_suggestion(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    """Spec test 11b: a control character forges report lines, so that
    degrade nulls the suggestion entirely - and no raw control byte appears
    anywhere in the rendered report."""
    env = mkenv(tmp_path / "bel")
    measured_pair(env, write_transcript, write_row, title="ACME\x07review")
    m = ct.plan_converge(env, ct.ConvergeFlags())
    held = collision_holds(m)
    assert len(held) == 2
    for h in held:
        mm = h["measured"]
        assert mm["degrade_reason"] == "title not printable"
        assert mm["suggested_title"] is None
        assert mm["command_runnable"] is False
        assert "<new title>" in h["retitle"]
    rc = ct.cmd_converge(env, cv_ns())
    out = capsys.readouterr().out
    assert rc == 0
    assert "\x07" not in out

    env = mkenv(tmp_path / "nl")
    measured_pair(env, write_transcript, write_row, title="ACME\nreview")
    m = ct.plan_converge(env, ct.ConvergeFlags())
    held = collision_holds(m)
    assert len(held) == 2
    for h in held:
        assert h["measured"]["degrade_reason"] == "title not printable"
        assert h["measured"]["suggested_title"] is None


def test_divergent_leg_titles_degrade(mkenv, tmp_path, write_transcript,
                                      write_row):
    """Spec test 12: retitle --only renames a conversation in EVERY account;
    a leg whose titles already diverge is mid-repair by other means, and a
    global rename would flatten intentional differences."""
    env = mkenv(tmp_path)
    measured_pair(env, write_transcript, write_row)
    # A second row of the SUPERSEDED leg, under a different title.
    write_row(env, 0, A3, O3, "local_s1b",
              row_data(S1, "Northwind variant",
                       createdAt=ms_local(2026, 8, 24),
                       lastActivityAt=ms_local(2026, 8, 27)))
    m = ct.plan_converge(env, ct.ConvergeFlags())
    held = [h for h in collision_holds(m)
            if h["measured"]["classification"] == "supersession"]
    assert held
    for h in held:
        mm = h["measured"]
        assert mm["superseded"] == S1
        assert mm["degrade_reason"] == \
            "the leg's titles diverge across accounts"
        assert mm["suggested_title"] is None
        assert mm["command_runnable"] is False


def test_taken_suggestion_degrades(mkenv, tmp_path, write_transcript,
                                   write_row):
    """Spec test 13: a generated title whose key any account already holds
    is not a suggestion - including when the LATER leg's own (divergent)
    title equals it under title_key."""
    env = mkenv(tmp_path / "row")
    measured_pair(env, write_transcript, write_row)
    write_row(env, 0, A3, O3, "local_taken", row_data(PAD, SUGGESTED))
    m = ct.plan_converge(env, ct.ConvergeFlags())
    held = [h for h in collision_holds(m)
            if h["measured"]["classification"] == "supersession"]
    assert held
    for h in held:
        assert h["measured"]["degrade_reason"] == \
            "suggested name already taken"
        assert h["measured"]["suggested_title"] is None

    env = mkenv(tmp_path / "leg")
    measured_pair(env, write_transcript, write_row)
    # The later leg itself carries the would-be suggestion in a third
    # account (whitespace variant: title_key is the comparator).
    write_row(env, 0, A3, O3, "local_s2b",
              row_data(S2, "  " + SUGGESTED + "  ",
                       createdAt=ms_local(2026, 8, 24),
                       lastActivityAt=ms_local(2026, 8, 27)))
    m = ct.plan_converge(env, ct.ConvergeFlags())
    held = [h for h in collision_holds(m)
            if h["measured"]["classification"] == "supersession"]
    assert held
    for h in held:
        assert h["measured"]["degrade_reason"] == \
            "suggested name already taken"
        assert h["measured"]["suggested_title"] is None


def test_two_different_targets_cannot_share_a_suggestion(
        mkenv, tmp_path, write_transcript, write_row):
    """Spec test 14: two groups whose generated titles coincide (one base
    plain, one wearing an exact suffix that strips to the same base and
    range) - the second target degrades rather than printing one name for
    two conversations."""
    env = mkenv(tmp_path)
    measured_pair(env, write_transcript, write_row)
    worn = "ACME-REVIEW session - earlier leg (Aug 1-3)"
    shared = turn_labels("t", 48)
    write_transcript(env, "C--p", S3,
                     prose_transcript(shared + turn_labels("c", 2)))
    write_transcript(env, "C--p", S4,
                     prose_transcript(shared + turn_labels("d", 13)))
    write_row(env, 0, A1, O1, "local_s3",
              row_data(S3, worn, createdAt=ms_local(2026, 8, 24),
                       lastActivityAt=ms_local(2026, 8, 28)))
    write_row(env, 0, A2, O2, "local_s4",
              row_data(S4, worn, createdAt=ms_local(2026, 8, 24),
                       lastActivityAt=ms_local(2026, 8, 29)))
    m = ct.plan_converge(env, ct.ConvergeFlags())
    per_pair = {}
    for h in collision_holds(m):
        pair = frozenset((h["measured"]["a"], h["measured"]["b"]))
        per_pair.setdefault(pair, []).append(h["measured"])
    first = per_pair[frozenset((S1, S2))]
    second = per_pair[frozenset((S3, S4))]
    assert all(mm["command_runnable"] for mm in first)
    assert all(mm["suggested_title"] == SUGGESTED for mm in first)
    assert all(mm["degrade_reason"] == "suggested name already taken"
               for mm in second)
    assert all(mm["suggested_title"] is None for mm in second)


def counting_measure(monkeypatch):
    """Instrument the measurement layer: census-excluded groups must never
    reach _measure_fingerprints at all, and added READS are visible at the
    _measure_load seam."""
    calls = {"fingerprints": [], "loads": []}
    real_fp = ct._measure_fingerprints
    real_load = ct._measure_load

    def fp(env, sid, cache, budget):
        calls["fingerprints"].append(sid)
        return real_fp(env, sid, cache, budget)

    def load(path):
        calls["loads"].append(path)
        return real_load(path)
    monkeypatch.setattr(ct, "_measure_fingerprints", fp)
    monkeypatch.setattr(ct, "_measure_load", load)
    return calls


def test_three_leg_group_degrades_every_hold(
        mkenv, tmp_path, write_transcript, write_row, monkeypatch):
    """Spec test 11c: three conversations on one title_key form a collision
    graph no single rename clears - every hold degrades wholesale, and
    nothing is fingerprinted for the group."""
    env = mkenv(tmp_path)
    calls = counting_measure(monkeypatch)
    for i, (sid, acct, org) in enumerate(((S1, A1, O1), (S2, A2, O2),
                                          (S3, A3, O3))):
        write_transcript(env, "C--p", sid,
                         prose_transcript(turn_labels("g{0}x".format(i), 10)))
        write_row(env, 0, acct, org, "local_g{0}".format(i),
                  row_data(sid, T_COLL, createdAt=ms_local(2026, 8, 24),
                           lastActivityAt=ms_local(2026, 8, 25 + i)))
    m = ct.plan_converge(env, ct.ConvergeFlags())
    held = collision_holds(m)
    assert len(held) == 6
    for h in held:
        assert h["measured"]["classification"] == "unmeasured"
        assert h["measured"]["reason"] == \
            "more than two legs share this title"
        assert h["measured"]["a_total"] is None
        assert "<new title>" in h["retitle"]
    assert calls["fingerprints"] == []
    assert calls["loads"] == []


def test_planned_three_way_collision_degrades(
        mkenv, tmp_path, write_transcript, write_row):
    """Spec test 25b: three conversations whose CHOSEN titles coincide while
    no existing row carries the key - untitled rows, one customTitle - are
    caught by the same prepass census as a three-row group."""
    env = mkenv(tmp_path)
    for i, (sid, acct, org) in enumerate(((S1, A1, O1), (S2, A2, O2),
                                          (S3, A3, O3))):
        entries = prose_transcript(turn_labels("p{0}x".format(i), 10))
        entries.append({"customTitle": T_COLL})
        write_transcript(env, "C--p", sid, entries)
        write_row(env, 0, acct, org, "local_p{0}".format(i),
                  row_data(sid, "", createdAt=ms_local(2026, 8, 24),
                           lastActivityAt=ms_local(2026, 8, 25 + i)))
    m = ct.plan_converge(env, ct.ConvergeFlags())
    held = collision_holds(m)
    assert held
    assert all(h["measured"]["reason"] ==
               "more than two legs share this title" for h in held)
    assert all("already creates" in h["detail"] for h in held)


def two_blocked_pairs(env, write_transcript, write_row, s3_turns=40):
    """Two independent collision pairs whose blockers are COMPLETE (rows in
    both accounts), so measuring each blocker costs one added read: S1
    collides with S3 in A2, S2 collides with S4 in A1. No prose is shared,
    so a measured pair classifies distinct. Returns the blockers' transcript
    paths."""
    write_transcript(env, "C--p", S1, prose_transcript(turn_labels("a", 10)))
    write_transcript(env, "C--p", S2, prose_transcript(turn_labels("b", 10)))
    p3 = write_transcript(env, "C--p", S3,
                          prose_transcript(turn_labels("c", s3_turns)))
    p4 = write_transcript(env, "C--p", S4,
                          prose_transcript(turn_labels("d", 10)))
    write_row(env, 0, A1, O1, "local_s1",
              row_data(S1, "ACME-REVIEW", createdAt=ms_local(2026, 8, 24),
                       lastActivityAt=ms_local(2026, 8, 28)))
    write_row(env, 0, A2, O2, "local_s2",
              row_data(S2, "Northwind kickoff",
                       createdAt=ms_local(2026, 8, 24),
                       lastActivityAt=ms_local(2026, 8, 28)))
    for acct, org, name in ((A1, O1, "local_s3a"), (A2, O2, "local_s3b")):
        write_row(env, 0, acct, org, name,
                  row_data(S3, "ACME-REVIEW", createdAt=ms_local(2026, 8, 24),
                           lastActivityAt=ms_local(2026, 8, 29)))
    for acct, org, name in ((A1, O1, "local_s4a"), (A2, O2, "local_s4b")):
        write_row(env, 0, acct, org, name,
                  row_data(S4, "Northwind kickoff",
                           createdAt=ms_local(2026, 8, 24),
                           lastActivityAt=ms_local(2026, 8, 29)))
    return p3, p4


def pair_holds(m):
    out = {}
    for h in collision_holds(m):
        pair = frozenset((h["measured"]["a"], h["measured"]["b"]))
        out.setdefault(pair, []).append(h["measured"])
    return out


def test_dead_collision_row_degrades(mkenv, tmp_path, write_transcript,
                                     write_row):
    """Spec test 18: the blocking conversation has no transcript - that side
    is 'no transcript', the numeric trio does not exist, and the plan
    otherwise proceeds (regression: no Refusal)."""
    env = mkenv(tmp_path)
    write_transcript(env, "C--p", S1, prose_transcript(turn_labels("a", 10)))
    write_transcript(env, "C--p", S2, prose_transcript(turn_labels("b", 10)))
    write_row(env, 0, A1, O1, "local_s1",
              row_data(S1, T_COLL, createdAt=ms_local(2026, 8, 24),
                       lastActivityAt=ms_local(2026, 8, 28)))
    write_row(env, 0, A1, O1, "local_s2",
              row_data(S2, "Northwind kickoff",
                       createdAt=ms_local(2026, 8, 24),
                       lastActivityAt=ms_local(2026, 8, 28)))
    write_row(env, 0, A2, O2, "local_dead", row_data(S3, T_COLL))
    m = ct.plan_converge(env, ct.ConvergeFlags())
    held = collision_holds(m)
    assert len(held) == 1
    mm = held[0]["measured"]
    assert mm["classification"] == "unmeasured"
    assert mm["reason"] == "no transcript"
    assert (mm["a"], mm["b"]) == (S1, S3)
    assert mm["shared"] is None and mm["a_total"] is None \
        and mm["b_total"] is None
    assert any(r["session"] == S2 for r in m["rows"])


def test_ambiguous_transcript_count_degrades(mkenv, tmp_path,
                                             write_transcript, write_row):
    """Spec test 19: one sid in two project dirs - comparing [0] would
    measure a file we only might have meant, so the pair is unmeasured."""
    env = mkenv(tmp_path)
    write_transcript(env, "C--p", S1, prose_transcript(turn_labels("a", 10)))
    write_transcript(env, "C--p", S3, prose_transcript(turn_labels("c", 10)))
    write_transcript(env, "C--q", S3, prose_transcript(turn_labels("c", 10)))
    write_row(env, 0, A1, O1, "local_s1",
              row_data(S1, T_COLL, createdAt=ms_local(2026, 8, 24),
                       lastActivityAt=ms_local(2026, 8, 28)))
    write_row(env, 0, A2, O2, "local_s3",
              row_data(S3, T_COLL, createdAt=ms_local(2026, 8, 24),
                       lastActivityAt=ms_local(2026, 8, 29)))
    m = ct.plan_converge(env, ct.ConvergeFlags())
    held = collision_holds(m)
    assert held
    for h in held:
        assert h["measured"]["reason"] == \
            "several transcripts carry this id"


def test_oversized_transcript_degrades(mkenv, tmp_path, write_transcript,
                                       write_row, monkeypatch):
    """Spec test 20: over TRANSCRIPT_COMPARE_MAX_BYTES is 'unreadable or too
    large', never a wrong number - and the plan completes."""
    env = mkenv(tmp_path)
    measured_pair(env, write_transcript, write_row)
    monkeypatch.setattr(ct, "TRANSCRIPT_COMPARE_MAX_BYTES", 10)
    m = ct.plan_converge(env, ct.ConvergeFlags())
    assert_unmeasured(m, "unreadable or too large")


def test_below_sample_floor_degrades(mkenv, tmp_path, write_transcript,
                                     write_row):
    """Spec test 21: below OVERLAP_MIN_SAMPLE a percentage would read as
    precise while meaning nothing - but the counts themselves are honest, so
    the numeric trio survives."""
    env = mkenv(tmp_path)
    measured_pair(env, write_transcript, write_row)
    shared = turn_labels("s", 4)
    write_transcript(env, "C--p", S1, prose_transcript(shared))
    write_transcript(env, "C--p", S2, prose_transcript(shared))
    held = assert_unmeasured(ct.plan_converge(env, ct.ConvergeFlags()),
                             "too few prose turns")
    for h in held:
        assert h["measured"]["shared"] == 4
        assert h["measured"]["a_total"] == 4
        assert h["measured"]["b_total"] == 4


def test_budget_exhaustion_degrades_later_pairs(
        mkenv, tmp_path, write_transcript, write_row, monkeypatch):
    """Spec test 22: the budget covers the first blocker exactly; the first
    pair measures and the second reports exhaustion instead of reading."""
    env = mkenv(tmp_path)
    p3, _p4 = two_blocked_pairs(env, write_transcript, write_row)
    monkeypatch.setattr(ct, "MEASURE_MAX_TOTAL_BYTES", os.path.getsize(p3))
    per_pair = pair_holds(ct.plan_converge(env, ct.ConvergeFlags()))
    assert all(mm["classification"] == "distinct"
               for mm in per_pair[frozenset((S1, S3))])
    assert all(mm["reason"] == "measurement budget exhausted"
               for mm in per_pair[frozenset((S2, S4))])


def test_oversized_transcript_charges_no_budget(
        mkenv, tmp_path, write_transcript, write_row, monkeypatch):
    """Spec test 22b: an over-cap blocker degrades its own pair as oversized
    while the later, smaller pair still measures - the budget was not
    consumed by bytes never read."""
    env = mkenv(tmp_path)
    p3, p4 = two_blocked_pairs(env, write_transcript, write_row,
                               s3_turns=2000)
    assert os.path.getsize(p3) > 50_000 > os.path.getsize(p4)
    monkeypatch.setattr(ct, "TRANSCRIPT_COMPARE_MAX_BYTES", 50_000)
    # Room for the SMALL blocker only: had the oversized one been charged,
    # the second pair would (wrongly) exhaust.
    monkeypatch.setattr(ct, "MEASURE_MAX_TOTAL_BYTES",
                        os.path.getsize(p4) + 100)
    per_pair = pair_holds(ct.plan_converge(env, ct.ConvergeFlags()))
    assert all(mm["reason"] == "unreadable or too large"
               for mm in per_pair[frozenset((S1, S3))])
    assert all(mm["classification"] == "distinct"
               for mm in per_pair[frozenset((S2, S4))])


def test_exception_in_measurement_degrades(mkenv, tmp_path, write_transcript,
                                           write_row, monkeypatch):
    """Spec test 23: an exception anywhere in the pipeline degrades that
    hold and the plan completes. Patched at _measure_load - the measurement
    path's own read - rather than _message_fingerprints wholesale, which the
    facts pass legitimately calls for every planned row and whose failure
    there is _transcript_facts' own held_transcript_unusable story."""
    env = mkenv(tmp_path)
    two_blocked_pairs(env, write_transcript, write_row)

    def boom(path):
        raise ValueError("synthetic")
    monkeypatch.setattr(ct, "_measure_load", boom)
    m = ct.plan_converge(env, ct.ConvergeFlags())
    held = collision_holds(m)
    assert len(held) == 2
    for h in held:
        assert h["measured"]["reason"] == "measurement failed (ValueError)"
        assert "<new title>" in h["retitle"]
    assert m["complete"] == {"now": 2, "of": 4, "after": 2, "held": 2,
                             "scoped": False}


def test_measurement_cache_bounds_added_reads(
        mkenv, tmp_path, write_transcript, write_row, monkeypatch):
    """Spec test 24: N holds over one pair cost at most two added loads -
    here exactly ONE, because the held side's fingerprints were computed by
    the plan's own _transcript_facts pass and reused, and the blocker is
    read once then cache-hit."""
    env = mkenv(tmp_path)
    calls = counting_measure(monkeypatch)
    p1 = write_transcript(env, "C--p", S1,
                          prose_transcript(turn_labels("a", 10)))
    p3 = write_transcript(env, "C--p", S3,
                          prose_transcript(turn_labels("c", 40)))
    write_row(env, 0, A1, O1, "local_s1",
              row_data(S1, T_COLL, createdAt=ms_local(2026, 8, 24),
                       lastActivityAt=ms_local(2026, 8, 28)))
    for acct, org, name in ((A1, O1, "local_s3a"), (A2, O2, "local_s3b"),
                            (A3, O3, "local_s3c")):
        write_row(env, 0, acct, org, name,
                  row_data(S3, T_COLL, createdAt=ms_local(2026, 8, 24),
                           lastActivityAt=ms_local(2026, 8, 29)))
    m = ct.plan_converge(env, ct.ConvergeFlags())
    held = collision_holds(m)
    assert len(held) == 2
    assert all(h["measured"]["classification"] == "distinct" for h in held)
    assert calls["loads"] == [p3]
    assert p1 not in calls["loads"]


def test_holds_free_plan_adds_no_measurement_reads(
        mkenv, tmp_path, write_transcript, write_row, monkeypatch):
    """Spec test 25: instrumented at the measurement cache layer - not raw
    _message_fingerprints, which the plan legitimately calls for planned
    rows - a plan with no collisions performs zero measurement loads."""
    env = mkenv(tmp_path)
    calls = counting_measure(monkeypatch)
    mixed_holdings(env, write_transcript, write_row)
    m = ct.plan_converge(env, ct.ConvergeFlags())
    assert m["rows"] and not m["holds"]
    assert calls["loads"] == []
    assert calls["fingerprints"] == []


def test_refused_facts_leave_no_cache_entry(mkenv, tmp_path,
                                            write_transcript, monkeypatch):
    """A transcript appended-to during the facts read is refused as
    describing 'no single version of the file' - and the fingerprints from
    that torn read must not survive in the measurement cache, where a later
    collision pair would classify from them confidently."""
    env = mkenv(tmp_path)
    path = write_transcript(env, "C--p", S1,
                            prose_transcript(turn_labels("a", 10)))
    real = ct._message_fingerprints

    def read_that_races(p):
        fps = real(p)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n")
        return fps
    monkeypatch.setattr(ct, "_message_fingerprints", read_that_races)
    cache = {}
    with pytest.raises(ct.Refusal, match="being written"):
        ct._transcript_facts(env, S1, fps_cache=cache)
    assert S1 not in cache


def test_shell_unsafe_and_taken_nulls_the_suggestion(
        mkenv, tmp_path, write_transcript, write_row):
    """A shell-unsafe title survives its degrade as prose ONLY when the
    other checks pass - a name that is also taken is not a suggestion, and
    handing it to the GUI would apply a title this feature itself rejects."""
    env = mkenv(tmp_path)
    unsafe = "ACME $ review"
    measured_pair(env, write_transcript, write_row, title=unsafe)
    write_row(env, 0, A1, O1, "local_taken",
              row_data(PAD, unsafe + " - earlier leg (Aug 24-28)"))
    m = ct.plan_converge(env, ct.ConvergeFlags())
    held = collision_holds(m)
    assert len(held) == 2
    for h in held:
        mm = h["measured"]
        assert mm["degrade_reason"] == "suggested name already taken"
        assert mm["suggested_title"] is None


def test_suggestion_taken_by_a_planned_title_degrades(
        mkenv, tmp_path, write_transcript, write_row):
    """The name-must-be-free check consults the plan's own chosen titles
    too: a conversation whose transcript customTitle equals the would-be
    suggestion is about to place rows under that very name."""
    env = mkenv(tmp_path)
    measured_pair(env, write_transcript, write_row)
    entries = prose_transcript(turn_labels("z", 10))
    entries.append({"customTitle": SUGGESTED})
    write_transcript(env, "C--p", S3, entries)
    write_row(env, 0, A1, O1, "local_s3",
              row_data(S3, "", createdAt=ms_local(2026, 8, 24),
                       lastActivityAt=ms_local(2026, 8, 27)))
    m = ct.plan_converge(env, ct.ConvergeFlags())
    held = [h for h in collision_holds(m)
            if h["measured"]["classification"] == "supersession"]
    assert held
    for h in held:
        assert h["measured"]["degrade_reason"] == \
            "suggested name already taken"
        assert h["measured"]["suggested_title"] is None
    assert any(r["session"] == S3 and r["title"] == SUGGESTED
               for r in m["rows"])


def test_untitled_row_does_not_divide_the_leg(
        mkenv, tmp_path, write_transcript, write_row):
    """An untitled row on the superseded leg is not an intentional
    difference a global rename would flatten - rows with no usable title do
    not compete (the module's own _converge_title rule), so the remedy still
    generates."""
    env = mkenv(tmp_path)
    measured_pair(env, write_transcript, write_row)
    write_row(env, 0, A3, O3, "local_s1b",
              row_data(S1, "", createdAt=ms_local(2026, 8, 24),
                       lastActivityAt=ms_local(2026, 8, 27)))
    m = ct.plan_converge(env, ct.ConvergeFlags())
    held = [h for h in collision_holds(m)
            if h["measured"]["classification"] == "supersession"]
    assert held
    for h in held:
        assert h["measured"]["degrade_reason"] is None
        assert h["measured"]["suggested_title"] == SUGGESTED
        assert h["measured"]["command_runnable"] is True


def test_exception_after_classification_keeps_todays_target(
        mkenv, tmp_path, write_transcript, write_row, monkeypatch):
    """An exception in title generation degrades the WHOLE pipeline to
    today's remedy - including the remedy's target, which must not stay
    redirected on the authority of a measurement the hold itself declares
    failed."""
    env = mkenv(tmp_path)
    measured_pair(env, write_transcript, write_row)

    def boom(base, rows):
        raise RuntimeError("synthetic")
    monkeypatch.setattr(ct, "_superseded_leg_title", boom)
    m = ct.plan_converge(env, ct.ConvergeFlags())
    held = {h["session"]: h for h in collision_holds(m)}
    assert set(held) == {S1, S2}
    for h in held.values():
        assert h["measured"]["reason"] == "measurement failed (RuntimeError)"
    # Shape (a)'s placeholder targets the BLOCKING row's conversation.
    assert "--only {0}".format(S2[:8]) in held[S1]["retitle"]
    assert "--only {0}".format(S1[:8]) in held[S2]["retitle"]


def test_failed_side_stops_the_pair_before_the_other_read(
        mkenv, tmp_path, write_transcript, write_row, monkeypatch):
    """When one side already failed, the pair can no longer classify -
    reading the other side anyway would charge the plan-wide budget for
    nothing and could starve later, measurable pairs."""
    env = mkenv(tmp_path)
    calls = counting_measure(monkeypatch)
    write_transcript(env, "C--p", S1,
                     prose_transcript(turn_labels("a", 300)))
    write_transcript(env, "C--p", S3,
                     prose_transcript(turn_labels("c", 10)))
    write_row(env, 0, A1, O1, "local_s1",
              row_data(S1, T_COLL, createdAt=ms_local(2026, 8, 24),
                       lastActivityAt=ms_local(2026, 8, 28)))
    for acct, org, name in ((A1, O1, "local_s3a"), (A2, O2, "local_s3b")):
        write_row(env, 0, acct, org, name,
                  row_data(S3, T_COLL, createdAt=ms_local(2026, 8, 24),
                           lastActivityAt=ms_local(2026, 8, 29)))
    # The held side's own transcript is over the cap (prefilled as failed);
    # the small blocker must then not be read at all.
    monkeypatch.setattr(ct, "TRANSCRIPT_COMPARE_MAX_BYTES", 5000)
    m = ct.plan_converge(env, ct.ConvergeFlags())
    assert_unmeasured(m, "unreadable or too large", count=1)
    assert calls["loads"] == []


def test_apply_time_hold_carries_no_measured_key(
        mkenv, tmp_path, write_transcript, write_row):
    """Spec test 11d: the apply-time re-check hold keeps its 0.13.0 shape -
    no `measured` key. Re-measuring under the lock would add I/O to the
    write path for a message."""
    env = _monotonic_now(mkenv(tmp_path))
    mixed_holdings(env, write_transcript, write_row)
    m = ct.plan_converge(env, ct.ConvergeFlags())
    write_row(env, 0, A3, O3, "local_late", row_data(S3.replace("a", "b", 1),
                                                     "ACME-REVIEW"))
    assert ct.run_converge(env, m) == "completed"
    held = [r for r in m["rows"]
            if r["session"] == S1 and r["account"] == A3]
    assert held[0]["skipped"] == "held_title_collision"
    assert "measured" not in held[0]
    assert "measured_line" not in held[0]
    pub = ct._public_converge_manifest(env, m)
    skipped = [r for r in pub["rows"] if r.get("skipped")]
    assert skipped and all("measured" not in r for r in skipped)


MEASURED_KEYS = {"classification", "reason", "superseded", "current",
                 "shared", "a", "a_total", "b", "b_total",
                 "suggested_title", "degrade_reason", "command_runnable"}


def json_holds(env, capsys):
    rc = ct.cmd_converge(env, cv_ns(json=True))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    holds = [h for h in payload["holds"]
             if h["reason"] == "held_title_collision"]
    assert holds
    for h in holds:
        assert set(h["measured"]) == MEASURED_KEYS
    return holds


def test_json_measured_schema(mkenv, tmp_path, write_transcript, write_row,
                              capsys):
    """Spec test 26: the full `measured` object per class - a runnable
    supersession, a DEGRADED supersession, a distinct pair, a one-sided
    unmeasured pair - nullability per §4, parseable."""
    env = mkenv(tmp_path / "runnable")
    measured_pair(env, write_transcript, write_row)
    for h in json_holds(env, capsys):
        mm = h["measured"]
        assert mm["classification"] == "supersession"
        assert mm["reason"] is None
        assert mm["superseded"] == S1 and mm["current"] == S2
        assert isinstance(mm["shared"], int)
        assert isinstance(mm["a_total"], int) and isinstance(
            mm["b_total"], int)
        assert mm["suggested_title"] == SUGGESTED
        assert mm["degrade_reason"] is None
        assert mm["command_runnable"] is True
        assert h["retitle"].endswith('--title "{0}" --apply'.format(
            SUGGESTED))

    env = mkenv(tmp_path / "degraded")
    measured_pair(env, write_transcript, write_row, title="ACME $ review")
    for h in json_holds(env, capsys):
        mm = h["measured"]
        assert mm["classification"] == "supersession"
        assert mm["command_runnable"] is False
        assert mm["degrade_reason"] == "title not shell-safe"
        assert mm["suggested_title"] == "ACME $ review - earlier leg " \
                                        "(Aug 24-28)"
        assert "<new title>" in h["retitle"]

    env = mkenv(tmp_path / "distinct")
    write_transcript(env, "C--p", S1,
                     prose_transcript(turn_labels("a", 10)))
    write_transcript(env, "C--p", S2,
                     prose_transcript(turn_labels("b", 10)))
    write_row(env, 0, A1, O1, "local_s1",
              row_data(S1, T_COLL, createdAt=ms_local(2026, 8, 24),
                       lastActivityAt=ms_local(2026, 8, 28)))
    write_row(env, 0, A2, O2, "local_s2",
              row_data(S2, T_COLL, createdAt=ms_local(2026, 8, 24),
                       lastActivityAt=ms_local(2026, 8, 29)))
    for h in json_holds(env, capsys):
        mm = h["measured"]
        assert mm["classification"] == "distinct"
        assert mm["reason"] is None
        assert mm["superseded"] is None and mm["current"] is None
        assert (mm["shared"], mm["a_total"], mm["b_total"]) == (0, 10, 10)
        assert mm["suggested_title"] is None
        assert mm["command_runnable"] is False

    env = mkenv(tmp_path / "onesided")
    write_transcript(env, "C--p", S1,
                     prose_transcript(turn_labels("a", 10)))
    write_row(env, 0, A1, O1, "local_s1",
              row_data(S1, T_COLL, createdAt=ms_local(2026, 8, 24),
                       lastActivityAt=ms_local(2026, 8, 28)))
    write_row(env, 0, A2, O2, "local_dead", row_data(S3, T_COLL))
    for h in json_holds(env, capsys):
        mm = h["measured"]
        assert mm["classification"] == "unmeasured"
        assert mm["reason"] == "no transcript"
        assert {mm["a"], mm["b"]} == {S1, S3}
        assert mm["shared"] is None
        assert mm["a_total"] is None and mm["b_total"] is None
        assert mm["command_runnable"] is False


def test_title_validation_is_retitles_own(mkenv, tmp_path, write_transcript,
                                          write_row, monkeypatch):
    """Spec test 15: generation routes through _valid_new_title rather than
    re-deriving its rules - what retitle would reject is never printed."""
    env = mkenv(tmp_path)
    measured_pair(env, write_transcript, write_row)

    def refuse(raw):
        raise ct.Refusal("not on my watch")
    monkeypatch.setattr(ct, "_valid_new_title", refuse)
    m = ct.plan_converge(env, ct.ConvergeFlags())
    held = collision_holds(m)
    assert len(held) == 2
    for h in held:
        mm = h["measured"]
        assert mm["classification"] == "supersession"
        assert mm["degrade_reason"] == "title rejected by retitle's validation"
        assert mm["suggested_title"] is None
        assert mm["command_runnable"] is False


# ------------------------------------- carry the link (planning, lineage pass)
# docs/specs/2026-11-17-carry-lineage-design.md, "converge": the plan carries
# a side session's link to its parent's row in each destination. The child
# sorts BEFORE its parent, so a plan that kept today's sid order would write
# the child first.

LP = "9aaa0000-1111-4000-8000-00000000000a"   # the parent
LC = "0ccc0000-2222-4000-8000-00000000000c"   # its side session
LD = "0ddd0000-3333-4000-8000-00000000000d"   # a second child, uncleared link
LX = "0eee0000-4444-4000-8000-00000000000e"   # dead; its rows link in a loop
LY = "0fff0000-5555-4000-8000-00000000000f"   # dead; LX's partner
TASK = "task_7f"
ORG = {A1: O1, A2: O2, A3: O3}
NUM = {A1: "1", A2: "2", A3: "3"}
LINEAGE_KEYS = {"parent_session", "parent_row", "parent_planned",
                "root_session", "root_row", "root_planned", "carried_keys",
                "dropped_keys", "notes"}


def side_link(row_id, **extra):
    d = {"sessionId": row_id, "taskId": TASK, "title": "Northwind",
         "sideSession": True}
    d.update(extra)
    return d


def lrow(sid, stem, title, **extra):
    """row_data with sessionId set to the row's stem (row_data defaults it to
    "local-x", which the lineage pass rightly refuses to resolve)."""
    return row_data(sid, title, sessionId=stem, **extra)


def post_of(r):
    return json.loads(ct.unb64(r["post_b64"]).decode("utf-8"))


def linked_holdings(env, write_transcript, write_row, parent_in=(A2, A3),
                    parent_title="Northwind kickoff", chain=None):
    """P (LP) and C (LC) are held by A1, and C's A1 row is a side session of
    P's A1 row; P is also held by each account in PARENT_IN, and C by none of
    them. An account without P gets a dead padding row, so it stays a
    destination. Every row's sessionId is its stem."""
    for sid in (LP, LC):
        write_transcript(env, "C--p", sid, t_entries())
    write_row(env, 0, A1, O1, "local_p1", lrow(LP, "local_p1", parent_title))
    extra = {"spawnedFrom": side_link("local_p1")}
    if chain is not None:
        extra["startChain"] = chain
    write_row(env, 0, A1, O1, "local_c1",
              lrow(LC, "local_c1", "ACME-REVIEW side task", **extra))
    for acct in (A2, A3):
        if acct in parent_in:
            stem = "local_p" + NUM[acct]
            write_row(env, 0, acct, ORG[acct], stem,
                      lrow(LP, stem, parent_title))
        else:
            write_row(env, 0, acct, ORG[acct], "local_pad",
                      lrow(PAD, "local_pad", "Padding"))


def rows_by(m, sid):
    return {r["account"]: r for r in m["rows"] if r["session"] == sid}


def test_plan_carries_a_translated_link_into_each_destination(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    linked_holdings(env, write_transcript, write_row)
    m = ct.plan_converge(env, ct.ConvergeFlags())
    assert m["lineage_format"] == 1
    assert m["lineage_cycles"] == 0
    mine = rows_by(m, LC)
    assert sorted(mine) == [A2, A3]
    assert not rows_by(m, LP)                       # P is complete already
    for acct in (A2, A3):
        r = mine[acct]
        target = "local_p" + NUM[acct]
        post = post_of(r)
        assert post["spawnedFrom"] == {"sessionId": target, "taskId": TASK}
        assert "startChain" not in post
        # The link is appended to the synthesized row; nothing else changes.
        assert list(post)[-1] == "spawnedFrom"
        assert post["sessionId"] == r["name"][:-len(".json")]
        assert post["cliSessionId"] == LC
        assert r["lineage"] == {
            "parent_session": LP, "parent_row": target,
            "parent_planned": False, "root_session": "", "root_row": "",
            "root_planned": False, "carried_keys": ["sessionId", "taskId"],
            "dropped_keys": ["sideSession", "title"], "notes": []}
    # Lineage notes live in the rows only, never among the title notes.
    assert m["notes"] == []


def test_plan_carries_the_link_to_a_parent_row_created_in_the_same_plan(
        mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    linked_holdings(env, write_transcript, write_row, parent_in=(A2,))
    m = ct.plan_converge(env, ct.ConvergeFlags())
    parent = rows_by(m, LP)[A3]
    child = rows_by(m, LC)[A3]
    stem = parent["name"][:-len(".json")]
    assert post_of(child)["spawnedFrom"] == {"sessionId": stem,
                                             "taskId": TASK}
    lin = child["lineage"]
    assert lin["parent_row"] == stem and lin["parent_planned"] is True
    assert lin["parent_session"] == LP
    # The parent's own new row carries no link, and an empty record.
    assert "spawnedFrom" not in post_of(parent)
    assert parent["lineage"]["parent_row"] == ""
    assert parent["lineage"]["notes"] == []
    # And A2's child row still links to the parent row A2 already had.
    assert post_of(rows_by(m, LC)[A2])["spawnedFrom"]["sessionId"] == \
        "local_p2"


def test_plan_rows_are_in_write_order_parents_first(
        mkenv, tmp_path, write_transcript, write_row):
    """Today's order is sid order, account order: (LC, A2), (LC, A3),
    (LP, A3). The child in A3 names a planned parent, so it moves behind it;
    the row with no planned dependency keeps its place."""
    env = mkenv(tmp_path)
    linked_holdings(env, write_transcript, write_row, parent_in=(A2,))
    m = ct.plan_converge(env, ct.ConvergeFlags())
    assert [(r["session"], r["account"]) for r in m["rows"]] == \
        [(LC, A2), (LP, A3), (LC, A3)]


def test_plan_leaves_the_link_out_when_the_parents_pair_is_held(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    """P's pair into A2 is held (its title already names another
    conversation there), so C's new A2 row cannot link to it: the note is
    parent_pair_held, with the hold's reason, not parent_not_in_account."""
    env = mkenv(tmp_path)
    linked_holdings(env, write_transcript, write_row, parent_in=())
    write_row(env, 0, A2, O2, "local_x2",
              lrow(LX, "local_x2", "Northwind kickoff"))
    m = ct.plan_converge(env, ct.ConvergeFlags())
    assert [(h["session"], h["account"], h["reason"]) for h in m["holds"]] \
        == [(LP, A2, "held_title_collision")]
    child = rows_by(m, LC)[A2]
    assert "spawnedFrom" not in post_of(child)
    lin = child["lineage"]
    assert lin["parent_row"] == "" and lin["parent_session"] == ""
    assert [n["code"] for n in lin["notes"]] == ["parent_pair_held"]
    assert "held_title_collision" in lin["notes"][0]["detail"]
    # The A3 copy links to the parent row this plan creates there.
    assert rows_by(m, LC)[A3]["lineage"]["parent_planned"] is True
    rc = ct.cmd_converge(env, cv_ns())
    out = capsys.readouterr().out
    assert rc == 0
    tagged = [ln for ln in out.splitlines()
              if "[link: the parent's own row is held in this plan]" in ln]
    assert len(tagged) == 1 and LC[:8] in tagged[0]
    assert ("links   : 1 to carry, 1 left out (the parent's own row is held "
            "in this plan 1)") in out


def test_only_on_a_child_whose_parent_is_out_of_scope(
        mkenv, tmp_path, write_transcript, write_row):
    """--only C leaves P's own missing pair (A3) out of the plan. C's link
    still resolves against the store: A2's existing parent row is carried,
    and A3, where no parent row exists or is planned, gets no link."""
    env = mkenv(tmp_path)
    linked_holdings(env, write_transcript, write_row, parent_in=(A2,))
    m = ct.plan_converge(env, ct.ConvergeFlags(only=LC[:8]))
    assert {r["session"] for r in m["rows"]} == {LC}
    mine = rows_by(m, LC)
    assert post_of(mine[A2])["spawnedFrom"] == {"sessionId": "local_p2",
                                                "taskId": TASK}
    assert "spawnedFrom" not in post_of(mine[A3])
    assert [n["code"] for n in mine[A3]["lineage"]["notes"]] == \
        ["parent_not_in_account"]


def test_dry_run_prints_the_links_and_chains_lines_and_row_reasons(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    """C carries a cleared link and a chain into A2 and A3. D's link is an
    uncleared variant (pendingPop), so D's rows carry neither its link nor
    its chain, and each says why on its own line."""
    env = mkenv(tmp_path)
    chain = {"firstSessionId": "local_p1", "depth": 1}
    linked_holdings(env, write_transcript, write_row, chain=chain)
    write_transcript(env, "C--p", LD, t_entries())
    write_row(env, 0, A1, O1, "local_d1",
              lrow(LD, "local_d1", "Northwind follow-up",
                   spawnedFrom={"sessionId": "local_p1", "taskId": TASK,
                                "pendingPop": True},
                   startChain=chain))
    m = ct.plan_converge(env, ct.ConvergeFlags())
    for acct in (A2, A3):
        assert post_of(rows_by(m, LC)[acct])["startChain"] == {
            "firstSessionId": "local_p" + NUM[acct], "depth": 1}
        assert "startChain" not in post_of(rows_by(m, LD)[acct])
    rc = ct.cmd_converge(env, cv_ns())
    out = capsys.readouterr().out
    assert rc == 0
    lines = out.splitlines()
    links = ("links   : 2 to carry, 2 left out (not a cleared link "
             "variant 2)")
    chains = "chains  : 2 to carry, 2 left out"
    assert links in lines and chains in lines
    complete = [i for i, ln in enumerate(lines)
                if ln.startswith("complete : ")]
    assert complete and complete[0] < lines.index(links) < lines.index(chains)
    assert not any(ln.startswith("cycles") for ln in lines)
    tagged = [ln for ln in lines if ln.endswith(
        "   [link: not a cleared link variant]")]
    assert len(tagged) == 2 and all(LD[:8] in ln for ln in tagged)
    assert not any("[link:" in ln for ln in lines if LC[:8] in ln)
    # The reasons: most frequent first, ties in the table's order.
    summary = {"links": {"carried": 0, "left_out": {
        "parent_not_in_account": 1, "lineage_self": 1,
        "lineage_variant": 3}}}
    assert ct._lineage_links_line(summary) == (
        "links   : 5 left out (not a cleared link variant 3, the link names "
        "the conversation itself 1, no row for the parent in that "
        "account 1)")


def test_dry_run_and_json_report_an_existing_cycle(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    """A2 and A3 each already hold two rows that link to each other. The plan
    leaves them alone and reports them once per account."""
    env = mkenv(tmp_path)
    linked_holdings(env, write_transcript, write_row)
    for acct in (A2, A3):
        x, y = "local_x" + NUM[acct], "local_y" + NUM[acct]
        write_row(env, 0, acct, ORG[acct], x,
                  lrow(LX, x, "Northwind loop one", spawnedFrom=side_link(y)))
        write_row(env, 0, acct, ORG[acct], y,
                  lrow(LY, y, "Northwind loop two", spawnedFrom=side_link(x)))
    m = ct.plan_converge(env, ct.ConvergeFlags())
    assert m["lineage_cycles"] == 2
    rc = ct.cmd_converge(env, cv_ns())
    out = capsys.readouterr().out
    assert rc == 0
    assert "cycles  : 2 already present, left as they are" in out.splitlines()
    rc = ct.cmd_converge(env, cv_ns(json=True))
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["lineage_cycles"] == 2
    # existing_cycle is a report only, never a row note.
    for r in payload["rows"]:
        assert all(n["code"] != "existing_cycle"
                   for n in r["lineage"]["notes"])


def test_json_keeps_lineage_and_strips_row_images(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    env = mkenv(tmp_path)
    linked_holdings(env, write_transcript, write_row)
    rc = ct.cmd_converge(env, cv_ns(json=True))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["lineage_format"] == 1
    assert payload["lineage_cycles"] == 0
    assert len(payload["rows"]) == 2
    for r in payload["rows"]:
        assert "post_b64" not in r and "pre_b64" not in r
        assert set(r["lineage"]) == LINEAGE_KEYS
        assert r["lineage"]["parent_row"] in ("local_p2", "local_p3")
    # No source link or chain is ever emitted.
    text = json.dumps(payload)
    assert "spawnedFrom" not in text and "startChain" not in text


def test_anonymized_plan_leaks_no_title_label_or_path(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    """The parent is titled literally 'lineage': the structural key must
    survive the scrub while the title is replaced. Labels carry addresses."""
    env = mkenv(tmp_path)
    linked_holdings(env, write_transcript, write_row, parent_title="lineage",
                    chain={"firstSessionId": "local_p1", "depth": 1})
    identity_files(env, A1, A1, email="alice@example.com")
    agent_mode_email(env, A2, "bob@example.com")
    ct._ANONYMIZE = True
    ct._ANON_CACHE.clear()
    try:
        rc = ct.cmd_converge(env, cv_ns(anonymize=True))
        out = capsys.readouterr().out
        assert rc == 0
        assert "ACME-REVIEW" not in out and "@example.com" not in out
        assert "links   : 2 to carry" in out
        assert "chains  : 2 to carry" in out

        rc = ct.cmd_converge(env, cv_ns(anonymize=True, json=True))
        text = capsys.readouterr().out
        assert rc == 0
        assert "ACME-REVIEW" not in text and "@example.com" not in text
        assert env.home not in text
        payload = json.loads(text)
        assert payload["lineage_format"] == 1
        assert len(payload["rows"]) == 2
        for r in payload["rows"]:
            assert r["title"].startswith("<session-")
            lin = r["lineage"]
            assert set(lin) == LINEAGE_KEYS
            assert lin["parent_row"] in ("local_p2", "local_p3")
            assert lin["root_row"] == lin["parent_row"]
            # Unescaped, so the ellipsis after a shortened id is not "\\".
            flat = json.dumps(lin, ensure_ascii=False)
            assert "\\" not in flat and "/" not in flat and "@" not in flat

        # Every note's detail, in lineage and lineage_planned, goes through
        # the title scrub (details are title-free by construction; scrubbed
        # anyway, as hold details are).
        m = ct.plan_converge(env, ct.ConvergeFlags())
        r = m["rows"][0]
        r["lineage"]["notes"] = [{"code": "parent_not_in_account",
                                  "detail": "ACME-REVIEW side task"}]
        r["lineage_planned"] = {
            **r["lineage"], "notes": [{"code": "lineage_variant",
                                       "detail": "ACME-REVIEW side task"}]}
        pub = ct._public_converge_manifest(env, m)
        for key in ("lineage", "lineage_planned"):
            detail = pub["rows"][0][key]["notes"][0]["detail"]
            assert "ACME-REVIEW" not in detail and "<session-" in detail
    finally:
        ct._ANONYMIZE = False
        ct._ANON_CACHE.clear()


def test_a_store_without_links_plans_the_same_post_b64_as_before(
        mkenv, tmp_path, write_transcript, write_row, monkeypatch):
    """With no link anywhere, the lineage pass changes no byte and no row's
    place: the plan equals one made with the pass and the write order turned
    off, row for row, with the same minted ids."""
    env = mkenv(tmp_path)
    mixed_holdings(env, write_transcript, write_row)
    counter = {"n": 0}

    def fake_uuid4():
        counter["n"] += 1
        return "00000000-0000-4000-8000-{0:012d}".format(counter["n"])
    monkeypatch.setattr(ct.uuid, "uuid4", fake_uuid4)
    m = ct.plan_converge(env, ct.ConvergeFlags())
    counter["n"] = 0
    monkeypatch.setattr(ct, "_converge_lineage", lambda *a, **k: 0)
    monkeypatch.setattr(ct, "_lineage_write_order",
                        lambda rows, key: list(rows))
    before = ct.plan_converge(env, ct.ConvergeFlags())

    def shape(rows):
        return [(r["name"], r["session"], r["account"], r["post_b64"])
                for r in rows]
    assert len(m["rows"]) == 3
    assert shape(m["rows"]) == shape(before["rows"])
    for r in m["rows"]:
        assert r["lineage"] == {
            "parent_session": "", "parent_row": "", "parent_planned": False,
            "root_session": "", "root_row": "", "root_planned": False,
            "carried_keys": [], "dropped_keys": [], "notes": []}


def deep_store():
    """The Review Focus fixture, in memory: a 2,000-row store in two accounts.

    A1 holds a 200-deep chain of side sessions (each the child of the one
    before it, each also carrying the chain's root at its depth) and one root
    with 500 children; A2 holds none of them. A2 holds two conversations of
    its own whose rows link to each other - an existing cycle - and A1 lacks
    both, so in A1 the pass proposes that same cycle and must drop one edge.
    The rest is filler held by both. Every child's sid sorts BEFORE its
    parent's, so today's sid order would write each child first."""
    store = {A1: os.path.join("u", A1, O1), A2: os.path.join("u", A2, O2)}
    records, rows = [], []

    def sid_of(n):
        return "{0:08x}-0000-4000-8000-000000000000".format(n)

    def existing(acct, sid, **extra):
        stem = "local_a{0}_{1}".format(NUM[acct], sid[:8])
        data = {"sessionId": stem, "cliSessionId": sid, "title": "Northwind",
                "lastActivityAt": 9}
        data.update(extra)
        records.append(ct._RetitleRow(
            acct, ORG[acct], store[acct], acct[:8], stem + ".json",
            os.path.join(store[acct], stem + ".json"), b"", data))
        return stem

    def planned(acct, sid):
        stem = "local_n{0}_{1}".format(NUM[acct], sid[:8])
        data = {"sessionId": stem, "cliSessionId": sid, "title": "Northwind",
                "lastActivityAt": 9}
        rows.append({"name": stem + ".json", "store_path": store[acct],
                     "dest_path": os.path.join(store[acct], stem + ".json"),
                     "account": acct, "session": sid,
                     "post_b64": ct.b64(json.dumps(
                         data, separators=(",", ":")).encode())})
        return stem

    deep = [sid_of(0xd0000000 + 199 - i) for i in range(200)]
    root = sid_of(0xf0000000)
    kids = [sid_of(0xe0000000 + j) for j in range(500)]
    lx, ly = sid_of(0xc0000001), sid_of(0xc0000002)
    filler = [sid_of(0x10000000 + k) for k in range(648)]

    first = existing(A1, deep[0])
    prev = first
    for i in range(1, 200):
        prev = existing(A1, deep[i],
                        spawnedFrom=side_link(prev, taskId="task_d%d" % i),
                        startChain={"firstSessionId": first, "depth": i})
    rstem = existing(A1, root)
    for k in kids:
        existing(A1, k, spawnedFrom=side_link(rstem, taskId="task_k"),
                 startChain={"firstSessionId": rstem, "depth": 1})
    xstem = "local_a2_" + lx[:8]
    ystem = "local_a2_" + ly[:8]
    existing(A2, lx, spawnedFrom=side_link(ystem, taskId="task_x"))
    existing(A2, ly, spawnedFrom=side_link(xstem, taskId="task_y"))
    for f in filler:
        existing(A1, f)
        existing(A2, f)
    dup = "local_dup_" + filler[0][:8]
    records.append(ct._RetitleRow(
        A1, O1, store[A1], A1[:8], dup + ".json",
        os.path.join(store[A1], dup + ".json"), b"",
        {"sessionId": dup, "cliSessionId": filler[0], "title": "Northwind"}))
    # The plan's rows, in today's order: by sid, each into its one missing
    # account.
    for sid in sorted(deep + kids + [root, lx, ly]):
        planned(A1 if sid in (lx, ly) else A2, sid)
    conversations = {}
    for rec in records:
        conversations.setdefault(rec.data["cliSessionId"], []).append(rec)
    tids = set(conversations)
    return records, conversations, rows, tids, deep, root, kids, lx, ly


def test_large_deep_store_plans_correctly_without_recursion():
    import inspect
    import sys
    import time
    records, conversations, rows, tids, deep, root, kids, lx, ly = \
        deep_store()
    assert len(records) == 2000 and len(rows) == 703
    limit = sys.getrecursionlimit()
    # Room for the pass's own call depth, far short of the 200-deep chain: a
    # walk that recursed per link would raise RecursionError here.
    sys.setrecursionlimit(len(inspect.stack(0)) + 100)
    try:
        t0 = time.perf_counter()
        cycles = ct._converge_lineage(records, conversations, rows, {}, tids)
        t1 = time.perf_counter()
        ordered = ct._lineage_write_order(
            rows, key=lambda r: (r["store_path"], r["name"]))
        t2 = time.perf_counter()
    finally:
        sys.setrecursionlimit(limit)
    print("lineage pass {0:.3f}s, write order {1:.3f}s".format(t1 - t0,
                                                              t2 - t1))
    assert cycles == 1                     # A2's own loop, counted once
    by = {r["session"]: r for r in rows}

    def stem(sid, n="2"):
        return "local_n{0}_{1}".format(n, sid[:8])
    assert "spawnedFrom" not in post_of(by[deep[0]])
    assert by[deep[0]]["lineage"]["notes"] == []
    for i in range(1, 200):
        post = post_of(by[deep[i]])
        assert post["spawnedFrom"] == {"sessionId": stem(deep[i - 1]),
                                       "taskId": "task_d%d" % i}
        assert post["startChain"] == {"firstSessionId": stem(deep[0]),
                                      "depth": i}
        lin = by[deep[i]]["lineage"]
        assert lin["parent_planned"] is True and lin["root_planned"] is True
    for k in kids:
        post = post_of(by[k])
        assert post["spawnedFrom"] == {"sessionId": stem(root),
                                       "taskId": "task_k"}
        assert post["startChain"] == {"firstSessionId": stem(root),
                                      "depth": 1}
    # In A1 the pass proposes A2's loop again: the first edge by the child's
    # id stands, the second would close the cycle and is dropped.
    assert post_of(by[lx])["spawnedFrom"] == {"sessionId": stem(ly, "1"),
                                              "taskId": "task_x"}
    assert "spawnedFrom" not in post_of(by[ly])
    assert [n["code"] for n in by[ly]["lineage"]["notes"]] == \
        ["lineage_cycle"]
    # Parents and roots are written before the rows that name them.
    assert len(ordered) == len(rows)
    pos = {(r["store_path"], r["name"]): i for i, r in enumerate(ordered)}
    for i, r in enumerate(ordered):
        lin = r["lineage"]
        for ref, flag in (("parent_row", "parent_planned"),
                          ("root_row", "root_planned")):
            if lin[flag]:
                assert pos[(r["store_path"], lin[ref] + ".json")] < i
    assert [r["session"] for r in ordered
            if r["session"] in deep] == deep


# ------------------------- carry the link (apply: re-resolved once, under lock)
# docs/specs/2026-11-17-carry-lineage-design.md, "converge", "Applying, once":
# after its pair-skip decisions the recheck runs the lineage pass again against
# the store as it now is plus the rows still to be written. A row whose link or
# chain came out differently is rewritten with its record, the difference is
# noted beside the underlying reason, the plan's record is kept as
# lineage_planned, and the rows are re-sorted - all before new_op.

LR = "0bbb0000-6666-4000-8000-00000000000b"   # a chain root, held by A1
SENT_TITLE = "SENTINEL-LINK-TITLE-7Q"
SENT_VALUE = "SENTINEL-LINK-VALUE-8R"
SENT_CHAIN = "SENTINEL-CHAIN-VALUE-9S"


def codes(lin):
    return [n["code"] for n in lin["notes"]]


def rm_transcript(env, sid):
    os.unlink(os.path.join(env.projects_root, "C--p", sid + ".jsonl"))


def on_disk(r):
    with open(r["dest_path"], encoding="utf-8") as fh:
        return json.load(fh)


def holder_row(env, name):
    return os.path.join(env.store_candidates[0], A1, O1, name + ".json")


def test_apply_retargets_when_the_planned_parent_row_appeared_elsewhere(
        mkenv, tmp_path, write_transcript, write_row):
    """The plan creates P's row in A3 and links C's new A3 row to it. A row
    for P then appears in A3 under another name: P's planned row is skipped
    as already_present, and C's link is retargeted to the row that appeared,
    never left naming a row that will not be written."""
    env = _monotonic_now(mkenv(tmp_path))
    linked_holdings(env, write_transcript, write_row, parent_in=(A2,))
    m = ct.plan_converge(env, ct.ConvergeFlags())
    planned_parent = rows_by(m, LP)[A3]
    planned_stem = planned_parent["name"][:-len(".json")]
    assert rows_by(m, LC)[A3]["lineage"]["parent_row"] == planned_stem
    write_row(env, 0, A3, O3, "local_q3",
              lrow(LP, "local_q3", "Northwind kickoff"))
    assert ct.run_converge(env, m) == "completed"
    assert planned_parent["skipped"] == "already_present"
    child = rows_by(m, LC)[A3]
    assert child["written"] is True and "skipped" not in child
    assert on_disk(child)["spawnedFrom"] == {"sessionId": "local_q3",
                                             "taskId": TASK}
    lin = child["lineage"]
    assert lin["parent_row"] == "local_q3" and lin["parent_planned"] is False
    assert lin["parent_session"] == LP
    assert codes(lin) == ["lineage_retargeted"]
    assert child["lineage_planned"]["parent_row"] == planned_stem
    assert child["lineage_planned"]["parent_planned"] is True
    assert codes(child["lineage_planned"]) == []
    # A2's copy linked to A2's own parent row in the plan and still does.
    other = rows_by(m, LC)[A2]
    assert codes(other["lineage"]) == [] and "lineage_planned" not in other
    assert on_disk(other)["spawnedFrom"] == {"sessionId": "local_p2",
                                             "taskId": TASK}


def test_apply_drops_when_the_parents_transcript_left(
        mkenv, tmp_path, write_transcript, write_row):
    """P's transcript leaves the disk between the dry run and the apply.
    Each child row is still written, without the link or the chain, and
    carries the underlying reason beside lineage_dropped."""
    env = _monotonic_now(mkenv(tmp_path))
    linked_holdings(env, write_transcript, write_row,
                    chain={"firstSessionId": "local_p1", "depth": 1})
    m = ct.plan_converge(env, ct.ConvergeFlags())
    assert all("spawnedFrom" in post_of(r) for r in m["rows"])
    rm_transcript(env, LP)
    assert ct.run_converge(env, m) == "completed"
    for acct in (A2, A3):
        child = rows_by(m, LC)[acct]
        assert child["written"] is True
        data = on_disk(child)
        assert "spawnedFrom" not in data and "startChain" not in data
        lin = child["lineage"]
        assert codes(lin) == ["lineage_parent_dead", "lineage_chain_dropped",
                              "lineage_dropped"]
        assert lin["parent_row"] == "" and lin["root_row"] == ""
        planned = child["lineage_planned"]
        assert planned["parent_row"] == planned["root_row"] == \
            "local_p" + NUM[acct]
    s = ct._lineage_summary([r for r in m["rows"] if r["written"]])
    assert s["links"] == {"carried": 0,
                          "left_out": {"lineage_parent_dead": 2}}
    assert s["chains"] == {"carried": 0, "left_out": 2}
    assert s["changes"] == {"changed": 0, "dropped": 2, "gained": 0}


def test_apply_gains_a_link_when_a_parent_row_appeared_since_the_dry_run(
        mkenv, tmp_path, write_transcript, write_row):
    """--only C, and no destination holds P, so the dry run carries no link.
    A row for P then appears in A2: C's A2 row gains its link. A3 still has
    no row for P and keeps the plan's reason, with no change noted."""
    env = _monotonic_now(mkenv(tmp_path))
    linked_holdings(env, write_transcript, write_row, parent_in=())
    m = ct.plan_converge(env, ct.ConvergeFlags(only=LC[:8]))
    assert {r["session"] for r in m["rows"]} == {LC}
    assert all(codes(r["lineage"]) == ["parent_not_in_account"]
               for r in m["rows"])
    write_row(env, 0, A2, O2, "local_p2",
              lrow(LP, "local_p2", "Northwind kickoff"))
    assert ct.run_converge(env, m) == "completed"
    gained = rows_by(m, LC)[A2]
    assert on_disk(gained)["spawnedFrom"] == {"sessionId": "local_p2",
                                              "taskId": TASK}
    assert codes(gained["lineage"]) == ["lineage_gained"]
    assert gained["lineage"]["parent_row"] == "local_p2"
    assert gained["lineage_planned"]["parent_row"] == ""
    assert codes(gained["lineage_planned"]) == ["parent_not_in_account"]
    still = rows_by(m, LC)[A3]
    assert still["written"] is True
    assert "spawnedFrom" not in on_disk(still)
    assert codes(still["lineage"]) == ["parent_not_in_account"]
    assert "lineage_planned" not in still
    s = ct._lineage_summary([r for r in m["rows"] if r["written"]])
    assert s["changes"] == {"changed": 0, "dropped": 0, "gained": 1}


def test_apply_reports_a_parent_skipped_as_held_title_collision_by_that_reason(
        mkenv, tmp_path, write_transcript, write_row):
    """Another conversation takes P's chosen title in A3 after the dry run,
    so the recheck holds P's planned row there. C's A3 row, planned to link
    to it, is reported by that hold's reason code - never by the hold's
    detail, which quotes a title."""
    env = _monotonic_now(mkenv(tmp_path))
    linked_holdings(env, write_transcript, write_row, parent_in=(A2,))
    m = ct.plan_converge(env, ct.ConvergeFlags())
    assert rows_by(m, LC)[A3]["lineage"]["parent_planned"] is True
    write_row(env, 0, A3, O3, "local_x3",
              lrow(LX, "local_x3", "Northwind kickoff"))
    assert ct.run_converge(env, m) == "completed"
    assert rows_by(m, LP)[A3]["skipped"] == "held_title_collision"
    child = rows_by(m, LC)[A3]
    assert child["written"] is True and "skipped" not in child
    assert "spawnedFrom" not in on_disk(child)
    lin = child["lineage"]
    assert codes(lin) == ["parent_pair_held", "lineage_dropped"]
    detail = lin["notes"][0]["detail"]
    assert "held_title_collision" in detail and "Northwind" not in detail
    assert child["lineage_planned"]["parent_planned"] is True


def chained_holdings(env, write_transcript, write_row, root_in=(A2, A3),
                     depth=2):
    """linked_holdings (P in A2 and A3), with C's A1 row also carrying a
    chain rooted at R (LR): a third conversation held by A1 and by each
    account in ROOT_IN, as local_r<n>."""
    linked_holdings(env, write_transcript, write_row,
                    chain={"firstSessionId": "local_r1", "depth": depth})
    write_transcript(env, "C--p", LR, t_entries())
    write_row(env, 0, A1, O1, "local_r1", lrow(LR, "local_r1", "Northwind root"))
    for acct in root_in:
        stem = "local_r" + NUM[acct]
        write_row(env, 0, acct, ORG[acct], stem,
                  lrow(LR, stem, "Northwind root"))


@pytest.mark.parametrize("case", ["gained", "dropped", "moved", "depth"])
def test_apply_chain_gained_dropped_or_moved_alone_is_classified(
        mkenv, tmp_path, write_transcript, write_row, case):
    """The link stays the same and only the chain changes since the dry run:
    a root row that appeared (gained), one that left (lineage_chain_dropped),
    one replaced by another row of the root (moved) and a source depth that
    changed are each classified by the chain alone."""
    env = _monotonic_now(mkenv(tmp_path))
    chained_holdings(env, write_transcript, write_row,
                     root_in=(A3,) if case == "gained" else (A2, A3))
    m = ct.plan_converge(env, ct.ConvergeFlags(only=LC[:8]))
    a2 = os.path.join(env.store_candidates[0], A2, O2)
    want = {"firstSessionId": "local_r2", "depth": 2}
    if case == "gained":
        assert "startChain" not in post_of(rows_by(m, LC)[A2])
        write_row(env, 0, A2, O2, "local_r2",
                  lrow(LR, "local_r2", "Northwind root"))
        code, n_changed = "lineage_gained", 1
    elif case == "dropped":
        os.unlink(os.path.join(a2, "local_r2.json"))
        want, code, n_changed = None, "lineage_chain_dropped", 1
    elif case == "moved":
        os.unlink(os.path.join(a2, "local_r2.json"))
        write_row(env, 0, A2, O2, "local_s2",
                  lrow(LR, "local_s2", "Northwind root"))
        want = {"firstSessionId": "local_s2", "depth": 2}
        code, n_changed = "lineage_retargeted", 1
    else:
        _app_touches(holder_row(env, "local_c1"),
                     startChain={"firstSessionId": "local_r1", "depth": 3})
        want = {"firstSessionId": "local_r2", "depth": 3}
        code, n_changed = "lineage_retargeted", 2
    assert ct.run_converge(env, m) == "completed"
    child = rows_by(m, LC)[A2]
    data = on_disk(child)
    assert data["spawnedFrom"] == {"sessionId": "local_p2", "taskId": TASK}
    assert data.get("startChain") == want
    assert child["lineage"]["parent_row"] == "local_p2"
    assert child["lineage"]["root_row"] == (want or {}).get(
        "firstSessionId", "")
    assert codes(child["lineage"]) == [code]
    assert child["lineage_planned"]["parent_row"] == "local_p2"
    third = rows_by(m, LC)[A3]
    if case == "depth":
        assert on_disk(third)["startChain"] == {"firstSessionId": "local_r3",
                                                "depth": 3}
        assert codes(third["lineage"]) == ["lineage_retargeted"]
    else:
        assert on_disk(third)["startChain"] == {"firstSessionId": "local_r3",
                                                "depth": 2}
        assert codes(third["lineage"]) == [] and \
            "lineage_planned" not in third
    changed = [r for r in m["rows"] if "lineage_planned" in r]
    assert len(changed) == n_changed
    s = ct._lineage_summary([r for r in m["rows"] if r["written"]])
    assert s["links"]["carried"] == 2 and not s["links"]["left_out"]
    # A dropped chain is counted on the chains line, never as a changed link.
    assert s["changes"]["dropped"] == 0
    if case == "dropped":
        assert s["chains"] == {"carried": 1, "left_out": 1}
        assert s["changes"] == {"changed": 0, "dropped": 0, "gained": 0}


def test_apply_task_id_change_on_the_same_parent_is_retargeted(
        mkenv, tmp_path, write_transcript, write_row):
    """The holder's link keeps its parent and takes another taskId after
    the dry run. `lineage` holds no taskId, so only the decoded bytes can
    see the change: it is lineage_retargeted, and the new taskId is
    written."""
    env = _monotonic_now(mkenv(tmp_path))
    linked_holdings(env, write_transcript, write_row)
    m = ct.plan_converge(env, ct.ConvergeFlags())
    _app_touches(holder_row(env, "local_c1"),
                 spawnedFrom=side_link("local_p1", taskId="task_8g"))
    assert ct.run_converge(env, m) == "completed"
    for acct in (A2, A3):
        child = rows_by(m, LC)[acct]
        assert on_disk(child)["spawnedFrom"] == {
            "sessionId": "local_p" + NUM[acct], "taskId": "task_8g"}
        assert codes(child["lineage"]) == ["lineage_retargeted"]
        planned = dict(child["lineage_planned"], notes=[])
        assert dict(child["lineage"], notes=[]) == planned


def assert_lineage_agrees(rows):
    """The constraints' binding rule over one op's rows: `lineage` names the
    parent and root rows exactly as the bytes carry them, and a *_planned
    flag is true exactly when another row of the op has that name in the
    same store. lineage_planned is history, checked for shape alone."""
    names = {(r["store_path"], r["name"]) for r in rows}
    for r in rows:
        data = json.loads(ct.unb64(r["post_b64"]))
        lin = r["lineage"]
        assert set(lin) == LINEAGE_KEYS
        for ref, flag, key, field in (
                ("parent_row", "parent_planned", "spawnedFrom", "sessionId"),
                ("root_row", "root_planned", "startChain",
                 "firstSessionId")):
            carried = data.get(key)
            assert bool(lin[ref]) == (carried is not None), (r["name"], ref)
            if carried is not None:
                assert lin[ref] == carried[field]
            in_op = bool(lin[ref]) and lin[ref] + ".json" != r["name"] and \
                (r["store_path"], lin[ref] + ".json") in names
            assert lin[flag] is in_op, (r["name"], flag)
        if "lineage_planned" in r:
            assert set(r["lineage_planned"]) == LINEAGE_KEYS


def test_lineage_and_post_b64_agree_after_apply_and_planned_is_kept(
        mkenv, tmp_path, write_transcript, write_row):
    """A row for P appears in A3 after the dry run: C's A3 row is retargeted
    in both its link and its chain. In the journal every row's record agrees
    with its bytes, only the changed row keeps the plan's record, and every
    unchanged row keeps the plan's bytes exactly."""
    env = _monotonic_now(mkenv(tmp_path))
    linked_holdings(env, write_transcript, write_row, parent_in=(A2,),
                    chain={"firstSessionId": "local_p1", "depth": 1})
    m = ct.plan_converge(env, ct.ConvergeFlags())
    plan = {r["name"]: (r["post_b64"], json.dumps(r["lineage"]))
            for r in m["rows"]}
    write_row(env, 0, A3, O3, "local_q3",
              lrow(LP, "local_q3", "Northwind kickoff"))
    assert ct.run_converge(env, m) == "completed"
    op = ct.list_ops(env)[-1]
    rows = op.manifest["rows"]
    assert_lineage_agrees(rows)
    changed = [r for r in rows if "lineage_planned" in r]
    assert [(r["session"], r["account"]) for r in changed] == [(LC, A3)]
    retargeted = changed[0]
    assert codes(retargeted["lineage"]) == ["lineage_retargeted"]
    post = post_of(retargeted)
    assert post["spawnedFrom"] == {"sessionId": "local_q3", "taskId": TASK}
    assert post["startChain"] == {"firstSessionId": "local_q3", "depth": 1}
    # The kept record is the plan's, exactly.
    assert json.dumps(retargeted["lineage_planned"]) == \
        plan[retargeted["name"]][1]
    for r in rows:
        if r is not retargeted:
            assert r["post_b64"] == plan[r["name"]][0]
        if r.get("written"):
            with open(r["dest_path"], "rb") as fh:
                assert fh.read() == ct.unb64(r["post_b64"])


def test_journal_holds_the_final_bytes_and_order(
        mkenv, tmp_path, write_transcript, write_row, monkeypatch):
    """At the dry run C's holder row carries no link, so C's A3 row keeps
    its sid place ahead of P's planned A3 row. The link appears before the
    apply: C's A3 row now names P's planned row, so the rows are re-sorted
    before new_op - the journal holds the final bytes in the final order,
    and the parent is written first. A cycle of links that appeared in A2
    meanwhile is counted again, too."""
    env = _monotonic_now(mkenv(tmp_path))
    linked_holdings(env, write_transcript, write_row, parent_in=(A2,))
    write_row(env, 0, A1, O1, "local_c1",
              lrow(LC, "local_c1", "ACME-REVIEW side task"))
    m = ct.plan_converge(env, ct.ConvergeFlags())
    assert [(r["session"], r["account"]) for r in m["rows"]] == \
        [(LC, A2), (LC, A3), (LP, A3)]
    assert not any("spawnedFrom" in post_of(r) for r in m["rows"])
    assert m["lineage_cycles"] == 0
    write_row(env, 0, A1, O1, "local_c1",
              lrow(LC, "local_c1", "ACME-REVIEW side task",
                   spawnedFrom=side_link("local_p1")))
    write_row(env, 0, A2, O2, "local_x2",
              lrow(LX, "local_x2", "Northwind loop one",
                   spawnedFrom=side_link("local_y2")))
    write_row(env, 0, A2, O2, "local_y2",
              lrow(LY, "local_y2", "Northwind loop two",
                   spawnedFrom=side_link("local_x2")))
    journaled, written = {}, []
    real_new_op, real_write = ct.new_op, ct.atomic_write

    def spy_new_op(env_, manifest):
        journaled["rows"] = [(r["session"], r["account"], r["post_b64"])
                             for r in manifest["rows"]]
        journaled["cycles"] = manifest["lineage_cycles"]
        return real_new_op(env_, manifest)

    def spy_write(path, data):
        name = os.path.basename(path)
        if name.startswith("local_"):          # rows only, not the journal
            written.append(name)
        return real_write(path, data)
    monkeypatch.setattr(ct, "new_op", spy_new_op)
    monkeypatch.setattr(ct, "atomic_write", spy_write)
    assert ct.run_converge(env, m) == "completed"
    final = [(LC, A2), (LP, A3), (LC, A3)]
    assert [(s, a) for s, a, _ in journaled["rows"]] == final
    parent, child = rows_by(m, LP)[A3], rows_by(m, LC)[A3]
    stem = parent["name"][:-len(".json")]
    assert post_of(child)["spawnedFrom"] == {"sessionId": stem,
                                             "taskId": TASK}
    assert codes(child["lineage"]) == ["lineage_gained"]
    assert journaled["cycles"] == 1
    assert ct.list_ops(env)[-1].manifest["lineage_cycles"] == 1
    # What new_op journaled is what was written, row for row and in order.
    on_record = ct.list_ops(env)[-1].manifest["rows"]
    assert [(r["session"], r["account"], r["post_b64"])
            for r in on_record] == journaled["rows"]
    assert written == [r["name"] for r in on_record]
    for r in on_record:
        with open(r["dest_path"], "rb") as fh:
            assert fh.read() == ct.unb64(r["post_b64"])


def test_after_apply_trailer_prints_the_links_lines(
        mkenv, tmp_path, write_transcript, write_row, monkeypatch, capsys):
    """The after-apply lines sit under `created :` and count the written
    rows: carried, left out by the underlying reason, then what changed
    since the plan. The dry run above them still says what the plan would
    carry."""
    env = _monotonic_now(mkenv(tmp_path))
    linked_holdings(env, write_transcript, write_row, parent_in=(A2,),
                    chain={"firstSessionId": "local_p1", "depth": 1})
    real = ct.run_converge

    def late(env_, m_):
        # A collision for P's chosen title in A3, after the dry run printed.
        write_row(env, 0, A3, O3, "local_x3",
                  lrow(LX, "local_x3", "Northwind kickoff"))
        return real(env_, m_)
    monkeypatch.setattr(ct, "run_converge", late)
    rc = ct.cmd_converge(env, cv_ns(apply=True))
    out = capsys.readouterr().out
    assert rc == 3                           # the apply-time hold
    lines = out.splitlines()
    assert "links   : 2 to carry" in lines
    assert "chains  : 2 to carry" in lines
    created = lines.index("created : 2 row(s)")
    assert lines[created + 1] == (
        "links   : 1 carried, 1 left out (the parent's own row is held in "
        "this plan 1); since the plan: 1 dropped")
    assert lines[created + 2] == "chains  : 1 carried, 1 left out"
    assert lines[created + 3].startswith("skipped : " + LP[:8])
    # Every part of the line, in its order, each only when nonzero.

    def row(parent_row, *cs):
        return {"written": True, "lineage": dict(
            ct._lineage_empty(), parent_row=parent_row,
            notes=[{"code": c, "detail": ""} for c in cs])}
    assert ct._lineage_apply_lines([
        row("local_a", "lineage_retargeted"), row("local_b", "lineage_gained"),
        row("", "lineage_parent_dead", "lineage_dropped")]) == [
        "links   : 2 carried, 1 left out (the parent's transcript is gone "
        "1); since the plan: 1 changed, 1 dropped, 1 gained"]
    assert ct._lineage_apply_lines([row("local_a")]) == \
        ["links   : 1 carried"]
    assert ct._lineage_apply_lines([row("")]) == []


def test_verbose_apply_lists_each_rows_link_reasons_with_its_account(
        mkenv, tmp_path, write_transcript, write_row, monkeypatch, capsys):
    """--verbose lists them (spec, "converge", "Reporting"): under the
    after-apply links lines, one line per written row whose link or chain
    was left out or changed since the plan, naming the row, its account and
    each reason's phrase. A row that carried its link is not listed, and
    without --verbose nothing is. Under --anonymize the account label is
    scrubbed as the rest of converge's output scrubs it (main refuses
    --anonymize beside --verbose or --apply; the list holds to it anyway)."""
    real = ct.run_converge
    outputs = {}
    for leg in ("plain", "verbose", "anonymized"):
        env = _monotonic_now(mkenv(tmp_path / leg))
        linked_holdings(env, write_transcript, write_row, parent_in=(A2,),
                        chain={"firstSessionId": "local_p1", "depth": 1})
        agent_mode_email(env, A3, "bob@example.com")

        def late(env_, m_, env=env):
            # A collision for P's chosen title in A3, after the dry run.
            write_row(env, 0, A3, O3, "local_x3",
                      lrow(LX, "local_x3", "Northwind kickoff"))
            return real(env_, m_)
        monkeypatch.setattr(ct, "run_converge", late)
        ct._ANONYMIZE = leg == "anonymized"
        ct._ANON_CACHE.clear()
        try:
            rc = ct.cmd_converge(env, cv_ns(
                apply=True, verbose=leg != "plain",
                anonymize=leg == "anonymized"))
        finally:
            ct._ANONYMIZE = False
            ct._ANON_CACHE.clear()
        assert rc == 3, leg
        op = ct.list_ops(env)[-1]
        child = rows_by(op.manifest, LC)[A3]
        assert codes(child["lineage"]) == [
            "parent_pair_held", "lineage_chain_dropped", "lineage_dropped"]
        # converge's own label for the account: its address, then its ids.
        assert child["label"].startswith("bob@example.com")
        outputs[leg] = (capsys.readouterr().out.splitlines(), stem(child),
                        child["label"])
    phrases = ("[link: the parent's own row is held in this plan; the chain "
               "was left out; dropped since the plan]")
    lines, child, label = outputs["verbose"]
    at = lines.index("chains  : 1 carried, 1 left out")
    assert lines[at - 1].startswith("links   : 1 carried, 1 left out")
    assert lines[at + 1] == "   {0} -> {1}   {2}".format(child, label,
                                                       phrases)
    assert lines[at + 2].startswith("skipped : " + LP[:8])
    assert sum(1 for x in lines if x.startswith("   local_")
               and "[link:" in x) == 1
    lines, child, _label = outputs["plain"]
    at = lines.index("chains  : 1 carried, 1 left out")
    assert lines[at + 1].startswith("skipped : ")
    assert not any(x.startswith("   local_") and "[link:" in x
                   for x in lines)
    lines, child, label = outputs["anonymized"]
    listed = [x for x in lines if x.startswith("   " + child)]
    assert listed == ["   {0} -> {1}   {2}".format(
        child, ct._anon_label("account", label), phrases)]
    assert "@" not in listed[0]
    # The helper itself: rows with nothing to list, and junk, give nothing.
    assert ct._lineage_row_lines([
        {"name": "local_a.json", "label": "x", "lineage": ct._lineage_empty()},
        {"name": "local_b.json", "lineage": "junk"}, 5, None]) == []
    assert ct._lineage_row_lines(None) == []
    assert ct._lineage_row_lines([{
        "name": "local_g.json", "account": A2, "lineage": dict(
            ct._lineage_empty(), parent_row="local_p",
            notes=[{"code": "lineage_gained", "detail": ""}])}]) == [
        "   local_g -> {0}   [link: gained since the plan]".format(A2[:8])]


def test_a_row_is_never_skipped_over_its_link(
        mkenv, tmp_path, write_transcript, write_row):
    """The plan creates P's row in A3 and links C's A3 row to it; P's
    transcript then leaves the disk. P's own row is skipped as
    transcript_gone, and C's rows are still written, each without the link
    and with its reason - none is skipped for its link."""
    env = _monotonic_now(mkenv(tmp_path))
    linked_holdings(env, write_transcript, write_row, parent_in=(A2,))
    m = ct.plan_converge(env, ct.ConvergeFlags())
    assert rows_by(m, LC)[A3]["lineage"]["parent_planned"] is True
    rm_transcript(env, LP)
    assert ct.run_converge(env, m) == "completed"
    assert [(r["session"], r["skipped"]) for r in m["rows"]
            if r.get("skipped")] == [(LP, "transcript_gone")]
    for acct in (A2, A3):
        child = rows_by(m, LC)[acct]
        assert child["written"] is True and "skipped" not in child
        assert "spawnedFrom" not in on_disk(child)
        assert codes(child["lineage"]) == ["lineage_parent_dead",
                                           "lineage_dropped"]
    assert_lineage_agrees(ct.list_ops(env)[-1].manifest["rows"])


def test_recheck_defaults_to_no_relink(
        mkenv, tmp_path, write_transcript, write_row, monkeypatch):
    """Recovery's call never re-resolves: `relink` defaults to False, a
    recheck without it leaves every row's bytes, record and place alone,
    and forward recovery never runs the pass."""
    import inspect
    param = inspect.signature(ct._converge_recheck).parameters["relink"]
    assert param.default is False
    env = _monotonic_now(mkenv(tmp_path / "recheck"))
    linked_holdings(env, write_transcript, write_row, parent_in=(A2,))
    m = ct.plan_converge(env, ct.ConvergeFlags())
    before = [(r["name"], r["post_b64"], json.dumps(r["lineage"]))
              for r in m["rows"]]
    write_row(env, 0, A3, O3, "local_q3",
              lrow(LP, "local_q3", "Northwind kickoff"))
    ct._converge_recheck(env, m)
    assert rows_by(m, LP)[A3]["skipped"] == "already_present"
    assert [(r["name"], r["post_b64"], json.dumps(r["lineage"]))
            for r in m["rows"]] == before
    assert not any("lineage_planned" in r for r in m["rows"])

    env2 = _monotonic_now(mkenv(tmp_path / "recover"))
    _m2, op = _crash_after_first_row(env2, write_transcript, write_row)

    def never(*_a, **_k):
        raise AssertionError("recovery ran the lineage pass")
    monkeypatch.setattr(ct, "_converge_lineage", never)
    assert ct.recover_op(env2, op, "forward") == "completed"


def test_no_source_link_value_reaches_the_plan_or_the_apply_output(
        mkenv, tmp_path, write_transcript, write_row, monkeypatch, capsys):
    """Task 3's review, pinned: the source holder's link carries its own
    title and a value under an unknown key, and its chain an extra key.
    Their names are recorded as dropped; their values never reach the dry
    run's text or --json, either anonymized, the after-apply --json (whose
    retargeted row carries lineage_planned), or a written row."""
    env = _monotonic_now(mkenv(tmp_path))
    linked_holdings(env, write_transcript, write_row, parent_in=(A2,))
    write_row(env, 0, A1, O1, "local_c1", lrow(
        LC, "local_c1", "ACME-REVIEW side task",
        spawnedFrom=side_link("local_p1", title=SENT_TITLE,
                              zzLinkExtra=SENT_VALUE),
        startChain={"firstSessionId": "local_p1", "depth": 1,
                    "zzChainExtra": SENT_CHAIN}))
    sentinels = (SENT_TITLE, SENT_VALUE, SENT_CHAIN)
    m = ct.plan_converge(env, ct.ConvergeFlags())
    assert rows_by(m, LC)[A2]["lineage"]["dropped_keys"] == [
        "sideSession", "title", "zzLinkExtra", "startChain.zzChainExtra"]
    outputs = {}
    assert ct.cmd_converge(env, cv_ns()) == 0
    outputs["dry run"] = capsys.readouterr().out
    assert ct.cmd_converge(env, cv_ns(json=True)) == 0
    outputs["dry run --json"] = capsys.readouterr().out
    ct._ANONYMIZE = True
    ct._ANON_CACHE.clear()
    try:
        assert ct.cmd_converge(env, cv_ns(anonymize=True)) == 0
        outputs["--anonymize"] = capsys.readouterr().out
        assert ct.cmd_converge(env, cv_ns(anonymize=True, json=True)) == 0
        outputs["--anonymize --json"] = capsys.readouterr().out
    finally:
        ct._ANONYMIZE = False
        ct._ANON_CACHE.clear()
    real = ct.run_converge

    def late(env_, m_):
        write_row(env, 0, A3, O3, "local_q3",
                  lrow(LP, "local_q3", "Northwind kickoff"))
        return real(env_, m_)
    monkeypatch.setattr(ct, "run_converge", late)
    assert ct.cmd_converge(env, cv_ns(apply=True, json=True)) == 0
    outputs["apply --json"] = capsys.readouterr().out
    payload = json.loads(outputs["apply --json"])
    assert payload["result"] == "completed"
    assert any("lineage_planned" in r for r in payload["rows"])
    for name, text in outputs.items():
        # Not vacuous: each output does carry the link's report.
        assert "links   :" in text or '"lineage"' in text, name
        assert not any(s in text for s in sentinels), name
    written = [r for r in payload["rows"] if r.get("written")]
    assert len(written) == 2
    for r in written:
        with open(r["dest_path"], encoding="utf-8") as fh:
            text = fh.read()
        assert "spawnedFrom" in text
        assert not any(s in text for s in sentinels), r["name"]


# ------------------------------------------- journals from before lineage

def as_0_17(op):
    """Rewrite a journal the way 0.17.0 wrote it: no lineage_format, no
    lineage_cycles, and no per-row lineage or lineage_planned."""
    m = op.manifest
    for k in ("lineage_format", "lineage_cycles"):
        m.pop(k, None)
    for r in m["rows"]:
        r.pop("lineage", None)
        r.pop("lineage_planned", None)
    ct.save_manifest(op)
    return op


def test_a_0_17_converge_journal_pending_goes_forward_and_back(
        mkenv, tmp_path, write_transcript, write_row):
    for leg in ("fwd", "back"):
        env = _monotonic_now(mkenv(tmp_path / leg))
        m, op = _crash_after_first_row(env, write_transcript, write_row)
        as_0_17(op)
        assert "lineage_format" not in op.manifest
        c = ct.classify_op(env, op)
        assert sorted(c["resolutions"]) == ["back", "forward"]
        if leg == "fwd":
            assert ct.recover_op(env, op, "forward") == "completed"
            assert all(r["written"] for r in op.manifest["rows"])
        else:
            assert ct.recover_op(env, op, "back") == "rolled_back"
            assert store_rows(env, A2, O2) == ["local_s2.json", "local_s3.json"]


def test_a_0_17_converge_journal_completed_undoes(
        mkenv, tmp_path, write_transcript, write_row):
    env = _monotonic_now(mkenv(tmp_path))
    m, op = converged(env, write_transcript, write_row)
    as_0_17(op)
    assert ct.undo_converge(env, op) == "undone"
    assert store_rows(env, A2, O2) == ["local_s2.json", "local_s3.json"]
    assert store_rows(env, A3, O3) == ["local_s3.json"]


def test_recover_listing_and_doctor_accept_old_journals(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    env = _monotonic_now(mkenv(tmp_path))
    m, op = _crash_after_first_row(env, write_transcript, write_row)
    as_0_17(op)
    ns = types.SimpleNamespace(op_id=None, verbose=True)
    assert ct.cmd_recover(env, ns) == 1
    out = capsys.readouterr().out
    assert op.manifest["op_id"] in out and "back" in out
    assert "damaged" not in out
    rep = ct.gather_doctor(env)
    assert "damaged" not in json.dumps(rep)


def test_damaged_lineage_in_a_converge_op_is_a_damaged_record_not_a_crash(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    env = _monotonic_now(mkenv(tmp_path))
    m, op = _crash_after_first_row(env, write_transcript, write_row,
                                   setup=linked_holdings)
    for r in op.manifest["rows"]:
        r["lineage"] = "junk"
    ct.save_manifest(op)
    c = ct.classify_op(env, op)
    assert c["resolutions"] == [] and "damaged" in c["note"]
    with pytest.raises(ct.Refusal):
        ct.recover_op(env, op, "forward")
    ns = types.SimpleNamespace(op_id=None, verbose=True)
    assert ct.cmd_recover(env, ns) == 1
    assert "damaged" in capsys.readouterr().out
    ct.gather_doctor(env)


def test_a_new_format_linked_row_without_lineage_is_a_damaged_converge_record(
        mkenv, tmp_path, write_transcript, write_row):
    env = _monotonic_now(mkenv(tmp_path))
    m, op = _crash_after_first_row(env, write_transcript, write_row,
                                   setup=linked_holdings)
    linked = [r for r in op.manifest["rows"] if "spawnedFrom" in post_of(r)]
    assert linked
    del linked[0]["lineage"]
    ct.save_manifest(op)
    assert ct._converge_shape_error(op.manifest)
    assert ct.classify_op(env, op)["resolutions"] == []


def test_a_retargeted_gained_or_dropped_row_passes_classify_recover_and_undo(
        mkenv, tmp_path, write_transcript, write_row):
    env = _monotonic_now(mkenv(tmp_path))
    m, op = _crash_after_first_row(env, write_transcript, write_row,
                                   setup=linked_holdings)
    # lineage agrees with the bytes; the plan's record says otherwise.
    for r in op.manifest["rows"]:
        r["lineage_planned"] = {
            "parent_session": "cli_z", "parent_row": "local_z",
            "parent_planned": True, "root_session": "", "root_row": "",
            "root_planned": False, "carried_keys": ["sessionId"],
            "dropped_keys": [],
            "notes": [{"code": "lineage_retargeted", "detail": "d"}]}
    ct.save_manifest(op)
    assert ct._converge_shape_error(op.manifest) is None
    c = ct.classify_op(env, op)
    assert sorted(c["resolutions"]) == ["back", "forward"]
    assert ct.recover_op(env, op, "forward") == "completed"
    assert ct.undo_converge(env, op) == "undone"


def test_a_pending_converge_with_lineage_and_a_corrupt_post_image_still_goes_both_ways(
        mkenv, tmp_path, write_transcript, write_row):
    env = _monotonic_now(mkenv(tmp_path))
    m, op = _crash_after_first_row(env, write_transcript, write_row)
    assert op.manifest["lineage_format"] == 1
    pending = [r for r in op.manifest["rows"] if not r.get("written")]
    pending[0]["post_b64"] = "!!!not base64!!!"
    ct.save_manifest(op)
    assert ct._converge_shape_error(op.manifest) is None
    c = ct.classify_op(env, op)
    assert sorted(c["resolutions"]) == ["back", "forward"]


# ------------------------- carry the link (recovery, undo and the link report)
# docs/specs/2026-11-17-carry-lineage-design.md, "converge" ("Recovery") and
# "undo". Recovery never re-resolves: a pending row whose journaled link or
# chain names a planned row that recovery does not write is skipped as
# parent_not_written, and one whose written or existing target is gone or
# opens another conversation as parent_changed. Reversals run children before
# parents. Recovery, undo and recover --back end with the link report, read
# from the stores after the command's writes.

def stem(r):
    return r["name"][:-len(".json")]


def crash_on(env, m, nth=1, point="converge-mid-write"):
    """Apply M and crash the NTH time POINT fires. Returns the stuck op."""
    seen = {"n": 0}

    def hook(p):
        if p == point:
            seen["n"] += 1
            if seen["n"] == nth:
                raise SimulatedCrash()
    ct._crash_hook = hook
    try:
        with pytest.raises(SimulatedCrash):
            ct.run_converge(env, m)
    finally:
        ct._crash_hook = None
    op = ct.nonterminal_ops(env)[0]
    assert op.manifest["status"] == "writing"
    return op


def crash_before_any_row(env, m, monkeypatch):
    """Apply M and crash at its first row write, before anything lands."""
    real = ct.atomic_write

    def boom(path, data):
        if os.path.basename(path).startswith("local_"):
            raise SimulatedCrash()
        return real(path, data)
    monkeypatch.setattr(ct, "atomic_write", boom)
    with pytest.raises(SimulatedCrash):
        ct.run_converge(env, m)
    monkeypatch.setattr(ct, "atomic_write", real)
    op = ct.nonterminal_ops(env)[0]
    assert op.manifest["status"] == "writing"
    assert not any(r.get("written") for r in op.manifest["rows"])
    return op


def journal_of(env, op_id):
    """The op's manifest as it is on disk."""
    with open(os.path.join(env.ops_dir, op_id, "manifest.json"),
              encoding="utf-8") as fh:
        return json.load(fh)


def recover_ns(op_id, direction):
    return types.SimpleNamespace(op_id=op_id, forward=direction == "forward",
                                 back=direction == "back", apply=True,
                                 verbose=True)


EMPTY_REPORT = {"dangling": [], "redirected": [], "unchecked": 0}


def test_crash_before_the_parent_landed_forward_writes_parent_then_child(
        mkenv, tmp_path, write_transcript, write_row, monkeypatch):
    """Nothing landed before the crash. The child's planned parent is still
    pending, so it is not checked: forward writes the parent, then the child
    with its journaled link, and skips nothing."""
    env = _monotonic_now(mkenv(tmp_path))
    linked_holdings(env, write_transcript, write_row, parent_in=(A2,))
    m = ct.plan_converge(env, ct.ConvergeFlags())
    op = crash_before_any_row(env, m, monkeypatch)
    written = []
    real = ct.atomic_write

    def spy(path, data):
        if os.path.basename(path).startswith("local_"):
            written.append(os.path.basename(path))
        return real(path, data)
    monkeypatch.setattr(ct, "atomic_write", spy)
    assert ct.recover_op(env, op, "forward") == "completed"
    rows = op.manifest["rows"]
    assert not any(r.get("skipped") for r in rows)
    assert written == [r["name"] for r in rows]
    parent, child = rows_by(op.manifest, LP)[A3], rows_by(op.manifest, LC)[A3]
    assert written.index(parent["name"]) < written.index(child["name"])
    assert on_disk(child)["spawnedFrom"] == {"sessionId": stem(parent),
                                             "taskId": TASK}
    assert op.manifest["link_report"] == EMPTY_REPORT


def test_crash_then_another_parent_row_appears_child_skipped_parent_not_written(
        mkenv, tmp_path, write_transcript, write_row):
    """The first row landed; P's planned A3 row and C's A3 row, which links to
    it, did not. A row for P appears in A3 in the window: P's planned row is
    skipped as already_present, so C's A3 row is skipped as
    parent_not_written - never written naming a row that does not exist,
    and never re-resolved. The next converge plans it afresh."""
    env = _monotonic_now(mkenv(tmp_path))
    linked_holdings(env, write_transcript, write_row, parent_in=(A2,))
    m = ct.plan_converge(env, ct.ConvergeFlags())
    op = crash_on(env, m)
    parent, child = rows_by(op.manifest, LP)[A3], rows_by(op.manifest, LC)[A3]
    assert not parent["written"] and not child["written"]
    write_row(env, 0, A3, O3, "local_q3",
              lrow(LP, "local_q3", "Northwind kickoff"))
    assert ct.recover_op(env, op, "forward") == "completed"
    assert parent["skipped"] == "already_present"
    assert child["skipped"] == "parent_not_written"
    assert LP[:8] in child["skip_detail"]
    assert "already_present" in child["skip_detail"]
    assert "Northwind" not in child["skip_detail"]
    assert not child.get("written")
    assert not os.path.exists(child["dest_path"])
    # The post-image was never re-resolved.
    assert post_of(child)["spawnedFrom"]["sessionId"] == stem(parent)
    assert journal_of(env, op.manifest["op_id"])["rows"][2]["skipped"] == \
        "parent_not_written"
    # Short for the next converge, which links it to the row that exists.
    again = ct.plan_converge(env, ct.ConvergeFlags())
    assert post_of(rows_by(again, LC)[A3])["spawnedFrom"]["sessionId"] == \
        "local_q3"


def chain_of_three(env, write_transcript, write_row):
    """R (LR), P (LP) and C (LC), each held by A1 and A2, where P is a side
    session of R and C one of P. A3 holds none of them (a padding row keeps
    it a destination), so the plan writes R, P and C into A3, in that order,
    P linking to R's planned row and C to P's."""
    for sid in (LR, LP, LC):
        write_transcript(env, "C--p", sid, t_entries())
    for acct in (A1, A2):
        n = NUM[acct]
        write_row(env, 0, acct, ORG[acct], "local_r" + n,
                  lrow(LR, "local_r" + n, "Northwind root"))
        write_row(env, 0, acct, ORG[acct], "local_p" + n,
                  lrow(LP, "local_p" + n, "Northwind kickoff",
                       spawnedFrom=side_link("local_r" + n)))
        write_row(env, 0, acct, ORG[acct], "local_c" + n,
                  lrow(LC, "local_c" + n, "ACME-REVIEW side task",
                       spawnedFrom=side_link("local_p" + n)))
    write_row(env, 0, A3, O3, "local_pad", lrow(PAD, "local_pad", "Padding"))


def test_planned_chain_skip_carries_down_r_p_c(
        mkenv, tmp_path, write_transcript, write_row, monkeypatch):
    env = _monotonic_now(mkenv(tmp_path))
    chain_of_three(env, write_transcript, write_row)
    m = ct.plan_converge(env, ct.ConvergeFlags())
    assert [r["session"] for r in m["rows"]] == [LR, LP, LC]
    op = crash_before_any_row(env, m, monkeypatch)
    write_row(env, 0, A3, O3, "local_q3",
              lrow(LR, "local_q3", "Northwind root"))
    assert ct.recover_op(env, op, "forward") == "completed"
    skips = [(r["session"], r.get("skipped")) for r in op.manifest["rows"]]
    assert skips == [(LR, "already_present"), (LP, "parent_not_written"),
                     (LC, "parent_not_written")]
    p, c = rows_by(op.manifest, LP)[A3], rows_by(op.manifest, LC)[A3]
    assert LR[:8] in p["skip_detail"]
    assert LP[:8] in c["skip_detail"] and "parent_not_written" in \
        c["skip_detail"]
    assert not os.path.exists(p["dest_path"])
    assert not os.path.exists(c["dest_path"])


@pytest.mark.parametrize("target", ["parent", "root"])
def test_parent_written_then_repointed_child_skipped_parent_changed(
        mkenv, tmp_path, write_transcript, write_row, target):
    """The parent's (or the chain root's) planned row was written before the
    crash and repointed at another conversation since. The pending child
    that names it is skipped as parent_changed - never written with the
    stale link - and nothing is resolved again."""
    env = _monotonic_now(mkenv(tmp_path))
    if target == "parent":
        linked_holdings(env, write_transcript, write_row, parent_in=(A2,))
        m = ct.plan_converge(env, ct.ConvergeFlags())
        assert [(r["session"], r["account"]) for r in m["rows"]] == \
            [(LC, A2), (LP, A3), (LC, A3)]
        op = crash_on(env, m, nth=2)
        named, sid = rows_by(op.manifest, LP)[A3], LP
    else:
        chained_holdings(env, write_transcript, write_row, root_in=(A2,))
        m = ct.plan_converge(env, ct.ConvergeFlags())
        assert [(r["session"], r["account"]) for r in m["rows"]] == \
            [(LR, A3), (LC, A2), (LC, A3)]
        op = crash_on(env, m, nth=1)
        named, sid = rows_by(op.manifest, LR)[A3], LR
    child = rows_by(op.manifest, LC)[A3]
    assert named["written"] and not child["written"]
    _app_touches(named["dest_path"], cliSessionId=S3)
    assert ct.recover_op(env, op, "forward") == "completed"
    assert child["skipped"] == "parent_changed"
    assert sid[:8] in child["skip_detail"] and S3[:8] in child["skip_detail"]
    assert not os.path.exists(child["dest_path"])
    assert "lineage_planned" not in child
    if target == "root":
        assert rows_by(op.manifest, LC)[A2]["written"] is True


def test_existing_parent_deleted_in_the_window_child_skipped(
        mkenv, tmp_path, write_transcript, write_row):
    """C's A3 row links to the parent row A3 already had. That row is
    deleted in the crash window: C's A3 row is skipped as parent_changed.
    (A padding row keeps A3 a populated destination once its row for P
    goes.)"""
    env = _monotonic_now(mkenv(tmp_path))
    linked_holdings(env, write_transcript, write_row)
    write_row(env, 0, A3, O3, "local_pad", lrow(PAD, "local_pad", "Padding"))
    m = ct.plan_converge(env, ct.ConvergeFlags())
    op = crash_on(env, m)
    first, child = op.manifest["rows"]
    assert first["written"] and first["account"] == A2
    os.unlink(os.path.join(env.store_candidates[0], A3, O3, "local_p3.json"))
    assert ct.recover_op(env, op, "forward") == "completed"
    assert child["skipped"] == "parent_changed"
    assert LP[:8] in child["skip_detail"]
    assert not os.path.exists(child["dest_path"])
    assert op.manifest["link_report"] == EMPTY_REPORT


@pytest.mark.parametrize("reason", ["parent_not_written", "parent_changed"])
def test_recover_forward_prints_each_row_it_skipped_over_its_link(
        mkenv, tmp_path, write_transcript, write_row, capsys, reason):
    """recover --forward names each row it skipped as parent_not_written or
    parent_changed, with its reason, in the shape converge prints its
    skipped rows, after the result line. A row it wrote is not named, and
    neither is a skip of another kind (P's already_present)."""
    env = _monotonic_now(mkenv(tmp_path))
    linked_holdings(env, write_transcript, write_row, parent_in=(A2,))
    m = ct.plan_converge(env, ct.ConvergeFlags())
    if reason == "parent_not_written":
        op = crash_on(env, m)
        write_row(env, 0, A3, O3, "local_q3",
                  lrow(LP, "local_q3", "Northwind kickoff"))
    else:
        op = crash_on(env, m, nth=2)
        _app_touches(rows_by(op.manifest, LP)[A3]["dest_path"],
                     cliSessionId=S3)
    op_id = op.manifest["op_id"]
    assert ct.cmd_recover(env, recover_ns(op_id, "forward")) == 0
    out = capsys.readouterr().out.splitlines()
    child = rows_by(journal_of(env, op_id), LC)[A3]
    assert child["skipped"] == reason
    line = "skipped : {0} -> {1}: {2} - {3}".format(
        LC[:8], child["label"], reason, child["skip_detail"])
    assert ct._converge_skip_line(child) == line
    skips = [x for x in out if x.startswith("skipped : ")]
    assert skips == [line]
    assert out.index("result: completed") < out.index(line)
    assert "Northwind" not in line and "ACME" not in line


def test_child_landed_then_parent_deleted_forward_reports_dangling(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    """C's A2 row landed before the crash; its parent's row is then deleted.
    Forward finishes C's A3 row and its link report names the landed row
    whose link now dangles - the crash-window deletion."""
    env = _monotonic_now(mkenv(tmp_path))
    linked_holdings(env, write_transcript, write_row)
    m = ct.plan_converge(env, ct.ConvergeFlags())
    op = crash_on(env, m)
    landed = op.manifest["rows"][0]
    assert landed["written"] and landed["account"] == A2
    os.unlink(os.path.join(env.store_candidates[0], A2, O2, "local_p2.json"))
    op_id = op.manifest["op_id"]
    assert ct.cmd_recover(env, recover_ns(op_id, "forward")) == 0
    out = capsys.readouterr().out
    assert "result: completed" in out
    rep = journal_of(env, op_id)["link_report"]
    assert rep == {"dangling": [{"row": stem(landed), "target": "local_p2",
                                 "kind": "parent"}],
                   "redirected": [], "unchecked": 0}
    lines = out.splitlines()
    line = "link left dangling: {0} (its parent's row was removed)".format(
        stem(landed))
    assert line in lines
    assert lines.index("result: completed") < lines.index(line)
    assert rows_by(journal_of(env, op_id), LC)[A3]["written"] is True


def test_all_landed_forward_still_reports_links(
        mkenv, tmp_path, write_transcript, write_row):
    """Every row landed and only the completion marker is missing, so
    forward is journal-only - no guard, no recheck - and it still ends with
    the link report: one landed row's parent row deleted (dangling), the
    other's repointed (redirected)."""
    env = _monotonic_now(mkenv(tmp_path))
    linked_holdings(env, write_transcript, write_row)
    m = ct.plan_converge(env, ct.ConvergeFlags())
    op = crash_on(env, m, point="converge-before-complete")
    a2, a3 = rows_by(op.manifest, LC)[A2], rows_by(op.manifest, LC)[A3]
    assert a2["written"] and a3["written"]
    os.unlink(os.path.join(env.store_candidates[0], A3, O3, "local_p3.json"))
    _app_touches(os.path.join(env.store_candidates[0], A2, O2,
                              "local_p2.json"), cliSessionId=S3)
    env.process_lister = lambda: [(99999, "claude.exe")]
    assert ct.recover_op(env, op, "forward") == "completed"
    want = {"dangling": [{"row": stem(a3), "target": "local_p3",
                          "kind": "parent"}],
            "redirected": [{"row": stem(a2), "target": "local_p2",
                            "kind": "parent"}],
            "unchecked": 0}
    assert op.manifest["link_report"] == want
    assert journal_of(env, op.manifest["op_id"])["link_report"] == want


def test_undo_removes_children_before_parents(
        mkenv, tmp_path, write_transcript, write_row, monkeypatch):
    """undo, and recover --back, remove the rows in reverse write order: the
    child before the parent it links to, so an interrupted reversal never
    leaves a child naming a row already removed."""
    removed = []
    real = ct.os.unlink

    def spy(path, *a, **k):
        if os.path.basename(str(path)).startswith("local_"):
            removed.append(os.path.basename(str(path)))
        return real(path, *a, **k)
    monkeypatch.setattr(ct.os, "unlink", spy)
    for leg in ("undo", "back"):
        removed.clear()
        env = _monotonic_now(mkenv(tmp_path / leg))
        linked_holdings(env, write_transcript, write_row, parent_in=(A2,))
        m = ct.plan_converge(env, ct.ConvergeFlags())
        if leg == "undo":
            assert ct.run_converge(env, m) == "completed"
            op = ct.list_ops(env)[-1]
            assert ct.undo_converge(env, op) == "undone"
        else:
            op = crash_on(env, m, point="converge-before-complete")
            assert ct.recover_op(env, op, "back") == "rolled_back"
        rows = op.manifest["rows"]
        assert removed == [r["name"] for r in reversed(rows)], leg
        parent, child = rows_by(op.manifest, LP)[A3], \
            rows_by(op.manifest, LC)[A3]
        assert removed.index(child["name"]) < removed.index(parent["name"])
        assert op.manifest["link_report"] == EMPTY_REPORT, leg


def test_undo_last_pointer_survivor_follows_reverse_write_order(
        mkenv, tmp_path, write_transcript, write_row):
    """Both of S1's created rows would go once its holder is removed, and the
    last-pointer rule keeps the one met last. Undo now meets rows in reverse
    write order, so the survivor is S1's row that was written first."""
    env = _monotonic_now(mkenv(tmp_path))
    m, op = converged(env, write_transcript, write_row)
    os.unlink(os.path.join(env.store_candidates[0], A1, O1, "local_s1.json"))
    first, second = [r for r in op.manifest["rows"] if r["session"] == S1]
    assert ct.undo_converge(env, op) == "undone"
    kept = [s for s in op.manifest["undo_report"]["skipped"]
            if s["reason"] == "last_pointer"]
    assert [s["name"] for s in kept] == [first["name"]]
    assert os.path.exists(first["dest_path"])
    assert not os.path.exists(second["dest_path"])


def test_undo_reports_a_surviving_link_to_a_removed_row_from_an_earlier_op(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    """An earlier converge created P's row in A3; a later one linked C's new
    A3 row to it. Undoing the earlier op removes P's row, and its link
    report names C's surviving row, whichever op created it."""
    env = _monotonic_now(mkenv(tmp_path))
    linked_holdings(env, write_transcript, write_row, parent_in=(A2,))
    m1 = ct.plan_converge(env, ct.ConvergeFlags(only=LP[:8]))
    assert [(r["session"], r["account"]) for r in m1["rows"]] == [(LP, A3)]
    assert ct.run_converge(env, m1) == "completed"
    op1 = ct.list_ops(env)[-1]
    parent = op1.manifest["rows"][0]
    m2 = ct.plan_converge(env, ct.ConvergeFlags())
    assert ct.run_converge(env, m2) == "completed"
    child = rows_by(m2, LC)[A3]
    assert on_disk(child)["spawnedFrom"]["sessionId"] == stem(parent)
    op1_id = op1.manifest["op_id"]
    assert ct.cmd_undo(env, undo_ns(apply=True, verbose=True,
                                    op_id=op1_id)) == 0
    out = capsys.readouterr().out
    assert not os.path.exists(parent["dest_path"])
    assert os.path.exists(child["dest_path"])
    rep = journal_of(env, op1_id)["link_report"]
    assert rep == {"dangling": [{"row": stem(child), "target": stem(parent),
                                 "kind": "parent"}],
                   "redirected": [], "unchecked": 0}
    lines = out.splitlines()
    line = "link left dangling: {0} (its parent's row was removed)".format(
        stem(child))
    assert line in lines
    assert lines.index("removed 1 row(s); 0 already gone") < lines.index(line)


def test_undo_does_not_report_unrelated_pre_existing_broken_links(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    """The report names links to rows THIS undo removed. A row outside the
    op whose links named a missing row before the undo, and a kept row of
    the op whose parent someone else deleted before it, are not this undo's
    doing and are not reported."""
    env = _monotonic_now(mkenv(tmp_path))
    linked_holdings(env, write_transcript, write_row, parent_in=(A2,))
    m = ct.plan_converge(env, ct.ConvergeFlags())
    assert ct.run_converge(env, m) == "completed"
    op = ct.list_ops(env)[-1]
    write_row(env, 0, A3, O3, "local_k3", lrow(
        LX, "local_k3", "Northwind loose end",
        spawnedFrom={"sessionId": "local_never", "taskId": TASK},
        startChain={"firstSessionId": "local_never", "depth": 1}))
    kept = rows_by(op.manifest, LC)[A2]
    _app_touches(kept["dest_path"], title="Northwind renamed")
    os.unlink(os.path.join(env.store_candidates[0], A2, O2, "local_p2.json"))
    assert ct.cmd_undo(env, undo_ns(apply=True, verbose=True)) == 0
    out = capsys.readouterr().out
    saved = journal_of(env, op.manifest["op_id"])
    assert [s["name"] for s in saved["undo_report"]["skipped"]] == \
        [kept["name"]]
    assert saved["undo_report"]["deleted"] == 2
    assert saved["link_report"] == EMPTY_REPORT
    assert os.path.exists(kept["dest_path"])
    assert not any(line.startswith(("link left", "link redirected",
                                    "links not checked"))
                   for line in out.splitlines())


def test_back_after_a_skipped_drifted_child_reports_dangling(
        mkenv, tmp_path, write_transcript, write_row, capsys):
    """Every row landed; the app then rewrote C's A3 row. recover --back
    leaves that drifted row in place, as residue, and removes P's A3 row
    beneath it: the report names the surviving child's dangling link."""
    env = _monotonic_now(mkenv(tmp_path))
    linked_holdings(env, write_transcript, write_row, parent_in=(A2,))
    m = ct.plan_converge(env, ct.ConvergeFlags())
    op = crash_on(env, m, point="converge-before-complete")
    parent, child = rows_by(op.manifest, LP)[A3], rows_by(op.manifest, LC)[A3]
    _app_touches(child["dest_path"], lastFocusedAt=1_900_000_000_000)
    op_id = op.manifest["op_id"]
    assert ct.cmd_recover(env, recover_ns(op_id, "back")) == 0
    out = capsys.readouterr().out
    assert os.path.exists(child["dest_path"])
    assert not os.path.exists(parent["dest_path"])
    saved = journal_of(env, op_id)
    assert child["name"] in saved["rollback_residue"]
    assert saved["link_report"] == {
        "dangling": [{"row": stem(child), "target": stem(parent),
                      "kind": "parent"}],
        "redirected": [], "unchecked": 0}
    lines = out.splitlines()
    line = "link left dangling: {0} (its parent's row was removed)".format(
        stem(child))
    assert line in lines
    assert lines.index(line) > [i for i, x in enumerate(lines)
                                if "left something behind" in x][0]


def test_link_report_counts_unreadable_targets_and_never_raises(
        tmp_path, monkeypatch):
    store = tmp_path / "acct" / "org"
    store.mkdir(parents=True)
    s = str(store)

    def put(name, text):
        with open(os.path.join(s, name), "w", encoding="utf-8") as fh:
            fh.write(text)
    put("local_c.json", json.dumps({
        "sessionId": "local_c", "cliSessionId": LC,
        "spawnedFrom": {"sessionId": "local_p", "taskId": TASK},
        "startChain": {"firstSessionId": "local_r", "depth": 1}}))
    put("local_p.json", "{not json")
    put("local_r.json", json.dumps({"sessionId": "local_r",
                                    "cliSessionId": LR}))
    put("local_junk.json", "[1, 2")
    lin = dict(ct._lineage_empty(), parent_session=LP, parent_row="local_p",
               root_session=LR, root_row="local_r")
    op_row = {"name": "local_c.json", "store_path": s, "written": True,
              "dest_path": os.path.join(s, "local_c.json"), "lineage": lin}
    # Forward: the parent's row cannot be read; the root is fine. Rows the
    # report never needed are not counted.
    rep = ct._link_report([op_row], [s], forward=True)
    assert rep == {"dangling": [], "redirected": [], "unchecked": 1}
    # A reversal that removed a row from this store reads every row in it,
    # and counts the two it could not read.
    rep = ct._link_report([], [s], removed={(s, "local_gone")})
    assert rep == {"dangling": [], "redirected": [], "unchecked": 2}
    # Nothing it is handed makes it raise.
    for args, kw in (
            ((None, None), {}),
            (([1, "x", {"lineage": "junk"}, {"name": 5, "lineage": {}}],
              [None, 5, os.path.join(s, "missing")]),
             {"removed": {("a",), None}, "restored": 5}),
            (([op_row], [s]), {"removed": "nonsense", "forward": True})):
        rep = ct._link_report(*args, **kw)
        assert isinstance(rep, dict)
        assert "error" in rep or set(rep) == {"dangling", "redirected",
                                             "unchecked"}

    def broken(*_a, **_k):
        raise RuntimeError("a reader broke")
    monkeypatch.setattr(ct, "_lineage_target", broken)
    rep = ct._link_report([op_row], [s], forward=True)
    assert set(rep) == {"error"} and isinstance(rep["error"], str)
    assert rep["error"] and "\n" not in rep["error"]
    # The two readers never raise either.
    monkeypatch.undo()
    assert ct._lineage_store_rows(None) == ([], 0)
    assert ct._lineage_store_rows(os.path.join(s, "missing")) == ([], 0)
    assert ct._lineage_target(None, None) == ("unreadable", None)
    assert ct._lineage_target(s, "local_p") == ("unreadable", None)
    assert ct._lineage_target(s, "local_none") == ("absent", None)
    state, data = ct._lineage_target(s, "local_r")
    assert state == "present" and data["cliSessionId"] == LR
    rows, bad = ct._lineage_store_rows(s)
    assert sorted(i for i, _ in rows) == ["local_c", "local_r"] and bad == 2


def test_link_report_scans_only_the_ops_stores_after_the_writes(
        mkenv, tmp_path, write_transcript, write_row, monkeypatch):
    """Undo removes every row it created, child and parent alike. The report
    reads each of the op's stores once, after the removals, so the removed
    child is never read as a survivor. A2 holds a row under the removed
    parent's filename (sync keeps filenames across accounts) and a row
    linking to it: that pair was not removed, so it is not dangling. A4 is
    not one of the op's stores and is never read."""
    env = _monotonic_now(mkenv(tmp_path))
    linked_holdings(env, write_transcript, write_row, parent_in=(A2,))
    m = ct.plan_converge(env, ct.ConvergeFlags())
    assert ct.run_converge(env, m) == "completed"
    op = ct.list_ops(env)[-1]
    parent = rows_by(op.manifest, LP)[A3]
    write_row(env, 0, A2, O2, stem(parent),
              lrow(LP, stem(parent), "Northwind kickoff"))
    write_row(env, 0, A2, O2, "local_k2", lrow(
        LX, "local_k2", "Northwind loose end",
        spawnedFrom={"sessionId": stem(parent), "taskId": TASK}))
    write_row(env, 0, A4, O4, "local_k4", lrow(
        LX, "local_k4", "Northwind loose end",
        spawnedFrom={"sessionId": stem(parent), "taskId": TASK}))
    scans = []
    real = ct._lineage_store_rows

    def spy(store_path):
        rows, bad = real(store_path)
        scans.append((os.path.normcase(os.path.realpath(store_path)),
                      sorted(i for i, _ in rows)))
        return rows, bad
    monkeypatch.setattr(ct, "_lineage_store_rows", spy)
    assert ct.undo_converge(env, op) == "undone"
    assert op.manifest["undo_report"]["deleted"] == 3
    root = env.store_candidates[0]

    def norm(acct, org):
        return os.path.normcase(os.path.realpath(os.path.join(root, acct,
                                                              org)))
    a2, a3 = norm(A2, O2), norm(A3, O3)
    assert sorted(p for p, _ in scans) == sorted([a2, a3])
    ids = dict(scans)
    assert ids[a2] == sorted(["local_p2", stem(parent), "local_k2"])
    assert ids[a3] == ["local_pad"]
    assert op.manifest["link_report"] == EMPTY_REPORT


@pytest.mark.parametrize("path", ["pending", "all landed"])
def test_link_report_is_saved_to_the_journal_on_forward_recovery(
        mkenv, tmp_path, write_transcript, write_row, monkeypatch, path):
    """execute_converge_op has already saved the completed op when the report
    is made, so forward recovery saves it again - before rotate_ops, on both
    paths."""
    env = _monotonic_now(mkenv(tmp_path))
    linked_holdings(env, write_transcript, write_row)
    m = ct.plan_converge(env, ct.ConvergeFlags())
    if path == "pending":
        op = crash_on(env, m)
    else:
        op = crash_on(env, m, point="converge-before-complete")
    op_id = op.manifest["op_id"]
    at_rotate = {}
    real = ct.rotate_ops

    def spy(env_):
        at_rotate.update(journal_of(env, op_id))
        return real(env_)
    monkeypatch.setattr(ct, "rotate_ops", spy)
    assert ct.recover_op(env, op, "forward") == "completed"
    assert at_rotate["status"] == "completed"
    assert at_rotate["link_report"] == EMPTY_REPORT


def test_link_report_failure_prints_its_line(
        mkenv, tmp_path, write_transcript, write_row, monkeypatch, capsys):
    """A report that fails never fails the command it reports for: undo and
    recover still finish, exit 0, and print the report's own line."""
    def broken(*_a, **_k):
        raise RuntimeError("a reader broke")
    env = _monotonic_now(mkenv(tmp_path / "undo"))
    m, op = converged(env, write_transcript, write_row)
    monkeypatch.setattr(ct, "_lineage_store_rows", broken)
    assert ct.cmd_undo(env, undo_ns(apply=True, verbose=True)) == 0
    out = capsys.readouterr().out.splitlines()
    assert "result: undone" in out
    failed = [x for x in out
              if x.startswith("links not checked: the link report failed (")]
    assert len(failed) == 1 and failed[0].endswith(")")
    assert out.index(failed[0]) > out.index("result: undone")
    assert "error" in journal_of(env, op.manifest["op_id"])["link_report"]

    env = _monotonic_now(mkenv(tmp_path / "recover"))
    linked_holdings(env, write_transcript, write_row)
    op = crash_on(env, ct.plan_converge(env, ct.ConvergeFlags()))
    monkeypatch.setattr(ct, "_lineage_target", broken)
    assert ct.cmd_recover(env, recover_ns(op.manifest["op_id"],
                                          "forward")) == 0
    out = capsys.readouterr().out.splitlines()
    assert "result: completed" in out
    assert any(x.startswith("links not checked: the link report failed (")
               for x in out)


def test_lineage_target_refuses_a_traversal_id_and_never_reads_an_unresolved_store(
        mkenv, tmp_path, write_transcript, write_row, monkeypatch):
    env = _monotonic_now(mkenv(tmp_path))
    store = os.path.join(env.store_candidates[0], A2, O2)
    os.makedirs(store)
    good = json.dumps({"sessionId": "local_x", "cliSessionId": LP})
    # A readable row one level up, and one beside the store: a traversal id
    # must never reach either.
    for where in (os.path.dirname(store), os.path.join(store, "sub")):
        os.makedirs(where, exist_ok=True)
        with open(os.path.join(where, "local_x.json"), "w",
                  encoding="utf-8") as fh:
            fh.write(good)
    for bad in ("../local_x", "..\\local_x", "local_x/../../local_x",
                "sub/local_x", "sub\\local_x", "local_x\n", "local_x.json",
                "local_", "LOCAL_x", "x", "", None, 5, ["local_x"]):
        assert ct._lineage_target(store, bad) == ("unreadable", None), bad
    # A store this version does not read is never handed to either reader:
    # a pending row of a stuck op is moved, in its journal, outside every
    # discovered root. Only back is offered, and its report scans only the
    # stores the resolution gate resolved.
    env2 = _monotonic_now(mkenv(tmp_path / "stray"))
    m, op = _crash_after_first_row(env2, write_transcript, write_row)
    outside = str(tmp_path / "elsewhere" / A3 / O3)
    os.makedirs(outside)
    stray = [r for r in op.manifest["rows"] if not r["written"]][-1]
    stray["store_path"] = outside
    stray["dest_path"] = os.path.join(outside, stray["name"])
    with open(stray["dest_path"], "w", encoding="utf-8") as fh:
        fh.write(good)
    ct.save_manifest(op)
    assert ct.classify_op(env2, op)["resolutions"] == ["back"]
    seen = []
    real_rows, real_target = ct._lineage_store_rows, ct._lineage_target

    def rows_spy(store_path):
        seen.append(store_path)
        return real_rows(store_path)

    def target_spy(store_path, row_id):
        seen.append(store_path)
        return real_target(store_path, row_id)
    monkeypatch.setattr(ct, "_lineage_store_rows", rows_spy)
    monkeypatch.setattr(ct, "_lineage_target", target_spy)
    assert ct.recover_op(env2, op, "back") == "rolled_back"
    assert seen
    norm = os.path.normcase(os.path.realpath(outside))
    assert not any(os.path.normcase(os.path.realpath(p)) == norm
                   for p in seen)
    assert os.path.exists(stray["dest_path"])
    assert "link_report" in op.manifest
