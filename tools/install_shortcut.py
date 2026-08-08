"""Create a Desktop + Start Menu shortcut for the sync GUI. Run once.

    python tools/install_shortcut.py            # create
    python tools/install_shortcut.py --remove   # delete

Uses pythonw.exe so no console window ever flashes, and resolves that
interpreter from the running one rather than PATH - a machine with several
Pythons must get the SAME one that can import claude_code_sessions.

Windows only, stdlib only (drives WScript.Shell through PowerShell; there is no
stdlib .lnk writer).
"""

import argparse
import os
import subprocess
import sys

NAME = "Claude session sync.lnk"


def targets():
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    start = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows",
                         "Start Menu", "Programs")
    return [os.path.join(d, NAME) for d in (desktop, start) if os.path.isdir(d)]


def psq(s):
    """A PowerShell single-quoted literal. Backslashes are literal inside one,
    which is exactly what a Windows path needs."""
    return "'" + s.replace("'", "''") + "'"


def pythonw():
    """The windowed twin of the interpreter running THIS script."""
    exe = sys.executable or ""
    cand = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return cand if os.path.isfile(cand) else exe


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--remove", action="store_true")
    args = ap.parse_args(argv[1:])

    if os.name != "nt":
        print("Windows only - the GUI's guards and store layout are Windows-verified.")
        return 2

    made = []
    for link in targets():
        if args.remove:
            try:
                os.remove(link)
                made.append("removed " + link)
            except OSError:
                pass
            continue
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sync_gui.pyw")
        if not os.path.isfile(script):
            print("missing:", script)
            return 2
        # NOT Python repr: repr escapes backslashes for PYTHON, and a PowerShell
        # single-quoted string takes those literally, producing C:\\Users\\...
        # in the saved shortcut. Quote the PowerShell way instead - wrap in
        # single quotes and double any embedded single quote.
        ps = (
            "$s = (New-Object -ComObject WScript.Shell).CreateShortcut({0});"
            "$s.TargetPath = {1};"
            "$s.Arguments = '\"' + {2} + '\"';"
            "$s.WorkingDirectory = {3};"
            "$s.IconLocation = {1};"
            "$s.Description = 'Copy Claude sessions to your other account';"
            "$s.Save()"
        ).format(psq(link), psq(pythonw()), psq(script),
                 psq(os.path.dirname(script)))
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                            "-Command", ps], capture_output=True, text=True)
        if r.returncode != 0:
            print("failed:", link, r.stderr.strip())
            return 1
        made.append("created " + link)

    for line in made:
        print(line)
    if not args.remove and made:
        print("\nDouble-click 'Claude session sync' to plan a sync. Nothing is "
              "written until you press Apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
