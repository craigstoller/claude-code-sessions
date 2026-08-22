"""`ccs repoint`, and doctor's ranking of conversations nothing points at.

Both exist because of one incident, 2026-08-21. A sidebar entry is one app
session whose transcript id changes every time it is resumed, so opening it
under a different account continued an older branch and the app stamped that id
into whichever store was active - leaving a 32 MB conversation on disk that no
account's sidebar could reach. Nothing this tool did caused it and no guard it
has could have stopped it: the mutation never went through the tool.

What the tool COULD have done is say so. `doctor` already knew - it printed
"transcript ... has no listing row" for all 155 unreferenced transcripts, in a
569-line report, undifferentiated. So the fix was not a new check but a usable
one, and a command to put a pointer back.
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


LIVE, DORM = "a" * 32, "b" * 32
ORG_L, ORG_D = "1" * 32, "2" * 32
# UUID-SHAPED on purpose. These were 32 plain digits, which redact() does not
# recognise as a session id - so the doctor block was tested against ids that
# could not trigger the very redaction that broke the workflow in 0.9.11. A
# fixture that does not look like production data verifies a path production
# does not take.
def _sid(n):
    h = "%032x" % n
    return "-".join((h[:8], h[8:12], h[12:16], h[16:20], h[20:]))


OLD, NEW = _sid(0x41), _sid(0x42)


def build(rows=(("ACME-REVIEW session", OLD),)):
    """A live store holding rows, plus two transcripts to point between."""
    root = tempfile.mkdtemp(prefix="rp-")
    home = os.path.join(root, "home")
    store = os.path.join(root, "Claude", "claude-code-sessions")
    live_dir = os.path.join(store, LIVE, ORG_L)
    dorm_dir = os.path.join(store, DORM, ORG_D)
    for d in (live_dir, dorm_dir, home):
        os.makedirs(d)
    projects = os.path.join(home, ".claude", "projects", "proj")
    os.makedirs(projects)
    for sid, n in ((OLD, 3), (NEW, 9)):
        with open(os.path.join(projects, sid + ".jsonl"), "w", encoding="utf-8") as fh:
            for i in range(n):
                fh.write(json.dumps({"type": "user", "timestamp": "t%d" % i,
                                     "message": {"content": "msg %d" % i}}) + "\n")
    for i, (title, sid) in enumerate(rows):
        with open(os.path.join(live_dir, "local_slot%d.json" % i), "w") as fh:
            json.dump({"appSessionId": "slot%d" % i, "cliSessionId": sid,
                       "title": title, "cwd": projects, "lastActivityAt": 1000}, fh)
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
    return root, env, os.path.join(live_dir, "local_slot0.json")


def sid_of(path):
    return json.load(open(path, encoding="utf-8"))["cliSessionId"]


# ---------------------------------------------------------------- planning
root, env, row = build()
m = ccs.plan_repoint(env, ccs.RepointFlags(only="KRIS", to_session=NEW))
check("plans against the live store by default", m["op_type"] == "repoint")
check("  naming the row it found", m["name"] == "local_slot0.json", m["name"])
check("  the pointer it would replace", m["from_session"] == OLD)
check("  and the one it would install", m["to_session"] == NEW)
check("  planning writes nothing", sid_of(row) == OLD)
check("  the post-image differs ONLY in cliSessionId",
      {k: v for k, v in json.loads(ccs.unb64(m["rows"][0]["post_b64"]).decode()).items()
       if k != "cliSessionId"} ==
      {k: v for k, v in json.loads(ccs.unb64(m["rows"][0]["pre_b64"]).decode()).items()
       if k != "cliSessionId"})

# ---------------------------------------------------------------- refusals
for flags, want, why in (
        (ccs.RepointFlags(only="KRIS"), "--to is required", "no target"),
        (ccs.RepointFlags(to_session=NEW), "--only is required", "no row named"),
        (ccs.RepointFlags(only="KRIS", to_session="%032d" % 99),
         "no transcript on disk", "target does not exist"),
        (ccs.RepointFlags(only="KRIS", to_session=OLD),
         "already opens", "already pointing there"),
        (ccs.RepointFlags(only="nothing-matches-this", to_session=NEW),
         "no row matching", "row not found")):
    try:
        ccs.plan_repoint(env, flags)
        check("refuses: %s" % why, False, "it planned anyway")
    except ccs.Refusal as exc:
        check("refuses: %s" % why, want in str(exc), str(exc)[:70])
shutil.rmtree(root, ignore_errors=True)

# an ambiguous --only must name the candidates rather than pick one
root, env, row = build(rows=(("ACME-REVIEW session", OLD), ("ACME-REVIEW again", OLD)))
try:
    ccs.plan_repoint(env, ccs.RepointFlags(only="KRIS", to_session=NEW))
    check("refuses: two rows match --only", False, "it picked one")
except ccs.Refusal as exc:
    check("refuses: two rows match --only", "matches 2 rows" in str(exc), str(exc)[:60])
    check("  and lists them so one can be named",
          "local_slot0.json" in str(exc) and "local_slot1.json" in str(exc))
shutil.rmtree(root, ignore_errors=True)

# ---------------------------------------------------------------- apply + undo
root, env, row = build()
m = ccs.plan_repoint(env, ccs.RepointFlags(only="KRIS", to_session=NEW))
check("apply repoints the row", ccs.run_repoint(env, m) == "completed")
check("  the row now opens the other conversation", sid_of(row) == NEW, sid_of(row))
op = [o for o in ccs.list_ops(env) if o.manifest["op_id"] == m["op_id"]][0]
check("  and it is journalled as a repoint", op.manifest["op_type"] == "repoint")
check("undo puts the original pointer back", ccs.undo_repoint(env, op) == "undone")
check("  byte-for-byte", sid_of(row) == OLD, sid_of(row))
shutil.rmtree(root, ignore_errors=True)

# the app rewriting the row after the repoint blocks undo, as with sync
root, env, row = build()
m = ccs.plan_repoint(env, ccs.RepointFlags(only="KRIS", to_session=NEW))
ccs.run_repoint(env, m)
d = json.load(open(row, encoding="utf-8"))
d["title"] = "the app touched this"
json.dump(d, open(row, "w", encoding="utf-8"))
theirs = open(row, "rb").read()
op = [o for o in ccs.list_ops(env) if o.manifest["op_id"] == m["op_id"]][0]
try:
    ccs.undo_repoint(env, op)
    check("undo refuses once something else touched the row", False, "it overwrote")
except ccs.Refusal as exc:
    check("undo refuses once something else touched the row",
          "no longer holds" in str(exc), str(exc)[:60])
    check("  and their version survives", open(row, "rb").read() == theirs)
shutil.rmtree(root, ignore_errors=True)

# the running-app guard - the one that was missing when the pointer was lost
root, env, row = build()
env.process_lister = lambda: [(1, r"c:\program files\windowsapps"
                                  r"\claude_1.0_x64__pzs8sxrjxfjjc\app\claude.exe")]
m = ccs.plan_repoint(env, ccs.RepointFlags(only="KRIS", to_session=NEW))
try:
    ccs.run_repoint(env, m)
    check("apply refuses while the desktop app is running", False, "it wrote!")
except ccs.Refusal as exc:
    check("apply refuses while the desktop app is running", "running" in str(exc),
          str(exc)[:60])
    check("  leaving the row untouched", sid_of(row) == OLD)
shutil.rmtree(root, ignore_errors=True)

# a repoint is undoable through the ordinary `undo` candidate filter
root, env, row = build()
m = ccs.plan_repoint(env, ccs.RepointFlags(only="KRIS", to_session=NEW))
ccs.run_repoint(env, m)
cands = [o for o in ccs.list_ops(env)
         if o.manifest.get("status") == "completed"
         and o.manifest.get("op_type", "move") in ("move", "sync", "repoint")]
check("a completed repoint is a candidate for undo", len(cands) == 1)
op = cands[-1]
check("  and classify_op does not treat it as a move",
      ccs.classify_op(env, op)["status"] == "completed")
shutil.rmtree(root, ignore_errors=True)

# ---------------------------------------------------------------- doctor
root, env, row = build()
rep = ccs.gather_doctor(env)
check("doctor sees the transcript nothing points at",
      NEW in rep["unlisted_transcripts"], str(rep["unlisted_transcripts"]))
# The previous version of this asserted `d["mb"] >= 0 and d["age_days"] >= 0`,
# which is true of every possible value - it would have passed with the ranking
# deleted. Assert the thing that matters instead: the entry is present and
# carries a size that matches the file on disk.
entry = [d for d in rep["unlisted_ranked"] if d["session_id"] == NEW]
check("  and ranks it, with a size that matches the file", len(entry) == 1
      and abs(entry[0]["mb"] - os.path.getsize(
          os.path.join(env.projects_root, "proj", NEW + ".jsonl")) / 1e6) < 0.05,
      str(rep["unlisted_ranked"]))
check("  counting how many are recent",
      rep["unlisted_recent"] == len([d for d in rep["unlisted_ranked"]
                                     if d["age_days"] <= 7]),
      "reported %r" % (rep["unlisted_recent"],))
out = []


class NS:
    json = False
    verbose = False


import contextlib  # noqa: E402
import io as _io  # noqa: E402
buf = _io.StringIO()
with contextlib.redirect_stdout(buf):
    ccs.cmd_doctor(env, NS())
txt = buf.getvalue()
check("  the report summarises rather than listing every one",
      "have no listing row in any account" in txt, "(no summary line)")
check("  says most are normal", "most are normal" in txt)
check("  and names the case worth looking at",
      "reachable from no sidebar" in txt)
check("  the old one-line-per-transcript dump is gone",
      "has no listing row" not in txt)
shutil.rmtree(root, ignore_errors=True)

# Ranking: a LARGE recent orphan must not be buried by trivial fresher ones.
# Newest-first defeated itself - investigating a lost conversation starts CLI
# sessions, each minting a fresh unreferenced transcript, so looking for the
# thing you lost is what pushes it off the cap.
root, env, row = build()
projects = os.path.join(env.home, ".claude", "projects", "proj")
big = os.path.join(projects, (_sid(0x44)) + ".jsonl")
with open(big, "w", encoding="utf-8") as fh:
    fh.write("x" * 30_000_000)                      # the 30 MB one, from "yesterday"
os.utime(big, (0, os.path.getmtime(big) - 1 * 86400))
for i in range(12):                                 # a dozen tiny ones from just now
    tiny = os.path.join(projects, _sid(0x50 + i) + ".jsonl")
    with open(tiny, "w", encoding="utf-8") as fh:
        fh.write("{}\n")
rep = ccs.gather_doctor(env)
ranked = [d["session_id"] for d in rep["unlisted_ranked"]]
check("a 30 MB orphan from yesterday survives 12 fresher trivial ones",
      (_sid(0x44)) in ranked, "top: %s" % [r[-2:] for r in ranked])
check("  and outranks them", ranked.index(_sid(0x44)) == 0,
      "position %d" % (ranked.index(_sid(0x44)) if (_sid(0x44)) in ranked else -1))
old_orphan = os.path.join(projects, (_sid(0x43)) + ".jsonl")
with open(old_orphan, "w", encoding="utf-8") as fh:
    fh.write("x" * 5_000_000)
os.utime(old_orphan, (0, os.path.getmtime(old_orphan) - 60 * 86400))
rep = ccs.gather_doctor(env)
ranked = [d["session_id"] for d in rep["unlisted_ranked"]]
check("  a 60-day-old one ranks below everything recent",
      (_sid(0x43)) not in ranked[:1], str(ranked[:1]))

# the printed id must be usable by `repoint --to`, which matches the filename
buf2 = _io.StringIO()
with contextlib.redirect_stdout(buf2):
    ccs.cmd_doctor(env, NS())
dtxt = buf2.getvalue()
# Not just present somewhere - present in the DEFAULT report. 0.9.11 printed
# the full id and redact() shortened it, so --verbose worked and the report
# people actually read did not.
check("doctor prints FULL session ids in the default report",
      _sid(0x44) in dtxt,
      "full" if _sid(0x44) in dtxt else ("prefix only" if _sid(0x44)[:8] in dtxt
                                          else "absent"))
check("  and the ordering label matches the ordering",
      "Largest first" in dtxt and "Newest first" not in dtxt,
      "Largest first" if "Largest first" in dtxt else "wrong/missing label")
check("  and names the command that uses them", "repoint --only" in dtxt)
shutil.rmtree(root, ignore_errors=True)

# ------------------------------------------------- findings from the panel
# --apply --json must APPLY, not print a plan and exit 0
root, env, row = build()


class NSJ:
    only = "KRIS"
    to_session = NEW
    store = ""
    live = ""
    apply = True
    json = True
    verbose = False


buf3 = _io.StringIO()
with contextlib.redirect_stdout(buf3):
    ccs.cmd_repoint(env, NSJ())
pub = json.loads(buf3.getvalue())
check("--apply --json actually writes", sid_of(row) == NEW, sid_of(row))
check("  and reports the result", pub.get("result") == "completed", str(pub.get("result")))
check("  without leaking the row images",
      all("pre_b64" not in r and "post_b64" not in r for r in pub["rows"]),
      str(sorted(pub["rows"][0])))
shutil.rmtree(root, ignore_errors=True)

# the target vanishing between plan and apply must refuse, not write a dangler
root, env, row = build()
m = ccs.plan_repoint(env, ccs.RepointFlags(only="KRIS", to_session=NEW))
os.remove(os.path.join(env.projects_root, "proj", NEW + ".jsonl"))
try:
    ccs.run_repoint(env, m)
    check("a target deleted after planning is refused", False, "it wrote a dangler!")
except ccs.Refusal as exc:
    check("a target deleted after planning is refused",
          "no longer on disk" in str(exc), str(exc)[:60])
    check("  leaving the row pointing where it did", sid_of(row) == OLD)
shutil.rmtree(root, ignore_errors=True)

# an interrupted repoint must be finishable rather than jamming the journal
root, env, row = build()
m = ccs.plan_repoint(env, ccs.RepointFlags(only="KRIS", to_session=NEW))
op = ccs.new_op(env, m)
ccs.set_status(op, "journaled")
ccs.set_status(op, "writing")                       # died between the two
check("an interrupted repoint re-enters and completes",
      ccs.execute_repoint_op(env, op) == "completed")
check("  landing the pointer", sid_of(row) == NEW)
shutil.rmtree(root, ignore_errors=True)

# recover must refuse cleanly on a repoint, not traceback on move-shaped keys
root, env, row = build()
m = ccs.plan_repoint(env, ccs.RepointFlags(only="KRIS", to_session=NEW))
op = ccs.new_op(env, m)
ccs.set_status(op, "journaled")
ccs.set_status(op, "writing")
try:
    ccs.recover_op(env, op, "forward")
    check("recover on a repoint does not traceback", True)
except ccs.Refusal:
    check("recover on a repoint does not traceback", True)   # a refusal is fine
except (KeyError, TypeError) as exc:
    check("recover on a repoint does not traceback", False,
          "%s: %s" % (type(exc).__name__, exc))
shutil.rmtree(root, ignore_errors=True)

# --live must actually resolve the store, not be parsed and ignored
root, env, row = build()
# make the identity files disagree, which is the state --live exists for
with open(os.path.join(env.home, ".claude.json"), "w") as fh:
    json.dump({"oauthAccount": {"accountUuid": DORM, "organizationUuid": ORG_D,
                                "emailAddress": "dorm@example.com"}}, fh)
try:
    ccs.plan_repoint(env, ccs.RepointFlags(only="KRIS", to_session=NEW))
    check("with the identity files disagreeing, the default store is refused",
          False, "it planned anyway")
except ccs.Refusal as exc:
    check("with the identity files disagreeing, the default store is refused",
          "cannot identify" in str(exc) or "--live" in str(exc), str(exc)[:60])
m = ccs.plan_repoint(env, ccs.RepointFlags(only="KRIS", to_session=NEW, live=LIVE))
check("  and --live resolves it instead of being ignored",
      m["store_path"].endswith(os.path.join(LIVE, ORG_L)), m["store_path"][-40:])
shutil.rmtree(root, ignore_errors=True)

print()
print("ALL PASS" if all(ok) else "SOME CHECKS FAILED")
sys.exit(0 if all(ok) else 1)
