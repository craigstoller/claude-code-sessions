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
      ccs._sync_delete_targets(env, op.manifest)[2:] == ([], []))
shutil.rmtree(root, ignore_errors=True)

# ------------------------------------- --json must not leak the destination row
root, env, dest_row, src_row = build()
# give the destination row the kind of field the transform exists to strip
d = json.load(open(dest_row, encoding="utf-8"))
d["remoteMcpServersConfig"] = {"secret-endpoint": "https://internal.example/mcp"}
json.dump(d, open(dest_row, "w", encoding="utf-8"))
m = ccs.plan_sync(env, ccs.SyncFlags(update=True))
check("the pre-image is journaled", bool(m["rows"][0].get("pre_b64")))
pub = json.dumps(ccs._public_manifest(m))
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

print()
print("ALL PASS" if all(ok) else "SOME CHECKS FAILED")
sys.exit(0 if all(ok) else 1)
