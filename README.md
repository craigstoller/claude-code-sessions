# claude-code-sessions

See your Claude Code sessions under your other Claude account on the same machine - and move
sessions between project folders. Journaled, reversible, fails closed.

> **Unofficial.** Not affiliated with Anthropic. Reverse-engineered on-disk formats; fails
> closed when it sees anything it doesn't recognize.
>
> **Not to be confused with** `claude-code-sessions\` - the folder *Claude Desktop* keeps your
> per-account session list in, which this tool reads. This tool's own bookkeeping lives in
> `~/.claude-code-journal/` and is safe to delete.

**Companion read:** [The session that synced itself](https://github.com/craigstoller/claude-code-sessions/blob/main/docs/the-session-that-synced-itself.md) -
what Claude Desktop actually keeps on disk, and why this tool is built out of refusals.

## The problem

**Claude Desktop files its session list per account.** Switch logins and the sessions you
started under the other one disappear from the sidebar. They are not gone: the conversation
itself is a single shared file carrying no account identity at all, sitting untouched on your
disk. Only the *listing row* - the sidebar entry pointing at it - is private to each login, and
the app reads only the folder belonging to the account you are signed into right now. `sync`
copies that row into your other account's store, so the session shows up there too.

**Separately, every session is filed under the directory it was started in**, and there is no
official way to move one afterwards - a session begun in the wrong folder is stuck there.
`move` relocates the transcript and every listing row that points at it, verifying each side
before touching the other, so the session reopens cleanly in its new home.

## Install

```
pipx install claude-code-sessions
```

This installs three commands: `claude-code-sessions` and `ccs` (identical - the short one
is for everyday use; examples below use the long form), plus `ccs-gui`, a window for
people who would rather not use a terminal. Examples below are the CLI; see
[A window instead of the CLI](#a-window-instead-of-the-cli).

Or download `claude_code_sessions.py` and run it directly - the runtime has no dependencies beyond
the Python 3.9+ standard library:

```
python claude_code_sessions.py --help
```

## A window instead of the CLI

```
ccs-gui                     open it
ccs-gui --install-shortcut  add a Desktop + Start Menu shortcut (Windows)
```

The window is called **"Claude sessions"** and has three tabs:

- **Level** - the home tab. It measures how close the accounts are to holding one shared
  history (`alignment`'s five numbers), previews what `converge` would create, and renders
  each title-collision hold as a rename row prefilled from the measured suggestion - tick,
  edit, or ignore each one. Apply confirms in two stages, each describing exactly what runs
  next: first every rename as an `old -> new` list (each lands as its own journalled,
  individually undoable operation), then a fresh converge plan's own numbers. After any
  apply the pane re-measures and re-renders from the store as it now is.
- **Health** - `doctor`'s report (findings first), plus interrupted-operation detection.
  While an unresolved operation sits in the journal, Apply and Undo are disabled on every
  tab and the tab shows each stuck operation with a Copy button for the `recover` command -
  resolving is a directional judgment the CLI walks you through, so execution stays there.
- **One session** - the exception: the sync pane. Copy one session to the other account,
  or refresh the row it already has there - type its title in the filter (`sync --only`);
  destination choice, the refresh checkboxes and the same confirmations as the CLI. Still
  the only tab that can overwrite a row; converge is additive and never refreshes. Apply is
  live for a one-row plan; copying every row a plan lists takes a tick that names the
  count. The two tabs can disagree about counts, because Level counts conversations a
  sidebar cannot open while this tab counts row files the destination lacks - and a
  multi-account store accumulates different row filenames for the same conversation - so
  Level is the routine, and this tab is for one session at a time (the pane says so, and
  warns when the rows it would add already carry a title that sidebar uses).

One Undo button below the tabs reverses the most recent operation the window can reverse
(a copy, a rename, or a converge), exactly the one `ccs undo` would pick; pressing it
repeatedly walks the journal back one operation at a time.

Everything **writes nothing until you press Apply**. It is a thin shell over the same
planning and execution the CLI uses, not a reimplementation, so every guard, refusal, and
journal entry behaves identically - and a refusal is shown **verbatim** rather than
summarised into something friendlier. Some things it deliberately does not offer, because
the friction is the safeguard: `--verbatim` (which copies permission grants across an
account boundary), `--include-deleted` (which brings back a session you deleted), and
`recover` execution. `move`, `repoint`, and `new-row` are CLI-only for now.

Registered as a GUI script, so nothing flashes a console window. It needs `tkinter` - part
of the standard library, but packaged separately on some Linux distributions
(`apt install python3-tk`). Upgrading from an earlier version? `--install-shortcut`
replaces the old "Claude session sync" shortcut with the new name; a taskbar pin to the
old name is yours to re-pin.

## Optional: let the browser stay open

The desktop app ships a helper for its browser extension that Chrome or Edge launches and
that can outlive the app, so the guard counts it and you have to close that browser too. A
measured build of that helper is excluded - but **the helper auto-updates every few days**
(three builds in eight days, as measured), and each update correctly fails closed, so the
exclusion lapses and the browser has to be closed again until someone re-runs the ~12-minute
measurement in
`tools/native-host-measurement/`.

If that trade isn't worth it to you, there is an opt-in: trust **any** helper at the app's
own package path that Windows reports as validly signed by `Anthropic, PBC`.

```
claude-code-sessions trust-signed-helper          # show the current state
claude-code-sessions trust-signed-helper --on     # enable it
claude-code-sessions trust-signed-helper --off    # back to the default
```

In the window it is the **"Let browsers stay open"** checkbox, which explains the trade before
enabling anything. Either way it is one marker file - `~/.claude-code-journal/trust-signed-helper` -
so deleting that file also revokes it.

It is off by default deliberately. It is weaker than the default - trust moves from "these
exact measured bytes" to "this publisher", so a future Anthropic build that started touching
the store would be excused without being measured. That risk is small (the measurement found
a message bridge that never touched the store) but it is real, and it rests on evidence you
have not personally examined, which is why nothing enables it for you. An unsigned, tampered,
differently-signed, or out-of-path binary still counts, as does one whose bytes can't be
read. See `docs/internals.md`, RULING 7.

## Before any move: close the Claude app.

`move`, `undo`, `recover`, `repoint --apply`, `new-row --apply`, `realign --apply`,
`relink --apply`, and every mutating `sync` route (`--apply`, `undo` of a completed sync,
`recover --back` on a stuck one) all refuse while they can see the Claude desktop app
running - whoever the identity files currently say is signed in. That includes `undo` of a completed `repoint` or `new-row`, and `recover` on an
interrupted one wherever it would write or delete a row rather than only close the record. Closing the app first avoids
the refusal and guarantees nothing is actively appending to the sidebar rows you're about to
touch. A running Claude Code CLI session does **not** count: it's recognised by its own
install path and excluded from the check, so an open `claude` session never blocks any of this.
(For `move` specifically, a running CLI session *can* still be writing the transcript being
relocated - the transcript-freshness check and the last-instant content re-verification cover
that case; see Safety design.)
If `~/.claude.json` (the CLI's identity file) and the desktop app's `config.json` disagree
about which account is signed in, `sync` refuses to even plan - re-authenticate the CLI (run
`claude`, then `/login`) as the account you use, or switch the desktop app to it, so the two
files agree - or, if you know which account the desktop app is signed into, assert it with
`sync --live <account>` (see the `sync` section below; the running-app guard still applies in
full). Signing into the desktop app does **not** refresh `~/.claude.json`. A refused
`sync --apply` (desktop app running) refuses **before anything is journaled** - no lock file,
no op directory - so there's nothing left behind for `doctor` to flag or `recover` to clean up;
just close the app and re-run. One measured wrinkle: the desktop ships a helper for its browser
extension (`chrome-native-host.exe`), launched by Chrome or Edge, that can outlive the app
itself, and the guard counts it - if a refusal names that process, fully exit that browser too
(the refusal always names what it saw).

## Usage

Fourteen commands. All mutating commands default to a dry run; add `--apply` to execute.
(`trust-signed-helper` is the opt-in described above; it changes a setting, not your
sessions.)

`--version` prints the release **and the file it ran from** - worth knowing when a pipx
install and a source checkout are both on `PATH`, which is exactly when a version number
alone cannot tell you which copy answered:

```
$ ccs --version
claude-code-sessions 0.10.0
running from: C:\Users\<name>\AppData\Local\pipx\...\claude_code_sessions.py
```

**`list`** - inventory sessions, optionally filtered by a search term:

```
claude-code-sessions list gate
```

**`doctor`** - read-only health report (stale locks, unresolved operations, orphaned rows,
encoding-scheme ambiguity, a synthesized row that has since vanished, **conversations no
account's sidebar points at**, and **conversations whose accounts open different copies of one
transcript**):

```
claude-code-sessions doctor
```

That last one needs a word, because most of what it finds is normal: a session started from
the CLI never had a listing row, and deleting a session in the app leaves its transcript
behind. What is *not* normal is a **recent, large** one - so they are ranked newest-first and
only the top few are printed, with the total alongside (the full list is in `--json`).
Resuming a session while signed into a different account repoints its row, which can leave the
conversation you were in an hour ago reachable from no sidebar at all. That is what this
ranking is for, and `repoint` is how you put it back.

The copies check covers a case every other number misses. A conversation's transcript can end
up in two project folders, usually after a session moves into a second worktree. Each account's
row names a folder through its `cwd`, and the app opens the copy in that folder, even a stale
one. So one sidebar can show a conversation as it was weeks ago while another shows it today,
and `alignment` still counts it complete. `doctor` lists each such conversation, which
copy each account's row opens, and how each pair of copies relates: one behind the other (one
history, one copy stopped growing), the same conversation, byte-identical, or diverged (each
holds turns the other lacks). It compares the bytes first and then reads what lies past the
bytes two copies share, because the app writes bookkeeping records (`tools-used` when it opens
a copy, `cost-state` when it quits) to whichever copy it has open, the stale one included. A
copy ahead only by those records holds the same conversation; a stale copy the app has opened
and quit is still behind, although its bytes no longer match. Any other record type counts as
conversation. `doctor` exits 1 when an unarchived row opens an older or a diverged copy.
Identical copies and copies that differ only in bookkeeping are listed and do not change the
exit code. Neither does a row whose folder holds no copy at all. A row whose `cwd` names a main repository while the transcript sits in
one of its worktree folders is the app's normal placement, not a fault.

Deleting the stale copy is a catch-up, not a fix. On one conversation, measured on Desktop
5.11492.5, opening a row whose folder held no copy made the app copy the transcript back into
that folder, so the split returns once the conversation is worked from one account and opened
from another. For the same reason `doctor` also lists, without counting them, conversations with
a single copy and a row whose folder holds none: opening that row starts a split. `realign`
(below) is the repair for both.

**`alignment`** - read-only scoreboard: how close your accounts are to holding one shared
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

It reads row files and transcript **filenames and sizes** only - no transcript content - so it
stays fast on a store with gigabytes behind it. That is why its line on conversations whose
accounts open different copies only counts them, and how many differ in length; telling a stale
copy from a diverged one means reading both, which `doctor` does. **It always exits 0** when it could produce the report. It
is a scoreboard, not a check: these numbers stay non-zero for as long as the work takes, and a
command that exits 1 for months trains you to stop reading it.

Duplicate titles are counted **per sidebar**, never machine-wide. One conversation synced to
three accounts shows one row in each - that is not a duplicate, and treating it as one leads to
proposing the removal of an account's only copy.

**`repoint`** - point one sidebar entry at a different conversation:

```
claude-code-sessions repoint --only "ACME-REVIEW" --to <cliSessionId> --apply
```

A listing row's filename is the *app's* session id, which survives being resumed, while the
`cliSessionId` inside it names the transcript - and that changes on every new CLI run. So one
sidebar entry accumulates several conversations over its life, and this chooses which one it
opens. It changes that single field and nothing else; both conversations stay on disk.

`--only` matches a title substring or a local id and must resolve to exactly one row. `--to`
takes a `cliSessionId` - `doctor` lists the ones nothing currently points at. `--store` picks
the account (id, org, email, or path substring); it defaults to the account that is signed in,
which is the one difference from `sync`: this command exists to fix the sidebar you are looking
at. The app must be closed either way, and `undo` puts the old pointer back.

**`new-row`** - create a sidebar row for a conversation that has none:

```
ccs new-row --to a1b2c3d4-e5f6-4a7b-8c9d-e0f1a2b3c4d5
```

`repoint` moves an existing row and `sync` copies one between accounts; neither can
make one where none exists. A conversation whose row was overwritten, or never copied,
is on disk and unopenable - 170 such transcripts were measured on one machine.
`doctor` lists them; this turns one back into a sidebar entry.

The title comes from `--title`, or the transcript's own title if it has one, or a
placeholder like `(untitled - 2026-06-14, 181 turns, Personal)` - deliberately not a
summary, because a machine-made title that reads like one is worse than an obvious
placeholder.

**Unofficial and unverified across app versions.** The app accepting a row it did not
create was established by experiment, not documentation. The row is built from fields
measured across 988 real rows plus values read out of the transcript itself, and it
asserts nothing beyond those. If a future version rejects one, `doctor` reports it -
but only while the creating operation is still in the journal. That journal keeps the
last ten finished operations, plus any it has to pin indefinitely: an unfinished
`sync`'s claim on a destination row, and a rollback that left behind a row it could
not remove. A **completed** `new-row` op is never pinned, so ten finished operations
of any kind is exactly the window this alert has. In practice the alert covers the
case it was built for, since an app version that rejects a row does so the first time
it opens the sidebar. A row that disappears after a busy stretch of other operations
will go unreported.

**`retitle`** - rename a conversation's sidebar row in every account that holds it:

```
claude-code-sessions retitle --only <cliSessionId> --title "ACME-REVIEW" --apply
```

Every sidebar title is written by the app's summariser, so two conversations about the same
work get the same sentence - roughly 15 such collisions accumulate per month on the machine
this was measured on - and the app's own rename surface is one row, in one account, at a time.
This command renames a **conversation**: `--only` takes a title substring or a `cliSessionId`
prefix, must resolve to exactly one conversation, and the rename lands on its row in *every*
account, so the sidebars keep reading the same. Resolving a colliding title is *expected* to
refuse the first time - a colliding title names several conversations by construction - and the
refusal lists each candidate with its id, so the retry is a copy-paste.

The stored title is the trimmed input, `titleSource` is pinned to `"user"` (measured on
desktop app 1.34493.1.0: the app leaves such a title alone when it reopens the session), and
retitling to the *same* title is allowed because that pin is still a useful write. A new title
that equals another conversation's title in any target sidebar is refused with no override -
two rows in one sidebar under one name is the state this tool spends effort removing - and
every check re-runs at `--apply` against the store as it is then, including re-enumerating
which accounts hold the conversation. The op journal records the complete prior bytes of every
row *before* any row is touched, so an interruption is recoverable in both directions
(`recover --back` restores every written row, `--forward` finishes the rest) and `undo` is
all-or-nothing and exact - including fields the rename never touched. `--store` narrows the
rename to one account for repair work; because that can leave the accounts reading
differently, the plan prints the sibling accounts' current titles and a warning when they
would diverge. This is a **sidebar** rename: the transcript's own `customTitle` is
deliberately not touched.

**`move`** - relocate a session to another project folder:

```
claude-code-sessions move 3c3c3eae-0e2f-4be4-9fba-407f06816f79 "C:\path\to\project" --apply
```

Get the full id from `claude-code-sessions list --full`.

**`undo`** - reverse the most recent completed operation:

```
claude-code-sessions undo --apply
```

**`recover`** - resolve an operation left non-terminal by a crash or interruption:

```
claude-code-sessions recover
```

**`sync`** - copy a session's sidebar **listing row** from your signed-in account into your
*other* Claude account's store on this machine, so it shows up in that account's sidebar too:

```
claude-code-sessions sync --apply
```

There is only one copy of the conversation itself - shared, and carrying no account identity -
so a synced session opens and resumes normally under the other account. Without `--to`, the
destination must be unambiguous: exactly one other account store on the machine, or `--to
<substring>` naming one of several by its account id, org id, **store path**, or - when it can be
recovered, see below - its email. The path is matchable because ids alone are not
always enough - Windows exposes two store roots (the MSIX package path and the classic
`%APPDATA%\Claude` path), and a machine that migrated between installers can hold the same
account under both, in which case the path is the only thing that tells the two copies apart.
Both "which store did you mean" refusals print the full path beside each candidate. `sync` refuses outright if it
cannot tell which account is signed in - either because neither `~/.claude.json` nor
`config.json` names one, or because the two disagree about which account it is. `--to`
cannot substitute for that: it only narrows *which* dormant store to use, and if we don't know
which account is live we cannot verify the one you named isn't it. A disagreement prints both
files' 8-character id prefixes; the fix is to re-authenticate the CLI (run `claude`, then
`/login`) as the account you're using, or switch the desktop app to that account, so the two
files agree.

When the two files **disagree**, there is a faster path than `/login`: you know which account
your desktop app is signed into, and `--live <substring>` asserts it (an account id, org id,
store path, or email - the same matching as `--to`; the printed account form works too,
`aaaa1111/cccc3333`, resolved as two anchored prefixes rather than a substring; ambiguity is a
refusal listing the candidates). For `sync` the assertion is store-strict - the resolved store
is the source sync reads, so a wrong org half reads the wrong store - while `converge --live` resolves at
*account* scope: RULING 5's certified fact is which account the app is on (`config.json` does
not even record an org), so an email or account id is enough there even when that account owns
several org directories, and the assertion carries no org at all (it renders as `aaaa1111/-`,
never a concrete pair to re-paste). It is deliberately not a `--force`: for `sync` it works
*only* while the files disagree, it must name one of the two accounts they name (anything else
is refused - an account neither file names is evidence of something else being wrong), and the
dry run, apply, `--json` (stderr), `undo`, and `recover` output all shout that the override was
used and which file it overrode. `converge` alone relaxes the first clause: a `--live` naming
the very account the files *agree* on - and no other account on the machine - proceeds with a
note instead of a refusal, because nothing was arbitrated and refusing certifies nothing; its
manifest records no assertion for that case. The assertion is journaled with the operation and
re-checked against the identity
files before every write, undo, or recover it ever performs; if the disagreement has changed
or vanished by then, the operation refuses rather than trust a stale assertion. The
running-app guard is completely unaffected: `--live --apply` still refuses while the desktop
app is running. Design rationale in `docs/internals.md` (RULING 5).

`--json` prints the plan by itself, the same as the default dry run. Combined with `--apply` it
runs first and describes what actually happened instead - real `written` flags per row and a
`result` key, not the plan it would have executed. Automation that assumes `sync --json` output
is always a preview will misread `sync --apply --json`.

- **Sessions you deleted in the destination stay deleted.** The app writes a small record
  (`deleted_<id>`) when you delete a session but does not consult it when a row for that session
  reappears elsewhere - confirmed by restoring a deleted row alongside its own record and
  reopening the app, which showed the session again. `sync` reads the *destination's* records and
  skips any source session they cover, and names each skip in its report (`kept deleted: …`).
  `--include-deleted "<title-or-id>"` overrides the skip for **one** named session; it never
  applies to a whole run. The name must resolve unambiguously - a full id, or a title
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
  them. That proves the *app* tolerates the field's absence - it does not prove the *destination
  account* has the same connectors configured. A session that relies on one opens fine either way
  and fails at its first tool call if the integration isn't set up there too; set it up in the
  destination account. The default transform also **resets the row's permission state**
  (`alwaysAllowedReasons`, `sessionPermissionUpdates`, `chromePermissionMode`,
  `chromeTabGroupId`) to its defaults: a permission you granted under one login was never granted
  under the other, and the worst case of resetting it is a re-prompt.
  `--verbatim` skips the whole transform - it copies connector config **and those permission
  grants** across the account boundary unchanged. The permission half is the more
  security-relevant of the two: use `--verbatim` only when you actually want the second account
  to inherit what the first one had allowed.
- **A row's link to its parent is translated, not copied.** A row `sync` adds carries the same
  translated link and chain that `converge` writes (see `converge`, below), or none, and the plan
  prints the same `links   :` and `chains  :` lines. A refresh replaces the whole row, link
  included, so the destination's own link is dropped or translated, never merged. A refresh that
  drops it or points it at another row says so, one line per row (`link    : local_c1 loses its
  link`, `link    : local_c1 now links to another row`, and `chain   :` for the chain), and the
  fields line leaves the link out so it is reported once. One exception: a destination link that
  already names the same row and `taskId` is kept as it is. A refresh whose bytes already match
  the destination's row is reported unchanged, unless the source row carries a link or a chain:
  then the link is translated first, and the row is written when the translated link differs, so
  a copy that names a row in the source account is corrected. (Under `--newer-only` such a row
  meets the age holds first, like any refresh.) In the window the sync pane shows the
  same lines. An apply that ends with nothing to write says "Nothing to copy: the destination
  already holds these rows" and leaves Undo as it was.
  **`--verbatim` copies links as they are**, so a link may name a row that does not exist in the
  destination. Such an add is counted: `links   : 2 copied verbatim (not translated)`.
- **It needs the app closed too, the same as `move`, `undo`, and `recover` - regardless of which
  file resolved the signed-in account.** `sync` only ever writes the store you are *not* signed
  into, and re-checks that at the moment it writes, not just when it planned; that re-check
  catches an account *switch* between planning and writing, but it is the same determination run
  again, not an independent second opinion. An earlier version of this tool trusted
  `~/.claude.json`'s `oauthAccount` enough to skip the running-app check whenever that file
  named an account outright. A real desktop account switch measured `oauthAccount` staying
  *stale* while `config.json`'s `lastKnownAccountUuid` tracked the switch - the opposite of the
  trust ordering that exemption assumed - and a review had already constructed the reverse
  (`config.json` stale, `oauthAccount` fresh) in a synthetic store: either identity file can be the stale one, and
  neither is trusted to certify "the destination is dormant" while the app is running. `--apply`,
  `undo`, and `recover --back` on a sync op therefore all refuse whenever the Claude desktop app
  is visible, whoever the files say is signed in. The dry run still labels a source resolved from
  `config.json` rather than `oauthAccount` (`from  (from config.json)`, plus a warning line) -
  that's a provenance note now, not a stronger/weaker gate: the guard applies the same way either
  side. A running Claude Code CLI session does not trip it - only the desktop app does. Closing
  the desktop app removes the check; signing *into* it does not, since the check is about
  whether its process is running, not which account it's signed into.
- **The destination's email is best-effort.** `~/.claude.json` names only the account you are
  signed into, so for the *other* account `sync` looks in the per-account Claude Code config the
  desktop app leaves inside its local-agent-mode sandbox
  (`local-agent-mode-sessions\<accountUuid>\…\.claude\.claude.json`), and uses its email only if
  the account id inside matches. That directory exists only for an account that has used local
  agent mode, so it is not always there - when it isn't, the dry run prints `(email unknown)`.
  Either way both endpoints also print their account/org id prefix and their full store path, so
  you always have a physical folder to recognise: the home directory becomes `~` and each id is
  truncated to 8 characters (e.g. `~\AppData\...\claude-code-sessions\aaaaaaaa…\bbbbbbbb…`)
  unless you pass `--verbose` for the paths and ids in full. Check the path, not just the email,
  before `--apply`.
- A synced row is a **snapshot**: title, last-activity time and turn count live in the row
  itself, so a session you keep using shows its copy-time state in the other account. **`sync
  --update` refreshes those rows** - it is the only route that overwrites rather than adds, so
  it is opt-in per run (an unticked checkbox in the window) and lists every overwrite before
  doing it. A row the destination account changed since planning is refused, never
  overwritten, and `undo` puts the exact replaced bytes back - for as long as the operation
  stays in the journal, which keeps the ten most recent finished ops. Two things worth knowing
  before you tick it: a refresh replaces the **whole** row with this account's copy, not just
  its title and timestamp; and because each account keeps its own snapshot, the copy you are
  overwriting is not automatically the older one, so any refresh that would move the other
  account *backwards* is called out by name in the plan - and **`--newer-only`** (in the
  window, "only where mine is newer", ticked by default) holds those back instead of sending
  them, along with any whose direction cannot be determined. Both sets are listed by name, so
  you can see exactly what was not sent. **A stale row never truncates the conversation it
  points at - but it can point at an entirely different one.** A row's filename is the app's
  session id, which survives being resumed, while the transcript it names changes on each new
  run; each account records whichever transcript it last saw. So the same sidebar entry can be
  a 1,386-message conversation from today in one account and a different 738-message one from
  last week in another - the newer one intact on disk but unreachable from that sidebar. On a
  real three-account machine, 15 of 333 shared rows were in exactly that state. **That is the
  strongest reason `--update` exists**, and the reason direction matters: refreshing *from* the
  account holding the older pointer hides the newer conversation from the account that had it,
  which is what `--newer-only` prevents. The plan says which refreshes **open a different
  conversation** rather than just updating a title, and if the conversation being displaced is
  reachable from no other account, that refresh is **held back and named** unless you pass
  `--allow-orphan` (window: "allow hiding a conversation", off by default) - the mirror of
  `--include-deleted`, for the case where a refresh takes access away instead of updating
  something. (`completedTurns` is not a count of your conversation and should not be read as
  one: rows showing 17 and 33 sat in front of a transcript holding 472 messages.)
- **"Already there" is decided by filename, not by conversation.** A row counts as present in the
  destination when a file of the *same name* (`local_<appSessionId>.json`) exists there. That is
  exactly right for rows `sync` itself copied, since it copies the name along with the contents.
  But a destination row pointing at the same conversation under a *different* local id - one
  placed by an earlier hand-run script, say - is not detected, and `sync` would add a second row
  for the same session, showing it twice in that account's sidebar. Deleting the duplicate in the
  app is enough to fix it.
- Sign into the other account (or restart the app) to see the results.

**`converge`** - create the missing sidebar rows so every conversation any account can open is
openable from *every* account:

```
claude-code-sessions converge --apply
```

`sync` copies rows **from the account you are signed into** into one other account, so levelling
three sidebars means signing into each in turn, app closed for every write. The recurring chore
is narrower than sync: new sessions exist only where they were created, so `alignment`'s
*complete* line decays with every burst of work in one account - measured, 21 conversations went
short within two days of full convergence. `converge` derives the whole target set itself -
every (conversation, account) pair where the transcript is on disk, *some* account holds a row,
and that account holds none - and creates exactly those rows in one pass, from any holder,
without the sign-in dance. There is deliberately no `--to`, `--from`, or `--store`: naming a
direction is the interface this command exists to delete. `--only` narrows to one conversation,
resolved the way `retitle --only` is.

**Purely additive.** It never repoints (accounts that disagree about what a row opens keep their
disagreement - measured across four such groups, both sides held real unique work, so "pick a
winner" destroys by construction), never refreshes (a stale row is `sync --update`'s job), never
deletes, never resurrects. Each new row is synthesized from the transcript the way `new-row`
builds one - nothing is cloned from a holding account, so focus times, archive flags, and
permission state never cross accounts. The title comes from the holder whose row shows the
newest conversation activity; when holders disagree about the name, the plan flags it and prints
the `retitle` command that would level them.

A pair is **held** - reported with a reason, never silently skipped, never fatal to the rest -
when creating the row would damage the destination sidebar: chiefly when the chosen title
already names a *different* conversation there. The comparison is the same trimmed-exact
comparator `alignment` counts duplicates with, so a converge can never raise that count - and
there is deliberately no auto-suffix, because a `(2)` is a collision avoided, not a name. Each
collision hold **measures the two conversations against each other** - the same prose-turn
overlap `doctor`'s duplicate report prints - and when the measurement is decisive the pastable
fix arrives complete, naming the *superseded* leg whichever side of the hold it sits on, with a
title generated from the store's own convention:

```
   s-early12 -> alice@example.com (aaaa1111/cccc3333): held_title_collision - 'ACME-REVIEW session' already names a different conversation in that sidebar: row local_….json (opens s-late34)
      measured: s-early12 is the earlier leg - 52 of its 53 prose turns continue in s-late34, which adds 11 more; last activity Aug 28 vs Aug 29
      claude-code-sessions retitle --only s-early12 --title "ACME-REVIEW session - earlier leg (Aug 24-28)" --apply
```

One retitle clears every hold of a two-leg group, and the current leg keeps its name. The
measurement is advisory by construction: it runs only when holds exist, its added reads are
bounded by a plan-wide byte budget, and every inconclusive or degraded state - overlap between
the bands, overlap and recency disagreeing about which leg is older, a generated name that is
taken or not shell-safe, more than two legs on one name - falls back to exactly the old
placeholder remedy plus one reason line, so silence is never ambiguous. Two conversations that
merely share a name are said to be that (`largely distinct … both need human names`); nothing
is ever renamed automatically. An applied run that ends with holds **exits 3**: bulk and
unattended is exactly where an exit code gets trusted without reading prose. The plan ends with
truthful math projected from what will actually be written -
`complete : 337 of 359 -> 356 of 359 (3 held)` - never a promised full house.

The dry run also discloses the one apply-time refusal that is fully knowable at plan time:
when `~/.claude.json` and the desktop's `config.json` disagree about the signed-in account,
the plan says so, with each side's remedy as a pastable command - in 0.12.0 the plan could
read `0 held` while `--apply` was always going to refuse (RULING 5), and discovering that
cost a full close-the-app cycle:

```
warning : ~/.claude.json (aaaa1111) and config.json (bbbb2222) disagree about
   the signed-in account. While that disagreement stands, --apply will refuse
   (RULING 5) unless you assert which account the desktop app is on:
      claude-code-sessions converge --live alice@example.com --apply    (if the app is on aaaa1111)
      claude-code-sessions converge --live bob@example.com --apply      (if the app is on bbbb2222)
   A refusal writes nothing, so trying --apply without --live is safe.
```

The dry run still exits 0 - disclosure and enforcement are different jobs at different times:
the warning is a snapshot, the apply re-evaluates the files fresh under its lock either way,
and a chained `converge && converge --apply` stops at the apply's own refusal with nothing
written. `--json` carries the same fact as an `identity_disagreement` field, which is the
machine caller's signal.

The whole run is one journalled operation, and every guard - the running-app check, identity
resolution, store re-discovery, per-pair revalidation - passes *before* the record is written,
so a refused or zero-write run (everything already present, or everything held) leaves no
journal residue at all. An interrupted run recovers in both directions: `recover --back`
removes the rows it created, `recover --forward` re-evaluates the remaining pairs against the
store as it stands rather than replaying the plan. `undo` of a completed converge is
deliberately *forgiving* where `retitle`'s is all-or-nothing: it removes the rows the op
created but skips, by name, a row that is now the conversation's only pointer in any account,
a row someone has repointed, and a row someone has retitled since - presence it created that
stopped being redundant is presence it leaves alone. `doctor`'s vanished-row check watches
converge-created rows the same way it watches `new-row`'s, with the same journal-retention
window.

**Side sessions keep their parent.** A side session's row names its parent's row (`spawnedFrom`),
and a row deep in a chain of them names the chain's first row (`startChain`). Both name a row by
its id in the same account, so a copy would point at a row the other account does not have.
`converge` and `sync` therefore write them translated. The link becomes
`{"sessionId": <that account's own row for the parent>, "taskId": <copied unchanged>}`, and the
chain becomes `{"firstSessionId": <that account's own row for the root>, "depth": <copied>}`.
Everything else in the source link (`title`, `sideSession`, `name`, `sharedFolder`, `wakeParent`,
`pendingPop` and any key this tool has not seen) is left behind and recorded. The `taskId` is not
optional: on the one side session measured per link shape, a link without it hid the child from
the sidebar, and a link with it nested the child under its parent. Only the link shapes that were
measured are carried: an app-written side-session link, the older `{sessionId, taskId, title}`,
and this tool's own output, so a carried link can be carried again.

The row is still created when its link is left out, and the plan says why. A link is left out
when the holders disagree on the parent or on `taskId`; when it names the conversation itself;
when the parent's transcript is gone; when that account has no row for the parent, or the
parent's own row is held in this plan, or its row there opens another conversation; when the
link's shape is not one that was measured; or when it would close a cycle. A link is written only
to a row that will exist, including a parent created in the same run, which is written first.
The plan reports it:

```
links   : 4 to carry, 1 left out (holders disagree on the parent 1)
chains  : 2 to carry
cycles  : 1 already present, left as they are
```

Each part prints only when its count is not zero, the most frequent reason first. A row whose
link was left out carries `[link: <reason>]` on its own line. The apply resolves every link
again under the lock, against the store as it then stands, and its last lines say what changed
since the plan: `links   : 4 carried, 1 left out (...); since the plan: 1 changed, 1 gained`.
`changed` and `dropped` count links the plan had, `gained` one it did not. With `--verbose` the
apply also lists them, one line per written row whose link or chain was left out or changed
since the plan, with its account:

```
   local_c3 -> bob@example.com (cccc1111/dddd2222)   [link: the parent's own row is held in this plan; dropped since the plan]
```

The journal records the final link per row, and recovery never resolves again. It writes the
journaled bytes, skips a row (`parent_not_written`) whose planned parent it did not write, and
skips one (`parent_changed`) whose parent's row is gone or now opens another conversation.
`recover --forward` names each row it skipped that way, with its reason, in the `skipped :` line
`converge` prints for a skipped row.

`undo` can leave a link dangling. When a row it created has become the last pointer to its
conversation, `undo` keeps it, while it still removes the parent row the same operation created.
It says so, for `undo` of a `converge`, a `sync` or a `new-row`, and for `recover --back` and a
finished `recover --forward`:

```
link left dangling: local_c1 (its parent's row was removed)
```

A child whose parent's row is missing is listed on its own and opens, which was measured. A
chain root's row that is missing is reported the same way ("its chain root's row"), but how the
app shows that was not measured. `link redirected: ... (its parent's row now opens another
conversation)` names a link whose target row survives and now opens a different conversation,
and `links not checked: N could not be read` counts what the report could not read. The report
covers rows that link to a row the command removed, and for `recover --forward` the operation's
own targets. It is not a check of every link in the store. A reversal cut short and run again
counts a row the first run removed as removed, so a link to it is still named. The window's Undo
shows the same lines, for a copy and a converge alike.

**`realign`** - point every account's row of a split conversation at the folder holding its
live transcript copy:

```
claude-code-sessions realign
claude-code-sessions realign --apply
```

These are the split rows `doctor` reports. A probe on Desktop 5.11492.5, run on one plain and
one worktree conversation, found that a row's `cwd` alone decides which copy it opens; that the
app rewrites the row's `folderPlace` to match `cwd`, and never writes `cwd` back; and that a
turn typed after `cwd` moved runs in the new folder, in the same transcript. So `realign`
rewrites `cwd`, and `folderPlace.cwd` and `folderPlace.realPath` where the row has a
`folderPlace` (it never adds one), and leaves every other key alone, including `originCwd` and
the title. The rewritten rows then open the live copy. No row opens the stale copy any more, so
it moves into the operation's journal, with its sidecar folder, where `undo` can put it back
and `doctor` lists it. Without that step, `move`, `repoint`, `new-row` and `converge` would
keep refusing the conversation for its duplicated transcript. A copy that no row opened even
before `realign` stays where it is: that is the duplicated-transcript case, which `realign` did
not cause.

By default it plans conversations with a copy behind the live one, and rows whose folder holds
no copy. Copies that hold the same conversation (byte-identical, or different only in
bookkeeping records) are realigned only with `--include-identical`, onto the copy the most
recently active row opens. `--only` narrows to one conversation, resolved the way `converge
--only` is. A conversation is **held**, with the reason printed, when:

- its copies have diverged (each holds turns the other lacks), so which history to keep is
  your decision;
- a copy no row opens holds turns the live copy lacks;
- a row to change has a worktree link (`worktreePath` or `worktreeName`). In the probe, moving
  such a row's path keys either detached it from its worktree or left it unable to start;
- the live copy is reached only through the worktree placement, sits in a legacy-encoded
  folder, or has no path on record that encodes to its folder;
- the paths that reach the live copy's folder disagree, or its directory no longer exists;
- an unfinished operation involves it, or anything about it changed between plan and apply.

`--apply` needs the desktop app fully closed, re-plans under its lock and writes only the
conversations whose rows, copies and target are unchanged, prints any it held at that point,
and exits 3 when any conversation was held. An interrupted run recovers in both directions:
`recover --forward` finishes from the journal, and is offered only when every row and copy it
would touch is as the journal expects; `recover --back` puts each conversation's copies and
rows back, and keeps realign's placement for a conversation whose copy cannot go back (its
place is taken, for instance), saying why. `undo` works per conversation. It puts back only
the keys realign changed, keeps whatever the app wrote to other keys since, and refuses a
conversation, naming the reason, when its rows no longer hold what realign wrote, now open
another conversation, were written by a later operation, or when the conversation has gained
turns since (bookkeeping records do not count). The other conversations go ahead, and a later
`undo --id <op_id> --apply` retries the refused ones; every hint to resume names the op, since a
bare `undo --apply` takes the latest completed operation, which may by then be another.

A copy realign sets aside stays in its journal until `undo` puts it back or `realign --release`
sends it to the Recycle Bin; `doctor` lists every copy held. Once a conversation has gained
turns, undo refuses it for good, which is what release is for:

```
claude-code-sessions realign --release
claude-code-sessions realign --release --apply
```

A copy is released only when every row of its conversation now opens one copy, that copy holds
the set-aside copy's whole conversation (the bookkeeping records `tools-used` and `cost-state`
aside, which carry no conversation), and its sidecar folder holds every file the set-aside
copy's does, with the same content: a copy that holds anything else the live one lacks stays in
the journal, with the reason printed. It goes to the Windows Recycle Bin, and the step is built
so that a permanent delete cannot happen silently. A shell that cannot recycle a file destroys
it instead. So release refuses a bin turned off (by you or by policy), one whose capacity the
registry does not record, one without room for the whole run without pushing out your older
deleted files (asked again just before each move), and a path too long for the shell, and it
asks Windows to warn rather than delete in any case it cannot see. After each move it checks
that a new bin record names the file, with its size and its contents present. It needs the
desktop app closed, since its checks read the rows and transcripts the app rewrites. A copy
whose release is interrupted is finished by the next run, and only after the same checks; once
its transcript is in the bin, only while the live copy still starts with everything it held
when the release began. One gap is stated rather than closed: a case the checks cannot see
rests on Windows' warning, whose behaviour beside the silent flags release uses is untested. Once
every copy of an operation is released, the journal ages the operation out like any other;
`undo` refuses a conversation whose copy was released or is part-way through release, and
`--json` gives each copy's outcome, a failed run included.

A released copy can be restored from the Recycle Bin by hand until the bin is emptied or
Windows clears it to make room. It comes back to its journal folder, not to its project folder;
the operation's record names where each part went. Windows only, like every other store mutation
here.

**`relink`** - give a side session's existing rows the parent link another account's row
carries:

```
claude-code-sessions relink
claude-code-sessions relink --apply
```

Since 0.18.0, `converge` and `sync` write a side session's link to its parent (`spawnedFrom`)
and its chain (`startChain`) on the rows they write, translated to the destination's own rows
(see "Side sessions keep their parent", above). Rows that already existed were left as they
were. So a side session's row made before 0.18.0 can lack the link in one account while
another account's row of it carries one, and that account's sidebar then lists it as a session
of its own rather than under its parent. `relink` gives such a row the link, translated exactly
as `converge` writes it on a new row, and the chain beside it when one resolves.

It plans each conversation that some account's row links to its parent in a shape `converge`
carries. A row of that conversation gains the link when it has no `spawnedFrom` object (the key
is absent, or holds a non-object such as `null`) and its own account holds a row for the
parent. The other accounts' rows are the holders `converge`'s resolver reads, so a link is left
out for the reasons `converge` gives above, in the same words. A row gains a chain only beside
a link written in the same run, or beside a link of its own that agrees, so a row whose link is
left out gains no chain. Archived rows are included. Beyond the resolver's rules:

- Agreement is judged across every account's rows of the conversation before any row is
  resolved. When their links name different parents or give different `taskId`s, a row's own
  link among them, every row of the conversation is left out (`holders disagree on the
  parent`, `holders disagree on taskId`).
- `the row's own link names no usable row`: the row already has a link object, and it names no
  row of its own account, or lacks the `taskId` the accounts agree on. The row gains nothing.
- `no app-written link backs it`: none of the other accounts' rows that name the parent carries
  a link in one of the two shapes the app writes, a link with `sideSession: true` or exactly
  `{sessionId, taskId, title}` with a non-empty title. This tool's own `{sessionId, taskId}`
  copies support a link but never justify one alone, so a row whose app-written link was taken
  away is not linked again from them. This has two limits. The provenance is read from the
  link's shape, not proved. And while another account still holds an app-written link, a row
  whose own account's app removed its link is linked again, because a snapshot cannot tell a
  deliberate unlink from a link never received. `--verbose` lists every row, so you can read
  them before you apply.
- `the row's own chain disagrees with its holders'`: the row already has a chain object, the
  other accounts' rows carry chains, and the row's does not name a row for the one root and
  `depth` they agree on (or they agree on none). It counts on the `chains  :` line, and the row
  gains nothing. A row's own chain stands, unjudged, when no other account's row carries one.

Only `spawnedFrom` and `startChain` change. A key the row lacks is added at the end, a
non-object is replaced where it stands, and a link or chain object the row already has is never
replaced, even one that disagrees. Every other key, the app's own included, keeps its value and
its place. The file is rewritten as compact JSON, as `realign` writes rows, so its bytes change;
the apply reads each row back and checks it by value.

Before the release, `relink` ran on the machine it was built on, one row at a time first. Seven
rows went one by one, each with its own apply: two shaped like the app's own (one of them in a
second account), one in `converge`'s shape, one that gained its chain beside the link, an
archived child, a child whose parent row is archived, and one whose holders' links carry
`sharedFolder`. Each was looked at in the app, then again after a quit and reopen, and read back.
The children nested under their parents. The archived child stayed among the archived sessions
and out of the sidebar. The child of an archived parent stayed at the top level of the sidebar
rather than disappearing; whether it nests once its parent is unarchived has not been looked at.
An undo of the row with a chain took out the link and the chain and nothing else, and the child
went back to the top level; the same row was then linked again. The rest went in one run: 924
rows gained a link and 41 a chain across three accounts, and a second dry run found nothing
more to add. 120 rows stayed out, every one because its holders disagree on the parent. Opening
a session made the app rewrite keys of its own (moving an old session to the current model,
recording that it could not resume one into a worktree folder that had been removed), and every
link and chain still read back as written. The apply's last line still asks for a look rather
than saying the rows nest:

```
Reopen the app and check that the relinked rows show under their parents. 'undo' takes the links out again, key by key.
```

A dry run reads every row of every account's store, about 400 MB across the three account
stores the design measured, and lists every transcript name. One row anywhere that cannot be
read refuses the whole run, as it refuses `converge`: an unreadable row could be a holder or a
target, and "couldn't look" is never "nothing there". So does an account with rows in two org
folders.

`--only` narrows to one conversation, by a title substring or a `cliSessionId` prefix, or to
one row, by its row id (`local_` and a prefix: the listing's first column). `--account` narrows
to one account, the signed-in one included: an account id, org id, email or store path
substring, or the `acct/org` pair the listing prints. A value that matches no account, or two,
refuses and lists every account directory. Either option picks only which rows are written and
listed; agreement, holders and targets are always read from every account. `--live` asserts
the app's account when the identity files disagree, at account scope as for `converge`, and
the dry run reports the disagreement first, in `converge`'s words. `--verbose` lists every row,
not only the first 15 per account, and each row left out with its reason. `--json` prints the
plan without the link objects: each row names the keys it `adds` and `keeps`, and its
`lineage` record the rows they point at.

```
-> alice@example.com (aaaa1111/cccc3333)
   local_c1  'ACME-REVIEW side task'   link under 'Northwind kickoff'

-> bob@example.com (cccc1111/dddd2222)
   local_q2  'Northwind follow-up'     link and chain
   local_c2  'ACME-REVIEW side task'   link under 'Northwind kickoff'

links   : 3 to add, 3 left out (holders disagree on the parent 2, the row's own link names no usable row 1)
chains  : 1 to add
cycles  : 1 already present, left as they are

dry run - pass --apply to add them
```

The counts are of rows, and each part prints only when it is not zero. A row whose link is
added while its chain was left out ends `[chain: <reason>]`. A plan that writes no row prints
`nothing to add` and journals nothing. Try the apply on one row first, named by its row id:

```
claude-code-sessions relink --only local_c2 --apply
```

A conversation `--only` that would change more than one row refuses and lists their row ids, so
a proof never touches two rows; a row id that two accounts hold needs `--account` beside it.

`--apply` needs the desktop app fully closed and no unfinished operation in the journal, and
runs on Windows only, as every store mutation here does. It prints the plan, then makes it again
under its lock against the stores as they then stand, and writes what that plan says. Its last
lines count, in rows, what changed between the two, as `converge`'s do:
`links   : 4 added, 3 left out (...); since the plan: 1 changed, 1 gained`. So an apply reads
every store twice, and the app stays closed through a full read of them as well as the writes.
How long that takes on a large store has not been timed. A run that leaves rows out still exits
0: a left-out row is one relink will not change, and holders that disagree stay that way until
someone changes a row, so a non-zero code would follow every run. A refusal exits 1. A second
run can add what the first made possible, a chain that resolves only through a link the first
run wrote, so run the dry run again after an apply.

While any operation in the journal is unfinished, of any kind, `relink` refuses, its dry run
included, and a relink's `undo` and recovery refuse while any other one is, each naming the
operation to recover first. An interrupted `converge` or `sync` rolled back after a relink could
delete a parent row a new link names, and one finished forward would meet rows a relink
changed. The cost is running `recover` first. A journal record that cannot be read holds them
the same way, named by its folder, and `recover` cannot clear it, since it cannot read that
record either: with the app closed, repair the record or move its folder out of the journal.

`undo` takes the links out key by key. A key that still holds what relink wrote goes back:
removed where the row had none, set back to its exact old value where it held a non-object. A
key the app has changed since is left and named, as is a row that is gone or now opens another
conversation, and every other key stays as the app now has it. A link beside a chain the app
changed stays too, named, since a chain without the link it follows is a shape relink never
writes; a chain the app took out lets the link go:

```
left    : local_q2 -> bob@example.com (cccc1111/dddd2222): startChain (the app changed it since)
```

A key that a later operation wrote since waits, whatever the row holds now, because that
operation's own undo may put relink's value back. So does every key of a row that cannot be
read. The undo takes out the rest, prints `result: partial`, names each key that waits and what
it waits on, says what to do next, and exits 1:

```
result: partial
waiting : local_c2 -> bob@example.com (cccc1111/dddd2222): spawnedFrom (operation(s) <later_id> wrote it since)
Undo operation(s) <later_id> first; then run 'undo --id <op_id> --apply' again. If one of those operations cannot be undone, the keys it claims stay linked for good.
```

The relink stays `completed`, and `undo --id <op_id> --apply` goes on from what each row holds;
a key it has finished is never written again. Name the op: a bare `undo --apply` takes the latest
completed operation, which may by then be another. A `retitle` cannot be undone once the app has
rewritten any row it wrote, so the keys it wrote stay linked. A `realign`, which writes other
keys, and a finished `undo`, which no command can undo, do not count. When every key left
waits, so nothing can go, the undo refuses instead and changes no row. A journal record that
cannot be read or is damaged refuses the whole undo, named by its folder (above), since it could
claim any key. Like every operation's undo, it works only while the relink is in the journal,
which keeps the ten most recent finished operations; once an undo has begun, the journal keeps
the relink, and every record naming a row it has not finished, until the undo ends. `relink` is
CLI-only, and while a relink is the latest operation the window offers no undo, as for
`realign`; an operation before it, and a partial undo's resume, run from the CLI.

An interrupted apply recovers in both directions, by what each row holds now. `recover
--forward` counts a row that still opens its conversation and has a key already at relink's
value as written, writes a row still as planned once its parent's row and the account's links
pass again, and skips and names anything else:

```
skipped : local_c2 -> bob@example.com (cccc1111/dddd2222): changed_since_plan - changed since the plan
```

It then names any link that landed whose target row has gone, or now opens another
conversation. `recover --back` takes out every key that landed, key by key as `undo` does, and
names what it left on its `reason:` line; once a `--back` has begun, only `--back` is offered.
Either direction needs the app closed only when it will write a row, and refuses a row that
changed while it checked.

## Safety design

- **Dry-run by default.** Every mutating command prints its plan and does nothing until you
  pass `--apply`.
- **Journaled copy-verify-commit-delete.** A move journals its complete intended state before
  touching anything, copies to the destination, re-verifies the copy by hash, rewrites listing
  rows atomically, re-verifies both sides one last time, and only then deletes the source.
  Nothing is ever deleted while it is the only copy.
- **`undo`** reverses the most recent completed `move`, `sync`, `repoint` or `new-row` by
  running the same journaled protocol in reverse - for a `sync`, deleting exactly the rows the
  op wrote; for a `repoint`, restoring the pointer the row held before; for a `new-row`,
  deleting the row it created. In every case only while what is on disk still matches what was
  written.
- **`recover`** classifies and resolves any operation a crash or interruption left in a
  non-terminal state - nothing is left stranded.
- **`sync`** only ever writes rows into the store of the account you are *not* currently signed
  into, re-verifying that at the moment it writes as well as when it planned. It refuses outright
  if it cannot identify which account is signed in at all, or if `~/.claude.json` and
  `config.json` disagree about which account that is: `--to` names a destination, but without a
  confirmed, unambiguous live account there is nothing to check that destination against. The
  one sanctioned exception is `--live` (RULING 5 in `docs/internals.md`): while the files
  *disagree*, you may assert by name which account the desktop app is signed into - a fact you
  can verify and the files cannot - and the assertion is journaled, shouted in every output,
  and re-checked against the identity files before every mutation the operation ever performs.
  Every sync mutation - `--apply`, `undo`, and `recover --back` on a sync op - also refuses while it can
  see the Claude desktop app running, the same guard `move`/`undo`/`recover` use, applied
  regardless of which identity file resolved the signed-in account - and regardless of
  `--live`, which never touches this guard: staleness has been shown in
  both directions (`oauthAccount` measured stale across a real account switch; `config.json`
  stale in a review-constructed store), so no file evidence is trusted to certify the
  destination is dormant while the app is visible. A running Claude Code CLI does not count
  toward this - only the desktop app does.
- **Refusal philosophy.** The tool fails closed: an unrecognized on-disk layout, an unreadable
  row, an ambiguous encoding scheme, or a running Claude process is a refusal, not a guess.
  "Couldn't look" is never treated as "nothing there."

## Compatibility matrix

| Platform | Status | Mutations |
|---|---|---|
| Windows 11 + Claude Desktop | verified 2026-07-31; sync end-to-end incl. live continuation 2026-08-03; sync-undo drift refusal live 2026-08-04 | read-only + mutations |
| Windows 11 + Claude Desktop 2.42304.2 (user data moved to `%LOCALAPPDATA%\Claude-Data`) | from 0.16.3: read-only paths probed 2026-10-19 against the moved store. 0.16.2 and earlier find no store where the app moved the folder, and read the old copy where it copied the folder and left the original in place | read-only + mutations, `--apply` never run on this build (the app updated to 3.x first) |
| Windows 11 + Claude Desktop 3.6556.2 (user data still in `%LOCALAPPDATA%\Claude-Data`) | on 0.16.5: read-only paths and `converge --apply` verified 2026-10-23, the apply with the app closed and the browser helper running. Needs 0.16.3 or later to find the store | read-only + mutations |
| Windows 11 + Claude Desktop 4.4212.0 (user data still in `%LOCALAPPDATA%\Claude-Data`) | on 0.16.5: `doctor`, `alignment` and `converge --apply` verified 2026-11-01, the apply with the app closed and the browser helper running. Needs 0.16.3 or later to find the store | read-only + mutations |
| Windows 11 + Claude Desktop 5.1231.0 (user data still in `%LOCALAPPDATA%\Claude-Data`) | on 0.16.5: `doctor` and `alignment` verified read-only 2026-11-10. Needs 0.16.3 or later to find the store | read-only + mutations, `--apply` not yet run on this build |
| Windows 11 + Claude Desktop 6.6395.0 (user data still in `%LOCALAPPDATA%\Claude-Data`) | from 0.17.0: `doctor` and `realign --apply` verified 2026-11-17, the apply with the app closed; after the app reopened, `doctor` and a fresh `realign` plan found every rewritten row opening the live copy and no set-aside copy back in its old folder. From 0.18.0: `converge --apply` with carried links verified 2026-11-19, the apply with the app closed; the app nested both children opened, and after a reopen every carried link and chain read back as written. Needs 0.16.3 or later to find the store | read-only + mutations |
| Windows 11 + Claude Desktop 6.28899.6 (user data still in `%LOCALAPPDATA%\Claude-Data`) | from 0.19.0: `relink --apply` verified 2026-11-20 and 2026-11-21, each apply with the app closed: seven rows one at a time, then 924 more in one run. Each child looked at was where it should be, after a reopen too (nested, or for an archived child and a child of an archived parent, where it was before), and every link and chain read back as written. The update to this build came in after the first of the seven had been applied and looked at once, on 6.6395.0. `doctor` and `alignment` read-only 2026-11-21. Needs 0.16.3 or later to find the store | read-only + mutations |
| macOS / Linux desktop | unverified | **read-only only** - desktop-store mutations refuse, with no override |
| CLI-only sessions (any OS) | transcript layout verified | mutations allowed via `--transcript-only` |

The dates are measurements against specific builds of the MSIX that Anthropic signs and
distributes itself from claude.ai - most recently Claude Desktop 6.28899.6.0 for `doctor` and a
`relink` on 2026-11-21, and 6.6395.0.0 with Claude Code CLI 2.1.326 for a `realign` on
2026-11-17 and a `converge` on 2026-11-19. The Microsoft Store build is a separate package, `AnthropicPBC.Claude_<id>`.
This tool does not look in that package's folder and has never been run against it.
On 2026-10-14 the
read-only paths were verified against a real Desktop 2.x store, build 2.37337.1.0 (`list`,
`doctor`, `alignment`, `recover` without `--apply`, and a `converge` dry run): the row format
had only gained keys, and every row this tool had written under 1.x was still present, some
already re-saved by the new app. Later on 2026-10-14 a `converge --apply` ran against that
2.x store with the app closed: after the app restarted, every row it had written was still
present and `doctor` reported none vanished. On 2026-10-17 two more `converge --apply` runs
went through against 2.42304.0.0 with the app closed, with the same result. That build moved
the browser-extension helper, which the running-app guard follows from 0.16.1 (see Roadmap).
On 2026-10-19, 2.42304.2.0 moved the app's whole user-data folder, session store included,
from the package's `LocalCache\Roaming\Claude` to `%LOCALAPPDATA%\Claude-Data\claude_<pkg>`,
and left the helper where it was. 0.16.2 and earlier report the store absent there. 0.16.3
reads the moved store and still finds the helper. Its read-only paths (`doctor`, `alignment`
and a `converge` dry run) were checked against the moved store the same day. 0.16.3 has one
known gap: rows this tool created before the move are journaled at their old paths, so
`doctor` reports them as "no longer on disk" although they moved with the store, and advises
running `new-row` again; for the same reason `undo` cannot reverse an operation from before
the move. 0.16.4 reads those paths under the moved store: `doctor` checks
the rows where they now are, and `undo` and `recover` treat such an operation as they did
before the move. A row in a store the tool cannot read at all is listed by `doctor` apart, not
counted in its exit code, and `undo` refuses it.
On 2026-10-23, under 3.6556.2.0, the data folder had not moved again, and rows written by the
3.x builds had only gained two keys. A `converge --apply` on 0.16.5 ran with the app closed while the browser
helper kept running, and the running-app guard excused the helper under the
`trust-signed-helper` opt-in. After the app restarted, every row the apply had written was
present and byte-identical to what it wrote, and `doctor` reported none vanished.
On 2026-11-01, under 4.4212.0.0, the data folder had not moved again. A `converge --apply` on
0.16.5 ran with the app closed and the browser helper running, and the guard excused the
helper as it had under 3.x. After the app restarted, every row the apply had written was
present and `doctor` reported none vanished. Within a few minutes the app had re-saved six of
those rows, and each had only gained keys. By 2026-11-02 the 4.x app had created 36 rows for
itself. None lacked a key that every older row carries, and four had gained one new key,
`isStarred`.
On 2026-11-10, under 5.1231.0.0, the data folder had not moved again, and `doctor` and
`alignment` on 0.16.5 found the store where 4.x left it. That build's release notes say
Windows 10 21H2 or later and Windows Server 2022 now keep the app's data in
`%LOCALAPPDATA%\Claude-Data` too, as the Windows 11 machine these measurements come from has
since 2.42304.2, so on those systems the move happens at 5.1231.0. If the folder layout there
matches that machine's, the same versions apply: 0.16.3 or later to find the store, 0.16.4 or
later for operations from before the move. The notes also say the Microsoft Store build keeps
its data where it was, and they mention moves that copied the folder instead of moving it.
Where such a copy leaves the old store in place, run this tool from an ordinary terminal: from
a shell the desktop app started, `%APPDATA%\Claude` is the app's view of its old folder, and
0.16.5 can read the old copy through it. Not measured here: a Windows 10 machine, a Microsoft
Store install, and an `--apply` under 5.1231.0.
The on-disk format has changed once already during this tool's own development; treat a
much newer build as unverified territory.

**On non-Windows, `move` and every `sync` route refuse to touch the desktop store, and there
is no flag to override that.** The layout is confirmed on Windows only; macOS reportedly has
two candidate layouts - the ordinary Application Support path and a sandboxed
`~/Library/Containers/…` one - and neither has been confirmed here. An override would let you
waive a risk you have no way to evaluate, which is the opposite of how every other refusal in
this tool works.

What *does* work there: `list` and `doctor`, which are read-only, and `move` for a session
that has **no desktop listing row** - a CLI-created one - via `--transcript-only`, because the
transcript layout is verified cross-platform. (Note `--transcript-only` does not force that
mode; it permits it when no row exists. A session that *has* a desktop row is not movable on
an unverified platform.)

**If you're on macOS and want this supported:** `claude-code-sessions doctor --verbose` output
in an issue is exactly what's needed. It's read-only, mutates nothing, and it reports the store
roots found and the layout recognised - which is the whole of what's missing.

The Windows row is an end-to-end check on a real store, not just a passing test suite:
a disposable session was moved between projects, the app was restarted and the session
resumed at its new location, `undo` correctly **refused** once that resume had appended
to the transcript (rather than discarding the new messages), and a deliberately
interrupted move was resolved in both directions with `recover`. Afterwards `doctor`
reported no new findings and the journal held no unresolved operations.

`sync`'s underlying mechanics were checked against two real, live accounts on this machine before
the command existed: rows copied by hand between them and confirmed visible in the destination's
sidebar, and - separately - a deleted session's row restored alongside its own deletion record and
confirmed visible again, which is the finding that makes `sync`'s tombstone-skipping mandatory
(see `docs/internals.md`). The `sync` command itself is covered by its test suite and, as of
2026-08-02, its own end-to-end `--apply` run against two real, live accounts: a row synced from
one account opened in the other with its full conversation history intact. That run did not send
a new turn through the synced row; a second verification on 2026-08-03 closed that gap - a live
session's own listing row was synced across accounts and the conversation was then continued
from the destination account's sidebar, new turns flowing through the synced row. And on
2026-08-04, `undo` of a synced row the destination account had since opened was refused on
real data: the row no longer matched what the op journaled, so the drift refusal declined
to delete it - exactly the designed behavior.

## What's stored locally

`~/.claude-code-journal/` holds the tool's own bookkeeping, never your conversation content:

- `ops/<op-id>/manifest.json` - the journal for each move/undo/recover/sync operation (paths,
  hashes, row pre-images, phase history). Rotated: the 10 most recent terminal operations are
  kept, non-terminal ones never pruned automatically.
- `ops/lock` - a single-instance lock held for the duration of a mutation.
- `moved-log.jsonl` - a tiny, append-only, never-rotated record of completed moves (session id,
  from-path, to-path, date), used to recognize your own past moves during future collision and
  encoding-evidence checks.
- `account-emails.json` - account uuid → the email that account had when it was **signed in**,
  with the date seen. `~/.claude.json` names only the live account, so the destination of a sync
  is precisely the account whose email is hardest to recover; but every account is the live one
  sometimes, so syncing in both directions teaches the pair. A remembered email is **labelled as
  remembered** wherever it is shown, because it says what was true when that account was last
  signed in, not what is true now - the store path beside it remains the identifier to check.
  Delete the file to forget everything; it is rebuilt as accounts are used.
- `trust-signed-helper` - present only if you enabled the opt-in above.

To purge everything the tool has ever written, delete the whole `~/.claude-code-journal/` directory.
This does not touch any transcript or listing row - only the tool's own journal.

**`--json` output is not redacted.** Plain-text output replaces your home directory with `~`
and truncates any UUID-shaped identifier (session ids, org/account ids in store paths) to its
first 8 characters by default - pass `--verbose` to see paths and ids in full. `--json` output
always contains full paths, titles, and ids, unredacted, so it can be consumed programmatically.
Do not paste `--json` output into a public issue or forum post - copy only the fields you mean
to share.

`sync --json` is the one place this discloses **a second account's** identifiers: unlike every
other command, its output carries the destination account's account/org uuids and full store path
in the clear, regardless of `--verbose`, because those are what the plan is *about*. The
plain-text report redacts them like everything else; the JSON does not. `--anonymize` is the
exception, because pasteability is its whole point: both account addresses become labels, titles
are labelled in the rows and the tally alike, the home directory folds to `~`, the row
images (`post_b64`, plus `pre_b64` on refreshes) are dropped from the JSON outright - a base64
blob embeds every title it carries and cannot be eyeballed in a paste - and every uuid, the
destination's included, truncates to its 8-character prefix, exactly as the plain-text report
prints them.

**`--anonymize` hides the *content*, which redaction never did.** The redaction above covers
the **machine** - your home directory and uuid-shaped ids. It has never touched **titles**, and a
title is a model-written summary of whatever the session was about, so it can name a client, a
legal matter or a medical one. Output that announced itself as redacted was therefore unsafe to
paste, and that gap is how real titles reached this repo's own documentation more than once.

`--anonymize` is available on every command and replaces each title, project path and account
address with a stable opaque label:

```
claude-code-sessions list --anonymize

c0ffee06…  <session-69f9>       <project-0c8f>
c0ffee07…  <session-9c06>       <project-1a20>
```

The labels are deliberately unreadable rather than plausible fake titles - a convincing fake read
back later is indistinguishable from a real one, and that confusion is the original problem. They
are stable (a hash of the value), so two pasted listings can still be compared to each other, and
ids are never labelled - they are random, and, truncated to their 8-character prefixes, they are
what makes the output useful.

It covers `--json` too, which no amount of care with plain-text output would have. Under the
flag - and only under it - `--json` borrows both of the plain-text redaction's machine-layer
rules: your home directory folds to `~`, and every uuid-shaped id - session, account and org,
standalone or inside a store path - truncates to the same 8-character prefix the text report
has always printed. Anonymized output exists to be pasted, `--json` never passes through the
plain-text redaction, and a payload that hides every title while printing your account name
inside `C:\Users\<name>\...` - or your full, globally-unique account uuid - would read as safe
without being safe. Truncated ids stay distinct and stable, so a pasted report is still
debuggable; plain `--json` without the flag is unchanged - still deliberately unredacted, as
above.

**What `--anonymize` deliberately does not mask**, so the paste review knows where to look:
strings shorter than 4 characters never enter the substitution table - a 1-3 character
fragment substring-matches everywhere, and the over-matching history above says a partial
replacement that corrupts surrounding output is worse than none - so a very short title
survives verbatim. Dates and counts pass through too: activity dates, prose-turn tallies, and
the date range inside a generated `… - earlier leg (Aug 24-28)` title are labels and
quantities, not identity, and masking them would anonymize the report into uselessness. Both
are accepted residuals, not gaps: eyeball a paste before publishing it, especially one whose
titles were very short.

Two combinations are refused rather than half-honoured:

| | |
|---|---|
| `--anonymize --verbose` | `--verbose` prints lines unredacted and never reaches the redaction path, so titles would appear anyway |
| `--anonymize --apply` | a plan is what gets pasted; an apply writes to a store, and a substituted title must never be mistaken for real data |

**Windows durability note.** The commit step fsyncs every file it writes before deleting the
source, but Windows has no equivalent of a directory fsync, so the directory-entry update
itself (the rename, the delete) is not separately forced to disk. The spec accepts this as a
residual risk rather than a defect: file-level fsync plus delete-last ordering means a power
loss in this window can only lose an already-fsynced deletion, never earlier fsynced writes -
worst case is a leftover duplicate, never lost data, and `recover` classifies exactly this
window into `journaled`/`completed`/`rolled_back`.

## Roadmap

- **`sync --update` shipped in 0.9.9** with the drift-refusal treatment this entry asked for:
  the plan records the destination's current bytes, the write refuses if they moved since, and
  `undo` restores them exactly. It turned out to matter more than "refresh a stale title": a
  row is a **pointer** to a transcript, and two accounts can point at different ones, so
  `--update` can restore access to a newer conversation - or, in the wrong direction, hide one.
  Hence `--newer-only` (on by default in the window) and `--allow-orphan` (off). **0.9.10**
  adds the number that makes that last decision judgeable: the plan says how much of the
  conversation a refresh would displace also appears in the incoming one, so a hold reads as
  "5% of its prose is in the incoming conversation" rather than only "would hide a
  conversation". See `docs/internals.md`, RULING 8.
- **`repoint` and doctor's orphan ranking shipped in 0.9.11**, from the case none of the above
  covers: the *app* repointed a row, while this tool was not running. Resuming a session under
  another account continues that branch and stamps its id into whichever store is active, which
  can leave the conversation you were in reachable from no sidebar. No guard here applies -
  the mutation never came through this tool - so what shipped is detection (`doctor` ranks
  conversations nothing points at) and repair (`repoint` puts a pointer back, journalled and
  undoable).
- **0.9.13 makes the swap report answer the question people actually ask.** A long real session
  on 2026-08-22 found three ways the plan described a pointer swap without saying what mattered.
  It now states the **length**: "this row goes from 388 to 189 prose turns - 199 FEWER", because
  a percentage is not a loss and every reader was converting one into the other in their head.
  Reachability is now checked **per row rather than per store**, so a conversation still held by
  another row *in the destination account* no longer reads as about to be orphaned - a false
  alarm whose only cure was ticking the override that guards the real case. And app-emitted
  plumbing (`[Request interrupted by user]`, `No response requested.`) no longer counts as
  conversation: three rows reporting 95-98% overlap turned out to have **zero** authored turns
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

- **0.11.0 adds `retitle` and `--anonymize`.** The case for `retitle` is not that renaming is
  hard but that renaming *outside* the journal had already failed twice: an August cleanup of 69
  rows went through a throwaway script, and the script's backups silently overwrote each other
  (one conversation's three copies share a filename across accounts, so second-resolution
  backup names collided - 33 writes produced 20 backups) while its `--undo` restored the newest
  run forever, so stacked runs could never be unwound. Both are defects the op journal already
  prevents for `move`, `sync`, `repoint` and `new-row`; `retitle` puts renaming behind the same
  machinery - complete prior bytes journaled before any row is touched, recovery in both
  directions, an all-or-nothing consuming undo - and renames the *conversation* across every
  account instead of one row in one sidebar. **`--anonymize`** exists because the redaction
  layer hides *where* things are, not *what* they are called: it replaces every title, project
  path and account address with a stable opaque label, so real output can be pasted somewhere
  public. It refuses to combine with `--verbose` (which bypasses redaction) and with `--apply`
  (a substituted title must never be mistaken for real data), which keeps it strictly a display
  concern.
- **0.12.0 adds `converge`.** The case is a chore with a measured recurrence: making every
  conversation openable from every sidebar meant generating a shell script of `new-row` calls -
  28, then 30, then a pending ~22, three times in one week - because `sync` only copies *from*
  the signed-in account and no command said "level them all" in one word. Each script had to
  rediscover the same four things (full store paths, because an account id matches several org
  directories; explicit titles, because derived ones collide; a tolerant-rerun wrapper, because
  "already there" is success on a rerun; a per-call journal, because there was no batch op);
  `converge` owns all four once - and the chore is permanent, because new sessions exist only
  where they were created and the *complete* count decays with every burst of work in one
  account. It is purely additive - never repoints, refreshes, deletes, or resurrects - which is
  why it may read and write every store in one pass without sync's sign-in dance. A title
  collision **holds** the pair rather than spreading a duplicate (the comparator is
  `alignment`'s own, so *distinguishable* cannot move by construction), every hold prints its
  pastable `retitle` fix, and an applied run with holds exits 3. Its undo is deliberately
  forgiving where `retitle`'s is all-or-nothing: it removes the rows the op created except one
  that became the conversation's last pointer anywhere, or that someone repointed or retitled
  since - deleting those would destroy work the undo was never asked to touch.
- **0.13.0 is the identity release: the tool takes its own word for an account.** Four changes
  from a third real re-levelling pass, all about which account the tool believes it is talking
  to. The converge dry run now discloses the RULING 5 identity disagreement at plan time, with
  a pastable per-account remedy; in 0.12.0 the plan could read `0 held` while `--apply` was
  always going to refuse for a reason fully knowable at plan time, and discovering that cost a
  close-the-app cycle. The `acct/org` pair form the reports print parses back as two anchored
  prefixes wherever an account can be named, so a printed label is always a valid input.
  `--live` resolves at account scope for converge (an email works), and asserting an account
  the files already agree on is a note, not an error. And the refusal texts now tell the
  measured truth about their own remedies: switching the desktop app "may not" refresh
  `config.json`, because it has been measured doing both.
- **0.14.0 measures the collision holds it already prints.** Every converge pass on a real
  three-account store has produced title-collision holds, because both routine gestures,
  resuming a session and rewinding to edit a prompt, fork the conversation and carry the
  pinned title across. Clearing them by hand took the same steps every time: measure the two
  legs' shared prose, conclude supersession, retitle the superseded one. The hold now runs
  that measurement itself (the same prose-turn overlap `doctor`'s duplicate report prints)
  and, when it is decisive, prints a complete remedy naming the superseded leg with a
  generated `<base> - earlier leg (<range>)` title, so one paste clears a two-leg group and
  the current leg keeps its name (three or more legs on one title refuse wholesale). Advisory
  by construction: bands, not a binary (supersession at 0.8 overlap or above, distinct at 0.2
  or below, everything between refuses as inconclusive with both ratios printed); the
  superseded side picked by overlap and corroborated by recency, never by clocks alone; a
  plan-wide byte budget on measurement-added reads; and every degraded state falls back to
  exactly the old placeholder remedy plus one reason line. The same release hardens
  `--anonymize` end to end: plan-chosen titles and the app-encoded project folders join the
  substitution table, `new-row`, `repoint` and `sync` honour the flag through their own public
  manifests, manifest schema keys can never be renamed by a terse title, anonymized JSON
  borrows both machine-layer redactions (the home fold, and 8-character uuid truncation), and
  the README now says what the flag deliberately does not mask.
- **0.15.0 rebuilds the window around the routine that actually runs.** The window had been
  `ccs sync` with buttons - two of the tool's twelve commands - while the maintenance routine
  a multi-account store needs (plan, name the title-collision holds, converge, confirm)
  stayed CLI-only. 0.14.0 made its one interactive step one-click-shaped: holds
  arrive measured, with a suggested title and a named target, fields designed with this
  window as their intended second consumer. This release is that consumer, and the order was
  deliberate - a window built before the measurements would have been a data-entry form
  built twice. Three tabs now. **Level** is the home: `alignment`'s five numbers, the
  converge preview, and each hold as a rename row prefilled from the measured suggestion -
  tick, edit, or ignore. Apply confirms in two stages, each describing exactly and only what
  runs next: first every `old -> new` mapping (each rename lands as its own journalled,
  individually undoable operation), then a **fresh** converge plan's numbers - never the
  preview's, which the renames just made stale. Every Apply ends in a re-measure, so the
  pane describes the store as it now is, not as it was planned to be. **Copy & refresh** is
  the sync pane moved intact - still the only tab that can overwrite a row, because converge
  is additive and deliberately never refreshes. **Health** keeps `doctor`'s report and adds
  the interrupted-operation net a 2026-08-08 review asked for: an unresolved journal
  operation disables Apply and Undo on every tab, re-checked at every press because a scan
  at window-open goes stale the moment another process dies mid-write - and the tab hands
  over the `recover` command with a Copy button rather than running it, since recover is a
  directional judgment whose CLI report walks you through the evidence a window would have
  to duplicate to be safe. One Undo button under the tabs walks the journal back one
  operation at a time, exactly as `ccs undo` would; there is deliberately no one-click "undo
  the whole apply" - the journal's unit is the operation, so the completion status names
  what was created instead. The window becomes **"Claude sessions"**, because the old name
  described one tab of three; installing the shortcut replaces the old one.
- **0.15.1 is the window's first-use patch.** Six findings from GUI 2.0's first real run, on
  2026-09-01, and the one that mattered was a hold the window could show but not clear. A
  collision the measurement declines to classify - distinct legs, or an unmeasured pair
  because overlap and recency disagree - rendered as a read-only line, and clearing it meant
  `ccs retitle` in a terminal, the exact step the tab exists to absorb. The 0.15.0 design had
  conflated "the measurement made no suggestion" with "this leg should not be named", and a
  human always knows more than the measurement; that is the whole reason the measurement is
  allowed to decline. Those rows are now editable, empty and unticked. Each renames its own
  held conversation, keeps the measured line as context, and names the other leg's id and the
  sidebar it collides in, so you know you are naming one of two. The smaller findings: the
  scoreboard box sizes to its content, so `complete ... N short`, the tab's headline number,
  no longer sits below an inner scrollbar; the header counts what the rows offer ("2 held: 1
  suggested, 1 needs a name") and the section label follows the ticks; Level's button is
  **Level the sidebars**, because two tabs sharing "Apply" read as one action; and Health's
  body says "mutation" like its title. The last finding is about the other tab. Measured on
  a freshly levelled store, **Copy & refresh** offered 78 rows into one account: 76 of them
  for conversations that sidebar already opened under another row file, 77 under a title
  already naming a row there. Sync counts row files the destination lacks, converge counts
  conversations it cannot open, and a multi-account store accumulates different row
  filenames for the same conversation, so the two tabs count different things and one press
  would have taken `distinguishable` from 0 to the seventies. The sync plan now reports both
  counts, per row and in its tally, the tab warns above Apply with the number, and a
  guidance line says what the tab is for: one session at a time, through the title filter.
- **0.16.0 makes the window legible.** After the first-use patch came a full audit of the
  window, read-only, over a headless layout probe, and its verdict was that the engine's
  guards reach the window intact while the window itself is hard to read. The one question
  it must ask - which account the desktop app is signed into, when the two identity files
  disagree - was asked twice, by two tabs, in two wordings, into two variables, the first
  time as a popup in a screen corner before the home tab had been seen. The tab strip read
  as a three-step checklist although one tab is the routine, one a diagnostic and one a
  rare exception. And the exception tab's filter row wanted 1101 px in an 896 px frame, so
  its third checkbox clipped and its fourth, "allow hiding a conversation", the RULING 8
  opt-in whose whole purpose is to be seen and said yes to, was not drawn at all at the
  default window size. This release is the audit's fixes in the order its design set. A
  layout harness lands first (`tools/check_gui_layout.py`, the window measured for real
  over a fake-cast store), and it found a defect nobody had reported: the bar holding Undo
  and Close was clipped at every window size, packed after the notebook that expands into
  it. Then the filter block becomes two rows and every label wraps to the window; the tabs
  read **Level | Health | One session**, each with a role line, and the exception tab's
  Apply is live for a one-row plan or for a tick that names the count ("copy every row
  this plan lists (78)"), bound to the rows on screen so consent cannot go stale; there is
  one identity answer, asked in-pane where it is read, shared by both tabs, bound to the
  file pair it was given for and dropped before any plan the files have outgrown - the
  sync pane plans on first visit, so nothing asks at open unless the home tab does, and
  the copy stage asks from its fresh plan if the question is still open; Level's
  scoreboard yields to seven lines behind a sash and the holds keep the rest; the window
  bar reads Undo, the Chrome setting, Close, and Close asks before losing typed names;
  every dialog opens centred, with Escape and the default on Cancel; the wheel reaches
  the holds. No default flipped: the audit's per-control table ended in visibility and
  placement, and the one box the operator's own habit would have flipped stays opt-in per
  run because RULING 8 says so. DPI awareness, the audit's last item, is its own later
  release; the harness it needed now exists.
- **0.16.1 follows the desktop app's browser helper to its new folder.** Desktop 2.42304.0
  deleted the `ChromeNativeHost` folder beside the session store and installed the helper in
  a versioned `Claude-NativeHost\<version>` folder instead. This tool recognised the helper
  only at the old exact path, so the copy at the new one counted as the running app. With the
  desktop app closed and a browser running the Claude extension, every apply refused, and
  neither the measured-build exclusion nor the signed-helper opt-in was consulted. The guard
  failed closed, but the refusal said only to close the desktop app, which was already
  closed. The guard now also recognises the new layout, derived from the real store root,
  with the version folder accepted only when it is dotted ASCII digits; what each exclusion
  requires is unchanged. When a process named like the helper sits at a location neither
  rule recognises, the refusal now names its path and says neither exclusion can apply, so
  the next move shows up in the refusal instead of failing silently. The note says Chrome or
  Edge can launch the helper; on the machine where this was found, Edge did. The measurement
  ceremony and the opt-in check script now find the helper where the guard does; the
  ceremony had been waiting indefinitely for a helper it could not see. Also in this
  release: when a session's transcript exists in two project folders and one copy is a
  strict byte-prefix of the other, the duplicated-transcript hold names the stale copy, the
  live copy and the removal command. It still chooses nothing. Identical copies are named
  as interchangeable; copies that have diverged keep the old wording. The prefix case is
  what a desktop session leaves behind when it enters a second worktree and is later
  resumed in the one its sidebar row remembers: the copy in the second worktree stops
  growing. It was seen twice on one machine in October.
- **0.16.2 puts the fake cast everywhere, and the helper note on every refusal.** Examples and
  fixtures across the repository, from the README to the tests and the check scripts, now use
  the project's fake cast rather than values read off the maintainer's own machine: account and
  store identifiers, an address, a Windows user path, a sidebar title and a session id. Nothing
  about what the tool reads, writes or refuses changed, and every test still exercises what it
  did. Also, from the browser-helper work: the note that explains a counted helper now appears
  on `move`, `undo` and `recover` refusals, not only on converge and sync; when the signed-helper
  opt-in is on and a helper still counts, the note says why; and the opt-in's wording, in this
  README, the window's checkbox and the command's help, says Chrome or Edge, since either can
  launch the helper. Three constants left unused since the August anti-fabrication fix are gone.
- **0.16.3 finds the session store where Desktop 2.42304.2 moved it.** That build moved the
  app's whole user-data folder out of the Microsoft Store package (corrected 2026-11-10: out of
  the MSIX package Anthropic distributes itself, not the Microsoft Store build, whose data the
  5.1231.0 release notes say does not move), from
  `%LOCALAPPDATA%\Packages\Claude_<pkg>\LocalCache\Roaming\Claude` to
  `%LOCALAPPDATA%\Claude-Data\claude_<pkg>`, and left a 0-byte file in the old folder naming
  the new one. 0.16.2 looked only in the old places, so `doctor` and `alignment` reported no
  store and `converge` planned nothing. This release reads the moved folder first. When a
  package's data has moved, its old folder is never read, and a store still sitting there is
  named in `doctor` rather than read beside the new one. The browser-extension helper did not
  move. The guard had found it two folders up from the store, which after the move is a
  folder where no helper lives, so the signed-helper opt-in would have stopped working again
  without a word. Helper locations are now listed on their own. The CLI the app spawns moved
  with the data, and the guard recognises it at its new path. When no store is found and the
  app's marker names a folder this version does not know, `doctor` says so instead of
  reporting a bare absence, and when the folder the data moved to cannot be listed, discovery
  reports an error rather than falling back to the old store. One gap remains: operations
  from before the move are journaled at their old paths, so `doctor` lists the rows this tool
  created then as no longer on disk although they moved with the store, and `undo` cannot
  reverse those operations (see the compatibility matrix). Until that is fixed, judge a
  `converge --apply` by `alignment`.
- **0.16.4 follows the journal and the window's saved destination across the data move.**
  Every operation journals the rows it touched by absolute path, so an entry written before
  Desktop 2.42304.2 moved its data names a folder that now holds only the app's marker. 0.16.3
  read those paths as written: `doctor` reported every row this tool had created as no longer
  on disk and exited 1, 320 rows on the machine where this was found, and `undo` and `recover`
  of those operations refused, misreported, or for a `move` ended in an unhandled error. This
  release maps a recorded path under the old store to the same place under the moved one as it
  reads the journal, and writes nothing back to it. On that machine `doctor` now finds all 320
  rows and exits 0. Before `undo` or `recover` acts on a journaled row, the row must resolve
  inside a store that discovery found; a row in a store the tool cannot read is listed by
  `doctor` apart, not counted in its exit code, and `undo` refuses it. `undo` of a `sync` now
  says what it reversed instead of a bare "undone". The window had the same trouble with its
  saved sync destination, a full store path, so every plan was refused with Apply off. It now
  sets a saved destination that is proven gone aside and asks for a new one. "Change
  destination" keeps the saved value until another is picked, so a cancelled pick loses
  nothing, and no plan goes into a different store without a pick. A saved store whose account
  is now signed in is named as such, and the candidate holding the saved ids is marked.
- **0.16.5 finishes the window's side of the data move, and lets a store path use either
  slash.** On Windows, `sync --to` and the store-scope `--live` of `sync`, `repoint`, `new-row`
  and `retitle` compared a store path with its separators as typed, so a path spelled with `/`,
  which is what Git Bash hands Python for `/c/Users/...`, matched nothing, and a Tab-completed
  folder's trailing `/` never matched either. Both now fold separators beside the exact test,
  and a trailing separator may match a store path's end. A `--live` value made only of
  separators is refused like a blank one; on Windows `--live \` used to certify a sole
  candidate. In the window, a saved sync destination from before the move is now re-rooted
  through the same package pairs the journal uses. It is the same store, moved whole by the
  app, so the window plans into it without asking and saves the new path only after an Apply
  into it succeeds; 0.16.4 set such a destination aside and asked. The window also builds its
  view of the stores afresh at every read and before every engine call that writes or plans a
  write, where it used to build it once when opened. A move made while the window was open had
  left every route refusing until a restart, or, when the app copied its data instead of moving
  it, left the window writing into the old copy. A press whose store layout changed since its
  plan now stops and replans before its next write.
- **0.17.0 shows which copy of a transcript each account's row opens, and repairs it.** A
  conversation's transcript can end up in two project folders, and each account's row opens
  the copy in the folder its `cwd` names, so one sidebar can show a conversation as it was
  weeks ago while another shows it today. `doctor` now lists each such conversation, which copy
  each row opens, and how the copies relate, reading past the bookkeeping records the app
  writes to whichever copy it has open; it exits 1 when an unarchived row opens an older or a
  diverged copy. It also lists, without counting them, conversations with one copy and a row
  whose folder holds none, because opening such a row made the app copy the transcript into
  that folder (measured on Desktop 5.11492.5). The new `realign` command points those rows at
  the folder holding the live copy, by rewriting `cwd` and the `folderPlace` keys the app keeps
  in step with it, and moves the stale copy into its journal, where `undo` can put it back. It
  holds anything it cannot decide safely, with the reason: diverged copies, a row with a
  worktree link, a live copy whose folder is gone, and identical copies unless
  `--include-identical` is given. On the machine it was built on, the first apply realigned 43
  conversations (84 rows) and held 52; `doctor` then listed 26 split conversations where it
  had listed 30, and 26 waiting ones where it had listed 65. `realign --release` sends a
  set-aside copy to the Recycle Bin once every row of its conversation opens one copy and that
  copy holds everything the set-aside one did, sidecar files included. It refuses each case it
  can see in which Windows would delete the copy outright, and asks Windows to warn in any
  other; how that warning behaves beside the silent flags release passes is untested, and no
  `--release --apply` had run when this shipped. The window's health report counts the split
  conversations, the waiting ones and the copies realign holds. `realign` itself is CLI-only,
  and while one is the latest operation the window offers no undo.
  The window also declares system DPI awareness before it opens, on Windows with Tk 9.0, so
  at 250 % it draws its text and widgets at the display's resolution and at the size it had
  before, where it used to be a stretched bitmap. Every pixel constant goes through one factor,
  the process's DPI over 96. Verified at 250 % and in a faithful 100 % run, and every other
  GUI harness passes in an aware process. 150 %, a common laptop setting, is untested; Tk 8.6
  is unchanged.
- **0.18.0 keeps a side session under its parent in every account.** A side session's row names
  its parent's row (`spawnedFrom`) and its chain's first row (`startChain`), each by an id that
  exists only in that account. `converge` used to write its rows without either, and `sync`
  copied both as they stood, so a copied link named a row the destination does not have. Both
  commands now write them translated to the destination's own rows, the link as `{sessionId,
  taskId}`. On the machine it was built on, probes found that the app hides a child whose link
  lacks `taskId` and nests it when the link has both keys, for the app's side-session links and
  for its older `{sessionId, taskId, title}` links, one candidate each. Only those measured
  shapes and this tool's own output are carried. Any other link is left out with its reason, as
  are the cases "Side sessions keep their parent" above lists: holders that disagree, a parent
  the account lacks, a link that would close a cycle. A parent created in the same run is
  written first. The apply resolves every link again under the lock, and the journal keeps the
  final link per row. Recovery never resolves again. When a pending row's parent was not
  written, or its row is gone or now opens another conversation, `converge` skips that row and
  `sync` stops offering to go forward. `undo` and
  `recover --back` remove children before their parents and report any link they leave
  dangling, or naming a row that now opens another conversation; the window's Undo shows the
  same lines. A
  `spawnedFrom` or `startChain` that is not an object, a null say, counts as no link. Before
  the release, a `converge --apply` on that machine created 20 rows and carried 20 links and 18
  chains. The app nested both children opened, one whose source link also carried
  `sharedFolder` and one with `userFork`, and after a reopen every carried link and chain read
  back as written. A child with the older link shape written by a release run, and a child
  `sync` added, have not been looked at in the app. `sync` now decodes its stores a second time
  for the links. On a store of about 3,000 rows (400 MB) its dry run took about 30% longer and
  peaked at 879 MB against 229 MB, and with `--update` at 1.13 GB against 452 MB. A fix is
  planned. `converge` recovery also keeps a row that landed before a hard kill: it records the
  row as written before re-checking anything, so `undo` still removes it, even after the app
  has rewritten it.
- **0.18.1 removes the cost 0.18.0 added to `sync` planning.** 0.18.0's planning decoded both
  stores a second time for the links. Planning now decodes each store once: the link pass keeps
  only the six keys it reads from each row, takes the source from selection's own read, and its
  one read of the destination also serves the duplicate check. Planning also finds transcripts
  from one listing of `~/.claude/projects` per plan, where it used to look in every project
  folder for each candidate row. On a store of about 3,000 rows (400 MB) with 838 project
  folders, a dry run from an account of about 1,000 rows took a median of 4.0 s against
  0.18.0's 29.4 s, and peaked at 237 MB against 882 MB. With `--update` it took 12.6 s against
  50.1 s, and peaked at 472 MB against 1.13 GB. Those peaks are back near 0.17.0's 229 MB and
  453 MB, measured on the same store earlier that day. Both versions printed the same report
  and, run back to back, the same `--json` plan, with and without `--update` and `--verbatim`.
  The apply still reads both stores again under the lock and looks transcripts up afresh, so a
  store that changed after the plan is judged as it stands, and recovery still never resolves.
  Example output in this README and in `docs/internals.md`, a docstring and one test's fixture
  now use the fake cast's ids, or none.
- **0.19.0 adds `relink`, which gives a side session's older rows their parent link.** Since
  0.18.0, `converge` and `sync` write the link (`spawnedFrom`) and the chain (`startChain`) on
  the rows they write, but rows made before then were left as they were, so a side session
  could sit under its parent in one account and on its own in another. `relink` gives such a
  row the link another account's row carries, translated to its own account's rows, and the
  chain beside it. It changes only those two keys, and never replaces a link or chain a row
  already has. It plans and applies as `converge` does, with the app closed, and its `undo`
  takes the keys out one by one: it leaves and names any key the app changed since, and waits,
  named, on any key a later operation wrote. Before the release it ran on the machine it was
  built on: seven rows one at a time, each looked at in the app and read back, then the other
  924 rows in one run, 931 links and 42 chains in all across three accounts. Every link and
  chain read back as written, and the 120 rows whose accounts disagree on the parent stayed out.
  "`relink`" above says what was looked at and what was not.
- Platform rows above move from "unverified" to "verified" as contributors confirm the store
  paths and behavior on their own machines.

## Reporting problems

This is a one-person project and support is best-effort. The fastest reports to act on
carry the failing command's plain-text output, `claude-code-sessions doctor` output, and -
if an operation was involved - the journal op id; the issue templates ask for each. Please
don't paste `--json` output into an issue: it is deliberately unredacted (see above).

## More

Companion post: [The session that synced itself](https://github.com/craigstoller/claude-code-sessions/blob/main/docs/the-session-that-synced-itself.md).

MIT licensed - see [LICENSE](https://github.com/craigstoller/claude-code-sessions/blob/main/LICENSE).
