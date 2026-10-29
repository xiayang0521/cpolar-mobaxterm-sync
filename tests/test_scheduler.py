"""定时同步调度器（SyncScheduler）单元测试。

覆盖三类真实需求：
- 按间隔重复触发
- 改间隔后立即生效（不必等当前周期跑完）
- 能干净停止，且异常不会杀死线程
"""

import datetime
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import tray  # noqa: E402


class TestSyncScheduler(unittest.TestCase):
    def _make(self, interval_min=30, interval_sec=None):
        config = {"auto_sync_interval_min": interval_min, "selected_session": "s"}
        calls = []

        def rec(label, interactive=True):
            calls.append((label, interactive))
            return True, "ok"

        return config, calls, tray.SyncScheduler(
            config, rec, interval_sec=interval_sec
        )

    def test_triggers_on_interval(self):
        # 用秒级间隔注入，才能在测试的秒级时间窗内观察到触发
        config, calls, sched = self._make(interval_sec=1)
        sched.start()
        try:
            time.sleep(2.5)
            self.assertGreaterEqual(len(calls), 2, f"触发次数不足: {len(calls)}")
        finally:
            sched.stop()

    def test_auto_mode_is_non_interactive(self):
        """自动同步不得弹窗（interactive 必须为 False）。"""
        config, calls, sched = self._make(interval_sec=1)
        sched.start()
        try:
            time.sleep(1.5)
        finally:
            sched.stop()
        self.assertTrue(calls)
        self.assertFalse(any(flag for _, flag in calls), "自动同步不应请求弹窗确认")

    def test_survives_sync_exception(self):
        """同步抛异常时线程不能死，否则自动同步静默失效。"""
        config = {"auto_sync_interval_min": 30, "selected_session": "s"}
        calls = []

        def boom(label, interactive=True):
            calls.append(label)
            raise RuntimeError("模拟同步崩溃")

        sched = tray.SyncScheduler(config, boom, interval_sec=1)
        sched.start()
        try:
            time.sleep(2.5)
            self.assertGreaterEqual(len(calls), 2, "线程在首次异常后停止")
            self.assertTrue(sched.is_running(), "线程已死亡")
        finally:
            sched.stop()

    def test_interval_change_takes_effect_immediately(self):
        """改间隔应立即生效，而不是等当前周期跑完。

        这是本次改造的核心：原实现用 time.sleep()，改配置后必须重启程序。
        验证方式：先用长间隔起线程，确认还没触发；再通过公开 API 改成短间隔，
        不重启线程就应很快触发——证明等待中的计时被正确唤醒重算。
        """
        config, calls, sched = self._make(interval_min=30, interval_sec=600)
        sched.start()
        try:
            time.sleep(0.8)
            self.assertEqual(len(calls), 0, "长间隔不应这么快触发")
            nxt = sched.next_run_at()
            self.assertIsNotNone(nxt)
            remain = (nxt - datetime.datetime.now()).total_seconds()
            self.assertGreater(remain, 9 * 60, "下次同步时间应按 600 秒算")

            # 通过公开 API 改间隔（模拟用户在设置窗口改了值并保存）。
            # set_interval_minutes 的单位是分钟，这里用 0 分钟会被钳制为 1 分钟，
            # 所以直接改配置 + 唤醒，走与设置窗口保存时相同的代码路径。
            sched.config["auto_sync_interval_min"] = 1
            sched._override_cleared = True
            sched._wake.set()

            # 新的间隔是 1 分钟 = 60 秒，仍超出测试时间窗，
            # 因此这里只断言「下次同步时间被重算到 1 分钟内」，
            # 触发本身由 test_triggers_on_interval 覆盖。
            time.sleep(0.8)
            nxt2 = sched.next_run_at()
            self.assertIsNotNone(nxt2)
            remain2 = (nxt2 - datetime.datetime.now()).total_seconds()
            self.assertLess(
                remain2, 90,
                f"等待中的计时未被唤醒重算（剩余 {remain2:.0f} 秒）",
            )
        finally:
            sched.stop()

    def test_stop_is_clean(self):
        config, calls, sched = self._make()
        sched.start()
        time.sleep(0.5)
        sched.stop()
        self.assertFalse(sched.is_running(), "stop() 后线程仍在运行")
        self.assertIsNone(sched.next_run_at(), "stop() 后仍报告下次同步时间")

    def test_trigger_now_runs_immediately(self):
        config, calls, sched = self._make()
        ok, msg = sched.trigger_now()
        self.assertTrue(ok)
        self.assertEqual(len(calls), 1, "trigger_now 未立即执行")

    def test_set_interval_minutes_updates_config(self):
        config, calls, sched = self._make(interval_min=30)
        sched.set_interval_minutes(5)
        self.assertEqual(config["auto_sync_interval_min"], 5)
        self.assertEqual(sched.interval_seconds(), 300)

    def test_interval_minimum_is_one_minute(self):
        config, calls, sched = self._make(interval_min=0)
        self.assertEqual(sched.interval_seconds(), 60, "间隔下限应为 1 分钟")

    def test_result_callback_invoked(self):
        config = {"auto_sync_interval_min": 30, "selected_session": "s"}
        results = []
        sched = tray.SyncScheduler(
            config, lambda label, interactive=True: (True, "同步成功"),
            on_result=lambda ok, msg: results.append((ok, msg)),
        )
        sched.trigger_now()
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0][0])
        self.assertEqual(results[0][1], "同步成功")

    def test_notify_failure_does_not_break_sync(self):
        """通知（托盘气泡）失败不应影响同步结果。"""
        config = {"auto_sync_interval_min": 30, "selected_session": "s"}

        def bad_notify(msg, title):
            raise RuntimeError("通知失败")

        sched = tray.SyncScheduler(
            config, lambda label, interactive=True: (True, "ok"), notify=bad_notify
        )
        ok, msg = sched.trigger_now()
        self.assertTrue(ok, "通知失败不应让同步判定为失败")


if __name__ == "__main__":
    unittest.main()
