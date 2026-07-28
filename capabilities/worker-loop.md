# Worker Loop

## Open Once

```sh
research-cockpit work open --root <data-root> --assignment <assignment_id> --json --compact
```

Work Packet 是本 assignment 的完整控制面上下文。使用其中的 `objective`、`scope`、`success_criteria`、`deliverables`、`lease`、`input_revision`、`active_runs` 和 `allowed_operations`；不要再读取 coordinator context 或完整 graph。

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

若 packet 未列出 `start` 而列出 `record`/`close`，从 `active_runs.assignment.items` 取得本 assignment 的 `run_id` 并复用，不再启动第二个 run。若 mutation 列表为空，检查 `active_runs.experiment.items`；目标 experiment 由其他 assignment 占用时不要关闭他人的 run。`active_run_blocks_start` 的 `dependency_blockers.items` 仍给出占用者；按 receipt 查看 execution context，并继续本 assignment 的 run，或让 coordinator 协调其 owner。

`work_close_v1` 一次提交 run status、experiment result、finding、assignment result、cursor、review requirement、proposal 与 optional `evidence_inputs`。只有缺少 closeout contract 时才运行 `work close --print-schema --json --compact`；返回示例包含 `review_required`，其默认 `false` 继承 assignment policy，设为 `true` 只用于追加 review，不能用 `false` 取消 coordinator 已要求的 review。`finding.confidence` 只接受 `weak`、`medium` 或 `strong`。

同一 research contract 下的 code edit、retry、seed、parameter adjustment、preflight 与 repeated local attempt 都复用当前 assignment，不创建新的 assignment 或 graph node。只有 hypothesis、protocol 或 success criteria 的变化足以影响 research judgment 时，才在 closeout 中提出新 branch/node proposal，由 coordinator 决定是否建图和分派。

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

`work release` 用于显式交还；正常 mutation 自动续租。Bundled launcher 与 `progress.json` 不会自动续租；长任务需在 start contract 中设置足够的 `lease_seconds`，或由外部 runtime 明确安排 `work renew`，不要让模型按 progress update 周期性轮询。

## Detail On Demand

只有 packet 无法解释 experiment closeout 字段时才读取 `capabilities/experiment-cycle.md`；只有需要 legacy run、gate 或 artifact-record truth 语义时才继续读取 `capabilities/experiment-tracking.md`。
