# realign - point every account's row at the folder holding the live copy (design)

Status: **ruled and implemented, not released.** The maintainer ruled every decision below on
2026-11-16 and 2026-11-17, all as recommended, after the probe ran in both phases. Detection (`doctor`'s split rows, `alignment`'s
line under `consistent`) and `realign` itself, with D9's listing, are built on branch
`claude/split-row-detection` (2026-11-17); "Implementation notes" at the end says where the
build departs from this text. Two review rounds (four engines each) are applied; the Revisions
section says what they changed.

Fake cast only: Northwind, ACME-REVIEW, alice@ and bob@example.com, the `u` user path.

## The problem in one example

Conversation S1, "ACME-REVIEW handoff", has its transcript in two project folders:

| Folder | Copy |
|---|---|
| `C--Users-u-Projects-Northwind` | 40 MB, stopped growing a week ago |
| `C--Users-u-Projects-Northwind--claude-worktrees-wt1` | 60 MB; the first 40 MB are the other copy byte for byte |

alice@'s row has `cwd` `C:\Users\u\Projects\Northwind`; bob@'s has
`C:\Users\u\Projects\Northwind\.claude\worktrees\wt1`. The app opens the copy in the folder
the row names, so alice@ sees last week's conversation and bob@ sees today's. A reply from
alice@ forks it. `doctor` now reports this. Nothing in the tool repairs it:

- Removing the stale copy is a catch-up only. Measured once (Desktop 5.11492.5): opening a
  row whose folder holds no copy makes the app copy the transcript into that folder, so the
  split comes back as soon as the conversation is worked from one account and opened from
  the other.
- `repoint` changes `cliSessionId`, not the folder. `converge` never touches an existing row.
  `move` rewrites a row's `cwd` and `originCwd`, but refuses while the transcript is in more
  than one folder.

The lasting state is every account's row opening the same copy, the live one.

## What this rests on

| Claim | Basis |
|---|---|
| A row opens the copy in the folder its `cwd` encodes to, even a stale one | measured, one conversation, Desktop 5.11492.5; and by the probe, phase 1 |
| A row whose folder holds no copy gets one copied in when opened | measured twice: that conversation, and the probe's `empty-b` trial (the whole transcript, plus what the open appended) |
| The app appends `tools-used` to a copy it opens and `cost-state` to one it quits on, the stale one included | measured, same conversation; the probe saw `bridge-session` and `mode` records too, written only when the app started the conversation's session |
| `cwd` is the key the app reads to choose that folder | **measured on a plain-folder row** (probe phase 1, 2026-11-17, Desktop 5.11492.5): `cwd` alone on the other folder opened that folder's copy, every other path key there but `cwd` did not, and `originCwd`, `folderPlace.cwd`, `folderPlace.realPath` and `gitAnchorsFolderRealpath` each alone did not. **Untested on a worktree row** (phase 2) |
| The app does not rewrite the folder keys back when it opens a row | **measured, plain row**: it never rewrote `cwd`, `originCwd` or `gitAnchorsFolderRealpath`; it rewrote `folderPlace` to `{cwd: <the row's cwd>, lost: true}` whenever `folderPlace.cwd` disagreed with `cwd`, and left it when they agreed |
| Changing a row's folder keys changes where the conversation's next turns run | **measured, plain row**: a turn typed after `cwd` moved ran in, and was written to, the new folder, in the same transcript (no new conversation id) |
| A row with a worktree link (`worktreePath`, `worktreeName`, `branch`) can have its folder keys rewritten | **measured false** (probe phase 2, one worktree session): moving `worktreePath` to another folder, even another live worktree of the same repository, made the app drop the row's worktree link (`worktreePath`, `worktreeName`, `sourceBranch` deleted) and say the old worktree was removed; moving `cwd` alone showed that folder's copy, but the session could not start (the app recorded a "worktree branch in use" failure). Git was not touched either time |
| A session started in a worktree records the main repository as `cwd` (the July placement) | **not what the current app writes**: a worktree session started on 2026-11-17 recorded the worktree as `cwd` and `worktreePath`, kept its transcript in the worktree's own folder, and kept the main repository as `originCwd` and `folderPlace.origin` |
| The live copy is the one holding every other copy's conversation | inference from append-only transcripts, with the two bookkeeping records set aside; `doctor` computes it among the copies rows open, and realign must compare it with every copy on disk |

Nothing below is built until the probe has run.

### The probe (run by the maintainer, not by the tool)

One scratch conversation in folder A with two typed turns, and a copy of its transcript in
folder B's project folder holding everything before the second turn. Each trial restores the
row and both copies from a journal, points some path keys at B, and the maintainer opens the
conversation without typing and says whether the second turn shows.

- **Controls first.** `control-a` (no key on B) must show the second turn and `control-b`
  (every listed key the row has on B) must not; `sweep-b` points every string in the row that
  names the row's folder, or any value a listed key holds, at B, so a failed `control-b` can be
  told apart from a key the list does not name. Until both controls pass, the script refuses
  the key trials. `empty-b` is `control-b` with B's copy set aside: whether opening a row whose
  folder holds no copy copies the transcript in, the measured-once claim D3 and D9 rest on.
- **Then each key alone, and every key but one**, to tell a key that suffices from a key
  that is needed; if no key suffices alone, every `all-but:` trial runs. Trials change only
  keys the row already has, as realign would; `add:<key>` trials, separately, ask what an
  absent key does when added.
- **A verdict per trial.** It is conclusive only when the controls passed, the screen and the
  writes agree (the copy the app appended to on quit is the one the screen showed, its earlier
  bytes unchanged), no copy vanished, appeared or was rewritten, and the row still opens the
  same conversation. A trial with no write at all is inconclusive: the screen alone is not
  evidence. A path key the app rewrote on open is recorded, for D5, and does not make a trial
  inconclusive: the app had already chosen the copy.
- **A typed turn** in the winning configuration shows which copy the conversation then grows
  and which `cwd` its new records carry: where it runs.
- **Two phases.** A plain-folder row, then a row of a session the app started in a worktree
  of a scratch repository, where `worktreePath`, `folderPlace` and `gitAnchorsFolderRealpath`
  carry their real values. Phase 1 alone says nothing about worktree rows.
- **Recoverable.** The journal records what each step is about to do before it writes, an
  interrupted step can be finished by `undo`, and `undo` checks the row, the copy and its
  sidecar against their state before the probe before saying it is done.

The result comes back here as measured rows in the table above.

## The command

```
claude-code-sessions realign [--only <title|id>] [--include-identical] [--live <id>]
                             [--json] [--verbose] [--anonymize] [--apply]
```

A dry run by default. `--anonymize` with `--apply` is refused, as for every command.
`--only` takes a title substring or a `cliSessionId` prefix and must resolve to exactly one
conversation, as `converge --only` does. The command writes every account's rows, the
signed-in one included, so the running-app guard is the only thing between it and the app.
`--live` is converge's, at account scope: an apply refuses while the two identity files
disagree about which account is signed in, unless `--live` names it, and `--live` is refused
when they agree (converge's RULING 5 refusal, word for word). Realign needs the answer only to
name the signed-in account in its plan; if the maintainer prefers, the flag and the refusal
can go together, since no write depends on which account is live.

**Which conversations.** Every split conversation `doctor` reports is planned or held, and
the plan says which and why:

- **Behind** (`strict_prefix`): planned. One opened copy holds every other opened copy's
  conversation, and some row opens an older one or none.
- **Diverged**: held, with the copies named. Which history to keep is the user's call. A
  diverged conversation keeps `doctor` at exit 1 until the user resolves it by hand; a later
  `--keep <folder>` could take that call, and is out of scope here.
- **A row that opens no copy**, whatever the relation of the copies the other rows open:
  planned whenever the conversation has a target (below), since a row with no copy gets one
  copied in on open and starts a split of its own (D3). This is a flag on the conversation,
  not a relation: identical copies plus a row with no copy are planned for that row even
  without `--include-identical`, and only that row changes.
- **Identical or same conversation** (copies differ in nothing, or only in bookkeeping):
  held unless `--include-identical`, with the target below (D3).
- **Inconclusive**: held.

**The target folder.** The folder of the live copy: the one holding every other copy's
conversation, bookkeeping aside. It is compared with **every** copy on disk, not only the
ones rows open: when a copy no row opens holds conversation the target lacks, the
conversation is held and that copy named. When more than one copy holds the whole
conversation (they are identical, or differ only in bookkeeping, and each is ahead of or equal
to every other copy), the target is chosen among their folders: the folder opened by the most
recently active unarchived row among those that open one of them. Rows that open no copy do
not vote; a tie between distinct folders, or no such row, holds the conversation. Rows that
`converge` built carry a `lastActivityAt` taken from the transcript, which can run ahead of the
app's own row, so the plan names the row that decided.

**Which rows change.** Every row that does not open the target copy, once the conversation is
planned: rows that open a copy behind it, rows that open a copy holding the same conversation in
another folder, and rows that open no copy. A copy ahead of the target or diverged from it never
reaches this point: it holds the conversation. So a planned conversation ends with every row
opening the target, and `doctor` no longer lists it. `--include-identical` decides only whether
a conversation with no copy behind is planned at all. A row that already opens the target copy
is left alone, whether it reaches it through its `cwd` or through the documented worktree
placement (`cwd` names the main repository and the copy sits in one of its worktree folders).

**A row with a worktree link is never rewritten.** A row carrying `worktreePath` or
`worktreeName` holds its conversation, with the reason: the probe showed that rewriting such a
row's folder keys either detaches it from its worktree or leaves a conversation that cannot
start. On the store this was found on, no row realign would change carries a worktree link;
the rows that do are live rows, which realign leaves alone.

**Which copy a row opens is decided by one shared rule.** `doctor`'s `_split_conversations`
resolves it from `cwd` today, which the probe has to confirm. If the probe shows another key
decides the folder, that rule changes first, in detection, and realign's planning, apply-time
re-check, undo and its post-run check all call the same rule; realign never carries a copy of
it (D2). **A conversation whose live copy no row reaches through its `cwd`** -
only through the worktree placement - is held until the probe's worktree phase has run: making
other rows reach that copy means either naming the worktree, which the app's own rows for it do
not, or naming the main repository, whose folder the app may then copy the transcript into.

**The path value it writes.** A folder name cannot be decoded back into a path: every
character outside `[A-Za-z0-9]` became `-`, so different paths can share one folder. The value
comes from the `cwd` of a row that reaches the target folder through its own `cwd`, exactly as
that row spells it, or else from the `cwd` values the live copy's own records carry. The
candidates are first filtered to those that encode to the target folder (a transcript that
changed directory mid-session carries others, which are not candidates), and must encode to
it under the **current** scheme (a legacy-only match would make
the app look in another folder and copy the transcript there). So a live copy in a
legacy-encoded folder holds its conversation, and the hold says so ("the live copy is in a
legacy-encoded folder; how the app finds such a copy is unmeasured"); `doctor` keeps counting
it. Whether the app reads a legacy folder for a row whose `cwd` encodes differently under the
two schemes is a probe question neither phase asks, and the answer would decide whether such
a conversation can be repaired by rows alone. When the candidates disagree
(compared as paths, without case on Windows) the conversation is held: they may name
different directories. With no candidate it is held. The directory must exist (D4).

**The keys it rewrites** are decision D2's. The draft, for the probe to confirm or replace:
`cwd`, plus `worktreePath`, `folderPlace.cwd` and `folderPlace.realPath` wherever they equal
the row's old `cwd` as a path; `originCwd` and `gitAnchorsFolderRealpath` only if the probe
shows the app reads them; a key the row lacks is not added; `folderPlace`'s other members are
kept. Every other key is left as it is, value for value.

**What else changes, and the plan says so.** Where a rewritten row's conversation runs next
(its working directory) moves with its folder keys, and the sidebar groups the conversation
under the new folder. Each planned row's block says both, old to new.

**Refusals, per conversation, the rest of the run going ahead:** diverged, inconclusive or
same-conversation copies (the last without the flag); a copy no row opens that holds
conversation the target lacks; a live copy reached only through the worktree placement;
disagreeing or missing path evidence; a target directory gone (D4); an operation in the
journal still open on any of its rows or copies. **Refusals for the whole run:** the app
running, an unreadable store or row (the run cannot tell which conversation an unreadable row
belongs to, as `converge` and `retitle` cannot), a stale lock.

**Apply re-checks everything the plan used, under the lock, before journaling:** it discovers
the rows and copies again, recomputes each row's placement, every pair's relation (bytes and
conversation), the target, the path value and the directory, and holds any conversation where
one of them changed since the plan. A held conversation is reported, not written.

**Exit codes**, as `converge`'s: a dry run exits 0 whenever it could plan, holds included; an
apply exits 0 when every planned conversation was written and 3 when any was held; 1 for a
refusal of the whole run, 2 when the store could not be read.

## Journal and undo

The shape of `retitle` and `repoint`: plan, re-check under the lock, journal each row's
pre-image as bytes and its post-image, write atomically, verify by value (the row is
re-serialised from a dict, so byte identity is not the test, as `_retitle_write_rows`
documents), mark completed. A new op type, `realign`, has to join every dispatch point that
names op types: `classify_op`; `recover_op`'s list of op types that skip the move-shaped
`_validate_manifest_paths`, with a `_realign_shape_error` in its place, as each op type that
skips it has; `_journal_rows`, whose last branch reads a per-row `store_path`, so every realign
row records its own `store_path` (as retitle's do) and the resolution gate keeps its store
check; `cmd_undo`'s list of undoable op types and its GUI mirror `_find_undoable_sync`;
`rotate_ops`' `_collides`, which pins terminal ops whose rows another op may still claim;
`doctor`'s journal readers; the structural-keys set for `--anonymize`; and
`test_journal_reroot.py`'s row-reader list, by hand, since its source check does not see a
reader that calls `read_json`. The row-reader test does not catch a missing op-type branch, so
a new test lists every function that compares `op_type` against a literal and fails until
each names `realign` or says why it need not.

**Ownership.** A realign's post-image is a function of the store, so a stalled realign and a
later one write the same bytes, and a later operation of any kind may rewrite the row with
different ones. So `_realign_claimed_later` claims by row path and journal order, not by
bytes: any later operation that wrote the same row (a later realign, retitle, repoint, sync
or move) claims it, and recover's `back` and undo refuse for it, naming that operation.

**Recovery.** `recover` forward finishes from the journal, not from a new plan: a row whose
current bytes equal its journaled post-image has landed and is not re-checked; a row still at
its pre-image is written; anything else stops for that conversation. The plan-change
re-check runs before journaling, never on recovery, so rows that already landed do not hold
their own conversation.

**Status.** One run is one operation, and undo is per conversation, so the manifest records a
status per conversation: undone, refused (with why), or not yet tried. The operation is
`undone` only when every conversation is; until then a later undo retries the rest and skips
the undone ones. Within a conversation it is every row or none, so accounts are never left
split in a new way.

**Undo** is decision D5, and is ruled after the probe: if the app rewrites the folder keys
when it opens a row, no undo rule survives the first open. The draft is per conversation, not
all-or-nothing across the run, with these preconditions for each row: it still opens the
same conversation (`cliSessionId` unchanged); each key realign changed still holds the value
realign wrote; no later operation claims the row; the folder it would go back to still exists
and is as realign journaled it: holding its copy, or (under D6(b)) with the copy in realign's
journal, which undo puts back and verifies before touching the row, or, for a row that opened
no copy, still holding none. Then a history check for the conversation, against what realign
journaled (each copy's size and hash when it ran): undo proceeds when the copy the rows now
open holds no conversation it did not hold then (bookkeeping aside), and the copy undo sends a
row back to is unchanged or only gained bookkeeping. It refuses for that conversation, saying
which, when the open copy has gained turns since (putting the rows back would hide them and
invite a fork), when the old copy has gained turns of its own (the two have diverged), or when
either comparison is inconclusive. So an undo straight after realign succeeds, and puts every
row back on the stale copy it opened before, as the plan said; undo restores routing, not
history.

## What it does not do

- **Row metadata.** `lastActivityAt`, `completedTurns` and the title stay as they are. The app
  refreshes them on focus, but only in the account it is signed into (measured 2026-11), so a
  realigned row can sit in the wrong place in a sidebar until it is opened (D7).
- **Prevention.** `converge` builds a row's `cwd` from the transcript's *first* recorded `cwd`
  (`_transcript_facts`), not from the folder whose copy it read. When those differ, a
  converge-built row names a different folder from the app's own row. Whether that is how
  the splits seen so far began is untested (D8).

## Tests (to write first)

- Planning: behind conversations planned; diverged, inconclusive and same-conversation held
  (the last without the flag); unplaced planned when the opened copy holds every copy's
  conversation; a copy no row opens that is ahead holds the conversation; a live copy reached
  only through the worktree placement holds it; identical with the flag targets the most
  recently active row's folder, and a tie holds.
- Rows: only rows behind or without a copy change; a row already opening the live copy,
  through `cwd` or the worktree placement, is untouched.
- Path value: from a row reaching the target through `cwd`, else from the live copy's
  records; held when candidates disagree, when none encodes under the current scheme, when
  there is none, when the directory is gone.
- Keys: only D2's keys change, a missing key is not added, `folderPlace`'s other members are
  kept, every other key is value-identical after the write.
- Apply: the app running refuses before writing; a copy that grew, shrank or changed relation
  since the plan, a row that changed, or a new copy holds that conversation.
- Journal: interruption at each phase recovers forward and back; every dispatch point above
  knows the op type; a later realign or another op's claim stops undo and `back`.
- Undo: straight after realign, restores the changed keys and keeps the app's later edits to
  other keys; refuses for a changed `cliSessionId`, a key moved since, a missing old folder or
  copy, an open copy that gained turns since, an old copy that gained turns of its own, and an
  inconclusive comparison; bookkeeping gained on either side does not refuse; one
  conversation's refusal does not stop the others'.
- Every planned conversation ends with every row opening the target, including the mixed
  case of one copy behind and another holding the same conversation; `--include-identical`
  plans conversations with no copy behind, and the same holds for them.
- D6(b), if ruled: the copy moves only after the rows verify; an interruption mid-move
  recovers both ways; undo restores and verifies the copy before any row; an ahead or diverged
  copy is never moved; `move`, `repoint` and `converge` act on the conversation afterwards.
- `doctor` reports no split for a realigned conversation; `alignment`'s line drops by one;
  `--anonymize` hides titles, paths and folders in the plan and the manifest; the
  structural-keys pin covers the manifest's keys.

## Decisions for the maintainer

Ordered so that the ones others depend on come first (D9, added in round 2, sits beside D6).
D0 asks for confirmation of what the detection branch already does; D1 to D9 are open. D2,
D5 and D9 wait on the probe.

**D0. Detection defaults, on the branch now (please confirm).**
(a) `doctor` exits 1 when an unarchived row opens an older or a diverged copy: on the store
this was measured on, 4 conversations today (3 behind, 1 diverged). The alternative, listing
them without changing the exit code, keeps routine checks at 0, and is how this went unseen.
(b) Copies are compared as conversation, not only as bytes: `tools-used` and `cost-state`
records past the shared bytes are bookkeeping. Without this, 2 of the 6 conversations the
bytes called behind were false alarms (the copies differed only in `cost-state`), and a stale
copy the app opened and quit would read as diverged. The alternative is bytes only. (c)
Byte-identical and same-conversation splits (21 and 2 on that store) are listed, not counted.
(d) A split where only archived rows are behind is not counted. (e) A row whose folder holds
no copy is listed, not counted, since which copy it would open is unknown. Recommended: all
five as built.

*Ruled 2026-11-16, the maintainer's call: as built, all five.* Not taken: (a) listing
counted splits without changing the exit code; (b) comparing copies as bytes only; and,
for (c) to (e), counting identical, same-conversation, archived-only or no-copy splits
toward exit 1.

**D1. Command shape.**
(a) A new `realign` command, recommended: one job, its own journal type and undo. (b)
`repoint --folder`: one less command, but `repoint` is one row in one account and changes
`cliSessionId`; this changes rows in every account, different keys, with a different undo.
(c) A step inside `converge`: the routine run would repair splits unasked, but writing rows
that already exist breaks converge's promise that it only adds rows. Recommended: (a); (c) can
call it later.

**D2. Which keys it rewrites.** Ruled after the probe. *Phase 1 (plain row) is in: `cwd`
decides, alone; the app rewrites `folderPlace` to match `cwd` and flags it `lost` when they
disagree, so writing `folderPlace.cwd` and `folderPlace.realPath` together with `cwd` keeps the
row as the app would leave it; `originCwd` and `gitAnchorsFolderRealpath` do not choose the
folder and can stay. Phase 2 (a worktree row) is in too: a row with a worktree link cannot be
moved by its path keys (it detaches, or will not start), so it is held. The recommendation is
now concrete: on a row without a worktree link, write `cwd`, `folderPlace.cwd` and
`folderPlace.realPath` (where `folderPlace` exists; never add it); leave every other key;
hold a row with a worktree link.*
(a) The keys the probe shows decide the folder, on a plain row and on a worktree row alike,
plus any the app rewrites back on open; if that is not `cwd` alone, detection's placement rule
changes to match before realign is built. (b) If the probe is inconclusive, or the two phases
disagree: realign is not built, or is built for the row shape the probe settled and holds
the other; and `doctor`'s split rows carry a note that their placement rests on `cwd`
unconfirmed. (c) `cwd` alone now, without the probe. Recommended: (a), else (b). Never (c), and
not "every path key" on an inconclusive probe: that writes keys nothing has shown to matter,
on row shapes nothing has tested.

**D3. Default scope.**
(a) Behind and unplaced conversations by default; identical and same-conversation behind
`--include-identical`. Recommended: an unplaced row starts a split of its own the first time
it is opened. (b) Behind only, unplaced behind a flag: fewer rows written by default, but the
unplaced rows go on seeding splits. (c) Everything including identical by default: prevents
future splits, but on the measured store it covers 29 conversations instead of 6, for no
present gain. (d) `--only` required: safest, but the routine becomes many runs.

**D4. A target directory that no longer exists** (a worktree removed since).
(a) Hold that conversation and name the directory. Recommended: whether the app can open a
session whose directory is gone is untested, and a broken row is worse than a stale one. (b)
Realign anyway and warn. (c) Recreate the directory: a write outside the store, which no
command here does.

**D5. Undo.** Ruled after the probe. *Phase 1: the app did not write `cwd` back on any open,
and rewrote `folderPlace` only when it disagreed with `cwd`, so a realign that writes the two
together leaves nothing the app will change, and (a) is open to it on a plain row.*
(a) Key-scoped and per conversation, with the identity, ownership, folder and history checks
above. Recommended if the probe shows the app leaves the folder keys alone. (b) Byte
pre-images, all-or-nothing, as `repoint` and `retitle`: exact, but refused as soon as the app
opens any realigned row, which is when undo is wanted. (c) No undo beyond recover's `back`
for an interrupted run, if the probe shows the app rewrites the keys on open.

**D6. The stale copy, once no row opens it.**
(a) Leave it. Nothing opens it, but `move`, `repoint`, `new-row` and `converge` still find two
copies and refuse that conversation, and `doctor` no longer lists it as a split, so nothing
says why. (b) Move it into the journal, undoable, but only a copy that holds nothing the target lacks
(behind it, or the same conversation), never a diverged copy or one ahead in any way, and its
sidecar folder with it; files present only in the moved sidecar are named in the plan. The
operation holding a moved copy is pinned against journal rotation for as long as the copy is
there (`rotate_ops` deletes an operation's folder after ten later ones, which would turn (b)
into (c)), and `doctor` lists the copies it holds. The conversation then has one copy, and
every command can act on it. The cost is a second transaction inside the op, with
`move`'s discipline for transcripts: the journal records the copy's path, size and hash and
its sidecar's file inventory before the move; both paths are checked to sit inside the
projects root (`ensure_contained`, and the sidecar names as `_validate_sidecar_rel` checks
them), and `_realign_shape_error` validates that half of the manifest too, since realign skips
the move-shaped validator; the copy moves only after every row of the conversation is written
and verified; recover finishes or reverses a move left half-done from that record; undo puts
the copy back and checks its bytes and inventory before it restores any row; and the tests
cover each of those, a refused move for a copy that is ahead or diverged, and `move`, `repoint`
and `converge` acting on the conversation after.
(c) Remove it: not undoable. Recommended: (b), with that transaction. The live copy holds
everything the stale one does, so nothing is lost, and (a) leaves a conversation the tool can
no longer act on with nothing to say why. If the second transaction is judged too much for a
first version, (a) is the fallback, with `doctor` taught to list conversations whose rows all
open one copy while another copy remains.

**D9. Conversations with one copy that a row cannot reach.** Detection lists only
conversations with two or more copies. On the measured store, 64 conversations with one copy
(63 with an unarchived row among those rows) have a row whose folder holds no copy and which
the worktree placement does not reach either: by the one measurement, each becomes a split the
first time that row is opened (the probe's `empty-b` trial measured the copy-in a second
time, on 2026-11-17). (a) After the probe's `empty-b` trial confirms the copy-in,
`doctor` lists them (not counted) and realign plans them like a row that opens no copy.
Recommended: the realign half is cheap once the rest exists, and a listing that predicts a
split should rest on more than one observation. (b) List them now, before the probe. (c) Leave
them until one becomes a split, which `doctor` then reports.

**D7. Row metadata.**
(a) Leave it, recommended: the app corrects it on focus. (b) Refresh `lastActivityAt` and
`completedTurns` from the live copy, as converge's facts do: sidebars sort right at once, but
the command writes keys outside its job and its undo grows.

**D8. Prevention in converge.**
(a) A separate, later change: a row converge builds names the folder whose copy it read.
Recommended after the probe, since it depends on D2's keys. (b) Fold it into this change. (c)
Leave converge alone.

## Decisions taken (maintainer, 2026-11-16 and 2026-11-17)

Every decision above, the maintainer's call, each as recommended:

- **D0** (2026-11-16): the detection defaults as built, all five.
- **D1** (2026-11-17): a new `realign` command. Not taken: `repoint --folder`; a step inside
  `converge`.
- **D2**: on a row without a worktree link, write `cwd`, `folderPlace.cwd` and
  `folderPlace.realPath` (where `folderPlace` exists; never add it) and leave every other key;
  hold a row with a worktree link. Not taken: every path key naming the old folder; `cwd` alone;
  rewriting a worktree row's identity keys.
- **D3**: behind conversations and rows with no copy by default; identical and same-conversation
  splits with `--include-identical`. Not taken: behind only; everything by default; `--only`
  required.
- **D4**: hold a conversation whose target directory is gone. Not taken: realign and warn;
  recreate the directory.
- **D5**: undo per conversation, the changed keys only, with the identity, ownership, folder and
  history checks. Not taken: byte pre-images; no undo beyond recover's `back`.
- **D6**: move the stale copy into the journal, with the transaction above. Not taken: leave it
  (the fallback); remove it.
- **D7**: leave row metadata. Not taken: refresh it from the live copy.
- **D8**: `converge`'s folder choice is a separate, later change. Not taken: fold it in; leave
  converge alone.
- **D9**: `doctor` lists single-copy conversations with a row that reaches no copy, not counted,
  and realign plans them. Not taken: list them before the probe; leave them until they split.

## Revisions

Round 1 (Codex, Gemini via agy, DeepSeek and Kimi on the Fireworks API, the last two reading
the repository), applied 2026-11-16:

- "Live" is now the copy holding every other copy's conversation, compared with every copy
  on disk, bookkeeping records set aside; it was the longest opened copy. The detection
  branch gained the same comparison (D0(b)), which turned 2 of 6 counted conversations on the
  measured store into same-conversation splits.
- Only rows behind or without a copy change; rows already opening the live copy, through
  `cwd` or the worktree placement, do not. A live copy reached only through the worktree
  placement holds the conversation until the probe's worktree phase.
- D2 no longer widens the write set on an inconclusive probe; it blocks instead. D5 is ruled
  after the probe, per conversation, with identity, ownership, folder and history checks.
- Path evidence: current scheme only, refused on disagreeing candidates or a missing
  directory. Identical targets: the most recently active row's folder.
- Unplaced conversations are planned (D3); diverged ones stay held and keep `doctor` at 1.
- D6's cost is stated correctly and the recommendation is now (b).
- Apply-time re-checks, exit codes, `--only`, `--json`, `--live`, value identity, the
  unreadable-row refusal and every journal dispatch point are now named.
- The probe gained enforced controls, a sweep control, necessity trials, verdicts, a typed
  turn, a worktree phase and a recoverable journal.

Round 2 (the same panel, with the round-2 instruction to report only new blockers and majors):

- Undo's history check now compares against what realign journaled, so an undo straight
  after realign succeeds; it refuses on turns gained on either side or an inconclusive
  comparison, not on the old copy having been behind.
- Every row of a planned conversation that does not open the target changes (behind,
  same-conversation or no copy), so the mixed case is no longer left split;
  `--include-identical` only decides whether a conversation with no copy behind is planned.
- Which copy a row opens is one shared rule, detection's; a probe result other than `cwd`
  changes it there first (D2).
- D6(b) is now specified as a transaction inside the op, with `move`'s containment, sidecar
  inventory and its own shape check, and undo restores the moved copy before any row; (a) is
  named as the fallback.
- A row that opens no copy is a flag on the conversation, planned whatever the copies'
  relation; rows with no copy do not vote on an identical target.
- `_journal_rows` joins the dispatch list, realign rows carry their own `store_path`, and a
  test lists every op-type branch. `--live` is converge's, at account scope, with its RULING 5
  refusal (or both can go). A live copy in a legacy-encoded folder is held, and says why. Exit
  codes match converge's: a dry run exits 0.
- The probe: a path key rewritten on open is recorded for D5 and no longer makes a key trial
  inconclusive; `record` checks the controls, as its description said.
- Undo's folder precondition compares with what realign journaled, so a row that opened no
  copy can be undone. Ties among copies holding the whole conversation use the recency rule,
  between folders, not rows. Path candidates are filtered to the target folder first.
- Ownership claims go by row path and journal order, not bytes; recover forward finishes
  from the journal without re-planning; undo keeps a status per conversation.
- D6(b) covers same-conversation copies and sidecars, and its operation is pinned against
  journal rotation.
- The probe changes only keys the row has (`add:` trials ask about absent ones), `sweep-b`
  catches the main repository's path on a worktree row, every `all-but:` runs when no key
  suffices alone, undo removes project folders the probe's strays created, and a new
  `empty-b` trial measures the copy-in on open. D9 is new: 64 single-copy conversations on the
  measured store have a row that reaches no copy.
- The probe: a copy counts as written to only when it was appended to (its baseline bytes
  unchanged and longer), a trial with no write is inconclusive, a typed trial needs a new user
  record with a `cwd`, and `init --session` (with a read-only `find`) selects a row whose one
  copy sits in a worktree folder, for phase 2.

Rejected: none. Deferred to the maintainer: the decisions above.

## Review record

Two rounds, 2026-11-16, each by Codex, Gemini via agy, and DeepSeek and Kimi on the Fireworks
API (the last two reading the repository); all four engines reported in both rounds. Every
finding was applied and none rejected, so the stopping rule (no unresolved blocker or major)
was met after round 2 and a third round was not run. D9 and the probe's `empty-b` trial were
added while applying round 2 and have not been reviewed.

## Implementation notes (2026-11-17)

Built test-first on the branch (`tests/test_realign.py`, 56 tests, written before the code
for the first 38). Where the build departs from the text above:

- **Recovery forward is all or nothing, not per conversation.** `classify_op` offers forward
  only when every pending row still holds its journaled pre-image and every pending copy is
  where the plan found it (or already in the journal); otherwise it offers `back` alone. The
  text says anything else "stops for that conversation". The build is the stricter of the two.
- **A row's old copy never stays behind today.** Under D3 and D6 either every row off the live
  copy moves, so every other copy goes into the journal, or only rows with no copy move. Undo's
  check that "the copy undo sends a row back to is unchanged or only gained bookkeeping"
  therefore runs on the journaled copy (its size, hash and sidecar inventory). The check on an
  old copy left in place is kept for a narrower rule later, and nothing reaches it now.
- **An interrupted undo resumes.** A conversation is marked `undoing` before its first write,
  and a later undo accepts the rows and copies already put back. The text did not cover this.
- **The GUI mirror is `_find_undoable_op`** (the text says `_find_undoable_sync`, its old
  name). `realign` is parked there: the window does not run it, so a completed realign on top
  means the window offers no undo, the way it treats a move.
- **The dispatch-point test** is `test_every_op_type_branch_names_realign_or_says_why_not`.
  It lists each function that compares an operation's type with a literal, and whether it
  names `realign` or why it need not.
- **The sidecar inventory is `move`'s own** (`sidecar_inventory`), so a symlink or junction
  inside a stale copy's sidecar holds the conversation at plan time. The plan names the files
  only the stale sidecar holds, the first five in text and all of them in `--json`.

End of the realign design.
