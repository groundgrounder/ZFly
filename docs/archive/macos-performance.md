> 历史记录：仅描述当时的修改与验证。当前结果见 [审计修复验证](../audit-validation-2026-10-21.md)。旧构建截图与临时日志未归档。

# macOS 性能记录

2026-10-19：速率、流量总计、连接流量和订阅用量统一使用固定的英文 `B / KB / MB / GB / TB / PB / EB`，沿用 1024 进位。速率附加 `/s`，小数点固定为英文格式，零值显示 `0 B`。

这次处理的性能开销：

- `Store` 改为 Observation，核心配置、节点与连接分别观察；相同数据不重复发布，流量采样不会通知代理、订阅和设置页面。
- 页面保留筛选、草稿和滚动状态；隐藏的流量卡片与连接列表暂停接收实时数据，返回时读取最新状态。
- 连接列表每次更新只执行一次筛选，分隔线判断直接使用最后一条连接的 ID，避免在每一行重新筛选完整列表。
- 流量每两秒采样；版本、核心配置、节点和管理状态每五轮刷新一次，约十秒。手动刷新和操作完成后的刷新继续立即读取完整状态。稳定运行时，每五轮从 25 次请求减少为 9 次；这是请求数量的代码分析，不代表实测 CPU 降幅。
- 安全预览不启动轮询任务。

验证：Apple Silicon Release 构建、应用签名与元数据检查通过。七项 Swift 回归测试通过，包含英文单位边界、重复快照、连续 100 次流量变化的观察隔离、配置变化通知、预览修改与单接口流量请求。

构建：

```sh
ZFLY_SWIFTPM_DISABLE_SANDBOX=1 \
ZFLY_CORE=/path/to/standalone/ZFlyCore \
ZFLY_CORE_KIND=ipc \
ZFLY_GEODATA="$PWD/build/flclash-host/assets/data" sh macos/build-app.sh
```

本次构建从已安装的 ZFly 复制核心并打包独立运行时，未启动该核心。`ZFLY_SWIFTPM_DISABLE_SANDBOX=1` 用于当前环境禁止嵌套 `sandbox-exec` 的情况。

测试：

```sh
CLANG_MODULE_CACHE_PATH="$PWD/build/macos/modules" \
SWIFTPM_MODULECACHE_OVERRIDE="$PWD/build/macos/modules" \
swift test --package-path macos --disable-sandbox \
  --scratch-path build/macos/tests --cache-path build/macos/cache \
  --config-path build/macos/config --security-path build/macos/security \
  --manifest-cache local --disable-netrc --disable-keychain
```

安全模式 GUI 尝试在系统应用注册 `_RegisterApplication` 阶段以 SIGABRT 退出，未创建窗口。记录见 `build/macos/traffic-review/results.json`，因此本轮未完成截图、实际滚动交互或 CPU／能耗测量。未启动真实运行时、修改系统代理或结束用户进程；未修改 `FlClash/`。Intel、旧版 macOS 和 Linux 本轮未验证。

产物：`build/macos/ZFly.app`。用户已自行替换前一版；本轮生成的新包尚未安装。

## 连接与退出等待（2026-10-19）

针对“连接耗时、连接后立即退出也耗时”的反馈，修复了以下等待路径：

- IPC 核心握手后直接由服务加载保存的配置，省去空配置的首次加载。自动运行时也只加载保存配置一次，不再先停止后再加载一次。
- 自动订阅更新延后到运行时就绪一分钟后；下载移出事务锁，状态查询和退出无需等待订阅服务器。下载完成后重新检查配置版本和关闭状态，已编辑或删除的配置不会被旧响应覆盖；校验、应用、保存与失败回滚仍在同一事务内。
- 收到退出信号时，启动握手和 IPC 响应等待会检查停止事件，避免等待完整的 10 秒握手或 60 秒响应超时。Swift 先取消轮询，清理任务独立于已取消的启动任务，重复退出请求共享一次清理。
- 状态、DNS 和延迟查询的核心 HTTP 请求移出事务锁，退出不再等待这些只读请求的响应超时。持久设置修改仍受事务锁保护。
- HTTP 服务的关闭轮询从默认的 0.5 秒改为 0.05 秒。关闭自有核心时直接发送终止信号，移除发送信号前固定的 2 秒等待；保留正常清理窗口和必要时的进程回收。

验证：57 项 Python 回归与 7 项 Swift 回归通过。新增测试覆盖启动配置加载次数、退出期间取消 IPC／握手等待、慢状态响应不阻塞退出、慢订阅下载不阻塞状态和关闭、下载结果过期丢弃、失败回滚和自有进程清理。全部使用替身运行，无真实核心或主机代理操作。

只读检查时，用户的两份自动订阅均未到更新时间，因此自动更新阻塞不能作为这次现象的完整根因。保存配置的纯解析中位数约 34 毫秒，也未发现足以解释长时间等待的解析开销。系统代理通过 `networksetup` 逐项设置和按所有权恢复的真实耗时尚未测得；这一恢复步骤仍保留。

已用 `reverse-engineer-anything` 的本地 REA CLI 读取安装核心的 Go 构建元数据，证据 ID 为 `ev_65a203e8b0fae86102aae68cff1166c9d95f991e1d42210b63d03fbd53ad334e`，完整输出在 `/tmp/zfly-core-go-evidence.json`。该证据仅确认核心身份和构建信息；本机没有可用的原生反编译引擎，未把附近的历史源码当作安装二进制行为已验证的依据，也未改变 REA 配置。

隔离运行检查 `python3 tools/verify-runtime.py` 因沙箱禁止本地监听端口而以 77 跳过，未启动子进程。GUI 安全预览也受前述系统应用注册错误限制。因此尚无真实连接／退出秒数、系统代理或核心端到端实测，不能据离线测试承诺耗时降幅。没有结束用户正在运行的 ZFly、核心或其他应用。
