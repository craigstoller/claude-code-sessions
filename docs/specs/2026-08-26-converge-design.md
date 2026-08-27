# `ccs converge` — design

*2026-08-26. Status: approved, not yet implemented. Written as a handoff: the implementer is
assumed to know the codebase's conventions (`plan_*`/`execute_*`/`cmd_*`, the journal, RULING 4)
but none of the history below. Revised the same evening after a two-engine review; see "Review"
at the end for what changed and what was declined.*

## The gap

`sync` copies rows **from the account you are signed into** into one other account. It has no
`--from`: the source is always the live account, so making one conversation reachable from every
account means signing into each in turn, with the desktop app closed for every write and reopened
to switch.

The actual recurring need is narrower and different: **"every conversation some sidebar can open
should be openable from every sidebar."** In one week on the machine this was written on, that
chore came up three times and was done three times by generating a shell script of `new-row`
calls — 28, then 30, then a pending ~22 — because no command said it in one word. The scripts
worked, and they are the argument for the command: each had to rediscover the same four things
(full store paths, because an account id matches several org directories; explicit titles,
because derived ones collide; a tolerant-rerun wrapper, because a refusal for "already there" is
success on a rerun; and a per-call journal, because there was no batch op). A command owns all
four once.

It also drifts back on its own. New sessions exist only where they were created, so `alignment`'s
`complete` line decays with every burst of work in one account — measured: 21 conversations went
short within two days of full convergence, all of them ordinary new work. This is a monthly-plus
chore, permanently.

## Keys, eligibility, and the target set

Stated normatively, because the review found every one of these implicitly assumed:

- **ConversationKey is the `cliSessionId`.** Multiple rows in one account pointing at the same
  conversation are one holding — the account reaches it, so it is not a missing pair, and the
  extra rows are untouched.
- **Transcripts are machine-global** (one `~/.claude/projects` tree serves every account), so
  "transcript exists" is a property of the conversation, never of a holder. A dead row in one
  account does not block a conversation whose transcript exists.
- **A conversation is eligible** iff its transcript is on disk AND at least one account holds a
  row for it. Dead-only groups (rows everywhere, transcript gone) are excluded from the work and
  from the completeness denominator — they are `doctor`'s dead-row report, not convergence.
  Transcripts with no row anywhere are equally out: retired stays retired; first rows are
  `new-row`'s job.
- **StoreKey: an account's destination store is the org directory that already holds that
  account's rows.** Measured on this machine, each account has three org directories and exactly
  one is populated; the populated one is the account's real store, and that is the same evidence
  `sync`'s candidate listing uses. An account whose every org directory is empty is **not a
  destination** — there is no evidence which org is real, and writing into a guessed one is the
  exact move `new-row --apply` refuses. The plan lists each destination as `email (acct/org)` so
  the resolution is visible before anything is written.
- **The target set** is every (eligible conversation, destination account) pair where that
  account holds no row for it.

## What converge is, and refuses to be

**Purely additive.** For every pair in the target set, create a row. Nothing else:

- **Never repoints.** Row files whose accounts disagree about what they open stay as they are.
  Measured 2026-08-24 across four such disputed groups: both sides held real unique work
  (unique-turn splits of 9/14, 12/19, 60/48, 28/60), so "pick a winner" destroys by construction.
- **Never refreshes.** A row that exists in the destination is left byte-for-byte alone, however
  stale its snapshot fields. Refresh is `sync --update`'s job and carries `sync`'s own guards.
- **Never deletes, never resurrects.**

The asymmetry with `sync` is the point. `sync` moves the live account's *state* outward and
therefore needs the live account, the learned-email memo, and the claim protocol. Converge
synthesizes presence from **any** holder, copies no per-account state, and overwrites nothing —
which is why it may read every store and write every store in one pass without the sign-in dance.

**Presence, not agreement — including titles.** Copies of one conversation can carry different
titles across accounts, and converge's new rows take one of them (below) without touching the
others. That extends the chosen title's reach while the minority copies keep theirs; converge
does not hide this — the plan flags every title disagreement it sees and prints the
ready-to-paste `retitle` command that would level it. Renaming existing rows is `retitle`'s job,
with `retitle`'s guarantees; a converge that also renamed would be an overwrite path in a command
whose whole safety case is having none.

## Command surface

```
ccs converge [--only <title-or-session-id>] [--live <substring>]
             [--apply] [--json] [--verbose] [--anonymize]
```

- No `--to`, no `--from`, no `--store`. Naming a direction is the interface converge exists to
  delete; the target set is derived.
- `--only` narrows to one conversation, resolved the way `retitle --only` is: title substring or
  `cliSessionId` prefix, exactly one match, ambiguity refuses with a candidate list.
- One journal op for the whole run, however many rows it creates.
- The tool's premise, stated rather than assumed: **every discovered account belongs to the
  operator.** That is already the premise of `sync`, `repoint --store` and `new-row --store`;
  converge adds fan-out, not a new trust boundary. The per-destination `email (acct/org)` lines
  in the plan are where an operator with accounts that must not mix would see it before `--apply`.

## The plan

Grouped by destination account, one line per row to create: the conversation, the title it will
carry, and which accounts already hold it. The plan ends with truthful completeness math:

```
complete : 337 of 359  ->  356 of 359   (3 held)
```

— projected from the pairs that will actually be written, never a promised `<m> of <m>`. Under
`--only` the line is scoped and says so.

**Title: the holder whose row has the greatest `lastActivityAt`.** That field is a snapshot of
the conversation's own activity, not of any account's attention (`lastFocusedAt` is, and is not
used). Missing values compare as zero; an exact tie breaks to the lexicographically greatest
account uuid — arbitrary, deterministic, stated. When holders' titles disagree, the plan says so
on that line and prints the levelling `retitle` command. A non-colliding minority title is **not**
substituted when the chosen title collides (below): canonical consistency outranks maximising
placements, because a placement under a minority title deepens the very fragmentation the
disagreement note exists to surface.

**Holds — reported, not silent, not fatal to the rest.** A pair is held, with a reason, when
creating the row would damage the destination sidebar; other pairs proceed:

- `held_title_collision` — the chosen title is already carried by a **different** conversation
  in the destination sidebar. Creating the row would recreate the duplicate-title problem this
  store spent a cleanup removing. `new-row` treats an explicit-title collision as a printed
  warning and proceeds (measured — its `title_collision` field is informational); converge holds,
  because converge is bulk and unattended where `new-row` is single and watched. The hold names
  the colliding row and prints the `retitle` command that clears it. Deliberately **no
  auto-suffix**: a store full of generic titles would mass-hold, and that is converge refusing to
  spread a mess rather than converge breaking — the holds arrive with their fixes attached.
- The title comparison is **the same comparator `alignment` uses** for its duplicate-title
  grouping (trimmed exact match) — one named function, shared and tested, so "the collision hold
  guarantees `distinguishable` does not move" is true by construction rather than by parallel
  implementations agreeing.

## Applying

Order — **every precondition before anything durable**, because journalling first would let an
ordinary refusal strand an open op for `recover` to clean up:

1. The operation lock is acquired; RULING 4 (running app) and RULING 5 (identity disagreement,
   `--live`) pass; the plan's stores are re-discovered and still resolve. Only then:
2. **The op record is journalled and fsynced**, listing every planned row: destination store
   path, row filename (uuid minted at plan time), the complete bytes to write, and the holds.
3. Per pair, revalidated against the store as it now is: the destination still lacks a row for
   the conversation (one appearing since — the app, a sync, another converge — makes that pair an
   `already_present` skip, not an error), and the hold checks re-run. A pair that held at plan
   time is never written even if the collision has since cleared — clearing it changed the
   sidebar, so the user replans rather than the tool guessing.
4. Rows are written through `atomic_write`. Synthesis reuses `new-row`'s derivation
   (`_transcript_facts`, `NEW_ROW_DEFAULTS`, `_synthesize_row`) with the title forced to the
   plan's choice. Converge copies **no per-account fields** from the holder — not focus times
   (false recency in an account that never opened it), not archive flags (the holder's shelving
   decision, not the conversation's), and there is no pin field to copy: the 21-field row census
   contains none. Sidebar ordering in the destination comes from the activity times synthesis
   derives from the transcript, which is the honest signal.
5. A completion marker closes the op. If the plan yields zero writes (store already complete, or
   everything held/skipped), **no op is created at all** — nothing durable happened, there is
   nothing to undo, and the run reports and exits without journal residue.

**Exit status is truthful about the postcondition**: `0` when every planned pair applied or was
`already_present`; a documented partial code (`3`) when holds remain, because "bulk and
unattended" is exactly where a status code gets trusted without reading prose. The holds are in
the report and `--json` either way.

**Interrupted mid-run**, both exits are safe and both exist: `recover --back` deletes the rows
the op created (they are pointers; the conversations keep their rows elsewhere), and
`recover --forward` **is a fresh evaluation of the remaining pairs** — it re-runs step 1's guards
and step 3's checks against the store as it stands, so a pair whose situation changed in the
window resolves against reality rather than against the plan's snapshot. That is intended, and
stated so recovery is not read as replay. `classify_op` treats a converge with rows landed and no
completion marker like the equivalent `new-row` state. Landed-versus-unattempted is decidable
from the op record's planned list against the disk; held/skipped pairs need no durable record
precisely because forward recovery re-evaluates.

## Undo

`ccs undo` of a completed converge removes the rows the op created — **forgiving, not
all-or-nothing**, the deliberate opposite of `retitle`'s rule: retitle's undo restores prior
states that must agree across accounts, while converge's undo deletes independent creations whose
prior state is uniformly *absent*. Per created row:

- **Never delete the last pointer.** If this row is now the only row in any account reaching the
  conversation (the holders it was copied from have since been removed), skip it and say so —
  undo's job is to remove redundant presence, and this presence is no longer redundant.
- If its `cliSessionId` changed, skip and say so: someone repointed it, and deleting would
  destroy their work.
- If its `title` or `titleSource` changed, skip and say so: someone curated it, and a curated row
  is theirs now. (A moved `lastFocusedAt` alone is not curation — the app writes that on focus.)
- Otherwise delete. Already gone counts as done.

The report tallies deleted / skipped-by-reason / already-gone, and the op is consumed either way.

## Refusals

The standard set, all **before** the op record exists: running app (RULING 4), identity
disagreement (RULING 5, `--live`), ambiguous `--only`, unreadable row anywhere (existing global
refusal), a destination store that no longer resolves. Journal-write failure aborts with nothing
durable. A write failure mid-run (disk full, permissions) leaves the op open for `recover`, like
every other mutating command; the completion marker failing to write is the same state and the
same remedy. **Nothing to do is not an error**: a complete store plans zero rows, prints the
`complete` line, and exits 0.

## Interactions

- `alignment` is the before/after: `complete` rises by exactly the applied count;
  `distinguishable` cannot move (shared comparator, above); `consistent` untouched.
- `retitle` is the remedy every hold and every disagreement note prints, by name, as a pastable
  command.
- **`doctor`'s vanished-row check covers converge-created rows**: the op records created rows in
  the same shape `new-row` ops do, so the existing check extends without new machinery. At
  converge's fan-out, a low per-row chance of the app rejecting a row becomes worth watching —
  this reverses an earlier draft's deferral, on the review's argument.
- The app absorbs new rows by directory enumeration — measured: a hand-built row appeared in the
  sidebar and survived restart — and RULING 4 means the app is **closed** during every write, so
  there is no live-watcher scale concern by construction.
- `--anonymize` composes as everywhere: plan-only by construction, fields substituted before
  formatting.

## Testing

- Three accounts, mixed holdings; the plan enumerates exactly the missing pairs; apply levels the
  counts; `alignment`'s `complete` rises by the applied count; `distinguishable` unchanged.
- StoreKey: an account with several org directories receives its row in the populated one; an
  account with no populated org directory is not a destination and the plan says so.
- Duplicate rows for one conversation in one account: counted as one holding, no pair generated.
- Title choice: disagreeing holders -> greatest `lastActivityAt` wins; missing values and exact
  ties resolve deterministically; the disagreement note and its `retitle` command appear.
- `held_title_collision`: destination holds a different conversation under the title -> held and
  named; everything else applies; **exit 3**; a non-colliding minority title is not substituted.
- Dead-only conversation (transcript gone): excluded from work and denominator both.
- Apply-time skip: a row appears in the destination between plan and apply -> `already_present`,
  exit unaffected.
- Guard ordering: `--apply` with the app running refuses **before** any op record exists — no
  journal residue, nothing for `recover`.
- All-held and all-`already_present` applies: no op record, no undo entry, correct exits.
- Fault injection after row 1 of N: `recover --back` leaves no trace; `recover --forward`
  re-evaluates — including a pair whose destination gained a row in the window (skip) and one
  whose hold cleared in the window (still not written).
- Undo: deletes created rows; skips the last-pointer case, the repointed case, and the
  retitled-since case, each named; op consumed; a second `undo` reaches the previous op.
- Completion-marker write failure: op left open, `recover` classifies it, report says so.
- Example titles from the fake cast (`ACME-REVIEW`, `Northwind`, `Quarterly board report
  finalization`) — never real titles. The pre-push hook enforces this; do not make it the first
  line of defence.

## Known limits

- **Converge spreads presence, not agreement.** Divergent titles and disagreeing row files
  survive a run untouched; the plan surfaces both, with the fix printed, and `retitle` owns it.
- **The claim protocol.** Converge-created rows do not participate in `sync`'s claim protocol —
  and the mechanism argument, not just the `new-row` precedent: claims key on row-file identity,
  and converge mints a fresh uuid for every row it creates, never reusing or copying an existing
  row file, so a claim collision has nothing to collide on. If the protocol ever grows beyond
  file identity, revisit.
- **No GUI surface in v1**, consistent with `move` and `retitle`.
- **`sync --from` was considered and declined.** Bolting a dormant-store source onto `sync`
  doubles the state space of the one command that overwrites (its claim protocol, learned-email
  memo, and `--update` routes all assume a live source), to serve a need that is additive by
  nature. Converge covers the recurring chore with no overwrite path at all; if a true
  "copy per-account state from a dormant store" need ever materialises, it should be its own
  argued design, not a flag.

## Review

Reviewed 2026-08-26 by two independent engines (Codex at `xhigh`, Gemini 3.1 Pro); both canaries
verified. What changed on their findings: the **"Keys, eligibility, and the target set"** section
exists because every one of its definitions was implicitly assumed (StoreKey was the
implementation-blocking one — an account id matching several org directories is the exact trap
the old scripts dodged with full paths); **guards now precede the journal**, because the drafted
order let an ordinary RULING 4 refusal strand an open op; **undo gained the last-pointer and
curated-row skips**, closing a path where undoing a converge could make a conversation
unreachable everywhere or delete someone's rename; completeness math became truthful
(`n of m -> k of m (h held)`, never a promised full house); **exit 3 for a run with holds**,
because unattended is where status codes are trusted; forward recovery is defined as fresh
re-evaluation, guards included; the title rule names its field, its missing-value and tie
behaviour, and why a non-colliding minority title is not substituted; the collision comparator is
`alignment`'s own, shared; the vanished-row `doctor` check now covers converge rows, reversing
the draft's deferral on the reviewers' scale argument; the claim-protocol deferral carries a
mechanism argument instead of a precedent; arrows in this file are ASCII because one engine's
console mangled the UTF-8 ones, and the next reader may be a terminal too.

Declined, with reasons: **copying the holder's full row state** (one engine's highest-impact
fix) — focus times would fake recency in accounts that never opened the conversation, archive
flags are the holder's shelving decision, and the pin field the concern rested on does not exist
in the 21-field row census; **auto-suffixing colliding titles** — a `(2)` is a collision avoided,
not a name, and mass-holding on a store full of generic titles is refusal working as designed;
**an account/store allowlist** — `--only` scopes, the operator-owns-all-accounts premise is the
tool's existing one, and the plan shows every destination before `--apply`; **holding when
holders' titles merely disagree** — a reachable conversation under a slightly-stale name beats an
unreachable one, and the disagreement ships with its fix printed.
