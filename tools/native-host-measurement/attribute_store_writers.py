#!/usr/bin/env python3
"""Who holds handles under the claude-code-sessions store?

The companion question to measure_native_host.py. That script shows the helper holds nothing
under the store; this one shows which processes DO, so the store activity the directory
watcher records is positively attributed rather than merely un-attributed. Without this, "the
helper wasn't seen holding a store handle" is weaker than it looks -- it could equally mean the
sampler never resolves store handles for anyone.

Same non-admin technique, no name filter: enumerate every process's File handles and report
every one that resolves under a store root.
"""

import ctypes
import ctypes.wintypes as wt
import glob
import json
import os
import subprocess
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from measure_native_host import (  # noqa: E402
    enum_handles, query_name, nt_to_win32, find_file_type_index,
    kernel32, WRITE_MASK, PROCESS_DUP_HANDLE, DUPLICATE_SAME_ACCESS, now, Watcher)


def process_table():
    out = {}
    r = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
         "Get-CimInstance Win32_Process | ForEach-Object { "
         "\"$($_.ProcessId)|$($_.Name)|$($_.ExecutablePath)\" }"],
        capture_output=True, text=True, timeout=60)
    for line in r.stdout.splitlines():
        parts = line.strip().split("|", 2)
        if len(parts) == 3 and parts[0].isdigit():
            out[int(parts[0])] = (parts[1], parts[2])
    return out


def main():
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0
    out_path = sys.argv[2] if len(sys.argv) > 2 else "attribution.json"

    local = os.environ["LOCALAPPDATA"]
    roots = []
    for pkg in glob.glob(os.path.join(local, "Packages", "Claude_*")):
        p = os.path.join(pkg, "LocalCache", "Roaming", "Claude", "claude-code-sessions")
        if os.path.isdir(p):
            roots.append(os.path.normcase(p))
    classic = os.path.normcase(os.path.join(os.environ["APPDATA"], "Claude",
                                            "claude-code-sessions"))
    if os.path.isdir(classic):
        roots.append(classic)
    roots_for_watch = list(roots)

    # NEGATIVE CONTROL. Watch the store for real write activity during the very same window
    # in which handles are sampled. If the watcher records writes while the sampler catches
    # nobody holding a store handle, then the sampler is demonstrably blind to this write
    # pattern -- and "the helper was never caught holding one" proves nothing about the helper.
    events = []
    watchers = []
    for r in roots_for_watch:
        w = Watcher(r, events, r)
        w.start()
        watchers.append(w)
    time.sleep(1.0)

    ftype = find_file_type_index()
    procs = process_table()
    # Scope limit, recorded honestly: only processes whose image path mentions Claude are
    # scanned. A system-wide scan would mean a DuplicateHandle + NtQueryObject round trip for
    # every handle on the machine, with a 150 ms timeout on each hang-prone pipe -- minutes of
    # wall clock and a thread per hang. The question here is which CLAUDE process accounts for
    # the store writes the watcher saw, so that is the set worth paying for.
    candidates = {pid for pid, (name, exe) in procs.items()
                  if "claude" in (name or "").lower() or "claude" in (exe or "").lower()}
    print("candidate processes:", len(candidates))
    holders = defaultdict(lambda: {"paths": set(), "writeable": False, "name": "?",
                                   "exe": "?", "samples": 0})
    cache = {}
    me = kernel32.GetCurrentProcess()
    samples = 0
    deadline = time.time() + seconds
    while time.time() < deadline:
        samples += 1
        try:
            entries = enum_handles()
        except Exception as exc:
            print("enum failed:", exc)
            break
        opened = {}
        for e in entries:
            if ftype is not None and e.ObjectTypeIndex != ftype:
                continue
            pid = e.UniqueProcessId
            if pid not in candidates:
                continue
            key = (e.Object, pid, e.HandleValue)
            if key in cache:
                name = cache[key]
            else:
                if pid not in opened:
                    opened[pid] = kernel32.OpenProcess(PROCESS_DUP_HANDLE, False, pid)
                hp = opened[pid]
                if not hp:
                    cache[key] = None
                    continue
                dup = wt.HANDLE()
                if not kernel32.DuplicateHandle(wt.HANDLE(hp), wt.HANDLE(e.HandleValue),
                                                wt.HANDLE(me), ctypes.byref(dup), 0,
                                                False, DUPLICATE_SAME_ACCESS):
                    cache[key] = None
                    continue
                try:
                    name = query_name(dup.value, timeout=0.15)
                finally:
                    kernel32.CloseHandle(dup)
                cache[key] = name
            if not name or name == "<timeout>":
                continue
            win = nt_to_win32(name)
            n = os.path.normcase(win)
            if any(n == r or n.startswith(r + "\\") for r in roots):
                rec = holders[pid]
                rec["paths"].add(win)
                rec["samples"] += 1
                rec["name"], rec["exe"] = procs.get(pid, ("?", "?"))
                if e.GrantedAccess & WRITE_MASK:
                    rec["writeable"] = True
        for hp in opened.values():
            if hp:
                kernel32.CloseHandle(hp)

    for w in watchers:
        w.stop_flag.set()
    result = {
        "finished": now(), "samples": samples, "roots": roots,
        "watcher_live": [w.live for w in watchers],
        "store_write_events_during_window": len(events),
        "store_events": events[:60],
        "holders": {str(pid): {"name": v["name"], "exe": v["exe"],
                               "writeable": v["writeable"], "samples": v["samples"],
                               "paths": sorted(v["paths"])[:20]}
                    for pid, v in holders.items()},
    }
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)

    print("watchers live:", [w.live for w in watchers])
    print("NEGATIVE CONTROL -- store write events seen by watcher during this "
          "same window:", len(events))
    for e in events[:12]:
        print("    ", e["action"], e["path"])
    print("samples:", samples, "| processes holding store handles:", len(holders))
    if events and not holders:
        print("\n>>> The store WAS written during the window, yet handle sampling caught")
        print(">>> NO process holding a store handle. The sampler is blind to this write")
        print(">>> pattern; absence of helper hits is therefore NOT evidence of absence.")
    for pid, v in sorted(holders.items()):
        print("  pid=%-7s %-24s writeable=%-5s samples=%d"
              % (pid, v["name"], v["writeable"], v["samples"]))
        print("      exe:", v["exe"])
        for p in sorted(v["paths"])[:6]:
            print("      ", p)


if __name__ == "__main__":
    main()
