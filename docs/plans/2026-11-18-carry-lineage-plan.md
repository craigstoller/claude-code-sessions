# Carry the parent-child link across accounts: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When `converge` creates a row for a side session, or `sync` writes one, the row carries
a link to its parent's row in that account (`spawnedFrom = {sessionId, taskId}`) and, where the
source has one, a translated `startChain`. So the side session shows under its parent in every
account. It must not carry a link that, when the op decides, names a missing row or another
conversation.

**Architecture:** One pure resolver, the lineage pass, takes an index of plain data and returns,
for each row an op will write, the link and chain to write and a journaled `lineage` record.
- `converge` and `sync` call it at plan time, and once more under the lock just before `new_op`.
  Each then rewrites the row's `post_b64`.
- The journal holds the final bytes, so recovery never re-resolves.
- Rows are written parents first.
- Recovery, `undo` and `recover --back` check the links the op wrote, and report any left
  dangling or redirected.

**Tech Stack:** Python 3.9+, standard library only. One module, `claude_code_sessions.py`, and
the GUI module `claude_code_sessions_gui.py`. Tests run under pytest (`tests/`), with standalone
checks in `tools/check_*.py`.

**Spec:** `docs/specs/2026-11-17-carry-lineage-design.md` (design; the maintainer's rulings L1
to L7 are in its "Decisions" section). Read its "Design" section before any task. Where this plan
and the spec disagree, the spec wins, and the disagreement is a defect to report. The departures
this plan makes on purpose are listed in "Departures from the spec" at the end.

The spec's "A defect in the shipped tool" (a landed `converge` row lost to `undo`) is already
fixed on main, at cf0e16e. `_converge_mark_landed` exists, and its tests are in
`tests/test_converge.py`. Those tests must keep passing; no task re-implements the fix.

## Global Constraints

- **One module.** All production code goes in `claude_code_sessions.py`, apart from GUI lines in
  `claude_code_sessions_gui.py`. Do not split either.
- **Anchors.** Line numbers here ("about line N") were taken at main cf0e16e and drift as tasks
  land. Each anchor also names its symbol, and the symbol wins.
- **Line endings.** Every tracked file is LF in git and CRLF in this working copy
  (`core.autocrlf=true`; `git ls-files --eol` shows `i/lf w/crlf`), `.py` and `.md` alike. Keep
  each file's working-copy endings, never normalise one, and compare files by committed blob.
- **Pinned output.** The `tools/check_*.py` scripts pin report text (for example
  `check_sync_update.py`, `check_report_fixes.py` and `check_gui_undo.py`). A task whose change
  alters output a check pins updates that check in the same task, and names it in its report.
- **Fake cast only** in tests, docs and messages: ACME-REVIEW, Northwind, alice@ and
  bob@example.com, the `u` user path. No real ids, titles or paths anywhere.
- **Refusals are `Refusal`**, layout bugs are `LayoutError`. Every new function that a
  classifier, `doctor` or a report calls never raises.
- **Writers.** Like every mutating command here, the guarantee holds while the desktop app is
  closed (the RULING 4 guard, which `converge` and `sync` already run) and this tool's lock is
  held. A store changed by anything else between the apply-time pass and the write is outside
  it. Recovery's dependency checks and the link report are how such a change is caught.
- **Identity.**
  - A row is identified by `(account, name)`. `name` is its filename; its row id is the filename
    stem, which must equal its `sessionId`.
  - The link graph's nodes are **conversations** (`cliSessionId`), one graph per destination
    account. Cycles are found per account over conversations, while write order is over the op's
    rows.
  - Every result, edge and note is keyed by the row, never by conversation alone, since
    `converge` plans one conversation into several accounts.
- **The carried link** is `{"sessionId": T, "taskId": <taskId>}`.
  - `T` is the target row's id. `taskId` is copied unchanged from the template holder.
  - There is never a link without `taskId`.
  - Every other key of the template's link is dropped and recorded, `title`, `sideSession`,
    `name`, `sharedFolder`, `wakeParent`, `pendingPop`, `sharedCheckout`, `userFork` and unknown
    keys alike (L1).
  - One exception, for `sync` refreshes: when the destination's own row already carries a link
    whose `sessionId` is `T` and whose `taskId` is equal, that link is kept as it is (see
    "Departures"). `carried_keys` and `dropped_keys` then describe the link written: the kept
    link's keys in their order, and `[]`.
- **The carried chain** is `{"firstSessionId": R_row, "depth": d}`, with `d` an integer, not a
  boolean, and at least 1 (spec, step 6). Other keys of a source chain are dropped, and recorded
  in `dropped_keys` as `"startChain.<key>"`.
- **Link variants.** Two predicates, used separately:
  - `_lineage_variant_ok(link)`: the shape is cleared. That is:
    1. `sideSession: true`, with any other keys;
    2. exactly the keys `sessionId`, `taskId` and `title`, with `title` a non-empty string (L7);
    3. exactly the keys `sessionId` and `taskId` (this tool's own output).
  - `_lineage_task_ok(link)`: `taskId` is a non-empty string.
- **Row bytes.** Compact JSON, as today: `json.dumps(data, separators=(",", ":")).encode()`.
  - A key that is present has its value replaced **in place**, so the key keeps its position.
  - A key that is absent is appended at the end. A key being removed is popped.
  - Nothing else in the row changes.
  - `_lineage_rewrite_post(post_b64, link, chain) -> str` takes and returns base64.
- **Journal fields, verbatim:**
  - manifest: `lineage_format: 1` on every `converge` and `sync` op this version journals;
  - per row: `lineage`; and `lineage_planned`, a dict of the same shape, holding the plan's
    `lineage` when the apply-time pass changed it;
  - inside `lineage`: `parent_session`, `parent_row`, `parent_planned`, `root_session`,
    `root_row`, `root_planned`, `carried_keys`, `dropped_keys` and `notes`. Strings are `""` and
    lists are `[]` where absent; the `*_planned` fields are booleans;
  - `notes`: a list of `{"code": str, "detail": str}`, with no code repeated in one row;
  - `detail`: a short sentence naming ids by their first 8 characters only. Never a title, label
    or path.
- **Lineage must agree with the bytes.** In a row of an op with `lineage_format: 1`:
  - `lineage["parent_row"]` is non-empty exactly when the decoded `post_b64` carries
    `spawnedFrom`, and then equals `spawnedFrom["sessionId"]`;
  - `root_row` and `startChain["firstSessionId"]` are bound the same way;
  - `parent_planned: true` means some other row of the op has the name `parent_row + ".json"` in
    the same store, and `root_planned` the same for the root.

  `sync --verbatim` rows carry no `lineage`, and are exempt. These checks bind `lineage` only.
  `lineage_planned` is the plan's record, kept as history; after a retarget, gain or drop it
  disagrees with the final bytes by design, so it is checked for shape alone.
- **Note codes, verbatim**, with the phrase the reports use:

  | code | phrase | counts as |
  |---|---|---|
  | `lineage_parent_disagree` | holders disagree on the parent | link left out |
  | `lineage_self` | the link names the conversation itself | link left out |
  | `lineage_parent_dead` | the parent's transcript is gone | link left out |
  | `parent_not_in_account` | no row for the parent in that account | link left out |
  | `parent_pair_held` | the parent's own row is held in this plan | link left out |
  | `parent_row_drifted` | that account's row for the parent opens another conversation | link left out |
  | `lineage_variant` | not a cleared link variant | link left out |
  | `lineage_no_task` | no usable taskId | link left out |
  | `lineage_task_disagree` | holders disagree on taskId | link left out |
  | `lineage_cycle` | it would close a cycle | link left out |
  | `lineage_source_changed` | the source row changed since the plan | link left out |
  | `lineage_chain_dropped` | the chain was left out | chain left out |
  | `existing_cycle` | the account already holds a cycle of links | report only, never a row note |
  | `lineage_retargeted` | changed since the plan | apply change |
  | `lineage_dropped` | dropped since the plan | apply change |
  | `lineage_gained` | gained since the plan | apply change |

  The codes that the spec does not name (the first three, and `lineage_source_changed`) and the
  phrase column are this plan's;
  the spec leaves wording to the builder. A dropped link also omits the chain. That adds
  `lineage_chain_dropped` only when a source chain existed.
- **Recovery skip reasons, verbatim:** `parent_not_written`, `parent_changed`.
- **Report lines, verbatim shapes.** Each part is printed only when its count is nonzero, and a
  line with nothing to say is not printed.
  - plan: `links   : N to carry, M left out (<phrase> K, <phrase> K)`, the most frequent reason
    first, ties in the table's order;
  - plan, when any source carried a chain: `chains  : N to carry, M left out`;
  - plan, when any destination already holds a cycle: `cycles  : N already present, left as they
    are`, from the manifest's `lineage_cycles`;
  - plan, `sync` refreshes: `link    : <row> loses its link` or `... now links to another row`,
    and `chain   : <row> loses its chain` or `... now has another chain root`, one line per
    `link_change` or `chain_change`;
  - after apply: `links   : N carried, M left out (<phrase> K, ...)`, then on the same line `;
    since the plan: R changed, D dropped, G gained`. The left-out phrases are the underlying
    reasons, never `lineage_dropped`;
  - after apply, when any source carried a chain: `chains  : N carried, M left out`;
  - `sync --verbatim`: `links   : N copied verbatim (not translated)`;
  - undo and recover:
    - `link left dangling: <row> (its parent's row was removed)`;
    - `link redirected: <row> (its parent's row now opens another conversation)`;
    - `links not checked: N could not be read`;
    - `links not checked: the link report failed (<its one sentence>)`, when the report itself
      failed;
    - for a root, "its chain root's row" replaces "its parent's row".
  - A row with a link left out gets `[link: <phrase>]` appended to its own line, as `[skipped:
    ...]` is today.
- **`--verbatim` help text, verbatim:** "copy rows unchanged instead of stripping connector
  config. Links to a parent or chain root (spawnedFrom, startChain) are copied as is, not
  translated, so they may name a row that does not exist in the destination."
- **Old journals.** An op with no `lineage_format` runs exactly as before: no shape check of
  lineage, and no dependency checks. Its undo or `recover --back` still gets the dangling part of
  the link report. In undo and `recover --back` that part reads only the store and the rows the
  command removed, never the journal's lineage. The redirected part, and forward recovery's
  check of the op's own targets, need a `lineage`, so neither fires for an old journal. This
  settles the round-1 contradiction.
- **`new-row` is unchanged** and still writes no `spawnedFrom` (`tools/check_new_row.py` keeps
  passing). Only its `undo` gains the dangling report.
- **Defaults are the safe ones.** `_converge_recheck`'s `relink` defaults to `False`, and only
  `run_converge` passes `True`. Recovery never re-resolves.
- **Run both suites as separate steps, and check each exit code:**

  ```bash
  python -m pytest -q -p no:cacheprovider
  ```

  ```bash
  fail=0; for t in tools/check_*.py tools/native-host-measurement/check_watch_ceremony.py; do python "$t" >/dev/null 2>&1 || { echo "FAIL $t"; fail=1; }; done; [ "$fail" = 0 ] && echo "checks: all passed"
  ```

  The second command exits non-zero when any check fails. At the base (main cf0e16e), pytest
  reports 1002 passed and every check passes. Never pipe a suite through `tail` in a chain that
  hides its exit code.
- **Commits:** the tests first, as `test(<scope>): N tests, failing until ...`, then
  `feat(<scope>): ...` or `fix(<scope>): ...`, then `docs: ...`. No attribution trailers. No push.
  The version bump belongs to the release, which is the maintainer's call.

## Review Focus

- **A holder row whose link is malformed:** `spawnedFrom` is not a dict, its `sessionId` is not a
  string, it names no row, or it names a row whose `sessionId` is not its stem. The pass yields
  nothing for that holder and never raises. Pinned in Task 1.
- **An account whose existing rows already link in a cycle.** It is reported once, and a proposed
  edge that would join it is dropped. The write order still terminates. Pinned in Task 2.
- **`--json` and `--anonymize` on an op that carries links.** No title, label, path or source
  connector state leaks out, and every emitted key is in `_ANON_STRUCTURAL_KEYS`. This includes
  a conversation whose title is literally `lineage`. Pinned in Task 3 (converge) and Task 7
  (sync).
- **A journal from 0.17.0 opened by this version**, pending or completed, converge or sync,
  including a verbatim sync link. Forward, back, undo, `recover`'s listing and `doctor` behave as
  before. Pinned in Task 5.
- **A large or deep store.** The pass indexes once per destination account and walks the graph
  iteratively, never recursively. Pinned in Task 3 on a synthetic 2,000-row store with:
  - a 200-deep chain of side sessions;
  - one root with 500 children;
  - an existing cycle.

  The test asserts correct results and no `RecursionError`, not a time: a timing assertion
  fails on a slow or busy machine and pins no behaviour. The implementer measures the pass's
  time on that fixture and records it in the task report, against a target of one second.

---

### Task 1: The lineage resolver

**Files:**
- Modify: `claude_code_sessions.py` — a new section "lineage" just before "9. converge" (about
  line 14653).
- Test: `tests/test_lineage.py` (create).

**Interfaces:**
- Consumes: `_activity_of`.
- Produces:
  - `LINEAGE_FORMAT = 1`.
  - `LINEAGE_PHRASES`, the code-to-phrase table above.
  - `LINEAGE_LEFT_OUT`, the set of codes that count as "link left out".
  - `@dataclass(frozen=True) class LineageRow: account: str; name: str; data: dict; planned:
    bool = False`.
  - `_lineage_variant_ok(link) -> bool` and `_lineage_task_ok(link) -> bool`, as in Global
    Constraints.
  - `class LineageIndex`, built by `_lineage_index(rows) -> LineageIndex` over one store's
    `LineageRow`s. It has three lookups:
    - `by_id: dict[str, LineageRow]`, the stem-checked rows by row id;
    - `by_name: dict[str, LineageRow]`;
    - `by_cli: dict[str, list[LineageRow]]`, the stem-checked rows by `cliSessionId`.
  - `_lineage_pick(rows, prefer_name="") -> LineageRow | None` — the spec's step 4 order:
    1. `prefer_name` (sync only);
    2. `isArchived` not `True`;
    3. existing before planned;
    4. the greatest `_activity_of(data) or 0`;
    5. the greatest `name`;
    6. the greatest `account`.
  - `_lineage_resolve(child_sid, child_name, holders, holder_index, dest, *, held,
    transcript_exists, prefer_parent_name="", prefer_root_name="") -> dict`. Its arguments:
    - `holders`: C's rows outside B (for sync, the one source row);
    - `holder_index`: `account -> LineageIndex` of each holder's own store;
    - `dest`: B's after-state `LineageIndex`;
    - `held`: `{conversation_id: reason}` for B, the plan's holds and any apply-time skips
      together;
    - `transcript_exists`: `sid -> bool`. Callers holding a set pass its `__contains__`.

    It returns `{"link", "chain", "lineage", "edges"}`. `edges` is `{"parent": P | None, "root":
    R | None}`, conversation ids.

**What `_lineage_resolve` does** — the spec's steps 1 to 6. It never raises.
1. **Source links.** A holder yields P when all of these hold:
   - its `spawnedFrom` is a dict, with a string `sessionId`;
   - `holder_index[holder.account].by_id` has that id;
   - that row's `cliSessionId` is a non-empty string.

   A holder that fails any of them yields nothing and is not a disagreement.
2. **Agreement.** These give no link, with the note in brackets:
   - holders yield two or more distinct P, of any variant (`lineage_parent_disagree`);
   - P is C (`lineage_self`);
   - P has no transcript (`lineage_parent_dead`).

   With no holder yielding a P, there is no link and no note.
3. **Template.** The eligible holders yielded P and pass both `_lineage_variant_ok` and
   `_lineage_task_ok`.
   - None eligible: `lineage_variant` if no holder that yielded P passes `_lineage_variant_ok`,
     else `lineage_no_task`.
   - Every holder that yielded P and passes `_lineage_task_ok` must give the same `taskId`; else
     `lineage_task_disagree`. A holder failing `_lineage_task_ok` is not counted.
   - The template is `_lineage_pick(eligible)`.
4. **Target.** `_lineage_pick(dest.by_cli.get(P, []), prefer_parent_name)`. With none, the
   first that applies of:
   - P is in `held`: `parent_pair_held`, with the held reason in `detail`;
   - `dest.by_name` has the template's parent filename, but it opens another conversation:
     `parent_row_drifted`;
   - otherwise: `parent_not_in_account`.
5. **Link:** `{"sessionId": T, "taskId": template_link["taskId"]}`. Then `carried_keys =
   ["sessionId", "taskId"]`, and `dropped_keys` is the template link's other keys, sorted.
6. **Chain.**
   - The sources are the holders whose `startChain` is a dict, as the spec says. Unlike a link
     (step 1), a source whose `firstSessionId` does not resolve (as in step 1) to a conversation
     R is a failure, not ignored.
   - No source: no chain, and no note.
   - Every source must resolve, and all must agree on R and on `depth`. R must not be C, and must
     have a transcript. `depth` must be an integer that is not a bool, and at least 1.
   - The root's row is found as in step 4, preferring `prefer_root_name`. It must not be the row
     named `child_name`.
   - Any failure gives `lineage_chain_dropped`. A dropped link also drops the chain.

- [ ] **Step 1: Write the failing tests** in `tests/test_lineage.py`, building `LineageRow`s
  directly:

```python
def test_side_session_link_translates_to_the_parent_row_in_b(): ...
    # link == {"sessionId": "local_pb", "taskId": "task_7f"}; "title" and "sideSession" in dropped_keys
def test_older_three_key_link_is_cleared(): ...
def test_tools_own_two_key_link_is_cleared(): ...
def test_variant_and_task_predicates_are_separate(): ...
    # pendingPop link -> lineage_variant; side-session link without taskId -> lineage_no_task
def test_empty_or_non_string_task_id_counts_as_absent_not_disagreeing(): ...
def test_holders_disagree_on_parent_any_variant_counts(): ...                 # lineage_parent_disagree
def test_holders_disagree_on_task_id(): ...                                    # lineage_task_disagree
def test_self_link_and_dead_parent(): ...
def test_malformed_holder_links_yield_nothing_and_never_raise(): ...
    # a non-dict, an int id, an unknown id, a stem mismatch
def test_parent_not_in_account_held_and_drifted_are_told_apart(): ...
def test_parent_pick_order(): ...
    # prefer_name, then unarchived, then existing, then activity, then name, then account
def test_newest_holder_without_task_id_falls_back_to_an_older_cleared_one(): ...
def test_chain_translated_exactly_two_keys_extra_keys_recorded(): ...
    # {"firstSessionId": "local_rb", "depth": 2}; "startChain.extra" in dropped_keys
def test_chain_dropped_on_bool_depth_dead_root_disagreement_or_self_root(): ...
def test_an_unresolvable_chain_source_drops_the_chain_with_its_note(): ...    # the spec's rule; a lone sync source too
def test_no_startchain_anywhere_means_no_chain_and_no_note(): ...
def test_lineage_record_has_every_field_with_its_type_and_no_repeated_code(): ...
def test_note_details_name_ids_by_8_chars_and_no_titles(): ...
```

- [ ] **Step 2:** Run `python -m pytest -q -p no:cacheprovider tests/test_lineage.py`. Expected:
  every test fails, because the names do not exist yet.
- [ ] **Step 3: Implement** the interfaces above.
- [ ] **Step 4:** Run the file again, then both full suites. Expected: all pass.
- [ ] **Step 5: Commit** the tests first, then the code.

### Task 2: Cycles, write order, and the row rewrite

**Files:** Modify `claude_code_sessions.py` (the lineage section). Test: `tests/test_lineage.py`.

**Interfaces:**
- Consumes: Task 1.
- Produces:
  - `_lineage_existing_edges(dest) -> dict[str, set[str]]` — B's current conversation graph from
    existing rows only (not planned ones): `{conversation: {target conversations}}`.
    - A planned row's `data` gives its identity alone (`cliSessionId`, `isArchived`, activity).
      Its links are never read: for `sync` they are the source's, untranslated, and name rows in
      the source account. A refreshed row is planned, so its old links drop out too.
    - Each existing row adds an edge for its link and one for its chain, when the target row is in
      `dest.by_id`.
    - A conversation with several rows gets the union of their edges, never one row's alone.
  - `_lineage_drop_cycles(results, existing) -> int` — `results` maps a row key `(account,
    name)` to its `_lineage_resolve` result, for one destination account.
    - It adds every proposed parent edge first, then every proposed root edge. Each set goes in
      order of the child row's conversation id, then its name.
    - A proposed edge that would close a cycle with the edges added so far (existing included) is
      dropped. A dropped parent edge gives `lineage_cycle`, and also drops that row's root edge. A
      dropped root edge gives `lineage_chain_dropped`.
    - Dropping an edge sets that result's `link` or `chain` to `None` and clears the matching
      `lineage` fields, so the bytes and the record agree: for a parent edge, `parent_*`,
      `root_*`, `carried_keys` and `dropped_keys`; for a root edge, `root_*` and the
      `startChain.*` entries of `dropped_keys`.
    - It returns the number of cycles among existing edges alone: each strongly connected
      component of two or more conversations, or a conversation linking to itself, counts once,
      whether or not a proposed edge touches it. The callers sum it over accounts into the
      manifest's `lineage_cycles`; it is never a row note.
    - Reachability is an iterative search, and the component count an iterative Tarjan, never
      recursion.
  - `_lineage_write_order(rows, key) -> list` — a stable topological order of one op's rows over
    planned targets. A row whose `lineage` names a planned row, in the same store, comes after it.
    - Ties keep input order. Rows without `lineage` keep their place.
    - If an edge somehow remains in a cycle, the rows left are appended in input order, so it
      always terminates. A test forces this.
  - `_lineage_rewrite_post(post_b64, link, chain) -> str` — rewrites `spawnedFrom` and
    `startChain` as Global Constraints say (in place, appended, or popped), and returns base64.

- [ ] **Step 1: Write the failing tests:**

```python
def test_two_account_cycle_drops_the_later_parent_edge(): ...
def test_three_account_cycle(): ...
def test_root_edge_never_evicts_a_parent_edge(): ...      # C root R, R parent C: C's chain dropped, both parent links kept
def test_a_proposed_edge_joining_an_existing_cycle_is_dropped(): ...
def test_existing_cycle_counted_once_even_when_no_proposed_edge_touches_it(): ...
def test_planned_rows_links_never_enter_the_existing_graph(): ...
def test_existing_edges_union_every_row_of_a_conversation(): ...
    # P's two rows link to C and Q; a proposed C -> P edge is dropped as lineage_cycle
def test_dropping_an_edge_clears_link_and_lineage_together(): ...
def test_one_conversation_in_two_accounts_gets_two_independent_results(): ...
def test_write_order_parents_and_roots_first_stable_ties(): ...
def test_write_order_terminates_on_a_forced_cycle(): ...
def test_rewrite_post_keeps_key_position_and_touches_nothing_else(): ...
def test_rewrite_post_appends_a_new_key_and_pops_a_removed_one(): ...
```

- [ ] **Steps 2 to 5** as in Task 1: run the tests to see them fail, implement, run both suites,
  then commit the tests and then the code.

### Task 3: `converge` planning carries the link

**Files:**
- Modify `claude_code_sessions.py`:
  - `plan_converge` (about 15199 to 15472);
  - `_print_converge_report` (about 16039);
  - `_public_converge_manifest` (about 15937);
  - `_ANON_STRUCTURAL_KEYS` (about 4093).
- Modify `claude_code_sessions_gui.py`: `_plan_summary_lines` (about 603).
- Test:
  - `tests/test_converge.py`, `tests/test_anonymize.py` and `tests/test_gui_models.py`;
  - one new check in `tools/check_gui_level.py`.

**Interfaces:**
- Consumes: Tasks 1 and 2. In `plan_converge`: `records` (`_retitle_scan`), `conversations`
  (`{sid: [_RetitleRow]}`), `m["holds"]`, `m["rows"]` and the transcript id set `tids`.
- Produces:
  - `_converge_lineage(records, conversations, rows, held_by_account, tids) -> None`, which works
    per destination account. For each account:
    - `dest` indexes the account's existing records, plus the rows planned into it, as
      `planned=True` with their decoded `post_b64` as `data`;
    - each holder account's store is indexed once;
    - every row in `rows` is resolved, then `_lineage_drop_cycles` runs;
    - each row's `post_b64` is rewritten, and `r["lineage"]` set.
  - `held_by_account` is `{account: {conversation_id: reason}}`. At plan time it comes from
    `m["holds"]` (keys `session` and `account`); Task 4 adds apply-time skips to it.
  - `_lineage_summary(rows) -> dict` with:
    - `links`: `{"carried": int, "left_out": {code: int}}`, from `LINEAGE_LEFT_OUT` codes only;
    - `chains`: `{"carried": int, "left_out": int}`;
    - `changes`: `{"changed": int, "dropped": int, "gained": int}`.
  - The manifest gains `lineage_format: 1` and `lineage_cycles` (Task 2's count, summed over
    accounts), and `m["rows"]` is put in `_lineage_write_order`.
  - **Existing tests that assert row order** are audited in this task. Each one is either updated
    with a comment (when order was incidental) or kept (when it tests the order). The report
    lists them.

**Report, JSON and window:**
- `_print_converge_report` prints the plan `links :`, `chains :` and `cycles :` lines after the
  `complete :` line, and appends `[link: <phrase>]` to the line of each row whose link was left out.
- Lineage notes live only in the rows' `lineage`, never in `m["notes"]`, so the existing
  title-disagreement branch never sees one.
- `_public_converge_manifest` keeps `lineage` and `lineage_planned`. Under `--anonymize` it
  scrubs each note's `detail` with `anonymize(env, ...)`.
- `_ANON_STRUCTURAL_KEYS` gains every key this plan emits in any public manifest or report:
  - lineage: `lineage`, `lineage_planned`, `lineage_format`, `parent_session`, `parent_row`,
    `parent_planned`, `root_session`, `root_row`, `root_planned`, `carried_keys`, `dropped_keys`,
    `code`, `lineage_cycles`;
  - the link report: `link_report`, `dangling`, `redirected`, `unchecked`, `target`, `kind`,
    `error`;
  - sync: `link_change`, `chain_change`.

  `held_sessions` is not emitted: it is keyed by conversation ids, which the anonymize key walk
  would read as unregistered keys, so `_public_manifest` drops it from the tally (Task 7).

  No source link or chain is ever emitted (Task 7), so no camelCase link key reaches a manifest.
- The GUI's `_plan_summary_lines` adds the plan's `links :` line, with its left-out reasons. This
  is the Level tab's lineage branch. The window shows no other note kind today; see "Departures".

- [ ] **Step 1: Write the failing tests.** First add a fixture, `linked_holdings(env,
  write_transcript, write_row)`, to `test_converge.py`:
  - P and C are held by A1, and C's A1 row has a side-session link to P's A1 row;
  - P is held by A2 and A3, and C is missing from both;
  - every row's `sessionId` is its stem. Pass `sessionId=` explicitly, because `row_data`
    defaults it to `"local-x"`.

```python
def test_plan_carries_a_translated_link_into_each_destination(): ...
def test_plan_carries_the_link_to_a_parent_row_created_in_the_same_plan(): ...
def test_plan_rows_are_in_write_order_parents_first(): ...
def test_plan_leaves_the_link_out_when_the_parents_pair_is_held(): ...        # parent_pair_held, with the hold reason
def test_only_on_a_child_whose_parent_is_out_of_scope(): ...                   # the link still resolves against the store
def test_dry_run_prints_the_links_and_chains_lines_and_row_reasons(): ...
def test_dry_run_and_json_report_an_existing_cycle(): ...                    # cycles line, lineage_cycles
def test_json_keeps_lineage_and_strips_row_images(): ...
def test_anonymized_plan_leaks_no_title_label_or_path(): ...                  # includes a conversation titled "lineage"
def test_a_store_without_links_plans_the_same_post_b64_as_before(): ...
def test_large_deep_store_plans_correctly_without_recursion(): ...            # the Review Focus fixture; time recorded, not asserted
def test_every_emitted_report_key_is_in_the_structural_set(): ...             # extend test_anonymize's pin with a linked fixture
def test_plan_summary_lines_include_the_links_line(): ...                     # test_gui_models
```

  Also add one check to `tools/check_gui_level.py`: a linked plan's summary shows the `links :`
  line.
- [ ] **Steps 2 to 5** as in Task 1.

### Task 4: `converge` apply re-resolves once, under the lock

**Files:** Modify `claude_code_sessions.py`: `_converge_recheck`, `run_converge` and
`cmd_converge`'s after-apply trailer (about 16241). Test: `tests/test_converge.py`.

**Interfaces:**
- Consumes: Task 3's `_converge_lineage` and `_lineage_summary`.
- Produces:
  - `_converge_recheck(env, m, ignore_own_paths=False, relink=False)`. After its pair-skip
    decisions, and only with `relink=True`, it:
    1. adds its own skips to `held_by_account`, as `{account: {session: skip_reason}}`, merged with
       the plan's holds;
    2. re-runs `_converge_lineage` over the records it just scanned and the rows still to be
       written, so a skipped row is not in the after-state;
    3. records any change. `lineage` holds neither `taskId` nor `depth`, so for each row the
       plan's `spawnedFrom` and `startChain`, decoded from `post_b64` before it is rewritten, are
       compared with the new link and chain:
       - a link in both, with a different `sessionId` or `taskId`: `lineage_retargeted`;
       - a link at plan time only: `lineage_dropped`;
       - a link now only: `lineage_gained`;
       - the link unchanged and the chain changed: a dropped chain adds `lineage_chain_dropped`,
         a gained one `lineage_gained`, and one with another `firstSessionId` or `depth`
         `lineage_retargeted`.

       The change code goes in the new `lineage["notes"]`, beside the underlying reason's code
       (a dropped link carries both `lineage_dropped` and, say, `lineage_parent_dead`).
       `_lineage_summary` counts `changes` from these codes, and its `left_out` from the reason
       codes alone. A changed row keeps the plan's record as `lineage_planned`. A row whose only
       change is in notes or `detail` gets no change code;
    4. re-sorts `m["rows"]` into write order.
  - `run_converge` calls it with `relink=True`. `recover_op`'s forward arm keeps `relink=False`
    (Task 6).
  - The after-apply trailer prints the after-apply `links :` and `chains :` lines beside
    `created :`, from `_lineage_summary` over the written rows. `--json`'s output carries the
    rows' `lineage`.

- [ ] **Step 1: Write the failing tests:**

```python
def test_apply_retargets_when_the_planned_parent_row_appeared_elsewhere(): ...
def test_apply_drops_when_the_parents_transcript_left(): ...
def test_apply_gains_a_link_when_a_parent_row_appeared_since_the_dry_run(): ...
def test_apply_reports_a_parent_skipped_as_held_title_collision_by_that_reason(): ...
def test_apply_chain_gained_dropped_or_moved_alone_is_classified(): ...
def test_apply_task_id_change_on_the_same_parent_is_retargeted(): ...
def test_lineage_and_post_b64_agree_after_apply_and_planned_is_kept(): ...
def test_journal_holds_the_final_bytes_and_order(): ...
def test_after_apply_trailer_prints_the_links_lines(): ...
def test_a_row_is_never_skipped_over_its_link(): ...
def test_recheck_defaults_to_no_relink(): ...                      # recover's call never re-resolves
```

- [ ] **Steps 2 to 5** as in Task 1.

### Task 5: Shape checks, and journals from before this change

**Files:**
- Modify `claude_code_sessions.py`:
  - `_converge_shape_error`;
  - a new `_lineage_shape_error`;
  - a new `_sync_shape_error`;
  - `classify_op`'s shape table (about 2909);
  - `classify_sync_op`, `undo_sync` and `recover_op`'s sync arms;
  - `rotate_ops` and its `_collides`, and `_sync_paths_claimed_elsewhere`.
- Test: `tests/test_lineage.py`, `tests/test_converge.py`, `tests/test_sync.py`.

**Interfaces:**
- Produces:
  - `_lineage_shape_error(m) -> str | None` — never raises.
    - With no `lineage_format`, it returns `None`. With any value other than `1`, it returns
      "its 'lineage_format' is not one this version reads".
    - `lineage_cycles`, where present, is an integer that is not a bool, and not negative.
    - Each row's `lineage` and `lineage_planned`, where present, must have the Global Constraints
      shape. Only `lineage` must also agree with the bytes ("Lineage must agree with the bytes").
    - A row with no `lineage` is valid only when its `post_b64` carries neither `spawnedFrom` nor
      `startChain`, or the manifest is `verbatim`.
  - `_converge_shape_error` keeps its current checks, then calls `_lineage_shape_error`.
    `doctor`'s vanished-row scan already calls `_converge_shape_error`, so it reads a damaged
    lineage as a damaged record, as it does today.
  - `_sync_shape_error(m) -> str | None` checks that `rows` is a list of dicts and that each row has
    string `name`, `dest_path` and `post_b64`, then calls `_lineage_shape_error`. It runs:
    - at the top of `classify_sync_op` itself, before anything reads `rows`. A shape error there
      gives `resolutions: []` with the error as the note, as `classify_op` does for a damaged
      repoint. `classify_op`'s shape table is consulted only for ops with rows in an unresolved
      store, so it alone would not close the hole;
    - in `classify_op`'s shape table, as `"sync"`, for the unresolved-store branch;
    - in `undo_sync` and `recover_op`'s sync arms, before anything reads `rows`.

    This closes a hole that exists today: a sync manifest whose rows are not dicts, or whose
    `post_b64` is not a string, makes `classify_sync_op` raise, and with it `recover`'s listing.
  - `rotate_ops` (its `live` set, and `_collides`) and `_sync_paths_claimed_elsewhere` skip a
    journal row that is not a dict, with `isinstance(r, dict)`. Today they call `r.get` on every
    row of every pending sync, retitle and realign op, and `rotate_ops` runs at the end of every
    successful mutation, so one damaged pending op makes a later, unrelated command raise.
  - Every reader of `lineage` uses `.get` and type checks, never a bare index.

- [ ] **Step 1: Write the failing tests:**

```python
def test_a_0_17_converge_journal_pending_goes_forward_and_back(): ...      # hand-built, no lineage_format
def test_a_0_17_converge_journal_completed_undoes(): ...
def test_a_0_17_sync_journal_with_a_verbatim_link_recovers_and_undoes(): ...
def test_recover_listing_and_doctor_accept_old_journals(): ...
def test_damaged_lineage_is_a_shape_error_never_an_exception(): ...
    # a string, a missing key, a wrong type, lineage_planned
def test_lineage_that_disagrees_with_the_bytes_is_a_shape_error(): ...
    # empty parent_row with a link in the bytes; another row id; a planned target not in the op
def test_a_retargeted_gained_or_dropped_row_passes_classify_recover_and_undo(): ...
    # lineage_planned disagrees with the bytes, lineage agrees: valid
def test_a_new_format_linked_row_without_lineage_is_a_shape_error(): ...
def test_unknown_lineage_format_is_a_shape_error(): ...
def test_a_damaged_sync_manifest_is_a_shape_error_in_classify_and_undo(): ...
    # rows in a resolved store: classify_sync_op returns the error, recover's listing survives
def test_a_damaged_pending_op_does_not_crash_a_later_commands_rotation(): ...
```

- [ ] **Steps 2 to 5** as in Task 1.

### Task 6: `converge` recovery, undo and the link report

**Files:**
- Modify `claude_code_sessions.py`:
  - `recover_op`'s converge arms (about 3478 to 3575);
  - `undo_converge`;
  - `undo_new_row` (about 8617);
  - `cmd_undo`'s report printing (about 5888 to 5909), and `cmd_recover`'s.
- Modify `tests/test_journal_reroot.py`: `_ROW_READERS` lists each new reader with its gate.
- Test: `tests/test_converge.py`, `tests/test_undo.py` and `tests/test_journal_reroot.py`.

**Interfaces:**
- Produces:
  - `_lineage_target(store_path, row_id) -> tuple[str, dict | None]` — the one reader of a link
    target. It returns `("present", data)`, `("absent", None)` or `("unreadable", None)`, named
    states as `_sync_row_drift` uses, never a bare sentinel. It never raises.
    - It gives `"unreadable"` unless `row_id` matches `^local_[A-Za-z0-9-]+$`, and
      `ensure_contained(<store_path>/<row_id>.json, [store_path])` passes with the file a direct
      child of `store_path`.
    - Because it calls `ensure_contained`, `test_journal_reroot`'s AST scan detects it as a row
      reader, and it is added to `_ROW_READERS`.
  - `_lineage_store_rows(store_path) -> tuple[list[tuple[str, dict]], int]` — every
    `<store_path>/local_*.json`, a direct child, as `(row_id, data)`, plus the count it could not
    read. Each file goes through `ensure_contained` and `read_json`; it never raises. It is added
    to `_ROW_READERS` too. `load_rows` is not used here: it takes store roots and globs two levels
    down, so it would read other accounts' stores.
    - Callers pass only store paths of rows that the op's resolution gate has already resolved
      (`_unresolved_rows`). A row in an unresolved store is never read through it.
  - `_converge_dependency_skip(m, r) -> tuple[str, str] | None` — for a pending row whose
    `lineage` names a parent or root.
    - `("parent_not_written", detail)`: the target is a planned row (`*_planned`) that is now
      skipped, for any reason. Rows are checked in write order, so the skip carries down a
      planned chain.
    - `("parent_changed", detail)`: the target is a planned row that is written, or an existing
      row, and it is absent, unreadable or opens a conversation other than `parent_session` or
      `root_session`.
    - `None` otherwise. A planned target still pending is not checked: it is written first, in
      write order.
  - **`recover_op`'s forward arm, in order:**
    1. `_converge_mark_landed`;
    2. if nothing is pending, close journal-only, with the link report (below);
    3. the guard;
    4. `_converge_recheck(env, m, ignore_own_paths=True)`, with `relink` left at its default of
       `False`;
    5. the dependency pass: each pending row, in write order, with a skip is marked `skipped` with
       that reason and a `skip_detail`;
    6. `execute_converge_op`;
    7. the link report.

    Recovery never resolves again.
  - **`_link_report(op_rows, stores, removed=frozenset(), restored=frozenset()) -> dict`** —
    `{"dangling": [{"row", "target", "kind"}], "redirected": [...], "unchecked": int}`.
    - It never raises: any failure gives `{"error": "<one sentence>"}` and never fails the command
      it reports for. `cmd_undo` and `cmd_recover` print that as the `links not checked: the link
      report failed (...)` line.
    - `stores` are the resolved store paths of the op's rows. `removed` and `restored` are sets of
      `(store_path, row_id)` pairs: the rows this command removed, and the rows it put back to a
      pre-image. Pairs, not bare ids, because `sync` keeps filenames across accounts, so one id
      can name healthy rows in other stores.
    - It scans each store in `stores` once, through `_lineage_store_rows`, **after** the command's
      writes, never from a census taken before them, so a removed row is not read as surviving.
    - **Dangling:** a row in a store whose `spawnedFrom.sessionId` or
      `startChain.firstSessionId` names an id removed from that same store. Forward recovery
      passes no `removed`, and instead checks each written row of the op with a `lineage` target,
      through `_lineage_target`. An absent target is dangling; that is the crash-window deletion.
    - **Redirected:** a row of the op, not in `removed` or `restored`, that still carries the link
      this op wrote (its current `spawnedFrom.sessionId` equals `lineage["parent_row"]`, and the
      same for the chain), whose target exists but opens a conversation other than the journaled
      one. A restored row carries no expectation from this op, even when its pre-image names the
      same row id, so it is never checked.
    - `unchecked` counts the rows or targets it tried to read and could not.
  - The callers set `m["link_report"]`, then save the manifest (`save_manifest(op)`) before
    `rotate_ops`, so the report is on disk and not only in memory:
    - forward recovery, on both paths. `execute_converge_op` has already completed and saved the
      op by then, so this is a second save;
    - `recover --back` and `undo_converge`, after the whole reversal;
    - `undo_new_row`, for the one row it removed.
  - `undo_converge` and `recover --back` iterate `m["rows"]` in reverse, which is write order for
    new-format ops. This changes one thing today's order decides: when a conversation's rows
    would all go, `undo_converge`'s last-pointer rule keeps the one met last, which now follows
    reverse write order. Which account keeps the row is not otherwise specified, and this plan
    accepts the change; a test pins it so it does not move again unnoticed.
  - `cmd_undo` and `cmd_recover` print the report's lines (Global Constraints) after their
    existing lines.

- [ ] **Step 1: Write the failing tests:**

```python
def test_crash_before_the_parent_landed_forward_writes_parent_then_child(): ...   # pending planned parent: no skip
def test_crash_then_another_parent_row_appears_child_skipped_parent_not_written(): ...
def test_planned_chain_skip_carries_down_r_p_c(): ...
def test_parent_written_then_repointed_child_skipped_parent_changed(): ...
def test_existing_parent_deleted_in_the_window_child_skipped(): ...
def test_child_landed_then_parent_deleted_forward_reports_dangling(): ...
def test_all_landed_forward_still_reports_links(): ...
def test_undo_removes_children_before_parents(): ...
def test_undo_last_pointer_survivor_follows_reverse_write_order(): ...
def test_undo_reports_a_surviving_link_to_a_removed_row_from_an_earlier_op(): ...
def test_undo_does_not_report_unrelated_pre_existing_broken_links(): ...
def test_back_after_a_skipped_drifted_child_reports_dangling(): ...
def test_link_report_counts_unreadable_targets_and_never_raises(): ...
def test_link_report_scans_only_the_ops_stores_after_the_writes(): ...
    # a same-id row in another account is not dangling; a cleanly removed child is not read
def test_link_report_is_saved_to_the_journal_on_forward_recovery(): ...
def test_link_report_failure_prints_its_line(): ...
def test_lineage_target_refuses_a_traversal_id_and_never_reads_an_unresolved_store(): ...
def test_new_row_undo_reports_a_surviving_link_to_its_row(): ...                  # test_undo.py
def test_every_row_reader_goes_through_the_gate(): ...                            # test_journal_reroot, extended
```

- [ ] **Steps 2 to 5** as in Task 1.

### Task 7: `sync` planning, restructured

**Files:**
- Modify `claude_code_sessions.py`:
  - `plan_sync` (about 9032 to 9278);
  - `select_sync_rows`'s tally (about 7355);
  - `_print_sync_report` (about 14417);
  - the `--verbatim` argparse help (about 5672);
  - `_public_manifest`.
- Test: `tests/test_sync.py`, `tests/test_anonymize.py`.

**Interfaces:**
- Consumes: Tasks 1 and 2.
- Produces, in `plan_sync`, the spec's seven planning steps in order:
  1. **Transformed bytes**, from `transform_row` as today. A non-verbatim row's `spawnedFrom` and
     `startChain` stay in place, and step 5 replaces or pops them. No source link or chain is
     stored on the row or the manifest.
     - **Exact identity stays first, as today.** A refresh whose transformed bytes already equal
       `pre` goes to `tally["unchanged"]` here, before the age holds, so a byte-identical row is
       never reported as `held_same` or `held_unknown`. Such a row changes nothing, so the
       after-state is the same either way (see "Departures").
  2. **Age holds next.** `--newer-only`'s `held_older`, `held_same` and `held_unknown` now run
     before the provisional unchanged test. A held row is recorded in a new tally dict,
     `held_sessions: {source_cliSessionId: reason}`.
     - A held row keeps its `activity_unknown` or `regressing` entry, as today. For a row that is
       not held, those two informational entries are made at step 6, and only if the row
       survives it, so a row dropped as unchanged is never counted in them.
  3. **Provisional unchanged.** A refresh whose decoded row equals the destination's, ignoring
     `spawnedFrom` and `startChain`, is marked provisional and kept.
  4. **`held_orphan`.** Its `doomed` set is the refreshes that are not provisionally unchanged. A
     row it holds is recorded in `held_sessions` too.
  5. **The lineage pass**, non-verbatim only. Its inputs:
     - the holder is the source row, and `holder_index` is the source store's index;
     - `dest` is the destination store, with each surviving refresh replaced by its decoded
       `post_b64` (the source's contents, transformed), plus the planned adds the same way, each
       `planned=True`, as `converge` does (Task 3). A planned row's links are never read
       (Task 2);
     - held rows count as they stand;
     - `held` is `held_sessions`;
     - the preferred parent and root filenames are the ones the source links name.

     Then:
     - each row's `post_b64` is rewritten, and its `lineage` set;
     - for a refresh whose destination row already carries a link with `sessionId` T and the
       same `taskId`, that link is kept as it is (Global Constraints).
  6. **Final unchanged.** A provisionally unchanged refresh whose final bytes equal `pre` is
     dropped, into `tally["unchanged"]`.
     - Any other row's `lineage` that named it as planned now names it as existing
       (`parent_planned` or `root_planned` set `False`): it is still there, unchanged.
     - A refresh whose only change is the link is kept.
  7. **Field loss on the final bytes.** `spawnedFrom` and `startChain` are left out of
     `dest_dropped`. Each refresh, verbatim or not, compares the destination's row with its final
     bytes:
     - `link_change: "dropped" | "retargeted"` when the destination's `spawnedFrom` is gone, or
       names a different row id;
     - `chain_change: "dropped" | "retargeted"` when its `startChain` is gone, or names a
       different `firstSessionId` or `depth`.

     The report prints one line for each (Global Constraints). A `taskId`-only change names the
     same parent row, and is not reported.
- **The manifest** gains `lineage_format: 1` and `lineage_cycles`, and its rows come back in
  write order.
- **`--verbatim`:**
  - it skips steps 3, 5 and 6's link handling and copies links as is;
  - its rows carry no `lineage` and keep their order;
  - its adds that carry a link are counted for the `copied verbatim` line;
  - its help text is the Global Constraints sentence.
- **Report:** `_print_sync_report` prints the plan `links :`, `chains :` and `cycles :` lines, the
  link-change and chain-change lines, and the verbatim line.
- **Public manifest:** `_public_manifest` keeps `lineage`, `link_change` and `chain_change`, and
  under `--anonymize` scrubs note details as converge does. It drops `held_sessions` from the
  tally it emits, with or without `--anonymize`: the dict is keyed by conversation ids, which
  the tally's per-list anonymizer and the key walk in `test_anonymize` would both mishandle.
  `held_sessions` stays in the in-memory manifest's tally, which the journal does not keep
  (`run_sync` strips the tally; Task 8 reads it before `new_op` for that reason).

- [ ] **Step 1: Write the failing tests:**

```python
def test_sync_add_translates_the_link(): ...
def test_sync_add_with_no_parent_in_b_carries_no_link_and_says_so(): ...
def test_sync_parent_removed_by_held_orphan_gives_no_link(): ...
def test_sync_parent_synced_in_the_same_run_same_filename(): ...
def test_sync_parent_refreshed_into_another_conversation_is_not_chosen(): ...
def test_sync_newer_only_hold_on_the_parent_resolves_against_the_held_row(): ...
def test_sync_link_only_refresh_is_written_not_discarded(): ...
def test_sync_link_only_refresh_still_vouches_in_held_orphan(): ...
def test_sync_dropped_unchanged_parent_turns_the_childs_link_to_existing(): ...
def test_sync_refresh_that_drops_the_destinations_own_link_reports_it_once(): ...
def test_sync_refresh_that_drops_or_moves_the_destinations_chain_reports_it(): ...  # chain_change, verbatim too
def test_sync_round_trip_keeps_the_destinations_link_and_bytes(): ...      # A->B, then B->A --update: A's row byte-identical
def test_sync_verbatim_copies_links_and_counts_them(): ...
def test_sync_verbatim_update_replacing_a_link_is_reported_by_the_link_line(): ...
def test_sync_verbatim_help_text(): ...
def test_sync_without_links_plans_the_same_post_b64_as_before(): ...
def test_sync_json_and_anonymize_emit_no_source_link_and_no_unregistered_key(): ...
    # with a held row: held_sessions never emitted
def test_sync_newer_only_byte_identical_row_is_unchanged_not_held(): ...
def test_sync_row_dropped_as_unchanged_is_not_counted_as_activity_unknown(): ...
```

- [ ] **Steps 2 to 5** as in Task 1.

### Task 8: `sync` apply re-resolves once, under the lock

**Files:** Modify `claude_code_sessions.py`: `run_sync` (about 9642) and `cmd_sync` (about
14280). Modify `claude_code_sessions_gui.py`: the sync pane's `_apply_done` (about 4650). Test:
`tests/test_sync.py` and `tools/check_gui_undo.py`.

**Interfaces:**
- Produces, in `run_sync`, after its guard and lock and **before `new_op`**, for a manifest that
  is not `verbatim`. A verbatim op skips all of this, as Task 7 skips its link handling. It runs
  there because the tally, which holds `held_sessions`, is not journaled:
  1. It re-reads the source and destination stores.
  2. It re-runs Task 7's step 5 against the current source rows. Each row's source row is
     re-read by name from `source_path`, and must still open the row's `session_id`.
     - If it does not, and the plan's `lineage` named a parent or a root, the row keeps neither,
       with the note `lineage_source_changed`. A row that had no link or chain at plan time gets
       no note.
     - The row's other bytes are the plan's snapshot, as `run_sync` writes them today; only the
       link and the chain reflect the stores at apply time, as in `converge`, where the row is
       synthesized at plan time and its link re-resolved under the lock.
  3. It rewrites `post_b64` and `lineage`, with `lineage_planned` and the same change codes as
     Task 4.
  4. It recomputes "unchanged", `dest_dropped`, `link_change` and `chain_change`.
  5. For a refresh whose final `post_b64` now equals its `pre_b64`, it reads the destination
     row's current bytes, under the lock:
     - bytes equal to the final image: the refresh is dropped as a no-op. Then Task 7 step 6's
       reconciliation runs again, so a surviving row whose `lineage` named it as planned now
       names it as existing;
     - the row absent, unreadable or different: `run_sync` refuses with a `Refusal`, before
       `new_op`, so nothing is journaled: "the destination's row <8-char id> changed since the
       plan; plan again". Equality with the plan's pre-image says nothing about the store now,
       and treating that row as existing could leave a child linking to a row that is gone.
  6. It re-sorts the rows into write order.
- If no row is left, `run_sync` returns `"unchanged"` without journaling an op. `cmd_sync` treats
  `"unchanged"` as success, as `cmd_converge` does:
  - exit 0;
  - in `--json`, `result: "unchanged"`;
  - otherwise the line `nothing to write: the destination already holds these rows`, and no
    `copied :` line.
  - The account-email memo is still written before the lock, as today (`remember_account_email`).
    An `"unchanged"` result does not undo it, since an apply was asked for.
- **The window.** `claude_code_sessions_gui.py`'s `_apply_done` today ignores `run_sync`'s
  result and counts written rows. On `"unchanged"` it shows the status `Nothing to copy: the
  destination already holds these rows`, does not mark a mutation as run, and leaves the Undo
  button as it was, so Undo never points at an older op. `tools/check_gui_undo.py` gains that
  check.
- `cmd_sync`'s after-apply trailer prints the after-apply `links :` and `chains :` lines, beside
  `copied :` and `refreshed :`.
- Swap and voucher decisions are not recomputed: they read `cliSessionId`, which the link does not
  change.

- [ ] **Step 1: Write the failing tests:**

```python
def test_sync_apply_retargets_drops_or_gains_like_converge(): ...
def test_sync_apply_source_row_repointed_since_the_plan_keeps_no_link(): ...   # lineage_source_changed
def test_sync_apply_source_row_changed_on_an_unlinked_row_adds_no_note(): ...
def test_sync_verbatim_apply_skips_the_pass_and_keeps_order(): ...
def test_sync_apply_drops_a_refresh_that_became_a_no_op(): ...           # never journals post == pre
def test_sync_apply_dropped_parent_turns_a_kept_childs_link_to_existing(): ...
    # the journal passes the shape check, then recovers and undoes
def test_sync_apply_no_op_refresh_whose_row_was_deleted_or_repointed_refuses(): ...
    # parent and chain root alike; nothing journaled, no child written
def test_sync_apply_that_leaves_nothing_journals_no_op_and_exits_0(): ...  # text and --json
def test_sync_after_apply_trailer_prints_the_links_lines(): ...
def test_sync_journal_holds_the_final_bytes_and_order(): ...
```

- [ ] **Steps 2 to 5** as in Task 1.

### Task 9: `sync` recovery and reversal

**Files:**
- Modify `claude_code_sessions.py`:
  - `classify_sync_op` (about 9904);
  - `_sync_reverse_all` (about 10282), and its callers in `undo_sync` and `recover_op`'s sync
    back arm;
  - `recover_op`'s sync forward arm, for the link report.
- Test: `tests/test_sync.py`.

**Interfaces:**
- Produces:
  - **`classify_sync_op`** treats a dependency mismatch as drift, using Task 6's
    `_lineage_target`. Its rules:
    - a pending row's target is checked only when the target is a row the op has written, or a
      row not in the op (existing, age-held or unchanged);
    - a target that is a pending row of the op is not checked: it is written first, in write
      order. The same goes for a pending refresh still at its pre-image;
    - a checked target that is absent, unreadable, or opens a conversation other than the
      journaled one is a mismatch;
    - a mismatch gives `resolutions = ["back"]`, with a note naming the row. `sync` has no per-row
      skip, and none is added.
  - **`_sync_reverse_all(ops)`** takes one list, in reverse write order, of `("remove", path)` and
    `("restore", path, pre_bytes)` entries.
    - Its callers build it from `_sync_delete_targets`' results: `removable` paths and
      `restorable` pairs are mapped back to their row's index in `m["rows"]`, then sorted by that
      index, descending.
    - Rows in neither list are absent from it, and shift nothing.
    - It keeps today's behaviour of collecting failures and raising them together.
  - Both callers then set `m["link_report"]` with Task 6's `_link_report`, passing the
    `(store_path, row_id)` pairs they removed as `removed` and those they restored as `restored`,
    and save the manifest as Task 6 says. `cmd_undo` and `cmd_recover` print it.
  - **`recover_op`'s sync forward arm** sets `m["link_report"]` too, after `execute_sync_op`,
    and on the path where every row had already landed. As for converge (Task 6), it passes no
    `removed`, and checks each written row with a journaled `lineage` target through
    `_lineage_target`. So a landed child whose parent was deleted or repointed before `recover
    --forward` is reported, even though `classify_sync_op` checks pending rows only. It saves
    the manifest after setting the report, since `execute_sync_op` has already completed and
    saved the op. `cmd_recover` prints it.

- [ ] **Step 1: Write the failing tests:**

```python
def test_sync_forward_reports_a_landed_childs_parent_deleted_or_repointed(): ...
def test_sync_forward_all_landed_still_reports_links(): ...
def test_sync_crash_before_the_parent_add_landed_forward_is_offered(): ...         # pending planned parent: forward + back
def test_sync_crash_between_parent_and_child_forward_finishes(): ...
def test_sync_parent_written_then_repointed_withdraws_forward(): ...
def test_sync_existing_parent_outside_the_op_deleted_withdraws_forward(): ...
def test_sync_age_held_or_unchanged_parent_changed_withdraws_forward(): ...
def test_sync_reversal_restores_a_refreshed_child_before_removing_an_added_parent(): ...  # crash between the two
def test_sync_back_with_a_drifted_child_still_carrying_our_link_reports_redirected(): ...
def test_sync_undo_restoring_a_different_original_parent_reports_no_redirection(): ...
    # parent and child refreshed; the child's pre-image names the same row id; both restored
def test_sync_undo_reports_dangling_links_to_removed_rows(): ...
```

- [ ] **Steps 2 to 5** as in Task 1.

### Task 10: Documentation

**Files:**
- `README.md`: the converge and sync sections (about line 490). The roadmap entry belongs to the
  release, not this task.
- `docs/internals.md`: a "Carrying the parent-child link" section, beside the hard-won
  invariants (about line 920).
- `docs/specs/2026-08-22-new-row-design.md`: a dated note under "Field derivation".

**What each says:**
- **README:** what is carried and when; the `links :` and `chains :` lines; what `--verbatim`
  does with links; and that `undo` can leave a link dangling, which it reports.
- **internals.md:**
  - the pass, with its index and its steps;
  - the cleared variants and why;
  - row identity versus conversation nodes;
  - write order;
  - recovery's no-re-resolution rule, `parent_not_written` and `parent_changed`;
  - the link report's scope;
  - `lineage_format` and old journals;
  - the writer bound;
  - this plan's departures from the spec.

  It should also state the evidence behind `taskId`: Phase 0, one candidate per variant, with the
  release's acceptance check as the second measurement. If that check disagrees, the carried set
  goes back to the maintainer.
- **new-row design note:** `new-row` still writes no link; `converge` and `sync` add one after
  synthesis, per the carry-lineage design.

- [ ] **Step 1:** Write the three changes, in the house style: no em-dashes, and the fake cast.
- [ ] **Step 2:** Run both full suites once more.
- [ ] **Step 3: Commit** — `docs: carrying the parent-child link - what converge and sync write,
  the links lines, recovery and the link report`.

## Departures from the spec

These are this plan's calls, recorded so the spec's authority can be checked against them:
1. **`sync` keeps a destination link that already names the same row and `taskId`**, instead of
   rewriting it to the bare two keys. It stops a round trip from stripping keys from the original
   account's row, and stops it rewriting bytes that need no change. It narrows L4's whole-row
   rule for this one key, and the link stays correct.
2. **`sync`'s apply-time pass re-reads the source row** rather than keeping its raw link in the
   plan. The spec's aim, resolving from the source and not from a stripped post-image, holds
   either way, and no source connector state enters the journal or `--json`.
3. **The dangling report covers rows that link to a row the command removed**, plus, for forward
   recovery, the op's own targets. It is not every broken link in the store.
4. **The Level tab's lineage branch is the `links :` line** with its reasons. The window renders
   no per-row notes of any kind today.
5. **Codes and phrases the spec leaves open** are in the Global Constraints table.
6. **`sync` keeps today's exact identity test ahead of the age holds.** The spec's order puts the
   holds before any unchanged test. Taken literally, `--newer-only` would then report a
   byte-identical row as `held_same` or `held_unknown` instead of `unchanged`. Such a row writes
   nothing either way, so the after-state the lineage pass sees is the same; only the report's
   heading differs, and today's is kept. The spec's provisional test still follows the holds.
7. **`run_sync` refuses when a refresh that became a no-op finds its destination row changed**
   (Task 8, step 5). The spec does not cover it. Treating that row as existing could leave a
   child linking to a row that is gone.

## Release

Release placement is the maintainer's call, and is not part of this plan. Before it, the spec's
acceptance check runs on the maintainer's machine, with the app closed:
- `converge --apply`, then open a carried side session in an account that lacked it;
- one older-variant child as well;
- one `sync` add, when one is available.

## Revisions

- **Round 1 (Codex, agy and Kimi reported; DeepSeek INCOMPLETE, its reply cut before its last
  line, its points used as leads).** The plan is rewritten:
  - results keyed by row, and the graph over conversations per account (all four);
  - recovery and `classify_sync_op` no longer treat a pending planned target as drift, and the
    dependency pass runs after the recheck (Codex, agy, DeepSeek);
  - lineage must agree with the bytes (Kimi, Codex);
  - a contained, gated target reader (Kimi);
  - the old-journal rule settled (Codex, Kimi);
  - the link report scoped, non-raising, and never comparing a restored row with the op's
    expectation (Codex, agy, Kimi, DeepSeek);
  - `sync`'s empty apply exits 0 (Kimi, DeepSeek);
  - `held` keyed by conversation everywhere (Kimi, agy, DeepSeek);
  - apply-time change classification defined (Kimi);
  - the variant and `taskId` predicates split (DeepSeek);
  - no source link stored, and every emitted key registered (DeepSeek, agy);
  - byte order kept on rewrite (agy, DeepSeek);
  - planned flags fixed when an unchanged parent is dropped (agy);
  - `relink` defaults to `False` (DeepSeek);
  - an iterative graph walk with a deep-store fixture (Codex, Kimi);
  - the suite command's exit code (Codex);
  - the writer bound stated (Codex);
  - spec tests restored: `--only`, a parent held at apply, a title literally `lineage` (DeepSeek);
  - the shipped recovery fix noted as done (DeepSeek, Kimi).
- **Rejected in round 1:**
  - "A child row should not be skipped when only its chain root changed" (agy). The skip is L6 as
    ruled; the next `converge` creates the row afresh.
  - "One mismatch should not make all of a sync back-only" (agy). `sync` has no per-row skip by
    the spec's round-2 ruling.
  - "Pick an unarchived row before the same filename" (agy). The spec's order puts `sync`'s
    filename first.
  - "Bump the version" (DeepSeek). That is the release's step.
- **Round 2.** Codex, Kimi and DeepSeek reported. Gemini was lost for the round:
  - Gemini 4 Argon High, the skill's pin, ran twice, and both runs ended at agy's 15-minute print
    timeout;
  - Gemini 4 Neon High, the stand-in the maintainer chose for this plan's review, ran twice, and
    both runs ended on agy's `command` denial.

  Codex's two findings were fixed while Kimi and DeepSeek were still reading; both raised the
  same two, so each saw the earlier text there. Accepted, all of them:
  - `lineage_planned` is history, checked for shape only; only `lineage` must agree with the
    bytes. Until now this made every retargeted, gained or dropped row a shape error (Codex,
    Kimi, DeepSeek; a defect in round 1's fix);
  - the existing graph takes the union of every row of a conversation (Codex, Kimi);
  - `sync`'s apply-time drop of a no-op refresh runs the planned-flag reconciliation again
    (Kimi);
  - a `sync` refresh reports a lost or moved `startChain` (`chain_change`), as the spec's link
    line says (Kimi, DeepSeek);
  - an existing cycle is counted once per account into `lineage_cycles`, and reported by a
    `cycles :` line whether or not a proposed edge touches it, as the spec says (Kimi);
  - `_sync_shape_error` runs at the top of `classify_sync_op` itself, since `classify_op`'s shape
    table is read only for unresolved stores (DeepSeek, checked against the code);
  - a planned row's links never enter the existing graph (DeepSeek);
  - the apply-time change codes live in `lineage["notes"]` (DeepSeek);
  - undo's last-pointer survivor now follows reverse write order, accepted and pinned by a test
    (DeepSeek);
  - `_lineage_target` returns named states, and calls `ensure_contained` so the AST gate detects
    it (DeepSeek);
  - the window handles `run_sync`'s `"unchanged"` (DeepSeek's inference; checked: `_apply_done`
    ignores the result today);
  - the pinned-output checks and the anchors' drift are named (DeepSeek);
  - the line-ending constraint is corrected: every tracked file is LF in git and CRLF in this
    working copy (DeepSeek; checked with `git ls-files --eol`).

  Also this plan's own additions: the account-email memo on an `"unchanged"` sync, and a shape
  check for `lineage_cycles`. Nothing was rejected in round 2.
- **Round 3, the last under the skill's cap.** All four reported: Codex, Kimi, DeepSeek, and
  Gemini 4 Neon High, standing in for the pin as the maintainer ruled. Accepted:
  - a refresh that became a no-op at apply time is dropped only when the destination still holds
    those bytes; otherwise `run_sync` refuses (Codex; Departure 7);
  - `sync`'s forward recovery runs the link report, as `converge`'s does (Kimi);
  - the link report:
    - takes `removed` and `restored` as `(store, row id)` pairs;
    - scans only the op's own stores, after the writes, through a new direct-child reader. Not
      `load_rows`, which takes roots and globs two levels down (checked);
    - never compares a restored row with this op's link;
    - is saved to the journal on forward recovery;
    - prints a line when it fails (Neon);
  - `run_sync`'s apply pass skips a verbatim op (Neon);
  - a source row changed since the plan gets its own code, `lineage_source_changed`, and only
    when the row had a link or chain, never `parent_not_in_account` (Neon, DeepSeek);
  - `sync`'s planned rows enter the after-state from their decoded `post_b64`, as in `converge`
    (Neon);
  - today's exact identity test stays ahead of the age holds, and the informational tallies count
    only rows that survive (Neon; Departure 6);
  - the apply-time change test compares the decoded link and chain, so a `taskId` or `depth`
    change and a moved chain are classified (Neon);
  - a chain source that does not resolve drops the chain with its note, as the spec says. This
    reverses round 1's change, which the spec does not allow (Neon, checked against the spec);
  - `held_sessions` is never emitted, and `error` is registered (Neon, checked against
    `_public_manifest`);
  - `carried_keys` and `dropped_keys` describe the link written, also when a destination link is
    kept or an edge is dropped (Neon, DeepSeek);
  - the deep-store test asserts results, not time (DeepSeek);
  - `rotate_ops` and `_sync_paths_claimed_elsewhere` skip a non-dict row (DeepSeek, checked);
  - other fixes:
    - ties in the `links :` line;
    - the old-journal sentence corrected;
    - `transcript_exists`'s type (DeepSeek).
- **Rejected in round 3:** "mixing the plan's row body with the source's link at apply time is
  split-brain" (Neon). The body is the plan's snapshot, as `run_sync` writes today, and the link
  reflects the stores under the lock, as in `converge`. That is the spec's design, now stated in
  Task 8.
