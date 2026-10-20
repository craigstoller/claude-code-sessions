# Internals: how Claude Code / Claude Desktop store sessions

This is background for anyone extending or auditing `claude-code-sessions`, not a user guide. It
describes the on-disk layout the tool depends on and refuses to guess about.

> Every claim below was **observed July 2026, app channel: Claude Desktop (Windows); format may
> change.** This is a reverse-engineered format, not a documented one — treat all of it as
> subject to revision by a future app release, and prefer deriving facts from the disk over
> hard-coding them (see `doctor` and the encoding-scheme detection below).
>
> Last confirmed end to end against a live store on **2026-07-31, Claude Desktop (Windows)
> app 2.1.219**: a session was relocated between projects, resumed by the app at its new
> location, and an interrupted move was recovered in both directions.

## Where things live

*(observed July 2026, Claude Desktop (Windows); format may change)*

**Transcript** — the actual conversation:

```
C:\Users\<you>\.claude\projects\<encoded-cwd>\<cliSessionId>.jsonl
```

Some sessions also have a folder of the same name beside the `.jsonl`, holding subagent
transcripts. It travels with the file.

**Listing metadata** — one JSON per session, holding its title, `cwd`, timestamps, model, and
the `cliSessionId` pointing at the transcript above:

```
C:\Users\<you>\AppData\Roaming\Claude\claude-code-sessions\<account-id>\<org-id>\local_<appSessionId>.json
```

Also in that folder, and **not** session metadata: `scheduled-tasks.json`, `deleted_<id>`
markers for removed sessions, and transient `*.json.tmp` files. Any bulk operation should glob
`local_*.json` specifically.

**That `%APPDATA%` path does not exist for most processes.** Claude Desktop on Windows is an
MSIX package (`Claude_<id>`, installed under `C:\Program Files\WindowsApps\`), so its writes to
`%APPDATA%\Claude` are redirected into the package's private storage. The real location, and
the one to use from scripts, is:

```
C:\Users\<you>\AppData\Local\Packages\Claude_<id>\LocalCache\Roaming\Claude\claude-code-sessions\...
```

*Moved, 2026-10-19: Desktop 2.42304.2 moved the whole user-data folder, store included, out of
the package to `C:\Users\<you>\AppData\Local\Claude-Data\claude_<pkg>\`, and left a 0-byte
`.user-data-left.claude_<pkg>` in the old `Claude` folder. The browser helper stayed in the
package. See "Amendment 2026-10-19" under the running-app guard below. The text here records
the layout before that build.*

Only processes carrying the package identity — the app, and shells it spawns — see the merged
view where `%APPDATA%\Claude` appears to exist. From an ordinary terminal it is absent. Both
paths are the same bytes; resolving the real path on the virtual one lands on the `LocalCache`
location. Resolve the `LocalCache` path first, fall back to `%APPDATA%`, and treat a missing
store as a hard error rather than an empty result — a glob over a non-existent directory
returns zero rows, which is indistinguishable from "this session has no listing row" unless the
absence is checked for explicitly.

Note that `~/.claude/projects` is **not** redirected — it is an ordinary profile directory that
both contexts see identically. Only the `%APPDATA%` half of the two-layer model below is
affected.

## The two-layer model

*(observed July 2026, Claude Desktop (Windows); format may change)*

This is the part that is easy to get wrong, because the two layers behave differently.

| Layer | Shared or copied? | Consequence |
|-------|-------------------|-------------|
| Transcript (`.jsonl`) | **One genuinely shared file.** Carries no account identifier at all. | Any account whose listing points at it reads and writes the same bytes. Continuing a session from one login and opening it from another login shows the new content immediately — *while both rows still point at the same transcript*; see "The fork mechanic" below for the two ordinary gestures that end that state. |
| Listing (`local_*.json`) | **Per-account copy.** | Which sessions appear, plus title/timestamp/sort order, is private to each login and frozen at copy time. |

So relocating or copying a session's listing entry is a **snapshot**, not a live sync of the
conversation — a resumed session's row goes stale elsewhere until refreshed (`sync --update`,
RULING 8; before that existed, re-running sync skipped it as "already present" and it stayed
stale forever). The conversation content itself was never copied; only the pointer to it was,
which is why a stale row never means stale *content*: the transcript is shared and current, and
only the sidebar's snapshot of its title, position and turn count is out of date.

**Verified end to end on a real machine, 2026-08-19** — this had been a design claim, argued
from layout, and it was worth stopping to actually test because a user reported the opposite
("the sessions were there, but they're not updated with the latest chat history"). Three
accounts, 346 sessions present in more than one of them:

- **320 of those sessions resolve to exactly one transcript on disk. None resolve to more
  than one.** There is no per-account copy of a conversation to fall out of date.
- A listing row's complete key set is metadata — `title`, `cwd`, `model`, `completedTurns`,
  `lastActivityAt`, permission and MCP fields. **No messages, no transcript excerpt, and no
  offset, cursor or checkpoint into the conversation.** There is nothing in a row with which
  the app *could* truncate history.
- The decisive test: session "Quarterly board report finalization", whose row read **28 Jun** in two
  accounts and **3 Aug** in the third — 36 days apart. Opened under one of the stale rows, the
  full 3 August exchange rendered, including the five messages that postdate the row's own
  timestamp by more than a month.

**So `completedTurns` must not be read as a measure of how much conversation exists**, and the
number is a trap for exactly that reason: rows for one session read 17/33/33 against a
transcript holding **472** typed messages, and another read 33/37 against **52**. It counts
something internal (turns since a context compaction, most likely) and matches nothing a user
can see on screen.

What a stale row costs is therefore **not the content of the conversation it points at**.

**But it can point at a DIFFERENT conversation — and that is the case that matters most
(measured 2026-08-19, and it corrects the paragraph above).** A row's filename is the *app's*
session id (`local_<appSessionId>.json`), which is stable across runs, while `cliSessionId` —
the transcript it resolves to — changes when a session is resumed as a new CLI run. So one
session slot accumulates several transcripts over time, and **each account's row records
whichever one that account last saw**. Two accounts can hold the same filename pointing at
entirely different conversations:

```
local_a00afbc1-….json   in alice@example.com -> 0678aca4  17.9 MB, 1386 msgs, last used 19 Aug
local_a00afbc1-….json   in bob@example.com   -> c92ae7b1  13.0 MB,  738 msgs, last used 14 Aug
```

Both are titled "ACME-REVIEW session Northwind". Opening it under the second account does
not show a truncated version of the first — it shows **a different conversation**, complete and
current as of its own last use. To a user that is indistinguishable from "my history is behind",
and it is the far more serious failure: the newer conversation is intact on disk but
**unreachable from that account's sidebar**. On this machine, **15 of 333 shared rows** were in
that state, with the newer conversation on the alice side for 9 and the other side for 6.

Two consequences worth stating plainly:

- **`--update` is not cosmetic.** Rewriting the row rewrites its `cliSessionId`, which is what
  restores access to the newer conversation. This is the strongest reason the feature exists.
- **Direction is critical, and the wrong direction is destructive.** Refreshing *from* the
  account holding the older pointer overwrites the row that pointed at the newer conversation,
  making it unreachable from there too. `--newer-only` is what prevents that, and this is the
  case it was really built for — not tidiness. Undo restores the pointer, but only while the
  op remains in the journal.

The secondary cost is still real and still worth fixing: a stale row keeps an old title and
sorts by an old `lastActivityAt`, so a session used this week sits weeks down the other
account's list under a name it has outgrown.

## The fork mechanic — resumes and the rewind button (measured 2026-08-29, app 1.40609.0.0)

The section above says a row "accumulates several transcripts over time" without saying what
produces them. Three ordinary gestures do, and they share one mechanism — a new
`cliSessionId`, and the live account's row repointed at it. **They differ in how much of the
old conversation the new transcript actually contains, and that difference matters to every
tool that compares the two:**

- **Resuming** a session starts a new CLI run whose transcript carries the displayed history
  forward (the same fact stated above as "`cliSessionId` changes when a session is resumed
  as a new CLI run"; this section is about what that does to the rows).
- **The rewind button** ("rewind to here", used to edit an earlier prompt and resend) is not
  an in-place edit: it creates a new conversation holding the history up to the rewind point
  plus the edited prompt.
- **Compaction** — a long session running out of context — also forks, but the new leg
  begins with a *summary* of what came before rather than the turns themselves. **So the
  continuation can be far smaller than its parent**, which is the opposite of the other two
  gestures and is the case that breaks naive "which leg is superseded" reasoning. Measured
  2026-09-01 on a compaction pair: parent 429 prose turns / 8.8 MB, continuation 128 turns /
  4.8 MB, 84 shared — the *continuation* was 0.66 contained in the parent while the parent
  was only 0.20 contained in it. Read carelessly, the live leg looks like the redundant one.

In both cases the app then **repoints the live account's existing row** at the fork. Not a
new row — the same `local_*.json`, keeping its `createdAt` to the millisecond, its title, and
its `titleSource`; at the fork event, the pointer is what changes. (The app's ordinary row
drift — `lastActivityAt`, the RULING 8 `held_same` fields — continues afterwards as always;
this is a claim about the event, not about the row being otherwise frozen.) The original
conversation keeps its bytes on disk and loses exactly one thing: that account's pointer.

The evidence, scoped to what was actually measured — one rewind-to-edit on a real
three-account store, plus prose-overlap measurement of three fork pairs from the same week:
the two transcripts' copies of the edited prompt differed by **exactly the words the edit
added** (seven, in the measured case), the original leg ends in an interruption marker
nineteen seconds after the first send, the fork's copy lands thirty seconds later, and
`isArchived` was `False` on every row involved across all three accounts. No flag the rows
carry marks the abandoned leg; whether some other mechanism marks it elsewhere was not
probed — what the census below observed in practice is these legs sitting unreferenced with
nothing `doctor` recognizes as a marker. The three fork pairs measured **0.98, 0.98 and
0.80** prose overlap between superseded leg and continuation — high because a *rewind*
carries the prefix forward, so near-total overlap is how that gesture presents; those three
are the calibration behind the measured hold remedies' supersession band
(`docs/specs/2026-08-30-hold-remedies-design.md`).

**That calibration does not generalise to compaction forks, and 2026-09-01 proved it.** An
earlier version of this section said the fork "shares its entire prefix by construction",
citing the rewind pairs as though they characterised forking in general. A compaction pair
measured the same week landed at 0.66 / 0.20 — inside the band's inconclusive zone, with the
higher containment on the *live* leg. The band's design is what saved the outcome: overlap
nominated the live session as superseded, recency contradicted it, and the two-signal
agreement rule refused rather than renaming the conversation the operator was typing in
(reason `overlap and recency disagree`). **The lesson for anyone extending this: overlap
alone cannot order two legs, because which leg is larger depends on which gesture forked
them.** Recency is the corroborating signal precisely because it is independent of that.

The consequences key on **how many rows still point at the old leg** — rows, not accounts,
are the unit (the 0.9.13 lesson elsewhere in this file), though in practice the common cases
line up one per account:

- **Zero surviving rows — the usual single-account outcome — and the old leg is
  sidebar-unreachable.** The sidebar entry itself survives, title intact, now opening the
  fork; what a user experiences is not a missing entry but edited history, with the old
  leg's tail gone. This lineage was the largest class in a machine-wide orphan census here
  (2026-08-26: 52 of 187 unreferenced transcripts attributed as ancestors of reachable
  conversations, by prose-overlap against every reachable transcript; `doctor` had counted
  155 unreferenced on the same store five days earlier — the store grows). `new-row` is the
  way back **while the transcript survives**: retention (above) deletes unreferenced
  transcripts on the app's cleanup clock, 30 days by default, so an orphaned leg worth
  keeping has a deadline, not just a remedy.
- **Surviving rows elsewhere keep the old leg reachable — and collide.** Rows in other
  accounts were not repointed, so they still open the original: nothing lost, but both legs
  now sit under one title, because the repointed row brought its name along. This is the
  standing producer of `converge`'s title-collision holds (`converge` shipped in 0.12.0;
  the holds and their measured remedies are in the spec linked above). On this machine,
  every converge pass since the command existed has carried holds from this mechanism —
  twelve, then eight, then six — so here they are the expected texture of a pass rather
  than a sign of damage. (A later `sync --update` would propagate the fork's pointer over a
  surviving row; RULING 8's `held_orphan` exists for exactly that displacement.)

One field deserves its own sentence: rows carry a `forkedFromSessionId`, and the rewind path
**leaves it empty** — the repointed row is not new, so no breadcrumb lands. Older rows on
this machine do carry the field, so some app path that creates fresh rows for forks writes
it; that path was not the one measured here. The practical consequence stands either way:
there is no reliable parent edge in metadata (the shared row *filename* across accounts
hints that two transcripts belong to one lineage, but proves neither direction nor
parentage), which is why the measured remedies compare transcript content instead of
reading a field the rewind path never writes.

(The 2026-08-21 orphaning incident under RULING 8 is this same mechanism crossed with an
account switch: a resume under the wrong login repointed a row in a store that was holding
the *newer* leg. The gesture is ordinary; the storage model is what makes its cost vary from
"invisible bookkeeping" to "a conversation no sidebar opens".)

## The encoding rule

*(observed July 2026, Claude Desktop (Windows); format may change)*

`<encoded-cwd>` is the project's working directory with **every character outside
`[A-Za-z0-9]` replaced by `-`** — including `:`, `\`, spaces, dots, and (as of the change below)
underscores. So `C:\Users\<you>\Projects\_Tools\my-project` becomes
`C--Users-you-Projects--Tools-my-project` — note the doubled `-` where `\_` was.

> **The rule changed around 2026-07-12.** Underscores used to survive encoding and now do not,
> which is why some projects have a folder in each form: an older-scheme folder from before the
> change, alongside a current-scheme folder from after it. Old folders are not migrated — their
> transcripts and per-project memory simply stop being read once the app moves to the new
> scheme.
>
> **Do not hard-code this rule.** Derive it from folders the app itself created: take recent
> `cwd` values out of the listing store, encode each under both schemes, and keep whichever
> matches real directories on disk. Getting it wrong files a transcript where the app will never
> look, and the session vanishes from the sidebar. `claude-code-sessions` does exactly this evidence
> comparison before every move (see `scheme_evidence` / `choose_scheme`), and refuses to proceed
> if the evidence is genuinely tied.

## Worktree transcript placement

*(observed July 2026, Claude Desktop (Windows); format may change)*

A session started inside a git worktree records the **main repository's** path as `cwd` in its
listing row and transcript entries, but the transcript file itself is written under the
**worktree's own** encoded folder, not the main repo's. Code that resolves "the" transcript
folder for a session from `cwd` alone, without scanning all project folders, will wrongly
report such a session as missing.

## Retention

*(observed July 2026, Claude Desktop (Windows); format may change)*

Transcripts are cleaned up on a retention timer, `cleanupPeriodDays`, defaulting to **30 days**
when unset in `~/.claude/settings.json`. A listing row whose `cliSessionId` no longer resolves
to any transcript on disk is consistent with this: the transcript aged out from under a row
that still references it, which is a normal, expected state rather than corruption.

## Deletion records ("tombstones")

*(observed July 2026, Claude Desktop (Windows); format may change)*

Deleting a session in the app writes a `deleted_<id>` file beside the listing rows, inside
`<accountUuid>/<organizationUuid>/`. It is 13 bytes: an epoch-millisecond timestamp of the
deletion. The listing row is **removed outright**, not blanked in place, and the **transcript
is left on disk**.

**`<id>` is not always the `cliSessionId`, which is the trap.** The obvious reading — and what
the first measurement here recorded — is `deleted_<cliSessionId>`. That is incomplete. A
session on the author's own store carries *two* tombstones: one named for its `cliSessionId`
(`bc7333f9…`) and one named for its **local id**, the `local_<id>.json` filename stem
(`747a0b6e…`). So deletions are filed in **both id spaces**, and anything that honours
tombstones has to test both. Checking only the session id misses a real deletion silently;
`sync` checks both, and errs toward declining to copy, because a false positive is one named
row you can override and a false negative resurrects something someone deliberately deleted.

Two further consequences that are easy to get wrong:

- **They are per-account.** A deletion under one account says nothing about what another
  account should see — a tombstone lives inside the same per-account folder as the rows it's
  paired with, never anywhere shared.
- **The app writes them but does not honour them.** Deleting a disposable session, then
  restoring its backed-up listing row while leaving the tombstone in place, then relaunching the
  app, showed the session in the sidebar again. The tombstone survived the launch with its
  contents unchanged — nothing consumed or pruned it.

So any tool that copies listing rows between accounts must consult the *destination's*
tombstones itself; nothing in the app stops a copy from resurrecting a session the user
deliberately deleted. `sync` (see the README) reads the destination's tombstones and skips any
source session they cover for exactly this reason. As far as we found, this is undocumented
elsewhere: a review of seven other Claude session-copying tools' source, READMEs, and docs
turned up zero mentions of `deleted_*` files, "tombstone," or soft-deletion of any kind.

## The identity-file model and the running-app guard

*(describes this tool's own design, informed by measurements of the app's identity files —
see `_identity_disagreement`, `live_account`, `claude_running`, `_is_cli_process`, and
`_guard_mutation` in `claude_code_sessions.py`)*

Two files claim to name the signed-in account, and they have two different owners. The likely
mechanism, consistent with what has been measured but not itself verified: the Claude Code CLI
owns `~/.claude.json`, and the desktop app owns `config.json`'s `lastKnownAccountUuid`, so each
kind of sign-in freshens only its own file.

Staleness has been measured in **both** directions, which is why neither file is trusted alone:

- **2026-08-02 (E4 verification):** across a real desktop account switch, `~/.claude.json`'s
  `oauthAccount` stayed **stale** while `config.json`'s `lastKnownAccountUuid` tracked the
  switch — the inverse of the trust ordering this tool originally shipped with.
- **Whole-branch review (earlier):** the opposite case — `config.json` stale, `oauthAccount`
  fresh — had already been built synthetically.

So either file can be the one that's wrong, and there is no way to prefer one over the other
from the files alone. `_identity_disagreement(env)` treats a disagreement between them as **no
signal about which one is right**, not as something to break a tie on: `live_account()` returns
`None` the moment the two disagree, and `resolve_sync_endpoints` (every route that plans a
sync) refuses outright rather than guess, naming both files' 8-character id prefixes and the
fix — re-authenticate the CLI (run `claude`, then `/login`) as the account in use, or switch the
desktop app to it, so the two files agree.

Agreement between the files is therefore read as **consistency, not liveness**: two files that
happen to name the same account is weaker evidence than it looks, because both could still be
stale in the same direction (e.g. both left over from before a switch neither has registered
yet). The actual safety mechanism for every mutation is the running-app check
(`_guard_mutation`), not the identity files — it applies unconditionally to every sync mutation
route regardless of which file (or whether either) resolved the live account.

### RULING 5: the user-certified liveness override (`sync --live`)

*(2026-08-05. The one sanctioned exception to "refuses rather than guessing" — see
`_resolve_live_assertion`, `_certified_live_account`, `_refuse_dest_possibly_live`, and
`_live_override_record` in `claude_code_sessions.py`.)*

The disagreement refusal above was never "the files are the safety" — the running-app guard
(RULING 4) is what binds every mutation regardless of what the files say, and it is a
point-in-time check with an accepted TOCTOU residual, not a proof. The refusal was "the tool
will not *guess* between two claims it cannot rank." `sync --live <account>` does not add
guessing; it adds the one witness who can rank them. The certified fact is defined narrowly:
**"this is the account the Claude desktop app is currently signed into."** Desktop liveness —
not CLI authentication — is what `sync`'s safety model cares about: these are the desktop's
stores, and the guard is about the desktop's processes. The two files can even both be right
for their own application (CLI authenticated to A, desktop signed into B — each owner
freshening only its own file, per the likely mechanism above); there is still exactly one
desktop-liveness fact, and the user is its authoritative source: they can look at the app.
That is the asymmetry with the rejected macOS layout override (`_require_verified_platform`,
which deliberately has **no** flag): a user cannot evaluate a store-layout risk, but "which
account is my desktop app signed into" they can simply see. `/login` was always the user
asserting a related fact slowly and by side effect — freshening one file until the two agree;
`--live` is the same knowledge asserted directly.

The constraints that keep it a certification rather than a `--force`:

- **It must name the account** — id, org, path, or email substring, `--to`'s exact matching
  semantics over the two named accounts' on-disk stores; ambiguity is a refusal listing
  candidates (with row counts), and an empty or whitespace value is refused outright, since
  substring containment would let it match everything.
- **It must name one of the two accounts the files name.** The assertion arbitrates a specific
  two-way tie, cross-checked against a file that already names that account. A substring
  matching some third account is refused as evidence of something else being wrong; the
  agreeing, no-evidence, and config-only-ambiguous-org states are refused too — there the
  assertion would certify a bit no file corroborates at all.
- **Fail-closed default unchanged**: without the flag, byte-for-byte today's refusals (which
  now advertise `--live` as the fast path beside `/login`).
- **RULING 4 untouched**: the guard never reads the manifest and applies unconditionally
  before every mutation, `--live` or not. The flag converts only the identity refusal.
- **Loud everywhere**: the dry run and apply print a `!! LIVE-ACCOUNT OVERRIDE` banner naming
  the overridden file and its stale uuid; `--json` (which prints no report and executes first)
  gets the banner on stderr while stdout stays machine-pure; `undo` and `recover` print a note
  before mutating a certified op. All of it is derived at print time from the operative fields
  (`pair` + `account`), so no manifest edit can make the output name the wrong file.

The certification is journaled (`live_override`: the asserted account, the ordered
`[oauth, config]` pair, the derivable overrode-file/uuid audit fields, and an unvalidated
best-effort `config_path`) and **revalidated at every mutation the operation ever performs** —
execute, `recover --forward`/`--back`, `undo` — by `_certified_live_account`, which is
tri-state on purpose. *Absent* → exactly the pre-RULING-5 rules, so nothing changes for
ordinary ops. *Valid* (the same ordered pair still on disk, the asserted account a member and
equal to the manifest's own `source_account`, audit fields telling the same story) → the
disagreement-names-the-destination refusal narrows: the certified account's own store still
refuses, the other named account's store — the asserted-dormant one, the whole point —
proceeds. *Void* (anything else, garbage shapes included) → refuse outright, unless
`live_account()` now resolves, in which case the ordinary live-match rules apply in full — the
certification is moot, not honored, and single-file resolution counts, because that is the
evidence level every uncertified sync already plans and executes on. Order matters in the pair
comparison: a flipped direction means both files changed claims since planning, and a
twice-moved world is not the tie the user arbitrated.

Two consequences recorded honestly rather than hidden:

- **The hand-edited-manifest posture changes shape here.** `live_override` is the one manifest
  key the executor honors for an identity decision — and only after revalidating all of it
  against the identity files on disk at that moment. A fabricated record that names the real,
  current disagreement in the current direction *and* matches the manifest's own source does
  certify; what that unlocks is exactly the action the flag lets the user authorize from the
  command line — a write into the asserted-dormant store while the app is closed — and nothing
  else. RULING 4 stays out of reach of any manifest content.
- **Same-pair recurrence is an accepted residual.** An ordered uuid pair is not a timestamp:
  if the disagreement resolves and later re-forms identically while a non-terminal `--live` op
  sits in the journal (or before an `undo`), revalidation cannot distinguish the re-formed tie
  from one that never moved, and the certification is honored even though desktop liveness may
  have flipped in between. Fingerprinting the identity files was rejected — `~/.claude.json`
  is the CLI's whole config, rewritten constantly, so any interim CLI use would void the
  record and make `undo` of a `--live` sync effectively impossible — and a TTL is an arbitrary
  constant pretending to be knowledge. Bounded by the running-app guard on every mutation,
  `doctor` flagging the non-terminal op the whole time, and the pre-mutation notes above.
  Same posture as the TOCTOU residual below: named, not closed.

One asymmetry worth naming: if the files come to agree on the *destination* account by undo
time (the user actually signed into it), the ordinary live-match refusal now protects it and
the `--live` sync cannot be undone by this tool. That is the live-store protection working,
not a lockout — the refusal names the escape routes (delete the rows in the app itself, or
sign the desktop out of that account and re-run).

### The process-narrowing rule

`claude_running(env)` has to answer "is the *desktop app* running," not "is anything named
claude running" — the Claude Code CLI is itself a native `claude.exe` (since ~2.x) sharing the
desktop app's image name on Windows, so a literal name match would refuse every mutation while
an ordinary CLI session is merely open. `_is_cli_process(text)` recognises the CLI by precise
path **segments**, not substrings (a bare substring test would excuse a desktop app installed
under an unlucky parent directory, e.g. a user account literally named `claude-code`): the
versioned CLI home `...\appdata\roaming\claude\claude-code\<ver>\claude.exe`, an npm-style
`...\@anthropic-ai\claude-code\...` install, and the `.local/bin/claude[.exe]` PATH shim —
including a POSIX shim invoked with trailing arguments, since `ps -A -o args=` reports the
whole command line and a shimmed invocation with arguments never satisfies an ends-with check;
the shim marker is therefore also checked as a contains-match followed by a space. Everything
else claude-named — the MSIX desktop, a non-MSIX desktop install, or a bare image name the
fallback lister couldn't resolve to a path at all — counts as the desktop app: **name-only
resolution is a fail-closed fallback, not an exemption.** A bare `"claude"` argv0 with no path
segment to test is deliberately left unclassified, and therefore still counts, on the same
logic: with nothing to safely exclude, ambiguity resolves to "desktop."

This narrowing has a cost: at the old, cruder name-only base, *any* claude-named process —
including an ordinary open CLI session — blocked `move`/`undo`/`recover` too, so a transcript the
CLI was actively appending to could never be relocated out from under it by accident; now a CLI
session trips no guard at all, even though the CLI is exactly what's writing to the very
transcript `move` deletes from its source location. What's left is layered, not gone:
`plan_move`'s `MTIME_GUARD_SECONDS` heuristic (600 seconds, bypassable with `--force`) is now the
*primary* defense against relocating a transcript an open CLI session may still be writing,
backed by `execute_op`'s last-instant sha256 re-verification of the source immediately before it
deletes anything — which only catches a CLI write landing in that narrow window after the fact
(aborting the op, keeping both copies), not by preventing it.

### The account x org cross-pair, and why an empty store is ambiguous (2026-08-08)

Sessions are filed per **`<accountUuid>/<organizationUuid>` pair**, not per account,
which is why a machine with two accounts can present more than two candidate stores. On
the machine measured, all four combinations of two accounts and two orgs existed on disk,
and the split was clean:

| store | rows |
|---|---|
| `aaaa1111/cccc3333` — account with **its own** org | 266 |
| `bbbb2222/dddd4444` — account with **its own** org | 315 |
| `aaaa1111/dddd4444` — **cross pair** | 0 |
| `bbbb2222/cccc3333` — **cross pair** | 0 |

Only the pairs joining an account to its own org ever held sessions. One cross pair was
watched being created two minutes after a sync, containing exactly one file
(`scheduled-tasks.json`) and no listing rows — so these appear to be scaffolding the app
writes around account switches. *Appears to be*: the trigger was not isolated, and nothing
documents the layout.

This matters because **a zero-row store is genuinely ambiguous**. It is either scaffolding
or a legitimately fresh second account that has not been used yet, and the row count alone
cannot separate them — which is the one case where the count added in the 2026-08-05
amendment is no help. The cross-pair test can: `resolve_sync_endpoints` has already
resolved the signed-in account when it raises "more than one other account store", so it
passes that account's org to `_candidate_listing`, which tags any candidate sharing it
`[shares your signed-in org]`.

Kept as evidence, never a filter, for the same reason the row count is: the pairing
behaviour is an observation about an undocumented layout, not a rule the app has promised
to keep, and a cross-pair store becomes a real destination the moment its account/org pair
is signed in to. The tag is omitted entirely where no live account has been resolved (the
"stores found" listing, and `--live`'s own refusals) — there is no signed-in org to compare
against there, and implying one would be worse than saying nothing.

### The Chrome native host, and why it still counts (measured 2026-08-05)

*Superseded location, 2026-10-17: Desktop 2.42304 moved the helper to a versioned
`Claude-NativeHost` folder, and Edge as well as Chrome can launch it. See "Amendment
2026-10-17" below. The text here records the August layout as measured.*

The desktop app ships a helper for its Chrome extension —
`...\Packages\Claude_<pkgid>\LocalCache\Roaming\Claude\ChromeNativeHost\chrome-native-host.exe`
— which Chrome launches as a native-messaging host. It lives under the desktop's own package
directory, so `"claude" in text` matches it and `_is_cli_process` does not excuse it (none of
the CLI markers appear in that path). It therefore counts as the desktop app, and every
mutation refuses while it is alive. That it can outlive the desktop app, making "close the
desktop app" insufficient, is the field observation this section exists to re-examine — it
comes from the earlier episode written up in `the-session-that-synced-itself.md`, not from the
measurement below, which never ran with the app closed.

Whether that cost is *necessary* was measured on **2026-08-05**. The verdict is
**inconclusive, so the guard stays.**

Measured against: helper `chrome-native-host.exe` version 0.1.0, 1,018,704 bytes, sha256
`744187C7990FB3317CAD8345AE9EF17E7A0C1E2CCE9CF0A2C777B7F3D3D44D8A`; desktop package
`Claude_1.25927.0.0_x64__pzs8sxrjxfjjc`; Chrome
150.0.7871.189, extension `fcoeoabgfenejglbffodgkkbkcdhcgfn`; Windows 10.0.26200. The ruling is
bound to those builds: a helper update invalidates it without changing the executable path, so
re-measure rather than assume it carries forward.

What was gathered, all of it pointing the same way but none of it sufficient:

- **The binary contains no store path.** Extracting every ASCII and UTF-16 string from the
  image yields no occurrence of `claude-code-sessions`, `sessions`, `Roaming`, `AppData`,
  `projects`, or `jsonl`. Its only path-shaped strings are the components of its own log
  location (`LOCALAPPDATA` / `Claude` / `Logs`), the pipe-name prefix
  `claude-mcp-browser-bridge-`, and `CLAUDE_LOG_LEVEL`. This bounds *literals in this image*
  and nothing more: a path can be composed at runtime from known-folder APIs, read from
  configuration, or — the case that matters most for a message bridge — arrive in an IPC
  payload. Absence of the literal is consistent with the helper never touching the store and
  equally consistent with a store path it is *told*.
- **Of the handles that resolved at 148 sampled instants over 304 s, none was under either
  store root — but one handle per sample never resolved at all.** Enumerating the handle table
  via `NtQuerySystemInformation` (`SystemExtendedHandleInformation`), filtered to File-type
  handles and resolved with `NtQueryObject`, the helper resolved to six distinct named objects:
  its own log (`%LOCALAPPDATA%\Claude\Logs\chrome-native-host.log`, granted `0x00120194` —
  **append**, not overwrite), its own `ChromeNativeHost` directory (`0x00100020` — SYNCHRONIZE
  plus TRAVERSE, no write bit), `\Device\CNG`, `\Device\ConDrv`, the Chrome native-messaging
  *out* pipe, and the `claude-mcp-browser-bridge-<user>` pipe. Two limits are load-bearing and
  must be read with the list, not after it: this is the set held *at the sampled instants*, not
  the set it ever opens; and the one handle that timed out in **all 148 samples** is
  unidentified, so the resolved list is not a complete inventory.
- **Its own log records a message bridge**: create named pipe → main message loop → socket
  server, and on `Chrome disconnected (EOF received)` → shut down. That is what the helper
  *logs*, which is not the same as what it does; an unlogged write path is not excluded. It
  does cover the Chrome-exit leg to the extent anything here does: nothing appears around
  shutdown but the shutdown itself.

Why that is *not* enough, which is the whole point of recording this:

- **Handle sampling has no useful power against how this store is written.** Negative control:
  449 samples over 120 s across all 26 claude-named processes caught **zero** processes holding
  a handle under the store — during a window in which a directory watcher recorded **28** store
  write events (≈4 atomic write transactions; a session row is written `.tmp` → write → rename,
  which emits about seven events each). The writer is *inferred* to be the desktop app from the
  event pattern and location (inside a live session directory under the account store); it was
  not attributed by measurement, and the scan covered only claude-named processes. If that
  inference holds, the writer was inside the scanned set and the method still scored zero.
  A **resolver positive control** rules out one specific alternative — that the sampler cannot
  see store paths at all: a child process holding a file open under the real store root was
  detected on the first scan, at `0x0012019F`, exercising enumeration, cross-process
  `DuplicateHandle`, `NtQueryObject` naming, path normalisation and root-matching. Note what
  that control does *not* cover: it used a continuously held ordinary file, so it says nothing
  about resolving a short-lived handle (which is the failure being explained) or a blocking
  pipe handle (see the unresolved handle below). (Order of magnitude, on the unmeasured
  assumption that each transaction holds its handle a few milliseconds: a duty cycle under
  0.05% against a 3.7 Hz sampler puts the expected number of catches well below one, so
  observing zero is the expected outcome even for a process that certainly writes. The
  empirical 0-for-449 is the claim; the arithmetic only offers a mechanism for it.)
  So the sampling licenses very little: it says essentially nothing about brief atomic access,
  which is exactly the pattern in question, and — because of the unresolved handle below — it
  cannot even fully exclude a *sustained* store handle.
- **One handle per sample never resolved, and this is not a footnote.** `NtQueryObject` timed
  out on exactly one of the helper's handles in all 148 samples — a *persistent* handle of
  unknown identity. Its identity is *inferred* (Chrome creates both an `in` and an `out`
  native-messaging pipe, and only `out` resolved) but not measured, and the inference is only
  plausible, not established. Until that handle is identified, the otherwise tempting
  conclusion "the helper holds no sustained handle under the store" is **not established** —
  the one handle the method could never see is precisely a persistent one.
- **The decisive lifecycle leg was not captured.** The condition the guard's cost is actually
  paid under — desktop app closed, helper surviving — went unmeasured because the measuring
  session was itself hosted by the desktop app (`pwsh` → CLI `claude.exe` → `Claude.exe`), so
  closing the app terminates the measurement. That is a limitation of *this* setup, not of the
  problem: a detached capture (Task Scheduler, or a shell started outside the app's process
  tree) can hold the app-closed window open and is the obvious way to get this leg.
- **The workload was not characterised.** Elapsed time is not exposure: the run does not record
  how many native-messaging round trips, extension actions, reconnects, or helper restarts
  occurred, so "304 s of observation" does not establish that the interesting code paths ran.
- **The classic `%APPDATA%\Claude` path does not exist on the measured machine**, so its
  behaviour is untested rather than cleared.
- **No elevation was available**, so the instrument that would answer this directly — a
  Procmon or ETW kernel-file trace, which is continuous *and* attributed — could not be run.
  `logman` on `Microsoft-Windows-Kernel-File` returns access-denied unelevated, and Procmon
  needs to load a driver.

**What would license the exclusion.** "Never writes" is not empirically provable, and a rule
that demands it is a permanent veto dressed as a standard — so the bar is stated as a bounded
one.

*Capture.* An **elevated** Procmon or ETW kernel-file capture scoped to *the store roots, all
processes* — not filtered to `chrome-native-host.exe`, which is the mistake that would make the
trace unable to contain its own control — retaining create/write/rename/disposition and
set-information operations plus mapped-section writes, which a short operation allowlist
misses. Run it from a session outside the desktop app's process tree (Task Scheduler or a
detached shell), and record the dropped-event counters: a lossy trace is not a quiet one.

*Workload.* Enumerate it rather than measuring wall-clock. At minimum, and repeated across
trials: helper start, a stated number of native-messaging round trips of each message type the
extension sends, a disconnect/reconnect cycle, the app-closed/helper-surviving window held open
deliberately, and Chrome exit. The run must also record what it exercised, so a later reader
can tell coverage from elapsed time.

*Acceptance.* The endpoint is **no non-control mutation of either store root by any process**
during the helper-only intervals — not "the helper issued no write." A message bridge can cause
a write it does not perform, and mapped-page flushes are attributed to the memory manager
rather than the requesting process, so a helper-scoped endpoint would pass while the store
changed. The same capture must show it *does* record the desktop app's own store writes;
without that control an empty result means nothing.

*If it passes*, the exclusion must be bound to the binary that was measured, not to a
directory name: match the parsed image path — exact basename `chrome-native-host.exe`, anchored
under a `\packages\claude_*\localcache\roaming\claude\chromenativehost\` segment chain — and
fail closed on anything else. A bare `\chromenativehost\` segment test is not enough: it would
excuse any binary under any directory of that name, and because `_is_cli_process` sees an
unparsed lister entry, it could also be satisfied by a command-line *argument*. Note too that
the helper auto-updates in place, so a passing trace binds only the measured build; without a
version or hash check the exclusion silently carries over to code nobody measured, which is the
fail-open this whole section exists to avoid.

The non-elevated harness these results came from is committed at
`tools/native-host-measurement/` (handle sampler, negative control, resolver positive control).
It cannot settle the question — that is the point of this section — but it is the cross-check
and the store-side control for whoever runs the elevated capture, and its README records the
two mistakes that make this measurement report a clean result by construction.

Until then `chrome-native-host.exe` counts. Two tests in
`tests/test_plan_move.py::TestClaudeRunningNarrowing` pin that — `test_chrome_native_host_still_counts`
(the helper's path is returned by `claude_running`) and `test_chrome_native_host_is_not_read_as_the_cli`
(it does not drift into `_is_cli_process`, whose markers it sits one path segment away from).
They pin the *classification*, which is what a future refactor would break; they assert nothing
about the helper's I/O, which is the open question above and not something a unit test can settle.

#### RULING 8 (2026-08-19) — `sync --update`, the only route that overwrites

Every other thing `sync` does only ADDS. A row already present at the destination was skipped
as `present`, and re-running never refreshed it. `--update` refreshes such rows. It is opt-in
per run, never implied, and in the window it is an unticked checkbox that is never remembered.

This section is consolidated from six same-day amendments — four rounds of adversarial peer
review over the code, then two rounds of the author actually using it. That order matters, and
the record of what each found is kept at the end under *How the rules above were arrived at*,
because the pattern is more instructive than any individual fix.

##### What a listing row actually is

Not a snapshot. A **pointer**, wrapped in metadata.

- The row's **filename** is the *app's* session id. It survives a session being resumed.
- The **`cliSessionId` inside it** names the transcript, and changes on **every new CLI run**.

So one sidebar entry accumulates several transcripts over its life, and each account's row
records whichever one *that account* last saw. Two accounts holding the same filename can
therefore point at **entirely different conversations**. Opening the entry under the stale
account does not show a truncated version of the other — it shows a different conversation,
complete and current as of its own last use, which to a user is indistinguishable from "my
history is behind". Measured on the reporting machine: **15 of 333 shared rows**, with the
newer conversation on one side for 9 and the other for 6.

Two things follow, and they are the whole reason this ruling is not cosmetic:

- **Refreshing a row can restore access to a newer conversation** — that is the strongest
  argument for the feature.
- **Refreshing in the wrong direction can take that access away**, and if no other account
  points at the displaced conversation it becomes reachable from nowhere. The transcript
  survives on disk, findable by nothing.

What *cannot* happen: a stale row hiding part of the conversation it points at. There is one
shared transcript per `cliSessionId` and a row carries no message data — no excerpt, no
offset, no cursor. Verified by opening a session under a row 36 days stale and getting its full
current conversation. Relatedly, **`completedTurns` is not a message count** and must never be
presented as one: rows reading 17/33/33 sat in front of a transcript holding 472 typed
messages, and 33/37 in front of 52. It tracks something internal, most likely turns since a
context compaction.

##### What makes overwriting safe enough to offer

The plan records the destination's CURRENT bytes as a `pre_b64` pre-image alongside the
post-image it intends to write, and every later step is a comparison against it:

| at write time the destination holds | what happens |
|---|---|
| exactly the pre-image | overwrite — this is the refresh |
| the post-image already | nothing; recorded as written |
| anything else | **refused** — it changed since planning, so overwriting would discard that |
| nothing (deleted) | **refused** — writing would resurrect a session that account removed |

**Byte equality, deliberately.** A refresh whose post-image equals the pre-image is dropped as
`unchanged`. The comparison is raw bytes, not semantics: "these two rows say the same thing" is
not a judgement this tool will make immediately before overwriting one.

**Undo restores rather than deletes.** For an added row the reversal is deletion; for a
refreshed one it is putting the measured pre-image back byte for byte via `atomic_write`, so an
interrupted reversal cannot leave a row that is neither version. Reversal only proceeds while
the row still holds exactly what the op wrote — once that account has touched it, undo refuses.

**The undo window is bounded.** `pre_b64` lives only in the journalled manifest and
`rotate_ops` keeps the ten most recent terminal ops, after which the pre-image is pruned and
the overwrite is unrecoverable. The overwrite block and the window's confirmation both say so.

##### Which rows a refresh will and will not send

Three qualifiers, because "refresh everything in one direction" is almost never what anyone
means once more than two accounts exist.

- **`--newer-only`** (window: *"only where mine is newer"*, **ticked by default** — the one
  default here that is not "do nothing", safe because it can only ever send *fewer* rows).
  Sends only rows this account's copy is **strictly** newer than. Holds back three distinct
  cases under their own tallies, each named row by row rather than counted:
  `held_older` (their copy is newer), `held_same` (same moment — a row can differ while its
  timestamp does not, because `model`, `permissionMode` and the MCP fields drift without any
  activity), and `held_unknown` (either side's timestamp unreadable — *only what is newer* is a
  claim, and it cannot be made).
- **`--allow-orphan`** (window: *"allow hiding a conversation"*, **off by default**). A refresh
  that changes which conversation the row opens is detected (`swaps_conversation`, tallied
  under `swapping`) and reported on its own line — *"opens a DIFFERENT conversation
  afterwards"* — never as a title refresh. `_other_pointers` then scans every store except the
  destination to ask whether anything else still points at the displaced conversation. If
  nothing does, the refresh is **held back and named** under `held_orphan` unless this flag is
  given. This is the mirror of `--include-deleted`: that one resurrects something deliberately
  removed, this one removes access to something never deleted. A store that cannot be *read*
  yields `"unknown"`, never "nothing there".
- **`--only`**, the ordinary title filter, which is usually the right answer for "I just want
  this one session".

**And every hold carries a measurement, because naming one is not enough.** Shipped without it,
the orphan guard held back **7 of 7** candidate rows on a real three-account machine and wrote
nothing — since once a session is worked from two accounts, *every* propagation is an orphaning
swap by that definition. A guard that fires on the normal case is a wall, and it was reported as
the tool being broken. `_displaced_overlap` compares the conversation a refresh would displace
against the one it brings in, and the plan prints the answer per row. The same 7 rows then read
5%, 35%, 72%, 87%, 97%, 99% and one unmeasurable — a decision, where seven identical warnings
were not.

Four properties of that number, each of which was got wrong first:

- **Prose only, and measured rather than assumed.** Tool calls and results dominate a transcript
  by count and are near-identical boilerplate across unrelated sessions. Counting every block
  put two real pairs at 74% and 94% "already in the incoming conversation" where the prose those
  pairs share is **5% and 36%** — numbers that would have talked a user into the overwrite the
  prose says to avoid. Timestamps are excluded for the same reason: keyed on them, a
  conversation "diverged" from its own continuation at message 8 of 738.
- **It never claims nothing is lost.** The full-overlap line says *every prose turn appears in
  the incoming conversation (text only, first 400 characters compared — images, attachments and
  tool output were NOT)*. It compares truncated prose as an unordered set, so it cannot see
  attachments, cannot distinguish a long turn that diverges after 400 characters, and cannot
  tell a clean continuation from one interleaved into an unrelated conversation. The earlier
  wording claimed preservation, and it sat on the line that tells the user how to override the
  hold — every engine on the review panel raised it independently.
- **Percentages floor.** `int(round(…))` printed 99.9% as 100% and gave it the full-overlap line.
  Live on the reporting machine: the session the user cared most about read "nothing is lost"
  and is 99%.
- **Unmeasurable is never a number.** A missing, oversized, unreadable or ambiguous transcript,
  or too few prose turns to support a percentage, all report NOT MEASURED. *Ambiguous* is real:
  `find_transcripts` returns every project directory holding a session id, and a git worktree
  put one session in two — it had been reporting 98% against whichever the directory walk
  reached first.

Known and deferred: the comparison is set membership, not sequence, so repeated boilerplate
prose inflates it and an interleaved conversation can still read high; planning cost is bounded
per row but not in total, and the window re-measures on every replan; and the several causes of
NOT MEASURED print identically.

Held back rather than refused outright: a refusal would block every other row in the run, and
the point of naming them is that the user should not have to adjudicate a wall of titles.

**Visibility.** Overwrites are listed separately and FIRST under `!! OVERWRITING n row(s)`, the
same unmissable treatment `--include-deleted` gets. Each row carries its direction (*moves it
BACKWARDS* / *could not be determined*) and, where relevant, its swap line. The block names
what the destination's row actually **loses** — computed by diffing its own pre-image against
the post-image, not from the transform's source-side lists — and the window shows all of it,
plus a second confirmation naming the counts.

##### Hard-won invariants

Each of these was a real defect. They are stated as rules because each one generalises.

**Evidence and ownership**

- **`written` is an intention journalled *after* the write, so it is not proof.** A hard kill
  between `atomic_write` returning and the manifest save leaves a row holding this op's bytes
  while the manifest denies it. `_sync_delete_targets` consults the disk too: state `match`
  means the file holds exactly this op's post-image.
- **Matching bytes are not proof of authorship.** `transform_row` is deterministic, so a later
  op planned over unchanged source mints an identical post-image. Ownership needs an **order**:
  `_sync_paths_claimed_elsewhere` counts only ops *later* than this one (by `_op_sort_key`), so
  the latest claimant owns the row. A symmetric check is not a fix — it deadlocks both parties.
- **A reversed op withdraws its claim.** `undone`/`rolled_back` ops are excluded, or their
  leftover flags would block every later legitimate reversal of the same row, permanently.
- **Evidence must outlive what it protects.** `rotate_ops` holds back a terminal sync op whose
  written rows share a destination path with any nonterminal one — a stalled op is never
  pruned, so its claimant must not age out from under it.

**Fail closed, everywhere, on the same rule**

- **"Could not look" is never "nothing there"** — an unreadable destination row, an unreadable
  store during orphan detection, an unreadable timestamp on either side of the newer-check.
- **A corrupt manifest refuses; it does not crash.** `_sync_pre_image` fails on a missing key
  and on an explicit null while keeping `""` valid as a genuine zero-byte pre-image; `post_b64`
  and the journal byte budget both type-check rather than raising `AttributeError`/`TypeError`
  out of code whose contract is "never raises".
- **A damaged flag reads the safe way.** `_row_is_refresh` accepts `is_update` **or** a present
  `pre_b64`, because a refresh that lost its flag would otherwise be reversed by *deletion*.
- **A fix that overcorrects is still a defect.** `r.get("pre_b64") or ""` fixed the zero-byte
  case and simultaneously turned a *missing* key into an empty pre-image, so undo would have
  written a zero-byte file over the row.

**State machines must agree**

- **Every state one function can produce, another must handle.** An absent *pending refresh* is
  a third blocking state (`deleted`): `_sync_write_rows` refuses to resurrect it on every
  re-entry, so offering `forward` was a dead end. For an *add*, absent is ordinary resumption.
- **An interrupted refresh stays resumable** — the `pristine` state (row still holds the
  pre-image) exists so an untouched pending row is not mistaken for drift.
- **Reversal attempts both halves.** `_sync_reverse_all` unlinks and restores in one pass and
  reports every failure together; sequentially, one locked added row left every refreshed row
  stranded in its overwritten state.

**Say what is actually true**

- **The pre-image is the other account's row verbatim**, including the connector config the
  transform strips precisely so it is not carried. `_public_manifest` keeps it out of `--json`;
  it belongs only in the private journal, which is what undo restores from.
- **Never describe an action as something milder than it is.** A refresh replaces the *whole*
  row, not "the stale title/last-activity snapshot"; `--update` never claimed to compare
  recency yet the window said "the newer copy"; the conflict refusal advised deleting the row
  in the app, which for a refresh destroys the session rather than reversing anything.
- **An assertion asked too often stops being read.** The `--live` answer now holds for the
  window session, is cleared on apply, and is changeable from a button beside Refresh —
  visible state rather than state re-demanded on every replan.

##### When the app orphans a conversation, and nothing here can stop it (2026-08-21)

The first incident where **no guard in this tool was applicable**, and the most instructive so
far.

A sidebar entry was resumed while the desktop was signed into a *different* account. That
continued the older branch, and the app then wrote that transcript id into whichever store was
active — including the account that had been holding the newer one. A 32 MB conversation was
left on disk with no account's sidebar pointing at it. Reconstructed from mtimes: the row was
written at 21:27:49, after every operation in the journal and two days after the last one that
touched that store as a destination. **The mutation never went through this tool**, so
`--newer-only`, the orphan hold and the running-app guard were all irrelevant — and by the time
the tool next looked, the app's pointer genuinely *was* the newer one, so `--newer-only`
correctly propagated it onward.

Two consequences:

- **`doctor` already knew, and buried it.** `unlisted_transcripts` has always been computed; it
  printed one identical line per orphan — **155 of them inside a 569-line report**. The
  information was there and unusable. It is now ranked newest-first with size and age, capped at
  the top few, with the total named and the full list still in `--json`; the report dropped to
  273 lines. Most unreferenced transcripts are ordinary — a CLI-started session never had a row,
  and deleting a session leaves its transcript behind — so the ranking *is* the feature: a large
  one from this afternoon was previously indistinguishable from a small one from last month.
- **`repoint` puts a pointer back.** One row, one field (`cliSessionId`), journalled with a
  pre-image so `undo` reverses it, under the same drift rule as everything else here: reversal
  proceeds only while the row still holds exactly what the repoint wrote, because the app
  rewrites these rows whenever it opens the session.

**`repoint` defaults to the LIVE account's store, and that is a deliberate departure from
`sync`.** Sync refuses to write the signed-in account, reasoning that the account you are using
is the one you least want a background tool rearranging. Repoint exists to fix the sidebar you
are looking at, so that refusal would rule out its only real use. What protects it instead is
the running-app guard: the app must be closed, which is what makes "the live account's store" a
safe target rather than a live one.

**One judgement worth recording.** `--store` is *not* refused when several stores match, unlike
every other selector here. An account owns one store per org, so naming it by email necessarily
matches all of them — but only one can hold the row being repointed and the rest are the empty
cross-pair scaffolding. The *row* settles it: every candidate store is searched, and the
refusal fires only if the row itself is ambiguous. That is a question about the thing being
changed rather than about directory naming, and it is the one a user can actually answer.

**Two gaps in that matching, both shipped, both fixed 2026-08-23.** Found while building
`new-row`, which reuses `_repoint_store`; neither was caused by that work. The help text and
the README had advertised both inputs all along, so this was documented behaviour that did not
run.

- *The live account's own email matched nothing.* `account_email` answers for a **dormant**
  account — the per-account sandbox config, then the memo — and the signed-in account's email
  is in neither: it lives in `~/.claude.json`'s `oauthAccount`, which only `live_account`
  reads. So the one account `repoint` *defaults* to was the one account whose own email named
  no store, and the plan labelled the store you are looking at with eight hex characters.
  `_email_of` now asks `live_account` first. Deliberately *there* rather than inside
  `account_email`: that function's second return value is labelled `'sandbox'` or
  `'memo:<date>'` and a freshly observed email is neither, so widening it would have forced
  either a new provenance value on every caller or a mislabelled one. `_email_of` returns a
  bare string, so there is no provenance to misreport. It was invisible on a machine that
  already holds an email memo for every account, which is how it survived.

- *A path with this platform's own separators matched nothing.* `os.path.normcase` on Windows
  lower-cases **and** rewrites `/` as `\`; the candidate was then flipped back to `/`, while
  the string the user typed was only `.lower()`ed — so the backslashes in a path copied out of
  this tool's own "which store did you mean" listing survived and could never match. On POSIX
  `normcase` is the identity function, so there an upper-case component failed the same way.
  `_path_match_key` now normalizes both sides identically. It lower-cases on every platform,
  which is more lenient than a case-sensitive filesystem strictly requires — deliberate, since
  this is a substring match on a fragment a person typed and matching several stores is
  already the normal outcome here.

`tools/check_repoint.py` exercised no `--store` matching whatsoever before this, which is how
both reached a release. It now covers the live account's email, a dormant account's email, an
account id, an org id, and a native-separator path.

##### Accepted residuals

- **Read-then-write.** `_sync_write_rows` reads, compares, then writes; `atomic_write` is an
  indivisible replacement, not a compare-and-swap. The honest claim is "refuses on drift
  observed at check time". Closing it needs a primitive this layout does not offer. See
  *The accepted TOCTOU residual*.
- **Sibling hard-kill.** An op that writes an identical post-image and is killed *before*
  journalling `written` leaves no claim, so a stalled op can still reverse its write. Closing
  it needs a write-intent record before every `atomic_write` — the per-row manifest rewrite
  `SYNC_JOURNAL_BYTE_BUDGET` exists to avoid.
- **Ownership orders by creation, not by write.** An op created first but rolled *forward*
  after a later op completed is treated as the earlier claimant. Still exactly one owner.

##### How the rules above were arrived at

Four rounds of a four-engine panel (Codex, Gemini, DeepSeek, Kimi) over the diff, then the
author using the result on a real three-account machine.

**Each review round found a defect in the previous round's fix** — the disk-evidence rule broke
cross-op ownership, the ownership check was dead code in `undo`, the fix for that was symmetric
and deadlocked. That is the value of re-running a panel rather than stopping at "no blockers",
and the shape to expect: fixes that are correct in their own frame and wrong one frame out.

**But no review round questioned the premise**, because every engine saw only the diff and a
design doc that shared the mistake. The belief that a stale row cost titles and sort order —
not access to conversations — survived all four rounds intact. It died the first time a user
said "that doesn't sound cosmetic to me", twice, after being told it was. Two consequences
worth carrying forward: **run the cheap empirical test before building the thing** (this one
took thirty seconds), and when a user reports something the model of the system says is
impossible, the model is the more likely thing to be wrong.

**Two defects were found only by running it on real data**, never by review: `--newer-only`
originally meant "not older", which let 248 timestamp-identical rows through; and the pointer
swap, which turned 18 apparently-routine refreshes into 6 conversation swaps of which 5 would
have hidden a conversation entirely.

#### RULING 7 (2026-08-13) — opt-in signature trust, because the hash binding decays

RULING 6's exclusion binds to the measured build's sha256. That is the strictest possible
binding and it is also, in practice, a slowly self-cancelling one: **the helper auto-updates
in place, and three distinct builds were observed in eight days** — `744187C7…` (2026-08-04),
`711AD7E7…` (08-06), `D0374C9B…` (08-11). Each update correctly fails closed, so the helper
starts counting again and "close Chrome too" returns until someone re-runs the ~12-minute
measurement ceremony.

That trade is bad enough to be self-defeating: roughly 2–4 days of relief per ceremony means
the feature is rationally abandoned, leaving the original friction *plus* dead code. This was
observed directly — the exclusion lapsed on 08-11 and the next real sync attempt, on 08-13,
was refused with the user believing the feature still worked.

**The alternative binding.** The helper is Authenticode-signed: `CN="Anthropic, PBC"`, EV
certificate, DigiCert-issued, and Windows reports the signature `Valid`. Binding to *anchored
package path + valid signature by that publisher* survives routine updates while still failing
closed on an unsigned binary, a tampered one (the signature breaks), one signed by anyone else,
or one outside the package chain.

**What it trades.** Trust moves from "these exact measured bytes" to "this publisher". The
residual is not forgery — it is that a **future** Anthropic build of the helper begins touching
the store and the signature excuses it, because nobody re-measured. Small, given what the
measurement found (a message bridge whose image contains no store path, held no store handle,
and produced no store mutation across a 4m23s window under live extension traffic), but real.

**Why it is OPT-IN and not the default.** This is a published tool and its other users have
not examined that measurement. Loosening the shipped default would hand them a weaker guard
silently, on evidence they never saw. The default therefore remains RULING 6. The opt-in is the
**existence of a marker file**, `~/.claude-code-journal/trust-signed-helper` — nothing to parse,
nothing to typo, trivially auditable, and deleting it is a complete revocation. The refusal
names the exact path to create when the helper is counted for a hash mismatch.

**Boundaries, all pinned by `tools/check_signed_helper_optin.py`:** a measured build is excluded
regardless of the opt-in; a changed build counts while opted out; changed + signed + opted in is
excluded; changed + **unsigned** + opted in still counts; **`unreadable` never qualifies** even
signed and opted in (a binary whose bytes cannot be read cannot be shown to be the file that was
signature-checked); and the desktop app itself is never excused by any of it.

Review closed two holes where the implementation was weaker than the paragraphs above, and both
are worth recording because both made a *documented* guarantee false rather than merely
imprecise. The publisher test was a **substring** match, so a valid certificate for
`CN="Not Anthropic, PBC"` — or any DN carrying that text in some other field — satisfied it;
it now parses the DN and requires CN *and* O to equal the publisher exactly. And the helper was
recognised by **path fragments** (`\packages\claude_` plus `\localcache\roaming\claude\
chromenativehost\` appearing anywhere), which any fabricated path satisfies, so with the opt-in
enabled another Anthropic-signed binary copied to `C:\tmp\packages\claude_x\localcache\roaming\
claude\chromenativehost\chrome-native-host.exe` would have been excused. The helper's location
is now **derived** from the discovered store roots and matched exactly.

**One limit that is not fixed, and applies to RULING 6 equally.** Both rulings check the file
*at the path*, not the image the running process actually loaded. An attacker who can write to
the app's own package directory could therefore leave a tampered helper running while placing a
trusted binary at its path. That is accepted rather than solved: an adversary with write access
to the desktop app's install directory can replace the desktop app itself, which no guard here
could survive, so it sits outside this tool's threat model. The claim to make is "a tampered
binary *at that path* still counts", not "a tampered running process is always detected".

**Verification is deliberately un-cached and tries two interpreters.** Caching on
`(path, size, mtime)` is precisely the fail-open review caught in the hash version. And measured
2026-08-13: `powershell` (5.1) on this machine fails with *"the module could not be loaded"* for
`Microsoft.PowerShell.Security`, while `pwsh` 7 answers correctly — so trying only the
always-present interpreter would report every helper unsigned and make the opt-in silently
useless. Both are tried; if neither can verify, that is "couldn't look", the helper keeps
counting, and the opt-in simply does not take effect.

#### Amendment 2026-08-07 — the store-side watch replaces the elevated trace

The *capture* clause above is superseded; the **acceptance endpoint is unchanged**. The
instrument moved from an elevated kernel-file trace to two unprivileged store-side
instruments, for one practical reason and one principled one.

*Practical.* The elevated capture was built and run twice. Both runs died before the decisive
window, and the second showed why the design was unusable: **9.1 GB/min** (52.7 GB in 5.8
minutes), putting a full ceremony near 180 GB and a CSV export in the hours. Part of that was
an implementation error — the protocol says *scoped to the store roots*, which is a **path**
filter, and the first implementation read the neighbouring "not filtered to
`chrome-native-host.exe`" clause as a blanket prohibition on filtering and captured
machine-wide. But even filtered correctly, ProcMon needs elevation, and its filters cannot be
set from a command line: they arrive through an undocumented binary `.pmc` that must be
authored in the GUI. That is a lot of ceremony to answer a question about one directory.

*Principled.* The endpoint is a statement about the **store**, not about the helper — "no
non-control mutation of either store root by any process". Attribution is what a trace buys,
and attribution is not what a PASS needs. It is only needed on a FAIL, to say *who*.

**Two instruments, because one is measurably not enough:**

- **A recursive `ReadDirectoryChangesW` watch** over each store root. Continuous, not sampled
  — which is precisely the property handle sampling lacked, so millisecond-scale
  `.tmp` → rename transactions cannot slip between observations. It time-localizes events to
  the decisive window.
- **A content snapshot (sha256 per file) taken at both window boundaries**, diffed. This
  catches net change by *any* mechanism.

**The measurement that forced the second instrument, and the reason a watcher-only design
would have been a false-PASS generator.** A pure mapped-section write — `mmap`, write through
the mapping, `flush`, close, to an existing file within its existing size — produces **no
`ReadDirectoryChangesW` notification at all**. Not delayed: absent at 2, 5, 10, 20, 35 and 60
seconds, with `FILE_NOTIFY_CHANGE_SIZE | LAST_WRITE | ATTRIBUTES | CREATION | SECURITY` in the
filter (measured 2026-08-07). That is exactly the mutation class the original protocol singled
out, and a watcher alone would have reported the window quiet. The snapshot diff catches it
(verified in the same session: the diff reports the file `changed`). Neither instrument is
redundant; the watcher says *when*, the snapshot says *whether*.

**Five controls, each able to void a run** — a quiet result means nothing without all of them:

| Control | What its failure would otherwise hide |
|---|---|
| Canary heartbeat (write-rename-delete under each root every 20 s) | A watcher that was never delivering |
| Overflow detection (`ReadDirectoryChangesW` signals overflow by returning success with **zero bytes**, discarding the whole buffer) | A dropped interval, indistinguishable from a quiet one |
| Phase-1 real traffic (genuine non-canary store writes must be observed while the app is open) | A watcher that sees only its own canary |
| Mapped-write control (a real mapped-section write must be caught by the snapshot **pipeline**) | The one class the watcher provably misses |
| Process-tree guard (the runner walks its own ancestry and refuses to start inside the app's tree) | The failure that lost the 2026-08-05 decisive leg — closing the app kills the measurement |

Every control is per-root: one working watcher must not be able to vouch for a root whose own
watcher delivered nothing. An overflow **anywhere in the run** voids it, not merely one inside
the window — overflow timestamps record when the discard was *discovered*, and the discarded
buffer may hold older events, so a near-boundary overflow cannot be safely excluded.

**Residuals, recorded rather than hidden.**

- **A PASS means "no mutation observed, and no net change between the window boundaries" — not
  literally "no mutation."** The distinction is load-bearing here rather than pedantic: since a
  mapped write produces no watcher event at all, a mapped write followed by restoration of the
  original bytes is a *concrete* evasion route, not the merely-theoretical one an earlier draft
  of this section called absurd. Nothing in the threat model motivates it, but the claim is
  stated at the strength the instruments actually support.
- **Watcher timestamps are delivery times, not mutation times.** Every event in one
  `ReadDirectoryChangesW` buffer is stamped when the call returns, so an event near a phase
  boundary can be filed on the wrong side of it.
- **The phase-1 control shows the watcher sees real store traffic — not that the desktop app
  caused it.** Any non-canary write satisfies it; the workload description is recorded as prose
  and not validated.
- **The watcher is unattributed**, so a FAIL says the store changed, not who changed it.
  Attribution then needs the elevated trace, retained at
  `tools/native-host-measurement/elevated/` as the diagnostic follow-up rather than the primary
  instrument.
- **The snapshot compares window boundaries**, so it cannot time-localize what it finds, and it
  fingerprints file contents only — not directory state, ACLs, or alternate streams.

The runner is `tools/native-host-measurement/watch_ceremony.py`; its verdict function is a pure
function over a saved report (`--verdict report.json`), exercised by
`check_watch_ceremony.py` beside it — PASS, both FAIL routes, every INCONCLUSIVE route, and a
live check that the snapshot pipeline catches a mapped write to a 9 MB file. (Named `check_`
rather than `test_` so `pytest` does not collect it: nothing under `tools/` runs in CI, and it
exits with a status code rather than asserting.)

**This amendment was independently reviewed before it was trusted, and the review found real
false-PASS routes** (Codex, 2026-08-07; the second engine failed its model check twice, so this
was single-engine and is weaker evidence than a two-engine pass). The material ones, all now
fixed and pinned by tests: the snapshot hashed only files ≤ 8 MB and recorded size alone above
that, so an in-place same-size mapped write to a large file changed *neither* instrument — a
deterministic false PASS in exactly the class the second instrument exists for; control traffic
was excluded by filename *substring*, carving out a namespace where a real store file could
masquerade as a canary; a window the runner itself flagged as contaminated (app relaunched,
helper exited early) still licensed a PASS because the contamination note was consulted only
when a boundary was missing; snapshot traversal and read errors were recorded as ordinary
manifest values, so a subtree unreadable at both boundaries cancelled out of the diff and read
as quiet; and the mapped-write control hashed one file directly instead of running the snapshot
pipeline, which is precisely why it passed while the pipeline was blind above 8 MB. That last
one is the lesson worth carrying: **a control that bypasses the instrument it certifies
certifies nothing.**

**First real run, 2026-08-07 — the alive-window question is answered; the shutdown leg is
not.** Over a 4 m 44 s helper-only window (desktop app confirmed gone, helper alive, two
extension round trips performed inside it), **both instruments were silent**: zero watcher
events under the store, an empty snapshot diff with zero traversal errors, and zero buffer
overflows. The controls held — 70 canary events inside the window, 42 genuine store writes
observed in phase 1, mapped-write control caught by the snapshot pipeline. On the alive
helper, this is the evidence the guard's cost was always waiting on.

It does **not** license the exclusion, because the enumerated workload's last step did not
happen: the helper was ended from Task Manager rather than allowed to close itself when Chrome
disconnected. `TerminateProcess` skips the helper's entire shutdown path — and that path, which
its own log describes as running on `Chrome disconnected (EOF received)`, is exactly the kind
of moment a process flushes state. So *"does the helper write while alive?"* now has an answer;
*"does it write while shutting down?"* does not, and a shutdown write is the more dangerous of
the two, since it could land at the instant the tool decides the helper is gone.

Two fixes came out of that run, both about not trusting narration. The phase-3 prompt said
"the helper must EXIT", which reasonably reads as an instruction to kill it; it now says to let
the helper close itself and warns explicitly against ending the task. And the runner no longer
takes the operator's word for how it ended: it opens a handle to the helper **before** the
window (an exit code is unreadable afterwards) and reads `GetExitCodeProcess`, treating a
non-zero code — or an unknown one — as an unmeasured shutdown leg that voids the run. Re-scored
under that rule, the 2026-08-07 run is **INCONCLUSIVE**, which is the honest label for it.

A third wart the run exposed: the mapped-control's own file was classified as *real* traffic
rather than control, so in principle our own control could have satisfied the "the watcher sees
genuine store writes" check — a control validating itself. It is now control traffic. (The run
was unaffected: 42 of its 43 phase-1 real events were genuine app writes.)

**Second run, 2026-08-07 — PASS, and the helper updated underneath it.** The re-run completed
the workload: a 4 m 23 s helper-only window with the app confirmed gone and two extension round
trips inside it, then Chrome closed normally and the helper **closed itself, exit code 0**, so
the shutdown path this leg exists for actually ran. Both instruments silent: zero watcher
events under the store, empty snapshot diff, zero traversal errors, zero overflows; controls
held (65 in-window canary events, 35 genuine phase-1 store writes, mapped-write control caught
by the pipeline). Under the protocol's terms this licenses an exclusion — for
`711AD7E7DEC73AA58187479F5F99B13480DF93AB1306BD171A61027D84FA81F1`.

That hash is **not** the one measured hours earlier the same day. The helper binary changed
from `744187C7…` (mtime 2026-08-04) to `711AD7E7…` (mtime 2026-08-06) *between* the two runs —
same byte length, different content — while its path stayed identical. The likely mechanism,
consistent with the observation but not itself verified: the update was staged earlier and
landed once the running helper exited, which is precisely what the first ceremony's
Task-Manager kill caused. Two consequences worth stating plainly:

- **The "helper unchanged across an app update" note recorded earlier that day is superseded.**
  It was true at that instant and wrong as a generalization: the helper does update in place,
  observed inside a single afternoon.
- **This is the case the hash binding exists for, and it validates it** — but it also means a
  hash-bound exclusion *lapses on every helper update*, restoring the guard (and the
  Chrome-exit friction) until someone re-measures. That is the correct fail-closed direction:
  an exclusion that survived an update would be trusting code nobody measured. The design
  consequence is that the exclusion must fall back to *counting* the helper on any hash
  mismatch, so the worst case is exactly today's behaviour and never worse.

**Standing caveat on strength of evidence.** This is **one** clean trial. The protocol's
workload clause asks for the enumerated actions "repeated across trials", and one PASS plus one
alive-window-only run is not that. It is enough to license the exclusion under the acceptance
rule as written; it is not enough to call the helper's behaviour characterised.

*The binding rules for a PASS are unchanged* — anchored package-path segment chain **and** the
measured binary's hash, failing closed on anything else. One observation strengthens the case
for hashing over versioning: on 2026-08-07 the desktop app had updated from 1.25927.0.0 to
**1.26832.0.0** while the helper binary was **byte-identical** (sha256 `744187C7…`, unchanged).
A version-bound exclusion would have expired for no reason; a hash-bound one correctly
survived. The converse — a helper update under a static app version — is the case the hash
binding exists to catch.

#### Amendment 2026-10-17 — the helper moved, and the derived location follows it

*Superseded derivation, 2026-10-19: the helper is now derived from helper roots listed apart
from the store candidates, because Desktop 2.42304.2 moved the store and not the helper. See
"Amendment 2026-10-19" below. The text here records the derivation as implemented on
2026-10-17; the two layouts and the version pattern are unchanged.*

**Status: the maintainer's ruling of 2026-10-17, implemented.** Peer review raised alternatives
to the version pattern below. Round 1 raised binding to the browser's manifest or listing the
folder. Round 2 raised accepting any single folder name, or adding a note for a helper seen at
a location nothing recognises. The maintainer ruled on three points, each as recommended:

1. The new location is recognised by a version folder of dotted ASCII digits only. Not taken:
   any single folder name, binding to the manifest, listing the folder.
2. A note-only warning is added for a `chrome-native-host.exe` counted at a location nothing
   recognises. It advises and never excuses. Not taken: leaving the next move silent.
3. The re-measurement tools are fixed in this change. The 12-minute ceremony itself is not
   re-run. Not taken: deferring the tools to a follow-up.

**What changed (observed 2026-10-17, read-only probes).** Claude Desktop 2.42304.0.0, installed
by the 2026-10-15 update, removed `<Roaming>\Claude\ChromeNativeHost\`. The helper now lives at
`<Roaming>\Claude-NativeHost\<version>\chrome-native-host.exe`, where `<Roaming>` is the folder
holding the store's `Claude` folder (for the packaged app,
`...\Packages\Claude_<pkgid>\LocalCache\Roaming`) and `<version>` is a dotted number such as
`2.42304.0`. The browser's native-messaging manifest in the same `Claude-NativeHost` folder
names that versioned path. On the probed machine both store candidates' `Claude` folders exist,
the classic `%APPDATA%\Claude-NativeHost` exists too, and its manifest reads as the same file as
the packaged one. The copy that outlived the app was launched by Edge, not Chrome.

**What that broke.** `_expected_helper_paths` derived only the old location, so the live helper
was not recognised as the helper at all. It counted as the desktop app, and neither ruling was
reached: RULING 6's hash check and RULING 7's signature check both sit behind that recognition.
A `converge --apply` refused with the app closed. That is the fail-closed direction, but the
opt-in silently stopped working. RULING 7 was written to end exactly that failure, where the
user believes the feature still works. The refusal named the helper's path and nothing else.
The helper note, which only fires for a recognised helper, never appeared. So there was no
pointer to the opt-in, and the only advice, "close the desktop app", could not help with the
app already closed. Had the note fired, its "Closing Chrome clears this" would have been wrong
too: Edge had launched the helper.

**The change.** The helper is recognised at either of two locations, both derived from each
store-root candidate exactly as before (the packaged-app roots found on disk, plus the classic
`%APPDATA%` one whether or not it exists):

- old (kept for installs that have not updated): `<store root's parent>\ChromeNativeHost\chrome-native-host.exe`;
- new: `<the parent of the store root's parent>\Claude-NativeHost\<version>\chrome-native-host.exe`,
  where `<version>` is one path component made of ASCII digits separated by single dots,
  `[0-9]+(\.[0-9]+)*` matched whole. Python's `\d` also accepts digits from other scripts, so
  it is not used.

The old location is still an exact path. The new one is exact except for the version: the
`Claude-NativeHost` folder is derived exactly, and only the version folder's name varies,
within the pattern. After the same separator folding and normalisation as today, the entry's
grandparent must equal a derived `Claude-NativeHost` folder, its parent's name must match the
pattern, and its basename must be `chrome-native-host.exe`. A fabricated path that merely
contains these segments still counts. So do an entry carrying arguments, a binary one folder too
deep or too shallow, and a helper that cannot be read. RULING 6 (measured hash) and RULING 7
(opt-in, a valid signature with CN and O exactly `Anthropic, PBC`) decide exactly as before, for
a helper at either location.

One narrowing rides along. A candidate that is not an absolute path now derives no helper
location. With `APPDATA` unset, the classic candidate is the relative `Claude\claude-code-sessions`,
and the old code resolved its helper location against the working directory. The new layout
would have done the same one level higher, so both are closed.

The refusal note fires at either location, because it uses the same recognition. It now names
the helper by its path instead of "the process above": the converge and sync refusal prints only
the first process it found, which may be the desktop app. It says a browser running the Claude
extension, Chrome or Edge, launches the helper and that it can outlive the app. It says that
closing every such browser should stop it. That is measured for Chrome, whose disconnect made the
helper exit by itself in August; for Edge it was expected, not probed, when this was written. Observed later the same day (2026-10-17), once, on one machine: with Edge closed, no `chrome-native-host.exe` was running, checked both in Task Manager and in a process listing. That is an observation, not a controlled probe. It no longer says the
helper updates "in place".

A second note covers the next move. This one failed closed in silence because the note only
fires for a recognised helper. Now, when a counted process's file name is
`chrome-native-host.exe` but it sits at a location neither rule recognises, the refusal names it
and says neither exclusion can apply. It says the app may have moved the helper again, and that
reporting the location lets the guard follow it. The file-name test only decides whether to
warn: the process keeps counting either way. A recognised helper's note takes precedence.

The remedy the note names has to work as well. `tools/native-host-measurement/watch_ceremony.py`
carried its own definition of the helper, a path-tail test for the old location plus a
`\packages\claude_` anchor check. At the new location the first gate finds no helper, and the
ceremony waits at "helper running" until the operator aborts. The ceremony stops carrying its own
definition: both gates ask the guard's recognition instead, so the instrument and the guard cannot
disagree about which process is the helper, and `check_watch_ceremony.py` pins that. The store
roots it watches are still its own. The cost is a dependency: the ceremony now imports the
guard's module, so it runs from a source checkout, where `tools/` always lived (it does not ship
in the package). Its shutdown prompt names the browser that launched the helper rather than
Chrome alone. `tools/check_signed_helper_optin.py`'s live signature check looked only at the old
packaged path and reported a skip. It now looks wherever the guard looks, both layouts under
every store candidate. It checks every helper it recognises, not the first, and its skip line
says how many candidates it examined. Its decision
table patches recognition away, so it pins the decisions, not recognition, which the tests below
pin.

**Why the trust does not widen.** Measured 2026-10-17 on the probed machine, where both store
candidates' `Claude` folders exist: in each, the `Claude-NativeHost` folder beside it carries
exactly the `Claude` folder's ACL, every entry inherited. Whoever can plant a binary there can
already plant one at the old location, or write the store directly, so RULING 7's limit covers
both. A machine without the classic `%APPDATA%\Claude` rests on an equivalence, not a
measurement. `%APPDATA%\Claude\ChromeNativeHost\` was a trusted location before this change
whether or not `%APPDATA%\Claude` existed, and creating that folder is no harder than creating
`%APPDATA%\Claude-NativeHost`. Deriving only from candidates that exist would change nothing for
such a writer, so the candidates stay as they are.

**Why every version folder, not only the one the manifest names, and why no listing.** The guard
judges running processes only. A process is excused only if its image can be read and hashed,
or signature-checked; an unreadable one counts. So only a version whose binary exists and can be
read can ever be excused. Listing the folder would add I/O and a new failure to handle without
narrowing what can be excused. The browser launches the helper at the path its manifest names. A leftover
version folder therefore matters only while a helper started before an update is still running
from it (an inference; not observed). Binding to the manifest would make that helper count until
the browser restarts it. It would also add a parse of an undocumented, user-writable file to
every guard check, and it would move the fragility rather than remove it, because the manifest's
name and folder are layout details too. Each recognised build is still judged on its own bytes:
by measured hash, or, with the opt-in on, by a valid Anthropic signature. With the opt-in on, a
leftover signed build is trusted exactly as much as a current one. RULING 7 already accepts that
for any future build.

**Why a version pattern at all.** It limits recognition to the folder naming the app was seen to
use. It is not a security boundary, because the folder is user-writable. Its cost: a future
folder name with a suffix, such as a pre-release tag, fails closed, and that would be the next
report.

**Alternatives set aside.** Trusting any Anthropic-signed `chrome-native-host.exe` wherever it
sits would undo the August fix above, which made a copied binary under a look-alike tree count.
Leaving the code alone means closing the browser before every apply, the friction RULING 7
removed. Binding to the manifest and listing the folder are set aside for the reasons above.

**What this says about the new build.** It restores the opt-in's reach; it is not a measurement
of 2.42304.0, and the August measurement is not claimed to transfer to it. What is restored is
RULING 7's existing policy: trust in the publisher, not in measured bytes. Two read-only probes
ran on 2026-10-17. RULING 7's own signature reader reports the installed helper `signed`, CN and
O both `Anthropic, PBC`. A static string scan with the 2026-08-05 literal list finds no
`claude-code-sessions`, `sessions`, `Roaming`, `projects` or `jsonl`. It finds only the
log-location variable `LOCALAPPDATA`, the browser-bridge pipe prefix and `CLAUDE_LOG_LEVEL`.
Like the August scan, that bounds the literals in one image and nothing more. The build's bytes
are not RULING 6's measured hash, so without the opt-in it counts, as it did before the move.
With this change, a read-only probe on the same machine fed the installed helper's lowercased
path to `claude_running` as the only process, the helper itself not running at the time. Both
store candidates recognise it and read it `changed`. With the opt-in marker present it is
excused: the guard reports nothing running.

**Residuals.** A future layout move will still fail closed, but no longer silently, on two
conditions. The helper must keep its file name, and its path must still contain `claude`. The
guard counts only claude-named processes (RULING 4's narrowing), and the helper has always
counted only because its path does. A helper moved to a path without `claude` would not count at
all, warning or not, exactly as before this change. Counting it would change the guard's
counting rule, which this ruling does not touch. Several live helpers (browsers or profiles)
are each signature-checked separately and uncached, as before. The measurement ceremony was not
re-run at the new location, so its changed recognition is untested end to end. Lister entries
are lowercased on every platform while a derived folder keeps its case off Windows; the helper
is a Windows binary, and the old layout had the same property.

**Pinned by** `tests/test_plan_move.py::TestClaudeRunningNarrowing`, which CI runs on Windows and
Linux. At the new location: a measured build is excluded; a changed build counts; changed,
signed and opted in is excluded; that excused helper does not excuse the desktop app beside it;
an unreadable helper counts even signed and opted in; a non-numeric version segment counts,
a non-ASCII digit included; a look-alike tree outside the real root counts; a binary one folder
too deep or too shallow counts; an entry carrying arguments counts; a relative candidate derives
nothing. Both layouts are recognised side by side, and the note's wording and attribution are
checked at both. The unrecognised-location warning fires, including behind the desktop app; it
stays silent with no helper or for a near-miss name; and it gives way to a recognised helper's
note. Two checks under `tools/`, which CI does not run, cover the tools.
`tools/native-host-measurement/check_watch_ceremony.py` pins that the ceremony picks exactly
the processes the guard recognises. `tools/check_signed_helper_optin.py`'s live leg ran on
2026-10-17 against the installed helper and read it signed.

**Follow-up, 2026-10-17, after 0.16.1.** Two notes were extended; no decision changed. First, the
helper note now rides the running-app refusals of `move`, `undo` and `recover` as well as those
of converge and sync, through the same `helper_hash_note`. The last-instant process re-check
inside a move or undo transaction rolls back rather than refusing, and prints its abort reason
without the note, as before. Second, with the opt-in on, a recognised helper that still counts
now gets a line saying why the opt-in did not excuse it. An unreadable helper never qualifies.
Otherwise its signature was not confirmed as valid and by Anthropic, PBC: it is unsigned, signed
by someone else, or the check could not run. That line does not re-run the signature check. It
states what a changed helper still counting with the opt-in on implies. Both are note-only:
`claude_running` decides exactly as RULINGS 6 and 7 say, and no note excuses a process. Pinned
by one refusal test each in `tests/test_plan_move.py`, `tests/test_undo.py` and
`tests/test_recover.py`, and three tests in `TestClaudeRunningNarrowing`.

#### Amendment 2026-10-19 — the user data moved, and the helper did not

**Status: the maintainer's ruling of 2026-10-19, implemented.** The change was revised the same
evening after three rounds of independent review by four models: the layout now lists data
folders rather than stores, names what it does not read, and fails closed when it cannot look
(below). The fixes from the third round, the marker read beside a found store and the corrections
to where the notes appear, were not reviewed again. The maintainer then ruled on three points,
each as recommended:

1. When a package's moved root exists, its old root is never read, and an old store that still
   holds rows is named in `doctor`'s detail. Not taken: refusing every mutation while such a
   store exists; requiring the app's marker before the moved root takes precedence; reading both
   roots.
2. The marker note stays advisory: it explains and never decides. Not taken: making a marker
   that names an unknown location a discovery error, which a stale marker could turn into a
   permanent refusal.
3. The version bump to 0.16.3 lands with the release, under the release procedure; the README
   names 0.16.3 ahead of it. Not taken: bumping on this branch; wording the README as "the next
   release".

Leaving the journaled-path gap (Residuals) to a follow-up is the implementation's choice.

**What changed (observed 2026-10-19, read-only).** Claude Desktop 2.42304.2.0, installed by an
automatic update, moved the app's whole user-data folder out of the package, from
`%LOCALAPPDATA%\Packages\Claude_<pkg>\LocalCache\Roaming\Claude\` to
`%LOCALAPPDATA%\Claude-Data\claude_<pkg>\`, with a lower-case `claude_`. The old folder keeps
one 0-byte file, `.user-data-left.claude_<pkg>`. Modification times survived, so the folder was
moved, not rebuilt, and the live app writes to the new one. The store inside is unchanged:
`claude-code-sessions\<account>\<org>\local_*.json`. The CLI the app spawns moved too, to
`Claude-Data\claude_<pkg>\claude-code\<version>\<hash>\claude.exe`. The browser helper did not
move. It stays at `<package>\LocalCache\Roaming\Claude-NativeHost\<version>\chrome-native-host.exe`,
and the browser's manifest names that path.

**What that broke.** 0.16.2 listed only the old candidates, so every command started from
"store absent": `converge` planned 0 of 0, and `doctor` and `alignment` reported the store
absent. `converge` has no `--store`, so there was no workaround. The guard's CLI marker missed
the moved CLI, which then counted as the desktop app. That should have changed nothing in
practice, on the 2026-08-02 reasoning that the app-spawned CLI exists only while the app runs;
that was not measured for the moved CLI. The real trap was the helper. It was derived from the
store candidates, two folders up, and two folders up from the moved store is
`%LOCALAPPDATA%\Claude-Data\Claude-NativeHost`, where no helper lives. A fix that only added the
new store candidate would have silently disabled the signed-helper opt-in again, the failure
the 2026-10-17 amendment fixed.

**The change.**

- Store candidates, in the order they are read (`_windows_store_layout`):
  1. each moved data root, every entry of `Claude-Data` named `claude_*` that is not a plain
     file, as `<root>\claude-code-sessions`, listed whether or not the store folder exists, as
     the classic candidate always was. Discovery then proves an absence there, reads the app's
     marker beside it, and the identity checks read that root's own `config.json` (measured
     2026-10-19: the moved root's `config.json` carries `lastKnownAccountUuid`, and the old
     folder has no `config.json` at all);
  2. each old packaged data folder, `...\Roaming\Claude\`, that exists, store or not, so the
     marker the app leaves there is read, unless the same package's moved root exists;
  3. the classic `%APPDATA%\Claude\claude-code-sessions`, when `APPDATA` is absolute.
- Precedence: when a package's moved root exists, its old root is superseded. It is not read:
  not its store, not its `config.json`, not its agent-mode folder (the identity and email
  lookups follow the candidates). The updated app was seen writing to the moved root, and the
  old one is what it left; after the observed move it holds only the marker. Reading both would
  list every row twice and give the identity checks a pre-move `config.json`. A superseded
  store that still exists is named in the discovery detail ("not read: ..."), which `doctor`
  prints. Packages match by folder name, case folded.
- Couldn't look is never "nothing moved": when `Claude-Data` exists but cannot be listed,
  nobody can say which packages moved. The failure is carried on the Env (`layout_errors`),
  and discovery reports an error every time, which refuses every mutation, even if a later
  listing would succeed, instead of reading an old store as though nothing had moved. An
  unreadable moved store is an error in discovery itself.
- Helper roots: `Env.helper_roots`, listed apart from the store. On Windows they are each
  package's `LocalCache\Roaming` that is a folder, plus `%APPDATA%` when it is absolute. Both
  layouts derive from them exactly as they did from the store candidates' `<Roaming>`: the old
  one `<root>\Claude\ChromeNativeHost\chrome-native-host.exe`, the versioned one
  `<root>\Claude-NativeHost\<version>\chrome-native-host.exe`. An Env that names no helper root
  recognises no helper. A relative root still derives nothing. Off Windows the roots are what
  the store candidate gave before: `~/Library/Application Support` and `~/.config`.
- The CLI marker adds the measured shape to the end of the entry:
  `\appdata\local\claude-data\claude_<pkg>\claude-code\<version>\<hash>\claude.exe`, whole
  segments, `<pkg>` lowercase letters and digits as observed, `<version>` dotted digits,
  `<hash>` hex. The existing markers are unchanged.
- The marker note. A `.user-data-left.<folder>` file in a folder discovery looked in, beside a
  store it found or in place of one, names the folder the data moved to. Discovery's detail,
  which `doctor` prints and `alignment` and the window show only when no store is found, says
  per marker: nothing, when a store was found in the
  folder it names (the move this version followed); that the folder holds no store, when this
  version looked there; otherwise that it names a location this version does not know. That last
  one is in the detail beside a found store too, so a stale leftover store cannot hide the next
  move from `doctor`. It only explains, and no
  decision depends on it. 0.16.2's bare "store absent" read as "no sessions on this machine",
  although the app had left a marker naming the folder its data went to.
- One narrowing rides along: only an absolute `LOCALAPPDATA` is globbed and only an absolute
  `APPDATA` is used, and glob metacharacters in either are escaped. With either unset, the old
  code resolved a candidate against the working directory, where a look-alike tree would have
  become a store. "Absolute" is `os.path.isabs`, whose reading of a drive-relative `\path`
  differs across Python versions on Windows.

**How far the helper trust widens.** The helper roots are the folders the store candidates'
`<Roaming>` gave on a machine that has not updated, with one widening: a package's `Roaming`
now counts whether or not a store sits under it, which is exactly the updated machine's case.
Any folder matching `Packages\Claude_*` counts, a third party's included. The bar for planting
a binary there is unchanged: before, creating `Packages\Claude_<x>\LocalCache\Roaming\Claude\claude-code-sessions`
made that same `Roaming` a helper root, and whoever can create either can already write the
store directly, which is RULING 7's stated limit. The 2026-10-17 measurement found
`Claude-NativeHost` carrying the `Claude` folder's ACL on the probed machine. No moved data root
is ever a helper root, and each recognised build is still judged on its own bytes or, with the
opt-in, its signature.

**What the precedence costs.** Downgrading to an app version from before the move would make the
old root live again, while this version kept reading the moved one: `converge` and `sync` would
then report success while writing rows the running app never reads. That has not been observed,
and the old store reappearing would show as "not read: ..." in the detail. A stray or empty
`Claude-Data\claude_<pkg>` folder, or a junction of that name, supersedes the package's old root
the same way; the detail then names the unread store, and with no store under the moved root
nothing is planned. A moved root whose folder name differed from its package's would not
supersede the old root, so both would be read. On the observed move the old store folder is
gone, so none of these arises there. Refusing mutations while a superseded store still holds
rows was considered and not taken (ruling 1 above).

**What was measured with the change (2026-10-19, read-only).** A wheel was built from the fix
commit after each review round, last after round 3, installed into a fresh virtual environment,
and run from a process without
package identity, the installed 0.16.2 run through the same checks as a control. Results, 30
checks, all passing:

- the store was found at the moved root only, 2333 rows in three accounts, matching an
  independent count of the files, with the detail a clean "1 root(s)";
- the old packaged store was not a candidate, and was listed as superseded, holding no store;
- the manifest-named helper, both versions on disk and the running one were recognised, state
  `changed`;
- with the opt-in on, the manifest-named helper alone was excused, its signature read live;
- the path two folders up from the moved store was not a helper location;
- the moved CLI was classified as the CLI;
- with the real process list, only the desktop app's processes counted.

The installed 0.16.2 found no store, recognised neither the helper nor the CLI, and counted the
helper.

**Residuals.**

- *Journaled paths from before the move.* Completed `converge` and `new-row` operations record
  each row's absolute path. Those paths are gone after the move, so `doctor` reports every such
  row as vanished, exits 1, and advises running `new-row` again. On the probed machine that was
  320 rows in 10 operations, and every one was present at the same relative path under the moved
  root. `undo` refuses such an operation and changes nothing: for `converge` because the
  recorded paths fall outside every discovered root (a layout error), for `new-row` with "that
  row is already gone", which is false, since the row moved with the store. `undo` of a pre-move
  `move` would fail with an unhandled error, since it reads each recorded row before any
  containment check, again changing nothing; the probed machine's journal held only the 10
  `converge` operations. `recover --back` of
  a pre-move `converge` left unfinished would skip its rows as absent and close the operation
  with the rows still in place; none was unfinished on the probed machine. (The `undo` and
  `recover` behaviour is read from the code, not run.) Not fixed here, because it needs a
  decision on how journaled paths are re-rooted. *(Fixed by "Amendment 2026-10-19
  (journal)" below, which re-measured these in a sandbox: `undo` of a `sync` also reported
  "undone" while removing nothing, and `recover --back` of an unfinished `new-row` or `sync`
  closed the operation the same way.)*
- The measurement tools' own store lookups (`watch_ceremony.py`, `measure_native_host.py`,
  `attribute_store_writers.py`, `resolver_positive_control.py`) still list only the old roots.
  With no store left there they stop with "no store root found"; where a superseded old store
  still holds rows they would measure that store, not the live one.
- Where the notes do not reach: `move`'s "no desktop store found" refusal and `converge`'s
  report do not carry the discovery detail, and `alignment` and the window's Level tab drop it
  once a store is found; when discovery reports an error, the error replaces the notes. `doctor`
  is where they are read.
- The CLI marker is a run of segments, not a path derived from `LOCALAPPDATA`: the same run under
  another prefix still matches, as with the Roaming marker, and a relocated `LOCALAPPDATA`
  counts the CLI as the desktop app (fail-closed).
- No `--apply` has run against the moved store yet. macOS and Linux are untouched.

**Pinned by** `tests/test_data_root.py`, which goes through `_windows_store_layout` and
`discover_stores` end to end: the candidates and their order, per-package precedence with the
package name's case folded, a superseded store named and not read (whatever its case), an
unlistable `Claude-Data` as an error, including when the failure clears before discovery, an
unreadable moved store, a file named like a moved root, relative `LOCALAPPDATA` and `APPDATA`,
glob metacharacters, the helper roots, the identity check reading the moved root's `config.json`
and not a superseded folder's, the `default_env` wiring, and each form of the marker note,
including one beside a store that was found, for a moved, an old and the classic folder.
And by `TestClaudeRunningNarrowing` in `tests/test_plan_move.py`, whose tests check that:

- a store candidate alone derives no helper;
- the moved layout's helper is recognised, and the opt-in reaches it;
- three locations under the moved root are not helper locations;
- the moved CLI is recognised, and ten near misses, among them other depths and files under
  `claude-code`, still count;
- relative helper roots derive nothing.

`tools/check_signed_helper_optin.py`'s live leg now walks the guard's helper roots. On
2026-10-19 it found both helper versions under the packaged root and under the classic one, and
read each as signed.

#### Amendment 2026-10-19 (journal) — paths recorded before the data moved

**Status: the maintainer's ruling of 2026-10-19, implemented.** The amendment above left the
journaled paths of operations from before the move as a residual. The brief for it went
through three rounds of independent review by four models; the third round's fixes were not
reviewed again. The maintainer then ruled on seven points, each as recommended:

1. Re-root at read time. Not taken: rewriting the journal once; re-rooting in `doctor` only
   and refusing everywhere else; leaving it; the resolution gate (3) alone.
2. The mapping lives in memory, and no command changes the paths a manifest recorded. Not
   taken: persisting the mapped paths with a provenance record.
3. The resolution gate, in this change rather than a follow-up. 3.1: unresolved rows are
   listed by `doctor`, not counted in its exit code. Not taken: exit 1.
4. `recover --back`: nothing beyond 1 and 3. Not taken: refusing back.
5. `undo` of a move checks containment and reads every row before anything is journaled.
   Not taken: only catching the error.
6. `new-row`'s "that row is already gone" stays. Not taken: rewording it.
7. Sync's `undo` reports what it reversed, and the window's line follows it. Not taken: the
   bare "undone".

**What was wrong (measured 2026-10-19 in a sandbox against the 0.16.3 code, read from it
where marked).** Every operation journals each row it touched by absolute path. After the
move, a path recorded before it names a folder that holds only the app's marker, and every
consumer read it as written:

- `doctor` reported every row this tool had created as "no longer on disk" and exited 1,
  advising `new-row` again (320 rows in 10 operations on the probed machine).
- `undo` of a `sync` said "undone", exit 0, and removed nothing; with `--update` it put no
  pre-image back; either way the operation was consumed. Its live-store refusal compared the
  old path with the moved store, so it no longer fired.
- `undo` of a `move` ended in an unhandled error. With the old store left behind it read the
  old row, journaled an undo, and then failed path validation, leaving an operation that
  `recover` refused in both directions for good.
- `undo` of a `new-row` refused with "that row is already gone", which was false; of
  `converge` and `retitle` with a layout error; of `repoint` with a misleading one.
- `recover --back` of an unfinished `converge`, `new-row` or `sync` closed it with its rows
  still in the store and recorded nothing; of a `repoint` it recorded "gone from the store",
  and `doctor`'s residue reader, which stats the recorded path, then dropped it.
- With the old store copied rather than moved, `undo` of a `new-row` deleted the row from the
  store discovery never reads and reported success.
- Nothing created the old store folder or wrote a row under it. `recover --forward` refused
  for every type (retitle's and repoint's read from the code).

**The change.**

- The pairs. `_windows_store_moves` takes them from the `Claude-Data` listing alone:
  `Claude-Data\claude_<pkg>\claude-code-sessions` pairs with
  `Packages\Claude_<pkg>\LocalCache\Roaming\Claude\claude-code-sessions` under the same
  `LOCALAPPDATA`, by the package name ruling 1 pairs on. Not the layout's superseded list,
  which names an old store only while its vacated folder exists. `Env.store_moves` carries
  them, and is empty when the layout had errors.
- The mapping. `list_ops`, the only code that loads a manifest, maps the keys a decision
  reads back: `store_path` and sync's `dest_path`; each row's `path`, `dest_path` and
  `store_path`; and each entry of `rollback_residue_paths`. A value equal to an old store, or
  below it at a separator boundary, becomes the moved store plus the same relative path, both
  sides normalized as discovery normalizes its roots, so `claude-code-sessions-bak` never
  matches. Not mapped: `row_path`, `source_path` and `destinations[].path`, which nothing
  decides on, and `live_override.config_path`, audit data beside the store. Every tagged
  release used these key names (checked 2026-10-19). Every consumer, and every comparison
  between operations (rotation's hold, the ownership claims, the live-store check), then sees
  one path.
- Nothing already journaled is rewritten. `Op.rerooted` records each mapping, and
  `save_manifest` serializes a copy with the recorded strings put back, leaving the in-memory
  manifest mapped for the rest of the run. A new operation records the paths it acts on: an
  `undo` of a move from before the move, journaled after it, records the moved ones.
- The resolution gate, `_row_resolved`. A journaled row is resolved when discovery found the
  store and the row's path lies in a discovered root at `<root>\<acct>\<org>\<file>`, and,
  for an operation that records a store, that store is the row's folder. Every function that
  reads a journaled row's file or acts on it asks first; `tests/test_journal_reroot.py` checks
  that list against the code. For an unresolved row:
  - `undo` refuses before changing anything, naming the rows. That includes converge's
    forgiving undo, which used to check containment inside its delete loop, after earlier
    rows were already deleted.
  - `recover --back` reverses what it can check and records the rest under
    `rollback_residue`, with the paths in `rollback_residue_paths`, on every operation type.
    It records an unresolved row whether or not the operation marked it written, since a hard
    kill can land a row and lose the flag; only a converge row marked skipped, never
    attempted, is left out. For sync, retitle and move this replaces a refusal that left the
    operation stuck. A move
    goes through `_abort`, which counted such a row as drifted and refused at `aborting`; it
    now leaves the row alone and keeps both transcript copies, since the row may already point
    at the destination copy.
  - `recover --forward` is not offered. Finishing a committed move touches no row, so it is.
  - `doctor` lists the row under "cannot be checked", apart from the vanished rows, and does
    not count it in the exit code.
  A discovery error refuses `undo` and `recover` in both directions, and changes nothing: on
  a layout error the old packaged stores come back as discovery's roots. The nine sites that
  checked containment against the journal's own store path meet the gate first.
- `doctor`'s residue reader checks every row a residue names: the paths in
  `rollback_residue_paths`, or, for a residue recorded before those existed, its first row's
  `dest_path` or a move's `path`. It reports while any named row is present, lists an
  unresolved one, and drops the residue when every named row is resolved and absent.
- `undo` of a move checks discovery and every row's containment, then reads each row, before
  an undo operation is journaled. A missing row refuses as missing; any other read error
  refuses naming it.
- Sync's `undo` records `undo_report`: rows removed, restored, added rows already gone, and
  refreshed rows not restored because the account deleted them since. `undo` prints it and
  the window's line follows it, where it used to say "the copied rows were removed" every
  time.
- `doctor` prints "N operation(s) recorded before the data moved; their paths are read under
  the moved root" when there are any, and `--json` carries the count.
- Three expectations changed. A hand-edited sync row outside the store is refused by the
  gate as a refusal (exit 1) where a layout error (exit 2) answered before. A stalled sync with
  an uncontained pending row offers `back` alone, no longer a `forward` that could only fail.
  And `back` on a new-row whose path a corrupt journal sent outside the store records why it
  left the row ("not one this version reads") where it used to record "could not remove",
  having tried.

**Measured with the change (2026-10-20, read-only, the probed machine).** From the branch,
against the live store and journal: `doctor` exited 0 with no row vanished, none unresolved
and 10 operations read under the moved root; all 320 written rows resolved and present. An
`undo` dry run previewed the newest converge. On a copy of the journal with one row's file and
conversation made up, `doctor` reported that one row vanished, exit 1. The live journal's
files hashed the same before and after.

**Residuals.**

- Converge's `undo` deletes a row the app has since rewritten when its conversation, title
  and title source are unchanged and it is not the last pointer: field evidence, not bytes.
  With the mapping it reaches pre-move rows again, on that rule and the gate. On the probed
  machine 155 of the 320 rows had been rewritten. Every other undo route refuses a changed
  row.
- A further move from the moved root needs discovery to learn the new root before the pairs
  can follow it.
- Rows that resolve are still checked against the journal's recorded store at the write
  paths, after the gate.
- A Windows folder with per-directory case sensitivity, where two package folders could
  differ only by case, is not considered.
- An unresolved row stays listed for as long as its store is out of reach.
- macOS and Linux have no moved roots, so nothing changes there.

**Pinned by** `tests/test_journal_reroot.py`, 60 tests: the pairs, what maps and what does
not, the save-side restore, `doctor`'s counts and its untouched journal, `undo` and `recover`
of every operation type from before the move, a store left behind, every route of the gate,
an operation half resolved in both row orders, a discovery error, the residue reader, move
undo's refusals, the sync tally and the window's line, and a check that every function reading
a journaled row goes through the gate. Two tests in `tests/test_sync.py` and one case in
`tools/check_new_row.py` changed expectation, as above.

#### Amendment 2026-10-20 — the window's saved destination, after the move

**Status: the maintainer's ruling of 2026-10-20, implemented.** Three points, each as recommended
after three rounds of independent review of the design: no write target changes without a pick;
a saved destination proven gone is set aside, and the window asks; a saved store whose account is
now signed in is named rather than asked about, and the candidate with the saved ids is marked.
Not taken: keeping "Change destination" as it was; a pointer alone, with no set-aside; re-rooting
the saved path through the move's package pairs now, a follow-up once the journal's pairing
lands; re-resolving by the saved ids under any root.

*(Amended the same day by "Amendment 2026-10-20 (re-root)" below: a saved value proven gone is
first re-rooted through the journal's package pairs, and the preference file also changes, after
a successful Apply into a re-rooted saved destination, to that same store's new path. The text
here is otherwise unchanged.)*

**What broke.** The window saves its chosen sync destination as a full store path in
`~/.claude-code-sessions-gui.json`. One saved before the move names a folder under the old root.
Every plan passed it as `--to`, which matched no moved store, and the window showed the refusal
bare, with Apply off; only the "Change destination" button got out, and nothing pointed at it.
That button had a fault of its own: it erased the saved value first, so a cancelled pick lost it,
and on a machine with one other store the next plan went straight into that store with no pick.
A third fault predates the move: a pick saved `_resolve_pair(p) or p`, and where two roots hold
the same `<account>/<org>` that is the bare pair, which the next plan refused as ambiguous, back
into the picker.

**What the window does now.**

- Before each plan the worker classifies the saved value (`_destination_findings`). It is proven
  gone when it lies at or under a superseded store (ruling 1 above), or looking at it raises
  `FileNotFoundError` or `NotADirectoryError`; both sides go through `realpath` and
  `_path_match_key`. A path that exists but no root holds is not gone: a newer build may know it.
  Its account is checked against `live_account`, and no claim is made when that returns nothing
  or discovery fails.
- A set-aside, or a press of "Change destination", plans with no destination through the pick
  flow. More than one other store opens the picker; exactly one opens a one-candidate picker with
  no plan installed, so Apply and the bulk-copy tick have nothing to act on; none, or another
  refusal, shows that refusal with the reason. A pick saves the path its candidate line prints.
  The preference file changes only on a pick, and a cancel leaves it as it was.
- A saved store whose account is signed in is named on the detail line, and no picker opens on
  the window's own; "Change destination" still opens one. The candidate whose path ends in the
  saved ids gets a note line under its button; two matches mark nothing.
- A saved value left in place that the plan refuses with "matched no other account store" gets a
  pointer to "Change destination", which now shows whenever a destination is saved.

**Residuals.**

- `FileNotFoundError` can also come from an unavailable network or removable path. The set-aside
  then gives a reason that is not quite true, and erases nothing.
- The window builds its `Env` once, at open, so a move made while it is open is seen after a
  restart, as discovery's own view is.
- A saved path spelled with `/`, like one typed after `--to` at the CLI, still matches nothing:
  `resolve_sync_endpoints` does not fold separators. The window points at "Change destination"
  for it. *Superseded by "Amendment 2026-10-20 (separators)" below: the core now folds
  separators, so a `/`-spelled saved path that names a listed store plans into it.*

**Pinned by** `tests/test_gui_destination.py`: the candidate path for every label form, the saved
ids, proven-gone with its controls, the mark, the findings on a real `Env`, and the core's
sentences and candidate-line format the window routes on. And by
`tools/check_gui_stale_destination.py`, which drives the window over synthetic moved layouts:
the set-aside with candidates of one account and of two, a cancel and the next plan asking
again, one other store of the saved account and of another, the move with an account switch
over zero, one and two other stores, "Change destination" over a saved destination that is
fine, two roots holding the same ids, and, unchanged, no saved destination, an unlisted path, a
fragment, the signed-in store itself, an identity disagreement, a discovery error and a saved
value that went ambiguous.

#### Amendment 2026-10-20 (separators) — store paths in either spelling

**Status: the maintainer's ruling of 2026-10-20, implemented.** Four points, each as recommended
after three rounds of independent review: `--to` and store-scope `--live` fold path separators,
beside today's test rather than in place of it; on every platform; a trailing separator may match
the end of a store path, in all four matchers; a `--live` value made only of separators is refused
like a blank one. Not taken: folding the whole value, as `converge --live` does; no change;
folding in the window only; a refusal that names the spelling; testing each field separately;
Windows only; the trailing-separator rule at two sites, or nowhere; stripping trailing separators
from the value; leaving a separator-only `--live` as it was.

**What broke.** `sync --to`, and the store-scope branch of `_resolve_live_assertion` (`--live` for
`sync`, `repoint`, `new-row` and `retitle`), tested `value.lower()` against a store's joined
account, org, email and path text with separators left as they were, while `--store` and
`converge --live` already folded both sides through `_path_match_key`. On Windows a store path
spelled with `/` - what Git Bash hands Python for `/c/Users/...` - matched nothing, and under
`--live` drew the warning that an account named by neither file is evidence of something else
being wrong. Git Bash also keeps a Tab-completed folder's trailing `/`, and no store path ends in
a separator, so even the folding matchers refused that. And `--live \` certified a sole candidate
on Windows: every store path holds the native separator, so the value matched as a blank one
would, the bare override RULING 5 refuses.

**What the matchers do now.**

- `_store_matches` serves `--to` and store-scope `--live`: today's joined-text test, or the value
  through `_path_match_key` as a substring of the store path alone, through `_path_hay`. Being an
  OR, it can only add stores: a value that named one store still names it, or now matches more and
  gets the ambiguity refusal that lists them. A value with no separator in it matches exactly as
  before.
- `_path_hay` folds a path and appends one `/`, so a value ending in a separator can match the
  path's end. All four matchers use it on their path side (`_store_matches` for two of them,
  `_repoint_store`, `_principal_matches`). Nothing is stripped from the value, so `Vendor/` still stops short of
  `Vendor-Data`. The `/` is withheld when the value without its trailing separators is the printed
  account form, so `0003/<org id>/` cannot match at the end of a path whose account id merely ends
  in `0003`.
- The printed account form is still parsed first and matched as two anchored prefixes. Its `\`
  twin is not pair-shaped and stays a substring, as it always was on Windows.
- `_resolve_live_assertion`'s first check refuses a value made only of separators and whitespace,
  in both scopes, and its sentence now says so.
- No sentence the window routes on changed. A `/`-spelled saved path naming a listed store of
  another account now plans into it; one naming several stores reaches the existing "no longer
  unique" picker, whose "probably because an account was added" is the wrong reason for a
  hand-edited value, left as it is.

**Residuals.**

- `converge --live` keeps its whole-value fold, so there a value holding `\` would stop matching an
  email that holds one, as a quoted address may. Rare; not changed.
- On POSIX a `\` is a legal filename character, so a value newly accepted there may not name the
  folder its author meant. These commands cannot write the desktop store off Windows, so it shows
  only in a plan. Worth revisiting if POSIX writes are enabled.
- A value that resolved before can now be refused as ambiguous, but only if it holds a separator
  and its folded form also matches another store's path: on Windows that needs an email holding
  `/`, or a match spanning two fields of the joined text.
- Doubled separators (`C://x`) are not handled.

**Pinned by** `tests/test_sync.py`: the other spelling through `--to`, `plan_sync` and `--live`;
a trailing separator; a fragment; a broad fragment with one other store and with two; the
signed-in store's exact refusal in every spelling; the mid-uuid pair with and without a trailing
separator, and its `\` twin; values that matched before; two roots whose names share a prefix; a
separator-only `--live` in both scopes; `repoint --live`. By `tests/test_retitle.py` and
`tests/test_converge.py`: `--store` and `converge --live` with a trailing separator, and the
mid-uuid pair with one. Each builds its value in the separator the platform does not use, so both
CI legs exercise the fold. And by `tools/check_gui_stale_destination.py`, section 8: a
`/`-spelled saved store with and without a trailing `/`, an account folder naming two stores,
and, unchanged, an unlisted folder and the signed-in store.

#### Amendment 2026-10-20 (re-root) — the saved destination, re-rooted through the package pairs

**Status: the maintainer's ruling of 2026-10-20, implemented.** The follow-up "Amendment 2026-10-20"
above named, once the journal's pair helper was on `main`. Three points, as recommended after three
rounds of independent review: a re-rooted store is the same destination, moved whole by the app,
so the window plans into it with no pick; the preference file changes only on a pick, or, after a
successful Apply into a re-rooted saved destination, to that same store's new path; and the
behaviour below. Not taken: re-rooting in memory on every plan and never saving the new path;
saving it as soon as a re-rooted plan is shown, which would make a Refresh write a file.

**Why save after an Apply.** Kept in memory only, a saved old path stays in the file. If
`Claude-Data` later disappears while an old leftover copy is still on disk, that path is no longer
proven gone, and the window would plan into the leftover with no pick. A saved moved path is
proven gone in the same state, and the window asks. The Apply is an explicit act on that store,
and it ends the notice; a store with nothing to copy keeps the notice until the user picks it.

**What the window does now.**

- When a saved value is proven gone, `_destination_findings` also asks `_reroot_destination`:
  `_rerooted_path` over `_reroot_pairs(env)` (the journal's own helpers; no second pairing rule),
  then the one store `_account_dirs` lists whose key equals it, in the listing's spelling. No
  pair, no listed store, more than one, or a listing that raises: no re-root, and the set-aside
  above. A leftover old copy changes nothing; the moved root wins, as ruling 1 says.
- Signed in now means the account a held identity answer resolves to
  (`_resolve_live_assertion`, the account `plan_sync` will use) when the window holds one, else
  `live_account`; a lookup that raises makes no claim. A signed-in saved store is named, at its new
  path when it re-roots, and is never planned into. The signed-in and pointer routes redraw the
  identity banner, so a held answer shows as in force instead of the question it answered.
- A re-rooted, not signed-in store is planned into with its listed path as `--to`, no pick. The
  detail line carries two sentences, used apart: the location (moved with the app's data folder,
  from the old path to the new) on every route where a re-root was found, a picker's reason
  included; and the plan clause only over an installed re-rooted plan: applying saves the new path,
  or, with nothing to copy, picking it under "Change destination" does.
- After a successful Apply into that plan, `_save_after_reroot` re-reads the file. Still the value
  the plan re-rooted from: it writes the manifest's `dest_path`, the discovery form the picker's
  candidate line also prints, and `dest_choice` follows only when `save_pref` reports it wrote.
  Changed by another window's pick: left as it is, adopted, and named. Unreadable, or a write that
  did not take: reported, and the next plan re-roots again. The outcome shows once, on the Apply's
  own detail line. `save_pref` now returns whether it wrote; a pick still ignores it.
- A cancel after "Change destination" over a re-rootable value plans again with the saved
  destination, so the re-rooted plan comes back.

**Residuals.**

- The re-read and the write are two steps, so a pick saved by another window between them is
  overwritten. A lock across processes is out of proportion for a preference file.
- The pairs, like the superseded list, come from the `Env` built at window open. A move made while
  the window is open is not re-rooted until a restart, and a copied old folder stays the store the
  window plans into, as on every route, Apply's write-time checks included.
- A failed save on a pick is still silent, as before; only the new save after an Apply reports.
- A second app move needs discovery to learn the new root first, and a pair from one moved root to
  the next.

**Pinned by** `tests/test_gui_destination.py`: the re-root lookup (the listed store, the vacated
folder deleted, a leftover copy, a store that did not move, a classic-root path, the moved store
itself, no pairs, a listing that raises, upper-case ids, a lower-case drive with forward slashes
on Windows, values that are not paths), the findings carrying it and taking a held answer, the
save's outcomes and `save_pref`'s result, and the two sentences. And by
`tools/check_gui_stale_destination.py`, section 9, whose `Env` now takes its pairs from the
synthetic layout:
a re-rooted plan installed with Apply available and a real Apply saving the new path and ending
the notice, a refused Apply, an unwritable file, another window's pick, a level store, one other
store, a held answer naming the saved account, and "Change destination" with a cancel. Its
set-aside scenarios now save a path under a package no pair re-roots.


### The lister, and its two deliberate costs

`_default_process_lister()` resolves full executable paths on Windows via
`Get-CimInstance Win32_Process` (through PowerShell), which is what makes the narrowing above
possible at all — since both the desktop app and the CLI share the image name `claude.exe`,
only the path tells them apart. If CIM produces nothing usable — an error, or a return code of
0 with no parseable output (a real machine never legitimately reports zero processes) — it
falls back to name-only `tasklist` output, where a claude-named entry with no resolvable path
is treated as the desktop app per the narrowing rule above. POSIX uses `ps -A -o pid=,args=`
unchanged.

Two costs were accepted deliberately here, not overlooked:

- **Total enumeration failure reads as "possibly running," never as "nothing running."** Any
  unusable result on either platform — an exception, a non-zero return code, or a zero-return
  empty result — returns the `_PROC_UNAVAILABLE` sentinel (text containing "claude" so every
  guard's substring match treats it as a claude-named process, with no CLI marker so the
  narrowing never excuses it) instead of an empty list. Every guard built on `claude_running`
  refuses rather than fails open when the process list can't be read — this reaches beyond
  `sync`'s own guard to `move`'s guards too (`plan_move`'s pre-flight check, `execute_op`'s
  last-instant revalidation before committing, `_finish_committed`'s guard before it deletes the
  now-redundant source copy, and `run_undo`'s own check), since they all call `claude_running`
  directly. `_guard_mutation` gives this specific case its own honest wording
  ("the running-process list could not be read... refusing to {what} another account's store
  while that is unavailable") rather than falsely claiming the app is running; `move`'s guards
  report it as an ordinary "Claude appears to be running" refusal, because to them the sentinel
  is just another claude-named entry. `doctor` never calls `claude_running` itself — it is
  read-only — but a refusal any of these guards raises leaves a non-terminal op behind that
  `doctor` flags and `recover` has to resolve, so enumeration failure still surfaces there,
  once removed.
- **The CIM call spawns PowerShell**, adding roughly 0.3–1.5 s wherever `claude_running` is
  consulted. That is every mutation gate — rare, human-paced operations — so this cost is
  accepted as-is rather than cached or optimised away.

### The accepted TOCTOU residual

The guard checks **once**, at the gate it's called from. For `sync`: `run_sync` checks before
journaling (the earliest clean point, so a refusal leaves no lock file and no op directory
behind); `execute_sync_op` checks again at the top of its own run — the *only* guard a
crash-resumed `recover --forward` gets, since resuming re-enters `execute_sync_op` directly
rather than `run_sync`; `_sync_delete_targets` checks once, shared by `undo_sync` and
`recover --back`. A desktop app that starts **after** the relevant check — mid-write,
mid-delete — is not itself caught, because there is no second running-process check later in
that same call. This is the same posture `move` already had: its own guards (`plan_move`'s
check, and `execute_op`'s last-instant revalidation before committing) are each a single
point-in-time check, not a continuous one.

The second layer, for both commands, is the overwrite/drift refusal that already exists for a
different reason: `_sync_write_rows` refuses if a destination row's bytes have changed since
planning, and `_sync_delete_targets` (via `undo_sync`, or skipped rather than refused for
`recover --back`) treats a row that has drifted since it wrote it as unsafe to delete. Neither
check detects "the app is running" — each detects "the app already touched *this* row" — so
they only close the gap when the app that started mid-operation happens to touch a row this run
is also touching. An app that starts mid-write and never touches the rows in flight is a
residual gap this design accepts rather than one it claims to close.

**And they do not fully close it even for a row both are touching** (added 2026-08-19 — every
engine on RULING 8's review panel raised this independently). `_sync_write_rows` *reads* the
destination row, compares it with the pre-image, and *then* calls `atomic_write`: those are
separate steps, and `atomic_write` guarantees an indivisible replacement, not a
compare-and-swap. A write landing in that window is overwritten with no refusal. The same
shape exists in reverse on the reversal path, where `_sync_delete_targets` classifies rows and
`_sync_unlink_all` / `_sync_restore_all` mutate them afterwards. So the honest claim is
**"refuses on drift observed at check time"**, not "cannot overwrite a row that changed". The
window is microseconds wide behind a guard that already requires the app to be closed, and
closing it properly needs a compare-and-swap primitive this file layout does not offer —
accepted, but stated rather than implied. RULING 8 raises what it costs: before `--update` the
worst case in that window was failing to add a row; now it is discarding a concurrent edit.

## `sync`'s journal, and how `recover`/`undo` treat it

*(describes this tool's own design, not a reverse-engineered fact about the app)*

A `sync` operation reuses `move`'s journal, single-instance lock, and rotation — the same
`ops/<op-id>/manifest.json`, the same `doctor`/`recover` bookkeeping — but moves through a much
shorter phase sequence, because nothing is ever deleted and no transcript moves:

```
journaled -> writing -> completed
```

Each row is marked `written: true` in the manifest after its `atomic_write`, so an interrupted
run leaves a record of which rows landed and which didn't (see the journal-budget note at the
end of this section for exactly how fine-grained that record is). A run that finds nothing to
copy never creates an operation at all — there is nothing to journal.

What is journaled is the operation, not the report. `plan_sync` returns a `tally` of every
session the run skipped and why — including the ones the destination account deliberately deleted
— for the CLI to print; `run_sync` strips it from the copy it journals, so those titles are never
written into `~/.claude-code-journal/`. Nothing in execute/undo/recover reads it.

**Every row is re-read immediately before it would be written.** Absent → write it. Present and
already byte-identical to what `sync` would write → leave it alone and mark the row done anyway;
this is what makes re-running, or resuming a crashed run, safe. Present with *different* bytes →
refuse, because the destination account has touched that row since the sync was planned (opened
the session, for instance) and overwriting it would discard that. The refusal leaves the op at
`writing`, not a terminal status.

**`sync` also re-confirms, at the moment it writes, that the destination is still the dormant
account** — the same check that chose it at plan time (`resolve_sync_endpoints`), run again
against `live_account()` at execute time. This catches an account **switch** between planning
and writing (or a hand-edited/stale manifest); it is the *same* determination run again, not an
independent verification — if `live_account()` was wrong at plan time it is wrong again at
execute time, and the two agree. A sync planned under `--live` adds one certified input to this
re-check — the journaled `live_override` record, revalidated against the identity files at
every mutation, never trusted from the manifest alone (RULING 5, above).

This tool originally treated that re-check as sufficient on its own whenever `live_account()`'s
answer came from `~/.claude.json`'s `oauthAccount` (`Account.resolved_from == "oauth"`), on the
reasoning that `oauthAccount` names the account, org, and email outright and so is strong
evidence — `sync` shipped with no running-app guard for that case, only falling back to one when
the weaker `config.json` evidence was all that was available. That reasoning held for the wrong
file: see "The identity-file model and the running-app guard" below, where a real desktop
account switch measured `oauthAccount` itself staying stale across it. Since **RULING 4**
(2026-08-02), the running-app guard (`_guard_mutation`) applies to every sync mutation — the
write side (`execute_sync_op`) and the delete side (`_sync_delete_targets`, shared by
`undo_sync` and `recover`'s `back` arm) — regardless of `resolved_from`. `resolved_from` is kept
on the resolved `Account` only for messages and diagnostics (the dry run still labels a source
resolved from `config.json` rather than `oauthAccount` explicitly, e.g. `from  (from
config.json)`), never as a guard exemption.

**The dormant account's email is recoverable, which was not obvious.** `oauthAccount` names
only the live account, so the destination printed as `(email unknown)` — eight hex characters
to identify the account you are about to write into. But the desktop app runs local agent mode
in a per-account sandbox and drops a Claude Code config inside it, at
`local-agent-mode-sessions\<accountUuid>\<orgUuid>\**\.claude\.claude.json`, whose own
`oauthAccount` names *that* account (observed on Windows, August 2026). `dormant_account_email`
reads it and accepts the address only when the `accountUuid` inside matches the one being
resolved — trusting an unrelated sandbox's email would mislabel an account, which is worse than
no label. It is best-effort by design: the directory exists only for an account that has used
local agent mode (of the two accounts it was found on, one had 109 such files and the other
none), it is a nested detail of a feature this tool does not otherwise touch, and any failure
degrades to `(email unknown)` rather than erroring. A recovered email also becomes matchable by
`--to`.

The related-but-separate rule is that `sync` refuses outright if it cannot identify the
signed-in account at all, from neither `~/.claude.json` nor `config.json`: with no live account
confirmed, there is nothing to check the named destination *against*. `--to` only narrows which
dormant store to use among several; it cannot supply that missing certainty, and does not
attempt to. `--live` (RULING 5) cannot either — it arbitrates a *disagreement* between two
claims that both exist, and is refused in the no-evidence state, where nothing on disk could
corroborate the assertion.

**An empty store directory can manufacture ambiguity, so the candidate listing counts rows.**
Observed August 2026: the desktop app created a store directory holding exactly one file —
`scheduled-tasks.json`, 87 bytes — and no `local_*.json` rows at all. Because candidates are
every `<accountUuid>\<organizationUuid>` pair on disk, that artifact became a second candidate
and turned a previously unambiguous `sync` into a refusal. Worse, it sat under the *same*
dormant account as the real 290-file store, so the two listed lines carried the same account
uuid and the same email and differed only in an org-id prefix — nothing said which one held the
sessions. `_candidate_line` now prints a listing-row count per candidate (`(262 rows)` vs `(no
listing rows)`), and `_candidate_listing` appends a footnote explaining what an empty store is
whenever one is listed. Zero-row candidates are deliberately **not** excluded: a store with no
rows yet is a legitimate destination the moment its account/org pair is signed in to, and
silently narrowing the choice is the opposite of the rest of this module. The count is evidence
offered, never a filter applied. An unreadable directory reports `(row count unreadable)` and
never `(no listing rows)` — "couldn't look" is never "nothing there", and here the store printed
as empty is the one the user will rule out.

**A stuck sync is recoverable, and `back` is always on the table.** A non-terminal sync op
always offers `back`, and additionally offers `forward` whenever rolling forward still looks
viable. `back` is unconditionally safe here — it only ever deletes rows the op recorded as
written *and* that are still byte-identical to what it wrote, skipping everything else — so
there is no state in which withholding it is correct.

That matters because drift is only one of the ways forward can be permanently blocked. If a
destination row drifts while a sync is non-terminal, `writing` can never complete (`sync`
refuses on that same row every time it re-enters, per the re-read rule above) and `recover`
offers `back` alone, naming the blocking row. But `atomic_write` raising `OSError`, the
row-containment `LayoutError` and the vanished-store `LayoutError` all leave the pending row
simply **absent**, which is not drift at all — classifying on drift alone called those
"forward", forward raised the same error on every retry, `back` was refused as unsafe and `undo`
refused the op for not being `completed`. Every exit refused; the op was stuck forever.

Resolving with `--resolve <op-id> --back --apply` removes every row this op can verify it wrote
— present, and still byte-identical to what was written — and **skips**, rather than deletes,
any row it wrote that has since drifted or gone unreadable, reporting what it left behind. It
always ends at `rolled_back`, because `back` is the only guaranteed exit from a stuck op and
must terminate rather than recreate the same dead end.

**The journal is written on a byte budget, not once per row.** `save_manifest` rewrites and
fsyncs the whole manifest, which carries a base64 post-image of every row — so flagging each row
`written` with its own save costs *rows × manifest* bytes. Measured at 60 stripped rows (~2 KB
each) that is 11.9 MB; extrapolated to this machine's real rows under `--verbatim` (432 rows,
the largest 1.36 MB) it is a ~385 MB manifest rewritten 432 times, over 160 GB of fsynced I/O.
So `execute_sync_op` spends a fixed byte budget (`SYNC_JOURNAL_BYTE_BUDGET`) on per-row
journaling and stops when it is gone. How far that stretches depends on manifest *size*, not row
count — it buys `budget ÷ manifest bytes` individual saves. A small run journals every row; a
stripped sync of this machine's own corpus (432 rows, ~417 KB of manifest) journals roughly the
first 78 individually and batches the rest; a ~385 MB `--verbatim` manifest is written once. The
manifest is also always written on the way out of the loop, normally *or* through an exception,
so every failure the process can observe still leaves an exact record — only a hard kill (power
loss, `SIGKILL`) can lose the tail. A manifest that under-reports what landed is harmless for
rolling *forward*: the re-read rule above recognises a row that already holds exactly the planned
bytes and marks it done rather than duplicating or refusing.

Rolling *back* is the direction where a lost tail shows. `back` only removes rows the manifest
records as `written`, so rows that landed in a batch the kill discarded are left in the
destination and `back` reports that it removed nothing. The route that cleans them up is
`recover --resolve <op> --forward --apply` — which re-reads them, recognises them, and records
them — followed by `undo --id <op> --apply`. `back` names that route when it removes nothing.

This is a deliberate asymmetry with `undo` of a *completed* sync: `undo` refuses entirely,
touching nothing, if even one row it wrote has drifted or gone unreadable. A completed operation
being reversed at the user's explicit request is a case where a surprise should mean stop and
ask, not best-effort cleanup — the opposite of what a stuck, still-in-flight op needs.

Every route here first asks whether each row is in a store this version reads, and a row in
one it does not read follows the same asymmetry: `undo` refuses, `back` leaves it alone and
records it. `undo` also records what it did in `undo_report` and prints it. See "Amendment
2026-10-19 (journal)".

## Open question — is anchoring `sync` to the live account still earning its cost? (2026-08-21)

**Not a ruling. A question raised by the user after `repoint` shipped, recorded because the
answer is not obvious and the change it implies is large.**

`sync` defines its source as the **live** account: `resolve_sync_endpoints` sets
`source = live_account(env)`, and the destination is refused if it might be live
(`_refuse_dest_possibly_live`). The rule this enforces is *never write the account you are
currently using*.

The user's observation: **the tool never needed to be signed in to READ a store.** All stores
sit on one disk and are readable at any time. Liveness is not a data requirement — it is the
anchor for a safety rule. And `repoint` is the existence proof that the rule is stronger than
it strictly needs to be: it writes the LIVE account's store, deliberately, and its safety rests
entirely on the running-app guard. With the app closed, every store is equally inert and the
live/dormant distinction stops doing work.

If that reasoning holds, a `sync --from X --to Y` gated on the same app-closed guard would be
as safe as `repoint` already is, and would remove:

- signing into the source account before every sync — the single largest piece of friction in
  ordinary use, and the thing that made the 2026-08-21 incident possible in the first place
  (opening a session under a different account is exactly what repointed its row);
- most of **RULING 5** — the `--live` certification, the identity-disagreement refusal, the
  GUI's account picker — all of which exist only to answer "which account is live?";
- the account-ambiguity refusals that answer the same question a second time.

**What the current design buys, and would be traded away.** Live-anchoring is defence in depth:
it makes "overwrite the account you are actively using" structurally impossible even when the
running-app guard is *wrong*. That is not hypothetical - the guard was wrong for four days when
the Chrome helper's hash-bound exclusion lapsed (RULING 6/7), and it is a point-in-time process
check with a documented residual window either way. Live-anchoring is the layer that holds when
that one fails.

**What would settle it.** How often is the guard actually wrong, and what is the blast radius
when it is? A write into a dormant store under a running app corrupts a sidebar the user is not
looking at; the same write into the live store lands under the app's feet. If the guard's
failure rate is materially non-zero, the current design is right and the friction is the price.
If the guard is reliable and the residual is only the microsecond check-to-write window, then
the friction is buying very little and `repoint` has been demonstrating that for a release.

**Not to be decided in passing.** `repoint` is one row, named explicitly, by a user who has just
been told exactly what it will do. `sync` is up to hundreds of rows in one apply. The asymmetry
in blast radius is a real argument for the asymmetry in rules, and any change here needs the
review panel and a measurement of the guard, not a refactor.

## Open question — the swap report answers a question about the LINEAGE, not about the ROW (2026-08-22)

**Two of the three symptoms below shipped fixed in 0.9.13. The first is still open, and it is
the one that needs a decision rather than an implementation — read on.**

- **Symptom 1 — reachability spans accounts you are not working in. STILL OPEN.** A related but
  narrower bug (the destination's own rows being ignored) was fixed in 0.9.13; see the section
  below. This one is different and unfixed: "still reachable from another account" remains true
  of an account the user has not opened in weeks and may not open again. Fixing it means the
  plan must know which accounts are *in play*, which sync does not — it knows a source and a
  destination. Whether that pair IS the scope is a design decision, not an implementation
  detail, and it changes what the orphan check means.
- **Symptom 2 — the report never said a row got SHORTER. FIXED in 0.9.13.** `_displaced_sizes`
  counts prose turns either side of a swap and `_length_clause` states the result: "this row
  goes from 388 to 189 prose turns - 199 FEWER". Reported even when the overlap cannot be
  measured, because a length does not depend on the comparison. Carried on the row as
  `displaced_turns` / `incoming_turns`, so `--json` and the window get it too. `repoint` still
  does not compare content — that gap stands.
- **Symptom 3 — interruption markers counted as prose. FIXED in 0.9.13.**
  `TRANSCRIPT_PLUMBING_PREFIXES` excludes app-emitted turns from the fingerprint. Deliberately
  narrow: only text the app itself writes, never a turn a person typed, because deciding which
  of the user's own words are "real" is not that function's job. Over-filtering degrades to NOT
  MEASURED, never to a wrong percentage.

**The original record follows, unedited — the reasoning that produced the fixes, and the cost
argument that still applies to symptom 1.**

---

**Not a ruling. Three symptoms of one gap, all hit in a single afternoon of real use on
2026-08-22, recorded because the fix is not obviously a small one.**

When `sync --update` swaps which conversation a row opens, the report says two things about it:
whether the displaced conversation is **orphaned**, and what **fraction of its prose** already
appears in the incoming one. Both are properties of the CONVERSATION. Neither is a property of
the ROW the user is about to change. That turns out to matter, in three different ways.

### 1. "Still reachable" is measured across ALL accounts, including ones out of scope

`plan_sync` sets `displaced_orphan = False` the moment ANY store holds a pointer to the
displaced conversation (`if sid in pointers`). On a three-account machine, a user working two of
them sees "still reachable from another account" for a row whose *only* remaining door is the
third account - the one they are not signed into and may not open for weeks.

Measured: a 436-turn record of an active legal matter, reachable from exactly one row on one
account. The plan reported it safe to overwrite, correctly by its own definition, because a
different account held it. The user's actual question - "can *I* still get to it from the
accounts I use?" - was answered NO by the same data.

### 2. The report never says the row got SHORTER

A swap that takes a row from a 436-turn conversation to a 336-turn one is not flagged at all, as
long as the 436-turn one is reachable somewhere. Two of four swaps in one real plan were
regressions of this kind (-256 turns and -100 turns); the report's only comment on them was the
overlap percentage, which the user has to convert into a length judgement themselves.

The same blind spot exists in `repoint`, which prints `opens now` / `will open` and the incoming
file's size on disk, but never compares their content. One of fourteen repoints applied that
day moved a row from a 181-turn conversation to a 174-turn one - a deliberate and defensible
choice (the shorter branch had the ending where the work shipped) but presented as if it were
purely a gain.

### 3. Interruption markers count as prose, inflating apparent risk

`_message_fingerprints` already excludes timestamps and tool blocks, for good measured reasons.
It does NOT exclude `[Request interrupted by user]`, `No response requested`, "I accidentally
closed the app", or the other plumbing the app writes into a transcript. Three rows in one plan
reported 95%, 98% and 98% overlap - implying a handful of real turns at risk - and on inspection
every single non-overlapping turn was one of those markers. The number made safe overwrites look
dangerous, which is the opposite of the error it was built to prevent, and it pushes users
toward the habit this report exists to discourage: accepting the warning without reading it.

**What a fix would have to decide.** Symptom 3 is a filter and is nearly free. Symptoms 1 and 2
are not: reporting "this ROW loses N turns" means comparing content on every swap row rather
than only where an orphan is suspected, and scoping reachability to "the accounts in play"
means the plan needs to know which accounts those ARE - which sync currently does not, since it
knows only a source and a destination. A `--scope` argument, or treating the source/destination
pair as the scope, both change what the orphan check means; neither is obviously right.

**Why it is recorded rather than fixed.** All three were found in one session by a user reading
plans carefully and pushing back when a claim did not match what he was looking at. That is
evidence about the report's wording as much as its arithmetic, and the wording is what stops a
careless reader from losing something. Worth the panel.

## FIXED in 0.9.13 — the orphan check was blind to the destination's OWN other rows

**Recorded as a defect on 2026-08-22 and fixed the same evening. Kept in full because the
shape of the fix is the interesting part, and because the failure it caused is the one a
future change here would most easily reintroduce.**

**What shipped.** `_other_pointers(env, dest_path, doomed_rows=())` now reads EVERY store,
including the destination, and excludes exactly the destination rows this plan will overwrite —
by name, per row. `plan_sync` passes `{r["name"] for r in rows if r.get("is_update")}`: every
row being overwritten, not only the swapping ones, because a plain refresh also replaces its
row wholesale and so stops vouching for whatever it pointed at. Covered by
`tools/check_report_fixes.py`, which asserts the voucher count directly as rows are doomed
(2 → 1 → 0) rather than only through a plan, so a future regression cannot pass for the wrong
reason. The `unreadable_store` sentinel is asserted to still yield `"unknown"`, never `False`.

The original record follows.

`_other_pointers(env, dest_path)` builds its reachability set from *"every store EXCEPT
dest_path"*:

```
    real_dest = os.path.realpath(dest_path)
    for acct, org, path in _account_dirs(env):
        if os.path.realpath(path) == real_dest:
            continue
```

`plan_sync` then asks `if sid in pointers` to decide `displaced_orphan`. So when a swap displaces
a conversation that is still held by **another row in the destination account itself**, the plan
reports it as about to become unreachable. It is not.

**How it was hit.** A conversation lived on exactly one account. To make a sync of its row safe,
a redundant `(fork)` row in that SAME account was repointed at it first - deliberately, as the
cheapest way to keep a second door open. The sync then still demanded `--allow-orphan`, because
the door it had just been given was inside the store the check skips. Verified on disk: the
conversation was held by two rows in the destination, and the plan called it orphaned.

**Why the exclusion exists, and why it is the wrong shape.** The question the check serves is
"this row is about to stop pointing at the conversation - does anything else still point at
it?", so the row being changed must not vote for itself. Excluding the whole destination STORE
is one way to guarantee that, and it is too coarse.

**The fix is narrower AND wider than either.** Exclude **every row this plan will overwrite** -
by `(store, row id)`, not by store:

- Narrower than today: other rows in the destination account, untouched by this plan, SHOULD
  count. That is the false positive above.
- Wider than "just this row": a conversation held only by some OTHER row that this same plan
  also overwrites is genuinely about to become unreachable, and a per-row exclusion alone would
  call it safe. Today's store-wide exclusion happens to get that case right, and any fix must
  keep getting it right.

**Severity.** It fails in the safe direction - false alarms, never false reassurance - which is
why it survived to 0.9.12. But a false alarm is not free: it is indistinguishable from a real
one at the point of decision, and the only way past it is to tick `--allow-orphan` / "allow
hiding a conversation". Training the user to tick that box is the specific harm, because that
box is the last thing standing between a real orphaning and an unrecoverable one.

**Note for whoever fixes this.** `unreadable_store` (the `None` sentinel that makes an
unreadable sibling count as "cannot rule it out") must survive the change - the fail-closed
posture is separate from, and more important than, this fix.

## The 0.9.13 review panel — what it changed, and what it left open

**Codex, Gemini (agy) and DeepSeek (repo-aware) reviewed 0.9.13 before it shipped. Kimi was not
run. Three findings changed the code; several more are recorded here unfixed.**

### Changed before shipping

- **`"Continue from where you left off."` was removed from `TRANSCRIPT_PLUMBING_PREFIXES`.** All
  three engines raised it independently, and the draft's own comment had already flagged the
  doubt: the string arrives as a USER-role turn and nothing distinguishes it from a person
  typing those words. Codex went further and killed the accompanying claim that over-filtering
  is safe — "degrades to NOT MEASURED, never a wrong percentage" is only true of short
  transcripts. On a long one, dropping an authored turn removes it from BOTH sides and can lift
  the overlap to a **false 100%**, which is precisely the overstatement `_overlap_clause` exists
  to prevent. `tools/check_report_fixes.py` now pins that string as NOT filtered, and asserts
  the false-1.0 case directly.
- **The window was not showing the length at all.** 0.9.13 added `_length_clause` to
  `_print_sync_report` only; `claude_code_sessions_gui.py` still rendered `_overlap_clause`
  alone at both sites. Caught by Codex. That is the surface where the "allow hiding a
  conversation" checkbox lives, so it is the one that most needed the number. Now rendered at
  both sites, with a structural test asserting the GUI never renders fewer length clauses than
  overlap clauses — the exact regression of adding a clause to one surface and not the other.
- **"stays reachable from another account" became false the moment Fix 2 shipped.** Reachability
  is per ROW now, so the voucher can be a different row in the SAME destination account, and
  the old wording sent the reader to the wrong sidebar. Codex caught it. Now "another surviving
  row still opens it", and the orphan case says "unreachable from every sidebar" rather than
  "from every account" — accounts are the wrong unit in both directions.
- **An unmeasurable length printed nothing.** Gemini's point: a reader trained to look for
  "FEWER" reads silence as "no loss". Both surfaces now say "turn counts unknown - length
  change could not be measured" instead.

### Raised, not fixed — recorded so they are known rather than rediscovered

- **Reachability was plan-time, never revalidated at apply time (Codex). FIXED in 0.9.15.**
  `_sync_recheck_reachability` re-runs the orphan question under the lock `execute_sync_op`
  already holds, once per invocation, before that invocation writes anything - and refuses with
  a message naming reachability rather than drift, stating that nothing was written, and
  offering `--allow-orphan` as the deliberate route. A resumed op gets it too, since
  `recover --forward` sets the status back to `journaled` and re-enters through the same door;
  rows already written are skipped, and the refusal says how many an earlier run landed rather
  than claiming nothing was written.

  **`--allow-orphan` is consent to the orphans the plan NAMED, not a blanket licence.** The first
  version returned early on the flag, and both review engines rejected that independently: at
  plan time the flag means "hide these conversations, the ones the report just listed", so
  honouring it at apply time for a conversation that became hideable *afterwards* lets through
  an orphan the user never saw - and makes the plan phase decorative. No separate approval list
  is needed, because the manifest already records it per row: a row survives planning with
  `displaced_orphan` True or `"unknown"` only when the flag was passed, so that field IS the
  record of what was shown and accepted. Rows planned `False` rested on a voucher, and those are
  the ones re-checked. When one of them has lost its voucher the refusal points at a re-plan
  rather than at the flag, because the flag is the wrong answer to "an orphan nobody reviewed".

  Residual: this narrows the window from "between planning and applying" - minutes to overnight,
  and where the app does its repointing - to "between the check and the writes".

  **Correction, 2026-08-22.** The first version of this entry said closing the remaining window
  needed "an apply-time serialization boundary spanning the guard, the check, every write and the
  journal update", because both review engines named exactly that as the highest-impact fix. That
  boundary already existed - see the section below. Both engines were reasoning from a review
  document that never mentioned locking, so they assumed the lock was per-op; the omission was
  mine, and the gap was recorded on their inference rather than on the code. Against another copy
  of THIS tool the window is not merely narrow, it is closed. What remains open is the desktop
  app and hand edits, which no cooperative lock can bind.
- **`doomed` is computed before orphaning swaps are removed from the plan (Codex, DeepSeek).**
  Two swaps that are each other's only remaining door are both marked doomed, both classified
  orphaning, and both held - so both survive and either could have vouched. Deterministic
  over-conservatism: a false `--allow-orphan` demand, which is the same class of harm as the
  false alarm this release fixed. A fixed-point pass over the executable subset would settle it.
- **The overlap is set membership, so multiplicity and order are ignored (Codex).** One incoming
  occurrence can mark any number of repeated displaced turns as preserved, and two long turns
  sharing a 400-character prefix hash identically. `Counter` intersection is the cheap
  improvement; an order-aware comparison is the real one. Long-standing, not new here.
- **Doomed-row matching is case-sensitive (Gemini).** `name in doomed` compares filenames from
  `os.listdir` against names carried on the row. On a case-insensitive filesystem a casing
  difference would silently fail to exclude a doomed row, letting it vouch for a conversation it
  is about to stop opening. Not observed - the names come from the same directory listing today
  - but it is one refactor away from being real.
- **`_displaced_sizes` re-parses rather than reusing the fingerprints (Codex, DeepSeek).** The
  docstring's "costs nothing extra" was wrong and has been corrected in place: the page cache
  spares the disk I/O, not the JSON parse, normalisation and hashing. Bounded by
  TRANSCRIPT_COMPARE_MAX_ROWS. One pass computing both numbers would be strictly better.
- **Error paths around `_displaced_sizes` are untested (DeepSeek).** A missing transcript, an
  unreadable one, or a non-string `text` block should degrade to `(None, None)`; nothing pins
  that today, so a change letting an exception escape would take `plan_sync` down with it.

## Publishing — the PyPI index lag, and the pin that routes around it

*(observed across 8 releases to 2026-08-22; PyPI behaviour, not this tool's)*

**`pipx upgrade` can report "already at latest version" minutes after a successful upload.** Hit
on 6 of the 8 releases so far, which makes it the normal case rather than an incident. It is
index propagation, not a failed publish - `twine upload` has already returned success and the
files are on the server.

**The two surfaces disagree, and they disagree in a direction that is useful.** Measured for
0.9.14 at 2026-08-22 21:28, minutes after upload:

| Surface | Consumer | Had the new version? |
|---|---|---|
| `https://pypi.org/simple/claude-code-sessions/` | what pip *resolves* from | **yes** |
| `https://pypi.org/pypi/claude-code-sessions/json` | what pipx checks for "is there a newer one?" | no - still the previous release |

So `pipx upgrade` was asking a stale oracle about a package that was already installable.

**The workaround is to pin the version, which goes through the fresh surface:**

```
pipx install --force "claude-code-sessions==<new version>"
```

Verified 2026-08-22: this installed 0.9.14 immediately, in the same minute `pipx upgrade`
insisted 0.9.13 was current.

**pipx also caches, independently of both.** Measured on 0.9.15, 2026-08-22 21:50: BOTH PyPI
surfaces were serving it - JSON API and simple index - and `pipx upgrade` still reported
"already at latest version 0.9.14". So the pin is not merely a workaround for a stale JSON API;
it is the reliable route regardless of which layer is behind. Reach for it first and skip the
diagnosis unless it fails.

**Check which surface is actually stale before waiting.** Earlier releases were handled by
retrying a few minutes later, on the assumption that everything was lagging together. That is
sometimes true and was not true here - and when only the JSON API is behind, waiting costs time
for nothing. Two commands settle it - and note they say nothing about pipx's own cache, which
is why the pin comes first:

```
curl -s https://pypi.org/pypi/claude-code-sessions/json | python -c "import json,sys; print(json.load(sys.stdin)['info']['version'])"
curl -s https://pypi.org/simple/claude-code-sessions/ | grep -o "claude_code_sessions-[0-9.]*" | sort -u | tail -3
```

If the simple index has it, pin and install. If neither does, the upload has genuinely not
landed yet and waiting is the only option.

**Not a reason to re-upload.** A second `twine upload` of the same version is rejected as a
duplicate, and a bumped version to "force" propagation burns a release number on an index
delay.

## The 0.9.15 review panel — the consent question both engines asked

**Codex and Gemini (agy) reviewed 0.9.15 before it shipped. The roster was not run.** One finding
changed the semantics; the rest are recorded here.

### Changed before shipping

- **`--allow-orphan` was being treated as blanket consent at apply time.** Both engines raised it
  independently and framed it the same way: at plan time the flag approves the specific orphans
  the report named, so honouring it later for a conversation that became orphanable *after* the
  user read the plan approves something they never saw. Fixed as described above - only rows the
  plan actually recorded as orphaning are exempt, and a newly-orphaning row refuses even with the
  flag set, pointing at a re-plan rather than at the flag.
- **The refusal claimed "Nothing has been written" on a resumed op** (Codex), where an earlier
  invocation had already landed rows. It now names how many, so nobody goes looking for an
  untouched destination that does not exist.
- **The residual was overstated.** The docstring said the check covers "another copy of this tool,
  or a hand edit, in the same seconds". Codex: it covers edits *completed before* the check, not
  concurrent ones. Corrected in place.

### The one place the engines disagreed

Gemini's headline fix was to move the check **inside** the row loop, re-verifying immediately
before each write, to shrink the race window. Codex looked at the same trade and concluded the
opposite: per-row "merely narrows the race while increasing partial-application risk; it does not
establish correctness". Kept the up-front check on Codex's reasoning - a fresh op that refuses
before writing anything preserves the reviewed plan, whereas a mid-loop refusal leaves a
partially-applied plan nobody reviewed. Recorded because the disagreement is real and a future
change here should know both arguments existed rather than rediscovering one of them.

### Raised, not fixed

- **"A stalled op can deadlock"** (Gemini high, Codex medium): rows written, then a voucher
  vanishes, and the re-check refuses the remainder - "permanently stuck in the exact
  partially-applied state the up-front check was designed to avoid". **Reproduced 2026-08-22 and
  it is not stuck.** Three routes were open the whole time, and section G of
  `tools/check_report_fixes.py` is the executable form of that investigation:

  | Route | Result |
  |---|---|
  | `recover --forward` | refused - correct, it would orphan a conversation nobody reviewed |
  | `recover --back` | **succeeds**, reverses what landed and closes the op |
  | re-plan and apply | **succeeds**, and correctly HOLDS the swap back under `held_orphan` |

  The third is the one that settles it: the fresh plan runs the orphan guard against the store as
  it now is, so the row that would orphan is held rather than written, and the guard needs no
  special case for "a previous op stalled here".

  **What WAS wrong is narrower: the refusal named no exit.** It said "re-run to re-plan", which
  works, while saying nothing about the open op still sitting in the journal for `doctor` to flag,
  and nothing about `--back`. Fixed - the message now branches on whether rows have already
  landed, and the resumed form names the op id and both routes verbatim.

  Worth recording as a pattern rather than a one-off: this is the second 0.9.15 finding that
  shrank on investigation, after the serialization boundary that already existed. Both were
  reasoned from a review document rather than from the code, which is the documented limit of
  reviewing a summary - and the reason the repo-aware roster route exists at all.
- **An unreadable sibling store aborted an op that already passed planning** (both). **FIXED
  2026-08-22.** `_listdir_retrying` gives every store read `STORE_READ_ATTEMPTS` (3) tries with a
  linear `STORE_READ_BACKOFF` (0.25s, so 0.75s total) before the OSError propagates and
  `_other_pointers` records its "cannot rule it out" sentinel exactly as before. Less brittle,
  never less strict: a store still unreadable a second later is not blinking, it is unavailable.

  Every OSError is retried rather than a curated "transient" subset, deliberately. Deciding which
  errno means "will never work" is not portable - a Windows network drive can surface a momentary
  outage as ENOENT, EACCES or a WinError with no stable mapping - and being wrong in the retry
  direction costs about a second on a failure that was going to be reported anyway, while being
  wrong in the other direction refuses an operation over a blink.

  Pinned in `tools/check_report_fixes.py` section F, which asserts both halves: a store that fails
  once and then succeeds does NOT reach the sentinel, and a store that always fails still yields
  `"unknown"` after exactly `STORE_READ_ATTEMPTS` tries and no more.
- **The pointer scan is not a consistent snapshot** (Codex): `_other_pointers` walks several
  stores, and a concurrent change during the walk yields a mixed-time answer. **Narrower than
  filed** - see the correction above and the lock section below. Against another copy of this
  tool the walk is already serialized, so the only writer that can produce a mixed-time answer is
  the desktop app or a hand edit, which is RULING 4's territory rather than a scan-atomicity
  problem.
- **Untested paths** (Codex): a crash between a row write and its `written` marker; drift that
  changes a row's swap classification between plan and apply; a voucher *repointed* rather than
  deleted; two applies interleaved between check and write. **All four now covered by
  `tools/check_apply_edges.py` (2026-08-22), and all four already behaved correctly** - the gap
  was coverage, not conduct:

  | Path | What the test found |
  |---|---|
  | crash between write and marker | recovery is idempotent - `current == post` marks it written rather than re-writing, so the row is byte-identical afterwards |
  | drift changing swap classification | the write loop's own `current != pre` drift refusal catches it before the reachability question arises; the destination keeps what it had |
  | voucher repointed, not deleted | caught - the check asks what a row POINTS AT, not whether the file exists, so a repointed voucher is a lost voucher |
  | two applies interleaved | the second refuses on the lock, **attempted from inside the first one's write loop** |

  That last test is the strongest evidence for the lock in the suite. `check_serialization.py`
  watches whether the lock file exists during a write; this one re-enters `run_sync` from within
  the write loop and shows the second call is refused - the interleaving itself, not a proxy
  for it.

## The operation lock — the serialization boundary, and exactly what it binds

*(the code is `_lock_path`, `acquire_lock`, `release_lock`; pinned by
`tools/check_serialization.py`)*

**One lock file per journal directory, not one per operation.** `_lock_path(env)` is
`<ops_dir>/lock`, a fixed name, and `acquire_lock` creates it with `O_CREAT | O_EXCL` - the
atomic "create only if absent" primitive. The `op_id` written inside is for diagnostics, so a
refusal can name the holder; it plays no part in exclusion. Every mutating entry point competes
for that same file: `run_move`, `run_undo`, `recover_op`, `run_sync`, `undo_sync`, `run_repoint`,
`undo_repoint`, `run_new_row` and `undo_new_row`. All nine release it in a `finally`.

**What is inside the boundary for a sync.** `run_sync` acquires before `new_op`, and releases
after `execute_sync_op` returns. So the running-app guard, the journal write, the apply-time
reachability re-check and every row write happen while it is held. `tools/check_serialization.py`
asserts the strong form rather than the shape: it wraps `atomic_write` and records whether the
lock file existed at the moment of each row write. Every write, every time.

**What it binds, and what it cannot.**

- **Another copy of this tool: bound.** A second `--apply` refuses immediately, naming the pid
  and op holding it. This is the case a review panel named as the open gap in 0.9.15 and it was
  already closed - see the correction above.
- **The desktop app: NOT bound, and cannot be.** The app knows nothing about this file and would
  not honour it if it did. RULING 4's running-app guard is the control there, and it is a
  point-in-time process check with a documented residual window - a different mechanism for a
  different adversary, not a weaker version of this one.
- **A hand edit: not bound.** Same reason.

Naming that distinction matters more than it looks. "Is the apply serialized?" has two different
answers depending on who is asking, and answering it with one word is how a closed gap gets
recorded as an open one.

**A stale lock is recoverable, not fatal.** `lock_is_stale` probes the recorded pid; `recover`
clears a lock whose holder is gone. The refusal text points there, so a machine that lost power
mid-sync does not need the file deleted by hand.

**Not documented before 2026-08-22, and not tested at all.** That is how a real guarantee ended
up written down as a missing one. The test file exists to make the property checkable rather than
inferable from a call-site reading.

## Creating a listing row

*(the code is `_transcript_facts`, `NEW_ROW_DEFAULTS`, `_synthesize_row`; pinned by
`tools/check_new_row.py`)*

**The store has no index.** `load_rows` finds every row by globbing `local_*.json` across each
account/org folder — the same enumeration `list`, `doctor`, and everything else in this tool
does, and the whole reason a synthesized row is visible at all: nothing consults a registry
that would need to know a given row was built by this tool rather than issued by the app.

**The row template comes from a census, not a design meeting.** Re-measured 2026-08-23 across
988 real rows on this machine: 52 distinct keys exist, and only 12 appear on every one of them,
so there is no fixed row shape to copy. `NEW_ROW_DEFAULTS` follows a three-tier policy instead —
transcript-derived first, then a field at ≥95% presence *with a defensible zero value*, then
omission. Clearing the threshold is necessary and not sufficient: `classifierSummaryEnabled`
sits at 97.6% (964 of 988) but is `True` on every row that carries it, which is behaviour
asserted rather than a zero, so it is omitted regardless of the number. An earlier draft
asserted `reportFindingsCard`, `chromeTabGroupId`, `lastSpawnRootDetected` and
`remoteControlAutoEligible` on every synthesized row; measured presence on 2026-08-23 is 60.2%,
14.6%, 6.9% and **2.3%**. Writing a field that 97.7% of real rows do not carry is exactly the
"plausible-looking default" the policy exists to forbid.

**Those percentages are a snapshot of a moving target — which is the argument for the policy,
not a caveat on it.** The first three of those four were 60.2%, 5.6%, 2.7% and 0.9% when the
census first ran on 2026-08-22. One day later they were 60.2%, 14.6%, 6.9% and 2.3%:
`chromeTabGroupId` had nearly tripled, `lastSpawnRootDetected` had more than doubled, and
`remoteControlAutoEligible` had more than doubled. Every row carrying either of the last two
had been written in the preceding two days (oldest file mtime 2026-08-21 for both, measured on
the same run), so the app is rolling these fields onto rows as it touches them rather than
having shipped them retroactively. A field the app is mid-rollout on is exactly what a
synthesized row must not assert: whatever value it would write is behaviour the app has not
finished deciding, not a zero — and the percentage that justified writing it will be wrong by
the time anyone reads this paragraph. So the question to ask of a candidate field is not "is it
above 95%" but "is it stable"; re-run the census in the plan and compare against the numbers
here before touching `NEW_ROW_DEFAULTS`.

**`model` and `effort` are read from the transcript, never defaulted.** An earlier draft
hardcoded `"model": "claude-opus-5"` as the account default; the same census says otherwise —
`claude-fable-5` leads `claude-opus-5` 522 to 244 across the 988 rows. `_transcript_facts` keeps
the **last** `message.model` and top-level `effort` it sees, because what the session was
running when it stopped is what a resumed row should carry — the opposite of `cwd`, which is
first-wins because `originCwd` should name where the session began, not wherever it was when it
stopped. A transcript with no assistant reply has no `model` to read and no zero value to fall
back to (`model` sits at 100% of the 988 rows), so `_transcript_facts` refuses rather than
invent one; if that ever costs someone a real conversation, the fix is a `--model` flag, not a
silent default.

**`lastFocusedAt` is seeded, then overwritten by the app.** It is set to the transcript's last
activity at creation time rather than omitted, because the 2026-08-22 prototype showed the app
rewriting the field the first time the row is focused — so seeding it is transient either way,
and omitting it risks the app sorting a fresh row to the bottom of the sidebar, which reads as
the command having failed.

**`doctor`'s detection of a rejected row is bounded by journal retention, not by time.**
`rotate_ops` keeps the ten most recent *terminal* ops across every operation type, and
`_collides` is what holds an op back from that rotation. It shields two things: an unresolved
`sync`'s claim on a destination row, and any op carrying a `rollback_residue` — which a
rollback of any operation type can now carry (see "Amendment 2026-10-19 (journal)"), and
which is pinned indefinitely so `doctor` can go on reporting the rows that rollback could not
remove (`gather_doctor`'s `rollback_residue` list; the alert clears when every row it names is
actually gone, because it asks the store rather than the journal, and a row in a store this
version does not read is listed apart rather than counted). A **completed** `new-row` op — the only kind `vanished_new_rows` reads — is never
shielded. So the window that check can see is however many other operations happen to run
next, not a number of days. Measured
2026-08-23: after 15 sequential `new-row` runs, the first run's op had aged out of the journal,
its vanished-row alert was gone, and `doctor` returned to exit 0 — with nothing about the row
itself having changed to explain the alert clearing. In practice this covers the case the
command was built for, since an app version that rejects a synthesized row does so the first
time it opens the sidebar, before ten more operations of any kind have a chance to run. A busy
stretch of other `ccs` usage between the rejection and the next `doctor` run is the failure
mode, and closing it needs a standalone registry with its own retention, not a bigger number
here — recorded as an open limit in the README and in the plan's self-review.

**The app accepting a row it did not issue rests on one experiment, not documentation.** There
is no official word on what the sidebar will and won't accept; a row created by hand on
2026-08-22, opened successfully by the app, is the entire evidentiary basis for this command
existing. Nothing in this file or in `tools/check_new_row.py` proves that holds across app
versions — the `doctor` check above is the mitigation for that, not a substitute for it.
