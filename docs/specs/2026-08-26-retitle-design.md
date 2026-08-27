# `ccs retitle` — design

*2026-08-26. Status: approved, not yet implemented. Written as a handoff: the implementer is
assumed to know the codebase's conventions (`plan_*`/`execute_*`/`cmd_*`, the journal, RULING 4)
but none of the history below.*

## The gap

Every title in a sidebar is written by the app's summariser. Two conversations about the same work
therefore get the same sentence — measured on the machine this was written on, roughly **15 title
collisions accumulate per month** — and the only rename surface is the app's own UI, one row at a
time, in one account at a time. A conversation synced to three accounts needs the same manual
rename three times, and nothing checks the three stayed in step.

In August 2026 a cleanup renamed **69 rows across four passes** to get the `alignment` report's
`distinguishable` line to zero. No command existed, so the work went through a throwaway script,
and the script shipped **two real bugs** — each invisible at the time and found only by counting
afterwards:

- **Backups silently overwrote each other.** They were named `<timestamp>-<row filename>` at
  second resolution — and a row's filename is the app's session id, which `sync` preserves across
  accounts, so one conversation's three copies share a filename. 33 writes produced 20 backups.
- **`--undo` restored the newest run forever.** With one run that is invisible. With four stacked
  runs, undoing twice restored the same run twice and silently left the older three in place.

Both are the kind of defect the tool's existing op-log machinery already prevents for `move`,
`sync`, `repoint` and `new-row`. That is the argument for this command: not that renaming is hard,
but that renaming **outside** the journal has already failed twice in ways the journal is built to
stop.

## What is measured, and what the design leans on

- **The sidebar shows the row's `title`, and the app does not push the transcript's own
  `customTitle` back into it.** Of 133 rows checked whose `titleSource` was `auto`, 4 carried a
  title differing from their transcript's `customTitle`, and all 4 persisted.
- **A hand-set title with `titleSource: "user"` survives the app opening the session.** Verified
  on a live row: the app rewrote the row on focus (mtime moved) and left the title alone.
- **A rename touches exactly two fields** — `title` and `titleSource` — and the correct
  post-write check is *value-identical after parsing* on every other field, not byte-identity:
  the row is rewritten from a dict, so key order is the only thing allowed to move.
- **Op ids are second-resolution**, so "which op came later" is not decidable within a second.
  Any logic comparing this op to others must use exclusivity, not recency — the same rule
  `repoint` adopted after a flaky test proved 8-in-20 failures.

## Command surface

```
ccs retitle --only <title-or-session-id> --title "New name"
            [--store <substring>] [--live <substring>] [--apply] [--json] [--verbose]
```

- `--only` selects the conversation the way `repoint --only` does: a title substring or a
  `cliSessionId` prefix. It must resolve to exactly one conversation; anything else is a refusal
  that lists the candidates.
- **Default scope is every account whose sidebar holds the conversation.** The recurring need is
  "this conversation reads the same everywhere", and the failure mode of per-account renaming is
  three sidebars drifting apart — the thing the August cleanup spent four passes repairing.
  `--store` narrows to one account, taking the same matcher `new-row --store` uses (full store
  path when an email or account id is ambiguous; a guessed store is shown but `--apply` refuses
  to act on a guess).
- One conversation per invocation. The August cleanup's batch-TSV era was a backlog being
  cleared; the steady state is a handful of renames a month, each needing a thought-out name.
  A batch file was considered and deferred — it is where the one-title-for-two-rows bug came
  from, and nothing recurring needs it.

## The plan (dry run is the default)

The plan names, per account: the row file, the current title, and the new title. Two refusals are
computed at plan time and re-checked at apply time against the store as it then is:

- **The new title must not equal an existing title in any target sidebar** (exact match, after
  trimming). The entire point of renaming is the `distinguishable` line; a rename that creates a
  collision is the bug this command exists to fix, arriving through the front door. The refusal
  names the colliding row. No override flag — retitle the other row first if the name is truly
  wanted.
- **The row must still carry the title the plan showed.** A drift between plan and apply means
  the app or another op moved underneath; replan rather than overwrite blind.

## Writing

- `title` ← the new name; `titleSource` ← `"user"` (measured above: this is what makes it stick).
- Through `atomic_write`, under the operation lock, behind the RULING 4 running-app guard —
  planning is allowed while the app runs, applying is not.
- After each write, re-read and verify value-identity of every other field. A verify failure
  stops the op mid-flight with the journal holding what landed; `recover` classifies it like any
  other interrupted op.

## Journal and undo

One op record for the whole invocation, holding **the complete prior bytes of every row it
touched** — not the prior title, the prior bytes. The August scaffold could not restore
`titleSource` because it recorded too little, and the field turned out to be unreconstructable
(absent on some rows, `"auto"` on others, inconsistently within one conversation). Bytes make
undo exact by construction, including fields this spec has never heard of.

`ccs undo` reverses the newest completed op as usual: every row restored to its recorded bytes,
with the same store-drift refusal `sync`'s undo applies — if a row changed since the op, refuse
and say which row, rather than clobbering whatever changed it.

## Refusals

Beyond the two plan-time refusals above, the standard set: running app (RULING 4), identity-file
disagreement (`--live`, RULING 5), ambiguous `--only`, ambiguous `--store`, no row anywhere for
the conversation (that is `new-row`'s job, and the refusal should say so by name).

## Interactions

- `--anonymize --apply` is already refused globally; a retitle plan under `--anonymize` would
  substitute the very titles being discussed, so the plan output is the one place labels are
  expected to appear on both sides of the arrow.
- `alignment`'s `distinguishable` line is the before/after metric. The docs should say so: run
  it after a rename pass, expect no change or a decrease, never an increase.
- `doctor` needs no new check: a retitled row is an ordinary row, and the vanished-row check
  added for `new-row` does not apply because nothing new is created.

## Testing

Port the scaffold's lessons as tests, not as prose:

- Three accounts holding one conversation; a rename writes all three, and the op record holds
  three byte snapshots — count asserted, the way the scaffold learned to.
- Undo restores byte-exact rows, including one whose `titleSource` was absent — the case the
  scaffold lost.
- A plan whose new title collides with an existing row in one target sidebar refuses and names
  it — the one-title-for-two-rows bug, pinned.
- Apply-time drift: row title changed between plan and apply → refusal, nothing written.
- The `--store` guess (email matching several org directories) is refused at apply.
- Example titles in tests use the fake cast (`ACME-REVIEW`, `Northwind`, `Quarterly board report
  finalization`) — never a real title. The pre-push hook enforces this; do not make it the first
  line of defence.

## Known limits

- **The app can still rename over you.** `titleSource: "user"` survives focus today; that is a
  measurement, not a contract. If a future app build reasserts titles, `doctor`'s drift report
  is where it will show up.
- **No GUI surface in v1**, consistent with `move`.
- **The rename does not touch the transcript's `customTitle`** — the two are independent stores
  of the same idea, and this command owns only the row.
