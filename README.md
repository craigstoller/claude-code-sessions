# claude-code-sessions

See your Claude Code sessions under your other Claude account on the same machine — and move
sessions between project folders. Journaled, reversible, fails closed.

> **Unofficial.** Not affiliated with Anthropic. Reverse-engineered on-disk formats; fails
> closed when it sees anything it doesn't recognize.
>
> **Not to be confused with** `claude-code-sessions\` — the folder *Claude Desktop* keeps your
> per-account session list in, which this tool reads. This tool's own bookkeeping lives in
> `~/.claude-code-journal/` and is safe to delete.

**Companion read:** [The session that synced itself](https://github.com/craigstoller/claude-code-sessions/blob/main/docs/the-session-that-synced-itself.md)
— what Claude Desktop actually keeps on disk, and why this tool is built out of refusals.

## The problem

**Claude Desktop files its session list per account.** Switch logins and the sessions you
started under the other one disappear from the sidebar. They are not gone: the conversation
itself is a single shared file carrying no account identity at all, sitting untouched on your
disk. Only the *listing row* — the sidebar entry pointing at it — is private to each login, and
the app reads only the folder belonging to the account you are signed into right now. `sync`
copies that row into your other account's store, so the session shows up there too.

**Separately, every session is filed under the directory it was started in**, and there is no
official way to move one afterwards — a session begun in the wrong folder is stuck there.
`move` relocates the transcript and every listing row that points at it, verifying each side
before touching the other, so the session reopens cleanly in its new home.

## Install

```
pipx install claude-code-sessions
```

This installs three commands: `claude-code-sessions` and `ccs` (identical — the short one
is for everyday use; examples below use the long form), plus `ccs-gui`, a window for
people who would rather not use a terminal. Examples below are the CLI; see
[A window instead of the CLI](#a-window-instead-of-the-cli).

Or download `claude_code_sessions.py` and run it directly — the runtime has no dependencies beyond
the Python 3.9+ standard library:

```
python claude_code_sessions.py --help
```

## A window instead of the CLI

```
ccs-gui                     open it
ccs-gui --install-shortcut  add a Desktop + Start Menu shortcut (Windows)
```

It plans a sync, shows which account copies into which, the destination store and what
would be copied — and **writes nothing until you press Apply**. It also carries Undo, a
title filter (`sync --only`), and a health check (`doctor`, findings first).

It is a thin shell over the same planning and execution the CLI uses, not a
reimplementation, so every guard, refusal, and journal entry behaves identically — and a
refusal is shown **verbatim** rather than summarised into something friendlier. Two things
it deliberately does not offer, because the friction is the safeguard: `--verbatim` (which
copies permission grants across an account boundary) and `--include-deleted` (which brings
back a session you deleted). `move` is CLI-only for now.

Registered as a GUI script, so nothing flashes a console window. It needs `tkinter` — part
of the standard library, but packaged separately on some Linux distributions
(`apt install python3-tk`).

## Optional: let Chrome stay open

The desktop app ships a helper for its Chrome extension that can outlive the app, so the
guard counts it and you have to close Chrome too. A measured build of that helper is
excluded — but **the helper auto-updates every few days** (three builds in eight days, as
measured), and each update correctly fails closed, so the exclusion lapses and Chrome has to
be closed again until someone re-runs the ~12-minute measurement in
`tools/native-host-measurement/`.

If that trade isn't worth it to you, there is an opt-in: trust **any** helper at the app's
own package path that Windows reports as validly signed by `Anthropic, PBC`.

```
claude-code-sessions trust-signed-helper          # show the current state
claude-code-sessions trust-signed-helper --on     # enable it
claude-code-sessions trust-signed-helper --off    # back to the default
```

In the window it is the **"Let Chrome stay open"** checkbox, which explains the trade before
enabling anything. Either way it is one marker file — `~/.claude-code-journal/trust-signed-helper`
— so deleting that file also revokes it.

It is off by default deliberately. It is weaker than the default — trust moves from "these
exact measured bytes" to "this publisher", so a future Anthropic build that started touching
the store would be excused without being measured. That risk is small (the measurement found
a message bridge that never touched the store) but it is real, and it rests on evidence you
have not personally examined, which is why nothing enables it for you. An unsigned, tampered,
differently-signed, or out-of-path binary still counts, as does one whose bytes can't be
read. See `docs/internals.md`, RULING 7.

## Before any move: close the Claude app.

`move`, `undo`, `recover`, `repoint --apply`, `new-row --apply`, and every mutating `sync`
route (`--apply`, `undo` of a completed sync, `recover --back` on a stuck one) all refuse
while they can see the Claude desktop app running — whoever the identity files currently say
is signed in. That includes `undo` of a completed `repoint` or `new-row`, and `recover` on an
interrupted one wherever it would write or delete a row rather than only close the record. Closing the app first avoids
the refusal and guarantees nothing is actively appending to the sidebar rows you're about to
touch. A running Claude Code CLI session does **not** count: it's recognised by its own
install path and excluded from the check, so an open `claude` session never blocks any of this.
(For `move` specifically, a running CLI session *can* still be writing the transcript being
relocated — the transcript-freshness check and the last-instant content re-verification cover
that case; see Safety design.)
If `~/.claude.json` (the CLI's identity file) and the desktop app's `config.json` disagree
about which account is signed in, `sync` refuses to even plan — re-authenticate the CLI (run
`claude`, then `/login`) as the account you use, or switch the desktop app to it, so the two
files agree — or, if you know which account the desktop app is signed into, assert it with
`sync --live <account>` (see the `sync` section below; the running-app guard still applies in
full). Signing into the desktop app does **not** refresh `~/.claude.json`. A refused
`sync --apply` (desktop app running) refuses **before anything is journaled** — no lock file,
no op directory — so there's nothing left behind for `doctor` to flag or `recover` to clean up;
just close the app and re-run. One measured wrinkle: the desktop ships a helper for its Chrome
extension (`chrome-native-host.exe`) that can outlive the app itself, and the guard counts it —
if a refusal names that process, fully exit Chrome too (the refusal always names what it saw).

## Usage

Ten commands. All mutating commands default to a dry run; add `--apply` to execute.
(`trust-signed-helper` is the opt-in described above; it changes a setting, not your
sessions.)

`--version` prints the release **and the file it ran from** — worth knowing when a pipx
install and a source checkout are both on `PATH`, which is exactly when a version number
alone cannot tell you which copy answered:

```
$ ccs --version
claude-code-sessions 0.10.0
running from: C:\Users\craig\AppData\Local\pipx\...\claude_code_sessions.py
```

**`list`** — inventory sessions, optionally filtered by a search term:

```
claude-code-sessions list gate
```

**`doctor`** — read-only health report (stale locks, unresolved operations, orphaned rows,
encoding-scheme ambiguity, a synthesized row that has since vanished, and **conversations no
account's sidebar points at**):

```
claude-code-sessions doctor
```

That last one needs a word, because most of what it finds is normal: a session started from
the CLI never had a listing row, and deleting a session in the app leaves its transcript
behind. What is *not* normal is a **recent, large** one — so they are ranked newest-first and
only the top few are printed, with the total alongside (the full list is in `--json`).
Resuming a session while signed into a different account repoints its row, which can leave the
conversation you were in an hour ago reachable from no sidebar at all. That is what this
ranking is for, and `repoint` is how you put it back.

**`alignment`** — read-only scoreboard: how close your accounts are to holding one shared
history. `doctor` answers "is anything broken"; this answers "how far apart are my sidebars,
and which way is it moving":

```
claude-code-sessions alignment
```

Five properties, reported separately and never averaged, because they fail independently:
**reachable** (a conversation opens from *some* sidebar), **distinguishable** (no two rows in
*one* sidebar share a title), **consistent** (a row file opens the *same* conversation in every
account), **complete** (a conversation opens from *every* sidebar), and **safe** (no dead, blank
or unreadable rows).

Two of those actively trade against each other: making a conversation reachable everywhere lands
both halves of a disagreeing pair in every sidebar under one name, which is a direct hit to
*distinguishable*. A single "aligned: yes/no" would hide exactly the decision you have to make.

It reads row files and transcript **filenames** only — no transcript content — so it stays fast
on a store with gigabytes behind it. **It always exits 0** when it could produce the report. It
is a scoreboard, not a check: these numbers stay non-zero for as long as the work takes, and a
command that exits 1 for months trains you to stop reading it.

Duplicate titles are counted **per sidebar**, never machine-wide. One conversation synced to
three accounts shows one row in each — that is not a duplicate, and treating it as one leads to
proposing the removal of an account's only copy.

**`repoint`** — point one sidebar entry at a different conversation:

```
claude-code-sessions repoint --only "ACME-REVIEW" --to <cliSessionId> --apply
```

A listing row's filename is the *app's* session id, which survives being resumed, while the
`cliSessionId` inside it names the transcript — and that changes on every new CLI run. So one
sidebar entry accumulates several conversations over its life, and this chooses which one it
opens. It changes that single field and nothing else; both conversations stay on disk.

`--only` matches a title substring or a local id and must resolve to exactly one row. `--to`
takes a `cliSessionId` — `doctor` lists the ones nothing currently points at. `--store` picks
the account (id, org, email, or path substring); it defaults to the account that is signed in,
which is the one difference from `sync`: this command exists to fix the sidebar you are looking
at. The app must be closed either way, and `undo` puts the old pointer back.

**`new-row`** — create a sidebar row for a conversation that has none:

```
ccs new-row --to 174eb7c1-879f-4f0e-abff-8fdc7210f3d9
```

`repoint` moves an existing row and `sync` copies one between accounts; neither can
make one where none exists. A conversation whose row was overwritten, or never copied,
is on disk and unopenable — 170 such transcripts were measured on one machine.
`doctor` lists them; this turns one back into a sidebar entry.

The title comes from `--title`, or the transcript's own title if it has one, or a
placeholder like `(untitled - 2026-06-14, 181 turns, Personal)` — deliberately not a
summary, because a machine-made title that reads like one is worse than an obvious
placeholder.

**Unofficial and unverified across app versions.** The app accepting a row it did not
create was established by experiment, not documentation. The row is built from fields
measured across 988 real rows plus values read out of the transcript itself, and it
asserts nothing beyond those. If a future version rejects one, `doctor` reports it —
but only while the creating operation is still in the journal. That journal keeps the
last ten finished operations, plus any it has to pin indefinitely: an unfinished
`sync`'s claim on a destination row, and a rollback that left behind a row it could
not remove. A **completed** `new-row` op is never pinned, so ten finished operations
of any kind is exactly the window this alert has. In practice the alert covers the
case it was built for, since an app version that rejects a row does so the first time
it opens the sidebar. A row that disappears after a busy stretch of other operations
will go unreported.

**`move`** — relocate a session to another project folder:

```
claude-code-sessions move 3c3c3eae-0e2f-4be4-9fba-407f06816f79 "C:\path\to\project" --apply
```

Get the full id from `claude-code-sessions list --full`.

**`undo`** — reverse the most recent completed operation:

```
claude-code-sessions undo --apply
```

**`recover`** — resolve an operation left non-terminal by a crash or interruption:

```
claude-code-sessions recover
```

**`sync`** — copy a session's sidebar **listing row** from your signed-in account into your
*other* Claude account's store on this machine, so it shows up in that account's sidebar too:

```
claude-code-sessions sync --apply
```

There is only one copy of the conversation itself — shared, and carrying no account identity —
so a synced session opens and resumes normally under the other account. Without `--to`, the
destination must be unambiguous: exactly one other account store on the machine, or `--to
<substring>` naming one of several by its account id, org id, **store path**, or — when it can be
recovered, see below — its email. The path is matchable because ids alone are not
always enough — Windows exposes two store roots (the MSIX package path and the classic
`%APPDATA%\Claude` path), and a machine that migrated between installers can hold the same
account under both, in which case the path is the only thing that tells the two copies apart.
Both "which store did you mean" refusals print the full path beside each candidate. `sync` refuses outright if it
cannot tell which account is signed in — either because neither `~/.claude.json` nor
`config.json` names one, or because the two disagree about which account it is. `--to`
cannot substitute for that: it only narrows *which* dormant store to use, and if we don't know
which account is live we cannot verify the one you named isn't it. A disagreement prints both
files' 8-character id prefixes; the fix is to re-authenticate the CLI (run `claude`, then
`/login`) as the account you're using, or switch the desktop app to that account, so the two
files agree.

When the two files **disagree**, there is a faster path than `/login`: you know which account
your desktop app is signed into, and `--live <substring>` asserts it (an account id, org id,
store path, or email — the same matching as `--to`; ambiguity is a refusal listing the
candidates). It is deliberately not a `--force`: it works *only* while the files disagree, it
must name one of the two accounts they name (anything else is refused — an account neither
file names is evidence of something else being wrong), and the dry run, apply, `--json`
(stderr), `undo`, and `recover` output all shout that the override was used and which file it
overrode. The assertion is journaled with the operation and re-checked against the identity
files before every write, undo, or recover it ever performs; if the disagreement has changed
or vanished by then, the operation refuses rather than trust a stale assertion. The
running-app guard is completely unaffected: `--live --apply` still refuses while the desktop
app is running. Design rationale in `docs/internals.md` (RULING 5).

`--json` prints the plan by itself, the same as the default dry run. Combined with `--apply` it
runs first and describes what actually happened instead — real `written` flags per row and a
`result` key, not the plan it would have executed. Automation that assumes `sync --json` output
is always a preview will misread `sync --apply --json`.

- **Sessions you deleted in the destination stay deleted.** The app writes a small record
  (`deleted_<id>`) when you delete a session but does not consult it when a row for that session
  reappears elsewhere — confirmed by restoring a deleted row alongside its own record and
  reopening the app, which showed the session again. `sync` reads the *destination's* records and
  skips any source session they cover, and names each skip in its report (`kept deleted: …`).
  `--include-deleted "<title-or-id>"` overrides the skip for **one** named session; it never
  applies to a whole run. The name must resolve unambiguously — a full id, or a title
  substring matching exactly one deleted session. (The app files a deletion under its
  session id *or* under its local id, and `sync` honours both, so either id works here.) A substring that hits several is a refusal
  listing the candidates, not a silent multi-resurrection. Anything it does resurrect is printed
  under a `!! RESURRECTING …` heading *before* the list of rows to copy, and flagged again in
  that list, so bringing back a session you deliberately deleted is never something the command
  does quietly.
- **Connector config and permission grants are not copied by default.** A row can carry a full
  snapshot of the account's connected MCP servers; across 432 real rows on this machine that
  field ran as large as 1.36 MB in a single row and totalled 289 MB. Stripping it is verified
  safe: on a real row it cut 132,264 bytes to 715 (99.5% smaller) with the sidebar entry,
  history, responses, and connectors all working normally afterward, under the account that owned
  them. That proves the *app* tolerates the field's absence — it does not prove the *destination
  account* has the same connectors configured. A session that relies on one opens fine either way
  and fails at its first tool call if the integration isn't set up there too; set it up in the
  destination account. The default transform also **resets the row's permission state**
  (`alwaysAllowedReasons`, `sessionPermissionUpdates`, `chromePermissionMode`,
  `chromeTabGroupId`) to its defaults: a permission you granted under one login was never granted
  under the other, and the worst case of resetting it is a re-prompt.
  `--verbatim` skips the whole transform — it copies connector config **and those permission
  grants** across the account boundary unchanged. The permission half is the more
  security-relevant of the two: use `--verbatim` only when you actually want the second account
  to inherit what the first one had allowed.
- **It needs the app closed too, the same as `move`, `undo`, and `recover` — regardless of which
  file resolved the signed-in account.** `sync` only ever writes the store you are *not* signed
  into, and re-checks that at the moment it writes, not just when it planned; that re-check
  catches an account *switch* between planning and writing, but it is the same determination run
  again, not an independent second opinion. An earlier version of this tool trusted
  `~/.claude.json`'s `oauthAccount` enough to skip the running-app check whenever that file
  named an account outright. A real desktop account switch measured `oauthAccount` staying
  *stale* while `config.json`'s `lastKnownAccountUuid` tracked the switch — the opposite of the
  trust ordering that exemption assumed — and a review had already constructed the reverse
  (`config.json` stale, `oauthAccount` fresh) in a synthetic store: either identity file can be the stale one, and
  neither is trusted to certify "the destination is dormant" while the app is running. `--apply`,
  `undo`, and `recover --back` on a sync op therefore all refuse whenever the Claude desktop app
  is visible, whoever the files say is signed in. The dry run still labels a source resolved from
  `config.json` rather than `oauthAccount` (`from  (from config.json)`, plus a warning line) —
  that's a provenance note now, not a stronger/weaker gate: the guard applies the same way either
  side. A running Claude Code CLI session does not trip it — only the desktop app does. Closing
  the desktop app removes the check; signing *into* it does not, since the check is about
  whether its process is running, not which account it's signed into.
- **The destination's email is best-effort.** `~/.claude.json` names only the account you are
  signed into, so for the *other* account `sync` looks in the per-account Claude Code config the
  desktop app leaves inside its local-agent-mode sandbox
  (`local-agent-mode-sessions\<accountUuid>\…\.claude\.claude.json`), and uses its email only if
  the account id inside matches. That directory exists only for an account that has used local
  agent mode, so it is not always there — when it isn't, the dry run prints `(email unknown)`.
  Either way both endpoints also print their account/org id prefix and their full store path, so
  you always have a physical folder to recognise: the home directory becomes `~` and each id is
  truncated to 8 characters (e.g. `~\AppData\...\claude-code-sessions\aaaaaaaa…\bbbbbbbb…`)
  unless you pass `--verbose` for the paths and ids in full. Check the path, not just the email,
  before `--apply`.
- A synced row is a **snapshot**: title, last-activity time and turn count live in the row
  itself, so a session you keep using shows its copy-time state in the other account. **`sync
  --update` refreshes those rows** — it is the only route that overwrites rather than adds, so
  it is opt-in per run (an unticked checkbox in the window) and lists every overwrite before
  doing it. A row the destination account changed since planning is refused, never
  overwritten, and `undo` puts the exact replaced bytes back — for as long as the operation
  stays in the journal, which keeps the ten most recent finished ops. Two things worth knowing
  before you tick it: a refresh replaces the **whole** row with this account's copy, not just
  its title and timestamp; and because each account keeps its own snapshot, the copy you are
  overwriting is not automatically the older one, so any refresh that would move the other
  account *backwards* is called out by name in the plan — and **`--newer-only`** (in the
  window, "only where mine is newer", ticked by default) holds those back instead of sending
  them, along with any whose direction cannot be determined. Both sets are listed by name, so
  you can see exactly what was not sent. **A stale row never truncates the conversation it
  points at — but it can point at an entirely different one.** A row's filename is the app's
  session id, which survives being resumed, while the transcript it names changes on each new
  run; each account records whichever transcript it last saw. So the same sidebar entry can be
  a 1,386-message conversation from today in one account and a different 738-message one from
  last week in another — the newer one intact on disk but unreachable from that sidebar. On a
  real three-account machine, 15 of 333 shared rows were in exactly that state. **That is the
  strongest reason `--update` exists**, and the reason direction matters: refreshing *from* the
  account holding the older pointer hides the newer conversation from the account that had it,
  which is what `--newer-only` prevents. The plan says which refreshes **open a different
  conversation** rather than just updating a title, and if the conversation being displaced is
  reachable from no other account, that refresh is **held back and named** unless you pass
  `--allow-orphan` (window: "allow hiding a conversation", off by default) — the mirror of
  `--include-deleted`, for the case where a refresh takes access away instead of updating
  something. (`completedTurns` is not a count of your conversation and should not be read as
  one: rows showing 17 and 33 sat in front of a transcript holding 472 messages.)
- **"Already there" is decided by filename, not by conversation.** A row counts as present in the
  destination when a file of the *same name* (`local_<appSessionId>.json`) exists there. That is
  exactly right for rows `sync` itself copied, since it copies the name along with the contents.
  But a destination row pointing at the same conversation under a *different* local id — one
  placed by an earlier hand-run script, say — is not detected, and `sync` would add a second row
  for the same session, showing it twice in that account's sidebar. Deleting the duplicate in the
  app is enough to fix it.
- Sign into the other account (or restart the app) to see the results.

## Safety design

- **Dry-run by default.** Every mutating command prints its plan and does nothing until you
  pass `--apply`.
- **Journaled copy-verify-commit-delete.** A move journals its complete intended state before
  touching anything, copies to the destination, re-verifies the copy by hash, rewrites listing
  rows atomically, re-verifies both sides one last time, and only then deletes the source.
  Nothing is ever deleted while it is the only copy.
- **`undo`** reverses the most recent completed `move`, `sync`, `repoint` or `new-row` by
  running the same journaled protocol in reverse — for a `sync`, deleting exactly the rows the
  op wrote; for a `repoint`, restoring the pointer the row held before; for a `new-row`,
  deleting the row it created. In every case only while what is on disk still matches what was
  written.
- **`recover`** classifies and resolves any operation a crash or interruption left in a
  non-terminal state — nothing is left stranded.
- **`sync`** only ever writes rows into the store of the account you are *not* currently signed
  into, re-verifying that at the moment it writes as well as when it planned. It refuses outright
  if it cannot identify which account is signed in at all, or if `~/.claude.json` and
  `config.json` disagree about which account that is: `--to` names a destination, but without a
  confirmed, unambiguous live account there is nothing to check that destination against. The
  one sanctioned exception is `--live` (RULING 5 in `docs/internals.md`): while the files
  *disagree*, you may assert by name which account the desktop app is signed into — a fact you
  can verify and the files cannot — and the assertion is journaled, shouted in every output,
  and re-checked against the identity files before every mutation the operation ever performs.
  Every sync mutation — `--apply`, `undo`, and `recover --back` on a sync op — also refuses while it can
  see the Claude desktop app running, the same guard `move`/`undo`/`recover` use, applied
  regardless of which identity file resolved the signed-in account — and regardless of
  `--live`, which never touches this guard: staleness has been shown in
  both directions (`oauthAccount` measured stale across a real account switch; `config.json`
  stale in a review-constructed store), so no file evidence is trusted to certify the
  destination is dormant while the app is visible. A running Claude Code CLI does not count
  toward this — only the desktop app does.
- **Refusal philosophy.** The tool fails closed: an unrecognized on-disk layout, an unreadable
  row, an ambiguous encoding scheme, or a running Claude process is a refusal, not a guess.
  "Couldn't look" is never treated as "nothing there."

## Compatibility matrix

| Platform | Status | Mutations |
|---|---|---|
| Windows 11 + Claude Desktop | verified 2026-07-31; sync end-to-end incl. live continuation 2026-08-03; sync-undo drift refusal live 2026-08-04 | read-only + mutations |
| macOS / Linux desktop | unverified | **read-only only** — desktop-store mutations refuse, with no override |
| CLI-only sessions (any OS) | transcript layout verified | mutations allowed via `--transcript-only` |

The dates are measurements against specific builds — most recently Claude Desktop
1.24012.11.0 (Microsoft Store install) and Claude Code CLI 2.1.220. The on-disk format has
changed once already during this tool's own development; treat a much newer build as
unverified territory.

**On non-Windows, `move` and every `sync` route refuse to touch the desktop store, and there
is no flag to override that.** The layout is confirmed on Windows only; macOS reportedly has
two candidate layouts — the ordinary Application Support path and a sandboxed
`~/Library/Containers/…` one — and neither has been confirmed here. An override would let you
waive a risk you have no way to evaluate, which is the opposite of how every other refusal in
this tool works.

What *does* work there: `list` and `doctor`, which are read-only, and `move` for a session
that has **no desktop listing row** — a CLI-created one — via `--transcript-only`, because the
transcript layout is verified cross-platform. (Note `--transcript-only` does not force that
mode; it permits it when no row exists. A session that *has* a desktop row is not movable on
an unverified platform.)

**If you're on macOS and want this supported:** `claude-code-sessions doctor --verbose` output
in an issue is exactly what's needed. It's read-only, mutates nothing, and it reports the store
roots found and the layout recognised — which is the whole of what's missing.

The Windows row is an end-to-end check on a real store, not just a passing test suite:
a disposable session was moved between projects, the app was restarted and the session
resumed at its new location, `undo` correctly **refused** once that resume had appended
to the transcript (rather than discarding the new messages), and a deliberately
interrupted move was resolved in both directions with `recover`. Afterwards `doctor`
reported no new findings and the journal held no unresolved operations.

`sync`'s underlying mechanics were checked against two real, live accounts on this machine before
the command existed: rows copied by hand between them and confirmed visible in the destination's
sidebar, and — separately — a deleted session's row restored alongside its own deletion record and
confirmed visible again, which is the finding that makes `sync`'s tombstone-skipping mandatory
(see `docs/internals.md`). The `sync` command itself is covered by its test suite and, as of
2026-08-02, its own end-to-end `--apply` run against two real, live accounts: a row synced from
one account opened in the other with its full conversation history intact. That run did not send
a new turn through the synced row; a second verification on 2026-08-03 closed that gap — a live
session's own listing row was synced across accounts and the conversation was then continued
from the destination account's sidebar, new turns flowing through the synced row. And on
2026-08-04, `undo` of a synced row the destination account had since opened was refused on
real data: the row no longer matched what the op journaled, so the drift refusal declined
to delete it — exactly the designed behavior.

## What's stored locally

`~/.claude-code-journal/` holds the tool's own bookkeeping, never your conversation content:

- `ops/<op-id>/manifest.json` — the journal for each move/undo/recover/sync operation (paths,
  hashes, row pre-images, phase history). Rotated: the 10 most recent terminal operations are
  kept, non-terminal ones never pruned automatically.
- `ops/lock` — a single-instance lock held for the duration of a mutation.
- `moved-log.jsonl` — a tiny, append-only, never-rotated record of completed moves (session id,
  from-path, to-path, date), used to recognize your own past moves during future collision and
  encoding-evidence checks.
- `account-emails.json` — account uuid → the email that account had when it was **signed in**,
  with the date seen. `~/.claude.json` names only the live account, so the destination of a sync
  is precisely the account whose email is hardest to recover; but every account is the live one
  sometimes, so syncing in both directions teaches the pair. A remembered email is **labelled as
  remembered** wherever it is shown, because it says what was true when that account was last
  signed in, not what is true now — the store path beside it remains the identifier to check.
  Delete the file to forget everything; it is rebuilt as accounts are used.
- `trust-signed-helper` — present only if you enabled the opt-in above.

To purge everything the tool has ever written, delete the whole `~/.claude-code-journal/` directory.
This does not touch any transcript or listing row — only the tool's own journal.

**`--json` output is not redacted.** Plain-text output replaces your home directory with `~`
and truncates any UUID-shaped identifier (session ids, org/account ids in store paths) to its
first 8 characters by default — pass `--verbose` to see paths and ids in full. `--json` output
always contains full paths, titles, and ids, unredacted, so it can be consumed programmatically.
Do not paste `--json` output into a public issue or forum post — copy only the fields you mean
to share.

`sync --json` is the one place this discloses **a second account's** identifiers: unlike every
other command, its output carries the destination account's account/org uuids and full store path
in the clear, regardless of `--verbose`, because those are what the plan is *about*. The
plain-text report redacts them like everything else; the JSON does not.

**`--anonymize` hides the *content*, which redaction never did.** The redaction above covers
the **machine** — your home directory and uuid-shaped ids. It has never touched **titles**, and a
title is a model-written summary of whatever the session was about, so it can name a client, a
legal matter or a medical one. Output that announced itself as redacted was therefore unsafe to
paste, and that gap is how real titles reached this repo's own documentation more than once.

`--anonymize` is available on every command and replaces each title, project path and account
address with a stable opaque label:

```
claude-code-sessions list --anonymize

f41ea19e…  <session-69f9>       <project-0c8f>
e82cfaac…  <session-9c06>       <project-1a20>
```

The labels are deliberately unreadable rather than plausible fake titles — a convincing fake read
back later is indistinguishable from a real one, and that confusion is the original problem. They
are stable (a hash of the value), so two pasted listings can still be compared to each other, and
session ids are left alone because they are random and are what makes the output useful.

It covers `--json` too, which no amount of care with plain-text output would have.

Two combinations are refused rather than half-honoured:

| | |
|---|---|
| `--anonymize --verbose` | `--verbose` prints lines unredacted and never reaches the redaction path, so titles would appear anyway |
| `--anonymize --apply` | a plan is what gets pasted; an apply writes to a store, and a substituted title must never be mistaken for real data |

**Windows durability note.** The commit step fsyncs every file it writes before deleting the
source, but Windows has no equivalent of a directory fsync, so the directory-entry update
itself (the rename, the delete) is not separately forced to disk. The spec accepts this as a
residual risk rather than a defect: file-level fsync plus delete-last ordering means a power
loss in this window can only lose an already-fsynced deletion, never earlier fsynced writes —
worst case is a leftover duplicate, never lost data, and `recover` classifies exactly this
window into `journaled`/`completed`/`rolled_back`.

## Roadmap

- **`sync --update` shipped in 0.9.9** with the drift-refusal treatment this entry asked for:
  the plan records the destination's current bytes, the write refuses if they moved since, and
  `undo` restores them exactly. It turned out to matter more than "refresh a stale title": a
  row is a **pointer** to a transcript, and two accounts can point at different ones, so
  `--update` can restore access to a newer conversation — or, in the wrong direction, hide one.
  Hence `--newer-only` (on by default in the window) and `--allow-orphan` (off). **0.9.10**
  adds the number that makes that last decision judgeable: the plan says how much of the
  conversation a refresh would displace also appears in the incoming one, so a hold reads as
  "5% of its prose is in the incoming conversation" rather than only "would hide a
  conversation". See `docs/internals.md`, RULING 8.
- **`repoint` and doctor's orphan ranking shipped in 0.9.11**, from the case none of the above
  covers: the *app* repointed a row, while this tool was not running. Resuming a session under
  another account continues that branch and stamps its id into whichever store is active, which
  can leave the conversation you were in reachable from no sidebar. No guard here applies —
  the mutation never came through this tool — so what shipped is detection (`doctor` ranks
  conversations nothing points at) and repair (`repoint` puts a pointer back, journalled and
  undoable).
- **0.9.13 makes the swap report answer the question people actually ask.** A long real session
  on 2026-08-22 found three ways the plan described a pointer swap without saying what mattered.
  It now states the **length**: "this row goes from 388 to 189 prose turns — 199 FEWER", because
  a percentage is not a loss and every reader was converting one into the other in their head.
  Reachability is now checked **per row rather than per store**, so a conversation still held by
  another row *in the destination account* no longer reads as about to be orphaned — a false
  alarm whose only cure was ticking the override that guards the real case. And app-emitted
  plumbing (`[Request interrupted by user]`, `No response requested.`) no longer counts as
  conversation: three rows reporting 95–98% overlap turned out to have **zero** authored turns
  only in the displaced copy. See `docs/internals.md`.
- **0.10.0 adds `new-row` and `alignment`.** `repoint` could already reach a conversation that no
  sidebar opened - but only by spending the row it was aimed at, which is the trade that produced
  the loss `doctor`'s orphan ranking exists to report. **`new-row` is the additive answer**: it
  creates a row and changes nothing that already exists. It takes its title from the transcript's
  own `customTitle` where there is one, and a placeholder that does not impersonate a summary where
  there is not - measured across the orphans on one machine, only 48 of 169 had a title to derive
  from, so manufacturing the rest would have been guessing dressed as a summary. It also refuses to
  add a second door to a conversation the account can already open.

  **`alignment` answers a different question from `doctor`.** `doctor` asks whether anything is
  broken or stuck; `alignment` asks how far apart several accounts are, as five numbers that fail
  independently: reachable, distinguishable, consistent, complete, safe. They are reported
  separately and never averaged, because two of them trade against each other - making a
  conversation reachable everywhere lands both halves of a divergent pair in every sidebar under one
  name, which is a direct hit to *distinguishable*. A single "aligned: yes/no" would hide exactly
  the decision the reader has to make. It always exits 0 when it can produce the report: it is a
  scoreboard, not a check, and a command that exits 1 for months trains you to stop reading it.

- Platform rows above move from "unverified" to "verified" as contributors confirm the store
  paths and behavior on their own machines.

## Reporting problems

This is a one-person project and support is best-effort. The fastest reports to act on
carry the failing command's plain-text output, `claude-code-sessions doctor` output, and —
if an operation was involved — the journal op id; the issue templates ask for each. Please
don't paste `--json` output into an issue: it is deliberately unredacted (see above).

## More

Companion post: [The session that synced itself](https://github.com/craigstoller/claude-code-sessions/blob/main/docs/the-session-that-synced-itself.md).

MIT licensed — see [LICENSE](https://github.com/craigstoller/claude-code-sessions/blob/main/LICENSE).
