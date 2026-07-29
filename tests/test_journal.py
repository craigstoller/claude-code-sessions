import json
import os

import pytest

import claude_threads as ct


def _op(env, **extra):
    m = {"op_type": "move", "session_id": "s1"}
    m.update(extra)
    return ct.new_op(env, m)


def test_new_op_journaled_with_history(mkenv, tmp_path):
    env = mkenv(tmp_path)
    op = _op(env)
    assert op.manifest["status"] == "journaled"
    assert os.path.isfile(os.path.join(op.op_dir, "manifest.json"))
    assert op.manifest["history"][0]["status"] == "journaled"


def test_set_status_persists(mkenv, tmp_path):
    env = mkenv(tmp_path)
    op = _op(env)
    ct.set_status(op, "copying")
    reloaded = ct.list_ops(env)[0]
    assert reloaded.manifest["status"] == "copying"
    assert [h["status"] for h in reloaded.manifest["history"]] == ["journaled", "copying"]


def test_rotation_prunes_only_terminal_beyond_10(mkenv, tmp_path):
    env = mkenv(tmp_path)
    ops = [_op(env, session_id="s%d" % i) for i in range(13)]
    for o in ops[:12]:
        ct.set_status(o, "completed")
    # ops[12] stays non-terminal
    pruned = ct.rotate_ops(env)
    left = ct.list_ops(env)
    assert len(pruned) == 2
    assert len(left) == 11                       # 10 terminal + 1 non-terminal
    assert any(o.manifest["status"] == "journaled" for o in left)


def test_lock_exclusive_and_stale_detection(mkenv, tmp_path):
    env = mkenv(tmp_path)
    ct.acquire_lock(env, "op-1")
    with pytest.raises(ct.Refusal):
        ct.acquire_lock(env, "op-2")
    ct.release_lock(env)
    # stale: write a lock with a dead pid
    with open(os.path.join(env.ops_dir, "lock"), "w") as fh:
        fh.write("999999999 op-x")
    assert ct.lock_is_stale(env) is True


def test_moved_log_roundtrip(mkenv, tmp_path):
    env = mkenv(tmp_path)
    ct.append_moved_log(env, {"kind": "move", "session_id": "a", "from": "x", "to": "y"})
    ct.append_moved_log(env, {"kind": "move", "session_id": "b", "from": "x", "to": "y"})
    ct.append_moved_log(env, {"kind": "undo", "session_id": "b"})
    assert ct.moved_session_ids(env) == {"a"}
