# Measured hold remedies (design) — B6(5)

**Status: draft, for handoff. Round-1 panel review applied — see "Review" at the end.**
One change: when `converge` holds a title collision, it measures the two conversations
against each other and — when the measurement is decisive — prints a *complete* remedy, with
the right conversation and a generated title, instead of
`retitle --only <id> --title "<new title>" --apply`. Advisory throughout: the measurement
can never block or refuse, its I/O is budgeted (stated numbers under "Performance"), and
every degraded or inconclusive state falls back to exactly today's placeholder remedy plus
one reason line.

Names below are the fake cast (`ACME-REVIEW session`, ids `aaaa1111…`; conversations
`s-early…`/`s-late…`). Measurements quoted are real.

## The evidence

Every converge pass on a real three-account store has produced title-collision holds, because
both routine gestures — resuming a session, and rewinding to edit a prompt — fork the
conversation, repoint the live account's row to the fork, and carry the pinned title across.
The two legs then share a name, and converge correctly refuses to spread the ambiguity.

Clearing the 2026-08-29 pass's holds took, by hand, exactly the algorithm this spec
mechanizes: extract each leg's prose turns, measure the shared set (52 of 53; 46 of 47;
8 of 10 — note the rewind-to-edit forks measured 0.98: the rewind copies the entire prefix,
so near-total overlap is how that gesture presents *by construction*), conclude
"supersession, not two distinct conversations," pick the superseded leg, and compose its new
title from the store's own convention — `<base> - earlier leg (<dates>)` — checking the name
against every sidebar first.

**The measurement also corrects the remedy's target, which the placeholder cannot.** Holds
come in both directions — `s-early -> account-c` held by `s-late`'s row there, *and*
`s-late -> account-a` held by `s-early`'s row there — and today each hold prescribes
retitling **the blocking row's conversation**, whichever it is. In one of those two
directions that is the *live, current* leg — the one whose name should stay. On 2026-08-29
the operator ignored that direction's prescription, retitled the superseded leg once, and
both directions cleared. A measured remedy names the superseded leg in *every* hold of the
group, so one retitle clears the group and the current leg keeps its name.

That claim is scoped to **two-leg groups, and the measurement enforces the scope**: with
exactly two conversations on one `title_key`, renaming either changes its key and every
hold on the collision — shape (a) and shape (b) alike — dissolves on the next plan (a test
replans after the simulated paste and asserts zero holds). Three or more legs on one key
form a complete collision graph where no single rename clears everything and pairwise
verdicts could even disagree about who supersedes whom — so a `title_key` shared by three
or more conversations is not measured at all: all its holds degrade to
`more than two legs share this title` and keep today's remedies. Conservative on purpose;
every group ever observed in this store was a pair, and a store that produces triples has a
mess this feature must describe, not adjudicate.

## Where the pieces live today (0.13.0)

Nearly everything exists; this feature is composition, not construction:

- `_message_fingerprints(path)` (~5793) — prose-turn fingerprints for one transcript.
  Excludes tool calls/results and ids/timestamps (both exclusions argued and measured in its
  docstring); returns `None` when the file is unreadable or larger than
  `TRANSCRIPT_COMPARE_MAX_BYTES` (96 MB, ~5747). Its documented failure mode is the `None`
  return; this feature nevertheless wraps its calls (see §1), because "the helper can only
  fail via None" is an assumption, not a contract.
- `OVERLAP_MIN_SAMPLE` (= 8, ~5751) — the floor below which a percentage would read as
  precise while meaning nothing; existing users report "not measured" under it.
- The one-directional overlap helper (~6896, serving `sync --update`'s displacement
  warnings) — including the multi-transcript refusal: `find_transcripts` can return several
  project dirs holding one sid, and comparing `[0]` would measure a file we only might have
  meant, so that state is "unmeasured", never a guess.
- `_duplicate_title_groups(env, rows)` (~2857, consumed by `gather_doctor` ~3206) — full
  pairwise `a_in_b`/`b_in_a` percentages, unique counts, `unmeasured` reasons, sorted
  most-redundant-first. Doctor already shows users these numbers for duplicate titles; this
  spec brings the same arithmetic to the place a user acts on them.
- `plan_converge`'s two `held_title_collision` shapes (~10368, ~10396): (a) the chosen title
  already names a different conversation in the destination sidebar — the hold carries
  `retitle: _retitle_command(hit[0], "<new title>")`, i.e. **the blocking conversation**;
  (b) two planned conversations converge on one title — the deterministically-ordered later
  sid holds, remedy names itself. `_retitle_command(sid, title)` renders the copy-paste
  line. ("Later" in shape (b)'s ordering is sid order, a tiebreak for *which pair holds* —
  unrelated to this spec's recency ordering, and the two never mix: measurement runs on the
  pair after the hold exists, whichever ordering produced it.)
- The scan `plan_converge` already ran: `records` (every row on the machine, with
  `createdAt` and `lastActivityAt`), `acct_titles`, and `title_key()` — the one shared
  comparator. `title_key` is trimmed-exact matching (its docstring: the deliberate
  comparator both `alignment` and the collision hold share); the taken-title checks below
  go through it, and a test pins that a generated title cannot collide with the *later
  leg's own title* under the key.
- Terminology guard: **"containment" in this module means path containment**
  (`ensure_contained`, a security check). This feature says *overlap* and *supersession*
  everywhere, including identifiers.

## The change

### 1. Measure at hold time, from a typed per-plan cache, under a budget

When `plan_converge` constructs a `held_title_collision` hold, it measures the pair — the
held conversation and the one it collided with (`hit[0]`, or the counterpart sid in shape
(b)):

- Fingerprints via `_message_fingerprints`, resolved through the same single-transcript
  rule as the ~6896 helper (0 or 2+ transcript files → unmeasured, reason
  `several transcripts carry this id` / `no transcript`).
- **The plan already reads transcripts — integrate, don't duplicate.** `plan_converge`
  calls `_transcript_facts` (~10342) for every conversation it will create rows for, and
  that helper already calls `_message_fingerprints`. The measurement cache is therefore
  wired through that existing pass: fingerprints computed for a planned sid are reused,
  and the measurement typically *adds* one read per pair (the blocking side, which is not
  planned). Every read-count claim, test, and the budget below is scoped to
  **measurement-added reads**, counted at the cache layer — not to
  `_message_fingerprints` invocations globally, which the plan makes anyway.
- **The cache is typed, not fingerprints-or-None:** per sid,
  `(status, reason, fingerprints)` — so `unreadable or too large`, `no transcript`,
  `several transcripts carry this id`, and `measurement budget exhausted` stay
  distinguishable at reporting time. The cache is per plan run and sid-global within it,
  which is sound because transcript resolution is sid-global (transcripts live under
  `projects_root`, outside any account dir) — the same verdict holds for a later hold on
  the same sid in a different destination.
- **A plan-wide byte budget bounds the I/O:** `MEASURE_MAX_TOTAL_BYTES = 256 * 1024 * 1024`.
  A sid's transcript size is charged **only when the file will actually be read**: a file
  already over `TRANSCRIPT_COMPARE_MAX_BYTES` degrades its pair as oversized *without
  touching the budget* (charging for bytes never read would let one 200 MB file starve
  every later pair, as the review computed). A read that would push the charged total past
  the budget is not performed; that pair and all later unmeasured-by-budget pairs report
  `measurement budget exhausted`. Worst case is therefore ~256 MB actually read on a
  pathological store and zero reads on a holds-free plan. This is what makes the advisory
  promise honest and is the stated reason no `--no-measure` flag ships: the escape hatch
  exists, it is just automatic.
- **Advisory by construction, including against exceptions:** the whole per-pair pipeline —
  measurement *and* title generation — runs under a broad `except Exception`, degrading
  that hold to today's remedy plus
  `measurement failed (<exception type>)`. No new `Refusal` exists anywhere in this
  feature. This is a deliberate inversion of `_retitle_scan`'s strictness: the scan's
  strictness protects *what converge writes*; this measurement only decorates *what
  converge says*, and a plan that dies on a corrupt transcript would convert advice into a
  guard nobody asked for.

### 2. Classify: supersession, distinct, or unmeasured — and pick the superseded side by overlap, not clocks

With fingerprint sets `A` and `B`, `shared = |A ∩ B|`, and the two directional ratios
`a_in_b = shared/|A|`, `b_in_a = shared/|B|` (the same two numbers doctor's report prints).
One honesty note inherited with the machinery: `_message_fingerprints` hashes normalized
prose prefixes into a *set*, so repeated identical turns collapse and very long turns
compare by their opening — "prose turns" is the module's established approximation, the
same one doctor and sync's displacement warnings already print, not a claim of exact
transcript equality. The band, the two-signal agreement rule, and the human holding the
paste are the margins for that approximation — including its known residual, two runs of a
templated conversation measuring as near-identical (the measured line shows the numbers;
the human knows their own templates):

- **Unmeasured** first, when the pair's `title_key` group holds more than two
  conversations — where the group census is a **prepass counting every holder of the
  key: existing rows *and* this plan's chosen titles**, so a three-way shape-(b)
  collision (three conversations whose planned titles coincide with no row carrying the
  key yet) is caught exactly like a three-row one (`more than two legs share this title`
  — argued under "The evidence"),
  when either side's cache status is not ok, when either side's **own fingerprint count**
  (`|A|` or `|B|`, never the intersection) is below `OVERLAP_MIN_SAMPLE`, or when any rule
  below ends in disagreement or a tie. The reason string comes from a fixed vocabulary
  (never interpolating titles or paths — that keeps `--anonymize` trivially safe for
  reasons).
- **Bands, not a binary** (`SUPERSESSION_MIN_OVERLAP = 0.8`,
  `DISTINCT_MAX_OVERLAP = 0.2`, both module constants whose comment records the
  calibration set — supersessions measured at 0.98, 0.98, 0.80; distinct pairs near 0.0 —
  and instructs re-derivation, not trust, if a real pair ever lands between the bands):
  - `max(a_in_b, b_in_a) >= 0.8` → **supersession-shaped**; continue to side-selection.
  - `max(a_in_b, b_in_a) <= 0.2` → **distinct** (deliberately not "branch": a shared title
    does not prove shared ancestry, and the output must not claim it — two unrelated
    conversations under one generic name classify here too, correctly).
  - Between the bands → **unmeasured**, reason `inconclusive overlap` (with both ratios in
    the printed line). The most fragile number in the design must produce *hedged* output
    at its boundary, never a confident wrong sentence.
- **Side-selection: the superseded leg is the more-contained leg, corroborated by
  recency.** The candidate is the side with the higher ratio (its content continues in the
  other). Recency — `max(lastActivityAt)` over every row pointing at each sid, all written
  by this one machine's clock (rows are local files; no cross-machine skew exists in this
  store model) — must agree that the candidate is also the *older* leg:
  - **A recency tie (equal `max(lastActivityAt)`, either branch below) is unmeasured**
    (`legs cannot be ordered`) — checked before anything else, so neither branch has an
    undefined tie state.
  - Ratios asymmetric (differ by more than `0.1`): candidate = higher-ratio side. If
    recency disagrees — the contained leg is the *newer* one — the pair is **unmeasured**,
    reason `overlap and recency disagree`. This is the guard against the trunk-touched-
    after-forking case the review named: when the two signals point at different legs, the
    honest output is neither.
  - Ratios symmetric (within `0.1`, the mutual-fork shape all three 2026-08-29 pairs had):
    recency picks the superseded leg, **guarded by a margin** —
    `RECENCY_MARGIN_MS = 5 * 60 * 1000`; a gap under it is unmeasured
    (`legs cannot be ordered`), so timestamp jitter and touch-updates cannot decide.
    Recency deciding here is argued, not assumed: symmetric mutual overlap means the two
    legs are the *same content* give or take at most 10%, so "superseded" degrades to
    "less recently touched" — which is the ordering the user's own sidebar already shows —
    and the cost of the recency signal being wrong is bounded by that same ≤10%
    asymmetry. The trunk-touched-after-forking hazard belongs to the *asymmetric* branch
    (real divergent work on one side), where the disagreement guard above refuses. All
    three measured 2026-08-29 pairs pass the margin (gaps of 15 minutes to a day).
  - Any row of either leg missing a usable `createdAt`/`lastActivityAt` (absent, null,
    non-numeric), or a leg whose `min(createdAt) > max(lastActivityAt)`: **unmeasured**
    (`row dates unusable`).

### 3. The remedy per class

**Supersession** — the hold's remedy becomes a complete command naming the superseded leg,
whichever side of the hold it is on. Shape (a):

```
   s-early12 -> account-c (aaaa1111/cccc3333): held_title_collision - 'ACME-REVIEW session'
      already names a different conversation in that sidebar: row local_….json (opens s-late34)
      measured: s-early12 is the earlier leg - 52 of its 53 prose turns continue in
         s-late34, which adds 11 more; last activity Aug 28 vs Aug 29
      claude-code-sessions retitle --only s-early12 --title "ACME-REVIEW session - earlier leg (Aug 24-28)" --apply
```

Shape (b), where the hold text otherwise never names the counterpart, the measured line
carries both sids:

```
   s-late34 -> account-c (aaaa1111/cccc3333): held_title_collision - this plan already
      creates a row named 'ACME-REVIEW session' there, for conversation s-early12
      measured: s-early12 is the earlier leg - 52 of its 53 prose turns continue in
         s-late34, which adds 11 more; last activity Aug 28 vs Aug 29
      claude-code-sessions retitle --only s-early12 --title "ACME-REVIEW session - earlier leg (Aug 24-28)" --apply
```

The generated title is `<base> - earlier leg (<range>)` where:

- `<base>` is the colliding title, trimmed. If it already ends with text matching the
  *exact* generated grammar — `- earlier leg (<range>)` with the range in one of the
  **four** formats below — that suffix is replaced (a second fork of an already-renamed
  leg); a tail that merely resembles it — different wording or a malformed range —
  degrades instead (`title already carries a leg suffix`). The residual is acknowledged
  rather than hidden: exact-grammar match is treated as tool provenance, so a human who
  hand-typed a byte-exact generated suffix would get its *range* refreshed — accepted,
  because the range is the only thing rewritten and it is being rewritten to the truth.
- `<range>` comes from the superseded leg's rows: `min(createdAt)` to
  `max(lastActivityAt)`, rendered in local time, in one of exactly four formats —
  `Aug 28` (single day), `Aug 24-28` (one month), `Aug 24 - Sep 2` (across months),
  `2026-12-30 - 2027-01-02` (across years). The dates are a label for a human, not
  evidence; local-time rendering matches every date the person has ever seen in their
  sidebar.
- **The command is printed only when the title is shell-inert and will actually apply.**
  Two different degrades here, because the failures differ in kind:
  - *Printability first:* a generated title containing control characters or newlines is
    dropped entirely — `degrade_reason: title not printable`, `suggested_title: null` —
    because emitting it even "as prose" would let it forge report lines or corrupt
    terminal layout. (Bases are stored sidebar titles, so this is near-impossible; the
    rule exists so the impossible case is defined, not discovered.)
  - *Shell safety:* the printed line wraps the title in double quotes, inside which
    PowerShell and POSIX shells still interpret `$`, backtick, backslash and the quote
    itself (plus `!` under bash history expansion and `%` under cmd). A printable title
    containing any of `" $ \` (backtick) `\ ! %` degrades the *command only*
    (`degrade_reason: title not shell-safe`): the measured line still ends with
    `suggested name: <title>` and `suggested_title` stays non-null, because the title
    itself is perfectly valid — the GUI applies it through `plan_retitle`, no shell
    involved; only the pasted string would be unsafe. Refusing rather than escaping is
    deliberate: one printed string cannot be correctly escaped for two shell families at
    once, and an injectable remedy is worse than no remedy.
  - *It must be a rename the leg can take uniformly:* generated only when every row of the
    superseded leg shares one `title_key` — a leg whose titles already diverge across
    accounts is mid-repair by other means, and a global `retitle --only` would overwrite
    intentional differences (`the leg's titles diverge across accounts`).
  - *It must be free:* the generated title's `title_key` must match no existing row title
    in any account (the plan's full scan, not just destination `acct_titles`) — including
    the later leg's own title — and no *different-target* suggestion this plan already
    made. The **same** (sid, title) suggestion repeating across a group's holds is one
    suggestion printed identically in each hold, not a collision — that repetition is
    test 2's point.
  - *It must be valid:* the generated title passes the same validation `plan_retitle`
    applies to `--title` (trimmed, non-empty, plus any constraint that validator enforces
    — the implementer routes generation through it rather than re-deriving rules).
  Any check failing degrades that hold's *command* to the placeholder, records the check's
  name in `degrade_reason`, and keeps the measured line. `suggested_title` survives only
  the shell-safety degrade (argued above); every other failed check nulls it — a title
  that is taken, divergent-unsafe, unprintable, or validator-refused is not a suggestion,
  and handing it to the GUI as one would apply a title this spec itself rejected.
- *Staleness between the printed plan and the human's paste is retitle's own problem, and
  it already solves it:* `--only` must resolve to exactly one conversation at *its* run
  time and lists candidates on ambiguity, and a re-plan after any store change re-measures
  from scratch. The remedy carries no baked-in state beyond the sid and the new title.

One decoration for a wart the review named: when the **current** leg's own title matches
the generated grammar (a fork of an already-renamed leg inherited `… - earlier leg (…)`),
the measured block gains one fixed line —
`note: the current leg also carries a leg suffix and likely wants a fresh name` — because
the remedy renames only the superseded side, and without the note the active leg stays
branded "earlier" until a human notices.

**Distinct** — no generated name (the tool cannot invent two descriptive titles), but the
measurement is still the finding:

```
      measured: largely distinct conversations - they share 2 of 41 and 2 of 38 prose
         turns; both need human names
      claude-code-sessions retitle --only <id> --title "<new title>" --apply
```

**Unmeasured** — today's remedy line, preceded by `      not measured: <reason>` (with both
ratios included when the reason is `inconclusive overlap`), so silence is never ambiguous.

The rule for *which conversation the remedy names* changes only on supersession: the
superseded leg, in every hold of the group. Distinct and unmeasured keep today's targets
(the blocking side in shape (a), the deterministic later sid in shape (b)) — where there is
no measured basis to redirect, current behavior is the honest default.

### 4. Manifest, JSON, anonymize

Every **plan-time** `held_title_collision` hold gains a `measured` object — always present
on those holds, never omitted. (The apply-time re-check hold at ~10565 keeps its 0.13.0
shape with no `measured` key, per Compatibility; consumers read with `.get`, and this
sentence is the scope of "always present".)

```
"measured": {"classification": "supersession" | "distinct" | "unmeasured",
             "reason": <string, unmeasured only; null otherwise>,
             "superseded": <sid; supersession only, else null>,
             "current": <sid; supersession only, else null>,
             "shared": <int|null>, "a": <sid|null>, "a_total": <int|null>,
             "b": <sid|null>, "b_total": <int|null>,
             "suggested_title": <string|null>,
             "degrade_reason": <string|null>,
             "command_runnable": <bool>}
```

**Under `--anonymize`, `command_runnable` is forced `false`.** Anonymized output exists to
be pasted somewhere public, and its rendered command necessarily carries an opaque label
where the title was — a command that would literally rename a conversation to that label.
A "runnable" flag on it would be false actionability (round 3's blocker); anonymized
reports are display artifacts, and the flag now says so.

Nullability rules, stated so test 26 tests something definite:

- `a`/`b` (the pair's sids) are always present once a pair exists; the numeric trio
  (`shared`, `a_total`, `b_total`) is non-null **iff both sides were fingerprinted** —
  so `inconclusive overlap` carries its numbers, while one-sided states (`no transcript`
  on one leg) carry none, because an intersection with an unmeasured side does not exist.
- `degrade_reason` is non-null exactly when a supersession's title generation failed a §3
  check (`title not shell-safe`, `title not printable`, `suggested name already taken`,
  `the leg's titles diverge across accounts`, `title already carries a leg suffix`,
  `title rejected by retitle's validation`); `reason` remains the *classification*-level
  field for `unmeasured`. Two fields because they answer different questions — "could we
  measure?" and "could we compose the rename?".
- **`command_runnable` is the entire consumer contract**: `true` iff the hold's `retitle`
  field carries the fully rendered, checked command; `false` means `retitle` is today's
  placeholder template. No consumer reconstructs runnability from classification and
  title nullability — round 2 proved that predicate wrong (a shell-unsafe supersession
  has a non-null `suggested_title` and a placeholder command). The GUI applies
  `suggested_title` through `plan_retitle` when it is non-null, and treats
  `command_runnable` as what it says about the *rendered string* only.

`--anonymize` coverage is by construction, not by field-chasing: the rendered `retitle`
string is produced *at emit time* — in `_print_converge_report` and the JSON serializer —
from the manifest's structured fields, after `anonymize_report` has run over them. So
anonymizing `suggested_title` (added to `_ANON_FIELDS`) anonymizes the command, because
the command is derived from the field rather than stored beside it. The mechanism, stated
precisely this time (round 2's "wholesale" description was wrong about how the anonymizer
works, though right about the outcome): `anonymize()` substitutes **known stored titles as
substrings** wherever they appear in a value — and the base of every generated title *is*
a stored row title by construction (it is the colliding title), so
`<base> - earlier leg (<range>)` anonymizes to `<label> - earlier leg (<range>)`. The
suffix and range carry no identity; a test pins the suffix-replacement path specifically. Session ids in the command (`--only <sid>`) are
deliberately not anonymized: ids are opaque machine identifiers the anonymizer has never
treated as content, in this feature or any other. Reason and degrade-reason strings are a
fixed vocabulary containing no titles or paths. Turn counts and dates pass through: they
are the same class of information alignment's counts already print under `--anonymize`.

### 5. Reuse shape (B2 lives downstream of this)

The measurement/classification lands as one function —
`_overlap_verdict(env, sid_a, sid_b, records, cache, budget)` returning the `measured`
dict — with title generation separate (`_superseded_leg_title(base, rows)`). B2 (row
retirement) needs exactly the first function's verdict; no B2 behavior ships here.

## Compatibility and ordering

- Purely additive to the manifest, with the same scope honesty as the 0.13.0 spec: this
  tool's own `.get`-based consumers are the only consumers. 0.13.x manifests (no
  `measured`) replay, undo, and recover unchanged; apply-time re-checks do not consult
  `measured`, and the apply-time hold (~10565) keeps its current unmeasured form —
  re-measuring under the lock would add I/O to the write path for a message.
- Dry run and apply exit codes unchanged (0 / 3-on-holds / 1).
- **Performance, stated as numbers and scoped to what the feature adds:** the plan
  already reads transcripts for its own purposes (`_transcript_facts`, every planned
  conversation); the measurement adds **zero reads when no hold exists** (test-pinned at
  the cache layer), at most one-to-two *added* reads per distinct collision pair
  (fingerprints the plan already computed are reused), and at most
  `MEASURE_MAX_TOTAL_BYTES` (256 MB) of added reads across the whole plan, after which
  remaining pairs report `measurement budget exhausted`. Fingerprint sets live only for
  the plan call.
- Version: lands in 0.14.0. README's converge section swaps its hold example for a
  measured one. Release notes: fake cast, `--anonymize` for real output, the public-safety
  scan before push.

## Tests

In `tests/test_converge.py` unless noted; fixtures via the existing
`mkenv`/`write_row`/`write_transcript`, plus a helper writing N distinct prose turns
(≥ `OVERLAP_MIN_SAMPLE` where measurability is wanted).

1. `test_supersession_hold_names_the_superseded_leg` — asymmetric overlap (48/50 vs
   48/61), contained side also older; remedy carries that sid and the generated title.
2. `test_both_directions_of_a_group_print_one_identical_remedy` — holds both ways; both
   carry the same command; the repeated (sid, title) suggestion is not a self-collision.
3. `test_replan_after_the_paste_clears_the_group` — apply the suggested title to the
   superseded leg's rows in the fixture; a fresh plan holds nothing for that pair (shapes
   (a) and (b) both).
4. `test_overlap_picks_the_side_recency_confirms` — symmetric ratios, recency decides
   (the measured 2026-08-29 shape).
5. `test_overlap_and_recency_disagreeing_is_unmeasured` — contained leg is the newer one;
   `overlap and recency disagree`; placeholder remedy.
6. `test_inconclusive_band_is_unmeasured_with_ratios` — overlap 0.5; reason
   `inconclusive overlap`; both ratios printed; no generated title.
7. `test_band_edges` — 0.8 exactly is supersession-shaped; 0.2 exactly is distinct.
8. `test_distinct_wording_claims_no_ancestry` — near-zero overlap; output says
   `largely distinct`, not "branch"; both totals present.
9. `test_generated_title_shape` — single-day, same-month, cross-month, cross-year
   renderings.
10. `test_exact_suffix_is_replaced_lookalike_degrades` — a leg already named
    `… - earlier leg (Aug 1-3)` gets its range replaced, and so does a cross-year
    `… - earlier leg (2026-12-30 - 2027-01-02)`; a user-authored
    `… - earlier leg (probably)` degrades with `title already carries a leg suffix`.
11. `test_shell_unsafe_title_degrades_but_suggests` — base containing `$`; command
    degrades to placeholder, `command_runnable` false,
    `degrade_reason == "title not shell-safe"`, measured line ends `suggested name: …`,
    `suggested_title` non-null; same per-character for backtick, `!`, `%`, a double
    quote.
11b. `test_unprintable_title_drops_the_suggestion` — base with an embedded newline or
    control character; `suggested_title` null, `degrade_reason == "title not printable"`,
    and no raw control byte appears anywhere in the rendered report.
11c. `test_three_leg_group_degrades_every_hold` — three conversations on one `title_key`;
    every hold reports `more than two legs share this title`; nothing is fingerprinted
    for the group.
11d. `test_apply_time_hold_carries_no_measured_key` — the ~10565 re-check hold keeps its
    0.13.0 shape.
12. `test_divergent_leg_titles_degrade` — the superseded leg's rows carry two different
    titles; `the leg's titles diverge across accounts`.
13. `test_taken_suggestion_degrades` — including the case where the *later leg's own
    title* equals the would-be suggestion under `title_key`.
14. `test_two_different_targets_cannot_share_a_suggestion` — two groups whose generated
    titles collide; the second degrades.
15. `test_title_validation_is_retitles_own` — monkeypatch the retitle validator to refuse;
    generation degrades rather than printing what retitle would reject.
16. `test_missing_row_dates_degrade` — null `lastActivityAt` on one row; inverted
    `createdAt > lastActivityAt`; both `row dates unusable`.
17. `test_recency_tie_is_unmeasured` — equal `max(lastActivityAt)` in *both* the
    symmetric-ratio and asymmetric-ratio branches; both report `legs cannot be ordered`.
18. `test_dead_collision_row_degrades` — blocking conversation has no transcript; plan
    otherwise unchanged (regression: no Refusal).
19. `test_ambiguous_transcript_count_degrades` — one sid in two project dirs.
20. `test_oversized_transcript_degrades` — monkeypatched `TRANSCRIPT_COMPARE_MAX_BYTES`.
21. `test_below_sample_floor_degrades`.
22. `test_budget_exhaustion_degrades_later_pairs` — monkeypatched
    `MEASURE_MAX_TOTAL_BYTES` below the second pair's size; first pair measured, second
    `measurement budget exhausted`.
22b. `test_oversized_transcript_charges_no_budget` — an over-cap transcript degrades its
    own pair as oversized while a later, smaller pair still measures (the budget was not
    consumed by bytes never read).
23. `test_exception_in_measurement_degrades` — monkeypatch `_message_fingerprints` to
    raise; `measurement failed (...)`; the plan completes.
24. `test_measurement_cache_bounds_added_reads` — N holds over one pair; the
    measurement-path cache performs at most two loads, and a fingerprint set the plan's
    `_transcript_facts` pass already computed is reused rather than re-read.
25. `test_holds_free_plan_adds_no_measurement_reads` — instrumented at the measurement
    cache layer (not raw `_message_fingerprints`, which the plan legitimately calls for
    planned rows); a plan with no collisions performs zero measurement loads.
25b. `test_planned_three_way_collision_degrades` — three conversations whose *chosen*
    titles coincide while no existing row carries the key; the prepass census catches it;
    every hold reports `more than two legs share this title`.
25c. `test_symmetric_recency_below_margin_is_unmeasured` — symmetric ratios, activity gap
    under `RECENCY_MARGIN_MS`; `legs cannot be ordered`.
25d. `test_current_leg_wearing_suffix_gets_the_note` — fork of an already-renamed leg;
    the fixed note line appears; only the superseded side gets a command.
25e. `test_anonymized_output_is_never_runnable` (`tests/test_anonymize.py`) —
    `--anonymize --json`: every hold's `command_runnable` is false and the rendered
    command carries the opaque label, not the real title.
26. `test_json_measured_schema` — full object per class, including a *degraded
    supersession* (`command_runnable` false, `degrade_reason` set, `retitle` placeholder)
    and a one-sided unmeasured pair (numeric trio null); nullability per §4; parseable.
27. `test_anonymize_covers_the_rendered_command` (`tests/test_anonymize.py`) — under
    `--anonymize`, the generated title appears in neither the measured line, the
    `suggested_title` field, nor the rendered `retitle` string, text or JSON — including
    the suffix-*replacement* path, where the base of the generated title is itself a real
    stored title.
28. `test_planned_collision_shape_is_measured_too` — shape (b) supersession redirects the
    remedy; the measured line names both sids.

## Non-goals, stated so the review can disagree with them

- **No auto-apply.** The measurement composes a command; a human runs it. Renaming rows on
  overlap arithmetic alone would put a five-observation calibration in charge of
  user-pinned titles.
- **No `--no-measure` flag.** The byte budget is the escape hatch, and it is automatic;
  a store pathological enough to exhaust it degrades loudly per pair.
- **No conditional/stateful remedy** (expected-state fingerprints baked into the printed
  command). retitle's own run-time resolution and converge's replan are the staleness
  guards; a command that encodes plan-time state would fail on harmless drift and still
  not catch harmful drift retitle's guards miss.
- **No per-gesture threshold study.** The review asked whether prompt-edit forks might
  land mid-band; the 2026-08-29 rewind-to-edit pairs measured 0.98 — the gesture copies
  the whole prefix, so near-total overlap is structural. The inconclusive band exists for
  whatever gesture eventually proves otherwise.
- **The apply-time hold (~10565) keeps its unmeasured form** — argued under Compatibility.
- **The divergent-title `notes` (~10360) are untouched** — different mechanism, remedies
  already carry real titles.
- **B2 does not ship here**; `_overlap_verdict` is shaped for it, no more.
- **`doctor`'s duplicate-title report is not rewritten** onto the new classifier — same
  arithmetic already, presentation unification is cosmetic and separate.

## Review

**Round 1, 2026-08-30 (~00:35).** Full four-engine panel: Codex (xhigh) and Gemini
(gemini-3.1-pro-high via agy) reported; DeepSeek and Kimi reported on the roster's
**sealed** route — chosen over the repo-aware default because the previous night's
repo-aware runs failed deterministically on this repo (both models ingested the ~560 KB
main module and the final request aborted at transport level; documented in the 0.13.0
spec's review). Kimi's canary quoted the penultimate line; on the roster the mechanical
completion signals are authoritative, so both roster reviews count as complete.

**Applied:**

- **[Codex+DeepSeek, critical] Shell safety rebuilt.** The draft refused only double
  quotes; `$()`, backticks, `\`, `!` and `%` are all live inside double quotes in at least
  one target shell, so a hostile title could execute on paste. Now: a shell-inert
  character check drawn per-shell (§3), degrade with the suggestion preserved as prose,
  and an explicit refusal to escape (one string cannot be safely escaped for two shell
  families). Tests cover each metacharacter.
- **[Gemini, critical] Direction comes from overlap, corroborated by recency.** The draft
  picked "earlier" by timestamp alone; touching the old trunk after a fork would flip it
  and rename the live leg — the exact harm the feature exists to fix. Now: the superseded
  side is the more-contained side; recency must agree; disagreement is unmeasured
  (`overlap and recency disagree`). Gemini's denominator-asymmetry corollary (ratio
  computed against the timestamp-chosen side) dissolves with the same change.
- **[Kimi highest + Codex] The binary classifier became a band.** ≥0.8 supersession-shaped,
  ≤0.2 distinct, between → `inconclusive overlap` with both ratios printed. The draft's
  "branch otherwise" would have produced its most confident sentence from its most
  fragile number. "Branch" is renamed **distinct** throughout — a shared title does not
  prove shared ancestry, and the old word claimed it.
- **[Codex] Group-level suggestion identity.** The draft's "no suggestion may collide with
  any other this plan made" contradicted its own test 2 (both directions of a group must
  print the same command). Now: identical (sid, title) repetition is one suggestion;
  only different-target collisions degrade.
- **[Codex+Gemini+Kimi] The advisory promise is now arithmetic.** "Never slow-fail" was
  unsupported against 96 MB × unbounded pairs. Now: a typed cache, a 256 MB per-plan byte
  budget charged before reading, `measurement budget exhausted` per remaining pair, and
  the budget stated as the reason no flag ships.
- **[Codex] Typed cache** preserving failure provenance (unreadable vs oversized vs
  ambiguous vs budget), replacing fingerprints-or-None.
- **[Kimi] Exception wrapping stated** (`measurement failed (<type>)`) — "the helper only
  fails via None" was an assumption.
- **[Kimi+Codex] Anonymize by construction.** The rendered `retitle` command is emitted
  from structured fields *after* `anonymize_report`, so anonymizing `suggested_title`
  covers the command; reasons are a fixed title-free vocabulary. Test 27 pins the
  rendered string.
- **[Kimi] The `retitle` consumer contract stated** (runnable iff supersession with a
  title); the GUI reads `suggested_title`.
- **[DeepSeek] Uniform-title guard.** `retitle --only` renames a conversation in every
  account; a leg whose titles already diverge across accounts degrades rather than
  receiving a command that would flatten intentional differences.
- **[Kimi] "One retitle clears the group" now argued and test-pinned** (§ evidence,
  test 3 replans after the paste), including why shape (b) clears.
- **[Kimi] `title_key` characterized** (trimmed-exact) and the later-leg's-own-title
  collision case added to test 13.
- **[Gemini] Single-day date rendering** (`Aug 28`, not `Aug 28-28`); local-time rendering
  stated; inverted ranges degrade.
- **[Kimi] Sid-global cache validity argued**; shape (b) example output added; the two
  "later" orderings (sid-order vs recency) explicitly disentangled.
- **[Kimi+DeepSeek] Missing/null/inverted timestamps degrade** (`row dates unusable`),
  with tests.
- **[Kimi] Suffix replacement narrowed to the exact generated grammar**; lookalike tails
  degrade — silently rewriting user-authored prose is not this feature's job.
- **[Codex+Kimi] Tests grew 18 → 28**, adding the metacharacter set, band edges, replan-
  clears-group, signal disagreement, divergent titles, budget exhaustion, exceptions, and
  the anonymized rendered command.

**Declined, with reasons:**

- **[Codex] Ordered-ancestry evidence (sequence alignment) instead of set overlap.** The
  existing fingerprint machinery is set-based, its two exclusions were measured, and every
  decisive state now requires two agreeing signals plus a wide band of refusal between the
  classes; ordered alignment would be new machinery for precision the degrade ladder
  already substitutes with honesty. Revisit if a real pair ever defeats the band.
- **[Codex] Structured/conditional remedy executed by the tool.** Same ground as
  no-auto-apply plus the staleness non-goal: retitle re-validates at its own run time,
  and the paste is the human checkpoint this design wants to keep.
- **[DeepSeek] Per-gesture threshold study** — answered by measurement already in hand
  (rewind forks are 0.98 by construction); the band covers the residual.
- **[Gemini] Single-quote wrapping** — safer in bash, differently unsafe in PowerShell
  (and apostrophes are common in prose titles); the shell-inert check plus prose fallback
  loses nothing a dual-shell escape could safely win.
- **[Kimi] Quasi-identifier review of counts/dates under `--anonymize`** — turn counts and
  dates are the same class alignment already emits anonymized; treating them as identity
  would anonymize the report into uselessness. Noted, not adopted.

**Round 2, same sitting.** Full four-engine panel again (sealed roster; both roster
canaries were tail-anchored formatting quirks, mechanical signals clean). Every engine
found the same central defect — round 1's consumer contract broken by round 1's own
shell-safety degrade — which is the defective-fix clause doing its job twice in one spec.

**Applied:**

- **[Codex+DeepSeek+Kimi, blocker] The consumer contract is now a field, not a
  predicate.** `command_runnable` says whether `retitle` carries the checked command;
  `degrade_reason` (new, fixed vocabulary) says which §3 check failed; `suggested_title`
  nullability is defined per degrade — it survives only shell-unsafety (the one failure
  where the title itself is valid and the GUI can apply it via `plan_retitle`, no shell
  involved); taken/divergent/unprintable/validator-refused null it. Reconstructing
  runnability from classification+title — round 1's rule — is dead.
- **[Gemini+DeepSeek+Codex, blocker] N>2 groups excluded honestly.** Three legs on one
  `title_key` form a collision graph one rename cannot clear and pairwise verdicts can
  contradict; such groups now degrade wholesale (`more than two legs share this title`),
  the one-retitle claim is scoped to pairs, and a three-leg test pins it.
- **[Codex+Gemini] Unprintable titles are dropped, not "preserved as prose."** Control
  characters and newlines forge report lines; that degrade nulls the suggestion entirely
  (`title not printable`). The prose fallback survives only for shell-unsafe *printable*
  titles.
- **[DeepSeek] Budget accounting charges only bytes actually read** — an over-cap file
  degrades its pair without spending budget the review showed it could otherwise burn
  (200 MB charged, zero read).
- **[Kimi] "Always present" scoped to plan-time holds**; the apply-time hold's
  0.13.0 shape is stated and test-pinned (11d).
- **[Kimi] One-sided nullability defined** — the numeric trio exists iff both sides were
  fingerprinted.
- **[Gemini+Kimi] The asymmetric recency tie defined** — any tie, either branch, is
  `legs cannot be ordered`, checked first.
- **[Gemini] `len` disambiguated** to each side's own fingerprint count.
- **[Gemini+Kimi] "Three formats" corrected to four**, the cross-year rendering spelled
  (`2026-12-30 - 2027-01-02`), test 10 extended to a cross-year suffix.
- **[Kimi] The exact-grammar provenance residual acknowledged in place** — exactness is
  treated as tool provenance; a hand-typed byte-exact suffix gets its range refreshed,
  accepted because the range is all that is rewritten.
- **[DeepSeek] The exception wrap covers title generation**, not just measurement.
- **[Codex] Anonymize mechanism clarified against the derived-prefix worry** — field
  replacement is wholesale (the value becomes one opaque label; nothing substring-
  matches), and test 27 now exercises the suffix-replacement path whose base is a real
  title.

**Declined, with reasons:**

- **[DeepSeek] Anonymizing the sid in `--only <sid>`.** Session ids are opaque machine
  identifiers; the anonymizer's contract has always been content (titles, projects,
  emails), and every anonymized report the tool has ever printed carries ids. Widening
  that contract is a product decision for `--anonymize` itself, not a rider on this
  feature.
- **[Codex] A structured remedy object replacing the rendered command.** `command_runnable`
  + `degrade_reason` + the existing structured fields give consumers everything the
  object would, without a second remedy representation to keep coherent; the rendered
  string stays what it has always been — a convenience for the human pasting it.

**Round 3, same sitting — the cap round.** Codex and Gemini reported (Codex's canary
rendered an em-dash as a hyphen — the documented cp1252 false-failure family; coverage
otherwise verified); the roster was not re-fired for the cap round. Both engines again
exercised the defective-fix clause, and one Codex claim was verified against the code
before being accepted.

**Applied:**

- **[Codex, blocker] Anonymized output is never runnable.** Rendering the command from
  post-anonymization fields — round 2's own fix — would produce a "runnable" command that
  renames a conversation to an opaque label. `command_runnable` is now forced false under
  `--anonymize`, with the argument (anonymized reports are display artifacts) and a test.
  In the same stroke, round 2's "wholesale replacement" description of the anonymizer was
  corrected: `anonymize()` substitutes known stored titles as substrings, which covers the
  generated title because its base is a stored title by construction.
- **[Codex, verified in code] The plan already reads transcripts.** `plan_converge` →
  `_transcript_facts` (~10342) → `_message_fingerprints`, for every planned conversation —
  so round 2's "zero transcript reads on a holds-free plan" was false as stated. All
  read-count claims, the budget, and tests 24–25 are now scoped to *measurement-added*
  reads at the cache layer, and the cache is wired through the existing pass so already-
  computed fingerprints are reused (the measurement typically adds one read per pair, not
  two).
- **[Codex] The >2-leg census counts planned titles too** — a three-way shape-(b)
  collision (chosen titles coinciding with no row yet carrying the key) is caught by the
  same prepass; test added.
- **[Gemini, partially] The symmetric branch gains a recency margin**
  (`RECENCY_MARGIN_MS`, 5 minutes): a gap under it is `legs cannot be ordered`, so
  timestamp jitter and touch-updates cannot decide. Full removal of the symmetric branch
  — Gemini's ask — was declined (below), but the volatility concern behind it is now a
  concrete guard, and the branch carries the argument for why recency may decide there at
  all (symmetric overlap means content-equivalent legs; the stakes are bounded by the
  ≤10% asymmetry; the trunk-touch hazard lives in the asymmetric branch, which refuses on
  disagreement).
- **[Gemini] The inherited-suffix wart is surfaced** — when the *current* leg's title
  matches the generated grammar (a fork of an already-renamed leg), a fixed note line
  says it likely wants a fresh name; the remedy still renames only the superseded side.
- **[Codex] Fingerprint semantics stated honestly** — normalized-prefix set hashing is the
  module's established approximation (the same numbers doctor already prints), named as
  such in §2 with its templated-conversation residual, rather than implied to be exact
  turn equality.

**Declined, with reasons:**

- **[Gemini] Delete the symmetric-recency branch entirely.** It would have suppressed all
  three measured 2026-08-29 remedies — the motivating cases — to guard against a hazard
  the asymmetric branch's disagreement rule already owns. The margin plus the bounded-
  stakes argument is the proportionate version of the same concern.
- **[Codex] A full-body, role-aware verification stage before any decisive verdict.** New
  measurement machinery, on top of arithmetic the module has shipped for two releases, to
  harden an advisory line a human reads before acting. The band, the agreement rule, and
  the margin are the accepted mitigations; revisit with B2, where a verdict would gate
  actual deletion.
- **[Codex] Binding the remedy to transcript identity / remeasuring at paste time.**
  Re-raised from rounds 1–2 in sharper form; the ground stands — the routine runs
  plan-and-paste in one sitting, `retitle` re-validates identity and ambiguity at its own
  run time, the rename is reversible by `undo`, and a suggestion that encodes plan-time
  state would refuse on harmless drift while still missing harmful drift outside its
  fingerprint. Recorded as the standing trade: this feature composes advice for the
  sitting that printed it.

**Loop closed at the 3-round cap.** Round 3's blocker (anonymize × runnability) and the
false performance claim are resolved above; those resolutions are review-driven but not
themselves panel-verified — the implementing session should treat §4's anonymize rules and
the measurement-cache integration as the two places to read most skeptically against the
code.
