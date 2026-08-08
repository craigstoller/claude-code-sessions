"""Exercise the GUI's Undo path end to end against a synthetic store.

Does a real sync --apply and a real undo through the same functions the window
calls, then checks the destination is byte-for-byte back where it started.
Also checks the button's selection rule (only when the most recent completed op
is a sync) and that the drift refusal still bites. Touches nothing real.
"""
import json
import os
import shutil
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import claude_code_sessions as ccs  # noqa: E402

GUIDIR = tempfile.mkdtemp()
shutil.copy(os.path.join(REPO, "claude_code_sessions_gui.py"),
            os.path.join(GUIDIR, "gp.py"))
sys.path.insert(0, GUIDIR)
import gp  # noqa: E402

LIVE, DORM = "a" * 32, "b" * 32
ORG_L, ORG_D = "1" * 32, "2" * 32
ok = []


def check(name, cond, extra=""):
    print("%s %s%s" % ("OK " if cond else "BAD", name, ("  " + extra) if extra else ""))
    ok.append(bool(cond))


root = tempfile.mkdtemp(prefix="undotest-")
home = os.path.join(root, "home")
store = os.path.join(root, "Claude", "claude-code-sessions")
live_dir = os.path.join(store, LIVE, ORG_L)
dorm_dir = os.path.join(store, DORM, ORG_D)
os.makedirs(live_dir), os.makedirs(dorm_dir), os.makedirs(home)
projects = os.path.join(home, ".claude", "projects", "proj")
os.makedirs(projects)

# two sessions in the live store, each with a transcript so they are syncable
for i in (1, 2):
    sid = "%032d" % i
    with open(os.path.join(projects, sid + ".jsonl"), "w") as fh:
        fh.write('{"type":"user"}\n')
    with open(os.path.join(live_dir, "local_%s.json" % sid), "w") as fh:
        json.dump({"appSessionId": sid, "cliSessionId": sid,
                   "title": "session %d" % i, "cwd": projects}, fh)
with open(os.path.join(home, ".claude.json"), "w") as fh:
    json.dump({"oauthAccount": {"accountUuid": LIVE, "organizationUuid": ORG_L,
                                "emailAddress": "live@example.com"}}, fh)
with open(os.path.join(root, "Claude", "config.json"), "w") as fh:
    json.dump({"lastKnownAccountUuid": LIVE}, fh)

env = ccs.default_env()
env.home = home
# projects_root is derived from home inside default_env(), so overriding home
# alone leaves it pointing at the REAL profile and every session looks like it
# has no transcript.
env.projects_root = os.path.join(home, ".claude", "projects")
env.store_candidates = [store]
env.ops_dir = os.path.join(root, "journal", "ops")
env.moved_log = os.path.join(root, "journal", "moved-log.jsonl")
env.process_lister = lambda: []          # no desktop app running

before = sorted(os.listdir(dorm_dir))
check("destination starts empty", before == [], str(before))

app = type("E", (), {"env": env})()
check("no undo offered before anything ran",
      gp.SyncApp._find_undoable_sync(app) is None)

m = ccs.plan_sync(env, ccs.SyncFlags())
check("plan finds both sessions", len(m["rows"]) == 2, str(len(m["rows"])))
final = ccs.run_sync(env, m)
check("apply completes", final == "completed", str(final))
after_sync = sorted(os.listdir(dorm_dir))
check("rows landed in the destination", len(after_sync) == 2, str(len(after_sync)))

target = gp.SyncApp._find_undoable_sync(app)
check("undo button appears after the sync", target is not None)
if target:
    check("  it names the right op and row count",
          target[0] == m["op_id"] and target[1] == 2, str(target[:2]))
    check("  and carries a live-override note field (empty when unused)",
          len(target) == 4 and target[3] == "", repr(target[3]))

    # the drift refusal: destination touched a row after the copy
    victim = os.path.join(dorm_dir, after_sync[0])
    original = open(victim, "rb").read()
    with open(victim, "wb") as fh:
        fh.write(b'{"appSessionId":"changed"}')
    ops = [o for o in ccs.list_ops(env) if o.manifest["op_id"] == target[0]]
    try:
        ccs.undo_sync(env, ops[0])
        check("drifted row refuses the undo", False, "it deleted anyway!")
    except ccs.Refusal as exc:
        check("drifted row refuses the undo", True)
        check("  and nothing was removed", len(os.listdir(dorm_dir)) == 2)
    with open(victim, "wb") as fh:      # restore and undo for real
        fh.write(original)

    ops = [o for o in ccs.list_ops(env) if o.manifest["op_id"] == target[0]]
    result = ccs.undo_sync(env, ops[0])
    check("undo succeeds once the row matches again", result == "undone", str(result))
    check("destination is back to empty", sorted(os.listdir(dorm_dir)) == before,
          str(sorted(os.listdir(dorm_dir))))
    check("source store untouched", len(os.listdir(live_dir)) == 2)
    check("transcripts untouched", len(os.listdir(projects)) == 2)
    check("no undo offered once it is undone",
          gp.SyncApp._find_undoable_sync(app) is None)

shutil.rmtree(root, ignore_errors=True)
shutil.rmtree(GUIDIR, ignore_errors=True)
print()
print("ALL PASS" if all(ok) else "SOME CHECKS FAILED")
sys.exit(0 if all(ok) else 1)
