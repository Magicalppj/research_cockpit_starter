# Maintenance Actions

## Input Contract

`maintenance repair`、`maintenance migrate` 和 `maintenance compact` 接受：

```yaml
schema_version: maintenance_action_v1
action: <route-specific-action>
execute: false
parameters: {}
```

相对 filesystem path 以 input file 所在目录解析。Unknown fields、错误类型和跨 route action 会在写入前拒绝。

## Repair

Actions：

- `interaction_log`: 修复 legacy YAML interaction document；execution 在变更前写 backup。
- `suggestion_lifecycle`: 清理符合 state/age 条件的 orphan lifecycle rows。

```sh
research-cockpit maintenance repair --root <data-root> --file repair.yaml --json --compact
```

Segmented interaction backend 激活后，legacy prefix 为只读；此时应使用 migration/audit guidance，而不是覆盖旧文件。

## Migrate

Actions：

- `interaction_log`: 原子写入并激活 JSONL generation，保留 legacy prefix。
- `worktree_findings`: 从指定 worktree root 导入 bounded evidence fields，拒绝结构性 graph/global-focus 变更。
- `terminal_next_actions`: 将 eligible terminal experiment 的单个 follow-up 转成 sibling experiment，其他情况只返回 guidance。

```sh
research-cockpit maintenance migrate --root <data-root> --file migration.yaml --json --compact
```

Migration 必须先 dry-run；source evidence 不得删除。

## Compact

`action: artifact` 的 dry-run 返回 artifact classification。Execution 必须指定一个 `artifact_id`，且目标为 `can_demote`。

```sh
research-cockpit maintenance compact --root <data-root> --file compaction.yaml --json --compact
```

Demotion 写入 artifact record 与 migration report，但不删除 payload bytes。一次 invocation 只处理一个 artifact。
