"""Verdict tests after the review fixes, including the false-PASS routes Codex found."""
import sys, os, tempfile, shutil, mmap
sys.path.insert(0, r"C:\Users\craig\Projects\_Tools\claude-code-sessions\tools\native-host-measurement")
import importlib, watch_ceremony
importlib.reload(watch_ceremony)
from watch_ceremony import verdict, snapshot, diff_snapshots, mapped_write_control

R = r"C:\store"
CN = [".ccs-watch-canary-1.tmp", ".ccs-watch-canary-1.txt",
      ".ccs-watch-canary-9.tmp", ".ccs-watch-canary-9.txt"]


def ev(ts, path, action="MODIFIED", root=R):
    return {"ts": ts, "root": root, "action": action, "path": path}


def base(**over):
    rep = {
        "store_roots": [R],
        "canary_names": CN,
        "phases": {"p1_begin": "2026-08-07T18:00:00.000+00:00",
                   "p1_end": "2026-08-07T18:05:00.000+00:00",
                   "app_closed_helper_alive": "2026-08-07T18:06:00.000+00:00",
                   "helper_exited": "2026-08-07T18:10:00.000+00:00"},
        "window_note": None,
        "helper_exit_code": 0,
        "mapped_write_control_caught": True,
        "window_snapshot_diff": {"added": [], "removed": [], "changed": [], "errors": []},
        "watchers": [{"root": R, "live": True, "error": None, "overflows": [],
                      "event_count": 0}],
        "events": [ev("2026-08-07T18:01:00.000+00:00", ".ccs-watch-canary-1.tmp"),
                   ev("2026-08-07T18:02:00.000+00:00", r"acc\org\local_abc.json"),
                   ev("2026-08-07T18:07:00.000+00:00", ".ccs-watch-canary-9.tmp")],
    }
    rep.update(over)
    return rep


ok = []


def show(name, rep, want):
    st, why = verdict(rep)
    good = st == want
    print("%s %-40s -> %-12s (want %s)" % ("OK " if good else "BAD", name, st, want))
    if not good:
        for w in why:
            print("        ", w)
    ok.append(good)


show("pass-quiet", base(), "PASS")
show("fail-watcher-write", base(events=base()["events"] + [
    ev("2026-08-07T18:08:00.000+00:00", r"acc\org\evil.json")]), "FAIL")
show("fail-snapshot-mapped-write", base(window_snapshot_diff={
    "added": [], "removed": [], "changed": [r"acc\org\local_abc.json"], "errors": []}), "FAIL")

# --- the false-PASS routes review found ---
show("R1 contaminated-window-now-voids",
     base(window_note="desktop app relaunched inside the window (contaminated)"),
     "INCONCLUSIVE")
show("R2 overflow-outside-window-now-voids",
     base(watchers=[{"root": R, "live": True, "error": None,
                     "overflows": ["2026-08-07T18:02:30.000+00:00"], "event_count": 0}]),
     "INCONCLUSIVE")
show("R3 snapshot-errors-now-void",
     base(window_snapshot_diff={"added": [], "removed": [], "changed": [],
                                "errors": [{"where": "walk", "error": "denied"}]}),
     "INCONCLUSIVE")
show("R4 unwatched-root-now-voids",
     base(store_roots=[R, r"C:\store2"]), "INCONCLUSIVE")
show("R5 missing-canary-names-now-voids", base(canary_names=[]), "INCONCLUSIVE")
show("R6 per-root-canary-missing-in-window",
     base(events=[e for e in base()["events"]
                  if e["ts"] < "2026-08-07T18:06:00.000+00:00"]), "INCONCLUSIVE")
# substring-masquerade: a REAL file whose name contains the marker is no longer excused
show("R7 canary-lookalike-counts-as-real",
     base(events=base()["events"] + [
         ev("2026-08-07T18:08:00.000+00:00", r"acc\.ccs-watch-canary-evil.json")]), "FAIL")
# the leg the real 2026-08-07 run lost: the helper was terminated, so its shutdown
# code never ran and the run cannot speak to shutdown-time writes
show("R8 terminated-helper-voids-run", base(helper_exit_code=1), "INCONCLUSIVE")
show("R9 unknown-exit-code-voids-run", base(helper_exit_code=None), "INCONCLUSIVE")

print("\n--- live: mapped-write control now runs the real snapshot pipeline ---")
d = tempfile.mkdtemp(prefix="mc-")
caught = mapped_write_control(d, set())
print("mapped_write_control (pipeline) ->", caught)
ok.append(caught)

print("--- live: large-file mapped write is no longer invisible (the 8MB false-PASS) ---")
big = os.path.join(d, "big.bin")
with open(big, "wb") as fh:
    fh.write(b"\0" * (9 * 1024 * 1024))
before = snapshot(d, set())
with open(big, "r+b") as fh:
    mm = mmap.mmap(fh.fileno(), 0)
    mm[0:16] = b"MAPPED-BIG-WRITE"
    mm.flush(); mm.close()
after = snapshot(d, set())
dd = diff_snapshots(before, after)
print("diff on a 9 MB in-place mapped write:", {k: dd[k] for k in ("added", "changed")})
ok.append(bool(dd["changed"]))
shutil.rmtree(d, ignore_errors=True)

print()
print("ALL PASS" if all(ok) else "SOME CHECKS FAILED")
sys.exit(0 if all(ok) else 1)
