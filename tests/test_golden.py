import json
import os

import claude_session_store as ct


def test_real_row_shapes_load(mkenv, tmp_path, write_row):
    env = mkenv(tmp_path)
    shapes = json.load(open(os.path.join(os.path.dirname(__file__),
                                         "golden", "row-shapes.json"), encoding="utf-8"))
    for i, shape in enumerate(shapes):
        shape = dict(shape)
        shape["sessionId"] = "local_g%d" % i
        write_row(env, 0, "org", "acct", "local_g%d" % i, shape)
    rows, errors = ct.load_rows(env.store_candidates)
    assert len(rows) == len(shapes) and errors == []
    for r in rows:
        r.local_id, r.cli_session_id, r.cwd, r.last_activity   # accessors never raise
