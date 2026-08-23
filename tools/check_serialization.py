"""The apply-time serialization boundary - proving it exists.

Two review engines independently named "establish an apply-time serialization
boundary spanning the guard, the reachability check, every row write and the
journal update" as the highest-impact fix for 0.9.15, and it was recorded in
internals.md as an open gap.

It was already there. Both engines were reasoning from a review document that
never mentioned locking - my omission, not their error - so they assumed the
lock was per-op. It is not: `_lock_path` is ONE file per journal directory,
created with O_CREAT|O_EXCL, and every mutating entry point takes it.

This file exists because that guarantee was undocumented and untested, which is
how a real property gets recorded as a missing one. It pins the property rather
than the prose:

  - the lock is global, not per-op
  - a second mutating op refuses while one is held, and names the holder
  - row writes happen while it is held
  - it is released after success AND after a refusal
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


def build():
    root = tempfile.mkdtemp(prefix="ser-")
    home = os.path.join(root, "home")
    store = os.path.join(root, "Claude", "claude-code-sessions")
    live_dir = os.path.join(store, LIVE, ORG_L)
    dorm_dir = os.path.join(store, DORM, ORG_D)
    for d in (live_dir, dorm_dir, home):
        os.makedirs(d)
    projects = os.path.join(home, ".claude", "projects", "proj")
    os.makedirs(projects)
    sid = "%032d" % 1
    with open(os.path.join(projects, sid + ".jsonl"), "w") as fh:
        fh.write('{"type":"user","message":{"content":"a real turn of prose here"}}\n')
    with open(os.path.join(live_dir, "local_%s.json" % sid), "w") as fh:
        json.dump({"cliSessionId": sid, "title": "A session", "cwd": projects,
                   "lastActivityAt": 2000}, fh)
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
    return root, env, dorm_dir


# ---------------------------------------------------------------- global, not per-op
root, env, dorm = build()
a = ccs._lock_path(env)
b = ccs._lock_path(env)
check("the lock path is one file per journal dir, not per op", a == b, a)
check("  and it lives in the ops dir", os.path.dirname(a) == env.ops_dir)
check("  under a fixed name, so every op competes for the same file",
      os.path.basename(a) == ccs.LOCK_NAME, ccs.LOCK_NAME)

# ---------------------------------------------------------------- it excludes
ccs.acquire_lock(env, "someone-else")
try:
    m = ccs.plan_sync(env, ccs.SyncFlags(to=dorm))
    check("a plan is still allowed while the lock is held (it writes nothing)",
          len(m["rows"]) == 1, str(len(m["rows"])))
    try:
        ccs.run_sync(env, m)
        check("a second APPLY refuses while the lock is held", False, "it ran")
    except ccs.Refusal as exc:
        check("a second APPLY refuses while the lock is held", True)
        check("  naming the holder so the user can tell what has it",
              "someone-else" in str(exc), str(exc)[:90])
        check("  and pointing at recover for a dead holder",
              "recover" in str(exc))
    # every mutating entry point takes the SAME lock - not one per command
    try:
        ccs.run_repoint(env, {"op_type": "repoint", "row_path": "x", "to": "y"})
        check("repoint competes for the same lock", False, "it ran")
    except ccs.Refusal as exc:
        check("repoint competes for the same lock",
              "holds the lock" in str(exc), str(exc)[:70])
    except Exception as exc:                       # noqa: BLE001
        check("repoint competes for the same lock", False,
              "%s: %s" % (type(exc).__name__, exc))
finally:
    ccs.release_lock(env)
check("releasing removes the file", not os.path.exists(a))
shutil.rmtree(root, ignore_errors=True)

# ---------------------------------------------------------------- writes are INSIDE it
root, env, dorm = build()
m = ccs.plan_sync(env, ccs.SyncFlags(to=dorm))
held_during_write = []
real_write = ccs.atomic_write


def watched_write(path, data):
    # The property that matters: at the moment a row is written, the lock file
    # exists. Anything else means the boundary has a hole in it.
    held_during_write.append(os.path.exists(ccs._lock_path(env)))
    return real_write(path, data)


ccs.atomic_write = watched_write
try:
    ccs.run_sync(env, m)
finally:
    ccs.atomic_write = real_write
check("row writes happened", bool(held_during_write), str(held_during_write))
check("  and EVERY one happened while the lock was held",
      all(held_during_write), str(held_during_write))
check("the lock is released after a successful apply",
      not os.path.exists(ccs._lock_path(env)))
shutil.rmtree(root, ignore_errors=True)

# ---------------------------------------------------------------- released on refusal
root, env, dorm = build()
m = ccs.plan_sync(env, ccs.SyncFlags(to=dorm))
real_write = ccs.atomic_write


def exploding_write(path, data):
    # ONLY row writes. Patching every atomic_write also breaks the journal,
    # and a journal failure is a different path with different handling - the
    # first version of this fixture broke that instead and reported a raw
    # OSError escaping, which was the fixture's doing, not the code's.
    if os.path.dirname(os.path.abspath(path)) == os.path.abspath(dorm):
        raise OSError(13, "permission denied")
    return real_write(path, data)


ccs.atomic_write = exploding_write
try:
    ccs.run_sync(env, m)
    check("a failing write surfaces as a refusal", False, "it succeeded")
except ccs.Refusal:
    check("a failing write surfaces as a refusal", True)
except OSError:
    check("a failing write surfaces as a refusal", False, "raw OSError escaped")
finally:
    ccs.atomic_write = real_write
check("  and the lock is released anyway, so the next run is not wedged",
      not os.path.exists(ccs._lock_path(env)))
shutil.rmtree(root, ignore_errors=True)

print()
print("ALL PASS" if all(ok) else "SOME CHECKS FAILED")
sys.exit(0 if all(ok) else 1)
