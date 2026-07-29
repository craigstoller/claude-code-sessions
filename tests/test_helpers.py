import hashlib
import os

import pytest

import claude_threads as ct


def test_sha256_file(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"hello world")
    digest, size = ct.sha256_file(str(p))
    assert digest == hashlib.sha256(b"hello world").hexdigest()
    assert size == 11


def test_atomic_write_replaces_and_leaves_no_tmp(tmp_path):
    p = tmp_path / "row.json"
    p.write_bytes(b"old")
    ct.atomic_write(str(p), b"new")
    assert p.read_bytes() == b"new"
    assert [f for f in os.listdir(tmp_path) if f.endswith(".tmp")] == []


def test_read_json_failure_is_layout_error(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json")
    with pytest.raises(ct.LayoutError):
        ct.read_json(str(p))


def test_b64_roundtrip():
    assert ct.unb64(ct.b64(b"\x00\xffdata")) == b"\x00\xffdata"


def test_fsync_file_smoke(tmp_path):
    p = tmp_path / "s.bin"
    p.write_bytes(b"data")
    ct.fsync_file(str(p))          # must not raise on Windows
