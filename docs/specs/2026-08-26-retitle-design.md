# `ccs retitle` — design

*2026-08-26. Status: approved, not yet implemented. Written as a handoff: the implementer is
assumed to know the codebase's conventions (`plan_*`/`execute_*`/`cmd_*`, the journal, RULING 4)
but none of the history below. Revised same day after a two-engine review; see "Review" at the
end for what changed and what was declined.*

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
  Measured against desktop app **1.34493.1.0**; it is an observation about that build, not a
  contract — see Known limits.
- **A rename touches exactly two fields** — `title` and `titleSource`.
- **Op ids are second-resolution**, so "which op came later" is not decidable within a second.
  Any logic comparing this op to others must use exclusivity, not recency — the same rule
  `repoint` adopted after a flaky test proved 8-in-20 failures.
- **A row is small.** The census median is ~650 bytes, so journaling complete row preimages for
  a three-account rename costs about 2 KB per operation. Size is not a design constraint.

## Command surface

```
ccs retitle --only <title-or-session-id> --title "New name"
            [--store <substring>] [--live <substring>] [--apply] [--json] [--verbose]
```

**A "conversation" is a `cliSessionId`.** The target set is every row, in every account, whose
`cliSessionId` equals the resolved id. `--only` resolves to that id the way `repoint --only`
does — a title substring or a `cliSessionId` prefix — and must land on exactly one conversation.

**Resolving a collision by its title is expected to refuse the first time**, because a colliding
title matches several conversations by construction. The refusal is the workflow, not a dead end:
it lists every candidate with its session id, title, accounts and last activity, so the retry is a
copy-paste of the id. The spec calls this out because the command's main use case walks straight
into it.

- **Default scope is every account whose sidebar holds the conversation.** The recurring need is
  "this conversation reads the same everywhere", and the failure mode of per-account renaming is
  three sidebars drifting apart — the thing the August cleanup spent four passes repairing.
- **`--store` narrows to one account**, using the same matcher `new-row --store` uses (full store
  path when an email or account id is ambiguous; a guessed store is shown but `--apply` refuses
  to act on a guess). Its purpose is repair — one account's copy was renamed by the app or a
  person and the others were not. Because a one-account rename can *create* cross-account
  divergence, the plan under `--store` prints the sibling accounts' current titles and a warning
  when the rename would diverge from them. `alignment`'s `distinguishable` line counts per
  sidebar, so a `--store` rename cannot increase it, but it can leave the accounts reading
  differently — which the warning says.
- One conversation per invocation. The August cleanup's batch-TSV era was a backlog being
  cleared; the steady state is a handful of renames a month, each needing a thought-out name.
  A batch file was considered and deferred — it is where the one-title-for-two-rows bug came
  from, and nothing recurring needs it.

## Title validation

The stored value is the **trimmed** input, and every comparison below uses that same trimmed
form — the spec's first draft trimmed for comparison and stored the raw input, which is two
titles pretending to be one.

- Empty or whitespace-only after trimming: refused. An empty title can render a row unclickable
  in the app; nothing useful is behind allowing it.
- Newlines and C0 control characters: refused, not stripped — silently altering the name the
  user typed is how surprises ship.
- No length cap and no Unicode normalisation. The app imposes neither (measured: titles with
  em-dashes, arrows and 70+ characters exist and render); inventing constraints the app does not
  have would make this command refuse titles the app itself writes.

## The plan (dry run is the default)

The plan names, per account: the row file, the current title, and the new title. Refusals
computed at plan time and **re-checked at apply time against the store as it then is**:

- **The new title must not equal an existing title in any target sidebar** (trimmed exact
  match), **excluding the target conversation's own rows**. The exclusion matters twice: a
  case-only or punctuation fix must not collide with itself, and retitling to the *same* title
  is allowed because it still performs a useful write — pinning `titleSource` to `"user"` so the
  app stops resummarising. A collision with any *other* conversation refuses and names the
  colliding row; no override flag — retitle the other row first if the name is truly wanted.
- **The row must still carry the title the plan showed.** A drift between plan and apply means
  the app or another op moved underneath; replan rather than overwrite blind.
- **The target set itself is re-enumerated under the operation lock at apply.** A row added by
  `sync` or removed in the app between plan and apply changes what "every account" means; the
  apply refuses and asks for a replan rather than renaming the set the plan showed while
  reporting success against the set that now exists.

Rows that fail to parse already block all mutations (the existing unreadable-row refusal);
duplicate JSON keys inside a row are treated as unreadable at plan time rather than silently
collapsed by the parser.

## Writing, and what happens when it stops

Order is the contract:

1. **The op record is written and fsynced first**, holding the complete prior bytes of every
   target row and the planned new title. Nothing touches a row until the journal can already
   restore all of them.
2. Each row is rewritten through `atomic_write`, under the operation lock, behind the RULING 4
   running-app guard — planning is allowed while the app runs, applying is not.
3. After each write, re-read and verify: `title` equals the new name, `titleSource` equals
   `"user"`, and **every other field is value-identical after parsing** to the journaled
   preimage. (Value-identical, not byte-identical: the row is re-serialised from a dict, so
   representation may change; content may not.)
4. A completion marker closes the op.

**An interruption between 1 and 4 must not strand the sidebars half-renamed** — that is the
drift this command exists to remove, and the first draft of this spec allowed it. Both exits are
safe and both exist, because unlike `repoint`, neither direction can lose a conversation:

- `recover --back` restores every row that was written from its journaled preimage — the
  operation never happened.
- `recover --forward` completes the remaining writes from the journaled plan, re-running the
  apply-time checks first — the operation finishes.

`recover` reports the op as this command's and names both routes; `classify_op` treats a retitle
with rows landed and no completion marker exactly like the equivalent `sync` state.

## Journal and undo

One op record for the whole invocation, holding **the complete prior bytes of every row it
touched** — not the prior title, the prior bytes. The August scaffold could not restore
`titleSource` because it recorded too little, and the field turned out to be unreconstructable
(absent on some rows, `"auto"` on others, inconsistently within one conversation). Bytes make
undo exact by construction, including fields this spec has never heard of.

`ccs undo` reverses the newest completed op, consuming it so a second `undo` reaches the next
op — the stacked-runs failure from the scaffold, stated here as a requirement and pinned by a
test below. Undo is **all-or-nothing**: if any touched row changed since the op, the whole undo
refuses and names the row. A partial undo would leave the accounts disagreeing about the title,
which is the drift this command exists to remove; the refusal is the same shape as `sync`'s.

**Undo restores history even when history collides.** If some other row has adopted the old
title since, the byte-exact restore proceeds and the report says a collision now exists —
exact restoration outranks the distinguishability invariant, because undo's one job is to put
things back, and a silent partial restore in the name of tidiness is the scaffold's bug wearing
a new hat.

## Refusals

Beyond those above, the standard set: running app (RULING 4), identity-file disagreement
(`--live`, RULING 5), ambiguous `--only` (listing candidates with ids), ambiguous `--store`,
journal-write failure before any row is touched (nothing landed, nothing to recover), and no row
anywhere for the conversation — that is `new-row`'s job, and the refusal says so by name.

## Interactions

- **`--anonymize` is view-only here by construction**: `--anonymize --apply` is already refused
  globally, so an anonymized retitle plan exists only to be looked at or pasted. In that plan,
  both the current and the new title appear as labels — the current title through the standard
  substitution map, the new one as `<proposed-title>` since it exists nowhere yet. Candidate
  listings from an ambiguous `--only` are anonymized the same way. Nothing about this command
  weakens the flag.
- `alignment`'s `distinguishable` line is the before/after metric for default-scope renames:
  expect no change or a decrease. Under `--store` the line still cannot increase (it counts per
  sidebar), but cross-account agreement can — the plan's sibling warning covers it.
- **`new-row` derives titles from the transcript's `customTitle`, which this command does not
  touch.** Consequence, stated so nobody rediscovers it: if a retitled conversation later loses
  its row and is resurrected by `new-row`, the resurrected row carries the transcript's old
  `customTitle`, not the retitle. Accepted for v1; the alternative — writing into transcripts —
  mutates conversation history to fix a label, and is out.
- `doctor` needs no new check: a retitled row is an ordinary row.

## Testing

Port the scaffold's lessons and the review's findings as tests, not as prose:

- Three accounts holding one conversation; a rename writes all three, and the op record holds
  three byte preimages — count asserted, the way the scaffold learned to.
- **Fault injection after each write**: kill the op after row 1 of 3, then prove both
  `recover --back` (all preimages restored) and, separately, `recover --forward` (remaining
  writes completed) leave the three sidebars agreeing.
- **Stacked undo**: retitle A, retitle B, `undo`, `undo` — B's preimages restored first, then
  A's, both byte-exact, each op consumed. This is the scaffold's second bug, pinned.
- Undo restores byte-exact rows, including one whose `titleSource` was absent — the case the
  scaffold lost.
- A plan whose new title collides with another conversation's row refuses and names it; a plan
  whose new title equals the target's *own* current title proceeds and flips `titleSource`.
- Apply-time drift: row title changed between plan and apply → refusal, nothing written.
- Apply-time target-set drift: a row for the conversation appears in a new account between plan
  and apply → refusal, nothing written.
- Empty and control-character titles refused; the stored title is the trimmed input.
- The `--store` guess (email matching several org directories) is refused at apply; the
  `--store` sibling-divergence warning appears when and only when the siblings would differ.
- Example titles in tests use the fake cast (`ACME-REVIEW`, `Northwind`, `Quarterly board report
  finalization`) — never a real title. The pre-push hook enforces this; do not make it the first
  line of defence.

## Known limits

- **The app can still rename over you.** `titleSource: "user"` survives focus on desktop app
  1.34493.1.0; that is a measurement, not a contract. If a future build reasserts titles,
  `doctor`'s drift report is where it will show up — and the measurement should be re-run when
  the app updates, which the compatibility matrix already tracks for other behaviours.
- **This is a sidebar rename, and the help text should say so.** The transcript's `customTitle`
  is a second store of the same idea and is deliberately not touched; the `new-row` interaction
  above is the one place the difference is observable today.
- **No GUI surface in v1**, consistent with `move`.

## Review

Reviewed 2026-08-26 by two independent engines (Codex at `xhigh`, Gemini 3.1 Pro); both canaries
verified. Their overlapping findings reshaped the spec: the transactional order in "Writing"
(journal preimages before first write, both recovery directions defined) replaced a draft that
allowed a mid-flight stop to strand the sidebars half-renamed; undo became explicitly
all-or-nothing and op-consuming with a stacked test; the collision check now excludes the
target's own rows (case-only fixes, and same-title `titleSource` pinning); title validation,
apply-time target-set revalidation, the `--store` sibling warning, the `--only`-refusal-as-
workflow note, the `new-row`/`customTitle` interaction, and the app-version pin on the
persistence measurement all came from review findings.

Declined, with reasons: **removing `--store`** (one engine argued it recreates the drift the
command fights — but one-account repair is a real need, and the sibling warning makes the
divergence visible rather than forbidden); **dual-store renaming** (writing `customTitle` into
transcripts mutates conversation history to fix a label); **Unicode normalisation and length
caps** (the app imposes neither; a command stricter than the surface it manages would refuse
titles the app itself writes); **interactive disambiguation** (the candidate-listing refusal
already hands over the id, and this tool has no interactive prompts anywhere — consistency wins).
