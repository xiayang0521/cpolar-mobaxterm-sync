"""GUI 行为单元测试（需要 tkinter）。

覆盖本次新增的关键行为：
- 「最小化到托盘」隐藏窗口、托盘图标保持
- 关闭窗口（X）= 隐藏到托盘，而不是退出
- 托盘「显示主窗口」能恢复窗口（跨线程安全的队列机制）
- 定时同步状态能显示下次同步时间
- 设置里的定时同步开关能启停调度器

注意：Tk 不是线程安全的，所以测试里所有窗口操作都在主线程完成，
跨线程场景（托盘线程 → GUI）通过队列间接验证。
"""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

try:
    import tkinter as tk

    _TK_OK = True
except Exception:  # pragma: no cover
    _TK_OK = False

import config as cfg  # noqa: E402
import gui  # noqa: E402
import tray  # noqa: E402


@unittest.skipUnless(_TK_OK, "需要 tkinter")
class TestGuiTrayIntegration(unittest.TestCase):
    def setUp(self):
        self.config = {
            "cpolar_base_url": "http://127.0.0.1:9200",
            "cpolar_source": "dashboard",
            "cpolar_email": "",
            "session_file_path": "",
            "selected_session": "",
            "auto_sync_interval_min": 30,
            "start_with_windows": False,
        }
        self.sync_calls = []

        def rec(label, interactive=True):
            self.sync_calls.append((label, interactive))
            return True, "ok"

        self.sync_func = rec
        self.scheduler = tray.SyncScheduler(self.config, rec)
        self.tray_app = tray.create_tray(self.config, rec, scheduler=self.scheduler)
        self.root = tk.Tk()
        self.root.withdraw()   # 测试中不弹窗，避免遮挡
        self.app = gui.MainApp(
            self.root, self.config, rec, scheduler=self.scheduler, tray_app=self.tray_app
        )

    def tearDown(self):
        try:
            self.scheduler.stop()
        except Exception:
            pass
        # 走 app.destroy()：它会取消 after 定时器，
        # 直接 root.destroy() 会让残留的定时器报 "invalid command name"
        try:
            self.app.destroy()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def test_has_minimize_to_tray_button(self):
        """主窗口必须有「最小化到托盘」和「退出」两个按钮。"""
        texts = []

        def walk(w):
            for c in w.winfo_children():
                if c.winfo_class() == "TButton":
                    texts.append(c.cget("text"))
                walk(c)

        walk(self.root)
        self.assertIn("最小化到托盘", texts, f"缺少最小化到托盘按钮: {texts}")
        self.assertIn("退出", texts, f"缺少退出按钮: {texts}")

    def test_minimize_to_tray_hides_window(self):
        """最小化到托盘后窗口应隐藏，但托盘应用仍在。"""
        self.root.deiconify()
        self.root.update()
        self.app.minimize_to_tray()
        self.root.update()
        self.assertFalse(self.root.winfo_ismapped(), "窗口未隐藏")
        self.assertIsNotNone(self.tray_app, "托盘应用丢失")

    def test_minimize_without_tray_reports_error(self):
        """没有托盘时点击最小化应给出提示而不是崩溃。"""
        self.app.tray_app = None
        self.app.minimize_to_tray()
        self.assertIn("托盘", self.app.status_var.get())

    def test_tray_show_window_command_restores_window(self):
        """托盘「显示主窗口」命令应让隐藏的窗口回来。

        跨线程场景：托盘线程只能往队列塞消息，由主线程轮询执行，
        所以这里直接往队列塞命令并驱动一次轮询。
        """
        self.root.deiconify()
        self.root.update()
        self.app.minimize_to_tray()
        self.root.update()
        self.assertFalse(self.root.winfo_ismapped())

        # 模拟托盘线程投递命令
        self.app.show_from_tray()
        # 主线程处理队列
        self.app._pump_tray_commands()
        self.root.update()
        self.assertTrue(self.root.winfo_ismapped(), "窗口未从托盘恢复")

    def test_tray_quit_command_destroys_window(self):
        """托盘「退出」命令应销毁窗口。"""
        self.app.quit_from_tray()
        self.app._pump_tray_commands()
        self.root.update()
        # destroy 后再访问 Tk 会抛 TclError，说明窗口确实被销毁了
        with self.assertRaises(Exception):
            self.root.winfo_ismapped()

    def test_schedule_status_shows_next_run(self):
        """界面应显示下次同步时间。"""
        self.scheduler.start()
        try:
            self.app.refresh_schedule()
            self.root.update()
            text = self.app.schedule_var.get()
            self.assertIn("定时同步", text)
            self.assertIn("下次", text)
        finally:
            self.scheduler.stop()

    def test_close_hides_to_tray_instead_of_exiting(self):
        """点 X 应该隐藏到托盘而不是销毁窗口（有托盘时）。"""
        self.root.deiconify()
        self.root.update()
        self.app.on_close()
        self.root.update()
        self.assertFalse(self.root.winfo_ismapped(), "X 未隐藏到托盘")
        # 窗口还活着
        self.root.winfo_ismapped()

    def test_settings_toggle_controls_scheduler(self):
        """设置里的「启用定时同步」开关应能启停调度器。"""
        win = gui.SettingsWindow(
            self.root, self.config, lambda: None, self.scheduler
        )
        # 关闭状态：保存后调度器应停止
        win.v_auto.set(False)
        win.v_interval.set("15")
        win.save()
        self.assertFalse(self.scheduler.is_running(), "关闭后调度器仍在运行")
        self.assertEqual(self.config["auto_sync_interval_min"], 15)

        # 开启状态：保存后调度器应启动
        win2 = gui.SettingsWindow(
            self.root, self.config, lambda: None, self.scheduler
        )
        win2.v_auto.set(True)
        win2.v_interval.set("20")
        win2.save()
        try:
            self.assertTrue(self.scheduler.is_running(), "开启后调度器未启动")
            self.assertEqual(self.config["auto_sync_interval_min"], 20)
        finally:
            self.scheduler.stop()


if __name__ == "__main__":
    unittest.main()
