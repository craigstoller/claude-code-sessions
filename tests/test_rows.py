import json
import os

import pytest

import claude_session_store as ct


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


# ------------------------------------------------------------------- I2


def test_non_dict_row_reported_not_crashed(mkenv, tmp_path, write_row):
    """I2: a row file whose top-level JSON parses fine but is not an object
    (e.g. a bare list) must not become a Row - every Row property assumes
    dict.get() and would raise AttributeError. It must be reported in the
    errors list instead, with no traceback."""
    env = mkenv(tmp_path)
    p = write_row(env, 0, "org", "acct", "local_list", {"placeholder": 1})
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(["not", "a", "dict"], fh)
    rows, errors = ct.load_rows(env.store_candidates)
    assert rows == []
    assert len(errors) == 1
    assert "not a JSON object" in errors[0] and "local_list" in errors[0]


def test_non_dict_row_doctor_exit2_no_crash(mkenv, tmp_path, write_row):
    """I2: doctor must surface a non-dict row as a fail-closed finding (exit
    2), not crash with an unhandled AttributeError."""
    env = mkenv(tmp_path)
    p = write_row(env, 0, "org", "acct", "local_list", {"placeholder": 1})
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(["not", "a", "dict"], fh)
    rep = ct.gather_doctor(env)
    assert rep["exit_code"] == 2
    assert len(rep["row_errors"]) == 1


def test_non_dict_row_move_refuses_fail_closed(mkenv, tmp_path, write_row,
                                               write_transcript):
    """I2: plan_move's existing fail-closed row-errors path must catch this
    too, refusing the move rather than crashing while scanning rows."""
    env = mkenv(tmp_path)
    p = write_row(env, 0, "org", "acct", "local_list", {"placeholder": 1})
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(["not", "a", "dict"], fh)
    src = "C:\\proj\\src"
    target = str(tmp_path / "target")
    os.makedirs(target)
    write_transcript(env, ct.encode(src, ct.SCHEME_CURRENT), "some-sess", [{"cwd": src}])
    with pytest.raises(ct.LayoutError, match="unreadable listing rows"):
        ct.plan_move(env, "some-sess", target, ct.MoveFlags())
