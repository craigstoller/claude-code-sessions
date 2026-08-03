import os

import claude_session_store as ct


def test_found_single_root(mkenv, tmp_path):
    env = mkenv(tmp_path, n_store_roots=1)
    d = ct.discover_stores(env)
    assert d.status == "found"
    assert d.roots == [os.path.realpath(env.store_candidates[0])]


def test_absent_when_no_candidate_exists(mkenv, tmp_path):
    env = mkenv(tmp_path, n_store_roots=0)
    env.store_candidates = [str(tmp_path / "nowhere" / "claude-code-sessions")]
    (tmp_path / "nowhere").mkdir()          # parent enumerable, store not present
    d = ct.discover_stores(env)
    assert d.status == "absent"


def test_error_when_parent_unenumerable(mkenv, tmp_path, monkeypatch):
    env = mkenv(tmp_path, n_store_roots=0)
    env.store_candidates = [str(tmp_path / "ghost" / "claude-code-sessions")]
    real_listdir = os.listdir

    def boom(p):
        if "ghost" in str(p):
            raise PermissionError("no")
        return real_listdir(p)

    monkeypatch.setattr(os, "listdir", boom)
    d = ct.discover_stores(env)
    assert d.status == "error"
    assert "ghost" in d.detail


def test_alias_dedup(mkenv, tmp_path):
    env = mkenv(tmp_path, n_store_roots=1)
    env.store_candidates = [env.store_candidates[0], env.store_candidates[0]]
    d = ct.discover_stores(env)
    assert d.status == "found" and len(d.roots) == 1


def test_absent_when_parent_missing_entirely(mkenv, tmp_path):
    env = mkenv(tmp_path, n_store_roots=0)
    env.store_candidates = [str(tmp_path / "never-installed" / "Claude" / "claude-code-sessions")]
    d = ct.discover_stores(env)
    assert d.status == "absent"
