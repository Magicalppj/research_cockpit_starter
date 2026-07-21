# Worker Loop

## Open Once

```sh
research-cockpit work open --root <data-root> --assignment <assignment_id> --json --compact
```

Work Packet 是本 assignment 的完整控制面上下文。使用其中的 `objective`、`scope`、`success_criteria`、`deliverables`、`lease`、`input_revision` 和 `allowed_operations`；不要再读取 coordinator context 或完整 graph。

Unchanged polling：

```sh
research-cockpit work open --root <data-root> --assignment <assignment_id> --since <revision> --json --compact
```

收到 `changed: false` 后停止，不追加查询。

## Claim

Packet 未被领取时只调用一次：

```sh
research-cockpit work claim --root <data-root> --assignment <assignment_id> --agent <agent_id> --operation-id <operation_id> --return-packet --json --compact
```

直接复用返回 packet，不立即 reopen。

## Start And Close

```sh
research-cockpit work start --root <data-root> --assignment <assignment_id> --file start.yaml --json --compact
research-cockpit work close --root <data-root> --assignment <assignment_id> --file closeout.yaml --json --compact
```

`work_start_v1` 使用 packet 中的 agent、lease、epoch 与 `input_revision`，并显式设置 `experiment_id: <packet.cursor.current_node>`；run id 由 runtime 生成。若 `cursor.current_node` 不是 experiment，不要搜索或猜测目标，停止并让 coordinator 修正 assignment。只有缺少 contract 时才运行 `work start --print-schema --json --compact`。

`work_close_v1` 一次提交 run status、experiment result、finding、assignment result、cursor、review requirement、proposal 与 optional `evidence_inputs`。只有缺少 closeout contract 时才运行 `work close --print-schema --json --compact`；返回示例包含 `review_required`，其默认 `false` 继承 assignment policy，设为 `true` 只用于追加 review，不能用 `false` 取消 coordinator 已要求的 review。`finding.confidence` 只接受 `weak`、`medium` 或 `strong`。

Final payload 优先通过 closeout 提交。只有 crash recovery、共享消费或超大 streaming output 要求 close 前 durable 时才使用：

```sh
research-cockpit work record --root <data-root> --assignment <assignment_id> --file record.yaml --json --compact
```

通过 `work record --print-schema` 获取 `work_record_v1`。`source_dir` 相对 input file 解析；payload staging、内容哈希、record 写入、lease renewal 和 operation receipt 属于一个事务。

## Stop Conditions

- `internally_verified: true` 且 `additional_verification_required: false`：立即停止当前控制面步骤。
- `stale_inputs`：reopen packet，不继续提交旧结果。
- lease owner/epoch mismatch：停止写入，交还 coordinator。
- scope conflict：不要扩大 scope 或手改 YAML。
- `new_branch` proposal：只记录 proposal，不能自行创建 assignment。

`work renew` 与 `work release` 仅用于恢复或显式交还；正常 mutation 和 launcher heartbeat 自动续租。

## Detail On Demand

只有 packet 无法解释 experiment closeout 字段时才读取 `capabilities/experiment-cycle.md`；只有需要 legacy run、gate 或 artifact-record truth 语义时才继续读取 `capabilities/experiment-tracking.md`。
