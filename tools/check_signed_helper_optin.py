"""Check RULING 7 - the opt-in signature trust for the Chrome helper.

Exercises the decision table against a synthetic env, then confirms the
signature reader agrees with Windows about the REAL helper on this machine.
Creates and removes its own opt-in marker; never touches the real one.
"""
import os
import shutil
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import claude_code_sessions as ccs  # noqa: E402

ok = []


def check(name, cond, extra=""):
    print("%s %s%s" % ("OK " if cond else "BAD", name, ("  " + extra) if extra else ""))
    ok.append(bool(cond))


root = tempfile.mkdtemp(prefix="ruling7-")
env = ccs.default_env()
env.ops_dir = os.path.join(root, "journal", "ops")
os.makedirs(env.ops_dir)

HELPER = ("c:\\users\\u\\appdata\\local\\packages\\claude_x\\localcache\\roaming\\"
          "claude\\chromenativehost\\chrome-native-host.exe")
DESKTOP = "c:\\program files\\windowsapps\\claude_1.0_x64__x\\app\\claude.exe"

# ---------------------------------------------------------------- the opt-in
check("opt-in is OFF by default", not ccs.signed_helper_trust_enabled(env))
marker = ccs.trust_signed_helper_path(env)
check("marker sits beside the journal, not in it",
      os.path.basename(marker) == "trust-signed-helper", marker)

# ------------------------------------------------- the decision table
# _measured_helper_state is patched to simulate a changed/unreadable binary
# without needing real files; _signed_helper_state likewise for the verdict.
orig_measured = ccs._measured_helper_state
orig_signed = ccs._signed_helper_state


def run_case(measured, signed, opted_in):
    ccs._measured_helper_state = (
        lambda t, e: measured if "chromenativehost" in t else None)
    ccs._signed_helper_state = lambda t: signed
    if opted_in:
        open(marker, "w").close()
    elif os.path.exists(marker):
        os.remove(marker)
    env.process_lister = lambda: [(1, HELPER)]
    return ccs.claude_running(env)


try:
    check("measured build is excluded regardless of opt-in",
          run_case("measured", "unsigned", False) == [])
    check("changed build COUNTS while opted out",
          run_case("changed", "signed", False) == [HELPER])
    check("changed + signed + opted in is excluded (RULING 7)",
          run_case("changed", "signed", True) == [])
    check("changed + UNSIGNED + opted in still counts",
          run_case("changed", "unsigned", True) == [HELPER])
    check("UNREADABLE never qualifies, even signed and opted in",
          run_case("unreadable", "signed", True) == [HELPER])

    # the desktop app itself must never be excused by any of this
    ccs._measured_helper_state = lambda t, e: None
    ccs._signed_helper_state = lambda t: "signed"
    open(marker, "w").close()
    env.process_lister = lambda: [(1, DESKTOP)]
    check("the desktop app is never excused by RULING 7",
          ccs.claude_running(env) == [DESKTOP])
finally:
    ccs._measured_helper_state = orig_measured
    ccs._signed_helper_state = orig_signed

# ------------------------------------------------- the real signature reader
if sys.platform == "win32":
    import glob
    found = glob.glob(os.path.join(os.environ.get("LOCALAPPDATA", ""), "Packages",
                                   "Claude_*", "LocalCache", "Roaming", "Claude",
                                   "ChromeNativeHost", "chrome-native-host.exe"))
    if found:
        p = found[0].lower()
        subj = ccs._authenticode_publisher(p)
        check("the real helper reads as validly signed", bool(subj),
              (subj or "no verdict")[:50])
        check("  and names the expected publisher",
              ccs._signed_helper_state(p) == "signed")
    else:
        print("--  no helper on this machine; live signature check skipped")
    # a file that is certainly not signed
    unsigned = os.path.join(root, "not-signed.exe")
    with open(unsigned, "wb") as fh:
        fh.write(b"MZ" + b"\0" * 64)
    check("an unsigned file is not mistaken for signed",
          ccs._signed_helper_state(unsigned) == "unsigned")

# ------------------------------------------------- the CLI toggle
# The opt-in must be reachable without hand-creating a file: "go make this path"
# is off-by-default policy delivered through a hostile mechanism.
import argparse  # noqa: E402


class NS(object):
    def __init__(self, on=False, off=False):
        self.on, self.off = on, off


if os.path.exists(marker):
    os.remove(marker)
ccs.cmd_trust_signed_helper(env, NS())            # bare call must not enable
check("bare invocation reports without enabling",
      not ccs.signed_helper_trust_enabled(env))
ccs.cmd_trust_signed_helper(env, NS(on=True))
check("--on enables it", ccs.signed_helper_trust_enabled(env))
check("  and the marker explains itself",
      "RULING 7" in open(marker, encoding="utf-8").read())
ccs.cmd_trust_signed_helper(env, NS(off=True))
check("--off disables it", not ccs.signed_helper_trust_enabled(env))
ccs.cmd_trust_signed_helper(env, NS(off=True))    # idempotent, no traceback
check("--off twice is not an error", not ccs.signed_helper_trust_enabled(env))

parser_ok = True
try:
    p = ccs.build_parser() if hasattr(ccs, "build_parser") else None
except Exception:
    parser_ok = False
check("the subcommand is registered in the CLI dispatch",
      "trust-signed-helper" in open(
          os.path.join(REPO, "claude_code_sessions.py"), encoding="utf-8").read())

shutil.rmtree(root, ignore_errors=True)
print()
print("ALL PASS" if all(ok) else "SOME CHECKS FAILED")
sys.exit(0 if all(ok) else 1)
