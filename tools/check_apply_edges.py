"""The four apply-path edges the 0.9.15 panel named as untested.

Each is a state the write loop can genuinely reach and none had coverage:

  1. A crash between a row's write and its `written` marker. Recovery must be
     idempotent - the row is already on disk while the journal says pending.
  2. Drift that would change a row's swap classification between plan and
     apply. The plan judged it a non-swap; the destination has since moved.
  3. A voucher REPOINTED rather than deleted. The row still exists, so a check
     that looked for a missing file would miss it entirely.
  4. Two applies interleaved. The lock is supposed to make this impossible;
     this asserts it from inside the write loop rather than from outside.
"""
import json
import os
import shutil
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import claude_code_sessions as ccs  # noqa: E402

ok = []


def check(name, cond, extra=""):
    print("%s %s%s" % ("OK " if cond else "BAD", name, ("  " + extra) if extra else ""))
    ok.append(bool(cond))


LIVE, DORM = "a" * 32, "b" * 32
ORG_L, ORG_D = "1" * 32, "2" * 32
OLD, NEW, PLAIN = "%032d" % 21, "%032d" % 22, "%032d" % 23


def prose(n, tag="t"):
    return ["%s turn %d with enough words to count as a real message" % (tag, i)
            for i in range(n)]


def turns(bodies):
    return "\n".join(json.dumps({"type": "user" if i % 2 == 0 else "assistant",
                                 "message": {"content": b}})
                     for i, b in enumerate(bodies)) + "\n"


def build():
    root = tempfile.mkdtemp(prefix="edge-")
    home = os.path.join(root, "home")
    store = os.path.join(root, "Claude", "claude-code-sessions")
    live = os.path.join(store, LIVE, ORG_L)
    dorm = os.path.join(store, DORM, ORG_D)
    for d in (live, dorm, home):
        os.makedirs(d)
    projects = os.path.join(home, ".claude", "projects", "proj")
    os.makedirs(projects)
    shared = prose(12, "s")
    for sid, bodies in ((OLD, shared), (NEW, shared + prose(4, "n")),
                        (PLAIN, prose(6, "p"))):
        with open(os.path.join(projects, sid + ".jsonl"), "w", encoding="utf-8") as fh:
            fh.write(turns(bodies))
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
    return root, env, live, dorm


def row(d, name, sid, title, when=5000):
    with open(os.path.join(d, name), "w") as fh:
        json.dump({"cliSessionId": sid, "title": title, "cwd": "proj",
                   "lastActivityAt": when}, fh)


# ======================================================================== 1
print("\n--- 1. crash between the write and the written marker ---")

root, env, live, dorm = build()
row(live, "local_add.json", PLAIN, "Plain add")
m = ccs.plan_sync(env, ccs.SyncFlags(to=dorm))
fired = {"n": 0}


def crash_once(point):
    if point == "sync-write-before-save" and fired["n"] == 0:
        fired["n"] += 1
        raise KeyboardInterrupt("killed after the write, before the marker")


ccs._crash_hook = crash_once
try:
    ccs.run_sync(env, m)
except BaseException:                            # noqa: BLE001 - the simulated kill
    pass
finally:
    ccs._crash_hook = None

dest = os.path.join(dorm, "local_add.json")
check("the row IS on disk - the write completed", os.path.exists(dest))
op = ccs.nonterminal_ops(env)[0]
pending = [r for r in op.manifest["rows"] if not r.get("written")]
check("  while the journal still calls it pending - the exact torn state",
      len(pending) == 1, "%d pending" % len(pending))
before = open(dest, "rb").read()
res = ccs.recover_op(env, op, "forward")
check("recover --forward completes rather than refusing", res == "completed", str(res))
check("  the row is byte-identical - the re-write was a no-op, not a double write",
      open(dest, "rb").read() == before)
check("  and the op is closed", not ccs.nonterminal_ops(env))
shutil.rmtree(root, ignore_errors=True)


# ======================================================================== 2
print("\n--- 2. drift that would change a row's swap classification ---")

root, env, live, dorm = build()
# planned as a NON-swap: both sides name the same conversation
row(live, "local_same.json", NEW, "Same slot")
row(dorm, "local_same.json", NEW, "Same slot", when=4000)
m = ccs.plan_sync(env, ccs.SyncFlags(update=True, to=dorm))
r = [x for x in m["rows"] if x["title"] == "Same slot"][0]
check("planned as a refresh, NOT a swap", r["swaps_conversation"] is False)
check("  so it carries no displaced conversation", r["displaced_session"] is None)
# the destination moves to a different conversation after planning: applying
# the planned bytes now WOULD swap, and was never judged as one
row(dorm, "local_same.json", OLD, "Same slot", when=4000)
try:
    ccs.run_sync(env, m)
    check("apply refuses rather than performing an unjudged swap", False, "it wrote")
except ccs.Refusal as exc:
    check("apply refuses rather than performing an unjudged swap", True)
    check("  as drift, naming the row", "changed since this refresh was planned"
          in str(exc), str(exc)[:80])
opened = json.load(open(os.path.join(dorm, "local_same.json"), encoding="utf-8"))
check("  and the destination still opens what it opened",
      opened["cliSessionId"] == OLD)
shutil.rmtree(root, ignore_errors=True)


# ======================================================================== 3
print("\n--- 3. a voucher REPOINTED rather than deleted ---")

root, env, live, dorm = build()
row(live, "local_swap.json", NEW, "Swap slot")
row(dorm, "local_swap.json", OLD, "Swap slot", when=4000)
row(dorm, "local_voucher.json", OLD, "Voucher", when=4000)
m = ccs.plan_sync(env, ccs.SyncFlags(update=True, to=dorm))
r = [x for x in m["rows"] if x["title"] == "Swap slot"][0]
check("planned safe - the voucher vouches", r["displaced_orphan"] is False)
# the row still EXISTS; it just points somewhere else now. A check that looked
# for a missing file rather than a missing POINTER would sail past this.
row(dorm, "local_voucher.json", PLAIN, "Voucher", when=4000)
check("  the voucher row still exists on disk",
      os.path.exists(os.path.join(dorm, "local_voucher.json")))
try:
    ccs.run_sync(env, m)
    check("apply refuses - a repointed voucher is a lost voucher", False, "it wrote")
except ccs.Refusal as exc:
    check("apply refuses - a repointed voucher is a lost voucher",
          "reachability changed" in str(exc), str(exc)[:70])
shutil.rmtree(root, ignore_errors=True)


# ======================================================================== 4
print("\n--- 4. two applies interleaved, from inside the write loop ---")

root, env, live, dorm = build()
row(live, "local_a.json", PLAIN, "Row A")
row(live, "local_b.json", NEW, "Row B")
first = ccs.plan_sync(env, ccs.SyncFlags(to=dorm))
second = ccs.plan_sync(env, ccs.SyncFlags(to=dorm))
check("two independent plans were built", len(first["rows"]) == 2
      and len(second["rows"]) == 2)

seen = {"tried": False, "refused": None}
real_write = ccs.atomic_write


def reenter(path, data):
    # The interleaving the panel asked about: a SECOND apply attempted while
    # the first is between its reachability check and its writes. Attempted
    # from inside the loop rather than from outside, because outside the loop
    # there is nothing to interleave with.
    if not seen["tried"]:
        seen["tried"] = True
        try:
            ccs.run_sync(env, second)
            seen["refused"] = False
        except ccs.Refusal as exc:
            seen["refused"] = str(exc)
    return real_write(path, data)


ccs.atomic_write = reenter
try:
    ccs.run_sync(env, first)
finally:
    ccs.atomic_write = real_write

check("the second apply was attempted mid-write", seen["tried"])
check("  and REFUSED - the lock spans the whole apply",
      isinstance(seen["refused"], str), str(seen["refused"])[:70])
check("  naming the lock rather than failing some other way",
      isinstance(seen["refused"], str) and "holds the lock" in seen["refused"],
      str(seen["refused"])[:70])
check("  while the first apply still completed",
      all(os.path.exists(os.path.join(dorm, n))
          for n in ("local_a.json", "local_b.json")))
check("  and the lock was released at the end",
      not os.path.exists(ccs._lock_path(env)))
shutil.rmtree(root, ignore_errors=True)

print()
print("ALL PASS" if all(ok) else "SOME CHECKS FAILED")
sys.exit(0 if all(ok) else 1)
