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
                  foreground="#555").pack(anchor="w", pady=(4, PAD))

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
        self.forget_btn = ttk.Button(bar, text="Change destination",
                                     command=self.forget_destination)
        if self.dest_choice:
            self.forget_btn.pack(side="left", padx=(6, 0))

        self.refresh()

    # ---------------------------------------------------------------- helpers
    def show(self, lines):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", "\n".join(lines))
        self.text.configure(state="disabled")

    def busy(self, on):
        # Undo is included: leaving it live during a plan or a copy let a click
        # launch a competing worker - mutation locks then produce spurious
        # refusals, an unlocked plan can read a half-undone destination, and the
        # two callbacks overwrite each other's status text.
        state = "disabled" if on else "normal"
        self.refresh_btn.configure(state=state)
        self.undo_btn.configure(state=state)

    # ---------------------------------------------------------------- planning
    def refresh(self, keep_live=False):
        # An explicit Refresh always re-asks which account is live. A --live
        # assertion is a statement about RIGHT NOW, not a setting: keeping it
        # across a deliberate re-look would let a stale answer survive an
        # account switch, which is the very failure --live exists to prevent.
        # (Only the picker re-plans with keep_live=True, immediately after
        # being told.)
        if not keep_live:
            self.live_choice = ""
        self.busy(True)
        self.apply_btn.configure(state="disabled")
        self.status.set("Planning...")
        self.detail.set("")
        self.show([])
        threading.Thread(target=self._plan_worker, daemon=True).start()

    def _plan_worker(self):
        try:
            flags = ccs.SyncFlags(to=self.dest_choice, live=self.live_choice)
            manifest = ccs.plan_sync(self.env, flags)
            self.root.after(0, self._plan_done, manifest, None)
        except ccs.Refusal as exc:
            self.root.after(0, self._plan_done, None, ("refusal", str(exc)))
        except Exception:
            self.root.after(0, self._plan_done, None, ("error", traceback.format_exc()))

    def _plan_done(self, manifest, problem):
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
                           ("resurrected", "!! RESURRECTED (deletion overridden)")):
            val = tally.get(key)
            count = len(val) if isinstance(val, (list, tuple, set)) else val
            if count:
                lines.append("{0:<38}: {1}".format(label, count))
        lines += ["{0:<38}: {1}".format("to copy", len(rows)), ""]
        for r in rows:
            lines.append("   " + (r.get("title") or r.get("session_id", ""))[:90])

        if manifest.get("live_override"):
            lines = ["!! --live certification in effect", ""] + lines

        self.show(lines)
        # Offered on every plan, not only right after an apply: "I synced
        # yesterday and want it back" is the same need, and the CLI was the
        # only answer to it before.
        self._sync_undo_button()
        if rows:
            self.status.set("{0} session{1} ready to copy".format(
                len(rows), "" if len(rows) == 1 else "s"))
            self.detail.set("Nothing is written until you press Apply. The Claude "
                            "desktop app must be closed for that step.")
            self.apply_btn.configure(state="normal")
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
        out = []
        for line in msg.splitlines():
            stripped = line.strip()
            parts = stripped.split()
            if len(parts) >= 2 and pat.match(parts[0]):
                out.append((parts[0].split("/")[1], stripped))
        return out

    def _offer_destination_picker(self, msg):
        cands = self._candidates(msg)
        if not cands:
            return                      # unrecognised shape: leave the raw refusal
        win = tk.Toplevel(self.root)
        win.title("Choose a destination")
        win.transient(self.root)
        ttk.Label(win, padding=PAD, justify="left",
                  text="More than one other account store on this machine.\n"
                       "Pick the one whose sidebar should get these sessions.\n"
                       "A store with no listing rows holds no sessions yet.").pack(
                           anchor="w")
        for token, line in cands:
            def pick(t=token):
                self.dest_choice = t
                save_pref(t)
                win.destroy()
                self.refresh()
            ttk.Button(win, text=line, command=pick).pack(fill="x", padx=PAD, pady=2)
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
                       "writes the store you are actually using.\n\nThis is not "
                       "remembered - it is asked again every time.").pack(anchor="w")
        for a, o, p in stores:
            rows = ccs._listing_row_count(p)
            count = ("{0} rows".format(rows) if rows
                     else "no listing rows" if rows == 0 else "row count unreadable")

            def pick(path=p):
                self.live_choice = path      # a full path matches exactly one store
                win.destroy()
                self.refresh(keep_live=True)
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

    # ------------------------------------------------------------------- undo
    def _find_undoable_sync(self):
        """(op_id, rows, dest) for the most recent completed op IF it is a sync.

        Deliberately only the MOST RECENT completed move/sync, mirroring what
        `ccs undo` would pick - so the button and the CLI can never disagree
        about which operation "the last one" is. If that op is a move (done
        from the CLI), no undo is offered here: this window does not do moves,
        and quietly reaching past it to an older sync would undo something
        other than what the user last did.
        """
        try:
            ops = [o for o in ccs.list_ops(self.env)
                   if o.manifest.get("status") == "completed"
                   and o.manifest.get("op_type", "move") in ("move", "sync")]
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
            self._sync_undo_button()
            return
        self.status.set("Undone - the copied rows were removed")
        self.detail.set("The other account's sidebar is back to how it was. "
                        "Press Refresh to plan again.")
        self.show([])
        self._sync_undo_button()

    def forget_destination(self):
        self.dest_choice = ""
        save_pref("")
        self.refresh()

    # ---------------------------------------------------------------- applying
    def on_apply(self):
        if not self.manifest:
            return
        n = len(self.manifest.get("rows") or [])
        dst = self.manifest.get("dest_email") or self.manifest["dest_account"][:8]
        if not messagebox.askokcancel(
                "Copy sessions?",
                "Copy {0} session{1} into {2}?\n\nThis adds listing rows to that "
                "account's sidebar. It never deletes anything, and `ccs undo` "
                "reverses it.".format(n, "" if n == 1 else "s", dst)):
            return
        self.busy(True)
        self.apply_btn.configure(state="disabled")
        self.status.set("Copying...")
        threading.Thread(target=self._apply_worker, daemon=True).start()

    def _apply_worker(self):
        try:
            result = ccs.run_sync(self.env, self.manifest)
            self.root.after(0, self._apply_done, result, None)
        except ccs.Refusal as exc:
            self.root.after(0, self._apply_done, None, ("refusal", str(exc)))
        except Exception:
            self.root.after(0, self._apply_done, None, ("error", traceback.format_exc()))

    def _apply_done(self, result, problem):
        self.busy(False)
        if problem:
            kind, msg = problem
            self.status.set("Refused - nothing was copied" if kind == "refusal"
                            else "Something went wrong")
            self.detail.set("The tool's own explanation:" if kind == "refusal" else "")
            self.show([msg])
            # A refusal here is nearly always "the desktop app is running", which
            # is fixable in seconds - so leave Apply reachable after a Refresh.
            self.apply_btn.configure(state="disabled")
            return
        written = sum(1 for r in (self.manifest.get("rows") or []) if r.get("written"))
        self.live_choice = ""            # an assertion covers one run, not a session
        self.status.set("Copied {0} session{1}".format(
            written, "" if written == 1 else "s"))
        self.detail.set("Sign into the other account (or restart the app) to see them. "
                        "Changed your mind? Undo is the button below - a GUI should not "
                        "send you to a terminal to reverse what it just did.")
        self.manifest = None
        self._sync_undo_button()


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
