import json
import os
import types

import claude_threads as ct


def ns(**kw):
    d = {"json": False, "verbose": False, "query": "", "project": None, "full": False}
    d.update(kw)
    return types.SimpleNamespace(**d)


def test_redact(mkenv, tmp_path):
    env = mkenv(tmp_path)
    s = env.home + "\\x cbc5281b-c0e5-4491-a043-0947c66555bc"
    out = ct.redact(env, s)
    assert env.home not in out and "~" in out
    assert "cbc5281b-c0e5-4491-a043-0947c66555bc" not in out and "cbc5281b" in out


def test_gather_list_merges(mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    write_transcript(env, "C--p-a", "s-listed", [{"cwd": "C:\\p\\a"}])
    write_transcript(env, "C--p-b", "s-cli", [{"cwd": "C:\\p\\b"}])
    write_row(env, 0, "o", "a", "local_1",
              {"sessionId": "local_1", "cliSessionId": "s-listed", "cwd": "C:\\p\\a",
               "title": "Hello", "lastActivityAt": 9})
    items = ct.gather_list(env)
    by_id = {i["session_id"]: i for i in items}
    assert by_id["s-listed"]["title"] == "Hello" and by_id["s-listed"]["listed"]
    assert not by_id["s-cli"]["listed"]
    assert [i["session_id"] for i in ct.gather_list(env, query="hello")] == ["s-listed"]


def test_gather_doctor_findings(mkenv, tmp_path, write_transcript, write_row, capsys):
    env = mkenv(tmp_path)
    write_row(env, 0, "o", "a", "local_blank",
              {"sessionId": "local_blank", "cliSessionId": "", "cwd": "C:\\p"})
    write_row(env, 0, "o", "a", "local_dead",
              {"sessionId": "local_dead", "cliSessionId": "gone", "cwd": "C:\\p",
               "lastActivityAt": 1})
    write_transcript(env, "C--q", "s-unlisted", [{"cwd": "C:\\q"}])
    rep = ct.gather_doctor(env)
    assert rep["blank_rows"] == ["local_blank"]
    assert rep["dead_rows"][0]["local_id"] == "local_dead"
    assert rep["unlisted_transcripts"] == ["s-unlisted"]
    assert rep["exit_code"] == 1
    rc = ct.cmd_doctor(env, ns())
    out = capsys.readouterr().out
    assert rc == 1 and "[hypothesis]" in out and "[observed]" in out


def test_doctor_json_and_exit0(mkenv, tmp_path, capsys):
    env = mkenv(tmp_path)
    rc = ct.cmd_doctor(env, ns(json=True))
    rep = json.loads(capsys.readouterr().out)
    assert rc == 0 and rep["exit_code"] == 0 and rep["stores"]["status"] == "found"


def test_missing_projects_root_no_crash(mkenv, tmp_path, capsys):
    env = mkenv(tmp_path)
    import shutil
    shutil.rmtree(env.projects_root)
    rc = ct.cmd_list(env, ns())
    out = capsys.readouterr().out
    assert rc == 0 and "no threads found" in out
    rc = ct.cmd_doctor(env, ns())
    assert rc == 0


def test_project_prefix_match(mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    write_row(env, 0, "o", "a", "local_1",
              {"sessionId": "local_1", "cliSessionId": "s1", "cwd": "C:\\work\\project-a",
               "lastActivityAt": 10})
    write_row(env, 0, "o", "a", "local_2",
              {"sessionId": "local_2", "cliSessionId": "s2", "cwd": "C:\\work\\project-a\\sub",
               "lastActivityAt": 20})
    write_row(env, 0, "o", "a", "local_3",
              {"sessionId": "local_3", "cliSessionId": "s3", "cwd": "C:\\work\\other",
               "lastActivityAt": 30})
    items = ct.gather_list(env, project="C:\\work\\project-a")
    ids = {i["session_id"] for i in items}
    assert ids == {"s1", "s2"}


def test_full_flag_shows_complete_id(mkenv, tmp_path, write_row, capsys):
    env = mkenv(tmp_path)
    full_sid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    write_row(env, 0, "o", "a", "local_1",
              {"sessionId": "local_1", "cliSessionId": full_sid, "cwd": "C:\\p",
               "lastActivityAt": 10})
    ct.cmd_list(env, ns(full=True))
    out = capsys.readouterr().out
    assert full_sid[:8] in out and "…" not in out


def test_cmd_list_default_redacted(mkenv, tmp_path, write_row, capsys):
    env = mkenv(tmp_path)
    sid = "12345678-9abc-def0-1234-567890abcdef"
    write_row(env, 0, "o", "a", "local_1",
              {"sessionId": "local_1", "cliSessionId": sid, "cwd": env.home + "\\work",
               "lastActivityAt": 10})
    ct.cmd_list(env, ns())
    out = capsys.readouterr().out
    assert "~" in out and "12345678" in out and "…" in out and "-9abc-" not in out


def test_cmd_list_json(mkenv, tmp_path, write_row, capsys):
    env = mkenv(tmp_path)
    write_row(env, 0, "o", "a", "local_1",
              {"sessionId": "local_1", "cliSessionId": "s1", "cwd": "C:\\p",
               "title": "Test", "lastActivityAt": 10})
    ct.cmd_list(env, ns(json=True))
    out = capsys.readouterr().out
    items = json.loads(out)
    assert len(items) == 1 and items[0]["title"] == "Test"


def test_cmd_list_empty(mkenv, tmp_path, capsys):
    env = mkenv(tmp_path)
    ct.cmd_list(env, ns())
    out = capsys.readouterr().out
    assert "no threads found" in out


def test_row_dedup_by_session_id(mkenv, tmp_path, write_row):
    env = mkenv(tmp_path)
    sid = "same-session"
    write_row(env, 0, "o", "a", "local_old",
              {"sessionId": "local_old", "cliSessionId": sid, "cwd": "C:\\old",
               "title": "Old", "lastActivityAt": 10})
    write_row(env, 0, "o", "a", "local_new",
              {"sessionId": "local_new", "cliSessionId": sid, "cwd": "C:\\new",
               "title": "New", "lastActivityAt": 20})
    items = ct.gather_list(env)
    assert len(items) == 1 and items[0]["title"] == "New" and items[0]["cwd"] == "C:\\new"


def test_unknown_layout_finding(mkenv, tmp_path, write_row):
    """Genuine tie: the ONLY row (trivially inside the recent-50 window)
    has both scheme-encoded folders on disk for its cwd, so recent-50
    evidence is exactly tied (1 == 1 > 0) - undecidable, exit 2."""
    env = mkenv(tmp_path)
    import os as os_module
    write_row(env, 0, "o", "a", "local_1",
              {"sessionId": "local_1", "cliSessionId": "s1", "cwd": "C:\\path_with_underscore",
               "lastActivityAt": 10})
    cur_folder = os_module.path.join(env.projects_root, "C--path-with-underscore")
    leg_folder = os_module.path.join(env.projects_root, "C--path_with_underscore")
    os_module.makedirs(cur_folder, exist_ok=True)
    os_module.makedirs(leg_folder, exist_ok=True)
    rep = ct.gather_doctor(env)
    assert rep["encoding_recent"] == {"current": 1, "legacy": 1}
    assert rep["unknown_layout"] == [
        "encoding-scheme evidence is tied/undecidable (recent 50: current=1 legacy=1)"]
    assert rep["exit_code"] == 2


def test_mixed_but_decided_history_is_not_unknown_layout(mkenv, tmp_path, write_row):
    """Ruling fix (Task 13's 'cur > 0 and leg > 0' over ALL rows was wrong):
    a machine that lived through the 2026-07 encoding change legitimately
    has folders under both schemes forever after. One old row sits on a
    project whose folder is shadowed under both encodings (a genuine
    historical artifact - the legacy_folders finding covers it); 50 more-
    recent rows all sit on an unrelated, purely current-scheme project.
    Recent-50 evidence therefore has a clear winner (current), so this must
    be exit 1 via legacy_folders, never exit 2 via unknown_layout."""
    env = mkenv(tmp_path)
    shadowed_cwd = "C:\\legacy_migrated_proj"
    current_cwd = "C:\\current_only_proj"
    os.makedirs(os.path.join(env.projects_root, ct.encode(shadowed_cwd, ct.SCHEME_CURRENT)),
               exist_ok=True)
    os.makedirs(os.path.join(env.projects_root, ct.encode(shadowed_cwd, ct.SCHEME_LEGACY)),
               exist_ok=True)
    os.makedirs(os.path.join(env.projects_root, ct.encode(current_cwd, ct.SCHEME_CURRENT)),
               exist_ok=True)
    write_row(env, 0, "o", "a", "local_old",
              {"sessionId": "local_old", "cliSessionId": "s-old", "cwd": shadowed_cwd,
               "lastActivityAt": 1})
    for i in range(50):
        write_row(env, 0, "o", "a", "local_r{0}".format(i),
                  {"sessionId": "local_r{0}".format(i), "cliSessionId": "s-r{0}".format(i),
                   "cwd": current_cwd, "lastActivityAt": 100 + i})
    rep = ct.gather_doctor(env)
    assert rep["encoding_recent"] == {"current": 1, "legacy": 0}   # clear winner, no tie
    assert rep["unknown_layout"] == []
    assert len(rep["legacy_folders"]) == 1
    assert rep["exit_code"] == 1


def test_doctor_exit2_message(mkenv, tmp_path, write_row, capsys):
    env = mkenv(tmp_path)
    import os as os_module
    write_row(env, 0, "o", "a", "local_1",
              {"sessionId": "local_1", "cliSessionId": "s1", "cwd": "C:\\path_with_underscore",
               "lastActivityAt": 10})
    cur_folder = os_module.path.join(env.projects_root, "C--path-with-underscore")
    leg_folder = os_module.path.join(env.projects_root, "C--path_with_underscore")
    os_module.makedirs(cur_folder, exist_ok=True)
    os_module.makedirs(leg_folder, exist_ok=True)
    rc = ct.cmd_doctor(env, ns())
    out = capsys.readouterr().out
    assert rc == 2 and "unrecognized or unreadable state" in out


def test_doctor_exit2_message_not_on_exit0(mkenv, tmp_path, capsys):
    env = mkenv(tmp_path)
    rc = ct.cmd_doctor(env, ns())
    out = capsys.readouterr().out
    assert rc == 0 and "unrecognized or unreadable state" not in out
