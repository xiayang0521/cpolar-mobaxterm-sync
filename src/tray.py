"""同步编排、自动同步、系统托盘与开机启动（仅依赖标准库）。

- make_sync_func：核心编排（cpolar 读取 → .moba 定点更新），interactive 控制是否弹窗确认。
- start_auto_sync：后台定时同步线程（无 GUI 环境静默写入 + 通知）。
- set_startup：用 schtasks 注册/注销开机启动。
- start_tray：Windows 系统托盘（ctypes），右键菜单「立即同步 / 退出」。
"""

import datetime
import os
import sys
import threading
import time

import config as cfg
import cpolar_client as cc
import mobaxterm as mb


def _tk_available():
    try:
        import tkinter  # noqa: F401
        return True
    except Exception:
        return False


def make_sync_func(config):
    """返回一个 sync(label='', interactive=True) -> (ok, msg)。"""

    def sync(label="", interactive=True):
        moba = mb.find_session_file(config.get("session_file_path", ""))
        if not moba:
            return False, "未找到会话文件(.moba)，请在设置里指定路径"

        # 1) 读取 cpolar 当前 SSH 隧道地址/端口
        try:
            client = cc.CpolarClient(
                config.get("cpolar_base_url", "http://127.0.0.1:9200"),
                config.get("cpolar_email", ""),
                cfg.get_password() or "",
                source=config.get("cpolar_source", "dashboard"),
            )
            host, port = client.get_ssh_tunnel("ssh")
        except cc.CpolarError as e:
            return False, str(e)

        # 2) 预览定点替换
        try:
            preview = mb.update_session(moba, host, port, label=label or None)
        except ValueError as e:
            return False, str(e)

        # 3) 手动模式弹窗确认；自动/托盘模式静默
        if interactive and _tk_available():
            import tkinter as tk
            from tkinter import messagebox

            ok = messagebox.askyesno(
                "确认修改 .moba 会话",
                f"会话：{preview['label']}\n\n改前：\n{preview['old_line']}\n\n改后：\n{preview['new_line']}\n\n确定写入？",
            )
            if not ok:
                return False, "已取消"

        # 4) 备份并写回
        try:
            backup = mb.apply_update(moba, preview["old_line"], preview["new_line"])
        except ValueError as e:
            return False, str(e)
        return True, f"已更新 {preview['label']} → {host}:{port}（备份：{backup}）"

    return sync


class SyncScheduler:
    """定时同步调度器。

    设计要点：用 Event.wait() 而非 time.sleep()，因此可以随时被唤醒——
    改了间隔、想立即同步一次、或要停止，都能立刻生效，不必等当前周期跑完。

    相比原先的无限 sleep 循环，解决了三个问题：
    - 改「自动同步间隔」后不必重启程序（等待中的计时会被唤醒重算）
    - 能在界面上显示「下次同步时间」
    - 能干净停止（线程随 stop() 退出，不留后台残留）
    """

    def __init__(self, config, sync_func, notify=None, on_result=None,
                 interval_sec=None):
        self.config = config
        self.sync_func = sync_func
        self.notify = notify          # 托盘气泡通知
        self.on_result = on_result    # 结果回调（GUI 刷新状态栏用）
        # interval_sec：仅供测试注入秒级间隔；一旦用户/代码显式改过配置，
        # 就以配置为准（否则设置窗口里改间隔不会生效）。
        self._interval_override = interval_sec
        self._override_cleared = False
        self._thread = None
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._next_run_at = None      # datetime，下一次同步的时间点

    # ---- 生命周期 ----

    def start(self):
        """启动调度线程（幂等）。"""
        if self._thread and self._thread.is_alive():
            return self._thread
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self._thread

    def stop(self, timeout=2.0):
        """停止调度线程并等待其退出。"""
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=timeout)
        self._thread = None
        self._next_run_at = None

    def is_running(self):
        return bool(self._thread and self._thread.is_alive())

    # ---- 参数 ----

    def interval_seconds(self):
        """当前配置的间隔（秒）；最小 1 秒。

        优先级：显式设置过的配置 > 测试注入的 override > 配置默认值。
        """
        if self._interval_override is not None and not self._override_cleared:
            return max(1, int(self._interval_override))
        try:
            minutes = int(self.config.get("auto_sync_interval_min", 30))
        except (TypeError, ValueError):
            minutes = 30
        return max(1, minutes) * 60

    def set_interval_minutes(self, minutes):
        """修改间隔（分钟）并立即让等待中的计时重新计算。"""
        self.config["auto_sync_interval_min"] = max(1, int(minutes))
        # 用户显式改过间隔后，测试注入的 override 不再生效
        self._override_cleared = True
        self._wake.set()

    def next_run_at(self):
        """下次同步的时间点；未运行时返回 None。"""
        with self._lock:
            return self._next_run_at

    def trigger_now(self):
        """立即触发一次同步（不改变原有节奏），返回 (ok, msg)。"""
        return self._run_once()

    # ---- 内部 ----

    def _loop(self):
        while not self._stop.is_set():
            delay = self.interval_seconds()
            with self._lock:
                self._next_run_at = datetime.datetime.now() + datetime.timedelta(
                    seconds=delay
                )
            # 用 wait 而非 sleep：set() 会立刻打断等待，用于重算间隔
            if self._wake.wait(timeout=delay):
                self._wake.clear()
                continue    # 间隔可能变了，重新计算后再等
            if self._stop.is_set():
                break
            self._run_once()

    def _run_once(self):
        """执行一次同步，全程兜住异常，保证调度线程不会因一次失败而死掉。"""
        try:
            ok, msg = self.sync_func(
                self.config.get("selected_session", ""), interactive=False
            )
        except Exception as e:
            ok, msg = False, f"同步异常：{e}"
        if self.notify:
            try:
                self.notify(msg, "cpolar 同步")
            except Exception:
                pass
        if self.on_result:
            try:
                self.on_result(ok, msg)
            except Exception:
                pass
        return ok, msg


def start_auto_sync(config, sync_func, notify=None, interval_sec=None):
    """启动后台定时同步线程（daemon）。notify 用于托盘气泡/日志。

    interval_sec：仅供测试注入秒级间隔；正常情况取配置里的分钟数。
    保留此函数是为了兼容既有调用方；新代码建议直接用 SyncScheduler。
    """
    if interval_sec is None:
        interval_sec = max(1, int(config.get("auto_sync_interval_min", 30))) * 60
    interval = max(1, int(interval_sec))

    def loop():
        while True:
            time.sleep(interval)
            try:
                ok, msg = sync_func(config.get("selected_session", ""), interactive=False)
            except Exception as e:
                # 绝不能让异常杀死后台线程，否则自动同步会静默失效
                msg = f"同步异常：{e}"
                ok = False
            if notify:
                notify(msg, "cpolar 同步")

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return t


def _exe_path():
    """返回用于开机启动的可执行文件路径（**不带引号**）。

    打包成 exe 时返回 exe 本身；
    未打包时返回主程序脚本路径（真正的命令行由 _startup_command 组装）。
    """
    if getattr(sys, "frozen", False):
        return sys.executable
    # 只有当 argv[0] 确实是本项目的主程序时才能用它；
    # 否则（例如被 `python -m unittest` 调起时 argv[0] 是测试运行器路径）
    # 回退到 src/main.py，否则注册表项会指向一个错误的目标。
    script = sys.argv[0] if sys.argv and sys.argv[0] else ""
    if script and not script.startswith("-"):
        script = os.path.abspath(script)
        if os.path.basename(script).lower() == "main.py":
            return script
    # 回退：定位到本模块同级的 main.py
    fallback = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
    return fallback if os.path.exists(fallback) else sys.executable


def _startup_registry_path():
    """开机启动注册表位置：HKCU\\...\\Run（当前用户，无需管理员权限）。"""
    return "Software\\Microsoft\\Windows\\CurrentVersion\\Run"


def _startup_command():
    """注册表里写入的启动命令：托盘模式静默启动。

    引号只能加一层——曾因 _exe_path 已带引号、这里又套一层，
    写出 `""python.exe" "main.py"" --minimized`，Windows 登录时无法解析。
    """
    exe = _exe_path()
    if getattr(sys, "frozen", False):
        return f'"{exe}" --minimized'
    if exe.lower().endswith(".py"):
        # 未打包：必须带上解释器，否则注册表项只会指向 .py 文件无法执行
        return f'"{sys.executable}" "{exe}" --minimized'
    return f'"{exe}" --minimized'


def startup_registered():
    """检查注册表里是否已存在开机启动项；返回 bool。"""
    try:
        import winreg
    except ImportError:  # pragma: no cover - 非 Windows
        return False
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _startup_registry_path(), 0, winreg.KEY_READ
        ) as key:
            try:
                winreg.QueryValueEx(key, "CpolarMobaSync")
                return True
            except FileNotFoundError:
                return False
    except OSError:
        return False


def set_startup(enabled):
    """注册/注销开机启动。

    用注册表 Run 键（winreg，标准库）而非 schtasks 外部命令——
    理由：① 不依赖外部 exe，不会因策略拦截而失败；
          ② 不会闪控制台黑框；③ HKCU 键无需管理员权限。

    返回 (ok, message)，调用方应把结果告知用户，不静默失败。
    """
    try:
        import winreg
    except ImportError:  # pragma: no cover - 非 Windows
        return False, "仅支持 Windows 设置开机启动"

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            _startup_registry_path(),
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            if enabled:
                winreg.SetValueEx(key, "CpolarMobaSync", 0, winreg.REG_SZ, _startup_command())
                return True, "已开启开机启动（登录后自动运行）"
            try:
                winreg.DeleteValue(key, "CpolarMobaSync")
            except FileNotFoundError:
                pass  # 本来就没开，视为成功
            return True, "已关闭开机启动"
    except OSError as e:
        return False, f"设置开机启动失败：{e}"


# ---------------------------------------------------------------------------
# Windows 系统托盘（ctypes）；非 Windows 或初始化失败时降级为隐藏窗口常驻
# ---------------------------------------------------------------------------

try:
    import ctypes
    from ctypes import wintypes

    _HAS_WIN = True
except Exception:  # pragma: no cover
    _HAS_WIN = False

if _HAS_WIN:  # pragma: no cover - 仅在 Windows 上定义
    WM_TRAYICON = 0x8000 + 1
    NIM_ADD = 0x0
    NIM_MODIFY = 0x1
    NIM_DELETE = 0x2
    NIF_MESSAGE = 0x1
    NIF_ICON = 0x2
    NIF_TIP = 0x4
    NIF_INFO = 0x10
    ID_TRAY = 1
    ID_SYNC = 1001
    ID_SHOW = 1002
    ID_QUIT = 1003

    # 部分类型在 wintypes 中缺失（随 Python 版本变化），补齐别名
    if not hasattr(wintypes, "HCURSOR"):
        wintypes.HCURSOR = ctypes.c_void_p

    # 窗口过程函数指针类型（wintypes 不含 WNDPROC，需自行用 WINFUNCTYPE 定义）
    WNDPROC = ctypes.WINFUNCTYPE(
        wintypes.LPARAM,
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )

    class _NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("hWnd", wintypes.HWND),
            ("uID", wintypes.UINT),
            ("uFlags", wintypes.UINT),
            ("uCallbackMessage", wintypes.UINT),
            ("hIcon", wintypes.HICON),
            ("szTip", wintypes.WCHAR * 128),
            ("dwState", wintypes.DWORD),
            ("dwStateMask", wintypes.DWORD),
            ("szInfo", wintypes.WCHAR * 256),
            ("uTimeout", wintypes.UINT),
            ("szInfoTitle", wintypes.WCHAR * 64),
            ("dwInfoFlags", wintypes.DWORD),
            ("guidItem", ctypes.c_byte * 16),
            ("hBalloonIcon", wintypes.HICON),
        ]

    class _WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT),
            ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", wintypes.INT),
            ("cbWndExtra", wintypes.INT),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HCURSOR),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        ]

    def _declare_win32_apis(user32, shell32, kernel32):
        """显式声明 Win32 API 的 argtypes/restype。

        必须显式声明的原因：ctypes 默认把 Python int 当 32 位 c_int 传，
        而 LPARAM/WPARAM/HANDLE 在 64 位 Windows 上是 8 字节，
        不声明会导致「int too long to convert」并让消息循环崩溃。
        """
        user32.DefWindowProcW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
        ]
        user32.DefWindowProcW.restype = wintypes.LPARAM
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p,
        ]
        user32.RegisterClassW.restype = wintypes.ATOM
        user32.RegisterClassW.argtypes = [ctypes.POINTER(_WNDCLASSW)]
        user32.LoadIconW.restype = wintypes.HICON
        # 第二个参数是资源 ID（MAKEINTRESOURCE 语义，实为整数指针），
        # 声明为 c_void_p 才能直接传整数 ID；声明成 LPCWSTR 会报
        # "'int' object cannot be interpreted as ctypes.c_wchar_p"
        user32.LoadIconW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        user32.PostQuitMessage.argtypes = [ctypes.c_int]
        user32.GetMessageW.restype = ctypes.c_int
        user32.GetMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT,
        ]
        user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.CreatePopupMenu.restype = wintypes.HMENU
        user32.AppendMenuW.argtypes = [
            wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR,
        ]
        user32.TrackPopupMenu.restype = wintypes.BOOL
        user32.TrackPopupMenu.argtypes = [
            wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, wintypes.HWND, ctypes.c_void_p,
        ]
        user32.DestroyMenu.argtypes = [wintypes.HMENU]
        shell32.Shell_NotifyIconW.restype = wintypes.BOOL
        shell32.Shell_NotifyIconW.argtypes = [
            wintypes.DWORD, ctypes.POINTER(_NOTIFYICONDATAW),
        ]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]

    class _TrayApp:
        def __init__(self, config, sync_func, on_show_window=None, scheduler=None,
                     on_quit=None):
            self.config = config
            self.sync_func = sync_func
            # 回调：从托盘恢复 GUI 窗口。由 GUI 侧注入（跨线程需自行调度）
            self.on_show_window = on_show_window
            # 回调：请求退出程序。由 GUI 侧注入（跨线程需自行调度）
            self.on_quit = on_quit
            # 调度器：用于「立即同步」后刷新托盘提示里的下次同步时间
            self.scheduler = scheduler
            self.user32 = ctypes.windll.user32
            self.shell32 = ctypes.windll.shell32
            self.kernel32 = ctypes.windll.kernel32
            _declare_win32_apis(self.user32, self.shell32, self.kernel32)
            self.hwnd = None
            self.hicon = None
            self.wndclass_name = "CpolarMobaSyncTray"
            self._wndproc = None  # 保持引用，防止被 GC

        def _wnd_proc(self, hwnd, msg, wparam, lparam):
            try:
                if msg == WM_TRAYICON:
                    event = lparam & 0xFFFF
                    if event == 0x205:  # WM_RBUTTONUP -> 右键菜单
                        self._show_menu()
                    elif event == 0x201:  # WM_LBUTTONUP -> 立即同步
                        self._do_sync()
                    elif event == 0x203:  # WM_LBUTTONDBLCLK -> 显示主窗口
                        self._show_window()
                elif msg == 0x111:  # WM_COMMAND
                    cmd = wparam & 0xFFFF
                    if cmd == ID_SYNC:
                        self._do_sync()
                    elif cmd == ID_SHOW:
                        self._show_window()
                    elif cmd == ID_QUIT:
                        self._request_quit()
                elif msg in (0x10, 0x2):  # WM_CLOSE / WM_DESTROY
                    self._quit()
            except Exception:
                # 回调里抛异常会被 ctypes 吞掉并打印栈，掩盖真实问题；显式记录
                import traceback

                traceback.print_exc()
            return self.user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        def _show_window(self):
            """从托盘恢复主窗口。"""
            if self.on_show_window:
                try:
                    self.on_show_window()
                except Exception:
                    import traceback

                    traceback.print_exc()

        def _request_quit(self):
            """请求退出程序。

            托盘消息循环在后台线程，不能直接销毁 Tk 窗口（Tk 非线程安全），
            必须交回主线程处理。GUI 未接管时（例如纯托盘模式）才直接退出。
            """
            if self.on_quit:
                try:
                    self.on_quit()
                    return
                except Exception:
                    import traceback

                    traceback.print_exc()
            self._quit()

        def _do_sync(self):
            ok, msg = self.sync_func(
                self.config.get("selected_session", ""), interactive=False
            )
            self.notify(msg, "cpolar 同步")

        def notify(self, msg, title="cpolar 同步"):
            nid = _NOTIFYICONDATAW()
            nid.cbSize = ctypes.sizeof(_NOTIFYICONDATAW)
            nid.hWnd = self.hwnd
            nid.uID = ID_TRAY
            nid.uFlags = NIF_ICON | NIF_TIP | NIF_INFO
            nid.hIcon = self.hicon
            nid.uTimeout = 4000
            nid.dwInfoFlags = 0x1  # NIIF_INFO
            # 注意：c_wchar 数组字段读出来是 str，必须整串赋值，不能按项赋值
            nid.szInfo = (msg or "")[:255]
            nid.szInfoTitle = (title or "")[:63]
            self.shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

        def _show_menu(self):
            hmenu = self.user32.CreatePopupMenu()
            self.user32.AppendMenuW(hmenu, 0x0, ID_SHOW, "显示主窗口")
            self.user32.AppendMenuW(hmenu, 0x0, ID_SYNC, "立即同步")
            self.user32.AppendMenuW(hmenu, 0x0, ID_QUIT, "退出")
            self.user32.SetForegroundWindow(self.hwnd)
            pt = wintypes.POINT()
            self.user32.GetCursorPos(ctypes.byref(pt))
            self.user32.TrackPopupMenu(
                hmenu, 0x100, pt.x, pt.y, 0, self.hwnd, None
            )
            self.user32.DestroyMenu(hmenu)

        def _register_class(self):
            wc = _WNDCLASSW()
            wc.style = 0
            wc.lpfnWndProc = self._get_wndproc()
            wc.hInstance = self.kernel32.GetModuleHandleW(None)
            wc.lpszClassName = self.wndclass_name
            self.user32.RegisterClassW(ctypes.byref(wc))
            self._wc = wc

        def _get_wndproc(self):
            prototype = ctypes.WINFUNCTYPE(
                wintypes.LPARAM,
                wintypes.HWND,
                wintypes.UINT,
                wintypes.WPARAM,
                wintypes.LPARAM,
            )
            self._wndproc = prototype(self._wnd_proc)
            return self._wndproc

        def _create_window(self):
            self.hwnd = self.user32.CreateWindowExW(
                0,
                self.wndclass_name,
                "cpolar",
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                self.kernel32.GetModuleHandleW(None),
                0,
            )
            self.user32.ShowWindow(self.hwnd, 0)  # SW_HIDE

        def _add_icon(self):
            self.hicon = self.user32.LoadIconW(None, 32512)  # IDI_APPLICATION
            nid = _NOTIFYICONDATAW()
            nid.cbSize = ctypes.sizeof(_NOTIFYICONDATAW)
            nid.hWnd = self.hwnd
            nid.uID = ID_TRAY
            nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
            nid.uCallbackMessage = WM_TRAYICON
            nid.hIcon = self.hicon
            # 整串赋值：c_wchar 数组字段读出来是 str，不能按项赋值
            nid.szTip = "cpolar → MobaXterm 同步"[:127]
            self.shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))

        def _message_loop(self):
            msg = wintypes.MSG()
            while self.user32.GetMessageW(ctypes.byref(msg), 0, 0, 0) > 0:
                self.user32.TranslateMessage(ctypes.byref(msg))
                self.user32.DispatchMessageW(ctypes.byref(msg))

        def _quit(self):
            nid = _NOTIFYICONDATAW()
            nid.cbSize = ctypes.sizeof(_NOTIFYICONDATAW)
            nid.hWnd = self.hwnd
            nid.uID = ID_TRAY
            self.shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
            self.user32.PostQuitMessage(0)

        def run(self):
            self._register_class()
            self._create_window()
            self._add_icon()
            self._message_loop()

        def start_in_background(self):
            """在后台线程里跑托盘消息循环，返回该线程。

            必须在独立线程执行：GUI 模式下 Tk 的 mainloop() 占用主线程，
            而 Win32 消息循环是阻塞式的，两者不能同时跑在一个线程里。
            """
            thread = threading.Thread(target=self.run, daemon=True)
            thread.start()
            return thread

        def stop(self):
            """移除托盘图标并结束消息循环。"""
            self._quit()


def create_tray(config, sync_func, on_show_window=None, scheduler=None, on_quit=None):
    """创建托盘应用对象（不启动）。失败返回 None。

    调用方负责决定何时/在哪个线程跑它的消息循环。
    """
    if not _HAS_WIN:
        return None
    try:
        return _TrayApp(
            config, sync_func, on_show_window=on_show_window,
            scheduler=scheduler, on_quit=on_quit,
        )
    except Exception:
        return None


def start_tray(config, sync_func, minimized=False, on_show_window=None, scheduler=None):
    """启动系统托盘（阻塞）。非 Windows 或初始化失败时降级为隐藏窗口常驻。

    仅在「纯托盘模式」（--minimized）下使用；
    GUI 模式请用 create_tray() + start_in_background() 与 Tk 主循环共存。
    """
    if scheduler is None:
        scheduler = SyncScheduler(config, sync_func)
        scheduler.start()

    app = create_tray(config, sync_func, on_show_window=on_show_window, scheduler=scheduler)
    if app is not None:
        try:
            app.run()   # 阻塞消息循环
            return
        except Exception:
            pass

    # 降级：用 tkinter 隐藏窗口常驻（仅 Windows 有 tkinter）
    if _tk_available():
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        root.mainloop()
