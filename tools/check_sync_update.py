"""RULING 8 - sync --update, the only route that overwrites rather than adds.

Drives a real refresh end to end on a synthetic store: a stale destination row
is refreshed, undo puts the exact original bytes back, and every way the
destination can move underneath the plan is refused rather than clobbered.
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


def build():
    root = tempfile.mkdtemp(prefix="upd-")
    home = os.path.join(root, "home")
    store = os.path.join(root, "Claude", "claude-code-sessions")
    live_dir = os.path.join(store, LIVE, ORG_L)
    dorm_dir = os.path.join(store, DORM, ORG_D)
    for d in (live_dir, dorm_dir, home):
        os.makedirs(d)
    projects = os.path.join(home, ".claude", "projects", "proj")
    os.makedirs(projects)
    sid = "%032d" % 1
    with open(os.path.join(projects, sid + ".jsonl"), "w") as fh:
        fh.write('{"type":"user"}\n')
    name = "local_%s.json" % sid
    # source: fresh
    with open(os.path.join(live_dir, name), "w") as fh:
        json.dump({"appSessionId": sid, "cliSessionId": sid, "title": "NEW title",
                   "cwd": projects, "lastActivityAt": 2000, "completedTurns": 16}, fh)
    # destination: the same session, frozen at an older moment
    with open(os.path.join(dorm_dir, name), "w") as fh:
        json.dump({"appSessionId": sid, "cliSessionId": sid, "title": "OLD title",
                   "cwd": projects, "lastActivityAt": 1000, "completedTurns": 10}, fh)
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
    return root, env, os.path.join(dorm_dir, name), os.path.join(live_dir, name)


# ---------------------------------------------- without --update, nothing happens
root, env, dest_row, src_row = build()
before = open(dest_row, "rb").read()
m = ccs.plan_sync(env, ccs.SyncFlags())
check("without --update a stale row is left alone", len(m["rows"]) == 0)
check("  and is counted as already present", len(m["tally"]["present"]) == 1)

# ---------------------------------------------- with --update it is refreshed
m = ccs.plan_sync(env, ccs.SyncFlags(update=True))
check("--update plans the refresh", len(m["rows"]) == 1)
check("  marked as an overwrite", m["rows"][0].get("is_update") is True)
check("  carrying the measured pre-image", bool(m["rows"][0].get("pre_b64")))
check("  whose bytes are what is on disk now",
      ccs.unb64(m["rows"][0]["pre_b64"]) == before)
ccs.run_sync(env, m)
after = json.load(open(dest_row, encoding="utf-8"))
check("destination now holds the fresh row", after["title"] == "NEW title", after["title"])
check("  including the turn count", after.get("completedTurns") == 16)

# ---------------------------------------------- undo restores the ORIGINAL bytes
op = [o for o in ccs.list_ops(env) if o.manifest["op_id"] == m["op_id"]][0]
check("undo reverses it", ccs.undo_sync(env, op) == "undone")
check("  restoring the exact original bytes", open(dest_row, "rb").read() == before)
check("  not deleting the row", os.path.exists(dest_row))
shutil.rmtree(root, ignore_errors=True)

# ---------------------------------------------- a no-op refresh is not a refresh
# The destination row must be one THIS TOOL wrote, as it would be in reality -
# a row the app serialised differs byte-wise even when it says the same thing,
# and comparing raw bytes is deliberate: "semantically equal" is not something
# this tool is willing to judge before overwriting.
root, env, dest_row, src_row = build()
os.remove(dest_row)
ccs.run_sync(env, ccs.plan_sync(env, ccs.SyncFlags()))     # plain add
check("the plain add landed", os.path.exists(dest_row))
m = ccs.plan_sync(env, ccs.SyncFlags(update=True))
check("re-running --update over its own output is a no-op", len(m["rows"]) == 0)
check("  reported as unchanged, not refreshed",
      len(m["tally"].get("unchanged") or []) == 1)
shutil.rmtree(root, ignore_errors=True)

# ---------------------------------------------- drift between plan and write
root, env, dest_row, src_row = build()
m = ccs.plan_sync(env, ccs.SyncFlags(update=True))
with open(dest_row, "w") as fh:                      # the other account edits it
    json.dump({"appSessionId": "x", "title": "THEIRS"}, fh)
theirs = open(dest_row, "rb").read()
try:
    ccs.run_sync(env, m)
    check("drift between plan and write is refused", False, "it overwrote!")
except ccs.Refusal as exc:
    check("drift between plan and write is refused", "changed since" in str(exc))
    check("  and their bytes survive", open(dest_row, "rb").read() == theirs)
shutil.rmtree(root, ignore_errors=True)

# ---------------------------------------------- deleted between plan and write
root, env, dest_row, src_row = build()
m = ccs.plan_sync(env, ccs.SyncFlags(update=True))
os.remove(dest_row)
try:
    ccs.run_sync(env, m)
    check("a session deleted after planning is not resurrected", False, "it wrote!")
except ccs.Refusal as exc:
    check("a session deleted after planning is not resurrected",
          "resurrect" in str(exc))
    check("  and it stays deleted", not os.path.exists(dest_row))
shutil.rmtree(root, ignore_errors=True)

# ---------------------------------------------- drift AFTER the refresh blocks undo
root, env, dest_row, src_row = build()
before = open(dest_row, "rb").read()
m = ccs.plan_sync(env, ccs.SyncFlags(update=True))
ccs.run_sync(env, m)
with open(dest_row, "w") as fh:                      # they open it afterwards
    json.dump({"appSessionId": "x", "title": "THEIRS AFTER"}, fh)
theirs = open(dest_row, "rb").read()
op = [o for o in ccs.list_ops(env) if o.manifest["op_id"] == m["op_id"]][0]
try:
    ccs.undo_sync(env, op)
    check("undo refuses once they have touched it", False, "it restored anyway!")
except ccs.Refusal:
    check("undo refuses once they have touched it", True)
    check("  and their version survives", open(dest_row, "rb").read() == theirs)
shutil.rmtree(root, ignore_errors=True)

# ------------------------------------- an interrupted refresh stays resumable
# Review caught this: _sync_row_drift compared only against the post-image, so
# an untouched pending refresh (still holding the pre-image) classified as
# "drifted", withdrew `forward`, and left back-then-replan as the only exit
# from an op that was perfectly resumable.
root, env, dest_row, src_row = build()
m = ccs.plan_sync(env, ccs.SyncFlags(update=True))
op = ccs.new_op(env, m)
ccs.set_status(op, "journaled")
r = op.manifest["rows"][0]
check("an untouched pending refresh reads as pristine",
      ccs._sync_row_drift(r) == "pristine", ccs._sync_row_drift(r))
cls = ccs.classify_sync_op(env, op)
check("  so recovery still offers forward", "forward" in cls["resolutions"],
      str(cls["resolutions"]))
check("  and reports no drift", not cls["drifted_rows"])
check("  and a pristine row is not reversed",
      ccs._sync_delete_targets(env, op.manifest)[2:4] == ([], []))
shutil.rmtree(root, ignore_errors=True)

# ------------------------------------- --json must not leak the destination row
root, env, dest_row, src_row = build()
# give the destination row the kind of field the transform exists to strip
d = json.load(open(dest_row, encoding="utf-8"))
d["remoteMcpServersConfig"] = {"secret-endpoint": "https://internal.example/mcp"}
json.dump(d, open(dest_row, "w", encoding="utf-8"))
m = ccs.plan_sync(env, ccs.SyncFlags(update=True))
check("the pre-image is journaled", bool(m["rows"][0].get("pre_b64")))
pub = json.dumps(ccs._public_manifest(env, m))
check("  but --json omits it", "pre_b64" not in pub)
check("  so the destination's config never reaches stdout",
      "secret-endpoint" not in pub)
check("  while the journal still has it (undo needs it)",
      "secret-endpoint" in ccs.unb64(m["rows"][0]["pre_b64"]).decode())
shutil.rmtree(root, ignore_errors=True)

# ------------------------------------- a zero-byte destination row is a refresh
root, env, dest_row, src_row = build()
open(dest_row, "wb").close()                      # empty, but present
m = ccs.plan_sync(env, ccs.SyncFlags(update=True))
check("an empty destination row plans as a refresh",
      len(m["rows"]) == 1 and m["rows"][0].get("is_update") is True)
check("  with an empty pre-image, not a missing one",
      m["rows"][0].get("pre_b64") == "")
ccs.run_sync(env, m)
check("  and it refreshes rather than refusing",
      json.load(open(dest_row, encoding="utf-8"))["title"] == "NEW title")
op = [o for o in ccs.list_ops(env) if o.manifest["op_id"] == m["op_id"]][0]
ccs.undo_sync(env, op)
check("  undo restores it to empty", os.path.getsize(dest_row) == 0)
shutil.rmtree(root, ignore_errors=True)

# ------------------------------------- recover --back RESTORES a refresh
# This path had no coverage at all, which is how the stranding case below got
# through: every reversal test drove undo_sync, and `back` is a different arm
# with its own skip-rather-than-refuse rule.
root, env, dest_row, src_row = build()
before = open(dest_row, "rb").read()
m = ccs.plan_sync(env, ccs.SyncFlags(update=True))
op = ccs.new_op(env, m)
ccs.set_status(op, "journaled")
ccs.execute_sync_op(env, op)
ccs.set_status(op, "writing")                    # pretend it never terminated
check("back reverses a completed refresh",
      ccs.recover_op(env, op, "back") == "rolled_back")
check("  by restoring the original bytes, not deleting the row",
      os.path.exists(dest_row) and open(dest_row, "rb").read() == before)
check("  and does not claim it reversed nothing",
      "reversed nothing" not in (op.manifest.get("abort_reason") or ""),
      op.manifest.get("abort_reason") or "(none)")
shutil.rmtree(root, ignore_errors=True)

# ------------------------------------- a refresh that landed but was never
# ------------------------------------- journalled is still reversed
# execute_sync_op sets r["written"] AFTER atomic_write returns. A kill in that
# window leaves the destination holding this op's bytes while the manifest says
# the row was never written - and `back` used to skip exactly those rows, report
# success, and silently keep an overwrite the user asked to reverse.
root, env, dest_row, src_row = build()
before = open(dest_row, "rb").read()
m = ccs.plan_sync(env, ccs.SyncFlags(update=True))
op = ccs.new_op(env, m)
ccs.set_status(op, "journaled")


class Killed(Exception):
    pass


ccs._crash_hook = lambda point: (_ for _ in ()).throw(Killed(point)) \
    if point == "sync-write-before-save" else None
try:
    ccs.execute_sync_op(env, op)
    check("the write is interrupted after it lands", False, "no crash fired")
except Killed:
    check("the write is interrupted after it lands", True)
finally:
    ccs._crash_hook = None
r = op.manifest["rows"][0]
check("  the row holds this op's bytes", open(dest_row, "rb").read() != before)
check("  while the manifest never recorded it as written", not r.get("written"))
check("  so the disk, not the flag, is what proves ownership",
      ccs._sync_row_drift(r) == "match", ccs._sync_row_drift(r))
check("  and back restores it anyway",
      ccs.recover_op(env, op, "back") == "rolled_back")
check("  putting the original bytes back", open(dest_row, "rb").read() == before)
shutil.rmtree(root, ignore_errors=True)

# ------------------------------------- a pending refresh whose row was deleted
# ------------------------------------- must not be offered `forward`
# _sync_write_rows refuses that row on every re-entry rather than resurrecting a
# session the destination deleted, so forward can never complete. "absent" read
# as no drift, so recovery used to offer it and promise "forward finishes them".
root, env, dest_row, src_row = build()
m = ccs.plan_sync(env, ccs.SyncFlags(update=True))
op = ccs.new_op(env, m)
ccs.set_status(op, "journaled")
os.remove(dest_row)
r = op.manifest["rows"][0]
check("a deleted pending refresh reads as absent",
      ccs._sync_row_drift(r) == "absent", ccs._sync_row_drift(r))
check("  and is reported as deleted, not as changed",
      ccs._sync_drift_titles([r])[2] == [r["title"]],
      str(ccs._sync_drift_titles([r])))
cls = ccs.classify_sync_op(env, op)
check("  so forward is withdrawn", "forward" not in cls["resolutions"],
      str(cls["resolutions"]))
check("  leaving back as the way out", cls["resolutions"] == ["back"])
check("  and it is named in the note", "deleted by that account" in cls["note"],
      cls["note"])
# an ADD in the same state is the ordinary resumption case and must be untouched
root2, env2, dest_row2, src_row2 = build()
os.remove(dest_row2)
m2 = ccs.plan_sync(env2, ccs.SyncFlags())
op2 = ccs.new_op(env2, m2)
ccs.set_status(op2, "journaled")
check("an absent pending ADD still offers forward",
      "forward" in ccs.classify_sync_op(env2, op2)["resolutions"])
shutil.rmtree(root, ignore_errors=True)
shutil.rmtree(root2, ignore_errors=True)

# ------------------------------------- a missing pre-image fails CLOSED
# `r.get("pre_b64") or ""` turned a missing key into an empty pre-image, so the
# guard that was supposed to catch it could never fire and the reversal would
# have written a ZERO-BYTE file over the destination row.
root, env, dest_row, src_row = build()
before = open(dest_row, "rb").read()
m = ccs.plan_sync(env, ccs.SyncFlags(update=True))
ccs.run_sync(env, m)
op = [o for o in ccs.list_ops(env) if o.manifest["op_id"] == m["op_id"]][0]
refreshed = open(dest_row, "rb").read()
for label, mutate in (("missing", lambda r: r.pop("pre_b64")),
                      ("null", lambda r: r.update(pre_b64=None)),
                      ("non-string", lambda r: r.update(pre_b64=1234))):
    row = op.manifest["rows"][0]
    saved = row.get("pre_b64")
    mutate(row)
    check("a %s pre-image reads as unreadable" % label,
          ccs._sync_row_drift(row) == "unreadable", ccs._sync_row_drift(row))
    (drifted, unreadable, removable,
     restorable, claimed) = ccs._sync_delete_targets(env, op.manifest)
    check("  and is never scheduled for restore", restorable == [] and removable == [])
    check("  it is reported unreadable instead", unreadable == [row["title"]])
    try:
        ccs.undo_sync(env, op)
        check("  undo refuses rather than zeroing the row", False, "it proceeded!")
    except ccs.Refusal:
        check("  undo refuses rather than zeroing the row", True)
    check("  and the destination row is untouched",
          open(dest_row, "rb").read() == refreshed)
    row["pre_b64"] = saved
shutil.rmtree(root, ignore_errors=True)

# ------------------------------------- a NEWER destination is flagged, not hidden
# Rows are per-account snapshots of one shared transcript, so the live account's
# copy is not automatically the fresher one - the window used to assert it was.
root, env, dest_row, src_row = build()
d = json.load(open(dest_row, encoding="utf-8"))
d["lastActivityAt"] = 9000                       # newer than the source's 2000
d["completedTurns"] = 40
json.dump(d, open(dest_row, "w", encoding="utf-8"))
m = ccs.plan_sync(env, ccs.SyncFlags(update=True))
check("a refresh onto a NEWER row is still planned", len(m["rows"]) == 1)
check("  but marked as moving that account backwards",
      m["rows"][0].get("regresses") is True)
check("  and named in the tally", m["tally"]["regressing"] == [m["rows"][0]["title"]])
out = []
ccs._print_sync_report(out.append, m)
check("  and the report says so out loud",
      any("BACKWARDS" in line for line in out), " | ".join(out[-6:]))
check("  and says the whole row is replaced",
      any("WHOLE row" in line for line in out))
shutil.rmtree(root, ignore_errors=True)

# the ordinary direction must NOT be flagged
root, env, dest_row, src_row = build()
m = ccs.plan_sync(env, ccs.SyncFlags(update=True))
check("an ordinary refresh is not flagged as a regression",
      m["rows"][0].get("regresses") is False and m["tally"]["regressing"] == [])
shutil.rmtree(root, ignore_errors=True)

# ------------------------------------- a stalled op must not reverse ANOTHER
# ------------------------------------- op's completed work
# Round 2 blocker, raised independently by two engines against round 2's own
# fix. transform_row is deterministic, so a later op planned over the same
# unchanged source mints a byte-identical post-image - and then "the file holds
# this op's post-image" stops proving THIS op wrote it. The sequence is the one
# the tool's own recovery advice walks a user into: op B stalls, the user
# re-runs (op C completes the same row), the user clears B with recover --back.
root, env, dest_row, src_row = build()
original = open(dest_row, "rb").read()
mB = ccs.plan_sync(env, ccs.SyncFlags(update=True))
opB = ccs.new_op(env, mB)
ccs.set_status(opB, "journaled")
ccs.set_status(opB, "writing")                   # stalled before writing its row
mC = ccs.plan_sync(env, ccs.SyncFlags(update=True))
opC = ccs.new_op(env, mC)
cid = opC.manifest["op_id"]
ccs.set_status(opC, "journaled")
ccs.execute_sync_op(env, opC)                    # C completes the same row
cs_bytes = open(dest_row, "rb").read()
check("the two ops mint identical post-images",
      mB["rows"][0]["post_b64"] == mC["rows"][0]["post_b64"])
rB = opB.manifest["rows"][0]
check("  so the stalled op's row reads as match", ccs._sync_row_drift(rB) == "match")
check("  while its own journal says it never wrote it", not rB.get("written"))
(drifted, unreadable, removable,
 restorable, claimed) = ccs._sync_delete_targets(env, opB.manifest)
check("  it is reported as claimed by another op", claimed == [rB["title"]],
      "claimed=%s restorable=%s removable=%s" % (claimed, restorable, removable))
check("  and is NOT scheduled for reversal", restorable == [] and removable == [])
check("back on the stalled op still terminates",
      ccs.recover_op(env, opB, "back") == "rolled_back")
check("  without reverting the completed op's work",
      open(dest_row, "rb").read() == cs_bytes)
check("  and the original bytes were NOT restored over it",
      open(dest_row, "rb").read() != original)
check("  the reason names the conflict",
      "later operation" in (opB.manifest.get("abort_reason") or ""),
      opB.manifest.get("abort_reason") or "(none)")
# the completed op can still undo its own work
opC = [o for o in ccs.list_ops(env) if o.manifest["op_id"] == cid][0]
check("  and the op that DID write it can still undo",
      ccs.undo_sync(env, opC) == "undone")
check("  restoring the true original", open(dest_row, "rb").read() == original)
shutil.rmtree(root, ignore_errors=True)

# ------------------------------------- a damaged is_update must not delete
# Testing only is_update failed OPEN: a completed refresh that lost the flag
# while keeping its pre-image classified as an ADD, and the reversal DELETED
# the destination row instead of restoring it.
root, env, dest_row, src_row = build()
original = open(dest_row, "rb").read()
m = ccs.plan_sync(env, ccs.SyncFlags(update=True))
ccs.run_sync(env, m)
op = [o for o in ccs.list_ops(env) if o.manifest["op_id"] == m["op_id"]][0]
for label, bad in (("missing", None), ("false", False)):
    row = op.manifest["rows"][0]
    if bad is None:
        row.pop("is_update", None)
    else:
        row["is_update"] = bad
    (drifted, unreadable, removable,
     restorable, claimed) = ccs._sync_delete_targets(env, op.manifest)
    check("a %s is_update still reverses by RESTORING" % label,
          len(restorable) == 1 and removable == [],
          "restorable=%d removable=%d" % (len(restorable), len(removable)))
    row["is_update"] = True
ccs.undo_sync(env, op)
check("  and the row comes back, not deleted",
      os.path.exists(dest_row) and open(dest_row, "rb").read() == original)
shutil.rmtree(root, ignore_errors=True)

# ------------------------------------- a locked ADD must not strand a REFRESH
# _sync_unlink_all raising meant _sync_restore_all never ran, so one locked
# added row held every refreshed row hostage in its overwritten state.
root, env, dest_row, src_row = build()
store_dir = os.path.dirname(dest_row)
projects = os.path.join(env.home, ".claude", "projects", "proj")
sid2 = "%032d" % 2
with open(os.path.join(projects, sid2 + ".jsonl"), "w") as fh:
    fh.write('{"type":"user"}\n')
with open(os.path.join(os.path.dirname(src_row), "local_%s.json" % sid2), "w") as fh:
    json.dump({"appSessionId": sid2, "cliSessionId": sid2, "title": "ADDED",
               "cwd": projects, "lastActivityAt": 3000}, fh)
original = open(dest_row, "rb").read()
m = ccs.plan_sync(env, ccs.SyncFlags(update=True))
check("the plan has one add and one refresh",
      sorted(bool(r.get("is_update")) for r in m["rows"]) == [False, True])
ccs.run_sync(env, m)
op = [o for o in ccs.list_ops(env) if o.manifest["op_id"] == m["op_id"]][0]
add_path = [r["dest_path"] for r in op.manifest["rows"] if not r.get("is_update")][0]
real_unlink = os.unlink


def failing_unlink(p):
    if os.path.normcase(os.path.abspath(p)) == os.path.normcase(os.path.abspath(add_path)):
        raise OSError(13, "locked by another process")
    return real_unlink(p)


ccs.os.unlink = failing_unlink
try:
    ccs.undo_sync(env, op)
    check("a locked add makes undo refuse", False, "it reported success")
except ccs.Refusal as exc:
    check("a locked add makes undo refuse", True)
    check("  and the refusal names both halves it attempted",
          "remove" in str(exc), str(exc)[:120])
finally:
    ccs.os.unlink = real_unlink
check("  but the REFRESH was still restored, not held hostage",
      open(dest_row, "rb").read() == original)
shutil.rmtree(root, ignore_errors=True)

# ------------------------------------- the field-loss list is the DESTINATION's
# It used to print transform_row's source-side `removed`, mislabelled as what
# the destination loses - so a destination-only remoteMcpServersConfig, exactly
# what SYNC_STRIP exists for, was dropped with no mention at all.
root, env, dest_row, src_row = build()
d = json.load(open(dest_row, encoding="utf-8"))
d["remoteMcpServersConfig"] = {"their-endpoint": "https://theirs.example/mcp"}
d["alwaysAllowedReasons"] = ["they-allowed-this"]
json.dump(d, open(dest_row, "w", encoding="utf-8"))
# The source carries its own value for the same key, so transform_row RESETS it
# to the default rather than dropping it - the other half of the loss, and a
# different thing to tell the user about than a field that simply vanishes.
s = json.load(open(src_row, encoding="utf-8"))
s["alwaysAllowedReasons"] = ["from-source"]
json.dump(s, open(src_row, "w", encoding="utf-8"))
m = ccs.plan_sync(env, ccs.SyncFlags(update=True))
row = m["rows"][0]
check("the source row has no such field",
      "remoteMcpServersConfig" not in json.load(open(src_row, encoding="utf-8")))
check("  and transform_row therefore reports nothing removed", row["removed"] == [])
check("  but the destination-side list names it",
      "remoteMcpServersConfig" in (row.get("dest_dropped") or []),
      str(row.get("dest_dropped")))
check("  and names the permission state it resets",
      "alwaysAllowedReasons" in (row.get("dest_reset") or []),
      str(row.get("dest_reset")))
out = []
ccs._print_sync_report(out.append, m)
check("  and the report prints it as the destination's loss",
      any("row loses" in line and "remoteMcpServersConfig" in line for line in out),
      " | ".join(l for l in out if "loses" in l) or "(no such line)")
shutil.rmtree(root, ignore_errors=True)

# ------------------------------------- ownership is checked on WRITTEN rows too
# Round 3 blocker: the claim check first sat inside `if not written`, which made
# it dead code in undo_sync - that arm only runs on a completed op, where every
# row is written. Both sequences below reach the damage through a written row.

# (a) ADD: B stalls, C completes the row, B is rolled FORWARD (the resolution
# classify_sync_op offers), and now two completed ops journal the same path.
root, env, dest_row, src_row = build()
os.remove(dest_row)                                   # so the plan is an add
mB = ccs.plan_sync(env, ccs.SyncFlags())
opB = ccs.new_op(env, mB)
ccs.set_status(opB, "journaled")
ccs.set_status(opB, "writing")                        # stalled before its write
mC = ccs.plan_sync(env, ccs.SyncFlags())
opC = ccs.new_op(env, mC)
cid = opC.manifest["op_id"]
ccs.set_status(opC, "journaled")
ccs.execute_sync_op(env, opC)
check("C's add landed", os.path.exists(dest_row))
check("forward is still offered to the stalled op",
      "forward" in ccs.classify_sync_op(env, opB)["resolutions"])
check("  and it completes by adopting the identical bytes",
      ccs.recover_op(env, opB, "forward") == "completed")
check("  leaving both ops recording the same path as written",
      opB.manifest["rows"][0].get("written") is True)
msg = ""
try:
    ccs.undo_sync(env, opB)
    check("undo of the superseded op REFUSES", False, "it deleted the other op's row!")
except ccs.Refusal as exc:
    msg = str(exc)
    check("undo of the superseded op REFUSES", "later operation" in msg, msg[:100])
check("  and the row C added survives", os.path.exists(dest_row))
check("  the refusal does not advise deleting the row in the app",
      "remove the rows in the app" not in msg and "Do NOT delete" in msg)
# ...and the op that OWNS the row can still reverse it. Round 4 caught that the
# first version of this check was symmetric: both ops saw each other, so BOTH
# refused and the row could never be reversed through the journal at all.
opC = [o for o in ccs.list_ops(env) if o.manifest["op_id"] == cid][0]
check("  while the owning op CAN still undo", ccs.undo_sync(env, opC) == "undone")
check("  removing the row", not os.path.exists(dest_row))
shutil.rmtree(root, ignore_errors=True)

# (b) REFRESH: B refreshes V0->V1, that account edits it to V2, C re-plans
# (pre=V2, post=V1) and completes. Undoing B would put V0 back over C's work.
root, env, dest_row, src_row = build()
v0 = open(dest_row, "rb").read()
mB = ccs.plan_sync(env, ccs.SyncFlags(update=True))
ccs.run_sync(env, mB)
v1 = open(dest_row, "rb").read()
with open(dest_row, "w") as fh:
    # The app rewriting a row it already has keeps the SAME cliSessionId - only
    # a new CLI run mints a different one. Getting that wrong made this an
    # orphaning pointer swap, which the new guard correctly held back.
    json.dump({"appSessionId": "x", "cliSessionId": "%032d" % 1,
               "cwd": os.path.join(env.home, ".claude", "projects", "proj"),
               "title": "V2 theirs", "lastActivityAt": 2500}, fh)
v2 = open(dest_row, "rb").read()
mC = ccs.plan_sync(env, ccs.SyncFlags(update=True))
opC = ccs.new_op(env, mC)
cid = opC.manifest["op_id"]
ccs.set_status(opC, "journaled")
ccs.execute_sync_op(env, opC)
check("C's refresh restores the same post-image B wrote",
      open(dest_row, "rb").read() == v1)
opB = [o for o in ccs.list_ops(env) if o.manifest["op_id"] == mB["op_id"]][0]
try:
    ccs.undo_sync(env, opB)
    check("undo of the superseded refresh REFUSES", False, "it reverted C's work!")
except ccs.Refusal as exc:
    check("undo of the superseded refresh REFUSES", "later operation" in str(exc),
          str(exc)[:100])
check("  and C's refresh survives", open(dest_row, "rb").read() == v1)
check("  the stale pre-image was NOT put back", open(dest_row, "rb").read() != v0)
# The whole point of ordering: C holds the ONLY copy of V2, the destination
# account's own newer state. Refusing C's undo too would strand it forever.
opC = [o for o in ccs.list_ops(env) if o.manifest["op_id"] == cid][0]
check("  but the owning op CAN undo", ccs.undo_sync(env, opC) == "undone")
check("  restoring that account's own V2, not B's stale V0",
      open(dest_row, "rb").read() == v2)
shutil.rmtree(root, ignore_errors=True)

# ------------------------------------- a REVERSED op withdraws its claim
# Without the status filter the flags a reversed op leaves behind would block
# every later op's legitimate reversal of the same row, forever.
root, env, dest_row, src_row = build()
os.remove(dest_row)
m1 = ccs.plan_sync(env, ccs.SyncFlags())
ccs.run_sync(env, m1)
op1 = [o for o in ccs.list_ops(env) if o.manifest["op_id"] == m1["op_id"]][0]
ccs.undo_sync(env, op1)
check("the first op is undone", not os.path.exists(dest_row))
m2 = ccs.plan_sync(env, ccs.SyncFlags())
ccs.run_sync(env, m2)
op2 = [o for o in ccs.list_ops(env) if o.manifest["op_id"] == m2["op_id"]][0]
check("a later op re-adds the same row", os.path.exists(dest_row))
check("  and its undo is not blocked by the withdrawn claim",
      ccs.undo_sync(env, op2) == "undone")
check("  the row is gone again", not os.path.exists(dest_row))
shutil.rmtree(root, ignore_errors=True)

# ------------------------------------- rotation must not prune the evidence
# A nonterminal op lives forever; its claimant must not age out from under it.
root, env, dest_row, src_row = build()
mB = ccs.plan_sync(env, ccs.SyncFlags(update=True))
opB = ccs.new_op(env, mB)
ccs.set_status(opB, "journaled")
ccs.set_status(opB, "writing")                        # stalled, never pruned
mC = ccs.plan_sync(env, ccs.SyncFlags(update=True))
opC = ccs.new_op(env, mC)
cid = opC.manifest["op_id"]
ccs.set_status(opC, "journaled")
ccs.execute_sync_op(env, opC)
for _ in range(14):                                   # push C well past the cap
    o = ccs.new_op(env, {"op_type": "sync", "rows": [], "dest_path": "",
                         "source_path": "", "tally": {}})
    ccs.set_status(o, "completed")
ccs.rotate_ops(env)
kept = [o.manifest["op_id"] for o in ccs.list_ops(env)]
check("the claimant survives rotation while a stalled op could collide",
      cid in kept, "kept=%d ops" % len(kept))
check("  so the stalled op still sees the claim",
      ccs._sync_delete_targets(env, opB.manifest)[4] == [mB["rows"][0]["title"]],
      str(ccs._sync_delete_targets(env, opB.manifest)[4]))
shutil.rmtree(root, ignore_errors=True)

# ------------------------------------- corrupt types refuse, never traceback
root, env, dest_row, src_row = build()
m = ccs.plan_sync(env, ccs.SyncFlags(update=True))
ccs.run_sync(env, m)
op = [o for o in ccs.list_ops(env) if o.manifest["op_id"] == m["op_id"]][0]
row = op.manifest["rows"][0]
row["post_b64"] = 1234
check("a non-string post-image reads as unreadable, not a crash",
      ccs._sync_row_drift(row) == "unreadable", ccs._sync_row_drift(row))
row["post_b64"] = ccs.b64(b"{}")
# the journal budget must not crash on the same input before the loop starts
row["pre_b64"] = 1234
row["written"] = False                                # so the loop reaches it
ccs.set_status(op, "journaled")
try:
    ccs.execute_sync_op(env, op)
    check("a non-string pre-image refuses inside the write loop", False, "no refusal")
except ccs.Refusal as exc:
    check("a non-string pre-image refuses inside the write loop",
          "pre-image cannot be read" in str(exc), str(exc)[:90])
except TypeError as exc:
    check("a non-string pre-image refuses inside the write loop", False,
          "TypeError from the budget: %s" % exc)
shutil.rmtree(root, ignore_errors=True)

# a source row with a non-numeric lastActivityAt must not kill the whole plan,
# and must read as UNKNOWN - not silently as age zero, which made every such
# row look like a regression. The previous assertion here was vacuous: it was an
# `or` that passed for every reachable outcome of this scenario.
root, env, dest_row, src_row = build()
s = json.load(open(src_row, encoding="utf-8"))
s["lastActivityAt"] = "soon"
json.dump(s, open(src_row, "w", encoding="utf-8"))
try:
    m = ccs.plan_sync(env, ccs.SyncFlags(update=True))
    check("a non-numeric source timestamp still plans", len(m["rows"]) == 1)
    row = m["rows"][0]
    check("  and is NOT called a regression", row.get("regresses") is False,
          "regresses=%s" % row.get("regresses"))
    check("  it is recorded as unknown instead",
          row.get("activity_unknown") is True and
          m["tally"]["activity_unknown"] == [row["title"]],
          "unknown=%s tally=%s" % (row.get("activity_unknown"),
                                   m["tally"]["activity_unknown"]))
    check("  and the tally does not claim a regression",
          m["tally"]["regressing"] == [])
    out = []
    ccs._print_sync_report(out.append, m)
    check("  the report says it could not be determined",
          any("could not be determined" in line for line in out),
          " | ".join(l for l in out if "<-" in l) or "(no marker line)")
except TypeError as exc:
    check("a non-numeric source timestamp still plans", False, "TypeError: %s" % exc)
shutil.rmtree(root, ignore_errors=True)

# a genuinely older destination is still not flagged, and a genuinely newer one
# still is - the unknown state must not have swallowed either real answer.
root, env, dest_row, src_row = build()
m = ccs.plan_sync(env, ccs.SyncFlags(update=True))
check("a genuinely older destination is not flagged",
      m["rows"][0].get("regresses") is False
      and m["rows"][0].get("activity_unknown") is False)
d = json.load(open(dest_row, encoding="utf-8"))
d["lastActivityAt"] = 9000
json.dump(d, open(dest_row, "w", encoding="utf-8"))
m = ccs.plan_sync(env, ccs.SyncFlags(update=True))
check("  and a genuinely newer one still is",
      m["rows"][0].get("regresses") is True
      and m["rows"][0].get("activity_unknown") is False)
shutil.rmtree(root, ignore_errors=True)

# ------------------------------------- --newer-only holds back the regressions
# The whole point: a bulk refresh sends whichever account you happen to be in
# over the top of the others. This sends only what it can show is newer.
root, env, dest_row, src_row = build()
d = json.load(open(dest_row, encoding="utf-8"))
d["lastActivityAt"] = 9000                        # destination is NEWER than 2000
json.dump(d, open(dest_row, "w", encoding="utf-8"))
before = open(dest_row, "rb").read()
m = ccs.plan_sync(env, ccs.SyncFlags(update=True))
check("plain --update would overwrite the newer row", len(m["rows"]) == 1)
m = ccs.plan_sync(env, ccs.SyncFlags(update=True, newer_only=True))
check("--newer-only holds it back instead", len(m["rows"]) == 0)
check("  and names it under held_older",
      m["tally"]["held_older"] == ["NEW title"], str(m["tally"]["held_older"]))
check("  not under held_unknown", m["tally"]["held_unknown"] == [])
check("  nor under held_same", m["tally"]["held_same"] == [])
out = []
ccs._print_sync_report(out.append, m)
check("  the report names the row it did not send",
      any("their copy is newer" in l and "NEW title" in l for l in out),
      " | ".join(l for l in out if "not sent" in l) or "(no line)")
ccs.run_sync(env, m)
check("  applying it changes nothing", open(dest_row, "rb").read() == before)
shutil.rmtree(root, ignore_errors=True)

# a genuinely newer source still goes through
root, env, dest_row, src_row = build()
before = open(dest_row, "rb").read()
m = ccs.plan_sync(env, ccs.SyncFlags(update=True, newer_only=True))
check("a genuinely newer source is still refreshed", len(m["rows"]) == 1)
check("  and nothing is held back",
      m["tally"]["held_older"] == [] and m["tally"]["held_unknown"] == [])
ccs.run_sync(env, m)
check("  it really lands",
      json.load(open(dest_row, encoding="utf-8"))["title"] == "NEW title")
shutil.rmtree(root, ignore_errors=True)

# SAME AGE is held back too. Found by running the flag for real: a row needs a
# refresh when its BYTES differ, and rows differ while their timestamps match
# because model / permissionMode / MCP config are per-account settings that
# drift without activity. "not older" let 248 such rows through on a real
# machine; "strictly newer" is the only reading that matches what was asked for.
root, env, dest_row, src_row = build()
d = json.load(open(dest_row, encoding="utf-8"))
d["lastActivityAt"] = 2000            # SAME as the source
d["model"] = "claude-something-else"  # but the bytes differ
json.dump(d, open(dest_row, "w", encoding="utf-8"))
before = open(dest_row, "rb").read()
m = ccs.plan_sync(env, ccs.SyncFlags(update=True))
check("plain --update would rewrite a same-age row", len(m["rows"]) == 1)
check("  and it is not counted as a regression",
      m["rows"][0].get("regresses") is False)
m = ccs.plan_sync(env, ccs.SyncFlags(update=True, newer_only=True))
check("--newer-only holds back a SAME-AGE row", len(m["rows"]) == 0)
check("  under held_same, not held_older",
      m["tally"]["held_same"] == ["NEW title"] and m["tally"]["held_older"] == [],
      "same=%s older=%s" % (m["tally"]["held_same"], m["tally"]["held_older"]))
ccs.run_sync(env, m)
check("  so that account keeps its own model setting",
      open(dest_row, "rb").read() == before)
shutil.rmtree(root, ignore_errors=True)

# unknown direction is held back too - "only newer" is a claim, not a guess
root, env, dest_row, src_row = build()
s = json.load(open(src_row, encoding="utf-8"))
s["lastActivityAt"] = "soon"
json.dump(s, open(src_row, "w", encoding="utf-8"))
m = ccs.plan_sync(env, ccs.SyncFlags(update=True, newer_only=True))
check("an undeterminable direction is held back", len(m["rows"]) == 0)
check("  under held_unknown, not held_older",
      m["tally"]["held_unknown"] == ["NEW title"] and m["tally"]["held_older"] == [],
      "unknown=%s older=%s" % (m["tally"]["held_unknown"], m["tally"]["held_older"]))
shutil.rmtree(root, ignore_errors=True)

# it narrows --update and never widens it: ADDs are untouched by the flag
root, env, dest_row, src_row = build()
os.remove(dest_row)
a = ccs.plan_sync(env, ccs.SyncFlags())
b = ccs.plan_sync(env, ccs.SyncFlags(newer_only=True))
c = ccs.plan_sync(env, ccs.SyncFlags(update=True, newer_only=True))
check("--newer-only leaves plain adds alone",
      len(a["rows"]) == 1 and len(b["rows"]) == 1 and len(c["rows"]) == 1)
out = []
ccs._print_sync_report(out.append, b)
check("  and says so when given without --update",
      any("nothing to act on" in l for l in out),
      " | ".join(l for l in out if "newer-only" in l) or "(no note)")
shutil.rmtree(root, ignore_errors=True)

# ------------------------------------- a row is a POINTER, not just a snapshot
# The failure this whole block exists for, found by using the tool rather than
# reviewing it: a row's FILENAME is the app's session id and survives a resume,
# while the cliSessionId inside names the transcript and changes on each new CLI
# run. Two accounts can hold the same entry pointing at different conversations,
# so a refresh can swap which one that account opens - and if nothing else
# points at the displaced one, hide it from every sidebar.


def build_swap(third_holds_old=False):
    """Same filename in both stores, pointing at DIFFERENT conversations."""
    root, env, dest_row, src_row = build()
    old_sid, new_sid = "%032d" % 7, "%032d" % 8
    projects = os.path.join(env.home, ".claude", "projects", "proj")
    for s in (old_sid, new_sid):
        with open(os.path.join(projects, s + ".jsonl"), "w") as fh:
            fh.write('{"type":"user"}\n')
    name = "local_shared.json"
    src = os.path.join(os.path.dirname(src_row), name)
    dst = os.path.join(os.path.dirname(dest_row), name)
    with open(src, "w") as fh:                       # live account: the NEW one
        json.dump({"cliSessionId": new_sid, "title": "Shared slot",
                   "cwd": projects, "lastActivityAt": 5000}, fh)
    with open(dst, "w") as fh:                       # dormant: a DIFFERENT one
        json.dump({"cliSessionId": old_sid, "title": "Shared slot",
                   "cwd": projects, "lastActivityAt": 4000}, fh)
    if third_holds_old:
        # a third store still pointing at the displaced conversation
        third = os.path.join(os.path.dirname(os.path.dirname(dest_row)),
                             "3" * 32)
        os.makedirs(third, exist_ok=True)
        with open(os.path.join(third, name), "w") as fh:
            json.dump({"cliSessionId": old_sid, "title": "Shared slot",
                       "cwd": projects, "lastActivityAt": 4000}, fh)
    return root, env, dst, old_sid, new_sid, os.path.dirname(dst)


# 1. the swap is DETECTED and not presented as a metadata refresh
root, env, dst, old_sid, new_sid, todir = build_swap(third_holds_old=True)
m = ccs.plan_sync(env, ccs.SyncFlags(update=True, allow_orphan=True, to=todir))
row = [r for r in m["rows"] if r["title"] == "Shared slot"][0]
check("a pointer swap is detected", row["swaps_conversation"] is True)
check("  naming the conversation it displaces",
      row["displaced_session"] == old_sid, str(row["displaced_session"])[:12])
check("  and it is tallied separately from an ordinary refresh",
      m["tally"]["swapping"] == ["Shared slot"], str(m["tally"]["swapping"]))
check("  the displaced one is NOT orphaned - a third store holds it",
      row["displaced_orphan"] is False, str(row["displaced_orphan"]))
out = []
ccs._print_sync_report(out.append, m)
check("  the report says it opens a DIFFERENT conversation",
      any("DIFFERENT conversation" in l for l in out),
      " | ".join(l for l in out if "DIFFERENT" in l) or "(no line)")
# 0.9.13 changed this wording deliberately: reachability is now measured per
# ROW, so the voucher can be another row in the SAME destination account, and
# "stays reachable from another account" would send the reader to the wrong
# sidebar. The claim being asserted is unchanged - only where it points.
check("  and says the displaced one is still opened by a surviving row",
      any("another surviving row still opens it" in l for l in out),
      " | ".join(l.strip() for l in out if "opens it" in l) or "(no line)")
shutil.rmtree(root, ignore_errors=True)

# 2. ORPHANING is detected and held back by default
root, env, dst, old_sid, new_sid, todir = build_swap()
before = open(dst, "rb").read()
m = ccs.plan_sync(env, ccs.SyncFlags(update=True))
check("an orphaning swap is held back by default",
      not [r for r in m["rows"] if r["title"] == "Shared slot"])
check("  and named under held_orphan",
      m["tally"]["held_orphan"] == ["Shared slot"], str(m["tally"]["held_orphan"]))
out = []
ccs._print_sync_report(out.append, m)
check("  the report says it would hide a conversation",
      any("would hide a conversation" in l.lower() for l in out),
      " | ".join(l for l in out if "hide" in l.lower()) or "(no line)")
ccs.run_sync(env, m)
check("  applying leaves the pointer alone", open(dst, "rb").read() == before)
shutil.rmtree(root, ignore_errors=True)

# 3. --allow-orphan lets it through, loudly
root, env, dst, old_sid, new_sid, todir = build_swap()
m = ccs.plan_sync(env, ccs.SyncFlags(update=True, allow_orphan=True))
row = [r for r in m["rows"] if r["title"] == "Shared slot"][0]
check("--allow-orphan lets the swap through", row["displaced_orphan"] is True)
check("  and it is no longer held back", m["tally"]["held_orphan"] == [])
out = []
ccs._print_sync_report(out.append, m)
check("  while still saying it becomes unreachable",
      # 0.9.13: "every account" -> "every sidebar". Reachability is measured per
      # ROW now, so counting accounts is the wrong unit in both directions - the
      # orphan case included, where the point is that no ROW anywhere opens it.
      any("unreachable from every sidebar" in l for l in out),
      " | ".join(l for l in out if "unreachable" in l) or "(no line)")
ccs.run_sync(env, m)
check("  and the pointer really moves",
      json.load(open(dst, encoding="utf-8"))["cliSessionId"] == new_sid)
shutil.rmtree(root, ignore_errors=True)

# 4. an ordinary refresh is NOT mistaken for a swap
root, env, dest_row, src_row = build()
m = ccs.plan_sync(env, ccs.SyncFlags(update=True))
check("an ordinary refresh is not flagged as a swap",
      m["rows"][0]["swaps_conversation"] is False
      and m["rows"][0]["displaced_orphan"] is None
      and m["tally"]["swapping"] == [])
shutil.rmtree(root, ignore_errors=True)

# 5. an unreadable store must not be read as "nothing points at it"
root, env, dst, old_sid, new_sid, todir = build_swap(third_holds_old=True)
third = os.path.join(os.path.dirname(os.path.dirname(dst)), "3" * 32)
orig_listdir = os.listdir


def blind_listdir(p):
    if os.path.normcase(os.path.abspath(p)) == os.path.normcase(os.path.abspath(third)):
        raise OSError(13, "permission denied")
    return orig_listdir(p)


ccs.os.listdir = blind_listdir
try:
    m = ccs.plan_sync(env, ccs.SyncFlags(update=True, to=todir))
finally:
    ccs.os.listdir = orig_listdir
check("a store that cannot be read is never read as 'nothing there'",
      m["tally"]["held_orphan"] == ["Shared slot"], str(m["tally"]["held_orphan"]))
m2 = ccs.plan_sync(env, ccs.SyncFlags(update=True, allow_orphan=True, to=todir))
row = [r for r in m2["rows"] if r["title"] == "Shared slot"][0]
ccs.os.listdir = blind_listdir
try:
    m3 = ccs.plan_sync(env, ccs.SyncFlags(update=True, allow_orphan=True, to=todir))
finally:
    ccs.os.listdir = orig_listdir
row3 = [r for r in m3["rows"] if r["title"] == "Shared slot"][0]
check("  and is reported as 'unknown', not as a confirmed orphan",
      row3["displaced_orphan"] == "unknown", str(row3["displaced_orphan"]))
shutil.rmtree(root, ignore_errors=True)

# ------------------------------------- how much would actually be lost?
# "This opens a different conversation and nothing else points at the old one"
# is true of EVERY propagation once you work a session from two accounts, so on
# its own it holds back the normal case and reads as the tool being broken.
# Measured on a real machine: 7 rows held, 5 of which displaced a conversation
# already 72-100% present in the incoming one. The number is what makes the
# hold judgeable, so it is measured and printed.


TURNS = 12          # prose turns in the displaced conversation


def build_lineage(overlap):
    """Two transcripts for one slot. `overlap` = how many of the older's TURNS
    prose messages are repeated verbatim in the newer one.

    Both transcripts also carry heavy tool traffic, which must NOT count: it is
    near-identical boilerplate across unrelated sessions, and counting it put
    two real pairs at 74%/94% shared when their prose was 5%/36% - numbers that
    would have talked a user into an overwrite the prose says to avoid.
    """
    root, env, dest_row, src_row = build()
    old_sid, new_sid = "%032d" % 21, "%032d" % 22
    projects = os.path.join(env.home, ".claude", "projects", "proj")

    def tool_noise(fh, tag):
        for i in range(40):
            fh.write(json.dumps({"type": "assistant", "timestamp": "%s%d" % (tag, i),
                                 "message": {"content": [
                                     {"type": "tool_use", "name": "Bash",
                                      "input": {"command": "ls"}}]}}) + "\n")
    with open(os.path.join(projects, old_sid + ".jsonl"), "w", encoding="utf-8") as fh:
        for i in range(TURNS):
            fh.write(json.dumps({"type": "user", "timestamp": "t%d" % i,
                                 "message": {"content": "shared message %d" % i}}) + "\n")
        tool_noise(fh, "oldtool")
    with open(os.path.join(projects, new_sid + ".jsonl"), "w", encoding="utf-8") as fh:
        for i in range(overlap):        # the same content, different timestamps
            fh.write(json.dumps({"type": "user", "timestamp": "LATER%d" % i,
                                 "message": {"content": "shared message %d" % i}}) + "\n")
        for i in range(6):
            fh.write(json.dumps({"type": "user", "timestamp": "new%d" % i,
                                 "message": {"content": "brand new message %d" % i}}) + "\n")
        tool_noise(fh, "newtool")
    name = "local_lineage.json"
    with open(os.path.join(os.path.dirname(src_row), name), "w") as fh:
        json.dump({"cliSessionId": new_sid, "title": "Lineage",
                   "cwd": projects, "lastActivityAt": 9000}, fh)
    dst = os.path.join(os.path.dirname(dest_row), name)
    with open(dst, "w") as fh:
        json.dump({"cliSessionId": old_sid, "title": "Lineage",
                   "cwd": projects, "lastActivityAt": 8000}, fh)
    return root, env, dst


for overlap, want_pct, phrase in ((TURNS, 100, "every prose turn"),
                                  (TURNS // 2, 50, "a real part is only there"),
                                  (0, 0, "largely its OWN conversation")):
    root, env, dst = build_lineage(overlap)
    m = ccs.plan_sync(env, ccs.SyncFlags(update=True, allow_orphan=True))
    row = [r for r in m["rows"] if r["title"] == "Lineage"][0]
    got = int(round((row.get("displaced_overlap") or 0) * 100))
    check("overlap of %d/%d measures as %d%%" % (overlap, TURNS, want_pct),
          got == want_pct, "got %s%%" % got)
    check("  and reads as '%s'" % phrase[:28],
          phrase in ccs._overlap_clause(row["displaced_overlap"]),
          ccs._overlap_clause(row["displaced_overlap"]))
    shutil.rmtree(root, ignore_errors=True)

# timestamps must NOT count - a resume rewrites them, and keying on them
# reported a conversation as diverging from its own continuation
root, env, dst = build_lineage(TURNS)
m = ccs.plan_sync(env, ccs.SyncFlags(update=True, allow_orphan=True))
row = [r for r in m["rows"] if r["title"] == "Lineage"][0]
check("identical content with different timestamps is 100% overlap",
      row["displaced_overlap"] == 1.0, str(row["displaced_overlap"]))
shutil.rmtree(root, ignore_errors=True)

# the number reaches the user on the held-back path, which is where it decides
root, env, dst = build_lineage(TURNS)
m = ccs.plan_sync(env, ccs.SyncFlags(update=True))
check("held back by default", m["tally"]["held_orphan"] == ["Lineage"])
d = m["tally"]["held_orphan_detail"]
check("  with the measurement carried alongside",
      len(d) == 1 and d[0]["overlap"] == 1.0, str(d))
check("  and no row bytes smuggled into the tally",
      not any(k.endswith("_b64") for k in d[0]), str(sorted(d[0])))
out = []
ccs._print_sync_report(out.append, m)
check("  the report states the full-overlap case precisely",
      any("every prose turn" in l for l in out)
      and not any("nothing is lost" in l for l in out),
      " | ".join(l.strip() for l in out if "prose turn" in l) or "(no line)")
check("  and names the flag that would send it",
      any("--allow-orphan" in l for l in out))
shutil.rmtree(root, ignore_errors=True)

# unmeasurable is reported as unmeasured, never as zero
root, env, dst = build_lineage(TURNS)
projects = os.path.join(env.home, ".claude", "projects", "proj")
os.remove(os.path.join(projects, "%032d.jsonl" % 21))     # displaced one is gone
m = ccs.plan_sync(env, ccs.SyncFlags(update=True, allow_orphan=True))
row = [r for r in m["rows"] if r["title"] == "Lineage"][0]
check("a missing displaced transcript is NOT MEASURED, not 0%",
      row["displaced_overlap"] is None
      and "NOT MEASURED" in ccs._overlap_clause(row["displaced_overlap"]),
      str(row["displaced_overlap"]))
shutil.rmtree(root, ignore_errors=True)

# ------------------------------------- what the overlap number may NOT claim
# Every engine on the review panel raised the same objection: the full-overlap
# line claimed "nothing is lost" from a prose-only, 400-char-truncated, unordered
# comparison that never looks at images, attachments or tool output.
check("the full-overlap line does not claim nothing is lost",
      "nothing is lost" not in ccs._overlap_clause(1.0), ccs._overlap_clause(1.0))
check("  it says what was actually compared",
      "prose" in ccs._overlap_clause(1.0) and "400" in ccs._overlap_clause(1.0),
      ccs._overlap_clause(1.0))
check("  and names what was NOT",
      all(w in ccs._overlap_clause(1.0) for w in ("images", "attachments", "tool output")))

# percentages floor; int(round(...)) turned 99.9% into the full-overlap line
for frac in (0.999, 0.9951, 0.995):
    c = ccs._overlap_clause(frac)
    check("%.4f does not render as full overlap" % frac,
          "every prose turn" not in c, c[:60])
check("  99.9% reads as 99%", "99%" in ccs._overlap_clause(0.999),
      ccs._overlap_clause(0.999))
check("a tiny non-zero overlap is not printed as 0%",
      "under 1%" in ccs._overlap_clause(0.004), ccs._overlap_clause(0.004))
check("exactly 1.0 is the only full-overlap case",
      "every prose turn" in ccs._overlap_clause(1.0))

# ------------------------------------- a malformed transcript must not crash
# `(d.get("message") or {}).get(...)` raised AttributeError out of a function
# whose caller catches only OSError, taking plan_sync down with it.
root, env, dst = build_lineage(TURNS)
projects = os.path.join(env.home, ".claude", "projects", "proj")
bad = os.path.join(projects, "%032d.jsonl" % 21)
with open(bad, "w", encoding="utf-8") as fh:
    fh.write(json.dumps({"type": "user", "message": "a string, not a dict"}) + "\n")
    fh.write(json.dumps(["a list, not an object"]) + "\n")
    fh.write("{not json at all\n")
    fh.write(json.dumps({"type": "user", "message": {"content": "real turn"}}) + "\n")
try:
    fps = ccs._message_fingerprints(bad)
    check("a malformed transcript parses instead of raising", fps == ccs._message_fingerprints(bad))
    check("  keeping only the well-formed turns", len(fps) == 1, str(fps))
except Exception as exc:
    check("a malformed transcript parses instead of raising", False,
          "%s: %s" % (type(exc).__name__, exc))
try:
    m = ccs.plan_sync(env, ccs.SyncFlags(update=True, allow_orphan=True))
    row = [r for r in m["rows"] if r["title"] == "Lineage"][0]
    check("  and planning survives it", row["displaced_overlap"] is None,
          str(row["displaced_overlap"]))
except Exception as exc:
    check("  and planning survives it", False, "%s: %s" % (type(exc).__name__, exc))
shutil.rmtree(root, ignore_errors=True)

# ------------------------------------- an ambiguous session id is unmeasurable
# find_transcripts returns EVERY project dir holding that id; taking [0] would
# compare whichever the walk reached first.
root, env, dst = build_lineage(TURNS)
projects = os.path.join(env.home, ".claude", "projects")
other = os.path.join(projects, "proj2")
os.makedirs(other, exist_ok=True)
shutil.copy(os.path.join(projects, "proj", "%032d.jsonl" % 21),
            os.path.join(other, "%032d.jsonl" % 21))
check("the same session id in two project dirs is ambiguous",
      len(ccs.find_transcripts(env.projects_root, "%032d" % 21)) == 2)
m = ccs.plan_sync(env, ccs.SyncFlags(update=True, allow_orphan=True))
row = [r for r in m["rows"] if r["title"] == "Lineage"][0]
check("  so it is NOT MEASURED rather than measured against a guess",
      row["displaced_overlap"] is None, str(row["displaced_overlap"]))
shutil.rmtree(root, ignore_errors=True)

# ------------------------------------- cheap failures must not starve the cap
root, env, dst = build_lineage(TURNS)
os.remove(os.path.join(env.home, ".claude", "projects", "proj", "%032d.jsonl" % 21))
saved = ccs.TRANSCRIPT_COMPARE_MAX_ROWS
ccs.TRANSCRIPT_COMPARE_MAX_ROWS = 1
try:
    m = ccs.plan_sync(env, ccs.SyncFlags(update=True, allow_orphan=True))
    row = [r for r in m["rows"] if r["title"] == "Lineage"][0]
    check("a measurement that produced no number does not spend the row budget",
          row["displaced_overlap"] is None)
finally:
    ccs.TRANSCRIPT_COMPARE_MAX_ROWS = saved
shutil.rmtree(root, ignore_errors=True)

# ------------------------------------- 'unknown' is never reported as certain
root, env, dst, old_sid, new_sid, todir = build_swap(third_holds_old=True)
third = os.path.join(os.path.dirname(os.path.dirname(dst)), "3" * 32)
orig_listdir = os.listdir


def blind2(p):
    if os.path.normcase(os.path.abspath(p)) == os.path.normcase(os.path.abspath(third)):
        raise OSError(13, "permission denied")
    return orig_listdir(p)


ccs.os.listdir = blind2
try:
    m = ccs.plan_sync(env, ccs.SyncFlags(update=True, to=todir))
finally:
    ccs.os.listdir = orig_listdir
d = m["tally"]["held_orphan_detail"]
check("an unreadable store yields an 'unknown' hold", len(d) == 1 and d[0]["orphan"] == "unknown",
      str([(x["title"], x.get("orphan")) for x in d]))
out = []
ccs._print_sync_report(out.append, m)
check("  and the report says UNKNOWN, not 'nothing else points at it'",
      any("UNKNOWN" in l for l in out)
      and not any("nothing else points at the conversation" in l for l in out),
      " | ".join(l.strip() for l in out if "UNKNOWN" in l or "nothing else" in l) or "(no line)")
shutil.rmtree(root, ignore_errors=True)

# a genuinely confirmed orphan still reads as certain
root, env, dst, old_sid, new_sid, todir = build_swap()
m = ccs.plan_sync(env, ccs.SyncFlags(update=True))
d = m["tally"]["held_orphan_detail"]
check("a confirmed orphan is reported as certain", d and d[0]["orphan"] is True,
      str([(x["title"], x.get("orphan")) for x in d]))
out = []
ccs._print_sync_report(out.append, m)
check("  with the definite wording",
      any("nothing else points at the conversation" in l for l in out))
shutil.rmtree(root, ignore_errors=True)

print()
print("ALL PASS" if all(ok) else "SOME CHECKS FAILED")
sys.exit(0 if all(ok) else 1)
