import json
import os
import stat

import pytest

import claude_threads as ct


def test_live_account_from_oauth(two_account_env, tmp_path):
    env, src, dst = two_account_env(tmp_path)
    acct = ct.live_account(env)
    assert acct.account_uuid.startswith("aaaaaaaa")
    assert acct.org_uuid.startswith("bbbbbbbb")
    assert acct.email == "me@example.com"
    assert os.path.normcase(acct.path) == os.path.normcase(src)


def test_resolve_endpoints_picks_the_single_other_store(two_account_env, tmp_path):
    env, src, dst = two_account_env(tmp_path)
    source, dest = ct.resolve_sync_endpoints(env)
    assert os.path.normcase(source.path) == os.path.normcase(src)
    assert os.path.normcase(dest.path) == os.path.normcase(dst)


def test_refuses_when_account_unidentifiable(two_account_env, tmp_path):
    env, src, dst = two_account_env(tmp_path)
    os.unlink(os.path.join(env.home, ".claude.json"))
    with pytest.raises(ct.Refusal, match="--to"):
        ct.resolve_sync_endpoints(env)


def test_refuses_when_no_other_store(two_account_env, tmp_path):
    env, src, dst = two_account_env(tmp_path)
    import shutil
    shutil.rmtree(os.path.dirname(dst))
    with pytest.raises(ct.Refusal, match="no other account"):
        ct.resolve_sync_endpoints(env)


def test_refuses_on_multiple_candidates_without_to(two_account_env, tmp_path):
    env, src, dst = two_account_env(tmp_path)
    os.makedirs(os.path.join(env.store_candidates[0],
                             "eeeeeeee-0000-0000-0000-000000000005",
                             "ffffffff-0000-0000-0000-000000000006"))
    with pytest.raises(ct.Refusal, match="--to"):
        ct.resolve_sync_endpoints(env)
    picked = ct.resolve_sync_endpoints(env, to="cccccccc")[1]
    assert os.path.normcase(picked.path) == os.path.normcase(dst)


def test_never_uses_freshness_to_choose(two_account_env, tmp_path):
    """Freshness must not be load-bearing: with the account unidentifiable it
    refuses even though one store is obviously more recently written."""
    env, src, dst = two_account_env(tmp_path)
    os.unlink(os.path.join(env.home, ".claude.json"))
    with open(os.path.join(dst, "local_x.json"), "w") as fh:
        fh.write("{}")
    with pytest.raises(ct.Refusal):
        ct.resolve_sync_endpoints(env)


def test_live_account_prefers_named_org_over_first_alphabetical(two_account_env, tmp_path):
    """oauthAccount's organizationUuid must be honored, not just accountUuid -
    with two org dirs under the live account, the alphabetically-first one
    ("bbbbbbbb...", from the fixture) must lose to the one actually named."""
    env, src, dst = two_account_env(tmp_path)
    second_org = os.path.join(env.store_candidates[0],
                              "aaaaaaaa-0000-0000-0000-000000000001",
                              "zzzzzzzz-0000-0000-0000-000000000009")
    os.makedirs(second_org)
    with open(os.path.join(env.home, ".claude.json"), "w", encoding="utf-8") as fh:
        json.dump({"oauthAccount": {
            "accountUuid": "aaaaaaaa-0000-0000-0000-000000000001",
            "organizationUuid": "zzzzzzzz-0000-0000-0000-000000000009",
            "emailAddress": "me@example.com"}}, fh)
    acct = ct.live_account(env)
    assert acct.org_uuid.startswith("zzzzzzzz")
    assert os.path.normcase(acct.path) == os.path.normcase(second_org)


def test_live_account_falls_back_when_named_org_dir_missing(two_account_env, tmp_path):
    """organizationUuid can name an org whose dir hasn't been created on disk
    yet; live_account must still resolve via accountUuid alone rather than
    failing to match anything."""
    env, src, dst = two_account_env(tmp_path)
    with open(os.path.join(env.home, ".claude.json"), "w", encoding="utf-8") as fh:
        json.dump({"oauthAccount": {
            "accountUuid": "aaaaaaaa-0000-0000-0000-000000000001",
            "organizationUuid": "99999999-0000-0000-0000-000000000099",
            "emailAddress": "me@example.com"}}, fh)
    acct = ct.live_account(env)
    assert acct.org_uuid.startswith("bbbbbbbb")
    assert os.path.normcase(acct.path) == os.path.normcase(src)


def test_config_fallback_refuses_when_matched_account_has_two_orgs(two_account_env, tmp_path):
    """lastKnownAccountUuid names only the account half. With two org dirs
    under that account there is no evidence which is live, so this must
    refuse (fail closed) rather than pick the alphabetically-first org - and
    the refusal must point at the real fix (sign in so ~/.claude.json names
    the account)."""
    env, src, dst = two_account_env(tmp_path)
    os.unlink(os.path.join(env.home, ".claude.json"))
    second_org = os.path.join(env.store_candidates[0],
                              "aaaaaaaa-0000-0000-0000-000000000001",
                              "zzzzzzzz-0000-0000-0000-000000000009")
    os.makedirs(second_org)
    cfg_path = os.path.join(os.path.dirname(env.store_candidates[0]), "config.json")
    with open(cfg_path, "w", encoding="utf-8") as fh:
        json.dump({"lastKnownAccountUuid": "aaaaaaaa-0000-0000-0000-000000000001"}, fh)
    assert ct.live_account(env) is None
    with pytest.raises(ct.Refusal, match="claude.json"):
        ct.resolve_sync_endpoints(env)


def test_to_disambiguates_two_orgs_under_one_dormant_account(two_account_env, tmp_path):
    """--to must be able to name a specific org, not just an account - two
    org dirs under the same dormant (non-live) account must be tellable
    apart by org uuid."""
    env, src, dst = two_account_env(tmp_path)
    second_dst_org = os.path.join(env.store_candidates[0],
                                  "cccccccc-0000-0000-0000-000000000003",
                                  "eeeeeeee-0000-0000-0000-000000000007")
    os.makedirs(second_dst_org)
    source, dest = ct.resolve_sync_endpoints(env, to="eeeeeeee")
    assert os.path.normcase(source.path) == os.path.normcase(src)
    assert os.path.normcase(dest.path) == os.path.normcase(second_dst_org)


def test_to_refuses_cleanly_when_nothing_matches(two_account_env, tmp_path):
    env, src, dst = two_account_env(tmp_path)
    with pytest.raises(ct.Refusal) as exc_info:
        ct.resolve_sync_endpoints(env, to="no-such-account")
    assert "be more specific" not in str(exc_info.value)


def test_layout_error_on_store_discovery_failure(two_account_env, tmp_path, monkeypatch):
    env, src, dst = two_account_env(tmp_path)
    root = env.store_candidates[0]
    real_listdir = os.listdir

    def boom(p):
        if os.path.normcase(str(p)) == os.path.normcase(root):
            raise PermissionError("no")
        return real_listdir(p)

    monkeypatch.setattr(os, "listdir", boom)
    with pytest.raises(ct.LayoutError) as exc_info:
        ct.resolve_sync_endpoints(env)
    assert exc_info.value.exit_code == 2


def _row(store, name, sid, title, extra=None):
    d = {"sessionId": name[:-5], "cliSessionId": sid, "cwd": "C:\\p",
         "title": title, "lastActivityAt": 1}
    d.update(extra or {})
    with open(os.path.join(store, name), "w", encoding="utf-8") as fh:
        json.dump(d, fh)
    return d


def _transcript(env, sid):
    folder = os.path.join(env.projects_root, "C--p")
    os.makedirs(folder, exist_ok=True)
    p = os.path.join(folder, sid + ".jsonl")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write('{"cwd":"C:\\\\p"}\n')
    return p


def test_selects_only_missing_rows_with_live_transcripts(two_account_env, tmp_path):
    env, src, dst = two_account_env(tmp_path)
    _row(src, "local_a.json", "sid-a", "Alpha"); _transcript(env, "sid-a")
    _row(src, "local_b.json", "sid-b", "Bravo"); _transcript(env, "sid-b")
    _row(dst, "local_b.json", "sid-b", "Bravo")          # already there
    _row(src, "local_c.json", "sid-c", "Charlie")         # transcript missing
    source, dest = ct.resolve_sync_endpoints(env)
    picked, tally = ct.select_sync_rows(env, source, dest, ct.SyncFlags())
    assert [p["title"] for p in picked] == ["Alpha"]
    assert tally["present"] == ["Bravo"]
    assert tally["no_transcript"] == ["Charlie"]


def test_skips_sessions_the_destination_deleted(two_account_env, tmp_path):
    """E4: the app writes tombstones but does not honour them, so copying a
    row for a tombstoned session resurrects a thread the user deleted."""
    env, src, dst = two_account_env(tmp_path)
    _row(src, "local_a.json", "sid-a", "Alpha"); _transcript(env, "sid-a")
    with open(os.path.join(dst, "deleted_sid-a"), "w") as fh:
        fh.write("1785541024931")
    source, dest = ct.resolve_sync_endpoints(env)
    picked, tally = ct.select_sync_rows(env, source, dest, ct.SyncFlags())
    assert picked == []
    assert tally["deleted"] == ["Alpha"]


def test_include_deleted_overrides_only_for_named_sessions(two_account_env, tmp_path):
    env, src, dst = two_account_env(tmp_path)
    _row(src, "local_a.json", "sid-a", "Alpha"); _transcript(env, "sid-a")
    _row(src, "local_b.json", "sid-b", "Bravo"); _transcript(env, "sid-b")
    for sid in ("sid-a", "sid-b"):
        with open(os.path.join(dst, "deleted_" + sid), "w") as fh:
            fh.write("1")
    source, dest = ct.resolve_sync_endpoints(env)
    picked, tally = ct.select_sync_rows(env, source, dest,
                                        ct.SyncFlags(include_deleted=("Alpha",)))
    assert [p["title"] for p in picked] == ["Alpha"]      # named one only
    assert tally["deleted"] == ["Bravo"]


def test_only_filter(two_account_env, tmp_path):
    env, src, dst = two_account_env(tmp_path)
    _row(src, "local_a.json", "sid-a", "Alpha"); _transcript(env, "sid-a")
    _row(src, "local_b.json", "sid-b", "Bravo"); _transcript(env, "sid-b")
    source, dest = ct.resolve_sync_endpoints(env)
    picked, tally = ct.select_sync_rows(env, source, dest, ct.SyncFlags(only="alph"))
    assert [p["title"] for p in picked] == ["Alpha"]
    assert tally["filtered"] == ["Bravo"]


def test_non_row_files_are_never_candidates(two_account_env, tmp_path):
    env, src, dst = two_account_env(tmp_path)
    for junk in ("scheduled-tasks.json", "deleted_sid-z", "local_q.json.tmp"):
        with open(os.path.join(src, junk), "w") as fh:
            fh.write("{}")
    source, dest = ct.resolve_sync_endpoints(env)
    picked, tally = ct.select_sync_rows(env, source, dest, ct.SyncFlags())
    assert picked == []


def test_unreadable_row_is_reported_not_copied(two_account_env, tmp_path):
    env, src, dst = two_account_env(tmp_path)
    with open(os.path.join(src, "local_bad.json"), "w") as fh:
        fh.write("{not json")
    source, dest = ct.resolve_sync_endpoints(env)
    picked, tally = ct.select_sync_rows(env, source, dest, ct.SyncFlags())
    assert picked == []
    assert tally["unreadable"] == ["local_bad.json"]


def test_transform_strips_connector_fields_and_shrinks_row():
    data = {"sessionId": "local_x", "cliSessionId": "sid", "cwd": "C:\\p",
            "title": "T", "lastActivityAt": 5,
            "remoteMcpServersConfig": [{"name": "Canva", "tools": ["x" * 5000]}],
            "enabledMcpTools": ["a"] * 200,
            "bridgeSessionIds": ["b"], "scheduledTaskId": "task-1"}
    before = len(json.dumps(data, separators=(",", ":")).encode("utf-8"))
    blob, removed, reset = ct.transform_row(data)
    out = json.loads(blob)
    for k in ("remoteMcpServersConfig", "enabledMcpTools", "bridgeSessionIds",
              "scheduledTaskId"):
        assert k not in out
    assert set(removed) == {"remoteMcpServersConfig", "enabledMcpTools",
                            "bridgeSessionIds", "scheduledTaskId"}
    assert len(blob) < before / 10          # E5 measured 99.5% on a real row


def test_transform_keeps_identity_fields():
    data = {"sessionId": "local_x", "cliSessionId": "sid", "cwd": "C:\\p",
            "originCwd": "C:\\p", "title": "T", "titleSource": "auto",
            "lastActivityAt": 5, "createdAt": 1, "model": "m", "effort": "high",
            "isArchived": False, "forkedFromSessionId": "other"}
    blob, removed, reset = ct.transform_row(data)
    out = json.loads(blob)
    for k in data:
        assert out[k] == data[k], k
    assert removed == [] and reset == []


def test_transform_resets_permission_fields():
    data = {"cliSessionId": "sid", "title": "T",
            "alwaysAllowedReasons": ["because"],
            "sessionPermissionUpdates": [{"grant": "all"}],
            "chromePermissionMode": "always", "chromeTabGroupId": 7}
    blob, removed, reset = ct.transform_row(data)
    out = json.loads(blob)
    assert out["alwaysAllowedReasons"] == []
    assert out["sessionPermissionUpdates"] == []
    assert out["chromePermissionMode"] is None and out["chromeTabGroupId"] is None
    assert set(reset) == {"alwaysAllowedReasons", "sessionPermissionUpdates",
                          "chromePermissionMode", "chromeTabGroupId"}


def test_verbatim_disables_the_transform():
    data = {"cliSessionId": "sid", "title": "T",
            "remoteMcpServersConfig": [{"name": "Canva"}],
            "alwaysAllowedReasons": ["because"]}
    blob, removed, reset = ct.transform_row(data, verbatim=True)
    assert json.loads(blob) == data
    assert removed == [] and reset == []


def test_transform_does_not_mutate_its_input():
    data = {"cliSessionId": "sid", "remoteMcpServersConfig": [{"name": "Canva"}]}
    ct.transform_row(data)
    assert "remoteMcpServersConfig" in data       # caller's dict untouched


class SimulatedCrash(Exception):
    pass


def _prepared(env, src, dst, n=2):
    for i in range(n):
        sid = "sid-%d" % i
        _row(src, "local_%d.json" % i, sid, "Thread %d" % i)
        _transcript(env, sid)


def test_run_sync_writes_rows_and_completes(two_account_env, tmp_path):
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst)
    m = ct.plan_sync(env, ct.SyncFlags())
    assert len(m["rows"]) == 2
    assert ct.run_sync(env, m) == "completed"
    assert sorted(f for f in os.listdir(dst) if f.startswith("local_")) == [
        "local_0.json", "local_1.json"]
    op = ct.list_ops(env)[-1]
    assert op.manifest["status"] == "completed"
    assert op.manifest["op_type"] == "sync"
    assert ct.read_lock(env) is None


def test_written_rows_are_transformed(two_account_env, tmp_path):
    env, src, dst = two_account_env(tmp_path)
    _row(src, "local_a.json", "sid-a", "Alpha",
         {"remoteMcpServersConfig": [{"name": "Canva"}]})
    _transcript(env, "sid-a")
    ct.run_sync(env, ct.plan_sync(env, ct.SyncFlags()))
    with open(os.path.join(dst, "local_a.json"), encoding="utf-8") as fh:
        assert "remoteMcpServersConfig" not in json.load(fh)


def test_dry_run_plan_writes_nothing(two_account_env, tmp_path):
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst)
    ct.plan_sync(env, ct.SyncFlags())          # planning alone must not write
    assert [f for f in os.listdir(dst) if f.startswith("local_")] == []
    assert ct.list_ops(env) == []


def test_crash_mid_write_leaves_a_recoverable_op(two_account_env, tmp_path):
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=3)
    m = ct.plan_sync(env, ct.SyncFlags())

    def hook(point):
        if point == "sync-mid-write":
            raise SimulatedCrash()
    ct._crash_hook = hook
    try:
        with pytest.raises(SimulatedCrash):
            ct.run_sync(env, m)
    finally:
        ct._crash_hook = None
    op = ct.nonterminal_ops(env)[0]
    assert op.manifest["status"] == "writing"
    assert any(r.get("written") for r in op.manifest["rows"])
    assert not all(r.get("written") for r in op.manifest["rows"])
    assert ct.read_lock(env) is None            # released by the finally


def test_refuses_to_write_the_live_store(two_account_env, tmp_path):
    """The no-process-guard design rests on only ever writing the dormant
    store, so a destination that resolves to the live account is fatal."""
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=1)
    m = ct.plan_sync(env, ct.SyncFlags())
    m["dest_path"] = src                        # pretend the account switched
    with pytest.raises(ct.Refusal, match="live"):
        ct.run_sync(env, m)
    # The refusal must land before the row loop ever starts: nothing written,
    # and the op never advances past the state new_op left it in.
    assert [f for f in os.listdir(dst) if f.startswith("local_")] == []
    op = ct.list_ops(env)[-1]
    assert op.manifest["status"] == "journaled"


def test_second_instance_is_locked_out(two_account_env, tmp_path):
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=1)
    m = ct.plan_sync(env, ct.SyncFlags())
    ct.acquire_lock(env, "other-op")
    try:
        with pytest.raises(ct.Refusal, match="lock"):
            ct.run_sync(env, m)
    finally:
        ct.release_lock(env)


def test_row_dest_path_outside_destination_root_is_refused(two_account_env, tmp_path):
    """The top-level dest_path guard (LIVE-account check, isdir check) only
    means something if each row's own dest_path is independently verified to
    sit inside it - a manifest whose row was hand-edited to point elsewhere
    must be refused before anything is written, never silently followed."""
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=1)
    m = ct.plan_sync(env, ct.SyncFlags())
    outside = os.path.join(str(tmp_path), "elsewhere.json")
    m["rows"][0]["dest_path"] = outside
    with pytest.raises(ct.LayoutError) as exc_info:
        ct.run_sync(env, m)
    assert exc_info.value.exit_code == 2
    assert not os.path.exists(outside)
    assert [f for f in os.listdir(dst) if f.startswith("local_")] == []


def test_crash_before_manifest_save_is_forward_pass_safe(two_account_env, tmp_path):
    """The write happens before the manifest records it, so a crash in that
    exact window leaves the destination file on disk with written still
    False. Resuming this op (a real recovery is Task 5's job; simulated here
    by re-entering execute_sync_op directly, mirroring what a forward pass
    will do) must recognize the already-correct bytes and neither duplicate
    the write nor refuse - the byte-identical branch doing its job."""
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=2)
    m = ct.plan_sync(env, ct.SyncFlags())

    def hook(point):
        if point == "sync-write-before-save":
            raise SimulatedCrash()
    ct._crash_hook = hook
    try:
        with pytest.raises(SimulatedCrash):
            ct.run_sync(env, m)
    finally:
        ct._crash_hook = None

    op = ct.nonterminal_ops(env)[0]
    assert op.manifest["status"] == "writing"
    row0 = op.manifest["rows"][0]
    assert row0["written"] is False
    with open(row0["dest_path"], "rb") as fh:
        on_disk = fh.read()
    assert on_disk == ct.unb64(row0["post_b64"])

    # A byte-for-byte comparison alone would also pass if the resume simply
    # rewrote identical bytes - it would prove content but not prove the
    # no-rewrite branch was actually taken. atomic_write goes through
    # os.replace, which changes the file's identity (confirmed empirically
    # on this platform: st_ino and st_ctime both change across a real
    # replace of the same bytes) - record that identity before the forward
    # pass so an accidental unconditional rewrite is caught even though its
    # bytes would look identical.
    before_stat = os.stat(row0["dest_path"])

    # Simulate a forward pass resuming this exact op.
    op.manifest["status"] = "journaled"
    ct.save_manifest(op)
    assert ct.execute_sync_op(env, op) == "completed"
    assert op.manifest["rows"][0]["written"] is True
    after_stat = os.stat(row0["dest_path"])
    assert after_stat.st_ino == before_stat.st_ino     # no os.replace happened
    assert after_stat.st_ctime == before_stat.st_ctime  # backstop signal
    with open(row0["dest_path"], "rb") as fh:
        assert fh.read() == on_disk            # not duplicated or corrupted


def test_row_dest_path_equal_to_destination_root_is_refused(two_account_env, tmp_path):
    """ensure_contained alone treats the destination root as 'contained' in
    itself (real == rreal) - a row dest_path hand-edited to equal
    m["dest_path"] must still be refused, since atomic_write's
    <path>.ct-tmp scratch file would otherwise land one level OUTSIDE the
    verified root (a sibling of the destination directory, in its parent)
    before the write even fails."""
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=1)
    m = ct.plan_sync(env, ct.SyncFlags())
    m["rows"][0]["dest_path"] = m["dest_path"]        # the store root itself
    stray_scratch = m["dest_path"] + ".ct-tmp"
    with pytest.raises(ct.LayoutError):
        ct.run_sync(env, m)
    assert not os.path.exists(stray_scratch)
    assert [f for f in os.listdir(dst) if f.startswith("local_")] == []


def test_destination_row_changed_since_planning_is_refused(two_account_env, tmp_path):
    """select_sync_rows only guarantees a row was ABSENT at plan time. If the
    destination account changes it before this op runs (or resumes), the
    planned post-image must never be rewritten over that change - refuse,
    leave the bytes untouched, and leave the op non-terminal at 'writing' so
    it stays recoverable instead of silently completing over the loss."""
    env, src, dst = two_account_env(tmp_path)
    _row(src, "local_a.json", "sid-a", "Alpha")
    _transcript(env, "sid-a")
    m = ct.plan_sync(env, ct.SyncFlags())
    dest_path = m["rows"][0]["dest_path"]
    with open(dest_path, "w", encoding="utf-8") as fh:
        fh.write('{"changed": true}')
    with pytest.raises(ct.Refusal, match="changed"):
        ct.run_sync(env, m)
    with open(dest_path, encoding="utf-8") as fh:
        assert fh.read() == '{"changed": true}'        # untouched
    op = ct.list_ops(env)[-1]
    assert op.manifest["status"] == "writing"


def test_atomic_write_failure_becomes_a_refusal_not_a_traceback(two_account_env, tmp_path):
    """atomic_write raising OSError (full disk, a permission change, an
    unmounted destination) must never propagate raw - main() only catches
    Refusal/LayoutError (and only those get redact()'d before being printed),
    so a bare OSError would surface as an unredacted traceback exposing
    paths and account UUIDs. Provoke a real OSError rather than a mock: a
    leftover, read-only <name>.ct-tmp scratch file at the write target
    blocks atomic_write's own open() while the row itself is still absent -
    so the pre-write existence check correctly treats it as new, and the
    failure exercised is atomic_write's, not the read-check's."""
    env, src, dst = two_account_env(tmp_path)
    _row(src, "local_a.json", "sid-a", "Alpha")
    _transcript(env, "sid-a")
    m = ct.plan_sync(env, ct.SyncFlags())
    dest_path = m["rows"][0]["dest_path"]
    scratch = dest_path + ".ct-tmp"
    with open(scratch, "wb") as fh:
        fh.write(b"leftover")
    os.chmod(scratch, stat.S_IREAD)
    try:
        with pytest.raises(ct.Refusal) as exc_info:
            ct.run_sync(env, m)
        assert "could not write" in str(exc_info.value)
        op = ct.list_ops(env)[-1]
        assert op.manifest["status"] == "writing"
    finally:
        os.chmod(scratch, stat.S_IWRITE)
        os.unlink(scratch)


def test_undo_sync_removes_exactly_the_rows_it_wrote(two_account_env, tmp_path):
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=2)
    _row(dst, "local_pre.json", "sid-pre", "Pre-existing")
    ct.run_sync(env, ct.plan_sync(env, ct.SyncFlags()))
    op = ct.list_ops(env)[-1]
    assert ct.undo_sync(env, op) == "undone"
    left = sorted(f for f in os.listdir(dst) if f.startswith("local_"))
    assert left == ["local_pre.json"]           # untouched
    assert ct.list_ops(env)[-1].manifest["status"] == "undone"


def test_undo_sync_refuses_when_a_written_row_changed(two_account_env, tmp_path):
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=1)
    ct.run_sync(env, ct.plan_sync(env, ct.SyncFlags()))
    with open(os.path.join(dst, "local_0.json"), "w", encoding="utf-8") as fh:
        fh.write('{"cliSessionId":"sid-0","title":"renamed by the app"}')
    op = ct.list_ops(env)[-1]
    with pytest.raises(ct.Refusal, match="changed"):
        ct.undo_sync(env, op)
    assert os.path.exists(os.path.join(dst, "local_0.json"))


def test_classify_sync_op_offers_forward_only(two_account_env, tmp_path):
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=3)
    m = ct.plan_sync(env, ct.SyncFlags())

    def hook(point):
        if point == "sync-mid-write":
            raise SimulatedCrash()
    ct._crash_hook = hook
    try:
        with pytest.raises(SimulatedCrash):
            ct.run_sync(env, m)
    finally:
        ct._crash_hook = None
    op = ct.nonterminal_ops(env)[0]
    c = ct.classify_op(env, op)
    assert c["status"] == "writing"
    assert c["resolutions"] == ["forward"]


def test_recover_forward_finishes_an_interrupted_sync(two_account_env, tmp_path):
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=3)
    m = ct.plan_sync(env, ct.SyncFlags())

    def hook(point):
        if point == "sync-mid-write":
            raise SimulatedCrash()
    ct._crash_hook = hook
    try:
        with pytest.raises(SimulatedCrash):
            ct.run_sync(env, m)
    finally:
        ct._crash_hook = None
    op = ct.nonterminal_ops(env)[0]
    assert ct.recover_op(env, op, "forward") == "completed"
    assert len([f for f in os.listdir(dst) if f.startswith("local_")]) == 3
    assert ct.nonterminal_ops(env) == []


def test_cmd_undo_can_select_a_sync_op(two_account_env, tmp_path, monkeypatch):
    import types
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=1)
    ct.run_sync(env, ct.plan_sync(env, ct.SyncFlags()))
    monkeypatch.setattr(ct, "default_env", lambda: env)
    ns = types.SimpleNamespace(show=False, op_id=None, apply=True, verbose=False)
    assert ct.cmd_undo(env, ns) == 0
    assert [f for f in os.listdir(dst) if f.startswith("local_")] == []


def test_cmd_recover_lists_sync_and_move_ops_without_a_traceback(
        two_account_env, tmp_path, write_transcript, write_row):
    """Regression for the review finding: classify_op's first line used to
    dereference m["source_transcript"] unconditionally, and a sync manifest
    has no such key - so classify_op(sync_op) raised a bare KeyError, which
    main() does not catch (only Refusal/LayoutError are). cmd_recover's
    listing loop calls classify_op on every pending op in turn, so that
    KeyError didn't just break the sync op's own line - it aborted the whole
    loop, taking an unrelated move op queued behind it down too. Prove both
    ops now list cleanly with the dispatch fix in place."""
    env, src, dst = two_account_env(tmp_path)

    # A non-terminal sync op, left exactly the way a real refused sync
    # leaves one on disk: journaled, then refused before any row is written
    # because the destination resolved to the live account (same scenario as
    # test_refuses_to_write_the_live_store).
    _prepared(env, src, dst, n=1)
    sync_manifest = ct.plan_sync(env, ct.SyncFlags())
    sync_manifest["dest_path"] = src                  # pretend the account switched
    with pytest.raises(ct.Refusal, match="live"):
        ct.run_sync(env, sync_manifest)

    # An unrelated non-terminal MOVE op, queued behind the sync op in
    # nonterminal_ops' creation-time order.
    move_sid = "move-sess"
    move_cwd = "C:\\proj\\src"
    target = str(tmp_path / "target")
    os.makedirs(target)
    write_transcript(env, ct.encode(move_cwd, ct.SCHEME_CURRENT), move_sid,
                     [{"cwd": move_cwd}])
    # Evidence folder + row so scheme detection resolves unambiguously
    # regardless of what pytest's own tmp_path segment happens to contain -
    # same precedent as test_recover.py's `crashed` fixture.
    os.makedirs(os.path.join(env.projects_root,
                             ct.encode("C:\\proj\\_ev", ct.SCHEME_CURRENT)))
    write_row(env, 0, "org", "acct", "local_move",
             {"sessionId": "local_move", "cliSessionId": move_sid, "cwd": move_cwd})
    write_row(env, 0, "org", "acct", "local_ev",
             {"sessionId": "local_ev", "cliSessionId": "other", "cwd": "C:\\proj\\_ev",
              "lastActivityAt": 2})
    move_manifest = ct.plan_move(env, move_sid, target, ct.MoveFlags())

    def hook(point):
        if point == "after-journaled":
            raise SimulatedCrash()
    ct._crash_hook = hook
    try:
        with pytest.raises(SimulatedCrash):
            ct.run_move(env, move_manifest)
    finally:
        ct._crash_hook = None

    pending = ct.nonterminal_ops(env)
    assert len(pending) == 2
    assert {o.manifest["op_type"] for o in pending} == {"sync", "move"}

    import types
    ns = types.SimpleNamespace(op_id=None, verbose=False)
    # Before the fix this call raised KeyError('source_transcript') straight
    # out of cmd_recover - unhandled, since main() only catches
    # Refusal/LayoutError. It must now return cleanly: 1 means "unresolved
    # ops remain", which is correct - neither op was resolved here.
    assert ct.cmd_recover(env, ns) == 1
