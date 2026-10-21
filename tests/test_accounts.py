"""多账号发现、切换与缓存隔离的测试。

会临时在家目录建一个 .codex-test 目录（里面是假 token），跑完自动删除。
不会读取、复制或改动任何真实凭证。

跑法：python tests/test_accounts.py
"""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import codex_gauge as g  # noqa: E402

FAKE = Path.home() / ".codex-test"
results = []


def check(name, got, want):
    ok = got == want
    results.append(ok)
    print("%-40s %-30s %s" % (name, got, "PASS" if ok else "FAIL (want %s)" % (want,)))


try:
    # --- 造一个假账号（假 token，不是真实凭证）
    FAKE.mkdir(exist_ok=True)
    (FAKE / "auth.json").write_text(json.dumps({
        "auth_mode": "chatgpt",
        "tokens": {
            "access_token": "fake.token.only.for.test",
            "account_id": "11111111-2222-3333-4444-555555555555",
        },
    }), encoding="utf-8")

    # --- 1. 能发现两个账号
    accs = g.discover_accounts()
    check("discover: account count", len(accs), 2)
    labels = [a["label"] for a in accs]
    check("discover: main account first", labels[0], "主账号")
    check("discover: test account label", "test" in labels, True)

    paths = {a["path"] for a in accs}
    check("discover: includes fake path", str(FAKE) in paths, True)

    # --- 2. 缓存按账号隔离
    g.save_cache({"plan": "plus", "primary": {"used": 11.0}}, {"primary": [[1, 11]]}, str(Path.home() / ".codex"))
    g.save_cache({"plan": "pro", "primary": {"used": 77.0}}, {"primary": [[2, 77]]}, str(FAKE))

    d1, s1 = g.load_cache(str(Path.home() / ".codex"))
    d2, s2 = g.load_cache(str(FAKE))
    check("cache: account A isolated", d1["primary"]["used"], 11.0)
    check("cache: account B isolated", d2["primary"]["used"], 77.0)
    check("cache: samples A isolated", s1["primary"], [[1, 11]])
    check("cache: samples B isolated", s2["primary"], [[2, 77]])

    # --- 3. _resolve_account 优先级
    obj = object.__new__(g.Gauge)
    obj.accounts = accs
    obj.cfg = {"active_account": str(FAKE)}
    check("resolve: uses saved active", obj._resolve_account(), str(FAKE))

    obj.cfg = {"active_account": "/nonexistent/path"}
    check("resolve: falls back to first", obj._resolve_account(), accs[0]["path"])

    obj.cfg = {}
    obj.accounts = []
    check("resolve: falls back to CODEX_HOME", obj._resolve_account(), str(g.CODEX_HOME))

finally:
    if FAKE.exists():
        shutil.rmtree(FAKE, ignore_errors=True)
        print("\ncleaned up", FAKE)

print("\nRESULT: %d/%d passed" % (sum(results), len(results)))
