"""开机启动（注册表 Run 键）单元测试。

覆盖：开启写入、关闭删除、重复关闭幂等、未打包时命令必须带解释器。
"""

import os
import sys
import unittest
import winreg

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import tray  # noqa: E402


RUN_KEY_PATH = "Software\\Microsoft\\Windows\\CurrentVersion\\Run"
VALUE_NAME = "CpolarMobaSync"


def read_run_value():
    """读取当前用户的开机启动项；不存在返回 None。"""
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_READ
        ) as key:
            try:
                return winreg.QueryValueEx(key, VALUE_NAME)[0]
            except FileNotFoundError:
                return None
    except OSError:
        return None


class TestStartup(unittest.TestCase):
    def setUp(self):
        # 保护用户真实设置：测试前先记下原值
        self._original = read_run_value()

    def tearDown(self):
        # 测试后恢复原值，避免动到用户真实配置
        if self._original is None:
            tray.set_startup(False)
        else:
            try:
                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE
                ) as key:
                    winreg.SetValueEx(
                        key, VALUE_NAME, 0, winreg.REG_SZ, self._original
                    )
            except OSError:
                pass

    def test_enable_writes_run_key(self):
        ok, msg = tray.set_startup(True)
        self.assertTrue(ok, msg)
        value = read_run_value()
        self.assertIsNotNone(value, "注册表 Run 键未写入")
        self.assertIn("--minimized", value)

    def test_disable_removes_run_key(self):
        tray.set_startup(True)
        ok, msg = tray.set_startup(False)
        self.assertTrue(ok, msg)
        self.assertIsNone(read_run_value(), "注册表 Run 键未被删除")

    def test_disable_is_idempotent(self):
        """重复关闭不应报错（本来就没开时也视为成功）。"""
        ok, msg = tray.set_startup(False)
        self.assertTrue(ok, msg)

    def test_command_includes_interpreter_when_not_frozen(self):
        """未打包时命令必须带 Python 解释器，否则注册表项无法执行 .py。"""
        original_argv = sys.argv
        original_frozen = getattr(sys, "frozen", False)
        try:
            sys.argv = ["C:\\somewhere\\main.py"]
            if hasattr(sys, "frozen"):
                del sys.frozen
            cmd = tray._startup_command()
            self.assertIn("main.py", cmd)
            self.assertIn(".exe", cmd.lower(), "命令缺少 Python 解释器")
        finally:
            sys.argv = original_argv
            if original_frozen:
                sys.frozen = original_frozen

    def test_returns_message_on_failure(self):
        """失败时必须返回可读消息，不能抛异常或静默。"""
        ok, msg = tray.set_startup(True)
        self.assertIsInstance(ok, bool)
        self.assertIsInstance(msg, str)
        self.assertTrue(msg)


if __name__ == "__main__":
    unittest.main()


class TestStartupReconcile(unittest.TestCase):
    """配置里的开关与注册表实际状态可能不一致，启动时应能对齐。"""

    def test_startup_registered_reflects_registry(self):
        tray.set_startup(False)
        self.assertFalse(tray.startup_registered())
        tray.set_startup(True)
        self.assertTrue(tray.startup_registered())
        tray.set_startup(False)

    def test_main_reconcile_registers_when_config_enabled(self):
        """配置为开启但注册表没注册时，_sync_startup_state 应把它补上。"""
        import importlib
        import main as main_mod

        importlib.reload(main_mod)
        tray.set_startup(False)
        config = {"start_with_windows": True}
        main_mod._sync_startup_state(config)
        self.assertTrue(tray.startup_registered(), "启动时未对齐注册表状态")
        tray.set_startup(False)

    def test_main_reconcile_removes_when_config_disabled(self):
        """配置为关闭但注册表仍存在时，应把注册项清掉。"""
        import importlib
        import main as main_mod

        importlib.reload(main_mod)
        tray.set_startup(True)
        config = {"start_with_windows": False}
        main_mod._sync_startup_state(config)
        self.assertFalse(tray.startup_registered(), "关闭状态未对齐")
        tray.set_startup(False)


class TestStartupCommandExecutable(unittest.TestCase):
    """注册表里的命令必须能被 Windows 正确解析并真的跑起来。

    曾出现 `""python.exe" "main.py"" --minimized`（引号套两层）的缺陷，
    单看字符串无法发现，必须真实执行一次。
    """

    def _parse(self, cmd):
        import re

        parts = re.findall(r'"([^"]*)"|(\S+)', cmd)
        return [a or b for a, b in parts]

    def test_command_has_no_doubled_quotes(self):
        cmd = tray._startup_command()
        self.assertNotIn('""', cmd, f"引号套了两层: {cmd}")

    def test_command_parses_into_expected_tokens(self):
        cmd = tray._startup_command()
        tokens = self._parse(cmd)
        self.assertIn("--minimized", tokens, f"缺少 --minimized: {cmd}")
        # 第一个 token 必须是可执行文件，且真实存在
        self.assertTrue(
            os.path.exists(tokens[0]), f"解释器不存在: {tokens[0]}"
        )

    def test_registered_command_actually_runs(self):
        """把命令写进注册表再真实执行，验证 Windows 能正确启动它。"""
        import subprocess
        import time

        original = read_run_value()
        try:
            tray.set_startup(True)
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_READ
            ) as key:
                cmd = winreg.QueryValueEx(key, VALUE_NAME)[0]

            tokens = self._parse(cmd)
            # 用解析出的 token 启动，参数不带引号（Windows 传参时的真实形态）
            proc = subprocess.Popen(
                tokens,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            time.sleep(3.0)
            alive = proc.poll() is None
            if alive:
                proc.terminate()
                try:
                    proc.communicate(timeout=5)
                except Exception:
                    pass
            else:
                output = proc.communicate()[0]
                self.fail(f"注册表命令启动后立刻退出，退出码 {proc.returncode}: {output[:300]}")
            self.assertTrue(alive, "进程未能常驻")
        finally:
            if original is None:
                tray.set_startup(False)
            else:
                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE
                ) as key:
                    winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, original)
