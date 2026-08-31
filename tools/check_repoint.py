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
ORG_L2 = "3" * 32                       # a SECOND org under the live account
# UUID-SHAPED on purpose. These were 32 plain digits, which redact() does not
# recognise as a session id - so the doctor block was tested against ids that
# could not trigger the very redaction that broke the workflow in 0.9.11. A
# fixture that does not look like production data verifies a path production
# does not take.
def _sid(n):
    h = "%032x" % n
    return "-".join((h[:8], h[8:12], h[12:16], h[16:20], h[20:]))


OLD, NEW = _sid(0x41), _sid(0x42)


def build(rows=(("ACME-REVIEW session", OLD),), live_orgs=(ORG_L,)):
    """A live store holding rows, plus two transcripts to point between.

    LIVE_ORGS may name more than one org under the live account. That is the
    shape --store has to tell apart and the shape a real machine has - one
    account, one store per org - so the store-matching block below builds it.
    Rows always land in the first.
    """
    root = tempfile.mkdtemp(prefix="rp-")
    home = os.path.join(root, "home")
    store = os.path.join(root, "Claude", "claude-code-sessions")
    live_dir = os.path.join(store, LIVE, live_orgs[0])
    dorm_dir = os.path.join(store, DORM, ORG_D)
    for d in [os.path.join(store, LIVE, o) for o in live_orgs] + [dorm_dir, home]:
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
        json.dump({"oauthAccount": {"accountUuid": LIVE,
                                    "organizationUuid": live_orgs[0],
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
m = ccs.plan_repoint(env, ccs.RepointFlags(only="ACME", to_session=NEW))
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
        (ccs.RepointFlags(only="ACME"), "--to is required", "no target"),
        (ccs.RepointFlags(to_session=NEW), "--only is required", "no row named"),
        (ccs.RepointFlags(only="ACME", to_session="%032d" % 99),
         "no transcript on disk", "target does not exist"),
        (ccs.RepointFlags(only="ACME", to_session=OLD),
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
    ccs.plan_repoint(env, ccs.RepointFlags(only="ACME", to_session=NEW))
    check("refuses: two rows match --only", False, "it picked one")
except ccs.Refusal as exc:
    check("refuses: two rows match --only", "matches 2 rows" in str(exc), str(exc)[:60])
    check("  and lists them so one can be named",
          "local_slot0.json" in str(exc) and "local_slot1.json" in str(exc))
shutil.rmtree(root, ignore_errors=True)

# ---------------------------------------------------------------- apply + undo
root, env, row = build()
m = ccs.plan_repoint(env, ccs.RepointFlags(only="ACME", to_session=NEW))
check("apply repoints the row", ccs.run_repoint(env, m) == "completed")
check("  the row now opens the other conversation", sid_of(row) == NEW, sid_of(row))
op = [o for o in ccs.list_ops(env) if o.manifest["op_id"] == m["op_id"]][0]
check("  and it is journalled as a repoint", op.manifest["op_type"] == "repoint")
check("undo puts the original pointer back", ccs.undo_repoint(env, op) == "undone")
check("  byte-for-byte", sid_of(row) == OLD, sid_of(row))
shutil.rmtree(root, ignore_errors=True)

# the app rewriting the row after the repoint blocks undo, as with sync
root, env, row = build()
m = ccs.plan_repoint(env, ccs.RepointFlags(only="ACME", to_session=NEW))
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
m = ccs.plan_repoint(env, ccs.RepointFlags(only="ACME", to_session=NEW))
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
m = ccs.plan_repoint(env, ccs.RepointFlags(only="ACME", to_session=NEW))
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
    only = "ACME"
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
m = ccs.plan_repoint(env, ccs.RepointFlags(only="ACME", to_session=NEW))
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
m = ccs.plan_repoint(env, ccs.RepointFlags(only="ACME", to_session=NEW))
op = ccs.new_op(env, m)
ccs.set_status(op, "journaled")
ccs.set_status(op, "writing")                       # died between the two
check("an interrupted repoint re-enters and completes",
      ccs.execute_repoint_op(env, op) == "completed")
check("  landing the pointer", sid_of(row) == NEW)
shutil.rmtree(root, ignore_errors=True)

# recover must refuse cleanly on a repoint, not traceback on move-shaped keys
root, env, row = build()
m = ccs.plan_repoint(env, ccs.RepointFlags(only="ACME", to_session=NEW))
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
    ccs.plan_repoint(env, ccs.RepointFlags(only="ACME", to_session=NEW))
    check("with the identity files disagreeing, the default store is refused",
          False, "it planned anyway")
except ccs.Refusal as exc:
    check("with the identity files disagreeing, the default store is refused",
          "cannot identify" in str(exc) or "--live" in str(exc), str(exc)[:60])
m = ccs.plan_repoint(env, ccs.RepointFlags(only="ACME", to_session=NEW, live=LIVE))
check("  and --live resolves it instead of being ignored",
      m["store_path"].endswith(os.path.join(LIVE, ORG_L)), m["store_path"][-40:])
shutil.rmtree(root, ignore_errors=True)

# ------------------------------------------------------- --store matching
# Two shipped gaps, neither covered: this file exercised no --store matching at
# all, so both branches below had never been run by a passing test. Found
# 2026-08-23 while building `new-row`, whose own store picker hands out the
# same advice this one does.
root, env, _row = build(live_orgs=(ORG_L, ORG_L2))
ccs.remember_account_email(env, DORM, "dorm@example.com")


def picks(want):
    """The (account, org) prefixes --store WANT selects, sorted.

    A Refusal comes back as ["(matched nothing)"] rather than propagating: not
    matching IS the failure under test, and letting it raise would end the run
    at the first gap instead of reporting both.
    """
    try:
        hits = ccs._repoint_store(env, ccs.RepointFlags(store=want))
    except ccs.Refusal:
        return ["(matched nothing)"]
    return sorted((a[:8], o[:8]) for a, o, _p in hits)


BOTH_LIVE = sorted((LIVE[:8], o[:8]) for o in (ORG_L, ORG_L2))
ONLY_DORM = [(DORM[:8], ORG_D[:8])]

# GAP 1. account_email answers for a DORMANT account - the sandbox config, then
# the memo - and the signed-in account's email is in neither: it lives in
# ~/.claude.json's oauthAccount, which only live_account reads. So the one
# account this command DEFAULTS to, and the one a person is likeliest to name,
# was the single account whose own email matched nothing. Invisible on a
# machine that already holds a memo for every account, which is why it shipped.
check("--store matches the live account's own email",
      picks("live@example.com") == BOTH_LIVE, str(picks("live@example.com")))
check("  without dragging the other account in",
      ONLY_DORM[0] not in picks("live@example.com"))
check("  a dormant account's email still matches it",
      picks("dorm@example.com") == ONLY_DORM, str(picks("dorm@example.com")))
check("  an account uuid matches every org under it",
      picks(LIVE) == BOTH_LIVE, str(picks(LIVE)))
check("  an org uuid matches just the one store",
      picks(ORG_L2) == [(LIVE[:8], ORG_L2[:8])], str(picks(ORG_L2)))

# GAP 2. os.path.normcase lower-cases AND turns "/" into "\" on Windows, and
# the candidate then had its separators flipped back to "/" - while `want` was
# only .lower()ed, so the backslashes in a path copied out of this tool's own
# listing survived and could never match. A user pasting what we printed got
# "matched no store on this machine".
native = os.path.join(env.store_candidates[0], LIVE, ORG_L)
check("--store matches a path spelled with this platform's own separators",
      picks(native) == [(LIVE[:8], ORG_L[:8])], native)
check("  and the same path spelled with forward slashes",
      picks(native.replace(os.sep, "/")) == [(LIVE[:8], ORG_L[:8])])
check("  a path naming the account alone matches both its orgs",
      picks(os.path.join(env.store_candidates[0], LIVE)) == BOTH_LIVE)

try:
    ccs._repoint_store(env, ccs.RepointFlags(store="no-such-store"))
    check("--store still refuses what it cannot find", False, "it matched something")
except ccs.Refusal as exc:
    check("--store still refuses what it cannot find",
          "matched no store" in str(exc), str(exc)[:60])

# The same lookup labels the plan, so gap 1 also printed eight hex characters
# for the store the user is looking at.
m = ccs.plan_repoint(env, ccs.RepointFlags(only="ACME", to_session=NEW))
check("the plan names the live store by email rather than a hex prefix",
      m["store_label"].startswith("live@example.com"), m["store_label"])
shutil.rmtree(root, ignore_errors=True)


# --------------------------------------------------------- recovery, 2026-08-23
# A stuck repoint had NO exit. classify_op returned resolutions: [] and
# cmd_recover's gate turned that into a refusal, while non-terminal ops are
# never rotated - so the record sat in the journal forever, holding doctor at
# exit 1, with the only advice being "run repoint again", which starts a new op
# and closes nothing. Six accumulated on one machine before anyone tried to
# clear them.
print("\n--- a stuck repoint has an exit ---")

DESKTOP = (1, r"c:\program files\windowsapps"
              r"\claude_1.0_x64__pzs8sxrjxfjjc\app\claude.exe")


def torn(landed=True, drift=False):
    """A repoint left non-terminal, as a crash between write and marker does."""
    root, env, row = build()
    m = ccs.plan_repoint(env, ccs.RepointFlags(only="ACME", to_session=NEW))
    op = ccs.new_op(env, m)
    ccs.set_status(op, "journaled")
    if landed:
        ccs.atomic_write(m["rows"][0]["dest_path"], ccs.unb64(m["rows"][0]["post_b64"]))
    if drift:                       # as the app does: touch a field we never set
        d = json.load(open(row, encoding="utf-8"))
        d["lastFocusedAt"] = 4242
        with open(row, "w") as fh:
            json.dump(d, fh)
    return root, env, row, m, ccs.nonterminal_ops(env)[0]


# 1. the write landed, the marker did not
root, env, row, m, op = torn(landed=True)
c = ccs.classify_op(env, op)
check("a landed-but-unmarked repoint offers both directions",
      sorted(c["resolutions"]) == ["back", "forward"], str(c["resolutions"]))
check("  and says the row holds what this op would write",
      "holds exactly what this operation would write" in c["note"],
      c["note"][:70])
env.process_lister = lambda: [DESKTOP]
try:
    _r = ccs.recover_op(env, op, "forward")
except BaseException as _e:                     # noqa: BLE001 - report, do not abort
    _r = "%s: %s" % (type(_e).__name__, str(_e)[:50])
check("  forward finishes it even with the app running - journal only",
      _r == "completed", str(_r))
check("  leaving the row at the new pointer", sid_of(row) == NEW, sid_of(row))
check("  and nothing pending", not ccs.nonterminal_ops(env))
shutil.rmtree(root, ignore_errors=True)

# 2. back on the same state restores the original pointer
root, env, row, m, op = torn(landed=True)
check("back puts the original pointer back",
      ccs.recover_op(env, op, "back") == "rolled_back")
check("  byte-for-byte", sid_of(row) == OLD, sid_of(row))
check("  with no residue - it reversed cleanly",
      not [o for o in ccs.list_ops(env) if o.manifest.get("rollback_residue")])
shutil.rmtree(root, ignore_errors=True)

# 3. back refuses to touch the store while the app is running
root, env, row, m, op = torn(landed=True)
env.process_lister = lambda: [DESKTOP]
try:
    ccs.recover_op(env, op, "back")
    check("back refuses to restore while the app runs", False, "it wrote")
except ccs.Refusal as exc:
    check("back refuses to restore while the app runs",
          "desktop app" in str(exc), str(exc)[:70])
check("  leaving the row alone", sid_of(row) == NEW)
shutil.rmtree(root, ignore_errors=True)

# 4. the write never landed: back closes clean, and touches nothing
root, env, row, m, op = torn(landed=False)
before = open(row, "rb").read()
check("a repoint that never wrote closes on back",
      ccs.recover_op(env, op, "back") == "rolled_back")
check("  without touching the row", open(row, "rb").read() == before)
check("  and with no residue, because it left nothing behind",
      not [o for o in ccs.list_ops(env) if o.manifest.get("rollback_residue")])
check("  and nothing is left pending", not ccs.nonterminal_ops(env))
shutil.rmtree(root, ignore_errors=True)

# 5. THE POINTER IS THE EVIDENCE, NOT THE BYTES. The app rewrites fields this
# op never set, so a row it never touched reads as "drifted" within a day. Six
# real stuck ops looked exactly like this.
root, env, row, m, op = torn(landed=False, drift=True)
check("a row the app touched reads as drifted",
      ccs._sync_row_drift(m["rows"][0]) == "drifted")
check("  but the pointer says the write never landed",
      ccs._repoint_landed(m, m["rows"][0]) is False)
c = ccs.classify_op(env, op)
# Only `back`. execute_repoint_op compares BYTES and refuses a row that changed
# since planning, so offering `forward` here would advertise a resolution that
# always refuses and send the user back into the re-run loop this branch exists
# to break. The pointer settles what happened; it does not make forward safe.
check("  so classify_op offers only back",
      c["resolutions"] == ["back"], str(c["resolutions"]))
check("  and says the row still opens what it opened before",
      "still opens what it opened before" in c["note"], c["note"][:80])
before = open(row, "rb").read()
check("  back closes it", ccs.recover_op(env, op, "back") == "rolled_back")
check("  touching nothing", open(row, "rb").read() == before)
check("  with no residue - our write never took effect",
      not [o for o in ccs.list_ops(env) if o.manifest.get("rollback_residue")])
shutil.rmtree(root, ignore_errors=True)

# 6. the write landed AND the app then changed the row: back cannot cleanly
# reverse it, so it says so rather than discarding the app's own edits.
root, env, row, m, op = torn(landed=True, drift=True)
check("a landed-then-changed row is drifted",
      ccs._sync_row_drift(m["rows"][0]) == "drifted")
check("  and the pointer says our write DID land",
      ccs._repoint_landed(m, m["rows"][0]) is True)
c = ccs.classify_op(env, op)
check("  so only back is offered", c["resolutions"] == ["back"], str(c["resolutions"]))
before = open(row, "rb").read()
try:
    _r = ccs.recover_op(env, op, "back")
except BaseException as _e:                     # noqa: BLE001
    _r = "%s: %s" % (type(_e).__name__, str(_e)[:50])
check("  back closes it", _r == "rolled_back", str(_r))
check("  leaving the row alone rather than discarding what changed it",
      open(row, "rb").read() == before)
res = [o.manifest.get("rollback_residue") for o in ccs.list_ops(env)
       if o.manifest.get("rollback_residue")]
check("  and RECORDING that it left something behind", len(res) == 1, str(res)[:90])
shutil.rmtree(root, ignore_errors=True)

# 7. back always terminates, whatever the row is doing - it is the only exit.
for label, wreck in (("row deleted", lambda p: os.unlink(p)),
                     ("row replaced with junk",
                      lambda p: open(p, "w").write("{not json")),
                     ("row is a directory",
                      lambda p: (os.unlink(p), os.makedirs(p)))):
    root, env, row, m, op = torn(landed=True)
    wreck(row)
    try:
        res = ccs.recover_op(env, op, "back")
        check("back terminates when the %s" % label, res == "rolled_back", str(res))
        check("  leaving nothing pending", not ccs.nonterminal_ops(env))
    except (ccs.Refusal, ccs.LayoutError) as exc:
        check("back terminates when the %s" % label, False,
              "Refusal: " + str(exc)[:56])
        check("  leaving nothing pending", False, "refused")
    except BaseException as exc:                    # noqa: BLE001 - that is the bug
        check("back terminates when the %s" % label, False,
              "ESCAPED %s" % type(exc).__name__)
        check("  leaving nothing pending", False, "escaped")
    shutil.rmtree(root, ignore_errors=True)

# 8. _repoint_landed never raises - classify_op depends on that.
root, env, row, m, op = torn(landed=True)
for bad in ({}, {"dest_path": row + ".missing"}, {"dest_path": 17}):
    try:
        ccs._repoint_landed(m, bad)
        check("_repoint_landed survives %r" % (sorted(bad) or "an empty row"), True)
    except BaseException as exc:                    # noqa: BLE001
        check("_repoint_landed survives %r" % (sorted(bad) or "an empty row"),
              False, type(exc).__name__)
shutil.rmtree(root, ignore_errors=True)


# --- back must not reverse a LATER completed repoint of the same row ---------
# plan_repoint builds its post-image as a function of the row, so a second
# repoint of the same row at the same target writes byte-identical bytes. "The
# row matches our post-image" therefore means EITHER we wrote it OR somebody
# else wrote the same thing - and restoring our pre-image in the second case
# silently reverses a completed operation whose own undo then refuses.
#
# This is the sequence the tool's own pre-fix advice produced: a stalled repoint
# said "run repoint again", the re-run completed, and clearing the stale record
# would undo the re-run. One real journal held exactly it - two stuck ops and
# one completed op on a single row.
print("\n--- back does not reverse a later completed repoint ---")

root, env, row = build()
m1 = ccs.plan_repoint(env, ccs.RepointFlags(only="ACME", to_session=NEW))
op1 = ccs.new_op(env, m1)
ccs.set_status(op1, "journaled")                  # stalls, never writes
m2 = ccs.plan_repoint(env, ccs.RepointFlags(only="ACME", to_session=NEW))
check("the re-run completes", ccs.run_repoint(env, m2) == "completed")
check("  and the row opens the new conversation", sid_of(row) == NEW, sid_of(row))

# new_op does not stamp op_id onto the caller's dict - only run_* does - so the
# stalled op's id has to come from the Op, not from the manifest passed in.
op1_id = op1.manifest["op_id"]
stuck = [o for o in ccs.nonterminal_ops(env) if o.manifest["op_id"] == op1_id][0]
check("the stuck op's row matches its post-image byte for byte",
      ccs._sync_row_drift(stuck.manifest["rows"][0]) == "match")
check("  and the later completed op IS identified",
      ccs._repoint_claimed_later(env, stuck.manifest,
                                 stuck.manifest["rows"][0]) == m2["op_id"],
      str(ccs._repoint_claimed_later(env, stuck.manifest,
                                     stuck.manifest["rows"][0])))
c = ccs.classify_op(env, stuck)
check("  and the note SAYS so rather than promising a clean reversal",
      "wrote that row after this one" in c["note"], c["note"][:110])
before = open(row, "rb").read()
check("back closes the stuck op", ccs.recover_op(env, stuck, "back") == "rolled_back")
check("  WITHOUT reverting the completed op's work", sid_of(row) == NEW, sid_of(row))
check("  leaving the row byte-identical", open(row, "rb").read() == before)
# DECLINED, not residue - and the distinction is what keeps doctor honest.
# Residue means "we left something we wrote that nothing else tracks", which is
# why doctor reports it and why _collides pins the op. Here the row belongs to
# an operation that IS tracked and IS completed, so leaving it is correct rather
# than an anomaly. Filing it as residue made doctor exit 1 permanently and told
# the user to delete a row they wanted.
dec = [o.manifest.get("rollback_declined") for o in ccs.list_ops(env)
       if o.manifest.get("rollback_declined")]
check("  and recording WHY it declined", len(dec) == 1 and m2["op_id"] in dec[0],
      str(dec)[:100])
check("  as DECLINED rather than residue, so doctor stays quiet",
      not [o for o in ccs.list_ops(env) if o.manifest.get("rollback_residue")])
check("  and doctor's exit code is unaffected by it",
      ccs.gather_doctor(env)["exit_code"] == 0,
      str(ccs.gather_doctor(env)["exit_code"]))
later = [o for o in ccs.list_ops(env) if o.manifest["op_id"] == m2["op_id"]][0]
check("  the completed op is untouched and still undoable",
      later.manifest["status"] == "completed")
check("  which it then does", ccs.undo_repoint(env, later) == "undone")
check("  restoring the original pointer", sid_of(row) == OLD, sid_of(row))
shutil.rmtree(root, ignore_errors=True)

# ...and with no later claimant, back still reverses normally.
root, env, row = build()
m = ccs.plan_repoint(env, ccs.RepointFlags(only="ACME", to_session=NEW))
op = ccs.new_op(env, m)
ccs.set_status(op, "journaled")
ccs.atomic_write(m["rows"][0]["dest_path"], ccs.unb64(m["rows"][0]["post_b64"]))
check("with no later op, back still restores the pre-image",
      ccs.recover_op(env, ccs.nonterminal_ops(env)[0], "back") == "rolled_back")
check("  putting the original pointer back", sid_of(row) == OLD, sid_of(row))
shutil.rmtree(root, ignore_errors=True)

# --- a damaged record must not traceback out of recover ---------------------
print("\n--- a damaged repoint record ---")

for label, wreck in (("no rows key", lambda mm: mm.pop("rows")),
                     ("rows empty", lambda mm: mm.__setitem__("rows", [])),
                     ("rows not a list", lambda mm: mm.__setitem__("rows", {})),
                     ("rows a string", lambda mm: mm.__setitem__("rows", "nope")),
                     ("dest_path None",
                      lambda mm: mm["rows"][0].__setitem__("dest_path", None)),
                     ("dest_path an int",
                      lambda mm: mm["rows"][0].__setitem__("dest_path", 17)),
                     ("store_path None",
                      lambda mm: mm.__setitem__("store_path", None)),
                     ("post_b64 missing",
                      lambda mm: mm["rows"][0].pop("post_b64"))):
    root, env, row = build()
    m = ccs.plan_repoint(env, ccs.RepointFlags(only="ACME", to_session=NEW))
    op = ccs.new_op(env, m)
    ccs.set_status(op, "journaled")
    wreck(op.manifest)
    ccs.save_manifest(op)
    o = ccs.nonterminal_ops(env)[0]
    try:
        c = ccs.classify_op(env, o)
        check("classify_op survives %s" % label, "damaged" in c["note"], c["note"][:60])
        check("  offering nothing it cannot run", c["resolutions"] == [])
    except BaseException as exc:                   # noqa: BLE001 - that is the bug
        check("classify_op survives %s" % label, False, type(exc).__name__)
        check("  offering nothing it cannot run", False, "raised")
    for d in ("forward", "back"):
        try:
            ccs.recover_op(env, o, d)
            check("  recover --%s refuses %s" % (d, label), False, "no refusal")
        except ccs.Refusal as exc:
            check("  recover --%s refuses %s" % (d, label),
                  "is damaged" in str(exc), str(exc)[:60])
        except BaseException as exc:               # noqa: BLE001
            check("  recover --%s refuses %s" % (d, label), False,
                  "ESCAPED " + type(exc).__name__)
    shutil.rmtree(root, ignore_errors=True)

# one damaged op must not take the whole listing down with it
root, env, row = build()
m = ccs.plan_repoint(env, ccs.RepointFlags(only="ACME", to_session=NEW))
bad_op = ccs.new_op(env, m)
ccs.set_status(bad_op, "journaled")
bad_op.manifest.pop("rows")
ccs.save_manifest(bad_op)
m2 = ccs.plan_repoint(env, ccs.RepointFlags(only="ACME", to_session=NEW))
good = ccs.new_op(env, m2)
ccs.set_status(good, "journaled")
try:
    notes = [ccs.classify_op(env, o)["note"] for o in ccs.nonterminal_ops(env)]
    check("a damaged op does not take the recover listing down", len(notes) == 2,
          str(len(notes)))
except BaseException as exc:                       # noqa: BLE001
    check("a damaged op does not take the recover listing down", False,
          "ESCAPED " + type(exc).__name__)
shutil.rmtree(root, ignore_errors=True)

# --- from_session None: the pointer cannot say "not landed", pristine can ----
print("\n--- a row that opened nothing before ---")

root, env, row = build()
d = json.load(open(row, encoding="utf-8"))
del d["cliSessionId"]
with open(row, "w") as fh:
    json.dump(d, fh)
m = ccs.plan_repoint(env, ccs.RepointFlags(only="ACME", to_session=NEW))
check("planning against a row that opens nothing works",
      (m.get("from_session") or None) is None, str(m.get("from_session")))
op = ccs.new_op(env, m)
ccs.set_status(op, "journaled")                    # never writes
check("  the untouched row reads as pristine",
      ccs._sync_row_drift(m["rows"][0]) == "pristine")
check("  and the pointer cannot settle it",
      ccs._repoint_landed(m, m["rows"][0]) is None)
c = ccs.classify_op(env, ccs.nonterminal_ops(env)[0])
check("  so pristine carries the answer instead",
      "was not written" in c["note"], c["note"][:80])
check("  and forward is still offered", "forward" in c["resolutions"],
      str(c["resolutions"]))
before = open(row, "rb").read()
check("  back closes it", ccs.recover_op(env, ccs.nonterminal_ops(env)[0],
                                         "back") == "rolled_back")
check("  touching nothing", open(row, "rb").read() == before)
check("  with no residue", not [o for o in ccs.list_ops(env)
                                if o.manifest.get("rollback_residue")])
shutil.rmtree(root, ignore_errors=True)

print()
print("ALL PASS" if all(ok) else "SOME CHECKS FAILED")
sys.exit(0 if all(ok) else 1)
