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

The re-root ruling of the same day (docs/internals.md, "Amendment 2026-10-20
(re-root)"): a saved value proven gone is first re-rooted through the
journal's package pairs and planned into when discovery lists that store -
the same destination, moved by the app - and the preference file changes on
a pick, or after a successful Apply into a re-rooted saved destination, to
that same store's new path. Set-aside and ask remain for a value no pair
re-roots, so those scenarios save a path under a package with no pair.

Real planning (plan_sync over the synthetic stores), a real Apply where one
is pressed, inline "threads", the preference file in a temp folder, and every
dialog answered by a script. Touches nothing real. Fake ids only.
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
MOVED = "moved with the desktop app's data folder"
SAVE_ON_APPLY = "Applying saves its new path"
SAVE_BY_PICK = "pick it under Change destination to save its new path"
SAVED = "saved at its new path"
NOT_SAVED = "could not be saved"
ELSEWHERE = "chosen in another window"
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


def layout(stores, live, classic=(), disagree=None, rows=0):
    """A moved layout: STORES under the moved root, CLASSIC under
    %APPDATA%\\Claude, the old package folder holding only the app's marker,
    and both identity files naming LIVE (or config.json naming DISAGREE).
    ROWS session rows in LIVE's store, each with its transcript, so a plan
    has something to copy. The Env's pairs come from this layout, never
    from the machine's own %LOCALAPPDATA%."""
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
    for i in range(rows):
        sid = "sid-%d" % i
        with open(os.path.join(moved_store, live[0], live[1], "local_%d.json" % i),
                  "w", encoding="utf-8") as fh:
            json.dump({"sessionId": "local_%d" % i, "cliSessionId": sid,
                       "cwd": "C:\\p", "title": "Session %d" % i,
                       "lastActivityAt": 1}, fh)
        folder = os.path.join(home, ".claude", "projects", "C--p")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, sid + ".jsonl"), "w", encoding="utf-8") as fh:
            fh.write('{"cwd":"C:\\\\p"}\n')
    cands, superseded, errors = ccs._windows_store_layout(local, appdata)
    env = ccs.default_env()
    env.home = home
    env.projects_root = os.path.join(home, ".claude", "projects")
    env.store_candidates = cands
    env.superseded_stores = superseded
    env.layout_errors = errors
    env.store_moves = [] if errors else ccs._windows_store_moves(local)
    env.ops_dir = os.path.join(root, "journal", "ops")
    env.moved_log = os.path.join(root, "journal", "moved-log.jsonl")
    env.process_lister = lambda: []
    env.helper_roots = []
    paths = {
        "moved": lambda a, o: os.path.join(moved_store, a, o),
        "old": lambda a, o: os.path.join(old_claude, "claude-code-sessions", a, o),
        # A package this layout's moved root does not pair with - an app
        # reinstalled under another package id, say: proven gone, and no
        # pair re-roots it, so the window sets it aside and asks.
        "unpaired": lambda a, o: os.path.join(
            local, "Packages", "Claude_pkgy", "LocalCache", "Roaming", "Claude",
            "claude-code-sessions", a, o),
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
print("-- a saved destination no pair re-roots, two other stores of one account")
env, P = layout([(A, O1), (A, O2), (B, O2)], live=(B, O2))
s = Session(env, P, P["unpaired"](A, O1), [has(P["moved"](A, O1))])
d = s.dialogs[0] if s.dialogs else {}
check("the window asks by itself: one picker", len(s.dialogs) == 1, str(len(s.dialogs)))
check("  headed as today", d.get("headline") == MULTI, d.get("headline"))
check("  with the reason on the detail line", GONE in d.get("detail", ""), d.get("detail"))
check("  and in the picker", any(GONE in t for t in d.get("labels", [])))
check("  no plan installed under it", d.get("manifest") is None)
check("  the file unchanged until the pick", d.get("pref") == P["unpaired"](A, O1))
check("  the store with the saved ids is marked",
      len(marked(d, P["moved"](A, O1))) == 1)
check("  and only it", not marked(d, P["moved"](A, O2)))
check("the pick saves that candidate's own path", same(s.pref(), P["moved"](A, O1)),
      s.pref())
check("  and plans into it", same(s.dest(), P["moved"](A, O1)), str(s.dest()))
check("  and shows Change destination", bool(s.app.forget_btn.winfo_manager()))
s.close()

print("-- the same, two other stores of two accounts")
env, P = layout([(A, O1), (C, O3), (B, O2)], live=(B, O2))
s = Session(env, P, P["unpaired"](A, O1), ["cancel"])
d = s.dialogs[0] if s.dialogs else {}
check("one picker, the saved account's store marked",
      len(s.dialogs) == 1 and len(marked(d, P["moved"](A, O1))) == 1)
check("  the other account's not", not marked(d, P["moved"](C, O3)))
s.close()

# ------------------------------------------------------------------ 2
print("-- a cancel after a set-aside")
env, P = layout([(A, O1), (A, O2), (B, O2)], live=(B, O2))
s = Session(env, P, P["unpaired"](A, O1), ["cancel", "cancel"])
check("the file is unchanged", s.pref() == P["unpaired"](A, O1), s.pref())
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
check("  and the file is still unchanged", s.pref() == P["unpaired"](A, O1))
s.close()

# ------------------------------------------------------------------ 3
print("-- one other store, a saved destination no pair re-roots")
env, P = layout([(A, O1), (B, O2)], live=(B, O2))
s = Session(env, P, P["unpaired"](A, O1), ["cancel"])
d = s.dialogs[0] if s.dialogs else {}
check("a one-candidate picker opens", len(s.dialogs) == 1 and len(d.get("buttons", [])) == 1,
      str(d.get("buttons")))
check("  under its own headline", d.get("headline") == SINGLE, d.get("headline"))
check("  without the all-empty advice",
      not any(ALL_EMPTY in t for t in d.get("labels", [])))
check("  marked, by the saved ids",
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

print("-- one other store, of another account than the saved one, which did not move")
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
    check("  at its new path", gp.short(P["moved"](A, O1), env.home)
          in s.app.detail.get(), s.app.detail.get())
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
s = Session(env, P, P["unpaired"](A, O1), [has(P["classic"](A, O1))])
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
s = Session(env, P, P["unpaired"](A, O1))
check("an identity disagreement: the identity question as today",
      not s.dialogs and "cannot identify the signed-in account" in s.text())
check("  the set-aside reason kept, no signed-in claim",
      GONE in s.app.detail.get() and SIGNED not in s.app.detail.get(),
      s.app.detail.get())
check("  the file unchanged", s.pref() == P["unpaired"](A, O1))
s.close()

s = Session(env, P, P["old"](A, O1))
check("an identity disagreement over a re-rootable value: the question as today",
      not s.dialogs and "cannot identify the signed-in account" in s.text())
check("  with the location sentence, no signed-in claim, no plan claimed",
      MOVED in s.app.detail.get() and SIGNED not in s.app.detail.get()
      and SAVE_ON_APPLY not in s.app.detail.get(), s.app.detail.get())
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

# ------------------------------------------------------------------ 8
# The 2026-10-20 separator ruling: the core folds '/' and '\' and lets a
# trailing separator match a store path's end. This check runs on Windows
# (default_env takes the platform), where '/' is the spelling that differs;
# the POSIX coverage is tests/test_sync.py's.
print("-- a saved path spelled with '/'")


def slashed(p):
    return p.replace("\\", "/")


env, P = layout([(A, O1), (A, O2), (B, O2)], live=(B, O2))
for name, saved in (("a '/'-spelled saved store", slashed(P["moved"](A, O1))),
                    ("  with a trailing '/'", slashed(P["moved"](A, O1)) + "/")):
    s = Session(env, P, saved)
    check("%s: plans into it, no picker" % name,
          not s.dialogs and same(s.dest(), P["moved"](A, O1)), str(s.dest()))
    check("  no pointer", POINTER not in s.app.detail.get(), s.app.detail.get())
    check("  the file unchanged", s.pref() == saved, s.pref())
    s.close()

acct = slashed(os.path.dirname(P["moved"](A, O1)))
s = Session(env, P, acct, ["cancel"])
d = s.dialogs[0] if s.dialogs else {}
check("a '/'-spelled account folder naming two stores: the no-longer-unique picker",
      len(s.dialogs) == 1 and "no longer unique" in d.get("status", ""),
      d.get("status"))
check("  the file unchanged", s.pref() == acct, s.pref())
s.close()

env, P = layout([(A, O1), (B, O2)], live=(B, O2))
elsewhere = os.path.join(P["root"], "elsewhere", A, O1)
os.makedirs(elsewhere)
s = Session(env, P, slashed(elsewhere))
check("a '/'-spelled existing folder no root holds: refused, with the pointer",
      not s.dialogs and s.app.status.get() == "Refused"
      and POINTER in s.app.detail.get() and SIGNED not in s.app.detail.get(),
      s.app.detail.get())
check("  the file unchanged", s.pref() == slashed(elsewhere))
s.close()

s = Session(env, P, slashed(P["moved"](B, O2)))
check("a '/'-spelled signed-in store: refused, named, no picker",
      not s.dialogs and SIGNED in s.app.detail.get()
      and "matched no other account store" in s.text(),
      s.app.detail.get())
s.close()

# ------------------------------------------------------------------ 9
print("-- re-rooting: a destination saved before the move, its store moved whole")


class _Modals:
    """Apply's confirmation answered OK; every call recorded."""

    def __init__(self):
        self.calls = []

    def askokcancel(self, title, message, **kw):
        self.calls.append(("askokcancel", title))
        return True

    def showwarning(self, title, message, **kw):
        self.calls.append(("showwarning", title))

    def showerror(self, title, message, **kw):
        self.calls.append(("showerror", title))


real_messagebox = gp.messagebox
gp.messagebox = _Modals()


def apply_state(s):
    return str(s.app.apply_btn.cget("state"))


env, P = layout([(A, O1), (A, O2), (B, O2)], live=(B, O2), rows=1)
s = Session(env, P, P["old"](A, O1))
check("no picker: the saved store is planned into at its new place",
      not s.dialogs and same(s.dest(), P["moved"](A, O1)), str(s.dest()))
check("  Apply is available", apply_state(s) == "normal", apply_state(s))
check("  the location and plan sentences on the detail line",
      MOVED in s.app.detail.get() and SAVE_ON_APPLY in s.app.detail.get(),
      s.app.detail.get())
check("  the file unchanged before Apply", s.pref() == P["old"](A, O1))
check("  Change destination is shown", bool(s.app.forget_btn.winfo_manager()))
s.app.on_apply()
s.settle()
check("a successful Apply copies into the moved store",
      s.app.status.get().startswith("Copied 1")
      and os.path.isfile(os.path.join(P["moved"](A, O1), "local_0.json")),
      s.app.status.get())
check("  saves the new path", same(s.pref(), P["moved"](A, O1)), s.pref())
check("  and says so", SAVED in s.app.detail.get(), s.app.detail.get())
check("  the window's destination follows the save",
      same(s.app.dest_choice, P["moved"](A, O1)), s.app.dest_choice)
s.app.refresh()
s.settle()
check("the next plan needs no re-root, and shows no notice",
      MOVED not in s.app.detail.get() and same(s.dest(), P["moved"](A, O1)),
      s.app.detail.get())
s.close()

env, P = layout([(A, O1), (A, O2), (B, O2)], live=(B, O2), rows=1)
s = Session(env, P, P["old"](A, O1))
real_run_sync = gp.ccs.run_sync


def _refuse(_env, _manifest):
    raise ccs.Refusal("refused for this check")


gp.ccs.run_sync = _refuse
try:
    s.app.on_apply()
    s.settle()
finally:
    gp.ccs.run_sync = real_run_sync
check("a refused Apply saves nothing", s.pref() == P["old"](A, O1), s.pref())
check("  and the window keeps the saved value", s.app.dest_choice == P["old"](A, O1))
s.close()

env, P = layout([(A, O1), (A, O2), (B, O2)], live=(B, O2), rows=1)
s = Session(env, P, P["old"](A, O1))
real_save_pref = gp.save_pref
gp.save_pref = lambda _v: False
try:
    s.app.on_apply()
    s.settle()
finally:
    gp.save_pref = real_save_pref
check("a preference file that cannot be written: reported, not claimed",
      NOT_SAVED in s.app.detail.get() and SAVED not in s.app.detail.get(),
      s.app.detail.get())
check("  the file and the window keep the saved value",
      s.pref() == P["old"](A, O1) and s.app.dest_choice == P["old"](A, O1))
s.app.refresh()
s.settle()
check("  the next plan re-roots again", MOVED in s.app.detail.get()
      and same(s.dest(), P["moved"](A, O1)), s.app.detail.get())
check("  nothing left to copy: the pick sentence, Apply off",
      SAVE_BY_PICK in s.app.detail.get() and SAVE_ON_APPLY not in s.app.detail.get()
      and apply_state(s) == "disabled", s.app.detail.get())
s.close()

env, P = layout([(A, O1), (A, O2), (B, O2)], live=(B, O2), rows=1)
s = Session(env, P, P["old"](A, O1))
with open(P["pref"], "w", encoding="utf-8") as fh:
    json.dump({"to": P["moved"](A, O2)}, fh)             # another window's pick
s.app.on_apply()
s.settle()
check("a pick made meanwhile in another window is left as it is",
      s.pref() == P["moved"](A, O2), s.pref())
check("  adopted by this window", s.app.dest_choice == P["moved"](A, O2))
check("  and named, not reported as a failed write",
      ELSEWHERE in s.app.detail.get() and NOT_SAVED not in s.app.detail.get(),
      s.app.detail.get())
s.app.refresh()
s.settle()
check("  the next plan goes to it", same(s.dest(), P["moved"](A, O2)), str(s.dest()))
s.close()

env, P = layout([(A, O1), (A, O2), (B, O2)], live=(B, O2))
s = Session(env, P, P["old"](A, O1))
check("a level re-rooted store: planned into, Apply off",
      not s.dialogs and same(s.dest(), P["moved"](A, O1)) and apply_state(s) == "disabled")
check("  the pick sentence, never the Apply one",
      MOVED in s.app.detail.get() and SAVE_BY_PICK in s.app.detail.get()
      and SAVE_ON_APPLY not in s.app.detail.get(), s.app.detail.get())
check("  the file unchanged", s.pref() == P["old"](A, O1))
s.close()

env, P = layout([(A, O1), (B, O2)], live=(B, O2), rows=1)
s = Session(env, P, P["old"](A, O1))
check("one other store: the same destination, re-rooted - no picker",
      not s.dialogs and same(s.dest(), P["moved"](A, O1)), str(s.dest()))
s.close()

env, P = layout([(A, O1), (A, O2), (B, O2)], live=(B, O2), disagree=A, rows=1)
s = Session(env, P, P["old"](A, O1))
s.app._apply_live("answered", {"path": P["moved"](A, O1),
                               "pair": s.app._read_pair(), "label": ""})
s.app.refresh()
s.settle()
banner = [str(w.cget("text")) for w in walk(s.app.sync_banner)
          if isinstance(w, gp.ttk.Button)]
check("a held answer naming the saved account: named as signed in, no picker",
      not s.dialogs and SIGNED in s.app.detail.get(), s.app.detail.get())
check("  at its new path", gp.short(P["moved"](A, O1), env.home)
      in s.app.detail.get(), s.app.detail.get())
check("  nothing installed, the file unchanged",
      s.app.manifest is None and s.pref() == P["old"](A, O1))
check("  the banner shows the answer in force, not the question",
      banner == ["Change signed-in account"], str(banner))
s.close()

env, P = layout([(A, O1), (A, O2), (B, O2)], live=(B, O2), rows=1)
s = Session(env, P, P["old"](A, O1), ["cancel"])
s.app.forget_btn.invoke()
s.settle()
d = s.dialogs[0] if s.dialogs else {}
check("Change destination over a re-rooted value opens the picker",
      len(s.dialogs) == 1 and d.get("headline") == MULTI, d.get("headline"))
check("  the re-rooted store marked", len(marked(d, P["moved"](A, O1))) == 1)
check("  the location sentence its reason, and no plan claimed",
      any(MOVED in t for t in d.get("labels", []))
      and not any(SAVE_ON_APPLY in t for t in d.get("labels", []))
      and SAVE_ON_APPLY not in d.get("detail", ""), str(d.get("labels")))
check("a cancel brings the re-rooted plan back, no second picker",
      same(s.dest(), P["moved"](A, O1)) and len(s.dialogs) == 1, str(s.dest()))
check("  the file unchanged", s.pref() == P["old"](A, O1))
s.close()

gp.messagebox = real_messagebox

for r in ROOTS:
    shutil.rmtree(r, ignore_errors=True)
shutil.rmtree(GUIDIR, ignore_errors=True)
print()
print("ALL PASS" if all(ok) else "SOME CHECKS FAILED")
sys.exit(0 if all(ok) else 1)
