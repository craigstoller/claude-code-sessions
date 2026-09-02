"""The two window fixes, driven against a real (withdrawn) Tk window.

1. The --live assertion survives a replan. It used to be cleared on EVERY
   replan, so a single ordinary sitting - assert, choose destination, assert,
   tick "Also refresh", assert - asked the same question four times. Field
   report, 2026-08-19: "I wound up selecting the account I was in, like, four
   times, which seemed excessive."
2. "only where mine is newer" is wired to sync's newer_only, defaults ON, and
   is live only while the box it qualifies is ticked.

Instantiates SyncApp for real (root withdrawn, planning stubbed) rather than
inspecting source, so a regression in the actual control flow fails here.
"""
import json
import os
import shutil
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import claude_code_sessions as ccs  # noqa: E402

GUI = os.path.join(tempfile.mkdtemp(), "gp2.py")
shutil.copy(os.path.join(REPO, "claude_code_sessions_gui.py"), GUI)
sys.path.insert(0, os.path.dirname(GUI))
import gp2  # noqa: E402

import tkinter as tk  # noqa: E402

ok = []


def check(name, cond, extra=""):
    print("%s %s%s" % ("OK " if cond else "BAD", name, ("  " + extra) if extra else ""))
    ok.append(bool(cond))


# ------------------------------------------------------------------ synthetic env
root_dir = tempfile.mkdtemp(prefix="guitest-")
home = os.path.join(root_dir, "home")
store = os.path.join(root_dir, "Claude", "claude-code-sessions")
A, B = "a" * 32, "b" * 32
ORG = "1" * 32
for a in (A, B):
    os.makedirs(os.path.join(store, a, ORG))
os.makedirs(os.path.join(home, ".claude", "projects"))
with open(os.path.join(home, ".claude.json"), "w") as fh:
    json.dump({"oauthAccount": {"accountUuid": A, "organizationUuid": ORG,
                                "emailAddress": "live@example.com"}}, fh)


# Captured BEFORE patching: gp2.ccs is the same module object as ccs, so
# assigning gp2.ccs.default_env rebinds the very function this calls.
_real_default_env = ccs.default_env
_real_plan_sync = ccs.plan_sync


def fake_env():
    env = _real_default_env()
    env.home = home
    env.projects_root = os.path.join(home, ".claude", "projects")
    env.store_candidates = [store]
    env.ops_dir = os.path.join(root_dir, "journal", "ops")
    env.moved_log = os.path.join(root_dir, "journal", "moved-log.jsonl")
    env.process_lister = lambda: []
    return env


gp2.ccs.default_env = fake_env
gp2.load_pref = lambda: ""
gp2.save_pref = lambda _v: None

# Record what the planner is asked for, and never touch a real store.
calls = []
FAKE_ROWS, FAKE_TALLY = [], {}        # what the next fake plan carries


def fake_plan(env, flags):
    calls.append(flags)
    return {"op_type": "sync", "source_account": A, "source_org": ORG,
            "source_email": "live@example.com", "source_path": store,
            "dest_account": B, "dest_org": ORG, "dest_email": "dorm@example.com",
            "dest_email_source": "", "dest_path": store, "verbatim": False,
            "update": flags.update, "newer_only": flags.newer_only,
            "rows": [dict(r) for r in FAKE_ROWS], "tally": dict(FAKE_TALLY)}


gp2.ccs.plan_sync = fake_plan


class _Inline:
    """Run the 'thread' body inline on the main thread.

    Not cosmetic. Tkinter's `after()` can only be called from the thread that
    owns the interpreter unless a real mainloop is running, and this harness
    pumps with update() instead - so a genuine worker thread's callback raises
    "main thread is not in main loop" and _plan_done never runs, leaving every
    control stuck in its busy(True) state. Running inline exercises the same
    code path, deterministically, with no sleeps.
    """

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._t, self._a, self._k = target, args, kwargs or {}

    def start(self):
        self._t(*self._a, **self._k)


class _FakeThreading:
    Thread = _Inline


gp2.threading = _FakeThreading

tkroot = tk.Tk()
tkroot.withdraw()
app = gp2.SyncApp(tkroot)


def settle():
    """Let the queued after() callbacks run. Planning is inline, so this only
    has to drain the event queue - no waiting, no sleeps, no flake."""
    for _ in range(20):
        tkroot.update()


def mapped(w):
    """Packed into its parent. winfo_ismapped() is False for every widget while
    the root is withdrawn, which is exactly how this harness runs."""
    return bool(w.winfo_manager())


settle()
check("the window planned on open", len(calls) >= 1)
check("  and the controls were released again",
      str(app.refresh_btn.cget("state")) == "normal",
      str(app.refresh_btn.cget("state")))

# ------------------------------------------------------- 1. the assertion sticks
app.live_choice = "/some/store/path"
before = len(calls)
app.refresh()
settle()
check("a plain replan KEEPS the live assertion",
      app.live_choice == "/some/store/path", repr(app.live_choice))
check("  and passes it to the planner",
      calls[-1].live == "/some/store/path", repr(calls[-1].live))

app.refresh()
settle()
check("a second replan still keeps it", app.live_choice == "/some/store/path")

app._on_update_toggle()          # ticking the box is a replan too
settle()
check("ticking 'Also refresh' keeps it", app.live_choice == "/some/store/path")

app.forget_live()
settle()
check("only an explicit change clears it", app.live_choice == "")
check("  and the planner is told nothing", calls[-1].live == "")

# the button that does that is only offered while an assertion is held
app.live_choice = "/some/store/path"
app._sync_live_button()
check("'Change signed-in account' appears while asserted",
      mapped(app.live_btn))
app.live_choice = ""
app._sync_live_button()
check("  and goes away when there is nothing to change",
      not mapped(app.live_btn))

# ------------------------------------------------------- 2. the newer-only box
check("'only where mine is newer' defaults ON", app.newer_var.get() is True)
app.update_var.set(False)
app._on_update_toggle()
settle()
check("  disabled while 'Also refresh' is unticked",
      str(app.newer_chk.cget("state")) == "disabled",
      str(app.newer_chk.cget("state")))
app.update_var.set(True)
app._on_update_toggle()
settle()
check("  enabled once it can mean something",
      str(app.newer_chk.cget("state")) == "normal",
      str(app.newer_chk.cget("state")))
check("  and reaches the planner as newer_only",
      calls[-1].update is True and calls[-1].newer_only is True,
      "update=%s newer_only=%s" % (calls[-1].update, calls[-1].newer_only))

app.newer_var.set(False)
app.refresh()
settle()
check("unticking it turns newer_only off", calls[-1].newer_only is False)

# busy() must not resurrect the qualifier while its parent is unticked
app.update_var.set(False)
app._on_update_toggle()
settle()
app.busy(True)
app.busy(False)
check("releasing the controls leaves it disabled when unticked",
      str(app.newer_chk.cget("state")) == "disabled",
      str(app.newer_chk.cget("state")))

# ------------------------- 0.15.1 (E, part 2). the duplicate-title warning
# plan_sync annotates each add with dup_conversation / dup_title (the
# destination already opens that conversation under another row file / already
# has a row under that title); the pane must say so ABOVE Apply, in numbers.
FAKE_ROWS[:] = [
    {"name": "local_%d.json" % i, "title": t, "is_update": False,
     "session_id": "s%d" % i, "dup_conversation": dc, "dup_title": dt,
     "pre_b64": None, "post_b64": "e30=", "written": False}
    for i, (t, dc, dt) in enumerate([("ACME-REVIEW session", True, True),
                                     ("Northwind backtest", True, False),
                                     ("Quarterly board report", False, False)])]
FAKE_TALLY.update({"dup_conversation": ["ACME-REVIEW session",
                                        "Northwind backtest"],
                   "dup_title": ["ACME-REVIEW session"]})
app.refresh()
settle()
warn = app.sync_warning.cget("text")
check("the warning counts the rows that would duplicate a title",
      mapped(app.sync_warning)
      and "1 of 3 rows would duplicate a title already in that sidebar" in warn,
      warn)
check("  and points back at Level as the routine", "Level" in warn)
# Bottom-pinned right after the bar in the pack list (GUI polish, Change
# 1): the bar takes the bottom edge, the warning the slice above it, so it
# sits directly above the buttons whatever the window's height.
slaves = app.sync_tab.pack_slaves()
check("  it sits above the Apply bar",
      app.sync_warning.pack_info()["side"] == "bottom"
      and app.sync_bar.pack_info()["side"] == "bottom"
      and slaves.index(app.sync_warning) == slaves.index(app.sync_bar) + 1,
      str([str(w) for w in slaves]))
pane_lines = app.text.get("1.0", "end").splitlines()
check("the tally lines count both kinds",
      any(l.startswith("already open there under another row file")
          and l.rstrip().endswith(": 2") for l in pane_lines),
      "\n".join(pane_lines[:12]))
check("  and the duplicate-title count",
      any(l.startswith("would duplicate a title already there")
          and l.rstrip().endswith(": 1") for l in pane_lines))
FAKE_ROWS[:] = [dict(FAKE_ROWS[2])]
FAKE_TALLY.clear()
app.refresh()
settle()
check("no duplicate titles, no warning", not mapped(app.sync_warning))

tkroot.destroy()
shutil.rmtree(root_dir, ignore_errors=True)
print()
print("ALL PASS" if all(ok) else "SOME CHECKS FAILED")
sys.exit(0 if all(ok) else 1)
