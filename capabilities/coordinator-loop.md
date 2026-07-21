# Coordinator Loop

## Overview

```sh
research-cockpit coord overview --root <data-root> --json --compact --limit 20
```

使用 snapshot 的 filters、counts、rows、`next_page` 和 revision。重复 polling 传 `--since <revision>`；不要扫描完整 assignments、runs 或 graph。

## Assign

```sh
research-cockpit coord assign --print-schema --action graph_plan
research-cockpit coord assign --print-schema --action session
research-cockpit coord assign --print-schema --action review_session
research-cockpit coord assign --root <data-root> --file assignment.yaml --json --compact
```

`coord_assign_v1` 支持：

- `action: graph_plan`: 原子创建或更新一组 research nodes。
- `action: session`, `kind: experiment`: 为 option 创建 agent、assignment、branch 和 worktree，并强制绑定该 option 下的 `experiment_id`。
- `action: session`, `kind: review`: 为已完成且 review pending 的 `producer_assignment_id` 创建只读 review assignment；`review_session` 只是 `--print-schema` selector，生成的 input action 仍为 `session`。

从已知 experiment 创建 session 时，`context --view execution` 返回的 `node.parent.id` 是所需 `option_id`。不要把 `effective_baseline.option.id` 当作结构父级；它可能来自其他 option。

Session 必须显式给出 stable `agent_id` 与 `assignment_id`，使 exact retry 不重复创建记录。Experiment session 不接受模糊 option-level target；review session 不占用 producer option 的 exclusive workstream ownership。`operation_id` 只对完全相同的请求复用。Graph node 的 canonical shape 与合法状态见 `capabilities/graph-state.md`。

## Review And Decide

```sh
research-cockpit coord review --print-schema --action assignment_result
research-cockpit coord review --print-schema --action promote_artifact
research-cockpit coord review --root <data-root> --assignment <producer_assignment_id> --file verdict.yaml --json --compact
research-cockpit coord review --root <data-root> --file artifact_promotion.yaml --json --compact
research-cockpit coord decide --print-schema --action promote
research-cockpit coord decide --print-schema --action set_baseline
research-cockpit coord decide --root <data-root> --file decision.yaml --json --compact
research-cockpit context --root <data-root> --id <decision_id> --json --compact
```

Producer close 返回 pending review 后，先用 `coord assign --print-schema --action review_session` 创建 review assignment。Reviewer 完成 `review report` 后，`coord review` 的 `assignment_result` action 才更新 producer review metadata，且要求 `--assignment <producer_assignment_id>`；`promote_artifact` 不接受 `--assignment`，用于保留 provenance 地提升单个 artifact record。两条路径都不改写 producer/reviewer Evidence Bundle。

执行 `coord decide` 前，从 bounded decision `context` 读取 `decision_acceptance`。`coord_decide_v1` 支持 `promote`、`refresh_evidence`、`update_checklist`、`accept` 和 `set_baseline`。Acceptance 与 baseline 必须基于已审 evidence；不能仅因 suggestion 或 worker proposal 自动执行。

## Milestone

```sh
research-cockpit coord handoff --print-schema
research-cockpit coord handoff --root <data-root> --file handoff.yaml --json --compact --progress
```

Handoff 在一个 captured revision 上执行一次 full validation，并复用结果完成 build、compact smoke 与 lifecycle blocker 检查。默认情况下，`queued`、`active`、`blocked` assignment，以及 waiting/unknown/expired 状态都会阻止 handoff；只有明确接受风险时才设置对应 `allow`。Blocked report 是 durable milestone record；truth 变化后使用新 operation id，transport retry 才复用旧 id。
