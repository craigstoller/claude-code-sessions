"""ccs new-row - creating a sidebar row for a conversation that has none."""
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

root, env, live, dorm = build([OPENER] + prose(8))
m = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID, title="Recovered"))
ccs.run_new_row(env, m)
os.unlink(m["transcript"])
refusal("apply refuses once the transcript is gone",
        lambda: ccs.run_new_row(env, ccs.plan_new_row(
            env, ccs.NewRowFlags(to_session=SID, store=DORM))),
        "no transcript on disk")
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

print()
print("ALL PASS" if all(ok) else "SOME CHECKS FAILED")
sys.exit(0 if all(ok) else 1)
