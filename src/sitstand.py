# -*- coding: utf-8 -*-
"""
久坐提醒 · 升降桌（Sit / Stand Reminder）
================================================
面向升降桌场景的久坐提醒工具：
    坐下工作  ->  到点提醒  ->  站立活动  ->  回到坐下  ->  循环

设计原则（对应需求）：
  1. 双击即用：免安装、离线、启动快（可打包为便携程序）
  2. 平时无遮挡：默认完全不显示，悬浮细条可磁吸回收、可拖动、可关闭
  3. 到点必提醒：托盘图标显示剩余分钟 + 图标闪烁 + 气泡；提醒窗自动弹出
  4. 不用找入口：到点自动弹出可操作的提醒窗（我已站起 / 稍后 / 跳过）
  5. 常驻元素可控：所有常驻显示均默认关闭，可按需开启
  6. 声音默认关：提示音默认关闭，可单独开启（柔和提示音，非刺耳蜂鸣）
  7. 设置与实际一致：设置项即生效并落盘 settings.json

依赖：tkinter（内置）、Pillow、pywin32、pystray
"""

import os
import sys
import json
import math
import time
import queue
import struct
import wave
import logging
import threading
import traceback

_MODULE_T0 = time.perf_counter()   # 用于统计启动耗时


# ----------------------------------------------------------------------------
# 路径（兼容「源码运行」与「打包成 exe 运行」两种方式）
# ----------------------------------------------------------------------------
def _app_dir():
    if getattr(sys, "frozen", False):          # PyInstaller 打包后
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


APP_DIR = _app_dir()
SETTINGS_PATH = os.path.join(APP_DIR, "settings.json")
LOG_PATH = os.path.join(APP_DIR, "app.log")
CHIME_PATH = os.path.join(APP_DIR, "soft_chime.wav")

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    encoding="utf-8",
)
log = logging.getLogger("sitstand")
log.info("========== 启动 (frozen=%s) ==========", getattr(sys, "frozen", False))

APP_NAME = "久坐提醒"
APP_TITLE = "久坐提醒 · 升降桌"
APP_VERSION = "1.1"
MUTEX_NAME = "Local\\SitStandReminder_9f3a1c2b"

# ----------------------------------------------------------------------------
# 设计令牌（统一的浅色现代风格）
# ----------------------------------------------------------------------------
C_PAGE = "#EFF1F5"       # 窗口底色
C_BG = "#FFFFFF"         # 卡片
C_SURFACE = "#F5F6F9"    # 次级面
C_SURFACE_H = "#EAEDF2"  # 次级面 hover
C_SURFACE_A = "#E2E6EC"  # 次级面 active
C_BORDER = "#E2E5EB"
C_BORDER_2 = "#EDEFF3"
C_TEXT = "#14171C"
C_TEXT2 = "#565E6C"
C_MUTED = "#98A0AD"
C_SHADOW = "#AEB6C2"
C_SHADOW_SOFT = "#E9ECF0"

# 三个状态色各驱动一整套视觉（悬浮条填充 / 卡片进度条 / 状态点 / 托盘图标），
# 改这里就是全链路一起变，不存在“只改了一半”的情况。
C_SIT = "#3B6FE0"        # 坐姿：蓝（未变动）
C_STAND = "#0E9384"      # 站姿：青碧（原绿 #0F9D58，用户反馈“进度条填满了不好看”）
C_ALERT = "#DE6B62"      # 提醒：柔珊瑚（原正红 #E5484D，用户反馈“太扎眼”）
C_IDLE = "#98A2B3"       # 空闲/暂停：灰
C_ON = "#3B6FE0"         # 开关开启色
C_TRACK = "#D5DAE2"      # 开关关闭底色

TRANSPARENT_KEY = "#010203"      # 抠色键（极少见的颜色，避免误伤界面像素）
FONT_FAMILY = "Microsoft YaHei UI"

# DPI 缩放：字体用磅值由 Tk 自动按 DPI 放大，像素几何需我们按同一比例缩放，
# 这样在 125% / 150% 屏幕上既清晰又不跑版。
SCALE = 1.0


def set_ui_scale(s):
    global SCALE
    try:
        SCALE = max(0.75, min(3.0, float(s)))
    except Exception:
        SCALE = 1.0
    log.info("UI scale = %.3f" % SCALE)


def px(v):
    """把设计稿像素换算为当前 DPI 下的实际像素。"""
    return int(round(v * SCALE))


# ----------------------------------------------------------------------------
# 默认配置
# ----------------------------------------------------------------------------
DEFAULTS = {
    "sit_minutes": 45,
    "stand_minutes": 8,
    "alert_minutes": 1,            # 提醒间隔：到点后若未处理，每隔这么久再提醒一次
    "flash_tray": True,            # 托盘图标闪烁
    "balloon": True,               # 弹系统气泡
    "sound": False,                # 提示音（默认关闭）
    "alert_on_fullscreen": True,   # 全屏时仍然弹出提醒窗
    "auto_start": True,            # 启动即开始计时
    "snooze_minutes": 5,
    "tray_show_minutes": True,     # 托盘图标显示剩余分钟
}
# 运行期由程序自己写入、不进设置界面的内部键（带下划线前缀）：
#   _strip_side / _strip_y  —— 悬浮条吸附在哪边、竖直位置
#   _alert_x   / _alert_y   —— 提醒窗被拖到哪了（未拖过则不写，用默认右下角）


def load_settings():
    cfg = dict(DEFAULTS)
    try:
        if os.path.exists(SETTINGS_PATH):
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
    except Exception:
        log.exception("读取设置失败，使用默认值")
    for k, dflt in (("sit_minutes", 45), ("stand_minutes", 8), ("alert_minutes", 1)):
        try:
            cfg[k] = max(1, int(cfg[k]))
        except Exception:
            cfg[k] = dflt
    return cfg


def save_settings(cfg):
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        log.exception("保存设置失败")


# ----------------------------------------------------------------------------
# 依赖加载
# ----------------------------------------------------------------------------
def _fatal(msg):
    log.error(msg)
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, msg, APP_TITLE, 0x10)
    except Exception:
        pass


try:
    import tkinter as tk
    from tkinter import ttk
    from tkinter import font as tkfont
    from PIL import ImageTk
except Exception as e:  # pragma: no cover
    _fatal("无法加载 tkinter，请确认 Python 安装完整。\n\n%s" % e)
    raise

try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    from pystray import Icon, Menu, MenuItem
except Exception as e:  # pragma: no cover
    _fatal("缺少运行依赖，请先安装：\n\n    pip install pillow pystray pywin32\n\n"
           "详细错误：%s" % e)
    raise


# ----------------------------------------------------------------------------
# 工具函数
# ----------------------------------------------------------------------------
def enable_dpi_awareness():
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _is_hex_color(s):
    """判断是不是一个能直接喂给 Pillow 的 #RRGGBB 字符串。"""
    if not isinstance(s, str):
        return False
    t = s.lstrip("#")
    if len(t) != 6:
        return False
    try:
        int(t, 16)
        return True
    except ValueError:
        return False


def _rgb_to_hex(rgb):
    return "#%02X%02X%02X" % tuple(max(0, min(255, int(round(c)))) for c in rgb)


def mix(c1, c2, t):
    a, b = _hex_to_rgb(c1), _hex_to_rgb(c2)
    return _rgb_to_hex(tuple(a[i] + (b[i] - a[i]) * t for i in range(3)))


def lighten(c, t=0.12):
    return mix(c, "#FFFFFF", t)


def darken(c, t=0.12):
    return mix(c, "#000000", t)


# ---- 收起态「试管」液柱的配色 -----------------------------------------------
# 三段式（用户定稿）：
#   < 50%   : 状态基色（坐姿蓝 / 站姿青碧）
#   50–90%  : 由基色渐变到黄，越接近 90% 越深（亮黄 → 琥珀）
#   ≥ 90%   : 由琥珀渐变到提醒色，到 100% 完全变成柔珊瑚红
#
# 只有「黄色之前那一档」的两个端点色是硬编码的常量，它们是纯粹的中性黄，
# 不属于任何一个状态色系，所以不算"硬编码同色系深浅档"（见 C_SIT/C_STAND 约定）。
_LIQ_YELLOW = "#F2C744"        # 50% 起点：亮黄
_LIQ_AMBER = "#C98A16"         # 90% 终点：琥珀


def liquid_color(frac, base, alert=C_ALERT):
    """按剩余进度返回收起态液柱的颜色。

    Args:
        frac: 剩余比例，0.0–1.0（越大越接近到点）。
        base: 当前状态色（坐姿蓝 / 站姿青碧）。
        alert: 提醒色，默认 C_ALERT。
    """
    try:
        f = max(0.0, min(1.0, float(frac)))
    except (TypeError, ValueError):
        f = 0.0
    if f < 0.5:
        return base
    if f < 0.9:
        return mix(_LIQ_YELLOW, _LIQ_AMBER, (f - 0.5) / 0.4)
    return mix(_LIQ_AMBER, alert, (f - 0.9) / 0.1)


def pick_font_file(size, bold=True):
    candidates = [
        r"C:\Windows\Fonts\segoeuib.ttf" if bold else r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\msyhbd.ttc" if bold else r"C:\Windows\Fonts\msyh.ttc",
    ]
    for p in candidates:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    try:
        return ImageFont.load_default()
    except Exception:
        return None


def ensure_chime():
    """生成一段柔和的提示音（默认不播放），振幅刻意压低，避免惊吓。"""
    try:
        if os.path.exists(CHIME_PATH):
            return True
        sr = 44100

        def tone(freq, dur, amp):
            n = int(sr * dur)
            out = []
            fi, fo = sr * 0.03, sr * 0.10
            for i in range(n):
                env = max(0.0, min(1.0, i / fi, (n - i) / fo))
                out.append(amp * env * math.sin(2 * math.pi * freq * i / sr))
            return out

        samples = tone(659.25, 0.18, 0.20) + [0.0] * int(sr * 0.05) + tone(880.0, 0.24, 0.18)
        with wave.open(CHIME_PATH, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(b"".join(
                struct.pack("<h", int(max(-1.0, min(1.0, s)) * 32767)) for s in samples))
        return True
    except Exception:
        log.exception("生成提示音失败")
        return False


def play_soft_chime():
    try:
        import winsound
        if ensure_chime() and os.path.exists(CHIME_PATH):
            winsound.PlaySound(CHIME_PATH, winsound.SND_FILENAME | winsound.SND_ASYNC)
    except Exception:
        log.exception("播放提示音失败")


def configure_floating_window(win):
    """无边框浮窗的窗口级调整：**不画任何阴影**，只保证它不进任务栏。

    ⚠️ 为什么最终连 CS_DROPSHADOW 也去掉了（踩过两轮坑后的结论）：

    1) 自己画阴影 —— 死路。
       在 `-transparentcolor` 窗口里 `ImageTk.PhotoImage` 的 alpha **不参与桌面合成**，
       半透明像素会被 Tk 拿黑底混合 → 黑边；"拍平成不透明浅灰"又会在深色/彩色
       桌面上变成一圈**白色光晕**。

    2) 系统原生 CS_DROPSHADOW —— 也是死路，这才是用户说的"黑色边框"。
       CS_DROPSHADOW 是围着**窗口的整个矩形**画投影的，而画布为了呼吸感四周留了
       一圈透明（key colour）边。实测在**纯白背景**上采样窗口外沿：
         · 窗口内侧白色区域 = (255,255,255)  ✓
         · 窗口外沿 1~2px   = (23,24,26)     ← 近黑！
       投影正好落在透明留白上，被合成成一道**近黑色的描边**。
       用 PrintWindow 抓窗口自身内容也能看到：透明区域被渲染成 (0,0,0)。

    3) 更早的大坑：DWM 的
         DwmSetWindowAttribute(DWMWA_NCRENDERING_POLICY, DWMNCRP_ENABLED)
         + DwmExtendFrameIntoClientArea(1,1,1,1)
       会让 DWM 把整个窗口当成玻璃非客户区，表现成一整块不透明白玻璃板。
       **任何时候都不要调。**

    最终方案：**彻底不要阴影**（PAD=0，窗口尺寸 == 内容尺寸），
    靠圆角 + 1px 描边（C_BORDER）+ 抗锯齿定轮廓，在任何背景上都干净。
    这里只做一件事：确保它**不进任务栏、不进 Alt+Tab**（WS_EX_TOOLWINDOW）。
    """
    try:
        import ctypes
        u32 = ctypes.windll.user32
        hwnd = int(win.winfo_id())
        hwnd = u32.GetAncestor(hwnd, 2) or hwnd       # GA_ROOT

        GWL_EXSTYLE = -20
        WS_EX_TOOLWINDOW = 0x00000080
        WS_EX_APPWINDOW = 0x00040000

        u32.GetWindowLongW.restype = ctypes.c_long
        u32.SetWindowLongW.restype = ctypes.c_long
        ex = u32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        # 加回 TOOLWINDOW（不进任务栏 / Alt+Tab），并确保没有 APPWINDOW。
        u32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                           (ex | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW)

        SWP_NOMOVE, SWP_NOSIZE, SWP_NOZORDER, SWP_FRAMECHANGED = 0x2, 0x1, 0x4, 0x20
        u32.SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                         SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED)
        return True
    except Exception:
        log.debug("配置浮窗样式失败", exc_info=True)
        return False

def is_fullscreen():
    try:
        import win32gui
        import win32api
        import win32con
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd or hwnd == win32gui.GetDesktopWindow():
            return False
        if win32gui.GetClassName(hwnd) in (
                "Shell_TrayWnd", "Progman", "WorkerW", "Shell_SecondaryTrayWnd"):
            return False
        rect = win32gui.GetWindowRect(hwnd)
        hmon = win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST)
        m = win32api.GetMonitorInfo(hmon)["Monitor"]
        return (rect[0] <= m[0] and rect[1] <= m[1]
                and rect[2] >= m[2] and rect[3] >= m[3])
    except Exception:
        return False


def fmt_mmss(seconds):
    seconds = max(0, int(math.ceil(seconds)))
    m, s = divmod(seconds, 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return "%d:%02d:%02d" % (h, m, s)
    return "%02d:%02d" % (m, s)


def cursor_pos():
    """取真实鼠标坐标（物理像素）。

    拖动一定要用这个，不能用 Canvas 事件的 e.x/e.y —— 当窗口自己也在移动时，
    e.x 的参照系（控件左上角）跟着变，于是「窗口位移」和「鼠标位移」互相污染，
    表现就是拖动发飘、抖动、跟手不到位。
    """
    try:
        import win32api
        return win32api.GetCursorPos()
    except Exception:
        try:
            import ctypes
            class _P(ctypes.Structure):
                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
            p = _P()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(p))
            return (p.x, p.y)
        except Exception:
            return (0, 0)


def round_rect(canvas, x1, y1, x2, y2, r=10, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
           x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
           x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(pts, smooth=True, **kw)


def make_soft_shadow(w, h, radius, spread=7, spread_y=None,
                     opacity=0.15, color=C_SHADOW, key=None, steps=5):
    """离屏渲染一层柔和投影，**结果只含两种像素**：完全不透明 或 纯透明键色。

    ⚠️ 两个坑，都踩过（其实是一个坑的两面）：
      1) 在 `overrideredirect` + `-transparentcolor` 的窗口里，`ImageTk.PhotoImage`
         的 alpha **不参与和桌面的合成**。实测：白色 alpha=128 显示为 (128,128,129)
         —— Tk 把它和**黑色**做了 alpha 混合。所以任何半透明像素都会发黑，
         这就是之前那圈「黑边/黑框」的来源。
      2) 那就把半透明「拍平」成不透明白底吧？—— 于是整张阴影图变成一个**不透明的
         白色方块**，放在非白桌面上就是用户反馈的「多出来一个正方形的白框」。

    真正可行的做法：让阴影图里**只有两种像素**
      · alpha=255 的实色（颜色已经按想要的深浅选好，不再依赖 alpha）
      · 恰好等于透明键 key 的像素（会被 -transparentcolor 整片抠掉）
    渐变用「量化成 steps 档实色」来模拟，档与档之间的过渡虽然不如真渐变细腻，
    但在浅灰区间肉眼几乎看不出，而且绝不会出现白框或黑边。
    """
    if key is None:
        key = TRANSPARENT_KEY
    sy = spread if spread_y is None else spread_y
    W, Hh = int(w + spread * 2), int(h + sy * 2)
    small = Image.new("L", (max(64, W // 2), max(64, Hh // 2)), 0)
    d = ImageDraw.Draw(small)
    r = max(1, int(radius / 2))
    d.rounded_rectangle([spread // 2, sy // 2,
                         small.size[0] - 1 - spread // 2,
                         small.size[1] - 1 - sy // 2], radius=r, fill=255)
    big = small.resize((W, Hh), Image.LANCZOS)
    try:
        big = big.filter(ImageFilter.GaussianBlur(max(1.6, spread / 2.0)))
    except Exception:
        pass

    src = _hex_to_rgb(color)
    krgb = _hex_to_rgb(key)
    out = Image.new("RGB", (W, Hh), krgb)
    pxs = out.load()
    bp = big.load()
    # 把 0..255 的模糊强度量化成 steps 档；最外一档直接抠成键色（整片透出桌面）
    thr = [int(255 * (i + 1) / (steps + 1)) for i in range(steps)]
    for y in range(Hh):
        for x in range(W):
            v = bp[x, y]
            if v <= thr[0]:
                continue                      # 保持键色 → 被 -transparentcolor 抠掉
            k = 1
            while k < steps - 1 and v > thr[k]:
                k += 1
            t = opacity * (k + 1) / steps     # 越靠内越深
            pxs[x, y] = (int(src[0] + (255 - src[0]) * (1 - t)),
                         int(src[1] + (255 - src[1]) * (1 - t)),
                         int(src[2] + (255 - src[2]) * (1 - t)))
    # 注意：**不能**在这里对整图做 point() 兜底修正 —— 那会把我们精心设成键色的
    # 那些像素一起改掉，于是"透明"区域变成不透明深色，屏幕上就是一圈黑块。
    return out


def draw_soft_shadow(canvas, x1, y1, x2, y2, r=14, spread=4):
    """贴一张柔和投影（向下偏移一点，更像真实投影）。

    返回的图只含「实色」和「透明键色」两种像素，所以既不会出现半透明黑边，
    也不会在外圈留下一个不透明白方块。
    """
    w, h = x2 - x1, y2 - y1
    key = (int(w), int(h), int(r), int(spread))
    cache = getattr(canvas, "_shadow_cache", None)
    if cache is None:
        cache = canvas._shadow_cache = {}
    img = cache.get(key)
    if img is None:
        img = ImageTk.PhotoImage(make_soft_shadow(w, h, r, spread))
        if len(cache) > 16:
            cache.clear()
        cache[key] = img
    return canvas.create_image(x1 - spread, y1 - spread + 1.5, image=img, anchor="nw")


# ============================================================================
# 通用：基于 Pillow 的「抗锯齿圆角」绘制
#   —— tkinter 的多边形圆角在屏幕上看是锯齿状的，这里是导致「看着简陋」的主因。
#      统一改为在离屏图上用 LANCZOS 画好后贴进来，边缘就平滑了。
# ============================================================================
def _rounded_mask(size, radius):
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1],
                                        radius=max(0, int(radius)), fill=255)
    return m


def aa_rounded_image(w, h, radius, fill, outline=None, border=1, super_ratio=4,
                     opaque_bg=None):
    """生成一张抗锯齿的圆角矩形 RGBA 图。

    ⚠️ `-transparentcolor` 窗口里必须传 `opaque_bg`，而且它要填**图形本身的颜色**
    （白卡片就传 "#FFFFFF"），不能填键色，详见 `flatten_to_key`。

    Tk 的 `-transparentcolor` 是**精确色匹配**：只有 RGB 完全等于键色的像素才透明。
    而 LANCZOS 抗锯齿必然在边缘产出**半透明**像素（例如 alpha=12, rgb=(212,212,233)）。
    这些像素既不是键色、又几乎是透明的，Tk 会把它们当成不透明内容按黑底合成 →
    在图形四周出现一圈**灰黑色描边**（就是用户反馈的"黑色边框"）。

    传了 `opaque_bg` 之后，图片会被「压平」成**每个像素要么完全实色、要么完全等于
    键色**：先把半透明的抗锯齿边缘按 alpha 混合到 `opaque_bg` 上做出柔和过渡，
    再把所有 RGB == 键色 的像素强制设置成 alpha=0。这样：
      · 边缘仍有抗锯齿的柔和平滑感（因为提前混合过了）；
      · 但不再有任何半透明像素，`-transparentcolor` 能精确命中，没有黑边。
    """
    w, h = max(1, int(round(w))), max(1, int(round(h)))
    r = max(0, int(round(radius)))
    over = 0 if border <= 0 else 1

    # `fill=None` → **只画描边、内部全透明**（"试管管壁"用；不给 fill 塞占位色，
    # 否则管腔会被填成那个颜色，就看不出"空心"了）。
    fill_rgba = (_hex_to_rgb(fill) + (255,)) if fill else None
    outline_rgba = (_hex_to_rgb(outline) + (255,)) if outline else None

    if super_ratio > 1:
        bw, bh = w * super_ratio, h * super_ratio
        img = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        inset = over * super_ratio
        d.rounded_rectangle([inset, inset, bw - 1 - inset, bh - 1 - inset],
                            radius=r * super_ratio, fill=fill_rgba,
                            outline=outline_rgba,
                            width=border * super_ratio if outline else 0)
        img = img.resize((w, h), Image.LANCZOS)
    else:
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([over, over, w - 1 - over, h - 1 - over], radius=r,
                            fill=fill_rgba, outline=outline_rgba,
                            width=border if outline else 0)
    if opaque_bg is not None:
        img = flatten_to_key(img, opaque_bg)
    return img

def _harden_alpha(img, thresh=140, palette=None):
    """把抗锯齿的边缘**硬化**：alpha 二值化 + RGB 吸附到合法色板，去掉"发虚/发脏"。

    为什么需要（两个独立的脏，必须都治）：

    1. **边缘发虚**：`make_tube_image` 用 8 倍超采样 + LANCZOS 缩回，缩回后每条边
       都带一圈**半透明过渡像素**。管子只有 11~16px 宽，这圈过渡几乎占满整个
       管壁/缝隙，看起来就是"糊的"。
       → 治：按 alpha 二值化，`alpha < thresh` 判为外部（全透明），否则拉满 255。

    2. **颜色发脏（更隐蔽）**：只治 alpha 是不够的。LANCZOS 缩回时，一个"最终应该
       是纯灰"的像素，其 RGB 也可能是**灰和透明底的混合**，于是管壁本该只有
       `#7F7F7F` 一种灰，实测却渲染出 **15 种色阶**（126~139），还整体偏亮。
       这正是用户说的"看着脏又乱"。
       → 治：把每个不透明像素的 RGB **吸附到最近的合法色**（色板 = 管壁色 + 液柱色）。

    做法合起来：alpha 二值化 → 对留下的像素做**最近邻调色板吸附**。

    ⚠️ 与 `flatten_to_key` 的区别：那个是给 Tk `-transparentcolor` 用的，会把
    "外部"像素涂成**键色**（#010203）；这里只是把 alpha 归零、把颜色收拢，
    因为预览图/合成图不需要键色，透明就是真透明。

    Args:
        img: RGBA 图。
        thresh: alpha 阈值，>= 该值视为实心。
        palette: 合法颜色的 RGB 列表；None 则只做 alpha 硬化（旧行为）。
    """
    from PIL import Image as _I
    rgba = img.convert("RGBA")
    a = rgba.getchannel("A").point(lambda v: 255 if v >= thresh else 0)
    out = rgba.copy()
    out.putalpha(a)
    if palette:
        pal = [tuple(c) for c in palette]
        px = out.load()
        w, h = out.size
        # 直接逐像素吸附：图很小（≥16×150），用查找表更快
        lut = {}
        for y in range(h):
            for x in range(w):
                r, g, b, al = px[x, y]
                if al == 0:
                    continue
                key = (r, g, b)
                hit = lut.get(key)
                if hit is None:
                    hit = min(pal, key=lambda c: (c[0]-r)**2 + (c[1]-g)**2 + (c[2]-b)**2)
                    lut[key] = hit
                if hit != key:
                    px[x, y] = (hit[0], hit[1], hit[2], al)
    return out


def make_tube_image(w, h, frac, fill_hex, wall_ratio=0.18, gap_ratio=0.07,
                    frame_hex=None, ss=8, harden=True):
    """画一根「试管」：**粗灰管壁 + 透明管腔 + 内部细液柱**。

    这是收起态悬浮条的整体贴图（管壁、液柱一次性画在同一张图上再交给 Tk），
    所以不存在"外壳和液柱对不齐"的问题。

    几何按 **外框宽 w 的比例** 给定（见 StripWindow.TUBE_* 常量），
    但会先**换算成整数像素预算**再画，保证"缝隙"永远至少 1 设备像素：
        · 管壁  = max(1, round(w * wall_ratio))
        · 缝隙  = max(1, round(w * gap_ratio))      ← 关键：不能小于 1px，否则看不见
        · 液柱  = max(1, w - 2*(管壁 + 缝隙))       ← 由预算倒推，保证不溢出
    液柱是**两端圆角的小胶囊**，从管底往上长；高度 = 可用内高 * frac。

    ⚠️ 必须在 `ss` 倍超采样空间里画（设备像素尺度下 1px 圆角全是锯齿）；
    而且**液柱和管壁要画在同一张图上**，不能拆成两张再拼 ——
    拆分之后液柱位置只能靠猜管腔边界，小尺寸下必然溢出管壁。

    `harden=True` 会在缩回后做一次** alpha 硬化**（见 `_harden_alpha`），
    把 LANCZOS 留下的半透明过渡边去掉，得到"干净利落"的硬边。
    想要柔和过渡（例如做更大幅面的插画）时传 False。

    Args:
        w, h: 外框的设备像素尺寸。
        frac: 液位，0.0–1.0。
        fill_hex: 液柱颜色（已由 liquid_color() 算好）。
        ss: 超采样倍数。
        harden: 是否硬化 alpha 边缘（默认 True）。

    Returns:
        RGBA 图（尺寸 == (w, h)），**不压平**（管腔要能透出桌面）。
    """
    from PIL import Image as _I
    w, h = max(3, int(round(w))), max(3, int(round(h)))
    frame_hex = frame_hex or "#9AA0A8"

    # ---- 整数像素预算（先定各段占几像素，再画）----
    wall_px = max(1, int(round(w * wall_ratio)))
    gap_px = max(1, int(round(w * gap_ratio)))
    liq_px = w - 2 * (wall_px + gap_px)
    if liq_px < 1:
        # 宽太窄：让液柱至少占 1px，从缝隙里匀
        gap_px = max(1, gap_px - 1)
        liq_px = max(1, w - 2 * (wall_px + gap_px))

    W, H = w * ss, h * ss
    img = _I.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    wall = wall_px * ss
    gap = gap_px * ss
    lw = liq_px * ss

    # 外壳胶囊：把整张图当作一个圆角矩形（圆角 = 半宽），管壁沿它的边描一圈。
    # 描边路径要相对**外缘**内收 wall/2，外缘才正好落在 0..W-1。
    r_shell = (w / 2.0) * ss                     # 外壳圆角半径
    d.rounded_rectangle(
        [wall / 2.0, wall / 2.0, W - 1 - wall / 2.0, H - 1 - wall / 2.0],
        radius=max(0.0, r_shell - wall / 2.0),
        outline=_hex_to_rgb(frame_hex) + (255,),
        width=int(wall))

    # ---- 2. 液柱：贴管底往上长的「小胶囊」----
    #
    # ⚠️ 关键：液柱的可用区域不是「矩形内缩 wall+gap」，而是**管腔这个圆角矩形
    # 内缩 gap**。因为外壳两端是**半圆帽**（半径 = 半宽 ≈ 8px），矩形内缩会把
    # 液柱的平底直接怼到半圆帽上 —— 底部就没有缝隙了（这就是上一版的毛病）。
    # 正解：液柱自己也是一个圆角矩形，圆角 = r_shell - wall - gap（与管腔同心），
    # 上下边界则各留 gap。
    f = max(0.0, min(1.0, float(frac)))
    y_top = wall + gap                            # 管腔顶端（已含缝隙）
    y_bot = H - wall - gap                        # 管腔底端（已含缝隙）
    avail = y_bot - y_top
    lh = avail * f
    if lh >= 2.0:
        lx = int(round((W - lw) / 2.0))           # 水平居中
        ly = int(round(y_bot - lh))
        # 液柱圆角 = 外壳圆角 - 管壁 - 缝隙，保证与管腔同心；
        # 再按实际宽高夹取，避免液面很矮时圆角超过半高。
        #
        # ⚠️ 不要试图给液面做"圆顶/半月面"。测过：要肉眼看出圆顶，圆头必须向
        # 两侧**鼓出至少 1 设备像素**，而本尺寸下缝隙总共只有 3px，鼓出去就顶到
        # 管壁，"四周留缝"立刻破掉（实测有 6 行贴壁）。
        # 而且用户给的参考图本身就是「壁2.5 | 缝1 | 液柱4.5 | 缝1 | 壁3」——
        # 液柱只有 4.5px、缝隙只有 1px，本来就没有做圆顶的余量。
        # 所以这里老老实实按「同心圆角胶囊」画，保证缝隙永远成立。
        r_liq = max(0.5, min(r_shell - wall - gap, lw / 2.0, lh / 2.0))
        d.rounded_rectangle(
            [lx, ly, lx + lw - 1, y_bot - 1],
            radius=r_liq,
            fill=_hex_to_rgb(fill_hex) + (255,))

    out = img.resize((w, h), _I.LANCZOS)
    if harden:
        # 色板 = 唯一合法的两种实心色：管壁灰 + 液柱色。
        # 加进去之后，管壁就只剩一种灰，不会再有 15 个色阶的"脏"。
        out = _harden_alpha(out, palette=[_hex_to_rgb(frame_hex),
                                          _hex_to_rgb(fill_hex)])
    return out


def flatten_to_key(img, fill_hex=None, key_hex=None, thresh=96):
    """把 RGBA 图压平到「透明键色」体系：每个像素要么完全实色、要么完全等于键色。

    用于 `-transparentcolor` 窗口 —— 那里不允许出现任何半透明像素，
    否则会被 Tk 按黑底合成，在图形四周留下一圈灰黑描边。

    做法（两步，缺一不可）：
      1) 按 **原图的 alpha** 决定「里面 / 外面」：alpha < thresh 的算图形外部。
      2) 外部的像素直接涂成**透明键色**（alpha=0，让 Tk 抠掉）；
         内部的像素按 alpha 混合到 `fill_hex`（图形本色）上，保留抗锯齿的柔和过渡
         并且**彻底实心**（alpha=255）。
    这样边缘平滑、又没有任何半透明像素，`-transparentcolor` 能精确命中。

    ⚠️ `fill_hex` 一定要传**图形本身的颜色**（白卡片就传 "#FFFFFF"），
    不能传 TRANSPARENT_KEY —— 拿近黑的键色当底会把整圈边缘混成深灰描边。
    """
    from PIL import Image as _I
    fill = _hex_to_rgb(fill_hex) if fill_hex else (255, 255, 255)
    key = _hex_to_rgb(key_hex or TRANSPARENT_KEY)
    rgba = img.convert("RGBA")
    alpha = rgba.getchannel("A").load()
    base = _I.new("RGB", rgba.size, fill)
    base.paste(rgba, (0, 0), rgba)          # 按 alpha 混合到本色底上
    bp = base.load()
    out = _I.new("RGBA", rgba.size, (0, 0, 0, 0))
    op = out.load()
    w, h = rgba.size
    for y in range(h):
        for x in range(w):
            if alpha[x, y] < thresh:
                op[x, y] = (key[0], key[1], key[2], 0)   # 外部 → 键色（透明）
            else:
                op[x, y] = (bp[x, y][0], bp[x, y][1], bp[x, y][2], 255)  # 内部 → 全实
    return out


def make_progress_bar(w, h, frac, fg, bg=C_TRACK):
    """一根两端圆头、带抗锯齿的细进度条（比两条 create_line 叠在一起干净）。"""
    w, h = max(2, int(round(w))), max(2, int(round(h)))
    r = h / 2.0
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=r, fill=_hex_to_rgb(bg) + (255,))
    fw = int(round(w * min(1.0, max(0.0, frac))))
    if fw >= h:
        d.rounded_rectangle([0, 0, fw - 1, h - 1], radius=r,
                            fill=_hex_to_rgb(fg) + (255,))
    elif fw > 0:
        d.ellipse([0, 0, h - 1, h - 1], fill=_hex_to_rgb(fg) + (255,))
    return img


class AACanvas(tk.Canvas):
    """带抗锯齿圆角能力的画布基类（控件内部使用）。"""

    def _aa_rect(self, x0, y0, x1, y1, radius, fill, outline=None, border=1):
        w, h = int(round(x1 - x0)), int(round(y1 - y0))
        if w <= 0 or h <= 0:
            return None
        key = (w, h, int(radius), fill, outline, border)
        cache = getattr(self, "_aa_cache", None)
        if cache is None:
            cache = self._aa_cache = {}
        ph = cache.get(key)
        if ph is None:
            # opaque_bg=fill：所有控件都画在 -transparentcolor 的透明画布上，
            # 必须按自身颜色压平，否则抗锯齿的半透明边缘会被合成成深色描边。
            ph = ImageTk.PhotoImage(
                aa_rounded_image(w, h, radius, fill, outline, border,
                                 opaque_bg=(fill if _is_hex_color(fill) else C_BG)))
            if len(cache) > 64:
                cache.clear()
            cache[key] = ph
        return self.create_image(x0, y0, image=ph, anchor="nw")


# ============================================================================
# 控件：圆角按钮
# ============================================================================
class RoundButton(AACanvas):
    def __init__(self, master, text, command=None, kind="primary",
                 accent=C_SIT, height=36, min_width=0, bg=C_BG, padx=18,
                 font_size=10, radius=None):
        self._text = text
        self._cmd = command
        self._kind = kind
        self._accent = accent
        self._fs = font_size
        f = tkfont.Font(family=FONT_FAMILY, size=font_size,
                        weight="bold" if kind in ("primary", "danger") else "normal")
        height = px(height)
        min_width = px(min_width)
        padx = px(padx)
        w = max(min_width, f.measure(text) + padx * 2)
        self._cw, self._ch = w, height
        self._r = px(radius) if radius is not None else height / 2
        super().__init__(master, width=w, height=height, bg=bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._state = "normal"
        self._enabled = True
        self.bind("<Enter>", lambda e: self._set("hover"))
        self.bind("<Leave>", lambda e: self._set("normal"))
        self.bind("<Button-1>", lambda e: self._set("press"))
        self.bind("<ButtonRelease-1>", self._release)
        self._draw()

    def _set(self, st):
        if not self._enabled and st != "normal":
            return
        self._state = st
        self._draw()

    def _release(self, e):
        if not self._enabled:
            return
        if 0 <= e.x <= self._cw and 0 <= e.y <= self._ch:
            self._set("hover")
            if self._cmd:
                self._cmd()
        else:
            self._set("normal")

    def _colors(self):
        k, st = self._kind, self._state
        if k == "primary":
            base = self._accent
            return {"normal": base, "hover": lighten(base, 0.09),
                    "press": darken(base, 0.09)}[st], "#FFFFFF", None
        if k == "danger":
            # 三个浅底一律由 C_ALERT 现算，换主色时不会残留旧色相。
            _danger_bg = {"normal": mix(C_ALERT, "#FFFFFF", 0.93),
                          "hover": mix(C_ALERT, "#FFFFFF", 0.86),
                          "press": mix(C_ALERT, "#FFFFFF", 0.78)}
            return _danger_bg[st], C_ALERT, mix(C_ALERT, "#FFFFFF", 0.70)
        return {"normal": C_SURFACE, "hover": C_SURFACE_H,
                "press": C_SURFACE_A}[st], C_TEXT, C_BORDER

    def _draw(self):
        self.delete("all")
        fill, fg, border = self._colors()
        if not self._enabled:
            fill, fg, border = C_SURFACE, C_MUTED, C_BORDER_2
        self._aa_rect(0, 0, self._cw, self._ch, self._r, fill, border, 1)
        self.create_text(self._cw / 2, self._ch / 2 + px(0.5), text=self._text, fill=fg,
                         font=(FONT_FAMILY, self._fs,
                               "bold" if self._kind in ("primary", "danger") else "normal"))


# ============================================================================
# 控件：开关
# ============================================================================
class Switch(AACanvas):
    W, H = 46, 26

    def __init__(self, master, value=False, command=None, bg=C_BG):
        self.W, self.H = px(46), px(26)
        self._inset = px(3)
        super().__init__(master, width=self.W, height=self.H, bg=bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.value = bool(value)
        self._cmd = command
        self._knob = (self.W - self.H / 2 - self._inset) if self.value else (self.H / 2 + self._inset)
        self._target = self._knob
        self._anim = None
        self.bind("<Button-1>", self._toggle)
        self._draw()

    def _toggle(self, _e=None):
        self.value = not self.value
        self._target = (self.W - self.H / 2 - self._inset) if self.value else (self.H / 2 + self._inset)
        self._animate()
        if self._cmd:
            self._cmd(self.value)

    def set(self, v):
        self.value = bool(v)
        self._target = (self.W - self.H / 2 - self._inset) if self.value else (self.H / 2 + self._inset)
        self._knob = self._target
        self._draw()

    def _animate(self):
        if self._anim:
            try:
                self.after_cancel(self._anim)
            except Exception:
                pass
            self._anim = None
        step = 2.2 if self._target > self._knob else -2.2

        def run():
            if abs(self._target - self._knob) <= 2.2:
                self._knob = self._target
                self._draw()
                self._anim = None
                return
            self._knob += step
            self._draw()
            self._anim = self.after(10, run)

        run()

    def _draw(self):
        self.delete("all")
        H = self.H
        r = H / 2
        track = C_ON if self.value else C_TRACK
        self._aa_rect(0, 0, self.W, H, r, track, None, 0)
        cx, cy = self._knob, H / 2
        inset = self._inset
        kr = r - inset
        # 白色滑块：图内即带柔和描边，避免贴到浅色轨道上「糊」成一片
        key = ("knob", int(round(kr * 2)), C_ON if self.value else C_TRACK)
        cache = getattr(self, "_cache", None)
        if cache is None:
            cache = self._cache = {}
        ph = cache.get(key)
        if ph is None:
            size = int(round(kr * 2))
            img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            ImageDraw.Draw(img).ellipse(
                [0, 0, size - 1, size - 1], fill=(255, 255, 255, 255),
                outline=_hex_to_rgb(mix(C_TRACK, C_TEXT, 0.06)) + (70,), width=1)
            ph = ImageTk.PhotoImage(img)
            cache[key] = ph
        self.create_image(cx - kr, cy - kr, image=ph, anchor="nw")


# ============================================================================
# 控件：时长输入（可直接打字 + 加减微调，二者并存）
# ============================================================================
class NumberField(tk.Frame):
    """三段式时长输入：[ − ] [ 数字（可直接键入） ] [ + ] + 右侧单位。

    · 中间的数字区域是真正的输入框，点一下就能直接敲数字，支持回车确认、
      上下方向键微调、滚轮微调、失焦自动收尾。
    · 左右两个圆形按钮保留，方便「点一下减一 / 加一」。
    """

    def __init__(self, master, value, lo, hi, suffix="分钟", command=None,
                 bg=C_BG, width=124, height=32):
        super().__init__(master, bg=bg, highlightthickness=0, bd=0)
        self.lo, self.hi = int(lo), int(hi)
        self.suffix = suffix
        self._cmd = command
        self._busy = False
        self._var = tk.StringVar(value=str(int(value)))

        self.H = px(height)
        self._wrap = tk.Frame(self, bg=bg, highlightthickness=0, bd=0)
        self._wrap.pack(side="left")

        self._step_w = px(30)
        self._entry_w = px(width) - self._step_w * 2
        if self._entry_w < px(44):
            self._step_w = px(26)
            self._entry_w = px(width) - self._step_w * 2

        self.minus = RoundButton(self._wrap, "−", command=lambda: self._step(-1),
                                 kind="ghost", height=height, radius=height / 2,
                                 bg=bg, font_size=12, padx=0)
        self.minus.configure(width=self._step_w, height=self.H)
        self.minus._cw, self.minus._ch = self._step_w, self.H
        self.minus._r = self.H / 2
        self.minus._draw()
        self.minus.pack(side="left")

        # 数字区：做成一个圆角「输入胶囊」，视觉上跟两侧按钮连成一体
        self._mid = tk.Frame(self._wrap, bg=C_BG, highlightthickness=0, bd=0)
        self._mid.pack(side="left", padx=px(5))
        self.entry = tk.Entry(
            self._mid, textvariable=self._var, justify="center",
            font=(FONT_FAMILY, 12, "bold"), fg=C_TEXT, bg=C_SURFACE,
            relief="flat", bd=0, highlightthickness=px(1),
            highlightbackground=C_BORDER, highlightcolor=C_SIT,
            insertbackground=C_SIT, width=1, disabledbackground=C_SURFACE,
            disabledforeground=C_TEXT)
        self.entry.configure(width=max(3, int(self._entry_w / (px(9) or 1))))
        self.entry.pack(ipady=px(5), ipadx=px(3))
        try:
            self.entry.configure(selectbackground=C_SIT, selectforeground="#FFFFFF")
        except Exception:
            pass

        self.plus = RoundButton(self._wrap, "+", command=lambda: self._step(1),
                                kind="ghost", height=height, radius=height / 2,
                                bg=bg, font_size=12, padx=0)
        self.plus.configure(width=self._step_w, height=self.H)
        self.plus._cw, self.plus._ch = self._step_w, self.H
        self.plus._r = self.H / 2
        self.plus._draw()
        self.plus.pack(side="left")

        if suffix:
            tk.Label(self, text=suffix, bg=bg, fg=C_MUTED,
                     font=(FONT_FAMILY, 9)).pack(side="left", padx=(px(7), 0))

        self._var.trace_add("write", lambda *a: self._on_type())
        self.entry.bind("<Return>", lambda e: (self.commit(), "break")[1])
        self.entry.bind("<KP_Enter>", lambda e: (self.commit(), "break")[1])
        self.entry.bind("<Escape>", lambda e: (self.set_value(self.value, notify=False),
                                                self.entry.selection_clear(), "break")[2])
        self.entry.bind("<FocusIn>", lambda e: self.entry.after(10, self.entry.selection_range, 0, "end"))
        self.entry.bind("<FocusOut>", lambda e: self.commit())
        self.entry.bind("<Up>", lambda e: (self._step(1), "break")[1])
        self.entry.bind("<Down>", lambda e: (self._step(-1), "break")[1])
        self.entry.bind("<MouseWheel>", self._wheel)
        self._hi_job = None

    # ---- 取值 / 赋值 --------------------------------------------------------
    @property
    def value(self):
        try:
            return int(str(self._var.get()).strip())
        except Exception:
            return self.lo

    def get(self):
        return self.value

    def set_value(self, v, notify=True):
        v = max(self.lo, min(self.hi, int(v)))
        self._busy = True
        self._var.set(str(v))
        self._busy = False
        if notify and self._cmd:
            self._cmd(v)

    def commit(self):
        """把输入框里的内容收尾：非法 / 越界都夹到合法区间。"""
        raw = str(self._var.get()).strip()
        digits = "".join(ch for ch in raw if ch.isdigit())
        try:
            v = int(digits) if digits else self.value
        except Exception:
            v = self.value
        v = max(self.lo, min(self.hi, v))
        self._busy = True
        self._var.set(str(v))
        self._busy = False
        if self._cmd:
            self._cmd(v)
        return v

    def commit_quiet(self):
        """只把显示值收尾到合法区间，不回调（用于实时预览刷新）。"""
        raw = str(self._var.get()).strip()
        if not raw.isdigit():
            return self.value
        v = max(self.lo, min(self.hi, int(raw)))
        if raw != str(v):
            self._busy = True
            self._var.set(str(v))
            self._busy = False
        return v

    # ---- 交互 ---------------------------------------------------------------
    def _on_type(self):
        if self._busy:
            return
        raw = self._var.get()
        if len(raw) > 4:
            self._busy = True
            self._var.set(raw[:4])
            self._busy = False
            raw = raw[:4]
        if raw.isdigit() and raw != "" and int(raw) > self.hi:
            # 输入过程中就先把超界夹回来，避免出现「看起来能填 9999」
            self._busy = True
            self._var.set(str(self.hi))
            self.entry.icursor("end")
            self._busy = False
        self._notify()

    def _notify(self):
        """打字过程中也回调，让设置窗里的「一轮循环」预览跟着变。"""
        if not self._cmd:
            return
        try:
            self._cmd(self.value)
        except Exception:
            pass

    def _step(self, delta):
        self.set_value(self.value + delta)

    def _wheel(self, e):
        self._step(1 if e.delta > 0 else -1)
        return "break"
# ============================================================================
# 悬浮细条
# ----------------------------------------------------------------------------
# 这一版彻底换掉了「窗口自己平移」的旧做法。旧做法的问题是：窗口为了收起而要
# 移动 272px，于是「窗口在动」和「你在拖」两件事互相打架 —— Windows 的命中测试
# 边界既包含屏幕内也包含屏幕外的部分，鼠标在透明区域上不产生 Canvas 事件，
# 但 Enter/Leave 状态机被搅乱，反复触发展开/回收动画，就成了又飘又抖又拖不动。
#
# 新做法（也是 QQ 那种贴边的正确做法）：
#   · 窗口永远是同一个尺寸，拖动期间**位置只由鼠标决定**，任何自动动画都不参与；
#   · 「收起」时窗口整体停在屏幕内、距边缘留一条呼吸缝，露出一个**完整的细长圆角矩形**
#     （四角圆润、长条状，不做任何裁剪），里面用颜色填充比例表示剩余时间；
#   · 鼠标移到它上面 → 展开露出完整信息。
# ============================================================================
class StripWindow:
    # ---- 尺寸（设计稿像素，运行时经 px() 按 DPI 缩放）--------------------
    CARD_W, CARD_H = 236, 40      # 展开态卡片
    # 收起态细条：11 设计像素 → 150% 下 16 设备像素。
    # 这个宽度是照用户参考图标定出来的：只有 ≥16 设备像素，才能同时容下
    # 「3px 灰管壁 + 1.5px 缝隙 + 6px 液柱」这套结构；再窄缝隙就会被抗锯齿吃掉。
    EDGE_W, EDGE_H = 11, 100      # 收起态的细长圆角矩形（宽 × 高）
    EDGE_GAP = 3                  # 收起态距屏幕边缘的呼吸缝

    # ---- 收起态「试管」的管壁 / 缝隙（**按外框宽的比例**，随 EDGE_W 缩放）----
    # 比例照用户参考图标定（参考样例 14px 宽实测：管壁≈2.5px、缝隙≈1px、液柱≈4.5px）。
    # 只有"管壁"和"缝隙"两个比例需要给定，**液柱宽自动由减法推**：
    #     液柱 = EDGE_W - 2*(管壁 + 缝隙)
    # 这样既能保证"液柱四周都留缝"，也永远不会溢出管壁。
    TUBE_WALL_RATIO = 0.19        # 管壁厚 = EDGE_W * 该值（11px 设计 → 16 设备 → 3px 壁）
    TUBE_GAP_RATIO = 0.18         # 缝隙   = EDGE_W * 该值（16 设备 → 3px 缝，明显留白）
    TUBE_FRAME = "#9AA0A8"        # 灰色管壁（参考图是中性灰 127，但实际偏重，调浅一档）
    # ⚠️ PAD 必须是 0：窗口四周只要留一圈透明（key colour）边，系统投影/合成
    # 就会在那圈上压出一道近黑色的描边（实测窗口外沿 1~2px = (23,24,26)）。
    # 所以窗口尺寸直接等于内容尺寸，靠圆角+描边定轮廓。
    PAD = 0
    SNAP = 56                     # 松手时距屏幕边缘多少像素以内就吸附贴边

    def __init__(self, app):
        self.app = app
        self.root = app.root
        self._remake_metrics()

        self.top = None
        self.canvas = None
        self.visible = False
        self.collapsed = False        # 是否处于收起态
        self.side = "right"           # 贴在屏幕哪一侧："left" / "right"
        self.pos_y = None             # 展开态的 y（自由竖直位置）
        self._drag = None
        self._anim = None
        self._hover_job = None
        self._hits = {}
        self._hover = None
        self._pressed_hit = None
        self._expanded = False        # 鼠标是否停在条上
        self._cur_x = None            # 我们记为当前的窗口 x（不依赖 winfo_x 的滞后）
        self._cur_y = None
        self._rendering_edge = None   # 当前画的是窄条还是卡片

    def _remake_metrics(self):
        self.CARD_W = px(self.__class__.CARD_W)
        self.CARD_H = px(self.__class__.CARD_H)
        self.EDGE_W = px(self.__class__.EDGE_W)
        self.EDGE_H = px(self.__class__.EDGE_H)
        self.EDGE_GAP = px(self.__class__.EDGE_GAP)
        self.PAD = px(self.__class__.PAD)
        self.SNAP = px(self.__class__.SNAP)
        # 窗口尺寸就是内容尺寸（PAD=0）。WIN_W/WIN_H 一律指**展开态卡片**，
        # 任何地方要"当前窗口多大"都请用 _cur_w/_cur_h 或 _geom_for()。
        self.WIN_W = self.CARD_W + self.PAD * 2
        self.WIN_H = self.CARD_H + self.PAD * 2

    # ---- 生命周期 -----------------------------------------------------------
    def _alive(self):
        """窗口对象存在且底层 Tk 窗口还活着。

        ⚠️ 必须同时判 `winfo_exists()`，不能只看 `self.top is not None`：
        底层窗口一旦被销毁，`self.top` 仍是非 None 的**死对象**，此时任何
        `withdraw()` 都会抛 `TclError: bad window path name`。
        这正是用户反馈「点了跳过之后一直卡住、怎么点都没法继续计时」的根因：
        `tick()` 里 `self.strip.refresh()` 抛了这个异常，导致函数后面的
        `self.root.after(200, self.tick)` **永远不会执行** —— 整个计时循环
        就此永久停摆，界面上再怎么点也不会更新。
        """
        if self.top is None or self.canvas is None:
            return False
        try:
            return bool(self.top.winfo_exists()) and bool(self.canvas.winfo_exists())
        except Exception:
            return False

    def _drop_refs(self):
        """把一个已经死掉的窗口的引用清干净，让下次 show() 能重建。"""
        self.top = None
        self.canvas = None
        self._hits = {}
        self._btns = {}
        self._render_key = None
        self._rendering_edge = None
        self._cur_w = self._cur_h = None

    def show(self):
        # 引用在、但底层窗口已经死了 → 先清掉引用，走重建分支。
        if self.top is not None and not self._alive():
            log.warning("悬浮条窗口已失效，自动重建")
            self._drop_refs()
        if self.top is None:
            self._build()
        self.visible = True
        try:
            self.top.deiconify()
            self.top.lift()
        except Exception:
            # 极端情况下重建后仍然失败：别再抛出去，避免连累 tick 循环。
            log.exception("悬浮条显示失败，尝试重建")
            self._drop_refs()
            try:
                self._build()
                self.top.deiconify()
                self.top.lift()
            except Exception:
                log.exception("悬浮条重建后仍无法显示")
                self.visible = False
                return
        cfg = self.app.cfg
        self.side = cfg.get("_strip_side", "right")
        if self.side not in ("left", "right"):
            self.side = "right"
        self.pos_y = int(cfg.get("_strip_y", px(320)))
        self._apply_geometry()
        self._render()

    def hide(self):
        if self._alive():
            try:
                self.top.withdraw()
            except Exception:
                log.debug("悬浮条隐藏失败（窗口可能已销毁）", exc_info=True)
                self._drop_refs()
        self.visible = False

    def toggle(self):
        self.hide() if self.visible else self.show()

    def destroy(self):
        self._cancel_anim()
        if self._hover_job is not None:
            try:
                self.root.after_cancel(self._hover_job)
            except Exception:
                pass
            self._hover_job = None
        if self.top is not None:
            try:
                self.top.destroy()
            except Exception:
                pass
            self.top = None
        self.visible = False

    def _build(self):
        top = tk.Toplevel(self.root)
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        top.configure(bg=TRANSPARENT_KEY)
        try:
            top.attributes("-transparentcolor", TRANSPARENT_KEY)
        except Exception:
            pass
        self.top = top
        # 注意：画布背景必须与窗口同色键，绝不能是白色 ——
        # 否则透明窗口里会出现一个刺眼的白方块（用户反馈的「多一个正方形白框」）。
        # 初始尺寸按当前状态取（收起时是细条、展开时是卡片）。
        _w0, _h0 = self._geom_for()
        c = tk.Canvas(top, width=_w0, height=_h0, bg=TRANSPARENT_KEY,
                      highlightthickness=0, bd=0, takefocus=0)
        c.pack()
        self.canvas = c
        self._cur_w, self._cur_h = _w0, _h0
        try:
            top.update_idletasks()
            configure_floating_window(top)
        except Exception:
            pass
        c.bind("<Enter>", self._on_enter)
        c.bind("<Leave>", self._on_leave)
        c.bind("<Motion>", self._on_hover_move)
        c.bind("<Button-1>", self._on_press)
        c.bind("<B1-Motion>", self._on_drag)
        c.bind("<ButtonRelease-1>", self._on_release)
        self._render()

    # ---- 屏幕与几何 ---------------------------------------------------------
    def _screen(self):
        return self.root.winfo_screenwidth(), self.root.winfo_screenheight()

    def _win_xy(self):
        """返回**我们记为当前**的窗口位置。

        注意：不能直接用 winfo_x()/winfo_y() —— 刚调用 geometry() 之后 Tk 还没处理
        移动消息，winfo_x() 返回的仍是旧位置，会让我们误判「露在屏幕内的是卡片还是
        窄条」，于是收起后画面上残留一张卡片。
        """
        if getattr(self, "_cur_x", None) is not None:
            return self._cur_x, self._cur_y
        try:
            return self.top.winfo_x(), self.top.winfo_y()
        except Exception:
            return 0, 0

    def _geom_for(self):
        """当前该用的窗口尺寸 (w, h)。

        窗口**永远等于内容尺寸**：
          · 收起态 → 圆角矩形（EDGE_W × EDGE_H）
          · 展开态 → 卡片（CARD_W × CARD_H）
        绝不能返回 `max(CARD_H, EDGE_H)` 那种"取大值"的尺寸：展开态卡片只有 40
        设计像素高，而收起条有 100 高，取大值会让展开窗比卡片高出一大截，
        卡片下方就空出一片没有任何内容的窗口区域 —— 那片空白会被系统合成为
        一圈难看的边（就是用户说的"外面有边框"）。
        """
        if self.collapsed and self._drag is None:
            return self.EDGE_W + self.PAD * 2, self.EDGE_H + self.PAD * 2
        return self.CARD_W + self.PAD * 2, self.CARD_H + self.PAD * 2

    def _place(self, x, y, w=None, h=None):
        """统一入口：设置窗口尺寸 + 位置，并同步画布尺寸与记录值。"""
        if w is None or h is None:
            w, h = self._geom_for()
        w, h = int(w), int(h)
        if (w, h) != (getattr(self, "_cur_w", None), getattr(self, "_cur_h", None)):
            try:
                self.canvas.configure(width=w, height=h)
            except Exception:
                pass
            self._cur_w, self._cur_h = w, h
        try:
            self.top.geometry("%dx%d+%d+%d" % (w, h, int(round(x)), int(y)))
        except Exception:
            pass
        self._cur_x, self._cur_y = int(round(x)), int(y)

    def _apply_geometry(self):
        """按当前 side / pos_y / collapsed 计算并设置窗口位置**与尺寸**。

        关键：窗口要**贴着实际内容缩放**，不能一直保持卡片尺寸。
        否则收起时窗口仍是整卡尺寸，而可见的只有一小条 —— 四周那一大片空白
        会被系统合成为一圈难看的边（用户截图里的「一个大白框/黑框包着一根细棍」）。
        """
        self._cancel_anim()      # 直接定位时必须先掐掉在飞的动画，否则会被动画覆盖
        sw, sh = self._screen()
        w, h = self._geom_for()
        if self.collapsed:
            y = self._clamp_y(self._collapsed_y(), self.EDGE_H, sh)
            x = (sw - w - self.EDGE_GAP) if self.side == "right" else self.EDGE_GAP
        else:
            y = self._clamp_y(self.pos_y, self.CARD_H, sh)
            x = (sw - w - px(10)) if self.side == "right" else px(10)
        self._place(x, y, w, h)
        self._render_key = None
        self._render()

    def _collapsed_y(self):
        """收起态的竖直位置。

        收起后窗口只有「圆角矩形 + PAD」那么大，而展开态是卡片大小，
        要让**圆角矩形的中心**与**卡片的中心**对齐，视觉上才是"原地收起"。
        """
        sh = self._screen()[1]
        if self.pos_y is None:
            return int(sh / 2 - (self.EDGE_H + self.PAD * 2) / 2)
        card_cy = self.pos_y + self.CARD_H / 2          # 展开态卡片中心
        return int(card_cy - (self.EDGE_H + self.PAD * 2) / 2)

    def _clamp_y(self, y, content_h, sh):
        """把窗口 y 夹在屏幕内。content_h 传的是**内容**高度（不含 PAD）。"""
        win_h = content_h + self.PAD * 2
        if y is None:
            y = int(sh / 2 - win_h / 2)
        return int(min(max(y, px(8)), max(px(8), sh - win_h - px(8))))

    def _offset(self):
        """鼠标相对窗口左上角的偏移（用于拖动时保持抓取点不变）。"""
        return (0, 0)

    # ---- 动画 ---------------------------------------------------------------
    def _cancel_anim(self):
        if self._anim is not None:
            try:
                self.root.after_cancel(self._anim)
            except Exception:
                pass
            self._anim = None

    def _animate_x(self, target_x, steps=8, interval=9):
        """只在 x 上做动画（收起/展开的进出效果）。

        收起/展开都是「吸附」动作，必须干脆，所以步数少、间隔短（~70ms 走完），
        否则用户会觉得条在自己慢慢滑走。
        """
        self._cancel_anim()
        start_x, y = self._win_xy()
        delta = target_x - start_x
        if abs(delta) < 1:
            self._set_x(target_x, y)
            return

        def step(i):
            t = i / steps
            e = 1 - (1 - t) ** 3
            self._set_x(start_x + delta * e, y)
            if i < steps:
                self._anim = self.root.after(interval, lambda: step(i + 1))
            else:
                self._anim = None

        step(0)

    def _set_x(self, x, y):
        # 动画过程中窗口尺寸也要跟着状态走（收起↔展开尺寸不同）
        self._place(x, y)
        # 现在收起态窗口是**完整停在屏幕内**的，不能再靠"露出来的宽度"来判断该画
        # 收起态还是卡片 —— 改用 collapsed 这个状态位本身，语义清楚也不会误判。
        want_edge = bool(self.collapsed) and self._drag is None
        if want_edge != getattr(self, "_rendering_edge", None):
            self._render()

    def _collapse_x(self):
        sw, _ = self._screen()
        w = self.EDGE_W + self.PAD * 2
        return (sw - w - self.EDGE_GAP) if self.side == "right" else self.EDGE_GAP

    def _expand_x(self):
        sw, _ = self._screen()
        return (sw - self.WIN_W - px(10)) if self.side == "right" else px(10)

    def expand(self):
        """从收起态展开：先清掉收起标记，再把窗口拉回屏幕内。

        注意顺序：先把尺寸切回卡片尺寸（_set_x 内部会做），再渲染，
        否则会拿卡片内容去画进"条尺寸"的画布里，出现裁切错位。
        """
        if not self.visible:
            return
        self.collapsed = False
        self._expanded = True
        # 立刻把窗口尺寸恢复成卡片大小，再动画位移
        self._place(self._cur_x, self._cur_y, self.WIN_W, self.WIN_H)
        self._render()
        self._animate_x(self._expand_x())

    def collapse(self):
        if not self.visible or self._drag is not None:
            return
        self.collapsed = True
        self._expanded = False
        self._save_pos()
        self._animate_x(self._collapse_x())

    # ---- 交互 ---------------------------------------------------------------
    def _schedule_collapse(self, delay=1600):
        if self._hover_job is not None:
            try:
                self.root.after_cancel(self._hover_job)
            except Exception:
                pass
        self._hover_job = self.root.after(delay, self._do_collapse)

    def _do_collapse(self):
        self._hover_job = None
        if self._drag is None:
            self.collapse()

    def _on_enter(self, _e):
        if self._hover_job is not None:
            try:
                self.root.after_cancel(self._hover_job)
            except Exception:
                pass
            self._hover_job = None
        if self.collapsed and self._drag is None:
            self.expand()

    def _on_leave(self, _e):
        self._hover = None
        self._render()
        if self._drag is not None:
            return
        self._schedule_collapse()

    def _on_hover_move(self, e):
        if self._drag is not None:
            return
        z = self._hit_test(e.x, e.y)
        if z != self._hover:
            self._hover = z
            self.canvas.configure(cursor="hand2" if z else "fleur")
            self._render()

    # ---- 拖动：实时跟随，不参与任何自动动画 ---------------------------------
    def _on_press(self, e):
        self._cancel_anim()
        if self._hover_job is not None:
            try:
                self.root.after_cancel(self._hover_job)
            except Exception:
                pass
            self._hover_job = None
        cx, cy = cursor_pos()
        wx, wy = self._win_xy()
        self._drag = {
            "cx": cx, "cy": cy,           # 按下瞬间的真实鼠标坐标
            "wx": wx, "wy": wy,           # 按下瞬间的窗口坐标
            "moved": False,
            "ox": e.x, "oy": e.y,         # 用于判断是否算「点击」
            "grab_dx": cx - wx,           # 抓取点相对窗口的偏移
            "grab_dy": cy - wy,
            # 如果是在收起态下按下的，先记住要展开到的目标 x
            "was_collapsed": self.collapsed,
        }
        self._pressed_hit = self._hit_test(e.x, e.y)
        # 一按下就立刻展开，避免「在窄条上拖」时窗口还被推到屏幕外
        if self.collapsed:
            self.collapsed = False
            self._expanded = True
            # 把窗口从边缘拉回来，并保持鼠标下的抓取点不动
            ex = self._expand_x()
            self._set_x(ex, wy)

    def _on_drag(self, _e):
        d = self._drag
        if d is None:
            return
        cx, cy = cursor_pos()
        dx = cx - d["cx"]
        dy = cy - d["cy"]
        if not d["moved"] and (abs(dx) > px(3) or abs(dy) > px(3)):
            d["moved"] = True
        if not d["moved"]:
            return
        sw, sh = self._screen()
        # 窗口位置 = 窗口原位置 + 鼠标位移（纯加法，绝无动画参与）
        nx = d["wx"] + dx
        ny = d["wy"] + dy
        nx = min(max(nx, -(self.WIN_W - px(60))), sw - px(60))
        ny = min(max(ny, 0), sh - self.WIN_H)
        self.collapsed = False
        self._expanded = True
        self._set_x(nx, ny)
        # 实时记住位置，松手时不需要再算
        self._live_x, self._live_y = nx, ny

    def _on_release(self, e):
        d = self._drag
        self._drag = None
        hit = self._pressed_hit
        self._pressed_hit = None
        if d is None:
            return

        if d["moved"]:
            x, y = getattr(self, "_live_x", None), getattr(self, "_live_y", None)
            if x is None:
                x, y = self._win_xy()
            sw, sh = self._screen()
            # 吸附判定：靠近哪边就贴哪边
            dist_left = x
            dist_right = sw - (x + self.WIN_W)
            if min(dist_left, dist_right) <= self.SNAP:
                if dist_left < dist_right:
                    self.side = "left"
                    self.pos_y = y
                else:
                    self.side = "right"
                    self.pos_y = y
                self._save_pos()
                self.collapsed = True
                self._expanded = False
                self.collapse()
                return
            # 没到边缘：就地停住，保持展开，过一会儿再收起
            self.pos_y = y
            self._save_pos()
            self._schedule_collapse(2200)
            return

        # 没移动 = 点击
        if hit:
            self._run_hit(hit)

    def _hit_test(self, x, y):
        for name, (x1, y1, x2, y2) in self._hits.items():
            if x1 <= x <= x2 and y1 <= y <= y2:
                return name
        return None

    def _run_hit(self, name):
        if name in ("menu", "body"):
            self.app.popup_menu_at_cursor()
        elif name == "close":
            self.hide()
        elif name == "toggle":
            self.app.toggle_pause()

    # ---- 绘制 ---------------------------------------------------------------
    def _state(self):
        app = self.app
        if app.alerting:
            return C_ALERT, "提醒"
        return (C_SIT, "坐") if app.mode == "sit" else (C_STAND, "站")

    def _progress(self):
        total = self.app._duration(self.app.mode)
        if total <= 0:
            return 0.0
        return min(1.0, max(0.0, 1.0 - self.app.remaining / total))

    def _render(self):
        c = self.canvas
        if c is None:
            return
        c.delete("all")
        self._hits = {}
        # 收起态窗口是完整停在屏幕内的，所以直接看状态位，不用去量"露出的宽度"。
        if self.collapsed and self._drag is None:
            self._rendering_edge = True
            self._render_edge()
        else:
            self._rendering_edge = False
            self._render_card()
        if self.collapsed and self._drag is None:
            # 收起态的"身体"就是那根细条本身，不能写成整卡尺寸 ——
            # 否则整窗都算可拖动区域，鼠标划过旁边空处也会触发。
            self._hits["body"] = (0, 0, self.EDGE_W, self.EDGE_H)

    def _render_edge(self):
        """收起态：一条「试管」——**粗灰管壁 + 透明管腔 + 内部细液柱**。

        结构（照用户参考图标定，见 `make_tube_image` 的文档）：
          · 管壁：一圈**明显的中灰粗描边**（不是浅灰/白），管腔内部**全透明**；
          · 液柱：一根**明显细于管腔**的小胶囊，两端圆角，从管底往上长；
          · 液柱四周（含左右两侧）都留一条**等宽缝隙**，这才是"试管"的观感；
          · 颜色由 `liquid_color()` 按进度三段式取（状态色 → 黄 → 珊瑚红）。

        ⚠️ 管壁 + 液柱必须画在**同一张贴图**里（`make_tube_image`），
        不能拆成"外壳图 + 液柱图"两张再拼：拆开之后液柱的位置只能靠猜管腔边界，
        8px 宽下猜不准就一定会溢出管壁（这就是上一版"液体溢出+变矩形"的原因）。

        收起态窗口尺寸 == 圆角矩形本身（PAD=0），所以这里直接铺满整窗。
        """
        c = self.canvas
        x0 = self.PAD
        y0 = self.PAD

        ew, eh = self.EDGE_W, self.EDGE_H
        frac = self._progress()
        base, _label = self._state()
        color = liquid_color(frac, base)

        # 缓存键要把"所有影响画面的量"都带上，否则改了比例/进度不会重画。
        # 液位只取整到 1/256，避免每 tick 都因为浮点抖动而爆缓存。
        key = ("tube", int(ew), int(eh), int(round(frac * 256)), color,
               round(self.TUBE_WALL_RATIO, 4), round(self.TUBE_GAP_RATIO, 4))
        cache = self._aa_cache
        img = cache.get(key)
        if img is None:
            img = ImageTk.PhotoImage(
                make_tube_image(ew, eh, frac, color,
                                wall_ratio=self.TUBE_WALL_RATIO,
                                gap_ratio=self.TUBE_GAP_RATIO,
                                frame_hex=self.TUBE_FRAME))
            if len(cache) > 160:
                cache.clear()
            cache[key] = img
        c.create_image(x0, y0, image=img, anchor="nw")

    def _render_card(self):
        c = self.canvas
        P = self.PAD
        W, H = self.CARD_W, self.CARD_H
        x0, y0, x1, y1 = P, P, P + W, P + H
        cy = y0 + H / 2
        color, label = self._state()
        left_side = (self.side == "left")

        # 没有任何投影（见 configure_floating_window 的说明：自绘会发黑/发白，
        # 系统 CS_DROPSHADOW 会在透明留白上压出黑描边）。靠圆角+1px 描边定轮廓。
        key = ("card", int(W), int(H))
        cache = self._aa_cache
        card = cache.get(key)
        if card is None:
            card = ImageTk.PhotoImage(
                aa_rounded_image(W, H, H / 2, C_BG, C_BORDER, 1, 4,
                                 opaque_bg=C_BG))
            cache[key] = card
        c.create_image(x0, y0, image=card, anchor="nw")

        pad = px(15)
        bstep = px(31)
        reading = "到点" if self.app.alerting else fmt_mmss(self.app.remaining)

        # 卡片是胶囊形（圆角半径 = H/2），所以两端各有一段弧形区域放不下东西。
        # 内容一律按 cap 内缩，避免状态点压到圆角边框上（会被圆角切出"蓝线"）。
        cap = H / 2 + px(5)

        if left_side:
            # 贴左：状态点在左，按钮在右
            dot_cx = x0 + cap
            text_x, anchor = dot_cx + px(14), "w"
            bar_l = x0 + px(112)
            bar_r = x1 - pad - px(100)
            btn_start, dirn = x1 - cap - px(2), -1
        else:
            # 贴右：按钮在左，状态点在右（收起时露出的正是"状态+时间"这一侧）
            dot_cx = x1 - cap
            text_x, anchor = dot_cx - px(14), "e"
            bar_l = x0 + pad + px(100)
            bar_r = x1 - px(112)
            btn_start, dirn = x0 + cap + px(2), 1

        # 状态点
        dr = px(3.5)
        c.create_oval(dot_cx - dr, cy - dr, dot_cx + dr, cy + dr, fill=color, outline="")
        c.create_text(text_x, cy + px(0.5), text="%s %s" % (label, reading), fill=C_TEXT,
                      font=(FONT_FAMILY, 11, "bold"), anchor=anchor)

        # 进度条（细一点：4 设计像素，跟读数/按钮在视觉上更协调）
        if bar_r - bar_l > px(20):
            frac = self._progress()
            bw, bh = int(bar_r - bar_l), px(4)
            pkey = ("bar", bw, bh, int(round(frac * 200)), color)
            ph = cache.get(pkey)
            if ph is None:
                ph = ImageTk.PhotoImage(make_progress_bar(bw, bh, frac, color))
                if len(cache) > 160:
                    cache.clear()
                cache[pkey] = ph
            c.create_image(bar_l, cy - bh / 2, image=ph, anchor="nw")

        # 三个小按钮
        playing = self.app.running and not self.app.paused
        rr, arm, dot2 = px(11), px(4), px(4)
        for i, (name, glyph) in enumerate(
                (("toggle", "pause" if playing else "play"),
                 ("menu", "dots"), ("close", "close"))):
            bx = btn_start + dirn * bstep * i
            self._hits[name] = (bx - px(12), cy - px(12), bx + px(12), cy + px(12))
            hv = (self._hover == name)
            c.create_oval(bx - rr, cy - rr, bx + rr, cy + rr,
                          fill=C_SURFACE_A if hv else C_SURFACE,
                          outline=C_BORDER, width=1)
            col = C_TEXT if hv else C_TEXT2
            if glyph == "pause":
                c.create_line(bx - px(3), cy - arm, bx - px(3), cy + arm, fill=col, width=2)
                c.create_line(bx + px(1), cy - arm, bx + px(1), cy + arm, fill=col, width=2)
            elif glyph == "play":
                c.create_polygon(bx - px(3), cy - px(5), bx - px(3), cy + px(5),
                                 bx + px(4), cy, fill=col, outline="")
            elif glyph == "dots":
                for dx in (-dot2, 0, dot2):
                    c.create_oval(bx + dx - px(1.6), cy - px(1.6),
                                  bx + dx + px(1.6), cy + px(1.6), fill=col, outline="")
            else:
                c.create_line(bx - arm, cy - arm, bx + arm, cy + arm, fill=col, width=2)
                c.create_line(bx - arm, cy + arm, bx + arm, cy - arm, fill=col, width=2)

        # 可拖动区域（排除按钮）
        btn_span = bstep * 2 + px(16)
        if left_side:
            self._hits["body"] = (x0 + px(2), y0 + px(4),
                                  x1 - pad - btn_span, y1 - px(4))
        else:
            self._hits["body"] = (x0 + pad + btn_span, y0 + px(4),
                                  x1 - px(2), y1 - px(4))

    @property
    def _aa_cache(self):
        cache = getattr(self, "_cache_store", None)
        if cache is None:
            cache = self._cache_store = {}
        return cache

    def refresh(self):
        """每 200ms 由 tick() 调用。

        ⚠️ 这里**绝对不能向外抛异常**：tick() 里
        `self._update_icon(); self.strip.refresh(); self.root.after(200, self.tick)`
        是顺序执行的，refresh() 一旦抛出去，最后那句 after() 就永远执行不到，
        整个计时循环当场死掉（用户反馈的「卡住、怎么点都没法继续计时」）。
        所以这里做三层防护：窗口活着 → canvas 活着 → 渲染本身包 try。
        """
        if not (self.visible and self._alive()):
            return
        try:
            app = self.app
            key = (app.mode, app.running, app.paused, app.alerting,
                   int(app.remaining), self.side, self._hover, self.collapsed)
            if key == getattr(self, "_render_key", None):
                return
            self._render_key = key
            self._render()
        except tk.TclError:
            # 窗口刚刚被销毁（正在销毁的窗口在 Tk 里是"半死"状态）
            log.debug("刷新悬浮条时窗口已失效", exc_info=True)
            self._drop_refs()
            self.visible = False
        except Exception:
            log.exception("刷新悬浮条失败")

    def _save_pos(self):
        self.app.cfg["_strip_side"] = self.side
        if self.pos_y is not None:
            self.app.cfg["_strip_y"] = int(self.pos_y)
        self.app.cfg.pop("_strip_x", None)
        self.app.debounce_save()



class AlertWindow:
    """右下角到点提醒窗。

    设计要点（用户反馈「太大了、太丑」后重做）：
      · 尺寸从 412×206 缩到 356×150，接近一条「通知」而不是一个对话框
      · 去掉那个占地方的大圆形箭头图标，改成标题前一枚小色点 + 细的顶部色条
      · 按钮高度 38 → 34，宽度收紧，三个按钮排成一行仍然宽松
    """
    def __init__(self, app):
        self.app = app
        self.root = app.root
        self.W, self.H = px(356), px(150)
        self.PAD = px(10)
        self.top = None
        self.canvas = None
        self._drag = None
        self._btns = {}
        self._close_hit = None
        self._hover_close = False
        self._cur_args = None

    def _alive(self):
        """同 StripWindow._alive：引用在 ≠ 窗口还活着。"""
        if self.top is None or self.canvas is None:
            return False
        try:
            return bool(self.top.winfo_exists()) and bool(self.canvas.winfo_exists())
        except Exception:
            return False

    def _drop_refs(self):
        self.top = None
        self.canvas = None
        self._btns = {}
        self._cur_args = None
        self._close_hit = None

    def _build(self):
        top = tk.Toplevel(self.root)
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        top.configure(bg=TRANSPARENT_KEY)
        try:
            top.attributes("-transparentcolor", TRANSPARENT_KEY)
        except Exception:
            pass
        self.top = top
        self.canvas = tk.Canvas(top, width=self.W, height=self.H,
                                bg=TRANSPARENT_KEY, highlightthickness=0, bd=0)
        self.canvas.pack()
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Motion>", self._on_hover)
        self.canvas.bind("<Leave>", lambda e: self._set_close_hover(False))
        try:
            top.update_idletasks()
            configure_floating_window(top)
        except Exception:
            pass

    def _place(self):
        """默认落在右下角；**如果用户拖过，就回到用户拖到的位置**。

        ⚠️ `geometry()` 收的是**物理像素**，而 `winfo_screenwidth()` 是**逻辑像素**。
        在 150% 缩放下两者差 1.5 倍 —— 早期版本直接
        `geometry("%dx%d+%d+%d" % (self.W, self.H, sw - self.W - px(16), sh - ...))`
        就是逻辑/物理混用，窗口会跑到离右下角很远的地方。所以这里统一用
        `root.winfo_screen*` × SCALE 换算成物理像素，并用 px() 做边距。
        """
        try:
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
        except Exception:
            sw, sh = 1920, 1080
        sw, sh = int(sw * SCALE), int(sh * SCALE)

        cfg = self.app.cfg
        ox, oy = cfg.get("_alert_x"), cfg.get("_alert_y")
        if ox is None or oy is None:
            ox = sw - self.W - px(16)
            oy = sh - self.H - px(56)
        # 防止改分辨率/换屏后落在屏幕外
        ox = min(max(int(ox), 0), max(0, sw - self.W))
        oy = min(max(int(oy), 0), max(0, sh - self.H))
        try:
            self.top.geometry("%dx%d+%d+%d" % (self.W, self.H, ox, oy))
        except Exception:
            pass

    def show(self, title, subtitle, primary_text, accent, glyph):
        if self.top is not None and not self._alive():
            log.warning("提醒窗已失效，自动重建")
            self._drop_refs()
        if self.top is None:
            self._build()
        self._cur_args = (title, subtitle, primary_text, accent, glyph)
        self._render(*self._cur_args)
        try:
            self.top.deiconify()
            self._place()
            self.top.lift()
            self.top.attributes("-topmost", True)
        except Exception:
            log.exception("提醒窗显示失败")

    def hide(self):
        if self._alive():
            try:
                self.top.withdraw()
            except Exception:
                log.debug("提醒窗隐藏失败（窗口可能已销毁）", exc_info=True)
                self._drop_refs()

    def destroy(self):
        if self.top is not None:
            try:
                self.top.destroy()
            except Exception:
                pass
            self._drop_refs()

    def _render(self, title, subtitle, primary_text, accent, glyph):
        c = self.canvas
        c.delete("all")
        for w in self._btns.values():
            try:
                w.destroy()
            except Exception:
                pass
        self._btns = {}
        P, W, H = self.PAD, self.W, self.H
        x0, y0, x1, y1 = P, P, W - P, H - P

        # 没有任何投影：见 configure_floating_window（自绘会发白/发黑，
        # 系统 CS_DROPSHADOW 会在透明留白上压出黑描边）。
        iw, ih = int(x1 - x0), int(y1 - y0)
        key = ("alert2", iw, ih)
        cache = getattr(self, "_aa_cache", None)
        if cache is None:
            cache = self._aa_cache = {}
        card = cache.get(key)
        if card is None:
            card = ImageTk.PhotoImage(aa_rounded_image(iw, ih, px(16), C_BG,
                                                       C_BORDER, 1, 4,
                                                       opaque_bg=C_BG))
            cache[key] = card
        c.create_image(x0, y0, image=card, anchor="nw")

        # 顶部那条细色带已删除 —— 站姿时它是绿色，会读成"窗口上多了一根绿线"。
        # 标题前的小色点已经足够表达状态，不需要再来一条线。

        # 标题行：小色点 + 标题
        dot_cx, dot_cy = x0 + px(24), y0 + px(32)
        c.create_oval(dot_cx - px(4.5), dot_cy - px(4.5),
                      dot_cx + px(4.5), dot_cy + px(4.5), fill=accent, outline="")
        c.create_text(dot_cx + px(14), dot_cy, text=title, fill=C_TEXT,
                      font=(FONT_FAMILY, 13, "bold"), anchor="w")

        # 副标题
        c.create_text(x0 + px(24), y0 + px(54), text=subtitle, fill=C_TEXT2,
                      font=(FONT_FAMILY, 9), anchor="nw", width=iw - px(56))

        # 关闭
        cxc, cyc = x1 - px(21), y0 + px(21)
        self._close_hit = (cxc - px(13), cyc - px(13), cxc + px(13), cyc + px(13))
        if self._hover_close:
            c.create_oval(cxc - px(11), cyc - px(11), cxc + px(11), cyc + px(11),
                          fill=C_SURFACE, outline="")
        cc = C_TEXT2 if self._hover_close else C_MUTED
        c.create_line(cxc - px(4.5), cyc - px(4.5), cxc + px(4.5), cyc + px(4.5), fill=cc, width=2)
        c.create_line(cxc - px(4.5), cyc + px(4.5), cxc + px(4.5), cyc - px(4.5), fill=cc, width=2)

        # 三个按钮
        by = y1 - px(12) - px(34)
        b1 = RoundButton(c, primary_text, command=self.app.alert_confirm,
                         kind="primary", accent=accent, height=34, min_width=112, bg=C_BG)
        b2 = RoundButton(c, "稍后 %d 分钟" % self.app.cfg["snooze_minutes"],
                         command=self.app.alert_snooze, kind="ghost", height=34,
                         min_width=104, bg=C_BG)
        b3 = RoundButton(c, "跳过", command=self.app.alert_skip,
                         kind="ghost", height=34, min_width=70, bg=C_BG)
        bx = x0 + px(20)
        c.create_window(bx, by, window=b1, anchor="nw")
        c.create_window(bx + b1._cw + px(8), by, window=b2, anchor="nw")
        c.create_window(bx + b1._cw + b2._cw + px(16), by, window=b3, anchor="nw")
        self._btns = {"p": b1, "s": b2, "k": b3}

    def _set_close_hover(self, v):
        if self._hover_close != v:
            self._hover_close = v
            self.canvas.configure(cursor="hand2" if v else "arrow")
            if self._cur_args:
                self._render(*self._cur_args)

    def _on_hover(self, e):
        v = bool(self._close_hit and self._close_hit[0] <= e.x <= self._close_hit[2]
                 and self._close_hit[1] <= e.y <= self._close_hit[3])
        self._set_close_hover(v)

    def _on_press(self, e):
        """按下：记住「真实鼠标坐标」和「窗口坐标」。

        ⚠️ 这里必须用 `cursor_pos()`（物理像素），**绝不能**用 Canvas 事件的 e.x/e.y。
        原因（跟窄条当初那个 bug 是同一个）：
          · `e.x/e.y` 是**逻辑像素**（要乘 DPI 缩放才是屏幕上的实际位置），
            而 `winfo_x()/winfo_y()` 同样是逻辑像素、`geometry("+x+y")` 收的却是**物理像素**；
          · 本机 DPI 缩放 150% 时，鼠标在屏幕上真的走了 150 物理像素，`e.x` 只增加 100，
            于是窗口只走 100 物理像素 —— 手感就是「鼠标走 10cm、窗口只走一小截」。
          · 更糟的是窗口自己在动，`e.x` 的参照系（控件左上角）跟着动，
            窗口位移和鼠标位移互相污染，拖动会发飘、抖动、甚至原地不动。
        用物理坐标做纯加法 `新位置 = 旧位置 + 鼠标位移` 就没有任何换算误差。
        """
        cx, cy = cursor_pos()
        self._drag = {
            "cx": cx, "cy": cy,           # 按下瞬间的真实鼠标坐标（物理像素）
            "wx": self.top.winfo_x(),     # 按下瞬间的窗口坐标
            "wy": self.top.winfo_y(),
            "ox": e.x, "oy": e.y,         # 仅用于判断「是拖动还是点击」
            "moved": False,
        }

    def _on_motion(self, e):
        d = self._drag
        if d is None:
            return
        cx, cy = cursor_pos()
        dx = cx - d["cx"]
        dy = cy - d["cy"]
        if not d["moved"] and (abs(dx) > 3 or abs(dy) > 3):
            d["moved"] = True
        if not d["moved"]:
            return
        # 窗口位置 = 窗口原位置 + 鼠标位移（纯加法，无动画、无 DPI 换算）
        nx = d["wx"] + dx
        ny = d["wy"] + dy
        # 至少留 60px 在屏幕内，别把窗口拖到完全找不回来
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        nx = min(max(nx, -(self.W - px(60))), sw - px(60))
        ny = min(max(ny, 0), sh - px(30))
        try:
            self.top.geometry("+%d+%d" % (nx, ny))
        except Exception:
            pass

    def _on_release(self, e):
        d = self._drag
        self._drag = None
        moved = bool(d and d["moved"])
        if moved:
            # 拖动结束：记住位置，下次弹出还在老地方，不用回来重新拖
            try:
                cfg = self.app.cfg
                cfg["_alert_x"] = self.top.winfo_x()
                cfg["_alert_y"] = self.top.winfo_y()
                save_settings(cfg)
            except Exception:
                log.debug("记住提醒窗位置失败", exc_info=True)
            return
        if (self._close_hit and self._close_hit[0] <= e.x <= self._close_hit[2]
                and self._close_hit[1] <= e.y <= self._close_hit[3]):
            # 右上角 × = 「关掉这个提醒窗」，等价于「稍后」——
            # 这里必须走 app.alert_snooze() 而不是只把窗口藏起来：
            # 只藏窗口的话 app.alerting 仍是 True，计时会一直停在提醒态。
            self.app.alert_snooze()


# ============================================================================
# 设置窗
# ============================================================================
class SettingsWindow:
    def __init__(self, app):
        self.app = app
        self.root = app.root
        self.win = None
        self._dur_fields = {}
        self._switches = {}
        self._preview = None
        self._hint = None

    def is_open(self):
        return self.win is not None and self.win.winfo_exists()

    def close(self):
        if self.win is not None:
            try:
                self.win.destroy()
            except Exception:
                pass
            self.win = None
            self._dur_fields = {}
            self._switches = {}
            self._preview = None
            self._hint = None

    # ---- 实时预览 ----------------------------------------------------------
    def _refresh_preview(self):
        if self._preview is None:
            return
        try:
            if not self._preview.winfo_exists():
                return
        except Exception:
            return
        try:
            sit = int(self._dur_fields["sit_minutes"].value)
            stand = int(self._dur_fields["stand_minutes"].value)
            gap = int(self._dur_fields["alert_minutes"].value)
        except Exception:
            return
        total = sit + stand
        self._preview.config(text="一轮循环：坐 %d 分钟 → 站 %d 分钟，共 %d 分钟"
                                  % (sit, stand, total))
        if self._hint is not None:
            if total > 1:
                self._hint.config(text="按 8 小时计，一天大约起身 %d 次。"
                                       % max(1, int(8 * 60 / total)))
            else:
                self._hint.config(text="")
        for nf in self._dur_fields.values():
            nf.commit_quiet()

    def open(self):
        if self.is_open():
            self.win.lift()
            return
        cfg = self.app.cfg
        w = tk.Toplevel(self.root)
        self.win = w
        w.title("%s · 设置" % APP_NAME)
        w.configure(bg=C_PAGE)
        w.resizable(False, False)
        try:
            w.attributes("-topmost", True)
        except Exception:
            pass

        outer = tk.Frame(w, bg=C_PAGE)
        outer.pack(fill="both", expand=True, padx=px(20), pady=px(16))

        head = tk.Frame(outer, bg=C_PAGE)
        head.pack(fill="x")
        tk.Label(head, text="设置", bg=C_PAGE, fg=C_TEXT,
                 font=(FONT_FAMILY, 17, "bold")).pack(anchor="w")
        tk.Label(head, text="时长可直接输入数字，也可点 − / + 微调；保存后立即生效。",
                 bg=C_PAGE, fg=C_TEXT2, font=(FONT_FAMILY, 9)).pack(anchor="w",
                                                                    pady=(px(2), px(10)))

        # ---------- 节奏 ----------
        card1 = self._card(outer)
        self._card_title(card1, "节奏", "坐多久、站多久，以及到点后多久重复提醒。",
                         C_SIT)
        grid = tk.Frame(card1, bg=C_BG)
        grid.pack(fill="x", padx=px(16), pady=(px(4), px(4)))
        for label, key, lo, hi, tip in (
                ("坐姿时长", "sit_minutes", 1, 600, "坐下之后，多久提醒你起身"),
                ("站姿时长", "stand_minutes", 1, 600, "站立活动阶段的时长"),
                ("提醒间隔", "alert_minutes", 1, 120, "到点未处理时，多久再提醒一次")):
            row = tk.Frame(grid, bg=C_BG)
            row.pack(fill="x", pady=px(5))
            txt = tk.Frame(row, bg=C_BG)
            txt.pack(side="left", anchor="w")
            tk.Label(txt, text=label, bg=C_BG, fg=C_TEXT,
                     font=(FONT_FAMILY, 10)).pack(anchor="w")
            tk.Label(txt, text=tip, bg=C_BG, fg=C_MUTED,
                     font=(FONT_FAMILY, 8)).pack(anchor="w")
            nf = NumberField(row, cfg[key], lo, hi, suffix="分钟", bg=C_BG,
                             command=lambda v: self._refresh_preview())
            nf.pack(side="right")
            self._dur_fields[key] = nf

        pv = tk.Frame(card1, bg=C_SURFACE)
        pv.pack(fill="x", padx=px(16), pady=(px(2), px(12)))
        self._preview = tk.Label(pv, text="", bg=C_SURFACE, fg=C_TEXT2,
                                 font=(FONT_FAMILY, 9, "bold"),
                                 anchor="w", padx=px(12), pady=px(7))
        self._preview.pack(fill="x")

        # ---------- 提醒方式 ----------
        card2 = self._card(outer)
        self._card_title(card2, "提醒方式", "可同时开启、自由搭配。提示音默认关闭。", C_STAND)
        rows = tk.Frame(card2, bg=C_BG)
        rows.pack(fill="x", padx=px(16), pady=(px(4), px(10)))
        items = (
            ("托盘图标闪烁", "flash_tray", "到点时图标反复闪烁，最醒目"),
            ("弹系统气泡通知", "balloon", "系统级气泡，全屏时也能看到"),
            ("提示音（默认关闭）", "sound", "柔和双音，不会突然吓一跳"),
            ("全屏时仍弹出提醒窗", "alert_on_fullscreen", "关闭则全屏时只闪烁 / 气泡"),
            ("启动时自动开始计时", "auto_start", "关闭则启动后处于暂停"),
            ("托盘图标显示剩余分钟", "tray_show_minutes", "关闭则图标只显示状态色块"),
        )
        for idx, (label, key, hint) in enumerate(items):
            if idx:
                self._divider(rows)
            row = tk.Frame(rows, bg=C_BG)
            row.pack(fill="x", pady=px(5))
            txt = tk.Frame(row, bg=C_BG)
            txt.pack(side="left", fill="x", expand=True)
            tk.Label(txt, text=label, bg=C_BG, fg=C_TEXT,
                     font=(FONT_FAMILY, 10)).pack(anchor="w")
            tk.Label(txt, text=hint, bg=C_BG, fg=C_MUTED,
                     font=(FONT_FAMILY, 8)).pack(anchor="w")
            sw = Switch(row, value=bool(cfg.get(key, DEFAULTS.get(key, False))), bg=C_BG)
            sw.pack(side="right")
            self._switches[key] = sw

        foot = tk.Frame(outer, bg=C_PAGE)
        foot.pack(fill="x", pady=(px(2), 0))
        self._hint = tk.Label(foot, text="", bg=C_PAGE, fg=C_MUTED,
                              font=(FONT_FAMILY, 9))
        self._hint.pack(side="left")
        RoundButton(foot, "保存", command=self._on_save, kind="primary",
                    accent=C_SIT, min_width=100, bg=C_PAGE, height=34).pack(side="right")
        RoundButton(foot, "取消", command=self.close, kind="ghost",
                    bg=C_PAGE, height=34).pack(side="right", padx=(0, 8))
        RoundButton(foot, "恢复默认", command=self._on_reset, kind="ghost",
                    bg=C_PAGE, height=34).pack(side="right", padx=(0, 8))

        self._refresh_preview()

        w.update_idletasks()
        sw_, sh_ = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        w.geometry("+%d+%d" % (max(0, (sw_ - w.winfo_width()) // 2),
                               max(0, (sh_ - w.winfo_height()) // 2 - px(30))))
        w.protocol("WM_DELETE_WINDOW", self.close)
        try:
            w.lift()
            w.focus_force()
        except Exception:
            pass

    @staticmethod
    def _card(parent):
        wrap = tk.Frame(parent, bg=C_BORDER)
        wrap.pack(fill="x", pady=(0, px(13)))
        inner = tk.Frame(wrap, bg=C_BG)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        return inner

    @staticmethod
    def _divider(parent):
        tk.Frame(parent, bg=C_BORDER_2, height=1).pack(fill="x")

    @staticmethod
    def _card_title(card, title, sub, accent=C_SIT):
        head = tk.Frame(card, bg=C_BG)
        head.pack(fill="x", padx=px(16), pady=(px(12), px(0)))
        line = tk.Frame(head, bg=C_BG)
        line.pack(anchor="w", fill="x")
        bar = tk.Frame(line, bg=accent, width=px(3), height=px(14))
        bar.pack(side="left")
        bar.pack_propagate(False)
        tk.Label(line, text=title, bg=C_BG, fg=C_TEXT,
                 font=(FONT_FAMILY, 11, "bold")).pack(side="left", padx=(px(8), 0))
        tk.Label(head, text=sub, bg=C_BG, fg=C_MUTED,
                 font=(FONT_FAMILY, 8)).pack(anchor="w", pady=(px(3), 0))

    def _on_reset(self):
        for key, nf in self._dur_fields.items():
            nf.set_value(DEFAULTS[key], notify=False)
        for key, sw in self._switches.items():
            sw.set(DEFAULTS[key])
        self._refresh_preview()

    def _on_save(self):
        cfg = self.app.cfg
        for key, nf in self._dur_fields.items():
            cfg[key] = int(nf.commit())
        for key, sw in self._switches.items():
            cfg[key] = bool(sw.value)
        save_settings(dict(cfg))
        self.app.on_settings_changed()
        self.close()


# ============================================================================
# 使用说明窗
# ============================================================================
class HelpWindow:
    def __init__(self, app):
        self.app = app
        self.root = app.root
        self.win = None

    def is_open(self):
        return self.win is not None and self.win.winfo_exists()

    def close(self):
        if self.win is not None:
            try:
                self.win.destroy()
            except Exception:
                pass
            self.win = None

    def open(self):
        if self.is_open():
            self.win.lift()
            return
        w = tk.Toplevel(self.root)
        self.win = w
        w.title("%s · 使用说明" % APP_NAME)
        w.configure(bg=C_PAGE)
        w.geometry("%dx%d" % (px(640), px(600)))
        try:
            w.attributes("-topmost", True)
        except Exception:
            pass

        outer = tk.Frame(w, bg=C_PAGE)
        outer.pack(fill="both", expand=True, padx=px(20), pady=px(18))
        tk.Label(outer, text="使用说明", bg=C_PAGE, fg=C_TEXT,
                 font=(FONT_FAMILY, 18, "bold")).pack(anchor="w")
        tk.Label(outer, text="%s v%s · 免安装 · 离线可用" % (APP_NAME, APP_VERSION),
                 bg=C_PAGE, fg=C_MUTED, font=(FONT_FAMILY, 9)).pack(anchor="w",
                                                                    pady=(px(2), px(12)))

        wrap = tk.Frame(outer, bg=C_BORDER)
        wrap.pack(fill="both", expand=True)
        card = tk.Frame(wrap, bg=C_BG)
        card.pack(fill="both", expand=True, padx=1, pady=1)

        sb = tk.Scrollbar(card, width=px(12))
        sb.pack(side="right", fill="y", padx=(0, px(10)), pady=px(10))
        t = tk.Text(card, wrap="word", bg=C_BG, fg=C_TEXT2, bd=0,
                    font=(FONT_FAMILY, 10), yscrollcommand=sb.set,
                    padx=px(18), pady=px(14), spacing1=px(2), spacing3=px(6),
                    cursor="arrow", highlightthickness=0)
        t.pack(side="left", fill="both", expand=True)
        sb.config(command=t.yview)
        t.tag_configure("h", foreground=C_TEXT, font=(FONT_FAMILY, 12, "bold"),
                        spacing1=10, spacing3=6)
        t.tag_configure("b", foreground=C_TEXT2, font=(FONT_FAMILY, 10))
        t.tag_configure("k", foreground=C_SIT, font=(FONT_FAMILY, 10, "bold"))
        for kind, text in HELP_SEGMENTS:
            t.insert("end", text + "\n", kind)
        t.config(state="disabled")

        foot = tk.Frame(outer, bg=C_PAGE)
        foot.pack(fill="x", pady=(px(12), 0))
        RoundButton(foot, "知道了", command=self.close, kind="primary",
                    accent=C_SIT, min_width=104, bg=C_PAGE, height=38).pack(side="right")

        w.update_idletasks()
        sw_, sh_ = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        w.geometry("+%d+%d" % (max(0, (sw_ - px(640)) // 2),
                               max(0, (sh_ - px(600)) // 2 - px(20))))
        w.protocol("WM_DELETE_WINDOW", self.close)


HELP_SEGMENTS = [
    ("h", "这是什么"),
    ("b", "面向升降桌的久坐提醒工具，按「坐下 → 到点提醒 → 站立活动 → 回到坐下」循环。"
          "默认 45 分钟坐 / 8 分钟站（均可调），默认静音、平时不遮挡桌面。"),
    ("h", "怎样开始"),
    ("b", "双击 SitStandReminder.exe（源码版为 Start.bat）即可。启动后自动开始计时，"
          "只在任务栏右下角常驻一个小图标，不会弹出窗口挡住你。"),
    ("h", "到点会怎样提醒"),
    ("b", "以下方式同时触发，可在设置里任意开关搭配："),
    ("k", "1) 托盘图标闪烁　2) 图标上直接显示剩余分钟　3) 弹系统气泡"),
    ("k", "4) 自动弹出提醒窗，内含「我已站起 / 稍后 N 分钟 / 跳过」"),
    ("b", "到点不需要去找入口——提醒窗会自己出现，点一下即可。"
          "若一直不处理，每隔「提醒间隔」分钟会再提醒一次。"),
    ("h", "任务栏图标怎么用"),
    ("b", "左键单击：默认执行「开始 / 暂停计时」。"),
    ("b", "鼠标悬停在图标上：气泡显示当前状态和剩余时间。"),
    ("b", "右键单击：打开菜单，可暂停/继续、立刻起身/坐下、显示/隐藏悬浮细条、设置、说明、退出。"),
    ("b", "图标上的数字＝当前阶段剩余分钟数。"),
    ("h", "悬浮细条（可选）"),
    ("b", "默认不显示。托盘菜单里选「显示悬浮细条」后出现。它平时会像 QQ 那样收在屏幕边缘："),
    ("k", "· 贴边只留一条细长的圆角矩形（约 12 像素宽）、完整显示，不占地方、不挡事；"),
    ("k", "· 里面用颜色从下往上填充，填充比例＝剩余时间百分比；"),
    ("k", "· 鼠标移上去自动弹出，显示精确剩余时间，移开后自动收回。"),
    ("b", "需要移动它时，按住卡片空白处拖动即可（严格跟手）："),
    ("k", "· 拖到屏幕左/右边缘附近松手 → 自动吸附贴边并收起；"),
    ("k", "· 拖到屏幕中间松手 → 停在那里，约 2 秒后自己收起；"),
    ("k", "· 卡片上的三个小按钮：暂停/继续、菜单、关闭。"),
    ("h", "关于窗口的投影"),
    ("b", "悬浮条与提醒窗外面那圈柔和的投影由 Windows 自己绘制（原生投影），"
          "所以它随壁纸明暗自然变化，不会出现「白边」或「黑框」。"),
    ("h", "声音"),
    ("b", "默认绝对不响。若想开启，到「设置」里打开「提示音」，会播放一段柔和的提示音。"),
    ("h", "设置"),
    ("b", "托盘菜单 →「设置…」。三个时长与各种提醒方式都在这里，改完点保存即刻生效，"
          "并写入同目录 settings.json（下次启动沿用）。"),
    ("k", "· 时长可以直接用键盘输入数字，也可以点 − / + 微调，两种方式都行；"),
    ("k", "· 输入超出范围会自动夹到合法值（坐/站 1–600 分钟，提醒间隔 1–120 分钟）；"),
    ("k", "· 输入框里按回车确认、按 Esc 撤销，也可以用滚轮微调。"),
    ("h", "便携与离线"),
    ("b", "整个文件夹可直接拷到任意 64 位 Windows 电脑运行，不需要安装 Python，也不需要联网。"
          "设置保存在程序同目录的 settings.json。"),
    ("h", "常见问题"),
    ("b", "· 启动没反应？看同目录 app.log。"),
    ("b", "· 想看到报错？用源码版的 Debug.bat 启动，会有黑色窗口显示日志。"),
    ("b", "· 想彻底退出？托盘右键 →「退出」。"),
    ("b", "· 提醒窗挡住视频？处理或点「稍后」后自动消失；也可在设置里关掉「全屏时仍弹出提醒窗」。"),
]


# ============================================================================
# 主应用
# ============================================================================
class App:
    def __init__(self):
        self.cfg = load_settings()
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(APP_TITLE)
        try:
            set_ui_scale(self.root.winfo_fpixels("1i") / 96.0)
        except Exception:
            set_ui_scale(1.0)

        self.mode = "sit"
        self.running = bool(self.cfg.get("auto_start", True))
        self.paused = False
        self.alerting = False
        self.alert_elapsed = 0.0
        self.remaining = float(self.cfg["sit_minutes"] * 60)
        self._last = time.monotonic()
        self._pending_target = None

        self.icon = None
        self.cmdq = queue.Queue()
        self._flash_on = False
        self._flash_last = 0.0
        self._last_icon_key = None
        self._last_tip = None
        self._last_menu_status = None

        self.strip = StripWindow(self)
        self.alert_win = AlertWindow(self)
        self.settings_win = SettingsWindow(self)
        self.help_win = HelpWindow(self)

        self._save_job = None
        self._build_tray()

    # ---- 托盘 ---------------------------------------------------------------
    def _make_icon_image(self, text, color, size=64):
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        radius = int(size * 0.26)
        grad = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        gd = ImageDraw.Draw(grad)
        top, bot = lighten(color, 0.18), darken(color, 0.10)
        for y in range(size):
            gd.line([(0, y), (size, y)],
                    fill=_hex_to_rgb(mix(top, bot, y / max(1, size - 1))) + (255,))
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).rounded_rectangle([1, 1, size - 2, size - 2],
                                               radius=radius, fill=255)
        img.paste(grad, (0, 0), mask)
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([1, 1, size - 2, size - 2], radius=radius,
                            outline=_hex_to_rgb(lighten(color, 0.32)) + (255,), width=1)
        fs = int(size * (0.62 if len(str(text)) <= 2 else 0.44))
        font = pick_font_file(fs, bold=True)
        try:
            bbox = d.textbbox((0, 0), str(text), font=font)
            d.text(((size - (bbox[2] - bbox[0])) / 2 - bbox[0],
                    (size - (bbox[3] - bbox[1])) / 2 - bbox[1] + 1),
                   str(text), font=font, fill=(255, 255, 255, 255))
        except Exception:
            d.text((8, 20), str(text), fill=(255, 255, 255, 255))
        return img

    def _icon_spec(self):
        if self.alerting:
            if self.cfg.get("flash_tray") and self._flash_on:
                # 闪烁的“深色档”由 C_ALERT 现算，换主色时自动跟随（原来硬编码 #B42318）。
                return ("!", darken(C_ALERT, 0.35))
            return ("!", C_ALERT)
        if not self.running:
            return ("--", C_IDLE)
        color = C_IDLE if self.paused else (C_SIT if self.mode == "sit" else C_STAND)
        if self.cfg.get("tray_show_minutes", True):
            m = int(math.ceil(max(0.0, self.remaining) / 60.0))
            return (str(min(m, 99)), color)
        return ("●", color)

    def _build_tray(self):
        """托盘右键菜单。

        ⚠️ 这里所有标签都必须是**静态字符串**，绝不能是 lambda/回调。
        原因（用户反馈「右键菜单卡住」的根因）：
          pystray 在 Windows 上是用 TrackPopupMenuEx 弹出菜单的，它会**阻塞托盘线程**
          直到菜单关闭。而带回调标签的菜单项只有在 update_menu() 里才会刷新文字，
          于是我们必须每秒调用一次 update_menu()，而它内部会 DestroyMenu 再重建 ——
          等于把「正在显示的那个菜单」拆掉了：鼠标悬停没了高亮、整块像卡死，
          但点击仍会被记录下来（关闭后才派发）。
        改成静态标签后，就不再需要 update_menu()，菜单自然就不会被中途拆掉。
        实时状态（剩余时间）放在 tooltip 里显示，效果一样而且不干扰弹出菜单。
        """
        menu = Menu(
            MenuItem("立即暂停 / 继续", self._q("toggle_pause"), default=True),
            MenuItem("现在就起身（切到站姿）", self._q("stand_now")),
            MenuItem("现在就坐下（切到坐姿）", self._q("sit_now")),
            MenuItem("重新开始本轮", self._q("restart")),
            Menu.SEPARATOR,
            MenuItem("显示 / 隐藏悬浮细条", self._q("toggle_strip")),
            MenuItem("设置…", self._q("settings")),
            MenuItem("使用说明…", self._q("help")),
            Menu.SEPARATOR,
            MenuItem("退出", self._q("quit")),
        )
        # 留一份标签清单：冒烟测试要靠它验证「标签全是静态字符串」，
        # 而直接遍历 pystray 的 Menu.items 会走到托盘线程上、可能把测试卡住。
        self._tray_menu_labels = [
            "立即暂停 / 继续", "现在就起身（切到站姿）", "现在就坐下（切到坐姿）",
            "重新开始本轮", "显示 / 隐藏悬浮细条", "设置…", "使用说明…", "退出",
        ]
        self.icon = Icon(APP_NAME, self._make_icon_image("45", C_SIT), APP_TITLE, menu)

    def _q(self, name):
        def cb(icon=None, item=None):
            self.cmdq.put(name)
        return cb

    def _status_text(self):
        if self.alerting:
            return "⏰ 到点：该%s了" % ("站起" if self.mode == "sit" else "坐下")
        if not self.running:
            return "已停止 · 点击开始计时"
        tag = "暂停" if self.paused else ("坐姿" if self.mode == "sit" else "站姿")
        return "%s · 剩余 %s" % (tag, fmt_mmss(self.remaining))

    def _tray_loop(self):
        try:
            self.icon.run()
        except Exception:
            log.exception("托盘线程异常")

    def _update_icon(self, force=False):
        # 整个函数包 try —— 它由 tick() 每 200ms 调用一次，
        # 而 pystray 的 icon/title 赋值是跨线程发 Windows 消息，
        # 托盘线程正忙时（例如右键菜单正显示）可能抛异常。
        # 一旦抛出去把 tick 打断，计时就会永久卡死，所以这里必须吞掉。
        try:
            if self.icon is None:
                return
            text, color = self._icon_spec()
            key = (text, color)
            if force or key != self._last_icon_key:
                self._last_icon_key = key
                try:
                    self.icon.icon = self._make_icon_image(text, color)
                except Exception:
                    log.exception("更新图标失败")
            if self.alerting:
                tip = "到点：该%s了" % ("站起活动一下" if self.mode == "sit" else "回来坐下")
            elif not self.running:
                tip = "已停止"
            else:
                tip = "%s · 剩余 %s" % ("坐姿" if self.mode == "sit" else "站姿",
                                        fmt_mmss(self.remaining))
            if tip != self._last_tip:
                self._last_tip = tip
                try:
                    self.icon.title = "%s — %s" % (APP_TITLE, tip)
                except Exception:
                    pass
            # 注意：这里**故意不调用** self.icon.update_menu()。
            # 菜单项标签是静态的，不需要刷新；而 update_menu() 会在菜单正被显示时
            # DestroyMenu+重建，正是「右键菜单卡住、悬停无高亮」的元凶。详见 _build_tray 注释。
            self._last_menu_status = self._status_text()
        except Exception:
            log.exception("刷新托盘图标失败（已忽略）")

    def _balloon(self, title, message):
        if not self.cfg.get("balloon", True) or self.icon is None:
            return
        try:
            self.icon.notify(message, title)
        except Exception:
            log.exception("气泡通知失败")

    # ---- 计时 ---------------------------------------------------------------
    def _duration(self, mode):
        return float(self.cfg["sit_minutes" if mode == "sit" else "stand_minutes"] * 60)

    def tick(self):
        """主循环（每 200ms 一次）。

        ⚠️ 整个函数体必须包在 try 里，而且 `after()` 的**重排必须放在 finally**。
        否则任何一处抛异常（最典型的是窗口被销毁后 strip.refresh() 抛 TclError）
        都会让 `after()` 执行不到 —— 主循环就此永久停摆，表现就是
        「计时不走、界面怎么点都没反应」。这是用户反馈的那个 bug 的根因。
        """
        try:
            now = time.monotonic()
            dt = now - self._last
            self._last = now
            if dt < 0 or dt > 30:
                dt = 0.2

            if self.running:
                if self.alerting:
                    self.alert_elapsed += dt
                    if self.alert_elapsed >= self.cfg["alert_minutes"] * 60:
                        self.alert_elapsed = 0.0
                        self._retrigger_alert()
                elif not self.paused:
                    self.remaining -= dt
                    if self.remaining <= 0:
                        self.remaining = 0.0
                        self._start_alert()

            if self.alerting and self.cfg.get("flash_tray"):
                if now - self._flash_last >= 0.45:
                    self._flash_last = now
                    self._flash_on = not self._flash_on
                    self._update_icon(force=True)
            else:
                self._flash_on = False

            self._update_icon()
            self.strip.refresh()
        except Exception:
            log.exception("主循环 tick 异常（已忽略，循环继续）")
        finally:
            # 无论如何都要把下一拍排上 —— 这一句是"计时永不卡死"的保证。
            try:
                self.root.after(200, self.tick)
            except Exception:
                log.exception("重排主循环失败")

    # ---- 提醒流程 -----------------------------------------------------------
    def _start_alert(self):
        self.alerting = True
        self.alert_elapsed = 0.0
        self._flash_on = True
        self._flash_last = time.monotonic()
        self._update_icon(force=True)

        if self.mode == "sit":
            title = "该站起来了"
            subtitle = "您已连续坐姿 %d 分钟，起身活动 %d 分钟。" % (
                self.cfg["sit_minutes"], self.cfg["stand_minutes"])
            primary, accent, glyph = "我已站起", C_STAND, "up"
            msg = "坐满 %d 分钟了，站起来活动一下。" % self.cfg["sit_minutes"]
        else:
            title = "该坐下了"
            subtitle = "您已站立 %d 分钟，可以回到坐姿了。" % self.cfg["stand_minutes"]
            primary, accent, glyph = "我已坐下", C_SIT, "down"
            msg = "站满 %d 分钟了，坐下继续工作。" % self.cfg["stand_minutes"]

        self._pending_target = "stand" if self.mode == "sit" else "sit"

        if (not is_fullscreen()) or self.cfg.get("alert_on_fullscreen", True):
            self.alert_win.show(title, subtitle, primary, accent, glyph)
        self._balloon("%s · %s" % (APP_NAME, title), msg)
        if self.cfg.get("sound", False):
            play_soft_chime()

    def _retrigger_alert(self):
        self._flash_on = True
        self._flash_last = time.monotonic()
        self._update_icon(force=True)
        title, msg = (("该站起来了", "提醒：该站起来活动了。") if self.mode == "sit"
                      else ("该坐下了", "提醒：可以坐下继续了。"))
        self._balloon("%s · 提醒" % APP_NAME, msg)
        if self.cfg.get("sound", False):
            play_soft_chime()
        if self.alert_win.top is not None and self.alert_win.top.winfo_viewable():
            try:
                self.alert_win.top.lift()
            except Exception:
                pass

    def _clear_alert(self):
        self.alerting = False
        self.paused = False
        self.running = True
        self.alert_win.hide()
        self._flash_on = False
        self._update_icon(force=True)

    def alert_confirm(self):
        if self._pending_target:
            self.mode = self._pending_target
        self._clear_alert()
        self.remaining = self._duration(self.mode)

    def alert_snooze(self):
        self._clear_alert()
        self.remaining = float(self.cfg["snooze_minutes"] * 60)

    def alert_skip(self):
        self._clear_alert()
        self.remaining = self._duration(self.mode)

    # ---- 主动切换 -----------------------------------------------------------
    def switch_now(self, mode):
        self.alert_win.hide()
        self.mode = mode
        self._clear_alert()
        self.remaining = self._duration(mode)

    def toggle_pause(self):
        if not self.running:
            self.running = True
            self.paused = False
        else:
            self.paused = not self.paused
        self._update_icon(force=True)

    def restart_round(self):
        self.alert_win.hide()
        self.mode = "sit"
        self._clear_alert()
        self.remaining = self._duration("sit")

    def on_settings_changed(self):
        if not self.alerting:
            dur = self._duration(self.mode)
            if self.remaining > dur or self.remaining <= 0:
                self.remaining = dur
        self._update_icon(force=True)
        self.strip.refresh()

    # ---- 菜单命令 -----------------------------------------------------------
    def popup_menu_at_cursor(self):
        m = tk.Menu(self.root, tearoff=0, font=(FONT_FAMILY, 10))
        m.add_command(label="开始 / 暂停计时", command=self.toggle_pause)
        m.add_command(label="现在就起身", command=lambda: self.switch_now("stand"))
        m.add_command(label="现在就坐下", command=lambda: self.switch_now("sit"))
        m.add_command(label="重新开始本轮", command=self.restart_round)
        m.add_separator()
        m.add_command(label="隐藏悬浮细条", command=self.strip.hide)
        m.add_command(label="设置…", command=self.open_settings)
        m.add_command(label="使用说明…", command=self.open_help)
        m.add_separator()
        m.add_command(label="退出", command=self.quit_app)
        try:
            m.tk_popup(self.root.winfo_pointerx(), self.root.winfo_pointery())
        except Exception:
            log.exception("弹出菜单失败")
        finally:
            try:
                m.grab_release()
            except Exception:
                pass

    def _poll_cmds(self):
        """从托盘线程取命令。

        每个命令单独包 try：一条命令失败不能影响后面排队的命令，
        更不能让 `after()` 断掉（否则托盘菜单从此失效）。
        """
        try:
            while True:
                try:
                    name = self.cmdq.get_nowait()
                except queue.Empty:
                    break
                try:
                    self._handle_cmd(name)
                except Exception:
                    log.exception("处理命令 %s 失败（已忽略）", name)
        finally:
            try:
                self.root.after(120, self._poll_cmds)
            except Exception:
                log.exception("重排命令轮询失败")

    def _handle_cmd(self, name):
        try:
            if name == "toggle_pause":
                self.toggle_pause()
            elif name == "stand_now":
                self.switch_now("stand")
            elif name == "sit_now":
                self.switch_now("sit")
            elif name == "restart":
                self.restart_round()
            elif name == "toggle_strip":
                self.strip.toggle()
            elif name == "settings":
                self.open_settings()
            elif name == "help":
                self.open_help()
            elif name == "quit":
                self.quit_app()
        except Exception:
            log.exception("处理命令 %s 失败", name)

    def open_settings(self):
        self.settings_win.open()

    def open_help(self):
        self.help_win.open()

    # ---- 保存节流 -----------------------------------------------------------
    def debounce_save(self):
        if self._save_job is not None:
            try:
                self.root.after_cancel(self._save_job)
            except Exception:
                pass
        self._save_job = self.root.after(800, self._flush_save)

    def _flush_save(self):
        self._save_job = None
        save_settings(dict(self.cfg))

    # ---- 退出 ---------------------------------------------------------------
    def quit_app(self):
        log.info("退出")
        try:
            self._flush_save()
        except Exception:
            pass
        try:
            if self.icon is not None:
                self.icon.stop()
        except Exception:
            pass
        for win in (self.alert_win, self.strip):
            try:
                win.destroy()
            except Exception:
                pass
        try:
            self.root.quit()
            self.root.destroy()
        except Exception:
            pass

    # ---- 运行 ---------------------------------------------------------------
    def run(self):
        threading.Thread(target=self._tray_loop, daemon=True).start()
        self.remaining = self._duration(self.mode)
        if not self.cfg.get("auto_start", True):
            self.running = False
        self.root.after(300, self._poll_cmds)
        self.root.after(200, self.tick)
        log.info("就绪：模块内耗时 %.0f ms" % ((time.perf_counter() - _MODULE_T0) * 1000))
        self.root.mainloop()


# ============================================================================
# 入口
# ============================================================================
def single_instance_guard():
    import ctypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        try:
            ctypes.windll.user32.MessageBoxW(
                0, "久坐提醒已经在运行了。\n请查看任务栏右下角的图标。", APP_TITLE, 0x40)
        except Exception:
            pass
        return None
    return handle


def main():
    enable_dpi_awareness()
    guard = single_instance_guard()
    if guard is None:
        return

    def excepthook(exc_type, exc, tb):
        log.error("未捕获异常:\n%s",
                  "".join(traceback.format_exception(exc_type, exc, tb)))
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0, "程序发生错误：\n\n%s\n\n详情见 app.log。" % exc, APP_TITLE, 0x10)
        except Exception:
            pass

    sys.excepthook = excepthook
    try:
        threading.excepthook = lambda a: log.error("线程异常: %s", a)
    except Exception:
        pass

    try:
        App().run()
    except Exception:
        log.exception("启动失败")
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, "启动失败，详情见 app.log。",
                                             APP_TITLE, 0x10)
        except Exception:
            pass


if __name__ == "__main__":
    main()
