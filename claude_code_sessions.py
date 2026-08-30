"""claude-code-sessions: inspect and relocate Claude Code sessions on disk.

Unofficial. Fails closed: verifies the on-disk layout against evidence and
refuses to mutate anything it cannot positively verify.

Sections (in order):
  1. Env, exceptions, constants        5. Transaction engine (move/undo/recover)
  2. Helpers (hashing, atomic IO)      6. Commands (list/doctor/move/undo/recover)
  3. Platform & store discovery,       7. CLI wiring
     rows, encoding detection          8. Sync (cross-account)
  4. Transcript location
"""
from __future__ import annotations

import dataclasses
import os
import sys

# The single source of truth for the version. pyproject reads it from here
# (setuptools dynamic attr), rather than the two declaring it separately - so
# `--version` always reports the code that is actually running, which is the
# only answer worth printing. A hardcoded duplicate in pyproject could disagree
# with the module after a partial bump, and the disagreement would surface as a
# user reporting a bug against a version they were not running.
__version__ = "0.13.0"

SCHEME_CURRENT = r"[^A-Za-z0-9]"    # app >= ~2026-07-12: underscores also become '-'
SCHEME_LEGACY = r"[^A-Za-z0-9_]"    # before: underscores survived

NONTERMINAL = ("journaled", "copying", "copied", "rewriting", "committed", "aborting",
               "writing")
TERMINAL = ("completed", "rolled_back", "undone")


class Refusal(Exception):
    exit_code = 1


class LayoutError(Exception):
    exit_code = 2


@dataclasses.dataclass
class Env:
    home: str
    projects_root: str
    store_candidates: list
    ops_dir: str
    moved_log: str
    is_windows: bool
    process_lister: object
    now: object


def default_env():
    home = os.path.expanduser("~")
    if sys.platform == "win32":
        candidates = sorted(
            __import__("glob").glob(os.path.join(os.environ.get("LOCALAPPDATA", ""),
                "Packages", "Claude_*", "LocalCache", "Roaming", "Claude", "claude-code-sessions"))
        ) + [os.path.join(os.environ.get("APPDATA", ""), "Claude", "claude-code-sessions")]
    elif sys.platform == "darwin":
        candidates = [os.path.join(home, "Library", "Application Support", "Claude", "claude-code-sessions")]
    else:
        candidates = [os.path.join(home, ".config", "Claude", "claude-code-sessions")]
    import time
    return Env(
        home=home,
        projects_root=os.path.join(home, ".claude", "projects"),
        store_candidates=candidates,
        ops_dir=os.path.join(home, ".claude-code-journal", "ops"),
        moved_log=os.path.join(home, ".claude-code-journal", "moved-log.jsonl"),
        is_windows=(sys.platform == "win32"),
        process_lister=_default_process_lister,
        now=time.time,
    )


# Fail-closed sentinel: returned (with pid -1) whenever the process list
# cannot be obtained. Contains "claude" so every guard's substring match
# treats it as a possibly-running desktop app, and no CLI marker so the
# narrowing never excuses it. "Couldn't look" is never "nothing there".
_PROC_UNAVAILABLE = ("(process listing unavailable - treating the claude "
                     "desktop app as possibly running)")


def _parse_proc_lines(out):
    """(pid, text) tuples from 'pid|name|path' lines (one process per line).

    text is the lowercased executable path when the process reports one,
    else the lowercased image name. Malformed lines are skipped - this
    parses our own PowerShell command's output, so anything unexpected is
    noise, not data.
    """
    result = []
    for line in out.splitlines():
        parts = line.strip().split("|", 2)
        if len(parts) != 3:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        text = (parts[2].strip() or parts[1].strip()).lower()
        if text:
            result.append((pid, text))
    return result


def _default_process_lister():
    """Running processes as (pid, text) tuples, text lowercased.

    On Windows, text is the full executable path when CIM can supply it
    (needed because BOTH the desktop app and the Claude Code CLI are now
    image name claude.exe - measured 2026-08-02: the MSIX desktop at
    ...\\WindowsApps\\Claude_...\\app\\Claude.exe and the CLI, a native
    binary since ~2.x, at ...\\AppData\\Roaming\\Claude\\claude-code\\...\\
    claude.exe. The old docstring's claim that a node-hosted CLI was
    invisible to tasklist is obsolete). If PowerShell/CIM yields nothing
    usable, fall back to name-only tasklist output - callers treat an
    unclassifiable claude-named entry as the desktop app (fail closed).
    Total enumeration failure returns the _PROC_UNAVAILABLE sentinel, never
    [] - "couldn't look" is never "nothing there". POSIX uses
    `ps ... args=` unchanged.
    """
    import subprocess
    try:
        if sys.platform == "win32":
            try:
                proc = subprocess.run(
                    ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                     "Get-CimInstance Win32_Process | ForEach-Object "
                     "{ '{0}|{1}|{2}' -f $_.ProcessId, $_.Name, $_.ExecutablePath }"],
                    capture_output=True, text=True, timeout=15)
                if proc.returncode == 0:
                    parsed = _parse_proc_lines(proc.stdout)
                    if parsed:
                        return parsed
                # empty or all-garbage CIM output falls through to tasklist
            except (OSError, subprocess.SubprocessError):
                pass
            proc = subprocess.run(["tasklist", "/FO", "CSV"], capture_output=True,
                                  text=True, timeout=15)
            if proc.returncode != 0:
                return [(-1, _PROC_UNAVAILABLE)]
            out = proc.stdout
            result = []
            for line in out.splitlines()[1:]:
                if not line.startswith('"'):
                    continue
                fields = line.split('","')
                if len(fields) < 2:
                    continue
                name = fields[0].strip('"').lower()
                try:
                    pid = int(fields[1].strip('"'))
                except ValueError:
                    continue
                result.append((pid, name))
            return result if result else [(-1, _PROC_UNAVAILABLE)]
        out = subprocess.run(["ps", "-A", "-o", "pid=,args="], capture_output=True,
                             text=True, timeout=15).stdout
        result = []
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            pid_s, _, rest = line.partition(" ")
            try:
                pid = int(pid_s)
            except ValueError:
                continue
            result.append((pid, rest.strip().lower()))
        return result if result else [(-1, _PROC_UNAVAILABLE)]
    except Exception:
        return [(-1, _PROC_UNAVAILABLE)]


# ---------------------------------------------------------------- 2. helpers
import base64
import hashlib
import json


def sha256_file(path):
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def fsync_file(path):
    # Windows FlushFileBuffers requires a write-capable handle; os.O_RDONLY fails with EBADF.
    fd = os.open(path, os.O_RDWR)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path, data):
    tmp = path + ".ct-tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        raise LayoutError("unreadable JSON at {0}: {1}".format(path, exc))


def b64(data):
    return base64.b64encode(data).decode("ascii")


def unb64(s):
    return base64.b64decode(s.encode("ascii"))


# ------------------------------------------------------- 3. encoding detection
import re


def encode(path, scheme):
    return re.sub(scheme, "-", path)


def scheme_evidence(cwds, projects_root):
    cur = leg = 0
    for cwd in set(c for c in cwds if c):
        a, b = encode(cwd, SCHEME_CURRENT), encode(cwd, SCHEME_LEGACY)
        if a == b:
            continue  # agreeing paths carry no signal
        cur += os.path.isdir(os.path.join(projects_root, a))
        leg += os.path.isdir(os.path.join(projects_root, b))
    return cur, leg


def choose_scheme(evidence, target_path):
    cur, leg = evidence
    if cur > leg:
        return SCHEME_CURRENT
    if leg > cur:
        return SCHEME_LEGACY
    # tie (including 0-0): only safe when the choice cannot matter for this target
    if encode(target_path, SCHEME_CURRENT) == encode(target_path, SCHEME_LEGACY):
        return SCHEME_CURRENT
    raise LayoutError(
        "cannot determine the path-encoding scheme (evidence current={0} legacy={1}) "
        "and the target '{2}' encodes differently under the two known schemes. "
        "Refusing to guess.".format(cur, leg, target_path))


# --------------------------------------------------------- store discovery
@dataclasses.dataclass
class StoreDiscovery:
    status: str          # found | absent | error
    roots: list
    detail: str


def discover_stores(env):
    # FileNotFoundError while looking is PROOF of absence (a machine that never
    # installed the desktop app has no %APPDATA%\Claude parent at all - that is
    # the normal CLI-only case, not an error). Any OTHER OSError means "couldn't
    # look", which is never "nothing there".
    roots, errors, seen = [], [], set()
    for cand in env.store_candidates:
        try:
            os.listdir(cand)                       # store exists and is enumerable
            real = os.path.realpath(cand)
            if real not in seen:
                seen.add(real)
                roots.append(real)
            continue
        except FileNotFoundError:
            pass                                   # candidate missing; prove the parent
        except OSError as exc:
            errors.append("{0}: {1}".format(cand, exc))
            continue
        parent = os.path.dirname(cand)
        try:
            os.listdir(parent)
        except FileNotFoundError:
            pass                                   # parent absent too: proven absent
        except OSError as exc:
            errors.append("{0}: {1}".format(cand, exc))
    if errors:
        return StoreDiscovery("error", roots, "; ".join(errors))
    if roots:
        return StoreDiscovery("found", roots, "{0} root(s)".format(len(roots)))
    return StoreDiscovery("absent", [], "no store under any known candidate")


# ----------------------------------------------------------- listing rows
import glob as _glob


@dataclasses.dataclass
class Row:
    path: str
    data: dict

    @property
    def local_id(self):
        return self.data.get("sessionId") or os.path.splitext(os.path.basename(self.path))[0]

    @property
    def cli_session_id(self):
        return self.data.get("cliSessionId") or ""

    @property
    def cwd(self):
        return self.data.get("cwd") or ""

    @property
    def last_activity(self):
        return self.data.get("lastActivityAt") or 0


def load_rows(roots):
    rows, errors = [], []
    for root in roots:
        for path in sorted(_glob.glob(os.path.join(root, "*", "*", "local_*.json"))):
            try:
                data = read_json(path)
            except LayoutError as exc:
                errors.append(str(exc))
                continue
            # I2: a row file whose top-level JSON is not an object (e.g. a
            # bare list) must not become a Row - every Row property assumes
            # dict.get() and would raise AttributeError, crashing
            # doctor/list/move instead of reporting a clean, fail-closed
            # error.
            if not isinstance(data, dict):
                errors.append("row is not a JSON object: {0}".format(path))
                continue
            rows.append(Row(path, data))
    return rows, errors


# ------------------------------------------------------ 4. transcript location
def find_transcripts(projects_root, session_id):
    hits = []
    try:
        for entry in sorted(os.listdir(projects_root)):
            cand = os.path.join(projects_root, entry, session_id + ".jsonl")
            if os.path.isfile(cand):
                hits.append(cand)
    except FileNotFoundError:
        pass
    return hits


def iter_transcripts(projects_root):
    out = []
    try:
        for entry in sorted(os.listdir(projects_root)):
            folder = os.path.join(projects_root, entry)
            if not os.path.isdir(folder):
                continue
            for name in sorted(os.listdir(folder)):
                if name.endswith(".jsonl"):
                    out.append((entry, os.path.join(folder, name)))
    except FileNotFoundError:
        pass
    return out


def _cwds_in(transcript_path):
    vals = []
    try:
        with open(transcript_path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if isinstance(obj, dict) and obj.get("cwd"):
                    vals.append(obj["cwd"])
    except OSError:
        pass
    return vals


def first_cwd(transcript_path):
    vals = _cwds_in(transcript_path)
    return vals[0] if vals else ""


def last_cwd(transcript_path):
    vals = _cwds_in(transcript_path)
    return vals[-1] if vals else ""


def sidecar_path(transcript_path):
    return transcript_path[:-len(".jsonl")]


# ---------------------------------------------- 5. transaction engine: journal
import time


@dataclasses.dataclass
class Op:
    op_dir: str
    manifest: dict
    now: object = time.time


def manifest_path(op):
    return os.path.join(op.op_dir, "manifest.json")


def save_manifest(op):
    atomic_write(manifest_path(op), json.dumps(op.manifest, indent=1).encode("utf-8"))


def new_op(env, manifest):
    op_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(env.now())) + "-" + os.urandom(3).hex()
    op_dir = os.path.join(env.ops_dir, op_id)
    os.makedirs(op_dir)
    manifest = dict(manifest)
    manifest["op_id"] = op_id
    manifest["status"] = "journaled"
    manifest["history"] = [{"status": "journaled", "at": env.now()}]
    op = Op(op_dir, manifest, env.now)
    save_manifest(op)
    return op


def set_status(op, status):
    op.manifest["status"] = status
    op.manifest["history"].append({"status": status, "at": op.now()})
    save_manifest(op)


def list_ops(env):
    out = []
    if not os.path.isdir(env.ops_dir):
        return out
    ops = []
    for name in os.listdir(env.ops_dir):
        mp = os.path.join(env.ops_dir, name, "manifest.json")
        if os.path.isfile(mp):
            m = read_json(mp)
            ops.append((m, Op(os.path.join(env.ops_dir, name), m)))
    # Sort by creation time (history[0]["at"]) then op_id for stability
    for m, op in sorted(ops, key=lambda x: (x[0].get("history", [{}])[0].get("at", 0), x[0]["op_id"])):
        out.append(op)
    return out


def nonterminal_ops(env):
    return [o for o in list_ops(env) if o.manifest.get("status") in NONTERMINAL]


def rotate_ops(env):
    import shutil
    ops = list_ops(env)
    terminal = [o for o in ops if o.manifest.get("status") in TERMINAL]
    # Paths a still-unfinished sync op could later try to reverse. A terminal op
    # that wrote one of them is the EVIDENCE _sync_paths_claimed_elsewhere needs
    # to stop that reversal clobbering its work, and pruning it would delete the
    # evidence while leaving the stalled op alive forever (nonterminal ops are
    # never rotated). Ten more finished ops and the protection would silently
    # expire - on exactly the "came back to a stuck op weeks later" case recover
    # exists for. So keep a claimant for as long as anything could collide with
    # it; this holds back only ops that actually share a destination row.
    # Retitle ops carry the same exposure since 0.11.0: their undo/back
    # ownership rule (_retitle_claimed_elsewhere) reads other ops' journals
    # for the same dest_path, so a terminal claimant is evidence there too.
    live = set()
    for o in ops:
        om = o.manifest
        if om.get("op_type") not in ("sync", "retitle") \
                or om.get("status") not in NONTERMINAL:
            continue
        for r in om.get("rows") or []:
            p = r.get("dest_path")
            if isinstance(p, str):
                live.add(os.path.normcase(os.path.abspath(p)))

    def _collides(op):
        om = op.manifest
        # A rolled-back op that left something on disk is the only durable
        # record that it did. Pruning it destroys the residue in the same call
        # that wrote it, and nothing afterwards looks for that row: doctor's
        # vanished-row check reads only 'completed' ops, deliberately. Hold it.
        if om.get("rollback_residue"):
            return True
        if not live or om.get("op_type") not in ("sync", "retitle"):
            return False
        if om.get("status") in ("undone", "rolled_back"):
            return False          # claim withdrawn; nothing left to protect
        for r in om.get("rows") or []:
            p = r.get("dest_path")
            if r.get("written") and isinstance(p, str) \
                    and os.path.normcase(os.path.abspath(p)) in live:
                return True
        return False

    pruned = []
    for op in terminal[:-10]:
        if _collides(op):
            continue
        try:
            shutil.rmtree(op.op_dir)
            pruned.append(op.manifest["op_id"])
        except OSError:
            pass
    return pruned


LOCK_NAME = "lock"


def _lock_path(env):
    return os.path.join(env.ops_dir, LOCK_NAME)


def acquire_lock(env, op_id):
    os.makedirs(env.ops_dir, exist_ok=True)
    try:
        fd = os.open(_lock_path(env), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        holder = read_lock(env)
        if holder:
            holder_str = "pid {0}, op {1}".format(*holder)
        else:
            holder_str = "unknown holder"
        raise Refusal("another claude-code-sessions operation holds the lock ({0}). "
                      "If it is dead, run: claude-code-sessions recover".format(holder_str))
    with os.fdopen(fd, "w") as fh:
        fh.write("{0} {1}".format(os.getpid(), op_id))
    return _lock_path(env)


def release_lock(env):
    try:
        os.unlink(_lock_path(env))
    except FileNotFoundError:
        pass


def read_lock(env):
    try:
        with open(_lock_path(env)) as fh:
            pid_s, _, op_id = fh.read().partition(" ")
        return int(pid_s), op_id
    except (OSError, ValueError):
        return None


def lock_is_stale(env):
    info = read_lock(env)
    if info is None:
        return False
    pid = info[0]
    try:
        os.kill(pid, 0)
        return False
    except OSError:
        return True


def append_moved_log(env, entry):
    os.makedirs(os.path.dirname(env.moved_log), exist_ok=True)
    with open(env.moved_log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def moved_session_ids(env):
    state = {}
    try:
        with open(env.moved_log, encoding="utf-8") as fh:
            for line in fh:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                state[e.get("session_id")] = e.get("kind")
    except FileNotFoundError:
        return set()
    except OSError as exc:
        raise LayoutError("cannot read moved-log at {0}: {1}".format(env.moved_log, exc))
    return {sid for sid, kind in state.items() if kind == "move"}


# ------------------------------------------- containment & sidecar inventory
import stat as _stat


def ensure_contained(path, allowed_roots):
    real = os.path.realpath(path)
    for root in allowed_roots:
        rreal = os.path.realpath(root)
        if real == rreal or real.startswith(rreal + os.sep):
            return real
    raise LayoutError("path {0} resolves outside every recognized root".format(path))


def _is_reparse(path):
    if os.path.islink(path):
        return True
    try:
        st = os.lstat(path)
        return bool(getattr(st, "st_file_attributes", 0) &
                    getattr(_stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    except OSError:
        return False


def sidecar_inventory(sidecar_dir):
    if not os.path.isdir(sidecar_dir):
        return []
    if _is_reparse(sidecar_dir):
        raise Refusal("sidecar dir {0} is a symlink/junction; refusing to "
                      "traverse".format(sidecar_dir))
    inv = []
    for dirpath, dirnames, filenames in os.walk(sidecar_dir):
        for name in dirnames + filenames:
            full = os.path.join(dirpath, name)
            if _is_reparse(full):
                raise Refusal("symlink/junction inside sidecar tree at {0}; refusing "
                              "to traverse".format(full))
        for name in filenames:
            full = os.path.join(dirpath, name)
            digest, size = sha256_file(full)
            rel = os.path.relpath(full, sidecar_dir).replace(os.sep, "/")
            inv.append({"rel": rel, "sha256": digest, "size": size})
    inv.sort(key=lambda e: e["rel"])
    return inv


# ------------------------------------------------------ move validation
@dataclasses.dataclass
class MoveFlags:
    transcript_only: bool = False
    row: list = ()
    yes: bool = False
    force: bool = False


# Our own console-script names. They contain "claude", so without this the
# process guard below would see this very tool and refuse to run.
OUR_COMMANDS = ("claude-code-sessions", "ccs")


def _is_cli_process(text):
    """True when TEXT (a lowercased lister entry) is the Claude Code CLI,
    which must NOT count as the desktop app.

    Recognised CLI locations (measured 2026-08-02):
      ...\\appdata\\roaming\\claude\\claude-code\\<ver>\\claude.exe  (versioned binary;
          also the backend the desktop spawns - harmless to exclude, because
          it only exists while the desktop's own MSIX processes are running
          and those still match)
      ...\\.local\\bin\\claude.exe / .../.local/bin/claude            (the PATH shim)
      any npm-style .../claude-code/... install

    Everything else claude-named - the MSIX desktop, a non-MSIX desktop
    install, or a bare image name the fallback lister could not resolve to
    a path - stays a match: the guard fails closed on ambiguity.
    Separators are normalised first so a forward-slash Windows path cannot
    dodge the backslash patterns. The markers are precise path SEGMENTS,
    not substrings: a bare 'claude-code' substring test would excuse a
    desktop app installed under an unlucky parent directory (say, a user
    account literally named claude-code), silently disabling the guard.

    POSIX `ps -A -o args=` reports the full command line, not just argv0, so
    a shimmed invocation carries trailing arguments (".../.local/bin/claude
    --resume x") and never matches an ENDS-WITH check. The shim marker is
    therefore also checked as a CONTAINS match when followed by a space -
    additive, every ENDS-WITH marker above still applies unchanged. A bare
    argv0 "claude" with no path is deliberately left unclassifiable: with no
    path segment to test, there is nothing to safely exclude, so it stays a
    match (fail-safe).
    """
    text = text.replace("/", "\\")
    return ("\\appdata\\roaming\\claude\\claude-code\\" in text  # measured CLI home
            or "\\@anthropic-ai\\claude-code\\" in text          # npm install layout
            or text.endswith("\\.local\\bin\\claude.exe")
            or text.endswith("\\.local\\bin\\claude")    # POSIX shim, post-normalise
            or "\\.local\\bin\\claude.exe " in text       # POSIX shim, argv w/ args
            or "\\.local\\bin\\claude " in text)


# RULING 6 (2026-08-07). The desktop app's Chrome-extension helper does not count
# as the desktop app - but ONLY the exact binary that was measured.
#
# Measured by tools/native-host-measurement/watch_ceremony.py: across a 4m23s
# helper-only window (desktop app confirmed gone, helper alive, extension round
# trips performed inside it) followed by a graceful Chrome-EOF shutdown (the
# helper closed itself, exit code 0), neither a continuous ReadDirectoryChangesW
# watch nor a sha256 snapshot diff across the window recorded any mutation of the
# store. Both instruments were needed: a pure mapped-section write produces no
# directory-change notification at all. See docs/internals.md, "The Chrome native
# host, and why it still counts" and its 2026-08-07 amendment - including the
# standing caveat that this is ONE clean trial.
#
# The binding is the anchored package-path segment chain AND the binary's hash,
# because the helper auto-updates in place: it changed under this very
# investigation (744187C7... -> 711AD7E7..., same path, same byte length). A
# path-only exclusion would therefore silently inherit trust for code nobody
# measured. On any mismatch - different hash, unreadable file, a path that is not
# the anchored chain - the helper COUNTS exactly as it did before, so the worst
# case is the previous behaviour and never worse.
_HELPER_SHA256 = "711ad7e7dec73aa58187479f5f99b13480df93ab1306bd171a61027d84fa81f1"
_HELPER_PKG_ANCHOR = "\\packages\\claude_"
_HELPER_DIR_ANCHOR = "\\localcache\\roaming\\claude\\chromenativehost\\"
_HELPER_EXE = "\\chrome-native-host.exe"


def _expected_helper_paths(env):
    """The helper's real location(s), derived from the discovered store roots.

    A store candidate is <...>\\Roaming\\Claude\\claude-code-sessions; the helper
    is its sibling <...>\\Roaming\\Claude\\ChromeNativeHost\\chrome-native-host.exe.

    Deriving this beats pattern-matching the path. An earlier version tested for
    the SEGMENTS "\\packages\\claude_" and "\\localcache\\roaming\\claude\\
    chromenativehost\\" anywhere in the string, which any fabricated path could
    satisfy - so with the opt-in enabled, some other Anthropic-signed binary
    copied to C:\\tmp\\packages\\claude_x\\localcache\\roaming\\claude\\
    chromenativehost\\chrome-native-host.exe would have been excused. The
    "out-of-path binaries still count" guarantee was not actually enforced.
    """
    out = set()
    for cand in getattr(env, "store_candidates", ()) or ():
        parent = os.path.dirname(cand)
        out.add(os.path.normcase(os.path.abspath(
            os.path.join(parent, "ChromeNativeHost", "chrome-native-host.exe"))))
    return out


def _looks_like_chrome_helper(text, env):
    """True when TEXT is the helper's own image path at a REAL store root.

    Exact match against _expected_helper_paths, not a substring or segment test.
    A lister entry carrying trailing arguments (POSIX `ps` reports whole command
    lines) will not equal a path and therefore falls through to counting, which
    is the safe direction - a process merely NAMING the helper must never
    inherit its exclusion.
    """
    if not text:
        return False
    # Separator folding is WINDOWS-ONLY. Doing it unconditionally turned a POSIX
    # path into '\tmp\...' - a relative path that abspath then resolved against
    # the cwd - so nothing ever matched and the Linux CI legs failed while
    # Windows passed. Forward slashes still have to be folded on Windows, where a
    # lister entry can legitimately carry them.
    if os.sep == "\\":
        text = text.replace("/", "\\")
    norm = os.path.normcase(os.path.abspath(text))
    return norm in _expected_helper_paths(env)


def _measured_helper_state(text, env):
    """'measured' | 'changed' | 'unreadable' | None (not the helper at all).

    Only 'measured' licenses the exclusion. Every other outcome counts, so a
    helper we cannot hash is treated exactly like one we never measured -
    "couldn't look" is never "nothing there".

    Deliberately NOT cached on (path, size, mtime). An earlier revision did, and
    it was a fail-open: the one helper update actually observed kept the SAME byte
    length (1,018,704 both builds), so metadata is not content identity here, and a
    same-size same-mtime replacement would have been served a stale "measured"
    verdict for bytes nobody measured. Re-reading ~1 MB a few times per command is
    cheap; carrying trust across an update is not.
    """
    if not _looks_like_chrome_helper(text, env):
        return None
    try:
        h = hashlib.sha256()
        with open(text, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        digest = h.hexdigest()
    except OSError:
        return "unreadable"
    return "measured" if digest == _HELPER_SHA256 else "changed"


# RULING 7 (2026-08-13). OPT-IN: trust a validly-signed Anthropic helper even when
# its bytes are not the measured build.
#
# Why an option exists at all. RULING 6 binds to the measured hash, and the helper
# auto-updates in place - three distinct builds were observed in eight days
# (2026-08-04, 08-06, 08-11). A hash binding therefore buys ~2-4 days of
# "Chrome may stay open" per ~12-minute re-measurement ceremony, which is a bad
# enough trade that the exclusion decays back into the friction it removed. That
# is not a safer outcome; it is the same friction plus dead code.
#
# What it trades. Trust moves from "these exact measured bytes" to "this
# publisher". The residual risk is not forgery - it is that a FUTURE Anthropic
# build of the helper starts touching the store and the signature excuses it
# because nobody re-measured. Small (the measurement found a message bridge whose
# image contains no store path at all, held no store handle, and produced no store
# mutation across a 4m23s window under live extension traffic) but not zero.
#
# Why OPT-IN and not the default. This is a published tool, and other users have
# not seen that measurement. Loosening the shipped default would hand them a
# weaker guard silently, on evidence they never examined. The default therefore
# stays RULING 6; only a deliberate, auditable act enables this.
_TRUST_SIGNED_MARKER = "trust-signed-helper"
_HELPER_PUBLISHER = "anthropic, pbc"


def trust_signed_helper_path(env):
    """The opt-in marker's path. Its EXISTENCE is the opt-in - no parsing, nothing
    to typo, and `del` is a complete revocation."""
    return os.path.join(os.path.dirname(env.ops_dir), _TRUST_SIGNED_MARKER)


def signed_helper_trust_enabled(env):
    try:
        return os.path.isfile(trust_signed_helper_path(env))
    except OSError:
        return False            # cannot look -> not enabled (fail closed)


def _authenticode_publisher(path):
    """Signing subject iff Windows reports the signature VALID, else None.

    Deliberately not cached. Caching on (path, size, mtime) is exactly the
    fail-open review caught in the hash version: metadata is not identity, and a
    same-size same-mtime replacement would be served a stale verdict. A ~0.3s
    subprocess a few times per command is the cheaper mistake.
    """
    if sys.platform != "win32":
        return None
    import subprocess
    ps = ("$ErrorActionPreference='Stop';"
          "$s = Get-AuthenticodeSignature -LiteralPath '{0}';"
          "if ($s.Status -eq 'Valid') {{ $s.SignerCertificate.Subject }}"
          ).format(path.replace("'", "''"))
    # pwsh FIRST, then Windows PowerShell. Measured 2026-08-13: on this machine
    # `powershell` (5.1) fails with "the module could not be loaded" for
    # Microsoft.PowerShell.Security, while pwsh 7 answers correctly - so trying
    # only the always-present interpreter would report every helper unsigned and
    # make the opt-in silently useless. Both are tried because neither is
    # guaranteed: pwsh is not installed by default, and 5.1's security module can
    # evidently be unavailable.
    for exe in ("pwsh", "powershell"):
        try:
            proc = subprocess.run(
                [exe, "-NoProfile", "-NonInteractive", "-Command", ps],
                capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.returncode == 0 and (proc.stdout or "").strip():
            return proc.stdout.strip()
    # No interpreter could verify. That is "couldn't look", which here means the
    # helper keeps counting - the opt-in simply does not take effect.
    return None


def _dn_field(subject, key):
    """One RDN value out of a certificate subject, quoted or bare."""
    m = re.search(r'(?:^|,)\s*' + key + r'=(?:"([^"]*)"|([^,]*))', subject)
    if not m:
        return ""
    return (m.group(1) if m.group(1) is not None else (m.group(2) or "")).strip()


def _signed_helper_state(text):
    """'signed' | 'unsigned', for a path already known to be the helper.

    Only a VALID signature whose CN *and* O are EXACTLY the expected publisher
    counts. A substring test was the first version and it was a hole: a valid
    certificate for `CN="Not Anthropic, PBC"` - or any DN merely containing that
    text in some other field - would have satisfied it, so the "differently
    signed binaries still count" guarantee did not hold. Anything else, including
    an unreadable verdict, returns 'unsigned' and therefore keeps counting.
    """
    subject = _authenticode_publisher(text)
    if not subject:
        return "unsigned"
    cn = _dn_field(subject, "CN").lower()
    org = _dn_field(subject, "O").lower()
    return "signed" if cn == _HELPER_PUBLISHER and org == _HELPER_PUBLISHER else "unsigned"


def helper_hash_note(running, env):
    """Explain a helper that is counted because it is not the measured build."""
    for text in running:
        state = _measured_helper_state(text, env)
        if state in ("changed", "unreadable"):
            note = (
                "\nNote: the process above is the desktop app's Chrome-extension "
                "helper. A measured build of it is excluded from this guard, but this "
                "one {0} - the helper auto-updates in place, so an exclusion cannot "
                "carry over to code that was never measured. Closing Chrome clears "
                "this. To restore the exclusion, re-measure with "
                "tools/native-host-measurement/watch_ceremony.py (about 12 minutes) "
                "and update _HELPER_SHA256.".format(
                    "could not be read" if state == "unreadable"
                    else "is a different binary"))
            if not signed_helper_trust_enabled(env):
                note += (
                    "\nThe helper updates every few days, so re-measuring each time is "
                    "usually not worth it. The alternative is to trust any helper at "
                    "this path that Windows reports as validly signed by Anthropic, "
                    "PBC - weaker than measured bytes, and OFF by default because it "
                    "rests on evidence you have not examined. Turn it on with:\n"
                    "   claude-code-sessions trust-signed-helper --on")
            return note
    return ""


def claude_running(env):
    my_pids = {os.getpid(), os.getppid()}
    try:
        procs = env.process_lister()
    except Exception:
        procs = [(-1, _PROC_UNAVAILABLE)]      # couldn't look != nothing there
    out = []
    for pid, text in procs:
        if pid in my_pids:
            continue                       # never self-refuse on our own process
        if any(name in text for name in OUR_COMMANDS):
            continue                       # nor on another instance of this tool
        if _is_cli_process(text):
            continue                       # the Claude Code CLI, not the desktop app
        helper = _measured_helper_state(text, env)
        if helper == "measured":
            continue                       # RULING 6: the measured Chrome helper
        if (helper == "changed" and signed_helper_trust_enabled(env)
                and _signed_helper_state(text) == "signed"):
            continue                       # RULING 7: opt-in, validly signed
        # note "unreadable" is deliberately NOT eligible for RULING 7: a helper
        # whose bytes cannot be read cannot be shown to be the file that was
        # signature-checked, so it keeps counting under either ruling.
        if "claude" in text:
            out.append(text)
    return out


MTIME_GUARD_SECONDS = 600


def plan_move(env, session_id, target, flags):
    target = os.path.normpath(os.path.abspath(target))

    # 1. store discovery / platform posture
    disc = discover_stores(env)
    if disc.status == "error":
        raise LayoutError("store discovery failed: {0}. 'Couldn't look' is never "
                          "'nothing there' - refusing to mutate.".format(disc.detail))
    rows, row_errors = load_rows(disc.roots)
    if row_errors:
        raise LayoutError("unreadable listing rows (fail-closed): " + "; ".join(row_errors))

    # 2. encoding + destination folder (computed before transcript lookup: a
    # transcript that already exists exactly AT the computed destination is a
    # destination collision, not an ambiguous source - see step 3)
    moved = moved_session_ids(env)
    if rows:
        cwds = [r.cwd for r in sorted(rows, key=lambda r: r.last_activity)[-50:]]
    else:
        cwds = []
        for folder, path in iter_transcripts(env.projects_root):
            sid = os.path.splitext(os.path.basename(path))[0]
            if sid in moved:
                continue
            c = first_cwd(path)
            if not c:
                continue
            enc_c, enc_l = encode(c, SCHEME_CURRENT), encode(c, SCHEME_LEGACY)
            if folder not in (enc_c, enc_l):
                continue        # worktree session: folder matches neither
            cwds.append(c)
    scheme = choose_scheme(scheme_evidence(cwds, env.projects_root), target)
    dest_dir = os.path.join(env.projects_root, encode(target, scheme))
    dest_transcript = os.path.join(dest_dir, session_id + ".jsonl")

    # 3. transcript location, globally. A hit whose real path is exactly the
    # computed destination transcript is not source-ambiguity - it is handled
    # by the destination-exists check in step 4 - so it is excluded here.
    hits = find_transcripts(env.projects_root, session_id)
    dest_real = os.path.realpath(dest_transcript)
    source_hits = [h for h in hits if os.path.realpath(h) != dest_real]
    if not source_hits:
        if hits:
            raise Refusal("source and destination transcript are identical: "
                          "{0}".format(dest_transcript))
        raise Refusal("No transcript found for {0}. Use 'claude-code-sessions list' to find "
                      "session ids.".format(session_id))
    if len(source_hits) > 1:
        raise Refusal("Ambiguous: transcript exists in several folders:\n  " +
                      "\n  ".join(source_hits))
    source = source_hits[0]

    # 4. destination checks
    if not os.path.isdir(target):
        raise Refusal("target must be an existing directory: {0}".format(target))
    real_target = os.path.normcase(os.path.realpath(target))
    for forbidden in (os.path.join(env.home, ".claude"), os.path.dirname(env.ops_dir)):
        # normcase both sides: on a first run ~/.claude-code-journal does not exist
        # yet, so realpath alone does not canonicalize case on Windows.
        fr = os.path.normcase(os.path.realpath(forbidden))
        if real_target == fr or real_target.startswith(fr + os.sep):
            raise Refusal("refusing target inside {0}".format(forbidden))
    if os.path.exists(dest_transcript) or os.path.exists(sidecar_path(dest_transcript)):
        raise Refusal("destination already exists: {0}".format(dest_transcript))
    if os.path.realpath(os.path.dirname(source)) == os.path.realpath(dest_dir):
        raise Refusal("source and destination are the same folder")
    if os.path.isdir(dest_dir):
        for name in sorted(os.listdir(dest_dir)):
            if not name.endswith(".jsonl"):
                continue
            other = os.path.join(dest_dir, name)
            try:
                with open(other, "rb"):
                    pass
            except OSError as exc:
                raise Refusal("cannot read {0} for the destination collision scan "
                              "(fail-closed): {1}".format(other, exc))
            sid = name[:-len(".jsonl")]
            if sid in moved:
                continue
            c = last_cwd(other)
            if not c:
                raise Refusal("destination collision: {0} has no recorded cwd; cannot "
                              "verify it belongs to this project - refusing to merge "
                              "(ambiguous, fail-closed).".format(other))
            if os.path.normcase(os.path.normpath(c)) != os.path.normcase(os.path.normpath(target)):
                raise Refusal("destination collision: {0} records cwd {1}, which is a "
                              "different real path than {2}. Two real paths can share "
                              "one encoded folder; refusing to merge projects."
                              .format(other, c, target))
    import shutil as _shutil
    t_hash, t_size = sha256_file(source)
    side_src = sidecar_path(source)
    inv = sidecar_inventory(side_src) if os.path.isdir(side_src) else []
    need = t_size + sum(e["size"] for e in inv) + (1 << 20)
    if _shutil.disk_usage(os.path.dirname(dest_dir)).free < need:
        raise Refusal("not enough free space for a safe copy")

    # 5. row set
    my_rows = [r for r in rows if r.cli_session_id == session_id]
    for local_id in (flags.row or ()):
        lid = local_id if local_id.startswith("local_") else "local_" + local_id
        # listing rows are per-account COPIES: the same local id can legitimately
        # appear once per store (e.g. one desktop app, two org/account stores),
        # so every matching row - not just the first found - must be adopted.
        matches = [r for r in rows if r.local_id == lid]
        if not matches:
            raise Refusal("no listing row with sessionId " + lid)
        if any(r.cli_session_id not in ("", session_id) for r in matches):
            raise Refusal("row {0} is linked to a different live session; rows linked "
                          "to a different live session are never adoptable".format(lid))
        if not flags.yes:
            first = matches[0]
            raise Refusal("adopting row {0} (title={1!r}, cwd={2!r}, "
                          "lastActivityAt={3!r}) requires confirmation: pass --yes"
                          .format(lid, first.data.get("title"), first.cwd,
                                  first.data.get("lastActivityAt")))
        for r in matches:
            if r not in my_rows:
                my_rows.append(r)
    if not my_rows:
        if disc.status == "found" and not flags.transcript_only:
            raise Refusal("no listing row references this transcript; moving it would "
                          "orphan the desktop entry. If this session was created by the "
                          "CLI (not the desktop app), pass --transcript-only.")
        if disc.status == "absent" and not flags.transcript_only:
            raise Refusal("no desktop store found. If you don't use the desktop app, "
                          "pass --transcript-only. (On mac/Linux the store locations "
                          "are unverified - absence may mean we looked in the wrong "
                          "place.)")
    mode = "desktop" if my_rows else "transcript_only"
    if mode == "desktop":
        _require_verified_platform(env, "mutate")

    # 6. guards
    running = claude_running(env)
    if running:
        raise Refusal("Claude appears to be running ({0}). Close the app, then retry."
                      .format(", ".join(sorted(set(running))[:3])))
    age = env.now() - os.path.getmtime(source)
    if age < MTIME_GUARD_SECONDS and not flags.force:
        raise Refusal("transcript was written {0:.0f} seconds ago - this session may be "
                      "open (checked because a recent mtime lasts ~10 minutes). Close "
                      "the app; pass --force only if you are sure this is stale."
                      .format(age))

    row_entries = []
    for r in my_rows:
        with open(r.path, "rb") as fh:
            pre = fh.read()
        post = dict(r.data)
        post["cwd"] = target
        post["originCwd"] = target
        post["cliSessionId"] = session_id
        row_entries.append({"path": r.path, "pre_b64": b64(pre),
                            "post_b64": b64(json.dumps(post, separators=(",", ":"))
                                            .encode("utf-8")),
                            "rewritten": False})
    return {
        "op_type": "move", "session_id": session_id, "mode": mode,
        "source_transcript": source, "dest_transcript": dest_transcript,
        "transcript_sha256": t_hash, "transcript_size": t_size,
        "sidecar_source": side_src if inv else None,
        "sidecar_dest": sidecar_path(dest_transcript) if inv else None,
        "sidecar_inventory": inv, "rows": row_entries, "target_cwd": target,
    }


# ------------------------------------------------------ engine execution
_crash_hook = None


def _maybe_crash(point):
    if _crash_hook is not None:
        _crash_hook(point)


def _engine_roots(env, manifest):
    """Allowed containment roots for listing-row paths.

    Store roots come from `discover_stores`, never from the row path being
    checked itself - deriving a row's "allowed root" from that same row's
    path makes the containment check vacuous (it can never fail).
    """
    roots = [os.path.dirname(env.ops_dir)]
    if manifest.get("rows"):
        disc = discover_stores(env)
        if disc.status != "found":
            raise LayoutError(
                "cannot verify listing-row containment: store discovery status is "
                "'{0}', not 'found'".format(disc.status))
        roots.extend(disc.roots)
    return roots


def _validate_sidecar_rel(rel):
    if os.path.isabs(rel) or "\\" in rel or any(part == ".." for part in rel.split("/")):
        raise LayoutError("unsafe sidecar rel path in manifest: {0!r}".format(rel))


def _delete_inventoried_files(root_dir, inventory):
    """Delete each inventoried file; return a list of (path, exc) for any
    that could not be removed instead of swallowing the error - a caller
    that silently ignores a failed delete here would let the file be
    orphaned with no journal trail once the source is gone."""
    failures = []
    for e in inventory:
        full = os.path.join(root_dir, *e["rel"].split("/"))
        try:
            os.unlink(full)
        except OSError as exc:
            failures.append((full, exc))
    return failures


def _rmdirs_bottom_up(root_dir):
    dirs = []
    for dirpath, dirnames, filenames in os.walk(root_dir):
        dirs.append(dirpath)
    for d in sorted(dirs, key=len, reverse=True):
        try:
            os.rmdir(d)
        except OSError:
            pass


def _copy_file(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    fd = os.open(dst, os.O_CREAT | os.O_EXCL | os.O_WRONLY)  # exclusive create
    with os.fdopen(fd, "wb") as out, open(src, "rb") as inp:
        while True:
            chunk = inp.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
        out.flush()
        os.fsync(out.fileno())


def _dest_files(manifest):
    files = [(manifest["dest_transcript"], manifest["transcript_sha256"],
              manifest["transcript_size"])]
    for e in manifest["sidecar_inventory"]:
        files.append((os.path.join(manifest["sidecar_dest"], *e["rel"].split("/")),
                      e["sha256"], e["size"]))
    return files


def _verify(path_hash_size_list):
    for path, digest, size in path_hash_size_list:
        got, gsize = sha256_file(path)
        if got != digest or gsize != size:
            return path
    return None


def _row_state(row):
    """Classify a manifest row's CURRENT on-disk bytes against its journaled
    pre/post images - a crash can land between os.replace and save_manifest,
    so the "rewritten" flag alone can never be trusted; only the bytes can.
    Returns "post" (needs no roll-forward, may need roll-back), "pre"
    (untouched / already rolled back), or "drifted" (neither - some other
    process wrote to it, or it is missing; never auto-resolved).
    """
    pre = unb64(row["pre_b64"])
    post = unb64(row["post_b64"])
    try:
        with open(row["path"], "rb") as fh:
            current = fh.read()
    except OSError:
        current = None
    if current == post:
        return "post"
    if current == pre:
        return "pre"
    return "drifted"


def _pre_abort_status(op):
    """The phase the op was in before it started (or resumed) aborting -
    used to decide whether destination files are still tool-owned scratch
    (I3). Trusting op.manifest["status"] directly breaks the moment a crash
    interrupts an abort itself and recover re-enters _abort: by then status
    already reads "aborting", which carries no information about the
    original phase. History is durable and append-only, so walk it
    backwards past every "aborting" entry to find the real one.
    """
    for entry in reversed(op.manifest.get("history", [])):
        if entry.get("status") != "aborting":
            return entry.get("status")
    return op.manifest["status"]


def _source_pre_verified(m):
    """C1(a): True iff the source transcript AND every inventoried sidecar
    file are currently present and byte-identical to what was journaled as
    the pre-state. Gates non-scratch (hash-gated) destination deletion
    during abort - deleting a hash-verified destination copy is only safe
    when the source being kept instead is itself provably intact. Without
    this, a source that vanished or drifted in a crash-adjacent window
    would leave the destination - possibly the only remaining copy -
    deleted anyway, because the existing hash-gate only ever checked the
    DEST against its own journaled hash and said nothing about the
    source's current state.
    """
    if not os.path.isfile(m["source_transcript"]):
        return False
    got, gsize = sha256_file(m["source_transcript"])
    if got != m["transcript_sha256"] or gsize != m["transcript_size"]:
        return False
    if m.get("sidecar_source"):
        for e in m["sidecar_inventory"]:
            p = os.path.join(m["sidecar_source"], *e["rel"].split("/"))
            if not os.path.isfile(p):
                return False
            got, gsize = sha256_file(p)
            if got != e["sha256"] or gsize != e["size"]:
                return False
    return True


def _abort(env, op, delete_dest=True, trigger=None):
    prior_status = _pre_abort_status(op)
    m = op.manifest
    # C1(b): once this op has committed to a keep-both resolution - either
    # the phase-6 decision a caller persisted to the manifest BEFORE ever
    # calling _abort, or one _abort itself reaches below - every future
    # invocation for this op must keep honoring it, including a
    # crash-resumed one via recover's "back" (which always calls _abort
    # with its own default delete_dest=True). Without this, the earlier
    # decision is invisible to a later call and "back" can silently
    # complete a hash-gated delete the first call deliberately declined.
    if m.get("abort_keep_dest"):
        delete_dest = False
    if trigger and not m.get("abort_reason"):
        m["abort_reason"] = trigger
    set_status(op, "aborting")
    _maybe_crash("after-aborting")

    # Classify everything FIRST, as pure reads - no row is rewritten and no
    # destination file is deleted until we know the WHOLE rollback can
    # complete cleanly. Interleaving classification with mutation meant a
    # drifted row (or dest file) discovered partway through left some rows
    # already reverted and/or some dest files already deleted before the
    # Refusal - making a "nothing was deleted" claim false.
    row_restores = []
    drifted_rows = []
    for r in m["rows"]:
        state = _row_state(r)
        if state == "post":
            row_restores.append((r, unb64(r["pre_b64"])))
        elif state == "drifted":
            drifted_rows.append(r["path"])

    scratch = prior_status in ("journaled", "copying")

    # C1(a): a hash-gated (non-scratch) destination deletion only ever
    # checked the DEST against its own journaled hash; it said nothing
    # about whether the SOURCE we are keeping instead is actually still
    # there. Verify it before any such delete is allowed to happen.
    source_unverifiable = delete_dest and not scratch and not _source_pre_verified(m)

    do_delete = delete_dest and not source_unverifiable
    dest_deletes = []
    drifted_dest = []
    if do_delete:
        for path, digest, size in _dest_files(m):
            if not os.path.isfile(path):
                continue
            if scratch:
                dest_deletes.append(path)
                continue
            got, gsize = sha256_file(path)
            if got == digest and gsize == size:
                dest_deletes.append(path)
            else:
                drifted_dest.append(path)

    problems = drifted_rows + drifted_dest
    if problems:
        m["drifted_rows"] = drifted_rows
        save_manifest(op)
        raise Refusal("rollback could not verify every file ({0}); nothing "
                      "was changed. Use 'claude-code-sessions recover' to "
                      "resolve.".format(", ".join(problems)))

    for r, pre_bytes in row_restores:
        atomic_write(r["path"], pre_bytes)
    for r in m["rows"]:
        r["rewritten"] = False
    m["drifted_rows"] = []
    save_manifest(op)

    if source_unverifiable:
        # C1(a): rows are restored as usual above, but the destination is
        # never touched - the source we would be relying on to justify
        # deleting a hash-verified dest copy could not itself be verified,
        # so both copies are kept. Persist that decision (mirrors C1(b))
        # so a later resumed "back" never re-attempts the same unsafe
        # hash-gated delete.
        m["abort_keep_dest"] = True
        if not m.get("abort_reason"):
            m["abort_reason"] = "source changed at last instant"
        save_manifest(op)
        raise Refusal(
            "rollback could not verify the source ({0}) against its journaled "
            "pre-state; the destination copy at {1} is being kept, not deleted "
            "- nothing was lost, both copies remain. Run 'claude-code-sessions "
            "recover' to resolve.".format(m["source_transcript"], m["dest_transcript"]))

    if do_delete:
        for path in dest_deletes:
            os.unlink(path)
        # I7: never rmtree - only the journaled files are ours to delete; any
        # leftover (non-inventoried) file makes its directory fail to rmdir
        # and survives, exactly like the source-side rule in execute_op.
        if m.get("sidecar_dest") and os.path.isdir(m["sidecar_dest"]):
            _rmdirs_bottom_up(m["sidecar_dest"])
    set_status(op, "rolled_back")


def _validate_manifest_paths(env, m):
    """Structural + containment validation for every path a manifest could
    direct a write or delete to. Must run before ANY mutation - a
    tampered/foreign manifest (or one whose target has since moved behind a
    symlink/junction) must be rejected before a single file is touched.
    Shared by execute_op (fresh runs) and recover_op (resumed runs, I4) so a
    resumed op gets exactly the same up-front check a fresh one does.
    """
    # C2(a): a tampered/foreign manifest's rel paths must be structurally
    # safe before they are ever joined onto a filesystem path.
    for e in m.get("sidecar_inventory", []):
        _validate_sidecar_rel(e["rel"])

    # C2(b): containment on the actual files (not just their dirnames), and
    # on every sidecar path we are about to touch - all before any mutation.
    ensure_contained(m["source_transcript"], [env.projects_root])
    ensure_contained(m["dest_transcript"], [env.projects_root])
    if m.get("sidecar_source"):
        ensure_contained(m["sidecar_source"], [env.projects_root])
    if m.get("sidecar_dest"):
        ensure_contained(m["sidecar_dest"], [env.projects_root])
    for e in m.get("sidecar_inventory", []):
        ensure_contained(os.path.join(m["sidecar_source"], *e["rel"].split("/")),
                         [env.projects_root])
        ensure_contained(os.path.join(m["sidecar_dest"], *e["rel"].split("/")),
                         [env.projects_root])

    roots = _engine_roots(env, m)
    for r in m["rows"]:
        ensure_contained(r["path"], roots)


def execute_op(env, op):
    """Drive a freshly-journaled op through copy -> verify -> commit -> delete-last.

    Only accepts ops whose status is 'journaled': this function always runs
    a full transaction from the top and is not itself resumption-aware.
    Resuming an op interrupted mid-flight is `recover`'s job (Task 11) - it
    inspects each phase individually rather than re-entering here. Callers
    hold the lock.
    """
    m = op.manifest
    if m.get("status") != "journaled":
        raise LayoutError("execute_op only runs ops from 'journaled'; use recover "
                          "for interrupted ops")

    _validate_manifest_paths(env, m)

    _maybe_crash("after-journaled")

    set_status(op, "copying")
    _maybe_crash("after-copying")
    try:
        _copy_file(m["source_transcript"], m["dest_transcript"])
        for e in m["sidecar_inventory"]:
            _copy_file(os.path.join(m["sidecar_source"], *e["rel"].split("/")),
                       os.path.join(m["sidecar_dest"], *e["rel"].split("/")))
    except OSError:
        _abort(env, op, delete_dest=True, trigger="copy failed")
        return "rolled_back"

    bad = _verify(_dest_files(m))
    if bad is not None:
        _abort(env, op, trigger="destination verification failed")
        return "rolled_back"
    for path, _, _ in _dest_files(m):
        fsync_file(path)
    set_status(op, "copied")
    _maybe_crash("after-copied")

    set_status(op, "rewriting")
    _maybe_crash("after-rewriting")
    try:
        rows = m["rows"]
        for i, r in enumerate(rows):
            # A row that changed between planning and rewriting (some other
            # process touched it) must never be blindly overwritten - re-read
            # its CURRENT bytes right before the write and compare against
            # the journaled pre-image. _abort independently re-derives each
            # row's state from its current bytes (never from the "rewritten"
            # flag), so it will correctly leave this drifted row untouched
            # and, per its existing fail-closed contract, refuse to complete
            # automatically if it cannot verify every row - `recover` is the
            # path out, exactly like any other drifted-row abort.
            with open(r["path"], "rb") as fh:
                current = fh.read()
            if current != unb64(r["pre_b64"]):
                # M3: the following return is unreachable in practice - this
                # is the FIRST time execute_op ever touches this row within a
                # fresh run (only journaled ops reach execute_op), so a
                # mismatch here can only mean "drifted" (never "post"), and
                # _abort always raises Refusal for a drifted row rather than
                # returning. Kept as a call, not inlined, so the abort still
                # happens if that invariant is ever wrong.
                _abort(env, op, trigger="row changed before rewrite")
            atomic_write(r["path"], unb64(r["post_b64"]))
            r["rewritten"] = True
            save_manifest(op)
            if i < len(rows) - 1:
                _maybe_crash("mid-rewriting")
    except OSError:
        _abort(env, op, trigger="row changed before rewrite")
        return "rolled_back"

    set_status(op, "committed")
    _maybe_crash("after-committed")

    # last-instant revalidation: BOTH sides + process guard (spec phase 6)
    src_ok = os.path.isfile(m["source_transcript"])
    if src_ok:
        got, gsize = sha256_file(m["source_transcript"])
        if got != m["transcript_sha256"] or gsize != m["transcript_size"]:
            src_ok = False
    if src_ok and m.get("sidecar_source"):
        for e in m["sidecar_inventory"]:
            p = os.path.join(m["sidecar_source"], *e["rel"].split("/"))
            if not os.path.isfile(p):
                src_ok = False
                break
            got, gsize = sha256_file(p)
            if got != e["sha256"] or gsize != e["size"]:
                src_ok = False
                break
    dest_ok = _verify(_dest_files(m)) is None
    running = claude_running(env)
    if not src_ok or not dest_ok or running:
        # I3/C1(b): persist the keep-both decision BEFORE _abort is even
        # called - a crash inside _abort itself (e.g. right after it enters
        # 'aborting') must not lose the fact that this rollback was always
        # meant to keep both copies. Once this is on the manifest, _abort
        # forces delete_dest=False on any future call for this op,
        # including a crash-resumed 'back' via recover.
        if not src_ok:
            reason = "source changed at last instant"
        elif not dest_ok:
            reason = "destination verification failed"
        else:
            reason = "process guard"
        m["abort_keep_dest"] = True
        m["abort_reason"] = reason
        save_manifest(op)
        _abort(env, op, delete_dest=False)   # phase-6 abort keeps BOTH copies (spec)
        return "rolled_back"

    # C1: never destroy a source-sidecar file that was never journaled - a
    # file that is the only copy of its data must not die with the source.
    if m.get("sidecar_source") and os.path.isdir(m["sidecar_source"]):
        inv_rels = {e["rel"] for e in m["sidecar_inventory"]}
        extra = []
        for dirpath, dirnames, filenames in os.walk(m["sidecar_source"]):
            for name in filenames:
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, m["sidecar_source"]).replace(os.sep, "/")
                if rel not in inv_rels:
                    extra.append(full)
        if extra:
            # I3/C1(b): same pre-persisted keep-both decision as above - a
            # newly-appeared source sidecar file is itself a form of
            # "source changed" since planning.
            m["abort_keep_dest"] = True
            m["abort_reason"] = "source changed at last instant"
            save_manifest(op)
            _abort(env, op, delete_dest=False)
            return "rolled_back"

    # I8: sidecar files first, then now-empty dirs, transcript LAST. A
    # cleanup failure (e.g. a locked file) leaves the op at 'committed'
    # (non-terminal, no new journal state) for `recover` to finish instead
    # of crashing after the move has already been fully committed. A failed
    # sidecar delete must NOT be swallowed and must NOT let the transcript
    # get deleted anyway - that would orphan the sidecar file with no
    # journal trail. Leaving the transcript in place keeps the source
    # coherent for recover's classification.
    if m.get("sidecar_source") and os.path.isdir(m["sidecar_source"]):
        failures = _delete_inventoried_files(m["sidecar_source"], m["sidecar_inventory"])
        if failures:
            print("warning: move committed, but the old copy could not be fully "
                  "removed ({0}). Run 'claude-code-sessions recover' to finish deleting "
                  "it.".format(", ".join(p for p, _ in failures)))
            return "committed"
        _rmdirs_bottom_up(m["sidecar_source"])

    try:
        os.unlink(m["source_transcript"])
    except OSError as exc:
        print("warning: move committed, but the old copy could not be fully "
              "removed ({0}). Run 'claude-code-sessions recover' to finish deleting "
              "it.".format(exc))
        return "committed"

    set_status(op, "completed")
    return "completed"


def run_move(env, manifest):
    lock_owner_op = "pending"
    acquire_lock(env, lock_owner_op)
    try:
        op = new_op(env, manifest)
        # we already hold the lock (no O_EXCL needed) - just record the real op_id
        with open(_lock_path(env), "w") as fh:
            fh.write("{0} {1}".format(os.getpid(), op.manifest["op_id"]))
        final = execute_op(env, op)
        if final == "completed":
            append_moved_log(env, {"kind": "move", "session_id": manifest["session_id"],
                                   "from": manifest["source_transcript"],
                                   "to": manifest["dest_transcript"],
                                   "at": env.now()})
            rotate_ops(env)
        return final
    finally:
        release_lock(env)


# ------------------------------------------------------ undo
def _op_sort_key(manifest):
    """(creation time, op_id) - the same compound key list_ops sorts by.
    op_id alone is not a safe "is this newer" comparison: its trailing
    os.urandom(3).hex() carries no chronological meaning, only the
    strftime-derived prefix does, and two ops created in the same wall-clock
    second collapse to comparing that random suffix.
    """
    return (manifest.get("history", [{}])[0].get("at", 0), manifest.get("op_id", ""))


def plan_undo(env, prior_op):
    """Build a reversal manifest for a completed move: source/dest swapped,
    row pre/post images swapped, same hashes. Every precondition here checks
    the CURRENT on-disk state against what the move journaled as its
    post-state - any drift (the app resumed the moved session, edited a row,
    etc.) means undoing would silently discard that activity, so it refuses
    instead of guessing. This is undo, not recover: growth at the
    destination is never accepted here the way `classify_op` accepts it for
    a crash-interrupted move.
    """
    pm = prior_op.manifest
    if pm.get("op_type") == "undo":
        raise Refusal("op {0} is itself an undo; to redo, run move again"
                      .format(pm.get("op_id")))
    if pm.get("status") != "completed":
        raise Refusal("op {0} is '{1}', not 'completed'; only completed ops can be "
                      "undone".format(pm.get("op_id"), pm.get("status")))
    pm_key = _op_sort_key(pm)
    for other in list_ops(env):
        if _op_sort_key(other.manifest) > pm_key and \
                other.manifest.get("session_id") == pm["session_id"] and \
                other.manifest.get("status") not in ("rolled_back", "undone"):
            raise Refusal("a newer op touches this session; undo newest-first")

    # C1: the undo's OWN destination is the original move's source path.
    # _abort's scratch rule treats a journaled/copying-phase destination as
    # tool-owned and deletes it unconditionally on rollback (no hash check);
    # a foreign file the user manually put back at that path - they restored
    # and resumed the session there by hand - would otherwise be destroyed
    # the moment the copy's O_EXCL create fails. Mirror plan_move's own
    # destination-exists check here, before any op is even journaled.
    if os.path.exists(pm["source_transcript"]) or \
            os.path.exists(sidecar_path(pm["source_transcript"])):
        raise Refusal("undo target already exists: {0}; refusing to overwrite it."
                      .format(pm["source_transcript"]))

    if not os.path.isfile(pm["dest_transcript"]):
        raise Refusal("the moved transcript is missing at {0}; cannot undo."
                      .format(pm["dest_transcript"]))
    got, gsize = sha256_file(pm["dest_transcript"])
    if got != pm["transcript_sha256"] or gsize != pm["transcript_size"]:
        raise Refusal("the moved transcript has changed since the move (resumed or "
                      "edited). Undoing would overwrite that activity; refusing.")
    for e in pm["sidecar_inventory"]:
        p = os.path.join(pm["sidecar_dest"], *e["rel"].split("/"))
        if not os.path.isfile(p):
            raise Refusal("sidecar file {0} has changed since the move; refusing."
                          .format(e["rel"]))
        got_s, gsize_s = sha256_file(p)          # M3: size AND hash, not hash alone
        if got_s != e["sha256"] or gsize_s != e["size"]:
            raise Refusal("sidecar file {0} has changed since the move; refusing."
                          .format(e["rel"]))
    # I3: the reverse of the loop above - journaled-subset-of-present is not
    # enough; a file that appeared in the moved sidecar AFTER the move (never
    # journaled, so nothing above would ever notice it) must also block undo.
    # That file is post-move activity and must survive; refusing at plan
    # time (before any op is journaled) avoids ever landing in a stuck
    # two-folder state over it.
    if pm.get("sidecar_dest") and os.path.isdir(pm["sidecar_dest"]):
        inv_rels = {e["rel"] for e in pm["sidecar_inventory"]}
        extra = []
        for dirpath, dirnames, filenames in os.walk(pm["sidecar_dest"]):
            for name in filenames:
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, pm["sidecar_dest"]).replace(os.sep, "/")
                if rel not in inv_rels:
                    extra.append(full)
        if extra:
            raise Refusal("untracked file(s) appeared in the moved sidecar since the "
                          "move ({0}); refusing - resolve manually."
                          .format(", ".join(extra)))

    rows = []
    for r in pm["rows"]:
        with open(r["path"], "rb") as fh:
            cur = fh.read()
        if cur != unb64(r["post_b64"]):
            raise Refusal("listing row {0} has changed since the move (the app may "
                          "have updated it); refusing.".format(r["path"]))
        rows.append({"path": r["path"], "pre_b64": r["post_b64"],
                     "post_b64": r["pre_b64"], "rewritten": False})

    # M4: target_cwd describes where THIS manifest is taking the session -
    # for undo that is the original pre-move location, not the move's own
    # target_cwd (which described where the FORWARD move went).
    if pm["rows"]:
        first_pre = json.loads(unb64(pm["rows"][0]["pre_b64"]).decode("utf-8"))
        target_cwd = first_pre.get("cwd", "")
    else:
        target_cwd = ""

    return {
        "op_type": "undo", "undo_of": pm["op_id"], "session_id": pm["session_id"],
        "mode": pm["mode"],
        "source_transcript": pm["dest_transcript"],
        "dest_transcript": pm["source_transcript"],
        "transcript_sha256": pm["transcript_sha256"],
        "transcript_size": pm["transcript_size"],
        "sidecar_source": pm.get("sidecar_dest"),
        "sidecar_dest": pm.get("sidecar_source"),
        "sidecar_inventory": pm["sidecar_inventory"],
        "rows": rows, "target_cwd": target_cwd,
    }


def run_undo(env, prior_op):
    """Lock -> claude_running check -> plan_undo -> new_op -> execute_op.
    The engine itself needs no changes for undo: an undo op is just a move
    manifest pointing the other way. But plan_move's process guard does not
    run for undo, so run_undo checks claude_running itself before executing
    (execute_op's own guard only fires at its last-instant revalidation,
    deep into the transaction) - and, like run_move/recover_op, it takes the
    single-instance lock FIRST, before doing any of its own checks, so two
    concurrent claude-code-sessions invocations can never race each other here.
    On 'completed' the prior op is marked 'undone' and a moved-log entry
    cancels its 'move' entry; any other outcome (e.g. 'committed' if final
    cleanup could not fully finish, or 'rolled_back') leaves the prior op's
    status untouched for the user to retry or recover.
    """
    acquire_lock(env, "undo-" + prior_op.manifest["op_id"])
    try:
        if claude_running(env):
            raise Refusal("Claude appears to be running; close the app before undoing.")
        manifest = plan_undo(env, prior_op)
        op = new_op(env, manifest)
        final = execute_op(env, op)
        if final == "completed":
            set_status(prior_op, "undone")
            append_moved_log(env, {"kind": "undo",
                                   "session_id": manifest["session_id"],
                                   "at": env.now()})
            rotate_ops(env)
        return final
    finally:
        release_lock(env)


# ------------------------------------------------------ recovery
def is_prefix_of(journaled_hash, journaled_size, path):
    h = hashlib.sha256()
    remaining = journaled_size
    try:
        with open(path, "rb") as fh:
            while remaining > 0:
                chunk = fh.read(min(1 << 20, remaining))
                if not chunk:
                    return False
                h.update(chunk)
                remaining -= len(chunk)
    except OSError:
        return False
    return h.hexdigest() == journaled_hash


def _new_row_shape_error(m):
    """A sentence naming what is wrong with this new-row manifest, or None.

    `recover_op` runs `_validate_manifest_paths` on every op type outside its
    allowlist, and the comment above that allowlist records why: without it a
    damaged manifest raised a bare KeyError out of recover, and main() catches
    only Refusal and LayoutError - so the user got a traceback from the one
    command whose job is to get them unstuck. Adding 'new-row' to that allowlist
    would have re-opened the hole, because the move-shaped validator cannot read
    a new-row manifest. This is the replacement, shaped for this op type.

    NEVER RAISES. classify_op calls it too, and classify_op's contract is that
    it never raises - cmd_recover classifies every pending op to print its
    listing, so one damaged manifest would otherwise take down the whole
    diagnostic rather than one line of it.
    """
    rows = m.get("rows")
    if not isinstance(rows, list) or len(rows) != 1:
        return "its 'rows' is not a one-element list"
    r = rows[0]
    if not isinstance(r, dict):
        return "its row is not an object"
    for k in ("name", "dest_path", "post_b64"):
        if not isinstance(r.get(k), str) or not r[k]:
            return "its row has no usable {0!r}".format(k)
    if not isinstance(m.get("store_path"), str) or not m["store_path"]:
        return "it has no usable 'store_path'"
    return None


def classify_op(env, op):
    """Classify a non-terminal op's source/destination/row state and the
    safe recovery resolutions. Rules per spec Recovery classification, plus
    the adversarial-review fixes noted inline (I3, I7, C2).
    """
    m = op.manifest
    status = m["status"]

    if m.get("op_type") == "repoint":
        # "There is no partial state to roll forward" was wrong, and it left
        # every stuck repoint with resolutions: [] - which cmd_recover's gate
        # turns into a refusal. Non-terminal ops are never rotated, so those
        # records sat in the journal permanently, holding `doctor` at exit 1
        # with the only advice being "run repoint again" - which starts a NEW
        # op and closes nothing. Six of them accumulated on one machine.
        #
        # There is a partial state, and the row on disk says which: a write can
        # land and the marker fail to save, exactly as it can for new-row.
        bad = _repoint_shape_error(m)
        if bad:
            return {"status": m["status"], "source": m.get("store_label", "n/a"),
                    "dest": m.get("store_label", "n/a"), "resolutions": [],
                    "drifted_rows": [],
                    "note": "repoint: this operation's record is damaged ({0}), "
                            "so neither direction can be run from it".format(bad)}
        r = m["rows"][0]
        state = _sync_row_drift(r)
        # unreadable is decided BEFORE the pointer is consulted. _sync_row_drift
        # fails closed and reaching past it to a pointer we could not verify
        # would offer `forward` on an image that cannot even be decoded.
        if state == "unreadable":
            said = "exists but could not be read"
            res = ["back"]
        elif state == "absent":
            said = "is gone from the store"
            res = ["back"]
        elif state == "match":
            # A later completed op writing the same bytes looks identical from
            # here. Say which it is, because the two have opposite advice: back
            # reverses OUR write, or declines to reverse SOMEBODY ELSE'S.
            _claim = _repoint_claimed_later(env, m, r)
            if _claim:
                said = ("holds exactly what this operation would write - but "
                        "operation {0} wrote that row after this one, so these "
                        "may be its bytes rather than this operation's"
                        .format(_claim))
                res = ["forward", "back"]
            else:
                said = "holds exactly what this operation would write"
                res = ["forward", "back"]
        elif state == "pristine":
            said = "was not written - the row is exactly as it was before"
            res = ["forward", "back"]
        else:
            # Drifted. THE POINTER IS THE EVIDENCE, not the bytes: a repoint
            # changes one field and the app rewrites the others whenever it
            # opens the session, so a row this op never touched reads as
            # drifted within a day.
            #
            # `forward` is NOT offered here even when the pointer says the write
            # never landed. execute_repoint_op compares BYTES and refuses a row
            # that changed since planning - so offering it would advertise a
            # resolution that always refuses, and send the user back to the
            # re-run loop this branch exists to break.
            landed = _repoint_landed(m, r)
            if landed is False:
                said = ("was not written - the row still opens what it opened "
                        "before, so there is nothing to undo")
            elif landed is True:
                said = ("was written, and something has changed the row since - "
                        "most likely the app")
            else:
                said = ("has changed since this ran, and this operation cannot "
                        "tell where it now points")
            res = ["back"]
        return {"status": m["status"], "source": m.get("store_label", "n/a"),
                "dest": m.get("store_label", "n/a"), "resolutions": res,
                "drifted_rows": [],
                "note": "repoint: the row {0}; {1}".format(
                    said,
                    "back closes this operation and leaves the row alone"
                    if res == ["back"] else
                    "forward completes it, back closes it without touching the "
                    "row - reversing it would undo that later operation"
                    if _repoint_claimed_later(env, m, r) else
                    "forward completes it, back puts the row back as it was")}
    if m.get("op_type") == "new-row":
        # Unlike repoint, there IS a partial state worth resolving: the row may
        # or may not have landed before the crash. Both directions are real -
        # forward finishes the write, back removes what landed - so offer them
        # rather than returning the empty list repoint returns.
        bad = _new_row_shape_error(m)
        if bad:
            # Damaged record. Say so and offer NOTHING: every resolution below
            # dereferences the row, so advertising them would invite exactly the
            # traceback the shape check exists to prevent.
            return {"status": m["status"], "source": m.get("store_label", "n/a"),
                    "dest": m.get("store_label", "n/a"), "resolutions": [],
                    "drifted_rows": [],
                    "note": "new-row: this operation's record is damaged ({0}), "
                            "so neither direction can be run from it".format(bad)}
        r = m["rows"][0]
        # Map the ACTUAL state, all five of them. `== "match"` collapsed four
        # states into "was not written", so a row the app had already reopened
        # and rewritten - the plan's own stated risk - was reported as never
        # written while sitting on disk. That note is what the user reads to
        # choose a direction, and it was wrong in the direction that invites a
        # careless `forward`.
        state = _sync_row_drift(r)
        said = {"match": "was written",
                "absent": "was not written",
                "drifted": "was written and something has since changed it",
                "pristine": "was written and something has since changed it",
                "unreadable": "exists but could not be read"}.get(
                    state, "is in an unrecognized state ({0})".format(state))
        return {"status": m["status"], "source": m.get("store_label", "n/a"),
                "dest": m.get("store_label", "n/a"),
                "resolutions": ["forward", "back"], "drifted_rows": [],
                # The guidance is state-aware too. An earlier draft made `said`
                # accurate and then told every reader "forward finishes creating
                # it" - false in the two states the five-way map exists to
                # detect, because a landed-and-changed row is precisely what
                # forward now refuses.
                "note": "new-row: the row {0}; {1}".format(
                    said,
                    "back closes this operation and leaves the row alone"
                    if state in ("drifted", "pristine", "unreadable") else
                    "forward finishes creating it, back removes it only if it "
                    "still matches what this op wrote")}
    if m.get("op_type") == "sync":
        return classify_sync_op(env, op)
    if m.get("op_type") == "retitle":
        return classify_retitle_op(env, op)
    if m.get("op_type") == "converge":
        return classify_converge_op(env, op)

    def _src_state():
        transcript_present = os.path.isfile(m["source_transcript"])
        if not transcript_present:
            if status == "committed":
                # I8 idempotency: a crash (or a prior partial recover) can
                # have already deleted the source transcript before this
                # classification runs. That is evidence deletion already
                # progressed, not drift - tolerate it here and let the
                # sidecar/dest checks below decide the rest.
                pass
            else:
                return "missing"
        else:
            got, gsize = sha256_file(m["source_transcript"])
            if got != m["transcript_sha256"] or gsize != m["transcript_size"]:
                return "drifted"
        if status == "committed" and m.get("sidecar_source"):
            for e in m["sidecar_inventory"]:
                p = os.path.join(m["sidecar_source"], *e["rel"].split("/"))
                if not os.path.isfile(p):
                    continue  # I8: already deleted - tolerated
                got, gsize = sha256_file(p)
                if got != e["sha256"] or gsize != e["size"]:
                    return "drifted"
        return "pre"

    def _dest_sidecar_ok(e):
        p = os.path.join(m["sidecar_dest"], *e["rel"].split("/"))
        if not os.path.isfile(p):
            return False
        got, gsize = sha256_file(p)
        return got == e["sha256"] and gsize == e["size"]   # minor: size, not just hash

    def _dest_state():
        if not os.path.exists(m["dest_transcript"]):
            return "absent"
        got, gsize = sha256_file(m["dest_transcript"])
        sidecars_ok = all(_dest_sidecar_ok(e) for e in m["sidecar_inventory"]) \
            if m.get("sidecar_dest") else True
        if got == m["transcript_sha256"] and gsize == m["transcript_size"] and sidecars_ok:
            return "intact"
        if gsize > m["transcript_size"] and sidecars_ok and \
                is_prefix_of(m["transcript_sha256"], m["transcript_size"], m["dest_transcript"]):
            return "grown"
        return "drifted"

    src, dest = _src_state(), _dest_state()
    # C1: rows are inspected here too so the CLI can warn about a drifted
    # row before the user even picks a direction; the actual write-vs-refuse
    # decision for a "copied"/"rewriting" forward happens in
    # _forward_rewrite_and_commit, not here.
    drifted_rows = [r["path"] for r in m["rows"] if _row_state(r) == "drifted"]

    if status in ("journaled", "copying"):
        dest_label = "partial-scratch" if dest != "absent" else "absent"
        if src != "pre":
            # I7: the scratch-deletion rule assumes the source is still
            # pristine. If it has vanished or drifted, the partial
            # destination might be the only remaining copy of the data -
            # never delete it, and never resume the copy automatically
            # either (it would read from an unverified source).
            return {"status": status, "source": src, "dest": dest_label, "resolutions": [],
                    "drifted_rows": drifted_rows,
                    "note": "source is {0}; the partial destination may be the only "
                            "remaining copy - refusing to delete it or resume "
                            "automatically".format(src)}
        return {"status": status, "source": src, "dest": dest_label,
                "resolutions": ["back", "forward"], "drifted_rows": drifted_rows,
                "note": "destination is tool-owned scratch at this phase"}
    if status == "aborting":
        if drifted_rows:
            # I3: a drifted row makes the rollback itself impossible to
            # complete automatically - offering "back" forever (which will
            # only raise the same Refusal every time) is a dead end.
            return {"status": status, "source": src, "dest": dest, "resolutions": [],
                    "drifted_rows": drifted_rows,
                    "note": "row(s) changed unexpectedly during rollback ({0}); "
                            "automatic recovery cannot proceed - resolve manually"
                            .format(", ".join(drifted_rows))}
        # C1(c): mirror the journaled/copying branch's source gate. A
        # hash-gated (non-scratch) "back" deletes the destination only after
        # verifying the source is still "pre" (C1a) - if it is not, and this
        # op has not already committed to keeping the destination
        # (abort_keep_dest), automatic recovery cannot know in advance
        # whether "back" will complete or refuse, and offering it forever
        # would be the same dead end as a drifted row.
        if src != "pre" and not m.get("abort_keep_dest"):
            return {"status": status, "source": src, "dest": dest, "resolutions": [],
                    "drifted_rows": drifted_rows,
                    "note": "source is {0} and the destination was never confirmed "
                            "kept; refusing to resolve automatically - resolve "
                            "manually".format(src)}
        note = "completing an interrupted rollback"
        if dest == "drifted":
            # I4: a drifted destination is never deleted either way - the
            # hash-gate in _abort only ever deletes a dest file that still
            # matches its journaled hash, so "back" remains safe to offer;
            # it will just keep (and report) the drifted file rather than
            # touch it.
            note += "; destination has drifted since it was journaled and will " \
                    "be kept, not deleted"
        return {"status": status, "source": src, "dest": dest, "resolutions": ["back"],
                "drifted_rows": drifted_rows, "note": note}
    if status in ("copied", "rewriting"):
        if dest == "grown":
            return {"status": status, "source": src, "dest": dest, "resolutions": ["forward"],
                    "drifted_rows": drifted_rows,
                    "note": "destination has post-crash growth; it will never be deleted"}
        if dest != "intact":
            # C2: only offer "forward" when finishing can actually succeed.
            # A genuinely drifted (or vanished) destination can never pass
            # the finish gate, so offering "forward" here would let the
            # rows get flipped to point at a bad copy before discovering
            # that. Neither direction is safe automatically - both copies
            # are kept and the row is never touched.
            return {"status": status, "source": src, "dest": dest, "resolutions": [],
                    "drifted_rows": drifted_rows,
                    "note": "destination no longer contains a verifiable copy; both "
                            "copies are kept - resolve manually"}
        return {"status": status, "source": src, "dest": dest,
                "resolutions": ["back", "forward"], "drifted_rows": drifted_rows, "note": ""}
    if status == "committed":
        if src != "pre":
            return {"status": status, "source": src, "dest": dest, "resolutions": [],
                    "drifted_rows": drifted_rows,
                    "note": "source changed after commit; resolve manually - both copies kept"}
        if dest in ("intact", "grown"):
            return {"status": status, "source": src, "dest": dest, "resolutions": ["forward"],
                    "drifted_rows": drifted_rows,
                    "note": "finishing means deleting the stale source duplicate"}
        return {"status": status, "source": src, "dest": dest, "resolutions": [],
                "drifted_rows": drifted_rows,
                "note": "destination no longer contains the copy; keeping the source"}
    return {"status": status, "source": src, "dest": dest, "resolutions": [],
            "drifted_rows": drifted_rows, "note": "terminal"}


def _forward_rewrite_and_commit(env, op, c):
    """Finish rolling a 'copied'/'rewriting' op forward: gate first (C2),
    classify every row's CURRENT bytes before writing any of them (C1), and
    only then mutate - either everything proceeds together or nothing does.
    `c` is the classify_op result already computed by the caller before any
    mutation, so its source/dest snapshot is still valid here.
    """
    m = op.manifest
    if c["source"] != "pre" or c["dest"] not in ("intact", "grown"):
        raise Refusal("cannot roll op {0} forward: {1}".format(m["op_id"], c["note"]))

    row_writes = []
    drifted = []
    for r in m["rows"]:
        state = _row_state(r)
        if state == "pre":
            row_writes.append(r)
        elif state == "drifted":
            drifted.append(r["path"])
        # state == "post": already applied, nothing to do
    if drifted:
        raise Refusal("cannot roll op {0} forward: row(s) changed unexpectedly since "
                      "the journaled images ({1}); nothing was written. Resolve "
                      "manually, then retry recover.".format(m["op_id"], ", ".join(drifted)))

    for r in row_writes:
        atomic_write(r["path"], unb64(r["post_b64"]))
        r["rewritten"] = True
        save_manifest(op)
    set_status(op, "committed")
    return _finish_committed(env, op)


def recover_op(env, op, direction):
    # I5: recover mutates a journaled op exactly like run_move does, so it
    # needs the same single-instance lock - two concurrent recover attempts
    # (or a recover racing a live move) must not interleave.
    acquire_lock(env, "recover-" + op.manifest["op_id"])
    try:
        # I4: validate containment before ANY mutation, including the
        # scratch-dest unlink below - not just on execute_op's fresh runs.
        # A sync manifest carries no source_transcript/sidecar_inventory/
        # row["path"] for this validator to inspect - execute_sync_op does
        # its own containment check inline, per-row, instead.
        # Neither sync nor repoint carries the move-shaped keys this validates
        # (source_transcript and friends). Running it on a repoint manifest
        # raised a bare KeyError out of recover - and main() catches only
        # Refusal/LayoutError, so the user got a traceback from the one command
        # that exists to get them unstuck.
        if op.manifest.get("op_type") not in ("sync", "repoint", "new-row",
                                              "retitle", "converge"):
            _validate_manifest_paths(env, op.manifest)
        elif op.manifest.get("op_type") == "new-row":
            # The allowlist above skips the move-shaped validator, so this op
            # type brings its own. Without it the branch below dereferences
            # m["rows"][0]["name"] and m["store_path"] straight out of a file a
            # user can edit, and a bare KeyError escapes main().
            bad = _new_row_shape_error(op.manifest)
            if bad:
                raise Refusal(
                    "the record for {0} is damaged ({1}), so neither direction "
                    "can be run from it. Nothing was changed. Any row it created "
                    "is still on disk - 'doctor' lists conversations that no "
                    "account points at.".format(op.manifest.get("op_id"), bad))

        c = classify_op(env, op)
        if direction not in c["resolutions"]:
            raise Refusal("'{0}' is not a safe resolution for op {1} ({2}); options: {3}"
                          .format(direction, op.manifest["op_id"], c["note"],
                                  c["resolutions"] or "none - manual intervention"))
        m = op.manifest
        if m.get("op_type") == "new-row":
            if direction == "forward":
                # The preflight moved out of execute_new_row_op, so this arm
                # owns it - and only for a row that did NOT land. A landed row
                # needs no re-validation (its facts are already committed) and
                # no mutation guard (it writes only the journal).
                state = _sync_row_drift(m["rows"][0])
                if state not in ("match", "absent"):
                    # The row DID land and is not what we wrote. Refuse HERE,
                    # accurately, rather than letting the preflight speak: every
                    # one of its refusals ends "Nothing was written", which is
                    # false about a row sitting on disk, and it sends the user to
                    # "re-run to replan" - which plan_new_row then refuses,
                    # because that row already opens the session. Two refusals,
                    # neither of them true, on exactly the path a user reaches
                    # weeks later when the transcript has aged out too.
                    raise Refusal(
                        "there is already a row at {0!r} and it is not what this "
                        "operation wrote ({1}); forward cannot finish a row that "
                        "is already there and already different, and nothing was "
                        "changed. Most likely the app rewrote it when it reopened "
                        "the session. 'recover --back' closes this operation and "
                        "leaves the row alone.".format(m["rows"][0]["name"], state))
                if state != "match":
                    _new_row_preflight(env, m)
                set_status(op, "journaled")
                final = execute_new_row_op(env, op)
                rotate_ops(env)
                return final
            r = m["rows"][0]
            # 'back' must always terminate - it is the only exit from a stuck
            # op - so a row it cannot safely remove is SKIPPED rather than
            # refused, the same asymmetry _sync_delete_targets documents against
            # undo. But skipping SILENTLY would report a clean rollback while
            # leaving a row on disk that nothing afterwards looks for: doctor's
            # vanished-row check reads only 'completed' ops, so a rolled_back op
            # with a surviving row is invisible everywhere. Record it instead.
            residue = None
            state = _sync_row_drift(r)
            if state == "match":
                # The running-app guard (RULING 4) belongs HERE, not at the top
                # of the arm. Until this task recover_op called it for no op type
                # at all, so recovering backward could delete a row out from
                # under a running app rewriting that store. But only this branch
                # deletes anything: on every other state 'back' is a journal-only
                # close, and guarding that would put the ONLY exit from a stuck
                # operation behind closing the desktop app - for an operation
                # that touched no bytes. Same asymmetry the forward arm has, for
                # the same reason.
                _guard_mutation(env, "remove a row from", NEW_ROW_STORE,
                                because=NEW_ROW_GUARD_WHY)
                try:
                    # Containment is re-established here, not inherited. This
                    # arm deletes a path out of a journal file, and a journal
                    # can be edited or corrupted; the write and undo paths both
                    # check, so the recovery path must too.
                    real = ensure_contained(r["dest_path"], [m["store_path"]])
                    if os.path.dirname(real) != os.path.realpath(m["store_path"]):
                        raise LayoutError("not a direct child of the store")
                    os.unlink(r["dest_path"])
                except (OSError, LayoutError) as exc:
                    residue = "could not remove {0!r}: {1}".format(r["name"], exc)
            elif state != "absent":
                # "couldn't read it" and "it changed" are different facts about a
                # file left on disk. undo_new_row and classify_op both bother to
                # tell them apart; this was the one place in the new code that
                # collapsed them, which is the module's posture inverted.
                why = ("it exists but could not be read" if state == "unreadable"
                       else "it no longer holds what this op wrote ({0})"
                            .format(state))
                residue = "left {0!r} in place - {1}".format(r["name"], why)
            if residue:
                m["rollback_residue"] = residue
                save_manifest(op)
            set_status(op, "rolled_back")
            rotate_ops(env)
            return "rolled_back"
        if m.get("op_type") == "retitle":
            bad = _retitle_shape_error(m)
            if bad:
                raise Refusal(
                    "the record for {0} is damaged ({1}), so neither direction "
                    "can be run from it. Nothing was changed."
                    .format(m.get("op_id"), bad))
            if direction == "forward":
                if all(_sync_row_drift(r) == "match" for r in m["rows"]):
                    # Every write landed; only the completion marker is
                    # missing. Journal-only, so no mutation guard and no
                    # recheck - the same asymmetry the repoint arm documents:
                    # re-validating would block finishing bookkeeping for
                    # writes that already succeeded.
                    for r in m["rows"]:
                        r["written"] = True
                    save_manifest(op)
                    set_status(op, "completed")
                    rotate_ops(env)
                    return "completed"
                _guard_mutation(env, "retitle rows in", NEW_ROW_STORE,
                                because=RETITLE_GUARD_WHY)
                # The spec's contract for forward: complete the remaining
                # writes from the journaled plan, RE-RUNNING THE APPLY-TIME
                # CHECKS FIRST against the store as it now is.
                _retitle_recheck(env, m)
                set_status(op, "journaled")
                final = execute_retitle_op(env, op)
                rotate_ops(env)
                return final
            # 'back' must always terminate - it is the only exit from a stuck
            # op - so rows it cannot verify are SKIPPED and recorded, never
            # refused (undo is the all-or-nothing arm; this one closes ops).
            # Restores go by disk evidence, not the `written` flag alone: a
            # hard kill between atomic_write and save_manifest leaves a row
            # holding this op's post-image with the flag unset, and walking
            # past it would report a clean rollback that restored nothing -
            # the same window _sync_delete_targets documents.
            roots = _retitle_roots(env)
            restorable, skipped = [], []
            for r in m["rows"]:
                try:
                    _retitle_contained(r, roots)
                except LayoutError:
                    if r.get("written"):
                        raise
                    continue                 # never landed and not ours to touch
                state = _sync_row_drift(r)
                if not r.get("written") and state != "match":
                    continue                 # never landed; nothing to reverse
                if state == "match":
                    claim = _retitle_claimed_elsewhere(env, m, r)
                    if claim:
                        skipped.append(
                            "{0} (operation {1} also records writing these "
                            "bytes, so putting ours back could reverse its "
                            "work)".format(r.get("name"), claim))
                        continue
                    try:
                        restorable.append((r["dest_path"], _sync_pre_image(r)))
                    except (KeyError, ValueError):
                        skipped.append("{0} (its journaled pre-image cannot be "
                                       "read)".format(r.get("name")))
                elif state == "pristine":
                    continue                 # already exactly as it was
                elif state == "absent":
                    skipped.append(
                        "{0} (gone from the store - restoring it would "
                        "resurrect a row that account removed)"
                        .format(r.get("name")))
                elif state == "drifted":
                    skipped.append(
                        "{0} (no longer holds what this op wrote - most likely "
                        "the app)".format(r.get("name")))
                else:
                    skipped.append("{0} (exists but could not be read)"
                                   .format(r.get("name")))
            if restorable:
                # Guard only when something will actually be written - on
                # every other state 'back' is a journal-only close, and
                # putting the only exit from a stuck op behind closing the
                # desktop app, for an operation about to touch no bytes, is
                # the trap the repoint and new-row arms both refused.
                _guard_mutation(env, "restore retitled rows in", NEW_ROW_STORE,
                                because=RETITLE_GUARD_WHY)
                _sync_restore_all(restorable)
            if skipped:
                m["abort_reason"] = (
                    "back restored {0} row(s) from their journaled preimages; "
                    "left {1} untouched: {2}".format(
                        len(restorable), len(skipped), ", ".join(skipped)))
            elif not restorable:
                m["abort_reason"] = (
                    "back restored nothing - no row still holds what this "
                    "operation wrote, so there was nothing to take back. The "
                    "op is closed; re-run retitle if the rename is still "
                    "wanted.")
            set_status(op, "rolled_back")
            rotate_ops(env)
            return "rolled_back"
        if m.get("op_type") == "converge":
            bad = _converge_shape_error(m)
            if bad:
                raise Refusal(
                    "the record for {0} is damaged ({1}), so neither direction "
                    "can be run from it. Nothing was changed."
                    .format(m.get("op_id"), bad))
            if direction == "forward":
                pending = [r for r in m["rows"]
                           if not r.get("written") and not r.get("skipped")]
                if all(_sync_row_drift(r) == "match" for r in pending):
                    # Every write landed (or none were pending); only the
                    # completion marker is missing. Journal-only, so no
                    # mutation guard and no recheck - the same asymmetry the
                    # repoint and retitle arms document.
                    for r in pending:
                        r["written"] = True
                    save_manifest(op)
                    set_status(op, "completed")
                    rotate_ops(env)
                    return "completed"
                _guard_mutation(env, "create rows in", NEW_ROW_STORE,
                                because=NEW_ROW_GUARD_WHY)
                # Forward recovery is a FRESH RE-EVALUATION (the spec's
                # contract, stated so recovery is not read as replay): the
                # guards and per-pair checks re-run against the store as it
                # now is. A pair whose destination gained a row in the window
                # becomes an already_present skip; a pair that held at plan
                # time is not in `rows` and stays unwritten even if its
                # collision has since cleared.
                _converge_recheck(env, m)
                save_manifest(op)         # the skips just decided are durable
                set_status(op, "journaled")
                final = execute_converge_op(env, op)
                rotate_ops(env)
                return final
            # 'back' must always terminate - it is the only exit from a stuck
            # op - so a row it cannot verify is SKIPPED and recorded, never
            # refused. Deletions go by disk evidence, not the `written` flag
            # alone: a hard kill between atomic_write and save_manifest
            # leaves a row holding this op's bytes with the flag unset (the
            # same window the retitle arm documents). The rows are pointers;
            # the conversations keep their rows elsewhere.
            deletable, residue = [], []
            for r in m["rows"]:
                state = _sync_row_drift(r)
                if state == "match":
                    deletable.append(r)
                elif state == "absent":
                    continue                  # never landed, or already gone
                elif r.get("written"):
                    why = ("it exists but could not be read"
                           if state == "unreadable"
                           else "it no longer holds what this op wrote ({0})"
                                .format(state))
                    residue.append("left {0!r} in place - {1}".format(
                        r.get("name"), why))
                # not written and not holding our bytes: not ours to touch
            if deletable:
                # Guard only when something will actually be deleted - on
                # every other state 'back' is a journal-only close, and
                # putting the only exit from a stuck op behind closing the
                # desktop app, for an operation about to touch no bytes, is
                # the trap the sibling arms all refused.
                _guard_mutation(env, "remove rows from", NEW_ROW_STORE,
                                because=NEW_ROW_GUARD_WHY)
                for r in deletable:
                    try:
                        real = ensure_contained(r["dest_path"],
                                                [r["store_path"]])
                        if os.path.dirname(real) != \
                                os.path.realpath(r["store_path"]):
                            raise LayoutError("not a direct child of the "
                                              "store")
                        os.unlink(r["dest_path"])
                    except (OSError, LayoutError) as exc:
                        residue.append("could not remove {0!r}: {1}".format(
                            r.get("name"), exc))
            if residue:
                m["rollback_residue"] = "; ".join(residue)
                save_manifest(op)
            set_status(op, "rolled_back")
            rotate_ops(env)
            return "rolled_back"
        if m.get("op_type") == "repoint":
            bad = _repoint_shape_error(m)
            if bad:
                raise Refusal(
                    "the record for {0} is damaged ({1}), so neither direction "
                    "can be run from it. Nothing was changed."
                    .format(m.get("op_id"), bad))
            r = m["rows"][0]
            state = _sync_row_drift(r)
            if direction == "forward":
                if state == "match":
                    # Journal-only: the write landed and the marker did not
                    # save. No store byte changes, so no mutation guard - the
                    # same asymmetry the new-row arm documents. Re-validating
                    # here would block finishing a write that already succeeded.
                    r["written"] = True
                    save_manifest(op)
                    set_status(op, "completed")
                    rotate_ops(env)
                    return "completed"
                set_status(op, "journaled")
                final = execute_repoint_op(env, op)
                rotate_ops(env)
                return final
            # 'back' must always terminate - it is the only exit from a stuck
            # op, and having no exit at all is what put six of these in one
            # journal. What it does depends on whether our write is still in
            # effect, which the POINTER answers and the bytes do not.
            residue = None
            claimant = _repoint_claimed_later(env, m, r) if state == "match" else None
            declined = None
            if claimant:
                # Byte-identical is not proof WE wrote it. A later repoint of
                # the same row at the same target writes the same bytes, and
                # restoring our pre-image would silently reverse that completed
                # operation - whose own undo then refuses, because the row no
                # longer holds what it wrote. Close the record; touch nothing.
                #
                # NOT residue. Residue means "we left something we wrote that
                # nothing else tracks", and doctor reports it because nothing
                # else would. Here the row belongs to an operation that IS
                # tracked and IS recorded as completed, and leaving it is the
                # correct outcome rather than an anomaly - so this is written
                # under its own key. Filing it as residue made doctor exit 1
                # permanently and told the user to delete a row they wanted.
                declined = ("left {0!r} alone - operation {1} wrote that row "
                            "after this one and is still recorded as completed, "
                            "so putting it back would silently reverse it"
                            .format(r.get("name"), claimant))
            elif state == "match":
                _guard_mutation(env, "repoint")
                try:
                    real = ensure_contained(r["dest_path"], [m["store_path"]])
                    if os.path.dirname(real) != os.path.realpath(m["store_path"]):
                        raise LayoutError("not a direct child of the store")
                    atomic_write(r["dest_path"], _sync_pre_image(r))
                except (OSError, LayoutError, ValueError, KeyError) as exc:
                    residue = ("could not put {0!r} back: {1}"
                               .format(r.get("name"), exc))
            elif state == "pristine" or _repoint_landed(m, r) is False:
                pass          # never took effect; nothing to undo, just close
            else:
                landed = _repoint_landed(m, r)
                why = ("it exists but could not be read" if state == "unreadable"
                       else "it is gone from the store" if state == "absent"
                       else "it still opens what this op pointed it at, and no "
                            "longer holds the bytes this op wrote, so putting "
                            "it back would discard whatever changed it"
                       if landed is True
                       else "it changed since this ran and this operation "
                            "cannot tell where it now points, so putting it "
                            "back could discard something it never wrote")
                residue = "left {0!r} alone - {1}".format(r.get("name"), why)
            if residue:
                m["rollback_residue"] = residue
            if declined:
                m["rollback_declined"] = declined
            if residue or declined:
                save_manifest(op)
            set_status(op, "rolled_back")
            rotate_ops(env)
            return "rolled_back"
        if m.get("op_type") == "sync":
            # direction is already guaranteed to be a member of c["resolutions"]
            # by the check above, and classify_sync_op only ever offers
            # "back", "forward" or both (never anything else) for a
            # non-terminal sync op - so no further validation is needed here.
            if direction == "back":
                # Always available for a non-terminal sync, because drift is
                # only one of the ways forward can be permanently blocked (an
                # I/O or layout failure leaves the pending row absent, which
                # looks like no drift at all). Unlike undo_sync's
                # all-or-nothing, back must
                # always terminate - refusing over a written row that also
                # drifted or turned unreadable would recreate the exact
                # dead end this resolution exists to close (recover back
                # refuses, recover forward refuses, undo refuses:
                # permanently stuck). So a row this op cannot verify is
                # SKIPPED, never deleted, and the rest are reversed; a
                # blocking pending row is still never considered, because
                # its state is drifted/deleted/unreadable rather than the
                # "match" that would prove this op wrote it.
                (drifted, unreadable, removable,
                 restorable, claimed) = _sync_delete_targets(env, m)
                _sync_reverse_all(removable, restorable)
                # `claimed` rows belong to another op's journal, so back leaves
                # them alone - the same skip-rather-than-refuse rule the other
                # unverifiable states get here, for the same reason: back must
                # always reach a terminal status.
                skipped = drifted + unreadable + claimed
                # Restores are reversals too: an interrupted update whose rows
                # were all refreshed removes nothing yet undoes everything, and
                # reporting that as "removed nothing" would send the user off to
                # forward-then-undo an op that is already reversed.
                reversed_n = len(removable) + len(restorable)
                # Same reporting mechanism _abort already uses for move -
                # cmd_recover's existing _print_abort_reason picks this up for
                # free once status is non-"completed".
                if skipped:
                    why = _drift_clause(drifted, unreadable)
                    if claimed:
                        why += (" and " if why else "") + (
                            "were written again by a later operation, which owns "
                            "them now and would have its work undone ({0})".format(
                                ", ".join(claimed)))
                    op.manifest["abort_reason"] = (
                        "back reversed {0} row(s) it could verify; left {1} "
                        "untouched because they {2}".format(
                            reversed_n, len(skipped), why))
                elif not reversed_n:
                    # Say so out loud rather than printing a bare
                    # "rolled_back". Narrower than it used to be: this branch
                    # once also caught the hard-kill case, where rows landed on
                    # disk but the manifest never recorded them and 'back'
                    # walked past every one - so it named the forward route as
                    # the way to pick them up. _sync_delete_targets now reads
                    # the disk rather than trusting that flag alone, so those
                    # rows are reversed here instead. What is left is the
                    # genuine nothing-to-do case.
                    op.manifest["abort_reason"] = (
                        "back reversed nothing - no row in this op's destination "
                        "still holds what it wrote, so there was nothing to take "
                        "back. The op is closed; re-run sync if the rows are "
                        "still wanted.")
                set_status(op, "rolled_back")
                rotate_ops(env)
                return "rolled_back"
            # forward: re-enter execute_sync_op to finish the remaining writes.
            set_status(op, "journaled")
            final = execute_sync_op(env, op)
            if final == "completed":
                rotate_ops(env)
            return final
        if direction == "back":
            _abort(env, op)
            return "rolled_back"
        # forward
        if m["status"] in ("journaled", "copying"):
            for path, _, _ in _dest_files(m):        # scratch rule: clear partials
                if os.path.exists(path):
                    os.unlink(path)
            set_status(op, "journaled")               # minor: history entry, not a raw write
            final = execute_op(env, op)
        elif m["status"] in ("copied", "rewriting"):
            final = _forward_rewrite_and_commit(env, op, c)
        else:  # committed
            final = _finish_committed(env, op)
        if final == "completed":
            # C2: a recovered op can be either direction of the engine, not
            # just a forward move - the moved-log entry (and any follow-on
            # bookkeeping) must be derived from what THIS op actually is,
            # not hardcoded to "move".
            if m.get("op_type") == "undo":
                append_moved_log(env, {"kind": "undo", "session_id": m["session_id"],
                                       "at": env.now()})
                _mark_undo_of_undone(env, m)
            else:
                append_moved_log(env, {"kind": "move", "session_id": m["session_id"],
                                       "from": m["source_transcript"], "to": m["dest_transcript"],
                                       "at": env.now()})
            rotate_ops(env)
        return final
    finally:
        release_lock(env)


def _mark_undo_of_undone(env, undo_manifest):
    """After a recovered undo op reaches 'completed', mark the ORIGINAL op
    it reversed as 'undone' - mirroring what run_undo does on its own
    successful path. Without this, a crash between an undo op's 'committed'
    status and run_undo's own set_status(prior_op, "undone") call would
    leave the original move op stuck reading 'completed' forever, even
    though its transcript has actually moved back home. Silently no-ops if
    the referenced op can no longer be found (e.g. already rotated away by
    a much later cleanup) rather than failing an otherwise-successful
    recovery over bookkeeping for an op that is long gone either way.
    """
    prior_id = undo_manifest.get("undo_of")
    if not prior_id:
        return
    for other in list_ops(env):
        if other.manifest.get("op_id") == prior_id:
            set_status(other, "undone")
            return


def _finish_committed(env, op):
    """Finish a 'committed' op by deleting the now-redundant source copy.

    Uses the same delete helpers and failure contract as execute_op's final
    commit step (never rmtree): the same un-inventoried-file and
    claude-running guards (I6), then inventoried sidecar files, then their
    now-empty directories, then the transcript LAST. A file already missing
    (I8: deletion already progressed - see classify_op) is simply skipped
    rather than treated as a failure. Any real delete failure, or either
    guard tripping, raises a Refusal naming the paths and leaves the op at
    'committed' (non-terminal) for a later recover to retry - it is never
    silently swallowed.
    """
    m = op.manifest
    c = classify_op(env, op)
    if c["source"] != "pre" or c["dest"] not in ("intact", "grown"):
        raise Refusal("cannot finish op {0}: {1}".format(m["op_id"], c["note"]))

    # I6(a): a live Claude process could be actively appending to the
    # source right now; deleting it out from under a running process is
    # exactly the hazard the pre-move guard exists to prevent.
    running = claude_running(env)
    if running:
        raise Refusal("cannot finish op {0}: Claude appears to be running ({1}). "
                      "Close it, then retry recover.".format(m["op_id"], ", ".join(sorted(set(running))[:3])))

    if m.get("sidecar_source") and os.path.isdir(m["sidecar_source"]):
        # I6(b): same C1 guard as execute_op's own commit step - a file
        # that appeared in the source sidecar after commit was never
        # journaled and must never be destroyed; both copies are kept.
        inv_rels = {e["rel"] for e in m["sidecar_inventory"]}
        extra = []
        for dirpath, dirnames, filenames in os.walk(m["sidecar_source"]):
            for name in filenames:
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, m["sidecar_source"]).replace(os.sep, "/")
                if rel not in inv_rels:
                    extra.append(full)
        if extra:
            raise Refusal("cannot finish op {0}: untracked file(s) appeared in the "
                          "source sidecar since commit ({1}); both copies are kept - "
                          "resolve manually".format(m["op_id"], ", ".join(extra)))

        remaining = [e for e in m["sidecar_inventory"]
                     if os.path.isfile(os.path.join(m["sidecar_source"], *e["rel"].split("/")))]
        failures = _delete_inventoried_files(m["sidecar_source"], remaining)
        if failures:
            raise Refusal("cannot finish op {0}: could not remove {1}".format(
                m["op_id"], ", ".join(p for p, _ in failures)))
        _rmdirs_bottom_up(m["sidecar_source"])

    if os.path.isfile(m["source_transcript"]):
        try:
            os.unlink(m["source_transcript"])
        except OSError as exc:
            raise Refusal("cannot finish op {0}: could not remove source transcript "
                          "{1}: {2}".format(m["op_id"], m["source_transcript"], exc))

    set_status(op, "completed")
    return "completed"


def clear_stale_lock(env):
    if lock_is_stale(env):
        release_lock(env)
        return True
    return False


# ------------------------------------------------- 6. commands: list, doctor
_UUID_RE = re.compile(r"\b([0-9a-f]{8})-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE)


# Content redaction, as opposed to the machine redaction `redact` does below.
#
# `redact` has always replaced the home directory and shortened uuid-shaped ids
# - everything that identifies the MACHINE. It has never touched titles, which
# is what identifies the WORK. That gap put a named third party alongside a
# professional-board complaint into a public repo, twice, because output that
# advertises itself as redacted was pasted into documentation.
#
# Titles cannot be recognised by shape the way a path or a uuid can, so this
# cannot be a regex. It builds a substitution table from the store instead:
# every title this machine actually has, and every project folder name, mapped
# to a stable opaque label.
#
# DELIBERATELY UNREADABLE LABELS. `<session-a1b2>` rather than a plausible fake
# title, because a plausible fake read back later is indistinguishable from a
# real one, and the entire failure being fixed here is real titles that looked
# like examples. Hand-written examples should use the fake cast instead; this is
# for pasting REAL output.
#
# Stable across runs (sha256 of the title), so two pasted listings can be
# compared to each other.
_ANONYMIZE = False
_ANON_CACHE = {}
_ANON_MIN = 4


def _anon_label(kind, value):
    return "<%s-%s>" % (kind, hashlib.sha256(value.encode("utf-8")).hexdigest()[:4])


def _anon_pairs(env):
    """(real, label) for every title and project folder, longest first.

    Longest first matters: one title is often a prefix of another - "… (fork)"
    is the common case here - and replacing the short one first would leave the
    tail of the long one exposed in the output.
    """
    key = (env.home, tuple(env.store_candidates), env.projects_root)
    if key in _ANON_CACHE:
        return _ANON_CACHE[key]
    seen = {}
    try:
        disc = discover_stores(env)
        rows, _ = load_rows(disc.roots)
    except Exception:
        rows = []
    for r in rows:
        data = r.data if isinstance(r.data, dict) else {}
        t = (data.get("title") or "").strip()
        if len(t) >= _ANON_MIN:
            seen[t] = _anon_label("session", t)
    # WHOLE cwd paths, not name fragments. The first version split the encoded
    # folder name on "-" and mapped the last piece, which turned
    # "…\Northwind Plastic Surgery" into
    # "…\Northwind Plastic <project-022e>" - anonymised-looking output
    # that still named the client. A partial replacement is worse than none,
    # because it reads as safe.
    for r in rows:
        c = (r.cwd or "").strip()
        if len(c) >= _ANON_MIN:
            # WHOLE paths only. Mapping the basename too seemed thorough and
            # was actively harmful: "craig" and "Projects" are basenames, so
            # the line pass rewrote 'craig@foundryside.co' into nonsense.
            # Over-matching corrupts the report; the structured pass below is
            # what actually guarantees coverage.
            seen.setdefault(c, _anon_label("project", c))
    pairs = sorted(seen.items(), key=lambda kv: -len(kv[0]))
    _ANON_CACHE[key] = pairs
    return pairs


_ANON_FIELDS = ("title", "cwd", "project", "folder", "new_title", "old_title")
_ANON_EMAIL_FIELDS = ("label", "email", "account_email")


def anonymize_report(env, obj):
    """Anonymise a report STRUCTURE, before anything formats it.

    The line-level pass below cannot see a title that formatting has already
    truncated to fit a column - and `list` truncates every one of them, so the
    first version of this feature left most titles untouched while looking like
    it had worked. Replacing the field first means truncation and column widths
    apply to the LABEL, so the output stays aligned and nothing survives being
    cut in half.

    This is also what covers `--json`, which no line-level pass ever reaches.
    """
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            # KEYS as well as values. Reports here are keyed by the thing being
            # counted - per-account tallies by email, duplicate-title groups by
            # the title itself - so anonymising only values left the private
            # string sitting in the key, one line below where it had just been
            # replaced.
            if isinstance(k, str):
                if "@" in k and "." in k:
                    k = _anon_label("account", k)
                else:
                    k = anonymize(env, k)
            if k in _ANON_FIELDS and isinstance(v, str) and v.strip():
                out[k] = anonymize(env, v)
            elif (k in _ANON_EMAIL_FIELDS and isinstance(v, str) and "@" in v):
                # An account address names a person and an organisation. It is
                # not a title, so the substitution table never sees it.
                out[k] = _anon_label("account", v)
            else:
                out[k] = anonymize_report(env, v)
        return out
    if isinstance(obj, list):
        return [anonymize_report(env, v) for v in obj]
    return obj


def anonymize(env, text):
    """Replace every real title and project name with its stable label."""
    for real, label in _anon_pairs(env):
        if real in text:
            text = text.replace(real, label)
    return text


def redact(env, text, keep_ids=False):
    for h in {env.home, os.path.realpath(env.home)}:
        text = text.replace(h, "~")
    if not keep_ids:
        text = _UUID_RE.sub(lambda m: m.group(1) + "…", text)
    if _ANONYMIZE:
        # Last, so a title containing a path fragment is still caught after
        # the home directory has been folded to ~.
        text = anonymize(env, text)
    return text


def gather_list(env, query="", project=None):
    disc = discover_stores(env)
    rows, _ = load_rows(disc.roots)
    items, seen = [], {}
    for r in rows:
        if not r.cli_session_id:
            continue
        if r.cli_session_id not in seen or seen[r.cli_session_id]["last_activity"] < r.last_activity:
            seen[r.cli_session_id] = {"session_id": r.cli_session_id, "title": r.data.get("title") or "",
                                      "cwd": r.cwd, "last_activity": r.last_activity, "listed": True}
    for folder, path in iter_transcripts(env.projects_root):
        sid = os.path.splitext(os.path.basename(path))[0]
        if sid in seen or not path.endswith(".jsonl"):
            continue
        seen[sid] = {"session_id": sid, "title": "", "cwd": first_cwd(path),
                     "last_activity": int(os.path.getmtime(path) * 1000),
                     "listed": False}
    items = list(seen.values())
    if query:
        q = query.lower()
        items = [i for i in items
                 if q in (i["title"] + " " + i["cwd"] + " " + i["session_id"]).lower()]
    if project:
        p = os.path.normpath(os.path.abspath(project)).lower() + os.sep
        items = [i for i in items
                 if os.path.normpath(i["cwd"]).lower() == p.rstrip(os.sep)
                 or os.path.normpath(i["cwd"]).lower().startswith(p)]
    items.sort(key=lambda i: i["last_activity"], reverse=True)
    return items


def cmd_list(env, ns):
    items = gather_list(env, query=getattr(ns, "query", "") or "",
                        project=getattr(ns, "project", None))
    if _ANONYMIZE:
        # Before anything formats or truncates it.
        items = anonymize_report(env, items)
    if ns.json:
        print(json.dumps(items, indent=1))
        return 0
    for i in items:
        line = "{0}  {1:40.40}  {2}".format(i["session_id"], i["title"] or "(no title)", i["cwd"])
        if ns.verbose:
            print(line)
        elif getattr(ns, "full", False):
            print(redact(env, line, keep_ids=True))
        else:
            print(redact(env, line))
    if not items:
        print("no sessions found")
    return 0


RETENTION_HINT_DAYS = 30


def _duplicate_title_groups(env, rows):
    """Conversations that share a title, with pairwise overlap in BOTH directions.

    The friction this reports is real and recurring: every title in this store is
    written by the app's summariser (measured 2026-08-23: titleSource is 'auto' on
    every row carrying it, 'user' on none), so two conversations about the same
    work get the same sentence. Twenty such groups here, spread across five
    months - it is not a one-off.

    BOTH DIRECTIONS, ALWAYS. Containment is asymmetric and reporting one half of
    it is worse than reporting none: if A has 400 turns and B has 200 and every
    one of B's is in A, then "100% contained" and "50% contained" are the same
    fact seen from either end. A reader shown only the first number, without
    being told WHICH conversation it describes, can delete the superset. So every
    pair carries a_in_b and b_in_a, and the caller never has to infer a direction.

    "Not compared" is not "no overlap". _message_fingerprints returns None above
    TRANSCRIPT_COMPARE_MAX_BYTES, and four conversations here - 607 MB between
    them, under one shared title - exceed it. They are marked unmeasured rather
    than being given a 0, which would read as "shares nothing" and invite exactly
    the wrong deletion.

    TWO DIFFERENT CONDITIONS WEAR THE SAME SHAPE, and only one of them is the
    sidebar friction people mean:

      scope "sidebar"       - one account holds two or more rows under this
                              title. That is the clicking-the-wrong-one problem,
                              and removing the redundant member may be right.
      scope "cross-account" - each account holds ONE row under this title, but
                              they open DIFFERENT conversations. No sidebar ever
                              shows two, so there is nothing to declutter -
                              removing a member strips an account's only door to
                              that conversation while the others keep pointing
                              elsewhere.

    An earlier version grouped by title across the whole machine and reported
    both as one thing. It listed nine cross-account groups as duplicates, and a
    cleanup list built from it proposed removing a row that was an account's
    only copy. The user noticed because a title the report called duplicated
    appeared exactly once in the sidebar they were looking at.

    Read-only, and deliberately NOT part of doctor's exit code: two conversations
    sharing a name is normal, not a fault.
    """
    by_title = {}
    for r in rows:
        if not r.cli_session_id:
            continue
        # r.data, not a fresh read_json: load_rows already parsed every row, and
        # re-reading all of them here doubled doctor's runtime for nothing.
        d = r.data
        if not isinstance(d, dict):
            continue
        title = d.get("title")
        if not isinstance(title, str) or not title.strip():
            continue
        entry = by_title.setdefault(title, {"members": {}, "per_account": {}})
        # One entry per CONVERSATION, not per row: the same duplicate exists in
        # every synced account, and listing it three times would treble a report
        # whose whole purpose is to reduce noise.
        if r.cli_session_id not in entry["members"]:
            entry["members"][r.cli_session_id] = (r.path, d)
        # ...but WHICH accounts hold it is what separates a sidebar duplicate
        # from a cross-account disagreement, so that is tracked alongside.
        acct = os.path.basename(os.path.dirname(os.path.dirname(r.path)))
        entry["per_account"].setdefault(acct, set()).add(r.cli_session_id)

    out = []
    for title, entry in by_title.items():
        members, per_account = entry["members"], entry["per_account"]
        if len(members) < 2:
            continue
        # "sidebar" the moment ANY single account holds two of them.
        sidebar = [a for a, s in per_account.items() if len(s) > 1]
        scope = "sidebar" if sidebar else "cross-account"
        sessions, prints = [], {}
        for sid, (rowpath, d) in sorted(members.items()):
            found = find_transcripts(env.projects_root, sid)
            path = found[0] if len(found) == 1 else None
            mb = None
            if path:
                try:
                    mb = round(os.path.getsize(path) / 1e6, 1)
                except OSError:
                    mb = None
            fps = _message_fingerprints(path) if path else None
            prints[sid] = set(fps) if fps is not None else None
            sessions.append({
                "session_id": sid,
                "turns": len(fps) if fps is not None else None,
                "mb": mb,
                "created": d.get("createdAt"),
                # Why it could not be compared, so the reader is never left to
                # guess whether "not compared" means missing or merely huge.
                "unmeasured": ("no transcript" if not path
                               else "too large to compare" if fps is None
                               else None),
            })
        pairs = []
        ids = [s["session_id"] for s in sessions]
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a, b = ids[i], ids[j]
                fa, fb = prints[a], prints[b]
                if fa is None or fb is None:
                    pairs.append({"a": a, "b": b, "a_in_b": None, "b_in_a": None,
                                  "shared": None, "unmeasured": True})
                    continue
                shared = len(fa & fb)
                pairs.append({
                    "a": a, "b": b, "shared": shared,
                    # Read these as: this fraction of A's turns also appear in B.
                    "a_in_b": round(100.0 * shared / len(fa), 1) if fa else None,
                    "b_in_a": round(100.0 * shared / len(fb), 1) if fb else None,
                    "a_unique": len(fa - fb), "b_unique": len(fb - fa),
                    "unmeasured": False,
                })
        # Most-redundant first: a group holding a conversation almost entirely
        # inside a sibling is the one worth a decision, and a group of genuine
        # forks is not.
        worst = 0.0
        for p in pairs:
            for v in (p.get("a_in_b"), p.get("b_in_a")):
                if v is not None and v > worst:
                    worst = v
        out.append({"title": title, "sessions": sessions, "pairs": pairs,
                    "max_containment": worst, "scope": scope,
                    "accounts": {a: sorted(s) for a, s in per_account.items()}})
    # Sidebar duplicates first - they are the ones a person can act on.
    out.sort(key=lambda g: (g["scope"] != "sidebar", -g["max_containment"],
                            g["title"]))
    return out


def gather_doctor(env):
    disc = discover_stores(env)
    rows, row_errors = load_rows(disc.roots)
    transcripts = iter_transcripts(env.projects_root)
    tids = {os.path.splitext(os.path.basename(p))[0] for _, p in transcripts}
    blank = [r.local_id for r in rows if not r.cli_session_id]
    dead = [{"local_id": r.local_id,
             "age_days": round((env.now() * 1000 - r.last_activity) / 86_400_000)}
            for r in rows if r.cli_session_id and r.cli_session_id not in tids]
    listed = {r.cli_session_id for r in rows if r.cli_session_id}
    unlisted = sorted(tids - listed)
    # A conversation on disk that no account's sidebar points at. MOST of these
    # are ordinary and always will be: a CLI-created session never had a row, and
    # a session deleted in the app leaves its transcript behind. On the machine
    # this was written for, 155 of 497 transcripts were unlisted, and the check
    # printed one identical line for each - 155 lines inside a 569-line report.
    #
    # That is why it did not help on 2026-08-21, when resuming a session under
    # another account repointed its row and left a 32 MB conversation reachable
    # from nothing. The information was in the report. Nobody could see it.
    #
    # So rank them. Recency and size are what separate "an old session I deleted"
    # from "the conversation I was in this afternoon", and the ranked few go in
    # the human report while the full list stays in --json for anything that
    # wants to walk it.
    sized = []
    for _, p in transcripts:
        sid = os.path.splitext(os.path.basename(p))[0]
        if sid in listed:
            continue
        try:
            st = os.stat(p)
        except OSError:
            continue
        sized.append({"session_id": sid, "mb": round(st.st_size / 1e6, 1),
                      "age_days": round((env.now() - st.st_mtime) / 86400.0, 1)})
    # LARGEST first within the recent window, not newest first.
    #
    # Newest-first was the obvious ordering and it defeats itself: age is the
    # primary key, so eight trivial transcripts from the last hour outrank a
    # 32 MB one from yesterday and push it off the cap. The scenario is not
    # hypothetical - investigating a lost conversation means starting CLI
    # sessions, each of which mints a fresh unreferenced transcript, so looking
    # for the thing you lost is exactly what buries it.
    #
    # Size within a recency window answers the real question ("is there a big
    # conversation I can no longer reach?"), and anything older than the window
    # is listed after, so a large orphan from last week still competes rather
    # than being invisible.
    recent = [d for d in sized if d["age_days"] <= 7]
    older = [d for d in sized if d["age_days"] > 7]
    recent.sort(key=lambda d: (-d["mb"], d["age_days"]))
    older.sort(key=lambda d: (-d["mb"], d["age_days"]))
    unlisted_ranked = (recent + older)[:8]
    unlisted_recent = recent
    cwds = [r.cwd for r in rows if r.cwd]
    cur, leg = scheme_evidence(cwds, env.projects_root)
    # Recent-50 evidence: the SAME population plan_move itself consults to
    # choose a scheme for a live move (the 50 most-recently-active rows).
    # Ruling fix (Task 13's "cur > 0 and leg > 0" over ALL rows was wrong):
    # a machine that lived through the 2026-07 encoding change legitimately
    # has folders under both schemes forever after - that history alone is
    # not an "unknown layout", it is exactly what legacy_folders already
    # reports (a exit-1 finding). Only a genuine TIE in the evidence that
    # would actually decide a future move - both counts equal and nonzero
    # among the most-recent 50 rows - means the layout cannot be
    # determined and is worth exit 2.
    recent_rows = sorted(rows, key=lambda r: r.last_activity)[-50:]
    recent_cwds = [r.cwd for r in recent_rows]
    cur_recent, leg_recent = scheme_evidence(recent_cwds, env.projects_root)
    legacy_folders = []
    legacy_folders_set = set()
    for cwd in set(cwds):
        a, b = encode(cwd, SCHEME_CURRENT), encode(cwd, SCHEME_LEGACY)
        if a != b and os.path.isdir(os.path.join(env.projects_root, a)) \
                and os.path.isdir(os.path.join(env.projects_root, b)):
            if b not in legacy_folders_set:
                n = len([x for x in os.listdir(os.path.join(env.projects_root, b))
                         if x.endswith(".jsonl")])
                legacy_folders.append({"folder": b, "transcripts": n})
                legacy_folders_set.add(b)
    unknown_layout = []
    if cur_recent == leg_recent > 0:
        unknown_layout = ["encoding-scheme evidence is tied/undecidable (recent 50: "
                          "current={0} legacy={1})".format(cur_recent, leg_recent)]
    nt = [o.manifest["op_id"] for o in nonterminal_ops(env)]
    # A synthesized row that is no longer on disk. The app tolerating a row it
    # never issued rests on one experiment (2026-08-22); if a future version
    # tombstones one, the row is gone, undo's byte-identity test finds nothing
    # to match, and nothing else would ever tell the user.
    #
    # BOUNDED BY JOURNAL RETENTION: rotate_ops ages ops out, so this sees only
    # rows whose creating op is still in the journal. That covers the window a
    # tombstoning app version would act in, and nothing after it. A durable
    # answer needs a standalone registry of synthesized rows - noted in the
    # README's known limits rather than silently implied here.
    vanished_new = []
    # A ROLLBACK THAT LEFT A ROW BEHIND. `recover --back` is the only exit from
    # a stuck operation, so it must always terminate - which means a row it
    # cannot safely remove is skipped rather than refused, and the fact is
    # recorded on the manifest as `rollback_residue`. `_collides` then pins that
    # op against rotation forever, on the stated grounds that "a rolled_back op
    # with a surviving row is invisible everywhere".
    #
    # It was: nothing ever read the key back. gather_doctor's vanished-row check
    # reads only 'completed' ops and cmd_recover's listing only non-terminal
    # ones, so the only reader was cmd_recover printing it during the very call
    # that wrote it. Probed 2026-08-23: the pinned op survived 25 subsequent
    # operations and was surfaced by nothing. The retention hold was write-only,
    # protecting a record no reader existed for. This is that reader.
    rollback_residue = []
    for _op in list_ops(env):
        _m = _op.manifest
        _res = _m.get("rollback_residue")
        if _res:
            _rows = _m.get("rows")
            _r0 = (_rows[0] if isinstance(_rows, list) and _rows
                   and isinstance(_rows[0], dict) else {})
            # ASK THE STORE, NOT THE JOURNAL - the same rule the vanished-row
            # check below follows, for the same reason. The residue records what
            # was true at the moment of the rollback; if the user has since
            # deleted that session in the app there is nothing left to report,
            # and an alert that could never clear would be a permanent exit 1
            # over a resolved condition. A path that cannot be checked is
            # reported, never hidden - "couldn't look" is never "nothing there".
            _dest, _left = _r0.get("dest_path"), True
            if isinstance(_dest, str) and _dest:
                try:
                    os.stat(_dest)
                except FileNotFoundError:
                    _left = False
                except OSError:
                    _left = True
            if _left:
                rollback_residue.append({
                    "op_id": _m.get("op_id"), "op_type": _m.get("op_type"),
                    "status": _m.get("status"), "title": _m.get("title"),
                    "name": _r0.get("name") or "",
                    "store_label": _m.get("store_label") or "",
                    "detail": _res if isinstance(_res, str) else str(_res),
                })
        if _m.get("status") != "completed":
            continue
        # SHAPE-VALIDATE FIRST, like every other consumer of a new-row manifest.
        # `(_m.get("rows") or [{}])[0]` reads a file a user can edit and a crash
        # can truncate: measured 2026-08-23, a 'rows' that is a dict raises
        # KeyError: 0 here, a row missing 'dest_path' raises KeyError out of
        # _sync_row_drift, and a row that is a string raises AttributeError.
        # main() catches only Refusal and LayoutError, so `doctor` - the command
        # you run when something is already wrong - died with an unredacted
        # traceback carrying store paths and account uuids. The same damage to a
        # repoint op leaves doctor healthy, so this loop was the one consumer
        # that skipped the validator built for exactly this.
        #
        # Skipping is the right answer, not reporting: every field below comes
        # out of the row this rejects, so there is nothing to report about it -
        # and a damaged record is `recover`'s subject, where classify_op already
        # names the damage in its listing.
        #
        # CONVERGE ROWS ARE COVERED TOO, per its design's Interactions section:
        # a converge op records every created row in the same per-row shape a
        # new-row op uses (name/dest_path/post_b64/written plus a store and
        # session), so this one check extends across both - at converge's
        # fan-out, a low per-row chance of the app rejecting a synthesized row
        # becomes worth watching.
        if _m.get("op_type") == "new-row":
            if _new_row_shape_error(_m):
                continue
            _watch = [(_m["rows"][0], _m.get("store_path") or "",
                       _m.get("to_session") or "", _m.get("title"),
                       _m.get("store_label"))]
        elif _m.get("op_type") == "converge":
            if _converge_shape_error(_m):
                continue
            _watch = [(_r, _r.get("store_path") or "", _r.get("session") or "",
                       _r.get("title"), _r.get("label"))
                      for _r in _m["rows"]]
        else:
            continue
        for _r, _store, _sid, _title, _slabel in _watch:
            if not (_r.get("written") and _sync_row_drift(_r) == "absent"):
                continue
            # ONE PATH BEING ABSENT IS NOT THE QUESTION. The question is whether
            # the account can still open the conversation, and those come apart
            # in two ordinary ways: the user takes doctor's own advice and
            # re-runs `new-row`, which mints a FRESH uuid and leaves this path
            # absent forever - so the alert would never clear, and the suggested
            # command would then refuse because a row already opens it - or the
            # user deletes the row deliberately. Either way, reporting a
            # tombstone is wrong. Ask the store, not the journal.
            try:
                _still, _ = _row_already_opens(_store, _sid)
            except (Refusal, LayoutError, OSError):
                _still = None       # unreadable store: report it, do not hide it
            if _still:
                continue
            _found = find_transcripts(env.projects_root, _sid)
            vanished_new.append({
                "op_id": _m.get("op_id"), "title": _title,
                "to_session": _sid,
                "store_label": _slabel,
                # Checked, not assumed - and counted, not merely tested for
                # truthiness. The diagnostic below offers `new-row --to <id>`,
                # which refuses when the id resolves to more than one project
                # folder; bool() here would send the user at a command that
                # refuses. Only exactly one hit means the advice will work.
                "transcript_count": len(_found),
                # And the PATHS. When there are several, "resolve that first"
                # is not advice - WHICH folders hold the duplicates is the whole
                # of the answer, and this loop is the only place that knows.
                "transcript_paths": _found,
            })
    # Conversations sharing a title. Informational: it does NOT touch exit_code,
    # because two conversations with one name is normal rather than a fault.
    try:
        duplicate_titles = _duplicate_title_groups(env, rows)
    except (LayoutError, OSError, ValueError):
        duplicate_titles = []
    report = {
        "stores": {"status": disc.status, "roots": disc.roots, "detail": disc.detail},
        "row_count": len(rows), "row_errors": row_errors, "blank_rows": sorted(blank),
        "dead_rows": dead, "unlisted_transcripts": unlisted,
        "unlisted_ranked": unlisted_ranked, "unlisted_recent": len(unlisted_recent),
        "encoding": {"current": cur, "legacy": leg},
        "encoding_recent": {"current": cur_recent, "legacy": leg_recent},
        "legacy_folders": legacy_folders, "nonterminal_ops": nt,
        "stale_lock": lock_is_stale(env),
        "unknown_layout": unknown_layout,
        "duplicate_titles": duplicate_titles,
        "vanished_new_rows": vanished_new,
        "rollback_residue": rollback_residue,
    }
    if disc.status == "error" or row_errors or unknown_layout:
        report["exit_code"] = 2
    elif (blank or dead or nt or report["stale_lock"] or legacy_folders
            or vanished_new or rollback_residue):
        report["exit_code"] = 1
    else:
        report["exit_code"] = 0
    return report


ALIGNMENT_DETAIL_LIMIT = 10


def title_key(title):
    """The duplicate-title comparator: trimmed exact match.

    ONE function, shared by `alignment`'s distinguishable grouping and
    `converge`'s collision hold, so "the collision hold guarantees
    `distinguishable` does not move" holds by construction rather than by two
    parallel implementations happening to agree (converge design, "Holds").
    A non-string compares as "" - the same fail-closed reading every caller
    gives an untitled row - where the old inline `.strip()` raised
    AttributeError on a malformed row.
    """
    return title.strip() if isinstance(title, str) else ""


def gather_alignment(env):
    """How close the accounts are to holding one coherent history.

    `doctor` answers "is anything broken or stuck". This answers a different
    question that was being re-derived by hand, in chat, every single session:
    **how far are the three sidebars from agreeing with each other, and which
    way is it moving.** Five properties, measured, none of them inferable from
    the others:

      reachable       a conversation opens from SOME sidebar
      distinguishable no two rows in ONE sidebar share a title
      consistent      a row file opens the SAME conversation in every account
      complete        a conversation opens from EVERY sidebar
      safe            no dead, blank or unreadable rows

    They fail independently, and fixing one can worsen another - converging
    accounts (complete) lands both halves of a disagreeing pair in every sidebar
    under one name, which is a direct hit to distinguishable. Reporting a single
    "aligned: yes/no" would hide exactly the trade-off the reader has to make,
    so this reports five numbers and never averages them.

    Reads only. No transcript CONTENT is opened - filenames and row files only -
    so this stays fast on a store with gigabytes of history behind it.
    """
    disc = discover_stores(env)
    rows, row_errors = load_rows(disc.roots)
    transcripts = iter_transcripts(env.projects_root)
    tids = {os.path.splitext(os.path.basename(p))[0] for _, p in transcripts}

    def acct(r):
        return os.path.basename(os.path.dirname(os.path.dirname(r.path)))

    accounts = sorted({acct(r) for r in rows})
    n_acct = len(accounts)
    labels = {a: (_email_of(env, a) or a[:8]) for a in accounts}

    # ---- safe ------------------------------------------------------------
    blank = sorted(r.local_id for r in rows if not r.cli_session_id)
    dead = sorted(r.local_id for r in rows
                  if r.cli_session_id and r.cli_session_id not in tids)

    # ---- reachable -------------------------------------------------------
    listed = {r.cli_session_id for r in rows if r.cli_session_id}
    orphans = sorted(tids - listed)

    # ---- complete --------------------------------------------------------
    reach = {}
    for r in rows:
        if r.cli_session_id:
            reach.setdefault(r.cli_session_id, set()).add(acct(r))
    by_count = {}
    for accs in reach.values():
        by_count[len(accs)] = by_count.get(len(accs), 0) + 1
    short = sum(n for k, n in by_count.items() if k < n_acct)

    # ---- consistent ------------------------------------------------------
    # Keyed by row FILENAME, because that is what sync copies: one row file
    # lands in several accounts and each account's copy can drift to a
    # different cliSessionId. Same file, different destination.
    byfile = {}
    for r in rows:
        byfile.setdefault(os.path.basename(r.path), {})[acct(r)] = r.cli_session_id
    disagree = []
    for name in sorted(byfile):
        per = byfile[name]
        if len({v for v in per.values()}) <= 1:
            continue
        # A disagreement only COSTS something when one of the conversations it
        # names is short of a sidebar. If both are reachable everywhere by some
        # other row, the accounts merely disagree about which row opens which -
        # untidy, not lossy. Separating these stops the report crying wolf.
        gap = sorted({s for s in per.values()
                      if s and len(reach.get(s, ())) < n_acct})
        disagree.append({
            "row": name,
            "opens": dict((labels[a], per[a]) for a in sorted(per)),
            "short_of_all_accounts": gap,
        })
    with_gap = [d for d in disagree if d["short_of_all_accounts"]]

    # ---- distinguishable -------------------------------------------------
    # PER ACCOUNT, never machine-wide. Grouping titles across all three stores
    # reports pairs that no sidebar ever shows together, and an earlier version
    # of this analysis did exactly that - it proposed removing an account's only
    # copy of a conversation because a DIFFERENT account also had one.
    #
    # The grouping key is title_key - THE shared comparator, the same one
    # converge's collision hold uses, which is what makes "converge cannot
    # move this number" true by construction.
    dup_per, dup_titles = {}, {}
    for a in accounts:
        seen = {}
        for r in rows:
            if acct(r) != a or not r.cli_session_id:
                continue
            data = r.data if isinstance(r.data, dict) else {}
            title = title_key(data.get("title"))
            if title:
                seen.setdefault(title, set()).add(r.cli_session_id)
        for title, sids in seen.items():
            if len(sids) > 1:
                dup_per[labels[a]] = dup_per.get(labels[a], 0) + 1
                dup_titles.setdefault(title, {})[labels[a]] = sorted(sids)
    for a in accounts:
        dup_per.setdefault(labels[a], 0)

    return {
        "stores": {"status": disc.status, "detail": disc.detail, "roots": disc.roots},
        "accounts": [{"account": a, "label": labels[a],
                      "rows": sum(1 for r in rows if acct(r) == a)}
                     for a in accounts],
        "row_errors": row_errors,
        "reachable": {"transcripts": len(tids),
                      "reachable": len(listed & tids),
                      "orphans": len(orphans),
                      "orphan_ids": orphans},
        "distinguishable": {"duplicate_titles": len(dup_titles),
                            "per_account": dup_per,
                            "titles": dup_titles},
        "consistent": {"disagreeing_rows": len(disagree),
                       "leaving_a_gap": len(with_gap),
                       "rows": disagree},
        "complete": {"conversations": len(reach),
                     "in_all_accounts": by_count.get(n_acct, 0),
                     "short": short,
                     "by_account_count": dict((str(k), v) for k, v in sorted(by_count.items()))},
        "safe": {"dead_rows": len(dead), "blank_rows": len(blank),
                 "unreadable_rows": len(row_errors)},
        # Always 0 when the report could be produced. This is a scoreboard, not
        # a check: the numbers stay non-zero for as long as the work takes, and
        # a command that exits 1 for months trains you to stop reading it. That
        # failure already happened once in this project, to `doctor`.
        "exit_code": 0 if disc.status == "found" else 1,
    }


def cmd_alignment(env, ns):
    rep = gather_alignment(env)
    if _ANONYMIZE:
        # Before anything formats or truncates it.
        rep = anonymize_report(env, rep)
    if ns.json:
        print(json.dumps(rep, indent=1))
        return rep["exit_code"]

    def say(line):
        print(line if ns.verbose else redact(env, line))

    if rep["stores"]["status"] != "found":
        say("[observed] store: {0} ({1})".format(rep["stores"]["status"],
                                                 rep["stores"]["detail"]))
        return rep["exit_code"]

    n_acct = len(rep["accounts"])
    say("[observed] {0} account(s):".format(n_acct))
    for a in rep["accounts"]:
        say("[observed]   {0:<28} {1:>4} rows".format(a["label"], a["rows"]))
    for e in rep["row_errors"]:
        say("[observed] UNREADABLE ROW (mutations blocked): " + e)

    r = rep["reachable"]
    say("[observed] reachable       {0} of {1} transcript(s) open from a sidebar; "
        "{2} orphaned".format(r["reachable"], r["transcripts"], r["orphans"]))
    if r["orphans"]:
        say("[hypothesis]   an orphan is usually the earlier half of a compacted "
            "session - the row follows")
        say("[hypothesis]   the resumed conversation and leaves the original "
            "behind. 'doctor' ranks the ones")
        say("[hypothesis]   worth a second look by size and recency; this line "
            "only counts them.")

    d = rep["distinguishable"]
    say("[observed] distinguishable {0} title(s) duplicated inside a single sidebar"
        .format(d["duplicate_titles"]))
    for label in sorted(d["per_account"]):
        say("[observed]   {0:<28} {1:>4}".format(label, d["per_account"][label]))
    shown = 0
    for title in sorted(d["titles"]):
        if shown >= ALIGNMENT_DETAIL_LIMIT:
            say("[observed]   ... and {0} more (all of them in --json)"
                .format(len(d["titles"]) - shown))
            break
        where = d["titles"][title]
        say("[observed]   {0!r} in {1}".format(title[:56], ", ".join(sorted(where))))
        shown += 1

    c = rep["consistent"]
    say("[observed] consistent      {0} row file(s) open a different conversation "
        "depending on the account".format(c["disagreeing_rows"]))
    if c["disagreeing_rows"]:
        # The number that decides whether to care. A disagreement whose
        # conversations are all reachable elsewhere costs nothing.
        say("[observed]                   {0} of those leave a conversation short "
            "of at least one sidebar".format(c["leaving_a_gap"]))
        if c["leaving_a_gap"] == 0:
            say("[hypothesis]   none of them lose you anything - every conversation "
                "involved is still reachable")
            say("[hypothesis]   from every account by some other row. Untidy "
                "bookkeeping, not lost history.")

    m = rep["complete"]
    say("[observed] complete        {0} of {1} conversation(s) reachable from all "
        "{2} account(s); {3} short"
        .format(m["in_all_accounts"], m["conversations"], n_acct, m["short"]))
    for k in sorted(m["by_account_count"], key=lambda s: -int(s)):
        say("[observed]   in {0} of {1} account(s)          {2:>4}"
            .format(k, n_acct, m["by_account_count"][k]))

    s = rep["safe"]
    say("[observed] safe            {0} dead, {1} blank, {2} unreadable row(s)"
        .format(s["dead_rows"], s["blank_rows"], s["unreadable_rows"]))
    if s["dead_rows"]:
        say("[hypothesis]   a dead row points at a transcript that no longer "
            "exists - usually retention.")
        say("[hypothesis]   'cleanupPeriodDays' defaults to 30; raising it stops "
            "the backlog rebuilding.")
    return rep["exit_code"]


def cmd_doctor(env, ns):
    rep = gather_doctor(env)
    if _ANONYMIZE:
        # Before anything formats or truncates it.
        rep = anonymize_report(env, rep)
    if ns.json:
        print(json.dumps(rep, indent=1))
        return rep["exit_code"]
    def say(line):
        print(line if ns.verbose else redact(env, line))

    def say_ids(line):
        """Like say(), but keeps session ids whole.

        redact() shortens uuid-shaped ids to eight characters, which is right
        for values that only identify a machine - and wrong for the one place
        this report hands the user something to type back in. The orphan block
        exists to feed `repoint --to`, which matches the transcript filename
        exactly, so a shortened id makes that workflow impossible from the
        default report. Home-directory redaction still applies; only the ids
        survive.
        """
        print(line if ns.verbose else redact(env, line, keep_ids=True))
    say("[observed] store: {0} ({1})".format(rep["stores"]["status"],
                                             rep["stores"]["detail"]))
    for r in rep["stores"]["roots"]:
        say("[observed]   root: " + r)
    say("[observed] listing rows: {0}".format(rep["row_count"]))
    for e in rep["row_errors"]:
        say("[observed] UNREADABLE ROW (mutations blocked): " + e)
    for lid in rep["blank_rows"]:
        say("[observed] row {0} has a blank cliSessionId".format(lid))
        say("[hypothesis]   the app blanks the link when a transcript goes missing")
    for d in rep["dead_rows"]:
        say("[observed] row {0}: transcript missing (last activity {1}d ago)"
            .format(d["local_id"], d["age_days"]))
        if d["age_days"] >= RETENTION_HINT_DAYS:
            say("[hypothesis]   age is consistent with the ~30-day retention default")
    ranked = rep.get("unlisted_ranked") or []
    if ranked:
        say("[observed] {0} transcript(s) have no listing row in any account; "
            "{1} touched in the last 7 days"
            .format(len(rep["unlisted_transcripts"]), rep.get("unlisted_recent", 0)))
        say("[hypothesis]   most are normal - a CLI-created session never had a row, "
            "and deleting a session in")
        say("[hypothesis]   the app leaves its transcript behind. A RECENT, LARGE one "
            "is the exception worth")
        say("[hypothesis]   looking at: resuming a session under another account "
            "repoints its row, which can")
        say("[hypothesis]   leave the conversation you were just in reachable from no "
            "sidebar. Largest first,")
        say("[hypothesis]   within the last 7 days; anything older comes after:")
        # The FULL id, through say_ids. `repoint --to` matches the transcript
        # filename exactly, and this block is where the README sends people to
        # find it - so both the [:8] slice this used to do AND redact()'s
        # uuid-shortening had to go. The first was fixed alone in 0.9.11, which
        # left the workflow just as broken, one layer down.
        for d in ranked:
            say_ids("[observed]   {0}  {1:>6.1f} MB  last written {2} day(s) ago"
                    .format(d["session_id"], d["mb"], d["age_days"]))
        # `new-row` FIRST, `repoint` as the alternative. Until this branch,
        # repointing an existing row was the only way to reach an unlisted
        # conversation, and it costs whatever that row used to open - the exact
        # trade that produced the 2026-08-21 loss this block exists to report.
        # A purely additive command now exists; README:186 says of this very
        # block that "doctor lists them; this turns one back into a sidebar
        # entry", and doctor was the one place that never said so.
        say("[hypothesis]   to reach one again: new-row --to <id above> adds a "
            "sidebar row for it and changes nothing that already exists")
        say("[hypothesis]   or repoint --only <title> --to <id above>, when you "
            "would rather an EXISTING row opened it than have another one")
        if len(rep["unlisted_transcripts"]) > len(ranked):
            say("[observed]   ... and {0} more (all of them in --json)"
                .format(len(rep["unlisted_transcripts"]) - len(ranked)))
    for v in rep.get("vanished_new_rows") or []:
        say_ids("[observed] a row this tool created is no longer on disk: {0!r} "
                "(op {1}, {2})".format(v["title"], v["op_id"], v["store_label"]))
        # say_ids, not say. redact() shortens a uuid-shaped id to eight
        # characters, and these lines hand the user a command to type back -
        # `new-row --to 174eb7c1…` is not a command. Same reason the ranked
        # orphan block above uses say_ids; this branch was written with say and
        # never noticed, because every test id here is 32 digits with no hyphens
        # and the uuid pattern does not match one.
        if v["transcript_count"] == 1:
            say_ids("[hypothesis]   the app removed a row it did not issue - the "
                    "one documented risk of 'new-row'. The conversation ({0}) is "
                    "still on disk; 'new-row --to {0}' makes another."
                    .format(v["to_session"]))
        elif v["transcript_count"] == 0:
            say_ids("[observed]     the conversation ({0}) is gone from disk too, "
                    "so this is retention catching up rather than the app "
                    "rejecting a row. Nothing to recreate.".format(v["to_session"]))
        else:
            # "Resolve that first" named a problem and no way out of it. The
            # duplicates are the answer, so print them.
            say_ids("[observed]     the conversation ({0}) is now in {1} project "
                    "folders, so 'new-row' would refuse to guess between them:"
                    .format(v["to_session"], v["transcript_count"]))
            for _p in v.get("transcript_paths") or []:
                say_ids("[observed]       " + _p)
            say_ids("[hypothesis]     remove or rename the copies you do not "
                    "want - whichever one is left is the one 'new-row --to {0}' "
                    "will open.".format(v["to_session"]))
    for res in rep.get("rollback_residue") or []:
        say_ids("[observed] a rolled-back operation left a row in the store: {0} "
                "(op {1}, {2})".format(res["detail"], res["op_id"],
                                       res["store_label"] or res["op_type"] or "?"))
        say("[hypothesis]   'recover --back' is the only exit from a stuck "
            "operation, so it closes even when it cannot remove what that "
            "operation wrote. Nothing else looks for that row - delete the "
            "session from the app if it is not wanted, or leave it if it is, "
            "and this clears.")
    dupes = rep.get("duplicate_titles") or []
    # Split before printing. These are two different conditions that happen to
    # look alike, and reporting them together produced a cleanup list that told
    # a user to remove an account's only copy of a conversation.
    same_sidebar = [g for g in dupes if g.get("scope") == "sidebar"]
    across = [g for g in dupes if g.get("scope") != "sidebar"]
    if same_sidebar:
        say("[observed] {0} title(s) appear MORE THAN ONCE IN A SINGLE SIDEBAR - "
            "the app writes these titles itself, so related work gets the same "
            "sentence".format(len(same_sidebar)))
        shown = 0
        for g in same_sidebar:
            if shown >= 5:
                break
            shown += 1
            say_ids("[observed]   {0!r}".format(g["title"]))
            for s in g["sessions"]:
                turns = ("{0} turns".format(s["turns"]) if s["turns"] is not None
                         else "turns " + (s["unmeasured"] or "not counted"))
                say_ids("[observed]     {0}  {1:>16}  {2}".format(
                    s["session_id"], turns,
                    "{0} MB".format(s["mb"]) if s["mb"] is not None else "size ?"))
            for p in g["pairs"]:
                if p.get("unmeasured"):
                    # NOT "0% shared". One of these is past the comparison cap,
                    # and printing a zero would read as "shares nothing" - which
                    # is the one conclusion most likely to delete the wrong one.
                    say_ids("[observed]     {0} vs {1}: not compared, one is too "
                            "large".format(p["a"][:8], p["b"][:8]))
                    continue
                # BOTH DIRECTIONS ON ONE LINE, each naming its own subject.
                # Containment is asymmetric: 100% of a 1-turn stub sits inside a
                # 26-turn conversation while only 4% of that one sits in the
                # stub. A reader shown a bare "100% contained" can delete the
                # superset, so neither number is ever printed without the id it
                # describes.
                say_ids("[observed]     {0:.0f}% of {1} is also in {2}; {3:.0f}% "
                        "of {2} is also in {1}".format(
                            p["a_in_b"], p["a"][:8], p["b"][:8], p["b_in_a"]))
        if len(same_sidebar) > shown:
            say("[observed]   ... and {0} more (all of them in --json)"
                .format(len(same_sidebar) - shown))
        say("[hypothesis]   a conversation whose turns are ~all inside a sibling "
            "is an earlier segment of it and can be removed from the sidebar in "
            "the app; one with turns of its own is a separate conversation that "
            "happens to share a name - rename it there rather than removing it")
    if across:
        say("[observed] {0} title(s) open a DIFFERENT conversation depending on "
            "which account you are signed into - each sidebar shows only one, so "
            "there is nothing to declutter".format(len(across)))
        for g in across[:5]:
            say_ids("[observed]   {0!r}".format(g["title"]))
            for acct, sids in sorted(g.get("accounts", {}).items()):
                say_ids("[observed]     {0}  opens  {1}".format(
                    _email_of(env, acct) or acct[:8], ", ".join(sids)))
        if len(across) > 5:
            say("[observed]   ... and {0} more (all of them in --json)"
                .format(len(across) - 5))
        say("[hypothesis]   removing one of these takes away an account's ONLY "
            "door to that conversation while the others keep pointing elsewhere. "
            "If they should agree, 'sync' the one you want everywhere; if they "
            "are genuinely different work, rename them in the app")
    say("[observed] encoding evidence (recent 50): current={0} legacy={1}"
        .format(rep["encoding_recent"]["current"], rep["encoding_recent"]["legacy"]))
    say("[observed] encoding evidence (all rows): current={0} legacy={1}"
        .format(rep["encoding"]["current"], rep["encoding"]["legacy"]))
    for msg in rep.get("unknown_layout", []):
        say("[observed] " + msg)
    for lf in rep["legacy_folders"]:
        say("[observed] legacy-encoded folder {0} ({1} transcripts) is shadowed"
            .format(lf["folder"], lf["transcripts"]))
    for oid in rep["nonterminal_ops"]:
        say("[observed] unresolved operation {0} - run: claude-code-sessions recover".format(oid))
    if rep["stale_lock"]:
        say("[observed] stale lock - run: claude-code-sessions recover")
    if rep["exit_code"] == 2:
        say("[observed] unrecognized or unreadable state - please open an issue including the output above (paths and ids are redacted by default)")
    return rep["exit_code"]


# --------------------------------------------------------------- 7. CLI wiring
import argparse


class _PrintVersion(argparse.Action):
    """`--version`, printed verbatim.

    Exists only because argparse's built-in version action formats its text
    through HelpFormatter, which re-wraps to the terminal width and will break
    an absolute path mid-word. The whole value of printing the path is that it
    can be pasted back; a wrapped one cannot.
    """

    def __call__(self, parser, namespace, values, option_string=None):
        sys.stdout.write("claude-code-sessions {0}\n".format(__version__))
        sys.stdout.write("running from: {0}\n".format(os.path.abspath(__file__)))
        parser.exit()


def build_parser():
    p = argparse.ArgumentParser(prog="claude-code-sessions",
        description="Inspect and relocate Claude Code sessions on disk. Unofficial; "
                    "fails closed. Close the Claude app before any mutation.")
    # Prints the RUNNING module's __version__, plus where that module was loaded
    # from. The path is the point: a pipx install and a source checkout can both
    # be on PATH, and "which copy answered?" is the question a version number
    # alone cannot settle. Exits during the argument scan, before the
    # required-subcommand check, so `--version` works with no subcommand.
    #
    # NOT argparse's built-in `action="version"`: it runs the text through
    # HelpFormatter, which re-wraps at the terminal width and broke this path
    # across a line MID-WORD - producing exactly the uncopyable output 0.9.11
    # and 0.9.12 were spent removing from `doctor`. A path you have to reassemble
    # by hand is not an answer to "which copy is running".
    p.add_argument("--version", action=_PrintVersion, nargs=0,
                   help="print the version and the file this ran from")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--verbose", action="store_true",
                        help="full paths and ids (default output is redacted)")

        sp.add_argument("--anonymize", action="store_true",
                        help="replace every session title and project name with a stable opaque label, for pasting real output somewhere public")

    sp = sub.add_parser("list", help="inventory sessions")
    sp.add_argument("query", nargs="?", default="")
    sp.add_argument("--project")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--full", action="store_true")
    common(sp)

    sp = sub.add_parser("doctor", help="read-only health report")
    sp.add_argument("--json", action="store_true")
    common(sp)

    sp = sub.add_parser(
        "alignment",
        help="read-only report: how close your accounts are to one shared history")
    sp.add_argument("--json", action="store_true")
    common(sp)

    sp = sub.add_parser("move", help="relocate a session to another project folder")
    sp.add_argument("session_id")
    sp.add_argument("target")
    sp.add_argument("--apply", action="store_true")
    sp.add_argument("--transcript-only", action="store_true", dest="transcript_only")
    sp.add_argument("--row", action="append", default=[])
    sp.add_argument("--yes", action="store_true")
    sp.add_argument("--force", action="store_true")
    common(sp)

    sp = sub.add_parser("undo", help="reverse the most recent operation")
    sp.add_argument("--list", action="store_true", dest="show")
    sp.add_argument("--id", dest="op_id")
    sp.add_argument("--apply", action="store_true")
    common(sp)

    sp = sub.add_parser("recover", help="resolve interrupted operations")
    sp.add_argument("--resolve", dest="op_id")
    direction = sp.add_mutually_exclusive_group()   # M2: --forward/--back are exclusive
    direction.add_argument("--forward", action="store_true")
    direction.add_argument("--back", action="store_true")
    sp.add_argument("--apply", action="store_true")
    common(sp)

    sp = sub.add_parser(
        "trust-signed-helper",
        help="let Chrome stay open by trusting the signed Chrome helper (opt-in)")
    grp = sp.add_mutually_exclusive_group()
    grp.add_argument("--on", action="store_true", help="enable it")
    grp.add_argument("--off", action="store_true", help="disable it (the default)")
    common(sp)

    rp = sub.add_parser("repoint",
                        help="point one sidebar row at a different conversation")
    rp.add_argument("--only", default="", metavar="SUBSTRING",
                    help="substring of the row's title, or its local id - must match "
                         "exactly one row")
    rp.add_argument("--to", dest="to_session", default="", metavar="CLI_SESSION_ID",
                    help="the cliSessionId the row should open instead. 'doctor' "
                         "lists conversations no account currently points at")
    rp.add_argument("--store", default="", metavar="SUBSTRING",
                    help="which account's store to change (account id, org id, path, "
                         "or email). Defaults to the account that is signed in - "
                         "unlike sync, this command exists to fix the sidebar you are "
                         "looking at, and the app must be closed either way")
    rp.add_argument("--live", default="", metavar="SUBSTRING",
                    help="assert which account the desktop app is signed into "
                         "(RULING 5), when the identity files disagree")
    rp.add_argument("--apply", action="store_true", help="actually write the row")
    rp.add_argument("--json", action="store_true", help="print the plan as JSON")
    rp.add_argument("--verbose", action="store_true", help="do not redact paths")

    nr = sub.add_parser("new-row",
                        help="create a sidebar row for a conversation that has none")
    nr.add_argument("--to", dest="to_session", default="", metavar="CLI_SESSION_ID",
                    help="the cliSessionId the new row should open. 'doctor' lists "
                         "conversations no account currently points at")
    nr.add_argument("--store", default="", metavar="SUBSTRING",
                    help="which account's store to add the row to (account id, org "
                         "id, path, or email). Defaults to the account the identity "
                         "files agree is signed in, and refuses when they disagree")
    nr.add_argument("--title", default="", metavar="TEXT",
                    help="the row's title. Without this it is taken from the "
                         "transcript's own title if it has one, otherwise a "
                         "placeholder that does not impersonate a summary")
    nr.add_argument("--live", default="", metavar="SUBSTRING",
                    help="assert which account the desktop app is signed into "
                         "(RULING 5), when the identity files disagree")
    nr.add_argument("--apply", action="store_true", help="actually create the row")
    nr.add_argument("--json", action="store_true", help="print the plan as JSON")
    # Not optional decoration. README's redaction section promises that
    # plain-text output replaces the home directory with `~` and that --verbose
    # shows paths in full, and it tells users not to paste --json into an issue
    # BECAUSE that one is unredacted - which reads as a guarantee that the plain
    # text is safe. This command printed the user's project directory in the
    # clear and had no --verbose to offer, so `new-row --verbose` exited 2.
    nr.add_argument("--verbose", action="store_true", help="do not redact paths")

    rt = sub.add_parser(
        "retitle",
        help="rename a conversation's sidebar row in every account that holds it")
    rt.add_argument("--only", default="", metavar="TITLE_OR_ID",
                    help="the conversation to rename: a title substring, or a "
                         "cliSessionId prefix - must resolve to exactly one "
                         "conversation. An ambiguous match (expected when the "
                         "title itself collides) lists the candidates with ids")
    rt.add_argument("--title", default="", metavar="TEXT",
                    help="the new title, stored trimmed, with titleSource pinned "
                         "to 'user' so the app stops resummarising the row. This "
                         "is a SIDEBAR rename: the transcript's own customTitle "
                         "is deliberately not touched")
    rt.add_argument("--store", default="", metavar="SUBSTRING",
                    help="narrow the rename to one account's store (account id, "
                         "org id, path, or email), to repair a single sidebar. "
                         "Default is every account whose sidebar holds the "
                         "conversation, so they keep reading the same")
    rt.add_argument("--live", default="", metavar="SUBSTRING",
                    help="assert which account the desktop app is signed into "
                         "(RULING 5), when the identity files disagree")
    rt.add_argument("--apply", action="store_true",
                    help="actually rewrite the rows")
    rt.add_argument("--json", action="store_true", help="print the plan as JSON")
    common(rt)

    cv = sub.add_parser(
        "converge",
        help="create the missing sidebar rows so every conversation any "
             "account can open is openable from EVERY account",
        description="Create the missing sidebar rows so every conversation "
                    "any account can open is openable from EVERY account. "
                    "Purely additive - existing rows are never repointed, "
                    "refreshed, or deleted - and the target set is derived, "
                    "so there is no --to, --from, or --store to name.")
    cv.add_argument("--only", default="", metavar="TITLE_OR_ID",
                    help="narrow to one conversation: a title substring, or a "
                         "cliSessionId prefix - must resolve to exactly one "
                         "conversation; an ambiguous match lists the "
                         "candidates with ids")
    cv.add_argument("--live", default="", metavar="SUBSTRING",
                    help="assert which account the desktop app is signed into "
                         "(RULING 5), when the identity files disagree - an "
                         "email, an id, or the acct/org pair reports print "
                         "(e.g. 'aaaa1111/cccc3333'). Resolves the ACCOUNT, so "
                         "it works even when the account owns several org "
                         "directories")
    cv.add_argument("--apply", action="store_true",
                    help="actually create the rows. Purely additive: nothing "
                         "existing is changed, refreshed, or deleted. Exits 3 "
                         "when hold(s) remain - each printed with its fix")
    cv.add_argument("--json", action="store_true", help="print the plan as JSON")
    common(cv)

    sp = sub.add_parser("sync", help="copy session listing rows to your other account")
    sp.add_argument("--to", default="", metavar="SUBSTRING",
                    help="destination account id, org id, store path, or email "
                         "(required if more than one exists)")
    sp.add_argument("--live", default="", metavar="SUBSTRING",
                    help="when ~/.claude.json and config.json disagree about the "
                         "signed-in account, assert which one the desktop app is "
                         "signed into (id, org, path, or email substring, or the "
                         "printed acct/org pair, e.g. 'aaaa1111/cccc3333'; refused "
                         "unless the files disagree - see RULING 5)")
    sp.add_argument("--only", default="", metavar="SUBSTRING",
                    help="only sessions whose title contains this")
    sp.add_argument("--include-deleted", action="append", default=[],
                    dest="include_deleted", metavar="TITLE_OR_ID",
                    help="also copy this session even though the destination "
                         "deleted it (names one session; not a blanket switch)")
    sp.add_argument("--update", action="store_true",
                    help="also REFRESH rows that already exist in the destination, "
                         "replacing the WHOLE row with the source account's copy "
                         "(not just its stale title/last-activity snapshot). This "
                         "is the only sync route that overwrites rather than adds")
    sp.add_argument("--newer-only", action="store_true", dest="newer_only",
                    help="with --update, refresh ONLY rows this account's copy is "
                         "strictly newer than. Holds back rows whose destination "
                         "copy is newer, rows of the same age (a row can differ in "
                         "per-account settings without either side being newer), "
                         "and rows whose direction cannot be determined. Narrows "
                         "--update; does nothing without it")
    sp.add_argument("--allow-orphan", action="store_true", dest="allow_orphan",
                    help="permit a refresh that would leave the conversation it "
                         "displaces unreachable from every account. A row points at "
                         "a transcript, and two accounts can point at different "
                         "ones; without this, such a refresh is held back and named "
                         "rather than silently hiding a conversation")
    sp.add_argument("--verbatim", action="store_true",
                    help="copy rows unchanged instead of stripping connector config")
    sp.add_argument("--apply", action="store_true")
    sp.add_argument("--json", action="store_true")
    common(sp)
    return p


def _flags_from(ns):
    return MoveFlags(transcript_only=ns.transcript_only,
                     row=ns.row, yes=ns.yes, force=ns.force)


def _print_abort_reason(env, ns, op):
    """I3: a rollback that completes silently (no exception - e.g. a
    phase-6 keep-both abort) gives the user no clue anything unusual
    happened beyond the bare word "rolled_back". If the op's manifest
    carries an abort_reason (set by _abort/execute_op), surface it - and,
    when the destination copy was deliberately kept, name it too.
    """
    reason = op.manifest.get("abort_reason")
    if not reason:
        return
    line = "reason: " + reason
    if op.manifest.get("abort_keep_dest"):
        line += "; both copies were kept (destination retained at {0})".format(
            op.manifest.get("dest_transcript", ""))
    print(line if ns.verbose else redact(env, line))


def _print_new_op_reason(env, ns, before_ids):
    """run_move/run_undo return only a plain status string, not the Op they
    created - so to print its abort reason (I3) after a non-completed
    result, find the op that appeared since `before_ids` was snapshotted.
    Safe because callers hold the single-instance lock for the duration of
    the call that created it, so at most one new op can have appeared."""
    for op in list_ops(env):
        if op.manifest["op_id"] not in before_ids:
            _print_abort_reason(env, ns, op)


def cmd_move(env, ns):
    manifest = plan_move(env, ns.session_id, ns.target, _flags_from(ns))
    summary = ("mode={0}\nsource={1}\ndest={2}\nrows={3}"
               .format(manifest["mode"], manifest["source_transcript"],
                       manifest["dest_transcript"], len(manifest["rows"])))
    print(summary if ns.verbose else redact(env, summary))
    if not ns.apply:
        print("dry run - pass --apply to execute")
        return 0
    before_ids = {o.manifest["op_id"] for o in list_ops(env)}
    final = run_move(env, manifest)
    print("result: " + final)
    if final != "completed":
        _print_new_op_reason(env, ns, before_ids)
    return 0 if final == "completed" else 1


def cmd_undo(env, ns):
    ops = list_ops(env)
    if ns.show:
        for o in ops:
            line = "{0}  {1:12}  {2}".format(o.manifest["op_id"],
                                             o.manifest["status"],
                                             o.manifest.get("session_id", ""))
            print(line if ns.verbose else redact(env, line))
        return 0
    # delta: only a completed op whose op_type is one of the six this command
    # can actually reverse - "move", "sync", "repoint", "new-row", "retitle",
    # "converge" (or missing, which in practice never happens - every manifest
    # sets op_type) - is eligible as "the operation to undo". A completed *undo* op is itself
    # terminal from cmd_undo's point of view - plan_undo always refuses an
    # undo-of-undo ("to redo, run move again") - so selecting one here would
    # only ever produce that refusal instead of reaching an older, still-
    # undoable completed operation underneath it.
    #
    # THE GUI'S _find_undoable_sync MIRRORS THIS TUPLE, and its docstring says
    # so. It read ("move", "sync") after this one grew, so with a completed
    # repoint or new-row on top it reached past that op to an older sync and
    # offered to undo THAT - the disagreement its docstring promises cannot
    # happen. Anything added here has to be added there.
    candidates = [o for o in ops if o.manifest.get("status") == "completed"
                 and o.manifest.get("op_type", "move") in ("move", "sync",
                                                           "repoint", "new-row",
                                                           "retitle",
                                                           "converge")]
    if ns.op_id:
        candidates = [o for o in candidates if o.manifest["op_id"] == ns.op_id]
    if not candidates:
        raise Refusal("no completed operation to undo" +
                      (" with id " + ns.op_id if ns.op_id else ""))
    prior = candidates[-1]
    # RULING 5: a sync op that ran under a --live certification says so on
    # every route that can mutate under it, BEFORE it mutates - the --apply
    # path skips the preview entirely, so the preview line alone would warn
    # only the users who happened to dry-run first.
    live_note = _live_override_note(prior.manifest)
    if not ns.apply:
        if prior.manifest.get("op_type") == "repoint":
            pm = prior.manifest
            line = ("would undo {0} (repoint: {1!r} goes back to opening {2} instead "
                    "of {3}); pass --apply to execute".format(
                        pm["op_id"], pm.get("title", ""),
                        (pm.get("from_session") or "nothing")[:8],
                        (pm.get("to_session") or "")[:8]))
        elif prior.manifest.get("op_type") == "sync":
            # A sync manifest has no session_id - the move-shaped preview
            # below would print "session None". Name what undo would
            # actually remove instead: how many rows landed, and where.
            n_written = sum(1 for r in prior.manifest.get("rows", []) if r.get("written"))
            dest = prior.manifest.get("dest_email") or prior.manifest.get("dest_account", "")
            line = ("would undo {0} (sync: {1} row(s) written to {2}); pass --apply "
                    "to execute".format(prior.manifest["op_id"], n_written, dest))
        elif prior.manifest.get("op_type") == "new-row":
            pm = prior.manifest
            line = ("would undo {0} (new-row: removes the row {1!r}, which opens "
                    "{2}); pass --apply to execute".format(
                        pm["op_id"], pm.get("title", ""),
                        (pm.get("to_session") or "")[:8]))
        elif prior.manifest.get("op_type") == "retitle":
            pm = prior.manifest
            n_written = sum(1 for r in pm.get("rows", []) if r.get("written"))
            line = ("would undo {0} (retitle: {1} row(s) get their previous "
                    "titles back, dropping {2!r}); pass --apply to execute"
                    .format(pm["op_id"], n_written, pm.get("new_title", "")))
        elif prior.manifest.get("op_type") == "converge":
            pm = prior.manifest
            created = [r for r in pm.get("rows", []) if r.get("written")]
            n_acct = len({r.get("account") for r in created})
            line = ("would undo {0} (converge: removes the {1} row(s) it "
                    "created across {2} account(s), skipping any that is now "
                    "load-bearing); pass --apply to execute"
                    .format(pm["op_id"], len(created), n_acct))
        else:
            line = ("would undo {0} (session {1}); pass --apply to execute"
                    .format(prior.manifest["op_id"], prior.manifest.get("session_id")))
        print(line if ns.verbose else redact(env, line))   # M1: redact the preview too
        if live_note:
            print(live_note if ns.verbose else redact(env, live_note))
        return 0
    if live_note:
        print(live_note if ns.verbose else redact(env, live_note))
    before_ids = {o.manifest["op_id"] for o in list_ops(env)}
    if prior.manifest.get("op_type") == "repoint":
        final = undo_repoint(env, prior)
    elif prior.manifest.get("op_type") == "sync":
        final = undo_sync(env, prior)
    elif prior.manifest.get("op_type") == "new-row":
        final = undo_new_row(env, prior)
    elif prior.manifest.get("op_type") == "retitle":
        final = undo_retitle(env, prior)
    elif prior.manifest.get("op_type") == "converge":
        final = undo_converge(env, prior)
    else:
        final = run_undo(env, prior)
    print("result: " + final)
    # Converge's undo is FORGIVING - it deletes what is still redundant and
    # skips what became load-bearing - so a bare "undone" would hide exactly
    # the rows it deliberately left. Print the tally the spec requires:
    # deleted / skipped-by-reason / already-gone.
    if prior.manifest.get("op_type") == "converge" \
            and prior.manifest.get("undo_report"):
        rep = prior.manifest["undo_report"]
        line = "removed {0} row(s); {1} already gone".format(
            rep.get("deleted", 0), rep.get("already_gone", 0))
        print(line if ns.verbose else redact(env, line))
        for s in rep.get("skipped") or []:
            line = "kept {0} in {1} - {2}: {3}".format(
                s.get("name"), s.get("label"), s.get("reason"),
                s.get("detail"))
            print(line if ns.verbose else redact(env, line))
    # Undo restores history even when history collides (exact restoration
    # outranks distinguishability); when it did, the report has to say a
    # collision now exists rather than leave it to be found by 'alignment'.
    if prior.manifest.get("undo_collision_note"):
        line = "note: " + prior.manifest["undo_collision_note"]
        print(line if ns.verbose else redact(env, line))
    # undo_sync's own terminal status is "undone" (it mutates the completed
    # sync op in place rather than journaling a fresh reversal op the way
    # run_undo/execute_op do) - "completed" remains the success value for
    # every move/undo op the engine drives.
    success = final == "completed" or final == "undone"
    if not success:
        _print_new_op_reason(env, ns, before_ids)
    return 0 if success else 1


def cmd_trust_signed_helper(env, ns):
    """Turn RULING 7's opt-in on or off, or report it. Bare invocation SHOWS the
    state and changes nothing - a command whose whole job is loosening a guard
    should not do it as a side effect of being run without arguments."""
    path = trust_signed_helper_path(env)
    if ns.on:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("Created by 'claude-code-sessions trust-signed-helper --on'.\n"
                         "While this file exists, a Chrome helper at the app's own\n"
                         "package path that Windows reports as validly signed by\n"
                         "Anthropic, PBC does not block mutations, even when its bytes\n"
                         "are not the build this tool measured. Delete it to revoke.\n"
                         "See docs/internals.md, RULING 7.\n")
        except OSError as exc:
            raise Refusal("could not enable it: {0}".format(exc))
        print("Signed-helper trust is ON. Chrome may stay open; the desktop app "
              "still has to be closed.")
        print("It is weaker than the default: trust moves from the exact measured "
              "bytes to the publisher, so a future Anthropic build that started "
              "touching the store would be excused without being measured.")
        print("Revoke with --off (or delete the file it created).")
        return 0
    if ns.off:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise Refusal("could not disable it: {0}".format(exc))
        print("Signed-helper trust is OFF (the default). Only the measured build "
              "is excluded, so Chrome has to be closed whenever the helper has "
              "updated.")
        return 0
    on = signed_helper_trust_enabled(env)
    print("Signed-helper trust: {0}".format("ON" if on else "OFF (the default)"))
    print("  marker: {0}".format(path))
    print("  turn {0} with: claude-code-sessions trust-signed-helper --{0}"
          .format("off" if on else "on"))
    return 0


def cmd_recover(env, ns):
    if clear_stale_lock(env):
        print("cleared a stale lock")
    pending = nonterminal_ops(env)
    if not ns.op_id:
        for op in pending:
            c = classify_op(env, op)
            line = "{0}  {1:10}  source={2} dest={3} options={4}  {5}".format(
                op.manifest["op_id"], c["status"], c["source"], c["dest"],
                ",".join(c["resolutions"]) or "manual", c["note"])
            print(line if ns.verbose else redact(env, line))
        return 1 if pending else 0
    matches = [o for o in pending if o.manifest["op_id"] == ns.op_id]
    if not matches:
        raise Refusal("no unresolved op with id " + ns.op_id)
    direction = "forward" if ns.forward else ("back" if ns.back else None)
    if direction is None:
        raise Refusal("--resolve needs --forward or --back")
    # RULING 5: same pre-mutation note as cmd_undo - recover re-enters the
    # certified op directly, so it must be exactly as loud about it.
    live_note = _live_override_note(matches[0].manifest)
    if live_note:
        print(live_note if ns.verbose else redact(env, live_note))
    if not ns.apply:
        print("would resolve {0} {1}; pass --apply to execute".format(ns.op_id, direction))
        return 0
    final = recover_op(env, matches[0], direction)
    print("result: " + final)
    if final != "completed":
        _print_abort_reason(env, ns, matches[0])
    if matches[0].manifest.get("rollback_residue"):
        line = "[observed] rollback left something behind: {0}".format(
            matches[0].manifest["rollback_residue"])
        print(line if ns.verbose else redact(env, line))
    if matches[0].manifest.get("rollback_declined"):
        # Printed for the record, and deliberately NOT reported by doctor: the
        # row belongs to an operation that is tracked and completed, so leaving
        # it is the correct outcome rather than something needing attention.
        line = "[observed] rollback deliberately changed nothing: {0}".format(
            matches[0].manifest["rollback_declined"])
        print(line if ns.verbose else redact(env, line))
    return 0


def main(argv=None):
    global _ANONYMIZE
    ns = build_parser().parse_args(argv)
    env = default_env()
    if getattr(ns, "anonymize", False):
        if getattr(ns, "verbose", False):
            # --verbose prints the raw line and never reaches redact(), which
            # is where anonymisation happens. Accepting both would print real
            # titles under a flag whose whole purpose is to hide them - the
            # exact shape of the failure this feature exists to fix. Refuse.
            print("refused: --anonymize and --verbose contradict each other. "
                  "--verbose prints lines unredacted, so titles would appear "
                  "anyway. Drop one.", file=sys.stderr)
            return 2
        if getattr(ns, "apply", False):
            # A plan is what gets pasted; an apply is not. Refusing the
            # combination keeps the anonymiser strictly a display concern and
            # removes any path by which a substituted title could be mistaken
            # for real data and written into a store.
            print("refused: --anonymize is for producing output you can paste, "
                  "and --apply writes to a store. Run the plan with "
                  "--anonymize, then apply without it.", file=sys.stderr)
            return 2
        _ANONYMIZE = True
    handlers = {"list": cmd_list, "doctor": cmd_doctor, "move": cmd_move,
                "alignment": cmd_alignment,
                "undo": cmd_undo, "recover": cmd_recover, "sync": cmd_sync,
                "repoint": cmd_repoint, "new-row": cmd_new_row,
                "retitle": cmd_retitle, "converge": cmd_converge,
                "trust-signed-helper": cmd_trust_signed_helper}
    try:
        return handlers[ns.cmd](env, ns)
    except (Refusal, LayoutError) as exc:
        label = "refused" if isinstance(exc, Refusal) else "unsafe"
        msg = str(exc) if getattr(ns, "verbose", False) else redact(env, str(exc))
        print("{0}: {1}".format(label, msg), file=sys.stderr)
        return exc.exit_code


# ----------------------------------------------------------------- 8. sync
@dataclasses.dataclass
class Account:
    account_uuid: str
    org_uuid: str
    email: str
    path: str
    # How live_account() decided this was the signed-in account:
    #   "oauth"  - ~/.claude.json's oauthAccount named it outright.
    #   "config" - only config.json's lastKnownAccountUuid named it.
    #   "user"   - the user asserted it with sync --live while the identity
    #              files disagreed (RULING 5, _resolve_live_assertion).
    #   "corroborated" - converge --live named the account the identity
    #              files already AGREE on, uniquely (_corroborated_live):
    #              no arbitration happened, the flag was redundant, and the
    #              caller records no assertion - live_asserted stays "".
    #   ""       - not a live-account determination at all (every dormant
    #              candidate resolve_sync_endpoints builds).
    # Since RULING 4 (2026-08-02) provenance buys no guard exemption - E4
    # measured oauth stale across a real switch - it is kept for messages
    # and diagnostics only; every mutation route takes _guard_mutation.
    resolved_from: str = ""
    # Where `email` came from, for a DORMANT account: "sandbox" (re-derived from
    # the app's own per-account config, so current), "memo:<date>" (recorded by
    # this tool when that account was last signed in - good enough to recognise
    # a destination, but not an observation of right now), or "" (unknown, or a
    # live account whose email came from ~/.claude.json).
    email_source: str = ""


def _listdir_or_refuse(path, what):
    """os.listdir, but a failure is a LayoutError rather than a raw OSError.

    discover_stores proves only that the store ROOT is enumerable; every
    listdir deeper than that (account dirs, org dirs, the two store folders
    sync reads) can still hit a PermissionError, and main() catches only
    Refusal/LayoutError - so a bare OSError escapes as an unredacted
    traceback carrying full paths and account uuids. Same fail-closed rule
    the rest of the module uses: "couldn't look" is never "nothing there"."""
    try:
        return os.listdir(path)
    except OSError as exc:
        raise LayoutError("could not read {0} at {1}: {2}. 'Couldn't look' is never "
                          "'nothing there' - refusing.".format(what, path, exc))


def _account_dirs(env):
    """Every <accountUuid>/<organizationUuid> pair present on disk."""
    disc = discover_stores(env)
    if disc.status == "error":
        raise LayoutError("store discovery failed: {0}. 'Couldn't look' is never "
                          "'nothing there' - refusing.".format(disc.detail))
    out = []
    for root in disc.roots:
        for acct in sorted(_listdir_or_refuse(root, "the store root")):
            ap = os.path.join(root, acct)
            if not os.path.isdir(ap):
                continue
            for org in sorted(_listdir_or_refuse(ap, "an account directory")):
                op = os.path.join(ap, org)
                if os.path.isdir(op):
                    out.append((acct, org, op))
    return out


def _identity_disagreement(env):
    """(oauth_uuid, config_uuid) when the two identity files name different
    accounts; None otherwise.

    Measured 2026-08-02 (E4 verification): across a real desktop account
    switch, ~/.claude.json's oauthAccount stayed STALE while config.json's
    lastKnownAccountUuid tracked the switch - the inverse of the trust
    ordering this module shipped with. The whole-branch review had already
    built the opposite case (config stale, oauth fresh) synthetically. So
    either file can be the stale one; a disagreement between them means the
    live account is genuinely unknowable from files, and callers must fail
    closed rather than pick a side. The likely mechanism (unverified): the
    CLI owns ~/.claude.json, the desktop owns config.json, so each kind of
    sign-in freshens only its own file.

    An unreadable or malformed file is NO SIGNAL, deliberately - oauth-only
    and config-only are legitimate states, not failures, and after RULING 4
    the safety of every mutation rests on the universal process guard
    (_guard_mutation), not on this comparison. Both values must be
    non-empty strings; anything else is treated as absent so a garbage
    value can never traceback later inside a refusal message's [:8] slice.
    """
    try:
        with open(os.path.join(env.home, ".claude.json"), encoding="utf-8") as fh:
            oauth = ((json.load(fh) or {}).get("oauthAccount") or {}).get("accountUuid")
    except (OSError, ValueError, AttributeError, TypeError):
        oauth = None
    if not isinstance(oauth, str) or not oauth:
        return None
    for cand in env.store_candidates:
        cfg = os.path.join(os.path.dirname(cand), "config.json")
        try:
            with open(cfg, encoding="utf-8") as fh:
                last = (json.load(fh) or {}).get("lastKnownAccountUuid")
        except (OSError, ValueError, AttributeError, TypeError):
            continue
        if isinstance(last, str) and last and last != oauth:
            return (oauth, last)
    return None


def live_account(env):
    """The signed-in account, named outright rather than guessed.

    ~/.claude.json's oauthAccount carries accountUuid, organizationUuid AND
    emailAddress, which resolves the whole store path and gives the user a
    destination they can recognise. The exact (account, org) pair is
    preferred; if organizationUuid names a dir that doesn't exist on disk
    (config known, dir not yet created), fall back to matching the account
    alone. config.json's lastKnownAccountUuid is the last-resort fallback
    but names only the account half - if more than one org dir sits under
    that account there is no evidence which is live, so this refuses to
    guess and returns None rather than picking one.

    The returned Account records WHICH path answered, in `resolved_from` -
    kept for messages and diagnostics. It no longer gates anything: RULING 4
    (2026-08-02) put the running-app check on every mutation route after E4
    measured oauthAccount stale across a real switch (see _guard_mutation
    and _identity_disagreement).
    """
    if _identity_disagreement(env):
        return None                    # fail closed - see _identity_disagreement
    try:
        with open(os.path.join(env.home, ".claude.json"), encoding="utf-8") as fh:
            oa = (json.load(fh) or {}).get("oauthAccount") or {}
    except (OSError, ValueError, AttributeError, TypeError):
        oa = {}
    if not isinstance(oa, dict):
        oa = {}
    dirs = _account_dirs(env)
    acct_uuid = oa.get("accountUuid")
    if acct_uuid:
        org_uuid = oa.get("organizationUuid")
        exact = [(a, o, p) for a, o, p in dirs if a == acct_uuid and o == org_uuid]
        if exact:
            a, o, p = exact[0]
            return Account(a, o, oa.get("emailAddress") or "", p, "oauth")
        for a, o, p in dirs:
            if a == acct_uuid:
                return Account(a, o, oa.get("emailAddress") or "", p, "oauth")
    for cand in env.store_candidates:
        cfg = os.path.join(os.path.dirname(cand), "config.json")
        try:
            with open(cfg, encoding="utf-8") as fh:
                last = (json.load(fh) or {}).get("lastKnownAccountUuid")
        except (OSError, ValueError, AttributeError, TypeError):
            continue
        if last:
            matches = [(a, o, p) for a, o, p in dirs if a == last]
            if len(matches) == 1:
                a, o, p = matches[0]
                return Account(a, o, "", p, "config")
            if len(matches) > 1:
                return None    # ambiguous org under this account - fail closed
            # zero matches: this candidate's config names an account with no
            # store dir on disk yet - try the next store candidate's config
    return None


def _require_verified_platform(env, what):
    """Refuse desktop-store mutations on a platform whose layout is unverified.

    There is deliberately NO override flag. The store layout is confirmed only
    on Windows; macOS reportedly has two candidate layouts - the ordinary
    Application Support path and a sandboxed ~/Library/Containers/... one - and
    neither has been confirmed here. An override would let a user waive a risk
    they have no way to evaluate, which inverts how every other refusal in this
    module works: we fail closed on what we cannot verify rather than asking the
    user to certify it for us.

    Unaffected: read-only commands, and --transcript-only mutations - that
    layout IS verified cross-platform.
    """
    if env.is_windows:
        return
    raise Refusal(
        "desktop-store mutations are Windows-only for now - this platform's store "
        "layout has never been verified, so refusing to {0} it. Read-only commands "
        "(list, doctor) work here, and a session with no desktop listing row (a "
        "CLI-created one) is still movable via --transcript-only, because THAT "
        "layout is verified. On macOS and want the desktop store supported? "
        "'claude-code-sessions doctor --verbose' output in an issue is exactly what "
        "is needed - it is read-only and mutates nothing.".format(what))


def _guard_mutation(env, what, whose="another account's store",
                    because=("No identity-file evidence can make 'the destination "
                             "is dormant' certain enough to mutate under a running "
                             "app")):
    """Refuse to WHAT WHOSE store while the Claude desktop app is running.
    Applies to every mutation route, whatever named the live account.

    `whose` and `because` are parameters because this guard now covers routes
    with different targets. `sync` writes into the account you are NOT signed
    into, and both defaults are written for it. `new-row` defaults to the
    account you ARE signed into, so telling that user it refuses to touch
    "another account's store" describes something they did not ask for, and
    "the destination is dormant" is not the claim being made about it. The
    guard is identical either way; only what it is guarding is named.

    RULING 4 (2026-08-02). The E4 verification measured ~/.claude.json's
    oauthAccount STALE across a real desktop account switch while
    config.json's lastKnownAccountUuid tracked it - the inverse of the
    ordering this module shipped trusting, and the whole-branch review had
    already built the opposite case synthetically. Either identity file can
    be the stale one, so no file evidence is allowed to certify "the
    destination is dormant" while the app runs; the oauth exemption this
    function used to carry is gone. claude_running is narrowed to the
    desktop app's own processes, so a Claude Code CLI session never trips
    this.
    """
    _require_verified_platform(env, what)
    running = claude_running(env)
    if not running:
        return
    dis = _identity_disagreement(env)
    extra = ""
    if dis:
        # The desktop-switch caveat is change 4's: config.json has been
        # measured both tracking a switch (2026-08-02) and keeping the
        # previous account across one (2026-08-29), so "so the two agree"
        # must not read as a promise the user can reliably keep.
        extra = (
            "\nAlso: ~/.claude.json ({0}) and config.json ({1}) disagree about the "
            "signed-in account. Re-authenticate the CLI (run 'claude', then /login) "
            "as the account you use, or switch the desktop app to it, so the two "
            "agree (a desktop switch may not refresh config.json - it has been "
            "measured both tracking one and keeping the previous account). "
            "--live does not lift this guard - close the app."
            .format(dis[0][:8], dis[1][:8]))
    if running[0] == _PROC_UNAVAILABLE:
        # "Couldn't look" is never "nothing there" (Task 2), but it is also
        # never "the app IS running" - that wording would be a lie here, and
        # "close the desktop app" is misleading advice when what actually
        # failed is reading the process list. Say what is really true.
        raise Refusal(
            "the running-process list could not be read, so whether the Claude "
            "desktop app is running cannot be confirmed; refusing to {0} {1} "
            "while that is unavailable - re-run once the process "
            "list can be read.{2}".format(what, whose, extra))
    raise Refusal(
        "the Claude desktop app appears to be running ({0}); refusing to {1} {2} "
        "while it is. {3} - close the desktop app and re-run.{4}{5}".format(
            running[0], what, whose, because, extra,
            helper_hash_note(running, env)))


# _certified_live_account's three states (RULING 5). Tri-state on purpose:
# "the certification didn't validate" must never quietly become "no
# certification, proceed by the old rules" - the old rules PROCEED in the
# no-evidence and third-account cases, and a voided certification falling
# into a proceed path would be exactly the fail-open this module never
# allows.
_CERT_ABSENT, _CERT_VALID, _CERT_VOID = "absent", "valid", "void"


# What `new-row` and its undo/recover routes pass to _guard_mutation. Named
# rather than repeated at the three call sites, so the three can never drift
# into saying different things about the same guard.
NEW_ROW_STORE = "the session store"
NEW_ROW_GUARD_WHY = ("The app rewrites these rows as it opens sessions, so one "
                     "added or removed underneath it can be overwritten or lost")


def _certified_live_account(env, m):
    """(state, account) for a sync manifest's --live certification (RULING 5).

    The manifest key is the ONE piece of manifest content the executor
    honors for an identity decision, and only after revalidating every part
    of it against the identity files on disk, right now:

    - _CERT_ABSENT: no "live_override" key. Behavior everywhere: exactly
      the pre-RULING-5 rules, byte for byte.
    - _CERT_VALID: a disagreement exists NOW; the recorded pair equals it
      positionally (order deliberately significant - a flipped direction
      means both files changed claims since planning, i.e. the world moved
      twice, and a twice-moved world is not the tie the user arbitrated);
      the asserted account is a member, equals the manifest's own
      source_account, and the derivable audit fields (overrode_file,
      overrode_uuid) tell the same story as the operative ones - an
      internally inconsistent record never certifies, however well-typed.
    - _CERT_VOID: the key is present but anything above failed, garbage
      shapes included. Callers refuse (unless live_account() resolves, in
      which case the ordinary live-match rules apply - see
      _refuse_dest_possibly_live).

    Exception-free in the _sync_row_drift style: wrong types, missing keys,
    tuple-vs-list mismatches all classify as void, never raise.

    NOT validated, deliberately: config_path - best-effort audit data in
    the same class as source_resolved_from, not re-derivable on a machine
    whose config roots changed since planning.
    """
    if "live_override" not in m:
        return _CERT_ABSENT, None
    ov = m.get("live_override")
    if not isinstance(ov, dict):
        return _CERT_VOID, None
    acct = ov.get("account")
    pair = ov.get("pair")
    dis = _identity_disagreement(env)
    if dis is None:
        return _CERT_VOID, None
    if not (isinstance(acct, str) and acct in dis):
        return _CERT_VOID, None
    if not isinstance(pair, (list, tuple)) or list(pair) != list(dis):
        return _CERT_VOID, None
    if acct != m.get("source_account"):
        return _CERT_VOID, None
    oauth_uuid, config_uuid = dis
    expected = (("~/.claude.json", oauth_uuid) if acct == config_uuid
                else ("config.json", config_uuid))
    if (ov.get("overrode_file"), ov.get("overrode_uuid")) != expected:
        return _CERT_VOID, None
    return _CERT_VALID, acct


def _refuse_dest_possibly_live(env, live, dest_path, what, live_match_message,
                               cert=(_CERT_ABSENT, None)):
    """Shared by execute_sync_op and _sync_delete_targets: refuse when
    dest_path might be the live account's store, by either of two
    independent tests. Factored into one place so the two sites cannot
    drift apart on this - the whole reason this helper exists.

    1. live_account() resolved a live account outright, and dest_path IS
       that account's store (realpath/normcase both sides, matching
       ensure_contained - see the callers' own comments on junctions).
       Unchanged from before Task 1: callers pass their own
       `live_match_message` (a zero-arg callable, evaluated only on an
       actual match - so it may safely assume `live` is not None) because
       the two sites' wording differs (sync's write-side voice vs undo's
       delete-side voice) and existing tests pin that wording.

    2. live_account() returned None *because the identity files disagree*
       (_identity_disagreement). Task 1 made None mean this too, not only
       "no evidence at all" - and a disagreement is not "safe to proceed":
       either of the two disagreeing accounts could be the one genuinely
       live. So if dest_path resolves under EITHER named account's store on
       disk, refuse, naming both 8-char id prefixes and the fix - mirroring
       _guard_mutation's own disagreement note. dest_path under some THIRD
       account's store (named by neither uuid) is not covered by this
       disagreement at all and proceeds, same as today.

    No disagreement and live is None (genuinely no evidence, e.g. no
    identity file resolves anything) falls through both checks and
    proceeds - unchanged from before Task 1.

    CERT (RULING 5) is _certified_live_account's verdict on the manifest's
    --live certification, and it modulates ONLY test 2:
    - valid: the certified account is asserted-live, so a destination under
      its store still refuses; the OTHER named account is asserted-dormant
      and proceeds - the point of the flag; a third account proceeds as
      today.
    - void: refuse outright, before the disagreement test - a certified
      operation never executes in a state where the files can neither
      validate the assertion nor resolve an account. (When they DO resolve,
      live is not None and test 1 already applied the ordinary rules; the
      certification is moot, not honored.)
    """
    real_dest = os.path.normcase(os.path.realpath(dest_path))
    if live is not None:
        if real_dest == os.path.normcase(os.path.realpath(live.path)):
            raise Refusal(live_match_message())
        return
    state, certified = cert
    if state == _CERT_VOID:
        dis = _identity_disagreement(env)
        now = ("they now name a different disagreement ({0} vs {1}), or the "
               "record does not match this operation".format(dis[0][:8], dis[1][:8])
               if dis else
               "they no longer disagree, and no account currently resolves")
        raise Refusal(
            "this operation carries a --live certification that no longer holds: "
            "it was recorded against a specific disagreement between "
            "~/.claude.json and config.json, and {0}; refusing to {1}. Re-plan "
            "the sync, or restore an identity state the files can resolve, then "
            "retry.".format(now, what))
    dis = _identity_disagreement(env)
    if dis is None:
        return
    oauth_uuid, config_uuid = dis
    named_dirs = _account_dirs(env)
    for acct_uuid in (oauth_uuid, config_uuid):
        if state == _CERT_VALID and acct_uuid != certified:
            continue        # asserted-dormant under the certification: allowed
        for a, o, p in named_dirs:
            if a == acct_uuid and real_dest == os.path.normcase(os.path.realpath(p)):
                if state == _CERT_VALID:
                    raise Refusal(
                        "your --live assertion names {0} as the account the "
                        "desktop app is signed into, and the destination is that "
                        "very account's store; refusing to {1} - sync never "
                        "touches the asserted-live store."
                        .format(certified[:8], what))
                raise Refusal(
                    "~/.claude.json ({0}) and config.json ({1}) disagree about the "
                    "signed-in account, so which one is actually live is unknowable "
                    "from files alone; the destination matches the store of one of "
                    "those two possibly-live accounts, so refusing to {2}. "
                    "Re-authenticate the CLI (run 'claude', then /login) as the "
                    "account you use, or switch the desktop app to it, so the two "
                    "agree, then re-run.".format(oauth_uuid[:8], config_uuid[:8], what))


def _listing_row_count(path):
    """How many listing rows a candidate store holds, or None if the directory
    could not be read.

    Best-effort on purpose, like dormant_account_email: this only ever labels
    a line inside a refusal that is already stopping the command, so a failed
    listdir must not escalate into a LayoutError and replace an informative
    refusal with a worse one. That is why it calls os.listdir directly rather
    than _listdir_or_refuse - and it is not a hole in the fail-closed rule,
    because nothing here decides anything; the refusal has already been
    decided by the caller.

    None must never be flattened to 0 by callers. "Couldn't look" is never
    "nothing there", and here that mistake bites hardest: the store printed as
    empty is the one the user will rule out, so labelling an unreadable
    290-row store "no listing rows" would point them at the wrong destination
    - the exact failure this count exists to prevent."""
    try:
        names = os.listdir(path)
    except OSError:
        return None
    return sum(1 for n in names if n.startswith("local_") and n.endswith(".json"))


def _candidate_line(account_uuid, org_uuid, path, rows):
    """One line of a "which store did you mean" listing. The store path is
    part of it because the 8-char id prefixes alone are not always
    distinguishing: two store roots (Windows' MSIX path and the classic
    %APPDATA% path) can hold the same account, and telling the user to "be
    more specific" while showing two identical lines is a wall, not a
    refusal. Redacted like everything else by main()'s redact().

    `rows` (from _listing_row_count) is on the line for the same reason the
    path is. Observed August 2026: the desktop app created a store directory
    holding exactly one file - scheduled-tasks.json, 87 bytes - and no listing
    rows at all. That empty artifact became a second candidate and turned a
    previously unambiguous sync into a refusal whose two lines differed only
    in an org-id prefix, saying nothing about which held 290 sessions and
    which held none. The path could not settle it either: both sat under the
    same store root.

    `cross_org` marks a candidate that pairs its account with the org of the
    account you are SIGNED IN AS. Observed 2026-08-08 on a two-account machine:
    the store is filed per <account>/<org> PAIR, and all four combinations of
    two accounts and two orgs existed on disk - but only the two pairing an
    account with its OWN org held sessions (266 and 315 rows); both cross pairs
    held none. That makes the tag a second discriminator, and one that works
    where the row count cannot: a genuinely fresh second account also shows zero
    rows, so "empty" alone does not separate scaffolding from new. Evidence
    offered, never a filter applied - the pairing behaviour is an observation
    about an undocumented layout, not a rule the app promises to keep."""
    if rows is None:
        label = "(row count unreadable)"
    elif rows == 0:
        label = "(no listing rows)"
    else:
        label = "({0} row{1})".format(rows, "" if rows == 1 else "s")
    return "   {0}/{1}   {2:<17}   {3}".format(
        account_uuid[:8], org_uuid[:8], label, path)


def _candidate_listing(items, live_org=None):
    """The whole "which store did you mean" block: one _candidate_line per
    (account, org, path) triple, plus a footnote when any candidate holds no
    listing rows.

    The footnote is there because the empty candidate is deliberately NOT
    dropped from the list. Excluding zero-row stores would make the refusal
    narrower rather than clearer, and would be wrong twice over: a store with
    no rows yet becomes a legitimate destination the moment its account/org
    pair is signed in to, and silently deciding for the user which stores are
    real is the opposite of how the rest of this module behaves. So the count
    is offered as evidence and the choice stays theirs.

    `live_org` (the signed-in account's organization, when one is resolved) adds
    the second discriminator described in _candidate_line. It is optional because
    the refusals raised BEFORE a live account is resolved - "stores found" when
    neither identity file names one, and --live's own listings - have no
    signed-in org to compare against, and inventing one there would be worse
    than omitting the tag."""
    lines, any_empty, any_cross = [], False, False
    for account_uuid, org_uuid, path in items:
        rows = _listing_row_count(path)
        cross = bool(live_org) and org_uuid == live_org
        any_empty = any_empty or rows == 0
        any_cross = any_cross or cross
        lines.append(_candidate_line(account_uuid, org_uuid, path, rows))
        if cross:
            # Its OWN line, not a suffix. A redacted store path already runs
            # ~110 characters, so a trailing tag landed past column 155 and
            # wrapped off-screen in any real terminal - present in the string,
            # invisible to the reader, which is worse than absent.
            lines.append("        ^ shares your signed-in org - the likelier "
                         "scaffolding, see below")
    if any_empty:
        lines.append(
            "A store with no listing rows holds no sessions yet - the app created the\n"
            "directory but never filled it. It is still listed, not ruled out: an empty\n"
            "store becomes a real destination as soon as you sign in to that account.")
    if any_cross:
        lines.append(
            "'shares your signed-in org' means that store pairs its account with the\n"
            "ORGANIZATION of the account you are signed in as. Sessions are filed per\n"
            "<account>/<org> pair, and on the machine this was measured on only the pairs\n"
            "joining an account to its OWN org held any sessions. Such a store is the\n"
            "likelier scaffolding artifact - but it is a hint from an undocumented layout,\n"
            "not a rule, so it is flagged rather than hidden.")
    return "\n".join(lines)


_AGENT_MODE_DIR = "local-agent-mode-sessions"


_EMAIL_MEMO = "account-emails.json"


def _email_memo_path(env):
    return os.path.join(os.path.dirname(env.ops_dir), _EMAIL_MEMO)


def remember_account_email(env, account_uuid, email):
    """Record the email of an account seen SIGNED IN, for later use as a label.

    ~/.claude.json names only the live account, so the destination of a sync
    is exactly the account whose email is hardest to recover. But every account
    is the live one sometimes: sync from A to B today, from B to A tomorrow, and
    the pair is learned. This turns "the account you sync into is a hex prefix"
    into a self-healing problem rather than a permanent one.

    Best-effort in both directions - a failure to record, or a corrupt memo, is
    never an error. This only ever improves a label; the store path printed
    beside it is the identifier that actually matters, and the README says so.
    """
    if not account_uuid or not email:
        return
    path = _email_memo_path(env)
    try:
        memo = read_json(path) or {}
        if not isinstance(memo, dict):
            memo = {}
    except (LayoutError, OSError, ValueError, AttributeError):
        memo = {}
    prior = memo.get(account_uuid)
    if isinstance(prior, dict) and prior.get("email") == email:
        return                      # unchanged - do not rewrite for a timestamp
    import time as _time
    memo[account_uuid] = {"email": email,
                          "seen": _time.strftime("%Y-%m-%d", _time.gmtime())}
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(memo, fh, indent=1, sort_keys=True)
        os.replace(tmp, path)
    except OSError:
        pass


def remembered_account_email(env, account_uuid):
    """(email, date_seen) previously recorded for ACCOUNT_UUID, or ("", "").

    Every field is type-checked before it is returned. The file is ordinary JSON
    that a user may edit or a disk may mangle, so a record like
    {"email": 123} is possible - and returning that would push a non-string into
    a "memo:" + seen concatenation and into the --to match string, turning a
    best-effort LABEL into an uncaught TypeError. Anything unexpected is simply
    unknown, which is what this promises.
    """
    try:
        memo = read_json(_email_memo_path(env)) or {}
        rec = memo.get(account_uuid) if isinstance(memo, dict) else None
        if isinstance(rec, dict):
            email, seen = rec.get("email"), rec.get("seen", "")
            if isinstance(email, str) and email:
                return email, seen if isinstance(seen, str) else ""
    except (LayoutError, OSError, ValueError, AttributeError):
        pass
    return "", ""


def account_email(env, account_uuid):
    """(email, source) for a dormant account. source: 'sandbox'|'memo'|''.

    The sandbox lookup is preferred because it is re-derived from the app's own
    files every time, so it cannot go stale. The memo is a record of what was
    true when that account was last signed in, which is good enough to
    RECOGNISE a destination but is not evidence about right now - callers label
    it, rather than passing it off as freshly observed.
    """
    email = dormant_account_email(env, account_uuid)
    if email:
        return email, "sandbox"
    email, seen = remembered_account_email(env, account_uuid)
    if email:
        return email, "memo:" + seen if seen else "memo"
    return "", ""


def dormant_account_email(env, account_uuid):
    """Best-effort email for an account that is NOT signed in, or "".

    `oauthAccount` in ~/.claude.json names only the live account, so for a
    long time this returned nothing and the dry run printed "(email unknown)"
    for the destination - eight hex characters to identify the account you are
    about to write into, which is a poor safety surface for the one command
    that touches a second account.

    It is recoverable. The desktop app runs local agent mode inside a
    per-account sandbox and drops a Claude Code config in it, at
    `<AGENT_MODE_DIR>/<accountUuid>/<orgUuid>/**/.claude/.claude.json`, whose
    own `oauthAccount` names THAT account. Observed on Windows, August 2026.

    Deliberately best-effort, and it must stay that way: the directory only
    exists for an account that has used local agent mode (of the two accounts
    it was found on, one had 109 such files and the other none), it is a
    nested implementation detail of a feature we do not otherwise touch, and
    it can move. Any failure means "unknown", never an error - this only ever
    improves a label.

    The account uuid inside the file must match the one asked for. Reading a
    config and trusting its email without that check would let an unrelated
    sandbox mislabel an account, which is worse than no label at all.
    """
    for root in getattr(env, "store_candidates", ()) or ():
        base = os.path.join(os.path.dirname(root), _AGENT_MODE_DIR, account_uuid)
        for dirpath, dirnames, filenames in os.walk(base):
            if ".claude.json" not in filenames:
                continue
            if os.path.basename(dirpath) != ".claude":
                continue
            try:
                oa = (read_json(os.path.join(dirpath, ".claude.json"))
                      or {}).get("oauthAccount") or {}
            except (LayoutError, OSError, ValueError, AttributeError):
                continue
            if oa.get("accountUuid") == account_uuid and oa.get("emailAddress"):
                return oa["emailAddress"]
    return ""


def _disagreeing_config_path(env, config_uuid):
    """The config.json whose lastKnownAccountUuid carries CONFIG_UUID, or "".

    A pure audit field for the live_override record (RULING 5): more than one
    candidate config can exist on disk (the MSIX root and the classic
    %APPDATA% root), and _identity_disagreement returns only uuids, so this
    re-walks the same candidates in the same order to name the file the
    certification was formed against. Best-effort and NOT part of
    certification validity - see _certified_live_account."""
    for cand in env.store_candidates:
        cfg = os.path.join(os.path.dirname(cand), "config.json")
        try:
            with open(cfg, encoding="utf-8") as fh:
                last = (json.load(fh) or {}).get("lastKnownAccountUuid")
        except (OSError, ValueError, AttributeError, TypeError):
            continue
        if last == config_uuid:
            return cfg
    return ""


def _oauth_email_for(env, account_uuid):
    """oauthAccount's emailAddress when it names ACCOUNT_UUID, else "".

    ~/.claude.json records only its own account's email, so asking for any
    other account yields "" rather than a wrong pairing - the same standard
    dormant_account_email holds its sandbox configs to."""
    try:
        with open(os.path.join(env.home, ".claude.json"), encoding="utf-8") as fh:
            oa = (json.load(fh) or {}).get("oauthAccount") or {}
    except (OSError, ValueError, AttributeError, TypeError):
        return ""
    if isinstance(oa, dict) and oa.get("accountUuid") == account_uuid:
        return oa.get("emailAddress") or ""
    return ""


_PAIR_CHARS = frozenset("0123456789abcdef-")


def _pair_query(q):
    """(acct_prefix, org_prefix) when Q is the tool's own printed account
    form - exactly one '/', both halves non-empty and hex-shaped
    ([0-9a-f-]) after lowercasing - else None.

    Every report labels an account `email (aaaa1111/cccc3333)`, and until
    0.13.0 no matcher accepted any form of that label back: two sites join
    their haystack fields with spaces and the third tests fields
    separately, so the '/' matched nothing anywhere. Nor could substring
    semantics express the form once added: a mid-uuid fragment like
    '1111/cccc' would match INSIDE a joined 'aaaa1111/cccc3333', and a
    legitimate shorter pair like 'aaaa11/cccc33' would fail because its '/'
    lands at a different index. "Two anchored prefixes" is a structure, so
    the pair form is PARSED here and matched by _pair_matches, never
    searched. The hex guard keeps real substrings out of this branch: a
    cygwin-style path carries more than one '/', and an email never has
    hex-only halves, so neither can be captured by accident.
    """
    if q.count("/") != 1:
        return None
    left, right = q.lower().split("/", 1)
    if not left or not right:
        return None
    if not (set(left) <= _PAIR_CHARS and set(right) <= _PAIR_CHARS):
        return None
    return left, right


def _pair_matches(pair, account_uuid, org_uuid):
    """Anchored prefix match of a parsed pair against one store's own ids.

    Prefixes are what a human shortening an id actually types, so the
    printed 8-char form, the full uuids, and any unambiguous prefix pair
    all resolve. A pair-shaped query that matches nothing gets the caller's
    ordinary no-match refusal - it must NEVER fall back into substring
    semantics, which would silently re-admit the over-matching the parsed
    form exists to remove. Two stores genuinely sharing both prefixes hit
    the ordinary >1-match refusal, which is the correct outcome.
    """
    left, right = pair
    return (account_uuid.lower().startswith(left)
            and org_uuid.lower().startswith(right))


def _principal_matches(query, pair, uuid, email, own_dirs):
    """Whether one account's PRINCIPAL - its uuid, its email where known,
    and every store dir it owns - matches the user's string.

    Pair-shaped queries resolve per _pair_query against the principal's own
    (account, org) dirs; anything else is a substring of the principal's
    combined uuid/email/org/path text. Paths are folded through
    _path_match_key on both sides for the reason that function documents:
    the fragments this tool prints, and a person pastes back, carry forward
    slashes whatever the platform's separator is.
    """
    if pair is not None:
        return any(_pair_matches(pair, a, o) for a, o, _p in own_dirs)
    want = _path_match_key(query)
    hay = " ".join([uuid, email or ""]
                   + [o for _a, o, _p in own_dirs]
                   + [_path_match_key(p) for _a, _o, p in own_dirs]).lower()
    return want in hay


def _principal_listing(principals):
    """The candidate listing grouped per ACCOUNT, for account-scope --live
    refusals: each account's header line (email where known, then id), its
    org half rendered '-' because the assertion is account-level, then its
    stores. An account with no store directory is still listed - and named
    still assertable - because the principal exists even when no directory
    does."""
    lines = []
    for u, email, own in principals:
        lines.append("   {0} ({1}/-)".format(email, u[:8]) if email
                     else "   {0}/-".format(u[:8]))
        for a, o, p in own:
            lines.append("   " + _candidate_line(a, o, p,
                                                 _listing_row_count(p)))
        if not own:
            lines.append("      (no store directory on disk - still "
                         "assertable by its id or email)")
    return "\n".join(lines)


def _corroborated_live(env, live, dirs, res):
    """Change 3b (0.13.0), converge only: RES with
    resolved_from="corroborated" when LIVE names the account the identity
    files already agree on and no other account on the machine; None when
    it does not name RES at all (the caller keeps today's refusal
    verbatim - a --live naming anything else under agreement really is
    evidence of confusion, and refusing it is the feature).

    The measured ping-pong this removes (2026-08-29 runbook, documented in
    both directions): plain apply refused under a disagreement -> the user
    re-runs with --live -> the app meanwhile rewrote its file -> --live
    refused under the agreement -> the flag comes off again. When the
    string names the very account the files agree on, refusing certifies
    nothing; it only round-trips the user.

    The uniqueness check runs over EVERY discovered account, because with
    no disagreement there is no two-candidate frame to bound it - without
    it, `--live a` would silently resolve to whichever account the files
    happen to agree on while also naming others. Deliberately
    conservative: a string that incidentally substring-matches some other
    account's path text raises the listing refusal - a safe annoyance,
    never a mis-selection.

    Sync never reaches this (account_scope=False keeps both
    agreement-branch refusals standing): under agreement sync's source is
    resolved by the files and the flag selects nothing - the design's
    round 3 resolved 3b's defect there by deleting the sync branch rather
    than refining it.
    """
    pair = _pair_query(live)
    res_matched, others = False, []
    seen = set()
    for a, _o, _p in dirs:
        if a in seen:
            continue
        seen.add(a)
        email = (res.email if a == res.account_uuid
                 else account_email(env, a)[0])
        own = [(x, o, p) for x, o, p in dirs if x == a]
        hit = _principal_matches(live, pair, a, email, own)
        if a == res.account_uuid:
            res_matched = hit
        elif hit:
            others.append((a, email, own))
    if not res_matched:
        return None
    if others:
        raise Refusal(
            "--live {0!r} names the account the identity files already "
            "agree on ({1}) but also matches {2} other account(s) on this "
            "machine; refusing to treat an ambiguous string as "
            "corroboration. Re-run without --live (the files agree, so "
            "the flag selects nothing), or use a value only that account "
            "matches - the printed account form works too, e.g. "
            "'aaaa1111/cccc3333':\n{3}".format(
                live, res.account_uuid[:8], len(others),
                _principal_listing(others)))
    return dataclasses.replace(res, resolved_from="corroborated")


def _resolve_live_assertion(env, live, dirs, account_scope=False):
    """The Account the user certifies as desktop-live via --live (RULING 5).

    The certified fact is deliberately narrow: "this is the account the
    Claude DESKTOP APP is currently signed into" - the liveness sync's
    safety model actually cares about, since these are the desktop's stores
    and the running-app guard is about the desktop's processes. The two
    identity files can even both be right for their own application (CLI
    authenticated to one account, desktop signed into another); there is
    still exactly one desktop-liveness fact, and the user is its
    authoritative source - they can simply look at the app. That is the
    asymmetry with the rejected macOS layout override
    (_require_verified_platform): a user cannot evaluate a store-layout
    risk, but "which account is my desktop app on" they can.

    Usable ONLY while the identity files disagree - the assertion arbitrates
    a specific two-way tie, cross-checked against a file that already names
    the account. With no disagreement it is refused: agreeing files make it
    unnecessary, and the no-evidence and config-only-ambiguous-org states
    would have it certify a bit no file corroborates at all. One
    converge-only exception (change 3b, _corroborated_live): at
    ACCOUNT_SCOPE, a --live naming exactly the account the files agree on -
    and no other - returns that account marked "corroborated" instead of
    refusing; the caller notes it and records no assertion.

    ACCOUNT_SCOPE (converge passes True; sync, repoint, new-row and retitle
    keep the store-strict default) resolves the ACCOUNT rather than a
    store. The certified fact was always account-level - config.json does
    not even record an org - yet the store-strict matching refused an email
    (the natural way a person names an account, and unambiguous as one) for
    matching the account's three org directories. At account scope the
    string is matched against each disagreeing account's PRINCIPAL - uuid,
    email where known, every store dir it owns (_principal_matches) - and
    exactly one principal matching is acceptance; both or neither keep the
    refusals, with the listing grouped per account. The returned Account
    carries an EMPTY org and path: nothing consumes a display
    representative (converge's recheck tests the account uuid, the labels
    take the email and render a missing org as nothing), a guessed concrete
    pair is something a user might trust or re-paste, and an account whose
    store dirs are missing entirely stays assertable, because the principal
    exists even when no directory does. Sync stays store-strict because its
    resolved store is the SOURCE it reads - there the org half is
    load-bearing.

    Store-scope matching reuses --to's disambiguation semantics over the
    two named accounts' on-disk stores - a pair-shaped value resolves as
    the printed account form (_pair_query), anything else as a substring -
    with one addition: an empty or whitespace-only value is refused
    outright - substring containment would make it match every candidate,
    which on a one-candidate machine is exactly the bare force flag this
    design refuses to be.
    """
    if not live.strip():
        raise Refusal(
            "--live must name the account - an id, org, store-path or email "
            "substring. An empty value would match every candidate, which is the "
            "bare override this flag refuses to be.")
    dis = _identity_disagreement(env)
    if dis is None:
        res = live_account(env)
        if res is not None:
            if account_scope:
                # Converge-only (3b): a --live naming the agreed account,
                # uniquely, is corroboration - a note, not an error. The
                # caller records NO assertion for it (live_asserted stays
                # ""), because nothing was arbitrated.
                got = _corroborated_live(env, live, dirs, res)
                if got is not None:
                    return got
            raise Refusal(
                "--live arbitrates a disagreement between ~/.claude.json and "
                "config.json, and they do not currently disagree - the signed-in "
                "account already resolves to {0} without it. Re-run without "
                "--live.".format(res.account_uuid[:8]))
        raise Refusal(
            "--live arbitrates a disagreement between ~/.claude.json and "
            "config.json, and they do not currently disagree - there is not enough "
            "identity evidence to resolve an account, and --live cannot supply "
            "evidence no file corroborates. Fix: sign in to the Claude desktop app "
            "(which writes config.json) or authenticate the CLI (run 'claude', "
            "then /login) so a file names the account, then re-run without --live.")
    oauth_uuid, config_uuid = dis
    oauth_email = _oauth_email_for(env, oauth_uuid)
    if account_scope:
        pair = _pair_query(live)
        principals = [
            (u,
             (oauth_email if u == oauth_uuid and oauth_email
              else account_email(env, u)[0]),
             [(a, o, p) for a, o, p in dirs if a == u])
            for u in (oauth_uuid, config_uuid)]
        matched = [pr for pr in principals
                   if _principal_matches(live, pair, *pr)]
        if len(matched) == 1:
            u, email, _own = matched[0]
            return Account(u, "", email, "", "user")
        listing = _principal_listing(principals)
        if matched:
            raise Refusal(
                "--live {0!r} matches both accounts the disagreeing "
                "identity files name; be more specific - an email, a "
                "longer id, or the printed account form, e.g. "
                "'aaaa1111/cccc3333':\n{1}".format(live, listing))
        raise Refusal(
            "--live {0!r} matched neither account the disagreeing identity "
            "files name ({1}, {2}). If it names some other account: an "
            "account named by neither file is evidence of something else "
            "being wrong - investigate before writing. The two named "
            "accounts:\n{3}".format(live, oauth_uuid[:8], config_uuid[:8],
                                    listing))
    cands = []
    for a, o, p in dirs:
        if a not in (oauth_uuid, config_uuid):
            continue
        email, src = ((oauth_email, "") if a == oauth_uuid and oauth_email
                      else account_email(env, a))
        cands.append(Account(a, o, email, p, "user", src))
    pair = _pair_query(live)
    if pair is not None:
        matched = [c for c in cands
                   if _pair_matches(pair, c.account_uuid, c.org_uuid)]
    else:
        matched = [c for c in cands if live.lower() in
                   (c.account_uuid + " " + c.org_uuid + " " + c.email + " " +
                    c.path).lower()]
    if len(matched) > 1:
        listing = _candidate_listing((c.account_uuid, c.org_uuid, c.path)
                                     for c in matched)
        raise Refusal("--live {0!r} matched {1} stores; be more specific (a longer "
                      "id, or part of the store path):\n"
                      "the printed account form works too, e.g. "
                      "'aaaa1111/cccc3333'\n{2}"
                      .format(live, len(matched), listing))
    if not matched:
        listing = _candidate_listing((c.account_uuid, c.org_uuid, c.path)
                                     for c in cands)
        # A named account with NO store dir can never be matched, whatever
        # the substring - say so per account rather than implying a typo.
        for u in (oauth_uuid, config_uuid):
            if not any(a == u for a, o, p in dirs):
                extra = "   {0}/-          (no store on disk - cannot be the " \
                        "sync source)".format(u[:8])
                listing = (listing + "\n" + extra) if listing else extra
        raise Refusal(
            "--live {0!r} matched no store belonging to either account the "
            "disagreeing identity files name ({1}, {2}). If it names some other "
            "account: an account named by neither file is evidence of something "
            "else being wrong - investigate before syncing. The two named "
            "accounts' stores:\n{3}"
            .format(live, oauth_uuid[:8], config_uuid[:8], listing))
    return matched[0]


def _authenticated_org(env, account_uuid):
    """The organizationUuid oauthAccount NAMES for ACCOUNT_UUID, or None.

    Deliberately NOT source.org_uuid, which is a resolved *directory* rather
    than an authenticated fact: live_account falls back to the first dir under
    the account when the named org has no dir yet, so that field can itself be
    a cross-pair/scaffolding org. Tagging candidates against it would then flag
    the REAL destination as "shares your signed-in org" and steer the user
    toward the artifact - the precise opposite of the hint's purpose, in a
    safety-sensitive choice.

    Returns None whenever the org is not authenticated for this account -
    including config.json-only resolution, which names the account half and
    nothing about the org. No hint beats a wrong hint.
    """
    try:
        with open(os.path.join(env.home, ".claude.json"), encoding="utf-8") as fh:
            oa = (json.load(fh) or {}).get("oauthAccount") or {}
    except (OSError, ValueError, AttributeError, TypeError):
        return None
    if not isinstance(oa, dict) or oa.get("accountUuid") != account_uuid:
        return None
    org = oa.get("organizationUuid")
    return org if isinstance(org, str) and org else None


def resolve_sync_endpoints(env, to=None, live=None):
    """(source, destination). Source is the signed-in account; destination is
    the other store. Refuses rather than guessing - row-freshness is NEVER
    used to choose, because sync's whole safety model is 'we only ever write
    the dormant store', and a wrong guess writes the live one.

    LIVE (RULING 5) is the one sanctioned exception to "refuses rather than
    guessing", and it is not a guess: while the identity files DISAGREE, the
    user may assert outright which account the desktop app is signed into
    (_resolve_live_assertion). Everything else - destination choice, --to
    narrowing, every refusal - is unchanged."""
    dirs = _account_dirs(env)
    if live is not None:
        source = _resolve_live_assertion(env, live, dirs)
    else:
        source = live_account(env)
    if source is None:
        listing = _candidate_listing(dirs)
        dis = _identity_disagreement(env)
        if dis:
            raise Refusal(
                "cannot identify the signed-in account: ~/.claude.json's oauthAccount "
                "({0}) and config.json's lastKnownAccountUuid ({1}) disagree, and either "
                "can be the stale one - refusing to guess which store is live.\n"
                "--to cannot override this: it names the destination, and without knowing\n"
                "which account is live we cannot verify the one you named is not it.\n"
                "Fix: re-authenticate the CLI (run 'claude', then /login) as the account\n"
                "you are using, or switch the desktop app to that account, so the two\n"
                "files agree - or, if you know which account the desktop app is signed\n"
                "into, assert it with --live <account-id, email, or store-path substring>\n"
                "(RULING 5; the running-app guard still applies).\n"
                "Stores found:\n".format(dis[0][:8], dis[1][:8]) + listing)
        raise Refusal(
            "cannot identify the signed-in account from ~/.claude.json or config.json.\n"
            "Refusing to guess which store is live - naming the wrong one would write the\n"
            "account the app is actively using.\n"
            "--to cannot override this: it names the destination, and without knowing\n"
            "which account is live we cannot verify the one you named is not it.\n"
            "Fix: sign in to the Claude desktop app (which writes config.json) or\n"
            "authenticate the CLI (which writes ~/.claude.json) so one of them names\n"
            "the account.\n"
            "Stores found:\n" + listing)
    others = []
    for a, o, p in dirs:
        if a == source.account_uuid:
            continue
        email, src = account_email(env, a)
        others.append(Account(a, o, email, p, "", src))
    if not others:
        raise Refusal("no other account store on this machine - nothing to sync into")
    if to:
        # The PATH is part of the match string, not just the ids and email.
        # default_env legitimately yields two store roots on Windows (the MSIX
        # package path and the classic %APPDATA%\Claude path), and a machine
        # that migrated between installers can hold the SAME account uuids
        # under both - in which case account_uuid/org_uuid/email are identical
        # for both candidates and no --to value could ever tell them apart.
        # The path is the only thing that differs, so it has to be matchable
        # (and, below, printed) or sync is simply unusable on such a machine.
        pair = _pair_query(to)
        if pair is not None:
            matched = [c for c in others
                       if _pair_matches(pair, c.account_uuid, c.org_uuid)]
        else:
            matched = [c for c in others if to.lower() in
                       (c.account_uuid + " " + c.org_uuid + " " + c.email +
                        " " + c.path).lower()]
        if not matched:
            raise Refusal("--to {0!r} matched no other account store".format(to))
        if len(matched) > 1:
            listing = _candidate_listing(
                ((c.account_uuid, c.org_uuid, c.path) for c in matched),
                _authenticated_org(env, source.account_uuid))
            raise Refusal("--to {0!r} matched {1} accounts; be more specific (a longer "
                          "id, or part of the store path):\n"
                          "the printed account form works too, e.g. "
                          "'aaaa1111/cccc3333'\n{2}"
                          .format(to, len(matched), listing))
        return source, matched[0]
    if len(others) > 1:
        # The cross-pair discriminator - the one signal that still works when
        # every candidate has zero rows (a genuinely fresh second account). Uses
        # the AUTHENTICATED org, never source.org_uuid; see _authenticated_org.
        listing = _candidate_listing(
            ((c.account_uuid, c.org_uuid, c.path) for c in others),
            _authenticated_org(env, source.account_uuid))
        raise Refusal("more than one other account store; name one with --to:\n" + listing)
    return source, others[0]


@dataclasses.dataclass
class SyncFlags:
    to: str = ""
    # RULING 8: refresh rows that already exist at the destination. Every other
    # sync route only ADDS; this is the single path that can overwrite, so it is
    # opt-in per run and never implied.
    update: bool = False
    # RULING 8, amended: refresh ONLY the rows this account's copy is demonstrably
    # newer than. Narrows `update`, never widens it - with update off there are no
    # refreshes for it to act on. A row whose direction cannot be established is
    # skipped too: "only what is newer" is a claim, and an unreadable timestamp on
    # either side means the claim cannot be made.
    newer_only: bool = False
    # Permit a refresh that would leave the conversation it displaces
    # unreachable from every account. Held back by default because it is the
    # one outcome of a refresh that removes access to something rather than
    # updating it - the same treatment --include-deleted's resurrection gets,
    # from the other direction.
    allow_orphan: bool = False
    only: str = ""
    include_deleted: tuple = ()
    verbatim: bool = False
    live: str = ""


def _destination_tombstones(dest):
    """Ids the DESTINATION account has deleted. Only the destination's history
    matters - tombstones are per-account, so the source's deletions say
    nothing about what this account should see.

    Returns raw ids, deliberately not "session ids": a tombstone is filed
    under a row's cliSessionId OR its local id, and callers must test both
    (see _tombstone_ids)."""
    out = set()
    for name in _listdir_or_refuse(dest.path, "the destination store"):
        if name.startswith("deleted_"):
            out.add(name[len("deleted_"):])
    return out


def _tombstone_ids(e):
    """Both ids a tombstone for this row could be filed under.

    The spec said `deleted_<cliSessionId>`, and that was what E4 measured. It
    is not the whole truth. On this machine's own live store the session titled
    'E4 tombstone test' carries TWO tombstones: one named for its cliSessionId
    (bc7333f9...) and one named for its filename stem, i.e. its local id
    (747a0b6e...). So the app files deletions in both id spaces, and a skip
    that checks only the session id can miss a real deletion and resurrect a
    session the account's user deliberately removed - the first row of this
    design's own risk table.

    Checking both is safe in the direction that matters. A false positive
    means declining to copy one row, which the report names and
    --include-deleted overrides; a false negative resurrects a deletion
    silently.
    """
    return [i for i in (e.get("session_id"), e.get("local_id")) if i]


def _resolve_tombstone_overrides(entries, tombs, named):
    """Map each --include-deleted term to exactly ONE tombstoned source row.

    The flag's contract is that it names a single session and "never applies
    blanket to a whole run" - but the match used to be a bare title substring
    tested per row, so one term silently resurrected every tombstoned session
    whose title happened to contain it (the reviewer got three from one
    term). Resolve each term against the tombstoned rows up front instead: a
    full id - either of the two a tombstone can be filed under, see
    _tombstone_ids - matches exactly; anything else is a title substring and
    must single one out. More than one match is a refusal that names the
    candidates - resurrecting a deliberately deleted session is the first row
    of this design's own risk table and must never happen by accident.

    A term that matches nothing is deliberately NOT an error: the destination
    may simply hold no tombstone for that session, in which case the row is
    copied by the ordinary rules and the report's "resurrected" section
    correctly stays empty. Nothing is claimed that did not happen.

    Returns the set of row filenames whose tombstone skip is overridden.
    """
    out = set()
    candidates = [e for e in entries
                  if any(i in tombs for i in _tombstone_ids(e))]
    for term in (named or ()):
        t = term.lower()
        matched = [e for e in candidates
                   if t in [i.lower() for i in _tombstone_ids(e)]]
        if not matched:
            matched = [e for e in candidates if t in e["title"].lower()]
        if len(matched) > 1:
            listing = "\n".join("   {0}  (session {1})".format(e["title"], e["session_id"])
                                for e in matched)
            raise Refusal(
                "--include-deleted {0!r} matched {1} sessions the destination account "
                "deleted. It names ONE session; it is not a blanket override. Re-run "
                "naming a full session id, or a title substring unique to one of:\n{2}"
                .format(term, len(matched), listing))
        out.update(e["name"] for e in matched)
    return out


def _activity_of(d):
    """lastActivityAt out of a PARSED row, or None if it is missing or not a
    number. Both sides of the newer-copy comparison go through this, because
    plan_sync compares them with `>`: letting a string through on either side
    turns one malformed row into a TypeError that kills the whole plan."""
    if not isinstance(d, dict):
        return None
    v = d.get("lastActivityAt")
    # bool is an int subclass and is never a timestamp - exclude it explicitly.
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return v


def _row_activity(blob):
    """lastActivityAt out of a raw row's bytes, or None if it cannot be read.

    None means "could not tell", and callers must never collapse that into
    "older" - the whole point of reading this field is to notice a refresh
    that would move a row BACKWARDS, and an unparseable row is exactly the
    case where that judgement is unavailable.
    """
    try:
        return _activity_of(json.loads(blob.decode("utf-8")))
    except (ValueError, UnicodeDecodeError, AttributeError):
        return None


def _sync_pre_image(r):
    """The measured pre-image bytes of a REFRESH row (RULING 8).

    Fails closed on a manifest that cannot supply one: a MISSING `pre_b64`
    raises KeyError and an explicit null raises ValueError, both of which
    every caller already classifies as "unreadable". An empty STRING is
    valid and returns b"" - a zero-byte destination row is a real state,
    and telling it apart from "no pre-image at all" is the entire reason
    this is a function rather than `r.get("pre_b64") or ""`.

    That idiom is what this replaces. It was introduced to stop a zero-byte
    pre-image being demoted to an add, and it did - but `.get()` turns a
    missing key into "" too, so the `except KeyError` guarding the reversal
    path could never fire and undo would have written a ZERO-BYTE file over
    the destination row instead of refusing. Fail-open, in the one module
    whose whole posture is fail-closed.
    """
    raw = r["pre_b64"]                    # missing -> KeyError -> "unreadable"
    if not isinstance(raw, str):
        # None, or any non-string a hand-edited manifest can carry (a number,
        # a list). unb64 would raise AttributeError on `.encode`, which none of
        # the callers catch and main() does not either - a traceback instead of
        # a refusal, on the one code path whose job is to fail closed.
        raise ValueError("pre_b64 is {0!r}, not a base64 string".format(type(raw).__name__))
    return unb64(raw)                     # "" is a legitimate zero-byte row


def _row_is_refresh(r):
    """Whether a sync row is a REFRESH rather than an add.

    `is_update` OR a present `pre_b64` - either one is enough, deliberately.
    Testing only `is_update` fails OPEN on a damaged manifest: a completed
    refresh that lost that flag (or had it flipped false) while keeping its
    pre-image classifies as an add, and the reversal path then DELETES the
    destination row instead of restoring it. Both fields are written together
    by plan_sync, so disagreement means corruption, and the safe reading of a
    corrupt row is the one that never deletes.
    """
    return bool(r.get("is_update")) or r.get("pre_b64") is not None


def select_sync_rows(env, source, dest, flags):
    """Which source rows are eligible to copy, and why the rest were skipped.

    A row qualifies only if: it is a local_*.json row (not a tombstone or
    other sidecar), it is absent from the destination by filename, its
    transcript still exists somewhere under ~/.claude/projects (a row with no
    transcript is a dead pointer), and the destination holds no tombstone for
    its cliSessionId - unless --include-deleted named it unambiguously
    (_resolve_tombstone_overrides), which overrides the tombstone skip for
    that row only. Such a row is marked `overrode_tombstone` and listed in
    tally["resurrected"] so the report can say what it is about to bring
    back; tally["deleted"] holds only the rows whose deletion was honoured.

    Note that presence is keyed on FILENAME, not cliSessionId: a destination
    row for the same conversation under a different local id is not detected.
    """
    tally = {"present": [], "no_transcript": [], "deleted": [], "unreadable": [],
             "filtered": [], "resurrected": [], "unchanged": [],
             # refreshes whose destination row is NEWER than the source's, or
             # whose age could not be read - filled in by plan_sync, which is
             # where the two sides are finally compared
             "regressing": [], "activity_unknown": [],
             # refreshes --newer-only declined to make: the destination's copy
             # was newer, or which one is newer could not be established
             "held_older": [], "held_same": [], "held_unknown": [],
             # refreshes that change WHICH conversation the row opens, and the
             # subset of those held back because the displaced conversation
             # would be left unreachable from every account
             "swapping": [], "held_orphan": [], "held_orphan_detail": []}
    have = set(_listdir_or_refuse(dest.path, "the destination store"))
    tombs = _destination_tombstones(dest)

    # Parse first, decide second: --include-deleted has to be resolved
    # against the whole set of tombstoned rows to know whether a term is
    # ambiguous, which a single streaming pass cannot see.
    entries = []
    for name in sorted(_listdir_or_refuse(source.path, "the source store")):
        if not (name.startswith("local_") and name.endswith(".json")):
            continue                      # scheduled-tasks.json, deleted_*, *.tmp
        p = os.path.join(source.path, name)
        try:
            d = read_json(p)
        except LayoutError:
            tally["unreadable"].append(name)
            continue
        if not isinstance(d, dict):
            tally["unreadable"].append(name)
            continue
        entries.append({"name": name, "src_path": p, "data": d,
                        "session_id": d.get("cliSessionId") or "",
                        # The row's OTHER id: the filename stem, which is the
                        # local id, not the session id. Tombstones are written
                        # in both spaces - see _tombstone_ids.
                        "local_id": name[len("local_"):-len(".json")],
                        "title": d.get("title") or "(untitled)",
                        # Through the same type check the destination side gets.
                        # plan_sync compares the two with `>`, so a source row
                        # carrying a STRING lastActivityAt raised TypeError and
                        # killed the whole plan - every row, not just that one -
                        # as a raw traceback, since main() catches only Refusal
                        # and LayoutError. Newly reachable with --update: the
                        # pre-existing sort only compares when two rows disagree
                        # on type, while this fires on a single-row plan.
                        # None, NOT 0, when it cannot be read. `or 0` made every
                        # such row compare as older than any real destination
                        # timestamp, so plan_sync flagged it "destination copy is
                        # NEWER; this moves it BACKWARDS" whether or not that was
                        # true - collapsing "could not tell" into a definite
                        # direction, which is the one thing _row_activity's
                        # docstring says callers must never do. Alarmist rather
                        # than dangerous, but a warning that fires on unknown
                        # input is a warning nobody can act on.
                        "last_activity": _activity_of(d)})
    overridden = _resolve_tombstone_overrides(entries, tombs, flags.include_deleted)

    picked = []
    for e in entries:
        name, sid, title = e["name"], e["session_id"], e["title"]
        if flags.only and flags.only.lower() not in title.lower():
            tally["filtered"].append(title)
            continue
        if name in have:
            if not flags.update:
                tally["present"].append(title)
                continue
            # --update: the row exists, so this is a REFRESH. Capture the
            # destination's current bytes as the pre-image; plan_sync drops the
            # row if the transform turns out to produce identical bytes, and
            # execute refuses if these bytes change between planning and writing.
            try:
                with open(os.path.join(dest.path, name), "rb") as fh:
                    pre = fh.read()
            except OSError:
                # Cannot read what we would overwrite - never treat that as
                # "safe to replace". Same fail-closed rule as everywhere else.
                tally["unreadable"].append(title)
                continue
            e["pre"] = pre
            # The destination's OWN last-activity, for the newer-copy check in
            # plan_sync. A row is a per-account snapshot of a shared
            # transcript, so the live account's copy is not automatically the
            # fresher one: measured on this machine, one session's row held
            # 16, 13 and 10 completed turns in three different stores. Without
            # this the refresh silently regresses a destination that was used
            # more recently than the source.
            e["pre_activity"] = _row_activity(pre)
        if not sid or not find_transcripts(env.projects_root, sid):
            tally["no_transcript"].append(title)
            continue
        overrode = False
        if any(i in tombs for i in _tombstone_ids(e)):
            # E4: the app shows a restored row for a deleted session, so this
            # skip is the only thing preventing a resurrection.
            if name not in overridden:
                tally["deleted"].append(title)
                continue
            overrode = True
            tally["resurrected"].append(title)
        picked.append({"name": name, "src_path": e["src_path"], "data": e["data"],
                       "session_id": sid, "title": title,
                       "last_activity": e["last_activity"],
                       "overrode_tombstone": overrode,
                       # bytes currently at the destination, for a refresh only
                       "pre": e.get("pre"),
                       "pre_activity": e.get("pre_activity")})
    # An unreadable timestamp is None now, which cannot be compared with a
    # number - sort it to the far end rather than letting one malformed row
    # raise TypeError and take the whole plan down with it.
    picked.sort(key=lambda r: (r["last_activity"] is not None,
                               r["last_activity"] or 0), reverse=True)
    return picked, tally


# E5: stripping these took a real row from 132,264 to 715 bytes with the
# sidebar, history, responses and connectors all unaffected - the app sources
# connectors from the destination account's own configuration, so the row's
# copy is redundant baggage that would otherwise disclose which integrations
# the source account has and where their endpoints are.
SYNC_STRIP = ("remoteMcpServersConfig", "enabledMcpTools", "bridgeSessionIds",
              "scheduledTaskId")

# A permission granted under one login was never granted under the other.
# NOT yet measured (the E5 row carried no non-default permission state); if the
# app dislikes the defaults the failure mode is a re-prompt, not a leak.
SYNC_RESET = {"alwaysAllowedReasons": [], "sessionPermissionUpdates": [],
              "chromePermissionMode": None, "chromeTabGroupId": None}


def _pointed_session(blob):
    """The cliSessionId a raw row resolves to, or None if it cannot be read."""
    try:
        d = json.loads(blob.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, AttributeError):
        return None
    if not isinstance(d, dict):
        return None
    sid = d.get("cliSessionId")
    return sid if isinstance(sid, str) and sid else None


# A store read that fails is treated as "cannot rule it out", which is correct
# and, without a retry, brittle: a network drive blinking for a moment turns
# into a hard stop on an operation that already passed planning. Three attempts
# over roughly a second recovers a blip without making a genuine failure slow.
# Module-level so a test can shorten them; deliberately small numbers rather
# than a policy object, because the only caller is the one below.
STORE_READ_ATTEMPTS = 3
STORE_READ_BACKOFF = 0.25


def _listdir_retrying(path):
    """os.listdir, retried a bounded number of times on OSError.

    Every OSError is retried, not a curated "transient" subset. Deciding which
    errno means "will never work" is not portable - a Windows network drive can
    surface a momentary outage as ENOENT, EACCES or a WinError with no stable
    mapping - and the cost of being wrong in the retry direction is about a
    second on a failure that was going to be reported anyway. The cost of being
    wrong in the other direction is refusing an operation over a blink.

    Still fails closed: after the last attempt the OSError propagates and the
    caller records its "cannot rule it out" sentinel exactly as before. This
    makes the guard less brittle, never less strict.
    """
    for attempt in range(STORE_READ_ATTEMPTS):
        try:
            return os.listdir(path)
        except OSError:
            if attempt + 1 >= STORE_READ_ATTEMPTS:
                raise
            # Linear, not exponential: the failure this exists for resolves in
            # well under a second, and the total bound matters more than the
            # shape. Worst case here is 0.25 + 0.50 = 0.75s per unreadable
            # store before reporting it.
            time.sleep(STORE_READ_BACKOFF * (attempt + 1))


def _other_pointers(env, dest_path, doomed_rows=()):
    """{cliSessionId: [store labels]} for every row that will SURVIVE this plan.

    Every store is read, including `dest_path`. What is excluded is narrower and
    exact: the rows named in `doomed_rows`, which are the destination rows this
    plan is about to overwrite. Those cannot vouch for the conversation they
    currently open, because they are about to stop opening it.

    Until 2026-08-22 this excluded the whole destination STORE, which was the
    wrong shape in both directions. Too coarse: a conversation still held by
    ANOTHER row in the destination account read as about to become unreachable.
    Hit in live use - a redundant `(fork)` row in the destination was repointed
    at a conversation specifically to keep a second door open, and the very next
    sync still demanded --allow-orphan, because the door it had just been given
    sat inside the store the check skipped. And "just exclude the one row" would
    have been too narrow the other way: a conversation held only by some OTHER
    row this same plan also overwrites is genuinely about to be orphaned, and a
    single-row exclusion would call it safe. Per-row, for every doomed row, is
    the only version that gets both cases right.

    Answers the only question that matters before repointing a row: if this
    account stops pointing at a conversation, does anything else still point at
    it? A row is not just a stale snapshot, it is a POINTER - its filename is
    the app's session id, which survives a resume, while the cliSessionId it
    names changes on each new CLI run. So one sidebar entry accumulates several
    transcripts over time and each account records whichever it last saw.
    Overwriting such a row swaps which conversation that account can open, and
    if no other account holds the displaced one it becomes unreachable from
    every sidebar - present on disk, findable by nothing.

    Built lazily and only when a plan actually contains a pointer swap: it is a
    full read of every other store, which is the same order of work
    select_sync_rows already does, but there is no reason to pay it otherwise.
    """
    real_dest = os.path.realpath(dest_path)
    doomed = set(doomed_rows or ())
    out = {}
    for acct, org, path in _account_dirs(env):
        is_dest = os.path.realpath(path) == real_dest
        label = "{0}/{1}".format(acct[:8], org[:8])
        try:
            names = _listdir_retrying(path)
        except OSError:
            # Fail CLOSED: a store we cannot read might hold the displaced
            # conversation, so we must not report it as orphaned. Recorded
            # under a sentinel the caller treats as "cannot rule it out".
            # Reached only after STORE_READ_ATTEMPTS - a store that is still
            # unreadable a second later is not blinking, it is unavailable.
            out.setdefault(None, []).append(label)
            continue
        for name in names:
            if not (name.startswith("local_") and name.endswith(".json")):
                continue
            if is_dest and name in doomed:
                # This row is about to be overwritten by THIS plan, so it cannot
                # vouch for the conversation it currently opens. Every other row
                # in the destination still can - that is the whole point of the
                # change.
                continue
            try:
                d = read_json(os.path.join(path, name))
            except (LayoutError, OSError, ValueError):
                continue
            if isinstance(d, dict):
                sid = d.get("cliSessionId")
                if isinstance(sid, str) and sid:
                    out.setdefault(sid, []).append(label)
    return out


# Bounds on the transcript comparison below. Transcripts reach tens of MB, so
# this reads real data - but it only ever runs for rows that CHANGE which
# conversation they open, which is a handful even on a machine with hundreds of
# sessions (measured: 7 of 265). Both caps degrade to "unmeasured", never to a
# wrong number.
TRANSCRIPT_COMPARE_MAX_BYTES = 96 * 1024 * 1024
TRANSCRIPT_COMPARE_MAX_ROWS = 40
# Below this many prose turns in the displaced conversation, report "not
# measured" rather than a percentage a handful of messages cannot support.
OVERLAP_MIN_SAMPLE = 8

# Turns the APP writes into a transcript, which no person authored. They are
# excluded from the overlap comparison because counting them answers the wrong
# question: the number exists to say how much of a displaced CONVERSATION is
# also in its replacement, and an interruption marker is not conversation.
#
# Measured 2026-08-22 on three real swap rows that reported 95%, 98% and 98%
# overlap: every single non-matching turn was one of these markers. The metric
# was reporting up to 8 turns "only there" when the true count of authored
# content only there was ZERO - making safe overwrites read as risky, which is
# the opposite of the error the number exists to prevent, and which trains the
# user to click past the warning that guards the destructive case.
#
# Deliberately narrow. Only text the app itself emits is listed; a turn a
# person typed is never filtered, however little it carries, because deciding
# which of the user's own words are "real" is not this function's job. The
# filter is a prefix match so the "... for tool use" variant is covered.
#
# **Over-filtering is NOT free, and an earlier version of this comment claimed
# it was.** It said a conversation pushed below OVERLAP_MIN_SAMPLE reports NOT
# MEASURED "never a wrong percentage", which is only the small-transcript case.
# On a transcript with plenty of turns, dropping one turn that a person really
# authored removes it from BOTH sides of the comparison and can lift the
# overlap to a false 100% - the exact overstatement `_overlap_clause` is
# written to avoid. So the bar for adding an entry here is evidence that the
# app authors the string, not that the string looks like boilerplate.
#
# **"Continue from where you left off." was removed on that test.** It shipped
# in the first draft of this list and three independent reviewers flagged the
# same thing: it appears as a USER-role turn, nothing distinguishes it from a
# person typing the same words, and filtering it contradicts the paragraph
# above. The two that remain are bracketed status text and a fixed system
# string; neither is plausible as authored prose. If a future entry is only
# probably app-emitted, leave it in - a false alarm costs a second look, a
# false 100% costs a conversation.
TRANSCRIPT_PLUMBING_PREFIXES = (
    "[Request interrupted by user",   # both the bare and "for tool use" forms
    "No response requested.",         # emitted when a turn ends without a reply
)


def _message_fingerprints(path):
    """Fingerprints of the PROSE turns in a transcript - what a person said and
    what was said back - or None if it cannot be read or is too big to compare.

    Two deliberate exclusions, both measured rather than assumed:

    **Timestamps and ids.** Resuming a session rewrites them, so a comparison
    that keys on them calls two copies of the same exchange different - it put
    a conversation's overlap with its own continuation at "diverges at message
    8 of 738" while the content of those messages was identical.

    **Tool calls and their results.** They dominate a transcript by count and
    are near-identical boilerplate across unrelated sessions, so counting them
    inflates the answer to the only question this serves: how much of what
    would be displaced is really gone? Measured on two real pairs - counting
    every block put them at 74% and 94% "already in the incoming conversation",
    while the prose those same pairs share is 5% and 36%. The first pair of
    numbers invites a user to tick the box; the second correctly stops them.
    Prose is a smaller sample and the right one.
    """
    try:
        if os.path.getsize(path) > TRANSCRIPT_COMPARE_MAX_BYTES:
            return None
    except OSError:
        return None
    out = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                # Both shapes are checked, not assumed. A JSONL line can decode
                # to a list or a bare string, and `message` can be a string in a
                # malformed or hand-edited transcript - in which case
                # `(d.get("message") or {}).get(...)` raised AttributeError
                # straight out of a function whose caller catches only OSError,
                # crashing plan_sync. Verified before the fix: a line whose
                # message is a string raised `'str' object has no attribute
                # 'get'`. A transcript we cannot parse must degrade to
                # "unmeasured", which is what the comment on the caps promises.
                if not isinstance(d, dict) or d.get("type") not in ("user", "assistant"):
                    continue
                msg = d.get("message")
                if not isinstance(msg, dict):
                    continue
                c = msg.get("content")
                if isinstance(c, str):
                    body = c
                elif isinstance(c, list):
                    body = " ".join(b.get("text") or "" for b in c
                                    if isinstance(b, dict) and b.get("type") == "text")
                else:
                    continue
                body = " ".join(body.split())
                if not body:
                    continue
                if body.startswith(TRANSCRIPT_PLUMBING_PREFIXES):
                    continue        # app-emitted, not conversation - see the constant
                out.append(hashlib.sha1(body[:400].encode("utf-8")).hexdigest()[:12])
    except OSError:
        return None
    return out


import datetime


def _iso_ms(ts):
    """An ISO-8601 transcript timestamp as epoch milliseconds, or None.

    Transcripts write `2026-06-14T09:00:00.000Z`. Parsed with the same
    tolerance the rest of this module applies to the app's format: a value it
    cannot read is None, never a guess, and the caller decides what that means.
    """
    if not isinstance(ts, str) or len(ts) < 19:
        return None
    try:
        base = datetime.datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None
    frac = 0
    if len(ts) > 20 and ts[19] == ".":
        digits = ""
        for ch in ts[20:]:
            if not ch.isdigit():
                break
            digits += ch
        frac = int((digits + "000")[:3]) if digits else 0
    # Stamping UTC explicitly is what makes .timestamp() exact. A naive
    # datetime would be read as local time, so createdAt would be wrong by the
    # machine's offset (8 hours here) and wrong by a DIFFERENT amount on a
    # machine in another zone. Transcripts have always written Z; a value
    # carrying some other offset is parsed as UTC anyway, which is a known and
    # accepted approximation rather than a silent one.
    base = base.replace(tzinfo=datetime.timezone.utc)
    return int(base.timestamp()) * 1000 + frac


def _transcript_facts(env, session_id):
    """Everything a synthesized row needs, read from the transcript itself.

    Refuses rather than guessing. A row built on values that could not be read
    is a guess wearing the app's clothes, and the whole point of the template is
    that it asserts nothing it cannot support.

    Sequential order, NOT minimum and maximum. An earlier draft of the spec said
    to take the earliest timestamp "so a malformed tail cannot move the start",
    which is exactly backwards: scanning the file for a minimum is what lets a
    corrupted, back-dated record at the tail pull the start earlier. The first
    record's timestamp is what resists that - and it is right for the ordinary
    case because transcripts are append-only, which is the real justification.
    """
    found = find_transcripts(env.projects_root, session_id)
    if not found:
        raise Refusal(
            "no transcript on disk for {0}, so there is nothing for a new row to "
            "open. Check the id - 'doctor' lists conversations that no account "
            "points at.".format(session_id))
    if len(found) > 1:
        # The paths alone are evidence, not a way forward - this plan's rule is
        # that a refusal names one, and listing the duplicates and stopping left
        # the user holding the evidence with no instruction.
        raise Refusal(
            "{0} exists in more than one project folder, so a row built for it "
            "would name a file this tool cannot identify; refusing to guess "
            "which. Nothing was written. Remove or rename the copies you do not "
            "want - whichever one is left is what the row will open:\n{1}"
            .format(session_id, "\n".join("   " + f for f in found)))
    path = found[0]
    # Stat BEFORE reading, and again after - see the comparison at the end.
    try:
        before = os.stat(path)
    except OSError as exc:
        raise Refusal("could not stat the transcript for {0}: {1}. Nothing was "
                      "written.".format(session_id, exc))
    cwd = custom = model = effort = None
    first_ms = last_ms = None
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(d, dict):
                    continue
                # cwd is FIRST-wins while model and effort below are LAST-wins,
                # and the asymmetry is deliberate. cwd populates `originCwd` as
                # well as `cwd`, and a session that changed directories mid-way
                # still originated in the first one - which is also what the app
                # sorts and groups by. model and effort are the opposite: what
                # the session was running when it stopped is what a resumed row
                # should carry.
                if cwd is None and isinstance(d.get("cwd"), str) and d["cwd"]:
                    cwd = d["cwd"]
                # LAST-wins, like model and effort. Renaming a conversation
                # appends a new customTitle rather than editing the old record,
                # so first-wins resurrects a title the user already replaced.
                # Measured 2026-08-23: 47 of 507 transcripts on this machine
                # carry more than one distinct customTitle - 9%, not a corner
                # case - and the later one is the live one (one example goes
                # "Task manager performance audit" -> "... (fork)").
                if isinstance(d.get("customTitle"), str) and d["customTitle"].strip():
                    custom = d["customTitle"].strip()
                # LAST of each - see the note on cwd above for why these two go
                # the other way.
                if isinstance(d.get("effort"), str) and d["effort"]:
                    effort = d["effort"]
                msg = d.get("message")
                if isinstance(msg, dict) and isinstance(msg.get("model"), str) \
                        and msg["model"] and not msg["model"].startswith("<"):
                    model = msg["model"]          # skip the "<synthetic>" marker
                ms = _iso_ms(d.get("timestamp"))
                if ms is not None:
                    if first_ms is None:
                        first_ms = ms
                    last_ms = ms
    except OSError as exc:
        raise Refusal("could not read the transcript for {0}: {1}. Nothing was "
                      "written.".format(session_id, exc))
    missing = []
    if not cwd:
        missing.append("no cwd")
    if first_ms is None:
        missing.append("no usable timestamp")
    if not model:
        # A transcript with no assistant record - someone typed a prompt and
        # closed the app before a reply. `model` is on 100% of the 988 rows
        # measured (2026-08-23) and has no zero value, so there is nothing to
        # omit and nothing to derive; the only alternative to refusing is
        # inventing one.
        # Refusing costs the user a conversation in which nothing was said back,
        # which is the cheapest thing this rule could cost. Called out here
        # because it is a deliberate trade, not an oversight - if it ever bites
        # someone with a conversation worth keeping, the fix is a --model flag,
        # not a silent default.
        missing.append("no assistant reply, so nothing records which model it ran")
    if missing:
        raise Refusal(
            "the transcript for {0} parses but cannot populate a row ({1}), so a "
            "row built from it would assert values this tool never read. Nothing "
            "was written.".format(session_id, " and ".join(missing)))
    # _message_fingerprints does its own I/O and returns None for a transcript
    # over TRANSCRIPT_COMPARE_MAX_BYTES. `len(fps or [])` would turn "too big to
    # count" into "0 turns" - a false assertion, and precisely on the large
    # conversations most worth recovering. Unmeasured stays unmeasured, and
    # _synthesize_row omits the field rather than writing a number.
    try:
        fps = _message_fingerprints(path)
    except OSError as exc:
        raise Refusal("could not count the turns in {0}: {1}. Nothing was "
                      "written.".format(session_id, exc))
    # Stat AFTER both reads and compare against the stat taken BEFORE them. The
    # apply-time check re-stats this file and compares, so whatever is recorded
    # here becomes the definition of "unchanged" - and a stat taken only at the
    # end would happily record the state produced by an append that happened
    # DURING the read, baptising a half-read file as the baseline. If the two
    # stats disagree the file moved under us and there is nothing to record.
    try:
        after = os.stat(path)
    except OSError as exc:
        raise Refusal("could not stat the transcript for {0}: {1}. Nothing was "
                      "written.".format(session_id, exc))
    if (after.st_size, int(after.st_mtime)) != (before.st_size,
                                                int(before.st_mtime)):
        raise Refusal(
            "the transcript for {0} was being written while this read it, so the "
            "facts gathered describe no single version of the file. Nothing was "
            "written - re-run once the session is idle.".format(session_id))
    return {"path": path, "cwd": cwd, "created_ms": first_ms, "last_ms": last_ms,
            "turns": len(fps) if fps is not None else None,
            "custom_title": custom, "model": model, "effort": effort,
            "size": after.st_size, "mtime": int(after.st_mtime)}


# The static half of a synthesized row: every field that comes neither from the
# transcript nor from the caller. One place, one comment per field.
#
# EVERY MEMBER EARNED ITS PLACE IN A CENSUS, not in a design meeting. Re-measured
# 2026-08-23 across 988 real rows: 52 distinct keys exist and only 12 appear on
# all of them, so there is no fixed row shape to copy. A field belongs here only
# if it is effectively universal AND has a defensible zero value.
#
# What that ruled OUT is the point: an earlier draft asserted reportFindingsCard
# (60.2% of real rows), chromeTabGroupId (14.6%), lastSpawnRootDetected (6.9%)
# and remoteControlAutoEligible (2.3%) on every synthesized row. Writing a field
# that 97.7% of real rows do not carry is the "plausible-looking default" this
# comment exists to forbid.
#
# THOSE NUMBERS MOVE, AND THAT STRENGTHENS THE RULE RATHER THAN WEAKENING IT.
# In the ONE DAY between the 2026-08-22 census and this re-run, chromeTabGroupId
# went 5.6% -> 14.6%, lastSpawnRootDetected 2.7% -> 6.9% and
# remoteControlAutoEligible 0.9% -> 2.3%; every row carrying either of the last
# two was written in the preceding two days. The app is rolling these fields
# onto rows as it touches them. A field mid-rollout is precisely what a
# synthesized row must not assert - the value is behaviour the app has not
# finished deciding, not a zero. Re-run the census in the plan before adding
# one, and ask whether the number is STABLE, not merely whether it clears 95%.
NEW_ROW_DEFAULTS = {
    "isArchived": False,              # 100% of rows; a recovered row is not archived
    "alwaysAllowedReasons": [],       # 100%; no permission history to inherit
    "sessionPermissionUpdates": [],   # 99.7%; ditto
    "spawnSeed": {},                  # 95.7%; not spawned from anything
    "chromePermissionMode": None,     # 100%; None is the plurality - no Chrome state
    # 100% of rows. Three values observed on 2026-08-23: auto (768),
    # bypassPermissions (202), acceptEdits (18) - they sum to the census total,
    # so a stale row count shows up here as arithmetic that no longer adds up.
    # 'auto' is chosen because it is the most RESTRICTIVE of the three, not
    # because it is the most common - a synthesized row must never hand a
    # resumed session more permission than it had.
    "permissionMode": "auto",
}


def _synthesize_row(session_id, title, title_source, facts, row_uuid):
    """A complete listing row, built from a template plus transcript facts.

    NEVER cloned from a sibling row. The 2026-08-22 prototype cloned one and had
    to strip a `spawnedFrom` asserting descent from a task that never happened;
    cloning also inherits permission modes, MCP configuration, Chrome tab state
    and worktree paths, none of which a new row has any business asserting.

    `lastFocusedAt` is seeded with the transcript's last activity rather than
    omitted. Measured on the prototype: the app REWRITES this field when the row
    is first focused, so seeding is transient - and omitting it risks the app
    sorting a recovered row to the bottom of the sidebar, where a user concludes
    the command failed.
    """
    # dict(NEW_ROW_DEFAULTS) alone is only a SHALLOW copy: alwaysAllowedReasons,
    # sessionPermissionUpdates and spawnSeed would still be the exact same
    # list/dict objects NEW_ROW_DEFAULTS holds, shared across every row built in
    # this process. Nothing mutates them today, but the day some caller does
    # `row["alwaysAllowedReasons"].append(...)` instead of reassigning, that
    # append leaks into every OTHER synthesized row - and into
    # NEW_ROW_DEFAULTS itself, permanently, for the life of the process. Each
    # row gets its own list/dict so mutating one can never touch another.
    row = {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v)
           for k, v in NEW_ROW_DEFAULTS.items()}
    row.update({
        "sessionId": "local_" + row_uuid,
        "cliSessionId": session_id,
        "title": title,
        "titleSource": title_source,
        "cwd": facts["cwd"],
        "originCwd": facts["cwd"],
        "createdAt": facts["created_ms"],
        "lastActivityAt": facts["last_ms"],
        "lastFocusedAt": facts["last_ms"],
        "model": facts["model"],
    })
    # Both omitted rather than defaulted when the transcript could not settle
    # them. effort is absent from 0.6% of real rows, so its absence is a shape
    # the app already tolerates; completedTurns is absent whenever the
    # transcript was too big to count.
    if facts.get("effort"):
        row["effort"] = facts["effort"]
    if facts.get("turns") is not None:
        row["completedTurns"] = facts["turns"]
    return row


def _placeholder_title(facts):
    """A title that does NOT impersonate a summary.

    Used for the orphaned conversations that carry no customTitle of their own -
    120 of 170 on this machine, measured 2026-08-23 by the orphan query in the
    plan's Task 1. Expect the count to drift: it moved 169 -> 170 in one day
    simply because a new session was created, so treat it as a magnitude rather
    than a constant. The app's titles are model-written summaries, so
    nothing mechanical can produce one - and a machine-made title that LOOKS
    like a summary is the failure this whole command is careful about. This one
    is identifying, sortable, and visibly not a summary.

    UTC, matching _iso_ms. time.localtime here would render the same
    conversation as two different dates on two machines, and the row's own
    createdAt - which IS UTC - would disagree with its title.
    """
    day = time.strftime("%Y-%m-%d", time.gmtime((facts["last_ms"] or 0) / 1000.0))
    # Split on BOTH separators, never os.path.basename. The cwd comes out of a
    # transcript, not off this filesystem, so a store synced from Windows can
    # hand a POSIX machine `C:\Users\craig\Projects\Personal` - where basename
    # finds no `/`, returns the whole string, and the "placeholder" becomes an
    # absolute path. The test below pins a Windows cwd precisely so this stays
    # correct when the suite runs on macOS or Linux.
    #
    # A BARE DRIVE IS NOT A LEAF. `C:\` strips to `C:` and splits to `C:`, which
    # is truthy - so a plain split appends "C:" to the title where the intent is
    # to append nothing. ntpath.basename returned "" here and hid the case; the
    # cross-platform split exposes it, so it has to be handled rather than
    # inherited. Measured: C:\ -> 'C:', D:\ -> 'D:', / -> '', \\server\share ->
    # 'share' (a real leaf, correctly kept).
    leaf = re.split(r"[\\/]", (facts.get("cwd") or "").rstrip("\\/"))[-1]
    if re.match(r"^[A-Za-z]:$", leaf):
        leaf = ""
    turns = facts.get("turns")
    parts = [day, "{0} turns".format(turns) if turns is not None
             else "turns not counted"]
    if leaf:                       # a cwd of C:\ or / has no leaf - drop the
        parts.append(leaf)         # clause rather than dangle a comma
    return "(untitled - {0})".format(", ".join(parts))


def _new_row_title(explicit, facts):
    """(title, provenance, title_source).

    Provenance is printed, because the user's decision differs: a customTitle
    was written by a person about that conversation, and a placeholder is an
    admission that nothing was available.

    title_source is what goes IN the row, and it is a claim about authorship
    rather than a formatting detail. 533 of the 537 real rows carrying the field
    say 'auto'; writing 'user' on a machine-made placeholder would tell the app,
    and the next person to read the file, that someone chose it.
    """
    explicit = (explicit or "").strip()
    if explicit:
        return explicit, "yours", "user"
    if facts.get("custom_title"):
        return facts["custom_title"], "the transcript's custom title", "auto"
    return _placeholder_title(facts), "placeholder", "auto"


def _unique_title(title, existing, generated):
    """Suffix a GENERATED title until it is unique within the account.

    A user-supplied duplicate is allowed and merely reported - they asked for
    that exact string. A customTitle counts as generated even though a person
    wrote it: they wrote it for a different row and were never asked whether a
    duplicate here was acceptable, and silence is not consent to a string the
    user has not seen.
    """
    if not generated or title not in existing:
        return title
    n = 2
    while "{0} ({1})".format(title, n) in existing:
        n += 1
    return "{0} ({1})".format(title, n)


import uuid


def _new_row_store(env, flags):
    """(acct, org, path, why, heuristic) - exactly one store, and how it was
    chosen.

    `_repoint_store` returns a LIST, and refusing whenever it returns more than
    one would refuse the ordinary case: an account owns one directory per org, so
    naming it by email matches all of them. `plan_repoint` resolves that by
    letting the ROW settle which store is meant - a luxury this command does not
    have, because the row is what it is about to create.

    So let the CONTENT settle it: the cross-pair directories are empty
    scaffolding, and the account's real store is the one with rows in it.

    Know what that heuristic costs, because it is a guess and not a fact: a
    genuinely new or deliberately empty organization loses to an old populated
    one, and this function would pick the wrong store confidently.

    So the guess is allowed to PLAN and not to WRITE. `heuristic` comes back
    True whenever row counts broke the tie, `plan_new_row` carries it into the
    manifest, and `cmd_new_row` refuses `--apply` on it, naming the STORE
    PATH that would settle it.

    It named the org id until 2026-08-23, and that advice LOOPED. Measured on
    a real machine with three accounts across three orgs: an org id matches one
    directory per account, so re-running with the one the refusal printed
    produced the same three candidates, the same row-count tie-break, the same
    guess and the same refusal - forever. The path was the right answer all
    along and could not be given, because _repoint_store matched no Windows
    path at all until that same day; the org id was chosen to work around a bug
    and inherited its own. A full path matches exactly one directory, which is
    what "settles it" has to mean. An earlier draft relied on printing
    the reasoning before the write instead - which is not a safeguard at all in
    a one-shot `--apply`, because there is no moment between the print and the
    write in which anyone can intervene, and no saved plan to approve. A dry run
    that shows the guess and an apply that refuses to act on it are two
    different promises; only the second one holds unattended.
    """
    hits = _repoint_store(env, flags, what="add a row to")
    if len(hits) == 1:
        a, o, p = hits[0]
        # This string exists to JUSTIFY the choice to the user before --apply,
        # printed by _print_new_row_report as "chosen as ...", so it has to be
        # true on every path that reaches it. On the default path the user named
        # nothing at all and _repoint_store returned the live account's single
        # store; "the only store matching what you named" claimed a match
        # against an argument that was never given.
        if flags.store:
            why = "the only store matching what you named"
        elif flags.live:
            why = "the store of the account you asserted with --live"
        else:
            why = ("the store of the account the identity files agree is signed "
                   "in - nothing was named, so this is the default")
        return a, o, p, why, False
    populated = []
    for a, o, p in hits:
        rows = [n for n in _listdir_or_refuse(p, "an account directory")
                if n.startswith("local_") and n.endswith(".json")]
        if rows:
            populated.append((a, o, p, len(rows)))
    # Every refusal below tells the user to narrow by PATH, and prints the org
    # id beside it because that is the part that differs and so the part a
    # person reads to tell the candidates apart.
    #
    # Not the org id, which is what this said until 2026-08-23 and which does
    # not narrow: an org id matches one directory PER ACCOUNT, so on a machine
    # with three accounts across three orgs, re-running with the org id from
    # this listing returns the same three candidates and refuses again. The
    # advice looped. It was chosen in the first place because _repoint_store
    # matched no Windows path at all - a bug fixed that same day - so this is
    # a workaround outliving the thing it worked around. A full path matches
    # exactly one directory.
    listing = "\n".join("   org {0}   {1}".format(o, p) for _, o, p in hits)
    if not populated:
        raise Refusal(
            "--store {0!r} matched {1} directories and none of them holds any "
            "rows, so nothing distinguishes them. Re-run naming one of these "
            "paths with --store - a full path matches exactly one:\n{2}"
            .format(flags.store, len(hits), listing))
    if len(populated) > 1:
        raise Refusal(
            "--store {0!r} matched {1} directories that each hold rows; refusing "
            "to guess which should get the new one. Re-run naming one of these "
            "paths with --store - a full path matches exactly one:\n{2}".format(
                flags.store, len(populated),
                "\n".join("   org {0}   {1}  ({2} rows)".format(o, p, n)
                          for _, o, p, n in populated)))
    a, o, p, n = populated[0]
    return a, o, p, ("the only one of {0} matching directories that holds any "
                     "rows ({1} of them)".format(len(hits), n)), True


def _row_already_opens(store, session_id, exclude_name=None):
    """(name of a row opening session_id or None, every title in the store).

    `exclude_name` skips one filename entirely - the row THIS op wrote. Both
    answers need it once an op can re-enter after a crash: the reachability
    hit would be our own row, and so would the title. Excluding it in one
    place beats two separate exemptions at the call sites, which is what an
    earlier draft had - and it had the exemption on the reachability check
    only, so `recover --forward` on a written-then-drifted row refused
    against its own title with "it appeared since this was planned".

    FAILS CLOSED. An earlier draft skipped rows it could not parse, which meant
    an unreadable row pointing at this conversation went unseen and the command
    created a second door to it - a fail-open in a module whose entire posture is
    that "couldn't look" is never "nothing there".
    """
    titles = set()
    hit = None
    for name in sorted(_listdir_or_refuse(store, "the store")):
        if not (name.startswith("local_") and name.endswith(".json")):
            continue
        if exclude_name and name == exclude_name:
            continue
        try:
            # read_json already converts ValueError to LayoutError, so that arm
            # is unreachable today. It stays as defence against a future
            # read_json that stops converting - this is a fail-closed path, and
            # the cost of an unreachable except clause is a comment.
            d = read_json(os.path.join(store, name))
        except (LayoutError, OSError, ValueError) as exc:
            raise Refusal(
                "the row {0!r} in this store could not be read ({1}), so this "
                "command cannot tell whether it already opens {2}. Refusing "
                "rather than risk a second row for the same conversation. "
                "Nothing was written. Open that file and repair or remove it - "
                "'doctor' reports rows it cannot parse - then re-run."
                .format(name, exc, session_id[:8]))
        if not isinstance(d, dict):
            raise Refusal("the row {0!r} is not a JSON object; refusing to add "
                          "a row beside it. Nothing was written. Open that file "
                          "and repair or remove it, then re-run.".format(name))
        if d.get("cliSessionId") == session_id:
            hit = name
        if isinstance(d.get("title"), str):
            titles.add(d["title"])
    return hit, titles


@dataclasses.dataclass
class NewRowFlags:
    """Which conversation to surface, in which account, under what name."""
    to_session: str = ""    # the cliSessionId the new row should open
    store: str = ""         # substring naming the store; default = the live one
    title: str = ""         # explicit title; otherwise derived
    live: str = ""          # RULING 5 assertion, as sync and repoint use it


def plan_new_row(env, flags):
    """Build a new-row manifest. Pure planning - writes nothing.

    Creates a row where none existed, which is the one thing `repoint`, `sync`
    and `move` all need to already have been done for them. Measured 2026-08-22:
    170 transcripts on one machine were reachable from no row in any account.
    """
    if not flags.to_session:
        raise Refusal("--to is required: the cliSessionId the new row should open. "
                      "'doctor' lists conversations that no account points at.")
    acct, org, store, why, heuristic = _new_row_store(env, flags)
    label = "{0} ({1}{2})".format(_email_of(env, acct) or acct[:8], acct[:8],
                                  "/" + org[:8] if org else "")
    facts = _transcript_facts(env, flags.to_session)

    # Within ONE account. Several accounts each holding a row for the same
    # conversation is the normal and desirable state; two rows in one sidebar
    # opening the same conversation is the clutter this tool spent a day
    # removing. This is re-checked under the lock in execute_new_row_op - the
    # check here is for the dry run's benefit, and is not the guard.
    hit, existing_titles = _row_already_opens(store, flags.to_session)
    if hit:
        raise Refusal(
            "{0} already opens {1} in this account (row {2!r}), so a new row "
            "would be a second door to the same conversation. Nothing was "
            "written. If that row is the problem - it opens the right "
            "conversation under the wrong title, say - edit it rather than "
            "adding another.".format(label, flags.to_session[:8], hit))

    title, provenance, title_source = _new_row_title(flags.title, facts)
    collision = title if title in existing_titles else None
    title = _unique_title(title, existing_titles, generated=provenance != "yours")

    # The uuid is minted HERE so that everything this manifest reports - the
    # filename, the sessionId inside the row - is internally consistent, and so
    # `--apply --json` reports the id it actually wrote. It is NOT a promise
    # that a later, separate `--apply` run reuses it: there is no way to hand a
    # saved manifest back to the CLI, so a second invocation replans and mints
    # a new one. That is fine; nothing keys on the value.
    row_uuid = str(uuid.uuid4())
    row = _synthesize_row(flags.to_session, title, title_source, facts, row_uuid)
    name = "local_{0}.json".format(row_uuid)
    post = json.dumps(row, separators=(",", ":")).encode("utf-8")
    return {"op_type": "new-row", "store_path": store, "store_label": label,
            "store_why": why, "store_is_a_guess": heuristic,
            "store_org": org, "name": row_uuid,
            "row_path": os.path.join(store, name),
            "title": title, "title_provenance": provenance,
            "title_collision": collision,
            "to_session": flags.to_session, "transcript": facts["path"],
            # The snapshot marker _new_row_preflight compares against - taken
            # from `facts`, NOT re-stat'd here. Re-stat'ing would capture the
            # file as it is now rather than as it was when its facts were read,
            # so an append between the two would silently become the accepted
            # baseline and the very drift this is meant to catch would validate.
            "transcript_size": facts["size"],
            "transcript_mtime": facts["mtime"],
            # Derived from the SAME snapshot, not a fresh stat. A second
            # os.path.getsize here would undo the two lines above it: real
            # I/O happens in between (_row_already_opens scans every row in
            # the store), so the size reported could disagree with the size
            # validated - and a file deleted in that window raises a bare
            # OSError, which main() does not catch and which would surface as
            # an unredacted traceback carrying paths and account uuids.
            "transcript_mb": round(facts["size"] / 1e6, 1),
            "turns": facts["turns"], "cwd": facts["cwd"], "model": facts["model"],
            "rows": [{"name": name, "dest_path": os.path.join(store, name),
                      "title": title, "pre_b64": None, "post_b64": b64(post),
                      "is_update": False, "written": False}]}


def _row_is_ours(r):
    """Whether the file at this row's dest_path is the one THIS op created.

    The evidence is the uuid4 in the row's own `sessionId`: this op minted it at
    plan time, so a file carrying it can only have come from here. That matters
    on the drifted path, where "the app rewrote the row we wrote" and "an
    unrelated file is sitting on our filename" are the same byte-level mismatch
    and want opposite explanations.

    Never raises - it is called from an error path, and a second exception
    thrown while composing the first one's message helps nobody.
    """
    dest, name = r.get("dest_path"), r.get("name")
    if not dest or not name:          # .get, not [] - "never raises" includes
        return False                  # a manifest missing the keys entirely
    try:
        d = read_json(dest)
    except (LayoutError, OSError, ValueError):
        return False
    return (isinstance(d, dict)
            and d.get("sessionId") == os.path.splitext(name)[0])


def _new_row_preflight(env, m):
    """Every non-mutating re-check, run under the lock, before anything is
    journalled or written. Raises Refusal; returns nothing.

    THESE ARE THE REAL GUARDS, not the ones in plan_new_row. Planning runs
    unlocked, so between a dry run and --apply another writer can create a row
    for this conversation, the transcript can age out, move, or be duplicated.

    It is a separate function, and it runs BEFORE `new_op`, for a reason that
    only shows up on the failure path: journalling first meant a perfectly safe
    refusal - transcript gone, duplicate row appeared - left a non-terminal op
    behind, and `doctor` then told the user to run `recover` over a command that
    had touched nothing at all. A refusal that manufactures cleanup work is a
    refusal that trains people to ignore the tool.
    """
    _guard_mutation(env, "create a row in", NEW_ROW_STORE,
                    because=NEW_ROW_GUARD_WHY)
    if not os.path.isdir(m["store_path"]):
        raise LayoutError("store vanished: " + m["store_path"])
    # The transcript must still be THE one this row was planned against - not
    # merely some transcript with that id, and not the same path with different
    # contents. Every fact in the post-image was read out of that file, so a
    # transcript that grew or was rewritten between plan and apply leaves the
    # row asserting timestamps and a turn count that no longer describe it.
    found = find_transcripts(env.projects_root, m["to_session"])
    if not found:
        raise Refusal(
            "the transcript for {0} is no longer on disk, so this row would open "
            "nothing. Nothing was written.".format(m["to_session"]))
    if len(found) > 1:
        raise Refusal(
            "{0} now exists in more than one project folder, so the row planned "
            "against a single transcript no longer names one. Nothing was "
            "written:\n{1}".format(m["to_session"], "\n".join("   " + f
                                                             for f in found)))
    if os.path.realpath(found[0]) != os.path.realpath(m["transcript"]):
        raise Refusal(
            "the transcript for {0} has moved since this was planned ({1} -> "
            "{2}); the row's recorded facts came from the old path. Nothing was "
            "written - re-run to replan.".format(m["to_session"], m["transcript"],
                                                 found[0]))
    # Same path, different bytes. Size and mtime rather than a hash: these
    # transcripts run to 96 MB, re-reading one to prove it is unchanged costs
    # more than the fact is worth, and an append - the way transcripts actually
    # change - moves both.
    try:
        st = os.stat(found[0])
    except OSError as exc:
        raise Refusal("could not stat the transcript for {0}: {1}. Nothing was "
                      "written.".format(m["to_session"], exc))
    if (st.st_size != m.get("transcript_size")
            or int(st.st_mtime) != m.get("transcript_mtime")):
        raise Refusal(
            "the transcript for {0} has changed since this was planned, so the "
            "row's recorded timestamps and turn count no longer describe it. "
            "Nothing was written - re-run to replan.".format(m["to_session"]))
    # Reachability, re-checked under the lock. plan_new_row's identical check is
    # for the dry run's benefit and closes no race at all.
    # Excluding our own row here is what lets this run again after a crash:
    # on re-entry the row may already be on disk, and without the exclusion
    # both checks below would fire against it.
    hit, titles = _row_already_opens(m["store_path"], m["to_session"],
                                     exclude_name=m["rows"][0]["name"])
    if hit:
        raise Refusal(
            "{0} now already opens {1} (row {2!r}) - something created it since "
            "this was planned. Nothing was written.".format(
                m["store_label"], m["to_session"][:8], hit))
    # The title set is re-read too, and not thrown away. _unique_title suffixed
    # past everything that existed at PLAN time; a row created since can hold
    # the suffix that was chosen, and writing it anyway would break the one
    # uniqueness promise this command makes. Only generated titles are checked -
    # an explicit --title duplicate was the user's own call.
    if m.get("title_provenance") != "yours" and m["title"] in titles:
        raise Refusal(
            "another row in {0} is now called {1!r} - it appeared since this was "
            "planned, and that title was chosen to be unique. Nothing was "
            "written; re-run and a fresh suffix will be picked."
            .format(m["store_label"], m["title"]))


def execute_new_row_op(env, op):        # noqa: ARG001 - see the note on env
    """journaled -> writing -> completed. One row, created from nothing.

    `env` is deliberately unused. Every guard that needs the environment runs in
    _new_row_preflight before this is called, so an unused parameter here is the
    structural evidence of that - not an oversight. It stays in the signature
    because recover_op calls this the same way run_new_row does.

    Re-entrant from 'writing' for the same reason execute_repoint_op is: an op
    that died between the two set_status calls must still be finishable, and
    every decision here is made from the bytes on disk rather than from what the
    journal expects to find.
    """
    m = op.manifest
    if m.get("status") not in ("journaled", "writing"):
        raise LayoutError("execute_new_row_op runs ops from 'journaled' or "
                          "'writing'; this one is " + str(m.get("status")))
    r = m["rows"][0]
    # ONE read of the destination, and every branch below dispatches on its
    # result. _sync_row_drift already opens the file, compares it to post_b64,
    # and turns every way that can fail into a state - so re-reading the file
    # here to compare it again would duplicate the work AND reintroduce the two
    # holes an earlier draft had: a bare `open()` whose OSError escaped as an
    # unredacted traceback (main() catches only Refusal and LayoutError), and an
    # `os.path.exists` test that returns False when stat itself fails, so an
    # unreadable-and-unstattable row fell through to atomic_write and got
    # OVERWRITTEN - "couldn't look" becoming "nothing there" in the one function
    # whose whole promise is that it never overwrites.
    #
    # All five states are handled. `pristine` is unreachable for an add
    # (_row_is_refresh is False when is_update is False and pre_b64 is None) but
    # is grouped with `drifted` rather than left to fall off the end.
    state = _sync_row_drift(r)
    if state == "match":
        # THE WRITE ALREADY LANDED - finish the journal and stop.
        #
        # This is the crash `recover --forward` exists for: the row was written
        # and the process died before the marker was saved. Re-validating a
        # transcript whose facts have ALREADY been consumed and committed to the
        # bytes on disk would make a transcript that has since aged out block
        # recover - the one command whose job is to get the user unstuck,
        # refusing to finish bookkeeping for a write that succeeded. The row
        # matching post_b64 byte for byte is better evidence than any
        # re-derivation could be.
        #
        # It also needs no mutation guard: it writes only the journal, so there
        # is nothing in the account's store for a running app to race.
        r["written"] = True
        save_manifest(op)
        set_status(op, "completed")
        return "completed"
    if state == "unreadable":
        raise Refusal(
            "something is at {0!r} but it could not be read, so whether it is "
            "the row this op wrote cannot be settled. Refusing rather than "
            "overwrite a file nobody can see. Nothing was written - check that "
            "file's permissions, or move it aside if it is not wanted, then "
            "re-run.".format(r["name"]))
    # NO PREFLIGHT HERE. It runs in the two places that call this - run_new_row
    # before journalling, recover_op's forward arm before re-entering - and
    # calling it here as well would undo the very fix that moved it out: a
    # refusal raised at this point is raised AFTER new_op, which is what left a
    # non-terminal op behind for a command that changed nothing. One caller, one
    # preflight, always before the journal entry exists.
    #
    # Every refusal that CAN happen before set_status does, for the same reason:
    # a containment failure or a corrupt post-image raised after the flip would
    # leave a 'writing' op behind for a run that touched nothing, which is the
    # pathology that refactor removed - reproduced one layer down.
    #
    # EXACTLY ONE refusal necessarily survives past it, and the message below
    # owns that rather than pretending otherwise: a write cannot be known to
    # fail until it is tried. So the op really is journalled and open when
    # atomic_write raises, re-running really does succeed while leaving it open,
    # and only 'recover --back' closes it - which the message has to say,
    # because 'doctor' will otherwise report an unresolved operation the user
    # was told had changed nothing.
    real = ensure_contained(r["dest_path"], [m["store_path"]])
    if os.path.dirname(real) != os.path.realpath(m["store_path"]):
        raise LayoutError("row {0!r} is not a direct child of {1!r}; refusing"
                          .format(r["dest_path"], m["store_path"]))
    if state != "absent":
        # Something is there and it is not what this op wrote. Two very
        # different situations, and telling the user the wrong one is worse than
        # saying nothing: if this op already wrote the row and the app has since
        # rewritten it, "a different row already exists, nothing was written" is
        # false twice over. Only an op that never wrote is looking at a genuine
        # uuid4 collision. Never overwrite either way: this command adds.
        if _row_is_ours(r):
            raise Refusal(
                "this op wrote {0!r}, and something has changed it since - "
                "most likely the app, which rewrites these rows when it "
                "opens the session. It was NOT overwritten and nothing more "
                "was written; 'recover --back' closes this operation and "
                "leaves the row alone.".format(r["name"]))
        raise Refusal(
            "a different row already exists at {0!r}; refusing to overwrite "
            "it. Nothing was written.".format(r["name"]))
    post = unb64(r["post_b64"])          # cannot raise: 'unreadable' covers it
    set_status(op, "writing")
    try:
        atomic_write(r["dest_path"], post)
    except OSError as exc:
        raise Refusal(
            "could not write the row: {0}. Nothing landed in the store - but "
            "unlike every other refusal here, this one leaves the operation "
            "journalled and open at 'writing', because a write cannot be known "
            "to fail before it is tried. Check the store is writable and re-run "
            "to create the row; then close this one with 'claude-code-sessions "
            "recover --resolve {1} --back --apply', which has nothing to delete "
            "and only closes the record. 'doctor' lists it until you do."
            .format(exc, m.get("op_id")))
    _maybe_crash("new-row-write-before-save")
    r["written"] = True
    save_manifest(op)
    set_status(op, "completed")
    return "completed"


def run_new_row(env, manifest):
    """Lock, journal, execute, rotate - the same shape as run_repoint.

    Journal BEFORE the write: the only crash-visible states are then
    "journalled, not written" - which recover completes or closes - and
    "journalled and written", which is finished. Writing first would allow a row
    on disk that no op knows about, and nothing could find it to undo it.
    """
    # No _guard_mutation here - _new_row_preflight opens with it. Calling it in
    # both places enumerated the running process list twice per apply for one
    # answer, and process enumeration is the slowest thing this command does.
    acquire_lock(env, "new-row")
    try:
        # Preflight BEFORE new_op. Journalling first meant a safe refusal - the
        # transcript aged out, another writer got there first - left a
        # non-terminal op behind, and doctor then told the user to run 'recover'
        # over a command that had touched nothing.
        _new_row_preflight(env, manifest)
        op = new_op(env, manifest)
        manifest["op_id"] = op.manifest["op_id"]
        set_status(op, "journaled")
        final = execute_new_row_op(env, op)
        rotate_ops(env)
        return final
    finally:
        release_lock(env)


def undo_new_row(env, op):
    """Delete the row this op created - but only while it still holds exactly
    what was written.

    The same evidence rule `undo_sync` applies to rows a sync ADDED. If the
    account has since opened the session the app rewrites the row, and deleting
    it would discard that account's own state.
    """
    m = op.manifest
    acquire_lock(env, "undo-" + m["op_id"])
    try:
        if m.get("op_type") != "new-row":
            raise Refusal("not a new-row op: " + str(m.get("op_id")))
        if m.get("status") != "completed":
            raise Refusal("op {0} is '{1}', not 'completed'".format(
                m.get("op_id"), m.get("status")))
        # Shape-validated here for the same reason recover_op validates: `undo`
        # dereferences m["rows"][0] and m["store_path"] straight out of a
        # journal file, and a damaged one raised KeyError or IndexError, which
        # main() does not catch. The branch closed that hole for `recover` and
        # left it open for `undo`.
        #
        # BEFORE _guard_mutation deliberately: a record this cannot read is
        # refusable without enumerating the process list, which is the slowest
        # thing either command does.
        bad = _new_row_shape_error(m)
        if bad:
            raise Refusal(
                "the record for {0} is damaged ({1}), so undo cannot tell which "
                "row it created; refusing rather than guess. Nothing was "
                "changed, and any row it wrote is still on disk - 'doctor' "
                "lists conversations that no account points at."
                .format(m.get("op_id"), bad))
        _guard_mutation(env, "remove a row from", NEW_ROW_STORE,
                        because=NEW_ROW_GUARD_WHY)
        r = m["rows"][0]
        if not r.get("written"):
            raise Refusal("this op never wrote the row; nothing to undo")
        real = ensure_contained(r["dest_path"], [m["store_path"]])
        if os.path.dirname(real) != os.path.realpath(m["store_path"]):
            raise LayoutError("row {0!r} is not a direct child of {1!r}; refusing"
                              .format(r["dest_path"], m["store_path"]))
        state = _sync_row_drift(r)
        if state == "absent":
            raise Refusal(
                "that row is already gone - something removed it since this op "
                "created it. Nothing to undo, and nothing was changed.")
        if state != "match":
            said = ("exists but could not be read" if state == "unreadable"
                    else "no longer holds what this op wrote ({0})".format(state))
            raise Refusal(
                "that row {0}; something "
                "changed it since - most likely the app, which rewrites these "
                "rows whenever it opens the session. Refusing to delete it."
                .format(said))
        try:
            os.unlink(r["dest_path"])
        except OSError as exc:
            raise Refusal("could not remove the row: {0}".format(exc))
        set_status(op, "undone")
        rotate_ops(env)
        return "undone"
    finally:
        release_lock(env)


def _print_new_row_report(say, m):
    say("store   : {0}".format(m["store_label"]))
    # Printed because _new_row_store's choice among an account's org directories
    # is a heuristic. A heuristic the user can see before --apply is a very
    # different thing from one they cannot.
    say("          chosen as {0}".format(m["store_why"]))
    if m.get("store_is_a_guess"):
        say("          ^ that is a GUESS from row counts, not an identification."
            " --apply")
        say("            will refuse until you name the store with --store.")
    say("new row : local_{0}.json".format(m["name"]))
    say("title   : {0}   ({1})".format(m["title"], m["title_provenance"]))
    if m.get("title_collision"):
        say("          another row in this account is already called {0!r}"
            .format(m["title_collision"]))
    say("")
    say("will open : {0}   ({1} MB, {2})".format(
        m["to_session"], m["transcript_mb"],
        "{0} prose turns".format(m["turns"]) if m["turns"] is not None
        else "too large to count turns"))
    say("project   : {0}".format(m["cwd"]))
    say("model     : {0}   (read from the transcript)".format(m["model"]))
    say("")
    say("This creates a NEW sidebar row. Nothing existing is changed, and the")
    say("conversation itself is not touched. No row in THIS account opens it")
    say("today; other accounts are not consulted, and may well have one.")


def _public_new_row_manifest(m):
    """The new-row manifest with the row image removed, for --json.

    Same rule as `_public_repoint_manifest` and `_public_manifest`: a listing
    row carries `remoteMcpServersConfig` and permission state, and printing it
    to stdout lets ordinary automation log an account's connector configuration.
    This command's post-image is synthesized rather than copied out of an
    account, which makes it less sensitive and not differently governed - it
    goes through the same filter rather than around it.
    """
    out = {k: v for k, v in m.items() if k != "rows"}
    out["rows"] = [{k: v for k, v in r.items() if k not in ("pre_b64", "post_b64")}
                   for r in m.get("rows", [])]
    return out


def cmd_new_row(env, ns):
    flags = NewRowFlags(to_session=ns.to_session, store=ns.store,
                        title=ns.title, live=ns.live)
    m = plan_new_row(env, flags)

    def say(line):
        """The report line, redacted by default and never a traceback.

        The same `print(line if ns.verbose else redact(env, line))` form
        cmd_recover, cmd_doctor and cmd_repoint use - this command was printing
        the plan through a bare `print`, so the project directory went out in
        full while the README promised otherwise.

        The try/except is the second half, and it is not decoration either.
        Piped stdout on Windows is the console codepage (cp1252 on the machine
        this was measured on), so a cwd holding a character outside it made
        print() raise UnicodeEncodeError - a bare traceback, out of a report
        that prints BEFORE the write, so the command aborted having done
        nothing at all. Replacement characters are a worse report than the real
        one and a far better one than a stack trace over a command that then
        refused to run. It is here rather than in _print_new_row_report because
        the report is only half the output; the result trailer goes through the
        same wrapper.
        """
        text = line if ns.verbose else redact(env, line)
        try:
            print(text)
        except UnicodeEncodeError:
            enc = getattr(sys.stdout, "encoding", None) or "utf-8"
            print(text.encode(enc, "replace").decode(enc, "replace"))

    # The HUMAN report prints before the write, unlike cmd_sync and cmd_repoint,
    # and the difference is deliberate. _new_row_store may have picked this
    # account's store out of several by a heuristic, and the plan justifies that
    # heuristic by saying the user sees it before anything happens. Printing
    # afterwards made that false for `--apply` in one shot - which is how the
    # command will usually be run, because there is no way to hand a saved dry
    # run back to the CLI, so a separate dry run replans and proves nothing
    # about what the apply will choose.
    #
    # --json keeps the other order (apply first, then print), exactly as
    # cmd_sync and cmd_repoint do: printing first meant `--apply --json`
    # reported a plan, exited 0, and wrote nothing, which automation reads as a
    # completed operation.
    if not ns.json:
        _print_new_row_report(say, m)
        if ns.apply:
            say("")
    # A guessed store may PLAN but never WRITE. Printing the guess and then
    # writing anyway leaves no moment for anyone to intervene, so the dry run
    # shows it and the apply refuses until the user settles it themselves.
    if ns.apply and m.get("store_is_a_guess"):
        raise Refusal(
            "which store should get this row was decided by counting rows, not "
            "by anything that identifies the account: {0}. That is fine for a "
            "dry run and not fine for a write. Re-run with --store {1!r} if that "
            "is the one you mean. Nothing was written."
            .format(m["store_why"], m["store_path"]))
    final = run_new_row(env, m) if ns.apply else None
    if ns.json:
        pub = _public_new_row_manifest(m)
        if final is not None:
            pub["result"] = final
        print(json.dumps(pub, indent=1))
        return 0 if final in (None, "completed") else 1
    if final is None:
        say("\ndry run - pass --apply to create the row")
        return 0
    say("result  : {0}".format(final))
    if final == "completed":
        say("Reopen the app - the session should be in the sidebar. 'undo' "
            "removes the row again.")
        return 0
    # cmd_move and cmd_sync both gate their exit code on the result; cmd_repoint
    # returns 0 unconditionally, and that is the sibling not to copy. Today
    # execute_new_row_op can only return "completed" or raise - but an
    # unconditional success trailer plus exit 0 is a trap laid for whoever adds
    # a second return value later.
    return 1


def _displaced_overlap(env, old_sid, new_sid):
    """How much of the conversation a refresh DISPLACES also lives in the one it
    brings in - a float 0.0-1.0, or None when it cannot be measured.

    This is the number that makes an orphan warning actionable. "This row will
    open a different conversation and nothing else points at the old one" is
    true of every propagation in a multi-account workflow, because resuming a
    session mints a new transcript id each time and each account records
    whichever it last saw. Held back on that alone, the guard fires on the
    normal case and reads as the tool being broken - measured on a real
    machine, it held back all 7 candidate rows, of which 5 would have displaced
    a conversation whose content was already 72-100% present in the incoming
    one. The other 2 were 36% and 5%, and genuinely deserved the pause.

    So: measure it, and let the number carry the decision. 1.0 means the
    displaced conversation is wholly contained in its replacement and nothing is
    reachable-only-there; a low number means it is substantially its own
    conversation.
    """
    if not old_sid or not new_sid or old_sid == new_sid:
        return None
    old = find_transcripts(env.projects_root, old_sid)
    new = find_transcripts(env.projects_root, new_sid)
    if not old or not new:
        return None                      # nothing on disk to compare
    if len(old) > 1 or len(new) > 1:
        # find_transcripts returns EVERY project directory holding that session
        # id, and taking [0] would silently compare whichever the directory walk
        # happened to reach first. A number derived from a file we only might
        # have wanted is worse than no number - report it unmeasured.
        return None
    a = _message_fingerprints(old[0])
    if not a or len(a) < OVERLAP_MIN_SAMPLE:
        # Too few prose turns to say anything honest. A percentage off three
        # messages reads as precise and is not; "not measured" is the truthful
        # answer and the one the caller already knows how to print.
        return None
    b = _message_fingerprints(new[0])
    if b is None:
        return None
    seen = set(b)
    return sum(1 for h in a if h in seen) / float(len(a))


def _displaced_sizes(env, old_sid, new_sid):
    """(displaced_turns, incoming_turns) - prose-turn counts for the two sides
    of a pointer swap, or (None, None) when either cannot be counted.

    A LENGTH, alongside the overlap percentage, because they answer different
    questions and only one of them was being asked. Overlap says how much of
    the displaced conversation survives in its replacement; it says nothing
    about whether the ROW ends up on a shorter conversation than it started on.

    Both directions of that were hit in live use on 2026-08-22 and neither was
    visible in the plan: a row going 436 -> 336 turns, and another going 338 ->
    130, each reported only as a percentage the user had to convert into a
    length judgement in their head. "436 -> 336 turns" needs no conversion.

    Deliberately reuses the same fingerprint list the overlap uses, so the two
    numbers can never disagree about what counts as a turn - including the
    plumbing exclusion.

    **Cost, stated accurately.** An earlier version of this docstring said it
    "costs nothing extra" because the page cache serves the re-read. That is
    only true of the disk I/O: this re-runs the JSON parse, the normalisation
    and the hashing on both files. Bounded by the same
    TRANSCRIPT_COMPARE_MAX_ROWS the overlap loop runs under, and only for rows
    that actually swap, but it is real CPU rather than free.

    **Scope of "reported even when the overlap cannot be measured."** True for
    a row the loop reaches: a pair too short to yield an honest percentage
    still yields a length. NOT true past the row cap - the loop breaks there
    and the remaining rows get neither number, which is the cap working as
    documented rather than a length-specific gap.
    """
    if not old_sid or not new_sid or old_sid == new_sid:
        return (None, None)
    old = find_transcripts(env.projects_root, old_sid)
    new = find_transcripts(env.projects_root, new_sid)
    if not old or not new or len(old) > 1 or len(new) > 1:
        return (None, None)
    a = _message_fingerprints(old[0])
    b = _message_fingerprints(new[0])
    if a is None or b is None:
        return (None, None)
    return (len(a), len(b))


def _length_clause(before, after):
    """One plain sentence about what a swap does to the row's LENGTH, or "" when
    it cannot be counted.

    Says nothing when the counts are unknown rather than guessing, and stays
    silent on a wash - a row that gains or loses nothing does not need a line.
    The shrink case is the one this exists for, so it is the one that gets the
    blunt wording.
    """
    if before is None or after is None:
        return ""
    if after < before:
        return ("this row goes from {0} to {1} prose turns - {2} FEWER"
                .format(before, after, before - after))
    if after > before:
        return ("this row goes from {0} to {1} prose turns - {2} more"
                .format(before, after, after - before))
    return "both conversations have {0} prose turns".format(before)


def _refresh_field_loss(pre, post):
    """(dropped, reset) top-level keys the DESTINATION's row loses when a
    refresh replaces PRE with POST, or (None, None) if either side cannot be
    parsed - "could not tell", never an empty list.

    This is deliberately NOT transform_row's `removed`/`reset`, which the
    overwrite block used to print. Those describe what the transform took out
    of the SOURCE row, and labelling them "fields dropped from the
    destination's row" was wrong in both directions: a destination row
    carrying its own `remoteMcpServersConfig` while the source had none
    produced an empty list and printed nothing - silently losing exactly the
    per-account connector config SYNC_STRIP exists to keep out of transit -
    while a source-only stripped field was announced as a loss the destination
    never had. The pre-image needed to answer the question properly is already
    in hand, so ask it of the two rows actually involved.
    """
    try:
        a = json.loads(pre.decode("utf-8"))
        b = json.loads(post.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, AttributeError):
        return None, None
    if not isinstance(a, dict) or not isinstance(b, dict):
        return None, None
    dropped = sorted(k for k in a if k not in b)
    # Present on both sides, but the refresh puts this tool's default back over
    # something the destination account had set itself.
    reset = sorted(k for k, default in SYNC_RESET.items()
                   if k in a and k in b and b[k] == default and a[k] != default)
    return dropped, reset


def transform_row(data, verbatim=False):
    """Serialize a row for the destination account. Returns (bytes, removed, reset).

    Never mutates the caller's dict - selection holds the originals and a
    dry run must be able to report without changing anything.
    """
    if verbatim:
        return json.dumps(data, separators=(",", ":")).encode("utf-8"), [], []
    out = dict(data)
    # Sorted, not SYNC_STRIP declaration order: `reset` below is sorted() too,
    # and the JSON manifest (--json) surfaces both lists on the same row - a
    # reviewer flagged the mismatched conventions as a stability trap for
    # anything that reads or diffs that output.
    removed = sorted(k for k in SYNC_STRIP if k in out)
    for k in removed:
        out.pop(k)
    reset = []
    for k, v in SYNC_RESET.items():
        if k in out and out[k] != v:
            out[k] = v
            reset.append(k)
    return json.dumps(out, separators=(",", ":")).encode("utf-8"), removed, sorted(reset)


def _live_override_record(env, source):
    """The manifest record of a --live certification (RULING 5).

    Redundant on purpose: `account` + `pair` are the operative fields the
    executor revalidates; `overrode_file`/`overrode_uuid` restate what the
    assertion overrode for the journal reader (they are re-derived, never
    trusted, wherever they could mislead - see _live_override_derived and
    _certified_live_account); `config_path` is best-effort audit only.

    The disagreement is re-read here rather than threaded from
    _resolve_live_assertion; if the files changed in the microseconds
    between the two reads, fail closed rather than record a certification
    about a state that no longer exists."""
    dis = _identity_disagreement(env)
    if dis is None or source.account_uuid not in dis:
        raise Refusal("the identity files changed while this sync was being "
                      "planned; re-run it.")
    oauth_uuid, config_uuid = dis
    if source.account_uuid == config_uuid:
        overrode_file, overrode_uuid = "~/.claude.json", oauth_uuid
    else:
        overrode_file, overrode_uuid = "config.json", config_uuid
    return {"account": source.account_uuid,
            "pair": [oauth_uuid, config_uuid],     # ordered: oauth, config
            "overrode_file": overrode_file, "overrode_uuid": overrode_uuid,
            "config_path": _disagreeing_config_path(env, config_uuid)}


def plan_sync(env, flags):
    """Build the sync manifest. Pure planning - writes nothing."""
    source, dest = resolve_sync_endpoints(env, flags.to or None,
                                          flags.live or None)
    picked, tally = select_sync_rows(env, source, dest, flags)
    rows = []
    for cand in picked:
        blob, removed, reset = transform_row(cand["data"], flags.verbatim)
        pre = cand.get("pre")
        if pre is not None and pre == blob:
            # A refresh that would write the identical bytes is not a refresh.
            # Dropping it here keeps the plan honest about what --update will
            # actually overwrite, and keeps a no-op run from journaling one.
            tally["unchanged"].append(cand["title"])
            continue
        # Which way does this refresh move the row? Only meaningful when both
        # sides can be read; "unknown" is tracked separately and never folded
        # into "fine", for the same reason an unreadable row is never treated
        # as absent anywhere else in this module.
        pre_act = cand.get("pre_activity")
        src_act = cand.get("last_activity")
        regresses = False
        dest_dropped, dest_reset = (None, None)
        if pre is not None:
            dest_dropped, dest_reset = _refresh_field_loss(pre, blob)
            # Unknown on EITHER side is unknown, full stop. Only the destination
            # side used to reach this state; an unreadable source timestamp was
            # silently treated as age zero, which made every such row look like
            # a regression.
            if pre_act is None or src_act is None:
                tally["activity_unknown"].append(cand["title"])
                if flags.newer_only:
                    # Cannot show it is newer, so it is not sent. Tallied under
                    # its own heading rather than folded into "held back as
                    # older" - the user asked for newer, and "we could not tell"
                    # is a different answer from "it was older".
                    tally["held_unknown"].append(cand["title"])
                    continue
            elif pre_act > src_act:
                regresses = True
                tally["regressing"].append(cand["title"])
                if flags.newer_only:
                    tally["held_older"].append(cand["title"])
                    continue
            elif pre_act == src_act and flags.newer_only:
                # Same moment on both sides, so there is nothing newer to send -
                # and "only what is newer" has to mean STRICTLY newer, not
                # "not older". The distinction is not academic: a row counts as
                # needing a refresh when its BYTES differ, and rows can differ
                # while their timestamps match, because `model`,
                # `permissionMode`, `chromePermissionMode` and the MCP fields
                # are per-account settings that drift without any activity.
                # Measured on a real machine the first time this flag was run in
                # anger: 36 rows genuinely older, 11 genuinely newer - and 248
                # timestamp-identical rows that the first version happily
                # overwrote, carrying one account's model and permission choices
                # into another to fix nothing at all.
                tally["held_same"].append(cand["title"])
                continue
        # Does this refresh change WHICH CONVERSATION the row opens? A row's
        # filename is the app's session id and survives a resume, while the
        # cliSessionId inside it names the transcript and changes on each new
        # CLI run - so the two accounts can hold the same entry pointing at
        # different conversations, and overwriting it swaps which one that
        # account can reach. Categorically different from refreshing a title,
        # and it must never be presented as the same thing.
        pre_sid = _pointed_session(pre) if pre is not None else None
        post_sid = _pointed_session(blob)
        swaps = bool(pre_sid and post_sid and pre_sid != post_sid)
        if swaps:
            tally["swapping"].append(cand["title"])
        rows.append({"name": cand["name"],
                     "pre_b64": b64(pre) if pre is not None else None,
                     "is_update": pre is not None,
                     # the pointer, and the conversation this row would stop
                     # opening. displaced_orphan is filled in after the loop,
                     # once it is known whether a swap actually occurs.
                     "swaps_conversation": swaps,
                     "displaced_session": pre_sid if swaps else None,
                     "displaced_orphan": None,
                     # 0.0-1.0: how much of the displaced conversation is also
                     # in the incoming one. None = not measured / not knowable.
                     "displaced_overlap": None,
                     # prose-turn counts either side of the swap. Carried
                     # separately from the overlap because a row can lose length
                     # while keeping a high overlap - the case the percentage
                     # alone hid twice on 2026-08-22.
                     "displaced_turns": None,
                     "incoming_turns": None,
                     # carried per row so the report, --json and the window can
                     # each say which overwrites move a row backwards without
                     # re-deriving it
                     "pre_activity": pre_act if pre is not None else None,
                     "regresses": regresses,
                     # either side unreadable - carried explicitly so the report
                     # and the window do not have to re-derive "unknown" from a
                     # null pre_activity, which no longer covers the source side
                     "activity_unknown": pre is not None
                     and (pre_act is None or src_act is None),
                     # what the DESTINATION's row loses, measured against its
                     # own pre-image - not transform_row's source-side lists
                     "dest_dropped": dest_dropped, "dest_reset": dest_reset,
                     "dest_path": os.path.join(dest.path, cand["name"]),
                     "post_b64": b64(blob), "session_id": cand["session_id"],
                     "title": cand["title"], "removed": removed, "reset": reset,
                     # carried per row (not just in the tally) so --json and
                     # any later reader can tell which rows only exist because
                     # a deliberate deletion was overridden
                     "overrode_tombstone": bool(cand.get("overrode_tombstone")),
                     "written": False})

    # Second pass, and only when it can matter: for every row that swaps which
    # conversation it opens, is the DISPLACED one still reachable from some
    # other account? If not, this write makes it unreachable from every
    # sidebar - the transcript survives on disk with nothing pointing at it.
    # That is the genuinely destructive case hiding inside "refresh a row", and
    # until this pass existed the plan presented it identically to a title
    # update. Measured on a real machine: of 18 rows a --newer-only run would
    # have written, 6 swapped the conversation and 5 of those orphaned it.
    swap_rows = [r for r in rows if r["swaps_conversation"]]
    if swap_rows:
        # Every row this plan will overwrite, not just the swapping ones: a
        # plain refresh also replaces its row wholesale, so it stops vouching
        # for whatever it pointed at too.
        doomed = {r["name"] for r in rows if r.get("is_update")}
        pointers = _other_pointers(env, dest.path, doomed)
        unreadable_store = None in pointers
        # How much of each displaced conversation survives in its replacement.
        # Bounded: past the row cap the number stops being worth the reads, and
        # an unmeasured row simply says so rather than guessing.
        measured = 0
        for r in swap_rows:
            if measured >= TRANSCRIPT_COMPARE_MAX_ROWS:
                break
            try:
                incoming = _pointed_session(unb64(r["post_b64"]))
            except (KeyError, ValueError):
                incoming = None
            r["displaced_overlap"] = _displaced_overlap(
                env, r["displaced_session"], incoming)
            # Lengths are cheap here - both files are already in the page cache
            # from the overlap read - and they are reported even when the
            # overlap could not be measured, because "436 -> 336 turns" is
            # useful on its own and does not depend on the comparison.
            r["displaced_turns"], r["incoming_turns"] = _displaced_sizes(
                env, r["displaced_session"], incoming)
            # Only a row that actually produced a number spends the budget. It
            # used to increment unconditionally, so a run of cheap failures - a
            # missing transcript, an unresolvable id - could exhaust the cap and
            # starve the rows further down that could have been measured. The
            # cap exists to bound expensive reads, and a failure that never
            # opened a file is not one. Residual: a file that IS read and then
            # yields too few prose turns also goes uncounted; that is bounded by
            # the number of swap rows, which is small by construction.
            if r["displaced_overlap"] is not None:
                measured += 1
        kept = []
        for r in rows:
            if not r["swaps_conversation"]:
                kept.append(r)
                continue
            sid = r["displaced_session"]
            if sid in pointers:
                r["displaced_orphan"] = False          # another account holds it
            elif unreadable_store:
                # A store we could not read might be the one holding it. Never
                # treat "could not look" as "nothing there" - the same rule the
                # rest of this module runs on - so this counts as at risk.
                r["displaced_orphan"] = "unknown"
            else:
                r["displaced_orphan"] = True
            if r["displaced_orphan"] and not flags.allow_orphan:
                tally["held_orphan"].append(r["title"])
                # Carried alongside rather than inside the title list, which
                # other readers treat as plain titles. No row bytes here - only
                # what a user needs to judge the hold.
                tally["held_orphan_detail"].append(
                    {"title": r["title"], "overlap": r["displaced_overlap"],
                     # True = nothing else points at it; "unknown" = a store
                     # could not be read, so reachability was never established.
                     # Carried because the report used to state flatly that every
                     # held row leaves its conversation reachable from no
                     # account, which is a certainty the "unknown" rows do not
                     # have and the code one line up is careful not to claim.
                     "orphan": r["displaced_orphan"],
                     # same reason the swap list carries them: a held row is
                     # exactly where the user decides whether to override, so
                     # the length belongs next to the percentage
                     "displaced_turns": r["displaced_turns"],
                     "incoming_turns": r["incoming_turns"],
                     "displaced_session": r["displaced_session"]})
                continue
            kept.append(r)
        rows = kept
    out = {"op_type": "sync",
           "source_account": source.account_uuid, "source_org": source.org_uuid,
           "source_email": source.email, "source_path": source.path,
           # Provenance of the live-account determination ("oauth"/"config"/
           # "user"), so the CLI can say plainly which evidence this plan
           # rests on and warn that --apply will need the app closed. The
           # executor does NOT read this key - it re-derives live_account
           # itself, which is what makes the guard unbypassable by a
           # hand-edited manifest. (The "live_override" key below has a
           # deliberately different contract: the executor DOES read it, but
           # only after revalidating it against the identity files on disk -
           # see _certified_live_account, RULING 5.)
           "source_resolved_from": source.resolved_from,
           "dest_account": dest.account_uuid, "dest_org": dest.org_uuid,
           "dest_email": dest.email, "dest_email_source": dest.email_source,
           "dest_path": dest.path,
           "verbatim": bool(flags.verbatim), "update": bool(flags.update),
           "newer_only": bool(flags.newer_only),
           # Recorded because it changes what this op is PERMITTED to do, and
           # because the apply step needs it: reachability is re-checked before
           # writing, and a run the user consented to orphan on must not be
           # stopped by that check. Inferring it from the rows is not sound - a
           # plan with --allow-orphan that happens to orphan nothing looks
           # identical to one without it.
           "allow_orphan": bool(flags.allow_orphan),
           "rows": rows, "tally": tally}
    if source.resolved_from == "user":
        out["live_override"] = _live_override_record(env, source)
    return out


# Journal-write budget for execute_sync_op's row loop.
#
# save_manifest rewrites and fsyncs the WHOLE manifest, which carries a base64
# post-image of every row in the op - so flagging each row `written` with its
# own save_manifest costs rows x manifest bytes. Measured: 60 stripped rows at
# ~2 KB each produce a 197,693-byte manifest and rewrite 11.9 MB during one
# execute. Extrapolated to this machine's real rows under --verbatim (432
# rows, the largest 1.36 MB) that is a ~385 MB manifest rewritten 432 times:
# over 160 GB of fsynced I/O, i.e. a first `sync --verbatim --apply` into an
# empty second account would look like an indefinite hang.
#
# So spend a fixed byte budget on per-row journaling and stop when it is gone.
# How far the budget stretches is a function of manifest size, not row count:
# it buys BUDGET / manifest_bytes per-row saves. Small runs - every op in the
# test suite, and any modest stripped sync - journal every row individually.
# A stripped sync of this machine's own corpus (432 rows, ~1 KB of base64
# each, ~417 KB of manifest) journals roughly the first 78 rows individually
# and batches the rest; that is the intended shape, not a shortfall. A huge --verbatim manifest is
# written once, at the end (or on the way out through an exception - see the
# loop). Under-reporting what landed is harmless by construction:
# execute_sync_op re-reads every destination row before writing it and
# recognises one that already holds exactly the planned bytes, so a resumed op
# marks it done rather than duplicating or refusing. The tradeoff bought is
# bounded I/O for a coarser - never wrong - record of which rows landed.
SYNC_JOURNAL_BYTE_BUDGET = 32 * 1024 * 1024


def execute_sync_op(env, op):
    """journaled -> writing -> completed.

    Far simpler than execute_op because nothing is deleted and no transcript
    moves: the destructive step that dominates a move does not exist here.
    Rows are journaled as written on a byte budget (SYNC_JOURNAL_BYTE_BUDGET),
    and always on the way out - normally or through an exception - so any
    failure this process can observe leaves an exact record of which rows
    landed. Only a hard kill (power loss, SIGKILL) can lose the tail of that
    record, and a resumed op recovers from it safely either way.
    """
    m = op.manifest
    if m.get("status") != "journaled":
        raise LayoutError("execute_sync_op runs ops from 'journaled'; use recover "
                          "for interrupted ops")

    # Two independent gates. The path comparison below catches a resolvable
    # live account that IS the destination (a switch the identity files did
    # register); _guard_mutation catches everything the files cannot prove -
    # including the E4 case where they are stale or disagree - by refusing
    # any write while the desktop app itself is running (RULING 4).
    # realpath on BOTH sides, not normpath: ensure_contained - the other half
    # of this guarantee, in the row loop below - resolves reparse points, and
    # this comparison has to agree with it. A junction makes the two disagree
    # (dest realpath == live realpath while the normpath strings differ).
    # Everywhere else in this module treats reparse points as hostile; so
    # does this.
    live = live_account(env)
    _refuse_dest_possibly_live(
        env, live, m["dest_path"], "write to",
        lambda: "destination resolves to the LIVE account ({0}); refusing - sync must "
                "never write to the account that is currently live."
                .format(live.email or live.account_uuid),
        _certified_live_account(env, m))
    _guard_mutation(env, "write to")
    if not os.path.isdir(m["dest_path"]):
        raise LayoutError("destination store vanished: " + m["dest_path"])

    # BEFORE set_status: a refusal here has written nothing, so the op should
    # stay at 'journaled' rather than being marked 'writing' - a status that
    # tells recover a partial write may have happened when none did.
    _sync_recheck_reachability(env, m, m["rows"])

    set_status(op, "writing")
    rows = m["rows"]
    # What one save_manifest costs, estimated once rather than measured per
    # row: the manifest is dominated by the rows' base64 images, and flipping a
    # `written` flag does not change its size materially. BOTH images count - a
    # refresh carries a pre-image too, and on rows whose connector config the
    # default transform strips, the pre-image is the larger of the two. Counting
    # only post-images there under-estimates every save and defeats the byte
    # budget that exists to stop a big run looking like a hang.
    # isinstance, not `or ""`: a corrupt manifest carrying a NUMBER in either
    # image makes `123 or ""` evaluate to 123 and len() raise TypeError - and
    # this runs BEFORE the write loop, so it would crash out past the very
    # refusal _sync_pre_image exists to produce. A size estimate has no business
    # being the thing that decides whether the tool fails closed.
    def _b64len(v):
        return len(v) if isinstance(v, str) else 0
    per_save = sum(_b64len(r.get("post_b64")) + _b64len(r.get("pre_b64"))
                   for r in rows) + 4096
    budget = SYNC_JOURNAL_BYTE_BUDGET
    try:
        _sync_write_rows(op, m, rows, per_save, budget)
    except BaseException:
        # Journal what actually landed before the failure propagates. Every
        # in-process failure - Refusal, LayoutError, a bare OSError, even
        # KeyboardInterrupt - therefore still leaves an exact record, which is
        # what recover's 'back' arm needs to remove exactly the rows this op
        # wrote. Best-effort: a save that itself fails must never mask the
        # original failure.
        try:
            save_manifest(op)
        except Exception:
            pass
        raise
    # set_status saves the manifest itself, so it IS the tail-of-batch write -
    # an explicit save_manifest here would serialize and fsync the whole thing
    # a second time, which on the very manifest the budget exists to bound
    # doubles the cost the budget just saved.
    set_status(op, "completed")
    return "completed"


def _sync_recheck_reachability(env, m, rows):
    """Re-verify, at APPLY time, that every swap approved as safe still is.

    `plan_sync` decides `displaced_orphan` from the store as it stood when the
    plan was built. That answer is only as fresh as the plan, and the gap
    between planning and applying is exactly where the app repoints rows - the
    behaviour that started this whole line of work. A conversation that had a
    second door when the plan was made can have lost it by the time Apply is
    pressed, and until this function existed the write went ahead on the stale
    answer, orphaning it without ever asking for --allow-orphan.

    0.9.13 raised the stakes: reachability became per-row, so a single
    surviving row can now be what makes a swap safe, where before it took a
    whole account. A single row is a much easier thing to lose.

    Runs ONCE per invocation, before that invocation writes anything, under the
    lock execute_sync_op already holds:

    - Before, not during: the moment the loop writes its first row the store no
      longer matches the plan, and a mid-loop recount would measure this op's
      own progress rather than anything the user needs to know about.
    - Once, not per row: `_other_pointers` is a full read of every store, and
      the plan spent its budget answering this same question at the same
      granularity. Per row would be a different, more expensive guarantee.
    - A RESUMED op gets the check too, and should: `recover --forward` sets the
      status back to 'journaled' and re-enters execute_sync_op, so an op that
      stalled overnight re-verifies against the store as it is now rather than
      as it was when the plan was built. Rows already `written` are skipped -
      they are done, and this op is what made them so.

    **What this does NOT do.** It narrows the window from "between planning and
    applying" - which is minutes to overnight, and is where the app does its
    repointing - to "between this check and the writes", which is milliseconds.
    It does not close it, and it is not a lock: a change that lands after the
    check is not seen, so this catches edits COMPLETED BEFORE it, not
    concurrent ones. A reviewer put that plainly and was right to: a second
    copy of this tool could pass the same check, remove the last voucher, and
    let this op orphan the conversation anyway. Closing that needs an apply-time
    serialization boundary spanning the guard, the check, every write and the
    journal update - a bigger change than this, and recorded as such in
    internals.md rather than implied to be handled here.

    Also unhandled by design: a destination row that changed from a non-swap
    into a swap since planning is not re-classified here. The write loop's own
    drift check covers it - `current != pre` refuses that row outright - so the
    row never gets written, but it is the drift refusal doing the work, not
    this function.
    """
    # NOT `if m["allow_orphan"]: return`. That was the first version, and both
    # reviewers rejected it independently: at plan time --allow-orphan means
    # "hide THESE conversations, the ones the report just named", and treating
    # it at apply time as a blanket licence lets a NEW orphan through - one the
    # user never saw, created by a change after they read the plan. The plan
    # phase would be doing no work at all in that case.
    #
    # The manifest already records what was consented to, per row, and does not
    # need a separate list: a row survives planning with `displaced_orphan` True
    # or "unknown" ONLY when --allow-orphan was passed (plan_sync drops it
    # otherwise), so that field IS the record of what the user was shown and
    # accepted. Rows planned as False are the ones whose safety rested on a
    # voucher, and they are exactly the ones worth re-checking.
    at_risk = [r for r in rows
               if r.get("swaps_conversation")
               and r.get("displaced_orphan") is False
               and not r.get("written")
               and r.get("displaced_session")]
    if not at_risk:
        return
    pointers = _other_pointers(env, m["dest_path"],
                               {r["name"] for r in rows if r.get("is_update")})
    unreadable = None in pointers
    for r in at_risk:
        if r["displaced_session"] in pointers:
            continue
        # Fail closed on an unreadable sibling, exactly as the planner does:
        # "we could not look" is never "nothing there". Named separately so the
        # user is not sent hunting for a vanished row that may never have gone.
        why = ("a store could not be read, so whether anything still opens it "
               "cannot be confirmed" if unreadable else
               "nothing else points at it any more")
        # "Nothing has been written" is only true of a FRESH op. On a resume,
        # an earlier invocation already landed rows, and telling the user
        # otherwise would send them looking for an untouched destination that
        # does not exist - and would mislead anyone reading this to decide
        # whether a rollback is needed.
        # Name the way out, and name it accurately for the state the op is in.
        # A refusal that says only "re-run" is fine for a fresh op and is not
        # the whole story for a resumed one, where rows have already landed and
        # an open op is sitting in the journal for doctor to flag. Both routes
        # verified to work from exactly this state: `--back` reverses what
        # landed and closes the op, and a re-plan succeeds and correctly HOLDS
        # this row back under the orphan tally rather than writing it.
        done = sum(1 for x in rows if x.get("written"))
        if not done:
            landed = ("Nothing has been written. Re-run to re-plan against the "
                      "store as it is now - the new plan will name this "
                      "conversation and hold the row back, so you can decide "
                      "with the evidence in front of you.")
        else:
            landed = (
                "This op had already written {0} row(s) on an earlier run; "
                "nothing further has been written now, and those rows are "
                "untouched. Two ways on, and the op stays open until you take "
                "one: 'recover --id {1} --back --apply' reverses the {0} row(s) "
                "and closes it, or re-run sync to re-plan - the new plan holds "
                "this row back and names the conversation, and 'recover --id "
                "{1} --back --apply' still closes the stalled op afterwards."
                .format(done, m.get("op_id", "<op>")))
        raise Refusal(
            "reachability changed since this sync was planned: refreshing row "
            "{0!r} (session {1}) would stop it opening conversation {2}, and "
            "{3}. When this was planned another row still opened it, so this "
            "is NOT one of the conversations the plan offered to hide - it "
            "became hideable afterwards. {4}"
            .format(r["name"], r["session_id"],
                    (r["displaced_session"] or "?")[:8], why, landed))


def _sync_write_rows(op, m, rows, per_save, budget):
    """execute_sync_op's write loop, split out only so its caller can wrap it
    in the journal-on-the-way-out handler above."""
    for i, r in enumerate(rows):
        if r.get("written"):
            continue
        # Containment: a hand-edited or simply wrong row dest_path must never
        # let this loop touch a path outside the destination this op was
        # verified against above - the dest_path check just above only means
        # something if every row it is supposed to "cover" is independently
        # confirmed to actually sit inside it. ensure_contained alone admits
        # the root itself (real == rreal); a row dest_path equal to the root
        # would pass that check yet still put atomic_write's <path>.ct-tmp
        # scratch file one level OUTSIDE the root (a sibling, in its parent)
        # before the write even fails - so also require every row to be a
        # direct child of the verified root, not just "under" it.
        real_dest = ensure_contained(r["dest_path"], [m["dest_path"]])
        if os.path.dirname(real_dest) != os.path.realpath(m["dest_path"]):
            raise LayoutError(
                "row dest_path {0!r} is not a direct child of the destination "
                "store {1!r}; refusing".format(r["dest_path"], m["dest_path"]))
        post = unb64(r["post_b64"])
        try:
            with open(r["dest_path"], "rb") as fh:
                current = fh.read()
        except FileNotFoundError:
            current = None            # not there yet - the common case
        except OSError as exc:
            # Anything other than "doesn't exist yet" - permission denied,
            # the row name resolving to a directory, an I/O error - must
            # refuse rather than be treated as "absent" and written over:
            # _row_state elsewhere in this module maps an unreadable current
            # file to "drifted" (the REFUSING branch), never to "safe to
            # write". Getting this wrong here would silently overwrite a
            # destination row this process could not actually verify.
            raise Refusal(
                "could not read destination row {0!r} (session {1}) to check "
                "for changes since planning: {2}. The op is left at 'writing' "
                "- resolve the row, then re-run.".format(
                    r["name"], r["session_id"], exc))
        # is_update, NOT truthiness of pre_b64: a zero-byte destination row
        # encodes to "", which is falsy, and would silently demote a refresh to
        # an add - which then refuses because "a different row is already there".
        # _sync_pre_image keeps that distinction while still failing closed on a
        # manifest that has no pre-image at all; main() catches neither KeyError
        # nor binascii.Error, so turn both into a Refusal here rather than a
        # traceback (_sync_row_drift classifies the same input "unreadable").
        pre = None
        if _row_is_refresh(r):
            try:
                pre = _sync_pre_image(r)
            except (KeyError, ValueError) as exc:
                raise Refusal(
                    "row {0!r} (session {1}) is marked as a refresh but its "
                    "journalled pre-image cannot be read ({2}); refusing rather "
                    "than overwriting a row whose original bytes this op could "
                    "no longer restore. Use 'recover --resolve {3} --back' to "
                    "reverse what did land.".format(
                        r["name"], r["session_id"], exc, m.get("op_id", "")))
        if pre is not None:
            # A REFRESH (RULING 8). The row existed at plan time and we intend to
            # overwrite it, so "present with different bytes" is no longer proof
            # of drift - the question is whether it still holds the SAME bytes we
            # planned against.
            if current is None:
                # It was there when planned and is gone now: the destination
                # account deleted this session. Writing would resurrect it, which
                # is precisely what the tombstone skip exists to prevent.
                raise Refusal(
                    "destination row {0!r} (session {1}) was deleted since this "
                    "refresh was planned; re-creating it would resurrect a session "
                    "that account removed. The op is left at 'writing' - re-run to "
                    "re-plan without it.".format(r["name"], r["session_id"]))
            if current == post:
                r["written"] = True      # already refreshed; nothing to do
                if budget >= per_save:
                    budget -= per_save
                    save_manifest(op)
                continue
            if current != pre:
                raise Refusal(
                    "destination row {0!r} (session {1}) changed since this refresh "
                    "was planned - it no longer matches the copy this op measured, "
                    "so overwriting it would discard whatever changed it. The op is "
                    "left at 'writing' - re-run to re-plan against its current "
                    "state.".format(r["name"], r["session_id"]))
            try:
                atomic_write(r["dest_path"], post)
            except OSError as exc:
                raise Refusal(
                    "could not refresh destination row {0!r} (session {1}): {2}"
                    .format(r["name"], r["session_id"], exc))
            _maybe_crash("sync-write-before-save")
        elif current is None:
            try:
                atomic_write(r["dest_path"], post)
            except OSError as exc:
                # The op stays at "writing" either way (non-terminal) -
                # recover has an accurate record of exactly which rows
                # landed, same as any other crash mid-loop.
                raise Refusal(
                    "could not write destination row {0!r} (session {1}): {2}"
                    .format(r["name"], r["session_id"], exc))
            _maybe_crash("sync-write-before-save")
        elif current != post:
            # select_sync_rows only picked rows that were ABSENT at the
            # destination at plan time. A row now present with DIFFERENT
            # bytes means the destination account changed it since planning
            # (e.g. the user signed in and touched that session) - rewriting
            # over that would silently discard the change. execute_op
            # refuses on the equivalent drift rather than blindly
            # overwriting; do the same here, and leave the op non-terminal
            # (still "writing") so it stays recoverable.
            raise Refusal(
                "destination row {0!r} (session {1}) changed since this sync was "
                "planned; re-running would discard that change. The op is left "
                "at 'writing' - resolve the row, then re-run.".format(
                    r["name"], r["session_id"]))
        # else: already byte-identical to the planned post-image - nothing
        # left to write, just record this row as done.
        r["written"] = True
        if budget >= per_save:
            budget -= per_save
            save_manifest(op)
        if i < len(rows) - 1:
            _maybe_crash("sync-mid-write")


def run_sync(env, manifest):
    """Lock, journal, execute, rotate - the same shape as run_move, minus the
    moved-log append (sync moves nothing, so there is nothing to log there).
    One difference from run_move worth flagging: run_move's execute_op calls
    _validate_manifest_paths once, up front, for every path in the manifest.
    Sync has no such single up-front pass - each row's dest_path is instead
    validated inline, immediately before that row is touched, inside
    execute_sync_op's write loop (there is no sidecar inventory or transcript
    path here for a single shared validator to be worth factoring out).

    Plan-review fix (RULING 4 follow-up): _guard_mutation is checked here
    too, before acquire_lock/new_op - the earliest clean point, so a refused
    run creates no lock file and no op directory. execute_sync_op's own copy
    of this guard fires too late to prevent that: it runs AFTER new_op has
    already journaled the op, so every refusal there left a stray
    'journaled' op behind - doctor flags it, recover has to clear it - and
    the common case triggering this (desktop app left open) is exactly the
    one RULING 4 made this guard fire on. execute_sync_op's guard still
    stays, unchanged: it is the ONLY guard recover --forward gets, since
    resuming a crash-interrupted op re-enters execute_sync_op directly and
    never calls back through here.
    """
    _guard_mutation(env, "write to")
    # Learn the live account's email HERE, not while planning. plan_sync
    # documents "writes nothing", and both the dry run and the GUI's plan
    # promise the same - creating a memo file during a preview would break a
    # guarantee this tool makes loudly, to save a label. An apply is already a
    # write, and applying is how accounts get used, so the memo still fills in.
    remember_account_email(env, manifest.get("source_account"),
                           manifest.get("source_email"))
    acquire_lock(env, "pending")
    try:
        # "tally" is the report's data, not the operation's: it names every
        # session the run skipped - including the ones the destination account
        # deliberately DELETED - and nothing in execute/undo/recover reads it.
        # Journaling it would write those titles to disk in ~/.claude-code-journal
        # for the lifetime of the op, so strip it from the copy that is
        # journaled. The "rows" list (and every row dict in it) is still the
        # same object, so run_sync's caller keeps seeing `written` flags flip.
        op = new_op(env, dict((k, v) for k, v in manifest.items() if k != "tally"))
        # Hand the op_id back to the caller: new_op shallow-copies the
        # manifest and sets op_id on ITS copy, so without this a `sync --apply
        # --json` run reports a result but no id for `undo --id` to use.
        manifest["op_id"] = op.manifest["op_id"]
        # already holding the lock (no O_EXCL needed) - just record the real op_id
        with open(_lock_path(env), "w") as fh:
            fh.write("{0} {1}".format(os.getpid(), op.manifest["op_id"]))
        final = execute_sync_op(env, op)
        if final == "completed":
            rotate_ops(env)
        return final
    finally:
        release_lock(env)


def _repoint_shape_error(m):
    """A sentence naming what is wrong with this repoint manifest, or None.

    Repoint sits in recover_op's allowlist that skips _validate_manifest_paths,
    and until now brought nothing in its place - so a damaged record raised
    KeyError, IndexError or TypeError straight out of recover, which main() does
    not catch. Worse, cmd_recover's bare listing classifies EVERY pending op, so
    one damaged repoint took the whole diagnostic down with it.

    NEVER RAISES. classify_op calls it, and classify_op must never raise.

    dest_path is required to be a str specifically: read_json(17) reaches
    open(17), which treats an int as a FILE DESCRIPTOR - it would open and close
    an unrelated fd rather than fail.
    """
    rows = m.get("rows")
    if not isinstance(rows, list) or len(rows) != 1:
        return "its 'rows' is not a one-element list"
    r = rows[0]
    if not isinstance(r, dict):
        return "its row is not an object"
    for k in ("name", "dest_path", "pre_b64", "post_b64"):
        if not isinstance(r.get(k), str) or not r[k]:
            return "its row has no usable {0!r}".format(k)
    if not isinstance(m.get("store_path"), str) or not m["store_path"]:
        return "it has no usable 'store_path'"
    return None


def _repoint_claimed_later(env, m, r):
    """The op_id of a LATER operation that wrote this same row, or None.

    plan_repoint builds its post-image as a deterministic function of the row,
    so a second repoint of the same row at the same target produces
    byte-identical bytes. That makes "the row matches our post-image" mean
    either "we wrote it" or "somebody else wrote exactly the same thing" - and
    restoring our pre-image in the second case silently reverses a COMPLETED
    operation, whose own undo then refuses because the row no longer holds what
    it wrote. The work becomes unreachable through the tool.

    This is not hypothetical: it is what the tool's own pre-fix advice produced.
    A stalled repoint told the user to run repoint again; the re-run completed;
    clearing the stale record with --back would then undo the re-run. One real
    journal held exactly that - two stuck ops and one completed op on one row.

    _sync_paths_claimed_elsewhere does this for sync and filters op_type
    != "sync"; this is the repoint-shaped equivalent.

    ANY other terminal op that wrote this row disqualifies the restore - not
    only a demonstrably later one. Op ids are timestamp-prefixed at SECOND
    resolution, so two created in the same second order by their random suffix,
    and a 20-run stress of the test caught that being flaky 8 times before a
    user could. Ordering is therefore not reliable evidence here, and the two
    failure modes are not symmetric: declining wrongly closes the record without
    reverting, which is safe, while restoring wrongly destroys a completed
    operation's work. So the question is not "who went last" but "can we prove
    these bytes are ours" - and if another op wrote the same row, we cannot.

    Never raises - every caller is on a path that must terminate.
    """
    try:
        mine = m.get("op_id") or ""
        dest = os.path.normcase(os.path.abspath(r["dest_path"]))
        for o in list_ops(env):
            om = o.manifest
            oid = om.get("op_id") or ""
            if oid == mine or om.get("status") not in TERMINAL:
                continue
            for orow in om.get("rows") or []:
                p = orow.get("dest_path")
                if not isinstance(p, str) or not orow.get("written"):
                    continue
                if os.path.normcase(os.path.abspath(p)) == dest:
                    return oid
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    return None


def _repoint_landed(m, r):
    """True if the row now opens what this repoint aimed it at, False if it
    still opens what it opened before, None if neither or unreadable.

    A repoint changes ONE field. The app rewrites the others - lastActivityAt,
    lastFocusedAt - every time it opens the session, so a row this operation
    never touched reads as "drifted" against its own pre-image within a day.
    Byte comparison answers "is this exactly what we wrote"; only the pointer
    answers "did what we intended happen", which is what recovery needs.

    Never raises: classify_op calls it, and classify_op must never raise.
    """
    try:
        d = read_json(r["dest_path"])
    except (LayoutError, OSError, ValueError, KeyError):
        return None
    if not isinstance(d, dict):
        return None
    cur = d.get("cliSessionId")
    if cur and cur == m.get("to_session"):
        return True
    if cur and cur == m.get("from_session"):
        return False
    return None


def _sync_row_drift(r):
    """Compare a single sync row's post-image to whatever currently sits at
    its dest_path. Returns 'absent' (nothing there), 'match' (present and
    byte-identical to what this op wrote/would write), 'drifted' (present
    with different bytes - someone else touched this path since the sync
    was planned), or 'unreadable' (present but could not be read - a
    permission or I/O error; fail-closed like every "couldn't look" in this
    module, never treated as "nothing there"). Never raises: classify_op
    must never raise (that was this task's original defect - a KeyError on
    a sync manifest), and undo_sync / recover_op's 'back' arm both need this
    same read-only per-row classification before deciding what to do.

    "Never raises" has to include the manifest side, not just the disk side:
    unb64(r["post_b64"]) raises binascii.Error (a ValueError) on a corrupt
    manifest and KeyError if the field is missing, and main() catches
    neither. A row whose planned bytes cannot be reconstructed is
    "unreadable" - fail-closed, and correctly so: it can never be written
    forward, and it must never be deleted either.
    """
    try:
        raw_post = r["post_b64"]
        if not isinstance(raw_post, str):
            # Same trap _sync_pre_image closes, on the other image: unb64 calls
            # .encode, so a number here raises AttributeError, which this except
            # does not catch and main() does not either - a traceback out of the
            # one function whose contract is "never raises".
            raise ValueError("post_b64 is not a base64 string")
        post = unb64(raw_post)
    except (KeyError, ValueError):
        return "unreadable"
    pre = None
    if _row_is_refresh(r):
        try:
            pre = _sync_pre_image(r)
        except (KeyError, ValueError):
            return "unreadable"
    try:
        with open(r["dest_path"], "rb") as fh:
            cur = fh.read()
    except FileNotFoundError:
        return "absent"
    except OSError:
        return "unreadable"
    if cur == post:
        return "match"
    if pre is not None and cur == pre:
        # A REFRESH this op has not performed yet: the row still holds exactly
        # the bytes the plan measured. Without this state an interrupted update
        # classified every untouched row as "drifted", which withdrew `forward`
        # and left back-then-replan as the only exit from an op that was in fact
        # perfectly resumable.
        return "pristine"
    return "drifted"


def _sync_drift_titles(rows):
    """(changed, unreadable, deleted) titles for ROWS, via _sync_row_drift.
    Read-only and exception-safe.

    `deleted` is REFRESH rows whose destination row is now gone, and it is a
    third category rather than part of `changed` for two reasons: it blocks
    for a different cause (nothing edited the row - that account removed it),
    and it is only blocking for a refresh. For an ADD row "absent" is the
    ordinary resumption state, which forward completes by simply writing it.

    That asymmetry is the bug this split exists to close. Collecting only
    "drifted" and "unreadable" left an absent PENDING REFRESH looking like no
    drift at all, so classify_sync_op offered ["forward", "back"] and told the
    user "forward finishes them" - while _sync_write_rows refuses that exact
    row on every re-entry, by design, because re-creating it would resurrect a
    session the destination account deleted. Forward could never complete, and
    the recovery listing pointed straight at it: the same dead end 'back'
    exists to close, reopened for a state RULING 8 introduced.
    """
    changed, unreadable, deleted = [], [], []
    for r in rows:
        state = _sync_row_drift(r)
        if state == "drifted":
            changed.append(r["title"])
        elif state == "unreadable":
            unreadable.append(r["title"])
        elif state == "absent" and _row_is_refresh(r):
            deleted.append(r["title"])
    return changed, unreadable, deleted


def _drift_clause(changed, unreadable, deleted=()):
    """A note/refusal fragment naming drifted vs unreadable vs deleted rows
    separately. 'Changed' is only true of one of them - conflating them would
    falsely claim an unreadable row 'changed' when the real reason is a
    permission or I/O error, or that a row someone deleted was edited."""
    parts = []
    if changed:
        parts.append("changed since this sync was planned ({0})".format(", ".join(changed)))
    if unreadable:
        parts.append("could not be read ({0})".format(", ".join(unreadable)))
    if deleted:
        parts.append("were deleted by that account since this refresh was "
                     "planned ({0})".format(", ".join(deleted)))
    return " and ".join(parts)


def classify_sync_op(env, op):
    """Sync's recovery shape. Forward finishes the remaining writes, and
    reversing what was written is `undo`'s job - removing an added row, and
    (since RULING 8) restoring a refreshed one to the bytes it replaced. The
    one exception: if a destination
    row changes underneath a still-in-flight sync, forward can never
    complete (execute_sync_op refuses on that exact row every time it
    re-enters), so offering it forever would be a dead end - 'back' becomes
    the only way off a stuck op. A written row that cannot itself be
    verified is surfaced here too, before the user even picks a direction -
    a single event ("the dormant account got opened") can plausibly both
    block a pending row and rewrite an already-written one, so 'options:
    back' alone would otherwise promise more than back can deliver.

    Correction from the whole-branch review: 'back' is offered ALWAYS, not
    only on drift. Destination-row drift is just one of the ways
    execute_sync_op can leave an op non-terminal - atomic_write raising
    OSError, the row-containment LayoutError and the vanished-store
    LayoutError all leave the pending row simply ABSENT, which reads as no
    drift, which used to classify as "forward only". Forward then raised the
    same error on every re-entry, 'back' was refused as unsafe, and undo
    refused the op for not being 'completed': every exit refused and the op
    was stuck forever - the exact dead end 'back' exists to close, left open
    for the I/O and layout cases. 'back' is unconditionally safe here (it
    only deletes rows this op recorded as written AND that are still
    byte-identical to what it wrote, skipping everything else), so there is
    no state in which withholding it is right. classify_op's move branches
    already offer two resolutions where both are safe; follow that shape:
    ["forward", "back"] normally, ["back"] alone when a drifted or
    unreadable pending row makes forward impossible.

    RULING 8 added a third way for forward to be impossible, and it needed
    its own state: a pending REFRESH whose destination row has since been
    DELETED. _sync_write_rows refuses that row on every re-entry rather than
    resurrecting a session the destination account removed, so forward can
    never complete - but "absent" reads as no drift, so it used to classify
    as ["forward", "back"] with a note promising "forward finishes them".
    _sync_drift_titles now reports it as a third category (deleted), blocking
    for a refresh and still perfectly resumable for an add.
    """
    m = op.manifest
    written = [r for r in m["rows"] if r.get("written")]
    pending = [r for r in m["rows"] if not r.get("written")]
    if m["status"] not in NONTERMINAL:
        return {"status": m["status"], "source": "n/a", "dest": "n/a",
                "resolutions": [], "drifted_rows": [],
                "note": "sync: {0} row(s) written, {1} pending; forward finishes "
                        "them (use undo to reverse what was written - added rows "
                        "are removed, refreshed ones restored to their original bytes)"
                        .format(len(written), len(pending))}
    # RULING 5: warn BEFORE the user picks a direction if this op's --live
    # certification no longer validates - both directions go through gates
    # that will refuse on it, and surprising them at execution when the
    # listing could have said so is exactly what this note exists to avoid.
    # Never raises (_certified_live_account classifies, it doesn't throw),
    # which classify_op requires.
    cert_note = ""
    if "live_override" in m and _certified_live_account(env, m)[0] == _CERT_VOID:
        cert_note = ("; NOTE: this op's --live certification no longer matches "
                     "the identity files - forward/back will refuse until the "
                     "identity state is restored or resolves (RULING 5)")
    pend_changed, pend_unreadable, pend_deleted = _sync_drift_titles(pending)
    if pend_changed or pend_unreadable or pend_deleted:
        blocking = pend_changed + pend_unreadable + pend_deleted
        skip_changed, skip_unreadable, skip_deleted = _sync_drift_titles(written)
        skipped = skip_changed + skip_unreadable + skip_deleted
        note = ("sync: destination row(s) {0}; it can no longer be rolled "
                "forward - 'back' reverses the row(s) this op safely can"
                .format(_drift_clause(pend_changed, pend_unreadable, pend_deleted)))
        if skipped:
            note += ("; it will also skip {0} already-written row(s) it cannot "
                     "verify ({1})".format(len(skipped),
                                           _drift_clause(skip_changed, skip_unreadable,
                                                         skip_deleted)))
        # dict.fromkeys, not a bare concatenation: a pending row and a
        # written row can share a title, and this list reaches cmd_recover's
        # printed line - listing the same title twice reads as two problems.
        # Order-preserving, unlike set().
        return {"status": m["status"], "source": "n/a", "dest": "n/a",
                "resolutions": ["back"], "note": note + cert_note,
                "drifted_rows": list(dict.fromkeys(blocking + skipped))}
    return {"status": m["status"], "source": "n/a", "dest": "n/a",
            "resolutions": ["forward", "back"], "drifted_rows": [],
            "note": "sync: {0} row(s) written, {1} pending; forward finishes them, "
                    "back reverses the {0} already written - removing added rows "
                    "and restoring refreshed ones (use undo instead once "
                    "the op has completed)".format(len(written), len(pending))
                    + cert_note}


def _sync_paths_claimed_elsewhere(env, m):
    """Destination paths some OTHER op's journal records having written.

    The disk-evidence rule below - "the file holds this op's post-image, so
    this op wrote it" - is sound only while nobody else can mint those bytes.
    Somebody can. transform_row is a deterministic function of the source row,
    so a LATER sync planned over the same unchanged source produces a
    byte-identical post-image. The sequence is ordinary rather than exotic, and
    it is the one this tool's own recovery advice walks a user into:

      1. `sync --update --apply` stalls mid-loop (an I/O error, a containment
         refusal), leaving later rows unwritten and the op at "writing".
      2. The user re-runs. The new op plans the same rows - same pre-image,
         same post-image - and completes.
      3. The user clears the stuck op with `recover --back`, exactly as
         classify_sync_op tells them to.

    At step 3 those rows read `not written` and `match`, so without this check
    the stalled op reverses the completed one's work: unlinking a row the other
    op added, or restoring a stale pre-image over a refresh it completed. No
    refusal fires, and the completed op's journal still says "completed".

    Cheap enough to do unconditionally: the journal keeps ten terminal ops plus
    whatever is live, so this is one pass over a bounded set, built once per
    _sync_delete_targets call rather than per ambiguous row.
    """
    mine_id = m.get("op_id")
    mine_key = _op_sort_key(m)
    out = set()
    for o in list_ops(env):
        om = o.manifest
        if om.get("op_id") == mine_id or om.get("op_type") != "sync":
            continue
        # ORDERING, not mere existence. Without it the check is symmetric: two
        # ops that both journal the same path each see the other as a claimant,
        # so BOTH refuse undo and the row can never be reversed through the
        # journal at all - a mutual lockout whose only escape is deleting the
        # row in the app, which for a refresh destroys the session instead of
        # restoring what was replaced. It also refused the one reversal that
        # was still correct: when B refreshes V0->V1, that account rewrites the
        # row to V2, and C re-plans (pre=V2, post=V1) and completes, C holds the
        # only copy of V2 - refusing C's undo strands the destination's own
        # newer state with no route back.
        #
        # So the LATEST op to claim a path owns it: a later claimant supersedes
        # this op (refuse), an earlier one was superseded BY it (proceed).
        # Exactly one of any two ops is later, so a lockout is impossible.
        # _op_sort_key is the module's existing op ordering - creation time then
        # op_id, the same key list_ops sorts by - reused rather than inventing a
        # second notion of "newer". Residual: it orders by creation, not by the
        # moment each row was written, so an op created first but rolled FORWARD
        # after a later op completed is treated as the earlier claimant. It
        # still yields one owner and one refusal, never two of either.
        if _op_sort_key(om) < mine_key:
            continue
        # A reversed op has WITHDRAWN its claim: "undone"/"rolled_back" mean
        # its rows were deleted or put back, so it no longer owns anything.
        # Without this filter the flags it left behind would block a later
        # op's legitimate reversal forever - C completes, C is undone, D
        # re-adds the same row, and undo of D refuses because C's dead
        # manifest still says written. Fails the wrong way: a stale claim
        # that cannot be cleared is as bad as no claim at all.
        if om.get("status") in ("undone", "rolled_back"):
            continue
        for r in om.get("rows") or []:
            p = r.get("dest_path")
            if r.get("written") and isinstance(p, str):
                out.add(os.path.normcase(os.path.abspath(p)))
    return out


def _sync_delete_targets(env, m):
    """Rows THIS sync op actually wrote, classified for safe deletion.
    Shared by undo_sync and recover_op's 'back' path, which react
    differently to a non-empty drifted/unreadable result: undo_sync refuses
    the whole operation (a surprise during a user-initiated reversal of a
    completed sync means stop and ask), while back SKIPS those rows and
    proceeds with the rest, because back is the only exit from a stuck op
    and must always reach a terminal status - refusing there would recreate
    the exact dead end it exists to close.

    Two hard gates, checked up front - never "skip and continue", because
    they mean the op itself cannot be trusted, not just one row:
    1. The same live-account re-check execute_sync_op makes on every write,
       plus the same universal running-app guard (_guard_mutation, RULING
       4) - a delete is exactly as dangerous as a write here, and must
       carry the identical guarantee.
    2. The same containment execute_sync_op's write loop uses
       (ensure_contained plus the direct-child check), so a hand-edited or
       corrupted manifest row can never point this delete outside the
       destination store.

    Per row, via _sync_row_drift: a row is considered when the manifest says
    this op wrote it, OR when the file itself still holds this op's exact
    post-image (see the note in the loop - `written` is journalled after the
    write, so a hard kill in between leaves the flag unset on a row that did
    land). 'absent' is skipped as already-undone. 'match' is removable for an
    add and restorable for a refresh. 'pristine' is an untouched refresh, so
    there is nothing to put back. 'drifted' and 'unreadable' are reported
    separately - neither is ever deleted or restored over.

    Returns (drifted_titles, unreadable_titles, removable_paths,
    restorable_pairs, claimed_titles). Writes and deletes nothing itself.
    `restorable_pairs` is [(dest_path, original_bytes)] for rows this op
    REFRESHED (RULING 8), whose reversal is restoring the pre-image rather
    than deleting the file. `claimed_titles` is rows whose bytes match but
    which another op's journal records writing - ambiguous ownership, so
    neither reversed nor silently dropped (undo refuses, back skips).
    """
    # realpath on both sides, matching execute_sync_op's write-side check and
    # ensure_contained below - see the note there on junctions.
    live = live_account(env)
    _refuse_dest_possibly_live(
        env, live, m["dest_path"], "delete from",
        lambda: "destination resolves to the LIVE account ({0}); refusing - undo, "
                "like sync, may only ever touch a dormant store, never the account "
                "that is currently live. The synced rows can also be removed in "
                "the app itself, or sign the desktop out of this account and "
                "re-run.".format(live.email or live.account_uuid),
        _certified_live_account(env, m))
    # The same guard as the write side (RULING 4: every mutation route,
    # regardless of provenance). Both callers of this helper (undo_sync,
    # recover_op's sync 'back' arm) inherit it from this one place.
    _guard_mutation(env, "delete from")
    drifted, unreadable, removable, restorable, claimed = [], [], [], [], []
    elsewhere = _sync_paths_claimed_elsewhere(env, m)
    for r in m["rows"]:
        written = bool(r.get("written"))
        # Containment BEFORE the row is classified, because classifying opens
        # its dest_path: a hand-edited manifest must not be able to get this
        # helper to read a file outside the destination store.
        #
        # An uncontained row is fatal only if this op RECORDED writing it -
        # then the manifest is untrustworthy about a mutation that really
        # happened, and refusing is the only safe answer. An uncontained row
        # this op never wrote is simply not ours, and raising on it would
        # reopen the dead end 'back' exists to close: a row-containment
        # LayoutError is one of the ways a sync stalls in the first place, so
        # back must still be able to reverse the rows that did land beside it.
        try:
            real_dest = ensure_contained(r["dest_path"], [m["dest_path"]])
            direct_child = os.path.dirname(real_dest) == os.path.realpath(m["dest_path"])
        except LayoutError:
            if written:
                raise
            continue
        if not direct_child:
            if written:
                raise LayoutError(
                    "row dest_path {0!r} is not a direct child of the destination "
                    "store {1!r}; refusing".format(r["dest_path"], m["dest_path"]))
            continue
        state = _sync_row_drift(r)
        # `written` is an intention journalled AFTER the write, so it is not the
        # only evidence this op wrote a row - and on its own it is not safe
        # evidence either. execute_sync_op sets r["written"] and saves the
        # manifest only after atomic_write has already returned; a hard kill in
        # that window (power loss, SIGKILL - the one case its docstring admits
        # the journal can lose the tail of the record) leaves the row holding
        # the post-image while the manifest still says it was never written.
        #
        # For an ADD that stranded a stray row `back` claimed to have removed.
        # For a REFRESH it is worse and is why this changed: the destination row
        # keeps bytes this op wrote, the measured pre-image sits unused in the
        # journal, and `back` reports success having restored nothing - an
        # overwrite the user explicitly asked to reverse, silently kept.
        #
        # So consult the disk, which holds better evidence than the flag: state
        # "match" means the file currently holds exactly this op's post-image,
        # and for a refresh plan_sync guarantees post != pre (identical rows are
        # dropped as "unchanged"), so those bytes cannot be the original either.
        # Anything else on an unattempted row is genuinely not ours to reverse.
        if not written and state != "match":
            continue
        # Ownership, checked for EVERY row whose bytes match - not just the
        # unwritten ones. The first version of this check sat inside the
        # `not written` branch, which made it dead code in the arm that needed
        # it most: undo_sync only ever runs on a "completed" op, where every
        # row is written, so `claimed` was always empty there and undo's
        # conflict refusal could never fire. Both harmful sequences run through
        # a WRITTEN row:
        #   - B stalls on row R; C completes R; classify offers B `forward`
        #     ("forward finishes them"), which sees current == post and marks R
        #     written. Two completed ops now journal the same path, and
        #     undo(B) unlinks the row C added.
        #   - B refreshes V0 -> V1; that account uses the session and the app
        #     rewrites R to V2; C re-plans (pre=V2, post=V1) and completes;
        #     undo(B) restores V0 over C's refresh, and C's own undo is then
        #     permanently refused because R is neither its post nor its pre.
        # Same rule either way: matching bytes are not proof of current
        # ownership when a LATER op records writing the same path. Note this is
        # only about who may REVERSE the row - _sync_write_rows still adopts a
        # row that already holds its post-image rather than refusing, because a
        # refusal there would make `forward` permanently impossible on a row
        # classify_sync_op still offers to roll forward, which is exactly the
        # dead end the previous two rounds closed. Ordering settles ownership
        # without reopening it.
        if state == "match" and os.path.normcase(os.path.abspath(r["dest_path"])) in elsewhere:
            claimed.append(r["title"])
            continue
        if state == "pristine":
            continue                       # untouched refresh - already original
        if state == "absent":
            # Nothing to undo. For a REFRESH this also means the destination
            # deleted the session after we rewrote it - putting the old bytes
            # back would resurrect it, so absent is skipped there too.
            continue
        elif state == "match":
            # "match" means the row still holds exactly what this op wrote. For
            # an added row the reversal is deletion; for a refreshed one it is
            # putting the measured pre-image back. Same evidence, different act.
            if _row_is_refresh(r):
                try:
                    restorable.append((r["dest_path"], _sync_pre_image(r)))
                except (KeyError, ValueError):
                    unreadable.append(r["title"])   # cannot rebuild the original
            else:
                removable.append(r["dest_path"])
        elif state == "drifted":
            drifted.append(r["title"])
        else:                              # "unreadable"
            unreadable.append(r["title"])
    return drifted, unreadable, removable, restorable, claimed


def _sync_restore_all(pairs):
    """Put every measured pre-image back, attempting all of them even if some
    fail, and report every failure together - the same shape as
    _sync_unlink_all, and for the same reason: stopping at the first failure
    would leave a reversal half-applied with no record of the rest.

    atomic_write, not a plain write: a reversal interrupted mid-file would
    leave a row that is neither the refreshed version nor the original, which
    is worse than either.
    """
    failures = []
    for path, blob in pairs:
        try:
            atomic_write(path, blob)
        except OSError as exc:
            failures.append((path, exc))
    if failures:
        raise Refusal("could not restore {0}".format(
            ", ".join("{0} ({1})".format(p, exc) for p, exc in failures)))


def _sync_unlink_all(paths):
    """Delete every path, attempting all of them even if some fail, and
    report every failure together rather than stopping at the first -
    mirrors _delete_inventoried_files. A bare OSError here (permission
    denied, a locked file) must never propagate raw: main() only catches
    Refusal/LayoutError."""
    failures = []
    for p in paths:
        try:
            os.unlink(p)
        except OSError as exc:
            failures.append((p, exc))
    if failures:
        raise Refusal("could not remove {0}".format(
            ", ".join("{0} ({1})".format(p, exc) for p, exc in failures)))


def _sync_reverse_all(removable, restorable):
    """Both halves of a reversal, in one pass that always attempts both.

    Each half already refuses to stop at its own first failure, for the stated
    reason that a half-applied reversal with no record of the rest is the worst
    outcome. Calling them in sequence quietly gave up exactly that guarantee
    ACROSS the two: _sync_unlink_all raising on one locked added row meant
    _sync_restore_all never ran at all, so every refreshed row stayed in its
    overwritten state - held hostage by an unrelated file, and reported as a
    failure to *remove* something, which does not hint that restores were
    skipped. Deletions still go first (the simpler half, and the one whose
    failure is most often transient), but a failure in either is collected and
    both are attempted before anything is raised.
    """
    failures = []
    for p in removable:
        try:
            os.unlink(p)
        except OSError as exc:
            failures.append(("remove {0}".format(p), exc))
    for path, blob in restorable:
        try:
            atomic_write(path, blob)
        except OSError as exc:
            failures.append(("restore {0}".format(path), exc))
    if failures:
        raise Refusal("could not {0}".format(
            ", ".join("{0} ({1})".format(what, exc) for what, exc in failures)))


def undo_sync(env, op):
    """Reverse exactly what this sync did - and only while every row is still
    byte-identical to what it wrote. Rows it ADDED are deleted; rows it
    REFRESHED (RULING 8) are restored to the pre-image the plan measured. If the destination account has since
    opened the session the app rewrites the row, and deleting it would discard
    that account's own state. A row that cannot even be read is treated the
    same way - a surprise either way, so undo refuses rather than guessing.
    This is the deliberate asymmetry with recover_op's 'back' arm: undo is a
    user-initiated reversal of a *completed* sync, where a surprise means
    stop and ask; back is the only exit from a stuck op and must always
    terminate, so it skips instead (see _sync_delete_targets).

    Takes the single-instance lock first, before any of its own checks - the
    same discipline run_undo/run_move/run_sync/recover_op all use, so two
    concurrent 'undo --apply' runs can never race their unlinks.
    """
    m = op.manifest
    acquire_lock(env, "undo-" + m["op_id"])
    try:
        if m.get("op_type") != "sync":
            raise Refusal("not a sync op: " + str(m.get("op_id")))
        if m.get("status") != "completed":
            raise Refusal("op {0} is '{1}', not 'completed'".format(
                m.get("op_id"), m.get("status")))
        (drifted, unreadable, removable,
         restorable, claimed) = _sync_delete_targets(env, m)
        if drifted or unreadable:
            raise Refusal("these synced rows {0}; the other account may have opened "
                          "them. Refusing to change any of them."
                          .format(_drift_clause(drifted, unreadable)))
        if claimed:
            # A completed op should have every row journalled as written, so
            # reaching here means the manifest and the journal disagree about
            # who owns a row. undo is the all-or-nothing arm: stop and ask.
            raise Refusal(
                "a later operation has since written these rows ({0}), so this op "
                "no longer owns them - reversing would undo that operation's work "
                "instead. Undo that operation instead ('list --ops' names it); it "
                "holds the bytes these rows had when IT ran, which is the state "
                "immediately before its own write. Do NOT delete a refreshed row "
                "in the app to reverse it: that removes the session from that "
                "account altogether rather than putting back what was replaced."
                .format(", ".join(claimed)))
        # One pass, both halves always attempted - a locked added row must not
        # leave every refreshed row stranded in its overwritten state.
        _sync_reverse_all(removable, restorable)
        set_status(op, "undone")
        rotate_ops(env)
        return "undone"
    finally:
        release_lock(env)


def _overlap_clause(frac):
    """Plain words for how much of a displaced conversation is also in its
    replacement. A user acts on this line without opening either conversation,
    so it has to say what the number MEANS - and, at the top of the range, what
    it does not mean.

    **The full-overlap line never claims nothing is lost.** It used to. Every
    engine on the review panel raised the same objection independently, and they
    were right: this compares PROSE ONLY, truncated to the first 400 characters
    of each turn, as an unordered set. Images, attachments, thinking blocks and
    tool output are never looked at, a long turn that diverges after 400
    characters fingerprints as identical, and set membership cannot tell a clean
    continuation from a conversation interleaved into an unrelated one. So the
    honest ceiling is "every prose turn appears somewhere in the incoming
    conversation", which is worth knowing and is not a preservation guarantee.
    The sentence sits on the line that then tells the user how to override the
    hold, which is exactly where an overstatement does its damage.

    **Percentages floor, never round.** `int(round(...))` turned 99.9% into
    "100%" and printed the full-overlap line for a conversation with an
    unmatched turn in it - possibly the decisive one. Only frac == 1.0 is full.
    """
    if frac is None:
        return "how much of it is in the incoming one: NOT MEASURED"
    if frac >= 1.0:
        return ("every prose turn of it already appears in the incoming "
                "conversation (text only, first 400 characters compared - images, "
                "attachments and tool output were NOT)")
    pct = int(frac * 100)               # floor: never round up to a false 100
    if frac > 0 and pct == 0:
        return ("under 1% of its prose is in the incoming conversation - it is "
                "essentially its OWN conversation")
    if pct >= 90:
        return ("{0}% of its prose is already in the incoming conversation - a "
                "little is only there".format(pct))
    if pct >= 50:
        return ("only {0}% of its prose is in the incoming conversation - a real "
                "part is only there".format(pct))
    return ("just {0}% of its prose is in the incoming conversation - it is "
            "largely its OWN conversation".format(pct))


@dataclasses.dataclass
class RepointFlags:
    """Which row to repoint, and at what."""
    only: str = ""          # substring of the row's title, or its local id
    to_session: str = ""    # the cliSessionId the row should open instead
    store: str = ""         # substring naming the store; default = the live one
    live: str = ""          # RULING 5 assertion, as sync uses it


# "the caller did not look", which is NOT "the caller looked and there is no
# live account". live_account legitimately returns None (the identity files
# disagree), and a plain None default would send every such call back to look
# again - the one case where re-looking is guaranteed to find nothing.
_LIVE_UNRESOLVED = object()


def _email_of(env, account_uuid, live=_LIVE_UNRESOLVED):
    """Just the email for an account, or "". account_email returns
    (email, provenance) and every caller that wants a label wants the first
    half; unpacking it here keeps that mistake in one place.

    It also covers account_email's one deliberate blind spot. That function
    answers for a DORMANT account - the per-account sandbox config, then the
    memo - and neither source knows the account the app is signed INTO, whose
    email lives only in ~/.claude.json's oauthAccount, which only live_account
    reads. So the live account was the single account whose own email named
    nothing: `repoint --store <the email you are signed in as>` refused while
    that account sat in the listing the refusal printed, and the plan labelled
    the store you are looking at with eight hex characters. Invisible on a
    machine that already holds an email memo for every account, which is how it
    shipped.

    Asked HERE rather than inside account_email so that function's dormant-only
    contract stays exactly as documented - callers label its second half
    'sandbox' or 'memo:<date>' and a freshly observed email is neither. This
    returns a bare string, so there is no provenance to misreport.

    live_account is a safe source for the pairing: it takes the uuid and the
    email from one oauthAccount record and this checks that uuid against the
    one asked for, the same standard dormant_account_email holds its sandbox
    configs to. It also returns None outright when the two identity files
    disagree (RULING 4), so a stale oauthAccount cannot answer here for an
    account it no longer names.

    LIVE may be passed by a caller that has already resolved it - live_account
    re-reads both identity files and re-walks the store tree on every call
    (11 ms of a 300 ms `repoint` plan, measured 2026-08-23 over nine store
    dirs), and its answer is the same for every account in one command.
    Omitting it is always correct, only slower.
    """
    if live is _LIVE_UNRESOLVED:
        live = live_account(env)
    if live and live.account_uuid == account_uuid and live.email:
        return live.email
    got = account_email(env, account_uuid)
    if isinstance(got, tuple):
        return got[0] or ""
    return got or ""


def _path_match_key(path):
    """A path flattened for substring matching: lower case, forward slashes.

    The point is that BOTH sides go through it. os.path.normcase looks like the
    right primitive and is not: on Windows it lower-cases AND rewrites "/" as
    "\\", so normalizing only the candidate left a `want` that had merely been
    .lower()ed still carrying the backslashes every Windows path has - including
    the ones this tool prints in its own "which store did you mean" listing.
    Pasting one of those back was refused. On POSIX normcase is the identity
    function, so there the candidate kept its case while `want` was lowered, and
    any upper-case component failed the same way.

    Lower-casing both sides is more lenient than a case-sensitive filesystem
    strictly requires. That is deliberate: this is a substring match on a
    fragment a person typed, the other branches beside it are already
    case-insensitive, and matching several stores is a normal outcome here
    rather than an error - the caller lets the ROW settle which one it meant.
    """
    return path.lower().replace("\\", "/")


def _repoint_store(env, flags, what="repoint"):
    """(path, label) of the store whose row is being repointed.

    Defaults to the LIVE account's store, and that is the whole difference
    between this command and `sync`. Sync refuses to write the account the app
    is signed into, because the account you are USING is the one whose state you
    least want a background tool rearranging. Repoint exists precisely to fix
    the sidebar you are looking at, so that refusal would rule out its only
    real use. What protects it instead is the running-app guard: the app must be
    closed, which is what makes "the live account's store" a safe target rather
    than a live one. That is the same guard whose absence caused the loss this
    command exists to undo - the app repointed a row itself, while running,
    through no route this tool controls.
    """
    dirs = _account_dirs(env)
    if flags.store:
        pair = _pair_query(flags.store)
        if pair is not None:
            # The printed account form, parsed rather than searched (see
            # _pair_query). It filters the same dirs the substring branches
            # filter, and everything downstream - the no-match refusal, the
            # let-the-row-settle-it multi-hit return - is shared.
            hits = [(a, o, p) for a, o, p in dirs
                    if _pair_matches(pair, a, o)]
        else:
            # Once, not once per candidate: the same answer for every dir
            # below. Only in this branch - the --live path resolves its own,
            # and asking here would be work thrown away.
            live = live_account(env)
            want = flags.store.lower()
            # The path gets its OWN normalized form, and is the only branch that
            # uses it. Kept separate from `want` so the substring semantics of the
            # other three branches are untouched: neither a uuid nor an email can
            # contain a path separator, so folding one in would be a no-op for them
            # in practice - but a no-op by assumption rather than by construction,
            # and this is exactly the kind of shared-string reasoning that produced
            # the mismatch _path_match_key documents.
            want_path = _path_match_key(flags.store)
            # Email included on purpose: an account id and an org id BOTH collide
            # across stores on a real machine (three accounts x three org dirs here),
            # so the fragments people reach for first are exactly the ones that come
            # back ambiguous. The email is how a person names an account.
            # account_email returns (email, provenance) - not a bare string. Taking
            # it for one raised AttributeError on the first real invocation.
            hits = [(a, o, p) for a, o, p in dirs
                    if want in a.lower() or want in o.lower()
                    or want in (_email_of(env, a, live) or "").lower()
                    or want_path in _path_match_key(p)]
        if not hits:
            raise Refusal("--store {0!r} matched no store on this machine:\n{1}"
                          .format(flags.store, _candidate_listing(dirs)))
        # Deliberately NOT refused here when several match. An account owns one
        # store per org, and naming it by email necessarily matches all of them -
        # but only one can hold the row being repointed, and the others are the
        # empty cross-pair scaffolding described under "The account x org
        # cross-pair". Let the ROW settle it: the caller searches every candidate
        # and refuses only if the row itself is ambiguous. That is a question
        # about the thing being changed rather than about directory naming, which
        # is the one the user can actually answer.
        return hits
    # --live, wired the same way sync wires it. It was parsed, stored on the
    # flags, named in the refusal below as the remedy - and never read, so the
    # one state it exists for (the identity files disagreeing, where
    # live_account returns None) sent the user to a flag that did nothing.
    if flags.live:
        live = _resolve_live_assertion(env, flags.live, dirs)
    else:
        live = live_account(env)
    if not live or not live.path:
        # `what` names the CALLER's verb. `_new_row_store` reuses this whole
        # function, so a `new-row` user with ambiguous identity files was told
        # there was "no default store to repoint" by a command that repoints
        # nothing.
        raise Refusal(
            "cannot identify the signed-in account, so there is no default store to "
            "{0}. Name one with --store <account id, email, or path substring>, "
            "or assert the live account with --live (RULING 5).".format(what))
    return [(live.account_uuid, getattr(live, "org_uuid", "") or "", live.path)]


def plan_repoint(env, flags):
    """Build a repoint manifest. Pure planning - writes nothing.

    A repoint changes ONE field in ONE row: the `cliSessionId` that decides
    which conversation a sidebar entry opens. Everything else in the row is
    preserved byte-for-byte, because nothing else is wrong with it.
    """
    if not flags.to_session:
        raise Refusal("--to is required: the cliSessionId the row should open")
    if not flags.only:
        raise Refusal("--only is required: a substring of the row's title, or its "
                      "local id, naming exactly one row to repoint")
    candidates = _repoint_store(env, flags)
    want = flags.only.lower()
    hits = []
    for a, o, store in candidates:
        label = "{0} ({1}{2})".format(_email_of(env, a) or a[:8], a[:8],
                                      "/" + o[:8] if o else "")
        try:
            names = _listdir_or_refuse(store, "the store")
        except Refusal:
            continue                      # an empty or unreadable sibling store
        for name in sorted(names):
            if not (name.startswith("local_") and name.endswith(".json")):
                continue
            p = os.path.join(store, name)
            try:
                d = read_json(p)
            except LayoutError:
                continue
            if not isinstance(d, dict):
                continue
            title = d.get("title") or ""
            local_id = name[len("local_"):-len(".json")]
            if want in title.lower() or want in local_id.lower():
                hits.append((store, label, name, p, d, title))
    if not hits:
        where = ", ".join(sorted({"{0}".format(a[:8]) for a, _o, _p in candidates}))
        raise Refusal("no row matching --only {0!r} in any store for {1}"
                      .format(flags.only, where))
    if len(hits) > 1:
        listing = "\n".join("   {0}   {1}   [{2}]".format(n, (t or "(untitled)")[:52], lb)
                            for _s, lb, n, _p, _d, t in hits[:10])
        raise Refusal("--only {0!r} matches {1} rows; name one exactly (a local id is "
                      "unambiguous):\n{2}".format(flags.only, len(hits), listing))
    store, label, name, path, data, title = hits[0]
    current = data.get("cliSessionId")
    if current == flags.to_session:
        raise Refusal("that row already opens {0}; nothing to repoint"
                      .format(flags.to_session))
    # The target has to exist, or this trades a reachable conversation for a
    # dangling pointer - a worse state than the one being fixed.
    found = find_transcripts(env.projects_root, flags.to_session)
    if not found:
        raise Refusal(
            "no transcript on disk for {0}, so repointing there would leave the row "
            "opening nothing. Check the id - 'doctor' lists conversations that no "
            "account points at.".format(flags.to_session))
    if len(found) > 1:
        raise Refusal("{0} exists in more than one project folder; refusing to guess "
                      "which:\n{1}".format(flags.to_session,
                                           "\n".join("   " + f for f in found)))
    with open(path, "rb") as fh:
        pre = fh.read()
    post_data = dict(data)
    post_data["cliSessionId"] = flags.to_session
    post = json.dumps(post_data, separators=(",", ":")).encode("utf-8")
    return {"op_type": "repoint", "store_path": store, "store_label": label,
            "name": name, "row_path": path, "title": title or "(untitled)",
            "from_session": current, "to_session": flags.to_session,
            "transcript": found[0],
            "transcript_mb": round(os.path.getsize(found[0]) / 1e6, 1),
            "rows": [{"name": name, "dest_path": path, "title": title or "(untitled)",
                      "pre_b64": b64(pre), "post_b64": b64(post),
                      "is_update": True, "written": False}]}


def execute_repoint_op(env, op):
    """journaled -> writing -> completed. One row, one field, one write."""
    m = op.manifest
    # "writing" is resumable, not an error. A repoint that died between the two
    # set_status calls would otherwise be neither finishable nor reversible:
    # execute refused it, undo refuses anything not "completed", and nonterminal
    # ops are never rotated away - a permanent entry in every doctor report
    # pointing at a recovery route that could not help. Re-entry is safe because
    # every decision below is made from the bytes on disk, not from what the
    # journal expects to find.
    if m.get("status") not in ("journaled", "writing"):
        raise LayoutError("execute_repoint_op runs ops from 'journaled' or "
                          "'writing'; this one is " + str(m.get("status")))
    # The guard that was missing when this row was repointed by something else.
    _guard_mutation(env, "repoint")
    if not os.path.isdir(m["store_path"]):
        raise LayoutError("store vanished: " + m["store_path"])
    # Re-check the target HERE, not only at plan time. Between a dry run and
    # --apply the transcript can be deleted, and writing then trades a reachable
    # conversation for a row that opens nothing - the exact state this command
    # exists to repair.
    if not find_transcripts(env.projects_root, m["to_session"]):
        raise Refusal(
            "the transcript for {0} is no longer on disk, so this repoint would "
            "leave the row opening nothing. Nothing was written."
            .format(m["to_session"]))
    set_status(op, "writing")
    r = m["rows"][0]
    real = ensure_contained(r["dest_path"], [m["store_path"]])
    if os.path.dirname(real) != os.path.realpath(m["store_path"]):
        raise LayoutError("row {0!r} is not a direct child of {1!r}; refusing"
                          .format(r["dest_path"], m["store_path"]))
    pre, post = _sync_pre_image(r), unb64(r["post_b64"])
    try:
        with open(r["dest_path"], "rb") as fh:
            current = fh.read()
    except OSError as exc:
        raise Refusal("could not read the row to check it for changes since "
                      "planning: {0}".format(exc))
    if current == post:
        r["written"] = True                      # already done
    elif current != pre:
        raise Refusal(
            "that row changed since this repoint was planned, so writing would "
            "discard whatever changed it. Re-run to plan against its current state.")
    else:
        try:
            atomic_write(r["dest_path"], post)
        except OSError as exc:
            raise Refusal("could not write the row: {0}".format(exc))
        r["written"] = True
    save_manifest(op)
    set_status(op, "completed")
    return "completed"


def run_repoint(env, manifest):
    """Lock, journal, execute, rotate - the same shape as run_sync."""
    acquire_lock(env, "repoint")
    try:
        op = new_op(env, manifest)
        # Hand the op_id back, exactly as run_sync does and for the same reason:
        # new_op shallow-copies the manifest and sets op_id on ITS copy, so
        # without this the caller cannot name the op it just ran - which is what
        # `undo --id` and every report line need.
        manifest["op_id"] = op.manifest["op_id"]
        set_status(op, "journaled")
        final = execute_repoint_op(env, op)
        rotate_ops(env)
        return final
    finally:
        release_lock(env)


def undo_repoint(env, op):
    """Put the original cliSessionId back, byte-for-byte from the pre-image.

    Same evidence rule as undo_sync: only while the row still holds exactly what
    this op wrote. If something has touched it since - the app, another repoint -
    refuse rather than clobber it.
    """
    m = op.manifest
    acquire_lock(env, "undo-" + m["op_id"])
    try:
        if m.get("op_type") != "repoint":
            raise Refusal("not a repoint op: " + str(m.get("op_id")))
        if m.get("status") != "completed":
            raise Refusal("op {0} is '{1}', not 'completed'".format(
                m.get("op_id"), m.get("status")))
        _guard_mutation(env, "repoint")
        r = m["rows"][0]
        if not r.get("written"):
            raise Refusal("this repoint never wrote the row; nothing to undo")
        real = ensure_contained(r["dest_path"], [m["store_path"]])
        if os.path.dirname(real) != os.path.realpath(m["store_path"]):
            raise LayoutError("row {0!r} is not a direct child of {1!r}; refusing"
                              .format(r["dest_path"], m["store_path"]))
        state = _sync_row_drift(r)
        if state != "match":
            raise Refusal(
                "that row no longer holds what this repoint wrote ({0}); something "
                "changed it since - most likely the app, which rewrites these rows "
                "whenever it opens the session. Refusing to overwrite it."
                .format(state))
        atomic_write(r["dest_path"], _sync_pre_image(r))
        set_status(op, "undone")
        rotate_ops(env)
        return "undone"
    finally:
        release_lock(env)


def _print_repoint_report(say, m):
    say("store   : {0}".format(m["store_label"]))
    say("row     : {0}".format(m["name"]))
    say("title   : {0}".format(m["title"]))
    say("")
    say("opens now : {0}".format(m["from_session"] or "(nothing)"))
    say("will open : {0}   ({1} MB on disk)".format(m["to_session"], m["transcript_mb"]))
    say("")
    say("Only the row's cliSessionId changes. Both conversations stay on disk -")
    say("this decides which one that sidebar entry opens.")


def _public_repoint_manifest(m):
    """The repoint manifest with both row images removed, for --json.

    `pre_b64` and `post_b64` are the destination row VERBATIM - the same bytes
    `_public_manifest` scrubs out of `sync --json`, and for the same reason:
    a listing row carries `remoteMcpServersConfig` and permission state, and
    printing it to stdout lets ordinary automation log another account's
    connector configuration. This route had the identical exposure and no
    scrub, which is worse than sync's was, because a repoint's post-image is a
    copy of the row it is about to write rather than a stripped transform.
    """
    out = {k: v for k, v in m.items() if k != "rows"}
    out["rows"] = [{k: v for k, v in r.items() if k not in ("pre_b64", "post_b64")}
                   for r in m.get("rows", [])]
    return out


def cmd_repoint(env, ns):
    flags = RepointFlags(only=ns.only, to_session=ns.to_session,
                         store=ns.store, live=ns.live)
    m = plan_repoint(env, flags)
    # --apply runs BEFORE --json prints, the way cmd_sync does it. Printing and
    # returning first meant `repoint --apply --json` reported a plan, exited 0,
    # and wrote nothing - automation would read that as a completed repoint.
    final = run_repoint(env, m) if ns.apply else None
    if ns.json:
        pub = _public_repoint_manifest(m)
        if final is not None:
            pub["result"] = final
        print(json.dumps(pub, indent=1))
        return 0
    _print_repoint_report(print, m)
    if final is None:
        print("\ndry run - pass --apply to repoint")
        return 0
    print("\nresult  : {0}".format(final))
    print("Reopen the app and check the session - 'undo' puts the old pointer back.")
    return 0


# ------------------------------------------------------------------ retitle
# Design and its measured facts: docs/specs/2026-08-26-retitle-design.md. The
# short version: renaming OUTSIDE the journal already failed twice (backups
# that overwrote each other, an undo that restored the newest run forever),
# and this command exists to put renaming inside the machinery that prevents
# both.


@dataclasses.dataclass
class RetitleFlags:
    """Which conversation to rename, to what, and in which account(s)."""
    only: str = ""          # title substring, or a cliSessionId prefix
    title: str = ""         # the new title; stored trimmed
    store: str = ""         # substring naming ONE account; default = every account
    live: str = ""          # RULING 5 assertion, as sync and new-row use it


def _valid_new_title(raw):
    """The trimmed title this command would store, or a Refusal.

    The stored value is the TRIMMED input, and every comparison anywhere in
    this command uses that same trimmed form - the spec's first draft trimmed
    for comparison and stored the raw input, which is two titles pretending to
    be one. No length cap and no Unicode normalisation: the app imposes
    neither (titles with em-dashes, arrows and 70+ characters exist and
    render), and a command stricter than the surface it manages would refuse
    titles the app itself writes.
    """
    title = (raw or "").strip()
    if not title:
        raise Refusal(
            "--title is empty once surrounding whitespace is trimmed. An empty "
            "title can leave the row unclickable in the app; nothing useful is "
            "behind allowing it.")
    bad = sorted({"U+{0:04X}".format(ord(ch)) for ch in title if ord(ch) < 0x20})
    if bad:
        raise Refusal(
            "--title contains control character(s) ({0}); newlines and C0 "
            "controls are refused, not stripped - silently altering the name "
            "you typed is how surprises ship.".format(", ".join(bad)))
    return title


def _strict_row_dict(raw):
    """A row's parsed dict, refusing what json.loads would silently accept.

    json.loads keeps the LAST of two duplicate keys, so a row carrying
    {"title": "A", "title": "B"} parses cleanly and re-serialises with half its
    story gone. This command rewrites whole rows from their parse, which is
    exactly the operation that would launder such a row - so a duplicate key is
    treated as unreadable rather than collapsed. Raises ValueError.
    """
    def no_dupes(pairs):
        d = {}
        for k, v in pairs:
            if k in d:
                raise ValueError("duplicate JSON key {0!r}".format(k))
            d[k] = v
        return d
    return json.loads(raw.decode("utf-8"), object_pairs_hook=no_dupes)


@dataclasses.dataclass
class _RetitleRow:
    """One row file as the scan saw it: where, whose, and its exact bytes."""
    account: str
    org: str
    store: str
    label: str
    name: str
    path: str
    raw: bytes
    data: dict


def _retitle_scan(env, live=_LIVE_UNRESOLVED, store_path="",
                  why=("whether it belongs to the conversation being renamed "
                       "or already holds the new title")):
    """Every local_*.json row in scope, strictly read. Refuses on ANY row it
    cannot parse: an unreadable row could be a copy of the very conversation
    being renamed, or already hold the new title - "couldn't look" is never
    "nothing there", and both the target set and the collision rule depend on
    having looked. STORE_PATH narrows the scan to one resolved account
    directory (the --store scope); default is every account on the machine.
    WHY names, in the refusal, what the unreadable row leaves undecidable -
    `converge` shares this scan and its stakes are worded differently.
    """
    if live is _LIVE_UNRESOLVED:
        live = live_account(env)
    dirs = _account_dirs(env)
    if store_path:
        key = os.path.normcase(os.path.realpath(store_path))
        dirs = [(a, o, p) for a, o, p in dirs
                if os.path.normcase(os.path.realpath(p)) == key]
        if not dirs:
            raise Refusal("the store this plan named no longer exists on disk: "
                          "{0}. Re-run to replan.".format(store_path))
    out = []
    for acct, org, store in dirs:
        label = "{0} ({1}{2})".format(_email_of(env, acct, live) or acct[:8],
                                      acct[:8], "/" + org[:8] if org else "")
        for name in sorted(_listdir_or_refuse(store, "an account directory")):
            if not (name.startswith("local_") and name.endswith(".json")):
                continue
            path = os.path.join(store, name)
            try:
                with open(path, "rb") as fh:
                    raw = fh.read()
                data = _strict_row_dict(raw)
            except (OSError, ValueError) as exc:
                raise Refusal(
                    "the row {0!r} in {1} could not be read ({2}), so this "
                    "command cannot tell {3}. Refusing; "
                    "nothing was written. 'doctor' reports rows it cannot parse "
                    "- repair or remove it, then re-run.".format(name, label,
                                                                 exc, why))
            if not isinstance(data, dict):
                raise Refusal(
                    "the row {0!r} in {1} is not a JSON object; refusing to plan "
                    "around it. Nothing was written. Repair or remove it, then "
                    "re-run.".format(name, label))
            out.append(_RetitleRow(acct, org, store, label, name, path, raw, data))
    return out


def _retitle_candidate_listing(conversations):
    """One line per candidate conversation: id, title, accounts, last activity.

    This listing is the WORKFLOW, not a dead end: a colliding title matches
    several conversations by construction, so the expected first run refuses
    with this list and the retry is a copy-paste of an id (--only takes a
    prefix, so the 8 characters printed here are enough while they are unique).
    """
    lines = []
    for cid in sorted(conversations):
        recs = conversations[cid]
        newest = max(recs, key=lambda rec: _activity_of(rec.data) or 0)
        ms = _activity_of(newest.data)
        try:
            when = (time.strftime("%Y-%m-%d", time.gmtime(ms / 1000.0))
                    if ms else "unknown")
        except (OverflowError, OSError, ValueError):
            when = "unknown"
        labels = sorted({rec.label for rec in recs})
        lines.append("   {0}   {1:<52}  {2} account(s): {3}   last activity {4}"
                     .format(cid[:8], (newest.data.get("title") or "(untitled)")[:52],
                             len(labels), ", ".join(labels), when))
    return "\n".join(lines)


def plan_retitle(env, flags):
    """Build a retitle manifest. Pure planning - writes nothing.

    A "conversation" is a cliSessionId, and the default target set is every
    row, in every account, whose cliSessionId equals the resolved id - the
    recurring need is "this conversation reads the same everywhere", and the
    failure mode of per-account renaming is three sidebars drifting apart.
    --only resolves the way repoint's does (a title substring, or an id
    prefix) but to a CONVERSATION rather than a row, because the same
    conversation legitimately has one row per account.

    Every refusal computed here is re-checked at apply time against the store
    as it then is (_retitle_recheck); this pass exists for the dry run and to
    keep a doomed plan from ever reaching the journal.
    """
    if not flags.only:
        raise Refusal("--only is required: a title substring, or a cliSessionId "
                      "prefix, naming exactly one conversation")
    if not flags.title:
        raise Refusal("--title is required: the new sidebar title")
    new_title = _valid_new_title(flags.title)
    if flags.live:
        live = _resolve_live_assertion(env, flags.live, _account_dirs(env))
    else:
        live = live_account(env)
    scope_store = scope_label = scope_why = ""
    scope_guess = False
    if flags.store:
        # The same matcher new-row uses, guess semantics included: a store
        # picked by row counts may PLAN and never WRITE (cmd_retitle refuses
        # --apply on it, naming the path that would settle it).
        acct, org, scope_store, scope_why, scope_guess = _new_row_store(env, flags)
        scope_label = "{0} ({1}{2})".format(_email_of(env, acct, live) or acct[:8],
                                            acct[:8], "/" + org[:8] if org else "")

    # The scan is ALWAYS machine-wide, even under --store: --only resolves to
    # a conversation across every account (the row in the narrowed store may
    # carry the drifted title that made the repair necessary), and the sibling
    # report below needs the other accounts' copies either way.
    records = _retitle_scan(env, live=live)
    want = flags.only.lower()
    conversations = {}
    for rec in records:
        cid = rec.data.get("cliSessionId") or ""
        if not cid:
            continue                    # a row that opens nothing is not a conversation
        title = rec.data.get("title") or ""
        if want in title.lower() or cid.lower().startswith(want):
            conversations.setdefault(cid, []).append(rec)
    if not conversations:
        raise Refusal(
            "no row in any account matches --only {0!r} (a title substring, or "
            "a cliSessionId prefix). If the conversation exists on disk but no "
            "account has a row for it, renaming is not the gap - creating a row "
            "is 'new-row's job: claude-code-sessions new-row --to <cliSessionId>."
            .format(flags.only))
    if len(conversations) > 1:
        raise Refusal(
            "--only {0!r} matches {1} conversations - expected when resolving a "
            "colliding title, since a colliding title names several by "
            "construction. Re-run with the session id of the one you mean (a "
            "prefix is enough):\n{2}".format(
                flags.only, len(conversations),
                _retitle_candidate_listing(conversations)))
    sid = next(iter(conversations))

    # The target set is re-collected from the FULL scan, not from the rows that
    # matched --only: a conversation whose titles have already drifted apart
    # across accounts - the exact state --store repairs - matches on some rows
    # and not others, and all of them are the conversation.
    targets = [rec for rec in records
               if (rec.data.get("cliSessionId") or "") == sid]
    siblings = []
    if scope_store:
        skey = os.path.normcase(os.path.realpath(scope_store))
        in_scope = [rec for rec in targets
                    if os.path.normcase(os.path.realpath(rec.store)) == skey]
        siblings = [rec for rec in targets
                    if os.path.normcase(os.path.realpath(rec.store)) != skey]
        if not in_scope:
            raise Refusal(
                "no row in {0} opens {1}, so there is nothing there to rename. "
                "Creating one is 'new-row's job: claude-code-sessions new-row "
                "--to {1} --store <path>.".format(scope_label or flags.store, sid))
        targets = in_scope

    # The new title must not equal an existing title in any TARGET sidebar
    # (trimmed exact match), excluding the target conversation's own rows. The
    # exclusion matters twice: a case-only or punctuation fix must not collide
    # with itself, and retitling to the SAME title is allowed because it still
    # performs a useful write - pinning titleSource to "user" so the app stops
    # resummarising.
    target_keys = {os.path.normcase(os.path.realpath(rec.store)) for rec in targets}
    for rec in records:
        if os.path.normcase(os.path.realpath(rec.store)) not in target_keys:
            continue
        if (rec.data.get("cliSessionId") or "") == sid:
            continue
        if (rec.data.get("title") or "").strip() == new_title:
            raise Refusal(
                "that title already names a different conversation in {0}: row "
                "{1!r} (opens {2}). Two rows in one sidebar under one name is "
                "the state this command exists to remove, so there is no "
                "override - retitle that row first if the name is truly wanted. "
                "Nothing was written.".format(
                    rec.label, rec.name, (rec.data.get("cliSessionId") or "?")[:8]))

    # A one-account rename can CREATE cross-account divergence; the plan says
    # so rather than forbidding it. alignment's `distinguishable` line counts
    # per sidebar, so a --store rename cannot increase it - but the accounts
    # can end up reading differently, which is what this reports.
    sib_entries = [{"label": rec.label, "title": rec.data.get("title") or ""}
                   for rec in sorted(siblings, key=lambda rec: (rec.store, rec.name))]
    divergence = any((e["title"] or "").strip() != new_title for e in sib_entries)

    rows = []
    for rec in sorted(targets, key=lambda rec: (rec.store, rec.name)):
        post_data = dict(rec.data)
        post_data["title"] = new_title
        post_data["titleSource"] = "user"
        post = json.dumps(post_data, separators=(",", ":")).encode("utf-8")
        rows.append({"name": rec.name, "dest_path": rec.path,
                     "store_path": rec.store, "label": rec.label,
                     "title": rec.data.get("title") or "(untitled)",
                     "pre_b64": b64(rec.raw), "post_b64": b64(post),
                     "is_update": True, "written": False})
    return {"op_type": "retitle", "cli_session_id": sid, "new_title": new_title,
            "store_path": scope_store, "store_label": scope_label,
            "store_why": scope_why, "store_is_a_guess": scope_guess,
            "siblings": sib_entries, "sibling_divergence": divergence,
            "rows": rows}


# What retitle's mutation routes pass to _guard_mutation. RETITLE reuses
# new-row's `whose` (NEW_ROW_STORE, "the session store") and brings its own
# `because`: the risk here is not a row appearing or vanishing but a title
# being decided by the wrong writer.
RETITLE_GUARD_WHY = ("The app holds these rows in memory and rewrites them on "
                     "focus, so a title written underneath it is whatever the "
                     "app decides later, not what you wrote")


def _retitle_shape_error(m):
    """A sentence naming what is wrong with this retitle manifest, or None.

    Same job as _repoint_shape_error and _new_row_shape_error, for the
    multi-row shape: recover and undo dereference rows straight out of a
    journal file a user can edit, and a bare KeyError out of either escapes
    main(). NEVER RAISES - classify_op calls it, and classify_op must never
    raise.
    """
    rows = m.get("rows")
    if not isinstance(rows, list) or not rows:
        return "its 'rows' is not a non-empty list"
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            return "row {0} is not an object".format(i)
        for k in ("name", "dest_path", "store_path", "pre_b64", "post_b64"):
            if not isinstance(r.get(k), str) or not r[k]:
                return "row {0} has no usable {1!r}".format(i, k)
    if not isinstance(m.get("new_title"), str) or not m["new_title"]:
        return "it has no usable 'new_title'"
    if not isinstance(m.get("cli_session_id"), str) or not m["cli_session_id"]:
        return "it has no usable 'cli_session_id'"
    return None


def _retitle_roots(env):
    """The discovered store roots retitle's containment checks run against.

    Roots come from discover_stores, never from the journal being checked -
    deriving a row's allowed root from the same manifest that supplied its
    path would let a hand-edited journal authorize itself.
    """
    disc = discover_stores(env)
    if disc.status == "error":
        raise LayoutError("store discovery failed: {0}. 'Couldn't look' is "
                          "never 'nothing there' - refusing.".format(disc.detail))
    return disc.roots


def _retitle_contained(r, roots):
    """The row's real path, after the same two-step check every other row
    write in this module makes: inside a discovered store root, AND a direct
    child of the store this op recorded for it - atomic_write's scratch file
    lands in the row's parent directory, so 'under the root somewhere' is not
    enough."""
    real = ensure_contained(r["dest_path"], roots)
    if os.path.dirname(real) != os.path.realpath(r["store_path"]):
        raise LayoutError("row {0!r} is not a direct child of {1!r}; refusing"
                          .format(r["dest_path"], r["store_path"]))
    return real


def _retitle_recheck(env, m):
    """The plan's refusals, re-run against the store AS IT NOW IS - under the
    operation lock, before anything is journaled (run_retitle) or resumed
    (recover --forward). Raises Refusal; returns nothing.

    Three re-checks, in the order the spec states them:
    - the target set is re-enumerated: a row added by sync or removed in the
      app between plan and apply changes what "every account" means, and
      renaming the set the plan showed while reporting success against the set
      that now exists would be a quiet lie;
    - the collision rule, against titles as they are now;
    - every not-yet-written row must still hold the exact bytes the plan
      measured (its journaled pre-image) - drift means the app or another op
      moved underneath, and the answer is a replan, never a blind overwrite.
    """
    sid = m["cli_session_id"]
    new_title = m["new_title"]
    records = _retitle_scan(env, store_path=m.get("store_path") or "")
    current = {}
    for rec in records:
        if (rec.data.get("cliSessionId") or "") == sid:
            current[os.path.normcase(os.path.abspath(rec.path))] = rec
    planned = {os.path.normcase(os.path.abspath(r["dest_path"])): r
               for r in m["rows"]}
    appeared = sorted(set(current) - set(planned))
    vanished = sorted(set(planned) - set(current))
    if appeared or vanished:
        parts = []
        if appeared:
            parts.append("appeared since: " + ", ".join(
                "{0} ({1})".format(current[k].name, current[k].label)
                for k in appeared))
        if vanished:
            parts.append("gone since: " + ", ".join(
                planned[k].get("name", "?") for k in vanished))
        raise Refusal(
            "the set of rows holding this conversation changed between planning "
            "and applying ({0}), so what \"every account\" means has changed. "
            "Nothing was written - re-run to plan against the store as it is "
            "now.".format("; ".join(parts)))
    target_keys = {os.path.normcase(os.path.realpath(r["store_path"]))
                   for r in m["rows"]}
    for rec in records:
        if os.path.normcase(os.path.realpath(rec.store)) not in target_keys:
            continue
        if (rec.data.get("cliSessionId") or "") == sid:
            continue
        if (rec.data.get("title") or "").strip() == new_title:
            raise Refusal(
                "that title now names a different conversation in {0}: row "
                "{1!r} appeared or was renamed since this plan was made. Nothing "
                "was written - re-run to replan.".format(rec.label, rec.name))
    for r in m["rows"]:
        if r.get("written"):
            continue
        rec = current[os.path.normcase(os.path.abspath(r["dest_path"]))]
        try:
            pre = _sync_pre_image(r)
            post = unb64(r["post_b64"])
        except (KeyError, ValueError) as exc:
            raise Refusal(
                "row {0!r}: this plan's journaled images cannot be read ({1}); "
                "refusing rather than write from a record that could not restore "
                "what it replaced. Nothing was written.".format(r.get("name"), exc))
        if rec.raw != pre and rec.raw != post:
            raise Refusal(
                "row {0!r} in {1} changed since this plan was made - writing "
                "would discard whatever changed it, most likely the app. Nothing "
                "was written; re-run to plan against its current state."
                .format(r.get("name"), r.get("label", "")))


def _retitle_write_rows(op, m, roots):
    """execute_retitle_op's write loop, split out so its caller can wrap it in
    the journal-on-the-way-out handler. Per row: containment, byte-drift
    re-check, atomic write, then the verify the scaffold taught - re-read and
    confirm `title` and `titleSource` changed and EVERY other field is
    value-identical after parsing to the journaled preimage (value-identical,
    not byte-identical: the row is re-serialised from a dict, so
    representation may change; content may not).
    """
    rows = m["rows"]
    new_title = m["new_title"]
    for i, r in enumerate(rows):
        if r.get("written"):
            continue
        _retitle_contained(r, roots)
        try:
            pre = _sync_pre_image(r)
            post = unb64(r["post_b64"])
        except (KeyError, ValueError) as exc:
            raise Refusal(
                "row {0!r}: the journaled images cannot be read ({1}); refusing "
                "rather than write a row whose original bytes this op could no "
                "longer restore. The op is left at 'writing' - 'recover "
                "--resolve {2} --back' reverses what did land."
                .format(r.get("name"), exc, m.get("op_id", "")))
        try:
            with open(r["dest_path"], "rb") as fh:
                current = fh.read()
        except FileNotFoundError:
            raise Refusal(
                "row {0!r} ({1}) is gone from the store since this plan was "
                "made - that account removed it, and re-creating it to rename "
                "it would resurrect a row the account deleted. The op is left "
                "at 'writing' - 'recover --resolve {2} --back' reverses what "
                "did land.".format(r["name"], r.get("label", ""),
                                   m.get("op_id", "")))
        except OSError as exc:
            raise Refusal(
                "could not read row {0!r} to check it for changes since "
                "planning: {1}. The op is left at 'writing' - resolve the row, "
                "then re-run.".format(r["name"], exc))
        if current == post:
            r["written"] = True             # already holds this op's bytes
            save_manifest(op)
            continue
        if current != pre:
            raise Refusal(
                "row {0!r} ({1}) changed since this retitle was planned - "
                "writing would discard whatever changed it, most likely the "
                "app. The op is left at 'writing' - re-run to replan, or "
                "'recover --resolve {2} --back' to restore the rows already "
                "renamed.".format(r["name"], r.get("label", ""),
                                  m.get("op_id", "")))
        try:
            atomic_write(r["dest_path"], post)
        except OSError as exc:
            raise Refusal(
                "could not write row {0!r}: {1}. The op is left at 'writing' - "
                "re-run, or 'recover --resolve {2} --back' to restore what "
                "landed.".format(r["name"], exc, m.get("op_id", "")))
        _maybe_crash("retitle-write-before-save")
        try:
            with open(r["dest_path"], "rb") as fh:
                check = json.loads(fh.read().decode("utf-8"))
            before = json.loads(pre.decode("utf-8"))
        except (OSError, ValueError) as exc:
            r["written"] = True
            save_manifest(op)
            raise LayoutError(
                "verify failed on {0!r}: the row could not be re-read after "
                "writing ({1}). Its original bytes are in the journal - "
                "'recover --resolve {2} --back --apply' restores every row this "
                "operation wrote.".format(r["name"], exc, m.get("op_id", "")))
        rest_now = {k: v for k, v in check.items()
                    if k not in ("title", "titleSource")}
        rest_was = {k: v for k, v in before.items()
                    if k not in ("title", "titleSource")}
        if (check.get("title") != new_title or check.get("titleSource") != "user"
                or rest_now != rest_was):
            r["written"] = True
            save_manifest(op)
            raise LayoutError(
                "verify failed on {0!r}: the row on disk after the write is not "
                "the planned rename of the journaled original. Nothing more will "
                "be written; 'recover --resolve {1} --back --apply' restores "
                "every row this operation wrote from its journaled preimage."
                .format(r["name"], m.get("op_id", "")))
        r["written"] = True
        save_manifest(op)
        if i < len(rows) - 1:
            _maybe_crash("retitle-mid-write")


def execute_retitle_op(env, op):
    """journaled -> writing -> completed. N rows, two fields each.

    The order is the contract (spec, "Writing"): by the time this runs, the op
    record already holds the complete prior bytes of every target row - new_op
    wrote and fsynced it before this was called - so an interruption anywhere
    in the loop strands nothing: `recover --back` restores every written row
    from its preimage, `recover --forward` finishes the rest after re-running
    the apply-time checks. Both callers (run_retitle, recover_op's forward
    arm) own the RULING 4 guard and the recheck; running either here as well
    would enumerate the process list twice per apply for one answer, which is
    the duplication run_new_row's refactor removed.
    """
    m = op.manifest
    if m.get("status") != "journaled":
        raise LayoutError("execute_retitle_op runs ops from 'journaled'; use "
                          "recover for interrupted ops")
    roots = _retitle_roots(env)
    set_status(op, "writing")
    try:
        _retitle_write_rows(op, m, roots)
    except BaseException:
        # Journal what actually landed before the failure propagates - the
        # same on-the-way-out save execute_sync_op makes, for the same reason:
        # recover needs an exact record of which rows hold new bytes.
        try:
            save_manifest(op)
        except Exception:
            pass
        raise
    set_status(op, "completed")
    return "completed"


def run_retitle(env, manifest):
    """Guard, lock, re-check, journal, execute, rotate.

    The re-check runs BEFORE new_op, so every plan-level refusal - drift, a
    changed target set, a new collision - leaves no op behind (run_new_row's
    lesson: a refusal after journaling turns "this changed nothing" into a
    doctor finding). The journal entry itself is the transactional heart: it
    holds the complete prior bytes of every row before any row is touched. A
    failure writing IT is therefore the one clean failure: nothing landed,
    nothing to recover, and the refusal says so.
    """
    _guard_mutation(env, "retitle rows in", NEW_ROW_STORE,
                    because=RETITLE_GUARD_WHY)
    acquire_lock(env, "retitle")
    try:
        _retitle_recheck(env, manifest)
        try:
            op = new_op(env, manifest)
        except OSError as exc:
            raise Refusal(
                "could not write the operation record ({0}). The journal is "
                "written before any row is touched, so nothing landed and there "
                "is nothing to recover - fix the space or permissions under "
                "{1} and re-run.".format(exc, env.ops_dir))
        # Hand the op_id back, as run_sync and run_repoint do: new_op
        # shallow-copies the manifest, so without this the caller cannot name
        # the op it just ran.
        manifest["op_id"] = op.manifest["op_id"]
        final = execute_retitle_op(env, op)
        rotate_ops(env)
        return final
    finally:
        release_lock(env)


def _retitle_claimed_elsewhere(env, m, r):
    """The op_id of ANOTHER operation whose journal equally accounts for the
    bytes now at this row, or None.

    The restore rule "the file holds this op's post-image, so this op wrote
    it" is sound only while nobody else can mint those bytes - and a second
    retitle of the same row to the same title mints them exactly, the same
    determinism _repoint_claimed_later documents. Per that function's lesson
    (and the spec's): op ids are second-resolution, so "who went last" is not
    decidable evidence - the question is EXCLUSIVITY, "can we prove these
    bytes are ours". Another live op whose journal records writing this path
    with these same bytes means we cannot; one whose recorded bytes DIFFER
    from what is on disk is no obstacle, because matching our post-image
    already proves the disk does not hold theirs. Ops that were undone or
    rolled back have withdrawn their claim (their rows were deleted or put
    back), so they never block - without that, a chain of retitles could
    never be unwound: undoing the newest restores bytes identical to the
    previous op's post-image, and that previous op must then still be
    undoable.

    Never raises - every caller is on a path that must terminate.
    """
    try:
        mine = m.get("op_id") or ""
        dest = os.path.normcase(os.path.abspath(r["dest_path"]))
        post = unb64(r["post_b64"])
        for o in list_ops(env):
            om = o.manifest
            oid = om.get("op_id") or ""
            if oid == mine:
                continue
            if om.get("status") in ("undone", "rolled_back"):
                continue                     # claim withdrawn
            for orow in om.get("rows") or []:
                p = orow.get("dest_path")
                if not isinstance(p, str) or not orow.get("written"):
                    continue
                if os.path.normcase(os.path.abspath(p)) != dest:
                    continue
                raw = orow.get("post_b64")
                if not isinstance(raw, str):
                    return oid               # cannot prove the bytes are not theirs
                try:
                    theirs = unb64(raw)
                except ValueError:
                    return oid
                if theirs == post:
                    return oid
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    return None


def _retitle_restored_collision_note(env, m):
    """After a successful undo: does any restored title now collide with a
    DIFFERENT conversation in the same sidebar? A sentence naming each hit,
    or "".

    Undo restores history even when history collides - exact restoration
    outranks the distinguishability invariant, because undo's one job is to
    put things back, and a silent partial restore in the name of tidiness is
    the scaffold's bug wearing a new hat. So this never blocks anything; it
    only makes the collision visible in the report. Best-effort and NEVER
    RAISES: the undo has already succeeded, and a reporting failure must not
    retroactively turn it into an error.
    """
    try:
        sid = m.get("cli_session_id") or ""
        hits = []
        for r in m.get("rows") or []:
            try:
                old = (json.loads(_sync_pre_image(r).decode("utf-8"))
                       .get("title") or "").strip()
            except Exception:
                continue
            if not old:
                continue
            store = r.get("store_path") or ""
            for name in sorted(os.listdir(store)):
                if not (name.startswith("local_") and name.endswith(".json")):
                    continue
                if name == r.get("name"):
                    continue
                try:
                    d = read_json(os.path.join(store, name))
                except LayoutError:
                    continue
                if not isinstance(d, dict):
                    continue
                if (d.get("cliSessionId") or "") == sid:
                    continue
                if (d.get("title") or "").strip() == old:
                    hits.append("{0!r} is also row {1} in {2}".format(
                        old, name, r.get("label") or store))
                    break
        if not hits:
            return ""
        return ("the restored title(s) now collide with another conversation: "
                "{0}. Restored anyway - undo's job is exact restoration, and "
                "'alignment' counts these per sidebar.".format("; ".join(hits)))
    except Exception:
        return ""


def undo_retitle(env, op):
    """Restore every row this retitle wrote to its journaled preimage - all of
    them or none of them, and consume the op so a second undo reaches the one
    beneath it (both halves are the scaffold's two bugs, stated as behaviour).

    All-or-nothing is the deliberate asymmetry with recover's 'back' arm: a
    partial undo would leave the accounts disagreeing about the title, which
    is the drift this command exists to remove, so a single row that changed
    since - or that another live operation's journal also accounts for -
    refuses the whole reversal and names the row. Byte preimages make the
    restore exact by construction, including titleSource on rows where it was
    absent - the field the scaffold recorded too little to put back.
    """
    m = op.manifest
    acquire_lock(env, "undo-" + m["op_id"])
    try:
        if m.get("op_type") != "retitle":
            raise Refusal("not a retitle op: " + str(m.get("op_id")))
        if m.get("status") != "completed":
            raise Refusal("op {0} is '{1}', not 'completed'".format(
                m.get("op_id"), m.get("status")))
        # Shape before the guard, as undo_new_row orders it: a record this
        # cannot read is refusable without enumerating the process list.
        bad = _retitle_shape_error(m)
        if bad:
            raise Refusal(
                "the record for {0} is damaged ({1}), so undo cannot tell "
                "which rows it renamed; refusing rather than guess. Nothing "
                "was changed.".format(m.get("op_id"), bad))
        _guard_mutation(env, "restore retitled rows in", NEW_ROW_STORE,
                        because=RETITLE_GUARD_WHY)
        roots = _retitle_roots(env)
        blocked, pairs = [], []
        for r in m["rows"]:
            if not r.get("written"):
                continue                     # never landed; nothing to restore
            _retitle_contained(r, roots)
            state = _sync_row_drift(r)
            if state == "match":
                claim = _retitle_claimed_elsewhere(env, m, r)
                if claim:
                    blocked.append((r, "operation {0} also records writing "
                                       "these bytes, so this op can no longer "
                                       "prove they are its own - undo that "
                                       "operation instead".format(claim)))
                    continue
                try:
                    pairs.append((r["dest_path"], _sync_pre_image(r)))
                except (KeyError, ValueError):
                    blocked.append((r, "its journaled pre-image cannot be read"))
            elif state == "absent":
                blocked.append((r, "it is gone from that store - the account "
                                   "removed it, and restoring it would "
                                   "resurrect a row the account deleted"))
            elif state == "drifted":
                blocked.append((r, "it no longer holds what this op wrote - "
                                   "most likely the app, which rewrites these "
                                   "rows whenever it opens the session"))
            else:
                blocked.append((r, "it exists but could not be read"))
        if blocked:
            raise Refusal(
                "cannot undo {0}: {1}. A partial undo would leave the accounts "
                "disagreeing about the title - the exact drift this command "
                "exists to remove - so nothing was changed.".format(
                    m["op_id"],
                    "; ".join("row {0!r} in {1}: {2}".format(
                        r.get("name"), r.get("label", "?"), why)
                        for r, why in blocked)))
        _sync_restore_all(pairs)
        note = _retitle_restored_collision_note(env, m)
        if note:
            m["undo_collision_note"] = note
        set_status(op, "undone")
        rotate_ops(env)
        return "undone"
    finally:
        release_lock(env)


def classify_retitle_op(env, op):
    """Retitle's recovery shape - deliberately the same as sync's, because a
    retitle with rows landed and no completion marker IS the equivalent sync
    state: some sidebars hold the new title, some the old, and they disagree
    until one direction is taken. Forward completes the remaining renames
    after re-running the plan's apply-time checks; back restores every
    written row from its journaled preimage. Both exits leave the sidebars
    agreeing, which is the drift this command exists to remove.

    Never raises (classify_op's contract - cmd_recover classifies every
    pending op to print its listing).
    """
    m = op.manifest
    bad = _retitle_shape_error(m)
    if bad:
        return {"status": m.get("status"), "source": "n/a", "dest": "n/a",
                "resolutions": [], "drifted_rows": [],
                "note": "retitle: this operation's record is damaged ({0}), so "
                        "neither direction can be run from it".format(bad)}
    written = [r for r in m["rows"] if r.get("written")]
    pending = [r for r in m["rows"] if not r.get("written")]
    if m["status"] not in NONTERMINAL:
        return {"status": m["status"], "source": "n/a", "dest": "n/a",
                "resolutions": [], "drifted_rows": [],
                "note": "retitle: {0} row(s) renamed, {1} pending (use undo to "
                        "restore a completed retitle's previous titles)"
                        .format(len(written), len(pending))}
    pend_changed, pend_unreadable, pend_deleted = _sync_drift_titles(pending)
    if pend_changed or pend_unreadable or pend_deleted:
        blocking = pend_changed + pend_unreadable + pend_deleted
        skip_changed, skip_unreadable, skip_deleted = _sync_drift_titles(written)
        skipped = skip_changed + skip_unreadable + skip_deleted
        note = ("retitle: row(s) {0}; it can no longer be rolled forward - "
                "'back' restores the row(s) this op safely can from their "
                "journaled preimages".format(
                    _drift_clause(pend_changed, pend_unreadable, pend_deleted)))
        if skipped:
            note += ("; it will also skip {0} already-renamed row(s) it cannot "
                     "verify ({1})".format(
                         len(skipped),
                         _drift_clause(skip_changed, skip_unreadable,
                                       skip_deleted)))
        return {"status": m["status"], "source": "n/a", "dest": "n/a",
                "resolutions": ["back"], "note": note,
                "drifted_rows": list(dict.fromkeys(blocking + skipped))}
    return {"status": m["status"], "source": "n/a", "dest": "n/a",
            "resolutions": ["forward", "back"], "drifted_rows": [],
            "note": "retitle: {0} row(s) renamed, {1} pending; forward "
                    "completes the remaining renames (re-running the plan's "
                    "checks first), back restores every renamed row from its "
                    "journaled preimage - either way the sidebars end up "
                    "agreeing".format(len(written), len(pending))}


def _public_retitle_manifest(env, m):
    """The retitle manifest with both row images removed, for --json and for
    the printed report - the same exposure rule as _public_repoint_manifest,
    for the same reason: a pre-image is an account's row VERBATIM, connector
    config and permission state included.

    Also where --anonymize is honoured. Existing titles - the rows' current
    ones, the siblings' - go through the standard substitution map. The
    PROPOSED title exists nowhere yet, so no map can cover it; it becomes the
    fixed label <proposed-title>, which keeps an anonymized plan strictly a
    thing to look at or paste (--anonymize --apply is refused globally).
    """
    out = {k: v for k, v in m.items() if k != "rows"}
    out["rows"] = [{k: v for k, v in r.items()
                    if k not in ("pre_b64", "post_b64")}
                   for r in m.get("rows", [])]
    if _ANONYMIZE:
        out = anonymize_report(env, out)
        out["new_title"] = "<proposed-title>"
        # anonymize_report knows "label"; the manifest-level store label is
        # not one of its field names, so cover it here.
        if isinstance(out.get("store_label"), str) and "@" in out["store_label"]:
            out["store_label"] = _anon_label("account", out["store_label"])
    return out


def _print_retitle_report(say, m):
    """The plan, per account: the row file, the current title, the new title.
    M is the PUBLIC manifest (row images stripped, --anonymize already
    applied), never the raw one."""
    if m.get("store_path"):
        say("store   : {0}".format(m.get("store_label", "")))
        say("          chosen as {0}".format(m.get("store_why", "")))
        if m.get("store_is_a_guess"):
            say("          ^ that is a GUESS from row counts, not an "
                "identification. --apply")
            say("            will refuse until you name the store with --store.")
    say("conversation : {0}".format(m.get("cli_session_id", "")))
    say("new title    : {0}".format(m.get("new_title", "")))
    say("titleSource  : pinned to 'user' - the app stops resummarising the row")
    say("")
    for r in m.get("rows", []):
        say("{0}".format(r.get("label", "")))
        say("   row   : {0}".format(r.get("name", "")))
        say("   title : {0!r}  ->  {1!r}".format(r.get("title", ""),
                                                 m.get("new_title", "")))
    if m.get("store_path") and m.get("siblings"):
        say("")
        if m.get("sibling_divergence"):
            # A one-account rename can CREATE the cross-account drift a
            # default-scope rename removes; forbidding it would kill the
            # repair use, so the plan says it out loud instead.
            say("WARNING: this renames ONE account's copy, and the sibling "
                "accounts will")
            say("read differently afterwards. Their current titles:")
        else:
            say("The sibling accounts will read the same after this rename:")
        for e in m["siblings"]:
            say("   {0}   {1!r}".format(e.get("label", ""), e.get("title", "")))
    say("")
    say("Only the row's title and titleSource change. This is a SIDEBAR rename -")
    say("the conversation itself, including its own customTitle, is not touched.")


def cmd_retitle(env, ns):
    flags = RetitleFlags(only=ns.only, title=ns.title, store=ns.store,
                         live=ns.live)
    m = plan_retitle(env, flags)

    def say(line):
        """cmd_new_row's wrapper, for the same two reasons: the report is
        mostly titles (arbitrary text), and piped Windows stdout is the
        console codepage, where an unprintable character must become a
        replacement character rather than a traceback."""
        text = line if ns.verbose else redact(env, line)
        try:
            print(text)
        except UnicodeEncodeError:
            enc = getattr(sys.stdout, "encoding", None) or "utf-8"
            print(text.encode(enc, "replace").decode(enc, "replace"))

    if not ns.json:
        # Report BEFORE the write, as cmd_new_row orders it and for the same
        # reason: under --store the account may have been chosen by a
        # heuristic, and a heuristic the user sees before anything happens is
        # a different promise from one they cannot.
        _print_retitle_report(say, _public_retitle_manifest(env, m))
        if ns.apply:
            say("")
    # A guessed store may PLAN and never WRITE - same contract as new-row.
    if ns.apply and m.get("store_is_a_guess"):
        raise Refusal(
            "which account's copy to rename was decided by counting rows, not "
            "by anything that identifies the account: {0}. That is fine for a "
            "dry run and not fine for a write. Re-run with --store {1!r} if "
            "that is the one you mean. Nothing was written."
            .format(m["store_why"], m["store_path"]))
    final = run_retitle(env, m) if ns.apply else None
    if ns.json:
        # Built AFTER the apply so the written flags and op_id are real - the
        # order cmd_sync and cmd_repoint settled on, because a plan printed
        # with exit 0 is what automation reads as a completed operation.
        pub = _public_retitle_manifest(env, m)
        if final is not None:
            pub["result"] = final
        print(json.dumps(pub, indent=1))
        return 0 if final in (None, "completed") else 1
    if final is None:
        say("\ndry run - pass --apply to retitle")
        return 0
    say("result  : {0}".format(final))
    if final == "completed":
        say("Reopen the app - every targeted sidebar shows the new title. "
            "'undo' puts the previous titles back.")
        return 0
    # execute_retitle_op can only return "completed" or raise today; the
    # explicit non-zero exit is the trap-avoidance cmd_new_row documents.
    return 1


def _public_manifest(m):
    """The manifest with refresh pre-images removed, for --json.

    `pre_b64` is the destination row VERBATIM - including the
    `remoteMcpServersConfig` and permission state the default transform strips
    precisely so they are not carried around. It belongs in the private journal,
    which is what undo restores from; printing it to stdout would let ordinary
    automation log another account's connector configuration without anyone
    asking for --verbatim.
    """
    out = dict(m)
    out["rows"] = [{k: v for k, v in r.items() if k != "pre_b64"}
                   for r in m.get("rows", [])]
    return out


def cmd_sync(env, ns):
    flags = SyncFlags(to=ns.to, only=ns.only, update=ns.update,
                      newer_only=ns.newer_only, allow_orphan=ns.allow_orphan,
                      include_deleted=tuple(ns.include_deleted or ()),
                      verbatim=ns.verbatim, live=ns.live)
    manifest = plan_sync(env, flags)

    def say(line):
        print(line if ns.verbose else redact(env, line))

    # Ordering, which the human report and the JSON dump have no reason to
    # share: the human report prints BOTH ENDPOINTS FIRST, before anything
    # happens (spec s5 - a recognisable destination is a safety feature, and
    # a run that dies inside run_sync must still leave a record of which two
    # accounts were involved rather than a bare "refused: <msg>"). --json
    # instead has to execute first, because "sync --apply --json" - exactly
    # the combination automation would use - must report what actually
    # happened, not the plan it would have run.
    if not ns.json:
        _print_sync_report(say, manifest)
    elif "live_override" in manifest:
        # --json prints no report and (with --apply) executes first, so the
        # override would otherwise mutate with no pre-mutation notice at
        # all. Shout it on stderr - stdout stays pure JSON for the machine
        # reader, which gets the live_override key in the manifest instead.
        for line in _live_override_lines(manifest):
            print(line if ns.verbose else redact(env, line), file=sys.stderr)

    # A zero-row plan skips run_sync regardless of --apply: there's nothing
    # to journal, and journaling an empty op anyway was a parked finding.
    final = None
    if manifest["rows"] and ns.apply:
        final = run_sync(env, manifest)

    if ns.json:
        if final is not None:
            manifest["result"] = final
        print(json.dumps(_public_manifest(manifest), indent=1))
        return 0 if final in (None, "completed") else 1

    if not manifest["rows"]:
        say("\nnothing to copy")
        return 0
    if final is None:
        say("\ndry run - pass --apply to copy")
        return 0

    # "copied: N" reads r["written"], which run_sync's execute loop set on
    # the row dicts THIS manifest still holds: new_op shallow-copies the
    # manifest, so the "rows" list and every row dict inside it are shared
    # between the caller's manifest and the journaled one. That coupling is
    # load-bearing here and easy to break by deep-copying "for safety".
    # Split the count: a refreshed row was not "copied" in the sense the rest
    # of this report uses, and folding the two together hid the overwrites in
    # a number the user reads as "rows added".
    done = [r for r in manifest["rows"] if r.get("written")]
    n_upd = sum(1 for r in done if r.get("is_update"))
    say("\ncopied     : {0}".format(len(done) - n_upd))
    if n_upd:
        say("refreshed  : {0}".format(n_upd))
    say("result     : {0}".format(final))
    d = _live_override_derived(manifest)
    if d is not None:
        _, _, asserted_uuid, stale_file, stale_uuid = d
        say("live-account override used: --live asserted {0}; {1} ({2}) was "
            "overridden.".format(asserted_uuid[:8], stale_file, stale_uuid[:8]))
    say("Sign into {0} (or restart the app) to see them."
        .format(manifest["dest_email"] or "the other account"))
    return 0 if final == "completed" else 1


def _live_override_derived(manifest):
    """(oauth_uuid, config_uuid, asserted, stale_file, stale_uuid) from a
    manifest's live_override, or None.

    Derived from the operative fields (pair + account) at print time, NEVER
    read from overrode_file/overrode_uuid - those exist for the journal
    reader, and deriving here means no manifest edit can make any
    human-facing line name the wrong file while the certification story
    still looks plausible. Shape-hardened: garbage yields None, and the
    caller prints nothing rather than something wrong."""
    ov = manifest.get("live_override")
    if not isinstance(ov, dict):
        return None
    pair = ov.get("pair")
    asserted = ov.get("account")
    if not (isinstance(pair, (list, tuple)) and len(pair) == 2
            and all(isinstance(u, str) and u for u in pair)
            and isinstance(asserted, str) and asserted in pair):
        return None
    oauth_uuid, config_uuid = pair
    if asserted == config_uuid:
        stale_file, stale_uuid = "~/.claude.json", oauth_uuid
    else:
        stale_file, stale_uuid = "config.json", config_uuid
    return oauth_uuid, config_uuid, asserted, stale_file, stale_uuid


def _live_override_lines(manifest):
    """The banner _print_sync_report and cmd_sync's --json stderr path share.
    Unmissable on purpose: the override is the one place this tool acts on a
    user's word instead of file evidence, and the output must testify to
    that (RULING 5)."""
    d = _live_override_derived(manifest)
    if d is None:
        return []
    oauth_uuid, config_uuid, asserted, stale_file, stale_uuid = d
    return [
        "      !! LIVE-ACCOUNT OVERRIDE: ~/.claude.json ({0}) and config.json "
        "({1})".format(oauth_uuid[:8], config_uuid[:8]),
        "      !! disagree about the signed-in account. Proceeding on your --live",
        "      !! assertion that {0} is what the desktop app is signed "
        "into,".format(asserted[:8]),
        "      !! overriding {0} ({1}). If that is wrong, this sync would "
        "write".format(stale_file, stale_uuid[:8]),
        "      !! into the store of the account you actually use - stop now.",
    ]


def _live_override_note(m):
    """One pre-mutation line for the undo/recover routes over a --live sync
    op. Every command that can mutate under the certification says so BEFORE
    it mutates - undo --apply skips the preview entirely, so warning there
    after deletion would be too late (RULING 5)."""
    if m.get("op_type") != "sync":
        return ""
    d = _live_override_derived(m)
    if d is None:
        return ""
    _, _, asserted, stale_file, stale_uuid = d
    return ("note: op {0} ran under a --live assertion that {1} is the "
            "desktop-live account (overriding {2} {3}); this relies on that "
            "certification, revalidated against the identity files before "
            "anything is touched.".format(m.get("op_id", "?"), asserted[:8],
                                          stale_file, stale_uuid[:8]))


def _print_sync_report(say, manifest):
    """The human-readable plan: both endpoints, then the skip tally, then
    what --include-deleted is resurrecting, then what would be copied."""
    # Spec s5: name both endpoints, with emails, before doing anything.
    # dest_email is "" for every non-live account - the dormant account's
    # email isn't recorded anywhere on disk, so an unlabelled run always hits
    # this, not just an edge case - so also print the store path and org
    # prefix, through the same say()/redact() convention (redacted unless
    # --verbose), giving a cautious user a physical folder to recognise
    # instead of eight hex characters. Symmetric for the source.
    # A source resolved from config.json must never print the same
    # "(email unknown)" an ordinary dormant-side line prints - that would
    # look identical to the normal case and hide how the account was
    # identified. Say where it came from; since RULING 4 that provenance is
    # a note for the user, not a gate - --apply's guard applies the same way
    # regardless of resolved_from (see _guard_mutation).
    weak = manifest.get("source_resolved_from") == "config"
    asserted = (manifest.get("source_resolved_from") == "user"
                and _live_override_derived(manifest) is not None)
    say("from  {0:24} ({1}/{2})   signed in{3}".format(
        "(from config.json)" if weak else
        (manifest["source_email"] or "(email unknown)"),
        manifest["source_account"][:8], manifest["source_org"][:8],
        " (YOUR --live assertion)" if asserted else ""))
    say("      " + manifest["source_path"])
    if weak:
        say("      ! identified from config.json's lastKnownAccountUuid, not from a")
        say("        signed-in oauthAccount - a provenance note only, not a stronger/")
        say("        weaker distinction.")
    if asserted:
        for line in _live_override_lines(manifest):
            say(line)
    # Minor 5: this used to sit inside `if weak:` above, so an ordinary
    # oauth-resolved dry run never warned that --apply refuses while Claude
    # is running - even though _guard_mutation (RULING 4) applies exactly
    # the same way regardless of resolved_from. Every dry run prints it now;
    # the weak-only provenance note above stays weak-only.
    say("      --apply will refuse while Claude is running either way (RULING 4).")
    say("to    {0:24} ({1}/{2})   signed out".format(
        manifest["dest_email"] or "(email unknown)",
        manifest["dest_account"][:8], manifest["dest_org"][:8]))
    say("      " + manifest["dest_path"])
    # A remembered email is labelled rather than passed off as freshly observed:
    # it says what was true when that account was last signed in, which is enough
    # to RECOGNISE a destination but is not evidence about right now. The path
    # above remains the identifier to check.
    src = manifest.get("dest_email_source") or ""
    if src.startswith("memo"):
        _, _, seen = src.partition(":")
        say("      (email remembered from when this account was last signed in{0}"
            " - the path above is what identifies it)".format(
                ", " + seen if seen else ""))
    say("")

    tally = manifest["tally"]
    LABELS = [("present", "already in the destination"),
              ("no_transcript", "skipped, transcript gone"),
              ("deleted", "skipped, deleted in the destination"),
              ("unreadable", "skipped, unreadable row"),
              ("filtered", "skipped, did not match --only"),
              ("unchanged", "already identical, nothing to refresh"),
              ("held_older", "held back, their copy is NEWER (--newer-only)"),
              ("held_same", "held back, same age - nothing newer to send"),
              ("held_orphan", "held back, would HIDE a conversation"),
              ("swapping", "refreshes that change WHICH conversation opens"),
              ("held_unknown", "held back, could not tell which is newer")]
    for key, label in LABELS:
        items = tally.get(key) or []
        if items:
            say("{0:36}: {1}".format(label, len(items)))
    # Tombstone skips are named individually: the user deleted these on
    # purpose and should see the deletion was honoured, not silently
    # dropped. Capped the same way as the "to copy" list below - a source
    # account with many deliberate deletions must not produce unbounded
    # output.
    deleted_titles = tally.get("deleted") or []
    for title in deleted_titles[:15]:
        say("   kept deleted: {0}".format(title))
    if len(deleted_titles) > 15:
        say("   ... and {0} more".format(len(deleted_titles) - 15))

    # Name what --newer-only declined to send. A count alone would leave the
    # user unable to tell a correct hold-back from a wrong one, and the whole
    # point of the flag is that they no longer have to eyeball every row.
    for key, label in (("held_older", "not sent, their copy is newer"),
                       ("held_unknown", "not sent, direction unknown")):
        held = tally.get(key) or []
        for title in held[:15]:
            say("   {0}: {1}".format(label, title))
        if len(held) > 15:
            say("   ... and {0} more".format(len(held) - 15))

    # Held-back swaps get the measurement, not just the name. Without it every
    # line reads the same and the only way to judge one is to go and open both
    # conversations - which is the position this whole feature exists to spare
    # the user. Sorted so the ones that would really lose something come first.
    detail = list(tally.get("held_orphan_detail") or [])
    if detail:
        say("")
        say("!! NOT SENT - each of these would open a DIFFERENT conversation, and the "
            "one it opens")
        say("   now was not confirmed reachable from any other surviving row. Pass "
            "--allow-orphan (window:")
        say("   \"allow hiding a conversation\") to send them.")
        # Unmeasured first, then lowest overlap first. `None` sorts ahead of every
        # number because "we could not look" is the least reassuring answer here,
        # not because it is the largest loss - the same fail-closed posture the
        # rest of this module runs on. (The earlier comment claimed this ordered
        # by how much would be lost, which is not what the key does.)
        detail.sort(key=lambda d: (d.get("overlap") is not None,
                                   d.get("overlap") if d.get("overlap") is not None else 0))
        for d in detail[:15]:
            say("   !! {0}".format(d["title"]))
            # Per row, because the two states are not the same claim.
            say("        {0}".format(
                "nothing else points at the conversation it opens now"
                if d.get("orphan") is True else
                "a store could not be read, so whether anything else points at "
                "the conversation it opens now is UNKNOWN"))
            length = _length_clause(d.get("displaced_turns"), d.get("incoming_turns"))
            say("        " + (length or "turn counts unknown - length change "
                                        "could not be measured"))
            say("        " + _overlap_clause(d.get("overlap")))
        if len(detail) > 15:
            say("   ... and {0} more".format(len(detail) - 15))
        say("")
    if manifest.get("newer_only") and not manifest.get("update"):
        say("   (--newer-only had nothing to act on: it narrows --update, "
            "which was not given)")

    # --include-deleted is the one thing this command does that the user
    # cannot undo by simply deleting a row again - it brings back a session
    # they deliberately deleted, the first row of the design's own risk
    # table. It used to be the LEAST visible thing here: the rescued row
    # entered the plan with no marker and tally["deleted"] held only the
    # skips, so the report said nothing at all. Name every resurrection,
    # under an unmissable label, BEFORE the ordinary "to copy" list.
    resurrected = tally.get("resurrected") or []
    if resurrected:
        say("")
        say("!! RESURRECTING {0} session(s) the destination account DELETED "
            "(--include-deleted):".format(len(resurrected)))
        for title in resurrected[:15]:
            say("   !! {0}".format(title))
        if len(resurrected) > 15:
            say("   ... and {0} more".format(len(resurrected) - 15))
        say("")

    # Refreshes are the only thing this tool does that OVERWRITES rather than
    # adds, so they are listed separately and first, the same treatment
    # --include-deleted gets above. A user must never discover after the fact
    # that "to copy" quietly included rewriting rows that were already there.
    refreshes = [r for r in manifest["rows"] if r.get("is_update")]
    adds = [r for r in manifest["rows"] if not r.get("is_update")]
    if refreshes:
        say("")
        say("!! OVERWRITING {0} row(s) that already exist in the destination "
            "(--update):".format(len(refreshes)))
        for r in refreshes[:15]:
            # Name the direction per row. A refresh is not automatically an
            # upgrade: rows are per-account snapshots of one shared transcript,
            # so the dormant copy can be the more recently used one, and
            # overwriting it moves that account BACKWARDS.
            if r.get("regresses"):
                mark = "  <- destination copy is NEWER; this moves it BACKWARDS"
            elif r.get("activity_unknown"):
                mark = "  <- which copy is newer could not be determined"
            else:
                mark = ""
            say("   !! {0}{1}".format(r["title"], mark))
            # A pointer swap is not a metadata refresh and must not read like
            # one: after this write that entry opens a DIFFERENT conversation.
            if r.get("swaps_conversation"):
                orph = r.get("displaced_orphan")
                # "another account" was accurate only while reachability was
                # measured per STORE. Since 0.9.13 it is per row, so the voucher
                # may be a different row in this same destination account -
                # saying "account" would point the user at the wrong sidebar.
                fate = ("and NOTHING else points at it - it becomes unreachable "
                        "from every sidebar" if orph is True else
                        "and it could not be confirmed reachable elsewhere"
                        if orph == "unknown" else
                        "another surviving row still opens it")
                say("      ^ opens a DIFFERENT conversation afterwards; the one it "
                    "opens now ({0}) {1}".format(
                        (r.get("displaced_session") or "?")[:8], fate))
                # Length first: it is the fact a reader can act on without
                # interpreting anything, and the one a percentage cannot carry.
                # Always a line, never silence - a reader trained to look for
                # "FEWER" would read a missing line as "no loss", when it
                # actually means the two sides could not be counted.
                length = _length_clause(r.get("displaced_turns"),
                                        r.get("incoming_turns"))
                say("        " + (length or "turn counts unknown - length "
                                            "change could not be measured"))
                say("        " + _overlap_clause(r.get("displaced_overlap")))
        if len(refreshes) > 15:
            say("   ... and {0} more".format(len(refreshes) - 15))
        # What a refresh actually replaces. It is not a field-level patch of
        # the title and timestamp: the whole row is rewritten from the source's
        # transformed bytes, so anything the default transform strips or resets
        # is dropped from the DESTINATION's copy too. Say so before doing it -
        # the per-row `removed`/`reset` lists are already computed.
        # Measured against each destination row's OWN pre-image, so this names
        # what that account actually loses - including fields it had and the
        # source never did, which the source-side lists could not see.
        dropped = sorted({k for r in refreshes for k in (r.get("dest_dropped") or [])})
        reset = sorted({k for r in refreshes for k in (r.get("dest_reset") or [])})
        unknown = sum(1 for r in refreshes if r.get("dest_dropped") is None)
        say("   each refresh replaces the WHOLE row with the source's copy, not just")
        say("   its title and last-activity time.")
        if dropped:
            say("   fields the destination's row loses: {0}".format(", ".join(dropped)))
        if reset:
            say("   fields reset over that account's own setting: {0}".format(", ".join(reset)))
        if unknown:
            say("   {0} row(s) could not be compared field by field - the whole row "
                "is still replaced.".format(unknown))
        say("   undo restores the exact bytes replaced; a row the destination has")
        say("   changed since planning is refused, never overwritten.")
        say("   that undo lasts only while this op is in the journal - the ten most")
        say("   recent finished ops are kept, older ones are pruned with their images.")
        say("")

    say("{0:36}: {1}".format("to copy", len(adds)))
    for r in adds[:15]:
        say("   {0}{1}".format("!! " if r.get("overrode_tombstone") else "", r["title"]))
    if len(adds) > 15:
        say("   ... and {0} more".format(len(adds) - 15))


# ------------------------------------------------------------- 9. converge
# "Every conversation some sidebar can open should be openable from every
# sidebar." Purely additive: for every (eligible conversation, destination
# account) pair where the account holds no row, create one. Never repoints,
# never refreshes, never deletes, never resurrects - the whole safety case is
# having no overwrite path at all. Design: docs/specs/2026-08-26-converge-design.md.


@dataclasses.dataclass
class ConvergeFlags:
    """Narrowing and identity assertion only. No --to, no --from, no --store:
    naming a direction is the interface converge exists to delete - the target
    set is derived."""
    only: str = ""          # title substring, or a cliSessionId prefix
    live: str = ""          # RULING 5 assertion, as sync and retitle use it


# What the unreadable-row refusal says converge could not decide. The scan is
# _retitle_scan's; the stakes here are eligibility and the collision hold, not
# a rename.
_CONVERGE_SCAN_WHY = ("which conversations that account already holds, or "
                      "which titles its sidebar carries - both the target set "
                      "and the collision hold depend on having looked")

# 3b's one line. In non-JSON output it is printed (prefixed "note: "); under
# --json the same sentence rides the manifest's `notes` array so stdout stays
# pure JSON. It travels as a plain string among the dict-shaped title notes -
# _print_converge_report branches on the type.
_CORROBORATED_NOTE = ("--live names the account the identity files already "
                      "agree on; no arbitration was needed.")


def _converge_destinations(env, records):
    """({account: {account, org, path, label, rows}}, non_destinations).

    StoreKey (spec, "Keys, eligibility, and the target set"): an account's
    destination store is the org directory that already holds that account's
    rows - the same evidence sync's candidate listing uses. Measured on the
    machine this was designed on: each account has three org directories and
    exactly one is populated. An account whose every org directory is empty is
    NOT a destination - there is no evidence which org is real, and writing
    into a guessed one is the exact move new-row --apply refuses - and the
    plan says so rather than silently shrinking "every sidebar".

    An account with rows in MORE than one org directory (two store roots after
    an installer migration can do this) fails the populated-one rule outright:
    refusing to guess beats writing into one of two stores that both claim to
    be real. Measured reality is exactly-one-populated, so this refusal should
    never fire outside a layout worth a human's attention.
    """
    populated = {}
    for rec in records:
        populated.setdefault(rec.account, {})[
            os.path.normcase(os.path.realpath(rec.store))] = rec
    dests = {}
    counts = {}
    for rec in records:
        counts[rec.account] = counts.get(rec.account, 0) + 1
    for acct in sorted(populated):
        stores = populated[acct]
        if len(stores) > 1:
            raise Refusal(
                "account {0} holds rows in more than one org directory, so the "
                "populated-one rule cannot resolve which store is its real "
                "sidebar; refusing to guess. Nothing was written:\n{1}".format(
                    acct[:8], "\n".join(
                        "   " + rec.store for rec in stores.values())))
        rec = next(iter(stores.values()))
        dests[acct] = {"account": acct, "org": rec.org, "path": rec.store,
                       "label": rec.label, "rows": counts.get(acct, 0)}
    non_dest = []
    for acct, org, path in _account_dirs(env):
        if acct in dests or any(nd["account"] == acct for nd in non_dest):
            continue
        non_dest.append({"account": acct,
                         "label": _email_of(env, acct) or acct[:8]})
    return dests, non_dest


def _retitle_command(sid, title):
    """The pastable remedy every hold and disagreement note prints, by name
    (spec, "Interactions"). An 8-char id prefix on purpose: --only takes a
    prefix, and the full uuid would be shortened by redact() into something
    that cannot be pasted back."""
    return ('claude-code-sessions retitle --only {0} --title "{1}" '
            '--apply'.format(sid[:8], title))


# The generated remedy title's suffix grammar (hold-remedies design, §3).
# Fixed English month abbreviations, NOT strftime's %b: the suffix-replacement
# rule below matches this grammar byte-exactly to decide tool provenance, and
# a locale-dependent rendering would make a title generated on one machine a
# "lookalike" on the next.
_LEG_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_LEG_RANGE_PAT = (
    r"(?:{m} {d}(?:-{d})?"                                # Aug 28 / Aug 24-28
    r"|{m} {d} - {m} {d}"                                 # Aug 24 - Sep 2
    r"|\d{{4}}-\d{{2}}-\d{{2}} - \d{{4}}-\d{{2}}-\d{{2}})"  # across years
).format(m="(?:" + "|".join(_LEG_MONTHS) + ")",
         d=r"(?:[1-9]|[12]\d|3[01])")
# The EXACT generated grammar - matching it is treated as tool provenance, so
# the range (the only part rewritten) is refreshed. A tail that merely
# resembles it - different wording, spacing or a malformed range - is a
# human's prose, and rewriting a person's words is not this feature's job.
_LEG_SUFFIX_RE = re.compile(r" - earlier leg \(" + _LEG_RANGE_PAT + r"\)$")
_LEG_SUFFIX_LOOSE_RE = re.compile(r"(?i)- ?earlier leg ?\(.*\)\s*$")


def _leg_day(ms):
    """One date as the sidebar convention renders it - 'Aug 28', local time.
    The dates are a label for a human, not evidence; local time matches every
    date the person has ever seen in their sidebar."""
    t = time.localtime(ms / 1000.0)
    return "{0} {1}".format(_LEG_MONTHS[t.tm_mon - 1], t.tm_mday)


def _leg_range(start_ms, end_ms):
    """The generated title's date range, in exactly four formats (§3):
    'Aug 28', 'Aug 24-28', 'Aug 24 - Sep 2', '2026-12-30 - 2027-01-02'."""
    s = time.localtime(start_ms / 1000.0)
    e = time.localtime(end_ms / 1000.0)
    if (s.tm_year, s.tm_mon, s.tm_mday) == (e.tm_year, e.tm_mon, e.tm_mday):
        return _leg_day(start_ms)
    if s.tm_year != e.tm_year:
        return "{0:04d}-{1:02d}-{2:02d} - {3:04d}-{4:02d}-{5:02d}".format(
            s.tm_year, s.tm_mon, s.tm_mday, e.tm_year, e.tm_mon, e.tm_mday)
    if s.tm_mon == e.tm_mon:
        return "{0} {1}-{2}".format(_LEG_MONTHS[s.tm_mon - 1], s.tm_mday,
                                    e.tm_mday)
    return "{0} - {1}".format(_leg_day(start_ms), _leg_day(end_ms))


def _superseded_leg_title(base, rows):
    """(generated title, None) for a superseded leg, or (None, degrade reason).

    '<base> - earlier leg (<range>)', where the range spans the leg's rows -
    min(createdAt) to max(lastActivityAt) - because the rows are what the
    remedy renames and their dates are what the person's sidebar shows. BASE
    is the colliding title; a base already wearing the exact generated suffix
    (a second fork of an already-renamed leg) gets its range refreshed, and a
    lookalike tail degrades instead - see _LEG_SUFFIX_RE. ROWS are the leg's
    parsed row dicts; callers guarantee usable dates (classification already
    refused 'row dates unusable' pairs), and the per-pair exception wrap owns
    whatever slips past that guarantee."""
    base = base.strip()
    rng = _leg_range(min(r.get("createdAt") for r in rows),
                     max(r.get("lastActivityAt") for r in rows))
    if _LEG_SUFFIX_RE.search(base):
        base = _LEG_SUFFIX_RE.sub("", base)
    elif _LEG_SUFFIX_LOOSE_RE.search(base):
        return None, "title already carries a leg suffix"
    return "{0} - earlier leg ({1})".format(base, rng), None


def _converge_title(recs, facts):
    """(title, title_source, disagreement, holder_titles) for one conversation.

    The holder whose row has the greatest `lastActivityAt` names it - that
    field is a snapshot of the conversation's own activity, not of any
    account's attention (`lastFocusedAt` is, and is not used). Missing values
    compare as zero; an exact tie breaks to the lexicographically greatest
    account uuid - arbitrary, deterministic, stated - and then the row
    filename, for the duplicate-rows-in-one-account case the account rule
    cannot split. The stored value is the TRIMMED title (title_key's form),
    retitle's lesson: comparing trimmed while storing raw is two titles
    pretending to be one.

    titleSource travels with the title it qualifies: a holder row saying
    "user" records that a person chose that name for this conversation, and
    dropping the pin would invite the app to resummarise the new row back
    into the drift the disagreement note exists to surface. It is a claim
    about the title's authorship, not per-account state.

    Rows with no usable title do not compete. If NO holder carries one, fall
    back to new-row's own derivation (the transcript's customTitle, else the
    placeholder that does not impersonate a summary).
    """
    titled = [rec for rec in recs if title_key(rec.data.get("title"))]
    if not titled:
        title, _provenance, source = _new_row_title("", facts)
        return title, source, False, []
    best = max(titled, key=lambda rec: (_activity_of(rec.data) or 0,
                                        rec.account, rec.name))
    title = title_key(best.data.get("title"))
    source = "user" if best.data.get("titleSource") == "user" else "auto"
    distinct = {title_key(rec.data.get("title")) for rec in titled}
    holder_titles = [{"label": rec.label, "title": rec.data.get("title") or ""}
                     for rec in sorted(titled, key=lambda rec: (rec.store,
                                                                rec.name))]
    return title, source, len(distinct) > 1, holder_titles


def plan_converge(env, flags):
    """Build a converge manifest. Pure planning - writes nothing.

    The keys, stated because the review found them implicitly assumed:
    ConversationKey is the cliSessionId (several rows in one account are ONE
    holding); transcripts are machine-global, so "transcript exists" is a
    property of the conversation, never of a holder; a conversation is
    ELIGIBLE iff its transcript is on disk AND at least one account holds a
    row for it. Dead-only groups (rows everywhere, transcript gone) are
    excluded from the work and from the completeness denominator - they are
    doctor's dead-row report, not convergence. Transcripts with no row
    anywhere are equally out: retired stays retired; first rows are new-row's
    job. The target set is every (eligible conversation, destination account)
    pair where that account holds no row for it.

    The tool's premise, stated rather than assumed: every discovered account
    belongs to the operator - already the premise of sync, repoint --store
    and new-row --store; converge adds fan-out, not a new trust boundary. The
    per-destination `email (acct/org)` lines are where an operator with
    accounts that must not mix would see it before --apply.
    """
    # Recorded on the manifest whether or not --live arbitrates it: the
    # RULING 5 state is knowable at plan time, and 0.12.0's dry run reading
    # `0 held` while the apply was always going to refuse was the one gap
    # that cost a full close-the-app cycle to discover. The field is the
    # machine caller's warning (--json carries it); cmd_converge prints the
    # human one. A snapshot only - _converge_recheck keeps evaluating the
    # files fresh under the lock, in both directions.
    dis = _identity_disagreement(env)
    if flags.live:
        # account_scope: converge consumes the account uuid (the recheck's
        # membership test) and the email (the scan's labels) - never a
        # store, so --live here asserts the account-level fact RULING 5
        # actually certifies. See _resolve_live_assertion.
        live = _resolve_live_assertion(env, flags.live, _account_dirs(env),
                                       account_scope=True)
    else:
        live = live_account(env)
    # Strict machine-wide scan - refuses on ANY unreadable row, because both
    # the holdings census and the collision hold depend on having looked.
    records = _retitle_scan(env, live=live, why=_CONVERGE_SCAN_WHY)
    dests, non_dest = _converge_destinations(env, records)

    conversations = {}
    for rec in records:
        sid = rec.data.get("cliSessionId") or ""
        if sid:
            conversations.setdefault(sid, []).append(rec)
    tids = {os.path.splitext(os.path.basename(p))[0]
            for _, p in iter_transcripts(env.projects_root)}

    only_sid = ""
    if flags.only:
        want = flags.only.lower()
        matches = {sid: recs for sid, recs in conversations.items()
                   if sid.lower().startswith(want)
                   or any(want in (rec.data.get("title") or "").lower()
                          for rec in recs)}
        if not matches:
            raise Refusal(
                "no row in any account matches --only {0!r} (a title "
                "substring, or a cliSessionId prefix). A transcript no account "
                "points at is not converge's gap - first rows are 'new-row's "
                "job: claude-code-sessions new-row --to <cliSessionId>."
                .format(flags.only))
        if len(matches) > 1:
            raise Refusal(
                "--only {0!r} matches {1} conversations - expected when the "
                "title itself collides. Re-run with the session id of the one "
                "you mean (a prefix is enough):\n{2}".format(
                    flags.only, len(matches),
                    _retitle_candidate_listing(matches)))
        only_sid = next(iter(matches))
        if only_sid not in tids:
            raise Refusal(
                "the conversation --only names ({0}) has no transcript on "
                "disk - its rows are dead, and a new row for it would open "
                "nothing. Dead rows are 'doctor's report, not convergence. "
                "Nothing was written.".format(only_sid[:8]))

    eligible = {sid: recs for sid, recs in conversations.items()
                if sid in tids}
    dead_excluded = len(conversations) - len(eligible)
    if only_sid:
        eligible = {only_sid: eligible[only_sid]}

    # Holdings are per ACCOUNT, matching alignment's `complete` - a row in any
    # of the account's org directories means the account reaches it, so a pair
    # is only missing when no row anywhere under that account opens the
    # conversation. That is what makes "complete rises by exactly the applied
    # count" arithmetic rather than aspiration.
    short, complete_now = [], 0
    for sid in sorted(eligible):
        held_by = {rec.account for rec in eligible[sid]}
        missing = [a for a in sorted(dests) if a not in held_by]
        if missing:
            short.append((sid, missing))
        else:
            complete_now += 1

    # The existing-title census for the collision hold, per account (the same
    # scope alignment's distinguishable uses), keyed by title_key - THE shared
    # comparator.
    acct_titles = {}
    for rec in records:
        k = title_key(rec.data.get("title"))
        if k:
            acct_titles.setdefault(rec.account, {}).setdefault(
                k, (rec.data.get("cliSessionId") or "", rec.name))

    rows, holds, notes = [], [], []
    planned_titles = {}          # account -> {title_key: sid this plan places}
    for sid, missing in short:
        recs = eligible[sid]
        holder_labels = sorted({rec.label for rec in recs})
        try:
            facts = _transcript_facts(env, sid)
        except Refusal as exc:
            # Not fatal to the rest (spec, "Holds"): a transcript that exists
            # but cannot populate a row - duplicated across project folders,
            # missing a cwd or a model, being written right now - holds this
            # conversation's pairs with the reason attached, and every other
            # pair proceeds. Refusing the whole bulk run over one such
            # conversation would leave the monthly chore permanently stuck on
            # its weirdest member.
            for acct in missing:
                holds.append({"session": sid, "account": acct,
                              "label": dests[acct]["label"], "title": "",
                              "reason": "held_transcript_unusable",
                              "detail": str(exc), "retitle": ""})
            continue
        title, title_source, disagree, holder_titles = _converge_title(recs,
                                                                       facts)
        if disagree:
            notes.append({"session": sid, "title": title,
                          "holder_titles": holder_titles,
                          "retitle": _retitle_command(sid, title)})
        k = title_key(title)
        for acct in missing:
            d = dests[acct]
            hit = acct_titles.get(acct, {}).get(k)
            if hit is not None and hit[0] != sid:
                # held_title_collision: the chosen title already names a
                # DIFFERENT conversation in that sidebar. new-row warns and
                # proceeds on this; converge holds, because converge is bulk
                # and unattended where new-row is single and watched.
                # Deliberately NO auto-suffix - a store full of generic titles
                # mass-holding is converge refusing to spread a mess, and the
                # holds arrive with their fixes attached. A non-colliding
                # minority title is NOT substituted either: canonical
                # consistency outranks maximising placements.
                holds.append({
                    "session": sid, "account": acct, "label": d["label"],
                    "title": title, "reason": "held_title_collision",
                    "detail": "{0!r} already names a different conversation "
                              "in that sidebar: row {1} (opens {2})".format(
                                  title, hit[1], (hit[0] or "?")[:8]),
                    "retitle": (_retitle_command(hit[0], "<new title>")
                                if hit[0] else "")})
                continue
            planned = planned_titles.get(acct, {}).get(k)
            if planned is not None and planned != sid:
                # Two eligible conversations converging on one destination
                # under one chosen title. Writing both recreates the duplicate
                # this store spent a cleanup removing, so the later one (sid
                # order - deterministic) holds; at apply time the same state
                # is what the per-pair re-check would produce, so the plan
                # says it now rather than surprising the applier.
                holds.append({
                    "session": sid, "account": acct, "label": d["label"],
                    "title": title, "reason": "held_title_collision",
                    "detail": "this plan already creates a row named {0!r} "
                              "there, for conversation {1}".format(
                                  title, planned[:8]),
                    "retitle": _retitle_command(sid, "<new title>")})
                continue
            # The uuid is minted at plan time (spec, "Applying") so the
            # journalled op can list the complete bytes to write, and so
            # landed-versus-unattempted is decidable from the record alone.
            row_uuid = str(uuid.uuid4())
            row = _synthesize_row(sid, title, title_source, facts, row_uuid)
            name = "local_{0}.json".format(row_uuid)
            post = json.dumps(row, separators=(",", ":")).encode("utf-8")
            rows.append({"name": name,
                         "dest_path": os.path.join(d["path"], name),
                         "store_path": d["path"], "account": acct,
                         "org": d["org"], "label": d["label"],
                         "session": sid, "title": title,
                         "title_source": title_source,
                         "holders": holder_labels,
                         "pre_b64": None, "post_b64": b64(post),
                         "is_update": False, "written": False})
            planned_titles.setdefault(acct, {})[k] = sid

    # Truthful completeness math: projected from the pairs that will actually
    # be written, never a promised full house. A short conversation with any
    # held pair stays short.
    held_sids = {h["session"] for h in holds}
    total = len(eligible)
    after = complete_now + sum(1 for sid, _m in short if sid not in held_sids)
    held_conversations = sum(1 for sid, _m in short if sid in held_sids)

    if flags.live and live and live.resolved_from == "corroborated":
        notes.insert(0, _CORROBORATED_NOTE)
    m = {"op_type": "converge",
         # A corroborated --live records NO assertion: nothing was
         # arbitrated, the flag was redundant, and an unearned uuid here
         # would let the recheck treat agreement-time corroboration as a
         # disagreement-time certification (3b).
         "live_asserted": (live.account_uuid
                           if flags.live and live
                           and live.resolved_from == "user" else ""),
         "only": flags.only, "only_session": only_sid,
         "destinations": [dests[a] for a in sorted(dests)],
         "non_destinations": non_dest,
         "complete": {"now": complete_now, "of": total, "after": after,
                      "held": held_conversations,
                      "scoped": bool(only_sid)},
         "dead_excluded": dead_excluded,
         "notes": notes, "holds": holds, "rows": rows}
    if dis:
        m["identity_disagreement"] = {"oauth": dis[0], "config": dis[1]}
    return m


def _converge_shape_error(m):
    """A sentence naming what is wrong with this converge manifest, or None.

    Same job as _retitle_shape_error, for the additive multi-row shape:
    recover and undo dereference rows straight out of a journal file a user
    can edit. pre_b64 is deliberately NOT required - converge rows are adds,
    and their pre-image is uniformly absent. NEVER RAISES (classify_op's
    contract).
    """
    rows = m.get("rows")
    if not isinstance(rows, list) or not rows:
        return "its 'rows' is not a non-empty list"
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            return "row {0} is not an object".format(i)
        for k in ("name", "dest_path", "store_path", "post_b64", "account",
                  "session", "title"):
            if not isinstance(r.get(k), str) or not r[k]:
                return "row {0} has no usable {1!r}".format(i, k)
    return None


def _converge_recheck(env, m):
    """Every apply-time guard and per-pair revalidation, under the operation
    lock, BEFORE the op record exists. Raises Refusal; MARKS apply-time skips
    on the manifest's rows (`skipped` + `skip_detail`) rather than returning
    anything.

    Runs pre-journal for the same reason run_retitle's recheck does, with the
    spec's own sharpening: a zero-write apply - store already complete,
    everything held or already present - must create NO op at all, and only a
    recheck that precedes new_op can know that in time. Nothing can change
    between this pass and the writes: the lock is held and RULING 4 has
    already established the app is closed, so the write loop's own checks are
    against I/O surprises, not concurrent editors.

    Per pair, against the store as it now is: a row that appeared since - the
    app, a sync, another converge - makes that pair an `already_present` skip,
    not an error; a transcript that left the disk makes it `transcript_gone`;
    and the collision hold re-runs, so a collision that appeared since plan
    time holds now. A pair that held at PLAN time is never written even if
    the collision has since cleared - it is not in `rows` at all; clearing it
    changed the sidebar, so the user replans rather than the tool guessing.
    """
    # RULING 5. Converge writes every account's store, so there is no dormant
    # side to protect - but a machine whose identity files disagree is a
    # machine in a state this module never mutates under silently. --live
    # (validated at plan time by _resolve_live_assertion) arbitrates it.
    # The remedies are ordered by what can actually work (change 4,
    # 0.13.0): --live works in every disagreement; /login rewrites only
    # ~/.claude.json, so re-auth clears this only when the CLI side is the
    # stale one - and the one re-auth that would force "agreement" when
    # config.json is stuck is signing the CLI into the STALE account, the
    # false agreement a liveness guard must not invite, which is why the
    # old "so the two agree" promise is gone. The desktop switch says "may
    # not", not "cannot": config.json has been measured both ways.
    dis = _identity_disagreement(env)
    if dis and (m.get("live_asserted") or "") not in dis:
        raise Refusal(
            "~/.claude.json ({0}) and config.json ({1}) disagree about the "
            "signed-in account (RULING 5). The designed remedy is to "
            "assert which account the desktop app is on and re-plan:\n"
            "   claude-code-sessions converge --live <email, or acct/org "
            "as reports print it> --apply\n"
            "If it is the CLI's record that is stale, re-authenticating "
            "the CLI as the desktop's account (run 'claude', then /login) "
            "also clears this. Switching the desktop app may not - "
            "config.json has been measured both tracking a switch "
            "(2026-08-02) and keeping the previous account across one "
            "(2026-08-29) - and re-authenticating never writes it. --live "
            "exists for exactly that case. Nothing was written."
            .format(dis[0][:8], dis[1][:8]))
    # The plan's stores are re-discovered and must still resolve to the same
    # populated-one answer - a store that moved since planning is a replan,
    # never a guess.
    records = _retitle_scan(env, why=_CONVERGE_SCAN_WHY)
    dests, _non = _converge_destinations(env, records)
    for r in m["rows"]:
        d = dests.get(r.get("account") or "")
        if d is None or (os.path.normcase(os.path.realpath(d["path"]))
                         != os.path.normcase(os.path.realpath(
                             r["store_path"]))):
            raise Refusal(
                "the destination store this plan recorded for {0} no longer "
                "resolves ({1}) - the store layout moved since planning. "
                "Nothing was written; re-run to replan.".format(
                    r.get("label") or (r.get("account") or "?")[:8],
                    r.get("store_path")))
    acct_sids, acct_titles = {}, {}
    for rec in records:
        sid = rec.data.get("cliSessionId") or ""
        if sid:
            acct_sids.setdefault(rec.account, set()).add(sid)
        k = title_key(rec.data.get("title"))
        if k:
            acct_titles.setdefault(rec.account, {}).setdefault(
                k, (rec.data.get("cliSessionId") or "", rec.name))
    for r in m["rows"]:
        if r.get("written") or r.get("skipped"):
            continue        # landed rows are settled; skips stay skipped
        acct = r["account"]
        if r["session"] in acct_sids.get(acct, set()):
            r["skipped"] = "already_present"
            r["skip_detail"] = ("a row for it appeared in {0} since this was "
                               "planned".format(r.get("label") or acct[:8]))
            continue
        if not find_transcripts(env.projects_root, r["session"]):
            r["skipped"] = "transcript_gone"
            r["skip_detail"] = ("its transcript left the disk since this was "
                               "planned, so the row would open nothing")
            continue
        k = title_key(r["title"])
        hit = acct_titles.get(acct, {}).get(k)
        if hit is not None and hit[0] != r["session"]:
            r["skipped"] = "held_title_collision"
            r["skip_detail"] = ("{0!r} now names a different conversation "
                               "there: row {1} (opens {2})".format(
                                   r["title"], hit[1], (hit[0] or "?")[:8]))
            continue
        # This pair will be written; later pairs in this same run must see it
        # - the sequential-write semantics, decided here where the whole run
        # is visible.
        acct_sids.setdefault(acct, set()).add(r["session"])
        if k:
            acct_titles.setdefault(acct, {}).setdefault(k, (r["session"],
                                                            r["name"]))


def _converge_write_rows(op, m, roots):
    """execute_converge_op's write loop. Per pair: containment, a disk check
    at the minted path, atomic write. The minted filename is a fresh uuid4,
    so anything already sitting at it is either this op's own landed write
    (crash re-entry - finish the bookkeeping) or evidence of something wrong
    enough to stop for; there is no legitimate third party."""
    rows = m["rows"]
    for i, r in enumerate(rows):
        if r.get("written") or r.get("skipped"):
            continue
        real = ensure_contained(r["dest_path"], roots)
        if os.path.dirname(real) != os.path.realpath(r["store_path"]):
            raise LayoutError("row {0!r} is not a direct child of {1!r}; "
                              "refusing".format(r["dest_path"],
                                                r["store_path"]))
        state = _sync_row_drift(r)
        if state == "match":
            r["written"] = True          # landed before a crash; bookkeeping
            save_manifest(op)
            continue
        if state == "unreadable":
            raise Refusal(
                "something is at {0!r} but it could not be read, so whether "
                "it is the row this op wrote cannot be settled. Refusing "
                "rather than overwrite a file nobody can see. The op is left "
                "at 'writing' - 'recover --resolve {1} --back --apply' "
                "removes what this op verifiably wrote and closes it."
                .format(r["name"], m.get("op_id", "")))
        if state != "absent":
            raise Refusal(
                "a different row already exists at {0!r}; refusing to "
                "overwrite it - this command only adds. The op is left at "
                "'writing' - 'recover --resolve {1} --back --apply' removes "
                "what this op verifiably wrote and closes it."
                .format(r["name"], m.get("op_id", "")))
        post = unb64(r["post_b64"])      # cannot raise: 'unreadable' covers it
        try:
            atomic_write(r["dest_path"], post)
        except OSError as exc:
            raise Refusal(
                "could not write row {0!r}: {1}. The op is left at 'writing' "
                "- re-run 'recover --resolve {2} --forward --apply' once the "
                "store is writable, or '--back' to remove what did land."
                .format(r["name"], exc, m.get("op_id", "")))
        _maybe_crash("converge-write-before-save")
        r["written"] = True
        save_manifest(op)
        if i < len(rows) - 1:
            _maybe_crash("converge-mid-write")


def execute_converge_op(env, op):
    """journaled -> writing -> completed. N rows, all adds.

    By the time this runs, the op record already lists every planned row -
    destination store path, minted filename, the complete bytes to write -
    and was fsynced by new_op, so an interruption anywhere in the loop
    strands nothing: back removes what landed, forward re-evaluates and
    finishes. Both callers (run_converge, recover_op's forward arm) own the
    RULING 4 guard and the recheck - the once-per-apply rule run_new_row's
    refactor established.
    """
    m = op.manifest
    if m.get("status") != "journaled":
        raise LayoutError("execute_converge_op runs ops from 'journaled'; "
                          "use recover for interrupted ops")
    roots = _retitle_roots(env)
    set_status(op, "writing")
    try:
        _converge_write_rows(op, m, roots)
    except BaseException:
        # Journal what actually landed before the failure propagates - the
        # same on-the-way-out save execute_sync_op and execute_retitle_op
        # make, so recover reads an exact record.
        try:
            save_manifest(op)
        except Exception:
            pass
        raise
    _maybe_crash("converge-before-complete")
    set_status(op, "completed")
    return "completed"


def run_converge(env, manifest):
    """Guard, lock, re-check, journal, execute, rotate - and the one shape no
    sibling has: a run whose re-check leaves nothing to write returns
    "unchanged" WITHOUT creating an op. Nothing durable happened, there is
    nothing to undo, and a journal entry would be residue for recover to
    clean up after a command that touched nothing (spec, "Applying" step 5).
    """
    _guard_mutation(env, "create rows in", NEW_ROW_STORE,
                    because=NEW_ROW_GUARD_WHY)
    acquire_lock(env, "converge")
    try:
        _converge_recheck(env, manifest)
        if not any(not r.get("written") and not r.get("skipped")
                   for r in manifest["rows"]):
            return "unchanged"
        try:
            op = new_op(env, manifest)
        except OSError as exc:
            raise Refusal(
                "could not write the operation record ({0}). The journal is "
                "written before any row is touched, so nothing landed and "
                "there is nothing to recover - fix the space or permissions "
                "under {1} and re-run.".format(exc, env.ops_dir))
        manifest["op_id"] = op.manifest["op_id"]
        final = execute_converge_op(env, op)
        rotate_ops(env)
        return final
    finally:
        release_lock(env)


def undo_converge(env, op):
    """Remove the rows this converge created - FORGIVING, not all-or-nothing,
    the deliberate opposite of retitle's rule: retitle's undo restores prior
    states that must agree across accounts, while converge's undo deletes
    independent creations whose prior state is uniformly ABSENT. Per created
    row, in the spec's order:

    - NEVER DELETE THE LAST POINTER. If this row is now the only row in any
      account reaching the conversation (the holders it was copied from have
      since been removed), skip it and say so - undo's job is to remove
      redundant presence, and this presence is no longer redundant.
    - A changed cliSessionId: skip - someone repointed it, and deleting would
      destroy their work.
    - A changed title or titleSource: skip - someone curated it, and a
      curated row is theirs now. (A moved lastFocusedAt alone is not
      curation - the app writes that on focus.)
    - Otherwise delete. Already gone counts as done.

    The report tallies deleted / skipped-by-reason / already-gone, and the op
    is consumed either way. No claimed-elsewhere machinery: every row's bytes
    embed a uuid4 this op minted, so no other operation can account for them
    (the same argument that keeps converge out of sync's claim protocol).
    """
    m = op.manifest
    acquire_lock(env, "undo-" + m["op_id"])
    try:
        if m.get("op_type") != "converge":
            raise Refusal("not a converge op: " + str(m.get("op_id")))
        if m.get("status") != "completed":
            raise Refusal("op {0} is '{1}', not 'completed'".format(
                m.get("op_id"), m.get("status")))
        # Shape before the guard, as undo_new_row orders it: a record this
        # cannot read is refusable without enumerating the process list.
        bad = _converge_shape_error(m)
        if bad:
            raise Refusal(
                "the record for {0} is damaged ({1}), so undo cannot tell "
                "which rows it created; refusing rather than guess. Nothing "
                "was changed.".format(m.get("op_id"), bad))
        _guard_mutation(env, "remove rows from", NEW_ROW_STORE,
                        because=NEW_ROW_GUARD_WHY)
        roots = _retitle_roots(env)
        # The pointer census the last-pointer rule reads. load_rows is
        # best-effort (it collects unreadable rows instead of raising), and
        # that is SAFE here by construction: an unreadable row can never
        # prove another pointer exists, so it can only push a decision
        # toward skipping - the direction that never deletes.
        all_rows, _errs = load_rows(discover_stores(env).roots)
        pointers = {}
        for row in all_rows:
            if row.cli_session_id:
                pointers.setdefault(row.cli_session_id, set()).add(
                    os.path.normcase(os.path.realpath(row.path)))
        deleted, already_gone, skips = 0, 0, []
        for r in m["rows"]:
            if not r.get("written"):
                continue
            real = ensure_contained(r["dest_path"], roots)
            if os.path.dirname(real) != os.path.realpath(r["store_path"]):
                raise LayoutError("row {0!r} is not a direct child of {1!r}; "
                                  "refusing".format(r["dest_path"],
                                                    r["store_path"]))
            state = _sync_row_drift(r)
            if state == "absent":
                already_gone += 1
                continue
            if state == "unreadable":
                skips.append((r, "unreadable",
                              "it exists but could not be read"))
                continue
            if state == "drifted":
                try:
                    with open(r["dest_path"], "rb") as fh:
                        cur = json.loads(fh.read().decode("utf-8"))
                except (OSError, ValueError):
                    skips.append((r, "unreadable",
                                  "it exists but could not be read"))
                    continue
                if not isinstance(cur, dict):
                    skips.append((r, "unreadable",
                                  "it is no longer a JSON object"))
                    continue
                if cur.get("cliSessionId") != r["session"]:
                    skips.append((r, "repointed",
                                  "someone repointed it at {0} since; "
                                  "deleting it would destroy their work"
                                  .format(str(cur.get("cliSessionId")
                                              or "?")[:8])))
                    continue
                if (cur.get("title") != r.get("title")
                        or cur.get("titleSource") != r.get("title_source")):
                    skips.append((r, "curated",
                                  "its title was changed since, so the row "
                                  "is that account's now"))
                    continue
                # Only per-account state moved (focus times and the like) -
                # the app touched it, which is not curation. Fall through.
            key = os.path.normcase(os.path.realpath(r["dest_path"]))
            others = pointers.get(r["session"], set()) - {key}
            if not others:
                skips.append((r, "last_pointer",
                              "it is now the only row in any account that "
                              "reaches this conversation - the holders it "
                              "was copied from are gone, so this presence "
                              "is no longer redundant"))
                continue
            try:
                os.unlink(r["dest_path"])
            except OSError as exc:
                skips.append((r, "unremovable",
                              "could not remove it: {0}".format(exc)))
                continue
            pointers.get(r["session"], set()).discard(key)
            deleted += 1
        m["undo_report"] = {
            "deleted": deleted, "already_gone": already_gone,
            "skipped": [{"name": r.get("name"), "label": r.get("label"),
                         "session": r.get("session"), "reason": reason,
                         "detail": detail} for r, reason, detail in skips]}
        set_status(op, "undone")
        rotate_ops(env)
        return "undone"
    finally:
        release_lock(env)


def classify_converge_op(env, op):
    """Converge's recovery shape. Forward is a FRESH RE-EVALUATION of the
    remaining pairs - it re-runs the apply-time guards and per-pair checks
    against the store as it stands, so a pair whose situation changed in the
    window resolves against reality rather than against the plan's snapshot;
    recovery is never replay. Back removes the rows the op created (they are
    pointers; the conversations keep their rows elsewhere). A converge with
    rows landed and no completion marker is the equivalent new-row state,
    multiplied.

    Never raises (classify_op's contract).
    """
    m = op.manifest
    bad = _converge_shape_error(m)
    if bad:
        return {"status": m.get("status"), "source": "n/a", "dest": "n/a",
                "resolutions": [], "drifted_rows": [],
                "note": "converge: this operation's record is damaged ({0}), "
                        "so neither direction can be run from it".format(bad)}
    written = sum(1 for r in m["rows"] if r.get("written"))
    skipped = sum(1 for r in m["rows"] if r.get("skipped"))
    pending = sum(1 for r in m["rows"]
                  if not r.get("written") and not r.get("skipped"))
    if m["status"] not in NONTERMINAL:
        return {"status": m["status"], "source": "n/a", "dest": "n/a",
                "resolutions": [], "drifted_rows": [],
                "note": "converge: {0} row(s) created, {1} skipped (use undo "
                        "to remove a completed converge's rows)"
                        .format(written, skipped)}
    return {"status": m["status"], "source": "n/a", "dest": "n/a",
            "resolutions": ["forward", "back"], "drifted_rows": [],
            "note": "converge: {0} row(s) created, {1} pending, {2} skipped; "
                    "forward re-evaluates the remaining pairs against the "
                    "store as it now is (fresh guards and checks, never a "
                    "replay), back removes the rows this operation created"
                    .format(written, pending, skipped)}


def _public_converge_manifest(env, m):
    """The converge manifest with row post-images removed, for --json and the
    printed report - the same exposure rule as every sibling's public
    manifest, and where --anonymize is honoured. The structured pass covers
    the named fields (title, label); the free-text strings that EMBED titles
    - hold details, the pastable retitle commands, the holder labels inside
    `holders` - are scrubbed here, because anonymize_report only knows field
    names and a title inside a sentence is not a field.
    """
    out = {k: v for k, v in m.items() if k != "rows"}
    out["rows"] = [{k: v for k, v in r.items()
                    if k not in ("pre_b64", "post_b64")}
                   for r in m.get("rows", [])]
    if _ANONYMIZE:
        out = anonymize_report(env, out)

        def _scrub(s):
            return anonymize(env, s) if isinstance(s, str) else s

        def _scrub_label(s):
            if isinstance(s, str) and "@" in s:
                return _anon_label("account", s)
            return _scrub(s)

        for h in out.get("holds") or []:
            if isinstance(h, dict):
                h["detail"] = _scrub(h.get("detail"))
                h["retitle"] = _scrub(h.get("retitle"))
        for note in out.get("notes") or []:
            if isinstance(note, dict):
                note["retitle"] = _scrub(note.get("retitle"))
        for r in out["rows"]:
            if isinstance(r.get("holders"), list):
                r["holders"] = [_scrub_label(x) for x in r["holders"]]
            if isinstance(r.get("skip_detail"), str):
                r["skip_detail"] = _scrub(r["skip_detail"])
    return out


def _print_converge_report(say, m):
    """The plan: destinations first (the resolution is visible before
    anything is written), then the rows grouped by destination account, then
    the disagreement notes and holds with their pastable fixes, ending with
    the truthful completeness line. M is the PUBLIC manifest."""
    say("destinations:")
    for d in m.get("destinations", []):
        say("   {0:<44} {1}".format(d.get("label", ""), d.get("path", "")))
    for nd in m.get("non_destinations", []):
        say("   {0:<44} NOT a destination - every org directory under it is "
            "empty, so there is no evidence which one is real"
            .format(nd.get("label", "")))
    say("")
    rows = m.get("rows", [])
    holds = m.get("holds", [])
    if not rows and not holds:
        say("nothing to do - every eligible conversation already opens from "
            "every destination sidebar")
    for d in m.get("destinations", []):
        mine = [r for r in rows if r.get("account") == d.get("account")]
        if not mine:
            continue
        say("-> {0}".format(d.get("label", "")))
        for r in mine:
            line = "   {0}  {1!r}   held by: {2}".format(
                (r.get("session") or "")[:8], r.get("title", ""),
                ", ".join(r.get("holders") or []))
            if r.get("skipped"):
                line += "   [{0}: {1}]".format(r["skipped"],
                                               r.get("skip_detail", ""))
            say(line)
        say("")
    for note in m.get("notes", []):
        if isinstance(note, str):
            # 3b's corroboration note: one line, not a title-disagreement
            # block.
            say("note: {0}".format(note))
            say("")
            continue
        say("titles disagree across the holders of {0}; the newest "
            "activity's title {1!r} is used, the minority copies keep "
            "theirs:".format((note.get("session") or "")[:8],
                             note.get("title", "")))
        for e in note.get("holder_titles", []):
            say("   {0}   {1!r}".format(e.get("label", ""),
                                        e.get("title", "")))
        say("   level them: {0}".format(note.get("retitle", "")))
        say("")
    if holds:
        say("held - not applied, each with its fix:")
        for h in holds:
            say("   {0} -> {1}: {2} - {3}".format(
                (h.get("session") or "")[:8], h.get("label", ""),
                h.get("reason", ""), h.get("detail", "")))
            if h.get("retitle"):
                say("      {0}".format(h["retitle"]))
        say("")
    c = m.get("complete", {})
    say("complete{0} : {1} of {2}  ->  {3} of {2}   ({4} held)".format(
        " (scoped to --only)" if c.get("scoped") else "",
        c.get("now"), c.get("of"), c.get("after"), c.get("held")))
    if m.get("dead_excluded"):
        say("({0} dead conversation(s) excluded from the count - rows exist "
            "but the transcript is gone; 'doctor' reports them)"
            .format(m["dead_excluded"]))


def _identity_warning_lines(env, m):
    """Change 1 of 0.13.0: the warning block for a converge manifest that
    carries an identity_disagreement no valid --live arbitrated. [] when
    there is nothing to warn about.

    RULING 5 is evaluated only inside _converge_recheck, so a 0.12.0 plan
    could read `0 held` while --apply was always going to refuse for a
    reason fully knowable at plan time - the one break in the dry run's
    "this is what --apply will do" promise, and it cost a close-the-app
    cycle to discover. The wording is conditional ("while that disagreement
    stands") because this is a snapshot: the files can change in either
    direction before apply, and the recheck evaluates them fresh under the
    lock. Disclosure and enforcement are different jobs at different times;
    the plan never claims to be the gate, and the dry run still exits 0 - a
    chained `converge && converge --apply` stops at the apply's own
    refusal, loud, non-zero, nothing written. Machine callers read the
    manifest field instead (--json carries it).

    The remedy lines name each side by email - the oauth side's from
    oauthAccount.emailAddress, the config side's via account_email -
    falling back to the 8-char account id when an email is unknown OR when
    the two sides' emails are equal (two accounts under one address would
    print two identical, unusable remedies; the ids are already distinct,
    so nothing longer is needed). The parenthetical "(if the app is on
    ...)" is the honest part: the tool cannot know which - that is the
    user's fact, which is the entire premise of RULING 5.
    """
    dis = m.get("identity_disagreement")
    if not isinstance(dis, dict) or m.get("live_asserted"):
        return []
    oauth_u, config_u = dis.get("oauth") or "", dis.get("config") or ""
    if not (oauth_u and config_u):
        return []
    oauth_email = _oauth_email_for(env, oauth_u)
    config_email = account_email(env, config_u)[0]
    if oauth_email and oauth_email == config_email:
        oauth_name, config_name = oauth_u[:8], config_u[:8]
    else:
        oauth_name = oauth_email or oauth_u[:8]
        config_name = config_email or config_u[:8]
    if _ANONYMIZE:
        # An account address names a person; the ids are machine ids. Same
        # rule _public_converge_manifest's label scrub applies.
        if "@" in oauth_name:
            oauth_name = _anon_label("account", oauth_name)
        if "@" in config_name:
            config_name = _anon_label("account", config_name)
    remedies = ["claude-code-sessions converge --live {0} --apply".format(n)
                for n in (oauth_name, config_name)]
    width = max(len(r) for r in remedies)
    return [
        "warning : ~/.claude.json ({0}) and config.json ({1}) disagree about"
        .format(oauth_u[:8], config_u[:8]),
        "   the signed-in account. While that disagreement stands, --apply "
        "will refuse",
        "   (RULING 5) unless you assert which account the desktop app is "
        "on:",
        "      {0:<{w}}    (if the app is on {1})".format(
            remedies[0], oauth_u[:8], w=width),
        "      {0:<{w}}    (if the app is on {1})".format(
            remedies[1], config_u[:8], w=width),
        "   A refusal writes nothing, so trying --apply without --live is "
        "safe.",
    ]


def cmd_converge(env, ns):
    flags = ConvergeFlags(only=ns.only, live=ns.live)
    m = plan_converge(env, flags)

    def say(line):
        """cmd_new_row's wrapper, for the same two reasons: the report is
        mostly titles (arbitrary text), and piped Windows stdout is the
        console codepage, where an unprintable character must become a
        replacement character rather than a traceback."""
        text = line if ns.verbose else redact(env, line)
        try:
            print(text)
        except UnicodeEncodeError:
            enc = getattr(sys.stdout, "encoding", None) or "utf-8"
            print(text.encode(enc, "replace").decode(enc, "replace"))

    if not ns.json:
        # Report BEFORE the write, as cmd_new_row orders it: the destination
        # resolution is the plan's one derived judgement, and it must be on
        # screen before anything lands in a store.
        _print_converge_report(say, _public_converge_manifest(env, m))
        for line in _identity_warning_lines(env, m):
            say(line)
        if ns.apply:
            say("")

    final = None
    if ns.apply:
        # A zero-row plan skips run_converge the way cmd_sync skips run_sync:
        # there is nothing to journal, and "nothing to do is not an error".
        final = run_converge(env, m) if m["rows"] else "unchanged"

    def holds_remain():
        # Plan-time holds plus pairs the apply-time re-check held. Exit 3 is
        # the documented partial code for a run with holds - "bulk and
        # unattended" is exactly where a status code gets trusted without
        # reading prose.
        return bool(m.get("holds")) or any(
            r.get("skipped") == "held_title_collision"
            for r in m.get("rows", []))

    if ns.json:
        pub = _public_converge_manifest(env, m)
        if final is not None:
            pub["result"] = final
        print(json.dumps(pub, indent=1))
        if final is None:
            return 0
        if final not in ("completed", "unchanged"):
            return 1
        return 3 if holds_remain() else 0

    if final is None:
        say("\ndry run - pass --apply to create the rows")
        return 0
    created = sum(1 for r in m["rows"] if r.get("written"))
    say("result  : {0}".format(final))
    say("created : {0} row(s)".format(created))
    for r in m["rows"]:
        if r.get("skipped"):
            say("skipped : {0} -> {1}: {2} - {3}".format(
                (r.get("session") or "")[:8], r.get("label", ""),
                r["skipped"], r.get("skip_detail", "")))
    if final == "unchanged":
        say("nothing was written, so no operation was journalled and there "
            "is nothing to undo.")
    elif final == "completed" and created:
        say("Reopen the app - the sessions appear in every destination "
            "sidebar. 'undo' removes the created rows again.")
    if final not in ("completed", "unchanged"):
        return 1
    if holds_remain():
        say("exit 3: hold(s) remain - each is listed above with the fix "
            "that clears it.")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
