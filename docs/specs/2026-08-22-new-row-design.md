# `ccs new-row` — design

*2026-08-22. Status: approved, not yet implemented.*

## The gap

A **row** is a pointer: a JSON file whose name is the app's session id and whose `cliSessionId`
field names the transcript it opens. Three commands act on rows today and none can make one.
`repoint` moves an existing row to a different conversation. `sync` copies a row from one account
to another. `move` relocates a session between project folders. Every one of them needs a row to
already exist.

That is a real limit, not a theoretical one. **169 transcripts on the machine this was written on
are reachable from no row in any account.** They are on disk, complete, and unopenable. The app
creates rows; nothing else did, so a conversation whose row was overwritten or never copied had no
route back.

It also caps what the tool can achieve. A session lineage accumulates a new transcript on every
resume while the app creates rows far more slowly, so lineages routinely have more branches than
rows. On 2026-08-22: hub A 6 branches / 2–3 rows, hub B 3 / 2, hub C 2 / 1. When
branches outnumber rows, "every account reaches every branch" and "nothing is lost" become
incompatible, and the choice has to be made by hand — which is how an evening got spent.

**The row shortage was assumed to be structural and is not.** Measured 2026-08-22: the store
directory holds only `local_*.json` files, `deleted_*` tombstones and `scheduled-tasks.json` — no
index, no manifest, no database. The app enumerates the directory. A row is 21 plain fields with
no signature or checksum. A hand-built row was installed in one account, and it appeared in the
sidebar, opened the correct conversation, and survived a restart.

## Scope

**One conversation, one account, one row.** Making a whole lineage reachable from every account is
a different command with different arguments, and it should be designed after this one has been
used a few times rather than guessed at now.

## Command surface

```
ccs new-row --to <cliSessionId> [--store <substring>] [--title "..."]
            [--live <substring>] [--apply] [--json] [--verbose]
```

Deliberately shaped like `repoint`, which is the nearest neighbour: same `--store` resolution
(account id, org id, path or email substring, defaulting to the signed-in account), same `--live`
assertion for RULING 5, same dry-run-unless-`--apply`, same RULING 4 running-app guard, same
`--json` and `--verbose`.

`--to` takes a full `cliSessionId`. Not a substring: `repoint --only` accepts a substring because
it is matching against rows the user can see listed, while this names a transcript the user cannot
currently open, and a substring that matched two would be resolved against something invisible.

## Field derivation

**Synthesized from a fixed template, never cloned from a sibling row.** The prototype built on
2026-08-22 cloned a sibling and had to strip `spawnedFrom`, which asserted descent from a chip task
that never happened. Cloning inherits whatever the donor carried — permission modes, MCP
configuration, Chrome tab state, worktree paths — and every one of those is a field the new row has
no business asserting. A documented template inherits nothing by accident.

| Field | Source |
|---|---|
| filename, `sessionId` | fresh uuid4; `sessionId` is `"local_" + <uuid>` |
| `cliSessionId` | `--to` |
| `cwd`, `originCwd` | the **first** `cwd` in file order; both get the same value, since nothing in the transcript distinguishes them and inventing a difference would assert a relocation that never happened |
| `createdAt` | the **earliest** `timestamp` in the file, not the first record's — records are appended in order but a malformed tail must not be able to move the start |
| `lastActivityAt` | the **latest** `timestamp` in the file, by the same reasoning |
| `lastFocusedAt` | **omitted.** The row has never been focused. Writing the transcript's last-activity time here would assert an event that did not happen, which is the exact thing the template exists to avoid — and the app's semantics for the field are unknown, so a guess could affect ordering or cleanup. If the app requires the key, `null`; that is a question for the implementation to settle against a real row, not for this spec to invent |
| `completedTurns` | prose turns as `_message_fingerprints` counts them, so it agrees with every other turn count the tool prints |
| `title`, `titleSource` | see below |
| `alwaysAllowedReasons`, `sessionPermissionUpdates` | `[]` |
| `spawnSeed` | `{}` |
| `spawnedFrom` | omitted entirely |
| remaining fields | documented defaults, listed in the implementation |

The exact template is part of the implementation and must be written out in one place with a
comment per field, so a future app version adding a field is a one-line diff rather than an
archaeology exercise.

## Title derivation

Titles are the safety-critical field. Every expensive mistake on 2026-08-22 traced to a row whose
title did not match what it opened: `(fork)` rows holding the *real* work in six sets, and a
`Employee handbook revision` pair where tidying by title alone would have deleted the month of
work and kept the dead end.

**A good title cannot be derived mechanically.** The app's own are model-written summaries -
`Quarterly board report finalization` is not a prefix of that conversation's opening line - and
measured across the 169 orphans on this machine, only **48 carry a `customTitle`** of their own.
The other **121 have nothing to derive from**, so for most of them the honest output is a
placeholder rather than a manufactured summary. (A third source was considered and rejected on the
numbers; see below.)

In order:

1. `--title` if given.
2. The transcript's `customTitle` record — the **first** one in file order if several exist.
3. A placeholder that does not impersonate a summary, in this exact shape:

   ```
   (untitled — 2026-06-14, 181 turns, Personal)
   ```

   The date is the transcript's last record in `YYYY-MM-DD`; the count is prose turns as
   `_message_fingerprints` counts them, so it agrees with every other turn count the tool prints;
   the final element is the **last path component** of `cwd`, not the whole path, which would push
   the useful parts off the end of a sidebar entry. Identifying, sortable, and visibly machine-made.

**The dry run prints the title and its provenance** — "yours", "from the transcript's custom
title", "placeholder". A `customTitle` was written by a person about that conversation; a
placeholder is an admission that nothing was available. The report must not present them
identically, because the user's decision differs.

**Titles are checked for collision at every tier, not one.** If the resulting title already exists
on another row in the target account, the dry run says so and names that row. A user-supplied
duplicate is allowed with the collision reported; a generated one gets ` (2)`, ` (3)` appended
until unique. Duplicate titles across accounts are untouched and normal — the check is
within one account, like the reachability refusal.

### A tier that was designed and removed

The first draft had a third tier between `customTitle` and the placeholder: inherit a sibling
branch's title with a ` (recovered)` suffix. A reviewer did the arithmetic the draft itself
supplied and it does not survive. Of 169 orphans on this machine, 48 have a sibling row's title
available and 48 have a `customTitle` — **and 45 of those are the same conversations.** Since
`customTitle` wins, the sibling tier fires for **3 of 169 cases, 1.8%.**

That does not justify a naming convention, a suffix scheme, and collision handling. Nor was its
premise sound: a sibling branch can have diverged substantially from the conversation it would be
naming, so "inherited from a sibling" is not the mark of accuracy the draft claimed it was — it is
provenance, which is a different thing. Recorded here because the tier looks obviously useful right
up until the three counts are added together, and the next person to design this will think of it
too.

## Refusals

- **Already reachable from this account** — if any row in the target store already opens that
  `cliSessionId`, refuse and name the row. Scope is deliberately *within one account*: several
  accounts each holding a row for the same conversation is the normal and desirable state.

  **`--anyway` was in the approved design and is dropped.** It contradicted the invariant one
  section above it — "one conversation, one account, one row" — and no use case survived being
  asked for. If a row already opens the conversation and is wrong, `repoint` fixes it; if it is
  dead, delete it in the app. A second door to the same conversation in one sidebar is the clutter
  this spec's own known-limits section warns the command will create. Add the flag if a real case
  turns up, with the case written down.
- **`--to` names no transcript on disk** — refuse. A row pointing at nothing is what this command
  exists to clean up.
- **`--to` resolves to more than one transcript** — refuse, the same rule `_displaced_overlap`
  applies when `find_transcripts` returns several.
- **App running** — refuse (RULING 4). The app rewrites rows while it runs.
- **Identity files disagree** — refuse unless `--live` asserts which account is signed in
  (RULING 5).
- **Transcript unreadable** — refuse. `cwd` and the timestamps come from it, and a row built on
  values that could not be read is a guess wearing the app's clothes.
- **Transcript readable but not usable** — refuse, separately from the above and with a different
  message. A file that parses but yields no `cwd`, no timestamp, or zero prose turns cannot
  populate the template, and "unreadable" would be the wrong word for it. The refusal says which
  of the three was missing.

## Journal, undo, and the lock

Journalled as `op_type: "new-row"`, a single row, no pre-image — there is nothing to restore
because nothing existed.

**Ordering: journal first, then write.** The manifest carrying the synthesized row is committed to
the journal before the row file is created, so the only crash-visible states are "journalled, not
written" — which `recover --forward` completes and `--back` closes — and "journalled and written",
which is the finished state. Writing first would allow a row on disk that no op knows about, and
nothing in the tool could then find it to undo it. This is the ordering `run_sync` already uses.

**The row is written with `atomic_write`, like every other row this tool creates**, so a crash
mid-write cannot leave a half-serialized JSON file where the app expects a row. The filename is a
fresh uuid4 and is checked for absence before writing; a collision means something is badly wrong
and refuses rather than overwriting.

`undo` deletes the row **only if it is byte-identical to what the op wrote**. That is not a new
rule: `undo_sync` already applies exactly this test to rows a sync ADDED, refusing when the
destination account has since opened the session and the app rewrote the row. Reusing the rule
means `new-row` inherits its reasoning and its tests rather than inventing a second deletion path.

The operation lock needs no change. `_lock_path` is one file per journal directory created
`O_CREAT|O_EXCL`, and every mutating entry point already competes for it; `new-row` takes it the
same way, releasing in a `finally`.

`--json` prints the manifest through a `_public_new_row_manifest` filter, matching
`_public_repoint_manifest` and `_public_manifest`. The rule those enforce carries over unchanged:
**no row images in `--json` output.** A `new-row` manifest has no `pre_b64` and its `post_b64` is
a row this command just synthesized rather than one copied out of an account, but it still names a
`cwd` and a title, so it goes through the same filter rather than around it.

## Testing

`tools/check_new_row.py`, following the house pattern of a synthetic store in a temp directory:

- each refusal above, asserted on its message rather than only its type — including the two
  distinct unreadable/unusable cases
- each title tier, and that the printed provenance names the right one
- title collision at every tier: a generated title gets ` (2)`, a user-supplied duplicate is
  allowed but reported
- **the created row against the full field list**, not a sample of it — a golden comparison, so a
  field silently dropped or renamed fails the test rather than passing it
- `lastFocusedAt` is not fabricated
- the malformed and empty states: zero records, no `cwd`, no timestamps, zero prose turns,
  several `customTitle` records (first wins)
- `undo` removes the row; `undo` refuses once the row has drifted; `undo` reports usefully when
  the row is already absent
- crash injection between the journal commit and the row write, asserting `recover --forward`
  completes and `--back` closes
- the lock is held across the write, asserted the way `check_apply_edges.py` does it
- `--json` carries no row image, asserted by scanning the output for the row's bytes rather than
  by checking a key is absent

## Known limits, to be stated where users read them

**The app tolerating a row it never issued rests on one experiment.** On 2026-08-22 a hand-built
row appeared in the sidebar, opened the right conversation, and survived a restart. That is
evidence, not a guarantee. A future app version could validate rows against something this tool
cannot see, and the failure mode would be a row that silently does not appear — or, worse, one the
app tombstones. The README and `--help` should say the capability is unofficial and unverified
across app versions, in the same voice the rest of the tool uses about the format it reads.

**A fresh uuid4 could in principle collide with one the app later issues.** At 122 bits of entropy
this is not a real risk and is recorded only so nobody re-derives the question. No mitigation is
warranted.

**This command creates clutter as easily as it repairs a gap.** It is the first command in the tool
that adds a sidebar entry from nothing, and 126 dead rows were pruned from these accounts the day
it was designed. The reachability refusal is the main guard; the placeholder title is the second,
because a row that announces it was machine-made is one a future cleanup can recognise.

## Review, and what was declined

Reviewed by Codex before implementation; agy and the roster were not run. Six findings changed the
spec: the sibling tier removed on its own arithmetic, `--anyway` dropped, `lastFocusedAt` no longer
fabricated, collision detection widened to every tier, journal-before-write ordering stated, and
the derivation rules pinned to specific records rather than "the transcript's own".

Two were declined, and the reasons belong here rather than being silently dropped:

- **"Fail closed unless the observed store schema / app version is known compatible."** There is no
  version signal to gate on. The store carries no schema marker, and the app's version lives
  somewhere this tool does not read. More fundamentally, the whole tool is built on reading an
  undocumented format whose shape was established by measurement - `docs/internals.md` is a
  catalogue of exactly that, each entry dated and marked "may change". A compatibility gate here
  would be a promise the rest of the tool does not make and could not keep. What is warranted is
  the loud statement of the limit, which the section above already requires.

- **"Cloud reconciliation could upload, reject or resurrect a synthetic row."** No evidence of any
  server round-trip for these files. They are named `local_*.json`, they live under the desktop
  app's local cache, and nothing observed in a day of watching them move suggested anything else
  writes them. Recorded as unexamined rather than ruled out: if a future observation shows a sync
  path, this needs revisiting before anything else in the spec does.
