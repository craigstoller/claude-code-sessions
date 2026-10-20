"""The window's saved destination after the Desktop data-root move, driven
against a real (withdrawn) Tk window on synthetic moved layouts.

Claude Desktop 2.42304.2 moved its user data from
%LOCALAPPDATA%\\Packages\\Claude_<pkg>\\LocalCache\\Roaming\\Claude\\ to
%LOCALAPPDATA%\\Claude-Data\\claude_<pkg>\\. A destination the window saved
before that names a folder under the old root, and every plan refused it with
a bare "Refused". The maintainer's ruling of 2026-10-20 (docs/internals.md,
"Amendment 2026-10-20"):

1. No write target changes without a pick, and the preference file changes
   only on a pick. "Change destination" asks without erasing; a pick saves the
   path on the candidate's own line.
2. A saved value proven gone is set aside and the window asks.
3. A saved store whose account is now signed in is named rather than asked
   about; the candidate with the saved ids is marked.

Real planning (plan_sync over the synthetic stores), inline "threads", the
preference file in a temp folder, and every dialog answered by a script.
Touches nothing real. Fake ids only.
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
            os.path.join(GUIDIR, "gsd.py"))
sys.path.insert(0, GUIDIR)
import gsd as gp  # noqa: E402

import tkinter as tk  # noqa: E402

ok = []


def check(name, cond, extra=""):
    # The marker's arrow and a picker's warning sign are not in a Windows
    # console's code page; print them escaped rather than die mid-run.
    line = "%s %s%s" % ("OK " if cond else "BAD", name,
                        ("  " + str(extra)) if extra else "")
    print(line.encode("ascii", "backslashreplace").decode("ascii"))
    ok.append(bool(cond))


A, B, C = "a" * 32, "b" * 32, "c" * 32
O1, O2, O3 = "1" * 32, "2" * 32, "3" * 32

GONE = "no longer names a store"
SIGNED = "signed into now"
POINTER = "Change destination picks another"
MARK = "same account and org ids as your saved destination"
MULTI = "More than one other account store on this machine."
SINGLE = "One other account store on this machine."
ALL_EMPTY = "All of them are empty"


class _Inline:
    """Run the worker inline: after() needs the interpreter's own thread
    when no mainloop runs (see check_gui_live_and_newer.py)."""

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._t, self._a, self._k = target, args, kwargs or {}

    def start(self):
        self._t(*self._a, **self._k)


class _FakeThreading:
    Thread = _Inline


gp.threading = _FakeThreading

ROOTS = []


def layout(stores, live, classic=(), disagree=None):
    """A moved layout: STORES under the moved root, CLASSIC under
    %APPDATA%\\Claude, the old package folder holding only the app's marker,
    and both identity files naming LIVE (or config.json naming DISAGREE)."""
    root = tempfile.mkdtemp(prefix="stale-dest-")
    ROOTS.append(root)
    local = os.path.join(root, "Local")
    appdata = os.path.join(root, "Roaming")
    home = os.path.join(root, "home")
    moved = os.path.join(local, "Claude-Data", "claude_pkgx")
    old_claude = os.path.join(local, "Packages", "Claude_pkgx", "LocalCache",
                              "Roaming", "Claude")
    os.makedirs(old_claude)
    open(os.path.join(old_claude, ".user-data-left.claude_pkgx"), "w").close()
    moved_store = os.path.join(moved, "claude-code-sessions")
    classic_store = os.path.join(appdata, "Claude", "claude-code-sessions")
    os.makedirs(moved_store)
    for a, o in stores:
        os.makedirs(os.path.join(moved_store, a, o))
    for a, o in classic:
        os.makedirs(os.path.join(classic_store, a, o))
    os.makedirs(os.path.join(home, ".claude", "projects"))
    with open(os.path.join(home, ".claude.json"), "w", encoding="utf-8") as fh:
        json.dump({"oauthAccount": {"accountUuid": live[0],
                                    "organizationUuid": live[1]}}, fh)
    with open(os.path.join(moved, "config.json"), "w", encoding="utf-8") as fh:
        json.dump({"lastKnownAccountUuid": disagree or live[0]}, fh)
    cands, superseded, errors = ccs._windows_store_layout(local, appdata)
    env = ccs.default_env()
    env.home = home
    env.projects_root = os.path.join(home, ".claude", "projects")
    env.store_candidates = cands
    env.superseded_stores = superseded
    env.layout_errors = errors
    env.ops_dir = os.path.join(root, "journal", "ops")
    env.moved_log = os.path.join(root, "journal", "moved-log.jsonl")
    env.process_lister = lambda: []
    env.helper_roots = []
    paths = {
        "moved": lambda a, o: os.path.join(moved_store, a, o),
        "old": lambda a, o: os.path.join(old_claude, "claude-code-sessions", a, o),
        "classic": lambda a, o: os.path.join(classic_store, a, o),
        "root": root,
        "pref": os.path.join(root, "gui-pref.json"),
    }
    return env, paths


def walk(w):
    out = []
    for c in w.winfo_children():
        out.append(c)
        out += walk(c)
    return out


def same(p, q):
    return bool(p) and bool(q) and os.path.normcase(os.path.normpath(p)) == \
        os.path.normcase(os.path.normpath(q))


class Session:
    """One window over one layout, its dialogs answered from ANSWERS: each
    "cancel", or a predicate choosing the candidate button by its text."""

    def __init__(self, env, paths, saved, answers=()):
        self.paths = paths
        self.answers = list(answers)
        self.dialogs = []
        gp.PREF_PATH = paths["pref"]
        with open(paths["pref"], "w", encoding="utf-8") as fh:
            json.dump({"to": saved}, fh)
        gp.ccs.default_env = lambda: env
        self.tkroot = tk.Tk()
        self.tkroot.withdraw()
        self.tkroot.wait_window = self._answer
        self.app = gp.SyncApp(self.tkroot)
        self.settle()
        self.app.nb.select(self.app.sync_tab)
        self.settle()

    def settle(self):
        for _ in range(30):
            self.tkroot.update()

    def pref(self):
        with open(self.paths["pref"], encoding="utf-8") as fh:
            return (json.load(fh) or {}).get("to", "")

    def _answer(self, win):
        app = self.app
        labels = [str(w.cget("text")) for w in walk(win)
                  if isinstance(w, gp.ttk.Label)]
        buttons = [w for w in walk(win) if isinstance(w, gp.ttk.Button)
                   and w.cget("text") != "Cancel"]
        rec = {"headline": labels[0] if labels else "",
               "labels": labels,
               "buttons": [str(b.cget("text")) for b in buttons],
               "detail": app.detail.get(), "status": app.status.get(),
               "pref": self.pref(), "manifest": app.manifest,
               "apply": str(app.apply_btn.cget("state")),
               "consent": str(app.consent_chk.cget("state"))}
        self.dialogs.append(rec)
        answer = self.answers.pop(0) if self.answers else "cancel"
        if answer != "cancel":
            hit = [b for b in buttons if answer(str(b.cget("text")))]
            if len(hit) == 1:
                hit[0].invoke()
                return
            rec["unanswered"] = len(hit)
        app._open_dialog[1]()             # Cancel

    def text(self):
        return self.app.text.get("1.0", "end")

    def dest(self):
        return (self.app.manifest or {}).get("dest_path")

    def close(self):
        self.tkroot.destroy()


def has(p):
    return lambda text: p in text


def marked(rec, p):
    return [t for t in rec.get("buttons", []) if p in t and MARK in t]


# ------------------------------------------------------------------ 1
print("-- a destination saved before the move, two other stores of one account")
env, P = layout([(A, O1), (A, O2), (B, O2)], live=(B, O2))
s = Session(env, P, P["old"](A, O1), [has(P["moved"](A, O1))])
d = s.dialogs[0] if s.dialogs else {}
check("the window asks by itself: one picker", len(s.dialogs) == 1, str(len(s.dialogs)))
check("  headed as today", d.get("headline") == MULTI, d.get("headline"))
check("  with the reason on the detail line", GONE in d.get("detail", ""), d.get("detail"))
check("  and in the picker", any(GONE in t for t in d.get("labels", [])))
check("  no plan installed under it", d.get("manifest") is None)
check("  the file unchanged until the pick", d.get("pref") == P["old"](A, O1))
check("  the moved copy of the saved store is marked",
      len(marked(d, P["moved"](A, O1))) == 1)
check("  and only it", not marked(d, P["moved"](A, O2)))
check("the pick saves that candidate's own path", same(s.pref(), P["moved"](A, O1)),
      s.pref())
check("  and plans into it", same(s.dest(), P["moved"](A, O1)), str(s.dest()))
check("  and shows Change destination", bool(s.app.forget_btn.winfo_manager()))
s.close()

print("-- the same, two other stores of two accounts")
env, P = layout([(A, O1), (C, O3), (B, O2)], live=(B, O2))
s = Session(env, P, P["old"](A, O1), ["cancel"])
d = s.dialogs[0] if s.dialogs else {}
check("one picker, the saved account's store marked",
      len(s.dialogs) == 1 and len(marked(d, P["moved"](A, O1))) == 1)
check("  the other account's not", not marked(d, P["moved"](C, O3)))
s.close()

# ------------------------------------------------------------------ 2
print("-- a cancel after a set-aside")
env, P = layout([(A, O1), (A, O2), (B, O2)], live=(B, O2))
s = Session(env, P, P["old"](A, O1), ["cancel", "cancel"])
check("the file is unchanged", s.pref() == P["old"](A, O1), s.pref())
check("  the status says no destination is chosen",
      "No destination chosen" in s.app.status.get(), s.app.status.get())
check("  the detail keeps the reason and points at the button",
      GONE in s.app.detail.get() and "Change destination" in s.app.detail.get(),
      s.app.detail.get())
check("  nothing installed, Apply off",
      s.app.manifest is None and str(s.app.apply_btn.cget("state")) == "disabled")
check("  no second picker on its own", len(s.dialogs) == 1, str(len(s.dialogs)))
s.app.refresh()
s.settle()
check("the next plan asks again", len(s.dialogs) == 2, str(len(s.dialogs)))
check("  and the file is still unchanged", s.pref() == P["old"](A, O1))
s.close()

# ------------------------------------------------------------------ 3
print("-- one other store after the move")
env, P = layout([(A, O1), (B, O2)], live=(B, O2))
s = Session(env, P, P["old"](A, O1), ["cancel"])
d = s.dialogs[0] if s.dialogs else {}
check("a one-candidate picker opens", len(s.dialogs) == 1 and len(d.get("buttons", [])) == 1,
      str(d.get("buttons")))
check("  under its own headline", d.get("headline") == SINGLE, d.get("headline"))
check("  without the all-empty advice",
      not any(ALL_EMPTY in t for t in d.get("labels", [])))
check("  marked, since it is the saved store moved",
      len(marked(d, P["moved"](A, O1))) == 1)
check("  with no plan installed, Apply and the bulk-copy tick off",
      d.get("manifest") is None and d.get("apply") == "disabled"
      and d.get("consent") == "disabled", repr((d.get("apply"), d.get("consent"))))
check("after the cancel nothing is installed",
      s.app.manifest is None and s.app._consent_rows == 0)
s.answers = [has(P["moved"](A, O1))]
s.app.refresh()
s.settle()
check("a pick saves it", same(s.pref(), P["moved"](A, O1)), s.pref())
check("  and only then plans into it", same(s.dest(), P["moved"](A, O1)))
s.close()

print("-- one other store, of another account than the saved one")
env, P = layout([(C, O3), (B, O2)], live=(B, O2))
s = Session(env, P, P["old"](A, O1), ["cancel"])
d = s.dialogs[0] if s.dialogs else {}
check("the one-candidate picker still opens - never a silent re-target",
      len(s.dialogs) == 1 and d.get("headline") == SINGLE, d.get("headline"))
check("  unmarked", not any(MARK in t for t in d.get("buttons", [])))
check("  and nothing planned into it", s.app.manifest is None)
s.close()

# ------------------------------------------------------------------ 4
print("-- the move and an account switch together")
for name, stores, n_other in (("one other store", [(A, O1), (A, O2), (B, O2)], 1),
                              ("two other stores",
                               [(A, O1), (A, O2), (B, O2), (C, O3)], 2),
                              ("no other store", [(A, O1), (A, O2)], 0)):
    env, P = layout(stores, live=(A, O1))
    s = Session(env, P, P["old"](A, O1), ["cancel"])
    check("%s: the signed-in store is named, no picker" % name,
          not s.dialogs and SIGNED in s.app.detail.get(), s.app.detail.get())
    check("  nothing installed, the file unchanged",
          s.app.manifest is None and s.pref() == P["old"](A, O1))
    if n_other:
        s.app.forget_btn.invoke()
        s.settle()
        d = s.dialogs[0] if s.dialogs else {}
        check("  Change destination opens the pick anyway",
              len(s.dialogs) == 1 and len(d.get("buttons", [])) == n_other,
              str(len(s.dialogs)))
        check("  with the signed-in sentence above it", SIGNED in d.get("detail", ""))
        check("  a cancel does not reopen it", len(s.dialogs) == 1)
        check("  and leaves the file alone", s.pref() == P["old"](A, O1))
    s.close()

env, P = layout([(A, O1), (A, O2), (B, O2), (C, O3)], live=(A, O1))
s = Session(env, P, P["old"](A, O1), [has(P["moved"](C, O3))])
s.app.forget_btn.invoke()
s.settle()
check("after the switch, a pick from Change destination saves it",
      same(s.pref(), P["moved"](C, O3)), s.pref())
s.close()

# ------------------------------------------------------------------ 5
print("-- Change destination over a saved destination that is fine")
env, P = layout([(A, O1), (A, O2), (B, O2)], live=(B, O2))
s = Session(env, P, P["moved"](A, O2), ["cancel"])
check("the saved destination plans as today, no picker",
      not s.dialogs and same(s.dest(), P["moved"](A, O2)))
check("  Change destination is shown", bool(s.app.forget_btn.winfo_manager()))
s.app.forget_btn.invoke()
s.settle()
d = s.dialogs[0] if s.dialogs else {}
check("the button opens the picker", len(s.dialogs) == 1 and d.get("headline") == MULTI)
check("  with the file untouched while it is open", d.get("pref") == P["moved"](A, O2))
check("  and the saved store marked", len(marked(d, P["moved"](A, O2))) == 1)
check("a cancel restores the saved destination's plan",
      same(s.dest(), P["moved"](A, O2)) and s.pref() == P["moved"](A, O2))
check("  without reopening the picker", len(s.dialogs) == 1)
s.close()

env, P = layout([(A, O1), (B, O2)], live=(B, O2))
s = Session(env, P, P["moved"](A, O1), [has(P["moved"](A, O1))])
check("one other store: the saved destination plans as today",
      not s.dialogs and same(s.dest(), P["moved"](A, O1)))
s.app.forget_btn.invoke()
s.settle()
d = s.dialogs[0] if s.dialogs else {}
check("  Change destination opens the one-candidate picker, no silent plan",
      len(s.dialogs) == 1 and d.get("headline") == SINGLE and d.get("manifest") is None)
check("  and a pick saves it", same(s.pref(), P["moved"](A, O1)))
s.close()

# ------------------------------------------------------------------ 6
print("-- two roots holding the same ids")
env, P = layout([(A, O1), (B, O2)], live=(B, O2), classic=[(A, O1)])
s = Session(env, P, P["old"](A, O1), [has(P["classic"](A, O1))])
d = s.dialogs[0] if s.dialogs else {}
check("the picker offers both", len(d.get("buttons", [])) == 2, str(d.get("buttons")))
check("  and marks neither, since both match",
      not any(MARK in t for t in d.get("buttons", [])))
check("a pick saves its own path, not the shared pair",
      same(s.pref(), P["classic"](A, O1)), s.pref())
check("  plans into it", same(s.dest(), P["classic"](A, O1)), str(s.dest()))
check("  and does not loop back into the picker", len(s.dialogs) == 1,
      str(len(s.dialogs)))
s.close()

# ------------------------------------------------------------------ 7
print("-- negative controls: what stays as it was")
env, P = layout([(A, O1), (B, O2)], live=(B, O2))
s = Session(env, P, "")
check("no saved destination, one other store: plans straight to it",
      not s.dialogs and same(s.dest(), P["moved"](A, O1)))
check("  and shows no Change destination", not s.app.forget_btn.winfo_manager())
s.close()

env, P = layout([(A, O1), (A, O2), (B, O2)], live=(B, O2))
s = Session(env, P, "", ["cancel"])
d = s.dialogs[0] if s.dialogs else {}
check("no saved destination, two other stores: the picker as today",
      len(s.dialogs) == 1 and d.get("headline") == MULTI)
check("  no reason, no mark", GONE not in d.get("detail", "")
      and not any(MARK in t for t in d.get("buttons", [])))
check("  and the cancel leaves the file empty", s.pref() == "")
s.close()

env, P = layout([(A, O1), (B, O2)], live=(B, O2))
elsewhere = os.path.join(P["root"], "elsewhere", A, O1)
os.makedirs(elsewhere)
for name, saved in (("an existing folder no root holds", elsewhere),
                    ("an id fragment", "zzzz9999")):
    s = Session(env, P, saved)
    check("%s: refused, with the pointer" % name,
          not s.dialogs and s.app.status.get() == "Refused"
          and POINTER in s.app.detail.get() and SIGNED not in s.app.detail.get(),
          s.app.detail.get())
    check("  the file unchanged", s.pref() == saved)
    s.close()

s = Session(env, P, P["moved"](B, O2))
check("the signed-in store itself, still there: refused, named, no picker",
      not s.dialogs and SIGNED in s.app.detail.get()
      and "matched no other account store" in s.text(),
      s.app.detail.get())
s.close()

env, P = layout([(A, O1), (A, O2), (B, O2)], live=(B, O2), disagree=A)
s = Session(env, P, P["old"](A, O1))
check("an identity disagreement: the identity question as today",
      not s.dialogs and "cannot identify the signed-in account" in s.text())
check("  the set-aside reason kept, no signed-in claim",
      GONE in s.app.detail.get() and SIGNED not in s.app.detail.get(),
      s.app.detail.get())
check("  the file unchanged", s.pref() == P["old"](A, O1))
s.close()

env, P = layout([(A, O1), (A, O2), (B, O2)], live=(B, O2))
env.layout_errors = ["a store folder: Access is denied"]
s = Session(env, P, P["old"](A, O1))
check("a discovery error: shown as today, no picker, no claim",
      not s.dialogs and s.app.status.get() == "Something went wrong"
      and SIGNED not in s.app.detail.get(), s.app.status.get())
check("  the file unchanged", s.pref() == P["old"](A, O1))
s.close()

env, P = layout([(A, O1), (A, O2), (B, O2)], live=(B, O2))
s = Session(env, P, "aaaaaaaa", [has(P["moved"](A, O2))])
d = s.dialogs[0] if s.dialogs else {}
check("a saved value that went ambiguous: its picker as today",
      len(s.dialogs) == 1 and "no longer unique" in d.get("status", ""),
      d.get("status"))
check("  and a pick saves the full path", same(s.pref(), P["moved"](A, O2)), s.pref())
s.close()

for r in ROOTS:
    shutil.rmtree(r, ignore_errors=True)
shutil.rmtree(GUIDIR, ignore_errors=True)
print()
print("ALL PASS" if all(ok) else "SOME CHECKS FAILED")
sys.exit(0 if all(ok) else 1)
