"""The window's Env, rebuilt at each read and before each engine call, driven
against a real (withdrawn) Tk window on synthetic layouts that move while it
is open (docs/internals.md, "Amendment 2026-10-20 (Env rebuild)").

The window used to build its Env once, at open. Claude Desktop 2.42304.2
moved its user data from %LOCALAPPDATA%\\Packages\\Claude_<pkg>\\LocalCache\\
Roaming\\Claude\\ to %LOCALAPPDATA%\\Claude-Data\\claude_<pkg>\\, so a window
left open across that move kept refusing until a restart, and a move that
copied rather than moved would have left it writing into the old folder. The
maintainer's ruling of 2026-10-20:

1. every read builds a fresh Env, and every engine call in a press's worker
   runs on one built just before it;
2. a press whose store roots changed since its plan stops before the next
   engine call and replans, saying so;
3. the held identity answer is bound to the layout it was given under and
   set aside, with a sentence, when a clean fresh Env shows another;
4. a layout error is a state, not a change: the press stops and the replan
   shows discovery's error as a refusal, never as a launcher bug.

Real planning and real writes over synthetic stores. ccs.default_env is
replaced by a builder that lists the synthetic %LOCALAPPDATA% afresh on every
call, so a move made on disk while the window is open is seen exactly where
the window rebuilds. Inline "threads", every dialog answered by a script,
the preference file in a temp folder. Touches nothing real. Fake ids only.
"""
import json
import os
import shutil
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import claude_code_sessions as ccs  # noqa: E402

GUIDIR = tempfile.mkdtemp()
shutil.copy(os.path.join(REPO, "claude_code_sessions_gui.py"),
            os.path.join(GUIDIR, "ger.py"))
sys.path.insert(0, GUIDIR)
import ger as gp  # noqa: E402

import tkinter as tk  # noqa: E402

ok = []


def check(name, cond, extra=""):
    line = "%s %s%s" % ("OK " if cond else "BAD", name,
                        ("  " + str(extra)) if extra else "")
    print(line.encode("ascii", "backslashreplace").decode("ascii"))
    ok.append(bool(cond))


A, B = "a" * 32, "b" * 32
O1, O2 = "1" * 32, "2" * 32
PKG = "Claude_pkgx"
FOLDER = getattr(gp, "ENV_FOLDER_CHANGED", "The desktop app's data folder changed since "
                 "this plan, so it was planned again.")
ASIDE = getattr(gp, "ENV_ANSWER_SET_ASIDE", "The desktop app's data folder changed, so "
                "your answer about the signed-in account was set aside.")


class _Inline:
    """Run the worker inline: after() needs the interpreter's own thread
    when no mainloop runs (see check_gui_live_and_newer.py)."""

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._t, self._a, self._k = target, args, kwargs or {}

    def start(self):
        self._t(*self._a, **self._k)


class _FakeThreading:
    Thread = _Inline

    @staticmethod
    def current_thread():
        return None

    @staticmethod
    def main_thread():
        return None

    Event = None


gp.threading = _FakeThreading


class _Modals:
    """Every confirmation answered OK, after ON_OK runs (once) - the hook a
    scenario uses to move the data while a dialog is open."""

    def __init__(self):
        self.calls = []
        self.on_ok = None

    def askokcancel(self, title, message, **kw):
        self.calls.append(("askokcancel", title))
        hook, self.on_ok = self.on_ok, None
        if hook:
            hook()
        return True

    def showwarning(self, title, message, **kw):
        self.calls.append(("showwarning", title))

    def showerror(self, title, message, **kw):
        self.calls.append(("showerror", title))

    def showinfo(self, title, message, **kw):
        self.calls.append(("showinfo", title))

    def asked(self):
        return [c for c in self.calls if c[0] == "askokcancel"]


MODALS = _Modals()
real_messagebox = gp.messagebox
gp.messagebox = MODALS

ROOTS = []


class World:
    """A packaged layout from before the move: A (signed in, with ROWS
    session rows and their transcripts) and B, the other account, in
    LocalCache\\Roaming\\Claude\\claude-code-sessions. env() is the
    default_env stand-in: it lists the synthetic layout afresh each call."""

    def __init__(self, rows=1, disagree=None, b_row=False):
        root = tempfile.mkdtemp(prefix="env-rebuild-")
        ROOTS.append(root)
        self.root = root
        self.local = os.path.join(root, "Local")
        self.appdata = os.path.join(root, "Roaming")
        self.home = os.path.join(root, "home")
        self.old_claude = os.path.join(self.local, "Packages", PKG, "LocalCache",
                                       "Roaming", "Claude")
        self.moved_root = os.path.join(self.local, "Claude-Data", PKG.lower())
        self.ops = os.path.join(root, "journal", "ops")
        self.pref = os.path.join(root, "gui-pref.json")
        self.error = None
        self.raise_ = None
        os.makedirs(self.appdata)
        for a, o in ((A, O1), (B, O2)):
            os.makedirs(self.store(a, o))
        os.makedirs(os.path.join(self.home, ".claude", "projects", "C--p"))
        os.makedirs(self.ops)
        with open(os.path.join(self.home, ".claude.json"), "w", encoding="utf-8") as fh:
            json.dump({"oauthAccount": {"accountUuid": A, "organizationUuid": O1,
                                        "emailAddress": "alice@example.com"}}, fh)
        with open(os.path.join(self.old_claude, "config.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"lastKnownAccountUuid": disagree or A}, fh)
        sessions = [((A, O1), "local_%d" % i, "sid-%d" % i, "Session %d" % i)
                    for i in range(rows)]
        if b_row:
            # Converge writes only into accounts with rows of their own, so
            # the Level scenarios give B one session that A lacks.
            sessions.append(((B, O2), "local_b", "sid-b", "Session b"))
        for (a, o), name, sid, title in sessions:
            with open(os.path.join(self.store(a, o), name + ".json"), "w",
                      encoding="utf-8") as fh:
                json.dump({"sessionId": name, "cliSessionId": sid,
                           "cwd": "C:\\p", "title": title,
                           "lastActivityAt": 1}, fh)
            # Two prose turns with a timestamp and a model, so converge can
            # build a row from it (check_gui_level.py's shape).
            with open(os.path.join(self.home, ".claude", "projects", "C--p",
                                   sid + ".jsonl"), "w", encoding="utf-8") as fh:
                for e in ({"cwd": "C:\\p", "timestamp": "2026-08-01T00:00:00.000Z",
                           "type": "user",
                           "message": {"role": "user", "content": "prose " + sid}},
                          {"timestamp": "2026-08-01T00:10:00.000Z", "type": "assistant",
                           "message": {"role": "assistant", "model": "claude-opus-5",
                                       "content": [{"type": "text",
                                                    "text": "reply " + sid}]}}):
                    fh.write(json.dumps(e) + "\n")

    def env(self):
        if self.raise_ is not None:
            raise self.raise_
        cands, superseded, errors = ccs._windows_store_layout(self.local, self.appdata)
        if self.error:
            errors = list(errors) + [self.error]
        return ccs.Env(
            home=self.home,
            projects_root=os.path.join(self.home, ".claude", "projects"),
            store_candidates=cands,
            ops_dir=self.ops,
            moved_log=os.path.join(self.root, "journal", "moved-log.jsonl"),
            is_windows=True,
            process_lister=lambda: [],
            now=time.time,
            helper_roots=[],
            superseded_stores=superseded,
            layout_errors=errors,
            store_moves=[] if errors else ccs._windows_store_moves(self.local))

    def store(self, a, o, moved=False):
        base = self.moved_root if moved else self.old_claude
        return os.path.join(base, "claude-code-sessions", a, o)

    def move(self, copy=False):
        """The app's move: the data folder's contents to Claude-Data, the 0-byte
        marker left behind. COPY leaves the old store in place too."""
        os.makedirs(os.path.dirname(self.moved_root), exist_ok=True)
        if copy:
            shutil.copytree(self.old_claude, self.moved_root)
        else:
            shutil.move(self.old_claude, self.moved_root)
            os.makedirs(self.old_claude)
        open(os.path.join(self.old_claude, ".user-data-left." + PKG.lower()), "w").close()

    def rows(self, path):
        try:
            return sorted(n for n in os.listdir(path) if n.endswith(".json"))
        except FileNotFoundError:
            return []

    def ops_list(self):
        return sorted(os.listdir(self.ops))


def walk(w):
    out = []
    for c in w.winfo_children():
        out.append(c)
        out += walk(c)
    return out


def same(p, q):
    return bool(p) and bool(q) and os.path.normcase(os.path.realpath(p)) == \
        os.path.normcase(os.path.realpath(q))


def under(p, root):
    k, r = os.path.normcase(os.path.realpath(p or "")), os.path.normcase(
        os.path.realpath(root))
    return bool(p) and (k == r or k.startswith(r + os.sep))


class Session:
    """One window over one World, the One session tab selected unless TAB is
    "level"; pickers answered from ANSWERS (each "cancel", or a predicate
    choosing the candidate button by its text)."""

    def __init__(self, world, saved, tab="sync", answers=()):
        self.world = world
        self.answers = list(answers)
        self.dialogs = []
        gp.PREF_PATH = world.pref
        with open(world.pref, "w", encoding="utf-8") as fh:
            json.dump({"to": saved}, fh)
        gp.ccs.default_env = world.env
        MODALS.calls[:] = []
        MODALS.on_ok = None
        self.tkroot = tk.Tk()
        self.tkroot.withdraw()
        self.tkroot.wait_window = self._answer
        self.app = gp.SyncApp(self.tkroot)
        self.settle()
        if tab == "sync":
            self.app.nb.select(self.app.sync_tab)
            self.settle()

    def settle(self):
        for _ in range(30):
            self.tkroot.update()

    def _answer(self, win):
        buttons = [w for w in walk(win) if isinstance(w, gp.ttk.Button)
                   and w.cget("text") != "Cancel"]
        self.dialogs.append([str(b.cget("text")) for b in buttons])
        answer = self.answers.pop(0) if self.answers else "cancel"
        if answer != "cancel":
            hit = [b for b in buttons if answer(str(b.cget("text")))]
            if len(hit) == 1:
                hit[0].invoke()
                return
        self.app._open_dialog[1]()

    def text(self):
        return self.app.text.get("1.0", "end")

    def level_text(self):
        return self.app.level_text.get("1.0", "end")

    def dest(self):
        return (self.app.manifest or {}).get("dest_path")

    def banner_buttons(self, frame):
        return [b for b in walk(frame) if isinstance(b, gp.ttk.Button)]

    def close(self):
        self.tkroot.destroy()


# ------------------------------------------------------------------ 1
print("-- a copying move while the window is open, then Apply")
w = World()
s = Session(w, w.store(B, O2))
check("before the move the plan targets the old store", same(s.dest(), w.store(B, O2)),
      str(s.dest()))
w.move(copy=True)
MODALS.calls[:] = []
s.app.on_apply()
s.settle()
check("the press writes nothing into either store",
      w.rows(w.store(B, O2)) == [] and w.rows(w.store(B, O2, moved=True)) == [],
      repr((w.rows(w.store(B, O2)), w.rows(w.store(B, O2, moved=True)))))
check("  journals nothing", w.ops_list() == [], str(w.ops_list()))
check("  asks for no confirmation", not MODALS.asked(), str(MODALS.calls))
check("  says why", FOLDER in s.app.detail.get(), s.app.detail.get())
check("  and plans again, into the moved store",
      same(s.dest(), w.store(B, O2, moved=True)), str(s.dest()))
check("  every control released", s.app._busy_count == 0)
s.app.on_apply()
s.settle()
check("Apply on the new plan copies into the moved store",
      w.rows(w.store(B, O2, moved=True)) == ["local_0.json"]
      and w.rows(w.store(B, O2)) == [], s.app.status.get())
check("  the note was shown once", FOLDER not in s.app.detail.get(), s.app.detail.get())
s.close()

# ------------------------------------------------------------------ 2
print("-- a move while the confirmation is open")
w = World()
s = Session(w, w.store(B, O2))
MODALS.on_ok = lambda: w.move(copy=True)
s.app.on_apply()
s.settle()
check("the worker stops before the write: nothing in either store",
      w.rows(w.store(B, O2)) == [] and w.rows(w.store(B, O2, moved=True)) == [],
      repr((w.rows(w.store(B, O2)), w.rows(w.store(B, O2, moved=True)))))
check("  nothing journaled, so no red line", w.ops_list() == [] and not s.app.gate_text,
      str(w.ops_list()))
check("  no modal after the stop", not [c for c in MODALS.calls if c[0] != "askokcancel"],
      str(MODALS.calls))
check("  the note", FOLDER in s.app.detail.get(), s.app.detail.get())
check("  planned again into the moved store", same(s.dest(), w.store(B, O2, moved=True)),
      str(s.dest()))
check("  every control released", s.app._busy_count == 0)
s.close()

# ------------------------------------------------------------------ 3
print("-- a renaming move, as 2.42304.2 made it")
w = World()
s = Session(w, w.store(B, O2))
w.move(copy=False)
s.app.refresh()
s.settle()
check("the next Refresh plans into the moved store, not a refusal",
      same(s.dest(), w.store(B, O2, moved=True)) and s.app.status.get() != "Refused",
      s.app.status.get())
s.close()

# ------------------------------------------------------------------ 4
print("-- an empty package folder appearing")
w = World()
s = Session(w, w.store(B, O2))
os.makedirs(os.path.join(w.local, "Claude-Data", "claude_other"))
s.app.on_apply()
s.settle()
check("does not stop a press: the copy lands in the store that did not move",
      w.rows(w.store(B, O2)) == ["local_0.json"], s.app.status.get())
check("  and says nothing about a move", FOLDER not in s.app.detail.get())
s.close()

# ------------------------------------------------------------------ 5
print("-- a layout error at the press")
w = World()
s = Session(w, w.store(B, O2))
w.error = (os.path.join(w.local, "Claude-Data") +
           ": [WinError 5] Access is denied - cannot tell which packages' data moved")
s.app.on_apply()
s.settle()
check("nothing written, nothing journaled",
      w.rows(w.store(B, O2)) == [] and w.ops_list() == [], str(w.ops_list()))
check("  reads as a refusal, not a launcher bug",
      s.app.status.get() == "Refused" and "bug in the launcher" not in s.app.detail.get()
      and "Traceback" not in s.text(), s.app.status.get())
check("  with discovery's own error", "Access is denied" in s.text())
check("  and no folder-changed sentence", FOLDER not in s.app.detail.get())
check("  every control released", s.app._busy_count == 0)
w.error = None
s.app.refresh()
s.settle()
check("once the listing clears, the next read plans again",
      same(s.dest(), w.store(B, O2)) and s.app.status.get() != "Refused",
      s.app.status.get())
s.close()

# ------------------------------------------------------------------ 6
print("-- a rebuild that raises")
w = World()
s = Session(w, w.store(B, O2))
w.raise_ = OSError("simulated: the profile folder went away")
s.app.on_apply()
s.settle()
check("nothing written, nothing journaled",
      w.rows(w.store(B, O2)) == [] and w.ops_list() == [], str(w.ops_list()))
check("  reads as a refusal naming it",
      s.app.status.get() == "Refused" and "simulated" in s.text()
      and "Traceback" not in s.text(), s.app.status.get())
check("  every control released", s.app._busy_count == 0)
w.raise_ = None
s.close()

# ------------------------------------------------------------------ 7
print("-- Undo after a copying move")
w = World()
s = Session(w, w.store(B, O2))
s.app.on_apply()
s.settle()
check("setup: the copy landed in the old store", w.rows(w.store(B, O2)) == ["local_0.json"],
      s.app.status.get())
w.move(copy=True)
check("setup: the moved store holds it too",
      w.rows(w.store(B, O2, moved=True)) == ["local_0.json"])
s.app.on_undo()
s.settle()
check("Undo removes the row from the store the app reads",
      w.rows(w.store(B, O2, moved=True)) == [], s.app.status.get())
check("  and leaves the old copy, which the app does not read",
      w.rows(w.store(B, O2)) == ["local_0.json"])
check("  every control released", s.app._busy_count == 0)
s.close()

w = World()
s = Session(w, w.store(B, O2))
s.app.on_apply()
s.settle()
MODALS.on_ok = lambda: w.move(copy=True)
s.app.on_undo()
s.settle()
check("a move while Undo's confirmation is open: it acts on the moved store",
      w.rows(w.store(B, O2, moved=True)) == [] and w.rows(w.store(B, O2)) == ["local_0.json"],
      s.app.status.get())
s.close()

# ------------------------------------------------------------------ 8
print("-- the held identity answer across a move")
w = World(disagree=B)
s = Session(w, w.store(B, O2))
alice = [b for b in s.banner_buttons(s.app.sync_banner)
         if "alice@example.com" in str(b.cget("text"))]
check("setup: the identity question is asked, with a button for alice",
      len(alice) == 1, str([str(b.cget("text")) for b in s.banner_buttons(s.app.sync_banner)]))
if alice:
    alice[0].invoke()
    s.settle()
check("setup: the answer holds, under the old root",
      under(s.app.live_choice, os.path.join(w.old_claude, "claude-code-sessions")),
      s.app.live_choice)
w.move(copy=True)
s.app.refresh()
s.settle()
check("after the move the next read sets the answer aside", not s.app.live_choice,
      s.app.live_choice)
check("  and says so", ASIDE in s.app.detail.get(), s.app.detail.get())
alice = [b for b in s.banner_buttons(s.app.sync_banner)
         if "alice@example.com" in str(b.cget("text"))]
check("  the question is asked again", len(alice) == 1)
if alice:
    alice[0].invoke()
    s.settle()
check("  and answering it holds a store under the moved root",
      under(s.app.live_choice, os.path.join(w.moved_root, "claude-code-sessions")),
      s.app.live_choice)
s.close()

# ------------------------------------------------------------------ 9
print("-- Level: a move while stage 2's confirmation is open")


def level_rows(w):
    """Each account's row files, old store then moved, as one tuple."""
    return tuple(tuple(w.rows(w.store(a, o, moved=m)))
                 for a, o in ((A, O1), (B, O2)) for m in (False, True))


START = (("local_0.json",), (), ("local_b.json",), ())
MOVED = (("local_0.json",), ("local_0.json",), ("local_b.json",), ("local_b.json",))
w = World(b_row=True)
s = Session(w, w.store(B, O2), tab="level")
m = s.app.level_manifest or {}
check("setup: Level plans a row each way", len(m.get("rows") or []) == 2,
      s.app.level_status.get())
check("setup: the stores as built", level_rows(w) == START, str(level_rows(w)))
MODALS.on_ok = lambda: w.move(copy=True)
s.app.on_level_apply()
s.settle()
check("no row written into any store, old or moved", level_rows(w) == MOVED,
      str(level_rows(w)))
check("  nothing journaled", w.ops_list() == [], str(w.ops_list()))
check("  the status names the move", "data folder changed" in s.app.level_status.get(),
      s.app.level_status.get())
dests = [d.get("path") for d in (s.app.level_manifest or {}).get("destinations") or []]
check("  the pane was measured again under the moved store",
      bool(dests) and all(under(p, w.moved_root) for p in dests), str(dests))
check("  every control released", s.app._busy_count == 0)
s.close()

# ------------------------------------------------------------------ 10
print("-- Level: a press after a move")
w = World(b_row=True)
s = Session(w, w.store(B, O2), tab="level")
w.move(copy=True)
MODALS.calls[:] = []
s.app.on_level_apply()
s.settle()
check("writes nothing, journals nothing",
      level_rows(w) == MOVED and w.ops_list() == [], str((level_rows(w), w.ops_list())))
check("  asks for no confirmation", not MODALS.asked(), str(MODALS.calls))
check("  re-measures and says why", FOLDER in s.app.level_detail.get(),
      s.app.level_detail.get())
s.close()

gp.messagebox = real_messagebox
for r in ROOTS:
    shutil.rmtree(r, ignore_errors=True)
shutil.rmtree(GUIDIR, ignore_errors=True)
print()
print("ALL PASS" if all(ok) else "SOME CHECKS FAILED")
sys.exit(0 if all(ok) else 1)
