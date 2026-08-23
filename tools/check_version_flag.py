"""`--version` - one source of truth, and a path you can paste.

Two things are pinned here, and both are regressions that have already happened
in this repo in another form:

  1. The path must survive a NARROW terminal unwrapped. argparse's built-in
     version action formats through HelpFormatter and broke it mid-word, which
     is the same uncopyable output 0.9.11 and 0.9.12 were spent removing from
     `doctor`.
  2. pyproject must read the version FROM the module, so `--version` reports the
     code that is running rather than a number some other file declared.
"""
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import claude_code_sessions as ccs  # noqa: E402

ok = []


def check(name, cond, extra=""):
    print("%s %s%s" % ("OK " if cond else "BAD", name, ("  " + extra) if extra else ""))
    ok.append(bool(cond))


MODULE = os.path.join(REPO, "claude_code_sessions.py")


def run(cols=None):
    env = dict(os.environ)
    if cols:
        env["COLUMNS"] = str(cols)
    p = subprocess.run([sys.executable, MODULE, "--version"],
                       capture_output=True, text=True, env=env)
    return p.returncode, p.stdout


rc, out = run()
lines = [l for l in out.splitlines() if l.strip()]
check("--version exits 0 with no subcommand", rc == 0, "rc=%d" % rc)
check("  and prints exactly two lines", len(lines) == 2, repr(lines))
check("  naming the tool and the running version",
      lines and lines[0] == "claude-code-sessions " + ccs.__version__, repr(lines[:1]))
check("  and where it ran from",
      len(lines) > 1 and lines[1] == "running from: " + MODULE, repr(lines[1:2]))

# the regression this action exists for: HelpFormatter re-wrapping the path
rc_n, out_n = run(cols=40)
check("a 40-column terminal does not wrap the path", out_n == out,
      repr(out_n))
check("  the path is still one unbroken line",
      any(l.endswith("claude_code_sessions.py") for l in out_n.splitlines()),
      repr(out_n.splitlines()[-1:]))
check("  and still absolute, so it can be pasted back",
      os.path.isabs(out_n.splitlines()[-1].split("running from: ", 1)[-1]))

# one source of truth: pyproject must not declare a version of its own
proj = open(os.path.join(REPO, "pyproject.toml"), encoding="utf-8").read()
check("pyproject declares the version dynamic, not literal",
      'dynamic = ["version"]' in proj and
      not re.search(r'(?m)^version\s*=\s*"', proj))
check("  and reads it from the module attribute",
      'attr = "claude_code_sessions.__version__"' in proj)
check("__version__ looks like a release number",
      re.match(r"^\d+\.\d+\.\d+$", ccs.__version__) is not None, ccs.__version__)

# the flag must not have cost the required-subcommand behaviour
p = subprocess.run([sys.executable, MODULE], capture_output=True, text=True)
check("a bare invocation still demands a subcommand", p.returncode != 0)
check("  and lists --version in its usage", "--version" in p.stderr, p.stderr[:80])

print()
print("ALL PASS" if all(ok) else "SOME CHECKS FAILED")
sys.exit(0 if all(ok) else 1)
