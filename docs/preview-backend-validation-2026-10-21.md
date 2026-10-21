# 共享演示后端验证（2026-10-21）

环境：macOS 27.0.1，Apple Silicon / arm64。基线 `b177293`。本轮只接入 macOS；未修改 `linux/zfly/`、`runtime/zfly_runtime/preview.json` 或只读上游快照。

## 自动验证

| 验证项 | 结果 | 证据与范围 |
| --- | --- | --- |
| `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=runtime python3 -m unittest discover -s runtime/tests -v` | 95 项通过 | 包括新增的 12 项 preview 测试；本轮最终日志 `/tmp/zfly-runtime-tests.log` |
| `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=linux:runtime python3 -m unittest discover -s linux/tests -v` | 79 项中 23 项通过、56 项按条件跳过 | 本机缺少 GTK / gi 与隔离 D-Bus 图形会话；日志 `/tmp/zfly-linux-tests.log` |
| `cd macos && swift build && swift test` | 构建通过，20 项测试通过 | 自动组固定与解除、配置写入及观察、连接关闭改为连接实际 Python preview Service；另测测速 busy / 取消、开发环境变量边界、独立实例和内存偏好。日志 `/tmp/zfly-swift-build.log`、`/tmp/zfly-swift-tests.log` |
| `sh macos/build-app.sh` | 通过 | 包内 Python、独立核心、Geo、许可证、元数据、图标、fixture、本地化、架构和依赖检查，以及临时签名的 deep / strict 校验通过。构建脚本导入 preview 入口验证新模块；日志 `/tmp/zfly-package-build.log` |
| `python3 tools/verify-preview.py --app build/macos/ZFly.app` | 24 / 24 通过 | 八页 × default / empty / stopped；记录 PID、监听及退出清理。日志 `/tmp/zfly-preview-check.log`，详细结果和截图位于 `build/macos/preview-backend-review/` |
| 直接 `swift run ... ZFly --safe-mode --render-preview …` | 通过 | 显式设置 `/usr/bin/python3` 与仓库 runtime 路径；未使用包内 Python。`swift-run.png`、`swift-run.log`、`swift-run-results.json` 保存截图和 PID / 清理证据 |
| `rg -n "managePreview\|validatePreviewProfileCommand\|workspacePreview\|previewCatalog\|PreviewCatalog\|PreviewWorkspace" macos/Sources` | 无匹配 | Swift 模拟管理逻辑已删除；Snapshot / ManagedState fixture 方法与 preview.json 符号链接保留 |
| 工作区检查 | 通过 | `git diff --check` 无空白错误；Linux 应用与上游快照无改动，未覆盖已安装应用，未 push |

`test_preview.py` 的 Service / FakeCore 测试覆盖 profile 增删改查、read / write、订阅更新、Selector、URLTest 与 Fallback 固定 / 解除、core.configure 重试、workspace 合法和非法写入、catalog 引用及重命名、network.save、running.set、工具、备份和完整 / 配置恢复。磁盘保存失败验证了配置与选择回滚。

这些操作测试将 socket、子进程、真实 SystemProxy runner 与 urllib 外部请求入口替换为抛错断言；单独运行实际管理 Server 的测试记录 bind，仅允许一次 `127.0.0.1:0`。还断言临时目录为 0700、状态为 0600、忽略指定 data-dir / core、退出删除目录。子进程集成通过 HTTP 调用 `/state`、`/action`、`/proxies` 并验证 secret 鉴权与父 PID 不匹配时的清理。

## 页面与生命周期复核

最终 24 次截图都直接执行仓库 `build/macos/ZFly.app/Contents/MacOS/ZFly`，未使用 `open`。每次只观察到本次 ZFly 及其 Python 子进程；Python 没有子进程，也没有 ZFlyCore / networksetup。每次只观察到一个 `127.0.0.1` 管理监听。正常退出后，应用和 Python PID 均消失，Python 数据目录及原生启动会话目录均不存在。PID 与路径详见本地 `results.json`，不含密钥或订阅凭据。

三张总览图为 `default-contact.png`、`empty-contact.png`、`stopped-contact.png`，已逐图检查：

- default：两份配置、42 / 86 ms 的示例节点、两条连接、运行状态与用量可见。
- empty：无配置、无代理组、无连接，仍能载入核心设置、workspace 和工具。
- stopped：保留两份配置及节点，代理停止、连接为空、速率为零。
- 资源库从空 catalog 开始；规则页显示真实 Service 返回的 DNS / hosts 文档，不再显示 Swift 预置文档。页面无启动错误或永久等待状态。

截图在连接成功且首个快照到达后生成，最长等待 20 秒。刚启动时流量图可能只有一个样本，图表会在后续两秒轮询中增长；不再预填人工曲线。运行时测试验证了计数随时间增长。自动截图退出前会先异步关闭本次运行时，再调用 AppKit 退出，以完成临时目录清理。

## 演示与真实模式差异

Service 的管理校验、revision、事务、原子保存和回滚一致。外部环境由适配器模拟：不启动真实核心，不绑定代理端口，不做端口占用 / 监听 / 网卡检查，不修改系统代理、DNS 或 TUN，也不访问订阅或 WebDAV 服务器。订阅返回内置 YAML / 用量头；WebDAV 归档在内存中；Geo 为标明用途的占位文件，提供者更新为模拟调用。

模拟核心只做 YAML / schema 校验，不检查未知代理类型、协议必需参数等真实核心语义，不支持 Age 解密。测速固定等待 450 ms，返回 fixture 延迟；DNS 与连接为示例结果，自动组不模拟实际健康检查和故障转移。演示不能证明网络可用性、系统代理恢复、TUN 权限或真实核心兼容性。SIGKILL / 断电无法执行运行时退出清理。

未在本轮验证：Linux GTK / 托盘实机交互、Linux 打包和网络接管、Intel macOS、旧版 macOS、真实核心和外部服务器、真实系统代理 / DNS / TUN、登录项注册。Linux 的已有 preview 适配器本轮保持不变。
