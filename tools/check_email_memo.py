"""Check the learned-email memo: recording, precedence, labelling, and failure modes.

Synthetic env throughout - never touches the real journal.
"""
import json
import os
import shutil
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import claude_code_sessions as ccs  # noqa: E402

ok = []


def check(name, cond, extra=""):
    print("%s %s%s" % ("OK " if cond else "BAD", name, ("  " + extra) if extra else ""))
    ok.append(bool(cond))


root = tempfile.mkdtemp(prefix="memo-")
env = ccs.default_env()
env.ops_dir = os.path.join(root, "journal", "ops")
env.store_candidates = []          # no sandbox anywhere, so only the memo can answer
os.makedirs(env.ops_dir)
A = "a" * 32

check("unknown account yields nothing", ccs.account_email(env, A) == ("", ""))

ccs.remember_account_email(env, A, "someone@example.com")
email, src = ccs.account_email(env, A)
check("a recorded email comes back", email == "someone@example.com", email)
check("  and is labelled as remembered", src.startswith("memo:"), src)
check("  with the date it was seen", len(src.split(":", 1)[1]) == 10, src)

# blank inputs must not create junk entries
before = open(ccs._email_memo_path(env), encoding="utf-8").read()
ccs.remember_account_email(env, A, "")
ccs.remember_account_email(env, "", "x@y.z")
check("blank email or uuid records nothing",
      open(ccs._email_memo_path(env), encoding="utf-8").read() == before)

# re-recording the same value must not churn the file (it would move the date)
ccs.remember_account_email(env, A, "someone@example.com")
check("an unchanged email does not rewrite the record",
      open(ccs._email_memo_path(env), encoding="utf-8").read() == before)

# a changed email wins - accounts get renamed
ccs.remember_account_email(env, A, "renamed@example.com")
check("a changed email replaces the old one",
      ccs.account_email(env, A)[0] == "renamed@example.com")

# corruption must degrade to "unknown", never raise: this only labels things
with open(ccs._email_memo_path(env), "w", encoding="utf-8") as fh:
    fh.write("{not json at all")
check("a corrupt memo reads as unknown, not an error",
      ccs.account_email(env, A) == ("", ""))
ccs.remember_account_email(env, A, "recovered@example.com")
check("  and is repaired by the next write",
      ccs.account_email(env, A)[0] == "recovered@example.com")

# a structurally valid file with wrong TYPES must still read as unknown - it
# would otherwise push a non-string into "memo:" + seen and the --to match string
for bad in ({"email": 123}, {"email": "x@y.z", "seen": 5}, {"email": None}, "notadict"):
    with open(ccs._email_memo_path(env), "w", encoding="utf-8") as fh:
        json.dump({A: bad}, fh)
    email, src = ccs.account_email(env, A)
    got = ccs.remembered_account_email(env, A)
    check("typed junk %-28s degrades safely" % (json.dumps(bad)[:28],),
          isinstance(got[0], str) and isinstance(got[1], str)
          and isinstance(src, str), repr(got))
ccs.remember_account_email(env, A, "recovered@example.com")

# planning must NOT write the memo - plan_sync promises it writes nothing
os.remove(ccs._email_memo_path(env))
env2 = ccs.default_env()
env2.ops_dir = os.path.join(root, "j2", "ops")
os.makedirs(env2.ops_dir)
check("planning does not create the memo (dry runs write nothing)",
      not os.path.exists(ccs._email_memo_path(env2)))

# the sandbox wins over the memo - it is re-derived, so it cannot go stale
saved = ccs.dormant_account_email
try:
    ccs.dormant_account_email = lambda e, u: "fresh@example.com"
    email, src = ccs.account_email(env, A)
    check("a sandbox answer beats the memo", email == "fresh@example.com", email)
    check("  and is labelled as such", src == "sandbox", src)
finally:
    ccs.dormant_account_email = saved

# the report must MARK a remembered email rather than pass it off as observed
lines = []
manifest = {"source_email": "live@example.com", "source_account": "b" * 32,
            "source_org": "c" * 32, "source_path": "/src", "source_resolved_from": "oauth",
            "dest_email": "recovered@example.com", "dest_email_source": "memo:2026-08-16",
            "dest_account": A, "dest_org": "d" * 32, "dest_path": "/dst",
            "rows": [], "tally": {}, "verbatim": False}
ccs._print_sync_report(lines.append, manifest)
text = "\n".join(lines)
check("the report marks a remembered email", "remembered" in text)
check("  and says the path is what identifies it", "identifies it" in text)

manifest["dest_email_source"] = "sandbox"
lines = []
ccs._print_sync_report(lines.append, manifest)
check("a sandbox email is NOT marked remembered",
      "remembered" not in "\n".join(lines))

shutil.rmtree(root, ignore_errors=True)
print()
print("ALL PASS" if all(ok) else "SOME CHECKS FAILED")
sys.exit(0 if all(ok) else 1)
