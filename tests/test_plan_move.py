import json
import os

import pytest

import claude_session_store as ct

SID = "cbc9-sess"


@pytest.fixture
def setup(mkenv, tmp_path, write_transcript, write_row):
    """Desktop machine: one store root, one row, transcript in C--src, target dir exists."""
    env = mkenv(tmp_path)
    src_cwd = "C:\\proj\\src"
    target = str(tmp_path / "target")
    os.makedirs(target)
    t = write_transcript(env, ct.encode(src_cwd, ct.SCHEME_CURRENT), SID, [{"cwd": src_cwd}])
    # evidence folder so scheme detection resolves (underscore path disambiguates)
    os.makedirs(os.path.join(env.projects_root, ct.encode("C:\\proj\\_ev", ct.SCHEME_CURRENT)))
    write_row(env, 0, "org", "acct", "local_r1",
              {"sessionId": "local_r1", "cliSessionId": SID, "cwd": src_cwd,
               "originCwd": src_cwd, "lastActivityAt": 1, "title": "T"})
    write_row(env, 0, "org", "acct", "local_ev",
              {"sessionId": "local_ev", "cliSessionId": "other", "cwd": "C:\\proj\\_ev",
               "lastActivityAt": 2})
    return env, t, target


def flags(**kw):
    return ct.MoveFlags(**kw)


def test_happy_desktop(setup):
    env, t, target = setup
    m = ct.plan_move(env, SID, target, flags())
    assert m["mode"] == "desktop"
    assert m["source_transcript"] == t
    assert m["dest_transcript"].endswith(SID + ".jsonl")
    assert len(m["rows"]) == 1
    post = json.loads(ct.unb64(m["rows"][0]["post_b64"]))
    assert post["cwd"] == os.path.normpath(target) and post["cliSessionId"] == SID


def test_store_error_fatal(setup, monkeypatch):
    env, t, target = setup
    monkeypatch.setattr(ct, "discover_stores",
                        lambda e: ct.StoreDiscovery("error", [], "boom"))
    with pytest.raises(ct.LayoutError):
        ct.plan_move(env, SID, target, flags())


def test_absent_store_needs_transcript_only(setup, monkeypatch, mkenv, tmp_path,
                                            write_transcript):
    env = mkenv(tmp_path / "cli", n_store_roots=0)
    env.store_candidates = []
    src = "C:\\p\\src"
    write_transcript(env, ct.encode(src, ct.SCHEME_CURRENT), SID, [{"cwd": src}])
    # evidence transcript so scheme detection resolves regardless of what
    # characters pytest's own tmp_path happens to contain (it may include
    # underscores from the test name, which would otherwise make the target
    # itself scheme-ambiguous and mask the refusal this test is checking for)
    write_transcript(env, ct.encode("C:\\p\\_ev", ct.SCHEME_CURRENT), "decoy-ev",
                     [{"cwd": "C:\\p\\_ev"}])
    target = str(tmp_path / "cli-target")
    os.makedirs(target)
    with pytest.raises(ct.Refusal, match="transcript-only"):
        ct.plan_move(env, SID, target, flags())
    m = ct.plan_move(env, SID, target, flags(transcript_only=True))
    assert m["mode"] == "transcript_only" and m["rows"] == []


def test_store_present_no_row_refuses_without_flag(setup, write_row):
    env, t, target = setup
    os.unlink([r.path for r in ct.load_rows(env.store_candidates)[0]
               if r.cli_session_id == SID][0])
    with pytest.raises(ct.Refusal, match="orphan"):
        ct.plan_move(env, SID, target, flags())
    m = ct.plan_move(env, SID, target, flags(transcript_only=True))
    assert m["mode"] == "transcript_only"


def test_nonwindows_desktop_needs_unverified_platform(setup):
    env, t, target = setup
    env.is_windows = False
    with pytest.raises(ct.Refusal, match="unverified-platform"):
        ct.plan_move(env, SID, target, flags())
    assert ct.plan_move(env, SID, target, flags(unverified_platform=True))["mode"] == "desktop"


def test_missing_and_ambiguous_transcript(setup, write_transcript):
    env, t, target = setup
    with pytest.raises(ct.Refusal, match="[Nn]o transcript"):
        ct.plan_move(env, "nope", target, flags())
    write_transcript(env, "C--elsewhere", SID, [{}])
    with pytest.raises(ct.Refusal, match="[Aa]mbiguous"):
        ct.plan_move(env, SID, target, flags())


def test_target_must_exist_and_not_be_forbidden(setup):
    env, t, target = setup
    with pytest.raises(ct.Refusal, match="exist"):
        ct.plan_move(env, SID, target + "-nope", flags())
    with pytest.raises(ct.Refusal, match="refus"):
        ct.plan_move(env, SID, os.path.join(env.home, ".claude"), flags())


def test_dest_transcript_collision_refused(setup, write_transcript):
    env, t, target = setup
    write_transcript(env, ct.encode(os.path.normpath(target), ct.SCHEME_CURRENT), SID, [{}])
    with pytest.raises(ct.Refusal, match="already exists"):
        ct.plan_move(env, SID, target, flags())


def test_dest_folder_cwd_collision_scan(setup, write_transcript):
    env, t, target = setup
    # foreign transcript whose LAST cwd is a different real path -> refuse
    write_transcript(env, ct.encode(os.path.normpath(target), ct.SCHEME_CURRENT),
                     "foreign", [{"cwd": "C:\\entirely\\different"}])
    with pytest.raises(ct.Refusal, match="collision"):
        ct.plan_move(env, SID, target, flags())


def test_moved_log_excuses_collision(setup, write_transcript):
    env, t, target = setup
    write_transcript(env, ct.encode(os.path.normpath(target), ct.SCHEME_CURRENT),
                     "moved-one", [{"cwd": "C:\\entirely\\different"}])
    ct.append_moved_log(env, {"kind": "move", "session_id": "moved-one",
                              "from": "C:\\entirely\\different", "to": os.path.normpath(target)})
    assert ct.plan_move(env, SID, target, flags())["mode"] == "desktop"


def test_row_adoption_rules(setup, write_row):
    env, t, target = setup
    write_row(env, 0, "org", "acct", "local_orph",
              {"sessionId": "local_orph", "cliSessionId": "", "cwd": "C:\\x", "title": "O"})
    write_row(env, 0, "org", "acct", "local_alive",
              {"sessionId": "local_alive", "cliSessionId": "someone-else", "cwd": "C:\\x"})
    m = ct.plan_move(env, SID, target, flags(row=["local_orph"], yes=True))
    assert len(m["rows"]) == 2
    with pytest.raises(ct.Refusal, match="never adoptable"):
        ct.plan_move(env, SID, target, flags(row=["local_alive"], yes=True))
    with pytest.raises(ct.Refusal, match="confirmation"):
        ct.plan_move(env, SID, target, flags(row=["local_orph"]))  # no --yes


def test_row_adoption_across_stores(mkenv, tmp_path, write_transcript, write_row):
    """The same local id can legitimately appear once per org/account store
    (a per-account copy of the same desktop-app row); --row must adopt every
    copy, not just the first one found."""
    env = mkenv(tmp_path, n_store_roots=2)
    src_cwd = "C:\\proj\\src"
    target = str(tmp_path / "target")
    os.makedirs(target)
    write_transcript(env, ct.encode(src_cwd, ct.SCHEME_CURRENT), SID, [{"cwd": src_cwd}])
    os.makedirs(os.path.join(env.projects_root, ct.encode("C:\\proj\\_ev", ct.SCHEME_CURRENT)))
    write_row(env, 0, "org", "acct", "local_ev",
              {"sessionId": "local_ev", "cliSessionId": "other", "cwd": "C:\\proj\\_ev",
               "lastActivityAt": 2})
    write_row(env, 0, "org", "acct", "local_orph",
              {"sessionId": "local_orph", "cliSessionId": "", "cwd": "C:\\x", "title": "O"})
    write_row(env, 1, "org2", "acct2", "local_orph",
              {"sessionId": "local_orph", "cliSessionId": "", "cwd": "C:\\x", "title": "O"})
    m = ct.plan_move(env, SID, target, flags(row=["local_orph"], yes=True))
    assert len(m["rows"]) == 2
    got = {os.path.realpath(r["path"]) for r in m["rows"]}
    expected = {
        os.path.realpath(os.path.join(env.store_candidates[0], "org", "acct", "local_orph.json")),
        os.path.realpath(os.path.join(env.store_candidates[1], "org2", "acct2", "local_orph.json")),
    }
    assert got == expected


def test_sidecar_in_manifest(setup):
    env, t, target = setup
    side = ct.sidecar_path(t)
    os.makedirs(os.path.join(side, "nested"))
    with open(os.path.join(side, "a.bin"), "wb") as fh:
        fh.write(b"hello")
    with open(os.path.join(side, "nested", "b.bin"), "wb") as fh:
        fh.write(b"world!!")
    m = ct.plan_move(env, SID, target, flags())
    assert m["sidecar_source"] == side
    assert m["sidecar_dest"] == ct.sidecar_path(m["dest_transcript"])
    rels = {e["rel"]: e for e in m["sidecar_inventory"]}
    assert set(rels) == {"a.bin", "nested/b.bin"}
    for rel, entry in rels.items():
        full = os.path.join(side, rel.replace("/", os.sep))
        exp_hash, exp_size = ct.sha256_file(full)
        assert entry["sha256"] == exp_hash
        assert entry["size"] == exp_size
    exp_t_hash, exp_t_size = ct.sha256_file(t)
    assert m["transcript_sha256"] == exp_t_hash
    assert m["transcript_size"] == exp_t_size
    assert m["target_cwd"] == os.path.normpath(target)


def test_process_guard(setup):
    env, t, target = setup
    env.process_lister = lambda: [(99999, "claude.exe")]
    with pytest.raises(ct.Refusal, match="running"):
        ct.plan_move(env, SID, target, flags())


def test_process_guard_ignores_self_and_own_tool(setup):
    env, t, target = setup
    env.process_lister = lambda: [(os.getpid(), "claude something"),
                                  (os.getppid(), "claude something else"),
                                  (99999, "claude-code-session-store.exe"),
                                  (99998, r"C:\pipx\venvs\claude-code-session-store\cc-store.exe")]
    assert ct.plan_move(env, SID, target, flags())["mode"] == "desktop"


def test_mtime_guard_force(setup):
    env, t, target = setup
    env.now = lambda: os.path.getmtime(t) + 60      # transcript written 1 min "ago"
    with pytest.raises(ct.Refusal, match="minutes"):
        ct.plan_move(env, SID, target, flags())
    assert ct.plan_move(env, SID, target, flags(force=True))


def test_malformed_row_anywhere_is_fatal(setup):
    env, t, target = setup
    bad = os.path.join(env.store_candidates[0], "org", "acct", "local_zz.json")
    open(bad, "w").write("{broken")
    with pytest.raises(ct.LayoutError):
        ct.plan_move(env, SID, target, flags())
