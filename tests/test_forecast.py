"""用量预测逻辑的单元测试（不启动 GUI）。

跑法：python tests/test_forecast.py
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import codex_gauge as g  # noqa: E402

# 绕过 GUI，只测数据逻辑
obj = object.__new__(g.Gauge)
obj.samples = {}

now = time.time()
results = []


def check(name, got, want):
    ok = got == want
    results.append(ok)
    print("%-42s %-28s %s" % (name, got, "PASS" if ok else "FAIL (want %s)" % (want,)))


# --- 1. 速率计算：10 分钟涨 10% → 60%/h
obj.samples = {"primary": [[now - 600, 40.0], [now, 50.0]]}
check("rate: 10%% in 10min", round(obj._rate("primary"), 2), 60.0)

# --- 2. 采样不足
obj.samples = {"primary": [[now, 50.0]]}
check("rate: single sample -> None", obj._rate("primary"), None)

# --- 3. 跨度太短（<45s）判为不可靠
obj.samples = {"primary": [[now - 20, 50.0], [now, 51.0]]}
check("rate: span 20s -> None", obj._rate("primary"), None)

# --- 4. 预测打满：剩 40%，60%/h → 约 40 分钟后
obj.samples = {"primary": [[now - 600, 40.0], [now, 50.0]]}
win = {"used": 50.0, "reset_at": now + 7200}  # 2 小时后重置，够用
state, eta = obj._forecast("primary", win)
check("forecast: burn state", state, "burn")
check("forecast: eta 50%%/60%%h = 50min", round((eta - now) / 60), 50)

# --- 5. 重置先到 → safe
win = {"used": 50.0, "reset_at": now + 1200}  # 20 分钟后重置
check("forecast: reset first -> safe", obj._forecast("primary", win)[0], "safe")

# --- 6. 已用尽
check("forecast: used 100 -> full",
      obj._forecast("primary", {"used": 100.0, "reset_at": now + 3600})[0], "full")

# --- 7. 几乎不消耗 → safe
obj.samples = {"primary": [[now - 3600, 50.0], [now, 50.01]]}
check("forecast: idle -> safe", obj._forecast("primary", {"used": 50.0, "reset_at": now + 3600})[0], "safe")

# --- 8. 窗口重置检测：用量从 80 掉到 5，历史清空
obj.samples = {}
obj._record_sample("primary", 70, now - 300)
obj._record_sample("primary", 80, now - 120)
obj._record_sample("primary", 5, now)
check("reset: history cleared to 1 point", len(obj.samples["primary"]), 1)

# --- 9. 采样上限 40
obj.samples = {}
for i in range(60):
    obj._record_sample("primary", i, now - (60 - i) * 60)
check("samples capped at 40", len(obj.samples["primary"]), 40)

# --- 10. fmt_clock
t = time.localtime()
today_1530 = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 15, 30, 0, 0, 0, -1))
check("fmt_clock: today", g.fmt_clock(today_1530), "15:30")
check("fmt_clock: zero", g.fmt_clock(0), "--")

print()
print("RESULT: %d/%d passed" % (sum(results), len(results)))
