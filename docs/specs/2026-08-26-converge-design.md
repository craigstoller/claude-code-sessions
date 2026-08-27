# `ccs converge` — design

*2026-08-26. Status: approved, not yet implemented. Written as a handoff: the implementer is
assumed to know the codebase's conventions (`plan_*`/`execute_*`/`cmd_*`, the journal, RULING 4)
but none of the history below.*

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

## What converge is, and refuses to be

**Purely additive.** For every conversation reachable from at least one account, create a row in
each account that lacks one. Nothing else:

- **Never repoints.** Row files whose accounts disagree about what they open stay as they are.
  Measured 2026-08-24 across four such disputed groups: both sides held real unique work
  (unique-turn splits of 9/14, 12/19, 60/48, 28/60), so "pick a winner" destroys by construction.
- **Never refreshes.** A row that exists in the destination is left byte-for-byte alone, however
  stale its snapshot fields. Refresh is `sync --update`'s job and carries `sync`'s own guards.
- **Never deletes, never resurrects.** A transcript with no row in *any* account is invisible to
  converge — deliberately. Conversations knowingly retired (rows removed everywhere) stay
  retired; giving orphans their first row is `new-row`'s job, informed by `doctor`.

The asymmetry with `sync` is the point. `sync` moves the live account's *state* outward and
therefore needs the live account, the learned-email memo, and the claim protocol. Converge
synthesizes presence from **any** holder, copies no per-account state, and overwrites nothing —
which is why it may read every store and write every store in one pass without the sign-in dance.

## Command surface

```
ccs converge [--only <title-or-session-id>] [--live <substring>]
             [--apply] [--json] [--verbose] [--anonymize]
```

- No `--to`, no `--from`, no `--store`. The target set is derived: every
  (conversation, account) pair where the conversation has a row in some account and none in that
  one. Naming a direction is the interface converge exists to delete.
- `--only` narrows to one conversation, resolved the way `retitle --only` is: title substring or
  `cliSessionId` prefix, exactly one match, ambiguity refuses with a candidate list.
- One journal op for the whole run, however many rows it creates.

## The plan

Grouped by account, one line per row to create: the conversation, the title it will carry, and
which accounts already hold it. The plan ends with the number that is the entire point:
`complete: <n> of <m> -> <m> of <m>`, the same figure `alignment` reports.

**Title: the most recently active holder's.** Copies of one conversation can carry different
titles (the app resummarises per account). The newest holder's title wins; when holders disagree
the plan says so on that line, because the durable fix is `retitle`, not converge. Converge never
invents a title: unlike `new-row` it always has at least one existing row to copy from.

**Holds — reported, not fatal.** A pair is held back, with a reason, when creating the row would
damage the destination sidebar; the rest of the plan proceeds. Two hold reasons exist:

- `held_title_collision` — the title (trimmed exact match) is already carried by a **different
  conversation** in the destination sidebar. Creating the row would recreate the duplicate-title
  problem this store spent a cleanup removing. `new-row` treats an explicit-title collision as a
  printed warning and proceeds (measured — `title_collision` is informational); converge holds,
  because converge is bulk and unattended where `new-row` is single and watched. The hold names
  the colliding row and the remedy: `retitle` one of them, re-run converge.
- `held_transcript_missing` — no transcript on disk for the conversation (a dead row in the
  holder). Creating a pointer to nothing manufactures a dead row; `doctor` already reports the
  holder's.

Holds appear in the report and in `--json`, each with conversation id, account, reason, and the
colliding row where applicable. **A run with holds still exits 0 when everything else applied** —
the holds are the report's job, not a failure; the scripts this replaces could not distinguish
"held for a reason" from "failed" at all.

## Writing

Order, per the retitle precedent:

1. **The op record is journalled and fsynced first**, listing every planned row: destination
   store path, row filename (uuid minted at plan time), and the complete bytes to write.
2. Rows are written through `atomic_write` under the operation lock, behind the RULING 4 guard —
   plan free while the app runs, apply refused. RULING 5 (`--live`) applies unchanged when the
   identity files disagree.
3. **Apply-time revalidation, per pair, against the store as it then is**: the destination still
   lacks a row for the conversation (one appearing since — the app, a sync, another converge —
   turns that pair into a skip, reported as `already_present`), and the hold checks re-run.
   A pair that held at plan time is never written even if the collision has since cleared —
   clearing it changed the sidebar, so the user replans rather than the tool guessing.
4. Row synthesis reuses `new-row`'s derivation (`_transcript_facts`, `NEW_ROW_DEFAULTS`,
   `_synthesize_row`) with the title forced to the plan's choice. One deliberate difference from
   `sync`: converge does **not** copy the source row's per-account fields (pins, flags, focus
   times) — those are the holder's account state, not the conversation's. The row is born the way
   `new-row` births one.
5. A completion marker closes the op.

Interrupted mid-run, both exits are safe and both exist: `recover --back` deletes the rows the op
created (they are pointers; the conversations keep their existing rows elsewhere), and
`recover --forward` finishes the remaining writes after re-running step 3's checks. `classify_op`
treats a converge with rows landed and no completion marker like the equivalent `new-row` state.

## Undo

`ccs undo` of a completed converge removes every row the op created — **forgiving, not
all-or-nothing**, the opposite of retitle's rule, for a stated reason: retitle's undo restores
prior states that must agree across accounts, while converge's undo deletes independent creations
whose "prior state" is uniformly *absent*. Per row: if it still points at the conversation the op
created it for, delete it (a moved `lastFocusedAt` from the app is not drift — the pointer is the
identity); if its `cliSessionId` has changed, skip it and say so (someone repointed it; deleting
would destroy their work); if it is already gone, count it done. The report tallies deleted /
skipped / already-gone, and the op is consumed either way.

## Refusals

The standard set: running app (RULING 4), identity disagreement (RULING 5, `--live`), ambiguous
`--only`, unreadable row anywhere (existing global refusal), journal-write failure before any row
lands. And one of its own: **nothing to do is not an error** — a store already complete plans
zero rows, prints the `complete` line, and exits 0, because converge is meant to be run casually
and often.

## Interactions

- `alignment` is the before/after: `complete` reaches `<m> of <m>`; `distinguishable` must not
  move (the collision hold is what guarantees it); `consistent` untouched.
- `retitle` is the remedy converge's holds point at, by name.
- `doctor` untouched: rows born here are ordinary rows; the vanished-row check `new-row` grew
  does not extend to converge in v1 (a converge op can create dozens of rows, and the app
  removing one is the documented `new-row` risk already; watching all of them is post-v1 if it
  earns it).
- `--anonymize` composes as everywhere: plan-only by construction (`--apply` refused globally),
  fields substituted before formatting.

## Testing

- Three accounts, mixed holdings; the plan enumerates exactly the missing pairs, and apply
  levels the counts. Alignment's `complete` reaches full; `distinguishable` unchanged.
- Title choice: holders disagree → newest holder's title wins and the plan says the copies
  disagree.
- `held_title_collision`: destination holds a *different* conversation under the title → held,
  named, everything else applies, exit 0.
- `held_transcript_missing`: dead holder row → held, nothing created.
- Apply-time skip: a row appears in the destination between plan and apply → `already_present`,
  not an error, not a duplicate.
- Fault injection after row 1 of N: `recover --back` leaves no trace; `recover --forward`
  finishes; both re-checked against a store mutated in the window.
- Undo: deletes created rows; skips one whose `cliSessionId` changed since, with the skip named;
  op consumed; a second `undo` reaches the previous op.
- Empty case: complete store plans zero, exits 0.
- Example titles from the fake cast (`ACME-REVIEW`, `Northwind`, `Quarterly board report
  finalization`) — never real titles. The pre-push hook enforces this; do not make it the first
  line of defence.

## Known limits

- **Converge spreads presence, not agreement.** Divergent titles for one conversation and
  disagreeing row files survive a converge run untouched; `retitle` and human judgement own
  those. The plan surfaces both where it sees them.
- **The claim protocol.** Converge-created rows do not participate in `sync`'s claim protocol,
  matching `new-row` (deferred there, deferred here, same reason).
- **No GUI surface in v1**, consistent with `move` and `retitle`.
- **`sync --from` was considered and declined.** Bolting a dormant-store source onto `sync`
  doubles the state space of the one command that overwrites (its claim protocol, learned-email
  memo, and `--update` routes all assume a live source), to serve a need that is additive by
  nature. Converge covers the recurring chore with no overwrite path at all; if a true
  "copy per-account state from a dormant store" need ever materialises, it should be its own
  argued design, not a flag.
