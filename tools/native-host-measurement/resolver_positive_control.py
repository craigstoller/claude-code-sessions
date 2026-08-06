#!/usr/bin/env python3
"""Positive control for the handle sampler's RESOLVER, not its timing.

The negative control showed the sampler catching nobody holding a store handle while the store
was demonstrably being written. That is consistent with two very different explanations:

  (a) the sampler works, but the writes are too brief to sample (temporal blindness), or
  (b) the sampler is broken for these paths -- handle duplication fails across processes, or
      NtQueryObject names don't normalize to the store root, so it would miss a store handle
      even if one were held wide open.

If (b) were true, the whole measurement says nothing at all. This distinguishes them: a CHILD
process holds a file open under the real store root for write, and the same scan used on the
helper must find it. Detection proves the enumerate -> duplicate -> resolve -> normalize ->
match-under-root pipeline works cross-process against these exact paths, leaving (a) as the
explanation for the negative control.

The child is used deliberately rather than this process: cross-process DuplicateHandle is the
step most likely to fail, and testing it against our own handles would skip it.
"""

import ctypes
import ctypes.wintypes as wt
import glob
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from measure_native_host import (  # noqa: E402
    enum_handles, query_name, nt_to_win32, find_file_type_index,
    kernel32, WRITE_MASK, PROCESS_DUP_HANDLE, DUPLICATE_SAME_ACCESS)

CHILD = r"""
import sys, time
p = sys.argv[1]
f = open(p, 'w+b')
f.write(b'resolver positive control')
f.flush()
print('HOLDING', flush=True)
time.sleep(25)
f.close()
"""


def main():
    local = os.environ["LOCALAPPDATA"]
    roots = []
    for pkg in glob.glob(os.path.join(local, "Packages", "Claude_*")):
        p = os.path.join(pkg, "LocalCache", "Roaming", "Claude", "claude-code-sessions")
        if os.path.isdir(p):
            roots.append(os.path.normcase(p))
    if not roots:
        print("no store root found")
        return 2
    store = roots[0]
    target = os.path.join(store, ".__ccs_resolver_control__.tmp")

    # The child script goes to a temp dir, not next to this file: running the harness from a
    # git checkout must not leave an untracked artifact in the tree.
    child_dir = tempfile.mkdtemp(prefix="ccs-resolver-control-")
    child_src = os.path.join(child_dir, "_hold_child.py")
    with open(child_src, "w") as fh:
        fh.write(CHILD)

    proc = subprocess.Popen([sys.executable, child_src, target],
                            stdout=subprocess.PIPE, text=True)
    try:
        line = proc.stdout.readline()
        if "HOLDING" not in line:
            print("child failed to open the control file:", line)
            return 2
        print("child pid", proc.pid, "holding", target)

        ftype = find_file_type_index()
        me = kernel32.GetCurrentProcess()
        found = None
        deadline = time.time() + 15
        attempts = 0
        while time.time() < deadline and not found:
            attempts += 1
            hp = kernel32.OpenProcess(PROCESS_DUP_HANDLE, False, proc.pid)
            if not hp:
                print("OpenProcess on child failed err=%d" % ctypes.get_last_error())
                break
            try:
                for e in enum_handles():
                    if e.UniqueProcessId != proc.pid:
                        continue
                    if ftype is not None and e.ObjectTypeIndex != ftype:
                        continue
                    dup = wt.HANDLE()
                    if not kernel32.DuplicateHandle(
                            wt.HANDLE(hp), wt.HANDLE(e.HandleValue), wt.HANDLE(me),
                            ctypes.byref(dup), 0, False, DUPLICATE_SAME_ACCESS):
                        continue
                    try:
                        name = query_name(dup.value)
                    finally:
                        kernel32.CloseHandle(dup)
                    if not name or name == "<timeout>":
                        continue
                    win = nt_to_win32(name)
                    n = os.path.normcase(win)
                    if any(n == r or n.startswith(r + "\\") for r in roots):
                        found = (win, e.GrantedAccess)
                        break
            finally:
                kernel32.CloseHandle(hp)
            if not found:
                time.sleep(0.3)

        print("attempts:", attempts)
        if found:
            path, acc = found
            print("PASS - resolver detected a cross-process store handle")
            print("   path :", path)
            print("   access: 0x%08X  writeable=%s" % (acc, bool(acc & WRITE_MASK)))
            print("   => enumerate/duplicate/resolve/normalize/match all work on these")
            print("      paths, so the negative control's zero hits reflect TIMING,")
            print("      not a broken resolver.")
            rc = 0
        else:
            print("FAIL - a file held wide open under the store was NOT detected.")
            print("   => the sampler cannot see store handles at all; every 'no hits'")
            print("      result in this measurement is meaningless.")
            rc = 1
    finally:
        try:
            proc.wait(timeout=30)
        except Exception:
            proc.kill()
        for p in (target, child_src):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except OSError as exc:
                print("cleanup failed for", p, exc)
        try:
            os.rmdir(child_dir)
        except OSError:
            pass
    print("control file removed:", not os.path.exists(target))
    return rc


if __name__ == "__main__":
    sys.exit(main())
