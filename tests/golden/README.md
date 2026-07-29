# Golden fixtures

`row-shapes.json` holds key-shape skeletons of real Windows Claude Desktop listing rows — every
value redacted to structure only (strings become short `"S..."` runs, numbers become `0`, lists
and dicts become empty), captured 2026-07-28. Any new capture must pass the sanitization
checklist (no titles, no paths, no UUIDs, no account identifiers — only key names and structure
may survive) before it is committed.
