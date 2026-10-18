# macOS 前端审查与重构

审查日期：2026-10-18。范围：`macos/Sources/ZFly` 的 SwiftUI 前端。

这次调整以清晰、克制和操作确定性为目标：先看连接状态，再看流量，最后进入配置与管理。强调色用于选中项和主要操作，正文与图表使用稳定底色。用户最后补充的重点是规整度：保留宽裕的卡片尺寸，统一卡片边界、内部列线和间距。

## 当前布局规则

| 项目 | 当前值与行为 |
| --- | --- |
| 页面与卡片内边距 | 均为 24pt；页头、卡片、结果与底部操作使用一致的左右边界 |
| 区块间距 | 20pt；区块标题放在所属卡片内部 |
| 表单行 | 最小高 44pt；标签左对齐，输入框与开关使用同一右边缘 |
| 输入框和选择框 | 共用 240pt 宽度 |
| 分类切换条 | 总高 44pt；各项按最长标签等宽，受可用宽度约束；选中前后字重相同，切换时不改变整体宽度 |
| 工具操作 | 两列等宽按钮；维护、备份、恢复和 WebDAV 测试共用列线 |
| 工具结果 | 跟随操作区在同一滚动容器内排列，使用相同卡片宽度 |
| 列表 | 订阅、资源与连接采用显式行边距；连接表头与数据行共用列宽及间距 |

设置页移除了分组 `Form` 的额外内缩，改用共用卡片与表单行；核心、网络接管和外观的草稿与保存行为保留。概览移除独立的最大宽度限制，与其他页面保持相同外边界；主题选项保留等宽的大卡片。内容超过最小窗口高度时通过滚动访问，保存操作保留在底部。

侧栏仍使用 SwiftUI `regularMaterial`，并非 macOS Liquid Glass。减少透明度与减少动态效果设置继续生效。

## 发现的问题与已完成调整

| 原有问题 | 已完成调整 |
| --- | --- |
| 大面积渐变和透明叠层干扰文字；部分浅色图标对比不足 | 内容区改用实色卡片和细边框，透明材质集中在侧栏；浅色、深色使用独立配色 |
| 宣传式大标题、巨大圆形电源按钮占据过多空间 | 概览改为连接卡片，集中显示运行状态、当前配置、端口和启动／停止操作 |
| 流量指标、图例与图表关系松散，底部悬浮状态遮挡图表 | 指标和趋势合并为一个区域；状态栏拥有独立空间 |
| 导航缺少分组，中文额外字距影响阅读 | 导航按工作区与管理分组；设置和当前配置放在底部；使用自然字距 |
| 页面标题、按钮、搜索框、圆角与间距不一致，卡片内部列线松散 | 抽取共用组件和视觉常量；统一 24pt 边距、44pt 表单行、240pt 控件列与等宽操作列 |
| 切换页面或设置分类会销毁局部视图状态 | 页面首次访问后保留，保存未提交草稿、筛选和滚动状态；隐藏页面不接收点击和辅助功能访问 |
| 节点、连接、订阅列表的选择状态与操作层级不够清晰 | 节点使用自适应网格、选中边框与勾选标记；连接流量列对齐；订阅突出当前配置，次要操作收入菜单 |
| 设置与订阅入口重复，工具操作混排 | 设置分为核心、网络接管、外观；工具分为诊断、维护、备份与恢复；保留核心停止时可保存的持久化选项 |
| 空数据、无搜索结果、启动中等状态表达不足 | 增加相应状态和下一步操作；区分启动中、等待核心、运行和停止 |
| 英文动态标题未完整本地化，窄窗口标签换行不合理 | 补全动态分类、端口和菜单翻译；检查 900×640 最小窗口的中英文布局 |
| 动效和材质未充分响应系统辅助设置 | 响应减少动态效果与减少透明度；增加标题、图标按钮和选中状态的辅助功能语义 |

## 代码组织

- [Appearance.swift](../macos/Sources/ZFly/Appearance.swift)：颜色、尺寸、卡片、页头、搜索、状态标签、等宽分类条、共用表单行和按钮。
- [App.swift](../macos/Sources/ZFly/App.swift)：窗口布局、分组导航、页面保留和状态栏。
- [NetworkViews.swift](../macos/Sources/ZFly/NetworkViews.swift)：独立的节点与连接页面。
- [DashboardView.swift](../macos/Sources/ZFly/DashboardView.swift)：连接状态、流量与常用控制。
- [ManagementView.swift](../macos/Sources/ZFly/ManagementView.swift)、[SettingsView.swift](../macos/Sources/ZFly/SettingsView.swift)：订阅管理、设置分类与草稿。
- [CatalogView.swift](../macos/Sources/ZFly/CatalogView.swift)、[WorkspaceView.swift](../macos/Sources/ZFly/WorkspaceView.swift)：资源、规则与工具。
- [PreviewOptions.swift](../macos/Sources/ZFly/PreviewOptions.swift)：仅在 `--safe-mode` 下接受页面、分类、查询与示例状态覆盖。

`Store.swift` 的改动仅用于安全预览数据初始化；`NativeRuntime.swift` 扩展预览尺寸参数，并在窗口首次成为活动窗口时调整初始焦点。共享适配层 `ZFlyKit` 与重构前备份一致，`FlClash/` 工作树保持干净。

## 验证

- `sh macos/build-app.sh` 的 Release 构建成功。
- `codesign --verify --deep --strict build/macos/ZFly.app` 通过；应用元数据、图标、预览资源和中英文本地化已检查。
- 当前布局的 21 个 `--safe-mode` 离屏预览均成功生成，进程退出码均为 0。记录见 [grid/results.json](../build/macos/ui-review/grid/results.json)。
- 当前布局覆盖全部主页面、设置与工具分类；包含 900×640、1100×760、1440×900，浅色与深色、中英文，以及工具示例输出。这些维度按代表性场景组合验证，并非全部排列组合。
- 额外截取当前布局的概览、工具浅色实际窗口和设置深色实际窗口，核对卡片边界、控件对齐、材质和启动焦点。离屏深色截图中的亮分隔线未在实际窗口复现。记录见 [grid/native-results.json](../build/macos/ui-review/grid/native-results.json)；这些进程在截图后由脚本结束，记录中的 `-15` 为 SIGTERM。
- 此前的空配置、停止和搜索无匹配状态检查保留于 [results.json](../build/macos/ui-review/results.json)。
- 所有验证启动均使用 `--safe-mode`；只结束本次任务记录的进程。最终核对的 65 个已记录进程均已退出，未启动真实核心或修改宿主网络。

本轮完成构建与视觉检查，未执行完整自动化交互测试、VoiceOver 实机测试、真实核心／系统代理集成测试或 Linux 平台验证。TUN 等能力仍按实际支持情况显示限制。

## 查看效果

| 场景 | 截图 |
| --- | --- |
| 重构前概览 | [before-overview.png](../build/macos/ui-review/before-overview.png) |
| 当前概览浅色实际窗口 | [overview-light-native.png](../build/macos/ui-review/grid/overview-light-native.png) |
| 当前工具浅色实际窗口 | [tools-light-native.png](../build/macos/ui-review/grid/tools-light-native.png) |
| 当前设置深色实际窗口 | [settings-dark-native.png](../build/macos/ui-review/grid/settings-dark-native.png) |
| 最小窗口英文概览 | [overview-min-en.png](../build/macos/ui-review/grid/overview-min-en.png) |
| 代理选择 | [proxies-light.png](../build/macos/ui-review/grid/proxies-light.png) |
| 订阅管理 | [profiles-min-en.png](../build/macos/ui-review/grid/profiles-min-en.png) |
| 网络接管设置 | [settings-network-min-en.png](../build/macos/ui-review/grid/settings-network-min-en.png) |
| 规则编辑 | [rules-min-en.png](../build/macos/ui-review/grid/rules-min-en.png) |
| 工具维护操作 | [tools-maintenance-min-en.png](../build/macos/ui-review/grid/tools-maintenance-min-en.png) |
| 工具备份与恢复 | [tools-backup-min-en.png](../build/macos/ui-review/grid/tools-backup-min-en.png) |
| 工具输出示例 | [tools-result-min-en.png](../build/macos/ui-review/grid/tools-result-min-en.png) |

截图与验证记录位于 `build/macos/ui-review/`；构建产物位于 `build/macos/ZFly.app`。

## 启动时侧栏蓝色焦点框修正

实际窗口复现了“概览”按钮在启动时自动获得键盘焦点的问题：即使打开规则或工具页，概览外侧仍显示系统蓝色焦点框。

`AppDelegate` 现在只在每个普通窗口首次成为活动窗口时，将初始响应对象设为窗口本身。使用弱引用记录已处理窗口，后续页面切换和窗口重新激活不再清除焦点；按钮继续参与键盘导航，系统焦点提示仍可正常显示。

- Release 构建与签名验证通过。
- 浅色默认启动和深色工具页的实际窗口均不再出现自动焦点框。截图与进程记录见 [focus/native-results.json](../build/macos/ui-review/focus/native-results.json)。
- 使用实际 `AppDelegate` 和独立 SwiftUI 控件进行安全模式验证：初始响应对象为窗口，键盘遍历可以进入控件，窗口重新激活后保留主动选择的焦点。记录见 [focus/keyboard-check.log](../build/macos/ui-review/focus/keyboard-check.log)。这项检查不等同于全页面键盘操作或 VoiceOver 测试。
- 仅启动安全模式进程，所有记录进程均已退出；未修改宿主网络。`ZFlyKit` 与原始备份一致，`FlClash/` 工作树保持干净。

| 场景 | 截图 |
| --- | --- |
| 浅色默认启动 | [overview-light-native.png](../build/macos/ui-review/focus/overview-light-native.png) |
| 深色工具页 | [tools-dark-native.png](../build/macos/ui-review/focus/tools-dark-native.png) |
