import os

import pytest

import claude_code_sessions as ct


def test_contained_ok(tmp_path):
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    p = root / "sub" / "f.txt"
    p.write_text("x")
    assert ct.ensure_contained(str(p), [str(root)]) == os.path.realpath(str(p))


def test_escape_refused(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    with pytest.raises(ct.LayoutError):
        ct.ensure_contained(str(root / ".." / "outside.txt"), [str(root)])


def test_symlink_escape_refused(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    target = tmp_path / "secret"
    target.mkdir()
    link = root / "link"
    try:
        os.symlink(str(target), str(link))
    except OSError:
        pytest.skip("no symlink privilege")
    with pytest.raises(ct.LayoutError):
        ct.ensure_contained(str(link / "f"), [str(root)])


def test_sidecar_inventory(tmp_path):
    d = tmp_path / "side"
    (d / "nested").mkdir(parents=True)
    (d / "a.jsonl").write_bytes(b"one")
    (d / "nested" / "b.jsonl").write_bytes(b"two")
    inv = ct.sidecar_inventory(str(d))
    assert [e["rel"] for e in inv] == ["a.jsonl", "nested/b.jsonl"]
    assert all(e["sha256"] and e["size"] for e in inv)
    assert ct.sidecar_inventory(str(tmp_path / "missing")) == []


def test_sidecar_symlink_aborts(tmp_path):
    d = tmp_path / "side"
    d.mkdir()
    try:
        os.symlink(str(tmp_path), str(d / "loop"))
    except OSError:
        pytest.skip("no symlink privilege")
    with pytest.raises(ct.Refusal):
        ct.sidecar_inventory(str(d))


def test_sidecar_toplevel_symlink_aborts(tmp_path):
    real = tmp_path / "real-side"
    real.mkdir()
    (real / "a.jsonl").write_bytes(b"x")
    link = tmp_path / "linked-side"
    try:
        os.symlink(str(real), str(link), target_is_directory=True)
    except OSError:
        pytest.skip("no symlink privilege")
    with pytest.raises(ct.Refusal):
        ct.sidecar_inventory(str(link))
