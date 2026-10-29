"""Tkinter 主窗口与设置窗口（仅依赖标准库 tkinter）。

主窗口：选择 SSH 会话、立即同步（手动，会弹窗预览确认）、设置。
设置窗口：cpolar 邮箱/密码（加密保存）、.moba 会话文件路径、读取源、自动间隔、开机启动。
"""

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
    def __init__(self, master, config, sync_func):
        if not _TK_AVAILABLE:
            raise RuntimeError(
                "当前环境缺少 tkinter，无法启动 GUI。请使用包含 Tk 的标准 Python 安装。"
            )
        self.master = master
        self.config = config
        self.sync_func = sync_func
        master.title("cpolar → MobaXterm 同步")
        master.geometry("460x240")

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

        ttk.Button(master, text="立即同步", command=self.do_sync).grid(
            row=2, column=0, padx=10, pady=10
        )
        ttk.Button(master, text="设置", command=self.open_settings).grid(
            row=2, column=1, padx=10, pady=10
        )

        self.refresh_sessions()

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
        SettingsWindow(self.master, self.config, self.refresh_sessions)


class SettingsWindow:
    def __init__(self, parent, config, on_saved):
        self.config = config
        self.on_saved = on_saved
        top = tk.Toplevel(parent)
        top.title("设置")
        top.geometry("490x390")
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
        ttk.Label(top, text="自动同步间隔(分)").grid(
            row=r, column=0, sticky="w", padx=10, pady=4
        )
        ttk.Entry(top, textvariable=self.v_interval, width=10).grid(
            row=r, column=1, sticky="w", padx=10
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

        # 开机启动：仅在用户改动该选项时调用外部命令，且失败要明确告知
        if self.v_startup.get() != self._startup_before:
            import tray

            ok, msg = tray.set_startup(self.v_startup.get())
            if not ok:
                messagebox.showwarning("开机启动", msg)

        self.top.destroy()


def run_gui(config, sync_func):
    if not _TK_AVAILABLE:
        raise RuntimeError(
            "当前环境缺少 tkinter，无法启动 GUI。请使用包含 Tk 的标准 Python 安装。"
        )
    root = tk.Tk()
    MainApp(root, config, sync_func)
    root.mainloop()
