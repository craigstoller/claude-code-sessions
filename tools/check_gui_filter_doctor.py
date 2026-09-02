"""Check the GUI's --only filter and its health-check rendering.

The filter is checked against a synthetic store (does it actually narrow the
plan, and is the narrowing visible in the tally). The health report is checked
against synthetic report dicts, because what matters is that a BLOCKING finding
is never buried under routine counts.
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

ok = []


def check(name, cond, extra=""):
    print("%s %s%s" % ("OK " if cond else "BAD", name, ("  " + extra) if extra else ""))
    ok.append(bool(cond))


# ----------------------------------------------------------------- the filter
LIVE, DORM = "a" * 32, "b" * 32
ORG_L, ORG_D = "1" * 32, "2" * 32
root = tempfile.mkdtemp(prefix="filtertest-")
home = os.path.join(root, "home")
store = os.path.join(root, "Claude", "claude-code-sessions")
live_dir = os.path.join(store, LIVE, ORG_L)
os.makedirs(live_dir), os.makedirs(os.path.join(store, DORM, ORG_D)), os.makedirs(home)
projects = os.path.join(home, ".claude", "projects", "proj")
os.makedirs(projects)

TITLES = ["Northwind backtest", "northwind notes", "Acme draft", "unrelated thing"]
for i, title in enumerate(TITLES):
    sid = "%032d" % i
    with open(os.path.join(projects, sid + ".jsonl"), "w") as fh:
        fh.write('{"type":"user"}\n')
    with open(os.path.join(live_dir, "local_%s.json" % sid), "w") as fh:
        json.dump({"appSessionId": sid, "cliSessionId": sid, "title": title,
                   "cwd": projects}, fh)
with open(os.path.join(home, ".claude.json"), "w") as fh:
    json.dump({"oauthAccount": {"accountUuid": LIVE, "organizationUuid": ORG_L,
                                "emailAddress": "live@example.com"}}, fh)
with open(os.path.join(root, "Claude", "config.json"), "w") as fh:
    json.dump({"lastKnownAccountUuid": LIVE}, fh)

env = ccs.default_env()
env.home = home
env.projects_root = os.path.join(home, ".claude", "projects")
env.store_candidates = [store]
env.ops_dir = os.path.join(root, "journal", "ops")
env.moved_log = os.path.join(root, "journal", "moved-log.jsonl")
env.process_lister = lambda: []

m = ccs.plan_sync(env, ccs.SyncFlags())
check("unfiltered plan sees every session", len(m["rows"]) == 4, str(len(m["rows"])))

m = ccs.plan_sync(env, ccs.SyncFlags(only="northwind"))
titles = sorted(r["title"] for r in m["rows"])
check("filter narrows the plan", len(m["rows"]) == 2, str(len(m["rows"])))
check("  and is case-insensitive", titles == ["Northwind backtest", "northwind notes"],
      str(titles))
filtered = m["tally"].get("filtered")
# the tally records the TITLES it hid, not a count - the GUI renders either
check("  and the tally records what it hid", len(filtered) == 2, str(filtered))
check("  which the GUI's tally rendering can count",
      (len(filtered) if isinstance(filtered, (list, tuple, set)) else filtered) == 2)

m = ccs.plan_sync(env, ccs.SyncFlags(only="nothing matches this"))
check("a filter matching nothing yields an empty plan", len(m["rows"]) == 0)

# ----------------------------------------------------------- the health report
clean = {"stores": {"status": "found", "roots": ["/x"]}, "row_count": 10,
         "row_errors": [], "blank_rows": [], "dead_rows": [], "legacy_folders": [],
         "unlisted_transcripts": [], "nonterminal_ops": [], "stale_lock": False,
         "unknown_layout": []}
lines = gp.SyncApp.doctor_lines(clean)
check("a clean report leads with 'nothing blocking'",
      lines[0].startswith("Nothing is blocking"), lines[0])
# 0.15.1 (C): the window gates every MUTATION now - renames and converges
# too - so the sync-era body line has to say what the tab title says.
check("  and names a mutation, not a sync - the tab title's own word",
      lines[0] == "Nothing is blocking a mutation.", lines[0])
check("  and does not shout NEEDS ATTENTION", "NEEDS ATTENTION" not in "\n".join(lines))

# routine-but-numerous findings must NOT be treated as blocking
noisy = dict(clean, dead_rows=["r"] * 38, blank_rows=["r"] * 12,
             unlisted_transcripts=["t"] * 69)
lines = gp.SyncApp.doctor_lines(noisy)
check("38 aged-out transcripts are not called blocking",
      lines[0].startswith("Nothing is blocking"), lines[0])
check("  but they are still reported", any("retention" in l for l in lines))

for key, val, word in (("stale_lock", True, "stale lock"),
                       ("nonterminal_ops", ["op1"], "unresolved"),
                       ("row_errors", ["bad"], "unreadable"),
                       ("unknown_layout", ["?"], "unrecognised")):
    lines = gp.SyncApp.doctor_lines(dict(clean, **{key: val}))
    check("%s surfaces as NEEDS ATTENTION" % key, lines[0] == "NEEDS ATTENTION")
    check("  and names it", any(word in l for l in lines), word)

# GUI polish (Change 6): one spelling for the command - the copyable
# RECOVER_COMMAND and the doctor lines agree.
lines = gp.SyncApp.doctor_lines(dict(clean, stale_lock=True,
                                     nonterminal_ops=["op1"]))
check("the doctor lines and RECOVER_COMMAND agree on one spelling",
      all("ccs recover" not in l for l in lines)
      and sum(1 for l in lines if gp.RECOVER_COMMAND in l) == 2,
      "\n".join(l for l in lines if "recover" in l))

# blocking findings must come BEFORE the inventory, not after it
lines = gp.SyncApp.doctor_lines(dict(noisy, stale_lock=True))
check("blocking findings precede the inventory",
      lines.index("NEEDS ATTENTION") < lines.index("Inventory"))

# a store that could not be read must BLOCK, not read as healthy: gather_doctor
# exits 2 there and every mutation fails closed
lines = gp.SyncApp.doctor_lines(
    dict(clean, stores={"status": "error", "roots": [], "detail": "access denied"}))
check("an unreadable store is blocking", lines[0] == "NEEDS ATTENTION", lines[0])
check("  and the reason is shown", any("access denied" in l for l in lines))

# ------------------- the library/GUI contract for the destination picker
# _candidate_listing puts a cross-pair warning on the line AFTER the candidate.
# The picker must carry that note onto the button. It did not once: the marker
# was moved off the candidate line to stop it wrapping in a terminal, and the
# GUI silently lost it - so on a three-account machine every empty candidate
# rendered identically and the wrong one was picked. Pin the contract.
LIVE_ORG = "53346e14" + "0" * 24
a1, a2 = "250c8128" + "0" * 24, "d0fcaa6f" + "0" * 24
o_own = "d4e23045" + "0" * 24
d1 = os.path.join(root, "s1"); d2 = os.path.join(root, "s2")
os.makedirs(d1, exist_ok=True); os.makedirs(d2, exist_ok=True)
listing = ccs._candidate_listing([(a1, LIVE_ORG, d1), (a1, o_own, d2)],
                                 live_org=LIVE_ORG)
cands = gp.SyncApp._candidates(listing)
check("picker finds both candidates", len(cands) == 2, str(len(cands)))
check("  each entry carries a note field", all(len(c) == 3 for c in cands))
warned = [c for c in cands if c[2]]
check("  exactly the cross pair is warned", len(warned) == 1, str(len(warned)))
check("  and it is the one sharing the signed-in org",
      warned and warned[0][0].endswith("/53346e14"), warned[0][0] if warned else "-")
# the candidate token must be the FULL account/org pair, not a bare org id: a
# saved org id stopped being unique the moment a third account appeared, and
# every plan then refused with Apply disabled
check("  the token identifies an account AND an org",
      all(c[0].count("/") == 1 and len(c[0].split("/")[0]) == 8 for c in cands),
      cands[0][0])
check("  the warning text survives onto the button",
      warned and "shares your signed-in org" in warned[0][2])
check("  the footnote never becomes a button",
      not any("<account>" in c[1] for c in cands))

# ------------------ GUI polish (Change 6). Health runs on its first selection
# The window for real (root withdrawn, workers inline) over this store:
# selecting Health runs the doctor without a press; the report's first line
# is the one pinned above.
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
tkroot = tk.Tk()
tkroot.withdraw()
app = gp.SyncApp(tkroot)


def settle():
    for _ in range(30):
        tkroot.update()


settle()
check("Health has not run the doctor at open",
      not app.health_text.get("1.0", "end").strip()
      or "Nothing is blocking" not in app.health_text.get("1.0", "end"))
app.nb.select(app.health_tab)
settle()
first = app.health_text.get("1.0", "end").splitlines()[0]
check("selecting Health runs the doctor without a press",
      first == "Nothing is blocking a mutation.", first)
check("  and the status line says so",
      app.health_status.get() == "Health check: nothing blocking a mutation",
      app.health_status.get())
tkroot.destroy()

shutil.rmtree(root, ignore_errors=True)
shutil.rmtree(GUIDIR, ignore_errors=True)
print()
print("ALL PASS" if all(ok) else "SOME CHECKS FAILED")
sys.exit(0 if all(ok) else 1)
