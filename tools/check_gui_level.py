"""Exercise the GUI's Level tab end to end against synthetic stores.

Harness items 9-21 (and 20b) of docs/specs/2026-08-31-gui-level-design.md.
Part A drives the Apply sequence headlessly - a fake UI adapter, the real
engine, a real journal - so the three-plan shape, the refusal stops, the
zero-rows skip and the unconditional refresh are pinned without tkinter.
Part B instantiates the window for real (root withdrawn) for the widget-level
items: the passive notice, the identity banner, the gate, read failures,
window close. Touches nothing real; every title is the fake cast.
"""
import json
import os
import shutil
import sys
import tempfile
import datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import claude_code_sessions as ccs  # noqa: E402

GUIDIR = tempfile.mkdtemp()
shutil.copy(os.path.join(REPO, "claude_code_sessions_gui.py"),
            os.path.join(GUIDIR, "gp.py"))
sys.path.insert(0, GUIDIR)
import gp  # noqa: E402

ok = []
# Captured before Part B patches ccs.default_env for the real-window runs.
REAL_DEFAULT_ENV = ccs.default_env


def check(name, cond, extra=""):
    print("%s %s%s" % ("OK " if cond else "BAD", name,
                       ("  " + extra) if extra else ""))
    ok.append(bool(cond))


A1 = "aaaaaaaa-0000-0000-0000-000000000001"
O1 = "bbbbbbbb-0000-0000-0000-000000000002"
A2 = "cccccccc-0000-0000-0000-000000000003"
O2 = "dddddddd-0000-0000-0000-000000000004"
S1 = "12345678-9abc-def0-1234-56789abcdef0"   # the earlier leg
S2 = "87654321-9abc-def0-1234-56789abcdef0"   # the current leg
T_COLL = "ACME-REVIEW session"
SUGGESTED = "ACME-REVIEW session - earlier leg (Aug 24-28)"
DESKTOP = [(4242, r"c:\program files\windowsapps\claude_2.1_x64\app"
                  r"\claude.exe")]


def ms_local(y, mo, d, h=12):
    return int(datetime.datetime(y, mo, d, h).timestamp() * 1000)


def prose(labels):
    entries = [
        {"cwd": "C:\\Users\\u\\Projects\\Northwind",
         "timestamp": "2026-08-01T00:00:00.000Z", "type": "user",
         "message": {"role": "user", "content": "prose turn " + labels[0]}},
        {"timestamp": "2026-08-01T00:10:00.000Z", "type": "assistant",
         "message": {"role": "assistant", "model": "claude-opus-5",
                     "content": [{"type": "text",
                                  "text": "prose turn " + labels[1]}]}},
    ]
    for lab in labels[2:]:
        entries.append({"type": "user", "message": {"role": "user",
                        "content": "prose turn " + lab}})
    return entries


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


def write_transcript(env, sid, labels):
    folder = os.path.join(env.projects_root, "C--p")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, sid + ".jsonl"), "w",
              encoding="utf-8") as fh:
        for e in prose(labels):
            fh.write(json.dumps(e) + "\n")


def build_env(collide=True, disagree=False):
    """A two-account store. COLLIDE builds the measured-supersession shape:
    S1 (A1 only) and S2 (A2 only) under one title, 48 shared prose turns, so
    each is held in the other sidebar and measured.superseded == S1 with the
    suggested title prefillable. collide=False leaves both conversations
    everywhere - a level store."""
    root = tempfile.mkdtemp(prefix="leveltest-")
    home = os.path.join(root, "home")
    store = os.path.join(root, "Claude", "claude-code-sessions")
    os.makedirs(home)
    env = REAL_DEFAULT_ENV()
    env.home = home
    env.projects_root = os.path.join(home, ".claude", "projects")
    env.store_candidates = [store]
    env.ops_dir = os.path.join(root, "journal", "ops")
    env.moved_log = os.path.join(root, "journal", "moved-log.jsonl")
    env.process_lister = lambda: []
    shared = ["s%d" % i for i in range(48)]
    write_transcript(env, S1, shared + ["a0", "a1"])
    write_transcript(env, S2, shared + ["b%d" % i for i in range(13)])
    write_json(os.path.join(store, A1, O1, "local_s1.json"),
               {"sessionId": "local-1", "cliSessionId": S1, "title": T_COLL,
                "createdAt": ms_local(2026, 8, 24),
                "lastActivityAt": ms_local(2026, 8, 28)})
    write_json(os.path.join(store, A2, O2, "local_s2.json"),
               {"sessionId": "local-2", "cliSessionId": S2, "title": T_COLL,
                "createdAt": ms_local(2026, 8, 24),
                "lastActivityAt": ms_local(2026, 8, 29)})
    if not collide:
        write_json(os.path.join(store, A2, O2, "local_s1.json"),
                   {"sessionId": "local-1", "cliSessionId": S1,
                    "title": T_COLL + " one",
                    "createdAt": ms_local(2026, 8, 24),
                    "lastActivityAt": ms_local(2026, 8, 28)})
        write_json(os.path.join(store, A1, O1, "local_s2.json"),
                   {"sessionId": "local-2", "cliSessionId": S2,
                    "title": T_COLL + " two",
                    "createdAt": ms_local(2026, 8, 24),
                    "lastActivityAt": ms_local(2026, 8, 29)})
    write_json(os.path.join(home, ".claude.json"),
               {"oauthAccount": {"accountUuid": A1, "organizationUuid": O1,
                                 "emailAddress": "alice@example.com"}})
    write_json(os.path.join(root, "Claude", "config.json"),
               {"lastKnownAccountUuid": A2 if disagree else A1})
    return env, root


class FakeUI(object):
    """The sequence's UI adapter, scripted: records statuses, answers the
    stage-2 dialog per CONFIRM (a callable hook runs first), and reports
    whatever GATE returns."""

    def __init__(self, confirm=True, gate=None, on_confirm=None):
        self.statuses = []
        self.confirmed_with = []
        self.remaining = 0
        self.truncate = False
        self._confirm = confirm
        self._gate = gate or (lambda: None)
        self._on_confirm = on_confirm

    def status(self, text):
        self.statuses.append(text)

    def gate(self):
        return self._gate()

    def truncate_requested(self):
        return self.truncate

    def confirm_stage2(self, fresh):
        self.confirmed_with.append(fresh)
        if self._on_confirm:
            self._on_confirm()
        return self._confirm


class PlanSpy(object):
    """Counts plan_converge calls and captures (flags, manifest); counts
    run_converge calls and which manifest object each received."""

    def __init__(self):
        self.plans = []
        self.runs = []
        self._plan, self._run = ccs.plan_converge, ccs.run_converge

    def __enter__(self):
        def plan(env, flags):
            m = self._plan(env, flags)
            self.plans.append((flags, m))
            return m

        def run(env, manifest):
            self.runs.append(manifest)
            return self._run(env, manifest)
        ccs.plan_converge, ccs.run_converge = plan, run
        return self

    def __exit__(self, *a):
        ccs.plan_converge, ccs.run_converge = self._plan, self._run


roots = []

# ------------------------------------------------- 9. the hold row + dialog
env, root = build_env()
roots.append(root)
preview = ccs.plan_converge(env, ccs.ConvergeFlags())
check("preview holds both directions of the collision",
      len(preview["holds"]) == 2 and not preview["rows"],
      "%d holds, %d rows" % (len(preview["holds"]), len(preview["rows"])))
models = gp._hold_models(preview)
check("one hold row for the pair - one rename clears every sidebar",
      len(models) == 1, str(len(models)))
m0 = models[0] if models else {}
check("  prefilled from the measured suggestion",
      m0.get("prefill") == SUGGESTED and m0.get("ticked") is True,
      repr(m0.get("prefill")))
check("  aimed at measured.superseded", m0.get("target_sid") == S1)
steps, problems = gp._level_steps_stage1(models)
head, mappings, footer = gp._stage1_dialog_parts(steps)
check("stage-1 dialog text carries the full old->new mapping",
      any(repr(T_COLL) in line and repr(SUGGESTED) in line
          for line in mappings), "; ".join(mappings))
check("  and the every-account-scope sentence",
      "every account" in footer)

# --------------------------------------------- 10. full Apply, three plans
with PlanSpy() as spy:
    ui = FakeUI(confirm=True)
    seq, refresh = gp._run_level_apply(env, steps, "", ui)
check("stage 1 landed the rename", seq["landed"] == 1,
      "landed=%s stage2=%s" % (seq["landed"], seq["stage2"]))
check("stage 2 completed", seq["stage2"] == "completed", seq["stage2"])
check("plan_converge ran twice inside the press (fresh + refresh)",
      len(spy.plans) == 2, str(len(spy.plans)))
check("run_converge ran once, with the FRESH manifest - never the preview",
      len(spy.runs) == 1 and spy.runs[0] is spy.plans[0][1]
      and spy.runs[0] is not preview)
check("rows were created in both sidebars",
      sorted(len(m["rows"]) for m in (spy.plans[0][1],)) == [2]
      and all(r.get("written") for r in spy.plans[0][1]["rows"]))
check("the post-apply refresh replanned fresh",
      refresh is not None and refresh[0] == "ok"
      and not refresh[2]["rows"] and not refresh[2]["holds"])
check("  and the scoreboard reports level",
      refresh[1]["complete"]["short"] == 0
      and gp._scoreboard_half(refresh[1]) == "Level: 2 / 2 - 0 short.")
check("per-step progress statuses, in order",
      ui.statuses == ["Applying rename 1 of 1...", "Planning the copy...",
                      "Creating rows...", "Re-measuring..."],
      str(ui.statuses))
check("completion status carries both halves",
      gp._sequence_status(seq, gp._scoreboard_half(refresh[1]))
      == "Applied (1 rename + 1 converge) - Level: 2 / 2 - 0 short.",
      gp._sequence_status(seq, gp._scoreboard_half(refresh[1])))
post_models = gp._merge_hold_models(gp._hold_models(refresh[2]), models)
check("the post-apply rows come from the third, post-write plan",
      post_models == [], str(post_models))

# ------------------------------- 11. retitle refusal stops the sequence
env, root = build_env()
roots.append(root)
write_json(os.path.join(env.store_candidates[0], A2, O2, "local_pad.json"),
           {"sessionId": "local-p", "cliSessionId":
            "0dead000-9abc-def0-1234-56789abcdef0",
            "title": "Northwind backtest",
            "createdAt": ms_local(2026, 8, 20),
            "lastActivityAt": ms_local(2026, 8, 21)})
two_steps = [
    {"key": (S1, T_COLL), "target_sid": S1, "old_title": T_COLL,
     "new_title": SUGGESTED},
    # Collides with the padding row in S2's own sidebar - plan_retitle
    # refuses, and the refusal must stop the sequence cold.
    {"key": (S2, T_COLL), "target_sid": S2, "old_title": T_COLL,
     "new_title": "Northwind backtest"},
]
with PlanSpy() as spy:
    ui = FakeUI(confirm=True)
    seq, refresh = gp._run_level_apply(env, two_steps, "", ui)
check("second rename refused, sequence stopped",
      seq["landed"] == 1 and seq["rename_refusal"] is not None
      and seq["rename_refusal"][0] == 2)
check("  the refusal is the engine's, verbatim",
      "already names a different conversation" in seq["rename_refusal"][1])
check("  no converge call was made", len(spy.runs) == 0)
check("  status names the one landed rename",
      gp._sequence_status(seq)
      == "1 rename landed, each undoable; the second was refused.",
      gp._sequence_status(seq))
check("  the pane still replans fresh (the refresh ran)",
      refresh is not None and refresh[0] == "ok" and len(spy.plans) == 1)

# --------------------- 12. stage-2 run_converge refusal between the stages
env, root = build_env()
roots.append(root)
models = gp._hold_models(ccs.plan_converge(env, ccs.ConvergeFlags()))
steps, _ = gp._level_steps_stage1(models)


def app_reopens():
    env.process_lister = lambda: DESKTOP


with PlanSpy() as spy:
    ui = FakeUI(confirm=True, on_confirm=app_reopens)
    seq, refresh = gp._run_level_apply(env, steps, "", ui)
env.process_lister = lambda: []
check("run_converge refused (the desktop app reopened between stages)",
      seq["stage2"] == "refused" and seq["converge_problem"] is not None,
      seq["stage2"])
check("  verbatim RULING 4 text",
      "Claude" in (seq["converge_problem"] or ("", ""))[1])
check("  renames landed / nothing copied",
      seq["landed"] == 1 and gp._sequence_status(seq)
      == "1 rename landed, each undoable; nothing was copied - the copy "
         "was refused.", gp._sequence_status(seq))
check("  pane replanned anyway", refresh is not None and refresh[0] == "ok")
check("  the renames stand - a complete, valid store state",
      any("earlier leg" in (json.load(open(os.path.join(
          env.store_candidates[0], A1, O1, n), encoding="utf-8"))
          .get("title") or "")
          for n in os.listdir(os.path.join(env.store_candidates[0], A1, O1))))

# ---------- the mid-sequence gate: renames stand, and the pane says so
env, root = build_env()
roots.append(root)
models = gp._hold_models(ccs.plan_converge(env, ccs.ConvergeFlags()))
steps, _ = gp._level_steps_stage1(models)
gate_state = {"hits": 0}


def gate_after_stage1():
    gate_state["hits"] += 1
    return ["20990101T000009Z-feedcc"] if gate_state["hits"] > 1 else None


with PlanSpy() as spy:
    ui = FakeUI(confirm=True, gate=gate_after_stage1)
    seq, refresh = gp._run_level_apply(env, steps, "", ui)
check("a gate hit between the stages stops the copy",
      seq["stage2"] == "gate" and seq["landed"] == 1 and len(spy.runs) == 0)
check("  but the landed rename still gets its refresh",
      refresh is not None and refresh[0] == "ok")
check("  and the status names what landed, never 'nothing was written'",
      gp._sequence_status(seq)
      == "1 rename landed, each undoable; the copy did not run - "
         "interrupted operation(s) need attention (see Health).",
      gp._sequence_status(seq))

# ---------- close confirmed while the stage-2 dialog is open
env, root = build_env()
roots.append(root)
models = gp._hold_models(ccs.plan_converge(env, ccs.ConvergeFlags()))
steps, _ = gp._level_steps_stage1(models)
with PlanSpy() as spy:
    ui = FakeUI(confirm=True)

    def confirm_then_close():
        ui.truncate = True               # the close prompt was confirmed
    ui._on_confirm = confirm_then_close
    seq, refresh = gp._run_level_apply(env, steps, "", ui)
check("an OK on the stage-2 dialog after a confirmed close runs nothing",
      seq["stage2"] == "truncated" and len(spy.runs) == 0
      and refresh is None)

# ------------------------------------------------- 13. RULING 4 up front
env, root = build_env()
roots.append(root)
env.process_lister = lambda: DESKTOP
check("the passive notice has data: claude_running reports the app",
      bool(ccs.claude_running(env)))
models = gp._hold_models(ccs.plan_converge(env, ccs.ConvergeFlags()))
steps, _ = gp._level_steps_stage1(models)
with PlanSpy() as spy:
    ui = FakeUI(confirm=True)
    seq, refresh = gp._run_level_apply(env, steps, "", ui)
check("with the guard tripping at stage 1, nothing was written",
      seq["landed"] == 0 and seq["rename_refusal"] is not None
      and seq["rename_refusal"][0] == 1 and len(spy.runs) == 0)
check("  the refusal is modal material, verbatim",
      "Close it" in seq["rename_refusal"][1]
      or "running" in seq["rename_refusal"][1])
check("  status renders the zero honestly",
      gp._sequence_status(seq) == "The rename was refused - nothing was "
                                  "written.", gp._sequence_status(seq))
env.process_lister = lambda: []

# ----------------------------------------- 20. zero-rows stage 2, and Nothing
env, root = build_env(collide=False)          # already level, nothing to copy
roots.append(root)
one_step = [{"key": (S1, T_COLL + " one"), "target_sid": S1,
             "old_title": T_COLL + " one",
             "new_title": "ACME-REVIEW session - renamed leg (Aug 24-28)"}]
with PlanSpy() as spy:
    ui = FakeUI(confirm=True)
    seq, refresh = gp._run_level_apply(env, one_step, "", ui)
check("renames landed, fresh plan empty: no dialog, no run_converge",
      seq["landed"] == 1 and seq["stage2"] == "empty"
      and ui.confirmed_with == [] and len(spy.runs) == 0)
check("  and the post-mutation refresh still ran",
      refresh is not None and refresh[0] == "ok" and len(spy.plans) == 2)
check("  status carries both halves",
      gp._sequence_status(seq)
      == "Applied (1 rename; nothing to copy) - the rows below are current.",
      gp._sequence_status(seq))
with PlanSpy() as spy:
    ui = FakeUI(confirm=True)
    seq, refresh = gp._run_level_apply(env, [], "", ui)
check("the all-skipped variant reads Nothing to do.",
      seq["stage2"] == "empty" and gp._sequence_status(seq)
      == "Nothing to do.", gp._sequence_status(seq))
check("  and even that press ended in a refresh",
      refresh is not None and refresh[0] == "ok")

# ------------------- 14/20b. live threads to stage 2; the refresh drops it
env, root = build_env(disagree=True)
roots.append(root)
man = ccs.plan_converge(env, ccs.ConvergeFlags())
check("a disagreeing store plans with the banner field",
      isinstance(man.get("identity_disagreement"), dict))
models = gp._hold_models(man)
steps, _ = gp._level_steps_stage1(models)
with PlanSpy() as spy:
    ui = FakeUI(confirm=True)
    seq, refresh = gp._run_level_apply(env, steps, "alice@example.com", ui)
check("the stage-2 plan still carried the live assertion",
      len(spy.plans) == 2 and spy.plans[0][0].live == "alice@example.com",
      repr([p[0].live for p in spy.plans]))
check("  and stage 2 completed under it", seq["stage2"] == "completed")
check("the refresh plans with live cleared - the sequence has ended",
      spy.plans[1][0].live == "")
check("  so the banner re-raises while the files still disagree",
      refresh[0] == "ok"
      and isinstance(refresh[2].get("identity_disagreement"), dict))

# 20b: a stage-1 refusal also ends the sequence; the post-refusal replan
# re-raises the banner.
env, root = build_env(disagree=True)
roots.append(root)
env.process_lister = lambda: DESKTOP          # trips RULING 4 at rename 1
models = gp._hold_models(ccs.plan_converge(env, ccs.ConvergeFlags()))
steps, _ = gp._level_steps_stage1(models)


def clear_guard():
    env.process_lister = lambda: []


with PlanSpy() as spy:
    ui = FakeUI(confirm=True)
    seq, refresh = gp._run_level_apply(env, steps, "alice@example.com", ui)
clear_guard()
check("stage-1 refusal ends the sequence with live cleared for the replan",
      seq["rename_refusal"] is not None and len(spy.plans) == 1
      and spy.plans[0][0].live == "")
check("  and the post-refusal replan re-raises the banner",
      refresh is not None and refresh[0] == "ok"
      and isinstance(refresh[2].get("identity_disagreement"), dict))

# ------------------------------- 21. the final refresh fails after success
env, root = build_env()
roots.append(root)
models = gp._hold_models(ccs.plan_converge(env, ccs.ConvergeFlags()))
steps, _ = gp._level_steps_stage1(models)
_real_gather = ccs.gather_alignment


def broken_gather(_env):
    raise RuntimeError("simulated re-measure failure")


ccs.gather_alignment = broken_gather
try:
    with PlanSpy() as spy:
        ui = FakeUI(confirm=True)
        seq, refresh = gp._run_level_apply(env, steps, "", ui)
finally:
    ccs.gather_alignment = _real_gather
check("converge completed but the refresh failed",
      seq["stage2"] == "completed" and refresh is not None
      and refresh[0] == "error")
check("  applied-but-unverified is distinct from not-applied",
      seq["mutated"] is True and len(spy.runs) == 1)

# =====================================================================
# Part B - the window itself, instantiated for real (root withdrawn),
# workers inlined so the harness pumps deterministically with no sleeps.
# The same trick check_gui_live_and_newer.py established.
import threading as _real_threading  # noqa: E402
import tkinter as tk  # noqa: E402


class _Inline(object):
    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._t, self._a, self._k = target, args, kwargs or {}

    def start(self):
        self._t(*self._a, **self._k)


class _FakeThreading(object):
    Thread = _Inline
    # The Level UI bridge asks which thread it is on; inline workers run on
    # the main thread, so the bridge calls its dialogs directly.
    current_thread = staticmethod(_real_threading.current_thread)
    main_thread = staticmethod(_real_threading.main_thread)
    Event = _real_threading.Event


gp.threading = _FakeThreading
gp.load_pref = lambda: ""
gp.save_pref = lambda _v: None

_CURRENT = {"env": None}
gp.ccs.default_env = lambda: _CURRENT["env"]


class Modals(object):
    """Every messagebox call recorded; answers scripted per title."""

    def __init__(self):
        self.calls = []
        self.answers = {}
        self.default = True

    def askokcancel(self, title, message, **kw):
        self.calls.append(("askokcancel", title, message))
        return self.answers.get(title, self.default)

    def showwarning(self, title, message, **kw):
        self.calls.append(("showwarning", title, message))

    def showerror(self, title, message, **kw):
        self.calls.append(("showerror", title, message))

    def of(self, kind, title=None):
        return [c for c in self.calls
                if c[0] == kind and (title is None or c[1] == title)]


def open_app(env, stage1=True):
    """A real SyncApp over ENV, withdrawn, stage-1 dialog scripted (a
    Toplevel needs a display pump this harness does not want mid-check)."""
    _CURRENT["env"] = env
    modals = Modals()
    gp.messagebox = modals
    tkroot = tk.Tk()
    tkroot.withdraw()
    stage1_calls = []

    def fake_stage1(self, steps):
        stage1_calls.append(list(steps))
        return stage1
    gp.SyncApp._confirm_stage1 = fake_stage1
    app = gp.SyncApp(tkroot)
    app._stage1_calls = stage1_calls

    def settle():
        for _ in range(30):
            tkroot.update()
    return app, tkroot, modals, settle


def plant_op(env, op_id, status="writing", op_type="repoint", at=None):
    d = os.path.join(env.ops_dir, op_id)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"op_id": op_id, "status": status, "op_type": op_type,
                   "history": [{"status": "journaled",
                                "at": at or (1_800_000_000.0)}]}, fh)


# ---------------------- 13 (render) + full Apply through the real window
env, root = build_env()
roots.append(root)
env.process_lister = lambda: DESKTOP
app, tkroot, modals, settle = open_app(env)
settle()
check("the passive notice renders while the app is up",
      bool(app.level_notice.winfo_manager()))
env.process_lister = lambda: []
app.refresh_level()
settle()
check("  and clears once it is closed - weather, not a gate",
      not app.level_notice.winfo_manager())
check("the hold row rendered as widgets",
      len(app.hold_models) == 1 and len(app._hold_widgets) == 2)
check("Apply is enabled in the naming-only state",
      str(app.level_apply_btn.cget("state")) == "normal")
app.on_level_apply()
settle()
check("stage-1 dialog saw the steps", len(app._stage1_calls) == 1
      and app._stage1_calls[0][0]["new_title"] == SUGGESTED)
check("stage-2 dialog described the fresh plan",
      any("Create 2 rows across the 2 accounts named?" in c[2]
          for c in modals.of("askokcancel", "Create the rows?")),
      str(modals.of("askokcancel")))
check("the completion status carries both halves",
      app.level_status.get()
      == "Applied (1 rename + 1 converge) - Level: 2 / 2 - 0 short.",
      app.level_status.get())
check("the post-apply pane shows no holds", app.hold_models == [])
check("Undo points at the converge",
      app.undo_target is not None and app.undo_target["type"] == "converge"
      and app.undo_target["label"] == "Undo last converge (2 rows)",
      str(app.undo_target))
check("no refusal modal on the happy path",
      not modals.of("showwarning"))
check("the sync tab's Apply is void until IT replans - the converge wrote "
      "into its destination",
      str(app.apply_btn.cget("state")) == "disabled")
app.refresh()
settle()
check("  and its own Refresh re-arms it against a fresh plan",
      str(app.apply_btn.cget("state")) == "normal"
      or not (app.manifest or {}).get("rows"))
# A stale-generation callback must still pay back its busy(True): the
# counted busy state deadlocks the whole window otherwise.
app.busy(True)
app._level_plan_done(-1, None, None, [], [], ("refusal", "stale"))
check("a superseded worker callback still releases the busy counter",
      str(app.level_refresh_btn.cget("state")) == "normal")
tkroot.destroy()

# ------------------- 14. banner buttons; typed text survives the replan
env, root = build_env(disagree=True)
roots.append(root)
app, tkroot, modals, settle = open_app(env)
settle()
check("the identity banner renders one button per disagreeing store",
      len(app._banner_widgets) == 2, str(len(app._banner_widgets)))
check("  labeled by email where known",
      any("alice@example.com" in b.cget("text")
          for b in app._banner_widgets))
mine = "ACME-REVIEW session - my own words"
app.hold_models[0]["_entry_var"].set(mine)
app.hold_models[0]["_tick_var"].set(False)
app._banner_widgets[0].invoke()
settle()
check("choosing an account replans with the assertion held",
      app.level_live and app.level_manifest is not None)
check("  the banner now shows the in-force line instead of the pickers",
      len(app._banner_widgets) == 1)
check("  and the typed entry text and tick survived that replan",
      app.hold_models and app.hold_models[0]["_entry_var"].get() == mine
      and app.hold_models[0]["_tick_var"].get() is False)
tkroot.destroy()

# ------------- 16. two unresolved ops: red line, listing, copy, refresh
env, root = build_env()
roots.append(root)
plant_op(env, "20260831T000001Z-aaaaaa")
plant_op(env, "20260831T000002Z-bbbbbb", at=1_800_000_100.0)
app, tkroot, modals, settle = open_app(env)
settle()
check("the plural red line renders",
      app.gate_var.get()
      == "!! 2 interrupted operation(s) need attention - see Health.",
      app.gate_var.get())
check("  on every tab", all(lbl.winfo_manager()
                            for lbl, _a in app._gate_labels))
check("  Apply and Undo are gated",
      str(app.level_apply_btn.cget("state")) == "disabled"
      and str(app.apply_btn.cget("state")) == "disabled"
      and str(app.undo_btn.cget("state")) == "disabled")
health = app.health_text.get("1.0", "end")
check("both ops are listed",
      "20260831T000001Z-aaaaaa" in health
      and "20260831T000002Z-bbbbbb" in health)
check("  the bare-recover target is marked, once",
      health.count("listed first by a bare 'recover'") == 1
      and health.index("listed first") > health.index("aaaaaa"))
check("  the Copy button is offered, and no resolution buttons exist",
      bool(app.copy_btn.winfo_manager()))
check("  the copyable command is the CLI hand-off",
      "claude-code-sessions recover" in health)
for op_id in ("20260831T000001Z-aaaaaa", "20260831T000002Z-bbbbbb"):
    mp = os.path.join(env.ops_dir, op_id, "manifest.json")
    with open(mp, encoding="utf-8") as fh:
        m = json.load(fh)
    m["status"] = "rolled_back"          # resolved fixture-side, as recover would
    with open(mp, "w", encoding="utf-8") as fh:
        json.dump(m, fh)
app.on_doctor()
settle()
check("a Refresh that finds nothing unresolved clears the banner",
      app.gate_var.get() == "" and not any(lbl.winfo_manager()
                                           for lbl, _a in app._gate_labels))
check("  and lifts the gate",
      str(app.level_apply_btn.cget("state")) == "normal")
tkroot.destroy()

# ---------------------- 17. window close during a two-rename sequence
env, root = build_env(collide=False)
roots.append(root)
app, tkroot, modals, settle = open_app(env)
settle()
app.hold_models = [
    {"key": (S1, "a"), "title": T_COLL + " one", "evidence": "x",
     "target_sid": S1, "prefill": "", "entry": "ACME-REVIEW leg one",
     "editable": True, "ticked": True, "degrade_reason": "",
     "classification": "supersession"},
    {"key": (S2, "b"), "title": T_COLL + " two", "evidence": "x",
     "target_sid": S2, "prefill": "", "entry": "ACME-REVIEW leg two",
     "editable": True, "ticked": True, "degrade_reason": "",
     "classification": "supersession"},
]
calls = {"retitle": 0}
_real_run_retitle = ccs.run_retitle


def closing_run_retitle(env_, manifest):
    final = _real_run_retitle(env_, manifest)
    calls["retitle"] += 1
    if calls["retitle"] == 1:
        app._on_close()                  # the user clicks X mid-operation
    return final


ccs.run_retitle = closing_run_retitle
modals.answers["Close during an operation?"] = True
with PlanSpy() as spy:
    app.on_level_apply()
    try:
        settle()
    except tk.TclError:
        pass                             # the root died at the boundary - expected
ccs.run_retitle = _real_run_retitle
closes = modals.of("askokcancel", "Close during an operation?")
check("the close prompt stated the remaining-step count",
      len(closes) == 1 and "2 remaining step(s) will NOT run" in closes[0][2],
      closes[0][2] if closes else "-")
check("the in-flight rename completed; the remainder was truncated",
      calls["retitle"] == 1)
check("  no further engine calls - no plan, no converge",
      len(spy.plans) == 0 and len(spy.runs) == 0)
try:
    gone = not tkroot.winfo_exists()
except tk.TclError:
    gone = True                          # the application object itself died
check("  the window closed at the boundary", gone)
renamed = [json.load(open(os.path.join(env.store_candidates[0], A1, O1, n),
                          encoding="utf-8")).get("title")
           for n in os.listdir(os.path.join(env.store_candidates[0], A1, O1))]
check("  the landed rename is in the store, journalled",
      "ACME-REVIEW leg one" in renamed, str(renamed))

# --------------------------- 18. read failure on open: in-pane, no modal
env, root = build_env()
roots.append(root)
_real_gather = ccs.gather_alignment


def broken(_env):
    raise RuntimeError("simulated unreadable store")


ccs.gather_alignment = broken
try:
    app, tkroot, modals, settle = open_app(env)
    settle()
finally:
    ccs.gather_alignment = _real_gather
check("a read failure on open renders in the pane",
      "simulated unreadable store" in app.level_text.get("1.0", "end"))
check("  with the status line set",
      app.level_status.get() == "Something went wrong",
      app.level_status.get())
check("  never as a modal", not modals.of("showwarning")
      and not modals.of("showerror"))
check("  and Refresh stays enabled as the retry",
      str(app.level_refresh_btn.cget("state")) == "normal")
app.refresh_level()
settle()
check("  a later Refresh recovers", app.level_manifest is not None)
# A read failure must not erase a standing gate: the scan's findings
# survive the gather/plan raising.
plant_op(env, "20260831T000005Z-eeeeee")
ccs.gather_alignment = broken
try:
    app.on_doctor()                      # raises the gate first
    settle()
    app.refresh_level()                  # then the failing read
    settle()
finally:
    ccs.gather_alignment = _real_gather
check("a failing Refresh keeps the standing red line",
      "1 interrupted operation(s)" in app.gate_var.get(),
      app.gate_var.get())
check("  and the gate stays down on Undo",
      str(app.undo_btn.cget("state")) == "disabled")
tkroot.destroy()

# ------------------- 19. press-time gate re-scan, every mutation press
env, root = build_env()
roots.append(root)
app, tkroot, modals, settle = open_app(env)
settle()
# A completed sync op so the Undo button is offered, THEN an unresolved op
# planted after the pane rendered - the scan at open is stale now.
plant_op(env, "20260831T000003Z-cccccc", status="completed", op_type="sync",
         at=1_800_000_000.0)
mp = os.path.join(env.ops_dir, "20260831T000003Z-cccccc", "manifest.json")
with open(mp, encoding="utf-8") as fh:
    m = json.load(fh)
m["rows"] = [{"written": True}]
m["dest_email"] = "dorm@example.com"
with open(mp, "w", encoding="utf-8") as fh:
    json.dump(m, fh)
app._update_undo_button()
check("undo is offered before the plant", app.undo_target is not None)
plant_op(env, "20260831T000004Z-dddddd", at=1_800_000_200.0)
with PlanSpy() as spy:
    app.on_level_apply()
    settle()
check("the next Apply press aborts with the red line",
      "interrupted operation" in app.gate_var.get()
      and len(app._stage1_calls) == 0)
check("  no engine mutation call was made",
      len(spy.plans) == 0 and len(spy.runs) == 0)
app.manifest = {"rows": [], "tally": {}, "dest_account": "x" * 32}
before = len(modals.calls)
app.on_apply()
check("the sync tab's Apply press aborts the same way",
      len(modals.calls) == before)
app.on_undo()
check("and the Undo press too - no confirmation was even asked",
      len(modals.calls) == before)
tkroot.destroy()

for r in roots:
    shutil.rmtree(r, ignore_errors=True)
shutil.rmtree(GUIDIR, ignore_errors=True)
print()
print("ALL PASS" if all(ok) else "SOME CHECKS FAILED")
sys.exit(0 if all(ok) else 1)
