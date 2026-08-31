"""The Level tab's pure functions (GUI 2.0 design, "Tests" 1-8).

Everything here is importable without tkinter - claude_code_sessions_gui
guards its tkinter import, so these run (rather than skip) on a machine
with no python3-tk. Names are the fake cast: ACME-REVIEW, Northwind,
alice@example.com, ids aaaa1111.../bbbb2222....
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import claude_code_sessions_gui as gui  # noqa: E402

SID_A = "aaaa1111" + "0" * 24        # the earlier leg (superseded)
SID_B = "bbbb2222" + "0" * 24        # the current leg
ACCT = "cccc3333" + "0" * 24
TITLE = "Quarterly board report finalization"
SUGGESTED = TITLE + " - earlier leg (Aug 24-28)"
LINE = ("aaaa1111 is the earlier leg - 48 of its 50 prose turns continue "
        "in bbbb2222, which adds 13 more; last activity Aug 28 vs Aug 30")


def measured(**kw):
    base = {"classification": "supersession", "reason": None,
            "superseded": SID_A, "current": SID_B,
            "shared": 48, "a": SID_A, "a_total": 50,
            "b": SID_B, "b_total": 61,
            "suggested_title": SUGGESTED, "degrade_reason": None,
            "command_runnable": True}
    base.update(kw)
    return base


def hold(**kw):
    base = {"session": SID_B, "account": ACCT,
            "label": "alice@example.com (cccc3333/dddd4444)", "title": TITLE,
            "reason": "held_title_collision",
            "detail": "%r already names a different conversation in that "
                      "sidebar" % TITLE,
            "retitle": "claude-code-sessions retitle --only aaaa1111 "
                       '--title "%s" --apply' % SUGGESTED,
            "measured": measured(), "measured_line": LINE}
    base.update(kw)
    return base


def manifest(holds=(), rows=(), complete=None, **kw):
    m = {"op_type": "converge", "live_asserted": "", "only": "",
         "only_session": "",
         "destinations": [{"account": ACCT, "org": "dddd4444" + "0" * 24,
                           "path": "/x", "label": "alice@example.com",
                           "rows": 3}],
         "non_destinations": [],
         "complete": complete or {"now": 3, "of": 3, "after": 3, "held": 0,
                                  "scoped": False},
         "dead_excluded": 0, "notes": [], "holds": list(holds),
         "rows": list(rows)}
    m.update(kw)
    return m


def alignment(short=0, rows_per_account=(379, 379, 379)):
    labels = ["alice@example.com", "bob@example.com", "carol@example.com"]
    n = len(rows_per_account)
    return {
        "stores": {"status": "found", "detail": "", "roots": ["/x"]},
        "accounts": [{"account": chr(97 + i) * 32, "label": labels[i],
                      "rows": rows_per_account[i]} for i in range(n)],
        "row_errors": [],
        "reachable": {"transcripts": 400, "reachable": 398, "orphans": 2,
                      "orphan_ids": []},
        "distinguishable": {"duplicate_titles": 1,
                            "per_account": {labels[0]: 1},
                            "titles": {}},
        "consistent": {"disagreeing_rows": 3, "leaving_a_gap": 0, "rows": []},
        "complete": {"conversations": 379, "in_all_accounts": 379 - short,
                     "short": short, "by_account_count": {}},
        "safe": {"dead_rows": 0, "blank_rows": 0, "unreadable_rows": 0},
        "exit_code": 0,
    }


# --------------------------------------------------------------- hold models

def test_hold_models_prefills_runnable_supersession():
    models = gui._hold_models(manifest(holds=[hold()]))
    assert len(models) == 1
    m = models[0]
    # The engine's verdict is the target - measured.superseded, which here is
    # NOT the hold's own session id, so any re-derivation from the hold would
    # aim the rename at the wrong leg.
    assert m["target_sid"] == SID_A
    assert m["target_sid"] != SID_B
    assert m["prefill"] == SUGGESTED
    assert m["entry"] == SUGGESTED
    assert m["editable"] is True
    assert m["ticked"] is True
    assert m["classification"] == "supersession"
    assert m["title"] == TITLE
    assert LINE in m["evidence"]
    assert m["key"] == (SID_A, TITLE)


def test_hold_models_prefills_shell_unsafe_supersession():
    # command_runnable speaks about the rendered shell command only; the GUI
    # applies titles through plan_retitle, no shell involved.
    unsafe = TITLE + " $99 - earlier leg (Aug 24-28)"
    h = hold(measured=measured(suggested_title=unsafe, command_runnable=False,
                               degrade_reason="title not shell-safe"))
    m = gui._hold_models(manifest(holds=[h]))[0]
    assert m["prefill"] == unsafe
    assert m["editable"] is True
    assert m["ticked"] is True
    assert m["degrade_reason"] == "title not shell-safe"


def test_hold_models_degraded_supersession_is_empty_editable():
    h = hold(measured=measured(suggested_title=None, command_runnable=False,
                               degrade_reason="suggested name already taken"))
    m = gui._hold_models(manifest(holds=[h]))[0]
    assert m["prefill"] == ""
    assert m["entry"] == ""
    assert m["editable"] is True
    assert m["ticked"] is False
    assert m["degrade_reason"] == "suggested name already taken"
    assert m["target_sid"] == SID_A


def test_hold_models_distinct_unmeasured_and_unknown_reasons():
    distinct = hold(measured=measured(
        classification="distinct", superseded=None, current=None,
        suggested_title=None, command_runnable=False),
        measured_line="largely distinct conversations - they share 2 of 50 "
                      "and 2 of 61 prose turns; both need human names")
    unmeasured = hold(session="eeee5555" + "0" * 24,
                      title="Northwind backtest",
                      measured=measured(
                          classification="unmeasured", reason="no transcript",
                          superseded=None, current=None, shared=None,
                          a_total=None, b_total=None, suggested_title=None,
                          command_runnable=False),
                      measured_line="no transcript")
    unknown = {"session": "ffff6666" + "0" * 24, "account": ACCT,
               "label": "alice@example.com", "title": "",
               "reason": "held_future_reason",
               "detail": "the engine grew a hold this window predates",
               "retitle": ""}
    models = gui._hold_models(manifest(holds=[distinct, unmeasured, unknown]))
    # The pane must never count holds it cannot show.
    assert len(models) == 3
    d, u, f = models
    for m in (d, u, f):
        assert m["editable"] is False
        assert m["ticked"] is False
        assert m["target_sid"] is None
        assert m["prefill"] == ""
    assert "largely distinct" in d["evidence"]
    assert u["evidence"].startswith("not measured: no transcript")
    # The fallback row carries the hold's own reason/detail verbatim.
    assert f["classification"] == "held_future_reason"
    assert "the engine grew a hold this window predates" in f["evidence"]


# --------------------------------------------------------------- scoreboard

def test_scoreboard_lines_render_from_alignment_report():
    rep = alignment(short=4, rows_per_account=(379, 371, 380))
    lines = gui._scoreboard_lines(rep)
    text = "\n".join(lines)
    # Per-account rows, verbatim from the report - including unequal counts.
    assert "alice@example.com" in text and "379" in text
    assert "bob@example.com" in text and "371" in text
    assert "carol@example.com" in text and "380" in text
    assert "375 of 379" in text          # complete: in_all_accounts of conversations
    assert "4 short" in text
    assert "398 of 400" in text          # reachable
    assert "2 orphaned" in text
    assert "1 title(s) duplicated" in text
    assert "0 dead, 0 blank, 0 unreadable" in text
    half = gui._scoreboard_half(rep)
    assert half == "Level: 379 / 371 / 380 - 4 short."


# ---------------------------------------------------------------- predicate

def test_level_predicate_states():
    # Level: rows 0, holds 0, short 0 - all three required.
    st = gui._level_state(alignment(short=0), manifest(), [])
    assert st["status"] == "Nothing to do - the sidebars are level."
    assert st["apply"] is False

    # Naming-only: rows 0, holds N - Apply stays enabled (renames to run).
    models = gui._hold_models(manifest(holds=[hold()]))
    st = gui._level_state(alignment(short=0),
                          manifest(holds=[hold()],
                                   complete={"now": 2, "of": 3, "after": 2,
                                             "held": 1, "scoped": False}),
                          models)
    assert st["status"] == "Nothing to copy - 1 naming decision below."
    assert st["apply"] is True

    # Naming-only but every hold read-only: nothing ticked-able, Apply off.
    ro = hold(measured=measured(classification="distinct", superseded=None,
                                current=None, suggested_title=None,
                                command_runnable=False))
    ro_models = gui._hold_models(manifest(holds=[ro]))
    st = gui._level_state(alignment(short=0), manifest(holds=[ro]), ro_models)
    assert st["apply"] is False

    # Short-but-empty-plan: alignment says short, the plan has nothing - the
    # tab has nothing it can do about that state, so Apply is disabled and
    # the line points at Health.
    st = gui._level_state(alignment(short=2), manifest(), [])
    assert st["apply"] is False
    assert "Health" in st["status"] + st["detail"]
    assert "2" in st["status"]

    # Rows present: Apply is live.
    row = {"name": "local_x.json", "dest_path": "/x/local_x.json",
           "store_path": "/x", "account": ACCT, "org": "d" * 32,
           "label": "alice@example.com", "session": SID_B, "title": TITLE,
           "title_source": "auto", "holders": [], "pre_b64": None,
           "post_b64": "e30=", "is_update": False, "written": False}
    st = gui._level_state(alignment(short=1), manifest(rows=[row]), [])
    assert st["apply"] is True
    assert "1 row" in st["status"]


# ------------------------------------------------------------------- stage 1

def test_level_steps_stage1():
    a = gui._hold_models(manifest(holds=[hold()]))[0]
    b_hold = hold(session="eeee5555" + "0" * 24, title="Northwind backtest",
                  measured=measured(superseded="eeee5555" + "0" * 24,
                                    current="ffff6666" + "0" * 24,
                                    a="eeee5555" + "0" * 24,
                                    b="ffff6666" + "0" * 24,
                                    suggested_title="Northwind backtest - "
                                                    "earlier leg (Aug 27)"))
    b = gui._hold_models(manifest(holds=[b_hold]))[0]
    ro = gui._hold_models(manifest(holds=[hold(
        measured=measured(classification="distinct", superseded=None,
                          current=None, suggested_title=None,
                          command_runnable=False))]))[0]
    unticked = dict(a, ticked=False)

    steps, problems = gui._level_steps_stage1([a, b, ro])
    assert problems == []
    # Ticked renames in row order, aimed at measured.superseded.
    assert [s["target_sid"] for s in steps] == [SID_A, "eeee5555" + "0" * 24]
    assert steps[0]["new_title"] == SUGGESTED
    assert steps[0]["old_title"] == TITLE

    # Unticked and read-only rows contribute nothing.
    steps, problems = gui._level_steps_stage1([unticked, ro])
    assert steps == [] and problems == []

    # A ticked row with an empty entry refuses locally - no steps at all.
    empty = dict(a, entry="   ")
    steps, problems = gui._level_steps_stage1([empty, b])
    assert steps == []
    assert any("needs a name" in p for p in problems)

    # Two ticked rows sharing a trimmed title refuse locally too.
    twin = dict(b, entry=a["entry"] + "  ")
    steps, problems = gui._level_steps_stage1([a, twin])
    assert steps == []
    assert any("share a name" in p for p in problems)


# ------------------------------------------------------------------- merges

def test_row_merge_preserves_edits():
    fresh_a = gui._hold_models(manifest(holds=[hold()]))[0]
    fresh_b = gui._hold_models(manifest(holds=[hold(
        session="eeee5555" + "0" * 24, title="Northwind backtest",
        measured=measured(superseded="eeee5555" + "0" * 24,
                          current="ffff6666" + "0" * 24,
                          a="eeee5555" + "0" * 24, b="ffff6666" + "0" * 24,
                          suggested_title=None,
                          degrade_reason="suggested name already taken",
                          command_runnable=False))]))[0]

    edited = dict(fresh_a, entry="ACME-REVIEW session - my own name",
                  ticked=False)
    vanished = dict(fresh_b, key=("gone" + "0" * 28, "x"), entry="typed")

    merged = gui._merge_hold_models([dict(fresh_a), dict(fresh_b)],
                                    [edited, vanished])
    assert len(merged) == 2
    # A surviving row keeps the user's entry text and tick...
    assert merged[0]["entry"] == "ACME-REVIEW session - my own name"
    assert merged[0]["ticked"] is False
    # ...a new row arrives with its defaults...
    assert merged[1]["entry"] == ""
    assert merged[1]["ticked"] is False
    # ...and the vanished row's state went with it (nothing resurrects it).
    assert all(m["key"] != ("gone" + "0" * 28, "x") for m in merged)
