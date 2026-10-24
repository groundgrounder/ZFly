[English](README.md) | **简体中文**

# ZFly

独立运行的原生代理客户端。ZFly 自己管理订阅、配置、代理核心、持久化设置和系统代理，不需要安装或运行 FlClash，也无需填写管理桥地址或控制器密钥。

macOS 版仅支持 Apple Silicon（arm64），使用 SwiftUI 与系统材质，不提供 Intel 版本。Linux 使用 GTK 4 / libadwaita。代理协议由应用自带的 FlClash Go 核心或 mihomo 提供。

<img src="assets/icons/zfly-256.png" width="160" height="160" alt="ZFly 应用图标">

## 使用

1. 在 macOS 的“订阅”或 Linux 的“配置与订阅”中添加 Clash 格式订阅、导入 YAML，或创建自定义配置。
2. 选择配置，点击“启动代理”。在代理页选择节点、固定自动组节点和测速。
3. 按需开启“系统代理”，让遵循系统代理设置的应用使用 ZFly。默认混合端口为 `127.0.0.1:7890`。
4. 停止代理或退出时，ZFly 恢复仍由自己持有的系统代理设置。

macOS 的 `--safe-mode` 启动隔离的演示运行时，使用临时数据目录和模拟核心，退出时删除数据；不启动真实核心、不访问外部网络、不修改系统代理。Linux 仍使用原有演示适配器。完整说明与功能范围见 [使用说明](docs/usage.md)。

## 已实现与限制

- 自动启动自己的私有本地服务和核心；macOS 完整包包含 Python、核心、地理数据和许可证。
- 订阅下载、定时更新、ETag、用量和到期信息；本地 YAML 查看、编辑、导出；自定义节点与代理组。
- 规则 / 全局 / 直连模式；Selector 选择、URLTest / Fallback 固定与恢复自动；可取消的节点和整组测速；连接查看与关闭。
- macOS 仪表盘、流量趋势、菜单栏、主题和侧栏透明材质；macOS 与 GTK 均支持中文与 English，运行时消息仍可能包含中文。
- macOS 登录项、Linux 当前用户登录启动与隐藏窗口；GTK 系统托盘和后台运行。这些能力的本次平台验证范围见[验证记录](docs/audit-validation-2026-10-21.md)。
- 持久化核心设置、DNS / hosts、配置覆盖、追加规则、提供者和 Geo 资源更新、网络诊断。
- 本地 JSON / HTTPS WebDAV 备份恢复；私有数据权限、原子保存、过期写入拒绝、配置失败回滚和系统代理恢复记录。

macOS TUN 权限助手、系统 DNS 接管、JavaScript 脚本、全局热键、Wi-Fi 策略、二维码和应用自更新尚未实现。Linux TUN 需要额外核心权限，真实网络接管与系统代理恢复仍需平台验证。旧版 macOS 与 aarch64 Linux 尚未完成本轮验证。

## macOS 构建

在 Apple Silicon Mac 上构建，需要 Swift 6、macOS 26 或更新 SDK。最低部署目标为 macOS 14；旧系统兼容性仍需实机验证。

```sh
sh macos/build-app.sh

# 隔离演示运行时；直接启动二进制，与已运行的实例分开
build/macos/ZFly.app/Contents/MacOS/ZFly --safe-mode
```

直接从 SwiftPM 开发演示界面时，必须显式指定 Python 和共享运行时目录（这两个变量只在 `--safe-mode` 下读取）：

```sh
cd macos
ZFLY_DEV_PYTHON=/absolute/path/to/python3 \
ZFLY_DEV_RUNTIME=/absolute/path/to/zfly/runtime \
swift run ZFly --safe-mode
```

演示操作经过真实 Service 的校验、版本检查、事务、回滚与临时持久化。模拟核心只做 YAML / schema 校验，不能检查真实核心的协议语义（例如未知代理类型），也不执行真实测速、DNS、WebDAV、Geo 或提供者下载；不提供 Age 解密。详见[演示模式差异](docs/usage.md#演示模式)。

输出为 `build/macos/ZFly.app`。包使用本机临时签名，正式发布还需开发者签名与公证。

打包需要可重定位的 CPython 3.12、独立核心、四个非空 Geo 数据文件和两份核心许可证：

```sh
ZFLY_PYTHON_HOME=/path/to/python-build-standalone \
ZFLY_CORE=/path/to/ZFlyCore \
ZFLY_CORE_KIND=ipc \
ZFLY_GEODATA=/path/to/geodata \
ZFLY_CORE_LICENSE=/path/to/FlClash-LICENSE \
ZFLY_MIHOMO_LICENSE=/path/to/mihomo-LICENSE \
sh macos/build-app.sh
```

Python 可放在 `build/dependencies/python`，脚本也查找本机 uv 的 CPython 3.12。核心默认查找 `build/dependencies/ZFlyCore` 或 `build/dependencies/mihomo`；标准 mihomo 指定 `ZFLY_CORE_KIND=mihomo`。构建不从已安装应用读取核心。

Geo 默认依次查找 `FlClash/assets/data`、`build/flclash-host/assets/data`，需包含 `ASN.mmdb`、`GEOIP.dat`、`GEOIP.metadb`、`GEOSITE.dat`。许可证分别查找上述源码根目录的 `LICENSE` 和 `core/Clash.Meta/LICENSE`。环境变量可覆盖这些路径；资源缺失时构建失败。构建结束校验可执行文件、元数据、图标、预览、本地化、Geo、许可证与核心校验和。

## Linux / NixOS

在源码目录根部执行以下命令：

```sh
nix profile install .#
nix run .#
nix run .# -- --safe-mode

nix build .#
nix flake check
```

`flake.nix` 为 `x86_64-linux` 和 `aarch64-linux` 配置 GTK、Python、nixpkgs mihomo、桌面入口和图标，并在构建时运行测试。GNOME 系统代理由 gsettings 管理；其他桌面可手动设置本地代理。2026-10-24 已在 x86_64 Linux 完成 Nix 构建，并单独通过 48 项 GTK 界面测试与 12 项托盘测试，用安全模式验证中英文、宽窗、窄窗和原生返回导航；真实代理接管等缺口见[验证记录](docs/audit-validation-2026-10-21.md)。

安装 GTK 4、libadwaita 和 PyGObject 后可从源码开发：

```sh
PYTHONPATH=linux:runtime python3 -m zfly --safe-mode
# 正常运行时需独立核心
PYTHONPATH=linux:runtime ZFLY_CORE=/path/to/mihomo ZFLY_CORE_KIND=mihomo python3 -m zfly
```

### DEB 与 RPM 包（x86_64）

本地构建产物位于 `build/packages`，当前软件包仅支持 x86_64。在该目录中安装：

```sh
cd build/packages

# Debian / Ubuntu
sudo apt install ./zfly_0.2.0-1_amd64.deb

# Fedora / RHEL
sudo dnf install ./zfly-0.2.0-1.x86_64.rpm
```

## 验证

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=runtime python3 -m unittest discover -s runtime/tests -v
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=linux:runtime python3 -m unittest discover -s linux/tests -v
swift build --package-path macos
swift test --package-path macos
sh macos/build-app.sh
python3 tools/verify-preview.py --app build/macos/ZFly.app
python3 tools/verify-runtime.py --app build/macos/ZFly.app
```

集成脚本仅使用临时数据和回环端口，记录并清理自己的运行时与核心；不启用系统代理、TUN 或外部下载。GTK 图形交互验证方法见[使用说明](docs/usage.md#开发验证)。分阶段结果、测试对应关系和未验证部分见[审计修复验证](docs/audit-validation-2026-10-21.md)。

## 源码与许可

`macos/` 为 SwiftUI 应用，`linux/zfly/` 为 GTK 应用，`runtime/zfly_runtime/` 为共享独立运行时。两端预览数据的唯一来源为 `runtime/zfly_runtime/preview.json`。`assets/icon.svg` 为可编辑图标，通过 `python3 tools/render-icons.py` 生成。

`FlClash/` 是可选的外部上游 checkout，当前工作区没有此目录；存在时须保留其历史和工作树。`build/flclash-host/` 是只读源码快照。历史管理桥补丁、基准和迁移脚本位于 [integrations/flclash](integrations/flclash/README.md)，当前应用不使用它们。

ZFly 基于 FlClash / mihomo 相关工作继续开发，采用 [GPL-3.0](LICENSE)，保留原版权和许可。第三方 Python / PyYAML 及核心许可随 macOS 包分发。
