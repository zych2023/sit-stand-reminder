# -*- coding: utf-8 -*-
"""开发用截图脚本：把各个界面截出来放进 ../预览。不属于交付内容。

用法： direct 运行即可。所有截图都用「全屏抓取 + 按窗口矩形裁剪」，
因为 DPI 缩放下 winfo_rootx/y 是逻辑像素、ImageGrab 是物理像素，
直接 grab(bbox=...) 会错位甚至全黑。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import sitstand as S
from PIL import Image as I, ImageGrab

OUT = os.path.join(os.path.dirname(HERE), "预览")
os.makedirs(OUT, exist_ok=True)

S.enable_dpi_awareness()

# ---- 截图期护栏：绝不出声（截图虽然要真窗口，但一点声音都不要有）----
S.play_soft_chime = lambda *a, **k: None
try:
    import winsound
    winsound.PlaySound = lambda *a, **k: None
    winsound.Beep = lambda *a, **k: None
    winsound.MessageBeep = lambda *a, **k: None
except Exception:
    pass

app = S.App()
app.root.update()

_PUMP = [False]


def pump(dt):
    if _PUMP[0]:
        return
    _PUMP[0] = True
    try:
        t0 = time.time()
        while time.time() - t0 < dt:
            try:
                app.root.update()
            except Exception:
                pass
            time.sleep(0.006)
    finally:
        _PUMP[0] = False


def grab_box(x, y, w, h, name, pad=18, scale=1.6):
    pump(0.45)
    full = ImageGrab.grab()
    bx0 = max(0, int(x - pad))
    by0 = max(0, int(y - pad))
    bx1 = min(full.width, int(x + w + pad))
    by1 = min(full.height, int(y + h + pad))
    img = full.crop((bx0, by0, bx1, by1))
    if scale != 1.0:
        img = img.resize((int(img.width * scale), int(img.height * scale)),
                         I.LANCZOS)
    img.save(os.path.join(OUT, name))
    print("saved", name, img.size)


def shots():
    sw, sh = app.strip._screen()

    # ---------- 1. 悬浮条：展开态 ----------
    app.mode = "sit"
    app.remaining = 44 * 60 + 59
    st = app.strip
    st.show()
    st.collapsed = False
    st.side = "right"
    st.pos_y = int(sh * 0.30)
    st._apply_geometry()
    pump(0.4)
    x, y = st._win_xy()
    grab_box(x, y, st._cur_w, st._cur_h, "悬浮细条.png", pad=22, scale=1.8)

    # ---------- 2. 悬浮条：收起态细长圆角矩形（三档进度）----------
    # 注意：收起态现在是**完整停在屏幕内**的，所以按窗口实际位置裁剪即可。
    tiles = []
    for frac in (0.20, 0.55, 0.88):
        total = app._duration(app.mode)
        app.remaining = total * (1 - frac)
        st.collapsed = True
        st.side = "right"
        st.pos_y = 320
        st._apply_geometry()
        st._render_key = None
        st._render()
        pump(0.3)
        x, y = st._win_xy()
        # 只裁画布中间那根圆角矩形所在的区域
        # 收起态窗口已经缩到内容大小，所以居中定位要用**窗口当前实际尺寸**
        cw, chh = st.EDGE_W, st.EDGE_H
        win_w = getattr(st, "_cur_w", st.EDGE_W + st.PAD * 2)
        win_h = getattr(st, "_cur_h", st.EDGE_H + st.PAD * 2)
        bx0 = int(x + win_w / 2 - cw / 2) - 12
        by0 = int(y + win_h / 2 - chh / 2) - 12
        img = ImageGrab.grab(bbox=(bx0, by0, bx0 + cw + 24, by0 + chh + 24))
        img = img.resize((img.width * 3, img.height * 3), I.NEAREST)
        tiles.append(img)
    W = sum(t.width for t in tiles) + 40 * (len(tiles) - 1)
    H = max(t.height for t in tiles)
    sheet = I.new("RGB", (W, H), (238, 240, 244))
    ox = 0
    for t in tiles:
        sheet.paste(t, (ox, 0))
        ox += t.width + 40
    sheet.save(os.path.join(OUT, "收起窄条.png"))
    print("saved 收起窄条.png", sheet.size, "（左→右：剩余 80% / 45% / 12%）")

    # ---------- 3. 设置窗口 ----------
    app.open_settings()
    pump(0.5)
    w = app.settings_win.win
    w.update_idletasks()
    grab_box(w.winfo_rootx(), w.winfo_rooty(), w.winfo_width(), w.winfo_height(),
             "设置窗口.png", pad=6, scale=1.0)
    app.settings_win.close()

    # ---------- 4. 到点提醒窗 ----------
    app.mode = "sit"
    aw = app.alert_win
    aw.show("该站起来了", "您已连续坐姿 45 分钟，起身活动 8 分钟。",
            "我已站起", S.C_STAND, "up")
    pump(0.4)
    t = aw.top
    grab_box(t.winfo_rootx(), t.winfo_rooty(), aw.W, aw.H,
             "到点提醒窗.png", pad=20, scale=1.7)
    aw.hide()

    # ---------- 5. 使用说明 ----------
    app.open_help()
    pump(0.5)
    hw = app.help_win.win
    hw.update_idletasks()
    grab_box(hw.winfo_rootx(), hw.winfo_rooty(), hw.winfo_width(),
             hw.winfo_height(), "使用说明.png", pad=4, scale=1.0)


try:
    pump(0.4)
    shots()
except Exception:
    import traceback
    traceback.print_exc()
finally:
    try:
        app.strip.destroy()
    except Exception:
        pass
    try:
        app.root.destroy()
    except Exception:
        pass
    try:
        app.icon.stop()
    except Exception:
        pass
print("done")
