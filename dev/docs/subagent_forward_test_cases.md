# Subagent Forward Test Cases

本文档说明 `dev/scripts/run_subagent_forward_check.py` 当前验证的无上下文 agent 路径。
它是开发验证材料，不是下游 agent 的 startup 指南。

## 测试边界

- 每次运行都在 `.test_tmp/` 下创建独立副本。
- 原 skill package 必须保持不变。
- 测试只通过 0.3.0 public CLI surface 操作数据。
- workflow 指标记录命令数、输出字节、耗时、额外验证和意外写入。
- role facade 回执若已内部验证，不允许追加 `validate`、`build` 或 `smoke`。

## Track

### `track_a_known_node_reader`

模拟只知道 node id 的 agent。

唯一 startup 路径：

```sh
research-cockpit context --root <data-root> --id <node_id> --view execution --json --compact
```

通过条件：一次命令返回 bounded execution context，不运行 broad discovery，不写数据。

### `track_b_assigned_worker`

模拟已获得 assignment id 的 worker，执行完整 round trip：

```sh
research-cockpit work open --root <data-root> --assignment <assignment_id> --json --compact
research-cockpit work start --root <data-root> --assignment <assignment_id> --file <work_start.yaml> --json --compact
research-cockpit work close --root <data-root> --assignment <assignment_id> --file <closeout.yaml> --json --compact
```

通过条件：总计三次 CLI 调用；closeout 一次提交 run、finding、artifact metadata 与 assignment result；不追加独立验证。

### `track_c_reviewer`

模拟 reviewer 消费 producer revision 并提交一次 verdict：

```sh
research-cockpit review open --root <data-root> --assignment <review_id> --json --compact
research-cockpit review report --root <data-root> --assignment <review_id> --file <review.yaml> --json --compact
```

通过条件：总计两次 CLI 调用；review 绑定 producer result revision；不修改 producer evidence truth。

### `track_d_portable_install`

从隔离副本执行 editable install 后验证入口、依赖与已保存数据可读。该 track 不依赖仓库当前工作目录，也不能把本机绝对路径写入 package。

## 运行

完整检查：

```sh
python dev/scripts/run_subagent_forward_check.py --json
```

只运行读取与 portable install track：

```sh
python dev/scripts/run_subagent_forward_check.py --skip-mutating --json
```

仅在诊断失败副本时使用 `--keep-temp`。正常运行会自动清理临时目录。

## 判定

顶层 `ok` 只有在所有未跳过 track 通过且原 package 未变化时才为 `true`。
每个 track 的 `workflow_contract` 是稳定门槛；`metrics` 是本次实测，不应复制为长期固定 benchmark。
失败时先看该 track 的 `checks`、`unexpected_writes` 与 contract violations，不要补跑 broad smoke。
