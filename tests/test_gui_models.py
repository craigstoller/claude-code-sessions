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
    """0.15.1 (finding F): a hold the measurement declined to name is still
    a hold a HUMAN can name. Distinct and unmeasured rows are editable,
    empty and unticked - the degraded-supersession treatment - and each
    renames ITS OWN held conversation (the hold's session; measured.superseded
    is null here). Only the unknown-reason fallback stays read-only."""
    distinct = hold(measured=measured(
        classification="distinct", superseded=None, current=None,
        suggested_title=None, command_runnable=False),
        measured_line="largely distinct conversations - they share 2 of 50 "
                      "and 2 of 61 prose turns; both need human names")
    SID_E = "eeee5555" + "0" * 24
    unmeasured = hold(session=SID_E,
                      title="Northwind backtest",
                      measured=measured(
                          classification="unmeasured", reason="no transcript",
                          superseded=None, current=None, shared=None,
                          a=SID_E, b=SID_B,
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
    for m in (d, u):
        assert m["editable"] is True
        assert m["ticked"] is False
        assert m["prefill"] == ""
        assert m["entry"] == ""
    # The row is ABOUT its own conversation - typing a name names that one.
    assert d["target_sid"] == SID_B
    assert d["key"] == (SID_B, TITLE)
    assert u["target_sid"] == SID_E
    # The measurement's own line stays above the entry as context...
    assert d["evidence"].startswith("measured: largely distinct")
    assert u["evidence"].startswith("not measured: no transcript")
    # ...and says what the leg collides with - the other sid's prefix and
    # the account - so the user knows they are naming one of two.
    assert "aaaa1111" in d["evidence"]
    assert "bbbb2222" in u["evidence"]
    assert "alice@example.com" in d["evidence"]
    assert "alice@example.com" in u["evidence"]
    # The fallback row carries the hold's own reason/detail verbatim, and
    # stays read-only: no measured object, no verdict, nothing to aim at.
    assert f["editable"] is False
    assert f["ticked"] is False
    assert f["target_sid"] is None
    assert f["prefill"] == ""
    assert f["classification"] == "held_future_reason"
    assert "the engine grew a hold this window predates" in f["evidence"]


def test_hold_models_unmeasured_pair_is_two_rows_each_naming_its_own_leg():
    """The two directions of an unmeasured collision do NOT share a key
    (no superseded sid to merge on), so they render as two rows - one per
    leg - and naming either one clears the pair. A supersession pair still
    merges into one row keyed on the superseded leg."""
    reason = "overlap and recency disagree"
    a = hold(session=SID_A, label="bob@example.com",
             measured=measured(classification="unmeasured", reason=reason,
                               superseded=None, current=None,
                               a=SID_A, b=SID_B, shared=48,
                               suggested_title=None, command_runnable=False),
             measured_line=reason)
    b = hold(session=SID_B, label="alice@example.com",
             measured=measured(classification="unmeasured", reason=reason,
                               superseded=None, current=None,
                               a=SID_B, b=SID_A, shared=48,
                               suggested_title=None, command_runnable=False),
             measured_line=reason)
    models = gui._hold_models(manifest(holds=[a, b]))
    assert [m["target_sid"] for m in models] == [SID_A, SID_B]
    assert all(m["editable"] and not m["ticked"] for m in models)
    assert "bbbb2222" in models[0]["evidence"]
    assert "bob@example.com" in models[0]["evidence"]
    assert "aaaa1111" in models[1]["evidence"]
    assert "alice@example.com" in models[1]["evidence"]


def test_hold_models_unmeasured_row_held_in_two_accounts_lists_both():
    """The same conversation held under one title in two destination
    sidebars is ONE decision (retitle's scope is every account), so the
    rows merge by key - and the evidence names both accounts."""
    reason = "no transcript"
    one = hold(session=SID_B, label="alice@example.com",
               measured=measured(classification="unmeasured", reason=reason,
                                 superseded=None, current=None,
                                 a=SID_B, b=SID_A, shared=None,
                                 a_total=None, b_total=None,
                                 suggested_title=None, command_runnable=False),
               measured_line=reason)
    two = dict(one, account="e" * 32, label="carol@example.com")
    models = gui._hold_models(manifest(holds=[one, two]))
    assert len(models) == 1
    ev = models[0]["evidence"]
    assert "alice@example.com" in ev and "carol@example.com" in ev
    assert models[0]["target_sid"] == SID_B


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
    # GUI polish, Change 4: the detail line leads with the scoreboard half -
    # the tab's headline number, above the box a sash can shorten.
    assert st["detail"] == "Level: 379 / 379 / 379 - 0 short."

    # Naming-only: rows 0, holds N - Apply stays enabled (renames to run).
    # The header counts honestly (0.15.1, B): holds WITH a suggestion apart
    # from holds needing a human name, numbers from the models.
    models = gui._hold_models(manifest(holds=[hold()]))
    st = gui._level_state(alignment(short=0),
                          manifest(holds=[hold()],
                                   complete={"now": 2, "of": 3, "after": 2,
                                             "held": 1, "scoped": False}),
                          models)
    assert st["status"] == "Nothing to copy - 1 held: 1 suggested."
    assert st["apply"] is True
    assert "Level the sidebars" in st["detail"]
    assert st["detail"].startswith("Level: 379 / 379 / 379 - 0 short.  ")

    # A suggested supersession beside an unmeasured leg: "2 held - 1
    # suggested, 1 needs a name", and Apply is live (one rename is ticked).
    unm = hold(session="eeee5555" + "0" * 24, title="Northwind backtest",
               measured=measured(classification="unmeasured",
                                 reason="no transcript", superseded=None,
                                 current=None, a="eeee5555" + "0" * 24,
                                 b=SID_B, suggested_title=None,
                                 command_runnable=False),
               measured_line="no transcript")
    mixed = gui._hold_models(manifest(holds=[hold(), unm]))
    st = gui._level_state(alignment(short=0), manifest(holds=[hold(), unm]),
                          mixed)
    assert st["status"] == ("Nothing to copy - 2 held: 1 suggested, "
                            "1 needs a name.")
    assert st["apply"] is True
    # Two unmeasured legs: plural.
    unm2 = dict(unm, session=SID_B, measured=dict(unm["measured"],
                                                   a=SID_B, b="e" * 32))
    st = gui._level_state(alignment(short=0), manifest(holds=[unm, unm2]),
                          gui._hold_models(manifest(holds=[unm, unm2])))
    assert st["status"] == "Nothing to copy - 2 held: 2 need a name."

    # Naming-only but every hold read-only (an unknown-reason fallback):
    # nothing ticked-able, Apply off, and the count says read-only.
    ro = {"session": "ffff6666" + "0" * 24, "account": ACCT,
          "label": "alice@example.com", "title": "",
          "reason": "held_future_reason", "detail": "unknown", "retitle": ""}
    ro_models = gui._hold_models(manifest(holds=[ro]))
    st = gui._level_state(alignment(short=0), manifest(holds=[ro]), ro_models)
    assert st["apply"] is False
    assert st["status"] == "Nothing to copy - 1 held: 1 read-only."

    # Short-but-empty-plan: alignment says short, the plan has nothing - the
    # tab has nothing it can do about that state, so Apply is disabled and
    # the line points at Health.
    st = gui._level_state(alignment(short=2), manifest(), [])
    assert st["apply"] is False
    assert "Health" in st["status"] + st["detail"]
    assert "2" in st["status"]
    assert st["detail"].startswith("Level: 379 / 379 / 379 - 2 short.  ")

    # Rows present: Apply is live.
    row = {"name": "local_x.json", "dest_path": "/x/local_x.json",
           "store_path": "/x", "account": ACCT, "org": "d" * 32,
           "label": "alice@example.com", "session": SID_B, "title": TITLE,
           "title_source": "auto", "holders": [], "pre_b64": None,
           "post_b64": "e30=", "is_update": False, "written": False}
    st = gui._level_state(alignment(short=1), manifest(rows=[row]), [])
    assert st["apply"] is True
    assert "1 row" in st["status"]
    assert "held" not in st["status"]
    assert st["detail"] == ("Level: 379 / 379 / 379 - 1 short.  Nothing is "
                            "written until you press Level the sidebars.")
    # Rows AND holds: the same honest clause after the row count.
    st = gui._level_state(alignment(short=1), manifest(rows=[row]), mixed)
    assert st["status"] == ("1 row to create across 1 account - 2 held: "
                            "1 suggested, 1 needs a name")
    assert "Level the sidebars" in st["detail"]


def test_holds_heading_never_claims_ticked_rows_that_are_not():
    """0.15.1 (B): the section label above the rows counts what is ticked
    NOW, from the models - it must not promise 'each ticked row becomes one
    rename' over a list where nothing is ticked."""
    unm = hold(session="eeee5555" + "0" * 24, title="Northwind backtest",
               measured=measured(classification="unmeasured",
                                 reason="no transcript", superseded=None,
                                 current=None, a="eeee5555" + "0" * 24,
                                 b=SID_B, suggested_title=None,
                                 command_runnable=False),
               measured_line="no transcript")
    models = gui._hold_models(manifest(holds=[hold(), unm]))
    assert gui._holds_heading(models).startswith(
        "Naming decisions - 1 of 2 ticked")
    assert "Level the sidebars" in gui._holds_heading(models)
    none = [dict(m, ticked=False) for m in models]
    heading = gui._holds_heading(none)
    assert heading.startswith("Naming decisions - none ticked")
    assert "each ticked row" not in heading
    both = [dict(m, ticked=True) for m in models]
    assert gui._holds_heading(both).startswith(
        "Naming decisions - 2 of 2 ticked")


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
    # A distinct row arrives editable but UNTICKED (0.15.1, F), so it
    # contributes nothing until someone ticks it and types a name.
    distinct = gui._hold_models(manifest(holds=[hold(
        measured=measured(classification="distinct", superseded=None,
                          current=None, suggested_title=None,
                          command_runnable=False))]))[0]
    ro = gui._hold_models(manifest(holds=[{
        "session": "ffff6666" + "0" * 24, "account": ACCT,
        "label": "alice@example.com", "title": "",
        "reason": "held_future_reason", "detail": "unknown", "retitle": ""}]
    ))[0]
    unticked = dict(a, ticked=False)

    steps, problems = gui._level_steps_stage1([a, b, distinct, ro])
    assert problems == []
    # Ticked renames in row order, aimed at measured.superseded.
    assert [s["target_sid"] for s in steps] == [SID_A, "eeee5555" + "0" * 24]
    assert steps[0]["new_title"] == SUGGESTED
    assert steps[0]["old_title"] == TITLE

    # Unticked and read-only rows contribute nothing.
    steps, problems = gui._level_steps_stage1([unticked, distinct, ro])
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


def test_level_steps_stage1_renames_a_ticked_unmeasured_row():
    """0.15.1 (F): a ticked unmeasured row with a typed title becomes one
    rename aimed at ITS OWN held conversation - the hold's session, which
    the model carries as target_sid. The CLI's remedy line targets the
    blocking side; that convention stays in the CLI."""
    SID_E = "eeee5555" + "0" * 24
    h = hold(session=SID_E, title="Northwind backtest",
             measured=measured(classification="unmeasured",
                               reason="overlap and recency disagree",
                               superseded=None, current=None,
                               a=SID_E, b=SID_B, suggested_title=None,
                               command_runnable=False),
             measured_line="overlap and recency disagree")
    m = gui._hold_models(manifest(holds=[h]))[0]
    typed = dict(m, ticked=True, entry="Northwind backtest - the Aug 27 leg ")
    steps, problems = gui._level_steps_stage1([typed])
    assert problems == []
    assert steps == [{"key": (SID_E, "Northwind backtest"),
                      "target_sid": SID_E,
                      "old_title": "Northwind backtest",
                      "new_title": "Northwind backtest - the Aug 27 leg"}]
    # Both legs of an unmeasured pair named at once: two renames, and the
    # local duplicate-title check still stands between them.
    other = hold(session=SID_B, title="Northwind backtest",
                 measured=measured(classification="unmeasured",
                                   reason="overlap and recency disagree",
                                   superseded=None, current=None,
                                   a=SID_B, b=SID_E, suggested_title=None,
                                   command_runnable=False),
                 measured_line="overlap and recency disagree")
    m2 = gui._hold_models(manifest(holds=[other]))[0]
    both = [typed, dict(m2, ticked=True, entry="Northwind backtest - Aug 30")]
    steps, problems = gui._level_steps_stage1(both)
    assert [s["target_sid"] for s in steps] == [SID_E, SID_B]
    clash = [typed, dict(m2, ticked=True, entry=typed["entry"].strip())]
    steps, problems = gui._level_steps_stage1(clash)
    assert steps == [] and any("share a name" in p for p in problems)


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


# -------------------------------------------------- the sync pane's warning

def test_sync_dup_warning_counts_adds_that_duplicate_a_title():
    """0.15.1 (E, part 2): the Copy & refresh tab warns ABOVE Apply, in
    numbers, when rows it would add already name a row in that sidebar -
    computed from plan_sync's per-row dup_title flag over the adds only
    (a refresh duplicates its own conversation by definition and is not
    counted either way)."""
    def add(title, dup_title, dup_conversation=False, is_update=False):
        return {"name": "local_x.json", "title": title, "is_update": is_update,
                "dup_title": dup_title, "dup_conversation": dup_conversation}
    rows = [add("ACME-REVIEW session", True, True),
            add("Northwind backtest", False, True),
            add("Quarterly board report", False),
            add("a refresh", True, True, is_update=True)]
    text = gui._sync_dup_warning(rows)
    assert text.startswith("!! ")
    assert "1 of 3 rows would duplicate a title already in that sidebar" in text
    assert "Level" in text
    assert gui._sync_dup_warning([add("Quarterly board report", False)]) == ""
    assert gui._sync_dup_warning([]) == ""
    # A manifest from an older engine carries no flags: no warning, no crash.
    assert gui._sync_dup_warning([{"name": "local_y.json", "title": "x",
                                   "is_update": False}]) == ""


# ------------------------------------------------- the window's geometry

def test_window_geometry_clamps_to_the_work_area():
    """Change 1 item 4 of the GUI polish design: the initial size is
    min(940x640, work area minus window chrome) and the minsize is
    min(fit floor, work area minus chrome) - a constant minsize larger
    than the screen would trap controls off-screen."""
    # A roomy desktop: the defaults apply.
    assert gui._initial_geometry((1536, 912)) == (940, 640)
    assert gui._min_size((1536, 912)) == gui.FIT_FLOOR
    assert gui.FIT_FLOOR == (760, 420)
    # A 1366x768 panel at 150 % (911x512 logical, 480 after the taskbar):
    # 871x432 after chrome.
    assert gui._initial_geometry((911, 480)) == (871, 432)
    assert gui._min_size((911, 480)) == (760, 420)
    # The same panel at 200 %: 683x384 logical - the minsize must not
    # exceed the work area, so both clamp to the area minus chrome.
    assert gui._initial_geometry((683, 384)) == (643, 336)
    assert gui._min_size((683, 384)) == (643, 336)
    w, h = gui._min_size((683, 384))
    assert w <= 683 and h <= 384
    # The clamp never produces a size Tk cannot lay out at all.
    assert gui._min_size((100, 100)) == gui._initial_geometry((100, 100))
    assert all(v >= 240 for v in gui._min_size((100, 100)))


# ------------------------------------------- the One-session tab's gate

def _sync_row(name, sid, is_update=False):
    return {"name": name, "session_id": sid, "is_update": is_update,
            "title": "Northwind backtest", "pre_b64": None, "post_b64": "e30="}


def test_plan_digest_binds_consent_to_the_row_set():
    """GUI polish, Change 3: the bulk-copy consent is bound to a digest of
    the rendered rows - per row the filename, the session id and is_update,
    order-independent - so a replan with the same rows keeps the tick and
    one with a different row set clears it."""
    a = _sync_row("local_1.json", "aaaa1111" + "0" * 24)
    b = _sync_row("local_2.json", "bbbb2222" + "0" * 24)
    c = _sync_row("local_3.json", "eeee5555" + "0" * 24, is_update=True)
    same = gui._rows_digest([a, b, c])
    assert gui._rows_digest([c, a, b]) == same
    assert gui._rows_digest([b, c, a]) == same
    assert gui._rows_digest([a, b, dict(c, is_update=False)]) != same
    assert gui._rows_digest([a, b, dict(c, session_id="f" * 32)]) != same
    assert gui._rows_digest([a, b, dict(c, name="local_9.json")]) != same
    assert gui._rows_digest([a, b]) != same
    assert gui._rows_digest([]) != same
    # A title change alone is not a different row set.
    assert gui._rows_digest([a, b, dict(c, title="renamed")]) == same


def test_sync_apply_gate():
    """_sync_apply_allowed(rows, consent): one row -> live without consent
    (an add or a refresh alike); zero rows -> never; two or more -> only
    with consent whose digest matches the rendered rows."""
    a = _sync_row("local_1.json", "aaaa1111" + "0" * 24)
    b = _sync_row("local_2.json", "bbbb2222" + "0" * 24)
    r = _sync_row("local_3.json", "eeee5555" + "0" * 24, is_update=True)
    assert gui._sync_apply_allowed([a], None) is True
    assert gui._sync_apply_allowed([r], None) is True
    assert gui._sync_apply_allowed([], None) is False
    assert gui._sync_apply_allowed([], gui._rows_digest([])) is False
    assert gui._sync_apply_allowed([a, b], None) is False
    assert gui._sync_apply_allowed([a, b], gui._rows_digest([b, a])) is True
    assert gui._sync_apply_allowed([a, b, r], gui._rows_digest([a, b])) is False
    stale = gui._rows_digest([a, dict(b, is_update=True)])
    assert gui._sync_apply_allowed([a, b], stale) is False
    assert gui._consent_label(3) == "copy every row this plan lists (3)"
    assert gui._consent_label(78) == "copy every row this plan lists (78)"


# ---------------------------------------------- the identity answer's life

def test_live_answer_reducer():
    """GUI polish, Change 2 item 5, as a pure reducer over events: the
    answer is bound to the (oauth, config) pair it was given for and drops
    on a run of the mutation path, on a fresh read that no longer returns
    that pair, or on an explicit change; everything else keeps it."""
    A, B, C = "aaaa1111" + "0" * 24, "bbbb2222" + "0" * 24, "eeee5555" + "0" * 24
    held = {"path": "/x/" + A, "pair": (A, B), "label": "alice@example.com"}
    nxt = gui._live_answer_next
    assert nxt(None, "answered", held) == held
    # (a) the mutation path ran under it - a sync apply that wrote, or a
    # converge ending completed or unchanged.
    assert nxt(held, "mutation_ran", "completed") is None
    assert nxt(held, "mutation_ran", "unchanged") is None
    # (c) the user changes it.
    assert nxt(held, "explicit_change") is None
    # (b) a fresh read of the files no longer returns that pair.
    assert nxt(held, "files_read", None) is None
    assert nxt(held, "files_read", (A, C)) is None
    assert nxt(held, "files_read", (B, A)) is None       # roles swapped
    assert nxt(held, "files_read", (A, B)) == held       # the same pair keeps
    assert nxt(held, "files_read", [A, B]) == held       # list or tuple alike
    # Nothing else clears it: the zero-rows empty outcome is pinned apart
    # from unchanged - the seam a reviewer found.
    for ev in ("refused", "cancelled", "empty", "truncated", "tab_switched"):
        assert nxt(held, ev) == held, ev
    assert nxt(None, "files_read", (A, B)) is None
    assert nxt(None, "refused") is None
    # The sequence's endings map onto those events.
    assert gui._live_event_for("completed") == "mutation_ran"
    assert gui._live_event_for("unchanged") == "mutation_ran"
    assert gui._live_event_for("empty") == "empty"
    assert gui._live_event_for("cancelled") == "cancelled"
    assert gui._live_event_for("truncated") == "truncated"
    for s2 in ("refused", "error", "plan_failed", "gate", "not_reached"):
        assert gui._live_event_for(s2) == "refused", s2
    # The line every confirmation prints while an answer is held.
    assert (gui._assertion_line("alice@example.com")
            == "under your assertion: the desktop app is on alice@example.com")


# ------------------------------------------------- the dirty-close prompt

def test_dirty_holds_counts_edited_rows():
    """GUI polish, Change 5: an editable row whose entry differs from its
    prefill, or whose tick differs from its default, is one unapplied
    naming change; untouched rows and read-only rows are not."""
    suggested = gui._hold_models(manifest(holds=[hold()]))[0]
    unm = hold(session="eeee5555" + "0" * 24, title="Northwind backtest",
               measured=measured(classification="unmeasured",
                                 reason="no transcript", superseded=None,
                                 current=None, a="eeee5555" + "0" * 24,
                                 b=SID_B, suggested_title=None,
                                 command_runnable=False),
               measured_line="no transcript")
    empty = gui._hold_models(manifest(holds=[unm]))[0]
    ro = gui._hold_models(manifest(holds=[{
        "session": "ffff6666" + "0" * 24, "account": ACCT,
        "label": "alice@example.com", "title": "",
        "reason": "held_future_reason", "detail": "unknown", "retitle": ""}]
    ))[0]
    # Untouched: the prefilled-and-ticked default and the empty-unticked
    # default are both "as rendered".
    assert gui._dirty_hold_count([suggested, empty, ro]) == 0
    # Typed text counts, in either kind of row.
    assert gui._dirty_hold_count([dict(suggested, entry="my own name")]) == 1
    assert gui._dirty_hold_count([dict(empty, entry="a name")]) == 1
    # A changed tick counts as much as typed text - either direction.
    assert gui._dirty_hold_count([dict(suggested, ticked=False)]) == 1
    assert gui._dirty_hold_count([dict(empty, ticked=True)]) == 1
    # One row, one change, however many fields moved.
    assert gui._dirty_hold_count([dict(suggested, entry="x",
                                       ticked=False)]) == 1
    assert gui._dirty_hold_count([dict(suggested, entry="x"),
                                  dict(empty, entry="y"), ro]) == 2
    # A read-only row never counts, whatever its fields say.
    assert gui._dirty_hold_count([dict(ro, entry="typed", ticked=True)]) == 0
    # Whitespace-only edits are not changes.
    assert gui._dirty_hold_count([dict(suggested,
                                       entry=suggested["prefill"] + "  ")]) == 0
    # The prompt renders the count with the "change(s)" noun.
    assert gui._close_prompt(1) == ("1 unapplied naming change will be lost. "
                                    "Close?")
    assert gui._close_prompt(2) == ("2 unapplied naming changes will be lost. "
                                    "Close?")
    # The interception's clause, when nonzero.
    assert gui._unapplied_clause(0) == ""
    assert gui._unapplied_clause(1) == "; 1 unapplied naming change will be lost"
    assert gui._unapplied_clause(3) == "; 3 unapplied naming changes will be lost"


# ------------------------------------------------------- dialog quoting

def test_dialog_quoting_is_not_repr():
    """GUI polish, Change 6: one quoting style - straight double quotes -
    in every dialog. Python's repr rendered a title with an apostrophe in
    double quotes beside a neighbour in single quotes, and doubled a
    backslash."""
    steps = [{"key": ("a", "x"), "target_sid": SID_A,
              "old_title": "Northwind's plan", "new_title": "Northwind plan"},
             {"key": ("b", "y"), "target_sid": SID_B,
              "old_title": "C:\\Users\\craig",
              "new_title": "Users folder"}]
    _head, mappings, _footer = gui._stage1_dialog_parts(steps)
    assert mappings[0] == '"Northwind\'s plan"  ->  "Northwind plan"'
    assert mappings[1] == '"C:\\Users\\craig"  ->  "Users folder"'
    assert all(m.startswith('"') for m in mappings)
    assert "\\\\" not in mappings[1]              # no doubled backslash
    assert gui._q("it's") == '"it\'s"'
    # The local refusals and the undo prompt use the same style.
    empty = {"key": ("a", "x"), "target_sid": SID_A, "title": "Bob's leg",
             "editable": True, "ticked": True, "entry": " ", "prefill": ""}
    _steps, problems = gui._level_steps_stage1([empty])
    assert problems and '"Bob\'s leg"' in problems[0]
    assert "'Bob" not in problems[0]
    _title, prompt = gui.SyncApp._undo_prompt(None, {
        "type": "retitle", "rows": 2, "new_title": "Alice's leg"})
    assert '"Alice\'s leg"' in prompt


def test_stage2_question_names_the_destinations():
    """Change 6: the stage-2 dialog lists the fresh manifest's destination
    labels - nothing in that dialog named an account before."""
    rows = [{"account": ACCT, "name": "a"}, {"account": ACCT, "name": "b"},
            {"account": "e" * 32, "name": "c"}]
    fresh = manifest(rows=rows, holds=[hold()])
    fresh["destinations"].append({"account": "e" * 32, "org": "d" * 32,
                                  "path": "/y",
                                  "label": "bob@example.com (bbbb2222/dddd4444)",
                                  "rows": 5})
    q = gui._stage2_question(fresh)
    assert q.startswith("Create 3 rows across 2 accounts:")
    assert "   2 into alice@example.com" in q
    assert "   1 into bob@example.com (bbbb2222/dddd4444)" in q
    assert "(1 held - they stay held.)" in q
    assert "converge never overwrites or deletes" in q
    assert "under your assertion" not in q
    q2 = gui._stage2_question(fresh, gui._assertion_line("alice@example.com"))
    assert q2.endswith("under your assertion: the desktop app is on "
                       "alice@example.com")
    one = gui._stage2_question(manifest(rows=rows[:1]))
    assert one.startswith("Create 1 row across 1 account:")


def test_sync_second_box_title_follows_its_body():
    """Change 6: the sync second box's title follows its body - 'Refresh
    rows?' for a pure refresh, not 'Copy sessions?' over 'It adds nothing'."""
    assert gui._sync_box_title(n_add=3, n_upd=0) == "Copy sessions?"
    assert gui._sync_box_title(n_add=0, n_upd=2) == "Refresh rows?"
    assert gui._sync_box_title(n_add=1, n_upd=2) == "Add and refresh rows?"


# ------------------------------------------- the One-session status line

def test_sync_status_line_counts_row_files_not_sessions():
    """GUI polish, Change 6: the honest count the 0.15.1 manifest can
    support - row files the destination lacks, and how many of them are
    conversations it already opens under another row (dup_conversation) -
    never "sessions ready to copy"; an older manifest without the flag
    falls back to the row-file count alone."""
    def add(i, dup=False, is_update=False):
        return {"name": "local_%d.json" % i, "session_id": "s%d" % i,
                "is_update": is_update, "dup_conversation": dup,
                "title": "Northwind backtest %d" % i}
    man = {"dest_email": "bob@example.com", "dest_account": "bbbb2222" + "0" * 24,
           "rows": [add(i, dup=i < 77) for i in range(78)]}
    assert gui._sync_status(man, "") == (
        "78 row files missing from bob@example.com's sidebar - 77 of them for "
        "conversations it already opens under another row")
    one = dict(man, rows=[add(1)])
    assert gui._sync_status(one, "") == (
        "1 row file missing from bob@example.com's sidebar")
    # An older manifest carries no flag: the row-file count alone.
    old = dict(man, rows=[{"name": "local_%d.json" % i, "session_id": "s%d" % i,
                           "is_update": False} for i in range(3)])
    assert gui._sync_status(old, "") == (
        "3 row files missing from bob@example.com's sidebar")
    # Refreshes are counted apart, as rows to refresh.
    upd = dict(man, rows=[add(1, is_update=True), add(2, is_update=True)])
    assert gui._sync_status(upd, "") == (
        "2 rows to refresh in bob@example.com's sidebar")
    mixed = dict(man, rows=[add(1, dup=True), add(2), add(3),
                            add(4, is_update=True), add(5, is_update=True)])
    assert gui._sync_status(mixed, "") == (
        "3 row files missing from bob@example.com's sidebar - 1 of them for a "
        "conversation it already opens under another row; 2 rows to refresh")
    # The filter suffix, in straight quotes.
    assert gui._sync_status(one, "Northwind") == (
        '1 row file missing from bob@example.com\'s sidebar  (filtered by '
        '"Northwind")')
    # The empty states claim only what a row-file count can: no
    # conversation-level "up to date".
    empty = dict(man, rows=[])
    assert gui._sync_status(empty, "") == (
        "No row files to add to bob@example.com's sidebar")
    assert gui._sync_status(empty, "Northwind") == (
        'No row files matching "Northwind" to add to bob@example.com\'s sidebar')
    # Without an email the destination is its id prefix.
    anon = dict(empty, dest_email="")
    assert gui._sync_status(anon, "") == "No row files to add to bbbb2222's sidebar"
