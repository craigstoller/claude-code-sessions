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


def test_pair_form_resolves_sync_to(two_account_env, tmp_path):
    """The tool labels every account `email (acct/org)`, and 0.12.0 accepted
    no form of that label back - the '/' matched nothing in any matcher. The
    pair form is PARSED (two anchored prefixes), never substring-searched."""
    env, src, dst = two_account_env(tmp_path)
    os.makedirs(os.path.join(env.store_candidates[0],
                             "eeeeeeee-0000-0000-0000-000000000005",
                             "ffffffff-0000-0000-0000-000000000006"))
    source, dest = ct.resolve_sync_endpoints(env, to="cccccccc/dddddddd")
    assert os.path.normcase(dest.path) == os.path.normcase(dst)
    # The full printed uuids work too.
    _, dest = ct.resolve_sync_endpoints(
        env, to="cccccccc-0000-0000-0000-000000000003/"
                "dddddddd-0000-0000-0000-000000000004")
    assert os.path.normcase(dest.path) == os.path.normcase(dst)
    # A pair whose left half is a MID-uuid fragment is anchored out - and a
    # pair matching nothing gets the ordinary no-match refusal, never a
    # silent fall back into substring semantics.
    with pytest.raises(ct.Refusal, match="matched no other account store"):
        ct.resolve_sync_endpoints(env, to="0000-0000/dddddddd")


def test_to_ambiguity_refusal_offers_the_pair_form(two_account_env, tmp_path):
    """The 'be more specific' refusal now names the one form every report
    already prints, so the retry can be a copy-paste of a label."""
    env, src, dst = two_account_env(tmp_path)
    second_dst_org = os.path.join(env.store_candidates[0],
                                  "cccccccc-0000-0000-0000-000000000003",
                                  "eeeeeeee-0000-0000-0000-000000000007")
    os.makedirs(second_dst_org)
    with pytest.raises(ct.Refusal) as exc_info:
        ct.resolve_sync_endpoints(env, to="cccccccc")
    assert "printed account form works too" in str(exc_info.value)
    # ...and the offered form actually settles the ambiguity it appears in.
    _, dest = ct.resolve_sync_endpoints(env, to="cccccccc/eeeeeeee")
    assert os.path.normcase(dest.path) == os.path.normcase(second_dst_org)


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
    one session carries TWO tombstones - one for its cliSessionId and one
    for its filename stem, i.e. its local id. A skip that only checks the
    session id therefore misses
    a real deletion and resurrects the session."""
    env, src, dst = two_account_env(tmp_path)
    _row(src, "local_c0ffee02.json", "c0ffee01", "ACME tombstone test")
    _transcript(env, "c0ffee01")
    # ONLY the local-id tombstone - the session-id one is absent, which is
    # the case the old `sid in tombs` test could not see.
    with open(os.path.join(dst, "deleted_c0ffee02"), "w") as fh:
        fh.write("1785541024931")
    source, dest = ct.resolve_sync_endpoints(env)
    picked, tally = ct.select_sync_rows(env, source, dest, ct.SyncFlags())
    assert picked == []
    assert tally["deleted"] == ["ACME tombstone test"]

    # ...and --include-deleted can still name it, by either id.
    picked, tally = ct.select_sync_rows(
        env, source, dest, ct.SyncFlags(include_deleted=("c0ffee02",)))
    assert [r["title"] for r in picked] == ["ACME tombstone test"]
    assert tally["resurrected"] == ["ACME tombstone test"]
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


def test_a_title_that_is_not_text_reads_as_untitled(two_account_env, tmp_path,
                                                     monkeypatch, capsys):
    """A row whose title is a number or a list reads as untitled, as title_key
    compares it: --only passes over it, and the dry run around it still runs."""
    env, src, dst = two_account_env(tmp_path)
    _row(src, "local_a.json", "sid-a", "Alpha"); _transcript(env, "sid-a")
    _row(src, "local_b.json", "sid-b", 42); _transcript(env, "sid-b")
    _row(src, "local_c.json", "sid-c", ["Alpha"]); _transcript(env, "sid-c")
    source, dest = ct.resolve_sync_endpoints(env)
    picked, tally = ct.select_sync_rows(env, source, dest, ct.SyncFlags(only="alph"))
    assert [p["title"] for p in picked] == ["Alpha"]
    assert tally["filtered"] == ["(untitled)", "(untitled)"]
    m = ct.plan_sync(env, ct.SyncFlags())
    assert sorted(r["title"] for r in m["rows"]) == ["(untitled)", "(untitled)", "Alpha"]
    monkeypatch.setattr(ct, "default_env", lambda: env)
    for argv in (["sync", "--only", "alph"], ["sync", "--verbose"]):
        assert ct.main(argv) == 0
        assert "dry run" in capsys.readouterr().out.lower()


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
    # Since the 2026-10-19 journal ruling the resolution gate refuses it first,
    # before the row is read: a written row outside every discovered store is
    # one this version does not read. A Refusal (exit 1) now, where the
    # containment check's LayoutError (exit 2) used to answer.
    with pytest.raises(ct.Refusal, match="not one this version reads"):
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
    # The pending row is outside every discovered store, so forward - which
    # could only fail on it, every time - is no longer offered (the 2026-10-19
    # journal ruling's resolution gate); back still is.
    assert ct.classify_op(env, op)["resolutions"] == ["back"]
    with pytest.raises(ct.Refusal, match="not a safe resolution"):
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

    def test_sync_live_under_agreement_still_refused(self, two_account_env,
                                                     tmp_path):
        """0.13.0's corroboration (3b) is converge-only: under agreement
        sync's source is resolved by the files and the flag selects
        nothing, so every form - the exact live pair, a sibling-org pair,
        the bare email - keeps today's refusal. This is the boundary the
        design's round 3 drew by deleting the sync branch outright."""
        env, src, dst = two_account_env(tmp_path)
        _write_desktop_config(env, SOURCE_ACCT)          # agreement
        os.makedirs(os.path.join(env.store_candidates[0], SOURCE_ACCT,
                                 "eeeeeeee-0000-0000-0000-000000000007"))
        exact = SOURCE_ACCT + "/bbbbbbbb-0000-0000-0000-000000000002"
        for q in (exact, "aaaaaaaa/eeeeeeee", "me@example.com"):
            with pytest.raises(ct.Refusal) as exc:
                ct.resolve_sync_endpoints(env, live=q)
            msg = str(exc.value)
            assert "do not currently disagree" in msg
            assert "Re-run without --live" in msg

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

    def test_pair_form_resolves_live(self, two_account_env, tmp_path):
        """sync --live accepts the printed account form at store scope: the
        pair is parsed into two anchored prefixes, exactly as --to parses
        it, and the resolved Account keeps its concrete org and path -
        sync's source is the resolved STORE, so the org half stays
        load-bearing there."""
        env, src, dst = two_account_env(tmp_path)
        self._e4(env)
        source, dest = ct.resolve_sync_endpoints(env,
                                                 live="cccccccc/dddddddd")
        assert source.account_uuid == DORMANT
        assert source.resolved_from == "user"
        assert os.path.normcase(source.path) == os.path.normcase(dst)

    def test_email_live_still_store_strict_for_sync(self, two_account_env,
                                                    tmp_path):
        """Account-scope resolution is converge's (0.13.0 change 3a); sync
        keeps the store-strict default, because the resolved store is the
        SOURCE sync reads - there the org half is load-bearing. The same
        email converge accepts still refuses here, with the N-stores
        listing now carrying the pair-form hint."""
        env, src, dst = two_account_env(tmp_path)
        self._e4(env)
        for org in ("eeeeeeee-0000-0000-0000-000000000007",
                    "ffffffff-0000-0000-0000-000000000008"):
            os.makedirs(os.path.join(env.store_candidates[0], SOURCE_ACCT,
                                     org))
        with pytest.raises(ct.Refusal) as exc:
            ct.resolve_sync_endpoints(env, live="me@example.com")
        msg = str(exc.value)
        assert "matched 3 stores" in msg
        assert "be more specific" in msg
        assert "printed account form works too" in msg

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
        assert "printed account form works too" in msg
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

    def test_guard_disagreement_note_caveats_the_desktop_switch(
            self, two_account_env, tmp_path):
        """Change 4's one-clause caveat reaches the guard's note too: a
        desktop switch may not refresh config.json - measured tracking one
        (E4, 2026-08-02) and keeping the previous account across one
        (2026-08-29) - so "so the two agree" must not read as a promise."""
        env, src, dst = two_account_env(tmp_path)
        self._e4(env)
        env.process_lister = lambda: [(99999, _DESKTOP_EXE)]
        with pytest.raises(ct.Refusal) as exc:
            ct._guard_mutation(env, "write to")
        msg = str(exc.value)
        assert "may not refresh config.json" in msg
        assert "--live does not lift this guard" in msg

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


# ------------------------------------------------ 0.15.1 (E, part 2): dup reporting

def test_plan_sync_annotates_rows_the_destination_already_opens_or_names(
        two_account_env, tmp_path):
    """Sync counts ROW FILES the destination lacks; converge counts
    CONVERSATIONS it cannot open. A multi-account store accumulates different
    row filenames for the same conversation, so a "to copy" row can add a
    second row for a conversation that sidebar already opens - and a second
    row under a title already naming one there. Additive reporting only:
    each add carries `dup_conversation` (the destination has a row opening
    the same cliSessionId) and `dup_title` (a destination row's title_key
    matches), and the tally lists both, so the window can warn before the
    bulk apply that drove `distinguishable` from 0 to ~78 on a real store.
    Nothing about what sync writes or refuses changes."""
    env, src, dst = two_account_env(tmp_path)
    # Alpha: the destination opens the SAME conversation under another
    # row file (a different local id) - and under a different title.
    _row(src, "local_a.json", "sid-a", "Alpha"); _transcript(env, "sid-a")
    _row(dst, "local_a2.json", "sid-a", "Alpha (their name for it)")
    # Bravo: the destination already has a row NAMED Bravo - trimmed exact,
    # title_key's comparator - for a different conversation.
    _row(src, "local_b.json", "sid-b", "Bravo"); _transcript(env, "sid-b")
    _row(dst, "local_other.json", "sid-z", "  Bravo ")
    # Charlie: genuinely new to that sidebar.
    _row(src, "local_c.json", "sid-c", "Charlie"); _transcript(env, "sid-c")
    # A case-different title is NOT a duplicate by title_key.
    _row(src, "local_d.json", "sid-d", "Delta"); _transcript(env, "sid-d")
    _row(dst, "local_dd.json", "sid-y", "delta")

    m = ct.plan_sync(env, ct.SyncFlags())
    by = {r["title"]: r for r in m["rows"]}
    assert set(by) == {"Alpha", "Bravo", "Charlie", "Delta"}
    assert by["Alpha"]["dup_conversation"] is True
    assert by["Alpha"]["dup_title"] is False
    assert by["Bravo"]["dup_conversation"] is False
    assert by["Bravo"]["dup_title"] is True
    assert by["Charlie"]["dup_conversation"] is False
    assert by["Charlie"]["dup_title"] is False
    assert by["Delta"]["dup_conversation"] is False
    assert by["Delta"]["dup_title"] is False
    assert m["tally"]["dup_conversation"] == ["Alpha"]
    assert m["tally"]["dup_title"] == ["Bravo"]
    # The plan still copies every one of them - reporting, not refusal.
    assert all(not r["is_update"] for r in m["rows"])


def test_plan_sync_dup_flags_are_reporting_only(two_account_env, tmp_path):
    """The flags change nothing about what sync writes: the same rows land,
    with the same bytes, and an unreadable destination row is skipped by
    the census rather than failing the plan. Refresh rows (--update) are
    not assessed - a refresh duplicates its own conversation by definition
    - so they carry neither key."""
    env, src, dst = two_account_env(tmp_path)
    _row(src, "local_a.json", "sid-a", "Alpha"); _transcript(env, "sid-a")
    _row(dst, "local_a2.json", "sid-a", "Alpha")          # both flags
    _row(src, "local_b.json", "sid-b", "Bravo"); _transcript(env, "sid-b")
    _row(dst, "local_b.json", "sid-b", "Bravo", {"lastActivityAt": 0})
    with open(os.path.join(dst, "local_junk.json"), "w") as fh:
        fh.write("{not json")
    m = ct.plan_sync(env, ct.SyncFlags(update=True))
    by = {r["title"]: r for r in m["rows"]}
    assert by["Alpha"]["dup_conversation"] is True
    assert by["Alpha"]["dup_title"] is True
    assert by["Bravo"]["is_update"] is True
    assert "dup_conversation" not in by["Bravo"]
    assert "dup_title" not in by["Bravo"]
    assert m["tally"]["dup_conversation"] == ["Alpha"]
    assert m["tally"]["dup_title"] == ["Alpha"]
    assert ct.run_sync(env, m) == "completed"
    assert sorted(f for f in os.listdir(dst) if f.startswith("local_")) == [
        "local_a.json", "local_a2.json", "local_b.json", "local_junk.json"]
    # The two flags are schema, never data, for the anonymizer - a title
    # equal to one must not rename the key.
    assert "dup_conversation" in ct._ANON_STRUCTURAL_KEYS
    assert "dup_title" in ct._ANON_STRUCTURAL_KEYS


# ---------------------------------------------- separator folding (2026-10-20)
#
# The maintainer's ruling (docs/internals.md, "Amendment 2026-10-20 -
# separators"): --to and store-scope --live fold '/' and '\' in the path field,
# beside today's test; a trailing separator may match the end of a store path,
# except on a value that is the printed account form without it; a --live value
# made only of separators is refused like a blank one. Each value is built in
# the separator this platform does NOT use - '/' on Windows, '\' on POSIX - so
# both CI legs exercise the fold.

OTHER_SEP = "/" if os.sep == "\\" else "\\"
DORMANT_ORG = "dddddddd-0000-0000-0000-000000000004"


def _other_spelling(path):
    return path.replace(os.sep, OTHER_SEP)


def _second_dormant_store(env):
    second = os.path.join(env.store_candidates[0],
                          "eeeeeeee-0000-0000-0000-000000000005",
                          "ffffffff-0000-0000-0000-000000000006")
    os.makedirs(second)
    return second


def test_to_matches_a_store_path_in_the_other_spelling(two_account_env,
                                                       tmp_path):
    env, src, dst = two_account_env(tmp_path)
    _second_dormant_store(env)
    _, dest = ct.resolve_sync_endpoints(env, to=_other_spelling(dst))
    assert os.path.normcase(dest.path) == os.path.normcase(dst)


def test_plan_sync_takes_a_store_path_in_the_other_spelling(two_account_env,
                                                            tmp_path):
    env, src, dst = two_account_env(tmp_path)
    _second_dormant_store(env)
    m = ct.plan_sync(env, ct.SyncFlags(to=_other_spelling(dst)))
    assert os.path.normcase(m["dest_path"]) == os.path.normcase(dst)


def test_to_matches_a_store_path_with_a_trailing_separator(two_account_env,
                                                           tmp_path):
    """What Git Bash hands over for a Tab-completed folder: '/' spelling and a
    trailing '/'. No store path ends in a separator, so the '/' appended to
    the path side is what lets it match, in either spelling."""
    env, src, dst = two_account_env(tmp_path)
    _second_dormant_store(env)
    for value in (dst + os.sep, _other_spelling(dst) + OTHER_SEP):
        _, dest = ct.resolve_sync_endpoints(env, to=value)
        assert os.path.normcase(dest.path) == os.path.normcase(dst), value


def test_to_matches_a_path_fragment_in_the_other_spelling(two_account_env,
                                                          tmp_path):
    env, src, dst = two_account_env(tmp_path)
    _second_dormant_store(env)
    _, dest = ct.resolve_sync_endpoints(
        env, to="claude-code-sessions" + OTHER_SEP + "cccccccc")
    assert os.path.normcase(dest.path) == os.path.normcase(dst)


def test_a_broad_fragment_in_the_other_spelling_is_a_broad_fragment(
        two_account_env, tmp_path):
    """It names every other store: that one store when there is one, the
    ambiguity refusal listing them when there are two - as any broad fragment
    already does. Before the fold it matched nothing."""
    env, src, dst = two_account_env(tmp_path)
    broad = "claude-code-sessions" + OTHER_SEP
    _, dest = ct.resolve_sync_endpoints(env, to=broad)
    assert os.path.normcase(dest.path) == os.path.normcase(dst)
    second = _second_dormant_store(env)
    with pytest.raises(ct.Refusal) as exc:
        ct.resolve_sync_endpoints(env, to=broad)
    msg = str(exc.value)
    assert "matched 2 accounts" in msg
    assert _line_containing(msg, dst) and _line_containing(msg, second)


def test_the_signed_in_store_in_any_spelling_keeps_the_exact_refusal(
        two_account_env, tmp_path):
    """The window routes on this sentence (tests/test_gui_destination.py).
    Every store of the signed-in account is removed before matching, so no
    spelling of one can become the destination."""
    env, src, dst = two_account_env(tmp_path)
    for value in (_other_spelling(src), _other_spelling(src) + OTHER_SEP,
                  src + os.sep):
        with pytest.raises(ct.Refusal) as exc:
            ct.resolve_sync_endpoints(env, to=value)
        assert str(exc.value) == \
            "--to {0!r} matched no other account store".format(value)


def test_a_mid_uuid_pair_is_refused_with_or_without_a_trailing_separator(
        two_account_env, tmp_path):
    """DORMANT's id ends in 0003 and its org starts dddd, so '0003/dddd' and
    '0003/<its whole org id>' both sit inside its folded store path. The pair
    form anchors both halves and refuses them, and a trailing separator must
    not turn either into a path substring: the '/' appended to the path side
    is withheld from a value that is the printed account form without its
    trailing separators."""
    env, src, dst = two_account_env(tmp_path)
    for value in ("0003/dddd", "0003/dddd/", "0003/dddd\\",
                  "0003/" + DORMANT_ORG, "0003/" + DORMANT_ORG + "/",
                  "0003/" + DORMANT_ORG + "\\"):
        with pytest.raises(ct.Refusal,
                           match="matched no other account store"):
            ct.resolve_sync_endpoints(env, to=value)


def test_the_backslash_twin_of_a_mid_uuid_pair_stays_a_substring(
        two_account_env, tmp_path):
    """'0003\\dddd' is not pair-shaped, so it is matched as a substring and
    names the store whose account id ends in 0003 - as it already does on
    Windows, whose store paths hold that backslash. Pair precedence is
    untouched in both directions."""
    env, src, dst = two_account_env(tmp_path)
    _, dest = ct.resolve_sync_endpoints(env, to="0003\\dddd")
    assert os.path.normcase(dest.path) == os.path.normcase(dst)


def test_values_that_matched_before_still_match_the_same_store(
        two_account_env, tmp_path):
    """The guarantee the ruling rests on: the folded test is added beside
    today's, never in place of it."""
    env, src, dst = two_account_env(tmp_path)
    second = _second_dormant_store(env)
    _agent_mode_config(env, DORMANT, {"accountUuid": DORMANT,
                                      "emailAddress": "other@example.com"})
    for value in (dst, "cccccccc", "dddddddd", "other@example.com"):
        _, dest = ct.resolve_sync_endpoints(env, to=value)
        assert os.path.normcase(dest.path) == os.path.normcase(dst), value
    _, dest = ct.resolve_sync_endpoints(env, to="ffffffff")
    assert os.path.normcase(dest.path) == os.path.normcase(second)


def test_a_trailing_separator_keeps_its_folder_boundary(mkenv, tmp_path):
    """Two roots whose folder names share a prefix, holding the same ids. A
    folder name plus a separator still names one root, in either spelling:
    nothing is stripped from the value, so 'Vendor/' never reaches
    'Vendor-Data'."""
    env = mkenv(tmp_path)
    roots = [str(tmp_path / name / "claude-code-sessions")
             for name in ("Vendor", "Vendor-Data")]
    for r in roots:
        os.makedirs(os.path.join(r, SOURCE_ACCT,
                                 "bbbbbbbb-0000-0000-0000-000000000002"))
        os.makedirs(os.path.join(r, DORMANT, DORMANT_ORG))
    env.store_candidates = roots
    with open(os.path.join(env.home, ".claude.json"), "w", encoding="utf-8") as fh:
        json.dump({"oauthAccount": {
            "accountUuid": SOURCE_ACCT,
            "organizationUuid": "bbbbbbbb-0000-0000-0000-000000000002"}}, fh)
    want = os.path.join(roots[0], DORMANT, DORMANT_ORG)
    folder = str(tmp_path / "Vendor")
    for value in (folder + os.sep, _other_spelling(folder) + OTHER_SEP):
        _, dest = ct.resolve_sync_endpoints(env, to=value)
        assert os.path.normcase(dest.path) == os.path.normcase(want), value


def test_live_matches_the_dormant_store_path_in_any_spelling(two_account_env,
                                                             tmp_path):
    env, src, dst = two_account_env(tmp_path)
    _write_desktop_config(env, DORMANT)
    for value in (_other_spelling(dst), dst + os.sep,
                  _other_spelling(dst) + OTHER_SEP):
        source, _ = ct.resolve_sync_endpoints(env, live=value)
        assert source.account_uuid == DORMANT, value
        assert os.path.normcase(source.path) == os.path.normcase(dst), value


def test_live_fragment_naming_both_accounts_stores_is_ambiguous(
        two_account_env, tmp_path):
    env, src, dst = two_account_env(tmp_path)
    _write_desktop_config(env, DORMANT)
    with pytest.raises(ct.Refusal) as exc:
        ct.resolve_sync_endpoints(env, live="claude-code-sessions" + OTHER_SEP)
    assert "matched 2 stores" in str(exc.value)


def test_live_mid_uuid_pair_with_a_trailing_separator_is_refused(
        two_account_env, tmp_path):
    env, src, dst = two_account_env(tmp_path)
    _write_desktop_config(env, DORMANT)
    for value in ("0003/dddd/", "0003/" + DORMANT_ORG + "/"):
        with pytest.raises(ct.Refusal, match="matched no store"):
            ct.resolve_sync_endpoints(env, live=value)


def test_live_made_only_of_separators_is_refused_like_a_blank_one(
        two_account_env, tmp_path):
    """RULING 5 refuses a blank value because it matches every candidate - on
    a one-candidate machine, the bare force flag. A value of nothing but
    separators is that value in another form: '\\' matched every Windows store
    path before the fold, '/' every POSIX one. Here only the config-named
    account has a store, so either would certify it, in either scope."""
    env, src, dst = two_account_env(tmp_path)
    os.rmdir(src)
    os.rmdir(os.path.dirname(src))        # the oauth-named account: no store
    _write_desktop_config(env, DORMANT)
    dirs = ct._account_dirs(env)
    for value in ("/", "\\", " / ", "\\\\", "/\\"):
        for scope in (False, True):
            with pytest.raises(ct.Refusal, match="name the account"):
                ct._resolve_live_assertion(env, value, dirs,
                                           account_scope=scope)


def test_repoint_live_takes_the_other_spelling_too(two_account_env, tmp_path):
    """repoint, new-row and retitle reach the same store-scope assertion."""
    env, src, dst = two_account_env(tmp_path)
    _write_desktop_config(env, DORMANT)
    hits = ct._repoint_store(env, ct.RepointFlags(live=_other_spelling(dst)))
    assert [os.path.normcase(p) for _a, _o, p in hits] == \
        [os.path.normcase(dst)]


# ------------------------------------- Task 5: old journals, damaged manifests

def _as_0_17_sync(m):
    """A sync manifest as 0.17.0 wrote it: no lineage fields at all."""
    m = dict(m)
    for k in ("lineage_format", "lineage_cycles"):
        m.pop(k, None)
    m["rows"] = [{k: v for k, v in r.items()
                  if k not in ("lineage", "lineage_planned")}
                 for r in m["rows"]]
    return m


def _verbatim_link_plan(env, src, dst):
    _row(src, "local_0.json", "sid-0", "Northwind",
         {"spawnedFrom": {"sessionId": "local_zz", "taskId": "task_7f",
                          "title": "ACME-REVIEW", "sideSession": True}})
    _transcript(env, "sid-0")
    m = _as_0_17_sync(ct.plan_sync(env, ct.SyncFlags(verbatim=True)))
    assert m["verbatim"] is True
    assert json.loads(ct.unb64(m["rows"][0]["post_b64"]))["spawnedFrom"][
        "sessionId"] == "local_zz"
    return m


def test_a_0_17_sync_journal_with_a_verbatim_link_recovers_and_undoes(
        two_account_env, tmp_path):
    env, src, dst = two_account_env(tmp_path)
    m = _verbatim_link_plan(env, src, dst)
    op = ct.new_op(env, m)                     # pending, as 0.17.0 left it
    assert sorted(ct.classify_op(env, op)["resolutions"]) == ["back", "forward"]
    assert ct.recover_op(env, op, "forward") == "completed"
    with open(os.path.join(dst, "local_0.json"), encoding="utf-8") as fh:
        assert json.load(fh)["spawnedFrom"]["sessionId"] == "local_zz"
    assert ct.undo_sync(env, op) == "undone"
    assert not os.path.exists(os.path.join(dst, "local_0.json"))
    # and a second pending one goes back
    op2 = ct.new_op(env, _as_0_17_sync(ct.plan_sync(
        env, ct.SyncFlags(verbatim=True))))
    assert ct.recover_op(env, op2, "back") == "rolled_back"


def _tree(root):
    """{relative path: bytes} for every file under ROOT."""
    out = {}
    for d, _dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(d, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, root)] = fh.read()
    return out


def test_a_hand_built_0_17_sync_journal_undoes_children_first(
        two_account_env, tmp_path, monkeypatch):
    """R15 holds for a journal 0.17.0 wrote, built here by hand: no
    lineage_format, no lineage_cycles, and no lineage, link_change or
    chain_change on its rows, which are in the order 0.17.0 journaled them:
    an add, a refresh, an add. Undo reverses them in one sequence, the
    reverse of m["rows"] - the last add removed, the refresh restored, the
    first add removed - not every add before every refresh, as 0.17.0's own
    undo ran. Nothing else changes: the source store and the destination's
    other row are byte-identical, the journal's rows are as they were, and
    the manifest gains only its undo report and the dangling part of the
    link report, which names nothing here."""
    env, src, dst = two_account_env(tmp_path)
    _transcripts(env, "sid-a", "sid-b", "sid-c", "sid-o")
    _lrow(src, "local_a.json", "sid-a", "Northwind alpha", lastActivityAt=5)
    _lrow(src, "local_b.json", "sid-b", "Northwind bravo v2", lastActivityAt=5)
    _lrow(src, "local_c.json", "sid-c", "Northwind charlie", lastActivityAt=5)
    _lrow(dst, "local_b.json", "sid-b", "Northwind bravo", lastActivityAt=1)
    _lrow(dst, "local_o.json", "sid-o", "ACME-REVIEW other", lastActivityAt=1)
    pre_b = _bytes(os.path.join(dst, "local_b.json"))
    root = env.store_candidates[0]
    before = _tree(root)
    rows = []
    for name in ("local_c.json", "local_b.json", "local_a.json"):
        post = _bytes(os.path.join(src, name))
        data = json.loads(post)
        refresh = name == "local_b.json"
        rows.append({
            "name": name, "dest_path": os.path.join(dst, name),
            "pre_b64": ct.b64(pre_b) if refresh else None,
            "is_update": refresh, "swaps_conversation": False,
            "displaced_session": None, "displaced_orphan": None,
            "displaced_overlap": None, "displaced_turns": None,
            "incoming_turns": None, "pre_activity": 1 if refresh else None,
            "regresses": False, "activity_unknown": False,
            "dest_dropped": [] if refresh else None,
            "dest_reset": [] if refresh else None,
            "post_b64": ct.b64(post), "session_id": data["cliSessionId"],
            "title": data["title"], "removed": [], "reset": [],
            "overrode_tombstone": False, "written": True})
        with open(os.path.join(dst, name), "wb") as fh:
            fh.write(post)
    op_id = "20260901T000000Z-0a17d0"
    m = {"op_id": op_id, "op_type": "sync", "status": "completed",
         "source_path": src, "dest_path": dst,
         "source_account": "aaaaaaaa-0000-0000-0000-000000000001",
         "source_org": "bbbbbbbb-0000-0000-0000-000000000002",
         "source_email": "me@example.com", "source_resolved_from": "oauth",
         "dest_account": "cccccccc-0000-0000-0000-000000000003",
         "dest_org": "dddddddd-0000-0000-0000-000000000004",
         "dest_email": None, "dest_email_source": None, "update": True,
         "verbatim": False, "newer_only": False, "allow_orphan": False,
         "history": [{"status": s, "at": 1_700_000_000.0 + i}
                     for i, s in enumerate(("journaled", "writing",
                                            "completed"))],
         "rows": rows}
    os.makedirs(os.path.join(env.ops_dir, op_id))
    with open(os.path.join(env.ops_dir, op_id, "manifest.json"), "w",
              encoding="utf-8") as fh:
        json.dump(m, fh)
    op, = ct.list_ops(env)
    assert "lineage_format" not in op.manifest
    assert ct._sync_shape_error(op.manifest) is None
    journaled = json.loads(json.dumps(op.manifest["rows"]))
    steps = []
    real_unlink, real_write = os.unlink, ct.atomic_write

    def unlink(path, *a, **k):
        if os.path.basename(str(path)).startswith("local_"):
            steps.append(("remove", os.path.basename(str(path))))
        return real_unlink(path, *a, **k)

    def write(path, data):
        if os.path.basename(path).startswith("local_"):
            steps.append(("restore", os.path.basename(path)))
        return real_write(path, data)
    monkeypatch.setattr(ct.os, "unlink", unlink)
    monkeypatch.setattr(ct, "atomic_write", write)
    assert ct.undo_sync(env, op) == "undone"
    monkeypatch.setattr(ct.os, "unlink", real_unlink)
    monkeypatch.setattr(ct, "atomic_write", real_write)
    assert steps == [("remove", "local_a.json"), ("restore", "local_b.json"),
                     ("remove", "local_c.json")]
    assert _tree(root) == before
    saved = _journal_of(env, op)
    assert saved["rows"] == journaled
    assert set(saved) - set(m) == {"undo_report", "link_report"}
    assert saved["undo_report"] == {"removed": 2, "restored": 1,
                                    "already_gone": 0, "not_restored": 0}
    assert saved["link_report"] == EMPTY_LINKS
    assert saved["status"] == "undone"
    assert {k: saved[k] for k in m if k not in ("status", "history")} == \
        {k: m[k] for k in m if k not in ("status", "history")}


@pytest.mark.parametrize("damage", [
    lambda m: m.__setitem__("rows", 5),
    lambda m: m.__setitem__("rows", [5, None]),
    lambda m: m["rows"][0].__setitem__("dest_path", None),
    lambda m: m["rows"][0].__setitem__("name", ["x"]),
    lambda m: (m.__setitem__("lineage_format", 1),
               m["rows"][0].__setitem__("lineage", "junk")),
    lambda m: m.__setitem__("lineage_format", 2),
])
def test_a_damaged_sync_manifest_is_a_shape_error_in_classify_and_undo(
        two_account_env, tmp_path, damage):
    import types
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=1)
    m = ct.plan_sync(env, ct.SyncFlags())
    damage(m)
    op = ct.new_op(env, m)
    for fn in (ct.classify_sync_op, ct.classify_op):
        c = fn(env, op)
        assert c["resolutions"] == [] and "damaged" in c["note"]
    ct.cmd_recover(env, types.SimpleNamespace(
        op_id=None, verbose=True))                  # the listing survives
    with pytest.raises(ct.Refusal, match="damaged"):
        ct.recover_op(env, op, "forward")
    with pytest.raises(ct.Refusal, match="damaged"):
        ct.recover_op(env, op, "back")
    ct.set_status(op, "completed")
    assert ct.classify_sync_op(env, op)["resolutions"] == []
    with pytest.raises(ct.Refusal, match="damaged"):
        ct.undo_sync(env, op)
    ct.gather_doctor(env)


def test_a_damaged_sync_op_in_an_unresolved_store_is_still_a_shape_error(
        two_account_env, tmp_path):
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=1)
    m = ct.plan_sync(env, ct.SyncFlags())
    m["rows"][0]["dest_path"] = os.path.join(str(tmp_path), "elsewhere",
                                             "local_0.json")
    op = ct.new_op(env, m)
    assert ct.classify_op(env, op)["resolutions"] == ["back"]
    m["rows"][0]["name"] = None
    op2 = ct.new_op(env, m)
    c = ct.classify_op(env, op2)
    assert c["resolutions"] == [] and "damaged" in c["note"]


def test_a_damaged_pending_op_does_not_crash_a_later_commands_rotation(
        mkenv, tmp_path):
    env = mkenv(tmp_path)
    live = os.path.join(str(tmp_path), "d", "local_a.json")
    # A pending sync whose rows are partly junk, and one retitle likewise.
    ct.new_op(env, {"op_type": "sync", "rows": [5, None, "x",
                                                  {"dest_path": live}]})
    ct.new_op(env, {"op_type": "retitle", "rows": [[], {"dest_path": 3}]})
    # More than ten finished ops, the oldest claiming the live path and also
    # carrying junk rows, so _collides has to step over them.
    oldest = ct.new_op(env, {"op_type": "sync", "rows": [
        7, {"dest_path": live, "written": True}]})
    ct.set_status(oldest, "completed")
    for i in range(11):
        o = ct.new_op(env, {"op_type": "sync", "rows": [
            "junk", {"dest_path": "/z/%d.json" % i, "written": True}]})
        ct.set_status(o, "completed")
    pruned = ct.rotate_ops(env)
    assert oldest.manifest["op_id"] not in pruned
    assert os.path.isdir(oldest.op_dir)
    m = {"op_id": "zzz", "op_type": "sync", "rows": []}
    assert isinstance(ct._sync_paths_claimed_elsewhere(env, m), set)


@pytest.mark.parametrize("title", ["missing", None, 5])
@pytest.mark.parametrize("fmt", [False, True])
def test_a_row_without_a_usable_title_and_bad_bytes_still_offers_back(
        two_account_env, tmp_path, title, fmt):
    import types
    env, src, dst = two_account_env(tmp_path)
    _prepared(env, src, dst, n=1)
    m = ct.plan_sync(env, ct.SyncFlags())
    if fmt:
        m["lineage_format"] = 1
    r = m["rows"][0]
    for bad in ("corrupt", "missing"):
        if title == "missing":
            r.pop("title", None)
        else:
            r["title"] = title
        if bad == "corrupt":
            r["post_b64"] = "!!!not base64!!!"
        else:
            r.pop("post_b64", None)
        op = ct.new_op(env, m)
        assert ct.classify_op(env, op)["resolutions"] == ["back"]
        assert ct.classify_sync_op(env, op)["resolutions"] == ["back"]
        assert ct.cmd_recover(env, types.SimpleNamespace(
            op_id=None, verbose=True)) == 1
        ct.set_status(op, "rolled_back")


# ------------------------------------------- Task 7: sync carries the link
#
# docs/specs/2026-11-17-carry-lineage-design.md, "sync": planning runs in the
# spec's seven steps, and the lineage pass gives each row its source's link
# and chain, translated into the destination. Rows here are written as this
# tool writes them (compact JSON, sessionId the filename stem), so a row
# that says the same thing has the same bytes.

LTASK = "task_7f"
LP, LC, LG, LQ, LR = ("sid-parent", "sid-child", "sid-grand", "sid-other",
                      "sid-root")


def _lrow(store, name, sid, title, **extra):
    """A row as this tool writes one: compact JSON whose sessionId is the
    filename stem. Keyword arguments add keys in order, or replace them."""
    d = {"sessionId": name[:-len(".json")], "cliSessionId": sid,
         "cwd": "C:\\p", "title": title, "lastActivityAt": 1}
    d.update(extra)
    with open(os.path.join(store, name), "w", encoding="utf-8") as fh:
        fh.write(json.dumps(d, separators=(",", ":")))
    return d


def _side(target, **extra):
    """An app-written side-session link to the row TARGET."""
    d = {"sessionId": target, "taskId": LTASK, "title": "Northwind kickoff",
         "sideSession": True}
    d.update(extra)
    return d


def _transcripts(env, *sids):
    for sid in sids:
        _transcript(env, sid)


def _post(r):
    return json.loads(ct.unb64(r["post_b64"]))


def _by_name(m):
    return {r["name"]: r for r in m["rows"]}


def _codes(r):
    return [n["code"] for n in r["lineage"]["notes"]]


def _sync_main(env, monkeypatch, capsys, *argv):
    monkeypatch.setattr(ct, "default_env", lambda: env)
    rc = ct.main(["sync"] + list(argv))
    return rc, capsys.readouterr().out.splitlines()


def _sign_in(env, account, org, email="bob@example.com"):
    with open(os.path.join(env.home, ".claude.json"), "w",
              encoding="utf-8") as fh:
        json.dump({"oauthAccount": {"accountUuid": account,
                                    "organizationUuid": org,
                                    "emailAddress": email}}, fh)


def test_sync_add_translates_the_link(two_account_env, tmp_path):
    """The destination holds the parent under another row file. The add
    carries the link translated to that row, in place and as exactly
    {sessionId, taskId}; the source link's other keys are recorded by name,
    and its values are kept nowhere in the plan."""
    env, src, dst = two_account_env(tmp_path)
    _transcripts(env, LP, LC)
    _lrow(src, "local_p.json", LP, "Northwind kickoff")
    child = _lrow(src, "local_c.json", LC, "ACME-REVIEW side task",
                  spawnedFrom=_side("local_p", title="ZZ-LINK-TITLE"),
                  model="m")
    _lrow(dst, "local_q.json", LP, "Northwind kickoff")
    m = ct.plan_sync(env, ct.SyncFlags(only="ACME"))
    assert m["lineage_format"] == 1 and m["lineage_cycles"] == 0
    assert [r["name"] for r in m["rows"]] == ["local_c.json"]
    r = m["rows"][0]
    post = _post(r)
    assert post["spawnedFrom"] == {"sessionId": "local_q", "taskId": LTASK}
    # In place: the key keeps its position, and nothing else changes.
    assert list(post) == list(child)
    assert {k: v for k, v in post.items() if k != "spawnedFrom"} == \
        {k: v for k, v in child.items() if k != "spawnedFrom"}
    assert r["lineage"] == {
        "parent_session": LP, "parent_row": "local_q",
        "parent_planned": False, "root_session": "", "root_row": "",
        "root_planned": False, "carried_keys": ["sessionId", "taskId"],
        "dropped_keys": ["sideSession", "title"], "notes": []}
    assert r["link_change"] is None and r["chain_change"] is None
    assert "ZZ-LINK-TITLE" not in json.dumps(m)
    assert "ZZ-LINK-TITLE" not in ct.unb64(r["post_b64"]).decode("utf-8")


def test_sync_add_with_no_parent_in_b_carries_no_link_and_says_so(
        two_account_env, tmp_path, monkeypatch, capsys):
    """Nothing in the destination opens the parent, and --only leaves the
    parent's own row out of the plan: the add carries neither its link nor
    its chain, and the report says why on the row's own line and on the
    links and chains lines. A cycle of links the destination already holds
    is reported once."""
    env, src, dst = two_account_env(tmp_path)
    _transcripts(env, LP, LC, LQ, LR)
    _lrow(src, "local_p.json", LP, "Northwind kickoff")
    _lrow(src, "local_c.json", LC, "ACME-REVIEW side task",
          spawnedFrom=_side("local_p"),
          startChain={"firstSessionId": "local_p", "depth": 1})
    _lrow(dst, "local_x.json", LQ, "Loop one", spawnedFrom=_side("local_y"))
    _lrow(dst, "local_y.json", LR, "Loop two", spawnedFrom=_side("local_x"))
    m = ct.plan_sync(env, ct.SyncFlags(only="ACME"))
    r, = m["rows"]
    post = _post(r)
    assert "spawnedFrom" not in post and "startChain" not in post
    assert r["lineage"]["parent_row"] == "" and r["lineage"]["root_row"] == ""
    assert _codes(r) == ["parent_not_in_account", "lineage_chain_dropped"]
    assert m["lineage_cycles"] == 1
    rc, lines = _sync_main(env, monkeypatch, capsys, "--only", "ACME")
    assert rc == 0
    assert ("   ACME-REVIEW side task   [link: no row for the parent in that "
            "account]") in lines
    assert ("links   : 1 left out (no row for the parent in that "
            "account 1)") in lines
    assert "chains  : 1 left out" in lines
    assert "cycles  : 1 already present, left as they are" in lines


def test_sync_parent_removed_by_held_orphan_gives_no_link(two_account_env,
                                                          tmp_path):
    """The parent's refresh would hide the conversation the destination's
    row opens now, so held_orphan holds it. That row counts as it stands,
    opening the other conversation, so the child added beside it carries no
    link, and the note names the hold rather than a missing row."""
    env, src, dst = two_account_env(tmp_path)
    _transcripts(env, LP, LC, LQ)
    _lrow(src, "local_p.json", LP, "Northwind kickoff")
    _lrow(src, "local_c.json", LC, "ACME-REVIEW side task",
          spawnedFrom=_side("local_p"))
    _lrow(dst, "local_p.json", LQ, "Northwind old thread")
    m = ct.plan_sync(env, ct.SyncFlags(update=True))
    assert m["tally"]["held_orphan"] == ["Northwind kickoff"]
    assert m["tally"]["held_sessions"] == {LP: "held_orphan"}
    r, = m["rows"]
    assert r["name"] == "local_c.json"
    assert "spawnedFrom" not in _post(r)
    assert _codes(r) == ["parent_pair_held"]
    assert "held_orphan" in r["lineage"]["notes"][0]["detail"]


def test_sync_parent_synced_in_the_same_run_same_filename(two_account_env,
                                                         tmp_path):
    """The destination already opens the parent under another row file, and
    this sync adds the source's own parent row too. The link names the row
    with the source parent's filename, planned in this run, and the rows
    come back parents first although selection lists the newer child
    first."""
    env, src, dst = two_account_env(tmp_path)
    _transcripts(env, LP, LC)
    _lrow(src, "local_p.json", LP, "Northwind kickoff", lastActivityAt=1)
    _lrow(src, "local_c.json", LC, "ACME-REVIEW side task", lastActivityAt=5,
          spawnedFrom=_side("local_p"))
    _lrow(dst, "local_q.json", LP, "Northwind kickoff", lastActivityAt=9)
    m = ct.plan_sync(env, ct.SyncFlags())
    assert [r["name"] for r in m["rows"]] == ["local_p.json", "local_c.json"]
    c = _by_name(m)["local_c.json"]
    assert _post(c)["spawnedFrom"] == {"sessionId": "local_p",
                                       "taskId": LTASK}
    assert c["lineage"]["parent_row"] == "local_p"
    assert c["lineage"]["parent_planned"] is True
    assert ct._sync_shape_error(m) is None


def test_sync_parent_refreshed_into_another_conversation_is_not_chosen(
        two_account_env, tmp_path):
    """The destination's only row for the parent is the one this sync
    refreshes into another conversation, so in the store as planned nothing
    opens the parent: no link. Without --update that same row is the
    parent's, and the link names it."""
    env, src, dst = two_account_env(tmp_path)
    _transcripts(env, LP, LC, LQ)
    _lrow(src, "local_p.json", LP, "ACME-REVIEW kickoff")
    _lrow(src, "local_c.json", LC, "Northwind side task",
          spawnedFrom=_side("local_p"))
    _lrow(src, "local_x.json", LQ, "Northwind other thread")
    _lrow(dst, "local_x.json", LP, "Northwind other thread")
    m = ct.plan_sync(env, ct.SyncFlags(only="Northwind"))
    assert _post(_by_name(m)["local_c.json"])["spawnedFrom"] == {
        "sessionId": "local_x", "taskId": LTASK}
    m = ct.plan_sync(env, ct.SyncFlags(only="Northwind", update=True))
    by = _by_name(m)
    assert by["local_x.json"]["swaps_conversation"] is True
    c = by["local_c.json"]
    assert "spawnedFrom" not in _post(c)
    assert _codes(c) == ["parent_not_in_account"]


def test_sync_newer_only_hold_on_the_parent_resolves_against_the_held_row(
        two_account_env, tmp_path):
    """--newer-only holds the parent's refresh (the destination's copy is
    newer). The held row counts as it stands, so the child's link names it,
    as an existing row; the hold is recorded by the conversation its source
    opens, and the held row keeps its regressing entry."""
    env, src, dst = two_account_env(tmp_path)
    _transcripts(env, LP, LC)
    _lrow(src, "local_p.json", LP, "Northwind kickoff v2", lastActivityAt=1)
    _lrow(src, "local_c.json", LC, "ACME-REVIEW side task", lastActivityAt=5,
          spawnedFrom=_side("local_p"))
    _lrow(dst, "local_p.json", LP, "Northwind kickoff", lastActivityAt=9)
    m = ct.plan_sync(env, ct.SyncFlags(update=True, newer_only=True))
    t = m["tally"]
    assert t["held_older"] == ["Northwind kickoff v2"]
    assert t["regressing"] == ["Northwind kickoff v2"]
    assert t["held_sessions"] == {LP: "held_older"}
    c, = m["rows"]
    assert _post(c)["spawnedFrom"] == {"sessionId": "local_p",
                                       "taskId": LTASK}
    assert c["lineage"]["parent_planned"] is False


def test_sync_link_only_refresh_is_written_not_discarded(two_account_env,
                                                        tmp_path):
    """The destination's child row equals the source's apart from the link,
    which it lacks. Translated, the link is the refresh's one change, and
    the refresh is written. The parent's rows are byte-identical, so that
    refresh is unchanged."""
    env, src, dst = two_account_env(tmp_path)
    _transcripts(env, LP, LC)
    for store in (src, dst):
        _lrow(store, "local_p.json", LP, "Northwind kickoff")
    _lrow(src, "local_c.json", LC, "ACME-REVIEW side task",
          spawnedFrom=_side("local_p"), model="m")
    _lrow(dst, "local_c.json", LC, "ACME-REVIEW side task", model="m")
    m = ct.plan_sync(env, ct.SyncFlags(update=True))
    assert m["tally"]["unchanged"] == ["Northwind kickoff"]
    c, = m["rows"]
    assert c["name"] == "local_c.json" and c["is_update"] is True
    post = _post(c)
    assert post["spawnedFrom"] == {"sessionId": "local_p", "taskId": LTASK}
    assert {k: v for k, v in post.items() if k != "spawnedFrom"} == \
        json.loads(ct.unb64(c["pre_b64"]))
    assert c["dest_dropped"] == [] and c["link_change"] is None


def test_sync_link_only_refresh_still_vouches_in_held_orphan(
        two_account_env, tmp_path, monkeypatch):
    """A refresh that keeps its row's conversation and changes only the
    link leaves that row opening what it opens now, so held_orphan does not
    count it among the rows about to stop vouching (the doomed set). A
    refresh that changes anything else still is. The source's own row
    vouches for the same conversation as well, so the doomed set is what
    this pins; the swap is not held either way."""
    env, src, dst = two_account_env(tmp_path)
    _transcripts(env, LP, LC, LQ, LR)
    for store in (src, dst):
        _lrow(store, "local_p.json", LP, "Northwind kickoff")
    _lrow(src, "local_c.json", LC, "ACME-REVIEW side task",
          spawnedFrom=_side("local_p"))
    _lrow(dst, "local_c.json", LC, "ACME-REVIEW side task")
    _lrow(src, "local_t.json", LR, "Northwind notes v2", lastActivityAt=2)
    _lrow(dst, "local_t.json", LR, "Northwind notes")
    _lrow(src, "local_x.json", LQ, "Northwind other thread")
    _lrow(dst, "local_x.json", LC, "Northwind other thread")
    seen = []
    real = ct._other_pointers

    def spy(env_, dest_path, doomed_rows=()):
        seen.append(set(doomed_rows))
        return real(env_, dest_path, doomed_rows)
    monkeypatch.setattr(ct, "_other_pointers", spy)
    m = ct.plan_sync(env, ct.SyncFlags(update=True))
    assert seen == [{"local_t.json", "local_x.json"}]
    by = _by_name(m)
    assert by["local_x.json"]["displaced_orphan"] is False
    assert m["tally"]["held_orphan"] == []
    assert "spawnedFrom" in _post(by["local_c.json"])


def test_sync_dropped_unchanged_parent_turns_the_childs_link_to_existing(
        two_account_env, tmp_path):
    """The parent is itself a side session, and the destination's parent row
    already carries the link this tool would write. Once translated, the
    parent's refresh changes nothing and is dropped as unchanged. The child,
    added beside it, named it as planned; it now names it as existing, and
    the journal passes the shape check."""
    env, src, dst = two_account_env(tmp_path)
    _transcripts(env, LG, LP, LC)
    for store in (src, dst):
        _lrow(store, "local_g.json", LG, "Northwind board")
    _lrow(src, "local_p.json", LP, "Northwind kickoff",
          spawnedFrom=_side("local_g"))
    _lrow(dst, "local_p.json", LP, "Northwind kickoff",
          spawnedFrom={"sessionId": "local_g", "taskId": LTASK})
    _lrow(src, "local_c.json", LC, "ACME-REVIEW side task",
          spawnedFrom=_side("local_p"))
    m = ct.plan_sync(env, ct.SyncFlags(update=True))
    assert sorted(m["tally"]["unchanged"]) == ["Northwind board",
                                               "Northwind kickoff"]
    c, = m["rows"]
    assert c["name"] == "local_c.json"
    assert _post(c)["spawnedFrom"] == {"sessionId": "local_p",
                                       "taskId": LTASK}
    assert c["lineage"]["parent_row"] == "local_p"
    assert c["lineage"]["parent_planned"] is False
    assert ct._sync_shape_error(m) is None
    op = ct.new_op(env, m)
    assert sorted(ct.classify_op(env, op)["resolutions"]) == ["back",
                                                              "forward"]


def test_sync_refresh_that_drops_the_destinations_own_link_reports_it_once(
        two_account_env, tmp_path, monkeypatch, capsys):
    """The source's row has no link and the destination's has one, beside a
    field of its own. The refresh replaces the whole row, so the
    destination's link goes: link_change says so, and spawnedFrom is not
    listed again among the fields the row loses. A destination link that
    the source's translates to another row is retargeted."""
    env, src, dst = two_account_env(tmp_path)
    _transcripts(env, LP, LC, LR)
    for store in (src, dst):
        _lrow(store, "local_p.json", LP, "Northwind kickoff")
        _lrow(store, "local_q.json", LP, "Northwind kickoff copy")
    _lrow(src, "local_c.json", LC, "ACME-REVIEW side task", lastActivityAt=2)
    _lrow(dst, "local_c.json", LC, "ACME-REVIEW side task",
          spawnedFrom={"sessionId": "local_p", "taskId": LTASK},
          zzDestOnly=1)
    _lrow(src, "local_d.json", LR, "ACME-REVIEW second task",
          lastActivityAt=2, spawnedFrom=_side("local_p"))
    _lrow(dst, "local_d.json", LR, "ACME-REVIEW second task",
          spawnedFrom={"sessionId": "local_q", "taskId": LTASK})
    m = ct.plan_sync(env, ct.SyncFlags(update=True))
    by = _by_name(m)
    c, d = by["local_c.json"], by["local_d.json"]
    assert c["link_change"] == "dropped" and c["chain_change"] is None
    assert c["dest_dropped"] == ["zzDestOnly"]
    assert d["link_change"] == "retargeted" and d["chain_change"] is None
    assert _post(d)["spawnedFrom"] == {"sessionId": "local_p",
                                       "taskId": LTASK}
    assert d["dest_dropped"] == []
    rc, lines = _sync_main(env, monkeypatch, capsys, "--update")
    assert rc == 0
    assert lines.count("link    : local_c loses its link") == 1
    assert "link    : local_d now links to another row" in lines
    assert [ln for ln in lines if "fields the destination's row loses" in ln] \
        == ["   fields the destination's row loses: zzDestOnly"]
    assert not any("spawnedFrom" in ln for ln in lines)


@pytest.mark.parametrize("verbatim", [False, True])
def test_sync_refresh_that_drops_or_moves_the_destinations_chain_reports_it(
        two_account_env, tmp_path, monkeypatch, capsys, verbatim):
    """The destination's chain is lost (the source's row has none) or moved
    (the source's names another root and depth): chain_change says which,
    with or without --verbatim, and the report prints one line for each.
    The link names the same row throughout, so no link line."""
    env, src, dst = two_account_env(tmp_path)
    _transcripts(env, LP, LC, LR, LQ)
    for store in (src, dst):
        _lrow(store, "local_p.json", LP, "Northwind kickoff")
        _lrow(store, "local_r.json", LR, "Northwind board")
    _lrow(src, "local_c.json", LC, "ACME-REVIEW side task", lastActivityAt=2,
          spawnedFrom=_side("local_p"))
    _lrow(dst, "local_c.json", LC, "ACME-REVIEW side task",
          spawnedFrom={"sessionId": "local_p", "taskId": LTASK},
          startChain={"firstSessionId": "local_p", "depth": 1})
    _lrow(src, "local_d.json", LQ, "ACME-REVIEW second task",
          lastActivityAt=2, spawnedFrom=_side("local_p"),
          startChain={"firstSessionId": "local_r", "depth": 2})
    _lrow(dst, "local_d.json", LQ, "ACME-REVIEW second task",
          spawnedFrom={"sessionId": "local_p", "taskId": LTASK},
          startChain={"firstSessionId": "local_p", "depth": 1})
    m = ct.plan_sync(env, ct.SyncFlags(update=True, verbatim=verbatim))
    by = _by_name(m)
    assert sorted(by) == ["local_c.json", "local_d.json"]
    assert by["local_c.json"]["chain_change"] == "dropped"
    assert by["local_d.json"]["chain_change"] == "retargeted"
    for r in m["rows"]:
        assert r["link_change"] is None
        assert "startChain" not in (r["dest_dropped"] or [])
    argv = ["--update"] + (["--verbatim"] if verbatim else [])
    rc, lines = _sync_main(env, monkeypatch, capsys, *argv)
    assert rc == 0
    assert "chain   : local_c loses its chain" in lines
    assert "chain   : local_d now has another chain root" in lines
    assert not any(ln.startswith("link    :") for ln in lines)


def test_sync_round_trip_keeps_the_destinations_link_and_bytes(
        two_account_env, tmp_path):
    """A -> B carries the link as {sessionId, taskId}. B -> A with --update
    then finds A's own app-written link naming the same row with the same
    taskId and keeps it as it is (Departure 1), so A's rows are left
    byte-identical: the plan has nothing to write."""
    env, src, dst = two_account_env(tmp_path)
    _transcripts(env, LP, LC)
    _lrow(src, "local_p.json", LP, "Northwind kickoff")
    _lrow(src, "local_c.json", LC, "ACME-REVIEW side task",
          spawnedFrom=_side("local_p"),
          startChain={"firstSessionId": "local_p", "depth": 1})
    names = ("local_p.json", "local_c.json")
    before = {}
    for n in names:
        with open(os.path.join(src, n), "rb") as fh:
            before[n] = fh.read()
    assert ct.run_sync(env, ct.plan_sync(env, ct.SyncFlags())) == "completed"
    with open(os.path.join(dst, "local_c.json"), encoding="utf-8") as fh:
        assert json.load(fh)["spawnedFrom"] == {"sessionId": "local_p",
                                                "taskId": LTASK}
    _sign_in(env, DORMANT, DORMANT_ORG)          # now B is signed in
    back = ct.plan_sync(env, ct.SyncFlags(update=True))
    assert os.path.normcase(back["dest_path"]) == os.path.normcase(src)
    assert back["rows"] == []
    assert sorted(back["tally"]["unchanged"]) == ["ACME-REVIEW side task",
                                                  "Northwind kickoff"]
    for n in names:
        with open(os.path.join(src, n), "rb") as fh:
            assert fh.read() == before[n], n


def test_sync_verbatim_copies_links_and_counts_them(two_account_env, tmp_path,
                                                    monkeypatch, capsys):
    """--verbatim copies the link and chain as they are, carries no lineage,
    keeps selection's order, and counts the adds that copy a link for the
    `copied verbatim` line."""
    env, src, dst = two_account_env(tmp_path)
    _transcripts(env, LP, LC)
    _lrow(src, "local_p.json", LP, "Northwind kickoff", lastActivityAt=1)
    link = _side("local_p")
    chain = {"firstSessionId": "local_p", "depth": 1}
    _lrow(src, "local_c.json", LC, "ACME-REVIEW side task", lastActivityAt=5,
          spawnedFrom=link, startChain=chain)
    m = ct.plan_sync(env, ct.SyncFlags(verbatim=True))
    assert m["lineage_format"] == 1 and m["lineage_cycles"] == 0
    assert [r["name"] for r in m["rows"]] == ["local_c.json", "local_p.json"]
    assert all("lineage" not in r for r in m["rows"])
    post = _post(m["rows"][0])
    assert post["spawnedFrom"] == link and post["startChain"] == chain
    assert m["tally"]["verbatim_links"] == ["ACME-REVIEW side task"]
    assert ct._sync_shape_error(m) is None
    rc, lines = _sync_main(env, monkeypatch, capsys, "--verbatim")
    assert rc == 0
    assert "links   : 1 copied verbatim (not translated)" in lines
    assert [ln for ln in lines if ln.startswith(("links   :", "chains  :"))] \
        == ["links   : 1 copied verbatim (not translated)"]


def test_sync_verbatim_update_replacing_a_link_is_reported_by_the_link_line(
        two_account_env, tmp_path, monkeypatch, capsys):
    """--verbatim --update copies the source's link over the destination's
    own, which named another row: the link line reports it. It is a
    refresh, not an add, so the copied-verbatim count does not take it."""
    env, src, dst = two_account_env(tmp_path)
    _transcripts(env, LP, LC)
    for store in (src, dst):
        _lrow(store, "local_p.json", LP, "Northwind kickoff")
    _lrow(src, "local_c.json", LC, "ACME-REVIEW side task", lastActivityAt=2,
          spawnedFrom=_side("local_p"))
    _lrow(dst, "local_c.json", LC, "ACME-REVIEW side task",
          spawnedFrom={"sessionId": "local_q", "taskId": LTASK})
    m = ct.plan_sync(env, ct.SyncFlags(update=True, verbatim=True))
    c, = m["rows"]
    assert c["link_change"] == "retargeted"
    assert _post(c)["spawnedFrom"] == _side("local_p")
    assert m["tally"]["verbatim_links"] == []
    rc, lines = _sync_main(env, monkeypatch, capsys, "--update", "--verbatim")
    assert rc == 0
    assert "link    : local_c now links to another row" in lines
    assert not any(ln.startswith("links   :") for ln in lines)


def test_sync_verbatim_help_text():
    import argparse
    p = ct.build_parser()
    sub = next(a for a in p._actions
               if isinstance(a, argparse._SubParsersAction))
    act = next(a for a in sub.choices["sync"]._actions
               if "--verbatim" in a.option_strings)
    assert act.help == (
        "copy rows unchanged instead of stripping connector config. Links to "
        "a parent or chain root (spawnedFrom, startChain) are copied as is, "
        "not translated, so they may name a row that does not exist in the "
        "destination.")


def test_sync_without_links_plans_the_same_post_b64_as_before(
        two_account_env, tmp_path):
    """A store with no link anywhere plans what it did before the lineage
    pass: each row's post_b64 is the source row transformed, the rows keep
    selection's order (newest first), and the tally is today's. A refresh of
    a row the app serialised with spaces is still a refresh. Then: every
    row's lineage is the empty record."""
    env, src, dst = two_account_env(tmp_path)
    for s in ("sid-a", "sid-b", "sid-c", "sid-d", "sid-e"):
        _transcript(env, s)
    data = {
        "local_a.json": _row(src, "local_a.json", "sid-a", "Alpha", {
            "lastActivityAt": 5,
            "remoteMcpServersConfig": [{"name": "Northwind"}]}),
        "local_b.json": _row(src, "local_b.json", "sid-b", "Bravo",
                             {"lastActivityAt": 3}),
        "local_c.json": _lrow(src, "local_c.json", "sid-c", "Charlie",
                              lastActivityAt=4),
        "local_d.json": _row(src, "local_d.json", "sid-d", "Delta",
                             {"lastActivityAt": 2}),
        "local_e.json": _row(src, "local_e.json", "sid-e", "Echo",
                             {"lastActivityAt": 1}),
    }
    _row(dst, "local_b.json", "sid-b", "Bravo", {"lastActivityAt": 3})
    _lrow(dst, "local_c.json", "sid-c", "Charlie", lastActivityAt=4)
    _row(dst, "local_e.json", "sid-e", "Echo", {"lastActivityAt": 9})
    m = ct.plan_sync(env, ct.SyncFlags(update=True))
    assert [r["name"] for r in m["rows"]] == [
        "local_a.json", "local_b.json", "local_d.json", "local_e.json"]
    for r in m["rows"]:
        assert r["post_b64"] == ct.b64(ct.transform_row(data[r["name"]])[0])
    t = m["tally"]
    assert t["unchanged"] == ["Charlie"] and t["regressing"] == ["Echo"]
    assert t["activity_unknown"] == [] and t["swapping"] == []
    assert [r["is_update"] for r in m["rows"]] == [False, True, False, True]
    for r in m["rows"]:
        assert r["lineage"] == ct._lineage_empty()
        assert r["link_change"] is None and r["chain_change"] is None
    assert m["lineage_format"] == 1 and m["lineage_cycles"] == 0


def test_sync_newer_only_byte_identical_row_is_unchanged_not_held(
        two_account_env, tmp_path):
    """Exact identity stays ahead of the age holds (Departure 6): a row whose
    bytes already match is unchanged, never held_same, and one with no
    readable activity on either side is never held_unknown."""
    env, src, dst = two_account_env(tmp_path)
    _transcripts(env, "sid-a", "sid-b")
    for store in (src, dst):
        _lrow(store, "local_a.json", "sid-a", "Alpha", lastActivityAt=4)
        _lrow(store, "local_b.json", "sid-b", "Bravo", lastActivityAt=None)
    m = ct.plan_sync(env, ct.SyncFlags(update=True, newer_only=True))
    t = m["tally"]
    assert m["rows"] == []
    assert sorted(t["unchanged"]) == ["Alpha", "Bravo"]
    assert t["held_same"] == [] and t["held_unknown"] == []
    assert t["activity_unknown"] == []
    assert t["held_sessions"] == {}


def test_sync_byte_identical_linked_child_is_translated_not_unchanged(
        two_account_env, tmp_path):
    """Departure 6, narrowed (R21): the exact-identity exit ahead of the age
    holds is only for a row whose source carries neither spawnedFrom nor
    startChain. The destination's child is byte-identical to the
    transformed source row, so it carries the source account's link, to
    local_p, a filename the destination does not hold; the parent's row
    there is local_q. The child takes the spec's path, provisionally
    unchanged and then decided on its translated bytes, and is written with
    the link to local_q. An unlinked byte-identical row is still unchanged,
    before the holds; under --newer-only the linked child meets the holds
    first, as the spec orders them."""
    env, src, dst = two_account_env(tmp_path)
    _transcripts(env, LP, LC, "sid-u")
    _lrow(src, "local_p.json", LP, "Northwind kickoff")
    _lrow(dst, "local_q.json", LP, "Northwind kickoff")
    for store in (src, dst):
        _lrow(store, "local_c.json", LC, "ACME-REVIEW side task",
              lastActivityAt=5, spawnedFrom=_side("local_p"))
        _lrow(store, "local_u.json", "sid-u", "ACME-REVIEW notes",
              lastActivityAt=4)
    child = os.path.join(dst, "local_c.json")
    pre = _bytes(child)
    held = ct.plan_sync(env, ct.SyncFlags(update=True, newer_only=True,
                                          only="ACME"))
    t = held["tally"]
    assert held["rows"] == []
    assert t["unchanged"] == ["ACME-REVIEW notes"]
    assert t["held_same"] == ["ACME-REVIEW side task"]
    m = ct.plan_sync(env, ct.SyncFlags(update=True, only="ACME"))
    assert m["tally"]["unchanged"] == ["ACME-REVIEW notes"]
    assert [r["name"] for r in m["rows"]] == ["local_c.json"]
    r, = m["rows"]
    assert r["is_update"] and ct.unb64(r["pre_b64"]) == pre
    assert _post(r)["spawnedFrom"] == {"sessionId": "local_q",
                                       "taskId": LTASK}
    assert r["lineage"]["parent_row"] == "local_q"
    assert r["lineage"]["parent_planned"] is False
    assert r["link_change"] == "retargeted"
    assert ct.run_sync(env, m) == "completed"
    with open(child, encoding="utf-8") as fh:
        assert json.load(fh)["spawnedFrom"] == {"sessionId": "local_q",
                                                "taskId": LTASK}
    # Under --verbatim the link is copied as it is, so identical bytes are
    # still unchanged at step 1.
    _lrow(dst, "local_c.json", LC, "ACME-REVIEW side task",
          lastActivityAt=5, spawnedFrom=_side("local_p"))
    v = ct.plan_sync(env, ct.SyncFlags(update=True, verbatim=True,
                                       only="ACME"))
    assert v["rows"] == []
    assert sorted(v["tally"]["unchanged"]) == ["ACME-REVIEW notes",
                                               "ACME-REVIEW side task"]


def test_sync_byte_identical_row_with_null_link_keys_is_unchanged(
        two_account_env, tmp_path, monkeypatch, capsys):
    """R28: a spawnedFrom or startChain of null names no row, so a row whose
    bytes already match, and whose source holds only null link keys, takes
    the exact-identity exit ahead of the age holds (Departure 6, narrowed):
    it is unchanged, never planned or held, and no line says it loses its
    link."""
    env, src, dst = two_account_env(tmp_path)
    _transcripts(env, LC, "sid-u")
    for store in (src, dst):
        _lrow(store, "local_c.json", LC, "ACME-REVIEW side task",
              lastActivityAt=5, spawnedFrom=None, startChain=None)
        _lrow(store, "local_u.json", "sid-u", "ACME-REVIEW notes",
              lastActivityAt=4, startChain=None)
    both = ["ACME-REVIEW notes", "ACME-REVIEW side task"]
    for flags in (ct.SyncFlags(update=True, only="ACME"),
                  ct.SyncFlags(update=True, newer_only=True, only="ACME")):
        m = ct.plan_sync(env, flags)
        assert m["rows"] == []
        assert sorted(m["tally"]["unchanged"]) == both
        assert m["tally"]["held_same"] == []
    rc, lines = _sync_main(env, monkeypatch, capsys, "--update")
    assert rc == 0
    assert not any("loses its" in ln for ln in lines)


def test_sync_refresh_over_link_keys_that_name_no_row_reports_no_loss(
        two_account_env, tmp_path, monkeypatch, capsys):
    """m36: a destination spawnedFrom or startChain that is not an object
    names no row, so a refresh that removes it reports no link or chain
    change. A null link key in the source is popped, never written, and the
    plan's lineage agrees with its bytes."""
    env, src, dst = two_account_env(tmp_path)
    _transcripts(env, LC, LR)
    _lrow(src, "local_c.json", LC, "ACME-REVIEW side task", lastActivityAt=2,
          spawnedFrom=None)
    _lrow(dst, "local_c.json", LC, "ACME-REVIEW side task",
          spawnedFrom=None, startChain=None)
    _lrow(src, "local_d.json", LR, "ACME-REVIEW second task",
          lastActivityAt=2)
    _lrow(dst, "local_d.json", LR, "ACME-REVIEW second task",
          spawnedFrom="local_p", startChain=[1])
    m = ct.plan_sync(env, ct.SyncFlags(update=True))
    by = _by_name(m)
    assert sorted(by) == ["local_c.json", "local_d.json"]
    for r in by.values():
        assert r["link_change"] is None and r["chain_change"] is None
        post = _post(r)
        assert "spawnedFrom" not in post and "startChain" not in post
        assert r["lineage"]["parent_row"] == "" and r["lineage"]["root_row"] == ""
    assert ct._lineage_shape_error(m) is None
    rc, lines = _sync_main(env, monkeypatch, capsys, "--update")
    assert rc == 0
    assert not any("loses its" in ln or "another row" in ln for ln in lines)


def test_sync_ref_change_reads_a_non_object_on_either_side_as_no_row():
    """R28, from its review: both sides of _sync_ref_change take objects
    only. A link replaced by a null, a string or a list is dropped, as a
    removed key is; a destination value that is not an object changes no
    link; another row is a retarget, and other keys are not."""
    link = {"sessionId": "local_p", "taskId": LTASK}
    other = {"sessionId": "local_q", "taskId": LTASK}
    f = ("sessionId",)
    for post in ({}, {"spawnedFrom": None}, {"spawnedFrom": "local_p"},
                 {"spawnedFrom": [link]}):
        assert ct._sync_ref_change({"spawnedFrom": link}, post,
                                   "spawnedFrom", f) == "dropped", post
    assert ct._sync_ref_change({"spawnedFrom": link}, {"spawnedFrom": other},
                               "spawnedFrom", f) == "retargeted"
    assert ct._sync_ref_change({"spawnedFrom": link},
                               {"spawnedFrom": dict(link, title="x")},
                               "spawnedFrom", f) is None
    for pre in ({}, {"spawnedFrom": None}, {"spawnedFrom": "local_p"}):
        assert ct._sync_ref_change(pre, {"spawnedFrom": other},
                                   "spawnedFrom", f) is None, pre


def test_sync_verbatim_null_link_over_a_real_destination_link_is_a_loss(
        two_account_env, tmp_path, monkeypatch, capsys):
    """R28, from its review: --verbatim copies a source's null link as it
    is. Over the destination's real link that null names no row, so the
    refresh reports the destination's link lost, not retargeted."""
    env, src, dst = two_account_env(tmp_path)
    _transcripts(env, LP, LC)
    for store in (src, dst):
        _lrow(store, "local_p.json", LP, "Northwind kickoff")
    _lrow(src, "local_c.json", LC, "ACME-REVIEW side task", lastActivityAt=2,
          spawnedFrom=None)
    _lrow(dst, "local_c.json", LC, "ACME-REVIEW side task",
          spawnedFrom=_side("local_p"))
    m = ct.plan_sync(env, ct.SyncFlags(update=True, verbatim=True))
    c = _by_name(m)["local_c.json"]
    assert _post(c)["spawnedFrom"] is None
    assert c["link_change"] == "dropped" and c["chain_change"] is None
    rc, lines = _sync_main(env, monkeypatch, capsys, "--update", "--verbatim")
    assert rc == 0
    assert "link    : local_c loses its link" in lines
    assert not any("another row" in ln for ln in lines)


def test_sync_row_dropped_as_unchanged_is_not_counted_as_activity_unknown(
        two_account_env, tmp_path):
    """Neither side of the parent's refresh has a readable lastActivityAt.
    Once its link is translated the refresh changes nothing and is dropped
    as unchanged, so it is not counted among the refreshes whose direction
    is unknown. A refresh that survives with unknown activity still is."""
    env, src, dst = two_account_env(tmp_path)
    _transcripts(env, LG, LP, "sid-u")
    for store in (src, dst):
        _lrow(store, "local_g.json", LG, "Northwind board")
    _lrow(src, "local_p.json", LP, "Northwind kickoff", lastActivityAt=None,
          spawnedFrom=_side("local_g"))
    _lrow(dst, "local_p.json", LP, "Northwind kickoff", lastActivityAt=None,
          spawnedFrom={"sessionId": "local_g", "taskId": LTASK})
    _lrow(src, "local_u.json", "sid-u", "Northwind notes v2",
          lastActivityAt=None)
    _lrow(dst, "local_u.json", "sid-u", "Northwind notes",
          lastActivityAt=None)
    m = ct.plan_sync(env, ct.SyncFlags(update=True))
    t = m["tally"]
    assert [r["name"] for r in m["rows"]] == ["local_u.json"]
    assert sorted(t["unchanged"]) == ["Northwind board", "Northwind kickoff"]
    assert t["activity_unknown"] == ["Northwind notes v2"]


# ------------------------- Task 8: sync apply re-resolves once, under the lock
#
# docs/specs/2026-11-17-carry-lineage-design.md, "sync", "Applying, once":
# run_sync runs the lineage pass again after its guard and lock and before
# new_op, against the stores as they are then. Each row's source row is read
# again by name (the plan's Departure 2); only the link and the chain follow
# the stores at apply time, every other byte is the plan's snapshot.

def test_sync_apply_retargets_drops_or_gains_like_converge(two_account_env,
                                                           tmp_path):
    """Between the dry run and the apply the destination gains a row for
    C1's parent under the source's own filename (retargeted), loses its only
    row for C2's parent (dropped), and gains a row for C3's parent (gained).
    Each change is noted on its row as converge notes it, the plan's record
    is kept as lineage_planned, and the journal holds the final bytes."""
    env, src, dst = two_account_env(tmp_path)
    p2, p3, c2, c3 = "sid-parent-2", "sid-parent-3", "sid-child-2", "sid-child-3"
    _transcripts(env, LP, LC, p2, p3, c2, c3)
    _lrow(src, "local_p.json", LP, "Northwind kickoff")
    _lrow(src, "local_p2.json", p2, "Northwind board")
    _lrow(src, "local_p3.json", p3, "Northwind notes")
    _lrow(src, "local_c1.json", LC, "ACME-REVIEW one",
          spawnedFrom=_side("local_p"))
    _lrow(src, "local_c2.json", c2, "ACME-REVIEW two",
          spawnedFrom=_side("local_p2"))
    _lrow(src, "local_c3.json", c3, "ACME-REVIEW three",
          spawnedFrom=_side("local_p3"))
    _lrow(dst, "local_q.json", LP, "Northwind kickoff")
    _lrow(dst, "local_q2.json", p2, "Northwind board")
    m = ct.plan_sync(env, ct.SyncFlags(only="ACME"))
    by = _by_name(m)
    assert sorted(by) == ["local_c1.json", "local_c2.json", "local_c3.json"]
    assert _post(by["local_c1.json"])["spawnedFrom"]["sessionId"] == "local_q"
    assert _post(by["local_c2.json"])["spawnedFrom"]["sessionId"] == "local_q2"
    assert "spawnedFrom" not in _post(by["local_c3.json"])
    plan = {n: json.dumps(r["lineage"]) for n, r in by.items()}
    _lrow(dst, "local_p.json", LP, "Northwind kickoff")
    os.remove(os.path.join(dst, "local_q2.json"))
    _lrow(dst, "local_r3.json", p3, "Northwind notes")
    assert ct.run_sync(env, m) == "completed"
    op = ct.list_ops(env)[-1]
    on = _by_name(op.manifest)
    c1, c2r, c3r = on["local_c1.json"], on["local_c2.json"], on["local_c3.json"]
    assert _post(c1)["spawnedFrom"] == {"sessionId": "local_p", "taskId": LTASK}
    assert c1["lineage"]["parent_row"] == "local_p"
    assert _codes(c1) == ["lineage_retargeted"]
    assert "spawnedFrom" not in _post(c2r)
    assert c2r["lineage"]["parent_row"] == ""
    assert _codes(c2r) == ["parent_not_in_account", "lineage_dropped"]
    assert _post(c3r)["spawnedFrom"] == {"sessionId": "local_r3",
                                         "taskId": LTASK}
    assert _codes(c3r) == ["lineage_gained"]
    for r in (c1, c2r, c3r):
        assert json.dumps(r["lineage_planned"]) == plan[r["name"]]
    assert ct._lineage_summary(op.manifest["rows"])["changes"] == {
        "changed": 1, "dropped": 1, "gained": 1}
    assert ct._sync_shape_error(op.manifest) is None
    for r in op.manifest["rows"]:
        with open(r["dest_path"], "rb") as fh:
            assert fh.read() == ct.unb64(r["post_b64"])


@pytest.mark.parametrize("how", ["repointed", "deleted"])
def test_sync_apply_source_row_repointed_since_the_plan_keeps_no_link(
        two_account_env, tmp_path, how):
    """After the dry run the source's row for the child opens another
    conversation, or is gone. It is no holder any more: the row keeps
    neither its link nor its chain, says lineage_source_changed beside the
    chain and change codes, and every other byte is the plan's snapshot, the
    plan's title included. The after-apply lines count it by that reason."""
    env, src, dst = two_account_env(tmp_path)
    _transcripts(env, LP, LC, LQ)
    chain = {"firstSessionId": "local_p", "depth": 1}
    _lrow(src, "local_p.json", LP, "Northwind kickoff")
    _lrow(src, "local_c.json", LC, "ACME-REVIEW side task",
          spawnedFrom=_side("local_p"), startChain=chain)
    _lrow(dst, "local_q.json", LP, "Northwind kickoff")
    m = ct.plan_sync(env, ct.SyncFlags(only="ACME"))
    r, = m["rows"]
    planned_post = _post(r)
    assert planned_post["spawnedFrom"] == {"sessionId": "local_q",
                                           "taskId": LTASK}
    assert planned_post["startChain"] == {"firstSessionId": "local_q",
                                          "depth": 1}
    planned = json.dumps(r["lineage"])
    if how == "repointed":
        _lrow(src, "local_c.json", LQ, "ACME-REVIEW side task v2",
              spawnedFrom=_side("local_p"), startChain=chain)
    else:
        os.remove(os.path.join(src, "local_c.json"))
    assert ct.run_sync(env, m) == "completed"
    op = ct.list_ops(env)[-1]
    r, = op.manifest["rows"]
    post = _post(r)
    assert post == {k: v for k, v in planned_post.items()
                    if k not in ("spawnedFrom", "startChain")}
    assert list(post) == [k for k in planned_post
                          if k not in ("spawnedFrom", "startChain")]
    assert r["lineage"]["parent_row"] == "" and r["lineage"]["root_row"] == ""
    assert _codes(r) == ["lineage_source_changed", "lineage_chain_dropped",
                         "lineage_dropped"]
    for n in r["lineage"]["notes"]:
        assert "ACME" not in n["detail"] and "Northwind" not in n["detail"]
        assert LC not in n["detail"]
    assert LC[:8] in r["lineage"]["notes"][0]["detail"]
    assert json.dumps(r["lineage_planned"]) == planned
    assert ct._lineage_apply_lines(op.manifest["rows"]) == [
        "links   : 1 left out (the source row changed since the plan 1); "
        "since the plan: 1 dropped",
        "chains  : 1 left out"]
    assert ct._sync_shape_error(op.manifest) is None
    with open(r["dest_path"], "rb") as fh:
        assert fh.read() == ct.unb64(r["post_b64"])


def test_sync_apply_source_row_changed_on_an_unlinked_row_adds_no_note(
        two_account_env, tmp_path):
    """Two rows carry no link at the dry run: one whose source has none, and
    one whose link was left out (no row for the parent in the destination).
    Both source rows change before the apply. Neither named a parent or a
    root, so neither gets lineage_source_changed: each is written with the
    plan's bytes exactly, and has nothing to say about a link."""
    env, src, dst = two_account_env(tmp_path)
    _transcripts(env, LP, LC, LQ, LR)
    _lrow(src, "local_p.json", LP, "Northwind kickoff")
    _lrow(src, "local_a.json", LQ, "ACME-REVIEW plain")
    _lrow(src, "local_c.json", LC, "ACME-REVIEW side task",
          spawnedFrom=_side("local_p"))
    m = ct.plan_sync(env, ct.SyncFlags(only="ACME"))
    by = _by_name(m)
    assert by["local_a.json"]["lineage"] == ct._lineage_empty()
    assert _codes(by["local_c.json"]) == ["parent_not_in_account"]
    plan = {n: r["post_b64"] for n, r in by.items()}
    _lrow(src, "local_a.json", LR, "ACME-REVIEW plain")
    os.remove(os.path.join(src, "local_c.json"))
    assert ct.run_sync(env, m) == "completed"
    op = ct.list_ops(env)[-1]
    assert sorted(r["name"] for r in op.manifest["rows"]) == [
        "local_a.json", "local_c.json"]
    for r in op.manifest["rows"]:
        assert r["post_b64"] == plan[r["name"]]
        assert _codes(r) == []
        assert r["lineage"] == ct._lineage_empty()
        assert "lineage_planned" not in r
    assert ct._sync_shape_error(op.manifest) is None


def test_sync_verbatim_apply_skips_the_pass_and_keeps_order(two_account_env,
                                                            tmp_path,
                                                            monkeypatch):
    """--verbatim never runs the apply-time pass. The source's child loses
    its link after the dry run, and still the journal holds the plan's
    bytes, the link copied as it was, in selection's order (the newer child
    first), with no lineage on any row."""
    env, src, dst = two_account_env(tmp_path)
    _transcripts(env, LP, LC)
    link = _side("local_p")
    _lrow(src, "local_p.json", LP, "Northwind kickoff", lastActivityAt=1)
    _lrow(src, "local_c.json", LC, "ACME-REVIEW side task", lastActivityAt=5,
          spawnedFrom=link)
    m = ct.plan_sync(env, ct.SyncFlags(verbatim=True))
    plan = [(r["name"], r["post_b64"]) for r in m["rows"]]
    assert [n for n, _ in plan] == ["local_c.json", "local_p.json"]
    _lrow(src, "local_c.json", LC, "ACME-REVIEW side task", lastActivityAt=5)
    calls = []
    real = ct._sync_lineage

    def spy(*a, **k):
        calls.append(1)
        return real(*a, **k)
    monkeypatch.setattr(ct, "_sync_lineage", spy)
    assert ct.run_sync(env, m) == "completed"
    assert calls == []
    op = ct.list_ops(env)[-1]
    assert [(r["name"], r["post_b64"]) for r in op.manifest["rows"]] == plan
    assert _post(op.manifest["rows"][0])["spawnedFrom"] == link
    for r in op.manifest["rows"]:
        assert "lineage" not in r and "lineage_planned" not in r
    assert ct._sync_shape_error(op.manifest) is None


def test_sync_apply_drops_a_refresh_that_became_a_no_op(two_account_env,
                                                        tmp_path):
    """At the dry run the child's refresh changes only its link. The source
    row loses that link before the apply, so the refresh would now write the
    bytes the destination's row already holds: it is dropped from the op,
    and counted as unchanged. The journal never holds a refresh whose
    post_b64 equals its pre_b64, and the other refresh is written."""
    env, src, dst = two_account_env(tmp_path)
    _transcripts(env, LP, LC, LQ)
    for store in (src, dst):
        _lrow(store, "local_p.json", LP, "Northwind kickoff")
    _lrow(src, "local_c.json", LC, "ACME-REVIEW side task",
          spawnedFrom=_side("local_p"), model="m")
    _lrow(dst, "local_c.json", LC, "ACME-REVIEW side task", model="m")
    _lrow(src, "local_n.json", LQ, "Northwind notes v2", lastActivityAt=2)
    _lrow(dst, "local_n.json", LQ, "Northwind notes")
    m = ct.plan_sync(env, ct.SyncFlags(update=True))
    assert sorted(r["name"] for r in m["rows"]) == ["local_c.json",
                                                    "local_n.json"]
    with open(os.path.join(dst, "local_c.json"), "rb") as fh:
        child_bytes = fh.read()
    _lrow(src, "local_c.json", LC, "ACME-REVIEW side task", model="m")
    assert ct.run_sync(env, m) == "completed"
    assert [r["name"] for r in m["rows"]] == ["local_n.json"]
    op = ct.list_ops(env)[-1]
    assert [r["name"] for r in op.manifest["rows"]] == ["local_n.json"]
    for r in op.manifest["rows"]:
        assert r["post_b64"] != r["pre_b64"]
    with open(os.path.join(dst, "local_c.json"), "rb") as fh:
        assert fh.read() == child_bytes
    assert "ACME-REVIEW side task" in m["tally"]["unchanged"]
    assert ct._sync_shape_error(op.manifest) is None


def test_sync_apply_dropped_parent_turns_a_kept_childs_link_to_existing(
        two_account_env, tmp_path):
    """The parent is itself a side session, and its refresh only adds the
    link to the grandparent, so the child added beside it names the
    parent's row as planned. The parent's source row loses that link before
    the apply: its refresh is dropped, and the child, kept, now names the
    parent's row as existing. The journal passes the shape check, and the
    op, interrupted after the child landed, recovers forward and undoes."""
    env, src, dst = two_account_env(tmp_path)
    _transcripts(env, LG, LP, LC)
    for store in (src, dst):
        _lrow(store, "local_g.json", LG, "Northwind board")
    _lrow(src, "local_p.json", LP, "Northwind kickoff",
          spawnedFrom=_side("local_g"))
    _lrow(dst, "local_p.json", LP, "Northwind kickoff")
    _lrow(src, "local_c.json", LC, "ACME-REVIEW side task",
          spawnedFrom=_side("local_p"))
    m = ct.plan_sync(env, ct.SyncFlags(update=True))
    by = _by_name(m)
    assert sorted(by) == ["local_c.json", "local_p.json"]
    assert by["local_c.json"]["lineage"]["parent_planned"] is True
    with open(os.path.join(dst, "local_p.json"), "rb") as fh:
        parent_bytes = fh.read()
    _lrow(src, "local_p.json", LP, "Northwind kickoff")

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
    c, = op.manifest["rows"]
    assert c["name"] == "local_c.json"
    assert _post(c)["spawnedFrom"] == {"sessionId": "local_p", "taskId": LTASK}
    assert c["lineage"]["parent_row"] == "local_p"
    assert c["lineage"]["parent_planned"] is False
    assert "lineage_planned" not in c
    assert ct._sync_shape_error(op.manifest) is None
    assert "forward" in ct.classify_op(env, op)["resolutions"]
    assert ct.recover_op(env, op, "forward") == "completed"
    assert ct.undo_sync(env, op) == "undone"
    assert not os.path.exists(os.path.join(dst, "local_c.json"))
    with open(os.path.join(dst, "local_p.json"), "rb") as fh:
        assert fh.read() == parent_bytes


@pytest.mark.parametrize("target", ["parent", "root"])
@pytest.mark.parametrize("how", ["deleted", "repointed"])
def test_sync_apply_no_op_refresh_whose_row_was_deleted_or_repointed_refuses(
        two_account_env, tmp_path, target, how):
    """The parent's (or the chain root's) refresh only adds a link at the dry
    run, and the child added beside it names that row as planned. Before the
    apply the source loses that link, so the refresh would write nothing -
    and the destination's row is gone, or opens another conversation.
    Equality with the plan's pre-image says nothing about the store now, so
    run_sync refuses before anything is journaled, and no child is
    written."""
    env, src, dst = two_account_env(tmp_path)
    _transcripts(env, LG, LP, LC, LQ, LR)
    for store in (src, dst):
        _lrow(store, "local_g.json", LG, "Northwind board")
        _lrow(store, "local_x.json", LR, "Northwind other parent")
    _lrow(src, "local_p.json", LP, "Northwind kickoff",
          spawnedFrom=_side("local_g"))
    _lrow(dst, "local_p.json", LP, "Northwind kickoff")
    if target == "parent":
        _lrow(src, "local_c.json", LC, "ACME-REVIEW side task",
              spawnedFrom=_side("local_p"))
    else:
        _lrow(src, "local_c.json", LC, "ACME-REVIEW side task",
              spawnedFrom=_side("local_x"),
              startChain={"firstSessionId": "local_p", "depth": 2})
    m = ct.plan_sync(env, ct.SyncFlags(update=True))
    lin = _by_name(m)["local_c.json"]["lineage"]
    assert lin["parent_planned" if target == "parent" else "root_planned"] \
        is True
    _lrow(src, "local_p.json", LP, "Northwind kickoff")
    if how == "deleted":
        os.remove(os.path.join(dst, "local_p.json"))
    else:
        _lrow(dst, "local_p.json", LQ, "Northwind kickoff")
    with pytest.raises(ct.Refusal) as exc:
        ct.run_sync(env, m)
    assert str(exc.value) == ("the destination's row local_p changed since "
                              "the plan; plan again")
    assert ct.list_ops(env) == []
    assert not os.path.exists(os.path.join(dst, "local_c.json"))
    assert ct.read_lock(env) is None


@pytest.mark.parametrize("as_json", [False, True])
def test_sync_apply_that_leaves_nothing_journals_no_op_and_exits_0(
        two_account_env, tmp_path, monkeypatch, capsys, as_json):
    """The plan's one row is a refresh that changes only its link. The source
    loses that link after the dry run, so the apply-time pass leaves nothing
    to write: no op is journaled and the run is a success. Under --json it
    says `result: "unchanged"`; otherwise it prints the line below and no
    `copied :` line. The account-email memo is still written."""
    env, src, dst = two_account_env(tmp_path)
    _transcripts(env, LP, LC)
    for store in (src, dst):
        _lrow(store, "local_p.json", LP, "Northwind kickoff")
    _lrow(src, "local_c.json", LC, "ACME-REVIEW side task",
          spawnedFrom=_side("local_p"))
    _lrow(dst, "local_c.json", LC, "ACME-REVIEW side task")
    with open(os.path.join(dst, "local_c.json"), "rb") as fh:
        child_bytes = fh.read()
    real, real_memo = ct.run_sync, ct.remember_account_email
    memo = []

    def late(env_, m_):
        assert [r["name"] for r in m_["rows"]] == ["local_c.json"]
        _lrow(src, "local_c.json", LC, "ACME-REVIEW side task")
        return real(env_, m_)

    def remember(*a):
        memo.append(a[1:])
        return real_memo(*a)
    monkeypatch.setattr(ct, "run_sync", late)
    monkeypatch.setattr(ct, "remember_account_email", remember)
    argv = ["--update", "--apply"] + (["--json"] if as_json else [])
    rc, lines = _sync_main(env, monkeypatch, capsys, *argv)
    assert rc == 0
    assert ct.list_ops(env) == []
    assert memo == [("aaaaaaaa-0000-0000-0000-000000000001",
                     "me@example.com")]
    with open(os.path.join(dst, "local_c.json"), "rb") as fh:
        assert fh.read() == child_bytes
    if as_json:
        rep = json.loads("\n".join(lines))
        assert rep["result"] == "unchanged"
        assert rep["rows"] == []
        assert "ACME-REVIEW side task" in rep["tally"]["unchanged"]
    else:
        assert "nothing to write: the destination already holds these rows" \
            in lines
        assert not any(ln.startswith("copied") for ln in lines)
        assert not any(ln.startswith("refreshed") for ln in lines)


def test_sync_after_apply_trailer_prints_the_links_lines(
        two_account_env, tmp_path, monkeypatch, capsys):
    """The after-apply lines sit beside `copied :` and `refreshed :`, over
    the written rows: carried, left out by the underlying reason, then what
    changed since the plan, and the chains. The dry-run lines above them
    still say what the plan would carry."""
    env, src, dst = two_account_env(tmp_path)
    p2, c2 = "sid-parent-2", "sid-child-2"
    _transcripts(env, LP, LC, p2, c2, LQ)
    for store in (src, dst):
        _lrow(store, "local_p.json", LP, "Northwind kickoff")
        _lrow(store, "local_p2.json", p2, "Northwind board")
    _lrow(src, "local_c.json", LC, "ACME-REVIEW one",
          spawnedFrom=_side("local_p"),
          startChain={"firstSessionId": "local_p", "depth": 1})
    _lrow(src, "local_d.json", c2, "ACME-REVIEW two",
          spawnedFrom=_side("local_p2"))
    _lrow(src, "local_n.json", LQ, "Northwind notes v2", lastActivityAt=2)
    _lrow(dst, "local_n.json", LQ, "Northwind notes")
    real = ct.run_sync

    def late(env_, m_):
        os.remove(os.path.join(dst, "local_p2.json"))   # D's parent row goes
        return real(env_, m_)
    monkeypatch.setattr(ct, "run_sync", late)
    rc, lines = _sync_main(env, monkeypatch, capsys, "--update", "--apply")
    assert rc == 0
    assert "links   : 2 to carry" in lines
    assert "chains  : 1 to carry" in lines
    i = lines.index("copied     : 2")
    assert lines[i + 1:i + 5] == [
        "refreshed  : 1",
        "links   : 1 carried, 1 left out (no row for the parent in that "
        "account 1); since the plan: 1 dropped",
        "chains  : 1 carried",
        "result     : completed"]


def test_sync_journal_holds_the_final_bytes_and_order(two_account_env,
                                                      tmp_path, monkeypatch):
    """At the dry run the child's source row carries no link, so the newer
    child keeps selection's place ahead of its parent. The source row gains
    the link before the apply: the child now names the parent's planned row,
    so the rows are re-sorted before new_op. The journal holds the final
    bytes in the final order, the parent is written first, and a cycle of
    links the destination gained meanwhile is counted again."""
    env, src, dst = two_account_env(tmp_path)
    _transcripts(env, LP, LC, LQ, LR)
    _lrow(src, "local_p.json", LP, "Northwind kickoff", lastActivityAt=1)
    _lrow(src, "local_c.json", LC, "ACME-REVIEW side task", lastActivityAt=5)
    m = ct.plan_sync(env, ct.SyncFlags())
    assert [r["name"] for r in m["rows"]] == ["local_c.json", "local_p.json"]
    assert m["lineage_cycles"] == 0
    plan_c = json.dumps(_by_name(m)["local_c.json"]["lineage"])
    _lrow(src, "local_c.json", LC, "ACME-REVIEW side task", lastActivityAt=5,
          spawnedFrom=_side("local_p"))
    _lrow(dst, "local_x.json", LQ, "Loop one", spawnedFrom=_side("local_y"))
    _lrow(dst, "local_y.json", LR, "Loop two", spawnedFrom=_side("local_x"))
    journaled, written = {}, []
    real_new_op, real_write = ct.new_op, ct.atomic_write

    def spy_new_op(env_, manifest):
        journaled["rows"] = [(r["name"], r["post_b64"])
                             for r in manifest["rows"]]
        journaled["cycles"] = manifest["lineage_cycles"]
        return real_new_op(env_, manifest)

    def spy_write(path, data):
        name = os.path.basename(path)
        if name.startswith("local_"):          # rows only, not the journal
            written.append(name)
        return real_write(path, data)
    monkeypatch.setattr(ct, "new_op", spy_new_op)
    monkeypatch.setattr(ct, "atomic_write", spy_write)
    assert ct.run_sync(env, m) == "completed"
    assert [n for n, _ in journaled["rows"]] == ["local_p.json",
                                                 "local_c.json"]
    c = _by_name(m)["local_c.json"]
    assert _post(c)["spawnedFrom"] == {"sessionId": "local_p", "taskId": LTASK}
    assert c["lineage"]["parent_row"] == "local_p"
    assert c["lineage"]["parent_planned"] is True
    assert _codes(c) == ["lineage_gained"]
    assert json.dumps(c["lineage_planned"]) == plan_c
    assert journaled["cycles"] == 1
    on_record = ct.list_ops(env)[-1].manifest
    assert on_record["lineage_cycles"] == 1
    assert [(r["name"], r["post_b64"]) for r in on_record["rows"]] == \
        journaled["rows"]
    assert written == ["local_p.json", "local_c.json"]
    for r in on_record["rows"]:
        with open(r["dest_path"], "rb") as fh:
            assert fh.read() == ct.unb64(r["post_b64"])
    assert ct._sync_shape_error(on_record) is None


# ----------------------------------- Task 9: sync recovery and reversal
#
# docs/specs/2026-11-17-carry-lineage-design.md, "sync", "Recovery has no
# per-row skip, and needs none", and "undo": classify_sync_op treats a
# pending row whose journaled link or chain names a row that is gone or
# opens another conversation as drift, so only 'back' is offered; undo and
# recover --back reverse every row in reverse write order, children before
# parents, adds and refreshes in one sequence; and forward recovery, undo
# and back each end with the link report, saved to the journal.

EMPTY_LINKS = {"dangling": [], "redirected": [], "unchecked": 0}
LINK_LINES = ("link left", "link redirected", "links not checked")


def _sync_crash(env, m, point="sync-mid-write", nth=1):
    """Apply M and crash the NTH time POINT fires. Returns the stuck op."""
    seen = {"n": 0}

    def hook(p):
        if p == point:
            seen["n"] += 1
            if seen["n"] == nth:
                raise SimulatedCrash()
    ct._crash_hook = hook
    try:
        with pytest.raises(SimulatedCrash):
            ct.run_sync(env, m)
    finally:
        ct._crash_hook = None
    op = ct.nonterminal_ops(env)[0]
    assert op.manifest["status"] == "writing"
    return op


def _sync_crash_before_any_row(env, m, monkeypatch):
    """Apply M and crash at its first row write, before anything lands."""
    real = ct.atomic_write

    def boom(path, data):
        if os.path.basename(path).startswith("local_"):
            raise SimulatedCrash()
        return real(path, data)
    monkeypatch.setattr(ct, "atomic_write", boom)
    with pytest.raises(SimulatedCrash):
        ct.run_sync(env, m)
    monkeypatch.setattr(ct, "atomic_write", real)
    op = ct.nonterminal_ops(env)[0]
    assert op.manifest["status"] == "writing"
    assert not any(r.get("written") for r in op.manifest["rows"])
    return op


def _journal_of(env, op):
    """The op's manifest as it is on disk."""
    with open(os.path.join(env.ops_dir, op.manifest["op_id"],
                           "manifest.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _recover_ns(op, direction):
    import types
    return types.SimpleNamespace(op_id=op.manifest["op_id"],
                                 forward=direction == "forward",
                                 back=direction == "back", apply=True,
                                 verbose=True)


def _undo_ns(op):
    import types
    return types.SimpleNamespace(show=False, op_id=op.manifest["op_id"],
                                 apply=True, verbose=True)


def _made_0_17(op):
    """Rewrite a journaled sync op as 0.17.0 wrote it: no lineage fields."""
    m = op.manifest
    for k in ("lineage_format", "lineage_cycles"):
        m.pop(k, None)
    for r in m["rows"]:
        r.pop("lineage", None)
        r.pop("lineage_planned", None)
    ct.save_manifest(op)
    assert ct._sync_shape_error(m) is None


def _touch(path, **changes):
    """Rewrite a row in place, keys kept in order: the app on focus, or a
    repoint when CHANGES names cliSessionId."""
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    d.update(changes)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(d, separators=(",", ":")))


def _bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def _added_parent_and_child(env, src):
    """P (local_p) and its side session C (local_c), both added by one sync
    into an empty destination: the parent first, in write order, and the
    child's link names the parent's planned row."""
    _transcripts(env, LP, LC)
    _lrow(src, "local_p.json", LP, "Northwind kickoff", lastActivityAt=1)
    _lrow(src, "local_c.json", LC, "ACME-REVIEW side task", lastActivityAt=5,
          spawnedFrom=_side("local_p"))
    m = ct.plan_sync(env, ct.SyncFlags())
    assert [r["name"] for r in m["rows"]] == ["local_p.json", "local_c.json"]
    assert m["rows"][1]["lineage"]["parent_planned"] is True
    return m


@pytest.mark.parametrize("fmt", ["lineage", "0.17"])
@pytest.mark.parametrize("how", ["deleted", "repointed"])
def test_sync_forward_reports_a_landed_childs_parent_deleted_or_repointed(
        two_account_env, tmp_path, capsys, how, fmt):
    """The child landed before the crash, linked to the parent's existing
    row; that row is then deleted or repointed. classify checks pending rows
    only, so forward is still offered, and it finishes the other row and
    ends with the link report naming the landed child. A journal from 0.17.0
    recovers exactly as before, with no report at all."""
    env, src, dst = two_account_env(tmp_path)
    _transcripts(env, LP, LC, LR)
    _lrow(src, "local_p.json", LP, "Northwind kickoff")
    _lrow(src, "local_c.json", LC, "ACME-REVIEW side task", lastActivityAt=9,
          spawnedFrom=_side("local_p"))
    _lrow(src, "local_n.json", LR, "ACME-REVIEW notes", lastActivityAt=1)
    _lrow(dst, "local_q.json", LP, "Northwind kickoff")
    m = ct.plan_sync(env, ct.SyncFlags(only="ACME"))
    assert [r["name"] for r in m["rows"]] == ["local_c.json", "local_n.json"]
    op = _sync_crash(env, m)
    child, other = op.manifest["rows"]
    assert child["written"] and not other["written"]
    assert _post(child)["spawnedFrom"]["sessionId"] == "local_q"
    if fmt == "0.17":
        _made_0_17(op)
    parent = os.path.join(dst, "local_q.json")
    if how == "deleted":
        os.remove(parent)
    else:
        _touch(parent, cliSessionId=LQ)
    assert ct.classify_op(env, op)["resolutions"] == ["forward", "back"]
    assert ct.cmd_recover(env, _recover_ns(op, "forward")) == 0
    out = capsys.readouterr().out.splitlines()
    assert "result: completed" in out
    assert _bytes(other["dest_path"]) == ct.unb64(other["post_b64"])
    saved = _journal_of(env, op)
    assert saved["status"] == "completed"
    if fmt == "0.17":
        assert "link_report" not in saved
        assert not any(x.startswith(LINK_LINES) for x in out)
        return
    entry = [{"row": "local_c", "target": "local_q", "kind": "parent"}]
    if how == "deleted":
        want = dict(EMPTY_LINKS, dangling=entry)
        line = "link left dangling: local_c (its parent's row was removed)"
    else:
        want = dict(EMPTY_LINKS, redirected=entry)
        line = ("link redirected: local_c (its parent's row now opens another "
                "conversation)")
    assert saved["link_report"] == want
    assert line in out
    assert out.index("result: completed") < out.index(line)


def test_sync_forward_all_landed_still_reports_links(two_account_env,
                                                     tmp_path, monkeypatch):
    """Every row landed and only the last one's flag and the completion
    marker are missing. Forward finishes the bookkeeping and still ends
    with the link report: one landed child's parent row deleted (dangling),
    the other's repointed (redirected). The report is on disk before the
    journal is rotated."""
    env, src, dst = two_account_env(tmp_path)
    p2, c2 = "sid-parent-2", "sid-child-2"
    _transcripts(env, LP, LC, p2, c2, LR)
    _lrow(src, "local_p.json", LP, "Northwind kickoff")
    _lrow(src, "local_p2.json", p2, "Northwind board")
    _lrow(src, "local_c.json", LC, "ACME-REVIEW one", lastActivityAt=9,
          spawnedFrom=_side("local_p"))
    _lrow(src, "local_d.json", c2, "ACME-REVIEW two", lastActivityAt=8,
          spawnedFrom=_side("local_p2"))
    _lrow(src, "local_n.json", LR, "ACME-REVIEW notes", lastActivityAt=1)
    _lrow(dst, "local_q.json", LP, "Northwind kickoff")
    _lrow(dst, "local_q2.json", p2, "Northwind board")
    m = ct.plan_sync(env, ct.SyncFlags(only="ACME"))
    assert [r["name"] for r in m["rows"]] == ["local_c.json", "local_d.json",
                                              "local_n.json"]
    op = _sync_crash(env, m, point="sync-write-before-save", nth=3)
    rows = op.manifest["rows"]
    assert [r["written"] for r in rows] == [True, True, False]
    for r in rows:
        assert _bytes(r["dest_path"]) == ct.unb64(r["post_b64"])
    os.remove(os.path.join(dst, "local_q.json"))
    _touch(os.path.join(dst, "local_q2.json"), cliSessionId=LQ)
    assert ct.classify_op(env, op)["resolutions"] == ["forward", "back"]
    at_rotate = {}
    real = ct.rotate_ops

    def spy(env_):
        at_rotate.update(_journal_of(env, op))
        return real(env_)
    monkeypatch.setattr(ct, "rotate_ops", spy)
    assert ct.recover_op(env, op, "forward") == "completed"
    want = {"dangling": [{"row": "local_c", "target": "local_q",
                          "kind": "parent"}],
            "redirected": [{"row": "local_d", "target": "local_q2",
                            "kind": "parent"}],
            "unchecked": 0}
    assert op.manifest["link_report"] == want
    assert at_rotate["status"] == "completed"
    assert at_rotate["link_report"] == want


def test_sync_crash_before_the_parent_add_landed_forward_is_offered(
        two_account_env, tmp_path, monkeypatch):
    """Nothing landed. The child's target is the parent's planned row,
    still pending, so it is not checked - it is written first: forward and
    back are both offered, and forward writes the parent, then the child
    with its journaled link, and reports nothing."""
    env, src, dst = two_account_env(tmp_path)
    m = _added_parent_and_child(env, src)
    op = _sync_crash_before_any_row(env, m, monkeypatch)
    assert ct.classify_op(env, op)["resolutions"] == ["forward", "back"]
    written = []
    real = ct.atomic_write

    def spy(path, data):
        if os.path.basename(path).startswith("local_"):
            written.append(os.path.basename(path))
        return real(path, data)
    monkeypatch.setattr(ct, "atomic_write", spy)
    assert ct.recover_op(env, op, "forward") == "completed"
    assert written == ["local_p.json", "local_c.json"]
    with open(os.path.join(dst, "local_c.json"), encoding="utf-8") as fh:
        assert json.load(fh)["spawnedFrom"] == {"sessionId": "local_p",
                                                "taskId": LTASK}
    assert op.manifest["link_report"] == EMPTY_LINKS


def test_sync_crash_between_parent_and_child_forward_finishes(
        two_account_env, tmp_path, monkeypatch):
    """The parent's add landed and was recorded; the child did not. The
    parent's row, written by this op, is checked and still opens the
    parent, so forward is offered. It writes the child with its journaled
    bytes, never resolving again, and reports nothing."""
    env, src, dst = two_account_env(tmp_path)
    m = _added_parent_and_child(env, src)
    op = _sync_crash(env, m)
    parent, child = op.manifest["rows"]
    assert parent["written"] and not child["written"]
    journaled = child["post_b64"]
    assert ct.classify_op(env, op)["resolutions"] == ["forward", "back"]
    calls = []
    real = ct._sync_lineage

    def spy(*a, **k):
        calls.append(1)
        return real(*a, **k)
    monkeypatch.setattr(ct, "_sync_lineage", spy)
    assert ct.recover_op(env, op, "forward") == "completed"
    assert calls == []
    assert child["post_b64"] == journaled
    assert _bytes(child["dest_path"]) == ct.unb64(journaled)
    assert op.manifest["link_report"] == EMPTY_LINKS


@pytest.mark.parametrize("target", ["parent", "root"])
def test_sync_parent_written_then_repointed_withdraws_forward(
        two_account_env, tmp_path, target):
    """The parent's (or the chain root's) add landed before the crash and
    was repointed at another conversation since. The pending child names
    it, so forward is withdrawn and 'back' alone is offered, with a note
    naming the child's row; forward refuses, and the child is never
    written with the stale link."""
    env, src, dst = two_account_env(tmp_path)
    if target == "parent":
        m = _added_parent_and_child(env, src)
        named = "local_p.json"
    else:
        _transcripts(env, LR, LG, LC)
        for store in (src, dst):
            _lrow(store, "local_x.json", LG, "Northwind other parent")
        _lrow(src, "local_r.json", LR, "Northwind root", lastActivityAt=1)
        _lrow(src, "local_c.json", LC, "ACME-REVIEW side task",
              lastActivityAt=5, spawnedFrom=_side("local_x"),
              startChain={"firstSessionId": "local_r", "depth": 2})
        m = ct.plan_sync(env, ct.SyncFlags())
        assert [r["name"] for r in m["rows"]] == ["local_r.json",
                                                  "local_c.json"]
        lin = m["rows"][1]["lineage"]
        assert lin["root_row"] == "local_r" and lin["root_planned"] is True
        assert lin["parent_row"] == "local_x" and not lin["parent_planned"]
        named = "local_r.json"
    op = _sync_crash(env, m)
    first, child = op.manifest["rows"]
    assert first["name"] == named and first["written"]
    assert not child["written"]
    _touch(first["dest_path"], cliSessionId=LQ)
    c = ct.classify_op(env, op)
    assert c["resolutions"] == ["back"]
    # The link's clause names the pending row by its id and conversations
    # by their first 8 characters, never a title. (The written row that
    # drifted is named by title, as the drift clause always has.)
    what, sid = ("chain root", LR) if target == "root" else ("parent", LP)
    assert ("the journaled link of pending row(s) no longer matches its "
            "target (local_c: its {0}'s row now opens {1}, not {2})".format(
                what, LQ[:8], sid[:8])) in c["note"]
    with pytest.raises(ct.Refusal):
        ct.recover_op(env, op, "forward")
    assert not os.path.exists(child["dest_path"])
    assert ct.recover_op(env, op, "back") == "rolled_back"
    assert not os.path.exists(child["dest_path"])


@pytest.mark.parametrize("fmt", ["lineage", "0.17"])
@pytest.mark.parametrize("how", ["deleted", "repointed"])
def test_sync_existing_parent_outside_the_op_deleted_withdraws_forward(
        two_account_env, tmp_path, monkeypatch, how, fmt):
    """The child links to a row the destination already held, outside the
    op. That row is deleted, or repointed, after the crash: forward is
    withdrawn and the child never written. A journal from 0.17.0 has no
    dependency checks: forward is offered and writes the child as before."""
    env, src, dst = two_account_env(tmp_path)
    _transcripts(env, LP, LC)
    _lrow(src, "local_p.json", LP, "Northwind kickoff")
    _lrow(src, "local_c.json", LC, "ACME-REVIEW side task",
          spawnedFrom=_side("local_p"))
    _lrow(dst, "local_q.json", LP, "Northwind kickoff")
    m = ct.plan_sync(env, ct.SyncFlags(only="ACME"))
    child, = m["rows"]
    assert child["lineage"]["parent_row"] == "local_q"
    assert child["lineage"]["parent_planned"] is False
    op = _sync_crash_before_any_row(env, m, monkeypatch)
    child, = op.manifest["rows"]
    if fmt == "0.17":
        _made_0_17(op)
    parent = os.path.join(dst, "local_q.json")
    if how == "deleted":
        os.remove(parent)
    else:
        _touch(parent, cliSessionId=LQ)
    c = ct.classify_op(env, op)
    if fmt == "0.17":
        assert c["resolutions"] == ["forward", "back"]
        assert ct.recover_op(env, op, "forward") == "completed"
        assert _bytes(child["dest_path"]) == ct.unb64(child["post_b64"])
        assert "link_report" not in op.manifest
        return
    assert c["resolutions"] == ["back"]
    assert "local_c" in c["note"]
    with pytest.raises(ct.Refusal):
        ct.recover_op(env, op, "forward")
    assert not os.path.exists(child["dest_path"])


@pytest.mark.parametrize("how", ["deleted", "repointed"])
@pytest.mark.parametrize("kind", ["age-held", "unchanged"])
def test_sync_age_held_or_unchanged_parent_changed_withdraws_forward(
        two_account_env, tmp_path, monkeypatch, kind, how):
    """The child links to the parent's own row, which this sync does not
    write: --newer-only holds its refresh (the destination's copy is newer),
    or it is dropped as unchanged. That row is deleted or repointed after
    the crash: forward is withdrawn and the child never written."""
    env, src, dst = two_account_env(tmp_path)
    _transcripts(env, LP, LC)
    if kind == "age-held":
        _lrow(src, "local_p.json", LP, "Northwind kickoff v2",
              lastActivityAt=1)
        _lrow(dst, "local_p.json", LP, "Northwind kickoff", lastActivityAt=9)
        flags = ct.SyncFlags(update=True, newer_only=True)
    else:
        for store in (src, dst):
            _lrow(store, "local_p.json", LP, "Northwind kickoff")
        flags = ct.SyncFlags(update=True)
    _lrow(src, "local_c.json", LC, "ACME-REVIEW side task", lastActivityAt=5,
          spawnedFrom=_side("local_p"))
    m = ct.plan_sync(env, flags)
    held = "held_older" if kind == "age-held" else "unchanged"
    assert len(m["tally"][held]) == 1
    child, = m["rows"]
    assert child["lineage"]["parent_row"] == "local_p"
    assert child["lineage"]["parent_planned"] is False
    op = _sync_crash_before_any_row(env, m, monkeypatch)
    parent = os.path.join(dst, "local_p.json")
    if how == "deleted":
        os.remove(parent)
    else:
        _touch(parent, cliSessionId=LQ)
    c = ct.classify_op(env, op)
    assert c["resolutions"] == ["back"]
    assert "local_c" in c["note"]
    with pytest.raises(ct.Refusal):
        ct.recover_op(env, op, "forward")
    assert not os.path.exists(os.path.join(dst, "local_c.json"))


@pytest.mark.parametrize("leg", ["undo", "back"])
def test_sync_reversal_restores_a_refreshed_child_before_removing_an_added_parent(
        two_account_env, tmp_path, monkeypatch, leg):
    """One op added the parent's row and refreshed the child's row to link
    to it, in that order. The reversal goes in reverse write order, one
    sequence for both kinds: the child is restored first, then the parent
    removed. Cut short between the two, it leaves the parent without the
    child's link - never a child naming a row already removed - and a
    second run finishes it."""
    env, src, dst = two_account_env(tmp_path)
    _transcripts(env, LP, LC)
    _lrow(src, "local_p.json", LP, "Northwind kickoff", lastActivityAt=1)
    _lrow(src, "local_c.json", LC, "ACME-REVIEW side task v2",
          lastActivityAt=5, spawnedFrom=_side("local_p"))
    _lrow(dst, "local_c.json", LC, "ACME-REVIEW side task", lastActivityAt=5)
    pre = _bytes(os.path.join(dst, "local_c.json"))
    m = ct.plan_sync(env, ct.SyncFlags(update=True))
    by = _by_name(m)
    assert [r["name"] for r in m["rows"]] == ["local_p.json", "local_c.json"]
    assert by["local_c.json"]["is_update"] and not by["local_p.json"].get(
        "is_update")
    assert by["local_c.json"]["lineage"]["parent_planned"] is True
    if leg == "undo":
        assert ct.run_sync(env, m) == "completed"
        op = ct.list_ops(env)[-1]
    else:
        op = _sync_crash(env, m, point="sync-write-before-save", nth=2)
    steps = []
    real_unlink, real_write = os.unlink, ct.atomic_write

    def unlink(path, *a, **k):
        name = os.path.basename(str(path))
        if name.startswith("local_"):
            steps.append(("remove", name))
            raise SimulatedCrash()          # the reversal is cut short here
        return real_unlink(path, *a, **k)

    def write(path, data):
        name = os.path.basename(path)
        if name.startswith("local_"):
            steps.append(("restore", name))
        return real_write(path, data)
    monkeypatch.setattr(ct.os, "unlink", unlink)
    monkeypatch.setattr(ct, "atomic_write", write)
    with pytest.raises(SimulatedCrash):
        if leg == "undo":
            ct.undo_sync(env, op)
        else:
            ct.recover_op(env, op, "back")
    monkeypatch.undo()
    assert steps == [("restore", "local_c.json"), ("remove", "local_p.json")]
    assert _bytes(os.path.join(dst, "local_c.json")) == pre
    assert os.path.exists(os.path.join(dst, "local_p.json"))
    if leg == "undo":
        assert ct.undo_sync(env, op) == "undone"
    else:
        assert ct.recover_op(env, op, "back") == "rolled_back"
    assert not os.path.exists(os.path.join(dst, "local_p.json"))
    assert _bytes(os.path.join(dst, "local_c.json")) == pre
    assert op.manifest["link_report"] == EMPTY_LINKS


def _swapped_parent(env, src, dst, child_title="ACME-REVIEW side task",
                    child_in_dst=False):
    """The destination's row local_p opens another conversation (LQ), which
    its row local_y still opens, so a refresh may move local_p onto the
    parent (LP) without orphaning anything. The source's child links to
    local_p. With CHILD_IN_DST the destination already holds the child's
    row, linking to its own local_p, under an older title."""
    _transcripts(env, LP, LC, LQ, LR)
    _lrow(dst, "local_p.json", LQ, "ACME-REVIEW kickoff")
    _lrow(dst, "local_y.json", LQ, "Northwind other thread")
    _lrow(src, "local_p.json", LP, "ACME-REVIEW kickoff", lastActivityAt=3)
    _lrow(src, "local_c.json", LC, child_title, lastActivityAt=5,
          spawnedFrom=_side("local_p"))
    if child_in_dst:
        _lrow(dst, "local_c.json", LC, "ACME-REVIEW side task",
              lastActivityAt=5, spawnedFrom=_side("local_p"))


def test_sync_back_with_a_drifted_child_still_carrying_our_link_reports_redirected(
        two_account_env, tmp_path, capsys):
    """One op moved the destination's row local_p onto the parent and added
    the child linking to it; the app then touched the child, which still
    carries that link. recover --back leaves the drifted child in place and
    restores local_p to the conversation it opened before: the report names
    the child's link as redirected, after the line saying what back left."""
    env, src, dst = two_account_env(tmp_path)
    _swapped_parent(env, src, dst)
    _lrow(src, "local_n.json", LR, "ACME-REVIEW notes", lastActivityAt=1)
    pre = _bytes(os.path.join(dst, "local_p.json"))
    m = ct.plan_sync(env, ct.SyncFlags(update=True, only="ACME"))
    by = _by_name(m)
    assert [r["name"] for r in m["rows"]] == ["local_p.json", "local_c.json",
                                              "local_n.json"]
    assert by["local_p.json"]["swaps_conversation"] is True
    assert by["local_c.json"]["lineage"]["parent_planned"] is True
    op = _sync_crash(env, m, nth=2)
    parent, child, other = op.manifest["rows"]
    assert parent["written"] and child["written"] and not other["written"]
    _touch(child["dest_path"], lastFocusedAt=1_900_000_000_000)
    assert ct.classify_op(env, op)["resolutions"] == ["forward", "back"]
    assert ct.cmd_recover(env, _recover_ns(op, "back")) == 0
    out = capsys.readouterr().out.splitlines()
    assert "result: rolled_back" in out
    assert _bytes(parent["dest_path"]) == pre
    with open(child["dest_path"], encoding="utf-8") as fh:
        assert json.load(fh)["spawnedFrom"]["sessionId"] == "local_p"
    saved = _journal_of(env, op)
    assert saved["link_report"] == dict(EMPTY_LINKS, redirected=[
        {"row": "local_c", "target": "local_p", "kind": "parent"}])
    line = ("link redirected: local_c (its parent's row now opens another "
            "conversation)")
    assert line in out
    left = [i for i, x in enumerate(out) if "left 1 untouched" in x]
    assert left and left[0] < out.index(line)


def test_sync_undo_restoring_a_different_original_parent_reports_no_redirection(
        two_account_env, tmp_path, capsys):
    """One op refreshed both the parent's row (moving local_p onto the
    parent) and the child's, whose own pre-image already named local_p.
    Undo restores both. local_p opens its earlier conversation again and
    the child's restored link names it - as it did before the op - so the
    report says nothing: a restored row carries no expectation from this
    op, even when its pre-image names the same row id."""
    env, src, dst = two_account_env(tmp_path)
    _swapped_parent(env, src, dst, child_title="ACME-REVIEW side task v2",
                    child_in_dst=True)
    pre = {n: _bytes(os.path.join(dst, n))
           for n in ("local_p.json", "local_c.json")}
    m = ct.plan_sync(env, ct.SyncFlags(update=True, only="ACME"))
    by = _by_name(m)
    assert [r["name"] for r in m["rows"]] == ["local_p.json", "local_c.json"]
    assert all(r["is_update"] for r in m["rows"])
    assert by["local_c.json"]["lineage"]["parent_row"] == "local_p"
    assert by["local_c.json"]["lineage"]["parent_session"] == LP
    assert ct.run_sync(env, m) == "completed"
    op = ct.list_ops(env)[-1]
    assert ct.cmd_undo(env, _undo_ns(op)) == 0
    out = capsys.readouterr().out.splitlines()
    assert "result: undone" in out
    for n, b in pre.items():
        assert _bytes(os.path.join(dst, n)) == b
    assert _journal_of(env, op)["link_report"] == EMPTY_LINKS
    assert not any(x.startswith(LINK_LINES) for x in out)
    # Without the restored exclusion the child's link would read as
    # redirected: it names local_p, which now opens LQ, not LP.
    assert ct._link_report(op.manifest["rows"], [dst])["redirected"] == [
        {"row": "local_c", "target": "local_p", "kind": "parent"}]


@pytest.mark.parametrize("fmt", ["lineage", "0.17"])
@pytest.mark.parametrize("leg", ["undo", "back"])
def test_sync_undo_reports_dangling_links_to_removed_rows(
        two_account_env, tmp_path, capsys, leg, fmt):
    """A first sync added the parent's row; a second added the child,
    linking to it. Reversing the first removes the parent's row, and its
    report names the second op's surviving child, whichever op wrote it.
    The dangling part reads the store and the rows removed, never the
    journal's lineage, so a journal from 0.17.0 gets it too."""
    env, src, dst = two_account_env(tmp_path)
    _transcripts(env, LP, LC)
    _lrow(src, "local_p.json", LP, "Northwind kickoff")
    _lrow(src, "local_c.json", LC, "ACME-REVIEW side task",
          spawnedFrom=_side("local_p"))
    m1 = ct.plan_sync(env, ct.SyncFlags(only="Northwind"))
    assert [r["name"] for r in m1["rows"]] == ["local_p.json"]
    if leg == "undo":
        assert ct.run_sync(env, m1) == "completed"
        op1 = ct.list_ops(env)[-1]
    else:
        op1 = _sync_crash(env, m1, point="sync-write-before-save")
    if fmt == "0.17":
        _made_0_17(op1)
    m2 = ct.plan_sync(env, ct.SyncFlags(only="ACME"))
    assert _post(m2["rows"][0])["spawnedFrom"]["sessionId"] == "local_p"
    assert ct.run_sync(env, m2) == "completed"
    if leg == "undo":
        assert ct.cmd_undo(env, _undo_ns(op1)) == 0
    else:
        assert ct.cmd_recover(env, _recover_ns(op1, "back")) == 0
    out = capsys.readouterr().out.splitlines()
    assert not os.path.exists(os.path.join(dst, "local_p.json"))
    assert os.path.exists(os.path.join(dst, "local_c.json"))
    assert _journal_of(env, op1)["link_report"] == dict(EMPTY_LINKS, dangling=[
        {"row": "local_c", "target": "local_p", "kind": "parent"}])
    line = "link left dangling: local_c (its parent's row was removed)"
    assert line in out
    result = "result: " + ("undone" if leg == "undo" else "rolled_back")
    assert out.index(result) < out.index(line)
    if leg == "undo":
        assert out.index("removed 1 row(s), restored 0; 0 already gone; 0 "
                         "refreshed row(s) not restored - that account "
                         "deleted them since") < out.index(line)


@pytest.mark.parametrize("leg", ["undo", "back"])
def test_sync_resumed_reversal_reports_a_link_to_a_parent_an_earlier_run_removed(
        two_account_env, tmp_path, monkeypatch, capsys, leg):
    """A first sync added the parent's row and two others; a second added
    the child, linking to the parent's row. Reversing the first is cut short
    with no crash: one row cannot be removed, so _sync_reverse_all removes
    the parent's row, attempts the rest and then refuses, and no report is
    saved. The resumed reversal finds the parent's row already gone. That
    earlier removal counts as this reversal's, so the report names the
    second op's surviving child, as the first run would have."""
    env, src, dst = two_account_env(tmp_path)
    _transcripts(env, LP, LC, LR, LG)
    _lrow(src, "local_p.json", LP, "Northwind kickoff")
    _lrow(src, "local_s.json", LR, "Northwind notes")
    _lrow(src, "local_t.json", LG, "Northwind board")
    _lrow(src, "local_c.json", LC, "ACME-REVIEW side task",
          spawnedFrom=_side("local_p"))
    m1 = ct.plan_sync(env, ct.SyncFlags(only="Northwind"))
    assert [r["name"] for r in m1["rows"]] == [
        "local_p.json", "local_s.json", "local_t.json"]
    if leg == "undo":
        assert ct.run_sync(env, m1) == "completed"
        op1 = ct.list_ops(env)[-1]
    else:
        op1 = _sync_crash(env, m1, nth=2)        # two rows landed, one not
    written = [r["name"] for r in op1.manifest["rows"] if r.get("written")]
    assert "local_p.json" in written and len(written) >= 2
    stuck = [n for n in written if n != "local_p.json"][0]
    m2 = ct.plan_sync(env, ct.SyncFlags(only="ACME"))
    assert _post(m2["rows"][0])["spawnedFrom"]["sessionId"] == "local_p"
    assert ct.run_sync(env, m2) == "completed"
    real = os.unlink

    def locked(path, *a, **k):
        if os.path.basename(str(path)) == stuck:
            raise PermissionError(13, "locked", str(path))
        return real(path, *a, **k)
    monkeypatch.setattr(ct.os, "unlink", locked)
    with pytest.raises(ct.Refusal):
        if leg == "undo":
            ct.undo_sync(env, op1)
        else:
            ct.recover_op(env, op1, "back")
    monkeypatch.setattr(ct.os, "unlink", real)
    assert not os.path.exists(os.path.join(dst, "local_p.json"))
    assert os.path.exists(os.path.join(dst, stuck))
    saved = _journal_of(env, op1)
    assert saved["status"] == ("completed" if leg == "undo" else "writing")
    assert "link_report" not in saved
    if leg == "undo":
        assert ct.cmd_undo(env, _undo_ns(op1)) == 0
    else:
        assert ct.cmd_recover(env, _recover_ns(op1, "back")) == 0
    out = capsys.readouterr().out.splitlines()
    assert not os.path.exists(os.path.join(dst, stuck))
    assert os.path.exists(os.path.join(dst, "local_c.json"))
    assert _journal_of(env, op1)["link_report"] == dict(EMPTY_LINKS, dangling=[
        {"row": "local_c", "target": "local_p", "kind": "parent"}])
    assert "link left dangling: local_c (its parent's row was removed)" in out


@pytest.mark.parametrize("leg", ["undo", "back"])
def test_sync_resumed_reversal_after_the_childs_restore_reports_no_redirection(
        two_account_env, tmp_path, monkeypatch, leg):
    """The scenario above, with the first reversal cut short where the
    children-first order puts the break: the child's row is back at its
    pre-image, the parent's is not yet. The resumed reversal finds the
    child already restored and restores the parent. That earlier run's
    restore counts as this reversal's, so the child, whose pre-image names
    the same row id the op journaled, is not reported as redirected. A third
    row, added after the child, lets back find the child recorded as
    written."""
    env, src, dst = two_account_env(tmp_path)
    _swapped_parent(env, src, dst, child_title="ACME-REVIEW side task v2",
                    child_in_dst=True)
    _lrow(src, "local_n.json", LR, "ACME-REVIEW notes", lastActivityAt=1)
    pre = {n: _bytes(os.path.join(dst, n))
           for n in ("local_p.json", "local_c.json")}
    m = ct.plan_sync(env, ct.SyncFlags(update=True, only="ACME"))
    assert [r["name"] for r in m["rows"]] == ["local_p.json", "local_c.json",
                                              "local_n.json"]
    if leg == "undo":
        assert ct.run_sync(env, m) == "completed"
        op = ct.list_ops(env)[-1]
    else:
        op = _sync_crash(env, m, nth=2)
        assert [r["written"] for r in op.manifest["rows"]] == [True, True,
                                                               False]
    steps = []
    real_write = ct.atomic_write

    def write(path, data):
        name = os.path.basename(path)
        if name.startswith("local_"):
            steps.append(name)
            if name == "local_p.json":
                raise SimulatedCrash()      # cut short after the child
        return real_write(path, data)
    monkeypatch.setattr(ct, "atomic_write", write)
    with pytest.raises(SimulatedCrash):
        if leg == "undo":
            ct.undo_sync(env, op)
        else:
            ct.recover_op(env, op, "back")
    monkeypatch.undo()
    assert steps == ["local_c.json", "local_p.json"]
    assert _bytes(os.path.join(dst, "local_c.json")) == pre["local_c.json"]
    assert _bytes(os.path.join(dst, "local_p.json")) != pre["local_p.json"]
    if leg == "undo":
        assert ct.undo_sync(env, op) == "undone"
    else:
        assert ct.recover_op(env, op, "back") == "rolled_back"
    for n, b in pre.items():
        assert _bytes(os.path.join(dst, n)) == b
    assert not os.path.exists(os.path.join(dst, "local_n.json"))
    saved = _journal_of(env, op)
    assert saved["link_report"]["redirected"] == []
    assert saved["link_report"] == EMPTY_LINKS


# ------------------------- R26: sync's planning decodes each store once
#
# docs/internals.md, "Reading each store once". The lineage pass holds rows
# as only the keys it reads (_lineage_light), takes its source index from
# selection's own read of the source, and takes the duplicate census from
# its one read of the destination. Nothing a plan says changes.

def _decode_once_stores(env, src, dst):
    """A source with a linked, chained child among plain adds and one
    refresh, and a destination holding the parent under another row and a
    bulky connector config on most rows."""
    bulk = [{"name": "Northwind", "url": "https://example.com/" + "x" * 400}]
    _transcripts(env, LP, LC, LR, "sid-add", "sid-e")
    _lrow(src, "local_p.json", LP, "Northwind kickoff",
          remoteMcpServersConfig=bulk)
    _lrow(src, "local_r.json", LR, "Northwind root")
    _lrow(src, "local_c.json", LC, "ACME-REVIEW side task", lastActivityAt=5,
          spawnedFrom=_side("local_p"),
          startChain={"firstSessionId": "local_r", "depth": 1},
          remoteMcpServersConfig=bulk)
    _lrow(src, "local_a.json", "sid-add", "ACME onboarding",
          remoteMcpServersConfig=bulk)
    _lrow(src, "local_e.json", "sid-e", "Northwind retro v2", lastActivityAt=7)
    _lrow(dst, "local_q.json", LP, "Northwind kickoff",
          remoteMcpServersConfig=bulk)
    _lrow(dst, "local_e.json", "sid-e", "Northwind retro", lastActivityAt=3)


def _store_files(*stores):
    return {os.path.normcase(os.path.realpath(os.path.join(s, n)))
            for s in stores for n in os.listdir(s)
            if n.startswith("local_") and n.endswith(".json")}


@pytest.mark.parametrize("flags", [
    ct.SyncFlags(), ct.SyncFlags(update=True), ct.SyncFlags(verbatim=True)],
    ids=["add", "update", "verbatim"])
def test_sync_planning_decodes_each_store_row_once(two_account_env, tmp_path,
                                                   monkeypatch, flags):
    """Every row file of the source and the destination is decoded exactly
    once while planning: selection's read of the source serves the lineage
    pass, and the pass's read of the destination serves the duplicate
    census. A refresh's pre-image is read as bytes, as before."""
    env, src, dst = two_account_env(tmp_path)
    _decode_once_stores(env, src, dst)
    reads = {}
    real = ct.read_json

    def spy(path):
        k = os.path.normcase(os.path.realpath(path))
        reads[k] = reads.get(k, 0) + 1
        return real(path)
    monkeypatch.setattr(ct, "read_json", spy)
    m = ct.plan_sync(env, flags)
    assert m["rows"]
    rows = {k: n for k, n in reads.items() if k in _store_files(src, dst)}
    assert set(rows) == _store_files(src, dst)
    assert set(rows.values()) == {1}


def test_sync_lineage_pass_holds_only_the_keys_it_reads(two_account_env,
                                                        tmp_path,
                                                        monkeypatch):
    """The pass, at planning and again under the lock, is handed the
    stores' rows as only _LINEAGE_ROW_KEYS, never whole rows."""
    env, src, dst = two_account_env(tmp_path)
    _decode_once_stores(env, src, dst)
    seen = []
    real = ct._sync_lineage

    def spy(rows, sources, source_store, dest_store, *a, **k):
        seen.append((list(source_store), list(dest_store)))
        return real(rows, sources, source_store, dest_store, *a, **k)
    monkeypatch.setattr(ct, "_sync_lineage", spy)
    m = ct.plan_sync(env, ct.SyncFlags(update=True))
    assert ct.run_sync(env, m) == "completed"
    assert len(seen) == 2                      # the plan, then the relink
    for source_store, dest_store in seen:
        assert source_store and dest_store
        for _rid, d in source_store + dest_store:
            assert set(d) <= set(ct._LINEAGE_ROW_KEYS)
    assert any("remoteMcpServersConfig" in ct.read_json(os.path.join(src, n))
               for n in os.listdir(src))


def _resolver_stores(env, src, dst):
    """Rows that exercise every key the pass reads: the parent held twice
    (unarchived wins over a later archived row), the chain root held twice
    (the later activity wins), a row whose sessionId is not its stem, a
    parent row that opens another conversation, and two cycles the
    destination already holds, one through a chain."""
    _transcripts(env, LP, LC, LR, LG, LQ, "sid-d")
    _lrow(src, "local_p.json", LP, "Northwind kickoff")
    _lrow(src, "local_r.json", LR, "Northwind root")
    _lrow(src, "local_g.json", LG, "Northwind planning")
    _lrow(src, "local_c.json", LC, "ACME-REVIEW side task",
          spawnedFrom=_side("local_p"),
          startChain={"firstSessionId": "local_r", "depth": 2})
    _lrow(src, "local_d.json", "sid-d", "ACME-REVIEW follow-up",
          spawnedFrom=_side("local_g"))
    _lrow(dst, "local_q.json", LP, "Northwind kickoff", lastActivityAt=1)
    _lrow(dst, "local_z.json", LP, "Northwind kickoff", lastActivityAt=9,
          isArchived=True)
    _lrow(dst, "local_w.json", LP, "Northwind kickoff", lastActivityAt=99,
          sessionId="local_elsewhere")
    _lrow(dst, "local_r1.json", LR, "Northwind root", lastActivityAt=2)
    _lrow(dst, "local_r2.json", LR, "Northwind root", lastActivityAt=7)
    _lrow(dst, "local_g.json", LQ, "Northwind other")
    _lrow(dst, "local_x.json", "sid-x", "Loop one", spawnedFrom=_side("local_y"))
    _lrow(dst, "local_y.json", "sid-y", "Loop two", spawnedFrom=_side("local_x"))
    _lrow(dst, "local_u.json", "sid-u", "Chain loop one",
          startChain={"firstSessionId": "local_v", "depth": 1})
    _lrow(dst, "local_v.json", "sid-v", "Chain loop two",
          spawnedFrom=_side("local_u"))


@pytest.mark.parametrize("update", [False, True], ids=["add", "update"])
def test_sync_light_rows_plan_exactly_what_whole_rows_plan(
        two_account_env, tmp_path, monkeypatch, update):
    """The pass reads nothing a light row leaves out: with _lineage_light
    made the identity, so the pass sees whole rows, the plan and the journal
    are the same, byte for byte."""
    env, src, dst = two_account_env(tmp_path)
    _resolver_stores(env, src, dst)
    flags = ct.SyncFlags(only="ACME", update=update)
    light = ct.plan_sync(env, flags)
    with monkeypatch.context() as mp:
        mp.setattr(ct, "_lineage_light", lambda d: d)
        whole = ct.plan_sync(env, flags)
    assert json.dumps(light) == json.dumps(whole)       # key order included
    by = _by_name(light)
    c, d = by["local_c.json"], by["local_d.json"]
    assert _post(c)["spawnedFrom"] == {"sessionId": "local_q", "taskId": LTASK}
    assert _post(c)["startChain"] == {"firstSessionId": "local_r2", "depth": 2}
    assert "spawnedFrom" not in _post(d)
    assert _codes(d) == ["parent_row_drifted"]
    assert light["lineage_cycles"] == 2
    # And again under the lock: the journal holds the same rows either way.
    journals = []
    real_new_op = ct.new_op

    def spy_new_op(env_, manifest):
        journals.append(json.dumps(manifest["rows"]))
        return real_new_op(env_, manifest)
    monkeypatch.setattr(ct, "new_op", spy_new_op)
    assert ct.run_sync(env, light) == "completed"
    assert ct.undo_sync(env, ct.list_ops(env)[-1]) == "undone"
    monkeypatch.setattr(ct, "_lineage_light", lambda d: d)
    assert ct.run_sync(env, whole) == "completed"
    assert len(journals) == 2 and journals[0] == journals[1]


def test_sync_planning_looks_up_each_transcript_once(two_account_env,
                                                     tmp_path, monkeypatch):
    """A conversation's transcript is looked up once while planning, in the
    plan's one index of the projects folder: the pass reuses selection's
    answer for a parent or root that is itself a candidate, and nothing
    scans the folder per conversation. Under the lock the pass looks again,
    afresh, with find_transcripts."""
    env, src, dst = two_account_env(tmp_path)
    _decode_once_stores(env, src, dst)
    asked, scanned = [], []
    real_find, real_scan = ct._TranscriptIndex.find, ct.find_transcripts

    def spy_find(self, sid):
        asked.append(sid)
        return real_find(self, sid)

    def spy_scan(root, sid):
        scanned.append(sid)
        return real_scan(root, sid)
    monkeypatch.setattr(ct._TranscriptIndex, "find", spy_find)
    monkeypatch.setattr(ct, "find_transcripts", spy_scan)
    m = ct.plan_sync(env, ct.SyncFlags())
    assert LP in asked and LR in asked
    assert sorted(asked) == sorted(set(asked))
    assert scanned == []
    del asked[:]
    assert ct.run_sync(env, m) == "completed"
    assert asked == []
    assert LP in scanned and LR in scanned


def _prose_transcript(env, folder, sid, bodies):
    path = os.path.join(env.projects_root, folder, sid + ".jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for i, b in enumerate(bodies):
            fh.write(json.dumps({"type": "user" if i % 2 == 0 else "assistant",
                                 "message": {"content": b}}) + "\n")
    return path


def _index_stores(env, src, dst):
    """Rows that reach every lookup planning makes - candidates, a parent
    and a root for the lineage pass, and two swaps whose overlap and sizes
    are measured - over transcripts in every shape the plan's index has to
    answer as find_transcripts does: an id in two folders, a filename in
    another case, a .jsonl that is a directory, a non-ASCII filename, and
    an id with no transcript."""
    _swapped_parent(env, src, dst)
    turns = ["Northwind turn %d with enough words to be a real message" % i
             for i in range(12)]
    _prose_transcript(env, "C--p", LQ, turns[:10])
    _prose_transcript(env, "C--p", LP, turns)
    _lrow(src, "local_r.json", LR, "ACME-REVIEW root")
    _transcript(env, "sid-dup")
    _prose_transcript(env, "C--q", "sid-dup", turns[:9])
    _transcript(env, "sid-s")
    _lrow(dst, "local_s.json", "sid-dup", "ACME swap", lastActivityAt=1)
    _lrow(src, "local_s.json", "sid-s", "ACME swap", lastActivityAt=4)
    _lrow(src, "local_d.json", "sid-dup", "Northwind duplicate")
    _prose_transcript(env, "C--q", "SID-CASE", turns[:2])
    _lrow(src, "local_k.json", "sid-case", "ACME case")
    os.makedirs(os.path.join(env.projects_root, "C--q", "sid-dir.jsonl"))
    _lrow(src, "local_j.json", "sid-dir", "ACME directory")
    _prose_transcript(env, "C--q", "n\u0131-x", turns[:2])
    _lrow(src, "local_m.json", "sid-none", "Northwind no transcript")


def _scan_per_lookup(self, sid):
    """The index answering as 0.18.0's planning did: a fresh
    find_transcripts for every lookup."""
    return ct.find_transcripts(self.root, sid)


@pytest.mark.parametrize("flags", [
    ct.SyncFlags(), ct.SyncFlags(update=True),
    ct.SyncFlags(update=True, allow_orphan=True), ct.SyncFlags(verbatim=True),
    ct.SyncFlags(update=True, verbatim=True)],
    ids=["add", "update", "update-allow-orphan", "verbatim", "update-verbatim"])
def test_sync_plan_from_the_index_is_the_plan_from_scanning(
        two_account_env, tmp_path, monkeypatch, flags):
    """Planning from one index of the projects folder plans exactly what a
    fresh scan per lookup plans, byte for byte."""
    env, src, dst = two_account_env(tmp_path)
    _index_stores(env, src, dst)
    indexed = ct.plan_sync(env, flags)
    with monkeypatch.context() as mp:
        mp.setattr(ct._TranscriptIndex, "find", _scan_per_lookup)
        scanned = ct.plan_sync(env, flags)
    assert json.dumps(indexed) == json.dumps(scanned)   # key order included
    if flags.update and flags.allow_orphan:
        by = _by_name(indexed)
        assert by["local_p.json"]["displaced_overlap"] == 1.0
        assert (by["local_p.json"]["displaced_turns"],
                by["local_p.json"]["incoming_turns"]) == (10, 12)
        assert by["local_s.json"]["swaps_conversation"] is True
        assert by["local_s.json"]["displaced_overlap"] is None   # two copies
    names = {r["name"] for r in indexed["rows"]}
    assert "local_d.json" in names and "local_j.json" not in names
    assert "local_m.json" not in names
    folds_case = os.path.isfile(os.path.join(env.projects_root, "C--q",
                                             "sid-case.jsonl"))
    assert ("local_k.json" in names) == folds_case


@pytest.mark.parametrize("update", [False, True], ids=["add", "update"])
def test_sync_planning_lists_the_projects_folder_once(
        two_account_env, tmp_path, monkeypatch, update):
    env, src, dst = two_account_env(tmp_path)
    _index_stores(env, src, dst)
    flags = ct.SyncFlags(update=update, allow_orphan=True)
    listed = []
    real = os.listdir

    def spy(path="."):
        listed.append(os.path.normcase(str(path)))
        return real(path)
    monkeypatch.setattr(os, "listdir", spy)
    ct.plan_sync(env, flags)
    assert listed.count(os.path.normcase(env.projects_root)) == 1
    # And the scan it replaces listed it once per lookup.
    del listed[:]
    monkeypatch.setattr(ct._TranscriptIndex, "find", _scan_per_lookup)
    ct.plan_sync(env, flags)
    assert listed.count(os.path.normcase(env.projects_root)) > 5


def test_sync_plan_fails_a_non_string_session_id_as_before(
        two_account_env, tmp_path, monkeypatch):
    """A row whose cliSessionId is not a string fails in find_transcripts,
    as it always has, index or no index."""
    env, src, dst = two_account_env(tmp_path)
    _index_stores(env, src, dst)
    _lrow(src, "local_z.json", 5, "ACME five")

    def outcome():
        try:
            ct.plan_sync(env, ct.SyncFlags())
        except Exception as e:
            return type(e), str(e)
        return None
    indexed = outcome()
    monkeypatch.setattr(ct._TranscriptIndex, "find", _scan_per_lookup)
    assert indexed == outcome()
    assert indexed is not None and indexed[0] is TypeError


def test_sync_refresh_loss_decodes_each_image_once(monkeypatch):
    """A refresh's field loss and its link and chain change lines come from
    one decode of each image, and say what they said before."""
    pre = {"sessionId": "local_e", "cliSessionId": "sid-e", "title": "Old",
           "remoteMcpServersConfig": [{"name": "Northwind"}],
           "chromePermissionMode": "ask",
           "spawnedFrom": {"sessionId": "local_q", "taskId": LTASK},
           "startChain": {"firstSessionId": "local_r", "depth": 1}}
    post = {"sessionId": "local_e", "cliSessionId": "sid-e", "title": "New",
            "chromePermissionMode": None,
            "spawnedFrom": {"sessionId": "local_k", "taskId": LTASK}}

    def blob(d):
        return json.dumps(d, separators=(",", ":")).encode("utf-8")
    r = {"is_update": True, "pre_b64": ct.b64(blob(pre)),
         "post_b64": ct.b64(blob(post))}
    calls = []
    real = json.loads

    def spy(*a, **k):
        calls.append(1)
        return real(*a, **k)
    monkeypatch.setattr(json, "loads", spy)
    ct._sync_refresh_loss(r)
    monkeypatch.undo()
    assert len(calls) == 2
    assert r["dest_dropped"] == ["remoteMcpServersConfig"]
    assert r["dest_reset"] == ["chromePermissionMode"]
    assert r["link_change"] == "retargeted" and r["chain_change"] == "dropped"
    for bad in ({"is_update": True, "pre_b64": None, "post_b64": "e30="},
                {"is_update": True, "pre_b64": ct.b64(b"{"),
                 "post_b64": "e30="}):
        ct._sync_refresh_loss(bad)
        assert bad["dest_dropped"] is None and bad["dest_reset"] is None
        assert bad["link_change"] is None and bad["chain_change"] is None


def test_sync_scan_census_matches_the_census_it_stands_in_for(tmp_path):
    """The destination scan's census is _destination_census's own answer,
    and stands in for it only when it read every row the census reads: an
    unreadable row or one that is not an object is skipped by both, while a
    store it cannot list leaves the census to read the store itself."""
    store = str(tmp_path / "store")
    os.makedirs(store)
    _lrow(store, "local_a.json", "sid-a", "  Northwind kickoff ")
    _lrow(store, "local_b.json", "", "ACME onboarding")
    _lrow(store, "local_c.json", "sid-c", None)
    with open(os.path.join(store, "local_bad.json"), "w") as fh:
        fh.write("{")
    with open(os.path.join(store, "local_list.json"), "w") as fh:
        fh.write("[1]")
    with open(os.path.join(store, "deleted_sid-z"), "w") as fh:
        fh.write("")
    census = {}
    rows, unread = ct._lineage_store_rows(store, light=True, census=census)
    assert sorted(i for i, _ in rows) == ["local_a", "local_b", "local_c"]
    assert unread == 2
    assert census["whole"] is True
    assert (census["sids"], census["keys"]) == ct._destination_census(store)
    census = {}
    assert ct._lineage_store_rows(str(tmp_path / "missing"),
                                  census=census) == ([], 0)
    assert census["whole"] is False


def test_sync_scan_census_gives_way_on_a_failure_read_json_does_not_name(
        tmp_path, monkeypatch):
    """A row whose read fails other than as read_json refuses (a
    RecursionError from deeply nested JSON, say) is counted unread by the
    scan, but the census would not have skipped it, so the scan's census
    does not stand in for it: the census reads the store itself, and fails
    as it always has."""
    store = str(tmp_path / "store")
    os.makedirs(store)
    _lrow(store, "local_a.json", "sid-a", "Northwind kickoff")
    _lrow(store, "local_deep.json", "sid-d", "ACME onboarding")
    real = ct.read_json

    def read(path):
        if os.path.basename(path) == "local_deep.json":
            raise RecursionError("maximum recursion depth exceeded")
        return real(path)
    monkeypatch.setattr(ct, "read_json", read)
    census = {}
    rows, unread = ct._lineage_store_rows(store, light=True, census=census)
    assert [i for i, _ in rows] == ["local_a"] and unread == 1
    assert census["whole"] is False
    with pytest.raises(RecursionError):
        ct._destination_census(store)


def test_sync_census_reads_the_store_when_the_scan_left_a_row_out(
        two_account_env, tmp_path, monkeypatch):
    """A destination row the containment test refuses is not in the pass's
    index, but the duplicate census, which reads every row by its name,
    still finds it, as it did when it read the store on its own."""
    env, src, dst = two_account_env(tmp_path)
    _transcripts(env, LP)
    _lrow(src, "local_p.json", LP, "Northwind kickoff")
    _lrow(dst, "local_q.json", LP, "Northwind kickoff")
    real = ct.ensure_contained

    def contained(path, roots):
        if os.path.basename(path) == "local_q.json":
            raise ct.LayoutError("resolves outside every recognized root")
        return real(path, roots)
    monkeypatch.setattr(ct, "ensure_contained", contained)
    m = ct.plan_sync(env, ct.SyncFlags())
    r = _by_name(m)["local_p.json"]
    assert r["dup_conversation"] is True and r["dup_title"] is True
    assert m["tally"]["dup_conversation"] == ["Northwind kickoff"]


def test_sync_source_row_the_containment_test_refuses_is_no_link_target(
        two_account_env, tmp_path, monkeypatch):
    """Selection's read of the source serves the pass through the same
    containment test the pass's own read made: a source parent row that
    resolves outside the store is still copied, as selection copies it, but
    names no conversation to its child's link."""
    env, src, dst = two_account_env(tmp_path)
    _transcripts(env, LP, LC)
    _lrow(src, "local_p.json", LP, "Northwind kickoff")
    _lrow(src, "local_c.json", LC, "ACME-REVIEW side task", lastActivityAt=5,
          spawnedFrom=_side("local_p"))
    real = ct.ensure_contained

    def contained(path, roots):
        if os.path.basename(path) == "local_p.json" \
                and os.path.dirname(os.path.realpath(path)) == \
                os.path.realpath(src):
            raise ct.LayoutError("resolves outside every recognized root")
        return real(path, roots)
    monkeypatch.setattr(ct, "ensure_contained", contained)
    m = ct.plan_sync(env, ct.SyncFlags())
    by = _by_name(m)
    assert set(by) == {"local_p.json", "local_c.json"}
    c = by["local_c.json"]
    assert "spawnedFrom" not in _post(c)
    assert c["lineage"] == ct._lineage_empty()
