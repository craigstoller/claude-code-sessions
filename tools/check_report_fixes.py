"""0.9.13 - the three report fixes, each pinned to the failure that produced it.

All three were found in live use on 2026-08-22, in a session where the plan was
read carefully enough to catch what it was not saying:

  A. `_message_fingerprints` counted app-emitted plumbing as conversation, so
     three rows reported 95/98/98% overlap when the authored content only in the
     displaced copy was ZERO.
  B. `_other_pointers` skipped the whole destination STORE, so a conversation
     still held by another row in the destination read as about to be orphaned.
  C. Nothing reported that a row got SHORTER. Two swaps in one plan cost 256 and
     100 prose turns, described only as a percentage.
"""
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


LIVE, DORM, THIRD = "a" * 32, "b" * 32, "c" * 32
ORG_L, ORG_D, ORG_T = "1" * 32, "2" * 32, "3" * 32


def turns(*bodies):
    """A transcript body: alternating user/assistant prose turns."""
    out = []
    for i, b in enumerate(bodies):
        out.append(json.dumps({"type": "user" if i % 2 == 0 else "assistant",
                               "message": {"content": b}}))
    return "\n".join(out) + "\n"


def prose(n, tag="t"):
    """n distinct authored turns, comfortably above OVERLAP_MIN_SAMPLE."""
    return ["%s turn number %d with enough words to be a real message" % (tag, i)
            for i in range(n)]


def build(transcripts):
    """transcripts: {sid: [body, ...]} -> (root, env, store dirs)"""
    root = tempfile.mkdtemp(prefix="rep-")
    home = os.path.join(root, "home")
    store = os.path.join(root, "Claude", "claude-code-sessions")
    dirs = {}
    for acct, org, key in ((LIVE, ORG_L, "live"), (DORM, ORG_D, "dorm"),
                           (THIRD, ORG_T, "third")):
        dirs[key] = os.path.join(store, acct, org)
        os.makedirs(dirs[key])
    projects = os.path.join(home, ".claude", "projects", "proj")
    os.makedirs(projects)
    for sid, bodies in transcripts.items():
        with open(os.path.join(projects, sid + ".jsonl"), "w", encoding="utf-8") as fh:
            fh.write(turns(*bodies))
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
    return root, env, dirs, projects


def row(path, name, sid, title, when=5000):
    with open(os.path.join(path, name), "w") as fh:
        json.dump({"cliSessionId": sid, "title": title, "cwd": "proj",
                   "lastActivityAt": when}, fh)


# ==========================================================================
# A. plumbing is not conversation
# ==========================================================================
print("\n--- A. app-emitted plumbing is excluded from the comparison ---")

A_SID, B_SID = "%032d" % 11, "%032d" % 12
authored = prose(10)
noise = ["[Request interrupted by user]",
         "[Request interrupted by user for tool use]",
         "No response requested."]
root, env, dirs, projects = build({A_SID: authored + noise, B_SID: authored})

fa = ccs._message_fingerprints(os.path.join(projects, A_SID + ".jsonl"))
fb = ccs._message_fingerprints(os.path.join(projects, B_SID + ".jsonl"))
check("the plumbing markers are not fingerprinted",
      len(fa) == 10, "got %d, expected 10" % len(fa))
check("  a transcript with only plumbing added compares as identical",
      fa == fb)
check("  so its overlap is a clean 1.0, not 10/14",
      ccs._displaced_overlap(env, A_SID, B_SID) == 1.0,
      str(ccs._displaced_overlap(env, A_SID, B_SID)))
check("  which prints the full-overlap wording",
      "every prose turn" in ccs._overlap_clause(
          ccs._displaced_overlap(env, A_SID, B_SID)))
shutil.rmtree(root, ignore_errors=True)

# the filter must never eat something a PERSON typed, however thin.
# "Continue from where you left off." is pinned here because it SHIPPED in the
# first draft of the prefix list and three independent reviewers flagged the
# same thing: it arrives as a user-role turn and nothing distinguishes it from
# a person typing those words. Dropping it removed the turn from both sides of
# the comparison, which is how a real difference becomes a false 100%.
C_SID, D_SID = "%032d" % 13, "%032d" % 14
human = "I accidentally closed the app. Try again."
resume = "Continue from where you left off."
root, env, dirs, projects = build({C_SID: prose(10) + [human, resume],
                                   D_SID: prose(10)})
fc = ccs._message_fingerprints(os.path.join(projects, C_SID + ".jsonl"))
check("a turn the USER typed is never filtered, however little it carries",
      len(fc) == 12, "got %d, expected 12" % len(fc))
check("  'Continue from where you left off.' is NOT in the prefix list",
      not any(resume.startswith(p) for p in ccs.TRANSCRIPT_PLUMBING_PREFIXES),
      str(ccs.TRANSCRIPT_PLUMBING_PREFIXES))
check("  so it correctly shows as content only in the displaced copy",
      ccs._displaced_overlap(env, C_SID, D_SID) < 1.0,
      str(ccs._displaced_overlap(env, C_SID, D_SID)))
shutil.rmtree(root, ignore_errors=True)

# the specific failure the removal prevents: a filtered-but-authored turn is
# dropped from BOTH sides, and a real difference reads as full overlap
G_SID, H_SID = "%032d" % 17, "%032d" % 18
root, env, dirs, projects = build({G_SID: prose(10) + [resume], H_SID: prose(10)})
check("dropping an authored turn would have inflated overlap to a false 1.0",
      ccs._displaced_overlap(env, G_SID, H_SID) < 1.0,
      str(ccs._displaced_overlap(env, G_SID, H_SID)))
check("  and the clause therefore does NOT claim every prose turn is carried",
      "every prose turn" not in ccs._overlap_clause(
          ccs._displaced_overlap(env, G_SID, H_SID)))
shutil.rmtree(root, ignore_errors=True)

# over-filtering degrades to NOT MEASURED, never to a wrong number
E_SID, F_SID = "%032d" % 15, "%032d" % 16
root, env, dirs, projects = build({E_SID: prose(3) + noise * 3, F_SID: prose(3)})
check("filtering below OVERLAP_MIN_SAMPLE reports NOT MEASURED, not a percentage",
      ccs._displaced_overlap(env, E_SID, F_SID) is None)
check("  and the clause says so in words",
      "NOT MEASURED" in ccs._overlap_clause(ccs._displaced_overlap(env, E_SID, F_SID)))
shutil.rmtree(root, ignore_errors=True)


# ==========================================================================
# B. the orphan check sees the destination's OWN surviving rows
# ==========================================================================
print("\n--- B. reachability is per-row, not per-store ---")

OLD, NEW = "%032d" % 21, "%032d" % 22
shared = prose(12, "s")


def build_two_rows(also_overwrite_the_second):
    """Destination holds the same conversation on TWO rows. The plan overwrites
    the first. Whether the second survives decides whether OLD is orphaned."""
    root, env, dirs, projects = build({OLD: shared, NEW: shared + prose(4, "n")})
    row(dirs["live"], "local_one.json", NEW, "Shared slot")
    row(dirs["dorm"], "local_one.json", OLD, "Shared slot", when=4000)
    # the destination's OTHER row, pointing at the same displaced conversation
    row(dirs["dorm"], "local_two.json", OLD, "Second door", when=4000)
    if also_overwrite_the_second:
        # ...and the source has that row too, so this plan overwrites BOTH
        row(dirs["live"], "local_two.json", NEW, "Second door")
    return root, env, dirs["dorm"]


# the bug: another row in the DESTINATION still holds it
root, env, todir = build_two_rows(also_overwrite_the_second=False)
m = ccs.plan_sync(env, ccs.SyncFlags(update=True, allow_orphan=True, to=todir))
r1 = [r for r in m["rows"] if r["title"] == "Shared slot"][0]
check("a swap is still detected", r1["swaps_conversation"] is True)
check("the destination's OWN surviving row counts - NOT orphaned",
      r1["displaced_orphan"] is False, str(r1["displaced_orphan"]))
out = []
ccs._print_sync_report(out.append, m)
check("  and the report does not claim it becomes unreachable",
      not any("becomes unreachable" in l for l in out))
shutil.rmtree(root, ignore_errors=True)

# the guard against over-correcting: if THIS plan also overwrites the second
# row, that row cannot vouch for the conversation either
root, env, todir = build_two_rows(also_overwrite_the_second=True)
m = ccs.plan_sync(env, ccs.SyncFlags(update=True, allow_orphan=True, to=todir))
r2 = [r for r in m["rows"] if r["title"] == "Shared slot"][0]
check("a row THIS PLAN also overwrites does not vouch for it - IS orphaned",
      r2["displaced_orphan"] is True, str(r2["displaced_orphan"]))
shutil.rmtree(root, ignore_errors=True)

# a row must never vouch for its own conversation
root, env, dirs, projects = build({OLD: shared, NEW: shared + prose(4, "n")})
row(dirs["live"], "local_solo.json", NEW, "Only slot")
row(dirs["dorm"], "local_solo.json", OLD, "Only slot", when=4000)
m = ccs.plan_sync(env, ccs.SyncFlags(update=True, allow_orphan=True, to=dirs["dorm"]))
r3 = [r for r in m["rows"] if r["title"] == "Only slot"][0]
check("the doomed row does not vouch for itself - orphaned as before",
      r3["displaced_orphan"] is True, str(r3["displaced_orphan"]))
shutil.rmtree(root, ignore_errors=True)

# the reachability set itself: exactly which rows are allowed to vouch.
# Asserted directly, because it is the one place the old and new behaviour
# differ and a plan-level check could pass for the wrong reason.
root, env, dirs, projects = build({OLD: shared, NEW: shared + prose(4, "n")})
row(dirs["live"], "local_one.json", NEW, "Shared slot")
row(dirs["dorm"], "local_one.json", OLD, "Shared slot", when=4000)
row(dirs["dorm"], "local_two.json", OLD, "Second door", when=4000)
p_none = ccs._other_pointers(env, dirs["dorm"])
p_one = ccs._other_pointers(env, dirs["dorm"], {"local_one.json"})
p_both = ccs._other_pointers(env, dirs["dorm"], {"local_one.json", "local_two.json"})
check("with nothing doomed, both destination rows vouch",
      len(p_none.get(OLD, [])) == 2, str(p_none.get(OLD)))
check("  dooming one leaves the other vouching",
      len(p_one.get(OLD, [])) == 1, str(p_one.get(OLD)))
check("  dooming both leaves nobody - this is the OLD store-wide behaviour",
      OLD not in p_both, str(p_both.get(OLD)))
shutil.rmtree(root, ignore_errors=True)

# the fail-closed sentinel must survive the change
root, env, dirs, projects = build({OLD: shared, NEW: shared + prose(4, "n")})
row(dirs["live"], "local_solo.json", NEW, "Only slot")
row(dirs["dorm"], "local_solo.json", OLD, "Only slot", when=4000)
row(dirs["third"], "local_other.json", OLD, "Third door", when=4000)
third_dir = dirs["third"]
orig_listdir = os.listdir


def blind_listdir(p):
    if os.path.normcase(os.path.abspath(p)) == os.path.normcase(
            os.path.abspath(third_dir)):
        raise OSError(13, "permission denied")
    return orig_listdir(p)


ccs.os.listdir = blind_listdir
try:
    m = ccs.plan_sync(env, ccs.SyncFlags(update=True, allow_orphan=True,
                                         to=dirs["dorm"]))
finally:
    ccs.os.listdir = orig_listdir
r4 = [r for r in m["rows"] if r["title"] == "Only slot"][0]
check("an unreadable sibling store still yields 'unknown', never False",
      r4["displaced_orphan"] == "unknown", str(r4["displaced_orphan"]))
shutil.rmtree(root, ignore_errors=True)


# ==========================================================================
# C. the plan says what the swap does to the row's LENGTH
# ==========================================================================
print("\n--- C. per-row length, not just a percentage ---")

check("shrink is stated bluntly", "100 FEWER" in ccs._length_clause(436, 336),
      ccs._length_clause(436, 336))
check("  naming both counts", ccs._length_clause(436, 336).startswith(
    "this row goes from 436 to 336"))
check("growth reads as growth", "205 more" in ccs._length_clause(130, 335))
check("a wash says so without alarm",
      ccs._length_clause(19, 19) == "both conversations have 19 prose turns")
check("unknown counts say nothing rather than guess",
      ccs._length_clause(None, 5) == "" and ccs._length_clause(5, None) == "")

LONG, SHORT = "%032d" % 31, "%032d" % 32
root, env, dirs, projects = build({LONG: prose(20, "L"), SHORT: prose(9, "L")})
row(dirs["live"], "local_x.json", SHORT, "Going backwards")
row(dirs["dorm"], "local_x.json", LONG, "Going backwards", when=4000)
m = ccs.plan_sync(env, ccs.SyncFlags(update=True, allow_orphan=True, to=dirs["dorm"]))
r5 = [r for r in m["rows"] if r["title"] == "Going backwards"][0]
check("the plan carries both turn counts",
      (r5["displaced_turns"], r5["incoming_turns"]) == (20, 9),
      str((r5["displaced_turns"], r5["incoming_turns"])))
out = []
ccs._print_sync_report(out.append, m)
check("  and the report states the loss in turns",
      any("20 to 9 prose turns" in l and "FEWER" in l for l in out),
      next((l.strip() for l in out if "prose turns" in l), "(no length line)"))
shutil.rmtree(root, ignore_errors=True)

# the length is reported even when the overlap cannot be measured
TINY_A, TINY_B = "%032d" % 33, "%032d" % 34
root, env, dirs, projects = build({TINY_A: prose(5, "A"), TINY_B: prose(2, "B")})
row(dirs["live"], "local_y.json", TINY_B, "Unmeasurable")
row(dirs["dorm"], "local_y.json", TINY_A, "Unmeasurable", when=4000)
m = ccs.plan_sync(env, ccs.SyncFlags(update=True, allow_orphan=True, to=dirs["dorm"]))
r6 = [r for r in m["rows"] if r["title"] == "Unmeasurable"][0]
check("overlap is unmeasurable on a short pair", r6["displaced_overlap"] is None)
check("  but the length is still reported",
      (r6["displaced_turns"], r6["incoming_turns"]) == (5, 2),
      str((r6["displaced_turns"], r6["incoming_turns"])))
out = []
ccs._print_sync_report(out.append, m)
check("  so the report shows a length line AND 'NOT MEASURED'",
      any("5 to 2 prose turns" in l for l in out)
      and any("NOT MEASURED" in l for l in out))
shutil.rmtree(root, ignore_errors=True)


# ==========================================================================
# D. the wording, and the window
# ==========================================================================
print("\n--- D. what the two surfaces actually say ---")

# "another account" was true only while reachability was per STORE. Since the
# voucher can now be a different row in the SAME destination account, that
# wording would send the reader to the wrong sidebar.
root, env, dirs, projects = build({OLD: shared, NEW: shared + prose(4, "n")})
row(dirs["live"], "local_one.json", NEW, "Shared slot")
row(dirs["dorm"], "local_one.json", OLD, "Shared slot", when=4000)
row(dirs["dorm"], "local_two.json", OLD, "Second door", when=4000)
m = ccs.plan_sync(env, ccs.SyncFlags(update=True, allow_orphan=True, to=dirs["dorm"]))
out = []
ccs._print_sync_report(out.append, m)
joined = "\n".join(out)
check("the report says 'another surviving row', not 'another account'",
      "another surviving row" in joined and
      "stays reachable from another account" not in joined)
shutil.rmtree(root, ignore_errors=True)

# an unmeasurable length must SAY so, never print nothing - a reader trained to
# look for "FEWER" reads a missing line as "no loss"
root, env, dirs, projects = build({TINY_A: prose(5, "A"), TINY_B: prose(2, "B")})
row(dirs["live"], "local_y.json", TINY_B, "Unmeasurable")
row(dirs["dorm"], "local_y.json", TINY_A, "Unmeasurable", when=4000)
m = ccs.plan_sync(env, ccs.SyncFlags(update=True, allow_orphan=True, to=dirs["dorm"]))
m["rows"][0]["displaced_turns"] = None       # force the unknown branch
m["rows"][0]["incoming_turns"] = None
out = []
ccs._print_sync_report(out.append, m)
check("an unknown length prints a line rather than silence",
      any("turn counts unknown" in l for l in out),
      next((l.strip() for l in out if "unknown" in l), "(silent)"))
shutil.rmtree(root, ignore_errors=True)

# The window is where the "allow hiding a conversation" checkbox lives, so it
# is where the length has to appear. 0.9.13 shipped it to the CLI report only;
# a reviewer caught that the GUI still rendered the percentage alone. This is a
# STRUCTURAL check on the source, not a behavioural one - it exists to catch
# exactly that regression: a clause added to one surface and not the other.
gui = os.path.join(REPO, "claude_code_sessions_gui.py")
src = open(gui, encoding="utf-8").read()
check("the window renders the length clause, not only the overlap clause",
      src.count("_length_clause") >= 2, "found %d" % src.count("_length_clause"))
check("  at every site where it renders the overlap clause",
      src.count("_length_clause") >= src.count("_overlap_clause"),
      "length %d vs overlap %d" % (src.count("_length_clause"),
                                   src.count("_overlap_clause")))
check("  and it does not say 'another account' either",
      "reachable from another account" not in src)

# ==========================================================================
# E. reachability is re-checked at APPLY time, not trusted from the plan
# ==========================================================================
print("\n--- E. the voucher that vanishes between plan and apply ---")

# plan_sync decides displaced_orphan from the store as it stood when the plan
# was built. The app repoints rows while this tool is not running, so a
# conversation that had a second door when planned can lose it before Apply.
# Until 0.9.15 the write went ahead on the stale answer and orphaned it without
# ever asking for --allow-orphan.
root, env, dirs, projects = build({OLD: shared, NEW: shared + prose(4, "n")})
row(dirs["live"], "local_one.json", NEW, "Shared slot")
row(dirs["dorm"], "local_one.json", OLD, "Shared slot", when=4000)
row(dirs["dorm"], "local_two.json", OLD, "Second door", when=4000)
m = ccs.plan_sync(env, ccs.SyncFlags(update=True, to=dirs["dorm"]))
r = [x for x in m["rows"] if x["title"] == "Shared slot"][0]
check("planned as safe - a second door exists", r["displaced_orphan"] is False)
check("  and the plan records the orphan consent it was built under",
      m.get("allow_orphan") is False, str(m.get("allow_orphan")))

# the door closes after planning, exactly as the app does it
os.remove(os.path.join(dirs["dorm"], "local_two.json"))
try:
    ccs.run_sync(env, m)
    check("apply REFUSES once the only other door is gone", False, "it wrote")
except ccs.Refusal as exc:
    msg = str(exc)
    check("apply REFUSES once the only other door is gone", True)
    check("  naming reachability, not drift", "reachability changed" in msg, msg[:70])
    check("  and stating nothing was written", "Nothing has been written" in msg)
    # It must NOT offer --allow-orphan here. That flag means "hide the
    # conversations this plan named", and this one is not among them - it
    # became hideable after the plan was read. Re-planning is the only route
    # that puts the decision in front of the user with the evidence.
    check("  pointing at a re-plan, not at the flag", "Re-run to re-plan" in msg)
    check("  and saying what the re-plan will do with it",
          "hold the row back" in msg, msg[-120:])
    check("  and NOT offering --allow-orphan for an orphan nobody reviewed",
          "--allow-orphan" not in msg, msg[-90:])
dest = os.path.join(dirs["dorm"], "local_one.json")
still = json.load(open(dest, encoding="utf-8"))["cliSessionId"]
check("  the destination row is untouched - it still opens the old conversation",
      still == OLD, "opens %s, expected %s" % (still[:8], OLD[:8]))
shutil.rmtree(root, ignore_errors=True)

# --allow-orphan is consent to the orphans the PLAN NAMED, not a blanket
# licence. Both reviewers rejected the first version, which returned early on
# the flag and would have let a brand-new orphan through unseen. A row planned
# as SAFE, whose voucher then vanishes, is not something the user was ever
# shown - so it refuses even with the flag set.
root, env, dirs, projects = build({OLD: shared, NEW: shared + prose(4, "n")})
row(dirs["live"], "local_one.json", NEW, "Shared slot")
row(dirs["dorm"], "local_one.json", OLD, "Shared slot", when=4000)
row(dirs["dorm"], "local_two.json", OLD, "Second door", when=4000)
m = ccs.plan_sync(env, ccs.SyncFlags(update=True, allow_orphan=True, to=dirs["dorm"]))
check("a plan built with --allow-orphan records it", m.get("allow_orphan") is True)
check("  and this row was planned SAFE, so consent never covered it",
      [x for x in m["rows"] if x["title"] == "Shared slot"][0]["displaced_orphan"]
      is False)
os.remove(os.path.join(dirs["dorm"], "local_two.json"))
try:
    ccs.run_sync(env, m)
    check("  --allow-orphan does NOT wave through a newly-created orphan",
          False, "it wrote")
except ccs.Refusal as exc:
    check("  --allow-orphan does NOT wave through a newly-created orphan", True)
    check("    and says why - it was not one the plan offered to hide",
          "NOT one of the conversations the plan offered to hide" in str(exc),
          str(exc)[:80])
shutil.rmtree(root, ignore_errors=True)

# a swap that was ALWAYS orphaning is not re-litigated either
root, env, dirs, projects = build({OLD: shared, NEW: shared + prose(4, "n")})
row(dirs["live"], "local_solo.json", NEW, "Only slot")
row(dirs["dorm"], "local_solo.json", OLD, "Only slot", when=4000)
m = ccs.plan_sync(env, ccs.SyncFlags(update=True, allow_orphan=True, to=dirs["dorm"]))
try:
    ccs.run_sync(env, m)
    check("an always-orphaning swap still applies under --allow-orphan", True)
except ccs.Refusal as exc:
    check("an always-orphaning swap still applies under --allow-orphan",
          False, str(exc)[:70])
shutil.rmtree(root, ignore_errors=True)

# A manifest written before 0.9.15 has no allow_orphan key at all. `.get()`
# returns None there, which is falsy, so the check RUNS - the fail-safe
# direction. Bracket access would raise KeyError on exactly the recovery path
# this feature is meant to protect, so the accessor is pinned here.
root, env, dirs, projects = build({OLD: shared, NEW: shared + prose(4, "n")})
row(dirs["live"], "local_one.json", NEW, "Shared slot")
row(dirs["dorm"], "local_one.json", OLD, "Shared slot", when=4000)
row(dirs["dorm"], "local_two.json", OLD, "Second door", when=4000)
m = ccs.plan_sync(env, ccs.SyncFlags(update=True, to=dirs["dorm"]))
del m["allow_orphan"]                       # a pre-0.9.15 manifest
os.remove(os.path.join(dirs["dorm"], "local_two.json"))
try:
    ccs.run_sync(env, m)
    check("a legacy manifest with no allow_orphan key still gets the check",
          False, "it wrote")
except KeyError as exc:
    check("a legacy manifest with no allow_orphan key still gets the check",
          False, "KeyError: %s" % exc)
except ccs.Refusal:
    check("a legacy manifest with no allow_orphan key still gets the check", True)
shutil.rmtree(root, ignore_errors=True)

# On a RESUME the op has already written rows, so "Nothing has been written"
# would be false - and would send the user looking for an untouched
# destination that does not exist.
root, env, dirs, projects = build({OLD: shared, NEW: shared + prose(4, "n")})
row(dirs["live"], "local_one.json", NEW, "Shared slot")
row(dirs["dorm"], "local_one.json", OLD, "Shared slot", when=4000)
row(dirs["dorm"], "local_two.json", OLD, "Second door", when=4000)
m = ccs.plan_sync(env, ccs.SyncFlags(update=True, to=dirs["dorm"]))
m["rows"].append({"name": "local_done.json", "written": True, "is_update": False,
                  "swaps_conversation": False, "session_id": "x" * 32,
                  "dest_path": os.path.join(dirs["dorm"], "local_done.json"),
                  "post_b64": ccs.b64(b"{}"), "pre_b64": None,
                  "displaced_orphan": None, "displaced_session": None})
os.remove(os.path.join(dirs["dorm"], "local_two.json"))
try:
    ccs.run_sync(env, m)
    check("a resumed op does not claim nothing was written", False, "it wrote")
except ccs.Refusal as exc:
    msg = str(exc)
    check("a resumed op does not claim nothing was written",
          "Nothing has been written" not in msg, msg[:90])
    check("  it names how many rows an earlier run landed",
          "already written 1 row(s) on an earlier run" in msg, msg[-150:])
    # A refusal that names no exit is how a recoverable state gets reported as
    # a deadlock. Both routes below were verified to work from exactly this
    # state before the wording was written.
    check("  and names --back as a route out, with the op id",
          "--back --apply" in msg and "recover --id" in msg, msg[-170:])
    check("  and re-running sync as the other route",
          "re-run sync to re-plan" in msg, msg[-170:])
shutil.rmtree(root, ignore_errors=True)


# ==========================================================================
# F. a store that blinks is not a store that is gone
# ==========================================================================
print("\n--- F. bounded retry on an unreadable store ---")

# Failing closed on an unreadable store is right. Failing closed WITHOUT a
# retry turns a network drive blinking for a moment into a hard stop on an
# operation that already passed planning - raised by both review engines.
_backoff = ccs.STORE_READ_BACKOFF
ccs.STORE_READ_BACKOFF = 0                       # keep the suite fast

root, env, dirs, projects = build({OLD: shared, NEW: shared + prose(4, "n")})
row(dirs["live"], "local_one.json", NEW, "Shared slot")
row(dirs["dorm"], "local_one.json", OLD, "Shared slot", when=4000)
row(dirs["third"], "local_other.json", OLD, "Third door", when=4000)
third_dir = dirs["third"]
real_listdir = os.listdir
state = {"fails": 1}


def blinking_listdir(p):
    same = os.path.normcase(os.path.abspath(p)) == os.path.normcase(
        os.path.abspath(third_dir))
    if same and state["fails"] > 0:
        state["fails"] -= 1
        raise OSError(5, "the drive blinked")
    return real_listdir(p)


ccs.os.listdir = blinking_listdir
try:
    m = ccs.plan_sync(env, ccs.SyncFlags(update=True, allow_orphan=True,
                                         to=dirs["dorm"]))
finally:
    ccs.os.listdir = real_listdir
r = [x for x in m["rows"] if x["title"] == "Shared slot"][0]
check("one failed read is retried, not reported as unreadable",
      r["displaced_orphan"] is False, str(r["displaced_orphan"]))
check("  so the voucher in that store still counts", state["fails"] == 0)
shutil.rmtree(root, ignore_errors=True)

# a store that is genuinely gone still fails closed after the attempts
root, env, dirs, projects = build({OLD: shared, NEW: shared + prose(4, "n")})
row(dirs["live"], "local_one.json", NEW, "Shared slot")
row(dirs["dorm"], "local_one.json", OLD, "Shared slot", when=4000)
row(dirs["third"], "local_other.json", OLD, "Third door", when=4000)
third_dir = dirs["third"]
attempts = {"n": 0}


def dead_listdir(p):
    if os.path.normcase(os.path.abspath(p)) == os.path.normcase(
            os.path.abspath(third_dir)):
        attempts["n"] += 1
        raise OSError(13, "permission denied")
    return real_listdir(p)


ccs.os.listdir = dead_listdir
try:
    m = ccs.plan_sync(env, ccs.SyncFlags(update=True, allow_orphan=True,
                                         to=dirs["dorm"]))
finally:
    ccs.os.listdir = real_listdir
r = [x for x in m["rows"] if x["title"] == "Shared slot"][0]
check("a permanently unreadable store still yields 'unknown'",
      r["displaced_orphan"] == "unknown", str(r["displaced_orphan"]))
check("  after exactly STORE_READ_ATTEMPTS tries, not more",
      attempts["n"] == ccs.STORE_READ_ATTEMPTS,
      "%d tries, expected %d" % (attempts["n"], ccs.STORE_READ_ATTEMPTS))
shutil.rmtree(root, ignore_errors=True)

ccs.STORE_READ_BACKOFF = _backoff


# ==========================================================================
# G. the "stalled op deadlock" - reproduced, and shown not to be one
# ==========================================================================
print("\n--- G. a stalled op whose voucher vanished has ways out ---")

# Filed after the 0.9.15 panel as a high-severity deadlock: rows written, the
# process dies, a voucher disappears, and the apply-time re-check then refuses
# the remainder - "permanently stuck in the exact partially-applied state the
# up-front check was designed to avoid". Reproduced here, and it is not stuck.
# Every assertion below is the executable form of that investigation, so the
# claim cannot drift back into the record unchallenged.
PLAIN = "%032d" % 41


def stalled():
    """Crash mid-op with one row landed, then lose the swap row's voucher."""
    root, env, dirs, projects = build({OLD: shared,
                                       NEW: shared + prose(4, "n"),
                                       PLAIN: prose(6, "p")})
    row(dirs["live"], "local_add.json", PLAIN, "Plain add")
    row(dirs["live"], "local_swap.json", NEW, "Swap slot")
    row(dirs["dorm"], "local_swap.json", OLD, "Swap slot", when=4000)
    row(dirs["dorm"], "local_voucher.json", OLD, "Voucher", when=4000)
    m = ccs.plan_sync(env, ccs.SyncFlags(update=True, to=dirs["dorm"]))
    real = ccs.atomic_write

    def die_on_swap(path, data):
        if "swap" in os.path.basename(path):
            raise KeyboardInterrupt("simulated kill mid-op")
        return real(path, data)

    ccs.atomic_write = die_on_swap
    try:
        ccs.run_sync(env, m)
    except BaseException:                      # noqa: BLE001 - the simulated kill
        pass
    finally:
        ccs.atomic_write = real
    os.remove(os.path.join(dirs["dorm"], "local_voucher.json"))
    return root, env, dirs


root, env, dirs = stalled()
pending = ccs.nonterminal_ops(env)
check("the op is left non-terminal after the kill", len(pending) == 1)
c = ccs.classify_op(env, pending[0])
check("  at status 'writing', with rows written and pending",
      c["status"] == "writing", c["status"])
check("  and recover offers BOTH directions",
      sorted(c["resolutions"]) == ["back", "forward"], str(c["resolutions"]))
shutil.rmtree(root, ignore_errors=True)

# forward is refused - it would orphan a conversation nobody reviewed
root, env, dirs = stalled()
try:
    ccs.recover_op(env, ccs.nonterminal_ops(env)[0], "forward")
    check("recover --forward refuses - that part of the report was right",
          False, "it completed")
except ccs.Refusal as exc:
    check("recover --forward refuses - that part of the report was right",
          "reachability changed" in str(exc), str(exc)[:70])
shutil.rmtree(root, ignore_errors=True)

# ...but back is a real way out, and it closes the op
root, env, dirs = stalled()
res = ccs.recover_op(env, ccs.nonterminal_ops(env)[0], "back")
check("recover --back succeeds - so it is NOT a deadlock",
      res == "rolled_back", str(res))
check("  and the op is closed afterwards", not ccs.nonterminal_ops(env))
shutil.rmtree(root, ignore_errors=True)

# ...and so is simply re-planning, which holds the row back rather than
# writing it - the orphan guard doing exactly its job on the fresh plan
root, env, dirs = stalled()
m2 = ccs.plan_sync(env, ccs.SyncFlags(update=True, to=dirs["dorm"]))
check("a fresh plan still works while the stalled op sits there",
      isinstance(m2.get("rows"), list))
check("  and it HOLDS the swap back rather than writing it",
      "Swap slot" in (m2["tally"].get("held_orphan") or []),
      str(m2["tally"].get("held_orphan")))
check("  so applying that plan writes no swap row",
      not any(r["title"] == "Swap slot" for r in m2["rows"]))
shutil.rmtree(root, ignore_errors=True)


# ==========================================================================
# H. conversations that share a title, with overlap reported BOTH ways
# ==========================================================================
print("\n--- H. duplicate titles: containment is asymmetric, so say both ---")

# SUB is an earlier segment wholly inside SUPER. Reporting a bare "100%
# contained" without naming WHICH is contained lets a reader delete the
# superset - so every pair carries both directions and each number names its
# own subject.
SUB, SUPER, OTHER = "%032d" % 71, "%032d" % 72, "%032d" % 73
shared = prose(20, "s")
root, env, dirs, projects = build({SUB: shared,
                                   SUPER: shared + prose(20, "x"),
                                   OTHER: prose(15, "z")})
row(dirs["live"], "local_a.json", SUB, "Same name")
row(dirs["live"], "local_b.json", SUPER, "Same name")
row(dirs["live"], "local_c.json", OTHER, "Different name")
rep = ccs.gather_doctor(env)
groups = {g["title"]: g for g in rep["duplicate_titles"]}
check("a shared title is reported", "Same name" in groups, str(list(groups)))
check("  and a unique one is NOT", "Different name" not in groups)
g = groups.get("Same name", {"pairs": [], "sessions": []})
check("  with one pair for two conversations", len(g["pairs"]) == 1)
p = g["pairs"][0] if g["pairs"] else {}
a_in_b = p.get("a_in_b") if p.get("a") == SUB else p.get("b_in_a")
b_in_a = p.get("b_in_a") if p.get("a") == SUB else p.get("a_in_b")
check("  the SUBSET reports ~100% of itself inside the superset",
      a_in_b is not None and a_in_b >= 99, str(a_in_b))
check("  the SUPERSET reports only ~half of itself inside the subset",
      b_in_a is not None and 40 <= b_in_a <= 60, str(b_in_a))
check("  which is the whole point - one number alone is ambiguous",
      a_in_b != b_in_a)
check("  and doctor's exit code is NOT affected by a duplicate title",
      rep["exit_code"] == 0, str(rep["exit_code"]))
shutil.rmtree(root, ignore_errors=True)

# The same conversation listed in three accounts is ONE conversation. A report
# whose purpose is to reduce noise must not treble it.
root, env, dirs, projects = build({SUB: shared, SUPER: shared + prose(20, "x")})
for d in ("live", "dorm", "third"):
    row(dirs[d], "local_a.json", SUB, "Same name")
    row(dirs[d], "local_b.json", SUPER, "Same name")
rep = ccs.gather_doctor(env)
g = [x for x in rep["duplicate_titles"] if x["title"] == "Same name"][0]
check("a duplicate synced to three accounts is listed once, not three times",
      len(g["sessions"]) == 2, str(len(g["sessions"])))
check("  and yields one pair, not nine", len(g["pairs"]) == 1, str(len(g["pairs"])))
shutil.rmtree(root, ignore_errors=True)

# "Not compared" must never render as 0%. A transcript past the comparison cap
# shares an unknown amount, and printing a zero reads as "shares nothing" -
# the one conclusion most likely to delete the wrong conversation.
root, env, dirs, projects = build({SUB: shared, SUPER: shared + prose(20, "x")})
row(dirs["live"], "local_a.json", SUB, "Same name")
row(dirs["live"], "local_b.json", SUPER, "Same name")
real_cap = ccs.TRANSCRIPT_COMPARE_MAX_BYTES
ccs.TRANSCRIPT_COMPARE_MAX_BYTES = 1        # everything is now "too large"
try:
    rep = ccs.gather_doctor(env)
finally:
    ccs.TRANSCRIPT_COMPARE_MAX_BYTES = real_cap
g = [x for x in rep["duplicate_titles"] if x["title"] == "Same name"][0]
check("an uncomparable transcript reports turns as unknown, not zero",
      all(s["turns"] is None for s in g["sessions"]),
      str([s["turns"] for s in g["sessions"]]))
check("  saying WHY it could not be compared",
      all(s["unmeasured"] == "too large to compare" for s in g["sessions"]),
      str([s["unmeasured"] for s in g["sessions"]]))
check("  and the pair is marked not-compared rather than 0%",
      g["pairs"][0]["unmeasured"] is True
      and g["pairs"][0]["a_in_b"] is None,
      str(g["pairs"][0]))
shutil.rmtree(root, ignore_errors=True)

# Four conversations under one name produce all six pairs, both ways each.
A4, B4, C4, D4 = ["%032d" % n for n in (74, 75, 76, 77)]
root, env, dirs, projects = build({A4: prose(10, "a"), B4: prose(10, "b"),
                                   C4: prose(10, "c"), D4: prose(10, "d")})
for i, s in enumerate((A4, B4, C4, D4)):
    row(dirs["live"], "local_%d.json" % i, s, "Four of these")
g = [x for x in ccs.gather_doctor(env)["duplicate_titles"]
     if x["title"] == "Four of these"][0]
check("four conversations yield all six pairs", len(g["pairs"]) == 6,
      str(len(g["pairs"])))
check("  every pair carries BOTH directions",
      all(p["a_in_b"] is not None and p["b_in_a"] is not None for p in g["pairs"]))
check("  and unrelated conversations report ~0 both ways",
      all(p["a_in_b"] == 0 and p["b_in_a"] == 0 for p in g["pairs"]),
      str([(p["a_in_b"], p["b_in_a"]) for p in g["pairs"]][:3]))
shutil.rmtree(root, ignore_errors=True)

# A row this tool cannot parse must not take the report down with it.
root, env, dirs, projects = build({SUB: shared, SUPER: shared + prose(20, "x")})
row(dirs["live"], "local_a.json", SUB, "Same name")
row(dirs["live"], "local_b.json", SUPER, "Same name")
with open(os.path.join(dirs["live"], "local_broken.json"), "w") as fh:
    fh.write("{not json")
try:
    rep = ccs.gather_doctor(env)
    check("an unparseable row does not break the duplicate report",
          any(x["title"] == "Same name" for x in rep["duplicate_titles"]))
except BaseException as exc:                       # noqa: BLE001 - that is the bug
    check("an unparseable row does not break the duplicate report", False,
          type(exc).__name__)
shutil.rmtree(root, ignore_errors=True)

print()
print("ALL PASS" if all(ok) else "SOME CHECKS FAILED")
sys.exit(0 if all(ok) else 1)
