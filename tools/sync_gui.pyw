"""Double-clickable front end for `ccs sync` - no terminal, nothing to remember.

Deliberately a THIN SHELL over the library, not a reimplementation: it calls
plan_sync() and run_sync() exactly as the CLI does, so every refusal, guard, and
safety property (RULING 4's running-app guard, RULING 5's --live certification,
RULING 6's helper exclusion, tombstone skipping, dry-run-then-apply) behaves
identically here. The GUI adds no path of its own into the store.

Two rules it holds to:
  - Nothing is written until you press Apply. Opening the window plans only.
  - A refusal is shown verbatim, never summarised into something friendlier.
    The refusals in this tool carry the reason and the fix, and softening them
    would be the one place a GUI could do real harm.

Saved as .pyw so Windows runs it without a console window. Create a shortcut
with tools/install_shortcut.py.
"""

import os
import sys
import threading
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk
from tkinter import ttk, messagebox

import claude_code_sessions as ccs

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
        state = "disabled" if on else "normal"
        self.refresh_btn.configure(state=state)

    # ---------------------------------------------------------------- planning
    def refresh(self):
        self.busy(True)
        self.apply_btn.configure(state="disabled")
        self.status.set("Planning...")
        self.detail.set("")
        self.show([])
        threading.Thread(target=self._plan_worker, daemon=True).start()

    def _plan_worker(self):
        try:
            flags = ccs.SyncFlags(to=self.dest_choice)
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

        so a line qualifies when its first token is 'a/b'. The row count stays in
        the button text: it is what distinguishes the real store from the empty
        directory the desktop app scaffolds, which is the whole reason that count
        was added to the refusal.
        """
        out = []
        for line in msg.splitlines():
            stripped = line.strip()
            parts = stripped.split()
            if len(parts) >= 2 and parts[0].count("/") == 1:
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
        self.status.set("Copied {0} session{1}".format(
            written, "" if written == 1 else "s"))
        self.detail.set("Sign into the other account (or restart the app) to see them. "
                        "`ccs undo --apply` reverses this.")
        self.manifest = None


def main():
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
