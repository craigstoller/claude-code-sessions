# claude-code-threads

Move a Claude Code conversation to a different project folder — safely.

> **Unofficial.** Not affiliated with Anthropic. Reverse-engineered on-disk formats; fails
> closed when it sees anything it doesn't recognize.

## The problem

Claude Code and Claude Desktop file every thread under the working directory it was started
in, and there is no official way to move one to a different project after the fact — a thread
started in the wrong folder is stuck there. `claude-code-threads` relocates the transcript and every
listing row that points at it, verifying each side before touching the other, so the thread
reopens cleanly in its new home.

## Install

```
pipx install claude-code-threads
```

This installs two identical commands: `claude-code-threads`, and `cc-threads` as a shorter
alias for everyday use. Examples below use the long form; `cc-threads doctor` is the same
thing.

Or download `claude_threads.py` and run it directly — the runtime has no dependencies beyond
the Python 3.9+ standard library:

```
python claude_threads.py --help
```

## Before any move: close the Claude app.

`move`, `undo`, and `recover` refuse to mutate anything while they can see a running Claude
process, but closing the app first avoids the refusal and guarantees nothing is actively
appending to the transcript you're about to relocate. (`sync` is the one exception — see below:
it never writes the account you're signed into, so it carries no such check and doesn't need the
app closed.)

## Usage

Six commands. All mutating commands default to a dry run; add `--apply` to execute.

**`list`** — inventory threads, optionally filtered by a search term:

```
claude-code-threads list gate
```

**`doctor`** — read-only health report (stale locks, unresolved operations, orphaned rows,
encoding-scheme ambiguity):

```
claude-code-threads doctor
```

**`move`** — relocate a thread to another project folder:

```
claude-code-threads move 3c3c3eae-0e2f-4be4-9fba-407f06816f79 "C:\path\to\project" --apply
```

Get the full id from `claude-code-threads list --full`.

**`undo`** — reverse the most recent completed operation:

```
claude-code-threads undo --apply
```

**`recover`** — resolve an operation left non-terminal by a crash or interruption:

```
claude-code-threads recover
```

**`sync`** — copy a thread's sidebar **listing row** from your signed-in account into your
*other* Claude account's store on this machine, so it shows up in that account's sidebar too:

```
claude-code-threads sync --apply
```

There is only one copy of the conversation itself — shared, and carrying no account identity —
so a synced thread opens and resumes normally under the other account. Without `--to`, the
destination must be unambiguous: exactly one other account store on the machine, or `--to
<uuid-substring>` naming one of several by its account or org id. Not by email: the destination
is an account you are not signed into, so its email is never recorded on disk for `sync` to match
against (more on this below) — only account/org id substrings work. `sync` refuses outright if it
cannot tell which account is signed in, from either `~/.claude.json` or `config.json` — `--to`
cannot substitute for that: it only narrows *which* dormant store to use, and if we don't know
which account is live we cannot verify the one you named isn't it.

`--json` prints the plan by itself, the same as the default dry run. Combined with `--apply` it
runs first and describes what actually happened instead — real `written` flags per row and a
`result` key, not the plan it would have executed. Automation that assumes `sync --json` output
is always a preview will misread `sync --apply --json`.

- **Threads you deleted in the destination stay deleted.** The app writes a small record
  (`deleted_<id>`) when you delete a thread but does not consult it when a row for that thread
  reappears elsewhere — confirmed by restoring a deleted row alongside its own record and
  reopening the app, which showed the thread again. `sync` reads the *destination's* records and
  skips any source thread they cover. `--include-deleted "<title-or-id>"` overrides the skip for
  that one named thread; it never applies to a whole run.
- **Connector config is not copied by default.** A row can carry a full snapshot of the
  account's connected MCP servers; across 432 real rows on this machine that field ran as large
  as 1.36 MB in a single row and totalled 289 MB. Stripping it is verified safe: on a real row it
  cut 132,264 bytes to 715 (99.5% smaller) with the sidebar entry, history, responses, and
  connectors all working normally afterward, under the account that owned them. That proves the
  *app* tolerates the field's absence — it does not prove the *destination account* has the same
  connectors configured. A thread that relies on one opens fine either way and fails at its first
  tool call if the integration isn't set up there too; set it up in the destination account.
  `--verbatim` copies rows unchanged, connector config included.
- **It does not need the app closed, unlike `move`, `undo`, and `recover`.** `sync` only ever
  writes the store you are *not* signed into, and re-checks that at the moment it writes, not
  just when it planned.
- **`sync` cannot tell you the destination's email** — it isn't recorded anywhere on disk for an
  account you aren't currently signed into, so a dry run prints `(email unknown)` for it. Both
  endpoints print their account/org id prefix and their full store path instead, so you have a
  physical folder to recognise: the home directory becomes `~` and each id is truncated to 8
  characters (e.g. `~\AppData\...\claude-code-sessions\aaaaaaaa…\bbbbbbbb…`) unless you pass
  `--verbose` for the paths and ids in full. Check the path, not just the email, before `--apply`.
- A synced row is a **snapshot**: title and last-activity time live in the row itself, so a
  thread you keep using shows its copy-time title and sits at its copy-time position in the
  other account, indefinitely. Re-running does not refresh it — `sync` only ever adds rows that
  are missing, never rewrites one that's already there. Refreshing a stale row (`--update`) is
  planned but not yet built.
- Sign into the other account (or restart the app) to see the results.

## Safety design

- **Dry-run by default.** Every mutating command prints its plan and does nothing until you
  pass `--apply`.
- **Journaled copy-verify-commit-delete.** A move journals its complete intended state before
  touching anything, copies to the destination, re-verifies the copy by hash, rewrites listing
  rows atomically, re-verifies both sides one last time, and only then deletes the source.
  Nothing is ever deleted while it is the only copy.
- **`undo`** reverses the most recent completed move by running the same journaled protocol in
  reverse.
- **`recover`** classifies and resolves any operation a crash or interruption left in a
  non-terminal state — nothing is left stranded.
- **`sync`** only ever writes rows into the store of the account you are *not* currently signed
  into, re-verifying that at the moment it writes as well as when it planned. That is also why
  it refuses outright if it cannot identify which account is signed in at all: `--to` names a
  destination, but without a confirmed live account there is nothing to check that destination
  against.
- **Refusal philosophy.** The tool fails closed: an unrecognized on-disk layout, an unreadable
  row, an ambiguous encoding scheme, or a running Claude process is a refusal, not a guess.
  "Couldn't look" is never treated as "nothing there."

## Compatibility matrix

| Platform | Status | Mutations |
|---|---|---|
| Windows 11 + Claude Desktop | verified 2026-07-31 (app 2.1.219) | read-only + mutations |
| macOS / Linux desktop | unverified | read-only (+ mutations behind `--unverified-platform`) |
| CLI-only machines (any OS) | transcript layout verified | mutations behind `--transcript-only` |

The Windows row is an end-to-end check on a real store, not just a passing test suite:
a disposable thread was moved between projects, the app was restarted and the thread
resumed at its new location, `undo` correctly **refused** once that resume had appended
to the transcript (rather than discarding the new messages), and a deliberately
interrupted move was resolved in both directions with `recover`. Afterwards `doctor`
reported no new findings and the journal held no unresolved operations.

`sync`'s underlying mechanics were checked against two real, live accounts on this machine before
the command existed: rows copied by hand between them and confirmed visible in the destination's
sidebar, and — separately — a deleted thread's row restored alongside its own deletion record and
confirmed visible again, which is the finding that makes `sync`'s tombstone-skipping mandatory
(see `docs/internals.md`). The `sync` command itself is covered by its test suite and a read-only
dry run against a real store; it has not yet had its own end-to-end `--apply` run against two live
accounts the way `move` has above.

## What's stored locally

`~/.claude-code-threads/` holds the tool's own bookkeeping, never your conversation content:

- `ops/<op-id>/manifest.json` — the journal for each move/undo/recover/sync operation (paths,
  hashes, row pre-images, phase history). Rotated: the 10 most recent terminal operations are
  kept, non-terminal ones never pruned automatically.
- `ops/lock` — a single-instance lock held for the duration of a mutation.
- `moved-log.jsonl` — a tiny, append-only, never-rotated record of completed moves (session id,
  from-path, to-path, date), used to recognize your own past moves during future collision and
  encoding-evidence checks.

To purge everything the tool has ever written, delete the whole `~/.claude-code-threads/` directory.
This does not touch any transcript or listing row — only the tool's own journal.

**`--json` output is not redacted.** Plain-text output replaces your home directory with `~`
and truncates any UUID-shaped identifier (session ids, org/account ids in store paths) to its
first 8 characters by default — pass `--verbose` to see paths and ids in full. `--json` output
always contains full paths, titles, and ids, unredacted, so it can be consumed programmatically.
Do not paste `--json` output into a public issue or forum post — copy only the fields you mean
to share.

**Windows durability note.** The commit step fsyncs every file it writes before deleting the
source, but Windows has no equivalent of a directory fsync, so the directory-entry update
itself (the rename, the delete) is not separately forced to disk. The spec accepts this as a
residual risk rather than a defect: file-level fsync plus delete-last ordering means a power
loss in this window can only lose an already-fsynced deletion, never earlier fsynced writes —
worst case is a leftover duplicate, never lost data, and `recover` classifies exactly this
window into `journaled`/`completed`/`rolled_back`.

## Roadmap

- **`sync --update`** (refreshing a previously synced row that the destination account has kept
  using) is deferred, not dismissed. Refreshing means overwriting a row the destination account
  may have changed itself since the copy — the one place `sync` could destroy something instead
  of just adding to it — so it needs the same drift-refusal treatment `undo` already has before
  it ships.
- Platform rows above move from "unverified" to "verified" as contributors confirm the store
  paths and behavior on their own machines.

## More

Companion post: _(link pending)_.

MIT licensed — see [LICENSE](LICENSE).
