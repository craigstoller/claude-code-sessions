# Elevated capture runbook — the conclusive chrome-native-host run

This is the follow-up run the 2026-08-05 measurement could not perform (see
`docs/internals.md`, "The Chrome native host, and why it still counts" — read its
*acceptance protocol* first; this kit implements it). One person, ~20 minutes, one UAC
prompt.

**Why a human has to drive it:** the capture must run from *outside* the desktop app's
process tree (any Claude session dies or pauses when the app closes — the exact window we
need to hold open), and the decisive phase requires closing the desktop app while Chrome's
helper survives, then exercising the extension.

## Prerequisites

- Process Monitor, if not already installed (~3–4 MB, Sysinternals via winget):

      winget install Microsoft.Sysinternals.ProcessMonitor

- Chrome running with the Claude extension, desktop app running, and **generous free disk**:
  the 2026-08-06 aborted run measured ~1 GB of backing file per minute unfiltered, so a full
  ceremony plus CSV export can want 25–40 GB. The script preflights this and asks before
  proceeding under 40 GB free. Backing-file segments are deleted after CSV export unless you
  pass `-KeepPml`.

## The ceremony

1. Open **your own** Windows Terminal / PowerShell — NOT a terminal inside any Claude
   surface. Run:

       pwsh -File "C:\Users\craig\Projects\_Tools\claude-code-sessions\tools\native-host-measurement\elevated\run-elevated-capture.ps1"

   It relaunches itself elevated (one UAC prompt) and from there walks you through numbered
   phases, recording a UTC timestamp for every step into `workload-timeline.json`. Follow
   the prompts; the script verifies process states itself (app alive, helper alive, app
   gone, helper gone) rather than trusting your answers.

2. The phases it walks you through, per the acceptance protocol's workload enumeration:
   - **P1 (controls, app open):** use the desktop app briefly so its own store writes land
     in the trace — the capture-side positive control — and use the extension a counted
     number of times (it asks you what you did and how many times).
   - **P2 (the decisive window):** close the desktop app; the script confirms the app
     processes are gone while the helper survives, then holds the window ≥3 minutes while
     you exercise the extension again.
   - **P3:** fully exit Chrome; the script confirms the helper exits.
   - Throughout, a canary heartbeat writes-renames-deletes a marker file under each store
     root every 30 s — the empirical loss probe (a heartbeat gap = a lossy interval).

3. When it finishes it exports `capture.csv`, records the helper binary's version/hash and
   the Chrome/package/Windows builds (the ruling binds to these), and prints the output
   directory. Bring that directory to a Claude session and ask it to run:

       python tools/native-host-measurement/elevated/analyze_capture.py <output-dir>

## What the analyzer decides

- **INCONCLUSIVE** if any positive control failed: missing canary heartbeats (lossy trace),
  or no desktop-app store writes during P1 (a capture that can't see the app's own writes
  proves nothing by being empty).
- **FAIL (exclusion not licensed)** if any non-canary mutation of either store root, by any
  process, occurred during the helper-only window (P2→P3). The protocol's endpoint is about
  the *store*, not the helper — a message bridge can cause a write it does not perform.
- **PASS** licenses the exclusion **for the measured helper build only** (hash recorded in
  the report). The code change that consumes a PASS must bind to the anchored package-path
  segment chain AND the binary hash, per the protocol — never to a directory name.

A PASS here does not edit any code. It produces the evidence; the guard change is its own
reviewed commit.
