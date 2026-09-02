"""The window's layout, measured - every required control drawn and reachable.

Harness items 8, 15b and 15c of docs/specs/2026-09-01-gui-polish-design.md
(Change 0). Instantiates SyncApp for real over stubbed engine reads - a
three-account fake-cast store with an identity disagreement, a 13-row
converge plan, one suggested hold and one distinct hold with a 130-character
title, a 78-row sync plan, the desktop app reported running - and measures
widget geometry at the default 940x640, at the fit floor, at 871x432 (a
1366x768 panel at 150 % after window chrome) and at a simulated 683x384 work
area (the same panel at 200 %), where the computed minsize must not exceed
the work area.

The root is MAPPED, not withdrawn: measured 2026-09-01, a withdrawn root
never runs its geometry managers (every child stays 1x1 after update() or
update_idletasks()), so the audit's "withdrawn root" description of its own
probe cannot have been how the numbers were obtained. The window is mapped
fully transparent, as a tool window (no taskbar button), off-screen, and
update() is pumped - the layout is real and nothing is visible. Workers run
inline (the check_gui_live_and_newer.py pattern). Touches nothing real;
every title is the fake cast.
"""
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

import threading as _real_threading  # noqa: E402
import tkinter as tk  # noqa: E402

ok = []


def check(name, cond, extra=""):
    print("%s %s%s" % ("OK " if cond else "BAD", name,
                       ("  " + extra) if extra else ""))
    ok.append(bool(cond))


# ------------------------------------------------------------- the fake cast
ALICE, BOB, CAROL = "aaaa1111" + "0" * 24, "bbbb2222" + "0" * 24, "eeee5555" + "0" * 24
ORG_C, ORG_D = "cccc3333" + "0" * 24, "dddd4444" + "0" * 24
SID_A, SID_B = "12345678" + "0" * 24, "87654321" + "0" * 24
SID_D1, SID_D2 = "11111111" + "0" * 24, "22222222" + "0" * 24
T_COLL = "ACME-REVIEW session"
SUGGESTED = T_COLL + " - earlier leg (Aug 24-28)"
T_LONG = ("Northwind quarterly planning - drafting the ACME-REVIEW rollout "
          "memo and reconciling the three vendor spreadsheets before Friday")
T_LONG = (T_LONG + " " + "x" * 130)[:130]
assert len(T_LONG) == 130
LABELS = {ALICE: "alice@example.com (aaaa1111/cccc3333)",
          BOB: "bob@example.com (bbbb2222/dddd4444)",
          CAROL: "carol@example.com (eeee5555/dddd4444)"}
EMAILS = {ALICE: "alice@example.com", BOB: "bob@example.com",
          CAROL: "carol@example.com"}

root_dir = tempfile.mkdtemp(prefix="layouttest-")
home = os.path.join(root_dir, "home")
store = os.path.join(root_dir, "Claude", "claude-code-sessions")
STORES = [(ALICE, ORG_C, os.path.join(store, ALICE, ORG_C)),
          (BOB, ORG_D, os.path.join(store, BOB, ORG_D)),
          (CAROL, ORG_D, os.path.join(store, CAROL, ORG_D))]
for _a, _o, p in STORES:
    os.makedirs(p)
os.makedirs(os.path.join(home, ".claude", "projects"))
with open(os.path.join(home, ".claude.json"), "w", encoding="utf-8") as fh:
    fh.write('{"oauthAccount": {"accountUuid": "%s", "organizationUuid": '
             '"%s", "emailAddress": "alice@example.com"}}' % (ALICE, ORG_C))

REAL_DEFAULT_ENV = ccs.default_env


def fake_env():
    env = REAL_DEFAULT_ENV()
    env.home = home
    env.projects_root = os.path.join(home, ".claude", "projects")
    env.store_candidates = [store]
    env.ops_dir = os.path.join(root_dir, "journal", "ops")
    env.moved_log = os.path.join(root_dir, "journal", "moved-log.jsonl")
    env.process_lister = lambda: []
    return env


def alignment():
    return {
        "stores": {"status": "found", "detail": "", "roots": [store]},
        "accounts": [{"account": a, "label": LABELS[a], "rows": n}
                     for a, n in ((ALICE, 379), (BOB, 366), (CAROL, 379))],
        "row_errors": [],
        "reachable": {"transcripts": 379, "reachable": 379, "orphans": 0,
                      "orphan_ids": []},
        "distinguishable": {"duplicate_titles": 0, "per_account": {},
                            "titles": {}},
        "consistent": {"disagreeing_rows": 3, "leaving_a_gap": 0, "rows": []},
        "complete": {"conversations": 379, "in_all_accounts": 366,
                     "short": 13, "by_account_count": {}},
        "safe": {"dead_rows": 0, "blank_rows": 0, "unreadable_rows": 0},
        "exit_code": 1,
    }


def converge_manifest(live=""):
    rows = [{"name": "local_%02d.json" % i, "dest_path": STORES[1][2],
             "store_path": STORES[1][2], "account": BOB, "org": ORG_D,
             "label": LABELS[BOB], "session": "%08x" % i + "0" * 24,
             "title": "Northwind backtest %d" % i, "title_source": "auto",
             "holders": [], "pre_b64": None, "post_b64": "e30=",
             "is_update": False, "written": False} for i in range(13)]
    measured = {"classification": "supersession", "reason": None,
                "superseded": SID_A, "current": SID_B, "shared": 40,
                "a": SID_A, "a_total": 42, "b": SID_B, "b_total": 55,
                "suggested_title": SUGGESTED, "degrade_reason": None,
                "command_runnable": True}
    distinct = {"classification": "distinct", "reason": None,
                "superseded": None, "current": None, "shared": 2,
                "a": SID_D1, "a_total": 50, "b": SID_D2, "b_total": 61,
                "suggested_title": None, "degrade_reason": None,
                "command_runnable": False}
    holds = [
        {"session": SID_B, "account": BOB, "label": LABELS[BOB],
         "title": T_COLL, "reason": "held_title_collision",
         "detail": "already names a different conversation in that sidebar",
         "retitle": "", "measured": measured,
         "measured_line": "supersession: 12345678 (40 turns) is the earlier "
                          "leg of 87654321 (55 turns); last activity Aug 28 "
                          "vs Aug 30"},
        {"session": SID_D1, "account": BOB, "label": LABELS[BOB],
         "title": T_LONG, "reason": "held_title_collision",
         "detail": "already names a different conversation in that sidebar",
         "retitle": "", "measured": distinct,
         "measured_line": "largely distinct conversations - they share 2 of "
                          "50 and 2 of 61 prose turns; both need human names"},
    ]
    m = {"op_type": "converge", "live_asserted": live, "only": "",
         "only_session": "",
         "destinations": [{"account": BOB, "org": ORG_D, "path": STORES[1][2],
                           "label": LABELS[BOB], "rows": 366}],
         "non_destinations": [],
         "complete": {"now": 366, "of": 379, "after": 377, "held": 2,
                      "scoped": False},
         "dead_excluded": 0, "notes": [], "holds": holds, "rows": rows,
         "identity_disagreement": {"oauth": ALICE, "config": BOB}}
    return m


def sync_manifest(flags):
    rows = [{"name": "local_%02d.json" % i, "title": "Northwind backtest %d" % i,
             "is_update": False, "session_id": "%08x" % i + "0" * 24,
             "dup_conversation": i < 77, "dup_title": True,
             "pre_b64": None, "post_b64": "e30=", "written": False}
            for i in range(78)]
    return {"op_type": "sync", "source_account": ALICE, "source_org": ORG_C,
            "source_email": "alice@example.com", "source_path": STORES[0][2],
            "dest_account": BOB, "dest_org": ORG_D,
            "dest_email": "bob@example.com", "dest_email_source": "",
            "dest_path": STORES[1][2], "verbatim": False,
            "update": flags.update, "newer_only": flags.newer_only,
            "rows": rows,
            "tally": {"dup_conversation": [r["title"] for r in rows[:77]],
                      "dup_title": [r["title"] for r in rows]},
            "live_override": {"account": ALICE, "pair": [ALICE, BOB]}}


DESKTOP = [r"c:\program files\windowsapps\claude_2.1_x64\app\claude.exe"]

# --------------------------------------------------------- the engine, stubbed
gp.ccs.default_env = fake_env
gp.ccs.plan_sync = lambda env, flags: sync_manifest(flags)
gp.ccs.gather_alignment = lambda env: alignment()
gp.ccs.plan_converge = lambda env, flags: converge_manifest(flags.live)
gp.ccs.claude_running = lambda env: list(DESKTOP)
gp.ccs.nonterminal_ops = lambda env: []
gp.ccs.list_ops = lambda env: []
gp.ccs._account_dirs = lambda env: list(STORES)
gp.ccs._listing_row_count = lambda path: 286
gp.ccs._identity_disagreement = lambda env: (ALICE, BOB)
gp.ccs.dormant_account_email = lambda env, uuid: EMAILS.get(uuid, "")
gp.ccs.signed_helper_trust_enabled = lambda env: True
gp.load_pref = lambda: STORES[1][2]     # a saved destination, as the audit had
gp.save_pref = lambda _v: None


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


class Modals(object):
    def __init__(self):
        self.calls = []

    def askokcancel(self, title, message, **kw):
        self.calls.append(("askokcancel", title, message, kw))
        return False

    def showwarning(self, title, message, **kw):
        self.calls.append(("showwarning", title, message, kw))

    def showerror(self, title, message, **kw):
        self.calls.append(("showerror", title, message, kw))


gp.messagebox = Modals()

WORK = {"area": (1536, 912)}     # the simulated display's work area
if hasattr(gp, "_work_area"):
    gp._work_area = lambda root: WORK["area"]


def invisible_root():
    """A MAPPED root nobody can see: transparent, a tool window (no taskbar
    button), placed off-screen. Only a mapped root lays out its children."""
    r = tk.Tk()
    r.attributes("-alpha", 0.0)
    try:
        r.attributes("-toolwindow", True)
    except tk.TclError:
        pass
    r.geometry("+-4000+-4000")
    return r


def open_app():
    tkroot = invisible_root()
    app = gp.SyncApp(tkroot)

    def settle():
        for _ in range(30):
            tkroot.update()
    settle()
    return app, tkroot, settle


def resize(tkroot, settle, w, h):
    tkroot.geometry("%dx%d" % (w, h))
    settle()


def geom(w):
    return (w.winfo_x(), w.winfo_width(), w.winfo_reqwidth(),
            w.winfo_height(), w.winfo_reqheight())


app, tkroot, settle = open_app()
app.nb.select(app.sync_tab)
settle()

# --------------------------------------------- the audit's numbers, reproduced
check("the window opens at 940x640",
      (tkroot.winfo_width(), tkroot.winfo_height()) == (940, 640),
      "%dx%d" % (tkroot.winfo_width(), tkroot.winfo_height()))
check("tab inner width 916", app.sync_tab.winfo_width() == 916,
      str(app.sync_tab.winfo_width()))
filt = app.only_entry.master
check("filter frame 896", filt.winfo_width() == 896, str(filt.winfo_width()))
controls = [w for w in filt.winfo_children()]
xs = [w.winfo_x() for w in controls]
widths = [w.winfo_width() for w in controls]
reqs = [w.winfo_reqwidth() for w in controls]
print("   filter controls x =", xs)
print("   allocated        =", widths)
print("   requested        =", reqs)
check("the filter row packs at x = 0, 192, 408, 488, 580, 770",
      xs[:6] == [0, 192, 408, 488, 580, 770], str(xs))
# The audit's table folded the label's 6 px padx into its width (192); the
# label itself requests 186 and the Entry starts at 192.
check("  requesting 186, 210, 76, 76, 184, 157, 168",
      reqs == [186, 210, 76, 76, 184, 157, 168], str(reqs))
check("  the sixth ('only where mine is newer') is clipped to 126 of 157",
      widths[5] == 126, str(widths[5]))
check("  the seventh ('allow hiding a conversation') is allocated 1 px - "
      "not drawn", widths[6] == 1, str(widths[6]))
check("  the row requests 1101 px in a 896 px frame",
      filt.winfo_reqwidth() == 1101, str(filt.winfo_reqwidth()))
check("the sync bar requests 416 with four controls",
      app.sync_bar.winfo_reqwidth() == 416
      and len(app.sync_bar.pack_slaves()) == 4,
      "%d, %d controls" % (app.sync_bar.winfo_reqwidth(),
                           len(app.sync_bar.pack_slaves())))
check("the guidance label wraps at 880 and the warning at 880",
      app.sync_guidance.cget("wraplength") == 880
      and app.sync_warning.cget("wraplength") == 880)

app.nb.select(app.level_tab)
settle()
shown = app.level_text.get("1.0", "end-1c").split("\n")
print("   scoreboard lines:", len(shown), "text box h:",
      app.level_text.winfo_height(), "body h:", app.level_body.winfo_height())
check("the Level bar requests 299", app.level_bar.winfo_reqwidth() == 299,
      str(app.level_bar.winfo_reqwidth()))
check("the scoreboard box is 13 lines and 186 px tall",
      len(shown) == 13 and app.level_text.winfo_height() == 186,
      "%d lines, %d px" % (len(shown), app.level_text.winfo_height()))
check("the holds canvas gets 185 px", app.hold_canvas.winfo_height() == 185,
      str(app.hold_canvas.winfo_height()))
# Row height depends on the fixture's own text (the audit measured 163 for
# its two rows); what the design cares about is the budget: both rows fit
# at the default size and a third would scroll.
check("  and two rows fit inside it (the audit measured 163 for its two)",
      app.hold_frame.winfo_reqheight() <= app.hold_canvas.winfo_height(),
      "rows request %d" % app.hold_frame.winfo_reqheight())
check("the identity banner requests 866 with two buttons",
      app.level_banner.winfo_reqwidth() == 866
      and len(app._banner_widgets) == 2,
      "%d, %d buttons" % (app.level_banner.winfo_reqwidth(),
                          len(app._banner_widgets)))
bw = sorted(b.winfo_reqwidth() for b in app._banner_widgets)
check("  of 406 and 411 px", bw == [406, 411], str(bw))
check("the running-app notice is packed", bool(app.level_notice.winfo_manager()))

# ------------------------------------------------------------ the 760x520 floor
resize(tkroot, settle, 760, 520)
check("at 760x520 the tab inner is 736", app.level_tab.winfo_width() == 736,
      str(app.level_tab.winfo_width()))
check("  the holds canvas gets 65 px - less than one row",
      app.hold_canvas.winfo_height() == 65, str(app.hold_canvas.winfo_height()))
rows = [w for w in app.hold_frame.winfo_children()
        if isinstance(w, gp.ttk.Frame)]
long_row = [r for r in rows if any(
    isinstance(c, gp.ttk.Label) and str(c.cget("text")) == T_LONG
    for c in r.winfo_children())]
# The overflow's exact size is the title text's pixel width (the audit's
# own 130-character title overflowed by 121); the defect is the overflow.
check("  the 130-character title overflows its row (the audit measured 121 px)",
      long_row and long_row[0].winfo_reqwidth() - long_row[0].winfo_width()
      > 100,
      "%s" % ([(r.winfo_reqwidth(), r.winfo_width()) for r in long_row]))
app.nb.select(app.sync_tab)
settle()
check("  the guidance label requests 855 in a 736 px tab",
      app.sync_guidance.winfo_reqwidth() == 855,
      str(app.sync_guidance.winfo_reqwidth()))
check("  the warning label requests 868",
      app.sync_warning.winfo_reqwidth() == 868,
      str(app.sync_warning.winfo_reqwidth()))
check("  'Also refresh rows already there' is clipped to 136 of 184",
      controls[4].winfo_width() == 136, str(controls[4].winfo_width()))

# ----------------------------------------------------------------- 1200x800
resize(tkroot, settle, 1200, 800)
app.nb.select(app.level_tab)
settle()
check("at 1200x800 the holds canvas gets 310",
      app.hold_canvas.winfo_height() == 310, str(app.hold_canvas.winfo_height()))
app.nb.select(app.sync_tab)
settle()
check("  and every filter control is drawn",
      all(w.winfo_width() > 1 for w in controls),
      str([w.winfo_width() for w in controls]))

tkroot.destroy()
shutil.rmtree(root_dir, ignore_errors=True)
shutil.rmtree(GUIDIR, ignore_errors=True)
print()
print("ALL PASS" if all(ok) else "SOME CHECKS FAILED")
sys.exit(0 if all(ok) else 1)
