"""Desktop 2.42304.2 moved the app's user data (2026-10-19).

Old data root: %LOCALAPPDATA%\\Packages\\Claude_<pkg>\\LocalCache\\Roaming\\Claude\\,
left holding only a 0-byte marker, .user-data-left.claude_<pkg>. New data root:
%LOCALAPPDATA%\\Claude-Data\\claude_<pkg>\\, the store inside unchanged. The helper
did not move: it stays under the package's LocalCache\\Roaming.

0.16.2 knew only the old roots, so every command started from "store absent".
These tests pin where the store is looked for, which root wins when both exist,
where the helper is looked for (independently of the store), and the note an
absent store carries when the app left its marker.
"""
import json
import os
import types

import pytest

import claude_code_sessions as ct


def _ns(**kw):
    d = {"json": False, "verbose": True, "query": "", "project": None, "full": False}
    d.update(kw)
    return types.SimpleNamespace(**d)


def _old_claude_dir(local, pkg="abc123"):
    return local / "Packages" / ("Claude_" + pkg) / "LocalCache" / "Roaming" / "Claude"


def _moved_root(local, pkg="abc123"):
    return local / "Claude-Data" / ("claude_" + pkg)


def _row(store, name, account="a" * 32, org="o" * 32):
    d = store / account / org
    d.mkdir(parents=True, exist_ok=True)
    (d / (name + ".json")).write_text(json.dumps(
        {"sessionId": name, "cliSessionId": "s-" + name, "cwd": "x"}),
        encoding="utf-8")


@pytest.fixture
def roots(tmp_path):
    local = tmp_path / "local"
    appdata = tmp_path / "roaming"
    local.mkdir()
    appdata.mkdir()
    return local, appdata


# ------------------------------------------------------- store candidates

def test_moved_data_root_holds_the_store(mkenv, tmp_path, roots):
    local, appdata = roots
    moved = _moved_root(local) / "claude-code-sessions"
    moved.mkdir(parents=True)
    old = _old_claude_dir(local)
    old.mkdir(parents=True)
    (old / ".user-data-left.claude_abc123").write_bytes(b"")
    cands = ct._windows_store_candidates(str(local), str(appdata))
    assert cands[0] == str(moved)
    env = mkenv(tmp_path, n_store_roots=0)
    env.store_candidates = cands
    d = ct.discover_stores(env)
    assert d.status == "found"
    assert d.roots == [os.path.realpath(str(moved))]


def test_old_package_root_is_read_on_a_machine_that_has_not_updated(
        mkenv, tmp_path, roots):
    local, appdata = roots
    old = _old_claude_dir(local) / "claude-code-sessions"
    old.mkdir(parents=True)
    cands = ct._windows_store_candidates(str(local), str(appdata))
    assert str(old) in cands
    env = mkenv(tmp_path, n_store_roots=0)
    env.store_candidates = cands
    assert ct.discover_stores(env).roots == [os.path.realpath(str(old))]


def test_moved_root_wins_over_the_same_packages_old_root(mkenv, tmp_path, roots):
    # Both populated: the moved root is the one the updated app reads (it was
    # seen writing there), so the old one is the pre-move copy. Reading both
    # would put every row in twice, and the old root's config.json would feed
    # the identity checks a pre-move account.
    local, appdata = roots
    moved = _moved_root(local) / "claude-code-sessions"
    old = _old_claude_dir(local) / "claude-code-sessions"
    _row(moved, "local_moved")
    _row(old, "local_stale")
    cands = ct._windows_store_candidates(str(local), str(appdata))
    assert str(moved) in cands
    assert str(old) not in cands
    env = mkenv(tmp_path, n_store_roots=0)
    env.store_candidates = cands
    rows, errors = ct.load_rows(ct.discover_stores(env).roots)
    assert errors == []
    assert [r.local_id for r in rows] == ["local_moved"]


def test_moved_root_without_a_store_still_supersedes_the_old_one(
        mkenv, tmp_path, roots):
    # The data root is the app's, store or no store: its absence of a store is
    # proven there, not filled in from the root the app left.
    local, appdata = roots
    _moved_root(local).mkdir(parents=True)
    old = _old_claude_dir(local) / "claude-code-sessions"
    _row(old, "local_stale")
    cands = ct._windows_store_candidates(str(local), str(appdata))
    assert str(_moved_root(local) / "claude-code-sessions") in cands
    assert str(old) not in cands
    env = mkenv(tmp_path, n_store_roots=0)
    env.store_candidates = cands
    assert ct.discover_stores(env).status == "absent"


def test_precedence_is_per_package(roots):
    local, appdata = roots
    other_old = _old_claude_dir(local, pkg="aaa111") / "claude-code-sessions"
    other_old.mkdir(parents=True)
    moved = _moved_root(local, pkg="bbb222") / "claude-code-sessions"
    moved.mkdir(parents=True)
    cands = ct._windows_store_candidates(str(local), str(appdata))
    assert str(moved) in cands and str(other_old) in cands


def test_moved_roots_come_first_and_the_classic_candidate_last(roots):
    local, appdata = roots
    other_old = _old_claude_dir(local, pkg="aaa111") / "claude-code-sessions"
    other_old.mkdir(parents=True)
    moved = _moved_root(local, pkg="bbb222") / "claude-code-sessions"
    moved.mkdir(parents=True)
    classic = os.path.join(str(appdata), "Claude", "claude-code-sessions")
    assert ct._windows_store_candidates(str(local), str(appdata)) == [
        str(moved), str(other_old), classic]


def test_no_package_or_moved_candidate_without_an_absolute_localappdata(
        tmp_path, monkeypatch, roots):
    # With LOCALAPPDATA unset, a glob of the relative pattern would search the
    # working directory, and a look-alike tree there would become a store.
    _local, appdata = roots
    cwd = tmp_path / "cwd"
    (cwd / "Claude-Data" / "claude_x" / "claude-code-sessions").mkdir(parents=True)
    (cwd / "Packages" / "Claude_x" / "LocalCache" / "Roaming" / "Claude" /
     "claude-code-sessions").mkdir(parents=True)
    monkeypatch.chdir(cwd)
    assert ct._windows_store_candidates("", str(appdata)) == [
        os.path.join(str(appdata), "Claude", "claude-code-sessions")]


# ------------------------------------------------------------ helper roots

def test_helper_roots_are_the_package_roaming_folders(roots):
    local, appdata = roots
    roaming = _old_claude_dir(local).parent
    roaming.mkdir(parents=True)
    (_moved_root(local) / "claude-code-sessions").mkdir(parents=True)
    assert ct._windows_helper_roots(str(local), str(appdata)) == [
        str(roaming), str(appdata)]


def test_helper_roots_skip_a_relative_localappdata(tmp_path, monkeypatch, roots):
    _local, appdata = roots
    cwd = tmp_path / "cwd"
    (cwd / "Packages" / "Claude_x" / "LocalCache" / "Roaming").mkdir(parents=True)
    monkeypatch.chdir(cwd)
    assert ct._windows_helper_roots("", str(appdata)) == [str(appdata)]


def test_default_env_on_windows_lists_both(monkeypatch, roots):
    local, appdata = roots
    (_moved_root(local) / "claude-code-sessions").mkdir(parents=True)
    _old_claude_dir(local).mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setattr(ct.sys, "platform", "win32")
    env = ct.default_env()
    assert env.store_candidates == ct._windows_store_candidates(str(local), str(appdata))
    assert env.helper_roots == ct._windows_helper_roots(str(local), str(appdata))
    assert env.helper_roots[0] == str(_old_claude_dir(local).parent)


@pytest.mark.parametrize("platform, tail", [
    ("linux", (".config",)),
    ("darwin", ("Library", "Application Support")),
])
def test_default_env_off_windows_keeps_the_helper_root_it_derived_before(
        monkeypatch, platform, tail):
    # Before, the helper root was two folders up from the one store candidate.
    monkeypatch.setattr(ct.sys, "platform", platform)
    env = ct.default_env()
    home = os.path.expanduser("~")
    assert env.helper_roots == [os.path.join(home, *tail)]
    assert env.helper_roots == [os.path.dirname(os.path.dirname(env.store_candidates[0]))]


# --------------------------------------------- the marker the app leaves

def test_absent_store_names_the_marker_the_app_left(mkenv, tmp_path):
    env = mkenv(tmp_path, n_store_roots=0)
    left = tmp_path / "left" / "Claude"
    left.mkdir(parents=True)
    (left / ".user-data-left.claude_y").write_bytes(b"")
    env.store_candidates = [str(left / "claude-code-sessions")]
    d = ct.discover_stores(env)
    assert d.status == "absent"
    assert d.detail.startswith("no store under any known candidate")
    assert os.path.join(str(left), ".user-data-left.claude_y") in d.detail
    assert "moved" in d.detail


def test_absent_store_without_a_marker_says_only_that(mkenv, tmp_path):
    env = mkenv(tmp_path, n_store_roots=0)
    (tmp_path / "plain").mkdir()
    env.store_candidates = [str(tmp_path / "plain" / "claude-code-sessions")]
    d = ct.discover_stores(env)
    assert (d.status, d.detail) == ("absent", "no store under any known candidate")


def test_found_store_does_not_mention_a_marker(mkenv, tmp_path):
    env = mkenv(tmp_path, n_store_roots=1)
    left = tmp_path / "left"
    left.mkdir()
    (left / ".user-data-left.claude_y").write_bytes(b"")
    env.store_candidates = env.store_candidates + [str(left / "claude-code-sessions")]
    d = ct.discover_stores(env)
    assert d.status == "found"
    assert "user-data-left" not in d.detail


def test_doctor_and_alignment_carry_the_marker_note(mkenv, tmp_path, capsys):
    env = mkenv(tmp_path, n_store_roots=0)
    left = tmp_path / "left"
    left.mkdir()
    (left / ".user-data-left.claude_y").write_bytes(b"")
    env.store_candidates = [str(left / "claude-code-sessions")]
    assert ".user-data-left.claude_y" in ct.gather_doctor(env)["stores"]["detail"]
    assert ".user-data-left.claude_y" in ct.gather_alignment(env)["stores"]["detail"]
    ct.cmd_doctor(env, _ns())
    out = capsys.readouterr().out
    assert "[observed] store: absent" in out and ".user-data-left.claude_y" in out
