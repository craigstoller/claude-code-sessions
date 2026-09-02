"""Exercise the GUI's --live path against a synthetic identity disagreement.

Builds a throwaway store with two accounts and two identity files that name
DIFFERENT accounts, then checks: the refusal is the one the GUI matches on, the
uuids it would offer are the right two, and asserting one actually resolves the
plan. Touches nothing real.
"""
import json
import os
import shutil
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import claude_code_sessions as ccs  # noqa: E402

GUI = os.path.join(tempfile.mkdtemp(), "gp.py")
shutil.copy(os.path.join(REPO, "claude_code_sessions_gui.py"), GUI)
sys.path.insert(0, os.path.dirname(GUI))
import gp  # noqa: E402

A = "a" * 32          # oauthAccount says this one
B = "b" * 32          # config.json says this one
ORG_A, ORG_B = "1" * 32, "2" * 32
# A deliberately owns TWO org dirs, mirroring a real machine (an account paired
# with its own org and with the other account's). A bare account uuid then
# matches two stores and is refused - which is why the picker asserts a PATH.
PAIRS = ((A, ORG_A), (A, ORG_B), (B, ORG_B))

root = tempfile.mkdtemp(prefix="livetest-")
home = os.path.join(root, "home")
store = os.path.join(root, "Claude", "claude-code-sessions")
for a, o in PAIRS:
    os.makedirs(os.path.join(store, a, o))
os.makedirs(home)
for a, o in PAIRS:
    with open(os.path.join(store, a, o, "local_%s%s.json" % (a[:8], o[:4])), "w") as fh:
        json.dump({"appSessionId": a[:8], "cliSessionId": "x", "title": "t"}, fh)
with open(os.path.join(home, ".claude.json"), "w") as fh:
    json.dump({"oauthAccount": {"accountUuid": A, "organizationUuid": ORG_A,
                                "emailAddress": "oauth-side@example.com"}}, fh)
with open(os.path.join(root, "Claude", "config.json"), "w") as fh:
    json.dump({"lastKnownAccountUuid": B}, fh)

env = ccs.default_env()
env.home = home
env.store_candidates = [store]

ok = []


def check(name, cond, extra=""):
    print("%s %s%s" % ("OK " if cond else "BAD", name, ("  " + extra) if extra else ""))
    ok.append(bool(cond))


dis = ccs._identity_disagreement(env)
check("a disagreement exists", dis == (A, B), str(dis and (dis[0][:4], dis[1][:4])))

msg = ""
try:
    ccs.plan_sync(env, ccs.SyncFlags())
    check("plan refuses without --live", False, "it did NOT refuse")
except ccs.Refusal as exc:
    msg = str(exc)
    check("plan refuses without --live", True)

# the exact condition the GUI branches on
matched = ("cannot identify the signed-in account" in msg) and ("disagree" in msg)
check("GUI would detect this refusal", matched)

# the GUI must NOT mistake it for the destination-picker refusal
check("not confused with the destination picker",
      "more than one other account store" not in msg)

# labels the picker would render
labels = [gp.SyncApp._account_label.__get__(
    type("E", (), {"env": env})(), object)(u) for u in dis]
check("both accounts get a label", all(labels), " | ".join(labels))
check("oauth-side email recovered", "oauth-side@example.com" in labels[0], labels[0])

# the bug review caught: a bare account uuid is ambiguous when the account owns
# several org dirs, and the picker had no way back from that refusal
try:
    ccs.plan_sync(env, ccs.SyncFlags(live=A))
    check("bare account uuid is ambiguous (regression guard)", False,
          "it resolved - the multi-store case is no longer being exercised")
except ccs.Refusal as exc:
    check("bare account uuid is ambiguous (regression guard)",
          "matched" in str(exc).lower())

# what the picker actually asserts: a full store path, unique by construction
stores = [(a, o, p) for a, o, p in ccs._account_dirs(env) if a in dis]
check("picker would offer one button per store", len(stores) == 3, str(len(stores)))
dirs = ccs._account_dirs(env)
for a, o, p in stores:
    tag = "%s…/%s…" % (a[:4], o[:4])
    # 1. the assertion itself must resolve to exactly this store
    try:
        src = ccs._resolve_live_assertion(env, p, dirs)
        check("asserting %s resolves to that exact store" % tag,
              src.account_uuid == a and src.org_uuid == o)
        check("  and is marked as user-asserted", src.resolved_from == "user")
    except ccs.Refusal as exc:
        check("asserting %s resolves to that exact store" % tag, False,
              str(exc).splitlines()[0][:90])
        continue
    # 2. end to end. A destination ambiguity here is NOT a live failure - it is
    #    the destination picker's job, and the GUI routes it there.
    try:
        m = ccs.plan_sync(env, ccs.SyncFlags(live=p))
        check("  plan resolves, destination is a different account",
              m["dest_account"] != a, m["dest_account"][:4] + "…")
        check("  recorded as a live_override", bool(m.get("live_override")))
    except ccs.Refusal as exc:
        first = str(exc).splitlines()[0]
        check("  refusal here is the DESTINATION picker's, not the live path's",
              "more than one other account store" in first, first[:70])

# an account NEITHER file names must be refused
try:
    ccs.plan_sync(env, ccs.SyncFlags(live="c" * 32))
    check("a third account is refused", False, "it was accepted!")
except ccs.Refusal:
    check("a third account is refused", True)

# --------------- GUI polish (Change 2 item 2). the refusal feeds the banner
# The window for real over this store: on the One-session tab the identity
# refusal renders the shared banner IN-PANE - one button per disagreeing
# store, the same builder Level uses - above the verbatim refusal text; no
# Toplevel opens at all.
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


env.projects_root = os.path.join(home, ".claude", "projects")
env.ops_dir = os.path.join(root, "journal", "ops")
env.moved_log = os.path.join(root, "journal", "moved-log.jsonl")
env.process_lister = lambda: []
gp.threading = _FakeThreading
gp.load_pref = lambda: ""
gp.save_pref = lambda _v: None
gp.ccs.default_env = lambda: env
toplevels = []
_real_toplevel = tk.Toplevel


class _SpyToplevel(_real_toplevel):
    def __init__(self, *a, **kw):
        toplevels.append(self)
        _real_toplevel.__init__(self, *a, **kw)


gp.tk.Toplevel = _SpyToplevel
tkroot = tk.Tk()
tkroot.withdraw()
app = gp.SyncApp(tkroot)


def settle():
    for _ in range(30):
        tkroot.update()


settle()
app.nb.select(app.sync_tab)
settle()


def walk(w):
    out = []
    for c in w.winfo_children():
        out.append(c)
        out += walk(c)
    return out


buttons = [w for w in walk(app.sync_banner) if isinstance(w, gp.ttk.Button)]
check("the identity refusal renders the banner in-pane, one button per store",
      len(buttons) == 3, str(len(buttons)))
check("  with the shared wording", any(
    isinstance(w, gp.ttk.Label) and "Which account is the Claude desktop app"
    in str(w.cget("text")) for w in walk(app.sync_banner)))
check("  the verbatim refusal text renders under it",
      "cannot identify the signed-in account" in app.text.get("1.0", "end"))
check("  and no Toplevel opened", toplevels == [])
check("  the status line asks the question",
      "signed into" in app.status.get(), app.status.get())
buttons[0].invoke()
settle()
check("a button sets the one answer, bound to its pair",
      app.live_choice == stores[0][2] and app._live_pair == (A, B),
      repr((app.live_choice, app._live_pair)))
check("  and the replan carried it",
      app.manifest is not None or "more than one other account store"
      in app.text.get("1.0", "end"))
tkroot.destroy()

shutil.rmtree(root, ignore_errors=True)
print()
print("ALL PASS" if all(ok) else "SOME CHECKS FAILED")
sys.exit(0 if all(ok) else 1)
