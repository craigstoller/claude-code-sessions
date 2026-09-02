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
# The identity files DISAGREE here: an answer is bound to the pair it was
# given for and drops the moment a plan reads files that no longer show it
# (GUI polish, Change 2 item 5, rule (b)).
with open(os.path.join(root_dir, "Claude", "config.json"), "w") as fh:
    json.dump({"lastKnownAccountUuid": B}, fh)


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
gp2.ccs.run_sync = lambda env, manifest: "completed"   # never a real write


class Modals:
    """Every messagebox call recorded, answered per DEFAULT."""

    def __init__(self):
        self.calls = []
        self.default = True

    def askokcancel(self, title, message, **kw):
        self.calls.append(("askokcancel", title, message, kw))
        return self.default

    def showwarning(self, title, message, **kw):
        self.calls.append(("showwarning", title, message, kw))

    def showerror(self, title, message, **kw):
        self.calls.append(("showerror", title, message, kw))


modals = Modals()
gp2.messagebox = modals


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

# A completed sync op in the journal BEFORE the window opens: the Undo
# button's state at open must come from the Level render, since the sync
# pane no longer plans at open (GUI polish, Change 2 item 3).
_op_dir = os.path.join(root_dir, "journal", "ops", "20260901T000000Z-abcdef")
os.makedirs(_op_dir)
with open(os.path.join(_op_dir, "manifest.json"), "w", encoding="utf-8") as fh:
    json.dump({"op_id": "20260901T000000Z-abcdef", "status": "completed",
               "op_type": "sync", "dest_email": "dorm@example.com",
               "rows": [{"written": True}],
               "history": [{"status": "journaled", "at": 1_800_000_000.0}]},
              fh)

# The doctor, stubbed to a clean report, counted: Health runs it on its
# first selection (GUI polish, Change 6) through the same deferral the sync
# plan uses.
doctor_runs = []
CLEAN = {"stores": {"status": "found", "roots": ["/x"]}, "row_count": 2,
         "row_errors": [], "blank_rows": [], "dead_rows": [],
         "legacy_folders": [], "unlisted_transcripts": [],
         "nonterminal_ops": [], "stale_lock": False, "unknown_layout": []}


def fake_doctor(env):
    doctor_runs.append(1)
    return dict(CLEAN)


gp2.ccs.gather_doctor = fake_doctor

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


def walk(w):
    """W's descendants, depth first - the banner builder nests its line and
    buttons in a row frame."""
    out = []
    for c in w.winfo_children():
        out.append(c)
        out += walk(c)
    return out


def above(a, b):
    """A is placed above B in their common parent - by grid row where both
    are gridded, else by pack order."""
    if a.winfo_manager() == "grid" and b.winfo_manager() == "grid":
        return int(a.grid_info()["row"]) < int(b.grid_info()["row"])
    slaves = a.master.pack_slaves()
    return slaves.index(a) < slaves.index(b)


settle()
# ------------------- GUI polish (Change 2 item 3). the sync pane plans lazily
check("the window did NOT plan the sync pane at open", len(calls) == 0,
      str(len(calls)))
check("  nor run the doctor", len(doctor_runs) == 0)
# An unmapped notebook reports no selection until it is displayed; either
# answer is the home tab.
check("  Level is the selected tab",
      app.nb.select() in ("", str(app.level_tab)), repr(app.nb.select()))
check("  and the Undo button's state is right at open, before any tab is "
      "visited - it comes from the Level render",
      app.undo_target is not None and mapped(app.undo_btn)
      and app.undo_target["op_id"] == "20260901T000000Z-abcdef",
      str(app.undo_target))
check("  and the controls were released again",
      str(app.refresh_btn.cget("state")) == "normal",
      str(app.refresh_btn.cget("state")))
app.nb.select(app.sync_tab)
settle()
check("the first selection of One session plans it", len(calls) == 1,
      str(len(calls)))
app.nb.select(app.level_tab)
settle()
app.nb.select(app.sync_tab)
settle()
check("  and a later visit does not plan again - Refresh does",
      len(calls) == 1, str(len(calls)))
check("  the doctor has still not run - Health was never selected",
      len(doctor_runs) == 0)

# ------------------------------------------------------- 1. the assertion sticks
app._apply_live("answered", {"path": "/some/store/path", "pair": (A, B),
                             "label": "live@example.com"})
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

# the button that does that is only offered while an assertion is held -
# on the in-force line of the One-session banner, the same line Level shows
app._apply_live("answered", {"path": "/some/store/path", "pair": (A, B),
                             "label": "live@example.com"})
app._render_sync_banner()


def change_button():
    return [w for w in walk(app.sync_banner)
            if isinstance(w, gp2.ttk.Button)
            and w.cget("text") == "Change signed-in account"]


check("'Change signed-in account' appears while asserted",
      len(change_button()) == 1)
check("  under the in-force line that names the answer",
      any(isinstance(w, gp2.ttk.Label)
          and "under your assertion" in str(w.cget("text"))
          and "live@example.com" in str(w.cget("text"))
          for w in walk(app.sync_banner)))
app._apply_live("explicit_change")
app._render_sync_banner()
check("  and goes away when there is nothing to change",
      not change_button() and not app.sync_banner.winfo_children())

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
# GUI polish (Change 6): the status counts ROW FILES, and how many are
# conversations the sidebar already opens under another row.
check("the status line counts row files, not sessions",
      app.status.get() == "3 row files missing from dorm@example.com's "
                          "sidebar - 2 of them for conversations it already "
                          "opens under another row", app.status.get())
check("  and the detail no longer carries the static running-app sentence",
      "must be closed" not in app.detail.get(), app.detail.get())
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
FAKE_ROWS[:] = []
app.refresh()
settle()
check("the empty state claims only a row-file fact",
      app.status.get() == "No row files to add to dorm@example.com's sidebar",
      app.status.get())
app.only_var.set("zzz")
app.refresh()
settle()
check("  and the filtered empty state uses the same vocabulary",
      app.status.get() == 'No row files matching "zzz" to add to '
                          "dorm@example.com's sidebar", app.status.get())
app.only_var.set("")

# ------------------- GUI polish (Change 3). the tab strip and the role lines
tabs = [app.nb.tab(t, "text") for t in app.nb.tabs()]
check("the tabs read Level | Health | One session",
      tabs == ["Level", "Health", "One session"], str(tabs))
check("  the exception tab is third and named 'One session'",
      app.nb.tabs()[2] == str(app.sync_tab))
roles = [(app.level_role, app.level_tab, "The routine."),
         (app.health_role, app.health_tab, "Diagnostic."),
         (app.sync_role, app.sync_tab, "The exception.")]
check("a role line sits on every tab, above the status line",
      all(mapped(lbl) and str(lbl.cget("text")).startswith(lead)
          and above(lbl, status)
          for (lbl, tab, lead), status in zip(
              roles, (app.level_status_label, app.health_status_label,
                      app.sync_status_label))),
      str([str(l.cget("text"))[:20] for l, _t, _s in roles]))
check("  the One-session role line says what the tab is for",
      "Copy one session" in app.sync_role.cget("text")
      and "Level is the routine" in app.sync_role.cget("text"))
check("  and it replaces the 0.15.1 guidance line - no preamble line net",
      not hasattr(app, "sync_guidance"))

# ------------------------------ GUI polish (Change 3). the single-session gate
def row(i, is_update=False):
    return {"name": "local_%d.json" % i, "title": "Northwind backtest %d" % i,
            "is_update": is_update, "session_id": "s%d" % i,
            "pre_b64": None, "post_b64": "e30=", "written": False}


def apply_state():
    return str(app.apply_btn.cget("state"))


app.update_var.set(False)
app._on_update_toggle()
FAKE_ROWS[:] = [row(1)]
app.refresh()
settle()
check("a one-row plan (an add) enables Apply without consent",
      apply_state() == "normal" and not app.consent_var.get(), apply_state())
FAKE_ROWS[:] = [row(1, is_update=True)]
app.refresh()
settle()
check("  a one-row refresh is live too - the tab's routine use",
      apply_state() == "normal", apply_state())
FAKE_ROWS[:] = [row(1), row(2), row(3)]
app.refresh()
settle()
check("a three-row plan disables Apply until consent is given",
      apply_state() == "disabled", apply_state())
check("  the consent box names the count",
      app.consent_chk.cget("text") == "copy every row this plan lists (3)",
      app.consent_chk.cget("text"))
check("  and is off", not app.consent_var.get())
app.consent_var.set(True)
app._on_consent_toggle()
check("ticking it enables Apply", apply_state() == "normal", apply_state())
# A replan returning the same rows in a different order, driven through the
# real _plan_done, keeps the tick.
FAKE_ROWS[:] = [row(3), row(1), row(2)]
app.refresh()
settle()
check("a replan with the same rows in another order keeps the tick",
      app.consent_var.get() and apply_state() == "normal",
      "ticked=%s state=%s" % (app.consent_var.get(), apply_state()))
# Ticking "Also refresh" adds refresh rows: a different row set.
FAKE_ROWS[:] = [row(1), row(2), row(3), row(4, is_update=True)]
app.update_var.set(True)
app._on_update_toggle()
settle()
check("a replan that changes the row set clears the tick",
      not app.consent_var.get() and apply_state() == "disabled",
      "ticked=%s state=%s" % (app.consent_var.get(), apply_state()))
check("  and updates the count",
      app.consent_chk.cget("text") == "copy every row this plan lists (4)",
      app.consent_chk.cget("text"))
app.consent_var.set(True)
app._on_consent_toggle()
check("  re-ticked, Apply is live again", apply_state() == "normal")
app.on_apply()
settle()
check("an apply clears the tick",
      not app.consent_var.get() and app._consent_for is None,
      "ticked=%s" % app.consent_var.get())
check("  (the fake apply ran to completion)",
      app.status.get().startswith("Copied"), app.status.get())
# GUI polish (Change 6): the two boxes default to Cancel, and the second
# box's title follows its body.
boxes = [c for c in modals.calls if c[0] == "askokcancel"]
check("sync's two confirmation boxes default to Cancel",
      len(boxes) >= 2 and all(c[3].get("default") == "cancel" for c in boxes[-2:]),
      str([(c[1], c[3]) for c in boxes[-2:]]))
check("  a mixed plan's second box is titled for what it does",
      boxes and boxes[-1][1] == "Add and refresh rows?", boxes[-1][1])
FAKE_ROWS[:] = [row(9, is_update=True)]
app.update_var.set(True)
app._on_update_toggle()
settle()
modals.calls[:] = []
app.on_apply()
settle()
titles = [c[1] for c in modals.calls if c[0] == "askokcancel"]
check("  a pure refresh's second box is 'Refresh rows?'",
      titles == ["Overwrite existing rows?", "Refresh rows?"], str(titles))

tkroot.destroy()
shutil.rmtree(root_dir, ignore_errors=True)
print()
print("ALL PASS" if all(ok) else "SOME CHECKS FAILED")
sys.exit(0 if all(ok) else 1)


# ---------------- GUI polish (Change 2 item 3). one deferral mechanism, two tabs
# A fresh window per scenario: the first-visit flags are per window.


def fresh_window():
    calls[:] = []
    doctor_runs[:] = []
    r = tk.Tk()
    r.withdraw()
    a = gp2.SyncApp(r)

    def pump():
        for _ in range(20):
            r.update()
    pump()
    return a, r, pump


# 1. A first visit while a worker runs defers, and dispatches only if the
#    tab is still selected when the busy count reaches zero.
app, tkroot, settle = fresh_window()
app.busy(True)                        # a read in flight
app.nb.select(app.sync_tab)
settle()
check("a first visit while busy starts no worker", len(calls) == 0,
      str(len(calls)))
app.nb.select(app.level_tab)          # the user clicks away
settle()
app.busy(False)
settle()
check("  and when the busy count reaches zero with another tab selected, "
      "nothing dispatches", len(calls) == 0, str(len(calls)))
app.nb.select(app.sync_tab)           # the next visit gets the plan
settle()
check("  the next visit plans it", len(calls) == 1, str(len(calls)))
tkroot.destroy()

# 2. Clicking through Level -> Health -> One session during a read dispatches
#    at most one deferred worker: the tab selected when the count hits zero.
app, tkroot, settle = fresh_window()
app.busy(True)
app.nb.select(app.health_tab)
settle()
app.nb.select(app.sync_tab)
settle()
check("two first visits during a read start nothing", len(calls) == 0
      and len(doctor_runs) == 0)
app.busy(False)
settle()
check("  the busy count reaching zero dispatches ONE worker - the selected "
      "tab's", len(calls) == 1 and len(doctor_runs) == 0,
      "plans=%d doctor=%d" % (len(calls), len(doctor_runs)))
app.nb.select(app.health_tab)
settle()
check("  and Health's pending run waits for its next visit",
      len(doctor_runs) == 1 and len(calls) == 1,
      "plans=%d doctor=%d" % (len(calls), len(doctor_runs)))
check("  its report rendered without a press",
      app.health_text.get("1.0", "end").startswith("Nothing is blocking"),
      app.health_text.get("1.0", "end")[:40])
tkroot.destroy()

# 3. A pending run is dropped, not dispatched, when the window is closing.
app, tkroot, settle = fresh_window()
app.busy(True)
app.nb.select(app.sync_tab)
settle()
app._close_after_worker = True        # a close confirmed behind the worker
app.busy(False)
settle()
check("a pending run is dropped when the window is closing", len(calls) == 0,
      str(len(calls)))
check("  and the flag does not survive to dispatch later",
      not any(app._pending.values()), str(app._pending))
tkroot.destroy()
