"""程序入口。

用法：
    python src/main.py                # 打开 GUI（带托盘图标，可手动最小化到托盘）
    python src/main.py --minimized    # 启动即进系统托盘（开机启动用）
    python src/main.py --no-tray      # 不注册托盘图标（纯 GUI，关掉即退出）

设计：GUI 与系统托盘**共存于同一进程**。
- Tk 的 mainloop() 占用主线程；
- Win32 托盘消息循环是阻塞式的，必须跑在独立线程；
- 托盘回调要操作 Tk 时，通过 root.after() 调度回主线程（Tk 非线程安全）。
"""

import argparse
import threading

import config as cfg
import gui
import tray


def main():
    parser = argparse.ArgumentParser(description="cpolar → MobaXterm 自动同步")
    parser.add_argument(
        "--minimized", action="store_true", help="启动到系统托盘（不开 GUI 窗口）"
    )
    parser.add_argument(
        "--no-tray", action="store_true", help="不注册系统托盘图标"
    )
    args = parser.parse_args()

    config = cfg.load_config()
    sync_func = tray.make_sync_func(config)

    # 开机启动状态对账：配置里的开关是"用户意图"，注册表里的项才是"系统现状"。
    # 两者可能不一致（例如配置已勾选但注册项从未写入），这里对齐一次。
    # 之所以能放在启动路径上：set_startup 用的是 winreg（进程内调用），
    # 不像早期版本的 schtasks 那样会因外部命令被拦截而连累程序启动。
    _sync_startup_state(config)

    if args.minimized:
        _run_tray_only(config, sync_func)
    else:
        _run_with_gui(config, sync_func, use_tray=not args.no_tray)


def _run_tray_only(config, sync_func):
    """纯托盘模式：不显示 GUI 窗口，常驻后台定时同步。"""
    scheduler = tray.SyncScheduler(config, sync_func)
    scheduler.start()
    tray.start_tray(config, sync_func, scheduler=scheduler)


def _run_with_gui(config, sync_func, use_tray=True):
    """GUI 模式：窗口 + 托盘图标共存，可手动最小化到托盘。"""
    # 先起调度器，GUI 与托盘都能显示「下次同步时间」
    scheduler = tray.SyncScheduler(config, sync_func)
    scheduler.start()

    tray_app = None
    if use_tray:
        # 托盘应用先建好；消息循环在 GUI 就绪后再放到后台线程跑，
        # 这样「显示主窗口」回调才能安全地拿到 root
        tray_app = tray.create_tray(config, sync_func, scheduler=scheduler)

    if tray_app is not None:
        gui.run_gui(
            config,
            sync_func,
            scheduler=scheduler,
            tray_app=tray_app,
            on_tray_ready=lambda: _start_tray_thread(tray_app),
        )
    else:
        # 托盘不可用：退化为纯 GUI + 后台定时同步
        gui.run_gui(config, sync_func, scheduler=scheduler)


def _start_tray_thread(tray_app):
    """在后台线程启动托盘消息循环。"""
    def runner():
        try:
            tray_app.run()
        except Exception:
            import traceback

            traceback.print_exc()

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    return thread


def _sync_startup_state(config):
    """把注册表中的开机启动项与配置对齐。

    对齐规则是**单向补齐**，不是双向强制一致：
    - 配置为 True、注册表没有 → 补注册（用户想开但没生效）
    - 配置为 False、注册表有 → **不动**。因为"配置为 False"可能只是
      配置被重置/换机，而注册表项是用户实际生效的设置；
      只有用户在设置窗口里明确取消勾选时，才应该删注册表（见 gui.SettingsWindow.save）。

    这样避免程序一启动就把用户已生效的开机启动悄悄删掉。
    """
    try:
        desired = bool(config.get("start_with_windows", False))
        actual = tray.startup_registered()
        if desired and not actual:
            ok, msg = tray.set_startup(True)
            print(f"[开机启动] {'已补注册' if ok else '补注册失败：' + msg}")
        elif actual and not desired:
            print("[开机启动] 注册表已存在但配置为关闭，保留现状（如需关闭请在设置里取消勾选）")
    except Exception as e:  # pragma: no cover - 开机启动是附加功能
        print(f"[开机启动] 检查异常（不影响主流程）：{e}")


if __name__ == "__main__":
    main()
