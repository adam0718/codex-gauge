# Codex Gauge · 技术决策

按时间顺序记录，重点是「为什么这样选」和「踩过什么坑」。

---

## D1 · 用 tkinter，不用 Electron / Tauri

**选了什么**：Python + tkinter，单文件，零第三方依赖。

**为什么**：
- 需求只有一个 250×124 的卡片，Electron 光运行时就要 150MB 内存
- Tauri 需要 Rust 工具链，为了一个小浮窗不值得
- tkinter 是标准库，用户机器上大概率已经有了，双击即用
- 单文件 600 行，后续改动成本极低

**代价**：tkinter 的透明与圆角能力弱，必须下到 Win32 API（见 D2）。

---

## D2 · 透明度与圆角：绕过 tk 的 `-alpha`，直接操作分层窗口

**选了什么**：`SetWindowLongW` 加 `WS_EX_LAYERED`，再用
`SetLayeredWindowAttributes(hwnd, colorkey, alpha, LWA_ALPHA | LWA_COLORKEY)`。

**为什么不能用 `wm_attributes('-alpha')`**：
tkinter 的 `-alpha` 只设 `LWA_ALPHA`；想做圆角还得用 `-transparentcolor`，它只设 `LWA_COLORKEY`。**两个属性互斥**，设了后者会覆盖前者 —— 结果要么是方角+半透明，要么是圆角+完全透明。

**关键发现**：Windows 的 `SetLayeredWindowAttributes` 其实**支持同时**传 `LWA_ALPHA | LWA_COLORKEY`。只要绕过 tkinter 的封装直接调 API，圆角和可变透明可以共存。

**实现要点**：
- 窗口和 Canvas 背景都设成魔术色 `#010203`，圆角外的像素被 colorkey 抠掉
- `GetAncestor(winfo_id(), GA_ROOT)` 拿真正的顶层句柄
- 每次都显式带两个 flag，不要依赖上一次的状态

**代价**：colorkey 是硬边缘，圆角有轻微锯齿，无抗锯齿。

---

## D3 · 数据源：官方用量接口，而不是解析本地会话

**考虑过的方案**：
1. 解析 `~/.codex/sessions/**/rollout-*.jsonl.zst` 里的 `rate_limits` 字段
2. 调 `https://chatgpt.com/backend-api/wham/usage`

**选了 2，但方案 1 已探明**：本地 rollout 里确实有真实结构 ——
`{"primary": {"used_percent", "window_minutes": 300, "resets_at"}, "secondary": {...}, "plan_type": "plus"}`，
和接口返回同源。之所以不选它：

- 需要 `zstandard` 解压 → 破坏零依赖
- 数据是**上次会话结束时**的快照，不是当前值（实测滞后 8 天）
- 要遍历几百个文件找最新，慢

接口方案是实时的、一次 HTTP 请求就够。方案 1 留作未来离线场景的备份思路。

**踩的坑**：`/backend-api/codex/usage` 返回 403，`/backend-api/me` 是用户信息不是用量。正确的端点是 **`/backend-api/wham/usage`**，需要同时带 `Authorization: Bearer` 和 `chatgpt-account-id`。

---

## D4 · 凭证只读，绝不代替 CLI 刷新 token

**决定**：只读 `auth.json` 拿 access_token，**不写回、不刷新**。

**为什么**：OpenAI 的 OAuth refresh token 是**轮转**的 —— 一旦外部用掉一次，Codex CLI 手里的那份就失效，用户下次用 codex 会被要求重新登录。这是破坏用户环境的行为，收益（省一次手动操作）远小于代价。

**代价**：token 过期时浮窗显示「凭证已过期，请打开一次 Codex」。用户点一下 CLI 就恢复了，可接受。

---

## D5 · 网络层自己写重试，不引入 requests

**现象**：第一次用系统 Python 请求时抛 `SSL: UNEXPECTED_EOF_WHILE_READING`，同样的代码重试就通了。国内访问 chatgpt.com 这类中断并不罕见。

**决定**：`urllib` + 3 次退避重试（0.7s / 1.4s）。鉴权类错误（401/403）不重试 —— 重试也不会好。

**为什么不引入 requests**：一个浮窗为了重试装 5MB 依赖不划算，`urllib` 够用。

---

## D6 · 淡出透明度默认 55%，不是 36%

**过程**：第一版设 0.36，实拍发现背景图标直接穿透到卡片上，文字几乎读不出来 —— 那个状态不是「淡出」，是「糊成一团」。

**结论**：0.55 是能同时做到「退到背景」和「文字仍可读」的平衡点。菜单里给了 40/55/70/85 四档让用户自己调。

**教训**：透明度这种参数不能靠想象，必须截图看真实效果。

---

## D7 · 额度用尽时改描边颜色，而不只是改数字

**为什么**：半透明状态下，红色的 "100%" 和红色的底部小字都会变淡，警示力不够。整圈描边变暗红（`#B33A46`）在半透明下依然醒目，且不会像纯红那样刺眼。

**实现**：`_paint_static` 里判断任一窗口 `used >= 100`，决定 outline 颜色。

---

## D8 · 启动器 bat 必须保持纯 ASCII

**踩的坑**：第一版 bat 里写了中文注释，cmd 用 GBK 解析 UTF-8 字节，把注释行拆成了命令执行 —— 报了一堆「不是内部或外部命令」，甚至把 `Launcher` 当成参数传给了 python。

**决定**：bat 内容全英文。中文路径通过 `cd /d "%~dp0"` + 相对路径 `src\codex_gauge.py` 规避，命令行里不出现中文。

**另一个坑**：PATH 里的 `pythonw` 指向了一个**没有 tkinter** 的精简 Python（managed 运行时）。如果直接用 `where pythonw` 会启动失败且毫无提示。

**解法**：启动器逐个候选执行 `python -c "import tkinter"` 验证，第一个通过的才用 `pyw`。顺序是：`py -3` → `%LOCALAPPDATA%\Programs\Python\Python3*` → `%ProgramFiles%\Python3*`。

---

## D9 · 单文件，不拆模块

**决定**：600 行全放 `src/codex_gauge.py`。

**为什么**：拆成 `ui.py` / `api.py` / `config.py` 会让用户双击时路径解析变复杂（相对导入问题），也让「拷贝一个文件就能用」这个优势消失。这个规模下，单文件的可读性损失远小于部署便利性的收益。

**何时该拆**：超过 1000 行，或者要加第二套 UI（托盘 / 网页版）时。
