import json
import os

import pytest

import claude_code_sessions as ct


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
    row for a tombstoned session resurrects a session the user deleted."""
    env, src, dst = two_account_env(tmp_path)
    _row(src, "local_a.json", "sid-a", "Alpha"); _transcript(env, "sid-a")
    with open(os.path.join(dst, "deleted_sid-a"), "w") as fh:
        fh.write("1785541024931")
    source, dest = ct.resolve_sync_endpoints(env)
    picked, tally = ct.select_sync_rows(env, source, dest, ct.SyncFlags())
    assert picked == []
    assert tally["deleted"] == ["Alpha"]


def test_skips_a_tombstone_filed_under_the_rows_local_id(two_account_env, tmp_path):
    """The spec said tombstones are named `deleted_<cliSessionId>`, and E4
    measured one. That is not the whole truth: on the author's own live store
    the session 'E4 tombstone test' carries TWO tombstones - one for its
    cliSessionId (bc7333f9...) and one for its filename stem, i.e. its local
    id (747a0b6e...). A skip that only checks the session id therefore misses
    a real deletion and resurrects the session."""
    env, src, dst = two_account_env(tmp_path)
    _row(src, "local_747a0b6e.json", "bc7333f9", "E4 tombstone test")
    _transcript(env, "bc7333f9")
    # ONLY the local-id tombstone - the session-id one is absent, which is
    # the case the old `sid in tombs` test could not see.
    with open(os.path.join(dst, "deleted_747a0b6e"), "w") as fh:
        fh.write("1785541024931")
    source, dest = ct.resolve_sync_endpoints(env)
    picked, tally = ct.select_sync_rows(env, source, dest, ct.SyncFlags())
    assert picked == []
    assert tally["deleted"] == ["E4 tombstone test"]

    # ...and --include-deleted can still name it, by either id.
    picked, tally = ct.select_sync_rows(
        env, source, dest, ct.SyncFlags(include_deleted=("747a0b6e",)))
    assert [r["title"] for r in picked] == ["E4 tombstone test"]
    assert tally["resurrected"] == ["E4 tombstone test"]
    assert picked[0]["overrode_tombstone"] is True


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
        _row(src, "local_%d.json" % i, sid, "Session %d" % i)
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
    """The path-comparison check is absolute and independent of the
    running-app guard (RULING 4, _guard_mutation): a destination that
    resolves to the live account is fatal whether or not any process is
    running."""
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
    leftover <name>.ct-tmp at the write target blocks atomic_write's own
    open() while the row itself is still absent - so the pre-write existence
    check correctly treats it as new, and the failure exercised is
    atomic_write's, not the read-check's.

    The leftover is a DIRECTORY, not the read-only file this used to use.
    chmod-based blocking is not a property of the filesystem, it is a
    property of the user: root ignores the write bit, so under any root CI
    container (verified in python:3.12-slim) the open() succeeded, no refusal
    came, and the finally clause then died on the consumed scratch file. A
    directory cannot be opened for writing by anyone - IsADirectoryError on
    POSIX, PermissionError on Windows, both OSError - so the same real
    failure is provoked for every user on both platforms."""
    env, src, dst = two_account_env(tmp_path)
    _row(src, "local_a.json", "sid-a", "Alpha")
    _transcript(env, "sid-a")
    m = ct.plan_sync(env, ct.SyncFlags())
    dest_path = m["rows"][0]["dest_path"]
    scratch = dest_path + ".ct-tmp"
    os.mkdir(scratch)
    try:
        with pytest.raises(ct.Refusal) as exc_info:
            ct.run_sync(env, m)
        assert "could not write" in str(exc_info.value)
        op = ct.list_ops(env)[-1]
        assert op.manifest["status"] == "writing"
    finally:
        os.rmdir(scratch)


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


def test_classify_sync_op_offers_forward_and_back(two_account_env, tmp_path):
    """Was test_classify_sync_op_offers_forward_only, which pinned
    resolutions == ["forward"] for an undrifted interrupted sync. The
    whole-branch review's Finding 2 changes exactly that: 'back' is
    unconditionally safe for a sync and must always be offered, because
    drift is not the only way forward can be permanently blocked (see
    test_stalled_sync_after_a_write_error_can_still_go_back)."""
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
    assert c["resolutions"] == ["forward", "back"]


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


# ------------------------------------------- fix round 1 (Opus review)


def test_undo_sync_refuses_when_destination_is_the_live_account(two_account_env, tmp_path):
    """Finding 1 (Critical): every sync write re-checks live_account(env) at
    execute time (independent of, and in addition to, the running-app guard
    since RULING 4) - a stale manifest can never land a write in the account
    the app is actively using. undo deletes from that same store and must
    carry the identical guarantee, or 'sync A->B, sign into B, undo' unlinks
    listing rows out from under a live app."""
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=1)
    ct.run_sync(env, ct.plan_sync(env, ct.SyncFlags()))
    op = ct.list_ops(env)[-1]
    # sign into the destination account - it is now live
    with open(os.path.join(env.home, ".claude.json"), "w", encoding="utf-8") as fh:
        json.dump({"oauthAccount": {
            "accountUuid": "cccccccc-0000-0000-0000-000000000003",
            "organizationUuid": "dddddddd-0000-0000-0000-000000000004",
            "emailAddress": "them@example.com"}}, fh)
    with pytest.raises(ct.Refusal, match="live"):
        ct.undo_sync(env, op)
    assert os.path.exists(os.path.join(dst, "local_0.json"))     # untouched


def test_undo_sync_refuses_row_dest_path_outside_destination_root(two_account_env, tmp_path):
    """Finding 2 (Important): every other mutation in this module validates
    containment first (_validate_manifest_paths for move, ensure_contained
    plus the direct-child check for sync writes) - a hand-edited or
    corrupted manifest row must never let undo delete an arbitrary path."""
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=1)
    ct.run_sync(env, ct.plan_sync(env, ct.SyncFlags()))
    op = ct.list_ops(env)[-1]
    outside = os.path.join(str(tmp_path), "elsewhere.json")
    with open(outside, "w") as fh:
        fh.write("do not delete me")
    op.manifest["rows"][0]["dest_path"] = outside
    with pytest.raises(ct.LayoutError):
        ct.undo_sync(env, op)
    assert os.path.exists(outside)


def test_undo_sync_refuses_when_locked(two_account_env, tmp_path):
    """Finding 3 (Important): undo_sync must take the single-instance lock
    first, before any of its own checks - the same discipline
    run_undo/run_move/run_sync/recover_op all use - or two concurrent
    'undo --apply' runs can both pass the completed/drift checks and race
    their unlinks."""
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=1)
    ct.run_sync(env, ct.plan_sync(env, ct.SyncFlags()))
    op = ct.list_ops(env)[-1]
    ct.acquire_lock(env, "other-op")
    try:
        with pytest.raises(ct.Refusal, match="lock"):
            ct.undo_sync(env, op)
    finally:
        ct.release_lock(env)
    assert os.path.exists(os.path.join(dst, "local_0.json"))


def test_undo_sync_wraps_unlink_failure_as_a_refusal(two_account_env, tmp_path,
                                                     monkeypatch):
    """Finding 4 (Important): a bare OSError from os.unlink must never
    propagate raw - main() only catches Refusal/LayoutError, the exact
    traceback defect this task exists to fix, now on the delete side.

    The OSError is injected rather than provoked with permissions, which is a
    deliberate departure from the sibling write-side tests (they leave a
    read-only .ct-tmp in the way and let atomic_write's own open() fail, and
    that stays honest on both platforms). Unlink is the one that does not
    travel: POSIX checks the write bit on the containing DIRECTORY, not on the
    file, so chmod(row_path, S_IREAD) blocked nothing on Linux - the delete
    succeeded, no refusal came, and the test's own chmod-back cleanup then
    died on the missing file. Making the directory read-only instead trades
    one platform assumption for another, since it is a no-op for root and CI
    containers often are root. What is under test here is how undo_sync wraps
    an OSError, not which filesystem rule produced it."""
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=1)
    ct.run_sync(env, ct.plan_sync(env, ct.SyncFlags()))
    op = ct.list_ops(env)[-1]
    row_path = op.manifest["rows"][0]["dest_path"]
    real_unlink = os.unlink

    def boom(p, *a, **kw):
        # this row only - undo_sync unlinks the lock file too, and killing
        # that would leave the env locked for the rest of the test session.
        if os.path.normcase(str(p)) == os.path.normcase(row_path):
            raise PermissionError("no")
        return real_unlink(p, *a, **kw)

    monkeypatch.setattr(os, "unlink", boom)
    with pytest.raises(ct.Refusal) as exc_info:
        ct.undo_sync(env, op)
    assert "could not remove" in str(exc_info.value)
    assert os.path.exists(row_path)


def test_undo_sync_refuses_when_not_completed(two_account_env, tmp_path):
    """Minor 10: dedicated coverage for undo_sync's 'not completed' guard -
    previously only exercised incidentally."""
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=1)
    m = ct.plan_sync(env, ct.SyncFlags())
    op = ct.new_op(env, m)                         # status "journaled", never run
    with pytest.raises(ct.Refusal, match="journaled"):
        ct.undo_sync(env, op)


def test_undo_sync_all_or_nothing_with_two_rows_one_drifted(two_account_env, tmp_path):
    """Minor 10: the original single-row drift test doesn't prove the
    all-or-nothing claim the report made - with two written rows and only
    one drifted, undo_sync must refuse and leave BOTH files on disk, not
    quietly remove the one that didn't drift."""
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=2)
    ct.run_sync(env, ct.plan_sync(env, ct.SyncFlags()))
    op = ct.list_ops(env)[-1]
    with open(os.path.join(dst, "local_0.json"), "w", encoding="utf-8") as fh:
        fh.write('{"cliSessionId":"sid-0","title":"renamed by the app"}')
    with pytest.raises(ct.Refusal, match="changed"):
        ct.undo_sync(env, op)
    assert os.path.exists(os.path.join(dst, "local_0.json"))    # drifted - kept
    assert os.path.exists(os.path.join(dst, "local_1.json"))    # untouched too


def test_classify_sync_op_offers_back_when_a_pending_row_is_blocked(two_account_env, tmp_path):
    """Finding 5 (plan-mandated): when a destination row changes underneath
    a still-in-flight sync, forward can never complete - execute_sync_op
    refuses on that exact row every time it re-enters. classify_sync_op
    must detect this, name the blocking row, and switch from offering
    forward forever (a dead end) to offering back."""
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=2)
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
    pending_row = next(r for r in op.manifest["rows"] if not r.get("written"))
    with open(pending_row["dest_path"], "w", encoding="utf-8") as fh:
        fh.write('{"unexpected": true}')

    c = ct.classify_op(env, op)
    assert c["status"] == "writing"
    assert c["resolutions"] == ["back"]
    assert c["drifted_rows"] == [pending_row["title"]]


def test_recover_back_removes_written_rows_when_a_pending_row_is_blocked(two_account_env, tmp_path):
    """Finding 5 (plan-mandated): recover --resolve --back on a sync op
    removes exactly the rows it already wrote - never the blocking pending
    row, which this op never wrote in the first place - and leaves the op
    terminal (rolled_back) instead of stuck forever."""
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=2)
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
    written_row = next(r for r in op.manifest["rows"] if r.get("written"))
    pending_row = next(r for r in op.manifest["rows"] if not r.get("written"))
    with open(pending_row["dest_path"], "w", encoding="utf-8") as fh:
        fh.write('{"unexpected": true}')

    assert ct.recover_op(env, op, "back") == "rolled_back"
    assert not os.path.exists(written_row["dest_path"])          # removed
    with open(pending_row["dest_path"], encoding="utf-8") as fh:
        assert json.load(fh) == {"unexpected": True}              # left alone
    assert ct.list_ops(env)[-1].manifest["status"] == "rolled_back"
    assert ct.nonterminal_ops(env) == []


def test_recover_back_reverses_rows_the_journal_never_recorded(two_account_env,
                                                               tmp_path):
    """A hard kill during a batched run can leave rows on disk that the
    manifest never marked written, because `written` is journalled AFTER
    atomic_write returns.

    This used to assert the opposite: that back removed nothing and merely
    NAMED the forward-then-undo route that would clean them up. Peer review
    (RULING 8 round two) rejected that as the wrong contract - `written` is an
    intention recorded after the fact, while the file holding this op's exact
    post-image is direct evidence it wrote it, so back can just reverse them.
    It matters much more once --update exists: for an added row the old
    behaviour stranded a stray row, but for a REFRESH it silently kept an
    overwrite the user had explicitly asked to reverse, with the measured
    pre-image sitting unused in the journal."""
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=2)
    m = ct.plan_sync(env, ct.SyncFlags())
    ct.run_sync(env, m)
    op = ct.list_ops(env)[-1]
    # Simulate the lost tail: the rows are on disk, the journal never
    # recorded them. This is exactly the post-SIGKILL state.
    landed = [r["dest_path"] for r in op.manifest["rows"]]
    assert all(os.path.exists(p) for p in landed)
    for r in op.manifest["rows"]:
        r["written"] = False
    op.manifest["status"] = "writing"
    ct.save_manifest(op)

    op = ct.nonterminal_ops(env)[0]
    assert ct.recover_op(env, op, "back") == "rolled_back"
    # Reversed, not stranded, and not advertised as someone else's problem.
    assert not any(os.path.exists(p) for p in landed)
    reason = ct.list_ops(env)[-1].manifest.get("abort_reason") or ""
    assert "reversed nothing" not in reason


def test_recover_back_says_so_when_there_was_nothing_to_reverse(two_account_env,
                                                                tmp_path):
    """The genuine nothing-to-do case still says so out loud rather than
    printing a bare 'rolled_back': the rows this op wrote are gone from the
    destination, so back had nothing to take back."""
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=2)
    m = ct.plan_sync(env, ct.SyncFlags())
    ct.run_sync(env, m)
    op = ct.list_ops(env)[-1]
    for r in op.manifest["rows"]:
        os.remove(r["dest_path"])         # that account deleted them itself
        r["written"] = False
    op.manifest["status"] = "writing"
    ct.save_manifest(op)

    op = ct.nonterminal_ops(env)[0]
    assert ct.recover_op(env, op, "back") == "rolled_back"
    reason = ct.list_ops(env)[-1].manifest.get("abort_reason") or ""
    assert "reversed nothing" in reason


def test_cmd_undo_dry_run_preview_for_sync_op(two_account_env, tmp_path, capsys):
    """Minor 9: cmd_undo's dry-run line prints session_id, which a sync
    manifest does not have, so it used to print 'session None'. Newly
    reachable because this task widened the candidate filter to include
    sync - the preview must say something meaningful instead."""
    import types
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=2)
    ct.run_sync(env, ct.plan_sync(env, ct.SyncFlags()))
    ns = types.SimpleNamespace(show=False, op_id=None, apply=False, verbose=True)
    assert ct.cmd_undo(env, ns) == 0
    out = capsys.readouterr().out
    assert "None" not in out
    assert "2 row" in out


# ------------------------------------------- fix round 2 (Opus re-review)


def test_recover_back_skips_a_drifted_written_row_and_still_terminates(two_account_env, tmp_path):
    """Finding 5 (corrected): the reviewer's reproduction - a single event
    ("the dormant account got opened") can both rewrite an already-written
    row AND leave unexpected content at a still-pending row's path. Refusing
    the whole 'back' operation over the written row's drift recreates the
    exact dead end the user's ruling exists to close (recover back refuses,
    recover forward refuses, undo refuses - permanently stuck). 'back' must
    instead skip only the rows it cannot safely verify, delete the rest, and
    always reach a terminal status."""
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=3)
    m = ct.plan_sync(env, ct.SyncFlags())

    # let two rows land before crashing, so one can be left clean and one
    # deliberately drifted, alongside the still-pending third row.
    calls = []
    def hook(point):
        if point == "sync-mid-write":
            calls.append(1)
            if len(calls) == 2:
                raise SimulatedCrash()
    ct._crash_hook = hook
    try:
        with pytest.raises(SimulatedCrash):
            ct.run_sync(env, m)
    finally:
        ct._crash_hook = None

    op = ct.nonterminal_ops(env)[0]
    written_rows = [r for r in op.manifest["rows"] if r.get("written")]
    pending_row = next(r for r in op.manifest["rows"] if not r.get("written"))
    assert len(written_rows) == 2
    clean_row, drifted_row = written_rows

    with open(drifted_row["dest_path"], "w", encoding="utf-8") as fh:
        fh.write('{"cliSessionId":"changed","title":"rewritten by the app"}')
    with open(pending_row["dest_path"], "w", encoding="utf-8") as fh:
        fh.write('{"unexpected": true}')

    c = ct.classify_op(env, op)
    assert c["resolutions"] == ["back"]
    assert set(c["drifted_rows"]) == {pending_row["title"], drifted_row["title"]}

    assert ct.recover_op(env, op, "back") == "rolled_back"
    assert not os.path.exists(clean_row["dest_path"])         # removed: safe to delete
    assert os.path.exists(drifted_row["dest_path"])            # skipped: drifted, kept
    with open(drifted_row["dest_path"], encoding="utf-8") as fh:
        assert json.load(fh) == {"cliSessionId": "changed", "title": "rewritten by the app"}
    with open(pending_row["dest_path"], encoding="utf-8") as fh:
        assert json.load(fh) == {"unexpected": True}           # never touched by back
    assert ct.list_ops(env)[-1].manifest["status"] == "rolled_back"
    assert ct.nonterminal_ops(env) == []
    # the module's established "name what happened" mechanism (the same
    # abort_reason field/print path _abort already uses for move) must say
    # which row was left behind and why.
    reason = ct.list_ops(env)[-1].manifest.get("abort_reason")
    assert reason and drifted_row["title"] in reason


def test_recover_back_skips_an_unreadable_written_row(two_account_env, tmp_path):
    """Finding 5 (corrected): an unreadable written row is a second route to
    the same permanent-block dead end as a drifted one, and must be skipped
    the same way, not refused."""
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=2)
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
    written_row = next(r for r in op.manifest["rows"] if r.get("written"))
    pending_row = next(r for r in op.manifest["rows"] if not r.get("written"))
    os.unlink(written_row["dest_path"])
    os.makedirs(written_row["dest_path"])          # a directory now sits where it was
    with open(pending_row["dest_path"], "w", encoding="utf-8") as fh:
        fh.write('{"unexpected": true}')
    try:
        c = ct.classify_op(env, op)
        assert c["resolutions"] == ["back"]
        assert written_row["title"] in c["drifted_rows"]

        assert ct.recover_op(env, op, "back") == "rolled_back"
        assert os.path.isdir(written_row["dest_path"])         # left alone, not deleted
        assert ct.nonterminal_ops(env) == []
    finally:
        if os.path.isdir(written_row["dest_path"]):
            os.rmdir(written_row["dest_path"])


def test_classify_sync_op_note_distinguishes_changed_from_unreadable(two_account_env, tmp_path):
    """Minor (new, from round 1's fix): an unreadable pending row is
    correctly counted as blocking (fail-closed - 'couldn't look' is never
    'nothing there'), but the note must not claim it 'changed', since that
    is only true for a genuine byte mismatch, not a permission/I-O error."""
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=1)
    m = ct.plan_sync(env, ct.SyncFlags())
    op = ct.new_op(env, m)                          # "journaled" - row never written
    row = op.manifest["rows"][0]
    os.makedirs(row["dest_path"])                   # a directory sits where the row lands
    try:
        c = ct.classify_op(env, op)
        assert c["resolutions"] == ["back"]
        assert row["title"] in c["drifted_rows"]
        assert "could not be read" in c["note"]
        assert "changed since this sync was planned" not in c["note"]
    finally:
        os.rmdir(row["dest_path"])


def test_undo_sync_refuses_when_a_written_row_is_unreadable(two_account_env, tmp_path):
    """undo_sync keeps refusing all-or-nothing on the 'unreadable' case too,
    not just genuine byte-drift - _sync_delete_targets no longer raises
    immediately on an unreadable row (recover's back arm needs to skip it
    instead), so undo_sync must now check for it explicitly rather than
    relying on the old raise-from-within-the-gate behavior."""
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=1)
    ct.run_sync(env, ct.plan_sync(env, ct.SyncFlags()))
    op = ct.list_ops(env)[-1]
    row_path = op.manifest["rows"][0]["dest_path"]
    os.unlink(row_path)
    os.makedirs(row_path)                            # a directory now sits where it was
    try:
        with pytest.raises(ct.Refusal, match="could not be read"):
            ct.undo_sync(env, op)
        assert os.path.isdir(row_path)                # left alone
    finally:
        os.rmdir(row_path)


def test_undo_sync_refuses_a_non_sync_op(two_account_env, tmp_path):
    """Finding 10 (Minor): dedicated coverage for undo_sync's op_type
    guard - previously nothing reached it."""
    env, src, dst = two_account_env(tmp_path)
    op = ct.new_op(env, {"op_type": "move"})
    with pytest.raises(ct.Refusal, match="not a sync op"):
        ct.undo_sync(env, op)


# --------------------------------------------------------------- T6: CLI


def test_transform_removed_and_reset_lists_use_the_same_order():
    """Parked finding: removed came back in SYNC_STRIP declaration order
    while reset came back sorted() - inconsistent ordering between two
    lists the JSON manifest surfaces side by side for the same row. Both
    must use the same (sorted) convention so that output is stable."""
    data = {"cliSessionId": "sid", "title": "T",
            "scheduledTaskId": "task-1", "remoteMcpServersConfig": [1],
            "bridgeSessionIds": ["b"], "enabledMcpTools": ["a"],
            "chromeTabGroupId": 7, "alwaysAllowedReasons": ["x"]}
    blob, removed, reset = ct.transform_row(data)
    assert removed == sorted(removed)
    assert reset == sorted(reset)


def test_cli_dry_run_prints_endpoints_and_writes_nothing(two_account_env, tmp_path,
                                                         monkeypatch, capsys):
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=2)
    monkeypatch.setattr(ct, "default_env", lambda: env)
    assert ct.main(["sync"]) == 0
    out = capsys.readouterr().out
    assert "me@example.com" in out
    assert "signed in" in out
    assert "dry run" in out.lower()
    # Minor 5: the RULING 4 "--apply will refuse while Claude is running"
    # warning used to live only inside the config-resolved (weak) branch of
    # _print_sync_report, so a normal oauth-resolved dry run (this test's
    # case) never printed it even though the guard applies identically
    # either way. Every dry run must print it now.
    assert "--apply will refuse while Claude is running" in out
    assert [f for f in os.listdir(dst) if f.startswith("local_")] == []


def test_cli_apply_writes_and_reports(two_account_env, tmp_path, monkeypatch, capsys):
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=2)
    monkeypatch.setattr(ct, "default_env", lambda: env)
    assert ct.main(["sync", "--apply"]) == 0
    out = capsys.readouterr().out
    assert "copied     : 2" in out
    assert len([f for f in os.listdir(dst) if f.startswith("local_")]) == 2


def test_cli_names_tombstoned_skips_individually(two_account_env, tmp_path,
                                                 monkeypatch, capsys):
    env, src, dst = two_account_env(tmp_path)
    _row(src, "local_a.json", "sid-a", "Deleted Over There")
    _transcript(env, "sid-a")
    with open(os.path.join(dst, "deleted_sid-a"), "w") as fh:
        fh.write("1")
    monkeypatch.setattr(ct, "default_env", lambda: env)
    ct.main(["sync", "--verbose"])
    out = capsys.readouterr().out
    assert "Deleted Over There" in out
    assert "deleted in the destination" in out.lower()


def test_cli_refusal_exits_1(two_account_env, tmp_path, monkeypatch, capsys):
    env, src, dst = two_account_env(tmp_path)
    os.unlink(os.path.join(env.home, ".claude.json"))
    monkeypatch.setattr(ct, "default_env", lambda: env)
    assert ct.main(["sync"]) == 1
    assert "refused" in capsys.readouterr().err.lower()


def test_cli_json_output(two_account_env, tmp_path, monkeypatch, capsys):
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=1)
    monkeypatch.setattr(ct, "default_env", lambda: env)
    assert ct.main(["sync", "--json"]) == 0
    rep = json.loads(capsys.readouterr().out)
    # Minor 4: the fixture's destination account is always email-less (its
    # email lives nowhere on disk - resolve_sync_endpoints hardcodes "" for
    # every non-live account) - assert the real value, not a tautology that
    # is true for any possible string.
    assert rep["dest_email"] == ""
    assert len(rep["rows"]) == 1
    # --json without --apply is a plan, not a report of what happened - no
    # row should read as written and no "result" key should be present.
    assert all(r["written"] is False for r in rep["rows"])
    assert "result" not in rep


def test_cli_apply_json_executes_before_reporting(two_account_env, tmp_path,
                                                   monkeypatch, capsys):
    """Finding 1: `sync --apply --json` must actually execute - the JSON
    output must describe what happened (rows written, a result), not the
    bare plan silently left unexecuted."""
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=2)
    monkeypatch.setattr(ct, "default_env", lambda: env)
    assert ct.main(["sync", "--apply", "--json"]) == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["result"] == "completed"
    assert all(r["written"] is True for r in rep["rows"])
    assert sorted(f for f in os.listdir(dst) if f.startswith("local_")) == [
        "local_0.json", "local_1.json"]


def test_cli_apply_zero_candidates_never_calls_run_sync(two_account_env, tmp_path,
                                                         monkeypatch, capsys):
    """Finding 3: parked finding 1 (no journaling an empty op) had code but
    no regression test. Assert the short-circuit directly: run_sync must
    never be reached for a zero-row plan, even with --apply, and the ops
    directory must stay untouched."""
    env, src, dst = two_account_env(tmp_path)
    monkeypatch.setattr(ct, "default_env", lambda: env)

    def _boom(*a, **k):
        raise AssertionError("run_sync must not be called for a zero-row plan")
    monkeypatch.setattr(ct, "run_sync", _boom)
    assert ct.main(["sync", "--apply"]) == 0
    out = capsys.readouterr().out
    assert "nothing to copy" in out
    assert ct.list_ops(env) == []


def test_cli_dry_run_names_the_destination_by_path_not_just_uuid(
        two_account_env, tmp_path, monkeypatch, capsys):
    """Finding 2: dest_email is '' in the normal case (every non-live
    account), so an 8-char account uuid prefix was all a cautious user had
    to recognise the destination by. The store path and org prefix must
    also be printed, for both endpoints."""
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=1)
    monkeypatch.setattr(ct, "default_env", lambda: env)
    assert ct.main(["sync", "--verbose"]) == 0
    out = capsys.readouterr().out
    assert dst in out
    assert src in out
    assert "dddddddd" in out          # dest org prefix
    assert "bbbbbbbb" in out          # source org prefix


def test_cli_caps_the_tombstone_list_like_the_to_copy_list(
        two_account_env, tmp_path, monkeypatch, capsys):
    """Minor 5: the tombstone-title loop had no cap, unlike the "to copy"
    list capped at 15 with an "... and N more" tail - a source account with
    many deliberate deletions must not produce unbounded output."""
    env, src, dst = two_account_env(tmp_path)
    for i in range(17):
        sid = "sid-t%d" % i
        _row(src, "local_t%d.json" % i, sid, "Deleted %d" % i)
        _transcript(env, sid)
        with open(os.path.join(dst, "deleted_" + sid), "w") as fh:
            fh.write("1")
    monkeypatch.setattr(ct, "default_env", lambda: env)
    ct.main(["sync", "--verbose"])
    out = capsys.readouterr().out
    assert out.count("kept deleted:") == 15
    assert "... and 2 more" in out


# ------------------------------- whole-branch review: Finding 1 (Critical)
#
# live_account's config.json fallback can name a DORMANT account as live
# (lastKnownAccountUuid's freshness across an account switch has never been
# measured), and execute_sync_op's "re-check" calls the same live_account, so
# it returns the same wrong answer. The ruling: keep the fallback usable, but
# back the weaker evidence with the running-app guard the other mutating
# commands already use.


def _weaken(env):
    """Force live_account down its config.json fallback: no ~/.claude.json,
    and config.json's lastKnownAccountUuid naming the source account."""
    os.unlink(os.path.join(env.home, ".claude.json"))
    cfg = os.path.join(os.path.dirname(env.store_candidates[0]), "config.json")
    with open(cfg, "w", encoding="utf-8") as fh:
        json.dump({"lastKnownAccountUuid": "aaaaaaaa-0000-0000-0000-000000000001"}, fh)


def _app_running(env):
    env.process_lister = lambda: [(999999, "claude.exe")]


def test_live_account_config_fallback_success_path(two_account_env, tmp_path):
    """The fallback's SUCCESS branch had no test at all - only its None
    return was covered. It must resolve, report itself as the weaker
    'config' determination, and still leave the other store as the
    destination."""
    env, src, dst = two_account_env(tmp_path)
    _weaken(env)
    acct = ct.live_account(env)
    assert acct is not None
    assert acct.account_uuid == "aaaaaaaa-0000-0000-0000-000000000001"
    assert acct.org_uuid == "bbbbbbbb-0000-0000-0000-000000000002"
    assert acct.email == ""                       # config.json records no email
    assert acct.resolved_from == "config"
    assert os.path.normcase(acct.path) == os.path.normcase(src)
    source, dest = ct.resolve_sync_endpoints(env)
    assert source.resolved_from == "config"       # provenance survives resolution
    assert os.path.normcase(dest.path) == os.path.normcase(dst)


def test_oauth_resolution_records_its_stronger_provenance(two_account_env, tmp_path):
    env, src, dst = two_account_env(tmp_path)
    assert ct.live_account(env).resolved_from == "oauth"
    assert ct.resolve_sync_endpoints(env)[0].resolved_from == "oauth"


def test_weakly_resolved_apply_refuses_while_claude_is_running(two_account_env,
                                                               tmp_path):
    """The hole: config.json can name the account the user switched AWAY
    from, making the "other" store the live one - and the execute-time
    re-check calls the same live_account, so it agrees. Since RULING 4 the
    running-app guard is no longer a fallback for this weaker resolution -
    it is the universal gate every mutation route takes, regardless of how
    (or whether) the live account was resolved. Post plan-review fix: the
    guard now sits in run_sync itself, before the op is journaled at all -
    a refused --apply must leave no stray 'journaled' op for doctor to flag
    or recover to have to clear."""
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=1)
    _weaken(env)
    m = ct.plan_sync(env, ct.SyncFlags())
    _app_running(env)
    n_ops_before = len(ct.list_ops(env))
    with pytest.raises(ct.Refusal, match="desktop app appears to be running") as exc_info:
        ct.run_sync(env, m)
    assert "close the desktop app" in str(exc_info.value)
    assert [f for f in os.listdir(dst) if f.startswith("local_")] == []
    assert len(ct.list_ops(env)) == n_ops_before    # refused before any op was journaled


def test_weakly_resolved_apply_succeeds_when_claude_is_not_running(two_account_env,
                                                                  tmp_path):
    """The fallback stays usable: the same weakly-resolved run completes
    normally once no Claude process is visible."""
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=1)
    _weaken(env)
    assert ct.run_sync(env, ct.plan_sync(env, ct.SyncFlags())) == "completed"
    assert [f for f in os.listdir(dst) if f.startswith("local_")] == ["local_0.json"]


def test_oauth_resolved_apply_takes_the_guard_too(two_account_env, tmp_path):
    """RULING 4 (2026-08-02) inverted the old contract here - an
    oauthAccount-resolved run used to skip the process guard entirely. E4
    measured oauthAccount STALE across a real desktop account switch, so
    strong-looking provenance buys no exemption any more."""
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=1)
    _app_running(env)
    n_ops_before = len(ct.list_ops(env))
    with pytest.raises(ct.Refusal, match="desktop app appears to be running"):
        ct.run_sync(env, ct.plan_sync(env, ct.SyncFlags()))
    assert [f for f in os.listdir(dst) if f.startswith("local_")] == []
    assert len(ct.list_ops(env)) == n_ops_before    # refused before any op was journaled


def test_undo_sync_of_a_weak_store_refuses_while_claude_is_running(two_account_env,
                                                                   tmp_path):
    """A weakly-resolved store must not be DELETED from while the app runs
    either - undo_sync inherits the guard through _sync_delete_targets."""
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=1)
    ct.run_sync(env, ct.plan_sync(env, ct.SyncFlags()))    # strong evidence: fine
    op = ct.list_ops(env)[-1]
    _weaken(env)
    _app_running(env)
    with pytest.raises(ct.Refusal, match="desktop app appears to be running"):
        ct.undo_sync(env, op)
    assert os.path.exists(os.path.join(dst, "local_0.json"))     # untouched


def test_recover_back_on_a_weak_store_refuses_while_claude_is_running(two_account_env,
                                                                      tmp_path):
    """recover's sync 'back' arm deletes through the same helper and must
    honour the same guard - a weakly-resolved account must not be written
    OR deleted from while the app is running."""
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=2)
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
    written_row = next(r for r in op.manifest["rows"] if r.get("written"))
    pending_row = next(r for r in op.manifest["rows"] if not r.get("written"))
    with open(pending_row["dest_path"], "w", encoding="utf-8") as fh:
        fh.write('{"unexpected": true}')          # blocks forward -> back is offered
    _weaken(env)
    _app_running(env)
    with pytest.raises(ct.Refusal, match="desktop app appears to be running"):
        ct.recover_op(env, op, "back")
    assert os.path.exists(written_row["dest_path"])          # nothing deleted


def test_cli_labels_a_config_resolved_source_instead_of_email_unknown(
        two_account_env, tmp_path, monkeypatch, capsys):
    """A config.json-resolved source used to print a bare '(email unknown)',
    identical to an ordinary dormant-side line. It must name the evidence and
    state that --apply now needs the app closed."""
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=1)
    _weaken(env)
    monkeypatch.setattr(ct, "default_env", lambda: env)
    assert ct.main(["sync", "--verbose"]) == 0
    out = capsys.readouterr().out
    assert "(from config.json)" in out
    assert "from  (email unknown)" not in out
    assert "lastKnownAccountUuid" in out
    assert "refuse while Claude is running" in out


def test_json_plan_carries_the_live_account_provenance(two_account_env, tmp_path,
                                                       monkeypatch, capsys):
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=1)
    _weaken(env)
    monkeypatch.setattr(ct, "default_env", lambda: env)
    assert ct.main(["sync", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["source_resolved_from"] == "config"


# ----------------------------- whole-branch review: Finding 2 (Important)
#
# classify_sync_op picked back-vs-forward purely from destination-row DRIFT.
# Every other way execute_sync_op can fail - atomic_write raising OSError,
# the containment LayoutError, the vanished-store LayoutError - leaves the
# pending row simply ABSENT, which reads as no drift, which yielded
# ["forward"]. Forward then raised the same error every time, back was
# refused as unsafe, and undo refused the op for not being 'completed':
# permanently stuck. 'back' is now always offered.


def test_stalled_sync_after_a_write_error_can_still_go_back(two_account_env, tmp_path):
    """The reviewer's first reproduction: a persistent write error (here a
    leftover .ct-tmp scratch DIRECTORY, which makes atomic_write's open() fail
    identically on every re-entry) used to leave the op at 'writing' with
    every exit refusing. A directory rather than a read-only file for the
    reason given in test_atomic_write_failure_becomes_a_refusal_not_a_traceback:
    the write bit blocks nobody when the test runs as root."""
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=2)
    m = ct.plan_sync(env, ct.SyncFlags())
    blocked = m["rows"][1]["dest_path"] + ".ct-tmp"
    os.mkdir(blocked)
    try:
        with pytest.raises(ct.Refusal, match="could not write"):
            ct.run_sync(env, m)
        op = ct.nonterminal_ops(env)[0]
        assert op.manifest["status"] == "writing"
        c = ct.classify_op(env, op)
        # The blocking row is ABSENT, not drifted - which is exactly why the
        # old drift-only classification called this "forward" and stuck.
        assert c["drifted_rows"] == []
        assert c["resolutions"] == ["forward", "back"]
        # forward really is a dead end: the same refusal, every time.
        with pytest.raises(ct.Refusal, match="could not write"):
            ct.recover_op(env, op, "forward")
        assert ct.recover_op(env, op, "back") == "rolled_back"
        assert ct.nonterminal_ops(env) == []
        # (the test's own .ct-tmp directory is still there; it is not a row)
        assert [f for f in os.listdir(dst) if f.endswith(".json")] == []
    finally:
        os.rmdir(blocked)


def test_stalled_sync_after_a_containment_error_can_still_go_back(two_account_env,
                                                                  tmp_path):
    """The reviewer's second reproduction: the row-containment LayoutError
    reaches the same dead end by the same route."""
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=2)
    m = ct.plan_sync(env, ct.SyncFlags())
    outside = os.path.join(str(tmp_path), "elsewhere.json")
    m["rows"][1]["dest_path"] = outside
    with pytest.raises(ct.LayoutError):
        ct.run_sync(env, m)
    op = ct.nonterminal_ops(env)[0]
    assert op.manifest["status"] == "writing"
    assert ct.classify_op(env, op)["resolutions"] == ["forward", "back"]
    with pytest.raises(ct.LayoutError):
        ct.recover_op(env, op, "forward")
    assert ct.recover_op(env, op, "back") == "rolled_back"
    assert ct.nonterminal_ops(env) == []
    assert [f for f in os.listdir(dst) if f.startswith("local_")] == []
    assert not os.path.exists(outside)


# ----------------------------- whole-branch review: Finding 3 (Important)


def _tombstoned(env, src, dst, titles):
    """A source row + transcript + destination tombstone for each title."""
    out = []
    for i, title in enumerate(titles):
        sid = "sid-r%d" % i
        _row(src, "local_r%d.json" % i, sid, title)
        _transcript(env, sid)
        with open(os.path.join(dst, "deleted_" + sid), "w") as fh:
            fh.write("1")
        out.append(sid)
    return out


def test_include_deleted_refuses_an_ambiguous_term(two_account_env, tmp_path):
    """The spec says the flag names ONE session and "never applies blanket to
    a whole run". A bare substring tested per row let one term resurrect
    every tombstoned session it happened to hit - the reviewer got three."""
    env, src, dst = two_account_env(tmp_path)
    _tombstoned(env, src, dst, ["Alpha", "Beta", "Gamma"])
    source, dest = ct.resolve_sync_endpoints(env)
    with pytest.raises(ct.Refusal, match="matched 3 sessions") as exc_info:
        ct.select_sync_rows(env, source, dest, ct.SyncFlags(include_deleted=("a",)))
    msg = str(exc_info.value)
    for title in ("Alpha", "Beta", "Gamma"):
        assert title in msg               # the candidates are named
    assert "not a blanket override" in msg


def test_include_deleted_accepts_a_full_session_id_when_titles_collide(two_account_env,
                                                                       tmp_path):
    """A full cliSessionId is the unambiguous escape hatch even when every
    title contains the same substring."""
    env, src, dst = two_account_env(tmp_path)
    sids = _tombstoned(env, src, dst, ["Alpha", "Beta", "Gamma"])
    source, dest = ct.resolve_sync_endpoints(env)
    picked, tally = ct.select_sync_rows(env, source, dest,
                                        ct.SyncFlags(include_deleted=(sids[1],)))
    assert [p["title"] for p in picked] == ["Beta"]
    assert tally["resurrected"] == ["Beta"]
    assert sorted(tally["deleted"]) == ["Alpha", "Gamma"]


def test_include_deleted_marks_the_row_and_fills_the_resurrected_tally(two_account_env,
                                                                       tmp_path):
    """A rescued row used to enter the plan with no marker at all, and
    tally["deleted"] held only the rows that were SKIPPED - so nothing
    downstream could tell a tombstone had been overridden."""
    env, src, dst = two_account_env(tmp_path)
    _tombstoned(env, src, dst, ["Alpha", "Beta"])
    _row(src, "local_plain.json", "sid-plain", "Ordinary")
    _transcript(env, "sid-plain")
    source, dest = ct.resolve_sync_endpoints(env)
    picked, tally = ct.select_sync_rows(env, source, dest,
                                        ct.SyncFlags(include_deleted=("Alpha",)))
    by_title = {p["title"]: p for p in picked}
    assert by_title["Alpha"]["overrode_tombstone"] is True
    assert by_title["Ordinary"]["overrode_tombstone"] is False
    assert tally["resurrected"] == ["Alpha"]
    assert tally["deleted"] == ["Beta"]
    m = ct.plan_sync(env, ct.SyncFlags(include_deleted=("Alpha",)))
    marked = {r["title"]: r["overrode_tombstone"] for r in m["rows"]}
    assert marked == {"Alpha": True, "Ordinary": False}


def test_cli_names_what_include_deleted_resurrects_before_the_copy_list(
        two_account_env, tmp_path, monkeypatch, capsys):
    """Resurrecting a deliberately deleted session is the first row of the
    design's own risk table and was the least visible thing the command did:
    the report said nothing at all. It must now be named, unmissably, before
    the ordinary "to copy" list."""
    env, src, dst = two_account_env(tmp_path)
    _tombstoned(env, src, dst, ["Alpha", "Beta"])
    monkeypatch.setattr(ct, "default_env", lambda: env)
    assert ct.main(["sync", "--include-deleted", "Alpha", "--verbose"]) == 0
    out = capsys.readouterr().out
    assert "RESURRECTING 1 session" in out
    assert out.index("RESURRECTING") < out.index("to copy")
    assert "!! Alpha" in out
    assert "kept deleted: Beta" in out          # the honoured one still reported


def test_cli_include_deleted_ambiguity_is_a_refusal_not_three_resurrections(
        two_account_env, tmp_path, monkeypatch, capsys):
    env, src, dst = two_account_env(tmp_path)
    _tombstoned(env, src, dst, ["Alpha", "Beta", "Gamma"])
    monkeypatch.setattr(ct, "default_env", lambda: env)
    assert ct.main(["sync", "--include-deleted", "a", "--apply", "--verbose"]) == 1
    err = capsys.readouterr().err
    assert "matched 3 sessions" in err
    assert [f for f in os.listdir(dst) if f.startswith("local_")] == []


# ----------------------------- whole-branch review: Finding 4 (Important)


def test_endpoints_are_printed_before_the_run_even_when_it_fails(
        two_account_env, tmp_path, monkeypatch, capsys):
    """The spec calls a recognisable destination a safety feature and prints
    both endpoints BEFORE anything happens. Moving execution ahead of all
    printing (to make --apply --json report reality) was over-broad: a run
    that died inside run_sync emitted only "refused: <msg>" with no record of
    which two accounts were involved."""
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=1)
    monkeypatch.setattr(ct, "default_env", lambda: env)

    def boom(*a, **k):
        raise ct.Refusal("run_sync exploded")
    monkeypatch.setattr(ct, "run_sync", boom)
    assert ct.main(["sync", "--apply", "--verbose"]) == 1
    cap = capsys.readouterr()
    assert "run_sync exploded" in cap.err
    assert src in cap.out and dst in cap.out
    assert "signed in" in cap.out


# ----------------------------- whole-branch review: Finding 5 (Important)


def test_to_can_name_a_duplicated_account_by_store_path(mkenv, tmp_path):
    """default_env legitimately yields two store roots on Windows (the MSIX
    package path and the classic %APPDATA% path), and a machine that migrated
    between installers can hold the same account uuids under both. Matching
    only on account/org/email made every --to value ambiguous while the
    refusal listed two identical 8-char lines: no input could resolve it."""
    live_a = "aaaaaaaa-0000-0000-0000-000000000001"
    live_o = "bbbbbbbb-0000-0000-0000-000000000002"
    other_a = "cccccccc-0000-0000-0000-000000000003"
    other_o = "dddddddd-0000-0000-0000-000000000004"
    env = mkenv(tmp_path, n_store_roots=2)
    os.makedirs(os.path.join(env.store_candidates[0], live_a, live_o))
    dup = []
    for root in env.store_candidates:
        p = os.path.join(root, other_a, other_o)
        os.makedirs(p)
        dup.append(os.path.realpath(p))
    with open(os.path.join(env.home, ".claude.json"), "w", encoding="utf-8") as fh:
        json.dump({"oauthAccount": {"accountUuid": live_a, "organizationUuid": live_o,
                                    "emailAddress": "me@example.com"}}, fh)

    with pytest.raises(ct.Refusal) as exc_info:
        ct.resolve_sync_endpoints(env)
    msg = str(exc_info.value)
    assert dup[0] in msg and dup[1] in msg          # distinguishable at last
    with pytest.raises(ct.Refusal) as exc_info:
        ct.resolve_sync_endpoints(env, to=other_a)
    msg = str(exc_info.value)
    assert "be more specific" in msg
    assert dup[0] in msg and dup[1] in msg
    # ...and the store path is now a usable discriminator, which nothing was.
    source, dest = ct.resolve_sync_endpoints(env, to="store1")
    assert os.path.normcase(dest.path) == os.path.normcase(dup[1])
    assert os.path.normcase(ct.resolve_sync_endpoints(env, to="store0")[1].path) == \
        os.path.normcase(dup[0])


# ----------------------------- whole-branch review: Finding 6 (Important)


def _save_snapshots(monkeypatch):
    """Record the written-flags the journal held at each save_manifest."""
    saves = []
    real = ct.save_manifest

    def counting(op):
        saves.append([bool(r.get("written")) for r in op.manifest.get("rows", [])])
        real(op)
    monkeypatch.setattr(ct, "save_manifest", counting)
    return saves


def test_journal_writes_are_batched_when_the_manifest_is_large(two_account_env,
                                                               tmp_path, monkeypatch):
    """save_manifest rewrites and fsyncs the WHOLE manifest, which carries a
    base64 post-image of every row - so one save per row is O(rows x
    manifest). Measured at 60 rows x ~2 KB that is 11.9 MB; extrapolated to
    this machine's real --verbatim rows (432 rows, up to 1.36 MB) it is over
    160 GB, i.e. an apparent hang. A zero budget stands in for a manifest too
    big to journal per row."""
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=4)
    m = ct.plan_sync(env, ct.SyncFlags())
    monkeypatch.setattr(ct, "SYNC_JOURNAL_BYTE_BUDGET", 0)
    saves = _save_snapshots(monkeypatch)
    assert ct.run_sync(env, m) == "completed"
    # No per-row journal write: a snapshot with SOME but not all rows flagged
    # is the signature of one save per row (3 of them, for 4 rows).
    assert [s for s in saves if any(s) and not all(s)] == []
    assert saves[-1] == [True] * 4                 # the tail always lands
    assert len([f for f in os.listdir(dst) if f.startswith("local_")]) == 4


def test_small_manifests_still_journal_every_row(two_account_env, tmp_path,
                                                 monkeypatch):
    """The budget is a cap, not a mode switch: an ordinary (stripped) sync is
    nowhere near it, so it keeps the finest-grained crash record."""
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=4)
    saves = _save_snapshots(monkeypatch)
    assert ct.run_sync(env, ct.plan_sync(env, ct.SyncFlags())) == "completed"
    assert len([s for s in saves if any(s) and not all(s)]) == 3


def test_an_interrupted_batch_resumes_without_duplicating_or_refusing(
        two_account_env, tmp_path, monkeypatch):
    """The batching is safe precisely because of execute_sync_op's
    re-read-before-write: a manifest that UNDER-reports what landed is
    harmless, since a row already holding exactly the planned bytes is
    recognised and skipped on resume. Simulate the worst case a hard kill can
    produce - every row on disk, none of them journalled - and resume."""
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=4)
    monkeypatch.setattr(ct, "SYNC_JOURNAL_BYTE_BUDGET", 0)
    assert ct.run_sync(env, ct.plan_sync(env, ct.SyncFlags())) == "completed"

    op = ct.list_ops(env)[-1]
    for r in op.manifest["rows"]:
        r["written"] = False               # the journal tail was lost
    op.manifest["status"] = "journaled"
    ct.save_manifest(op)
    before = [os.stat(r["dest_path"]).st_ino for r in op.manifest["rows"]]

    assert ct.execute_sync_op(env, op) == "completed"
    assert all(r["written"] for r in op.manifest["rows"])
    # atomic_write goes through os.replace, which changes st_ino - identical
    # inodes prove the re-read branch skipped every row rather than rewriting
    # it (the same signal test_crash_before_manifest_save_is_forward_pass_safe
    # uses).
    assert [os.stat(r["dest_path"]).st_ino for r in op.manifest["rows"]] == before
    assert len([f for f in os.listdir(dst) if f.startswith("local_")]) == 4


def test_an_exception_always_journals_what_landed(two_account_env, tmp_path,
                                                  monkeypatch):
    """Batching must not cost 'back' its accuracy: every failure this process
    can observe journals the written flags on the way out, so recover --back
    still removes exactly the rows that landed. Only a hard kill can lose the
    tail."""
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=3)
    monkeypatch.setattr(ct, "SYNC_JOURNAL_BYTE_BUDGET", 0)
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
    op = ct.nonterminal_ops(env)[0]                  # re-read from disk
    assert [bool(r.get("written")) for r in op.manifest["rows"]] == [True, False, False]
    assert ct.recover_op(env, op, "back") == "rolled_back"
    assert [f for f in os.listdir(dst) if f.startswith("local_")] == []


# --------------------------- whole-branch review: minor findings


def _reparse_or_skip(target, link):
    """A directory reparse point at `link` pointing at `target`: a symlink
    where the platform allows one, otherwise a Windows junction (mklink /J,
    which needs no elevation), otherwise skip."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return link
    except (OSError, NotImplementedError, AttributeError):
        pass
    if os.name == "nt":
        import subprocess
        r = subprocess.run(["cmd", "/c", "mklink", "/J", link, target],
                           capture_output=True, text=True)
        if r.returncode == 0:
            return link
    pytest.skip("cannot create a directory reparse point in this environment")


def test_live_account_check_resolves_reparse_points_on_the_write_side(two_account_env,
                                                                      tmp_path):
    """Minor: the live-account comparison used normpath while
    ensure_contained (the other half of the same guarantee) uses realpath. A
    junction makes them disagree - dest realpath == live realpath while the
    normpath strings differ - which skips the single most load-bearing
    refusal in the design. Before the fix this raised the row loop's
    containment LayoutError instead of the LIVE-account Refusal."""
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=1)
    m = ct.plan_sync(env, ct.SyncFlags())
    m["dest_path"] = _reparse_or_skip(src, os.path.join(str(tmp_path), "live-link"))
    with pytest.raises(ct.Refusal, match="LIVE account"):
        ct.run_sync(env, m)
    assert [f for f in os.listdir(src) if f.startswith("local_")] == ["local_0.json"]


def test_live_account_check_resolves_reparse_points_on_the_delete_side(two_account_env,
                                                                       tmp_path):
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=1)
    ct.run_sync(env, ct.plan_sync(env, ct.SyncFlags()))
    op = ct.list_ops(env)[-1]
    op.manifest["dest_path"] = _reparse_or_skip(
        src, os.path.join(str(tmp_path), "live-link-2"))
    with pytest.raises(ct.Refusal, match="LIVE account"):
        ct.undo_sync(env, op)
    assert os.path.exists(os.path.join(dst, "local_0.json"))


def test_apply_json_reports_the_op_id(two_account_env, tmp_path, monkeypatch, capsys):
    """Minor: new_op shallow-copies the manifest and sets op_id on ITS copy,
    so automation that just ran `sync --apply --json` got a result with no id
    to hand to `undo --id`."""
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=1)
    monkeypatch.setattr(ct, "default_env", lambda: env)
    assert ct.main(["sync", "--apply", "--json"]) == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["result"] == "completed"
    assert rep["op_id"] == ct.list_ops(env)[-1].manifest["op_id"]


def test_classify_survives_a_corrupt_or_missing_post_image(two_account_env, tmp_path):
    """Minor: _sync_row_drift's docstring said "Never raises", but
    unb64(r["post_b64"]) raises binascii.Error on a corrupt manifest and
    KeyError if the field is missing - and main() catches neither. The
    original defect this whole feature had to fix was exactly "classify_op
    raised on a sync manifest"."""
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=1)
    op = ct.new_op(env, ct.plan_sync(env, ct.SyncFlags()))
    op.manifest["rows"][0]["post_b64"] = "!!!not base64!!!"
    c = ct.classify_op(env, op)                    # must not raise
    assert c["resolutions"] == ["back"]
    assert "could not be read" in c["note"]
    del op.manifest["rows"][0]["post_b64"]         # the KeyError half
    assert ct.classify_op(env, op)["resolutions"] == ["back"]


def test_tally_is_not_journaled_to_disk(two_account_env, tmp_path):
    """Minor: plan_sync returns tally inside the manifest, so every skipped
    session's title - including the ones the destination deliberately DELETED
    - was written into ~/.claude-code-journal. Nothing in execute/undo/recover
    reads it."""
    env, src, dst = two_account_env(tmp_path)
    _row(src, "local_a.json", "sid-a", "Copy Me")
    _transcript(env, "sid-a")
    _row(src, "local_b.json", "sid-b", "Deleted Secret")
    _transcript(env, "sid-b")
    with open(os.path.join(dst, "deleted_sid-b"), "w") as fh:
        fh.write("1")
    m = ct.plan_sync(env, ct.SyncFlags())
    assert m["tally"]["deleted"] == ["Deleted Secret"]      # the report keeps it
    assert ct.run_sync(env, m) == "completed"
    op = ct.list_ops(env)[-1]
    assert "tally" not in op.manifest
    with open(ct.manifest_path(op), encoding="utf-8") as fh:
        assert "Deleted Secret" not in fh.read()


def test_unreadable_destination_store_is_a_layout_error_not_a_traceback(
        two_account_env, tmp_path, monkeypatch):
    """Minor: discover_stores proves only the ROOT is enumerable. A
    PermissionError on an account/org dir escaped main() as a raw unredacted
    traceback."""
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=1)
    real_listdir = os.listdir

    def boom(p):
        if os.path.normcase(str(p)) == os.path.normcase(dst):
            raise PermissionError("denied")
        return real_listdir(p)
    monkeypatch.setattr(os, "listdir", boom)
    with pytest.raises(ct.LayoutError) as exc_info:
        ct.plan_sync(env, ct.SyncFlags())
    assert exc_info.value.exit_code == 2


def test_unreadable_account_dir_is_a_layout_error_not_a_traceback(
        two_account_env, tmp_path, monkeypatch):
    env, src, dst = two_account_env(tmp_path)
    account_dir = os.path.dirname(dst)
    real_listdir = os.listdir

    def boom(p):
        if os.path.normcase(str(p)) == os.path.normcase(account_dir):
            raise PermissionError("denied")
        return real_listdir(p)
    monkeypatch.setattr(os, "listdir", boom)
    with pytest.raises(ct.LayoutError) as exc_info:
        ct.resolve_sync_endpoints(env)
    assert exc_info.value.exit_code == 2


def test_drifted_rows_are_deduplicated(two_account_env, tmp_path):
    """Minor: a pending and a written row can share a title, which listed it
    twice - and that list reaches cmd_recover's printed line, reading as two
    separate problems."""
    env, src, dst = two_account_env(tmp_path)
    for i in range(2):
        sid = "sid-%d" % i
        _row(src, "local_%d.json" % i, sid, "Same Title")
        _transcript(env, sid)
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
    for r in op.manifest["rows"]:
        with open(r["dest_path"], "w", encoding="utf-8") as fh:
            fh.write('{"unexpected": true}')       # both drift, same title
    c = ct.classify_op(env, op)
    assert c["resolutions"] == ["back"]
    assert c["drifted_rows"] == ["Same Title"]


# --------------------------- dormant-account email (found during the drill)


DORMANT = "cccccccc-0000-0000-0000-000000000003"


def _agent_mode_config(env, account_uuid, oauth):
    """Write the per-account Claude Code config the desktop app drops inside
    its local-agent-mode sandbox, at the nesting depth the real one uses."""
    d = os.path.join(os.path.dirname(env.store_candidates[0]),
                     "local-agent-mode-sessions", account_uuid,
                     "dddddddd-0000-0000-0000-000000000004",
                     "agent", "local_ditto_x", ".claude")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, ".claude.json"), "w", encoding="utf-8") as fh:
        json.dump({"oauthAccount": oauth}, fh)
    return d


def test_dormant_account_email_is_recovered_from_the_agent_mode_config(
        two_account_env, tmp_path):
    """~/.claude.json names only the LIVE account, so the destination used to
    print as '(email unknown)' - eight hex characters to identify the account
    you are about to write into. The desktop app leaves a per-account config
    in its local-agent-mode sandbox that names it."""
    env, src, dst = two_account_env(tmp_path)
    assert ct.dormant_account_email(env, DORMANT) == ""      # nothing there yet
    _agent_mode_config(env, DORMANT, {"accountUuid": DORMANT,
                                      "emailAddress": "other@example.com"})
    assert ct.dormant_account_email(env, DORMANT) == "other@example.com"

    source, dest = ct.resolve_sync_endpoints(env)
    assert dest.email == "other@example.com"
    # ...and naming the destination by that email now works.
    assert ct.resolve_sync_endpoints(env, to="other@example.com")[1].path == dest.path


def test_dormant_account_email_ignores_a_config_for_a_different_account(
        two_account_env, tmp_path):
    """Trusting the email without checking the uuid inside would let an
    unrelated sandbox mislabel an account, which is worse than no label."""
    env, src, dst = two_account_env(tmp_path)
    _agent_mode_config(env, DORMANT, {"accountUuid": "somebody-else",
                                      "emailAddress": "wrong@example.com"})
    assert ct.dormant_account_email(env, DORMANT) == ""
    assert ct.resolve_sync_endpoints(env)[1].email == ""


def test_dormant_account_email_survives_a_corrupt_config(two_account_env, tmp_path):
    """Best-effort means best-effort: a broken config downgrades the label,
    it never fails the run."""
    env, src, dst = two_account_env(tmp_path)
    d = _agent_mode_config(env, DORMANT, {"accountUuid": DORMANT,
                                          "emailAddress": "other@example.com"})
    with open(os.path.join(d, ".claude.json"), "w", encoding="utf-8") as fh:
        fh.write("{ not json")
    assert ct.dormant_account_email(env, DORMANT) == ""
    assert ct.resolve_sync_endpoints(env)[1].email == ""


# ---------------------------------------------------------------- RULING 4


def _write_desktop_config(env, account_uuid):
    """The desktop's config.json, next to the claude-code-sessions dir -
    the exact location live_account() reads (os.path.dirname(candidate))."""
    cfg = os.path.join(os.path.dirname(env.store_candidates[0]), "config.json")
    with open(cfg, "w", encoding="utf-8") as fh:
        json.dump({"lastKnownAccountUuid": account_uuid}, fh)


class TestIdentityDisagreement:
    def test_live_account_fails_closed_when_identity_files_disagree(
            self, two_account_env, tmp_path):
        env, src, dst = two_account_env(tmp_path)
        # E4 shape: oauth says aaaa..., the desktop's config says cccc...
        _write_desktop_config(env, "cccccccc-0000-0000-0000-000000000003")
        assert ct.live_account(env) is None

    def test_identity_disagreement_reports_both_uuids(self, two_account_env, tmp_path):
        env, src, dst = two_account_env(tmp_path)
        _write_desktop_config(env, "cccccccc-0000-0000-0000-000000000003")
        assert ct._identity_disagreement(env) == (
            "aaaaaaaa-0000-0000-0000-000000000001",
            "cccccccc-0000-0000-0000-000000000003")

    def test_agreement_is_not_a_disagreement(self, two_account_env, tmp_path):
        env, src, dst = two_account_env(tmp_path)
        _write_desktop_config(env, "aaaaaaaa-0000-0000-0000-000000000001")
        assert ct._identity_disagreement(env) is None
        live = ct.live_account(env)
        assert live is not None and live.resolved_from == "oauth"

    def test_oauth_alone_still_resolves(self, two_account_env, tmp_path):
        env, src, dst = two_account_env(tmp_path)   # no config.json written
        assert ct._identity_disagreement(env) is None
        live = ct.live_account(env)
        assert live is not None and live.resolved_from == "oauth"

    def test_sync_refuses_on_disagreement_naming_both_and_the_cli_fix(
            self, two_account_env, tmp_path):
        env, src, dst = two_account_env(tmp_path)
        _write_desktop_config(env, "cccccccc-0000-0000-0000-000000000003")
        with pytest.raises(ct.Refusal) as exc:
            ct.resolve_sync_endpoints(env)
        msg = str(exc.value)
        assert "disagree" in msg
        assert "aaaaaaaa" in msg and "cccccccc" in msg
        assert "/login" in msg

    def test_no_evidence_refusal_no_longer_promises_desktop_signin_alone(
            self, mkenv, tmp_path):
        env = mkenv(tmp_path)
        # one store dir so the listing is non-empty; no identity file at all
        os.makedirs(os.path.join(env.store_candidates[0],
                                 "aaaaaaaa-0000-0000-0000-000000000001",
                                 "bbbbbbbb-0000-0000-0000-000000000002"))
        with pytest.raises(ct.Refusal) as exc:
            ct.resolve_sync_endpoints(env)
        # both freshening routes named. Assert on text only the NEW message
        # carries - the old one also mentioned both filenames, so matching on
        # those would pass against the unfixed code (no RED).
        assert "authenticate the CLI" in str(exc.value)
        assert "writes config.json" in str(exc.value)

    def test_malformed_identity_file_is_no_signal_not_a_crash(
            self, two_account_env, tmp_path):
        env, src, dst = two_account_env(tmp_path)
        # a list root makes ({} or {}).get-style code raise AttributeError;
        # the helper must swallow the shape, not traceback
        with open(os.path.join(env.home, ".claude.json"), "w", encoding="utf-8") as fh:
            json.dump([1, 2], fh)
        assert ct._identity_disagreement(env) is None

    def test_non_string_config_uuid_is_no_signal(self, two_account_env, tmp_path):
        env, src, dst = two_account_env(tmp_path)
        cfg = os.path.join(os.path.dirname(env.store_candidates[0]), "config.json")
        with open(cfg, "w", encoding="utf-8") as fh:
            json.dump({"lastKnownAccountUuid": 12345}, fh)
        # a non-string uuid must not become a "disagreement" whose [:8]
        # slice tracebacks inside a refusal message
        assert ct._identity_disagreement(env) is None

    def test_live_account_survives_a_malformed_identity_file(
            self, two_account_env, tmp_path):
        # end-to-end: live_account's own reads must be as shape-hardened as
        # the helper's - a list root previously raised AttributeError there
        env, src, dst = two_account_env(tmp_path)
        with open(os.path.join(env.home, ".claude.json"), "w", encoding="utf-8") as fh:
            json.dump([1, 2], fh)
        _write_desktop_config(env, "aaaaaaaa-0000-0000-0000-000000000001")
        live = ct.live_account(env)          # malformed oauth -> config fallback
        assert live is not None and live.resolved_from == "config"


_DESKTOP_EXE = ("c:\\program files\\windowsapps\\"
                "claude_1.24012.9.0_x64__pzs8sxrjxfjjc\\app\\claude.exe")
_CLI_EXE = "c:\\users\\u\\appdata\\roaming\\claude\\claude-code\\2.1.219\\claude.exe"


class TestGuardAllRoutes:
    def _syncable_row(self, env, src, write_transcript):
        sid = "11111111-2222-3333-4444-555555555555"
        write_transcript(env, "proj", sid, [{"type": "user"}])
        with open(os.path.join(src, "local_row1.json"), "w", encoding="utf-8") as fh:
            json.dump({"sessionId": "local_row1", "cliSessionId": sid,
                       "title": "guard test"}, fh)

    def test_oauth_resolved_apply_refuses_while_desktop_runs(
            self, two_account_env, write_transcript, tmp_path):
        # THE ruling test: resolution is strong (oauth, no config, no
        # disagreement) and the guard must fire anyway - and must fire
        # BEFORE anything lands (a guard placed after the row loop would
        # also raise, so the refusal alone proves nothing). Post
        # plan-review fix: it must fire before the op is even journaled -
        # otherwise every refused --apply leaves a stray 'journaled' op
        # that doctor flags and recover has to clear, which is exactly the
        # newly-common case (user forgot to close the desktop app).
        env, src, dst = two_account_env(tmp_path)
        self._syncable_row(env, src, write_transcript)
        env.process_lister = lambda: [(99999, _DESKTOP_EXE)]
        manifest = ct.plan_sync(env, ct.SyncFlags())
        n_ops_before = len(ct.list_ops(env))
        with pytest.raises(ct.Refusal, match="desktop app appears to be running"):
            ct.run_sync(env, manifest)
        assert [f for f in os.listdir(dst) if f.startswith("local_")] == []
        assert len(ct.list_ops(env)) == n_ops_before    # no op created at all

    def test_oauth_resolved_undo_refuses_while_desktop_runs(
            self, two_account_env, write_transcript, tmp_path):
        # the delete route under STRONG resolution - the weak-store undo
        # test already exists; this is the exemption being removed
        env, src, dst = two_account_env(tmp_path)
        self._syncable_row(env, src, write_transcript)
        assert ct.run_sync(env, ct.plan_sync(env, ct.SyncFlags())) == "completed"
        op = ct.list_ops(env)[-1]
        env.process_lister = lambda: [(99999, _DESKTOP_EXE)]
        with pytest.raises(ct.Refusal, match="desktop app appears to be running"):
            ct.undo_sync(env, op)
        assert os.path.exists(os.path.join(dst, "local_row1.json"))   # untouched

    def test_apply_proceeds_when_only_the_cli_runs(
            self, two_account_env, write_transcript, tmp_path):
        # PINNING TEST - green before and after this task. Pre-task it
        # passes trivially (the oauth path never consults the lister);
        # post-task it pins Task 2's narrowing against the guard rewrite:
        # the guard now consults the lister and must still let a
        # CLI-only process list through. The RED for the narrowing itself
        # was Task 2's test_cli_paths_do_not_match.
        env, src, dst = two_account_env(tmp_path)
        self._syncable_row(env, src, write_transcript)
        env.process_lister = lambda: [(99999, _CLI_EXE)]
        manifest = ct.plan_sync(env, ct.SyncFlags())
        assert ct.run_sync(env, manifest) == "completed"
        assert os.path.exists(os.path.join(dst, "local_row1.json"))

    def test_guard_message_names_disagreement_and_cli_reauth(
            self, two_account_env, tmp_path):
        env, src, dst = two_account_env(tmp_path)
        _write_desktop_config(env, "cccccccc-0000-0000-0000-000000000003")
        env.process_lister = lambda: [(99999, _DESKTOP_EXE)]
        with pytest.raises(ct.Refusal) as exc:
            ct._guard_mutation(env, "write to")
        msg = str(exc.value)
        assert "aaaaaaaa" in msg and "cccccccc" in msg and "/login" in msg

    def test_guard_is_silent_when_nothing_runs(self, two_account_env, tmp_path):
        env, src, dst = two_account_env(tmp_path)
        _write_desktop_config(env, "cccccccc-0000-0000-0000-000000000003")
        ct._guard_mutation(env, "write to")     # must not raise

    def test_guard_message_when_process_list_is_unavailable(
            self, two_account_env, tmp_path):
        # Task 2's narrowing gives claude_running a sentinel entry
        # (_PROC_UNAVAILABLE) when enumeration itself fails - "couldn't
        # look" is never "nothing there". But the normal refusal wording
        # ("the Claude desktop app appears to be running") would be a LIE
        # here: nothing said the app is running, only that we don't know.
        # The guard must say the process list was unavailable, not assert
        # the app IS running.
        env, src, dst = two_account_env(tmp_path)
        env.process_lister = lambda: [(-1, ct._PROC_UNAVAILABLE)]
        with pytest.raises(ct.Refusal) as exc:
            ct._guard_mutation(env, "write to")
        msg = str(exc.value)
        # Assert on wording unique to the sentinel branch, not just
        # "unavailable" - _PROC_UNAVAILABLE's own text contains that word
        # and running[0] is interpolated into the GENERIC "appears to be
        # running" message too, so a bare "unavailable" substring check
        # passes even with the sentinel branch deleted (it would then fall
        # through to the generic Refusal, which still quotes running[0]).
        assert "could not be read" in msg
        assert "appears to be running" not in msg

    def test_the_old_name_is_gone(self):
        assert not hasattr(ct, "_guard_weakly_resolved")

    def test_recover_forward_still_refuses_while_desktop_runs(
            self, two_account_env, tmp_path):
        # Plan-review fix pins TWO gateways now: run_sync's new pre-journal
        # guard protects a FRESH --apply (this class's other tests), but
        # recover --forward re-enters execute_sync_op directly - it never
        # calls run_sync, so its only protection is the execute-time guard
        # that stayed exactly as reviewed. A crash-interrupted sync must
        # still refuse to finish while the desktop app is running.
        env, src, dst = two_account_env(tmp_path)
        _prepared(env, src, dst, n=2)
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
        env.process_lister = lambda: [(99999, _DESKTOP_EXE)]
        with pytest.raises(ct.Refusal, match="desktop app appears to be running"):
            ct.recover_op(env, op, "forward")


# ------------------------------------------- Finding I1: disagreement, not
# just "no evidence", must gate the dest-is-possibly-live checks too


class TestDisagreementGuardsDestPossiblyLive:
    """Task 1 made live_account() return None for a second, DIFFERENT reason:
    not only "no evidence at all" (the original meaning, safe to proceed
    when the destination doesn't match) but also "the identity files
    disagree about which account is live" - and a disagreement is not "no
    evidence", it's live evidence that either of two named accounts might
    be the live one. execute_sync_op and _sync_delete_targets both treated
    every None the same way (proceed unless dest == the resolved live
    account), which is a regression once None can mean the second thing.
    """

    def test_undo_refuses_when_disagreement_names_dest_account(
            self, two_account_env, tmp_path):
        # The sync itself completes cleanly while the identity files still
        # agree - the disagreement only appears AFTERWARDS, before undo
        # runs, which is exactly the shape _sync_delete_targets' own
        # execute-time re-check exists to catch.
        env, src, dst = two_account_env(tmp_path)
        _prepared(env, src, dst, n=1)
        assert ct.run_sync(env, ct.plan_sync(env, ct.SyncFlags())) == "completed"
        op = ct.list_ops(env)[-1]
        assert os.path.exists(os.path.join(dst, "local_0.json"))

        # config.json now names the DESTINATION account while oauth still
        # names the source - the E4 shape, but this time the disagreement
        # names the very store undo is about to delete from.
        _write_desktop_config(env, "cccccccc-0000-0000-0000-000000000003")
        assert ct.live_account(env) is None
        assert ct._identity_disagreement(env) == (
            "aaaaaaaa-0000-0000-0000-000000000001",
            "cccccccc-0000-0000-0000-000000000003")

        with pytest.raises(ct.Refusal) as exc:
            ct.undo_sync(env, op)
        msg = str(exc.value)
        assert "disagree" in msg
        assert "aaaaaaaa" in msg and "cccccccc" in msg
        assert "/login" in msg
        assert os.path.exists(os.path.join(dst, "local_0.json"))    # untouched

    def test_execute_refuses_when_disagreement_names_dest_account(
            self, two_account_env, tmp_path):
        # Same shape on the write side: recover --forward re-enters
        # execute_sync_op directly (never through run_sync/resolve_sync_
        # endpoints), so a planned-and-journaled op is the realistic way
        # the identity files can disagree by the time this runs. A direct
        # execute_sync_op call on a journaled op is the idiom the existing
        # suite already uses for exactly this shape (e.g.
        # test_classify_survives_a_corrupt_or_missing_post_image).
        env, src, dst = two_account_env(tmp_path)
        _prepared(env, src, dst, n=1)
        m = ct.plan_sync(env, ct.SyncFlags())      # planned while identity agrees
        op = ct.new_op(env, m)                     # journaled

        _write_desktop_config(env, "cccccccc-0000-0000-0000-000000000003")
        assert ct.live_account(env) is None

        with pytest.raises(ct.Refusal) as exc:
            ct.execute_sync_op(env, op)
        msg = str(exc.value)
        assert "disagree" in msg
        assert "aaaaaaaa" in msg and "cccccccc" in msg
        assert "/login" in msg
        assert not os.path.exists(os.path.join(dst, "local_0.json"))
        assert op.manifest["status"] == "journaled"     # never advanced

    def test_disagreement_naming_other_accounts_does_not_block_a_third(
            self, two_account_env, tmp_path):
        # Guard against over-refusal: a disagreement names exactly two
        # accounts. A destination that is neither of them - a third,
        # uninvolved dormant account - must still proceed.
        env, src, dst = two_account_env(tmp_path)
        third = os.path.join(env.store_candidates[0],
                             "eeeeeeee-0000-0000-0000-000000000005",
                             "ffffffff-0000-0000-0000-000000000006")
        os.makedirs(third)
        _row(src, "local_0.json", "sid-0", "Session 0")
        _transcript(env, "sid-0")
        m = ct.plan_sync(env, ct.SyncFlags(to="eeeeeeee"))    # while identity agrees
        assert os.path.normcase(m["dest_path"]) == os.path.normcase(third)

        # Disagreement between the source (a...) and the OTHER dormant
        # account (c...) - neither is the third account this sync targets.
        _write_desktop_config(env, "cccccccc-0000-0000-0000-000000000003")
        assert ct.live_account(env) is None
        assert ct._identity_disagreement(env) is not None

        assert ct.run_sync(env, m) == "completed"
        assert os.path.exists(os.path.join(third, "local_0.json"))

    def test_no_identity_evidence_at_execute_time_still_proceeds(
            self, two_account_env, tmp_path):
        # The ORIGINAL meaning of a None live account - genuinely no
        # evidence, not a disagreement - must keep proceeding exactly as
        # before Task 1. Only a disagreement is new grounds to refuse.
        env, src, dst = two_account_env(tmp_path)
        _prepared(env, src, dst, n=1)
        m = ct.plan_sync(env, ct.SyncFlags())      # planned while oauth resolves
        op = ct.new_op(env, m)

        os.unlink(os.path.join(env.home, ".claude.json"))   # oauth evidence gone
        assert ct.live_account(env) is None
        assert ct._identity_disagreement(env) is None        # genuinely no evidence

        assert ct.execute_sync_op(env, op) == "completed"
        assert os.path.exists(os.path.join(dst, "local_0.json"))


# ------------------------------- platform gate on the sync mutation routes


def test_sync_mutations_refuse_on_an_unverified_platform(two_account_env, tmp_path):
    """The gap this closes: `is_windows` was checked in exactly one place -
    inside plan_move - so every sync route (write, undo, recover --back) would
    happily mutate a store on macOS whose layout has never been verified.
    The guard now lives in _guard_mutation, which all three already call."""
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=1)
    env.is_windows = False

    # planning is read-only and must still work - it is what a Mac user runs
    # to produce the doctor/dry-run output we actually want from them.
    m = ct.plan_sync(env, ct.SyncFlags())
    assert len(m["rows"]) == 1

    for what in ("write to", "delete from"):
        with pytest.raises(ct.Refusal, match="Windows-only"):
            ct._guard_mutation(env, what)

    # and the executor itself refuses, before journaling anything
    with pytest.raises(ct.Refusal, match="Windows-only"):
        ct.run_sync(env, m)
    assert [f for f in os.listdir(dst) if f.startswith("local_")] == []


def test_platform_gate_has_no_override(two_account_env, tmp_path):
    """Deliberately no flag: an override would let a user waive a risk they
    have no way to evaluate, which inverts the fail-closed rule everywhere
    else in this module."""
    env, src, dst = two_account_env(tmp_path)
    env.is_windows = False
    assert not hasattr(ct.SyncFlags(), "unverified_platform")
    with pytest.raises(ct.Refusal, match="Windows-only"):
        ct._require_verified_platform(env, "write to")
    env.is_windows = True
    assert ct._require_verified_platform(env, "write to") is None   # no-op on Windows


# ------------------- the empty store that broke a working sync (August 2026)


EMPTY_ORG = "eeeeeeee-0000-0000-0000-000000000007"


def _empty_store_dir(env, account=DORMANT, org=EMPTY_ORG):
    """The store directory the desktop app created and never filled: one
    scheduled-tasks.json, zero listing rows. Observed on a real machine in
    August 2026 - it appeared under the SAME dormant account as the real
    290-file store, so the two candidates shared an account uuid and an email
    and differed only in an org-id prefix."""
    d = os.path.join(env.store_candidates[0], account, org)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "scheduled-tasks.json"), "w", encoding="utf-8") as fh:
        fh.write('{"tasks": []}')
    return d


def _line_containing(msg, needle):
    hits = [ln for ln in msg.splitlines() if needle in ln]
    assert len(hits) == 1, "expected one line holding {0!r}, got {1}".format(needle, hits)
    return hits[0]


def test_ambiguity_refusal_counts_the_listing_rows_in_each_candidate(
        two_account_env, tmp_path):
    """The regression this closes: an empty store directory turned a working,
    unambiguous sync into a refusal listing two candidates that printed
    identically apart from an org-id prefix. Nothing on either line said which
    one held the sessions, so the refusal was a wall rather than a choice."""
    env, src, dst = two_account_env(tmp_path)
    empty = _empty_store_dir(env)
    _row(dst, "local_a.json", "sid-a", "Alpha")
    _row(dst, "local_b.json", "sid-b", "Bravo")

    with pytest.raises(ct.Refusal) as exc_info:
        ct.resolve_sync_endpoints(env)
    msg = str(exc_info.value)
    assert "name one with --to" in msg
    assert "(2 rows)" in _line_containing(msg, dst)
    assert "(no listing rows)" in _line_containing(msg, empty)
    assert "still listed, not ruled out" in msg      # and says why it kept it

    # non-listing files must not be counted: the empty store's one file is a
    # scheduled-tasks.json, which is exactly what made it look real.
    assert "(1 row)" not in msg

    os.unlink(os.path.join(dst, "local_b.json"))
    with pytest.raises(ct.Refusal) as exc_info:
        ct.resolve_sync_endpoints(env)
    msg = str(exc_info.value)
    assert "(1 row)" in _line_containing(msg, dst)
    assert "(1 rows)" not in msg


def test_a_zero_row_candidate_is_listed_not_dropped(two_account_env, tmp_path):
    """Auto-excluding empty stores would have made this refusal go away, and
    would have been wrong: a store with no rows yet is a legitimate
    destination the moment its account/org pair is signed in to. So the count
    is evidence offered to the user, never a filter applied for them."""
    env, src, dst = two_account_env(tmp_path)
    empty = _empty_store_dir(env)

    # still ambiguous - the empty candidate counts as a candidate
    with pytest.raises(ct.Refusal, match="name one with --to"):
        ct.resolve_sync_endpoints(env)
    # ...and naming it works, which is the whole reason it stays listed
    source, dest = ct.resolve_sync_endpoints(env, to="eeeeeeee")
    assert os.path.normcase(source.path) == os.path.normcase(src)
    assert os.path.normcase(dest.path) == os.path.normcase(empty)


def test_the_to_ambiguity_refusal_counts_rows_too(two_account_env, tmp_path):
    """Same wall, same fix: --to naming the shared account matches both orgs,
    and 'be more specific' is useless without something to be specific about."""
    env, src, dst = two_account_env(tmp_path)
    empty = _empty_store_dir(env)
    _row(dst, "local_a.json", "sid-a", "Alpha")

    with pytest.raises(ct.Refusal) as exc_info:
        ct.resolve_sync_endpoints(env, to=DORMANT)
    msg = str(exc_info.value)
    assert "be more specific" in msg
    assert "(1 row)" in _line_containing(msg, dst)
    assert "(no listing rows)" in _line_containing(msg, empty)


def test_the_stores_found_listing_counts_rows_too(two_account_env, tmp_path):
    """The source-unidentifiable refusals list every store, the live one
    included, and are read for the same reason: which of these is my real
    store?"""
    env, src, dst = two_account_env(tmp_path)
    os.unlink(os.path.join(env.home, ".claude.json"))
    _row(src, "local_a.json", "sid-a", "Alpha")

    with pytest.raises(ct.Refusal) as exc_info:
        ct.resolve_sync_endpoints(env)
    msg = str(exc_info.value)
    assert "Stores found:" in msg
    assert "(1 row)" in _line_containing(msg, src)
    assert "(no listing rows)" in _line_containing(msg, dst)


def test_an_unreadable_candidate_is_never_reported_as_empty(
        two_account_env, tmp_path, monkeypatch):
    """'Couldn't look' is never 'nothing there' - and here the mistake would
    be worst: the store printed as empty is the one the user rules out, so an
    unreadable real store labelled '(no listing rows)' would aim them at the
    wrong destination. It must degrade to a refusal that says so, not to a
    LayoutError that loses the listing."""
    env, src, dst = two_account_env(tmp_path)
    empty = _empty_store_dir(env)
    _row(dst, "local_a.json", "sid-a", "Alpha")
    real_listdir = os.listdir
    blocked = os.path.normcase(dst)

    def boom(p):
        if os.path.normcase(str(p)) == blocked:
            raise PermissionError("no")
        return real_listdir(p)

    monkeypatch.setattr(os, "listdir", boom)
    with pytest.raises(ct.Refusal) as exc_info:
        ct.resolve_sync_endpoints(env)
    msg = str(exc_info.value)
    assert "name one with --to" in msg
    assert "(row count unreadable)" in _line_containing(msg, dst)
    assert "(no listing rows)" in _line_containing(msg, empty)


# ------------------------------------------------------ RULING 5: sync --live


SOURCE_ACCT = "aaaaaaaa-0000-0000-0000-000000000001"


class TestLiveOverride:
    """RULING 5: `--live` asserts which of the two DISAGREEING identity files
    to believe about the account the desktop app is signed into. Never a bare
    force flag: it must name the account (reusing --to's matching), works
    only while the files disagree, is journaled as a certification that is
    revalidated at every mutation the op ever performs, and leaves the
    RULING 4 running-app guard completely untouched."""

    def _e4(self, env):
        """The E4 shape: oauth says a..., the desktop's config says c...."""
        _write_desktop_config(env, DORMANT)

    # ------------------------------------------------------ usability gate

    def test_refused_when_files_agree(self, two_account_env, tmp_path):
        env, src, dst = two_account_env(tmp_path)
        _write_desktop_config(env, SOURCE_ACCT)
        with pytest.raises(ct.Refusal) as exc:
            ct.resolve_sync_endpoints(env, live="cccccccc")
        msg = str(exc.value)
        assert "do not currently disagree" in msg
        assert "aaaaaaaa" in msg           # names the account that resolves

    def test_refused_when_oauth_alone_resolves(self, two_account_env, tmp_path):
        env, src, dst = two_account_env(tmp_path)     # no config.json at all
        with pytest.raises(ct.Refusal, match="do not currently disagree"):
            ct.resolve_sync_endpoints(env, live="cccccccc")

    def test_refused_with_no_evidence_at_all(self, two_account_env, tmp_path):
        env, src, dst = two_account_env(tmp_path)
        os.unlink(os.path.join(env.home, ".claude.json"))
        with pytest.raises(ct.Refusal) as exc:
            ct.resolve_sync_endpoints(env, live="cccccccc")
        msg = str(exc.value)
        assert "do not currently disagree" in msg
        assert "cannot supply evidence" in msg    # not a lying "they agree"

    def test_refused_in_the_config_only_two_org_state(self, two_account_env,
                                                      tmp_path):
        # live_account() is None here WITHOUT a disagreement (config-only
        # evidence naming an account with two org dirs). The refusal must
        # describe that state, not claim an agreement that does not exist.
        env, src, dst = two_account_env(tmp_path)
        os.unlink(os.path.join(env.home, ".claude.json"))
        os.makedirs(os.path.join(env.store_candidates[0], SOURCE_ACCT,
                                 "zzzzzzzz-0000-0000-0000-000000000009"))
        _write_desktop_config(env, SOURCE_ACCT)
        assert ct.live_account(env) is None
        assert ct._identity_disagreement(env) is None
        with pytest.raises(ct.Refusal) as exc:
            ct.resolve_sync_endpoints(env, live="aaaaaaaa")
        msg = str(exc.value)
        assert "do not currently disagree" in msg
        assert "resolves to" not in msg

    def test_empty_or_whitespace_assertion_is_refused(self, two_account_env,
                                                      tmp_path):
        # substring containment would make "" or "  " match every candidate,
        # which on a one-candidate machine is the bare force flag this
        # design rejects.
        env, src, dst = two_account_env(tmp_path)
        self._e4(env)
        with pytest.raises(ct.Refusal, match="name the account"):
            ct.resolve_sync_endpoints(env, live="   ")

    # ------------------------------------------------------------ matching

    def test_asserting_the_config_named_account_resolves_endpoints(
            self, two_account_env, tmp_path):
        env, src, dst = two_account_env(tmp_path)
        self._e4(env)
        assert ct.live_account(env) is None       # the wall this flag opens
        source, dest = ct.resolve_sync_endpoints(env, live="cccccccc")
        assert source.account_uuid == DORMANT
        assert source.resolved_from == "user"
        assert os.path.normcase(source.path) == os.path.normcase(dst)
        assert os.path.normcase(dest.path) == os.path.normcase(src)

    def test_matching_by_recovered_email_works(self, two_account_env, tmp_path):
        env, src, dst = two_account_env(tmp_path)
        self._e4(env)
        _agent_mode_config(env, DORMANT, {"accountUuid": DORMANT,
                                          "emailAddress": "other@example.com"})
        source, dest = ct.resolve_sync_endpoints(env, live="other@example.com")
        assert source.account_uuid == DORMANT
        assert source.email == "other@example.com"

    def test_ambiguous_assertion_lists_candidates(self, two_account_env, tmp_path):
        env, src, dst = two_account_env(tmp_path)
        self._e4(env)
        second = os.path.join(env.store_candidates[0], DORMANT,
                              "eeeeeeee-0000-0000-0000-000000000007")
        os.makedirs(second)
        _row(dst, "local_a.json", "sid-a", "Alpha")
        with pytest.raises(ct.Refusal) as exc:
            ct.resolve_sync_endpoints(env, live=DORMANT)
        msg = str(exc.value)
        assert "be more specific" in msg
        assert "(1 row)" in _line_containing(msg, dst)
        assert "(no listing rows)" in _line_containing(msg, second)
        # ...and an org substring settles it, exactly like --to
        source, _ = ct.resolve_sync_endpoints(env, live="dddddddd")
        assert os.path.normcase(source.path) == os.path.normcase(dst)

    def test_assertion_matching_neither_named_account_refuses(
            self, two_account_env, tmp_path):
        env, src, dst = two_account_env(tmp_path)
        self._e4(env)
        third = os.path.join(env.store_candidates[0],
                             "eeeeeeee-0000-0000-0000-000000000005",
                             "ffffffff-0000-0000-0000-000000000006")
        os.makedirs(third)
        with pytest.raises(ct.Refusal) as exc:
            ct.resolve_sync_endpoints(env, live="eeeeeeee")
        msg = str(exc.value)
        assert "matched no store belonging to either account" in msg
        assert "aaaaaaaa" in msg and "cccccccc" in msg
        assert "something else" in msg
        # a substring matching nothing at all gets the same refusal
        with pytest.raises(ct.Refusal, match="matched no store"):
            ct.resolve_sync_endpoints(env, live="zzzz-nope")

    def test_a_named_account_with_no_store_is_called_out(self, two_account_env,
                                                         tmp_path):
        # config names an account with no store dir on disk. Asserting it can
        # never match, and the refusal must say WHY rather than imply a typo.
        env, src, dst = two_account_env(tmp_path)
        _write_desktop_config(env, "99999999-0000-0000-0000-000000000099")
        assert ct._identity_disagreement(env) is not None
        with pytest.raises(ct.Refusal) as exc:
            ct.resolve_sync_endpoints(env, live="99999999")
        assert "no store on disk" in str(exc.value)

    # -------------------------------------------------- the manifest record

    def test_manifest_records_the_certification_both_directions(
            self, two_account_env, tmp_path):
        env, src, dst = two_account_env(tmp_path)
        self._e4(env)
        cfg = os.path.join(os.path.dirname(env.store_candidates[0]),
                           "config.json")

        m = ct.plan_sync(env, ct.SyncFlags(live="cccccccc"))
        assert m["source_resolved_from"] == "user"
        ov = m["live_override"]
        assert ov["account"] == DORMANT
        assert ov["pair"] == [SOURCE_ACCT, DORMANT]     # ordered: oauth, config
        assert ov["overrode_file"] == "~/.claude.json"
        assert ov["overrode_uuid"] == SOURCE_ACCT
        assert os.path.normcase(ov["config_path"]) == os.path.normcase(cfg)

        m2 = ct.plan_sync(env, ct.SyncFlags(live="aaaaaaaa"))
        ov2 = m2["live_override"]
        assert ov2["account"] == SOURCE_ACCT
        assert ov2["pair"] == [SOURCE_ACCT, DORMANT]
        assert ov2["overrode_file"] == "config.json"
        assert ov2["overrode_uuid"] == DORMANT
        assert os.path.normcase(ov2["config_path"]) == os.path.normcase(cfg)

    def test_ordinary_plan_carries_no_override_key(self, two_account_env, tmp_path):
        env, src, dst = two_account_env(tmp_path)
        _prepared(env, src, dst, n=1)
        m = ct.plan_sync(env, ct.SyncFlags())
        assert "live_override" not in m

    # ------------------------------------- the apply path (E4, end to end)

    def _c_row(self, env, dst):
        sid = "11111111-2222-3333-4444-555555555555"
        _row(dst, "local_c1.json", sid, "From c")
        _transcript(env, sid)

    def test_apply_writes_the_store_the_stale_file_named(self, two_account_env,
                                                         tmp_path):
        # oauth (stale) says a is live; the user certifies c. The sync must
        # write into a's store - the very account oauth wrongly calls live,
        # i.e. the account a file says is NOT dormant.
        env, src, dst = two_account_env(tmp_path)
        self._c_row(env, dst)
        self._e4(env)
        m = ct.plan_sync(env, ct.SyncFlags(live="cccccccc"))
        assert len(m["rows"]) == 1
        assert ct.run_sync(env, m) == "completed"
        assert os.path.exists(os.path.join(src, "local_c1.json"))

    def test_ruling4_guard_is_untouched_by_live(self, two_account_env, tmp_path):
        env, src, dst = two_account_env(tmp_path)
        self._c_row(env, dst)
        self._e4(env)
        m = ct.plan_sync(env, ct.SyncFlags(live="cccccccc"))
        n_ops = len(ct.list_ops(env))
        env.process_lister = lambda: [(99999, _DESKTOP_EXE)]
        with pytest.raises(ct.Refusal, match="desktop app appears to be running"):
            ct.run_sync(env, m)
        assert not os.path.exists(os.path.join(src, "local_c1.json"))
        assert len(ct.list_ops(env)) == n_ops          # nothing journaled
        # the unreadable-process-list sentinel refuses too
        env.process_lister = lambda: [(-1, ct._PROC_UNAVAILABLE)]
        with pytest.raises(ct.Refusal, match="could not be read"):
            ct.run_sync(env, m)

    def test_guard_disagreement_note_says_live_does_not_lift_it(
            self, two_account_env, tmp_path):
        env, src, dst = two_account_env(tmp_path)
        self._e4(env)
        env.process_lister = lambda: [(99999, _DESKTOP_EXE)]
        with pytest.raises(ct.Refusal) as exc:
            ct._guard_mutation(env, "write to")
        assert "--live does not lift this guard" in str(exc.value)

    def test_disagreement_refusal_advertises_live(self, two_account_env, tmp_path):
        env, src, dst = two_account_env(tmp_path)
        self._e4(env)
        with pytest.raises(ct.Refusal) as exc:
            ct.resolve_sync_endpoints(env)
        assert "--live" in str(exc.value)

    # ------------------------- lifecycle: undo / recover under certification

    def _completed_live_sync(self, env, src, dst):
        self._c_row(env, dst)
        self._e4(env)
        m = ct.plan_sync(env, ct.SyncFlags(live="cccccccc"))
        assert ct.run_sync(env, m) == "completed"
        assert os.path.exists(os.path.join(src, "local_c1.json"))
        return ct.list_ops(env)[-1]

    def test_undo_honors_the_persisting_certification(self, two_account_env,
                                                      tmp_path):
        # Without the certification honored on the delete side too, undo of a
        # --live sync would refuse (the disagreement names the destination)
        # and the sync would be irreversible in exactly the state the flag
        # exists to handle.
        env, src, dst = two_account_env(tmp_path)
        op = self._completed_live_sync(env, src, dst)
        assert ct.undo_sync(env, op) == "undone"
        assert not os.path.exists(os.path.join(src, "local_c1.json"))

    def test_undo_refuses_when_the_pair_direction_flipped(self, two_account_env,
                                                          tmp_path):
        # oauth and config have BOTH re-authenticated since, swapping claims:
        # the world moved twice, and a twice-moved world is not the tie the
        # user arbitrated. void -> refuse.
        env, src, dst = two_account_env(tmp_path)
        op = self._completed_live_sync(env, src, dst)
        with open(os.path.join(env.home, ".claude.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"oauthAccount": {
                "accountUuid": DORMANT,
                "organizationUuid": "dddddddd-0000-0000-0000-000000000004",
                "emailAddress": "other@example.com"}}, fh)
        _write_desktop_config(env, SOURCE_ACCT)
        assert ct._identity_disagreement(env) == (DORMANT, SOURCE_ACCT)
        with pytest.raises(ct.Refusal, match="--live"):
            ct.undo_sync(env, op)
        assert os.path.exists(os.path.join(src, "local_c1.json"))   # untouched

    def test_undo_refuses_when_the_disagreement_was_replaced(
            self, two_account_env, tmp_path):
        env, src, dst = two_account_env(tmp_path)
        op = self._completed_live_sync(env, src, dst)
        _write_desktop_config(env, "99999999-0000-0000-0000-000000000099")
        with pytest.raises(ct.Refusal, match="--live"):
            ct.undo_sync(env, op)
        assert os.path.exists(os.path.join(src, "local_c1.json"))

    def test_undo_refuses_when_files_agree_on_the_destination(
            self, two_account_env, tmp_path):
        # The user signed everything into the destination account: deleting
        # rows out of the now-live store is exactly what this tool never
        # does. The live-store protection, not the override machinery.
        env, src, dst = two_account_env(tmp_path)
        op = self._completed_live_sync(env, src, dst)
        with open(os.path.join(env.home, ".claude.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"oauthAccount": {
                "accountUuid": SOURCE_ACCT,
                "organizationUuid": "bbbbbbbb-0000-0000-0000-000000000002",
                "emailAddress": "me@example.com"}}, fh)
        _write_desktop_config(env, SOURCE_ACCT)
        with pytest.raises(ct.Refusal, match="LIVE account"):
            ct.undo_sync(env, op)
        assert os.path.exists(os.path.join(src, "local_c1.json"))

    def test_undo_proceeds_when_files_agree_on_the_source(self, two_account_env,
                                                          tmp_path):
        # The world resolved in the certified direction (files agree on c):
        # the certification is moot and ordinary rules bless the undo.
        env, src, dst = two_account_env(tmp_path)
        op = self._completed_live_sync(env, src, dst)
        with open(os.path.join(env.home, ".claude.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"oauthAccount": {
                "accountUuid": DORMANT,
                "organizationUuid": "dddddddd-0000-0000-0000-000000000004",
                "emailAddress": "other@example.com"}}, fh)
        assert ct._identity_disagreement(env) is None    # config still says c
        live = ct.live_account(env)
        assert live is not None and live.account_uuid == DORMANT
        assert ct.undo_sync(env, op) == "undone"

    def test_undo_proceeds_on_single_file_resolution(self, two_account_env,
                                                     tmp_path):
        # One identity file vanishing voids the certification, but the
        # surviving file resolves the source alone - the evidence level
        # every uncertified sync already plans and executes on - so ordinary
        # rules apply and undo proceeds. Accepted behavior, not an oversight.
        env, src, dst = two_account_env(tmp_path)
        op = self._completed_live_sync(env, src, dst)
        os.unlink(os.path.join(env.home, ".claude.json"))  # oauth claim gone
        live = ct.live_account(env)                # config alone resolves c
        assert live is not None and live.resolved_from == "config"
        assert ct.undo_sync(env, op) == "undone"

    def test_void_certification_refuses_where_uncertified_would_proceed(
            self, two_account_env, tmp_path):
        # Contrast with the pinned no-evidence-proceeds test: an op that
        # exists only because of a certification never executes in a state
        # where the files can neither validate the assertion nor resolve an
        # account at all.
        env, src, dst = two_account_env(tmp_path)
        self._c_row(env, dst)
        self._e4(env)
        m = ct.plan_sync(env, ct.SyncFlags(live="cccccccc"))
        op = ct.new_op(env, m)                 # journaled, not yet executed
        os.unlink(os.path.join(env.home, ".claude.json"))
        os.unlink(os.path.join(os.path.dirname(env.store_candidates[0]),
                               "config.json"))     # no identity evidence left
        assert ct.live_account(env) is None
        assert ct._identity_disagreement(env) is None
        with pytest.raises(ct.Refusal, match="--live"):
            ct.execute_sync_op(env, op)
        assert not os.path.exists(os.path.join(src, "local_c1.json"))
        assert op.manifest["status"] == "journaled"

    def _crashed_live_sync(self, env, src, dst):
        for i in range(2):
            sid = "sid-%d" % i
            _row(dst, "local_%d.json" % i, sid, "Session %d" % i)
            _transcript(env, sid)
        self._e4(env)
        m = ct.plan_sync(env, ct.SyncFlags(live="cccccccc"))

        def hook(point):
            if point == "sync-mid-write":
                raise SimulatedCrash()
        ct._crash_hook = hook
        try:
            with pytest.raises(SimulatedCrash):
                ct.run_sync(env, m)
        finally:
            ct._crash_hook = None
        return ct.nonterminal_ops(env)[0]

    def test_recover_forward_finishes_a_crashed_live_sync(self, two_account_env,
                                                          tmp_path):
        env, src, dst = two_account_env(tmp_path)
        op = self._crashed_live_sync(env, src, dst)
        assert ct.recover_op(env, op, "forward") == "completed"
        assert os.path.exists(os.path.join(src, "local_0.json"))
        assert os.path.exists(os.path.join(src, "local_1.json"))

    def test_recover_back_removes_what_a_stuck_live_sync_wrote(
            self, two_account_env, tmp_path):
        env, src, dst = two_account_env(tmp_path)
        op = self._crashed_live_sync(env, src, dst)
        assert ct.recover_op(env, op, "back") == "rolled_back"
        assert not os.path.exists(os.path.join(src, "local_0.json"))

    def test_classify_notes_a_void_certification(self, two_account_env, tmp_path):
        env, src, dst = two_account_env(tmp_path)
        op = self._crashed_live_sync(env, src, dst)
        _write_desktop_config(env, "99999999-0000-0000-0000-000000000099")
        c = ct.classify_sync_op(env, op)
        assert "--live" in c["note"]
        # ...and a VALID certification adds no such noise
        _write_desktop_config(env, DORMANT)
        c = ct.classify_sync_op(env, op)
        assert "--live" not in c["note"]

    # ------------------------------------------------ hand-edited manifests

    def test_grafted_inconsistent_override_never_certifies(self, two_account_env,
                                                           tmp_path):
        # An op journaled WITHOUT --live gets a record grafted on whose
        # `account` is not the manifest's own source_account: void, refuse.
        env, src, dst = two_account_env(tmp_path)
        _prepared(env, src, dst, n=1)
        m = ct.plan_sync(env, ct.SyncFlags())      # planned while oauth resolves
        op = ct.new_op(env, m)
        op.manifest["live_override"] = {
            "account": DORMANT, "pair": [SOURCE_ACCT, DORMANT],
            "overrode_file": "~/.claude.json", "overrode_uuid": SOURCE_ACCT,
            "config_path": ""}
        ct.save_manifest(op)
        self._e4(env)                    # a real a-vs-c disagreement appears
        with pytest.raises(ct.Refusal, match="--live"):
            ct.execute_sync_op(env, op)
        assert not os.path.exists(os.path.join(dst, "local_0.json"))

    def test_wrong_but_well_typed_audit_fields_void_the_record(
            self, two_account_env, tmp_path):
        # Operative fields all consistent, but overrode_uuid names the
        # asserted member instead of the overridden one: the record's audit
        # story contradicts its operative story - void, refuse.
        env, src, dst = two_account_env(tmp_path)
        _prepared(env, src, dst, n=1)
        m = ct.plan_sync(env, ct.SyncFlags())
        op = ct.new_op(env, m)
        op.manifest["live_override"] = {
            "account": SOURCE_ACCT, "pair": [SOURCE_ACCT, DORMANT],
            "overrode_file": "config.json", "overrode_uuid": SOURCE_ACCT,
            "config_path": ""}
        ct.save_manifest(op)
        self._e4(env)
        with pytest.raises(ct.Refusal, match="--live"):
            ct.execute_sync_op(env, op)

    def test_a_fully_consistent_record_certifies_by_design(self, two_account_env,
                                                           tmp_path):
        # The accepted posture, stated in the ruling: a record that names the
        # real, current disagreement in the current direction AND matches the
        # manifest's own source unlocks exactly what the user could have
        # authorized at the command line - nothing more, and RULING 4 still
        # binds it.
        env, src, dst = two_account_env(tmp_path)
        _prepared(env, src, dst, n=1)
        m = ct.plan_sync(env, ct.SyncFlags())
        op = ct.new_op(env, m)
        op.manifest["live_override"] = {
            "account": SOURCE_ACCT, "pair": [SOURCE_ACCT, DORMANT],
            "overrode_file": "config.json", "overrode_uuid": DORMANT,
            "config_path": ""}
        ct.save_manifest(op)
        self._e4(env)
        assert ct.execute_sync_op(env, op) == "completed"
        assert os.path.exists(os.path.join(dst, "local_0.json"))

    def test_garbage_override_key_is_void_not_absent(self, two_account_env,
                                                     tmp_path):
        # A third-account destination under a disagreement PROCEEDS for an
        # uncertified op (pinned elsewhere). A present-but-garbage
        # live_override must therefore refuse - treating garbage as "absent"
        # would fail open through that exact proceed path.
        env, src, dst = two_account_env(tmp_path)
        third = os.path.join(env.store_candidates[0],
                             "eeeeeeee-0000-0000-0000-000000000005",
                             "ffffffff-0000-0000-0000-000000000006")
        os.makedirs(third)
        _row(src, "local_0.json", "sid-0", "Session 0")
        _transcript(env, "sid-0")
        m = ct.plan_sync(env, ct.SyncFlags(to="eeeeeeee"))
        op = ct.new_op(env, m)
        op.manifest["live_override"] = ["garbage"]
        ct.save_manifest(op)
        self._e4(env)
        with pytest.raises(ct.Refusal, match="--live"):
            ct.execute_sync_op(env, op)
        assert not os.path.exists(os.path.join(third, "local_0.json"))

    def test_malformed_override_shapes_refuse_never_traceback(
            self, two_account_env, tmp_path):
        env, src, dst = two_account_env(tmp_path)
        self._c_row(env, dst)
        self._e4(env)
        m = ct.plan_sync(env, ct.SyncFlags(live="cccccccc"))
        op = ct.new_op(env, m)
        good = op.manifest["live_override"]
        for garbage in ("x", 42, [], {}, {"account": 7},
                        {"account": DORMANT, "pair": "not-a-list"},
                        dict(good, pair=list(reversed(good["pair"])))):
            op.manifest["live_override"] = garbage
            with pytest.raises(ct.Refusal, match="--live"):
                ct.execute_sync_op(env, op)
            assert op.manifest["status"] == "journaled"

    # ------------------------------------------------------------ loudness

    def test_dry_run_report_shouts_the_override(self, two_account_env, tmp_path):
        env, src, dst = two_account_env(tmp_path)
        self._e4(env)
        m = ct.plan_sync(env, ct.SyncFlags(live="cccccccc"))
        lines = []
        ct._print_sync_report(lines.append, m)
        text = "\n".join(lines)
        assert "LIVE-ACCOUNT OVERRIDE" in text
        assert "--live" in text
        assert "overriding ~/.claude.json" in text
        assert "aaaaaaaa" in text                  # the stale claim's uuid
        assert "RULING 4" in text                  # the guard warning stays

    def test_report_derives_the_banner_from_pair_and_account(
            self, two_account_env, tmp_path):
        # Print-time derivation: a hand-edited overrode_file cannot make the
        # banner lie (validation would refuse execution anyway; the report
        # must not lie either, e.g. on a dry run over a doctored manifest).
        env, src, dst = two_account_env(tmp_path)
        self._e4(env)
        m = ct.plan_sync(env, ct.SyncFlags(live="cccccccc"))
        m["live_override"]["overrode_file"] = "config.json"          # a lie
        lines = []
        ct._print_sync_report(lines.append, m)
        assert "overriding ~/.claude.json" in "\n".join(lines)   # derived truth

    def test_apply_epilogue_names_the_override(self, two_account_env, tmp_path,
                                               capsys, monkeypatch):
        env, src, dst = two_account_env(tmp_path)
        self._c_row(env, dst)
        self._e4(env)
        monkeypatch.setattr(ct, "default_env", lambda: env)
        assert ct.main(["sync", "--live", "cccccccc", "--apply"]) == 0
        out = capsys.readouterr().out
        assert "LIVE-ACCOUNT OVERRIDE" in out
        assert "live-account override used" in out
        assert "copied" in out

    def test_json_apply_banners_to_stderr_and_records_in_json(
            self, two_account_env, tmp_path, capsys, monkeypatch):
        # --json prints no report and executes first; the override must
        # still be shouted BEFORE mutation - on stderr, keeping stdout pure
        # JSON for the machine reader.
        env, src, dst = two_account_env(tmp_path)
        self._c_row(env, dst)
        self._e4(env)
        monkeypatch.setattr(ct, "default_env", lambda: env)
        assert ct.main(["sync", "--live", "cccccccc", "--apply", "--json"]) == 0
        captured = capsys.readouterr()
        assert "LIVE-ACCOUNT OVERRIDE" in captured.err
        data = json.loads(captured.out)
        assert data["live_override"]["account"] == DORMANT
        assert data["result"] == "completed"

    def test_undo_prints_the_note_in_preview_and_before_apply(
            self, two_account_env, tmp_path, capsys, monkeypatch):
        env, src, dst = two_account_env(tmp_path)
        self._completed_live_sync(env, src, dst)
        monkeypatch.setattr(ct, "default_env", lambda: env)
        assert ct.main(["undo"]) == 0
        out = capsys.readouterr().out
        assert "--live" in out                               # preview warns
        assert ct.main(["undo", "--apply"]) == 0
        out = capsys.readouterr().out
        assert "--live" in out
        assert out.index("--live") < out.index("result:")    # before mutation
        assert not os.path.exists(os.path.join(src, "local_c1.json"))

    def test_recover_prints_the_note_dry_and_applied(self, two_account_env,
                                                     tmp_path, capsys,
                                                     monkeypatch):
        env, src, dst = two_account_env(tmp_path)
        op = self._crashed_live_sync(env, src, dst)
        op_id = op.manifest["op_id"]
        monkeypatch.setattr(ct, "default_env", lambda: env)
        assert ct.main(["recover", "--resolve", op_id, "--forward"]) == 0
        out = capsys.readouterr().out
        assert "--live" in out
        assert "would resolve" in out
        assert ct.main(["recover", "--resolve", op_id, "--forward",
                        "--apply"]) == 0
        out = capsys.readouterr().out
        assert "--live" in out
        assert out.index("--live") < out.index("result:")
        assert os.path.exists(os.path.join(src, "local_1.json"))

    # -------------------------------------------------------- parser/flags

    def test_parser_wiring(self, capsys):
        p = ct.build_parser()
        assert p.parse_args(["sync", "--live", "x"]).live == "x"
        assert p.parse_args(["sync"]).live == ""
        for argv in (["undo", "--live", "x"], ["recover", "--live", "x"]):
            with pytest.raises(SystemExit):
                p.parse_args(argv)
        capsys.readouterr()          # swallow argparse usage noise
