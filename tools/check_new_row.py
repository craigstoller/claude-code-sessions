"""ccs new-row - creating a sidebar row for a conversation that has none."""
import builtins
import json
import os
import shutil
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import claude_code_sessions as ccs  # noqa: E402

ok = []


def check(name, cond, extra=""):
    print("%s %s%s" % ("OK " if cond else "BAD", name, ("  " + extra) if extra else ""))
    ok.append(bool(cond))


class _Ns(object):
    """A stand-in for argparse's Namespace, for driving cmd_* directly."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


LIVE, DORM = "a" * 32, "b" * 32
ORG_L, ORG_D = "1" * 32, "2" * 32
SID = "%032d" % 71


def rec(kind, body=None, ts="2026-06-14T10:00:00.000Z", cwd=None, custom=None,
        model=None, effort=None):
    d = {"type": kind, "timestamp": ts}
    if body is not None:
        d["message"] = {"content": body}
    if model:
        d.setdefault("message", {})["model"] = model
    if cwd:
        d["cwd"] = cwd
    if custom:
        d["customTitle"] = custom
    if effort:
        d["effort"] = effort
    return json.dumps(d)


def build(records, sid=SID):
    """A store with one signed-in account, one dormant, and one transcript."""
    root = tempfile.mkdtemp(prefix="newrow-")
    home = os.path.join(root, "home")
    store = os.path.join(root, "Claude", "claude-code-sessions")
    live = os.path.join(store, LIVE, ORG_L)
    dorm = os.path.join(store, DORM, ORG_D)
    for d in (live, dorm, home):
        os.makedirs(d)
    projects = os.path.join(home, ".claude", "projects", "proj")
    os.makedirs(projects)
    with open(os.path.join(projects, sid + ".jsonl"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(records) + "\n")
    with open(os.path.join(home, ".claude.json"), "w") as fh:
        json.dump({"oauthAccount": {"accountUuid": LIVE, "organizationUuid": ORG_L,
                                    "emailAddress": "live@example.com"}}, fh)
    with open(os.path.join(root, "Claude", "config.json"), "w") as fh:
        json.dump({"lastKnownAccountUuid": LIVE}, fh)
    env = ccs.default_env()
    env.home = home
    env.projects_root = os.path.join(home, ".claude", "projects")
    env.store_candidates = [store]
    env.ops_dir = os.path.join(root, "journal", "ops")
    env.moved_log = os.path.join(root, "journal", "moved-log.jsonl")
    env.process_lister = lambda: []
    return root, env, live, dorm


def prose(n):
    return [rec("user" if i % 2 == 0 else "assistant",
                "turn %d with enough words in it to count as a real message" % i,
                ts="2026-06-14T10:%02d:00.000Z" % (i + 1),
                model="claude-fable-5" if i % 2 else None,
                effort="xhigh")
            for i in range(n)]


OPENER = rec("user", "the opening message, long enough to be a real one",
             ts="2026-06-14T09:00:00.000Z", cwd=r"C:\Users\craig\Projects\Personal")


# ------------------------------------------------------------ transcript facts
print("\n--- transcript facts ---")

# _iso_ms pinned against INDEPENDENTLY computed epoch integers, not against
# itself. Every check below this point compares _transcript_facts's output to
# ccs._iso_ms(same literal string) - which pins the RELATIONSHIP between the
# two call sites, not the VALUE either one produces. If _iso_ms lost its
# `base.replace(tzinfo=datetime.timezone.utc)` and started reading timestamps
# as local time, both sides of every comparison below would shift by the same
# wrong amount and this suite would still print ALL PASS. Hardcoded integers
# are the point: a test that computes its expectation with the function under
# test cannot detect a timezone regression in that function.
check("_iso_ms is exact against an independently computed epoch",
      ccs._iso_ms("2026-06-14T09:00:00.000Z") == 1781427600000,
      str(ccs._iso_ms("2026-06-14T09:00:00.000Z")))
check("  a second independently computed epoch",
      ccs._iso_ms("2026-06-14T10:08:00.000Z") == 1781431680000,
      str(ccs._iso_ms("2026-06-14T10:08:00.000Z")))
check("  and the fractional-milliseconds branch",
      ccs._iso_ms("2026-06-14T09:00:00.123Z") == 1781427600123,
      str(ccs._iso_ms("2026-06-14T09:00:00.123Z")))
check("_iso_ms returns None for something unparseable, per its documented contract",
      ccs._iso_ms("not a timestamp") is None,
      repr(ccs._iso_ms("not a timestamp")))

root, env, live, dorm = build([OPENER] + prose(8))
f = ccs._transcript_facts(env, SID)
check("cwd comes from the transcript",
      f["cwd"] == r"C:\Users\craig\Projects\Personal", f["cwd"])
check("createdAt is the FIRST record in sequential order, not the earliest",
      f["created_ms"] == ccs._iso_ms("2026-06-14T09:00:00.000Z"), str(f["created_ms"]))
check("lastActivityAt is the LAST record in sequential order",
      f["last_ms"] == ccs._iso_ms("2026-06-14T10:08:00.000Z"), str(f["last_ms"]))
check("turns are counted as _message_fingerprints counts them",
      f["turns"] == 9, str(f["turns"]))
check("no customTitle means None", f["custom_title"] is None)
check("the model is DERIVED from the transcript, never defaulted",
      f["model"] == "claude-fable-5", str(f["model"]))
check("  and so is effort", f["effort"] == "xhigh", str(f["effort"]))
shutil.rmtree(root, ignore_errors=True)

# The first-wins / last-wins asymmetry, pinned. cwd also populates originCwd,
# and a session that changed directories still originated in the first one;
# model and effort should be whatever the session was running when it stopped.
root, env, live, dorm = build([
    rec("user", "opening message long enough to count here",
        ts="2026-06-14T09:00:00.000Z", cwd=r"C:\First\Place",
        effort="high"),
    rec("assistant", "a reply long enough to count as a real message",
        ts="2026-06-14T09:30:00.000Z", model="claude-opus-4-8"),
    rec("user", "a later message from somewhere else entirely",
        ts="2026-06-14T10:00:00.000Z", cwd=r"C:\Second\Place",
        effort="xhigh"),
    rec("assistant", "a later reply long enough to count as a message",
        ts="2026-06-14T10:30:00.000Z", model="claude-fable-5")])
f = ccs._transcript_facts(env, SID)
check("cwd is FIRST-wins - the session's origin, not its last stop",
      f["cwd"] == r"C:\First\Place", f["cwd"])
check("  while model is LAST-wins", f["model"] == "claude-fable-5", str(f["model"]))
check("  and so is effort", f["effort"] == "xhigh", str(f["effort"]))
shutil.rmtree(root, ignore_errors=True)

# customTitle is LAST-wins too. A rename appends a new record rather than
# editing the old one, so first-wins hands back a title the user already
# replaced. Measured 2026-08-23: 47 of 507 transcripts here carry more than one
# distinct customTitle - 9%, not a corner case.
root, env, live, dorm = build([
    rec("user", "the opening message, long enough to be a real one",
        ts="2026-06-14T09:00:00.000Z", cwd=r"C:\Users\craig\Projects\Personal",
        custom="Task manager performance audit"),
    rec("assistant", "a reply long enough to count as a real message",
        ts="2026-06-14T09:30:00.000Z", model="claude-fable-5"),
    rec("user", "a later message after the conversation was renamed",
        ts="2026-06-14T10:00:00.000Z",
        custom="Task manager performance audit (fork)")])
f = ccs._transcript_facts(env, SID)
check("customTitle is LAST-wins - a rename is not undone",
      f["custom_title"] == "Task manager performance audit (fork)",
      str(f["custom_title"]))
shutil.rmtree(root, ignore_errors=True)

# An unmeasurable turn count must stay unmeasured. _message_fingerprints
# returns None for a transcript over TRANSCRIPT_COMPARE_MAX_BYTES, and
# len(None or []) would silently turn "too big to count" into "0 turns" -
# a false assertion, on exactly the large conversations worth recovering.
root, env, live, dorm = build([OPENER] + prose(8))
real_fp = ccs._message_fingerprints
ccs._message_fingerprints = lambda p: None
try:
    f = ccs._transcript_facts(env, SID)
finally:
    ccs._message_fingerprints = real_fp
check("an uncountable transcript reports turns as None, NOT as zero",
      f["turns"] is None, repr(f["turns"]))
shutil.rmtree(root, ignore_errors=True)

# ------------------------------------------------------------ the row template
print("\n--- the synthesized row ---")

root, env, live, dorm = build([OPENER] + prose(8))
f = ccs._transcript_facts(env, SID)
row = ccs._synthesize_row(SID, "A title", "user", f, "u" * 36)

# The expected field set is written HERE, not read from the template. A golden
# test that reads its expectations from the thing it checks passes by
# construction and cannot tell "correctly added" from "accidentally added".
EXPECTED = {
    "sessionId": "local_" + "u" * 36,
    "cliSessionId": SID,
    "title": "A title",
    "titleSource": "user",
    "cwd": r"C:\Users\craig\Projects\Personal",
    "originCwd": r"C:\Users\craig\Projects\Personal",
    "createdAt": ccs._iso_ms("2026-06-14T09:00:00.000Z"),
    "lastActivityAt": ccs._iso_ms("2026-06-14T10:08:00.000Z"),
    "lastFocusedAt": ccs._iso_ms("2026-06-14T10:08:00.000Z"),
    "completedTurns": 9,
    "model": "claude-fable-5",
    "effort": "xhigh",
    "isArchived": False,
    "alwaysAllowedReasons": [],
    "sessionPermissionUpdates": [],
    "spawnSeed": {},
    "chromePermissionMode": None,
    "permissionMode": "auto",
}
check("the row has exactly the expected fields, no more and no fewer",
      set(row) == set(EXPECTED),
      "extra=%s missing=%s" % (sorted(set(row) - set(EXPECTED)),
                               sorted(set(EXPECTED) - set(row))))
for k in sorted(EXPECTED):
    if k in row:
        check("  %s" % k, row[k] == EXPECTED[k], "%r != %r" % (row.get(k), EXPECTED[k]))

# Fields the policy excludes. The first four an earlier draft asserted on every
# row; measured across 987 real rows they appear on 60.2%, 5.6%, 2.7% and 0.9%.
# classifierSummaryEnabled is the interesting one: it CLEARS the 95% bar at
# 97.6% and is still omitted, because True is a behavioural setting rather than
# an absence - a threshold alone would have let it through.
for absent in ("reportFindingsCard", "chromeTabGroupId", "lastSpawnRootDetected",
               "remoteControlAutoEligible", "classifierSummaryEnabled"):
    check("  %s is omitted - the policy does not support it" % absent,
          absent not in row)
check("spawnedFrom is absent - the row claims no lineage it does not have",
      "spawnedFrom" not in row)
check("lastFocusedAt is seeded, not omitted - the app rewrites it on first focus",
      row["lastFocusedAt"] == row["lastActivityAt"])
check("the row serializes to JSON", isinstance(
    json.dumps(row, separators=(",", ":")), str))

# titleSource must be TRUE, not decorative. 533 of 537 real rows carrying it say
# 'auto'; only 4 say 'user'. Writing 'user' on a machine-made placeholder would
# tell the app - and the next reader of the file - that a person chose it.
auto = ccs._synthesize_row(SID, "(untitled - ...)", "auto", f, "v" * 36)
check("a derived title records titleSource 'auto', not 'user'",
      auto["titleSource"] == "auto", auto["titleSource"])

# An uncountable transcript omits completedTurns rather than writing 0.
unc = ccs._synthesize_row(SID, "A title", "user", dict(f, turns=None), "w" * 36)
check("an uncountable transcript omits completedTurns entirely",
      "completedTurns" not in unc, str(unc.get("completedTurns")))
shutil.rmtree(root, ignore_errors=True)

# ------------------------------------------------------------ titles
print("\n--- title derivation ---")

FACTS = {"cwd": r"C:\Users\craig\Projects\Personal", "created_ms": 0,
         "last_ms": ccs._iso_ms("2026-06-14T10:08:00.000Z"), "turns": 181,
         "custom_title": None, "path": "x", "model": "claude-fable-5",
         "effort": "xhigh"}
check("the placeholder names date, turns and the LAST path component",
      ccs._placeholder_title(FACTS) == "(untitled - 2026-06-14, 181 turns, Personal)",
      ccs._placeholder_title(FACTS))

# The date must be UTC, matching _iso_ms. With localtime, this exact fact dict
# renders 2026-06-14 in Seattle and 2026-06-15 in UTC+14 - so the assertion
# above would pass or fail depending on where the suite runs.
check("  and the date is UTC, so the title does not vary by machine timezone",
      "2026-06-14" in ccs._placeholder_title(
          dict(FACTS, last_ms=ccs._iso_ms("2026-06-14T23:30:00.000Z"))),
      ccs._placeholder_title(dict(FACTS, last_ms=ccs._iso_ms("2026-06-14T23:30:00.000Z"))))

ROOT_FACTS = dict(FACTS, cwd="C:\\")
check("a root cwd drops the component instead of dangling a comma",
      ccs._placeholder_title(ROOT_FACTS) == "(untitled - 2026-06-14, 181 turns)",
      ccs._placeholder_title(ROOT_FACTS))
check("  a POSIX root likewise",
      ccs._placeholder_title(dict(FACTS, cwd="/"))
      == "(untitled - 2026-06-14, 181 turns)",
      ccs._placeholder_title(dict(FACTS, cwd="/")))
check("  and a non-C drive letter is not mistaken for a folder name",
      ccs._placeholder_title(dict(FACTS, cwd="D:\\"))
      == "(untitled - 2026-06-14, 181 turns)",
      ccs._placeholder_title(dict(FACTS, cwd="D:\\")))
check("  while a UNC share IS a real leaf and is kept",
      ccs._placeholder_title(dict(FACTS, cwd="\\\\server\\share"))
      == "(untitled - 2026-06-14, 181 turns, share)",
      ccs._placeholder_title(dict(FACTS, cwd="\\\\server\\share")))

# The cwd comes out of a transcript, not off this filesystem. os.path.basename
# on a POSIX host finds no '/' in a Windows path and returns the whole thing,
# so this assertion is what keeps the suite honest when it runs on macOS.
check("a Windows cwd yields its leaf on ANY host, not the whole path",
      ccs._placeholder_title(FACTS).endswith("Personal)"),
      ccs._placeholder_title(FACTS))
check("  and a POSIX cwd works the same way",
      ccs._placeholder_title(dict(FACTS, cwd="/home/craig/Projects/Personal"))
      == "(untitled - 2026-06-14, 181 turns, Personal)",
      ccs._placeholder_title(dict(FACTS, cwd="/home/craig/Projects/Personal")))

check("an uncountable transcript says so rather than claiming a number",
      ccs._placeholder_title(dict(FACTS, turns=None))
      == "(untitled - 2026-06-14, turns not counted, Personal)",
      ccs._placeholder_title(dict(FACTS, turns=None)))

check("--title wins, is reported as yours, and records titleSource user",
      ccs._new_row_title("Mine", FACTS) == ("Mine", "yours", "user"))
check("customTitle is used when there is no --title, and records auto",
      ccs._new_row_title("", dict(FACTS, custom_title="Theirs"))
      == ("Theirs", "the transcript's custom title", "auto"))
check("a whitespace-only --title is treated as absent, not as a title",
      ccs._new_row_title("   ", FACTS)[2] == "auto",
      str(ccs._new_row_title("   ", FACTS)))
check("otherwise the placeholder, reported as such",
      ccs._new_row_title("", FACTS)[1] == "placeholder")

existing = {"Mine", "Mine (2)", "Theirs"}
check("a user-supplied duplicate is left alone",
      ccs._unique_title("Mine", existing, generated=False) == "Mine")
check("a generated duplicate is suffixed past every taken variant",
      ccs._unique_title("Mine", existing, generated=True) == "Mine (3)",
      ccs._unique_title("Mine", existing, generated=True))
check("  and an untaken generated title is untouched",
      ccs._unique_title("Fresh", existing, generated=True) == "Fresh")

# ------------------------------------------------------------ planning
print("\n--- plan_new_row ---")

root, env, live, dorm = build([OPENER] + prose(8))
m = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID, title="A title"))
check("the plan names the new-row op type", m["op_type"] == "new-row")
check("  targeting the signed-in store by default",
      os.path.realpath(m["store_path"]) == os.path.realpath(live), m["store_path"])
check("  and says HOW that store was chosen", bool(m.get("store_why")),
      str(m.get("store_why")))
check("  with exactly one row", len(m["rows"]) == 1)
check("  which is an ADD, not an update", m["rows"][0]["is_update"] is False)
check("  and carries no pre-image, because nothing existed",
      m["rows"][0]["pre_b64"] is None)
check("  the row filename matches the synthesized sessionId",
      json.loads(ccs.unb64(m["rows"][0]["post_b64"]).decode("utf-8"))["sessionId"]
      == "local_" + m["name"], m["name"])
check("  and nothing was written", not os.listdir(live))
shutil.rmtree(root, ignore_errors=True)

print("\n--- refusals ---")


def refusal(label, fn, needle):
    try:
        fn()
        check(label, False, "no refusal")
    except ccs.Refusal as exc:
        check(label, needle in str(exc), str(exc)[:90])


root, env, live, dorm = build([OPENER] + prose(8))
refusal("--to naming no transcript refuses",
        lambda: ccs.plan_new_row(env, ccs.NewRowFlags(to_session="%032d" % 99)),
        "no transcript on disk")
refusal("an empty --to refuses",
        lambda: ccs.plan_new_row(env, ccs.NewRowFlags(to_session="")),
        "--to is required")
with open(os.path.join(live, "local_existing.json"), "w") as fh:
    json.dump({"cliSessionId": SID, "title": "Already here", "cwd": "proj",
               "lastActivityAt": 1}, fh)
refusal("a conversation this account already reaches refuses",
        lambda: ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID)),
        "already opens")
shutil.rmtree(root, ignore_errors=True)

# An UNREADABLE row must fail closed. Skipping it means a row that already
# opens this conversation goes unseen and the command creates a duplicate -
# a fail-open in a module whose whole posture is fail-closed.
root, env, live, dorm = build([OPENER] + prose(8))
with open(os.path.join(live, "local_broken.json"), "w") as fh:
    fh.write("{not json at all")
refusal("an unreadable row in the store refuses rather than being skipped",
        lambda: ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID)),
        "could not be read")
shutil.rmtree(root, ignore_errors=True)

root, env, live, dorm = build([rec("user", "no cwd anywhere in this file at all")])
refusal("a transcript with no cwd refuses distinctly from unreadable",
        lambda: ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID)),
        "no cwd")
shutil.rmtree(root, ignore_errors=True)

# A conversation where nobody replied. Deliberately refused - model is on 100%
# of real rows and has no zero value - and the message must name THAT, not
# something vague, because it is the one refusal a user could reasonably
# disagree with.
root, env, live, dorm = build(
    [rec("user", "typed a prompt and closed the app before any reply",
         ts="2026-06-14T09:00:00.000Z", cwd=r"C:\Users\craig\Projects\Personal")])
refusal("a transcript with no assistant reply refuses, naming why",
        lambda: ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID)),
        "no assistant reply")
shutil.rmtree(root, ignore_errors=True)

# the cross-pair: one account, two org directories, only one of them real
root, env, live, dorm = build([OPENER] + prose(8))
os.makedirs(os.path.join(os.path.dirname(live), ORG_D))    # empty scaffolding
with open(os.path.join(live, "local_anything.json"), "w") as fh:
    json.dump({"cliSessionId": "%032d" % 55, "title": "Something else",
               "cwd": "proj", "lastActivityAt": 1}, fh)
# The ACCOUNT id matches both of that account's org dirs - which is exactly
# the tie _new_row_store exists to break. Deliberately not the account's
# email: _repoint_store matches email through account_email, whose docstring
# says it resolves a DORMANT account, so the LIVE account's own email (which
# lives in ~/.claude.json and only live_account reads) matches nothing. That
# is a real gap in shipped `repoint`, filed separately - do not paper over it
# by teaching this test to use whichever identifier happens to work.
m = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID, store=LIVE))
check("an account id matching two org dirs picks the one holding rows",
      os.path.realpath(m["store_path"]) == os.path.realpath(live), m["store_path"])
check("  and says so, because a heuristic the user cannot see is a trap",
      "rows" in m["store_why"], m["store_why"])
check("  marking the choice as a guess", m["store_is_a_guess"] is True)
check("  and naming the org that would settle it", m["store_org"] == ORG_L,
      str(m.get("store_org")))
# the ORG id narrows to one directory, so no tie and no guess
m2 = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID, store=ORG_L))
check("  while naming the org id is not a guess",
      m2["store_is_a_guess"] is False)
check("  and planning wrote nothing either way",
      sorted(os.listdir(live)) == ["local_anything.json"], str(os.listdir(live)))
shutil.rmtree(root, ignore_errors=True)

# NOTE: that the guess may plan and may NOT write is asserted in Task 6, where
# `cmd_new_row` exists to enforce it. Do not test it here - `cmd_new_row` and
# `run_new_row` are introduced in Tasks 6 and 4, and calling them from this
# task's suite would raise AttributeError rather than fail a check.

# every candidate empty: name them rather than saying 'no way to tell'
root, env, live, dorm = build([OPENER] + prose(8))
os.makedirs(os.path.join(os.path.dirname(live), ORG_D))
try:
    ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID, store=LIVE))
    check("all-empty candidates refuse by naming them", False, "no refusal")
except ccs.Refusal as exc:
    check("all-empty candidates refuse by naming them", ORG_D in str(exc),
          str(exc)[:130])
    check("  naming ORG IDS, not paths - a Windows path pasted back would not "
          "match", "organization ids" in str(exc), str(exc)[:130])
shutil.rmtree(root, ignore_errors=True)

# zero prose turns is ALLOWED - a policy choice must not masquerade as a
# technical constraint. completedTurns takes 0 and the placeholder renders it.
root, env, live, dorm = build(
    [rec("system", None, ts="2026-06-14T09:00:00.000Z",
         cwd=r"C:\Users\craig\Projects\Personal", model="claude-fable-5")])
m = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID))
check("a transcript with zero prose turns is allowed", m["turns"] == 0)
check("  and its placeholder says so", "0 turns" in m["title"], m["title"])
shutil.rmtree(root, ignore_errors=True)

# ------------------------------------------------------------ apply and undo
print("\n--- apply, and undo by deletion ---")

root, env, live, dorm = build([OPENER] + prose(8))
m = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID, title="Recovered"))
check("apply completes", ccs.run_new_row(env, m) == "completed")
path = m["rows"][0]["dest_path"]
check("  the row exists on disk", os.path.exists(path))
written = json.load(open(path, encoding="utf-8"))
check("  opening the right conversation", written["cliSessionId"] == SID)
check("  under the title the plan showed", written["title"] == "Recovered")
check("  and the manifest carries an op_id for undo --id", bool(m.get("op_id")))
check("  the lock was released", not os.path.exists(ccs._lock_path(env)))

op = [o for o in ccs.list_ops(env) if o.manifest.get("op_id") == m["op_id"]][0]
check("undo deletes the row", ccs.undo_new_row(env, op) == "undone")
check("  the file is gone", not os.path.exists(path))
check("  and the transcript is untouched", os.path.exists(m["transcript"]))
shutil.rmtree(root, ignore_errors=True)

# The TOCTOU the lock exists to close: plan runs unlocked, so another writer
# can create a row for this conversation between plan and apply.
root, env, live, dorm = build([OPENER] + prose(8))
m = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID, title="Recovered"))
with open(os.path.join(live, "local_sneaked.json"), "w") as fh:
    json.dump({"cliSessionId": SID, "title": "Got there first", "cwd": "proj",
               "lastActivityAt": 1}, fh)
refusal("apply re-checks reachability under the lock and refuses",
        lambda: ccs.run_new_row(env, m), "already opens")
check("  writing nothing", not os.path.exists(m["rows"][0]["dest_path"]))
check("  and releasing the lock", not os.path.exists(ccs._lock_path(env)))
shutil.rmtree(root, ignore_errors=True)

# The transcript must still be the SAME single transcript, not merely some
# transcript: a duplicate appearing in another project folder between plan and
# apply would make the planned row's facts describe a file it no longer names.
root, env, live, dorm = build([OPENER] + prose(8))
m = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID, title="Recovered"))
other = os.path.join(env.projects_root, "other")
os.makedirs(other)
shutil.copy(m["transcript"], os.path.join(other, SID + ".jsonl"))
refusal("apply refuses when the transcript is no longer uniquely located",
        lambda: ccs.run_new_row(env, m), "more than one")
shutil.rmtree(root, ignore_errors=True)

# The transcript goes AFTER planning, so the refusal has to come from the
# preflight rather than from plan time. Note the needle: _transcript_facts says
# "no transcript on disk" and _new_row_preflight says "no LONGER on disk". An
# earlier version of this test built a second plan inside the lambda, so
# plan_new_row raised before run_new_row was ever entered - it asserted the
# plan-time message under an apply-time label, and the preflight's own
# not-found arm had no coverage at all.
root, env, live, dorm = build([OPENER] + prose(8))
m = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID, title="Recovered"))
os.unlink(m["transcript"])
refusal("apply refuses once the transcript is gone",
        lambda: ccs.run_new_row(env, m), "no longer on disk")
check("  writing nothing", not os.path.exists(m["rows"][0]["dest_path"]))
check("  and leaving no unresolved operation", not ccs.nonterminal_ops(env))
shutil.rmtree(root, ignore_errors=True)

# Same path, different bytes. Every fact in the post-image came out of this
# file, so an append between plan and apply leaves the row's timestamps and
# turn count describing a version that no longer exists. A path-only check
# cannot see this.
root, env, live, dorm = build([OPENER] + prose(8))
m = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID, title="Recovered"))
with open(m["transcript"], "a", encoding="utf-8") as fh:
    fh.write(rec("user", "a message appended after the plan was built",
                 ts="2026-06-14T11:00:00.000Z") + "\n")
os.utime(m["transcript"], (m["transcript_mtime"] + 60,
                           m["transcript_mtime"] + 60))
refusal("apply refuses when the transcript changed at the SAME path",
        lambda: ccs.run_new_row(env, m), "has changed since this was planned")
check("  writing nothing", not os.path.exists(m["rows"][0]["dest_path"]))
shutil.rmtree(root, ignore_errors=True)

# A safe refusal must not manufacture cleanup work. Journalling before the
# preflight left a non-terminal op behind, so doctor told the user to run
# 'recover' over a command that had touched nothing at all.
root, env, live, dorm = build([OPENER] + prose(8))
m = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID, title="Recovered"))
with open(os.path.join(live, "local_sneaked2.json"), "w") as fh:
    json.dump({"cliSessionId": SID, "title": "Got there first", "cwd": "proj",
               "lastActivityAt": 1}, fh)
refusal("a pre-write refusal still refuses",
        lambda: ccs.run_new_row(env, m), "already opens")
check("  and leaves NO unresolved operation behind",
      not ccs.nonterminal_ops(env), str([o.manifest.get("op_id")
                                         for o in ccs.nonterminal_ops(env)]))
check("  so doctor does not ask for a recover that has nothing to do",
      not ccs.gather_doctor(env)["nonterminal_ops"])
shutil.rmtree(root, ignore_errors=True)

# The generated title was suffixed past everything that existed at PLAN time.
# A row created since can hold that suffix, and the preflight re-reads the
# title set rather than discarding it.
root, env, live, dorm = build(
    [rec("user", "opening message long enough to count",
         ts="2026-06-14T09:00:00.000Z", cwd=r"C:\Users\craig\Projects\Personal",
         custom="Shared name")] + prose(8))
m = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID))
check("the generated title was taken from the transcript", m["title"] == "Shared name")
with open(os.path.join(live, "local_racer.json"), "w") as fh:
    json.dump({"cliSessionId": "%032d" % 88, "title": "Shared name",
               "cwd": "proj", "lastActivityAt": 1}, fh)
refusal("a generated title claimed since planning refuses under the lock",
        lambda: ccs.run_new_row(env, m), "chosen to be unique")
shutil.rmtree(root, ignore_errors=True)

# An op must never collide with ITSELF. After a crash the row can already be on
# disk when the preflight runs again, and both of its checks - reachability and
# title uniqueness - would otherwise fire against the very row this op wrote.
# The reachability half was exempted from the start; the title half was not, so
# `recover --forward` on a written-then-drifted row refused with "another row is
# now called X - it appeared since this was planned" about its own row.
root, env, live, dorm = build(
    [rec("user", "opening message long enough to count",
         ts="2026-06-14T09:00:00.000Z", cwd=r"C:\Users\craig\Projects\Personal",
         custom="Self collision")] + prose(8))
m = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID))
ccs.run_new_row(env, m)
d = json.load(open(m["rows"][0]["dest_path"], encoding="utf-8"))
d["lastFocusedAt"] = 4242                      # as the app does on first focus
with open(m["rows"][0]["dest_path"], "w") as fh:
    json.dump(d, fh)
try:
    ccs._new_row_preflight(env, m)
    check("the preflight does not refuse against the op's own row", True)
except ccs.Refusal as exc:
    check("the preflight does not refuse against the op's own row", False,
          str(exc)[:100])
# and the exclusion is surgical - a DIFFERENT row with that title still counts
with open(os.path.join(live, "local_other.json"), "w") as fh:
    json.dump({"cliSessionId": "%032d" % 77, "title": "Self collision",
               "cwd": "proj", "lastActivityAt": 1}, fh)
refusal("  while a different row taking the title still refuses",
        lambda: ccs._new_row_preflight(env, m), "chosen to be unique")
shutil.rmtree(root, ignore_errors=True)

# ...but an explicit --title duplicate was the user's own call and still writes.
root, env, live, dorm = build([OPENER] + prose(8))
m = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID, title="Mine"))
with open(os.path.join(live, "local_racer.json"), "w") as fh:
    json.dump({"cliSessionId": "%032d" % 88, "title": "Mine", "cwd": "proj",
               "lastActivityAt": 1}, fh)
check("an explicit --title duplicate is still written - the user asked for it",
      ccs.run_new_row(env, m) == "completed")
shutil.rmtree(root, ignore_errors=True)

# undo refuses once the row has drifted - the app rewrites rows it opens
root, env, live, dorm = build([OPENER] + prose(8))
m = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID, title="Recovered"))
ccs.run_new_row(env, m)
path = m["rows"][0]["dest_path"]
d = json.load(open(path, encoding="utf-8"))
d["lastFocusedAt"] = 999                      # as the app does on first focus
with open(path, "w") as fh:
    json.dump(d, fh)
op = [o for o in ccs.list_ops(env) if o.manifest.get("op_id") == m["op_id"]][0]
refusal("undo refuses a row the app has touched",
        lambda: ccs.undo_new_row(env, op), "no longer holds")
check("  and leaves it in place", os.path.exists(path))
shutil.rmtree(root, ignore_errors=True)

root, env, live, dorm = build([OPENER] + prose(8))
m = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID, title="Recovered"))
ccs.run_new_row(env, m)
os.unlink(m["rows"][0]["dest_path"])
op = [o for o in ccs.list_ops(env) if o.manifest.get("op_id") == m["op_id"]][0]
refusal("undo reports an already-absent row distinctly from drift",
        lambda: ccs.undo_new_row(env, op), "already gone")
shutil.rmtree(root, ignore_errors=True)

# ------------------------------------------------------------ recovery
print("\n--- a crash after the write, before the journal marker ---")


def crashed():
    root, env, live, dorm = build([OPENER] + prose(8))
    m = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID, title="Recovered"))
    fired = {"n": 0}

    def crash_once(point):
        if point == "new-row-write-before-save" and fired["n"] == 0:
            fired["n"] += 1
            raise KeyboardInterrupt("killed after the write, before the marker")

    ccs._crash_hook = crash_once
    try:
        ccs.run_new_row(env, m)
    except BaseException:                      # noqa: BLE001 - the simulated kill
        pass
    finally:
        ccs._crash_hook = None
    return root, env, m


root, env, m = crashed()
pending = ccs.nonterminal_ops(env)
check("the op is left non-terminal", len(pending) == 1)
check("  with the row already on disk - the torn state",
      os.path.exists(m["rows"][0]["dest_path"]))
c = ccs.classify_op(env, pending[0])
check("  classify_op knows the new-row shape rather than falling through",
      c["status"] in ("journaled", "writing"), c["status"])
check("  and offers both directions",
      sorted(c["resolutions"]) == ["back", "forward"], str(c["resolutions"]))
check("  with a note naming the command", "new-row" in c["note"], c["note"][:70])
shutil.rmtree(root, ignore_errors=True)

root, env, m = crashed()
check("recover --forward completes",
      ccs.recover_op(env, ccs.nonterminal_ops(env)[0], "forward") == "completed")
check("  and the row is on disk", os.path.exists(m["rows"][0]["dest_path"]))
shutil.rmtree(root, ignore_errors=True)

# The write already landed, and the transcript has since gone. recover MUST
# still finish the bookkeeping: the row's facts were consumed at plan time and
# are already committed to the bytes on disk, so re-validating the source of an
# answer that is already written would make the one command whose job is to
# unstick the user refuse on a write that succeeded.
root, env, m = crashed()
os.unlink(m["transcript"])
check("recover --forward finishes an already-written row even with the "
      "transcript gone",
      ccs.recover_op(env, ccs.nonterminal_ops(env)[0], "forward") == "completed")
check("  leaving the row in place", os.path.exists(m["rows"][0]["dest_path"]))
check("  and nothing pending", not ccs.nonterminal_ops(env))
shutil.rmtree(root, ignore_errors=True)

# ...but a row that did NOT land still gets the full preflight on the way
# forward, because nothing has been committed and the facts must still hold.
root, env, m = crashed()
os.unlink(m["rows"][0]["dest_path"])
os.unlink(m["transcript"])
refusal("recover --forward on an unwritten row still refuses a gone transcript",
        lambda: ccs.recover_op(env, ccs.nonterminal_ops(env)[0], "forward"),
        "no longer on disk")
shutil.rmtree(root, ignore_errors=True)

# classify_op's note is what the user reads to choose a direction. A drifted
# row must not be described as never written.
root, env, m = crashed()
d = json.load(open(m["rows"][0]["dest_path"], encoding="utf-8"))
d["lastFocusedAt"] = 4242
with open(m["rows"][0]["dest_path"], "w") as fh:
    json.dump(d, fh)
note = ccs.classify_op(env, ccs.nonterminal_ops(env)[0])["note"]
check("a drifted row is NOT reported as 'was not written'",
      "was not written" not in note, note)
check("  it is reported as written and since changed",
      "since changed" in note, note)
# And forcing forward on it must say something TRUE. "a different row already
# exists ... nothing was written" is false twice over: it is our row, and we
# did write it.
try:
    ccs.recover_op(env, ccs.nonterminal_ops(env)[0], "forward")
    check("  forcing forward on a drifted row refuses", False, "it did not")
except ccs.Refusal as exc:
    check("  forcing forward on a drifted row refuses", True)
    check("    without claiming nothing was written",
          "Nothing was written" not in str(exc), str(exc)[:100])
    check("    and names the app as the likely cause",
          "the app" in str(exc), str(exc)[:100])
shutil.rmtree(root, ignore_errors=True)

root, env, m = crashed()
check("recover --back closes the op",
      ccs.recover_op(env, ccs.nonterminal_ops(env)[0], "back") == "rolled_back")
check("  removing the row it had written",
      not os.path.exists(m["rows"][0]["dest_path"]))
check("  and nothing is left pending", not ccs.nonterminal_ops(env))
shutil.rmtree(root, ignore_errors=True)

# A crash BEFORE the write: nothing on disk, and 'back' must still terminate.
root, env, live, dorm = build([OPENER] + prose(8))
m = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID, title="Recovered"))
fired = {"n": 0}


def crash_early(point):
    if point == "new-row-write-before-save" and fired["n"] == 0:
        fired["n"] += 1
        raise KeyboardInterrupt("killed")


ccs._crash_hook = crash_early
try:
    ccs.run_new_row(env, m)
except BaseException:                          # noqa: BLE001
    pass
finally:
    ccs._crash_hook = None
os.unlink(m["rows"][0]["dest_path"])           # simulate the no-row-yet state
check("recover --back terminates even with no row on disk",
      ccs.recover_op(env, ccs.nonterminal_ops(env)[0], "back") == "rolled_back")
check("  and nothing is left pending", not ccs.nonterminal_ops(env))
shutil.rmtree(root, ignore_errors=True)

# RULING 4 on the recovery path. recover_op calls _guard_mutation for no op
# type at all, so before this fix, crashing, reopening the app, and then
# recovering backward would delete a row out from under a running app that is
# reading and rewriting that same store.
DESKTOP = (1, r"c:\program files\windowsapps"
              r"\claude_1.0_x64__pzs8sxrjxfjjc\app\claude.exe")

root, env, m = crashed()
env.process_lister = lambda: [DESKTOP]
refusal("recover --back refuses while the desktop app is running",
        lambda: ccs.recover_op(env, ccs.nonterminal_ops(env)[0], "back"),
        "desktop app appears to be running")
check("  leaving the row alone", os.path.exists(m["rows"][0]["dest_path"]))
check("  and the op still recoverable once the app is closed",
      len(ccs.nonterminal_ops(env)) == 1)
env.process_lister = lambda: []
check("  which it then is",
      ccs.recover_op(env, ccs.nonterminal_ops(env)[0], "back") == "rolled_back")
shutil.rmtree(root, ignore_errors=True)

# Forward is asymmetric, and deliberately so. On an ALREADY-WRITTEN row it
# touches only the journal - no store bytes change - so refusing it while the
# app runs would block harmless bookkeeping and leave the user stuck for no
# safety gain. On an UNWRITTEN row it is about to write the store, so the guard
# applies. The split falls out of _new_row_preflight running only in the second
# case; these two checks pin that it is real and not incidental.
root, env, m = crashed()
env.process_lister = lambda: [DESKTOP]
check("recover --forward on an already-written row is journal-only, so it "
      "completes even with the app running",
      ccs.recover_op(env, ccs.nonterminal_ops(env)[0], "forward") == "completed")
shutil.rmtree(root, ignore_errors=True)

root, env, m = crashed()
os.unlink(m["rows"][0]["dest_path"])           # nothing landed
env.process_lister = lambda: [DESKTOP]
refusal("recover --forward on an UNwritten row refuses while the app runs",
        lambda: ccs.recover_op(env, ccs.nonterminal_ops(env)[0], "forward"),
        "desktop app appears to be running")
shutil.rmtree(root, ignore_errors=True)

# A drifted row is NOT deleted by 'back', and 'back' must say so rather than
# reporting a clean rollback that left a row behind.
root, env, m = crashed()
d = json.load(open(m["rows"][0]["dest_path"], encoding="utf-8"))
d["lastFocusedAt"] = 999
with open(m["rows"][0]["dest_path"], "w") as fh:
    json.dump(d, fh)
res = ccs.recover_op(env, ccs.nonterminal_ops(env)[0], "back")
check("back closes an op whose row drifted", res == "rolled_back")
check("  leaving the drifted row alone", os.path.exists(m["rows"][0]["dest_path"]))
left = [o for o in ccs.list_ops(env) if o.manifest.get("op_id") == m["op_id"]][0]
check("  and RECORDING that it left something behind",
      bool(left.manifest.get("rollback_residue")),
      str(left.manifest.get("rollback_residue")))
shutil.rmtree(root, ignore_errors=True)

# ...and PRINTING it. A residue nobody prints is a residue nobody acts on, and
# the storing half is what the assertion above already covers.
#
# DEVIATION FROM THE BRIEF: the brief's version of this check called
# ccs.recover_op(..., "back") directly (as above) and THEN called cmd_recover
# with the same op_id, expecting it to print the residue on that second call.
# It cannot: "rolled_back" is a TERMINAL status (ccs.TERMINAL), and
# cmd_recover's --id lookup filters through nonterminal_ops(env) - the same
# filter the bare listing form uses - so the instant recover_op resolves the
# op, cmd_recover can no longer see it by id at all. It raises "no unresolved
# op with id ..." before ever reaching the print. Probed directly: reusing the
# brief's exact call sequence raises that Refusal every time, on this build
# and presumably any. The only point cmd_recover ever HOLDS this manifest
# nonterminal is during its own call to recover_op - so proving cmd_recover
# prints the residue means letting cmd_recover perform the resolution itself
# (back=True, apply=True) rather than resolving it beforehand.
root, env, m = crashed()
d = json.load(open(m["rows"][0]["dest_path"], encoding="utf-8"))
d["lastFocusedAt"] = 4343
with open(m["rows"][0]["dest_path"], "w") as fh:
    json.dump(d, fh)
out = []
real_bp2 = builtins.print
builtins.print = lambda *a, **k: out.append(" ".join(str(x) for x in a))
try:
    ccs.cmd_recover(env, _Ns(op_id=m["op_id"], forward=False, back=True,
                             apply=True, verbose=True))
finally:
    builtins.print = real_bp2
check("  and cmd_recover PRINTS the residue",
      any("left something behind" in line for line in out), str(out)[:120])
shutil.rmtree(root, ignore_errors=True)

# The headline safety property of this arm - containment before unlink - and
# the two other residue paths. All three were unprotected: the commit message
# led with containment and nothing exercised it.
root, env, m = crashed()
op = ccs.nonterminal_ops(env)[0]
outside = os.path.join(os.path.dirname(os.path.dirname(m["store_path"])),
                       "escaped.json")
# DEVIATION FROM THE BRIEF: the brief wrote literal b"{}" at `outside`. That
# never matches post_b64 (a full session row), so _sync_row_drift classifies
# the redirected dest_path as "drifted", not "match" - and the containment
# check this test exists to exercise sits ONLY inside the "match" branch (the
# same branch that guards and unlinks; see Finding 4's asymmetry: back never
# touches a row that isn't a byte-exact match for what it wrote). Probed
# directly: with b"{}" the residue comes back "left ... in place - it no
# longer holds what this op wrote (drifted)", never reaching ensure_contained
# at all, so "could not remove" never appears - failing the very check this
# block is for. Writing the REAL post_b64 bytes at the escaped path is what
# makes the row "match" so recover_op actually attempts the delete, hits
# ensure_contained, and records the containment failure.
with open(outside, "wb") as fh:
    fh.write(ccs.unb64(op.manifest["rows"][0]["post_b64"]))
op.manifest["rows"][0]["dest_path"] = outside          # as a corrupt journal would
ccs.save_manifest(op)
check("back closes even when the row path escapes the store",
      ccs.recover_op(env, ccs.nonterminal_ops(env)[0], "back") == "rolled_back")
check("  WITHOUT deleting the outside file", os.path.exists(outside))
left = [o for o in ccs.list_ops(env) if o.manifest.get("op_id") == m["op_id"]][0]
check("  recording that it could not remove it",
      "could not remove" in (left.manifest.get("rollback_residue") or ""),
      str(left.manifest.get("rollback_residue"))[:90])
shutil.rmtree(root, ignore_errors=True)

# An unlink that fails for an ordinary OS reason still terminates, still records
root, env, m = crashed()
real_unlink = os.unlink


def refuse_unlink(path, *a, **k):
    if os.path.abspath(path) == os.path.abspath(m["rows"][0]["dest_path"]):
        raise OSError(13, "Permission denied")
    return real_unlink(path, *a, **k)


os.unlink = refuse_unlink
try:
    res = ccs.recover_op(env, ccs.nonterminal_ops(env)[0], "back")
finally:
    os.unlink = real_unlink
check("back terminates when the unlink itself fails", res == "rolled_back")
left = [o for o in ccs.list_ops(env) if o.manifest.get("op_id") == m["op_id"]][0]
check("  recording the failure rather than reporting a clean rollback",
      "could not remove" in (left.manifest.get("rollback_residue") or ""),
      str(left.manifest.get("rollback_residue"))[:90])
shutil.rmtree(root, ignore_errors=True)

# A damaged journal must not traceback out of the one command that unsticks you.
root, env, m = crashed()
op = ccs.nonterminal_ops(env)[0]
op.manifest["rows"] = []
ccs.save_manifest(op)
c = ccs.classify_op(env, ccs.nonterminal_ops(env)[0])
check("classify_op survives a damaged record instead of raising",
      "damaged" in c["note"], c["note"][:80])
check("  and offers no direction it cannot run", c["resolutions"] == [])
for d in ("forward", "back"):
    refusal("  recover --%s refuses it as a Refusal, not a traceback" % d,
            lambda d=d: ccs.recover_op(env, ccs.nonterminal_ops(env)[0], d),
            "is damaged")
shutil.rmtree(root, ignore_errors=True)

# 'back' on a row there is nothing to delete is journal-only, so a running app
# must not block it - the same asymmetry the forward arm has. Otherwise the ONLY
# exit from a stuck operation sits behind closing the desktop app, for an
# operation that touched no bytes.
root, env, m = crashed()
os.unlink(m["rows"][0]["dest_path"])           # nothing left to remove
env.process_lister = lambda: [DESKTOP]
check("back on an absent row closes the op even with the app running",
      ccs.recover_op(env, ccs.nonterminal_ops(env)[0], "back") == "rolled_back")
check("  leaving nothing pending", not ccs.nonterminal_ops(env))
shutil.rmtree(root, ignore_errors=True)

# The residue must survive rotation - it is destroyed in the same call that
# writes it unless _collides holds the op back.
root, env, m = crashed()
d = json.load(open(m["rows"][0]["dest_path"], encoding="utf-8"))
d["lastFocusedAt"] = 1234
with open(m["rows"][0]["dest_path"], "w") as fh:
    json.dump(d, fh)
ccs.recover_op(env, ccs.nonterminal_ops(env)[0], "back")
for _ in range(12):                            # push it out of the newest ten
    ccs.rotate_ops(env)
    other = ccs.new_op(env, {"op_type": "new-row", "status": "rolled_back",
                             "store_path": m["store_path"], "rows": []})
    ccs.set_status(other, "rolled_back")
ccs.rotate_ops(env)
survivor = [o for o in ccs.list_ops(env) if o.manifest.get("op_id") == m["op_id"]]
check("the residue-bearing op survives rotation", len(survivor) == 1,
      "pruned - the only record that a row was left behind is gone")
shutil.rmtree(root, ignore_errors=True)

# ------------------------------------------------------------ report and json
print("\n--- the report, and --json ---")

root, env, live, dorm = build(
    [rec("user", "opening message long enough to count",
         ts="2026-06-14T09:00:00.000Z", cwd=r"C:\Users\craig\Projects\Personal",
         custom="Their own title")] + prose(8))
m = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID))
out = []
ccs._print_new_row_report(out.append, m)
joined = "\n".join(out)
check("the report names the title", "Their own title" in joined)
check("  AND where it came from", "custom title" in joined, joined[:120])
check("  the conversation it will open", SID[:8] in joined)
check("  which store, and why that one", m["store_why"] in joined, joined[:200])
check("  and says the row is new rather than moved",
      "creates a NEW" in joined or "new sidebar row" in joined, joined[:200])
check("  without claiming nothing else points at the conversation",
      "nothing pointing at it" not in joined, joined[:200])

pub = ccs._public_new_row_manifest(m)
blob = json.dumps(pub)
check("--json carries no row image", "post_b64" not in blob)
check("  nor a pre-image key", "pre_b64" not in blob)
check("  while still naming the op and the target",
      pub["op_type"] == "new-row" and pub["to_session"] == SID)
shutil.rmtree(root, ignore_errors=True)

# The store choice must be VISIBLE BEFORE the write, not after it. There is no
# way to hand a saved dry run back to the CLI, so a separate dry run replans
# and proves nothing about what the apply will pick - which makes "the user
# sees the heuristic first" false unless --apply itself prints first.
print("\n--- the report prints before the write ---")


root, env, live, dorm = build([OPENER] + prose(8))
order = []
real_bp = builtins.print
real_run = ccs.run_new_row


def spy_print(*a, **k):
    order.append(("print", " ".join(str(x) for x in a)))


def spy_run(e, mm):
    order.append(("write", mm["name"]))
    return real_run(e, mm)


builtins.print = spy_print
ccs.run_new_row = spy_run
try:
    ccs.cmd_new_row(env, _Ns(to_session=SID, store="", title="Recovered",
                             live="", apply=True, json=False, verbose=False))
finally:
    builtins.print = real_bp
    ccs.run_new_row = real_run

kinds = [k for k, _ in order]
check("the write happens at all", "write" in kinds)
check("  and the report was printed BEFORE it",
      kinds.index("print") < kinds.index("write"), str(kinds[:4]))
check("  including the store's reasoning",
      any(k == "print" and "chosen as" in v
          for k, v in order[:kinds.index("write")]),
      str([v for k, v in order[:kinds.index("write")]][:6]))
shutil.rmtree(root, ignore_errors=True)

# A guessed store may PLAN and may not WRITE. Task 3 asserts that planning
# marks the guess; this is where the refusal itself lives, because cmd_new_row
# is what enforces it and it does not exist before this task.
print("\n--- a guessed store may plan, and may not write ---")

root, env, live, dorm = build([OPENER] + prose(8))
os.makedirs(os.path.join(os.path.dirname(live), ORG_D))    # empty scaffolding
with open(os.path.join(live, "local_anything.json"), "w") as fh:
    json.dump({"cliSessionId": "%032d" % 55, "title": "Something else",
               "cwd": "proj", "lastActivityAt": 1}, fh)
refusal("--apply refuses when row counts chose the store",
        lambda: ccs.cmd_new_row(env, _Ns(to_session=SID, store=LIVE,
                                         title="", live="", apply=True,
                                         json=False, verbose=False)),
        "decided by counting rows")
check("  and writes nothing",
      sorted(os.listdir(live)) == ["local_anything.json"], str(os.listdir(live)))
check("  while a dry run on the same guess is allowed",
      ccs.cmd_new_row(env, _Ns(to_session=SID, store=LIVE, title="", live="",
                               apply=False, json=False, verbose=False)) == 0)
# The refusal must name something that WORKS when pasted back. Naming the org
# id does; naming the path does not, because _repoint_store forward-slashes the
# candidate but not the user's argument.
check("naming the org id the refusal suggested lets --apply through",
      ccs.cmd_new_row(env, _Ns(to_session=SID, store=ORG_L, title="Recovered",
                               live="", apply=True, json=False,
                               verbose=False)) == 0)
check("  and the row is on disk",
      any(n != "local_anything.json" for n in os.listdir(live)),
      str(os.listdir(live)))
shutil.rmtree(root, ignore_errors=True)

# The command's own closing line tells the user "'undo' removes the row again".
# cmd_undo filters candidates by op_type, so until new-row is in that tuple the
# promise is false in the worst way: undo silently selects an older unrelated
# operation and reverses THAT, or refuses when none exists. undo_new_row was
# fully built and tested in Task 4 and reachable from nothing.
print("\n--- undo actually reaches a new-row op ---")

root, env, live, dorm = build([OPENER] + prose(8))
m = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID, title="Undo me"))
ccs.run_new_row(env, m)
path = m["rows"][0]["dest_path"]
out = []
real_bp3 = builtins.print
builtins.print = lambda *a, **k: out.append(" ".join(str(x) for x in a))
try:
    rc = ccs.cmd_undo(env, _Ns(op_id="", apply=False, verbose=True, show=False))
finally:
    builtins.print = real_bp3
check("undo's dry run SEES the new-row op", rc == 0 and any(
    m["op_id"] in line for line in out), str(out)[:110])
check("  describing it as a new-row, not as 'session None'",
      any("new-row" in line for line in out) and not any("session None" in line
                                                         for line in out),
      str(out)[:110])
out = []
builtins.print = lambda *a, **k: out.append(" ".join(str(x) for x in a))
try:
    ccs.cmd_undo(env, _Ns(op_id="", apply=True, verbose=True, show=False))
finally:
    builtins.print = real_bp3
check("  and --apply removes the row", not os.path.exists(path), str(out)[:110])
shutil.rmtree(root, ignore_errors=True)

# ------------------------------------------------------------ doctor detection
print("\n--- doctor notices a synthesized row that vanished ---")

root, env, live, dorm = build([OPENER] + prose(8))
m = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID, title="Recovered"))
ccs.run_new_row(env, m)
d = ccs.gather_doctor(env)
check("a row that is still there is not reported",
      not d.get("vanished_new_rows"), str(d.get("vanished_new_rows")))
os.unlink(m["rows"][0]["dest_path"])           # as a tombstoning app would
d = ccs.gather_doctor(env)
check("a vanished synthesized row IS reported", len(d["vanished_new_rows"]) == 1)
v = d["vanished_new_rows"][0]
check("  naming the op that created it", v["op_id"] == m["op_id"])
check("  and the conversation it opened", v["to_session"] == SID)
check("  having actually counted the transcripts rather than assuming",
      v["transcript_count"] == 1, str(v["transcript_count"]))
check("  and doctor's exit code reflects the anomaly", d["exit_code"] != 0,
      str(d["exit_code"]))

# Two project folders: the recreate advice would refuse, so doctor must not
# give it. bool() on the find_transcripts list could not tell these apart.
other = os.path.join(env.projects_root, "other")
os.makedirs(other)
shutil.copy(m["transcript"], os.path.join(other, SID + ".jsonl"))
d = ccs.gather_doctor(env)
check("  an ambiguous transcript is counted, not just called present",
      d["vanished_new_rows"][0]["transcript_count"] == 2,
      str(d["vanished_new_rows"][0]["transcript_count"]))
shutil.rmtree(other, ignore_errors=True)

os.unlink(m["transcript"])
d = ccs.gather_doctor(env)
check("  a vanished row whose transcript ALSO went says so",
      d["vanished_new_rows"][0]["transcript_count"] == 0)
shutil.rmtree(root, ignore_errors=True)

# doctor must SURVIVE the conditions it exists to report. _row_already_opens
# fails closed and raises on a row it cannot parse - correct for a command that
# is about to write, wrong for a diagnostic, which has to produce a report
# precisely when the store is in a bad way. The try/except around that call had
# no test: delete it and every suite still passed, while a store holding one
# malformed row aborted the whole report.
root, env, live, dorm = build([OPENER] + prose(8))
m = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID, title="Recovered"))
ccs.run_new_row(env, m)
os.unlink(m["rows"][0]["dest_path"])           # the row vanishes...
with open(os.path.join(live, "local_broken.json"), "w") as fh:
    fh.write("{not json at all")               # ...and the store is unreadable
try:
    d = ccs.gather_doctor(env)
    check("doctor still produces a report when a store row is unparseable", True)
    check("  and still reports the vanished row",
          len(d.get("vanished_new_rows") or []) == 1,
          str(d.get("vanished_new_rows")))
    check("  with a non-zero exit code", d["exit_code"] != 0, str(d["exit_code"]))
except BaseException as exc:                   # noqa: BLE001 - that is the bug
    check("doctor still produces a report when a store row is unparseable",
          False, "%s: %s" % (type(exc).__name__, str(exc)[:70]))
    check("  and still reports the vanished row", False, "no report")
    check("  with a non-zero exit code", False, "no report")
shutil.rmtree(root, ignore_errors=True)

# The same, one layer out: the store directory itself is gone.
root, env, live, dorm = build([OPENER] + prose(8))
m = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID, title="Recovered"))
ccs.run_new_row(env, m)
shutil.rmtree(m["store_path"], ignore_errors=True)
try:
    d = ccs.gather_doctor(env)
    check("doctor survives the whole store directory vanishing", True)
    check("  reporting the row as vanished",
          len(d.get("vanished_new_rows") or []) == 1,
          str(d.get("vanished_new_rows")))
except BaseException as exc:                   # noqa: BLE001
    check("doctor survives the whole store directory vanishing", False,
          "%s: %s" % (type(exc).__name__, str(exc)[:70]))
    check("  reporting the row as vanished", False, "no report")
shutil.rmtree(root, ignore_errors=True)

# Taking doctor's OWN advice must clear doctor. Recreating mints a fresh uuid,
# so the original path stays absent forever - reporting on that path alone
# would leave a permanent alert whose suggested fix then refuses, because a row
# already opens the session. The question is reachability, not one path.
root, env, live, dorm = build([OPENER] + prose(8))
m = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID, title="Recovered"))
ccs.run_new_row(env, m)
os.unlink(m["rows"][0]["dest_path"])
check("doctor reports the vanished row", len(ccs.gather_doctor(env)["vanished_new_rows"]) == 1)
m2 = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID, title="Recovered again"))
check("  recreating it mints a DIFFERENT filename",
      m2["rows"][0]["name"] != m["rows"][0]["name"])
ccs.run_new_row(env, m2)
d = ccs.gather_doctor(env)
check("  and the alert clears, because the account can open it again",
      not d["vanished_new_rows"], str(d["vanished_new_rows"]))
check("  taking doctor's exit code back to clean", d["exit_code"] == 0,
      str(d["exit_code"]))
shutil.rmtree(root, ignore_errors=True)

# A DAMAGED op record must not take doctor down. `doctor` is the command you run
# when something is already wrong, and it read (_m.get("rows") or [{}])[0]
# straight out of a journal file a user can edit: 'rows' as a dict raises
# KeyError: 0, a row with no dest_path raises KeyError out of _sync_row_drift,
# and a row that is a string raises AttributeError - none caught by main(), so
# the user got an unredacted traceback carrying store paths and account uuids.
# _new_row_shape_error existed, was documented NEVER RAISES, and was wired into
# classify_op and recover_op; this loop was the one consumer that skipped it.
print("\n--- doctor survives a damaged new-row op record ---")

SID2 = "%032d" % 72


def damage_last_new_row(env, mutate):
    """Corrupt the most recent new-row manifest the way a hand edit would."""
    ops = [o for o in ccs.list_ops(env) if o.manifest.get("op_type") == "new-row"]
    mutate(ops[-1].manifest)
    ccs.save_manifest(ops[-1])
    return ops[-1].manifest["op_id"]


for label, mutate in (
        ("'rows' is a dict", lambda mm: mm.__setitem__("rows", {"name": "x"})),
        ("the row has no dest_path", lambda mm: mm["rows"][0].pop("dest_path")),
        ("the row is a string", lambda mm: mm.__setitem__("rows", ["nope"])),
        ("'rows' is empty", lambda mm: mm.__setitem__("rows", [])),
):
    root, env, live, dorm = build([OPENER] + prose(8))
    # A HEALTHY op whose row vanished, so the test can tell "skipped the damaged
    # one" from "bailed out of the whole loop".
    m = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID, title="Healthy"))
    ccs.run_new_row(env, m)
    os.unlink(m["rows"][0]["dest_path"])
    with open(os.path.join(env.projects_root, "proj", SID2 + ".jsonl"), "w",
              encoding="utf-8") as fh:
        fh.write("\n".join([OPENER] + prose(8)) + "\n")
    m2 = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID2, title="Damaged"))
    ccs.run_new_row(env, m2)
    bad_id = damage_last_new_row(env, mutate)
    try:
        d = ccs.gather_doctor(env)
        check("doctor survives a completed new-row op where %s" % label, True)
        check("  and still reports the healthy op's vanished row",
              [v["op_id"] for v in d["vanished_new_rows"]] == [m["op_id"]],
              str([v["op_id"] for v in d["vanished_new_rows"]]))
        check("  without inventing a finding for the damaged one",
              bad_id not in [v["op_id"] for v in d["vanished_new_rows"]])
    except BaseException as exc:                   # noqa: BLE001 - that is the bug
        check("doctor survives a completed new-row op where %s" % label, False,
              "%s: %s" % (type(exc).__name__, str(exc)[:70]))
        check("  and still reports the healthy op's vanished row", False, "no report")
        check("  without inventing a finding for the damaged one", False, "no report")
    shutil.rmtree(root, ignore_errors=True)

# undo shape-validates too. The branch closed this hole for `recover` and left
# it open for `undo`, which dereferences the same m["rows"][0] out of the same
# editable file - KeyError/IndexError, which main() does not catch either.
print("\n--- undo refuses a damaged record instead of tracebacking ---")

for label, mutate in (
        ("'rows' is a dict", lambda mm: mm.__setitem__("rows", {"name": "x"})),
        ("'rows' is empty", lambda mm: mm.__setitem__("rows", [])),
        ("the row has no dest_path", lambda mm: mm["rows"][0].pop("dest_path")),
        ("there is no store_path", lambda mm: mm.pop("store_path")),
):
    root, env, live, dorm = build([OPENER] + prose(8))
    m = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID, title="Undo me"))
    ccs.run_new_row(env, m)
    path = m["rows"][0]["dest_path"]
    damage_last_new_row(env, mutate)
    op = [o for o in ccs.list_ops(env) if o.manifest.get("op_type") == "new-row"][-1]
    try:
        ccs.undo_new_row(env, op)
        check("undo refuses when %s" % label, False, "it did not refuse")
        check("  and the row is left alone", os.path.exists(path))
    except ccs.Refusal as exc:
        check("undo refuses when %s" % label, "damaged" in str(exc), str(exc)[:80])
        check("  and the row is left alone", os.path.exists(path))
    except BaseException as exc:                   # noqa: BLE001 - that is the bug
        check("undo refuses when %s" % label, False,
              "%s: %s" % (type(exc).__name__, str(exc)[:70]))
        check("  and the row is left alone", os.path.exists(path))
    # the lock must not survive the refusal - undo_new_row releases in a finally
    check("  and the lock is released", not os.path.exists(ccs._lock_path(env)))
    shutil.rmtree(root, ignore_errors=True)


# README:470 promises plain-text output replaces the home directory with `~` and
# that --verbose shows paths in full; README:527 tells users not to paste --json
# into an issue BECAUSE that one is unredacted, which reads as a guarantee about
# the plain text. This command printed the plan through a bare `print`, so the
# user's project directory went out whole - and `new-row --verbose` exited 2,
# because the flag did not exist.
print("\n--- the report redacts, and --verbose turns that off ---")

import io  # noqa: E402 - needed only by the encoding check below

check("the parser accepts --verbose for new-row",
      ccs.build_parser().parse_args(["new-row", "--to", "x", "--verbose"]).verbose
      is True)
check("  and defaults it off",
      ccs.build_parser().parse_args(["new-row", "--to", "x"]).verbose is False)


def new_row_output(env, **kw):
    """Drive cmd_new_row and return (rc, printed lines)."""
    lines = []
    real = builtins.print
    builtins.print = lambda *a, **k: lines.append(" ".join(str(x) for x in a))
    try:
        rc = ccs.cmd_new_row(env, _Ns(to_session=SID, store="", title="Recovered",
                                      live="", **kw))
    finally:
        builtins.print = real
    return rc, lines


def retranscribe(env, cwd, sid=SID):
    """Rewrite the fixture transcript so its cwd is one this test chose."""
    opener = rec("user", "the opening message, long enough to be a real one",
                 ts="2026-06-14T09:00:00.000Z", cwd=cwd)
    with open(os.path.join(env.projects_root, "proj", sid + ".jsonl"), "w",
              encoding="utf-8") as fh:
        fh.write("\n".join([opener] + prose(8)) + "\n")


root, env, live, dorm = build([OPENER] + prose(8))
under_home = os.path.join(env.home, "Projects", "Personal")
retranscribe(env, under_home)

rc, lines = new_row_output(env, apply=False, json=False, verbose=False)
check("the default report does not print the home directory",
      rc == 0 and not any(env.home in l for l in lines),
      str([l for l in lines if env.home in l])[:90])
check("  it prints the project line redacted to ~",
      any(l.startswith("project") and "~" in l for l in lines),
      str([l for l in lines if l.startswith("project")]))

rc, lines = new_row_output(env, apply=False, json=False, verbose=True)
check("--verbose prints the project directory in full",
      rc == 0 and any(under_home in l for l in lines),
      str([l for l in lines if l.startswith("project")]))
shutil.rmtree(root, ignore_errors=True)

# A cwd outside the console codepage. Piped stdout on Windows is cp1252 here, so
# print() raised UnicodeEncodeError - and because the report prints BEFORE the
# write, the command aborted having done nothing at all. A bare traceback for a
# directory name, from a command whose whole job is to add one row.
print("\n--- an unprintable cwd degrades the report, it does not abort ---")

root, env, live, dorm = build([OPENER] + prose(8))
odd = os.path.join(env.home, "Projects", "\u65e5\u672c\u8a9e")   # not in cp1252
retranscribe(env, odd)

buf = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", newline="")
real_stdout, err, rc = sys.stdout, None, None
sys.stdout = buf
try:
    rc = ccs.cmd_new_row(env, _Ns(to_session=SID, store="", title="Recovered",
                                  live="", apply=True, json=False, verbose=True))
except BaseException as exc:                       # noqa: BLE001 - that is the bug
    err = exc
finally:
    sys.stdout = real_stdout
    try:
        buf.flush()
        printed = buf.buffer.getvalue().decode("cp1252", "replace")
    except BaseException:                          # noqa: BLE001
        printed = ""

check("a cwd outside the console codepage does not raise",
      err is None, "" if err is None else "%s: %s" % (type(err).__name__,
                                                      str(err)[:60]))
check("  the command still completes", rc == 0, str(rc))
check("  and the row is actually on disk - the report printed BEFORE the write, "
      "so a raising report meant nothing was created",
      any(n.startswith("local_") for n in os.listdir(live)), str(os.listdir(live)))
check("  the report still got out, with the unencodable characters replaced",
      "project" in printed and "Reopen the app" in printed, repr(printed[:70]))
shutil.rmtree(root, ignore_errors=True)


# The one refusal that CANNOT happen before set_status, because a write cannot
# be known to fail until it is tried. It said "Nothing was written - check the
# store is writable, then re-run", and re-running does succeed - while the
# 'writing' op it journalled stays open forever, doctor reports it, and only
# 'recover --back' clears it. The structure is unavoidable; the message was not.
print("\n--- the write refusal names the op it strands ---")

root, env, live, dorm = build([OPENER] + prose(8))
m = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID, title="Recovered"))
real_write = ccs.atomic_write


def fail_the_row_write(path, data):
    """Fail ONLY the row write - the journal shares atomic_write, and failing
    that instead would test a different (and much earlier) failure."""
    if os.path.normcase(path) == os.path.normcase(m["rows"][0]["dest_path"]):
        raise OSError("disk is full")
    return real_write(path, data)


ccs.atomic_write = fail_the_row_write
try:
    ccs.run_new_row(env, m)
    check("a failed write refuses", False, "no refusal")
    msg = ""
except ccs.Refusal as exc:
    msg = str(exc)
    check("a failed write refuses", True)
finally:
    ccs.atomic_write = real_write

check("  the message no longer claims nothing happened at all",
      "Nothing was written - " not in msg, msg[:80])
check("  it says the operation is journalled and still open",
      "journalled" in msg and "'writing'" in msg, msg[:120])
check("  and names recover --back, with the op id, as the way to close it",
      "recover --resolve %s --back" % m["op_id"] in msg, msg[-140:])

# The claim has to be TRUE, not merely reassuring: the op really is stranded,
# and the command the message names really does close it.
pending = ccs.nonterminal_ops(env)
check("  the op really is left non-terminal", len(pending) == 1, str(len(pending)))
check("    at exactly the status the message names",
      pending[0].manifest["status"] == "writing", pending[0].manifest["status"])
d = ccs.gather_doctor(env)
check("    and doctor really does report it",
      pending[0].manifest["op_id"] in d["nonterminal_ops"], str(d["nonterminal_ops"]))
check("    so doctor's exit code is non-zero", d["exit_code"] != 0, str(d["exit_code"]))

# Re-running succeeds - which is why the stale op is a real cost and not a
# theoretical one: the user does what the refusal says and is left with two
# things, one of which nothing told them about.
m2 = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID, title="Recovered"))
check("  re-running does succeed, leaving the stranded op behind",
      ccs.run_new_row(env, m2) == "completed")
check("    with the old op still open",
      [o.manifest["op_id"] for o in ccs.nonterminal_ops(env)] == [m["op_id"]],
      str([o.manifest["op_id"] for o in ccs.nonterminal_ops(env)]))

# and the advice works when followed
stuck = [o for o in ccs.nonterminal_ops(env) if o.manifest["op_id"] == m["op_id"]][0]
check("  'recover --back' closes it, exactly as the message promises",
      ccs.recover_op(env, stuck, "back") == "rolled_back")
check("    leaving nothing unresolved", not ccs.nonterminal_ops(env),
      str(ccs.nonterminal_ops(env)))
check("    and the row created by the re-run untouched",
      os.path.exists(m2["rows"][0]["dest_path"]))
shutil.rmtree(root, ignore_errors=True)


# The rollback residue was WRITE-ONLY. `recover --back` records that it left a
# row it could not remove, and `_collides` pins that op against rotation forever
# on the stated grounds that "a rolled_back op with a surviving row is invisible
# everywhere" - but nothing ever read the key back. gather_doctor read only
# 'completed' ops; cmd_recover's listing only non-terminal ones. The only reader
# was cmd_recover printing it during the call that wrote it, which the user may
# never see again. Probed: the pinned op survived 25 later ops, surfaced by
# nothing. The hold was protecting a record with no reader.
print("\n--- doctor surfaces a rollback that left a row behind ---")


def rolled_back_with_residue():
    """A torn new-row op whose row DRIFTED, rolled back - so 'back' skips the
    delete and records what it left. The row is on disk afterwards."""
    root, env, m = crashed()
    d = json.load(open(m["rows"][0]["dest_path"], encoding="utf-8"))
    d["lastFocusedAt"] = 777
    with open(m["rows"][0]["dest_path"], "w") as fh:
        json.dump(d, fh)
    ccs.recover_op(env, ccs.nonterminal_ops(env)[0], "back")
    return root, env, m


root, env, m = rolled_back_with_residue()
check("the setup really did leave a row behind",
      os.path.exists(m["rows"][0]["dest_path"]))
check("  on a TERMINAL op, which is why nothing else could see it",
      [o.manifest["status"] for o in ccs.list_ops(env)] == ["rolled_back"],
      str([o.manifest["status"] for o in ccs.list_ops(env)]))
check("  so recover's listing does not mention it",
      not ccs.nonterminal_ops(env))
d = ccs.gather_doctor(env)
check("doctor now reports it", len(d.get("rollback_residue") or []) == 1,
      str(d.get("rollback_residue")))
if d.get("rollback_residue"):
    r0 = d["rollback_residue"][0]
    check("  naming the op that left it", r0["op_id"] == m["op_id"], str(r0["op_id"]))
    check("  and the row itself", m["rows"][0]["name"] in r0["detail"],
          r0["detail"][:90])
check("  and it counts toward the exit code, like a vanished row does",
      d["exit_code"] == 1, str(d["exit_code"]))

# The human report has to say it too - a report key nobody prints is the same
# write-only failure one layer up.
out = []
real_bp4 = builtins.print
builtins.print = lambda *a, **k: out.append(" ".join(str(x) for x in a))
try:
    rc = ccs.cmd_doctor(env, _Ns(json=False, verbose=True))
finally:
    builtins.print = real_bp4
check("cmd_doctor prints it", rc == 1 and any(
    "left a row in the store" in line for line in out), str(out)[:100])
check("  naming the row left behind", any(m["rows"][0]["name"] in line
                                          for line in out))
check("  and the op that left it", any(m["op_id"] in line for line in out))

# ASK THE STORE, NOT THE JOURNAL. The residue is what was true at rollback time;
# once the user deletes that session in the app there is nothing left to report,
# and an alert that could never clear would be a permanent exit 1 over a
# resolved condition - the exact trap the vanished-row check was reshaped to
# avoid.
os.unlink(m["rows"][0]["dest_path"])
d = ccs.gather_doctor(env)
check("  the alert clears once the row is actually gone",
      not d.get("rollback_residue"), str(d.get("rollback_residue")))
check("    taking doctor's exit code back to clean", d["exit_code"] == 0,
      str(d["exit_code"]))
shutil.rmtree(root, ignore_errors=True)

# The residue survives rotation - that is what the hold is for - and doctor
# still finds it after other operations have come and gone.
root, env, m = rolled_back_with_residue()
with open(os.path.join(env.projects_root, "proj", ("%032d" % 73) + ".jsonl"), "w",
          encoding="utf-8") as fh:
    fh.write("\n".join([OPENER] + prose(8)) + "\n")
for i in range(12):
    m2 = ccs.plan_new_row(env, ccs.NewRowFlags(to_session="%032d" % 73,
                                               title="Filler %d" % i))
    ccs.run_new_row(env, m2)
    ccs.undo_new_row(env, [o for o in ccs.list_ops(env)
                           if o.manifest["op_id"] == m2["op_id"]][0])
check("12 later operations rotate the journal",
      len(ccs.list_ops(env)) <= 12, str(len(ccs.list_ops(env))))
d = ccs.gather_doctor(env)
check("  and doctor STILL reports the residue - what the hold is for",
      [r["op_id"] for r in d.get("rollback_residue") or []] == [m["op_id"]],
      str(d.get("rollback_residue")))
shutil.rmtree(root, ignore_errors=True)


# doctor recommended `repoint` for the condition this feature exists to fix.
# Repointing an existing row costs whatever that row used to open - the exact
# trade that produced the loss the block reports - while `new-row` is purely
# additive. new-row's own refusals point users at `doctor`, and README:186 says
# of this block "doctor lists them; this turns one back into a sidebar entry".
# doctor never said so.
print("\n--- doctor recommends new-row for an unlisted transcript ---")

# A UUID-SHAPED id, not the 32-digit ones the rest of this suite uses. That is
# the point of this fixture: redact() only shortens ids matching the uuid
# pattern, so every existing check here is blind to a truncated id - and these
# lines hand the user a command to paste back.
UUID_SID = "174eb7c1-879f-4f0e-abff-8fdc7210f3d9"


def doctor_output(env, verbose=False):
    lines = []
    real = builtins.print
    builtins.print = lambda *a, **k: lines.append(" ".join(str(x) for x in a))
    try:
        rc = ccs.cmd_doctor(env, _Ns(json=False, verbose=verbose))
    finally:
        builtins.print = real
    return rc, lines


root, env, live, dorm = build([OPENER] + prose(8), sid=UUID_SID)
rc, lines = doctor_output(env)
advice = [l for l in lines if "to reach one again" in l or "repoint --only" in l]
check("doctor still reports the unlisted transcript",
      any("no listing row in any account" in l for l in lines), str(lines)[:90])
check("  and offers new-row FIRST", advice and "new-row --to" in advice[0],
      str(advice))
check("  keeping repoint as the alternative",
      any("repoint --only" in l for l in advice), str(advice))
check("  and saying what repoint costs that new-row does not",
      any("EXISTING row" in l for l in advice), str(advice))
shutil.rmtree(root, ignore_errors=True)

# The vanished-row branch hands over a command too, and printed it through
# say() - which truncates a uuid-shaped id to eight characters, so the command
# could not be pasted back. The ranked-orphan block above uses say_ids for
# exactly this reason; this branch was written with say and nothing caught it.
print("\n--- and the vanished-row advice survives redaction ---")

root, env, live, dorm = build([OPENER] + prose(8), sid=UUID_SID)
m = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=UUID_SID, title="Recovered"))
ccs.run_new_row(env, m)
os.unlink(m["rows"][0]["dest_path"])
rc, lines = doctor_output(env)
hint = [l for l in lines if "makes another" in l]
check("the vanished-row advice is printed", len(hint) == 1, str(lines)[-160:])
check("  with the WHOLE session id, so the command can be pasted back",
      hint and UUID_SID in hint[0], str(hint))
check("  and it is the new-row command", hint and "new-row --to" in hint[0],
      str(hint))

# The ambiguous branch said "Resolve that first" and stopped. Which folders hold
# the duplicates is the entire answer, and only gather_doctor knows it.
other = os.path.join(env.projects_root, "other")
os.makedirs(other)
shutil.copy(m["transcript"], os.path.join(other, UUID_SID + ".jsonl"))
d = ccs.gather_doctor(env)
check("the report carries the duplicate PATHS, not just a count",
      len(d["vanished_new_rows"][0].get("transcript_paths") or []) == 2,
      str(d["vanished_new_rows"][0].get("transcript_paths")))
rc, lines = doctor_output(env)
check("  and doctor prints both of them",
      sum(1 for l in lines if l.strip().endswith(UUID_SID + ".jsonl")) == 2,
      str([l for l in lines if UUID_SID in l])[:150])
check("  telling the user how to resolve it, not just that they must",
      any("remove or rename" in l.lower() for l in lines),
      str([l for l in lines if "folders" in l or "rename" in l])[:150])
check("  and no longer just saying 'Resolve that first'",
      not any("Resolve that first" in l for l in lines))
shutil.rmtree(root, ignore_errors=True)

# The same condition reached through the command itself. Its refusal listed the
# duplicate paths and stopped - evidence with no instruction, against this
# plan's own rule that every refusal names a way forward.
print("\n--- and so does the refusal the command raises ---")

root, env, live, dorm = build([OPENER] + prose(8))
other = os.path.join(env.projects_root, "other")
os.makedirs(other)
shutil.copy(os.path.join(env.projects_root, "proj", SID + ".jsonl"),
            os.path.join(other, SID + ".jsonl"))
try:
    ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID))
    check("a duplicated transcript still refuses", False, "no refusal")
    msg = ""
except ccs.Refusal as exc:
    msg = str(exc)
    check("a duplicated transcript still refuses", True)
check("  still naming both paths", msg.count(SID + ".jsonl") == 2, msg[-90:])
check("  and now saying what to do about them",
      "Remove or rename" in msg, msg[:140])
shutil.rmtree(root, ignore_errors=True)


print()
print("ALL PASS" if all(ok) else "SOME CHECKS FAILED")
sys.exit(0 if all(ok) else 1)
