"""claude-code-threads: inspect and relocate Claude Code threads on disk.

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
        ops_dir=os.path.join(home, ".claude-code-threads", "ops"),
        moved_log=os.path.join(home, ".claude-code-threads", "moved-log.jsonl"),
        is_windows=(sys.platform == "win32"),
        process_lister=_default_process_lister,
        now=time.time,
    )


def _default_process_lister():
    """Running processes as (pid, text) tuples, text lowercased.

    Windows `tasklist` cannot show full command lines, only the image name -
    a node-hosted CLI (the `claude` command is a node.exe process, not an exe
    named "claude") is therefore a known blind spot on Windows: it will not
    match "claude" in the returned text. The mtime heuristic in plan_move is
    the second layer that covers that gap. POSIX uses `ps ... args=` (full
    command line, not just the executable name) specifically so a node-hosted
    CLI process *is* visible there.
    """
    import subprocess
    try:
        if sys.platform == "win32":
            out = subprocess.run(["tasklist", "/FO", "CSV"], capture_output=True,
                                 text=True, timeout=15).stdout
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
            return result
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
        return result
    except Exception:
        return []


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
    terminal = [o for o in list_ops(env) if o.manifest.get("status") in TERMINAL]
    pruned = []
    for op in terminal[:-10]:
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
        raise Refusal("another claude-code-threads operation holds the lock ({0}). "
                      "If it is dead, run: claude-code-threads recover".format(holder_str))
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
    unverified_platform: bool = False
    row: list = ()
    yes: bool = False
    force: bool = False


# Our own console-script names. They contain "claude", so without this the
# process guard below would see this very tool and refuse to run.
OUR_COMMANDS = ("claude-code-threads", "cc-threads")


def claude_running(env):
    my_pids = {os.getpid(), os.getppid()}
    out = []
    for pid, text in env.process_lister():
        if pid in my_pids:
            continue                       # never self-refuse on our own process
        if any(name in text for name in OUR_COMMANDS):
            continue                       # nor on another instance of this tool
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
        raise Refusal("No transcript found for {0}. Use 'claude-code-threads list' to find "
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
        # normcase both sides: on a first run ~/.claude-code-threads does not exist
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
                          "orphan the desktop entry. If this thread was created by the "
                          "CLI (not the desktop app), pass --transcript-only.")
        if disc.status == "absent" and not flags.transcript_only:
            raise Refusal("no desktop store found. If you don't use the desktop app, "
                          "pass --transcript-only. (On mac/Linux the store locations "
                          "are unverified - absence may mean we looked in the wrong "
                          "place.)")
    mode = "desktop" if my_rows else "transcript_only"
    if mode == "desktop" and not env.is_windows and not flags.unverified_platform:
        raise Refusal("desktop-store mutations are unverified on this platform; pass "
                      "--unverified-platform to proceed anyway.")

    # 6. guards
    running = claude_running(env)
    if running:
        raise Refusal("Claude appears to be running ({0}). Close the app, then retry."
                      .format(", ".join(sorted(set(running))[:3])))
    age = env.now() - os.path.getmtime(source)
    if age < MTIME_GUARD_SECONDS and not flags.force:
        raise Refusal("transcript was written {0:.0f} seconds ago - this thread may be "
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
                      "was changed. Use 'claude-code-threads recover' to "
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
            "- nothing was lost, both copies remain. Run 'claude-code-threads "
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
                  "removed ({0}). Run 'claude-code-threads recover' to finish deleting "
                  "it.".format(", ".join(p for p, _ in failures)))
            return "committed"
        _rmdirs_bottom_up(m["sidecar_source"])

    try:
        os.unlink(m["source_transcript"])
    except OSError as exc:
        print("warning: move committed, but the old copy could not be fully "
              "removed ({0}). Run 'claude-code-threads recover' to finish deleting "
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
    post-state - any drift (the app resumed the moved thread, edited a row,
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
    # and resumed the thread there by hand - would otherwise be destroyed
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
    concurrent claude-code-threads invocations can never race each other here.
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


def classify_op(env, op):
    """Classify a non-terminal op's source/destination/row state and the
    safe recovery resolutions. Rules per spec Recovery classification, plus
    the adversarial-review fixes noted inline (I3, I7, C2).
    """
    m = op.manifest
    status = m["status"]

    if m.get("op_type") == "sync":
        return classify_sync_op(env, op)

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
        if op.manifest.get("op_type") != "sync":
            _validate_manifest_paths(env, op.manifest)

        c = classify_op(env, op)
        if direction not in c["resolutions"]:
            raise Refusal("'{0}' is not a safe resolution for op {1} ({2}); options: {3}"
                          .format(direction, op.manifest["op_id"], c["note"],
                                  c["resolutions"] or "none - manual intervention"))
        m = op.manifest
        if m.get("op_type") == "sync":
            # direction is already guaranteed to be a member of c["resolutions"]
            # by the check above, and classify_sync_op only ever offers
            # exactly one of "back" or "forward" (never both, never
            # anything else) for a non-terminal sync op - so no further
            # validation is needed here.
            if direction == "back":
                # Only reachable when a destination row has drifted enough
                # to permanently block forward (classify_sync_op's
                # diagnosis). Unlike undo_sync's all-or-nothing, back must
                # always terminate - refusing over a written row that also
                # drifted or turned unreadable would recreate the exact
                # dead end this resolution exists to close (recover back
                # refuses, recover forward refuses, undo refuses:
                # permanently stuck). So a row this op cannot verify is
                # SKIPPED, never deleted, and the rest are removed; the
                # blocking pending row itself is never even considered,
                # because this op never wrote it.
                drifted, unreadable, removable = _sync_delete_targets(env, m)
                _sync_unlink_all(removable)
                skipped = drifted + unreadable
                if skipped:
                    # Same reporting mechanism _abort already uses for
                    # move - cmd_recover's existing _print_abort_reason
                    # picks this up for free once status is non-"completed".
                    op.manifest["abort_reason"] = (
                        "back removed {0} row(s) it could verify; left {1} "
                        "untouched because {2}".format(
                            len(removable), len(skipped),
                            _drift_clause(drifted, unreadable)))
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


def redact(env, text, keep_ids=False):
    for h in {env.home, os.path.realpath(env.home)}:
        text = text.replace(h, "~")
    if not keep_ids:
        text = _UUID_RE.sub(lambda m: m.group(1) + "…", text)
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
        print("no threads found")
    return 0


RETENTION_HINT_DAYS = 30


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
    report = {
        "stores": {"status": disc.status, "roots": disc.roots, "detail": disc.detail},
        "row_count": len(rows), "row_errors": row_errors, "blank_rows": sorted(blank),
        "dead_rows": dead, "unlisted_transcripts": unlisted,
        "encoding": {"current": cur, "legacy": leg},
        "encoding_recent": {"current": cur_recent, "legacy": leg_recent},
        "legacy_folders": legacy_folders, "nonterminal_ops": nt,
        "stale_lock": lock_is_stale(env),
        "unknown_layout": unknown_layout,
    }
    if disc.status == "error" or row_errors or unknown_layout:
        report["exit_code"] = 2
    elif blank or dead or nt or report["stale_lock"] or legacy_folders:
        report["exit_code"] = 1
    else:
        report["exit_code"] = 0
    return report


def cmd_doctor(env, ns):
    rep = gather_doctor(env)
    if ns.json:
        print(json.dumps(rep, indent=1))
        return rep["exit_code"]
    def say(line):
        print(line if ns.verbose else redact(env, line))
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
    for sid in rep["unlisted_transcripts"]:
        say("[observed] transcript {0} has no listing row".format(sid))
        say("[hypothesis]   normal for CLI-created sessions; also what an interrupted "
            "external move leaves behind")
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
        say("[observed] unresolved operation {0} - run: claude-code-threads recover".format(oid))
    if rep["stale_lock"]:
        say("[observed] stale lock - run: claude-code-threads recover")
    if rep["exit_code"] == 2:
        say("[observed] unrecognized or unreadable state - please open an issue including the output above (paths and ids are redacted by default)")
    return rep["exit_code"]


# --------------------------------------------------------------- 7. CLI wiring
import argparse


def build_parser():
    p = argparse.ArgumentParser(prog="claude-code-threads",
        description="Inspect and relocate Claude Code threads on disk. Unofficial; "
                    "fails closed. Close the Claude app before any mutation.")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--verbose", action="store_true",
                        help="full paths and ids (default output is redacted)")

    sp = sub.add_parser("list", help="inventory threads")
    sp.add_argument("query", nargs="?", default="")
    sp.add_argument("--project")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--full", action="store_true")
    common(sp)

    sp = sub.add_parser("doctor", help="read-only health report")
    sp.add_argument("--json", action="store_true")
    common(sp)

    sp = sub.add_parser("move", help="relocate a thread to another project folder")
    sp.add_argument("session_id")
    sp.add_argument("target")
    sp.add_argument("--apply", action="store_true")
    sp.add_argument("--transcript-only", action="store_true", dest="transcript_only")
    sp.add_argument("--unverified-platform", action="store_true",
                    dest="unverified_platform")
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

    sp = sub.add_parser("sync", help="copy thread listing rows to your other account")
    sp.add_argument("--to", default="", metavar="UUID_SUBSTRING",
                    help="destination account/org id substring (required if more than one exists)")
    sp.add_argument("--only", default="", metavar="SUBSTRING",
                    help="only threads whose title contains this")
    sp.add_argument("--include-deleted", action="append", default=[],
                    dest="include_deleted", metavar="TITLE_OR_ID",
                    help="also copy this session even though the destination "
                         "deleted it (names one session; not a blanket switch)")
    sp.add_argument("--verbatim", action="store_true",
                    help="copy rows unchanged instead of stripping connector config")
    sp.add_argument("--apply", action="store_true")
    sp.add_argument("--json", action="store_true")
    common(sp)
    return p


def _flags_from(ns):
    return MoveFlags(transcript_only=ns.transcript_only,
                     unverified_platform=ns.unverified_platform,
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
    # delta: only a completed op whose op_type is "move" or "sync" (or
    # missing, which in practice never happens - every manifest sets
    # op_type) is eligible as "the operation to undo". A completed *undo*
    # op is itself terminal from cmd_undo's point of view - plan_undo
    # always refuses an undo-of-undo ("to redo, run move again") - so
    # selecting one here would only ever produce that refusal instead of
    # reaching an older, still-undoable completed move/sync underneath it.
    candidates = [o for o in ops if o.manifest.get("status") == "completed"
                 and o.manifest.get("op_type", "move") in ("move", "sync")]
    if ns.op_id:
        candidates = [o for o in candidates if o.manifest["op_id"] == ns.op_id]
    if not candidates:
        raise Refusal("no completed operation to undo" +
                      (" with id " + ns.op_id if ns.op_id else ""))
    prior = candidates[-1]
    if not ns.apply:
        if prior.manifest.get("op_type") == "sync":
            # A sync manifest has no session_id - the move-shaped preview
            # below would print "session None". Name what undo would
            # actually remove instead: how many rows landed, and where.
            n_written = sum(1 for r in prior.manifest.get("rows", []) if r.get("written"))
            dest = prior.manifest.get("dest_email") or prior.manifest.get("dest_account", "")
            line = ("would undo {0} (sync: {1} row(s) written to {2}); pass --apply "
                    "to execute".format(prior.manifest["op_id"], n_written, dest))
        else:
            line = ("would undo {0} (session {1}); pass --apply to execute"
                    .format(prior.manifest["op_id"], prior.manifest.get("session_id")))
        print(line if ns.verbose else redact(env, line))   # M1: redact the preview too
        return 0
    before_ids = {o.manifest["op_id"] for o in list_ops(env)}
    if prior.manifest.get("op_type") == "sync":
        final = undo_sync(env, prior)
    else:
        final = run_undo(env, prior)
    print("result: " + final)
    # undo_sync's own terminal status is "undone" (it mutates the completed
    # sync op in place rather than journaling a fresh reversal op the way
    # run_undo/execute_op do) - "completed" remains the success value for
    # every move/undo op the engine drives.
    success = final == "completed" or final == "undone"
    if not success:
        _print_new_op_reason(env, ns, before_ids)
    return 0 if success else 1


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
    if not ns.apply:
        print("would resolve {0} {1}; pass --apply to execute".format(ns.op_id, direction))
        return 0
    final = recover_op(env, matches[0], direction)
    print("result: " + final)
    if final != "completed":
        _print_abort_reason(env, ns, matches[0])
    return 0


def main(argv=None):
    ns = build_parser().parse_args(argv)
    env = default_env()
    handlers = {"list": cmd_list, "doctor": cmd_doctor, "move": cmd_move,
                "undo": cmd_undo, "recover": cmd_recover, "sync": cmd_sync}
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


def _account_dirs(env):
    """Every <accountUuid>/<organizationUuid> pair present on disk."""
    disc = discover_stores(env)
    if disc.status == "error":
        raise LayoutError("store discovery failed: {0}. 'Couldn't look' is never "
                          "'nothing there' - refusing.".format(disc.detail))
    out = []
    for root in disc.roots:
        for acct in sorted(os.listdir(root)):
            ap = os.path.join(root, acct)
            if not os.path.isdir(ap):
                continue
            for org in sorted(os.listdir(ap)):
                op = os.path.join(ap, org)
                if os.path.isdir(op):
                    out.append((acct, org, op))
    return out


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
    """
    try:
        with open(os.path.join(env.home, ".claude.json"), encoding="utf-8") as fh:
            oa = (json.load(fh) or {}).get("oauthAccount") or {}
    except (OSError, ValueError):
        oa = {}
    dirs = _account_dirs(env)
    acct_uuid = oa.get("accountUuid")
    if acct_uuid:
        org_uuid = oa.get("organizationUuid")
        exact = [(a, o, p) for a, o, p in dirs if a == acct_uuid and o == org_uuid]
        if exact:
            a, o, p = exact[0]
            return Account(a, o, oa.get("emailAddress") or "", p)
        for a, o, p in dirs:
            if a == acct_uuid:
                return Account(a, o, oa.get("emailAddress") or "", p)
    for cand in env.store_candidates:
        cfg = os.path.join(os.path.dirname(cand), "config.json")
        try:
            with open(cfg, encoding="utf-8") as fh:
                last = (json.load(fh) or {}).get("lastKnownAccountUuid")
        except (OSError, ValueError):
            continue
        if last:
            matches = [(a, o, p) for a, o, p in dirs if a == last]
            if len(matches) == 1:
                a, o, p = matches[0]
                return Account(a, o, "", p)
            if len(matches) > 1:
                return None    # ambiguous org under this account - fail closed
            # zero matches: this candidate's config names an account with no
            # store dir on disk yet - try the next store candidate's config
    return None


def resolve_sync_endpoints(env, to=None):
    """(source, destination). Source is the signed-in account; destination is
    the other store. Refuses rather than guessing - row-freshness is NEVER
    used to choose, because sync's whole safety model is 'we only ever write
    the dormant store', and a wrong guess writes the live one."""
    dirs = _account_dirs(env)
    source = live_account(env)
    if source is None:
        listing = "\n".join("   {0}/{1}".format(a[:8], o[:8]) for a, o, _ in dirs)
        raise Refusal(
            "cannot identify the signed-in account from ~/.claude.json or config.json.\n"
            "Refusing to guess which store is live - naming the wrong one would write the\n"
            "account the app is actively using, with no process guard to stop it.\n"
            "--to cannot override this: it names the destination, and without knowing\n"
            "which account is live we cannot verify the one you named is not it.\n"
            "Fix: sign in to the Claude desktop app so ~/.claude.json names the account.\n"
            "Stores found:\n" + listing)
    others = [Account(a, o, "", p) for a, o, p in dirs if a != source.account_uuid]
    if not others:
        raise Refusal("no other account store on this machine - nothing to sync into")
    if to:
        matched = [c for c in others
                  if to.lower() in (c.account_uuid + " " + c.org_uuid + " " + c.email).lower()]
        if not matched:
            raise Refusal("--to {0!r} matched no other account store".format(to))
        if len(matched) > 1:
            listing = "\n".join("   {0}/{1}".format(c.account_uuid[:8], c.org_uuid[:8])
                                for c in matched)
            raise Refusal("--to {0!r} matched {1} accounts; be more specific:\n{2}"
                          .format(to, len(matched), listing))
        return source, matched[0]
    if len(others) > 1:
        listing = "\n".join("   {0}/{1}".format(c.account_uuid[:8], c.org_uuid[:8])
                            for c in others)
        raise Refusal("more than one other account store; name one with --to:\n" + listing)
    return source, others[0]


@dataclasses.dataclass
class SyncFlags:
    to: str = ""
    only: str = ""
    include_deleted: tuple = ()
    verbatim: bool = False


def _destination_tombstones(dest):
    """Session ids the DESTINATION account has deleted. Only the destination's
    history matters - tombstones are per-account, so the source's deletions
    say nothing about what this account should see."""
    out = set()
    for name in os.listdir(dest.path):
        if name.startswith("deleted_"):
            out.add(name[len("deleted_"):])
    return out


def select_sync_rows(env, source, dest, flags):
    """Which source rows are eligible to copy, and why the rest were skipped.

    A row qualifies only if: it is a local_*.json row (not a tombstone or
    other sidecar), it is absent from the destination by filename, its
    transcript still exists somewhere under ~/.claude/projects (a row with no
    transcript is a dead pointer), and the destination holds no tombstone for
    its cliSessionId - unless the row's title/session id was named via
    --include-deleted, which overrides the tombstone skip for that row only.
    """
    tally = {"present": [], "no_transcript": [], "deleted": [], "unreadable": [],
             "filtered": []}
    have = set(os.listdir(dest.path))
    tombs = _destination_tombstones(dest)
    named = tuple(s.lower() for s in (flags.include_deleted or ()))
    picked = []
    for name in sorted(os.listdir(source.path)):
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
        title = d.get("title") or "(untitled)"
        sid = d.get("cliSessionId") or ""
        if flags.only and flags.only.lower() not in title.lower():
            tally["filtered"].append(title)
            continue
        if name in have:
            tally["present"].append(title)
            continue
        if not sid or not find_transcripts(env.projects_root, sid):
            tally["no_transcript"].append(title)
            continue
        if sid in tombs:
            # E4: the app shows a restored row for a deleted session, so this
            # skip is the only thing preventing a resurrection.
            if not any(n in title.lower() or n == sid.lower() for n in named):
                tally["deleted"].append(title)
                continue
        picked.append({"name": name, "src_path": p, "data": d, "session_id": sid,
                       "title": title, "last_activity": d.get("lastActivityAt") or 0})
    picked.sort(key=lambda r: r["last_activity"], reverse=True)
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


def plan_sync(env, flags):
    """Build the sync manifest. Pure planning - writes nothing."""
    source, dest = resolve_sync_endpoints(env, flags.to or None)
    picked, tally = select_sync_rows(env, source, dest, flags)
    rows = []
    for cand in picked:
        blob, removed, reset = transform_row(cand["data"], flags.verbatim)
        rows.append({"name": cand["name"],
                     "dest_path": os.path.join(dest.path, cand["name"]),
                     "post_b64": b64(blob), "session_id": cand["session_id"],
                     "title": cand["title"], "removed": removed, "reset": reset,
                     "written": False})
    return {"op_type": "sync",
            "source_account": source.account_uuid, "source_org": source.org_uuid,
            "source_email": source.email, "source_path": source.path,
            "dest_account": dest.account_uuid, "dest_org": dest.org_uuid,
            "dest_email": dest.email, "dest_path": dest.path,
            "verbatim": bool(flags.verbatim), "rows": rows, "tally": tally}


def execute_sync_op(env, op):
    """journaled -> writing -> completed.

    Far simpler than execute_op because nothing is deleted and no transcript
    moves: the destructive step that dominates a move does not exist here.
    Each row is journaled as written immediately after its atomic_write, so a
    crash leaves an exact record of which rows landed.
    """
    m = op.manifest
    if m.get("status") != "journaled":
        raise LayoutError("execute_sync_op runs ops from 'journaled'; use recover "
                          "for interrupted ops")

    # The whole no-process-guard design rests on sync only ever writing the
    # dormant account's store (resolve_sync_endpoints picks it, never the
    # live one). Re-check at execute time, not just at planning time, so a
    # manifest that is stale - or was hand-edited between plan and run -
    # can never land a write in the account the app is actively using. This
    # check on m["dest_path"] only carries that guarantee transitively: it is
    # the row loop's own ensure_contained call below - not this check alone -
    # that stops an individually hand-edited row["dest_path"] from escaping
    # the account this refusal just verified is safe to write.
    live = live_account(env)
    if live is not None and os.path.normcase(os.path.normpath(m["dest_path"])) == \
            os.path.normcase(os.path.normpath(live.path)):
        raise Refusal(
            "destination resolves to the LIVE account ({0}); refusing - sync must "
            "never write to the account that is currently live, which is exactly "
            "why it ships with no process guard.".format(live.email or live.account_uuid))
    if not os.path.isdir(m["dest_path"]):
        raise LayoutError("destination store vanished: " + m["dest_path"])

    set_status(op, "writing")
    rows = m["rows"]
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
        if current is None:
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
            # (e.g. the user signed in and touched that thread) - rewriting
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
        save_manifest(op)
        if i < len(rows) - 1:
            _maybe_crash("sync-mid-write")
    set_status(op, "completed")
    return "completed"


def run_sync(env, manifest):
    """Lock, journal, execute, rotate - the same shape as run_move, minus the
    moved-log append (sync moves nothing, so there is nothing to log there).
    One difference from run_move worth flagging: run_move's execute_op calls
    _validate_manifest_paths once, up front, for every path in the manifest.
    Sync has no such single up-front pass - each row's dest_path is instead
    validated inline, immediately before that row is touched, inside
    execute_sync_op's write loop (there is no sidecar inventory or transcript
    path here for a single shared validator to be worth factoring out)."""
    acquire_lock(env, "pending")
    try:
        op = new_op(env, manifest)
        # already holding the lock (no O_EXCL needed) - just record the real op_id
        with open(_lock_path(env), "w") as fh:
            fh.write("{0} {1}".format(os.getpid(), op.manifest["op_id"]))
        final = execute_sync_op(env, op)
        if final == "completed":
            rotate_ops(env)
        return final
    finally:
        release_lock(env)


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
    """
    try:
        with open(r["dest_path"], "rb") as fh:
            cur = fh.read()
    except FileNotFoundError:
        return "absent"
    except OSError:
        return "unreadable"
    return "match" if cur == unb64(r["post_b64"]) else "drifted"


def _sync_drift_titles(rows):
    """(changed_titles, unreadable_titles) for ROWS, via _sync_row_drift.
    Read-only and exception-safe."""
    changed, unreadable = [], []
    for r in rows:
        state = _sync_row_drift(r)
        if state == "drifted":
            changed.append(r["title"])
        elif state == "unreadable":
            unreadable.append(r["title"])
    return changed, unreadable


def _drift_clause(changed, unreadable):
    """A note/refusal fragment naming drifted vs unreadable rows separately.
    'Changed' is only true of one of them - conflating the two would falsely
    claim an unreadable row 'changed' when the real reason is a permission
    or I/O error."""
    parts = []
    if changed:
        parts.append("changed since this sync was planned ({0})".format(", ".join(changed)))
    if unreadable:
        parts.append("could not be read ({0})".format(", ".join(unreadable)))
    return " and ".join(parts)


def classify_sync_op(env, op):
    """Sync's recovery shape. A sync only ever adds rows, so 'back' does not
    normally exist - forward finishes the remaining writes, and removing
    what was written is `undo`'s job. The one exception: if a destination
    row changes underneath a still-in-flight sync, forward can never
    complete (execute_sync_op refuses on that exact row every time it
    re-enters), so offering it forever would be a dead end - 'back' becomes
    the only way off a stuck op. A written row that cannot itself be
    verified is surfaced here too, before the user even picks a direction -
    a single event ("the dormant account got opened") can plausibly both
    block a pending row and rewrite an already-written one, so 'options:
    back' alone would otherwise promise more than back can deliver.
    """
    m = op.manifest
    written = [r for r in m["rows"] if r.get("written")]
    pending = [r for r in m["rows"] if not r.get("written")]
    if m["status"] not in NONTERMINAL:
        return {"status": m["status"], "source": "n/a", "dest": "n/a",
                "resolutions": [], "drifted_rows": [],
                "note": "sync: {0} row(s) written, {1} pending; forward finishes "
                        "them (use undo to remove what was written)"
                        .format(len(written), len(pending))}
    pend_changed, pend_unreadable = _sync_drift_titles(pending)
    if pend_changed or pend_unreadable:
        blocking = pend_changed + pend_unreadable
        skip_changed, skip_unreadable = _sync_drift_titles(written)
        skipped = skip_changed + skip_unreadable
        note = ("sync: destination row(s) {0}; it can no longer be rolled "
                "forward - 'back' removes the row(s) this op safely can"
                .format(_drift_clause(pend_changed, pend_unreadable)))
        if skipped:
            note += ("; it will also skip {0} already-written row(s) it cannot "
                     "verify ({1})".format(len(skipped),
                                           _drift_clause(skip_changed, skip_unreadable)))
        return {"status": m["status"], "source": "n/a", "dest": "n/a",
                "resolutions": ["back"], "drifted_rows": blocking + skipped, "note": note}
    return {"status": m["status"], "source": "n/a", "dest": "n/a",
            "resolutions": ["forward"], "drifted_rows": [],
            "note": "sync: {0} row(s) written, {1} pending; forward finishes them "
                    "(use undo to remove what was written)"
                    .format(len(written), len(pending))}


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
    1. The same live-account re-check execute_sync_op makes on every write.
       Sync ships with no process guard specifically because it re-verifies
       live_account(env) against the destination at execute time, never
       relying on planning-time state alone - a delete is exactly as
       dangerous as a write here, and must carry the identical guarantee.
    2. The same containment execute_sync_op's write loop uses
       (ensure_contained plus the direct-child check), so a hand-edited or
       corrupted manifest row can never point this delete outside the
       destination store.

    Per row, via _sync_row_drift: a row this op never wrote (`written` is
    not True) is never considered. 'absent' is skipped as already-undone.
    'match' is removable. 'drifted' and 'unreadable' are reported
    separately - neither is ever deleted.

    Returns (drifted_titles, unreadable_titles, removable_paths). Deletes
    nothing itself.
    """
    live = live_account(env)
    if live is not None and os.path.normcase(os.path.normpath(m["dest_path"])) == \
            os.path.normcase(os.path.normpath(live.path)):
        raise Refusal(
            "destination resolves to the LIVE account ({0}); refusing - undo, "
            "like sync, may only ever touch a dormant store, never the account "
            "that is currently live.".format(live.email or live.account_uuid))
    drifted, unreadable, removable = [], [], []
    for r in m["rows"]:
        if not r.get("written"):
            continue
        real_dest = ensure_contained(r["dest_path"], [m["dest_path"]])
        if os.path.dirname(real_dest) != os.path.realpath(m["dest_path"]):
            raise LayoutError(
                "row dest_path {0!r} is not a direct child of the destination "
                "store {1!r}; refusing".format(r["dest_path"], m["dest_path"]))
        state = _sync_row_drift(r)
        if state == "absent":
            continue                       # confirmed absent - nothing to undo
        elif state == "match":
            removable.append(r["dest_path"])
        elif state == "drifted":
            drifted.append(r["title"])
        else:                              # "unreadable"
            unreadable.append(r["title"])
    return drifted, unreadable, removable


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


def undo_sync(env, op):
    """Delete exactly the rows this sync wrote - and only while they are still
    byte-identical to what it wrote. If the destination account has since
    opened the thread the app rewrites the row, and deleting it would discard
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
        drifted, unreadable, removable = _sync_delete_targets(env, m)
        if drifted or unreadable:
            raise Refusal("these synced rows {0}; the other account may have opened "
                          "them. Refusing to delete any of them."
                          .format(_drift_clause(drifted, unreadable)))
        _sync_unlink_all(removable)
        set_status(op, "undone")
        rotate_ops(env)
        return "undone"
    finally:
        release_lock(env)


def cmd_sync(env, ns):
    flags = SyncFlags(to=ns.to, only=ns.only,
                      include_deleted=tuple(ns.include_deleted or ()),
                      verbatim=ns.verbatim)
    manifest = plan_sync(env, flags)

    # --apply executes up front, before ANY reporting - JSON included - so
    # that "sync --apply --json" (exactly the combination automation would
    # use) reports what actually happened rather than silently planning-only
    # while claiming success. A zero-row plan is the one case that skips
    # run_sync regardless of --apply: there's nothing to journal, and
    # journaling an empty op anyway was the parked finding this task closed.
    final = None
    if manifest["rows"] and ns.apply:
        final = run_sync(env, manifest)

    if ns.json:
        if final is not None:
            manifest["result"] = final
        print(json.dumps(manifest, indent=1))
        return 0 if final in (None, "completed") else 1

    def say(line):
        print(line if ns.verbose else redact(env, line))

    # Spec s5: name both endpoints, with emails, before doing anything (or,
    # above, immediately after - the report still leads with identity).
    # dest_email is "" for every non-live account - the dormant account's
    # email isn't recorded anywhere on disk, so an unlabelled run always hits
    # this, not just an edge case - so also print the store path and org
    # prefix, through the same say()/redact() convention (redacted unless
    # --verbose), giving a cautious user a physical folder to recognise
    # instead of eight hex characters. Symmetric for the source.
    say("from  {0:24} ({1}/{2})   signed in".format(
        manifest["source_email"] or "(email unknown)",
        manifest["source_account"][:8], manifest["source_org"][:8]))
    say("      " + manifest["source_path"])
    say("to    {0:24} ({1}/{2})   signed out".format(
        manifest["dest_email"] or "(email unknown)",
        manifest["dest_account"][:8], manifest["dest_org"][:8]))
    say("      " + manifest["dest_path"])
    say("")

    tally = manifest["tally"]
    LABELS = [("present", "already in the destination"),
              ("no_transcript", "skipped, transcript gone"),
              ("deleted", "skipped, deleted in the destination"),
              ("unreadable", "skipped, unreadable row"),
              ("filtered", "skipped, did not match --only")]
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
    say("{0:36}: {1}".format("to copy", len(manifest["rows"])))
    for r in manifest["rows"][:15]:
        say("   {0}".format(r["title"]))
    if len(manifest["rows"]) > 15:
        say("   ... and {0} more".format(len(manifest["rows"]) - 15))

    if not manifest["rows"]:
        say("\nnothing to copy")
        return 0
    if final is None:
        say("\ndry run - pass --apply to copy")
        return 0

    say("\ncopied     : {0}".format(sum(1 for r in manifest["rows"] if r.get("written"))))
    say("result     : {0}".format(final))
    say("Sign into {0} (or restart the app) to see them."
        .format(manifest["dest_email"] or "the other account"))
    return 0 if final == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
