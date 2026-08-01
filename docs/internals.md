# Internals: how Claude Code / Claude Desktop store threads

This is background for anyone extending or auditing `claude-code-threads`, not a user guide. It
describes the on-disk layout the tool depends on and refuses to guess about.

> Every claim below was **observed July 2026, app channel: Claude Desktop (Windows); format may
> change.** This is a reverse-engineered format, not a documented one — treat all of it as
> subject to revision by a future app release, and prefer deriving facts from the disk over
> hard-coding them (see `doctor` and the encoding-scheme detection below).
>
> Last confirmed end to end against a live store on **2026-07-31, Claude Desktop (Windows)
> app 2.1.219**: a thread was relocated between projects, resumed by the app at its new
> location, and an interrupted move was recovered in both directions.

## Where things live

*(observed July 2026, Claude Desktop (Windows); format may change)*

**Transcript** — the actual conversation:

```
C:\Users\<you>\.claude\projects\<encoded-cwd>\<cliSessionId>.jsonl
```

Some threads also have a folder of the same name beside the `.jsonl`, holding subagent
transcripts. It travels with the file.

**Listing metadata** — one JSON per thread, holding its title, `cwd`, timestamps, model, and
the `cliSessionId` pointing at the transcript above:

```
C:\Users\<you>\AppData\Roaming\Claude\claude-code-sessions\<account-id>\<org-id>\local_<appSessionId>.json
```

Also in that folder, and **not** thread metadata: `scheduled-tasks.json`, `deleted_<id>`
markers for removed threads, and transient `*.json.tmp` files. Any bulk operation should glob
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
returns zero rows, which is indistinguishable from "this thread has no listing row" unless the
absence is checked for explicitly.

Note that `~/.claude/projects` is **not** redirected — it is an ordinary profile directory that
both contexts see identically. Only the `%APPDATA%` half of the two-layer model below is
affected.

## The two-layer model

*(observed July 2026, Claude Desktop (Windows); format may change)*

This is the part that is easy to get wrong, because the two layers behave differently.

| Layer | Shared or copied? | Consequence |
|-------|-------------------|-------------|
| Transcript (`.jsonl`) | **One genuinely shared file.** Carries no account identifier at all. | Any account whose listing points at it reads and writes the same bytes. Continuing a thread from one login and opening it from another login shows the new content immediately. |
| Listing (`local_*.json`) | **Per-account copy.** | Which threads appear, plus title/timestamp/sort order, is private to each login and frozen at copy time. |

So relocating or copying a thread's listing entry is a **snapshot**, not a live sync of the
conversation — a resumed thread's row goes stale elsewhere until re-copied. The conversation
content itself was never copied; only the pointer to it was.

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
> look, and the thread vanishes from the sidebar. `claude-code-threads` does exactly this evidence
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

Deleting a thread in the app writes a file named `deleted_<cliSessionId>` beside the listing
rows, inside `<accountUuid>/<organizationUuid>/`. It is 13 bytes: an epoch-millisecond
timestamp of the deletion. The listing row is **removed outright**, not blanked in place, and
the **transcript is left on disk**.

Two consequences that are easy to get wrong:

- **They are per-account.** A deletion under one account says nothing about what another
  account should see — a tombstone lives inside the same per-account folder as the rows it's
  paired with, never anywhere shared.
- **The app writes them but does not honour them.** Deleting a disposable thread, then
  restoring its backed-up listing row while leaving the tombstone in place, then relaunching the
  app, showed the thread in the sidebar again. The tombstone survived the launch with its
  contents unchanged — nothing consumed or pruned it.

So any tool that copies listing rows between accounts must consult the *destination's*
tombstones itself; nothing in the app stops a copy from resurrecting a thread the user
deliberately deleted. `sync` (see the README) reads the destination's tombstones and skips any
source thread they cover for exactly this reason. As far as we found, this is undocumented
elsewhere: a review of seven other Claude session-copying tools' source, READMEs, and docs
turned up zero mentions of `deleted_*` files, "tombstone," or soft-deletion of any kind.

## `sync`'s journal, and how `recover`/`undo` treat it

*(describes this tool's own design, not a reverse-engineered fact about the app)*

A `sync` operation reuses `move`'s journal, single-instance lock, and rotation — the same
`ops/<op-id>/manifest.json`, the same `doctor`/`recover` bookkeeping — but moves through a much
shorter phase sequence, because nothing is ever deleted and no transcript moves:

```
journaled -> writing -> completed
```

Each row is written and marked `written: true` in the manifest immediately after its
`atomic_write`, one row at a time, so a crash mid-run leaves an exact record of which rows
landed and which didn't. A run that finds nothing to copy never creates an operation at all —
there is nothing to journal.

**Every row is re-read immediately before it would be written.** Absent → write it. Present and
already byte-identical to what `sync` would write → leave it alone and mark the row done anyway;
this is what makes re-running, or resuming a crashed run, safe. Present with *different* bytes →
refuse, because the destination account has touched that row since the sync was planned (opened
the thread, for instance) and overwriting it would discard that. The refusal leaves the op at
`writing`, not a terminal status.

**`sync` also re-confirms, at the moment it writes, that the destination is still the dormant
account** — the same check that chose it at plan time (`resolve_sync_endpoints`), run again
against `live_account()` at execute time. This is *why* `sync` can ship with no running-app
guard at all, unlike `move`/`undo`/`recover`: those refuse in the presence of a running Claude
process because they might be racing it, but `sync` never touches the account a running process
would be using, and re-verifies that fact fresh on every write rather than trusting a plan made
moments — or days — earlier. The same logic is why `sync` refuses outright if it cannot identify
the signed-in account at all, from neither `~/.claude.json` nor `config.json`: with no live
account confirmed, there is nothing to check the named destination *against*. `--to` only
narrows which dormant store to use among several; it cannot supply that missing certainty, and
does not attempt to.

**A stuck sync is recoverable, but not by rolling forward forever.** If a destination row drifts
while a sync is non-terminal, `writing` can never complete — `sync` refuses on that same row
every time it re-enters, per the re-read rule above. `doctor` flags the op as nonterminal, and
running `recover` (without `--id`) lists it offering `back` instead of `forward`, naming the row
that's blocking it. `recover --back` removes every row this op can verify it wrote — present,
and still byte-identical to what was written — and **skips**, rather than deletes, any row it
wrote that has since drifted or gone unreadable, reporting what it left behind. It always ends
at `rolled_back`, because `back` is the only exit from a stuck op and must terminate rather than
recreate the same dead end.

This is a deliberate asymmetry with `undo` of a *completed* sync: `undo` refuses entirely,
touching nothing, if even one row it wrote has drifted or gone unreadable. A completed operation
being reversed at the user's explicit request is a case where a surprise should mean stop and
ask, not best-effort cleanup — the opposite of what a stuck, still-in-flight op needs.
