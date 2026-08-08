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

shutil.rmtree(root, ignore_errors=True)
shutil.rmtree(GUIDIR, ignore_errors=True)
print()
print("ALL PASS" if all(ok) else "SOME CHECKS FAILED")
sys.exit(0 if all(ok) else 1)
