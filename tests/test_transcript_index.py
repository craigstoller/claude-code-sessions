"""The per-plan transcript index (_TranscriptIndex): what sync planning asks
of ~/.claude/projects, answered from one listing of it, with exactly
find_transcripts' results - the same hits, in the same order, as the same
strings, and the same exceptions."""
import os
import pathlib
import random
import shutil

import pytest

import claude_code_sessions as ct


def _touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("{}\n")
    return path


def _outcome(fn, *args):
    try:
        return ("returned", fn(*args))
    except Exception as e:               # compared, never swallowed
        return ("raised", type(e), str(e))


def _assert_same(root, sids):
    idx = ct._TranscriptIndex(root)
    for sid in sids:
        assert _outcome(idx.find, sid) == \
            _outcome(ct.find_transcripts, root, sid), repr(sid)


@pytest.fixture
def projects(tmp_path):
    p = tmp_path / "projects"
    p.mkdir()
    return str(p)


def _awkward_layout(root):
    """Every shape the equivalence has to survive: an id in two folders, a
    filename in another case, a .jsonl that is a directory, a plain file and
    an empty folder in the root, non-ASCII names in a folder and as a
    folder, and a transcript one folder below a project folder."""
    _touch(os.path.join(root, "C--Northwind", "s-1.jsonl"))
    _touch(os.path.join(root, "C--Northwind--claude-worktrees-wt1",
                        "s-1.jsonl"))
    _touch(os.path.join(root, "C--ACME", "S-2.JSONL"))
    os.makedirs(os.path.join(root, "C--Northwind", "s-3.jsonl"))
    _touch(os.path.join(root, "notes.txt"))
    os.makedirs(os.path.join(root, "C--empty"))
    _touch(os.path.join(root, "C--\u0131", "s-4.jsonl"))
    _touch(os.path.join(root, "C--ACME", "s\u0131d-5.jsonl"))
    _touch(os.path.join(root, "C--ACME", "\u017f-6.jsonl"))
    _touch(os.path.join(root, "C--ACME", "\u212a-8.jsonl"))
    _touch(os.path.join(root, "C--Northwind", "sub", "s-7.jsonl"))


def test_index_answers_as_find_transcripts_does(projects):
    _awkward_layout(projects)
    absolute = os.path.join(projects, "C--Northwind", "s-1")
    _assert_same(projects, [
        "s-1", "S-1", "s-2", "S-2", "s-3", "s-4", "sid-5", "SID-5",
        "s\u0131d-5", "s-6", "S-6", "k-8", "K-8", "\u212a-8", "sub/s-7",
        "sub\\s-7", "..\\C--Northwind\\s-1", "../C--Northwind/s-1", absolute,
        "missing", "", " s-1", "s-1 ", "s-1.", "s.1", "C:", "s-1\x00",
        "s-1:x", "*", "s-?"])


def test_index_hits_are_built_from_the_id_asked(projects):
    """A hit is the path find_transcripts builds - folder, then the id as
    asked plus .jsonl - in the root's sorted order, not the name on disk."""
    _awkward_layout(projects)
    idx = ct._TranscriptIndex(projects)
    assert idx.find("s-1") == [
        os.path.join(projects, "C--Northwind", "s-1.jsonl"),
        os.path.join(projects, "C--Northwind--claude-worktrees-wt1",
                     "s-1.jsonl")]
    assert idx.find("s-3") == []                    # a directory, not a file


@pytest.mark.parametrize("sid", [None, 5, 5.0, True, b"s-1", ["s-1"],
                                 {"s": 1}, ("s-1",)],
                         ids=lambda s: type(s).__name__)
def test_index_fails_a_non_string_id_as_find_transcripts_does(projects, sid):
    _touch(os.path.join(projects, "C--Northwind", "s-1.jsonl"))
    _assert_same(projects, [sid])
    assert _outcome(ct._TranscriptIndex(projects).find, sid)[0] == "raised"


@pytest.mark.parametrize("sid", [None, 5, b"s-1", ["s-1"], "s-1", "sub/s"])
def test_index_of_an_empty_or_missing_root(projects, tmp_path, sid):
    _assert_same(projects, [sid])                          # empty
    _assert_same(str(tmp_path / "no-such-folder"), [sid])  # missing


def test_index_of_a_root_that_is_a_file(tmp_path):
    root = _touch(str(tmp_path / "projects"))
    _assert_same(root, ["s-1", "s-2", 5])
    assert _outcome(ct._TranscriptIndex(root).find, "s-1")[0] == "raised"


def test_index_of_a_root_that_cannot_be_listed_raises_every_time(
        projects, monkeypatch):
    """No answer is cached from a listing that failed: each lookup lists
    again, and raises where find_transcripts raises."""
    _touch(os.path.join(projects, "C--Northwind", "s-1.jsonl"))
    real = os.listdir

    def refuse(path="."):
        if os.path.normcase(str(path)) == os.path.normcase(projects):
            raise PermissionError(13, "Access is denied", str(path))
        return real(path)
    monkeypatch.setattr(os, "listdir", refuse)
    idx = ct._TranscriptIndex(projects)
    for _ in range(2):
        assert _outcome(idx.find, "s-1") == \
            _outcome(ct.find_transcripts, projects, "s-1")
        assert _outcome(idx.find, "s-1")[1] is PermissionError
    monkeypatch.setattr(os, "listdir", real)
    assert idx.find("s-1") == [os.path.join(projects, "C--Northwind",
                                            "s-1.jsonl")]


class _Calls:
    """os.listdir and os.path.isfile, counted by path."""

    def __init__(self, monkeypatch):
        self.listed, self.checked = [], []
        real_list, real_isfile = os.listdir, os.path.isfile

        def listdir(path="."):
            self.listed.append(os.path.normcase(str(path)))
            return real_list(path)

        def isfile(path):
            self.checked.append(os.path.normcase(str(path)))
            return real_isfile(path)
        monkeypatch.setattr(os, "listdir", listdir)
        monkeypatch.setattr(os.path, "isfile", isfile)


def test_index_lists_the_root_once_and_each_folder_once(projects,
                                                         monkeypatch):
    sids = ["%032x" % (0x5eed0000 + i) for i in range(40)]
    for i, sid in enumerate(sids):
        _touch(os.path.join(projects, "C--Northwind-%02d" % i,
                            sid + ".jsonl"))
    calls = _Calls(monkeypatch)
    idx = ct._TranscriptIndex(projects)
    assert calls.listed == [] and calls.checked == []      # built on demand
    for sid in sids + ["%032x" % i for i in range(10)]:
        idx.find(sid)
    assert calls.listed.count(os.path.normcase(projects)) == 1
    assert len(calls.listed) == 1 + len(sids)
    assert len(set(calls.listed)) == len(calls.listed)
    # One check per hit: a folder that does not list the id is not asked.
    assert len(calls.checked) == len(sids)


def test_a_folder_that_cannot_be_listed_is_asked_per_file(projects,
                                                           monkeypatch):
    """A folder whose listing fails is asked of each id the way
    find_transcripts asks it, os.path.isfile on the same path, so its
    answer is today's answer, not a guess in either direction."""
    locked = os.path.join(projects, "C--ACME-locked")
    _touch(os.path.join(locked, "s-1.jsonl"))
    _touch(os.path.join(projects, "C--Northwind", "s-2.jsonl"))
    real = os.listdir

    def refuse(path="."):
        if os.path.normcase(str(path)) == os.path.normcase(locked):
            raise PermissionError(13, "Access is denied", str(path))
        return real(path)
    monkeypatch.setattr(os, "listdir", refuse)
    _assert_same(projects, ["s-1", "S-1", "s-2", "s-3", "sub/s-1"])
    calls = _Calls(monkeypatch)
    idx = ct._TranscriptIndex(projects)
    assert idx.find("s-1") == [os.path.join(locked, "s-1.jsonl")]
    assert idx.find("s-2") == [os.path.join(projects, "C--Northwind",
                                            "s-2.jsonl")]
    assert idx.find("s-3") == []
    in_locked = [p for p in calls.checked
                 if p.startswith(os.path.normcase(locked) + os.sep)]
    assert len(in_locked) == 3                # every lookup, as today


@pytest.mark.skipif(os.name != "nt", reason="Windows path normalisation")
@pytest.mark.parametrize("odd, twin", [("NUL", None),
                                       ("C--ACME ", "C--ACME"),
                                       ("C--ACME.", "C--ACME"),
                                       ("C--ACME..", "C--ACME")])
def test_a_folder_its_plain_path_lists_otherwise_is_asked_per_file(
        projects, odd, twin):
    """Folders whose plain path os.listdir may list as another folder, or
    not at all. On a Windows Python whose os.listdir normalises the path
    first (CPython gh-150880, in later 3.13 and 3.14 patch releases and
    3.15), os.listdir of "C--ACME " or "C--ACME.." lists its twin
    "C--ACME", and of a folder named NUL fails, while os.path.isfile still
    reaches the file inside through a middle path component; older ones
    list them as they are. A single trailing dot is dropped by both calls
    alike, so "C--ACME." only pins the equality. Made through the \\\\?\\
    prefix. The index answers as find_transcripts does on either kind of
    Python."""
    if twin:
        os.mkdir(os.path.join(projects, twin))         # empty
    raw_folder = "\\\\?\\" + os.path.join(projects, odd)
    os.mkdir(raw_folder)
    try:
        with open(raw_folder + "\\s-1.jsonl", "w", encoding="utf-8") as fh:
            fh.write("{}\n")
        _assert_same(projects, ["s-1", "S-1", "s-2"])
    finally:
        shutil.rmtree(raw_folder)


def test_index_of_a_root_that_is_not_a_str(projects):
    """A root this was never meant for goes to find_transcripts as it is,
    whatever its folders hold: bytes, over an empty folder and over a
    transcript, and a pathlib path. And a str root with a NUL in it fails
    as find_transcripts fails."""
    os.makedirs(os.path.join(projects, "C--empty"))
    broot = os.fsencode(projects)
    _assert_same(broot, ["s-1", "s-2", 5])
    _touch(os.path.join(projects, "C--Northwind", "s-1.jsonl"))
    _assert_same(broot, ["s-1", "s-2", 5])
    _assert_same(pathlib.Path(projects), ["s-1", "S-1", "s-2", 5])
    _assert_same(projects + "\x00x", ["s-1", 5])
    assert _outcome(ct._TranscriptIndex(projects + "\x00x").find,
                    "s-1")[1] is ValueError


def test_a_transcript_gone_since_the_listing_is_not_found(projects):
    """Every hit is checked on disk when it is asked for, so a transcript
    removed after the listing is no hit, as find_transcripts would say."""
    a = _touch(os.path.join(projects, "C--Northwind", "s-1.jsonl"))
    _touch(os.path.join(projects, "C--ACME", "s-2.jsonl"))
    idx = ct._TranscriptIndex(projects)
    assert idx.find("s-2")                                 # listed now
    os.unlink(a)
    assert idx.find("s-1") == ct.find_transcripts(projects, "s-1") == []


def test_a_transcript_written_since_the_listing_is_not_seen(projects):
    """The listing is the plan's snapshot: a transcript that appears after
    it is not seen by this index. Apply time looks again, under the lock,
    with find_transcripts (test_sync's
    test_sync_planning_looks_up_each_transcript_once)."""
    _touch(os.path.join(projects, "C--ACME", "s-2.jsonl"))
    idx = ct._TranscriptIndex(projects)
    assert idx.find("s-2")
    late = _touch(os.path.join(projects, "C--Northwind", "s-1.jsonl"))
    assert idx.find("s-1") == []
    assert ct.find_transcripts(projects, "s-1") == [late]


_NAMES = ["s-1", "s-2", "S-1", "ab-CD", "AB-cd", "x_y", "s\u0131-1",
          "\u017f-2", "e\u0301-3", "\u00e9-3"]


@pytest.mark.parametrize("seed", range(150))
def test_index_matches_find_transcripts_on_random_layouts(tmp_path, seed):
    rnd = random.Random(seed)
    root = str(tmp_path / "projects")
    os.makedirs(root)
    folders = ["C--Northwind", "C--ACME", "C--ACME--claude-worktrees-a",
               "C--\u00e9t\u00e9", "C--Contoso"]
    for folder in rnd.sample(folders, rnd.randint(0, len(folders))):
        os.makedirs(os.path.join(root, folder), exist_ok=True)
        for _ in range(rnd.randint(0, 5)):
            name = rnd.choice(_NAMES)
            name = rnd.choice([name, name.upper(), name.lower()])
            path = os.path.join(root, folder, name + rnd.choice(
                [".jsonl", ".JSONL", ".Jsonl", ".jsonl.tmp", ""]))
            if os.path.exists(path):
                continue
            if rnd.random() < 0.15:
                os.makedirs(path)
            else:
                _touch(path)
    if rnd.random() < 0.3:
        _touch(os.path.join(root, "stray.jsonl"))
    asks = [rnd.choice([n, n.upper(), n.lower()]) for n in _NAMES]
    _assert_same(root, asks + [5, None, "", "C--ACME/s-1"])
