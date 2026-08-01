import json
import os

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
