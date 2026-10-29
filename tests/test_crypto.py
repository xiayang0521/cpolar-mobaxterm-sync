import base64
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import crypto as c  # noqa: E402


class TestCrypto(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        # 把配置目录重定向到临时目录，避免动到用户真实凭据
        patcher = mock.patch.object(c, "_config_dir", return_value=self.tmp)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_roundtrip(self):
        """set 后再 get 应得到原值（Windows 走 DPAPI，其他平台走 KDF1 兜底）。"""
        c.set_credential("svc", "acc", "secret123")
        self.assertEqual(c.get_credential("svc", "acc"), "secret123")

    def test_wrong_account_returns_none(self):
        c.set_credential("svc", "acc", "secret123")
        self.assertIsNone(c.get_credential("svc", "other"))
        self.assertIsNone(c.get_credential("other", "acc"))

    def test_missing_returns_none(self):
        self.assertIsNone(c.get_credential("svc", "acc"))

    def test_plaintext_not_on_disk(self):
        """核心安全要求：密码绝不能以明文出现在磁盘文件里。"""
        secret = "MySuperSecretP@ssw0rd"
        c.set_credential("svc", "acc", secret)
        for name in os.listdir(self.tmp):
            with open(os.path.join(self.tmp, name), "rb") as f:
                blob = f.read()
            self.assertNotIn(
                secret.encode("utf-8"), blob, f"{name} 中出现了明文密码"
            )
            self.assertNotIn(b"svc|acc|", blob, f"{name} 中出现了明文凭据")

    def test_ciphertext_differs_from_plaintext(self):
        c.set_credential("svc", "acc", "secret123")
        with open(os.path.join(self.tmp, c._ENC_FILE), "rb") as f:
            blob = f.read()
        self.assertNotIn(b"secret123", blob)
        # 应带有版本标签
        self.assertTrue(
            blob.startswith(c._PREFIX_DPAPI) or blob.startswith(c._PREFIX_KDF),
            "缺少加密方案标签",
        )

    def test_delete_credential(self):
        c.set_credential("svc", "acc", "secret123")
        self.assertTrue(c.delete_credential("svc", "acc"))
        self.assertIsNone(c.get_credential("svc", "acc"))

    def test_kdf_roundtrip_and_tamper_detection(self):
        """跨平台兜底方案：往返正确，且密文被篡改时能被发现。"""
        secret = "kdf-secret-中文-!@#".encode("utf-8")
        blob = c._kdf_encrypt(secret)
        self.assertNotIn(secret, blob)
        self.assertEqual(c._kdf_decrypt(blob), secret)

        # 篡改密文 -> HMAC 校验失败
        tampered = bytearray(blob)
        tampered[-1] ^= 0xFF
        with self.assertRaises(ValueError):
            c._kdf_decrypt(bytes(tampered))

    def test_windows_does_not_silently_downgrade(self):
        """Windows 上 DPAPI 失败必须抛错，不能降级成弱保护。"""
        with mock.patch.object(c, "_is_windows", return_value=True), \
             mock.patch.object(
                 c, "_dpapi_protect", side_effect=OSError("DPAPI 不可用")
             ), \
             mock.patch.object(c, "_kdf_encrypt") as kdf_mock:
            with self.assertRaises(RuntimeError):
                c.set_credential("svc", "acc", "secret123")
            kdf_mock.assert_not_called()  # 未发生降级写入

    def test_non_windows_uses_kdf_scheme(self):
        """非 Windows 上使用真正的对称加密（KDF1），而非 base64 混淆。"""
        with mock.patch.object(c, "_is_windows", return_value=False):
            c.set_credential("svc", "acc", "secret123")
            with open(os.path.join(self.tmp, c._ENC_FILE), "rb") as f:
                blob = f.read()
        self.assertTrue(blob.startswith(c._PREFIX_KDF))
        self.assertNotIn(b"secret123", blob)
        # 密钥文件应存在且权限收紧
        key_path = os.path.join(self.tmp, c._KEY_FILE)
        self.assertTrue(os.path.exists(key_path))
        if os.name != "nt":
            self.assertEqual(os.stat(key_path).st_mode & 0o777, 0o600)

    def test_legacy_b64_is_read_and_upgraded(self):
        """旧版本写出的 base64 凭据能被读取，并立即升级为强加密。"""
        legacy = base64.b64encode("svc|acc|legacysecret".encode("utf-8"))
        with open(os.path.join(self.tmp, c._ENC_FILE), "wb") as f:
            f.write(c._PREFIX_B64 + legacy)
        self.assertEqual(c.get_credential("svc", "acc"), "legacysecret")
        # 读取后应已重写为新方案
        with open(os.path.join(self.tmp, c._ENC_FILE), "rb") as f:
            blob = f.read()
        self.assertFalse(blob.startswith(c._PREFIX_B64))
        self.assertNotIn(b"legacysecret", blob)


if __name__ == "__main__":
    unittest.main()
