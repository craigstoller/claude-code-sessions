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
REPORT = {"rep": alignment()}      # switchable for the sash items
gp.ccs.gather_alignment = lambda env: REPORT["rep"]
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

# ----------------------- the audit's constants that still hold (Change 0)
# The defect the audit measured - the filter row requesting 1101 px in this
# 896 px frame, its seventh control allocated 1 px - is reproduced by this
# harness at commit 9bb0651; Change 1 removed it, and the pins below are the
# fixed state.
app.nb.select(app.sync_tab)
settle()
check("the window opens at 940x640",
      (tkroot.winfo_width(), tkroot.winfo_height()) == (940, 640),
      "%dx%d" % (tkroot.winfo_width(), tkroot.winfo_height()))
check("tab inner width 916", app.sync_tab.winfo_width() == 916,
      str(app.sync_tab.winfo_width()))
check("filter frame 896", app.only_entry.master.winfo_width() == 896,
      str(app.only_entry.master.winfo_width()))
check("the identity banner offers two buttons", len(app._banner_widgets) == 2,
      str(len(app._banner_widgets)))
check("the running-app notice is packed on Level",
      bool(app.level_notice.winfo_manager()))


def rect(w):
    return (w.winfo_rootx(), w.winfo_rooty(),
            w.winfo_rootx() + w.winfo_width(),
            w.winfo_rooty() + w.winfo_height())


def inside(w, area):
    """W lies within every ancestor up to AREA - a widget can sit inside
    the tab's rectangle while its own parent frame has been squeezed to
    nothing around it."""
    a = rect(w)
    p = w
    while p is not None and p is not area:
        p = p.master
        if p is None:
            return True
        b = rect(p)
        if not (a[0] >= b[0] and a[1] >= b[1] and a[2] <= b[2]
                and a[3] <= b[3]):
            return False
    return True


def drawn(w):
    """Managed, mapped, and allocated more than the 1x1 an unplaced widget
    reports. winfo_ismapped is meaningful here because the root is mapped;
    an unmapped widget keeps the stale geometry of its last placement, which
    is why the size test alone is not enough."""
    return (bool(w.winfo_manager()) and w.winfo_ismapped()
            and w.winfo_width() > 1 and w.winfo_height() > 1)


# Every required control, by attribute name, with the area it must lie in.
# Optional widgets (the saved-destination button, the identity buttons) are
# listed where the fixture packs them.
def required(app, identity=True):
    st, lt, ht = app.sync_tab, app.level_tab, app.health_tab
    items = [("filter_label", st), ("only_entry", st), ("filter_btn", st),
             ("clear_btn", st), ("refresh_label", st), ("update_chk", st),
             ("newer_chk", st), ("orphan_chk", st), ("consent_chk", st),
             ("sync_bar", st), ("apply_btn", st), ("refresh_btn", st),
             ("forget_btn", st), ("sync_status_label", st), ("sync_role", st),
             ("level_bar", lt), ("level_apply_btn", lt),
             ("level_refresh_btn", lt), ("level_status_label", lt),
             ("level_role", lt),
             ("health_bar", ht), ("doctor_btn", ht),
             ("health_status_label", ht), ("health_role", ht),
             # The Chrome-helper checkbox lives on the sync bar until
             # Change 5 moves it to the window bar.
             ("trust_chk", st),
             ("close_btn", app.root)]
    out = [(name, getattr(app, name), area) for name, area in items]
    if identity:
        out += [("identity button %d" % i, b, lt)
                for i, b in enumerate(app._banner_widgets)]
    return out


def audit(app, tkroot, settle, size, names=None, identity=True):
    w, h = size
    tkroot.geometry("%dx%d" % (w, h))
    settle()
    label = "%dx%d" % (tkroot.winfo_width(), tkroot.winfo_height())
    check("the window is %dx%d" % size, label == "%dx%d" % size, label)
    bad = []
    for name, widget, area in required(app, identity):
        if names is not None and name not in names:
            continue
        tab = area if area is not app.root else None
        if tab is not None:
            app.nb.select(tab)
            settle()
        if not drawn(widget):
            bad.append("%s not drawn (%s, %dx%d)" % (
                name, widget.winfo_manager() or "unmanaged",
                widget.winfo_width(), widget.winfo_height()))
        elif not inside(widget, area):
            bad.append("%s outside its area %s vs %s" % (
                name, rect(widget), rect(area)))
    check("  every required control is drawn inside its tab at %s" % label,
          not bad, "; ".join(bad))
    wide = []
    for lbl, container, _m in app._wrapped:
        if not lbl.winfo_manager() or container.winfo_width() <= 1:
            continue                     # not placed at this size at all
        if lbl.winfo_reqwidth() > container.winfo_width():
            wide.append("%s %d > %d" % (str(lbl.cget("text"))[:30]
                                          or lbl.cget("textvariable"),
                                          lbl.winfo_reqwidth(),
                                          container.winfo_width()))
    check("  every wrapping label's requested width is within its container "
          "at %s" % label, not wide, "; ".join(wide))


audit(app, tkroot, settle, (940, 640))
app.nb.select(app.sync_tab)
settle()
filt = app.only_entry.master
group = app.update_chk.master
check("the filter block is two rows: the filter row and the refresh group",
      filt is not group and app.newer_chk.master is group
      and app.orphan_chk.master is group,
      "%s / %s" % (filt, group))
check("  the group is laid out with grid",
      app.update_chk.winfo_manager() == "grid"
      and app.orphan_chk.winfo_manager() == "grid")
check("  and labelled as the opt-in for this run",
      "Refresh (opt-in for this run):" == str(app.refresh_label.cget("text")),
      str(app.refresh_label.cget("text")))
check("  the newer-only advice sits inside the group",
      app.refresh_hint.master is group
      and "only where mine is newer" in str(app.refresh_hint.cget("text")))
check("  the consent box sits in the action bar beside the button it gates",
      app.consent_chk.master is app.sync_bar
      and app.consent_chk.winfo_rootx() < app.refresh_btn.winfo_rootx())
check("  the seventh control - 'allow hiding a conversation' - is drawn",
      drawn(app.orphan_chk),
      "%dx%d" % (app.orphan_chk.winfo_width(), app.orphan_chk.winfo_height()))
check("  and 'only where mine is newer' is allocated its full request",
      app.newer_chk.winfo_width() == app.newer_chk.winfo_reqwidth(),
      "%d of %d" % (app.newer_chk.winfo_width(), app.newer_chk.winfo_reqwidth()))
check("no label carries the 880 px wraplength constant",
      not any(int(lbl.cget("wraplength")) == 880 for lbl, _c, _m in app._wrapped)
      and len(app._wrapped) >= 8, str(len(app._wrapped)))

# The bars are bottom-pinned on every tab: packed first against the bottom
# edge, before the region that scrolls.
for tab, bar, body in ((app.sync_tab, app.sync_bar, app.text.master),
                       (app.health_tab, app.health_bar, app.health_text.master),
                       (app.level_tab, app.level_bar, app.level_pane)):
    slaves = tab.pack_slaves()
    check("the %s bar is bottom-pinned before its scrolling middle" % tab.winfo_name(),
          bar.pack_info()["side"] == "bottom"
          and slaves.index(bar) < slaves.index(body)
          and body.pack_info()["expand"] in (1, True, "1"),
          str([str(w) for w in slaves]))
app.nb.select(app.sync_tab)
settle()
check("the duplicate-title warning sits above the bar, bottom-pinned too",
      bool(app.sync_warning.winfo_manager())
      and app.sync_warning.pack_info()["side"] == "bottom"
      and app.sync_warning.winfo_rooty() < app.sync_bar.winfo_rooty()
      and app.sync_warning.winfo_rooty() > app.text.winfo_rooty(),
      "warning y=%d bar y=%d" % (app.sync_warning.winfo_rooty(),
                                 app.sync_bar.winfo_rooty()))

# The holds canvas: at least one row tall at 940x640; scrollable at the floor.
app.nb.select(app.level_tab)
settle()
def hold_rows():
    """The row frames as they are NOW - a replan rebuilds them."""
    return [w for w in app.hold_frame.winfo_children()
            if isinstance(w, gp.ttk.Frame)]


def long_rows():
    return [r for r in hold_rows() if any(
        isinstance(c, gp.ttk.Label) and str(c.cget("text")) == T_LONG
        for c in r.winfo_children())]


rows = hold_rows()
row_h = max(r.winfo_reqheight() for r in rows) if rows else 0
check("the holds canvas is at least one row tall at 940x640",
      rows and app.hold_canvas.winfo_height() >= row_h,
      "canvas %d, row %d" % (app.hold_canvas.winfo_height(), row_h))
long_row = long_rows()
check("  the 130-character title wraps inside its row instead of overflowing",
      long_row and long_row[0].winfo_reqwidth() <= long_row[0].winfo_width(),
      "%s" % ([(r.winfo_reqwidth(), r.winfo_width()) for r in long_row]))

# ------------------------------------------------------------ the fit floor
# The identity buttons are required at every size: the sash (Change 4) lets
# the scoreboard yield to two lines so the banner keeps its room.
audit(app, tkroot, settle, gp.FIT_FLOOR)
app.nb.select(app.level_tab)
settle()
# With the two-button question up, the floor leaves the rows no viewport
# (the question is what there is to act on); the answer collapses the
# banner to one line and the holds become a scrollable viewport.
app._banner_widgets[0].invoke()
settle()
check("  the answered banner collapses to its in-force line",
      len(app._banner_widgets) == 1, str(len(app._banner_widgets)))
check("at the floor the holds canvas is scrollable - a viewport with a "
      "scrollbar over rows taller than itself",
      app.hold_canvas.winfo_height() > 1
      and (app.hold_canvas.yview() != (0.0, 1.0)
           or app.hold_frame.winfo_reqheight() <= app.hold_canvas.winfo_height()),
      "canvas %d, rows %d, yview %s" % (app.hold_canvas.winfo_height(),
                                        app.hold_frame.winfo_reqheight(),
                                        app.hold_canvas.yview()))
long_row = long_rows()
check("  and the 130-character title still wraps inside its row",
      long_row and long_row[0].winfo_reqwidth() <= long_row[0].winfo_width(),
      "%s" % ([(r.winfo_reqwidth(), r.winfo_width()) for r in long_row]))

# ------------------------------------- 871x432: a 1366x768 panel at 150 %
audit(app, tkroot, settle, (871, 432))
tkroot.destroy()

# ---------------------------- 15b. a work area smaller than the fit floor
WORK["area"] = (683, 384)
app, tkroot, settle = open_app()
size = (tkroot.winfo_width(), tkroot.winfo_height())
check("on a 683x384 work area the window opens at 643x336 - the area minus "
      "chrome", size == (643, 336), "%dx%d" % size)
mins = tkroot.minsize()
check("  and the computed minsize is no larger than the work area",
      mins[0] <= 683 and mins[1] <= 384 and tuple(mins) == (643, 336),
      str(mins))
FIXED = {"filter_label", "only_entry", "filter_btn", "clear_btn",
         "refresh_label", "update_chk", "newer_chk", "orphan_chk",
         "consent_chk", "sync_bar", "apply_btn", "refresh_btn", "forget_btn",
         "sync_status_label", "sync_role", "level_bar", "level_apply_btn",
         "level_refresh_btn", "level_status_label", "level_role",
         "health_bar", "doctor_btn", "health_status_label", "health_role",
         "close_btn", "trust_chk"}
# 15b governs here: the fixed chrome is what must fit; the scrolling middle
# yields (the scoreboard to two lines, the holds to one row, the panes to
# whatever remains).
audit(app, tkroot, settle, size, names=FIXED, identity=False)
app.nb.select(app.level_tab)
settle()
check("  and the scoreboard yielded to its two-line minimum - the first "
      "thing that yields below the floor",
      app.level_pane.sashpos(0) == app._sash_px(2),
      "sash %d, two lines %d" % (app.level_pane.sashpos(0), app._sash_px(2)))
# With the two-button question in the lower pane nothing is left for the
# rows at this height; the buttons are what there is to act on, and the
# answer collapses the banner to one line and gives the rows a viewport.
app._banner_widgets[0].invoke()
settle()
check("  and once the question is answered the holds canvas has height - "
      "cramped, never unreachable",
      app.hold_canvas.winfo_ismapped() and app.hold_canvas.winfo_height() > 1,
      str(app.hold_canvas.winfo_height()))
tkroot.destroy()

# --------------------------- 911x480: the same panel at 150 %, work area
WORK["area"] = (911, 480)
app, tkroot, settle = open_app()
size = (tkroot.winfo_width(), tkroot.winfo_height())
check("on a 911x480 work area the window opens at 871x432", size == (871, 432),
      "%dx%d" % size)
check("  with the fit floor as its minsize", tuple(tkroot.minsize()) == (760, 420),
      str(tkroot.minsize()))
tkroot.destroy()

# ------------------------------------------- 15c. the sash follows content
WORK["area"] = (1536, 912)
REPORT["rep"] = {"stores": {"status": "not found", "detail": "no store"},
                 "accounts": [], "complete": {"short": 0}}
app, tkroot, settle = open_app()
app.nb.select(app.level_tab)
settle()
pw = app.level_pane
lines = len(app.level_text.get("1.0", "end-1c").split("\n"))
check("a short first render puts the sash at the content height (%d lines)"
      % lines, lines < 7 and pw.sashpos(0) == app._sash_px(lines),
      "sash %d, content %d" % (pw.sashpos(0), app._sash_px(lines)))
REPORT["rep"] = alignment()
app.refresh_level()
settle()
check("  and a full scoreboard grows it to seven lines",
      pw.sashpos(0) == app._sash_px(7)
      and int(app.level_text.cget("height")) == 7,
      "sash %d, seven lines %d" % (pw.sashpos(0), app._sash_px(7)))
# With the identity question answered (the banner collapses to its in-force
# line) the lower pane's fixed part is one line and a button; the two-button
# question plus a row do not fit above the floor's height, and there the
# scoreboard's two-line minimum is what holds - the buttons stay reachable,
# as the floor audit above pins.
app._banner_widgets[0].invoke()
settle()
check("  the answered banner collapses to its in-force line",
      len(app._banner_widgets) == 1, str(len(app._banner_widgets)))
dragged = app._sash_px(7) + 60
pw.sashpos(0, dragged)
pw.event_generate("<ButtonRelease-1>")
settle()
check("a scripted drag is remembered", app._sash_user == dragged,
      str(app._sash_user))
app.refresh_level()
settle()
check("  and the dragged position survives a replan", pw.sashpos(0) == dragged,
      str(pw.sashpos(0)))
rows = [w for w in app.hold_frame.winfo_children()
        if isinstance(w, gp.ttk.Frame)]
row_h = max(r.winfo_reqheight() for r in rows) if rows else 0
tkroot.geometry("940x%d" % gp.FIT_FLOOR[1])
settle()
check("after a shrink to the floor the holds pane keeps at least one row's "
      "height - the sash yields first",
      app.hold_canvas.winfo_height() >= row_h > 0
      and pw.sashpos(0) < dragged,
      "canvas %d, row %d, sash %d" % (app.hold_canvas.winfo_height(), row_h,
                                      pw.sashpos(0)))
check("  and the scoreboard keeps at least two lines",
      pw.sashpos(0) >= app._sash_px(2), str(pw.sashpos(0)))
# (Below the floor is unreachable here: the window's own minsize is the
# floor. The small-work-area case above is where that state is pinned.)
tkroot.geometry("940x640")
settle()
check("  a re-enlargement restores the dragged offset", pw.sashpos(0) == dragged,
      str(pw.sashpos(0)))
tkroot.destroy()

shutil.rmtree(root_dir, ignore_errors=True)
shutil.rmtree(GUIDIR, ignore_errors=True)
print()
print("ALL PASS" if all(ok) else "SOME CHECKS FAILED")
sys.exit(0 if all(ok) else 1)
