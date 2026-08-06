#!/usr/bin/env python3
"""Measure whether chrome-native-host.exe opens the claude-code-sessions store for write.

Two independent instruments, because neither alone is sufficient without admin:

  1. HANDLE SAMPLER (attributed, sampled). Enumerates the system handle table via
     NtQuerySystemInformation(SystemExtendedHandleInformation), filters to the target
     process(es), duplicates each File handle into this process and resolves its name via
     NtQueryObject. Tells us WHICH process holds WHICH path open and with what access mask.
     Catches persistent write handles. Misses opens shorter than the sample interval.

  2. DIRECTORY WATCHER (continuous, unattributed). ReadDirectoryChangesW, recursive, on each
     watch root. Catches every create/modify/delete/rename that lands, including transient
     ones the sampler would miss -- but does not say who did it.

The bridge between them: if the watcher records ZERO events under the store for a window,
then nothing wrote to the store in that window, which necessarily includes the target. That
is the direction that yields evidence; a nonzero count needs the sampler (or elimination) to
attribute.

POSITIVE CONTROL: "no events" is only meaningful if the instrument was live. A canary file is
created and deleted inside each watch root at the start and end of the run; if the canary
events do not appear in the log, that root's watcher was not working and its silence proves
nothing. A second control comes free from watching a busy sibling tree.

Requires no elevation. Run under the account that owns the target process.
"""

import argparse
import ctypes
import ctypes.wintypes as wt
import glob
import hashlib
import json
import os
import string
import sys
import threading
import time
from datetime import datetime, timezone

ntdll = ctypes.WinDLL("ntdll")
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# Explicit signatures: the ctypes default restype is c_int, which silently TRUNCATES 64-bit
# HANDLEs and makes every handle operation below fail in confusing ways on x64.
kernel32.CreateFileW.restype = wt.HANDLE
kernel32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p,
                                 wt.DWORD, wt.DWORD, wt.HANDLE]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.GetCurrentProcess.restype = wt.HANDLE
kernel32.GetCurrentProcess.argtypes = []
kernel32.CloseHandle.restype = wt.BOOL
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.DuplicateHandle.restype = wt.BOOL
kernel32.DuplicateHandle.argtypes = [wt.HANDLE, wt.HANDLE, wt.HANDLE,
                                     ctypes.POINTER(wt.HANDLE), wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.QueryDosDeviceW.restype = wt.DWORD
kernel32.QueryDosDeviceW.argtypes = [wt.LPCWSTR, wt.LPWSTR, wt.DWORD]
kernel32.ReadDirectoryChangesW.restype = wt.BOOL
kernel32.ReadDirectoryChangesW.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD, wt.BOOL,
                                           wt.DWORD, ctypes.POINTER(wt.DWORD),
                                           ctypes.c_void_p, ctypes.c_void_p]
ntdll.NtQuerySystemInformation.restype = ctypes.c_long
ntdll.NtQuerySystemInformation.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong,
                                           ctypes.POINTER(ctypes.c_ulong)]
ntdll.NtQueryObject.restype = ctypes.c_long
ntdll.NtQueryObject.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong,
                                ctypes.POINTER(ctypes.c_ulong)]
INVALID_HANDLE = ctypes.c_void_p(-1).value

STATUS_INFO_LENGTH_MISMATCH = 0xC0000004
SystemExtendedHandleInformation = 0x40
ObjectNameInformation = 1

PROCESS_DUP_HANDLE = 0x0040
DUPLICATE_SAME_ACCESS = 0x0002

# Access-mask bits that imply the handle could change the object.
ACCESS_BITS = [
    (0x0002, "FILE_WRITE_DATA"),
    (0x0004, "FILE_APPEND_DATA"),
    (0x0010, "FILE_WRITE_EA"),
    (0x0100, "FILE_WRITE_ATTRIBUTES"),
    (0x00010000, "DELETE"),
    (0x00040000, "WRITE_DAC"),
    (0x00080000, "WRITE_OWNER"),
    (0x40000000, "GENERIC_WRITE"),
    (0x10000000, "GENERIC_ALL"),
]
READ_BITS = [
    (0x0001, "FILE_READ_DATA"),
    (0x0008, "FILE_READ_EA"),
    (0x0080, "FILE_READ_ATTRIBUTES"),
    (0x00020000, "READ_CONTROL"),
    (0x80000000, "GENERIC_READ"),
]
WRITE_MASK = 0
for _bit, _n in ACCESS_BITS:
    WRITE_MASK |= _bit

# NtQueryObject(ObjectNameInformation) can block forever on a synchronous pipe handle, and a
# native-messaging host always holds such pipes. Do NOT skip by GrantedAccess: the usual
# skip-list (0x0012019F et al) is the ordinary "synchronous file, full access" mask, i.e.
# precisely the writable-file class this measurement exists to look for -- skipping it would
# manufacture the negative result. Instead every name query runs under a timeout, and results
# are cached by kernel object pointer so a given hang costs one leaked thread for the whole
# run rather than one per sample.


class UNICODE_STRING(ctypes.Structure):
    _fields_ = [("Length", ctypes.c_ushort),
                ("MaximumLength", ctypes.c_ushort),
                ("Buffer", ctypes.c_void_p)]


class SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX(ctypes.Structure):
    _fields_ = [("Object", ctypes.c_void_p),
                ("UniqueProcessId", ctypes.c_size_t),
                ("HandleValue", ctypes.c_size_t),
                ("GrantedAccess", ctypes.c_ulong),
                ("CreatorBackTraceIndex", ctypes.c_ushort),
                ("ObjectTypeIndex", ctypes.c_ushort),
                ("HandleAttributes", ctypes.c_ulong),
                ("Reserved", ctypes.c_ulong)]


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def decode_access(mask):
    names = [n for bit, n in ACCESS_BITS + READ_BITS if mask & bit]
    return names


def dos_device_map():
    """\\Device\\HarddiskVolume3 -> C:  so NT paths can be compared to Win32 paths."""
    out = {}
    buf = ctypes.create_unicode_buffer(1024)
    for letter in string.ascii_uppercase:
        dos = letter + ":"
        if kernel32.QueryDosDeviceW(dos, buf, 1024):
            for target in buf.value.split("\x00"):
                if target:
                    out[target] = dos
    return out


DEVMAP = dos_device_map()


def nt_to_win32(path):
    for dev, letter in DEVMAP.items():
        if path.startswith(dev + "\\"):
            return letter + path[len(dev):]
        if path == dev:
            return letter
    return path


def enum_handles():
    size = 1 << 22
    while True:
        buf = ctypes.create_string_buffer(size)
        ret = ctypes.c_ulong(0)
        status = ntdll.NtQuerySystemInformation(
            SystemExtendedHandleInformation, buf, size, ctypes.byref(ret))
        if status == 0:
            break
        if (status & 0xFFFFFFFF) != STATUS_INFO_LENGTH_MISMATCH:
            raise OSError("NtQuerySystemInformation failed: 0x%08X" % (status & 0xFFFFFFFF))
        size = max(size * 2, ret.value + (1 << 20))
        if size > (1 << 30):
            raise OSError("handle table too large")
    psize = ctypes.sizeof(ctypes.c_size_t)
    count = ctypes.c_size_t.from_buffer(buf, 0).value
    base = psize * 2
    entry_size = ctypes.sizeof(SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX)
    entries = []
    for i in range(count):
        off = base + i * entry_size
        if off + entry_size > size:
            break
        entries.append(SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX.from_buffer_copy(
            buf, off))
    return entries


def query_name(dup, timeout=0.25):
    """NtQueryObject(ObjectNameInformation) with a timeout, since it can block forever."""
    result = {}

    def work():
        try:
            size = 0x1000
            b = ctypes.create_string_buffer(size)
            ret = ctypes.c_ulong(0)
            st = ntdll.NtQueryObject(wt.HANDLE(dup), ObjectNameInformation,
                                     b, size, ctypes.byref(ret))
            if (st & 0xFFFFFFFF) == STATUS_INFO_LENGTH_MISMATCH and ret.value:
                size = ret.value
                b = ctypes.create_string_buffer(size)
                st = ntdll.NtQueryObject(wt.HANDLE(dup), ObjectNameInformation,
                                         b, size, ctypes.byref(ret))
            if st != 0:
                result["name"] = None
                return
            us = UNICODE_STRING.from_buffer_copy(b, 0)
            if not us.Buffer or not us.Length:
                result["name"] = None
                return
            result["name"] = ctypes.wstring_at(us.Buffer, us.Length // 2)
        except Exception:
            result["name"] = None

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return "<timeout>"
    return result.get("name")


def find_file_type_index():
    """Discover the ObjectTypeIndex for type 'File' by looking up a handle we own."""
    import msvcrt as _msvcrt          # stdlib: shares Python's own CRT fd table
    probe = open(__file__, "rb")
    idx = None
    try:
        h = _msvcrt.get_osfhandle(probe.fileno())
        mypid = os.getpid()
        for e in enum_handles():
            if e.UniqueProcessId == mypid and e.HandleValue == (h & (2 ** 64 - 1)):
                idx = e.ObjectTypeIndex
                break
    except Exception:
        idx = None
    finally:
        probe.close()
    return idx


class HandleSampler:
    def __init__(self, proc_names, watch_roots, file_type_index):
        self.proc_names = [n.lower() for n in proc_names]
        self.watch_roots = [os.path.normcase(os.path.abspath(r)) for r in watch_roots]
        self.file_type_index = file_type_index
        self.samples = 0
        self.all_paths = {}          # path -> {access_names, count, pids}
        self.hits = []               # handles resolving under a watch root
        self.pids_seen = {}
        self.unresolved_timeout = 0
        self.unresolved_dup = 0
        self.unresolved_unnamed = 0
        self.file_handles_seen = 0
        self.name_cache = {}         # kernel object ptr -> resolved name / None
        self.errors = []

    def target_pids(self):
        import subprocess
        out = {}
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 "Get-CimInstance Win32_Process | Where-Object { $_.Name -match "
                 "'chrome-native-host' } | ForEach-Object { \"$($_.ProcessId)|$($_.ExecutablePath)\" }"],
                capture_output=True, text=True, timeout=30)
            for line in r.stdout.splitlines():
                line = line.strip()
                if "|" in line:
                    pid, path = line.split("|", 1)
                    out[int(pid)] = path
        except Exception as exc:
            self.errors.append("pid lookup: %r" % (exc,))
        return out

    def sample(self):
        pids = self.target_pids()
        for pid, path in pids.items():
            self.pids_seen[pid] = path
        if not pids:
            self.samples += 1
            return
        try:
            entries = enum_handles()
        except Exception as exc:
            self.errors.append("enum: %r" % (exc,))
            return
        procs = {}
        for pid in pids:
            hp = kernel32.OpenProcess(PROCESS_DUP_HANDLE, False, pid)
            if hp:
                procs[pid] = hp
            else:
                self.errors.append("OpenProcess %d failed err=%d"
                                   % (pid, ctypes.get_last_error()))
        me = kernel32.GetCurrentProcess()
        try:
            for e in entries:
                pid = e.UniqueProcessId
                if pid not in procs:
                    continue
                if (self.file_type_index is not None
                        and e.ObjectTypeIndex != self.file_type_index):
                    continue
                self.file_handles_seen += 1
                key = (e.Object, pid, e.HandleValue)
                if key in self.name_cache:
                    name = self.name_cache[key]
                else:
                    dup = wt.HANDLE()
                    ok = kernel32.DuplicateHandle(
                        wt.HANDLE(procs[pid]), wt.HANDLE(e.HandleValue),
                        wt.HANDLE(me), ctypes.byref(dup), 0, False, DUPLICATE_SAME_ACCESS)
                    if not ok:
                        self.unresolved_dup += 1
                        self.name_cache[key] = None
                        continue
                    try:
                        name = query_name(dup.value)
                    finally:
                        kernel32.CloseHandle(dup)
                    self.name_cache[key] = name
                if name == "<timeout>":
                    self.unresolved_timeout += 1
                    continue
                if not name:
                    self.unresolved_unnamed += 1
                    continue
                win = nt_to_win32(name)
                acc = decode_access(e.GrantedAccess)
                rec = self.all_paths.setdefault(
                    win, {"access": set(), "count": 0, "pids": set(),
                          "raw_access": set()})
                rec["access"].update(acc)
                rec["raw_access"].add("0x%08X" % e.GrantedAccess)
                rec["count"] += 1
                rec["pids"].add(pid)
                nwin = os.path.normcase(win)
                for root in self.watch_roots:
                    if nwin == root or nwin.startswith(root + "\\"):
                        self.hits.append({
                            "ts": now(), "pid": pid, "path": win,
                            "granted_access": "0x%08X" % e.GrantedAccess,
                            "access_names": acc,
                            "writeable": bool(e.GrantedAccess & WRITE_MASK),
                        })
                        break
        finally:
            for hp in procs.values():
                kernel32.CloseHandle(hp)
        self.samples += 1


# ---------------------------------------------------------------- directory watcher

FILE_LIST_DIRECTORY = 0x0001
FILE_SHARE_ALL = 0x07
OPEN_EXISTING = 3
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
NOTIFY_FILTER = (0x001 | 0x002 | 0x004 | 0x008 | 0x010 | 0x040 | 0x100)
# name | dirname | attributes | size | last write | creation | security.
# LAST_ACCESS (0x020) deliberately excluded: NTFS last-access updates are disabled by
# default, so it would be an unreliable signal and pure noise where enabled.

ACTIONS = {1: "ADDED", 2: "REMOVED", 3: "MODIFIED",
           4: "RENAMED_OLD", 5: "RENAMED_NEW"}


class Watcher(threading.Thread):
    def __init__(self, root, sink, label):
        super().__init__(daemon=True)
        self.root = root
        self.sink = sink
        self.label = label
        self.live = False
        self.error = None
        self.stop_flag = threading.Event()

    def run(self):
        h = kernel32.CreateFileW(
            self.root, FILE_LIST_DIRECTORY, FILE_SHARE_ALL, None,
            OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, None)
        if not h or h == INVALID_HANDLE:
            self.error = "CreateFileW failed err=%d" % ctypes.get_last_error()
            return
        self.live = True
        buf = ctypes.create_string_buffer(64 * 1024)
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
                off = 0
                stamp = now()
                while True:
                    nxt = int.from_bytes(buf[off:off + 4], "little")
                    action = int.from_bytes(buf[off + 4:off + 8], "little")
                    namelen = int.from_bytes(buf[off + 8:off + 12], "little")
                    name = buf[off + 12:off + 12 + namelen].decode("utf-16-le",
                                                                  errors="replace")
                    self.sink.append({"ts": stamp, "root": self.label,
                                      "action": ACTIONS.get(action, str(action)),
                                      "path": name})
                    if not nxt:
                        break
                    off += nxt
        finally:
            kernel32.CloseHandle(wt.HANDLE(h))


# ---------------------------------------------------------------- snapshots

def snapshot(root):
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root)
            try:
                st = os.stat(full)
                digest = None
                if st.st_size <= 4 * 1024 * 1024:
                    with open(full, "rb") as fh:
                        digest = hashlib.sha256(fh.read()).hexdigest()
                out[rel] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns,
                            "sha256": digest}
            except OSError as exc:
                out[rel] = {"error": str(exc)}
    return out


def diff_snapshots(a, b):
    added = sorted(set(b) - set(a))
    removed = sorted(set(a) - set(b))
    changed = sorted(k for k in set(a) & set(b) if a[k] != b[k])
    return {"added": added, "removed": removed, "changed": changed}


def canary(root, tag, log):
    """Positive control: prove this watcher is actually delivering events."""
    path = os.path.join(root, ".__ccs_probe_canary_%s__.tmp" % tag)
    try:
        with open(path, "w") as fh:
            fh.write("canary")
        time.sleep(0.4)
        os.remove(path)
        log.append({"ts": now(), "root": root, "canary": tag, "ok": True})
        return os.path.basename(path)
    except OSError as exc:
        log.append({"ts": now(), "root": root, "canary": tag, "ok": False,
                    "error": str(exc)})
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=120.0)
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--out", required=True)
    ap.add_argument("--label", default="run")
    args = ap.parse_args()

    local = os.environ["LOCALAPPDATA"]
    appdata = os.environ["APPDATA"]
    store_roots = []
    for pkg in glob.glob(os.path.join(local, "Packages", "Claude_*")):
        p = os.path.join(pkg, "LocalCache", "Roaming", "Claude", "claude-code-sessions")
        if os.path.isdir(p):
            store_roots.append(p)
    classic = os.path.join(appdata, "Claude", "claude-code-sessions")
    classic_present = os.path.isdir(classic)
    if classic_present:
        store_roots.append(classic)

    # Sibling tree the desktop app writes to constantly: a free liveness control, and the
    # context that separates "helper is idle" from "helper is busy but never near the store".
    context_roots = []
    for pkg in glob.glob(os.path.join(local, "Packages", "Claude_*")):
        p = os.path.join(pkg, "LocalCache", "Roaming", "Claude")
        if os.path.isdir(p):
            context_roots.append(p)

    file_type_index = find_file_type_index()

    store_events, context_events, canary_log = [], [], []
    watchers = []
    for r in store_roots:
        w = Watcher(r, store_events, "STORE:" + r)
        w.start()
        watchers.append(w)
    for r in context_roots:
        w = Watcher(r, context_events, "CONTEXT:" + r)
        w.start()
        watchers.append(w)
    time.sleep(1.0)

    before = {r: snapshot(r) for r in store_roots}
    canary_names = []
    for r in store_roots:
        n = canary(r, "start", canary_log)
        if n:
            canary_names.append(n)

    sampler = HandleSampler(["chrome-native-host.exe"], store_roots, file_type_index)
    started = time.time()
    deadline = started + args.seconds
    while time.time() < deadline:
        sampler.sample()
        time.sleep(max(0.05, args.interval))

    for r in store_roots:
        n = canary(r, "end", canary_log)
        if n:
            canary_names.append(n)
    time.sleep(1.5)
    for w in watchers:
        w.stop_flag.set()

    after = {r: snapshot(r) for r in store_roots}

    def is_canary(ev):
        return any(ev["path"].endswith(c) for c in canary_names)

    store_real = [e for e in store_events if not is_canary(e)]
    store_canary = [e for e in store_events if is_canary(e)]

    result = {
        "label": args.label,
        "started": datetime.fromtimestamp(started, timezone.utc).isoformat(
            timespec="milliseconds"),
        "finished": now(),
        "duration_s": round(time.time() - started, 1),
        "elevated": bool(ctypes.windll.shell32.IsUserAnAdmin()),
        "store_roots": store_roots,
        "classic_path_present": classic_present,
        "classic_path": classic,
        "context_roots": context_roots,
        "file_type_index": file_type_index,
        "watchers": [{"root": w.root, "live": w.live, "error": w.error} for w in watchers],
        "canary_log": canary_log,
        "canary_events_seen": len(store_canary),
        "handle_samples": sampler.samples,
        "target_pids_seen": sampler.pids_seen,
        "handle_hits_under_store": sampler.hits,
        "file_handles_examined": sampler.file_handles_seen,
        "unresolved_timeout": sampler.unresolved_timeout,
        "unresolved_dup_failed": sampler.unresolved_dup,
        "unresolved_unnamed": sampler.unresolved_unnamed,
        "sampler_errors": sampler.errors,
        "distinct_paths_held_by_target": {
            p: {"access": sorted(v["access"]), "raw": sorted(v["raw_access"]),
                "samples": v["count"], "pids": sorted(v["pids"])}
            for p, v in sorted(sampler.all_paths.items())},
        "store_events_excluding_canary": store_real,
        "store_event_count": len(store_real),
        "context_event_count": len(context_events),
        "context_events_sample": context_events[:80],
        "snapshot_diff": {r: diff_snapshots(before[r], after[r]) for r in store_roots},
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)

    print("=" * 72)
    print("elevated:", result["elevated"], "| samples:", sampler.samples,
          "| duration:", result["duration_s"], "s")
    print("watchers live:", [(os.path.basename(w.root), w.live) for w in watchers])
    print("canary events seen in store watcher:", len(store_canary),
          "(0 means the store watcher proved nothing)")
    print("target pids:", sampler.pids_seen)
    print("handle hits UNDER STORE:", len(sampler.hits))
    for h in sampler.hits:
        print("   ", h)
    print("store events (excluding canary):", len(store_real))
    for e in store_real[:40]:
        print("   ", e)
    print("context events (positive control, app tree):", len(context_events))
    print("distinct paths held by target:", len(sampler.all_paths))
    for p, v in sorted(sampler.all_paths.items()):
        print("    %-70s %s" % (p[:70], sorted(v["access"])[:4]))
    print("file handles examined:", sampler.file_handles_seen,
          "| unresolved: timeout=%d dup_failed=%d unnamed=%d"
          % (sampler.unresolved_timeout, sampler.unresolved_dup,
             sampler.unresolved_unnamed))
    print("snapshot diff:", json.dumps(result["snapshot_diff"], indent=2)[:800])
    print("=" * 72)


if __name__ == "__main__":
    main()
