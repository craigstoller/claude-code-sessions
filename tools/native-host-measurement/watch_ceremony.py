#!/usr/bin/env python3
"""The conclusive chrome-native-host run, as a continuous store-side watch.

Implements the AMENDED acceptance protocol (docs/internals.md, "Amendment
2026-08-07: the store-side watch replaces the elevated trace"). Read that first;
this is the instrument, that is the reasoning.

WHY A WATCHER AND NOT A TRACE. The acceptance endpoint is a statement about the
STORE -- "no non-control mutation of either store root by any process during the
helper-only window" -- not about the helper. ReadDirectoryChangesW answers exactly
that question: continuous (not sampled, so millisecond-scale atomic .tmp->rename
writes cannot slip between observations, which is what defeated handle sampling),
unattributed (irrelevant to the endpoint), needs no elevation, and costs kilobytes
instead of the ~9 GB/min an unfiltered machine-wide trace measured.

WHAT MAKES A QUIET RESULT MEAN ANYTHING. Three controls, each able to void the run:

  1. CANARY HEARTBEAT. A write-rename-delete under each store root every 20 s. Its
     events must appear in the watcher's own log. Absent heartbeats = that watcher
     was not delivering, and its silence proves nothing.
  2. OVERFLOW DETECTION. ReadDirectoryChangesW reports buffer overflow by returning
     success with zero bytes, discarding the whole buffer. Unhandled, that is a
     dropped interval indistinguishable from a quiet one -- a false PASS generator.
     Every overflow is timestamped and any overflow inside the decisive window is
     an automatic INCONCLUSIVE.
  3. REAL-TRAFFIC CONTROL. Phase 1 requires observing genuine (non-canary) store
     writes caused by the desktop app. A watcher that only ever sees its own canary
     has not been shown to see the traffic that matters.

PROCESS-TREE GUARD. The 2026-08-05 measurement lost its decisive leg because the
measuring session was hosted by the desktop app, so closing the app killed the
measurement. This script walks its own ancestry and refuses to start from inside
the app's process tree.

No elevation. Run from your own terminal. Python 3.9+, stdlib only, Windows.
"""

import argparse
import ctypes
import ctypes.wintypes as wt
import glob
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# ctypes' default restype is c_int, which truncates 64-bit HANDLEs. Always declare.
kernel32.CreateFileW.restype = wt.HANDLE
kernel32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p,
                                 wt.DWORD, wt.DWORD, wt.HANDLE]
kernel32.CloseHandle.restype = wt.BOOL
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.ReadDirectoryChangesW.restype = wt.BOOL
kernel32.ReadDirectoryChangesW.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD, wt.BOOL,
                                           wt.DWORD, ctypes.POINTER(wt.DWORD),
                                           ctypes.c_void_p, ctypes.c_void_p]
kernel32.CreateToolhelp32Snapshot.restype = wt.HANDLE
kernel32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.QueryFullProcessImageNameW.restype = wt.BOOL
kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR,
                                                ctypes.POINTER(wt.DWORD)]
kernel32.Process32FirstW.restype = wt.BOOL
kernel32.Process32FirstW.argtypes = [wt.HANDLE, ctypes.c_void_p]
kernel32.Process32NextW.restype = wt.BOOL
kernel32.Process32NextW.argtypes = [wt.HANDLE, ctypes.c_void_p]

INVALID_HANDLE = ctypes.c_void_p(-1).value
TH32CS_SNAPPROCESS = 0x00000002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
FILE_LIST_DIRECTORY = 0x0001
FILE_SHARE_ALL = 0x07
OPEN_EXISTING = 3
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
NOTIFY_FILTER = (0x001 | 0x002 | 0x004 | 0x008 | 0x010 | 0x040 | 0x100)
# name | dirname | attributes | size | last write | creation | security.
# LAST_ACCESS excluded: NTFS last-access updates are off by default, so it is an
# unreliable signal and pure noise where enabled.
ACTIONS = {1: "ADDED", 2: "REMOVED", 3: "MODIFIED", 4: "RENAMED_OLD", 5: "RENAMED_NEW"}

APP_IMAGE_RE = r"\windowsapps\claude_"          # the MSIX app EXECUTES from here
HELPER_TAIL = r"\chromenativehost\chrome-native-host.exe"
CANARY_PREFIX = ".ccs-watch-canary-"
HEARTBEAT_SECONDS = 20


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD),
                ("th32ProcessID", wt.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
                ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wt.DWORD), ("szExeFile", wt.WCHAR * 260)]


def process_table():
    """{pid: (ppid, exe_name, full_path_or_None)} for every visible process."""
    out = {}
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE:
        return out
    try:
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not kernel32.Process32FirstW(wt.HANDLE(snap), ctypes.byref(e)):
            return out
        while True:
            path = None
            hp = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False,
                                      e.th32ProcessID)
            if hp:
                try:
                    buf = ctypes.create_unicode_buffer(32768)
                    size = wt.DWORD(32768)
                    if kernel32.QueryFullProcessImageNameW(wt.HANDLE(hp), 0, buf,
                                                           ctypes.byref(size)):
                        path = buf.value
                finally:
                    kernel32.CloseHandle(wt.HANDLE(hp))
            out[e.th32ProcessID] = (e.th32ParentProcessID, e.szExeFile, path)
            if not kernel32.Process32NextW(wt.HANDLE(snap), ctypes.byref(e)):
                break
    finally:
        kernel32.CloseHandle(wt.HANDLE(snap))
    return out


def app_procs(table=None):
    t = table if table is not None else process_table()
    return {pid: p for pid, (_, _, p) in t.items()
            if p and APP_IMAGE_RE in p.lower()}


def helper_procs(table=None):
    t = table if table is not None else process_table()
    return {pid: p for pid, (_, _, p) in t.items()
            if p and p.lower().endswith(HELPER_TAIL)}


def ancestry(pid, table):
    """Walk parents, cycle-safe, so a corrupt table cannot hang the guard."""
    chain, seen = [], set()
    cur = pid
    while cur in table and cur not in seen:
        seen.add(cur)
        ppid, name, path = table[cur]
        chain.append((cur, name, path))
        cur = ppid
    return chain


class Watcher(threading.Thread):
    """Recursive ReadDirectoryChangesW over one root, with overflow detection."""

    def __init__(self, root, label):
        super().__init__(daemon=True)
        self.root = root
        self.label = label
        self.events = []
        self.overflows = []          # timestamps of discarded buffers -- lossy intervals
        self.live = False
        self.error = None
        self.stop_flag = threading.Event()

    def run(self):
        h = kernel32.CreateFileW(self.root, FILE_LIST_DIRECTORY, FILE_SHARE_ALL, None,
                                 OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, None)
        if not h or h == INVALID_HANDLE:
            self.error = "CreateFileW failed err=%d" % ctypes.get_last_error()
            return
        self.live = True
        # 1 MB: overflow is the enemy here, and a discarded buffer costs a whole run.
        buf = ctypes.create_string_buffer(1024 * 1024)
        nbytes = wt.DWORD()
        try:
            while not self.stop_flag.is_set():
                ok = kernel32.ReadDirectoryChangesW(
                    wt.HANDLE(h), buf, len(buf), True, NOTIFY_FILTER,
                    ctypes.byref(nbytes), None, None)
                if not ok:
                    self.error = "ReadDirectoryChangesW err=%d" % ctypes.get_last_error()
                    break
                if self.stop_flag.is_set():
                    break
                if nbytes.value == 0:
                    # Documented overflow signal: the ENTIRE buffer was discarded, so an
                    # unknown number of events in this interval are gone. Never treat as quiet.
                    self.overflows.append(now())
                    continue
                off, stamp = 0, now()
                while True:
                    nxt = int.from_bytes(buf[off:off + 4], "little")
                    action = int.from_bytes(buf[off + 4:off + 8], "little")
                    namelen = int.from_bytes(buf[off + 8:off + 12], "little")
                    name = buf[off + 12:off + 12 + namelen].decode("utf-16-le",
                                                                  errors="replace")
                    self.events.append({"ts": stamp, "root": self.label,
                                        "action": ACTIONS.get(action, str(action)),
                                        "path": name})
                    if not nxt:
                        break
                    off += nxt
        finally:
            kernel32.CloseHandle(wt.HANDLE(h))


class Heartbeat(threading.Thread):
    """Write-rename-delete a canary under each root, proving the watchers are live."""

    def __init__(self, roots):
        super().__init__(daemon=True)
        self.roots = roots
        self.beats = []
        self.names = set()           # EXACT filenames created, for precise exclusion
        self.stop_flag = threading.Event()

    def run(self):
        n = 0
        while not self.stop_flag.is_set():
            n += 1
            for root in self.roots:
                tn, fn_ = "%s%d.tmp" % (CANARY_PREFIX, n), "%s%d.txt" % (CANARY_PREFIX, n)
                self.names.update((tn, fn_))
                try:
                    with open(os.path.join(root, tn), "w") as fh:
                        fh.write(now())
                    os.replace(os.path.join(root, tn), os.path.join(root, fn_))
                    os.remove(os.path.join(root, fn_))
                    self.beats.append({"ts": now(), "root": root, "n": n, "ok": True})
                except OSError as exc:
                    self.beats.append({"ts": now(), "root": root, "n": n, "ok": False,
                                       "error": str(exc)})
            self.stop_flag.wait(HEARTBEAT_SECONDS)


def snapshot(root, canaries=()):
    """Content fingerprint of every file under a root.

    `canaries` is the set of EXACT control filenames the runner created. Excluding by
    substring instead would carve out a blind namespace: any real store file whose name
    contained the canary marker would vanish from this manifest and simultaneously read
    as control traffic to the watcher.

    The second instrument, and not redundant with the watcher: MEASURED 2026-08-07,
    a pure mapped-section write to an existing file, within its existing size,
    produces NO ReadDirectoryChangesW notification at all -- not delayed, absent at
    60 s. That is precisely the mutation class the original protocol singled out.
    Comparing bytes across the window catches it, because it asks what the store IS
    rather than waiting to be told what happened to it.

    What the pair covers, and what it does not: the watcher time-localizes ordinary
    writes; the snapshot catches any NET change by any mechanism. A mutation that
    was written and then perfectly reverted inside the window would evade both. That
    residual is accepted as absurd for this threat model and recorded rather than
    hidden.
    """
    import hashlib
    files, errors = {}, []

    def on_walk_error(exc):
        # A subtree silently skipped at BOTH boundaries would cancel out of the diff and
        # read as quiet. Traversal failure is missing evidence, so it must be reported
        # and void the run -- never swallowed.
        errors.append({"where": "walk", "error": str(exc)})

    for dirpath, _dirnames, filenames in os.walk(root, onerror=on_walk_error):
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root)
            if rel in canaries or os.path.basename(rel) in canaries:
                continue
            try:
                st = os.stat(full)
                h = hashlib.sha256()
                with open(full, "rb") as fh:
                    for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                        h.update(chunk)
                # No size cap. An earlier revision hashed only files <= 8 MB and recorded
                # size alone above that, which left a deterministic false-PASS: an in-place
                # same-size mapped write to a large file changed neither instrument. Streamed
                # so an unbounded file costs memory-bounded time rather than RAM.
                files[rel] = {"size": st.st_size, "sha256": h.hexdigest()}
            except OSError as exc:
                errors.append({"where": rel, "error": str(exc)})
    return {"files": files, "errors": errors}


def diff_snapshots(a, b):
    fa, fb = a["files"], b["files"]
    return {"added": sorted(set(fb) - set(fa)),
            "removed": sorted(set(fa) - set(fb)),
            "changed": sorted(k for k in set(fa) & set(fb) if fa[k] != fb[k]),
            "errors": a["errors"] + b["errors"]}


def mapped_write_control(root, canaries):
    """Prove the SNAPSHOT PIPELINE catches a mapped-section write.

    Deliberately not a watcher control -- the watcher demonstrably cannot see this
    class (see snapshot()). Critically, it runs through the real snapshot() and
    diff_snapshots() over the whole root rather than hashing one file directly: a
    control that bypasses the pipeline it certifies would have missed the size-cap
    false-PASS that review caught, since the direct-hash version passed while the
    pipeline was blind above 8 MB. A control that cannot fail proves nothing, so this
    one is allowed to fail and voids the run when it does.
    """
    import mmap
    name = "%smapped-control.bin" % CANARY_PREFIX
    path = os.path.join(root, name)
    try:
        with open(path, "wb") as fh:
            fh.write(b"\0" * 4096)
            fh.flush()
            os.fsync(fh.fileno())
        # The control file must be VISIBLE to these snapshots, so it is excluded from
        # `canaries` here even though the heartbeat canaries are not.
        before = snapshot(root, canaries)
        with open(path, "r+b") as fh:
            mm = mmap.mmap(fh.fileno(), 4096)
            try:
                mm[0:32] = b"mapped-section-write-control----"
                mm.flush()
            finally:
                mm.close()
        after = snapshot(root, canaries)
        d = diff_snapshots(before, after)
        caught = name in d["changed"]
        os.remove(path)
        return caught
    except (OSError, ValueError):
        return False


def discover_store_roots():
    roots = []
    for pkg in glob.glob(os.path.join(os.environ["LOCALAPPDATA"], "Packages", "Claude_*")):
        p = os.path.join(pkg, "LocalCache", "Roaming", "Claude", "claude-code-sessions")
        if os.path.isdir(p):
            roots.append(p)
    classic = os.path.join(os.environ["APPDATA"], "Claude", "claude-code-sessions")
    if os.path.isdir(classic):
        roots.append(classic)
    return roots


def is_canary(ev, canary_names):
    """Exact-name match only. A substring test would let any real store file whose name
    happened to contain the marker masquerade as control traffic."""
    return os.path.basename(ev["path"]) in canary_names


def in_span(ts, span):
    return span[0] <= ts <= span[1]


def verdict(report):
    """Pure function over a saved report -> (status, reasons). Testable offline."""
    reasons = []
    phases = report["phases"]

    def mark(name):
        return phases.get(name)

    win_start, win_end = mark("app_closed_helper_alive"), mark("helper_exited")
    p1 = (mark("p1_begin"), mark("p1_end"))
    roots = report["store_roots"]
    canary_names = set(report.get("canary_names") or ())
    if not canary_names:
        reasons.append("report records no canary filenames -- control traffic cannot be "
                       "distinguished from real traffic")

    # Every declared root must have a live watcher of its own. Without this, one working
    # watcher could satisfy a global control while another root went unobserved.
    watched = {w["root"] for w in report["watchers"]}
    for r in roots:
        if r not in watched:
            reasons.append("store root %s had no watcher -- it was never observed" % r)
    for w in report["watchers"]:
        if not w["live"]:
            reasons.append("watcher for %s never started (%s)" % (w["root"], w["error"]))
        elif w["error"]:
            reasons.append("watcher for %s failed mid-run: %s" % (w["root"], w["error"]))

    events = report["events"]
    canary_events = [e for e in events if is_canary(e, canary_names)]
    real_events = [e for e in events if not is_canary(e, canary_names)]

    # Any overflow ANYWHERE in the run voids it, not merely one inside the window.
    # Overflow timestamps are recorded when the discard is DISCOVERED, and the discarded
    # buffer may hold events from before that instant, so a near-boundary overflow cannot
    # be safely excluded from the window.
    overflows = [o for w in report["watchers"] for o in w["overflows"]]
    if overflows:
        reasons.append("%d watcher buffer overflow(s) during the run -- events were "
                       "discarded and the discard instant is not the loss instant, so no "
                       "interval can be called quiet" % len(overflows))

    # A run the runner itself flagged as contaminated (app relaunched, helper exited early)
    # can still have both boundary timestamps; it must not be allowed to license a PASS.
    if report.get("window_note"):
        reasons.append("window contaminated or truncated: " + report["window_note"])

    if p1[0] and p1[1]:
        p1_real = [e for e in real_events if in_span(e["ts"], p1)]
        for r in roots:
            if not [e for e in canary_events
                    if e.get("root") == r and in_span(e["ts"], p1)]:
                reasons.append("no canary heartbeat for %s during phase 1 -- that watcher "
                               "is not proven live" % r)
        if not p1_real:
            reasons.append("no genuine (non-canary) store writes seen during phase 1 -- "
                           "the watcher was never shown to see real traffic")
    else:
        reasons.append("phase 1 boundaries missing from the report")

    if not report.get("mapped_write_control_caught"):
        reasons.append("mapped-section write control was NOT caught by the snapshot "
                       "instrument -- the run cannot speak to the one mutation class "
                       "the watcher is known to miss")

    if not (win_start and win_end):
        reasons.append("decisive window absent: " +
                       (report.get("window_note") or "helper did not outlive the app"))
        return "INCONCLUSIVE", reasons

    span = (win_start, win_end)
    for r in roots:
        if not [e for e in canary_events if e.get("root") == r and in_span(e["ts"], span)]:
            reasons.append("no canary heartbeat for %s inside the decisive window -- "
                           "its silence is unverified" % r)

    win_real = [e for e in real_events if in_span(e["ts"], span)]

    # Second instrument: net content change across the window, catching the mapped-write
    # class the watcher cannot see.
    sdiff = report.get("window_snapshot_diff") or {}
    changed = sorted(set(sdiff.get("added", [])) | set(sdiff.get("removed", []))
                     | set(sdiff.get("changed", [])))
    if report.get("window_snapshot_diff") is None:
        reasons.append("no window snapshot diff recorded -- the mapped-write class is "
                       "unobserved for this run")
    elif sdiff.get("errors"):
        # A subtree unreadable at both boundaries cancels out of the diff and reads as
        # quiet. Missing evidence is not evidence of absence.
        reasons.append("%d snapshot traversal/read error(s) -- part of the store was not "
                       "fingerprinted, so the diff cannot be called complete"
                       % len(sdiff["errors"]))

    if reasons:
        return "INCONCLUSIVE", reasons
    if win_real or changed:
        out = []
        if win_real:
            out.append("%d watcher-observed store mutation(s) in the helper-only window"
                       % len(win_real))
            out += ["  %s %s %s" % (e["ts"], e["action"], e["path"]) for e in win_real[:20]]
        if changed:
            out.append("%d file(s) changed on disk across the window (snapshot diff)"
                       % len(changed))
            out += ["  " + c for c in changed[:20]]
        return "FAIL", out
    return "PASS", [
        "no store mutation OBSERVED in the helper-only window, by either instrument",
        "controls: %d canary events in window across %d root(s), %d real writes seen in "
        "phase 1, mapped-write control caught by the snapshot pipeline"
        % (len([e for e in canary_events if in_span(e["ts"], span)]), len(roots),
           len([e for e in real_events if in_span(e["ts"], p1)])),
        "NOTE: this is 'no observed event and no net boundary change', not literally "
        "'no mutation' -- a write perfectly reverted inside the window evades both"]


def ask(prompt):
    return input("\n>> " + prompt + ": ").strip()


def wait_gate(cond, what):
    """Never race a clock against a person."""
    while not cond():
        r = ask("Not verified yet: %s. Enter to re-check, or 'abort'" % what)
        if r.lower() == "abort":
            raise SystemExit("Operator aborted at: %s" % what)


def run_ceremony(outdir, hold_minutes):
    os.makedirs(outdir, exist_ok=True)

    table = process_table()
    chain = ancestry(os.getpid(), table)
    if any(p and APP_IMAGE_RE in p.lower() for _, _, p in chain):
        print("REFUSING: this process is inside the Claude desktop app's process tree.")
        print("Closing the app in phase 2 would kill the measurement -- the exact failure")
        print("that lost the 2026-08-05 decisive leg. Run from your own terminal instead.")
        for pid, name, path in chain:
            print("   pid %-7s %-28s %s" % (pid, name, path or ""))
        return 2

    roots = discover_store_roots()
    if not roots:
        print("No store root found - nothing to measure.")
        return 2
    print("Store roots under watch:")
    for r in roots:
        print("  ", r)

    helpers = helper_procs(table)
    if not helpers:
        print("\nHelper not running. Open Chrome and use the Claude extension once.")
        wait_gate(lambda: helper_procs(), "chrome-native-host.exe running")
        helpers = helper_procs()
    distinct = sorted(set(helpers.values()))
    if len(distinct) != 1:
        # Hashing "whichever helper we saw first" could license an exclusion for a binary
        # that was not the one exercised.
        print("REFUSING: %d distinct helper images running; the run could not bind a "
              "single measured binary:" % len(distinct))
        for p in distinct:
            print("   ", p)
        return 2
    helper_path = distinct[0]
    anchor = r"\packages\claude_"
    lp = helper_path.lower()
    if anchor not in lp or not lp.endswith(HELPER_TAIL):
        print("REFUSING: helper image is not under the anchored package path chain:",
              helper_path)
        return 2
    st = os.stat(helper_path)
    import hashlib
    with open(helper_path, "rb") as fh:
        helper_sha = hashlib.sha256(fh.read()).hexdigest().upper()

    watchers = [Watcher(r, r) for r in roots]
    for w in watchers:
        w.start()
    time.sleep(1.0)
    hb = Heartbeat(roots)
    hb.start()

    phases, window_note = {}, None
    mapped_caught, window_snapshot_diff = False, None
    d1 = d2 = None

    def phase(name):
        phases[name] = now()
        print("  [%s] %s" % (phases[name], name))

    try:
        print("\n=== PHASE 1 - controls (desktop app OPEN) ===")
        phase("p1_begin")
        if not app_procs():
            ask("Desktop app not detected - open it, then press Enter")
            wait_gate(lambda: app_procs(), "desktop app running")
        mapped_caught = all(mapped_write_control(r, hb.names) for r in roots)
        print("  mapped-section write control caught by snapshot pipeline:", mapped_caught)
        d1 = ask("Use the DESKTOP APP briefly (new chat + a throwaway message) so its own "
                 "store writes are observed. Describe what you did")
        d2 = ask("Now use the CHROME EXTENSION several times. What did you do, how many "
                 "round trips?")
        phase("p1_end")

        print("\n=== PHASE 2 - close the desktop app; the helper must SURVIVE ===")
        ask("Fully close the Claude desktop app now (system tray too). Press Enter when done")
        wait_gate(lambda: not app_procs(), "desktop app fully exited")
        if not helper_procs():
            window_note = "helper exited with the app; the helper-only window never occurred"
            print("NOTE:", window_note)
        else:
            snap_start = {r: snapshot(r, hb.names) for r in roots}
            phase("app_closed_helper_alive")
            print("Holding the helper-only window for %d minute(s)." % hold_minutes)
            print("USE THE CHROME EXTENSION during this window.")
            deadline = time.time() + hold_minutes * 60
            while time.time() < deadline:
                time.sleep(5)
                if not helper_procs():
                    window_note = "helper exited early"
                    break
                if app_procs():
                    window_note = "desktop app relaunched inside the window (contaminated)"
                    break
            d3 = ask("Window held. What extension actions did you perform during it, "
                     "and how many?")
            phases["window_workload"] = d3

            print("\n=== PHASE 3 - exit Chrome fully; the helper must EXIT ===")
            ask("Fully exit Chrome now (all windows; check the tray). Press Enter when done")
            wait_gate(lambda: not helper_procs(), "helper exited")
            phase("helper_exited")
            snap_end = {r: snapshot(r, hb.names) for r in roots}
            merged = {"added": [], "removed": [], "changed": [], "errors": []}
            empty = {"files": {}, "errors": [{"where": "root", "error": "no snapshot"}]}
            for r in roots:
                d = diff_snapshots(snap_start.get(r, empty), snap_end.get(r, empty))
                for k in ("added", "removed", "changed"):
                    merged[k].extend(os.path.join(r, x) for x in d[k])
                merged["errors"].extend(d["errors"])
            window_snapshot_diff = merged
    finally:
        time.sleep(2.0)          # let trailing notifications drain
        hb.stop_flag.set()
        for w in watchers:
            w.stop_flag.set()
        # nudge each watcher out of its blocking read so the thread can exit
        for r in roots:
            try:
                p = os.path.join(r, "%sfinal.tmp" % CANARY_PREFIX)
                with open(p, "w") as fh:
                    fh.write("x")
                os.remove(p)
            except OSError:
                pass
        time.sleep(1.0)

    events = []
    for w in watchers:
        events.extend(w.events)
    events.sort(key=lambda e: e["ts"])

    report = {
        "generated": now(),
        "store_roots": roots,
        "helper": {"path": helper_path, "sha256": helper_sha, "bytes": st.st_size},
        "windows": sys.getwindowsversion()[:3] if hasattr(sys, "getwindowsversion") else None,
        "phases": phases,
        "window_note": window_note,
        "mapped_write_control_caught": mapped_caught,
        "window_snapshot_diff": window_snapshot_diff,
        "canary_names": sorted(hb.names),
        "workload": {"p1_app": d1, "p1_extension": d2,
                     "window_extension": phases.get("window_workload")},
        "watchers": [{"root": w.root, "live": w.live, "error": w.error,
                      "overflows": w.overflows, "event_count": len(w.events)}
                     for w in watchers],
        "heartbeats": hb.beats,
        "events": events,
    }
    path = os.path.join(outdir, "report.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)

    status, reasons = verdict(report)
    report["verdict"] = {"status": status, "reasons": reasons}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)

    print("\n" + "=" * 72)
    print("VERDICT:", status)
    for r in reasons:
        print("  -", r)
    print("report:", path)
    if status == "PASS":
        print("\nLicenses an exclusion for helper sha256=%s ONLY." % helper_sha)
        print("The guard change is a separate reviewed commit binding path chain + hash.")
    print("=" * 72)
    return 0 if status == "PASS" else 1


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=None, help="output directory")
    ap.add_argument("--hold-minutes", type=int, default=3)
    ap.add_argument("--verdict", metavar="REPORT.JSON",
                    help="re-render the verdict from a saved report and exit")
    args = ap.parse_args(argv[1:])

    if args.verdict:
        with open(args.verdict, encoding="utf-8") as fh:
            report = json.load(fh)
        status, reasons = verdict(report)
        print("VERDICT:", status)
        for r in reasons:
            print("  -", r)
        return 0 if status == "PASS" else 1

    out = args.out or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "watch-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    return run_ceremony(out, args.hold_minutes)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
