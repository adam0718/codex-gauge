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

WIN_W, WIN_H = 252, 142
CARD = (6, 6, 246, 136)
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
    "follow_codex": False,
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
    """返回 (data, samples)。兼容旧版只有 data 的缓存文件。"""
    try:
        if CACHE_FILE.exists():
            raw = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and "data" in raw:
                return raw.get("data"), raw.get("samples") or {}
            return raw, {}
    except Exception:
        pass
    return None, {}


def save_cache(data: dict, samples: dict) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(
            json.dumps({"data": data, "samples": samples}, ensure_ascii=False),
            encoding="utf-8",
        )
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


def fmt_clock(ts: float) -> str:
    """时间戳 → 紧凑时刻。今天只给 HH:MM，明天带前缀，更远给日期。"""
    if not ts:
        return "--"
    try:
        lt = time.localtime(ts)
        now = time.localtime()
        today0 = time.mktime(
            (now.tm_year, now.tm_mon, now.tm_mday, 0, 0, 0, 0, 0, -1)
        )
        days = int((ts - today0) // 86400)
        if days == 0:
            return time.strftime("%H:%M", lt)
        if days == 1:
            return time.strftime("明 %H:%M", lt)
        return time.strftime("%m-%d", lt)
    except Exception:
        return "--"


# ---------------------------------------------------------------- 进程探测


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.wintypes.DWORD),
        ("cntUsage", ctypes.wintypes.DWORD),
        ("th32ProcessID", ctypes.wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", ctypes.wintypes.DWORD),
        ("cntThreads", ctypes.wintypes.DWORD),
        ("th32ParentProcessID", ctypes.wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("szExeFile", ctypes.wintypes.WCHAR * 260),
    ]


def codex_running() -> bool:
    """Codex 桌面应用是否在运行。

    用进程快照而不是 tasklist 子进程：后者每次要起一个控制台进程，
    每 3 秒调一次太浪费。快照遍历是纯内存操作，微秒级。
    """
    TH32CS_SNAPPROCESS = 0x00000002
    INVALID = ctypes.c_void_p(-1).value
    k32 = ctypes.windll.kernel32
    k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    k32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_ulong, ctypes.c_ulong]
    k32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PROCESSENTRY32W)]
    k32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PROCESSENTRY32W)]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]

    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID:
        return True  # 探测失败时别误判成"没在跑"，否则窗口会莫名消失

    entry = _PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
    try:
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.lower() in ("codex.exe", "codex"):
                return True
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
        return False
    finally:
        k32.CloseHandle(snap)


# ---------------------------------------------------------------- 窗口


class Gauge:
    def __init__(self, root: tk.Tk, cfg: dict):
        self.root = root
        self.cfg = cfg
        self.data, self.samples = load_cache()
        self.error = None
        self.fetching = False
        self._drag = None
        self._idle_job = None
        self._tick_job = None
        self._refresh_job = None
        self._watch_job = None
        self._hidden = False

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
        self.root.after(200, self._watch_codex)

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
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        pos = self.cfg.get("pos")
        if isinstance(pos, (list, tuple)) and len(pos) == 2:
            try:
                x, y = int(pos[0]), int(pos[1])
                # 必须整块落在屏幕内，否则底部信息会被屏幕边缘切掉
                x = max(0, min(x, sw - WIN_W))
                y = max(0, min(y, sh - WIN_H))
                return x, y
            except Exception:
                pass
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
        self.follow_var = tk.BooleanVar(value=bool(self.cfg.get("follow_codex")))
        self.menu.add_checkbutton(
            label="跟随 Codex 启动", variable=self.follow_var, command=self._toggle_follow
        )
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
        # 拖动时就把窗口夹在屏幕内，避免存下一个半出界的位置
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = max(0, min(x, sw - WIN_W))
        y = max(0, min(y, sh - WIN_H))
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

    # -------------------------------------------------- 跟随 Codex

    def _watch_codex(self):
        """每 3 秒看一次 Codex 在不在：在就露出来，不在就收起来。"""
        try:
            if self.cfg.get("follow_codex"):
                running = codex_running()
                if running and self._hidden:
                    self._show_window()
                elif not running and not self._hidden:
                    self._hide_window()
        except Exception:
            pass
        self._watch_job = self.root.after(3000, self._watch_codex)

    def _hide_window(self):
        self.root.withdraw()
        self._hidden = True

    def _show_window(self):
        self.root.deiconify()
        self._hidden = False

        def sync_alpha():
            # withdraw / deiconify 之后分层属性可能被重置，这里按鼠标位置补一次
            try:
                px, py = self.root.winfo_pointerxy()
                x, y = self.root.winfo_rootx(), self.root.winfo_rooty()
                w, h = self.root.winfo_width(), self.root.winfo_height()
                inside = x <= px < x + w and y <= py < y + h
            except Exception:
                inside = False
            self.set_alpha(self.cfg["hover_alpha"] if inside else self.cfg["idle_alpha"])

        self.root.after(60, sync_alpha)

    def _toggle_follow(self):
        on = bool(self.follow_var.get())
        self.cfg["follow_codex"] = on
        save_config(self.cfg)
        if not on and self._hidden:
            self._show_window()

    def _force_show(self):
        """被第二次启动唤起时：关掉跟随并显示，免得用户被锁在窗口外。"""
        self.cfg["follow_codex"] = False
        save_config(self.cfg)
        try:
            self.follow_var.set(False)
        except Exception:
            pass
        if self._hidden:
            self._show_window()
        try:
            self.root.lift()
        except Exception:
            pass

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
        self.foot = cv.create_text(WIN_W / 2, 120, text="", fill=C_FOOT,
                                   font=("Microsoft YaHei UI", 8))

    def _row(self, y, label, win):
        cv = self.canvas
        x1, x2 = 52, 186
        cv.create_text(20, y, text=label, anchor="w", fill=C_DIM,
                       font=("Segoe UI Semibold", 8), tags="dyn")
        round_rect(cv, x1, y - 3, x2, y + 4, 3.5, fill=C_TRACK, outline="", tags="dyn")
        if not win:
            cv.create_text(240, y, text="--", anchor="e", fill=C_DIM,
                           font=("Segoe UI Semibold", 8), tags="dyn")
            return
        pct = max(0.0, min(100.0, win["used"]))
        color = level_color(pct)
        bar_w = (x2 - x1) * pct / 100.0
        if bar_w >= 2:
            round_rect(cv, x1, y - 3, x1 + max(4, bar_w), y + 4, 3.5,
                       fill=color, outline="", tags="dyn")
        cv.create_text(240, y, text="%d%%" % round(pct), anchor="e",
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
            round_rect(cv, 240 - tw, 15, 240, 29, 7, fill=color, outline="", tags="dyn")
            cv.create_text(240 - tw / 2, 22, text=plan, fill="#FFFFFF",
                           font=("Segoe UI Semibold", 7), tags="dyn")

        primary = (d or {}).get("primary")
        secondary = (d or {}).get("secondary")
        self._row(50, "5H", primary)
        self._row(74, "7D", secondary)
        self._paint_forecast(primary, secondary)
        self._paint_foot()

    def _paint_forecast(self, primary, secondary):
        """预测行：按当前消耗速度，各自的窗口会不会在重置前打满。"""
        cv = self.canvas
        for tag, key, win, x, anchor in (
            ("5H", "primary", primary, 20, "w"),
            ("7D", "secondary", secondary, 240, "e"),
        ):
            text, color = self._forecast_label(tag, key, win)
            cv.create_text(x, 98, text=text, anchor=anchor, fill=color,
                           font=("Microsoft YaHei UI", 8), tags="dyn")

    def _forecast_label(self, tag, key, win):
        state, eta = self._forecast(key, win)
        if state == "full":
            return tag + " 已用尽", C_DANGER
        if state == "unknown":
            return tag + " 采样中", C_FOOT
        if state == "safe":
            return tag + " 不会打满", C_DIM
        left = eta - time.time()
        color = C_DANGER if left < 3600 else C_WARN
        return "%s %s 打满" % (tag, fmt_clock(eta)), color

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
            # 「用尽 / 打满」由上面的预测行负责，这里只交代重置时刻，避免重复
            parts = []
            for label, w in (("5H", d.get("primary")), ("7D", d.get("secondary"))):
                if w and w.get("reset_at"):
                    parts.append("%s %s" % (label, fmt_clock(w["reset_at"])))
            text = "重置 " + " · ".join(parts) if parts else "额度正常"
        self.canvas.itemconfigure(self.foot, text=text, fill=color)

    # -------------------------------------------------- 用量预测

    def _record_sample(self, key, used, ts):
        """记录一次采样。用量突降视为窗口已重置，清空历史重新计速。"""
        arr = self.samples.setdefault(key, [])
        if arr and used < arr[-1][1] - 5:
            arr.clear()
        arr.append([round(ts, 1), round(float(used), 2)])
        if len(arr) > 40:
            del arr[:-40]

    def _rate(self, key):
        """最近若干采样的消耗速度，单位 %/小时。采样不足或跨度过短返回 None。"""
        arr = self.samples.get(key) or []
        if len(arr) < 2:
            return None
        recent = arr[-6:]
        t0, u0 = recent[0]
        t1, u1 = recent[-1]
        dt = t1 - t0
        if dt < 45:  # 跨度太短，噪声压过信号
            return None
        return (u1 - u0) / (dt / 3600.0)

    def _forecast(self, key, win):
        """返回 (状态, 预计打满时间戳)。状态：full / safe / burn / unknown。"""
        if not win:
            return "unknown", None
        used = win.get("used", 0)
        if used >= 100:
            return "full", None
        rate = self._rate(key)
        if rate is None:
            return "unknown", None
        if rate <= 0.02:  # 每小时不到 0.02%，等同于不消耗
            return "safe", None
        eta = time.time() + (100.0 - used) / rate * 3600.0
        reset_at = win.get("reset_at") or 0
        if reset_at and eta >= reset_at:
            return "safe", None  # 重置先到，打不满
        return "burn", eta

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
        now = time.time()
        for key in ("primary", "secondary"):
            win = data.get(key)
            if win:
                self._record_sample(key, win["used"], now)
        save_cache(data, self.samples)
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
    """True = 本进程是主实例；False = 已有实例（已顺手通知它显示出来）。"""
    global _LOCK_SOCKET
    try:
        _LOCK_SOCKET = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _LOCK_SOCKET.bind(("127.0.0.1", 49731))
        _LOCK_SOCKET.listen(1)
        return True
    except OSError:
        # 已经有一个在跑。让它显形并退出跟随模式——否则跟随开启且 Codex 没开时，
        # 窗口是隐藏的，用户会以为程序坏了，再点也没反应。
        try:
            conn = socket.create_connection(("127.0.0.1", 49731), timeout=2)
            conn.sendall(b"show")
            conn.close()
        except Exception:
            pass
        return False


def _serve_show_requests(gauge: "Gauge") -> None:
    """主实例监听「把我叫出来」的请求。"""

    def loop():
        while True:
            try:
                conn, _ = _LOCK_SOCKET.accept()
                data = conn.recv(32)
                conn.close()
                if data.strip() == b"show":
                    gauge.root.after(0, gauge._force_show)
            except Exception:
                break

    threading.Thread(target=loop, daemon=True).start()


def main():
    if not _single_instance():
        return
    cfg = load_config()
    root = tk.Tk()
    gauge = Gauge(root, cfg)
    _serve_show_requests(gauge)
    root.mainloop()


if __name__ == "__main__":
    main()
