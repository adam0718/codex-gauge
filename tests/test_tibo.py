"""Tibo 重置预测的单元测试（不启动 GUI，不打网络）。

跑法：python tests/test_tibo.py

样本来自 codex-reset.com /api/forecast 的真实响应（2026-10-23 实测裁剪）。
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import codex_gauge as g  # noqa: E402

results = []


def check(name, got, want):
    ok = got == want
    results.append(ok)
    print("%-44s %-30s %s" % (name, got, "PASS" if ok else "FAIL (want %s)" % (want,)))


# --- 真实响应样本（裁剪到浮窗用到的字段）
PAYLOAD = {
    "mode": "announced",
    "updated_at": "2026-10-23T08:28:04.076Z",
    "probabilities": {
        "rounded_24h": 32,
        "rounded_48h": 53,
        "commitment": 0.93,
    },
    "signal_score": {"band": "dated_commitment", "base": 93, "value": 93},
    "official_signal": {
        "summary": "Landing tomorrow before 10am PST together with a small ship.",
        "at": "2026-10-23T04:47:34.000Z",
        "url": "https://x.com/thsottiaux/status/2113492502355562875",
        "signal_tier": "likely",
        "window": {
            "label": "by 10 AM PT on Oct 23",
            "target_at": "2026-10-23T17:00:00.000Z",
            "localized_label": "10月23日上午10点（PT）前",
        },
        "localized_summary": "明天太平洋标准时间上午10点前与一艘小船一同降落。",
    },
}

t = g.normalize_tibo(PAYLOAD)
check("normalize: mode", t["mode"], "announced")
check("normalize: prob_24h", t["prob_24h"], 32)
check("normalize: prob_48h", t["prob_48h"], 53)
check("normalize: band", t["band"], "dated_commitment")
check("normalize: tier", t["tier"], "likely")
check("normalize: label 取本地化", t["label"], "10月23日上午10点（PT）前")
check("normalize: summary 取中文", "明天" in t["summary"], True)
check("normalize: target_at 在未来", t["target_at"] > g._parse_ts("2026-10-23T10:00:00Z"), True)
check("normalize: updated_at 解析成功", t["updated_at"] > 0, True)

# --- 接口异常 / 改版：安静退场，返回 None
check("normalize: 无 probabilities", g.normalize_tibo({"mode": "x"}), None)
check("normalize: 非 dict", g.normalize_tibo(None), None)
check("normalize: 空 probabilities 字典", g.normalize_tibo({"probabilities": {}})["prob_24h"], None)
check("normalize: 无 signal 不炸", g.normalize_tibo({"probabilities": {"rounded_24h": 5}})["target_at"], 0.0)

# --- 概率越界钳制
odd = g.normalize_tibo({"probabilities": {"rounded_24h": 250, "rounded_48h": -5}})
check("normalize: 24h 钳到 100", odd["prob_24h"], 100)
check("normalize: 48h 钳到 0", odd["prob_48h"], 0)

# --- _parse_ts 边界
check("ts: 空串", g._parse_ts(""), 0.0)
check("ts: 垃圾串", g._parse_ts("not-a-time"), 0.0)
check("ts: None", g._parse_ts(None), 0.0)
check("ts: 小写 z 后缀", g._parse_ts("2026-10-23T08:28:04z") > 0, True)
check("ts: 无时区后缀", g._parse_ts("2026-10-23T08:28:04") > 0, True)

# --- tibo_line 文案与配色
now = g._parse_ts("2026-10-23T10:00:00Z")
left, right, color = g.tibo_line(t, now)
check("line: 未来目标带时间", "预测重置" in left, True)
check("line: 右侧概率", right, "24h 32% · 48h 53%")
check("line: 32%% 用黄色", color, g.C_WARN)

past = dict(t, target_at=g._parse_ts("2026-10-20T10:00:00Z"))
left2, right2, _ = g.tibo_line(past, now)
check("line: 目标已过", left2, "Tibo · 下次信号待定")
check("line: 目标已过仍带概率", right2, "24h 32% · 48h 53%")

low = g.normalize_tibo({"probabilities": {"rounded_24h": 10, "rounded_48h": 20}})
_, _, c_low = g.tibo_line(low, now)
check("line: 低概率灰色", c_low, g.C_DIM)
high = g.normalize_tibo({"probabilities": {"rounded_24h": 61, "rounded_48h": 80}})
_, _, c_high = g.tibo_line(high, now)
check("line: 高概率绿色", c_high, g.C_OK)
check("line: 无概率时右侧为空",
      g.tibo_line(g.normalize_tibo({"probabilities": {}}), now)[1], "")
check("line: 无数据", g.tibo_line(None, now), (None, None, None))

# --- 配置与常量
check("config: show_tibo 默认开", g.DEFAULT_CONFIG.get("show_tibo"), True)
check("const: 节流 >= 60s", g.TIBO_MIN_INTERVAL >= 60, True)
check("const: 接口是 https", g.TIBO_FORECAST_URL.startswith("https://"), True)

print()
print("tibo: %d/%d passed" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
