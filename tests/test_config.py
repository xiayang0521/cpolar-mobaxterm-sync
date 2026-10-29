import os
import sys
import shutil
import tempfile
import unittest
from unittest import mock

# 将 src/ 加入 import 路径，使 `import config` 可用
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import config as cfg


class TestConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_defaults(self):
        with mock.patch.dict(os.environ, {"APPDATA": self.tmp}, clear=False):
            data = cfg.load_config()
        self.assertEqual(data["cpolar_base_url"], "http://127.0.0.1:9200")
        self.assertEqual(data["cpolar_source"], "dashboard")
        self.assertEqual(data["selected_session"], "")
        # .moba 默认路径应按 %APPDATA% 动态推导，而不是写死某个用户的绝对路径
        self.assertTrue(data["session_file_path"].endswith(os.path.join(".ssh", "session.moba")))
        self.assertIn(self.tmp, data["session_file_path"])

    def test_roundtrip(self):
        with mock.patch.dict(os.environ, {"APPDATA": self.tmp}, clear=False):
            data = cfg.load_config()
            data["selected_session"] = "myserver"
            data["auto_sync_interval_min"] = 15
            cfg.save_config(data)
            reloaded = cfg.load_config()
        self.assertEqual(reloaded["selected_session"], "myserver")
        self.assertEqual(reloaded["auto_sync_interval_min"], 15)

    def test_unknown_field_dropped_on_save(self):
        with mock.patch.dict(os.environ, {"APPDATA": self.tmp}, clear=False):
            data = cfg.load_config()
            data["bogus"] = "should-not-persist"
            cfg.save_config(data)
            reloaded = cfg.load_config()
        self.assertNotIn("bogus", reloaded)


if __name__ == "__main__":
    unittest.main()


class TestSessionFileDiscovery(unittest.TestCase):
    """默认 .moba 路径应自动探测，而不是写死某个文件名。

    背景：.moba 文件名由用户在 MobaXterm 里自定义（实测为 ubuntu_sever.moba），
    写死名字会导致用户看到"未找到会话文件"。
    """

    def _make_appdata(self, tmp, moba_name="my_server.moba"):
        ssh_dir = os.path.join(tmp, "MobaXterm", "home", ".ssh")
        os.makedirs(ssh_dir, exist_ok=True)
        target = os.path.join(ssh_dir, moba_name)
        with open(target, "w", encoding="utf-8") as f:
            f.write("s =  #109#0%h%22%u%%-1%-1%\n")
        return ssh_dir, target

    def test_discovers_existing_moba_file(self):
        tmp = tempfile.mkdtemp()
        try:
            _, target = self._make_appdata(tmp, "ubuntu_sever.moba")
            with mock.patch.dict(os.environ, {"APPDATA": tmp}, clear=False):
                path = cfg.default_session_file()
            self.assertEqual(os.path.normpath(path), os.path.normpath(target))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_falls_back_when_dir_missing(self):
        tmp = tempfile.mkdtemp()
        try:
            with mock.patch.dict(os.environ, {"APPDATA": tmp}, clear=False):
                path = cfg.default_session_file()
            self.assertIn(".ssh", path)
            self.assertFalse(os.path.exists(path))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_stale_config_path_falls_back_to_discovery(self):
        """磁盘上的路径失效时应回退到自动探测。"""
        tmp = tempfile.mkdtemp()
        try:
            _, target = self._make_appdata(tmp, "real.moba")
            with mock.patch.dict(os.environ, {"APPDATA": tmp}, clear=False):
                data = cfg.load_config()
                data["session_file_path"] = os.path.join(tmp, "gone.moba")
                cfg.save_config(data)
                reloaded = cfg.load_config()
            self.assertEqual(
                os.path.normpath(reloaded["session_file_path"]),
                os.path.normpath(target),
                "失效路径未回退到自动探测",
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
