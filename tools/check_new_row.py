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

print()
print("ALL PASS" if all(ok) else "SOME CHECKS FAILED")
sys.exit(0 if all(ok) else 1)
