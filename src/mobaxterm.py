"""MobaXterm .moba 会话文件：定位 / 解析 / 定点更新（仅依赖标准库）。

.moba 文件是 MobaXterm 导出的单会话纯文本。一行形如：
    <label> =  #109#0%<host>%<port>%<user>%<pw>%...
其中 #109# 是 SSH 会话编码段起头，其后按 % 切分：
    parts[0]='0', parts[1]=host, parts[2]=port, parts[3]=user, ...

本工具只替换 host(段1) / port(段2)，其余（用户名、私钥路径等）原样保留。
写入前自动备份；不修改运行中的 MobaXterm.ini。
"""

import datetime
import os
import shutil

MARKER = "#109#"   # SSH 会话编码段起头
HOST_IDX = 1       # #109# 后按 % 切分，host 在索引 1（已用真实文件校准）
PORT_IDX = 2       # port 在索引 2


def find_session_file(explicit_path=""):
    """返回存在的 .moba 路径；不存在则返回 None。"""
    if explicit_path and os.path.exists(explicit_path):
        return explicit_path
    return None


def _parse_line(line):
    """解析一行；不是 #109# SSH 会话行则返回 None。"""
    idx = line.find(MARKER)
    if idx == -1:
        return None
    prefix = line[:idx]
    label = prefix.split("=")[0].strip()
    rest = line[idx + len(MARKER):].rstrip("\n")
    parts = rest.split("%")
    if len(parts) <= PORT_IDX:
        return None
    # (label, host, port, 原始 rest)
    return label, parts[HOST_IDX], parts[PORT_IDX], rest


def list_ssh_sessions(moba_path):
    """返回 [(label, host, port), ...]。"""
    out = []
    with open(moba_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            parsed = _parse_line(line)
            if parsed:
                out.append((parsed[0], parsed[1], parsed[2]))
    return out


def _pick_label(moba_path, label):
    if label:
        return label
    sessions = list_ssh_sessions(moba_path)
    if not sessions:
        raise ValueError("未在 .moba 文件中找到任何 SSH 会话")
    return sessions[0][0]


def update_session(moba_path, host, port, label=None):
    """预览定点替换（不直接落盘）。

    返回 {"backup": None, "old_line": str, "new_line": str, "label": str}。
    label=None 时自动选文件中第一个 SSH 会话。
    """
    label = _pick_label(moba_path, label)
    with open(moba_path, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    old_line = None
    new_line = None
    for line in lines:
        parsed = _parse_line(line)
        if parsed and parsed[0] == label:
            rest = parsed[3]
            parts = rest.split("%")
            parts[HOST_IDX] = host
            parts[PORT_IDX] = port
            new_rest = "%".join(parts)
            old_line = line.rstrip("\n")
            prefix = line[: line.index(MARKER)]  # 保留原行前缀（标签+分隔符+空格）
            new_line = prefix + MARKER + new_rest
            break
    if old_line is None or new_line is None:
        raise ValueError(f"未找到会话 '{label}'")
    return {"backup": None, "old_line": old_line, "new_line": new_line, "label": label}


def apply_update(moba_path, old_line, new_line):
    """备份后把预览过的 new_line 写回（替换原 old_line 那一行），返回备份路径。"""
    backup = moba_path + ".bak_" + _timestamp()
    shutil.copy(moba_path, backup)
    with open(moba_path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    if old_line not in text:
        raise ValueError("原会话行已变化，放弃写入（请重新同步）")
    text = text.replace(old_line, new_line, 1)
    with open(moba_path, "w", encoding="utf-8") as f:
        f.write(text)
    _cleanup_old_backups(moba_path)
    return backup


MAX_BACKUPS = 10   # 保留最近 10 份备份，避免每日同步堆积垃圾文件


def _cleanup_old_backups(moba_path, keep=MAX_BACKUPS):
    """删除超出保留数量的旧备份，返回被删除的文件名列表。"""
    directory = os.path.dirname(os.path.abspath(moba_path))
    prefix = os.path.basename(moba_path) + ".bak_"
    try:
        candidates = [
            os.path.join(directory, name)
            for name in os.listdir(directory)
            if name.startswith(prefix)
        ]
    except OSError:
        return []
    # 按文件名里的时间戳排序（格式 YYYYmmdd_HHMMSS，字典序即时间序）
    candidates.sort(reverse=True)
    removed = []
    for stale in candidates[keep:]:
        try:
            os.remove(stale)
            removed.append(stale)
        except OSError:
            pass
    return removed


def _timestamp():
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
