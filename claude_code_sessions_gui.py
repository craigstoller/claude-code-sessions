"""Windowed front end for `ccs sync` - no terminal, nothing to remember.

    ccs-gui                     open the window
    ccs-gui --install-shortcut  put "Claude session sync" on the Desktop + Start Menu
    ccs-gui --remove-shortcut   take them away again

Installed as a GUI script (pyproject's [project.gui-scripts]), so the launcher
runs under pythonw and no console window ever appears - the console-script
equivalent would flash one on every double-click.

Deliberately a THIN SHELL over the library, not a reimplementation: it calls
plan_sync() and run_sync() exactly as the CLI does, so every refusal, guard, and
safety property (RULING 4's running-app guard, RULING 5's --live certification,
RULING 6's helper exclusion, tombstone skipping, dry-run-then-apply) behaves
identically here. It adds no path of its own into the store.

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
        root.title("Claude session sync")
        root.geometry("880x580")
        root.minsize(700, 460)

        outer = ttk.Frame(root, padding=PAD)
        outer.pack(fill="both", expand=True)

        self.status = tk.StringVar(value="Planning...")
        ttk.Label(outer, textvariable=self.status, font=("Segoe UI", 11, "bold"),
                  wraplength=840, justify="left").pack(anchor="w")

        self.detail = tk.StringVar(value="")
        ttk.Label(outer, textvariable=self.detail, wraplength=840, justify="left",
                  foreground="#555").pack(anchor="w", pady=(4, 6))

        # Title filter -> sync's --only. Deliberately the SAME flag the CLI uses
        # rather than per-row checkboxes: checkboxes would mean assembling a
        # subset here and handing plan_sync a selection it did not make, i.e. a
        # second route into the store. This stays one route.
        filt = ttk.Frame(outer)
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

        body = ttk.Frame(outer)
        body.pack(fill="both", expand=True)
        self.text = tk.Text(body, wrap="none", height=18, font=("Consolas", 9),
                            state="disabled", borderwidth=1, relief="solid")
        sb = ttk.Scrollbar(body, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        self.text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        bar = ttk.Frame(outer)
        bar.pack(fill="x", pady=(PAD, 0))
        self.apply_btn = ttk.Button(bar, text="Apply", command=self.on_apply,
                                    state="disabled")
        self.apply_btn.pack(side="right")
        self.undo_btn = ttk.Button(bar, text="Undo last copy", command=self.on_undo)
        self.undo_target = None          # (op_id, rows_written, destination label)
        self.refresh_btn = ttk.Button(bar, text="Refresh", command=self.refresh)
        self.refresh_btn.pack(side="right", padx=(0, 6))
        ttk.Button(bar, text="Close", command=root.destroy).pack(side="left")
        self.doctor_btn = ttk.Button(bar, text="Health check", command=self.on_doctor)
        self.doctor_btn.pack(side="left", padx=(6, 0))
        self.trust_var = tk.BooleanVar(value=ccs.signed_helper_trust_enabled(self.env))
        self.trust_chk = ttk.Checkbutton(
            bar, text="Let Chrome stay open", variable=self.trust_var,
            command=self.on_toggle_trust)
        self.trust_chk.pack(side="left", padx=(12, 0))
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

        self.refresh()

    # ---------------------------------------------------------------- helpers
    def show(self, lines):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", "\n".join(lines))
        self.text.configure(state="disabled")

    def busy(self, on):
        """Disable EVERY action while a worker runs - not a selected few.

        Each control left live is a competing worker: mutation locks produce
        spurious refusals, an unlocked plan can read a half-undone destination,
        and callbacks overwrite each other's UI state. The health check was the
        sharpest case - it clears self.manifest, so finishing mid-copy made the
        apply callback fault on a manifest that had become None *after the rows
        were already written*.
        """
        state = "disabled" if on else "normal"
        for w in (self.refresh_btn, self.undo_btn, self.doctor_btn,
                  self.only_entry, self.filter_btn, self.clear_btn,
                  self.trust_chk, self.update_chk, self.newer_chk,
                  self.orphan_chk, self.live_btn, self.forget_btn):
            w.configure(state=state)
        # newer_chk qualifies update_chk, so re-releasing everything must not
        # leave it live while the box it qualifies is unticked - it would read
        # as a control that does nothing.
        if not on and not self.update_var.get():
            self.newer_chk.configure(state="disabled")
            self.orphan_chk.configure(state="disabled")

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
        self._sync_undo_button()
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
            self.apply_btn.configure(state="normal")
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
        self.busy(True)
        self.apply_btn.configure(state="disabled")
        self.status.set("Checking...")
        self.detail.set("")
        threading.Thread(target=self._doctor_worker, daemon=True).start()

    def _doctor_worker(self):
        try:
            rep = ccs.gather_doctor(self.env)
            self.root.after(0, self._doctor_done, rep, None)
        except Exception:
            self.root.after(0, self._doctor_done, None, traceback.format_exc())

    def _doctor_done(self, rep, err):
        self.busy(False)
        if err:
            self.status.set("Health check failed")
            self.detail.set("")
            self.show([err])
            return
        lines = self.doctor_lines(rep, self.env.home)
        blocking = lines and lines[0] == "NEEDS ATTENTION"
        self.status.set("Health check: needs attention" if blocking
                        else "Health check: nothing blocking a sync")
        self.detail.set("Read-only - this changed nothing. Press Refresh to plan a sync.")
        self.show(lines)
        self.manifest = None      # the text area no longer shows a plan

    # ------------------------------------------------------------------- undo
    def _find_undoable_sync(self):
        """(op_id, rows, dest) for the most recent completed op IF it is a sync.

        Deliberately only the MOST RECENT completed op `ccs undo` would pick -
        so the button and the CLI can never disagree about which operation "the
        last one" is. If that op is anything this window does not do (a move,
        a repoint, a new-row - all CLI-only), no undo is offered here, because
        quietly reaching past it to an older sync would undo something other
        than what the user last did.

        THE FILTER HAS TO MATCH `cmd_undo`'s CANDIDATE TUPLE, or this docstring
        is a lie. It read ("move", "sync") while cmd_undo grew "repoint" and
        then "new-row": with a completed new-row as the most recent operation,
        this skipped straight past it to an older sync and offered to undo THAT,
        while `ccs undo` would have reversed the new-row. "retitle" joined the
        tuple in 0.11.0 for the same reason, and "converge" in 0.12.0.
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
        if not ops or ops[-1].manifest.get("op_type") != "sync":
            return None
        m = ops[-1].manifest
        rows = sum(1 for r in m.get("rows", []) if r.get("written"))
        if not rows:
            return None
        return (m["op_id"], rows,
                m.get("dest_email") or (m.get("dest_account", "")[:8] + "…"),
                ccs._live_override_note(m))

    def _sync_undo_button(self):
        self.undo_target = self._find_undoable_sync()
        if self.undo_target:
            self.undo_btn.configure(text="Undo last copy ({0} session{1})".format(
                self.undo_target[1], "" if self.undo_target[1] == 1 else "s"))
            self.undo_btn.pack(side="left", padx=(6, 0))
        else:
            self.undo_btn.pack_forget()

    def on_undo(self):
        if not self.undo_target:
            return
        op_id, rows, dest, live_note = self.undo_target
        prompt = ("Remove the {0} listing row{1} copied into {2}?\n\nThis deletes only "
                  "rows this tool wrote, and only while they still match what was "
                  "written - if that account has since opened one, it refuses rather "
                  "than discard the change. Conversations are never touched.".format(
                      rows, "" if rows == 1 else "s", dest))
        if live_note:
            # RULING 5: every route that can mutate under a --live certification
            # discloses it BEFORE mutating. The CLI prints this; a generic
            # confirmation here would hide the premise the deletion rests on.
            prompt += "\n\n" + live_note
        if not messagebox.askokcancel("Undo the last copy?", prompt):
            return
        self.busy(True)
        self.apply_btn.configure(state="disabled")
        self.undo_btn.configure(state="disabled")
        self.status.set("Undoing...")
        threading.Thread(target=self._undo_worker, args=(op_id,), daemon=True).start()

    def _undo_worker(self, op_id):
        try:
            # Re-check that this is STILL the latest eligible operation, not just
            # that it exists. Another CLI move or sync can complete between the
            # button being drawn and the confirmation being accepted; undoing the
            # captured id then reaches behind a newer operation and disagrees with
            # what `ccs undo` would pick.
            current = self._find_undoable_sync()
            if not current or current[0] != op_id:
                raise ccs.Refusal(
                    "another operation completed since this window last looked, so "
                    "{0} is no longer the most recent one to undo. Nothing was "
                    "touched - press Refresh to see the current state.".format(op_id))
            ops = [o for o in ccs.list_ops(self.env)
                   if o.manifest.get("op_id") == op_id]
            if not ops:
                raise ccs.Refusal("operation {0} is no longer in the journal".format(op_id))
            result = ccs.undo_sync(self.env, ops[0])
            self.root.after(0, self._undo_done, result, None)
        except ccs.Refusal as exc:
            self.root.after(0, self._undo_done, None, ("refusal", str(exc)))
        except Exception:
            self.root.after(0, self._undo_done, None, ("error", traceback.format_exc()))

    def _undo_done(self, result, problem):
        self.busy(False)
        self.undo_btn.configure(state="normal")
        if problem:
            kind, msg = problem
            # NOT "nothing was removed": _sync_unlink_all attempts every unlink
            # and only raises after collecting failures, so a refusal can follow
            # some rows having already been deleted. Claiming otherwise could
            # leave a half-undone destination looking untouched.
            self.status.set("Undo did not complete" if kind == "refusal"
                            else "Something went wrong")
            self.detail.set(
                "The tool's own explanation is below. Press Refresh to see the "
                "destination's current state before deciding what to do - a refusal "
                "that names specific rows may have removed others first."
                if kind == "refusal" else "")
            self.show([msg])
            messagebox.showwarning("Undo did not complete", msg)   # see _apply_done
            self._sync_undo_button()
            self._sync_live_button()
            return
        self.status.set("Undone - the copied rows were removed")
        self.detail.set("The other account's sidebar is back to how it was. "
                        "Press Refresh to plan again.")
        self.show([])
        self._sync_undo_button()
        self._sync_live_button()

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

    # ---------------------------------------------------------------- applying
    def on_apply(self):
        if not self.manifest:
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
        self.apply_btn.configure(state="disabled")
        self.status.set("Copying...")
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
        self._sync_undo_button()
        self._sync_live_button()


# ------------------------------------------------------------------- shortcuts

SHORTCUT_NAME = "Claude session sync.lnk"


def _psq(s):
    """A PowerShell single-quoted literal. Backslashes are literal inside one,
    which is exactly what a Windows path needs - Python's repr is NOT a
    substitute, since it escapes for Python and PowerShell then takes the
    doubled backslashes literally."""
    return "'" + s.replace("'", "''") + "'"


def _shortcut_paths():
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    start = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows",
                         "Start Menu", "Programs")
    return [os.path.join(d, SHORTCUT_NAME) for d in (desktop, start)
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
              "$s.Description = 'Copy Claude sessions to your other account';"
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
        print('\nDouble-click "Claude session sync" to plan a sync. Nothing is '
              "written until you press Apply.")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="ccs-gui", description="Windowed front end for claude-code-sessions sync.")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--install-shortcut", action="store_true",
                   help="add Desktop + Start Menu shortcuts (Windows)")
    g.add_argument("--remove-shortcut", action="store_true",
                   help="remove those shortcuts")
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
        messagebox.showerror("Claude session sync", traceback.format_exc())
        return 1
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
