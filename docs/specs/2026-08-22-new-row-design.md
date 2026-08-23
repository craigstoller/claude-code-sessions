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
            [--anyway] [--live <substring>] [--apply] [--json] [--verbose]
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
| `cwd`, `originCwd` | read from the transcript's own records |
| `createdAt` | first record timestamp in the transcript |
| `lastActivityAt`, `lastFocusedAt` | last record timestamp |
| `completedTurns` | counted from the transcript |
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

Measured across the 169 orphans on this machine: **48 (28%) have a sibling branch with a row**
whose title could be inherited; 48 carry a `customTitle` record; **118 (70%) have neither.** The
app's own titles are model-written summaries — `Quarterly board report finalization` is not a
prefix of that conversation's opening line — so a good title cannot be derived mechanically.

In order:

1. `--title` if given.
2. The transcript's `customTitle` record if present.
3. A sibling row's title with the suffix **` (recovered)`** — and ` (recovered 2)`, ` (recovered 3)`
   and so on if a row with that exact title already exists in the target account. Not the app's own
   `(fork)` / `(fork 2)` convention, deliberately: the point is that an inherited title can never be
   mistaken for one the app named. Verbatim inheritance would manufacture exactly the ambiguity this
   project spent a day undoing — two rows, one title, different conversations.
4. A placeholder that does not impersonate a summary, in this exact shape:

   ```
   (untitled — 2026-06-14, 181 turns, Personal)
   ```

   The date is the transcript's last record in `YYYY-MM-DD`; the count is prose turns as
   `_message_fingerprints` counts them, so it agrees with every other turn count the tool prints;
   the final element is the **last path component** of `cwd`, not the whole path, which would push
   the useful parts off the end of a sidebar entry. Identifying, sortable, and visibly machine-made.

**The dry run prints the title and its provenance** — "from a sibling row", "from the transcript's
custom title", "placeholder". A sibling-derived title is trustworthy; a placeholder is an
admission. The report must not present them identically, because the user's decision differs.

## Refusals

- **Already reachable from this account** — if any row in the target store already opens that
  `cliSessionId`, refuse and name the row. `--anyway` overrides. Scope is deliberately *within one
  account*: several accounts each holding a row for the same conversation is the normal and
  desirable state.
- **`--to` names no transcript on disk** — refuse. A row pointing at nothing is what this command
  exists to clean up.
- **`--to` resolves to more than one transcript** — refuse, the same rule `_displaced_overlap`
  applies when `find_transcripts` returns several.
- **App running** — refuse (RULING 4). The app rewrites rows while it runs.
- **Identity files disagree** — refuse unless `--live` asserts which account is signed in
  (RULING 5).
- **Transcript unreadable** — refuse. `cwd` and the timestamps come from it, and a row built on
  values that could not be read is a guess wearing the app's clothes.

## Journal, undo, and the lock

Journalled as `op_type: "new-row"`, a single row, no pre-image — there is nothing to restore
because nothing existed.

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

- each refusal above, asserted on its message rather than only its type
- each title tier, and that the printed provenance names the right one
- the created row parses, carries the expected `cliSessionId`, and its `cwd` matches the transcript
- `--anyway` overrides the reachability refusal and nothing else
- `undo` removes the row; `undo` refuses once the row has drifted
- the lock is held across the write, asserted the way `check_apply_edges.py` does it

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
