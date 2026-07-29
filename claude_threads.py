"""claude-threads: inspect and relocate Claude Code threads on disk.

Unofficial. Fails closed: verifies the on-disk layout against evidence and
refuses to mutate anything it cannot positively verify.

Sections (in order):
  1. Env, exceptions, constants        4. Transcript location
  2. Helpers (hashing, atomic IO)      5. Transaction engine (move/undo/recover)
  3. Platform & store discovery,       6. Commands (list/doctor/move/undo/recover)
     rows, encoding detection          7. CLI wiring
"""
from __future__ import annotations

import dataclasses
import os
import sys

SCHEME_CURRENT = r"[^A-Za-z0-9]"    # app >= ~2026-07-12: underscores also become '-'
SCHEME_LEGACY = r"[^A-Za-z0-9_]"    # before: underscores survived

NONTERMINAL = ("journaled", "copying", "copied", "rewriting", "committed", "aborting")
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
        ops_dir=os.path.join(home, ".claude-threads", "ops"),
        moved_log=os.path.join(home, ".claude-threads", "moved-log.jsonl"),
        is_windows=(sys.platform == "win32"),
        process_lister=_default_process_lister,
        now=time.time,
    )


def _default_process_lister():
    """Names of running processes, lowercased. Implemented in Task 9."""
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
                rows.append(Row(path, read_json(path)))
            except LayoutError as exc:
                errors.append(str(exc))
    return rows, errors


def main(argv=None):
    return 0


if __name__ == "__main__":
    sys.exit(main())
