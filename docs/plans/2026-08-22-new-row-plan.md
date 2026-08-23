# `ccs new-row` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `ccs new-row`, which creates one sidebar listing row in one account pointing at a conversation that already exists on disk.

**Architecture:** A new command shaped exactly like `repoint` — plan / execute / run / undo / report / public-manifest / cmd — sharing its lock, journal, guards and refusal style. The differences are that it synthesizes a row rather than editing one (so there is no pre-image), its undo deletes rather than restores, and `classify_op` / `recover_op` need a branch for the new `op_type`.

**Tech Stack:** Python 3.9+, standard library only. Single module `claude_code_sessions.py`. Tests are standalone scripts under `tools/` that build a synthetic store in a temp directory and print `OK`/`BAD` lines, ending with `ALL PASS`.

## Global Constraints

- **Design source:** `docs/specs/2026-08-22-new-row-design.md`. Every rule below comes from it, except where the field census in Task 1 overrides it — the spec was written before the census ran.
- **One module.** All production code goes in `claude_code_sessions.py`. Do not split it; the repo's established pattern is one file.
- **Line endings:** `claude_code_sessions.py` and `tools/*.py` are LF in git (`core.autocrlf=true` handles the working copy). `docs/*.md` are CRLF in the working copy. Never normalise a file wholesale.
- **Refusals are `Refusal`, layout bugs are `LayoutError`.** A refusal names what happened, what was NOT done, and the way forward.
- **Assert nothing you did not measure.** This is the rule the whole command turns on, and it is a *threshold policy*, not an absolute — stating it as "only universal fields" would be a rule this plan then breaks three times. The actual policy, applied in Task 1:
  1. **Derived from the transcript** — `cwd`, the timestamps, `completedTurns`, `model`, `effort`, `title`, `titleSource`. Preferred wherever the transcript can settle it.
  2. **Present on ≥95% of real rows AND with a defensible zero value** — `isArchived: False`, `alwaysAllowedReasons: []`, `sessionPermissionUpdates: []`, `spawnSeed: {}`, `chromePermissionMode: None`. "Zero value" is doing the work: these say *nothing happened*, which is true of a row that has never been opened.
  3. **Everything else is omitted.** Including fields at 60% and below, **and any field whose plausible value would be a claim rather than a zero even when it clears 95%** — `classifierSummaryEnabled` is present on 97.6% of rows and is `True` on every one of them, and it is still omitted, because `True` is a behavioural setting rather than an absence. Absence is tolerated (24 rows have none), so omitting asserts strictly less at no cost.

  `permissionMode` is the one member of tier 2 with no zero value — it is on 100% of rows and every value is a claim — so it takes the most **restrictive** observed value rather than the most common. That is a documented compatibility choice, not a measurement, and it is called out as one in `NEW_ROW_DEFAULTS`. "Plausible default" remains the specific failure this command must not commit.
- **No row images in `--json`.** `post_b64` never reaches stdout.
- **Nothing is written without `--apply`.**
- **`_maybe_crash(point)`** is the crash-injection hook; tests set `ccs._crash_hook`.
- **Every test suite must end `ALL PASS`** and exit non-zero on failure, like the existing twelve.
- **Run the whole suite before every commit.** Use this exact command — a bare `python $t | tail -1` returns *tail's* exit status and will report a failing suite as passing:

```bash
for t in tools/check_*.py; do python "$t" >/dev/null 2>&1 && echo "PASS $t" || echo "FAIL $t"; done
```

  There are **12** suites today; adding `check_new_row.py` makes **13**. Every task below expects thirteen `PASS` lines.

---

### Task 1: The field census, transcript facts, and the row template

**Files:**
- Modify: `claude_code_sessions.py` (add near `_message_fingerprints`, ~line 4098)
- Test: `tools/check_new_row.py` (create)

**Interfaces:**
- Consumes: `find_transcripts(projects_root, sid)`, `_message_fingerprints(path)`, `Refusal`
- Produces:
  - `_iso_ms(ts) -> int | None`
  - `_transcript_facts(env, session_id) -> dict` with keys `path` (str), `cwd` (str), `created_ms` (int), `last_ms` (int), `turns` (int **or None**), `custom_title` (str|None), `model` (str|None), `effort` (str|None), plus the snapshot pair `size` (int) and `mtime` (int) that Task 3 journals and Task 4's preflight re-checks. Raises `Refusal`.
  - `NEW_ROW_DEFAULTS -> dict` — the static half of the template.
  - `_synthesize_row(session_id, title, title_source, facts, row_uuid) -> dict`

**The census this task is built on.** Run it first; it is the evidence every field decision below rests on, and re-running it is how a future maintainer checks whether the app has moved:

```bash
python -c "
import json,os,glob
from collections import Counter
base=os.path.expandvars(r'%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude-code-sessions')
rows=[]
for f in glob.glob(os.path.join(base,'*','*','local_*.json')):
    try: rows.append(json.load(open(f,encoding='utf-8')))
    except Exception: pass
c=Counter(k for r in rows for k in r)
print('rows:',len(rows),' distinct keys:',len(c))
for k,n in c.most_common(): print('  %-30s %4d  %5.1f%%' % (k,n,100.0*n/len(rows)))
"
```

And the orphan query, which is where the "how many conversations need this command" and "how many can derive a title" figures come from — both are asserted in the code comments, so both have to be reproducible:

```bash
python -c "
import json,os,glob
base=os.path.expandvars(r'%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude-code-sessions')
listed=set()
for f in glob.glob(os.path.join(base,'*','*','local_*.json')):
    try: d=json.load(open(f,encoding='utf-8'))
    except Exception: continue
    if isinstance(d,dict) and d.get('cliSessionId'): listed.add(d['cliSessionId'])
orphans=[p for p in glob.glob(os.path.expanduser('~/.claude/projects/*/*.jsonl'))
         if os.path.splitext(os.path.basename(p))[0] not in listed]
withtitle=0
for p in orphans:
    with open(p,encoding='utf-8',errors='replace') as fh:
        for line in fh:
            try: d=json.loads(line.strip() or '{}')
            except Exception: continue
            if isinstance(d,dict) and isinstance(d.get('customTitle'),str) and d['customTitle'].strip():
                withtitle+=1; break
print('orphaned transcripts:',len(orphans))
print('  carrying a customTitle:',withtitle)
print('  needing a placeholder :',len(orphans)-withtitle)
"
```

Measured 2026-08-23: **170 orphans, 50 with a customTitle, 120 needing a placeholder.** The number drifts — it was 169 the previous day, because a new session was created — so it is a magnitude, not a constant.

Measured 2026-08-22 over 987 rows: **52 distinct keys, and only 12 appear on every row.** There is no fixed row shape, so the template follows the three-tier policy in Global Constraints — transcript-derived first, then fields at **≥95% presence with a defensible zero value**, then omission. The 95% line is a judgement, not a discovery; what makes it defensible is that everything above it is a *zero* (`[]`, `{}`, `False`) rather than a claim, so including it asserts only "nothing has happened here yet". Specifically:

| Field | Presence | Decision |
|---|---|---|
| `sessionId` `cliSessionId` `cwd` `originCwd` `createdAt` `lastActivityAt` `title` `isArchived` `model` `permissionMode` `chromePermissionMode` `alwaysAllowedReasons` | 100% | required — include |
| `sessionPermissionUpdates` | 99.7% | include (`[]`) |
| `effort` | 99.4% | include, **derived from the transcript** |
| `lastFocusedAt` | 100% of rows that have `completedTurns`; 95% overall | include |
| `classifierSummaryEnabled` | 97.6%, and `True` on every row that has it | **omit** — `True` is behaviour, not a zero |
| `spawnSeed` | 95.7% | include (`{}`) |
| `completedTurns` | 38.2%… of *sampled* rows; 100% of recent ones | include **only when measured** |
| `titleSource` | 54.4% — `auto` 533, `user` 4 | include, **truthfully**: `user` only for `--title` |
| `reportFindingsCard` | 60.2% | **omit** |
| `chromeTabGroupId` | 5.6% | **omit** |
| `lastSpawnRootDetected` | 2.7% | **omit** |
| `remoteControlAutoEligible` | **0.9%** | **omit** |

The last four are the ones an earlier draft of this plan asserted on every synthesized row. `remoteControlAutoEligible` was to be written `True` on a field that 99.1% of real rows do not carry at all. That is the exact "plausible-looking default" the template's own comment forbids, and it is why the census comes first.

**`model` and `effort` are derived, not defaulted.** An earlier draft hardcoded `"model": "claude-opus-5"` and justified it as the account default. The census says otherwise — `claude-fable-5` is the plurality at 522/987, `claude-opus-5` is 243 — so that default was both an assertion and a wrong one. The transcript records both: `message.model` on assistant records, and a top-level `effort`. Take the **last** of each, because that is what the session was running when it stopped.

- [ ] **Step 1: Write the failing test**

Create `tools/check_new_row.py`:

```python
"""ccs new-row - creating a sidebar row for a conversation that has none."""
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


class _Ns(object):
    """A stand-in for argparse's Namespace, for driving cmd_* directly."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


LIVE, DORM = "a" * 32, "b" * 32
ORG_L, ORG_D = "1" * 32, "2" * 32
SID = "%032d" % 71


def rec(kind, body=None, ts="2026-06-14T10:00:00.000Z", cwd=None, custom=None,
        model=None, effort=None):
    d = {"type": kind, "timestamp": ts}
    if body is not None:
        d["message"] = {"content": body}
    if model:
        d.setdefault("message", {})["model"] = model
    if cwd:
        d["cwd"] = cwd
    if custom:
        d["customTitle"] = custom
    if effort:
        d["effort"] = effort
    return json.dumps(d)


def build(records, sid=SID):
    """A store with one signed-in account, one dormant, and one transcript."""
    root = tempfile.mkdtemp(prefix="newrow-")
    home = os.path.join(root, "home")
    store = os.path.join(root, "Claude", "claude-code-sessions")
    live = os.path.join(store, LIVE, ORG_L)
    dorm = os.path.join(store, DORM, ORG_D)
    for d in (live, dorm, home):
        os.makedirs(d)
    projects = os.path.join(home, ".claude", "projects", "proj")
    os.makedirs(projects)
    with open(os.path.join(projects, sid + ".jsonl"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(records) + "\n")
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


def prose(n):
    return [rec("user" if i % 2 == 0 else "assistant",
                "turn %d with enough words in it to count as a real message" % i,
                ts="2026-06-14T10:%02d:00.000Z" % (i + 1),
                model="claude-fable-5" if i % 2 else None,
                effort="xhigh")
            for i in range(n)]


OPENER = rec("user", "the opening message, long enough to be a real one",
             ts="2026-06-14T09:00:00.000Z", cwd=r"C:\Users\craig\Projects\Personal")


# ------------------------------------------------------------ transcript facts
print("\n--- transcript facts ---")

root, env, live, dorm = build([OPENER] + prose(8))
f = ccs._transcript_facts(env, SID)
check("cwd comes from the transcript",
      f["cwd"] == r"C:\Users\craig\Projects\Personal", f["cwd"])
check("createdAt is the FIRST record in sequential order, not the earliest",
      f["created_ms"] == ccs._iso_ms("2026-06-14T09:00:00.000Z"), str(f["created_ms"]))
check("lastActivityAt is the LAST record in sequential order",
      f["last_ms"] == ccs._iso_ms("2026-06-14T10:08:00.000Z"), str(f["last_ms"]))
check("turns are counted as _message_fingerprints counts them",
      f["turns"] == 9, str(f["turns"]))
check("no customTitle means None", f["custom_title"] is None)
check("the model is DERIVED from the transcript, never defaulted",
      f["model"] == "claude-fable-5", str(f["model"]))
check("  and so is effort", f["effort"] == "xhigh", str(f["effort"]))
shutil.rmtree(root, ignore_errors=True)

# The first-wins / last-wins asymmetry, pinned. cwd also populates originCwd,
# and a session that changed directories still originated in the first one;
# model and effort should be whatever the session was running when it stopped.
root, env, live, dorm = build([
    rec("user", "opening message long enough to count here",
        ts="2026-06-14T09:00:00.000Z", cwd=r"C:\First\Place",
        effort="high"),
    rec("assistant", "a reply long enough to count as a real message",
        ts="2026-06-14T09:30:00.000Z", model="claude-opus-4-8"),
    rec("user", "a later message from somewhere else entirely",
        ts="2026-06-14T10:00:00.000Z", cwd=r"C:\Second\Place",
        effort="xhigh"),
    rec("assistant", "a later reply long enough to count as a message",
        ts="2026-06-14T10:30:00.000Z", model="claude-fable-5")])
f = ccs._transcript_facts(env, SID)
check("cwd is FIRST-wins - the session's origin, not its last stop",
      f["cwd"] == r"C:\First\Place", f["cwd"])
check("  while model is LAST-wins", f["model"] == "claude-fable-5", str(f["model"]))
check("  and so is effort", f["effort"] == "xhigh", str(f["effort"]))
shutil.rmtree(root, ignore_errors=True)

# customTitle is LAST-wins too. A rename appends a new record rather than
# editing the old one, so first-wins hands back a title the user already
# replaced. Measured 2026-08-23: 47 of 507 transcripts here carry more than one
# distinct customTitle - 9%, not a corner case.
root, env, live, dorm = build([
    rec("user", "the opening message, long enough to be a real one",
        ts="2026-06-14T09:00:00.000Z", cwd=r"C:\Users\craig\Projects\Personal",
        custom="Task manager performance audit"),
    rec("assistant", "a reply long enough to count as a real message",
        ts="2026-06-14T09:30:00.000Z", model="claude-fable-5"),
    rec("user", "a later message after the conversation was renamed",
        ts="2026-06-14T10:00:00.000Z",
        custom="Task manager performance audit (fork)")])
f = ccs._transcript_facts(env, SID)
check("customTitle is LAST-wins - a rename is not undone",
      f["custom_title"] == "Task manager performance audit (fork)",
      str(f["custom_title"]))
shutil.rmtree(root, ignore_errors=True)

# An unmeasurable turn count must stay unmeasured. _message_fingerprints
# returns None for a transcript over TRANSCRIPT_COMPARE_MAX_BYTES, and
# len(None or []) would silently turn "too big to count" into "0 turns" -
# a false assertion, on exactly the large conversations worth recovering.
root, env, live, dorm = build([OPENER] + prose(8))
real_fp = ccs._message_fingerprints
ccs._message_fingerprints = lambda p: None
try:
    f = ccs._transcript_facts(env, SID)
finally:
    ccs._message_fingerprints = real_fp
check("an uncountable transcript reports turns as None, NOT as zero",
      f["turns"] is None, repr(f["turns"]))
shutil.rmtree(root, ignore_errors=True)

print()
print("ALL PASS" if all(ok) else "SOME CHECKS FAILED")
sys.exit(0 if all(ok) else 1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tools/check_new_row.py`
Expected: FAIL — `AttributeError: module 'claude_code_sessions' has no attribute '_transcript_facts'`

- [ ] **Step 3: Write minimal implementation**

Add `import datetime` on the line above `_iso_ms`. The module places stdlib imports at the section that first needs them (`import time` at :410, `import re` at :230, `import argparse` at :2405) rather than collecting them at the top; follow that. `json`, `os` and `time` are already imported.

```python
def _iso_ms(ts):
    """An ISO-8601 transcript timestamp as epoch milliseconds, or None.

    Transcripts write `2026-06-14T09:00:00.000Z`. Parsed with the same
    tolerance the rest of this module applies to the app's format: a value it
    cannot read is None, never a guess, and the caller decides what that means.
    """
    if not isinstance(ts, str) or len(ts) < 19:
        return None
    try:
        base = datetime.datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None
    frac = 0
    if len(ts) > 20 and ts[19] == ".":
        digits = ""
        for ch in ts[20:]:
            if not ch.isdigit():
                break
            digits += ch
        frac = int((digits + "000")[:3]) if digits else 0
    # Stamping UTC explicitly is what makes .timestamp() exact. A naive
    # datetime would be read as local time, so createdAt would be wrong by the
    # machine's offset (8 hours here) and wrong by a DIFFERENT amount on a
    # machine in another zone. Transcripts have always written Z; a value
    # carrying some other offset is parsed as UTC anyway, which is a known and
    # accepted approximation rather than a silent one.
    base = base.replace(tzinfo=datetime.timezone.utc)
    return int(base.timestamp()) * 1000 + frac


def _transcript_facts(env, session_id):
    """Everything a synthesized row needs, read from the transcript itself.

    Refuses rather than guessing. A row built on values that could not be read
    is a guess wearing the app's clothes, and the whole point of the template is
    that it asserts nothing it cannot support.

    Sequential order, NOT minimum and maximum. An earlier draft of the spec said
    to take the earliest timestamp "so a malformed tail cannot move the start",
    which is exactly backwards: scanning the file for a minimum is what lets a
    corrupted, back-dated record at the tail pull the start earlier. The first
    record's timestamp is what resists that - and it is right for the ordinary
    case because transcripts are append-only, which is the real justification.
    """
    found = find_transcripts(env.projects_root, session_id)
    if not found:
        raise Refusal(
            "no transcript on disk for {0}, so there is nothing for a new row to "
            "open. Check the id - 'doctor' lists conversations that no account "
            "points at.".format(session_id))
    if len(found) > 1:
        raise Refusal("{0} exists in more than one project folder; refusing to guess "
                      "which:\n{1}".format(session_id,
                                           "\n".join("   " + f for f in found)))
    path = found[0]
    # Stat BEFORE reading, and again after - see the comparison at the end.
    try:
        before = os.stat(path)
    except OSError as exc:
        raise Refusal("could not stat the transcript for {0}: {1}. Nothing was "
                      "written.".format(session_id, exc))
    cwd = custom = model = effort = None
    first_ms = last_ms = None
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(d, dict):
                    continue
                # cwd is FIRST-wins while model and effort below are LAST-wins,
                # and the asymmetry is deliberate. cwd populates `originCwd` as
                # well as `cwd`, and a session that changed directories mid-way
                # still originated in the first one - which is also what the app
                # sorts and groups by. model and effort are the opposite: what
                # the session was running when it stopped is what a resumed row
                # should carry.
                if cwd is None and isinstance(d.get("cwd"), str) and d["cwd"]:
                    cwd = d["cwd"]
                # LAST-wins, like model and effort. Renaming a conversation
                # appends a new customTitle rather than editing the old record,
                # so first-wins resurrects a title the user already replaced.
                # Measured 2026-08-23: 47 of 507 transcripts on this machine
                # carry more than one distinct customTitle - 9%, not a corner
                # case - and the later one is the live one (one example goes
                # "Task manager performance audit" -> "... (fork)").
                if isinstance(d.get("customTitle"), str) and d["customTitle"].strip():
                    custom = d["customTitle"].strip()
                # LAST of each - see the note on cwd above for why these two go
                # the other way.
                if isinstance(d.get("effort"), str) and d["effort"]:
                    effort = d["effort"]
                msg = d.get("message")
                if isinstance(msg, dict) and isinstance(msg.get("model"), str) \
                        and msg["model"] and not msg["model"].startswith("<"):
                    model = msg["model"]          # skip the "<synthetic>" marker
                ms = _iso_ms(d.get("timestamp"))
                if ms is not None:
                    if first_ms is None:
                        first_ms = ms
                    last_ms = ms
    except OSError as exc:
        raise Refusal("could not read the transcript for {0}: {1}. Nothing was "
                      "written.".format(session_id, exc))
    missing = []
    if not cwd:
        missing.append("no cwd")
    if first_ms is None:
        missing.append("no usable timestamp")
    if not model:
        # A transcript with no assistant record - someone typed a prompt and
        # closed the app before a reply. `model` is on 100% of the 987 rows
        # measured and has no zero value, so there is nothing to omit and
        # nothing to derive; the only alternative to refusing is inventing one.
        # Refusing costs the user a conversation in which nothing was said back,
        # which is the cheapest thing this rule could cost. Called out here
        # because it is a deliberate trade, not an oversight - if it ever bites
        # someone with a conversation worth keeping, the fix is a --model flag,
        # not a silent default.
        missing.append("no assistant reply, so nothing records which model it ran")
    if missing:
        raise Refusal(
            "the transcript for {0} parses but cannot populate a row ({1}), so a "
            "row built from it would assert values this tool never read. Nothing "
            "was written.".format(session_id, " and ".join(missing)))
    # _message_fingerprints does its own I/O and returns None for a transcript
    # over TRANSCRIPT_COMPARE_MAX_BYTES. `len(fps or [])` would turn "too big to
    # count" into "0 turns" - a false assertion, and precisely on the large
    # conversations most worth recovering. Unmeasured stays unmeasured, and
    # _synthesize_row omits the field rather than writing a number.
    try:
        fps = _message_fingerprints(path)
    except OSError as exc:
        raise Refusal("could not count the turns in {0}: {1}. Nothing was "
                      "written.".format(session_id, exc))
    # Stat AFTER both reads and compare against the stat taken BEFORE them. The
    # apply-time check re-stats this file and compares, so whatever is recorded
    # here becomes the definition of "unchanged" - and a stat taken only at the
    # end would happily record the state produced by an append that happened
    # DURING the read, baptising a half-read file as the baseline. If the two
    # stats disagree the file moved under us and there is nothing to record.
    try:
        after = os.stat(path)
    except OSError as exc:
        raise Refusal("could not stat the transcript for {0}: {1}. Nothing was "
                      "written.".format(session_id, exc))
    if (after.st_size, int(after.st_mtime)) != (before.st_size,
                                                int(before.st_mtime)):
        raise Refusal(
            "the transcript for {0} was being written while this read it, so the "
            "facts gathered describe no single version of the file. Nothing was "
            "written - re-run once the session is idle.".format(session_id))
    return {"path": path, "cwd": cwd, "created_ms": first_ms, "last_ms": last_ms,
            "turns": len(fps) if fps is not None else None,
            "custom_title": custom, "model": model, "effort": effort,
            "size": after.st_size, "mtime": int(after.st_mtime)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python tools/check_new_row.py`
Expected: PASS — eight checks `OK`, ending `ALL PASS`

- [ ] **Step 5: Add the template and its golden test**

Append to `tools/check_new_row.py` before the final `print()` block:

```python
# ------------------------------------------------------------ the row template
print("\n--- the synthesized row ---")

root, env, live, dorm = build([OPENER] + prose(8))
f = ccs._transcript_facts(env, SID)
row = ccs._synthesize_row(SID, "A title", "user", f, "u" * 36)

# The expected field set is written HERE, not read from the template. A golden
# test that reads its expectations from the thing it checks passes by
# construction and cannot tell "correctly added" from "accidentally added".
EXPECTED = {
    "sessionId": "local_" + "u" * 36,
    "cliSessionId": SID,
    "title": "A title",
    "titleSource": "user",
    "cwd": r"C:\Users\craig\Projects\Personal",
    "originCwd": r"C:\Users\craig\Projects\Personal",
    "createdAt": ccs._iso_ms("2026-06-14T09:00:00.000Z"),
    "lastActivityAt": ccs._iso_ms("2026-06-14T10:08:00.000Z"),
    "lastFocusedAt": ccs._iso_ms("2026-06-14T10:08:00.000Z"),
    "completedTurns": 9,
    "model": "claude-fable-5",
    "effort": "xhigh",
    "isArchived": False,
    "alwaysAllowedReasons": [],
    "sessionPermissionUpdates": [],
    "spawnSeed": {},
    "chromePermissionMode": None,
    "permissionMode": "auto",
}
check("the row has exactly the expected fields, no more and no fewer",
      set(row) == set(EXPECTED),
      "extra=%s missing=%s" % (sorted(set(row) - set(EXPECTED)),
                               sorted(set(EXPECTED) - set(row))))
for k in sorted(EXPECTED):
    if k in row:
        check("  %s" % k, row[k] == EXPECTED[k], "%r != %r" % (row.get(k), EXPECTED[k]))

# Fields the policy excludes. The first four an earlier draft asserted on every
# row; measured across 987 real rows they appear on 60.2%, 5.6%, 2.7% and 0.9%.
# classifierSummaryEnabled is the interesting one: it CLEARS the 95% bar at
# 97.6% and is still omitted, because True is a behavioural setting rather than
# an absence - a threshold alone would have let it through.
for absent in ("reportFindingsCard", "chromeTabGroupId", "lastSpawnRootDetected",
               "remoteControlAutoEligible", "classifierSummaryEnabled"):
    check("  %s is omitted - the policy does not support it" % absent,
          absent not in row)
check("spawnedFrom is absent - the row claims no lineage it does not have",
      "spawnedFrom" not in row)
check("lastFocusedAt is seeded, not omitted - the app rewrites it on first focus",
      row["lastFocusedAt"] == row["lastActivityAt"])
check("the row serializes to JSON", isinstance(
    json.dumps(row, separators=(",", ":")), str))

# titleSource must be TRUE, not decorative. 533 of 537 real rows carrying it say
# 'auto'; only 4 say 'user'. Writing 'user' on a machine-made placeholder would
# tell the app - and the next reader of the file - that a person chose it.
auto = ccs._synthesize_row(SID, "(untitled - ...)", "auto", f, "v" * 36)
check("a derived title records titleSource 'auto', not 'user'",
      auto["titleSource"] == "auto", auto["titleSource"])

# An uncountable transcript omits completedTurns rather than writing 0.
unc = ccs._synthesize_row(SID, "A title", "user", dict(f, turns=None), "w" * 36)
check("an uncountable transcript omits completedTurns entirely",
      "completedTurns" not in unc, str(unc.get("completedTurns")))
shutil.rmtree(root, ignore_errors=True)
```

- [ ] **Step 6: Run it, watch it fail, then implement**

Run: `python tools/check_new_row.py`
Expected: FAIL — `AttributeError: ... '_synthesize_row'`

Add to `claude_code_sessions.py`, after `_transcript_facts`:

```python
# The static half of a synthesized row: every field that comes neither from the
# transcript nor from the caller. One place, one comment per field.
#
# EVERY MEMBER EARNED ITS PLACE IN A CENSUS, not in a design meeting. Measured
# 2026-08-22 across 987 real rows: 52 distinct keys exist and only 12 appear on
# all of them, so there is no fixed row shape to copy. A field belongs here only
# if it is effectively universal AND has a defensible zero value.
#
# What that ruled OUT is the point: an earlier draft asserted reportFindingsCard
# (present on 60.2% of real rows), chromeTabGroupId (5.6%), lastSpawnRootDetected
# (2.7%) and remoteControlAutoEligible (0.9%) on every synthesized row. Writing a
# field that 99.1% of real rows do not carry is the "plausible-looking default"
# this comment exists to forbid. Re-run the census in the plan before adding one.
NEW_ROW_DEFAULTS = {
    "isArchived": False,              # 100% of rows; a recovered row is not archived
    "alwaysAllowedReasons": [],       # 100%; no permission history to inherit
    "sessionPermissionUpdates": [],   # 99.7%; ditto
    "spawnSeed": {},                  # 95.7%; not spawned from anything
    "chromePermissionMode": None,     # 100%; None is the plurality - no Chrome state
    # 100% of rows. Three values observed: auto (768), bypassPermissions (201),
    # acceptEdits (18). 'auto' is chosen because it is the most RESTRICTIVE of
    # the three, not because it is the most common - a synthesized row must
    # never hand a resumed session more permission than it had.
    "permissionMode": "auto",
}


def _synthesize_row(session_id, title, title_source, facts, row_uuid):
    """A complete listing row, built from a template plus transcript facts.

    NEVER cloned from a sibling row. The 2026-08-22 prototype cloned one and had
    to strip a `spawnedFrom` asserting descent from a task that never happened;
    cloning also inherits permission modes, MCP configuration, Chrome tab state
    and worktree paths, none of which a new row has any business asserting.

    `lastFocusedAt` is seeded with the transcript's last activity rather than
    omitted. Measured on the prototype: the app REWRITES this field when the row
    is first focused, so seeding is transient - and omitting it risks the app
    sorting a recovered row to the bottom of the sidebar, where a user concludes
    the command failed.
    """
    row = dict(NEW_ROW_DEFAULTS)
    row.update({
        "sessionId": "local_" + row_uuid,
        "cliSessionId": session_id,
        "title": title,
        "titleSource": title_source,
        "cwd": facts["cwd"],
        "originCwd": facts["cwd"],
        "createdAt": facts["created_ms"],
        "lastActivityAt": facts["last_ms"],
        "lastFocusedAt": facts["last_ms"],
        "model": facts["model"],
    })
    # Both omitted rather than defaulted when the transcript could not settle
    # them. effort is absent from 0.6% of real rows, so its absence is a shape
    # the app already tolerates; completedTurns is absent whenever the
    # transcript was too big to count.
    if facts.get("effort"):
        row["effort"] = facts["effort"]
    if facts.get("turns") is not None:
        row["completedTurns"] = facts["turns"]
    return row
```

- [ ] **Step 7: Run to verify it passes**

Run: `python tools/check_new_row.py`
Expected: PASS, `ALL PASS`

- [ ] **Step 8: Run the whole suite**

```bash
for t in tools/check_*.py; do python "$t" >/dev/null 2>&1 && echo "PASS $t" || echo "FAIL $t"; done
```

Expected: thirteen lines, every one `PASS`

- [ ] **Step 9: Commit**

```bash
git add claude_code_sessions.py tools/check_new_row.py
git commit -m "feat(new-row): field census, transcript facts, row template

The template is built from a census of 987 real rows, not from a guess. 52
distinct keys exist across them and only 12 appear on all of them, so there
is no fixed row shape - a field belongs in the template only if it is
effectively universal and has a defensible zero value.

That ruled out four fields an earlier draft asserted on every synthesized
row: reportFindingsCard (60.2% of real rows), chromeTabGroupId (5.6%),
lastSpawnRootDetected (2.7%) and remoteControlAutoEligible (0.9%). Writing
a field that 99.1% of rows do not carry is the plausible-looking default
the template exists to forbid.

model and effort are DERIVED from the transcript rather than defaulted. The
same draft hardcoded claude-opus-5 as 'the account default'; the census says
claude-fable-5 leads 522 to 243. The transcript records both, so neither
needs guessing.

titleSource is now truthful - 'user' only for --title, 'auto' otherwise.
533 of 537 real rows carrying it say 'auto'.

An uncountable transcript omits completedTurns instead of writing 0:
_message_fingerprints returns None over its size cap, and len(None or [])
would assert 'zero turns' about exactly the large conversations most worth
recovering."
```

---

### Task 2: Title derivation, provenance, and collision

**Files:**
- Modify: `claude_code_sessions.py` (after `_synthesize_row`)
- Test: `tools/check_new_row.py`

**Interfaces:**
- Consumes: `_transcript_facts`
- Produces:
  - `_placeholder_title(facts) -> str`
  - `_new_row_title(explicit, facts) -> (title, provenance, title_source)` — `provenance` is `"yours"` / `"the transcript's custom title"` / `"placeholder"`; `title_source` is `"user"` or `"auto"`
  - `_unique_title(title, existing, generated) -> str`

- [ ] **Step 1: Write the failing test**

Append to `tools/check_new_row.py`:

```python
# ------------------------------------------------------------ titles
print("\n--- title derivation ---")

FACTS = {"cwd": r"C:\Users\craig\Projects\Personal", "created_ms": 0,
         "last_ms": ccs._iso_ms("2026-06-14T10:08:00.000Z"), "turns": 181,
         "custom_title": None, "path": "x", "model": "claude-fable-5",
         "effort": "xhigh"}
check("the placeholder names date, turns and the LAST path component",
      ccs._placeholder_title(FACTS) == "(untitled - 2026-06-14, 181 turns, Personal)",
      ccs._placeholder_title(FACTS))

# The date must be UTC, matching _iso_ms. With localtime, this exact fact dict
# renders 2026-06-14 in Seattle and 2026-06-15 in UTC+14 - so the assertion
# above would pass or fail depending on where the suite runs.
check("  and the date is UTC, so the title does not vary by machine timezone",
      "2026-06-14" in ccs._placeholder_title(
          dict(FACTS, last_ms=ccs._iso_ms("2026-06-14T23:30:00.000Z"))),
      ccs._placeholder_title(dict(FACTS, last_ms=ccs._iso_ms("2026-06-14T23:30:00.000Z"))))

ROOT_FACTS = dict(FACTS, cwd="C:\\")
check("a root cwd drops the component instead of dangling a comma",
      ccs._placeholder_title(ROOT_FACTS) == "(untitled - 2026-06-14, 181 turns)",
      ccs._placeholder_title(ROOT_FACTS))
check("  a POSIX root likewise",
      ccs._placeholder_title(dict(FACTS, cwd="/"))
      == "(untitled - 2026-06-14, 181 turns)",
      ccs._placeholder_title(dict(FACTS, cwd="/")))
check("  and a non-C drive letter is not mistaken for a folder name",
      ccs._placeholder_title(dict(FACTS, cwd="D:\\"))
      == "(untitled - 2026-06-14, 181 turns)",
      ccs._placeholder_title(dict(FACTS, cwd="D:\\")))
check("  while a UNC share IS a real leaf and is kept",
      ccs._placeholder_title(dict(FACTS, cwd="\\\\server\\share"))
      == "(untitled - 2026-06-14, 181 turns, share)",
      ccs._placeholder_title(dict(FACTS, cwd="\\\\server\\share")))

# The cwd comes out of a transcript, not off this filesystem. os.path.basename
# on a POSIX host finds no '/' in a Windows path and returns the whole thing,
# so this assertion is what keeps the suite honest when it runs on macOS.
check("a Windows cwd yields its leaf on ANY host, not the whole path",
      ccs._placeholder_title(FACTS).endswith("Personal)"),
      ccs._placeholder_title(FACTS))
check("  and a POSIX cwd works the same way",
      ccs._placeholder_title(dict(FACTS, cwd="/home/craig/Projects/Personal"))
      == "(untitled - 2026-06-14, 181 turns, Personal)",
      ccs._placeholder_title(dict(FACTS, cwd="/home/craig/Projects/Personal")))

check("an uncountable transcript says so rather than claiming a number",
      ccs._placeholder_title(dict(FACTS, turns=None))
      == "(untitled - 2026-06-14, turns not counted, Personal)",
      ccs._placeholder_title(dict(FACTS, turns=None)))

check("--title wins, is reported as yours, and records titleSource user",
      ccs._new_row_title("Mine", FACTS) == ("Mine", "yours", "user"))
check("customTitle is used when there is no --title, and records auto",
      ccs._new_row_title("", dict(FACTS, custom_title="Theirs"))
      == ("Theirs", "the transcript's custom title", "auto"))
check("a whitespace-only --title is treated as absent, not as a title",
      ccs._new_row_title("   ", FACTS)[2] == "auto",
      str(ccs._new_row_title("   ", FACTS)))
check("otherwise the placeholder, reported as such",
      ccs._new_row_title("", FACTS)[1] == "placeholder")

existing = {"Mine", "Mine (2)", "Theirs"}
check("a user-supplied duplicate is left alone",
      ccs._unique_title("Mine", existing, generated=False) == "Mine")
check("a generated duplicate is suffixed past every taken variant",
      ccs._unique_title("Mine", existing, generated=True) == "Mine (3)",
      ccs._unique_title("Mine", existing, generated=True))
check("  and an untaken generated title is untouched",
      ccs._unique_title("Fresh", existing, generated=True) == "Fresh")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tools/check_new_row.py`
Expected: FAIL — `AttributeError: ... '_placeholder_title'`

- [ ] **Step 3: Write minimal implementation**

```python
def _placeholder_title(facts):
    """A title that does NOT impersonate a summary.

    Used for the orphaned conversations that carry no customTitle of their own -
    120 of 170 on this machine, measured 2026-08-23 by the orphan query in the
    plan's Task 1. Expect the count to drift: it moved 169 -> 170 in one day
    simply because a new session was created, so treat it as a magnitude rather
    than a constant. The app's titles are model-written summaries, so
    nothing mechanical can produce one - and a machine-made title that LOOKS
    like a summary is the failure this whole command is careful about. This one
    is identifying, sortable, and visibly not a summary.

    UTC, matching _iso_ms. time.localtime here would render the same
    conversation as two different dates on two machines, and the row's own
    createdAt - which IS UTC - would disagree with its title.
    """
    day = time.strftime("%Y-%m-%d", time.gmtime((facts["last_ms"] or 0) / 1000.0))
    # Split on BOTH separators, never os.path.basename. The cwd comes out of a
    # transcript, not off this filesystem, so a store synced from Windows can
    # hand a POSIX machine `C:\Users\craig\Projects\Personal` - where basename
    # finds no `/`, returns the whole string, and the "placeholder" becomes an
    # absolute path. The test below pins a Windows cwd precisely so this stays
    # correct when the suite runs on macOS or Linux.
    #
    # A BARE DRIVE IS NOT A LEAF. `C:\` strips to `C:` and splits to `C:`, which
    # is truthy - so a plain split appends "C:" to the title where the intent is
    # to append nothing. ntpath.basename returned "" here and hid the case; the
    # cross-platform split exposes it, so it has to be handled rather than
    # inherited. Measured: C:\ -> 'C:', D:\ -> 'D:', / -> '', \\server\share ->
    # 'share' (a real leaf, correctly kept).
    leaf = re.split(r"[\\/]", (facts.get("cwd") or "").rstrip("\\/"))[-1]
    if re.match(r"^[A-Za-z]:$", leaf):
        leaf = ""
    turns = facts.get("turns")
    parts = [day, "{0} turns".format(turns) if turns is not None
             else "turns not counted"]
    if leaf:                       # a cwd of C:\ or / has no leaf - drop the
        parts.append(leaf)         # clause rather than dangle a comma
    return "(untitled - {0})".format(", ".join(parts))


def _new_row_title(explicit, facts):
    """(title, provenance, title_source).

    Provenance is printed, because the user's decision differs: a customTitle
    was written by a person about that conversation, and a placeholder is an
    admission that nothing was available.

    title_source is what goes IN the row, and it is a claim about authorship
    rather than a formatting detail. 533 of the 537 real rows carrying the field
    say 'auto'; writing 'user' on a machine-made placeholder would tell the app,
    and the next person to read the file, that someone chose it.
    """
    explicit = (explicit or "").strip()
    if explicit:
        return explicit, "yours", "user"
    if facts.get("custom_title"):
        return facts["custom_title"], "the transcript's custom title", "auto"
    return _placeholder_title(facts), "placeholder", "auto"


def _unique_title(title, existing, generated):
    """Suffix a GENERATED title until it is unique within the account.

    A user-supplied duplicate is allowed and merely reported - they asked for
    that exact string. A customTitle counts as generated even though a person
    wrote it: they wrote it for a different row and were never asked whether a
    duplicate here was acceptable, and silence is not consent to a string the
    user has not seen.
    """
    if not generated or title not in existing:
        return title
    n = 2
    while "{0} ({1})".format(title, n) in existing:
        n += 1
    return "{0} ({1})".format(title, n)
```

- [ ] **Step 4: Run to verify it passes**

Run: `python tools/check_new_row.py`
Expected: PASS

- [ ] **Step 5: Run the whole suite and commit**

```bash
for t in tools/check_*.py; do python "$t" >/dev/null 2>&1 && echo "PASS $t" || echo "FAIL $t"; done
git add claude_code_sessions.py tools/check_new_row.py
git commit -m "feat(new-row): title derivation with printed provenance

Three tiers - --title, the transcript's customTitle, then a placeholder
that does not impersonate a summary. Measured 2026-08-23: 120 of 170
orphans here have nothing to derive from, and the app's own titles are
model-written summaries, so a mechanical title that LOOKS like one is the
failure this command is careful about.

The placeholder date is UTC, matching _iso_ms. localtime would render the
same conversation as two dates on two machines and disagree with the row's
own createdAt.

Provenance is returned alongside the title and printed; title_source is
returned separately and written into the row, because 'a person chose this'
is a claim, not a formatting detail."
```

---

### Task 3: `plan_new_row`, store selection, and every refusal

**Files:**
- Modify: `claude_code_sessions.py` (after `_unique_title`)
- Test: `tools/check_new_row.py`

**Interfaces:**
- Consumes: `_repoint_store(env, flags)` — reused for `--store` / `--live` resolution. **It returns a LIST of `(acct, org, path)` tuples, not one**, and its docstring says so deliberately: an account owns one store per org, so naming it by email matches all of them, and `plan_repoint` lets the *row* settle which. Also `_listdir_or_refuse`, `read_json`, `_email_of`, `_transcript_facts`, `_new_row_title`, `_unique_title`, `_synthesize_row`, `b64`.
- Produces:
  - `_new_row_store(env, flags) -> (acct, org, path, why, heuristic)` — `why` is a human sentence naming how the store was chosen; `heuristic` is True when row counts broke a tie, and `--apply` refuses on it
  - `_row_already_opens(store, session_id) -> (name | None, titles set)` — **fails closed**
  - `NewRowFlags` dataclass: `to_session: str = ""`, `store: str = ""`, `title: str = ""`, `live: str = ""`
  - `plan_new_row(env, flags) -> manifest` with keys `op_type="new-row"`, `store_path`, `store_label`, `store_why`, `store_is_a_guess`, `store_org`, `name`, `row_path`, `title`, `title_provenance`, `title_collision`, `to_session`, `transcript`, `transcript_size`, `transcript_mtime`,
    `transcript_mb`, `turns`, `cwd`, `model`, `rows=[{name, dest_path, title, pre_b64: None, post_b64, is_update: False, written: False}]`

- [ ] **Step 1: Write the failing test**

Append to `tools/check_new_row.py`:

```python
# ------------------------------------------------------------ planning
print("\n--- plan_new_row ---")

root, env, live, dorm = build([OPENER] + prose(8))
m = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID, title="A title"))
check("the plan names the new-row op type", m["op_type"] == "new-row")
check("  targeting the signed-in store by default",
      os.path.realpath(m["store_path"]) == os.path.realpath(live), m["store_path"])
check("  and says HOW that store was chosen", bool(m.get("store_why")),
      str(m.get("store_why")))
check("  with exactly one row", len(m["rows"]) == 1)
check("  which is an ADD, not an update", m["rows"][0]["is_update"] is False)
check("  and carries no pre-image, because nothing existed",
      m["rows"][0]["pre_b64"] is None)
check("  the row filename matches the synthesized sessionId",
      json.loads(ccs.unb64(m["rows"][0]["post_b64"]).decode("utf-8"))["sessionId"]
      == "local_" + m["name"], m["name"])
check("  and nothing was written", not os.listdir(live))
shutil.rmtree(root, ignore_errors=True)

print("\n--- refusals ---")


def refusal(label, fn, needle):
    try:
        fn()
        check(label, False, "no refusal")
    except ccs.Refusal as exc:
        check(label, needle in str(exc), str(exc)[:90])


root, env, live, dorm = build([OPENER] + prose(8))
refusal("--to naming no transcript refuses",
        lambda: ccs.plan_new_row(env, ccs.NewRowFlags(to_session="%032d" % 99)),
        "no transcript on disk")
refusal("an empty --to refuses",
        lambda: ccs.plan_new_row(env, ccs.NewRowFlags(to_session="")),
        "--to is required")
with open(os.path.join(live, "local_existing.json"), "w") as fh:
    json.dump({"cliSessionId": SID, "title": "Already here", "cwd": "proj",
               "lastActivityAt": 1}, fh)
refusal("a conversation this account already reaches refuses",
        lambda: ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID)),
        "already opens")
shutil.rmtree(root, ignore_errors=True)

# An UNREADABLE row must fail closed. Skipping it means a row that already
# opens this conversation goes unseen and the command creates a duplicate -
# a fail-open in a module whose whole posture is fail-closed.
root, env, live, dorm = build([OPENER] + prose(8))
with open(os.path.join(live, "local_broken.json"), "w") as fh:
    fh.write("{not json at all")
refusal("an unreadable row in the store refuses rather than being skipped",
        lambda: ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID)),
        "could not be read")
shutil.rmtree(root, ignore_errors=True)

root, env, live, dorm = build([rec("user", "no cwd anywhere in this file at all")])
refusal("a transcript with no cwd refuses distinctly from unreadable",
        lambda: ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID)),
        "no cwd")
shutil.rmtree(root, ignore_errors=True)

# A conversation where nobody replied. Deliberately refused - model is on 100%
# of real rows and has no zero value - and the message must name THAT, not
# something vague, because it is the one refusal a user could reasonably
# disagree with.
root, env, live, dorm = build(
    [rec("user", "typed a prompt and closed the app before any reply",
         ts="2026-06-14T09:00:00.000Z", cwd=r"C:\Users\craig\Projects\Personal")])
refusal("a transcript with no assistant reply refuses, naming why",
        lambda: ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID)),
        "no assistant reply")
shutil.rmtree(root, ignore_errors=True)

# the cross-pair: one account, two org directories, only one of them real
root, env, live, dorm = build([OPENER] + prose(8))
os.makedirs(os.path.join(os.path.dirname(live), ORG_D))    # empty scaffolding
with open(os.path.join(live, "local_anything.json"), "w") as fh:
    json.dump({"cliSessionId": "%032d" % 55, "title": "Something else",
               "cwd": "proj", "lastActivityAt": 1}, fh)
# The ACCOUNT id matches both of that account's org dirs - which is exactly
# the tie _new_row_store exists to break. Deliberately not the account's
# email: _repoint_store matches email through account_email, whose docstring
# says it resolves a DORMANT account, so the LIVE account's own email (which
# lives in ~/.claude.json and only live_account reads) matches nothing. That
# is a real gap in shipped `repoint`, filed separately - do not paper over it
# by teaching this test to use whichever identifier happens to work.
m = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID, store=LIVE))
check("an account id matching two org dirs picks the one holding rows",
      os.path.realpath(m["store_path"]) == os.path.realpath(live), m["store_path"])
check("  and says so, because a heuristic the user cannot see is a trap",
      "rows" in m["store_why"], m["store_why"])
check("  marking the choice as a guess", m["store_is_a_guess"] is True)
check("  and naming the org that would settle it", m["store_org"] == ORG_L,
      str(m.get("store_org")))
# the ORG id narrows to one directory, so no tie and no guess
m2 = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID, store=ORG_L))
check("  while naming the org id is not a guess",
      m2["store_is_a_guess"] is False)
check("  and planning wrote nothing either way",
      sorted(os.listdir(live)) == ["local_anything.json"], str(os.listdir(live)))
shutil.rmtree(root, ignore_errors=True)

# NOTE: that the guess may plan and may NOT write is asserted in Task 6, where
# `cmd_new_row` exists to enforce it. Do not test it here - `cmd_new_row` and
# `run_new_row` are introduced in Tasks 6 and 4, and calling them from this
# task's suite would raise AttributeError rather than fail a check.

# every candidate empty: name them rather than saying 'no way to tell'
root, env, live, dorm = build([OPENER] + prose(8))
os.makedirs(os.path.join(os.path.dirname(live), ORG_D))
try:
    ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID, store=LIVE))
    check("all-empty candidates refuse by naming them", False, "no refusal")
except ccs.Refusal as exc:
    check("all-empty candidates refuse by naming them", ORG_D in str(exc),
          str(exc)[:130])
    check("  naming ORG IDS, not paths - a Windows path pasted back would not "
          "match", "organization ids" in str(exc), str(exc)[:130])
shutil.rmtree(root, ignore_errors=True)

# zero prose turns is ALLOWED - a policy choice must not masquerade as a
# technical constraint. completedTurns takes 0 and the placeholder renders it.
root, env, live, dorm = build(
    [rec("system", None, ts="2026-06-14T09:00:00.000Z",
         cwd=r"C:\Users\craig\Projects\Personal", model="claude-fable-5")])
m = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID))
check("a transcript with zero prose turns is allowed", m["turns"] == 0)
check("  and its placeholder says so", "0 turns" in m["title"], m["title"])
shutil.rmtree(root, ignore_errors=True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tools/check_new_row.py`
Expected: FAIL — `AttributeError: ... 'NewRowFlags'`

- [ ] **Step 3: Write minimal implementation**

Add `import uuid` on the line above `_new_row_store`, following Task 1's placement convention.

```python
def _new_row_store(env, flags):
    """(acct, org, path, why, heuristic) - exactly one store, and how it was
    chosen.

    `_repoint_store` returns a LIST, and refusing whenever it returns more than
    one would refuse the ordinary case: an account owns one directory per org, so
    naming it by email matches all of them. `plan_repoint` resolves that by
    letting the ROW settle which store is meant - a luxury this command does not
    have, because the row is what it is about to create.

    So let the CONTENT settle it: the cross-pair directories are empty
    scaffolding, and the account's real store is the one with rows in it.

    Know what that heuristic costs, because it is a guess and not a fact: a
    genuinely new or deliberately empty organization loses to an old populated
    one, and this function would pick the wrong store confidently.

    So the guess is allowed to PLAN and not to WRITE. `heuristic` comes back
    True whenever row counts broke the tie, `plan_new_row` carries it into the
    manifest, and `cmd_new_row` refuses `--apply` on it, naming the
    ORGANIZATION ID that would settle it - never a path, because _repoint_store
    forward-slashes the candidate and not the argument, so a Windows path
    pasted back from this tool matches nothing. An earlier draft relied on printing
    the reasoning before the write instead - which is not a safeguard at all in
    a one-shot `--apply`, because there is no moment between the print and the
    write in which anyone can intervene, and no saved plan to approve. A dry run
    that shows the guess and an apply that refuses to act on it are two
    different promises; only the second one holds unattended.
    """
    hits = _repoint_store(env, flags)
    if len(hits) == 1:
        a, o, p = hits[0]
        return a, o, p, "the only store matching what you named", False
    populated = []
    for a, o, p in hits:
        rows = [n for n in _listdir_or_refuse(p, "an account directory")
                if n.startswith("local_") and n.endswith(".json")]
        if rows:
            populated.append((a, o, p, len(rows)))
    # Every refusal below tells the user to narrow by ORGANIZATION ID, never
    # "name it by path". Measured 2026-08-23 against shipped code: _repoint_store
    # compares `flags.store.lower()` against `os.path.normcase(p).replace("\\","/")`,
    # so the candidate path is lower-cased AND forward-slashed while the user's
    # argument is only lower-cased - a Windows path pasted from this very listing
    # matches nothing. Advice that fails when followed is worse than no advice,
    # and the org id is the thing that actually distinguishes these directories.
    listing = "\n".join("   org {0}   {1}".format(o, p) for _, o, p in hits)
    if not populated:
        raise Refusal(
            "--store {0!r} matched {1} directories and none of them holds any "
            "rows, so nothing distinguishes them. Re-run naming one of these "
            "organization ids with --store:\n{2}"
            .format(flags.store, len(hits), listing))
    if len(populated) > 1:
        raise Refusal(
            "--store {0!r} matched {1} directories that each hold rows; refusing "
            "to guess which should get the new one. Re-run naming one of these "
            "organization ids with --store:\n{2}".format(
                flags.store, len(populated),
                "\n".join("   org {0}   {1}  ({2} rows)".format(o, p, n)
                          for _, o, p, n in populated)))
    a, o, p, n = populated[0]
    return a, o, p, ("the only one of {0} matching directories that holds any "
                     "rows ({1} of them)".format(len(hits), n)), True


def _row_already_opens(store, session_id, exclude_name=None):
    """(name of a row opening session_id or None, every title in the store).

    `exclude_name` skips one filename entirely - the row THIS op wrote. Both
    answers need it once an op can re-enter after a crash: the reachability
    hit would be our own row, and so would the title. Excluding it in one
    place beats two separate exemptions at the call sites, which is what an
    earlier draft had - and it had the exemption on the reachability check
    only, so `recover --forward` on a written-then-drifted row refused
    against its own title with "it appeared since this was planned".

    FAILS CLOSED. An earlier draft skipped rows it could not parse, which meant
    an unreadable row pointing at this conversation went unseen and the command
    created a second door to it - a fail-open in a module whose entire posture is
    that "couldn't look" is never "nothing there".
    """
    titles = set()
    hit = None
    for name in sorted(_listdir_or_refuse(store, "the store")):
        if not (name.startswith("local_") and name.endswith(".json")):
            continue
        if exclude_name and name == exclude_name:
            continue
        try:
            # read_json already converts ValueError to LayoutError, so that arm
            # is unreachable today. It stays as defence against a future
            # read_json that stops converting - this is a fail-closed path, and
            # the cost of an unreachable except clause is a comment.
            d = read_json(os.path.join(store, name))
        except (LayoutError, OSError, ValueError) as exc:
            raise Refusal(
                "the row {0!r} in this store could not be read ({1}), so this "
                "command cannot tell whether it already opens {2}. Refusing "
                "rather than risk a second row for the same conversation. "
                "Nothing was written. Open that file and repair or remove it - "
                "'doctor' reports rows it cannot parse - then re-run."
                .format(name, exc, session_id[:8]))
        if not isinstance(d, dict):
            raise Refusal("the row {0!r} is not a JSON object; refusing to add "
                          "a row beside it. Nothing was written. Open that file "
                          "and repair or remove it, then re-run.".format(name))
        if d.get("cliSessionId") == session_id:
            hit = name
        if isinstance(d.get("title"), str):
            titles.add(d["title"])
    return hit, titles


@dataclasses.dataclass
class NewRowFlags:
    """Which conversation to surface, in which account, under what name."""
    to_session: str = ""    # the cliSessionId the new row should open
    store: str = ""         # substring naming the store; default = the live one
    title: str = ""         # explicit title; otherwise derived
    live: str = ""          # RULING 5 assertion, as sync and repoint use it


def plan_new_row(env, flags):
    """Build a new-row manifest. Pure planning - writes nothing.

    Creates a row where none existed, which is the one thing `repoint`, `sync`
    and `move` all need to already have been done for them. Measured 2026-08-22:
    170 transcripts on one machine were reachable from no row in any account.
    """
    if not flags.to_session:
        raise Refusal("--to is required: the cliSessionId the new row should open. "
                      "'doctor' lists conversations that no account points at.")
    acct, org, store, why, heuristic = _new_row_store(env, flags)
    label = "{0} ({1}{2})".format(_email_of(env, acct) or acct[:8], acct[:8],
                                  "/" + org[:8] if org else "")
    facts = _transcript_facts(env, flags.to_session)

    # Within ONE account. Several accounts each holding a row for the same
    # conversation is the normal and desirable state; two rows in one sidebar
    # opening the same conversation is the clutter this tool spent a day
    # removing. This is re-checked under the lock in execute_new_row_op - the
    # check here is for the dry run's benefit, and is not the guard.
    hit, existing_titles = _row_already_opens(store, flags.to_session)
    if hit:
        raise Refusal(
            "{0} already opens {1} in this account (row {2!r}), so a new row "
            "would be a second door to the same conversation. Nothing was "
            "written. If that row is the problem - it opens the right "
            "conversation under the wrong title, say - edit it rather than "
            "adding another.".format(label, flags.to_session[:8], hit))

    title, provenance, title_source = _new_row_title(flags.title, facts)
    collision = title if title in existing_titles else None
    title = _unique_title(title, existing_titles, generated=provenance != "yours")

    # The uuid is minted HERE so that everything this manifest reports - the
    # filename, the sessionId inside the row - is internally consistent, and so
    # `--apply --json` reports the id it actually wrote. It is NOT a promise
    # that a later, separate `--apply` run reuses it: there is no way to hand a
    # saved manifest back to the CLI, so a second invocation replans and mints
    # a new one. That is fine; nothing keys on the value.
    row_uuid = str(uuid.uuid4())
    row = _synthesize_row(flags.to_session, title, title_source, facts, row_uuid)
    name = "local_{0}.json".format(row_uuid)
    post = json.dumps(row, separators=(",", ":")).encode("utf-8")
    return {"op_type": "new-row", "store_path": store, "store_label": label,
            "store_why": why, "store_is_a_guess": heuristic,
            "store_org": org, "name": row_uuid,
            "row_path": os.path.join(store, name),
            "title": title, "title_provenance": provenance,
            "title_collision": collision,
            "to_session": flags.to_session, "transcript": facts["path"],
            # The snapshot marker _new_row_preflight compares against - taken
            # from `facts`, NOT re-stat'd here. Re-stat'ing would capture the
            # file as it is now rather than as it was when its facts were read,
            # so an append between the two would silently become the accepted
            # baseline and the very drift this is meant to catch would validate.
            "transcript_size": facts["size"],
            "transcript_mtime": facts["mtime"],
            # Derived from the SAME snapshot, not a fresh stat. A second
            # os.path.getsize here would undo the two lines above it: real
            # I/O happens in between (_row_already_opens scans every row in
            # the store), so the size reported could disagree with the size
            # validated - and a file deleted in that window raises a bare
            # OSError, which main() does not catch and which would surface as
            # an unredacted traceback carrying paths and account uuids.
            "transcript_mb": round(facts["size"] / 1e6, 1),
            "turns": facts["turns"], "cwd": facts["cwd"], "model": facts["model"],
            "rows": [{"name": name, "dest_path": os.path.join(store, name),
                      "title": title, "pre_b64": None, "post_b64": b64(post),
                      "is_update": False, "written": False}]}
```

- [ ] **Step 4: Run to verify it passes**

Run: `python tools/check_new_row.py`
Expected: PASS

- [ ] **Step 5: Run the whole suite and commit**

```bash
for t in tools/check_*.py; do python "$t" >/dev/null 2>&1 && echo "PASS $t" || echo "FAIL $t"; done
git add claude_code_sessions.py tools/check_new_row.py
git commit -m "feat(new-row): plan_new_row, store selection, and refusals

The reachability check FAILS CLOSED. An earlier draft skipped rows it could
not parse, so an unreadable row already opening the conversation went unseen
and the command would create a second door to it - a fail-open in a module
whose whole posture is that 'couldn't look' is never 'nothing there'.

Store selection returns its reasoning as well as its answer. Picking the
populated directory out of an account's cross-pair is a guess: a genuinely
new or deliberately empty org loses to an old populated one. It refuses when
more than one candidate holds rows, names all candidates when none does, and
the dry-run report prints which store it chose and why - a heuristic the
user can see is a different thing from one they cannot.

Zero prose turns is explicitly ALLOWED; an earlier draft refused it, which
dressed a policy choice as a technical constraint.

Corrected the duplicate-row refusal, which used to advise 'repoint' - the
one command that cannot help, since by construction the row already points
exactly where the user asked."
```

---

### Task 4: Execute, run, and undo-by-deletion

**Files:**
- Modify: `claude_code_sessions.py` (after `plan_new_row`)
- Test: `tools/check_new_row.py`

**Interfaces:**
- Consumes: `acquire_lock`, `release_lock`, `new_op`, `set_status`, `save_manifest`, `rotate_ops`, `_guard_mutation`, `ensure_contained`, `atomic_write`, `unb64`, `_maybe_crash`, `find_transcripts`, `_row_already_opens`, `_sync_row_drift`
- Produces: `execute_new_row_op(env, op) -> "completed"`, `run_new_row(env, manifest) -> str`, `undo_new_row(env, op) -> "undone"`

- [ ] **Step 1: Write the failing test**

Append to `tools/check_new_row.py`:

```python
# ------------------------------------------------------------ apply and undo
print("\n--- apply, and undo by deletion ---")

root, env, live, dorm = build([OPENER] + prose(8))
m = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID, title="Recovered"))
check("apply completes", ccs.run_new_row(env, m) == "completed")
path = m["rows"][0]["dest_path"]
check("  the row exists on disk", os.path.exists(path))
written = json.load(open(path, encoding="utf-8"))
check("  opening the right conversation", written["cliSessionId"] == SID)
check("  under the title the plan showed", written["title"] == "Recovered")
check("  and the manifest carries an op_id for undo --id", bool(m.get("op_id")))
check("  the lock was released", not os.path.exists(ccs._lock_path(env)))

op = [o for o in ccs.list_ops(env) if o.manifest.get("op_id") == m["op_id"]][0]
check("undo deletes the row", ccs.undo_new_row(env, op) == "undone")
check("  the file is gone", not os.path.exists(path))
check("  and the transcript is untouched", os.path.exists(m["transcript"]))
shutil.rmtree(root, ignore_errors=True)

# The TOCTOU the lock exists to close: plan runs unlocked, so another writer
# can create a row for this conversation between plan and apply.
root, env, live, dorm = build([OPENER] + prose(8))
m = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID, title="Recovered"))
with open(os.path.join(live, "local_sneaked.json"), "w") as fh:
    json.dump({"cliSessionId": SID, "title": "Got there first", "cwd": "proj",
               "lastActivityAt": 1}, fh)
refusal("apply re-checks reachability under the lock and refuses",
        lambda: ccs.run_new_row(env, m), "already opens")
check("  writing nothing", not os.path.exists(m["rows"][0]["dest_path"]))
check("  and releasing the lock", not os.path.exists(ccs._lock_path(env)))
shutil.rmtree(root, ignore_errors=True)

# The transcript must still be the SAME single transcript, not merely some
# transcript: a duplicate appearing in another project folder between plan and
# apply would make the planned row's facts describe a file it no longer names.
root, env, live, dorm = build([OPENER] + prose(8))
m = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID, title="Recovered"))
other = os.path.join(env.projects_root, "other")
os.makedirs(other)
shutil.copy(m["transcript"], os.path.join(other, SID + ".jsonl"))
refusal("apply refuses when the transcript is no longer uniquely located",
        lambda: ccs.run_new_row(env, m), "more than one")
shutil.rmtree(root, ignore_errors=True)

root, env, live, dorm = build([OPENER] + prose(8))
m = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID, title="Recovered"))
ccs.run_new_row(env, m)
os.unlink(m["transcript"])
refusal("apply refuses once the transcript is gone",
        lambda: ccs.run_new_row(env, ccs.plan_new_row(
            env, ccs.NewRowFlags(to_session=SID, store=DORM))),
        "no transcript on disk")
shutil.rmtree(root, ignore_errors=True)

# Same path, different bytes. Every fact in the post-image came out of this
# file, so an append between plan and apply leaves the row's timestamps and
# turn count describing a version that no longer exists. A path-only check
# cannot see this.
root, env, live, dorm = build([OPENER] + prose(8))
m = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID, title="Recovered"))
with open(m["transcript"], "a", encoding="utf-8") as fh:
    fh.write(rec("user", "a message appended after the plan was built",
                 ts="2026-06-14T11:00:00.000Z") + "\n")
os.utime(m["transcript"], (m["transcript_mtime"] + 60,
                           m["transcript_mtime"] + 60))
refusal("apply refuses when the transcript changed at the SAME path",
        lambda: ccs.run_new_row(env, m), "has changed since this was planned")
check("  writing nothing", not os.path.exists(m["rows"][0]["dest_path"]))
shutil.rmtree(root, ignore_errors=True)

# A safe refusal must not manufacture cleanup work. Journalling before the
# preflight left a non-terminal op behind, so doctor told the user to run
# 'recover' over a command that had touched nothing at all.
root, env, live, dorm = build([OPENER] + prose(8))
m = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID, title="Recovered"))
with open(os.path.join(live, "local_sneaked2.json"), "w") as fh:
    json.dump({"cliSessionId": SID, "title": "Got there first", "cwd": "proj",
               "lastActivityAt": 1}, fh)
refusal("a pre-write refusal still refuses",
        lambda: ccs.run_new_row(env, m), "already opens")
check("  and leaves NO unresolved operation behind",
      not ccs.nonterminal_ops(env), str([o.manifest.get("op_id")
                                         for o in ccs.nonterminal_ops(env)]))
check("  so doctor does not ask for a recover that has nothing to do",
      not ccs.gather_doctor(env)["nonterminal_ops"])
shutil.rmtree(root, ignore_errors=True)

# The generated title was suffixed past everything that existed at PLAN time.
# A row created since can hold that suffix, and the preflight re-reads the
# title set rather than discarding it.
root, env, live, dorm = build(
    [rec("user", "opening message long enough to count",
         ts="2026-06-14T09:00:00.000Z", cwd=r"C:\Users\craig\Projects\Personal",
         custom="Shared name")] + prose(8))
m = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID))
check("the generated title was taken from the transcript", m["title"] == "Shared name")
with open(os.path.join(live, "local_racer.json"), "w") as fh:
    json.dump({"cliSessionId": "%032d" % 88, "title": "Shared name",
               "cwd": "proj", "lastActivityAt": 1}, fh)
refusal("a generated title claimed since planning refuses under the lock",
        lambda: ccs.run_new_row(env, m), "chosen to be unique")
shutil.rmtree(root, ignore_errors=True)

# An op must never collide with ITSELF. After a crash the row can already be on
# disk when the preflight runs again, and both of its checks - reachability and
# title uniqueness - would otherwise fire against the very row this op wrote.
# The reachability half was exempted from the start; the title half was not, so
# `recover --forward` on a written-then-drifted row refused with "another row is
# now called X - it appeared since this was planned" about its own row.
root, env, live, dorm = build(
    [rec("user", "opening message long enough to count",
         ts="2026-06-14T09:00:00.000Z", cwd=r"C:\Users\craig\Projects\Personal",
         custom="Self collision")] + prose(8))
m = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID))
ccs.run_new_row(env, m)
d = json.load(open(m["rows"][0]["dest_path"], encoding="utf-8"))
d["lastFocusedAt"] = 4242                      # as the app does on first focus
with open(m["rows"][0]["dest_path"], "w") as fh:
    json.dump(d, fh)
try:
    ccs._new_row_preflight(env, m)
    check("the preflight does not refuse against the op's own row", True)
except ccs.Refusal as exc:
    check("the preflight does not refuse against the op's own row", False,
          str(exc)[:100])
# and the exclusion is surgical - a DIFFERENT row with that title still counts
with open(os.path.join(live, "local_other.json"), "w") as fh:
    json.dump({"cliSessionId": "%032d" % 77, "title": "Self collision",
               "cwd": "proj", "lastActivityAt": 1}, fh)
refusal("  while a different row taking the title still refuses",
        lambda: ccs._new_row_preflight(env, m), "chosen to be unique")
shutil.rmtree(root, ignore_errors=True)

# ...but an explicit --title duplicate was the user's own call and still writes.
root, env, live, dorm = build([OPENER] + prose(8))
m = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID, title="Mine"))
with open(os.path.join(live, "local_racer.json"), "w") as fh:
    json.dump({"cliSessionId": "%032d" % 88, "title": "Mine", "cwd": "proj",
               "lastActivityAt": 1}, fh)
check("an explicit --title duplicate is still written - the user asked for it",
      ccs.run_new_row(env, m) == "completed")
shutil.rmtree(root, ignore_errors=True)

# undo refuses once the row has drifted - the app rewrites rows it opens
root, env, live, dorm = build([OPENER] + prose(8))
m = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID, title="Recovered"))
ccs.run_new_row(env, m)
path = m["rows"][0]["dest_path"]
d = json.load(open(path, encoding="utf-8"))
d["lastFocusedAt"] = 999                      # as the app does on first focus
with open(path, "w") as fh:
    json.dump(d, fh)
op = [o for o in ccs.list_ops(env) if o.manifest.get("op_id") == m["op_id"]][0]
refusal("undo refuses a row the app has touched",
        lambda: ccs.undo_new_row(env, op), "no longer holds")
check("  and leaves it in place", os.path.exists(path))
shutil.rmtree(root, ignore_errors=True)

root, env, live, dorm = build([OPENER] + prose(8))
m = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID, title="Recovered"))
ccs.run_new_row(env, m)
os.unlink(m["rows"][0]["dest_path"])
op = [o for o in ccs.list_ops(env) if o.manifest.get("op_id") == m["op_id"]][0]
refusal("undo reports an already-absent row distinctly from drift",
        lambda: ccs.undo_new_row(env, op), "already gone")
shutil.rmtree(root, ignore_errors=True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tools/check_new_row.py`
Expected: FAIL — `AttributeError: ... 'run_new_row'`

- [ ] **Step 3: Write minimal implementation**

```python
def _row_is_ours(r):
    """Whether the file at this row's dest_path is the one THIS op created.

    The evidence is the uuid4 in the row's own `sessionId`: this op minted it at
    plan time, so a file carrying it can only have come from here. That matters
    on the drifted path, where "the app rewrote the row we wrote" and "an
    unrelated file is sitting on our filename" are the same byte-level mismatch
    and want opposite explanations.

    Never raises - it is called from an error path, and a second exception
    thrown while composing the first one's message helps nobody.
    """
    try:
        d = read_json(r["dest_path"])
    except (LayoutError, OSError, ValueError):
        return False
    return (isinstance(d, dict)
            and d.get("sessionId") == os.path.splitext(r["name"])[0])


def _new_row_preflight(env, m):
    """Every non-mutating re-check, run under the lock, before anything is
    journalled or written. Raises Refusal; returns nothing.

    THESE ARE THE REAL GUARDS, not the ones in plan_new_row. Planning runs
    unlocked, so between a dry run and --apply another writer can create a row
    for this conversation, the transcript can age out, move, or be duplicated.

    It is a separate function, and it runs BEFORE `new_op`, for a reason that
    only shows up on the failure path: journalling first meant a perfectly safe
    refusal - transcript gone, duplicate row appeared - left a non-terminal op
    behind, and `doctor` then told the user to run `recover` over a command that
    had touched nothing at all. A refusal that manufactures cleanup work is a
    refusal that trains people to ignore the tool.
    """
    _guard_mutation(env, "create a row in")
    if not os.path.isdir(m["store_path"]):
        raise LayoutError("store vanished: " + m["store_path"])
    # The transcript must still be THE one this row was planned against - not
    # merely some transcript with that id, and not the same path with different
    # contents. Every fact in the post-image was read out of that file, so a
    # transcript that grew or was rewritten between plan and apply leaves the
    # row asserting timestamps and a turn count that no longer describe it.
    found = find_transcripts(env.projects_root, m["to_session"])
    if not found:
        raise Refusal(
            "the transcript for {0} is no longer on disk, so this row would open "
            "nothing. Nothing was written.".format(m["to_session"]))
    if len(found) > 1:
        raise Refusal(
            "{0} now exists in more than one project folder, so the row planned "
            "against a single transcript no longer names one. Nothing was "
            "written:\n{1}".format(m["to_session"], "\n".join("   " + f
                                                             for f in found)))
    if os.path.realpath(found[0]) != os.path.realpath(m["transcript"]):
        raise Refusal(
            "the transcript for {0} has moved since this was planned ({1} -> "
            "{2}); the row's recorded facts came from the old path. Nothing was "
            "written - re-run to replan.".format(m["to_session"], m["transcript"],
                                                 found[0]))
    # Same path, different bytes. Size and mtime rather than a hash: these
    # transcripts run to 96 MB, re-reading one to prove it is unchanged costs
    # more than the fact is worth, and an append - the way transcripts actually
    # change - moves both.
    try:
        st = os.stat(found[0])
    except OSError as exc:
        raise Refusal("could not stat the transcript for {0}: {1}. Nothing was "
                      "written.".format(m["to_session"], exc))
    if (st.st_size != m.get("transcript_size")
            or int(st.st_mtime) != m.get("transcript_mtime")):
        raise Refusal(
            "the transcript for {0} has changed since this was planned, so the "
            "row's recorded timestamps and turn count no longer describe it. "
            "Nothing was written - re-run to replan.".format(m["to_session"]))
    # Reachability, re-checked under the lock. plan_new_row's identical check is
    # for the dry run's benefit and closes no race at all.
    # Excluding our own row here is what lets this run again after a crash:
    # on re-entry the row may already be on disk, and without the exclusion
    # both checks below would fire against it.
    hit, titles = _row_already_opens(m["store_path"], m["to_session"],
                                     exclude_name=m["rows"][0]["name"])
    if hit:
        raise Refusal(
            "{0} now already opens {1} (row {2!r}) - something created it since "
            "this was planned. Nothing was written.".format(
                m["store_label"], m["to_session"][:8], hit))
    # The title set is re-read too, and not thrown away. _unique_title suffixed
    # past everything that existed at PLAN time; a row created since can hold
    # the suffix that was chosen, and writing it anyway would break the one
    # uniqueness promise this command makes. Only generated titles are checked -
    # an explicit --title duplicate was the user's own call.
    if m.get("title_provenance") != "yours" and m["title"] in titles:
        raise Refusal(
            "another row in {0} is now called {1!r} - it appeared since this was "
            "planned, and that title was chosen to be unique. Nothing was "
            "written; re-run and a fresh suffix will be picked."
            .format(m["store_label"], m["title"]))


def execute_new_row_op(env, op):
    """journaled -> writing -> completed. One row, created from nothing.

    Re-entrant from 'writing' for the same reason execute_repoint_op is: an op
    that died between the two set_status calls must still be finishable, and
    every decision here is made from the bytes on disk rather than from what the
    journal expects to find.
    """
    m = op.manifest
    if m.get("status") not in ("journaled", "writing"):
        raise LayoutError("execute_new_row_op runs ops from 'journaled' or "
                          "'writing'; this one is " + str(m.get("status")))
    r = m["rows"][0]
    # THE WRITE ALREADY LANDED - finish the journal and stop.
    #
    # This is the crash `recover --forward` exists for: the row was written and
    # the process died before the marker was saved. Re-validating a transcript
    # whose facts have ALREADY been consumed and committed to the bytes on disk
    # would make a transcript that has since aged out block recover - the one
    # command whose job is to get the user unstuck, refusing to finish
    # bookkeeping for a write that succeeded. The row matching post_b64 byte for
    # byte is better evidence than any re-derivation could be.
    #
    # It also needs no mutation guard: it writes only the journal, so there is
    # nothing in the account's store for a running app to race.
    if _sync_row_drift(r) == "match":
        r["written"] = True
        save_manifest(op)
        set_status(op, "completed")
        return "completed"
    # NO PREFLIGHT HERE. It runs in the two places that call this - run_new_row
    # before journalling, recover_op's forward arm before re-entering - and
    # calling it here as well would undo the very fix that moved it out: a
    # refusal raised at this point is raised AFTER new_op, which is what left a
    # non-terminal op behind for a command that changed nothing. One caller, one
    # preflight, always before the journal entry exists.
    set_status(op, "writing")
    real = ensure_contained(r["dest_path"], [m["store_path"]])
    if os.path.dirname(real) != os.path.realpath(m["store_path"]):
        raise LayoutError("row {0!r} is not a direct child of {1!r}; refusing"
                          .format(r["dest_path"], m["store_path"]))
    post = unb64(r["post_b64"])
    if os.path.exists(r["dest_path"]):
        with open(r["dest_path"], "rb") as fh:
            current = fh.read()
        if current == post:
            r["written"] = True              # already done; re-entry is safe
        else:
            # Two very different situations reach here, and telling the user the
            # wrong one is worse than saying nothing. If this op had already
            # written the row and the app has since rewritten it, "a different
            # row already exists, nothing was written" is simply false - this op
            # DID write it. Only an op that never wrote is looking at a genuine
            # uuid4 collision. Never overwrite either way: this command adds.
            if _row_is_ours(r):
                raise Refusal(
                    "this op wrote {0!r}, and something has changed it since - "
                    "most likely the app, which rewrites these rows when it "
                    "opens the session. It was NOT overwritten and nothing more "
                    "was written; 'recover --back' closes this operation and "
                    "leaves the row alone.".format(r["name"]))
            raise Refusal(
                "a different row already exists at {0!r}; refusing to overwrite "
                "it. Nothing was written.".format(r["name"]))
    else:
        try:
            atomic_write(r["dest_path"], post)
        except OSError as exc:
            raise Refusal("could not write the row: {0}".format(exc))
        _maybe_crash("new-row-write-before-save")
        r["written"] = True
    save_manifest(op)
    set_status(op, "completed")
    return "completed"


def run_new_row(env, manifest):
    """Lock, journal, execute, rotate - the same shape as run_repoint.

    Journal BEFORE the write: the only crash-visible states are then
    "journalled, not written" - which recover completes or closes - and
    "journalled and written", which is finished. Writing first would allow a row
    on disk that no op knows about, and nothing could find it to undo it.
    """
    # No _guard_mutation here - _new_row_preflight opens with it. Calling it in
    # both places enumerated the running process list twice per apply for one
    # answer, and process enumeration is the slowest thing this command does.
    acquire_lock(env, "new-row")
    try:
        # Preflight BEFORE new_op. Journalling first meant a safe refusal - the
        # transcript aged out, another writer got there first - left a
        # non-terminal op behind, and doctor then told the user to run 'recover'
        # over a command that had touched nothing.
        _new_row_preflight(env, manifest)
        op = new_op(env, manifest)
        manifest["op_id"] = op.manifest["op_id"]
        set_status(op, "journaled")
        final = execute_new_row_op(env, op)
        rotate_ops(env)
        return final
    finally:
        release_lock(env)


def undo_new_row(env, op):
    """Delete the row this op created - but only while it still holds exactly
    what was written.

    The same evidence rule `undo_sync` applies to rows a sync ADDED. If the
    account has since opened the session the app rewrites the row, and deleting
    it would discard that account's own state.
    """
    m = op.manifest
    acquire_lock(env, "undo-" + m["op_id"])
    try:
        if m.get("op_type") != "new-row":
            raise Refusal("not a new-row op: " + str(m.get("op_id")))
        if m.get("status") != "completed":
            raise Refusal("op {0} is '{1}', not 'completed'".format(
                m.get("op_id"), m.get("status")))
        _guard_mutation(env, "remove a row from")
        r = m["rows"][0]
        if not r.get("written"):
            raise Refusal("this op never wrote the row; nothing to undo")
        real = ensure_contained(r["dest_path"], [m["store_path"]])
        if os.path.dirname(real) != os.path.realpath(m["store_path"]):
            raise LayoutError("row {0!r} is not a direct child of {1!r}; refusing"
                              .format(r["dest_path"], m["store_path"]))
        state = _sync_row_drift(r)
        if state == "absent":
            raise Refusal(
                "that row is already gone - something removed it since this op "
                "created it. Nothing to undo, and nothing was changed.")
        if state != "match":
            raise Refusal(
                "that row no longer holds what this op wrote ({0}); something "
                "changed it since - most likely the app, which rewrites these "
                "rows whenever it opens the session. Refusing to delete it."
                .format(state))
        try:
            os.unlink(r["dest_path"])
        except OSError as exc:
            raise Refusal("could not remove the row: {0}".format(exc))
        set_status(op, "undone")
        rotate_ops(env)
        return "undone"
    finally:
        release_lock(env)
```

- [ ] **Step 4: Run to verify it passes**

Run: `python tools/check_new_row.py`
Expected: PASS

- [ ] **Step 5: Run the whole suite and commit**

```bash
for t in tools/check_*.py; do python "$t" >/dev/null 2>&1 && echo "PASS $t" || echo "FAIL $t"; done
git add claude_code_sessions.py tools/check_new_row.py
git commit -m "feat(new-row): execute, run, and undo by deletion

The guards live in execute_new_row_op, under the lock - not in plan_new_row,
which runs unlocked and closes no race at all. Between a dry run and --apply
another writer can create a row for the same conversation, the transcript can
age out, or a duplicate can appear in a second project folder; all three are
re-checked before the write, and the transcript must still be the same single
file the row's recorded facts came from.

Journal before write, so the only crash-visible states are 'journalled not
written' and 'journalled and written' - writing first would allow a row on
disk that no op knows about and nothing could find to undo.

Undo DELETES rather than restores, under the same evidence rule undo_sync
applies to rows a sync added: only while the row still holds exactly what
was written. An already-absent row reports that distinctly from drift."
```

---

### Task 5: `classify_op` and `recover_op` learn the new op type

**Files:**
- Modify: `claude_code_sessions.py` — `classify_op` (:1743) and `recover_op`
- Test: `tools/check_new_row.py`

**Interfaces:**
- Consumes: `execute_new_row_op`, `_sync_row_drift`, `ensure_contained`
- Produces: `classify_op` returns `resolutions: ["forward", "back"]` for a non-terminal `new-row`; `recover_op` handles both directions

**Why this task exists:** both functions dispatch on `op_type` against explicit allowlists — `recover_op` runs `_validate_manifest_paths` for anything not in `("sync", "repoint")`, and that validator expects move-shaped keys a new-row manifest does not carry. Without this, a crashed `new-row` op fails inside a validator written for a different command, with a message about missing keys that says nothing about what happened.

- [ ] **Step 1: Write the failing test**

Append to `tools/check_new_row.py`:

```python
# ------------------------------------------------------------ recovery
print("\n--- a crash after the write, before the journal marker ---")


def crashed():
    root, env, live, dorm = build([OPENER] + prose(8))
    m = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID, title="Recovered"))
    fired = {"n": 0}

    def crash_once(point):
        if point == "new-row-write-before-save" and fired["n"] == 0:
            fired["n"] += 1
            raise KeyboardInterrupt("killed after the write, before the marker")

    ccs._crash_hook = crash_once
    try:
        ccs.run_new_row(env, m)
    except BaseException:                      # noqa: BLE001 - the simulated kill
        pass
    finally:
        ccs._crash_hook = None
    return root, env, m


root, env, m = crashed()
pending = ccs.nonterminal_ops(env)
check("the op is left non-terminal", len(pending) == 1)
check("  with the row already on disk - the torn state",
      os.path.exists(m["rows"][0]["dest_path"]))
c = ccs.classify_op(env, pending[0])
check("  classify_op knows the new-row shape rather than falling through",
      c["status"] in ("journaled", "writing"), c["status"])
check("  and offers both directions",
      sorted(c["resolutions"]) == ["back", "forward"], str(c["resolutions"]))
check("  with a note naming the command", "new-row" in c["note"], c["note"][:70])
shutil.rmtree(root, ignore_errors=True)

root, env, m = crashed()
check("recover --forward completes",
      ccs.recover_op(env, ccs.nonterminal_ops(env)[0], "forward") == "completed")
check("  and the row is on disk", os.path.exists(m["rows"][0]["dest_path"]))
shutil.rmtree(root, ignore_errors=True)

# The write already landed, and the transcript has since gone. recover MUST
# still finish the bookkeeping: the row's facts were consumed at plan time and
# are already committed to the bytes on disk, so re-validating the source of an
# answer that is already written would make the one command whose job is to
# unstick the user refuse on a write that succeeded.
root, env, m = crashed()
os.unlink(m["transcript"])
check("recover --forward finishes an already-written row even with the "
      "transcript gone",
      ccs.recover_op(env, ccs.nonterminal_ops(env)[0], "forward") == "completed")
check("  leaving the row in place", os.path.exists(m["rows"][0]["dest_path"]))
check("  and nothing pending", not ccs.nonterminal_ops(env))
shutil.rmtree(root, ignore_errors=True)

# ...but a row that did NOT land still gets the full preflight on the way
# forward, because nothing has been committed and the facts must still hold.
root, env, m = crashed()
os.unlink(m["rows"][0]["dest_path"])
os.unlink(m["transcript"])
refusal("recover --forward on an unwritten row still refuses a gone transcript",
        lambda: ccs.recover_op(env, ccs.nonterminal_ops(env)[0], "forward"),
        "no longer on disk")
shutil.rmtree(root, ignore_errors=True)

# classify_op's note is what the user reads to choose a direction. A drifted
# row must not be described as never written.
root, env, m = crashed()
d = json.load(open(m["rows"][0]["dest_path"], encoding="utf-8"))
d["lastFocusedAt"] = 4242
with open(m["rows"][0]["dest_path"], "w") as fh:
    json.dump(d, fh)
note = ccs.classify_op(env, ccs.nonterminal_ops(env)[0])["note"]
check("a drifted row is NOT reported as 'was not written'",
      "was not written" not in note, note)
check("  it is reported as written and since changed",
      "since changed" in note, note)
# And forcing forward on it must say something TRUE. "a different row already
# exists ... nothing was written" is false twice over: it is our row, and we
# did write it.
try:
    ccs.recover_op(env, ccs.nonterminal_ops(env)[0], "forward")
    check("  forcing forward on a drifted row refuses", False, "it did not")
except ccs.Refusal as exc:
    check("  forcing forward on a drifted row refuses", True)
    check("    without claiming nothing was written",
          "Nothing was written" not in str(exc), str(exc)[:100])
    check("    and names the app as the likely cause",
          "the app" in str(exc), str(exc)[:100])
shutil.rmtree(root, ignore_errors=True)

root, env, m = crashed()
check("recover --back closes the op",
      ccs.recover_op(env, ccs.nonterminal_ops(env)[0], "back") == "rolled_back")
check("  removing the row it had written",
      not os.path.exists(m["rows"][0]["dest_path"]))
check("  and nothing is left pending", not ccs.nonterminal_ops(env))
shutil.rmtree(root, ignore_errors=True)

# A crash BEFORE the write: nothing on disk, and 'back' must still terminate.
root, env, live, dorm = build([OPENER] + prose(8))
m = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID, title="Recovered"))
fired = {"n": 0}


def crash_early(point):
    if point == "new-row-write-before-save" and fired["n"] == 0:
        fired["n"] += 1
        raise KeyboardInterrupt("killed")


ccs._crash_hook = crash_early
try:
    ccs.run_new_row(env, m)
except BaseException:                          # noqa: BLE001
    pass
finally:
    ccs._crash_hook = None
os.unlink(m["rows"][0]["dest_path"])           # simulate the no-row-yet state
check("recover --back terminates even with no row on disk",
      ccs.recover_op(env, ccs.nonterminal_ops(env)[0], "back") == "rolled_back")
check("  and nothing is left pending", not ccs.nonterminal_ops(env))
shutil.rmtree(root, ignore_errors=True)

# RULING 4 on the recovery path. recover_op calls _guard_mutation for no op
# type at all, so before this fix, crashing, reopening the app, and then
# recovering backward would delete a row out from under a running app that is
# reading and rewriting that same store.
DESKTOP = (1, r"c:\program files\windowsapps"
              r"\claude_1.0_x64__pzs8sxrjxfjjc\app\claude.exe")

root, env, m = crashed()
env.process_lister = lambda: [DESKTOP]
refusal("recover --back refuses while the desktop app is running",
        lambda: ccs.recover_op(env, ccs.nonterminal_ops(env)[0], "back"),
        "desktop app appears to be running")
check("  leaving the row alone", os.path.exists(m["rows"][0]["dest_path"]))
check("  and the op still recoverable once the app is closed",
      len(ccs.nonterminal_ops(env)) == 1)
env.process_lister = lambda: []
check("  which it then is",
      ccs.recover_op(env, ccs.nonterminal_ops(env)[0], "back") == "rolled_back")
shutil.rmtree(root, ignore_errors=True)

# Forward is asymmetric, and deliberately so. On an ALREADY-WRITTEN row it
# touches only the journal - no store bytes change - so refusing it while the
# app runs would block harmless bookkeeping and leave the user stuck for no
# safety gain. On an UNWRITTEN row it is about to write the store, so the guard
# applies. The split falls out of _new_row_preflight running only in the second
# case; these two checks pin that it is real and not incidental.
root, env, m = crashed()
env.process_lister = lambda: [DESKTOP]
check("recover --forward on an already-written row is journal-only, so it "
      "completes even with the app running",
      ccs.recover_op(env, ccs.nonterminal_ops(env)[0], "forward") == "completed")
shutil.rmtree(root, ignore_errors=True)

root, env, m = crashed()
os.unlink(m["rows"][0]["dest_path"])           # nothing landed
env.process_lister = lambda: [DESKTOP]
refusal("recover --forward on an UNwritten row refuses while the app runs",
        lambda: ccs.recover_op(env, ccs.nonterminal_ops(env)[0], "forward"),
        "desktop app appears to be running")
shutil.rmtree(root, ignore_errors=True)

# A drifted row is NOT deleted by 'back', and 'back' must say so rather than
# reporting a clean rollback that left a row behind.
root, env, m = crashed()
d = json.load(open(m["rows"][0]["dest_path"], encoding="utf-8"))
d["lastFocusedAt"] = 999
with open(m["rows"][0]["dest_path"], "w") as fh:
    json.dump(d, fh)
res = ccs.recover_op(env, ccs.nonterminal_ops(env)[0], "back")
check("back closes an op whose row drifted", res == "rolled_back")
check("  leaving the drifted row alone", os.path.exists(m["rows"][0]["dest_path"]))
left = [o for o in ccs.list_ops(env) if o.manifest.get("op_id") == m["op_id"]][0]
check("  and RECORDING that it left something behind",
      bool(left.manifest.get("rollback_residue")),
      str(left.manifest.get("rollback_residue")))
shutil.rmtree(root, ignore_errors=True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tools/check_new_row.py`
Expected: FAIL — `classify_op` falls through to the move branch and raises, or returns `resolutions: []`

- [ ] **Step 3: Write minimal implementation**

In `classify_op` (`claude_code_sessions.py:1743`), add this branch between the existing `repoint` branch (:1751) and the `sync` branch (:1763):

```python
    if m.get("op_type") == "new-row":
        # Unlike repoint, there IS a partial state worth resolving: the row may
        # or may not have landed before the crash. Both directions are real -
        # forward finishes the write, back removes what landed - so offer them
        # rather than returning the empty list repoint returns.
        r = (m.get("rows") or [{}])[0]
        # Map the ACTUAL state, all five of them. `== "match"` collapsed four
        # states into "was not written", so a row the app had already reopened
        # and rewritten - the plan's own stated risk - was reported as never
        # written while sitting on disk. That note is what the user reads to
        # choose a direction, and it was wrong in the direction that invites a
        # careless `forward`.
        state = _sync_row_drift(r)
        said = {"match": "was written",
                "absent": "was not written",
                "drifted": "was written and something has since changed it",
                "pristine": "was written and something has since changed it",
                "unreadable": "exists but could not be read"}.get(
                    state, "is in an unrecognized state ({0})".format(state))
        return {"status": m["status"], "source": m.get("store_label", "n/a"),
                "dest": m.get("store_label", "n/a"),
                "resolutions": ["forward", "back"], "drifted_rows": [],
                "note": "new-row: the row {0}; forward finishes creating it, "
                        "back removes it only if it still matches what this op "
                        "wrote".format(said)}
```

In `recover_op`, widen the allowlist so a new-row manifest is not handed to the move-shaped validator:

```python
        if op.manifest.get("op_type") not in ("sync", "repoint", "new-row"):
            _validate_manifest_paths(env, op.manifest)
```

Then, in `recover_op`'s direction handling, add — immediately before the `sync` branch:

```python
        if m.get("op_type") == "new-row":
            if direction == "forward":
                # The preflight moved out of execute_new_row_op, so this arm
                # owns it - and only for a row that did NOT land. A landed row
                # needs no re-validation (its facts are already committed) and
                # no mutation guard (it writes only the journal).
                if _sync_row_drift(m["rows"][0]) != "match":
                    _new_row_preflight(env, m)
                set_status(op, "journaled")
                final = execute_new_row_op(env, op)
                rotate_ops(env)
                return final
            # The running-app guard, which this arm did not have. `recover_op`
            # never calls _guard_mutation for any op type, and every other
            # deleting path in this module does - so after a crash, reopening
            # the app and then recovering backward would delete a row out from
            # under a running app that is reading and rewriting that store. The
            # exact race the whole command otherwise refuses to run into.
            _guard_mutation(env, "remove a row from")
            r = m["rows"][0]
            # 'back' must always terminate - it is the only exit from a stuck
            # op - so a row it cannot safely remove is SKIPPED rather than
            # refused, the same asymmetry _sync_delete_targets documents against
            # undo. But skipping SILENTLY would report a clean rollback while
            # leaving a row on disk that nothing afterwards looks for: doctor's
            # vanished-row check reads only 'completed' ops, so a rolled_back op
            # with a surviving row is invisible everywhere. Record it instead.
            residue = None
            state = _sync_row_drift(r)
            if state == "match":
                try:
                    # Containment is re-established here, not inherited. This
                    # arm deletes a path out of a journal file, and a journal
                    # can be edited or corrupted; the write and undo paths both
                    # check, so the recovery path must too.
                    real = ensure_contained(r["dest_path"], [m["store_path"]])
                    if os.path.dirname(real) != os.path.realpath(m["store_path"]):
                        raise LayoutError("not a direct child of the store")
                    os.unlink(r["dest_path"])
                except (OSError, LayoutError) as exc:
                    residue = "could not remove {0!r}: {1}".format(r["name"], exc)
            elif state != "absent":
                residue = ("left {0!r} in place - it no longer holds what this op "
                           "wrote ({1})".format(r["name"], state))
            if residue:
                m["rollback_residue"] = residue
                save_manifest(op)
            set_status(op, "rolled_back")
            rotate_ops(env)
            return "rolled_back"
```

Finally, in `cmd_recover`'s reporting, surface it — a residue nobody prints is a residue nobody acts on:

```python
    if op.manifest.get("rollback_residue"):
        say("[observed] rollback left something behind: {0}"
            .format(op.manifest["rollback_residue"]))
```

- [ ] **Step 4: Run to verify it passes**

Run: `python tools/check_new_row.py`
Expected: PASS

- [ ] **Step 5: Run the whole suite and commit**

```bash
for t in tools/check_*.py; do python "$t" >/dev/null 2>&1 && echo "PASS $t" || echo "FAIL $t"; done
git add claude_code_sessions.py tools/check_new_row.py
git commit -m "feat(new-row): classify_op and recover_op learn the op type

Both dispatch on op_type against explicit allowlists. recover_op ran
_validate_manifest_paths for anything outside ('sync', 'repoint'), and that
validator expects move-shaped keys a new-row manifest does not carry - so a
crashed new-row op would have failed inside a validator written for a
different command.

Unlike repoint, there is a real partial state: the row may or may not have
landed. Both directions are offered. 'back' skips a row it cannot safely
remove rather than refusing, because back is the only exit from a stuck op
and must always terminate - but it now RECORDS what it left behind and
recover prints it. Skipping silently reported a clean rollback while leaving
a row that nothing afterwards looks for: doctor's vanished-row check reads
only completed ops, so a rolled_back op with a surviving row was invisible.

The back arm re-establishes containment before unlinking. It deletes a path
read out of a journal file, and a journal can be corrupted or edited; the
write and undo paths both check, so this one must too."
```

---

### Task 6: CLI wiring, the report, and `--json`

**Files:**
- Modify: `claude_code_sessions.py` — `build_parser` (~:2420), plus new `_print_new_row_report`, `_public_new_row_manifest`, `cmd_new_row`, and the dispatch table
- Test: `tools/check_new_row.py`

**Interfaces:**
- Consumes: `plan_new_row`, `run_new_row`
- Produces: `_print_new_row_report(say, m)`, `_public_new_row_manifest(m) -> dict`, `cmd_new_row(env, ns) -> int`

- [ ] **Step 1: Write the failing test**

Append to `tools/check_new_row.py`:

```python
# ------------------------------------------------------------ report and json
print("\n--- the report, and --json ---")

root, env, live, dorm = build(
    [rec("user", "opening message long enough to count",
         ts="2026-06-14T09:00:00.000Z", cwd=r"C:\Users\craig\Projects\Personal",
         custom="Their own title")] + prose(8))
m = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID))
out = []
ccs._print_new_row_report(out.append, m)
joined = "\n".join(out)
check("the report names the title", "Their own title" in joined)
check("  AND where it came from", "custom title" in joined, joined[:120])
check("  the conversation it will open", SID[:8] in joined)
check("  which store, and why that one", m["store_why"] in joined, joined[:200])
check("  and says the row is new rather than moved",
      "creates a NEW" in joined or "new sidebar row" in joined, joined[:200])
check("  without claiming nothing else points at the conversation",
      "nothing pointing at it" not in joined, joined[:200])

pub = ccs._public_new_row_manifest(m)
blob = json.dumps(pub)
check("--json carries no row image", "post_b64" not in blob)
check("  nor a pre-image key", "pre_b64" not in blob)
check("  while still naming the op and the target",
      pub["op_type"] == "new-row" and pub["to_session"] == SID)
shutil.rmtree(root, ignore_errors=True)

# The store choice must be VISIBLE BEFORE the write, not after it. There is no
# way to hand a saved dry run back to the CLI, so a separate dry run replans
# and proves nothing about what the apply will pick - which makes "the user
# sees the heuristic first" false unless --apply itself prints first.
print("\n--- the report prints before the write ---")


root, env, live, dorm = build([OPENER] + prose(8))
order = []
import builtins                                  # noqa: E402
real_bp = builtins.print
real_run = ccs.run_new_row


def spy_print(*a, **k):
    order.append(("print", " ".join(str(x) for x in a)))


def spy_run(e, mm):
    order.append(("write", mm["name"]))
    return real_run(e, mm)


builtins.print = spy_print
ccs.run_new_row = spy_run
try:
    ccs.cmd_new_row(env, _Ns(to_session=SID, store="", title="Recovered",
                             live="", apply=True, json=False))
finally:
    builtins.print = real_bp
    ccs.run_new_row = real_run

kinds = [k for k, _ in order]
check("the write happens at all", "write" in kinds)
check("  and the report was printed BEFORE it",
      kinds.index("print") < kinds.index("write"), str(kinds[:4]))
check("  including the store's reasoning",
      any(k == "print" and "chosen as" in v
          for k, v in order[:kinds.index("write")]),
      str([v for k, v in order[:kinds.index("write")]][:6]))
shutil.rmtree(root, ignore_errors=True)

# A guessed store may PLAN and may not WRITE. Task 3 asserts that planning
# marks the guess; this is where the refusal itself lives, because cmd_new_row
# is what enforces it and it does not exist before this task.
print("\n--- a guessed store may plan, and may not write ---")

root, env, live, dorm = build([OPENER] + prose(8))
os.makedirs(os.path.join(os.path.dirname(live), ORG_D))    # empty scaffolding
with open(os.path.join(live, "local_anything.json"), "w") as fh:
    json.dump({"cliSessionId": "%032d" % 55, "title": "Something else",
               "cwd": "proj", "lastActivityAt": 1}, fh)
refusal("--apply refuses when row counts chose the store",
        lambda: ccs.cmd_new_row(env, _Ns(to_session=SID, store=LIVE,
                                         title="", live="", apply=True,
                                         json=False)),
        "decided by counting rows")
check("  and writes nothing",
      sorted(os.listdir(live)) == ["local_anything.json"], str(os.listdir(live)))
check("  while a dry run on the same guess is allowed",
      ccs.cmd_new_row(env, _Ns(to_session=SID, store=LIVE,
                               title="", live="", apply=False, json=False)) == 0)
# The refusal must name something that WORKS when pasted back. Naming the org
# id does; naming the path does not, because _repoint_store forward-slashes the
# candidate but not the user's argument.
check("naming the org id the refusal suggested lets --apply through",
      ccs.cmd_new_row(env, _Ns(to_session=SID, store=ORG_L, title="Recovered",
                               live="", apply=True, json=False)) == 0)
check("  and the row is on disk",
      any(n != "local_anything.json" for n in os.listdir(live)),
      str(os.listdir(live)))
shutil.rmtree(root, ignore_errors=True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tools/check_new_row.py`
Expected: FAIL — `AttributeError: ... '_print_new_row_report'`

- [ ] **Step 3: Write minimal implementation**

```python
def _print_new_row_report(say, m):
    say("store   : {0}".format(m["store_label"]))
    # Printed because _new_row_store's choice among an account's org directories
    # is a heuristic. A heuristic the user can see before --apply is a very
    # different thing from one they cannot.
    say("          chosen as {0}".format(m["store_why"]))
    if m.get("store_is_a_guess"):
        say("          ^ that is a GUESS from row counts, not an identification."
            " --apply")
        say("            will refuse until you name the store with --store.")
    say("new row : local_{0}.json".format(m["name"]))
    say("title   : {0}   ({1})".format(m["title"], m["title_provenance"]))
    if m.get("title_collision"):
        say("          another row in this account is already called {0!r}"
            .format(m["title_collision"]))
    say("")
    say("will open : {0}   ({1} MB, {2})".format(
        m["to_session"], m["transcript_mb"],
        "{0} prose turns".format(m["turns"]) if m["turns"] is not None
        else "too large to count turns"))
    say("project   : {0}".format(m["cwd"]))
    say("model     : {0}   (read from the transcript)".format(m["model"]))
    say("")
    say("This creates a NEW sidebar row. Nothing existing is changed, and the")
    say("conversation itself is not touched. No row in THIS account opens it")
    say("today; other accounts are not consulted, and may well have one.")


def _public_new_row_manifest(m):
    """The new-row manifest with the row image removed, for --json.

    Same rule as `_public_repoint_manifest` and `_public_manifest`: a listing
    row carries `remoteMcpServersConfig` and permission state, and printing it
    to stdout lets ordinary automation log an account's connector configuration.
    This command's post-image is synthesized rather than copied out of an
    account, which makes it less sensitive and not differently governed - it
    goes through the same filter rather than around it.
    """
    out = {k: v for k, v in m.items() if k != "rows"}
    out["rows"] = [{k: v for k, v in r.items() if k not in ("pre_b64", "post_b64")}
                   for r in m.get("rows", [])]
    return out


def cmd_new_row(env, ns):
    flags = NewRowFlags(to_session=ns.to_session, store=ns.store,
                        title=ns.title, live=ns.live)
    m = plan_new_row(env, flags)
    # The HUMAN report prints before the write, unlike cmd_sync and cmd_repoint,
    # and the difference is deliberate. _new_row_store may have picked this
    # account's store out of several by a heuristic, and the plan justifies that
    # heuristic by saying the user sees it before anything happens. Printing
    # afterwards made that false for `--apply` in one shot - which is how the
    # command will usually be run, because there is no way to hand a saved dry
    # run back to the CLI, so a separate dry run replans and proves nothing
    # about what the apply will choose.
    #
    # --json keeps the other order (apply first, then print), exactly as
    # cmd_sync and cmd_repoint do: printing first meant `--apply --json`
    # reported a plan, exited 0, and wrote nothing, which automation reads as a
    # completed operation.
    if not ns.json:
        _print_new_row_report(print, m)
        if ns.apply:
            print("")
    # A guessed store may PLAN but never WRITE. Printing the guess and then
    # writing anyway leaves no moment for anyone to intervene, so the dry run
    # shows it and the apply refuses until the user settles it themselves.
    if ns.apply and m.get("store_is_a_guess"):
        raise Refusal(
            "which store should get this row was decided by counting rows, not "
            "by anything that identifies the account: {0}. That is fine for a "
            "dry run and not fine for a write. Re-run with --store {1} if that "
            "is the one you mean. Nothing was written."
            .format(m["store_why"], m["store_org"]))
    final = run_new_row(env, m) if ns.apply else None
    if ns.json:
        pub = _public_new_row_manifest(m)
        if final is not None:
            pub["result"] = final
        print(json.dumps(pub, indent=1))
        return 0 if final in (None, "completed") else 1
    if final is None:
        print("\ndry run - pass --apply to create the row")
        return 0
    print("result  : {0}".format(final))
    if final == "completed":
        print("Reopen the app - the session should be in the sidebar. 'undo' "
              "removes the row again.")
        return 0
    # cmd_move and cmd_sync both gate their exit code on the result; cmd_repoint
    # returns 0 unconditionally, and that is the sibling not to copy. Today
    # execute_new_row_op can only return "completed" or raise - but an
    # unconditional success trailer plus exit 0 is a trap laid for whoever adds
    # a second return value later.
    return 1
```

Register the subcommand in `build_parser`, after the `repoint` parser:

```python
    nr = sub.add_parser("new-row",
                        help="create a sidebar row for a conversation that has none")
    nr.add_argument("--to", dest="to_session", default="", metavar="CLI_SESSION_ID",
                    help="the cliSessionId the new row should open. 'doctor' lists "
                         "conversations no account currently points at")
    nr.add_argument("--store", default="", metavar="SUBSTRING",
                    help="which account's store to add the row to (account id, org "
                         "id, path, or email). Defaults to the account the identity "
                         "files agree is signed in, and refuses when they disagree")
    nr.add_argument("--title", default="", metavar="TEXT",
                    help="the row's title. Without this it is taken from the "
                         "transcript's own title if it has one, otherwise a "
                         "placeholder that does not impersonate a summary")
    nr.add_argument("--live", default="", metavar="SUBSTRING",
                    help="assert which account the desktop app is signed into "
                         "(RULING 5), when the identity files disagree")
    nr.add_argument("--apply", action="store_true", help="actually create the row")
    nr.add_argument("--json", action="store_true", help="print the plan as JSON")
```

**Deliberately NO `--verbose`.** The other commands use it to stop redacting paths in their reports; `_print_new_row_report` has no redaction to switch off, so accepting the flag would advertise behaviour it does not have. Add the flag in the same change that adds redaction, or not at all.

And add `"new-row": cmd_new_row` to the dispatch mapping beside `"repoint": cmd_repoint`.

- [ ] **Step 4: Run to verify it passes, and exercise the CLI by hand**

Run: `python tools/check_new_row.py`
Expected: PASS

Run: `python claude_code_sessions.py new-row --help`
Expected: exactly six options — `--to`, `--store`, `--title`, `--live`, `--apply`, `--json` — and no `--verbose`

- [ ] **Step 5: Run the whole suite and commit**

```bash
for t in tools/check_*.py; do python "$t" >/dev/null 2>&1 && echo "PASS $t" || echo "FAIL $t"; done
git add claude_code_sessions.py tools/check_new_row.py
git commit -m "feat(new-row): CLI wiring, report and --json

The report names the title AND its provenance, and the store AND why that
store - _new_row_store's choice among an account's org directories is a
heuristic, and one the user can see before --apply is a different thing from
one they cannot.

It no longer claims the conversation has 'nothing pointing at it': the
reachability check is scoped to one account by design, and other accounts
may well have a row. Saying otherwise was false in exactly the case this
tool spent a day creating on purpose.

No --verbose. The other commands use it to stop redacting paths and this
report has no redaction to switch off, so accepting it would advertise
behaviour it does not have.

--apply runs before --json prints, as cmd_sync and cmd_repoint do."
```

---

### Task 7: `doctor` reports a synthesized row that has vanished

**Files:**
- Modify: `claude_code_sessions.py` — `gather_doctor` (the `report` literal at :2305) and `cmd_doctor`
- Test: `tools/check_new_row.py`

**Interfaces:**
- Consumes: `list_ops`, `_sync_row_drift`, `find_transcripts`
- Produces: `gather_doctor`'s `report` gains `vanished_new_rows: [{op_id, title, to_session, store_label, transcript_count}]`

**Why:** the app tolerating a row it never issued rests on one experiment. If a future version tombstones one, the row is gone, `undo`'s byte-identity test finds nothing to match, and the user learns by noticing an absent sidebar entry.

**Stated limit — this detection is bounded by journal retention.** `rotate_ops` bounds the ops journal, so once a completed `new-row` op rotates out, its row becomes invisible to this check. That makes the detector good for the days after a row is created — when a tombstoning app version would act — and silent thereafter. A durable answer would need a separate append-only registry of synthesized rows, which is a design change beyond this plan; it is recorded as a known limit in Task 8 rather than pretended away here.

- [ ] **Step 1: Write the failing test**

```python
# ------------------------------------------------------------ doctor detection
print("\n--- doctor notices a synthesized row that vanished ---")

root, env, live, dorm = build([OPENER] + prose(8))
m = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID, title="Recovered"))
ccs.run_new_row(env, m)
d = ccs.gather_doctor(env)
check("a row that is still there is not reported",
      not d.get("vanished_new_rows"), str(d.get("vanished_new_rows")))
os.unlink(m["rows"][0]["dest_path"])           # as a tombstoning app would
d = ccs.gather_doctor(env)
check("a vanished synthesized row IS reported", len(d["vanished_new_rows"]) == 1)
v = d["vanished_new_rows"][0]
check("  naming the op that created it", v["op_id"] == m["op_id"])
check("  and the conversation it opened", v["to_session"] == SID)
check("  having actually counted the transcripts rather than assuming",
      v["transcript_count"] == 1, str(v["transcript_count"]))
check("  and doctor's exit code reflects the anomaly", d["exit_code"] != 0,
      str(d["exit_code"]))

# Two project folders: the recreate advice would refuse, so doctor must not
# give it. bool() on the find_transcripts list could not tell these apart.
other = os.path.join(env.projects_root, "other")
os.makedirs(other)
shutil.copy(m["transcript"], os.path.join(other, SID + ".jsonl"))
d = ccs.gather_doctor(env)
check("  an ambiguous transcript is counted, not just called present",
      d["vanished_new_rows"][0]["transcript_count"] == 2,
      str(d["vanished_new_rows"][0]["transcript_count"]))
shutil.rmtree(other, ignore_errors=True)

os.unlink(m["transcript"])
d = ccs.gather_doctor(env)
check("  a vanished row whose transcript ALSO went says so",
      d["vanished_new_rows"][0]["transcript_count"] == 0)
shutil.rmtree(root, ignore_errors=True)

# Taking doctor's OWN advice must clear doctor. Recreating mints a fresh uuid,
# so the original path stays absent forever - reporting on that path alone
# would leave a permanent alert whose suggested fix then refuses, because a row
# already opens the session. The question is reachability, not one path.
root, env, live, dorm = build([OPENER] + prose(8))
m = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID, title="Recovered"))
ccs.run_new_row(env, m)
os.unlink(m["rows"][0]["dest_path"])
check("doctor reports the vanished row", len(ccs.gather_doctor(env)["vanished_new_rows"]) == 1)
m2 = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID, title="Recovered again"))
check("  recreating it mints a DIFFERENT filename",
      m2["rows"][0]["name"] != m["rows"][0]["name"])
ccs.run_new_row(env, m2)
d = ccs.gather_doctor(env)
check("  and the alert clears, because the account can open it again",
      not d["vanished_new_rows"], str(d["vanished_new_rows"]))
check("  taking doctor's exit code back to clean", d["exit_code"] == 0,
      str(d["exit_code"]))
shutil.rmtree(root, ignore_errors=True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tools/check_new_row.py`
Expected: FAIL — `KeyError: 'vanished_new_rows'`

- [ ] **Step 3: Write minimal implementation**

In `gather_doctor`, immediately **above** the `report = {` literal at `:2305`:

```python
    # A synthesized row that is no longer on disk. The app tolerating a row it
    # never issued rests on one experiment (2026-08-22); if a future version
    # tombstones one, the row is gone, undo's byte-identity test finds nothing
    # to match, and nothing else would ever tell the user.
    #
    # BOUNDED BY JOURNAL RETENTION: rotate_ops ages ops out, so this sees only
    # rows whose creating op is still in the journal. That covers the window a
    # tombstoning app version would act in, and nothing after it. A durable
    # answer needs a standalone registry of synthesized rows - noted in the
    # README's known limits rather than silently implied here.
    vanished_new = []
    for _op in list_ops(env):
        _m = _op.manifest
        if _m.get("op_type") != "new-row" or _m.get("status") != "completed":
            continue
        _r = (_m.get("rows") or [{}])[0]
        if _r.get("written") and _sync_row_drift(_r) == "absent":
            # ONE PATH BEING ABSENT IS NOT THE QUESTION. The question is whether
            # the account can still open the conversation, and those come apart
            # in two ordinary ways: the user takes doctor's own advice and
            # re-runs `new-row`, which mints a FRESH uuid and leaves this path
            # absent forever - so the alert would never clear, and the suggested
            # command would then refuse because a row already opens it - or the
            # user deletes the row deliberately. Either way, reporting a
            # tombstone is wrong. Ask the store, not the journal.
            try:
                _still, _ = _row_already_opens(_m.get("store_path") or "",
                                               _m.get("to_session") or "")
            except (Refusal, LayoutError, OSError):
                _still = None       # unreadable store: report it, do not hide it
            if _still:
                continue
            vanished_new.append({
                "op_id": _m.get("op_id"), "title": _m.get("title"),
                "to_session": _m.get("to_session"),
                "store_label": _m.get("store_label"),
                # Checked, not assumed - and counted, not merely tested for
                # truthiness. The diagnostic below offers `new-row --to <id>`,
                # which refuses when the id resolves to more than one project
                # folder; bool() here would send the user at a command that
                # refuses. Only exactly one hit means the advice will work.
                "transcript_count": len(find_transcripts(
                    env.projects_root, _m.get("to_session") or "")),
            })
```

Add one key inside the `report` literal, after `"unknown_layout": unknown_layout,`:

```python
        "vanished_new_rows": vanished_new,
```

And extend the exit-code line below it — currently `elif blank or dead or nt or report["stale_lock"] or legacy_folders:` — to include `or vanished_new`. A row this tool created and something else removed is an anomaly worth a non-zero doctor, on the same footing as a dead row.

In `cmd_doctor`, after the existing orphan block:

```python
    for v in rep.get("vanished_new_rows") or []:
        say_ids("[observed] a row this tool created is no longer on disk: {0!r} "
                "(op {1}, {2})".format(v["title"], v["op_id"], v["store_label"]))
        if v["transcript_count"] == 1:
            say("[hypothesis]   the app removed a row it did not issue - the one "
                "documented risk of 'new-row'. The conversation ({0}) is still "
                "on disk; 'new-row --to {0}' makes another."
                .format(v["to_session"]))
        elif v["transcript_count"] == 0:
            say("[observed]     the conversation ({0}) is gone from disk too, so "
                "this is retention catching up rather than the app rejecting a "
                "row. Nothing to recreate.".format(v["to_session"]))
        else:
            say("[observed]     the conversation ({0}) is now in {1} project "
                "folders, so 'new-row' would refuse to guess between them. "
                "Resolve that first.".format(v["to_session"],
                                             v["transcript_count"]))
```

- [ ] **Step 4: Run to verify it passes**

Run: `python tools/check_new_row.py`
Expected: PASS

- [ ] **Step 5: Run the whole suite and commit**

```bash
for t in tools/check_*.py; do python "$t" >/dev/null 2>&1 && echo "PASS $t" || echo "FAIL $t"; done
git add claude_code_sessions.py tools/check_new_row.py
git commit -m "feat(new-row): doctor reports a synthesized row that vanished

The app tolerating a row it never issued rests on one experiment. If a
future version tombstones one, the row is gone, undo's byte-identity test
finds nothing to match, and the user learns by noticing an absent sidebar
entry.

It checks whether the transcript is still there rather than asserting it:
the diagnostic offers to recreate the row, and saying the conversation
survives without looking would send the user at a file that may also be
gone. A vanished row whose transcript went too is retention catching up,
not the app rejecting a row, and it now says which.

Bounded by journal retention - rotate_ops ages ops out, so this covers the
window a tombstoning version would act in and nothing after. Recorded as a
known limit rather than implied away."
```

---

### Task 8: Documentation and release

**Files:**
- Modify: `README.md` (the commands section — currently `Eight commands.` at :125), `docs/internals.md`, `claude_code_sessions.py` (`__version__` at :25)
- **Not** `pyproject.toml`: it reads the version dynamically via `[tool.setuptools.dynamic] version = {attr = "claude_code_sessions.__version__"}`, so bumping the module is the whole change.

- [ ] **Step 1: Update the README**

Change `Eight commands.` (`README.md:125`) to `Nine commands.` and add, after the `repoint` section:

```markdown
**`new-row`** — create a sidebar row for a conversation that has none:

```
ccs new-row --to 174eb7c1-879f-4f0e-abff-8fdc7210f3d9
```

`repoint` moves an existing row and `sync` copies one between accounts; neither can
make one where none exists. A conversation whose row was overwritten, or never copied,
is on disk and unopenable — 170 such transcripts were measured on one machine.
`doctor` lists them; this turns one back into a sidebar entry.

The title comes from `--title`, or the transcript's own title if it has one, or a
placeholder like `(untitled — 2026-06-14, 181 turns, Personal)` — deliberately not a
summary, because a machine-made title that reads like one is worse than an obvious
placeholder.

**Unofficial and unverified across app versions.** The app accepting a row it did not
create was established by experiment, not documentation. The row is built from fields
measured across 987 real rows plus values read out of the transcript itself, and it
asserts nothing beyond those. If a future version rejects one, `doctor` reports it —
but only while the creating operation is still in the journal, which `recover`'s
rotation bounds. A row removed long after it was made will go unreported.
```

- [ ] **Step 2: Add an internals section**

Append to `docs/internals.md` a `## Creating a listing row` section recording: that the store has no index (the app enumerates the directory); the 2026-08-22 census — 987 rows, 52 distinct keys, only 12 universal — and the four fields it disqualified; that `model` and `effort` are read from the transcript rather than defaulted, with the observed `claude-fable-5` 522 / `claude-opus-5` 243 split that killed the hardcoded default; that `lastFocusedAt` is rewritten by the app on first focus; and the journal-retention bound on doctor's detection.

- [ ] **Step 3: Bump the version**

`claude_code_sessions.py:25`: `__version__ = "0.10.0"` — a new command is a feature, not a fix, and this is the first command that adds a sidebar entry from nothing.

- [ ] **Step 4: Run the whole suite**

```bash
for t in tools/check_*.py; do python "$t" >/dev/null 2>&1 && echo "PASS $t" || echo "FAIL $t"; done
```

Then: `python claude_code_sessions.py --version`
Expected: thirteen `PASS` lines, and `claude-code-sessions 0.10.0`

- [ ] **Step 5: Commit**

```bash
git add README.md docs/internals.md claude_code_sessions.py
git commit -m "docs(new-row): README, internals, and 0.10.0

A new command that adds a sidebar entry from nothing is a feature and the
first of its kind in this tool, so it takes the minor version. pyproject
reads __version__ dynamically, so the module is the whole change.

The README states both limits where users will read them: the app accepting
a row it did not create was established by experiment, and doctor's
detection of a rejected row is bounded by journal rotation."
```

---

## Revisions

*Internal review scaffolding. This is a working plan, not a deliverable, so the block stays — but it records review history, not design, and adds nothing for an implementer.*

**Round 1 (2026-08-22) — panel: Codex, Gemini (agy), DeepSeek reported; Kimi INCOMPLETE (transport failure, no review text).** Applied:

- **The row template was rebuilt from a census, not a guess.** The draft asserted `reportFindingsCard`, `chromeTabGroupId`, `lastSpawnRootDetected` and `remoteControlAutoEligible` on every synthesized row; measured across 987 real rows they appear on 60.2%, 5.6%, 2.7% and **0.9%**. Task 1 now opens with the census that disqualified them.
- **`model` and `effort` are derived from the transcript.** The draft hardcoded `claude-opus-5` as "the account default"; the census says `claude-fable-5` leads it 522 to 243.
- **`titleSource` is truthful** — `user` only for `--title`, `auto` otherwise (533 of 537 real rows say `auto`).
- **`turns` is nullable.** `_message_fingerprints` returns `None` above its 96 MB cap, so `len(fps or [])` would have written `completedTurns: 0` on the largest conversations. Found while checking the panel's claims; no engine raised it.
- **The reachability check moved under the lock and now fails closed** — it ran before `acquire_lock` and skipped unparseable rows.
- **`execute_new_row_op` re-verifies the transcript** is still the same single file, not merely present.
- **`recover --back` re-establishes containment before unlinking, and records residue** when it cannot safely remove a row; `cmd_recover` prints it.
- **`_placeholder_title` uses UTC**, matching `_iso_ms`.
- **`--verbose` removed** (accepted, never read). **`doctor` checks the transcript** instead of asserting it survives. Test count corrected to 12 existing / 13 after. `pyproject.toml` dropped from Task 8 — it reads `__version__` dynamically.

Recorded as **limits rather than fixed**, both with reasons in the self-review: no test can prove a real app version accepts the row (the 2026-08-22 prototype is the evidence); `doctor`'s detection is bounded by `rotate_ops` retention, and a durable fix needs a standalone registry with its own retention and migration questions.

**Rejected:** DeepSeek reported `classify_op:1751` and `recover_op:1952` as stale references. Its tool trace shows it reading the module by **byte** offset in 40–120 byte windows — it compared line numbers to byte offsets. Both verified correct directly.

**Round 2 (2026-08-23) — panel: all four reported (Codex, Gemini, DeepSeek, Kimi).** Most findings were defects in round 1's *fixes*, which is what the round-2 contract is for. Applied:

- **Blocker `[Codex]` — `recover --back` deleted a row with no running-app guard.** Verified: `recover_op` calls `_guard_mutation` for **no** op type at all. Crashing, reopening the app, then recovering backward would have deleted a row out from under a running app — the exact race the command otherwise refuses to run into. `_guard_mutation` added to the `back` arm, with tests on both directions.
- **Blocker `[Kimi]` — `recover --forward` refused on a write that had already succeeded.** The re-validation added in round 1 re-checked a transcript whose facts were already consumed and committed to the bytes on disk, so a transcript that had since aged out made *recover* — the unstick-me command — refuse to finish bookkeeping. `execute_new_row_op` now returns early when `_sync_row_drift(r) == "match"`: the row matching byte for byte is better evidence than any re-derivation. That arm also skips the mutation guard, correctly, because it writes only the journal.
- **Blocker `[Gemini]` — `os.path.basename` on a Windows cwd under POSIX.** The cwd comes out of a transcript, not this filesystem, so a store synced from Windows makes the "placeholder" an absolute path on macOS, and the test asserting `Personal` fails on any POSIX runner. Now `re.split(r"[\\/]", ...)`, with both path shapes pinned.
- **Major `[Codex]` — the store heuristic wrote before showing its reasoning.** Round 1 justified the populated-directory guess by saying the user sees it before `--apply`; `cmd_new_row` printed the report *after* `run_new_row`. Since no saved dry run can be handed back to the CLI, a separate dry run proves nothing about what the apply will choose. The human report now prints first (`--json` keeps the apply-first order for the reason that ordering exists).
- **Major `[Codex+Kimi]` — the transcript re-check compared pathnames, not contents.** Same path, different bytes was invisible, leaving the row's timestamps and turn count describing a version that no longer existed. `transcript_size` and `transcript_mtime` are journalled and re-checked; size+mtime rather than a hash because these files run to 96 MB and an append moves both.
- **Major `[Codex]` — safe refusals left unresolved operations.** `run_new_row` journalled before the checks, so a perfectly safe refusal left a non-terminal op and `doctor` told the user to `recover` a command that had touched nothing. The checks are now `_new_row_preflight`, called before `new_op`.
- **Major `[Codex]` — the "universal fields only" rule was stated as absolute and then broken three times** (99.7%, 97.6%, 95.7%). Replaced with the actual three-tier policy: transcript-derived, then ≥95% *with a defensible zero value*, then omitted — with `permissionMode` called out as the one compatibility choice rather than a measurement.
- **Major `[Kimi]` — `classify_op` reported a drifted row as "was not written".** `== "match"` collapsed five states into two, so a row the app had reopened and rewritten read as never written — in the direction that invites a careless `forward`. All five states now map.
- **Major `[Kimi]` — `cmd_new_row` returned 0 unconditionally** with a success trailer. `cmd_move` and `cmd_sync` gate on the result; `cmd_repoint` does not, and it was the sibling copied.
- **Major `[DeepSeek]` — `cwd` is first-wins while `model`/`effort` are last-wins**, undocumented and untested. Both the reason and a test pinning the asymmetry added.
- **Minor `[Kimi]` — `doctor` used `bool(find_transcripts(...))`,** so it advised `new-row --to <id>` for a conversation now in two project folders, where that command refuses. Now counted, with a third message for the ambiguous case.
- **Minor `[Kimi]` — the orphan counts were not reproducible** from the census script, violating this plan's own Global Constraint. The query is now in Task 1 — and re-running it gave **170/50/120**, not the spec's 169/121, because a session was created overnight. Numbers corrected and flagged as drifting.
- **Minor `[DeepSeek]` — the `ValueError` arm of `_row_already_opens` is unreachable** (`read_json` converts it to `LayoutError`). Kept as defence with a comment saying so, rather than left as silent dead code.

**Also corrected:** the self-review claimed `_sync_row_drift` has four states; it has **five** (`match`, `pristine`, `absent`, `drifted`, `unreadable`).

**Nothing rejected this round.** Every finding was verified against the code before being applied.

**Round 3 (2026-08-23) — panel: Codex and Gemini reported; roster unavailable (DeepSeek and Kimi both INCOMPLETE, same transport failure, both aborted after one retry — one outage, not two independent voices).** Two blockers, both in round-2 fixes. Applied:

- **Blocker `[Codex+Gemini]` — the round-2 path fix broke the root-directory case it inherited.** `re.split(r"[\\/]", "C:\\".rstrip("\\/"))[-1]` is `"C:"`, which is truthy, so a root cwd appended `C:` to the title and Task 2's own test would have failed on the first run. `ntpath.basename` had returned `""` and hidden it. Verified across six path shapes: `C:\`→`C:`, `D:\`→`D:`, `/`→`""`, `\\server\share`→`share`. Bare drive letters are now dropped explicitly, with all four cases tested — including the UNC share, which *is* a real leaf and is kept.
- **Blocker `[Gemini]` — `_new_row_preflight` was called twice, reinstating the defect round 2 removed.** Moving it before `new_op` fixed nothing while `execute_new_row_op` still called it: a refusal there is raised *after* the journal entry exists, which is exactly what left non-terminal ops behind. It now has one caller per path — `run_new_row` before journalling, `recover_op`'s forward arm before re-entry, and only for a row that did not land.
- **Major `[Codex]` — the transcript snapshot was captured after its own read**, so an append *during* fact-gathering became the accepted baseline and the "same path, different bytes" check validated stale facts. `_transcript_facts` now stats before and after and refuses if they disagree; the manifest records the stat from `facts`, never a fresh one.
- **Major `[Gemini]` — `customTitle` was first-wins**, resurrecting titles users had already replaced. Confirmed against real data: **47 of 507 transcripts (9%) carry more than one distinct `customTitle`**, one going `"Task manager performance audit"` → `"... (fork)"`. Now last-wins, like `model` and `effort`; `cwd` stays first-wins and the comment says why.
- **Major `[Codex]` — `classifierSummaryEnabled: True` violated the field policy** — it is behaviour, not a zero, and clears the 95% bar, so a threshold alone let it through. Omitted, and the policy now says explicitly that clearing the bar is necessary and not sufficient.
- **Major `[Codex]` — the store guess printed and then wrote anyway.** In a one-shot `--apply` there is no moment between the print and the write for anyone to intervene, and no saved plan to approve, so "the user sees it first" was never a safeguard. `_new_row_store` now returns a `heuristic` flag; the guess may plan and may not write, and `--apply` refuses naming the `--store <path>` that settles it.
- **Major `[Codex+Gemini]` — `doctor` could never clear its own alert.** It judged a row vanished from one path being absent, but taking doctor's advice mints a fresh uuid, so that path stays absent forever while the suggested command starts refusing (a row already opens it). A legitimate user deletion looked identical. It now asks the store whether the account can still open the conversation.
- **Major `[Codex]` — generated-title uniqueness was computed unlocked and discarded.** The preflight took `hit, _ =` and threw the title set away, so a row created since planning could take the chosen suffix. Re-checked under the lock; an explicit `--title` duplicate still writes, because that was the user's own call.
- **Major `[Gemini]` — `_guard_mutation` ran up to three times per apply**, each enumerating the process list. Down to one: the preflight owns it.
- **Major `[Gemini]` — forcing `recover --forward` on a drifted row said "a different row already exists … Nothing was written",** which is false twice over — it is our row, and we did write it. `_row_is_ours` distinguishes a uuid4 collision from the app having rewritten our own row.
- **Documented rather than changed `[Gemini]`:** a transcript with no assistant reply is refused, because `model` is on 100% of real rows and has no zero value. The refusal now names that specific case, and the comment records it as a deliberate trade whose fix, if it ever bites, is a `--model` flag rather than a silent default.

**Nothing rejected.** Every finding verified before applying — the root-path one by running all six path shapes, the `customTitle` one against all 507 transcripts on disk.

**Loop closed at the 3-round cap.** Rounds 1 and 2 each found real defects in the previous round's fixes, and round 3 did too — so the honest reading is that this plan is *better reviewed*, not *proven clean*. What remains open is recorded below.

## Self-review

**Spec coverage.** Command surface → Task 6. Field derivation and the census → Task 1. Title derivation, provenance, collision → Task 2. Refusals → Task 3 (reachability incl. unreadable rows, missing/ambiguous transcript, unusable transcript, ambiguous store, zero turns allowed) and Task 4 (transcript gone, moved, or duplicated between plan and apply; another writer got there first). Journal/undo/lock → Tasks 3–4. `--json` filter → Task 6. `recover`/`classify_op` → Task 5. `doctor` detection → Task 7. Known limits in docs → Task 8. RULING 4 and RULING 5 come free via `_repoint_store` and `_guard_mutation`.

**Checked against the code, not assumed.** `_repoint_store` returns a **list**, deliberately — hence `_new_row_store`. `gather_doctor`'s dict is `report` at `:2305`; `cmd_doctor` calls it `rep`. `classify_op`'s `repoint` branch is first, at `:1751`. `_sync_row_drift` returns **five** states — `match`, `pristine`, `absent`, `drifted`, `unreadable` — not four as an earlier draft said; Task 4's undo refuses every non-`match` one, and Task 5's `classify_op` note maps all five; `_row_is_refresh` returns False for a new-row row, so drift compares post bytes only. `_message_fingerprints` returns **None** above `TRANSCRIPT_COMPARE_MAX_BYTES` (96 MB) — hence `turns` is `None`, never `0`. `README.md:125` says `Eight commands.` `tools/` holds 12 suites, so this adds the 13th. `pyproject.toml` needs no edit.

**Type consistency.** `_transcript_facts` returns the dict consumed by `_synthesize_row`, `_placeholder_title` and `_new_row_title` — keys `path`, `cwd`, `created_ms`, `last_ms`, `turns`, `custom_title`, `model`, `effort`, `size`, `mtime` throughout, with `turns` nullable. `_new_row_title` returns a **3-tuple**; `plan_new_row` passes the third element to `_synthesize_row` as `title_source` and derives `_unique_title`'s `generated` from the second. `_new_row_store` returns a **5-tuple** (`acct, org, path, why, heuristic`). `_row_already_opens` returns `(name|None, set)` and is called for both halves in the preflight. `_row_is_ours` returns a bool and never raises. Manifest key `name` holds the bare uuid; `rows[0]["name"]` holds the full `local_<uuid>.json` filename.

**Still open, and deliberately so** — the three-round loop ended at its cap, not at proof:

- **No test opens a real app version.** The command's central premise — that the app accepts a row it did not issue — rests on the 2026-08-22 prototype. `doctor` plus the README warning are the mitigation, not a solution.
- **`doctor`'s detection is bounded by `rotate_ops` retention.** It covers the window a tombstoning version would act in and nothing after. A durable answer needs a standalone registry with its own retention and migration questions.
- **Second-resolution mtime.** A rewrite that preserves both size and second-granularity mtime evades the snapshot check. Hashing would close it and costs a full re-read of files that reach 96 MB; the trade is recorded rather than taken.
- **The store heuristic still exists**, it just cannot write any more. An account whose only store is legitimately empty needs `--store <path>`.

**Two findings deliberately not acted on, with reasons:**
- *"No test opens the real app to prove the row is accepted"* (Codex, critical). True, and it cannot be fixed by a test in this suite — the prototype on 2026-08-22 is the evidence, and Task 7 plus the README are the mitigation. Recorded as the command's headline limit rather than treated as a solved problem.
- *"doctor's detection should use a persistent registry rather than the rotating journal"* (Gemini, highest-impact). The diagnosis is right and the fix is a new on-disk structure with its own retention, corruption and migration questions. Out of scope for this plan; stated as a limit in Tasks 7 and 8 so it is known rather than rediscovered.

**Two findings rejected as wrong:** DeepSeek reported that `recover_op`'s cited line `:1952` and `classify_op`'s `:1751` "point at store-directory code". Its own tool trace shows it reading `claude_code_sessions.py` by **byte** offset in 40–120 byte windows — it compared line numbers against byte offsets. Both line numbers were verified directly and are correct.
