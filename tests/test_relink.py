"""`relink`: give a row the link and chain its conversation already has elsewhere.

Implements docs/specs/2026-11-19-relink-design.md. This file starts with the
value helpers every later step builds on: the app-written shape check, the
key-by-key apply and the state comparison. Fake cast only: Northwind, the
local_p/local_r ids of the converge tests.
"""
import pytest

import claude_code_sessions as ct
from test_converge import TASK, side_link


@pytest.fixture(autouse=True)
def _reset():
    ct._ANONYMIZE = False
    ct._ANON_CACHE.clear()
    ct._crash_hook = None
    yield
    ct._ANONYMIZE = False
    ct._ANON_CACHE.clear()
    ct._crash_hook = None


def test_app_written_is_the_two_app_shapes_only():
    assert ct._relink_app_written(side_link("local_p1"))
    assert ct._relink_app_written({"sessionId": "local_p1", "taskId": TASK, "title": "Northwind"})
    for link in ({"sessionId": "local_p1", "taskId": TASK},                    # this tool's own
                 {"sessionId": "local_p1", "taskId": TASK, "title": ""},
                 {"sessionId": "local_p1", "taskId": TASK, "title": 7},
                 {"sessionId": "local_p1", "taskId": TASK, "title": "N", "pendingPop": True},
                 {"sessionId": "local_p1", "sideSession": "true"}, None, "local_p1", [], {}):
        assert not ct._relink_app_written(link)


def test_apply_new_sets_in_place_or_appends_and_copies():
    d = {"a": 1, "spawnedFrom": None, "z": 2}
    out = ct._relink_apply_new(d, {"spawnedFrom": {"sessionId": "local_p2", "taskId": TASK},
                                   "startChain": {"firstSessionId": "local_r2", "depth": 2}})
    assert list(out) == ["a", "spawnedFrom", "z", "startChain"]
    assert d == {"a": 1, "spawnedFrom": None, "z": 2}


def test_apply_old_pops_an_absent_key_and_restores_a_non_object_exactly():
    cur = {"a": 1, "spawnedFrom": {"sessionId": "local_p2", "taskId": TASK}, "z": 2}
    assert ct._relink_apply_old(cur, {"spawnedFrom": {"present": False, "value": None}}) \
        == {"a": 1, "z": 2}
    out = ct._relink_apply_old(cur, {"spawnedFrom": {"present": True, "value": False}})
    assert list(out) == ["a", "spawnedFrom", "z"] and out["spawnedFrom"] is False
    assert ct._relink_apply_old(cur, {"spawnedFrom": {"present": False, "value": None}},
                                keys=()) == cur


def test_key_state_compares_by_value_and_type():
    new = {"firstSessionId": "local_r2", "depth": 1}
    absent = {"present": False, "value": None}
    assert ct._relink_key_state({"startChain": {"depth": 1, "firstSessionId": "local_r2"}},
                                "startChain", absent, new) == "new"       # member order ignored
    assert ct._relink_key_state({"startChain": {"firstSessionId": "local_r2", "depth": True}},
                                "startChain", absent, new) == "other"
    assert ct._relink_key_state({}, "startChain", absent, new) == "old"
    assert ct._relink_key_state({"startChain": None}, "startChain",
                                {"present": True, "value": None}, new) == "old"


def test_row_bytes_are_compact_json():
    assert ct._relink_row_bytes({"a": "é", "b": [1]}) == b'{"a":"\\u00e9","b":[1]}'
