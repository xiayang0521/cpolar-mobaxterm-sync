"""程序入口。

用法：
    python src/main.py              # 打开 GUI（同时后台自动同步）
    python src/main.py --minimized   # 仅系统托盘 + 后台自动同步（开机启动用）
"""

import argparse

import config as cfg
import gui
import tray


def main():
    parser = argparse.ArgumentParser(description="cpolar → MobaXterm 自动同步")
    parser.add_argument(
        "--minimized", action="store_true", help="启动到系统托盘（开机启动用）"
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
        # 托盘模式：托盘内部已启动自动同步线程
        tray.start_tray(config, sync_func, minimized=True)
    else:
        # GUI 模式：打开窗口，同时启用后台自动同步
        tray.start_auto_sync(config, sync_func)  # notify=None -> 静默
        gui.run_gui(config, sync_func)


def _sync_startup_state(config):
    """把注册表中的开机启动项与配置对齐；失败只记录不阻断启动。"""
    try:
        desired = bool(config.get("start_with_windows", False))
        actual = tray.startup_registered()
        if desired == actual:
            return
        ok, msg = tray.set_startup(desired)
        print(f"[开机启动] {'已对齐' if ok else '对齐失败：' + msg}")
    except Exception as e:  # pragma: no cover - 开机启动是附加功能
        print(f"[开机启动] 检查异常（不影响主流程）：{e}")


if __name__ == "__main__":
    main()
