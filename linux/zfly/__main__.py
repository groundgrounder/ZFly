import argparse
import sys
from .i18n import tr


def main():
    parser = argparse.ArgumentParser(description=tr("ZFly GTK 原生网络客户端"))
    parser.add_argument("--safe-mode", action="store_true", help=tr("使用演示数据，不连接或更改核心"))
    parser.add_argument("--start-hidden", action="store_true", help=tr("启动时不显示主窗口"))
    args = parser.parse_args()
    try:
        from .app import Application
    except (ImportError, ValueError) as error:
        sys.exit(tr("需要 GTK 4、libadwaita 1.4+ 和 PyGObject，请使用项目根目录的 flake.nix。\n{error}", error=error))
    return Application(preview=args.safe_mode, start_hidden=args.start_hidden and not args.safe_mode).run([sys.argv[0]])


if __name__ == "__main__":
    sys.exit(main())
