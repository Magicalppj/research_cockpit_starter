# Coordinator Node Management

Node creation、结构更新、lifecycle update 与 assignment session 统一通过：

```sh
research-cockpit coord assign --print-schema --action graph_plan
research-cockpit coord assign --print-schema --action session
research-cockpit coord assign --root <data-root> --file graph-plan.yaml --json --compact
```

`coord_assign_v1` 的 `graph_plan` action 包含 `nodes` 与 `updates`。Domain handler 会验证 ids、node types、parent/children、status aliases、scope、lifecycle guards 与 target revision，并在一个 transaction 中写 interaction receipt。

`session` action 为一个 option 创建明确 assignment/worktree boundary；它不是 graph plan 的隐式副作用。

## Rules

- Decision acceptance 与 baseline 不属于 graph plan，使用 `coord decide`。
- Worker follow-up 优先放入 `work_close_v1.next_experiment`；跨方向只提交 proposal。
- 不用 generic YAML patch 修改 assignment、lease、result、interaction backend 或 accepted decision。
- 0.2.x node unknown fields 在 canonical mutation 后必须保留。
- 成功 receipt internal-verified 后不追加 changed-scope read；manual YAML edit 才运行 validate/context。
