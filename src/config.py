"""配置读写与密码托管。

配置保存在 %APPDATA%/cpolar-mobaxterm-sync/config.json。
密码凭证交由 crypto 模块加密存储（Windows DPAPI / 兜底）。
只依赖标准库。
"""

import json
import os
from pathlib import Path
from typing import Optional

import crypto  # 同目录下的加密模块


def default_session_file() -> str:
    """猜测 MobaXterm 导出的 .moba 会话文件。

    先按 MobaXterm 的常规布局找 %APPDATA%\\MobaXterm\\home\\.ssh\\；
    若该目录里有 .moba 文件，取最新修改的那个——因为 .moba 文件名由用户
    在 MobaXterm 里自定义（不一定是 session.moba），写死名字会找不到。
    找不到时返回一个不存在的占位路径，由用户在设置里指定。
    """
    base = os.environ.get("APPDATA") or str(Path.home())
    ssh_dir = Path(base) / "MobaXterm" / "home" / ".ssh"
    if ssh_dir.is_dir():
        candidates = [p for p in ssh_dir.glob("*.moba") if p.is_file()]
        if candidates:
            # 按修改时间取最新的
            newest = max(candidates, key=lambda p: p.stat().st_mtime)
            return str(newest)
    return str(ssh_dir / "session.moba")


DEFAULTS = {
    "cpolar_base_url": "http://127.0.0.1:9200",  # 仅「本地源」使用；云控制台地址固定
    "cpolar_source": "dashboard",  # "dashboard"=云控制台(默认, cpolar 在远程); "local"=本机 API
    "cpolar_email": "",
    "session_file_path": "",  # 留空则按 %APPDATA% 动态推导（见 _default_session_file）
    "selected_session": "",  # 留空则自动识别 .moba 文件里的会话
    "auto_sync_interval_min": 30,
    "start_with_windows": False,
}


def _config_dir() -> Path:
    """配置目录：优先 APPDATA，缺失时退回用户主目录。"""
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "cpolar-mobaxterm-sync"


def _config_path() -> Path:
    return _config_dir() / "config.json"


def load_config() -> dict:
    """合并默认值与磁盘配置，返回完整 dict（只保留已知字段）。"""
    path = _config_path()
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    merged = dict(DEFAULTS)
    merged.update({k: v for k, v in data.items() if k in DEFAULTS})
    # 默认路径惰性推导：必须在读取配置之后才算，
    # 否则模块导入时就固化了 %APPDATA%，运行时改环境变量不生效。
    if not merged.get("session_file_path"):
        merged["session_file_path"] = default_session_file()
    elif not os.path.exists(merged["session_file_path"]):
        # 磁盘上的路径已失效（例如 .moba 被重命名/删除）：
        # 回退到自动探测，避免用户看到"未找到会话文件"却不知为何
        fallback = default_session_file()
        if os.path.exists(fallback):
            merged["session_file_path"] = fallback
    return merged


def save_config(cfg: dict) -> None:
    """只写入已知字段，避免把未知/临时字段落盘。"""
    _config_dir().mkdir(parents=True, exist_ok=True)
    clean = {k: cfg.get(k, v) for k, v in DEFAULTS.items()}
    # 会话路径为空时写回推导出的默认值，避免落盘空串导致下次找不到文件
    if not clean.get("session_file_path"):
        clean["session_file_path"] = default_session_file()
    _config_path().write_text(
        json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def get_password() -> Optional[str]:
    """读取 cpolar 密码（解密后明文）。"""
    return crypto.get_credential("cpolar-mobaxterm-sync", "cpolar")


def set_password(pw: str) -> None:
    """加密保存 cpolar 密码。"""
    crypto.set_credential("cpolar-mobaxterm-sync", "cpolar", pw)
