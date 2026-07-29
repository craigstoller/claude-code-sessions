import json
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
