import os

import claude_threads as ct


def test_loads_rows_and_skips_non_rows(mkenv, tmp_path, write_row):
    env = mkenv(tmp_path)
    write_row(env, 0, "org", "acct", "local_a", {"sessionId": "local_a", "cliSessionId": "s1", "cwd": "C:\\p", "lastActivityAt": 5})
    d = os.path.join(env.store_candidates[0], "org", "acct")
    open(os.path.join(d, "scheduled-tasks.json"), "w").write("{}")
    open(os.path.join(d, "deleted_x"), "w").write("")
    open(os.path.join(d, "local_b.json.tmp"), "w").write("{")
    rows, errors = ct.load_rows(env.store_candidates)
    assert len(rows) == 1 and errors == []
    r = rows[0]
    assert (r.local_id, r.cli_session_id, r.cwd, r.last_activity) == ("local_a", "s1", "C:\\p", 5)


def test_unreadable_row_reported(mkenv, tmp_path, write_row):
    env = mkenv(tmp_path)
    p = write_row(env, 0, "org", "acct", "local_bad", {"x": 1})
    open(p, "w").write("{corrupt")
    rows, errors = ct.load_rows(env.store_candidates)
    assert rows == [] and len(errors) == 1 and "local_bad" in errors[0]
