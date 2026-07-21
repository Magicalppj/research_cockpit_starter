# Decision And Baseline

Decision lifecycle 只通过 coordinator facade 修改：

```sh
research-cockpit coord decide --print-schema --action promote
research-cockpit coord decide --print-schema --action set_baseline
research-cockpit coord decide --root <data-root> --file decision.yaml --json --compact
```

`coord_decide_v1` 包含 `operation_id`、`action` 和严格 `parameters`。

## Actions

- `promote`: 从 option 创建 proposed decision，并可计算 evidence bundle。
- `refresh_evidence`: 从 supporting experiments 刷新 strength/summary。
- `update_checklist`: 追加 alternatives、consequences、required actions 或 evidence summary。
- `accept`: 执行 acceptance checklist；只有明确 override 才允许 force。
- `set_baseline`: 为 problem/stage 写 effective option、decision、artifact records 和 reason，或显式 clear。

Decision acceptance 会同步相关 option/problem lifecycle，并拒绝 invalid parent、missing evidence 和 terminal-parent active descendants。不要通过 generic graph plan 或 manual YAML 把 decision 直接设为 accepted。

Baseline 是后续 Work Packet 继承的默认 evidence/decision bundle，不会覆盖 assignment 已冻结的 input revision。Baseline 变化会让依赖 packet 报告 stale inputs。
