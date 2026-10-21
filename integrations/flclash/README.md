# FlClash 管理桥集成（历史归档）

当前 ZFly 使用自己的运行时和核心；本目录不参与构建或运行。这里保留早期 Flutter 管理桥方案的补丁、基准与工具，不是应用安装步骤。

`native-control.patch` 保存桥接修改，`base-commit.txt` 记录基准。补丁不包含 `.gitmodules` URL 调整或原生前端。FlClash / chen08209 的原版权和许可继续有效。

`FlClash/` 是可选的外部 Git checkout，当前工作区没有它。`build/flclash-host/` 是现有的只读源码快照，没有 Git 历史，不能用作下面工具的 Git 来源。不要在该目录应用补丁或构建改写源码。

需要研究旧方案时，用另外的外部 Git checkout（含已初始化的对应子模块）导出到一个新的路径：

```sh
python3 integrations/flclash/tools/prepare-flclash-host.py \
  --source /path/to/original-flclash \
  --output build/flclash-migration
```

脚本只读 Git 来源，按基准递归导出子模块后应用归档补丁；目标存在时拒绝覆盖。默认输出也为 `build/flclash-migration`，不会覆盖只读快照。

若在另一个工作副本开发旧桥，可重新导出：

```sh
python3 integrations/flclash/tools/export-flclash-integration.py \
  --host /path/to/modified-flclash
```

导出脚本只读指定 checkout，更新本目录的补丁与基准，不提交仓库。旧管理桥尚未完成完整 Flutter 构建与真实系统网络联调。
