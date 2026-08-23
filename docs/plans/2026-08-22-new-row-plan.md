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
- **Assert nothing you did not measure.** This is the rule the whole command turns on. A field goes in the row only if it was *derived from the transcript* or *measured as universal across real rows* (Task 1). "Plausible default" is not a justification — it is the specific failure this command must not commit.
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
  - `_transcript_facts(env, session_id) -> dict` with keys `path` (str), `cwd` (str), `created_ms` (int), `last_ms` (int), `turns` (int **or None**), `custom_title` (str|None), `model` (str|None), `effort` (str|None). Raises `Refusal`.
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

Measured 2026-08-22 over 987 rows: **52 distinct keys, and only 12 appear on every row.** There is no fixed row shape, so the template is the *universal* set plus what the transcript can settle — nothing else. Specifically:

| Field | Presence | Decision |
|---|---|---|
| `sessionId` `cliSessionId` `cwd` `originCwd` `createdAt` `lastActivityAt` `title` `isArchived` `model` `permissionMode` `chromePermissionMode` `alwaysAllowedReasons` | 100% | required — include |
| `sessionPermissionUpdates` | 99.7% | include (`[]`) |
| `effort` | 99.4% | include, **derived from the transcript** |
| `lastFocusedAt` | 100% of rows that have `completedTurns`; 95% overall | include |
| `classifierSummaryEnabled` | 97.6%, and `True` on every row that has it | include (`True`) |
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
                if cwd is None and isinstance(d.get("cwd"), str) and d["cwd"]:
                    cwd = d["cwd"]
                if custom is None and isinstance(d.get("customTitle"), str) \
                        and d["customTitle"].strip():
                    custom = d["customTitle"].strip()
                # LAST of each, not first: what the session was running when it
                # stopped is what a resumed row should carry.
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
        missing.append("no model on any assistant record")
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
    return {"path": path, "cwd": cwd, "created_ms": first_ms, "last_ms": last_ms,
            "turns": len(fps) if fps is not None else None,
            "custom_title": custom, "model": model, "effort": effort}
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
    "classifierSummaryEnabled": True,
    "permissionMode": "auto",
}
check("the row has exactly the expected fields, no more and no fewer",
      set(row) == set(EXPECTED),
      "extra=%s missing=%s" % (sorted(set(row) - set(EXPECTED)),
                               sorted(set(EXPECTED) - set(row))))
for k in sorted(EXPECTED):
    if k in row:
        check("  %s" % k, row[k] == EXPECTED[k], "%r != %r" % (row.get(k), EXPECTED[k]))

# The four fields an earlier draft asserted on every row. Measured 2026-08-22
# across 987 real rows, they appear on 60.2%, 5.6%, 2.7% and 0.9% of them.
for absent in ("reportFindingsCard", "chromeTabGroupId", "lastSpawnRootDetected",
               "remoteControlAutoEligible"):
    check("  %s is omitted - the census does not support it" % absent,
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
    "classifierSummaryEnabled": True, # 97.6%, and True on every row that has it
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

    Used for the 121 of 169 orphans measured on 2026-08-22 that carry no
    customTitle of their own. The app's titles are model-written summaries, so
    nothing mechanical can produce one - and a machine-made title that LOOKS
    like a summary is the failure this whole command is careful about. This one
    is identifying, sortable, and visibly not a summary.

    UTC, matching _iso_ms. time.localtime here would render the same
    conversation as two different dates on two machines, and the row's own
    createdAt - which IS UTC - would disagree with its title.
    """
    day = time.strftime("%Y-%m-%d", time.gmtime((facts["last_ms"] or 0) / 1000.0))
    leaf = os.path.basename((facts.get("cwd") or "").rstrip("\\/"))
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
that does not impersonate a summary. Measured 2026-08-22: 121 of 169
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
  - `_new_row_store(env, flags) -> (acct, org, path, why)` — `why` is a human sentence naming how the store was chosen
  - `_row_already_opens(store, session_id) -> (name | None, titles set)` — **fails closed**
  - `NewRowFlags` dataclass: `to_session: str = ""`, `store: str = ""`, `title: str = ""`, `live: str = ""`
  - `plan_new_row(env, flags) -> manifest` with keys `op_type="new-row"`, `store_path`, `store_label`, `store_why`, `name`, `row_path`, `title`, `title_provenance`, `title_collision`, `to_session`, `transcript`, `transcript_mb`, `turns`, `cwd`, `model`, `rows=[{name, dest_path, title, pre_b64: None, post_b64, is_update: False, written: False}]`

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

# the cross-pair: one account, two org directories, only one of them real
root, env, live, dorm = build([OPENER] + prose(8))
os.makedirs(os.path.join(os.path.dirname(live), ORG_D))    # empty scaffolding
with open(os.path.join(live, "local_anything.json"), "w") as fh:
    json.dump({"cliSessionId": "%032d" % 55, "title": "Something else",
               "cwd": "proj", "lastActivityAt": 1}, fh)
m = ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID, store="live@example.com"))
check("an email matching two org dirs picks the one holding rows",
      os.path.realpath(m["store_path"]) == os.path.realpath(live), m["store_path"])
check("  and says so, because a heuristic the user cannot see is a trap",
      "rows" in m["store_why"], m["store_why"])
shutil.rmtree(root, ignore_errors=True)

# every candidate empty: name them rather than saying 'no way to tell'
root, env, live, dorm = build([OPENER] + prose(8))
os.makedirs(os.path.join(os.path.dirname(live), ORG_D))
try:
    ccs.plan_new_row(env, ccs.NewRowFlags(to_session=SID, store="live@example.com"))
    check("all-empty candidates refuse by naming them", False, "no refusal")
except ccs.Refusal as exc:
    check("all-empty candidates refuse by naming them", ORG_D[:8] in str(exc)
          or ORG_D in str(exc), str(exc)[:120])
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
    """(acct, org, path, why) - exactly one store, and how it was chosen.

    `_repoint_store` returns a LIST, and refusing whenever it returns more than
    one would refuse the ordinary case: an account owns one directory per org, so
    naming it by email matches all of them. `plan_repoint` resolves that by
    letting the ROW settle which store is meant - a luxury this command does not
    have, because the row is what it is about to create.

    So let the CONTENT settle it: the cross-pair directories are empty
    scaffolding, and the account's real store is the one with rows in it.

    Know what that heuristic costs, because it is a guess and not a fact: a
    genuinely new or deliberately empty organization loses to an old populated
    one, and this function would pick the wrong store confidently. Two things
    contain that. It refuses whenever more than one candidate holds rows, and it
    RETURNS ITS REASONING, which `_print_new_row_report` prints - so the dry run
    shows which store was chosen and why before anything is written. A heuristic
    the user can see is a very different thing from one they cannot.
    """
    hits = _repoint_store(env, flags)
    if len(hits) == 1:
        a, o, p = hits[0]
        return a, o, p, "the only store matching what you named"
    populated = []
    for a, o, p in hits:
        rows = [n for n in _listdir_or_refuse(p, "an account directory")
                if n.startswith("local_") and n.endswith(".json")]
        if rows:
            populated.append((a, o, p, len(rows)))
    listing = "\n".join("   " + p for _, _, p in hits)
    if not populated:
        raise Refusal(
            "--store {0!r} matched {1} directories and none of them holds any "
            "rows, so nothing distinguishes them. Name one by path:\n{2}"
            .format(flags.store, len(hits), listing))
    if len(populated) > 1:
        raise Refusal(
            "--store {0!r} matched {1} directories that each hold rows; refusing "
            "to guess which should get the new one:\n{2}".format(
                flags.store, len(populated),
                "\n".join("   {0}  ({1} rows)".format(p, n)
                          for _, _, p, n in populated)))
    a, o, p, n = populated[0]
    return a, o, p, ("the only one of {0} matching directories that holds any "
                     "rows ({1} of them)".format(len(hits), n))


def _row_already_opens(store, session_id):
    """(name of a row opening session_id or None, every title in the store).

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
        try:
            d = read_json(os.path.join(store, name))
        except (LayoutError, OSError, ValueError) as exc:
            raise Refusal(
                "the row {0!r} in this store could not be read ({1}), so this "
                "command cannot tell whether it already opens {2}. Refusing "
                "rather than risk a second row for the same conversation. "
                "Nothing was written.".format(name, exc, session_id[:8]))
        if not isinstance(d, dict):
            raise Refusal("the row {0!r} is not a JSON object; refusing to add "
                          "a row beside it. Nothing was written.".format(name))
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
    169 transcripts on one machine were reachable from no row in any account.
    """
    if not flags.to_session:
        raise Refusal("--to is required: the cliSessionId the new row should open. "
                      "'doctor' lists conversations that no account points at.")
    acct, org, store, why = _new_row_store(env, flags)
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
            "store_why": why, "name": row_uuid,
            "row_path": os.path.join(store, name),
            "title": title, "title_provenance": provenance,
            "title_collision": collision,
            "to_session": flags.to_session, "transcript": facts["path"],
            "transcript_mb": round(os.path.getsize(facts["path"]) / 1e6, 1),
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
            env, ccs.NewRowFlags(to_session=SID, store=dorm))),
        "no transcript on disk")
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
def execute_new_row_op(env, op):
    """journaled -> writing -> completed. One row, created from nothing.

    Re-entrant from 'writing' for the same reason execute_repoint_op is: an op
    that died between the two set_status calls must still be finishable, and
    every decision here is made from the bytes on disk rather than from what the
    journal expects to find.

    THE CHECKS BELOW ARE THE REAL GUARDS, not the ones in plan_new_row. Planning
    runs unlocked, so between a dry run and --apply another writer can create a
    row for this conversation, the transcript can age out, or a duplicate
    transcript can appear. Re-checking here, under the lock, is what makes the
    dry run's promises true at the moment of writing.
    """
    m = op.manifest
    if m.get("status") not in ("journaled", "writing"):
        raise LayoutError("execute_new_row_op runs ops from 'journaled' or "
                          "'writing'; this one is " + str(m.get("status")))
    _guard_mutation(env, "create a row in")
    if not os.path.isdir(m["store_path"]):
        raise LayoutError("store vanished: " + m["store_path"])
    # The transcript must still be THE one this row was planned against - not
    # merely some transcript with that id. A duplicate appearing in another
    # project folder makes the planned facts describe a file this row no longer
    # uniquely names.
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
    # Re-check reachability HERE, under the lock. plan_new_row's identical check
    # is for the dry run's benefit and closes no race at all.
    hit, _ = _row_already_opens(m["store_path"], m["to_session"])
    if hit and hit != m["rows"][0]["name"]:
        raise Refusal(
            "{0} now already opens {1} (row {2!r}) - something created it since "
            "this was planned. Nothing was written.".format(
                m["store_label"], m["to_session"][:8], hit))
    set_status(op, "writing")
    r = m["rows"][0]
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
            # A fresh uuid4 filename that already exists means something is
            # badly wrong. Never overwrite: this command adds, it does not edit.
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
    _guard_mutation(env, "create a row in")
    acquire_lock(env, "new-row")
    try:
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
        landed = _sync_row_drift(r) == "match"
        return {"status": m["status"], "source": m.get("store_label", "n/a"),
                "dest": m.get("store_label", "n/a"),
                "resolutions": ["forward", "back"], "drifted_rows": [],
                "note": "new-row: the row {0}; forward finishes creating it, "
                        "back removes it if it matches what this op wrote"
                        .format("was written" if landed else "was not written")}
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
                set_status(op, "journaled")
                final = execute_new_row_op(env, op)
                rotate_ops(env)
                return final
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
    # --apply runs BEFORE --json prints, exactly as cmd_sync and cmd_repoint do:
    # printing first meant `--apply --json` reported a plan, exited 0, and wrote
    # nothing, which automation reads as a completed operation.
    final = run_new_row(env, m) if ns.apply else None
    if ns.json:
        pub = _public_new_row_manifest(m)
        if final is not None:
            pub["result"] = final
        print(json.dumps(pub, indent=1))
        return 0
    _print_new_row_report(print, m)
    if final is None:
        print("\ndry run - pass --apply to create the row")
        return 0
    print("\nresult  : {0}".format(final))
    print("Reopen the app - the session should be in the sidebar. 'undo' removes "
          "the row again.")
    return 0
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
- Produces: `gather_doctor`'s `report` gains `vanished_new_rows: [{op_id, title, to_session, store_label, transcript_present}]`

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
check("  having actually checked the transcript rather than assuming it",
      v["transcript_present"] is True)
check("  and doctor's exit code reflects the anomaly", d["exit_code"] != 0,
      str(d["exit_code"]))
os.unlink(m["transcript"])
d = ccs.gather_doctor(env)
check("  a vanished row whose transcript ALSO went says so",
      d["vanished_new_rows"][0]["transcript_present"] is False)
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
            vanished_new.append({
                "op_id": _m.get("op_id"), "title": _m.get("title"),
                "to_session": _m.get("to_session"),
                "store_label": _m.get("store_label"),
                # Checked, not assumed. The diagnostic below tells the user the
                # conversation is still there and offers to recreate the row;
                # saying that without looking would send them at a file that
                # may also be gone.
                "transcript_present": bool(find_transcripts(
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
        if v["transcript_present"]:
            say("[hypothesis]   the app removed a row it did not issue - the one "
                "documented risk of 'new-row'. The conversation ({0}) is still "
                "on disk; 'new-row --to {0}' makes another."
                .format(v["to_session"]))
        else:
            say("[observed]     the conversation ({0}) is gone from disk too, so "
                "this is retention catching up rather than the app rejecting a "
                "row. Nothing to recreate.".format(v["to_session"]))
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
is on disk and unopenable — 169 such transcripts were measured on one machine.
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

## Self-review

**Spec coverage.** Command surface → Task 6. Field derivation and the census → Task 1. Title derivation, provenance, collision → Task 2. Refusals → Task 3 (reachability incl. unreadable rows, missing/ambiguous transcript, unusable transcript, ambiguous store, zero turns allowed) and Task 4 (transcript gone, moved, or duplicated between plan and apply; another writer got there first). Journal/undo/lock → Tasks 3–4. `--json` filter → Task 6. `recover`/`classify_op` → Task 5. `doctor` detection → Task 7. Known limits in docs → Task 8. RULING 4 and RULING 5 come free via `_repoint_store` and `_guard_mutation`.

**Checked against the code, not assumed.** `_repoint_store` returns a **list**, deliberately — hence `_new_row_store`. `gather_doctor`'s dict is `report` at `:2305`; `cmd_doctor` calls it `rep`. `classify_op`'s `repoint` branch is first, at `:1751`. `_sync_row_drift` has a fourth state, `"unreadable"`, which Task 4's undo refuses through its non-`"match"` arm; `_row_is_refresh` returns False for a new-row row, so drift compares post bytes only. `_message_fingerprints` returns **None** above `TRANSCRIPT_COMPARE_MAX_BYTES` (96 MB) — hence `turns` is `None`, never `0`. `README.md:125` says `Eight commands.` `tools/` holds 12 suites, so this adds the 13th. `pyproject.toml` needs no edit.

**Type consistency.** `_transcript_facts` returns the dict consumed by `_synthesize_row`, `_placeholder_title` and `_new_row_title` — keys `path`, `cwd`, `created_ms`, `last_ms`, `turns`, `custom_title`, `model`, `effort` throughout, with `turns` nullable. `_new_row_title` returns a **3-tuple**; `plan_new_row` passes the third element to `_synthesize_row` as `title_source` and derives `_unique_title`'s `generated` from the second. `_new_row_store` returns a **4-tuple**. `_row_already_opens` returns `(name|None, set)`. Manifest key `name` holds the bare uuid; `rows[0]["name"]` holds the full `local_<uuid>.json` filename.

**Two findings deliberately not acted on, with reasons:**
- *"No test opens the real app to prove the row is accepted"* (Codex, critical). True, and it cannot be fixed by a test in this suite — the prototype on 2026-08-22 is the evidence, and Task 7 plus the README are the mitigation. Recorded as the command's headline limit rather than treated as a solved problem.
- *"doctor's detection should use a persistent registry rather than the rotating journal"* (Gemini, highest-impact). The diagnosis is right and the fix is a new on-disk structure with its own retention, corruption and migration questions. Out of scope for this plan; stated as a limit in Tasks 7 and 8 so it is known rather than rediscovered.

**Two findings rejected as wrong:** DeepSeek reported that `recover_op`'s cited line `:1952` and `classify_op`'s `:1751` "point at store-directory code". Its own tool trace shows it reading `claude_code_sessions.py` by **byte** offset in 40–120 byte windows — it compared line numbers against byte offsets. Both line numbers were verified directly and are correct.
