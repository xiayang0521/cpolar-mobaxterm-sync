"""凭据加密存储（仅依赖标准库）。

安全要求：**任何情况下密码都不以明文落盘**。

加密方案（按优先级）：
1. **Windows DPAPI**（ctypes 调用 crypt32!CryptProtectData）
   OS 级加密，与「Windows 凭据管理器」同源，密钥由系统托管。
   密文只有当前 Windows 用户能解密，其他账户/其他机器无法还原。
   在 Windows 上这是首选且是**唯一**方案：DPAPI 失败时直接报错，
   绝不静默降级到弱保护（降级比失败更危险）。

2. **跨平台兜底**（非 Windows，或 Windows 上 DPAPI 完全不可用时）
   真正的对称加密，而非 base64 之类可被轻易还原的混淆：
   - 密钥：首次运行时生成 32 字节随机密钥存到 ``credentials.key``（权限 0600）
   - 派生：PBKDF2-HMAC-SHA256(密钥, 随机盐, 200000 次) → 加密密钥 / 认证密钥
   - 加密：SHA-256 计数器模式流密码（HMAC-SHA256 作 PRF）
   - 认证：HMAC-SHA256 校验（encrypt-then-MAC），防篡改
   威胁模型：抵御同机其他普通用户/直接读取密文文件的窥探；
   若攻击者已能读取同目录下的密钥文件则不再有效（与 DPAPI 相比更弱，
   因此 Windows 上始终优先 DPAPI）。

存储位置：%APPDATA%/cpolar-mobaxterm-sync/
  - credentials.enc   密文（首行标签 "DPAPI:" / "KDF1:" + 密文）
  - credentials.key   随机密钥（仅 KDF1 方案需要，权限 0600）
"""

import base64
import hashlib
import hmac
import os
import struct
from typing import Optional

_ENC_FILE = "credentials.enc"
_KEY_FILE = "credentials.key"

_PREFIX_DPAPI = b"DPAPI:"
_PREFIX_KDF = b"KDF1:"
_PREFIX_B64 = b"B64:"  # 历史遗留：仅用于读取旧版本写出的文件，读到后会立即升级

_PBKDF2_ITERATIONS = 200_000
_SALT_LEN = 16
_KEY_LEN = 32
_NONCE_LEN = 16
_TAG_LEN = 32


def _config_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "cpolar-mobaxterm-sync")


def _is_windows() -> bool:
    return os.name == "nt"


# ---------------------------------------------------------------------------
# 公共接口
# ---------------------------------------------------------------------------

def set_credential(service: str, account: str, secret: str) -> None:
    """保存一条凭据（加密后落盘，绝不写明文）。"""
    plaintext = f"{service}|{account}|{secret}".encode("utf-8")
    if _is_windows():
        try:
            blob = _dpapi_protect(plaintext)
        except Exception as e:
            # Windows 上不允许降级：宁可保存失败，也不要写出弱保护的凭据
            raise RuntimeError(
                f"Windows DPAPI 加密失败，为保证密码不落明文已中止保存：{e}"
            ) from e
        _write(blob, _PREFIX_DPAPI)
        return
    blob = _kdf_encrypt(plaintext)
    _write(blob, _PREFIX_KDF)


def get_credential(service: str, account: str) -> Optional[str]:
    """读取一条凭据并解密；找不到或解密失败返回 None。"""
    path = os.path.join(_config_dir(), _ENC_FILE)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return None

    try:
        if raw.startswith(_PREFIX_DPAPI):
            plain = _dpapi_unprotect(raw[len(_PREFIX_DPAPI):])
        elif raw.startswith(_PREFIX_KDF):
            plain = _kdf_decrypt(raw[len(_PREFIX_KDF):])
        elif raw.startswith(_PREFIX_B64):
            # 旧版本（base64 混淆）写出的凭据：能读就读，并立即用强加密重写
            plain = base64.b64decode(raw[len(_PREFIX_B64):])
            try:
                set_credential(service, account, plain.decode("utf-8").split("|", 2)[2])
            except Exception:
                pass
        else:
            return None
    except Exception:
        return None

    try:
        s, a, secret = plain.decode("utf-8").split("|", 2)
        return secret if (s == service and a == account) else None
    except (UnicodeDecodeError, ValueError):
        return None


def delete_credential(service: str, account: str) -> bool:
    """删除已保存的凭据；不存在返回 False。"""
    path = os.path.join(_config_dir(), _ENC_FILE)
    if not os.path.exists(path):
        return False
    if get_credential(service, account) is None:
        return False
    try:
        os.remove(path)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# 落盘
# ---------------------------------------------------------------------------

def _write(blob: bytes, prefix: bytes) -> None:
    path = os.path.join(_config_dir(), _ENC_FILE)
    os.makedirs(_config_dir(), exist_ok=True)
    with open(path, "wb") as f:
        f.write(prefix + blob)


# ---------------------------------------------------------------------------
# 跨平台兜底：PBKDF2 + SHA256-CTR + HMAC
# ---------------------------------------------------------------------------

def _load_or_create_master_key() -> bytes:
    """读取（或首次生成）本机随机主密钥，权限收紧到仅本人可读写。"""
    path = os.path.join(_config_dir(), _KEY_FILE)
    if os.path.exists(path):
        with open(path, "rb") as f:
            data = f.read()
        if len(data) >= _KEY_LEN:
            return data[:_KEY_LEN]
    key = os.urandom(_KEY_LEN)
    os.makedirs(_config_dir(), exist_ok=True)
    # 先以 0600 创建再写，避免出现短暂的宽权限窗口
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, key)
    finally:
        os.close(fd)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


def _derive_keys(master: bytes, salt: bytes) -> tuple:
    """PBKDF2 派生出加密密钥与认证密钥。"""
    material = hashlib.pbkdf2_hmac(
        "sha256", master, salt, _PBKDF2_ITERATIONS, dklen=_KEY_LEN * 2
    )
    return material[:_KEY_LEN], material[_KEY_LEN:]


def _keystream(enc_key: bytes, nonce: bytes, length: int) -> bytes:
    """SHA-256 计数器模式流密码（HMAC-SHA256 作 PRF）。"""
    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hmac.new(
            enc_key, nonce + struct.pack(">Q", counter), hashlib.sha256
        ).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


def _kdf_encrypt(plaintext: bytes) -> bytes:
    master = _load_or_create_master_key()
    salt = os.urandom(_SALT_LEN)
    nonce = os.urandom(_NONCE_LEN)
    enc_key, auth_key = _derive_keys(master, salt)
    cipher = bytes(a ^ b for a, b in zip(plaintext, _keystream(enc_key, nonce, len(plaintext))))
    tag = hmac.new(auth_key, salt + nonce + cipher, hashlib.sha256).digest()
    return salt + nonce + tag + cipher


def _kdf_decrypt(blob: bytes) -> bytes:
    header = _SALT_LEN + _NONCE_LEN + _TAG_LEN
    if len(blob) < header:
        raise ValueError("凭据文件已损坏")
    salt = blob[:_SALT_LEN]
    nonce = blob[_SALT_LEN:_SALT_LEN + _NONCE_LEN]
    tag = blob[_SALT_LEN + _NONCE_LEN:header]
    cipher = blob[header:]

    master = _load_or_create_master_key()
    enc_key, auth_key = _derive_keys(master, salt)
    expected = hmac.new(auth_key, salt + nonce + cipher, hashlib.sha256).digest()
    # 先验 MAC 再解密：防止篡改导致的明文猜测（encrypt-then-MAC）
    if not hmac.compare_digest(tag, expected):
        raise ValueError("凭据完整性校验失败（文件可能被篡改）")
    return bytes(a ^ b for a, b in zip(cipher, _keystream(enc_key, nonce, len(cipher))))


# ---------------------------------------------------------------------------
# Windows DPAPI 封装（ctypes）。仅在 Windows 平台可用。
# ---------------------------------------------------------------------------

def _dpapi_protect(data: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    in_buf = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), ctypes.cast(in_buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    # CRYPTPROTECT_UI_FORBIDDEN = 0x1
    res = crypt32.CryptProtectData(
        ctypes.byref(blob_in), None, None, None, None, 0x1, ctypes.byref(blob_out)
    )
    if not res:
        raise ctypes.WinError()  # 交给调用方处理，绝不静默降级
    out = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    kernel32.LocalFree(blob_out.pbData)
    return out


def _dpapi_unprotect(blob: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    in_buf = ctypes.create_string_buffer(blob, len(blob))
    blob_in = DATA_BLOB(len(blob), ctypes.cast(in_buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    res = crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0x1, ctypes.byref(blob_out)
    )
    if not res:
        raise ctypes.WinError()
    out = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    kernel32.LocalFree(blob_out.pbData)
    return out
