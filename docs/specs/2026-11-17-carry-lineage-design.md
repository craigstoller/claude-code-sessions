# Carry a side session's link to its parent across accounts (design)

Status: **approved by the maintainer on 2026-11-17, Phase 0 first; reviewed in three rounds.
Phase 0 ran on 2026-11-18, and the maintainer ruled L1 and L5 on its results the same day. This
revision follows them. It went through a fourth review of three rounds, and round 3's fixes have
not been re-reviewed. The maintainer ruled the two items it left open, L7 and L5b, the same
day ("Decisions"). L7's probe ran that evening and cleared the older link variant.** Scope ruled on 2026-11-17: rows
`converge` creates (and rows `sync` writes) carry the link now. Repairing rows that already exist
without it is a later, separate change. Ruled:
- L1, from Phase 0: carry `{sessionId, taskId}`;
- L5: accept and report;
- L6: as recommended;
- L2 to L4: as recommended, with the design.

Fake cast only: Northwind, ACME-REVIEW, alice@ and bob@example.com, the `u` user path.

## The problem in one example

In alice@'s sidebar, the conversation "ACME-REVIEW plan" (P) started a side session, "ACME-REVIEW
build" (C). The app wrote C's row with:

```json
"spawnedFrom": {"sessionId": "local_a1…", "taskId": "task_7f…", "title": "ACME-REVIEW build",
                "sideSession": true, "sharedFolder": true, "name": "acme-review-build"}
```

`local_a1…` is the `sessionId`, which is also the filename stem, of **P's row in alice@'s
store**. bob@ has a row for P, `local_b2…`, but none for C. `converge` gives bob@ a row for C.
That row is built from a fixed template that omits `spawnedFrom`
(`docs/specs/2026-08-22-new-row-design.md`, "Field derivation"). So in bob@'s sidebar C opens
with its full history but does not show as P's side session.

Copying the field as is would not help: bob@ has no row named `local_a1…`. The link has to be
**translated** to bob@'s row for P, `local_b2…`.

## What this rests on

| Claim | Basis |
|---|---|
| `spawnedFrom.sessionId` names the parent's row by its `sessionId` (= filename stem) **in the same account** | measured 2026-11-17 on the maintainer's store: 734 of 2,985 rows carry a non-empty `spawnedFrom`, and every one resolves to a row in its own account; every row's `sessionId` equals its filename stem |
| `startChain.firstSessionId` names the chain's root row the same way | same census: all 29 rows carrying `startChain` resolve in their own account. Phase 0's inventory (2026-11-18): every one has exactly two keys, `firstSessionId` (a string) and `depth` (an integer) |
| Keys seen inside `spawnedFrom` | same census: `sessionId`, `taskId`, `title` on all; `sideSession`, `name` on 204; `sharedFolder` 49; `wakeParent` 20; `pendingPop` 6; `sharedCheckout` 5; `userFork` 3. So links come in more than one variant (side sessions, forks, links with neither marker), and frequency is not meaning. On 2026-11-17 no key but `sessionId` had a measured meaning; since Phase 0, `taskId` has one too: the app needs it to nest the child. The other keys' meanings are still unmeasured |
| The app nests a side session under its parent using `spawnedFrom.sessionId` **and `taskId`** | **measured 2026-11-18 (Phase 0), on one side session's `converge`-made row:** `sessionId` alone **hid the child** from the sidebar, which is worse than no link. So did `sessionId` with `title`, with `sideSession`, or with `name`. `sessionId` with `taskId` nested it cleanly, with the app keeping the link as written and nothing else changed. In that account the parent's row and the chain root's row had been written by the same `converge` operation as the child's, so this is the case of a parent row `converge` creates in the same run. An earlier run on a second side session also saw `sessionId` alone hide the child. *(The 2026-11-17 version of this row said "`sessionId`, inferred"; the measurement supersedes it.)* |
| The app tolerates a link whose parent row is gone | **measured 2026-11-18 (Phase 0, Trial D):** with that link in place and the parent's row set aside, the child was listed on its own and opened, with nothing else changed |
| A translated `startChain` is kept | **measured 2026-11-18 (Phase 0, Trial C):** the carried link plus `{"firstSessionId": <the root's row in that account>, "depth": <the source depth>}` nested the child cleanly, and the app kept both as written |
| `sync` keeps the source row's filename, so a synced row has the same `sessionId` in both accounts | code: `plan_sync` writes `cand["name"]` |
| `sync` copies `spawnedFrom` verbatim today | code: `transform_row` copies the row less `SYNC_STRIP` and `SYNC_RESET`, and neither lists it. The census finds no foreign link in the store today |
| `plan_sync` decides "unchanged" before it removes rows | code: the `pre == blob` test is inside the row-building loop; the `held_orphan` removal (`rows = kept`) comes after it |
| "A's store" is one directory per account | code: `_converge_destinations` refuses when an account holds rows in more than one org directory |
| Writes happen only with the app closed | code: every mutating command runs the running-app guard (RULING 4) before it writes; `converge` and `sync` run it before the op is journaled, `repoint` inside its execution step. The operation lock coordinates this tool only, not the app |
| Recovery re-enters the apply path | code: `converge`'s forward recovery re-runs `_converge_recheck` (a "fresh re-evaluation") unless every pending row's bytes already match; `run_sync` calls `new_op` before `execute_sync_op`, which recovery re-enters |
| **A defect in the shipped tool:** in a mixed recovery, a landed `converge` row is lost to `undo` | code: when some pending rows match and some do not, forward recovery runs `_converge_recheck`, which marks a row `already_present` whenever its conversation has a row in that account, its own landed file included, without comparing bytes; the row stays `written: false`, and `undo_converge` skips every such row. It needs a hard kill between a write and its manifest save |

## Goals, non-goals, anti-goals

- **Goal:** a row `converge` creates for a side session shows it under its parent wherever that
  account also has a row for the parent, including a parent row created in the same run. The
  exceptions are the lineage pass's drop rules, each reported with its reason code. Among them:
  - the parent's own pair is held in the same plan (`parent_pair_held`); only the later repair
    change can add that link;
  - holders disagree on the parent or on `taskId`;
  - no holder carries a cleared variant with a `taskId`;
  - the parent's transcript is gone;
  - the link would close a cycle.
- **Goal:** neither command **decides** to write a link that names a row which, in the account's
  state after the op as planned, will not exist or will open another conversation. Scoped
  exceptions, stated rather than hidden: `sync --verbatim` (an exact copy by request, documented
  in its help); and links that something after the decision leaves dangling: the app, the user,
  an `undo`, or a target deleted in the window between a crash and its recovery.
- **Non-goals:** repairing existing rows that lack a link (the next change, on the update path
  `realign` uses; it inherits Phase 0's finding that a link must carry `taskId`, and any other
  shape it would write needs a trial first); `new-row`, which has no source row and keeps omitting the field; anything
  the parent row carries about its children (`queuedSideSessionNotes`,
  `resolvedBackgroundTaskSuggestions` and similar), which is per-account delivery state; keeping
  links current after a later `repoint`, or a later `sync --update`, changes which conversation a
  parent row opens (both can already do that to app-written links).
- **Anti-goals:** a link that hides the child (one without `taskId`, measured in Phase 0); a link
  to the wrong parent conversation; a self-link or a cycle; copying state
  that could make the app act (wake a parent, pop a window, share a folder) in an account where
  nothing happened; asserting a kind of relationship nobody measured (a fork carried as a side
  session); changing which transcript any row opens; rewriting a journaled image during recovery;
  writing, during recovery, a child whose planned parent row will not be written; a run whose
  output says links were carried when they were dropped.

## Approaches

1. **Resolve once, under the lock, before the op is journaled (chosen).** A lineage pass computes
   each new row's link from the store as it stands and the op's own planned rows. It runs when
   planning (for the dry run) and once more under the lock immediately before the op is journaled,
   where its result is final: the journal holds the exact bytes, and recovery never resolves again.
2. **Write the rows, then patch the links in.** Rejected: every linked row is written twice and
   needs the pre-image and undo machinery for edits, which belongs to the later repair change.
3. **Copy the link verbatim.** Rejected: the id names a row that does not exist in the other
   account.

## Phase 0 — measure before building

Every engine of the first review round found the same gap, and the maintainer's own rule says it:
a design that rests on how the app behaves runs a probe built to fail first. Nothing in "Design"
is implemented until this has run.

On the maintainer's machine, with the app closed, a hand-run probe (journaled pre-images, an
`undo`, the running-app guard, writes confined to the probe's own rows, kept outside this public
repository) takes one real side session whose link lives in one account and which has, in a
second account, rows for both itself and its parent, **its own row there made by `converge`** (the
row shape this design writes). In that second account, one trial at a time:

1. **Trial A, the minimal link:** `spawnedFrom` set to `{"sessionId": <the parent's row id in
   that account>}` only.
2. **Trial A+k, only if A does not nest the child:** A plus one key `k` of `taskId`, `title`,
   `sideSession`, `name`, `sharedFolder`, one trial per key, so a result names the key that caused
   it.
3. **Trial C, `startChain`,** when a chain of depth 2 or more exists: the translated
   `firstSessionId` and the source `depth`.
4. **Trial D, a dangling link:** with Trial A's link in place, the parent's row set aside (into
   the probe's journal), so the link names a row that is not there.

After each trial the maintainer opens the app in that account without typing, and the probe
records: whether the child appears under the parent (in D: whether it appears at all, and how);
whether the parent shows anything new (a badge, a task, a notification); whether opening either
session changes its folder or checkout; whether the app kept, normalized, rewrote or dropped the
field (read again after the app has been closed and reopened); and whether the parent's row
changed. Then `undo` restores every row byte for byte. The probe also inventories the keys and
value types of every `startChain` in the store.

**What it decides:**

- **The carried set (L1):** `{sessionId}` if Trial A nests the child with no side effect. Only if
  it does not, `{sessionId}` plus the one key whose Trial A+k does. A result seen once is a
  result, not proof of absence; a side effect seen in any trial drops that key for good.
- **`startChain`:** carried only if Trial C ran and passed, in the shape it passed with; otherwise
  never carried, and the plan says so.
- **Dangling links (L5):** if Trial D shows the child normally, a link left dangling by a later
  deletion is acceptable as designed; if the app hides the child, errors or misbehaves, `undo`'s
  stranding case is revisited before any code.
- **Which variants are carried:** only the link variant the probe measured (a side session,
  `sideSession: true`). Links of any other variant carry nothing, as today, with a note, until a
  further trial measures that variant. *(2026-11-18: a further trial, L7's, measured the older
  `{sessionId, taskId, title}` variant and cleared it. See "What it found".)*
- **Stop** if no trial nests the child: the app needs more than this field, and the design is
  revisited. Normalizing optional keys while keeping the parent passes; changing or losing the
  parent fails.

**What it found (2026-11-18).** Two runs, by the maintainer, each with a residue control and
`undo`, which restored both rows byte for byte each time. The probe and its records are kept
outside this repository.
- **The first run** stopped at Trial A: the minimal link hid the child from the sidebar, and the
  probe as first built treated that as a side effect.
- **The probe was then amended.** A clean hide goes on to the single-key trials, then a `taskId`
  + `title` pair, then all the keys together.
- **The second run**, on a different side session whose rows' working folders all existed, found:

| Trial | Link (beside the translated `sessionId`) | Child in the sidebar |
|---|---|---|
| A | nothing else | hidden |
| A+taskId | `taskId` | **nested, cleanly** |
| A+title, A+sideSession, A+name | that one key | hidden, each |
| C | `taskId`, plus the translated `startChain` | nested, cleanly |
| D | `taskId`, with the parent's row set aside | listed on its own, and it opens |

The pair and all-keys trials did not run, because a single key settled it. On every trial the app
kept the link as written, and nothing else changed on disk beyond what an ordinary open writes.
This is one candidate's result. The acceptance check is the second measurement.

**The older variant (L7, 2026-11-18).** A third run, on a side session whose source link is
exactly `{sessionId, taskId, title}` and whose child row a `converge` op still in the journal had
written:

| Trial | Link (beside the translated `sessionId`) | Child in the sidebar |
|---|---|---|
| A | nothing else | hidden |
| A+taskId | `taskId` | **nested, cleanly** |
| A+title | `title` | hidden |
| D | `taskId`, with the parent's row set aside | listed on its own, and it opens |

The same carried link, `{sessionId, taskId}`, nests this variant too, and a missing parent does
no harm. The candidate had no chain, so the chain is untested for this variant. This also rests on
one candidate.

## Design

### The lineage pass (shared by `converge` and `sync`)

`_synthesize_row` stays link-free (so `new-row` keeps its template exactly); the pass adds the
link to a row after synthesis, and the new-row design gets a dated note saying so. For each row
of conversation C that the op will write into destination account B, against B's **state after
the op as planned** (its existing rows, minus rows the op deletes or replaces, plus the op's
planned rows with their planned contents). Its other input is the plan's held pairs, so that it
can tell `parent_pair_held` from `parent_not_in_account`:

1. **Collect source links.** For every holder row of C outside B (for `sync`, the source row),
   take a `spawnedFrom` that is a dict whose `sessionId` is a string naming a row in the holder's
   store whose own `sessionId` equals its filename stem and whose `cliSessionId` is a non-empty
   string. That row's `cliSessionId` is the parent conversation P for that holder. Anything else
   (a non-dict, a non-string, an id that names no row, a mismatched stem) yields nothing for that
   holder. A link this tool wrote names a row in its own account, so it resolves here like an
   app-written one.
2. **Agree on one parent, never oneself, never a dead one.** Every holder that yielded a P counts
   here, whatever its link's variant. If they name more than one distinct P, or P is C, or P has
   no transcript on disk, the row carries no link and the pass records a note saying why. If no
   holder yields a P, there is no link (today's behaviour). **(L2)**
3. **Find a cleared template.** Among the holders that yielded P, the eligible ones are those
   whose link is a cleared variant and carries a `taskId` that is a non-empty string. Three shapes
   are cleared:
   - an app-written side-session link, with `sideSession: true` (the variant Phase 0 measured);
   - an app-written link that is exactly `{sessionId, taskId, title}`, with `title` a non-empty
     string (the older variant, cleared by L7's probe on 2026-11-18);
   - this tool's own output, a link that is exactly `{sessionId, taskId}`. The app never writes
     that shape, since all 734 links in the census carry `title`. The tool writes it only from a
     cleared source, so carrying it again approves no new variant. Without this clause, a link
     the tool wrote would be refused the next time it was a source: a second `converge` into a
     third account, a `sync` onward, or a `sync --update` back. The clause assumes the app leaves
     such a link as written, which Phase 0 measured (the app kept every link exactly as written).
     If the app later adds a key to one, that link stops being eligible, with the note
     `lineage_variant`. The acceptance check reads the link again after a reopen for this reason.

   The template holder is the eligible holder chosen by step 4's pick rule. With no eligible
   holder, the row carries no link: `lineage_variant` when no holder's link is a cleared variant,
   `lineage_no_task` when the cleared ones lack a usable `taskId`. A `sessionId`-only link hides
   the child, so there is never a link without `taskId`. Every holder whose link has a usable
   `taskId` (a non-empty string) must give the same value, eligible or not; otherwise no link,
   `lineage_task_disagree`. An empty, null or non-string `taskId` counts as absent, not as a
   disagreeing value (round 3, agy). That
   agreement rule is a reading of L2 that the maintainer has not ruled on separately. **(L1, L2)**
   It should rarely fire, which is my inference from where the value comes from. The app writes
   a side session's link only in the account where the side session started. Every other copy
   comes from this tool, carrying that same value. So holders disagree only when something
   unexpected happened, and then no link is the safe answer (round 3, DeepSeek).
4. **Find the parent's row in B's after-state.** Candidates are rows whose `cliSessionId` will be
   P and whose `sessionId` equals their filename stem. The pick, in order:
   - for `sync` only, the row with the same filename as the source parent row, existing or
     planned, since `sync` keeps filenames and that row is the one that pairs with the source;
   - a row whose `isArchived` is not `true` before an archived one. An archived parent is still
     a candidate when it is the only one, because the app itself leaves links to parents that
     are archived later. That is my inference from the census, which holds app-made children of
     archived parents; nesting under an archived parent was not measured;
   - an existing row before a planned one;
   - the greatest `lastActivityAt`, with missing, boolean or non-numeric values as zero
     (`_activity_of` returns `None` for those, and the pass reads `None` as zero);
   - the greatest filename.

   (Candidates all sit in B's one store folder, so the account id that `_converge_title` also
   compares never decides here.) Several candidates are normal: `converge` treats several rows of
   one conversation in one account as one holding. None means no link. The result is T. The same
   rule, without the `sync` clause, picks the template holder in step 3, across accounts, with
   the account id as the last tie-break. **(L2)**
   - **A drifted parent row is not P's row.** One row file can open different conversations in
     different accounts, as `gather_alignment` records. So B may hold the sidebar entry a
     holder's link names, under the same filename, while it opens another conversation. That row
     is not a candidate, and there is no link. The note is `parent_row_drifted`, not
     `parent_not_in_account`, so the report says why. This narrows Goal 1 on purpose: a link to a
     row that opens another conversation is the anti-goal (round 3, DeepSeek).
5. **Build the link:** exactly `{"sessionId": T, "taskId": <the template holder's taskId>}`, the
   carried set Phase 0 settled, with `taskId` copied unchanged. Every other key is dropped, and
   the drop is recorded: `title`, `sideSession`, `name`, `sharedFolder`, `wakeParent`,
   `pendingPop`, and any key this tool has not seen. **(L1)**
6. **`startChain`**, cleared by Phase 0's Trial C, is resolved independently of the link's
   template (round 3, agy). Its sources are the holders that carry a `startChain` dict.
   - **No holder carries one:** no chain, and no note. Most links have none; 29 of 734 rows carry
     a chain.
   - Each source's `firstSessionId` must resolve (stem-checked) to a conversation R. R must not be
     C, and R must have a transcript on disk.
   - `depth` must be an integer that is not a boolean, and at least 1.
   - All sources must agree on R and on `depth`.
   - The root's row in B is found by step 4's rule, applied to R. For `sync`, the filename it
     prefers is the source root row's, not the parent's. The root's row must not be the row being
     written for C.

   The chain written is exactly `{"firstSessionId": <the root's row in B>, "depth": <the agreed
   depth>}`, the only shape in the store and the one Trial C passed. When a source exists but
   any of these fails, `startChain` is omitted with the note `lineage_chain_dropped`. A dropped
   parent link also omits it.
7. **No cycles, over both kinds of edge.** Edges come from different conversations' holders, so a
   cycle can form in B that exists in no single account. C's link can come from alice@ and P's
   from bob@. A cycle can also mix a parent edge with a root edge: C's root is R, and R's parent
   is C.
   - After steps 1 to 6 for every row in the op, the pass builds B's after-state graph. It holds
     both edge kinds: the edges B's rows already carry, plus the proposed ones.
   - It adds every proposed parent edge first, then every proposed root edge, each in order of
     C's `cliSessionId` and then the row's filename. So a root edge can never evict a parent edge
     (round 3, agy).
   - A proposed edge that would close a cycle is dropped: `lineage_cycle` for a parent edge, which
     also drops that row's root edge, and `lineage_chain_dropped` for a root edge.

   What remains is acyclic over both kinds, which the write order needs. The write order ranks
   only the rows the op writes. A cycle already present among B's existing rows constrains
   nothing, because those rows are already written. The pass leaves such a cycle alone and
   reports it once, as `existing_cycle` (round 3, DeepSeek).
8. **Record it.** The manifest row gains `lineage`: `{"parent_session", "parent_row",
   "parent_planned", "root_session", "root_row", "root_planned", "carried_keys",
   "dropped_keys", "notes"}`, empty strings or lists where absent.
   - Lineage notes are a kind of their own, each with a reason code.
     - `parent_pair_held` (the parent's own pair was held in this plan) is kept apart from
       `parent_not_in_account`.
     - Other codes: `lineage_variant`, `lineage_no_task`, `lineage_task_disagree`,
       `lineage_chain_dropped` and `lineage_cycle`.
   - Every new key is registered in `_ANON_STRUCTURAL_KEYS`, so the `--anonymize` scrub never
     renames it.
   - The text report, the GUI's Level tab and the anonymized manifest each render lineage notes
     by a branch of their own. The existing title-disagreement rendering never sees them.
   - **Shape checks cover `lineage`, and old journals stay valid.**
     - Every `converge` and `sync` op this version journals records `lineage_format: 1` in its
       manifest. An op without that key was journaled before this change. Its rows need no
       `lineage`, and they are read as they always were, verbatim `sync` links included.
     - In an op with `lineage_format: 1`, a row with no `lineage` is valid only when its
       `post_b64` carries neither `spawnedFrom` nor `startChain`, or the op is `--verbatim`. A
       non-verbatim row whose image carries a link but has no usable `lineage` is a shape error.
       So a damaged journal cannot slip a link past recovery's checks (round 3, Kimi).
     - A `lineage`, or `lineage_planned`, that is present but not a dict, or a dict whose listed
       keys have the wrong types, is a shape error in `_converge_shape_error` and in the `sync`
       manifest's checks.
     - Every reader of either key (recovery, `undo`'s report, `recover`'s listing, `doctor`)
       reads it defensively. A damaged value is a shape error, never an exception, because
       `classify_op` must never raise.
     - (Corrected in round 2. Round 1's text made a missing `lineage` a shape error, which would
       have stranded every operation journaled before the upgrade (Codex, Kimi, DeepSeek).)

### `converge`

- **Planning:** the lineage pass runs after the plan's rows are final (holds and collisions
  removed) and every new row's id is minted. The plan report gains a lineage branch, so the dry
  run says what would be carried: `links   : 4 to carry, 1 left out (holders disagree)`.
- **Applying, once:** `_converge_recheck` makes its pair-skip decisions first. It then runs the
  lineage pass again, in full (source links, holder agreement, templates, targets, chains and
  the cycle check), against the store as it now is plus the rows still to be written. The
  recheck's own skips (`held_title_collision` among them) are the pass's held input, so a
  planned parent the recheck drops is reported by its real reason.
  - Where the result differs from the plan's, the row's `post_b64` and `lineage` are rewritten
    together. The difference is noted as one of:
    - `lineage_retargeted`;
    - `lineage_dropped` (the parent link);
    - `lineage_chain_dropped` (the root alone);
    - `lineage_gained`: a link or a chain the plan did not have, because a parent row or a holder
      link appeared since the dry run. The after-apply `links :` line counts it apart from the
      planned ones.

    The plan's values are kept as `lineage_planned`.
  - The row itself is never skipped over its link.
  - The rows are then re-sorted into write order, because a rewritten link can make a row depend
    on one that sorted after it.
  - All of this happens before `new_op`, so the journal records the final bytes and order.
    **(L3)**
- **Recovery, first fixing the shipped defect.** Before recovery runs in either direction, every
  pending row that has landed is marked written and saved. So a landed row is never reclassified
  as `already_present` going forward. Going back, it is removed or, if the app rewrote it,
  recorded as residue like any written row, and it stays within `undo`'s reach (the `back` half
  added in round 3, agy).
  - A row has landed when the file at its `dest_path` matches its `post_b64`.
  - It has also landed when that file's `sessionId` equals the minted filename stem
    (`_row_is_ours`'s test). The app may rewrite a landed row before recovery runs, and no other
    writer mints that id.
  - This is a fix to the current tool, needed with or without the link, and it is tested on its
    own.
- **Recovery never re-resolves, and never writes an orphan.** The recheck then runs without the
  lineage pass, so every row keeps its journaled `post_b64`.
  - The skips are evaluated in write order. A pending row is skipped as `parent_not_written` when
    its journaled link or chain names a planned row that recovery skips for any reason:
    `already_present`, `transcript_gone`, a collision, or `parent_not_written` itself. So a skip
    carries down a planned chain.
  - **A written dependency is re-checked, not re-resolved** (round 2, Kimi). Before writing a
    pending row whose journaled link or chain names a row that is already written or already
    existed, recovery checks two things:
    - that row still exists;
    - its `cliSessionId` still equals the journaled `parent_session` (or `root_session`).

    If either fails (the app or a `repoint` changed or removed the row since the crash), the
    pending row is skipped as `parent_changed`. Nothing is resolved again, and no row is written
    with a link to the wrong conversation.
  - The skipped row stays short for the next `converge`, which plans it afresh with a link to
    whatever row exists then. **(L6)**
  - A target deleted from the store in the crash window can still leave a link dangling, on a
    child that landed before the crash. So forward recovery ends with the same link report as a
    reversal, over the op's written rows, and that report names it (the goal's stated
    exception). It also runs when every row had already landed, which today is the
    journal-only path that skips the store (round 3, agy).
- **Write order:** parents and roots first, then the rows that link to them (possible because the
  pass leaves the graph acyclic), so an interrupted run can leave a parent without its child,
  never a child naming a row not yet written.
- **Reporting:** after the apply, a line next to `skipped :`, for example
  `links   : 3 carried, 1 retargeted, 1 dropped (parent's row gone)`; `--verbose` lists them.

### `sync`

- **Restructured planning.** `plan_sync` today decides "unchanged" inside the row-building loop,
  before its removals. The change splits that loop into this order:
  1. Build every candidate's transformed bytes, without a link.
  2. Apply the age holds of `--newer-only` (`held_older`, `held_same`, `held_unknown`). They
     compare activity times only, so the link cannot change them, and a held row never enters
     B's after-state as replaced.
  3. Mark as provisionally unchanged any refresh whose bytes equal the destination's apart from
     `spawnedFrom` and `startChain`. Such a refresh keeps the row's conversation, so it never
     swaps one.
  4. Run `held_orphan`. Its `doomed` set counts only the refreshes that are not provisionally
     unchanged, so a destination row that keeps its conversation still vouches for it, as today.
  5. Run the lineage pass over the rows that survive. Its held input is the `cliSessionId` each
     held row's source opens, which `plan_sync` now records beside the title it records today.
     Each candidate keeps its source row's raw `spawnedFrom` and `startChain` in the plan, so the
     apply-time pass resolves from the source and not from a stripped post-image. That makes a
     link the plan dropped gainable at apply time (round 3, agy). B's after-state counts each
     surviving refresh with its source contents. A row this sync refreshes into another
     conversation never qualifies as P's row, and a held row counts as it stands.
  6. Decide "unchanged" on the final bytes. A provisionally unchanged refresh whose final bytes
     still equal the destination's is dropped. One whose only change is the translated link is
     written.
  7. Compute the refresh's field loss on the final bytes.

  `transform_row` stays a pure serializer; it simply stops being what decides this key.
- **Applying, once:** `run_sync` runs the pass again under the lock, after its guard and before
  `new_op`.
  - Where the result changed, it rewrites `post_b64`.
  - It then recomputes every decision derived from the bytes: "unchanged", the field loss and
    the link line. A refresh whose rewritten `post_b64` equals its `pre_b64` is dropped from the
    op, which keeps `sync`'s invariant that a journaled refresh changes its row.
  - Decisions derived from the row's conversation, the swap and its vouchers, do not depend on
    `spawnedFrom` or `startChain`, so they stand.
  - The rows are re-sorted into write order, as for `converge`.
  - If the pass leaves no row to write, no op is journaled, and the run reports unchanged, as
    `converge` does (round 3, DeepSeek).
  - `execute_sync_op` and its recovery never resolve again.
- **Reporting after the apply.** `_print_sync_report` prints before the apply, so the apply-time
  result has its own line in the after-apply trailer, beside `copied :` and `refreshed :`. For
  example: `links   : 3 carried, 1 retargeted, 1 dropped (parent's row drifted)`. Gained links
  are counted apart (round 3, DeepSeek).
- **Recovery has no per-row skip, and needs none.** `sync`'s executor writes every pending row or
  refuses, and `classify_sync_op` offers only `back` once a pending row has drifted or vanished.
  This design does not change that, except in one way:
  - **Every dependency must still match.** `classify_sync_op` also treats as drift any row that a
    pending row's journaled link or chain names, when that row is gone or its `cliSessionId` no
    longer equals the journaled `parent_session` or `root_session`. Forward is then withdrawn, and
    `back` is offered (round 2, Kimi). This covers every target, whether or not the op wrote it:
    a row the op wrote, an existing row outside the op, a row an age hold kept, and a row dropped
    as unchanged (round 3, Codex).
  - Parents are written before the rows that link to them. So a parent that cannot be written
    stops the op before its child is reached, and no orphan is written forward.
  - `back` skips a row that drifted or cannot be read, as today. So it can remove a parent while
    leaving a drifted child, whose link then dangles. The dangling-link report below covers
    `recover --back` for both commands, as well as `undo` (round 2, DeepSeek).
  - (The 2026-11-17 text claimed a `parent_not_written` skip "as for `converge`". `sync` has no
    such state, and none is added.)
- **An add** carries the translated link, or none, and the report says which.
- **A refresh** (`--update`) keeps its documented whole-row semantics: the destination row is
  replaced by the source's copy with the link translated or dropped, never merged with the
  destination's own `spawnedFrom`. **(L4)**
  - **The link line:** the refresh report gains a line when the destination's own `spawnedFrom`
    or `startChain` is dropped, or names a different row, after the refresh.
  - Those two keys are then left out of the "fields the destination's row loses" line, so a lost
    link is reported once.
- **`--verbatim`** stays verbatim: no translation, no filtering. It is the goal's stated
  exception. **(L4)**
  - Its help gains a sentence, because today's help says only "copy rows unchanged instead of
    stripping connector config" (corrected in round 3, DeepSeek; the earlier text claimed the help
    already said it). The sentence: "Links to a parent or chain root (`spawnedFrom`,
    `startChain`) are copied as is, not translated, so they may name a row that does not exist in
    the destination."
  - A `--verbatim` add that copies a link is counted on its own report line, `links   : N copied
    verbatim (not translated)`, since such a link names a row in the source account.
  - A `--verbatim --update` that replaces the destination's own link is reported by the same
    link line.
  - The existing dropped-and-reset lines do not report it, because the key survives and is not
    one of the reset keys. (Corrected; the 2026-11-17 text said they did.)
- **Write order:** parents before the rows that link to them, as for `converge`.

### `undo`

`undo` of a `converge` can keep a row it created (when it has become the last pointer to its
conversation) while deleting the parent row the same op created, which leaves the kept row's link
dangling. Ruled (L5, 2026-11-18, after Trial D showed the child listed and opening with its
parent's row gone): accept it and say so in the undo report, `link left dangling: <row> (its
parent's row was removed)`. **(L5)**
- `undo` and `recover --back` reverse every row in reverse write order, children before parents.
  That covers a deleted add and a restored refresh alike, in one sequence. Today's
  `_sync_reverse_all` handles every add before every refresh, and that changes. So an
  interrupted reversal leaves a parent without a child, never a child naming a row already
  removed (round 3, Kimi).
- The link report is computed after the whole reversal, from the rows that survive. This applies
  to `undo` and to `recover --back`, for `converge` and for `sync`, and to `undo` of a `new-row`,
  whose row a later op may have linked to (round 3, agy). `new-row` itself is unchanged. It
  reports two cases:
  - **dangling:** a surviving row whose `spawnedFrom.sessionId` or `startChain.firstSessionId`
    names a row this reversal removed. That is reported whichever op created the surviving row,
    which covers undoing an earlier op of any kind that creates rows, whose parent row a later op
    linked to;
  - **redirected:** a surviving row with recorded `lineage` whose target still exists but now
    opens a conversation other than its journaled `parent_session` or `root_session`. A reversal
    that restores a refreshed parent to its earlier conversation does this (round 3, Kimi).
  - Rows the scan cannot read are counted in the report as not checked.
- **L5's measurement covers a dangling parent link, not a dangling root.** Trial D set aside the
  parent's row only. A `startChain` whose root row is gone is reported the same way, but how the
  app shows such a child was not measured (round 2, Kimi). Ruled (L5b, 2026-11-18): accept and
  report it, as for a parent.

### Unchanged

`new-row` (no source row; still omits the field), `realign` (it writes `cwd`, `folderPlace.cwd`
and `folderPlace.realPath`, never the link), `repoint`, `retitle` and `move`.

## Decisions for the maintainer

- **L1 — what is carried. Ruled 2026-11-18 from Phase 0: `{sessionId, taskId}`.** `sessionId`
  is translated, `taskId` is copied, and there is no link at all without `taskId`. `startChain`
  is translated, as Trial C cleared it. Only the link variants measured are carried: side
  sessions with `sideSession: true`; links of exactly `{sessionId, taskId, title}` (cleared by
  L7's probe, 2026-11-18); and this tool's own `{sessionId, taskId}` output, which comes only
  from those (added in the fourth review, so that a carried link can be carried again).
  `wakeParent`, `pendingPop` and unknown keys are always dropped. *The
  2026-11-17 default (carry `taskId`, `title`, `sideSession`, `name`, `sharedFolder`,
  `sharedCheckout`, `userFork`) is superseded, and so is "`{sessionId}` if it is enough", which
  Phase 0 ruled out.*
  - The variant clause stands from the approved design and was not re-ruled. Its consequence is
    now visible. In a census on 2026-11-18, 530 of 734 links carry neither `sideSession` nor
    `name`: 524 are exactly `{sessionId, taskId, title}`, and 6 also carry `pendingPop`. Those
    links keep carrying nothing until a trial clears their variant.
    *(2026-11-18 evening: L7's trial cleared the 524 `{sessionId, taskId, title}` links, which
    now carry the same `{sessionId, taskId}`. The 6 with `pendingPop` stay uncarried.)*
- **L2 — disagreeing holders, self-links, dead parents, cycles, several parent rows.**
  Recommended: no link when holders name different parents (any variant counts), a conversation
  names itself, the parent's transcript is gone, or the link would close a cycle; a deterministic
  pick when the parent has several rows in one account.
- **L3 — the store changes between plan and apply.** Recommended: re-run the whole lineage pass
  once under the lock before journaling; keep, retarget or drop; write the row anyway; report it.
- **L4 — `sync`.** Recommended: restructure planning so the pass runs over the final set and the
  refresh decisions come after it; re-run before journaling; a refresh keeps whole-row semantics
  and reports a lost or changed link; `--verbatim` is a stated exception.
- **L5 — `undo` stranding a link. Ruled 2026-11-18: accept and report.** Trial D showed the app
  copes.
- **L6 — recovery and a parent it will not write.** Recommended: skip the dependent row as
  `parent_not_written` and leave it for the next `converge`. Alternative: refuse forward recovery
  for that op and allow only `back`.
- **Also for the maintainer:** the recovery defect in the shipped tool ("What this rests on", last
  row) can be fixed on its own, ahead of this change.
- **Left open by the fourth review, and ruled by the maintainer on 2026-11-18 as recommended:**
  - L7: run the probe on the older shape before the build. If the carried `{sessionId, taskId}`
    nests that variant cleanly, `{sessionId, taskId, title}` links join the cleared set. If it
    does not, they stay uncarried.
  - L5b: accept and report a dangling root.

  The text below is kept as it was put to the maintainer.
  - **L7 — the older link shape.** 524 of 734 links are exactly `{sessionId, taskId, title}`,
    with no `sideSession`, and the variant gate carries nothing for them.
    - Phase 0 never wrote a link from that variant.
    - One more probe run on such a side session would measure it, and its `A+taskId` trial
      writes exactly the carried shape.
    - Recommended: run it before the build, because it decides whether most links can be
      carried at all.
    - Alternative: ship with the gate and widen it later.
  - **L5b — a dangling root.** The ruled L5 rests on a dangling parent, which was measured. A
    `startChain` whose root row is gone is reported the same way, and the app's handling of it
    was not measured.
    - It arises only through `undo`, `recover --back`, or a deletion in a crash window.
    - Recommended: accept and report it as for a parent, and measure it only if the acceptance
      check or later use shows a problem.
    - Alternative: extend the probe with a root-removal trial first.

## Tests (written first, synthetic stores, fake cast)

- lineage pass: parent and child both planned; parent existing; parent absent; parent's
  transcript gone; parent's pair held (`parent_pair_held`); two holders naming different parents,
  including one whose link is an uncleared variant; a self-link; a cycle across two accounts and
  one across three (`lineage_cycle`, the rest kept); two existing parent rows (the pick); a stem
  mismatch and non-string ids ignored; an agreed uncleared variant carrying nothing; the carried
  link exactly `{sessionId, taskId}`, every other key dropped and recorded; a holder link with no
  `taskId`, or an empty or non-string one, carrying no link (`lineage_no_task`); holders that
  agree on P but give different `taskId` values carrying no link (`lineage_task_disagree`); the
  chain exactly `{firstSessionId, depth}`, omitted on a bad depth or disagreement; `--only` on a
  child whose parent is out of scope.
- lineage pass, added in the fourth review:
  - a holder whose link is the tool's own `{sessionId, taskId}` is eligible, and a link carried
    into a second account is carried again into a third (two `converge` runs) and back by
    `sync --update`, unchanged;
  - an app link of exactly `{sessionId, taskId, title}` carries `{sessionId, taskId}`; one with
    any other key set and no `sideSession` (for example with `pendingPop`), or with an empty
    `title`, carries nothing (`lineage_variant`);
  - the newest holder has no `taskId` while an older cleared holder has one: the older one is
    the template;
  - a root edge that closes a cycle with a parent edge is dropped (`lineage_chain_dropped`), and
    the parent link is kept;
  - a root whose transcript is gone, a `depth` of `true`, and holders that agree on R but not on
    `depth` each omit the chain;
  - the parent-row pick: for `sync`, a same-filename planned row before an existing duplicate;
    otherwise an existing row before a planned one;
  - a damaged `lineage` or `lineage_planned` (a string, a wrong type) is a shape error in
    `recover` and `doctor`, never an exception;
  - **old journals:** real manifests in the format before this change, with no `lineage`, both
    pending and completed, still go forward, go back, undo, list in `recover` and pass `doctor`;
  - an archived parent row is picked only when no unarchived one exists;
  - a boolean `lastActivityAt` sorts as zero.
- converge, plan and apply: the dry-run `links :` line; the planned parent skipped as
  `already_present` (retarget); a holder's link changed since planning (re-derived); the parent's
  transcript gone (drop); the root changed alone; `lineage` and `post_b64` agree;
  `lineage_planned` kept; the journal holds the final bytes.
- converge, recovery: the shipped defect (a landed unflagged row and an unlanded one, recovered
  forward, then `undo` removes the landed row); a crash before the parent landed, then another
  row for the parent appearing, then forward: the child is skipped as `parent_not_written`;
  a child landed and its parent then deleted: the dangling link named; parents written before
  children; `undo` with a mixed outcome and its report line.
- converge, added in the fourth review:
  - the shipped defect again, but with the app having rewritten the landed row before recovery:
    it is still marked written (by its minted stem) and `undo` removes it;
  - a planned chain R, P, C with R skipped as `already_present`: P and C are both skipped as
    `parent_not_written`;
  - an apply-time rewrite that makes a row depend on one sorted after it: the rows are re-sorted
    before `new_op`;
  - the root dropped alone at apply time is reported as `lineage_chain_dropped`, with the parent
    link still counted as carried;
  - `undo` deletes children before parents;
  - its dangling report finds a surviving row from an earlier op that links to a row this undo
    removed;
  - round 2: a parent written before the crash and then repointed to another conversation means
    the pending child is skipped as `parent_changed`, never written with the stale link; the
    same for a root;
  - round 2: a parent row that appears between the dry run and the apply gives `lineage_gained`,
    counted apart on the `links :` line;
  - round 2: `recover --back` that skips a drifted child while removing its parent reports the
    dangling link;
  - round 3: a holder's parent row whose filename B holds but which opens another conversation
    gives `parent_row_drifted`, no link;
  - round 3: a planned parent the recheck drops as `held_title_collision` is reported by that
    reason;
  - round 3: a cycle already present among B's rows is left alone and reported as
    `existing_cycle`.
- sync: an add translates; an add with no parent in B (no link, reported); a parent removed by
  `held_orphan`; a parent synced in the same run (same filename); a candidate parent row the same
  sync refreshes into another conversation (not chosen); a refresh whose only change is the link
  (written); a refresh that drops or changes the destination's own link, and its report line;
  re-resolution before `new_op`; a crash and recovery with no re-resolution, and a parent
  refresh that drifted after the crash: forward refused, `back` offered, and no child written;
  `--verbatim` untouched, and `--verbatim --update` replacing the destination's own link reported
  by the link line.
- sync, added in the fourth review:
  - `--update --newer-only` holding a parent refresh: the child's link resolves against the held
    row as it stands;
  - a link-only refresh keeps its row vouching in `held_orphan`;
  - an apply-time pass that undoes a link-only refresh's only change: the row is dropped from the
    op, not journaled with `post == pre`;
  - a lost link reported once, by the link line, not again as a lost field;
  - round 2: a parent written before the crash and then repointed makes `classify_sync_op`
    withdraw forward and offer `back`;
  - round 2: a `--verbatim` add that copies a link is counted on its own line;
  - round 2: the dangling scan counts rows it cannot read as not checked;
  - round 3: a pending child whose parent is an existing row outside the op, an age-held row, or
    an unchanged row, repointed or deleted after the crash: forward withdrawn, the child never
    written;
  - round 3: the after-apply `links :` line; an apply-time pass that leaves nothing journals no
    op;
  - round 3: an interrupted reversal of an op that added a parent and refreshed a child restores
    the child before removing the parent;
  - round 3: `recover --back` that restores a refreshed parent to its earlier conversation
    reports the surviving child's link as redirected;
  - round 3: an op with `lineage_format: 1` whose non-verbatim row carries a link but no
    `lineage` is a shape error with no forward write. An old `sync` journal without the marker,
    holding a verbatim link, still recovers.
- reporting: text, `--json` and `--anonymize` golden output for lineage notes and both `links :`
  lines, including a conversation literally titled `lineage`; the GUI's Level tab with a lineage
  note (`tools/check_gui_level.py`).
- existing: `tools/check_new_row.py` (`new-row` rows still carry no `spawnedFrom`) and every
  `tools/check_*.py`.

## Acceptance check (after release)

With the app closed, `converge --apply`. In an account that lacked a side session, open the app:
the side session appears under its parent, and nothing Phase 0 ruled a side effect appears. Close
and reopen the app and read the row: the link is still `{sessionId, taskId}` with the translated
parent (normalized optional keys are fine). Record it as a dated measurement. It is the second
measurement of the carried shape after Phase 0's one candidate. When a `sync` add of a side
session is available, open one such row the same way. L7's probe cleared the older variant
(`{sessionId, taskId, title}` links), so also open one older-variant child that the release
carried, since the side session the check starts from is the side-session variant. The bytes are the same shape whichever
command wrote them, but `sync`'s route then gets an app-level look of its own.

## Revisions

- **Round 1 (Codex, agy, DeepSeek, Kimi; all four reported).** Added Phase 0, a probe before any
  code, which all four asked for; L1's carried set now comes from it, starting minimal. Apply time
  re-resolves parent and root independently, and rewrites `lineage` with `post_b64`. Several
  parent rows in one account get a deterministic pick. Self-links, stem mismatches and bad types
  are ignored. Writes go parents first. A refresh keeps whole-row semantics. The goal was scoped to
  what the tool writes. Added L5, an after-apply report line, a separate note kind, and tests.
  Rejected: that `move` carries rows between accounts (it moves a session between project
  folders); updating `spawnedFrom.title` on `retitle` (the field records the side session's title
  at spawn).
- **Round 2 (Codex, DeepSeek and Kimi reported; agy ran out its 15-minute limit, and its partial
  raised the same blocker as Kimi).** Apply-time rewriting had run after journaling for `sync` and
  during recovery for both commands; the lineage pass now runs once, under the lock, before
  `new_op`, and recovery never re-resolves. The pass re-derives source links and holder agreement,
  resolves against B's state after the op, and prefers `sync`'s same-filename row. Phase 0 probes
  a converge-made row, gates `startChain` on Trial C, and limits the first build to the measured
  variant. Dead parents carry no link; new keys are registered for `--anonymize`; notes separate a
  held parent pair from an absent one. Corrected the guard row and the `transform_row` wording.
- **Round 3 (Codex, DeepSeek and Kimi reported; agy refused twice on its `command` permission).**
  These fixes were applied after the review's last round and **have not been re-reviewed**.
  Recovery could write a child whose planned parent it then skipped: it now skips the child too
  (L6), and a defect in the shipped tool's recovery (a landed row lost to `undo`) is fixed first.
  Round 2's reason for not checking cycles was wrong, since edges from different accounts can
  close one: the pass now drops a link that would close a cycle. `sync`'s pass order was
  impossible in the current `plan_sync`: planning is restructured so the refresh decisions come
  after the pass. Phase 0 now runs one key per trial, so a result names its key, and adds Trial D
  for a dangling link, which L5 rests on. Holders of any variant count toward disagreement. The
  dry run reports links. Goal 1 states the held-parent exception. `_synthesize_row` stays
  link-free, with a note on the new-row design.
- **Phase 0 and the rulings (2026-11-18).** Two hand-run probe runs. The second found that
  `sessionId` alone hides the child, and that `sessionId` with `taskId` nests it. The maintainer
  ruled L1 (`{sessionId, taskId}`) and L5 (accept and report) on those results. The design now:
  - writes exactly that link, and no link at all without `taskId`;
  - requires holders to agree on `taskId` (a reading of L2, not ruled separately);
  - writes the chain in the two-key shape Trial C passed;
  - adds the anti-goal of a link that hides the child;
  - updates the claims table, L1, L5, the tests and the acceptance check.

  This revision is under the fourth review.
- **Fourth review, round 1 (Codex, agy, DeepSeek, Kimi; all four reported).**
  - Accepted:
    - The carried shape failed the design's own variant gate, so a carried link could never be
      carried again (Codex, agy). The tool's own `{sessionId, taskId}` output now counts as
      cleared, and the template is chosen among eligible holders (agy).
    - `sync`'s planning order let a `--newer-only` hold, or `held_orphan`, act after the pass
      (Codex, DeepSeek, agy). The order is now explicit: age holds, provisional "unchanged",
      `held_orphan`, the pass, final "unchanged", field loss.
    - `sync`'s claimed recovery skip does not exist (Kimi, agy). The design now relies on
      `sync`'s own all-or-back recovery and the write order.
    - The apply-time pass now recomputes every decision derived from the bytes, drops a refresh
      that becomes a no-op, and re-sorts rows into write order (Kimi, agy).
    - Cycles are now checked over root edges as well as parent edges. The root needs a live
      transcript and holder agreement on `depth`, and a boolean depth is refused (Kimi, agy).
    - The recovery-defect fix also recognizes a landed row by its minted stem (DeepSeek).
    - `parent_not_written` carries down a planned chain (agy).
    - Shape checks cover `lineage` (DeepSeek).
    - `--verbatim --update`'s link change is reported by the new line; the old claim was false
      (DeepSeek, agy).
    - A lost link is reported once (agy).
    - The parent-row pick is reordered so each clause can decide (agy).
    - A root dropped alone has its own note (agy).
    - `undo` deletes children first and finds dangling links after deleting (agy).
    - Goal 1 lists its drop exceptions (DeepSeek).
    - The acceptance check adds a `sync` add (Kimi, in part).
  - Measured to answer DeepSeek's premise question: in Phase 0's account, the parent's row and
    the root's row were written by the same `converge` operation as the child's (2026-11-18).
  - Rejected:
    - "A round trip destroys a valid destination link" (DeepSeek). After translation, a carried
      link names a row in its own account, so step 1 resolves it there and the round trip
      restores the link. The variant fix above was the missing piece.
    - "Gate `sync --update` separately" (Kimi). The row's bytes are the same shape whichever
      command writes them, and the app reads the row.
    - "`swaps_conversation` can change with the link" (Kimi). A swap compares the row's own
      `cliSessionId`, which the link does not touch.
    - "A further trial with a parent row whose own `taskId` differs" (DeepSeek). A parent row
      carries no child's `taskId`, and the same-run case is the one measured.
- **Fourth review, round 2 (Codex, DeepSeek and Kimi reported; agy failed, its headless
  `command` permission denied, and was retried once).**
  - Accepted:
    - Round 1's shape check made a missing `lineage` an error, which would have stranded every
      journal written before the upgrade (Codex, Kimi, DeepSeek). Absence now reads as "no link",
      and only a present but damaged value is an error.
    - Recovery could write a child linked to a parent written before the crash and since moved to
      another conversation (Kimi). Written dependencies are now re-checked, not re-resolved.
      `converge` skips the child as `parent_changed`, and `sync` withdraws forward.
    - `sync`'s `recover --back` can strand a link (DeepSeek). The dangling report now covers
      `recover --back` for both commands.
    - A link gained at apply time gets `lineage_gained` (DeepSeek).
    - Archived parent rows rank last (DeepSeek). A boolean `lastActivityAt` sorts as zero.
    - The eligibility clause's assumption that the app leaves the link as written is stated,
      with the acceptance check reading it again (DeepSeek).
    - Smaller: `lineage_planned` is checked too; the held pairs are named as an input;
      `--verbatim` adds that copy a link are counted; unreadable rows in the dangling scan are
      counted (DeepSeek).
  - Put to the maintainer as open items, not built around:
    - L7, the older `{sessionId, taskId, title}` shape (DeepSeek's strategy note);
    - L5b, a dangling root (Kimi).
- **Fourth review, round 3, the last (all four reported; see "Panel" below).** These fixes came
  after the last round and **have not been re-reviewed**.
  - Accepted:
    - `sync`'s recovery check now covers every dependency, outside the op too (Codex).
    - `sync` gets an after-apply `links :` line (DeepSeek), and journals no op when the pass
      leaves nothing to write (DeepSeek).
    - A drifted parent row gets `parent_row_drifted` (DeepSeek).
    - The `--verbatim` help sentence is specified, after a false claim (DeepSeek).
    - The claims table's stale "only `sessionId` has a meaning" is corrected (DeepSeek).
    - The recheck's skips feed the pass's reasons (DeepSeek).
    - The `_activity_of` wording is fixed (DeepSeek).
    - An existing cycle is reported (DeepSeek).
    - Reversals order deletions and restorations together (Kimi).
    - The link report also finds redirected links (Kimi).
    - A `lineage_format` marker separates old journals from damaged new ones (Kimi; Codex's
      round-2 suggestion).
  - Clarified, not changed: why `lineage_task_disagree` should rarely fire (DeepSeek).
  - agy (round 3, which arrived after the edits above). Accepted:
    - the cycle pass adds every parent edge before any root edge;
    - `startChain` comes from the holders that carry one, not from the link's template, with no
      note when none does, and with `sync`'s root filename;
    - forward recovery ends with the link report, all-landed path included;
    - the landed-row recognition runs before `back` as well;
    - `sync` records the held rows' sessions and keeps the source links for the apply-time pass;
    - `taskId` agreement compares usable values only;
    - `new-row`'s undo runs the link report.

    The rest of agy's round 3 repeated points the edits above already fix: the reversal order,
    redirected links, and the empty `sync` op.
  - Rejected:
    - "A whole-row `sync --update` from an account whose link is unusable removes a valid link"
      (agy). It does, and the link line reports it. L4 ruled whole-row semantics over a merge,
      and that ruling stands.
    - "The exact-shape clause is fragile if the app normalizes keys" (agy, and DeepSeek in round
      2). It is stated, with the acceptance check reading the link again.
- **Panel for the fourth review:**
  - Round 1: all four reported.
  - Round 2: Codex, DeepSeek and Kimi reported. agy failed twice: first its headless `command`
    permission was denied, then it hit its 15-minute print timeout.
  - Round 3: all four reported, Kimi at its byte cap with nothing discarded.

## Left to the builder

The exact structure of the lineage pass, message wording, the `--json` field names beyond
`lineage` and `lineage_planned`, and where the shared resolver lives so `converge` and `sync` use
one implementation.
