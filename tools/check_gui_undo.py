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

# ---------------------------------------------------------------------
# GUI 2.0 (harness item 15): the button generalizes to _find_undoable_op -
# sync, retitle and converge are offered, each labeled by what it reverses;
# a CLI-only op on top means NO button rather than reaching past it. The
# unresolved-op gate itself (Undo and Apply disabled everywhere until a
# refresh finds none) is pinned window-level in check_gui_level.py.
import json as _json  # noqa: E402
import time as _time  # noqa: E402

pm = ccs.plan_retitle(env, ccs.RetitleFlags(only="0" * 31 + "1",
                                            title="Northwind renamed one"))
ccs.run_retitle(env, pm)
t = gp.SyncApp._find_undoable_op(app)
check("latest = retitle -> offered", t is not None and t["type"] == "retitle",
      str(t and t["type"]))
if t:
    check("  labeled as the rename it reverses", t["label"] == "Undo last rename")
    check("  and the sync-shaped wrapper stays quiet",
          gp.SyncApp._find_undoable_sync(app) is None)

# A padding row makes the dormant account a converge destination again
# (the populated-one rule), so the two sessions spread there: 2 rows. The
# fixture's one-line transcripts cannot populate a row (_transcript_facts
# needs a cwd, timestamps and a model), so give the two sessions real ones.
with open(os.path.join(dorm_dir, "local_pad.json"), "w") as fh:
    _json.dump({"sessionId": "local-pad", "cliSessionId": "f" * 32,
                "title": "Padding", "lastActivityAt": 1}, fh)
for i in (1, 2):
    sid = "%032d" % i
    with open(os.path.join(projects, sid + ".jsonl"), "w") as fh:
        fh.write(_json.dumps(
            {"cwd": projects, "timestamp": "2026-08-01T00:00:00.000Z",
             "type": "user",
             "message": {"role": "user", "content": "hello"}}) + "\n")
        fh.write(_json.dumps(
            {"timestamp": "2026-08-01T00:10:00.000Z", "type": "assistant",
             "message": {"role": "assistant", "model": "claude-opus-5",
                         "content": [{"type": "text", "text": "hi"}]}})
            + "\n")
cm = ccs.plan_converge(env, ccs.ConvergeFlags())
check("converge plans the spread", len(cm["rows"]) == 2, str(len(cm["rows"])))
final = ccs.run_converge(env, cm)
check("  and applies", final == "completed", str(final))
t = gp.SyncApp._find_undoable_op(app)
check("latest = converge -> offered with the row count",
      t is not None and t["type"] == "converge"
      and t["label"] == "Undo last converge (2 rows)", str(t))

# A completed CLI new-row lands on top: this window does not undo those,
# and quietly reaching past it to the converge would undo something other
# than what the user last did.
newrow_dir = os.path.join(env.ops_dir, "20990101T000000Z-feedaa")
os.makedirs(newrow_dir)
with open(os.path.join(newrow_dir, "manifest.json"), "w") as fh:
    _json.dump({"op_id": "20990101T000000Z-feedaa", "status": "completed",
                "op_type": "new-row", "rows": [{"written": True}],
                "history": [{"status": "journaled",
                             "at": _time.time() + 9999}]}, fh)
check("latest = CLI new-row -> no button",
      gp.SyncApp._find_undoable_op(app) is None)

# An unresolved op is what the window-level gate scans for - pin that the
# shared selection helper sees exactly what cmd_recover would.
stuck_dir = os.path.join(env.ops_dir, "20990101T000001Z-feedbb")
os.makedirs(stuck_dir)
with open(os.path.join(stuck_dir, "manifest.json"), "w") as fh:
    _json.dump({"op_id": "20990101T000001Z-feedbb", "status": "writing",
                "op_type": "repoint",
                "history": [{"status": "journaled",
                             "at": _time.time() + 10000}]}, fh)
entries = gp._scan_interrupted(env)
check("the unresolved op is detected by the shared scan",
      len(entries) == 1
      and entries[0][0]["op_id"] == "20990101T000001Z-feedbb")
listing = gp._interrupted_lines(entries, _time.time() + 10001)
check("  and the listing marks it and carries the copyable command",
      any("listed first" in line for line in listing)
      and any("claude-code-sessions recover" in line for line in listing))

# ------------------ GUI polish (Change 5, harness item 18). the window bar
# The window for real (root withdrawn, workers inline): Undo at the left,
# the Chrome-helper checkbox in the centre, Close at the right - and the
# mutation gate on every tab unchanged.
import threading as _real_threading  # noqa: E402
import tkinter as tk  # noqa: E402


class _Inline(object):
    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._t, self._a, self._k = target, args, kwargs or {}

    def start(self):
        self._t(*self._a, **self._k)


class _FakeThreading(object):
    Thread = _Inline
    current_thread = staticmethod(_real_threading.current_thread)
    main_thread = staticmethod(_real_threading.main_thread)
    Event = _real_threading.Event


gp.threading = _FakeThreading
gp.load_pref = lambda: ""
gp.save_pref = lambda _v: None
gp.ccs.default_env = lambda: env
# The unresolved op planted above still sits in the journal: the gate is up.
tkroot = tk.Tk()
tkroot.withdraw()
app = gp.SyncApp(tkroot)


def settle():
    for _ in range(30):
        tkroot.update()


settle()
bar = app.close_btn.master
check("Undo, the Chrome checkbox and Close share the window bar",
      app.undo_btn.master is bar and app.trust_chk.master is bar
      and bar is not app.sync_bar)
check("  Close is at the right", app.close_btn.pack_info()["side"] == "right")
check("  the Chrome checkbox sits in the centre",
      app.trust_chk.pack_info()["side"] == "left"
      and app.trust_chk.pack_info()["expand"] in (1, True, "1"))
check("  the gate holds Apply and Undo down on every tab",
      "interrupted operation" in app.gate_var.get()
      and str(app.undo_btn.cget("state")) == "disabled"
      and str(app.apply_btn.cget("state")) == "disabled"
      and str(app.level_apply_btn.cget("state")) == "disabled",
      app.gate_var.get())
# Resolve the planted ops fixture-side so an undoable op is offered.
for name in ("20990101T000001Z-feedbb", "20990101T000000Z-feedaa"):
    shutil.rmtree(os.path.join(env.ops_dir, name), ignore_errors=True)
app.on_doctor()
settle()
check("  with the journal clean the gate lifts", app.gate_var.get() == "")
app._update_undo_button()
check("Undo is offered at the LEFT of the bar, before the checkbox",
      app.undo_target is not None and app.undo_btn.winfo_manager()
      and app.undo_btn.pack_info()["side"] == "left"
      and bar.pack_slaves().index(app.undo_btn)
      < bar.pack_slaves().index(app.trust_chk),
      str([str(w).split(".")[-1] for w in bar.pack_slaves()]))
tkroot.destroy()

shutil.rmtree(root, ignore_errors=True)
shutil.rmtree(GUIDIR, ignore_errors=True)
print()
print("ALL PASS" if all(ok) else "SOME CHECKS FAILED")
sys.exit(0 if all(ok) else 1)
