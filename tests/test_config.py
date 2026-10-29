import os
import sys
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
