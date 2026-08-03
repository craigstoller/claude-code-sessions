import json
import os
import sys

import pytest

import claude_threads as ct

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
                                  (99999, "claude-code-threads.exe"),
                                  (99998, r"C:\pipx\venvs\claude-code-threads\cc-threads.exe")]
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


# ------------------------------------------- RULING 4: claude_running narrowing

DESKTOP_EXE = ("c:\\program files\\windowsapps\\"
               "claude_1.24012.9.0_x64__pzs8sxrjxfjjc\\app\\claude.exe")
CLI_EXE = "c:\\users\\u\\appdata\\roaming\\claude\\claude-code\\2.1.219\\claude.exe"
CLI_SHIM = "c:\\users\\u\\.local\\bin\\claude.exe"


class TestClaudeRunningNarrowing:
    def test_desktop_app_path_matches(self, mkenv, tmp_path):
        env = mkenv(tmp_path)
        env.process_lister = lambda: [(99999, DESKTOP_EXE)]
        assert ct.claude_running(env) == [DESKTOP_EXE]

    def test_cli_paths_do_not_match(self, mkenv, tmp_path):
        env = mkenv(tmp_path)
        env.process_lister = lambda: [(99999, CLI_EXE), (99998, CLI_SHIM)]
        assert ct.claude_running(env) == []

    def test_bare_image_name_fails_closed(self, mkenv, tmp_path):
        # tasklist fallback yields names only; unclassifiable claude.exe
        # counts as the desktop app, never as its absence
        env = mkenv(tmp_path)
        env.process_lister = lambda: [(99999, "claude.exe")]
        assert ct.claude_running(env) == ["claude.exe"]

    def test_non_msix_desktop_install_still_matches(self, mkenv, tmp_path):
        env = mkenv(tmp_path)
        exe = "c:\\users\\u\\appdata\\local\\programs\\claude\\claude.exe"
        env.process_lister = lambda: [(99999, exe)]
        assert ct.claude_running(env) == [exe]

    def test_desktop_under_an_unlucky_parent_dir_still_matches(self, mkenv, tmp_path):
        # adversarial: a USER named claude-code must not turn their desktop
        # app into a "CLI" and disable the guard - the CLI markers are path
        # segments, not substrings
        env = mkenv(tmp_path)
        exe = "c:\\users\\claude-code\\appdata\\local\\programs\\claude\\claude.exe"
        env.process_lister = lambda: [(99999, exe)]
        assert ct.claude_running(env) == [exe]

    def test_desktop_under_a_bare_claude_claude_code_path_still_matches(
            self, mkenv, tmp_path):
        # adversarial: the CLI marker is anchored to the measured
        # appdata\roaming location - a desktop binary under some other
        # claude\claude-code directory must stay a desktop match
        env = mkenv(tmp_path)
        exe = "d:\\claude\\claude-code\\claude.exe"
        env.process_lister = lambda: [(99999, exe)]
        assert ct.claude_running(env) == [exe]


    def test_forward_slash_cli_path_is_still_the_cli(self, mkenv, tmp_path):
        # separators must be normalised before matching - a forward-slash
        # CLI path must not be misread as the desktop app
        env = mkenv(tmp_path)
        exe = "c:/users/u/appdata/roaming/claude/claude-code/2.1.219/claude.exe"
        env.process_lister = lambda: [(99999, exe)]
        assert ct.claude_running(env) == []

    def test_lister_that_raises_fails_closed(self, mkenv, tmp_path):
        env = mkenv(tmp_path)

        def boom():
            raise OSError("no process API here")
        env.process_lister = boom
        running = ct.claude_running(env)
        assert running and "unavailable" in running[0]

    def test_posix_shim_with_args_is_recognised_as_cli(self, mkenv, tmp_path):
        # REVIEW FINDING 1: `ps -A -o args=` reports the full command line,
        # so an invocation through the shim carries trailing arguments and
        # never matches an ENDS-WITH check. The marker must also match as a
        # CONTAINS check (marker followed by a space). A desktop-ish control
        # path with no shim marker must keep matching as the desktop app.
        env = mkenv(tmp_path)
        env.process_lister = lambda: [
            (43, "/home/u/.local/bin/claude --resume x"),
            (44, "/applications/claude.app/contents/macos/claude"),
        ]
        assert ct.claude_running(env) == [
            "/applications/claude.app/contents/macos/claude"]


def test_parse_proc_lines_prefers_path_falls_back_to_name_skips_garbage():
    out = ("12|Claude.exe|C:\\Program Files\\WindowsApps\\Claude_1\\app\\Claude.exe\n"
           "13|svchost.exe|\n"
           "not a pid line\n"
           "x|bad.exe|c:\\bad\n")
    assert ct._parse_proc_lines(out) == [
        (12, "c:\\program files\\windowsapps\\claude_1\\app\\claude.exe"),
        (13, "svchost.exe"),
    ]


@pytest.mark.skipif(sys.platform != "win32", reason="windows lister branch")
def test_default_lister_falls_back_to_tasklist_when_cim_is_garbage(monkeypatch):
    import subprocess as sp
    calls = []

    class R:
        def __init__(self, rc, out):
            self.returncode = rc
            self.stdout = out

    def fake_run(cmd, **kw):
        calls.append(cmd[0])
        if cmd[0] == "powershell":
            return R(0, "no pipes in this output at all\n")
        return R(0, '"Image Name","PID","Session","Num","Mem"\n'
                    '"claude.exe","123","Console","1","9,000 K"\n')
    monkeypatch.setattr(sp, "run", fake_run)
    out = ct._default_process_lister()
    assert calls == ["powershell", "tasklist"]
    assert (123, "claude.exe") in out


@pytest.mark.skipif(sys.platform != "win32", reason="windows lister branch")
def test_default_lister_reports_unavailable_when_everything_fails(monkeypatch):
    import subprocess as sp

    def fake_run(cmd, **kw):
        raise OSError("blocked")
    monkeypatch.setattr(sp, "run", fake_run)
    out = ct._default_process_lister()
    assert out and out[0][0] == -1 and "unavailable" in out[0][1]


@pytest.mark.skipif(sys.platform != "win32", reason="windows lister branch")
def test_default_lister_treats_empty_successful_output_as_unavailable(monkeypatch):
    # rc 0 with nothing parseable: a real machine never has zero processes,
    # so an empty enumeration is unusable output, not an empty system
    import subprocess as sp

    class R:
        returncode = 0
        stdout = ""

    monkeypatch.setattr(sp, "run", lambda cmd, **kw: R())
    out = ct._default_process_lister()
    assert out and out[0][0] == -1 and "unavailable" in out[0][1]


def test_default_lister_posix_branch_reports_unavailable_on_empty_ps(monkeypatch):
    # REVIEW FINDING 2: the POSIX branch's guarded return had zero coverage
    # because every lister test above is skipif'd to win32 only. Force the
    # POSIX branch by patching sys.platform itself (not skipif'd, runs on
    # Windows CI too) so an rc-0/empty `ps` result is proven to still return
    # the fail-closed sentinel rather than an empty "nothing running" list.
    import subprocess as sp
    monkeypatch.setattr(ct.sys, "platform", "linux")

    class R:
        returncode = 0
        stdout = ""

    monkeypatch.setattr(sp, "run", lambda cmd, **kw: R())
    out = ct._default_process_lister()
    assert out and out[0][0] == -1 and "unavailable" in out[0][1]
