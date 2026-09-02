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


def build_env(collide=True, disagree=False, recency_disagree=False):
    """A two-account store. COLLIDE builds the measured-supersession shape:
    S1 (A1 only) and S2 (A2 only) under one title, 48 shared prose turns, so
    each is held in the other sidebar and measured.superseded == S1 with the
    suggested title prefillable. collide=False leaves both conversations
    everywhere - a level store. RECENCY_DISAGREE gives the smaller leg (S1)
    the NEWER lastActivityAt, so overlap says S1 is contained in S2 while
    recency says S1 is the live one: the pair measures as unmeasured,
    'overlap and recency disagree' - a hold with no suggestion, which the
    window must still let a human name (0.15.1, F)."""
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
                "lastActivityAt": ms_local(2026, 8, 30 if recency_disagree
                                           else 28)})
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
    """The sequence's UI adapter, scripted, mirroring _TkLevelUI member for
    member: records statuses, answers the stage-2 dialog per CONFIRM (a
    callable hook runs first), reports whatever GATE returns, and answers
    the stage-2 identity question with ASK (a store path, or None for
    Cancel; ON_ASK runs first)."""

    def __init__(self, confirm=True, gate=None, on_confirm=None, ask=None,
                 on_ask=None):
        self.statuses = []
        self.confirmed_with = []
        self.asked_with = []
        self.remaining = 0
        self.truncate = False
        self._confirm = confirm
        self._gate = gate or (lambda: None)
        self._on_confirm = on_confirm
        self._ask = ask
        self._on_ask = on_ask

    def ask_live(self, fresh):
        self.asked_with.append(fresh)
        if self._on_ask:
            self._on_ask()
        return self._ask

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

# ----------------- F (0.15.1). an unmeasured pair is nameable from the window
env, root = build_env(recency_disagree=True)
roots.append(root)
preview = ccs.plan_converge(env, ccs.ConvergeFlags())
verdicts = [h.get("measured") or {} for h in preview["holds"]]
check("the fixture measures as 'overlap and recency disagree'",
      len(verdicts) == 2 and all(
          v.get("classification") == "unmeasured"
          and v.get("reason") == "overlap and recency disagree"
          for v in verdicts),
      "; ".join("%s/%s" % (v.get("classification"), v.get("reason"))
                for v in verdicts))
models = gp._hold_models(preview)
check("both legs render as rows - editable, empty, unticked",
      len(models) == 2 and all(m["editable"] and not m["ticked"]
                               and m["entry"] == "" and m["prefill"] == ""
                               for m in models),
      str([(m["editable"], m["ticked"], m["entry"]) for m in models]))
check("  each aimed at its OWN held conversation",
      sorted(m["target_sid"] for m in models) == sorted([S1, S2]),
      str([m["target_sid"] for m in models]))
by_target = {m["target_sid"]: m for m in models}
check("  the evidence names the other leg and the account it collides in",
      S2[:8] in by_target.get(S1, {}).get("evidence", "")
      and S1[:8] in by_target.get(S2, {}).get("evidence", "")
      and "alice@example.com" in by_target.get(S2, {}).get("evidence", ""),
      by_target.get(S1, {}).get("evidence", "-"))
check("  the header counts them as needing a name",
      gp._level_state(ccs.gather_alignment(env), preview, models)["status"]
      == "Nothing to copy - 2 held: 2 need a name.",
      gp._level_state(ccs.gather_alignment(env), preview, models)["status"])
named = [dict(m) for m in models]
named[0]["ticked"] = True
named[0]["entry"] = "ACME-REVIEW session - the other leg"
steps, problems = gp._level_steps_stage1(named)
check("one ticked unmeasured row is one rename, aimed at that row's leg",
      problems == [] and len(steps) == 1
      and steps[0]["target_sid"] == models[0]["target_sid"],
      str(problems or steps))
with PlanSpy() as spy:
    seq, refresh = gp._run_level_apply(env, steps, "", FakeUI(confirm=True))
check("the rename landed and the copy completed",
      seq["landed"] == 1 and seq["stage2"] == "completed",
      "landed=%s stage2=%s" % (seq["landed"], seq["stage2"]))
check("  naming ONE leg cleared the collision - the fresh plan holds nothing",
      refresh is not None and refresh[0] == "ok"
      and not refresh[2]["holds"] and not refresh[2]["rows"])
check("  and the sidebars are level",
      refresh[1]["complete"]["short"] == 0
      and gp._scoreboard_half(refresh[1]) == "Level: 2 / 2 - 0 short.")

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
    seq, refresh = gp._run_level_apply(env, steps, "alice@example.com", ui,
                                       pair=(A1, A2))
check("the stage-2 plan still carried the live assertion",
      len(spy.plans) == 2 and spy.plans[0][0].live == "alice@example.com",
      repr([p[0].live for p in spy.plans]))
check("  and stage 2 completed under it", seq["stage2"] == "completed")
check("the refresh plans with live cleared - the sequence has ended",
      spy.plans[1][0].live == "")
check("  so the banner re-raises while the files still disagree",
      refresh[0] == "ok"
      and isinstance(refresh[2].get("identity_disagreement"), dict))

check("  and the answer's pair travelled with the sequence",
      seq.get("live_pair") == (A1, A2), str(seq.get("live_pair")))

# 20b, INVERTED by the GUI polish (Change 2 item 5, rule (a)): a stage-1
# refusal ran nothing and consumed nothing, so the answer survives it - the
# post-refusal replan CARRIES the assertion while the files still disagree.
env, root = build_env(disagree=True)
roots.append(root)
env.process_lister = lambda: DESKTOP          # trips RULING 4 at rename 1
models = gp._hold_models(ccs.plan_converge(env, ccs.ConvergeFlags()))
steps, _ = gp._level_steps_stage1(models)


def clear_guard():
    env.process_lister = lambda: []


with PlanSpy() as spy:
    ui = FakeUI(confirm=True)
    seq, refresh = gp._run_level_apply(env, steps, "alice@example.com", ui,
                                       pair=(A1, A2))
clear_guard()
check("a stage-1 refusal keeps the answer: the post-refusal replan carries it",
      seq["rename_refusal"] is not None and len(spy.plans) == 1
      and spy.plans[0][0].live == "alice@example.com",
      repr([p[0].live for p in spy.plans]))
check("  and the files still disagree, so the manifest carries the field",
      refresh is not None and refresh[0] == "ok"
      and isinstance(refresh[2].get("identity_disagreement"), dict))

# Rule (a) and `unchanged`: a converge whose apply-time re-check certified
# the answer and then found nothing to write spent the assertion exactly as
# the CLI's would - the refresh plans without it.
env, root = build_env(disagree=True)
roots.append(root)
models = gp._hold_models(ccs.plan_converge(env, ccs.ConvergeFlags()))
steps, _ = gp._level_steps_stage1(models)
import base64 as _b64  # noqa: E402


def plant_rows_from(spy_ref):
    """Write the fresh plan's rows fixture-side before run_converge, so its
    re-check marks every pair already_present and returns unchanged."""
    def hook():
        for r in spy_ref.plans[0][1]["rows"]:
            with open(r["dest_path"], "wb") as fh:
                fh.write(_b64.b64decode(r["post_b64"]))
    return hook


with PlanSpy() as spy:
    ui = FakeUI(confirm=True, on_confirm=plant_rows_from(spy))
    seq, refresh = gp._run_level_apply(env, steps, "alice@example.com", ui,
                                       pair=(A1, A2))
check("a converge whose re-check writes nothing ends 'unchanged'",
      seq["stage2"] == "unchanged" and len(spy.runs) == 1, seq["stage2"])
check("  and that clears the answer too - the refresh plans without it",
      len(spy.plans) == 2 and spy.plans[1][0].live == "",
      repr([p[0].live for p in spy.plans]))

# The zero-rows `empty` outcome is different: no converge ran, nothing was
# certified, the answer stays and the refresh carries it.
env, root = build_env(collide=False, disagree=True)
roots.append(root)
one_step = [{"key": (S1, T_COLL + " one"), "target_sid": S1,
             "old_title": T_COLL + " one",
             "new_title": "ACME-REVIEW session - renamed leg (Aug 24-28)"}]
with PlanSpy() as spy:
    ui = FakeUI(confirm=True)
    seq, refresh = gp._run_level_apply(env, one_step, "alice@example.com", ui,
                                       pair=(A1, A2))
check("the zero-rows empty outcome keeps the answer",
      seq["stage2"] == "empty" and len(spy.runs) == 0
      and len(spy.plans) == 2 and spy.plans[1][0].live == "alice@example.com",
      "%s %s" % (seq["stage2"], [p[0].live for p in spy.plans]))

# ------------------- 13 (GUI polish). the stage-2 ask, from the fresh plan
# No answer held, the files disagree: the sequence asks AFTER the renames,
# from the fresh manifest, and replans with the answer before the stage-2
# dialog - never before stage 1 from the stale preview.
env, root = build_env(disagree=True)
roots.append(root)
models = gp._hold_models(ccs.plan_converge(env, ccs.ConvergeFlags()))
steps, _ = gp._level_steps_stage1(models)
A1_STORE = os.path.join(env.store_candidates[0], A1, O1)
with PlanSpy() as spy:
    ui = FakeUI(confirm=True, ask=A1_STORE)
    seq, refresh = gp._run_level_apply(env, steps, "", ui)
check("the picker fires after the renames, from the fresh plan",
      seq["landed"] == 1 and len(ui.asked_with) == 1
      and ui.asked_with[0] is spy.plans[0][1]
      and isinstance(ui.asked_with[0].get("identity_disagreement"), dict),
      "asked %d" % len(ui.asked_with))
check("  an answer replans: four plans counting the preview (fresh, replan, "
      "refresh)", len(spy.plans) == 3, str(len(spy.plans)))
check("  the stage-2 dialog saw the REPLANNED manifest, which carries "
      "live_asserted",
      ui.confirmed_with and ui.confirmed_with[0] is spy.plans[1][1]
      and spy.plans[1][1].get("live_asserted") == A1,
      repr((spy.plans[1][1] or {}).get("live_asserted")))
check("  and run_converge received that manifest",
      len(spy.runs) == 1 and spy.runs[0] is spy.plans[1][1]
      and seq["stage2"] == "completed", seq["stage2"])
check("  the answer is recorded on the result with its pair",
      seq.get("asked") == A1_STORE and seq.get("live_pair") == (A1, A2),
      str((seq.get("asked"), seq.get("live_pair"))))
check("  and the refresh, after a completed write, plans without it",
      spy.plans[2][0].live == "", repr(spy.plans[2][0].live))

# Cancel on the picker: the renames stand, the refresh runs, no converge.
env, root = build_env(disagree=True)
roots.append(root)
models = gp._hold_models(ccs.plan_converge(env, ccs.ConvergeFlags()))
steps, _ = gp._level_steps_stage1(models)
with PlanSpy() as spy:
    ui = FakeUI(confirm=True, ask=None)
    seq, refresh = gp._run_level_apply(env, steps, "", ui)
check("Cancel on the picker ends the sequence as cancelled",
      seq["stage2"] == "cancelled" and seq["landed"] == 1
      and len(spy.runs) == 0 and ui.confirmed_with == [], seq["stage2"])
check("  the status discloses that the renames stand",
      gp._sequence_status(seq)
      == "1 rename landed, each undoable; the copy was not confirmed.",
      gp._sequence_status(seq))
check("  and the refresh ran, planning without an answer",
      refresh is not None and refresh[0] == "ok" and len(spy.plans) == 2
      and spy.plans[1][0].live == "")

# An answer given, then a close confirmed before the replan: truncated.
env, root = build_env(disagree=True)
roots.append(root)
models = gp._hold_models(ccs.plan_converge(env, ccs.ConvergeFlags()))
steps, _ = gp._level_steps_stage1(models)
with PlanSpy() as spy:
    ui = FakeUI(confirm=True, ask=A1_STORE)
    ui._on_ask = lambda: setattr(ui, "truncate", True)
    seq, refresh = gp._run_level_apply(env, steps, "", ui)
check("a close confirmed while the picker sits open ends as truncated",
      seq["stage2"] == "truncated" and len(spy.runs) == 0
      and len(spy.plans) == 1 and refresh is None, seq["stage2"])

# Rule (b) inside the worker: an answer whose pair the files have outgrown
# by the time stage 2 plans is dropped BEFORE the plan, never threaded into
# it - a plan-then-drop order would have turned the healed files into a
# plan-time refusal.
env, root = build_env(disagree=True)
roots.append(root)
models = gp._hold_models(ccs.plan_converge(env, ccs.ConvergeFlags()))
steps, _ = gp._level_steps_stage1(models)
_real_run_retitle_b = ccs.run_retitle


def heal_after_rename(env_, manifest):
    final = _real_run_retitle_b(env_, manifest)
    write_json(os.path.join(root, "Claude", "config.json"),
               {"lastKnownAccountUuid": A1})     # the files now agree
    return final


ccs.run_retitle = heal_after_rename
with PlanSpy() as spy:
    ui = FakeUI(confirm=True)
    seq, refresh = gp._run_level_apply(env, steps, "alice@example.com", ui,
                                       pair=(A1, A2))
ccs.run_retitle = _real_run_retitle_b
check("files that healed mid-sequence drop the answer before the stage-2 plan",
      seq.get("live_dropped") is True and spy.plans[0][0].live == ""
      and seq["stage2"] == "completed" and not seq.get("plan_problem"),
      "%s live=%r" % (seq["stage2"], spy.plans[0][0].live))
check("  and the healed files produce a plan with no banner",
      not spy.plans[0][1].get("identity_disagreement")
      and refresh is not None and refresh[0] == "ok")

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


def above(a, b):
    """A is placed above B in their common parent - by grid row where both
    are gridded, else by pack order."""
    if a.winfo_manager() == "grid" and b.winfo_manager() == "grid":
        return int(a.grid_info()["row"]) < int(b.grid_info()["row"])
    slaves = a.master.pack_slaves()
    return slaves.index(a) < slaves.index(b)


def walk(w):
    """W's descendants, depth first - the banner builder nests its line and
    buttons in a row frame."""
    out = []
    for c in w.winfo_children():
        out.append(c)
        out += walk(c)
    return out


def open_app(env, stage1=True):
    """A real SyncApp over ENV, withdrawn, stage-1 dialog scripted (a
    Toplevel needs a display pump this harness does not want mid-check)."""
    _CURRENT["env"] = env
    modals = Modals()
    gp.messagebox = modals
    tkroot = tk.Tk()
    tkroot.withdraw()
    stage1_calls = []
    stage1_notes = []

    def fake_stage1(self, steps, note=""):
        stage1_calls.append(list(steps))
        stage1_notes.append(note)
        return stage1
    gp.SyncApp._confirm_stage1 = fake_stage1
    app = gp.SyncApp(tkroot)
    app._stage1_calls = stage1_calls
    app._stage1_notes = stage1_notes

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
# D (0.15.1): two tabs, two labels - Level's button is its spec's name for
# the flow; the One-session tab keeps "Apply".
check("the Level button is labeled 'Level the sidebars'",
      app.level_apply_btn.cget("text") == "Level the sidebars",
      app.level_apply_btn.cget("text"))
check("  and the One-session tab's stays 'Apply'",
      app.apply_btn.cget("text") == "Apply", app.apply_btn.cget("text"))
check("  the detail line names the button, not 'Apply'",
      "Level the sidebars" in app.level_detail.get()
      and "press Apply" not in app.level_detail.get(),
      app.level_detail.get())
# A (0.15.1), revised by the GUI polish (Change 4): the scoreboard box sits
# above a sash and shows about seven lines; the completeness number - the
# tab's headline - lives in the detail line, where no box height can hide
# it, and a 40-line traceback scrolls inside seven lines.
shown = app.level_text.get("1.0", "end-1c").split("\n")
check("the scoreboard box shows at most seven lines (%d shown)" % len(shown),
      int(app.level_text.cget("height")) == min(7, len(shown)),
      "height=%s" % app.level_text.cget("height"))
check("  the detail line carries the scoreboard half - the headline number",
      app.level_detail.get().startswith("Level: ")
      and " short." in app.level_detail.get(), app.level_detail.get())
check("  a traceback still scrolls inside seven lines",
      (app.show_level(["x"] * 40) or True)
      and int(app.level_text.cget("height")) == 7)
app.show_level(shown)
# The a32798b geometry contract, kept: the button bar packs FIRST against
# the bottom edge - first in the pack list - and the paned middle expands.
slaves = app.level_tab.pack_slaves()
check("the button bar is bottom-pinned, first in the pack list",
      app.level_bar.pack_info()["side"] == "bottom"
      and slaves.index(app.level_bar) == 0
      and slaves.index(app.level_bar) < slaves.index(app.level_pane),
      str([str(w) for w in slaves]))
check("  and the paned middle is the one that expands",
      app.level_pane.pack_info()["expand"] in (1, True, "1")
      and app.holds_wrap.pack_info()["expand"] in (1, True, "1"))
check("  the scoreboard and the holds sit in the two panes of a sash",
      app.level_pane.panes() and len(app.level_pane.panes()) == 2
      and str(app.level_body.master) == app.level_pane.panes()[0]
      and str(app.holds_wrap.master) == app.level_pane.panes()[1],
      str(app.level_pane.panes()))
check("the holds heading is a fixed header outside the scrolled frame",
      app.holds_head.master is not app.hold_frame
      and bool(app.holds_head.winfo_manager())
      and str(app.holds_head.master) == app.level_pane.panes()[1]
      and app.holds_head.cget("textvariable") == str(app.holds_heading),
      str(app.holds_head.master))
# B (0.15.1): the section label counts what is ticked NOW, from the models.
check("the section label says 1 of 1 ticked over a prefilled row",
      app.holds_heading.get().startswith("Naming decisions - 1 of 1 ticked"),
      app.holds_heading.get())
app.hold_models[0]["_tick_var"].set(False)
settle()
check("  and follows the tick live - none ticked, no 'each ticked row'",
      app.holds_heading.get().startswith("Naming decisions - none ticked")
      and "each ticked row" not in app.holds_heading.get(),
      app.holds_heading.get())
app.hold_models[0]["_tick_var"].set(True)
settle()
check("the header names the hold as suggested",
      app.level_status.get() == "Nothing to copy - 1 held: 1 suggested.",
      app.level_status.get())
# E part 1 (0.15.1), as the GUI polish (Change 3) reshaped it: the
# One-session tab's role line says what the tab is for, above the status
# line, and the newer-only advice sits next to the box it describes.
role = app.sync_role.cget("text")
check("the One-session tab carries its role line",
      "Level is the routine" in role and "one session" in role, role[:80])
check("  placed above the status line",
      bool(app.sync_role.winfo_manager())
      and above(app.sync_role, app.sync_status_label))
check("  the newer-only advice sits in the refresh group",
      "only where mine is newer" in app.refresh_hint.cget("text")
      and app.refresh_hint.master is app.newer_chk.master)
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
n_rows = len((app.manifest or {}).get("rows") or [])
if n_rows >= 2:
    # The single-session gate (GUI polish, Change 3): a bulk plan needs
    # the consent tick for exactly these rows before Apply is live.
    check("  a fresh bulk plan keeps Apply down until consent is ticked",
          str(app.apply_btn.cget("state")) == "disabled",
          str(app.apply_btn.cget("state")))
    app.consent_var.set(True)
    app._on_consent_toggle()
check("  and its own Refresh re-arms it against a fresh plan (%d rows)"
      % n_rows,
      str(app.apply_btn.cget("state")) == "normal" or n_rows == 0,
      str(app.apply_btn.cget("state")))
# A stale-generation callback must still pay back its busy(True): the
# counted busy state deadlocks the whole window otherwise.
app.busy(True)
app._level_plan_done(-1, None, None, [], [], ("refusal", "stale"))
check("a superseded worker callback still releases the busy counter",
      str(app.level_refresh_btn.cget("state")) == "normal")
tkroot.destroy()

# ------------ F (0.15.1). the window names an unmeasured leg end to end
env, root = build_env(recency_disagree=True)
roots.append(root)
app, tkroot, modals, settle = open_app(env)
settle()
check("two unmeasured legs render as two editable rows",
      len(app.hold_models) == 2 and len(app._hold_widgets) == 4
      and all(m["editable"] and not m["_tick_var"].get()
              for m in app.hold_models),
      "%d models, %d widgets" % (len(app.hold_models),
                                 len(app._hold_widgets)))
check("  the header counts them as needing a name",
      app.level_status.get() == "Nothing to copy - 2 held: 2 need a name.",
      app.level_status.get())
check("  the section label claims nothing ticked",
      app.holds_heading.get().startswith("Naming decisions - none ticked"),
      app.holds_heading.get())
check("  the button is enabled - there are rows a human can tick",
      str(app.level_apply_btn.cget("state")) == "normal")
app.on_level_apply()
settle()
check("pressing it with nothing ticked runs nothing and says so",
      len(app._stage1_calls) == 0
      and app.level_status.get().startswith("Nothing is ticked"),
      app.level_status.get())
leg = app.hold_models[0]
leg["_tick_var"].set(True)
leg["_entry_var"].set("ACME-REVIEW session - the other leg")
settle()
check("  ticking one updates the label live",
      app.holds_heading.get().startswith("Naming decisions - 1 of 2 ticked"),
      app.holds_heading.get())
app.on_level_apply()
settle()
check("the stage-1 dialog saw one rename aimed at that row's own leg",
      len(app._stage1_calls) == 1 and len(app._stage1_calls[0]) == 1
      and app._stage1_calls[0][0]["target_sid"] == leg["target_sid"],
      str(app._stage1_calls))
check("  the copy ran and the sidebars are level",
      app.level_status.get()
      == "Applied (1 rename + 1 converge) - Level: 2 / 2 - 0 short.",
      app.level_status.get())
check("  the post-apply pane shows no holds", app.hold_models == [])
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
check("  the buttons carry the one wording - 'Desktop app is signed in as'",
      all(b.cget("text").startswith("Desktop app is signed in as")
          for b in app._banner_widgets),
      str([b.cget("text")[:40] for b in app._banner_widgets]))
app._banner_widgets[0].invoke()
settle()
check("choosing an account replans with the assertion held - in the ONE "
      "variable, live_choice, bound to its pair",
      app.live_choice and app.level_manifest is not None
      and app._live_pair == (A1, A2) and not hasattr(app, "level_live"),
      str((app.live_choice, app._live_pair)))
check("  the banner now shows the in-force line instead of the pickers",
      len(app._banner_widgets) == 1)
check("  and the typed entry text and tick survived that replan",
      app.hold_models and app.hold_models[0]["_entry_var"].get() == mine
      and app.hold_models[0]["_tick_var"].get() is False)
# Part B item 14: the same answer reaches the sync pane's next plan.
sync_flags = []
_real_plan_sync = ccs.plan_sync


def spy_plan_sync(env_, flags):
    sync_flags.append(flags)
    return _real_plan_sync(env_, flags)


ccs.plan_sync = spy_plan_sync
app.nb.select(app.sync_tab)
settle()
ccs.plan_sync = _real_plan_sync
check("  the same answer reaches the sync pane's next plan",
      sync_flags and sync_flags[-1].live == app.live_choice,
      repr([f.live for f in sync_flags]))
check("  and the One-session tab shows the same in-force line and button",
      any(isinstance(w, gp.ttk.Button)
          and w.cget("text") == "Change signed-in account"
          for w in walk(app.sync_banner))
      and any(isinstance(w, gp.ttk.Label)
              and "under your assertion" in str(w.cget("text"))
              for w in walk(app.sync_banner)))
tkroot.destroy()

# ---------------- 20b, window-level: a refusal keeps the answer in force
env, root = build_env(disagree=True)
roots.append(root)
env.process_lister = lambda: DESKTOP          # RULING 4 trips at rename 1
app, tkroot, modals, settle = open_app(env)
settle()
app._banner_widgets[0].invoke()
settle()
with PlanSpy() as spy:
    app.on_level_apply()
    settle()
check("a stage-1 refusal leaves the answer held",
      app.live_choice != "" and modals.of("showwarning", "Nothing was renamed"),
      repr(app.live_choice))
check("  the post-refusal replan carried it",
      spy.plans and spy.plans[-1][0].live == app.live_choice)
check("  and the banner shows the in-force line, not the pickers",
      len(app._banner_widgets) == 1
      and app._banner_widgets[0].cget("text") == "Change signed-in account")
env.process_lister = lambda: []
tkroot.destroy()

# ------------- 13, window-level: the stage-1 line, the picker, its failure
env, root = build_env(disagree=True)
roots.append(root)
app, tkroot, modals, settle = open_app(env)
settle()
asked = []


def scripted_picker(self, fresh):
    asked.append(fresh)
    path = os.path.join(env.store_candidates[0], A1, O1)
    dis = fresh["identity_disagreement"]
    self._apply_live("answered", {"path": path,
                                  "pair": (dis["oauth"], dis["config"]),
                                  "label": "alice@example.com"})
    return path


gp.SyncApp._ask_live_dialog = scripted_picker
with PlanSpy() as spy:
    app.on_level_apply()
    settle()
check("the stage-1 dialog says the copy step will ask",
      app._stage1_notes and "the copy step will ask"
      in app._stage1_notes[0],
      repr(app._stage1_notes[:1]))
check("  the picker fired once, after the renames, and the converge ran",
      len(asked) == 1 and len(spy.runs) == 1
      and app.level_status.get().startswith("Applied (1 rename + 1 converge)"),
      app.level_status.get())
check("  the stage-2 dialog carried the assertion line",
      any("under your assertion: the desktop app is on" in c[2]
          for c in modals.of("askokcancel", "Create the rows?")),
      str(modals.of("askokcancel")))
check("  after the completed write the answer is spent and the banner is "
      "back", app.live_choice == "" and len(app._banner_widgets) == 2)
tkroot.destroy()

# The picker raising (fixture-side) reads as Cancel: the sequence ends
# cancelled, the busy count is released, no control is stranded.
env, root = build_env(disagree=True)
roots.append(root)
app, tkroot, modals, settle = open_app(env)
settle()


def broken_picker(self, fresh):
    raise RuntimeError("simulated Tk failure in the picker")


gp.SyncApp._ask_live_dialog = broken_picker
with PlanSpy() as spy:
    app.on_level_apply()
    settle()
del gp.SyncApp._ask_live_dialog       # the real one again
check("a picker that raises reads as Cancel",
      app.level_status.get()
      == "1 rename landed, each undoable; the copy was not confirmed."
      and len(spy.runs) == 0, app.level_status.get())
check("  and the busy count was released - nothing stranded",
      app._busy_count == 0
      and str(app.level_refresh_btn.cget("state")) == "normal")
tkroot.destroy()

# ------------------ 14 (GUI polish). rule (b) and its order, window-level
env, root = build_env(disagree=True)
roots.append(root)
app, tkroot, modals, settle = open_app(env)
settle()
CONFIG = os.path.join(root, "Claude", "config.json")
OAUTH = os.path.join(env.home, ".claude.json")


def set_identity(oauth, config):
    write_json(OAUTH, {"oauthAccount": {"accountUuid": oauth,
                                        "organizationUuid": O1,
                                        "emailAddress": "alice@example.com"}})
    write_json(CONFIG, {"lastKnownAccountUuid": config})


def answer():
    app._banner_widgets[0].invoke()
    settle()
    return app.live_choice


check("an answer is held", bool(answer()) and app._live_pair == (A1, A2))
set_identity(A1, A1)                  # the files now agree
with PlanSpy() as spy:
    app.refresh_level()
    settle()
check("files that agree drop the answer before the next plan",
      app.live_choice == "" and spy.plans[-1][0].live == "",
      repr(spy.plans[-1][0].live))
check("  the in-force line is gone and nothing refused",
      app._banner_widgets == [] and app.level_status.get() != "Refused",
      app.level_status.get())
set_identity(A1, A2)                  # the disagreement is back
app.refresh_level()
settle()
answer()
A3 = "eeeeeeee-0000-0000-0000-000000000005"
set_identity(A1, A3)                  # a different pair
with PlanSpy() as spy:
    app.refresh_level()
    settle()
check("a changed pair drops it too", app.live_choice == ""
      and spy.plans[-1][0].live == "")
set_identity(A1, A2)
app.refresh_level()
settle()
answer()
set_identity(A2, A1)                  # the same accounts, roles swapped
with PlanSpy() as spy:
    app.refresh_level()
    settle()
check("the roles swapped drops it", app.live_choice == ""
      and spy.plans[-1][0].live == "")
# A press after the files changed drops the answer and replans instead of
# running: no run_retitle, no stage-1 dialog.
set_identity(A1, A2)
app.refresh_level()
settle()
answer()
set_identity(A1, A1)
before = len(app._stage1_calls)
retitles = []
_real_run_retitle_c = ccs.run_retitle
ccs.run_retitle = lambda e, m: retitles.append(m) or _real_run_retitle_c(e, m)
with PlanSpy() as spy:
    app.on_level_apply()
    settle()
ccs.run_retitle = _real_run_retitle_c
check("a Level press after the files changed replans instead of running",
      len(app._stage1_calls) == before and retitles == []
      and len(spy.plans) == 1 and app.live_choice == "",
      "stage1=%d retitles=%d plans=%d" % (len(app._stage1_calls) - before,
                                          len(retitles), len(spy.plans)))
# And the sync Apply, the same way.
set_identity(A1, A2)
app.refresh_level()
settle()
answer()
app.nb.select(app.sync_tab)
settle()
syncs = []
_real_run_sync = ccs.run_sync
ccs.run_sync = lambda e, m: syncs.append(m) or "completed"
plans_before = len(sync_flags)
sync_plans = []
_real_plan_sync2 = ccs.plan_sync
ccs.plan_sync = lambda e, f: sync_plans.append(f) or _real_plan_sync2(e, f)
set_identity(A1, A1)
app.manifest = {"rows": [{"name": "local_x.json", "session_id": "x",
                          "is_update": False}], "tally": {},
                "dest_account": A2, "dest_email": "bob@example.com"}
app.on_apply()
settle()
ccs.run_sync = _real_run_sync
ccs.plan_sync = _real_plan_sync2
check("a sync Apply after the files changed replans instead of running",
      syncs == [] and len(sync_plans) == 1 and app.live_choice == "",
      "syncs=%d plans=%d" % (len(syncs), len(sync_plans)))
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
