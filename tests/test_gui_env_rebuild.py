"""The window's Env, rebuilt at each read and before each engine call
(docs/internals.md, "Amendment 2026-10-20 (Env rebuild)").

The window used to build its Env once, at open, so a Desktop data-root move
made while it was open was seen only after a restart, and a copied old folder
stayed the store it planned into and wrote to. The maintainer's ruling of
2026-10-20:

1. every read builds a fresh Env, and every engine call in a press's worker
   runs on one built just before it;
2. a press whose store roots changed since its plan stops before the next
   engine call and replans;
3. the held identity answer is bound to the layout it was given under and
   dropped when a clean fresh Env shows another;
4. a layout error is a state, not a change, and a LayoutError reads as a
   refusal.

These are the pure helpers and the Level sequence behind that, importable
without tkinter. Fake ids only.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import claude_code_sessions as ct  # noqa: E402
import claude_code_sessions_gui as gui  # noqa: E402
from test_journal_reroot import packaged, rebuild, relocate  # noqa: E402

A = "aaaa1111" + "0" * 24
B = "bbbb2222" + "0" * 24


def _key(*paths):
    return tuple(sorted(os.path.normcase(os.path.realpath(p)) for p in paths))


# ------------------------------------------------------------ the layout key

def test_the_layout_is_the_store_roots_discovery_finds(mkenv, tmp_path):
    env = mkenv(tmp_path)
    la, claude = packaged(env, tmp_path)
    rebuild(env, la)
    assert gui._layout_key(env) == _key(os.path.join(claude, "claude-code-sessions"))


def test_a_renaming_move_changes_the_layout(mkenv, tmp_path):
    env = mkenv(tmp_path)
    la, claude = packaged(env, tmp_path)
    rebuild(env, la)
    before = gui._layout_key(env)
    new = relocate(env, la, claude)
    assert gui._layout_key(env) == _key(new) != before


def test_a_copying_move_changes_the_layout_the_old_copy_is_no_root(mkenv, tmp_path):
    env = mkenv(tmp_path)
    la, claude = packaged(env, tmp_path)
    rebuild(env, la)
    before = gui._layout_key(env)
    new = relocate(env, la, claude, copy=True)
    assert os.path.isdir(os.path.join(claude, "claude-code-sessions"))
    assert gui._layout_key(env) == _key(new) != before


def test_an_empty_package_folder_appearing_changes_nothing(mkenv, tmp_path):
    env = mkenv(tmp_path)
    la, claude = packaged(env, tmp_path)
    rebuild(env, la)
    before = gui._layout_key(env)
    os.makedirs(os.path.join(la, "Claude-Data", "claude_other"))
    rebuild(env, la)
    assert env.store_candidates != [os.path.join(claude, "claude-code-sessions")]
    assert gui._layout_key(env) == before


def test_no_store_anywhere_is_an_empty_layout_not_an_error(mkenv, tmp_path):
    env = mkenv(tmp_path)
    env.store_candidates = [str(tmp_path / "nowhere" / "claude-code-sessions")]
    assert gui._layout_key(env) == ()


def test_a_layout_error_has_no_layout(mkenv, tmp_path):
    env = mkenv(tmp_path)
    env.layout_errors = ["Claude-Data: Access is denied"]
    assert gui._layout_key(env) is None


def test_discovery_that_raises_has_no_layout(mkenv, tmp_path, monkeypatch):
    env = mkenv(tmp_path)

    def boom(_env):
        raise OSError("simulated")
    monkeypatch.setattr(gui.ccs, "discover_stores", boom)
    assert gui._layout_key(env) is None


# --------------------------------------------------------- the rebuild helper

def test_fresh_env_is_a_new_default_env(mkenv, tmp_path, monkeypatch):
    prev = mkenv(tmp_path / "a")
    new = mkenv(tmp_path / "b")
    monkeypatch.setattr(gui.ccs, "default_env", lambda: new)
    assert gui._fresh_env(prev) is new


def test_a_rebuild_that_raises_is_an_error_state(mkenv, tmp_path, monkeypatch):
    prev = mkenv(tmp_path)
    prev.store_moves = [("old", "new")]

    def boom():
        raise OSError("simulated listing failure")
    monkeypatch.setattr(gui.ccs, "default_env", boom)
    env = gui._fresh_env(prev)
    assert env is not prev and env.home == prev.home
    assert any("simulated listing failure" in e for e in env.layout_errors)
    assert env.store_moves == []
    assert prev.layout_errors == [] and prev.store_moves == [("old", "new")]
    assert gui._layout_key(env) is None


# ------------------------------------------------- the held answer's layout

def test_the_answer_is_dropped_only_by_a_clean_different_layout():
    L1, L2 = ("/r1",), ("/r2",)
    held = {"path": "/r1/" + A, "pair": (A, B), "label": "", "layout": L1}
    nxt = gui._live_answer_next
    assert nxt(held, "layout_changed", L2) is None
    assert nxt(held, "layout_changed", L1) == held
    assert nxt(held, "layout_changed", None) == held      # an error state
    assert nxt(held, "layout_changed", ()) is None        # no store at all
    unbound = dict(held)
    del unbound["layout"]
    assert nxt(unbound, "layout_changed", L2) == unbound  # given before this
    assert nxt(None, "layout_changed", L2) is None


def test_a_layout_stop_keeps_the_answer_as_a_refusal_does():
    assert gui._live_event_for("layout") == "refused"


# ------------------------------------------- the Level sequence's per-call Env

class _UI(object):
    def __init__(self, confirm=True, on_confirm=None, ask=None):
        self.statuses, self.gate_envs = [], []
        self.remaining, self.truncate = 0, False
        self.landed_keys, self.current_key = [], None
        self._confirm, self._on_confirm, self._ask = confirm, on_confirm, ask

    def status(self, text):
        self.statuses.append(text)

    def gate(self):
        self.gate_envs.append(getattr(self, "env", None))
        return None

    def truncate_requested(self):
        return self.truncate

    def confirm_stage2(self, fresh):
        if self._on_confirm:
            self._on_confirm()
        return self._confirm

    def ask_live(self, fresh):
        return self._ask


class _World(object):
    """Engine calls recorded with the Env each got; fresh() hands out Envs
    named e1, e2, ... whose layout is whatever `layout` holds when built."""

    def __init__(self, monkeypatch, rows=1):
        self.calls, self.n, self.layout, self.keys = [], 0, ("/r1",), {}
        self.rows = rows
        monkeypatch.setattr(gui, "_layout_key", lambda env: self.keys.get(env, env))
        monkeypatch.setattr(gui.ccs, "plan_retitle", self._plan_retitle)
        monkeypatch.setattr(gui.ccs, "run_retitle", self._rec("run_retitle"))
        monkeypatch.setattr(gui.ccs, "plan_converge", self._plan_converge)
        monkeypatch.setattr(gui.ccs, "run_converge", self._run_converge)
        monkeypatch.setattr(gui.ccs, "gather_alignment", self._rec("gather_alignment", {}))
        monkeypatch.setattr(gui.ccs, "claude_running", self._rec("claude_running", []))

    def fresh(self, prev):
        self.n += 1
        env = "e%d" % self.n
        self.keys[env] = self.layout
        return env

    def _rec(self, name, result=None):
        def f(env, *a):
            self.calls.append((name, env))
            return result
        return f

    def _plan_retitle(self, env, flags):
        self.calls.append(("plan_retitle", env))
        return {"op_type": "retitle"}

    def _plan_converge(self, env, flags):
        self.calls.append(("plan_converge", env, flags.live))
        return {"rows": [{}] * self.rows, "holds": []}

    def _run_converge(self, env, manifest):
        self.calls.append(("run_converge", env))
        return "completed"

    def names(self):
        return [c[0] for c in self.calls]

    def envs(self, name):
        return [c[1] for c in self.calls if c[0] == name]


STEPS = [{"key": ("s1", "t"), "target_sid": "s1", "new_title": "One"},
         {"key": ("s2", "t"), "target_sid": "s2", "new_title": "Two"}]


def test_each_engine_call_gets_an_env_built_just_before_it(monkeypatch):
    w = _World(monkeypatch)
    ui = _UI()
    seq = gui._run_level_sequence("e0", STEPS, "", ui, fresh=w.fresh, layout=("/r1",))
    assert seq["stage2"] == "completed" and seq["landed"] == 2
    used = [c[1] for c in w.calls]
    assert "e0" not in used                               # never the press's
    assert len(set(used)) == len(used)                    # one per call
    assert ui.gate_envs and all(e and e != "e0" for e in ui.gate_envs)


def test_a_move_before_a_rename_stops_before_it(monkeypatch):
    w = _World(monkeypatch)

    def run_and_move(env, pm):
        w.calls.append(("run_retitle", env))
        w.layout = ("/r2",)                               # the app moved its data
    monkeypatch.setattr(gui.ccs, "run_retitle", run_and_move)
    seq = gui._run_level_sequence("e0", STEPS, "", _UI(), fresh=w.fresh,
                                  layout=("/r1",))
    assert seq["stage2"] == "layout" and seq["layout_stop"] == "layout_changed"
    assert seq["landed"] == 1 and seq["mutated"] is True
    assert w.names().count("plan_retitle") == 1           # the second never planned
    assert "plan_converge" not in w.names() and "run_converge" not in w.names()


def test_a_move_during_the_stage2_dialog_stops_before_the_copy(monkeypatch):
    w = _World(monkeypatch)

    def move():
        w.layout = ("/r2",)
    seq = gui._run_level_sequence("e0", [], "", _UI(on_confirm=move), fresh=w.fresh,
                                  layout=("/r1",))
    assert seq["stage2"] == "layout" and seq["layout_stop"] == "layout_changed"
    assert "plan_converge" in w.names() and "run_converge" not in w.names()
    assert seq["mutated"] is False


def test_a_move_during_the_identity_question_stops_before_the_second_plan(monkeypatch):
    w = _World(monkeypatch)
    first = {"rows": [{}], "holds": [], "identity_disagreement": {"oauth": A, "config": B}}
    plans = []

    def plan(env, flags):
        plans.append((env, flags.live))
        return first if len(plans) == 1 else {"rows": [{}], "holds": []}
    monkeypatch.setattr(gui.ccs, "plan_converge", plan)

    class AskAndMove(_UI):
        def ask_live(self, fresh):
            w.layout = ("/r2",)
            return "/r1/" + A
    seq = gui._run_level_sequence("e0", [], "", AskAndMove(), fresh=w.fresh,
                                  layout=("/r1",))
    assert seq["stage2"] == "layout" and len(plans) == 1
    assert "run_converge" not in w.names()


def test_a_layout_error_stops_the_sequence_too(monkeypatch):
    w = _World(monkeypatch)
    w.layout = None
    seq = gui._run_level_sequence("e0", STEPS, "", _UI(), fresh=w.fresh,
                                  layout=("/r1",))
    assert seq["stage2"] == "layout" and seq["layout_stop"] == "layout_error"
    assert seq["landed"] == 0 and w.calls == []


def test_without_fresh_the_sequence_runs_on_the_env_it_was_given(monkeypatch):
    w = _World(monkeypatch)
    seq = gui._run_level_sequence("e0", STEPS, "", _UI())
    assert seq["stage2"] == "completed"
    assert {c[1] for c in w.calls} == {"e0"}


def test_a_layout_error_from_a_rename_is_a_refusal_not_a_bug(monkeypatch):
    w = _World(monkeypatch)

    def fail(env, pm):
        raise ct.LayoutError("store discovery failed: simulated")
    monkeypatch.setattr(gui.ccs, "run_retitle", fail)
    seq = gui._run_level_sequence("e0", STEPS, "", _UI(), fresh=w.fresh,
                                  layout=("/r1",))
    assert seq["rename_refusal"][1] == "store discovery failed: simulated"
    assert seq["rename_error"] is False


# ----------------------------------------------- the re-measure after Apply

def test_a_layout_stop_with_nothing_written_still_re_measures(monkeypatch):
    w = _World(monkeypatch)
    w.layout = ("/r2",)
    seq, refresh = gui._run_level_apply("e0", [], "", _UI(), fresh=w.fresh,
                                        layout=("/r1",))
    assert seq["stage2"] == "layout" and not seq["mutated"]
    assert refresh is not None and refresh[0] == "ok"
    assert seq["env"] == w.envs("gather_alignment")[0]
    assert seq["layout_key"] == ("/r2",)


def test_the_re_measure_plans_without_an_answer_given_under_another_layout(
        monkeypatch):
    w = _World(monkeypatch)
    monkeypatch.setattr(gui, "_current_pair", lambda env: (A, B))   # rule (b) keeps it

    def move():
        w.layout = ("/r2",)
    seq, refresh = gui._run_level_apply(
        "e0", [], "/r1/" + A, _UI(confirm=False, on_confirm=move),
        pair=(A, B), fresh=w.fresh, layout=("/r1",), answer_layout=("/r1",))
    lives = [c[2] for c in w.calls if c[0] == "plan_converge"]
    assert lives[-1] == "" and seq["live_layout_dropped"] is True


def test_the_re_measure_keeps_an_answer_given_under_its_layout(monkeypatch):
    w = _World(monkeypatch)
    monkeypatch.setattr(gui, "_current_pair", lambda env: (A, B))
    seq, refresh = gui._run_level_apply(
        "e0", [], "/r1/" + A, _UI(confirm=False), pair=(A, B), fresh=w.fresh,
        layout=("/r1",), answer_layout=("/r1",))
    lives = [c[2] for c in w.calls if c[0] == "plan_converge"]
    assert lives[-1] == "/r1/" + A and not seq.get("live_layout_dropped")


def test_a_layout_error_in_the_re_measure_reads_as_a_refusal(monkeypatch):
    w = _World(monkeypatch)

    def fail(env):
        raise ct.LayoutError("store discovery failed: simulated")
    monkeypatch.setattr(gui.ccs, "gather_alignment", fail)
    seq, refresh = gui._run_level_apply("e0", [], "", _UI(confirm=False),
                                        fresh=w.fresh, layout=("/r1",))
    assert refresh == ("refusal", "store discovery failed: simulated")


# ------------------------------------------------------- the status lines

def test_the_status_of_a_layout_stop():
    changed = gui._sequence_status({"stage2": "layout", "layout_stop": "layout_changed",
                                    "landed": 0})
    assert changed.startswith("Nothing was written") and "data folder changed" in changed
    landed = gui._sequence_status({"stage2": "layout", "layout_stop": "layout_changed",
                                   "landed": 2})
    assert landed.startswith("2 renames landed, each undoable")
    assert "the copy did not run" in landed and "data folder changed" in landed
    err = gui._sequence_status({"stage2": "layout", "layout_stop": "layout_error",
                                "landed": 0})
    assert err.startswith("Nothing was written") and "could not be read" in err
    assert "data folder changed" not in err


def test_the_two_sentences():
    assert gui.ENV_FOLDER_CHANGED == ("The desktop app's data folder changed since this "
                                      "plan, so it was planned again.")
    assert gui.ENV_ANSWER_SET_ASIDE == ("The desktop app's data folder changed, so your "
                                        "answer about the signed-in account was set aside.")
    for s in (gui.ENV_FOLDER_CHANGED, gui.ENV_ANSWER_SET_ASIDE):
        assert "Apply" not in s and "asked" not in s
