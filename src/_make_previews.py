# -*- coding: utf-8 -*-
"""离屏生成 README 用的预览图 —— 零打扰：不抓屏、不闪窗、不出声、不碰桌面。

原理
----
窗口「离屏」但不「withdraw」：把它们真 map 到屏幕外的坐标（`-8000,-8000`），
用户看不到；然后用 `PrintWindow(hwnd, hdc, PW_RENDERFULLCONTENT=2)` 把窗口
自身的内容渲染到内存 DC 里抓回来。

为什么不直接抓屏（ImageGrab）
------------------------------
抓屏会把**窗口背后的桌面**一起拍进去 —— 这正是上一版预览图问题的根源：
提醒窗那张图上能看到用户桌面上别的窗口和广告图。PrintWindow 只渲染窗口自己，
背景天然是干净的。

产出（写入 ../previews/）：
    strip-expanded.png    悬浮细条 · 展开态卡片
    strip-collapsed.png   悬浮细条 · 收起态，三档剩余时间对照
    alert.png             到点提醒窗
    settings.png          设置窗口
    help.png              使用说明窗口
"""
import os
import sys
import ctypes
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import sitstand as S

S.enable_dpi_awareness()

import tkinter as tk
from PIL import Image, ImageDraw, ImageFont
import PIL.ImageTk as ImageTk

OUT = os.path.join(os.path.dirname(HERE), "previews")
os.makedirs(OUT, exist_ok=True)

# 屏幕外的作业坐标：真实 map 但用户看不见
OFF = -8000
BG = (238, 240, 244)          # 单窗预览图的底色
SHEET_BG = (231, 234, 240)    # 拼图底色：比白条略深，白条轮廓才看得清

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32


# ---------------------------------------------------------------------------
# PrintWindow
# ---------------------------------------------------------------------------
class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


def grab_window(hwnd, w, h):
    """把窗口自身内容渲染进内存位图并返回 RGBA Image。"""
    hdc = user32.GetWindowDC(hwnd)
    memdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(memdc, bmp)

    bi = BITMAPINFO()
    bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.bmiHeader.biWidth = w
    bi.bmiHeader.biHeight = -h            # top-down
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    bi.bmiHeader.biCompression = 0        # BI_RGB

    # PW_RENDERFULLCONTENT = 2
    ret = user32.PrintWindow(hwnd, memdc, 2)

    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(memdc, bmp, 0, h, buf, ctypes.byref(bi), 0)

    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(memdc)
    user32.ReleaseDC(hwnd, hdc)

    img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).copy()
    return ret, img


def hwnd_of(widget):
    return int(widget.winfo_id())


def pump(root, dt=0.35):
    import time
    t0 = time.time()
    while time.time() - t0 < dt:
        try:
            root.update()
        except Exception:
            pass
        time.sleep(0.008)


def _font(size, bold=False):
    name = "msyhbd.ttc" if bold else "msyh.ttc"
    for f in (name, "msyh.ttc", "simhei.ttf", "simsun.ttc"):
        try:
            return ImageFont.truetype(
                os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", f),
                size)
        except Exception:
            continue
    return ImageFont.load_default()


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main():
    # 静音护栏（这个脚本本来也不会触发提示音，双保险）
    S.play_soft_chime = lambda *a, **k: None
    try:
        import winsound
        winsound.PlaySound = lambda *a, **k: None
        winsound.Beep = lambda *a, **k: None
        winsound.MessageBeep = lambda *a, **k: None
    except Exception:
        pass

    # ------------------------------------------------------------------
    # 关键补丁：让窗口里的圆角贴图**保留天然抗锯齿**。
    #
    # `aa_rounded_image(..., opaque_bg=...)` 会把 alpha 压平成 0/255 —— 那是为了
    # 配合 Tk 的 `-transparentcolor` 精确抠色（实机运行时必需）。但我们要的是
    # 高清预览图：二值化的边缘一放大就是台阶锯齿。
    #
    # 而本脚本是用 PrintWindow 直接读**窗口自身内容**的，不依赖 Tk 抠色，
    # 所以这里把 opaque_bg 丢掉，让边缘恢复成柔和的半透明过渡。
    # ------------------------------------------------------------------
    _orig_aa = S.aa_rounded_image

    def _aa_no_flatten(w, h, radius, fill, outline=None, border=1,
                       super_ratio=4, opaque_bg=None):
        return _orig_aa(w, h, radius, fill, outline, border, super_ratio,
                        opaque_bg=None)

    S.aa_rounded_image = _aa_no_flatten

    app = S.App()
    root = app.root
    app.root.update()

    try:
        shots(app, root)
    finally:
        S.aa_rounded_image = _orig_aa
        try:
            app.strip.destroy()
        except Exception:
            pass
        try:
            app.alert_win.destroy()
        except Exception:
            pass
        try:
            app.settings_win.close()
        except Exception:
            pass
        try:
            root.destroy()
        except Exception:
            pass
        try:
            app.icon.stop()
        except Exception:
            pass


def _save(img, name, zoom=2, bg=BG, border=True):
    """把抓到的窗口图放到干净底色上，放大 zoom 倍存盘。

    PrintWindow 会把 `-transparentcolor` 的键色区域渲染成黑色，所以圆角外侧
    是纯黑。窗口里的圆角图已由 `flatten_to_key` 压平（alpha 只有 0/255），
    边缘没有过渡，直接放大就是阶梯锯齿。

    处理方式：先用**泛洪填充**（从四角出发）精确定位"窗口外部"的黑色区域，
    只把紧贴外部的**一圈**做羽化，卡片内部的深色文字/图标完全不受影响。
    """
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    w, h = img.size

    # 如果 PrintWindow 已经带上了可用的 alpha（贴图未压平时会保留天然抗锯齿），
    # 就直接用它，不再做任何抠色 —— 这是质量最好的路径。
    a_ch = img.split()[3]
    if a_ch.getextrema()[0] < 250 and a_ch.getextrema()[1] > 0:
        out = img.copy()
    else:
        out = _knock_out(img)

    pad = 18
    sheet = Image.new("RGBA", (w * zoom + pad * 2, h * zoom + pad * 2),
                      bg + (255,))
    big = out.resize((w * zoom, h * zoom), Image.LANCZOS)
    sheet.alpha_composite(big, (pad, pad))
    sheet.convert("RGB").save(os.path.join(OUT, name))
    print("saved %-22s %s" % (name, sheet.size))


def _knock_out(img):
    """按「与边界连通的黑色 = 窗口外部」把键色区抠成透明，并羽化一圈边缘。"""
    src = img.convert("RGB")
    sp = src.load()
    w, h = src.size

    # --- 1. 泛洪：从四边出发，把与边界连通的"黑区"标为外部 ---
    def is_dark(p):
        return p[0] <= 12 and p[1] <= 13 and p[2] <= 14

    outside = bytearray(w * h)
    stack = []
    for x in range(w):
        for y in (0, h - 1):
            if is_dark(sp[x, y]) and not outside[y * w + x]:
                outside[y * w + x] = 1
                stack.append((x, y))
    for y in range(h):
        for x in (0, w - 1):
            if is_dark(sp[x, y]) and not outside[y * w + x]:
                outside[y * w + x] = 1
                stack.append((x, y))
    while stack:
        cx, cy = stack.pop()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = cx + dx, cy + dy
            if 0 <= nx < w and 0 <= ny < h:
                i = ny * w + nx
                if not outside[i] and is_dark(sp[nx, ny]):
                    outside[i] = 1
                    stack.append((nx, ny))

    # --- 2. 组装：外部全透明；紧邻外部的内容像素按亮度给一个羽化 alpha ---
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    op = out.load()
    for y in range(h):
        for x in range(w):
            i = y * w + x
            if outside[i]:
                op[x, y] = (0, 0, 0, 0)
                continue
            r, g, b = sp[x, y]
            near = False
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1),
                           (1, 1), (1, -1), (-1, 1), (-1, -1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h and outside[ny * w + nx]:
                    near = True
                    break
            if near:
                lum = (r + g + b) / 3.0
                a = int(max(0, min(255, 255 * (lum - 12) / 120.0)))
                op[x, y] = (r, g, b, a)
            else:
                op[x, y] = (r, g, b, 255)
    return out


def _bar_tile(frac, ew, eh, radius, color):
    """复现 _render_edge 的细条：白底圆角矩形 + 从下往上按比例填色。

    ⚠️ 这里**故意不传 opaque_bg**。`aa_rounded_image` 的 `opaque_bg` 会把 alpha
    二值化成 0/255（那是为了配合 Tk 的 `-transparentcolor` 精确抠色），
    但预览图要放大好几倍，二值化后的硬边一放大就是明显的锯齿。
    不传它就能保留天然的抗锯齿过渡，放大后依然平滑。

    填充部分用**底条的圆角轮廓做遮罩**，让色块严丝合缝贴着白条，
    底部不会露出白色月牙（真实代码是直接叠一张裁切过的圆角矩形，
    在高倍放大下会看到白边的毛刺）。
    """
    base = S.aa_rounded_image(ew, eh, radius, "#FFFFFF", S.C_BORDER, 1, 8)
    fh = int(eh * frac)
    if fh >= 1:
        # 整条填色图（同圆角），只取底部 fh 高度，再**用底条 alpha 当遮罩**裁剪，
        # 这样蓝色严丝合缝贴着白条轮廓，底部不会露白月牙。
        shape = S.aa_rounded_image(ew, eh, radius, color, None, 0, 8)
        top = eh - fh
        band = Image.new("RGBA", (ew, eh), (0, 0, 0, 0))
        band.paste(shape.crop((0, top, ew, eh)), (0, top))
        # 只保留底条轮廓内 + 底部 fh 区域
        shape_mask = base.split()[3]
        band_mask = Image.new("L", (ew, eh), 0)
        band_mask.paste(shape_mask.crop((0, top, ew, eh)), (0, top))
        base = Image.composite(band, base, band_mask)
    return base


def _card_preview(st, app, ss=3):
    """离屏超采样渲染展开态卡片（复现 `StripWindow._render_card` 的布局）。

    为什么不用 PrintWindow 抓真窗口：窗口里的贴图是 `flatten_to_key` 压平过的
    （alpha 二值化，为了配合 Tk 的 `-transparentcolor`），分辨率就是屏幕物理尺寸，
    放大会露出台阶锯齿。这里用 ss 倍超采样自己画一遍，圆角与文字都平滑得多。

    布局参数与 `_render_card` 保持一致（贴右侧：按钮在左、读数在右）。
    """
    W, H = st.CARD_W, st.CARD_H
    # 画布与 px() 的关系：源码里 px(v) = v * SCALE，这里按 ss 再放大一遍
    k = ss
    Wk, Hk = W * k, H * k

    card = S.aa_rounded_image(Wk, Hk, Hk / 2, S.C_BG, S.C_BORDER, 1 * k, 4)

    def P(v):
        return int(round(S.px(v) * k))

    d = ImageDraw.Draw(card)
    cy = Hk / 2
    color, label = st._state()
    reading = "到点" if app.alerting else S.fmt_mmss(app.remaining)
    cap = Hk / 2 + P(5)

    # 贴右侧：按钮在左，状态点在右
    dot_cx = Wk - cap
    text_x = dot_cx - P(14)
    bar_l = P(15) + P(100)
    bar_r = Wk - P(112)

    # 状态点
    dr = P(3.5)
    d.ellipse([dot_cx - dr, cy - dr, dot_cx + dr, cy + dr], fill=color)

    # 读数（右对齐）
    f_time = _font(int(11 * k * 1.33), bold=True)
    txt = "%s %s" % (label, reading)
    tw = d.textlength(txt, font=f_time)
    d.text((text_x - tw, cy - f_time.size * 0.60), txt, font=f_time,
           fill=S.C_TEXT)

    # 进度条
    if bar_r - bar_l > P(20):
        frac = st._progress()
        bh = P(4)
        bar = S.make_progress_bar(int(bar_r - bar_l), int(bh), frac, color)
        card.alpha_composite(bar, (int(bar_l), int(cy - bh / 2)))

    # 三个小按钮
    playing = app.running and not app.paused
    rr, arm, dot2 = P(11), P(4), P(4)
    btn_start = cap + P(2)
    for i, glyph in enumerate(("pause" if playing else "play", "dots", "close")):
        bx = btn_start + P(31) * i
        d.ellipse([bx - rr, cy - rr, bx + rr, cy + rr],
                  fill=S.C_SURFACE, outline=S.C_BORDER, width=max(1, k))
        if glyph == "pause":
            d.line([bx - P(3), cy - arm, bx - P(3), cy + arm], fill=S.C_TEXT2, width=k)
            d.line([bx + P(1), cy - arm, bx + P(1), cy + arm], fill=S.C_TEXT2, width=k)
        elif glyph == "play":
            d.polygon([(bx - P(3), cy - P(5)), (bx - P(3), cy + P(5)),
                       (bx + P(4), cy)], fill=S.C_TEXT2)
        elif glyph == "dots":
            for ddx in (-dot2, 0, dot2):
                d.ellipse([bx + ddx - P(1.6), cy - P(1.6),
                           bx + ddx + P(1.6), cy + P(1.6)], fill=S.C_TEXT2)
        else:
            d.line([bx - arm, cy - arm, bx + arm, cy + arm], fill=S.C_TEXT2, width=k)
            d.line([bx - arm, cy + arm, bx + arm, cy - arm], fill=S.C_TEXT2, width=k)

    return card


def _round_button_img(text, kind, accent, height=34, min_width=0,
                      bg=S.C_BG, padx=18, font_size=10, k=4):
    """离屏复现 `RoundButton` 的外观（返回 RGBA 图 + 宽度）。

    ⚠️ 宽度必须用 **tkfont.measure()** 量，不能拿 Pillow 代替 ——
    源码里按钮宽度就是 `max(min_width, tkfont.measure(text) + 2*padx)`，
    换成 Pillow 量会有几个像素的出入，拼出来就和真实界面对不上
    （「跳过」那种窄按钮尤其容易被裁字）。
    """
    import tkinter.font as tkfont
    weight = "bold" if kind in ("primary", "danger") else "normal"
    f_tk = tkfont.Font(family=S.FONT_FAMILY, size=font_size, weight=weight)
    w = max(S.px(min_width), f_tk.measure(text) + S.px(padx) * 2)
    h = S.px(height)

    Wk, Hk = int(round(w * k)), int(round(h * k))
    r = Hk / 2

    if kind == "primary":
        fill, fg, border = accent, "#FFFFFF", None
    elif kind == "danger":
        fill = S.mix(S.C_ALERT, "#FFFFFF", 0.93)
        fg = S.C_ALERT
        border = S.mix(S.C_ALERT, "#FFFFFF", 0.70)
    else:
        fill, fg, border = S.C_SURFACE, S.C_TEXT, S.C_BORDER

    img = S.aa_rounded_image(Wk, Hk, r, fill, border, max(1, k), 4)
    d = ImageDraw.Draw(img)
    f = _font(int(font_size * k * 1.25), bold=(weight == "bold"))
    # 按「文字宽带 = tkfont 测量值 × k」定位，居中更准
    tw = d.textlength(text, font=f)
    d.text(((Wk - tw) / 2, Hk / 2 - f.size * 0.62), text, font=f, fill=fg)
    return img, Wk


def _wrap(draw, text, font, max_w):
    """按像素宽度手动折行（中文逐字断行）。"""
    lines, cur = [], ""
    for ch in text:
        if draw.textlength(cur + ch, font=font) <= max_w:
            cur += ch
        else:
            lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    return "\n".join(lines)


def shots(app, root):
    # =====================================================================
    # 1. 悬浮条 · 展开态（离屏超采样渲染）
    # =====================================================================
    st = app.strip
    app.mode = "sit"
    app.running = True
    app.paused = False
    app.alerting = False
    app.remaining = 44 * 60 + 59
    st.side = "right"
    st.collapsed = False
    st._render_key = None

    card = _card_preview(st, app, ss=3)
    pad = 22
    sheet = Image.new("RGBA", (card.width + pad * 2, card.height + pad * 2),
                      BG + (255,))
    sheet.alpha_composite(card, (pad, pad))
    sheet.convert("RGB").save(os.path.join(OUT, "strip-expanded.png"))
    print("saved %-22s %s" % ("strip-expanded.png", sheet.size))

    # =====================================================================
    # 2. 悬浮条 · 收起态（三档剩余时间）
    #
    # 这张不抓窗口，直接用 Pillow 复现 _render_edge 的贴图。
    # 原因：收起条本身只是 aa_rounded_image() 画出来的一张位图，
    # 而 PrintWindow 会把圆角外的透明键色渲染成黑色，抠色时抗锯齿边缘
    # 会被误判 → 圆角变成锯齿尖角。直接离屏渲染拿到的就是原始位图，
    # 圆角平滑、像素级还原。
    # =====================================================================
    ew, eh = st.EDGE_W, st.EDGE_H
    radius = max(2, int(ew * 0.42))
    tiles = []
    for frac in (0.80, 0.45, 0.12):
        tiles.append(_bar_tile(frac, ew, eh, radius, S.C_SIT))
        print("  collapsed frac=%.2f (offscreen Pillow)" % frac)

    # 拼成一张对照图（用 alpha_composite，圆角外的透明区域自然露出底色）
    Z = 6
    gap = 64
    labels = ("剩余 80%", "剩余 45%", "剩余 12%")
    tw = [t.width * Z for t in tiles]
    W = sum(tw) + gap * (len(tiles) - 1) + 80 * 2
    H = tiles[0].height * Z + 40 * 2 + 56
    # 底色比白条略深，白条轮廓才看得清（纯白底上白条几乎"消失"）
    sheet = Image.new("RGBA", (W, H), SHEET_BG + (255,))
    ox = 80
    for t in tiles:
        big = t.resize((t.width * Z, t.height * Z), Image.LANCZOS)
        sheet.alpha_composite(big, (ox, 40))
        ox += big.width + gap
    sheet = sheet.convert("RGB")
    d = ImageDraw.Draw(sheet)
    f_lab = _font(22)
    ox = 80
    for t, lab in zip(tiles, labels):
        bw = t.width * Z
        lw = d.textlength(lab, font=f_lab)
        d.text((ox + (bw - lw) / 2, H - 48), lab, font=f_lab,
               fill=(120, 128, 142))
        ox += bw + gap
    sheet.save(os.path.join(OUT, "strip-collapsed.png"))
    print("saved %-22s %s" % ("strip-collapsed.png", sheet.size))

    # =====================================================================
    # 3. 到点提醒窗（抓真实窗口 + 放大）
    #
    # ⚠️ 这里**不再离屏重画**。
    #
    # 曾经用 Pillow 复现 `AlertWindow._render` 的布局，但提醒窗的元素密度远高于
    # 悬浮条：标题 + 副标题 + 关闭叉 + 三个不同 min_width 的按钮挤在 356×150 里，
    # 任何一处字号/行高/间距的换算偏差都会让整张图的比例失真
    # （实测出现过「按钮高得离谱、正文与按钮之间空出一大块」的丑图）。
    #
    # 改为直接 PrintWindow 抓真实窗口再放大 —— 抓到的就是屏幕上那个样子，
    # 布局零偏差。代价是圆角边缘略有一点台阶（窗口内贴图经 flatten_to_key
    # 压平过，没有过渡像素），4 倍放大下几乎看不出来，远好于比例失真。
    # =====================================================================
    app.mode = "sit"
    aw = app.alert_win
    aw.show("该站起来了", "您已连续坐姿 45 分钟，起身活动 8 分钟。",
            "我已站起", S.C_STAND, "up")
    pump(root, 0.6)
    alert_w = aw.top
    alert_w.update_idletasks()
    aww, awh = alert_w.winfo_width(), alert_w.winfo_height()
    alert_w.geometry("+%d+%d" % (OFF, OFF))
    pump(root, 0.5)
    ret, im = grab_window(hwnd_of(alert_w), aww, awh)
    print("  alert PrintWindow ret =", ret, aww, awh)
    _save(im, "alert.png", zoom=4)
    try:
        aw.hide()
    except Exception:
        pass

    # =====================================================================
    # 4. 设置窗口
    # =====================================================================
    app.open_settings()
    pump(root, 0.5)
    w = app.settings_win.win
    w.update_idletasks()
    ww, wh = w.winfo_width(), w.winfo_height()
    w.geometry("+%d+%d" % (OFF, OFF))
    pump(root, 0.4)
    ret, im = grab_window(hwnd_of(w), ww, wh)
    print("  settings PrintWindow ret =", ret, ww, wh)
    _save(im, "settings.png", zoom=1)
    app.settings_win.close()

    # =====================================================================
    # 5. 使用说明窗口
    # =====================================================================
    app.open_help()
    pump(root, 0.5)
    hw = app.help_win.win
    hw.update_idletasks()
    hww, hwh = hw.winfo_width(), hw.winfo_height()
    hw.geometry("+%d+%d" % (OFF, OFF))
    pump(root, 0.4)
    ret, im = grab_window(hwnd_of(hw), hww, hwh)
    print("  help PrintWindow ret =", ret, hww, hwh)
    _save(im, "help.png", zoom=1)
    try:
        app.help_win.close()
    except Exception:
        pass


if __name__ == "__main__":
    main()
    print("done")
