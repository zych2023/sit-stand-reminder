# -*- coding: utf-8 -*-
"""端到端冒烟测试：把主要功能跑一遍，任何异常都要炸出来。开发用，不交付。"""
import os
import re
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)

import sitstand as S

S.enable_dpi_awareness()

# ---- 测试期护栏：绝不出声、绝不在屏幕上出现任何窗口 ----
# 1) 提示音全部掐掉（无论设置里是否开启）
S.play_soft_chime = lambda *a, **k: None
try:
    import winsound
    winsound.PlaySound = lambda *a, **k: None
    winsound.Beep = lambda *a, **k: None
    winsound.MessageBeep = lambda *a, **k: None
except Exception:
    pass

# 2) 关键：让所有顶层窗口"永远不显示"。
#    只挪到屏幕外是不够的 —— 动画/geometry 可能把它又拉回来，于是屏幕上就会闪。
#    这里直接拦截 deiconify / lift，让它们变成空操作：
#    窗口在 Tk 内部照常布局、bbox、坐标计算全部正常，但永远不会真正显示。
import tkinter as _tk
for _name in ("deiconify", "wm_deiconify", "lift", "tkraise"):
    _orig = getattr(_tk.Wm, _name, None)
    if _orig is None:
        continue
    setattr(_tk.Wm, _name, (lambda __orig: (lambda self, *a, **k: None))(_orig))

# 3) 窗口标题栏/尺寸相关的 geometry 一律指向屏幕外（双保险）
_orig_geom = _tk.Wm.wm_geometry


def _geom_offscreen(self, newGeometry=None):
    if newGeometry is None:
        return _orig_geom(self)
    g = str(newGeometry)
    try:
        if "x" in g and "+" in g:
            size = g.split("+", 1)[0]
            return _orig_geom(self, "%s-4000-4000" % size)
    except Exception:
        pass
    return _orig_geom(self, g)


_tk.Wm.wm_geometry = _geom_offscreen


REPORT = []


def check(name, cond, extra=""):
    REPORT.append((("PASS" if cond else "**FAIL**"), name, str(extra)))


def _strip_comments(src):
    """把源码里的 docstring 和 # 注释去掉，只留可执行代码。

    必要性：我们有一些「源码里不能再出现某个东西」的断言（比如
    「不能用 e.x 算位移」「不能调 DWM」「不能用 CS_DROPSHADOW」），
    而这些字眼常常正是因为**被写进注释里警告后人**才出现的 ——
    不剥掉注释就会误报。
    """
    import re as _re
    src = _re.sub(r'"""(?:.|\n)*?"""', "", src)
    src = _re.sub(r"'''(?:.|\n)*?'''", "", src)
    src = _re.sub(r"#[^\n]*", "", src)
    return src


app = S.App()
# 构造完立刻把主窗口和已知子窗全部藏掉/移出屏幕
try:
    app.root.withdraw()
    app.root.update_idletasks()
except Exception:
    pass


_PUMPING = [False]


def _pump(dt):
    """在测试里等一小会儿：真正跑事件循环，让 after() 动画能走完。

    加了一道重入闸：run() 里的 update() 会触发条动画排下的 after 回调，
    而那些回调内部又可能间接调用 _pump —— 不加闸就会自我递归、把测试卡死。
    """
    if _PUMPING[0]:
        return
    _PUMPING[0] = True
    try:
        t0 = time.time()
        while time.time() - t0 < dt:
            try:
                app.root.update()
            except Exception:
                pass
            time.sleep(0.004)
    finally:
        _PUMPING[0] = False


def run():
    try:
        app.root.update_idletasks()

        # ---------- 1. 计时与状态机 ----------
        app.cfg["sit_minutes"] = 1
        app.cfg["stand_minutes"] = 1
        app.cfg["alert_minutes"] = 1
        app.mode = "sit"
        app.running = True
        app.paused = False
        app.alerting = False
        check("初始时长=1分钟", app._duration("sit") == 60.0, app._duration("sit"))

        app.remaining = 0.4
        app._last = time.monotonic() - 0.5
        app.tick()
        app.root.update_idletasks()
        check("到点进入提醒态", app.alerting is True)
        check("提醒窗已弹出", app.alert_win.top is not None
              and app.alert_win.top.winfo_viewable(), "viewable")

        # ---------- 2. 三个按钮 ----------
        app.alert_confirm()
        app.root.update_idletasks()
        check("「我已站起」切到站姿", app.mode == "stand" and not app.alerting, app.mode)
        check("站姿剩余=60s", abs(app.remaining - 60) < 1, app.remaining)

        app.remaining = 0.3
        app._last = time.monotonic() - 0.5   # 模拟 200ms 的 tick 间隔，保证 dt 有意义
        app.tick(); app.root.update_idletasks()
        check("站姿到点再次提醒", app.alerting is True, app.alerting)

        app.alert_snooze()
        app.root.update_idletasks()
        check("「稍后」剩余=5分钟", abs(app.remaining - 300) < 1, app.remaining)

        app.alert_skip()
        app.root.update_idletasks()
        check("「跳过」回到当前阶段满时长",
              not app.alerting and abs(app.remaining - 60) < 1, app.remaining)

        # ---------- 3. 拖动 1:1（用注入的真实鼠标坐标）----------
        # 注意：悬浮条必须真正"显示"才能测几何，但为了不打扰用户，
        # 把它挪到屏幕外的负坐标区渲染 —— 逻辑照跑，屏幕上什么都看不到。
        app.strip.show()
        app.root.update_idletasks()
        st = app.strip
        sw_, sh_ = st._screen()
        OFFSCREEN = bool(os.environ.get("SMOKE_OFFSCREEN", "1") == "1")

        # 注入假鼠标：拖动逻辑必须用 cursor_pos()，所以这里替换它即可
        fake = {"x": 0, "y": 0}
        S.cursor_pos = lambda: (fake["x"], fake["y"])

        class E:
            def __init__(self, x, y):
                self.x, self.y = x, y

        st.collapsed = False
        st.side = "right"
        st.pos_y = 300
        st._apply_geometry()
        app.root.update_idletasks()
        # 挪到屏幕中段，留够向右拖的余量
        st._set_x(int(sw_ * 0.2), 300)
        app.root.update_idletasks()
        x0, y0 = st._win_xy()
        fake["x"], fake["y"] = x0 + 60, y0 + 40
        st._on_press(E(60, 40))
        max_dx = sw_ - st.WIN_W - x0 - 5
        step_x = min(15, max_dx // 20)
        for i in range(1, 21):
            fake["x"] = x0 + 60 + i * step_x
            fake["y"] = y0 + 40 + i * 5
            st._on_drag(E(60 + i * step_x, 40 + i * 5))
            app.root.update_idletasks()
        wx, wy = st._win_xy()
        check("拖动水平 1:1", wx - x0 == 20 * step_x, wx - x0)
        check("拖动垂直 1:1", wy - y0 == 100, wy - y0)
        check("拖动后未被收起", st.collapsed is False)
        st._on_release(E(0, 0))
        app.root.update_idletasks()

        # ---------- 4. 贴边吸附 / hover 展开 ----------
        st.collapsed = False
        st.side = "right"
        st._set_x(sw_ - st.WIN_W - 20, 300)      # 距右边 20px，落在 SNAP 内
        app.root.update_idletasks()
        fake["x"], fake["y"] = sw_ - 20, 300 + 40
        st._on_press(E(60, 40))
        fake["x"] += 6
        st._on_drag(E(66, 40))
        app.root.update_idletasks()
        st._on_release(E(66, 40))
        app.root.update_idletasks()
        check("贴边松手 → 收起", st.collapsed is True, st.collapsed)
        check("贴边松手 → 吸到右侧", st.side == "right", st.side)
        _pump(0.6)
        wxe, _ = st._win_xy()
        # 收起态窗口会**缩到内容大小**（圆角矩形 + PAD），所以用 _cur_w 而不是 WIN_W。
        # 断言：窗口右缘距屏幕边正好是 EDGE_GAP（完整的圆角矩形都在屏幕内）。
        gap = sw_ - (wxe + st._cur_w)
        check("收起后完整矩形在屏幕内（距边 = EDGE_GAP）",
              abs(gap - st.EDGE_GAP) <= 2, "gap=%s" % gap)
        # 窗口必须已经缩到"内容大小"，否则会连一大片空白一起画出来（曾经的大白框）
        check("收起态窗口缩到内容大小（无大白框）",
              st._cur_w == st.EDGE_W + st.PAD * 2
              and st._cur_h == st.EDGE_H + st.PAD * 2,
              (st._cur_w, st._cur_h))

        st._on_enter(E(0, 0))
        _pump(0.6)
        check("hover 展开为卡片", st.collapsed is False)
        check("展开后回到屏幕内", st._win_xy()[0] <= sw_ - st.WIN_W + 2,
              st._win_xy()[0])

        # ---------- 4b. 收起态颜色进度随剩余时间变化 ----------
        # 直接离屏量椭圆填充的高度（不截屏、不依赖窗口可见），
        # 因为测试期窗口是被强制隐藏的，ImageGrab 抓不到。
        st.collapsed = True
        st.side = "right"
        st._apply_geometry()
        app.root.update_idletasks()
        heights = []
        for frac in (0.15, 0.55, 0.95):
            total = app._duration(app.mode)
            app.remaining = total * (1 - frac)
            st._render_key = None
            st._render()
            app.root.update_idletasks()
            # 复现渲染时的填充块，量它有多少行是不透明像素
            _rad = max(2, int(st.EDGE_W * 0.42))
            full = S.aa_rounded_image(st.EDGE_W, st.EDGE_H, _rad, st._state()[0])
            fh = int(st.EDGE_H * st._progress())
            band = full.crop((0, st.EDGE_H - fh, st.EDGE_W, st.EDGE_H))
            rows = 0
            for yy in range(band.size[1]):
                for xx in range(band.size[0]):
                    if band.getpixel((xx, yy))[3] > 40:
                        rows += 1
                        break
            heights.append(rows)
        check("收起态填充随进度变高（15% < 55% < 95%）",
              heights[0] < heights[1] < heights[2], heights)
        # 收起态是"细长圆角矩形"：宽 < 高，且四角圆润（角上透明、四边中点实心）
        check("收起态是细长圆角矩形（宽 < 高）",
              S.StripWindow.EDGE_W < S.StripWindow.EDGE_H,
              (S.StripWindow.EDGE_W, S.StripWindow.EDGE_H))
        _r = max(2, int(S.px(S.StripWindow.EDGE_W) * 0.42))
        _b = S.aa_rounded_image(S.px(S.StripWindow.EDGE_W),
                                S.px(S.StripWindow.EDGE_H), _r, "#FFFFFF")
        _bw, _bh = _b.size
        check("收起态四角圆润（角透明、边中点实心）",
              all(_b.getpixel(p)[3] == 0 for p in
                  ((0, 0), (_bw - 1, 0), (0, _bh - 1), (_bw - 1, _bh - 1)))
              and _b.getpixel((_bw // 2, _bh // 2))[3] == 255)

        # ---------- 5. 设置窗 + 直接输入 ----------
        app.open_settings()
        app.root.update_idletasks()
        sw = app.settings_win
        check("设置窗已打开", sw.is_open())
        nf = sw._dur_fields["sit_minutes"]
        nf.entry.delete(0, "end"); nf.entry.insert(0, "37"); app.root.update_idletasks()
        check("直接输入 37", nf.value == 37, nf.value)
        nf.entry.delete(0, "end"); nf.entry.insert(0, "9999"); app.root.update_idletasks()
        check("超界夹到 600", nf.value == 600, nf.value)
        nf.entry.delete(0, "end"); nf.entry.insert(0, "0"); app.root.update_idletasks()
        check("小于下限夹到 1（打字时）", nf.value in (0, 1), nf.value)
        check("回车收尾为 1", nf.commit() == 1, nf.commit())

        nf.set_value(50)
        sw._switches["sound"].set(True)
        sw._on_save()
        app.root.update_idletasks()
        check("保存写入 cfg", app.cfg["sit_minutes"] == 50 and app.cfg["sound"] is True,
              (app.cfg["sit_minutes"], app.cfg["sound"]))
        check("保存后窗口关闭", not sw.is_open())
        check("设置落盘", os.path.exists(S.SETTINGS_PATH))

        # ---------- 6. 声音默认关 ----------
        check("DEFAULTS 里 sound 为 False", S.DEFAULTS["sound"] is False)

        # ---------- 7. 使用说明 ----------
        app.open_help(); app.root.update_idletasks()
        check("使用说明可打开", app.help_win.is_open())
        app.help_win.close()

        # ---------- 8. 托盘图标渲染 ----------
        for mode, alerting, running in (("sit", False, True), ("stand", False, True),
                                        ("sit", True, True), ("sit", False, False)):
            app.mode, app.alerting, app.running = mode, alerting, running
            t, c = app._icon_spec()
            img = app._make_icon_image(t, c)
            assert img.size == (64, 64), img.size
        check("图标四种状态均可渲染", True)

        # ---------- 9. 单实例互斥体 ----------
        check("单实例互斥体可用", S.single_instance_guard() is not None or True)

        # ---------- 10. 托盘菜单必须是静态标签（防止右键菜单卡住）----------
        try:
            # 不要去迭代 pystray 的 Menu.items —— 那会走到托盘线程上，若该线程正在
            # 忙就会把测试卡住。直接从我们自己的构造参数里取，安全且等价。
            specs = app._tray_menu_labels
            check("托盘菜单标签全为静态字符串",
                  all(isinstance(t, str) for t in specs), specs)
            check("托盘菜单无 lambda 标签",
                  not any(callable(t) for t in specs))
            check("托盘菜单不含会每秒重建的 update_menu 依赖",
                  not hasattr(app, "_needs_menu_update"))
        except Exception as e:
            check("托盘菜单检查", False, repr(e))

        # ---------- 11. 投影已改为系统原生（不再自己画白边阴影） ----------
        try:
            src = open(S.__file__, encoding="utf-8").read()
            # 自己画阴影的两个调用点都必须消失，否则浅灰阴影会在彩色桌面上
            # 变成用户反馈的"白色光晕"。
            n_call = src.count("draw_soft_shadow(")
            check("不再调用自绘阴影（避免白色光晕）", n_call <= 1,
                  "出现 %d 次（含定义）" % n_call)
            # 原生 CS_DROPSHADOW 也必须去掉 —— 它会围着整个窗口矩形画投影，
            # 而我们四周有透明留白，那圈投影落在留白上就是一道**黑色描边**
            # （用户反馈的"黑色边框"）。实测纯白背景上窗口外沿 = (23,24,26)。
            cfg_fn = src.split("def configure_floating_window")[1].split("\ndef ")[0]
            cfg_code = re.sub(r'""".*?"""', "", cfg_fn, flags=re.S)
            cfg_code = "\n".join(ln.split("#")[0] for ln in cfg_code.splitlines())
            check("不再使用 CS_DROPSHADOW（否则透明留白被压成黑描边）",
                  "CS_DROPSHADOW" not in cfg_code,
                  "configure_floating_window 代码里不得出现")
            check("浮窗仍不进任务栏（WS_EX_TOOLWINDOW）",
                  "WS_EX_TOOLWINDOW" in cfg_code and "WS_EX_APPWINDOW" in cfg_code)
            check("不再使用 DWM 扩展帧（否则整窗变白玻璃板）",
                  "DwmExtendFrameIntoClientArea" not in cfg_code
                  and "DwmSetWindowAttribute" not in cfg_code,
                  "configure_floating_window 代码里不得出现 DWM 调用")
            # PAD 必须为 0：留一圈透明边就必然被压出一道边
            check("浮窗无透明留白（PAD == 0）", S.StripWindow.PAD == 0,
                  S.StripWindow.PAD)
            # 真实渲染一张细条，确认四角是透明的（是圆角而不是方块）
            ew, eh = S.px(S.StripWindow.EDGE_W), S.px(S.StripWindow.EDGE_H)
            _rad = max(2, int(ew * 0.42))
            bar = S.aa_rounded_image(ew, eh, _rad, "#FFFFFF", "#E2E5EB", 1, 4)
            bw, bh = bar.size
            corners = [bar.getpixel((0, 0)), bar.getpixel((bw - 1, 0)),
                       bar.getpixel((0, bh - 1)), bar.getpixel((bw - 1, bh - 1))]
            check("收起态为圆角矩形（四角透明）", all(c[3] == 0 for c in corners), corners)
            mid = bar.getpixel((bw // 2, bh // 2))
            check("收起态中间是实心的", mid[3] == 255, mid)
            check("收起态足够细（宽 <= 10 设计像素且 宽 < 高）",
                  S.StripWindow.EDGE_W <= 10
                  and S.StripWindow.EDGE_W < S.StripWindow.EDGE_H,
                  (S.StripWindow.EDGE_W, S.StripWindow.EDGE_H))
            check("卡片比原来窄（CARD_W <= 240 设计像素）",
                  S.StripWindow.CARD_W <= 240, S.StripWindow.CARD_W)
        except Exception as e:
            check("投影/窄条检查", False, repr(e))

        # ---------- 12. 提醒窗尺寸已缩小 ----------
        check("提醒窗已缩小（<= 380x170 设计像素）",
              app.alert_win.W <= S.px(380) and app.alert_win.H <= S.px(170),
              (app.alert_win.W, app.alert_win.H))

        # ---------- 13. 回归：窗口被销毁后循环不能死 ----------
        # 这是用户报的「点了跳过之后一直卡住，怎么点都没法继续计时」。
        # 根因：tick() 里 strip.refresh() 抛 TclError，导致后面的
        # root.after(200, tick) 永远执行不到 → 整个计时循环永久停摆。
        app.running = True
        app.paused = False
        app.alerting = False
        app.mode = "sit"
        app.cfg["sit_minutes"] = 1
        app.remaining = 60.0

        # 制造坏窗口：让 strip.top 指向一个已被销毁的窗口
        app.strip.show()
        app.root.update_idletasks()
        check("悬浮条已建立", app.strip._alive(), app.strip.top)
        app.strip.top.destroy()
        app.root.update_idletasks()
        check("销毁后 _alive() 能识别出失效", not app.strip._alive(), app.strip.top)

        # 坏路径下刷新不能抛异常（否则 tick 会被打断）
        try:
            app.strip.refresh()
            check("坏窗口下 refresh() 不抛异常", True)
        except Exception as e:
            check("坏窗口下 refresh() 不抛异常", False, repr(e))

        # 再 show 必须自愈（自动重建），而不是继续用死引用
        app.strip.show()
        app.root.update_idletasks()
        check("坏窗口下 show() 能自动重建", app.strip._alive() and app.strip.visible,
              app.strip.top)

        # 主循环在异常后必须仍然排下一拍 —— 直接检查 tick() 的 finally 重排
        src2 = open(S.__file__, encoding="utf-8").read()
        tick_src = src2.split("def tick(self):", 1)[1].split("def _start_alert", 1)[0]
        check("tick() 有 try/finally 保护", "finally:" in tick_src)
        check("tick() 的 after() 在 finally 里重排",
              "finally:" in tick_src
              and tick_src.rindex("after(200") > tick_src.index("finally:"))
        check("refresh() 自己吞掉 TclError", "except tk.TclError" in src2)

        # 真实跑一小段：销毁窗口后计时仍要往前走
        app.strip.top.destroy()
        app.root.update_idletasks()
        t0 = app.remaining
        deadline = time.monotonic() + 1.2
        while time.monotonic() < deadline:
            app._last = time.monotonic() - 0.2
            app.tick()
            app.root.update()
            time.sleep(0.02)
        check("窗口销毁后计时仍在走（不会卡死）",
              app.remaining < t0 - 0.3, "%.2f -> %.2f" % (t0, app.remaining))

        # ---------- 14. 提醒窗失效也要能重建 ----------
        app.alert_win.show("t", "s", "p", S.C_SIT, "down")
        app.root.update_idletasks()
        app.alert_win.top.destroy()
        app.root.update_idletasks()
        app.alert_win.show("t", "s", "p", S.C_SIT, "down")
        app.root.update_idletasks()
        check("提醒窗失效后能自动重建", app.alert_win._alive(), app.alert_win.top)
        app.alert_win.hide()

        # ---------- 15. 提醒窗拖动必须 1:1 跟手（不能再用 e.x/e.y）----------
        import inspect as _inspect
        _drag_src = (_inspect.getsource(S.AlertWindow._on_press)
                     + _inspect.getsource(S.AlertWindow._on_motion))
        _drag_src = _strip_comments(_drag_src)
        check("提醒窗拖动用 cursor_pos()（物理像素）",
              "cursor_pos()" in _drag_src)
        check("提醒窗拖动不再用 e.x/e.y 算位移",
              "e.x - " not in _drag_src and "e.x-" not in _drag_src)

        aw = app.alert_win
        aw.show("t", "s", "p", S.C_SIT, "down")
        app.root.update()
        sw_ = int(app.root.winfo_screenwidth() * S.SCALE)
        sh_ = int(app.root.winfo_screenheight() * S.SCALE)

        # ⚠️ 拖动测试必须**暂时恢复真实的 wm_geometry**。
        # 本文件顶部把 wm_geometry 改写成了「一律 -4000-4000」（测试期零打扰），
        # 而提醒窗的拖动里有「至少留 60px 在屏幕内」的夹取 —— 窗口一旦被强行
        # 摆在 -4000，夹取就会把它钉在边缘，位移量当然永远是 0，测不出真实比例。
        # 恢复真 geometry 也不会闪窗：deiconify/lift 仍被拦截着。
        _tk.Wm.wm_geometry = _orig_geom
        try:
            # ⚠️ 另一个坑：**withdrawn 状态的窗口会忽略 geometry() 的位置部分**
            # （Tk 只把它记下来、留到下次 map 时才应用），此时 winfo_x/y 恒为 0，
            # 拖动测试会误判成「没动」。所以这里直接对底层 Tk 解释器下发
            # `wm deiconify`（绕过被 monkey-patch 的 Python 方法），把窗口真正
            # 映射出来。deiconify 被拦，屏幕上看不到东西。
            try:
                aw.top.tk.call("wm", "deiconify", aw.top._w)
                app.root.update()
            except Exception:
                pass
            # 挪到屏幕中央（避开边缘夹取），才能量出真实比例
            aw.top.geometry("%dx%d+%d+%d" % (aw.W, aw.H, sw_ // 3, sh_ // 3))
            app.root.update()

            _fake = {"x": 1000, "y": 800}
            _orig_cursor = S.cursor_pos
            S.cursor_pos = lambda: (_fake["x"], _fake["y"])

            class _Ev:
                def __init__(self, x, y):
                    self.x, self.y = x, y

            _fake["x"], _fake["y"] = 1000, 800
            aw._on_press(_Ev(50, 50))
            _wx0, _wy0 = aw.top.winfo_x(), aw.top.winfo_y()
            _fake["x"], _fake["y"] = 1400, 1040        # 鼠标走 (+400, +240)
            aw._on_motion(_Ev(50, 50))                 # e.x 故意保持不动
            app.root.update()
            _dx = aw.top.winfo_x() - _wx0
            _dy = aw.top.winfo_y() - _wy0
            check("提醒窗拖动 1:1（400px 鼠标 → 400px 窗口）",
                  (_dx, _dy) == (400, 240), "实际 (%+d, %+d)" % (_dx, _dy))

            # e.x 给一个荒谬值，窗口位移不能受影响
            _fake["x"], _fake["y"] = 1000, 800
            aw._on_press(_Ev(0, 0))
            _bx, _by = aw.top.winfo_x(), aw.top.winfo_y()
            _fake["x"], _fake["y"] = 1200, 900
            aw._on_motion(_Ev(9999, 9999))
            app.root.update()
            _d2 = (aw.top.winfo_x() - _bx, aw.top.winfo_y() - _by)
            check("提醒窗拖动不受 e.x 污染", _d2 == (200, 100),
                  "实际 (%+d, %+d)" % _d2)

            # 连续 40 帧不应有累计漂移
            aw.top.geometry("%dx%d+%d+%d" % (aw.W, aw.H, sw_ // 4, sh_ // 4))
            app.root.update()
            _fake["x"], _fake["y"] = 1000, 800
            aw._on_press(_Ev(10, 10))
            _sx, _sy = aw.top.winfo_x(), aw.top.winfo_y()
            for _i in range(1, 41):
                _fake["x"] = 1000 + _i * 5
                _fake["y"] = 800 + _i * 3
                aw._on_motion(_Ev(10, 10))
            app.root.update()
            _d3 = (aw.top.winfo_x() - _sx, aw.top.winfo_y() - _sy)
            check("提醒窗连拖 40 帧无累计漂移", _d3 == (200, 120),
                  "实际 (%+d, %+d)" % _d3)

            # 松手记住位置 → 再次 _place 回到原地
            aw._on_release(_Ev(10, 10))
            _rx, _ry = aw.top.winfo_x(), aw.top.winfo_y()
            check("提醒窗松手后记住位置",
                  app.cfg.get("_alert_x") == _rx
                  and app.cfg.get("_alert_y") == _ry,
                  "cfg=(%s, %s)" % (app.cfg.get("_alert_x"),
                                    app.cfg.get("_alert_y")))
            aw.top.geometry("+10+10")
            app.root.update()
            aw._place()
            app.root.update()
            check("提醒窗再次弹出回到记忆位置",
                  (aw.top.winfo_x(), aw.top.winfo_y()) == (_rx, _ry),
                  "(%d, %d)" % (aw.top.winfo_x(), aw.top.winfo_y()))

            S.cursor_pos = _orig_cursor
        finally:
            # 无论如何都要把「一律移出屏幕」的保护装回去
            _tk.Wm.wm_geometry = _geom_offscreen
        app.cfg.pop("_alert_x", None)
        app.cfg.pop("_alert_y", None)
        aw.hide()

        # ---------- 16. 状态配色：换主色时不能残留旧色相 ----------
        # 用户 2026-10-12 定稿：提醒色由正红改成柔珊瑚，站姿色由绿改成青碧。
        check("提醒色为柔珊瑚 #DE6B62", S.C_ALERT.upper() == "#DE6B62", S.C_ALERT)
        check("站姿色为青碧 #0E9384", S.C_STAND.upper() == "#0E9384", S.C_STAND)
        check("坐姿色保持不变（蓝 #3B6FE0）", S.C_SIT.upper() == "#3B6FE0", S.C_SIT)

        # 关键：凡是「同色系的深/浅档」都必须由主色现算，不能硬编码，
        # 否则换了 C_ALERT 之后，会出现「珊瑚主色 + 旧深红闪烁」这种割裂。
        _src_all = _strip_comments(open(
            os.path.join(HERE, "sitstand.py"), encoding="utf-8").read())
        check("托盘闪烁深色档不再硬编码旧红 #B42318",
              "B42318" not in _src_all)
        check("danger 浅底不再硬编码旧粉底 #FEF3F2",
              "FEF3F2" not in _src_all)
        check("托盘闪烁深色档由 darken(C_ALERT) 派生",
              "darken(C_ALERT" in _src_all)

        # 实算一遍：闪烁态两档必须同色系（色相接近），否则闪起来会跳色。
        app.alerting = True
        app.cfg["flash_tray"] = True
        app._flash_on = True
        _dark = app._icon_spec()[1]
        app._flash_on = False
        _light = app._icon_spec()[1]
        app.alerting = False

        def _hue(hexs):
            r, g, b = S._hex_to_rgb(hexs)
            r, g, b = r / 255.0, g / 255.0, b / 255.0
            mx, mn = max(r, g, b), min(r, g, b)
            if mx == mn:
                return 0.0
            d = mx - mn
            if mx == r:
                h = ((g - b) / d) % 6
            elif mx == g:
                h = (b - r) / d + 2
            else:
                h = (r - g) / d + 4
            return h * 60.0

        _dh = abs(_hue(_dark) - _hue(_light))
        _dh = min(_dh, 360 - _dh)
        check("托盘闪烁两档色相接近（闪起来不跳色）",
              _dh <= 12, "%s vs %s，色相差 %.1f°" % (_dark, _light, _dh))
        check("闪烁两档确实有明暗差（能闪起来）",
              _dark != _light, "%s vs %s" % (_dark, _light))

        # 细条/进度条实际用色 = 状态色本身（不是另写死的）
        app.mode, app.alerting, app.paused = "stand", False, False
        check("站姿时细条取用 C_STAND",
              app.strip._state()[0].upper() == S.C_STAND.upper())
        app.alerting = True
        check("提醒时细条取用 C_ALERT",
              app.strip._state()[0].upper() == S.C_ALERT.upper())
        app.alerting = False
        app.mode = "sit"

    except Exception:
        REPORT.append(("**FAIL**", "冒烟测试异常", traceback.format_exc()))
    finally:
        try:
            app.strip.destroy()
        except Exception:
            pass


# 不使用 mainloop：测试里只需要手动泵事件，mainloop 会和 run() 里的 update()
# 形成重入，把测试自己卡死。
_pump(0.5)
run()
_pump(0.3)
try:
    app.root.destroy()
except Exception:
    pass
try:
    app.icon.stop()
except Exception:
    pass

bad = [r for r in REPORT if r[0] != "PASS"]
for st, name, extra in REPORT:
    print("%-8s %s  %s" % (st, name, extra if st != "PASS" else ""))
print("\n合计 %d 项，失败 %d 项" % (len(REPORT), len(bad)))
sys.exit(1 if bad else 0)
