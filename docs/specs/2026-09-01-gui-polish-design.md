# GUI polish - the window becomes legible (design)

**Status: draft, for handoff. Three-round panel review applied - see "Review" at the end;
the operator's four decisions are recorded under "Decisions already taken".** The "Claude
sessions" window (`ccs-gui`) at 0.15.1 does
what the engine tells it to and shows what the engine returns, and it is still hard to read:
the one question it must ask is asked twice, the tab strip reads as a checklist, and the rare
tab's controls do not fit the default window. This spec is the polish run that follows the
2026-09-01 UX/UI audit of the window (a read-only audit with a headless layout probe, three
peer-review rounds; it lives outside this repo). Every change here is GUI-side; the engine is
untouched. Proposed to ship as **0.16.0**, not a patch: two of the changes alter what the
window does before and between engine calls.

Names below are the fake cast (`ACME-REVIEW session`, `Northwind`, `alice@example.com`,
`bob@example.com`, ids `aaaa1111…`/`bbbb2222…`, orgs `cccc3333…`/`dddd4444…`).

## Why, and why now

The audit walked a first-time user with two accounts through the 0.15.1 window against the
source and found four wrong turns, two of which nothing catches:

- **A.** Reads the tabs as steps, goes to Copy & refresh, and presses Apply over the whole
  bulk plan with an empty filter. One confirmation later the rows land. On the operator's
  store that plan was 78 rows, 77 of them for conversations the sidebar already opened under
  another row file; applying would have moved alignment's `distinguishable` from 0 into the
  seventies. The 0.15.1 warning above Apply is the only thing in the way, and it sits under
  a bold "78 sessions ready to copy".
- **B.** Meets the identity question (RULING 5) as a popup in the top-left corner of the
  screen, raised by the tab used least, before the home tab has been seen; answers it, and
  is asked again by the home tab in different words. A wrong answer writes the store the
  user is actually using, and RULING 5 trusts the answer by design.
- **C.** Ticks "Also refresh rows already there" to see what it does. The plan text then
  says to tick "allow hiding a conversation" - the RULING 8 opt-in - and that checkbox is not
  on screen: the filter row requests 1101 px in an 896 px frame at the default window size,
  so its third box clips and its fourth is not drawn at all until the window is about
  1150 px wide.
- **D.** Types two names into empty hold rows, closes the window to go close the desktop
  app, and the names are gone. Close asks nothing on the idle path.

C is a defect and goes first. The rest is legibility: where the question is asked, what the
tabs are called and in what order, what gets the room, what Close and the dialogs say.

## The principles carried over unchanged

From the module docstring and the GUI 2.0 design; the window's safety case rests on each, and each is kept:

1. **A thin shell over the library.** Every mutation goes through `run_sync`, `run_retitle`,
   `run_converge`, `undo_*`; every read through `gather_*`, `plan_*`, `list_ops`,
   `nonterminal_ops`, `claude_running`. Nothing below adds a call the CLI does not make.
   Extraction of shared wording into a helper is in-bounds; new engine behaviour is not.
2. **Nothing is written until an action button is pressed; refusals are shown verbatim**,
   modal after a press, in-pane after a read. Presentation *around* a verbatim refusal (the
   pickers `_plan_done` already routes three refusal shapes to) stays a presentation, never a
   rewrite.
3. **The two-stage apply, each dialog describing only its own stage**, the fresh stage-2 plan,
   the zero-rows skip, the unconditional post-mutation refresh, the enumerated failure states
   and the mutation gate are not reopened. One question is added inside stage 2 (Change 2)
   and every outcome it can produce maps onto a state the sequence already defines.
4. **Friction that is a safeguard stays.** RULING 8's per-run opt-ins, the absent `--verbatim`
   and `--include-deleted`, `recover` in the terminal, no one-click composite undo,
   prefilled-and-ticked rename rows, neither identity button pre-selected, the answer never
   written to disk. No default flips - see "Defaults".

## Decisions already taken (operator, 2026-09-01)

Recorded here so the review argues the design, not the decisions:

- **The single-session gate and the "One session" rename go together** (Change 3). The
  label is honest only with the gate.
- **The identity answer clears on a completed write** (Change 2, rule (a)). This is a
  change in both directions from 0.15.1: it is *longer-lived* than the Level pane's answer
  today, which `_level_apply_done` clears on every ending - refused, cancelled or empty
  alike - and *shorter-lived* than the alternative considered, an answer that lasts the
  sitting. The widening is argued in Change 2 item 5 (a refusal or a cancel ran nothing, so
  it covered nothing; re-asking after each is the question asked so often it stops being
  read); the sitting-long alternative was rejected because every further widening widens
  RULING 5's same-pair residual, and the routine has one Level apply per sitting, so the
  re-ask after a write is rare.
- **The scoreboard/holds split is a draggable sash, not rows-above-scoreboard** (Change 4).
  Rows-first reverses the design's stated order; revisit after the next real use.
- **DPI awareness ships separately** (the window is bitmap-scaled at 250 % on the
  operator's display; its own spec, after the layout harness exists).

## Change 0 - the layout harness lands first

`tools/check_gui_layout.py`, new, is the precondition for every layout claim below: until
it runs, the audit's pixel numbers are reproducible only by their author. It instantiates
`SyncApp` for real (root withdrawn, workers inlined - the `check_gui_live_and_newer.py`
pattern) over stubbed engine reads: a three-account fake-cast store with an identity
disagreement, a 13-row converge plan, one suggested hold and one distinct hold with a
130-character title, a 78-row sync plan, the desktop app reported running, and the
work-area read stubbed so a display can be simulated. It asserts at 940x640, at the fit
floor of Change 1, at 871x432 (a 1366x768 panel at 150 % after window chrome), and at a
simulated work area smaller than the fit floor (683x384, the same panel at 200 %) where it
asserts that the computed minsize does not exceed the work area, independent of geometry
manager:

- every required control - the title filter and its four controls, the three RULING 8
  checkboxes and the consent box of Change 3, each tab's action bar, the window bar's three
  controls, the identity buttons - is managed (`winfo_manager()` non-empty), has a
  non-trivial allocation after `update_idletasks()` (`winfo_width`/`winfo_height`, with a
  1x1 allocation counted as "not drawn" - which is what the hidden fourth checkbox measures
  today), and lies inside its tab's visible area by its `winfo_x`/`winfo_y`, by attribute
  name. Not `winfo_ismapped`: under a withdrawn root it is False for every widget
  regardless of packing (the `check_gui_live_and_newer.py` docstring records exactly that),
  so an assertion on it would be vacuous in the very pattern this harness uses;
- every wrapping label's requested width is within its tab;
- the holds canvas is at least one row tall at 940x640 and at least scrollable at the floor.

At the simulated 683x384 work area the governing assertion is item 15b's, and the
required-control list there is the fixed chrome: every action bar, the role and status
lines, both filter rows with all four checkboxes and the consent box, and the window bar.
The scrolling middle of each tab is what yields below the fit floor, in this order: the
scoreboard pane to its two-line minimum, then the holds canvas to one row, then the plan
and report text panes to whatever remains, which may be a few lines - cramped is the
accepted state there, unreachable is not.

The `a32798b` bar-geometry pin in `check_gui_level.py` stays as it is. The harness joins the
manual `tools/check_gui_*.py` battery the same way the others are wired.

## Change 1 - the One-session tab fits, and every bar is pinned

Measured at 940x640 (tab inner width 916, filter frame 896): the filter row's controls pack
at x = 0, 192, 408, 488, 580, 770 with requested widths 192, 210, 76, 76, 184, 157, 168 -
the sixth ("only where mine is newer") is allocated 126 of its 157 px, the seventh ("allow
hiding a conversation") is allocated 1 px and not drawn. The positions reconstruct from the
source's `padx` values, so the arithmetic holds on any display.

1. **A two-row filter block.** Row 1: the "Only sessions whose title contains:" label, the
   Entry, "Apply filter", "Clear". Row 2: a labelled group, **"Refresh (opt-in for this
   run):"**, holding the three checkboxes in their current order with their current
   enablement rules ("only where mine is newer" and "allow hiding a conversation" enabled
   only while "Also refresh rows already there" is ticked), laid out with `grid` and column
   weights. The group label does part of Change 3's work: it says that everything on that
   line is the overwrite path. The 0.15.1 guidance line's advice about "only where mine is
   newer" moves here, next to the box it describes.
2. Wrap to width, not to a constant. One helper binds each wrapping label's `wraplength`
   to its container's `<Configure>` width minus a margin, replacing the nine `wraplength=880`
   constants (status, detail, guidance, warning, banner and gate lines) and applied to the
   hold rows' title and evidence labels too, so a 130-character title wraps instead of
   overflowing its row by 121 px at the minsize. Two facts keep that binding from looping:
   every scrollbar in this window is packed unconditionally, so a container's width never
   changes because content grew taller; and inside the holds canvas the row frame's width
   is already driven from the viewport - the existing `<Configure>` binding sets the canvas
   window item's width to the canvas width - so the rows' labels wrap to the viewport, not
   to their own content. Pixel constants remain only where Tk needs them (Entry column
   widths).
3. Every action bar bottom-pinned. Level already packs its bar first with `side="bottom"`
   so a short window squeezes the scrolling holds area and never the button. The One-session
   and Health bars are packed last and are the first thing a short window clips. All three
   pack first, bottom-pinned, with the role and status lines above them fixed; the middle
   region of every tab (the text panes, the holds canvas) is what scrolls, so it absorbs the
   shortfall.
4. Initial geometry and minsize from the work area, not from constants. Initial size is
   `min(940x640, work area minus window chrome)` - on Windows the work area comes from
   `SystemParametersInfo(SPI_GETWORKAREA)` via ctypes (it excludes the taskbar, which
   `winfo_screenwidth/height` do not); elsewhere, `winfo_screenwidth/height` minus 96 px
   in each dimension, a fixed margin the harness can encode (the store mutations are
   Windows-only today, so the non-Windows branch only has to keep controls reachable, not
   match a panel). The minsize is
   `min(fit floor, work area minus chrome)`, where the fit floor is the size at which the
   pinned bars, the status lines and one scrolling row fit - about 760x420 with the two-row
   block. The clamp is not optional: a 1366x768 panel at 200 % is a 683x384 logical desktop
   whose work area after chrome is under 360 px tall, so a constant 760x420 minsize would
   be larger than the screen and trap controls off-screen - the exact rule this item
   states. Below the fit floor the window is cramped but nothing is unreachable, because
   the bars are pinned and the middle scrolls (item 3).

## Change 2 - one identity answer, asked where it is read

Today `live_choice` (the sync pane) and `level_live` (Level) hold the same RULING 5 fact -
which store the desktop app is signed into right now - with different lifetimes and different
wordings, and the sync pane asks first, as a `Toplevel` without geometry or grab that opens
at the screen corner, because `SyncApp.__init__` dispatches `refresh()` before
`refresh_level()` and `resolve_sync_endpoints` refuses to plan under a disagreement.

1. **One variable.** `live_choice` feeds both `SyncFlags.live` and `ConvergeFlags.live`;
   `level_live` and `forget_level_live` go. Retitle stays out of it: `RetitleFlags` never
   receives it, and nothing here gates a rename on the answer (the identity design's
   "retitle keeps writing under a disagreement" non-goal is untouched).
2. **One place to answer at open: the home tab's in-pane banner.** `_render_banner` is
   already the better pattern - no popup, on the tab being looked at, one button per store
   labelled by email and row count. It becomes the one banner builder, parameterised by
   the tab it renders into: the banner frame, the replan callback (`refresh_level` on
   Level, `refresh` on One session - one variable, still two replan entry points) and the
   button copy. What that adds to the One-session tab is new chrome, said plainly: a banner
   frame between the status lines and the plan text, and the red in-force line with its
   button, where today the tab has only a bare "Change signed-in account" button in its bar
   and the "!! --live certification in effect" first line of its plan text. On its identity
   refusal the sync pane renders the banner in-pane above its verbatim refusal text instead
   of raising a `Toplevel`. The `Toplevel` picker survives only as the stage-2 question
   (item 4), centred and modal (Change 6).
3. **The sync pane plans lazily**, on the first selection of its tab: a per-tab `planned`
   flag, checked on *every* `<<NotebookTabChanged>>` event and set when the plan dispatches
   - never a one-shot unbind, which would fire once during a busy period and leave the tab
   blank for good. Every later trigger (Refresh, the filter's Return, the update toggle,
   the pickers) calls `refresh()` as today. `busy()` disables controls but does not
   stop tab switching, so a first visit while a worker runs records the visit and dispatches
   the plan when the busy count reaches zero *and that tab is still selected*; a user who
   clicked away gets the plan on the next visit, never a background worker and its pickers
   under another tab. **One deferral mechanism for every first-visit run** (this one and
   Health's doctor run in Change 6): each tab holds one "first visit pending" flag; a visit
   while a worker runs sets the flag and neither starts a second worker nor cancels the
   running one; when the busy count reaches zero, only the *selected* tab's pending run
   dispatches, and every other pending flag waits for its next visit; a pending flag is
   dropped, not dispatched, if the window is closing when the busy count reaches zero. So
   clicking through `Level -> Health -> One session` during a read starts at most one
   deferred worker, for the tab the user ended on. What moves with it: the identity
   question and the
   stale-saved-destination picker (`_plan_done`'s "be more specific" route) surface on
   first visit instead of at open. What does not: the Undo button's state comes from the
   Level render (`_render_level` calls `_update_undo_button`; the sync pane's own call is
   no longer the first one at open - a harness item pins Undo's state at open before any
   tab is visited), and the gate scan runs in the Level worker. Effect: no question at
   open unless the home tab has one, and one engine read fewer.
4. **Ask inside stage 2, from the fresh plan, if still unanswered.** When the fresh stage-2
   manifest carries `identity_disagreement` and no answer is held, the worker asks, then
   replans with the answer before the stage-2 dialog. The replan is not optional:
   `run_converge` acts on the manifest it is handed, and `_converge_recheck` refuses unless
   that manifest's `live_asserted` - written by `plan_converge` from `flags.live` - names
   one of the two disagreeing accounts.

   **The adapter contract grows by one member**, stated because `_run_level_sequence`'s
   contract is exactly five today (`status`, `gate`, `confirm_stage2`,
   `truncate_requested`, `remaining`) and the sequence NEVER raises - every outcome travels
   in the result dict, so no exception path can skip the post-sequence refresh, and a raise
   in a daemon worker would strand `busy(True)` with every control disabled. (The refresh
   itself is skipped on purpose for exactly the two endings the GUI 2.0 design already
   exempts, and this spec adds no third: a truncated sequence, because the window is
   closing, and a pure gate abort, because nothing mutated.) The sixth member is
   `ask_live(fresh) -> store path or None`. `_TkLevelUI` implements it through the same
   `_on_ui` marshalling `confirm_stage2` uses, with the picker call wrapped so that any Tk
   error returns `None` - a failed picker reads as Cancel, never as a raise - and the
   harness's `FakeUI` mirrors it method-for-method with a scripted answer. The sequence
   re-checks `truncate_requested()` after the picker returns, exactly as it does after
   `confirm_stage2`: a close confirmed while the picker sits open ends the sequence as
   `truncated` (no refresh; the window closes at the boundary), whether or not an answer
   was given. Outcomes therefore map onto existing states with nothing new: answer ->
   replan -> the stage-2 dialog; picker cancelled, closed, or failed -> `cancelled` (status
   "N renames landed, each undoable; the copy was not confirmed" - the landed clause
   discloses that the renames stand; the unconditional refresh runs); close confirmed
   during the picker -> `truncated`; the replan refused or erroring -> `plan_failed`.

   This asks at the moment the engine needs the answer, from the manifest the engine will
   act on - never before stage 1 from the stale preview, which would gate the renames the
   identity design leaves ungated - and removes the round-trip the current design allows
   (renames land, converge refuses with the verbatim RULING 5 text, the banner returns).
   `_converge_recheck` stays the gate. One line keeps the order honest for the user who
   will see renames land before the question: when the preview carried the disagreement
   and no answer is held, the stage-1 dialog says so - "The identity files disagree; the
   copy step will ask which account the desktop app is on before it creates rows." - so
   nobody reads the later question as the tool having written first and asked second.
5. **One clearing rule, shown identically on both tabs.** The answer is bound to the
   disagreement it was given for - the `(oauth account, config account)` pair
   `_identity_disagreement(env)` returned when the question was asked, the same pair the
   engine's own `_certified_live_account` records and revalidates for sync - and it is
   dropped when (a) the mutation path ran under it - a sync apply that wrote, or a Level
   sequence whose `run_converge` ran, `completed` or `unchanged` alike, because on an
   `unchanged` run the engine's apply-time re-check certified the answer and only then
   found nothing left to write, so the assertion was spent exactly as the CLI's would be
   (the zero-rows `empty` outcome is different: no converge ran, nothing was certified,
   the answer stays); (b) a fresh read
   of the identity files no longer returns that pair - the files agree, or they disagree
   about a different pair, or the same two accounts with the roles swapped; or (c) the user
   presses "Change signed-in account". It survives refusals (the common one is the running
   app, fixable in seconds), cancels, the zero-rows `empty` outcome, truncation and tab
   switches, while that disagreement stands.

   **Rule (b) runs before the answer is used, never after.** The order matters, and it is
   stated: every plan on either tab reads `_identity_disagreement(env)` *first* and builds
   its `ConvergeFlags`/`SyncFlags` afterwards, so a plan can never receive an answer the
   files have already outgrown; and every press that would consume a held answer - stage
   1's confirm, the stage-2 replan, sync's Apply - re-reads the files at press time, the
   same press-time re-scan precedent the mutation gate set, and on a changed or vanished
   disagreement drops the answer and replans instead of running. Undo is not on that list:
   an undo consumes the certification the *op* recorded, which the engine revalidates
   against the files itself, never the window's held answer, so the Undo press runs as
   today and its dialog keeps the op's own live-override note. Why the order matters
   rather than merely tidies: with the files agreeing and a
   stale answer still threaded into a plan, `_resolve_live_assertion` refuses at plan time
   ("... do not currently disagree", or "also matches N other account(s)" when the string
   is ambiguous), so a plan-then-drop order would turn the files healing themselves into a
   Refused pane on both tabs - a plan-time refusal on Level, and on the One-session tab one
   that `_plan_done` does not route to any picker. Drop-then-plan means the healed files
   simply produce a plan with no banner. The same order is what bounds the case a reviewer
   raised of a window left open for hours across a desktop account switch: the next press
   re-reads the files, and a switch the files registered - agreement, or the pair changed -
   drops the answer before anything runs; a switch neither file registered is the residual
   RULING 5 already carries for the CLI's `--live`, and the engine's apply-time
   revalidation (`_certified_live_account` for sync, `_converge_recheck` for converge) is
   the last check either way.

   Why the widening over today's Level behaviour is right: RULING 5's "an assertion covers
   one run" is one run of the *mutation path* - the engine certifying the answer and
   acting under it. A refusal ran nothing and a cancel ran nothing, so neither consumed
   anything, and re-asking after each is the "question re-asked so often it stops
   being read" the sync pane's own comment records as the reason its answer survives
   replans. After a write the post-mutation refresh plans without the answer, as the GUI
   2.0 design already requires; converge plans fine under a disagreement, so the Level pane
   re-renders with the banner back if the files still disagree, and the sync pane does not
   replan after its own apply, so nothing refuses.

   **The answer's states, normatively**: *absent* -> *held* by a banner button on either
   tab or by the stage-2 picker; *held* is shown on both tabs (the same red `!!` line and
   the same "Change signed-in account" button) and threads into every plan; every
   confirmation dialog that precedes a write under it - stage 1, stage 2, sync's two boxes
   - prints the assertion line ("under your assertion: the desktop app is on
   alice@example.com"), so the OK on that dialog is the per-operation affirmative act and
   Cancel keeps the answer with nothing run; *held* -> *absent* on (a), (b) or (c); nothing
   else clears it and nothing ever persists it.
6. **One wording**, the identity design's vocabulary: "~/.claude.json (aaaa1111) and
   config.json (bbbb2222) disagree about the signed-in account, and either can be the stale
   one. Which account is the Claude desktop app signed into right now? One session writes
   the OTHER account's store; Level writes every store that lacks a row, with the app closed.
   The answer holds until an apply runs under it or the two files stop showing this
   disagreement; it is shown while it holds and never saved." Buttons: "Desktop app is
   signed in as alice@example.com
   (aaaa1111…) org cccc3333… (286 rows)". The current sync picker's "the OTHER one is what
   gets written" is sync-specific and wrong for converge, which is why the shared text names
   both. Cancel keeps "fix it at the source", with the caveat that a desktop switch may not
   refresh `config.json`.

**Risk.** A shared, longer-lived answer means one tab can consume an answer given on the
other, and a user who switched the desktop account mid-sitting without either file
registering it is not caught. Both are the residual the CLI already carries for a user who
types `--live` on two commands in one sitting; the engine's revalidation
(`_certified_live_account` for sync, `_converge_recheck`'s membership test for converge) and
RULING 4's running-app guard bound them identically, rule (b) drops the answer the moment the
files can settle it, and item 5's dialog line makes every consumption visible. What no rule
catches is a wrong answer; that is RULING 5's premise, and the reason the question is asked
where it is read.

## Change 3 - tab order, names, role lines, and the single-session gate

Today: `Level | Copy & refresh | Health`, two verbs in step order and the diagnostic last,
which is how a checklist looks; the only role sentence in the window is the 0.15.1 guidance
line, packed under the bold "78 sessions ready to copy" on the exception tab; and with an
empty filter that tab's Apply is enabled over the whole bulk plan, one confirmation away
(the "Overwrite existing rows?" box fires only when refreshes are present).

1. **Order: `Level | Health | One session`.** The exception goes last; the diagnostic sits
   next to the home, so the gate line's "see Health" points one tab over. Level stays the
   selected tab at open.
2. **The gate, bound to the plan on screen.** Apply on the One-session tab is enabled when
   the *rendered* plan lists exactly one row, or when a checkbox **"copy every row this plan
   lists (N)"** is ticked. N is `len(manifest["rows"])`, and `rows` holds exactly what
   `run_sync` would write - adds and permitted refreshes alike - because `plan_sync` never
   puts a held, skipped, unchanged or filtered row in `rows`; those live only in the tally.
   So N is the number the confirmation already quotes, the label and the confirmation can
   never disagree, and a one-row plan is one write, never a held row masquerading as one. The box is off at every open and never remembered. It is bound to a digest of
   the rendered rows - per row the filename `name`, the `session_id` (a row's
   cliSessionId) and `is_update`, order-independent, computed in `_plan_done`: a replan
   whose rows digest identically keeps the tick, a replan that changes the row set clears
   it and updates the label's count, and every apply clears it. So toggling "only where
   mine is newer" back and forth does not cost the tick when the rows come back the same,
   while ticking "Also refresh", which adds refresh rows, does - consent was for a
   different N. Typing in the filter box changes nothing until "Apply filter" replans,
   exactly as today; a substring filter matching three sessions still needs the tick, with
   "(3)" in its label, and one matching one session does not. **A one-row plan is live
   whether the row is an add or a refresh**, and that is deliberate: the one-row refresh is
   this tab's intended routine use, the title filter applies to refreshes exactly as to
   adds (`select_sync_rows` filters on `--only` before it reaches the update branch, so one
   matched title yields one row, never one add plus its refreshes), and a refresh already
   passes RULING 8's two per-run opt-ins and the "Overwrite existing rows?" confirmation
   before it writes - the gate adds consent to *bulk*, not to overwrite, which has its own.
   The bulk copy stays available - it is a valid `sync`, and README calls this tab the only
   overwrite route - but it becomes a said-yes-to act instead of the default state of an
   empty filter. The engine is untouched: `plan_sync` plans as before; the window decides
   when Apply is live.
3. **Labels.** "Level" and "Health" stay. "Copy & refresh" becomes **"One session"**; with
   the gate, the label tells the truth about the default and the checkbox names the
   exception. The rename reaches README, the GUI 2.0 design's tab map, the module docstring,
   `SyncApp`'s comments where they name the tab, and the 0.15.1 warning text (whose "Level
   (the first tab) is the routine; this tab is for one session at a time" stays true).
4. **A role line in the same slot on every tab**, above the status line, in the muted
   style of the guidance label. It *replaces* `sync_guidance` (Change 1 moved its one piece
   of advice next to the box it describes), so no tab gains a preamble line net:
   - Level: "The routine. Measure the sidebars, name the held collisions, level them."
   - Health: "Diagnostic. The doctor report and any interrupted operation. Nothing here
     writes."
   - One session: "The exception. Copy one session to the other account, or refresh the row
     it already has there. Type its title in the filter; Level is the routine."

Why not the other options, stated so the review can disagree: disabling or hiding the
exception tab hides the only refresh route (RULING 8's home) behind a mode, which makes the
rare case harder without making the routine safer; an "Advanced" grouping says "for
experts", not "for one session", and a three-tab window does not need a tier; a first-line
sentence alone leaves the tab strip reading as steps, and the strip is what the eye reads
first.

**Risk.** The gate is new friction on a tab whose 0.15.0 contract said "unchanged in
behaviour"; it removes nothing and adds one tick, and it reopens that non-goal for exactly
one control. The contract gains its third and fourth carve-outs (the shared answer and lazy
plan of Change 2, the gate).

## Change 4 - Level: the sash, and the headline where a short box cannot hide it

Measured at 940x640 with the running-app notice, a 13-line scoreboard (186 px) and the
two-button identity banner: the holds canvas - the decisions, which the design calls the
point of the tab - gets 185 px; two rows need 163; at 760x520 it gets 65 px, less than one
row. The holds heading ("Naming decisions - 1 of 2 ticked …") is packed *inside* the
scrolled frame, so it scrolls away with the first rows. And `LEVEL_TEXT_MAX_LINES = 16` is a
cap in lines, not in available height, so a short window loses the holds before it loses a
scoreboard line.

1. **A vertical `ttk.PanedWindow`** between the scoreboard box and the holds area, sash
   draggable, the scoreboard defaulting to about seven lines and scrolling for the rest.
   What bounds the scoreboard's share is the sash, which the window sets at *every* render
   to `min(7, content lines)` of text height until the user first drags it, after which the
   user's position holds for the life of the window; content never pushes it past seven
   lines, so a 40-line traceback scrolls inside a seven-line pane and the holds keep the
   remainder - the bound `LEVEL_TEXT_MAX_LINES` provided, without the cap - and a window
   that opened on a two-line "store: not found" pane grows to seven when a real scoreboard
   arrives, rather than locking a later traceback into two lines. A `ttk.PanedWindow` sash
   is an absolute offset, so a dragged position survives a shrink only if clamped: on every
   window `<Configure>` the sash is clamped so the holds pane keeps at least one row's
   height and the scoreboard at least two lines, and a later enlargement restores the
   user's dragged offset. Nothing can push the holds to zero pixels. Not a
   height-aware line cap: a box whose height derives from the available height that the
   box itself feeds back into the pack negotiation can oscillate on resize; a sash has no
   such loop.
2. **The headline number in the detail line.** The one invariant the 0.15.1 code states -
   "the completeness line is the tab's headline number and must never sit below an inner
   scrollbar" - is kept by construction: the detail line under the status gets
   `_scoreboard_half(rep)` ("Level: 379 / 366 / 379 - 13 short.") in front of "Nothing is
   written until you press Level the sidebars.", so the number is above the box, not inside
   it. `_level_state`'s detail strings change; the `len(shown) <= height` pin in
   `check_gui_level.py` is replaced by "the detail line carries the half".
3. **The holds heading becomes a fixed header** above the canvas, outside the scrolled
   frame; it still follows the ticks live.

The design's order - notice, scoreboard, plan summary, identity banner, holds, footer - is
unchanged.

## Change 5 - the window bar, and a close that asks

Today the window bar packs Close at the left and the Undo button 6 px to its right; outside a
running mutation `_on_close` calls `root.destroy()` with no confirmation, so the typed hold
names `_merge_hold_models` preserves across every replan are lost to one click on Close or
the title-bar X. And "Let Chrome stay open", the only persistent setting in the window,
lives on the exception tab's bar although the helper exclusion it toggles (RULING 6/7)
applies to every mutation - a Level-only user never sees it, and the refusal they meet names
the CLI command rather than the checkbox two tabs away.

1. **Window bar: Undo at the left, the Chrome-helper checkbox in the centre, Close at the
   right.** Undo keeps its descriptive label ("Undo last converge (13 rows)"), its
   confirmation, and its hiding when nothing is undoable. The checkbox keeps its confirmation
   dialog; RULING 7's marker file and its default-off rule are untouched - only the widget's
   home moves.
2. **A dirty-close prompt on the idle path.** Close, and the WM close, confirm when any
   editable hold row's entry differs from its prefill or its tick from its default ("2
   unapplied naming changes will be lost. Close?" - "changes", because a changed tick
   counts as much as typed text), read through the same `_current_models` the apply uses.
   Both close prompts, this one and the interception's, are stock messageboxes with the
   default button on Cancel, the class Change 6 gives every stock confirmation. A WM close
   that arrives while a `_dialog` Toplevel holds the grab (the stage-1 dialog, a picker)
   cancels that dialog and does nothing else; the next Close, from the idle state, gets
   the prompt - one modal at a time, never a prompt fighting a grab. Ordering inside
   `_on_close`: the running-mutation interception stays first and
   unchanged (it decides whether the window may close at all); the dirty prompt runs only on
   the path that today goes straight to `root.destroy()` - `_mutation_ui is None` - so a
   confirmed dirty close can never tear the window down under a mutation worker. On the
   other path the typed names are at risk too: a close confirmed during a Level sequence
   truncates the remainder, and any edited row not yet applied goes with the window. So
   the interception's existing prompt gains the count when it is nonzero - "The current
   operation will finish, and the 2 remaining step(s) will NOT run; 1 unapplied naming
   change will be lost. Close?" - one prompt, both losses named, never two prompts in a
   row. (The stage-1 dialog runs on the UI thread before any worker exists and before
   `_mutation_ui` is set - `on_level_apply` confirms first and starts the thread after -
   so no worker is ever blocked inside it; a close during it cancels the dialog per the
   grab rule above.) A reviewer
   asked whether the prompt can race a replan and read a half-destroyed row: it cannot,
   because Tk callbacks run on one thread and `_render_holds` rebuilds every row inside a
   single callback, so `_on_close` never observes a row mid-destruction; `_current_models`'s
   `TclError` fallback exists for a different case (a widget already gone) and is not on
   this path.

## Change 6 - dialogs, the wheel, wording, the icon

**Dialogs.** The two pickers build `Toplevel`s with `transient` but no `grab_set`, no
position, no Escape binding and no focus; measured, such a window opens at screen (8, 31)
with the root at (308, 231). The stage-1 dialog grabs but has no position or Escape, and
its question lives only in the title bar. Mappings and local refusals render titles with
Python `repr` (`{0!r}`), so a title with an apostrophe renders in double quotes beside a
neighbour in single quotes. The stage-2 question names no account. One `_dialog(parent,
title)` helper serves every `Toplevel` - the two pickers, the stage-1 dialog, and the new
stage-2 identity picker: centred over the root, `grab_set`, `<Escape>` bound to Cancel,
initial focus on Cancel (so Return on a dialog whose list is the safeguard activates Cancel,
and Rename or the answer takes a click or a Tab; pinned by harness, since nothing pins focus
today), the headline as the first body line in the bold status font. `repr` gives way to
one quoting style, straight double quotes. Stage 2 lists the fresh manifest's
`destinations[].label` ("… into bob@example.com (bbbb2222/dddd4444)"). The sync second
box's title follows its body ("Refresh rows?" for a pure refresh; the two-step itself is
RULING 8's visibility rule and stays). Stock `messagebox` dialogs - stage 2, sync's two
boxes, undo, and the two close prompts of Change 5 - get the same posture through the one
knob `tkinter.messagebox` offers, `default="cancel"`: Return on any confirmation that
precedes a write activates Cancel, so a confirm always takes a deliberate click or a Tab.
A reviewer was right that keeping stock defaults would have left the most consequential
confirmations as the only ones Return could accept; the text is still the safeguard, and
now the click is not a reflex either.

**The wheel.** `hold_canvas` has no `<MouseWheel>` binding; a `Canvas` does not scroll
natively. Bind the wheel on the canvas and on each row widget as `_render_holds` builds it -
labels, checkbuttons and the one-line entries, none of which scrolls vertically on its own,
so nothing native is overridden; not `bind_all`, which would reach the scrolling `Text`
panes on the other tabs - normalising per platform (Windows `delta` in multiples of 120,
X11 `<Button-4>`/`<Button-5>`, macOS small integers) to one unit per notch. For the
keyboard, `<Prior>`/`<Next>` bound on the canvas *and* on each row widget, with the canvas
given `takefocus=1`, so paging works from a row's entry as well as from the canvas itself;
without the row bindings the canvas would never hold focus, since clicks land in entries.

**Wording**, each a one-line change:

- The One-session status becomes the honest count the 0.15.1 manifest can support: "78 row
  files missing from bob@example.com's sidebar - 77 of them for conversations it already
  opens under another row" (from `dup_conversation`); the empty state "No row files to add
  to bob@example.com's sidebar", not "the other account is up to date", which claims a
  conversation-level fact from a row-file count; and the *filtered* empty state, the
  `elif only:` branch between those two in `_plan_done`, moves to the same vocabulary ("No
  row files matching "Northwind" to add to bob@example.com's sidebar") rather than keeping
  "sessions" one branch up from the line that was just corrected. The duplicate-title
  warning above Apply stays.
- The running-app notice is measured on Level (`claude_running`) and static on One session
  ("The Claude desktop app must be closed for that step"); the same notice widget, fed by
  the same result, packs on both tabs.
- "The copy stage will refuse (RULING 5)" says what it means in words; the ruling name, if
  kept, trails in a parenthesis. The sync picker's text is Change 2 item 6.
- Health runs the doctor on the first selection of its tab (the same once-only, wait-for-idle,
  still-selected guard as Change 2 item 3, sharing its one-pending-run mechanism) instead of
  opening on "Press Refresh for the full health check." above an empty pane. Cost, said out
  loud: `on_doctor` takes the counted `busy()` lock, so every action button dips for the
  length of that first scan - what pressing Refresh does today - and `busy()` disables the
  hold rows' entries too, so a user who glances at Health mid-typing loses keyboard focus
  for that scan; the typed text is kept (the entries are disabled, not rebuilt) and the
  dip happens once per window.
- The copyable command and every sentence around it use one spelling. Decision: the long
  form, `claude-code-sessions recover`, everywhere in the window (`RECOVER_COMMAND` and the
  two `doctor_lines` sentences that say `ccs recover`) - both scripts are installed, and the
  long form is self-explanatory when pasted.
- "Nothing is ticked - tick a rename, or press Refresh." moves from the status line, where
  the bold font makes a no-op press look like a state change, to the detail line.
- The `!!` prefix on every warning line and the amber/red split (`#a05000` notice, `#a00000`
  gate and identity) stay; the amber is the weakest contrast (5.1:1 on the ttk ground) and
  must not shrink.

**The icon.** The root has no `iconbitmap`/`iconphoto`, so the taskbar and title bar show
Tk's feather while the shortcut shows the launcher's icon. Set an icon on the root (the
launcher's where the installed layout provides one, else a small embedded image); every
`Toplevel` inherits it.

## Defaults - no flips

The operator's rule: an option ticked most of the time defaults on, unless a documented
ruling keeps it opt-in because the friction is the safeguard. Applied to every control, the
review ends in visibility, placement and one new opt-in, not in flipped switches:

| Control | Default now | Recommendation | Why |
|---|---|---|---|
| Rename tick on a suggested supersession | on, prefilled | keep | the measured verdict; the stage-1 list is the opt-out's look (GUI 2.0 non-goal) |
| Rename tick on a degraded / distinct / unmeasured row | off, empty | keep | nothing to prefill; a ticked empty row refuses locally |
| Identity answer | none pre-selected, in-memory | keep; one shared answer (Change 2) | RULING 5: the user states a fact; a default would be the tool guessing |
| Destination | remembered on disk | keep | a stable fact about the machine |
| Title filter | empty | keep; the gate decides when Apply is live (Change 3) | the empty-filter bulk press is the tab's likeliest wrong move |
| "copy every row this plan lists (N)" | new, off, never remembered | add | the bulk sync becomes a said-yes-to act bound to a row set and its count |
| "Also refresh rows already there" | off, every open | keep off | the operator's rule would flip it; RULING 8 keeps the only overwrite route opt-in per run, and this checkbox is the ruling's own example - flipping needs the ruling amended, not recommended |
| "only where mine is newer" | on, qualified by the box above | keep | can only send fewer rows than the box it qualifies |
| "allow hiding a conversation" | off, never remembered | keep; make it visible (Change 1) | RULING 8's `held_orphan` opt-in; its default is right and its visibility is the bug |
| "Let Chrome stay open" | mirrors the RULING 7 marker | keep; move to the window bar (Change 5) | a view of the marker, not a default |
| Doctor report | not run at open | run on first selection of Health (Change 6) | a read; the blank "Press Refresh" pane was the window's only empty state |
| Sync plan at open | yes | first selection of the tab (Change 2) | removes the open-time popup; one read fewer |
| Window size | 940x640, minsize 760x520 | clamped to the work area; floor about 760x420; bars pinned (Change 1) | a minsize the work area cannot hold traps controls off-screen |
| Scoreboard box | cap 16 lines | about 7 lines under a sash; headline in the detail line (Change 4) | a cap in lines squeezes the rows first |
| Absent: `--verbatim`, `--include-deleted`, `recover` execution | absent | keep absent | friction that is the safeguard |

## Cross-cutting

- **README**: the window section's three bullets follow the new order and name; the "Copy &
  refresh" sentences become "One session" sentences; the count-disagreement sentence stays.
  The module docstring's tab map likewise. The GUI 2.0 design gains a dated revision note at
  its Tab 2 paragraph (the third and fourth carve-outs) and its `live_choice` paragraph, the
  way the 0.15.1 note was added at Tab 1.
- **Version 0.16.0**, proposed: the lazy plan, the shared answer and the gate are behaviour
  changes on two tabs. If the operator prefers 0.15.2, nothing else here changes.
- **Sequencing for the implementer**: Change 0, then Change 1 (the defect), then Changes 3,
  2, 4, 5, 6, in commits that each leave the harnesses green; the rename lands with the gate
  in one commit, never before it.

## Tests

Pure-function coverage (`tests/test_gui_models.py`; importable without tkinter):

1. `test_plan_digest_binds_consent_to_the_row_set` - same rows in any order digest the same;
   a changed `is_update`, a changed `session_id`, or a changed row filename digests
   differently.
2. `test_sync_apply_gate` - `_sync_apply_allowed(rows, consent)`: one row -> live without
   consent; zero rows -> never; two or more rows -> only with consent whose digest matches
   the rendered rows; a stale consent digest -> not live.
3. `test_dirty_holds_counts_edited_rows` - entry differing from prefill, or tick from
   default, counts as one unapplied naming change; untouched rows and read-only rows do
   not; the prompt text renders the count with the "change(s)" noun.
4. `test_live_answer_reducer` - the states of Change 2 item 5 as a pure reducer over events
   (`answered(pair)`, `mutation_ran` - a sync apply that wrote, or `run_converge` ending
   `completed` or `unchanged` - `files_read(pair or None)`, `explicit_change`, `refused`,
   `cancelled`, `empty`, `truncated`, `tab_switched`): `answered` holds; `mutation_ran`
   and `explicit_change` drop; `files_read` drops when its pair is None, a different pair,
   or the same accounts with the roles swapped, and keeps on the same pair; `empty` keeps,
   pinned separately from `unchanged` because the two are the seam a reviewer found; the
   rest change nothing.
5. `test_sync_status_line_counts_row_files_not_sessions` - the honest status and empty
   state from a manifest carrying `dup_conversation`; an older manifest without the flag
   falls back to the row-file count alone.
6. `test_dialog_quoting_is_not_repr` - a title with an apostrophe and one with a backslash
   render in one style.
7. `test_level_predicate_states` (existing) - the detail line now carries the scoreboard
   half; the rest unchanged.

Harness coverage:

8. `tools/check_gui_layout.py` (new, Change 0) at the three sizes, by control name.
9. `check_gui_live_and_newer.py`: no sync plan at open; the plan runs on the first
   selection of the One-session tab; a first selection while a worker is busy defers and
   dispatches only if the tab is still selected; clicking through all three tabs during a
   read dispatches at most one deferred worker, for the tab selected when the busy count
   reaches zero; the tab is third and named "One session"; the role line is present on all
   three tabs; the Undo button's state is correct at open before any tab is visited.
10. `check_gui_live_and_newer.py`: the gate - a one-row rendered plan enables Apply, add or
    refresh alike; a three-row plan disables it until "copy every row this plan lists (3)"
    is ticked; a replan that returns the same rows in a different order, driven through the
    real `_plan_done`, keeps the tick; ticking "Also refresh" (rows change) clears it and
    the label's count updates; an apply clears it.
11. `check_gui_level.py` item 10: `plan_converge` still runs three times on the answered
    path; a new item pins four on the path where stage 2 asks and is answered, and that
    the manifest handed to `run_converge` carries `live_asserted`.
12. `check_gui_level.py` items 14 and 20b re-pinned to the clearing rule: 14 keeps "cleared
    after a completed write" (rule (a)) and gains the `unchanged` case - a converge whose
    re-check writes nothing also clears, while the zero-rows `empty` path keeps the
    answer; 20b inverts - it is the pin that carries today's
    clear-on-every-ending rule, and under rule (a) a stage-1 refusal keeps the answer, the
    post-refusal replan carries it, and the banner shows the in-force line rather than
    the pickers. Part B item 14: the banner sets `live_choice`, and the same answer reaches
    the sync pane's next plan.
13. New: the stage-2 ask - a fixture whose files disagree and no answer held: the stage-1
    dialog carries the "the copy step will ask" line; the picker fires after the renames
    from the fresh plan; Cancel -> `cancelled`, renames stand, refresh runs, no
    `run_converge` call; an answer -> replan, stage-2 dialog carries the assertion line,
    converge runs; a picker that raises (fixture-side) -> `cancelled`, the busy count
    released, no stranded controls; an answer given and then a close confirmed before the
    replan -> `truncated`, no refresh, no `run_converge` call, the window closes at the
    boundary.
14. New: rule (b) and its order - an answer held, then the files made to agree
    (fixture-side): the next plan on either tab is called with `live == ""` (the spy sees
    the flags), the in-force line is gone, and no refusal appears; the same with the pair
    changed and with the roles swapped; and a press (sync Apply, stage 1's confirm) after
    the files changed drops the answer and replans instead of running - no `run_sync` or
    `run_retitle` call.
15. New: the dirty-close prompt - two edited rows, `_on_close` on the idle path -> the prompt
    names two; Cancel keeps the window and the text; an unedited pane closes without a
    prompt; the mid-operation interception (item 17) keeps its behaviour and its message
    gains "1 typed name not yet applied will be lost" when an edited row is unapplied at
    the boundary, and omits the clause when none is.
15b. `check_gui_layout.py`: at the simulated 683x384 work area the computed minsize is no
    larger than the work area, and the pinned bars and status lines are inside it.
15c. `check_gui_level.py`: the sash sits at the scoreboard's content height on a two-line
    first render and at seven lines after a full scoreboard arrives; after a scripted drag
    it keeps the dragged position across a replan; after a scripted shrink of the window
    the holds pane keeps at least one row's height, and a re-enlargement restores the
    dragged offset.
16b. Dialog pins, `check_gui_level.py` and `check_gui_live_and_newer.py`: every `_dialog`
    Toplevel opens centred over the root, holds the grab, closes on Escape as Cancel, and
    has initial focus on Cancel; every stock confirmation that precedes a write is created
    with `default="cancel"` (asserted through the recorded messagebox calls' keyword
    arguments, the way the harness already records them); a WM close arriving while the
    stage-1 dialog is open cancels that dialog and shows no prompt.
16c. `check_gui_level.py`: `<Prior>`/`<Next>` bound on the canvas and on each row widget,
    the canvas has `takefocus`, and a paging key sent to a row's entry scrolls the canvas.
16. `check_gui_level.py`: the stage-1 line and the stage-2 string re-pinned to the new
    quoting and to the destination labels; the holds heading is outside the canvas and still
    follows the ticks; the wheel binding is present on the canvas and on each row.
17. `check_gui_filter_doctor.py`: the doctor runs on the first selection of Health without a
    press; the first line is unchanged; `RECOVER_COMMAND` and the doctor lines agree on one
    spelling.
18. `check_gui_undo.py`: Undo at the left of the window bar, Close at the right, the Chrome
    checkbox between them; the gate on every tab unchanged.
19. `check_live_picker.py`: the refusal shape the sync picker matched now feeds the banner
    builder; the same refusal text renders in-pane under the banner.

## Non-goals, stated so the review can disagree with them

- **DPI awareness.** Its own spec, after Change 0 exists to measure it: system-DPI awareness
  only (`SetProcessDpiAwareness(1)`, guarded), one scale factor for the remaining pixel
  constants, acceptance at 100 %, 150 % and 250 %.
- **Flipping "Also refresh rows already there" on.** RULING 8; see Defaults.
- **Rows above the scoreboard on Level.** Reverses the design's order; the sash first,
  revisit after the next real use.
- **Disabling, hiding, or tiering the One-session tab.** Reasons under Change 3.
- **Rewording refusals into native GUI states.** The repo's design law; the trade-off
  (dense verbatim text habituates clicking through) is answered here by frequency - Changes
  2 and 6 move the questions to where the engine would otherwise refuse, so a routine
  sitting meets fewer refusals.
- **Reopening the two-stage failure matrix.** Change 2 item 4 adds one question and maps its
  outcomes onto states the sequence defines; it merges nothing and adds no state.
- **Accessibility beyond what is measured.** Contrast passes AA; keyboard scrolling of the
  holds arrives with Change 6; fonts do not follow the system text-size setting, and screen
  readers and high-contrast themes are untested. Left as known gaps.
- **Engine hygiene.** The window reads six underscore-prefixed engine helpers
  (`_identity_disagreement`, `_account_dirs`, `_listing_row_count`, `_length_clause`,
  `_overlap_clause`, `_live_override_note`), all read-only; promoting them to public names
  would make the thin-shell boundary auditable by grep. Not this spec.
- **A different sync planning model.** The engine forces the sync question at plan time
  (`resolve_sync_endpoints` refuses under a disagreement) while converge plans and
  discloses; that asymmetry is by design - sync's destination *is* "the other account" - and
  is why Change 2 moves the sync plan to first selection rather than asking the engine to
  plan without an answer.

## Review

**Round 1, 2026-09-01 evening.** Panel: Gemini (via agy) and Kimi (repo-aware) reported,
canaries verified; DeepSeek returned INCOMPLETE on the repo-aware route's per-request abort
after paging the engine source, no review text; Codex failed on upstream capacity
("Selected model is at capacity"). Kimi read both source files and the two Level harnesses
end to end and checked every code claim; its findings drove the round.

**Applied:**

- **[Kimi] The stage-2 identity ask is now a named adapter member, `ask_live`**, with the
  never-raises rule kept (a Tk failure returns None and reads as Cancel), the `FakeUI`
  mirror, and a truncation re-check after the picker - the sequence's five-member contract
  and its "never raises" invariant were unstated by the draft, and a raise in the daemon
  worker would have stranded `busy(True)`. Close-during-picker now maps to `truncated`.
- **[Kimi] Rule (a) is named as the reversal it is** of today's Level behaviour
  (`_level_apply_done` clears on every ending), with the argument for the widening written
  in Change 2 and the Decisions section, and 20b identified as the pin that carries the
  old rule.
- **[Kimi+Gemini] Rule (b) is pair-bound and runs before use.** The answer binds to the
  `(oauth, config)` pair it was given for - the engine's own certification shape - and
  every plan reads the files before building its flags, every consuming press re-reads them
  at press time. Kimi showed the plan-then-drop order would have turned healed files into
  `_resolve_live_assertion` refusals on both tabs; Gemini's open-for-hours account switch
  and its stale-sync-view press are bounded by the same order.
- **[Gemini] One deferral mechanism for every first-visit run**: one pending flag per tab,
  only the selected tab's run dispatches when the busy count reaches zero, never two
  deferred workers. Health's auto-run shares it.
- **[Gemini] The stage-1 dialog says the copy step will ask** when the preview carried the
  disagreement and no answer is held, so renames-then-question does not read as
  written-first-asked-second.
- **[Kimi] Change 0 no longer asserts `winfo_ismapped`**, which is False for every widget
  under the withdrawn root the harness pattern uses; it asserts `winfo_manager` plus
  geometry after `update_idletasks`, with a 1x1 allocation as "not drawn".
- **[Kimi] The gate states that a one-row refresh is live without consent**, and why; the
  digest names its three fields (row filename, `session_id`, `is_update`).
- **[Kimi] The shared banner builder's parameters and the One-session tab's new chrome**
  (banner frame, red in-force line) are named as new.
- **[Kimi] The sash's bound is stated** (set by the window at first render, never by
  content); the non-Windows geometry margin is pinned at 96 px; the filtered empty state
  moves to row-file vocabulary; dialog focus-on-Cancel is stated for stage 1 and pinned;
  the Health auto-run's focus loss is acknowledged; harness items 9, 10, 12, 13 and 14 gain
  the pins Kimi found missing (Undo at open, reordered-rows replan through `_plan_done`,
  picker-raises, answer-then-close, the drop-before-plan order and the press-time re-read).

**Rejected, with reasons:**

- **[Gemini] "One session" locks Apply at N=11 when one title is filtered but ten permitted
  refreshes exist.** The title filter applies to refreshes: `select_sync_rows` tests
  `flags.only` against every row before the update branch, so one matched title yields one
  row. Recorded in Change 3.
- **[Gemini] The dirty-close prompt can be bypassed by a close racing the worker's end.**
  Tk callbacks run on one thread; `_on_close` runs whole and reads the state at that
  instant, and the mutation interception precedes the prompt. Recorded in Change 5.
- **[Gemini] Ask the identity question before stage 1 "to preserve trust".** The identity
  design leaves retitle ungated on purpose (its non-goal), and asking from the stale preview
  would gate renames on an answer the engine never needs for them; the stage-1 line above
  is the mitigation.
- **[Kimi] The dirty prompt can read a half-destroyed row.** Same single-thread reasoning;
  `_render_holds` completes within one callback.

**Round 2, same sitting.** Panel: Gemini (via agy) and DeepSeek (sealed route, after its
round-1 repo-aware abort) reported - DeepSeek's canary quoted the last two lines rather
than one, a formatting quirk over a clean `finish_reason`; Kimi's repo-aware run hit the
loop-iteration cap and aborted with no text; Codex failed on upstream capacity for the
second consecutive round, so this run has no OpenAI voice.

**Applied:**

- **[DeepSeek, blocker] The minsize is clamped to the work area.** A constant 760x420 floor
  is larger than a 1366x768 panel at 200 % (683x384 logical), which the draft's own rule
  named as the failure to avoid. Change 1 item 4 now computes
  `min(fit floor, work area minus chrome)`, and Change 0 gains a simulated small-work-area
  case (harness item 15b).
- **[DeepSeek, major] The refresh invariant is qualified.** "No exception path can skip
  the refresh" is the never-raises rule; the refresh is still skipped by design for the two
  endings the GUI 2.0 design exempts, truncated and pure gate abort, and Change 2 says so.
- **[Gemini] Undo is off the press-time list.** An undo consumes the op's recorded
  certification, revalidated by the engine, never the window's held answer; the draft had
  made a healed pair swallow an Undo press. Change 2 item 5 and its dialog list corrected.
- **[Gemini] The close-during-mutation prompt names unapplied typed names.** The dirty
  prompt on the idle path did not cover a close confirmed mid-sequence, where the
  interception's own prompt fires; that prompt now adds the count of edited rows not yet
  applied. The stage-1 dialog runs before `_mutation_ui` is set, so a close during it is
  the idle path. Harness item 15 extended.
- **[Gemini] The sash follows content until the user moves it.** "Set at first render"
  would have locked a two-line first pane against a later traceback; Change 4 now sets it
  at every render to `min(7, content)` until the first drag. Harness item 15c.
- **[DeepSeek] The gate's N is reworded** so "held and skipped rows live in the tally"
  cannot be read as "rows contains them": `plan_sync` never puts a held, skipped,
  unchanged or filtered row in `rows`.

**Rejected:** nothing of substance in round 2; DeepSeek's "N contradiction" was a wording
defect, fixed above, not a design one - `rows` already excludes every non-written row.

**Round 3, same sitting - the cap round.** Panel: Gemini (via agy), DeepSeek and Kimi
(both on the sealed route, Kimi after two repo-aware aborts) reported; both roster
canaries quoted the wrapped paragraph rather than its physical last line, over clean
`finish_reason` values, so they count as reported. Codex failed on upstream capacity for
the third consecutive round: this spec's whole review ran without an OpenAI voice, which
is reduced coverage worth knowing. The fixes below are review-driven but not
panel-verified, since the cap ends the loop; the implementing session should read Change
2's clearing rule and Change 5's close paths most skeptically.

**Applied:**

- **[DeepSeek+Kimi, from opposite sides] Rule (a) and `unchanged`.** DeepSeek read
  `unchanged` in rule (a) as contradicting "one write"; Kimi found the reducer's
  vocabulary had no `unchanged` event at all, leaving the branch to implementer
  discretion. Resolved toward the engine's own semantics: on an `unchanged` run
  `run_converge` certified the answer at its re-check and only then found nothing to
  write, so the assertion was spent as the CLI's would be; "one write" was the wrong word
  and now reads "one run of the mutation path". The reducer gains `mutation_ran` covering
  `completed` and `unchanged`, `empty` is pinned as a keep, and harness item 12 pins both.
  DeepSeek's proposed direction (keep the answer across `unchanged`) is rejected on that
  ground.
- **[Gemini, blocker] The lazy plan's guard is a persistent per-tab flag** checked on
  every tab-change event, never a one-shot unbind, which would have left a tab visited
  during a busy period blank for good; a pending flag is dropped if the window is closing.
- **[Gemini] The sash is clamped on resize** so the holds pane keeps one row's height and
  the scoreboard two lines, with the dragged offset restored on enlargement; an absolute
  sash offset would otherwise push the holds to zero pixels on a shrink.
- **[DeepSeek] Stock confirmations get `default="cancel"`.** Keeping stock defaults would
  have left the most consequential confirmations as the only ones Return could accept,
  against the friction principle; `tkinter.messagebox` has the knob, so it is used
  uniformly. Harness item 16b pins it with the `_dialog` geometry and focus pins Kimi
  found the draft had claimed without scheduling.
- **[Kimi] The close prompts join the dialog taxonomy**, as stock boxes with the Cancel
  default; a WM close while a `_dialog` holds the grab cancels that dialog and shows no
  prompt, so no prompt ever fights a grab.
- **[Kimi] The wrap helper's no-loop conditions are stated** (scrollbars packed
  unconditionally; the holds rows already take their width from the canvas viewport
  through the existing `<Configure>` binding).
- **[Kimi] Change 0 and item 15b are reconciled** at the small work area: 15b governs, the
  required controls there are the fixed chrome, and the yielding order of the scrolling
  middle is written down.
- **[Gemini+Kimi] Keyboard paging is deliverable**: `<Prior>`/`<Next>` on the rows as well
  as the canvas, and the canvas takes focus. **[Kimi] Small text fixes**: the prompt says
  "naming change(s)" since a changed tick counts; the tab-order example in Change 2 item 3
  follows the new order (DeepSeek); the shown answer text names both clearing conditions.

**Rejected, with reasons:**

- **[Gemini] Wheel bindings on row widgets hijack native scrolling.** The row widgets are
  labels, checkbuttons and one-line entries; none scrolls vertically on its own, so there
  is nothing to hijack. The concern is real for `Text` panes, which is why `bind_all` is
  avoided.
- **[Kimi] A worker blocked inside `confirm_stage1` on a confirmed close.** No worker
  exists during the stage-1 dialog: `on_level_apply` confirms on the UI thread first and
  starts the thread afterwards. Recorded in Change 5.

**Loop closed at the 3-round cap.**
