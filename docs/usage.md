# ZFly 使用与功能范围

ZFly 是独立应用。启动时自动创建带随机密钥的私有本机服务和自己的代理核心；不连接其他应用，不读取 FlClash 的配置或数据库。7890 是默认流量代理端口，管理端口每次启动随机分配，无需用户配置。

macOS 版仅支持 Apple Silicon（arm64），不提供 Intel 版本。

## 日常使用

macOS 启动后停留在“概览”，没有配置时也不会自动切换页面。点击“添加订阅”，或使用左侧“订阅”进入订阅页面。左侧导航为“概览、订阅、代理、连接、资源、规则、工具、设置”。Linux 使用“概览、配置与订阅、代理、连接、自定义资源、规则与高级、网络工具、设置”；其中网络接管位于设置中。GTK 窄窗折叠为单页导航，可通过原生返回键回到侧栏。

支持 Clash / mihomo YAML 订阅链接和本地 YAML，不支持把普通网页或任意格式的节点链接直接当作订阅。首份配置自动选中，随后点击“启动代理”。

机场面板按下载时的 User-Agent 决定下发哪些协议。ZFly 默认以 mihomo 客户端身份（`clash.meta/…`）请求订阅，以获得 VLESS、Hysteria、TUIC、AnyTLS、Mieru 等节点。如果某个服务商仍缺少节点或拒绝下载（HTTP 403），可在“规则 → 启动与测试”中填写 `userAgent`（例如 `clash-verge/v2.4.2`）后更新订阅；留空使用默认值。更改只影响之后的订阅下载。

在“代理”中，Selector 可直接选择节点，URLTest / Fallback 可点击节点进行手动固定；已固定节点有“已固定”标记，点击“恢复自动”解除。选择按配置分别保存，重启核心或切回配置后恢复。Fallback 在节点失效时仍遵循核心的健康检查与故障转移行为；LoadBalance / Relay 等组由核心选择。

分组遵循配置顺序并隐藏标记为 hidden 的组；只有全局模式显示 GLOBAL。搜索覆盖各组及其成员。选择或切换模式后会刷新完整状态。节点和整组测速各自显示进度并可取消，期间仍可选节点；取消后在途请求可能运行到超时，但不会发布迟到结果。测速访问测试 URL，可在 macOS 的“规则 → 启动与测试”或 Linux 的“规则与高级 → 启动与测试”修改。规则、全局、直连模式可在概览或 macOS 菜单栏切换。

启动代理会开启本地代理监听。开启“系统代理”后，遵循系统 HTTP / HTTPS / SOCKS 设置的应用才能自动使用 ZFly；也可为单个应用填写 `127.0.0.1` 和混合端口。其他代理工具占用 7890 时，可在 ZFly 的网络接管设置中改端口，无需终止已有工具。

macOS 登录项若需要系统批准，会显示待批准状态，须到系统设置允许。macOS 关闭最后一个主窗口或设置窗口后，ZFly 隐藏 Dock 图标，通过菜单栏继续控制。点击菜单栏中的“打开 ZFly”可重新打开主窗口并恢复 Dock 图标；最小化窗口时仍保留 Dock 图标。设置 → 启动可将 ZFly 加入 macOS 登录项，也可选择启动时隐藏窗口；隐藏后仍可从菜单栏打开。Linux 的设置 → 应用启动可创建当前用户的 XDG Autostart 项；隐藏启动时不显示窗口，再次从应用启动器打开可唤回已有实例。退出应用会停止自己的核心并恢复代理设置。订阅自动更新只在 ZFly 运行期间执行。“启动与测试”中的 `autoRun` 表示 **启动 ZFly 时自动启动代理**，不表示系统开机启动。

macOS 与 GTK 均可在“设置 → 外观 → 语言”选择跟随系统、中文或 English。GTK 切换后立即重绘当前窗口与托盘并保留未保存草稿；未覆盖的运行时错误仍可能显示中文。

## 功能对照

| 范围 | 当前实现 | 限制 |
| --- | --- | --- |
| 独立运行 | 自己的服务、核心、状态目录与生命周期；macOS 打包完整运行时 | 不复用 FlClash 数据，需要重新导入配置 |
| 应用启动 | macOS 登录项、Linux 当前用户登录启动；可选择启动时隐藏窗口 | Linux 使用 StatusNotifierItem 托盘；桌面需提供托盘区域 |
| 订阅 | HTTP(S)、手动与定时更新、ETag、用量和到期信息、可配置 User-Agent | 用量信息取决于订阅响应头；下发的协议由服务商按 User-Agent 决定；Age 解密仅 IPC 核心提供 |
| 本地配置 | 导入、编辑、导出、选中和删除 | YAML 必须通过基础校验和核心应用校验 |
| 自定义配置 | 节点、代理组、引用检查、重命名、导出 | 使用 JSON 编辑资源；不执行 JavaScript |
| 代理 | 三种路由模式、Selector 选择、URLTest / Fallback 固定与解除、可取消测速、HTTP / SOCKS 混合监听 | 协议能力取决于打包的核心 |
| 连接 | 目标、协议、规则与链路信息，搜索、关闭连接 | 可用信息取决于核心与操作系统 |
| 网络 | IPv6、LAN、端口、macOS / GNOME 系统代理 | macOS TUN 权限助手和系统 DNS 接管未实现 |
| 规则与 DNS | hosts、核心 DNS、全局追加规则、配置追加规则和配置覆盖 | JSON / 逐行规则编辑；没有完整图形规则编辑器 |
| 资源 | 提供者、Geo 文件列表与更新 | 远程下载需网络；IPC 的更新命令是异步提交 |
| 诊断 | DNS 查询、规则查看、缓存清理、关闭连接、核心重启 | IPC 历史最多保留 500 条；标准 mihomo 驱动未接入实时日志历史 |
| 备份 | 本地 JSON、HTTPS WebDAV；恢复配置或完整设置 | 备份包含订阅与凭据，未加密；恢复会停止代理 |
| 界面 | macOS 仪表盘、实时流量趋势、菜单栏、主题和侧栏系统材质；GTK 原生导航、中英文与系统托盘 | Linux 流量图尚未实现；托盘需要桌面面板支持 |
| 桌面集成 | macOS 窗口内快捷键 | 系统服务级开机启动、全局热键、Wi-Fi 策略、二维码、深链和应用自更新未实现 |

## Linux 托盘与退出

GTK 版通过 StatusNotifierItem 协议向桌面注册托盘图标，菜单提供运行状态、打开窗口、启动／停止代理及退出。托盘采用与 macOS 菜单栏一致的单色纸飞机：运行时为实心、停止时为轮廓，并提供主题图标名和 32×32 位图回退。KDE、Noctalia 或启用了 tray 模块的 Waybar 可提供相应托盘区域；GNOME 需要支持 AppIndicator / StatusNotifierItem 的扩展。

检测到可用托盘时，关闭主窗口会隐藏窗口并保留代理；要停止应用并恢复系统代理，请选择托盘菜单的“退出 ZFly”。没有可用托盘时，关闭窗口仍会退出，登录启动的“隐藏窗口”也会回退为显示窗口。桌面托盘服务消失时，隐藏的窗口会重新显示；面板重启后图标会重新注册。从应用启动器再次打开 ZFly 也能显示已有窗口。

## 配置覆盖与自定义资源

普通订阅和导入配置使用自己的节点与代理组；“自定义资源”只用于自定义配置。节点库在自定义配置间共享，代理组归属单个自定义配置。删除被引用的资源会被拒绝，重命名会同步相关引用与已记住的选择。

macOS 的“规则”和 Linux 的“规则与高级”支持核心、DNS / hosts、Geo、TUN 路由覆盖、配置专属覆盖、追加规则、启动偏好和 WebDAV。全局追加规则在前，配置追加规则次之，随后是配置本身的规则。通用覆盖之后应用配置专属覆盖；网络接管页中的端口、模式、IPv6 等仍以 ZFly 保存的值为准。

ZFly 固定管理自己的私有控制接口、密钥和监听边界。下载的配置不能自行开启额外入站服务、系统 DNS 监听或写入系统时间；HTTP 提供者缓存放入自己的核心目录，本地提供者路径也须位于该目录内。TUN 路由覆盖不能替代权限授权。

影响核心配置的设置提交后会应用到自己的核心并原子保存；启动与测试等应用偏好不会重新加载核心。核心应用失败会尝试恢复原配置；保存失败同样恢复原状态。编辑带有版本检查，其他窗口或订阅更新改变内容后，可用“重新载入”取得最新版本后保存。状态刷新失败时保留最后一次状态并提示可能过期，打开的编辑器和草稿继续保留。订阅与自定义配置的合并结果为只读查看，可选中复制并用 Esc 关闭；本地文件可编辑，输入不启用智能引号等自动替换。

## 数据与恢复

macOS 数据目录为 `~/Library/Application Support/ZFly`；Linux 为 `$XDG_DATA_HOME/zfly`，默认 `~/.local/share/zfly`。

数据目录权限为 0700，状态文件和代理恢复记录为 0600。`state.json` 保存订阅、配置、节点选择、偏好和 WebDAV 凭据；`core/` 保存自己的核心配置、Geo 与提供者缓存。不要公开上传这些文件。

“工具”可导出本地备份，或通过 HTTPS WebDAV 上传。WebDAV 的 `password: null` 保留已有密码，空字符串清除密码。恢复前应确认覆盖范围；恢复完成后代理保持停止，检查配置后手动启动。单份配置最多 16 MiB；JSON 备份与恢复单独限制为 40 MiB，备份生成超限时会直接拒绝。

状态文件损坏时，运行时保留原文件并报告恢复命令。退出 ZFly 后，可用受信任的 ZFly JSON 备份离线恢复；命令不会启动核心。`--data-dir` 必须显式指向要修复的目录；原状态另存为该目录中的 `state.before-recovery-*.json`。macOS 完整包示例：

```sh
ZFLY_APP=/path/to/ZFly.app
PYTHONPATH="$ZFLY_APP/Contents/Resources/runtime" \
  "$ZFLY_APP/Contents/Resources/python/bin/python3" -B -m zfly_runtime \
  --data-dir /path/to/zfly-data --recover-state-from /path/to/ZFly-backup.json
```

开启系统代理前保存原值，停止和退出时只恢复仍与 ZFly 设置一致的字段。如果用户或其他软件后来修改了代理，ZFly 保留那些改动。运行时会监测自己的核心退出并尝试还原系统代理；整个应用异常退出后，下一次启动会使用恢复记录尝试还原。系统拒绝修改时会报告错误并保留记录。

## 演示模式

macOS `--safe-mode` 会启动包内 Python 运行时并传入 `--preview`，全部操作使用与正常模式相同的 Service、HTTP 鉴权、事务、revision 校验、回滚和原子保存路径。数据从共享 `runtime/zfly_runtime/preview.json` 生成，每次新建权限 0700 的临时目录，状态权限为 0600；忽略运行时 `--data-dir` 和 `--core`，不读写默认数据目录。应用偏好也单独保存在内存中，演示实例可与用户自己的 ZFly 同时运行。

只监听一个随机分配的 `127.0.0.1` 管理端口。演示核心不启动子进程、不监听代理端口，下载器返回内置 YAML 与用量响应头，系统代理适配器只记录调用。WebDAV 备份保存在内存中，Geo 和提供者更新返回模拟结果；端口预检、监听与 TUN 网卡检查不接触主机环境。退出或检测到父进程退出后删除临时数据；和其他进程一样，运行时遭到 SIGKILL 或机器断电时无法执行清理。

`--preview-state default|empty|stopped` 分别表示有示例配置并运行、有空配置列表、有配置但停止代理。SwiftPM 直接开发时无内置 Python，需同时设置 `ZFLY_DEV_PYTHON`（Python 可执行文件绝对路径）和 `ZFLY_DEV_RUNTIME`（包含 `zfly_runtime` 的目录绝对路径），再运行 `swift run ZFly --safe-mode`；缺失时会明确报错，不搜索系统 Python。这两个变量只影响安全模式。运行时也可单独通过 `python3 -m zfly_runtime --preview --ready-file /path/to/ready.json` 启动。

已知差异：

- 模拟核心只验证 YAML 与 schema；未知协议类型、协议所需字段和实际核心语义由真实核心检验，不支持 Age 解密。
- Selector 与自动组固定记录由内存核心提供，不模拟健康检查、故障转移或实际代理通信。
- 测速固定等待 450 ms，然后返回 fixture 延迟；流量按运行时间递增，连接来自示例数据，DNS 返回文档示例地址。不能用这些结果判断网络可用性。
- HTTP(S) URL、订阅、workspace、catalog、WebDAV 配置、过期写入等仍经过真实 Service 校验；外部服务错误、网络超时、系统权限和端口冲突需要正常模式的独立验证。
- 不修改系统代理、DNS 或 TUN，也不注册登录项；这些系统能力不能由演示结果证明。

Linux 这次继续使用自己的 PreviewManagement / PreviewController，未接入共享预览后端。

## 开发验证

构建参数与安装方式见根目录 [README](../README.md)。离线测试使用模拟核心与系统代理接口，不访问实际网络设置：

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=runtime python3 -m unittest discover -s runtime/tests -v
```

独立集成脚本只使用临时数据目录和回环端口，不启用 TUN、系统代理或外部下载；它检查包内核心、SOCKS5 握手、导入、Selector 选择、自动组固定与解除、配置切换、停止重启、持久化、备份恢复和鉴权：

```sh
python3 tools/verify-runtime.py --app build/macos/ZFly.app
```

macOS 安全模式通过隔离演示运行时提供示例数据，截图等待管理连接和首个核心快照，超时为 20 秒。可导出本次启动的窗口内容用于视觉检查：

```sh
build/macos/ZFly.app/Contents/MacOS/ZFly --safe-mode --render-preview /tmp/zfly-overview.png
```

执行 `python3 tools/verify-preview.py --app build/macos/ZFly.app` 可为八个页面的三种初始状态生成 24 张截图，并记录本次启动的 PID、管理监听和退出后的目录清理。结果位于 `build/macos/preview-backend-review/`。本次共享预览后端结果见[验证记录](preview-backend-validation-2026-10-21.md)，此前结果见[审计修复验证](audit-validation-2026-10-21.md)。

GTK 交互回归需在带 GTK / libadwaita 的隔离图形会话内显式使用安全模式，并将 XDG 配置、数据和缓存目录指向临时目录：

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=linux:runtime python3 linux/tests/test_ui.py --safe-mode -v
```

测试只使用演示控制器，覆盖独立订阅导航、模式切换与失败恢复、无结果状态、弹窗校验、诊断结果、草稿保留、资源缓存、恢复后的状态更新、导出文件权限和窄窗布局。普通 unittest discovery 会跳过这些需要图形会话的测试。

GTK 安全模式使用独立的应用实例标识，与正常运行的 ZFly 分开。保存期间表单会等待操作结束后再允许关闭；YAML 和高级配置导出使用仅当前用户可读写的权限，覆盖已有文件时也会更新权限。

托盘协议回归另外需要独立的 D-Bus 会话，覆盖图标注册、菜单、隐藏恢复、退出清理及面板重启。仍需使用临时 XDG 目录和隔离显示：

```sh
dbus-run-session -- env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=linux:runtime python3 linux/tests/test_tray.py --safe-mode -v
```
