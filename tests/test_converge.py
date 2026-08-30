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
    for h in collision_holds(m):
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
    for h in collision_holds(m):
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
    for h in collision_holds(m):
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
    for h in collision_holds(m):
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
