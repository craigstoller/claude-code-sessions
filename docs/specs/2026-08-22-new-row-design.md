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
(account id, org id, path or email substring), same `--live` assertion for RULING 5, same
dry-run-unless-`--apply`, same RULING 4 running-app guard, same `--json` and `--verbose`.

`--store` defaults to **the account the identity files agree is signed in** — and refuses when they
disagree, exactly as RULING 5 requires. "Defaults to the signed-in account" was the first draft's
wording and it is wrong in the one state that matters: when the two files disagree there is no such
account, which is the whole reason `--live` exists.

`--to` takes a full `cliSessionId`. Not a substring: `repoint --only` accepts a substring because
it is matching against rows the user can see listed, while this names a transcript the user cannot
currently open, and a substring that matched two would be resolved against something invisible.

**Where the user gets that id.** By construction they are naming a conversation they cannot open,
so the command is useless without a discovery step and the docs must name it: `doctor` already
lists conversations no account points at, ranked, with ids that can be copied — that ranking and
the un-truncated ids were the whole subject of 0.9.11 and 0.9.12. `new-row` is the other half of
that feature, and its `--help` should say so rather than leaving the user to guess how to find an
id for something invisible.

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
| `cwd`, `originCwd` | the first `cwd` encountered reading the transcript **sequentially from the start**; both get the same value, since nothing in the transcript distinguishes them and inventing a difference would assert a relocation that never happened |
| `createdAt` | the timestamp of the **first record in sequential order** |
| `lastActivityAt` | the timestamp of the **last record in sequential order** |
| `lastFocusedAt` | the same value as `lastActivityAt` — **settled by measurement, see below** |
| `completedTurns` | prose turns as `_message_fingerprints` counts them, so it agrees with every other turn count the tool prints |
| `title`, `titleSource` | see below |
| `alwaysAllowedReasons`, `sessionPermissionUpdates` | `[]` |
| `spawnSeed` | `{}` |
| `spawnedFrom` | omitted entirely |
| remaining fields | documented defaults, listed in the implementation |

The exact template is part of the implementation and must be written out in one place with a
comment per field. **Unknown future fields are omitted**, not given a guessed default: a field this
tool does not understand, filled with a plausible-looking value, is the false assertion the
template exists to prevent. If measurement later shows the app requires one, it gets a documented
type-appropriate zero value and a note saying which app version made it necessary.

### Two derivation rules that were wrong, and how they were settled

**Sequential order, not minimum and maximum.** The first draft said `createdAt` should be the
*earliest* timestamp in the file "so a malformed tail cannot move the start". A reviewer pointed
out this is exactly backwards: scanning the whole file for a minimum is what lets a corrupted,
back-dated record at the tail pull the start time earlier. The first record's timestamp is what
resists that. Corrected above, and the same reasoning applied to `cwd`, where "first in file
order" was ambiguous between byte order and chronological order.

**`lastFocusedAt` is seeded, and the reason is measured rather than argued.** Three reviewers gave
three answers - omit it (asserting a focus that never happened), seed it (a missing value may sort
the row to the bottom of the sidebar, so the user concludes the command failed), and settle it
from the prototype. The prototype settles it. The row built on 2026-08-22 was written with
`lastFocusedAt` equal to the transcript's last activity, `1787334073357`. Read back after several
app restarts and one open, it holds `1787450348790` - **the app rewrote the field when the row was
focused**, while `lastActivityAt` still carries exactly what was written.

So the app owns this field and corrects it on first focus. Seeding is harmless because it is
transient, and it avoids the sort-order problem; omitting it would risk burying a recovered row for
no lasting gain. The objection that it asserts an event that did not happen is real and is
self-correcting within one open.

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
   `_message_fingerprints` counts them — user and assistant text blocks, whitespace-collapsed,
   truncated to 400 characters, excluding tool output and the app-emitted plumbing in
   `TRANSCRIPT_PLUMBING_PREFIXES` — so it agrees with every other turn count the tool prints; the
   final element is the **last path component** of `cwd`, not the whole path, which would push the
   useful parts off the end of a sidebar entry. **If that component is empty** — a `cwd` of `C:\`
   or `/` — the whole `, <component>` clause is dropped rather than emitted as a dangling comma.
   Identifying, sortable, and visibly machine-made.

**The dry run prints the title and its provenance** — "yours", "from the transcript's custom
title", "placeholder". A `customTitle` was written by a person about that conversation; a
placeholder is an admission that nothing was available. The report must not present them
identically, because the user's decision differs.

**Titles are checked for collision at every tier, not one.** If the resulting title already exists
on another row in the target account, the dry run says so and names that row. The rule per tier,
stated for all three rather than the two the first draft covered:

- **`--title`** — allowed, collision reported. You asked for that exact string.
- **`customTitle`** — treated as **generated**, so it gets ` (2)`, ` (3)` until unique. A person
  wrote it, but not *for this row*, and they were not asked whether a duplicate was acceptable.
  Silence is not consent when the user never saw the string.
- **placeholder** — generated, same suffixing. Two orphans from the same day, project and turn
  count would otherwise produce byte-identical titles.

Duplicate titles across accounts are untouched and normal — like the reachability refusal, the
check is within one account.

**The collision check runs inside the lock, immediately before the write** — not during the dry
run's planning. A uniqueness test performed before the mutex is a uniqueness test another process
can invalidate, which is the same class of stale-answer bug as the reachability re-check in 0.9.15.
The dry run may *report* a collision it saw; only the locked check decides the final title.

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
  message. A file that parses but yields **no `cwd`** or **no timestamp** cannot populate the
  template, and "unreadable" would be the wrong word for it. The refusal says which was missing.

  **Zero prose turns is NOT in that set.** The first draft lumped it in, and a reviewer was right
  that this confuses a policy choice with a technical constraint: `completedTurns` takes `0`
  perfectly well and the placeholder renders `0 turns` without complaint. A conversation of pure
  tool calls is a real thing and a row for it is a real row. It is allowed, and the dry run notes
  the count so the user can see what they are about to surface.

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

**The journal stores the FULL manifest, image included; the filter applies only to `--json`.**
Stating it because the opposite is an easy mistake with a nasty failure: filtering both paths would
leave `recover --forward` unable to reconstruct the row it is supposed to finish writing. The
journal is internal and already holds row images for `sync`; `--json` is the surface that reaches
logs and automation.

**The uuid is minted once, at plan time, and the dry run prints it.** The alternative - minting at
apply - means the id in a dry run's `--json` is not the id that gets written, so anything that
reads one and uses it later is silently wrong. Since the plan already carries the fully synthesized
row, planning is where the identity is fixed; applying writes what the plan showed. A plan whose
uuid somehow collides with an existing filename refuses at apply rather than overwriting.

## Scope this spec first missed: `recover` and `classify_op`

**Both dispatch on `op_type` with explicit allowlists, and neither knows `new-row`.** Verified in
the source: `recover_op` runs `_validate_manifest_paths` for any op whose type is not in
`("sync", "repoint")`, and that validator expects move-shaped keys - `source_transcript`,
`sidecar_inventory`, `row["path"]` - which a `new-row` manifest does not carry. `classify_op`
branches on `"repoint"` and `"sync"` and would fall through to the move path for anything else.

So a crashed `new-row` op would not merely be unrecoverable; it would fail inside a validator
written for a different shape, with a message about missing keys that says nothing about what
actually happened. The first draft of this spec asserted a crash-injection test would pass without
noticing that the code path it tests does not exist yet.

Part of this work, therefore:

- `recover_op` adds `"new-row"` to the set that skips the move-shaped validator.
- `classify_op` gains a `"new-row"` branch reporting the two real resolutions: **forward**
  completes the write, **back** deletes the row if it matches what was journalled. Its `note`
  says which, in the plain language the other two branches use.
- Both are covered by the crash-injection test rather than assumed.

## Testing

`tools/check_new_row.py`, following the house pattern of a synthetic store in a temp directory:

- each refusal above, asserted on its message rather than only its type — including the two
  distinct unreadable/unusable cases
- each title tier, and that the printed provenance names the right one
- title collision at every tier: a generated title gets ` (2)`, a user-supplied duplicate is
  allowed but reported
- **the created row against a field list written in the TEST, not read from the template** — a
  golden comparison whose expected value is stated independently. A reviewer caught the
  circularity in the first draft: a golden test that reads its expectations from the same template
  it is checking passes by construction, and cannot tell "correctly added" from "accidentally
  added". The test names every field and its expected value; adding a field to the template without
  adding it here is a failure, which is the point
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

**And rejection would be silent, with undo already spent.** A reviewer traced the interaction and
it is the nastiest thing in this design: if a future app tombstones a synthesized row on launch,
the row is gone, `undo`'s byte-identity test now finds nothing to match, and the user learns only
by noticing an absent sidebar entry. Two mitigations, both cheap, both part of this work:

- **Record which rows this tool synthesized**, in the journal it already keeps. Not a new store.
- **`doctor` reports a synthesized row that has since vanished**, naming the op that made it and
  the conversation it pointed at. That turns a silent disappearance into a line in a report the
  user already runs, and it is the only detection available without asking the app anything.

**The uuid is minted once at plan time** (see the journal section), so a fresh uuid4 colliding with
one the app later issues remains the only identity risk, and at 122 bits it is not a real one.
Recorded so nobody re-derives the question.

**A fresh uuid4 could in principle collide with one the app later issues.** At 122 bits of entropy
this is not a real risk and is recorded only so nobody re-derives the question. No mitigation is
warranted.

**This command creates clutter as easily as it repairs a gap.** It is the first command in the tool
that adds a sidebar entry from nothing, and 126 dead rows were pruned from these accounts the day
it was designed. The reachability refusal is the main guard; the placeholder title is the second,
because a row that announces it was machine-made is one a future cleanup can recognise.

## Review, and what was declined

**Panel: Codex, Gemini (agy) and DeepSeek (repo-aware) reported; Kimi INCOMPLETE (transport failure
after one retry, no review text, having read 428KB first).** The first pass ran Codex alone, which
was an unstated economy rather than a decision; the other three were run when that was questioned,
and two of the three findings that changed the design most came from them.

What the full panel changed:

- **The sibling title tier, removed on arithmetic the draft itself supplied** (Codex). 48 orphans
  with a sibling title, 48 with a `customTitle`, 45 of them the same — so the tier fires for 3 of
  169 cases. Verified against the disk before acting.
- **`createdAt` / `lastActivityAt` were derived backwards** (Gemini). The draft said "earliest
  timestamp, so a malformed tail cannot move the start", which is inverted — scanning for a minimum
  is what *lets* a back-dated tail record move it. This one was introduced while fixing a Codex
  finding, which is a good argument for running the whole panel rather than one engine.
- **`recover_op` and `classify_op` do not know `new-row`** (DeepSeek). Verified in the source: both
  dispatch on `op_type` against explicit allowlists, so a crashed `new-row` op would fail inside a
  validator built for `move`. The draft asserted a crash-injection test would pass against a code
  path that does not exist. Now scoped as part of the work.
- **`lastFocusedAt` settled by measurement rather than argument** (DeepSeek asked for it; three
  engines had three opinions). The prototype row's value was rewritten by the app on first focus,
  which answers it.
- Plus: `customTitle` collision behaviour specified, the collision check moved inside the lock
  (Gemini), the uuid minted at plan time so a dry run's `--json` id is the one that gets written
  (Gemini), the journal/`--json` filter asymmetry stated (DeepSeek), zero prose turns un-refused
  (Gemini), the placeholder's empty-path-component case handled (DeepSeek), `--store`'s default
  reworded for the RULING 5 state (DeepSeek), the golden test's circularity broken (DeepSeek), and
  `doctor` reporting a synthesized row that has since vanished (DeepSeek).

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
