# -*- coding: utf-8 -*-
"""离屏生成「悬浮条」相关的预览图 —— 完全不显示任何窗口、不出声。

为什么不在桌面上截图：截图依赖真实桌面（会闪窗、会被别的窗口遮挡、受 DPI 影响），
而悬浮条本身就是我们用 Pillow 画好再贴进画布的，所以直接复现那几张贴图即可，
像素级一致、而且对用户零打扰。

产出：
    预览/收起窄条.png   三档进度（剩余 80% / 45% / 12%）的收起细条
    预览/悬浮细条.png   展开态卡片（按控件配色手绘一份等价的示意）
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import sitstand as S
from PIL import Image, ImageDraw, ImageFont

OUT = os.path.join(os.path.dirname(HERE), "预览")
os.makedirs(OUT, exist_ok=True)

S.enable_dpi_awareness()

BG = (238, 240, 244)          # 预览图底色（跟 C_PAGE 接近）


def _font(size):
    candidates = [
        os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "msyh.ttc"),
        os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "msyhbd.ttc"),
        os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "simhei.ttf"),
        os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "simsun.ttc"),
        "msyh.ttc", "simhei.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def bar_tile(frac, ew, eh, radius, gb):
    """复现 _render_edge：白底圆角条 + 从下往上的进度填充。"""
    base = S.aa_rounded_image(ew, eh, radius, "#FFFFFF", S.C_BORDER, 1, 4,
                              opaque_bg="#FFFFFF")
    fh = int(eh * frac)
    if fh >= 2:
        full = S.aa_rounded_image(ew, eh, radius, gb, None, 0, 4,
                                  opaque_bg="#FFFFFF")
        cut = Image.new("RGBA", (ew, eh), (0, 0, 0, 0))
        cut.paste(full.crop((0, eh - fh, ew, eh)), (0, eh - fh))
        base.alpha_composite(cut)
    return base


def strip_previews():
    ew = S.px(S.StripWindow.EDGE_W)
    eh = S.px(S.StripWindow.EDGE_H)
    radius = max(2, int(ew * 0.42))
    print("收起条物理尺寸 %dx%d，圆角半径 %d" % (ew, eh, radius))

    # ---------- 收起窄条：三档进度 ----------
    Z = 7
    gap = 46
    tiles = [bar_tile(f, ew, eh, radius, S.C_SIT) for f in (0.80, 0.45, 0.12)]
    W = len(tiles) * ew * Z + gap * (len(tiles) - 1) + 44 * 2
    H = eh * Z + 48 * 2
    sheet = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(sheet)
    f_label = _font(15)
    ox = 44
    labels = ("剩余 80%", "剩余 45%", "剩余 12%")
    for t, lab in zip(tiles, labels):
        big = t.resize((ew * Z, eh * Z), Image.NEAREST)
        sheet.paste(big, (ox, 48), big)
        tw = d.textlength(lab, font=f_label)
        d.text((ox + (ew * Z - tw) / 2, H - 40), lab, font=f_label,
               fill=(86, 94, 108))
        ox += ew * Z + gap
    sheet.save(os.path.join(OUT, "收起窄条.png"))
    print("saved 收起窄条.png", sheet.size)


def card_preview():
    """展开态卡片：优先用**真实渲染**抓下来的图（_real_card.png）。

    手绘等价图容易和真实布局对不上（按钮/文字错位），所以这里优先用真图；
    拿不到真图时再退回手绘，保证脚本永远能跑通。
    """
    real = os.path.join(os.path.dirname(HERE), "_real_card.png")
    cw, ch = S.px(S.StripWindow.CARD_W), S.px(S.StripWindow.CARD_H)
    pad = 22
    Z2 = 2
    if os.path.exists(real):
        card = Image.open(real).convert("RGBA")
        if card.size != (cw, ch):
            card = card.resize((cw, ch), Image.LANCZOS)
        print("使用真实渲染的卡片 %dx%d" % card.size)
    else:
        card = _handmade_card(cw, ch)
    sheet = Image.new("RGB", (cw * Z2 + pad * 2, ch * Z2 + pad * 2), BG)
    big = card.resize((cw * Z2, ch * Z2), Image.LANCZOS)
    sheet.paste(big, (pad, pad), big)
    sheet.save(os.path.join(OUT, "悬浮细条.png"))
    print("saved 悬浮细条.png", sheet.size)


def _handmade_card(cw, ch):
    """兜底：手绘一份卡片（布局和 _render_card 对齐）。"""
    card = S.aa_rounded_image(cw, ch, ch / 2, S.C_BG, S.C_BORDER, 1, 4,
                              opaque_bg=S.C_BG)
    cd = ImageDraw.Draw(card)
    f_time = _font(23)
    cy = ch / 2
    cap = ch / 2 + S.px(5)
    dot_r = S.px(11)
    for i, glyph in enumerate(("pause", "dots", "close")):
        bx = cap + S.px(2) + S.px(31) * i
        cd.ellipse([bx - dot_r, cy - dot_r, bx + dot_r, cy + dot_r],
                   fill=S.C_SURFACE, outline=S.C_BORDER, width=1)
        if glyph == "pause":
            cd.line([bx - S.px(3), cy - S.px(4), bx - S.px(3), cy + S.px(4)],
                    fill=S.C_TEXT2, width=2)
            cd.line([bx + S.px(1), cy - S.px(4), bx + S.px(1), cy + S.px(4)],
                    fill=S.C_TEXT2, width=2)
        elif glyph == "dots":
            for dx in (-S.px(4), 0, S.px(4)):
                cd.ellipse([bx + dx - 1.6, cy - 1.6, bx + dx + 1.6, cy + 1.6],
                           fill=S.C_TEXT2)
        else:
            a = S.px(4)
            cd.line([bx - a, cy - a, bx + a, cy + a], fill=S.C_TEXT2, width=2)
            cd.line([bx - a, cy + a, bx + a, cy - a], fill=S.C_TEXT2, width=2)
    bar_l, bar_r = S.px(100) + S.px(15), cw - S.px(112)
    bh = S.px(4)
    cd.rounded_rectangle([bar_l, cy - bh / 2, bar_r, cy + bh / 2],
                         radius=bh / 2, fill=S.C_TRACK)
    cd.rounded_rectangle([bar_l, cy - bh / 2, bar_l + (bar_r - bar_l) * 0.62,
                          cy + bh / 2], radius=bh / 2, fill=S.C_SIT)
    dr = S.px(3.5)
    dot_cx = cw - cap
    cd.ellipse([dot_cx - dr, cy - dr, dot_cx + dr, cy + dr], fill=S.C_SIT)
    txt = "坐 44:59"
    tw = cd.textlength(txt, font=f_time)
    cd.text((dot_cx - S.px(14) - tw, cy - f_time.size / 2 - 2), txt,
            font=f_time, fill=S.C_TEXT)
    return card


if __name__ == "__main__":
    strip_previews()
    card_preview()
