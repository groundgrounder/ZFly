# 2026-10-21 审计修复验证

本轮按 `build/codex-prompt.md` 的七个阶段执行。原审计报告属于被忽略的本地资料，不纳入提交。本记录是可跟踪的结果与测试索引；历史记录不代表当前版本的平台验证。

> 2026-10-24 附注：后续已在 x86_64 Linux 完成 Nix 构建，并单独通过 48 项 GTK 界面测试与 12 项托盘测试。下文“未执行”“跳过”仅描述 2026-10-21 的历史环境，不代表当前验证状态。

## 执行范围

订阅 User-Agent 改动先单独提交，随后各阶段独立提交，提交信息标明审计编号。未 push；未读取或连接用户运行中的 ZFly、用户状态和偏好；未覆盖已安装应用；未改宿主系统代理、TUN 或其他网络设置。真实核心验证只创建临时数据、回环监听和本任务的进程，并清理记录的 PID。`build/flclash-host/` 保持只读，忽略的缓存未清理。

## 已修复及验证对应

下表的 Python 测试位于 `runtime/tests/` 或 `linux/tests/`，Swift 测试位于 `macos/Tests/ZFlyTests/`。GTK 测试虽已增加，但本机跳过，不能视为界面验收通过。

| 编号 | 修复与回归证据 |
| --- | --- |
| E1、E2、E3 | `ManagementTests.testReadOnlyTextRemainsSelectableAndLiteral`；独立应用标识的安全模式编辑器实测：订阅 `editable=false`、`selectable=true`，Z / Tab 不改变文本，文本命中和选中 5 字符成功；本地文件可输入，智能引号关闭；只读 Esc 关闭通过。url / custom 使用“查看”，file 保留编辑。脏标记在文本变更时更新。 |
| E4、M5 | `test_state_read_during_transaction_returns_committed_snapshot`、`testManagementTimeoutAndReconnectRetainLastState`；超时保留最近状态、标记过期，重连不更换页面 identity；规则与资源的配置校验仅在有管理状态时执行。测试覆盖 Store 状态保留，未自动化整页草稿与超时弹窗的组合交互。 |
| E5 | `test_stale_profile_and_workspace_writes_are_rejected`；macOS 属性、资源编辑及 Linux 属性、文件编辑增加重新载入版本入口。界面入口经编译检查，Linux GTK 交互待验。 |
| R1 | `test_persisted_mutations_round_trip_through_state_store`、`test_storage_rejects_deeply_invalid_state_shapes`；统一 save / load / restore / workspace 的持久化校验。`test_recovery_cli_needs_no_ready_file_or_core_and_preserves_original`、`test_invalid_recovery_backup_leaves_original_untouched` 实际执行离线恢复 CLI，验证无核心、原文件另存、错误不覆盖和 0600 权限。第 7 阶段补正恢复模式无需 ready-file。 |
| R2 | `test_failed_proxy_enable_reenables_previous_lease_on_rollback`：失败时清除代理签名，使回滚重新恢复租约。使用替身接口，不触碰系统代理。 |
| R3 | `test_tunnels_are_stripped_and_cannot_be_added_by_relay`、`test_config_confines_control_listeners_and_provider_caches`：剥离 tunnels，监听边界由 ZFly 保存值控制。 |
| R4 | 真实 IPC 核心先确认删除节点返回 HTTP 400、缺组返回 HTTP 404；`test_selection_pruning_uses_live_nodes_and_keeps_backend_errors` 使用应用后的实时组成员裁剪选择，包含 provider 节点，其他核心错误继续抛出。 |
| R5 | `test_backup_has_its_own_limit_and_rejects_oversize_at_creation`：超过 16 MiB 的备份解码、40 MiB 独立限制、生成端拒绝超限、DAV 下载上限；两端恢复选择器同步为 40 MiB。`tools/verify-runtime.py` 覆盖实际小备份的生成与恢复；未做接近 40 MiB 的整包性能测试。 |
| R6 | `test_corrupt_proxy_lease_is_quarantined_without_host_commands`、`test_corrupt_lease_is_reported_without_running_restore_commands`：隔离损坏租约并继续启动，保留警告。 |
| R7 | `test_auto_update_survives_disk_error_without_exposing_paths`：保存 IO 错误不杀死自动更新循环，显示安全的可操作消息。 |
| R8 | `test_save_receipt_survives_retries_without_overwriting_later_edits`：回执仅记结果，重试重新读取当前状态。 |
| 3.1 低项 | `test_batch_subscription_download_does_not_hold_transaction_lock`、`test_app_preferences_do_not_reload_core_and_poll_reuses_profile_hash`、`test_yaml_alias_expansion_is_bounded_before_serialization`、`test_catalog_non_string_id_is_a_presentable_error`、`test_delete_profile_clears_update_errors`。分别覆盖批量下载锁、应用偏好与哈希缓存、YAML 展开预算、catalog 类型错误、删除更新错误记录。 |
| 2.1-B | Swift `testProxyGroupsFollowGlobalOrderAndVisibility`、Linux `test_order_visibility_and_cross_group_search`：按 GLOBAL.all 的配置顺序，隐藏 hidden 及非 global 模式的 GLOBAL。 |
| 2.1-C | `testActionWaitsForPollAndRunsFullRefresh` 覆盖轮询与动作竞态；Linux 保留待执行的完整刷新，GTK 现有 `test_refresh_started_before_mutation_cannot_overwrite_new_state` 待平台执行。 |
| 2.1-D | `testLatencyDoesNotBlockActionsAndCancels`、`test_delays_are_concurrent_and_do_not_block_state_or_mutations`：独立节点 / 组测速状态、取消和完整刷新。Linux 队列取消及迟到结果抑制已实现；最终复查修正整组中单节点取消误开第二请求、重叠组进度相互清除及配置切换迟到结果问题，新增 `test_cancel_node_in_group_does_not_start_another_request`、`test_overlapping_measurements_keep_progress_and_group_cancel_clears_it`（均 GTK 跳过）；原测速测试改为等待测速任务而非全局 busy。urllib 在途请求运行到超时，GTK 交互待验。 |
| Linux 缺组选项 | `test_proxy_picker_falls_back_when_selected_group_disappears`（GTK 跳过）：选中组消失后回落到首组。 |
| 4.1 | `test_geodata_and_licenses_are_required_and_override_is_explicit`、`test_python_patch_versions_are_sorted_numerically`；完整 macOS 构建、签名和产物清单校验通过。四份非空 Geo 数据和两份核心许可证已打包；版本统一来源、证书检查、可配置图标库路径、显式校验、Nix checks 已加入。Nix 本轮未执行。 |
| M1 | `testOwnedRuntimeTerminationEscalatesAndReaps`：自己启动的忽略 SIGTERM 子进程，超时升级 SIGKILL 后回收；stop 清空状态并清理会话，失败诊断不再丢弃。 |
| M2 | `testLoginApprovalIsNotShownAsEnabled`：待批准状态不显示已启用，激活窗口时刷新。没有实际注册或更改用户登录项。 |
| M3 | 运行时文本改为 `Text(verbatim:)`，固定 UI key 保留本地化；Swift 构建和测试通过，源码检查未留下报告中的动态 LocalizedStringKey。未完成全部英文运行时消息翻译。 |
| M4 | `testManagementWireDefaultsAllowMissingOptionalAdditions`：新增非身份字段缺省兼容，身份与 API 契约继续严格校验。 |
| 3.2 低项 | 安全模式独立实例实测确认并修复：编辑器弹窗中退出、关闭窗口后 reopen AppleEvent 重开。规则分类重置源文本模式、导出清错误、资源重新载入、端口提示和测速 URL 冗余已修正；Swift 编译与上述 Store / revision 测试覆盖相关基础行为，未自动化全部表单操作。 |
| 3.3 生命周期 | `test_close_during_start_cancels_without_a_process_race`、`test_stop_reaps_a_wedged_owned_child`：启动 / 停止同步、取消启动、超时回收。启动失败更新状态；预览关闭后重新激活创建新窗口。GTK 窗口路径待验。 |
| 3.3 历史 / 文件 / 异常 | `test_readonly_poll_preserves_buffer_selection`（GTK 跳过）；只读历史无变更不替换 buffer，更新时恢复选区与滚动；Gio 读取支持取消，关闭取消文件任务；异常记录类型与代码位置，不记录敏感参数；导出确认时读取当前草稿。 |
| 3.3 搜索 / 连接 / 低项 | `test_order_visibility_and_cross_group_search`；`test_connection_without_id_still_renders`、`test_unknown_choice_value_is_preserved`（后两项 GTK 跳过）。CSS 按 display 只安装一次，忙碌动作给出提示；预览窗口重新创建。 |
| Linux 编辑补项 | base64 与 UTF-8 解码移到工作线程，实验证实 BOM 会被 utf-8 保留后改用 utf-8-sig；大文本分批插入、移除双层滚动；名称 trim，关闭自动更新时不校验禁用的间隔，显示用量 / 到期 / 更新错误。大文本布局与响应性仍需 GTK 平台验证。 |
| 2.1-A | `test_automatic_fixed_and_unfixed_selections_rollback_and_relay`、`test_automatic_pins_apply_on_both_drivers_and_prune_stale_fixed`、Swift `testAutomaticPinAndUnfixInPreview`、Linux `test_automatic_pin_preview_and_unfix_encoded_transport`。真实 IPC 核心验证 selected-map、PUT / DELETE、fixed / now、核心重启、切换配置和含 `/`、`%`、emoji 的组名；包内集成脚本同样通过。回滚以 fixed 而非 now 为准，原来自动选择时恢复为 DELETE。 |
| 4.2 | 迁移脚本归档到 integrations；preview 唯一来源位于 runtime，Linux 直接读取、SwiftPM 通过源链接打包独立副本。`test_preview_has_one_source_and_migration_tools_are_archived`、`test_english_keys_are_referenced_or_interpolation_formats`；删除 40 个无引用英文 key，两篇评审归档并去除失效构建链接，保留 vendor / 图标 / 忽略缓存。 |
| 4.3 | README 移除 Liquid Glass 与已安装应用取核心说明，区分平台导航、更新 Geo / 许可证来源和能力边界；AGENTS 明确可选外部 checkout；usage 同步测速取消、排序、自动组、恢复与备份限制。本文承接验证历史；本地文档链接检查与完整包预览一致性检查通过。 |

## 确认不成立或无需补丁的部分

- **3.3“退出同步 wait 冻结 GTK 主线程”**：审计基线的 `_close` 已在 `zfly-shutdown` 工作线程调用 stop，并由 GLib 回到主线程。未重复迁移线程；实际修复启动竞态、超时不回收和状态清理。GTK 实机响应仍待验证。
- **2.1-A 的 IPC 逐组 PUT 兼容分支**：真实 selected-map 对两类自动组均生效，因此 IPC 不再额外 PUT。标准 mihomo 驱动保留逐组 PUT，但只有模拟测试，本机无法运行 nixpkgs mihomo。
- **3.2 Controller.delay 的 URL 决策**：基线运行时已覆盖请求中的 URL，未复现设置被绕过；仅删除 Swift 的冗余硬编码参数，`test_node_latency_uses_saved_test_url` 保留验证。

“第一组以下不能选择”的原始根因不在本次修复范围，也未认定不存在。没有据此改动选择流程或宣称解决；2.1-B / C / D 和自动组补齐各自验收。

## 当前检查结果

2026-10-21，在本机 Apple Silicon / macOS 上：

| 检查 | 结果 |
| --- | --- |
| 运行时 unittest | 83 / 83 通过 |
| Linux unittest | 共 79 项：23 通过，56 项因缺 gi / 显式安全图形会话跳过 |
| Swift build / test | 编译通过；16 / 16 XCTest 通过，无生产源码警告 |
| macOS 完整包 | Release 构建、临时签名、四份 Geo 与五份许可文件、元数据 / 图标 / 预览 / 本地化 / 核心哈希检查通过 |
| 包内运行时集成 | 通过：自己的核心启停、SOCKS5 本地握手、Selector 持久化、自动组固定 / 解除 / 切换 / 重启、备份恢复、鉴权、0600 状态与无代理租约残留 |
| 安全模式交互 | 隔离标识的临时源码副本验证只读 / 编辑属性、键盘、选区、Esc、弹窗退出、窗口 reopen；自有进程均退出 |
| Linux Nix 构建 | 本机无 nix，未执行；未验证修改后的 flake 求值、构建或 Linux 安装包 |

最终包位于 `build/macos/ZFly.app`，未安装到 Applications。包内集成最后记录的运行时 PID 为 9890、9906，退出时已回收自己的核心。GUI 自有实例 PID 7873、7891、7896 均正常退出；最终包的代理页（900×640）和订阅页（1100×760）通过独立标识安全预览并检查截图，PID 10356、10362 正常退出；Swift 强制终止测试最后 PID 10663 已回收。日志是任务临时文件，本文保留结论与可重复的命令，未提交临时数据。

重复检查命令见根 README。每阶段均运行运行时、Linux、Swift build / test：

| 阶段 | runtime | Linux 总数 / 跳过 | Swift |
| --- | --- | --- | --- |
| 1：E1–E5、M5 | 64 | 69 / 50 | 9 |
| 2：R1–R8、3.1 | 75 | 69 / 50 | 9 |
| 3：2.1-B / C / D | 75 | 70 / 50 | 12 |
| 4：4.1 | 77 | 70 / 50 | 12 |
| 5：M1–M4、3.2、3.3 | 77 | 76 / 54 | 15 |
| 6：2.1-A | 79 | 77 / 54 | 16 |
| 7：4.2 / 4.3、恢复 CLI 补验与 2.1-D 边界回归 | 83 | 79 / 56 | 16 |

## 无法在本机验证的部分

- **3.3 / Linux 编辑与 2.1-B / C / D 的 GTK 界面**：本机没有 gi，新增界面测试跳过。大文件分批插入、布局高度、Gio 取消、滚动与选区、测速并发、后台退出和托盘须在隔离 Linux 图形会话执行。
- **2.1-A / R4 的标准 mihomo 后端**：两类组的持久化与裁剪有模拟驱动测试，真实 nixpkgs mihomo、provider 节点与故障节点健康切换尚未验收。IPC 健康的本地节点已通过；Fallback 对失效节点可能取消固定或选择替代节点，这是核心行为，不能承诺 fixed 与 now 在故障时总一致。
- **4.1 / 4.2 Linux 包**：未执行 Nix 求值或构建；aarch64 未验。Geo 文件已检查非空，网络受限时首次 Geo 加载耗时没有测量。
- **M1 / R2 / R6 系统代理、M2 登录项、TUN**：只用替身与自有进程验证，没有改宿主代理或注册登录项；真实系统接管、恢复与授权未验。macOS TUN 权限助手未实现。
- **E4 / E5 / M5 的组合界面交互**：已验 Store 超时 / 重连状态保留和版本冲突拒绝，未把每个表单、草稿与后台订阅更新组合自动化。旧版 macOS、Intel、完整 VoiceOver 和全量键盘导航未验。

## 建议的后续重构

- 报告第 5 节：运行时提供共享 preview 后端，替代两端不同的行为模拟；本轮只合并 JSON fixture。
- 报告第 5 节：由运行时输出 action / workspace / tool schema，生成两端契约与模型；R1 之外不继续扩展本轮重构。
- 报告第 5 节：拆分 service、Store、GTK app 与 NativeRuntime 的职责，显式表示事务副作用和回滚数据。
- **M3 / 本地化**：迁移 String Catalog、补运行时消息翻译，保持动态文本按字面显示。

## 移入的历史验证摘要

- **2026-10-18**：旧记录称 macOS Apple Silicon 发布构建、24 项包内离线测试与含空格路径搬移检查通过；当时 socket 与 GUI 注册受限制，真实核心与截图未完成。另有 x86_64 Linux Nix / GTK 明暗与窄窗验证。这些是历史记录，本轮没有重新确认其环境。
- **2026-10-19**：性能与 UI 的一次性评审已移到 `docs/archive/`，其中的测试数量、产物与用户安装状态仅属于当日。构建截图和临时日志未纳入版本控制，失效链接已移除。
