# The session that synced itself

Last night I did something mildly recursive. Mid-conversation, I had the Claude Code
session I was working in copy *itself* — the conversation you'd click on in the sidebar —
from one Claude account into another on the same machine. Then I switched accounts and
typed my next message from the other side. The conversation picked up where it left off:
same history, same working state, now running under the destination account's own
connectors and permissions rather than the source's.

The tool that did it is [claude-code-sessions](../README.md), a small CLI I built after
spending a week measuring what Claude actually keeps on disk. Before it made that copy,
it refused me — several times, for several different reasons, every one of them correct.
By the end of the evening all four of its refusal types had fired in real use, and the
single mutation that *should* have succeeded did, on the first cleanly-permitted attempt.

This post is about what's really in those files, and about why a tool that touches them
should be built mostly out of refusals. Everything in it was measured on one Windows
machine, on current builds, with methods and caveats collected at the end — including
the measurements that turned out to be wrong.

## One conversation, two sidebars

Two programs share the picture, and it helps to name them precisely. **Claude Code** — the
CLI — writes the conversation itself: a transcript at
`~/.claude/projects/<project-folder>/<session-id>.jsonl` (sessions that spawn subagents
get a sibling folder of their transcripts too). **Claude Desktop** — the app with the
sidebar and the account switcher — keeps, per account, a store of "listing rows": one
small JSON file per conversation, holding the title, timestamps, the session id, and a
snapshot of per-session configuration. The sidebar is that directory listing.

Here is the fact that makes cross-account sync possible, and it surprised me when I
measured it: **no account identifier appears anywhere in the transcript.** The
conversation file doesn't know who was signed in when it was written. What an account
"has" is its listing row — and the row's only load-bearing part is a pointer to the
shared transcript.

So "moving a conversation to another account" doesn't move the conversation at all. It
places a row in a different account's store; that account's sidebar shows the thread;
clicking it opens the shared transcript; the conversation resumes. I verified the resume
end-to-end before building anything — on 2026-07-31, rows copied by hand because the tool
didn't exist yet, in the desktop app and with `claude -r`, with a native-thread control
alongside so a broken invocation couldn't masquerade as a finding. (The shipped tool's
own end-to-end run, new turns included, came 2026-08-03 — it's the story this post ends
with.) Why Anthropic's backend is content to continue a session under
either account is a question I can only observe the answer to, not explain: this project
measured the local files, nothing past them.

Two numbers make the pointer-ness vivid. A native row can be big — the largest on my
machine was 1.36 MB, mostly a snapshot of the account's connector and MCP-tool
configuration. Stripped of everything account-specific, one 132,264-byte row came down to
**715 bytes**, and the thread still opened perfectly. The first time the destination
account focused it, the app rewrote the row back to full size, repopulating the connector
snapshot from *that account's own* configuration. The row you sync is a seed; the app
grows it back on the receiving side. Two honest limits to the metaphor: the copy's title
and sort position freeze at sync time (re-running `sync` won't refresh an existing row —
an `--update` mode is future work), and the moment the destination opens the thread, the
rewritten row no longer matches what the tool journaled — which, as you'll see, is
exactly when the tool stops being willing to undo it.

## The app disagrees with itself

Measuring the store turned up three places where what the app records and what the app
does are different things. Each one changed the tool's design.

**It writes deletion records it doesn't honour.** Delete a thread in the sidebar and the
app drops a small `deleted_<id>` marker — thirteen bytes, an epoch-milliseconds timestamp
— into the account's store. You'd assume that marker suppresses the thread. On the builds
I measured, nothing consumes it: put a listing row back and the "deleted" thread
reappears, tombstone still sitting there, across restarts and an account switch. Which
means a naive row-copying tool will *resurrect threads the user deliberately deleted* —
the single worst thing a sync tool could quietly do. So my tool honours the app's
tombstones even though the app itself doesn't: a tombstoned session is never synced into
that account unless you name it explicitly.

**The deletion records come in at least two id spaces.** After I'd built that check, I
found a deleted thread carrying *two* markers: one under its session id, one under the
listing row's own filename stem. My first implementation checked only the space I'd
observed; a deletion recorded the other way would have been invisible, and the thread
resurrected anyway. This was the second time in the project a measured fact turned out to
be narrower than the measurement — I'd seen *a* tombstone and generalized its naming from
one example. Measure, then assume your measurement is a special case.

**Its two identity files have two different owners.** Who's signed in? There are two
answers on disk: the CLI's `~/.claude.json` names an account (`oauthAccount`), and the
desktop's own `config.json` names one (`lastKnownAccountUuid`). I assumed the oauth
record was authoritative and the desktop's field was the stale-prone fallback — and
shipped a safety model that trusted it accordingly. Then I measured across a real account
switch: the desktop's field tracked the switch correctly and it was **the oauth record
that went stale**, still naming the account I'd switched away from. The simplest
explanation, consistent with everything observed but not separately proven: each program
writes only its own file, so signing in on the desktop gives the CLI's record no reason
to notice. A review had already constructed the mirror-image failure — desktop field
wrong, oauth fresh — in a synthetic store. One direction measured in the wild, the other
demonstrated in review; the design treats both as live. When the two files disagree, the
honest answer to "which account is live?" is *you cannot know from files*.

## Building the safe one

That conclusion set the design rule for the cross-account commands: **no cached identity
fact is allowed to be load-bearing for safety.** The one catastrophic act a tool like
this could commit is mutating the store the app is *actively using* — the app and the
tool racing on the same files. Everything else is journaled: copies are additive-only,
and undo holds for as long as the copy sits unchanged. So for anything that writes or
deletes across the account boundary, the guards stack:

1. **If the identity files disagree, planning refuses.** No mutation destination resolves
   at all. The refusal names both accounts and the two actions that reconcile them:
   re-authenticate the CLI as the account you're using, or switch the desktop to it.
   (Signing into the desktop does *not* refresh the CLI's file — that's the trap I fell
   into.)
2. **If the destination resolves to the live account — or to *either* account a
   disagreement names — it refuses**, writing or deleting, before or after a crash.
3. **If the desktop app is running, every mutation refuses**, whatever the identity
   files claim — and the refusal lands *before* anything is journaled, so a refused run
   leaves zero cleanup. The process check fails closed: an unreadable process list counts
   as "possibly running," never as "all clear." A running Claude Code *CLI* is recognised
   by its install path and doesn't block the sidebar-store commands — it doesn't write
   those stores.
4. **If a row changed since the tool journaled it, undo refuses to delete it.** The
   destination account may have opened that thread; a byte-for-byte match is the only
   license to remove. Since the app rewrites a row on first focus, this means undo's
   window closes the moment the synced thread is actually opened — deliberately. Past
   that point, deleting it is a decision for a human in a sidebar, not a tool in a
   journal.

Residuals, because absolutes are how this kind of post loses your trust: the process
check is point-in-time (an app *started* mid-write isn't caught — the drift and overwrite
refusals are the second layer), and for `move` — which relocates transcripts that a
running CLI session genuinely does write — excluding the CLI from the process check means
a transcript-freshness heuristic and a last-instant content re-verification carry that
case. Scoped claims, stated residuals; the [internals doc](internals.md) enumerates them.

I want to be honest about how rule 2 got its second clause, because it's the strongest
case I know for adversarial review. My disagreement fix made the account-resolver return
"unknown" — and two older checks read "unknown" as "no evidence, proceed," which quietly
disabled the most load-bearing refusal in the design *in exactly the state the fix
existed to handle*. A final review that mutation-tested the code caught it and reproduced
the failure end-to-end: at the previous release both operations refused; with my fix, a
crash-recovery wrote into a possibly-live store and an undo deleted from one. The fix for
that fix is why the ladder reads the way it does. The test suite grew from 232 to 267
along the way, and several of those tests exist because a reviewer proved an earlier test
would still pass with the feature deleted.

One more thing the guards taught me about the app itself: after you close Claude Desktop,
a helper it ships for its Chrome extension — `chrome-native-host.exe` — can keep running
under the desktop's own package directory. The guard counts it, which means "close the app"
is sometimes not enough — you have to fully exit Chrome too. The refusal names the surviving
process, so you can see why you're being refused. I later went looking for evidence that the
helper never touches the store, so the guard could stop charging for it; I didn't find enough
to act on, and the measurement that failed is written up in `internals.md` alongside the one
that would settle it.

## The finale, replayed

Which brings me back to the recursive part. To run the last verification I needed to
switch desktop accounts — but the session driving the work existed only in the *current*
account's sidebar. Switching would strand it. The obvious fix was the tool itself: sync
the session across first. And here the design bit its own author: the identity files
disagreed at that moment — the measured staleness, live on my machine — so the tool
**refused to plan the very sync that would have rescued my session**. Correctly: under
disagreement it cannot prove which store is dormant.

So: re-authenticate the CLI. First `--apply`: refused — the Chrome helper was still
alive, and the pre-journal guard declined with nothing written and nothing to clean up.
Exit Chrome fully, probe clear, re-run: seven rows written from one account's store into
the other's, journaled, one op. Reopen the desktop, switch accounts — and there's the
conversation in the other sidebar, where my next message became the end-to-end proof
(2026-08-03, now recorded in the repo's compatibility matrix): the session continuing,
live, from the far side of its own migration.

Meanwhile the disposable test thread I'd spent the week trying to `undo` ended the
evening untouched — protected, in sequence, by the disagreement refusal, the
live-destination refusal, the running-app refusal, and finally the drift refusal, because
the receiving account had opened it once. Four refusals, four different reasons, every
one of them the design working. For a tool whose worst failure mode is deleting someone's
conversations, that's the property I care most about.

## What it is, and isn't

[claude-code-sessions](https://github.com/craigstoller/claude-code-sessions) moves Claude
Code conversations between project folders and syncs their sidebar entries between
accounts on the same machine. Copies are additive-only, strip account-specific state, and
skip any thread the destination account has deleted — honouring tombstones the app writes
but doesn't. Every mutation is journaled, with `undo` and crash `recover`, and everything
above refuses first rather than guessing. It does **not** merge conversations, refresh
stale copies (yet), or touch anything in the cloud — though what you do with a synced
thread afterwards does: these safety claims are about local filesystem integrity, and
whether a particular conversation *should* cross an account boundary — work to personal,
say — is a data-governance question the tool cannot answer and will never refuse over.
It is unofficial, unaffiliated with Anthropic, verified on Windows only, and one
storage-format change away from needing new measurements. The
[internals write-up](internals.md) carries the storage-model measurements and methodology
behind this post; the repo's history carries the rest, including the findings that didn't
survive contact with a second data point.

*Disclosure, concretely: this tool was built in Claude Code sessions. Claude wrote the
code and this post; I ran the parts that needed hands — sign-ins, account switches,
closing apps — and made the design calls the repo's decision records attribute to
rulings. The session this post describes is one of the seven rows it synced.*
