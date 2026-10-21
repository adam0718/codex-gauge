#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Codex Gauge — Codex 额度悬浮窗
================================

一个常驻桌面的极简悬浮窗，实时显示 Codex（ChatGPT 账号）的额度消耗：

    5H 窗口   — 滚动 5 小时用量，到 100% 后需要等待重置
    7D 窗口   — 滚动 7 天用量

交互：
    鼠标移入  → 完全显示
    鼠标移出  → 半透明淡出，不挡视线
    左键拖动  → 移动位置（位置自动记忆）
    双击      → 立即刷新
    右键      → 菜单

数据来源：Codex CLI 登录凭证（~/.codex/auth.json）+ ChatGPT 官方用量接口。
凭证只读、不落盘、不写回，刷新 token 的动作留给 Codex CLI 自己完成。

依赖：Python 3.9+ 标准库。零第三方依赖。
"""

import ctypes
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import tkinter as tk

# ---------------------------------------------------------------- 常量

APP_NAME = "CodexGauge"
USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
CODEX_HOME = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
CONFIG_DIR = Path(os.environ.get("APPDATA") or Path.home()) / APP_NAME
CONFIG_FILE = CONFIG_DIR / "config.json"
CACHE_FILE = CONFIG_DIR / "cache.json"

WIN_W, WIN_H = 250, 124
CARD = (6, 6, 244, 118)
CARD_R = 14

# 魔术色：圆角之外的所有像素都会被系统抠掉
KEY_COLOR_HEX = "#010203"
KEY_COLOR_REF = 0x00030201  # COLORREF: 0x00BBGGRR

C_BG = "#0F1218"
C_BORDER = "#242C3A"
C_EDGE_ALERT = "#B33A46"
C_TRACK = "#1B2230"
C_TITLE = "#E8EEF9"
C_DIM = "#697687"
C_FOOT = "#6E7A8C"

C_OK = "#37D39A"
C_WARN = "#F5C542"
C_DANGER = "#FF5D5D"
C_OFFLINE = "#4B5563"

PLAN_COLORS = {
    "plus": "#7B61FF",
    "pro": "#F5A623",
    "team": "#3B82F6",
    "business": "#0EA5E9",
    "enterprise": "#0EA5E9",
    "free": "#4B5563",
}

DEFAULT_CONFIG = {
    "idle_alpha": 0.55,
    "hover_alpha": 1.0,
    "refresh_seconds": 60,
    "pos": None,
    "rounded": True,
}

# ---------------------------------------------------------------- 配置


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        if CONFIG_FILE.exists():
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
    except Exception:
        pass
    return cfg


def save_config(cfg: dict) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


# ---------------------------------------------------------------- 数据层


class UsageError(Exception):
    pass


def _read_auth():
    """读取 Codex CLI 的登录凭证。只读，绝不修改。"""
    path = CODEX_HOME / "auth.json"
    if not path.exists():
        raise UsageError("未找到 Codex 登录凭证")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        raise UsageError("凭证文件无法解析")
    tokens = data.get("tokens") or {}
    access = tokens.get("access_token")
    account = tokens.get("account_id")
    if not access:
        raise UsageError("请先登录 Codex CLI")
    return access, account


def _jwt_expired(token: str) -> bool:
    try:
        import base64

        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        return float(claims.get("exp", 0)) < time.time()
    except Exception:
        return False


def fetch_usage() -> dict:
    """拉取一次实时额度。网络/鉴权问题一律抛 UsageError。

    网络抖动（TLS 被中途掐断这类）在国内访问 chatgpt.com 时并不罕见，
    所以这里带两次重试；鉴权类错误不重试，重试也没意义。
    """
    access, account = _read_auth()

    if _jwt_expired(access):
        raise UsageError("凭证已过期，请打开一次 Codex")

    headers = {
        "Authorization": "Bearer " + access,
        "Accept": "application/json",
        "User-Agent": "codex-gauge/1.0",
    }
    if account:
        headers["chatgpt-account-id"] = account

    last = None
    for attempt in range(3):
        req = urllib.request.Request(USAGE_URL, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode("utf-8", "replace")
            break
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise UsageError("鉴权失败，请重新登录 Codex")
            last = UsageError("接口返回 %s" % e.code)
        except Exception:
            last = UsageError("网络不可用")
        if attempt < 2:
            time.sleep(0.7 * (attempt + 1))
    else:
        raise last or UsageError("网络不可用")

    try:
        payload = json.loads(raw)
    except Exception:
        raise UsageError("接口数据异常")

    return normalize(payload)


def normalize(payload: dict) -> dict:
    rl = payload.get("rate_limit") or {}

    def window(key):
        w = rl.get(key) or {}
        used = w.get("used_percent")
        if used is None:
            return None
        return {
            "used": float(used),
            "span_s": int(w.get("limit_window_seconds") or 0),
            "reset_at": float(w.get("reset_at") or 0),
        }

    primary = window("primary_window")
    secondary = window("secondary_window")
    if primary is None and secondary is None:
        raise UsageError("该账号暂无额度信息")

    return {
        "plan": (payload.get("plan_type") or "").lower() or "unknown",
        "primary": primary,
        "secondary": secondary,
        "allowed": bool(rl.get("allowed", True)),
        "limit_reached": bool(rl.get("limit_reached", False)),
        "fetched_at": time.time(),
    }


def load_cache():
    try:
        if CACHE_FILE.exists():
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def save_cache(data: dict) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------- 绘制工具


def round_rect(cv, x1, y1, x2, y2, r, **kw):
    """圆角矩形（平滑多边形近似）。"""
    r = max(1, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = [
        x1 + r, y1, x2 - r, y1, x2, y1,
        x2, y1 + r, x2, y2 - r, x2, y2,
        x2 - r, y2, x1 + r, y2, x1, y2,
        x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return cv.create_polygon(pts, smooth=True, **kw)


def level_color(pct: float) -> str:
    if pct >= 85:
        return C_DANGER
    if pct >= 60:
        return C_WARN
    return C_OK


def fmt_delta(seconds: float) -> str:
    seconds = int(seconds)
    if seconds <= 0:
        return "已重置"
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return "%dd%dh" % (d, h)
    if h:
        return "%dh%02dm" % (h, m)
    return "%dm" % m


def fmt_span(span_s: int) -> str:
    if span_s >= 86400:
        return "%dD" % round(span_s / 86400)
    if span_s >= 3600:
        return "%dH" % round(span_s / 3600)
    return "%dM" % max(1, round(span_s / 60))


# ---------------------------------------------------------------- 窗口


class Gauge:
    def __init__(self, root: tk.Tk, cfg: dict):
        self.root = root
        self.cfg = cfg
        self.data = load_cache()
        self.error = None
        self.fetching = False
        self._drag = None
        self._idle_job = None
        self._tick_job = None
        self._refresh_job = None

        self._hwnd = None
        self._layered = False

        self._build_window()
        self._bind_events()
        self._paint_static()
        self._paint_data()

        self.root.after(120, self._init_alpha)
        self.refresh()
        self._schedule_refresh()
        self._tick()

    # -------------------------------------------------- 窗口骨架

    def _build_window(self):
        r = self.root
        r.title("Codex Gauge")
        r.overrideredirect(True)
        r.attributes("-topmost", bool(self.cfg.get("always_on_top", True)))
        r.configure(bg=KEY_COLOR_HEX if self.cfg.get("rounded") else C_BG)
        r.geometry("%dx%d+%d+%d" % (WIN_W, WIN_H, *self._initial_pos()))
        r.resizable(False, False)

        self.canvas = tk.Canvas(
            r,
            width=WIN_W,
            height=WIN_H,
            bg=KEY_COLOR_HEX if self.cfg.get("rounded") else C_BG,
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack(fill="both", expand=True)

    def _initial_pos(self):
        pos = self.cfg.get("pos")
        if isinstance(pos, (list, tuple)) and len(pos) == 2:
            try:
                x, y = int(pos[0]), int(pos[1])
                sw = self.root.winfo_screenwidth()
                sh = self.root.winfo_screenheight()
                if -WIN_W < x < sw - 40 and -20 < y < sh - 40:
                    return x, y
            except Exception:
                pass
        sw = self.root.winfo_screenwidth()
        return sw - WIN_W - 28, 28

    # -------------------------------------------------- 分层窗口（圆角 + 可变透明）

    def _init_alpha(self):
        try:
            u32 = ctypes.windll.user32
            hwnd = u32.GetAncestor(self.root.winfo_id(), 2)  # GA_ROOT
            if not hwnd:
                hwnd = self.root.winfo_id()
            self._hwnd = hwnd
            GWL_EXSTYLE = -20
            WS_EX_LAYERED = 0x00080000
            ex = u32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            u32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex | WS_EX_LAYERED)
            self._layered = True
            self.set_alpha(self.cfg["idle_alpha"])
        except Exception:
            # 极端环境下退回 tk 自带的整体透明（直角）
            self._layered = False
            try:
                self.root.attributes("-alpha", self.cfg["idle_alpha"])
            except Exception:
                pass

    def set_alpha(self, value: float):
        value = max(0.05, min(1.0, float(value)))
        if self._layered and self._hwnd:
            LWA_ALPHA = 0x2
            LWA_COLORKEY = 0x1
            flags = LWA_ALPHA
            key = 0
            if self.cfg.get("rounded"):
                flags |= LWA_COLORKEY
                key = KEY_COLOR_REF
            try:
                ctypes.windll.user32.SetLayeredWindowAttributes(
                    self._hwnd, key, int(value * 255), flags
                )
                return
            except Exception:
                pass
        try:
            self.root.attributes("-alpha", value)
        except Exception:
            pass

    # -------------------------------------------------- 事件

    def _bind_events(self):
        c = self.canvas
        for w in (self.root, c):
            w.bind("<Enter>", self._on_enter)
            w.bind("<Leave>", self._on_leave)

        c.bind("<Button-1>", self._on_press)
        c.bind("<B1-Motion>", self._on_drag)
        c.bind("<ButtonRelease-1>", self._on_release)
        c.bind("<Double-Button-1>", lambda e: self.refresh())
        c.bind("<Button-3>", self._on_menu)

        self.menu = tk.Menu(self.root, tearoff=0, bg="#161C26", fg="#DDE5F2",
                            activebackground="#2A3446", activeforeground="#FFFFFF",
                            bd=0, font=("Microsoft YaHei UI", 9))
        self.menu.add_command(label="立即刷新", command=self.refresh)
        self.menu.add_command(label="重置位置", command=self._reset_pos)
        self.menu.add_separator()
        alpha_menu = tk.Menu(self.menu, tearoff=0, bg="#161C26", fg="#DDE5F2",
                             activebackground="#2A3446", activeforeground="#FFFFFF",
                             bd=0, font=("Microsoft YaHei UI", 9))
        for pct in (40, 55, 70, 85):
            alpha_menu.add_command(
                label="%d%%" % pct,
                command=lambda p=pct: self._set_idle_alpha(p / 100.0),
            )
        self.menu.add_cascade(label="淡出透明度", menu=alpha_menu)
        self.menu.add_separator()
        self.menu.add_command(label="退出", command=self._quit)

    def _on_enter(self, _=None):
        if self._idle_job:
            self.root.after_cancel(self._idle_job)
            self._idle_job = None
        self.set_alpha(self.cfg["hover_alpha"])

    def _on_leave(self, _=None):
        if self._idle_job:
            self.root.after_cancel(self._idle_job)
        self._idle_job = self.root.after(70, self._maybe_idle)

    def _maybe_idle(self):
        self._idle_job = None
        try:
            px, py = self.root.winfo_pointerxy()
            x, y = self.root.winfo_rootx(), self.root.winfo_rooty()
            w, h = self.root.winfo_width(), self.root.winfo_height()
            if x <= px < x + w and y <= py < y + h:
                return
        except Exception:
            pass
        self.set_alpha(self.cfg["idle_alpha"])

    def _on_press(self, e):
        self._drag = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())

    def _on_drag(self, e):
        if not self._drag:
            return
        x = e.x_root - self._drag[0]
        y = e.y_root - self._drag[1]
        self.root.geometry("+%d+%d" % (x, y))

    def _on_release(self, _=None):
        if self._drag:
            self.cfg["pos"] = [self.root.winfo_x(), self.root.winfo_y()]
            save_config(self.cfg)
        self._drag = None

    def _on_menu(self, e):
        try:
            self.menu.tk_popup(e.x_root, e.y_root)
        finally:
            self.menu.grab_release()

    def _reset_pos(self):
        self.cfg["pos"] = None
        save_config(self.cfg)
        self.root.geometry("+%d+%d" % self._initial_pos())

    def _set_idle_alpha(self, v):
        self.cfg["idle_alpha"] = v
        save_config(self.cfg)
        self._maybe_idle()

    def _quit(self):
        self.cfg["pos"] = [self.root.winfo_x(), self.root.winfo_y()]
        save_config(self.cfg)
        self.root.destroy()

    # -------------------------------------------------- 绘制

    def _paint_static(self):
        cv = self.canvas
        cv.delete("all")
        d = self.data
        # 任一窗口打满时，用暗红描边做全局警示——半透明状态下也一眼能看出来
        drained = bool(d) and any(
            w and w.get("used", 0) >= 100 for w in (d.get("primary"), d.get("secondary"))
        )
        border = C_EDGE_ALERT if drained else C_BORDER
        rounded = self.cfg.get("rounded", True)
        if rounded:
            round_rect(cv, *CARD, CARD_R, fill=C_BG, outline=border, width=1)
        else:
            cv.create_rectangle(*CARD, fill=C_BG, outline=border, width=1)

        self.dot = cv.create_oval(16, 18, 23, 25, fill=C_OFFLINE, outline="")
        cv.create_text(32, 22, text="CODEX", anchor="w", fill=C_TITLE,
                       font=("Segoe UI Semibold", 9))
        self.foot = cv.create_text(WIN_W / 2, 99, text="", fill=C_FOOT,
                                   font=("Microsoft YaHei UI", 8))

    def _row(self, y, label, win):
        cv = self.canvas
        x1, x2 = 52, 190
        cv.create_text(20, y, text=label, anchor="w", fill=C_DIM,
                       font=("Segoe UI Semibold", 8), tags="dyn")
        round_rect(cv, x1, y - 3, x2, y + 4, 3.5, fill=C_TRACK, outline="", tags="dyn")
        if not win:
            cv.create_text(238, y, text="--", anchor="e", fill=C_DIM,
                           font=("Segoe UI Semibold", 8), tags="dyn")
            return
        pct = max(0.0, min(100.0, win["used"]))
        color = level_color(pct)
        bar_w = (x2 - x1) * pct / 100.0
        if bar_w >= 2:
            round_rect(cv, x1, y - 3, x1 + max(4, bar_w), y + 4, 3.5,
                       fill=color, outline="", tags="dyn")
        cv.create_text(238, y, text="%d%%" % round(pct), anchor="e",
                       fill=color, font=("Segoe UI Semibold", 8), tags="dyn")

    def _paint_data(self):
        cv = self.canvas
        cv.delete("dyn")
        d = self.data

        # 状态点：取两个窗口里更紧张的那个
        used = max(
            (d or {}).get("primary", {}).get("used", 0) if d and d.get("primary") else 0,
            (d or {}).get("secondary", {}).get("used", 0) if d and d.get("secondary") else 0,
        )
        cv.itemconfigure(self.dot, fill=level_color(used) if d else C_OFFLINE)

        # 套餐徽章
        plan = ((d or {}).get("plan") or "").upper()
        if plan and plan != "UNKNOWN":
            color = PLAN_COLORS.get(plan.lower(), "#3A4557")
            tw = max(30, len(plan) * 5.8 + 14)
            round_rect(cv, 238 - tw, 15, 238, 29, 7, fill=color, outline="", tags="dyn")
            cv.create_text(238 - tw / 2, 22, text=plan, fill="#FFFFFF",
                           font=("Segoe UI Semibold", 7), tags="dyn")

        self._row(50, "5H", (d or {}).get("primary"))
        self._row(74, "7D", (d or {}).get("secondary"))
        self._paint_foot()

    def _paint_foot(self):
        if not hasattr(self, "foot"):
            return
        d = self.data
        color = C_FOOT
        if self.error and not d:
            text = self.error
        elif self.error:
            text = "%s · 上次数据" % self.error
        elif not d:
            text = "正在读取额度…"
        else:
            now = time.time()
            dead = [
                lb for lb, w in (("5H", d.get("primary")), ("7D", d.get("secondary")))
                if w and w["used"] >= 100
            ]
            if dead:
                color = C_DANGER
                w = d["primary"] if dead[0] == "5H" else d["secondary"]
                left = w["reset_at"] - now
                tail = fmt_delta(left) + " 后恢复" if left > 0 else "即将恢复"
                text = "%s 已用尽 · %s" % (dead[0], tail)
            else:
                parts = []
                for label, w in (("5H", d.get("primary")), ("7D", d.get("secondary"))):
                    if w and w.get("reset_at"):
                        parts.append("%s %s" % (label, fmt_delta(w["reset_at"] - now)))
                text = "重置 " + "  ·  ".join(parts) if parts else "额度正常"
        self.canvas.itemconfigure(self.foot, text=text, fill=color)

    # -------------------------------------------------- 刷新

    def refresh(self):
        if self.fetching:
            return
        self.fetching = True
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        try:
            data = fetch_usage()
            self.root.after(0, lambda: self._on_data(data))
        except UsageError as e:
            self.root.after(0, lambda: self._on_error(str(e)))
        except Exception:
            self.root.after(0, lambda: self._on_error("读取失败"))

    def _on_data(self, data):
        self.fetching = False
        self.error = None
        self.data = data
        save_cache(data)
        self._paint_static()
        self._paint_data()

    def _on_error(self, msg):
        self.fetching = False
        self.error = msg
        if self.data:
            self._paint_foot()
        else:
            self._paint_static()
            self._paint_data()

    def _schedule_refresh(self):
        secs = max(15, int(self.cfg.get("refresh_seconds", 60)))
        self._refresh_job = self.root.after(secs * 1000, self._auto_refresh)

    def _auto_refresh(self):
        self.refresh()
        self._schedule_refresh()

    def _tick(self):
        """每秒只更新倒计时文字，不重绘，避免闪烁。"""
        try:
            if self.data and not self.error:
                self._paint_foot()
            elif self.data and self.error:
                self._paint_foot()
        except Exception:
            pass
        self._tick_job = self.root.after(1000, self._tick)


# ---------------------------------------------------------------- 入口


def _single_instance() -> bool:
    global _LOCK_SOCKET
    try:
        _LOCK_SOCKET = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _LOCK_SOCKET.bind(("127.0.0.1", 49731))
        return True
    except OSError:
        return False


def main():
    if not _single_instance():
        return
    cfg = load_config()
    root = tk.Tk()
    Gauge(root, cfg)
    root.mainloop()


if __name__ == "__main__":
    main()
