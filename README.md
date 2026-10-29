<div align="center">

# cpolar-mobaxterm-sync

**自动把 cpolar 的 SSH 隧道地址同步到 MobaXterm 会话文件**

省掉每天手动改主机/端口的重复劳动

[![Python](https://img.shields.io/badge/Python-3.8%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![平台](https://img.shields.io/badge/platform-Windows-lightgrey.svg)]()
[![依赖](https://img.shields.io/badge/dependencies-0%20(仅标准库)-brightgreen.svg)]()

</div>

---

## 这是什么

用 cpolar 做内网穿透时，SSH 隧道的公网地址（`xxx.tcp.cpolar.cn`）和端口**每次重启都会变**。
你得手动打开 MobaXterm 的会话属性，把新的主机和端口敲进去。

这个工具替你做这件事：

```
┌─────────────┐    读取隧道地址    ┌──────────────┐   定点改写    ┌────────────┐
│ cpolar      │ ────────────────► │ 同步工具      │ ───────────► │ .moba 会话 │
│ 云控制台     │  host / port      │ (本项目)      │  只换两字段   │ 文件       │
└─────────────┘                   └──────────────┘              └────────────┘
```

改完在 MobaXterm 里重新导入该 `.moba` 文件即可生效——**不用再逐个字段手动敲**。

## 为什么是 .moba 文件

MobaXterm 的会话保存在运行中的 `MobaXterm.ini` 里，直接改它有两个问题：程序退出时会用内存里的旧值覆盖你的修改，
而且不同版本 ini 编码格式有差异。

所以本工具走另一条路：**MobaXterm 支持导入 `.moba` 会话文件**，工具只改这个独立文件，由你自己在 MobaXterm 里导入。
好处是完全不碰运行中的程序，改坏了也不影响你已有的会话。

## 特性

- **🔐 凭据加密存储** —— 密码用 Windows DPAPI（OS 级加密，密钥由系统托管）保存，绝不明文落盘
- **🪶 零第三方依赖** —— 纯 Python 标准库实现，`git clone` 完直接跑，不用 `pip install`
- **🖥️ GUI + 系统托盘** —— 图形界面手动同步，也可最小化到托盘后台定时自动同步
- **🔒 写入安全** —— 只替换 host/port 两个字段，用户名、私钥路径等原样保留；每次写入自动备份
- **👀 手动模式先预览** —— 弹窗展示「改前 / 改后」对比，确认后才落盘
- **📦 一键打包** —— PyInstaller 打成单个 exe，双击即用

## 快速开始

### 方式一：直接跑源码（推荐先这样试）

```bash
git clone https://github.com/xiayang0521/cpolar-mobaxterm-sync.git
cd cpolar-mobaxterm-sync
python src/main.py
```

要求：Python 3.8+，Windows。**无需安装任何第三方包。**

### 方式二：打包成单文件 exe

```bash
pip install pyinstaller
pyinstaller build.spec
# 产物：dist/cpolar-mobaxterm-sync.exe
```

## 使用步骤

### 1. 准备 `.moba` 会话文件

在 MobaXterm 里配置好你的 SSH 会话（主机、端口、用户名、私钥等），
然后导出会话文件，得到形如 `my_server.moba` 的文件。

> 工具只改写**主机和端口**两处，其余字段（用户名、私钥路径、终端配色等）原样保留。

### 2. 填设置

打开程序的「设置」窗口：

| 配置项 | 说明 |
|---|---|
| **读取源** | `dashboard` = cpolar 云控制台（默认，适合 cpolar 装在远程服务器）<br>`local` = 本机 API `http://127.0.0.1:9200`（适合 cpolar 装在本机） |
| **cpolar 邮箱 / 密码** | cpolar 账号。密码经 DPAPI 加密后保存，不落明文 |
| **会话文件(.moba)路径** | 步骤 1 导出的那个文件，可用「浏览」选择 |
| **自动同步间隔(分)** | 后台自动同步的周期，默认 30 分钟 |
| **开机启动** | 勾选后随 Windows 登录自动进托盘 |

### 3. 同步

回到主窗口 → 选好 SSH 会话 → 点「**立即同步**」→ 确认预览 → 完成。

之后在 MobaXterm 里**重新导入**这个 `.moba` 文件即可。

> 自动同步模式（托盘）会静默写入，不弹窗，并在托盘气泡提示结果。

## 两种读取源怎么选

| | `dashboard`（云控制台，默认） | `local`（本机 API） |
|---|---|---|
| 适用场景 | **cpolar 装在远程 Linux 服务器** | cpolar 装在这台 Windows 上 |
| 原理 | 登录 `dashboard.cpolar.com`，解析「在线隧道」表格 | 读 `http://127.0.0.1:9200/api/v1/tunnels` |
| 需要 | cpolar 账号密码 | 本机已装 cpolar |

## 工作原理

```
[定时触发 / 手动点击]
  │
  ├─ 读取 cpolar 当前 SSH 隧道
  │    dashboard: GET /login 取 csrf_token → POST /login → GET /status → 解析表格
  │    local:     POST /api/v1/user/login → GET /api/v1/tunnels
  │    → 提取 public_url (tcp://host:port) → 得到 (host, port)
  │
  ├─ 解析 .moba 文件
  │    #109# 之后按 % 切分 → parts[1]=host, parts[2]=port, parts[3]=user ...
  │
  ├─ 手动模式：弹窗展示改前/改后，用户确认
  │
  └─ 备份原文件（.bak_时间戳）→ 只替换 host/port 两段 → 写回
```

**解析 SSH 隧道行的判定**：隧道名称为 `ssh`，或「本地地址」以 `:22` 结尾。

**一个容易踩的坑**：云控制台里 SSH 那一行的 URL 单元格，其 `<a>` 标签的 `href` 是假锚点
（形如 `status#ZgotmplZ`），**必须取 `<a>` 的文本内容**才能拿到真正的 `tcp://host:port`。
本工具用标准库 `html.parser` 做结构化解析来避开这个坑。

## 安全说明

### 密码怎么存的

| 平台 | 方案 | 说明 |
|---|---|---|
| **Windows** | **DPAPI**（`CryptProtectData`） | OS 级加密，与「Windows 凭据管理器」同源。密钥由系统托管，密文**只有当前 Windows 用户**能解密，其他账户和其他机器都无法还原。DPAPI 失败时程序**直接报错**，绝不降级为弱保护。 |
| 其他平台 | PBKDF2 + SHA256-CTR + HMAC | 真正的对称加密：随机密钥文件（权限 `0600`）+ PBKDF2 派生 + 加密后 MAC 校验。仅作兜底，Windows 上始终优先 DPAPI。 |

存储位置：`%APPDATA%\cpolar-mobaxterm-sync\credentials.enc`

### 本程序不做的事

- ❌ 不上传你的密码、配置或任何数据到第三方服务器
- ❌ 不修改 `MobaXterm.ini`，不碰运行中的 MobaXterm 进程
- ❌ 不修改 `.moba` 文件里除 host/port 以外的任何字段
- ❌ 不在日志或界面明文显示密码

### 数据都存在哪

| 文件 | 内容 |
|---|---|
| `%APPDATA%\cpolar-mobaxterm-sync\config.json` | 非敏感配置（邮箱、路径、间隔等） |
| `%APPDATA%\cpolar-mobaxterm-sync\credentials.enc` | **加密后**的密码 |
| `<你的>.moba.bak_时间戳` | 会话文件备份（自动保留最近 10 份） |
| `HKCU\...\Run\CpolarMobaSync` | 开机启动项（仅当勾选时写入） |

以上路径均已加入 `.gitignore`，不会被误提交。

## 项目结构

```
cpolar-mobaxterm-sync/
├── src/
│   ├── main.py           # 入口：解析参数、加载配置、启动 GUI 或托盘
│   ├── config.py         # 配置读写（%APPDATA%/cpolar-mobaxterm-sync/config.json）
│   ├── crypto.py         # 凭据加密（DPAPI / PBKDF2 兜底）
│   ├── cpolar_client.py  # 读取 cpolar：云控制台 + 本机 API
│   ├── mobaxterm.py      # 解析与定点更新 .moba 会话文件
│   ├── gui.py            # Tkinter 主窗口 + 设置窗口
│   └── tray.py           # 系统托盘、定时同步、开机启动
├── tests/                # 单元测试（标准库 unittest）
│   └── fixtures/
├── build.spec            # PyInstaller 打包配置
└── README.md
```

## 开发与测试

```bash
python -m unittest discover -s tests -v
```

测试覆盖：配置读写、凭据加密（含明文泄漏检查与篡改检测）、云控制台登录与表格解析、
`.moba` 定点更新与备份清理、系统托盘交互、定时同步线程、开机启动注册表与命令可执行性。

## 常见问题

**Q：提示「未找到会话文件(.moba)」**
A：设置里的路径没填对。用「浏览」选中你导出的 `.moba` 文件。

**Q：提示「cpolar 云控制台登录失败」**
A：账号密码不对，或者多次失败触发了验证码。程序保存了凭据，通常只需重填一次。

**Q：同步成功了但 MobaXterm 里地址没变**
A：需要**重新导入**该 `.moba` 文件——MobaXterm 不会自动感知外部文件变化。

**Q：能改成写 `MobaXterm.ini` 吗？**
A：技术上可行，但不建议：`MobaXterm.ini` 会被运行中的程序在退出时覆盖，且格式随版本变化。

## License

MIT
