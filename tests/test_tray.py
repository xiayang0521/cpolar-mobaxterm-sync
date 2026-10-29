"""系统托盘与自动同步线程的单元测试。

这些都是实测中暴露过的真实缺陷的回归护栏：
- Win32 API 未声明 argtypes 导致 64 位 LPARAM 溢出、消息循环崩溃
- c_wchar 数组字段按项赋值 TypeError
- LoadIconW 资源 ID 的 MAKEINTRESOURCE 语义
- 自动同步线程遇到异常会静默死亡
"""

import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import tray  # noqa: E402


@unittest.skipUnless(tray._HAS_WIN, "仅 Windows 可测系统托盘")
class TestTrayIcon(unittest.TestCase):
    def setUp(self):
        self.app = tray._TrayApp({"selected_session": "test"}, lambda label, interactive=True: (True, "ok"))
        self.errors = []

        def run():
            try:
                self.app.run()
            except Exception as e:  # pragma: no cover
                self.errors.append(e)

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        time.sleep(1.0)

    def tearDown(self):
        if self.app.hwnd:
            try:
                self.app.user32.DestroyWindow(self.app.hwnd)
            except Exception:
                pass

    def test_message_loop_stays_alive(self):
        """消息循环不能因为参数类型问题崩溃。"""
        self.assertTrue(self.thread.is_alive(), f"消息循环已退出: {self.errors}")
        self.assertEqual(self.errors, [])

    def test_window_and_icon_created(self):
        self.assertTrue(self.app.hwnd, "未创建托盘窗口")
        self.assertTrue(self.app.hicon, "未加载托盘图标")

    def test_notify_does_not_raise(self):
        """气泡通知曾因 c_wchar 数组按项赋值而 TypeError。"""
        self.app.notify("测试通知", "cpolar 同步")

    def test_left_click_triggers_sync(self):
        """托盘左键应触发立即同步。"""
        called = []
        self.app.sync_func = lambda label, interactive=True: (called.append(label), (True, "ok"))[1]
        self.app.user32.PostMessageW(
            self.app.hwnd, tray.WM_TRAYICON, tray.ID_TRAY, 0x201
        )
        time.sleep(0.6)
        self.assertTrue(called, "左键点击未触发同步")


class TestAutoSync(unittest.TestCase):
    def test_triggers_on_interval(self):
        """自动同步应按间隔重复触发。"""
        calls = []
        config = {"auto_sync_interval_min": 30, "selected_session": "s"}
        tray.start_auto_sync(
            config,
            lambda label, interactive=True: (calls.append(label), (True, "ok"))[1],
            interval_sec=1,
        )
        time.sleep(2.5)
        self.assertGreaterEqual(len(calls), 2, f"触发次数不足: {len(calls)}")

    def test_survives_sync_exception(self):
        """同步抛异常时线程不能死，否则自动同步静默失效。"""
        calls = []

        def boom(label, interactive=True):
            calls.append(label)
            raise RuntimeError("模拟同步崩溃")

        config = {"auto_sync_interval_min": 30, "selected_session": "s"}
        thread = tray.start_auto_sync(config, boom, interval_sec=1)
        time.sleep(2.5)
        self.assertGreaterEqual(len(calls), 2, "线程在首次异常后停止")
        self.assertTrue(thread.is_alive(), "线程已死亡")

    def test_auto_mode_is_non_interactive(self):
        """自动同步不得弹窗（interactive 必须为 False）。"""
        flags = []

        def rec(label, interactive=True):
            flags.append(interactive)
            return True, "ok"

        config = {"auto_sync_interval_min": 30, "selected_session": "s"}
        tray.start_auto_sync(config, rec, interval_sec=1)
        time.sleep(1.5)
        self.assertTrue(flags)
        self.assertFalse(any(flags), "自动同步不应请求弹窗确认")


if __name__ == "__main__":
    unittest.main()
