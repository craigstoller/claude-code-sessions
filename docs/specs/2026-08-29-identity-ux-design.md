# 0.13.0 — the identity-assertion release (design)

**Status: draft, for handoff. Round-1 panel review applied — see "Review" at the end.**
Four small changes, one theme: when the two identity files disagree, the tool is *safe* but not
yet *kind*. Everything here was measured on a real three-account machine on 2026-08-29, during
the third full re-levelling pass.

Scope, stated precisely because the first draft overstated it: **nothing changes in what
RULING 4 or RULING 5 enforce at apply time.** Two *input-resolution* refusals are deliberately
narrowed (changes 3a and 3b), each with its acceptance boundary analyzed where it is made; no
mutation gains a new path around a guard.

Names below are the fake cast (`alice@example.com`, `bob@example.com`, account ids
`aaaa1111…`/`bbbb2222…`, org ids `cccc3333…`/`dddd4444…`). The measured behavior is real; the
identifiers are not.

## The night that produced this

The operator ran the documented maintenance routine: `alignment`, `converge` dry run, three
`retitle --apply`s, `converge --apply`. Every command behaved as designed. The evening still
cost three round-trips it didn't need to:

1. The `converge` dry run printed a clean plan — `366 of 379 -> 379 of 379 (0 held)` — with
   no hint of trouble. The operator closed the desktop app (ending the assistant session that
   had produced the commands), ran the retitles (fine), then `converge --apply` — which
   **refused**: `~/.claude.json` and `config.json` disagreed about the signed-in account
   (RULING 5). The refusal was correct. The surprise was not: the plan had known everything
   the refusal knew, and said nothing. Reopening the app to ask what to do cost the full
   close-the-app cycle the runbook existed to avoid.

2. The natural retry, `--live alice@example.com`, was refused: the email matched **three**
   stores, because every account owns a directory under every org it has touched and only one
   of the three holds rows. The value that finally worked was a 73-character path fragment
   spanning both uuids. Meanwhile every report the tool prints identifies accounts as
   `alice@example.com (aaaa1111/cccc3333)` — a form no resolver accepts back.

3. The refusal's first remedy — "switch the desktop app so the two agree" — had already been
   tried and had not worked: the desktop had been signed into the asserted account minutes
   earlier, and its `config.json` still carried the previous account. Measured, not supposed:
   `lastKnownAccountUuid` does not reliably freshen on a sign-in switch. (E4, 2026-08-02,
   measured the inverse staleness — `oauthAccount` stale while `config.json` tracked. Either
   file can lag. That is *why* RULING 5 exists; it is also why "make them agree by switching"
   is not advice the user can reliably follow.)

Each change below removes one of those round-trips.

## Where the pieces live today (0.12.0)

- `_identity_disagreement(env)` — `(oauth_uuid, config_uuid)` when the files name different
  accounts, else `None`. **Absent, unreadable, or malformed files are deliberately no signal**
  (the function's docstring argues this): oauth-only and config-only are legitimate states,
  and after RULING 4 mutation safety rests on the process guard, not this comparison. This
  release does not change that; every behavior below keys on a *truthy* disagreement and is
  silent in the no-signal states, exactly as the recheck already is.
- `live_account(env)` — fails closed (returns `None`) on a disagreement.
- `_resolve_live_assertion(env, live, dirs)` — validates `--live` (RULING 5). Requires a
  disagreement (refuses otherwise, both when the agreed account resolves and when nothing
  does); matches the user's string as a substring of
  `account_uuid + " " + org_uuid + " " + email + " " + path` per candidate store belonging to
  either disagreeing account; refuses on more than one match.
- `plan_converge` — resolves `live` via `_resolve_live_assertion` when `--live` was passed,
  else `live_account` (so `None` under a disagreement — the plan proceeds; `live` feeds only
  `_retitle_scan`'s account labels via `_email_of`). Records
  `live_asserted = live.account_uuid if flags.live and live else ""`.
- `_converge_recheck` — at apply, under the lock, pre-journal: refuses when
  `_identity_disagreement(env)` is truthy and `m["live_asserted"]` is not one of the two
  disagreeing uuids. **The consumed fact is the account uuid alone.** The org half of the
  resolved `--live` Account is never part of what apply checks or writes.
- The other two matcher sites: `resolve_sync_endpoints`'s `--to` (same joined haystack,
  refuses on >1) and `_repoint_store`'s `--store` (per-field substring — acct OR org OR email
  OR normalized path — returns all hits and lets the row settle it; `new-row` layers its
  row-count heuristic on top and refuses `--apply` when the heuristic chose).

## Change 1 — the dry run discloses the RULING 5 state

**The gap:** RULING 5 is evaluated only inside `_converge_recheck`, so a plan can read
`0 held` and the apply can still refuse for a reason fully knowable at plan time. The dry
run's implicit promise — "this is what --apply will do" — was broken in the one way that
costs an app-close cycle to discover.

**The change:** `plan_converge` records the disagreement on the manifest;
`cmd_converge` prints a warning block on every non-JSON run (dry and apply alike) when the
manifest carries one and no valid `--live` was given:

```
warning : ~/.claude.json (aaaa1111) and config.json (bbbb2222) disagree about
   the signed-in account. While that disagreement stands, --apply will refuse
   (RULING 5) unless you assert which account the desktop app is on:
      claude-code-sessions converge --live alice@example.com --apply    (if the app is on aaaa1111)
      claude-code-sessions converge --live bob@example.com --apply      (if the app is on bbbb2222)
   A refusal writes nothing, so trying --apply without --live is safe.
```

- The wording is conditional — "while that disagreement stands" — because the warning is a
  snapshot: the files can change in either direction before apply, and `_converge_recheck`
  keeps evaluating them fresh under the lock. Both transitions are legitimate: a plan with no
  warning can still refuse at apply (test 5), and a plan with the warning can apply cleanly
  if the disagreement has cleared (test 16). Disclosure and enforcement are different jobs
  at different times; the plan never claims to be the gate.
- Manifest: `"identity_disagreement": {"oauth": "<uuid>", "config": "<uuid>"}` or absent.
  `_public_converge_manifest` carries it with ids shortened per its existing conventions, and
  `--anonymize` must preserve the structure (the values are machine ids, shortened in public
  output like every other id the tool prints).
- The remedy lines name each side by email — the oauth side's from
  `oauthAccount.emailAddress`, the config side's via `account_email` — falling back to the
  8-char account id when the email is unknown **or when the two sides' emails are equal**
  (two accounts under one address would otherwise print two identical, unusable remedies;
  the 8-char ids are already distinct, so nothing longer is needed). The parenthetical
  "(if the app is on …)" is the honest part: the tool cannot know which — that is the
  user's fact, which is the entire premise of RULING 5.
- When `--live` was passed and validated, no warning: the arbitration is already in hand.
- **The dry run still exits 0, and this is argued, not assumed.** A chained
  `converge && converge --apply` does not "proceed blindly into damage": the apply step
  re-evaluates RULING 5 itself, refuses loudly, writes nothing, and exits non-zero — the
  chain stops at the right step with the right message. The dry run's contract is to report
  the plan; the apply's contract is to gate. For machine callers the manifest field *is* the
  signal (`--json` carries it). A `--strict-identity` exit mode was considered and declined:
  it would duplicate the apply's own gate one step earlier for no additional safety, and the
  panel's objection — "the dry run still lies to scripts" — is answered by the field, not by
  a second gate (see Review).

## Change 2 — the tool's printed account form is a valid input

**The gap:** every report labels accounts as `email (aaaa1111/cccc3333)`, and none of the
three matcher sites accepts `aaaa1111/cccc3333` back: two join their haystack fields with
spaces, the third tests fields separately, so the `/` form matches nothing anywhere. What the
tool prints as an identifier, it should accept as one.

**The change — a parsed pair form, not a haystack addition.** (The first draft appended the
printed strings to the substring haystacks; the panel showed that mechanism is wrong twice
over — a mid-uuid fragment like `1111/cccc` would match *inside* `aaaa1111/cccc3333`, and a
legitimate 6-character pair like `aaaa11/cccc33` would fail because the `/` lands at a
different index. Substring semantics cannot express "two anchored prefixes". See Review.)

A query is **pair-shaped** when it contains exactly one `/` and both halves are non-empty and
consist only of `[0-9a-f-]` after lowercasing. A pair-shaped query is resolved structurally:

- it matches a store iff `account_uuid.lower().startswith(left)` **and**
  `org_uuid.lower().startswith(right)`;
- it is **never** also tried as a substring — a pair-shaped query that matches no store gets
  the ordinary "matched no store" refusal (with the candidate listing), not a silent fall
  back into the semantics whose over-matching this change exists to remove;
- a query that is not pair-shaped keeps today's substring semantics untouched, including
  paths: a cygwin-style path contains more than one `/`, an email contains none inside a
  hex-only half, so neither can be captured by accident.

Consequences, intended: the printed 8-char form works, the full-uuid form works, and so does
any unambiguous prefix pair (`d0fc/ef43`-style) — anchored prefixes are what a human
shortening an id actually types. If two stores genuinely share both prefixes, the ordinary
>1-match refusal fires, which is the correct outcome.

Applies to all three sites: `_resolve_live_assertion`, `resolve_sync_endpoints`'s `--to`,
and `_repoint_store`'s `--store` (where a pair-shaped query filters `dirs` the same way and
the existing let-the-row-settle-it behavior continues on whatever survives). The
`--live`/`--to` "matched N stores; be more specific" refusals gain one line:
`the printed account form works too, e.g. 'aaaa1111/cccc3333'`.

## Change 3 — `--live` resolves the fact it certifies

RULING 5's certified fact is *"the desktop app is signed into account X"* — account-level.
`config.json` doesn't even record an org. Yet `_resolve_live_assertion` demands the user's
string resolve to exactly one *store*, which is why an email — the natural way a person names
an account, and unambiguous as one — was refused for matching the account's three org dirs.

**3a — account-first resolution, for converge only.** `_resolve_live_assertion` gains a
keyword `account_scope=False`. When `True`, resolution runs over **the two disagreeing
account principals, not their stores**:

- Build two principals from the disagreement pair: for each of `oauth_uuid` and
  `config_uuid`, the account uuid, its email where known, and every store dir it owns
  (org uuids and paths).
- Match the query against each principal — pair-shaped queries per change 2 against its
  (account, org) dirs; other queries as a substring of the principal's combined
  uuid/email/org/path text.
- **Exactly one principal matching is acceptance; both or neither keep today's refusals**
  (the listing now grouped per account). Matching a *principal* rather than a store is what
  makes `--live alice@example.com` work while three org dirs exist — and it also covers the
  edge the store-backed draft could not: an account whose store dirs are missing entirely is
  still assertable by uuid or email, because the principal exists even when no directory
  does.

The returned Account carries **empty org and path** — there is no display representative to
choose, because nothing consumes one: converge uses `live_asserted` (the account uuid) in
`_converge_recheck`'s membership test and the account/email for `_retitle_scan`'s labels,
whose formatter already renders a missing org as nothing. Everywhere the plan prints the
live account resolved this way, the label reads `aaaa1111/-` (account asserted; org not part
of the assertion) — never a concrete pair a user might trust or re-paste. (Round 1 specified
a selection ladder for a "display org"; round 2 showed it was dead code once the label masks
it, so it is deleted rather than tested. See Review.)

Contrast `_new_row_store`, whose org pick decides **where a row is created**: there the
row-count heuristic is allowed to plan and refused at `--apply`, and that stays untouched.
Same heuristic, opposite stakes, hence opposite rules — the line is "does anything written
depend on it". `resolve_sync_endpoints` keeps the default strict store scope: sync *reads
the resolved store as its source*, so there the org half is load-bearing. Unchanged.

**3b — a corroborated `--live` is a note, not an error.** Today, when the files *agree*,
`--live` is refused outright — even when it names the very account they agree on. That
produces a ping-pong: plain apply refused (disagreement) → user re-runs with `--live` → the
app meanwhile rewrote its file → `--live` refused (agreement) → user strips the flag again.
The 2026-08-29 runbook had to document both directions.

The change — **for converge only** (`account_scope=True`). Two rounds of review each found
a defect in a broader version (round 2: principal-level corroboration let
`sync --live aaaa1111/dddd4444` silently read from `cccc3333`; round 3: even exact-store
corroboration leaves an email — which matches the live store's haystack while the account
owns sibling orgs — reading as a store-level ambiguity), and the honest conclusion is that
sync never needed the feature: under agreement sync's source is resolved by the files, the
flag selects nothing, and the measured ping-pong was converge's. So:

- **`account_scope=True` (converge):** in the no-disagreement branch, when
  `live_account(env)` resolves, the string matches the resolved account's principal per 3a's
  semantics, **and it matches no other discovered account's principal** — the uniqueness
  check runs over every account on the machine, because with no disagreement there is no
  two-candidate frame to bound it (without it, `--live a` would silently resolve to
  whichever account the files happen to agree on while also naming others) — return the
  resolved account with `resolved_from="corroborated"` instead of refusing. The uniqueness
  check is deliberately conservative: a string that incidentally substring-matches some
  other account's path text is refused with the listing, which is a safe annoyance, never a
  mis-selection.
- **`account_scope=False` (sync): unchanged.** Both of today's agreement-branch refusals
  stand verbatim. `--live` on a sync whose files agree remains an error, and the ping-pong
  there remains the documented two-step; extending relief to sync gets its own design if the
  pain is ever actually measured there.

The corroborating caller records no assertion — `live_asserted` stays `""` — because no
arbitration happened; the flag was redundant. In non-JSON output one line is printed
(`note: --live names the account the identity files already agree on; no arbitration was needed.`);
in `--json` the same sentence is appended to the manifest's `notes` array and stdout stays
pure JSON. A `--live` that names anything else while the files agree keeps today's refusal
verbatim — that case really is evidence of confusion, and refusing it is the feature.

## Change 4 — refusal texts tell the measured truth

The apply-time RULING 5 refusal currently leads with the two remedies least likely to work
and ends with the one designed for the situation — and one of them, "so the two agree", can
be satisfied by making the files agree on the *wrong* account. Rewritten:

```
~/.claude.json (aaaa1111) and config.json (bbbb2222) disagree about the signed-in
account (RULING 5). The designed remedy is to assert which account the desktop
app is on and re-plan:
   claude-code-sessions converge --live <email, or acct/org as reports print it> --apply
If it is the CLI's record that is stale, re-authenticating the CLI as the
desktop's account (run 'claude', then /login) also clears this. Switching the
desktop app may not - config.json has been measured both tracking a switch
(2026-08-02) and keeping the previous account across one (2026-08-29) - and
re-authenticating never writes it. --live exists for exactly that case.
Nothing was written.
```

The remedies are ordered by what can actually work. `--live` is first because it works in
every disagreement. Re-auth is stated with its real scope: `/login` rewrites only
`~/.claude.json`, so it clears the disagreement only when the CLI side is the stale one —
when `config.json` is the stuck side, re-authenticating as the *true* account leaves the
files still disagreeing, and the only re-auth that would make them "agree" is signing the
CLI into the *stale* account, which manufactures exactly the false agreement a liveness
guard must not invite. The old text's "so the two agree" promise is gone for that reason,
and the desktop-switch caveat says "may not" rather than "cannot" because the evidence runs
both ways — `config.json` tracked a real switch in the E4 measurements and sat stuck across
one on 2026-08-29 (round 3 caught the overstatement; see Review). The recheck keys on
agreement and cannot tell true agreement from false — the user can, and `--live` is how
they say it.

The same "switch the desktop app to it, so the two agree" clause inside `_guard_mutation`'s
disagreement note gets the same one-clause caveat. No other text changes.

## Compatibility and ordering

- Manifest additions are purely additive *for this tool's own consumers, which are the only
  consumers*: `_converge_recheck`, the printers, `undo` and `recover` all read manifests
  with `.get`, and a 0.12.0 binary handed a 0.13.0 manifest ignores the unknown key the same
  way (no strict schema exists anywhere in the codebase). Journaled 0.12.0 manifests (no
  `identity_disagreement`) replay, undo, and recover exactly as before.
- `resolved_from="corroborated"` joins the existing `"oauth"/"config"/"user"` vocabulary; the
  one consumer that branches on `"user"` (assertion recording) is the point of the change,
  and every other consumer treats the field as opaque provenance text.
- Version bump to 0.13.0. README: the `--live` paragraphs gain the pair form and the
  account-scope sentence; the converge section gains the warning example. Argparse `--live`
  help mentions the printed form.
- Release notes follow the standing discipline: fake cast only, `--anonymize` for any real
  output, the public-safety scan before push.

## Tests

In `tests/test_converge.py` unless noted; all fixtures use the fake cast. The identity-file
fixture writes `~/.claude.json` with `oauthAccount` naming account A and a `config.json`
naming account B beside the store root, matching `_identity_disagreement`'s read paths.

1. `test_dry_run_warns_when_identity_files_disagree` — dry run exits 0; output contains
   `disagree`, both remedy command lines, and `refusal writes nothing`.
2. `test_dry_run_quiet_when_files_agree` — no warning block.
3. `test_dry_run_quiet_when_live_asserted` — valid `--live`, no warning.
4. `test_json_manifest_carries_identity_disagreement` — key present with both ids under
   disagreement; absent otherwise; `--anonymize --json` output parses and keeps the key's
   structure.
5. `test_apply_still_refuses_fresh_disagreement` — regression: plan clean, files then set to
   disagree, apply refuses (the recheck evaluates fresh).
6. `test_printed_pair_form_resolves_live` — `--live "aaaa1111/cccc3333"` resolves under a
   disagreement.
7. `test_full_uuid_pair_form_resolves_live`.
8. `test_arbitrary_prefix_pair_resolves` — `--live "aaaa/cccc"` resolves when unambiguous.
9. `test_mid_uuid_fragment_pair_is_refused` — `--live "1111/cccc"` (left half not a prefix)
   is refused with the candidate listing, proving pair-shaped queries never fall back to
   substring matching.
10. `test_non_hex_slash_query_stays_substring` — a query like `Users/craig` (non-hex halves)
    is not treated as a pair; existing path-substring behavior is unchanged.
11. `test_pair_form_resolves_sync_to` (`tests/test_sync.py`) — `--to` accepts the printed
    form.
12. `test_pair_form_resolves_store` (`tests/test_retitle.py` or `test_rows.py`, wherever
    `--store` resolution is already exercised) — `_repoint_store` accepts a pair-shaped
    query.
13. `test_email_live_accepted_at_account_scope` — three org dirs under account A, one with
    rows; converge `--live alice@example.com` resolves; `live_asserted == A`; the returned
    Account carries empty org/path and the live label renders `aaaa1111/-`.
14. `test_account_scope_works_with_no_store_dirs` — the asserted account has no directory on
    disk; `--live` by uuid still resolves at account scope.
15. `test_email_live_still_store_strict_for_sync` (`tests/test_sync.py`) — same three-dir
    fixture, sync's `--live` still refuses with the N-stores listing (now carrying the
    pair-form hint).
16. `test_warning_then_clean_apply_when_disagreement_clears` — plan warns, files then set to
    agree, apply proceeds (the reverse transition of test 5).
17. `test_account_scope_refuses_across_accounts` — a string matching both principals refuses
    even with `account_scope=True`.
18. `test_corroborated_live_proceeds_with_note` — files agree; converge `--live` names the
    agreed account uniquely; run proceeds, note printed, manifest `live_asserted == ""`.
19. `test_corroborated_live_ambiguous_is_refused` — files agree on A; the string also
    matches account C's principal; refused.
20. `test_live_naming_other_account_while_agreeing_refused` — unchanged refusal text.
21. `test_apply_refusal_leads_with_live` — the RULING 5 refusal names `--live` before
    re-authentication, scopes the re-auth remedy to the CLI-stale case, and says the
    desktop switch "may not" refresh `config.json`.
22. `test_json_stdout_stays_pure` — `--json` runs of every changed path (warning present,
    corroborated note present) emit parseable JSON with no stray prose lines.
23. `test_remedy_lines_fall_back_when_emails_collide` — both principals share one email; the
    remedy lines print the two distinct 8-char account ids instead of two identical emails.
24. `test_sync_live_under_agreement_still_refused` (`tests/test_sync.py`) — files agree on
    `aaaa1111/cccc3333`; sync `--live` is refused with today's message for every form tried:
    the exact pair, a sibling-org pair (`aaaa1111/dddd4444`), and the bare email — proving
    3b's corroboration never reaches `account_scope=False`.

## Non-goals, stated so the review can disagree with them

- **`retitle` keeps writing under an identity disagreement.** Measured 2026-08-29: the three
  retitles succeeded minutes before converge's RULING 5 refusal, and that asymmetry did no
  harm — retitle's target set (every row of one named conversation) does not depend on which
  account is live, and its running-app guard (RULING 4) held. The panel pressed this
  (inconsistent guard posture across commands, in one sitting); it stays deferred because
  aligning it means changing what a shipped command *refuses*, which deserves its own
  decision with its own review rather than riding a UX release. Logged as a B5 candidate in
  the maintenance hub.
- **No dry-run enforcement.** The warning discloses; only the apply refuses. Argued at
  change 1.
- **`new-row`'s heuristic-plans-but-refuses-to-write model is untouched** (distinguished in
  change 3a).
- **Sync's direction semantics, RULING 4, RULING 8, exit codes: untouched.**
- The GUI, and B6's items 5–6 (containment-measured hold remedies; the internals write-up of
  the rewind-fork mechanism), remain on the roadmap, not here.

## Review

**Round 1, 2026-08-29 evening.** Panel: Codex (xhigh) and Gemini (gemini-3.1-pro-high via
agy) reported; the open-weight roster (DeepSeek, Kimi, repo-aware route) was unavailable —
both calls failed with one shared transport cause after reading the main module, so the
round ran on a halved panel. Gemini's completeness canary quoted one line beyond the
document's last (its documented line-wrap behavior); coverage otherwise verified.

**Applied:**

- **[Codex+Gemini] Change 2 rebuilt.** Both engines independently showed the haystack
  approach over-matches (`1111/cccc` matches inside a joined pair string) and under-matches
  (any pair whose `/` lands at a different index). Replaced with the parsed pair form —
  split on the single `/`, hex-guard both halves, anchored prefix-match each — which was
  both engines' named highest-impact fix. The first draft's "simplicity" defense of
  substring matching was wrong and is withdrawn.
- **[Codex] Scope framing corrected.** The intro claimed "no change to what any guard
  enforces; none weakens a refusal" while 3a/3b narrow two refusals. Now stated as: apply
  -time enforcement unchanged, two input-resolution refusals narrowed with their acceptance
  boundaries analyzed in place.
- **[Codex] Account-first resolution.** 3a now resolves `--live` against the two account
  principals rather than inferring an account from store matches — covering the
  no-store-dir edge Codex named, and stopping incidental path text from arbitrating.
- **[Gemini] 3b uniqueness.** Corroboration now requires the string to match *only* the
  agreed account across all discovered principals; without it `--live a` would silently
  swallow an ambiguity the flag exists to surface.
- **[Codex+Gemini] Display org demoted.** An account-level assertion renders as
  `aaaa1111/-`, never a guessed concrete pair a user might trust or re-paste.
- **[Codex] Remedy honesty.** "Re-authenticate so the files agree" can manufacture false
  agreement (align both files on the stale side); the text now says *as the account the
  desktop app is signed into*, and the warning wording is conditional ("while that
  disagreement stands") rather than categorical.
- **[Codex] Identity-state semantics stated.** Absent/unreadable/malformed files are no
  signal, unchanged from 0.12.0, with RULING 4 as the backstop — now said in "Where the
  pieces live" instead of assumed.
- **[Codex] 3b output contract.** JSON purity specified (note goes to the manifest's
  `notes`; stdout stays JSON); corroboration is structural (`resolved_from`).
- **[Codex] Tests extended** — the reverse transition (16), no-store-dir principal (14),
  mid-fragment negative (9), non-hex-pair negative (10), corroborated-ambiguous (19), JSON
  purity (22), email-collision remedies (23).
- **[Codex] Compat claim narrowed** to what was actually demonstrated: this tool's own
  `.get`-based consumers, which are the only consumers; anonymize preserves structure.

**Declined, with reasons:**

- **[Codex+Gemini] Non-zero or strict exit for a dry run carrying the warning.** A chained
  `converge && converge --apply` stops at the apply's own refusal — loud, non-zero, nothing
  written — so a second gate one step earlier adds no safety; the manifest field is the
  machine-readable signal both engines asked for. Deferred, not rejected forever: if a real
  automation consumer materializes that needs the dry run itself to gate, `--strict-identity`
  is the obvious shape.
- **[Gemini] Fold retitle's guard posture into this release.** Real question, wrong
  vehicle — it changes a shipped command's refusal surface and gets its own decision.
  Recorded as a non-goal and a B5 candidate rather than silently dropped.
- **[Codex] Snapshot timestamps in the warning.** The conditional wording covers the
  staleness honestly; a timestamp would imply precision the second-resolution op-id world
  doesn't have.
- **[Codex] Executing the printed remedy commands in tests.** The remedy's resolvability is
  covered structurally (tests 6–8, 13–14, 23 exercise the same resolver the remedy strings
  target); spawning the printed command lines verbatim would test the shell, not the
  contract.

**Round 2, same evening.** Panel: Codex and Gemini reported (roster still out, same shared
transport cause — see round 1); both canaries verified. The round did what the
defective-fix clause exists for: both engines independently found that a round-1 fix was
itself defective.

**Applied:**

- **[Codex+Gemini, blocker] 3b scoped by `account_scope`.** The round-1 corroboration
  matched the agreed account's *principal* in the shared resolver, so
  `sync --live aaaa1111/dddd4444` — naming a different org of the agreed account — would be
  accepted and sync would silently read from `cccc3333` instead. Both engines converged on
  the same fix, now specified: principal-level corroboration for converge only; exact-store
  corroboration for sync, with the different-org case keeping its refusal. Tests 24–25
  added.
- **[Gemini] Change 4's re-auth promise corrected.** `/login` rewrites only
  `~/.claude.json`, so "re-authenticate as the desktop's account so the files agree" is
  unachievable precisely in the measured scenario (stuck `config.json`) — the only re-auth
  that produces agreement there is signing into the *stale* account, the false agreement
  the doc itself warns against. The refusal text now leads with `--live` as the designed
  remedy and states re-auth's real scope (helps only when the CLI side is stale).
- **[Gemini] Email-collision fallback simplified** to the 8-char account ids — already
  distinct; the round-1 full-uuid fallback traded usability for nothing.
- **[Gemini] 3a's display-org selection ladder deleted as dead code** — once the label
  masks the org (`aaaa1111/-`) and nothing consumes it, choosing a representative was
  complexity with no consumer; the Account now carries empty org/path at account scope.

**Declined:** nothing in round 2.

**Round 3, same evening — the cap round.** Panel: Codex and Gemini reported (roster still
out); both canaries verified. Both engines again found the prior round's fix defective in
the same place, which resolves as follows.

**Applied:**

- **[Codex+Gemini, blocker] 3b narrowed to converge only.** Round 2's exact-store rule for
  sync still let an email — which matches the live store's haystack while the account owns
  sibling orgs — read as store-level corroboration, and the two engines sketched
  field-aware matching to fix it. The simpler truth: sync never needed 3b. Under agreement
  sync's source is resolved by the identity files and `--live` selects nothing, the
  measured ping-pong was converge's, and deleting the sync branch removes the entire
  ambiguity class instead of refining it. Tests 24–25 collapsed into test 24 (sync `--live`
  under agreement stays refused in every form).
- **[Codex] "cannot fix a stale config.json" corrected to "may not".** The evidence runs
  both ways — E4 (2026-08-02) measured `config.json` *tracking* a real switch; 2026-08-29
  measured it stuck — so the refusal text now states the uncertainty instead of a false
  certainty that would steer users past a switch that might work.

**Declined, with reasons:**

- **[Gemini] Advising deletion of a stuck `config.json` in the refusal text.** Mechanically
  it would clear the disagreement (an absent file is no signal), but `config.json` is the
  desktop app's own configuration file, holding more than `lastKnownAccountUuid` — a CLI
  telling users to delete another application's config trades a typed `--live` flag for
  unbounded app-state risk. The per-run `--live` "tax" is one flag on an operation run a
  few times a week, and it is the sanctioned, auditable override.
- **[Gemini] Loosening the corroboration uniqueness check (substring over-match on
  unrelated principals' path text).** Kept as-is deliberately: its failure mode is a refusal
  with the candidate listing — a safe annoyance — never a mis-selection, and conservative
  refusals are this module's house style. Now stated in 3b's text.

**Loop closed at the 3-round cap.** Round 3's blocker is resolved by deletion (the sync
branch no longer exists to be defective), and that resolution is recorded here rather than
re-reviewed — the cap ends the loop, so the implementing session should treat 3b's
converge-only scoping as review-driven but not itself panel-verified.
