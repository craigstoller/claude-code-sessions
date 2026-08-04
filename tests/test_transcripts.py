import claude_code_sessions as ct


def test_find_across_folders_including_worktrees(mkenv, tmp_path, write_transcript):
    env = mkenv(tmp_path)
    write_transcript(env, "C--p-main", "other", [{}])
    t = write_transcript(env, "C--p-main--claude-worktrees-wt1", "s-1", [{"cwd": "C:\\p\\main"}])
    assert ct.find_transcripts(env.projects_root, "s-1") == [t]


def test_ambiguous_returns_both(mkenv, tmp_path, write_transcript):
    env = mkenv(tmp_path)
    a = write_transcript(env, "C--a", "dup", [{}])
    b = write_transcript(env, "C--b", "dup", [{}])
    assert sorted(ct.find_transcripts(env.projects_root, "dup")) == sorted([a, b])


def test_last_and_first_cwd(mkenv, tmp_path, write_transcript):
    env = mkenv(tmp_path)
    t = write_transcript(env, "C--p", "s2",
                         [{"cwd": "C:\\old"}, {"type": "x"}, {"cwd": "C:\\new"}])
    assert ct.first_cwd(t) == "C:\\old"
    assert ct.last_cwd(t) == "C:\\new"
    assert ct.sidecar_path(t) == t[:-len(".jsonl")]
