# Experiment Cycle

Assignment experiment 的标准控制面只有 open、start、optional record、close。

```sh
research-cockpit work open --root <data-root> --assignment <assignment_id> --json --compact
research-cockpit work start --root <data-root> --assignment <assignment_id> --file start.yaml --json --compact
research-cockpit work close --root <data-root> --assignment <assignment_id> --file closeout.yaml --json --compact
```

## Start

仅在缺少 input contract 时运行 `research-cockpit work start --print-schema --json --compact`；它不是普通 worker 流程中的额外步骤。

`work_start_v1` 绑定 `agent_id`、`lease_id`、`lease_epoch`、`operation_id`、`input_revision` 和显式 `experiment_id`。前五个 assignment 字段来自 packet，`experiment_id` 取 packet 的 `cursor.current_node`；该节点不是 experiment 时停止并让 coordinator 修正 assignment。Runtime 生成 run id，原子启动 experiment 并续租。长时间无 mutation 时设置足够的 `lease_seconds`，或由外部 runtime 明确安排 `work renew`；bundled launcher 不会因更新 `progress.json` 自动续租。

## Evidence

研究程序、训练、评测与日志不计入 Research Cockpit control-plane command budget。最终 output directory 放入 closeout 的 `evidence_inputs`，由 runtime 在锁外 stage/hash，并在事务内写 artifact record/provenance。

必须提前 durable 时使用：

```sh
research-cockpit work record --root <data-root> --assignment <assignment_id> --file record.yaml --json --compact
```

Record 不用于进度文本或每个 checkpoint；只用于有独立消费/恢复价值的 evidence。

## Close

`work_close_v1` 可一次包含 run status、experiment result、finding、gates、existing/new artifact record、assignment delivery、tests、proposals、cursor 和 review requirement。同 scope follow-up 使用 `next_experiment`；跨方向工作只提交 `new_branch` proposal。

缺少 closeout input contract 时运行 `research-cockpit work close --print-schema --json --compact`，不要先查询完整 command manifest。示例中的 `review_required: false` 继承 assignment policy；仅在 worker 需要追加独立审查时设为 `true`。`finding.confidence` 合法值为 `weak`、`medium`、`strong`。

成功 close receipt 已包含 result revision、entity refs、lease transition 与 internal verification。`additional_verification_required: false` 时不要 read-after-write。
