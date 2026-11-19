# relink: give rows already in place the parent link another account carries (design)

Status: **approved by the maintainer on 2026-11-19 (12:32, "Yes, both"), with D2 amended to a
wider proof; reviewed in three rounds.** The maintainer chose the shape: a command of its own
(not part of `converge`), every repairable row, proved first on one row and then on a few of
each risky kind, and a new `relink` command on `realign`'s per-key journal rather than a mode of
`realign` ("Decisions"). A builder's read raised four questions, settled as D11 to D14. Three
review rounds (Codex, agy, the repo-aware Fireworks.ai roster: DeepSeek and Kimi) are applied
("Revisions"); **round 3's fixes have not been re-reviewed**, the review loop's cap. Not built.

## Why

0.18.0 makes `converge` and `sync` write a side session's parent link (`spawnedFrom`) and its
chain (`startChain`) translated into the destination account, so a row they create from then on
nests under its parent there. Rows that already exist are untouched: a side session whose row in
another account was made before 0.18.0, by `converge`, by the app, or by anything else, still
shows on its own there, outside its parent. The maintainer wants those children under their
parents in every account too. This was ruled on 2026-11-17 as the second step after the
link-carrying change ("option 2", repairing existing rows), to be designed on the journaled,
undoable update path `realign` uses.

## What exists today (measured 2026-11-19 on the maintainer's machine, read-only)

Three account stores of about 1,000 rows each, 405 MB in all, a row averaging 135 KB. Counted
per row of a child conversation in an account that is not the one whose row carries the link:

| State of the row | Rows |
|---|---|
| already carries a link naming its own account's row for the right parent, with the agreed `taskId` | 629 |
| has no link object, while another account's row carries a cleared one, and its own account holds a row for the parent | **931** |
| carries a link object naming a row for another conversation, or naming no row | 0 |

- The 931 belong to 466 conversations; 64 of the rows are archived. Nearly every one of the 466
  has two candidate rows, one in each account that lacks the link. [measured; the split by
  arithmetic]
- 42 rows lack a `startChain` another account's row carries. Every one of them lacks the link
  too, and none of the 931 carries a chain of its own. The 42 is an upper bound on chains
  written: a row whose link is left out loses its chain with it (D6). [measured]
- 40 child conversations are left aside by the count: their accounts' rows disagree on the
  parent. [measured]
- No existing link lacks a `taskId`, the shape Phase 0 measured as hiding the child. [measured]
- Every one of the 931 has at least one link the app wrote itself among its holders (D11).
  [measured]
- Who made the 931 [measured from the journal and each row's keys; the origin is inference]:
  - 322 were written by a journaled `converge` or `sync` operation;
  - 434 more carry only the 18 keys `converge` writes, or those plus a few the app adds when it
    opens a row. These are most likely `converge` rows whose operations have left the journal;
  - 175 carry many keys beyond `converge`'s, the shape of a row the app wrote itself.
- Every carried link measured so far (Phase 0's three runs, and the 0.18.0 acceptance check)
  went onto a row this tool wrote. The app's response to a link added to one of its own rows
  is unmeasured. That is why the maintainer ruled "proved on one first". [doc]

## Goals

1. Every existing row of a child conversation that lacks a link another account carries, and
   whose account holds the parent's row, gains it, translated exactly as 0.18.0's `converge`
   would write it on a new row, with the chain when one resolves beside it.
2. Nothing else in a row changes. A repaired row is its old value with only `spawnedFrom` and
   `startChain` set, every other key and the key order kept. The bytes are re-serialized, as
   `realign`'s are, so the test is value identity, not byte identity.
3. Undoable per operation, and safe across the app's own later edits of the same rows and across
   other operations: `undo` takes out exactly what `relink` added and keeps everything else.
4. Provable on one row before the rest: `--only` with a row id narrows a run to that one row.

## Non-goals

- Replacing a link or chain a row already has as an object, even one that disagrees. None exists
  today; one that turns up is reported and left alone (D12).
- Choosing a parent for the 40 children whose accounts disagree. They stay as they are, with
  the reason printed.
- Creating rows. That stays `converge`'s job; a missing parent row means no link.
- The desktop window. `relink` is CLI-only, as `realign` is.
- A `doctor` check for missing links. `relink`'s dry run is the report (D1 made it a command
  run on purpose; a gap that appears later shows in the next dry run).

## Anti-goals

- A link that hides a child. A `sessionId`-only link did that in Phase 0, so every link
  `relink` writes is exactly `{sessionId, taskId}`, the measured shape.
- Any key besides the two. A row the app wrote keeps every key it had, in its order.
- An `undo` or a recovery that undoes the app, or another operation. A key that no longer holds
  what `relink` wrote, or that a later operation claims, is left as it is and named.

## Approaches (the maintainer chose A, 2026-11-19 11:02)

- **A, chosen: a new `relink` command on `realign`'s per-key journal.** It plans each missing
  link with 0.18.0's resolver, journals the old and new value of just the two keys per row, and
  undoes per key. Each command keeps one job, and the per-key undo is what makes editing the
  app's own rows safe. It costs a new operation type, which the code's dispatch points and
  other operations' ownership checks must learn ("Integration").
- B, not taken: a `--links` mode inside `realign`. It would reuse more plumbing, but
  `realign`'s plan, holds and `--release` are about transcript copies, and an operation that is
  sometimes copies and sometimes links muddies its output, its undo and the journal.
- C, ruled out: refreshing the rows with `sync --update`. It replaces the whole row, so the
  app's own keys on its rows would be lost.
- Also not taken (the maintainer's call, 11:01): repairing inside every routine `converge`.
  It would keep links whole on its own, but `converge` would start editing rows, and its undo
  would have to restore pre-images rather than delete rows.

## Decisions

The maintainer's calls (2026-11-19):
- D1, 11:01: its own command, not part of `converge`.
- D2, 11:01: all 931 repairable rows, the app's own included, proved on one row first.
  *(Amended 12:32, the maintainer's call on the four review engines' advice: proved first on
  one row shaped like the app's own, then on one row each of a chain gap, an archived child, a
  child whose parent row in that account is archived, and an app row whose link holders carry
  `sharedFolder`; then the rest, stopping and undoing on a hidden child, a dropped link, or any
  key changed besides the app's ordinary ones. Option not taken: one row only.)*
- D3, 11:02: approach A, a new `relink` command.

My calls, open to review (each is the default the design rests on):
- D4: archived rows are included. A link is metadata. That it changes nothing an archived row
  shows until it is unarchived is an assumption, unmeasured.
- D5: only a missing key is written. "Missing" means absent, or present but not an object
  (null, false, a string, a list), which names no row (0.18.0's R28 rule). A present object is
  never replaced. A non-object is replaced where it stands, and `undo` puts back exactly that
  value.
- D6: the chain follows the link, as 0.18.0's resolver ties them. A row gains a chain only when
  its link is written by the same run, or is kept and agrees (D12). A row whose link is not
  written for any reason, D11's included, gains no chain.
- D7: the resolver, its rules and its reason codes are 0.18.0's, unchanged. Two codes are
  added for D12: `own_link_differs`, a link code in `LINEAGE_LEFT_OUT`, and `own_chain_differs`,
  a chain code kept out of it, as `lineage_chain_dropped` is. Both get phrases in
  `LINEAGE_PHRASES`.
- D8: `undo` restores per row and per key, and never refuses for the app's later edits; it
  names each key it left. Another operation's claim is another matter (D13, D18).
- D9: exit 0 when the run succeeds, whether or not rows were left out; 1 on a refusal or an
  error. `converge` and `realign` exit 3 when something is held, because a hold asks for an
  action (close the app, resolve a split). A left-out link here asks for none: holders that
  disagree stay that way until someone changes a row, so a 3 would greet every run forever.
- D10: the plan lists only rows that gain a key, up to 15 per account and then "... and N
  more", as `sync` does; `--verbose` lists them all, and the rows left out with their tags.

Settled after the builder check (2026-11-19), my calls:
- D11: a link is written only when an app-written link backs it. App-written means exactly one
  of two shapes: a link with `sideSession: true` and any other keys, or exactly
  `{sessionId, taskId, title}` with a non-empty string `title`. This tool's own
  `{sessionId, taskId}` never counts, although `_lineage_variant_ok` clears it for carrying, so
  D11 has a predicate of its own. For a link candidate, an app-written link must be among the
  holders that yield the agreed parent. For a row that keeps its own agreeing link and gains only
  a chain, any agreeing linked row of the conversation counts, the row's own included. A tool copy
  supports a link but never justifies one alone, so a row whose app-written link was taken away
  is not re-nested from the tool's copies alone. Its limits, stated: the provenance is read from
  the shape, not proved; and if another account still holds an app-written link, a row whose own
  account's app removed its link is re-nested. A snapshot cannot tell a deliberate unlink from a
  link never received, and the dry run under `--verbose` lists every row first. Measured today:
  all 931 repairable rows have an app-written link among their holders.
- D12: agreement is judged across the whole conversation, every account's linked row of it
  included, before any row is resolved.
  - If the conversation's links disagree on the parent or on `taskId`, every row of it is left
    out with 0.18.0's reason (`lineage_parent_disagree`, `lineage_task_disagree`). A kept link
    that names the wrong parent or carries the wrong `taskId` is that disagreement, and is
    reported that way, once per row of the conversation.
  - If they agree, a row's kept link must also name a usable row: one in its own account, by a
    string `sessionId`, whose `cliSessionId` is a non-empty string. A kept link that does not, an
    empty object included, is `own_link_differs`, counted with the links, and its row gains
    nothing. An unreadable target is not that case: it refuses the run (D16).
  - A kept chain is judged only when the holders carry one: it agrees when it names a usable row
    in its own account for the agreed root, with the agreed `depth`. One that does not is
    `own_chain_differs`, counted with the chains, and its row gains nothing. When no holder
    carries a chain, a kept chain stands, unjudged, and is not entered in the `lineage` record.
  - The journaled `lineage` record describes what `relink` stands behind. For a key it writes,
    the fields come from the resolver. For a kept agreeing link, `parent_row` and
    `parent_session` name what it names, read at plan time, and `carried_keys` the kept link's
    keys, as `sync` records a kept link; `dropped_keys` keeps only `startChain.*` entries, as
    `sync`'s pass strips the rest. A kept chain, judged and agreeing, fills the root fields the
    same way; an unjudged one leaves them empty. `*_planned` is always false.
  - None of these cases exists today: all 629 linked rows carry the agreed `taskId`, and none of
    the 931 carries a chain of its own. [measured]
- D13: `undo`, and recovery's `back`, leave alone every row a later live operation claims.
  - **Who claims.** Every later live operation that wrote the row claims it whole, whatever its
    type: `sync --update`, `retitle`, `repoint`, `move`, an `undo`, and the rest. Two per-key
    exceptions: a `realign`, which writes other keys, claims nothing here; and a later `relink`
    claims only the keys it wrote on that row and the keys it kept there, since a chain it wrote
    relies on the link it kept.
  - **Live** is `realign`'s test: not undone or rolled back, or non-terminal and naming the row,
    written flag or not. A row or conversation an operation has already put back (a relink row
    marked `restored`, a conversation `realign`'s `undo_status` marks undone) claims nothing.
  - **Fail closed, without crashing.** The claim check loads each operation's manifest on its
    own: one that cannot be read, or whose rows are damaged, is a claim on every row the check
    asks about. One bad manifest never aborts the check.
  - **What a claim does.** `undo` refuses as a whole, before writing, names the claimant to undo
    first, and the relink stays `completed`. `back` must terminate, so it leaves a claimed key and
    names it in the operation's `abort_reason`, as `realign`'s `back` names the rows it skips.
  - Changes by the app, with no operation behind them, never refuse (D8).
- D14: the one-row proof's candidate is chosen by a read-only script on the maintainer's side,
  from the rows' key shapes, and named in the run sheet by its row id. `relink` needs no shape
  classifier and no new output for it.

Settled after review round 1, my calls:
- D15: the journal holds no row images. A row's bytes average 135 KB, so 931 base64 images
  would make a manifest of about 167 MB (126 MB of row bytes), rewritten after every row: the
  failure `SYNC_JOURNAL_BYTE_BUDGET` was built against. Instead each row records the sha256 of
  the bytes it found and of the bytes it wrote, and the two keys' old and new values. That is
  everything the write check, the verify, `undo`, recovery and other operations' ownership checks
  need. The manifest is saved as rows land, under a byte budget like `sync`'s. A record coarser
  than the disk is harmless, because recovery reads the disk (below).
- D16: an unreadable row refuses the run, naming it, as `converge`'s scan does, when it is a
  candidate, a holder or a target, a kept key's target included: a guess there could link the
  wrong row or miss a disagreement.
- D17: one store per account. An account holding rows in more than one org folder is refused, as
  `converge` refuses it, and each journaled row carries its `store_path`, checked at write time
  as `realign` checks it.

Settled after review round 2, my calls:
- D18: `relink` and its `--apply` refuse while any operation is non-terminal, and its `undo` and
  its recovery while any other one is, naming it, to be recovered first. An interrupted
  `converge` or `sync` rolled back after a relink would delete a parent row a new link names, and
  one rolled forward would meet rows whose bytes a relink changed. `plan_realign` holds
  conversations an open operation involves for the same reason; `relink` holds the whole run,
  which is simpler and costs only a `recover` first.
- D19: every per-row pass checks identity first: a row whose `cliSessionId` is not the journaled
  conversation is named and skipped, by forward and back recovery and by `undo`, as `realign`'s
  undo does, whether or not its write had landed. A landed link on a row repointed since is then
  left on it, and named.

## Design

### What a run plans

For each conversation C that some account's row links with a cleared link:

1. **Agreement (D12).** Judge C's links across every account. Disagreement leaves every row of
   C out, with 0.18.0's reason. Otherwise, check each row's kept link or chain; one that does
   not agree leaves that row out, reported.
2. **Candidacy.** In each account B, each row R of C is a **link candidate** when it has no
   `spawnedFrom` object (D5), and a **chain candidate** when it has no `startChain` object and a
   holder carries one. An account can hold more than one row of C; each is its own candidate,
   and a run without `--only` repairs each of them.
3. **Resolution.** The holders are C's rows in the accounts other than B, as `converge` defines
   them for a new row, and the destination is B's existing rows; nothing is planned into B.
   `_lineage_resolve` gives the link and chain R should carry. Its rules apply unchanged (D7): a
   cleared link shape; holders agreeing on the parent and on `taskId`; the parent's transcript
   present; B holding a row for the parent conversation, picked by the 0.18.0 pick rule when
   there are several; not the conversation itself. A link or chain it leaves out keeps its
   reason code. For a chain candidate that keeps its own link, only the resolver's chain is
   used, and the kept link stands.
4. **Provenance (D11).** A resolved link is kept only when an app-written link backs it;
   otherwise it is left out (`lineage_variant`, with a detail saying no app-written holder), and
   its chain with it (D6).
5. **Cycles.** Per account, `_lineage_drop_cycles` over the proposed edges and B's existing
   edges (`_lineage_existing_edges`), as for `converge`, in its fixed order (by child
   conversation, then row). The existing graph is the links on disk, every row's, whatever D12
   or candidacy decided about it; a kept link is an existing edge, never a proposed one. A
   proposed edge that would close a cycle is left out (`lineage_cycle`), its chain with it. The
   existing-cycle count is reported once.
6. **The new value.** R's value with the resolved candidate keys set, and nothing else touched:
   a key that is not written keeps whatever it holds, a null included. A key absent from R is
   appended; a non-object is replaced where it stands. (`_lineage_rewrite_post` pops a key
   given `None`, so the writer sets keys one by one, as `_realign_set` does, rather than
   passing the resolver's pair.)

The plan holds two lists, the rows to write and the rows left out, each row with its `account`,
`label`, `name`, `title` and codes. Only the first is written to; both are reported.

### What it prints

The dry run lists, per account, the rows it would change, then the totals. Fake cast:

```
-> bob@example.com (cccc1111/dddd2222)
   9f3c2a10  'ACME-REVIEW side task'   link under 'Northwind kickoff'
   41be7d02  'Northwind follow-up'     link and chain

links   : 12 to add, 3 left out (holders disagree on the parent 2, the row's own link names no usable row 1)
chains  : 2 to add
cycles  : 1 already present, left as they are

dry run - pass --apply to add them
```

- When the identity files disagree, the dry run says so before anything else, in `converge`'s
  words, and `--json` carries it as `identity_disagreement`, as `converge` has since 0.13.0. A
  plan that `--apply` would refuse never reads as clean.
- The counts are rows, and they count what is written, from the plan's new keys, plus the
  left-out list; not `_lineage_summary` over the written rows' records, which also name a kept
  key. A chain code (`lineage_chain_dropped`, `own_chain_differs`) is counted on the `chains`
  line, never on `links`. Each part prints only when its count is not zero, the most frequent
  reason first, in `converge`'s wording plus the two D12 phrases.
- A listed row whose link is added but whose chain was left out carries `[chain: <reason>]`.
  A row that gains nothing is listed only under `--verbose`, with its `[link: <reason>]` or
  `[chain: <reason>]` tag (D10).
- `--json` prints the plan through a public projection, `_public_relink_manifest`, as
  `_public_realign_manifest` does for `realign`: it omits `old`, `new` and `kept`, whose link
  objects can carry titles. Under `--anonymize` it carries no hashes or paths, keeps `account`
  shortened and scrubs `label` as `realign`'s report does, hides the `--only` and `--account`
  values as `--only` is hidden today, and scrubs every title: the row's under `title`, the
  parent's under a key added to `_ANON_FIELDS`. Every emitted key is registered in
  `_ANON_STRUCTURAL_KEYS`.
- Nothing to add prints `nothing to add` and journals nothing, exit 0, as `converge` does with
  nothing to create.

### Options

`relink [--only TITLE_OR_ID] [--account SUBSTRING] [--live SUBSTRING] [--apply] [--json] [--verbose] [--anonymize]`

- `--only` narrows to one conversation, by a title substring or a `cliSessionId` prefix that
  must resolve to exactly one conversation, as `realign --only` does; or to one row, by a row id
  (`local_` and a prefix of its uuid) that must resolve to exactly one row. No match, or more
  than one, refuses; a match with no candidate prints `nothing to add`.
- `--account` narrows to one account's store, matched against the stores D17 resolves, the
  signed-in account's included (not as `sync --to` matches, which leaves the live account out and
  also matches empty org folders): an account id, org id, store path or email substring that must
  resolve to one store. With a conversation `--only`, a run that still has more than one candidate
  row refuses and asks for the row id, so a proof never touches two rows.
- `--live` asserts the app's account when the identity files disagree (RULING 5), at account
  scope as for `converge`.

### Applying

- `_guard_mutation`, as `realign` and `converge` call it: the app fully closed and the
  account identity settled. Then D18: no operation may be non-terminal.
- Under the lock the plan runs again against the store as it then stands, and that plan is
  what is journaled. A row that changed between the pre-lock plan and the lock is simply planned
  afresh; what changed between the two, in the same invocation, is said in the last lines, as
  `converge` does (`since the plan: ... changed, ... gained, ... dropped`).
- Each row: read it, and check its `store_path` and its conversation (D19). If its bytes'
  sha256 is not the journaled one, refuse and leave the operation at `writing`, as `realign`
  does: the row changed after the locked plan. Otherwise write atomically and verify by value:
  the row on disk must be the found value with exactly the written keys set.
- The writer bound, as for every mutating command (internals, "The writer bound"): the guard
  refuses while the app runs, and the lock keeps other tool runs out. An app started during
  the apply could write a row between `relink`'s read and its replace, and that write would be
  lost without any check seeing it. The guarantee holds while the app stays closed, and that is
  the instruction the run sheet gives.
- The journal (`op_type: "relink"`, `lineage_format: 1`) holds:
  - per row to write: `account`, `label`, `name`, `dest_path`, `store_path`, `session_id`,
    `title`, `parent_title`; `pre_sha256` and `post_sha256`; `old`, for each key written,
    `{present, value}` as found; `new`, for each key written, the value written; `kept`, for a
    key the row already had as an object, its value; `lineage`, the record of D12, in `converge`'s
    journal shape; and the status fields, named as `realign`'s rows name theirs so the generic
    readers see them: a boolean `written`, a `skipped` reason, and, during an undo, a boolean
    `restored`;
  - the left-out list, and the `since the plan` counts.

### `undo` (D8, D13, D18, D19)

1. D18, then D13's check, before anything is written. A claim refuses the whole undo, names the
   claimant, and leaves the relink `completed`.
2. For each row marked `written` and not `restored`, newest operation first as usual:
   - a row that is gone, or now opens another conversation (D19), is named and skipped;
   - every key that still holds its `new` value goes back to `old`: popped when it was absent,
     the exact old value restored when it was a non-object. Every other key stays as the app now
     has it;
   - a key that holds its `old` value already counts as restored: an interrupted undo may have
     put it back before saving its mark;
   - a key that holds neither, because the app changed it, is left, and named in a `left:` line;
   - the row is marked `restored`, and the manifest saved, as the undo goes.
3. An unreadable row, or a write that fails, refuses with the reason; the relink stays
   `completed`, and `undo --apply` again resumes it. Rows marked `restored` are passed over and
   claim nothing, in this undo's checks and in every other operation's.
4. The operation ends `undone`. Its `left:` list is kept on the manifest and printed by the
   undo. Taking out a link cannot leave another link dangling, so there is no link report.

The app is measured to keep a carried link exactly as written across a reopen (the 0.18.0
acceptance check, 20 of 20 links). An app that normalized one would make its key "changed", so
`undo` would leave it and say so; it never fails silently.

### Recovery

Recovery reads the disk, not the flags alone, and never re-resolves. D18 holds it while any
other operation is non-terminal. Each row first passes D19.
- **forward**, per row not yet marked `written`, in journal order, by what each written key
  holds now: its `new` value, its `old` value, or something else:
  1. every key at `new`: the row landed, and the app may have written other keys since. It is
     marked `written`, before anything else is checked;
  2. some keys at `new` and the rest at something else, none at `old`: it landed, and the app
     changed a key since. It is marked `written`; `undo` will restore the keys still at `new` and
     name the rest;
  3. the bytes still match `pre_sha256`: the row is pending. It is re-checked against its
     journaled targets, for the keys it writes only (`_lineage_dependency_skip`), and against the
     account's current on-disk edges for a cycle, then written, or marked `skipped` with the
     reason, and named;
  4. anything else, a key at `old` beside one at `new` included: the row changed after the plan
     in a way recovery cannot attribute. It is marked `skipped` (`changed since the plan`),
     named, and nothing is written or later undone on it.

  Then the 0.18.0 link report for forward recovery (`_link_report(..., forward=True)`) runs
  over the written rows, read-only, and its dangling and redirected entries are printed and kept
  on the manifest: a link that landed and whose parent's or root's row has gone, or now opens
  another conversation, is named, not silently accepted.

  A value another operation wrote, identical to one `relink` planned, is adopted as landed only in
  the window D18 cannot close; that operation then claims the row (D13), so `undo` refuses rather
  than take its work.
- **back** restores each landed row's keys as `undo` does, rows marked `written` and rows the
  forward rules above would recognise as landed alike, and always terminates. A key a later
  operation claims (D13), or that the app changed, is left, and named in `abort_reason`, as
  `realign`'s `back` names the rows it skips. `rollback_residue` is not used: it is kept for
  unresolved stores, and `doctor` reports it with advice to delete a session, which is wrong for a
  row `relink` only edited.

### Integration (what must learn the new op type)

The builder must extend each of these:
- every function in `tests/test_realign.py::_OP_TYPE_BRANCHES`, all 23: `rotate_ops`,
  `classify_op`, `recover_op`, `gather_doctor`, `cmd_undo`, `undo_realign`, `_collides`,
  `plan_realign`, `_release_candidates`, `_journal_rows`, `_unresolved_rows`,
  `_realign_claimed_later`, `plan_undo`, `undo_new_row`, `undo_sync`, `undo_repoint`,
  `undo_retitle`, `undo_converge`, `_sync_paths_claimed_elsewhere`, `_live_override_note`,
  `_lineage_row_store`, `cmd_recover`, and the window's `_find_undoable_op`. Each either handles
  `relink` or says why not. That test only notices a new branching function, not a missing arm,
  so the builder also adds a `relink` column to it: each function names `relink`, or carries its
  reason, as realign's column does;
- the journaled-path readers in `tests/test_journal_reroot.py::_ROW_READERS`, with a test that a
  relink journal is recovered and undone after the data root moves;
- the ownership checks other operations run:
  - `_retitle_claimed_elsewhere` reads a written row with no `post_b64` as a claim, with no
    ordering. It must compare a relink row by `post_sha256`, and pass over a relink earlier than
    the retitle and any row marked `restored`. Otherwise an earlier relink and a later retitle that
    left the same bytes would each wait on the other forever;
  - `_repoint_claimed_later` counts any terminal operation that wrote the row, whatever its
    order, undone ones included. It must count only a relink later than the repoint, not undone,
    and pass over restored rows, or an earlier relink would block a repoint's rollback as it would
    a retitle's undo;
  - `_sync_paths_claimed_elsewhere` reads `sync` ops only and needs no change, since `sync`'s
    undo already refuses a drifted row;
  - `_realign_claimed_later` must apply D13's per-key exception;
- `_relink_shape_error`, over every journal field above, the `lineage` record checked against
  `new` and `kept` rather than against bytes, an unjudged kept chain outside it. It is called at
  the top of `classify_relink_op`, `undo_relink` and recovery's relink arms, as
  `_sync_shape_error` is for `sync`, not only from the shape table, which is consulted only for
  ops in an unresolved store;
- the phrase and left-out tables pinned by
  `tests/test_lineage.py::test_note_codes_phrases_and_left_out_set_match_the_table_exactly`;
- `doctor`, which reads relink journals like any other op's;
- the public projection, `_ANON_FIELDS` for the parent title, and `_ANON_STRUCTURAL_KEYS`.

Other operations meet relink's rows this way, and need no other change:
- `converge`'s undo deletes a row it created unless its `cliSessionId` or title changed
  (`undo_converge`), which `relink` never changes, so a relinked row is deleted, link and all,
  and relink's later undo finds it gone and names it;
- `sync`'s undo reads a relinked row as drifted and refuses;
- a later `sync --update` replaces the row whole (L4) and may drop a relinked link, which its
  change line reports.

While a relink is the latest operation, the window offers no undo, as for `realign`, so a
`converge` before it is undone from the CLI.

### Proving it before the bulk run (D2, as amended)

The release's run sheet, by the maintainer, with the app closed for each apply:
1. A dry run of all of it, with `--verbose`, so every row and every left-out row is listed.
2. `relink --only <row id> --apply` on one row shaped like the app's own: many keys beyond
   `converge`'s. Its account must be one the maintainer can open.
3. Open the app there and look: the child under its parent. Quit, reopen, look again. Then the
   row is read back: its link and chain still as written, and only the app's ordinary keys
   added.
4. The same, one row at a time, for one row each that is: (a) a chain gap; (b) an archived
   child; (c) a child whose parent row in that account is archived; (d) an app row whose link
   holders carry `sharedFolder`. A kind with no candidate today is recorded as untested.
5. Only then `relink --apply` for the rest, and a look at a handful, of both shapes.

Stop at any step on a hidden child, a dropped link, or any key changed besides the app's
ordinary ones: `undo --apply` takes the step out, and the design comes back for review.

## Tests (synthetic, fake cast)

- Plan: a missing link resolved, with its chain; a null, false or string link treated as
  missing and replaced in place; an existing link object never replaced; a kept agreeing link
  beside a chain gap gains the chain, the kept link counting for D11; a kept link with the wrong
  parent or `taskId` is the conversation's disagreement, every row of it left out under
  `lineage_parent_disagree` or `lineage_task_disagree`; a kept empty object, or a kept link naming
  a row in another account, is `own_link_differs`; a kept chain with no holder chain stands,
  outside the record; a kept chain with the wrong root or `depth` is `own_chain_differs` on the
  chains line; a link left out drops its chain; two rows of one conversation in one account are
  each repaired; archived rows included; every 0.18.0 left-out reason reaching the plan; cycles
  dropped against existing edges, a kept link counted as existing, an excluded row's link still in
  the graph; `--only` by conversation and by row id, `--account` including the signed-in account,
  refusing no match, an ambiguous one, or two candidate rows under a conversation `--only`, and
  `nothing to add`.
- D11: a link whose yielding holders are only this tool's copies is left out, with its chain,
  also when a tool copy carries a chain; one app-written yielding holder is enough; a
  `{sessionId, taskId, title}` with an empty title is not app-written.
- Value: the new value is the old one with only the written keys set, order kept; a key not
  written keeps its null; a row with many app keys keeps all of them.
- Output: the identity-disagreement line and `identity_disagreement`; counts from written keys and
  the left-out list, a kept link not counted as added; a chain code on the chains line only; the
  15-row cap and `--verbose`; the public projection without `old`, `new` or `kept`, under `--json`
  and `--anonymize`, the parent title, `--only` and `--account` hidden; the two new phrases.
- Apply: D18 refuses with an open operation; a row changed after the locked plan refuses and
  leaves the op at `writing`; the `store_path` and identity checks; value-identity verify; the
  apply-time re-plan and its `since the plan` line; nothing journaled when nothing is added; an
  unreadable candidate, holder, target or kept key's target refuses (D16); an account in two org
  folders refuses (D17).
- Journal: no images; the manifest stays small at 931 rows (a size bound, asserted); saves
  under the budget; `post_sha256` matches the written bytes; `written` and `restored` are
  booleans.
- Undo: restores per key; keeps the app's later keys; leaves and names a key the app changed; a
  missing or repointed row skipped and named; a non-object restored exactly; a crash after a key
  is put back but before its mark is saved, then a resumed undo, reports no false `left:` line;
  an unreadable row refuses and leaves the relink resumable.
- D13: a later live `sync --update`, `retitle` or `repoint` of a relinked row refuses the undo
  and names it; a later relink that wrote or kept the same key refuses it; a later relink of the
  other key only, that kept nothing here, does not; a later realign does not; a non-terminal later
  op naming the row refuses; a manifest that cannot be read, or with damaged rows, refuses without
  crashing the check; a row already `restored` claims nothing; `back` leaves a claimed key, names
  it in `abort_reason`, and terminates.
- Interop: an earlier relink and a later retitle leaving the same bytes are each undoable in turn;
  a later relink blocks a repoint's undo and a restored one does not; a realign undo after a
  relink of the same row succeeds.
- Recovery: D18 holds it while another operation is open. Forward: every key at `new` landed;
  some at `new` and one changed landed; a key at `old` beside one at `new` skipped; a pending row
  whose parent's row is gone skipped and named; a landed row whose parent's row is gone since
  named by the link report; a pending edge that now closes a cycle skipped; a row changed after
  the plan skipped; a repointed row skipped, landed or not. Back: terminates, naming what it left
  in `abort_reason`, and leaves no `rollback_residue` for an existing row.
- Shape checks: damaged `old`, `new`, `kept`, `lineage`, hashes, titles and status fields each
  refuse, through `classify`, `undo` and recovery alike; a link-only row that keeps an unjudged
  chain passes.
- Integration: the inventory test with its `relink` column, `_ROW_READERS` and the phrase-table
  pin updated; a relink journal recovered and undone after a data root move.

## Review focus

- An app-written row whose other keys contradict the link, a `sharedFolder` naming another
  folder, say. Only a look in the app can tell whether the app minds.
- A parent row that is archived in B. The resolver's pick prefers an unarchived row but accepts
  an archived one; what the app shows then is unmeasured.
- The app starting during an apply (the writer bound, above).
- A key the app normalizes after `relink` writes it, which `undo` would then leave.
- Round 3's fixes, which no review round has read.

## Revisions

- **Review round 1 (2026-11-19; Codex, agy, DeepSeek reported; Kimi INCOMPLETE, a complete
  review whose final line lacked the canary).** Applied:
  - D13 made per row and key, "live" as realign's test, and claim reading fail-closed (all four).
  - D6 replaced: the chain follows the link (DeepSeek, Kimi; agy and Codex on chain gating).
  - The writer sets keys one by one, so a key not written is never popped (DeepSeek, Kimi).
    Goal 2 says value identity, not bytes (DeepSeek, Kimi).
  - D15: no row images in the journal, after the manifest-size precedent (DeepSeek).
  - Recovery reads the disk; back terminates (all four).
  - Undo persists `restored` per row (agy, Kimi).
  - D12 made exact, with `taskId` (Codex, agy, DeepSeek); the two new codes added (DeepSeek).
  - D11's limits stated (Codex, agy, Kimi). D4 marked an assumption (Codex).
  - Counts from written keys (DeepSeek); units fixed (Kimi); a 15-row cap (DeepSeek); empty
    states (agy, Kimi).
  - D16, unreadable input refuses; D17, one store per account (Kimi, DeepSeek).
  - Integration points listed (Kimi, DeepSeek); the public projection (Kimi).
  - The writer bound stated (Codex, DeepSeek). Re-plan and refusal told apart (agy).
  - The wider proof put to the maintainer, not applied (all four). *(He took it, 12:32: D2 is
    amended and the run sheet widened.)*
  - Rejected: a `doctor` check (agy), since D1 chose a command run on purpose; D9's exit 3
    (DeepSeek), reasoned in D9; agy's split-target duplicate rows, since a kept key is compared
    by conversation. The rejection of agy's point that D11's shapes were undefined is withdrawn
    in round 2: it was right, `_lineage_variant_ok` also clears the tool's own shape.
- **Review round 2 (2026-11-19; Codex, Kimi, DeepSeek reported; agy reported on its one retry
  after a `command` denial).** Applied:
  - D11 given its own predicate, the two app shapes only (DeepSeek, agy); a D11 failure drops
    the chain too (agy).
  - D12 judged across the whole conversation; a kept chain judged only when the holders carry
    one, with `depth` (agy, DeepSeek). `own_chain_differs` moved to the chains tally (DeepSeek,
    agy); `carried_keys` for a kept key set (DeepSeek).
  - D13: every whole-row writer claims, `retitle` and `repoint` included; a later relink claims
    the keys it kept as well as those it wrote; restored rows claim nothing (agy, Kimi). `back`
    leaves claimed keys rather than ignoring claims (Codex, agy).
  - D18, no run or undo while an operation is open (Kimi, agy); D19, identity checks (Kimi).
  - Forward recovery recognizes a landed row by its keys (Codex).
  - Undo: a key already back at `old` counts as restored; unreadable rows and failed writes
    refuse, resumably; the `left:` list printed by undo (agy).
  - `post_sha256` per row, and other operations' ownership checks taught to compare it (agy,
    verified against `_retitle_claimed_elsewhere`).
  - `--account`; the run sheet's dry run uses `--verbose` (agy, DeepSeek).
  - The manifest's left-out list and `since the plan` counts; titles in the journal (agy).
  - `_relink_shape_error`'s call sites; the phrase-table pin (DeepSeek). D15's figure corrected
    (DeepSeek).
  - Checked and kept: `undo_converge` deletes a created row unless its `cliSessionId` or title
    changed, which `relink` never changes (DeepSeek asked; the docstring says so).
- **Review round 3, the last (2026-11-19; all four reported; Codex and DeepSeek "Blockers:
  none").** Applied, **not re-reviewed**:
  - Forward recovery classifies each key as `new`, `old` or changed, adopts a row only when no
    key is back at `old`, and skips the rest unattributed (Kimi, agy); then runs 0.18.0's
    forward link report, so a landed link whose parent's row has gone is named (Codex).
  - D18 extended to `undo` and recovery while any other operation is open (Kimi).
  - The cycle graph is the links on disk, whatever D12 decided; a kept link is an existing edge
    (Kimi, agy).
  - `--only` takes a row id, and a conversation `--only` refuses on two candidate rows; an
    account's several rows of one conversation are each repaired (Kimi, agy).
  - `--account` matches D17's stores, the signed-in account's included, not as `sync --to` does
    (agy, DeepSeek).
  - D12: a wrong parent or `taskId` is the conversation's disagreement, so `own_link_differs` is
    kept for a link naming no usable row, and an unreadable target refuses under D16 (agy, Kimi).
    An unjudged kept chain stays out of the record; `carried_keys` and `dropped_keys` follow
    `sync`'s rules (agy).
  - D11 counts the row's own kept app-written link for a chain-only repair (agy).
  - `back` names what it leaves in `abort_reason`, as `realign`'s does, never in
    `rollback_residue`, which `doctor` reports with advice to delete a session (agy, verified).
    DeepSeek's suggestion to use `rollback_residue` is rejected for that reason.
  - Status fields named as `realign`'s, booleans, so generic readers see them (agy, DeepSeek).
  - Written and left-out rows both carry `account` and `label`; the projection omits `old`,
    `new` and `kept`, and hides `--account` under `--anonymize` (agy).
  - The claim check loads each manifest on its own, fail-closed without crashing (agy).
  - The ownership-check integration made exact per function, including the deadlock between an
    earlier relink and a later retitle (agy).
  - All 23 dispatch functions named, and a `relink` column added to their test, which as it stands
    cannot catch a missing arm (DeepSeek).
  - The dry run discloses an identity disagreement, as `converge` has since 0.13.0 (DeepSeek).
  - The 42 stated as an upper bound (DeepSeek).

## Left to the builder

- Function names, the exact wording of the two new phrases and their positions in
  `LINEAGE_PHRASES` (which decide the `links :` tie-break), and the `--json` field names beyond
  the journal's.
- Where `relink` sits in the help and the README, and its README section, written to the same
  standard as `realign`'s.
- Whether the plan and apply share code with `converge`'s lineage pass beyond
  `_lineage_resolve`, `_lineage_drop_cycles` and the existing-edge graph.
- The journal byte budget's size, and how often the manifest is saved within it.
- Sequencing with the `sync` decode-once change in flight on another branch: both touch the
  lineage indexing; whichever lands second rebases.
