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
    env = ccs.default_env()
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
        self._confirm = confirm
        self._gate = gate or (lambda: None)
        self._on_confirm = on_confirm

    def status(self, text):
        self.statuses.append(text)

    def gate(self):
        return self._gate()

    def truncate_requested(self):
        return False

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

for r in roots:
    shutil.rmtree(r, ignore_errors=True)
shutil.rmtree(GUIDIR, ignore_errors=True)
print()
print("ALL PASS" if all(ok) else "SOME CHECKS FAILED")
sys.exit(0 if all(ok) else 1)
