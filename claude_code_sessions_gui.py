"""Windowed front end for the maintenance routine - no terminal, nothing to
remember.

    ccs-gui                     open the window
    ccs-gui --install-shortcut  put "Claude sessions" on the Desktop + Start Menu
    ccs-gui --remove-shortcut   take them away again

Three tabs (docs/specs/2026-08-31-gui-level-design.md):
  Level           the home - the alignment scoreboard, the converge plan, and
                  the title-collision holds as prefilled rename rows; Apply
                  runs the renames then a fresh converge, two confirmations,
                  each describing exactly what runs next.
  Copy & refresh  the original sync pane, moved intact - still the only tab
                  that can OVERWRITE a row (converge is additive and
                  deliberately never refreshes).
  Health          the doctor report plus interrupted-operation detection;
                  while an unresolved operation exists, Apply and Undo are
                  disabled on every tab and `recover` runs in the terminal.

Installed as a GUI script (pyproject's [project.gui-scripts]), so the launcher
runs under pythonw and no console window ever appears - the console-script
equivalent would flash one on every double-click.

Deliberately a THIN SHELL over the library, not a reimplementation: it calls
the same gather_*/plan_*/run_* functions the CLI does, so every refusal,
guard, and safety property (RULING 4's running-app guard, RULING 5's --live
certification, RULING 6's helper exclusion, tombstone skipping,
dry-run-then-apply) behaves identically here. It adds no path of its own into
the store.

Two rules it holds to:
  - Nothing is written until you press Apply. Opening the window plans only.
  - A refusal is shown verbatim, never summarised into something friendlier.
    The refusals in this tool carry the reason and the fix, and softening them
    would be the one place a GUI could do real harm.
"""

import argparse
import os
import subprocess
import sys
import threading
import time
import traceback

import claude_code_sessions as ccs

try:
    import tkinter as tk
    from tkinter import ttk, messagebox
    TK_ERROR = None
except ImportError as _exc:            # tkinter is stdlib but packaged separately
    tk = ttk = messagebox = None       # on some Linux distros (apt install python3-tk)
    TK_ERROR = str(_exc)

PAD = 10

# Remembering the destination is the difference between answering the
# "which store?" question once and answering it on every single run - this
# machine has two stores for one account, so the picker fires every time
# otherwise. Kept OUT of ~/.claude-code-journal/ on purpose: that directory is
# the tool's operation journal, documented as such, and a GUI preference is not
# part of any operation's record.
PREF_PATH = os.path.join(os.path.expanduser("~"), ".claude-code-sessions-gui.json")


def load_pref():
    try:
        import json
        with open(PREF_PATH, encoding="utf-8") as fh:
            return (json.load(fh) or {}).get("to", "") or ""
    except (OSError, ValueError):
        return ""


def save_pref(value):
    try:
        import json
        with open(PREF_PATH, "w", encoding="utf-8") as fh:
            json.dump({"to": value}, fh)
    except OSError:
        pass                    # a preference that cannot be saved is not an error


def short(path, home):
    return path.replace(home, "~") if home and path.startswith(home) else path


# ------------------------------------------------------------ Level tab models
#
# Pure functions, importable without tkinter (the tkinter import above is
# guarded), so tests/test_gui_models.py and the tools/check_gui_*.py harness
# can exercise them headlessly. The row model is the contract the design doc
# states (docs/specs/2026-08-31-gui-level-design.md, "The holds, as
# structured rows"): key, title, evidence, target_sid, prefill, editable,
# ticked, degrade_reason, classification - plus `entry`, the working text the
# widgets read and write, initialised from prefill.

def _hold_models(manifest):
    """One row dict per distinct hold identity in a converge manifest.

    `key` is the stable identity - (measured.superseded or the hold's own
    session id, title_key of the colliding title) - which is what edit-merging
    across replans keys on. Two holds sharing a key are ONE decision: retitle's
    default scope is every account, so a single rename clears them all, and
    rendering them as two ticked rows would make the default state refuse
    itself on the duplicate-title check. The engine agrees - its
    measure_suggested census treats the same (sid, title) repeating across a
    group's holds as one suggestion, not a collision.

    `target_sid` is measured.superseded - THE ENGINE'S VERDICT, never
    re-derived from the evidence text (the hold's own session id can be either
    leg of the pair). `command_runnable` is deliberately not consulted: it
    speaks about the rendered shell command only, and this window applies
    titles through plan_retitle with no shell involved - a `$` in a title
    degrades the pasteable command while remaining a valid title.

    Any hold reason this window does not recognise still gets a read-only row
    carrying the hold's own reason/detail verbatim: the pane must never count
    holds it cannot show.
    """
    models, by_key = [], {}
    for h in (manifest.get("holds") or []):
        mm = h.get("measured")
        title = h.get("title") or ""
        tkey = ccs.title_key(title)
        if h.get("reason") == "held_title_collision" and isinstance(mm, dict):
            cls = mm.get("classification") or "unmeasured"
            sup = mm.get("superseded")
            key = (sup or h.get("session") or "", tkey)
            if cls == "supersession":
                suggestion = mm.get("suggested_title") or ""
                model = {"key": key, "title": title,
                         "evidence": "measured: " + (h.get("measured_line")
                                                     or ""),
                         "target_sid": sup, "prefill": suggestion,
                         "entry": suggestion, "editable": True,
                         "ticked": bool(suggestion),
                         "degrade_reason": mm.get("degrade_reason") or "",
                         "classification": "supersession"}
            else:
                line = h.get("measured_line") or mm.get("reason") or ""
                tag = "not measured: " if cls == "unmeasured" else "measured: "
                model = {"key": key, "title": title,
                         "evidence": tag + line,
                         "target_sid": None, "prefill": "", "entry": "",
                         "editable": False, "ticked": False,
                         "degrade_reason": mm.get("degrade_reason") or "",
                         "classification": cls}
        else:
            key = (h.get("session") or "", tkey)
            model = {"key": key, "title": title,
                     "evidence": "{0} - {1}".format(h.get("reason") or "?",
                                                    h.get("detail") or ""),
                     "target_sid": None, "prefill": "", "entry": "",
                     "editable": False, "ticked": False, "degrade_reason": "",
                     "classification": h.get("reason") or "?"}
        seen = by_key.get(model["key"])
        if seen is not None:
            seen["_held_count"] = seen.get("_held_count", 1) + 1
            continue
        model["_held_count"] = 1
        by_key[model["key"]] = model
        models.append(model)
    for m in models:
        n = m.pop("_held_count", 1)
        if n > 1:
            m["evidence"] += " - held in {0} accounts".format(n)
    return models


def _merge_hold_models(fresh, previous):
    """Rebuild rows without silently resetting the user's input.

    Merge by stable key: a row still present keeps the previous entry text and
    tick; rows that vanished drop (their state goes with them); new rows
    arrive with their defaults. Only editable rows carry user state, and a row
    whose classification changed to read-only takes its fresh defaults - the
    old text belonged to a decision that no longer exists.
    """
    prev = {m["key"]: m for m in previous}
    for m in fresh:
        old = prev.get(m["key"])
        if old is not None and m.get("editable") and old.get("editable"):
            m["entry"] = old.get("entry", m["entry"])
            m["ticked"] = bool(old.get("ticked"))
    return fresh


def _plan_summary_lines(m):
    """The converge plan summary: the manifest's completeness line and
    per-destination row counts - the same facts `_print_converge_report`
    prints, condensed for a pane that renders holds as widgets below."""
    lines = []
    c = m.get("complete") or {}
    lines.append("complete{0} : {1} of {2}  ->  {3} of {2}   ({4} held)".format(
        " (scoped)" if c.get("scoped") else "", c.get("now"), c.get("of"),
        c.get("after"), c.get("held")))
    rows = m.get("rows") or []
    for d in m.get("destinations") or []:
        mine = [r for r in rows if r.get("account") == d.get("account")]
        if mine:
            lines.append("-> {0}: {1} row{2} to create".format(
                d.get("label", ""), len(mine), "" if len(mine) == 1 else "s"))
    for nd in m.get("non_destinations") or []:
        lines.append("   {0}: NOT a destination - every org directory under "
                     "it is empty, so there is no evidence which one is real"
                     .format(nd.get("label", "")))
    if m.get("dead_excluded"):
        lines.append("({0} dead conversation(s) excluded from the count - "
                     "rows exist but the transcript is gone; 'doctor' "
                     "reports them)".format(m["dead_excluded"]))
    return lines


def _scoreboard_lines(rep):
    """The alignment scoreboard, rendered from the report's own fields, never
    re-derived. Same facts the CLI's `alignment` prints, minus its
    [observed]/[hypothesis] prefixes - the window has no piped-output
    convention to honour."""
    if (rep.get("stores") or {}).get("status") != "found":
        return ["store: {0} ({1})".format(rep["stores"].get("status"),
                                          rep["stores"].get("detail"))]
    lines = ["{0} account(s):".format(len(rep["accounts"]))]
    for a in rep["accounts"]:
        lines.append("   {0:<28} {1:>4} rows".format(a["label"], a["rows"]))
    for e in rep.get("row_errors") or []:
        lines.append("UNREADABLE ROW (mutations blocked): " + e)
    r = rep["reachable"]
    lines.append("reachable       {0} of {1} transcript(s) open from a "
                 "sidebar; {2} orphaned".format(r["reachable"],
                                                r["transcripts"],
                                                r["orphans"]))
    d = rep["distinguishable"]
    lines.append("distinguishable {0} title(s) duplicated inside a single "
                 "sidebar".format(d["duplicate_titles"]))
    c = rep["consistent"]
    lines.append("consistent      {0} row file(s) open a different "
                 "conversation depending on the account"
                 .format(c["disagreeing_rows"]))
    if c["disagreeing_rows"]:
        lines.append("                {0} of those leave a conversation short "
                     "of at least one sidebar".format(c["leaving_a_gap"]))
    m = rep["complete"]
    lines.append("complete        {0} of {1} conversation(s) reachable from "
                 "all {2} account(s); {3} short"
                 .format(m["in_all_accounts"], m["conversations"],
                         len(rep["accounts"]), m["short"]))
    s = rep["safe"]
    lines.append("safe            {0} dead, {1} blank, {2} unreadable row(s)"
                 .format(s["dead_rows"], s["blank_rows"],
                         s["unreadable_rows"]))
    return lines


def _scoreboard_half(rep):
    """The completion status line's store half - per-account row counts plus
    the completeness gap, e.g. 'Level: 379 / 379 / 379 - 0 short.'"""
    counts = " / ".join(str(a["rows"]) for a in rep.get("accounts") or [])
    return "Level: {0} - {1} short.".format(
        counts or "?", (rep.get("complete") or {}).get("short", "?"))


def _level_state(rep, manifest, models):
    """The pane's headline and Apply enablement, per the defined predicate.

    'Nothing to do - the sidebars are level.' requires ALL THREE: the
    alignment report's `complete` short-count is 0, the plan has no rows, and
    no holds exist. Rows-empty-but-holds-present keeps Apply enabled (it has
    renames to run) - unless every hold is read-only, because whenever there
    is truly nothing to run, Apply is disabled. Rows-empty-holds-empty-but-
    alignment-short is the one state this tab can do nothing about (a
    conversation with no transcript for converge to spread), so it points at
    Health and disables Apply.
    """
    rows = manifest.get("rows") or []
    short_n = (rep.get("complete") or {}).get("short", 0)
    tickable = any(m.get("editable") for m in models)
    if rows:
        n_acct = len({r.get("account") for r in rows})
        status = "{0} row{1} to create across {2} account{3}".format(
            len(rows), "" if len(rows) == 1 else "s",
            n_acct, "" if n_acct == 1 else "s")
        if models:
            status += " - {0} naming decision{1} below".format(
                len(models), "" if len(models) == 1 else "s")
        return {"kind": "rows", "status": status,
                "detail": "Nothing is written until you press Apply.",
                "apply": True}
    if models:
        return {"kind": "naming",
                "status": "Nothing to copy - {0} naming decision{1} below."
                          .format(len(models),
                                  "" if len(models) == 1 else "s"),
                "detail": ("Each ticked rename applies in every account "
                           "holding that conversation; nothing is written "
                           "until you press Apply."),
                "apply": tickable}
    if short_n:
        return {"kind": "short",
                "status": "{0} conversation{1} short of a sidebar, but "
                          "nothing to copy or rename - see Health."
                          .format(short_n, "" if short_n == 1 else "s"),
                "detail": ("Usually a conversation whose transcript is gone, "
                           "so converge has nothing to spread. This tab "
                           "cannot fix that state."),
                "apply": False}
    return {"kind": "level",
            "status": "Nothing to do - the sidebars are level.",
            "detail": "", "apply": False}


def _level_steps_stage1(models):
    """(steps, problems) for stage 1 - the ticked renames, in row order.

    Each step aims plan_retitle at the model's target_sid with the entry's
    trimmed text. Unticked and read-only rows contribute nothing. Two local
    refusals run before any engine call - the cheap set-check: a ticked row
    with an empty entry ('a rename needs a name'), and two ticked rows whose
    trimmed titles collide ('two renames share a name'). Any problem means NO
    steps: a partial list would rename some rows under a plan the user was
    never shown.
    """
    steps, problems, seen = [], [], {}
    for m in models:
        if not (m.get("editable") and m.get("ticked")):
            continue
        text = (m.get("entry") or "").strip()
        if not text:
            problems.append("a rename needs a name: the row for {0!r} is "
                            "ticked with an empty title".format(
                                m.get("title") or "?"))
            continue
        if text in seen:
            problems.append("two renames share a name: {0!r} is the new "
                            "title for both {1!r} and {2!r}".format(
                                text, seen[text], m.get("title") or "?"))
            continue
        seen[text] = m.get("title") or "?"
        steps.append({"key": m["key"], "target_sid": m["target_sid"],
                      "old_title": m.get("title") or "",
                      "new_title": text})
    if problems:
        return [], problems
    return steps, []


def _ordinal(n):
    words = ("", "first", "second", "third", "fourth", "fifth", "sixth",
             "seventh", "eighth", "ninth", "tenth")
    return words[n] if 0 < n < len(words) else "{0}th".format(n)


def _stage1_dialog_parts(steps):
    """(headline, mapping lines, footer) for the stage-1 confirmation.

    Every ticked mapping is listed in full, one line per row - the
    prefilled-and-ticked default is an opt-out, and this dialog, showing
    every old->new pair, is the look the opt-out gets. The footer states
    ONCE that each rename applies in every account holding that conversation
    (retitle's default scope, said so the bulk list is not blind trust in
    routing) and that each is individually undoable.
    """
    head = "Rename {0} conversation{1}?".format(
        len(steps), "" if len(steps) == 1 else "s")
    mappings = ["{0!r}  ->  {1!r}".format(s["old_title"], s["new_title"])
                for s in steps]
    footer = ("Each rename applies in every account holding that "
              "conversation (retitle's default scope), and each is its own "
              "journalled operation - individually undoable.")
    return head, mappings, footer


def _stage2_question(fresh):
    """The stage-2 confirmation, describing the FRESH plan's numbers only."""
    rows = fresh.get("rows") or []
    holds = fresh.get("holds") or []
    n_acct = len({r.get("account") for r in rows})
    q = "Create {0} row{1} across the {2} account{3} named?".format(
        len(rows), "" if len(rows) == 1 else "s",
        n_acct, "" if n_acct == 1 else "s")
    if holds:
        q += "  ({0} held - they stay held.)".format(len(holds))
    return (q + "\n\nEach row adds the conversation to that account's "
                "sidebar; converge never overwrites or deletes. Undo "
                "removes the created rows again.")


# ------------------------------------------------------- the Apply sequence
#
# The two-stage Apply, as one linear function driven through a UI adapter so
# the harness can run it headlessly against a real store. The adapter's
# contract:
#
#   status(text)          - posted BEFORE every engine call (a twenty-rename
#                           Apply with a frozen status line reads as a hang)
#   gate()                - the press-time unresolved-op re-scan; None when
#                           clear, else what blocked (a list of op ids, or a
#                           sentence when the journal could not be read)
#   confirm_stage2(fresh) - show the fresh plan's numbers, return bool
#   truncate_requested()  - True once the user confirmed closing the window;
#                           checked at operation boundaries only, so the
#                           in-flight operation always completes
#   remaining             - attribute this function keeps current: how many
#                           steps a truncation right now would drop
#
# The function NEVER raises - every outcome travels in the result dict, so
# the worker's finally-refresh cannot be skipped by an exception path.

def _run_level_sequence(env, steps, live, ui):
    """Stage 1 (each ticked rename, its own journalled op) then stage 2 (a
    FRESH plan_converge, confirmed as itself, then run_converge on that fresh
    manifest only). Returns a dict:

      planned   how many renames were asked for
      landed    how many completed (each individually undoable)
      rename_refusal  (1-based index, verbatim text) when one stopped the
                      sequence - remaining renames and stage 2 do not run
      rename_error    True when the stopper was a bug, not a Refusal
      stage2    'gate' | 'truncated' | 'plan_failed' | 'empty' | 'cancelled'
                | 'refused' | 'error' | 'completed' | 'unchanged'
      plan_problem / converge_problem   ('refusal'|'error', text)
      fresh     the stage-2 plan manifest, when one was made
      gate      what the press-time re-scan found, when it aborted the press
      mutated   True once anything was written
    """
    seq = {"planned": len(steps), "landed": 0, "rename_refusal": None,
           "rename_error": False, "stage2": "not_reached",
           "plan_problem": None, "converge_problem": None, "fresh": None,
           "gate": None, "mutated": False}
    ui.remaining = len(steps) + 1
    blocked = ui.gate()
    if blocked:
        seq["gate"] = blocked
        seq["stage2"] = "gate"
        return seq
    for i, st in enumerate(steps):
        if ui.truncate_requested():
            seq["stage2"] = "truncated"
            return seq
        ui.remaining = (len(steps) - i - 1) + 1
        ui.status("Applying rename {0} of {1}...".format(i + 1, len(steps)))
        try:
            pm = ccs.plan_retitle(env, ccs.RetitleFlags(
                only=st["target_sid"], title=st["new_title"]))
            ccs.run_retitle(env, pm)
        except ccs.Refusal as exc:
            seq["rename_refusal"] = (i + 1, str(exc))
            return seq
        except Exception:
            seq["rename_refusal"] = (i + 1, traceback.format_exc())
            seq["rename_error"] = True
            return seq
        seq["landed"] += 1
        seq["mutated"] = True
    if ui.truncate_requested():
        seq["stage2"] = "truncated"
        return seq
    ui.remaining = 1
    ui.status("Planning the copy...")
    try:
        fresh = ccs.plan_converge(env, ccs.ConvergeFlags(live=live))
    except ccs.Refusal as exc:
        seq["stage2"] = "plan_failed"
        seq["plan_problem"] = ("refusal", str(exc))
        return seq
    except Exception:
        seq["stage2"] = "plan_failed"
        seq["plan_problem"] = ("error", traceback.format_exc())
        return seq
    seq["fresh"] = fresh
    if not fresh.get("rows"):
        # A user must never be asked to confirm creating zero rows: no
        # dialog appears and no converge runs.
        seq["stage2"] = "empty"
        return seq
    blocked = ui.gate()
    if blocked:
        seq["gate"] = blocked
        seq["stage2"] = "gate"
        return seq
    if ui.truncate_requested():
        seq["stage2"] = "truncated"
        return seq
    if not ui.confirm_stage2(fresh):
        seq["stage2"] = "cancelled"
        return seq
    ui.status("Creating rows...")
    try:
        final = ccs.run_converge(env, fresh)
    except ccs.Refusal as exc:
        seq["stage2"] = "refused"
        seq["converge_problem"] = ("refusal", str(exc))
        return seq
    except Exception:
        seq["stage2"] = "error"
        seq["converge_problem"] = ("error", traceback.format_exc())
        return seq
    ui.remaining = 0
    seq["stage2"] = final                      # "completed" or "unchanged"
    if final == "completed":
        seq["mutated"] = True
    return seq


def _sequence_status(seq, half=""):
    """One status line for a finished sequence, conditional on what actually
    ran: a skipped stage 1 must never produce 'renames landed' - the
    templates take the landed count, and zero renders as no clause at all.
    The completed line concatenates the journal half and the store half
    (HALF, from _scoreboard_half), because the ops-created part is what makes
    the Undo walk-back legible and the scoreboard part is what the tab
    exists to report."""
    landed = seq.get("landed") or 0
    plural = "" if landed == 1 else "s"
    landed_clause = "{0} rename{1} landed, each undoable".format(landed,
                                                                 plural)
    if seq.get("rename_refusal"):
        idx = seq["rename_refusal"][0]
        verb = "failed" if seq.get("rename_error") else "was refused"
        if landed:
            return "{0}; the {1} {2}.".format(landed_clause, _ordinal(idx),
                                              verb)
        return "The rename {0} - nothing was written.".format(verb)
    s2 = seq.get("stage2")
    if s2 == "plan_failed":
        tail = "the copy could not be planned."
        return (landed_clause + "; " + tail) if landed else tail.capitalize()
    if s2 == "cancelled":
        if landed:
            return landed_clause + "; the copy was not confirmed."
        return "Nothing was applied - the copy was not confirmed."
    if s2 in ("refused", "error"):
        tail = ("nothing was copied - the copy {0}."
                .format("was refused" if s2 == "refused" else "failed"))
        return (landed_clause + "; " + tail) if landed else tail.capitalize()
    if s2 == "empty":
        if landed:
            held = len((seq.get("fresh") or {}).get("holds") or [])
            line = "Applied ({0} rename{1}; nothing to copy)".format(landed,
                                                                     plural)
            if held:
                line += " - {0} held".format(held)
            return line + " - the rows below are current."
        return "Nothing to do."
    if s2 == "unchanged":
        tail = "the copy's re-check left nothing to write."
        return (landed_clause + "; " + tail) if landed else tail.capitalize()
    if s2 == "completed":
        ops = ("{0} rename{1} + 1 converge".format(landed, plural)
               if landed else "1 converge")
        line = "Applied ({0})".format(ops)
        return (line + " - " + half) if half else line
    return ""                                   # gate / truncated: no line


def _run_level_apply(env, steps, live, ui):
    """The whole Apply press, worker side: the sequence, then the
    post-mutation refresh. Returns (seq, refresh).

    The refresh is UNCONDITIONAL for every sequence that got past its gate -
    a finally, not a success step: whether renames landed and stage 2 was
    skipped, refused, or completed, the pane's next render comes from
    gather_alignment plus a fresh plan_converge, never from a manifest that
    predates the writes (which also reconciles a partial converge honestly).
    Two exceptions, both stated in the design: a gate abort mutated nothing
    and must not touch the pane the red line annotates, and a truncated
    sequence is a window on its way closed.

    The refresh plans with live="" because the sequence has ENDED here -
    completed, refused, or cancelled alike - and an assertion covers one
    attempt. If the identity files still disagree, this very replan is what
    re-raises the banner and asks again.

    REFRESH is ("ok", alignment_report, manifest, running) or
    ("refusal"|"error", text), or None when skipped.
    """
    seq = _run_level_sequence(env, steps, live, ui)
    if seq["stage2"] in ("gate", "truncated"):
        return seq, None
    ui.status("Re-measuring...")
    try:
        rep = ccs.gather_alignment(env)
        man = ccs.plan_converge(env, ccs.ConvergeFlags(live=""))
        running = ccs.claude_running(env)
        refresh = ("ok", rep, man, running)
    except ccs.Refusal as exc:
        refresh = ("refusal", str(exc))
    except Exception:
        refresh = ("error", traceback.format_exc())
    return seq, refresh


# --------------------------------------------- interrupted-operation detection

# The hand-off, not an executor: `recover` is a directional judgment whose
# CLI prose walks the user through the evidence, so this window copies the
# command instead of duplicating that surface.
RECOVER_COMMAND = "claude-code-sessions recover"


def _scan_interrupted(env):
    """[(manifest, classification note)] per unresolved journal op - the
    same selection `cmd_recover` makes (ccs.nonterminal_ops), in the same
    journal order it lists them. Raises when the journal cannot be read:
    'couldn't look' is never 'nothing there', and the caller gates mutations
    on the failure rather than treating it as a clean scan."""
    entries = []
    for op in ccs.nonterminal_ops(env):
        try:
            note = ccs.classify_op(env, op).get("note") or ""
        except Exception:
            note = ""                    # classify_op never raises, per its
        entries.append((op.manifest, note))  # contract - belt and braces
    return entries


def _age_text(seconds):
    s = max(0, int(seconds))
    if s < 120:
        return "{0}s ago".format(s)
    if s < 7200:
        return "{0} min ago".format(s // 60)
    if s < 172800:
        return "{0} h ago".format(s // 3600)
    return "{0} days ago".format(s // 86400)


def _interrupted_lines(entries, now_s):
    """The Health listing: id, type, age and what each op was doing, the
    first one marked (a bare `recover` lists them in this same order, so it
    is the one that listing leads with), the copyable command, and the
    rationale for keeping execution in the CLI."""
    lines = ["!! {0} interrupted operation(s) need attention"
             .format(len(entries)), ""]
    for i, (m, note) in enumerate(entries):
        at = (m.get("history") or [{}])[0].get("at") or now_s
        lines.append("{0}  {1:<10} {2:<12} {3}{4}".format(
            m.get("op_id", "?"), m.get("op_type", "move"),
            _age_text(now_s - at), m.get("status", "?"),
            "   <- listed first by a bare 'recover'" if i == 0 else ""))
        if note:
            lines.append("      " + note)
    lines += [
        "",
        "Resolve in a terminal - the Copy button has the command:",
        "   " + RECOVER_COMMAND,
        "",
        "Execution stays in the CLI deliberately: recover is a directional",
        "judgment (--back removes what landed, --forward re-evaluates the",
        "remainder), and its report walks you through the evidence this",
        "window would have to duplicate to be safe. A hard kill",
        "mid-operation lands here too - this listing is the net.",
        "Press Refresh here once recover finishes: a scan that finds",
        "nothing unresolved clears the banner and lifts the Apply/Undo",
        "gate on every tab.",
    ]
    return lines


class _MutationMarker(object):
    """The close handler's view of a single-operation worker (sync apply,
    undo): nothing remains after the in-flight op, and truncation is simply
    'close once it lands'."""
    def __init__(self):
        self.remaining = 0
        self.truncate = False


class _TkLevelUI(object):
    """The Apply sequence's bridge back onto the Tk thread.

    status() posts and never waits; confirm_stage2() marshals the dialog to
    the UI thread and blocks the worker on the answer - or calls it directly
    when already on that thread, which is how the inline-thread harness
    drives the same code path without deadlocking on itself. gate() is the
    press-time unresolved-op re-scan. `truncate` is set by the close handler
    (UI thread) and read by the sequence at operation boundaries only, so
    the in-flight operation always completes - a plain attribute is enough
    under the GIL for a set-once flag.
    """

    def __init__(self, app):
        self.app = app
        self.remaining = 0
        self.truncate = False

    def status(self, text):
        self.app.root.after(0, self.app.level_status.set, text)

    def gate(self):
        try:
            ops = ccs.nonterminal_ops(self.app.env)
        except Exception:
            return "the journal could not be read"
        return [o.manifest.get("op_id") for o in ops] or None

    def truncate_requested(self):
        return self.truncate

    def confirm_stage2(self, fresh):
        return bool(self._on_ui(lambda: messagebox.askokcancel(
            "Create the rows?", _stage2_question(fresh))))

    def _on_ui(self, fn):
        if threading.current_thread() is threading.main_thread():
            return fn()
        evt = threading.Event()
        box = []

        def run():
            try:
                box.append(fn())
            finally:
                evt.set()
        self.app.root.after(0, run)
        evt.wait()
        return box[0] if box else None


class SyncApp:
    def __init__(self, root):
        self.root = root
        self.env = ccs.default_env()
        self.manifest = None
        self.dest_choice = load_pref()
        # Never persisted, unlike dest_choice: the destination is a stable fact
        # about this machine, while "which account is signed in" changes every
        # time you switch. A remembered answer would be a stale assertion.
        self.live_choice = ""
        # Bumped on every plan; a callback whose generation is stale is dropped
        # rather than allowed to install a superseded manifest.
        self.generation = 0
        # The Level tab's own state, deliberately separate from the sync
        # pane's: level_live is the same RULING 5 fact the sync pane can hold
        # in live_choice, but its lifetime differs (it clears when the Apply
        # SEQUENCE ends, not when a sync lands), and sharing one variable
        # would let a Level apply silently consume an assertion the user gave
        # the sync pane - a behavior change the moved pane's contract forbids.
        # In-memory only, never written to disk; shown while in force.
        self.level_live = ""
        self.level_gen = 0
        self.level_manifest = None
        self.level_rep = None
        self.hold_models = []
        self._level_apply_ok = False
        self._sync_apply_ok = False
        self._level_note = ""
        # The mutation gate (unresolved journal ops) and the worker plumbing.
        self.gate_text = ""
        self._busy_count = 0
        self._banner_widgets = []        # rebuilt with the identity banner
        self._hold_widgets = []          # rebuilt with the hold rows
        self._mutation_ui = None
        self._close_after_worker = False
        root.title("Claude sessions")
        root.geometry("940x640")
        root.minsize(760, 520)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        outer = ttk.Frame(root, padding=PAD)
        outer.pack(fill="both", expand=True)
        self.nb = ttk.Notebook(outer)
        self.nb.pack(fill="both", expand=True)
        self.level_tab = ttk.Frame(self.nb, padding=PAD)
        self.sync_tab = ttk.Frame(self.nb, padding=PAD)
        self.health_tab = ttk.Frame(self.nb, padding=PAD)
        self.nb.add(self.level_tab, text="Level")
        self.nb.add(self.sync_tab, text="Copy & refresh")
        self.nb.add(self.health_tab, text="Health")

        # One window-level bar outside the notebook: Close, and the SHARED
        # Undo button - there is one journal, so there is one "last
        # operation", and a per-tab button would let two tabs disagree about
        # which op that is. It replaces the sync pane's own undo button.
        winbar = ttk.Frame(outer)
        winbar.pack(fill="x", pady=(PAD, 0))
        ttk.Button(winbar, text="Close", command=self._on_close).pack(
            side="left")
        self.undo_btn = ttk.Button(winbar, text="Undo", command=self.on_undo)
        self.undo_target = None          # the _find_undoable_op descriptor

        # ------------------------------------------------- Tab 1: Level
        lt = self.level_tab
        self.level_status = tk.StringVar(value="Measuring...")
        lb_status = ttk.Label(lt, textvariable=self.level_status,
                              font=("Segoe UI", 11, "bold"), wraplength=880,
                              justify="left")
        lb_status.pack(anchor="w")
        self.level_detail = tk.StringVar(value="")
        ttk.Label(lt, textvariable=self.level_detail, wraplength=880,
                  justify="left", foreground="#555").pack(anchor="w",
                                                         pady=(4, 2))
        # The passive environment notice - weather, not a gate: the guard
        # stays in the engine at mutation time; this line exists so nobody
        # types five titles first and learns about RULING 4 second.
        self.level_notice = ttk.Label(
            lt, foreground="#a05000", wraplength=880, justify="left",
            text="!! The Claude desktop app is running - Apply will refuse "
                 "until it is closed.")
        body = ttk.Frame(lt)
        body.pack(fill="x", pady=(4, 4))
        self.level_body = body           # the notice packs before this
        # The identity banner sits between the plan summary and the holds -
        # the pane's stated top-to-bottom order.
        self.level_banner = ttk.Frame(lt)
        self.level_banner.pack(fill="x")
        # 8 lines, scrolling: taller and a high-DPI screen leaves the hold
        # rows below no room at all - measured at 150% scaling, an 11-line
        # area consumed the whole tab.
        self.level_text = tk.Text(body, wrap="none", height=8,
                                  font=("Consolas", 9), state="disabled",
                                  borderwidth=1, relief="solid")
        lsb = ttk.Scrollbar(body, orient="vertical",
                            command=self.level_text.yview)
        self.level_text.configure(yscrollcommand=lsb.set)
        self.level_text.pack(side="left", fill="both", expand=True)
        lsb.pack(side="right", fill="y")
        # The button bar packs FIRST, pinned to the bottom edge, so a short
        # window squeezes the hold area (which scrolls) and never the Apply
        # button; the hold rows then take whatever is left.
        lbar = ttk.Frame(lt)
        lbar.pack(side="bottom", fill="x", pady=(6, 0))
        # The hold rows scroll when they overflow - a canvas-hosted frame,
        # the stock tkinter idiom for a scrollable widget stack.
        holds_wrap = ttk.Frame(lt)
        holds_wrap.pack(fill="both", expand=True)
        self.hold_canvas = tk.Canvas(holds_wrap, highlightthickness=0)
        hsb = ttk.Scrollbar(holds_wrap, orient="vertical",
                            command=self.hold_canvas.yview)
        self.hold_canvas.configure(yscrollcommand=hsb.set)
        self.hold_frame = ttk.Frame(self.hold_canvas)
        self._hold_window = self.hold_canvas.create_window(
            (0, 0), window=self.hold_frame, anchor="nw")
        self.hold_frame.bind(
            "<Configure>",
            lambda _e: self.hold_canvas.configure(
                scrollregion=self.hold_canvas.bbox("all")))
        self.hold_canvas.bind(
            "<Configure>",
            lambda e: self.hold_canvas.itemconfigure(self._hold_window,
                                                     width=e.width))
        self.hold_canvas.pack(side="left", fill="both", expand=True)
        hsb.pack(side="right", fill="y")
        self.level_apply_btn = ttk.Button(lbar, text="Apply",
                                          command=self.on_level_apply,
                                          state="disabled")
        self.level_apply_btn.pack(side="right")
        self.level_refresh_btn = ttk.Button(lbar, text="Refresh",
                                            command=self.refresh_level)
        self.level_refresh_btn.pack(side="right", padx=(0, 6))
        # The footer says when the snapshot was taken; converge's own
        # apply-time re-checks are the guard against drift between the two
        # reads, not this label.
        self.level_footer = tk.StringVar(value="")
        ttk.Label(lbar, textvariable=self.level_footer,
                  foreground="#555").pack(side="left")

        # ---------------------------------------- Tab 2: Copy & refresh
        st = self.sync_tab
        self.status = tk.StringVar(value="Planning...")
        sb_status = ttk.Label(st, textvariable=self.status,
                              font=("Segoe UI", 11, "bold"), wraplength=880,
                              justify="left")
        sb_status.pack(anchor="w")

        self.detail = tk.StringVar(value="")
        ttk.Label(st, textvariable=self.detail, wraplength=880,
                  justify="left", foreground="#555").pack(anchor="w",
                                                          pady=(4, 6))

        # Title filter -> sync's --only. Deliberately the SAME flag the CLI uses
        # rather than per-row checkboxes: checkboxes would mean assembling a
        # subset here and handing plan_sync a selection it did not make, i.e. a
        # second route into the store. This stays one route.
        filt = ttk.Frame(st)
        filt.pack(fill="x", pady=(0, PAD))
        ttk.Label(filt, text="Only sessions whose title contains:").pack(side="left")
        self.only_var = tk.StringVar(value="")
        self.only_entry = ttk.Entry(filt, textvariable=self.only_var, width=34)
        self.only_entry.pack(side="left", padx=6)
        self.only_entry.bind("<Return>", lambda _e: self.refresh())
        self.filter_btn = ttk.Button(filt, text="Apply filter", command=self.refresh)
        self.filter_btn.pack(side="left")
        self.clear_btn = ttk.Button(filt, text="Clear", command=self._clear_filter)
        self.clear_btn.pack(side="left", padx=(4, 0))
        # RULING 8. Unticked every time the window opens: this is the only
        # control here that overwrites rather than adds, so it is a decision
        # for one run, never a remembered preference.
        self.update_var = tk.BooleanVar(value=False)
        self.update_chk = ttk.Checkbutton(
            filt, text="Also refresh rows already there", variable=self.update_var,
            command=self._on_update_toggle)
        self.update_chk.pack(side="left", padx=(16, 0))
        # Ticked BY DEFAULT, and the one default in this window that is not
        # "do nothing". Refreshing everything is almost never what someone
        # means: each account's rows are a snapshot of when THAT account last
        # opened a session, so a bulk refresh sends whichever account you
        # happen to be signed into over the top of the others - measured on
        # this machine, one direction had 11 rows genuinely newer and 28 that
        # would have gone backwards. Safe to default on because it can only
        # ever send FEWER rows than the box above it.
        self.newer_var = tk.BooleanVar(value=True)
        self.newer_chk = ttk.Checkbutton(
            filt, text="only where mine is newer", variable=self.newer_var,
            command=self.refresh, state="disabled")
        self.newer_chk.pack(side="left", padx=(6, 0))
        # Off by default and never remembered. A row is a POINTER to a
        # conversation, and two accounts can point at different ones, so a
        # refresh can leave the displaced conversation reachable from nowhere.
        # That is the one outcome of a refresh that takes access away instead
        # of updating something, which is why it needs saying yes to.
        self.orphan_var = tk.BooleanVar(value=False)
        self.orphan_chk = ttk.Checkbutton(
            filt, text="allow hiding a conversation", variable=self.orphan_var,
            command=self.refresh, state="disabled")
        self.orphan_chk.pack(side="left", padx=(6, 0))

        body = ttk.Frame(st)
        body.pack(fill="both", expand=True)
        self.text = tk.Text(body, wrap="none", height=18, font=("Consolas", 9),
                            state="disabled", borderwidth=1, relief="solid")
        sb = ttk.Scrollbar(body, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        self.text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        bar = ttk.Frame(st)
        bar.pack(fill="x", pady=(PAD, 0))
        self.apply_btn = ttk.Button(bar, text="Apply", command=self.on_apply,
                                    state="disabled")
        self.apply_btn.pack(side="right")
        self.refresh_btn = ttk.Button(bar, text="Refresh", command=self.refresh)
        self.refresh_btn.pack(side="right", padx=(0, 6))
        self.trust_var = tk.BooleanVar(value=ccs.signed_helper_trust_enabled(self.env))
        self.trust_chk = ttk.Checkbutton(
            bar, text="Let Chrome stay open", variable=self.trust_var,
            command=self.on_toggle_trust)
        self.trust_chk.pack(side="left")
        self.forget_btn = ttk.Button(bar, text="Change destination",
                                     command=self.forget_destination)
        if self.dest_choice:
            self.forget_btn.pack(side="left", padx=(6, 0))
        # Only shown while a --live assertion is in force. It is the escape
        # hatch that lets the assertion persist across replans safely: the
        # answer stops being re-demanded every time, and stays changeable on
        # purpose rather than by accident.
        self.live_btn = ttk.Button(bar, text="Change signed-in account",
                                   command=self.forget_live)

        # ------------------------------------------------ Tab 3: Health
        ht = self.health_tab
        self.health_status = tk.StringVar(
            value="Press Refresh for the full health check.")
        hb_status = ttk.Label(ht, textvariable=self.health_status,
                              font=("Segoe UI", 11, "bold"), wraplength=880,
                              justify="left")
        hb_status.pack(anchor="w")
        hbody = ttk.Frame(ht)
        hbody.pack(fill="both", expand=True, pady=(6, 0))
        self.health_text = tk.Text(hbody, wrap="none", height=18,
                                   font=("Consolas", 9), state="disabled",
                                   borderwidth=1, relief="solid")
        hsb2 = ttk.Scrollbar(hbody, orient="vertical",
                             command=self.health_text.yview)
        self.health_text.configure(yscrollcommand=hsb2.set)
        self.health_text.pack(side="left", fill="both", expand=True)
        hsb2.pack(side="right", fill="y")
        hbar = ttk.Frame(ht)
        hbar.pack(fill="x", pady=(PAD, 0))
        self.doctor_btn = ttk.Button(hbar, text="Refresh",
                                     command=self.on_doctor)
        self.doctor_btn.pack(side="right")
        # Execution stays in the CLI, deliberately - recover is a directional
        # judgment whose CLI prose walks the user through the evidence. This
        # button hands over the command, nothing more.
        self.copy_btn = ttk.Button(hbar, text="Copy the recover command",
                                   command=self.on_copy_recover)

        # The mutation-gate red line, one per tab (the text carries its own
        # !! prefix - never color alone). One StringVar backs all three.
        self.gate_var = tk.StringVar(value="")
        self._gate_labels = []
        for tab, anchor in ((lt, lb_status), (st, sb_status),
                            (ht, hb_status)):
            lbl = ttk.Label(tab, textvariable=self.gate_var,
                            foreground="#a00000", wraplength=880,
                            justify="left")
            self._gate_labels.append((lbl, anchor))

        self.refresh()
        self.refresh_level()

    # ---------------------------------------------------------------- helpers
    def show(self, lines):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", "\n".join(lines))
        self.text.configure(state="disabled")

    def show_level(self, lines):
        self.level_text.configure(state="normal")
        self.level_text.delete("1.0", "end")
        self.level_text.insert("1.0", "\n".join(lines))
        self.level_text.configure(state="disabled")

    def show_health(self, lines):
        self.health_text.configure(state="normal")
        self.health_text.delete("1.0", "end")
        self.health_text.insert("1.0", "\n".join(lines))
        self.health_text.configure(state="disabled")

    def busy(self, on):
        """Disable EVERY action while a worker runs - not a selected few.

        Each control left live is a competing worker: mutation locks produce
        spurious refusals, an unlocked plan can read a half-undone destination,
        and callbacks overwrite each other's UI state. The health check was the
        sharpest historical case - it used to clear self.manifest, so
        finishing mid-copy made the apply callback fault on a manifest that
        had become None *after the rows were already written*.

        Counted, not boolean, because the window now runs one reader per tab
        at open: two concurrent read-only workers each call busy(True) and the
        controls stay down until the LAST one releases. The Apply buttons are
        deliberately not in the list - their enablement is each pane's own
        verdict (rows to copy, renames to run, the mutation gate), recomputed
        by _apply_gate_to_buttons on release.
        """
        self._busy_count = max(0, self._busy_count + (1 if on else -1))
        state = "disabled" if self._busy_count else "normal"
        for w in ((self.refresh_btn, self.undo_btn, self.doctor_btn,
                   self.only_entry, self.filter_btn, self.clear_btn,
                   self.trust_chk, self.update_chk, self.newer_chk,
                   self.orphan_chk, self.live_btn, self.forget_btn,
                   self.level_refresh_btn, self.copy_btn)
                  + tuple(self._banner_widgets)
                  + tuple(self._hold_widgets)):
            try:
                w.configure(state=state)
            except tk.TclError:
                pass                    # a rebuilt hold row already destroyed it
        # newer_chk qualifies update_chk, so re-releasing everything must not
        # leave it live while the box it qualifies is unticked - it would read
        # as a control that does nothing.
        if not self._busy_count:
            if not self.update_var.get():
                self.newer_chk.configure(state="disabled")
                self.orphan_chk.configure(state="disabled")
            self._apply_gate_to_buttons()

    # ---------------------------------------------------------------- planning
    def refresh(self, reset_live=False):
        # The --live assertion survives a replan, and is cleared only on apply
        # or when the user explicitly changes it.
        #
        # It used to be cleared on EVERY replan, on the reasoning that an
        # assertion is a statement about right now and a stale one must not
        # survive an account switch. Sound in principle; miserable in practice.
        # Picking a destination re-plans, and so does ticking "Also refresh",
        # so a single ordinary sitting - assert, choose destination, assert,
        # tick the box, assert - asked the same question four times, each one
        # phrased as though nothing had been said. A question re-asked that
        # often stops being read, which is the opposite of what an assertion
        # this consequential needs.
        #
        # Safe because the assertion is never load-bearing on its own: the
        # executor re-derives the live account itself and revalidates the
        # certification against the identity files before writing (RULING 5,
        # _certified_live_account), so a stale answer refuses at apply rather
        # than writing the wrong store. It is also cleared the moment an apply
        # completes, shown in the window while it is in force, and changeable
        # from the button beside it - visible state, not remembered state.
        if reset_live:
            self.live_choice = ""
        # Snapshot the filter on the UI thread and carry it through. Reading
        # only_var again inside the worker or the callback let a quick A-then-B
        # change install an A-selected manifest while the window described it as
        # filtered by B - so Apply would copy something other than what was
        # shown. The generation counter drops stale callbacks outright.
        self.generation += 1
        gen = self.generation
        only = self.only_var.get().strip()
        self.busy(True)
        self._sync_apply_ok = False
        self.apply_btn.configure(state="disabled")
        self.status.set("Planning...")
        self.detail.set("")
        self.show([])
        threading.Thread(
            target=self._plan_worker,
            args=(gen, only, self.update_var.get(), self.newer_var.get(),
                  self.orphan_var.get()),
            daemon=True).start()

    def _plan_worker(self, gen, only, update, newer_only, allow_orphan):
        try:
            flags = ccs.SyncFlags(to=self.dest_choice, live=self.live_choice,
                                  only=only, update=update,
                                  newer_only=newer_only,
                                  allow_orphan=allow_orphan)
            manifest = ccs.plan_sync(self.env, flags)
            self.root.after(0, self._plan_done, gen, only, manifest, None)
        except ccs.Refusal as exc:
            self.root.after(0, self._plan_done, gen, only, None, ("refusal", str(exc)))
        except Exception:
            self.root.after(0, self._plan_done, gen, only, None,
                            ("error", traceback.format_exc()))

    def _plan_done(self, gen, only, manifest, problem):
        if gen != self.generation:
            return                       # superseded by a newer plan
        self.busy(False)
        if problem:
            kind, msg = problem
            self.manifest = None
            if (kind == "refusal" and not self.live_choice
                    and "cannot identify the signed-in account" in msg
                    and "disagree" in msg):
                self.status.set("Which account is Claude Desktop signed into?")
                self.detail.set("The two files that record this disagree, and either can "
                                "be the stale one - so the tool refuses to guess.")
                self.show([msg])
                self._offer_live_picker()
                return
            # A SAVED destination can go stale: an 8-char id that identified one
            # store stops being unique the moment another account/org pair appears,
            # and every plan then refuses with Apply disabled. Route that refusal to
            # the same picker - otherwise the only way out is knowing about the
            # "Change destination" button, which is not a recovery path anyone
            # should have to guess at.
            if kind == "refusal" and "be more specific" in msg and "matched" in msg:
                self.status.set("The saved destination is no longer unique")
                self.detail.set("It matches more than one store now - probably because "
                                "an account was added. Pick the one you mean; the "
                                "choice is saved as a full path, which cannot go "
                                "ambiguous again.")
                self.show([msg])
                self._offer_destination_picker(msg)
                return
            if kind == "refusal" and "more than one other account store" in msg:
                self.status.set("Which account should these sessions go to?")
                self.detail.set("More than one other account store exists on this "
                                "machine. Pick the destination, then Refresh.")
                self.show([msg])
                self._offer_destination_picker(msg)
                return
            self.status.set("Refused" if kind == "refusal" else "Something went wrong")
            self.detail.set("Nothing was written. The tool's own explanation:"
                            if kind == "refusal" else
                            "This is a bug in the launcher, not a refusal.")
            self.show([msg])
            return

        self.manifest = manifest
        home = self.env.home
        tally = manifest.get("tally") or {}
        rows = manifest.get("rows") or []
        src = manifest.get("source_email") or manifest["source_account"][:8]
        dst = manifest.get("dest_email") or manifest["dest_account"][:8]

        # A remembered email is marked, never passed off as freshly observed: it
        # says what was true when that account was last signed in. The path below
        # it stays the identifier that actually settles which store this is.
        esrc = manifest.get("dest_email_source") or ""
        if esrc.startswith("memo"):
            _, _, seen = esrc.partition(":")
            dst += "   (remembered{0})".format(", " + seen if seen else "")
        lines = ["from  {0}".format(src),
                 "      " + short(manifest["source_path"], home),
                 "",
                 "to    {0}".format(dst),
                 "      " + short(manifest["dest_path"], home),
                 ""]
        # Tally keys are the manifest's own, verified against a real plan - not
        # guessed. A miscounted label here would quietly under-report skips.
        for key, label in (("present", "already in the destination"),
                           ("no_transcript", "skipped, transcript gone"),
                           ("deleted", "kept deleted (you deleted these there)"),
                           ("filtered", "filtered out"),
                           ("unreadable", "unreadable rows"),
                           ("held_older", "held back - their copy is NEWER"),
                           ("held_orphan", "held back - would HIDE a conversation"),
                           ("swapping", "change WHICH conversation opens"),
                           ("held_unknown", "held back - could not tell which is newer"),
                           ("resurrected", "!! RESURRECTED (deletion overridden)")):
            val = tally.get(key)
            count = len(val) if isinstance(val, (list, tuple, set)) else val
            if count:
                lines.append("{0:<38}: {1}".format(label, count))
        # Rows held back as orphaning, with the measurement that makes the hold
        # judgeable. Listed before the plan itself: they are the reason a plan
        # can come back empty, and a user staring at "0 to copy" needs to see
        # why and what ticking the box would actually cost.
        detail = list(tally.get("held_orphan_detail") or [])
        if detail:
            # Unmeasured first, then lowest overlap first - "could not look" is
            # the least reassuring answer, not the largest measured loss.
            detail.sort(key=lambda d: (d.get("overlap") is not None,
                                       d.get("overlap") if d.get("overlap") is not None else 0))
            lines += ["!! NOT SENT - each would open a DIFFERENT conversation, and the",
                      "   one it opens now was not confirmed reachable from any other",
                      "   account. Tick \"allow hiding a conversation\" to send them.", ""]
            for d in detail:
                lines.append("   !! " + (d.get("title") or "")[:88])
                # Confirmed-orphan and could-not-tell are different claims and
                # must not share a line.
                lines.append("        " + (
                    "nothing else points at the conversation it opens now"
                    if d.get("orphan") is True else
                    "a store could not be read - whether anything else points at "
                    "it is UNKNOWN"))
                # The length belongs in the WINDOW, not only in the CLI report.
                # This is where the "allow hiding a conversation" checkbox is,
                # so this is where the number that decides it has to appear.
                length = ccs._length_clause(d.get("displaced_turns"),
                                            d.get("incoming_turns"))
                lines.append("        " + (length or "turn counts unknown - "
                                           "length change could not be measured"))
                lines.append("        " + ccs._overlap_clause(d.get("overlap")))
            lines.append("")

        refreshes = [r for r in rows if r.get("is_update")]
        adds = [r for r in rows if not r.get("is_update")]
        if refreshes:
            lines += ["!! OVERWRITING {0} row(s) already in the destination"
                      .format(len(refreshes)),
                      "   each replaces the WHOLE row with this account's copy, not",
                      "   just its title and last-activity time.",
                      "   undo restores the exact bytes replaced - while the operation",
                      "   stays in the journal (the ten most recent are kept); a row",
                      "   that account changed since planning is refused, never",
                      "   overwritten.", ""]
            # Name the direction per row, exactly as the CLI report does. A
            # count alone ("2 of them would move that account backwards") tells
            # a window user that something is wrong but not which rows, and the
            # only way to find out was to leave for the terminal.
            for r in refreshes:
                title = (r.get("title") or r.get("session_id", ""))[:88]
                if r.get("regresses"):
                    mark = "   <- their copy is NEWER; this moves it BACKWARDS"
                elif r.get("activity_unknown"):
                    mark = "   <- which copy is newer could not be determined"
                else:
                    mark = ""
                lines.append("   !! " + title + mark)
                # Same rule as the CLI report: a pointer swap is a different
                # act from a metadata refresh and must not read like one.
                if r.get("swaps_conversation"):
                    orph = r.get("displaced_orphan")
                    # "another account" was wrong as of 0.9.13: reachability is
                    # now per ROW, so the voucher can be a different row in this
                    # same destination account. Saying "account" would send the
                    # user looking in the wrong sidebar.
                    fate = ("NOTHING else points at it - it becomes unreachable"
                            if orph is True else
                            "could not confirm anything else points at it"
                            if orph == "unknown" else
                            "another surviving row still opens it")
                    lines.append("        ^ opens a DIFFERENT conversation after "
                                 "this; the one it opens now: " + fate)
                    length = ccs._length_clause(r.get("displaced_turns"),
                                                r.get("incoming_turns"))
                    lines.append("          " + (
                        length or "turn counts unknown - length change could "
                                  "not be measured"))
                    lines.append("          " +
                                 ccs._overlap_clause(r.get("displaced_overlap")))
            # All three of what the CLI prints, not just the first. Printing
            # only `dest_dropped` hid a reset of permission state that account
            # set itself, and - worse - rendered nothing at all when the field
            # comparison returned "could not tell", turning "I could not look"
            # into "there was nothing to report" in the one surface whose users
            # are least likely to cross-check the terminal.
            lost = sorted({k for r in refreshes for k in (r.get("dest_dropped") or [])})
            reset = sorted({k for r in refreshes for k in (r.get("dest_reset") or [])})
            unknown = sum(1 for r in refreshes if r.get("dest_dropped") is None)
            if lost:
                lines.append("   fields their row loses: " + ", ".join(lost))
            if reset:
                lines.append("   reset over their own setting: " + ", ".join(reset))
            if unknown:
                lines.append("   {0} row(s) could not be compared field by field -"
                             " the whole row is still replaced.".format(unknown))
            lines.append("")
        lines += ["{0:<38}: {1}".format("to copy", len(adds)), ""]
        for r in adds:
            lines.append("   " + (r.get("title") or r.get("session_id", ""))[:90])

        if manifest.get("live_override"):
            lines = ["!! --live certification in effect", ""] + lines

        self.show(lines)
        # Offered on every plan, not only right after an apply: "I synced
        # yesterday and want it back" is the same need, and the CLI was the
        # only answer to it before.
        self._update_undo_button()
        self._sync_live_button()
        # A filter that hides candidates must say so on the status line, not only
        # in the tally: "nothing to copy" reads as "you are up to date", which is
        # a different and misleading statement when a filter caused it.
        suffix = "  (filtered by “{0}”)".format(only) if only else ""
        if rows:
            self.status.set("{0} session{1} ready to copy{2}".format(
                len(rows), "" if len(rows) == 1 else "s", suffix))
            self.detail.set("Nothing is written until you press Apply. The Claude "
                            "desktop app must be closed for that step.")
            # Carve-out 1 of the moved pane's "unchanged" contract: the
            # mutation gate can hold this button down even with rows ready.
            self._sync_apply_ok = True
            self._apply_gate_to_buttons()
        elif only:
            # NOT "no titles match": a title can match and still not be copyable
            # - already present, transcript gone, tombstoned. The tally above
            # shows which, so claim only what is certain.
            self.status.set("No sessions matching “{0}” are ready to copy".format(only))
            self.detail.set("Any that matched but were skipped are counted above. "
                            "Clear the filter to see everything.")
        else:
            self.status.set("Nothing to copy - the other account is up to date")
            self.detail.set("")

    @staticmethod
    def _candidates(msg):
        """(org_token, whole_line) for each candidate in a 'name one with --to'
        refusal. Candidate lines look like:

            dd44e101/53346e14   (286 rows)          ~\\...\\dd44e101...\\53346e14...

        The whole line becomes the button text, so the row count and any
        [shares your signed-in org] tag travel with it - those are exactly what
        distinguishes the real store from the empty directory the app scaffolds.

        A LOOSE "first token contains a slash" test is not enough: the refusal's
        own footnote contains the literal "<account>/<org> pair, and ...", which
        such a test turns into a bogus button. Ids are 8-hex prefixes, so match
        exactly that.
        """
        import re
        pat = re.compile(r"^[0-9a-f]{8}/[0-9a-f]{8}$")
        lines = msg.splitlines()
        out = []
        for i, line in enumerate(lines):
            stripped = line.strip()
            parts = stripped.split()
            if not (len(parts) >= 2 and pat.match(parts[0])):
                continue
            # A candidate's warning sits on the FOLLOWING line, as "^ ...", because
            # a trailing suffix wrapped off-screen in a terminal. That fix silently
            # cost the GUI its warning: this parser kept only candidate lines, so
            # every button rendered unmarked and a cross pair looked exactly like
            # the real store - which is precisely how an empty destination got
            # picked on a three-account machine. Carry the note with the candidate.
            note = ""
            if i + 1 < len(lines) and lines[i + 1].strip().startswith("^"):
                note = lines[i + 1].strip().lstrip("^").strip()
            out.append((parts[0], stripped, note))
        return out

    def _resolve_pair(self, pair):
        """'<acct8>/<org8>' -> that store's full path, or '' if it is not unique.

        The pair is resolved against the real store list rather than saved as-is,
        because BOTH halves are 8-character prefixes and neither is guaranteed
        unique: saving a bare org id is what broke here - "ef430bfb" identified
        one store until a third account appeared, then matched two and every plan
        refused with a disabled Apply and no way forward in the window. A full
        path is unique by construction.
        """
        acct, _, org = pair.partition("/")
        hits = [p for a, o, p in ccs._account_dirs(self.env)
                if a.startswith(acct) and o.startswith(org)]
        return hits[0] if len(hits) == 1 else ""

    def _offer_destination_picker(self, msg):
        cands = self._candidates(msg)
        if not cands:
            return                      # unrecognised shape: leave the raw refusal
        win = tk.Toplevel(self.root)
        win.title("Choose a destination")
        win.transient(self.root)
        header = ("More than one other account store on this machine.\n"
                  "Pick the one whose sidebar should get these sessions.")
        # Every candidate empty is the just-created-an-account case, where the row
        # count cannot help at all. Say the one thing that reliably settles it
        # rather than leaving the user to read uuids out of paths.
        if all("(no listing rows)" in line for _t, line, _n in cands):
            header += ("\n\nAll of them are empty, so row counts cannot tell them apart. "
                       "The reliable way:\nsend one message in the new account, close the "
                       "app, then press Refresh - the store\nthat gained a row is the "
                       "right one.")
        ttk.Label(win, padding=PAD, justify="left", wraplength=620,
                  text=header).pack(anchor="w")
        for pair, line, note in cands:
            text = line if not note else line + "\n     ⚠ " + note
            def pick(p=pair):
                # Save the resolved PATH, not the id fragment shown on the button.
                self.dest_choice = self._resolve_pair(p) or p
                save_pref(self.dest_choice)
                win.destroy()
                self.refresh()
            ttk.Button(win, text=text, command=pick).pack(fill="x", padx=PAD, pady=2)
        ttk.Button(win, text="Cancel", command=win.destroy).pack(pady=PAD)

    def _account_label(self, uuid):
        """email (id) when the email can be recovered, else just the id."""
        email = ""
        try:
            with open(os.path.join(self.env.home, ".claude.json"),
                      encoding="utf-8") as fh:
                import json
                oa = (json.load(fh) or {}).get("oauthAccount") or {}
            if isinstance(oa, dict) and oa.get("accountUuid") == uuid:
                email = oa.get("emailAddress") or ""
        except (OSError, ValueError, AttributeError, TypeError):
            pass
        email = email or ccs.dormant_account_email(self.env, uuid) or ""
        return ("{0}  ({1}…)".format(email, uuid[:8]) if email
                else "{0}…".format(uuid[:8]))

    def _offer_live_picker(self):
        """Turn the identity-disagreement refusal into an assertion, per RULING 5.

        Deliberately NOT a "just proceed" button. The user is stating a fact -
        which account the desktop app is signed into - so both candidates are
        shown neutrally, neither is pre-selected, and the consequence is spelled
        out: the OTHER store is the one that gets written.
        """
        dis = ccs._identity_disagreement(self.env)
        if not dis:
            return                       # shape changed: leave the raw refusal
        # One button per STORE, not per account, and the asserted value is the
        # store's path. An account can own several org directories - this very
        # machine has two per account - and a bare account uuid then matches
        # more than one store, which _resolve_live_assertion refuses. The user
        # would have been stuck: live_choice is set, so this picker would not
        # reopen, and there is no other way in the window to name an org.
        stores = [(a, o, p) for a, o, p in ccs._account_dirs(self.env) if a in dis]
        if not stores:
            return
        win = tk.Toplevel(self.root)
        win.title("Which account is signed in?")
        win.transient(self.root)
        ttk.Label(win, padding=PAD, justify="left", wraplength=560,
                  text="Claude Desktop and the Claude Code CLI disagree about which "
                       "account is signed in, and either record can be the stale one.\n\n"
                       "Tell it which store the DESKTOP APP is signed into right now. "
                       "The OTHER one is what gets written, so an answer that is wrong "
                       "writes the store you are actually using.\n\nThis answer holds "
                       "for this window until you copy or change it - it is shown "
                       "beside the Refresh button, and never saved to disk.").pack(
                           anchor="w")
        for a, o, p in stores:
            rows = ccs._listing_row_count(p)
            count = ("{0} rows".format(rows) if rows
                     else "no listing rows" if rows == 0 else "row count unreadable")

            def pick(path=p):
                self.live_choice = path      # a full path matches exactly one store
                win.destroy()
                self.refresh()
            ttk.Button(win, command=pick,
                       text="Signed in as  {0}   org {1}…   ({2})".format(
                           self._account_label(a), o[:8], count)).pack(
                               fill="x", padx=PAD, pady=3)
        ttk.Label(win, padding=(PAD, 4), foreground="#555", wraplength=520,
                  justify="left",
                  text="Or cancel and fix it at the source: run 'claude' then /login as "
                       "the account you are using, or switch the desktop app, so the two "
                       "records agree.").pack(anchor="w")
        ttk.Button(win, text="Cancel", command=win.destroy).pack(pady=PAD)

    def on_toggle_trust(self):
        """RULING 7's opt-in, as a checkbox rather than "go create a file".

        Turning it ON asks first and states the trade, because it loosens a
        safety guard; turning it OFF is a return to the default and needs no
        ceremony. The checkbox is re-read from disk afterwards rather than
        trusted, so a failed write cannot leave the box looking enabled.
        """
        want = self.trust_var.get()
        if want and not messagebox.askokcancel(
                "Let Chrome stay open?",
                "The desktop app's Chrome helper normally blocks writes unless it is "
                "the exact build this tool measured - and it auto-updates every few "
                "days, which is why Chrome keeps having to be closed.\n\n"
                "Turning this on trusts ANY helper at the app's own path that Windows "
                "reports as validly signed by Anthropic, PBC.\n\n"
                "It is weaker than the default: a future Anthropic build that started "
                "writing to the session store would be excused without anyone "
                "measuring it. Unsigned, tampered, differently-signed and out-of-path "
                "binaries still block.\n\n"
                "The desktop app itself must still be closed either way."):
            self.trust_var.set(False)
            return
        path = ccs.trust_signed_helper_path(self.env)
        try:
            if want:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write("Created from the claude-code-sessions window.\n"
                             "Delete this file to revoke. See docs/internals.md, "
                             "RULING 7.\n")
            elif os.path.exists(path):
                os.remove(path)
        except OSError as exc:
            messagebox.showerror("Could not change the setting", str(exc))
        actual = ccs.signed_helper_trust_enabled(self.env)
        self.trust_var.set(actual)
        self.status.set("Chrome may stay open (the desktop app still cannot)"
                        if actual else "Chrome must be closed again (the default)")
        self.detail.set("")

    def _clear_filter(self):
        self.only_var.set("")
        self.refresh()

    # ----------------------------------------------------------------- doctor
    @staticmethod
    def doctor_lines(rep, home=""):
        """The health report, findings first. Pure so it can be checked offline.

        Ordered by what actually blocks or endangers a sync rather than by what
        is numerous: an unresolved operation or a stale lock stops the next
        mutation, while dozens of aged-out transcripts are routine. Reporting
        them in report order would bury the one that matters.
        """
        blocking, notes = [], []
        st0 = rep.get("stores") or {}
        if st0.get("status") == "error":
            # gather_doctor exits 2 here and every mutation fails closed -
            # "couldn't look" is never "nothing there". Reporting this as merely
            # informational would claim a sync is possible when it is not.
            blocking.append("The session store could not be read: {0}. Mutations fail "
                            "closed until this is resolved.".format(
                                st0.get("detail") or "no detail reported"))
        if rep.get("stale_lock"):
            blocking.append("A stale lock is present - a previous run was interrupted. "
                            "`ccs recover` resolves it.")
        n = len(rep.get("nonterminal_ops") or [])
        if n:
            blocking.append("{0} operation(s) left unresolved by an interruption. "
                            "`ccs recover` classifies and finishes them.".format(n))
        if rep.get("row_errors"):
            blocking.append("{0} listing row(s) are unreadable - mutations are blocked "
                            "until they are readable again.".format(len(rep["row_errors"])))
        if rep.get("unknown_layout"):
            blocking.append("{0} unrecognised item(s) in the store layout. The tool "
                            "fails closed on these.".format(len(rep["unknown_layout"])))
        # Both of these make `ccs doctor` exit 1, and this window reported
        # "Nothing is blocking a sync" over the top of them. They go in the
        # blocking list rather than the inventory below because neither is
        # routine and neither is numerous - which is the line the ordering rule
        # in this docstring actually draws. Aged-out transcripts and legacy
        # folders are ordinary facts about a machine's history; a row this tool
        # created disappearing is the one documented risk of `new-row`, and a
        # rollback that could not remove what it wrote is an operation that did
        # not finish cleanly.
        n = len(rep.get("vanished_new_rows") or [])
        if n:
            blocking.append("{0} row(s) this tool created are no longer on disk - "
                            "the app may have rejected one it did not issue. Run "
                            "`ccs doctor`: it names the conversation each opened "
                            "and whether it can be recreated.".format(n))
        n = len(rep.get("rollback_residue") or [])
        if n:
            blocking.append("{0} rolled-back operation(s) left a row in the store "
                            "that could not be removed. `ccs doctor` names the row "
                            "and the operation.".format(n))

        st = rep.get("stores") or {}
        notes.append("store: {0}".format(st.get("status", "?")))
        for r in (st.get("roots") or []):
            notes.append("   " + (r.replace(home, "~") if home and r.startswith(home) else r))
        notes.append("listing rows: {0}".format(rep.get("row_count", "?")))
        for key, label in (
                ("dead_rows", "rows whose transcript is gone (usually retention)"),
                ("blank_rows", "rows with no transcript link"),
                ("unlisted_transcripts", "transcripts with no listing row (normal for "
                                         "CLI-created sessions)"),
                ("legacy_folders", "legacy-layout folders")):
            v = rep.get(key)
            if v:
                notes.append("{0}: {1}".format(label, len(v)))

        out = []
        if blocking:
            out.append("NEEDS ATTENTION")
            out += ["  - " + b for b in blocking]
            out.append("")
        else:
            out += ["Nothing is blocking a sync.", ""]
        out.append("Inventory")
        out += ["  " + n for n in notes]
        out += ["", "These counts are observations, not errors - see `ccs doctor` for the "
                    "full report with its reasoning."]
        return out

    def on_doctor(self):
        """The Health tab's Refresh: the doctor report plus the
        interrupted-operation scan. A successful scan that finds nothing
        unresolved clears the red line and lifts the mutation gate on every
        tab - this button is the designed way back after `ccs recover`."""
        self.busy(True)
        self.health_status.set("Checking...")
        threading.Thread(target=self._doctor_worker, daemon=True).start()

    def _doctor_worker(self):
        try:
            rep = ccs.gather_doctor(self.env)
            entries = _scan_interrupted(self.env)
            self.root.after(0, self._doctor_done, rep, entries, None)
        except Exception:
            self.root.after(0, self._doctor_done, None, None,
                            traceback.format_exc())

    def _doctor_done(self, rep, entries, err):
        self.busy(False)
        if err:
            self.health_status.set("Health check failed")
            self.show_health([err])
            return
        self._set_gate(entries)
        lines = []
        if entries:
            lines += _interrupted_lines(entries, self.env.now()) + [""]
        lines += self.doctor_lines(rep, self.env.home)
        blocking = entries or (lines and "NEEDS ATTENTION" in lines)
        self.health_status.set("Health check: needs attention" if blocking
                               else "Health check: nothing blocking a "
                                    "mutation")
        self.show_health(lines)

    # ------------------------------------------------------------------- undo
    def _find_undoable_op(self):
        """The most recent completed op, described, IF its type is one this
        window reverses - sync, retitle or converge. None otherwise.

        Deliberately only the MOST RECENT completed op `ccs undo` would pick -
        so the button and the CLI can never disagree about which operation
        "the last one" is. If that op is anything this window does not do (a
        move, a repoint, a new-row - all CLI-only), no undo is offered here,
        because quietly reaching past it to an older op would undo something
        other than what the user last did.

        THE FILTER HAS TO MATCH `cmd_undo`'s CANDIDATE TUPLE, or this
        docstring is a lie. It read ("move", "sync") while cmd_undo grew
        "repoint" and then "new-row": with a completed new-row as the most
        recent operation, this skipped straight past it to an older sync and
        offered to undo THAT, while `ccs undo` would have reversed the
        new-row. "retitle" joined the tuple in 0.11.0 for the same reason,
        and "converge" in 0.12.0; both are now also types this window itself
        creates (the Level tab), so they are offered rather than parked.

        The descriptor carries what the confirmation needs: op_id, type, the
        written-row count, a button label naming what it reverses, and the
        RULING 5 live-override note (sync ops only - retitle and converge
        record no assertion the undo path would need to disclose... converge
        records live_asserted, but its undo deletes rows this op minted, a
        target no identity file arbitrates).
        """
        try:
            ops = [o for o in ccs.list_ops(self.env)
                   if o.manifest.get("status") == "completed"
                   and o.manifest.get("op_type", "move") in ("move", "sync",
                                                             "repoint", "new-row",
                                                             "retitle",
                                                             "converge")]
        except Exception:
            return None
        if not ops:
            return None
        m = ops[-1].manifest
        kind = m.get("op_type")
        rows = sum(1 for r in m.get("rows", []) if r.get("written"))
        if kind not in ("sync", "retitle", "converge") or not rows:
            return None
        t = {"op_id": m["op_id"], "type": kind, "rows": rows,
             "live_note": ccs._live_override_note(m)}
        if kind == "sync":
            t["dest"] = (m.get("dest_email")
                         or (m.get("dest_account", "")[:8] + "…"))
            t["label"] = "Undo last copy ({0} session{1})".format(
                rows, "" if rows == 1 else "s")
        elif kind == "retitle":
            t["new_title"] = m.get("new_title", "")
            t["label"] = "Undo last rename"
        else:
            t["accounts"] = len({r.get("account")
                                 for r in m.get("rows", [])
                                 if r.get("written")})
            t["label"] = "Undo last converge ({0} row{1})".format(
                rows, "" if rows == 1 else "s")
        return t

    def _find_undoable_sync(self):
        """The old sync-only tuple - (op_id, rows, dest, live_note) when the
        undoable op is a sync, else None. Kept as a wrapper over
        _find_undoable_op because the check harness pins this exact contract;
        the selection rule (and its cmd_undo-mirroring obligation) lives in
        _find_undoable_op now. Dispatched through the class, not self: the
        harness calls this unbound, on a stand-in object that has only
        .env."""
        t = SyncApp._find_undoable_op(self)
        if not t or t["type"] != "sync":
            return None
        return (t["op_id"], t["rows"], t["dest"], t["live_note"])

    def _update_undo_button(self):
        self.undo_target = self._find_undoable_op()
        if self.undo_target:
            self.undo_btn.configure(text=self.undo_target["label"])
            if not self.undo_btn.winfo_manager():
                self.undo_btn.pack(side="left", padx=(6, 0))
        else:
            self.undo_btn.pack_forget()

    def _undo_prompt(self, t):
        """The confirmation, carrying the op's own semantics in the CLI's
        wording - converge-undo's skip rules included."""
        if t["type"] == "sync":
            return ("Undo the last copy?",
                    "Remove the {0} listing row{1} copied into {2}?\n\nThis "
                    "deletes only rows this tool wrote, and only while they "
                    "still match what was written - if that account has since "
                    "opened one, it refuses rather than discard the change. "
                    "Conversations are never touched.".format(
                        t["rows"], "" if t["rows"] == 1 else "s", t["dest"]))
        if t["type"] == "retitle":
            return ("Undo the last rename?",
                    "{0} row{1} get their previous titles back, dropping "
                    "{2!r} - all of them or none of them, and the operation "
                    "is then consumed. A row that changed since the rename "
                    "refuses rather than overwrite the change.".format(
                        t["rows"], "" if t["rows"] == 1 else "s",
                        t.get("new_title", "")))
        return ("Undo the last converge?",
                "Remove the {0} row{1} it created across {2} account{3}, "
                "skipping any that is now load-bearing - a row that became "
                "the only pointer to its conversation, or one that account "
                "has since repointed or retitled, is kept and named in the "
                "report. Conversations are never touched.".format(
                    t["rows"], "" if t["rows"] == 1 else "s",
                    t.get("accounts", 1),
                    "" if t.get("accounts", 1) == 1 else "s"))

    def on_undo(self):
        if not self.undo_target:
            return
        if self._press_gate():
            return                       # the red line is the explanation
        t = self.undo_target
        title, prompt = self._undo_prompt(t)
        if t.get("live_note"):
            # RULING 5: every route that can mutate under a --live
            # certification discloses it BEFORE mutating. The CLI prints
            # this; a generic confirmation here would hide the premise the
            # deletion rests on.
            prompt += "\n\n" + t["live_note"]
        if not messagebox.askokcancel(title, prompt):
            return
        self.busy(True)
        self.apply_btn.configure(state="disabled")
        self.level_apply_btn.configure(state="disabled")
        self.undo_btn.configure(state="disabled")
        self.status.set("Undoing...")
        self.level_status.set("Undoing...")
        self._mutation_ui = _MutationMarker()
        threading.Thread(target=self._undo_worker,
                         args=(t["op_id"], t["type"]), daemon=True).start()

    def _undo_worker(self, op_id, kind):
        try:
            # Re-check that this is STILL the latest eligible operation, not just
            # that it exists. Another CLI move or sync can complete between the
            # button being drawn and the confirmation being accepted; undoing the
            # captured id then reaches behind a newer operation and disagrees with
            # what `ccs undo` would pick.
            current = self._find_undoable_op()
            if not current or current["op_id"] != op_id:
                raise ccs.Refusal(
                    "another operation completed since this window last looked, so "
                    "{0} is no longer the most recent one to undo. Nothing was "
                    "touched - press Refresh to see the current state.".format(op_id))
            ops = [o for o in ccs.list_ops(self.env)
                   if o.manifest.get("op_id") == op_id]
            if not ops:
                raise ccs.Refusal("operation {0} is no longer in the journal".format(op_id))
            undo = {"sync": ccs.undo_sync, "retitle": ccs.undo_retitle,
                    "converge": ccs.undo_converge}[kind]
            result = undo(self.env, ops[0])
            report = ops[0].manifest.get("undo_report")
            self.root.after(0, self._undo_done, kind, result, report, None)
        except ccs.Refusal as exc:
            self.root.after(0, self._undo_done, kind, None, None,
                            ("refusal", str(exc)))
        except Exception:
            self.root.after(0, self._undo_done, kind, None, None,
                            ("error", traceback.format_exc()))

    def _undo_done(self, kind, result, report, problem):
        if self._mutation_over():
            return                       # the window was closing; it may now
        self.busy(False)
        self.undo_btn.configure(state="normal")
        if problem:
            kind_p, msg = problem
            # NOT "nothing was removed": a refusal can follow some rows having
            # already been deleted or restored - undo_sync collects unlink
            # failures, undo_retitle stops mid-restore. Claiming otherwise
            # could leave a half-undone destination looking untouched.
            self.status.set("Undo did not complete" if kind_p == "refusal"
                            else "Something went wrong")
            self.detail.set(
                "The tool's own explanation is below. Press Refresh to see the "
                "destination's current state before deciding what to do - a refusal "
                "that names specific rows may have removed others first."
                if kind_p == "refusal" else "")
            self.show([msg])
            messagebox.showwarning("Undo did not complete", msg)   # see _apply_done
            self._update_undo_button()
            self._sync_live_button()
            self.refresh_level()
            return
        if kind == "sync":
            self.status.set("Undone - the copied rows were removed")
            self.detail.set("The other account's sidebar is back to how it was. "
                            "Press Refresh to plan again.")
            self.show([])
            note = "Undone - the copied rows were removed."
        elif kind == "retitle":
            note = "Undone - the previous titles are back."
        else:
            # Converge's undo is FORGIVING - it deletes what is still
            # redundant and skips what became load-bearing - so a bare
            # "undone" would hide exactly the rows it deliberately left.
            rep = report or {}
            note = "Undone - removed {0} row(s); {1} already gone{2}.".format(
                rep.get("deleted", 0), rep.get("already_gone", 0),
                "; {0} kept (see Health/CLI report)".format(
                    len(rep.get("skipped") or []))
                if rep.get("skipped") else "")
        self._update_undo_button()
        self._sync_live_button()
        # Undo after any step triggers the same fresh replan/re-render as
        # Apply does - the Level pane must never keep describing rows an undo
        # just removed.
        self.refresh_level(note=note)

    def forget_destination(self):
        self.dest_choice = ""
        save_pref("")
        self.refresh()

    def _on_update_toggle(self):
        """Enable the newer-only qualifier only while it can mean something."""
        state = "normal" if self.update_var.get() else "disabled"
        self.newer_chk.configure(state=state)
        self.orphan_chk.configure(state=state)
        self.refresh()

    def forget_live(self):
        """Drop the --live assertion and re-ask. The deliberate re-look the
        old clear-on-every-replan behaviour was trying to provide, minus the
        three unasked-for repetitions."""
        self.refresh(reset_live=True)

    def _sync_live_button(self):
        """Show 'Change signed-in account' only while an assertion is held."""
        # winfo_manager(), not winfo_ismapped(): the question is "is this packed",
        # and ismapped answers "is it on screen right now", which is also False
        # for a minimised or withdrawn window - so the button would be re-packed
        # on every plan while iconified.
        if self.live_choice:
            if not self.live_btn.winfo_manager():
                self.live_btn.pack(side="left", padx=(6, 0))
        elif self.live_btn.winfo_manager():
            self.live_btn.pack_forget()

    # ----------------------------------------------------------- the Level tab
    def refresh_level(self, note=""):
        """One worker, two read-only calls - gather_alignment then
        plan_converge - plus the environment weather (claude_running,
        read-only) and the unresolved-op scan. NOTE, when given, survives
        into the next render's detail line so an undo's outcome is not
        instantly overwritten by the replan it triggers."""
        if note:
            self._level_note = note
        self.level_gen += 1
        gen = self.level_gen
        self.busy(True)
        self._level_apply_ok = False
        self.level_apply_btn.configure(state="disabled")
        self.level_status.set("Measuring...")
        threading.Thread(target=self._level_plan_worker,
                         args=(gen, self.level_live), daemon=True).start()

    def _level_plan_worker(self, gen, live):
        try:
            running = ccs.claude_running(self.env)
            try:
                entries = _scan_interrupted(self.env)
            except Exception:
                entries = None           # gates closed until Health can look
            rep = ccs.gather_alignment(self.env)
            man = ccs.plan_converge(self.env, ccs.ConvergeFlags(live=live))
            self.root.after(0, self._level_plan_done, gen, rep, man, running,
                            entries, None)
        except ccs.Refusal as exc:
            self.root.after(0, self._level_plan_done, gen, None, None, [],
                            [], ("refusal", str(exc)))
        except Exception:
            self.root.after(0, self._level_plan_done, gen, None, None, [],
                            [], ("error", traceback.format_exc()))

    def _level_plan_done(self, gen, rep, man, running, entries, problem):
        if gen != self.level_gen:
            return                       # superseded by a newer plan
        self.busy(False)
        self._set_gate(entries, error=entries is None)
        if entries:
            self._render_health_entries(entries)
        if problem:
            # A read-side failure is NOT a refusal-after-Apply: it renders in
            # the pane with the status line set, never as a modal, and
            # Refresh stays enabled as the retry.
            kind, msg = problem
            self.level_status.set("Refused" if kind == "refusal"
                                  else "Something went wrong")
            self.level_detail.set(
                "Nothing was written; Refresh retries. The tool's own "
                "explanation:" if kind == "refusal" else
                "This is a bug in the launcher, not a refusal.")
            self.show_level([msg])
            self.level_footer.set("")
            self._apply_gate_to_buttons()
            return
        self._render_level(rep, man, running)

    def _render_level(self, rep, man, running):
        """The pane, top to bottom, from post-read state only: notice,
        banner, scoreboard + plan summary, hold rows (merged by key so a
        replan never silently resets the user's edits), footer."""
        self.level_rep, self.level_manifest = rep, man
        self.hold_models = _merge_hold_models(_hold_models(man),
                                              self._current_models())
        if running:
            if not self.level_notice.winfo_manager():
                self.level_notice.pack(anchor="w", pady=(0, 2),
                                       before=self.level_body)
        elif self.level_notice.winfo_manager():
            self.level_notice.pack_forget()
        self._render_banner(man)
        self.show_level(_scoreboard_lines(rep) + [""]
                        + _plan_summary_lines(man))
        self._render_holds()
        state = _level_state(rep, man, self.hold_models)
        self.level_status.set(state["status"])
        detail = state["detail"]
        if self._level_note:
            detail = (self._level_note + "  " + detail).strip()
            self._level_note = ""
        self.level_detail.set(detail)
        self._level_apply_ok = state["apply"]
        self._apply_gate_to_buttons()
        self._update_undo_button()
        self.level_footer.set("Measured at " + time.strftime("%H:%M:%S"))

    def _render_banner(self, man):
        """The 0.13.0 identity warning, when the manifest carries one - the
        existing live-picker pattern inline: one button per disagreeing
        STORE (an account can own several org directories, the sync picker's
        hard-won lesson), labeled by email where known, setting the
        assertion and replanning."""
        for w in self.level_banner.winfo_children():
            w.destroy()
        self._banner_widgets = []
        if self.level_live:
            row = ttk.Frame(self.level_banner)
            row.pack(fill="x", pady=(0, 4))
            ttk.Label(row, foreground="#a00000", wraplength=680,
                      justify="left",
                      text="!! --live assertion in force for the next Apply "
                           "- cleared when its sequence ends; never saved."
                      ).pack(side="left")
            b = ttk.Button(row, text="Change signed-in account",
                           command=self.forget_level_live)
            b.pack(side="left", padx=(6, 0))
            self._banner_widgets.append(b)
            return
        dis = (man or {}).get("identity_disagreement")
        if not isinstance(dis, dict):
            return
        oauth, config = dis.get("oauth") or "", dis.get("config") or ""
        ttk.Label(self.level_banner, foreground="#a00000", wraplength=880,
                  justify="left",
                  text="!! ~/.claude.json ({0}) and config.json ({1}) "
                       "disagree about which account is signed in, and "
                       "either record can be the stale one. The copy stage "
                       "will refuse (RULING 5) until you say which account "
                       "the DESKTOP APP is on right now - the answer covers "
                       "one Apply and is never written to disk.".format(
                           oauth[:8], config[:8])).pack(anchor="w",
                                                        pady=(0, 2))
        try:
            stores = [(a, o, p) for a, o, p in ccs._account_dirs(self.env)
                      if a in (oauth, config)]
        except Exception:
            stores = []
        for a, o, p in stores:
            rows = ccs._listing_row_count(p)
            count = ("{0} rows".format(rows) if rows
                     else "no listing rows" if rows == 0
                     else "row count unreadable")

            def pick(path=p):
                self.level_live = path   # a full path matches exactly one store
                self.refresh_level()
            b = ttk.Button(self.level_banner, command=pick,
                           text="Signed in as  {0}   org {1}…   ({2})".format(
                               self._account_label(a), o[:8], count))
            b.pack(fill="x", pady=2)
            self._banner_widgets.append(b)

    def forget_level_live(self):
        """Drop the Level tab's --live assertion and re-ask - the same
        deliberate re-look the sync pane's forget_live provides."""
        self.level_live = ""
        self.refresh_level()

    def _render_holds(self):
        for w in self.hold_frame.winfo_children():
            w.destroy()
        self._hold_widgets = []
        if not self.hold_models:
            return
        ttk.Label(self.hold_frame,
                  text="Naming decisions - each ticked row becomes one "
                       "rename on Apply:",
                  font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(2, 4))
        for m in self.hold_models:
            row = ttk.Frame(self.hold_frame)
            row.pack(fill="x", pady=(0, 6), anchor="w")
            if m["editable"]:
                m["_tick_var"] = tk.BooleanVar(value=bool(m["ticked"]))
                m["_entry_var"] = tk.StringVar(value=m["entry"])
                chk = ttk.Checkbutton(row, text="Rename",
                                      variable=m["_tick_var"])
                chk.grid(row=0, column=0, sticky="nw", padx=(0, 6))
                ttk.Label(row, text=m["title"],
                          font=("Segoe UI", 9, "bold")).grid(row=0, column=1,
                                                             sticky="w")
                entry = ttk.Entry(row, textvariable=m["_entry_var"],
                                  width=86)
                entry.grid(row=1, column=1, sticky="we", pady=(2, 0))
                ttk.Label(row, text=m["evidence"], foreground="#555",
                          wraplength=780, justify="left").grid(row=2,
                                                               column=1,
                                                               sticky="w")
                if m["degrade_reason"]:
                    ttk.Label(row, text="no suggestion: " + m["degrade_reason"],
                              foreground="#a05000").grid(row=3, column=1,
                                                         sticky="w")
                row.columnconfigure(1, weight=1)
                self._hold_widgets += [chk, entry]
            else:
                ttk.Label(row, text="held: " + (m["title"]
                                                or m["classification"]),
                          font=("Segoe UI", 9, "bold")).pack(anchor="w")
                ttk.Label(row, text=m["evidence"], foreground="#555",
                          wraplength=820, justify="left").pack(anchor="w")

    def _current_models(self):
        """The hold models with the widgets' CURRENT text and ticks read
        back in - the tkinter variables stripped, so the result is the pure
        shape _level_steps_stage1 and _merge_hold_models take."""
        out = []
        for m in self.hold_models:
            clean = {k: v for k, v in m.items() if not k.startswith("_")}
            tick, entry = m.get("_tick_var"), m.get("_entry_var")
            if tick is not None:
                try:
                    clean["ticked"] = bool(tick.get())
                    clean["entry"] = entry.get()
                except tk.TclError:
                    pass                 # widget already destroyed: keep stored
            out.append(clean)
        return out

    # ------------------------------------------------------ the mutation gate
    def _set_gate(self, entries, error=False):
        """Render (or clear) the red line on every tab and gate Apply/Undo.

        The line carries its own !! prefix - never color alone. A scan that
        FAILED gates too: 'couldn't look' is never 'nothing there'.
        """
        if error:
            self.gate_text = ("!! the journal could not be read - Apply and "
                              "Undo are disabled until Health can scan it.")
        elif entries:
            self.gate_text = ("!! {0} interrupted operation(s) need "
                              "attention - see Health.".format(len(entries)))
        else:
            self.gate_text = ""
        self.gate_var.set(self.gate_text)
        for lbl, anchor in self._gate_labels:
            if self.gate_text:
                if not lbl.winfo_manager():
                    lbl.pack(anchor="w", after=anchor)
            elif lbl.winfo_manager():
                lbl.pack_forget()
        if self.gate_text and not error:
            if not self.copy_btn.winfo_manager():
                self.copy_btn.pack(side="left")
        elif self.copy_btn.winfo_manager():
            self.copy_btn.pack_forget()
        self._apply_gate_to_buttons()

    def _apply_gate_to_buttons(self):
        """Apply enablement is three verdicts ANDed: the pane's own (rows to
        copy / renames to run), no worker running, and no unresolved journal
        op. The gate covers the sync tab too - carve-out 1 of its "unchanged"
        contract - and Undo shares it, because a mutation launched over an
        unresolved op would change the state `recover` is about to reason
        over."""
        gated = bool(self.gate_text)
        busy = self._busy_count > 0
        self.apply_btn.configure(
            state="normal" if (self._sync_apply_ok and not gated
                               and not busy) else "disabled")
        self.level_apply_btn.configure(
            state="normal" if (self._level_apply_ok and not gated
                               and not busy) else "disabled")
        if gated:
            self.undo_btn.configure(state="disabled")
        elif not busy:
            self.undo_btn.configure(state="normal")

    def _press_gate(self):
        """The press-time re-scan, run before EVERY mutation - stage 1's
        confirm, stage 2's confirm (inside the worker), sync's Apply, Undo.
        A scan at window-open goes stale the moment another process dies
        mid-write; an op found here renders the red line and aborts the
        press. True when the press must abort."""
        try:
            entries = _scan_interrupted(self.env)
        except Exception:
            self._set_gate(None, error=True)
            return True
        self._set_gate(entries)
        if entries:
            self._render_health_entries(entries)
            return True
        return False

    def _render_health_entries(self, entries):
        self.show_health(_interrupted_lines(entries, self.env.now())
                         + ["", "Press Refresh here for the full health "
                                "check."])
        self.health_status.set("{0} interrupted operation(s) need attention"
                               .format(len(entries)))

    def on_copy_recover(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(RECOVER_COMMAND)
        self.health_status.set("Copied. Run it in a terminal, then press "
                               "Refresh here.")

    # ------------------------------------------------------- the Level apply
    def on_level_apply(self):
        if self.level_manifest is None:
            return
        if self._press_gate():
            return                       # the red line is the explanation
        models = self._current_models()
        steps, problems = _level_steps_stage1(models)
        if problems:
            # The cheap local set-check, before any engine call.
            messagebox.showwarning("These renames refuse locally",
                                   "\n\n".join(problems))
            return
        if not steps and not (self.level_manifest.get("rows") or []):
            return
        if steps and not self._confirm_stage1(steps):
            return
        self.level_gen += 1              # this press owns the pane now
        gen = self.level_gen
        self.busy(True)
        self._level_apply_ok = False
        self.level_apply_btn.configure(state="disabled")
        self.apply_btn.configure(state="disabled")
        self.level_status.set("Applying...")
        self.level_detail.set("")
        ui = _TkLevelUI(self)
        self._mutation_ui = ui
        threading.Thread(target=self._level_apply_worker,
                         args=(gen, steps, self.level_live, ui),
                         daemon=True).start()

    def _confirm_stage1(self, steps):
        """Stage 1's dialog: a scrollable Toplevel, not a stock messagebox -
        thirty mapping lines would push a messagebox's buttons off screen.
        The mapping list scrolls; the confirm/cancel row does not move."""
        head, mappings, footer = _stage1_dialog_parts(steps)
        win = tk.Toplevel(self.root)
        win.title(head)
        win.transient(self.root)
        result = {"ok": False}
        ttk.Label(win, padding=PAD, justify="left", wraplength=680,
                  text=footer).pack(anchor="w")
        bar = ttk.Frame(win)
        bar.pack(side="bottom", fill="x", padx=PAD, pady=PAD)

        def go():
            result["ok"] = True
            win.destroy()
        ttk.Button(bar, text="Rename", command=go).pack(side="right")
        ttk.Button(bar, text="Cancel",
                   command=win.destroy).pack(side="right", padx=(0, 6))
        body = ttk.Frame(win)
        body.pack(fill="both", expand=True, padx=PAD)
        txt = tk.Text(body, wrap="none", height=min(len(mappings), 14),
                      width=100, font=("Consolas", 9))
        tsb = ttk.Scrollbar(body, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=tsb.set)
        txt.insert("1.0", "\n".join(mappings))
        txt.configure(state="disabled")
        txt.pack(side="left", fill="both", expand=True)
        tsb.pack(side="right", fill="y")
        win.grab_set()
        self.root.wait_window(win)
        return result["ok"]

    def _level_apply_worker(self, gen, steps, live, ui):
        seq, refresh = _run_level_apply(self.env, steps, live, ui)
        self.root.after(0, self._level_apply_done, gen, seq, refresh)

    def _level_apply_done(self, gen, seq, refresh):
        if self._mutation_over():
            return                       # truncated: closed at the boundary
        self.busy(False)
        if gen != self.level_gen:
            return
        if seq.get("gate") is not None:
            # The press aborted before writing anything; the pane still
            # describes the store, and nothing consumed the live assertion.
            if isinstance(seq["gate"], str):
                self._set_gate(None, error=True)
            else:
                self._press_gate()       # re-render the line + the listing
            self.level_status.set("Nothing was written - resolve the "
                                  "interrupted operation(s) first.")
            return
        # The sequence has ended - completed, refused, or cancelled alike -
        # so the assertion it carried is spent. The refresh below already
        # planned without it; if the files still disagree, that replan is
        # what re-raises the banner.
        self.level_live = ""
        half = ""
        if refresh is not None and refresh[0] == "ok":
            _kind, rep, man, running = refresh
            self._render_level(rep, man, running)
            half = _scoreboard_half(rep)
            if seq.get("plan_problem"):
                # Stage 2's plan failed but the refresh's succeeded - a
                # transient. The failure still renders in-pane, under the
                # fresh render.
                self._append_level(["", "!! the copy stage could not plan:",
                                    seq["plan_problem"][1]])
        else:
            if seq.get("stage2") == "completed":
                # Applied-but-unverified is distinct from not-applied, and
                # must look it: keep the last rendered content, visibly
                # flagged stale, rather than going blank.
                self.level_status.set("Applied - could not re-measure; "
                                      "press Refresh.")
                self.level_detail.set("The operations landed and are "
                                      "journalled; everything below is "
                                      "shown from BEFORE the apply.")
                self._append_level(["", "!! shown from before the apply - "
                                        "press Refresh"])
            else:
                problem = (seq.get("plan_problem")
                           or (refresh if refresh is not None else None))
                if problem is not None:
                    self.show_level([problem[1]])
                self.level_detail.set("The pane could not be re-measured; "
                                      "Refresh retries.")
            self._update_undo_button()
        status = _sequence_status(seq, half)
        if status and not (refresh is not None and refresh[0] != "ok"
                           and seq.get("stage2") == "completed"):
            self.level_status.set(status)
        # Modal AFTER the pane settles, mirroring _apply_done: a refusal
        # after pressing Apply is the one message that must not be missable.
        if seq.get("rename_refusal"):
            messagebox.showwarning(
                "Nothing more was renamed" if seq.get("landed")
                else "Nothing was renamed", seq["rename_refusal"][1])
        elif seq.get("converge_problem"):
            messagebox.showwarning("Nothing was copied",
                                   seq["converge_problem"][1])

    def _append_level(self, lines):
        self.level_text.configure(state="normal")
        self.level_text.insert("end", "\n" + "\n".join(lines))
        self.level_text.configure(state="disabled")

    # ------------------------------------------------------- window lifecycle
    def _on_close(self):
        """Intercept the WM close (and the Close button) while a mutation
        worker runs: state the remaining-step count up front, finish the
        in-flight operation, truncate the remainder, close at that boundary.
        Landed operations are in the journal exactly as if the user had
        stopped there deliberately. (A hard kill mid-operation lands in the
        journal as an interrupted op; Health's detection is the net.)"""
        ui = self._mutation_ui
        if ui is None:
            self.root.destroy()
            return
        n = getattr(ui, "remaining", 0)
        if n:
            msg = ("The current operation will finish, and the {0} "
                   "remaining step(s) will NOT run. Close?".format(n))
        else:
            msg = ("The operation in progress will finish first, then the "
                   "window closes. Close?")
        if not messagebox.askokcancel("Close during an operation?", msg):
            return
        ui.truncate = True
        self._close_after_worker = True

    def _mutation_over(self):
        """True when the window was closing behind this worker - the
        callback must stop rendering into a window about to be destroyed."""
        self._mutation_ui = None
        if self._close_after_worker:
            self.root.destroy()
            return True
        return False

    # ---------------------------------------------------------------- applying
    def on_apply(self):
        if not self.manifest:
            return
        # Carve-out 1 of this pane's "unchanged" contract: the press-time
        # unresolved-op re-scan. A scan at window-open goes stale the moment
        # another process dies mid-write; a mutation launched over an
        # unresolved journal op would change the state `recover` is about to
        # reason over, so the window refuses to be the second writer.
        if self._press_gate():
            return
        n = len(self.manifest.get("rows") or [])
        dst = self.manifest.get("dest_email") or self.manifest["dest_account"][:8]
        rows = self.manifest.get("rows") or []
        n_upd = sum(1 for r in rows if r.get("is_update"))
        # Rows are per-account snapshots of one shared transcript, so the copy
        # being overwritten is not automatically the older one. Saying "the
        # newer copy" here asserted a comparison the tool never made; name the
        # ones that actually go backwards instead.
        n_back = sum(1 for r in rows if r.get("regresses"))
        back_note = ""
        if n_back:
            back_note = ("\n\n{0} of them would move that account BACKWARDS: its "
                         "copy was used more recently than this one.".format(n_back))
        # Swaps are the ones worth stopping for: the entry opens a different
        # conversation afterwards. Named in the confirmation, not just the pane.
        n_swap = sum(1 for r in rows if r.get("swaps_conversation"))
        n_hide = sum(1 for r in rows if r.get("displaced_orphan"))
        if n_swap:
            back_note += ("\n\n{0} of them will open a DIFFERENT CONVERSATION "
                          "afterwards - the row points at a transcript, and these "
                          "point at a different one in each account.".format(n_swap))
        if n_hide:
            back_note += ("\n{0} of those would leave the conversation they "
                          "displace unreachable from every sidebar.".format(n_hide))
        if n_upd and not messagebox.askokcancel(
                "Overwrite existing rows?",
                "{0} of these {1} row(s) ALREADY EXIST in {2}. Each will have its "
                "WHOLE row replaced by this account's copy - not just the title and "
                "last-activity time.{3}\n\nUndo restores the exact bytes replaced, "
                "for as long as the operation stays in the journal (the ten most "
                "recent are kept). A row that account has changed since this plan "
                "was made is refused rather than overwritten.\n\nContinue?".format(
                    n_upd, n, dst, back_note)):
            return
        # Split adds from refreshes. The old text said "This adds listing rows"
        # unconditionally, so a pure-refresh run told the user it was adding
        # rows immediately after they had confirmed overwriting some - the exact
        # mental model RULING 8's visibility rules exist to build.
        n_add = n - n_upd
        if n_add and n_upd:
            what = ("Add {0} row{1} and refresh {2} existing one{3} in {4}?".format(
                n_add, "" if n_add == 1 else "s",
                n_upd, "" if n_upd == 1 else "s", dst))
            does = ("It adds listing rows to that account's sidebar and overwrites "
                    "the {0} row{1} you just confirmed.".format(
                        n_upd, "" if n_upd == 1 else "s"))
        elif n_upd:
            what = "Refresh {0} existing row{1} in {2}?".format(
                n_upd, "" if n_upd == 1 else "s", dst)
            does = ("It adds nothing - it overwrites rows already in that account's "
                    "sidebar with this account's copy.")
        else:
            what = "Copy {0} session{1} into {2}?".format(
                n, "" if n == 1 else "s", dst)
            does = ("It adds listing rows to that account's sidebar. It never "
                    "deletes anything.")
        if not messagebox.askokcancel(
                "Copy sessions?", "{0}\n\n{1} Undo reverses it.".format(what, does)):
            return
        self.busy(True)
        self._sync_apply_ok = False
        self.apply_btn.configure(state="disabled")
        self.status.set("Copying...")
        self._mutation_ui = _MutationMarker()
        threading.Thread(target=self._apply_worker, args=(self.manifest,),
                         daemon=True).start()

    def _apply_worker(self, manifest):
        # The manifest is passed in, not read from self, so a callback that runs
        # concurrently cannot pull it out from under a copy that already wrote
        # rows. (The health check used to do exactly that by clearing it.)
        try:
            result = ccs.run_sync(self.env, manifest)
            self.root.after(0, self._apply_done, manifest, result, None)
        except ccs.Refusal as exc:
            self.root.after(0, self._apply_done, manifest, None, ("refusal", str(exc)))
        except Exception:
            self.root.after(0, self._apply_done, manifest, None,
                            ("error", traceback.format_exc()))

    def _apply_done(self, manifest, result, problem):
        if self._mutation_over():
            return                       # the window was closing; it may now
        self.busy(False)
        if problem:
            kind, msg = problem
            self.status.set("Refused - nothing was copied" if kind == "refusal"
                            else "Something went wrong")
            self.detail.set("The tool's own explanation:" if kind == "refusal" else "")
            self.show([msg])
            # MODAL, not just pane text. A refusal after pressing Apply is the one
            # message that must not be missable: in the CLI a refusal IS the whole
            # output, while here it lands quietly below a status line - so a user
            # who pressed Apply and then went to check the other account's sidebar
            # saw "nothing was copied" and no evidence anything had objected.
            messagebox.showwarning(
                "Nothing was copied" if kind == "refusal" else "Something went wrong",
                msg)
            # A refusal here is nearly always "the desktop app is running", which
            # is fixable in seconds - so leave Apply reachable after a Refresh.
            self.apply_btn.configure(state="disabled")
            return
        done = [r for r in (manifest.get("rows") or []) if r.get("written")]
        written = len(done)
        n_upd = sum(1 for r in done if r.get("is_update"))
        self.live_choice = ""            # an assertion covers one run, not a session
        # Same rule for the overwrite opt-in: it covers ONE run. Leaving the box
        # ticked after an apply meant the next plan in the same window silently
        # arrived with refreshes already enabled, which is not what "opt-in per
        # run, never implied" promises - and the window is the one place a user
        # is least likely to re-read what the checkbox does.
        self.update_var.set(False)
        self.status.set("Copied {0} session{1}{2}".format(
            written - n_upd, "" if written - n_upd == 1 else "s",
            ", refreshed {0}".format(n_upd) if n_upd else ""))
        self.detail.set("Sign into the other account (or restart the app) to see them. "
                        "Changed your mind? Undo is the button below - a GUI should not "
                        "send you to a terminal to reverse what it just did.")
        self.manifest = None
        self._update_undo_button()
        self._sync_live_button()


# ------------------------------------------------------------------- shortcuts

SHORTCUT_NAME = "Claude sessions.lnk"
# The pre-0.15 name. --install-shortcut deletes it so a rename does not leave
# two icons pointing at one window; --remove-shortcut removes both names. A
# taskbar pin to the old name is the user's to re-pin - pins are per-user
# shell state this tool does not touch.
OLD_SHORTCUT_NAME = "Claude session sync.lnk"


def _psq(s):
    """A PowerShell single-quoted literal. Backslashes are literal inside one,
    which is exactly what a Windows path needs - Python's repr is NOT a
    substitute, since it escapes for Python and PowerShell then takes the
    doubled backslashes literally."""
    return "'" + s.replace("'", "''") + "'"


def _shortcut_paths(name=SHORTCUT_NAME):
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    start = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows",
                         "Start Menu", "Programs")
    return [os.path.join(d, name) for d in (desktop, start)
            if d and os.path.isdir(d)]


def _launcher():
    """The installed ccs-gui launcher, so a shortcut survives this file moving.

    sys.executable is the launcher itself when frozen by a gui-script wrapper;
    otherwise fall back to pythonw + this module, which is what a source
    checkout has.
    """
    exe = sys.executable or ""
    if os.path.basename(exe).lower().startswith("ccs-gui"):
        return exe, ""
    guess = os.path.join(os.path.dirname(exe), "ccs-gui.exe")
    if os.path.isfile(guess):
        return guess, ""
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return (pyw if os.path.isfile(pyw) else exe), os.path.abspath(__file__)


def manage_shortcut(remove=False):
    if os.name != "nt":
        print("Shortcuts are Windows-only (as are this tool's store mutations).")
        return 2
    target, arg = _launcher()
    done = []
    # The old-name links go on BOTH routes: install replaces them (two icons
    # for one window is the migration bug), remove means remove.
    for link in _shortcut_paths(OLD_SHORTCUT_NAME):
        try:
            os.remove(link)
            done.append("removed " + link)
        except OSError:
            pass
    for link in _shortcut_paths():
        if remove:
            try:
                os.remove(link)
                done.append("removed " + link)
            except OSError:
                pass
            continue
        # Arguments is set UNCONDITIONALLY, including to empty. CreateShortcut on
        # an existing .lnk loads its current properties, so skipping this when
        # there is no argument leaves a stale one behind - measured: after moving
        # the GUI into the package, the target updated to ccs-gui.exe while
        # Arguments still pointed at the old tools/sync_gui.pyw. The launcher
        # would then be handed a path it rejects, with no console to show why.
        quoted_arg = ('"' + arg + '"') if arg else ""
        ps = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut({0});"
              "$s.TargetPath = {1};"
              "$s.Arguments = {2};"
              "$s.WorkingDirectory = {3};"
              "$s.IconLocation = {1};"
              "$s.Description = 'Keep your Claude account sidebars level';"
              "$s.Save()"
              ).format(_psq(link), _psq(target), _psq(quoted_arg),
                       _psq(os.path.dirname(target)))
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                            "-Command", ps], capture_output=True, text=True)
        if r.returncode != 0:
            print("failed:", link, r.stderr.strip())
            return 1
        done.append("created " + link)
    for line in done:
        print(line)
    if not remove and done:
        print('\nDouble-click "Claude sessions" to see how level the '
              "sidebars are. Nothing is written until you press Apply.")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="ccs-gui", description="Windowed front end for claude-code-sessions sync.")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--install-shortcut", action="store_true",
                   help='add Desktop + Start Menu shortcuts (Windows); an '
                        'existing "Claude session sync" shortcut from before '
                        '0.15.0 is deleted, not kept alongside')
    g.add_argument("--remove-shortcut", action="store_true",
                   help="remove those shortcuts (the pre-0.15.0 name too)")
    ns = ap.parse_args(argv if argv is not None else sys.argv[1:])

    if ns.install_shortcut or ns.remove_shortcut:
        return manage_shortcut(remove=ns.remove_shortcut)

    if TK_ERROR:
        # A GUI script has no console to print to, so say it where it can be seen.
        msg = ("This window needs tkinter, which is part of the Python standard "
               "library but is packaged separately on some Linux distributions "
               "(try: sudo apt install python3-tk).\n\n" + TK_ERROR)
        try:
            subprocess.run(["powershell", "-NoProfile", "-Command",
                            "Add-Type -AssemblyName PresentationFramework;"
                            "[System.Windows.MessageBox]::Show({0})".format(_psq(msg))],
                           check=False)
        except OSError:
            pass
        print(msg, file=sys.stderr)
        return 2

    root = tk.Tk()
    try:
        SyncApp(root)
    except Exception:
        messagebox.showerror("Claude sessions", traceback.format_exc())
        return 1
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
