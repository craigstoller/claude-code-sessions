# claude-threads

Move a Claude Code conversation to a different project folder — safely.

> **Unofficial.** Not affiliated with Anthropic. Reverse-engineered on-disk formats; fails
> closed when it sees anything it doesn't recognize.

## The problem

Claude Code and Claude Desktop file every thread under the working directory it was started
in, and there is no official way to move one to a different project after the fact — a thread
started in the wrong folder is stuck there. `claude-threads` relocates the transcript and every
listing row that points at it, verifying each side before touching the other, so the thread
reopens cleanly in its new home.

## Install

```
pipx install claude-threads
```

Or download `claude_threads.py` and run it directly — the runtime has no dependencies beyond
the Python 3.9+ standard library:

```
python claude_threads.py --help
```

## Before any move: close the Claude app.

The tool refuses to mutate anything while it can see a running Claude process, but closing the
app first avoids the refusal and guarantees nothing is actively appending to the transcript
you're about to relocate.

## Usage

Five commands. All mutating commands default to a dry run; add `--apply` to execute.

**`list`** — inventory threads, optionally filtered by a search term:

```
claude-threads list gate
```

**`doctor`** — read-only health report (stale locks, unresolved operations, orphaned rows,
encoding-scheme ambiguity):

```
claude-threads doctor
```

**`move`** — relocate a thread to another project folder:

```
claude-threads move 3c3c3eae-0e2f-4be4-9fba-407f06816f79 "C:\path\to\project" --apply
```

Get the full id from `claude-threads list --full`.

**`undo`** — reverse the most recent completed operation:

```
claude-threads undo --apply
```

**`recover`** — resolve an operation left non-terminal by a crash or interruption:

```
claude-threads recover
```

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

## What's stored locally

`~/.claude-threads/` holds the tool's own bookkeeping, never your conversation content:

- `ops/<op-id>/manifest.json` — the journal for each move/undo/recover operation (paths,
  hashes, row pre-images, phase history). Rotated: the 10 most recent terminal operations are
  kept, non-terminal ones never pruned automatically.
- `ops/lock` — a single-instance lock held for the duration of a mutation.
- `moved-log.jsonl` — a tiny, append-only, never-rotated record of completed moves (session id,
  from-path, to-path, date), used to recognize your own past moves during future collision and
  encoding-evidence checks.

To purge everything the tool has ever written, delete the whole `~/.claude-threads/` directory.
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

- **`sync`** (copying a thread's listing row across accounts on the same machine) is deferred
  pending some tombstone-semantics experiments — the two-layer model (shared transcript,
  per-account listing copy) means a naive copy can't currently tell a deleted thread from one
  that was never copied.
- Platform rows above move from "unverified" to "verified" as contributors confirm the store
  paths and behavior on their own machines.

## More

Companion post: _(link pending)_.

MIT licensed — see [LICENSE](LICENSE).
