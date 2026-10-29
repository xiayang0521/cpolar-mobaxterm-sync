import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import mobaxterm as mb

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "sample_session.moba")

# 样例数据里的占位值（均为虚构，仅用于验证格式解析）
SAMPLE_LABEL = "demo_session"
SAMPLE_HOST = "example-tcp.cpolar.top"
SAMPLE_PORT = "12345"
NEW_HOST = "new-tcp.cpolar.top"
NEW_PORT = "23456"


class TestMobaxterm(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_list_ssh_sessions(self):
        sessions = mb.list_ssh_sessions(FIX)
        self.assertIn((SAMPLE_LABEL, SAMPLE_HOST, SAMPLE_PORT), sessions)

    def test_update_session_preview_only(self):
        f = os.path.join(self.tmp, "demo_session.moba")
        shutil.copy(FIX, f)
        res = mb.update_session(f, NEW_HOST, NEW_PORT, label=SAMPLE_LABEL)
        # 预览模式不应改文件
        with open(f, encoding="utf-8") as fh:
            self.assertNotIn(NEW_HOST, fh.read())
        self.assertNotEqual(res["old_line"], res["new_line"])
        self.assertIn(NEW_HOST, res["new_line"])
        self.assertIn("myuser", res["new_line"])  # 用户名等其余字段原样保留

    def test_apply_update_changes_only_target(self):
        f = os.path.join(self.tmp, "demo_session.moba")
        shutil.copy(FIX, f)
        res = mb.update_session(f, NEW_HOST, NEW_PORT, label=SAMPLE_LABEL)
        backup = mb.apply_update(f, res["old_line"], res["new_line"])
        with open(f, encoding="utf-8") as fh:
            text = fh.read()
        self.assertNotIn(f"{NEW_HOST}:{NEW_PORT}", text)  # 注意是 % 分隔，不是冒号
        self.assertIn(NEW_HOST, text)
        self.assertIn(NEW_PORT, text)
        self.assertNotIn(SAMPLE_HOST, text)  # 原 host 被替换
        self.assertIn("myuser", text)  # 用户名保留
        # 备份文件存在
        backups = [p for p in os.listdir(self.tmp) if p.startswith("demo_session.moba.bak_")]
        self.assertEqual(len(backups), 1)
        self.assertTrue(os.path.exists(backup))

    def test_auto_pick_label(self):
        f = os.path.join(self.tmp, "demo_session.moba")
        shutil.copy(FIX, f)
        res = mb.update_session(f, "1.2.3.4", "22")  # 不传 label
        self.assertEqual(res["label"], SAMPLE_LABEL)


class TestBackupCleanup(unittest.TestCase):
    """备份文件不应无限堆积（每日同步会持续产生 .bak_）。"""

    def test_old_backups_are_pruned(self):
        import shutil as _sh
        import tempfile as _tf
        tmp = _tf.mkdtemp(prefix="bakcleanup_")
        try:
            moba = os.path.join(tmp, "s.moba")
            with open(moba, "w", encoding="utf-8") as f:
                f.write("s =  #109#0%old.host%22%ls%%-1%-1%\n")
            # 造 15 份旧备份
            for i in range(15):
                with open(f"{moba}.bak_20260101_0000{i:02d}", "w", encoding="utf-8") as f:
                    f.write("old")
            res = mb.update_session(moba, "new.host", "2222", label="s")
            mb.apply_update(moba, res["old_line"], res["new_line"])
            backups = [f for f in os.listdir(tmp) if ".bak_" in f]
            self.assertLessEqual(
                len(backups), mb.MAX_BACKUPS + 1,
                f"备份未清理: {len(backups)} 份",
            )
            # 最新的备份必须保留
            self.assertTrue(
                any("new.host" not in open(os.path.join(tmp, b), encoding="utf-8").read()
                    for b in backups),
                "最新备份内容异常",
            )
        finally:
            _sh.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
