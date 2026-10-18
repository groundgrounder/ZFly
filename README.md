# ZFly

独立运行的原生代理客户端。ZFly 自己管理订阅、配置、代理核心、持久化设置和系统代理，不需要安装或运行 FlClash，也无需填写管理桥地址或控制器密钥。

macOS 使用 SwiftUI，支持 Liquid Glass 与系统材质回退；Linux 使用 GTK 4 / libadwaita。代理协议由应用自带的 FlClash Go 核心或 mihomo 提供。

<img src="assets/icons/zfly-256.png" width="160" height="160" alt="ZFly 应用图标">

## 使用

1. 打开 ZFly，在“配置与订阅”中添加 **Clash 格式订阅**、导入 YAML，或创建自定义配置。
2. 选择配置，点击“启动代理”。代理组中可选择节点与测速。
3. 按需开启“系统代理”，让遵循系统代理设置的应用使用 ZFly。默认混合端口为 `127.0.0.1:7890`。
4. 停止代理或退出时，ZFly 恢复仍由自己持有的系统代理设置。

`--safe-mode` 仅供离线演示界面。完整说明与功能范围见 [docs/usage.md](docs/usage.md)。

## 已实现

- 应用自动启动自己的本地服务和核心；macOS 完整包包含 Python、核心和地理数据。
- 订阅下载、定时更新、ETag、流量与到期信息；本地 YAML 导入、编辑、导出；自定义节点和代理组。
- 代理启动与停止、规则 / 全局 / 直连模式、节点选择与测速、连接查看与关闭。
- macOS 仪表盘、流量趋势、菜单栏快捷操作、主题与透明材质偏好。
- macOS 登录项与 Linux 桌面会话登录启动，可选择启动时隐藏窗口。
- macOS 界面支持中文与 English；默认跟随系统，可在“设置 → 外观 → 语言”切换。
- 持久化核心设置、DNS / hosts、配置覆盖、追加规则、提供者与 Geo 资源更新、网络诊断。
- 本地备份与恢复、HTTPS WebDAV；私有数据权限、原子保存、过期写入拒绝、配置失败回滚和系统代理恢复记录。

尚未达到 FlClash 全功能对等：macOS 权限助手与 TUN、JavaScript 脚本、全局热键、Wi-Fi 策略、二维码导入和应用自更新尚未实现。Linux 的 TUN 需要额外核心权限，且本次没有完成 Linux 平台运行验证。

## macOS 构建

需要 Swift 6、macOS 26 或更新 SDK。最低部署目标为 macOS 14；旧系统运行兼容性仍需实机验证。

```sh
sh macos/build-app.sh
open build/macos/ZFly.app

# 离线界面演示；不启动运行时、不改系统网络
open build/macos/ZFly.app --args --safe-mode
```

输出为 `build/macos/ZFly.app`，可以移动到“应用程序”目录。包使用本机临时签名，正式发布还需开发者签名与公证。

打包需要可重定位的 CPython 3.12 和独立核心二进制：

```sh
ZFLY_PYTHON_HOME=/path/to/python-build-standalone \
ZFLY_CORE=/path/to/ZFlyCore \
ZFLY_CORE_KIND=ipc \
sh macos/build-app.sh
```

也可把依赖放入 `build/dependencies/python` 和 `build/dependencies/ZFlyCore`。标准 mihomo 使用 `ZFLY_CORE_KIND=mihomo`。脚本可查找本机 uv 安装的 CPython；如果没有指定核心，还可在**构建时**从已安装 FlClash 复制核心到 ZFly 包内，成品不依赖原应用。地理数据默认复制 `FlClash/assets/data`，可通过 `ZFLY_GEODATA` 指定其他目录。

## Linux / NixOS

```sh
nix build .#
nix run .#
nix run .# -- --safe-mode
```

`flake.nix` 为 `x86_64-linux` 和 `aarch64-linux` 配置 GTK、Python、独立 mihomo、桌面入口与图标。GNOME 系统代理通过 gsettings 管理。其他桌面可手动设置本地代理。当前环境没有 Nix、GTK 和 PyGObject，尚未验证 Linux 构建与运行。

已有 GTK 4、libadwaita 和 PyGObject 时可从源码开发运行：

```sh
PYTHONPATH=linux:runtime ZFLY_CORE=/path/to/mihomo ZFLY_CORE_KIND=mihomo python3 -m zfly
PYTHONPATH=linux:runtime python3 -m zfly --safe-mode
```

## 验证

```sh
# 无网络、无系统代理修改的回归测试
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=runtime python3 -m unittest discover -s runtime/tests -v

# 临时数据目录、仅回环连接、只启动和清理测试自己的核心
python3 tools/verify-runtime.py --app build/macos/ZFly.app
```

2026-10-18：macOS Apple Silicon 发布包构建、签名和资源校验通过；24 项离线测试在应用自带 Python 中通过；应用复制到含空格的新路径后，运行时、原生模块和 TLS 证书加载通过。当前沙箱禁止本机 socket 绑定，真实核心端到端测试返回跳过（退出码 77）；安全模式 GUI 直接启动在系统应用注册阶段退出（退出码 -6），LaunchServices 启动返回 -10827，未能完成视觉检查。实际外部代理连接、系统代理接管、TUN、旧版 macOS 和 Linux 均未在本次验证。

## 源码与许可

`macos/` 为 SwiftUI 应用，`linux/zfly/` 为 GTK 应用，`runtime/zfly_runtime/` 为共享独立运行时，`assets/icon.svg` 为可编辑图标。图标通过 `python3 tools/render-icons.py` 生成。

`FlClash/` 是保留历史与工作树的独立上游仓库，本次改动不修改它。`integrations/flclash/` 和对应导出工具仅保留历史迁移资料，当前应用不使用管理桥。

ZFly 基于 FlClash / mihomo 相关工作继续开发，采用 [GPL-3.0](LICENSE)，保留原版权和许可。第三方 Python / PyYAML 许可随 macOS 应用一起打包。
