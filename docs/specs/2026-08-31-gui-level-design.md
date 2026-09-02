# GUI 2.0 — the window learns the routine (design)

**Status: draft, for handoff. Round-1 panel review applied — see "Review" at the end.**
The `ccs-gui` window is rebuilt around the maintenance routine that actually runs every few
days — *plan → name the holds → converge → confirm* — instead of the one-way copy it was
built for in August. Three tabs: **Level** (new, the home), **Copy & refresh** (the
existing sync pane, moved intact), **Health** (doctor, plus interrupted-operation
detection). Ships as 0.15.0.

Names below are the fake cast (`ACME-REVIEW session`, `alice@example.com`, ids
`aaaa1111…`, conversations `s-early…`/`s-late…`).

## Why, and why now

The window is `class SyncApp`, titled "Claude session sync", and of the tool's twelve
commands it runs two. Meanwhile the routine a multi-account store needs is CLI-only, and
its one genuinely interactive step — naming the title-collision holds — became
one-click-shaped in 0.14.0, when holds started arriving with `measured.suggested_title`,
`measured.superseded`, and `command_runnable`. Those fields were designed with this window
as their intended second consumer; this spec is that consumer. The window also ships to
everyone who installs from PyPI, where its current shape misrepresents the product.
(Cadence evidence is one store — passes measured 08-26/28/29 on the author's machine — a
hypothesis about other users, stated as such.)

**The prerequisite order was deliberate and is now satisfied**: measured hold remedies
first (0.14.0), so the rename rows below arrive *prefilled*; a GUI built before them would
have been a data-entry form built twice.

## The two principles carried over unchanged

From the module docstring of the current GUI, both load-bearing and both kept:

1. **A thin shell over the library, never a reimplementation.** Every action calls the same
   `gather_*`/`plan_*`/`run_*` functions the CLI does — `gather_alignment`,
   `plan_converge`/`run_converge` (`ConvergeFlags(only, live)`),
   `plan_retitle`/`run_retitle` (`RetitleFlags(only, title, store, live)`),
   `claude_running` (read-only, for the passive notice below), `list_ops`. Every refusal,
   guard and safety property behaves identically; the GUI adds no path of its own into the
   store. Where the spec says "the same wording the CLI prints", the implementer may
   extract that wording into a shared helper — **extraction without behavior change is
   in-bounds; new engine behavior is not** (the non-goal at the end is about behavior).
2. **Nothing is written until Apply; refusals are shown verbatim, and modal after Apply.**
   Opening the window plans only. The existing worker-thread pattern is reused as-is:
   daemon thread per operation, `root.after(0, …)` back to the UI thread, the generation
   counter dropping superseded callbacks, `busy()` disabling *every* control while any
   worker runs (extended to the new tab's controls). **Read-side failures are not
   refusals-after-Apply**: a `Refusal` or exception from the on-open/Refresh calls renders
   in the pane with the status line set (the sync pane's `_plan_done` problem-routing,
   reused), never as a modal, and Refresh stays enabled as the retry.

## Tab 1 — Level (the new home)

### On open, and on Refresh

One worker runs two read-only calls: `gather_alignment(env)` then
`plan_converge(env, ConvergeFlags(live=<live_choice>))`. Sequential reads, one label:
the pane footer says when the snapshot was taken; converge's own apply-time re-checks are
the guard against drift between reads, not the display. The pane renders, top to bottom:

**A passive environment notice.** `claude_running(env)` (the engine's own check, called
read-only) sets one line when the desktop app is up: *"The Claude desktop app is running —
Apply will refuse until it is closed."* Weather, not a gate: the guard stays where it
lives, in the engine at mutation time; this line exists so nobody types five titles first
and learns about RULING 4 second.

**The scoreboard** — rendered from the alignment report's own fields, never re-derived.

**The plan summary** — the manifest's completeness line and per-destination row counts.

**The identity warning, when the manifest carries one** (0.13.0's
`identity_disagreement`): the banner plus the existing live-picker pattern — one button
per disagreeing account, labeled by email where known, setting `live_choice` and
replanning. `live_choice` is **in-memory only, never written to disk** (the sync pane's
hard-won rule, restated precisely this time): it survives replans, is shown while in
force, threads into `ConvergeFlags.live` on every plan, and clears when the Apply
sequence **ends — completed, refused, or truncated alike** (an assertion covers one
attempt, the same "covers one run" rule the sync pane records; if the files still
disagree, the post-refusal replan re-raises the banner and asks again). It never clears
*mid*-sequence, so the fresh plan in stage 2 below still carries it. It does **not** thread into `RetitleFlags`: retitle has no RULING 5 gate (a documented
0.13.0 scope decision), so passing it would assert something retitle never asks.
**Revised for 0.16.0 (2026-09-01, the GUI polish design, Change 2):** `live_choice` is
the window's one identity answer, shared with the One-session tab and bound to the
`(oauth, config)` pair the identity files showed when it was given. It is dropped when a
run of the mutation path consumed it (a sync apply that wrote, or a converge ending
`completed` or `unchanged`), when a fresh read of the identity files no longer returns
that pair (agreement, a different pair, or the roles swapped), or when the user presses
"Change signed-in account"; it survives refusals, cancels, the zero-rows `empty`
outcome, truncation and tab switches. Every plan reads the files before building its
flags and every consuming press re-reads them, so a plan never receives an answer the
files have outgrown. When the fresh stage-2 plan carries the disagreement and no answer
is held, the sequence asks then - a sixth adapter member, `ask_live(fresh)`, whose
failure reads as Cancel - and replans with the answer before the stage-2 dialog; the
stage-1 dialog says in advance that the copy step will ask. The same red in-force line
and button show on both tabs, and every confirmation that precedes a write under the
answer prints the assertion line. The 0.15.0 clear-on-every-ending rule, and harness
item 20b that pinned it, are superseded (item 20b is inverted).

**The holds, as structured rows — the point of the tab.** Each `held_title_collision`
hold renders as a row widget. The row model is a pure function,
`_hold_models(manifest) -> list of dicts`, importable without tkinter, with the contract:
`key` (the stable identity: `(measured.superseded or hold sid, title_key of the colliding
title)`), `title`, `evidence` (one line built from `measured`'s sids, counts and dates),
`target_sid` (**`measured.superseded` — the engine's verdict, never re-derived from the
evidence text**), `prefill` (`measured.suggested_title` or empty), `editable` (bool),
`ticked` (bool), `degrade_reason` (shown when set), `classification`.

- **Supersession with a suggestion**: evidence line, an **Entry prefilled with
  `suggested_title`**, **"Rename" ticked**. `command_runnable` is *not* the gate — it
  speaks about the rendered shell command only; the GUI applies titles through
  `plan_retitle`, no shell involved (exactly the split 0.14.0 argued: a `$` in a title
  degrades the pasteable command while remaining a valid title). A degraded supersession
  with a surviving `suggested_title` still prefills.
- **Supersession without a suggestion** (`suggested_title` null): evidence plus
  `degrade_reason`, empty Entry, unticked. A ticked row with an empty entry refuses
  locally ("a rename needs a name"); two ticked rows with the same trimmed title refuse
  locally too ("two renames share a name") — the cheap set-check before any engine call.
- **Distinct / unmeasured**: the measured or `not measured: <reason>` line, read-only.
  **Revised in 0.15.1 (2026-09-01, finding F of the first real use):** these rows are
  *editable, empty, unticked* - the degraded-supersession treatment - with the measured
  line kept above the entry as context. The 0.15.0 text conflated "the measurement made
  no suggestion" with "this leg should not be named"; a human always knows more than the
  measurement, which is the whole reason the measurement is allowed to decline, and the
  tab was showing holds it offered no way to clear. Such a row renames **its own held
  conversation** - the hold's `session`, which is also the key sid since
  `measured.superseded` is null - because a row is *about* conversation X, a user typing
  a name expects to name X, and renaming either leg of a collision clears it. (The CLI's
  remedy line targets the *blocking* side; that convention stays in the CLI.) The
  evidence line therefore names what the leg collides with - the other sid's prefix, from
  `measured.a`/`b`, and the account(s) - so the user knows they are naming one of two.
  The `target_sid` contract above reads: `measured.superseded` on a supersession, the
  hold's `session` otherwise. The unknown-reason fallback row below stays read-only.
- **Any other hold reason** (future engine versions): a read-only fallback row carrying
  the hold's own `reason`/`detail` verbatim — the pane must never count holds it cannot
  show.

**Edits survive replans.** Rebuilding rows (identity button, Refresh, post-Apply refresh)
merges by `key`: a row still present keeps the user's entry text and tick; rows that
vanished drop; new rows arrive with their defaults. User input is never silently reset by
a replan the user did not aim at it.

**The success predicate, defined:** the "Nothing to do — the sidebars are level." status
requires *all three*: the alignment report's `complete` short-count is 0, the plan has no
rows, **and** no holds exist. Rows-empty-but-holds-present reads
`Nothing to copy — N naming decisions below.`, and Apply stays enabled (it has renames to
run). Rows-empty-holds-empty-but-alignment-short (possible when a conversation has no
transcript for converge to spread) reads the alignment line, points at Health, and
**Apply is disabled** — the tab has nothing it can do about that state. Whenever there is
truly nothing to run — no rows, no ticked-able holds — Apply is **disabled**.

### Apply — two stages, each confirming exactly what runs next

The round-1 panel's heaviest convergent finding: a single confirmation quoting the
preview's row count would describe a manifest the design itself declares stale once
renames land. So Apply is two stages, each dialog describing precisely and only its own
stage:

**Stage 1 — the renames.** The dialog lists every ticked mapping in full —
`'ACME-REVIEW session'  ->  'ACME-REVIEW session - earlier leg (Aug 24-28)'` — one line
per row, states once that each rename applies **in every account holding that
conversation** (retitle's default scope, said so the bulk list is not blind trust in
routing), and that each is its own journalled, individually undoable operation. **The
dialog is a scrollable Toplevel, not a stock messagebox** — the pickers already establish
that pattern — because a stock dialog with thirty mapping lines pushes its buttons off
screen; the mapping list scrolls, the confirm/cancel row does not move. (The prefilled-and-ticked default is an opt-out; this dialog, showing every
old→new pair, is the look the opt-out gets.) On confirm, the worker runs each ticked
rename in row order: `plan_retitle(RetitleFlags(only=<target_sid>, title=<entry text>))`
then `run_retitle(...)`. A refusal **stops the sequence**: remaining renames and stage 2
do not run, the refusal is modal and verbatim, the status names how many landed
("2 renames landed, each undoable; the third was refused"), and the pane **replans fresh**
so the rows show the store as it now is. With zero ticked rows, stage 1 is skipped
entirely and Apply goes straight to stage 2.

**Stage 2 — the copy, confirmed as itself.** The worker replans
(`plan_converge`, fresh — applying the preview would both miss the cleared holds and trip
converge's own re-checks) and presents the *fresh* plan's numbers in the second dialog:
`Create N rows across the accounts named? (M held — they stay held.)` **When the fresh
plan has zero rows, no dialog appears and no converge runs** — a user must never be asked
to confirm creating zero rows. On confirm, `run_converge(fresh_manifest)`.

**The post-mutation refresh is unconditional — a `finally`, not a success step.** Whenever
the sequence performed *any* mutation — renames landed and stage 2 was skipped, refused,
or completed — the worker ends with `gather_alignment` **and a third `plan_converge`**,
and the pane fully re-renders from both. The displayed rows after Apply are always rebuilt
from post-Apply state, never a manifest that predates the writes (which also reconciles a
*partial* converge honestly: the refresh shows what happened, not what was planned). A
sequence that mutated nothing (all-skipped, or refused before the first write) refreshes
too when the refusal implies drift; the cheap constant is: **every Apply press ends in a
refresh.**

**One status line, both halves:** the completion status concatenates the journal and the
store — `Applied (2 renames + 1 converge) — Level: 379 / 379 / 379 — 0 short.` — or the
honest variants (`Applied (2 renames; nothing to copy) — 2 held — the rows below are
current.`, `Nothing to do.`). The ops-created half is what makes the Undo walk-back
legible; the scoreboard half is what the tab exists to report; the round-1/round-2 texts
that assigned the line to each half separately were unsatisfiable together.

**Progress is narrated, not implied.** The worker posts a status update before every
engine call — `Applying rename 3 of 20…`, `Planning the copy…`, `Creating rows…`,
`Re-measuring…` — because a twenty-rename Apply with a frozen status line reads as a hang,
and a user force-closing a "hung" window is how interrupted operations get manufactured.

**Failure states, all of them — status templates conditional on what actually ran** (a
skipped stage 1 must never produce "renames landed"; the templates take the landed count,
and zero renders as `No renames were requested` / omits the clause):

- Stage-2 *plan* refusal or error → in-pane (a read failure), status names the landed
  rename count (possibly zero).
- Stage-2 `run_converge` refusal (the desktop app reopened, RULING 5 fresh disagreement,
  drift) → modal verbatim; status: the landed renames (if any, individually undoable),
  nothing copied; pane refreshes. Renames standing alone is a *complete, valid* store
  state — the same state the CLI routine passes through between its own steps.
- An *interrupted* converge (process killed mid-write) is exactly what Health's detection
  exists for: on next open the unresolved op gates mutations (below).
- Final refresh failure after a successful converge → status
  `Applied — could not re-measure; press Refresh.`; **the pane keeps its last rendered
  content, visibly flagged stale** ("shown from before the apply") rather than going
  blank — applied-but-unverified is distinct from not-applied, and looks it.
- **Window close during a worker**: the WM close button is intercepted while a mutation
  worker runs, with a choice stated up front — "The current operation will finish, and
  the N remaining step(s) will NOT run. Close?" On confirm, the worker completes the
  in-flight operation, **truncates the rest of the sequence**, and the window closes at
  that boundary; landed operations are in the journal exactly as if the user had stopped
  there deliberately. (A hard kill mid-operation lands in the journal as an interrupted
  op; Health's detection is the net, and says so in its rationale line.)

### Undo, generalized — and gated

`_find_undoable_sync` becomes `_find_undoable_op`: the most recent completed operation
*if* its type is `sync`, `retitle`, or `converge`, labeled by what it reverses ("Undo last
rename", "Undo last converge (23 rows)"). **One button, living in the window-level bar
outside the notebook** — it replaces the sync pane's own undo button, and its state is
shared across tabs (there is one journal, so there is one "last operation"). Unchanged invariants keep their comments and
force: the button targets exactly the op `ccs undo` would pick, never reaches past an
unfamiliar latest op (`move`/`new-row`/`repoint` → no button), re-checks at press time,
and the confirmation carries the op's own semantics in the CLI's wording (converge-undo's
skip rules included). After a Level apply the button points at the converge; pressing it
repeatedly walks the stack exactly as repeated `ccs undo` would. **There is deliberately
no one-click "undo the whole Level"**: the journal's unit is the operation, a GUI-side
macro-undo would be a compound semantic the engine does not have, and the completion
status therefore names what was created ("3 operations: 2 renames + 1 converge") so the
walk-back is legible. Undo after any step triggers the same fresh replan/re-render as
Apply does.

**The gate:** while Health's detection (below) reports any unresolved operation, **Apply
and Undo are disabled on every tab** — explicitly including the sync tab, whose
"unchanged in behavior" contract carries exactly two carve-outs, this gate and the shared
Undo button — with the red line as the explanation. And because a scan at window-open can
go stale (another process can die mid-write while this window sits open), **every
mutation press re-runs the unresolved-op scan first** — stage 1's confirm, stage 2's
confirm, sync's Apply, and Undo all check at press time, the same precedent Undo's
still-the-latest re-check already set; an op found there renders the red line and aborts
the press. A mutation launched over an unresolved journal op would change the state
`recover` is about to reason over; the window refuses to be the second writer. (The
engine's operation lock protects live processes; this gate covers the restart case,
where the lock is gone but the journal still says "unfinished".)

## Tab 2 — Copy & refresh

The existing sync pane, moved into the notebook **unchanged in behavior**: destination
preference, `--only` filter, update/newer-only/orphan checkboxes, pickers, confirmation
dialogs. Its code moves; its contracts do not — and the existing
`tools/check_gui_*.py` harness must keep passing over the moved pane, which is the
regression net for the move itself. (`--update` refresh remains this tab's reason to
exist — converge is additive and deliberately never refreshes.)
**Revised for 0.16.0 (2026-09-01, the GUI polish design, Changes 2 and 3):** the tab is
third in the strip and named **One session**, with a role line above its status line,
and its "unchanged in behavior" contract carries two more carve-outs beside the mutation
gate and the shared Undo button. Carve-out 3: the identity answer is the one variable
shared with Level, the pane plans on the first selection of its tab rather than at open
(through the same one-pending-run mechanism Health's first-visit doctor run uses), and
its identity refusal renders the shared banner in-pane above the verbatim text instead
of raising a popup. Carve-out 4: Apply is live only when the rendered plan lists exactly
one row - an add or a refresh alike - or under a "copy every row this plan lists (N)"
tick bound to a digest of the rendered rows, off at every open and never remembered.
The engine is untouched; `plan_sync` plans as before, and the window decides when Apply
is live.

## Tab 3 — Health

The existing doctor rendering, plus **interrupted-operation detection** — the 2026-08-08
review's named minimum, still undone. On window open and on this tab's Refresh,
`list_ops(env)` is scanned with the same selection logic `cmd_recover` uses (anchor on
it; extract a helper if it lives inline). When unresolved operations exist — plural is
normal after a crash:

- a red status line (with a `!!` text prefix, not color alone) appears on every tab:
  `N interrupted operation(s) need attention — see Health.`;
- the Health tab lists each (id, type, age, what it was doing), marks the one a bare
  `ccs recover` would select, and shows the command with a **Copy button**;
- **execution stays in the CLI, deliberately**: `recover` is a directional judgment
  (`--back` removes what landed, `--forward` re-evaluates the remainder) whose CLI prose
  walks the user through evidence this window would have to duplicate to be safe. The
  rationale line renders under the command, with "press Refresh here once recover
  finishes" — a successful refresh with nothing unresolved clears the banner and lifts
  the mutation gate everywhere.

## Cross-cutting changes

- **Window title and shortcut.** The window becomes **"Claude sessions"**;
  `SHORTCUT_NAME` becomes `Claude sessions.lnk`. `--install-shortcut` deletes an existing
  `Claude session sync.lnk` when present (stated in the help text — a taskbar pin to the
  old name is the user's to re-pin); `--remove-shortcut` removes both names.
- **Geometry**: 940×640 default, 760×520 minsize; the hold area scrolls when rows
  overflow it.
- **No `--anonymize` surface** (unchanged; anonymize is for pasting into public places,
  a CLI act — stated so the review can disagree, and one reviewer did; see Review).
- **Preferences**: `PREF_PATH` keeps its single `to` key for the sync tab. The Level tab
  writes nothing to disk; its only session state (`live_choice`, unapplied edits) is
  in-memory and visible.
- **Version 0.15.0.** README's `ccs-gui` section rewritten around the three tabs.
  Release notes: fake cast, `--anonymize` for real output, the public-safety scan before
  push. New `tools/check_gui_level.py` joins CI the same way the existing check scripts
  are wired (the implementer mirrors their registration, whatever form it takes).

## Tests

Pure-function coverage (new `tests/test_gui_models.py`; skip cleanly without tkinter):

1. `test_hold_models_prefills_runnable_supersession` — prefill + editable + ticked, and
   `target_sid == measured.superseded`.
2. `test_hold_models_prefills_shell_unsafe_supersession` — `command_runnable` False with
   non-null `suggested_title` still prefills (the GUI path is not the shell path).
3. `test_hold_models_degraded_supersession_is_empty_editable` — null title,
   `degrade_reason` present in the model, unticked.
4. `test_hold_models_distinct_unmeasured_and_unknown_reasons` — read-only rows, including
   the unknown-hold-reason fallback carrying the hold's own detail.
5. `test_scoreboard_lines_render_from_alignment_report` — including an unequal-rows
   fixture.
6. `test_level_predicate_states` — level (rows 0, holds 0, short 0) / naming-only (rows
   0, holds N) / short-but-empty-plan; Apply enablement per state.
7. `test_level_steps_stage1` — ticked renames in row order with `target_sid`; unticked
   and read-only rows contribute nothing; ticked-empty and duplicate-title tickings yield
   local refusal markers, no steps.
8. `test_row_merge_preserves_edits` — rebuild by `key` keeps entry text and tick for
   surviving rows, drops vanished, defaults new.

Harness coverage (new `tools/check_gui_level.py`, plus additions to
`tools/check_gui_undo.py`; the existing check scripts must pass unmodified over the moved
sync pane):

9. Fixture store with two colliding conversations → one hold row prefilled from the
   measured suggestion; stage-1 dialog text contains the full old→new mapping.
10. Full Apply drives the real engine: retitle lands; `plan_converge` runs **three
    times** (preview, fresh-for-apply, post-apply refresh) and `run_converge` **once,
    with the fresh-for-apply manifest** — applying the preview is the bug this pins;
    rows created; scoreboard reports level; the post-Apply rows come from the third,
    post-write plan. Per-step progress statuses observed in order.
11. Retitle refusal mid-sequence (second of two renames) → no converge call, modal, status
    names one landed rename, pane replanned.
12. Stage-2 `run_converge` refusal (fake running-app guard tripping between stages) →
    modal verbatim, status says renames landed / nothing copied, pane replanned.
13. RULING 4 up front: with the guard tripping at stage 1, nothing written, modal shown;
    and the passive notice line renders when `claude_running` reports the app.
14. Identity disagreement fixture → banner + per-account buttons; choosing one replans
    with `live` set; `live_choice` still set going into stage 2 (cleared only after
    completion); user-typed entry text survives that replan.
15. `check_gui_undo.py` gains: latest = retitle → offered; latest = converge → offered
    with row count; latest = CLI `new-row` → no button; **any unresolved op → Undo and
    Apply disabled everywhere** until a refresh finds none.
16. Interrupted-op fixture with two unresolved ops → plural red line on every tab, both
    listed, the bare-`recover` target marked, Copy button present, no resolution buttons;
    resolving (fixture-side) then Refresh clears banner and lifts the gate.
17. Window-close interception: close during a (fixture-stalled) worker prompts with the
    remaining-step count, completes the in-flight op, truncates the remainder (no
    further engine calls), closes at the boundary.
18. Read failure on open (monkeypatched `gather_alignment` raising) → in-pane error, no
    modal, Refresh enabled.
19. Press-time gate re-scan: an unresolved op planted *after* the pane rendered → the
    next Apply/Undo press aborts with the red line, no engine mutation call made; same
    for the sync tab's Apply.
20. Zero-rows stage 2: renames land, fresh plan is empty → no second dialog, no
    `run_converge` call, **and the post-mutation refresh still runs** (the pane's hold
    rows are gone, rebuilt from the post-rename plan); status carries both halves
    (`Applied (2 renames; nothing to copy) …`); the all-skipped variant reads
    `Nothing to do.`
20b. `live_choice` clears when a sequence ends by refusal too — a stage-1 refusal with
    the identity files still disagreeing re-raises the banner on the post-refusal replan.
21. Post-converge refresh failure (monkeypatched second `gather_alignment` raising) →
    status `Applied — could not re-measure`, pane retains flagged-stale content.

## Non-goals, stated so the review can disagree with them

- **Prefilled-and-ticked stays the default.** It is an opt-out mutation tempered by the
  stage-1 dialog listing every mapping in full; unticked-by-default would re-type the
  suggestion's value away on the strength of no observed bad suggestion. Revisit on the
  first measured wrong prefill.
- **One converge per Apply, not two.** The rhythm's first "converge" is the *dry run* —
  the preview this tab always shows — so preview → renames → fresh plan → apply *is* the
  CLI routine, not an optimization of it (round 1 read it otherwise; the narrative now
  says dry run explicitly).
- **No one-click composite Undo** — argued at "Undo, generalized".
- **`recover` execution stays CLI** — argued at Tab 3, now with the copyable-command
  hand-off.
- **The list browser (old Tier 2 second item) is cut from this release** — the sidebars
  are the list browser for the common case, `ccs list` for the rest; the tab structure
  gives it an obvious home later.
- **Tier 3 stays CLI as previously decided**: `repoint`, `--verbatim`,
  `--include-deleted`, and `move`/`new-row` until demand reappears.
- **No B2/retirement button** — when B2 exists, its surface is one more control on the
  hold row.
- **The sync tab is not redesigned.**
- **No new engine behavior.** Extraction-for-reuse refactors are permitted (argued at
  principle 1); anything more is a finding for a separate change.

## Review

**Round 1, 2026-08-31 morning.** Full four-engine panel (Codex xhigh, Gemini via agy,
DeepSeek and Kimi on the sealed roster route); all four reported, canaries verified. The
round reshaped the Apply flow and closed a family of unspecified states.

**Applied:**

- **[Codex+DeepSeek+Kimi, the convergent core] Apply became two stages, each confirming
  exactly what runs next.** The draft's single dialog quoted the preview's row count —
  a manifest the draft itself declared stale after renames. Stage 1 confirms the renames
  (every old→new mapping listed in full — also Codex's opt-out-needs-a-look point);
  stage 2 confirms the *fresh* converge plan as itself. Every failure state between and
  after the stages is now specified, including converge-refusal-after-renames (Kimi's
  critical: previously only step 1 had failure semantics), applied-but-unverified, and
  the interrupted-mid-write case handed to Health.
- **[Kimi, sharpest single catch] The rename target is now a named schema field.**
  `target_sid = measured.superseded` — the engine's verdict, in the `_hold_models`
  contract, never re-derived from evidence text. (The field exists in the shipped 0.14.0
  schema; the draft's `<superseded sid>` placeholder just failed to name it.)
- **[Codex] Unresolved operations gate mutations.** Apply and Undo disable on every tab
  while Health detects an unresolved op — the restart case the process lock cannot cover;
  also resolves Kimi's Undo×interrupted interaction. Detection handles plural ops, marks
  the bare-`recover` target, gains a Copy button and a refresh-to-clear path
  (DeepSeek/Gemini's hand-off findings).
- **[Codex+Gemini] Edits survive replans** — rows merge by stable key; identity-button
  and post-Apply replans keep unapplied entry text and ticks; pinned by test 14.
- **[Kimi] The "level" predicate is defined** (rows 0 ∧ holds 0 ∧ short 0), with the
  naming-only and short-but-empty states given their own honest lines and Apply
  enablement per state (also Gemini's disabled-Apply-on-empty).
- **[Gemini] A passive running-app notice at plan time** — `claude_running` called
  read-only for a weather line, so nobody types five titles and then meets RULING 4;
  the guard itself stays in the engine.
- **[DeepSeek] Read-side failure routing specified** (in-pane, never modal, Refresh as
  retry); `live_choice` storage stated precisely (in-memory only — the draft's
  "persistence rules verbatim" vs "stores nothing" read as a contradiction); `live`
  threads to converge only, with the mid-sequence clearing order pinned (Kimi).
- **[Codex] Unknown hold reasons get a read-only fallback row** — the pane never counts
  holds it cannot show.
- **[Kimi] Local duplicate-title check** across ticked rows, matching the local
  empty-title refusal.
- **[Codex+DeepSeek+Kimi] Window-close-during-worker defined** (deferred to the
  inter-operation boundary; hard kills land in Health's net, which now says so).
- **[Codex] Composite-undo honesty**: the completion status names the operations created;
  the Undo button walks them back one at a time, stated in the dialog.
- **[Codex] The one-vs-two-converge question answered in the narrative**: the routine's
  first converge is the dry run (the preview), so this *is* the CLI shape — recorded as a
  non-goal with the reasoning rather than left ambiguous.
- **[Codex] Tests grew 13 → 18** and the sync-pane move gained its regression net (the
  existing harness must pass unmodified); CI registration named.
- **[Kimi] Cadence evidence scoped** to the one measured store; extraction-for-reuse
  refactors explicitly permitted so "reuse the CLI's wording/selection logic" cannot
  smuggle in an unplanned engine change.

**Declined, with reasons:**

- **[Gemini] A GUI-native rollback/abort for interrupted operations.** `recover`'s
  direction choice is a judgment over evidence the CLI prose presents at length;
  duplicating that surface is how a GUI gets it subtly wrong. The gate plus the copyable
  command plus refresh-to-clear is the deliberate shape; revisit only with evidence of
  users actually stranded.
- **[Gemini] One-click batch undo of a Level apply.** The journal's unit is the
  operation; a GUI-side macro-undo would invent a compound semantic the engine does not
  have, and the walk-back is now legible instead (ops named in the status).
- **[Codex] A shared store-revision snapshot across the two read calls.** No such
  primitive exists in the engine; converge's apply-time re-checks are the real guard, and
  inventing a revision concept for a display label is engine work this spec forbids
  itself. The footer timestamp plus the re-checks are the honest version.
- **[DeepSeek] An anonymized read-only GUI preview mode.** Real demand unshown; the CLI
  covers the paste-something-public case today. Recorded as the first candidate if a
  screenshot-driven request ever arrives.
- **[Kimi] Renaming concerns ("Claude sessions" resembling an official surface).** The
  existing name has said "Claude session sync" since 0.9.2; the rename removes a wrong
  word, not adds a claim. Noted.

**Round 2, same sitting.** Panel: Gemini, DeepSeek and Kimi reported (canaries verified);
**Codex failed the round** (rc=124 at its 540s bound — slot lost per the degradation
rules; it returns in round 3). All three reporting engines found defects inside the
round-1 redesign — the defective-fix clause at work again.

**Applied:**

- **[Gemini, blocker] The mutation gate now names its carve-out.** "Sync tab unchanged in
  behavior" contradicted "Apply and Undo disabled on every tab"; the sync tab's contract
  now carries exactly two stated exceptions — the gate and the shared Undo button.
- **[Kimi, major] The gate re-scans at press time.** A scan only at window-open goes
  stale the moment another process dies mid-write; every mutation press (both stages,
  sync Apply, Undo) now re-runs the unresolved-op scan first — the same press-time
  precedent Undo's still-the-latest check set. Test 19.
- **[Kimi, major] The third plan is now explicit and test 10 agrees with the prose.**
  "Re-render from a new plan" after `run_converge` *is* a third `plan_converge`; the
  round-1 test pinned two, making prose and harness unsatisfiable together. Three plan
  calls pinned; the post-write refresh is also what reconciles a partial converge
  honestly.
- **[DeepSeek, blocker] Status templates are conditional on what ran.** A skipped stage 1
  can no longer produce "renames landed"; templates take the landed count and render
  zero honestly.
- **[Gemini+DeepSeek] Zero-rows stage 2 never asks.** An empty fresh plan skips the
  dialog and `run_converge` entirely (`Renames landed; nothing to copy.` /
  `Nothing to do.`). Test 20.
- **[Gemini, high] Progress is narrated.** Per-step status before every engine call — a
  frozen status line during twenty renames reads as a hang, and force-closing a "hung"
  window manufactures the interrupted ops Health exists to catch.
- **[Kimi, major] Close-at-boundary truncates loudly.** The close prompt states the
  remaining-step count up front; the in-flight op completes, the remainder is truncated,
  nothing silent. Test 17 extended.
- **[DeepSeek, major] Undo button placement fixed** — one window-level button outside
  the notebook, replacing the sync pane's own.
- **[DeepSeek, major] Post-converge refresh failure keeps the pane** — flagged-stale
  content instead of blank. Test 21.
- **[DeepSeek] Apply disabled in the short-but-empty-plan state**, stated rather than
  implied.
- **[Gemini, low] Stage-1 dialog names retitle's every-account scope** once, so the bulk
  mapping list is not blind trust in routing.

**Declined:** nothing in round 2.

**Round 3, same sitting — the cap round.** Gemini reported (canary verified). **Codex
failed with rc=124 for the second consecutive round** — a documented failure mode
(slow-xhigh timeout at its 540s bound), so this spec's later rounds ran without an OpenAI
voice; worth knowing when weighing the panel's coverage. All four Gemini findings were
defects in the round-2 fixes and are applied:

- **[Blocker] The post-mutation refresh is now unconditional.** Round 2 nested it inside
  stage 2's success path, so the new zero-rows skip left renames applied and the pane
  still showing them as pending — success status over stale rows. The refresh is now a
  `finally`: every Apply press that mutated anything ends in
  `gather_alignment` + plan + re-render (test 20 re-pinned).
- **[Major] The completion status line was specified twice, incompatibly** (scoreboard
  text in stage 2, ops-created text under Undo). Now one concatenated line carrying both
  halves.
- **[Major] `live_choice` clearing on an aborted sequence defined** — clears whenever
  the sequence ends (completed, refused, truncated); the post-refusal replan re-raises
  the banner if the files still disagree. Test 20b.
- **[Risk] The stage-1 dialog is a scrollable Toplevel** — a stock messagebox with
  thirty mapping lines pushes its buttons off screen.

**Loop closed at the 3-round cap.** Round 3's fixes are review-driven but not
panel-verified (the cap ends the loop, and the round ran Gemini-only); the implementing
session should read the Apply sequence's refresh/status/`live_choice` paragraphs most
skeptically, and treat the absence of a round-3 Codex voice as reduced coverage on
exactly those paragraphs.