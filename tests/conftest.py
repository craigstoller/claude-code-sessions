import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import claude_code_sessions as ct


@pytest.fixture
def mkenv():
    def make(tmp_path, n_store_roots=1, is_windows=True):
        home = tmp_path / "home"
        projects = home / ".claude" / "projects"
        projects.mkdir(parents=True)
        roots = []
        for i in range(n_store_roots):
            r = tmp_path / f"store{i}" / "claude-code-sessions"
            r.mkdir(parents=True)
            roots.append(str(r))
        ops = home / ".claude-code-journal" / "ops"
        ops.mkdir(parents=True)
        return ct.Env(
            home=str(home),
            projects_root=str(projects),
            store_candidates=roots,
            ops_dir=str(ops),
            moved_log=str(home / ".claude-code-journal" / "moved-log.jsonl"),
            is_windows=is_windows,
            process_lister=lambda: [],
            now=lambda: 1_800_000_000.0,
        )
    return make


@pytest.fixture
def write_transcript():
    def make(env, folder_name, session_id, entries):
        folder = os.path.join(env.projects_root, folder_name)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, session_id + ".jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            for e in entries:
                fh.write(json.dumps(e) + "\n")
        return path
    return make


@pytest.fixture
def write_row():
    def make(env, root_idx, org, account, local_id, data):
        d = os.path.join(env.store_candidates[root_idx], org, account)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, local_id + ".json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        return path
    return make


@pytest.fixture
def two_account_env(mkenv):
    """Env with a source (signed-in) and destination account store, plus a
    ~/.claude.json naming the source, mirroring the real layout
    <accountUuid>/<organizationUuid>/local_*.json."""
    def make(tmp_path):
        env = mkenv(tmp_path, n_store_roots=1)
        root = env.store_candidates[0]
        src = os.path.join(root, "aaaaaaaa-0000-0000-0000-000000000001",
                           "bbbbbbbb-0000-0000-0000-000000000002")
        dst = os.path.join(root, "cccccccc-0000-0000-0000-000000000003",
                           "dddddddd-0000-0000-0000-000000000004")
        os.makedirs(src)
        os.makedirs(dst)
        with open(os.path.join(env.home, ".claude.json"), "w", encoding="utf-8") as fh:
            json.dump({"oauthAccount": {
                "accountUuid": "aaaaaaaa-0000-0000-0000-000000000001",
                "organizationUuid": "bbbbbbbb-0000-0000-0000-000000000002",
                "emailAddress": "me@example.com"}}, fh)
        return env, src, dst
    return make
