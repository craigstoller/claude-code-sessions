# relink: give rows already in place the parent link another account carries - implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A new `relink` command adds `spawnedFrom` (and `startChain` beside it) to existing rows
of a side session that lack the link another account's row carries, translated exactly as
0.18.0's `converge` would write it, with nothing else in the row changed, journaled per key and
undoable per key.

**Architecture:** One pure planning pass (`_relink_plan_rows`) over the strict scan judges each
linked conversation across every account (D12), finds the candidates (D5), resolves each with
0.18.0's `_lineage_resolve` against the account's existing rows, applies the provenance rule (D11)
and `_lineage_drop_cycles`, and yields rows to write and rows left out. `plan_relink` wraps it with
the scan, D16 to D18, identity and narrowing. `run_relink` re-plans under the lock and writes
each row from its found value with only the two keys set, journaling hashes and the two keys'
old and new values, never images (D15). `undo_relink` and `_recover_relink` work per key and read
the disk, not the flags alone; other operations' ownership checks learn the new op type.

**Tech Stack:** Python 3.9+, standard library only. One module, `claude_code_sessions.py`, and the
GUI module `claude_code_sessions_gui.py`. Tests under pytest (`tests/`), standalone checks in
`tools/check_*.py`.

**Spec:** `docs/specs/2026-11-19-relink-design.md`. The maintainer's rulings D1 to D3 (D2 as
amended) and the author's calls D4 to D19 are requirements. Read its "Decisions" and "Design"
sections before any task. Where this plan and the spec disagree, the spec wins and the
disagreement is a defect to report, except the departures listed in "Departures from the spec"
at the end.

**Status:** reviewed in three rounds (Codex, agy, and the repo-aware DeepSeek and Kimi; see
Revisions). Round 3's fixes are applied and **not re-reviewed**, the review loop's cap. Not built.

## Global Constraints

- **One module.** Production code goes in `claude_code_sessions.py`, in a new section
  `# ====== relink` placed after `cmd_converge` and before `if __name__ == "__main__":` (about line
  18936), apart from the two lineage tables (Task 1), the parser and `main`'s handler (Task 6),
  the ownership checks (Task 7), the undo and recovery dispatch (Tasks 8, 9) and one GUI line
  (Task 8). Do not split either module.
- **Anchors.** Line numbers ("about line N") were taken before the transcript index reached
  `main`, which added about 170 lines to the module, and they drift further as tasks land. Each
  anchor also names its symbol, and the symbol wins.
- **Line endings.** Tracked files are LF in git and CRLF in this working copy
  (`core.autocrlf=true`; `git ls-files --eol` shows `i/lf w/crlf` for the modules, README, internals
  and tests; the spec file is `w/lf`). Keep each file's working-copy endings and compare files by
  committed blob.
- **Fake cast only** in tests, docs and messages: Northwind, ACME-REVIEW, alice@ and
  bob@example.com, `local_c2`, `local_p3`, the A/O/LP/LC ids of `tests/test_converge.py`. No real id,
  email, title or path anywhere in the child repo.
- **Refusals are `Refusal`** (exit 1); an unsafe layout or a failed verify is `LayoutError` (exit 2,
  `main`'s rule for every command). Every function `classify_op`, `doctor`, a claim check or a
  report calls never raises.
- **The writer bound.** The guarantee holds while the desktop app stays closed (`_guard_mutation`)
  and this tool's lock is held. An app started during an apply could write a row between relink's
  read and its replace; nothing here can see that. The run sheet's instruction covers it.
- **Identity.** A row is `(account, name)`; its row id is the filename stem. A conversation is a
  `cliSessionId`. The link graph is per account over conversations, as 0.18.0's.
- **The written link** is exactly `{"sessionId": <B's row for the parent>, "taskId": <copied>}`; the
  written chain exactly `{"firstSessionId": <B's row for the root>, "depth": <int >= 1>}` - the
  resolver's output, unchanged (D7, anti-goal 1).
- **App-written (D11)**, `_relink_app_written(link)`: a dict with `sideSession` exactly `True` (any
  other keys), or a dict whose keys are exactly `{"sessionId", "taskId", "title"}` with `title` a
  non-empty string. Nothing else, this tool's `{sessionId, taskId}` included.
- **Missing (D5):** a key is missing when absent or present as a non-object (`None`, `False`, a
  string, a list, a number). A present object is never replaced.
- **Row bytes.** `_relink_row_bytes(d) = json.dumps(d, separators=(",", ":")).encode("utf-8")`, the
  serialization `realign`, `retitle` and `repoint` write. A key present keeps its position; an
  absent key is appended; keys are set one at a time (`_relink_apply_new`), never through
  `_lineage_rewrite_post`, which pops a key given `None`.
- **Journal fields, verbatim** (`op_type: "relink"`, `lineage_format: 1`):
  - manifest: `op_type`, `lineage_format`, `only`, `account_filter`, `live_asserted`,
    `lineage_cycles`, `rows`, `left_out`, `identity_disagreement` (only when the files disagree),
    `since_plan` (`{"changed": int, "dropped": int, "gained": int}`, set at apply), `undo_left`
    (set by undo), `rolling_back` (bool `True`, set and saved by `back` before its first write;
    Task 9), `back_left` (a list of entries shaped as `undo_left`'s) and `back_restored` (an int,
    the rows whose `back_keys` is not empty), both kept and saved by `back` as it goes (Task 9),
    plus `new_op`'s `op_id`, `status`, `history`. `live_asserted` holds the asserted account's
    uuid (`live.account_uuid`) when `--live` arbitrated a disagreement
    (`live.resolved_from == "user"`), else `""`, exactly as `converge` and `realign` store it;
    `_identity_warning_lines` reads its truthiness, and `_relink_recheck` passes it back as
    `--live`, which `_resolve_live_assertion` resolves at account scope by that uuid, as
    `_realign_recheck` does;
  - per row to write: `account`, `label`, `name`, `dest_path`, `store_path`, `session_id`, `title`,
    `parent_title`, `pre_sha256`, `post_sha256` (64 lowercase hex), `old` (`{key: {"present": bool,
    "value": <non-object or None>}}` for each written key), `new` (`{key: value}` for each written
    key), `kept` (`{key: value}` for each of the two keys the row already had as an object), `lineage`
    (0.18.0's record shape), `written` (bool, `False` at plan). `title` and `parent_title` are
    strings: a title that is not a string is journaled as `""`, so the tool never journals what
    its own shape check calls damaged. `store_path` is the directory of `dest_path`, and its
    account folder is `account`;
  - set later on a row: `skipped` (a reason code, Task 9) with `skip_detail` (a sentence);
    `written` set `True` by forward or `back` on a row whose write landed before its mark;
    `back_keys` (the keys `back` is putting back on the row, saved before its write, from which
    `back_restored` is counted; Task 9); and
    `restored` (bool `True`), which means **no relink-written key remains at `new` on the row**.
    Undo and `back` set it only then (every key put back, already back at `old`, changed by the
    app, or the row gone). A row with a key still at `new` (a claimed key, a repointed row under
    D19, an unreadable written row) stays unmarked, so its claim stands in `_relink_claims` and
    the other ownership checks, whatever the op's status;
  - per left-out row: `account`, `label`, `name`, `session_id`, `title`, `notes`
    (`[{"code": str, "detail": str}]`, link code first, never empty);
  - `undo_left` and `back_left` entries: `{"name", "label", "what", "why"}`, `what` a key name or
    `"the row"`. A row's entries are appended, and saved, before its `restored` mark is set or the
    pass moves on, and an entry already present is not appended again (Tasks 8, 9).
- **The `lineage` record** of a row to write. `*_planned` are always `False`.
  - `parent_row` is never empty: the `sessionId` of `spawnedFrom` in `new`, else in `kept`.
    `parent_session` is non-empty exactly when `parent_row` is, and `root_session` exactly when
    `root_row` is.
  - `root_row` is the `firstSessionId` of `startChain` in `new`; else, for a judged agreeing kept
    chain, the kept one's; else `""`. An unjudged kept chain is outside the record (`""`). For a
    kept key the record's fields are rebuilt from the kept object (Task 2, step 4), never left as
    the resolver set them: the resolver's link and chain name `_lineage_pick`'s row, which need not
    be the row a kept key names when B holds several rows of that conversation. The shape check
    accepts `""` or the kept chain's id whenever `kept` holds a `startChain` (Task 5).
  - For a written link: `carried_keys` `["sessionId", "taskId"]` and `dropped_keys` the
    resolver's. For a kept link: `carried_keys` the kept link's keys in order, `dropped_keys` only
    its `startChain.*` entries. `notes` are the resolver's, less `lineage_chain_dropped` when the row
    keeps its own chain.
- **Note codes.** 0.18.0's table is unchanged, with three codes added: the spec's two (D7) and
  one for a D11 failure (Departure 11). The phrases are this plan's (the spec leaves them to the
  builder):

  | code | phrase | position in `LINEAGE_PHRASES` | counts as |
  |---|---|---|---|
  | `own_link_differs` | the row's own link names no usable row | right after `lineage_source_changed` | link left out (in `LINEAGE_LEFT_OUT`) |
  | `lineage_no_app_link` | no app-written link backs it | right after `own_link_differs` | link left out (in `LINEAGE_LEFT_OUT`) |
  | `own_chain_differs` | the row's own chain disagrees with its holders' | right after `lineage_chain_dropped` | chain left out (not in `LINEAGE_LEFT_OUT`) |

  `RELINK_CHAIN_CODES = frozenset(("lineage_chain_dropped", "own_chain_differs"))`.
- **Recovery skip reasons, verbatim** (`RELINK_SKIP_REASONS`): `gone`, `repointed`, `unreadable`,
  `changed_since_plan`, `parent_changed`, `lineage_cycle` (the edge would close a cycle now),
  `edges_unreadable` (the account's links could not all be read, so a cycle cannot be ruled out).
- **Undo and back "why" phrases, verbatim:** `the app changed it since`, `gone from the store`,
  `now opens another conversation`, `could not be read`, `operation <op_id> wrote it since`, and,
  in `back` for a landed row that now opens another conversation, `now opens another
  conversation; its link was left`.
- **Report lines, verbatim shapes.** `<row>` is `_lineage_short(stem)` (`local_` and 8 characters);
  `<title>` is `repr(title)`, padded to the widest repr among that account's printed lines; each
  count part prints only when non-zero:
  - `-> <label>` heads each account with a line to print, a blank line after its rows;
  - `   <row>  <title>   link under <repr(parent_title)><tag>` (link only), `... link and chain`
    (both), `... chain` (chain only); `<tag>` is `   [chain: <phrase>]` when the row's link is
    written and its chain was left out, else `""`;
  - `   ... and <n> more` after `RELINK_LIST_CAP = 15` rows of an account, unless `--verbose`;
  - under `--verbose`, after an account's rows, each left-out row of it:
    `   <row>  <title>   [link: <phrase>]`, or `[chain: <phrase>]` when it has no link code;
  - `links   : <n> to add, <m> left out (<phrase> <k>, ...)` (`_lineage_links_line` with
    `carried="to add"`, the most frequent reason first, ties in `LINEAGE_PHRASES` order);
    `chains  : <n> to add, <m> left out`; `cycles  : <n> already present, left as they are`;
  - `nothing to add` when the plan writes no row, printed once per run; `dry run - pass --apply to
    add them` only when the plan writes a row;
  - after apply: `result  : <final>`, then `links   : <n> added, <m> left out (...)` with `; since
    the plan: <r> changed, <d> dropped, <g> gained` on the same line (converge's order; with no
    other part, `links   : since the plan: ...`), `chains  : <n> added, <m> left out`, and `Reopen
    the app and check that the relinked rows show under their parents. 'undo' takes the links out
    again, key by key.` (the app's response to a link on its own rows is measured only by the D2
    run sheet, so the line asks for the look rather than asserting the outcome);
  - undo preview: `would undo <op_id> (relink: takes out the links and chains it added to <n>
    row(s), key by key; a key changed since is left and named); pass --apply to execute`;
  - undo: `left    : <row> -> <label>: <what> (<why>)`, one per `undo_left` entry;
  - recover: `skipped : <row> -> <label>: <reason> - <skip_detail>`, one per skipped row.
- **CLI, verbatim** (Task 6): `relink [--only TITLE_OR_ID] [--account SUBSTRING] [--live SUBSTRING]
  [--apply] [--json] [--verbose] [--anonymize]`, help texts in Task 6.
- **D18 refusal, verbatim:** `<what> waits while another operation is unfinished (<op ids>): an
  unfinished operation of any kind may yet roll back or finish over rows a relink reads or
  changes - a converge or sync rolled back after a relink, for one, could delete a parent row a
  new link names. One unfinished operation holds every relink, its dry run included, until it is
  recovered. Run 'claude-code-sessions recover' and finish it first. Nothing was changed.`
  `<what>` is `relink`, `relink's undo` or `relink's recovery`. A journal that cannot be fully read
  refuses on its own, whether or not an operation is also open, with its own line, never an op
  id; every unreadable record is named: `<what> waits: the record(s) in <folders, sorted,
  comma-separated> could not be read, so whether they are unfinished cannot be told. Nothing was
  changed.`, and for a journal folder that cannot be listed, `<what> waits: the journal folder
  could not be listed (<error>). Nothing was changed.` When both apply, the refusal carries both
  lines, the open operations' first. Undo and recovery pass their own op id as `mine`, so a
  relink never waits on itself; the plan and the apply have no op yet and pass none.
- **Containment roots come from discovery, at every mutation route.** The apply
  (`execute_relink_op`), `undo_relink` and both directions of `_recover_relink` each call
  `_retitle_roots(env)` fresh, which refuses (`LayoutError`) when discovery fails, and pass those
  roots to `_relink_read` and `_relink_put`. A root is never derived from the journal, whose
  `store_path` and `dest_path` are what the check tests.
- **Every printed recover hint is runnable**: `recover --resolve <op_id> --back --apply` (and
  `--forward --apply` wherever forward is offered); a hint without `--apply` would only preview.
- **Journal budget (D15):** `RELINK_JOURNAL_BYTE_BUDGET = 32 * 1024 * 1024`, `sync`'s figure. One
  save is estimated once as `len(json.dumps(manifest, indent=1)) + 4096`; a row's mark is saved
  while the budget lasts, and always on the way out and with every status change. The apply,
  `undo_relink` and `_recover_relink` each save the manifest on the way out of any exception
  (`try ... except BaseException: save_manifest(op); raise`, a failed save never masking the
  original). Under-recording is harmless: the apply, recovery and undo read the disk.
- **Run both suites as separate steps, and check each exit code:**

  ```bash
  python -m pytest -q -p no:cacheprovider
  ```

  ```bash
  fail=0; for t in tools/check_*.py tools/native-host-measurement/check_watch_ceremony.py; do python "$t" >/dev/null 2>&1 || { echo "FAIL $t"; fail=1; }; done; [ "$fail" = 0 ] && echo "checks: all passed"
  ```

  The base is 1275 passed, measured at `eb7748a` on 2026-11-19 (the design commit adds no test).
  Never pipe a suite through `tail` in a chain that hides its exit code.
- **A name already taken.** 0.18.0's apply-time lineage pass is also called "relink":
  `_converge_recheck(..., relink=False)`, `_converge_relink` and `_sync_relink`. They are unrelated
  to this command; leave them as they are, and prefix every new name `relink`/`_relink_`.
- **The base.** This branch sits on `main` at dc08ad5, which holds the per-plan transcript index
  for `sync` planning (`_TranscriptIndex`, `_transcript_finder`) and an identifiers scrub of
  example output and one test fixture. The index changed `load_rows`, `select_sync_rows`,
  `_row_is_refresh`, `_displaced_overlap`, `_displaced_sizes`, `_sync_relink` and
  `cmd_new_row`, and this plan consumes none of them. `relink` builds its transcript set once
  per plan through `_relink_tids(env)` (`iter_transcripts`, as `plan_converge` does);
  switching it to the new index is not part of this plan. Every id in a test or a doc comes
  from the fake cast (above), short 8-hex prefixes included: a real session, row or
  conversation id prefix in a test or a doc breaks the repo's boundary rule.
- **Commits:** the tests first, `test(relink): N tests, failing until ...`, then
  `feat(relink): ...` or `fix(<scope>): ...`, then `docs: ...`. No attribution trailers. No push.
  The version bump and the run sheet belong to the release (the maintainer's call).

## Review Focus

- **A second run after an apply.** A row that gained a link is never planned again, and on a
  store where every chain resolved the first time a re-run prints `nothing to add` and journals
  nothing. A re-run may still add a chain that the first run's links made resolvable: account A
  has an app-written link and no chain, account B a usable chain and no link; the first run gives
  B its link, and only the second can give A the chain. Snapshot planning is intended, and the
  maintainer runs the dry run again after an apply. Pinned in Task 6
  (`test_a_rerun_adds_only_what_the_first_run_made_resolvable`).
- **The journal folder holds more than operations**: the `lock` file (always present during undo
  and recovery), a stray empty folder, a stray file. Only a folder holding `manifest.json` is an
  operation; an unreadable `manifest.json` is still a claim. Pinned in Task 7
  (`test_the_lock_file_and_stray_entries_are_not_operations`).
- **Titles that are not plain ASCII, or that equal a report key.** A title with `é` and an emoji
  prints on a cp1252 console without a traceback, and a conversation titled `left_out` or
  `lineage` under `--json --anonymize` leaves every key a key. A title equal to a schema value
  (`spawnedFrom`) corrupts neither the report nor the JSON's `adds` and note codes. Pinned in
  Task 4 (`test_odd_titles_survive_the_report_and_the_anonymized_json`,
  `test_a_title_equal_to_a_schema_value_corrupts_neither_report`).
- **The app re-serializing a written link.** Members in another order are still relink's value,
  and undo takes the key out; a member added by the app makes the key "changed", left and named.
  Pinned in Task 8 (`test_undo_takes_out_a_reordered_link_and_leaves_an_extended_one`).
- **A row whose `sessionId` is not its stem, and a holder whose `spawnedFrom` is not an object.**
  Neither is ever a link target or a source, the plan never raises, and the stem-mismatched row is
  still repaired as a candidate. Pinned in Task 2
  (`test_a_stem_mismatched_row_is_repaired_and_never_a_target`).

---

### Task 1: Vocabulary, value helpers, and the inventory's relink column

**Files:**
- Modify: `claude_code_sessions.py` - `LINEAGE_PHRASES` and `LINEAGE_LEFT_OUT` (about 15557 and
  15582); the new relink section (constants and helpers below).
- Modify: `tests/test_lineage.py` - `test_note_codes_phrases_and_left_out_set_match_the_table_exactly`
  (about 716).
- Modify: `tests/test_realign.py` - `_OP_TYPE_BRANCHES`, `_op_type_branches`, and its test (about
  845 to 908).
- Create: `tests/test_relink.py`.

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `RELINK_KEYS = ("spawnedFrom", "startChain")`; `RELINK_CHAIN_CODES`; `RELINK_LIST_CAP = 15`;
    `RELINK_SKIP_REASONS`; `RELINK_JOURNAL_BYTE_BUDGET`.
  - `_relink_app_written(link) -> bool` - Global Constraints. Never raises.
  - `_relink_same(a, b) -> bool` - `json.dumps(x, sort_keys=True, separators=(",", ":"))` equal,
    so member order does not count and `True` never equals `1`. Never raises (`False` on error).
  - `_relink_row_bytes(d: dict) -> bytes`; `_relink_sha(raw: bytes) -> str` (sha256 hex).
  - `_relink_apply_new(d: dict, new: dict) -> dict` - a copy of `d` with each key of `new` set, in
    `RELINK_KEYS` order: in place when present, appended when absent.
  - `_relink_apply_old(d: dict, old: dict, keys=None) -> dict` - a copy of `d` with each key of `old`
    (only those in `keys` when given) popped when `old[k]["present"]` is false, else set in place to
    `old[k]["value"]`.
  - `_relink_key_state(cur: dict, key: str, old: dict, new) -> str` - `"new"` when `cur` holds the
    key with a value `_relink_same` as `new`; `"old"` when `cur` matches `old` (absent when
    `old["present"]` is false, else present and `_relink_same` as `old["value"]`); else `"other"`.

- [ ] **Step 1: Write the failing tests.** In `tests/test_lineage.py`, the pin's expected list gains
  `("own_link_differs", "the row's own link names no usable row")` and then
  `("lineage_no_app_link", "no app-written link backs it")` right after the
  `lineage_source_changed` pair, and `("own_chain_differs", "the row's own chain disagrees with its
  holders'")` right after `lineage_chain_dropped`; the expected `LINEAGE_LEFT_OUT` gains
  `"own_link_differs"` and `"lineage_no_app_link"`. Create `tests/test_relink.py` with the module
  docstring (the spec path, fake cast only), the autouse `_reset` fixture `test_realign.py` uses,
  and:

```python
def test_app_written_is_the_two_app_shapes_only():
    assert ct._relink_app_written(side_link("local_p1"))
    assert ct._relink_app_written({"sessionId": "local_p1", "taskId": TASK, "title": "Northwind"})
    for link in ({"sessionId": "local_p1", "taskId": TASK},                    # this tool's own
                 {"sessionId": "local_p1", "taskId": TASK, "title": ""},
                 {"sessionId": "local_p1", "taskId": TASK, "title": 7},
                 {"sessionId": "local_p1", "taskId": TASK, "title": "N", "pendingPop": True},
                 {"sessionId": "local_p1", "sideSession": "true"}, None, "local_p1", [], {}):
        assert not ct._relink_app_written(link)

def test_apply_new_sets_in_place_or_appends_and_copies():
    d = {"a": 1, "spawnedFrom": None, "z": 2}
    out = ct._relink_apply_new(d, {"spawnedFrom": {"sessionId": "local_p2", "taskId": TASK},
                                   "startChain": {"firstSessionId": "local_r2", "depth": 2}})
    assert list(out) == ["a", "spawnedFrom", "z", "startChain"]
    assert d == {"a": 1, "spawnedFrom": None, "z": 2}

def test_apply_old_pops_an_absent_key_and_restores_a_non_object_exactly():
    cur = {"a": 1, "spawnedFrom": {"sessionId": "local_p2", "taskId": TASK}, "z": 2}
    assert ct._relink_apply_old(cur, {"spawnedFrom": {"present": False, "value": None}}) \
        == {"a": 1, "z": 2}
    out = ct._relink_apply_old(cur, {"spawnedFrom": {"present": True, "value": False}})
    assert list(out) == ["a", "spawnedFrom", "z"] and out["spawnedFrom"] is False
    assert ct._relink_apply_old(cur, {"spawnedFrom": {"present": False, "value": None}},
                                keys=()) == cur

def test_key_state_compares_by_value_and_type():
    new = {"firstSessionId": "local_r2", "depth": 1}
    absent = {"present": False, "value": None}
    assert ct._relink_key_state({"startChain": {"depth": 1, "firstSessionId": "local_r2"}},
                                "startChain", absent, new) == "new"       # member order ignored
    assert ct._relink_key_state({"startChain": {"firstSessionId": "local_r2", "depth": True}},
                                "startChain", absent, new) == "other"
    assert ct._relink_key_state({}, "startChain", absent, new) == "old"
    assert ct._relink_key_state({"startChain": None}, "startChain",
                                {"present": True, "value": None}, new) == "old"

def test_row_bytes_are_compact_json():
    assert ct._relink_row_bytes({"a": "é", "b": [1]}) == b'{"a":"\\u00e9","b":[1]}'
```

  In `tests/test_realign.py`, `_OP_TYPE_BRANCHES` becomes `{name: (realign column, relink column)}`,
  `_op_type_branches(path)` returns `{name: (names "realign", names "relink")}`, and the test is
  renamed `test_every_op_type_branch_names_realign_and_relink_or_says_why_not`, asserting each
  column as the realign one is asserted today and `gui == {"_find_undoable_op": (True, False)}`. Its
  comment says the relink column was added because the AST check only notices a new branching
  function, not a missing arm. The relink column, with the realign column unchanged:

  | function | relink column at Task 1 | final, set by |
  |---|---|---|
  | `rotate_ops` | `pending: Task 7 adds relink to the claimants` | `names it`, Task 7 |
  | `classify_op` | `pending: Task 9 adds the relink arm` | `names it`, Task 9 |
  | `recover_op` | `pending: Task 9 adds the relink arm` | `names it`, Task 9 |
  | `gather_doctor` | `reads a relink op as any other, by status and rollback_residue; a relink creates no row to vanish and sets no copy aside` | same |
  | `cmd_undo` | `pending: Task 8 adds the relink arm` | `names it`, Task 8 |
  | `undo_realign` | `its own type` | same |
  | `_collides` | `pending: Task 7 makes relink one of rotate_ops' claimants` | `relink is one of rotate_ops' claimants, which _collides reads; a relink sets no copy aside`, Task 7 |
  | `plan_realign` | `a relink writes no key realign plans from; an unfinished relink is in nonterminal_ops already` | same |
  | `_release_candidates` | `only realign holds copies to release` | same |
  | `_journal_rows` | `relink rows take the last branch, a per-row store_path` | same |
  | `_unresolved_rows` | `tells moves apart; relink rows carry written` | same |
  | `_realign_claimed_later` | `pending: Task 7 passes over a relink` | `names it`, Task 7 |
  | `plan_undo` | `undo of a move only` | same |
  | `undo_new_row`, `undo_sync`, `undo_repoint`, `undo_retitle`, `undo_converge` | `its own type` | same |
  | `_sync_paths_claimed_elsewhere` | `sync claimants by bytes; a relink write is drift to sync's undo` | same |
  | `_live_override_note` | `only sync records a live override note` | same |
  | `_lineage_row_store` | `a relink row's store_path is read by its non-sync branch, as a converge row's` | same |
  | `cmd_recover` | `pending: Task 9 prints relink's skipped rows` | `names it`, Task 9 |

  Later tasks add rows for the branching functions they create: Task 7 `_relink_claims` (`names
  it`, `names it`), `_retitle_claimed_elsewhere` (`a realign row carries post_b64, compared by
  bytes as any other op's`, `names it`), `_repoint_claimed_later` (`a realign that wrote the row
  claims it, as any terminal op does`, `names it`); Task 8 `undo_relink` (`its own type`, `names
  it`) and flips the GUI pair to `(True, True)`.
- [ ] **Step 2:** Run `python -m pytest -q -p no:cacheprovider tests/test_relink.py
  tests/test_lineage.py tests/test_realign.py`. Expected: the new relink tests and the phrase pin
  fail (names not defined, table mismatch); the inventory test passes in its new shape.
- [ ] **Step 3: Implement** the three table entries and the Produces list.
- [ ] **Step 4:** Run the three files, then both full suites. Expected: all pass.
- [ ] **Step 5: Commit** the tests first, then the code.

### Task 2: The resolution core

**Files:**
- Modify: `claude_code_sessions.py` - the relink section.
- Test: `tests/test_relink.py`.

**Interfaces:**
- Consumes: Task 1; `LineageRow`, `_lineage_index`, `_lineage_named_conversation`,
  `_lineage_resolve`, `_lineage_existing_edges`, `_lineage_drop_cycles`, `_lineage_clear_chain`,
  `_lineage_note_into`, `_lineage_variant_ok`, `_lineage_task_ok`, `_lineage_stem`,
  `_lineage_short`, `_RetitleRow`.
- Produces:
  - `_relink_tids(env) -> set` - the transcript stems under `env.projects_root`, once.
  - `_relink_judge(lrows: list, index: dict) -> tuple` - D12 over every row of one conversation,
    in every account, whatever the narrowing: `(parent, task, note)`. Rows whose `spawnedFrom` is
    a dict and names a usable row (`_lineage_named_conversation`) yield a parent. Two or more
    distinct parents: `(None, None, {"code": "lineage_parent_disagree", "detail": "holders name N
    different parents"})`. Among yielders with `_lineage_task_ok`, two or more `taskId`s:
    `(None, None, {"code": "lineage_task_disagree", "detail": "holders give N different taskIds
    for parent <8>"})`. One parent: `(P, T, None)`, `T` the one usable `taskId` or `None` when no
    yielder has one; none: `(None, None, None)`. A link with no usable `taskId` is not a
    disagreement (the resolver's rule); a kept one is `own_link_differs` (step 4).
  - `_relink_agreed_chain(holders: list, index: dict) -> tuple | None` - `(root, depth)` when every
    holder whose `startChain` is a dict names a usable row (as above) of one root conversation, with
    one `depth` that is an `int`, not a `bool`, of at least 1; else `None`.
  - `_relink_chain_agrees(row: LineageRow, index: dict, agreed) -> bool` - `agreed` is not `None`,
    and the row's `startChain` names a usable row in its own account whose conversation is
    `agreed[0]`, with `type(depth) is int` (so `True` never agrees with 1) and `depth ==
    agreed[1]`.
  - `_relink_backed(lrows: list, index: dict, parent: str) -> bool` - some row in `lrows` whose
    `spawnedFrom` is app-written and names a usable row of `parent`.
  - `_relink_row_decision(row, conv_rows, holders, index, judged, tids) -> tuple` - the one place
    a row's codes are chosen (step 4's table): `(proposal, notes)`, `proposal` a
    `_lineage_resolve` result trimmed to the keys the row writes, with its `lineage` rebuilt for
    any kept key, or `None`; `notes` the row's left-out notes when `proposal` is `None` (`[]` for
    a row in neither list). `judged` is `_relink_judge`'s `(parent, task, note)`. Never raises.
  - `_relink_plan_rows(records: list, tids: set, in_scope) -> tuple[list, list, int]` -
    `(rows, left_out, cycles)`. `in_scope(rec) -> bool` picks the rows that may be written or
    reported; the D12 judgement, holders, targets and the graph always read every record. Never
    raises on a malformed row.

**What `_relink_plan_rows` does** (spec steps 1 to 6):
1. Index once: `LineageRow(rec.account, rec.name, rec.data)` per record; `index = {account:
   _lineage_index(rows)}`; `conversations = {cliSessionId: [rec]}` over non-empty string ids.
2. A conversation C is in scope when some row of it has `_lineage_variant_ok(spawnedFrom)`.
   Conversations in sorted order; within C, accounts sorted, rows by name.
3. `parent, task, note = _relink_judge(all of C's LineageRows in every account, index)`: the
   judgement reads every account whatever `--account` or `--only` narrows, once per conversation.
   With a note, step 4's table leaves out every row of C in scope with that note, in every account,
   the linked rows included, so a selected row of a disagreeing conversation is always listed; a
   row with no `startChain` object whose holders carry one also gets `lineage_chain_dropped` ("the
   parent link was left out"), as the resolver adds it, so the `chains :` line counts it.
4. For each in-scope row R of C in account B, `_relink_row_decision` decides, with `holders` =
   C's LineageRows in accounts other than B, `holder_chain` = any holder whose `startChain` is a
   dict, `link_cand` = R has no `spawnedFrom` object, and `chain_cand` = R has no `startChain`
   object and `holder_chain`. Three judges can speak about one row: D12 (`_relink_judge`, step 3),
   the resolver, and D11. Which code the row carries is fixed by this table, the first line that
   applies winning. Each line's `+ chain` is `lineage_chain_dropped` ("the parent link was left
   out"), added exactly when `chain_cand`; a row that keeps its own `startChain` object never
   carries `lineage_chain_dropped` on any left-out path, whatever the resolver added:

   | the situation | the row's codes |
   |---|---|
   | D12: the conversation's links disagree | `lineage_parent_disagree` or `lineage_task_disagree`, + chain |
   | R keeps a link naming no usable row, or without the agreed usable `taskId` | `own_link_differs`, + chain |
   | R keeps a chain that does not agree with the holders' | `own_chain_differs` |
   | link candidate; the resolver produced no link | the resolver's notes, as it gives them (`lineage_variant`, `lineage_no_task`, `lineage_self`, `lineage_parent_dead`, `parent_not_in_account`, `parent_row_drifted`, ..., with its own `lineage_chain_dropped` only when `chain_cand`, stripped otherwise); no notes: neither list |
   | link candidate; the resolver produced a link, and no app-written link among the holders that yield the parent backs it (D11) | `lineage_no_app_link`, + chain |
   | chain-only candidate; the resolver produced no chain | the resolver's `lineage_chain_dropped` alone |
   | chain-only candidate; no app-written agreeing link in C, R's own included (D11) | `lineage_chain_dropped` ("no app-written link backs the chain") |
   | otherwise | a proposal; the cycle pass (step 5) may still drop it with its own codes |

   So `lineage_no_app_link` arises only when the resolver produced a link: some holder yielding
   the parent is a cleared variant with a usable `taskId`, and none of them is app-written. A
   holder set with no cleared variant keeps the resolver's `lineage_variant`. The bullets below
   say how each line is computed:
   - R's `spawnedFrom` is a dict that names no usable row, or whose `taskId` is not a usable
     string equal to `task` (a missing or empty one: the shape Phase 0 measured as hiding the
     child, which `relink` cannot repair but must surface): left out, `own_link_differs` ("the
     row's own link names no row in its account" or "the row's own link carries no usable
     taskId"), plus `lineage_chain_dropped` as in step 3. A wrong but usable `taskId` never gets
     here: it is the conversation's disagreement.
   - R's `startChain` is a dict and `holder_chain`: unless `_relink_chain_agrees(R,
     index, _relink_agreed_chain(holders, index))`, left out, `own_chain_differs`. The row gains
     nothing.
   - Neither `link_cand` nor `chain_cand`: nothing to do, not listed.
   - `res = _lineage_resolve(C, R.name, holders, index, index[B], held={},
     transcript_exists=tids.__contains__)`.
   - Link candidate: `res["link"] is None` with notes gives a left-out row with the resolver's
     notes, less `lineage_chain_dropped` unless `chain_cand`; with no notes left (its holders name
     no usable parent) the row is in neither list (step 7). Otherwise D11: unless
     `_relink_backed(holders, index, parent)`, the link and chain are cleared
     (`_lineage_clear_chain`, link fields emptied as `_lineage_drop_cycles` empties them) and the
     notes become `lineage_no_app_link` ("no app-written link among the holders that name parent
     <8>"), plus `lineage_chain_dropped` ("the parent link was left out") when `chain_cand`. Left
     out. A row keeping its own chain object proposes no chain: clear `res`'s chain without a note
     (and drop any `lineage_chain_dropped` the resolver added).
   - Chain-only candidate (R keeps its agreeing link): `res["chain"] is None` gives a left-out row
     with the resolver's `lineage_chain_dropped` note only. D11 for the chain counts every row of C,
     R's own included: unless `_relink_backed(all of C's rows, index, parent)`, left out,
     `lineage_chain_dropped` ("no app-written link backs the chain"). Otherwise its proposal is the
     chain alone: set `res["link"] = None` and `res["edges"]["parent"] = None`, since the kept link
     is an existing edge.
   - **The record of a kept key is rebuilt from the kept object**, in the proposal, before step 5:
     a kept link sets `parent_row` to its `sessionId`, `parent_session` to the conversation that row
     opens (`_lineage_named_conversation`, read at plan time), `carried_keys` to the kept link's
     keys in order and `dropped_keys` to only the `startChain.*` entries; a judged agreeing kept
     chain sets `root_row` to its `firstSessionId` and `root_session` to that row's conversation,
     and an unjudged one sets both to `""`, dropping any `startChain.*` entries. The resolver's own
     values name `_lineage_pick`'s row, which need not be the kept target when B holds several rows
     of the parent or root conversation, and the shape check holds the record to `new` and `kept`
     (Task 5).
5. Per account B (every account of the records, whatever the scope), `cycles +=
   _lineage_drop_cycles(results_B, _lineage_existing_edges(index[B]))`. A link proposal whose link
   it dropped is left out (`lineage_cycle`, with `lineage_chain_dropped` when it carried a chain); a
   chain-only proposal whose chain it dropped is left out; a link kept with its chain dropped is
   written with the chain code in its notes.
6. Each surviving proposal becomes a row (Global Constraints, journal fields): `new` holds the
   written keys; `old[k] = {"present": k in data, "value": data.get(k)}`; `kept` the row's object
   keys; `post = _relink_apply_new(data, new)`; `pre_sha256 = _relink_sha(rec.raw)`,
   `post_sha256 = _relink_sha(_relink_row_bytes(post))`; `title` the row's title and
   `parent_title` the title of the row `parent_row` names in B, each `""` when it is not a string;
   `lineage` per Global Constraints. Rows and left-out rows are sorted by `(account, session_id,
   name)`.
7. A candidate that gains nothing and carries no note (its holders yield no parent) is in neither
   list: a left-out entry always has at least one note.

- [ ] **Step 1: Write the failing tests.** Add the store helper and these tests:

```python
LQ = "0abc0000-7777-4000-8000-000000000011"   # another parent; never a real id
LR = "0bbb0000-6666-4000-8000-00000000000b"   # a chain root
CHAIN = {"firstSessionId": "local_r1", "depth": 2}

def tool_link(row_id): return {"sessionId": row_id, "taskId": TASK}

def relink_store(env, write_transcript, write_row, link=None, chain=None, roots=False, **per_row):
    """P (LP, 'Northwind kickoff') and C (LC, 'ACME-REVIEW side task') each hold a row in A1, A2
    and A3: local_p1..3, local_c1..3, every sessionId its stem. local_c1 carries LINK (default
    side_link('local_p1')) and CHAIN when given; local_c2 and local_c3 carry no link. ROOTS adds
    LR rows local_r1..3 ('Northwind board'). PER_ROW maps a stem to extra keys for that row.
    Transcripts exist for LP, LC (and LR). Returns {stem: path}."""

def plan_rows(env, scope=None):
    # _retitle_scan(env, live=_LIVE_UNRESOLVED, store_path="", why=...): every argument after env
    # has a default (live resolves through live_account), so env alone scans every account
    return ct._relink_plan_rows(ct._retitle_scan(env), ct._relink_tids(env),
                                scope or (lambda rec: True))

def test_a_missing_link_is_resolved_in_each_account_that_lacks_it(...):
    rows, left, cycles = plan_rows(env)
    assert [r["name"] for r in rows] == ["local_c2.json", "local_c3.json"] and left == []
    c2 = rows[0]
    assert c2["new"] == {"spawnedFrom": {"sessionId": "local_p2", "taskId": "task_7f"}}
    assert c2["old"] == {"spawnedFrom": {"present": False, "value": None}} and c2["kept"] == {}
    assert c2["lineage"]["parent_row"] == "local_p2" and c2["lineage"]["parent_session"] == LP
    assert c2["lineage"]["carried_keys"] == ["sessionId", "taskId"]
    assert c2["lineage"]["dropped_keys"] == ["sideSession", "title"]
    assert c2["parent_title"] == "Northwind kickoff" and c2["written"] is False
    assert c2["pre_sha256"] == hashlib.sha256(open(paths["local_c2"], "rb").read()).hexdigest()
def test_the_chain_comes_with_the_link(...):            # chain=CHAIN, roots=True
    # rows[0]["new"]["startChain"] == {"firstSessionId": "local_r2", "depth": 2}; root_row "local_r2"
def test_a_non_object_link_is_missing_and_replaced_where_it_stands(...):
    # parametrize v in (None, False, "local_p2", []): local_c2 written with spawnedFrom=v second of
    # its keys -> planned; old == {"spawnedFrom": {"present": True, "value": v}};
    # list(ct._relink_apply_new(data, new)) == list(data)
def test_an_existing_link_object_is_never_replaced(...):
    # local_c3=side_link("local_p3") -> no entry for local_c3.json in rows or left
def test_a_kept_agreeing_link_beside_a_chain_gap_gains_the_chain(...):
    # chain=CHAIN, roots=True, local_c3 keeps side_link("local_p3") -> its row:
    # new == {"startChain": {"firstSessionId": "local_r3", "depth": 2}},
    # kept == {"spawnedFrom": side_link("local_p3")}, lineage parent_row "local_p3",
    # carried_keys == ["sessionId", "taskId", "title", "sideSession"], dropped_keys == []
def test_a_kept_link_record_names_the_kept_target_not_the_pick(...):
    # chain=CHAIN, roots=True; A3 also holds local_p3b (LP, a greater lastActivityAt, so
    # _lineage_pick chooses it over local_p3); local_c3 keeps side_link("local_p3") -> its row:
    # new == {"startChain": {"firstSessionId": "local_r3", "depth": 2}}, lineage parent_row ==
    # "local_p3" (never "local_p3b"), parent_session == LP, carried_keys == ["sessionId",
    # "taskId", "title", "sideSession"], dropped_keys == []; parent_title is local_p3's title
def test_a_chain_only_repair_counts_the_rows_own_app_written_link(...):
    # link=tool_link("local_p1"), chain=CHAIN, local_c3 keeps side_link("local_p3"):
    # local_c3 gains the chain; local_c2 gains its link (local_c3 backs it) and its chain
def test_a_kept_link_with_another_parent_is_the_conversations_disagreement(...):
    # LQ rows local_q1..3; local_c3 keeps side_link("local_q3") -> rows == [];
    # left holds local_c1, local_c2, local_c3, each codes == ["lineage_parent_disagree"]
def test_a_kept_link_with_another_task_id_is_the_conversations_disagreement(...):
    # local_c3 keeps side_link("local_p3", taskId="task_9z") -> the three left out,
    # ["lineage_task_disagree"]
def test_a_kept_empty_object_or_another_accounts_row_is_own_link_differs(...):
    # parametrize local_c3 link in ({}, tool_link("local_p1")): local_c3 left out
    # ["own_link_differs"]; local_c2 still planned
def test_a_kept_link_without_a_usable_task_id_is_own_link_differs(...):
    # parametrize local_c3 link in ({"sessionId": "local_p3", "sideSession": True, "title":
    # "Northwind"}, {"sessionId": "local_p3", "taskId": ""}): local_c3 left out
    # ["own_link_differs"], detail "carries no usable taskId"; local_c2 still planned
def test_a_disagreement_or_own_link_differs_counts_the_chain_left_out(...):
    # chain=CHAIN, roots=True. (a) local_c3 keeps side_link("local_q3"): local_c1 left out
    # ["lineage_parent_disagree"] (it holds the chain itself); local_c2 and local_c3
    # ["lineage_parent_disagree", "lineage_chain_dropped"]. (b) local_c3 keeps {}: local_c3
    # ["own_link_differs", "lineage_chain_dropped"]
def test_a_row_keeping_its_own_chain_never_counts_a_chain_left_out(...):
    # chain=CHAIN, roots=True, local_c3 keeps the agreeing {"firstSessionId": "local_r3",
    # "depth": 2} and no link. (a) local_p3 removed: local_c3 left out ["parent_not_in_account"]
    # only (the resolver's lineage_chain_dropped stripped). (b) link=tool_link("local_p1"):
    # local_c3 left out ["lineage_no_app_link"] only. Neither counts on the chains line
def test_a_candidate_whose_holders_name_no_usable_row_is_in_neither_list(...):
    # link=side_link("local_zz") (no such row): local_c1 left out ["own_link_differs"];
    # local_c2 and local_c3 in neither list; every left-out entry has at least one note
def test_a_kept_chain_with_no_holder_chain_stands_outside_the_record(...):
    # local_c2 startChain={"firstSessionId": "local_r2", "depth": 5}, roots=True, no holder chain:
    # new == {"spawnedFrom": ...} only; kept == {"startChain": {...}}; root_row == "" == root_session
def test_a_kept_chain_with_the_wrong_root_or_depth_is_own_chain_differs(...):
    # chain=CHAIN, roots=True; parametrize local_c2 startChain in ({"firstSessionId": "local_q2",
    # "depth": 2}, {"firstSessionId": "local_r2", "depth": 3}): local_c2 left out
    # ["own_chain_differs"], no row for it; local_c3 planned with link and chain
def test_a_link_left_out_drops_its_chain(...):
    # chain=CHAIN, roots=True, local_p3 removed -> local_c3 ["parent_not_in_account",
    # "lineage_chain_dropped"]
def test_two_rows_of_one_conversation_in_one_account_are_each_repaired(...):
    # extra local_c2b (LC, A2): both local_c2.json and local_c2b.json in rows
def test_archived_rows_and_an_archived_parent_row_are_included(...):
    # local_c2 isArchived True -> planned; local_p3 isArchived True and the only P row in A3 ->
    # local_c3 planned to local_p3
@pytest.mark.parametrize("code, row, setup", [...])   # one entry per code below
def test_every_reachable_0_18_reason_reaches_the_plan(code, row, setup, ...):
    # The base store (default app-written side_link on local_c1) plans ROW; SETUP is applied;
    # then ROW is in left_out and its first note's code is CODE - the row's own transition, not
    # "some row has it". (code, row, setup):
    #   lineage_parent_disagree, local_c2: local_c3 keeps side_link("local_q3") (LQ rows exist)
    #   lineage_self, local_c2: local_c1 links a second LC row in A1, local_c1b
    #   lineage_parent_dead, local_c2: LP's transcript removed
    #   parent_not_in_account, local_c3: local_p3 removed
    #   parent_row_drifted, local_c3: local_p3 removed; local_p1.json in A3 opens LQ
    #   lineage_variant, local_c2: local_c1 links {"sessionId": "local_p1", "taskId": TASK,
    #     "pendingPop": True}; A2 gains local_c2b (LC) keeping tool_link("local_p2")
    #   lineage_no_task, local_c2: local_c1 links side_link("local_p1") without taskId
    #   lineage_task_disagree, local_c2: local_c3 keeps side_link("local_p3", taskId="task_9z")
    #   lineage_cycle, local_c2: local_p2 keeps side_link("local_c2"); local_c1's app-written
    #     side_link backs the proposal, which survives D11 and is dropped by the cycle pass
    # parent_pair_held and lineage_source_changed cannot arise (relink holds nothing; it is sync's)
def test_cycles_are_dropped_against_existing_edges_a_kept_link_counting(...):
    # local_p2 keeps side_link("local_c2") -> local_c2 ["lineage_cycle"]; local_c3 planned
def test_a_left_out_rows_link_still_counts_in_the_cycle_graph(...):
    # A3: local_p3 -> local_q3, local_q3 -> local_c3; local_q1 -> local_p1 so LQ's links disagree
    # and every LQ row is left out; local_c3 is still left out ["lineage_cycle"]
def test_an_existing_cycle_is_counted_once(...):
    # A3 rows local_x3 <-> local_y3 (LX, LY, side links both ways) -> cycles == 1
def test_a_link_backed_only_by_tool_copies_is_left_out_with_its_chain(...):
    # link=tool_link("local_p1"), chain=CHAIN, roots=True -> local_c2, local_c3:
    # codes ["lineage_no_app_link", "lineage_chain_dropped"]; the first detail contains
    # "no app-written link"; the links line's phrase is "no app-written link backs it"
def test_one_app_written_yielding_holder_is_enough(...):
    # link=tool_link("local_p1"), local_c3 keeps side_link("local_p3") -> local_c2 planned
@pytest.mark.parametrize("cleared_in, code", [("A3", "lineage_no_app_link"),
                                              ("A2", "lineage_variant")])
def test_a_titled_link_with_an_empty_title_is_not_app_written(cleared_in, code, ...):
    # local_c1 {"sessionId": "local_p1", "taskId": TASK, "title": ""}: not app-written, and not a
    # cleared variant either. The conversation is in scope only through a cleared tool copy:
    # A3: local_c3 keeps tool_link("local_p3"), so local_c2's holders include a cleared variant
    #     with a usable taskId, the resolver produces a link from it, and D11 finds no app-written
    #     holder -> local_c2 left out ["lineage_no_app_link"] (step 4's table, D11 line);
    # A2: a second A2 row local_c2b keeps tool_link("local_p2") instead, so local_c2's only
    #     holder yielding the parent is the empty-title link, the resolver produces no link
    #     -> local_c2 left out ["lineage_variant"] (the resolver's own note stands)
def test_the_new_value_is_the_old_with_only_the_written_keys_set(...):
    # local_c2 with APP_KEYS (cwd, model, permissionMode, lastFocusedAt, folderPlace, isArchived):
    # post = ct._relink_apply_new(data, new); {k: post[k] for k in data} == data;
    # list(post) == list(data) + ["spawnedFrom"]
def test_a_key_not_written_keeps_its_null(...):
    # local_c2 startChain None, no holder chain -> "startChain" not in new; post["startChain"] is None
def test_a_non_string_title_is_journaled_as_empty(...):
    # local_c2 title 5, local_p2 title None -> its row's title == "" and parent_title == ""
    # (Task 5's test_a_planned_manifest_passes_the_shape_check repeats this store)
def test_a_stem_mismatched_row_is_repaired_and_never_a_target(...):     # Review Focus
    # local_c2's sessionId "local-x" -> still planned; a P row in A3 whose sessionId is not its stem
    # is not picked (local_c3 left out parent_not_in_account when it is P's only A3 row);
    # a holder with spawnedFrom "local_p1" (a string) yields nothing; no exception
```

- [ ] **Step 2:** Run `tests/test_relink.py`. Expected: the new tests fail (names not defined).
- [ ] **Step 3: Implement** the Produces list.
- [ ] **Step 4:** Run the file, then both full suites. Expected: all pass.
- [ ] **Step 5: Commit** the tests first, then the code.

### Task 3: `plan_relink` - the scan, D16 to D18, identity and narrowing

**Files:** Modify `claude_code_sessions.py` (relink section). Test: `tests/test_relink.py`.

**Interfaces:**
- Consumes: Task 2; `_retitle_scan`, `_converge_destinations`, `_identity_disagreement`,
  `_resolve_live_assertion`, `live_account`, `_account_dirs`, `_email_of`, `read_json`, `Op`,
  `NONTERMINAL`, `_reroot_pairs`, `_reroot_manifest`, `_only_display`,
  `_retitle_candidate_listing`, `_listing_label`, `_listing_title`.
- Produces:
  - `@dataclasses.dataclass class RelinkFlags: only: str = ""; account: str = ""; live: str = ""`.
  - `_RELINK_SCAN_WHY = ("which rows lack a parent link another account carries, or which row a
    link would name - a guess there could link the wrong row or miss a disagreement")`.
  - `_relink_load_ops(env) -> list` - `[(folder name, manifest dict or None)]` for each entry of
    `env.ops_dir` that is a directory holding `manifest.json`, each read on its own (`None` when it
    cannot be read, is not an object, or has no string `op_id`), rerooted as `list_ops` reroots.
    Files (the `lock` among them) and folders without `manifest.json` are not operations. A journal
    folder that cannot be listed raises `OSError` to its callers, which word it. Used here for D18
    and in Task 7 for claims.
  - `_relink_open_others(env, mine="") -> tuple` - `(op ids, unreadable)`: the op ids of
    non-terminal operations other than `mine`, and the folder names of every record that could not
    be read (or the listing error, for a journal folder that could not be listed). Never raises.
  - `_relink_refuse_open(env, what, mine="") -> None` - the D18 refusal (Global Constraints),
    raised when any other op is open or any record (or the journal folder) could not be read, each
    condition on its own: the op-id sentence for open ops, and one line naming every unreadable
    folder, so a second damaged record is not found only on the next run; both lines when both
    apply.
  - `_relink_account_filter_display(value) -> str` - `"<account-filter>"` under `--anonymize` when
    non-empty, else the value.
  - `plan_relink(env, flags: RelinkFlags) -> dict` - the manifest (Global Constraints) before
    `new_op`, with `rows[*]["written"] = False`.

**What `plan_relink` does, in order:**
1. `dis = _identity_disagreement(env)`; `live` resolved as `plan_converge` resolves it
   (`account_scope=True`); `live_asserted` recorded only when `live.resolved_from == "user"`.
2. `_relink_refuse_open(env, "relink")` (D18; the dry run refuses too).
3. `records = _retitle_scan(env, live=live, why=_RELINK_SCAN_WHY)`: any unreadable row refuses,
   naming it (D16). `stores, _ = _converge_destinations(env, records)`: an account in two org
   folders refuses (D17).
4. `--account`: a case-insensitive substring of an account id, org id, store path or email
   (`_email_of(env, acct, live)`) over every account directory, `_account_dirs(env)`, empty org
   folders and the signed-in account's included. No match: `no account store matches --account
   <shown> (an account id, org id, store path or email).` followed by every account directory, one
   `   <email, else acct[:8]> (<acct[:8]>/<org[:8]>)   <path>` per line, the email from
   `_email_of(env, acct, live)` where known (scrubbed under `--anonymize` by `_listing_label`), so
   a user who typed an email can compare it. Matches in two or more accounts: `--account <shown>
   matches <n> accounts; give more of it:` and the same list. Matches in one account scope the run to the
   matched folders (a row is in scope when its store is one of them). An account's rows all sit in
   its one populated store (D17), so an account id or email that also matches its empty org
   folders is not ambiguous; matched folders that are all empty plan nothing (`nothing to add`).
5. `--only`: a value starting with `local_` (case-insensitive) is a row id: rows of the
   `--account` store (or all) whose stem starts with it must be exactly one; none refuses `no row
   matches --only <shown> (a row id is local_ and a prefix of its uuid).`, several refuse `--only
   <shown> matches <n> rows; give more of the id:` with `   <stem>   <label>   <title>` lines.
   Any other value is a conversation, matched over every conversation as `plan_converge` matches
   (`cliSessionId` prefix, or a title substring of any row): none refuses `no row in any account
   matches --only <shown> (a title substring, or a cliSessionId prefix).`, several refuse with
   `_retitle_candidate_listing`.
6. `rows, left_out, cycles = _relink_plan_rows(records, _relink_tids(env), in_scope)`.
7. A conversation `--only` that leaves more than one row to write refuses: `--only <shown> leaves
   <n> rows to change; name one by its row id, so a proof touches one row:` with
   `   <stem>   <label>` lines. A row `--only` whose row has nothing to add, or a conversation with
   no candidate, is not refused: the plan has no rows.

- [ ] **Step 1: Write the failing tests:**

```python
def test_plan_relink_returns_the_journal_shape(...):
    m = ct.plan_relink(env, ct.RelinkFlags())
    assert set(m) == {"op_type", "lineage_format", "only", "account_filter", "live_asserted",
                      "lineage_cycles", "rows", "left_out"}
    assert m["op_type"] == "relink" and m["lineage_format"] == 1
def test_only_by_row_id_narrows_to_that_row(...):           # only="local_c2" -> rows == [local_c2]
def test_only_by_conversation_with_two_rows_to_change_refuses(...):
    # only="ACME-REVIEW" -> Refusal "leaves 2 rows to change", naming local_c2 and local_c3;
    # only=LC[:8], account=A2[:8] -> one row
def test_only_refuses_no_match_and_an_ambiguous_one(...):
    # "Nothing-matches" -> "no row in any account matches"; "local_c" -> "matches 3 rows";
    # a second conversation titled "ACME-REVIEW side task, round two" -> "matches 2 conversations"
def test_only_with_no_candidate_plans_nothing(...):          # only="Northwind kickoff" -> rows == []
def test_account_narrows_to_one_store_the_signed_in_one_included(...):
    # identity_files(env, A2, A2, email="bob@example.com"): account="bob@" -> only local_c2;
    # account=O3[:8] -> only local_c3; account=<a substring of A1's store path> -> nothing to add
def test_account_matches_empty_org_folders_without_ambiguity(...):
    # A2 also has two empty org folders (OX = "77777777-0000-0000-0000-000000000007" and another
    # fake id): account=A2[:8] -> only local_c2; account=OX[:8] -> rows == [] and no Refusal.
    # Separately, a signed-in A4 whose only org folder (O4) is empty: account=A4[:8] -> rows == []
def test_account_refuses_no_match_or_several_accounts(...):
    # "zzzz" -> "no account store matches", and the listing shows "bob@example.com (cccccccc/
    # dddddddd)" for the signed-in A2 (identity_files(env, A2, A2, email="bob@example.com"));
    # "-0000-0000-" -> "matches 3 accounts"
def test_a_selected_row_of_a_disagreeing_conversation_is_listed(...):
    # local_c3 keeps side_link("local_q3") (A3): with account=A2[:8], and with only="local_c2",
    # rows == [] and left_out == [local_c2's entry, codes ["lineage_parent_disagree"]]
def test_an_unreadable_record_refuses_with_its_own_line(...):
    # two folders in ops_dir whose manifest.json is "{not json" -> one Refusal "the record(s) in
    # <folder 1>, <folder 2> could not be read" naming both, and no "(" + "the journal"
    # placeholder posing as an op id; the lock file and a folder without manifest.json do not
    # refuse. With a hand-made "writing" op beside them, the one Refusal carries both lines, the
    # open op's first: an unreadable record refuses on its own, never hidden behind the open op
@pytest.mark.parametrize("bad", ["local_c2", "local_c1", "local_p2", "local_p3"])
def test_an_unreadable_candidate_holder_target_or_kept_target_refuses(bad, ...):
    # local_c3 keeps side_link("local_p3"); the BAD row's file is "{not json" ->
    # Refusal "could not be read" naming bad + ".json"
def test_an_account_in_two_org_folders_refuses(...):         # a row for A2 under O4 too ->
                                                             # "more than one org directory"
def test_an_unfinished_operation_refuses_the_plan(...):
    # a hand-made manifest {"op_id": "20990101T000000Z-aaaaaa", "status": "writing", ...} ->
    # Refusal "relink waits while another operation is unfinished (20990101T000000Z-aaaaaa)"
def test_the_plan_records_an_identity_disagreement(...):
    # identity_files(env, A1, A2) -> m["identity_disagreement"] == {"oauth": A1, "config": A2}
def test_live_is_recorded_only_when_it_arbitrates(...):      # as converge's tests of live_asserted
```

- [ ] **Steps 2 to 5** as in Task 2.

### Task 4: The report and the public projection

**Files:**
- Modify: `claude_code_sessions.py` - the relink section; `_ANON_FIELDS` and
  `_ANON_STRUCTURAL_KEYS` (about 4144 and 4178); `_identity_warning_lines` (about 18771).
- Test: `tests/test_relink.py`; `tests/test_anonymize.py`'s
  `test_every_emitted_report_key_is_in_the_structural_set` (about 872).

**Interfaces:**
- Consumes: Tasks 1 to 3; `_lineage_links_line`, `_lineage_chains_line`, `LINEAGE_PHRASES`,
  `anonymize_report`, `_anon_register_title`, `_anon_lineage_notes`, `anonymize`.
- Produces:
  - `_relink_codes(entry) -> list` - the note codes of a row (`lineage["notes"]`) or left-out entry
    (`notes`), in order, each once.
  - `_relink_summary(m) -> dict` - in `_lineage_summary`'s shape, counted from the plan, never from
    `_lineage_summary`: `links.carried` = rows with `spawnedFrom` in `new`; `links.left_out` =
    `{code: n}` over left-out entries by each one's first code in `LINEAGE_LEFT_OUT`;
    `chains.carried` = rows with `startChain` in `new`; `chains.left_out` = rows and left-out entries
    with any code in `RELINK_CHAIN_CODES`. It takes the plan (its rows' `new` and `lineage`, and
    its `left_out`), which is what both callers, `_relink_plan_lines` and `_relink_apply_lines`,
    pass.
  - `_relink_tag(codes) -> str` - `[link: <phrase>]` for the first `LINEAGE_LEFT_OUT` code, else
    `[chain: <phrase>]` for the first chain code, else `""`.
  - `_relink_plan_lines(m) -> list`, `_relink_apply_lines(m) -> list` - the totals lines (Global
    Constraints); `_relink_apply_lines` prints `links   : since the plan: ...` when the links line
    has no other part.
  - `_print_relink_report(say, m, pub, verbose=False) -> None` - per account, the rows, the cap,
    the left-out rows under `verbose`, then `_relink_plan_lines(m)`, then `nothing to add` when `m`
    has no rows. Every structural fact (the written keys, the codes and their phrases, the counts,
    the row ids, the order) is read from the plan `m`, never from `pub`; only the displayed
    strings (`label`, `title`, `parent_title`) come from `pub`'s entry at the same index. Today
    `anonymize_report` leaves those structural values alone (below), so this costs nothing and
    keeps the report exact should that ever change.
  - `_public_relink_manifest(env, m) -> dict` - `op_type`, `only` (`_only_display`),
    `account_filter` (`_relink_account_filter_display`), `live_asserted`, `lineage_format`,
    `lineage_cycles`, and when present `op_id`, `identity_disagreement`, `since_plan`, `undo_left`;
    `rows`: `account`, `label`, `name`, `session_id`, `title`, `parent_title`, `store_path`,
    `dest_path`, `adds` (the written keys, in `RELINK_KEYS` order), `keeps` (the kept keys), a copy
    of `lineage`, `written`, and when present `skipped`, `skip_detail`, `restored`; `left_out`:
    `account`, `label`, `name`, `session_id`, `title`, `notes`. Never `old`, `new`, `kept` or a hash.
    Under `--anonymize`: titles and parent titles registered (`_anon_register_title`), then
    `anonymize_report`, then `store_path` and `dest_path` removed from every row,
    `_anon_lineage_notes` over the rows, and every note `detail` (rows' `lineage` and `left_out`)
    and `skip_detail` scrubbed with `anonymize`. What `anonymize_report` does, read from the code:
    it substitutes titles in keys that are not in `_ANON_STRUCTURAL_KEYS`, and in the values of
    `_ANON_FIELDS` (titles, `parent_title` once added) and `_ANON_EMAIL_FIELDS` (`label`); every
    other string value, a note `detail` or an `adds` element among them, is only home-folded and
    uuid-shortened (so `account` comes out shortened). Three consequences: registering every
    emitted key in `_ANON_STRUCTURAL_KEYS` is what stops a title equal to a key from renaming it;
    the explicit `anonymize` scrub of `detail` and `skip_detail` is load-bearing, since nothing
    else title-scrubs them; and the schema values (`adds`, `keeps`, `skipped`, note `code`s) pass
    through untouched. Last, those schema values are set again from `m` anyway, a no-op today
    that keeps them exact should `anonymize_report` ever scrub plain values.
  - `_ANON_FIELDS` gains `"parent_title"`. `_ANON_STRUCTURAL_KEYS` gains `"left_out"`,
    `"account_filter"`, `"since_plan"`, `"changed"`, `"dropped"`, `"gained"`, `"undo_left"`, `"what"`,
    `"why"`, `"adds"`, `"keeps"`, `"restored"`.
  - `_identity_warning_lines(env, m, command="converge")` - the remedy lines name
    `claude-code-sessions <command> --live <name> --apply`; `cmd_converge` keeps the default.

- [ ] **Step 1: Write the failing tests:**

```python
def test_dry_run_lists_rows_per_account_then_the_totals(...):
    # local_c3's P row removed; lines of _print_relink_report(lines.append, m, pub):
    # "-> " + label of A2, "   local_c2  'ACME-REVIEW side task'   link under 'Northwind kickoff'",
    # "", "links   : 1 to add, 1 left out (no row for the parent in that account 1)"
def test_both_keys_print_link_and_chain_and_a_chain_only_row_prints_chain(...):
    # "...   link and chain"; a kept-link row "...   chain"; "chains  : 2 to add"
def test_counts_come_from_written_keys_and_the_left_out_list(...):
    # a chain-only row is not counted on links; "links   : 1 to add" and "chains  : 2 to add"
def test_a_chain_code_is_counted_on_the_chains_line_only(...):
    # an own_chain_differs row -> "chains  : ... 1 left out"; no "own chain" phrase on links
def test_a_written_row_whose_chain_was_left_out_carries_a_chain_tag(...):
    # "   [chain: the chain was left out]" ends local_c3's line
def test_the_listing_is_capped_at_15_and_verbose_lists_all_with_left_out_tags(...):
    # 17 children in A2: 15 row lines then "   ... and 2 more"; verbose=True: 17 lines, then
    # "   local_c3  'ACME-REVIEW side task'   [link: no row for the parent in that account]"
def test_nothing_to_add(...):                      # a store with no candidate -> "nothing to add"
def test_no_rows_with_left_out_rows_prints_totals_then_nothing_to_add(...):
    # local_p2 and local_p3 removed: lines end ["links   : 2 left out (no row for the parent in
    # that account 2)", "nothing to add"]; "nothing to add" appears once; no account header;
    # _public_relink_manifest: rows == [] and len(left_out) == 2
def test_the_three_new_phrases_print(...):
    # local_c3 keeps {}: "links   : 1 to add, 1 left out (the row's own link names no usable
    # row 1)"; link=tool_link("local_p1"): "links   : 2 left out (no app-written link backs it
    # 2)"; "chains  : ... 1 left out" for own_chain_differs
def test_the_identity_warning_names_relink(...):
    # identity_files(env, A1, A2): every remedy line of _identity_warning_lines(env, m,
    # command="relink") contains "claude-code-sessions relink --live"
def test_the_public_projection_omits_old_new_kept_and_hashes(...):
    # no "old", "new", "kept", "pre_sha256", "post_sha256" in any row; rows[0]["adds"] ==
    # ["spawnedFrom"]
def test_the_anonymized_projection_hides_titles_paths_and_both_filters(...):
    # ct._ANONYMIZE = True; RelinkFlags(only="local_c2", account=A2[:8]): out["only"] ==
    # "<only-filter>", out["account_filter"] == "<account-filter>", rows[0]["title"] and
    # ["parent_title"] start "<session-", no "store_path"/"dest_path", rows[0]["account"] == A2[:8]
    # + "…", "Northwind" and "ACME" absent from json.dumps(out)
def test_odd_titles_survive_the_report_and_the_anonymized_json(...):    # Review Focus
    # titles "Café ☕ Northwind" and "left_out": the report printed through a cp1252 say() with
    # cmd_relink's replace fallback raises nothing; under --anonymize out["left_out"] is a list
def test_a_title_equal_to_a_schema_value_corrupts_neither_report(...):
    # the child titled "spawnedFrom", the parent "parent_not_in_account", local_p3 removed;
    # ct._ANONYMIZE = True: pub rows' "adds" == ["spawnedFrom"], left_out notes' codes ==
    # ["parent_not_in_account"]; _print_relink_report(lines.append, m, pub) prints
    # "links   : 1 to add, 1 left out (no row for the parent in that account 1)" and a row line
    # ending "link under '<session-...>'"
```

  And extend `test_every_emitted_report_key_is_in_the_structural_set` with a relink fixture whose
  public manifest has rows, a left-out row, `since_plan` and `undo_left` (set by hand on the
  manifest before projecting): `bad = walk_fixed_keys(ct._public_relink_manifest(env7, m7),
  bad=bad)`, with a sanity assert that `m7["left_out"]` and `m7["rows"]` are non-empty.
- [ ] **Steps 2 to 5** as in Task 2.

### Task 5: The shape check and the journal saver

**Files:** Modify `claude_code_sessions.py` (relink section). Test: `tests/test_relink.py`.

**Interfaces:**
- Consumes: Tasks 1 to 3; `_lineage_record_error`, `_LINEAGE_ROW_ID`, `save_manifest`.
- Produces:
  - `_relink_shape_error(m) -> str | None` - a sentence naming the first damaged field, or `None`.
    NEVER RAISES (an unexpected exception gives `"its record could not be read"`). Checks:
    1. `lineage_format` is the `int` 1; `lineage_cycles`, when present, an `int` (not a bool) of at
       least 0; `rows` a non-empty list; `left_out`, when present, a list of objects each with
       string `account`, `name`, `session_id` and a non-empty `notes` list of `{code: str,
       detail: str}`, each code a key of `LINEAGE_PHRASES`;
       `since_plan`, when present, a map of the three names to non-negative ints, not bools;
       `rolling_back`, when present, a bool; `back_restored`, when present, a non-negative int,
       not a bool; `undo_left` and `back_left`, when present, lists of objects with string `name`,
       `label`, `what`, `why`.
    2. Each row: an object; `account`, `name`, `dest_path`, `store_path`, `session_id` non-empty
       strings; `label`, `title`, `parent_title` strings; `name` is `_LINEAGE_ROW_ID` plus `.json`
       and equals `os.path.basename(dest_path)`; `store_path` is `os.path.dirname(dest_path)`
       (compared normcased and normpathed) and `os.path.basename(os.path.dirname(store_path))` is
       `account`; `pre_sha256`, `post_sha256` 64 lowercase hex. That the store is one this
       version reads is the resolution gate's test (`_row_resolved` with the row's store, through
       `_unresolved_rows`), which undo, recovery and `classify_op` run before acting on a row, as
       `_realign_paths_check` runs before acting on a copy; the shape check stays free of `env`.
    3. `new` a non-empty map with keys in `RELINK_KEYS`; `new["spawnedFrom"]` exactly
       `{sessionId, taskId}`, both non-empty strings; `new["startChain"]` exactly
       `{firstSessionId: non-empty str, depth: int (not bool) >= 1}`.
    4. `old` has exactly `new`'s keys, each `{"present": bool, "value": v}` with `v` not an object,
       and `v is None` when `present` is false.
    5. `kept` a map with keys in `RELINK_KEYS`, object values, none in `new`; a `startChain` in
       `new` needs `spawnedFrom` in `new` or `kept`.
    6. `lineage` passes `_lineage_record_error` and agrees with `new` and `kept` (Global
       Constraints): `parent_row` from `new` or `kept`; `root_row` from `new`, else `""` or the
       kept chain's `firstSessionId` when `kept` holds a `startChain` (judged or not), else `""`;
       `parent_session` and `root_session` non-empty exactly when their rows are; both
       `*_planned` false; every note code a key of `LINEAGE_PHRASES`.
    7. `written` a bool; `restored`, when present, a bool; `skipped`, when present, one of
       `RELINK_SKIP_REASONS`, with `skip_detail` a string; `back_keys`, when present, a list of
       keys of `new`.
  - `class _RelinkSaver` with `__init__(self, op, budget=None, then="")` (reads
    `RELINK_JOURNAL_BYTE_BUDGET` at call time when `budget` is `None`; `then` is the caller's next
    step, the same text it gives `_relink_put`), `tick(self) -> None` (saves while the budget
    lasts; an `OSError` from the save is `Refusal("could not save the operation record in <op_dir>
    (<error>). " + then)`, never a traceback) and attributes `per_save`, `left`.

- [ ] **Step 1: Write the failing tests:**

```python
def test_a_planned_manifest_passes_the_shape_check(...):
    # chain=CHAIN, roots=True, local_c3 keeps side_link("local_p3"): _relink_shape_error(m) is None;
    # so do the plans of Task 2's non-string-title store and of its two-parent-rows store
    # (test_a_kept_link_record_names_the_kept_target_not_the_pick)
def test_a_link_only_row_that_keeps_an_unjudged_chain_passes(...):
    # the Task 2 "outside the record" store -> None
def test_a_kept_chain_record_may_name_it_or_not(...):
    # that row with lineage root_row "" passes, with the kept chain's firstSessionId (and its
    # root_session set) passes, with "local_r9" fails naming "lineage"
@pytest.mark.parametrize("damage, named", [
    (lambda m: m["rows"][0].__setitem__("old", []), "'old'"),
    (lambda m: m["rows"][0]["old"]["spawnedFrom"].__setitem__("value", {}), "'old'"),
    (lambda m: m["rows"][0]["new"].__setitem__("cwd", "C:\\x"), "'new'"),
    (lambda m: m["rows"][0]["new"]["spawnedFrom"].__setitem__("title", "N"), "'new'"),
    (lambda m: m["rows"][0]["kept"].__setitem__("spawnedFrom", {}), "'kept'"),
    (lambda m: m["rows"][0]["lineage"].__setitem__("parent_row", "local_p9"), "lineage"),
    (lambda m: m["rows"][0].__setitem__("pre_sha256", "xyz"), "'pre_sha256'"),
    (lambda m: m["rows"][0].__setitem__("title", 5), "'title'"),
    (lambda m: m["rows"][0].__setitem__("written", "yes"), "'written'"),
    (lambda m: m["rows"][0].__setitem__("restored", 1), "'restored'"),
    (lambda m: m["rows"][0].__setitem__("skipped", "because"), "'skipped'"),
    (lambda m: m["rows"][0].__setitem__("name", "..\\x.json"), "'name'"),
    (lambda m: m.__setitem__("lineage_format", 2), "lineage_format"),
    (lambda m: m.__setitem__("rows", ["x"]), "row 0"),
    (lambda m: m["rows"][0]["lineage"].__setitem__("parent_session", ""), "lineage"),
    (lambda m: m["rows"][0]["lineage"]["notes"].append({"code": "made_up", "detail": "x"}),
     "lineage"),
    (lambda m: m["rows"][0].__setitem__("store_path", m["rows"][1]["store_path"]),
     "'store_path'"),
    (lambda m: m["rows"][0].__setitem__("account", m["rows"][1]["account"]), "'account'"),
    (lambda m: m.__setitem__("since_plan", {"changed": True, "dropped": 0, "gained": 0}),
     "since_plan"),
    (lambda m: m.__setitem__("rolling_back", "yes"), "rolling_back"),
    (lambda m: m.__setitem__("back_restored", True), "back_restored"),
    (lambda m: m.__setitem__("back_left", [{"name": "local_c2.json"}]), "back_left"),
])
def test_damaged_fields_are_named(damage, named, ...):   # the sentence contains NAMED
def test_the_shape_check_never_raises(...):              # m = {}, rows None, lineage "x" -> a str
def test_the_journal_holds_no_row_images_and_a_row_costs_a_bounded_size(...):
    # 60 children (LC-like uuids), each with an app-written link in A1 and a 16 KB "filler" key
    # in its A2 row; m = plan_relink(...): len(m["rows"]) == 60; every row's
    # len(json.dumps(r, indent=1).encode()) < 3072; len(json.dumps(m, indent=1).encode()) <
    # 60 * 3072; "FILLER" not in the dump; no key in any row ends with "_b64".
    # Why 60 and not the design's 931: the bound is per row, so a row of 135 KB costing under
    # 3 KB is what scales to 931 rows (under 3 MB, against about 126 MB of row bytes), and 60 rows
    # prove it without 15 MB of fixtures in every suite run.
def test_the_saver_saves_per_row_while_the_budget_lasts(monkeypatch, ...):
    # count save_manifest calls; saver = ct._RelinkSaver(op, budget=2 * per_save) where per_save
    # = len(json.dumps(op.manifest, indent=1)) + 4096; five ticks -> exactly 2 saves; with
    # save_manifest raising OSError, tick raises Refusal naming op.op_dir and ending with THEN
```

- [ ] **Steps 2 to 5** as in Task 2.

### Task 6: Applying, the CLI, and the row reader

**Files:**
- Modify: `claude_code_sessions.py` - the relink section; `build_parser` (a `relink` parser right
  after `realign`'s, about 5730); `main`'s handlers (about 6149).
- Modify: `tests/test_journal_reroot.py` - `_ROW_READERS` (about 899).
- Test: `tests/test_relink.py`.

**Interfaces:**
- Consumes: Tasks 1 to 5; `_guard_mutation`, `NEW_ROW_STORE`, `acquire_lock`, `release_lock`,
  `new_op`, `set_status`, `rotate_ops`, `_retitle_roots`, `_retitle_contained`,
  `_strict_row_dict`, `atomic_write`, `_maybe_crash`, `redact`, and `_realign_recheck`, whose
  RULING 5 refusal `_relink_recheck` words the same way (with `relink --live`), so the two
  commands do not drift.
- Produces:
  - `RELINK_GUARD_WHY = ("The app holds these rows in memory and rewrites them on focus, so a key
    written underneath it is whatever the app decides later, not what you wrote")`.
  - `_relink_read(r, roots) -> tuple` - `_retitle_contained(r, roots)` first (its `LayoutError`
    propagates), then one read of `r["dest_path"]`: `("present", data, raw)`, `("absent", None,
    None)`, or `("unreadable", None, raw_or_None)` for an `OSError`, a strict-parse failure or a
    non-object. The one relink function that opens a row.
  - `_relink_put(r, value: dict, roots, then: str) -> bool` - first `_retitle_contained(r, roots)`
    itself (its `LayoutError` propagates), whatever its caller read before, since a journal can be
    edited; then `atomic_write(r["dest_path"], _relink_row_bytes(value))`; an `OSError` is
    `Refusal("could not write row <name> (<label>): <error>. " + then)`. Then one `_relink_read`,
    returning `True` when it is `"present"` with a value equal to `value`, else `False`. What a
    `False` does is the caller's, each route stating its own (the apply and forward mark and
    raise; undo and `back` leave the row unmarked and raise). `then` is the caller's own next step,
    every one runnable: the apply's and forward's `The op is left at 'writing' - 'recover --resolve
    <op_id> --back --apply' takes out what landed.`, undo's `The relink stays 'completed'; run
    'undo --apply' again to finish it.`, back's `The op is left at '<status>' - run 'recover
    --resolve <op_id> --back --apply' again; it picks up where this stopped.`
  - `_relink_row_agrees(r, data: dict) -> bool` - the journal agrees with the row it names
    (D15 leaves no image to fall back on): for each key of `r["new"]`, `old[key]["present"]` is
    `key in data`, and when present `data[key]` is not an object and `_relink_same(data[key],
    old[key]["value"])`; and `_relink_sha(_relink_row_bytes(_relink_apply_new(data,
    r["new"]))) == r["post_sha256"]`. Never raises (`False`).
  - `_relink_recheck(env, m) -> None` - under the lock, before `new_op`: RULING 5 as
    `_realign_recheck` words it with `relink --live`; then `fresh = plan_relink(env,
    RelinkFlags(only=m["only"], account=m["account_filter"], live=m["live_asserted"]))` (its
    refusals refuse the run, nothing journaled); then `m["since_plan"]` by `(account, name)`:
    `changed` rows in both whose `new` differs (`_relink_same`), `dropped` in the plan only,
    `gained` in the fresh plan only; then `m["rows"]`, `m["left_out"]`, `m["lineage_cycles"]` take
    the fresh plan's.
  - `_relink_write_rows(op, m, roots, saver) -> None` - per row not `written`, in journal order:
    `_maybe_crash("relink-mid-rows")` before every row after the first; read; `"absent"` or
    `"unreadable"` refuses; a `cliSessionId` other than `session_id` refuses (`now opens another
    conversation`, D19); `_relink_sha(raw) == post_sha256` marks it `written` (a landed write);
    `!= pre_sha256` refuses (`changed since this relink was planned - writing would discard
    whatever changed it, most likely the app. The op is left at 'writing' - 'recover --resolve
    <op_id> --back --apply' takes out what landed, then re-run to replan.`); `_relink_row_agrees(r, data)`
    false is `LayoutError` (`the record for <op_id> does not match row <name>; refusing rather than
    write from it`); otherwise `ok = _relink_put(r, _relink_apply_new(data, r["new"]), roots,
    <the apply's then>)`, `_maybe_crash("relink-before-mark")`, `written = True`. When `ok` is
    false: `save_manifest(op)`, then `LayoutError("verify failed on <name>: the row on disk is not
    the found value with exactly relink's keys set. 'recover --resolve <op_id> --back --apply' takes
    out what it can check and names the rest.")`, as `_realign_write_rows` and
    `_retitle_write_rows` mark and save before raising. Otherwise `saver.tick()`. Every refusal
    leaves the op at `writing`.
  - `execute_relink_op(env, op) -> str` - `roots = _retitle_roots(env)` first, fresh (it refuses
    when discovery fails; never roots from the journal), then `journaled` -> `writing` ->
    `completed`, the rows written by `_relink_write_rows(op, m, roots, _RelinkSaver(op,
    then=<the apply's then>))`, saving the manifest on the way out of any exception,
    `_maybe_crash("relink-after-rows")` after the rows.
  - `run_relink(env, manifest) -> str` - `_guard_mutation(env, "add links to rows in",
    NEW_ROW_STORE, because=RELINK_GUARD_WHY)`; `_relink_refuse_open(env, "relink")`;
    `acquire_lock(env, "relink")`; `_relink_recheck`; `"unchanged"` without an op when no row is
    left; else `new_op`, `manifest["op_id"]`, `execute_relink_op`, `rotate_ops`; the lock released
    in `finally`. An `OSError` from `new_op` is `Refusal("could not write the operation record
    ({error}). The journal is written before any row is touched, so nothing landed - fix the space
    or permissions under <ops_dir> and re-run.")`, `run_realign`'s and `run_retitle`'s wording. An
    `OSError` from saving the `writing` status (in `execute_relink_op`, before its first row) is
    `Refusal("could not save the operation record in <op_dir> ({error}). Nothing landed; the op is
    left at 'journaled' - 'recover --resolve <op_id> --back --apply' closes it.")`. Both are raised
    before any row is written, never a traceback.
  - `cmd_relink(env, ns) -> int` - `say` as `cmd_realign`'s (redact unless `--verbose`, the
    `UnicodeEncodeError` fallback). Plan; unless `--json`, the identity warning lines
    (`command="relink"`) before anything else, then `_print_relink_report(say,
    m, _public_relink_manifest(env, m), ns.verbose)`, printed once in every path. Without
    `--apply`: `dry run - pass --apply to add them` only when the plan has rows; exit 0. With
    `--apply` and a plan with no rows: `final = "unchanged"`, no run, and only `result  :
    unchanged` follows the report (which already said `nothing to add`). With `--apply` and rows:
    `final = run_relink(env, m)`; then `result  : <final>`, `_relink_apply_lines(m)`, and the
    reopen line on `completed`, or `nothing to add` on `unchanged` (the locked re-plan left no
    row). `--json` prints the public manifest with `result` when applied. Exit 0 on `completed` or
    `unchanged`, rows left out or not (D9); 1 otherwise.
  - The parser, verbatim:

```python
rl = sub.add_parser(
    "relink",
    help="give existing rows of a side session the parent link another account's row carries",
    description="Give existing rows of a side session the parent link (spawnedFrom), and its "
                "chain (startChain), that another account's row carries, translated to that "
                "account's own rows as converge writes them on a new row. Only those two keys "
                "change, and only where a row has no link object; rows whose accounts disagree "
                "are left out with the reason. Undoable per key.")
rl.add_argument("--only", default="", metavar="TITLE_OR_ID",
                help="narrow to one conversation (a title substring, or a cliSessionId prefix) "
                     "or to one row (local_ and a prefix of its id) - must resolve to exactly "
                     "one; a conversation that leaves more than one row to change refuses and "
                     "lists their ids")
rl.add_argument("--account", default="", metavar="SUBSTRING",
                help="narrow to one account's store: an account id, org id, store path or email "
                     "substring that resolves to exactly one account, the signed-in account's "
                     "included")
rl.add_argument("--live", default="", metavar="SUBSTRING",
                help="assert which account the desktop app is signed into (RULING 5), when the "
                     "identity files disagree")
rl.add_argument("--apply", action="store_true",
                help="actually add the links. Needs the desktop app fully closed and no "
                     "unfinished operation. Exits 0 when rows were left out - each is counted "
                     "by its reason")
rl.add_argument("--json", action="store_true", help="print the plan as JSON")
common(rl)
```

  - `main`'s handlers gain `"relink": cmd_relink`.
  - `_ROW_READERS` gains `"_relink_read": "checks the row with _retitle_contained first; the apply
    reads only rows its own locked plan scanned, undo_relink refuses unresolved rows first, and
    recover_op passes them in to be skipped"`.

- [ ] **Step 1: Write the failing tests:**

```python
def ns(**kw): ...      # json, verbose, anonymize, apply False; only, account, live ""
def test_apply_writes_each_row_and_journals_it(...):
    # run_relink -> "completed"; json.load(local_c2) == {**before, "spawnedFrom":
    # {"sessionId": "local_p2", "taskId": "task_7f"}}; op_type "relink", status "completed";
    # every row written is True (a bool) and has no "restored"; post_sha256 ==
    # sha256(open(dest_path, "rb").read()); no row key ends with "_b64"
def test_a_row_with_many_app_keys_keeps_all_of_them_in_order(...):
    # APP_KEYS row: list(after) == list(before) + ["spawnedFrom"], every other value equal
def test_an_unfinished_operation_refuses_the_apply_and_journals_nothing(...)
def test_a_row_changed_after_the_locked_plan_refuses_and_leaves_writing(...):
    # crash hook at "relink-mid-rows" rewrites local_c3 (lastFocusedAt) and returns; run_relink
    # -> Refusal "changed since this relink was planned"; nonterminal op status "writing";
    # local_c2 written, local_c3 not
def test_a_store_path_that_is_not_the_rows_folder_is_unsafe(...):
    # crash hook edits op rows[1]["store_path"] to A1's store -> LayoutError "not a direct child"
def test_a_row_repointed_mid_run_refuses(...):            # hook repoints local_c3 -> "now opens
                                                          # another conversation"
def test_the_verify_is_by_value(monkeypatch, ...):
    # atomic_write patched to write {"cliSessionId": LC} -> LayoutError "verify failed";
    # rows[0]["written"] is True in the manifest on disk (marked and saved before the raise);
    # the op is at "writing"
def test_a_record_that_does_not_match_its_row_is_not_written_from(...):
    # crash hook at "relink-mid-rows" edits op rows[1]["old"]["spawnedFrom"] to {"present": True,
    # "value": "x"} -> LayoutError "does not match row local_c3.json"; local_c3 unchanged
def test_a_write_that_fails_names_the_recover_command(monkeypatch, ...):
    # atomic_write raises OSError for local_c3 -> Refusal containing "recover --resolve " + op_id
    # + " --back --apply" (a hint without --apply would only preview)
def test_a_row_outside_every_discovered_root_is_refused(tmp_path, ...):
    # crash hook at "relink-mid-rows" copies local_c3 to tmp_path / "elsewhere" / <A3> / <O3> and
    # points op rows[1]["dest_path"] and ["store_path"] there -> LayoutError "outside every
    # recognized root"; the copy is unchanged: the roots came from discovery, not the journal
def test_a_journal_that_cannot_be_written_refuses_before_any_row(monkeypatch, ...):
    # (a) new_op raises OSError -> Refusal "could not write the operation record" naming
    # env.ops_dir; (b) set_status raises OSError for "writing" -> Refusal "could not save the
    # operation record", the op left at "journaled". Either way local_c2 and local_c3 are
    # byte-identical to before, and cmd_relink(apply) returns 1 through main without a traceback
def test_the_apply_replans_under_the_lock_and_says_what_changed(capsys, ...):
    # plan; then local_p3 removed and a second child LD (rows in A1 linked, A2 unlinked) added;
    # cmd_relink(apply) -> m["since_plan"] == {"changed": 0, "dropped": 1, "gained": 1};
    # out contains "links   : 2 added, 1 left out (no row for the parent in that account 1);
    # since the plan: 1 dropped, 1 gained"
def test_nothing_to_add_journals_nothing(capsys, ...):    # exit 0, "nothing to add", no op
def test_a_plan_with_only_left_out_rows_prints_once_and_never_the_apply_hint(capsys, ...):
    # local_p2 and local_p3 removed. Dry run: the report once, "nothing to add" once, no "dry run -
    # pass --apply". --apply: the report once, "nothing to add" once, then "result  : unchanged",
    # no op journaled. --json --apply: rows == [], len(left_out) == 2, result "unchanged"; exit 0
def test_a_locked_replan_that_leaves_nothing_says_so_after_the_result(capsys, ...):
    # plan has local_c2 and local_c3; before the lock both P rows are removed (monkeypatch
    # acquire_lock to delete them first); out has "result  : unchanged" then "links   : 2 left out
    # (no row for the parent in that account 2); since the plan: 2 dropped" then "nothing to add"
def test_an_unreadable_row_at_apply_refuses_and_journals_nothing(...):
    # local_p2 corrupted after the plan -> Refusal "could not be read"; list_ops == []
def test_rows_left_out_still_exit_0_and_json_carries_the_result(capsys, ...):
    # local_p3 removed; --apply --json -> 0, json "result" == "completed", left_out non-empty
def test_the_help_texts(...):
    # build_parser()'s relink subparser: the five options' help strings as in the plan
def test_a_rerun_adds_only_what_the_first_run_made_resolvable(capsys, ...):   # Review Focus
    # (a) the base store: apply, then a second cmd_relink --apply prints "nothing to add", and the
    # journal holds one op. (b) Codex's two-account store: P, C and LR rows in A1 and A2;
    # local_c1 keeps side_link("local_p1") and no chain; local_c2 has no link and keeps
    # {"firstSessionId": "local_r2", "depth": 2}. First apply: local_c2 gains its link only
    # (its kept chain unjudged), local_c1 is left out ["lineage_chain_dropped"]. Second apply:
    # local_c1 gains {"firstSessionId": "local_r1", "depth": 2} and nothing else. Third:
    # "nothing to add"
```

  Also run `tests/test_journal_reroot.py::test_every_row_reader_goes_through_the_gate`.
- [ ] **Steps 2 to 5** as in Task 2.

### Task 7: Claims, and the other operations' ownership checks

**Files:**
- Modify: `claude_code_sessions.py` - the relink section; `rotate_ops` (about 1299);
  `_repoint_claimed_later` (about 10437); `_retitle_claimed_elsewhere` (about 12310);
  `_realign_claimed_later` (about 13666).
- Modify: `tests/test_realign.py` - `_OP_TYPE_BRANCHES` rows and flips (Task 1's table).
- Test: `tests/test_relink.py`.

**Interfaces:**
- Consumes: Tasks 1 to 6, `_relink_load_ops` among them; `_op_sort_key`, `_journal_rows`,
  `NONTERMINAL`.
- Produces:
  - `_relink_claims(env, m, rows) -> list` - D13, `[{"row": r, "op_id": str, "keys": set,
    "why": str}]`, one entry per claimed row and claimant; `keys` the claimed subset of `r["new"]`.
    NEVER RAISES. A journal folder that cannot be listed claims every row asked about. For every
    loaded operation other than `m`'s own:
    - `None` (unreadable), or rows that are not a list of objects with string paths: claims every
      row asked about, all keys, `why` `its record cannot be read`;
    - a sort key that cannot be computed counts as later; an earlier operation: nothing;
    - any type but `relink` whose status is `undone` or `rolled_back`: nothing;
    - `realign`: nothing (it writes other keys);
    - `relink`, whatever its status (`restored` alone says a row was put back; a row with a key
      still at `new` stays unmarked after undo or back, and its claim stands): per row with the
      same normalised `dest_path`, not `restored`, and `written` or the op non-terminal: the keys
      of `r["new"]` it wrote or kept (`set(orow["new"]) | set(orow["kept"])`), when any;
    - `move` and `undo` (`_realign_claimed_later`'s "moveish" rule): every row they name, by its
      `path`, flag or not, all keys. Their rows carry `path`, `pre_b64`, `post_b64` and
      `rewritten`, never `written` (`plan_move`, `plan_undo`), so a flag test would miss them;
    - any other type: per row with the same path (`_journal_rows`), when the row is `written` or
      the op is non-terminal: all keys.
  - `rotate_ops`: `claimants = ("sync", "retitle", "realign", "relink")`.
  - `_retitle_claimed_elsewhere`: a new `relink` branch, taken on `op_type` before the generic
    loop, in a `try` of its own whose failure is a claim (returns the relink's op id), never the
    function's broad `except` that returns `None`. In it: a sort key that cannot be computed, rows
    that are not a list of objects, or a row whose `dest_path` is not a string claims; the relink
    is passed over when `_op_sort_key(om) < _op_sort_key(m)`; a row marked `restored` is passed
    over, whatever the relink's status (its undo or back leaves a row unmarked exactly when a key
    stays at `new`); otherwise a `written` row with the same path claims when its `post_sha256` is
    not a string or equals `hashlib.sha256(post).hexdigest()`.
  - `_repoint_claimed_later`: the same shape of branch (its own `try`, malformed is a claim); a
    `relink` op counts only when `_op_sort_key(om) > _op_sort_key(m)` and the row is `written` and
    not `restored`, whatever the relink's status.
  - `_realign_claimed_later`: `continue` past a `relink` op on `op_type`, before any of its rows is
    read (D13's per-key exception: a relink never writes realign's keys). So a malformed relink
    manifest claims nothing here and cannot reach the broad `except` either; the test pins that it
    does not hide a later realign's claim.
  - `_OP_TYPE_BRANCHES`: flip `rotate_ops` and `_realign_claimed_later` to `names it`, give
    `_collides` its final reason; add
    `_relink_claims`, `_retitle_claimed_elsewhere`, `_repoint_claimed_later` (Task 1's table).

- [ ] **Step 1: Write the failing tests.** Later operations are hand-made manifests saved with
  `ct.new_op` after the relink's (a monotonic `env.now`), naming `local_c2`'s path, in each type's
  real row shape (`written` on sync, retitle, repoint, new-row and converge rows); `move` and
  `undo` come from a real run, never a hand-made `written`:

```python
@pytest.mark.parametrize("op_type", ["sync", "retitle", "repoint", "new-row", "converge"])
def test_a_later_live_operation_that_wrote_the_row_claims_every_key(op_type, ...):
    # -> one entry, op_id the later op's, keys == set(row["new"])
def test_a_later_move_and_its_undo_claim_the_row_without_a_written_flag(...):
    # after the relink, a real run_move of LC's transcript to another project folder (the
    # `moved` helper's pattern in tests/test_journal_reroot.py) rewrites local_c1..3: its rows have
    # no "written", and _relink_claims names the move for local_c2. Then a real run_undo of it:
    # the move is "undone" and claims nothing; the undo op claims local_c2
def test_a_later_relink_claims_only_the_keys_it_wrote_or_kept(...):
    # later relink row new {"startChain": ...}, kept {"spawnedFrom": ...} -> claims
    # {"spawnedFrom"}; later new {"startChain": ...}, kept {} -> no entry
def test_a_later_realign_claims_nothing(...)
def test_a_non_terminal_later_op_naming_the_row_claims_it_written_or_not(...)
def test_an_earlier_or_withdrawn_op_and_a_restored_row_claim_nothing(...):
    # an earlier sync; a later sync "undone"; a later relink "rolled_back" whose rows are all
    # restored; a later completed relink whose row is restored -> []
def test_a_later_relink_row_left_unrestored_claims_whatever_its_status(...):
    # a later relink "undone" (and, separately, "rolled_back") whose local_c2 row is written and
    # not restored, its spawnedFrom left at new -> one entry naming it, keys {"spawnedFrom"}
@pytest.mark.parametrize("bad", ["{not json", '{"op_id": "x", "rows": "nope"}',
                                 '{"op_id": "x", "status": "completed", "rows": [5]}'])
def test_an_unreadable_or_damaged_manifest_claims_every_row_without_crashing(bad, ...)
def test_the_lock_file_and_stray_entries_are_not_operations(...):      # Review Focus
    # ops_dir holds "lock", an empty folder and "notes.txt" -> _relink_claims == []
def test_retitle_claim_passes_an_earlier_relink_and_a_restored_row(...):
    # _retitle_claimed_elsewhere(env, retitle_m, r) is None for an earlier relink with the same
    # post_sha256; a later relink with the same post_sha256 claims; restored passes
def test_repoint_claim_counts_only_a_later_unrestored_relink(...):
    # earlier relink -> None; later completed written -> its op_id; later with the row restored
    # -> None; later "undone" with the row restored -> None, with the row left unrestored -> its
    # op_id
def test_realign_claim_passes_a_later_relink(...)
@pytest.mark.parametrize("damage", ['"rows": "nope"', '"rows": [5]',
                                    'a row whose dest_path is 7'])
def test_a_malformed_later_relink_is_a_claim_in_the_retitle_and_repoint_checks(damage, ...):
    # a later relink manifest so damaged, naming local_c2 where it can: _retitle_claimed_elsewhere
    # and _repoint_claimed_later each return its op_id, never None, and raise nothing. (A history
    # list_ops cannot sort fails list_ops itself, in every command, before either branch runs:
    # existing behaviour, out of this plan's reach.)
def test_a_malformed_relink_does_not_hide_a_later_realigns_claim(...):
    # a malformed relink manifest ('"rows": "nope"') beside a later realign that wrote the row:
    # _realign_claimed_later returns the realign's op_id
def test_rotation_keeps_a_terminal_relink_a_live_claimant_names(...):
    # 11 completed ops, the oldest a relink that wrote local_c2, and a non-terminal retitle naming
    # local_c2 -> rotate_ops keeps the relink's folder
def test_an_earlier_relink_does_not_block_a_repoint_rollback(...):
    # relink completed on local_c2; a repoint of local_c2 stalls after its write (crash); recover
    # --back restores the repoint's pre-image, and rollback_declined is absent
```

- [ ] **Steps 2 to 5** as in Task 2.

### Task 8: `undo`

**Files:**
- Modify: `claude_code_sessions.py` - the relink section; `cmd_undo` (about 5827: the candidate
  tuple, the preview, the dispatch, the printed lines).
- Modify: `claude_code_sessions_gui.py` - `_find_undoable_op` (about 3688).
- Modify: `tests/test_realign.py` - add `undo_relink`; flip `cmd_undo`; the GUI pair `(True, True)`.
- Test: `tests/test_relink.py`.

**Interfaces:**
- Consumes: Tasks 1 to 7 (`_relink_read`, `_relink_put`, `_RelinkSaver`, `_relink_claims`,
  `_relink_refuse_open`, `_relink_shape_error` among them); `_retitle_roots`,
  `_unresolved_rows`, `_journal_discovery`, `_unresolved_clause`.
- Produces:
  - `undo_relink(env, op) -> str` - returns `"undone"`. Under `acquire_lock(env, "undo-" +
    op_id)`, in order:
    1. `op_type` is `relink` (`Refusal("not a relink op: ...")`), status `completed`;
       `_relink_shape_error` refuses (`the record for <op> is damaged (<why>); refusing rather than
       guess. Nothing was changed.`).
    2. `_guard_mutation(env, "take links out of rows in", NEW_ROW_STORE,
       because=RELINK_GUARD_WHY)`; `_relink_refuse_open(env, "relink's undo", mine=<op_id>)` (D18).
    3. Unresolved written rows refuse (`_unresolved_rows(..., written_only=True)`), as
       `undo_realign` words it. Then `roots = _retitle_roots(env)`, fresh (Global Constraints),
       for every `_relink_read` and `_relink_put` below.
    4. **Preflight, before any write.** `todo` = rows `written` and not `restored`. Each is read
       once (`_relink_read`): any `"unreadable"` row refuses the whole undo now, nothing written
       (`could not read row <name> (<label>), so nothing was changed; the relink stays 'completed'
       and 'undo --apply' resumes it once the row can be read.`). Each row's disposition is
       decided from that read: `"absent"` is one entry (`the row`, `gone from the store`);
       another `cliSessionId` is one entry (`the row`, `now opens another conversation`), nothing
       written, and its keys' states (`_relink_key_state` per key) decide its mark in step 6;
       otherwise each key of `new`, by `_relink_key_state`: `"new"` goes back, `"old"` counts as
       restored, `"other"` is an entry (`<key>`, `the app changed it since`).
    5. `_relink_claims(env, m, todo)`: any entry refuses: `cannot undo <op>: operation(s) <ids,
       sorted, each once> wrote row(s) <rows> after it - undo those first. Nothing was changed.`
       The relink stays `completed`.
    6. **Writes**, per row of `todo` in journal order, with a `_RelinkSaver(op, then=<undo's
       then>)`, inside `try ... except BaseException: save_manifest(op); raise`: a row with keys to
       put back gets one `_relink_put(r, _relink_apply_old(data, r["old"], keys), roots, <undo's
       then>)` from the preflight's read (the lock is held and the app closed); a `False` from it
       is `LayoutError("verify failed restoring row <name>: ... 'undo --apply' again re-reads it
       and goes by what each key holds.")`, the row left unmarked so a resumed undo judges it
       afresh (the `except` saves what earlier rows recorded). Then the row's entries are appended
       to `m["undo_left"]`, each only if not already there; then
       `_maybe_crash("relink-undo-before-mark")`; then `restored = True` only when no
       relink-written key remains at `new` on the row (every key put back, already at `"old"`, or
       changed by the app, or the row gone). A repointed row whose key is still at `new` keeps its
       entry and stays unmarked, so its claim stands after the undo (Global Constraints). Then
       `saver.tick()`. A write `OSError` refuses with undo's `then`; the rows already done keep
       their entries and marks on disk, and `undo --apply` again resumes.
    7. `set_status(op, "undone")`; `rotate_ops`. No link report (spec, `undo` step 4).
  - `_relink_left_line(e) -> str` - `left    : <row> -> <label>: <what> (<why>)`.
  - `cmd_undo`: `"relink"` joins the candidate tuple; the preview line (Global Constraints, `n` the
    rows written and not restored); dispatch to `undo_relink`; after `result:`, each
    `_relink_left_line` through `redact` unless `--verbose`.
  - `_find_undoable_op`: `"relink"` joins its tuple; its docstring says relink is CLI-only and
    parked as realign is, so a converge before it is undone from the CLI.

- [ ] **Step 1: Write the failing tests:**

```python
def applied(env, ...): ...   # relink_store + run_relink; returns (paths, op)
def test_undo_takes_out_exactly_what_relink_added(...):
    # undo_relink -> "undone"; json.load(local_c2) == before, list order equal; status "undone";
    # every row restored is True
def test_undo_keeps_the_keys_the_app_added_since(...):        # lastFocusedAt added -> kept
def test_undo_leaves_and_names_a_key_the_app_changed(capsys, ...):
    # app sets spawnedFrom taskId "task_app" on local_c2 -> undo_left == [{"name":
    # "local_c2.json", "label": <A2 label>, "what": "spawnedFrom", "why": "the app changed it
    # since"}]; cmd_undo prints "left    : local_c2 -> <A2 label>: spawnedFrom (the app changed it
    # since)"; local_c3's link is gone; local_c2 restored True (no key of relink's left at new)
@pytest.mark.parametrize("change", ["gone", "repointed"])
def test_undo_skips_and_names_a_missing_or_repointed_row(change, ...):
    # undo_left [{"what": "the row", "why": "gone from the store" / "now opens another
    # conversation"}]; the gone row is restored True; the repointed row keeps its link, is NOT
    # marked restored, and its claim stands: _relink_claims for a hand-made earlier relink naming
    # local_c2 with new {"spawnedFrom": ...} returns this (now "undone") op, keys {"spawnedFrom"}
def test_undo_restores_a_non_object_exactly(...):              # spawnedFrom False before ->
                                                               # False after, same position
def test_a_resumed_undo_after_a_crash_before_the_mark_names_nothing_false(...):
    # hook raises at the first "relink-undo-before-mark"; release_lock; undo again -> "undone",
    # undo_left == []
def test_an_unreadable_row_refuses_the_undo_before_any_write(...):
    # local_c3 "{not json" -> Refusal "nothing was changed"; local_c2 still carries its link;
    # status "completed"; no row restored; local_c3 restored by hand -> undo again "undone"
def test_an_interrupted_undo_keeps_what_it_left_and_reports_it_once(monkeypatch, ...):
    # the app changes local_c2's spawnedFrom taskId; atomic_write raises OSError for local_c3 ->
    # Refusal containing "run 'undo --apply' again"; on disk: local_c2 restored True and
    # undo_left == [local_c2's "spawnedFrom" entry]; unpatched undo again -> "undone", undo_left
    # holds that entry exactly once
def test_undo_waits_while_another_operation_is_unfinished(...)
def test_a_later_live_op_refuses_the_undo_and_names_it(...):
    # a real retitle of LC to "ACME-REVIEW side task, round two" after the relink ->
    # Refusal names its op_id; relink "completed"; links still on disk
def test_a_later_relink_of_the_same_key_refuses_and_of_the_other_key_only_does_not(...)
def test_a_later_realign_does_not_refuse(...)                  # hand-made realign manifest
def test_cmd_undo_previews_the_relink(capsys, ...):
    # "would undo <op> (relink: takes out the links and chains it added to 2 row(s), key by
    # key; a key changed since is left and named); pass --apply to execute"
def test_the_window_offers_no_undo_while_a_relink_is_latest(...):
    # gui.SyncApp._find_undoable_op(types.SimpleNamespace(env=env)) is None
def test_undo_takes_out_a_reordered_link_and_leaves_an_extended_one(...):   # Review Focus
    # local_c2's link rewritten as {"taskId": ..., "sessionId": ...} -> taken out;
    # local_c3's link gains "title": "Northwind" -> left, "the app changed it since"
def test_an_earlier_relink_and_a_later_retitle_leaving_the_same_bytes_are_each_undoable(...):
    # local_c2 titleSource "user"; relink; retitle LC in A2's store to the same title (bytes
    # unchanged); undo_retitle -> "undone"; undo_relink -> "undone"; local_c2 == before
def test_a_later_relink_blocks_a_repoint_undo_and_a_restored_one_does_not(...):
    # repoint local_c2 onto LC; relink adds its link; undo_repoint refuses ("no longer holds");
    # undo_relink; undo_repoint -> "undone"
def test_a_realign_undo_after_a_relink_of_the_same_row_succeeds(...):
    # test_realign's World layout plus links; realign, relink; undo_realign -> "undone",
    # the link kept
def test_a_converge_undo_deletes_a_relinked_row_and_relink_undo_names_it_gone(...):
    # a converge-created row relinked; undo --op-id <converge> deletes it; undo_relink ->
    # undo_left [{"what": "the row", "why": "gone from the store"}]
def test_undoing_a_converge_whose_created_parent_a_relink_linked_to_reports_it_dangling(...):
    # A3 lacks P: converge creates P's row there (local_<uuid>); relink then links local_c3 to it;
    # undo_converge (by op id) removes that row -> its link_report["dangling"] holds {"row":
    # "local_c3", "target": <the created row id>, "kind": "parent"}; cmd_undo prints
    # "link left dangling: local_c3 (its parent's row was removed)"
```

- [ ] **Steps 2 to 5** as in Task 2, plus `python tools/check_gui_undo.py` exiting 0.

### Task 9: Recovery, forward and back

**Files:**
- Modify: `claude_code_sessions.py` - the relink section; `classify_op` (the D18 test after
  `kind` about 2918, shape table about 2920, dispatch about 3060); `recover_op` (allowlist about
  3273, dispatch about 3295); `cmd_recover` (about 6095).
- Modify: `tests/test_realign.py` - flip `classify_op`, `recover_op`, `cmd_recover`.
- Modify: `tests/test_journal_reroot.py` - two tests.
- Test: `tests/test_relink.py`.

**Interfaces:**
- Consumes: Tasks 1 to 8 (`_relink_put`, `_relink_row_agrees`, `_relink_claims`,
  `_relink_open_others` among them); `_lineage_dependency_skip`, `_lineage_rows_by_name`,
  `_lineage_store_rows`, `_lineage_index`, `_lineage_existing_edges`, `_lineage_reaches`,
  `_op_link_report`, `_add_residue`, `_unresolved_residue`, `_retitle_roots`.
- Produces:
  - `_relink_row_states(r, cur) -> dict` - `{key: _relink_key_state(...)}` over `r["new"]`.
  - `_relink_landed(states) -> bool` - at least one key at `"new"`, whatever the others hold
    (recovery goes by key; Departure 9).
  - `_relink_dependency_skip(m, r, by_name) -> tuple | None` - `_lineage_dependency_skip` on a copy
    of `r` whose `lineage` has `root_row` emptied unless `startChain` is in `new`. `parent_row` is
    always checked: a row that writes the link depends on its target, and a chain-only row
    depends on the kept link it stands behind, so a kept parent row that is gone, unreadable or
    now opens another conversation skips the chain (`"parent_changed"`).
  - `_relink_d18_note(env, m) -> dict | None` - the classification for an op that waits on D18,
    from `_relink_open_others(env, mine=m["op_id"])`: the op excludes itself, since a recovering
    relink is itself non-terminal and would otherwise always wait on itself. `resolutions: []`,
    note `relink: recovery waits while another operation is unfinished (<ids>); recover that
    first` (or the line naming every unreadable record); `None` when nothing else waits. Never
    raises.
  - `classify_relink_op(env, op) -> dict` - never raises. A shape error: `resolutions: []`, note
    `relink: this operation's record is damaged (<why>), so neither direction can be run from it`.
    Terminal: note `relink: <n> row(s) written (use undo to take a completed relink's links out)`.
    `_relink_d18_note`, when not `None`. With `rolling_back` set: `["back"]` only, note `relink: a
    back recovery began and stopped part-way; only back is offered - 'recover --resolve <op_id>
    --back --apply' goes on from what each row holds now`. `recover_op` refuses a direction the
    classification does not offer, quoting this note, before it dispatches, so a forward attempt
    meets this wording. Otherwise `["forward", "back"]`, note `relink: <w> row(s) written, <p>
    pending; forward finishes from the journal by what each key holds now, skipping and naming
    any row it cannot attribute; back takes out what landed, key by key`.
  - `_recover_relink(env, op, direction, stray) -> str` - shape error refuses;
    `_relink_refuse_open(env, "relink's recovery", mine=<op_id>)` (D18; without `mine` every
    relink recovery would wait on itself); then `roots = _retitle_roots(env)`, fresh, for every
    `_relink_read` and `_relink_put` in either direction (Global Constraints). Rows in `stray` are
    never read, in either direction. The per-row work runs inside `try ... except BaseException:
    save_manifest(op); raise`. Each row's outcome, for both directions, is the recovery matrix
    below; the bullets say how it is computed.
    - **forward** refuses outright while `rolling_back` is set, in the same words as the
      classification's note (`relink: a back recovery began and stopped part-way; only back is
      offered - 'recover --resolve <op_id> --back --apply' goes on from what each row holds now.
      Nothing was changed.`): a key that back already took out, and marked `restored`, would
      otherwise be written again where undo and the claim checks no longer look. Otherwise rows not
      `written` and not `skipped`, in journal order, each read once. A landed, marked row is not
      re-examined (undo names it later if it was repointed). D19 first:
      absent gives `skipped: "gone"`; unreadable `"unreadable"`; another conversation
      `"repointed"`, landed or not. Then `_relink_landed` marks it `written` (a key back at
      `"old"` beside one at `"new"` included: undo restores the one and counts the other).
      Otherwise bytes matching `pre_sha256` make it pending: `_relink_row_agrees(r, data)` false
      refuses the recovery as damaged (`the record for <op_id> does not match row <name>; refusing
      rather than write from it. Nothing was changed.`, a `LayoutError`); then
      `_relink_dependency_skip` (`"parent_changed"`, its detail); then a cycle check over the
      account's on-disk edges (`_lineage_store_rows(r["store_path"], light=True)` once per store,
      indexed, `_lineage_existing_edges`, plus the edges of rows decided earlier in this pass),
      for the keys the row writes only: a written link's edge to `parent_session`, a written
      chain's to `root_session`, never a kept link's; one that `_lineage_reaches` back to the
      child gives `"lineage_cycle"` (`it would close a cycle now`), and a store with unread rows
      gives `"edges_unreadable"` (`the account's links could not all be read, so a cycle cannot be
      ruled out`), a reason of its own, never `lineage_cycle`. Anything else gives
      `"changed_since_plan"` (`changed since the plan`). After deciding, `_guard_mutation` (the
      apply's words) only when a pending row will be written, each written by `_relink_put`
      (forward's `then`; a `False` marks, saves and raises the apply's verify `LayoutError`) and
      marked; then `completed`, `_op_link_report(op, m, skip_ids=..., forward=True)`, `rotate_ops`.
    - **back**, rows in reverse journal order, skipping `stray` (recorded with `_add_residue`, as
      realign's back records them): a `LayoutError` from containment re-raises for a written row
      and passes an unwritten one. Before its first write, `back` sets `m["rolling_back"] = True`
      and saves the manifest: the rollback intent is durable, so `classify_relink_op` offers only
      `back` from then on and forward refuses. A row has landed when it is `written`, or when a
      readable row has a key at `"new"` (`_relink_landed`), whatever conversation it now opens.
      Only landed rows are acted on or reported (the matrix says how each is reported). `back`'s
      report is durable, as undo's is: `m["back_left"]` (entries shaped as `undo_left`'s,
      deduplicated) and `m["back_restored"]` live in the manifest, and a resumed `back` keeps
      them. Per landed row, in this order:
      1. before any key is put back, and in one save: a landed row not yet marked `written` is
         marked `written` (so a crash mid-row can never leave it looking untouched), and the keys
         about to go back (its keys at `"new"` that `_relink_claims` does not claim) are added to
         the row's `back_keys` list;
      2. those keys go back in one `_relink_put` (back's `then`; `_guard_mutation` with the undo's
         words before the first write; `_maybe_crash("relink-back-mid-rows")` before every write
         after the first); keys at `"old"` count as back;
      3. a `False` from the put is `LayoutError("verify failed restoring row <name>: ... run
         'recover --resolve <op_id> --back --apply' again; it re-reads the row and goes by what
         each key holds.")`, raised after saving the manifest with the row left unmarked and
         `rolling_back` kept, so a second `back` retries the row;
      4. `_maybe_crash("relink-back-before-mark")` (for every landed row, put or not); then the
         row's left entries (a claimed key, `operation <op_id> wrote it since`; a changed key,
         `the app changed it since`; the row itself, by the matrix) are appended to
         `m["back_left"]`, `m["back_restored"]` is set to the number of rows whose `back_keys`
         is not empty (exact across passes: a row is counted once, and a row whose put landed in
         an earlier pass is counted though its keys are already at `"old"`), and the manifest is
         saved (the budget saver's `tick`), all before the row's mark;
      5. `restored = True` only when no relink-written key remains at `"new"` on the row (Global
         Constraints): a claimed key, a repointed row's key or an unreadable written row keeps the
         row unmarked, and its claim stands.
      A resumed `back` passes over rows already `restored`. At the end `abort_reason` is built
      from the manifest's fields, so it covers earlier passes too: `back restored
      <back_restored> row(s); left <k>: <row> <what> (<why>), ...` when `back_left` is not empty,
      else `back restored <back_restored> row(s)`. Never `rollback_residue` for an existing row.
      Status `rolled_back`; `rotate_ops`; returns `"rolled_back"`.

  **The recovery matrix.** One row of the journal, by its `written` flag and what a read finds:
  readable or not, the conversation it opens (same as `session_id` or other), its keys'
  states (`_relink_row_states`: all `"new"`, some `"new"`, none `"new"`), and for a pending row
  whether its bytes still match `pre_sha256` and its targets and the account's edges pass. A row
  already `restored` is passed over by both directions, and a `skipped` one by forward; `stray`
  rows are never read. In each direction the first line that applies decides; a `-` is a cell
  that direction never reaches:

  | written | read | conversation | keys at `new` | forward | back |
  |---|---|---|---|---|---|
  | yes | any | any | any | not re-examined (undo or back judges it) | - (decided by the `yes` lines at the end) |
  | no | absent | - | - | skip `gone` | pass over silently |
  | no | unreadable | - | - | skip `unreadable` | pass over silently |
  | no | readable | other | some or all | skip `repointed` | leave, mark `written`, name `the row` (`now opens another conversation; its link was left`), not restored |
  | no | readable | other | none | skip `repointed` | pass over silently |
  | no | readable | same | some or all | mark `written` | mark `written`, then as the written line below |
  | no | readable | same | none; bytes == `pre_sha256`; journal disagrees with the row | refuse, damaged record | pass over silently |
  | no | readable | same | none; bytes == `pre_sha256`; a target gone, unreadable or repointed | skip `parent_changed` | pass over silently |
  | no | readable | same | none; bytes == `pre_sha256`; the edge closes a cycle | skip `lineage_cycle` | pass over silently |
  | no | readable | same | none; bytes == `pre_sha256`; the account's links not all readable | skip `edges_unreadable` | pass over silently |
  | no | readable | same | none; bytes == `pre_sha256`; checks pass | write, mark `written` | pass over silently |
  | no | readable | same | none; bytes differ from `pre_sha256` | skip `changed_since_plan` | pass over silently (the residual of Departure 9) |
  | yes | absent | - | - | - | name `the row` (`gone from the store`), restored |
  | yes | unreadable | - | - | - | name `the row` (`could not be read`), not restored |
  | yes | readable | other | some or all | - | leave, name `the row` (`now opens another conversation; its link was left`), not restored |
  | yes | readable | other | none | - | name `the row` (`now opens another conversation`), restored |
  | yes | readable | same | some or all | - | put back the unclaimed keys at `new`; name each claimed key and each changed key; restored unless a claimed key stays at `new` |
  | yes | readable | same | none | - | nothing to write; name each changed key; restored |

  The rest of the Produces list:
  - `_relink_skip_line(r) -> str` - `skipped : <row> -> <label>: <skipped> - <skip_detail>`.
  - `classify_op`: the shape table gains `"relink": _relink_shape_error`. For a relink, D18 is
    tested before the unresolved-row gate: right after `stray` and `kind` are computed, `if kind ==
    "relink"` and `_relink_d18_note(env, m)` is not `None`, it is returned, so `classify_op` never
    advertises a `back` that `recover_op` would refuse. Then the gate runs as for every type, and
    `if m.get("op_type") == "relink": return classify_relink_op(env, op)` sits with the other
    dispatches.
  - `recover_op`: `"relink"` joins the allowlist that skips `_validate_manifest_paths`; `if
    m.get("op_type") == "relink": return _recover_relink(env, op, direction, stray)`.
  - `cmd_recover`: after a forward recovery of a relink, one `_relink_skip_line` per skipped row,
    through `redact` unless `--verbose`. `abort_reason` and the link report print as today.

- [ ] **Step 1: Write the failing tests:**

```python
def crashed(env, point, ...): ...   # crash at POINT on the first hit; release_lock; the op
def test_recovery_waits_while_another_operation_is_unfinished(...):
    # classify resolutions == [], note names the other op; recover_op both ways -> Refusal
def test_a_relink_alone_recovers_without_waiting_on_itself(...):
    # crash at "relink-mid-rows", no other op: classify offers forward and back; recover_op
    # forward -> "completed" (mine is passed)
def test_classify_puts_d18_before_the_unresolved_row_gate(...):
    # a relink with one row in a store this version does not read (strand(), as in
    # test_journal_reroot) and another op at "writing" -> resolutions == [] with the D18 note,
    # never ["back"]
def test_forward_marks_a_row_whose_keys_all_hold_new_as_landed(...):
    # crash at "relink-before-mark": rows[0] not written; forward -> "completed", both written,
    # local_c2's bytes unchanged by the forward
def test_forward_marks_landed_a_row_with_one_key_new_and_one_changed(...):
    # chain store; crash before mark; the app changes local_c2's startChain depth -> written
def test_forward_marks_landed_a_row_with_one_key_new_and_one_back_at_old(...):
    # chain store; crash before mark; the app removes local_c2's startChain -> written; a later
    # undo_relink takes spawnedFrom out and leaves no "left" entry for startChain
def test_forward_refuses_a_record_that_does_not_match_its_pending_row(...):
    # crash before row 2; op rows[1]["old"]["spawnedFrom"] edited to {"present": True, "value":
    # "x"} (or post_sha256 to another valid hex) -> LayoutError "does not match row
    # local_c3.json"; local_c3 unchanged; the op still non-terminal
def test_forward_skips_a_pending_chain_only_row_whose_kept_parent_row_is_gone(...):
    # chain store, local_c3 keeps side_link("local_p3"), so it plans the chain only; crash before
    # it; local_p3 deleted -> local_c3 skipped "parent_changed"; no startChain written
def test_forward_does_not_skip_a_chain_only_row_for_a_cycle_through_its_kept_link(...):
    # as above, but local_p3 gains side_link("local_c3") instead (C <-> P on disk): the chain to
    # LR closes nothing, so local_c3 gains its chain
def test_forward_writes_a_pending_row_and_skips_one_whose_parent_row_is_gone(...):
    # crash at the first "relink-mid-rows" (row 2); local_p3 deleted -> local_c3 skipped
    # "parent_changed", detail names LP[:8]; local_c2 written
def test_forward_names_a_landed_row_whose_parent_row_is_gone_since(capsys, ...):
    # crash after local_c2 landed; local_p2 deleted; forward -> link_report["dangling"] ==
    # [{"row": "local_c2", "target": "local_p2", "kind": "parent"}]; cmd_recover prints
    # "link left dangling: local_c2 (its parent's row was removed)"
def test_forward_skips_a_pending_edge_that_now_closes_a_cycle(...):
    # crash before row 2; local_p3 gains side_link("local_c3") -> local_c3 "lineage_cycle"
def test_forward_skips_a_row_whose_accounts_links_cannot_all_be_read(...):
    # crash before row 2; a local_zz.json in A3's store holds "{not json" -> local_c3 skipped
    # "edges_unreadable" (never "lineage_cycle"); local_c2 untouched by the forward
def test_forward_skips_a_row_changed_after_the_plan(...):      # pending local_c3 rewritten ->
                                                               # "changed_since_plan"
@pytest.mark.parametrize("landed", [True, False])
def test_forward_skips_a_repointed_row_landed_or_not(landed, ...):
    # landed=True: crash at "relink-before-mark" (landed but unmarked); landed=False: crash at
    # the first "relink-mid-rows" (row 2 pending). The row is then repointed -> skipped
    # "repointed", never marked written
def test_cmd_recover_prints_each_skipped_row(capsys, ...):
    # "skipped : local_c3 -> <A3 label>: parent_changed - <detail>"
def test_back_takes_out_what_landed_and_terminates(...):
    # crash before row 2; back -> "rolled_back"; local_c2 == before; restored True
def test_back_takes_out_a_landed_key_beside_one_the_app_reverted(...):
    # chain store; crash at "relink-before-mark"; the app removes local_c2's startChain; back ->
    # local_c2 has neither key and no left entry for it
def test_an_interrupted_back_offers_only_back_and_a_second_back_finishes(...):
    # crash at "relink-after-rows" (both rows written); back with a hook raising at the first
    # "relink-back-mid-rows" -> SimulatedCrash after one row is restored; release_lock. On disk:
    # rolling_back is True. classify_op -> resolutions == ["back"]; recover_op(op, "forward") ->
    # Refusal "began and stopped part-way". A second back -> "rolled_back"; no row of the
    # relink carries spawnedFrom; every landed row restored True; nothing left written outside
    # undo's reach
def test_back_names_a_landed_unmarked_row_repointed_since(...):
    # crash at "relink-before-mark" (local_c2 landed, unmarked); local_c2 then repointed to LQ
    # with its link kept -> back "rolled_back"; local_c2 untouched (its link still there);
    # abort_reason contains "local_c2 the row (now opens another conversation; its link was
    # left)"; on disk local_c2's journal row is written True and not restored, so its claim stands
def test_a_failed_verify_in_back_leaves_the_row_for_a_second_back(monkeypatch, ...):
    # crash at "relink-after-rows"; atomic_write patched so local_c3's restore writes other bytes
    # -> back raises LayoutError "verify failed restoring row local_c3.json" with "--back
    # --apply"; on disk rolling_back True, local_c3 not restored, local_c2 restored. Unpatched,
    # a second back re-reads local_c3, puts its key back -> "rolled_back", back_restored == 2
def test_back_marks_a_landed_row_written_before_putting_its_keys_back(...):
    # chain store; crash at "relink-before-mark" (local_c2 landed, unmarked); the app changes
    # local_c2's startChain depth; back with a hook raising at "relink-back-before-mark" (after the
    # put) -> on disk local_c2 written True, not restored. A second back: spawnedFrom at old counts
    # as back, startChain is named "the app changed it since"; abort_reason names it; local_c2
    # restored True
def test_a_resumed_back_reports_the_rows_its_first_pass_handled(...):
    # crash at "relink-after-rows"; the app changes local_c3's spawnedFrom taskId (back reaches
    # local_c3 first, in reverse order: nothing to put, one left entry, restored); back with a
    # hook raising at the second "relink-back-before-mark" (local_c2, after its put) -> on disk
    # back_left holds local_c3's entry and local_c2's back_keys == ["spawnedFrom"]. A second
    # back finds local_c2's key already at old -> abort_reason "back restored 1 row(s); left 1:
    # local_c3 spawnedFrom (the app changed it since)", back_restored == 1, built from the
    # manifest, not from the second pass alone
@pytest.mark.parametrize("change", ["repointed", "gone", "unreadable"])
def test_back_never_reports_an_untouched_pending_row(change, ...):
    # crash at the first "relink-mid-rows"; local_c3 (never written) is then CHANGE -> back
    # "rolled_back"; abort_reason does not name local_c3; local_c2 restored
def test_back_leaves_a_claimed_key_and_names_it_in_abort_reason(...):
    # crash at "relink-after-rows"; a later completed sync manifest wrote local_c2 -> back leaves
    # its key; abort_reason contains "local_c2 spawnedFrom (operation <sync id> wrote it
    # since)"; local_c2 is not marked restored, so the now rolled-back relink still claims it
    # (_relink_claims for a hand-made earlier relink naming local_c2 returns this op);
    # local_c3's link is taken out and local_c3 restored
def test_back_leaves_no_rollback_residue_for_an_existing_row(...)
@pytest.mark.parametrize("direction, line", [...])   # one entry per matrix line and direction
def test_recovery_matrix(direction, line, ...):
    # Each entry builds the row state the matrix line names (a crash point for written or not:
    # "relink-after-rows", "relink-before-mark" or the first "relink-mid-rows"; then the edit:
    # delete, "{not json", repoint to LQ, the app's change to one or both keys, a removed or
    # repointed target, a cycle-closing link, an unreadable local_zz.json) and asserts that
    # direction's cell exactly: the skip reason, written and restored as stored on disk, the
    # bytes of the row, and whether abort_reason names it. Every line of the table is an entry,
    # for each direction whose cell is not "-"
def test_every_route_passes_discovered_roots(monkeypatch, ...):
    # wrap ct._relink_read and ct._relink_put to record their roots argument; run the apply,
    # undo, a forward and a back (each from its own crash): every recorded roots equals
    # ct.discover_stores(env).roots, never a journaled store_path; and with discover_stores
    # patched to status "error", undo and recovery refuse before any read
@pytest.mark.parametrize("damage", [...])     # three of Task 5's damages
def test_a_damaged_record_refuses_through_classify_undo_and_recover(damage, ...):
    # classify resolutions == [] with "damaged"; recover_op -> Refusal; a completed copy's
    # undo_relink -> Refusal
```

  In `tests/test_journal_reroot.py`, built on a `relinked(mkenv, tmp_path, write_transcript,
  write_row, crash_at=None)` helper under `packaged()`:

```python
def test_relink_undo_takes_the_link_out_in_the_moved_store(...):
    # relocate; undo_relink(list_ops(env)[-1]) -> "undone"; the moved local_c2 has no spawnedFrom
@pytest.mark.parametrize("direction", ["forward", "back"])
def test_an_unfinished_relink_recovers_in_the_moved_store(direction, ...):
    # crash at the first "relink-mid-rows"; relocate; recover_op -> "completed" / "rolled_back";
    # the moved local_c3 has / local_c2 lacks its link
```

- [ ] **Steps 2 to 5** as in Task 2.

### Task 10: The README section and the internals note

**Files:**
- `README.md`: `relink --apply` joins the close-the-app list (about line 130); "Thirteen commands"
  becomes "Fourteen commands" (about 156); a **`relink`** section after `realign`'s, before "##
  Safety design" (about 742).
- `docs/internals.md`: a section `##### Repairing rows already in place: relink (design
  docs/specs/2026-11-19-relink-design.md)` after "This build's departures from the spec" and before
  "When the app orphans a conversation" (about 1227).

**What each says:**
- **README**, to `realign`'s standard and in the house style (no em dashes, fake cast):
  - what it does, and why (rows made before 0.18.0 sit outside their parent);
  - the two commands (`relink`, `relink --apply`), the options, and the dry run's sample from the
    spec in this plan's shape;
  - which rows gain a link, which are left out and why: the D12 cases, D11's rule and its limits
    as the spec states them, and the three new phrases;
  - that only the two keys change, the app's own keys and their order kept;
  - that the app's response to a link added to one of its own rows was measured only by the D2
    run sheet, as the existing lineage section says what was not measured;
  - that a dry run reads every account's store (about 400 MB on the store the design measured)
    and every transcript name, and an apply reads them again under its lock, so the app stays
    closed for longer than the writes alone;
  - that one row anywhere that cannot be read blocks the whole run, as it blocks `converge`: an
    unreadable row could be a holder or a target, and "couldn't look" is never "nothing there";
  - `--apply`'s conditions (the app closed, no unfinished operation), and that one unfinished
    operation of any kind holds every relink, its dry run included, until it is recovered (D18,
    and why);
  - the re-plan and its `since the plan` line, exit 0 with rows left out, and that a second run
    can add a chain the first run's links made resolvable;
  - `undo` per key, with its `left` lines, bounded by the journal's retention like every
    operation's (the ten most recent finished operations); `recover` in both directions;
  - the one-row proof, with `--only <row id>`;
  - that the window offers no undo while a relink is the latest operation.
- **internals.md**:
  - the planning steps and their order;
  - D11's predicate, separate from `_lineage_variant_ok`, and why, with its own code;
  - the kept-key judgement (a kept link's `taskId` included), and what the `lineage` record holds
    for a kept key;
  - the journal: no images, the hashes, the agreement check that stands in for images, the byte
    budget, the status fields;
  - D13's claims and the per-key exceptions, the moveish rule, the three ownership checks
    changed, and what `restored` means (no relink-written key left at `new`);
  - recovery by key: the recovery matrix, back's landed test, its durable intent
    (`rolling_back`) and report (`back_left`, `back_keys`, `back_restored`), and why not
    `rollback_residue`;
  - containment roots from discovery at every mutation route, and runnable recover hints;
  - D18 (any operation type, and an unreadable record on its own), its liveness cost, and D19;
  - the writer bound;
  - this plan's departures and residuals (below), as built.
- [ ] **Step 1:** Write the two changes.
- [ ] **Step 2:** Run both full suites once more.
- [ ] **Step 3: Commit** - `docs: relink - what it adds and leaves out, its journal, undo and
  recovery`.

## Departures from the spec

Recorded so the spec's authority can be checked against them. Departures 1, 2, 3, 5, 6, 7 and 8
are the seven "Corrections from the build plan's read of the code" the spec's Revisions record.
1. **The listing's first column is the row id** (`local_` and 8 characters, `_lineage_short`), not
   the sample's bare 8-character id. It pastes straight into `--only`, and it tells apart two rows
   of one conversation in one account, which the spec says are each repaired.
2. **An unreadable row anywhere refuses the plan.** The scan is converge's (`_retitle_scan`), which
   refuses on any row it cannot read, a superset of D16's candidate, holder and target list. Kept
   after review round 1, as `converge` keeps it; the README says so and why.
3. **A `LayoutError` exits 2**, as `main` gives every command. D9 says 1 "on a refusal or an
   error"; a refusal is 1.
4. **A chain-only D11 failure is `lineage_chain_dropped`**, detail `no app-written link backs the
   chain`. The spec names a code only for the link's D11 failure.
5. **D13's non-terminal branch is unreachable from `undo` and `back`**, because D18 refuses first
   while any other operation is open. `_relink_claims` keeps it and is tested directly.
6. **The repoint interop test is split.** `undo_repoint` never calls `_repoint_claimed_later` (it
   refuses on byte drift), so "a later relink blocks a repoint's undo and a restored one does not"
   is pinned through `undo_repoint` and the bytes (Task 8). The `_repoint_claimed_later` change is
   pinned on `recover --back` with an earlier relink (Task 7), the case it exists for; a later
   relink beside a non-terminal repoint cannot happen under D18.
7. **RULING 5 is checked in `_relink_recheck`, not by the guard.** The spec's "Applying" says
   `_guard_mutation` settles "the app fully closed and the account identity", but it checks only
   the platform and the running app; the identity check sits in the apply-time recheck, as in
   `_realign_recheck` and `_converge_recheck`.
8. **The status fields follow their siblings, not realign's rows alone.** `realign`'s rows carry
   only `written`; `restored` follows realign's copies, and `skipped` with `skip_detail` follows
   `converge`'s rows. The generic readers key on `written`, so the names work.
9. **Recovery goes by key** (spec, "Recovery", forward rules 2 and 4). Forward marks a row
   `written` whenever any written key holds its `new` value, whatever the others hold, including
   a key back at `old`; it skips such a row (`changed since the plan`) only when no key is at
   `new`. `undo` already restores keys at `new`, counts a key at `old` as restored and names the
   rest, so a partly reverted row loses nothing. `back` restores keys at `new` on rows marked
   `written` and on rows that pass the same test (marking them `written` first), and names a row
   as gone, unreadable or repointed only when it landed. Forward still skips, with its reason, a
   pending row it cannot write (gone, unreadable, repointed), as the spec's forward rules say.
   The task's recovery matrix (Task 9) gives every case. One case is narrower than "no
   relink-added key is ever out of undo's reach", and is recorded as a residual below: a row whose
   write landed, whose mark was lost, and every one of whose written keys the app has since
   changed.
10. **A kept link must carry a usable `taskId` equal to the agreed one, or it is
    `own_link_differs`.** D12 names `own_link_differs` only for a link naming no usable row. A kept
    link with no `taskId` is the shape Phase 0 measured as hiding the child, which `relink` may not
    replace (D5) but must surface rather than count as agreeing; a wrong but usable `taskId` stays
    the conversation's disagreement.
11. **A D11 failure has its own code, `lineage_no_app_link`** ("no app-written link backs it"),
    in `LINEAGE_LEFT_OUT`. D11 is consulted only when the resolver produced a link, that is when
    some holder yielding the parent is a cleared variant with a usable `taskId`, so the code means
    exactly "cleared, but none of them written by the app" (Task 2, step 4's table). When the
    resolver produces no link its own note stands: `lineage_variant` when no yielding holder is a
    cleared variant, `lineage_no_task` when none has a usable `taskId`. The spec's step 4 reports
    a D11 failure as `lineage_variant`, whose phrase "not a cleared link variant" would be false
    for holders that are cleared but tool-written. A chain-only D11 failure keeps
    `lineage_chain_dropped` (Departure 4).

Residuals, recorded rather than changed:
- `doctor`'s advice for `rollback_residue` (delete the session from the app if it is not wanted) is
  the shipped advice. A relink `back` writes residue only for rows in a store this version does not
  read (as realign's does), and for such a row, which relink only edited, that advice is wrong. It
  is left as it is for every operation, and named here.
- An operation whose `history` `list_ops` cannot sort fails `list_ops` in every command; the
  ownership checks' broad `except` then returns no claim, as it does today for every type.
- `list_ops` reads every manifest unguarded (`read_json`), so one unreadable `manifest.json`
  anywhere in the journal raises `LayoutError` out of `rotate_ops`, which every relink route
  calls after its writes. That is existing behaviour for every operation type. A relink's own
  work stands when it happens: its rows are written and its journal already saved with its final
  status before `rotate_ops` runs; only the rotation is lost, and `doctor` and the next run's
  D18 name the damaged record.
- A row whose write landed, whose `written` mark was lost, and every one of whose written keys the
  app has since changed is in neither undo's list (not `written`) nor `back`'s report (no key at
  `new`, so not provably landed); forward skips it as `changed_since_plan`, which names it once.
  Nothing is destroyed: the app's values stand, and relink has nothing of its own left on the row.
- Rotation (`_collides`) still treats an undone or rolled-back relink as having withdrawn its
  claim, so the claim of a row it left unrestored lasts while the journal keeps the operation,
  the retention bound every operation's undo already has.

Choices the spec leaves to the builder, made here:
- The function names, the three phrases and their table positions, and the `--json` names
  (`adds`, `keeps`, `account_filter`, `since_plan`, `undo_left`, `what`, `why`).
- Left-out rows carry `notes` (`{code, detail}`, the lineage notes' shape) in place of bare codes,
  so a detail has a place.
- A D12 disagreement is judged across every account whatever the narrowing, and leaves out every
  row of the conversation in scope, in every account, the linked rows included, each counted
  once; a selected row of such a conversation is always listed. A row left out by D12 or
  `own_link_differs` whose holders carry a chain also counts on the `chains :` line.
- A conversation `--only` refuses when more than one row would be written; candidates that would
  be left out do not count.
- `--account` matches every account directory, empty org folders included, and scopes the run to
  the matched folders; matches in two accounts refuse, while one account's empty org folders
  beside its populated store do not make it ambiguous.
- `cycles :` counts every account, whatever `--account` or `--only` narrows, as converge counts
  every destination.
- A kept chain beside holders whose own chains do not agree is `own_chain_differs`: it cannot be
  shown to agree.
- For a chain-only candidate the holders are the other accounts' rows, as the spec says; a
  resolver link left out for them drops the chain.
- A candidate whose holders yield no parent and that carries no note is in neither list; a
  left-out entry always has a note.
- The written row's tag shows the link reason when it has one, else the chain reason.
- The printed report reads its structure from the plan and only its displayed strings from the
  public manifest, and the projection puts the schema values back after anonymizing.
- The apply message asks for a look in the app rather than asserting the nesting.
- Forward recovery's link report also checks a kept link the record names; its dependency check
  re-validates a kept parent link a chain-only row stands behind; its cycle check uses only the
  keys the row writes; a store it cannot fully read skips with `edges_unreadable`.
- `back` marks `restored` on a landed row only when no relink-written key is left at `new`,
  records `rolling_back` before its first write so forward is never offered after it, keeps its
  report in the manifest (`back_left`, and `back_restored` counted from each row's `back_keys`,
  so a resumed pass neither loses nor double-counts a row), and uses `rollback_residue` only for
  rows in a store this version does not read, as realign's back does.
- The journal budget is `sync`'s 32 MB; a save is estimated once per pass. The journal-size test
  uses 60 rows with a per-row bound rather than the design's 931.

## Revisions

- **Review round 1 (2026-11-19; Codex, agy, DeepSeek and Kimi reported; DeepSeek and Kimi
  repo-aware).** Applied:
  - The write loop marks `written` and saves before raising a verify `LayoutError`, as realign's
    and retitle's do; `test_the_verify_is_by_value` now matches (Codex, agy, DeepSeek).
  - `_relink_put` takes its caller's next step (`then`), so the apply names `recover --resolve
    <op_id> --back` and undo names `undo --apply` (agy).
  - `undo_left` is durable: a row's entries are appended before its mark, deduplicated on resume,
    and `undo_relink` and `_recover_relink` save on the way out of any exception (Codex, agy,
    Kimi).
  - Recovery goes by key: forward marks a row written when any key holds `new`; back acts on rows
    written or so landed, and names gone, unreadable or repointed rows only when they landed; the
    repointed test's landed case means landed but unmarked (Codex, agy; Departure 9).
  - `move` and `undo` claim every row they name, flag or not (the moveish rule), tested from a real
    `run_move` and `run_undo` (Kimi).
  - Undo reads every row before its first write and refuses, writing nothing, on an unreadable one
    (Kimi).
  - Recovery and the apply check that the journal agrees with the row (`old` against the parsed
    keys, `post_sha256` against the found value with `new` set) before writing from it; the shape
    check keeps every `old` value absent or a non-object (Kimi).
  - The shape check: `parent_session` and `root_session` bound to their rows, `store_path` the
    directory of `dest_path` with `account` its account folder, note codes from the known set; a
    non-string title is journaled as `""` (Kimi, DeepSeek).
  - D12 is judged across every account whatever the narrowing, and a selected row of a disagreeing
    conversation is listed (Kimi).
  - `--account` matches every account directory, empty org folders included (Kimi).
  - The README says undo is bounded by the journal's retention (Kimi), that a dry run reads every
    store (Kimi; the release times one), and that one unreadable row blocks the run, and why
    (Kimi; Departure 2 kept).
  - The `lineage_cycle` reachability case has an explicit app-written backing link, and every case
    asserts its own row's transition (Kimi).
  - Output: a plan with only left-out rows prints its report and `nothing to add` once, with no
    apply hint, in text and JSON (Kimi, agy).
  - `_relink_open_others` names an unreadable record or journal as its own line, never as an op id,
    and takes `mine`, which every relink route passes (Kimi, DeepSeek).
  - The three ownership checks treat a malformed relink as a claim inside their relink branch,
    never their broad `except`; realign's branch claims nothing by design, and its test pins that a
    malformed relink hides no later realign claim (Kimi).
  - The 931-row journal test is 60 rows with a per-row bound, and says why (Kimi, DeepSeek).
  - Review Focus's second-run claim is narrowed, with the two-account fixture (Codex).
  - Forward recovery re-validates the kept parent link a pending chain-only row relies on (Codex),
    and its cycle check uses only the keys the row writes (agy).
  - A test that undoing a `converge` whose created parent row a relink linked to reports the link
    dangling (Codex).
  - The release depends on the D2 run sheet's observed results (Codex; Release).
  - The kept-chain record: the shape check accepts `""` or the kept chain's id (agy).
  - A D12 short-circuit and `own_link_differs` add `lineage_chain_dropped` when a holder carries a
    chain (agy).
  - `classify_op` tests D18 before its unresolved-row gate for a relink; forward never reads a
    stray row (agy).
  - A kept link without the agreed usable `taskId` is `own_link_differs` (agy; Departure 10).
  - The report reads its structure from the plan, and the projection restores schema values after
    anonymizing (agy).
  - A link candidate whose holders name no parent and carry no note is in neither list, pinned by
    a test (DeepSeek).
  - Recovery reads stores with `light=True` (DeepSeek).
  - A D11 failure is `lineage_no_app_link` (DeepSeek; Departure 11).
  - The apply message asks for a look rather than asserting the nesting, and the README says the
    app's response was measured only by the D2 run sheet (DeepSeek).
  - `_relink_chain_agrees` uses `type(depth) is int` (DeepSeek).
  - D18's liveness cost is said in the refusal, the README and the internals note (DeepSeek).
  - Departures carry the spec's seven corrections explicitly, and the residuals are recorded
    (DeepSeek).
  - Also fixed while applying: `test_a_chain_only_repair_counts_the_rows_own_app_written_link` had
    `local_c2` left out, but the kept app-written link on `local_c3` backs its link; it now gains
    its link and chain.
- **Rejected in round 1:**
  - "`since_plan`'s `changed` should count rows whose bytes changed" (Kimi). It counts link
    changes, as `converge`'s `since the plan` does; a row whose other keys changed is simply
    planned afresh under the lock.
  - "`cmd_recover` should exit non-zero when `back` leaves keys" (Kimi). Every operation's recovery
    exits 0 on a terminal result today, and `abort_reason` says what was left; changing it for
    relink alone would make it the odd one out.
  - "A chain-only row whose holders' link fails resolution should still gain its chain" (agy). D6
    and D7 tie the chain to the resolver's link, and the Choices already record it.
- **Review round 2 (2026-11-19; agy and Kimi raised nothing new; Codex two majors; DeepSeek,
  repo-aware, one blocker, two majors and smaller points).** Applied:
  - `back` records its rollback intent (`rolling_back`) and saves it before its first write; while
    it stands, only `back` is offered and forward refuses, so a key back took out is never written
    again outside undo's reach. Tested by an interrupted back, a refused forward and a finishing
    second back (Codex).
  - A landed row that `back` finds repointed, marked or not, is left (D19) and named "now opens
    another conversation; its link was left", never passed over silently; landed now means any key
    at `new`, whatever conversation the row opens (Codex).
  - One decision table in Task 2's step 4, applied in one function, `_relink_row_decision`: D12
    first, then the resolver's own notes whenever it produced no link, then `lineage_no_app_link`
    only when it produced one that no app-written yielding holder backs. Departure 11 reworded to
    match (DeepSeek). The empty-title test was not changed as the disposition proposed: its
    fixture keeps a cleared tool copy in A3, which the resolver takes as its template, so
    `lineage_no_app_link` is what the table gives there; it now also runs with that copy moved to
    the candidate's own account, where the empty-title link is the only holder and the
    resolver's `lineage_variant` stands. Both cases pin the table.
  - A kept key's `lineage` fields are rebuilt from the kept object, with a fixture where A3 holds
    two rows of the parent and the pick rule chooses the other, asserting `parent_row` is the kept
    target and the shape check passes (DeepSeek).
  - `_relink_d18_note` states its `mine` self-exclusion (DeepSeek).
  - `_relink_put` runs `_retitle_contained` itself before writing (DeepSeek).
  - A store forward recovery cannot fully read skips with its own reason, `edges_unreadable`, not
    `lineage_cycle` (DeepSeek).
  - Task 4 says what `anonymize_report` does (titles only in non-structural keys and in
    `_ANON_FIELDS` and `_ANON_EMAIL_FIELDS` values; other strings only home-folded and
    uuid-shortened), so the explicit `detail`/`skip_detail` scrub is named as load-bearing and the
    schema-value restore as a no-op kept for safety; `_relink_summary` takes the plan, as both its
    callers pass (DeepSeek).
  - `live_asserted` is stated: the asserted account's uuid or `""`, as converge and realign store
    it, passed back as `--live` by the recheck (DeepSeek).
  - Task 2's `plan_rows` helper notes `_retitle_scan`'s signature: every argument after `env`
    defaults (DeepSeek).
  - `_relink_refuse_open` names every unreadable record in one line (DeepSeek).
  No rejections this round.
- **Review round 3 (2026-11-19), the last, not re-reviewed** (Codex "Blockers: none", two
  majors; agy two majors; Kimi one blocker, three majors; DeepSeek, no blockers, three findings).
  Applied:
  - `back`'s durable state (Codex, agy): a failed verify in `back` leaves `restored` unset, keeps
    `rolling_back`, saves and raises, and a test shows a second `back` retries the row; a landed
    but unmarked row is marked `written`, and saved, before any of its keys is put back; `back`
    keeps its report in the manifest as it goes (`back_left`, and `back_restored`), and
    `abort_reason` is built from them, so a resumed `back` reports the earlier rows too.
    `back_restored` is counted from a per-row `back_keys`, saved before the row's write, rather
    than incremented, so that a resumed pass neither double-counts a row nor misses one whose put
    landed just before a crash.
  - `restored` means no relink-written key remains at `new` on the row (Codex, agy); a row with a
    key left at `new` (claimed, repointed under D19, unreadable) stays unmarked in `back` and in
    undo, and its claim stands: `_relink_claims`, `_retitle_claimed_elsewhere` and
    `_repoint_claimed_later` now judge a relink by its rows' `restored`, whatever its status.
    Tests for both.
  - `classify_relink_op`'s `rolling_back` note and forward's refusal share the wording the test
    asserts, "began and stopped part-way", since `recover_op` refuses from the note before it
    dispatches (agy).
  - A row that keeps its own `startChain` object never carries `lineage_chain_dropped` on a
    left-out path: stripped from the resolver's notes when the link is left out, and not added on
    a `lineage_no_app_link` failure; the `+ chain` rule is gated on `chain_cand`, not
    `holder_chain` (agy).
  - Containment roots come from discovery, fresh, at every mutation route (apply, undo, forward,
    back), never from the journal; tests for a row outside every discovered root and for the roots
    each route passes (Kimi).
  - `run_relink` turns an `OSError` from `new_op` or from the `writing` status save into a
    `Refusal` before any row is written, and the saver's own save failures into a `Refusal`
    (Kimi).
  - An unreadable manifest, or a journal folder that cannot be listed, refuses `plan_relink` and
    every relink route on its own, with its own line, whether or not another operation is open
    (Kimi).
  - The recovery matrix in Task 9, with a parametrized test over its lines, for forward and back
    (Kimi).
  - Every printed recover hint carries `--apply`, and the tests assert it (DeepSeek).
  - Departure 9 narrowed, and its uncovered case recorded as a residual: a row whose write landed,
    whose mark was lost, and whose written keys the app has all changed (DeepSeek).
  - D18's refusal explains itself for every operation type (DeepSeek).
  - Residual: `list_ops` reads every manifest unguarded, so one unreadable manifest raises out of
    `rotate_ops` after a relink's writes; existing behaviour, and the relink's work and journal
    stand (DeepSeek).
  - `_realign_recheck` is named where `_relink_recheck` reuses its RULING 5 wording: in Task 6's
    Consumes, where `_relink_recheck` is defined, rather than Task 3's, which the disposition named
    (DeepSeek).
  - `--account`'s no-match refusal lists each account directory with its email where known
    (DeepSeek).
  - Found in the consistency pass after applying: `_relink_put`'s description said the caller
    "marks, saves and raises" on a failed verify, which held only for the apply; it now says each
    route decides, and the routes say how. The `--account` help text said "exactly one store",
    which round 1's rule had made "exactly one account". `undo_relink` and `_recover_relink` now
    say where their roots come from; `execute_relink_op` did not either. Undo's preflight used
    `_relink_row_states`, which Task 9 defines; it now uses Task 1's `_relink_key_state`. D18's
    self-exclusion said every route passes its own op id as `mine`; only undo and recovery have
    one. A resumed-`back` test crashed at a point that runs before the first write; it now
    crashes at `relink-back-before-mark`. Two Task 7 tests that asserted the old claim rule are
    updated, and one new test covers the new rule.
  No rejections this round.

## Release

Before the release, and a prerequisite to it: the D2 run sheet's observed results, recorded with
the app's version. For the one row shaped like the app's own and each of the four risky kinds:
the child seen under its parent, still there after a quit and reopen, and the row read back with
its link and chain as written and only the app's ordinary keys added. For the first of them, an
`undo --apply` that took its keys out with nothing left, then the apply again. Also a dry run
timed on the live store, so the run sheet can say how long the app stays closed.

Not part of this plan: the version bump, the roadmap entry, and the D2 run sheet (one row shaped
like the app's own, then one each of the four risky kinds, then the rest), which the maintainer
runs with the app closed.
