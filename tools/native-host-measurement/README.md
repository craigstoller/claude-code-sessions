# Native-host measurement harness

Instruments used for the **2026-08-05** measurement of whether the desktop app's Chrome
extension helper (`chrome-native-host.exe`) ever writes under the `claude-code-sessions` store.
The ruling that came out of them — **inconclusive, so the guard keeps counting the helper** — is
recorded in [`../../docs/internals.md`](../../docs/internals.md), section "The Chrome native
host, and why it still counts". Read that first; it says what was and was not established, and
defines the acceptance protocol a conclusive run has to satisfy.

These are kept because the conclusive measurement still needs running, and rebuilding this from
scratch is most of the work. Nothing here is part of the shipped package (`pyproject.toml`
declares `py-modules = ["claude_code_sessions"]`, so `tools/` reaches neither wheel nor sdist),
nothing imports the module under test, and none of it runs in CI.

Windows-only. Python 3.9+. No third-party dependencies — everything is `ctypes` against
`ntdll`/`kernel32`. **No elevation required**, which is the whole reason these exist; the
instrument that would settle the question outright (Procmon / ETW kernel-file) does need it.

## The scripts

| Script | Answers |
|---|---|
| `measure_native_host.py` | What does the helper hold open, and does anything land in the store while it runs? |
| `attribute_store_writers.py` | Which processes hold store handles — and does the sampler see store writes *at all*? |
| `resolver_positive_control.py` | Is the handle resolver capable of seeing a store handle, or is it broken? |

### `measure_native_host.py`

```bash
python measure_native_host.py --seconds 300 --interval 0.5 --out run.json
```

Runs two independent instruments over the same window:

1. **Handle sampler** (attributed, sampled) — enumerates the system handle table via
   `NtQuerySystemInformation(SystemExtendedHandleInformation)`, filters to the target process
   and to File-type handles, duplicates each into this process and resolves its name with
   `NtQueryObject`. Says *which* process holds *which* path, with the access mask decoded.
2. **Directory watcher** (continuous, unattributed) — recursive `ReadDirectoryChangesW` on each
   store root. Catches transient writes the sampler misses, but not who made them.

It writes a JSON report plus a human summary, and takes before/after recursive snapshots
(size, mtime, sha256) of each store root as a third check.

**A canary file is created and deleted inside each watched root at start and end.** That is the
positive control for the watcher: if the canary events are absent from the log, that watcher was
not delivering and its silence proves nothing. It also watches the busy sibling tree
(`...\LocalCache\Roaming\Claude`) for a second, free liveness signal.

### `attribute_store_writers.py`

```bash
python attribute_store_writers.py 120 attribution.json
```

Scans every Claude-named process for handles under the store, while watching the store for real
write activity in the same window. This is the **negative control**, and it is the finding that
decided the ruling: it caught zero processes holding a store handle during a window with 28
recorded store writes. Handle sampling cannot see millisecond-scale atomic writes, so "the
helper was never caught holding a store handle" is not evidence of absence.

Scoped to Claude-named processes deliberately — a system-wide scan means a duplicate-and-resolve
round trip per handle on the machine, with a timeout on each hang-prone pipe.

### `resolver_positive_control.py`

```bash
python resolver_positive_control.py
```

Distinguishes "the sampler works but the writes are too brief" from "the sampler is broken for
these paths". A **child** process holds a file open under the real store root, and the same scan
must find it — the child matters, because cross-process `DuplicateHandle` is the step most
likely to fail and testing against our own handles would skip it. Exits non-zero if the held
file is not detected, in which case every "no hits" result from the other scripts is void.

Scope note: it uses a continuously held ordinary file, so it validates path resolution only. It
does not show that a short-lived handle or a blocking pipe handle would resolve.

## Two traps, both of which produce a false clean result

- **Never skip handles by `GrantedAccess`.** The usual `NtQueryObject` hang-avoidance advice is a
  skip-list containing `0x0012019F` — which is the ordinary "synchronous file, full access" mask,
  i.e. exactly the writable-file class being looked for. The first draft of this harness had that
  skip-list and would have reported a clean result by construction: the resolver positive control
  detects its held file at precisely `0x0012019F`. Hangs are handled with a per-query timeout and
  an object-pointer cache instead.
- **Set `argtypes`/`restype` on every `ctypes` call.** The default `restype` is `c_int`, which
  truncates 64-bit `HANDLE`s and makes handle operations fail in ways that look like "nothing
  found" rather than an error.

Also worth knowing: `NtQueryObject` times out on roughly one helper handle per sample (a
synchronous pipe blocked in a pending read). That is reported, not hidden — an unresolved handle
is a gap in the result, and the ruling treats it as one.

## What a conclusive run needs

Not these scripts. See the acceptance protocol in `docs/internals.md`: an elevated Procmon or ETW
kernel-file capture scoped to *the store roots, all processes* (a helper-only filter cannot
contain its own control), run from outside the desktop app's process tree so the
app-closed/helper-surviving window can actually be held open. These scripts remain useful there
as the cross-check and for generating the store-side positive control.

**Superseded 2026-08-07 — run [`watch_ceremony.py`](watch_ceremony.py) instead.** See the
amendment in `docs/internals.md` ("the store-side watch replaces the elevated trace"). The
elevated trace was built and run twice; it died before the decisive window both times and
measured **9.1 GB/min**, putting a full ceremony near 180 GB. The endpoint is a statement about
the store, not the helper, so attribution — the only thing a trace buys — is not what a PASS
needs.

```bash
python watch_ceremony.py                 # the ceremony (no elevation; your own terminal)
python watch_ceremony.py --verdict report.json   # re-render a verdict offline
```

Two instruments, because one is measurably not enough: a continuous `ReadDirectoryChangesW`
watch (says *when*), plus a sha256 snapshot diff across the decisive window (says *whether*).
**Measured 2026-08-07: a pure mapped-section write produces no directory-change notification at
all — absent at 60 s** — which is the class the protocol singled out, so a watcher-only design
would report such a window quiet. The snapshot catches it.

Five controls can each void a run: canary heartbeat, buffer-overflow detection (overflow is
signalled as success-with-zero-bytes and would otherwise look like quiet), phase-1 real
traffic, the mapped-write control, and a process-tree guard that refuses to run inside the
desktop app's own tree — the failure that lost the 2026-08-05 decisive leg.

`elevated/` is retained as the **attribution** follow-up: a FAIL says the store changed, not
who changed it, and that is when a trace earns its cost.
