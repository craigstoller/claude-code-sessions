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


def main(argv=None):
    return 0


if __name__ == "__main__":
    sys.exit(main())
