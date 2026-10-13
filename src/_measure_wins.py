# -*- coding: utf-8 -*-
"""零打扰测量各窗口在 100% DPI 下的真实尺寸（供 README 图片宽度换算）。

窗口 map 到 (-8000,-8000)，不闪屏；不出声；不写 settings.json。
不调用 set_ui_scale → SCALE 保持 1.0 → px() 是恒等映射，
于是 winfo_width() 出来的就是"设计像素"。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

OFF = -8000

import sitstand as S


def pump(root, dt):
    t0 = time.time()
    while time.time() - t0 < dt:
        root.update()
        time.sleep(0.01)


def main():
    S.play_soft_chime = lambda *a, **k: None
    try:
        import winsound
        winsound.PlaySound = lambda *a, **k: None
        winsound.Beep = lambda *a, **k: None
        winsound.MessageBeep = lambda *a, **k: None
    except Exception:
        pass

    app = S.App()
    root = app.root
    root.update()

    try:
        # --- 悬浮条 ---
        st = app.strip
        app.mode = "sit"
        app.running = True
        app.remaining = 44 * 60 + 59
        st.show()
        pump(root, 0.5)
        st.collapsed = False
        st._render()
        pump(root, 0.4)
        print("strip expanded window :", st.top.winfo_width(), "x",
              st.top.winfo_height())
        print("  CARD_W x CARD_H     :", st.CARD_W, "x", st.CARD_H,
              " PAD =", st.PAD)
        print("  EDGE_W x EDGE_H     :", st.EDGE_W, "x", st.EDGE_H)
        print("  SCALE               :", S.SCALE)

        # --- 提醒窗 ---
        app.alert_win.show("该站起来了", "已经坐了 45 分钟，起来活动一下",
                           "我已站起", S.C_ALERT, "up")
        pump(root, 0.5)
        print("alert window          :", app.alert_win.top.winfo_width(), "x",
              app.alert_win.top.winfo_height())
        print("  declared W x H      :", app.alert_win.W, "x", app.alert_win.H,
              " PAD =", app.alert_win.PAD)
        app.alert_win.hide()

        # --- 设置窗 ---
        app.open_settings()
        pump(root, 0.6)
        w = app.settings_win.win
        w.update_idletasks()
        print("settings window       :", w.winfo_width(), "x", w.winfo_height())
        app.settings_win.close()

        # --- 帮助窗 ---
        app.open_help()
        pump(root, 0.6)
        hw = app.help_win.win
        hw.update_idletasks()
        print("help window           :", hw.winfo_width(), "x", hw.winfo_height())
        app.help_win.close()
    finally:
        for obj, meth in ((app.strip, "destroy"), (app.alert_win, "destroy"),
                          (app.settings_win, "close"), (app.help_win, "close")):
            try:
                getattr(obj, meth)()
            except Exception:
                pass
        try:
            app.icon.stop()
        except Exception:
            pass
        try:
            root.destroy()
        except Exception:
            pass


if __name__ == "__main__":
    main()
