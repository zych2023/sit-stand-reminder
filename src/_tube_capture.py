# -*- coding: utf-8 -*-
"""抓**真实运行的收起态细条**（PrintWindow，零打扰）。

把窗口 map 到屏幕外 (-8000,-8000)，不闪屏、不出声、不碰桌面；
再用 PrintWindow(hwnd, hdc, PW_RENDERFULLCONTENT=2) 读窗口自身内容。

产出：previews/_tube_real.png（多个进度档并排）+ _tube_real_one.png（单档放大）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ctypes
from ctypes import wintypes

from PIL import Image, ImageDraw, ImageFont
import sitstand as S
import _make_previews as MP

user32 = ctypes.windll.user32


def font(n, bold=False):
    for p in ((r"C:\Windows\Fonts\msyhbd.ttc" if bold
               else r"C:\Windows\Fonts\msyh.ttc"),
              r"C:\Windows\Fonts\msyh.ttc"):
        try:
            return ImageFont.truetype(p, n)
        except Exception:
            continue
    return ImageFont.load_default()


def main():
    out = os.path.abspath(os.path.join(os.path.dirname(__file__), "..",
                                       "previews"))
    app = S.App()
    try:
        strip = app.strip
        # 关键：让细条处于"收起态"（否则画的是展开卡片）
        strip.collapsed = True
        strip._expanded = False
        strip._drag = None

        shots = []
        levels = [0.20, 0.50, 0.72, 0.92, 1.00]
        for lv in levels:
            # 直接把 remaining 设成对应进度（total * (1-frac)）
            total = app._duration(app.mode)
            app.remaining = max(0.0, total * (1.0 - lv))
            strip.show()
            strip.refresh()
            MP.pump(app.root, 0.25)
            hwnd = MP.hwnd_of(strip.top)
            w = strip.EDGE_W
            h = strip.EDGE_H
            ret, img = MP.grab_window(hwnd, w, h)
            print("lv=%.2f hwnd=%s ret=%s size=%s" % (lv, hwnd, ret, img.size))
            shots.append((lv, strip.EDGE_W, strip.EDGE_H, img))

        # 并排输出（放大 6 倍）
        Z = 6
        tiles = []
        for lv, w, h, img in shots:
            t = img.resize((w * Z, h * Z), Image.NEAREST)
            tiles.append((lv, t))

        gp, px_, py_ = 60, 70, 60
        cw = tiles[0][1].width
        W = px_ * 2 + cw * len(tiles) + gp * (len(tiles) - 1)
        H = py_ + tiles[0][1].height + 70
        # 深色底，方便看出"管腔是否真透明"
        sheet = Image.new("RGB", (W, H), (44, 48, 56))
        d = ImageDraw.Draw(sheet)
        ox = px_
        f_c = font(24, True)
        for lv, t in tiles:
            sheet.paste(t, (ox, py_), t)
            lab = "%d%%" % round(lv * 100)
            tw = d.textlength(lab, font=f_c)
            d.text((ox + (cw - tw) / 2, py_ + t.height + 14), lab,
                   font=f_c, fill=(220, 224, 232))
            ox += cw + gp
        sheet.save(os.path.join(out, "_tube_real.png"))
        print("saved _tube_real.png", sheet.size)

    finally:
        try:
            app.icon.stop()
        except Exception:
            pass
        try:
            app.root.destroy()
        except Exception:
            pass


if __name__ == "__main__":
    main()
