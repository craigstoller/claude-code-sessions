"""Verdict renderer for the elevated chrome-native-host capture.

Consumes a capture directory produced by run-elevated-capture.ps1 (capture.csv,
workload-timeline.json, workload-timeline.json.canary.log, binding.json) and applies
the acceptance protocol from docs/internals.md, "The Chrome native host, and why it
still counts":

  - INCONCLUSIVE if a positive control failed: canary heartbeat gaps (lossy or blind
    trace), or no desktop-app-attributed store mutation during phase P1 (a trace that
    cannot see the app's own writes proves nothing by being empty).
  - FAIL if any non-canary mutation of either store root, by ANY process, occurred in
    the helper-only window (P2 app-closed-helper-alive .. P3 helper-exited). The
    endpoint is the store, not the helper.
  - PASS otherwise -- licensing an exclusion bound to the measured helper build only
    (binding.json carries the hash); the guard change itself is a separate reviewed
    commit, not this script.

Windows-only input, stdlib-only, Python 3.9+.
"""

import csv
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

MUTATING_OPS = {
    # ProcMon operation names that can change a store root's contents. Reads,
    # queries, opens, and closes are deliberately not here.
    "WriteFile", "CreateFile", "SetRenameInformationFile",
    "SetRenameInformationFileEx", "SetDispositionInformationFile",
    "SetDispositionInformationFileEx", "SetEndOfFileInformationFile",
    "SetAllocationInformationFile", "SetBasicInformationFile", "FlushBuffersFile",
    "CreateFileMapping", "SetSecurityFile", "DeleteFile",
}
# CreateFile only mutates when it creates/overwrites; disposition is in Detail.
CREATE_MUTATING_MARKERS = ("Created", "Overwritten", "Superseded", "OpenIf", "OverwriteIf")
CANARY_MARKER = ".ccs-capture-canary-"
HEARTBEAT_SECONDS = 30
HEARTBEAT_SLACK = 3.0  # a gap counts only beyond slack * nominal interval


def parse_iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def load_timeline(d):
    events = json.loads((d / "workload-timeline.json").read_text(encoding="utf-8-sig"))
    if isinstance(events, dict):
        events = [events]
    by_key = {}
    for e in events:
        by_key.setdefault((e["phase"], e["event"]), []).append(parse_iso(e["utc"]))
    return events, by_key


def window(by_key):
    """The helper-only interval, or None with a reason."""
    start = by_key.get(("P2", "app-closed-helper-alive"))
    if not start:
        return None, "helper died with the app (P2 'app-closed-helper-alive' absent)"
    end = (by_key.get(("P3", "helper-exited")) or by_key.get(("P2", "helper-exited-early")))
    if not end:
        return None, "no helper exit event recorded"
    contaminated = by_key.get(("P2", "APP-RELAUNCHED-DURING-WINDOW"))
    if contaminated and contaminated[0] < end[0]:
        return None, "desktop app relaunched inside the window"
    return (start[0], end[0]), None


def canary_gaps(d, span):
    """Heartbeat gaps inside [span] -- each is an interval the trace can't vouch for."""
    log = d / "workload-timeline.json.canary.log"
    if not log.exists():
        return None  # no canary log at all
    stamps = []
    for line in log.read_text(encoding="utf-8-sig").splitlines():
        parts = line.split()
        if len(parts) >= 2 and not line.strip().endswith("canary-ERROR"):
            try:
                stamps.append(parse_iso(parts[0]))
            except ValueError:
                continue
    stamps = sorted(t for t in stamps if span[0] <= t <= span[1])
    gaps = []
    limit = timedelta(seconds=HEARTBEAT_SECONDS * HEARTBEAT_SLACK)
    prev = span[0]
    for t in stamps + [span[1]]:
        if t - prev > limit:
            gaps.append((prev, t))
        prev = t
    return gaps


def iter_store_rows(csv_path, roots):
    """Yield (time_of_day_str, process, pid, op, path, detail) for rows under a root.

    ProcMon CSV 'Time of Day' lacks a date; the timeline provides the date context.
    We keep the raw string and also parse clock time for interval checks.
    """
    lroots = [r.lower() for r in roots]
    with open(csv_path, newline="", encoding="utf-8-sig", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            path = (row.get("Path") or "")
            lp = path.lower()
            if not any(lp.startswith(r) for r in lroots):
                continue
            yield row


def clock_of(row):
    """ProcMon 'Time of Day' like '11:52:03.1234567 PM' -> naive time-of-day."""
    s = (row.get("Time of Day") or "").strip()
    for fmt in ("%I:%M:%S.%f %p", "%H:%M:%S.%f", "%I:%M:%S %p", "%H:%M:%S"):
        try:
            # %f tops out at 6 digits; trim 7-digit fractions
            parts = s.split(".")
            if len(parts) == 2:
                frac = parts[1].split(" ")
                frac[0] = frac[0][:6]
                s2 = parts[0] + "." + " ".join(frac)
            else:
                s2 = s
            return datetime.strptime(s2, fmt).time()
        except ValueError:
            continue
    return None


def in_utc_window(row, span):
    """True if the row's local clock time falls inside span (converted to local).

    ProcMon exports local time; the timeline is UTC. Compare in local clock time,
    tolerating a capture that crosses midnight by treating the window as at most a
    few hours long.
    """
    t = clock_of(row)
    if t is None:
        return False
    lo = span[0].astimezone().time()
    hi = span[1].astimezone().time()
    if lo <= hi:
        return lo <= t <= hi
    return t >= lo or t <= hi  # crossed midnight


def is_mutation(row):
    op = row.get("Operation") or ""
    if op not in MUTATING_OPS:
        return False
    if op == "CreateFile":
        detail = row.get("Detail") or ""
        return any(m in detail for m in CREATE_MUTATING_MARKERS)
    return (row.get("Result") or "") == "SUCCESS" or op == "WriteFile"


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        print("usage: python analyze_capture.py <capture-dir>")
        return 2
    d = Path(argv[1])
    binding = json.loads((d / "binding.json").read_text(encoding="utf-8-sig"))
    events, by_key = load_timeline(d)
    roots = []
    for e in events:
        if e["event"] == "store-roots":
            roots = [r.strip() for r in e["detail"].split("|")]
    if not roots:
        print("VERDICT: INCONCLUSIVE -- no store roots recorded in timeline")
        return 1

    span, reason = window(by_key)
    p1 = (by_key.get(("P1", "begin")), by_key.get(("P2", "begin")))

    report = {"binding": binding, "roots": roots}
    problems = []

    # Control 1: canary heartbeat continuity across the whole capture.
    cap_start = by_key.get(("P0", "capture-start"))
    cap_end = by_key.get(("P4", "capture-stopped"))
    if cap_start and cap_end:
        gaps = canary_gaps(d, (cap_start[0], cap_end[0]))
        if gaps is None:
            problems.append("no canary log -- loss probe absent")
        elif gaps:
            problems.append("canary heartbeat gaps: " +
                            ", ".join(f"{a.isoformat()}..{b.isoformat()}" for a, b in gaps))
        report["canary_gaps"] = 0 if not gaps else len(gaps or [])

    # Walk the CSV once, bucketing store-root rows.
    p1_mutations, window_mutations, canary_rows = [], [], 0
    for row in iter_store_rows(d / "capture.csv", roots):
        if CANARY_MARKER in (row.get("Path") or ""):
            canary_rows += 1
            continue
        if not is_mutation(row):
            continue
        keep = {k: row.get(k) for k in
                ("Time of Day", "Process Name", "PID", "Operation", "Path", "Detail", "Result")}
        if p1[0] and p1[1] and in_utc_window(row, (p1[0][0], p1[1][0])):
            p1_mutations.append(keep)
        if span and in_utc_window(row, span):
            window_mutations.append(keep)

    report["canary_rows_seen_in_trace"] = canary_rows
    report["p1_app_open_mutations"] = len(p1_mutations)
    report["helper_only_window"] = ([span[0].isoformat(), span[1].isoformat()] if span else reason)
    report["helper_only_mutations"] = window_mutations

    # Control 2: the trace must SEE canary writes at all.
    if canary_rows == 0:
        problems.append("trace contains zero canary rows -- capture was blind to the store roots")
    # Control 3: the app-open phase must show real (non-canary) store mutations.
    if p1[0] and not p1_mutations:
        problems.append("no desktop-app store mutations captured during P1 -- "
                        "empty result would prove nothing")
    if span is None:
        problems.append("decisive window absent: " + reason)

    (d / "verdict.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    if problems:
        print("VERDICT: INCONCLUSIVE")
        for p in problems:
            print("  -", p)
        return 1
    if window_mutations:
        print(f"VERDICT: FAIL -- {len(window_mutations)} non-canary store mutation(s) "
              "during the helper-only window (exclusion not licensed):")
        for m in window_mutations[:20]:
            print("  ", m["Time of Day"], m["Process Name"], m["Operation"], m["Path"])
        return 1
    print("VERDICT: PASS -- no non-canary store mutation during the helper-only window.")
    print(f"  Licenses exclusion for helper sha256={binding['helper_sha256']} ONLY.")
    print("  Controls: canary continuity OK; P1 app-open store mutations:",
          len(p1_mutations))
    print("  The guard change is a separate reviewed commit binding path chain + hash.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
