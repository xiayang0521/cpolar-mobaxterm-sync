"""Tkinter 主窗口与设置窗口（仅依赖标准库 tkinter）。

主窗口：选择 SSH 会话、立即同步（手动，会弹窗预览确认）、设置、最小化到托盘。
设置窗口：cpolar 邮箱/密码（加密保存）、.moba 会话文件路径、读取源、
          定时同步开关与间隔、开机启动。
"""

import datetime
import queue

try:
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog

    _TK_AVAILABLE = True
except Exception:  # 极简/无头环境可能缺失 Tk
    tk = ttk = messagebox = filedialog = None
    _TK_AVAILABLE = False

import config as cfg
import mobaxterm as mb


class MainApp:
    def __init__(self, master, config, sync_func, scheduler=None, tray_app=None):
        if not _TK_AVAILABLE:
            raise RuntimeError(
                "当前环境缺少 tkinter，无法启动 GUI。请使用包含 Tk 的标准 Python 安装。"
            )
        self.master = master
        self.config = config
        self.sync_func = sync_func
        # 调度器：驱动定时同步，并提供「下次同步时间」
        self.scheduler = scheduler
        # 托盘应用：用于「最小化到托盘」
        self.tray_app = tray_app
        self._hidden_to_tray = False
        # 托盘线程 -> GUI 主线程的命令通道（线程安全）
        self._tray_commands = queue.Queue()
        # 标记窗口是否已销毁，避免定时器回调在销毁后继续跑
        self._destroyed = False

        master.title("cpolar → MobaXterm 同步")
        master.geometry("560x210")
        master.minsize(560, 210)

        ttk.Label(master, text="SSH 会话：").grid(
            row=0, column=0, sticky="w", padx=10, pady=8
        )
        self.session_var = tk.StringVar(value=config.get("selected_session", ""))
        self.session_cb = ttk.Combobox(
            master, textvariable=self.session_var, state="readonly", width=28
        )
        self.session_cb.grid(row=0, column=1, padx=10, pady=8)
        ttk.Button(master, text="刷新", command=self.refresh_sessions).grid(
            row=0, column=2, padx=6
        )

        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(master, textvariable=self.status_var, foreground="blue").grid(
            row=1, column=0, columnspan=3, sticky="w", padx=10
        )

        # 定时同步状态：显示下次同步时间
        self.schedule_var = tk.StringVar(value="定时同步：未启用")
        ttk.Label(master, textvariable=self.schedule_var, foreground="#666666").grid(
            row=2, column=0, columnspan=3, sticky="w", padx=10
        )

        btn_frame = ttk.Frame(master)
        btn_frame.grid(row=3, column=0, columnspan=3, sticky="w", padx=6, pady=10)
        ttk.Button(btn_frame, text="立即同步", command=self.do_sync).grid(
            row=0, column=0, padx=4
        )
        ttk.Button(btn_frame, text="设置", command=self.open_settings).grid(
            row=0, column=1, padx=4
        )
        # 手动进托盘：GUI 与托盘图标共存，隐藏窗口后后台继续定时同步
        self.tray_btn = ttk.Button(
            btn_frame, text="最小化到托盘", command=self.minimize_to_tray
        )
        self.tray_btn.grid(row=0, column=2, padx=4)
        # X 键是「隐藏到托盘」，所以需要一个显式的退出入口
        ttk.Button(btn_frame, text="退出", command=self.on_exit).grid(
            row=0, column=3, padx=4
        )

        # 关闭窗口 = 隐藏到托盘（托盘菜单里才有「退出」），避免误关导致后台同步停掉
        master.protocol("WM_DELETE_WINDOW", self.on_close)

        # 托盘「显示主窗口」回调：托盘消息循环在后台线程，
        # 必须经由 show_from_tray 用队列 + 主线程轮询来执行
        if self.tray_app:
            self.tray_app.on_show_window = self.show_from_tray
            self.tray_app.on_quit = self.quit_from_tray

        self.refresh_sessions()
        self.refresh_schedule()
        self._tick = master.after(1000, self.refresh_schedule)
        # 主线程轮询托盘命令（Tk 的 after/event_generate 不能从别的线程调用）
        self._cmd_tick = master.after(200, self._pump_tray_commands)

    # ---- 定时同步状态 ----

    def refresh_schedule(self):
        """刷新「下次同步时间」显示。

        这个回调由 Tk 的 after 定时器驱动，窗口销毁后可能仍被调用一次，
        此时访问控件会抛 TclError 并在控制台刷噪音，因此整体包一层保护。
        """
        try:
            self._refresh_schedule_inner()
        except Exception:
            self._destroyed = True   # 窗口已不可用，不再续排定时器

    def _refresh_schedule_inner(self):
        if self._destroyed:
            return
        if not self.master.winfo_exists():
            self._destroyed = True
            return
        self._tick = self.master.after(1000, self.refresh_schedule)
        if not self.scheduler or not self.scheduler.is_running():
            self.schedule_var.set("定时同步：未启用")
            return
        nxt = self.scheduler.next_run_at()
        if nxt is None:
            self.schedule_var.set("定时同步：等待中…")
            return
        remain = max(0, int((nxt - datetime.datetime.now()).total_seconds()))
        self.schedule_var.set(
            f"定时同步：每 {self.scheduler.interval_seconds() // 60} 分钟"
            f" · 下次 {nxt.strftime('%H:%M:%S')}（{remain} 秒后）"
        )

    # ---- 托盘交互 ----

    def minimize_to_tray(self):
        """隐藏主窗口，保留托盘图标与后台定时同步。"""
        if not self.tray_app:
            self.status_var.set("托盘不可用，无法最小化到托盘")
            return
        self.master.withdraw()
        self._hidden_to_tray = True
        self.tray_app.notify(
            "已最小化到托盘，后台继续定时同步。\n右键托盘图标可显示主窗口或退出。",
            "cpolar 同步",
        )

    def show_from_tray(self):
        """从托盘恢复主窗口。

        托盘消息循环在后台线程，而 Tk 不是线程安全的：
        - `after()` 从非主线程调用会抛 "main thread is not in main loop"；
        - `event_generate()` 同样受此限制。
        因此改用**线程安全队列 + 主线程轮询**：后台线程只往队列里塞消息，
        由主线程的定时器（_pump_tray_commands）取出来执行。
        """
        try:
            self._tray_commands.put("show")
        except Exception:
            import traceback

            traceback.print_exc()

    def _pump_tray_commands(self):
        """在主线程处理来自托盘线程的命令。

        由 Tk 的 after 定时器驱动，窗口销毁后可能仍被调用一次，
        整体包一层保护以免在控制台刷 TclError 噪音。
        """
        try:
            self._pump_tray_commands_inner()
        except Exception:
            self._destroyed = True

    def _pump_tray_commands_inner(self):
        if self._destroyed:
            return
        try:
            while True:
                cmd = self._tray_commands.get_nowait()
                if cmd == "show":
                    self._show_window_now()
                elif cmd == "quit":
                    self._cleanup_and_exit()
                    return
        except Exception:
            pass  # 队列空
        # 窗口已销毁时不再续排定时器（否则 Tk 会刷 "invalid command name"）
        if not self.master.winfo_exists():
            self._destroyed = True
            return
        self._cmd_tick = self.master.after(200, self._pump_tray_commands)

    def quit_from_tray(self):
        """托盘「退出」：同样经由队列回到主线程执行。

        托盘菜单的「退出」是用户明确意图，不再弹确认框。
        """
        try:
            self._tray_commands.put("quit")
        except Exception:
            pass

    def _show_window_now(self):
        self.master.deiconify()
        self.master.lift()
        try:
            self.master.focus_force()
        except Exception:
            pass
        self._hidden_to_tray = False
        self.status_var.set("已从托盘恢复")

    def on_close(self):
        """点右上角 X：若有托盘则隐藏到托盘，否则真正退出。"""
        if self.tray_app:
            self.minimize_to_tray()
        else:
            self.on_exit()

    def on_exit(self):
        """真正退出程序（清理托盘图标与调度线程）。"""
        if not messagebox.askyesno(
            "退出", "确定要退出 cpolar 同步工具吗？\n退出后定时同步也会停止。"
        ):
            return
        self._cleanup_and_exit()

    def _cleanup_and_exit(self):
        """清理资源后退出（供「退出」按钮与托盘菜单共用）。"""
        # 停掉定时同步，避免进程退出后仍有后台动作
        if self.scheduler:
            try:
                self.scheduler.stop()
            except Exception:
                pass
        # 移除托盘图标，否则图标会残留在系统托盘直到鼠标悬停
        if self.tray_app:
            try:
                self.tray_app.stop()
            except Exception:
                pass
        self.destroy()

    def destroy(self):
        """真正退出（供托盘「退出」调用）。"""
        # 必须取消所有 after 定时器：窗口销毁后它们仍会触发，
        # 在 Tk 里报 "invalid command name ... refresh_schedule"
        for attr in ("_tick", "_cmd_tick"):
            try:
                handle = getattr(self, attr, None)
                if handle:
                    self.master.after_cancel(handle)
            except Exception:
                pass
            setattr(self, attr, None)
        # 标记已销毁，后续定时器回调据此自我终止
        self._destroyed = True
        try:
            self.master.destroy()
        except Exception:
            pass

    # ---- 同步 ----

    def refresh_sessions(self):
        path = self.config.get("session_file_path", "")
        moba = mb.find_session_file(path)
        if not moba:
            self.session_cb["values"] = []
            self.status_var.set(f"未找到会话文件(.moba)：{path or '（未设置）'}")
            return
        try:
            sessions = [s[0] for s in mb.list_ssh_sessions(moba)]
        except Exception as e:
            self.status_var.set(f"读取会话失败：{e}")
            return
        self.session_cb["values"] = sessions
        if self.session_var.get() not in sessions and sessions:
            self.session_var.set(sessions[0])

    def do_sync(self):
        label = self.session_var.get()
        if not label:
            messagebox.showerror("错误", "请先选择一个 SSH 会话（或点刷新）")
            return
        # 手动触发：弹窗预览确认
        ok, msg = self.sync_func(label, interactive=True)
        self.status_var.set(msg)
        if ok:
            messagebox.showinfo(
                "完成", msg + "\n\n请在 MobaXterm 中重新导入该 .moba 文件生效。"
            )
        else:
            messagebox.showerror("失败", msg)

    def open_settings(self):
        SettingsWindow(self.master, self.config, self.refresh_sessions, self.scheduler)


class SettingsWindow:
    def __init__(self, parent, config, on_saved, scheduler=None):
        self.config = config
        self.on_saved = on_saved
        self.scheduler = scheduler
        top = tk.Toplevel(parent)
        top.title("设置")
        top.geometry("520x440")
        self.top = top
        r = 0

        def add(label, var, row, secret=False, browse=False):
            ttk.Label(top, text=label).grid(
                row=row, column=0, sticky="w", padx=10, pady=4
            )
            ent = ttk.Entry(top, textvariable=var, width=34, show="*" if secret else "")
            ent.grid(row=row, column=1, padx=10, pady=4)
            if browse:
                ttk.Button(
                    top,
                    text="浏览",
                    command=lambda: var.set(
                        filedialog.askopenfilename(
                            filetypes=[("MobaXterm 会话", "*.moba")]
                        )
                    ),
                ).grid(row=row, column=2)
            return ent

        self.v_base = tk.StringVar(value=config.get("cpolar_base_url", ""))
        self.v_source = tk.StringVar(value=config.get("cpolar_source", "dashboard"))
        self.v_email = tk.StringVar(value=config.get("cpolar_email", ""))
        self.v_pw = tk.StringVar(value=cfg.get_password() or "")
        self.v_ini = tk.StringVar(value=config.get("session_file_path", ""))
        self.v_interval = tk.StringVar(value=str(config.get("auto_sync_interval_min", 30)))
        self.v_startup = tk.BooleanVar(value=bool(config.get("start_with_windows", False)))
        # 记录打开时的开机启动状态：只有用户改动它才需要调 schtasks
        self._startup_before = bool(config.get("start_with_windows", False))
        # 定时同步开关：默认与调度器实际运行状态一致
        self.v_auto = tk.BooleanVar(
            value=bool(scheduler.is_running()) if scheduler else False
        )

        add("本机cpolar地址(本地源)", self.v_base, r); r += 1
        ttk.Label(top, text="读取源").grid(row=r, column=0, sticky="w", padx=10, pady=4)
        src_cb = ttk.Combobox(top, textvariable=self.v_source, state="readonly", width=32)
        src_cb["values"] = ["dashboard", "local"]
        src_cb.set(self.v_source.get())
        src_cb.grid(row=r, column=1, padx=10, pady=4)
        ttk.Label(top, text="dashboard=云控制台(默认) / local=本机API").grid(
            row=r, column=2, padx=4
        )
        r += 1
        add("cpolar 邮箱", self.v_email, r); r += 1
        add("cpolar 密码", self.v_pw, r, secret=True); r += 1
        add("会话文件(.moba)路径", self.v_ini, r, browse=True); r += 1

        # ---- 定时同步 ----
        ttk.Label(top, text="定时同步").grid(
            row=r, column=0, sticky="w", padx=10, pady=4
        )
        ttk.Checkbutton(
            top, text="启用（按间隔自动同步）", variable=self.v_auto
        ).grid(row=r, column=1, sticky="w", padx=10, pady=4)
        r += 1
        ttk.Label(top, text="自动同步间隔(分)").grid(
            row=r, column=0, sticky="w", padx=10, pady=4
        )
        ttk.Entry(top, textvariable=self.v_interval, width=10).grid(
            row=r, column=1, sticky="w", padx=10
        )
        ttk.Label(top, text="最小 1 分钟；修改后立即生效").grid(
            row=r, column=2, sticky="w", padx=4
        )
        r += 1
        ttk.Checkbutton(top, text="开机启动", variable=self.v_startup).grid(
            row=r, column=0, columnspan=2, sticky="w", padx=10, pady=4
        )
        r += 1

        ttk.Button(top, text="保存", command=self.save).grid(
            row=r, column=0, padx=10, pady=10
        )
        ttk.Button(top, text="取消", command=top.destroy).grid(
            row=r, column=1, padx=10, pady=10
        )

    def save(self):
        try:
            interval = int(self.v_interval.get() or 30)
        except ValueError:
            interval = 30
        interval = max(1, interval)
        self.config["cpolar_base_url"] = self.v_base.get().strip()
        self.config["cpolar_source"] = self.v_source.get().strip()
        self.config["cpolar_email"] = self.v_email.get().strip()
        self.config["session_file_path"] = self.v_ini.get().strip()
        self.config["auto_sync_interval_min"] = interval
        self.config["start_with_windows"] = self.v_startup.get()
        cfg.save_config(self.config)
        if self.v_pw.get():
            cfg.set_password(self.v_pw.get())
        self.on_saved()

        # 定时同步：启停与间隔立即生效，无需重启程序
        if self.scheduler:
            self.scheduler.set_interval_minutes(interval)
            if self.v_auto.get() and not self.scheduler.is_running():
                self.scheduler.start()
            elif not self.v_auto.get() and self.scheduler.is_running():
                self.scheduler.stop()

        # 开机启动：仅在用户改动该选项时调用外部命令，且失败要明确告知
        if self.v_startup.get() != self._startup_before:
            import tray

            ok, msg = tray.set_startup(self.v_startup.get())
            if not ok:
                messagebox.showwarning("开机启动", msg)

        self.top.destroy()


def run_gui(config, sync_func, scheduler=None, tray_app=None, on_tray_ready=None):
    """打开 GUI 主窗口。

    scheduler：定时同步调度器（可为空）
    tray_app：托盘应用对象。传入时窗口可「最小化到托盘」，
               且点 X 只是隐藏到托盘（真正退出走托盘菜单）。
    on_tray_ready：GUI 就绪后的回调（用来在后台线程启动托盘消息循环）。
    """
    if not _TK_AVAILABLE:
        raise RuntimeError(
            "当前环境缺少 tkinter，无法启动 GUI。请使用包含 Tk 的标准 Python 安装。"
        )
    root = tk.Tk()
    app = MainApp(root, config, sync_func, scheduler=scheduler, tray_app=tray_app)
    if on_tray_ready:
        # 延后一帧再启动托盘：确保 root 已完全就绪，
        # 托盘回调调用 root.after() 时不会撞上初始化阶段
        root.after(100, on_tray_ready)
    root.mainloop()
