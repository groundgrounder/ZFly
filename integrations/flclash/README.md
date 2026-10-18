# FlClash 管理桥集成（历史归档）

当前 ZFly 使用自己打包的运行时与代理核心，独立管理订阅、配置、持久化设置和系统代理；不使用这里的管理桥，也不需要安装或运行 FlClash。以下内容仅保留旧方案的迁移与研究记录，不是当前应用的安装步骤。

早期方案的核心状态和节点操作使用 mihomo HTTP 控制器；订阅、配置和持久化网络设置使用这里导出的 Flutter 管理桥。该桥复用了 FlClash 的生命周期、数据库、校验和系统权限流程。

`native-control.patch` 包含主程序入口、管理桥、配置引用更新及已有数据库测试文件中的修改。`base-commit.txt` 记录补丁基准提交。补丁不包含原仓库的 `.gitmodules` URL 调整，也不包含原生前端。

当前工作区的 `FlClash/` 已恢复为原版，保留 Git 历史和已初始化的核心子模块。本次开发的修改已完整导出为补丁，原仓库中的新增管理桥源码和旧原生构建产物已删除。ZFly 根项目忽略该目录。

从 ZFly 根目录准备用于构建的独立源码快照：

```sh
python3 tools/prepare-flclash-host.py
```

脚本读取本地 Git 中记录的基准提交和子模块提交，导出到 `build/flclash-host/` 后应用补丁，不需要网络，也不修改原仓库。源码快照不含 `.git`；源仓库需要已初始化相应子模块。目标目录已存在时拒绝覆盖，可用 `--output build/flclash-host-new` 另选目录。

后续在 `build/flclash-host/` 内，按其 `.agents/commands.md` 准备指定的 Flutter、Go、Rust 和平台依赖，重新生成代码并构建主程序；无需在快照里执行 `git submodule update`，子模块源码已导出。控制器密钥非空、外部控制器已开启时，以 `--native-control` 启动构建结果可启用本机 `127.0.0.1:9097` 管理桥。已安装的原版发行包不包含此桥。

若在另一份独立的 Git 工作副本中继续开发管理桥，可从 ZFly 根目录刷新补丁；保持保留的 `FlClash/` 原仓库不变：

```sh
python3 tools/export-flclash-integration.py --host /path/to/modified-flclash
```

该脚本只读 FlClash 工作区，覆盖本目录的补丁和基准记录，不提交任何仓库。补丁基准包含原版来源信息：FlClash / chen08209；原版许可及版权说明继续有效。管理桥尚未完成完整 Flutter 构建和真实系统网络联调，具体限制见 [使用说明](../../docs/usage.md)。
