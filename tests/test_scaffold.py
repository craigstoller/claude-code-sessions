import claude_code_sessions as ct


def test_env_and_exceptions_exist(mkenv, tmp_path):
    env = mkenv(tmp_path)
    assert env.projects_root.endswith("projects")
    assert ct.Refusal("x").exit_code == 1
    assert ct.LayoutError("x").exit_code == 2
    assert ct.SCHEME_CURRENT == r"[^A-Za-z0-9]"
    assert ct.SCHEME_LEGACY == r"[^A-Za-z0-9_]"


def test_fixture_builders(mkenv, tmp_path, write_transcript, write_row):
    env = mkenv(tmp_path)
    t = write_transcript(env, "C--proj-alpha", "aaaa-bbbb", [{"cwd": "C:\\proj\\alpha"}])
    r = write_row(env, 0, "org1", "acct1", "local_x1", {"sessionId": "local_x1", "cliSessionId": "aaaa-bbbb", "cwd": "C:\\proj\\alpha"})
    import os
    assert os.path.exists(t) and os.path.exists(r)
