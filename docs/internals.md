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
| Transcript (`.jsonl`) | **One genuinely shared file.** Carries no account identifier at all. | Any account whose listing points at it reads and writes the same bytes. Continuing a session from one login and opening it from another login shows the new content immediately. |
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
- The decisive test: session "LinkedIn project structure", whose row read **28 Jun** in two
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
local_a00afbc1-….json   in craig@foundryside.co    -> 0678aca4  17.9 MB, 1386 msgs, last used 19 Aug
local_a00afbc1-….json   in claude@craigstoller.com -> c92ae7b1  13.0 MB,  738 msgs, last used 14 Aug
```

Both are titled "KRIS-REVIEW session Pacific Sound". Opening it under the second account does
not show a truncated version of the first — it shows **a different conversation**, complete and
current as of its own last use. To a user that is indistinguishable from "my history is behind",
and it is the far more serious failure: the newer conversation is intact on disk but
**unreachable from that account's sidebar**. On this machine, **15 of 333 shared rows** were in
that state, with the newer conversation on the foundryside side for 9 and the other side for 6.

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
| `d0fcaa6f/ef430bfb` — account with **its own** org | 266 |
| `dd44e101/53346e14` — account with **its own** org | 315 |
| `d0fcaa6f/53346e14` — **cross pair** | 0 |
| `dd44e101/ef430bfb` — **cross pair** | 0 |

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
